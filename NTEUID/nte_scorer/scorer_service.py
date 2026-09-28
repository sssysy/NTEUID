from __future__ import annotations

import shutil
import asyncio
from pathlib import Path
from dataclasses import dataclass

from gsuid_core.bot import Bot
from gsuid_core.pool import to_thread
from gsuid_core.models import Event
from gsuid_core.utils.plugins_update.git_async import run_git, git_clone, git_fetch, git_reset_hard, git_is_valid_repo

from ..utils.msgs import ScorerMsg, send_nte_notify
from ..scoring.registry import SCORERS_PATH, get_scorer, all_scorers
from ..nte_config.nte_config import NTEConfig

_update_lock = asyncio.Lock()


@dataclass(frozen=True, slots=True)
class ScorerUpdateResult:
    name: str
    success: bool
    message: str


def _pack_path(name: str) -> Path | None:
    if not name or name.startswith((".", "_", "-")) or any(char in name for char in "/\\:\0"):
        return None
    path = SCORERS_PATH / name
    if path.is_symlink() or path.resolve().parent != SCORERS_PATH.resolve():
        return None
    return path


async def run_scorer_list(bot: Bot, ev: Event) -> None:
    current: str = NTEConfig.get_config("NTEScoringProvider").data
    lines = ["已注册的评分算法："]
    for scorer_id, scorer in sorted(all_scorers().items()):
        meta = scorer.meta
        detail = " ".join(part for part in (meta.name, meta.version and f"v{meta.version}", meta.author) if part)
        mark = " ←当前" if scorer_id == current else ""
        lines.append(f"· {scorer_id}（{detail}）{mark}")
    packs = sorted(
        path.name for path in SCORERS_PATH.iterdir() if path.is_dir() and not path.name.startswith((".", "_"))
    )
    lines.append(f"已安装的外置评分包：{('、'.join(packs)) if packs else '无'}")
    await send_nte_notify(bot, ev, "\n".join(lines))


async def run_scorer_set(bot: Bot, ev: Event, scorer_id: str) -> None:
    """改「评分provider」配置并立刻激活验证；prepare 失败回滚原值，不让坏包挂在配置上。"""
    if not scorer_id:
        return await send_nte_notify(bot, ev, ScorerMsg.SET_USAGE)
    scorers = all_scorers()
    if scorer_id not in scorers:
        return await send_nte_notify(bot, ev, ScorerMsg.unknown_id(scorer_id, sorted(scorers)))
    previous: str = NTEConfig.get_config("NTEScoringProvider").data
    NTEConfig.set_config("NTEScoringProvider", scorer_id)
    try:
        await get_scorer()
    except Exception as error:  # prepare 是第三方代码，什么都可能抛；回滚后如实上报
        NTEConfig.set_config("NTEScoringProvider", previous)
        return await send_nte_notify(bot, ev, ScorerMsg.set_failed(scorer_id, error))
    await send_nte_notify(bot, ev, ScorerMsg.set_ok(scorer_id, scorers[scorer_id].meta.name))


async def run_scorer_add(bot: Bot, ev: Event, url: str) -> None:
    if not url or url.startswith("-") or "\0" in url:
        return await send_nte_notify(bot, ev, ScorerMsg.ADD_USAGE)
    name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    if _pack_path(name) is None:
        return await send_nte_notify(bot, ev, ScorerMsg.INVALID_NAME)
    await send_nte_notify(bot, ev, ScorerMsg.installing(name))
    async with _update_lock:
        path = _pack_path(name)
        if path is None:
            message = ScorerMsg.INVALID_NAME
        elif path.exists():
            message = ScorerMsg.already_exists(name)
        else:
            SCORERS_PATH.mkdir(exist_ok=True)
            success, output = await git_clone(url, path)
            if not success:
                message = ScorerMsg.git_failed("安装", output)
            elif not (path / "__init__.py").is_file():
                await to_thread(shutil.rmtree)(path)
                message = ScorerMsg.NOT_PACKAGE
            else:
                message = ScorerMsg.installed(name)
    await send_nte_notify(bot, ev, message)


async def update_scorer_packs(name: str = "", *, is_force: bool = False) -> list[ScorerUpdateResult]:
    """更新全部或指定评分包，单包失败不重试。"""
    async with _update_lock:
        if name:
            path = _pack_path(name)
            if path is None:
                return [ScorerUpdateResult(name, False, ScorerMsg.INVALID_NAME)]
            packs = [path]
        else:
            if not SCORERS_PATH.is_dir():
                return []
            packs = sorted(
                path
                for path in SCORERS_PATH.iterdir()
                if not path.name.startswith((".", "_")) and (path / ".git").is_dir()
            )
        results: list[ScorerUpdateResult] = []
        for pack in packs:
            if _pack_path(pack.name) is None or (pack / ".git").is_symlink():
                results.append(ScorerUpdateResult(pack.name, False, ScorerMsg.INVALID_NAME))
                continue
            if not (pack / ".git").is_dir() or not await git_is_valid_repo(pack):
                results.append(ScorerUpdateResult(pack.name, False, "不是有效的 Git 仓库"))
                continue
            success, output = await git_fetch(pack)
            if success:
                if is_force:
                    success, output = await git_reset_hard(pack, "@{u}")
                else:
                    code, stdout, stderr = await run_git(pack, "merge", "--ff-only", "--no-autostash", "@{u}")
                    success, output = code == 0, stderr or stdout
            results.append(ScorerUpdateResult(pack.name, success, output))
        return results


async def run_scorer_update(bot: Bot, ev: Event, name: str) -> None:
    """手动强制更新评分包。"""
    results = await update_scorer_packs(name, is_force=True)
    if not results:
        return await send_nte_notify(bot, ev, ScorerMsg.NO_PACK)
    lines: list[str] = []
    for result in results:
        message = result.message.rsplit("\n", 1)[-1][-120:]
        lines.append(f"· {result.name}: {'✅' if result.success else '❌'} {message}")
    await send_nte_notify(bot, ev, ScorerMsg.batch_updated(lines))


async def run_scorer_remove(bot: Bot, ev: Event, name: str) -> None:
    if not name:
        return await send_nte_notify(bot, ev, ScorerMsg.REMOVE_USAGE)
    async with _update_lock:
        path = _pack_path(name)
        if path is None:
            message = ScorerMsg.INVALID_NAME
        elif not path.is_dir():
            message = ScorerMsg.not_found(name)
        else:
            await to_thread(shutil.rmtree)(path)
            message = ScorerMsg.removed(name)
    await send_nte_notify(bot, ev, message)
