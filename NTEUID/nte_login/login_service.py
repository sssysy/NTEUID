from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime
from dataclasses import dataclass

from gsuid_core.bot import Bot
from gsuid_core.config import core_config
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment
from gsuid_core.utils.cookie_manager.qrlogin import get_qrcode_base64

from .transport import TransportError, WanmeiCredential, build_transport
from ..utils.msgs import LoginMsg, send_nte_notify
from ..utils.cache import TimedCache
from ..utils.utils import get_public_ip
from ..utils.session import is_auth_error
from ..utils.database import NTEUser, NTEWanmeiUser
from ..utils.sdk.laohu import LaohuDevice, make_device_id
from ..utils.background import create_background_task
from ..utils.sdk.wanmei import WanmeiIdClient, WanmeiKfClient
from ..utils.sdk.tajiduo import TajiduoClient
from ..utils.msgs.buttons import login_link_buttons
from ..utils.game_registry import PRIMARY_GAME_ID, GAME_SIGN_SWITCHES
from ..nte_config.nte_config import NTEConfig
from ..utils.sdk.wanmei_model import (
    WanmeiRole,
    WanmeiError,
    WanmeiAreaCode,
    WanmeiLoginPage,
)
from ..utils.sdk.tajiduo_model import GameRoleList, TajiduoError, GameRecordCard, TajiduoSession
from ..nte_role.character_cache import save_character_cache
from ..utils.resource.RESOURCE_PATH import QR_PATH

_MAX_LOGIN_TTL_S = 3600  # cache 容量上限；实际等待时长由 NTELoginTTL 决定
LOGIN_CACHE: TimedCache = TimedCache(timeout=_MAX_LOGIN_TTL_S, maxsize=32)
EXTERNAL_PENDING: TimedCache = TimedCache(timeout=_MAX_LOGIN_TTL_S, maxsize=128)
LOGIN_POLL_INTERVAL = 2.0


@dataclass(frozen=True, slots=True)
class LoginExchange:
    tajiduo: TajiduoClient
    roles: list[tuple[str, str, str]]


@dataclass(frozen=True, slots=True)
class LoginExchangeError:
    user_msg: str


@dataclass(slots=True, kw_only=True)
class WanmeiLoginState:
    client: WanmeiIdClient
    login_page: WanmeiLoginPage
    area_codes: list[WanmeiAreaCode]
    roles: list[WanmeiRole] | None = None
    logon: str | None = None


@dataclass(slots=True, kw_only=True)
class LoginState:
    user_id: str
    bot_id: str
    group_id: str | None
    device: LaohuDevice
    wanmei: WanmeiLoginState | None = None
    done: bool = False
    laohu_token: str = ""
    laohu_user_id: str = ""


async def prepare_wanmei_login(login_state: LoginState) -> WanmeiLoginState:
    state = login_state.wanmei
    if state is not None:
        return state
    client = WanmeiIdClient()
    state = WanmeiLoginState(
        client=client,
        login_page=await client.login_page(),
        area_codes=await client.area_codes(),
    )
    login_state.wanmei = state
    return state


async def finish_wanmei_login(
    *,
    login_state: LoginState,
    area_code_id: int,
    phone: str,
    sms_code: str,
    cap_ticket: str,
    sec_code: str,
) -> list[WanmeiRole]:
    state = login_state.wanmei
    if state is None:
        raise WanmeiError("完美登录链接已失效")
    logon = await state.client.login_by_sms(
        login_page=state.login_page,
        area_code_id=area_code_id,
        phone=phone,
        sms_code=sms_code,
        cap_ticket=cap_ticket,
        sec_code=sec_code,
    )
    roles = await WanmeiKfClient(logon).roles()
    logger.debug(
        f"[NTE完美登录] user_id={login_state.user_id} 角色列表={[role.model_dump(by_alias=True) for role in roles]}"
    )
    if not roles:
        raise WanmeiError("完美世界客服未返回异环角色")
    if len(roles) == 1:
        await _save_wanmei_login(login_state, roles[0], logon)
        return roles
    state.roles = roles
    state.logon = logon
    return roles


async def select_wanmei_role(login_state: LoginState, role_id: str) -> None:
    state = login_state.wanmei
    if state is None or state.roles is None or state.logon is None:
        raise WanmeiError("完美登录链接已失效")
    role = next((item for item in state.roles if item.role_id == role_id), None)
    if role is None:
        raise WanmeiError("所选角色不在本次完美世界登录结果中")
    await _save_wanmei_login(login_state, role, state.logon)


