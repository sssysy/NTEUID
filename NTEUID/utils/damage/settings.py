from __future__ import annotations

from .models import EnemyProfile
from .constants import DEFAULT_ENEMY_LEVEL, DEFAULT_ENEMY_RESIST


def base_enemy(
    *,
    level: int = DEFAULT_ENEMY_LEVEL,
    resist: float = DEFAULT_ENEMY_RESIST,
    def_reduction: float = 0.0,
    res_reduction: float = 0.0,
) -> EnemyProfile:
    """生成显式敌人假设；默认值只用于稳定审计，角色卡可从配置覆盖。"""
    return EnemyProfile(
        level=level,
        resist=resist,
        def_reduction=def_reduction,
        res_reduction=res_reduction,
    )
