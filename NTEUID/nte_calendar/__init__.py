from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event

from .calendar_service import run_calendar

sv_nte_calendar = SV("nte版本日历")


@sv_nte_calendar.on_fullmatch(("日历", "版本日历"), block=True)
async def nte_calendar_cmd(bot: Bot, ev: Event) -> None:
    await run_calendar(bot, ev)
