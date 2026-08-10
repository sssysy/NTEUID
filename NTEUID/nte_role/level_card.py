from __future__ import annotations

from pathlib import Path
from functools import lru_cache
from dataclasses import dataclass

from PIL import Image, ImageOps, ImageDraw

from gsuid_core.models import Event
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.utils.image.image_tools import get_event_avatar

from .heartlike import heart_level
from ..utils.image import (
    COLOR_WHITE,
    add_footer,
    get_nte_bg,
    open_texture,
    char_img_ring,
    make_head_avatar,
)
from ..scoring.contract import Scorer
from ..scoring.registry import get_scorer, grade_badge, grade_color
from ..utils.resource.cdn import get_avatar_img, get_weapon_img, get_char_element_img
from ..utils.fonts.nte_fonts import nte_font_origin
from ..utils.sdk.tajiduo_model import CharElement, CharQuality, CharacterDetail

LEVEL_TEX = Path(__file__).parent / "texture2d" / "level"

WIDTH = 1860
BANNER_H = 300
COLHEAD_H = 80
ROW_H = 136
ROW_GAP = 14
ROW_X0 = 24
ROW_W = 1832
AVATAR = 104

# 异环「版本福利 box」洋红 + 宇宙青配色。
MAGENTA = (236, 72, 150)
CYAN = (96, 208, 232)
SUBTEXT = (198, 184, 224)
HEART_PINK = (255, 150, 196)
AWAKEN_GOLD = (255, 208, 96)
RANK_COLOR = {1: (255, 208, 96), 2: (214, 220, 236), 3: (227, 170, 120)}
# 品级染角色名（CharQuality：S橙 A紫 B蓝 C绿 N白）
QUALITY_NAME = {
    CharQuality.S: (255, 178, 92),
    CharQuality.A: (198, 150, 248),
    CharQuality.B: (118, 178, 248),
    CharQuality.C: (120, 214, 162),
    CharQuality.N: (224, 224, 236),
}


@dataclass(frozen=True, slots=True)
class LevelEntry:
    char_id: str
    name: str
    quality: CharQuality
    element: CharElement
    alev: int
    awaken: int
    heart: int
    skills: tuple[int, ...]
    fork_id: str
    fork_name: str
    fork_stage: int
    suit_pieces: int
    score: int | None
    grade: str


async def build_level_entries(scorer: Scorer, characters: list[CharacterDetail]) -> list[LevelEntry]:
    """每角色解出练度字段，按「装备评分降序 → 不可评分置后 → 品级>等级>觉醒」排序。"""
    results = await scorer.score_batch(characters)
    if len(results) != len(characters):
        raise ValueError(f"评分包违反契约: scorer={scorer.scorer_id} score_batch 返回 {len(results)}/{len(characters)}")
    entries: list[LevelEntry] = []
    for char, result in zip(characters, results):
        score = result.score if result is not None else None
        grade = result.grade if result is not None else ""
        skills = tuple(skill.level for skill in char.skills if skill.type == "Proactive")[:4]
        fork = char.fork
        stage = int(fork.slev) if fork.slev.isdigit() else 0
        entries.append(
            LevelEntry(
                char_id=char.id,
                name=char.name,
                quality=char.quality,
                element=char.element_type,
                alev=char.alev,
                awaken=char.awaken_lev,
                heart=heart_level(char.likeability_lev),
                skills=skills,
                fork_id=fork.id,
                fork_name=fork.name,
                fork_stage=stage,
                suit_pieces=char.suit.suit_activate_num,
                score=score,
                grade=grade,
            )
        )

    def key(entry: LevelEntry) -> tuple[bool, int, int, int, int, str]:
        rank_score = entry.score if entry.score is not None else 0
        return (entry.score is None, -rank_score, -entry.quality.rank, -entry.alev, -entry.awaken, entry.char_id)

    return sorted(entries, key=key)


def _fit(draw: ImageDraw.ImageDraw, text: str, width: int, font) -> str:
    if round(draw.textlength(text, font=font)) <= width:
        return text
    while text and round(draw.textlength(f"{text}...", font=font)) > width:
        text = text[:-1]
    return f"{text}..." if text else "..."


