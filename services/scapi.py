"""scapi 数据层：调用 R星 scapi.rockstargames.com 拿玩家原始数据。

要点：
- 只带 `Authorization: Bearer` 头，**绝不带 Cookie**（避开 scapi 的 CSRF 双提交校验）。
- 遇 401 自动 refresh 一次并重试（懒刷新）。
- scapi_get() 为通用入口，方便随数据探明逐步加端点（生涯统计、车库、好友等）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from . import auth

SCAPI_BASE = "https://scapi.rockstargames.com"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


class ScapiError(RuntimeError):
    pass


def _headers(token: str) -> Dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": _UA,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://socialclub.rockstargames.com/",
        "Origin": "https://socialclub.rockstargames.com",
        "Authorization": f"Bearer {token}",
    }


def scapi_get(path: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10) -> Dict[str, Any]:
    """GET scapi 端点，返回 JSON。401 时自动刷新一次并重试。"""
    token = auth.get_authorization().strip()
    if not token:
        raise auth.AuthError("authorization 为空，请先注入 Cookie（见 CLI/接口）。")

    url = SCAPI_BASE + path
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url, params=params or {}, headers=_headers(token))
        if resp.status_code == 401:
            # token 过期 → 刷新后重试一次
            token = auth.refresh_authorization(timeout=timeout)
            resp = client.get(url, params=params or {}, headers=_headers(token))
        if resp.status_code >= 400:
            detail = resp.text[:300]
            if "6000.41" in detail or (resp.status_code == 404 and "6000" in detail):
                raise ScapiError("玩家不存在")
            raise ScapiError(f"scapi {path} -> HTTP {resp.status_code}: {detail}")
        try:
            return resp.json()
        except ValueError as e:
            raise ScapiError(f"scapi {path} 返回非 JSON：{resp.text[:200]}") from e


def get_profile(nickname: str, max_friends: int = 0, timeout: int = 60) -> Dict[str, Any]:
    """按昵称取玩家资料（含 accounts / rockstarAccount）。"""
    return scapi_get("/profile/getprofile", {"nickname": nickname, "maxFriends": max_friends}, timeout)


def name_to_rid(nickname: str, timeout: int = 10) -> int:
    """从 getprofile 结果解析 Rockstar ID（rid）。"""
    data = get_profile(nickname, timeout=timeout)
    accounts = data.get("accounts")
    if not isinstance(accounts, list):
        raise ScapiError("响应缺少 accounts 列表。")
    target = nickname.strip().lower()
    for acc in accounts:
        ra = acc.get("rockstarAccount") if isinstance(acc, dict) else None
        if not isinstance(ra, dict):
            continue
        name = str(ra.get("name", "")).strip().lower()
        disp = str(ra.get("displayName", "")).strip().lower()
        if target in (name, disp):
            rid = ra.get("rockstarId")
            if isinstance(rid, int):
                return rid
            if isinstance(rid, str) and rid.strip().isdigit():
                return int(rid.strip())
            raise ScapiError("rockstarId 缺失或非法。")
    raise ScapiError(f"未在 getprofile 结果中找到玩家 '{nickname}'。")
