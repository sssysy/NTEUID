from __future__ import annotations

from ..sdk.tajiduo_model import CharElement

# —— 当前社区公式采用的防御区等级常数 ——
ENEMY_DEF_LEVEL_CONST = 90
CHAR_DEF_LEVEL_CONST = 100

# —— 稳定审计默认值；角色卡允许从插件配置覆盖 ——
DEFAULT_ENEMY_LEVEL = 80
DEFAULT_ENEMY_RESIST = 0.2

# —— 角色元素 → 面板属性 id 后缀（取自真实面板 properties 的 id）——
# 注意：魂(PSYCHE) 在面板里写作 "syche" 而非 "psyche"，按真实数据来。
_ELEMENT_SUFFIX: dict[CharElement, str] = {
    CharElement.PSYCHE: "syche",
    CharElement.COSMOS: "cosmos",
    CharElement.NATURE: "nature",
    CharElement.INCANTATION: "incantation",
    CharElement.CHAOS: "chaos",
    CharElement.LAKSHANA: "lakshana",
}

# 面板里固定的属性 id
PROP_ATK = "atk"
PROP_DEF = "def"
PROP_HPMAX = "hpmax"
PROP_CRIT = "crit"
PROP_CRIT_DMG = "critdamage"
PROP_DMG_GENERAL = "damageupgeneral"


def element_dmg_prop(element: CharElement) -> str:
    """该元素「异能伤害增强」在面板里的属性 id，如 咒 → damageupincantation。"""
    return f"damageup{_ELEMENT_SUFFIX[element]}"
