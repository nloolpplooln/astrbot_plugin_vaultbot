"""对外数据结构：把 scapi + StatsAjax 两张数据源映射为干净的 Player 结果。

使用 dataclass 替代 Pydantic，避免 pydantic 版本兼容问题。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Game:
    name: str = ""
    platform: Optional[str] = None
    last_seen: Optional[str] = None


@dataclass
class LinkedAccount:
    service: str = ""
    user_name: Optional[str] = None
    user_id: Optional[str] = None


@dataclass
class Crew:
    id: int = 0
    name: Optional[str] = None
    tag: Optional[str] = None
    motto: Optional[str] = None
    member_count: Optional[int] = None
    is_primary: bool = False
    created_at: Optional[str] = None


@dataclass
class PlayerProfile:
    """基础资料（来自 scapi getprofile）。"""
    nickname: Optional[str] = None
    rockstar_id: Optional[int] = None
    avatar_url: Optional[str] = None
    country_code: Optional[str] = None
    friend_count: Optional[int] = None
    primary_crew: Optional[Crew] = None
    crews: List[Crew] = field(default_factory=list)
    games: List[Game] = field(default_factory=list)
    linked_accounts: List[LinkedAccount] = field(default_factory=list)


@dataclass
class PlayerStats:
    """深度生涯数据（来自 StatsAjax，200+ 项按分类分组）。"""
    categories: Dict[str, Dict[str, str]] = field(default_factory=dict)


@dataclass
class OverviewData:
    """等级/金钱概览（来自 overviewAjax）。"""
    rank: Optional[str] = None
    rp: Optional[str] = None
    cash: Optional[str] = None
    bank: Optional[str] = None
    play_time: Optional[str] = None
    crew_name: Optional[str] = None


@dataclass
class PlayerResult:
    """一次查询的完整结果。"""
    profile: Optional[PlayerProfile] = None
    overview: Optional[OverviewData] = None
    stats: Optional[PlayerStats] = None
    cached: bool = False
    updated_at: Optional[int] = None  # unix 秒


@dataclass
class ApiResponse:
    """统一返回体（对齐空桑 {code, message, body} 风格）。"""
    code: int = 200
    message: str = "ok"
    body: Any = None
