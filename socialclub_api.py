"""调用 SocialClub Query API 获取玩家数据。

替代 HQSHI（空桑）作为数据源，直接使用自建的 REST API。
API 地址可通过 set_api_base_url() 配置，默认 localhost:8686。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import aiohttp

_API_BASE = "http://localhost:8686"


def set_api_base_url(url: str) -> None:
    """配置 API 地址（由插件初始化时从 config 读取）。"""
    global _API_BASE
    _API_BASE = url.rstrip("/")


class ApiError(RuntimeError):
    pass


async def _get(endpoint: str, timeout: int = 30) -> Dict[str, Any]:
    """GET 请求本地 API，返回 JSON body。"""
    url = f"{_API_BASE}{endpoint}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                data = await resp.json()
                if data.get("code") != 200:
                    raise ApiError(data.get("message", f"HTTP {resp.status}"))
                body = data.get("body") or {}
                return body
    except aiohttp.ClientError as e:
        raise ApiError(f"API 连接失败: {e}") from e


async def check_health() -> Dict[str, Any]:
    """健康检查。"""
    url = f"{_API_BASE}/health"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            return await resp.json()


async def query_player(nickname: str, force: bool = False) -> Dict[str, Any]:
    """查询玩家完整数据：资料 + 等级金钱 + 深度统计。"""
    force_str = "&force=true" if force else ""
    return await _get(f"/api/player?nickname={nickname}{force_str}")


async def query_profile(nickname: str) -> Dict[str, Any]:
    """仅查询基础资料。"""
    return await _get(f"/api/player/profile?nickname={nickname}")


async def query_stats(nickname: str) -> Dict[str, Any]:
    """仅查询深度统计。"""
    return await _get(f"/api/player/stats?nickname={nickname}")


async def query_awards(nickname: str, category: str = "") -> Dict[str, Any]:
    """查询奖章数据。category 为空则全部，否则只查指定分类。"""
    cat_str = f"&category={category}" if category else ""
    return await _get(f"/api/player/awards?nickname={nickname}{cat_str}")


# ---- 奖章中英映射 ----
_awards_cn_path = os.path.join(os.path.dirname(__file__), "awards_cn.json")
_awards_index_path = os.path.join(os.path.dirname(__file__), "awards_index.json")
_CN_NAMES: Dict[str, str] = {}
_CN_CATEGORIES: Dict[str, str] = {}
_AWARDS_INDEX: list = []
if os.path.exists(_awards_cn_path):
    with open(_awards_cn_path, "r", encoding="utf-8") as _f:
        _data = json.load(_f)
        _CN_NAMES = {k.lower(): v for k, v in _data.get("names", {}).items()}
        _CN_CATEGORIES = _data.get("categories", {})
if os.path.exists(_awards_index_path):
    with open(_awards_index_path, "r", encoding="utf-8") as _f:
        _AWARDS_INDEX = json.load(_f)


def _cn(name: str) -> str:
    """返回中文名，找不到则回退到原名。"""
    return _CN_NAMES.get(name.lower(), name)


def _cn_cat(cat: str) -> str:
    """返回分类中文名。"""
    return _CN_CATEGORIES.get(cat, cat)


def format_awards_text(body: Dict[str, Any], search: str = "") -> str:
    """格式化奖章数据。search 非空则搜索指定奖章名（支持中英文）。"""
    awards = body.get("awards") or {}
    if not awards:
        return "暂无奖章数据。"
    items_list = awards.pop("_items", [])

    # 搜索模式（支持中英文关键词）
    if search:
        matches = []
        for n, m, d, t in items_list:
            cn = _cn(n)
            if search.lower() in n.lower() or search in cn:
                matches.append((cn, m, d, t))
        if not matches:
            return f"未找到包含「{search}」的奖章。"
        lines = [f"==== 搜索: {search} ({len(matches)}个) ===="]
        for cn_name, medal, done, total in matches[:20]:
            medal_cn = {"Gold": "金", "Silver": "银", "Bronze": "铜", "Platinum": "铂"}.get(medal, medal)
            lines.append(f"[{medal_cn}] {cn_name}: {done}/{total or 1}")
        if len(matches) > 20:
            lines.append(f"... 共 {len(matches)} 个匹配")
        return "\n".join(lines)

    # 全览模式
    lines = ["==== 奖章概览 ===="]

    # 分类概览
    for cat, items in sorted(awards.items()):
        if not items: continue
        d, t = items[0][1], items[0][2] if len(items[0]) > 2 else (0, 0)
        cn_cat = _cn_cat(cat)
        if t:
            pct = int(d / t * 10) if t > 0 else 0
            bar = "|" * pct + "." * (10 - pct) if pct <= 10 else "FULL"
            lines.append(f"[{bar}] {cn_cat}: {d}/{t}")
        else:
            lines.append(f"[?] {cn_cat}")

    if items_list:
        completed = sum(1 for _, _, d, t in items_list if d and t and d >= t)
        lines.append(f"\n-- 共 {completed}/{len(items_list)} 奖章完成")

    # 异常检测
    judgements = body.get("judgements") or []
    if judgements:
        lines.append("")
        for j in judgements:
            prefix = "!!" if j.get("level") == "异常" else "?"
            lines.append(f"{prefix} {j.get('level')}: {j.get('message')}")

    return "\n".join(lines)


def search_local_awards(keyword: str) -> list:
    """搜索本地奖章索引，返回匹配的奖章列表（含图片路径和中文名）。
    不查玩家数据，只查奖章定义。
    """
    if not _AWARDS_INDEX:
        return []
    kw = keyword.lower()
    matches = []
    for a in _AWARDS_INDEX:
        if kw in a["cn_name"] or kw in a["en_name"].lower():
            matches.append(a)
    return matches


def format_local_awards_result(matches: list, keyword: str, player: str = "", progress: dict = None) -> str:
    """格式化本地奖章搜索结果，可选显示玩家进度。"""
    if not matches:
        return f"未找到包含「{keyword}」的奖章。"
    lines = [f"==== 奖章: {keyword} ({len(matches)}个) ===="]
    medal_map = {"Gold": "金", "Silver": "银", "Bronze": "铜", "Platinum": "铂", "tattoo": "纹身"}
    progress = progress or {}
    for a in matches:
        medal_cn = medal_map.get(a["medal"], a["medal"])
        en_name = a["en_name"]
        lines.append(f"{a['cn_name']} [{medal_cn}]")
        if player:
            p = progress.get(en_name.lower())
            if p:
                pm, pd, pt = p
                total = pt or 1
                lines.append(f"  {player}: {pd}/{total}")
            else:
                lines.append(f"  {player}: 暂无数据")
        else:
            lines.append(f"  查进度: 查奖章 {a['cn_name']} <昵称>")
    return "\n".join(lines)


def _age_hint(body: Dict[str, Any]) -> str:
    """数据超过1小时返回提示文字。"""
    updated = body.get("updated_at", 0)
    if not updated:
        return ""
    import datetime, time
    age = int(time.time()) - int(updated)
    if age > 3600:
        h = age // 3600
        return f"\n[数据已过{h}小时]"
    return ""


def _fmt_time(body: Dict[str, Any]) -> str:
    """格式化更新时间。"""
    ts = body.get("updated_at", 0)
    if not ts:
        return ""
    import datetime
    return datetime.datetime.fromtimestamp(int(ts)).strftime('%m-%d %H:%M')


# ---- 中英 Key 映射（缓存可能是英文 key）----
_KEY_MAP = {
    "总收入": ("Overall income", "总收入"),
    "总花费": ("Overall expenses", "总花费"),
    "杀死的玩家总数": ("Total players killed", "杀死的玩家总数"),
    "被其他玩家杀死的总次数": ("Total deaths by players", "被其他玩家杀死的总次数"),
    "竞赛玩家击杀/死亡比率": ("Competitive Player Kill / Death ratio", "竞赛玩家击杀/死亡比率"),
    "GTA 在线模式中花费的时间": ("Time spent in GTA Online", "GTA 在线模式中花费的时间"),
    "角色使用时间": ("Time played as character", "角色使用时间"),
    "制作的角色": ("Character created", "制作的角色"),
    "最后一次升级": ("Last ranked up", "最后一次升级"),
    "死亡总数": ("Total deaths", "死亡总数"),
    "击杀数": ("Kills", "击杀数"),
    "精准度": ("Accuracy", "精准度"),
    "玩家击杀数": ("Player kills", "玩家击杀数"),
    "玩家爆头击杀数": ("Player headshot kills", "玩家爆头击杀数"),
    "击杀警察数": ("Cops killed", "击杀警察数"),
    "被通缉次数": ("Times Wanted", "被通缉次数"),
    "抢劫商店次数": ("Store Hold Ups", "抢劫商店次数"),
    "载具击杀数": ("Vehicular kills", "载具击杀数"),
    "驾驶过最快的陆上载具": ("Road vehicle driven fastest", "驾驶过最快的陆上载具"),
    "驾驶陆上载具达到的最高速度": ("Highest speed in a road vehicle", "驾驶陆上载具达到的最高速度"),
    "汽车驾驶距离": ("Distance traveled in cars", "汽车驾驶距离"),
    "差事收入": ("Earned from Jobs", "差事收入"),
    "出售载具收入": ("Earned from selling vehicles", "出售载具收入"),
    "载具和维护花费": ("Spent on vehicles & maintenance", "载具和维护花费"),
    "武器和护甲花费": ("Spent on weapons & armor", "武器和护甲花费"),
}


def _get_val(items, zh_key):
    """从 items 中取中文 key 的值，回退到英文 key。"""
    if not items: return None
    en_key, zh = _KEY_MAP.get(zh_key, (zh_key, zh_key))
    return items.get(zh) or items.get(en_key)


def _parse_num(v: Any) -> float:
    """'$1,373,379' '50.6K' '733.5M' → float"""
    if v is None: return 0.0
    s = str(v).replace('$','').replace(',','').replace(' ','').strip()
    try:
        if s.upper().endswith('K'): return float(s[:-1])*1e3
        if s.upper().endswith('M'): return float(s[:-1])*1e6
        if s.upper().endswith('B'): return float(s[:-1])*1e9
        return float(s)
    except ValueError: return 0.0


def _to_hours(v: str) -> str:
    """'54d 1h 9m 55s' → '1297.2h'"""
    import re
    total = 0
    m = re.search(r'(\d+)\s*天|(\d+)d', v); total += int(m.group(1) or m.group(2) or 0) * 24 if m else 0
    m = re.search(r'(\d+)\s*小时|(\d+)h', v); total += int(m.group(1) or m.group(2) or 0) if m else 0
    m = re.search(r'(\d+)\s*分|(\d+)m', v); total += (int(m.group(1) or m.group(2) or 0) / 60) if m else 0
    if total >= 10: return f'{total:.0f}h'
    return f'{total:.1f}h'


def _to_cn_date(v: str) -> str:
    """'Nov 04 2024' / 'Jul 05 2026' → '2024-11-04'"""
    import re
    from datetime import datetime
    try:
        dt = datetime.strptime(v.strip(), "%b %d %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return v


_TIME_KEYS = {"GTA 在线模式中花费的时间", "角色使用时间",
              "Time spent in GTA Online", "Time played as character"}
_DATE_KEYS = {"制作的角色", "最后一次升级", "Character created", "Last ranked up"}


def format_career_text(body: Dict[str, Any]) -> str:
    """格式化为 QQ 消息文本，模板风格。"""
    profile = body.get("profile") or {}
    overview = body.get("overview") or {}
    cats = (body.get("stats") or {}).get("categories") or {}

    nickname = profile.get("nickname") or "?"
    rid = profile.get("rockstar_id") or "?"

    lines = ["==== GTA 生涯查询 ===="]
    lines.append(f"昵称:{nickname} | RID: {rid}")

    # 平台
    games = profile.get("games") or []
    if games:
        platforms = []
        for g in games:
            p = g.get("platform", "")
            if p and p not in platforms:
                platforms.append(p)
        if platforms:
            lines.append(f"平台: {', '.join(platforms)}")

    # 等级/金钱/好友
    if overview.get("rank"): lines.append(f"等级: Lv.{overview['rank']}")
    if overview.get("cash"): lines.append(f"现金: {overview['cash']}")
    if overview.get("bank"): lines.append(f"银行: {overview['bank']}")
    if profile.get("friend_count", 0): lines.append(f"好友: {profile['friend_count']}")

    before_gap = [
        ("GTA 在线模式中花费的时间", "career"),
        ("角色使用时间", "general"),
        ("总收入", "career"),
        ("总花费", "career"),
    ]
    for key, cat in before_gap:
        v = _get_val(cats.get(cat) or {}, key)
        if v:
            if key in _TIME_KEYS: v = _to_hours(v)
            elif key in _DATE_KEYS: v = _to_cn_date(v)
            lines.append(f"{key}: {v}")

    # 收支差
    income_v = _get_val(cats.get("career") or {}, "总收入")
    expense_v = _get_val(cats.get("career") or {}, "总花费")
    if income_v and expense_v:
        income = _parse_num(income_v)
        expense = _parse_num(expense_v)
        cash = _parse_num(overview.get("cash", "0"))
        bank = _parse_num(overview.get("bank", "0"))
        gap = expense + cash + bank - income
        sign = "+" if gap >= 0 else ""
        lines.append(f"收支差: {sign}{gap:,.0f}")

    after_gap = [
        ("杀死的玩家总数", "career"),
        ("被其他玩家杀死的总次数", "career"),
        ("竞赛玩家击杀/死亡比率", "career"),
        ("制作的角色", "general"),
        ("最后一次升级", "general"),
        ("驾驶过最快的陆上载具", "vehicles"),
        ("驾驶陆上载具达到的最高速度", "vehicles"),
    ]
    for key, cat in after_gap:
        v = _get_val(cats.get(cat) or {}, key)
        if v:
            if key in _TIME_KEYS: v = _to_hours(v)
            elif key in _DATE_KEYS: v = _to_cn_date(v)
            lines.append(f"{key}: {v}")

    # 异常检测
    judgements = body.get("judgements") or []
    if judgements:
        for j in judgements:
            lv = j.get("level", "")
            msg = j.get("message", "")
            if lv == "异常":
                lines.append(f"!! {lv}: {msg}")
            else:
                lines.append(f"? {lv}: {msg}")

    ts = body.get("updated_at", 0)
    if ts:
        import datetime
        dt = datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"数据更新时间 |{dt}")
    lines.append("提示：更多数据请用其他指令查看")

    return "\n".join(lines)


def format_compare_text(b1: Dict[str, Any], n1: str, b2: Dict[str, Any], n2: str) -> str:
    """两个玩家并排对比（每行 ≤15汉字）。"""
    p1, p2 = b1.get("profile") or {}, b2.get("profile") or {}
    o1, o2 = b1.get("overview") or {}, b2.get("overview") or {}
    c1 = (b1.get("stats") or {}).get("categories") or {}
    c2 = (b2.get("stats") or {}).get("categories") or {}

    r1, r2 = o1.get("rank", "?"), o2.get("rank", "?")
    lines = [f"==== {n1} vs {n2} ===="]
    lines.append(f"Lv.{r1} vs Lv.{r2}")
    if o1.get("cash") or o2.get("cash"):
        lines.append(f"现金 {o1.get('cash','?')} vs {o2.get('cash','?')}")
    if o1.get("bank") or o2.get("bank"):
        lines.append(f"银行 {o1.get('bank','?')} vs {o2.get('bank','?')}")

    sections = [
        ("生涯", "career", (
            ("总收入", "总收入"), ("总花费", "总花费"),
            ("杀玩家", "杀死的玩家总数"), ("被击杀", "被其他玩家杀死的总次数"),
            ("KD比", "竞赛玩家击杀/死亡比率"),
        )),
        ("综合", "general", (("角色用时", "角色使用时间"),)),
        ("战斗", "combat", (("击杀", "击杀数"), ("精准", "精准度"))),
        ("犯罪", "crimes", (("杀警", "击杀警察数"),)),
    ]
    for cn_cat, cat, pairs in sections:
        items1, items2 = c1.get(cat) or {}, c2.get(cat) or {}
        vals = []
        for short_k, full_k in pairs:
            v1 = _get_val(items1, full_k) or "-"
            v2 = _get_val(items2, full_k) or "-"
            if full_k in _TIME_KEYS:
                v1 = _to_hours(v1) if v1 != '-' else '-'
                v2 = _to_hours(v2) if v2 != '-' else '-'
            vals.append(f"  {short_k}: {v1} vs {v2}")
        if vals:
            lines.append(f"\n[{cn_cat}]")
            lines.extend(vals)

    lines.append(f"\n-- {n1}: {_fmt_time(b1)}{_age_hint(b1)}")
    lines.append(f"-- {n2}: {_fmt_time(b2)}{_age_hint(b2)}")
    return "\n".join(lines)


def format_category_text(body: Dict[str, Any], category: str) -> str:
    """仅格式化某一分类的统计（用于独立查询命令）。"""
    cats = (body.get("stats") or {}).get("categories") or {}
    items = cats.get(category) or {}
    if not items:
        return f"该玩家暂无 [{category}] 数据。"

    zh_name = CATEGORY_ALIASES.get(category, category)
    cached = "缓存" if body.get("cached") else "实时"
    lines = [f"==== {zh_name} ({len(items)}项)  [{cached} {_fmt_time(body)}{_age_hint(body)}] ===="]
    for k, v in items.items():
        cn_k = _cn(k) if _CN_NAMES.get(k.lower()) else k
        lines.append(f"  {cn_k}: {v}")

    return "\n".join(lines)


CATEGORY_ALIASES = {
    "career": "生涯", "general": "综合", "combat": "战斗",
    "crimes": "犯罪", "vehicles": "载具", "cash": "收支",
    "skills": "技能", "weapons": "武器",
}


def format_profile_text(body: Dict[str, Any]) -> str:
    """仅格式化基础资料（简短版）。"""
    profile = body if "nickname" in body else body.get("profile") or body
    overview = body.get("overview") or {}

    lines = ["==== 玩家资料 ===="]
    if profile.get("nickname"): lines.append(f"昵称: {profile['nickname']}")
    if profile.get("rockstar_id"): lines.append(f"RID: {profile['rockstar_id']}")
    if overview.get("rank"): lines.append(f"等级: {overview['rank']}")
    if profile.get("country_code"): lines.append(f"所在地: {profile['country_code']}")
    if profile.get("friend_count"): lines.append(f"好友: {profile['friend_count']}")

    primary = profile.get("primary_crew")
    if primary: lines.append(f"帮会: {primary['name']} [{primary['tag']}]")

    games = profile.get("games") or []
    if games: lines.append(f"游戏: {', '.join(g['name'] for g in games)}")

    return "\n".join(lines)
