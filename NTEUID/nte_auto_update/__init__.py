from gsuid_core.aps import scheduler
from gsuid_core.logger import logger

from ..utils.restart import restart_after_update
from ..nte_config.nte_config import NTEConfig
from ..nte_scorer.scorer_service import update_scorer_packs
from ..utils.resource.git_resource import update_resources

AUTO_UPDATE_MINUTES: int = NTEConfig.get_config("NTEAutoUpdateMinutes").data


if AUTO_UPDATE_MINUTES <= 0:
    AUTO_UPDATE_MINUTES = 60


@scheduler.scheduled_job(
    "interval",
    minutes=AUTO_UPDATE_MINUTES,
    id="nte_auto_update",
    name="NTEUID 资源与评分包自动更新",
    max_instances=1,
    coalesce=True,
)
async def auto_update() -> None:
    resource_result = await update_resources(silent=True)
    scorer_results = await update_scorer_packs()
    for result in scorer_results:
        if not result.success:
            logger.warning(f"[NTE评分] 定时更新失败: {result.name}: {result.message}")
    if resource_result["changed"] or any(result.changed for result in scorer_results):
        await restart_after_update()
