import aiohttp
import re
from typing import Any, Awaitable, Callable
from http.cookies import SimpleCookie
from urllib.parse import urlencode

from multidict import CIMultiDictProxy
from astrbot.api import logger
from yarl import URL

BASE_URL = "https://scapi.rockstargames.com/profile/getprofile"
REFRESH_URL = "https://socialclub.rockstargames.com/connect/refreshaccess"
HQSHI_BASE_URL = "https://api.hqshi.cn"
_AUTHORIZATION = ""
_REFRESH_COOKIES: dict[str, str] = {}
_JWT_RE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_REFRESH_PERSIST_CALLBACK: Callable[[str, dict[str, str]], Awaitable[None]] | None = None
_PLUGIN_LOG_ENABLED = True

_REFRESH_COOKIE_KEYS = (
    "TS01008f56",
    "TS011be943",
    "TS01347d69",
    "RockStarWebSessionId",
    "prod",
)

PRIVATE_STATS_ENDPOINTS = (
)


def set_plugin_log_enabled(enabled: bool) -> None:
    """Enable or disable plugin informational logs."""
    global _PLUGIN_LOG_ENABLED
    _PLUGIN_LOG_ENABLED = bool(enabled)


def is_plugin_log_enabled() -> bool:
    """Return whether informational plugin logs are enabled."""
    return _PLUGIN_LOG_ENABLED


def _log_info(msg: str, *args: Any) -> None:
    if _PLUGIN_LOG_ENABLED:
        logger.info(msg, *args)


async def _hqshi_get(
    endpoint: str,
    params: dict[str, Any],
    timeout_seconds: int = 12,
) -> dict[str, Any]:
    """Send GET request to HQSHI API and return parsed JSON envelope."""
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "AstrBot-GTA-Plugin/1.0",
    }
    url = f"{HQSHI_BASE_URL}{endpoint}"

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params=params, headers=headers) as response:
            text = await response.text()
            if response.status >= 400:
                raise ValueError(f"HQSHI API HTTP {response.status}: {text[:160]}")
            try:
                data = await response.json(content_type=None)
            except Exception as e:
                raise ValueError(f"HQSHI API returned non-JSON response: {text[:160]}") from e

    if not isinstance(data, dict):
        raise ValueError("HQSHI API returned invalid JSON envelope.")

    code = data.get("code")
    if code != 200:
        message = data.get("message") or "HQSHI API request failed"
        raise ValueError(f"HQSHI API code={code}, message={message}")

    return data


async def get_hqshi_recent_text(
    nickname: str,
    expire: int = 7200,
    platform: str = "default",
    timeout_seconds: int = 12,
) -> str:
    """Get latest valid GTA career text from HQSHI by nickname."""
    data = await _hqshi_get(
        "/api/recent",
        {
            "nickname": nickname,
            "expire": expire,
            "platform": platform,
            "type": "text",
        },
        timeout_seconds=timeout_seconds,
    )
    body = data.get("body")
    if isinstance(body, str) and body.strip():
        return body.strip()

    post_message = await trigger_hqshi_snapshot_update(
        nickname=nickname,
        platform="pcalt",
        timeout_seconds=timeout_seconds,
    )
    raise ValueError(f"HQSHI recent API returned empty body. {post_message}")


async def trigger_hqshi_snapshot_update(
    nickname: str,
    platform: str = "pcalt",
    timeout_seconds: int = 12,
) -> str:
    """Request HQSHI to generate a fresh snapshot for the given nickname."""
    try:
        data = await _hqshi_get(
            "/api/post",
            {
                "nickname": nickname,
                "platform": platform,
            },
            timeout_seconds=timeout_seconds,
        )
    except Exception as e:
        logger.warning(
            "[gta_online_helper] HQSHI snapshot update request failed, nickname=%s, platform=%s, error=%s",
            nickname,
            platform,
            e,
        )
        return f"触发 /api/post 失败: {e}"

    message = str(data.get("message") or "请求成功").strip()
    return f"已触发 /api/post 生成数据(platform={platform})，服务端响应: {message}"


async def get_hqshi_status(
    nickname: str,
    limit: int = 20,
    timeout_seconds: int = 12,
) -> dict[str, Any]:
    """Get HQSHI player status record payload by nickname."""
    data = await _hqshi_get(
        "/api/status",
        {
            "nickname": nickname,
            "limit": limit,
        },
        timeout_seconds=timeout_seconds,
    )
    body = data.get("body")
    if not isinstance(body, dict):
        raise ValueError("HQSHI status API returned invalid body.")
    return body


def set_authorization(authorization: str) -> None:
    """Update the Authorization header value used by API calls."""
    global _AUTHORIZATION
    token = authorization.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    _AUTHORIZATION = _sanitize_bearer_token(token)


def get_authorization() -> str:
    """Get current Authorization header value."""
    return _AUTHORIZATION


