from __future__ import annotations

import base64
import asyncio
from io import BytesIO
from typing import Literal
from pathlib import Path

from PIL import Image

from gsuid_core.utils.html_render import _ensure_renderer, render_html_to_bytes

FONT_PATH = Path(__file__).parent / "fonts" / "nte_fonts.ttf"
_font_registered = False


def _register_font() -> None:
    global _font_registered
    if _font_registered:
        return
    _ensure_renderer().register_font(FONT_PATH.read_bytes(), name="NTEFont")
    _font_registered = True


async def render_card(
    html: str,
    width: int = 360,
    dpr: float = 2.0,
    *,
    image_format: Literal["png", "jpeg"] = "png",
    jpeg_quality: int = 85,
) -> bytes:
    await asyncio.to_thread(_register_font)
    return await render_html_to_bytes(
        html,
        max_width=width * dpr,
        dpi=96 * dpr,
        font_name="NTEFont",
        root_max_width=width,
        image_format=image_format,
        jpeg_quality=jpeg_quality,
    )


async def data_uri(path: Path, mime: str) -> str:
    data = await asyncio.to_thread(path.read_bytes)
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


async def image_data_uri(img: Image.Image) -> str:
    def _encode() -> str:
        buf = BytesIO()
        img.save(buf, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

    return await asyncio.to_thread(_encode)
