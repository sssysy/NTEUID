from __future__ import annotations

import re
from functools import lru_cache
from dataclasses import replace, dataclass
from collections.abc import Iterable, Sequence

from .raw import RawAbility, RawCharData, RawForkData, RawAbilityStat
from .models import BuffBundle, DamageScenario
from .scopes import extract_scope, scope_matches, extract_scope_at, is_orphan_damage
from ..sdk.tajiduo_model import CharacterFork, CharacterDetail
from ..resource.RESOURCE_PATH import STATIC_RESOURCE_PATH

_FORK_DATA_PATH = STATIC_RESOURCE_PATH / "data" / "fork"
_CHAR_DATA_PATH = STATIC_RESOURCE_PATH / "data" / "char"

_TAG_RE = re.compile(r"<[^>]+>")
_PLACEHOLDER_RE = re.compile(r"\{(\d+)\}")
_SENTENCE_RE = re.compile(r"[。\n\r；;]")
_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?%")

# 「全体」也可能指敌人，不能单独判作队友范围。
_TEAM_TOKENS = ("全队", "全体", "队伍成员", "队伍角色", "队伍中角色", "编队其他角色", "队友")
_ENEMY_SCOPE_TOKENS = ("目标", "敌")
_ALLY_ONLY_TOKENS = ("其他角色", "队友", "不包括")
# 支援者基础攻击力换算缺少队友上下文，不自动折算。
_SKIP_TOKEN = "基础攻击力"

# 只额外计算战斗中触发的自身增益，避免与面板常驻值重复。
_SELF_TRIGGERS = (
    "释放",
    "施放",
    "命中",
    "暴击",
    "每次",
    "每层",
    "叠加",
    "闪避",
    "弹反",
    "承轨",
    "站场",
    "后台",
    "当前控制",
    "持有",
    "受到",
    "攻击时",
    "伤害时",
    "攻击后",
    "伤害后",
    "治疗时",
    "boss",
    "Boss",
    "破碎",
    "破韧",
    "低于",
    "高于",
    "延滞",
    "浸染",
    "蓄",
    "失去生命",
    "击败",
)

_CONDITION_MARKERS = (
    "释放",
    "施放",
    "命中",
    "触发",
    "使用",
    "成功",
    "入场",
    "每",
    "闪避",
    "弹反",
    "承轨",
    "站场",
    "前台",
    "后台",
    "当前控制",
    "持有",
    "受到",
    "攻击时",
    "伤害时",
    "攻击后",
    "伤害后",
    "治疗时",
    "Boss",
    "boss",
    "破碎",
    "破韧",
    "低于",
    "高于",
    "延滞",
    "浸染",
    "蓄",
    "失去生命",
    "处于",
    "状态",
    "若",
    "如果",
    "锁定",
    "指定",
    "期间",
    "场上",
    "仅",
    "带有",
    "进入",
    "对其",
    "切换",
    "在队伍",
    "存活时",
    "缔结",
    "击败",
)

_DIRECT_CAP_RE = re.compile(r"(?:累计最大|最高|最多|至多)(?:可)?(?:提升|提高|增加|降低)(?:至)?\s*(\d+(?:\.\d+)?)%")
_STACK_COUNT_RE = re.compile(r"(?:最多|至多)(?:可)?叠加\s*(\d+|两)\s*(?:层|次)")
_NAMED_STACK_RE = re.compile(r"每(?:消耗|获得|持有)?\s*\d*(?:\.\d+)?\s*层[「『]([^」』]+)[」』]")
_NAMED_STACK_CAP_RES = (
    re.compile(
        r"[「『]([^」』]+)[」』]\s*[（(，,]?\s*"
        r"(?:最多|至多)(?:可)?(?:获得|叠加)?\s*(\d+|两)\s*层"
    ),
    re.compile(
        r"(?:获得|叠加)?\s*(?:\d+|一|两)\s*层[「『]([^」』]+)[」』][^。；]{0,32}?"
        r"(?:最多|至多)(?:可)?(?:获得|叠加)?\s*(\d+|两)\s*层"
    ),
)
_STACK_SET_RE = re.compile(
    r"每层[「『](?P<key>[^」』]+)[」』][^，。；]{0,36}?伤害加成提升至\s*(?P<value>\d+(?:\.\d+)?)%"
)
_EFFECT_DMG_SET_RES = (
    re.compile(r"[「『](?P<key>[^」』]+)[」』]的伤害(?:增加|提升)效果提升至\s*(?P<value>\d+(?:\.\d+)?)%"),
    re.compile(r"[「『](?P<key>[^」』]+)[」』][^，。；]{0,16}?自身造成的伤害提升至\s*(?P<value>\d+(?:\.\d+)?)%"),
)
_PANEL_STAT_KINDS = {"atk_pct", "def_pct", "hp_pct", "crit_rate", "crit_dmg"}
_NON_STACKING_TOKENS = ("不可叠加", "无法叠加", "不能叠加")

