from __future__ import annotations

from typing import Annotated

from pydantic import Field, BaseModel, ConfigDict, BeforeValidator

# 资源中的 null 在模型边界归一为空容器。
_none_to_list = BeforeValidator(lambda value: [] if value is None else value)
_none_to_dict = BeforeValidator(lambda value: {} if value is None else value)


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class RawAbilityStat(_Base):
    name: str = ""
    value_name: str = ""
    values: Annotated[list[list[float]], _none_to_list] = Field(default_factory=list)


class RawPhase(_Base):
    description: str = ""


class RawAbility(_Base):
    id: str = ""
    type: str = ""
    type_name: str = ""
    name: str = ""
    stats: Annotated[list[RawAbilityStat], _none_to_list] = Field(default_factory=list)
    phases: Annotated[list[RawPhase], _none_to_list] = Field(default_factory=list)


class RawCharacterStat(_Base):
    id_stats: str = ""
    values: Annotated[list[float], _none_to_list] = Field(default_factory=list)


class RawEffect(_Base):
    name: str = ""
    desc: str = ""
    awaken_num: int = 0


class RawCharData(_Base):
    id: str = ""
    name: str = ""
    introduction: str = ""
    element_name: str = ""
    arcs_name: str = ""
    rarity: int = 0
    hp: int = 0
    atk: int = 0
    def_: int = Field(0, alias="def")
    stats: Annotated[list[RawCharacterStat], _none_to_list] = Field(default_factory=list)
    abilities: Annotated[list[RawAbility], _none_to_list] = Field(default_factory=list)
    awaken: Annotated[list[RawEffect], _none_to_list] = Field(default_factory=list)
    resonance: Annotated[list[RawEffect], _none_to_list] = Field(default_factory=list)


class RawForkEffect(_Base):
    description: str = ""


class RawForkData(_Base):
    effect: Annotated[RawForkEffect, _none_to_dict] = Field(default_factory=RawForkEffect)
