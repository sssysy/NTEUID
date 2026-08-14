from __future__ import annotations

from datetime import datetime

LAOHU_APP_ID: int = 10550
LAOHU_APP_KEY: str = "89155cc4e8634ec5b1b6364013b23e3e"

# tajiduo access_token 本地缓存的 TTL —— 真实 TTL 未知但 HAR 证据 20+min 不 refresh 依旧能用，
# 1 小时是保守区间；若将来发现 access_token 在窗口内已被服务端作废，降到 30 分钟或加 401/402 重试
ACCESS_TOKEN_TTL_SECONDS: int = 3600

# 游戏 ID（塔吉多用户中心为每个游戏分配的 id）
GAME_ID_YIHUAN: str = "1289"
GAME_ID_HUANTA: str = "1256"

# 塔吉多站内社区 ID
TAJIDUO_COMMUNITY_HUANTA: str = "1"
TAJIDUO_COMMUNITY_YIHUAN: str = "2"

# 异环 App 签到使用的社区（和"社区任务所在社区"是同一个，但语义独立）
TAJIDUO_SIGNIN_COMMUNITY_ID: str = TAJIDUO_COMMUNITY_YIHUAN

# 异环社区任务覆盖的社区 ID
YIHUAN_TASK_COMMUNITY_IDS: tuple[str, ...] = (TAJIDUO_COMMUNITY_YIHUAN,)

# 异环公告（匿名社区接口）目标社区 / 栏目
NOTICE_COMMUNITY_NAME: str = "异环"
NOTICE_COLUMN_NAME: str = "官方资讯"

# 金币任务 taskKey（来自 /apihub/api/getUserTasks）
TASK_KEY_BROWSE_POST: str = "browse_post_c"
TASK_KEY_LIKE_POST: str = "like_post_c"
TASK_KEY_SHARE: str = "share"

# /bbs/api/post/share 的 platform 字段
SHARE_PLATFORM_WX_SESSION: str = "wx_session"
SHARE_PLATFORM_WX_TIMELINE: str = "wx_timeline"
SHARE_PLATFORM_QQ: str = "qq"
SHARE_PLATFORM_QZONE: str = "qzone"

# 命令里用于匹配角色名 / 角色 id 的通用模式
COMMAND_NAME_PATTERN: str = (
    r"[\u4e00-\u9fa5a-zA-Z0-9"
    r"\U0001F300-\U0001FAFF\U00002600-\U000027BF"
    r"\U00002B00-\U00002BFF\U00003200-\U000032FF"
    r"-—·()（）]{1,20}"
)

# TapTap 战绩 / 抽卡海报接口（匿名 GET，用于查 TapTap 用户分享出来的异环抽卡数据）
TAPTAP_BASE_URL: str = "https://www.taptap.cn"
TAPTAP_GAME_RECORD_PATH: str = "/webapiv2/game-record/v1"
TAPTAP_APP_ID_YIHUAN: str = "714119"
# 任意 UUID，TapTap 不校验内容只校验有值；写死即可
TAPTAP_DEFAULT_DEVICE_UID: str = "ff836e35-ed0e-4140-915b-5ef00f39b35b"
TAPTAP_USER_AGENT: str = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
)
# 用户在 TapTap 战绩页未绑游戏角色时，引导文案给的"长啥样"参考链接
TAPTAP_BIND_GUIDE_URL: str = "https://www.taptap.cn/poster/NIYXlFahOXHR"
# TapTap 个人资料页，用于获取 user_id
TAPTAP_PERSONAL_INFO_URL: str = "https://accounts.taptap.cn/personal-info"

# 小黑盒
XIAOHEIHE_BASE_URL: str = "https://api.xiaoheihe.cn"
XIAOHEIHE_WEB_URL: str = "https://www.xiaoheihe.cn/"
XIAOHEIHE_BIND_GUIDE_URL: str = "https://www.xiaoheihe.cn/game/nte/lottery_analysis"

WANMEI_ID_BASE_URL: str = "https://id.wanmei.com"
WANMEI_KF_BASE_URL: str = "https://kf.wanmei.com"
WANMEI_CAPTCHA_BASE_URL: str = "https://captchas.wanmei.com"
WANMEI_KF_GAME_ID_YIHUAN: str = "191"
WANMEI_QUERY_CAPTCHA_APP_ID: str = "20003"
WANMEI_LOGIN_RETURN_URL: str = f"{WANMEI_KF_BASE_URL}/selfItemFlowQuery?gameId={WANMEI_KF_GAME_ID_YIHUAN}"
WANMEI_USER_AGENT: str = (
    "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)

WANMEI_SCRATCH_TYPE_ID: str = "29"
WANMEI_SCRATCH_ITEM_TYPE: str = "13"
WANMEI_SCRATCH_ITEM_SUB_TYPE: str = "1"
WANMEI_SCRATCH_ITEM_ID: str = "110"
WANMEI_SCRATCH_FULL_REFRESH_START_AT: datetime = datetime(2026, 7, 1)
WANMEI_SCRATCH_HISTORY_DAYS: int = 60
WANMEI_SCRATCH_REFRESH_DAYS: int = 7
WANMEI_SCRATCH_PAGE_SIZE: int = 1000
WANMEI_SCRATCH_RANK_LIMIT: int = 20
