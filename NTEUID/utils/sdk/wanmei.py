from __future__ import annotations

import re
import json
import time
import secrets
from html import unescape
from base64 import b64encode
from typing import Any, TypeVar

import httpx
from Crypto.Hash import SHA1
from Crypto.Cipher import PKCS1_OAEP
from Crypto.PublicKey import RSA

from .base import BaseSdkClient
from ..constants import (
    WANMEI_USER_AGENT,
    WANMEI_ID_BASE_URL,
    WANMEI_KF_BASE_URL,
    WANMEI_SCRATCH_ITEM_ID,
    WANMEI_SCRATCH_TYPE_ID,
    WANMEI_CAPTCHA_BASE_URL,
    WANMEI_LOGIN_RETURN_URL,
    WANMEI_KF_GAME_ID_YIHUAN,
    WANMEI_SCRATCH_ITEM_TYPE,
    WANMEI_SCRATCH_PAGE_SIZE,
    WANMEI_QUERY_CAPTCHA_APP_ID,
    WANMEI_SCRATCH_ITEM_SUB_TYPE,
)
from .wanmei_model import (
    WanmeiRole,
    WanmeiError,
    WanmeiAreaCode,
    WanmeiResponse,
    WanmeiRoleList,
    WanmeiLoginPage,
    WanmeiAuthExpired,
    WanmeiCaptchaInfo,
    WanmeiScratchData,
    WanmeiResultResponse,
    WanmeiCaptchaResponse,
    WanmeiCaptchaResultResponse,
    WanmeiScratchResultResponse,
    parse_wanmei,
)

_PUBLIC_KEY_RE = re.compile(r'id="publicKey"[^>]*value="([^"]+)"')
_JSESSION_ID_RE = re.compile(r'id="jsessionId"[^>]*value="([^"]+)"')

_ResponseModel = TypeVar("_ResponseModel", bound=WanmeiResponse)
_CaptchaResponseModel = TypeVar("_CaptchaResponseModel", bound=WanmeiCaptchaResponse)


class _WanmeiClient(BaseSdkClient):
    USER_AGENT = WANMEI_USER_AGENT
    error_cls = WanmeiError


