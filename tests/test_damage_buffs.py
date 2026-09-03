from __future__ import annotations

import math

from NTEUID.utils.damage import buffs as buff_parser
from NTEUID.utils.damage.models import ScaleStat, BuffBundle, PanelStats, EnemyProfile, DamageScenario
from NTEUID.utils.damage.scopes import scope_matches
from NTEUID.utils.damage.formula import compute_segment
from NTEUID.utils.damage.settings import base_enemy
from NTEUID.utils.sdk.tajiduo_model import (
    CharGroup,
    CharElement,
    CharQuality,
    CharacterFork,
    CharacterSkill,
    CharacterDetail,
)


def _sentences(text: str) -> list[str]:
    return [
        sentence.strip() for sentence in buff_parser._SENTENCE_RE.split(buff_parser._clean(text)) if sentence.strip()
    ]


def _awaken_sentence(char_id: str, needle: str) -> str:
    for text in buff_parser._awaken_texts(char_id):
        for sentence in _sentences(text):
            if needle in sentence:
                return sentence
    raise AssertionError(f"角色 {char_id} 的觉醒文案中找不到 {needle}")


def _resonance_sentence(char_id: str, needle: str) -> str:
    for text, _ in buff_parser.resonance_effects(char_id):
        for sentence in _sentences(text):
            if needle in sentence:
                return sentence
    raise AssertionError(f"角色 {char_id} 的共鸣文案中找不到 {needle}")


def _skill_sentence(char_id: str, needle: str) -> str:
    for text in buff_parser._skill_texts(char_id):
        for sentence in _sentences(text):
            if needle in sentence:
                return sentence
    raise AssertionError(f"角色 {char_id} 的技能文案中找不到 {needle}")


def _scan_text(
    text: str,
) -> tuple[
    list[buff_parser.ParsedBuff],
    list[buff_parser.ParsedBuff],
    list[buff_parser.EnemyDebuff],
    list[str],
    list[str],
]:
    team: list[buff_parser.ParsedBuff] = []
    self_buffs: list[buff_parser.ParsedBuff] = []
    enemy: list[buff_parser.EnemyDebuff] = []
    unparsed: list[str] = []
    orphans: list[str] = []
    for sentence in _sentences(text):
        buff_parser._scan_sentence(sentence, "测试", team, self_buffs, enemy, unparsed, orphans)
    return team, self_buffs, enemy, unparsed, orphans


def test_damage_scope_uses_the_matching_phrase_and_segment_name() -> None:
    marked_target = _awaken_sentence("1008", "锁定的目标造成的伤害提升")
    [marked_hit] = buff_parser._extract_stats(marked_target)
    assert marked_hit.scope == ""

    ultimate = _skill_sentence("1076", "对非Boss目标造成的伤害提升")
    [ultimate_hit] = buff_parser._extract_stats(ultimate)
    assert ultimate_hit.scope == "type:ultraskill"

    assert scope_matches("ability:兽牙影刺", "melee", "拘管格斗术", "「兽牙影刺」伤害倍率")
    assert scope_matches("ability:幻影移行", "qte", "交仞", "幻影移行倍率")


def test_weapon_clauses_keep_owner_and_trigger_scope_separate() -> None:
    fork = CharacterFork(id="fork_bitgame", lbd=["10%", "2%", "2", "12%", "2%"])
    team, self_buffs, _, _, _ = _scan_text(buff_parser._resolve_fork_effect(fork))

    assert [(buff.kind, buff.value, buff.applies_to_owner) for buff in team] == [
        ("atk_pct", 0.1, False),
        ("atk_pct", 0.02, False),
    ]
    assert [(buff.kind, buff.value, buff.element, buff.scope) for buff in self_buffs] == [
        ("dmg_pct", 0.12, "魂", ""),
        ("dmg_pct", 0.02, "魂", ""),
    ]


