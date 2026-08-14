from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic import Field, BaseModel, RootModel, ConfigDict

from .RESOURCE_PATH import SCRATCH_PATH


class _ScratchModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _ScratchGift(_ScratchModel):
    name: str = Field(min_length=1)
    count: int = Field(gt=0)


class _ScratchDefinition(_ScratchModel):
    name: str = Field(min_length=1)
    cost: int = Field(gt=0)
    gift: _ScratchGift


class _ScratchDefinitions(RootModel[dict[str, _ScratchDefinition]]): ...


@dataclass(frozen=True, slots=True, kw_only=True)
class ScratchItem:
    name: str
    cost: int
    gift_name: str
    gift_count: int


_items_by_ref: dict[str, ScratchItem] = {}


def _normalize_ref(ref: str) -> str:
    if ref.startswith("《") and ref.endswith("》"):
        return ref[1:-1]
    return ref


def _bare_title(name: str) -> str:
    has_left = name.startswith("《")
    has_right = name.endswith("》")
    if has_left != has_right:
        raise ValueError(f"刮刮乐资源名称书名号不完整: {name}")
    bare = _normalize_ref(name)
    if bare == "":
        raise ValueError("刮刮乐资源名称为空")
    return bare


async def load_scratch_items(*, force: bool = False) -> None:
    global _items_by_ref
    if not SCRATCH_PATH.is_file():
        _items_by_ref = {}
        return
    if _items_by_ref and not force:
        return

    scratch_text = await asyncio.to_thread(SCRATCH_PATH.read_text, encoding="utf-8")
    definitions = _ScratchDefinitions.model_validate_json(scratch_text).root
    refs: dict[str, ScratchItem] = {}
    for item_id, definition in definitions.items():
        bare_name = _bare_title(definition.name)
        item = ScratchItem(
            name=definition.name,
            cost=definition.cost,
            gift_name=definition.gift.name,
            gift_count=definition.gift.count,
        )
        for ref in (item_id, bare_name):
            if ref in refs:
                raise ValueError(f"刮刮乐资源引用重复: {ref}")
            refs[ref] = item
    _items_by_ref = refs


def get_scratch_item(ref: str) -> ScratchItem | None:
    return _items_by_ref.get(_normalize_ref(ref))
