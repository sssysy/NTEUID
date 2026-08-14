from __future__ import annotations

import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass
from collections.abc import Iterable

from PIL import Image

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment
from gsuid_core.utils.image.image_tools import get_event_avatar

from ..utils.html import data_uri, render_card, image_data_uri
from ..utils.msgs import ScratchMsg, send_nte_notify
from ..utils.image import TEXT_PATH as CARD_TEXTURE_PATH, make_nte_role_title
from ..utils.avatar import fetch_avatar
from .scratch_model import (
    ScratchStats,
    ScratchRankKind,
    ScratchRankBoard,
    ScratchRankEntry,
    ScratchRankScope,
    money,
    rank_window,
    signed_money,
    history_start,
)
from ..utils.database import NTEWanmeiUser, NTEWanmeiGroupMember, NTEWanmeiScratchRecord
from .captcha_service import solve_captcha
from ..utils.constants import (
    WANMEI_SCRATCH_RANK_LIMIT,
    WANMEI_SCRATCH_REFRESH_DAYS,
    WANMEI_SCRATCH_FULL_REFRESH_START_AT,
)
from ..utils.sdk.wanmei import WanmeiKfClient
from ..utils.sdk.wanmei_model import WanmeiError, WanmeiAuthExpired, WanmeiScratchRecord
from ..utils.resource.RESOURCE_PATH import NTE_TEMPLATES
from ..utils.resource.scratch_items import get_scratch_item, load_scratch_items


def _split_windows(start_at: datetime, end_at: datetime) -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    cursor = start_at
    while cursor <= end_at:
        window_end = min(cursor + timedelta(days=7), end_at)
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(seconds=1)
    return windows


async def _avatar_uri(avatar: Image.Image) -> str:
    def _avatar_thumb(avatar: Image.Image) -> Image.Image:
        thumb = avatar.copy()
        thumb.thumbnail((56, 56), Image.Resampling.LANCZOS)
        return thumb

    return await image_data_uri(await asyncio.to_thread(_avatar_thumb, avatar))


@dataclass(frozen=True, slots=True, kw_only=True)
class _ScratchWindow:
    records: list[WanmeiScratchRecord]
    official_cost: int
    official_reward: int


async def _fetch_window(
    client: WanmeiKfClient,
    uid: str,
    start_at: datetime,
    end_at: datetime,
    cap_ticket: str,
    sec_code: str,
) -> _ScratchWindow:
    page_no = 1
    records: list[WanmeiScratchRecord] = []
    summary: tuple[int, int] | None = None
    while True:
        data = await client.search_scratch(
            role_id=uid,
            start_time=start_at.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=end_at.strftime("%Y-%m-%d %H:%M:%S"),
            cap_ticket=cap_ticket,
            sec_code=sec_code,
            page_no=page_no,
        )
        if data.total == 0:
            return _ScratchWindow(records=[], official_cost=0, official_reward=0)
        if summary is None:
            summary = data.summary
        elif data.summary != summary:
            raise WanmeiError(ScratchMsg.PAGE_SUMMARY_MISMATCH)
        records.extend(data.result)
        if len(records) >= data.total:
            return _ScratchWindow(
                records=records,
                official_cost=summary[0],
                official_reward=summary[1],
            )
        page_no += 1


def _validate_window(window: _ScratchWindow) -> None:
    reward = sum(record.gain for record in window.records)
    if reward != window.official_reward:
        raise WanmeiError(
            ScratchMsg.REWARD_SUMMARY_MISMATCH,
            {"local": reward, "official": window.official_reward},
        )

    cost = 0
    for record in window.records:
        item = get_scratch_item(record.scratch_card_id)
        if item is None:
            return
        cost += item.cost
    if cost != window.official_cost:
        raise WanmeiError(
            ScratchMsg.COST_SUMMARY_MISMATCH,
            {
                "records": len(window.records),
                "local": cost,
                "official": window.official_cost,
            },
        )