# 增益动词须紧邻属性名，避免把伤害倍率误认成属性加成。
_ATK_RES = (
    re.compile(r"攻击力(?:将)?(?:提升|提高|增加)\s*(\d+(?:\.\d+)?)%"),
    re.compile(r"(?:提升|提高|获得|增加)\s*(\d+(?:\.\d+)?)%\s*的?攻击力"),
    re.compile(r"(\d+(?:\.\d+)?)%\s*的?攻击力(?:提升|提高|加成)"),
)
_DEF_RES = (
    re.compile(r"防御(?:力)?(?:将)?(?:提升|提高|增加)\s*(\d+(?:\.\d+)?)%"),
    re.compile(r"(?:提升|提高|获得|增加)\s*(\d+(?:\.\d+)?)%\s*的?防御(?:力)?"),
    re.compile(r"(\d+(?:\.\d+)?)%\s*的?防御(?:力)?(?:提升|提高|加成)"),
)
_HP_RES = (
    re.compile(r"(?:最大生命值|生命上限)(?:将)?(?:提升|提高|增加)\s*(\d+(?:\.\d+)?)%"),
    re.compile(r"(?:提升|提高|获得|增加)\s*(\d+(?:\.\d+)?)%\s*的?(?:最大生命值|生命上限)"),
    re.compile(r"(\d+(?:\.\d+)?)%\s*(?:最大生命值|生命上限)(?:提升|提高|增加)"),
    re.compile(r"(?:提升|提高|增加)[^，。；]{0,6}固有生命上限的?\s*(\d+(?:\.\d+)?)%"),
)
_CRIT_DMG_RES = (
    re.compile(r"暴击伤害(?:将)?(?:额外)?(?:提升|提高|增加)\s*(\d+(?:\.\d+)?)%"),
    re.compile(r"(?:提升|提高|增加)[^，。；]{0,10}?(\d+(?:\.\d+)?)%\s*的?暴击伤害"),
    re.compile(r"(\d+(?:\.\d+)?)%\s*的?暴击伤害(?:提升|提高|增加)"),
)
_CRIT_RATE_RES = (
    re.compile(r"暴击率(?:将)?(?:提升|提高|增加)\s*(\d+(?:\.\d+)?)%"),
    re.compile(r"(?:提升|提高|增加)[^，。；]{0,10}?(\d+(?:\.\d+)?)%\s*的?暴击率"),
    re.compile(r"(\d+(?:\.\d+)?)%\s*的?暴击率(?:提升|提高|增加)"),
)
# 通用增伤不匹配「提升至」；后者是设值而非加算。
_DMG_RES = (
    re.compile(
        r"(?:造成的?伤害|通用伤害|技能伤害|增伤|伤害加成)(?:（[^）]*）)?"
        r"(?:额外)?(?:提升|提高|增加)\s*(\d+(?:\.\d+)?)%"
    ),
    re.compile(r"[」』]的?伤害(?:提升|提高)\s*(\d+(?:\.\d+)?)%"),
    re.compile(r"(?:获得|提升|提高|增加)\s*(\d+(?:\.\d+)?)%\s*的?(?:通用)?(?:增伤|伤害)"),
    re.compile(r"(?:通用)?增伤\s*(\d+(?:\.\d+)?)%"),
)
# 元素限定增伤只施加给同元素角色。
_ELEM_DMG_RE = re.compile(r"([光灵咒暗魂相])属性异能伤害(?:提升|提高|增加)(\d+(?:\.\d+)?)%")
_VALUE_ELEM_DMG_RE = re.compile(r"(\d+(?:\.\d+)?)%\s*([光灵咒暗魂相](?:[和与、][光灵咒暗魂相])*)属性(?:异能)?增伤")
_FINAL_DMG_RE = re.compile(r"(?:造成的?最终伤害|最终伤害)(?:额外)?(?:提升|提高|增加)\s*(\d+(?:\.\d+)?)%")
_FINAL_SOURCE_RE = re.compile(r"[「『]([^」』]+)[」』][^，。；]{0,16}?最终伤害")
_KNOWN_FINAL_DMG_RES = (
    re.compile(r"每消耗\s*\d+\s*层[「『]业[」』][^，。；]{0,24}?技能伤害"),
    re.compile(r"每层[「『]业[」』][^，。；]{0,24}?伤害加成"),
    re.compile(r"九原对缔结了?[「『]致命玫约[」』]的目标造成的伤害"),
)
_NEXT_SKILL_DMG_RE = re.compile(r"(?:提升|提高|增加)\s*(\d+(?:\.\d+)?)%[^，。；]{0,12}下次释放技能的伤害")
_NEXT_ATTACK_EXTRA_DMG_RE = re.compile(r"下次攻击附加的额外伤害(?:提升|提高|增加)\s*(\d+(?:\.\d+)?)%")
# 直伤描述不是属性增益。
_DAMAGE_DESC_RE = re.compile(
    r"\d+(?:\.\d+)?%\s*\*?\s*(?:攻击力|防御力|生命上限)的|相当于\s*\d+(?:\.\d+)?%[「『]"
    r"|额外造成|承受额外|(?:造成|受到|承受)\s*\d+\s*次"
)
_QUOTED_TARGET = r"[「『][^」』]+[」』]"
_TARGET_LIST = _QUOTED_TARGET + r"(?:(?:、|和|与|及|以及)" + _QUOTED_TARGET + r")*"
_MULT_RE = re.compile(
    rf"(?P<targets>{_TARGET_LIST})(?P<descriptor>[^，。；]{{0,18}}?)(?:伤害的?倍率|技能倍率)"
    r"(?:提升|提高)\s*(?P<value>\d+(?:\.\d+)?)\s*%"
)
_BASE_MULT_RE = re.compile(
    rf"(?P<targets>{_TARGET_LIST})的倍率(?:提升)?，提升值相当于基础倍率的"
    r"(?P<value>\d+(?:\.\d+)?)\s*%"
)
_MULT_SET_RE = re.compile(
    rf"(?P<targets>{_TARGET_LIST})(?P<descriptor>[^，。；]{{0,12}}?)伤害倍率提升至"
    r"(?P<value>\d+(?:\.\d+)?)%\s*\*?\s*攻击力"
)
_TARGET_NAME_RE = re.compile(r"[「『]([^」』]+)[」』]")
_MULT_SCOPE_ALIASES = {"来首歌吧": "来听首歌"}
_UNMODELED_BUFF_RES = (
    re.compile(r"^\[结构化技能数值\]"),
    re.compile(r"[「『][^」』]+[」』]造成的额外[^。；]{0,24}伤害(?:提升|提高|增加)\s*\d+(?:\.\d+)?%"),
    re.compile(r"每\s*\d+(?:\.\d+)?\s*点基础攻击力.*伤害(?:提升|提高|增加)"),
    re.compile(r"基于[^，。；]{0,16}基础攻击力[^，。；]{0,24}(?:攻击力|攻击增益)"),
    re.compile(r"(?:伤害|增伤)[^，。；]{0,16}提升至\s*(?:\d+(?:\.\d+)?%|\{\d+\}%)"),
    re.compile(r"伤害倍率提升[^。；]{0,32}提升至\s*\{\d+\}%"),
    re.compile(r"队伍造成的追加攻击伤害(?:提升|提高|增加)"),
    re.compile(r"增伤效果[^，。；]{0,16}(?:作用于|共享)"),
    re.compile(r"(?:基于|根据)[^。；]{0,120}(?:提升|提高|增加|获得)[^。；]{0,80}(?:伤害|攻击力)"),
    re.compile(r"(?:获得|转化为)[^，。；]{0,20}伤害增益"),
    re.compile(r"自身造成的伤害(?:和[^，。；]{0,12})?(?:提升|提高)(?:，|$)"),
    re.compile(r"技能期间自身防御力(?:提升|提高)(?:，|$)"),
    re.compile(r"(?:伤害值|倾陷值)[^，。；]{0,16}(?:提升|提高)(?:，|$)"),
)
_STRUCTURED_GAP_NAME_RE = re.compile(r"(?:暴击伤害提升|伤害(?:值)?提升(?:至)?|伤害增伤|额外伤害值)$")
_UNMODELED_DAMAGE_RES = (
    re.compile(r"额外造成(?:一|1)次[^。；]{0,80}技能等级下的倍率为\{\d+\}%"),
    re.compile(r"协助攻击\d+次造成[^，。；]{0,60}\d+(?:\.\d+)?%\s*\*?[^，。；]{0,20}攻击力的伤害"),
    re.compile(r"再造成一次相当于\d+(?:\.\d+)?%[「『][^」』]+[」』]的伤害"),
    re.compile(r"每次释放技能时受到\s*\d+(?:\.\d+)?%\s*倍率的伤害"),
    re.compile(r"额外[^。；]{0,96}(?:造成|伤害量)[^。；]{0,48}(?:\{\d+\}|\d+(?:\.\d+)?)%\s*\*?\s*攻击力"),
    re.compile(r"承受额外\s*\d+(?:\.\d+)?%\s*倍率的[^。；]{0,32}伤害"),
    re.compile(r"受到\s*\d+\s*次来自[^。；]{0,24}\d+(?:\.\d+)?%\s*攻击力的[^。；]{0,24}伤害"),
    re.compile(r"每次触发都会造成\s*\d+(?:\.\d+)?%\s*\*?\s*攻击力[^。；]{0,24}伤害"),
    re.compile(r"「延滞」强化[^。；]{0,96}造成\s*\d+(?:\.\d+)?%\s*\*?\s*攻击力[^。；]{0,24}伤害"),
    re.compile(r"已承受伤害[^。；]{0,32}差值的伤害"),
)
_NON_OFFENSIVE_RES = (
    re.compile(r"伤害分摊比例"),
    re.compile(r"削减[^。；]{0,12}生命值上限"),
    re.compile(r"总伤害的\d+(?:\.\d+)?%[^。；]{0,24}回复生命"),
    re.compile(r"回复比例[^，。；]{0,16}生命上限"),
    re.compile(r"最大生命值[^。；]{0,40}充能"),
)