def test_mixed_self_and_ally_weapon_effects_route_independently() -> None:
    fork = CharacterFork(id="fork_door", lbd=["16%", "15%", "30%", "20"])
    team, self_buffs, _, _, _ = _scan_text(buff_parser._resolve_fork_effect(fork))

    assert [(buff.kind, buff.value, buff.applies_to_owner) for buff in team] == [("dmg_pct", 0.15, False)]
    assert [(buff.kind, buff.value, buff.element) for buff in self_buffs] == [
        ("atk_pct", 0.16, ""),
        ("dmg_pct", 0.3, "灵"),
    ]


def test_linko_scoped_and_dual_element_buffs_are_preserved() -> None:
    cofrequency = _awaken_sentence("1072", "额外增加25%暴击率")
    [crit_hit] = buff_parser._extract_stats(cofrequency)
    assert (crit_hit.kind, crit_hit.value, crit_hit.scope, crit_hit.is_team) == (
        "crit_rate",
        0.25,
        "ability:同频合击",
        True,
    )

    element_text = _resonance_sentence("1072", "灵和咒属性增伤")
    element_hits = buff_parser._extract_stats(element_text)
    assert {(hit.kind, hit.value, hit.element, hit.is_team) for hit in element_hits} == {
        ("dmg_pct", 0.3, "灵", True),
        ("dmg_pct", 0.3, "咒", True),
    }

    cofrequency_damage = _awaken_sentence("1072", "获得50%增伤")
    [damage_hit] = buff_parser._extract_stats(cofrequency_damage)
    assert (damage_hit.value, damage_hit.scope) == (0.5, "ability:同频合击")


def test_next_skill_damage_wording_is_scoped_to_skill() -> None:
    sentence = _awaken_sentence("1023", "下次释放技能的伤害")
    [hit] = buff_parser._extract_stats(sentence)
    assert (hit.kind, hit.value, hit.scope) == ("dmg_pct", 0.3, "type:skill")


def test_all_party_members_wording_is_routed_as_a_team_buff() -> None:
    sentence = _resonance_sentence("1023", "所有队伍成员获得20%的攻击力加成")
    [hit] = buff_parser._extract_stats(sentence)
    assert (hit.kind, hit.value, hit.is_team, hit.applies_to_owner) == ("atk_pct", 0.2, True, True)


def test_next_attack_extra_damage_is_scoped_to_the_real_segment() -> None:
    sentence = _awaken_sentence("1020", "下次攻击附加的额外伤害提升")
    [hit] = buff_parser._extract_stats(sentence)
    assert (hit.kind, hit.value, hit.scope) == ("dmg_pct", 1.0, "segment:强化攻击")
    assert scope_matches(hit.scope, "ultraskill", "名为『哈尼娅』的旋律", "强化攻击伤害倍率")
    assert not scope_matches(hit.scope, "ultraskill", "名为『哈尼娅』的旋律", "伤害倍率")