async def _save_wanmei_login(
    login_state: LoginState,
    role: WanmeiRole,
    logon: str,
) -> None:
    await NTEWanmeiUser.save_login(
        user_id=login_state.user_id,
        bot_id=login_state.bot_id,
        uid=role.role_id,
        role_name=role.role_name,
        logon=logon,
    )
    logger.info(
        f"[NTE完美登录] user_id={login_state.user_id} uid={role.role_id} role_name={role.role_name} 登录并落库完成"
    )
    login_state.wanmei = None
    login_state.done = True


async def _wait(auth_token: str) -> LoginState | None:
    waited = 0.0
    wait_s = NTEConfig.get_config("NTELoginTTL").data
    while waited < wait_s:
        state: LoginState | None = LOGIN_CACHE.get(auth_token)
        if state is None:
            return None
        if state.done:
            LOGIN_CACHE.pop(auth_token)
            return state
        await asyncio.sleep(LOGIN_POLL_INTERVAL)
        waited += LOGIN_POLL_INTERVAL
    LOGIN_CACHE.pop(auth_token)
    return None


async def _exchange_and_persist(
    *,
    user_id: str,
    bot_id: str,
    dev_code: str,
    laohu_token: str,
    laohu_user_id: str,
) -> LoginExchange | LoginExchangeError:
    tajiduo = TajiduoClient(device_id=dev_code)
    try:
        tj_session = await tajiduo.user_center_login(laohu_token, laohu_user_id)
        roles = await _collect_all_roles(tajiduo)
    except TajiduoError as error:
        logger.warning(f"[NTE登录] 塔吉多 session 建立失败 user_id={user_id}: {error.message}")
        return LoginExchangeError(LoginMsg.USER_CENTER_LOGIN_FAILED)
    if not roles:
        return LoginExchangeError(LoginMsg.NO_SUPPORTED_GAME)
    await _persist_login_session(
        user_id=user_id,
        bot_id=bot_id,
        dev_code=dev_code,
        laohu_token=laohu_token,
        laohu_user_id=laohu_user_id,
        tj_session=tj_session,
        roles=roles,
    )
    logger.info(
        f"[NTE登录] user_id={user_id} center_uid={tj_session.center_uid} roles={[rid for rid, _, _ in roles]} 登录完成"
    )
    return LoginExchange(tajiduo=tajiduo, roles=roles)


async def _persist_login_session(
    *,
    user_id: str,
    bot_id: str,
    dev_code: str,
    laohu_token: str,
    laohu_user_id: str,
    tj_session: TajiduoSession,
    roles: list[tuple[str, str, str]],
) -> None:
    await NTEUser.sync_account_roles(
        user_id=user_id,
        bot_id=bot_id,
        center_uid=tj_session.center_uid,
        entries=roles,
        status="",
        dev_code=dev_code,
        cookie=tj_session.refresh_token,
        access_token=tj_session.access_token,
        access_token_updated_at=datetime.now(),
        laohu_token=laohu_token,
        laohu_user_id=laohu_user_id,
    )


async def login_by_access_token(bot: Bot, ev: Event, access_token: str) -> None:
    access_token = access_token.strip()
    if not access_token:
        await send_nte_notify(bot, ev, LoginMsg.USER_CENTER_LOGIN_FAILED)
        return

    dev_code = make_device_id()
    tajiduo = TajiduoClient(device_id=dev_code, access_token=access_token)

    try:
        info = await tajiduo.get_user_full_info()
    except TajiduoError as error:
        logger.warning(f"[NTE登录] accessToken 验证失败 user_id={ev.user_id}: {error.message}")
        await send_nte_notify(bot, ev, LoginMsg.USER_CENTER_LOGIN_FAILED)
        return

    center_uid = info.center_uid
    if not center_uid:
        logger.warning(f"[NTE登录] accessToken 用户资料缺少 center_uid user_id={ev.user_id}")
        await send_nte_notify(bot, ev, LoginMsg.USER_CENTER_LOGIN_FAILED)
        return
    tajiduo.center_uid = center_uid

    try:
        roles = await _collect_access_token_roles(tajiduo)
    except TajiduoError as error:
        logger.warning(f"[NTE登录] accessToken 登录失败 user_id={ev.user_id}: {error.message}")
        await send_nte_notify(bot, ev, LoginMsg.USER_CENTER_LOGIN_FAILED)
        return

    await NTEUser.upsert_access_token_account(
        user_id=ev.user_id,
        bot_id=ev.bot_id,
        center_uid=center_uid,
        access_token=access_token,
        dev_code=dev_code,
        entries=roles,
    )
    logger.info(f"[NTE登录] user_id={ev.user_id} center_uid={center_uid} accessToken 登录完成 roles={roles}")
    await send_nte_notify(bot, ev, LoginMsg.TAJIDUO_SUCCESS if roles else LoginMsg.ACCESS_TOKEN_SHELL_SUCCESS)
    if roles:
        await _post_login_actions(bot, ev, LoginExchange(tajiduo=tajiduo, roles=roles))


