from __future__ import annotations

from .constants import GAME_ID_HUANTA, GAME_ID_YIHUAN

# 参与签到的游戏 → 签到开关 config key（None = 强制签）；新增游戏只加一条。
# 顺序即优先级，第一条是主游戏：get_active 默认只看它，登录时为它自动绑主角色
GAME_SIGN_SWITCHES: dict[str, str | None] = {
    GAME_ID_YIHUAN: None,
    GAME_ID_HUANTA: "NTESignHuanta",
}

PRIMARY_GAME_ID: str = next(iter(GAME_SIGN_SWITCHES))

GAME_LABELS: dict[str, str] = {
    GAME_ID_YIHUAN: "异环",
    GAME_ID_HUANTA: "幻塔",
}

# 顶部 banner 资源 key —— `utils/texture2d/home-{key}.webp`
GAME_BANNER_KEYS: dict[str, str] = {
    GAME_ID_YIHUAN: "yihuan",
    GAME_ID_HUANTA: "huanta",
}


def disabled_sign_games() -> set[str]:
    """当前 config 下被关掉的游戏 game_id。开关为 None 的游戏永远参与签到。"""
    from ..nte_config.nte_config import NTEConfig

    return {
        gid
        for gid, switch in GAME_SIGN_SWITCHES.items()
        if switch is not None and not NTEConfig.get_config(switch).data
    }