# 用于「看着像 buff 却没解析出」的显式 surface 判定
_BUFFY_TOKENS = ("攻击力", "防御", "生命上限", "最大生命值", "暴击", "暴伤", "伤害", "增伤")

# 顿号也作为停止符，避免从相邻枚举项偷取减防或减抗数值。
_ENEMY_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"防御力?(?:下降|降低|减少)\s*(\d+(?:\.\d+)?)%|(?:下降|降低|减少)[^，。；、]{0,6}?(\d+(?:\.\d+)?)%[^，。；、]{0,6}防御"
        ),
        "def_reduction",
    ),
    (
        re.compile(
            r"抗性(?:下降|降低|减少)\s*(\d+(?:\.\d+)?)%|(?:下降|降低|减少)[^，。；、]{0,6}?(\d+(?:\.\d+)?)%[^，。；、]{0,8}抗性"
        ),
        "res_reduction",
    ),
)
# 无视防御与无视抗性不在面板中，直接作为战斗增益扫描。
_IGNORE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"无视[^，。；、]{0,4}?(\d+(?:\.\d+)?)%[^，。；、]{0,4}?防御|(\d+(?:\.\d+)?)%[^，。；、]{0,2}无视[^，。；、]{0,2}防御"
        ),
        "def_ignore",
    ),
    (
        re.compile(
            r"无视[^，。；、]{0,4}?(\d+(?:\.\d+)?)%[^，。；、]{0,8}?抗性|(\d+(?:\.\d+)?)%[^，。；、]{0,2}无视[^，。；、]{0,8}抗性"
        ),
        "res_ignore",
    ),
)


def _first_num(match: re.Match[str]) -> float:
    """多分支正则取第一个非空数字组（双向语序各占一组）。"""
    for group in match.groups():
        if group is not None:
            return float(group)
    return 0.0


@dataclass(frozen=True, slots=True)
class _StatHit:
    kind: str
    value: float
    element: str = ""
    scope: str = ""
    is_team: bool = False
    applies_to_owner: bool = True
    peak_value: float | None = None
    conditional: bool = False
    stacked: bool = False
    stack_key: str = ""


@dataclass(frozen=True, slots=True)
class ParsedBuff:
    kind: str
    value: float
    source: str
    text: str
    element: str = ""
    scope: str = ""
    applies_to_owner: bool = True
    is_team: bool = False
    peak_value: float | None = None
    conditional: bool = False
    stacked: bool = False
    stack_key: str = ""
    effect_key: str = ""
    replaces: bool = False
    panel_included: bool = False


@dataclass(frozen=True, slots=True)
class EnemyDebuff:
    kind: str  # def_reduction / res_reduction
    value: float
    source: str
    text: str
    peak_value: float | None = None
    conditional: bool = False


@dataclass(frozen=True, slots=True)
class BuffScan:
    owner: str
    team_buffs: tuple[ParsedBuff, ...]
    self_buffs: tuple[ParsedBuff, ...]
    enemy_debuffs: tuple[EnemyDebuff, ...]
    unparsed: tuple[str, ...]
    orphans: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceSentence:
    text: str
    inherited_scope: str = ""
    inherited_conditional: bool = False
    context: str = ""


def _clean(text: str) -> str:
    return _TAG_RE.sub("", text)


def source_sentences(raw_text: str) -> tuple[SourceSentence, ...]:
    """切句并把相邻无数值的作用域说明绑定到下一条数值句。"""
    sentences = [sentence.strip() for sentence in _SENTENCE_RE.split(_clean(raw_text)) if sentence.strip()]
    units: list[SourceSentence] = []
    pending_scope = ""
    pending_conditional = False
    pending_context = ""
    for index, sentence in enumerate(sentences):
        next_has_value = index + 1 < len(sentences) and "%" in sentences[index + 1]
        candidate_scope = extract_scope(sentence) if "%" not in sentence and next_has_value else ""
        if candidate_scope and "伤害" in sentence and any(verb in sentence for verb in ("提升", "提高", "增加")):
            pending_scope = candidate_scope
            pending_conditional = is_conditional(sentence)
            pending_context = sentence
            continue
        units.append(
            SourceSentence(
                text=sentence,
                inherited_scope=pending_scope,
                inherited_conditional=pending_conditional,
                context=pending_context,
            )
        )
        pending_scope = ""
        pending_conditional = False
        pending_context = ""
    return tuple(units)


def is_conditional(sentence: str) -> bool:
    """文案是否依赖战斗动作、状态或目标条件。"""
    if "常态获得" in sentence and "持续生效" in sentence:
        return False
    return any(marker in sentence for marker in _CONDITION_MARKERS)


def _number_count(raw: str) -> int:
    return 2 if raw == "两" else int(raw)


def _stack_key_at(sentence: str, end: int) -> str:
    """取数值前最近的具名层数资源。"""
    matches = [match for match in _NAMED_STACK_RE.finditer(sentence) if match.start() < end]
    return matches[-1].group(1) if matches else ""


def _cap_after(sentence: str, end: int, pattern: re.Pattern[str]) -> re.Match[str] | None:
    """取当前数值后的上限；中间出现另一百分比说明上限属于后一个效果。"""
    match = pattern.search(sentence, end)
    if match is None or _PERCENT_RE.search(sentence, end, match.start()) is not None:
        return None
    return match


def _peak_value(sentence: str, match: re.Match[str], value: float, stack_key: str) -> float | None:
    direct = _cap_after(sentence, match.end(), _DIRECT_CAP_RE)
    if direct is not None:
        return float(direct.group(1)) / 100.0

    count = _cap_after(sentence, match.end(), _STACK_COUNT_RE)
    if count is not None:
        return value * _number_count(count.group(1))

    if stack_key:
        for pattern in _NAMED_STACK_CAP_RES:
            for named in pattern.finditer(sentence):
                if named.group(1) == stack_key:
                    return value * _number_count(named.group(2))
    return None


