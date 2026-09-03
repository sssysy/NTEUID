from __future__ import annotations

import re
from functools import lru_cache
from dataclasses import replace, dataclass
from collections.abc import Sequence

from .raw import RawCharData
from .buffs import (
    ParsedBuff,
    bundle_from,
    segment_mult,
    resonance_effects,
    bundle_for_segment,
    scan_character_buffs,
    segment_mult_override,
)
from .models import (
    NEUTRAL_BUNDLE,
    ScaleStat,
    BuffBundle,
    PanelStats,
    EnemyProfile,
    AbilityDamage,
    DamageContext,
    DamageScenario,
    CharacterDamage,
)
from .formula import (
    compute_segment,
    crit_multipliers,
    defense_multiplier,
    effective_scale_value,
    resistance_multiplier,
    damage_bonus_multiplier,
    final_damage_multiplier,
)
from .constants import (
    PROP_ATK,
    PROP_DEF,
    PROP_CRIT,
    PROP_HPMAX,
    PROP_CRIT_DMG,
    PROP_DMG_GENERAL,
    element_dmg_prop,
)
from ..sdk.tajiduo_model import CharacterDetail
from ..resource.RESOURCE_PATH import STATIC_RESOURCE_PATH

_CHAR_DATA_PATH = STATIC_RESOURCE_PATH / "data" / "char"

# 战斗技能展示顺序：普攻 → 战技 → 终结技 → 援护技
_TYPE_ORDER = {"melee": 0, "skill": 1, "ultraskill": 2, "qte": 3}
# 倍率名里含这些词的是治疗 / 护盾，不挂攻击伤害，排除出直伤
_NON_DAMAGE_KEYWORDS = ("治疗", "护盾", "回复", "恢复")
# 倍率模板里的占位项：{idx} 后可带 % 与 *倍数
_TERM_RE = re.compile(r"\{(\d+)\}(%?)(?:\*(\d+))?")
_LITERAL_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(%?)\s*$")
_PANEL_VALUE_RE = re.compile(r"^-?\d+(?:\.\d+)?%?$")


@dataclass(frozen=True, slots=True)
class _AbilityStat:
    name: str
    value_name: str
    values: tuple[tuple[float, ...], ...]


@dataclass(frozen=True, slots=True)
class _AbilityProfile:
    ability_id: str
    type: str
    type_name: str
    name: str
    damage_stats: tuple[_AbilityStat, ...]


def _is_damage_stat(name: str, value_name: str) -> bool:
    if "%" not in value_name or "倍率" not in name:
        return False
    return not any(keyword in name for keyword in _NON_DAMAGE_KEYWORDS)


@lru_cache(maxsize=64)
def load_ability_profiles(char_id: str) -> dict[str, _AbilityProfile]:
    """读 resource/data/char/<id>.json 的 abilities，按小写技能 id 建表（与面板 skill.id 对齐）。"""
    path = _CHAR_DATA_PATH / f"{char_id}.json"
    if not path.exists():
        return {}
    raw = RawCharData.model_validate_json(path.read_text(encoding="utf-8"))
    profiles: dict[str, _AbilityProfile] = {}
    for ability in raw.abilities:
        damage_stats = tuple(
            _AbilityStat(
                name=stat.name,
                value_name=stat.value_name,
                values=tuple(tuple(arr) for arr in stat.values),
            )
            for stat in ability.stats
            # 无等级曲线的字面量强化系数不是直伤段。
            if _is_damage_stat(stat.name, stat.value_name) and stat.values
        )
        profiles[ability.id.lower()] = _AbilityProfile(
            ability_id=ability.id,
            type=ability.type,
            type_name=ability.type_name,
            name=ability.name,
            damage_stats=damage_stats,
        )
    return profiles


