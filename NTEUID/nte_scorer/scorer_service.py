from __future__ import annotations

import shutil
import asyncio
from pathlib import Path

from gsuid_core.bot import Bot
from gsuid_core.models import Event

from ..utils.msgs import ScorerMsg, send_nte_notify
from ..scoring.registry import SCORERS_PATH, get_scorer, all_scorers
from ..nte_config.nte_config import NTEConfig


async def _run_git(*args: str, cwd: Path, timeout_s: float = 180.0) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        proc.kill()
        return -1, f"git 执行超时(>{timeout_s:.0f}s)"
    return proc.returncode or 0, out_b.decode("utf-8", errors="replace").strip()


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
    if not url:
        return await send_nte_notify(bot, ev, ScorerMsg.ADD_USAGE)
    name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    path = SCORERS_PATH / name
    if path.exists():
        return await send_nte_notify(bot, ev, ScorerMsg.already_exists(name))

    SCORERS_PATH.mkdir(exist_ok=True)
    await send_nte_notify(bot, ev, ScorerMsg.installing(name))
    code, output = await _run_git("clone", url, name, cwd=SCORERS_PATH)
    if code != 0:
        return await send_nte_notify(bot, ev, ScorerMsg.git_failed("安装", output))
    if not (path / "__init__.py").exists():
        await asyncio.to_thread(shutil.rmtree, path, True)
        return await send_nte_notify(bot, ev, ScorerMsg.NOT_PACKAGE)
    await send_nte_notify(bot, ev, ScorerMsg.installed(name))


async def run_scorer_update(bot: Bot, ev: Event, name: str) -> None:
    """带包名更新单个；不带包名更新 scorers/ 下所有 git 安装的包。"""
    if name:
        path = SCORERS_PATH / name
        if not path.is_dir():
            return await send_nte_notify(bot, ev, ScorerMsg.not_found(name))
        packs = [path]
    else:
        SCORERS_PATH.mkdir(exist_ok=True)
        packs = sorted(path for path in SCORERS_PATH.iterdir() if (path / ".git").is_dir())
        if not packs:
            return await send_nte_notify(bot, ev, ScorerMsg.NO_PACK)
    lines = []
    for pack in packs:
        code, output = await _run_git("pull", "--ff-only", cwd=pack)
        tail = output.rsplit("\n", 1)[-1][-60:]
        lines.append(f"· {pack.name}: {'✅' if code == 0 else '❌'} {tail}")
    await send_nte_notify(bot, ev, ScorerMsg.batch_updated(lines))


async def run_scorer_remove(bot: Bot, ev: Event, name: str) -> None:
    if not name:
        return await send_nte_notify(bot, ev, ScorerMsg.REMOVE_USAGE)
    path = SCORERS_PATH / name
    if not path.is_dir():
        return await send_nte_notify(bot, ev, ScorerMsg.not_found(name))
    await asyncio.to_thread(shutil.rmtree, path)
    await send_nte_notify(bot, ev, ScorerMsg.removed(name))