def test_defense_and_hp_buffs_reach_their_scaling_stats() -> None:
    defense = _skill_sentence("1033", "防御提升20%")
    [defense_hit] = buff_parser._extract_stats(defense)
    assert (defense_hit.kind, defense_hit.value) == ("def_pct", 0.2)

    hp = _awaken_sentence("1039", "固有生命上限的30%")
    [hp_hit] = buff_parser._extract_stats(hp)
    assert (hp_hit.kind, hp_hit.value) == ("hp_pct", 0.3)

    team_hp = _resonance_sentence("1039", "10%最大生命值提升")
    [team_hp_hit] = buff_parser._extract_stats(team_hp)
    assert (team_hp_hit.kind, team_hp_hit.value, team_hp_hit.is_team) == ("hp_pct", 0.1, True)

    panel = PanelStats(
        level=80,
        atk=100.0,
        defense=200.0,
        hpmax=1000.0,
        crit_rate=0.0,
        crit_dmg=0.5,
        general_dmg=0.0,
        element_dmg=0.0,
        base_defense=100.0,
        base_hpmax=800.0,
    )
    enemy = EnemyProfile(level=80)
    baseline_def = compute_segment(
        name="防御",
        pct=100.0,
        flat=0.0,
        scale=ScaleStat.DEF,
        panel=panel,
        enemy=enemy,
        bundle=BuffBundle(),
    )
    boosted_def = compute_segment(
        name="防御",
        pct=100.0,
        flat=0.0,
        scale=ScaleStat.DEF,
        panel=panel,
        enemy=enemy,
        bundle=BuffBundle(def_pct=0.2),
    )
    assert math.isclose(boosted_def.non_crit / baseline_def.non_crit, 1.1)

    baseline_hp = compute_segment(
        name="生命",
        pct=100.0,
        flat=0.0,
        scale=ScaleStat.HP,
        panel=panel,
        enemy=enemy,
        bundle=BuffBundle(),
    )
    boosted_hp = compute_segment(
        name="生命",
        pct=100.0,
        flat=0.0,
        scale=ScaleStat.HP,
        panel=panel,
        enemy=enemy,
        bundle=BuffBundle(hp_pct=0.1),
    )
    assert math.isclose(boosted_hp.non_crit / baseline_hp.non_crit, 1.08)


def test_real_multiplier_wording_maps_to_damage_segments() -> None:
    linko = _awaken_sentence("1072", "爆发伤害的倍率提升")
    [linko_buff] = buff_parser._extract_multiplier_buffs(linko, "觉醒")
    assert (linko_buff.kind, linko_buff.value, linko_buff.scope) == ("mult_pct", 0.3, "爆发")
    assert math.isclose(buff_parser.segment_mult([linko_buff], "爆发伤害倍率"), 0.3)
    assert buff_parser.segment_mult([linko_buff], "多段伤害倍率") == 0.0

    zankou = _resonance_sentence("1036", "技能倍率提升20%")
    [zankou_buff] = buff_parser._extract_multiplier_buffs(zankou, "共鸣")
    assert zankou_buff.scope == "血宴入梦时|焚天烬灭舞"
    assert math.isclose(buff_parser.segment_mult([zankou_buff], "「血宴入梦时」伤害倍率"), 0.2)

    shinku = _awaken_sentence("1076", "伤害的倍率提升30%")
    [shinku_buff] = buff_parser._extract_multiplier_buffs(shinku, "觉醒")
    assert shinku_buff.scope == "天降赤锋|赤红的处决|处决的龙炎"
    assert math.isclose(buff_parser.segment_mult([shinku_buff], "「极轨终结：处决的龙炎」伤害倍率"), 0.3)


def test_oneiroi_multiplier_list_keeps_all_six_targets() -> None:
    text = buff_parser._awaken_texts("1075")[3]
    buffs = [buff for sentence in _sentences(text) for buff in buff_parser._extract_multiplier_buffs(sentence, "觉醒")]
    assert len(buffs) == 6
    assert {buff.scope for buff in buffs} == {
        "再睡会儿",
        "多睡会儿",
        "打一针吧",
        "多打几针",
        "来听首歌",
        "不好听吗",
    }
    assert all(math.isclose(buff.value, 0.3) for buff in buffs)


def test_absolute_multiplier_replaces_instead_of_stacking() -> None:
    sentence = _awaken_sentence("1051", "额外伤害倍率提升至")
    [buff] = buff_parser._extract_multiplier_buffs(sentence, "觉醒")
    assert buff.kind == "mult_set"
    assert buff_parser.segment_mult_override([buff], "额外伤害倍率") == 300.0
    assert buff_parser.segment_mult_override([buff], "破空伤害倍率") is None