def _eval_template(stat: _AbilityStat, level_idx: int) -> tuple[float, float, ScaleStat]:
    """把倍率模板在指定技能等级下求值，返回 (合计%, 合计固定值, 挂靠属性)。"""
    scale = ScaleStat.ATK
    text = stat.value_name
    if "防御力" in text:
        scale = ScaleStat.DEF
        text = text.replace("防御力", "")
    elif "生命上限" in text or "生命值" in text:
        scale = ScaleStat.HP
        text = text.replace("生命上限", "").replace("生命值", "")
    text = text.replace("每段", "")

    pct = 0.0
    flat = 0.0
    terms = list(_TERM_RE.finditer(text))
    for term in terms:
        idx = int(term.group(1))
        if idx >= len(stat.values):
            continue
        arr = stat.values[idx]
        if not arr:
            continue
        value = arr[min(level_idx, len(arr) - 1)] * int(term.group(3) or 1)
        if term.group(2) == "%":
            pct += value
        else:
            flat += value
    if not terms:
        literal = _LITERAL_RE.match(text)
        if literal is not None:
            value = float(literal.group(1))
            if literal.group(2) == "%":
                pct += value
            else:
                flat += value
    return pct, flat, scale


_SCALE_RESOURCE_STAT = {
    ScaleStat.ATK: "AtkBase",
    ScaleStat.DEF: "DefBase",
    ScaleStat.HP: "HPMaxBase",
}
_SCALE_UP_PROP = {
    ScaleStat.ATK: "atkup",
    ScaleStat.DEF: "defup",
    ScaleStat.HP: "hpmaxup",
}
_SCALE_ADD_PROP = {
    ScaleStat.ATK: "atkadd",
    ScaleStat.DEF: "defadd",
    ScaleStat.HP: "hpmaxadd",
}
_SCALE_BUFF_KIND = {
    ScaleStat.ATK: "atk_pct",
    ScaleStat.DEF: "def_pct",
    ScaleStat.HP: "hp_pct",
}


@lru_cache(maxsize=192)
def _base_curve(char_id: str, scale: ScaleStat) -> tuple[float, ...]:
    """读取角色指定基础属性的等级曲线。"""
    path = _CHAR_DATA_PATH / f"{char_id}.json"
    if not path.exists():
        return ()
    data = RawCharData.model_validate_json(path.read_text(encoding="utf-8"))
    for stat in data.stats:
        if stat.id_stats == _SCALE_RESOURCE_STAT[scale]:
            return tuple(stat.values)
    return ()


def _structured_scale(
    character: CharacterDetail,
    scale: ScaleStat,
    panel_buffs: Sequence[ParsedBuff],
) -> tuple[float, float]:
    """汇总面板已包含的百分比与固定词条，供白值反解。"""
    pct = sum(buff.value for buff in panel_buffs if buff.panel_included and buff.kind == _SCALE_BUFF_KIND[scale])
    flat = 0.0
    up_prop = _SCALE_UP_PROP[scale]
    add_prop = _SCALE_ADD_PROP[scale]
    for prop in character.fork.properties:
        if prop.value and prop.id.lower() == up_prop:
            pct += _parse_value(prop.value)
    for item in (*character.suit.core, *character.suit.pie):
        for prop in (*item.main_properties, *item.properties):
            if not prop.value:
                continue
            key = prop.id.lower()
            if key == up_prop:
                pct += _parse_value(prop.value)
            elif key == add_prop:
                flat += _parse_value(prop.value)
    return pct, flat


def _base_scale(
    character: CharacterDetail,
    scale: ScaleStat,
    panel_value: float,
    panel_buffs: Sequence[ParsedBuff],
) -> float:
    """从最终面板扣除可识别常驻词条，反解含突破加成的战斗白值。"""
    curve = _base_curve(character.id, scale)
    char_base = curve[min(max(character.alev - 1, 0), len(curve) - 1)] if curve else 0.0
    weapon_base = 0.0
    if scale is ScaleStat.ATK:
        fork_props = {prop.id.lower(): prop.value for prop in character.fork.properties}
        if "atkbase" in fork_props and fork_props["atkbase"]:
            weapon_base = _parse_value(fork_props["atkbase"])
    minimum = char_base + weapon_base
    pct, flat = _structured_scale(character, scale, panel_buffs)
    implied = (panel_value - flat) / (1.0 + pct)
    return max(minimum, implied)


