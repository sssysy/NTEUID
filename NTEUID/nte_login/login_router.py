from __future__ import annotations

from pydantic import Field, BaseModel
from starlette.responses import HTMLResponse, JSONResponse

from gsuid_core.logger import logger
from gsuid_core.web_app import app

from ..utils.msgs import LoginMsg
from .login_service import (
    LOGIN_CACHE,
    LoginState,
    select_wanmei_role,
    finish_wanmei_login,
    prepare_wanmei_login,
)
from ..utils.constants import LAOHU_APP_ID, LAOHU_APP_KEY
from ..utils.sdk.laohu import LaohuError, LaohuClient
from ..utils.sdk.wanmei_model import WanmeiError
from ..utils.resource.RESOURCE_PATH import NTE_TEMPLATES

_WANMEI_LINK_EXPIRED = "完美登录链接已失效"


def _error(message: str, *, key: str = "message") -> JSONResponse:
    return JSONResponse({"ok": False, key: message}, status_code=400)


def _login_state(auth_token: str) -> LoginState:
    state: LoginState | None = LOGIN_CACHE.get(auth_token)
    if state is None:
        raise WanmeiError(_WANMEI_LINK_EXPIRED)
    return state


class _SendSmsPayload(BaseModel):
    auth: str
    mobile: str


class _LoginPayload(BaseModel):
    auth: str
    mobile: str
    code: str


class _WanmeiAuthPayload(BaseModel):
    auth: str


class _WanmeiSmsPayload(_WanmeiAuthPayload):
    area_code_id: int = Field(alias="areaCodeId")
    phone: str
    cap_ticket: str = Field(alias="capTicket")
    sec_code: str = Field(alias="secCode")


class _WanmeiLoginPayload(_WanmeiSmsPayload):
    sms_code: str = Field(alias="smsCode")


class _WanmeiRolePayload(_WanmeiAuthPayload):
    role_id: str = Field(alias="roleId")


@app.get("/nte/i/{auth_token}")
async def nte_login_page(auth_token: str) -> HTMLResponse:
    state: LoginState | None = LOGIN_CACHE.get(auth_token)
    if state is None:
        return HTMLResponse(LoginMsg.link_expired(), status_code=404)
    return HTMLResponse(
        NTE_TEMPLATES.get_template("login.html.j2").render(
            auth=auth_token,
            user_id=state.user_id,
            done=state.done,
        )
    )


@app.post("/nte/sendSmsCode")
async def nte_send_sms(payload: _SendSmsPayload) -> JSONResponse:
    state: LoginState | None = LOGIN_CACHE.get(payload.auth)
    if state is None:
        return _error(LoginMsg.session_expired(), key="msg")
    try:
        await LaohuClient(LAOHU_APP_ID, LAOHU_APP_KEY, device=state.device).send_sms_code(payload.mobile)
    except LaohuError as error:
        logger.warning(f"[NTE登录] 短信下发失败 mobile={payload.mobile}: {error.message}")
        return _error(LoginMsg.SMS_SEND_FAILED, key="msg")
    return JSONResponse({"ok": True, "msg": LoginMsg.SMS_SENT})


@app.post("/nte/login")
async def nte_perform_login(payload: _LoginPayload) -> JSONResponse:
    state: LoginState | None = LOGIN_CACHE.get(payload.auth)
    if state is None:
        return _error(LoginMsg.session_expired(), key="msg")
    try:
        account = await LaohuClient(
            LAOHU_APP_ID,
            LAOHU_APP_KEY,
            device=state.device,
        ).login_by_sms(payload.mobile, payload.code)
    except LaohuError as error:
        logger.warning(f"[NTE登录] 老虎短信登录失败 mobile={payload.mobile}: {error.message}")
        return _error(LoginMsg.SMS_LOGIN_FAILED, key="msg")
    state.laohu_token = account.token
    state.laohu_user_id = str(account.user_id)
    state.done = True
    return JSONResponse({"ok": True, "msg": LoginMsg.SMS_VERIFIED})


@app.post("/nte/wanmei/prepare")
async def wanmei_prepare(payload: _WanmeiAuthPayload) -> JSONResponse:
    state: LoginState | None = LOGIN_CACHE.get(payload.auth)
    if state is None:
        return _error(_WANMEI_LINK_EXPIRED)
    try:
        wanmei = await prepare_wanmei_login(state)
        cap_ticket = await wanmei.client.refresh_cap_ticket()
    except WanmeiError as error:
        logger.warning(f"[NTE完美登录] 登录页初始化失败: {error.message}")
        return _error(error.message)
    return JSONResponse(
        {
            "ok": True,
            "areaCodes": [item.model_dump(by_alias=True) for item in wanmei.area_codes],
            "capTicket": cap_ticket,
        }
    )


@app.post("/nte/wanmei/sendSmsCode")
async def wanmei_send_sms(payload: _WanmeiSmsPayload) -> JSONResponse:
    try:
        wanmei = _login_state(payload.auth).wanmei
        if wanmei is None:
            raise WanmeiError(_WANMEI_LINK_EXPIRED)
        await wanmei.client.send_sms(
            area_code_id=payload.area_code_id,
            phone=payload.phone,
            cap_ticket=payload.cap_ticket,
            sec_code=payload.sec_code,
        )
    except WanmeiError as error:
        logger.warning(f"[NTE完美登录] 短信发送失败: {error.message}")
        return _error(error.message)
    return JSONResponse({"ok": True})


@app.post("/nte/wanmei/login")
async def wanmei_login(payload: _WanmeiLoginPayload) -> JSONResponse:
    try:
        roles = await finish_wanmei_login(
            login_state=_login_state(payload.auth),
            area_code_id=payload.area_code_id,
            phone=payload.phone,
            sms_code=payload.sms_code,
            cap_ticket=payload.cap_ticket,
            sec_code=payload.sec_code,
        )
    except WanmeiError as error:
        logger.warning(f"[NTE完美登录] 登录失败: {error.message}")
        return _error(error.message)
    return JSONResponse(
        {
            "ok": True,
            "roles": [role.model_dump(by_alias=True) for role in roles],
        }
    )


@app.post("/nte/wanmei/selectRole")
async def wanmei_select_role(payload: _WanmeiRolePayload) -> JSONResponse:
    try:
        await select_wanmei_role(_login_state(payload.auth), payload.role_id)
    except WanmeiError as error:
        return _error(error.message)
    return JSONResponse({"ok": True})