def _resolve_costs(refs: Iterable[str]) -> tuple[dict[str, int], set[str]]:
    costs: dict[str, int] = {}
    missing: set[str] = set()
    for ref in refs:
        item = get_scratch_item(ref)
        if item is None:
            missing.add(ref)
        else:
            costs[ref] = item.cost
    return costs, missing


async def _sync_records(
    user: NTEWanmeiUser,
    *,
    force_refresh: bool = False,
) -> tuple[list[NTEWanmeiScratchRecord], datetime, datetime]:
    end_at = datetime.now().replace(microsecond=0)
    history_start_at = WANMEI_SCRATCH_FULL_REFRESH_START_AT if force_refresh else history_start(end_at)
    query_start_at = (
        history_start_at
        if force_refresh or user.scratch_synced_at is None
        else max(
            history_start_at,
            min(
                user.scratch_synced_at,
                end_at - timedelta(days=WANMEI_SCRATCH_REFRESH_DAYS),
            ),
        )
    )
    query_start_at = query_start_at.replace(microsecond=0)

    client = WanmeiKfClient(user.logon, timeout=30.0)
    await load_scratch_items()
    cap_ticket, sec_code = await solve_captcha(client)
    fetched: list[WanmeiScratchRecord] = []
    for index, (window_start, window_end) in enumerate(_split_windows(query_start_at, end_at)):
        if index > 0:
            await asyncio.sleep(1)
        window = await _fetch_window(
            client,
            user.uid,
            window_start,
            window_end,
            cap_ticket,
            sec_code,
        )
        _validate_window(window)
        fetched.extend(window.records)

    rows: list[NTEWanmeiScratchRecord] = []
    skipped_ids: set[str] = set()
    for record in fetched:
        row = _scratch_row(user, record)
        if row is None:
            skipped_ids.add(record.scratch_card_id)
        else:
            rows.append(row)
    if skipped_ids:
        logger.warning(f"[NTE刮刮乐] user_id={user.user_id} uid={user.uid} 未收录道具跳过={sorted(skipped_ids)}")
    await NTEWanmeiScratchRecord.replace_range(
        user_id=user.user_id,
        bot_id=user.bot_id,
        uid=user.uid,
        start_at=query_start_at,
        end_at=end_at,
        history_start_at=history_start_at,
        records=rows,
    )
    logger.info(
        f"[NTE刮刮乐] user_id={user.user_id} uid={user.uid} "
        f"window={query_start_at:%Y-%m-%d}~{end_at:%Y-%m-%d} records={len(rows)} 同步完成"
    )
    records = await NTEWanmeiScratchRecord.list_for_account(user.user_id, user.bot_id, user.uid)
    return records, history_start_at, end_at


def _scratch_row(
    user: NTEWanmeiUser,
    record: WanmeiScratchRecord,
) -> NTEWanmeiScratchRecord | None:
    item = get_scratch_item(record.scratch_card_id)
    if item is None:
        if not any("\u4e00" <= char <= "\u9fff" for char in record.scratch_card_id):
            return None
        return NTEWanmeiScratchRecord(
            user_id=user.user_id,
            bot_id=user.bot_id,
            uid=user.uid,
            log_time=record.log_time,
            card_name=record.scratch_card_id,
            gain=record.gain,
            extra=record.extra,
            award=record.award,
        )
    award = record.award.replace(
        f"未知*{item.gift_count}",
        f"{item.gift_name}*{item.gift_count}",
    )
    return NTEWanmeiScratchRecord(
        user_id=user.user_id,
        bot_id=user.bot_id,
        uid=user.uid,
        log_time=record.log_time,
        card_name=item.name,
        gain=record.gain,
        extra=record.extra,
        award=award,
    )


def _make_title(avatar: Image.Image, role_name: str, uid: str) -> Image.Image:
    banner = make_nte_role_title(avatar, role_name, uid)
    return banner.crop(banner.getbbox())