def _sanitize_bearer_token(token: str) -> str:
    """Extract a valid JWT token and drop trailing noise like message ids."""
    raw = token.strip().strip('"').strip("'")
    match = _JWT_RE.search(raw)
    if match:
        return match.group(0)
    # Fallback: if it is not JWT-shaped, at least remove whitespace suffixes.
    return raw.split()[0] if raw else ""


def get_authorization_header() -> str:
    """Build HTTP Authorization header value in `Bearer <token>` format."""
    token = get_authorization().strip()
    if not token:
        return ""
    return f"Bearer {token}"


def set_refresh_persist_callback(
    callback: Callable[[str, dict[str, str]], Awaitable[None]] | None,
) -> None:
    """Set callback that persists refreshed auth/cookies after auto refresh."""
    global _REFRESH_PERSIST_CALLBACK
    _REFRESH_PERSIST_CALLBACK = callback


def set_refresh_cookies(cookies: dict[str, str]) -> None:
    """Set cookies used for refreshing BearerToken."""
    global _REFRESH_COOKIES
    normalized = {k: str(v).strip() for k, v in cookies.items() if str(v).strip()}
    _REFRESH_COOKIES = normalized

    missing = [key for key in _REFRESH_COOKIE_KEYS if key not in normalized]
    _log_info(
        "[gta_online_helper] set_refresh_cookies called, keys=%s, missing_required=%s",
        sorted(normalized.keys()),
        missing,
    )


def _mask_token(token: str, keep: int = 8) -> str:
    if not token:
        return "<empty>"
    if len(token) <= keep:
        return "*" * len(token)
    return f"{token[:keep]}...({len(token)})"


def get_refresh_cookies() -> dict[str, str]:
    """Get cached cookies for refresh workflow."""
    return dict(_REFRESH_COOKIES)


def parse_cookie_string(cookie_string: str) -> dict[str, str]:
    """Parse a Cookie header string into key-value pairs."""
    result: dict[str, str] = {}
    for item in cookie_string.split(";"):
        part = item.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            result[key] = value
    return result


def update_from_cookie_string(cookie_string: str) -> dict[str, str]:
    """Update local authorization/cookie cache from cookie string."""
    parsed = parse_cookie_string(cookie_string)
    if "BearerToken" in parsed:
        parsed["BearerToken"] = _sanitize_bearer_token(parsed["BearerToken"])
        set_authorization(parsed["BearerToken"])

    if parsed:
        merged = get_refresh_cookies()
        merged.update(parsed)
        set_refresh_cookies(merged)
    return parsed



def _parse_set_cookie_headers(set_cookie_values: CIMultiDictProxy[str]) -> dict[str, str]:
    """Parse Set-Cookie headers into a simple name->value map."""
    parsed: dict[str, str] = {}
    for header, value in set_cookie_values.items():
        if header.lower() in ("set-cookie"):
            _log_info("Parsing Set-Cookie header: %s", value)
            jar = SimpleCookie()
            try:
                jar.load(value)
            except Exception:
                continue
            for key, morsel in jar.items():
                value = morsel.value.strip()
                if value:
                    _log_info("parsed cookie: %s=%s", key, value)
                    parsed[key] = value
                    
    return parsed


def _extract_bearer_from_response(cookies: dict[str,str]) -> str:
    """Extract BearerToken from response headers/cookies/history."""
    # 1) Direct Set-Cookie headers on final response
    token = _sanitize_bearer_token(cookies.get("BearerToken", ""))
    if token:
        return token
    return ""