class WanmeiIdClient(_WanmeiClient):
    BASE_URL = WANMEI_ID_BASE_URL

    def __init__(self) -> None:
        self.cookie_jar: httpx.Cookies = httpx.Cookies()

    def _default_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.USER_AGENT,
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest",
        }

    async def login_page(self) -> WanmeiLoginPage:
        response = await self._request_raw(
            "/login",
            query={"location": WANMEI_LOGIN_RETURN_URL},
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
        public_key_match = _PUBLIC_KEY_RE.search(response.text)
        if public_key_match is None:
            raise WanmeiError("完美世界登录页缺少 RSA 公钥")
        jsession_id_match = _JSESSION_ID_RE.search(response.text)
        if jsession_id_match is None:
            raise WanmeiError("完美世界登录页缺少登录会话")
        return WanmeiLoginPage(
            public_key=unescape(public_key_match.group(1)),
            jsession_id=jsession_id_match.group(1),
        )

    async def area_codes(self) -> list[WanmeiAreaCode]:
        payload = await self._json_form(
            "/areaCode/list",
            {},
            WanmeiResultResponse[list[WanmeiAreaCode]],
        )
        return payload.result

    async def refresh_cap_ticket(self) -> str:
        payload = await self._json_form(
            "/user/security/getCapTicket",
            {"t": str(int(time.time() * 1000))},
            WanmeiResultResponse[str],
        )
        return payload.result

    async def send_sms(
        self,
        *,
        area_code_id: int,
        phone: str,
        cap_ticket: str,
        sec_code: str,
    ) -> None:
        await self._json_form(
            "/checkPhoneWithNationAreaId",
            {"nationAreaId": str(area_code_id), "phoneNumber": phone},
            WanmeiResponse,
        )
        await self._json_form(
            "/sendPhoneCaptchaForSlidCaptcha",
            {
                "nationAreaId": str(area_code_id),
                "phone": phone,
                "capTicket": cap_ticket,
                "secCode": sec_code,
            },
            WanmeiResponse,
        )

    async def login_by_sms(
        self,
        *,
        login_page: WanmeiLoginPage,
        area_code_id: int,
        phone: str,
        sms_code: str,
        cap_ticket: str,
        sec_code: str,
    ) -> str:
        await self._json_form(
            "/setDeviceInfo",
            {
                "jsessionId": login_page.jsession_id,
                "deviceId": f"NTEUID-{secrets.token_hex(8)}",
                "deviceModel": "NTEUID Web Login",
                "deviceSys": "Web",
            },
            WanmeiResponse,
        )
        await self._json_form(
            "/checkPhoneCaptcha",
            {"phone": phone, "phoneCaptcha": sms_code},
            WanmeiResponse,
        )
        await self._json_form(
            "/shortMessageLogon",
            {
                "phoneNumber": _rsa_oaep_encrypt(login_page.public_key, phone),
                "newCaptcha": _rsa_oaep_encrypt(login_page.public_key, sms_code),
                "nationAreaId": str(area_code_id),
                "capTicket": cap_ticket,
                "secCode": sec_code,
                "location": WANMEI_LOGIN_RETURN_URL,
                "state": login_page.jsession_id,
            },
            WanmeiResponse,
        )
        logon = self.cookie_jar.get("logon")
        if logon is None:
            raise WanmeiError("完美世界短信登录响应缺少 logon Cookie")
        return logon

    async def _json_form(
        self,
        path: str,
        body: dict[str, Any],
        model: type[_ResponseModel],
    ) -> _ResponseModel:
        response = await self._request_raw(
            path,
            method="POST",
            body=body,
        )
        data = response.json()
        payload = parse_wanmei(model, data, f"[{path}] 响应格式错误")
        if payload.code != 0:
            raise WanmeiError(payload.message, data)
        return payload


class WanmeiKfClient(_WanmeiClient):
    BASE_URL = WANMEI_KF_BASE_URL

    def __init__(
        self,
        logon: str,
        timeout: float = BaseSdkClient.timeout,
    ) -> None:
        self.timeout = timeout
        self.cookie_jar = httpx.Cookies()
        self.cookie_jar.set("logon", logon, domain="kf.wanmei.com", path="/")

    def _default_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.USER_AGENT,
            "Accept": "*/*",
            "Referer": WANMEI_LOGIN_RETURN_URL,
            "X-Requested-With": "XMLHttpRequest",
        }

    async def query_cap_ticket(self) -> str:
        response = await self._request_raw(
            "/laohuService/getMCaptcha",
        )
        return response.text

    async def roles(self) -> list[WanmeiRole]:
        response = await self._request_raw(
            "/laohuSelfService/searchActiveGameRoles",
            query={"gameId": WANMEI_KF_GAME_ID_YIHUAN},
        )
        return parse_wanmei(WanmeiRoleList, response.json(), "客服角色列表格式错误").root

    async def search_scratch(
        self,
        *,
        role_id: str,
        start_time: str,
        end_time: str,
        cap_ticket: str,
        sec_code: str,
        page_no: int = 1,
    ) -> WanmeiScratchData:
        body = {
            "capTicket": cap_ticket,
            "secCode": sec_code,
            "typeId": WANMEI_SCRATCH_TYPE_ID,
            "gameId": WANMEI_KF_GAME_ID_YIHUAN,
            "server": "",
            "roleId": role_id,
            "itemType": WANMEI_SCRATCH_ITEM_TYPE,
            "itemSubType": WANMEI_SCRATCH_ITEM_SUB_TYPE,
            "item5": WANMEI_SCRATCH_ITEM_ID,
            "item12": "",
            "startTime": start_time,
            "endTime": end_time,
            "pageSize": str(WANMEI_SCRATCH_PAGE_SIZE),
            "pageNo": str(page_no),
            "item": "",
        }
        response = await self._request_raw(
            "/selfItemFlowQuery/search",
            method="POST",
            body=body,
        )
        if not response.text.startswith("{"):
            raise WanmeiAuthExpired(response.text)
        data = response.json()
        payload = parse_wanmei(
            WanmeiResponse,
            data,
            "刮刮乐查询响应格式错误",
        )
        if payload.code == 0:
            return parse_wanmei(
                WanmeiScratchResultResponse,
                data,
                "刮刮乐查询响应格式错误",
            ).data
        if "没有搜索到" in payload.message:
            return WanmeiScratchData(total=0, result=[], info="")
        raise WanmeiError(payload.message, data)