def _visible_rank_rows(
    entries: list[ScratchRankEntry],
    current_account: tuple[str, str] | None,
) -> list[tuple[int, ScratchRankEntry, bool]]:
    rows = [(rank, entry, False) for rank, entry in enumerate(entries[:WANMEI_SCRATCH_RANK_LIMIT], 1)]
    if current_account is None:
        return rows
    for rank, entry in enumerate(entries[WANMEI_SCRATCH_RANK_LIMIT:], WANMEI_SCRATCH_RANK_LIMIT + 1):
        if (entry.user_id, entry.uid) == current_account:
            rows.append((rank, entry, True))
            break
    return rows


async def send_scratch_card(bot: Bot, ev: Event, *, force_refresh: bool = False) -> None:
    user = await NTEWanmeiUser.get_user(ev.user_id, ev.bot_id)
    if user is None:
        await send_nte_notify(bot, ev, ScratchMsg.not_logged_in())
        return
    try:
        records, history_start_at, synced_at = await _sync_records(user, force_refresh=force_refresh)
    except WanmeiAuthExpired:
        await send_nte_notify(bot, ev, ScratchMsg.login_expired())
        return
    except WanmeiError as error:
        logger.warning(f"[NTE刮刮乐] user_id={ev.user_id} 同步失败: {error.message}")
        await send_nte_notify(bot, ev, error.message)
        return
    if not records:
        await send_nte_notify(bot, ev, ScratchMsg.NO_RECORDS)
        return
    if ev.group_id:
        await NTEWanmeiGroupMember.upsert_member(
            group_id=ev.group_id,
            bot_id=ev.bot_id,
            user_id=ev.user_id,
            uid=user.uid,
            role_name=user.role_name,
        )
    cost_by_name, missing = _resolve_costs(record.card_name for record in records)
    if missing:
        await send_nte_notify(bot, ev, ScratchMsg.saved_with_missing_resources(missing))
        return
    stats = ScratchStats.from_records(records, history_start_at, synced_at, cost_by_name)
    avatar = await get_event_avatar(ev)
    bg, footer, title_img = await asyncio.gather(
        data_uri(CARD_TEXTURE_PATH / "bg.jpg", "image/jpeg"),
        data_uri(CARD_TEXTURE_PATH / "footer.png", "image/png"),
        image_data_uri(await asyncio.to_thread(_make_title, avatar, user.role_name, user.uid)),
    )
    html = NTE_TEMPLATES.get_template("scratch.html.j2").render(
        bg=bg,
        footer=footer,
        title_img=title_img,
        stats=stats,
        curve=stats.curve(),
        money=money,
        signed_money=signed_money,
    )
    await bot.send(MessageSegment.image(await render_card(html)))


