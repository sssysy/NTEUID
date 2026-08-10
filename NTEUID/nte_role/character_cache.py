from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from gsuid_core.logger import logger

from ..utils.database import NTECharData
from ..scoring.registry import get_scorer
from ..utils.sdk.tajiduo_model import CharacterDetail


async def save_character_cache(role_id: str, raw_characters: list[dict[str, Any]]) -> None:
    """整账号覆盖个人数据表：每个角色一行存完整 detail + 评分（不可评分 grade 留空）。
    刷新面板和登录预拉都走这里，保证评分随快照一起落库；身份(user_id/role_name)归 NTEUser。
    行内 `scorer` 标记产出该分数的评分 provider，排行只在同 provider 的行之间比较。
    """
    scorer = await get_scorer()
    entries: list[tuple[str, dict[str, Any], CharacterDetail | None]] = []
    for raw in raw_characters:
        char_id = str(raw.get("id", ""))
        if not char_id:
            continue
        try:
            detail = CharacterDetail.model_validate(raw)
        except ValidationError as error:
            # 单个角色解析异常只让它算「不可评分」(grade 留空)，不连累整账号落库
            logger.debug(f"[NTE角色] 角色快照解析失败 uid={role_id} char={char_id}: {error!r}")
            detail = None
        entries.append((char_id, raw, detail))

    valid = [detail for _, _, detail in entries if detail is not None]
    batch = await scorer.score_batch(valid)
    if len(batch) != len(valid):
        raise ValueError(f"评分包违反契约: scorer={scorer.scorer_id} score_batch 返回 {len(batch)}/{len(valid)}")
    results = iter(batch)
    rows: list[dict[str, Any]] = []
    for char_id, raw, detail in entries:
        result = next(results) if detail is not None else None
        score, grade = (result.score, result.grade) if result is not None else (0, "")
        rows.append(
            {
                "char_id": char_id,
                "detail": json.dumps(raw, ensure_ascii=False),
                "score": score,
                "grade": grade,
                "scorer": scorer.scorer_id,
            }
        )
    await NTECharData.replace_for_uid(role_id, rows)


async def load_character_cache(role_id: str) -> list[CharacterDetail]:
    """从数据库读该账号全部角色 detail 并解析；无数据 / 损坏 / 模型不兼容都跳过。"""
    out: list[CharacterDetail] = []
    for detail in await NTECharData.list_for_uid(role_id):
        try:
            out.append(CharacterDetail.model_validate(json.loads(detail)))
        except (json.JSONDecodeError, ValidationError) as error:
            logger.warning(f"[NTE角色] 角色快照解析失败 uid={role_id}: {error!r}")
    return out


async def load_character_detail_cache(role_id: str, char_id: str) -> CharacterDetail | None:
    """从数据库精确读取某账号的某个角色 detail；单角色面板用。"""
    detail = await NTECharData.detail_for_uid_char(role_id, char_id)
    if detail is None:
        return None
    try:
        return CharacterDetail.model_validate(json.loads(detail))
    except (json.JSONDecodeError, ValidationError) as error:
        logger.warning(f"[NTE角色] 角色快照解析失败 uid={role_id} char={char_id}: {error!r}")
        return None


async def has_character_cache(role_id: str) -> bool:
    return await NTECharData.has_for_uid(role_id)
