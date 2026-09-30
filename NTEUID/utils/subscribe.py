from __future__ import annotations

from typing import Literal

from gsuid_core.logger import logger
from gsuid_core.models import Event, Message
from gsuid_core.subscribe import gs_subscribe
from gsuid_core.utils.database.models import Subscribe

# NTEUID 用到的订阅 topic 集中声明，业务模块不要散落字符串。
TOPIC_NOTICE = "订阅NTE公告"
TOPIC_SIGN_PUSH = "订阅NTE自动签到"
TOPIC_SIGN_SUMMARY = "订阅NTE签到结果"
TOPIC_STAMINA_PUSH = "订阅NTE体力"


async def subscribe_single(topic: str, ev: Event, *, extra_message: str | None = None) -> None:
    """同 user 全局唯一一条订阅；后开覆盖前开（要多群独立请用 `session`）。"""
    await gs_subscribe.add_subscribe("single", topic, ev, extra_message=extra_message)


async def unsubscribe_single(topic: str, ev: Event) -> int:
    return await Subscribe.delete_row(task_name=topic, user_id=ev.user_id, bot_id=ev.bot_id)


async def delete_all_subscribers(topic: str) -> int:
    subs = await list_subscribers(topic)
    if not subs:
        return 0
    await Subscribe.delete_row(task_name=topic)
    return len(subs)


async def get_single_subscription(topic: str, ev: Event) -> Subscribe | None:
    rows = await Subscribe.select_rows(task_name=topic, user_id=ev.user_id, bot_id=ev.bot_id)
    return rows[0] if rows else None


async def update_subscribe_message(sub: Subscribe, extra_message: str) -> None:
    await Subscribe.update_data_by_data({"id": sub.id}, {"extra_message": extra_message})


async def subscribe_session(topic: str, ev: Event, *, extra_message: str = "") -> bool:
    """同一个群只保留一条订阅；重复订阅会刷新发送路由。返回此前是否已存在。"""
    existed = await unsubscribe_session(topic, ev)
    await gs_subscribe.add_subscribe("session", topic, ev, extra_message=extra_message)
    return bool(existed)


async def unsubscribe_session(topic: str, ev: Event) -> int:
    if ev.group_id:
        return await Subscribe.delete_row(task_name=topic, group_id=ev.group_id)
    return await Subscribe.delete_row(task_name=topic, user_id=ev.user_id, bot_id=ev.bot_id)


async def list_subscribers(topic: str) -> list[Subscribe]:
    subs = await gs_subscribe.get_subscribe(topic)
    return list(subs) if subs else []


def _user_type(raw: str) -> Literal["group", "direct", "channel", "sub_channel"]:
    match raw:
        case "group" | "direct" | "channel" | "sub_channel":
            return raw
    raise ValueError(f"unknown subscription user_type: {raw!r}")


def event_from_sub(sub: Subscribe) -> Event:
    """按订阅入库时的会话字段重建 Event，用于需要 Event 的工具函数。"""
    return Event(
        bot_id=sub.bot_id,
        user_id=sub.user_id,
        bot_self_id=sub.bot_self_id,
        user_type=_user_type(sub.user_type),
        group_id=sub.group_id,
        WS_BOT_ID=sub.WS_BOT_ID,
        real_bot_id=sub.bot_id,
        msg_id=sub.msg_id or "",
    )


async def broadcast(topic: str, messages: Message | list[Message] | str | bytes) -> None:
    """同一份消息发给 topic 全员；单订阅失败只 warn 不传播。"""
    for sub in await list_subscribers(topic):
        try:
            await sub.send(messages)
        except Exception as error:
            logger.warning(f"[NTE订阅] {topic} 推送 user={sub.user_id} group={sub.group_id} 失败: {error!r}")