def _is_stacked(sentence: str, match: re.Match[str], peak_value: float | None, stack_key: str) -> bool:
    if peak_value is not None or stack_key:
        return True
    clause = _clause_at(sentence, match.start(), match.end())
    suffix = sentence[match.end() :]
    next_value = _PERCENT_RE.search(suffix)
    tail = suffix[: next_value.start()] if next_value is not None else suffix
    stackable = "叠加" in tail and not any(token in tail for token in _NON_STACKING_TOKENS)
    return "每" in clause or stackable


def _is_team_scope(sentence: str) -> bool:
    """句子是否在给队友加成。「全队」恒为队友；「全体」需排除「全体目标 / 敌方全体」这类敌人范围。"""
    if any(token in sentence for token in _TEAM_TOKENS if token != "全体"):
        return True
    return "全体" in sentence and not any(token in sentence for token in _ENEMY_SCOPE_TOKENS)


def _clause_at(sentence: str, start: int, end: int) -> str:
    """取命中所在逗号子句，避免同一句多个效果互相污染目标与作用域。"""
    left = max(sentence.rfind("，", 0, start), sentence.rfind(",", 0, start))
    right = len(sentence)
    for separator in ("，", ","):
        found = sentence.find(separator, end)
        if found >= 0:
            right = min(right, found)
    return sentence[left + 1 : right]


def _target_for(sentence: str, start: int, end: int) -> tuple[bool, bool]:
    """返回 (是否队伍增益, 是否也作用于效果持有者)。"""
    clause = _clause_at(sentence, start, end)
    current_ally = "当前控制" in clause and "装备者位于后台" in sentence
    is_team = _is_team_scope(clause) or current_ally
    ally_only = current_ally or any(token in clause for token in _ALLY_ONLY_TOKENS)
    return is_team, not ally_only


def _ignore_scope(sentence: str, start: int, end: int) -> str:
    """解析穿透限定范围；“该伤害”优先回指同句的额外伤害段。"""
    clause = _clause_at(sentence, start, end)
    if "该伤害" in clause and "额外伤害" in sentence[:start]:
        return "segment:额外伤害"
    return extract_scope_at(sentence, start, end)


def _stat_hit(
    sentence: str,
    match: re.Match[str],
    kind: str,
    value: float,
    element: str = "",
    scope_override: str | None = None,
) -> _StatHit:
    is_team, applies_to_owner = _target_for(sentence, match.start(), match.end())
    scope = scope_override if scope_override is not None else extract_scope_at(sentence, match.start(), match.end())
    if not scope and "每消耗" in sentence and "技能伤害" in sentence:
        scope = "type:skill"
    if not scope and "本段攻击" in sentence:
        quoted = _TARGET_NAME_RE.findall(sentence[: match.start()])
        if quoted:
            scope = f"ability:{quoted[-1]}"
    stack_key = _stack_key_at(sentence, match.end())
    peak_value = _peak_value(sentence, match, value, stack_key)
    stacked = _is_stacked(sentence, match, peak_value, stack_key)
    if kind == "final_dmg_pct":
        source = next(
            (
                candidate
                for candidate in _FINAL_SOURCE_RE.finditer(sentence)
                if candidate.start() < match.end() and match.start() < candidate.end()
            ),
            None,
        )
        if source is not None:
            core = source.group(1).split("：")[-1].split(":")[-1].strip()
            scope = f"segment:{core}"
    return _StatHit(
        kind=kind,
        value=value,
        element=element,
        scope=scope,
        is_team=is_team,
        applies_to_owner=applies_to_owner,
        peak_value=peak_value,
        conditional=is_conditional(sentence) or stacked,
        stacked=stacked,
        stack_key=stack_key,
    )


def _append_stat(
    hits: list[_StatHit],
    seen: set[tuple[str, float, str, str, bool, bool, float | None]],
    hit: _StatHit,
) -> None:
    key = (
        hit.kind,
        round(hit.value, 6),
        hit.element,
        hit.scope,
        hit.is_team,
        hit.applies_to_owner,
        round(hit.peak_value, 6) if hit.peak_value is not None else None,
    )
    if key not in seen:
        seen.add(key)
        hits.append(hit)


def _is_unmodeled_buff(sentence: str) -> bool:
    """已确认是增益、但当前数据不足以可靠折算的机制。"""
    if "每层" in sentence and _DIRECT_CAP_RE.search(sentence) is not None:
        return False
    return not _is_non_offensive(sentence) and any(
        pattern.search(sentence) is not None for pattern in _UNMODELED_BUFF_RES
    )


def _is_unmodeled_damage(sentence: str) -> bool:
    """资源有额外直伤，但倍率表没有可绑定的独立段。"""
    return any(pattern.search(sentence) is not None for pattern in _UNMODELED_DAMAGE_RES)


def _is_non_offensive(sentence: str) -> bool:
    """只描述承伤或治疗，不影响当前输出伤害结算。"""
    return any(pattern.search(sentence) is not None for pattern in _NON_OFFENSIVE_RES)


def _uses_final_damage_zone(sentence: str) -> bool:
    """社区机制资料明确归入最终增伤区的非标准描述。"""
    return any(pattern.search(sentence) is not None for pattern in _KNOWN_FINAL_DMG_RES)


def _extract_stats(sentence: str) -> list[_StatHit]:
    """解析一句中的全部可折算属性增益，并保留各自作用域与受益目标。"""
    if _is_unmodeled_buff(sentence):
        return []

    hits: list[_StatHit] = []
    seen: set[tuple[str, float, str, str, bool, bool, float | None]] = set()
    occupied: list[tuple[int, int]] = []

    for match in _FINAL_DMG_RE.finditer(sentence):
        _append_stat(hits, seen, _stat_hit(sentence, match, "final_dmg_pct", float(match.group(1)) / 100.0))
        occupied.append(match.span())

    for match in _NEXT_SKILL_DMG_RE.finditer(sentence):
        _append_stat(
            hits,
            seen,
            _stat_hit(sentence, match, "dmg_pct", float(match.group(1)) / 100.0, scope_override="type:skill"),
        )
        occupied.append(match.span())

    for match in _NEXT_ATTACK_EXTRA_DMG_RE.finditer(sentence):
        _append_stat(
            hits,
            seen,
            _stat_hit(sentence, match, "dmg_pct", float(match.group(1)) / 100.0, scope_override="segment:强化攻击"),
        )
        occupied.append(match.span())

    for match in _ELEM_DMG_RE.finditer(sentence):
        _append_stat(hits, seen, _stat_hit(sentence, match, "dmg_pct", float(match.group(2)) / 100.0, match.group(1)))
        occupied.append(match.span())
    for match in _VALUE_ELEM_DMG_RE.finditer(sentence):
        for element in re.findall(r"[光灵咒暗魂相]", match.group(2)):
            _append_stat(hits, seen, _stat_hit(sentence, match, "dmg_pct", float(match.group(1)) / 100.0, element))
        occupied.append(match.span())

    stat_patterns = (
        (_ATK_RES, "atk_pct"),
        (_DEF_RES, "def_pct"),
        (_HP_RES, "hp_pct"),
        (_CRIT_RATE_RES, "crit_rate"),
        (_CRIT_DMG_RES, "crit_dmg"),
    )
    for patterns, kind in stat_patterns:
        for pattern in patterns:
            for match in pattern.finditer(sentence):
                _append_stat(hits, seen, _stat_hit(sentence, match, kind, float(match.group(1)) / 100.0))
                occupied.append(match.span())

    for pattern in _DMG_RES:
        for match in pattern.finditer(sentence):
            if any(match.start() < end and start < match.end() for start, end in occupied):
                continue
            kind = "final_dmg_pct" if _uses_final_damage_zone(sentence) else "dmg_pct"
            _append_stat(hits, seen, _stat_hit(sentence, match, kind, float(match.group(1)) / 100.0))
            occupied.append(match.span())
    return hits


