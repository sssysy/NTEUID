from __future__ import annotations

from typing import cast
from datetime import datetime

from pydantic import BaseModel, ConfigDict, TypeAdapter

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event, Message
from gsuid_core.segment import MessageSegment
from gsuid_core.utils.database.models import Subscribe
from gsuid_core.utils.image.image_tools import get_event_avatar

from ..utils.msgs import CommonMsg, StaminaMsg, send_nte_notify
from .realtime_card import draw_realtime_img
from ..utils.database import NTEUser
from ..utils.subscribe import (
    TOPIC_STAMINA_PUSH,
    event_from_sub,
    list_subscribers,
    subscribe_single,
    unsubscribe_single,
    delete_all_subscribers,
    get_single_subscription,
    update_subscribe_message,
)
from ..utils.sdk.tajiduo import TajiduoClient
from ..nte_config.nte_config import NTEConfig
from ..utils.sdk.tajiduo_model import TajiduoError

_THRESHOLD_MIN = 10
_THRESHOLD_MAX = 360
_THRESHOLD_DEFAULT = 200


class StaminaRoleState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    threshold: int | None = None
    last_push_date: str = ""
    today_pushed: int = 0

    def effective_threshold(self) -> int:
        return _THRESHOLD_DEFAULT if self.threshold is None else self.threshold

    def pushed_today(self, today: str) -> int:
        return self.today_pushed if self.last_push_date == today else 0


_STAMINA_STATE_ADAPTER = TypeAdapter(dict[str, StaminaRoleState])


def _parse_threshold_arg(raw: str) -> int | None:
    text = raw.strip()
    if not text:
        return None
    value = int(text)
    if not _THRESHOLD_MIN <= value <= _THRESHOLD_MAX:
        raise ValueError(value)
    return value


async def run_subscribe_stamina(bot: Bot, ev: Event, threshold_text: str = "") -> None:
    if not NTEConfig.get_config("NTEStaminaPushOpen").data:
        return await send_nte_notify(bot, ev, StaminaMsg.PUSH_CLOSED)

    try:
        threshold = _parse_threshold_arg(threshold_text)
    except ValueError:
        return await send_nte_notify(bot, ev, StaminaMsg.threshold_invalid(_THRESHOLD_MIN, _THRESHOLD_MAX))

    user = await NTEUser.get_active(ev.user_id, ev.bot_id)
    if user is None or not user.access_token:
        has_history = await NTEUser.has_logged_in_history(ev.user_id, ev.bot_id)
        return await send_nte_notify(bot, ev, CommonMsg.not_logged_in(has_history=has_history))

    existing = await get_single_subscription(TOPIC_STAMINA_PUSH, ev)
    state = _STAMINA_STATE_ADAPTER.validate_json(cast(str, existing.extra_message)) if existing else {}
    key = f"{user.game_id}:{user.uid}"
    subscribed = key in state
    role_state = state[key] if subscribed else StaminaRoleState()
    if threshold is not None:
        role_state = role_state.model_copy(update={"threshold": threshold})
    state[key] = role_state

    await subscribe_single(TOPIC_STAMINA_PUSH, ev, extra_message=_STAMINA_STATE_ADAPTER.dump_json(state).decode())
    if subscribed and threshold is not None:
        msg = StaminaMsg.threshold_updated(user.role_name, role_state.effective_threshold())
    else:
        msg = StaminaMsg.subscribed(user.role_name, role_state.effective_threshold())
    await send_nte_notify(bot, ev, msg)