def test_final_damage_is_separate_and_scoped_to_the_named_segment() -> None:
    sentence = _awaken_sentence("1036", "最终伤害提升150%")
    [hit] = buff_parser._extract_stats(sentence)
    assert (hit.kind, hit.value, hit.scope) == ("final_dmg_pct", 1.5, "segment:焚天烬灭舞")

    buff = buff_parser.ParsedBuff(
        kind=hit.kind,
        value=hit.value,
        source="觉醒",
        text=sentence,
        scope=hit.scope,
    )
    matching = buff_parser.bundle_for_segment([buff], "咒", "ultraskill", "焚天烬灭舞", "「焚天烬灭舞」伤害倍率")
    other = buff_parser.bundle_for_segment([buff], "咒", "ultraskill", "焚天烬灭舞", "「血宴入梦时」伤害倍率")
    assert matching.final_dmg_pct == 1.5
    assert other.final_dmg_pct == 0.0

    panel = PanelStats(
        level=80, atk=100.0, defense=100.0, hpmax=1000.0, crit_rate=0.0, crit_dmg=0.5, general_dmg=0.0, element_dmg=0.0
    )
    enemy = EnemyProfile(level=80)
    baseline = compute_segment(
        name="测试", pct=100.0, flat=0.0, scale=ScaleStat.ATK, panel=panel, enemy=enemy, bundle=BuffBundle()
    )
    boosted = compute_segment(
        name="测试",
        pct=100.0,
        flat=0.0,
        scale=ScaleStat.ATK,
        panel=panel,
        enemy=enemy,
        bundle=BuffBundle(final_dmg_pct=0.5),
    )
    assert math.isclose(boosted.non_crit / baseline.non_crit, 1.5)


def test_variable_mechanics_surface_without_becoming_fixed_buffs() -> None:
    base_attack = _awaken_sentence("1051", "每1点基础攻击力")
    placeholder_multiplier = _awaken_sentence("1004", "当前技能等级下提升至")
    for sentence in (base_attack, placeholder_multiplier):
        team, self_buffs, _, unparsed, _ = _scan_text(sentence)
        assert not team
        assert not self_buffs
        assert len(unparsed) == 1


def test_enemy_max_hp_reduction_is_not_mislabeled_as_outgoing_damage() -> None:
    sentence = _awaken_sentence("1004", "额外削减目标生命值上限")
    team, self_buffs, enemy, unparsed, orphans = _scan_text(sentence)
    assert not team and not self_buffs and not enemy
    assert not unparsed and not orphans


def test_unbound_structured_skill_buffs_are_explicit_gaps() -> None:
    gaps = buff_parser._skill_stat_gap_texts("1010")
    assert any("暴击伤害提升 30%" in text for text in gaps)
    _, self_buffs, _, unparsed, _ = _scan_text(next(text for text in gaps if "暴击伤害提升" in text))
    assert not self_buffs
    assert len(unparsed) == 1

    assert any("兽牙影刺」伤害提升 10%" in text for text in buff_parser._skill_stat_gap_texts("1008"))


def test_explicit_stack_cap_becomes_full_trigger_peak() -> None:
    early_mist = _awaken_sentence("1003", "每击败1名敌人")
    [hit] = buff_parser._extract_stats(early_mist)
    assert (hit.value, hit.peak_value, hit.conditional) == (0.06, 0.6, True)

    fork = CharacterFork(id="fork_rose", lbd=["14%", "6%", "3", "1"])
    _, self_buffs, _, _, _ = _scan_text(buff_parser._resolve_fork_effect(fork))
    crit = next(buff for buff in self_buffs if buff.kind == "crit_dmg")
    assert (crit.value, crit.peak_value, crit.conditional) == (0.06, 0.6, True)
    assert buff_parser.bundle_from(self_buffs, scenario=DamageScenario.BASELINE).crit_dmg == 0.0
    assert buff_parser.bundle_from(self_buffs, scenario=DamageScenario.FULL_TRIGGER).crit_dmg == 0.6


