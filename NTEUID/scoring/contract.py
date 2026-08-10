from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Sequence

from gsuid_core.logger import logger

from ..utils.sdk.tajiduo_model import CharacterDetail, CharacterProperty


@dataclass(frozen=True, slots=True, kw_only=True)
class ScorerMeta:
    """评分包元信息；`author` 必填（注册时校验），其余可选。展示 / 日志用，不参与评分逻辑。"""

    author: str
    name: str = ""
    version: str = ""
    updated_at: str = ""
    description: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class GradeSpec:
    """一个评级的展示声明：`icon` 为 None 时渲染端用 `color` 画自动徽章，无素材也能出图。"""

    id: str
    color: tuple[int, int, int]
    icon: Path | None = None


class EquipmentView(Protocol):
    """单件装备的展示视图。分数文本（display）的格式由 provider 决定，卡片原样绘制。"""

    @property
    def item_id(self) -> str: ...

    @property
    def display(self) -> str: ...

    @property
    def grade(self) -> str | None: ...

    @property
    def unlocked_subs(self) -> int: ...


class ScoreResult(Protocol):
    """一次角色评分的完整输出：总分/评级入库排序，判定方法驱动卡片词条高亮。
    `display` 是角色卡上的总分文本（如 "87分" / "毕业度92.5%"）；榜单类卡片始终显示整数 `score`。"""

    @property
    def score(self) -> int: ...

    @property
    def display(self) -> str: ...

    @property
    def grade(self) -> str: ...

    @property
    def equipment(self) -> Sequence[EquipmentView]: ...

    def is_role_prop_effective(self, prop: CharacterProperty) -> bool: ...

    def is_main_prop_counted(self, prop: CharacterProperty) -> bool: ...

    def is_sub_prop_recommended(self, prop: CharacterProperty) -> bool: ...


class Scorer(Protocol):
    """评分算法接入点。生命周期由 registry 驱动：激活前 prepare、切走时 close。"""

    @property
    def scorer_id(self) -> str: ...

    @property
    def meta(self) -> ScorerMeta: ...

    def grades(self) -> Sequence[GradeSpec]: ...

    def describe_char(self, char_id: str) -> str: ...

    async def prepare(self) -> None: ...

    async def close(self) -> None: ...

    async def score_character(self, character: CharacterDetail) -> ScoreResult | None: ...

    async def score_batch(self, characters: Sequence[CharacterDetail]) -> list[ScoreResult | None]: ...


class BaseScorer(ABC):
    """自定义评分器基类：子类只必须给出 scorer_id、grades()、score_character()。

    - describe_char 在插件 import 期就可能被调用（AI 知识库注册），不得依赖 prepare。
    - score_character 对「无该角色方案 / 无装备」返回 None；数据方案损坏抛 ValueError。
    - score_batch 默认逐个评分，并把单角色 ValueError 降级为 None（整账号落库不被单角色连累）。
    """

    scorer_id: str
    meta: ScorerMeta

    @abstractmethod
    def grades(self) -> Sequence[GradeSpec]: ...

    @abstractmethod
    async def score_character(self, character: CharacterDetail) -> ScoreResult | None: ...

    def describe_char(self, char_id: str) -> str:
        return ""

    async def prepare(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def score_batch(self, characters: Sequence[CharacterDetail]) -> list[ScoreResult | None]:
        results: list[ScoreResult | None] = []
        for character in characters:
            try:
                results.append(await self.score_character(character))
            except ValueError as error:
                logger.debug(f"[NTE评分] 单角色评分失败 scorer={self.scorer_id} char={character.id}: {error!r}")
                results.append(None)
        return results