async def run_unsubscribe_stamina(bot: Bot, ev: Event) -> None:
    existing = await get_single_subscription(TOPIC_STAMINA_PUSH, ev)
    if existing is None:
        return await send_nte_notify(bot, ev, StaminaMsg.NOT_SUBSCRIBED)

    user = await NTEUser.get_active(ev.user_id, ev.bot_id)
    if user is None:
        has_history = await NTEUser.has_logged_in_history(ev.user_id, ev.bot_id)
        return await send_nte_notify(bot, ev, CommonMsg.not_logged_in(has_history=has_history))

    state = _STAMINA_STATE_ADAPTER.validate_json(cast(str, existing.extra_message))
    key = f"{user.game_id}:{user.uid}"
    if state.pop(key, None) is None:
        return await send_nte_notify(bot, ev, StaminaMsg.NOT_SUBSCRIBED)
    if state:
        await update_subscribe_message(existing, _STAMINA_STATE_ADAPTER.dump_json(state).decode())
        return await send_nte_notify(bot, ev, StaminaMsg.unsubscribed(user.role_name))

    await unsubscribe_single(TOPIC_STAMINA_PUSH, ev)
    await send_nte_notify(bot, ev, StaminaMsg.unsubscribed(user.role_name))


async def run_unsubscribe_all_stamina(bot: Bot, ev: Event) -> None:
    removed = await unsubscribe_single(TOPIC_STAMINA_PUSH, ev)
    await send_nte_notify(bot, ev, StaminaMsg.UNSUBSCRIBED_ALL if removed else StaminaMsg.NOT_SUBSCRIBED)


async def run_delete_all_stamina_subscriptions(bot: Bot, ev: Event) -> None:
    removed = await delete_all_subscribers(TOPIC_STAMINA_PUSH)
    await send_nte_notify(bot, ev, StaminaMsg.all_deleted(removed) if removed else StaminaMsg.NO_SUBSCRIBERS)


async def check_stamina_push() -> None:
    if not NTEConfig.get_config("NTEStaminaPushOpen").data:
        return
    subs = await list_subscribers(TOPIC_STAMINA_PUSH)
    if not subs:
        return
    max_pushes = max(1, int(NTEConfig.get_config("NTEStaminaDailyPushLimit").data))
    today = datetime.now().strftime("%Y-%m-%d")
    for sub in subs:
        await _push_one(sub, max_pushes, today)


async def _push_one(sub: Subscribe, max_pushes: int, today: str) -> None:
    state = _STAMINA_STATE_ADAPTER.validate_json(cast(str, sub.extra_message))
    if not state:
        return

    sub_ev = event_from_sub(sub)
    segments: list[Message] = []
    pushed: dict[str, StaminaRoleState] = {}
    for key, role_state in state.items():
        if role_state.pushed_today(today) >= max_pushes:
            continue
        game_id, uid = key.split(":", 1)
        user = await NTEUser.get_by_role(sub.user_id, sub.bot_id, uid, game_id)
        if user is None or not user.access_token:
            continue

        try:
            client = TajiduoClient.from_user(user)
            client.access_token = user.access_token
            home = await client.get_role_home(user.uid)
        except TajiduoError as error:
            logger.warning(f"[NTE体力] 拉取失败 user={sub.user_id} uid={uid}: {error}")
            continue

        if home.stamina_value < role_state.effective_threshold():
            continue

        try:
            avatar = await get_event_avatar(sub_ev)
            img = await draw_realtime_img(avatar, user, home)
        except Exception as error:
            logger.warning(f"[NTE体力] 出图失败 user={sub.user_id}: {error!r}")
            continue

        segments.extend(
            [
                MessageSegment.text(
                    StaminaMsg.stamina_reached(user.role_name, home.stamina_value, home.stamina_max_value)
                ),
                MessageSegment.image(img),
            ]
        )
        pushed[key] = role_state.model_copy(
            update={"last_push_date": today, "today_pushed": role_state.pushed_today(today) + 1}
        )

    if not segments:
        return
    if sub.group_id:
        segments.insert(0, MessageSegment.at(sub.user_id))

    try:
        await sub.send(segments)
    except Exception as error:
        logger.warning(f"[NTE体力] 推送失败 user={sub.user_id} group={sub.group_id}: {error!r}")
        return

    state.update(pushed)
    await update_subscribe_message(sub, _STAMINA_STATE_ADAPTER.dump_json(state).decode())
