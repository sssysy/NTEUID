from __future__ import annotations

from pathlib import Path
from functools import lru_cache
from dataclasses import dataclass

from PIL import Image, ImageDraw

from gsuid_core.utils.image.convert import convert_img

from ..utils.image import COLOR_WHITE, SmoothDrawer, add_footer, get_nte_bg, open_texture, char_img_ring
from ..scoring.contract import Scorer
from ..scoring.registry import grade_badge, grade_color
from ..utils.resource.cdn import get_avatar_img, get_char_suit_detail_img
from ..utils.fonts.nte_fonts import nte_font_origin

RANK_TEX = Path(__file__).parent / "texture2d" / "rank"

WIDTH = 1280
HEADER_H = 272
ROW_H = 152
ROW_GAP = 16
PANEL_X0 = 30
PANEL_W = 1250 - PANEL_X0
AVATAR = 124

# 配色沿用评分排名卡：深靛蓝底 + 评级三色。
INDIGO_TOP = (54, 50, 86)
INDIGO_BOTTOM = (28, 26, 48)
SUBTEXT = (192, 190, 224)


@dataclass(frozen=True, slots=True)
class BoardEntry:
    char_id: str
    char_name: str
    awaken_lev: int
    suit_id: str
    suit_name: str
    suit_pieces: int
    holder_name: str
    holder_uid: str
    score: int
    grade: str


@lru_cache(maxsize=8)
def _asset(name: str) -> Image.Image:
    return open_texture(RANK_TEX / name)


@lru_cache(maxsize=1)
def _row_frame() -> Image.Image:
    # 所有行统一 frame_3，不按名次区分
    img = _asset("frame_3.png")
    return img.crop(img.split()[3].getbbox()).resize((PANEL_W, ROW_H), Image.Resampling.LANCZOS)


def _fit(draw: ImageDraw.ImageDraw, text: str, width: int, font) -> str:
    if round(draw.textlength(text, font=font)) <= width:
        return text
    while text and round(draw.textlength(f"{text}...", font=font)) > width:
        text = text[:-1]
    return f"{text}..." if text else "..."


def _vgradient(h: int, alpha: int) -> Image.Image:
    strip = Image.new("RGBA", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        strip.putpixel(
            (0, y), tuple(round(INDIGO_TOP[i] + (INDIGO_BOTTOM[i] - INDIGO_TOP[i]) * t) for i in range(3)) + (alpha,)
        )
    return strip.resize((WIDTH, h), Image.Resampling.BILINEAR)


async def _draw_row(canvas: Image.Image, draw: ImageDraw.ImageDraw, y: int, entry: BoardEntry, scorer: Scorer) -> None:
    canvas.alpha_composite(_row_frame(), (PANEL_X0, y))
    mid = y + ROW_H // 2
    # 角色头像 + 觉醒角标（不放名次序号：已按分降序，名次靠分数体现）
    ay = y + (ROW_H - AVATAR) // 2
    char_avatar = await get_avatar_img(entry.char_id)
    canvas.alpha_composite(
        char_img_ring(
            char_avatar if char_avatar is not None else Image.new("RGBA", (AVATAR, AVATAR), (40, 36, 60, 255)), AVATAR
        ),
        (60, ay),
    )
    SmoothDrawer().rounded_rectangle((114, ay + 80, 180, ay + 116), 18, fill=(140, 120, 228, 245), target=canvas)
    draw.text((147, ay + 98), f"{entry.awaken_lev}觉", font=nte_font_origin(24), fill=COLOR_WHITE, anchor="mm")
    # 角色名 + 持有者
    draw.text(
        (212, mid - 24),
        _fit(draw, entry.char_name, 320, nte_font_origin(38)),
        font=nte_font_origin(38),
        fill=COLOR_WHITE,
        anchor="lm",
    )
    holder = f"持有 {entry.holder_name}" if entry.holder_name else f"UID {entry.holder_uid}"
    draw.text(
        (212, mid + 28),
        _fit(draw, holder, 320, nte_font_origin(22)),
        font=nte_font_origin(22),
        fill=SUBTEXT,
        anchor="lm",
    )
    # 套装图标 + 名称·件数
    icon = await get_char_suit_detail_img(entry.suit_id) if entry.suit_id else None
    if icon is not None:
        canvas.alpha_composite(icon.convert("RGBA").resize((78, 78), Image.Resampling.LANCZOS), (560, mid - 39))
    draw.text(
        (652, mid),
        _fit(draw, f"{entry.suit_name} · {entry.suit_pieces}件", 350, nte_font_origin(28)),
        font=nte_font_origin(28),
        fill=COLOR_WHITE,
        anchor="lm",
    )
    # 评级图标 + 分数
    badge = grade_badge(scorer, entry.grade, 76)
    if badge is not None:
        canvas.alpha_composite(badge, (1014, mid - 38))
    draw.text(
        (1196, mid - 16),
        str(entry.score),
        font=nte_font_origin(52),
        fill=grade_color(scorer, entry.grade),
        anchor="rm",
    )
    draw.text((1196, mid + 33), "分", font=nte_font_origin(22), fill=SUBTEXT, anchor="rm")


def _draw_header(canvas: Image.Image, draw: ImageDraw.ImageDraw, scope_label: str) -> None:
    scrim = Image.new("RGBA", (1, HEADER_H))
    for y in range(HEADER_H):
        scrim.putpixel((0, y), (14, 12, 28, int(40 + 170 * (y / (HEADER_H - 1)) ** 1.4)))
    canvas.alpha_composite(scrim.resize((WIDTH, HEADER_H), Image.Resampling.BILINEAR), (0, 0))
    logo = _asset("logo_yh.png")
    canvas.alpha_composite(
        logo.resize((round(logo.width * 120 / logo.height), 120), Image.Resampling.LANCZOS), (48, 38)
    )
    draw.text((52, 208), f"{scope_label}最强排行", font=nte_font_origin(60), fill=COLOR_WHITE, anchor="lm")
    SmoothDrawer().rounded_rectangle((54, 246, 360, 253), 3, fill=(251, 221, 188, 240), target=canvas)


async def draw_strongest_board_img(entries: list[BoardEntry], scope_label: str, scorer: Scorer) -> bytes:
    height = HEADER_H + len(entries) * ROW_H + max(0, len(entries) - 1) * ROW_GAP + 110
    canvas = get_nte_bg(WIDTH, height, bg="bg4").convert("RGBA")
    canvas.alpha_composite(_vgradient(height, 70))
    draw = ImageDraw.Draw(canvas)
    _draw_header(canvas, draw, scope_label)

    y = HEADER_H
    for entry in entries:
        await _draw_row(canvas, draw, y, entry, scorer)
        y += ROW_H + ROW_GAP

    add_footer(canvas)
    return await convert_img(canvas)
