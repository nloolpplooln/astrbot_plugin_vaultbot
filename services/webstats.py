"""socialclub 网页数据层：StatsAjax（GTA Online 深度生涯数据）。

与 scapi 不同，这些是老 Social Club 网页的 Ajax 端点，返回 HTML（数据在表格里）。
认证方式（已验证）：**完整会话 Cookie（含刷新后的 BearerToken）+ X-Requested-With 头**。
- 缺 X-Requested-With → 302 跳登录；带上则直接 200 返回数据。
- 会话失效（302 到 signin / 401）→ 刷新 token 后重试。

深度数据（总收入/在线时长/K-D/技能/犯罪/载具/战斗/武器，共 200+ 项）都在这里，
是 scapi getprofile 不提供的部分。
"""
from __future__ import annotations

from typing import Dict

import httpx

from . import auth, parser

SC_BASE = "https://socialclub.rockstargames.com"
STATS_PATH = "/games/gtav/statsajax"
OVERVIEW_PATH = "/games/gtav/career/overviewAjax"
AWARDS_PATH = "/games/gtav/career/AwardsAjax"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


class WebStatsError(RuntimeError):
    pass


def _headers(nickname: str) -> Dict[str, str]:
    cookies = auth.get_refresh_cookies()
    if not cookies.get("BearerToken"):
        raise auth.AuthError("未注入凭证或缺少 BearerToken，请先 setck 注入完整 Cookie。")
    cookie_hdr = ";".join(f"{k}={v}" for k, v in cookies.items())
    return {
        "User-Agent": _UA,
        "Cookie": cookie_hdr,
        "X-Requested-With": "XMLHttpRequest",  # 关键：否则 302 跳登录
        "Referer": f"{SC_BASE}/games/gtav/career/stats?nickname={nickname}",
        "Accept": "text/html, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _get(nickname: str, slot: str, timeout: int, path: str = STATS_PATH, extra_params: dict = None) -> httpx.Response:
    params = {"nickname": nickname, "slot": slot}
    if extra_params:
        params.update(extra_params)
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        return client.get(SC_BASE + path, params=params, headers=_headers(nickname))


def _is_auth_fail(resp: httpx.Response) -> bool:
    if resp.status_code in (401, 403):
        return True
    if resp.status_code in (301, 302, 303, 307, 308):
        return "signin" in resp.headers.get("location", "").lower() or "authcheck" in resp.headers.get("location", "").lower()
    return False


def fetch_gtav_stats(nickname: str, slot: str = "Freemode", timeout: int = 60) -> Dict[str, Dict[str, str]]:
    """抓取并解析某玩家的 GTA Online 深度统计，返回 {分类: {项名: 值}}。

    会话失效时自动刷新 token 重试一次。
    """
    resp = _get(nickname, slot, timeout)
    if _is_auth_fail(resp):
        auth.refresh_authorization(timeout=timeout)  # 刷新后 get_refresh_cookies 已更新
        resp = _get(nickname, slot, timeout)
    if resp.status_code >= 400 or _is_auth_fail(resp):
        raise WebStatsError(f"StatsAjax 失败 HTTP {resp.status_code}（会话可能已失效，请重新注入 Cookie）")
    stats = parser.parse_gtav_stats(resp.text)
    if not stats:
        raise WebStatsError("StatsAjax 返回但未解析到数据（玩家隐藏或页面结构变化）。")
    return stats


def fetch_gtav_overview(nickname: str, slot: str = "Freemode", timeout: int = 60) -> dict:
    """抓取 overviewAjax 页面，提取等级/RP/现金/银行存款。

    R* 返回的 HTML 中含 .rankHex (等级)、.rankXP (RP)、.cash 和 .bank。
    """
    resp = _get(nickname, slot, timeout, path=OVERVIEW_PATH)
    if _is_auth_fail(resp):
        auth.refresh_authorization(timeout=timeout)
        resp = _get(nickname, slot, timeout, path=OVERVIEW_PATH)
    if resp.status_code >= 400 or _is_auth_fail(resp):
        raise WebStatsError(f"overviewAjax 失败 HTTP {resp.status_code}")
    ov = parser.parse_gtav_overview(resp.text)
    if not ov:
        raise WebStatsError("overviewAjax 返回但未解析到数据。")
    return ov


def fetch_gtav_awards(nickname: str, category: str = "", slot: str = "Freemode", timeout: int = 60) -> dict:
    """抓取 AwardsAjax 奖章数据。category 为空则返回全部分类，否则只返回指定分类的单项。"""
    params = {"nickname": nickname, "slot": slot}
    if category:
        params["category"] = category.lower()
    resp = _get(nickname, slot, timeout, path=AWARDS_PATH, extra_params=params if category else {})
    if _is_auth_fail(resp):
        auth.refresh_authorization(timeout=timeout)
        resp = _get(nickname, slot, timeout, path=AWARDS_PATH, extra_params=params if category else {})
    if resp.status_code >= 400 or _is_auth_fail(resp):
        raise WebStatsError(f"AwardsAjax 失败 HTTP {resp.status_code}")
    awards = parser.parse_gtav_awards(resp.text)
    if not awards:
        raise WebStatsError("AwardsAjax 返回但未解析到数据。")
    return awards
