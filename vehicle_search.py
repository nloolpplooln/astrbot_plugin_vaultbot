"""载具百科搜索模块。

提供载具搜索、详情、品牌列表、颜色搜索功能。
数据源：vehicle_index.json（精简索引） + gta-colors.json（颜色库）
"""
from __future__ import annotations

import json
import os
from typing import Any

_DIR = os.path.dirname(__file__)

# ── 载具索引 ──
_INDEX_PATH = os.path.join(_DIR, "vehicle_index.json")
_vehicles: list[dict] = []
_vehicles_loaded = False

# ── 颜色数据 ──
_COLORS_PATH = os.path.join(_DIR, "gta-colors.json")
_colors: list[dict] = []
_colors_loaded = False

# ── 颜色分类中文映射 ──
CATEGORY_CN: dict[str, str] = {
    "Metallic": "金属质感",
    "Matte": "哑光",
    "Util": "工业",
    "Worn": "磨损",
    "Metal": "金属",
    "Chrome": "铬合金",
    "Chameleon": "变色龙",
}
CATEGORY_EN: dict[str, str] = {v: k.lower() for k, v in CATEGORY_CN.items()}
# 分类别名（简称/别称 → 英文 key）
CATEGORY_ALIASES: dict[str, str] = {
    "金属质感": "metallic", "金属": "metallic", "metallic": "metallic",
    "哑光": "matte", "亚光": "matte", "matte": "matte",
    "工业": "util", "util": "util",
    "磨损": "worn", "worn": "worn",
    "铬合金": "chrome", "铬": "chrome", "chrome": "chrome",
    "变色龙": "chameleon", "chameleon": "chameleon",
}

# ── 品牌别称 ──
BRAND_ALIASES: dict[str, str] = {
    "pegassi": "佩嘉西", "grotti": "古罗帝", "benefactor": "贝菲特",
    "ubermacht": "优辉", "obey": "奥北", "bf": "毕福",
    "bravado": "冒险家", "declasse": "卡拉斯科", "vapid": "威皮",
    "albany": "奥尔巴尼", "annis": "安尼斯", "coil": "线圈",
    "dewbauchee": "德瓦奇", "dinka": "丁卡", "dundreary": "敦追里",
    "emperor": "帝王", "enus": "埃努斯", "fathom": "法萨姆",
    "gallivanter": "伽利瓦特", "hijak": "海贾克", "imponte": "因庞特",
    "invetero": "英维特罗", "karin": "卡林", "lampadati": "兰帕达缇",
    "maibatsu": "麦霸子", "mammoth": "猛犸", "maxwell": "麦克斯韦",
    "ocelot": "欧斯洛", "overflod": "傲弗拉", "pfister": "菲斯特",
    "principe": "普林西比", "progen": "普洛根", "rune": "茹恩",
    "schyster": "赛斯特", "shitzu": "希兹", "truffade": "特卢法德",
    "vulcar": "沃卡尔", "weeny": "威尼", "willard": "威拉德",
    "zirconium": "锆石", "nagasaki": "长崎", "western": "西部",
}


def _ensure_vehicles() -> list[dict]:
    global _vehicles, _vehicles_loaded
    if not _vehicles_loaded:
        if os.path.exists(_INDEX_PATH):
            with open(_INDEX_PATH, "r", encoding="utf-8") as f:
                _vehicles = json.load(f)
        _vehicles_loaded = True
    return _vehicles


