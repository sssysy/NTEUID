from __future__ import annotations

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event

from .scratch_model import ScratchRankKind, ScratchRankBoard, ScratchRankScope
from .scratch_service import send_scratch_card, send_scratch_rank

sv_nte_scratch = SV("nte刮刮乐")
sv_nte_scratch_rank = SV("nte刮刮乐排行", priority=2)

_RANK_KIND: dict[str, ScratchRankKind] = {
    "今日": "today",
    "今天": "today",
    "昨日": "yesterday",
    "昨天": "yesterday",
    "上一日": "yesterday",
    "本周": "week",
    "上周": "last_week",
}
_RANK_BOARD_KIND: dict[str, ScratchRankBoard] = {
    "亏损榜": "loss",
    "盈利榜": "profit",
}
_RANK_TIME = "|".join(_RANK_KIND)
_RANK_BOARD = "|".join((*_RANK_BOARD_KIND, "排行", "排名"))
_RANK_PATTERN = (
    rf"^(?=.*(?:{_RANK_TIME}|{_RANK_BOARD}))"
    rf"(?P<time_before>{_RANK_TIME})?"
    rf"刮刮乐"
    rf"(?P<time_after>{_RANK_TIME})?"
    rf"(?P<scope>群|(?i:bot))?"
    rf"(?P<board>{_RANK_BOARD})?$"
)


@sv_nte_scratch.on_fullmatch(("刮刮乐", "喵呜快报", "强制刷新刮刮乐", "刷新刮刮乐", "刷新喵呜快报"), block=True)
async def nte_scratch_cmd(bot: Bot, ev: Event) -> None:
    await send_scratch_card(bot, ev, force_refresh="刷新" in ev.command)


@sv_nte_scratch_rank.on_regex(_RANK_PATTERN, block=True)
async def nte_scratch_rank_cmd(bot: Bot, ev: Event) -> None:
    groups = ev.regex_dict
    time_word = groups["time_before"]
    if time_word is None:
        time_word = groups["time_after"]
    kind = "all" if time_word is None else _RANK_KIND[time_word]

    scope_word = groups["scope"]
    scope: ScratchRankScope = "bot" if scope_word is not None and scope_word.lower() == "bot" else "group"
    board_word = groups["board"]
    board = _RANK_BOARD_KIND[board_word] if board_word in _RANK_BOARD_KIND else None
    await send_scratch_rank(bot, ev, kind, scope, board)
