"""认证层：R星 BearerToken 的持久化、注入与自动刷新。

机制（依据 astrbot_plugin_gta_online_helper 已验证的实现）：
- token 只活 ~5 分钟，用 `POST socialclub.rockstargames.com/connect/refreshaccess` 续期；
  刷新需带「当前 BearerToken + 一组会话 Cookie(TS*/RockStarWebSessionId/prod)」，
  新 token 从响应的 Set-Cookie 里取，并把响应返回的所有 cookie 回写（滚动续期）。
- 凭证持久化到 SQLite 的 credential 表：authorization(token) 与 refresh_cookies(dict)。

注意：调 scapi 数据端点时只带 Authorization 头、不带 Cookie（避开 CSRF），见 scapi.py。
"""
from __future__ import annotations

import base64
import json
import re
import time
from typing import Dict, Optional

from curl_cffi import requests as cffi_requests

from . import models


def token_ttl_seconds() -> int:
    """返回当前 BearerToken 还剩多少秒有效。不存在则返回 0。"""
    tok = _authorization.strip()
    if not tok:
        return 0
    try:
        # JWT payload 在第二个 . 之后
        payload_b64 = tok.split(".")[1]
        # 补齐 padding
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp", 0)
        return max(0, exp - int(time.time()))
    except Exception:
        return 0

SC_BASE = "https://socialclub.rockstargames.com"
REFRESH_PATH = "/connect/refreshaccess"

# 刷新必需的会话 Cookie（缺任一则无法续期）
REQUIRED_COOKIE_KEYS = (
    "TS01008f56",
    "TS011be943",
    "TS01347d69",
    "RockStarWebSessionId",
    "prod",
)

_JWT_RE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# 内存态（进程内缓存，写操作同时落 SQLite）
_authorization: str = ""
_refresh_cookies: Dict[str, str] = {}


class AuthError(RuntimeError):
    """凭证缺失或刷新失败。"""


def sanitize_token(token: str) -> str:
    """从字符串中抠出 JWT，去掉引号与尾部噪声（如 [MSG_ID:xxx]）。"""
    raw = (token or "").strip().strip('"').strip("'")
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    m = _JWT_RE.search(raw)
    if m:
        return m.group(0)
    # 没匹配到完整 JWT：只保留合法字符，杜绝非 ASCII 导致后续 header 编码崩
    return re.sub(r"[^A-Za-z0-9._-]", "", raw)


def parse_cookie_string(cookie_string: str) -> Dict[str, str]:
    """把 `k1=v1; k2=v2` 的 Cookie 串解析为字典。"""
    out: Dict[str, str] = {}
    for item in (cookie_string or "").split(";"):
        part = item.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k and v:
            out[k] = v
    return out


def load_from_storage() -> None:
    """启动时从 SQLite 恢复内存态。"""
    global _authorization, _refresh_cookies
    auth = models.load_credential("authorization")
    if isinstance(auth, str) and auth.strip():
        _authorization = sanitize_token(auth)
    cookies = models.load_credential("refresh_cookies")
    if isinstance(cookies, dict):
        _refresh_cookies = {str(k): _ascii_safe(str(v)) for k, v in cookies.items() if str(v).strip()}


def set_authorization(token: str) -> None:
    global _authorization
    _authorization = sanitize_token(token)
    models.save_credential("authorization", _authorization)


def set_refresh_cookies(cookies: Dict[str, str]) -> None:
    global _refresh_cookies
    _refresh_cookies = {str(k): _ascii_safe(str(v).strip()) for k, v in cookies.items() if str(v).strip()}
    models.save_credential("refresh_cookies", _refresh_cookies)


def _ascii_safe(s: str) -> str:
    """剔除字符串中的非 ASCII 字符，HTTP Cookie 头只支持 ASCII。"""
    return s.encode("ascii", errors="replace").decode("ascii").replace("?", "")


def get_authorization() -> str:
    return _authorization


def get_refresh_cookies() -> Dict[str, str]:
    """返回当前缓存的刷新/会话 Cookie（含刷新后更新的 BearerToken 与 TS*）。"""
    return dict(_refresh_cookies)


def missing_refresh_keys() -> list:
    return [k for k in REQUIRED_COOKIE_KEYS if not _refresh_cookies.get(k)]


def update_from_cookie_string(cookie_string: str) -> Dict[str, str]:
    """从完整 Cookie 串注入凭证：提取 BearerToken 作 authorization，整串存为 refresh_cookies。"""
    parsed = parse_cookie_string(cookie_string)
    if "BearerToken" in parsed:
        parsed["BearerToken"] = sanitize_token(parsed["BearerToken"])
        set_authorization(parsed["BearerToken"])
    if parsed:
        merged = dict(_refresh_cookies)
        merged.update(parsed)
        set_refresh_cookies(merged)
    return parsed