def _ensure_colors() -> list[dict]:
    global _colors, _colors_loaded
    if not _colors_loaded:
        if os.path.exists(_COLORS_PATH):
            with open(_COLORS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                _colors = data.get("colors", [])
        _colors_loaded = True
    return _colors


# ── 搜索 ──

def search_vehicles(keyword: str, limit: int = 5) -> list[dict]:
    vehicles = _ensure_vehicles()
    kw = keyword.strip().lower()
    if not kw:
        return []

    tier1, tier2, tier3, tier4, tier5 = [], [], [], [], []

    for v in vehicles:
        name = (v.get("name") or "").lower()
        model = (v.get("model_name") or "").lower()
        brand = (v.get("brand") or "").lower()
        vtype = (v.get("type") or "").lower()
        py = (v.get("name_pinyin") or "").lower()
        eng = (v.get("name_eng") or "").lower()

        search_text = " ".join([name, model, brand, vtype, py, eng])

        if kw in search_text:
            if kw == name or kw == v.get("name", ""):
                tier1.append(v)
            elif kw in name:
                tier2.append(v)
            elif kw == model:
                tier3.append(v)
            elif kw == brand:
                tier4.append(v)
            else:
                tier5.append(v)

    # 品牌别名匹配
    brand_cn = BRAND_ALIASES.get(kw, "")
    if brand_cn:
        for v in vehicles:
            if (v.get("brand") or "") == brand_cn:
                if v not in tier1 + tier2 + tier3 + tier4 + tier5:
                    tier4.append(v)

    results = tier1 + tier2 + tier3 + tier4 + tier5
    return results[:limit]


def get_vehicle_by_name(name: str) -> dict | None:
    vehicles = _ensure_vehicles()
    nl = name.strip().lower()
    for v in vehicles:
        if (v.get("name") or "").lower() == nl:
            return v
        if (v.get("id") or "").lower() == nl:
            return v
        if (v.get("name_eng") or "").lower() == nl:
            return v
        if (v.get("model_name") or "").lower() == nl:
            return v
    return None


def get_vehicles_by_brand(brand: str) -> list[dict]:
    vehicles = _ensure_vehicles()
    bl = brand.strip().lower()
    bc = BRAND_ALIASES.get(bl, bl)

    results = [v for v in vehicles
               if (v.get("brand") or "").lower() in (bl, bc)]
    results.sort(key=lambda v: v.get("name", ""))
    return results


def search_colors(keyword: str, limit: int = 5) -> list[dict]:
    """搜索颜色。支持：纯色名、分类名、分类+色名、HEX值。
    例: "红" / "工业" / "工业红" / "#FF0000"
    """
    colors = _ensure_colors()
    kw = keyword.strip().lower()
    if not kw:
        return []

    # 解析分类前缀：尝试匹配 "分类名 + 色名" 组合
    cat_key = None
    name_kw = kw

    # 先检查别名直接匹配
    if kw in CATEGORY_ALIASES:
        cat_key = CATEGORY_ALIASES[kw]
        name_kw = ""
    else:
        # 检查是否为 分类名/别名 + 色名
        sorted_aliases = sorted(CATEGORY_ALIASES.items(), key=lambda x: -len(x[0]))
        for alias, eng_key in sorted_aliases:
            alias_lower = alias.lower()
            if kw.startswith(alias_lower) and len(kw) > len(alias_lower):
                cat_key = eng_key
                name_kw = kw[len(alias_lower):]
                break

    results = []
    for c in colors:
        cn = (c.get("name_cn") or "").lower()
        en = (c.get("name_en") or "").lower()
        cat = (c.get("category") or "").lower()
        hex_val = (c.get("hex") or "").lower()
        tech = (c.get("technical") or "").lower()

        # 分类筛选
        if cat_key and cat != cat_key:
            continue

        if not name_kw:
            results.append(c)
        elif name_kw in cn or name_kw in en or name_kw in hex_val or name_kw in tech:
            results.append(c)

    # 纯名称搜索返回更多结果
    if not cat_key:
        return results[:max(limit, 30)]
    return results[:limit]


def get_color_by_name(name: str) -> dict | None:
    colors = _ensure_colors()
    nl = name.strip().lower()
    for c in colors:
        if (c.get("name_cn") or "").lower() == nl:
            return c
        if (c.get("name_en") or "").lower() == nl:
            return c
    return None


def get_colors_by_name(name: str) -> list[dict]:
    """返回所有同名颜色（可能跨多个分类）。"""
    colors = _ensure_colors()
    nl = name.strip().lower()
    result = []
    for c in colors:
        if (c.get("name_cn") or "").lower() == nl:
            result.append(c)
        elif (c.get("name_en") or "").lower() == nl:
            result.append(c)
    return result


def get_color_by_id(color_id: int) -> dict | None:
    colors = _ensure_colors()
    for c in colors:
        if c.get("id") == color_id:
            return c
    return None


# ── 格式化 ──

def format_vehicle_list(results: list[dict], keyword: str) -> str:
    if not results:
        return f"未找到包含「{keyword}」的载具。试试英文名或车型名称？"

    lines = [f"==== 查车: {keyword} ({len(results)}个结果) ===="]
    for v in results:
        name = v.get("name", "?")
        brand = v.get("brand", "")
        vtype = v.get("type", "")
        price = v.get("price_buy", 0) or 0
        perf = v.get("performance", {}) or {}
        speed = perf.get("speed", 0)

        if price > 0:
            price_str = f"${price/1_000_000:.1f}M" if price >= 1_000_000 else f"${price:,.0f}"
        else:
            price_str = "免费/限时"

        line = f"{brand} {name} [{vtype}] {price_str}"
        if speed:
            line += f" 极速{speed:.0f}"
        lines.append(line)

    if len(results) >= 5:
        lines.append("\n提示：输入「查车详情 <车名>」查看完整信息")
    return "\n".join(lines)


def format_vehicle_detail(v: dict) -> str:
    name = v.get("name", "?")
    brand = v.get("brand", "")
    vtype = v.get("type", "")
    eng = v.get("name_eng", "")
    model = v.get("model_name", "")
    rarity = v.get("rarity", "")
    price = v.get("price_buy", 0) or 0
    seats = v.get("seats", "?")
    dlc = v.get("dlc", "")
    desc = v.get("description", "")
    shop = v.get("shop_source", "")
    armor = v.get("armor", {})
    perf = v.get("performance", {}) or {}
    specs = v.get("specs", {}) or {}
    release = v.get("release", {}) or {}
    upgrades = v.get("upgrades", []) or []
    imani = v.get("imani_tech", []) or []
    based = v.get("based_on", "")
    missile = v.get("missile_protection", False)
    livery_count = v.get("livery_count", 0)

    lines = [f"==== {name} ===="]
    if eng:
        lines.append(f"英文名: {eng}")
    lines.append(f"品牌: {brand}  |  类型: {vtype}  |  稀有度: {rarity}")
    lines.append(f"型号: {model}  |  座位: {seats}座")

    if price > 0:
        lines.append(f"价格: ${price:,.0f}" + (f" (${price/1_000_000:.2f}M)" if price >= 1_000_000 else ""))
    else:
        lines.append("价格: 免费/限时获取")

    if dlc:
        lines.append(f"DLC: {dlc}")
    if release.get("date"):
        lines.append(f"上线: {release['date']}")

    # 性能
    perf_items = []
    for k, label in [("speed", "极速"), ("acceleration", "加速"), ("handling", "操控"),
                     ("braking", "制动"), ("traction", "牵引")]:
        if k in perf:
            perf_items.append(f"{label} {perf[k]:.0f}")
    if perf_items:
        lines.append(f"性能: {' | '.join(perf_items)}")

    # 规格
    sp = []
    if specs.get("top_speed"):
        sp.append(f"极速 {specs['top_speed']}")
    if specs.get("top_speed_raw"):
        sp.append(f"({specs['top_speed_raw']})")
    if specs.get("lap_time"):
        sp.append(f"圈速 {specs['lap_time']}")
    if specs.get("drive"):
        sp.append(specs["drive"])
    if specs.get("layout"):
        sp.append(specs["layout"])
    if specs.get("gears"):
        sp.append(f"{specs['gears']}档")
    if sp:
        lines.append(f"规格: {' | '.join(sp)}")

    if upgrades:
        lines.append(f"可改装: {', '.join(upgrades)}")
    if imani:
        lines.append(f"伊玛尼科技: {', '.join(imani)}")

    defense = []
    if isinstance(armor, dict) and armor.get("name"):
        defense.append(armor["name"])
    if missile:
        defense.append("防导弹")
    if defense:
        lines.append(f"防御: {' | '.join(defense)}")

    if livery_count:
        lines.append(f"涂装: {livery_count}款")
    if based:
        lines.append(f"原型: {based}")
    if desc:
        lines.append(f"\n简介: {desc}")
    if shop:
        lines.append(f"  {shop}")

    return "\n".join(lines)


def format_brand_list(brand: str, vehicles: list[dict]) -> str:
    if not vehicles:
        return f"未找到品牌「{brand}」的载具。试试英文名？"

    lines = [f"==== {brand} ({len(vehicles)}辆) ===="]
    for v in vehicles:
        name = v.get("name", "?")
        vtype = v.get("type", "")
        price = v.get("price_buy", 0) or 0
        price_str = f"${price/1_000_000:.1f}M" if price >= 1_000_000 else (f"${price:,.0f}" if price > 0 else "免费")
        lines.append(f"  {name} [{vtype}] {price_str}")
    return "\n".join(lines)


def format_color_list(results: list[dict], keyword: str) -> str:
    if not results:
        return f"未找到「{keyword}」相关颜色。试试 红/蓝/金属质感/工业/铬合金？"

    lines = [f"==== 颜色: {keyword} ({len(results)}个) ===="]
    # 按分类分组
    by_cat: dict[str, list] = {}
    for c in results:
        cat_cn = CATEGORY_CN.get(c.get("category", ""), c.get("category", "?"))
        by_cat.setdefault(cat_cn, []).append(c)
    for cat_cn, items in by_cat.items():
        lines.append(f"\n【{cat_cn}】")
        for c in items:
            cn = c.get("name_cn", "")
            en = c.get("name_en", "")
            hex_val = c.get("hex", "")
            price = c.get("price", 0) or 0
            line = f"  {cn} ({en})  {hex_val}"
            if price:
                line += f"  ${price:,.0f}"
            lines.append(line)
    return "\n".join(lines)


def format_color_detail(c: dict) -> str:
    lines = [
        f"==== {c.get('name_cn', '?')} ====",
        f"英文: {c.get('name_en', '?')}",
        f"分类: {CATEGORY_CN.get(c.get('category', ''), c.get('category', '?'))}",
        f"色值: {c.get('hex', '?')}",
        f"技术名: {c.get('technical', '?')}",
    ]
    rgb = c.get("rgb", {})
    if rgb:
        lines.append(f"RGB: ({rgb.get('r', 0)}, {rgb.get('g', 0)}, {rgb.get('b', 0)})")

    price = c.get("price", 0) or 0
    if price:
        lines.append(f"价格: ${price:,.0f}")

    unlock = c.get("unlock", "")
    if unlock and unlock != "默认":
        lines.append(f"解锁: {unlock}")

    notes = c.get("notes", "")
    if notes:
        lines.append(f"备注: {notes}")

    purchase = c.get("purchase", {})
    if purchase:
        parts = [{"wheel": "轮毂", "body": "车体", "pearlescent": "珠光"}.get(k, k)
                 for k, v in purchase.items() if v]
        if parts:
            lines.append(f"可喷涂: {', '.join(parts)}")

    image = c.get("car_image", "")
    if image:
        lines.append(f"示例图见下方")

    return "\n".join(lines)


def get_thumbnail(v: dict) -> str | None:
    """获取载具缩略图 URL。"""
    thumb = v.get("thumbnail", "")
    return thumb if thumb else None


def get_color_image(c: dict) -> str | None:
    """获取颜色示例图 URL。"""
    return c.get("car_image", "") or c.get("car_image_thumb", "") or None
