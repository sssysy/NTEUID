import asyncio

from gsuid_core.bot import Bot
from gsuid_core.logger import logger

update_lock = asyncio.Lock()
_restart_requested = False


async def restart_after_update(bot: Bot | None = None) -> None:
    global _restart_requested
    async with update_lock:
        if _restart_requested:
            return

        from gsuid_core.buildin_plugins.core_command.core_restart.restart import restart_genshinuid

        message = "[异环] 更新完成，即将自动重启 Core。"
        if bot is not None:
            await bot.send(message)
        logger.info(message)
        await restart_genshinuid(event=None, is_send=False)
        _restart_requested = True