def parse_panel(character: CharacterDetail) -> PanelStats:
    """从真实面板 properties 解出最终战斗属性。元素增伤取角色自身元素那一条。"""
    props = {prop.id: prop.value for prop in character.properties}
    atk_value = _parse_value(props[PROP_ATK])
    def_value = _parse_value(props[PROP_DEF])
    hpmax_value = _parse_value(props[PROP_HPMAX])
    panel_buffs = scan_character_buffs(character).self_buffs
    return PanelStats(
        level=character.alev,
        atk=atk_value,
        defense=def_value,
        hpmax=hpmax_value,
        crit_rate=_parse_value(props[PROP_CRIT]),
        crit_dmg=_parse_value(props[PROP_CRIT_DMG]),
        general_dmg=_parse_value(props[PROP_DMG_GENERAL]),
        element_dmg=_parse_value(props[element_dmg_prop(character.element_type)]),
        base_atk=_base_scale(character, ScaleStat.ATK, atk_value, panel_buffs),
        base_defense=_base_scale(character, ScaleStat.DEF, def_value, panel_buffs),
        base_hpmax=_base_scale(character, ScaleStat.HP, hpmax_value, panel_buffs),
    )


def has_damage_panel(character: CharacterDetail) -> bool:
    """伤害所需面板项是否齐全且为可解析数字。"""
    props = {prop.id: prop.value.strip() for prop in character.properties}
    required = (
        PROP_ATK,
        PROP_DEF,
        PROP_HPMAX,
        PROP_CRIT,
        PROP_CRIT_DMG,
        PROP_DMG_GENERAL,
        element_dmg_prop(character.element_type),
    )
    return all(key in props and _PANEL_VALUE_RE.fullmatch(props[key]) is not None for key in required)


def _build_context(panel: PanelStats, enemy: EnemyProfile, bundle: BuffBundle) -> DamageContext:
    expected_crit, _ = crit_multipliers(panel.crit_rate + bundle.crit_rate, panel.crit_dmg + bundle.crit_dmg)
    return DamageContext(
        panel=panel,
        enemy=enemy,
        effective_atk=effective_scale_value(panel, bundle, ScaleStat.ATK),
        dmg_bonus_mult=damage_bonus_multiplier(panel, bundle),
        final_dmg_mult=final_damage_multiplier(bundle),
        crit_expected_mult=expected_crit,
        def_mult=defense_multiplier(panel.level, enemy, bundle.def_ignore),
        res_mult=resistance_multiplier(enemy, bundle.res_ignore),
    )


def _segment_bundle(
    bundle: BuffBundle,
    scoped_buffs: Sequence[ParsedBuff],
    element: str,
    ability_type: str,
    ability_name: str,
    segment_name: str,
    scenario: DamageScenario,
) -> BuffBundle:
    """全局 bundle + 命中本段的来源限定增益。"""
    if not scoped_buffs:
        return bundle
    extra = bundle_for_segment(scoped_buffs, element, ability_type, ability_name, segment_name, scenario)
    if not (
        extra.dmg_pct
        or extra.final_dmg_pct
        or extra.crit_rate
        or extra.crit_dmg
        or extra.def_ignore
        or extra.res_ignore
    ):
        return bundle
    return replace(
        bundle,
        dmg_pct=bundle.dmg_pct + extra.dmg_pct,
        final_dmg_pct=bundle.final_dmg_pct + extra.final_dmg_pct,
        crit_rate=bundle.crit_rate + extra.crit_rate,
        crit_dmg=bundle.crit_dmg + extra.crit_dmg,
        def_ignore=bundle.def_ignore + extra.def_ignore,
        res_ignore=bundle.res_ignore + extra.res_ignore,
    )


# 共鸣技能升级既可点名技能类型，也可只写技能名。
_TYPE_KEYWORD: dict[str, str] = {
    "melee": "普通攻击",
    "skill": "变轨技能",
    "ultraskill": "极轨终结",
    "qte": "援护技",
}
_SKILL_LEVEL_UP_RE = re.compile(r"技能等级提升\s*(\d+)\s*级")


def _norm_skill_text(text: str) -> str:
    """去标签 + 去括号/空白，消除文案与 ability.name 间的标点差异（『』vs「」、奥义· X 的空格）。"""
    return re.sub(r"[「」『』【】《》\s]", "", re.sub(r"<[^>]+>", "", text))


