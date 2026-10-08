from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, TypeAdapter

from gsuid_core.pool import to_thread

from .RESOURCE_PATH import SCORING_PATH


class _PropertyDefinition(BaseModel):
    name: str
    is_percent: bool


class _ItemStats(BaseModel):
    main: dict[str, dict[str, list[float]]]


class _Stats(BaseModel):
    core: _ItemStats
    pie: dict[str, _ItemStats]


@dataclass(frozen=True)
class _PropertyTables:
    attributes: dict[str, _PropertyDefinition]
    main_stats: dict[str, _ItemStats]


_ATTRIBUTES = TypeAdapter(dict[str, _PropertyDefinition])
_tables = _PropertyTables(attributes={}, main_stats={})


@to_thread
def load_suit_properties() -> None:
    global _tables
    attributes_path = SCORING_PATH / "attributes.json"
    stats_path = SCORING_PATH / "stats.json"
    if not attributes_path.is_file() or not stats_path.is_file():
        return
    definitions = _ATTRIBUTES.validate_json(attributes_path.read_bytes())
    stats = _Stats.model_validate_json(stats_path.read_bytes())
    main_stats = {"core": stats.core, **{f"cell{grid}": item for grid, item in stats.pie.items()}}
    _tables = _PropertyTables(attributes=definitions, main_stats=main_stats)


def get_suit_main_property(item_id: str, attr_id: str, level: int) -> tuple[str, str] | None:
    tables = _tables
    key = attr_id.lower()
    parts = item_id.split("_")
    kind = parts[0] if parts[0].startswith("cell") else "core"
    quality = parts[-1]
    if key not in tables.attributes or kind not in tables.main_stats:
        return None
    stats = tables.main_stats[kind]
    if key not in stats.main or quality not in stats.main[key]:
        return None
    values = stats.main[key][quality]
    if not 0 <= level < len(values):
        return None
    definition = tables.attributes[key]
    suffix = "%" if definition.is_percent else ""
    return definition.name, f"{values[level]:g}{suffix}"