def test_extra_crit_damage_per_named_stack_uses_the_resource_cap() -> None:
    fork = CharacterFork(id="fork_time", lbd=["16%", "24%", "8%", "12%", "70"])
    raw = buff_parser._resolve_fork_effect(fork)
    team, self_buffs, _, _, _ = _scan_text(raw)
    caps = buff_parser._named_stack_caps([("武器", raw)])
    resolved = buff_parser._resolve_buff_relations([*team, *self_buffs], caps)
    crit_buffs = [buff for buff in resolved if buff.kind == "crit_dmg"]
    assert [(buff.value, buff.peak_value, buff.scope) for buff in crit_buffs] == [
        (0.24, None, "type:ultraskill"),
        (0.08, 0.24, "type:ultraskill"),
    ]
    scoped = buff_parser.bundle_for_segment(
        crit_buffs,
        "光",
        "ultraskill",
        "浮世来潮",
        "伤害倍率",
        DamageScenario.FULL_TRIGGER,
    )
    assert scoped.crit_dmg == 0.48


def test_unconditional_self_panel_stat_is_not_applied_twice() -> None:
    _, [self_buff], _, _, _ = _scan_text("攻击力提高14%")
    assert self_buff.panel_included
    assert buff_parser.bundle_from([self_buff], scenario=DamageScenario.BASELINE).atk_pct == 0.0

    [team_buff], _, _, _, _ = _scan_text("全队攻击力提高14%")
    assert not team_buff.panel_included
    assert buff_parser.bundle_from([team_buff], scenario=DamageScenario.BASELINE).atk_pct == 0.14


def test_static_damage_bonus_is_not_assumed_to_be_in_the_api_panel() -> None:
    _, [self_buff], _, _, _ = _scan_text("史诗！[2]：暗属性异能伤害提升10%")
    assert not self_buff.panel_included
    bundle = buff_parser.bundle_from([self_buff], element="暗", scenario=DamageScenario.BASELINE)
    assert bundle.dmg_pct == 0.1


def test_named_extra_element_damage_does_not_leak_to_every_segment() -> None:
    sentence = _awaken_sentence("1073", "额外光属性异能伤害提高15%")
    assert not buff_parser._extract_stats(sentence)
    _, self_buffs, _, unparsed, _ = _scan_text(sentence)
    assert not self_buffs
    assert len(unparsed) == 1


def test_non_stacking_wording_is_not_reported_as_an_unknown_stack() -> None:
    _, [self_buff], _, _, _ = _scan_text("释放极轨终结后，攻击力提升15%，该效果不可叠加")
    assert self_buff.conditional
    assert not self_buff.stacked
    assert self_buff.peak_value is None


def test_neighboring_stack_caps_do_not_multiply_the_previous_effect() -> None:
    fork = CharacterFork(id="fork_bitgame", lbd=["10%", "2%", "2", "12%", "2%"])
    team, self_buffs, _, _, _ = _scan_text(buff_parser._resolve_fork_effect(fork))
    assert [(buff.value, buff.peak_value) for buff in team] == [(0.1, None), (0.02, 0.08)]
    assert [(buff.value, buff.peak_value) for buff in self_buffs] == [(0.12, None), (0.02, 0.2)]


def test_bounded_target_layers_are_scoped_and_counted() -> None:
    sentence = _awaken_sentence("1036", "每层使本段攻击")
    _, [buff], _, unparsed, _ = _scan_text(sentence)
    assert not unparsed
    assert (buff.value, buff.peak_value, buff.scope) == (0.12, 2.4, "ability:灭")


def test_replacement_effect_uses_terminal_value_instead_of_adding() -> None:
    base = buff_parser.ParsedBuff(
        kind="dmg_pct",
        value=0.2,
        source="技能",
        text="攻击带有「追缉许可」的目标时，造成的伤害增加20%",
        conditional=True,
    )
    [setter] = buff_parser._extract_effect_set_buffs("「追缉许可」的伤害增加效果提升至30%", "技能")
    resolved = buff_parser._resolve_buff_relations([base, setter], {})
    assert buff_parser.bundle_from(resolved, scenario=DamageScenario.BASELINE).dmg_pct == 0.0
    assert buff_parser.bundle_from(resolved, scenario=DamageScenario.FULL_TRIGGER).dmg_pct == 0.3