JOBS_PAGE = "/jobs?dateRangeCreated=any&filter=me&sort=likes&title=gtav"


def _refresh_session_cookies(timeout: int = 15) -> int:
    """访问 jobs 页面滚动续期 TS* / RockStarWebSessionId。
    不返回 BearerToken（页面不发 token），只续 CK。
    返回收集到的 Cookie 数。
    """
    if missing_refresh_keys():
        return 0
    cookie_data = {k: _ascii_safe(v) for k, v in _refresh_cookies.items()}
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = cffi_requests.get(
            SC_BASE + JOBS_PAGE, cookies=cookie_data,
            headers=headers, impersonate="chrome120", timeout=timeout
        )
        all_cookies: Dict[str, str] = {}
        for c in resp.cookies.jar:
            if c.value:
                all_cookies[c.name] = c.value
        if all_cookies:
            merged = dict(_refresh_cookies)
            merged.update(all_cookies)
            set_refresh_cookies(merged)
        return len(all_cookies)
    except Exception:
        return 0


def refresh_authorization(timeout: int = 10) -> str:
    """续期 BearerToken（refreshaccess）+ 续期会话 CK（页面访问）。"""
    current = _authorization.strip()
    missing = missing_refresh_keys()
    if missing:
        raise AuthError(f"CK 不完整，缺少：{', '.join(missing)}。请重新注入 Cookie。")
    if not current:
        raise AuthError("无 token。请先注入 Cookie。")

    ttl = token_ttl_seconds()
    cookie_data = {k: _ascii_safe(v) for k, v in _refresh_cookies.items()}
    cookie_data["BearerToken"] = current
    cookie_data.setdefault("AutoLoginCheck", "1")

    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "Referer": SC_BASE + "/",
        "Origin": SC_BASE,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "same-origin",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": _UA,
    }

    resp = cffi_requests.post(
        SC_BASE + REFRESH_PATH, cookies=cookie_data,
        data={"accessToken": current}, headers=headers,
        impersonate="chrome120", timeout=timeout
    )
    if resp.status_code in (401, 403):
        raise AuthError("token 已过期，refreshaccess 需要有效 token。请重新注入 Cookie。")
    if resp.status_code == 429:
        raise AuthError("HTTP 429 限速，暂停刷新")
    if resp.status_code >= 400:
        raise AuthError(f"refreshaccess HTTP {resp.status_code}: {resp.text[:160]}")

    # 提取新 token
    jar = {c.name: c.value for c in resp.cookies.jar}
    new_token = sanitize_token(jar.get("BearerToken", ""))
    if not new_token:
        new_token = sanitize_token(resp.cookies.get("BearerToken", "") or "")
    if jar:
        merged = dict(_refresh_cookies)
        merged.update({k: v for k, v in jar.items() if v})
        set_refresh_cookies(merged)
    if not new_token:
        raise AuthError("refreshaccess 未返回新 token。CK 可能已过期。")

    set_authorization(new_token)

    # 同时续 CK（访问 jobs 页面）
    _refresh_session_cookies(timeout)

    return new_token

# refresh_session_cookies 改为别名
refresh_session_cookies = _refresh_session_cookies


# 用于滚动续期会话 Cookie 的页面（需要完整登录态，响应更可能携带 TS* Set-Cookie）
SESSION_REFRESH_URLS = [
    "/jobs?dateRangeCreated=any&filter=me&sort=likes&title=gtav",
    "/",
]


def refresh_session_cookies(timeout: int = 15) -> int:
    """访问需要登录态的页面，利用滑动过期策略续期 TS* 等会话 Cookie。

    用现有 Cookie 访问页面时，R* 通常会在响应 Set-Cookie 中重置过期时间。
    依次访问多个页面，收集所有 Set-Cookie 并回写持久化。

    返回收集到的 Cookie 数量。
    """
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    cookie_data = {k: _ascii_safe(v) for k, v in _refresh_cookies.items()}
    if _authorization:
        cookie_data["BearerToken"] = _authorization

    all_jar: Dict[str, str] = {}
    for path in SESSION_REFRESH_URLS:
        try:
            resp = cffi_requests.get(
                SC_BASE + path, cookies=cookie_data,
                headers=headers, impersonate="chrome120", timeout=timeout
            )
            for c in resp.cookies.jar:
                if c.value:
                    all_jar[c.name] = c.value
            if resp.cookies:
                for name, value in resp.cookies.items():
                    if value:
                        all_jar[name] = value
        except Exception:
            continue

    if all_jar:
        merged = dict(_refresh_cookies)
        merged.update(all_jar)
        set_refresh_cookies(merged)

    return len(all_jar)