class WanmeiCaptchaClient(_WanmeiClient):
    BASE_URL = WANMEI_CAPTCHA_BASE_URL

    def _default_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.USER_AGENT,
            "Accept": "*/*",
            "Referer": WANMEI_LOGIN_RETURN_URL,
        }

    async def challenge(self, cap_ticket: str) -> tuple[str, list[int], bytes]:
        key_payload = await self._jsonp(
            "/mCaptcha/key",
            {
                "appId": WANMEI_QUERY_CAPTCHA_APP_ID,
                "capTicket": cap_ticket,
            },
            WanmeiCaptchaResultResponse[str],
        )
        cap_key = key_payload.result
        info_payload = await self._jsonp(
            f"/mCaptcha/info/{cap_key}",
            {},
            WanmeiCaptchaResultResponse[WanmeiCaptchaInfo],
        )
        challenge = info_payload.result
        image_response = await self._request_raw(challenge.img)
        return cap_key, challenge.loc, image_response.content

    async def validate(
        self,
        *,
        cap_key: str,
        valid_data: str,
        operation: str,
    ) -> str | None:
        callback = "nteuid"
        response = await self._request_raw(
            "/mCaptcha/validate",
            query={
                "callback": callback,
                "capKey": cap_key,
                "validData": valid_data,
                "op": operation,
                "fp": "1330308006",
                "label": "1",
                "_": str(int(time.time() * 1000)),
            },
        )
        data = json.loads(response.text.removeprefix(f"{callback}(").removesuffix(")"))
        status = parse_wanmei(WanmeiCaptchaResponse, data, "[/mCaptcha/validate] 响应格式错误")
        if status.code in (-1, 105):
            return None
        if status.code != 0:
            error = parse_wanmei(WanmeiResponse, data, "[/mCaptcha/validate] 响应格式错误")
            raise WanmeiError(error.message, data)
        return parse_wanmei(
            WanmeiCaptchaResultResponse[str],
            data,
            "[/mCaptcha/validate] 响应格式错误",
        ).result

    async def _jsonp(
        self,
        path: str,
        query: dict[str, str],
        model: type[_CaptchaResponseModel],
    ) -> _CaptchaResponseModel:
        callback = "nteuid"
        response = await self._request_raw(
            path,
            query={"callback": callback, **query, "_": str(int(time.time() * 1000))},
        )
        data = json.loads(response.text.removeprefix(f"{callback}(").removesuffix(")"))
        status = parse_wanmei(WanmeiCaptchaResponse, data, f"[{path}] 响应格式错误")
        if status.code != 0:
            error = parse_wanmei(WanmeiResponse, data, f"[{path}] 响应格式错误")
            raise WanmeiError(error.message, data)
        return parse_wanmei(model, data, f"[{path}] 响应格式错误")


def _rsa_oaep_encrypt(public_key: str, value: str) -> str:
    key = RSA.import_key(public_key)
    cipher = PKCS1_OAEP.new(key, hashAlgo=SHA1)
    return b64encode(cipher.encrypt(value.encode())).decode()