async def _collect_all_roles(tajiduo: TajiduoClient) -> list[tuple[str, str, str]]:
    collected: list[tuple[str, str, str]] = []
    for game_id in GAME_SIGN_SWITCHES:
        collected.extend(await _collect_roles(tajiduo, game_id))
    return collected


async def _collect_access_token_roles(tajiduo: TajiduoClient) -> list[tuple[str, str, str]]:
    try:
        roles = await _collect_all_roles(tajiduo)
    except TajiduoError as error:
        if is_auth_error(error):
            raise
        logger.warning(f"[NTE登录] accessToken 自动同步角色失败: {error.message}")
    else:
        if roles:
            return roles

    try:
        return _collect_roles_from_record_cards(await tajiduo.get_game_record_card())
    except TajiduoError as error:
        if is_auth_error(error):
            raise
        logger.warning(f"[NTE登录] accessToken 自动同步战绩卡失败: {error.message}")
        return []


def _collect_roles_from_record_cards(cards: list[GameRecordCard]) -> list[tuple[str, str, str]]:
    collected: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for card in cards:
        game_id = str(card.game_id)
        if game_id not in GAME_SIGN_SWITCHES:
            continue
        role = card.bind_role_info
        if role is None or not role.role_id:
            continue
        uid = str(role.role_id)
        key = (game_id, uid)
        if key in seen:
            continue
        seen.add(key)
        collected.append((uid, role.role_name.strip(), game_id))
    return collected


async def _collect_roles(tajiduo: TajiduoClient, game_id: str) -> list[tuple[str, str, str]]:
    collected: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    bind = await tajiduo.get_bind_role(game_id)
    if bind.uid:
        collected.append((bind.uid, bind.role_name.strip(), game_id))
        seen.add(bind.uid)

    extras = await tajiduo.get_game_roles(game_id)
    for item in extras.roles:
        if item.uid and item.uid not in seen:
            collected.append((item.uid, item.role_name.strip(), game_id))
            seen.add(item.uid)

    if game_id == PRIMARY_GAME_ID:
        await _ensure_bind_role(tajiduo, game_id, extras)
    return collected


async def _ensure_bind_role(tajiduo: TajiduoClient, game_id: str, roles: GameRoleList) -> None:
    if roles.bind_role_id != 0 or not roles.roles:
        return
    first_role_id = roles.roles[0].uid
    if not first_role_id:
        return
    try:
        await tajiduo.bind_game_role(game_id, first_role_id)
    except TajiduoError as error:
        logger.warning(f"[NTE登录] 自动绑定主角色失败 roleId={first_role_id}: {error.message}")
        return
    logger.info(f"[NTE登录] 自动绑定主角色 roleId={first_role_id}")


def _auth_token(user_id: str) -> str:
    return hashlib.sha256(user_id.encode()).hexdigest()[:8]


async def _login_page_url() -> str:
    url = NTEConfig.get_config("NTELoginUrl").data.strip()
    if url:
        return url if url.startswith("http") else f"https://{url}"

    host = core_config.get_config("HOST")
    port = core_config.get_config("PORT")
    if host in {"localhost", "127.0.0.1"}:
        host = "localhost"
    else:
        host = await get_public_ip(host)
    return f"http://{host}:{port}"