def _mult_scope(targets: str, descriptor: str = "") -> str:
    """把倍率文案目标转成可命中倍率段名的关键词集合。"""
    if "爆发" in descriptor:
        return "爆发"
    scopes: list[str] = []
    for raw in _TARGET_NAME_RE.findall(targets):
        core = raw.split("：")[-1].split(":")[-1].strip()
        scope = _MULT_SCOPE_ALIASES[core] if core in _MULT_SCOPE_ALIASES else core
        if scope and scope not in scopes:
            scopes.append(scope)
    return "|".join(scopes)


def _extract_multiplier_buffs(sentence: str, source: str) -> list[ParsedBuff]:
    """解析基础倍率相对提升与绝对设值，二者都不进入普通增伤区。"""
    buffs: list[ParsedBuff] = []
    conditional = is_conditional(sentence)
    for match in _MULT_RE.finditer(sentence):
        buffs.append(
            ParsedBuff(
                kind="mult_pct",
                value=float(match.group("value")) / 100.0,
                source=source,
                text=sentence,
                scope=_mult_scope(match.group("targets"), match.group("descriptor")),
                conditional=conditional,
            )
        )
    for match in _BASE_MULT_RE.finditer(sentence):
        buffs.append(
            ParsedBuff(
                kind="mult_pct",
                value=float(match.group("value")) / 100.0,
                source=source,
                text=sentence,
                scope=_mult_scope(match.group("targets")),
                conditional=conditional,
            )
        )
    for match in _MULT_SET_RE.finditer(sentence):
        buffs.append(
            ParsedBuff(
                kind="mult_set",
                value=float(match.group("value")) / 100.0,
                source=source,
                text=sentence,
                scope=_mult_scope(match.group("targets"), match.group("descriptor")),
                conditional=conditional,
            )
        )
    return buffs


def _extract_effect_set_buffs(sentence: str, source: str) -> list[ParsedBuff]:
    """解析有明确终值的跨文案替换；占位符与持续伤害仍留在缺口。"""
    buffs: list[ParsedBuff] = []
    stack_match = _STACK_SET_RE.search(sentence)
    if stack_match is not None:
        key = stack_match.group("key")
        buffs.append(
            ParsedBuff(
                kind="final_dmg_pct" if _uses_final_damage_zone(sentence) else "dmg_pct",
                value=float(stack_match.group("value")) / 100.0,
                source=source,
                text=sentence,
                conditional=True,
                stacked=True,
                stack_key=key,
                effect_key=f"stack:{key}",
                replaces=True,
            )
        )
    for pattern in _EFFECT_DMG_SET_RES:
        for match in pattern.finditer(sentence):
            key = match.group("key")
            buffs.append(
                ParsedBuff(
                    kind="dmg_pct",
                    value=float(match.group("value")) / 100.0,
                    source=source,
                    text=sentence,
                    conditional=not ("常态获得" in sentence and "持续生效" in sentence),
                    effect_key=f"effect:{key}",
                    replaces=True,
                )
            )
    return _dedup_buffs(buffs)


def _effect_sets_cover(sentence: str, buffs: Sequence[ParsedBuff]) -> bool:
    """精确数值替换是否覆盖了句中所有输出增益。"""
    if not buffs or "{" in sentence or "持续伤害" in sentence:
        return False
    set_values = re.findall(r"提升至\s*\d+(?:\.\d+)?%", sentence)
    return len(set_values) == len(buffs)


