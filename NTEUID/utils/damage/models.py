from __future__ import annotations

from enum import Enum
from dataclasses import dataclass


class ScaleStat(str, Enum):
    """技能倍率挂靠的基础属性。倍率名后缀「防御力」「生命上限」决定挂靠对象，缺省挂攻击力。"""

    ATK = "atk"
    DEF = "def"
    HP = "hpmax"

    @property
    def label(self) -> str:
        return {ScaleStat.ATK: "攻击力", ScaleStat.DEF: "防御力", ScaleStat.HP: "生命上限"}[self]


class DamageScenario(str, Enum):
    """静态面板伤害与显式假设全部战斗条件成立的伤害。"""

    BASELINE = "baseline"
    FULL_TRIGGER = "full_trigger"


@dataclass(frozen=True, slots=True, kw_only=True)
class PanelStats:
    """从 API properties 解出的最终面板属性及反解白值。"""

    level: int
    atk: float
    defense: float
    hpmax: float
    crit_rate: float  # 0-1
    crit_dmg: float  # 0-1
    general_dmg: float  # 0-1 通用伤害增强
    element_dmg: float  # 0-1 角色元素「异能伤害增强」
    base_atk: float = 0.0  # 白值，仅用于外部攻击力百分比增量
    base_defense: float = 0.0  # 防御白值，仅用于外部防御力百分比增量
    base_hpmax: float = 0.0  # 生命白值，仅用于外部生命上限百分比增量

    def scale_value(self, scale: ScaleStat) -> float:
        if scale is ScaleStat.DEF:
            return self.defense
        if scale is ScaleStat.HP:
            return self.hpmax
        return self.atk


@dataclass(frozen=True, slots=True, kw_only=True)
class EnemyProfile:
    """敌人假设。资源里没有敌人数据，全部是带默认值的可配置假设。"""

    level: int
    resist: float = 0.0  # 敌人对该元素的抗性 0-1（可负）
    def_reduction: float = 0.0  # 减防（降低敌人防御）
    def_ignore: float = 0.0  # 无视防御
    res_reduction: float = 0.0  # 减抗 / 无视抗性


@dataclass(frozen=True, slots=True, kw_only=True)
class BuffBundle:
    """聚合后的非面板增益；可来自角色自身或队伍。"""

    atk_pct: float = 0.0  # 攻击力% 加成池（仅作用于攻击力挂靠）
    def_pct: float = 0.0  # 防御力% 加成池（仅作用于防御力挂靠）
    hp_pct: float = 0.0  # 生命上限% 加成池（仅作用于生命挂靠）
    dmg_pct: float = 0.0  # 额外增伤（通用 / 元素，进增伤区）
    final_dmg_pct: float = 0.0  # 最终增伤，独立乘区
    crit_rate: float = 0.0
    crit_dmg: float = 0.0
    def_ignore: float = 0.0
    res_ignore: float = 0.0


NEUTRAL_BUNDLE = BuffBundle()


@dataclass(frozen=True, slots=True, kw_only=True)
class SegmentDamage:
    """单个伤害倍率条目（一段普攻 / 一个技能命中）的结算结果。"""

    name: str
    pct: float
    scale: ScaleStat
    scale_value: float
    non_crit: float
    crit: float
    expected: float
    dmg_bonus_mult: float
    final_dmg_mult: float
    crit_expected_mult: float
    def_mult: float
    res_mult: float


@dataclass(frozen=True, slots=True, kw_only=True)
class AbilityDamage:
    """一个技能（普攻 / 战技 / 终结技 / 援护技）下的全部伤害倍率条目。"""

    ability_id: str
    type: str  # melee / skill / ultraskill / qte
    type_name: str
    name: str
    level: int
    segments: tuple[SegmentDamage, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class DamageContext:
    """本次结算的全局乘区；来源限定增益记录在各 SegmentDamage。"""

    panel: PanelStats
    enemy: EnemyProfile
    effective_atk: float
    dmg_bonus_mult: float  # 增伤区
    final_dmg_mult: float  # 最终伤害区
    crit_expected_mult: float  # 暴击区（期望）
    def_mult: float  # 防御区
    res_mult: float  # 抗性区


@dataclass(frozen=True, slots=True, kw_only=True)
class CharacterDamage:
    """单角色完整伤害结算：各技能伤害 + 乘区上下文。"""

    character_id: str
    abilities: tuple[AbilityDamage, ...]
    context: DamageContext
    scenario: DamageScenario


@dataclass(frozen=True, slots=True, kw_only=True)
class DamageEstimate:
    """同一角色的面板基线与全条件假设结果，以及未建模边界计数。"""

    baseline: CharacterDamage
    full_trigger: CharacterDamage
    conditional_effects: int
    single_proc_effects: int
    unparsed_effects: int
    orphan_effects: int