async def _send_login_link(bot: Bot, ev: Event, url: str) -> None:
    at_sender = bool(ev.group_id)
    forward = bool(NTEConfig.get_config("NTELoginForward").data)
    private_onebot = not ev.group_id and ev.bot_id == "onebot"

    if NTEConfig.get_config("NTEQRLogin").data:
        path = QR_PATH / f"{ev.user_id}.gif"
        im = [
            f"[异环] 您的id为【{ev.user_id}】\n",
            LoginMsg.LINK_QR,
            MessageSegment.image(await get_qrcode_base64(url, path, ev.bot_id)),
        ]
        try:
            if forward and not private_onebot:
                await bot.send(MessageSegment.node(im))
            elif forward:
                await bot.send(im)
            else:
                await bot.send(im, at_sender=at_sender)
        finally:
            if path.exists():
                path.unlink()
        return

    if NTEConfig.get_config("NTETencentWord").data:
        url = f"https://docs.qq.com/scenario/link.html?url={url}"
    lines = [
        f"[异环] 您的id为【{ev.user_id}】",
        LoginMsg.LINK_COPY,
        f" {url}",
        LoginMsg.link_ttl(),
    ]
    if forward and not private_onebot:
        await bot.send(MessageSegment.node(lines))
    else:
        await bot.send_option("\n".join(lines), login_link_buttons(url), at_sender=at_sender)


async def request_login(bot: Bot, ev: Event) -> None:
    transport_name = NTEConfig.get_config("NTELoginTransport").data.strip()
    if transport_name in {"", "local"}:
        await _request_login_internal(bot, ev)
        return
    base_url = NTEConfig.get_config("NTELoginUrl").data.strip()
    if not base_url:
        logger.warning(f"[NTE登录] 接入方式为 {transport_name} 但 NTELoginUrl 未配置")
        return await send_nte_notify(bot, ev, LoginMsg.USER_CENTER_LOGIN_FAILED)
    await _request_login_external(bot, ev, base_url)


async def _request_login_internal(
    bot: Bot,
    ev: Event,
) -> None:
    auth_token = _auth_token(ev.user_id)
    login_url = f"{await _login_page_url()}/nte/i/{auth_token}"
    await _send_login_link(bot, ev, login_url)

    if LOGIN_CACHE.get(auth_token) is not None:
        return
    LOGIN_CACHE.set(
        auth_token,
        LoginState(
            user_id=ev.user_id,
            bot_id=ev.bot_id,
            group_id=ev.group_id,
            device=LaohuDevice(),
        ),
    )

    result = await _wait(auth_token)
    if result is None:
        await send_nte_notify(bot, ev, LoginMsg.timeout())
        return
    if result.laohu_token == "":
        await send_nte_notify(bot, ev, "完美登录成功")
        return
    await login_by_laohu_token(
        bot,
        ev,
        result.laohu_token,
        result.laohu_user_id,
        dev_code=result.device.device_id,
    )


async def _request_login_external(
    bot: Bot,
    ev: Event,
    base_url: str,
) -> None:
    auth_token = _auth_token(ev.user_id)
    transport = build_transport(base_url)

    try:
        page_url = await transport.start(
            auth=auth_token,
            user_id=ev.user_id,
            bot_id=ev.bot_id,
            group_id=ev.group_id,
        )
    except TransportError as err:
        logger.warning(f"[NTE登录] 外置 start 失败 user_id={ev.user_id}: {err}")
        return await send_nte_notify(bot, ev, LoginMsg.USER_CENTER_LOGIN_FAILED)

    await _send_login_link(bot, ev, page_url)

    # 已有进行中的 listen：仅重发链接，不另开监听，否则多个 listen 会竞争同一会话
    if EXTERNAL_PENDING.get(auth_token):
        return
    EXTERNAL_PENDING.set(auth_token, True)
    try:
        result = await transport.listen(auth_token)
    except TransportError as err:
        logger.warning(f"[NTE登录] 外置 listen 失败 user_id={ev.user_id}: {err}")
        return await send_nte_notify(bot, ev, LoginMsg.USER_CENTER_LOGIN_FAILED)
    finally:
        EXTERNAL_PENDING.pop(auth_token)

    if result is None or result.status == "expired":
        return await send_nte_notify(bot, ev, LoginMsg.timeout())
    if result.status != "success":
        message = result.msg
        if message == "":
            message = LoginMsg.USER_CENTER_LOGIN_FAILED
        return await send_nte_notify(bot, ev, message)
    credential = result.credential
    if credential is None:
        logger.warning(f"[NTE登录] 外置返回成功但凭据为空 user_id={ev.user_id}")
        return await send_nte_notify(bot, ev, LoginMsg.USER_CENTER_LOGIN_FAILED)
    if isinstance(credential, WanmeiCredential):
        await NTEWanmeiUser.save_login(
            user_id=ev.user_id,
            bot_id=ev.bot_id,
            uid=credential.role_id,
            role_name=credential.role_name,
            logon=credential.logon,
        )
        await send_nte_notify(bot, ev, "完美登录成功")
        return
    await login_by_laohu_token(bot, ev, credential.laohu_token, credential.laohu_user_id)


