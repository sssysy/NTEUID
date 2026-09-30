from __future__ import annotations

from functools import lru_cache
from dataclasses import dataclass

from pydantic import BaseModel

from ..utils.resource.RESOURCE_PATH import HEART_PATH


@dataclass(frozen=True, slots=True, kw_only=True)
class HeartLevel:
    level: int
    cumulative: int
    delta: int


@dataclass(frozen=True, slots=True, kw_only=True)
class HeartTable:
    max_level: int
    total_exp_to_max: int
    levels: tuple[HeartLevel, ...]

    def level_of(self, exp: int) -> int:
        """累计经验 `exp` 落在 `[levels[k-1].cumulative, levels[k].cumulative)` 时返回 `k`，溢出按 `max_level` 截断。"""
        if exp <= 0:
            return 0
        level = 0
        for item in self.levels:
            if exp < item.cumulative:
                break
            level = item.level
        return min(level, self.max_level)


class _HeartTableRaw(BaseModel):
    max_level: int
    total_exp_to_max: int
    levels: list[HeartLevel]


@lru_cache(maxsize=1)
def _heart_table() -> HeartTable:
    raw = _HeartTableRaw.model_validate_json(HEART_PATH.read_bytes())
    return HeartTable(
        max_level=raw.max_level,
        total_exp_to_max=raw.total_exp_to_max,
        levels=tuple(sorted(raw.levels, key=lambda item: item.level)),
    )


def heart_level(exp: int) -> int:
    """把服务端 `likeabilitylev`（累计经验值）换算为好感度（羁遇）展示等级 0–10。"""
    return _heart_table().level_of(exp)
