from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import Field, BaseModel, ValidationError

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.utils.image.convert import convert_img

from ..utils.msgs import CalendarMsg, send_nte_notify
from ..utils.resource.RESOURCE_PATH import CALENDAR_INDEX_PATH


class _CalendarIndex(BaseModel):
    latest: str = Field(description="最新版本日历图片文件名")


def _resolve_latest_calendar_path() -> Path:
    index = _CalendarIndex.model_validate_json(CALENDAR_INDEX_PATH.read_text(encoding="utf-8"))
    return CALENDAR_INDEX_PATH.parent / index.latest


async def run_calendar(bot: Bot, ev: Event) -> None:
    try:
        image_path = await asyncio.to_thread(_resolve_latest_calendar_path)
        image = await convert_img(image_path)
    except (OSError, ValidationError) as error:
        logger.warning(f"[NTE日历] 版本日历资源加载失败 index={CALENDAR_INDEX_PATH}: {error!r}")
        await send_nte_notify(bot, ev, CalendarMsg.LOAD_FAILED)
        return

    await bot.send(image)