async def login_by_laohu_token(
    bot: Bot,
    ev: Event,
    laohu_token: str,
    laohu_user_id: str,
    *,
    dev_code: str | None = None,
) -> None:
    if dev_code is None:
        dev_code = make_device_id()
    outcome = await _exchange_and_persist(
        user_id=ev.user_id,
        bot_id=ev.bot_id,
        dev_code=dev_code,
        laohu_token=laohu_token,
        laohu_user_id=laohu_user_id,
    )
    if isinstance(outcome, LoginExchangeError):
        await send_nte_notify(bot, ev, outcome.user_msg)
        return
    await send_nte_notify(bot, ev, LoginMsg.TAJIDUO_SUCCESS)
    await _post_login_actions(bot, ev, outcome)


async def _post_login_actions(bot: Bot, ev: Event, outcome: LoginExchange) -> None:
    if NTEConfig.get_config("NTELoginAutoPanel").data:
        await _send_login_panel(bot, ev)
    else:
        create_background_task(_auto_refresh_role_panel(outcome.tajiduo, outcome.roles))


async def _send_login_panel(bot: Bot, ev: Event) -> None:
    from ..nte_role.role_service import run_refresh_role_panel  # 局部导入避免循环依赖

    try:
        await run_refresh_role_panel(bot, ev)
    except Exception as error:
        logger.warning(f"[NTE登录] 自动发送角色面板失败 user_id={ev.user_id}: {error!r}")


async def _auto_refresh_role_panel(tajiduo: TajiduoClient, roles: list[tuple[str, str, str]]) -> None:
    for role_id, _, game_id in roles:
        if game_id != PRIMARY_GAME_ID:
            continue
        try:
            characters = await tajiduo.get_role_characters_data(role_id)
            await save_character_cache(role_id, characters)
        except Exception as error:
            logger.warning(f"[NTE登录] 自动刷新角色面板失败 roleId={role_id}: {error!r}")
            continue
        logger.debug(f"[NTE登录] 自动刷新角色面板 roleId={role_id} 完成")


async def refresh_all_user_tokens(user_id: str, bot_id: str) -> list[tuple[str, bool, str]]:
    users = await NTEUser.list_latest_per_account(user_id, bot_id)
    logger.info(f"[NTE刷新令牌] user_id={user_id} bot_id={bot_id} 取到 {len(users)} 个账号")
    results: list[tuple[str, bool, str]] = []
    for user in users:
        if not user.laohu_token or not user.laohu_user_id:
            reason = "accessToken 登录无法续签" if user.access_token and not user.cookie else "登录信息不完整"
            results.append((user.center_uid, False, reason))
            continue
        ok = await refresh_user_token(user)
        results.append((user.center_uid, ok, "" if ok else "凭证已失效"))
    if users:
        # 把初始 head 推回最新，避免末个成功 refresh 顶掉活跃账号
        await NTEUser.touch_account(user_id, bot_id, users[0].center_uid)
    return results


async def refresh_user_token(user: NTEUser) -> bool:
    """用库里的 laohu 凭据重走 user_center_login 续命；refresh_token 死了但 laohu_token
    还活时使用。会把服务端同 center_uid 的其它会话顶掉。"""
    if not user.laohu_token or not user.laohu_user_id:
        return False

    tajiduo = TajiduoClient(device_id=user.dev_code)
    try:
        session = await tajiduo.user_center_login(user.laohu_token, user.laohu_user_id)
    except TajiduoError as error:
        logger.warning(f"[NTE刷新令牌] 账号 {user.center_uid} 重新登录失败: {error.message}")
        return False

    await NTEUser.update_tokens(
        center_uid=session.center_uid,
        refresh_token=session.refresh_token,
        access_token=session.access_token,
    )

    # 顺带同步角色，best-effort，失败不影响 refresh 成功语义
    try:
        roles = await _collect_all_roles(tajiduo)
        await _persist_login_session(
            user_id=user.user_id,
            bot_id=user.bot_id,
            dev_code=user.dev_code,
            laohu_token=user.laohu_token,
            laohu_user_id=user.laohu_user_id,
            tj_session=session,
            roles=roles,
        )
    except TajiduoError as error:
        logger.warning(f"[NTE刷新令牌] 账号 {session.center_uid} 角色同步失败: {error.message}")
        return True
    logger.info(f"[NTE刷新令牌] 账号 {session.center_uid} 已刷新，同步 {len(roles)} 个角色")
    return True
