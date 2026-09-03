from __future__ import annotations

from .models import (
    ScaleStat,
    BuffBundle,
    PanelStats,
    EnemyProfile,
    SegmentDamage,
)
from .constants import CHAR_DEF_LEVEL_CONST, ENEMY_DEF_LEVEL_CONST


def defense_multiplier(char_level: int, enemy: EnemyProfile, extra_def_ignore: float = 0.0) -> float:
    """防御区。轨外之境：(100+角色等级) / ((100+角色等级) + (90+敌人等级)×(1-减防)×(1-无视防御))。"""
    char_term = CHAR_DEF_LEVEL_CONST + char_level
    ignore = min(1.0, enemy.def_ignore + extra_def_ignore)
    enemy_term = (ENEMY_DEF_LEVEL_CONST + enemy.level) * (1.0 - enemy.def_reduction) * (1.0 - ignore)
    enemy_term = max(0.0, enemy_term)
    return char_term / (char_term + enemy_term)


def resistance_multiplier(enemy: EnemyProfile, extra_res_ignore: float = 0.0) -> float:
    """抗性区：有效抗性≥0 → 1-抗性；<0 → 1-抗性/(1-抗性)。
    有效抗性 = 原始抗性 - 无视抗 - 减抗。负抗收益被有理函数压低（非线性减半）。"""
    resist = enemy.resist - enemy.res_reduction - extra_res_ignore
    if resist >= 0.0:
        return max(0.0, 1.0 - resist)  # 抗≥100% → 免伤 0，不出负伤害
    return 1.0 - resist / (1.0 - resist)


def crit_multipliers(crit_rate: float, crit_dmg: float) -> tuple[float, float]:
    """返回 (期望暴击系数, 必暴系数)。期望 = 1 + 暴击率×暴击伤害；必暴 = 1 + 暴击伤害。"""
    capped_rate = max(0.0, min(1.0, crit_rate))
    return 1.0 + capped_rate * crit_dmg, 1.0 + crit_dmg


def damage_bonus_multiplier(panel: PanelStats, bundle: BuffBundle) -> float:
    """增伤区 = 1 + 通用增伤 + 元素增伤 + 外部增伤。"""
    return 1.0 + panel.general_dmg + panel.element_dmg + bundle.dmg_pct


def final_damage_multiplier(bundle: BuffBundle) -> float:
    """最终伤害区独立于普通增伤区。"""
    return 1.0 + bundle.final_dmg_pct


def effective_scale_value(panel: PanelStats, bundle: BuffBundle, scale: ScaleStat) -> float:
    """面板终值加战斗内百分比增量；百分比只乘资源白值。"""
    if scale is ScaleStat.DEF:
        return panel.defense + panel.base_defense * bundle.def_pct
    if scale is ScaleStat.HP:
        return panel.hpmax + panel.base_hpmax * bundle.hp_pct
    return panel.atk + panel.base_atk * bundle.atk_pct


def compute_segment(
    *,
    name: str,
    pct: float,
    flat: float,
    scale: ScaleStat,
    panel: PanelStats,
    enemy: EnemyProfile,
    bundle: BuffBundle,
) -> SegmentDamage:
    """按完整乘区公式结算单个倍率条目的非暴 / 暴击 / 期望伤害。"""
    scale_value = effective_scale_value(panel, bundle, scale)
    base = scale_value * pct / 100.0 + flat

    dmg_bonus = damage_bonus_multiplier(panel, bundle)
    final_dmg = final_damage_multiplier(bundle)
    def_mult = defense_multiplier(panel.level, enemy, bundle.def_ignore)
    res_mult = resistance_multiplier(enemy, bundle.res_ignore)
    expected_crit, full_crit = crit_multipliers(panel.crit_rate + bundle.crit_rate, panel.crit_dmg + bundle.crit_dmg)

    non_crit = base * dmg_bonus * final_dmg * def_mult * res_mult
    return SegmentDamage(
        name=name,
        pct=pct,
        scale=scale,
        scale_value=scale_value,
        non_crit=non_crit,
        crit=non_crit * full_crit,
        expected=non_crit * expected_crit,
        dmg_bonus_mult=dmg_bonus,
        final_dmg_mult=final_dmg,
        crit_expected_mult=expected_crit,
        def_mult=def_mult,
        res_mult=res_mult,
    )
