from __future__ import annotations

from gsuid_core.bot import Bot
from gsuid_core.models import Event

from ..utils.msgs import CommonMsg, ResignMsg, send_nte_notify
from .sign_runner import account_lock
from ..utils.session import SessionCall, is_auth_error
from ..utils.database import NTEUser
from ..utils.sdk.tajiduo import TajiduoClient
from ..utils.sdk.tajiduo_model import TajiduoError

TAG = "补签"

# 游戏固定规则：单次补签消耗 200 呗果积点。次数限制 / 余额由服务端校验，
# 剩余次数在签到日历中展示（本地不落库、不做配置）。
RESIGN_COST = 200


async def run_user_resign(bot: Bot, ev: Event, game_id: str, role_id: str) -> None:
    selected_user = None
    if role_id:
        selected_user = await NTEUser.get_by_role(ev.user_id, ev.bot_id, role_id, game_id)
        if selected_user is None:
            return await send_nte_notify(bot, ev, ResignMsg.role_not_found(role_id))

    async with SessionCall(
        bot,
        ev,
        tag=TAG,
        not_logged_in_msg=CommonMsg.not_logged_in(),
        login_expired_msg=CommonMsg.login_expired(),
        load_failed_msg=ResignMsg.FAILED,
        game_id=game_id,
        selected_user=selected_user,
    ) as session:
        if session is None:
            return
        user, client = session

        lock = account_lock(user.center_uid)
        if lock.locked():
            return await send_nte_notify(bot, ev, ResignMsg.busy())
        async with lock:
            await _do_resign(bot, ev, user, client, game_id)


async def _do_resign(bot: Bot, ev: Event, user: NTEUser, client: TajiduoClient, game_id: str) -> None:
    state = await client.get_game_sign_state(game_id)
    if not state.today_sign:
        return await send_nte_notify(bot, ev, ResignMsg.not_signed_today())
    if state.days >= state.day:
        return await send_nte_notify(bot, ev, ResignMsg.no_missed())

    try:
        await client.game_sign_resign(user.uid, game_id)
    except TajiduoError as error:
        if is_auth_error(error) or error.server_message is None:
            raise
        return await send_nte_notify(bot, ev, error.server_message)
    await send_nte_notify(
        bot,
        ev,
        ResignMsg.done(user.role_name, user.uid, RESIGN_COST),
    )
