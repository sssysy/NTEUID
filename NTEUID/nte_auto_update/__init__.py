from gsuid_core.aps import scheduler
from gsuid_core.logger import logger

from ..nte_config.nte_config import NTEConfig
from ..nte_scorer.scorer_service import update_scorer_packs
from ..utils.resource.git_resource import update_resources

AUTO_UPDATE_MINUTES: int = NTEConfig.get_config("NTEAutoUpdateMinutes").data


if AUTO_UPDATE_MINUTES <= 0:
    AUTO_UPDATE_MINUTES = 60


@scheduler.scheduled_job(
    "interval",
    minutes=AUTO_UPDATE_MINUTES,
    id="nte_auto_update_resources",
    name="NTEUID 资源自动更新",
    max_instances=1,
    coalesce=True,
)
async def auto_update_resources() -> None:
    await update_resources(silent=True)


@scheduler.scheduled_job(
    "interval",
    minutes=AUTO_UPDATE_MINUTES,
    id="nte_auto_update_scorers",
    name="NTEUID 评分包自动更新",
    max_instances=1,
    coalesce=True,
)
async def auto_update_scorers() -> None:
    for result in await update_scorer_packs():
        if not result.success:
            logger.warning(f"[NTE评分] 定时更新失败: {result.name}: {result.message}")
