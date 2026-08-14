from __future__ import annotations

import asyncio
from datetime import timedelta

from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment

from ..utils.html import data_uri, render_card
from ..utils.msgs import TITLE, BindMsg, CommonMsg, send_nte_notify
from ..utils.image import TEXT_PATH as CARD_TEXTURE_PATH
from ..utils.database import NTEUser, NTEWanmeiUser
from ..nte_config.prefix import nte_prefix
from ..utils.sdk.tajiduo import TajiduoClient
from ..utils.msgs.buttons import switched_buttons, binding_switch_buttons
from ..utils.game_registry import GAME_LABELS
from ..utils.sdk.tajiduo_model import TajiduoError
from ..utils.resource.RESOURCE_PATH import NTE_TEMPLATES


async def view_bindings(bot: Bot, ev: Event) -> None:
    accounts, wanmei_accounts = await asyncio.gather(
        NTEUser.list_latest_per_account(ev.user_id, ev.bot_id),
        NTEWanmeiUser.list_accounts(ev.user_id, ev.bot_id),
    )
    if not accounts and not wanmei_accounts:
        has_history = await NTEUser.has_logged_in_history(ev.user_id, ev.bot_id)
        return await send_nte_notify(bot, ev, CommonMsg.not_logged_in(has_history=has_history))

    grouped: dict[str, list[NTEUser]] = {account.center_uid: [] for account in accounts}
    for row in await NTEUser.list_sign_targets_by_user(ev.user_id, ev.bot_id):
        grouped[row.center_uid].append(row)

    bg, icon, footer = await asyncio.gather(
        data_uri(CARD_TEXTURE_PATH / "bg.jpg", "image/jpeg"),
        data_uri(CARD_TEXTURE_PATH / "card_icon.png", "image/png"),
        data_uri(CARD_TEXTURE_PATH / "footer.png", "image/png"),
    )
    html = NTE_TEMPLATES.get_template("bind.html.j2").render(
        bg=bg,
        icon=icon,
        footer=footer,
        title="账号绑定",
        subtitle="NTE · ACCOUNT BINDING",
        prefix=nte_prefix(),
        accounts=[
            {
                "center_uid": account.center_uid,
                "current": index == 0,
                "roles": [
                    {
                        "game": GAME_LABELS[r.game_id],
                        "role_name": r.role_name,
                        "uid": r.uid,
                    }
                    for r in grouped[account.center_uid]
                ],
            }
            for index, account in enumerate(accounts)
        ],
        wanmei_accounts=[
            {
                "role_name": account.role_name,
                "uid": account.uid,
                "current": index == 0,
                "synced_at": account.scratch_synced_at.strftime("%m-%d %H:%M")
                if account.scratch_synced_at is not None
                else "",
            }
            for index, account in enumerate(wanmei_accounts)
        ],
    )
    image = MessageSegment.image(await render_card(html))
    buttons = binding_switch_buttons(len(accounts), len(wanmei_accounts))
    if buttons:
        await bot.send_option(image, buttons, at_sender=bool(ev.group_id))
        return
    await bot.send(image)


async def switch_wanmei_account(bot: Bot, ev: Event, target: str) -> None:
    accounts = await NTEWanmeiUser.list_accounts(ev.user_id, ev.bot_id)
    if not accounts:
        return await send_nte_notify(bot, ev, BindMsg.WANMEI_NOT_LOGGED_IN)
    if target == "" and len(accounts) == 1:
        return await send_nte_notify(bot, ev, BindMsg.WANMEI_ONLY_ONE_ACCOUNT)
    if target == "":
        row = await NTEWanmeiUser.cycle_account(ev.user_id, ev.bot_id)
    else:
        row = await NTEWanmeiUser.switch_account(ev.user_id, ev.bot_id, target)
    if row is None:
        return await send_nte_notify(bot, ev, BindMsg.target_not_found())
    await send_nte_notify(bot, ev, BindMsg.wanmei_switch_done(row.role_name, row.uid))


async def switch_binding(bot: Bot, ev: Event, target: str) -> None:
    accounts = await NTEUser.list_latest_per_account(ev.user_id, ev.bot_id)
    if len(accounts) < 2:
        msg = CommonMsg.not_logged_in() if not accounts else BindMsg.ONLY_ONE_ACCOUNT
        return await send_nte_notify(bot, ev, msg)

    account = _resolve_target(target, accounts)
    if account is None:
        return await send_nte_notify(bot, ev, BindMsg.target_not_found())

    if not target:
        await NTEUser.touch_account(
            ev.user_id,
            ev.bot_id,
            accounts[0].center_uid,
            when=accounts[-1].updated_at - timedelta(seconds=1),
        )
    await NTEUser.touch_account(ev.user_id, ev.bot_id, account.center_uid)
    msg = BindMsg.switch_done(account.center_uid, account.role_name, account.uid)
    await bot.send_option(f"{TITLE}{msg}", switched_buttons(), at_sender=bool(ev.group_id))


async def get_laohu_tokens(bot: Bot, ev: Event) -> None:
    accounts = await NTEUser.list_latest_per_account(ev.user_id, ev.bot_id)
    if not accounts:
        return await send_nte_notify(bot, ev, CommonMsg.not_logged_in())

    lines: list[str] = []
    for a in accounts:
        if not a.laohu_token or not a.laohu_user_id:
            continue
        lines += [
            f"塔吉多账号: {a.center_uid}",
            "laohuToken,laohuUserId:",
            f"{a.laohu_token},{a.laohu_user_id}",
            "--------------------------------",
        ]
    if not lines:
        return await send_nte_notify(bot, ev, BindMsg.TOKEN_EMPTY)
    await send_nte_notify(bot, ev, "\n".join(lines))


async def get_access_tokens(bot: Bot, ev: Event) -> None:
    accounts = await NTEUser.list_latest_per_account(ev.user_id, ev.bot_id)
    if not accounts:
        return await send_nte_notify(bot, ev, CommonMsg.not_logged_in())

    lines: list[str] = []
    for account in accounts:
        if not account.access_token:
            continue
        client = TajiduoClient.from_user(account)
        client.access_token = account.access_token
        try:
            info = await client.get_user_full_info()
        except TajiduoError:
            continue
        if info.center_uid != account.center_uid:
            continue
        lines += [
            f"塔吉多账号: {account.center_uid}",
            "accessToken:",
            account.access_token,
            "--------------------------------",
        ]
    if not lines:
        return await send_nte_notify(bot, ev, BindMsg.TOKEN_EMPTY)
    await send_nte_notify(bot, ev, "\n".join(lines))


def _resolve_target(target: str, accounts: list[NTEUser]) -> NTEUser | None:
    if target == "":
        return accounts[1]
    if target.isdigit() and 1 <= int(target) <= len(accounts):
        return accounts[int(target) - 1]
    return next((a for a in accounts if a.center_uid == target), None)