def test_ignore_upgrade_replaces_only_its_base_and_independent_effects_add() -> None:
    buffs = [
        buff_parser.ParsedBuff(kind="res_ignore", value=0.12, source="套装", text="无视敌人12%暗属性异能抗性"),
        buff_parser.ParsedBuff(
            kind="res_ignore",
            value=0.24,
            source="套装",
            text="触发后提升至无视敌人24%暗属性异能抗性",
            conditional=True,
        ),
        buff_parser.ParsedBuff(kind="res_ignore", value=0.1, source="套装", text="另一效果无视敌人10%暗属性异能抗性"),
    ]
    resolved = buff_parser._resolve_buff_relations(buffs, {})
    baseline = buff_parser.bundle_from(resolved, scenario=DamageScenario.BASELINE)
    full = buff_parser.bundle_from(resolved, scenario=DamageScenario.FULL_TRIGGER)
    assert math.isclose(baseline.res_ignore, 0.22)
    assert math.isclose(full.res_ignore, 0.34)


def test_named_stack_replacement_inherits_scope_and_cap() -> None:
    base_sentence = _skill_sentence("1033", "每消耗1层「业」")
    _, [base], _, _, _ = _scan_text(base_sentence)
    setter_sentence = _awaken_sentence("1033", "每层「业」")
    [setter] = buff_parser._extract_effect_set_buffs(setter_sentence, "觉醒")
    assert base.kind == setter.kind == "final_dmg_pct"
    resolved = buff_parser._resolve_buff_relations([base, setter], {"业": 20})
    replacement = next(buff for buff in resolved if buff.replaces)
    assert (replacement.scope, replacement.peak_value) == ("type:skill", 0.6)
    scoped = buff_parser.bundle_for_segment(
        resolved,
        "咒",
        "skill",
        "诛恶护持",
        "伤害倍率",
        DamageScenario.FULL_TRIGGER,
    )
    assert scoped.dmg_pct == 0.0
    assert scoped.final_dmg_pct == 0.6


def test_known_kuhara_effect_uses_the_final_damage_zone() -> None:
    sentence = _awaken_sentence("1055", "九原对缔结了")
    [hit] = buff_parser._extract_stats(sentence)
    assert (hit.kind, hit.value, hit.conditional) == ("final_dmg_pct", 0.08, True)


def test_independent_ignore_sources_add() -> None:
    buffs = [
        buff_parser.ParsedBuff(kind="res_ignore", value=0.12, source="套装", text="无视12%抗性"),
        buff_parser.ParsedBuff(kind="res_ignore", value=0.1, source="武器", text="无视10%抗性"),
    ]
    baseline = buff_parser.bundle_from(buffs, scenario=DamageScenario.BASELINE)
    full = buff_parser.bundle_from(buffs, scenario=DamageScenario.FULL_TRIGGER)
    assert math.isclose(baseline.res_ignore, 0.22)
    assert math.isclose(full.res_ignore, 0.22)


def test_this_damage_ignore_is_limited_to_the_extra_damage_segment() -> None:
    sentence = _awaken_sentence("1051", "该伤害无视目标")
    _, [buff], _, _, _ = _scan_text(sentence)
    assert (buff.kind, buff.value, buff.scope) == ("def_ignore", 0.75, "segment:额外伤害")
    assert buff_parser.bundle_from([buff], scenario=DamageScenario.FULL_TRIGGER).def_ignore == 0.0
    matching = buff_parser.bundle_for_segment(
        [buff], "光", "skill", "铭隙鉴刻", "额外伤害倍率", DamageScenario.FULL_TRIGGER
    )
    other = buff_parser.bundle_for_segment(
        [buff], "光", "skill", "铭隙鉴刻", "破空伤害倍率", DamageScenario.FULL_TRIGGER
    )
    assert matching.def_ignore == 0.75
    assert other.def_ignore == 0.0


