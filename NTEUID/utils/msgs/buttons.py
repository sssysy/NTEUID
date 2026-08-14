from typing import Literal

from gsuid_core.message_models import Button

from ...nte_config.prefix import nte_prefix

ButtonRows = list[list[Button]]


def cmd_btn(text: str, cmd: str, *, style: Literal[0, 1] = 1) -> Button:
    return Button(text, f"{nte_prefix()}{cmd}", action=2, style=style)


def link_btn(text: str, url: str) -> Button:
    return Button(text, url, action=0)


def login_buttons() -> ButtonRows:
    return [[cmd_btn("登录", "登录")]]


def relogin_buttons() -> ButtonRows:
    return [[cmd_btn("刷新令牌", "刷新令牌"), cmd_btn("重新登录", "登录")]]


def login_link_buttons(url: str) -> ButtonRows:
    return [[link_btn("点击登录", url)]]


def binding_switch_buttons(tajiduo_count: int, wanmei_count: int) -> ButtonRows:
    buttons: list[Button] = []
    if tajiduo_count > 1:
        buttons.append(cmd_btn("切换塔吉多", "切换"))
    if wanmei_count > 1:
        buttons.append(cmd_btn("切换完美", "完美切换"))
    return [buttons] if buttons else []


def help_buttons() -> ButtonRows:
    return [
        [cmd_btn("登录", "登录"), cmd_btn("登出", "登出")],
        [cmd_btn("练度", "练度")],
    ]


def sign_buttons() -> ButtonRows:
    return [[cmd_btn("查询", "查询"), cmd_btn("签到日历", "签到日历")]]


def switched_buttons() -> ButtonRows:
    return [[cmd_btn("查询", "查询"), cmd_btn("查看", "查看")]]


def guide_buttons(char_name: str) -> ButtonRows:
    """攻略卡入口：图鉴/配队。char_name 传标准名。"""
    return [[cmd_btn("图鉴", f"{char_name}图鉴"), cmd_btn("配队", f"{char_name}配队")]]


def catalog_char_buttons(char_name: str) -> ButtonRows:
    """角色图鉴卡入口：攻略/配队（武器图鉴不挂）。char_name 传标准名。"""
    return [[cmd_btn("攻略", f"{char_name}攻略"), cmd_btn("配队", f"{char_name}配队")]]


def role_home_buttons() -> ButtonRows:
    return [[cmd_btn("刷新面板", "刷新面板"), cmd_btn("练度", "练度")]]


def char_detail_buttons(char_name: str) -> ButtonRows:
    """角色详情卡入口：bot排行/攻略/配队/图鉴。char_name 传标准名，命令才能命中。"""
    return [
        [cmd_btn(f"{char_name}bot排行", f"{char_name}bot排行")],
        [
            cmd_btn("攻略", f"{char_name}攻略"),
            cmd_btn("配队", f"{char_name}配队"),
            cmd_btn("图鉴", f"{char_name}图鉴"),
        ],
    ]


def refresh_changed_buttons(char_names: list[str]) -> ButtonRows:
    """变动角色（调用方已按品级/等级排序）做成详情入口，最多 4 个、每行 2 个；无变动则给『练度』入口。"""
    if not char_names:
        return [[cmd_btn("练度", "练度")]]
    rows: ButtonRows = []
    for i in range(0, min(len(char_names), 4), 2):
        rows.append([cmd_btn(name, f"{name}面板") for name in char_names[i : i + 2]])
    return rows
