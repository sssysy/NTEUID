import re
import asyncio
from typing import TypedDict
from pathlib import Path

from gsuid_core.pool import to_thread
from gsuid_core.logger import logger
from gsuid_core.utils.plugins_update.git_async import (
    GIT_CLONE_TIMEOUT,
    run_git,
    git_reset_hard,
    git_get_current_commit,
)

from .RESOURCE_PATH import STATIC_RESOURCE_PATH
from .scratch_items import load_scratch_items
from .suit_properties import load_suit_properties

RESOURCE_URL = "https://cnb.cool/tyql688/NteMeta"
META_PATH: Path = STATIC_RESOURCE_PATH
META_PATH.mkdir(parents=True, exist_ok=True)
_update_lock = asyncio.Lock()


class ResourceUpdateResult(TypedDict):
    success: bool
    message: str
    files_changed: int


def _is_git_repo() -> bool:
    return (META_PATH / ".git").is_dir()


async def _detect_default_branch() -> str | None:
    # `git init` 流派下没有 origin/HEAD symref，本地分支名也不可信
    # （旧 buggy 安装可能落下 master 本地分支但远端是 main），所以走 ls-remote 现问。
    remote = "origin" if _is_git_repo() else RESOURCE_URL
    rc, ls_out, _ = await run_git(META_PATH, "ls-remote", "--symref", remote, "HEAD")
    if rc != 0:
        return None
    m = re.search(r"ref:\s+refs/heads/(\S+)", ls_out)
    return m.group(1) if m else None


async def update_resources(
    is_force: bool = False,
    silent: bool = False,
) -> ResourceUpdateResult:
    async with _update_lock:
        result = await _update_resources(is_force=is_force, silent=silent)
        if result["success"]:
            from ..name_convert import reload_all

            await to_thread(reload_all)()
            await load_scratch_items(force=True)
            await load_suit_properties()
        return result


async def _update_resources(is_force: bool, silent: bool) -> ResourceUpdateResult:
    result: ResourceUpdateResult = {
        "success": False,
        "message": "",
        "files_changed": 0,
    }

    branch = await _detect_default_branch()
    if branch is None:
        result["message"] = "无法获取资源仓库默认分支"
        logger.error(f"[NTEUID] {result['message']}")
        return result

    if _is_git_repo():
        old_head = await git_get_current_commit(META_PATH)
        rc, stdout, stderr = await run_git(META_PATH, "fetch", "--", "origin", branch)
        success, message = rc == 0, stderr or stdout
        if success:
            if is_force:
                success, message = await git_reset_hard(META_PATH, "FETCH_HEAD")
            else:
                rc, stdout, stderr = await run_git(META_PATH, "merge", "--ff-only", "--no-autostash", "FETCH_HEAD")
                success, message = rc == 0, stderr or stdout
        if not success:
            result["message"] = f"更新失败: {message}"
            logger.error(f"[NTEUID] 资源更新失败: {message}")
            return result

        new_head = await git_get_current_commit(META_PATH)
        if not new_head:
            result["message"] = "无法读取资源版本"
            logger.error(f"[NTEUID] {result['message']}")
            return result
        if old_head == new_head:
            result["success"] = True
            result["message"] = "已是最新"
            if not silent:
                logger.info("[NTEUID] 资源已是最新")
            return result

        files_changed = 0
        if old_head:
            _, diff_out, _ = await run_git(META_PATH, "diff", "--stat", old_head, new_head)
            num_match = re.search(r"(\d+) files? changed", diff_out)
            files_changed = int(num_match.group(1)) if num_match else 0
        result["success"] = True
        result["files_changed"] = files_changed
        result["message"] = f"更新成功，改动了{files_changed}个文件" if files_changed else "更新成功"
        logger.success(f"[NTEUID] 资源{result['message']}")
        return result

    checkout = ("checkout", "-f", "-b", branch, "FETCH_HEAD") if is_force else ("checkout", "-b", branch, "FETCH_HEAD")
    cmds: list[tuple[str, ...]] = [
        ("init",),
        ("remote", "add", "origin", RESOURCE_URL),
        ("fetch", "--depth=1", "--", "origin", branch),
        checkout,
    ]
    for cmd in cmds:
        rc, stdout, stderr = await run_git(META_PATH, *cmd, timeout=GIT_CLONE_TIMEOUT)
        if rc != 0:
            err = stderr or stdout
            result["message"] = f"安装失败: {err}"
            logger.error(f"[NTEUID] 资源安装失败: {err}")
            return result

    result["success"] = True
    result["message"] = "首次安装成功"
    logger.success("[NTEUID] 资源包首次安装成功")
    return result


async def init_resources() -> None:
    await load_suit_properties()
    result = await update_resources()
    if not result["success"]:
        await load_scratch_items(force=True)


async def start_resources() -> None:
    if _is_git_repo():
        from ..background import create_background_task

        create_background_task(init_resources())
    else:
        await init_resources()
