from __future__ import annotations

import asyncio
from base64 import b64decode
from typing import cast

import pytest

from gsuid_core.bot import Bot
from gsuid_core.models import Event
from NTEUID.nte_calendar import nte_calendar_cmd
from NTEUID.utils.resource.RESOURCE_PATH import CALENDAR_INDEX_PATH
from NTEUID.nte_calendar.calendar_service import _resolve_latest_calendar_path


class _RecordingBot:
    def __init__(self) -> None:
        self.message: str | None = None

    async def send(self, message: str) -> None:
        self.message = message


def test_calendar_command_sends_real_latest_image_unchanged() -> None:
    if not CALENDAR_INDEX_PATH.is_file():
        pytest.skip("NteMeta 资源仓尚未安装")

    image_path = _resolve_latest_calendar_path()
    bot = _RecordingBot()
    asyncio.run(nte_calendar_cmd(cast(Bot, bot), Event()))

    assert bot.message is not None
    assert bot.message.startswith("base64://")
    assert b64decode(bot.message.removeprefix("base64://")) == image_path.read_bytes()