async def send_scratch_rank(
    bot: Bot,
    ev: Event,
    kind: ScratchRankKind,
    scope: ScratchRankScope,
    board: ScratchRankBoard | None,
) -> None:
    current_user = await NTEWanmeiUser.get_user(ev.user_id, ev.bot_id)
    current_account = (ev.user_id, current_user.uid) if current_user is not None else None
    accounts: list[tuple[str, str]] | None = None
    if scope == "group":
        group_id = ev.group_id
        if group_id is None:
            await send_nte_notify(bot, ev, ScratchMsg.RANK_GROUP_ONLY)
            return
        members = await NTEWanmeiGroupMember.list_members(group_id, ev.bot_id)
        accounts = [(member.user_id, member.uid) for member in members]
        if current_account is not None:
            accounts = [account for account in accounts if account[0] != ev.user_id]
            accounts.append(current_account)
    await load_scratch_items()
    label, start_at, end_at = rank_window(kind)
    aggregates = await NTEWanmeiScratchRecord.aggregate_for_rank(
        ev.bot_id,
        start_at,
        end_at,
        accounts,
    )
    scope_label = ScratchMsg.RANK_SCOPE_GROUP if scope == "group" else ScratchMsg.RANK_SCOPE_BOT
    if not aggregates:
        await send_nte_notify(bot, ev, ScratchMsg.rank_empty(scope_label, label))
        return
    cost_by_name, missing = _resolve_costs(card_name for _, _, _, card_name, _, _ in aggregates)
    if missing:
        await send_nte_notify(bot, ev, ScratchMsg.resources_missing(missing))
        return
    totals: dict[tuple[str, str, str], list[int]] = {}
    for user_id, uid, role_name, card_name, gain, count in aggregates:
        total = totals.setdefault((user_id, uid, role_name), [0, 0, 0])
        total[0] += cost_by_name[card_name] * count
        total[1] += gain
        total[2] += count
    entries = [
        ScratchRankEntry(
            user_id=user_id,
            role_name=role_name,
            uid=uid,
            total_cost=cost,
            total_gain=gain,
            total_count=count,
        )
        for (user_id, uid, role_name), (cost, gain, count) in totals.items()
    ]
    losses = sorted((entry for entry in entries if entry.net < 0), key=lambda entry: entry.net)
    profits = sorted((entry for entry in entries if entry.net >= 0), key=lambda entry: entry.net, reverse=True)
    board_kinds: tuple[ScratchRankBoard, ...] = ("loss", "profit") if board is None else (board,)
    boards = [
        (
            board_kind,
            losses if board_kind == "loss" else profits,
            _visible_rank_rows(losses if board_kind == "loss" else profits, current_account),
        )
        for board_kind in board_kinds
    ]
    shown = [entry for _, _, rows in boards for _, entry, _ in rows]
    user_ids = list(dict.fromkeys(entry.user_id for entry in shown))
    avatars = await asyncio.gather(*(fetch_avatar(ev, user_id) for user_id in user_ids))
    avatar_uris = await asyncio.gather(*(_avatar_uri(avatar) for avatar in avatars))
    uri_by_user = dict(zip(user_ids, avatar_uris, strict=True))

    rank_bg, icon, footer = await asyncio.gather(
        data_uri(
            Path(__file__).parent / "texture2d" / "scratch_rank_bg.jpg",
            "image/jpeg",
        ),
        data_uri(CARD_TEXTURE_PATH / "card_icon.png", "image/png"),
        data_uri(CARD_TEXTURE_PATH / "footer.png", "image/png"),
    )
    period_end = end_at - timedelta(seconds=1) if kind in {"yesterday", "last_week"} else end_at
    period = f"{start_at:%Y.%m.%d} — {period_end:%Y.%m.%d}"
    for board_kind, ranked, rows in boards:
        html = NTE_TEMPLATES.get_template("scratch_rank.html.j2").render(
            rank_bg=rank_bg,
            icon=icon,
            footer=footer,
            board_kind=board_kind,
            board_title=ScratchMsg.LOSS_TITLE if board_kind == "loss" else ScratchMsg.PROFIT_TITLE,
            board_subtitle=ScratchMsg.LOSS_SUBTITLE if board_kind == "loss" else ScratchMsg.PROFIT_SUBTITLE,
            value_label=ScratchMsg.LOSS_VALUE_LABEL if board_kind == "loss" else ScratchMsg.PROFIT_VALUE_LABEL,
            empty_text=ScratchMsg.LOSS_EMPTY if board_kind == "loss" else ScratchMsg.PROFIT_EMPTY,
            scope_label=scope_label,
            label=label,
            period=period,
            rank_limit=WANMEI_SCRATCH_RANK_LIMIT,
            account_total=len(entries),
            board_total=len(ranked),
            record_total=f"{sum(entry.total_count for entry in ranked):,}",
            entries=[
                {
                    "rank": rank,
                    "rank_text": f"{rank:02d}",
                    "avatar": uri_by_user[entry.user_id],
                    "role_name": entry.role_name,
                    "uid": entry.uid,
                    "cost": money(entry.total_cost),
                    "gain": money(entry.total_gain),
                    "count": f"{entry.total_count:,}",
                    "profit_rate": f"{entry.profit_rate:+.1f}%",
                    "net": signed_money(entry.net),
                    "is_self": (entry.user_id, entry.uid) == current_account,
                    "overflow": is_overflow,
                }
                for rank, entry, is_overflow in rows
            ],
        )
        image = await render_card(
            html,
            width=780,
            image_format="jpeg",
            jpeg_quality=85,
        )
        await bot.send(MessageSegment.image(image))
