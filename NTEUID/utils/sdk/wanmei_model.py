from __future__ import annotations

import re
from typing import Any, Generic, TypeVar
from datetime import datetime
from dataclasses import dataclass

from pydantic import Field, BaseModel, RootModel, ConfigDict, ValidationError

from .base import SdkError

_SCRATCH_INFO_RE = re.compile(r"共计消耗(?P<cost>\d+)方斯购买好感度道具，获得奖券奖励(?P<reward>\d+)方斯")


class WanmeiError(SdkError):
    pass


class WanmeiAuthExpired(WanmeiError):
    pass


class _WanmeiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


_Result = TypeVar("_Result")


class WanmeiResponse(_WanmeiModel):
    code: int
    message: str


class WanmeiResultResponse(WanmeiResponse, Generic[_Result]):
    result: _Result


class WanmeiAreaCode(_WanmeiModel):
    area_code_id: int = Field(alias="areaCodeId", description="完美世界国际区号条目 ID")
    area_code: int = Field(alias="areaCode", description="国际电话区号")
    area_name: str = Field(alias="areaName", description="国家或地区名称")


@dataclass(frozen=True, slots=True, kw_only=True)
class WanmeiLoginPage:
    public_key: str
    jsession_id: str


class WanmeiCaptchaInfo(_WanmeiModel):
    loc: list[int] = Field(description="客服滑块底图切片顺序")
    img: str = Field(description="客服滑块题图地址")


class WanmeiCaptchaResponse(_WanmeiModel):
    code: int


class WanmeiCaptchaResultResponse(WanmeiCaptchaResponse, Generic[_Result]):
    result: _Result


class WanmeiRole(_WanmeiModel):
    role_id: str = Field(alias="roleId", description="异环角色 ID")
    role_name: str = Field(alias="roleName", description="异环角色名")


class WanmeiRoleList(RootModel[list[WanmeiRole]]):
    pass


def award_parts(award: str) -> list[tuple[str, int]]:
    parts: list[tuple[str, int]] = []
    for part in award.replace("，", ",").split(","):
        name, separator, count = part.rpartition("*")
        if separator:
            parts.append((name, int(count)))
    return parts


class WanmeiScratchRecord(_WanmeiModel):
    log_time: datetime = Field(alias="logTime", description="流水时间")
    scratch_card_id: str = Field(alias="scratchCardId", description="读物名或客服未收录的原始道具 ID")
    award: str = Field(description="本次奖励，多段以逗号分隔（如 方斯*20000,未知*3），空串表示未中奖")

    @property
    def gain(self) -> int:
        return sum(count for name, count in award_parts(self.award) if name == "方斯")

    @property
    def extra(self) -> int:
        return sum(count for name, count in award_parts(self.award) if name != "方斯")


class WanmeiScratchData(_WanmeiModel):
    total: int = Field(description="匹配记录总数")
    result: list[WanmeiScratchRecord] = Field(description="本页流水")
    info: str = Field(description="官方投入与奖励汇总")

    @property
    def summary(self) -> tuple[int, int]:
        match = _SCRATCH_INFO_RE.fullmatch(self.info)
        if match is None:
            raise WanmeiError("刮刮乐官方汇总格式错误", {"info": self.info})
        return int(match["cost"]), int(match["reward"])


class WanmeiScratchResultResponse(WanmeiResponse):
    data: WanmeiScratchData


_Model = TypeVar("_Model", bound=BaseModel)


def parse_wanmei(model: type[_Model], data: Any, message: str) -> _Model:
    try:
        return model.model_validate(data)
    except ValidationError as error:
        raise WanmeiError(f"{message}: {error}", {"response": data}) from error
