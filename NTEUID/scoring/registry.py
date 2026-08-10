from __future__ import annotations

import sys
import asyncio
import importlib.util
from pathlib import Path
from functools import lru_cache

from PIL import Image, ImageDraw

from gsuid_core.logger import logger

from .contract import Scorer, GradeSpec
from ..utils.image import COLOR_WHITE, SmoothDrawer, open_texture
from ..nte_config.nte_config import NTEConfig
from ..utils.fonts.nte_fonts import nte_font_origin

# 外置评分包固定目录（scoring/scorers/，.gitignore 忽略，更新仓库不冲突；多套并存、配置切换）
SCORERS_PATH = Path(__file__).parent / "scorers"

_scorers: dict[str, Scorer] = {}
_active: Scorer | None = None
_specs_cache: dict[str, dict[str, GradeSpec]] = {}
_switch_lock = asyncio.Lock()
_loaded = False


def register_scorer(scorer: Scorer) -> None:
    if scorer.scorer_id in _scorers:
        raise ValueError(f"评分 provider 重复注册: {scorer.scorer_id}")
    try:
        author = scorer.meta.author
    except AttributeError as error:
        raise ValueError(f"评分包必须定义 meta = ScorerMeta(author=...): {scorer.scorer_id}") from error
    if not author:
        raise ValueError(f"评分包必须声明作者: {scorer.scorer_id} (ScorerMeta.author)")
    _scorers[scorer.scorer_id] = scorer


def _load_scorers() -> None:
    """内置 provider + 外置评分包（scoring/scorers/ 下的独立 git 仓库），导入即注册。

    评分包布局扁平：仓根就是包，<name>/__init__.py 即入口。挂载名与磁盘层级一致
    （scoring.scorers.<name>），包内相对导入（from ...contract）静态分析与运行时都成立。
    第三方包质量不可控：单个评分包加载失败只 warning 跳过，不拖垮整个插件。
    """
    global _loaded
    if _loaded:
        return
    _loaded = True
    from . import providers  # noqa: F401

    SCORERS_PATH.mkdir(exist_ok=True)
    scoring_pkg = __name__.rsplit(".", 1)[0]
    # scorers 无 __init__.py，按命名空间包导入充当父包（子模块 import 需要完整父链）
    importlib.import_module(f"{scoring_pkg}.scorers")
    for path in sorted(SCORERS_PATH.iterdir()):
        if path.name.startswith((".", "_")) or not path.is_dir():
            continue
        entry = path / "__init__.py"
        if not entry.exists():
            continue
        name = f"{scoring_pkg}.scorers.{path.name}"
        spec = importlib.util.spec_from_file_location(name, entry, submodule_search_locations=[str(entry.parent)])
        if spec is None or spec.loader is None:
            logger.warning(f"[NTE评分] 评分包无法解析，已跳过: {entry}")
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as error:
            sys.modules.pop(name, None)
            logger.warning(f"[NTE评分] 评分包加载失败，已跳过: {path.name}: {error!r}")
            continue
        logger.info(f"[NTE评分] 评分包已加载: {path.name}")


def all_scorers() -> dict[str, Scorer]:
    """已注册的全部评分器（首次调用会触发评分包扫描）；查看 / 管理命令用。"""
    _load_scorers()
    return dict(_scorers)


def _configured_scorer() -> Scorer:
    _load_scorers()
    scorer_id: str = NTEConfig.get_config("NTEScoringProvider").data
    if scorer_id not in _scorers:
        raise ValueError(f"评分 provider 未注册: {scorer_id}，可用: {sorted(_scorers)}")
    return _scorers[scorer_id]


async def get_scorer() -> Scorer:
    """按配置取当前评分器；首次选中或配置切换时走 prepare → 激活 → close 旧的。"""
    global _active
    target = _configured_scorer()
    if _active is target:
        return target
    async with _switch_lock:
        if _active is target:
            return target
        await target.prepare()
        # 重新激活时定向刷新词表并清徽章位图缓存：数据/素材经 git pull 更新后切回即生效。
        # 其它 scorer 的词表保留，正在渲染的旧卡片仍能取到自己的评级。
        _specs_cache.pop(target.scorer_id, None)
        _badge_of.cache_clear()
        previous, _active = _active, target
        if previous is not None:
            await previous.close()
        meta = target.meta
        detail = " ".join(part for part in (meta.name, meta.version and f"v{meta.version}", meta.author) if part)
        logger.info(f"[NTE评分] 评分算法激活: {target.scorer_id}" + (f" ({detail})" if detail else ""))
    return target


def describe_char(char_id: str) -> str:
    """当前配置评分器对某角色的评分逻辑说明（AI 知识库用）；无该角色方案返回空串。
    配置的 scorer_id 未注册（拼错 / 包加载失败）只 warning 返回空串，不阻断插件加载。"""
    try:
        scorer = _configured_scorer()
    except ValueError as error:
        logger.warning(f"[NTE评分] 评分说明跳过: {error}")
        return ""
    return scorer.describe_char(char_id)


def _specs_of(scorer: Scorer) -> dict[str, GradeSpec]:
    specs = _specs_cache.get(scorer.scorer_id)
    if specs is None:
        specs = {spec.id: spec for spec in scorer.grades()}
        _specs_cache[scorer.scorer_id] = specs
    return specs


def grade_color(scorer: Scorer, grade: str) -> tuple[int, int, int]:
    """该评分器下某评级的主色；未知评级（空串 / 其它算法的遗留行）回退纯白，与未评分展示一致。"""
    spec = _specs_of(scorer).get(grade)
    return spec.color if spec is not None else COLOR_WHITE


def grade_badge(scorer: Scorer, grade: str | None, size: int) -> Image.Image | None:
    """该评分器下某评级的徽章图：有素材用素材，无素材画自动徽章；未知评级返回 None（不画）。"""
    if grade is None or grade not in _specs_of(scorer):
        return None
    return _badge_of(scorer.scorer_id, grade, size)


@lru_cache(maxsize=64)
def _badge_of(scorer_id: str, grade: str, size: int) -> Image.Image:
    spec = _specs_cache[scorer_id][grade]
    if spec.icon is not None:
        return open_texture(spec.icon, (size, size))
    return _auto_badge(spec, size)


def _auto_badge(spec: GradeSpec, size: int) -> Image.Image:
    """无素材评级的兜底徽章：主色圆底 + 评级文字；按实际文本宽度缩字防溢出。"""
    canvas = Image.new("RGBA", (size, size))
    SmoothDrawer().rounded_rectangle((size, size), radius=size // 2, fill=(*spec.color, 235), target=canvas)
    draw = ImageDraw.Draw(canvas)
    font_size = round(size * 0.52)
    width = draw.textlength(spec.id, font=nte_font_origin(font_size))
    if width > size * 0.78:
        font_size = max(1, round(font_size * size * 0.78 / width))
    draw.text((size // 2, size // 2), spec.id, font=nte_font_origin(font_size), fill=COLOR_WHITE, anchor="mm")
    return canvas
