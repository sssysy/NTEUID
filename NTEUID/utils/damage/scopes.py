from __future__ import annotations

import re

# 增益作用域单一真源。
#
# 来源限定增伤只加到对应伤害段，不能折入全局增伤区。
#
# 引号内的技能类型优先绑定 ability.type，其余名称绑定具体技能。

# 长关键词优先，避免短词抢先命中。
KEYWORD_TO_SCOPE: dict[str, str] = {
    "普通攻击": "type:melee",
    "普攻": "type:melee",
    "变轨技能": "type:skill",
    "极轨终结": "type:ultraskill",
    "极轨": "type:ultraskill",
    "终结": "type:ultraskill",
    "援护技": "type:qte",
    "援护": "type:qte",
}
# 仅保留语义明确的段名关键词。
SEGMENT_KEYWORDS: tuple[str, ...] = ("反击", "下落", "分支")
# 没有对应倍率段的伤害类型只报告，不折算。
ORPHAN_KEYWORDS: tuple[str, ...] = ("附着物伤害", "附着伤害", "持续伤害", "心灵伤害", "倾陷伤害")
# scope → 展示标签，供卡片/报告呈现作用域。
SCOPE_TO_LABEL: dict[str, str] = {
    "type:melee": "普攻",
    "type:skill": "变轨技能",
    "type:ultraskill": "极轨终结",
    "type:qte": "援护技",
}

# 只有直接管辖伤害短语的名称才构成作用域。
_DMG_TAIL = (
    r"(?:所?造成的?|的)?(?:额外)?(?:[光灵咒暗魂相]属性)?(?:异能)?(?:最终)?"
    r"(?:暴击伤害|暴击率|暴伤|伤害)"
)
_QUOTE_SCOPE_RE = re.compile(r"[「『]([^」』]+)[」』]" + _DMG_TAIL)
_KW_ALT = "|".join(sorted(KEYWORD_TO_SCOPE, key=len, reverse=True))
_BARE_SCOPE_RE = re.compile(r"(" + _KW_ALT + r")" + _DMG_TAIL)
_SEG_SCOPE_RE = re.compile(r"(" + "|".join(SEGMENT_KEYWORDS) + r")" + _DMG_TAIL)

# 条件目标可夹在来源与伤害短语之间，但不能跨子句。
_QUOTE_TARGET_SCOPE_RE = re.compile(r"[「『]([^」』]+)[」』][^，。；]{0,16}?" + _DMG_TAIL)
_QUOTE_VALUE_SCOPE_RE = re.compile(
    r"[「『]([^」』]+)[」』][^，。；]{0,10}?"
    r"(?:额外)?(?:获得|提升|提高|增加)\s*\d+(?:\.\d+)?%\s*(?:暴击伤害|暴击率|增伤)"
)

# 并列来源须共同紧邻同一个伤害短语。
_CONNECTOR = r"(?:和|与|、|及|以及|或)"
_SOURCE = r"(?:[「『][^」』]+[」』]|" + _KW_ALT + r")"
_MULTI_SCOPE_RE = re.compile(r"(" + _SOURCE + r"(?:" + _CONNECTOR + _SOURCE + r")+)" + _DMG_TAIL)
_SOURCE_RE = re.compile(r"[「『]([^」』]+)[」』]|(" + _KW_ALT + r")")


def _classify_quote(inner: str) -> str:
    """引号内文案 → scope。含来源类型词取 type:xxx；含段级词(反击/下落/分支)取 segment:；纯技能名降级 ability:<名>。"""
    for keyword in sorted(KEYWORD_TO_SCOPE, key=len, reverse=True):
        if keyword in inner:
            return KEYWORD_TO_SCOPE[keyword]
    for segment in SEGMENT_KEYWORDS:
        if segment in inner:
            return f"segment:{segment}"
    name = inner.split("：")[-1].split(":")[-1].strip()
    return f"ability:{name}" if name else ""