def _vgrad(w: int, h: int, color: tuple[int, int, int], a_top: int, a_bottom: int) -> Image.Image:
    strip = Image.new("RGBA", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        strip.putpixel((0, y), color + (round(a_top + (a_bottom - a_top) * t),))
    return strip.resize((w, h), Image.Resampling.BILINEAR)


@lru_cache(maxsize=1)
def _title_banner() -> Image.Image:
    return ImageOps.fit(
        open_texture(LEVEL_TEX / "title.png"), (WIDTH, BANNER_H), method=Image.Resampling.LANCZOS, centering=(0.5, 0.3)
    )


@lru_cache(maxsize=1)
def _marquee() -> Image.Image:
    return open_texture(LEVEL_TEX / "marquee.png", (ROW_W, 54))


@lru_cache(maxsize=8)
def _stretch_bar(name: str, height: int, cap: int) -> Image.Image:
    """切角条带按目标高度等比缩放，再横向只拉中段到 ROW_W：左右端(像素格/切角)保持原比例不变形。"""
    asset = open_texture(LEVEL_TEX / name)
    w0 = round(asset.width * height / asset.height)
    scaled = asset.resize((w0, height), Image.Resampling.LANCZOS)
    if ROW_W <= w0:
        return scaled.resize((ROW_W, height), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (ROW_W, height))
    out.paste(scaled.crop((0, 0, cap, height)), (0, 0))
    out.paste(
        scaled.crop((cap, 0, w0 - cap, height)).resize((ROW_W - 2 * cap, height), Image.Resampling.LANCZOS), (cap, 0)
    )
    out.paste(scaled.crop((w0 - cap, 0, w0, height)), (ROW_W - cap, 0))
    return out


async def _draw_banner(
    canvas: Image.Image, draw: ImageDraw.ImageDraw, ev: Event, role_name: str, uid: str, total: int
) -> None:
    canvas.alpha_composite(_title_banner(), (0, 0))
    canvas.alpha_composite(_vgrad(WIDTH, 176, (8, 6, 20), 0, 220), (0, BANNER_H - 176))

    draw.text((48, 96), "面板练度统计", font=nte_font_origin(62), fill=COLOR_WHITE, anchor="lm")
    draw.line((52, 140, 372, 140), fill=MAGENTA, width=6)
    draw.line((52, 140, 252, 140), fill=CYAN, width=6)

    avatar = make_head_avatar(await get_event_avatar(ev), size=128, avatar_size=118)
    canvas.alpha_composite(avatar, (40, 164))
    draw.text((182, 208), role_name, font=nte_font_origin(44), fill=COLOR_WHITE, anchor="lm")
    draw.text((184, 260), f"UID {uid} · 共 {total} 名角色", font=nte_font_origin(28), fill=SUBTEXT, anchor="lm")


def _draw_colhead(canvas: Image.Image, draw: ImageDraw.ImageDraw) -> None:
    canvas.alpha_composite(_stretch_bar("colhead.png", COLHEAD_H, 150), (ROW_X0, BANNER_H))
    y = BANNER_H + COLHEAD_H // 2 - 2
    font = nte_font_origin(32)
    labels = (
        (165, "#"),
        (268, "角色"),
        (700, "Lv"),
        (786, "觉醒"),
        (872, "好感"),
        (956, "普"),
        (1018, "技"),
        (1080, "终"),
        (1142, "连"),
        (1390, "武器"),
        (1660, "套装 · 评分"),
    )
    for x, label in labels:
        draw.text((x, y), label, font=font, fill=COLOR_WHITE, anchor="mm")


async def _draw_row(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    y: int,
    rank: int,
    entry: LevelEntry,
    scorer: Scorer,
    avatar: Image.Image,
    element: Image.Image | None,
    row_img: Image.Image,
) -> None:
    canvas.alpha_composite(row_img, (ROW_X0, y))
    mid = y + ROW_H // 2

    draw.text((165, mid), str(rank), font=nte_font_origin(42), fill=RANK_COLOR.get(rank, COLOR_WHITE), anchor="mm")

    canvas.alpha_composite(char_img_ring(avatar, AVATAR), (218, mid - 52))
    if element is not None:
        canvas.alpha_composite(element.convert("RGBA").resize((44, 44), Image.Resampling.LANCZOS), (216, mid - 54))
    draw.text(
        (336, mid),
        _fit(draw, entry.name, 340, nte_font_origin(40)),
        font=nte_font_origin(40),
        fill=QUALITY_NAME[entry.quality],
        anchor="lm",
    )

    draw.text((700, mid), str(entry.alev), font=nte_font_origin(42), fill=COLOR_WHITE, anchor="mm")
    draw.text((786, mid), str(entry.awaken), font=nte_font_origin(42), fill=AWAKEN_GOLD, anchor="mm")
    draw.text((872, mid), str(entry.heart), font=nte_font_origin(42), fill=HEART_PINK, anchor="mm")

    for index in range(4):
        text = str(entry.skills[index]) if index < len(entry.skills) else "-"
        draw.text((956 + index * 62, mid), text, font=nte_font_origin(38), fill=COLOR_WHITE, anchor="mm")

    if entry.fork_id:
        icon = await get_weapon_img(entry.fork_id)
        if icon is not None:
            canvas.alpha_composite(icon.convert("RGBA").resize((72, 72), Image.Resampling.LANCZOS), (1188, mid - 36))
        draw.text(
            (1270, mid - 15),
            _fit(draw, entry.fork_name, 290, nte_font_origin(30)),
            font=nte_font_origin(30),
            fill=COLOR_WHITE,
            anchor="lm",
        )
        draw.text((1270, mid + 22), f"{entry.fork_stage}阶", font=nte_font_origin(24), fill=CYAN, anchor="lm")
    else:
        draw.text((1270, mid), "—", font=nte_font_origin(32), fill=SUBTEXT, anchor="lm")

    grade_icon = grade_badge(scorer, entry.grade, 54)
    if grade_icon is not None:
        canvas.alpha_composite(grade_icon, (1584, mid - 27))
    if entry.score is None:
        draw.text((1740, mid), "--", font=nte_font_origin(44), fill=SUBTEXT, anchor="rm")
    else:
        draw.text(
            (1740, mid - 10),
            str(entry.score),
            font=nte_font_origin(50),
            fill=grade_color(scorer, entry.grade),
            anchor="rm",
        )
        draw.text((1740, mid + 32), f"· {entry.suit_pieces}件", font=nte_font_origin(24), fill=SUBTEXT, anchor="rm")


async def draw_level_img(ev: Event, role_name: str, uid: str, characters: list[CharacterDetail]) -> bytes:
    scorer = await get_scorer()
    entries = await build_level_entries(scorer, characters)
    avatars = [await get_avatar_img(entry.char_id) for entry in entries]
    # 异环仅 6 种元素，按 element 去重，避免每行重复 open 同一图标
    element_imgs: dict[str, Image.Image | None] = {}
    for entry in entries:
        if entry.element.value not in element_imgs:
            element_imgs[entry.element.value] = await get_char_element_img(entry.element.value)

    height = BANNER_H + COLHEAD_H + len(entries) * ROW_H + max(0, len(entries) - 1) * ROW_GAP + 150
    canvas = get_nte_bg(WIDTH, height, bg="bg1").convert("RGBA")
    canvas.alpha_composite(Image.new("RGBA", (WIDTH, height), (10, 9, 24, 170)))
    draw = ImageDraw.Draw(canvas)

    await _draw_banner(canvas, draw, ev, role_name, uid, len(entries))
    _draw_colhead(canvas, draw)

    y = BANNER_H + COLHEAD_H
    row_img = _stretch_bar("row.png", ROW_H, 180)
    placeholder = Image.new("RGBA", (AVATAR, AVATAR), (40, 36, 60, 255))
    for index, (entry, avatar) in enumerate(zip(entries, avatars)):
        element = element_imgs[entry.element.value]
        await _draw_row(
            canvas, draw, y, index + 1, entry, scorer, avatar if avatar is not None else placeholder, element, row_img
        )
        y += ROW_H + ROW_GAP

    canvas.alpha_composite(_marquee(), (ROW_X0, y + 8))
    add_footer(canvas)
    return await convert_img(canvas)
