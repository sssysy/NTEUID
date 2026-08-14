from __future__ import annotations

from typing import Literal
from datetime import datetime, timedelta
from collections import Counter
from dataclasses import dataclass

from ..utils.database import NTEWanmeiScratchRecord
from ..utils.constants import WANMEI_SCRATCH_HISTORY_DAYS
from ..utils.sdk.wanmei_model import award_parts

ScratchRankKind = Literal["all", "today", "yesterday", "week", "last_week"]
ScratchRankScope = Literal["group", "bot"]
ScratchRankBoard = Literal["loss", "profit"]


def _compact_unit(value: int, divisor: int, unit: str) -> str:
    scaled = value / divisor
    absolute = abs(scaled)
    decimals = 2 if absolute < 10 else 1 if absolute < 100 else 0
    text = f"{scaled:.{decimals}f}"
    if decimals:
        text = text.rstrip("0").rstrip(".")
    return f"{text}{unit}"


def money(value: int) -> str:
    absolute = abs(value)
    if absolute >= 99_995_000:
        return _compact_unit(value, 100_000_000, "亿")
    if absolute >= 10_000:
        return _compact_unit(value, 10_000, "万")
    return f"{value:,}"


def signed_money(value: int) -> str:
    return f"+{money(value)}" if value > 0 else money(value)


def history_start(end_at: datetime) -> datetime:
    return end_at - timedelta(days=WANMEI_SCRATCH_HISTORY_DAYS)


def rank_window(kind: ScratchRankKind) -> tuple[str, datetime, datetime]:
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == "today":
        return "今日", today, now
    if kind == "yesterday":
        return "昨日", today - timedelta(days=1), today
    monday = today - timedelta(days=today.weekday())
    if kind == "week":
        return "本周", monday, now
    if kind == "last_week":
        return "上周", monday - timedelta(days=7), monday
    return f"近 {WANMEI_SCRATCH_HISTORY_DAYS} 天", history_start(now), now


@dataclass(frozen=True, slots=True, kw_only=True)
class ScratchBookStats:
    name: str
    count: int
    cost: int
    gain: int

    @property
    def net(self) -> int:
        return self.gain - self.cost

    @property
    def return_rate(self) -> float:
        return self.gain / self.cost * 100


@dataclass(frozen=True, slots=True, kw_only=True)
class ScratchDayStats:
    date: str
    cost: int
    gain: int

    @property
    def net(self) -> int:
        return self.gain - self.cost


@dataclass(frozen=True, slots=True, kw_only=True)
class ScratchStats:
    start_at: datetime
    end_at: datetime
    total_cost: int
    total_gain: int
    total_count: int
    total_extra: int
    win_count: int
    max_award: int
    extra_awards: tuple[tuple[str, int], ...]
    books: tuple[ScratchBookStats, ...]
    days: tuple[ScratchDayStats, ...]

    @classmethod
    def from_records(
        cls,
        records: list[NTEWanmeiScratchRecord],
        start_at: datetime,
        end_at: datetime,
        cost_by_name: dict[str, int],
    ) -> ScratchStats:
        books: dict[str, list[int]] = {}
        days: dict[str, list[int]] = {}
        extra_awards: Counter[str] = Counter()
        total_cost = 0
        total_gain = 0
        total_extra = 0
        win_count = 0
        max_award = 0
        for record in records:
            cost = cost_by_name[record.card_name]
            total_cost += cost
            total_gain += record.gain
            total_extra += record.extra
            win_count += record.gain > 0
            max_award = max(max_award, record.gain)
            for name, count in award_parts(record.award):
                if name != "方斯":
                    extra_awards[name] += count

            book = books.setdefault(record.card_name, [0, 0, 0])
            book[0] += 1
            book[1] += cost
            book[2] += record.gain

            date = (record.log_time - timedelta(hours=5)).strftime("%Y-%m-%d")
            day = days.setdefault(date, [0, 0])
            day[0] += cost
            day[1] += record.gain

        return cls(
            start_at=start_at,
            end_at=end_at,
            total_cost=total_cost,
            total_gain=total_gain,
            total_count=len(records),
            total_extra=total_extra,
            win_count=win_count,
            max_award=max_award,
            extra_awards=tuple(sorted(extra_awards.items(), key=lambda item: item[1], reverse=True)),
            books=tuple(
                sorted(
                    (
                        ScratchBookStats(
                            name=name,
                            count=value[0],
                            cost=value[1],
                            gain=value[2],
                        )
                        for name, value in books.items()
                    ),
                    key=lambda book: book.net,
                    reverse=True,
                )
            ),
            days=tuple(
                ScratchDayStats(date=date, cost=value[0], gain=value[1]) for date, value in sorted(days.items())
            ),
        )

    @property
    def net(self) -> int:
        return self.total_gain - self.total_cost

    @property
    def return_rate(self) -> float:
        return self.total_gain / self.total_cost * 100

    @property
    def profit_rate(self) -> float:
        return self.net / self.total_cost * 100

    @property
    def hit_rate(self) -> float:
        return self.win_count / self.total_count * 100

    @property
    def span_days(self) -> int:
        return max(1, (self.end_at - self.start_at).days)

    @property
    def best_day(self) -> ScratchDayStats:
        return max(self.days, key=lambda item: item.net)

    @property
    def worst_day(self) -> ScratchDayStats:
        return min(self.days, key=lambda item: item.net)

    def curve(self, width: int = 320, height: int = 120) -> ScratchCurve:
        days_by_date = {day.date: day for day in self.days}
        values: list[int] = []
        running = 0
        cursor = self.start_at.date()
        while cursor <= self.end_at.date():
            day = days_by_date.get(cursor.isoformat())
            if day is not None:
                running += day.net
            values.append(running)
            cursor += timedelta(days=1)
        low = min(0, *values)
        high = max(0, *values)
        span = max(1, high - low)
        zero_y = height - (0 - low) / span * height
        count = len(values)
        points = [
            (
                index * width / (count - 1) if count > 1 else 0,
                height - (value - low) / span * height,
            )
            for index, value in enumerate(values)
        ]
        line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        area = f"{line} {width:.1f},{zero_y:.1f} 0.0,{zero_y:.1f}"
        return ScratchCurve(line=line, area=area, zero_y=round(zero_y, 1))


@dataclass(frozen=True, slots=True, kw_only=True)
class ScratchCurve:
    line: str
    area: str
    zero_y: float


@dataclass(frozen=True, slots=True, kw_only=True)
class ScratchRankEntry:
    user_id: str
    role_name: str
    uid: str
    total_cost: int
    total_gain: int
    total_count: int

    @property
    def net(self) -> int:
        return self.total_gain - self.total_cost

    @property
    def profit_rate(self) -> float:
        return self.net / self.total_cost * 100
