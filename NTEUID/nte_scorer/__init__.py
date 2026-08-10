from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event

from .scorer_service import run_scorer_add, run_scorer_set, run_scorer_list, run_scorer_remove, run_scorer_update

sv_nte_scorer = SV("nte评分包管理", pm=1)


@sv_nte_scorer.on_command("scorer查看", block=True)
async def list_scorer(bot: Bot, ev: Event):
    await run_scorer_list(bot, ev)


@sv_nte_scorer.on_command("scorer设置", block=True)
async def set_scorer(bot: Bot, ev: Event):
    await run_scorer_set(bot, ev, ev.text.strip())


@sv_nte_scorer.on_command("scorer增加", block=True)
async def add_scorer(bot: Bot, ev: Event):
    await run_scorer_add(bot, ev, ev.text.strip())


@sv_nte_scorer.on_command("scorer删除", block=True)
async def remove_scorer(bot: Bot, ev: Event):
    await run_scorer_remove(bot, ev, ev.text.strip())


@sv_nte_scorer.on_command("scorer更新", block=True)
async def update_scorer(bot: Bot, ev: Event):
    await run_scorer_update(bot, ev, ev.text.strip())