def extract_scope(sentence: str) -> str:
    """识别增益作用域标签。

    返回："" = 全局；单作用域 "type:…"/"ability:…"/"segment:…"；
    多作用域用 "|" 连接（如 "type:skill|type:ultraskill"，scope_matches 命中任一即算）。
    仅当限定词直接管辖伤害/暴击短语时才判定作用域，规避「释放『X』后…」触发语误判为限定。
    """
    multi = _MULTI_SCOPE_RE.search(sentence)
    if multi is not None:
        scopes: list[str] = []
        for quote, bare in _SOURCE_RE.findall(multi.group(1)):
            scope = _classify_quote(quote) if quote else KEYWORD_TO_SCOPE[bare]
            if scope and scope not in scopes:
                scopes.append(scope)
        if len(scopes) >= 2:
            return "|".join(scopes)
    quoted = _QUOTE_SCOPE_RE.search(sentence)
    if quoted is not None:
        return _classify_quote(quoted.group(1))
    bare = _BARE_SCOPE_RE.search(sentence)
    if bare is not None:
        return KEYWORD_TO_SCOPE[bare.group(1)]
    segment = _SEG_SCOPE_RE.search(sentence)
    if segment is not None:
        return f"segment:{segment.group(1)}"
    return ""


def extract_scope_at(sentence: str, start: int, end: int) -> str:
    """识别直接管辖指定增益短语的作用域，避免同句前后效果串扰。"""

    def overlaps(match: re.Match[str]) -> bool:
        return match.start() < end and start < match.end()

    multi = next((match for match in _MULTI_SCOPE_RE.finditer(sentence) if overlaps(match)), None)
    if multi is not None:
        scopes: list[str] = []
        for quote, bare in _SOURCE_RE.findall(multi.group(1)):
            scope = _classify_quote(quote) if quote else KEYWORD_TO_SCOPE[bare]
            if scope and scope not in scopes:
                scopes.append(scope)
        if len(scopes) >= 2:
            return "|".join(scopes)

    quoted = next((match for match in _QUOTE_SCOPE_RE.finditer(sentence) if overlaps(match)), None)
    if quoted is not None:
        return _classify_quote(quoted.group(1))

    targeted = next((match for match in _QUOTE_TARGET_SCOPE_RE.finditer(sentence) if overlaps(match)), None)
    if targeted is not None and any(keyword in targeted.group(1) for keyword in KEYWORD_TO_SCOPE):
        return _classify_quote(targeted.group(1))

    valued = next((match for match in _QUOTE_VALUE_SCOPE_RE.finditer(sentence) if overlaps(match)), None)
    if valued is not None:
        return _classify_quote(valued.group(1))

    bare = next((match for match in _BARE_SCOPE_RE.finditer(sentence) if overlaps(match)), None)
    if bare is not None:
        return KEYWORD_TO_SCOPE[bare.group(1)]
    segment = next((match for match in _SEG_SCOPE_RE.finditer(sentence) if overlaps(match)), None)
    if segment is not None:
        return f"segment:{segment.group(1)}"
    return ""


def scope_matches(scope: str, ability_type: str, ability_name: str, segment_name: str) -> bool:
    """段级匹配。ability_type / ability_name 来自资源（_AbilityProfile），绝不靠段名反推 type。
    多作用域（"|" 连接）命中任一子作用域即算（如「普攻和极限反击」同时管 melee 段与极限反击段）。"""
    if not scope:
        return True
    if "|" in scope:
        return any(scope_matches(part, ability_type, ability_name, segment_name) for part in scope.split("|"))
    if scope.startswith("type:"):
        return ability_type == scope[len("type:") :]
    if scope.startswith("ability:"):
        name = scope[len("ability:") :]
        return name in ability_name or name in segment_name
    if scope.startswith("segment:"):
        return scope[len("segment:") :] in segment_name
    return False


def is_orphan_damage(sentence: str) -> bool:
    """伤害类型标签型增益（附着/持续/心灵伤害 + 百分比）：无对应倍率段，诚实 surface 不折算。"""
    return "%" in sentence and any(keyword in sentence for keyword in ORPHAN_KEYWORDS)
