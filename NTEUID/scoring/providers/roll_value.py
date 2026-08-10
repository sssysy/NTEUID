from __future__ import annotations

import json
import math
from typing import Any, cast
from pathlib import Path
from functools import lru_cache
from dataclasses import dataclass
from collections.abc import Sequence

from ..contract import GradeSpec, BaseScorer, ScorerMeta
from ..registry import register_scorer
from ...utils.sdk.tajiduo_model import CharacterDetail, CharacterProperty, CharacterSuitItem
from ...utils.resource.RESOURCE_PATH import SCORING_PATH

_ASSETS = Path(__file__).parent / "assets"


@dataclass(frozen=True, slots=True, kw_only=True)
class AttributeInfo:
    name: str
    score: float


@dataclass(frozen=True, slots=True, kw_only=True)
class AttributeTable:
    entries: dict[str, AttributeInfo]

    def score_of(self, attr_id: str) -> float:
        attr = self.entries.get(attr_id.lower())
        return attr.score if attr is not None else 0.0

    def names_for(self, attr_ids: frozenset[str]) -> frozenset[str]:
        return frozenset(
            attr.name for attr_id in attr_ids if (attr := self.entries.get(attr_id)) is not None and attr.name
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class GradeTier:
    grade: str
    min_ratio: float


@dataclass(frozen=True, slots=True, kw_only=True)
class GradeTable:
    tiers: tuple[GradeTier, ...]

    def grade_of(self, ratio: float) -> str:
        for tier in self.tiers:
            if ratio >= tier.min_ratio:
                return tier.grade
        return self.tiers[-1].grade


@dataclass(frozen=True, slots=True, kw_only=True)
class ScorePlan:
    refer_score: int
    core_main_attrs: frozenset[str]
    recommend_attrs: frozenset[str]
    effective_attr_names: frozenset[str]
    max_core: float
    max_pies: dict[str, float]
    max_score: int

    @property
    def effective_attrs(self) -> frozenset[str]:
        return self.core_main_attrs | self.recommend_attrs

    def max_for(self, item: CharacterSuitItem) -> float:
        if not item.id.startswith("cell"):
            return self.max_core
        grid = str(int(item.id.split("_", 1)[0][4:]))
        return self.max_pies.get(grid, 0.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class EquipmentScore:
    item_id: str
    raw_score: float
    score: float
    grade: str | None
    unlocked_subs: int

    @property
    def display(self) -> str:
        return f"{self.score:.1f}分"


@dataclass(frozen=True, slots=True, kw_only=True)
class CharacterScore:
    plan: ScorePlan
    score: int
    grade: str
    equipment: tuple[EquipmentScore, ...]

    @property
    def display(self) -> str:
        return f"{self.score}分"

    def is_role_prop_effective(self, prop: CharacterProperty) -> bool:
        return prop.id.lower() in self.plan.effective_attrs or prop.name in self.plan.effective_attr_names

    def is_main_prop_counted(self, prop: CharacterProperty) -> bool:
        return prop.value.strip().endswith("%") and prop.id.lower() in self.plan.core_main_attrs

    def is_sub_prop_recommended(self, prop: CharacterProperty) -> bool:
        return prop.id.lower() in self.plan.recommend_attrs


@lru_cache(maxsize=1)
def load_attributes() -> AttributeTable:
    with (SCORING_PATH / "attributes.json").open("r", encoding="utf-8") as file:
        raw = cast(dict[str, dict[str, Any]], json.load(file))
    return AttributeTable(
        entries={
            attr_id.lower(): AttributeInfo(name=str(info.get("name", "")), score=float(info.get("score", 0.0)))
            for attr_id, info in raw.items()
        }
    )


@lru_cache(maxsize=1)
def load_grades() -> GradeTable:
    with (SCORING_PATH / "grades.json").open("r", encoding="utf-8") as file:
        raw = cast(dict[str, Any], json.load(file))
    tiers = tuple(
        GradeTier(grade=str(item["grade"]), min_ratio=float(item["min_ratio"]))
        for item in cast(list[dict[str, Any]], raw.get("tiers", []))
    )
    if not tiers:
        raise ValueError("grades.json has no tiers")
    return GradeTable(tiers=tuple(sorted(tiers, key=lambda item: item.min_ratio, reverse=True)))


@lru_cache(maxsize=64)
def load_score_plan(char_id: str) -> ScorePlan | None:
    path = SCORING_PATH / "chars" / f"{char_id}.json"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as file:
        raw = cast(dict[str, Any], json.load(file))

    refer_score = int(raw["refer_score"])
    max_data = cast(dict[str, Any], raw["max"])
    max_score = int(max_data["score"])
    if refer_score <= 0 or max_score <= 0:
        raise ValueError(f"invalid scoring plan: char_id={char_id}")

    max_pies = cast(dict[str, Any], max_data.get("pie") or {})
    core_main_attrs = frozenset(str(attr).lower() for attr in raw.get("core_main_attr_list", []))
    recommend_attrs = frozenset(str(attr).lower() for attr in raw.get("recommend_attr_list", []))
    attrs = load_attributes()
    return ScorePlan(
        refer_score=refer_score,
        core_main_attrs=core_main_attrs,
        recommend_attrs=recommend_attrs,
        effective_attr_names=attrs.names_for(core_main_attrs | recommend_attrs),
        max_core=float(max_data["core"]),
        max_pies={str(grid): float(value) for grid, value in max_pies.items()},
        max_score=max_score,
    )


def parse_score_value(value: str) -> tuple[float, bool]:
    raw = value.strip()
    if raw.endswith("%"):
        return float(raw[:-1]), True
    return float(raw), False


def score_equipment(
    item: CharacterSuitItem,
    plan: ScorePlan,
    attrs: AttributeTable,
    grades: GradeTable,
) -> EquipmentScore:
    raw_score = 0.0
    for prop in item.main_properties:
        if prop.value.strip() and prop.value.strip().endswith("%") and prop.id.lower() in plan.core_main_attrs:
            raw_score += _prop_score(prop, attrs)
    for index, prop in enumerate(item.properties):
        if prop.value.strip() and index < item.lev // 5 and prop.id.lower() in plan.recommend_attrs:
            raw_score += _prop_score(prop, attrs)

    max_score = plan.max_for(item)
    return EquipmentScore(
        item_id=item.id,
        raw_score=raw_score,
        score=raw_score * 100 / plan.refer_score,
        grade=grades.grade_of(raw_score / max_score) if max_score > 0 else None,
        unlocked_subs=item.lev // 5,
    )


def score_character(character: CharacterDetail) -> CharacterScore | None:
    plan = load_score_plan(character.id)
    if plan is None:
        return None
    items = (*character.suit.core, *character.suit.pie) if character.suit.id else ()
    if not items:
        return None

    attrs = load_attributes()
    grades = load_grades()
    equipment = tuple(score_equipment(item, plan, attrs, grades) for item in items)
    score = math.ceil(sum(item.raw_score for item in equipment) * 100 / plan.refer_score)
    return CharacterScore(
        plan=plan,
        score=score,
        grade=grades.grade_of(score / plan.max_score),
        equipment=equipment,
    )


def _prop_score(prop: CharacterProperty, attrs: AttributeTable) -> float:
    weight = attrs.score_of(prop.id)
    if weight <= 0:
        return 0.0
    value, is_percent = parse_score_value(prop.value)
    return (value / 100 if is_percent else value) / weight


def _attr_names(attr_ids: frozenset[str]) -> str:
    attrs = load_attributes().entries
    names = (attrs[attr_id].name for attr_id in sorted(attr_ids) if attr_id in attrs and attrs[attr_id].name)
    return "、".join(dict.fromkeys(names))


class RollValueScorer(BaseScorer):
    """内置词条折算评分：单词条得分 = 词条数值 / 标准词条价值，按角色方案计有效词条。"""

    scorer_id = "roll_value"
    meta = ScorerMeta(name="词条折算", author="NTEUID", description="内置默认评分：词条数值折算标准词条数")

    def grades(self) -> Sequence[GradeSpec]:
        return (
            GradeSpec(id="S", color=(255, 208, 96), icon=_ASSETS / "rank_S.png"),
            GradeSpec(id="A", color=(170, 165, 240), icon=_ASSETS / "rank_A.png"),
            GradeSpec(id="B", color=(176, 182, 214), icon=_ASSETS / "rank_B.png"),
        )

    def describe_char(self, char_id: str) -> str:
        plan = load_score_plan(char_id)
        if plan is None:
            return ""
        lines = [f"评分参考分：{plan.refer_score}"]
        core = _attr_names(plan.core_main_attrs)
        recommend = _attr_names(plan.recommend_attrs)
        if core:
            lines.append(f"核心主词条：{core}")
        if recommend:
            lines.append(f"推荐有效词条：{recommend}")
        return "\n".join(lines)

    async def prepare(self) -> None:
        load_attributes()
        load_grades()

    async def close(self) -> None:
        load_attributes.cache_clear()
        load_grades.cache_clear()
        load_score_plan.cache_clear()

    async def score_character(self, character: CharacterDetail) -> CharacterScore | None:
        return score_character(character)


register_scorer(RollValueScorer())
