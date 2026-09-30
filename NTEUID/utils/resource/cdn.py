from __future__ import annotations

import random
from typing import ParamSpec
from pathlib import Path
from functools import wraps
from collections.abc import Callable, Awaitable

from PIL import Image

from ..image import download_pic_from_url
from .RESOURCE_PATH import (
    WEAPON_PATH,
    CHAR_ART_PATH,
    AREA_TYPE_PATH,
    AREA_WIDE_PATH,
    AREA_SMALL_PATH,
    CHAR_GROUP_PATH,
    CHAR_SKILL_PATH,
    ACHIEVEMENT_PATH,
    CHAR_AVATAR_PATH,
    CHAR_AWAKEN_PATH,
    CHAR_ELEMENT_PATH,
    VEHICLE_WIDE_PATH,
    CHAR_PROPERTY_PATH,
    VEHICLE_MODEL_PATH,
    CHAR_CITY_SKILL_PATH,
    CHAR_SUIT_DRIVE_PATH,
    STATIC_RESOURCE_PATH,
    CHAR_GROUP_BLACK_PATH,
    CHAR_SUIT_DETAIL_PATH,
    REALESTATE_DETAIL_PATH,
    REALESTATE_FURNITURE_PATH,
)

# 本地缓存目录
COMMON_LOCAL_DIR = STATIC_RESOURCE_PATH / "common"  # 通用
AVATAR_LOCAL_DIR = STATIC_RESOURCE_PATH / "char" / "avatar"  # 角色头像
FASHION_LOCAL_DIR = STATIC_RESOURCE_PATH / "char" / "fashion"  # 角色全身立绘
FORK_LOCAL_DIR = STATIC_RESOURCE_PATH / "fork"  # 武器
PROPERTY_LOCAL_DIR = COMMON_LOCAL_DIR / "property"  # 属性


def _load_random_local_image(local_dir: Path) -> Image.Image | None:
    if local_dir.is_dir():
        files = [f for f in local_dir.iterdir() if f.is_file()]
        if files:
            return Image.open(random.choice(files))
    return None


def _load_local_image(path: Path) -> Image.Image | None:
    return Image.open(path) if path.is_file() else None


CDN_BASE = "https://webstatic.tajiduo.com/bbs/yh-game-records-web-source"

P = ParamSpec("P")


def safe_load_image(
    loader: Callable[P, Awaitable[Image.Image]],
) -> Callable[P, Awaitable[Image.Image | None]]:
    """装饰 cdn loader：OSError 返回 None，并统一转 RGBA。
    调用方按 `Image.Image | None` 处理，None 走占位逻辑。"""

    @wraps(loader)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> Image.Image | None:
        try:
            image = await loader(*args, **kwargs)
        except OSError:
            return None
        return image.convert("RGBA")

    return wrapper


async def _get(local_dir: Path, rel: str) -> Image.Image:
    name = rel.rsplit("/", 1)[-1]
    return await download_pic_from_url(local_dir, f"{CDN_BASE}/{rel}", name=name)


# 区域进度主卡大图（横幅底图）。id 来自 home.json `areaProgress[].id`
@safe_load_image
async def get_area_wide_img(area_id: str) -> Image.Image:
    return await _get(AREA_WIDE_PATH, f"area/wide/{area_id}.png")


# 区域进度小卡底图（配进度条蒙版）
@safe_load_image
async def get_area_small_img(area_id: str) -> Image.Image:
    return await _get(AREA_SMALL_PATH, f"area/small/{area_id}.png")


# 区域子类型图标（谕石 / 电话亭 / 维特海默塔 / 打卡 等）
# id 来自 `/apihub/awapi/yh/areaProgress` 明细 `data[].detail[].id`
@safe_load_image
async def get_area_type_img(type_id: str) -> Image.Image:
    return await _get(AREA_TYPE_PATH, f"area/type/{type_id}.PNG")


# 成就大类图标。id 枚举: friendship / life / play / develop / interest / battle / quest / explore
@safe_load_image
async def get_achievement_img(category_id: str) -> Image.Image:
    return await _get(ACHIEVEMENT_PATH, f"achievement/{category_id}.png")


# 玩家头像方图。id 为 home.json `avatar`（通常是角色 id，官方前端遇 "None" 落回 "1"）
@safe_load_image
async def get_avatar_img(avatar_id: str) -> Image.Image:
    img = _load_random_local_image(AVATAR_LOCAL_DIR / avatar_id)
    if img is not None:
        return img
    return await _get(CHAR_AVATAR_PATH, f"avatar/square/{avatar_id}.PNG")


# 角色详情主图（面板中部半身）。id 为角色 id
@safe_load_image
async def get_char_detail_img(char_id: str) -> Image.Image:
    img = _load_random_local_image(FASHION_LOCAL_DIR / char_id)
    if img is not None:
        return img
    return await _get(CHAR_ART_PATH, f"character/detail/{char_id}.png")