def _resonance_skill_bonus(character: CharacterDetail, profiles: dict[str, _AbilityProfile]) -> dict[str, int]:
    """共鸣1「技能等级提升N级」对各技能类型(ability.type)的等级加成。

    面板 skill.level 是玩家投入的技能等级，不含共鸣的隐藏 +N（实测觉6 角色面板各技能仍显示
    投入等级），结算倍率时必须补回，否则觉≥3 角色被低估。觉醒未达 awaken_num 的共鸣不计。
    """
    bonus: dict[str, int] = {}
    for desc, awaken_num in resonance_effects(character.id):
        if character.awaken_lev < awaken_num:
            continue
        clean = re.sub(r"<[^>]+>", "", desc)
        match = _SKILL_LEVEL_UP_RE.search(clean)
        if match is None:
            continue
        levels = int(match.group(1))
        norm_desc = _norm_skill_text(desc)
        for profile in profiles.values():
            keyword = _TYPE_KEYWORD.get(profile.type)
            if keyword is None:
                continue
            if keyword in norm_desc or (profile.name and _norm_skill_text(profile.name) in norm_desc):
                bonus[profile.type] = max(bonus.get(profile.type, 0), levels)
    return bonus


def build_character_damage(
    character: CharacterDetail,
    enemy: EnemyProfile,
    bundle: BuffBundle = NEUTRAL_BUNDLE,
    scoped_buffs: Sequence[ParsedBuff] = (),
    scenario: DamageScenario = DamageScenario.FULL_TRIGGER,
) -> CharacterDamage:
    """单角色完整伤害结算：真实面板 × 真实倍率表 × 乘区公式。

    scoped_buffs 为带 scope 的来源限定增益（如「极轨终结」造成的伤害+X%）：逐段按 ability.type/name
    匹配后只折给对应段，不平摊到全部段。默认空 = 仅全局 bundle（行为与旧版完全一致）。
    """
    panel = parse_panel(character)
    profiles = load_ability_profiles(character.id)
    element = character.element_type.label
    skill_bonus = _resonance_skill_bonus(character, profiles)

    abilities: list[AbilityDamage] = []
    for skill in character.skills:
        if skill.type == "Passive" or not skill.id:
            continue
        profile = profiles.get(skill.id.lower())
        if profile is None or not profile.damage_stats:
            continue
        level_idx = max(0, skill.level - 1) + skill_bonus.get(profile.type, 0)
        segments_list = []
        for stat in profile.damage_stats:
            pct, flat, scale = _eval_template(stat, level_idx)
            if not (pct or flat):
                continue
            override = segment_mult_override(scoped_buffs, stat.name, scenario)
            if override is not None:
                pct = override
            pct *= 1.0 + segment_mult(scoped_buffs, stat.name, scenario)
            seg_bundle = _segment_bundle(bundle, scoped_buffs, element, profile.type, profile.name, stat.name, scenario)
            segments_list.append(
                compute_segment(
                    name=stat.name, pct=pct, flat=flat, scale=scale, panel=panel, enemy=enemy, bundle=seg_bundle
                )
            )
        segments = tuple(segments_list)
        if not segments:
            continue
        abilities.append(
            AbilityDamage(
                ability_id=profile.ability_id,
                type=profile.type,
                type_name=profile.type_name,
                name=profile.name or skill.name,
                level=skill.level + (skill_bonus[profile.type] if profile.type in skill_bonus else 0),
                segments=segments,
            )
        )

    abilities.sort(key=lambda ability: _TYPE_ORDER.get(ability.type, 99))
    return CharacterDamage(
        character_id=character.id,
        abilities=tuple(abilities),
        context=_build_context(panel, enemy, bundle),
        scenario=scenario,
    )


def build_member_damage(
    character: CharacterDetail,
    enemy: EnemyProfile,
    member_buffs: Sequence[ParsedBuff],
    scenario: DamageScenario = DamageScenario.FULL_TRIGGER,
) -> tuple[CharacterDamage, BuffBundle]:
    """成员伤害单一入口：从同一份 member_buffs 同时派生全局 bundle 与逐段来源限定增益，
    杜绝「全局 bundle 与 scoped_buffs 来自不同列表」的静默错位。返回 (伤害, 全局 bundle)；
    bundle 供环合暴伤区等复用。元素自取，调用方无需再传。"""
    bundle = bundle_from(member_buffs, character.element_type.label, scenario)
    return build_character_damage(character, enemy, bundle, scoped_buffs=member_buffs, scenario=scenario), bundle


def _parse_value(value: str) -> float:
    raw = value.strip()
    if raw.endswith("%"):
        return float(raw[:-1]) / 100.0
    return float(raw)