async def refresh_authorization(timeout_seconds: int = 10) -> str:
    """Refresh BearerToken with cached cookies and return new token."""
    current_token = get_authorization().strip()
    _log_info(
        "[gta_online_helper] refresh_authorization start, timeout=%s, token=%s",
        timeout_seconds,
        _mask_token(current_token),
    )
    if not current_token:
        logger.warning("[gta_online_helper] refresh_authorization aborted: empty Authorization")
        raise ValueError("Authorization header is empty. Cannot refresh token.")

    missing = [key for key in _REFRESH_COOKIE_KEYS if not _REFRESH_COOKIES.get(key)]
    if missing:
        logger.warning(
            "[gta_online_helper] refresh_authorization aborted: missing refresh cookies %s",
            missing,
        )
        raise ValueError(f"Refresh cookies are incomplete, missing: {', '.join(missing)}")

    cookie_data = get_refresh_cookies()
    cookie_data["BearerToken"] = current_token
    cookie_data.setdefault("AutoLoginCheck", "1")
    cookie_header = ";".join(f"{k}={v}" for k, v in cookie_data.items())
    _log_info(
        "[gta_online_helper] refresh_authorization request prepared, cookie_keys=%s",
        sorted(cookie_data.keys()),
    )
    
    _log_info("refresh with cookie: %s", cookie_header)

    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "Referer": "https://socialclub.rockstargames.com/",
        "Origin": "https://socialclub.rockstargames.com",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "same-origin",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
        "Cookie": cookie_header,
    }

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(
        "https://socialclub.rockstargames.com",
        timeout=timeout,
    ) as session:
        async with session.post(
            "/connect/refreshaccess",
            headers=headers,
            data={"accessToken": current_token},
        ) as response:
            response_text = await response.text()
            response_cookie_map = _parse_set_cookie_headers(response.headers)
            _log_info(
                "[gta_online_helper] refresh response: status=%s, content_type=%s, redirect_count=%s, body_len=%s, body_preview=%s",
                response.status,
                response.headers.get("Content-Type"),
                len(response.history),
                len(response_text),
                response_text[:200],
            )
            new_token = _extract_bearer_from_response(response_cookie_map)

            if not new_token:
                jar_cookies = session.cookie_jar.filter_cookies(
                    URL("https://socialclub.rockstargames.com")
                )
                jar_token = jar_cookies.get("BearerToken")
                if jar_token and jar_token.value:
                    new_token = jar_token.value.strip()
            _log_info(
                "[gta_online_helper] refresh bearer extracted=%s",
                bool(new_token),
            )

            if response_cookie_map:
                merged = get_refresh_cookies()
                merged.update(response_cookie_map)
                set_refresh_cookies(merged)
                _log_info(
                    "[gta_online_helper] refresh cached all response cookies, keys=%s",
                    sorted(response_cookie_map.keys()),
                )

            if response.status >= 400 and not new_token:
                response.raise_for_status()

    if not new_token:
        logger.error(
            "[gta_online_helper] refresh_authorization failed: no BearerToken in Set-Cookie"
        )
        raise ValueError("Refresh succeeded but no BearerToken found in Set-Cookie.")

    set_authorization(new_token)
    cookie_data["BearerToken"] = new_token
    set_refresh_cookies(cookie_data)
    if _REFRESH_PERSIST_CALLBACK is not None:
        try:
            await _REFRESH_PERSIST_CALLBACK(get_authorization(), get_refresh_cookies())
            _log_info("[gta_online_helper] persisted refreshed auth/cookies via callback")
        except Exception as e:
            logger.warning("[gta_online_helper] persist callback failed: %s", e)
    _log_info(
        "[gta_online_helper] refresh_authorization success, new_token=%s",
        _mask_token(new_token),
    )
    return new_token


async def get_profile(
    nickname: str,
    authorization: str | None = None,
    max_friends: int = 0,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    """Fetch GTA Online profile information by nickname."""
    auth_value = (authorization or _AUTHORIZATION).strip()
    if auth_value.lower().startswith("bearer "):
        auth_value = auth_value[7:].strip()
    if not auth_value:
        raise ValueError("Authorization header is empty. Please set it before calling get_profile.")

    params = {
        "nickname": nickname,
        "maxFriends": max_friends,
    }
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://socialclub.rockstargames.com/",
        "Origin": "https://socialclub.rockstargames.com",
        "Authorization": f"Bearer {auth_value}",
    }
    url = f"{BASE_URL}?{urlencode(params)}"

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as response:
            if response.status != 401:
                response.raise_for_status()
                return await response.json()

    # Token may be expired; refresh once and retry.
    await refresh_authorization(timeout_seconds=timeout_seconds)
    retry_headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://socialclub.rockstargames.com/",
        "Origin": "https://socialclub.rockstargames.com",
        "Authorization": get_authorization_header(),
    }

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=retry_headers) as response:
            response.raise_for_status()
            return await response.json()


async def name_to_rid(name: str) -> int:
    """Convert player name to Rockstar ID (rid), preferring HQSHI status API first."""
    # Prefer HQSHI status because it can resolve rid without Rockstar Authorization.
    try:
        status = await get_hqshi_status(name, limit=1)
        rid_value = status.get("rockstar_id")
        if isinstance(rid_value, int):
            return rid_value
        if isinstance(rid_value, str) and rid_value.strip():
            return int(rid_value.strip())
    except Exception as e:
        _log_info("[gta_online_helper] HQSHI rid lookup failed for %s: %s", name, e)

    data = await get_profile(name)

    accounts = data.get("accounts")
    if not isinstance(accounts, list):
        raise ValueError("Invalid response: 'accounts' is missing or not a list.")

    target = name.strip().lower()
    for account in accounts:
        if not isinstance(account, dict):
            continue

        rockstar_account = account.get("rockstarAccount")
        if not isinstance(rockstar_account, dict):
            continue

        account_name = str(rockstar_account.get("name", "")).strip().lower()
        display_name = str(rockstar_account.get("displayName", "")).strip().lower()
        if account_name != target and display_name != target:
            continue

        rid = rockstar_account.get("rockstarId")
        if isinstance(rid, int):
            return rid
        if isinstance(rid, str):
            try:
                return int(rid)
            except ValueError as e:
                raise ValueError("Invalid response: 'rockstarId' is missing or invalid.") from e

        raise ValueError("Invalid response: 'rockstarId' is missing or invalid.")

    raise ValueError(f"Player '{name}' not found in get_profile response.")