# 阵营徽章（彩色版）。id 须是完整枚举值 `char.group_type.value`
@safe_load_image
async def get_char_group_img(group_id: str) -> Image.Image:
    return await _get(CHAR_GROUP_PATH, f"character/group/{group_id}.PNG")


# 阵营徽章（黑底版），同上用完整枚举值
@safe_load_image
async def get_char_group_black_img(group_id: str) -> Image.Image:
    return await _get(CHAR_GROUP_BLACK_PATH, f"character/group_black/{group_id}.PNG")


# 属性图标（魂 / 光 / 灵 / 咒 / 暗 / 相）。id 须是完整枚举值 `char.element_type.value`
@safe_load_image
async def get_char_element_img(element_id: str) -> Image.Image:
    return await _get(CHAR_ELEMENT_PATH, f"character/element/{element_id}.PNG")


# 单个觉醒效果图。effect 取自 home.json `awakenEffect[]` 单元素（Effect1…Effect6）
@safe_load_image
async def get_char_awaken_img(char_id: str, effect: str) -> Image.Image:
    return await _get(CHAR_AWAKEN_PATH, f"character/awaken/{char_id}_{effect}.png")


# 战技图标。id 来自 CharacterSkill.id，命名形如 `ga_<pinyin>_<type>`
@safe_load_image
async def get_char_skill_img(skill_id: str) -> Image.Image:
    return await _get(CHAR_SKILL_PATH, f"character/skill/{skill_id}.png")


# 城区技能图标。id 来自 CharacterDetail.city_skills[].id，形如 `city_ability_<pinyin>_NN`
@safe_load_image
async def get_char_city_skill_img(skill_id: str) -> Image.Image:
    return await _get(CHAR_CITY_SKILL_PATH, f"character/city_skill/{skill_id}.png")


# 弧盘外观图。id 为 CharacterFork.id（`fork_<拼音>`），空串表示未持有，需先判空；
# CDN 端路径仍叫 `character/fork/`，不要改
@safe_load_image
async def get_weapon_img(fork_id: str) -> Image.Image:
    img = _load_random_local_image(FORK_LOCAL_DIR / fork_id)
    if img is not None:
        return img
    return await _get(WEAPON_PATH, f"character/fork/{fork_id}.png")


# 属性条目图标。id 为 CharacterProperty.id（`hpmax` / `atk` / `crit` 等）
@safe_load_image
async def get_char_property_img(property_id: str) -> Image.Image:
    img = _load_local_image(PROPERTY_LOCAL_DIR / f"{property_id.lower()}.png")
    if img is not None:
        return img
    return await _get(CHAR_PROPERTY_PATH, f"character/property/{property_id}.png")


# 套装外观图与弧盘形状图标共用路径：id 为 CharacterSuit.id（`suit4`），
# 或 suit_condition[] 里的弧盘形状（`equipmentgeometry_shu2_1` / `hen3_1` 等）
@safe_load_image
async def get_char_suit_detail_img(entry_id: str) -> Image.Image:
    return await _get(CHAR_SUIT_DETAIL_PATH, f"character/suit/detail/{entry_id}.png")


# 驱动盘条目图（core / pie 共用）。id 来自 CharacterSuitItem.id
@safe_load_image
async def get_char_suit_drive_img(drive_id: str) -> Image.Image:
    return await _get(CHAR_SUIT_DRIVE_PATH, f"character/suit/drive/{drive_id}.png")


# 房产整体展示图。id 来自 home.json `realestate.showId`，形如 `bigword_l_1` 维纳公寓
@safe_load_image
async def get_realestate_img(show_id: str) -> Image.Image:
    return await _get(REALESTATE_DETAIL_PATH, f"realestate/detail/{show_id}.png")


# 单件家具图。id 来自 `/realestate` 接口 `detail[].fdetail[].id`，形如 `SF_0001`
@safe_load_image
async def get_furniture_img(furniture_id: str) -> Image.Image:
    return await _get(REALESTATE_FURNITURE_PATH, f"realestate/fdetail/{furniture_id}.png")


# 载具装饰件小图。type 取自 `/vehicles` 的 `detail[].models[].type`（非外层 id）；
# 官方 CDN 目录就拼作 `verhicle`，改成 `vehicle` 会 404
@safe_load_image
async def get_vehicle_model_img(model_type: str) -> Image.Image:
    return await _get(VEHICLE_MODEL_PATH, f"verhicle/model/{model_type}.png")


# 载具宽幅展示图。id 来自 home.json `vehicle.showId`，形如 `vehicle007` C2000
@safe_load_image
async def get_vehicle_wide_img(show_id: str) -> Image.Image:
    return await _get(VEHICLE_WIDE_PATH, f"verhicle/wide/{show_id}.png")
