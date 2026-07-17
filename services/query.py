"""查询层：整合 scapi + StatsAjax，封装缓存与快照，对外提供统一查询入口。

persist=False 时不写库（机器人服务端续期中继等场景），轻量查询仅返回数据。
"""
from __future__ import annotations

import dataclasses
import time
from typing import Any, Dict, Optional

from . import models, schemas
from .config import get_settings
from . import auth, scapi, webstats


def _dataclass_to_dict(obj):
    """Convert dataclass to dict, recursively."""
    if dataclasses.is_dataclass(obj):
        return {f.name: _dataclass_to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    elif isinstance(obj, list):
        return [_dataclass_to_dict(item) for item in obj]
    return obj


def _map_profile(data: Dict[str, Any]) -> schemas.PlayerProfile:
    accounts = data.get("accounts") or []
    ra = {}
    linked: list[schemas.LinkedAccount] = []
    crews: list[schemas.Crew] = []
    games: list[schemas.Game] = []
    if accounts:
        acc = accounts[0] if isinstance(accounts[0], dict) else {}
        ra = acc.get("rockstarAccount") or {}
        for la in acc.get("linkedAccounts") or []:
            linked.append(schemas.LinkedAccount(
                service=la.get("onlineService", ""),
                user_name=la.get("userName"),
                user_id=la.get("userId"),
            ))
        for c in acc.get("crews") or []:
            crews.append(schemas.Crew(
                id=c.get("crewId", 0),
                name=c.get("crewName"),
                tag=c.get("crewTag"),
                motto=c.get("crewMotto"),
                member_count=c.get("memberCount"),
                is_primary=c.get("isPrimary", False),
                created_at=c.get("createdAt"),
            ))
        for g in ra.get("gamesOwned") or []:
            games.append(schemas.Game(
                name=g.get("name", ""),
                platform=g.get("platform"),
                last_seen=g.get("lastSeen"),
            ))
    pc = ra.get("primaryClanId")
    profile = schemas.PlayerProfile(
        nickname=ra.get("name"),
        rockstar_id=ra.get("rockstarId"),
        avatar_url=ra.get("avatarUrl"),
        country_code=ra.get("countryCode"),
        friend_count=ra.get("friendCount"),
        primary_crew=next((c for c in crews if c.id == pc), None) if pc else None,
        crews=crews,
        games=games,
        linked_accounts=linked,
    )
    return profile


def query_player(nickname: str, persist: bool = True, timeout: int = 60) -> schemas.PlayerResult:
    """查一个玩家：取 scapi 基础资料 + overviewAjax 等级金钱 + StatsAjax 深度数据。

    - 先查缓存（persist=True 时），命中则在 TTL 内直接返回
    - 缓存过期/不存在 → 采集新鲜数据 → persist 时写快照
    """
    s = get_settings()
    result = schemas.PlayerResult()
    if persist:
        cached = _check_cache(nickname)
        if cached:
            return cached

    profile_raw = scapi.get_profile(nickname, timeout=timeout)
    stats = webstats.fetch_gtav_stats(nickname, timeout=timeout)
    # overviewAjax 失败不阻断（中英文页面差异可能导致解析失败）
    try:
        overview = webstats.fetch_gtav_overview(nickname, timeout=timeout)
    except Exception:
        overview = {}

    profile = _map_profile(profile_raw)
    result.profile = profile
    result.overview = schemas.OverviewData(**overview) if overview else None
    result.stats = schemas.PlayerStats(categories=stats)
    result.updated_at = int(time.time())

    if persist:
        models.save_snapshot(
            nickname=nickname,
            raw={"profile": profile_raw},
            parsed={"profile": _dataclass_to_dict(profile), "overview": overview, "stats": dict(stats)},
            source_url=f"scapi+overview+statsajax/{nickname}",
        )
    return result


def query_awards(nickname: str, category: str = "", persist: bool = True, timeout: int = 60) -> dict:
    """查询奖章：先查缓存，过期则从 R* 拉取。

    persist=False 跳过缓存读写，强制实时拉取。
    """
    if persist:
        cached = _check_awards_cache(nickname, category)
        if cached:
            return cached
    awards = webstats.fetch_gtav_awards(nickname, category=category, timeout=timeout)
    if persist:
        models.save_snapshot(
            nickname=f"{nickname}_awards",
            raw={},
            parsed={"awards": awards, "category": category},
            source_url=f"awardsajax/{nickname}/{category}",
        )
    return awards


def _check_awards_cache(nickname: str, category: str = "") -> Optional[dict]:
    row = models.get_latest(f"{nickname}_awards")
    if not row:
        return None
    ttl = get_settings().cache_ttl_seconds
    age = int(time.time()) - int(row.get("created_at", 0))
    if age > ttl:
        return None
    parsed = row.get("parsed_json") or {}
    awards = parsed.get("awards") if isinstance(parsed, dict) else None
    if not awards or not isinstance(awards, dict):
        return None
    # 如果指定了 category 但缓存的是全量（或不同 category），缓存仍有效
    return awards


def _check_cache(nickname: str) -> Optional[schemas.PlayerResult]:
    row = models.get_latest(nickname)
    if not row:
        return None
    ttl = get_settings().cache_ttl_seconds
    age = int(time.time()) - int(row.get("created_at", 0))
    if age > ttl:
        return None
    parsed = row.get("parsed_json") or {}
    # 拒绝空 stats（之前失败请求可能存了脏快照）
    stats_data = parsed.get("stats", {}) if isinstance(parsed, dict) else {}
    if not stats_data or not isinstance(stats_data, dict):
        return None
    if not any(v for v in stats_data.values() if v):
        return None
    overview_data = parsed.get("overview") or {}
    return schemas.PlayerResult(
        profile=schemas.PlayerProfile(**parsed.get("profile", {})) if parsed.get("profile") else None,
        overview=schemas.OverviewData(**overview_data) if overview_data else None,
        stats=schemas.PlayerStats(categories=stats_data) if stats_data else None,
        cached=True,
        updated_at=row.get("created_at"),
    )