def _resolve_fork_effect(fork: CharacterFork) -> str:
    """武器特效文案：用资源里的描述模板 + 面板 lbd（玩家实际精炼数值）填占位符。"""
    if not fork.id:
        return ""
    path = _FORK_DATA_PATH / f"{fork.id}.json"
    if not path.exists():
        return ""
    desc = RawForkData.model_validate_json(path.read_text(encoding="utf-8")).effect.description
    if not desc:
        return ""
    lbd = fork.lbd

    def _sub(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return lbd[index] if index < len(lbd) else match.group(0)

    return _PLACEHOLDER_RE.sub(_sub, desc)


@lru_cache(maxsize=64)
def _skill_texts(char_id: str) -> tuple[str, ...]:
    """战技 / 终结技 / 被动的描述文案，用于扫描技能型增益。"""
    path = _CHAR_DATA_PATH / f"{char_id}.json"
    if not path.exists():
        return ()
    raw = RawCharData.model_validate_json(path.read_text(encoding="utf-8"))
    return tuple(
        phase.description
        for ability in raw.abilities
        if ability.type in {"skill", "ultraskill", "passive"}
        for phase in ability.phases
        if phase.description
    )


def _render_stat_value(stat: RawAbilityStat, level_index: int) -> str:
    """把结构化技能数值模板渲染到当前技能等级。"""
    rendered = stat.value_name
    for index, values in enumerate(stat.values):
        if not values:
            continue
        value = values[min(level_index, len(values) - 1)]
        rendered = rendered.replace(f"{{{index}}}", f"{value:g}")
    return rendered


def _stat_is_repeated_in_phases(ability: RawAbility, stat: RawAbilityStat, rendered: str) -> bool:
    """描述已含相同目标与百分数时，不重复登记结构化数值。"""
    percentages = _PERCENT_RE.findall(rendered)
    if not percentages:
        return False
    targets = _TARGET_NAME_RE.findall(stat.name)
    for phase in ability.phases:
        clean = _clean(phase.description)
        if all(value in clean for value in percentages) and all(target in clean for target in targets):
            return True
    return False


def _ability_stat_gap_texts(ability: RawAbility, level_index: int) -> list[str]:
    gaps: list[str] = []
    for stat in ability.stats:
        if _STRUCTURED_GAP_NAME_RE.search(stat.name) is None or not stat.values:
            continue
        rendered = _render_stat_value(stat, level_index)
        if _stat_is_repeated_in_phases(ability, stat, rendered):
            continue
        gaps.append(f"[结构化技能数值] {ability.name}：{stat.name} {rendered}")
    return gaps


@lru_cache(maxsize=64)
def _skill_stat_gap_texts(char_id: str) -> tuple[str, ...]:
    """全量审计用的结构化增益缺口，等级取资源首档。"""
    path = _CHAR_DATA_PATH / f"{char_id}.json"
    if not path.exists():
        return ()
    raw = RawCharData.model_validate_json(path.read_text(encoding="utf-8"))
    return tuple(
        text
        for ability in raw.abilities
        if ability.type in {"melee", "skill", "ultraskill", "qte"}
        for text in _ability_stat_gap_texts(ability, 0)
    )


def _active_skill_stat_gap_texts(character: CharacterDetail) -> tuple[str, ...]:
    """按实卡技能等级登记尚未可靠接入公式的结构化增益。"""
    path = _CHAR_DATA_PATH / f"{character.id}.json"
    if not path.exists():
        return ()
    levels = {skill.id.lower(): max(skill.level - 1, 0) for skill in character.skills if skill.id}
    raw = RawCharData.model_validate_json(path.read_text(encoding="utf-8"))
    return tuple(
        text
        for ability in raw.abilities
        if ability.id.lower() in levels and ability.type in {"melee", "skill", "ultraskill", "qte"}
        for text in _ability_stat_gap_texts(ability, levels[ability.id.lower()])
    )


def _active_skill_texts(character: CharacterDetail) -> tuple[str, ...]:
    """只扫描面板已解锁技能；未解锁被动 level=0 不得进入伤害。"""
    path = _CHAR_DATA_PATH / f"{character.id}.json"
    if not path.exists():
        return ()
    active_ids = {
        skill.id.lower() for skill in character.skills if skill.id and (skill.type != "Passive" or skill.level > 0)
    }
    raw = RawCharData.model_validate_json(path.read_text(encoding="utf-8"))
    return tuple(
        phase.description
        for ability in raw.abilities
        if ability.id.lower() in active_ids and ability.type in {"skill", "ultraskill", "passive"}
        for phase in ability.phases
        if phase.description
    )


@lru_cache(maxsize=64)
def _awaken_texts(char_id: str) -> tuple[str, ...]:
    """觉醒6条全量描述。**保持下标对齐**（EffectN→awaken[N-1]），不能按 desc 过滤。"""
    path = _CHAR_DATA_PATH / f"{char_id}.json"
    if not path.exists():
        return ()
    raw = RawCharData.model_validate_json(path.read_text(encoding="utf-8"))
    return tuple(a.desc for a in raw.awaken)


@lru_cache(maxsize=64)
def resonance_effects(char_id: str) -> tuple[tuple[str, int], ...]:
    """共鸣条目 (描述, 解锁所需觉醒等级 awaken_num)。共鸣1=觉3(技能等级提升)、共鸣2=觉6(战斗增益)。
    按 awaken_num 解锁（**不是**按列表前 slev 个切片）。"""
    path = _CHAR_DATA_PATH / f"{char_id}.json"
    if not path.exists():
        return ()
    raw = RawCharData.model_validate_json(path.read_text(encoding="utf-8"))
    return tuple((r.desc, r.awaken_num) for r in raw.resonance)


def _chosen_awaken(awaken_all: tuple[str, ...], awaken_effect: list[str]) -> list[str]:
    """异环觉醒可选：按面板 awaken_effect 选中的 EffectN 取 awaken[N-1]，而非顺序前 N 个。"""
    texts: list[str] = []
    for effect in awaken_effect:
        match = re.search(r"\d+", effect)
        if match is None:
            continue
        index = int(match.group()) - 1
        if 0 <= index < len(awaken_all) and awaken_all[index]:
            texts.append(awaken_all[index])
    return texts


def _named_stack_caps(sources: Sequence[tuple[str, str]]) -> dict[str, int]:
    """从当前已启用文案汇总具名资源的明确层数上限。"""
    caps: dict[str, int] = {}
    for _, raw_text in sources:
        clean = _clean(raw_text)
        for pattern in _NAMED_STACK_CAP_RES:
            for match in pattern.finditer(clean):
                key = match.group(1)
                count = _number_count(match.group(2))
                caps[key] = max(caps[key], count) if key in caps else count
    return caps


@lru_cache(maxsize=64)
def _resource_stack_caps(char_id: str) -> dict[str, int]:
    """从角色全技能资源补足层数上限，不把无关普攻文案加入 buff 扫描。"""
    path = _CHAR_DATA_PATH / f"{char_id}.json"
    if not path.exists():
        return {}
    raw = RawCharData.model_validate_json(path.read_text(encoding="utf-8"))
    texts = [phase.description for ability in raw.abilities for phase in ability.phases if phase.description]
    caps = _named_stack_caps([("资源", text) for text in texts])
    return caps


def _bind_ignore_replacements(items: Sequence[ParsedBuff]) -> list[ParsedBuff]:
    """把“提升至无视”只绑定到同来源中最近的基础穿透。"""
    linked = list(items)
    claimed: set[int] = set()
    for index, item in enumerate(tuple(linked)):
        if item.kind not in {"def_ignore", "res_ignore"} or "提升至无视" not in item.text:
            continue
        base_index = next(
            (
                candidate_index
                for candidate_index in range(index - 1, -1, -1)
                if candidate_index not in claimed
                and linked[candidate_index].source == item.source
                and linked[candidate_index].kind == item.kind
                and "提升至无视" not in linked[candidate_index].text
            ),
            None,
        )
        if base_index is None:
            continue
        effect_key = f"ignore:{item.source}:{item.kind}:{index}"
        linked[base_index] = replace(linked[base_index], effect_key=effect_key)
        linked[index] = replace(item, effect_key=effect_key, replaces=True)
        claimed.add(base_index)
    return linked


def _resolve_buff_relations(items: Sequence[ParsedBuff], stack_caps: dict[str, int]) -> list[ParsedBuff]:
    """补齐跨句层数上限，并把“提升至”绑定到被替换的基础效果。"""
    normalized: list[ParsedBuff] = []
    for item in _bind_ignore_replacements(items):
        if item.stack_key and item.stack_key in stack_caps:
            normalized.append(
                replace(
                    item,
                    peak_value=item.value * stack_caps[item.stack_key],
                    stacked=True,
                    conditional=True,
                    effect_key=item.effect_key or f"stack:{item.stack_key}",
                )
            )
        else:
            normalized.append(item)

    replacements = [item for item in normalized if item.replaces and item.effect_key]
    bound: list[ParsedBuff] = []
    for item in normalized:
        if item.replaces or item.effect_key:
            bound.append(item)
            continue
        replacement = next(
            (
                candidate
                for candidate in replacements
                if candidate.kind == item.kind and candidate.effect_key.split(":", 1)[-1] in item.text
            ),
            None,
        )
        bound.append(replace(item, effect_key=replacement.effect_key) if replacement is not None else item)

    resolved: list[ParsedBuff] = []
    for item in bound:
        if not item.replaces or not item.effect_key:
            resolved.append(item)
            continue
        key = item.effect_key.split(":", 1)[-1]
        bases = [
            candidate
            for candidate in bound
            if not candidate.replaces
            and candidate.kind == item.kind
            and (candidate.effect_key == item.effect_key or key in candidate.text)
        ]
        if not bases:
            resolved.append(item)
            continue
        base = bases[0]
        resolved.append(
            replace(
                item,
                element=base.element,
                scope=base.scope,
                applies_to_owner=base.applies_to_owner,
                is_team=base.is_team,
                conditional=item.conditional or base.conditional,
                panel_included=False,
            )
        )
    return _dedup_buffs(resolved)


def _scan_sentence(
    sentence: str,
    source: str,
    team: list[ParsedBuff],
    self_buffs: list[ParsedBuff],
    enemy: list[EnemyDebuff],
    unparsed: list[str],
    orphans: list[str],
    inherited_scope: str = "",
    inherited_conditional: bool = False,
) -> None:
    unmodeled_damage = _is_unmodeled_damage(sentence)
    unmodeled_buff = _is_unmodeled_buff(sentence)
    stats = _extract_stats(sentence)
    matched = False

    for hit in stats:
        scope = hit.scope or (inherited_scope if hit.kind in {"dmg_pct", "final_dmg_pct"} else "")
        buff = ParsedBuff(
            kind=hit.kind,
            value=hit.value,
            source=source,
            text=sentence,
            element=hit.element,
            scope=scope,
            applies_to_owner=hit.applies_to_owner,
            is_team=hit.is_team,
            peak_value=hit.peak_value,
            conditional=hit.conditional or inherited_conditional,
            stacked=hit.stacked,
            stack_key=hit.stack_key,
            effect_key=f"stack:{hit.stack_key}" if hit.stack_key else "",
            panel_included=(not hit.is_team and not hit.conditional and not scope and hit.kind in _PANEL_STAT_KINDS),
        )
        (team if hit.is_team else self_buffs).append(buff)
        matched = True

    for pattern, kind in _ENEMY_RULES:
        found = pattern.search(sentence)
        if found is not None:
            value = _first_num(found) / 100.0
            enemy.append(
                EnemyDebuff(
                    kind=kind,
                    value=value,
                    source=source,
                    text=sentence,
                    peak_value=_peak_value(sentence, found, value, _stack_key_at(sentence, found.end())),
                    conditional=is_conditional(sentence) or inherited_conditional,
                )
            )
            matched = True
            break

    for pattern, kind in _IGNORE_RULES:
        found = pattern.search(sentence)
        if found is not None:
            is_team, applies_to_owner = _target_for(sentence, found.start(), found.end())
            buff = ParsedBuff(
                kind=kind,
                value=_first_num(found) / 100.0,
                source=source,
                text=sentence,
                scope=_ignore_scope(sentence, found.start(), found.end()),
                applies_to_owner=applies_to_owner,
                is_team=is_team,
                peak_value=_peak_value(sentence, found, _first_num(found) / 100.0, ""),
                conditional=is_conditional(sentence) or inherited_conditional,
            )
            (team if is_team else self_buffs).append(buff)
            matched = True
            break

    multiplier_buffs = [
        replace(buff, conditional=buff.conditional or inherited_conditional)
        for buff in _extract_multiplier_buffs(sentence, source)
    ]
    if multiplier_buffs:
        self_buffs.extend(multiplier_buffs)
        matched = True

    effect_set_buffs = [
        replace(buff, conditional=buff.conditional or inherited_conditional)
        for buff in _extract_effect_set_buffs(sentence, source)
    ]
    if effect_set_buffs:
        self_buffs.extend(effect_set_buffs)
        matched = True

    if unmodeled_damage:
        orphans.append(f"[{source}] {sentence.strip()}")

    multiplier_set_covered = "{" not in sentence and any(buff.kind == "mult_set" for buff in multiplier_buffs)
    if matched and unmodeled_buff and not _effect_sets_cover(sentence, effect_set_buffs) and not multiplier_set_covered:
        unparsed.append(f"[{source}] {sentence.strip()}")

    if matched:
        return

    if _is_non_offensive(sentence):
        return
    if unmodeled_damage:
        return
    if unmodeled_buff:
        unparsed.append(f"[{source}] {sentence.strip()}")
        return

    if is_orphan_damage(sentence):
        orphans.append(f"[{source}] {sentence.strip()}")
        return

    is_team = _is_team_scope(sentence)
    is_self = not is_team and _SKIP_TOKEN not in sentence and any(t in sentence for t in _SELF_TRIGGERS)
    if (
        "%" in sentence
        and (is_team or is_self)
        and any(t in sentence for t in _BUFFY_TOKENS)
        and not _DAMAGE_DESC_RE.search(sentence)
    ):
        unparsed.append(f"[{source}] {sentence.strip()}")


def scan_character_buffs(character: CharacterDetail) -> BuffScan:
    """扫描单角色：全队增益 / 自身条件增益 / 敌人减益。set des2 是自身常驻（已在面板），不重复计。"""
    team: list[ParsedBuff] = []
    self_buffs: list[ParsedBuff] = []
    enemy: list[EnemyDebuff] = []
    unparsed: list[str] = []
    orphans: list[str] = []
    # 觉醒按 Effect 编号选取；共鸣按觉醒等级解锁。
    awaken_lev = character.awaken_lev
    sources: list[tuple[str, str]] = [
        ("套装2件", character.suit.des2),
        ("套装4件", character.suit.des4),
        ("武器", _resolve_fork_effect(character.fork)),
        *[("觉醒", text) for text in _chosen_awaken(_awaken_texts(character.id), character.awaken_effect)],
        *[("共鸣", desc) for desc, awaken_num in resonance_effects(character.id) if awaken_lev >= awaken_num],
        *[("技能", text) for text in _active_skill_texts(character)],
        *[("技能数值", text) for text in _active_skill_stat_gap_texts(character)],
    ]
    for source, raw_text in sources:
        if not raw_text:
            continue
        for unit in source_sentences(raw_text):
            _scan_sentence(
                unit.text,
                source,
                team,
                self_buffs,
                enemy,
                unparsed,
                orphans,
                inherited_scope=unit.inherited_scope,
                inherited_conditional=unit.inherited_conditional,
            )
    stack_caps = dict(_resource_stack_caps(character.id))
    for key, count in _named_stack_caps(sources).items():
        stack_caps[key] = max(stack_caps[key], count) if key in stack_caps else count
    return BuffScan(
        owner=character.name,
        team_buffs=tuple(_resolve_buff_relations(team, stack_caps)),
        self_buffs=tuple(_resolve_buff_relations(self_buffs, stack_caps)),
        enemy_debuffs=tuple(_dedup_debuffs(enemy)),
        unparsed=tuple(dict.fromkeys(unparsed)),
        orphans=tuple(dict.fromkeys(orphans)),
    )


def _dedup_buffs(items: Iterable[ParsedBuff]) -> list[ParsedBuff]:
    """同一增益只留一份；元素、作用域和受益目标不同则分别保留。"""
    seen: set[tuple[str, float, str, str, str, str, bool, bool, float | None, bool, bool, str, bool, bool]] = set()
    out: list[ParsedBuff] = []
    for item in items:
        key = (
            item.kind,
            round(item.value, 4),
            item.source,
            item.text,
            item.element,
            item.scope,
            item.applies_to_owner,
            item.is_team,
            round(item.peak_value, 4) if item.peak_value is not None else None,
            item.conditional,
            item.stacked,
            item.effect_key,
            item.replaces,
            item.panel_included,
        )
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _dedup_debuffs(items: Iterable[EnemyDebuff]) -> list[EnemyDebuff]:
    """同一敌人减益只留一份。"""
    seen: set[tuple[str, float, str, str, float | None, bool]] = set()
    out: list[EnemyDebuff] = []
    for item in items:
        key = (
            item.kind,
            round(item.value, 4),
            item.source,
            item.text,
            round(item.peak_value, 4) if item.peak_value is not None else None,
            item.conditional,
        )
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _active_buff(buff: ParsedBuff, scenario: DamageScenario) -> bool:
    if buff.panel_included:
        return False
    return scenario is DamageScenario.FULL_TRIGGER or not buff.conditional


def _buff_value(buff: ParsedBuff, scenario: DamageScenario) -> float:
    if scenario is DamageScenario.FULL_TRIGGER and buff.peak_value is not None:
        return buff.peak_value
    return buff.value


def _aggregate_buffs(
    buffs: Sequence[ParsedBuff],
    kind: str,
    scenario: DamageScenario,
) -> float:
    """同机制的明确升级替换基础值，其余独立效果相加。"""
    total = 0.0
    groups: dict[str, list[ParsedBuff]] = {}
    for buff in buffs:
        if buff.kind != kind or not _active_buff(buff, scenario):
            continue
        key = buff.effect_key
        if not key:
            total += _buff_value(buff, scenario)
            continue
        groups.setdefault(key, []).append(buff)

    for group in groups.values():
        replacements = [buff for buff in group if buff.replaces]
        chosen = replacements if replacements else group
        if replacements:
            total += max(_buff_value(buff, scenario) for buff in chosen)
        else:
            total += sum(_buff_value(buff, scenario) for buff in chosen)
    return total


def bundle_from(
    buffs: Sequence[ParsedBuff],
    element: str = "",
    scenario: DamageScenario = DamageScenario.FULL_TRIGGER,
) -> BuffBundle:
    """汇总**全局**增益成 BuffBundle（scope 非空的来源限定增益不计入，避免平摊到所有段=超算）。
    元素限定增伤（buff.element 非空）只在与成员 element 同元素时计入。来源限定增益由
    bundle_for_segment 在逐段结算时按 ability.type/name 单独折算。"""

    def total(kind: str) -> float:
        selected = [buff for buff in buffs if not buff.scope and (not buff.element or buff.element == element)]
        return _aggregate_buffs(selected, kind, scenario)

    return BuffBundle(
        atk_pct=total("atk_pct"),
        def_pct=total("def_pct"),
        hp_pct=total("hp_pct"),
        dmg_pct=total("dmg_pct"),
        final_dmg_pct=total("final_dmg_pct"),
        crit_rate=total("crit_rate"),
        crit_dmg=total("crit_dmg"),
        def_ignore=total("def_ignore"),
        res_ignore=total("res_ignore"),
    )


def bundle_for_segment(
    buffs: Sequence[ParsedBuff],
    element: str,
    ability_type: str,
    ability_name: str,
    segment_name: str,
    scenario: DamageScenario = DamageScenario.FULL_TRIGGER,
) -> BuffBundle:
    """单段的**来源限定**增益增量（只取 scope 非空且命中本段的 buff）。

    攻击/防御/生命增益恒为全局；限定段的增伤、暴击与穿透在这里折算。
    """

    def total(kind: str) -> float:
        selected = [
            buff
            for buff in buffs
            if buff.scope
            and scope_matches(buff.scope, ability_type, ability_name, segment_name)
            and (not buff.element or buff.element == element)
        ]
        return _aggregate_buffs(selected, kind, scenario)

    return BuffBundle(
        dmg_pct=total("dmg_pct"),
        final_dmg_pct=total("final_dmg_pct"),
        crit_rate=total("crit_rate"),
        crit_dmg=total("crit_dmg"),
        def_ignore=total("def_ignore"),
        res_ignore=total("res_ignore"),
    )


def _multiplier_matches(buff: ParsedBuff, segment_name: str) -> bool:
    scopes = buff.scope.split("|") if buff.scope else []
    core_hit = any(scope in segment_name for scope in scopes)
    desc_hit = len(segment_name) > len("伤害倍率") and segment_name in buff.text
    if not (core_hit or desc_hit):
        return False
    if ("额外伤害倍率" in buff.text) != ("额外" in segment_name):
        return False
    return not (segment_name.startswith("自动") and "主动" in buff.text)


def segment_mult(
    buffs: Sequence[ParsedBuff],
    segment_name: str,
    scenario: DamageScenario = DamageScenario.FULL_TRIGGER,
) -> float:
    """命中本段的相对倍率提升合计。"""
    selected = [buff for buff in buffs if _multiplier_matches(buff, segment_name)]
    return _aggregate_buffs(selected, "mult_pct", scenario)


def segment_mult_override(
    buffs: Sequence[ParsedBuff],
    segment_name: str,
    scenario: DamageScenario = DamageScenario.FULL_TRIGGER,
) -> float | None:
    """命中本段的绝对倍率设值，返回倍率百分数。"""
    values = [
        _buff_value(buff, scenario) * 100.0
        for buff in buffs
        if buff.kind == "mult_set" and _multiplier_matches(buff, segment_name) and _active_buff(buff, scenario)
    ]
    return max(values) if values else None


def enemy_mods(
    debuffs: Sequence[EnemyDebuff],
    scenario: DamageScenario = DamageScenario.FULL_TRIGGER,
) -> tuple[float, float]:
    """聚合敌人减防 / 减抗，返回 (减防, 减抗)。减防封顶 0.9 防止防御区失真。"""

    def value(debuff: EnemyDebuff) -> float:
        if scenario is DamageScenario.FULL_TRIGGER and debuff.peak_value is not None:
            return debuff.peak_value
        return debuff.value

    active = [debuff for debuff in debuffs if scenario is DamageScenario.FULL_TRIGGER or not debuff.conditional]
    def_reduction = min(0.9, sum(value(debuff) for debuff in active if debuff.kind == "def_reduction"))
    res_reduction = sum(value(debuff) for debuff in active if debuff.kind == "res_reduction")
    return def_reduction, res_reduction