def test_adjacent_scope_context_carries_scope_and_condition() -> None:
    text = next(text for text in buff_parser._awaken_texts("1003") if "基于全队角色击败" in text)
    unit = next(unit for unit in buff_parser.source_sentences(text) if "每击败1名敌人" in unit.text)
    assert unit.inherited_scope == "type:skill|type:ultraskill"
    assert unit.inherited_conditional

    team: list[buff_parser.ParsedBuff] = []
    self_buffs: list[buff_parser.ParsedBuff] = []
    enemy: list[buff_parser.EnemyDebuff] = []
    unparsed: list[str] = []
    orphans: list[str] = []
    buff_parser._scan_sentence(
        unit.text,
        "觉醒",
        team,
        self_buffs,
        enemy,
        unparsed,
        orphans,
        unit.inherited_scope,
        unit.inherited_conditional,
    )
    [buff] = self_buffs
    assert (buff.scope, buff.conditional, buff.peak_value) == ("type:skill|type:ultraskill", True, 0.6)


def test_locked_passive_text_is_not_scanned_for_a_real_character() -> None:
    character = CharacterDetail(
        id="1003",
        name="早雾",
        alev=80,
        slev=0,
        likeabilitylev=0,
        awakenLev=0,
        elementType=CharElement.PSYCHE,
        groupType=CharGroup.ONE,
        quality=CharQuality.S,
        skills=[
            CharacterSkill(id="ga_sagiri_skill", type="Proactive", level=1),
            CharacterSkill(id="ga_sagiri_passive_1", type="Passive", level=1),
            CharacterSkill(id="ga_sagiri_passive_2", type="Passive", level=0),
        ],
    )
    active = "\n".join(buff_parser._active_skill_texts(character))
    assert "鬼郎丸张开大口" in active
    assert "防御力下降10%" not in active


def test_enemy_assumptions_are_explicit_inputs() -> None:
    enemy = base_enemy(level=95, resist=-0.2, def_reduction=0.1, res_reduction=0.05)
    assert (enemy.level, enemy.resist, enemy.def_reduction, enemy.res_reduction) == (95, -0.2, 0.1, 0.05)


def test_damage_on_hit_resistance_reduction_is_conditional() -> None:
    sentence = _resonance_sentence("1073", "降低目标10%光属性异能伤害抗性")
    _, _, [debuff], _, _ = _scan_text(sentence)
    assert debuff.conditional
    assert buff_parser.enemy_mods([debuff], DamageScenario.BASELINE) == (0.0, 0.0)
    assert buff_parser.enemy_mods([debuff], DamageScenario.FULL_TRIGGER) == (0.0, 0.1)


def test_damage_coefficient_does_not_hide_a_real_attack_buff() -> None:
    sentence = _skill_sentence("1076", "400%*攻击力")
    hits = buff_parser._extract_stats(sentence)
    assert [(hit.kind, hit.value) for hit in hits] == [("atk_pct", 0.05)]

    _, self_buffs, _, unparsed, orphans = _scan_text(sentence)
    assert [(buff.kind, buff.value) for buff in self_buffs] == [("atk_pct", 0.05)]
    assert not unparsed
    assert len(orphans) == 1


def test_unmapped_extra_attacks_are_reported_as_orphans() -> None:
    linko = _awaken_sentence("1072", "技能等级下的倍率为")
    oneiroi = _awaken_sentence("1075", "协助攻击1次造成共计200%")
    shinku = _awaken_sentence("1076", "再造成一次相当于20%")
    for sentence in (linko, oneiroi, shinku):
        _, _, _, unparsed, orphans = _scan_text(sentence)
        assert not unparsed
        assert len(orphans) == 1
