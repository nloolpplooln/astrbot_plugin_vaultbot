"""从 VaultGTA vehicles.json 构建精简搜索索引。

用法：python build_vehicle_index.py
输出：vehicle_index.json（约 1-1.5MB）
"""
import json
import os
import sys

SRC = r"C:\Users\lenovo\Desktop\ai\gta\new gta\gta\vehicles.json"
DST = os.path.join(os.path.dirname(__file__), "vehicle_index.json")

KEEP_FIELDS = [
    "id", "name", "model_name", "brand", "type", "rarity",
    "price_buy", "seats", "class_id", "dlc", "thumbnail",
    "performance", "specs", "release", "upgrades", "imani_tech",
    "armor", "description", "shop_source", "based_on",
    "missile_protection", "screenshots",
]

def build():
    print(f"Loading {SRC} ...")
    with open(SRC, "r", encoding="utf-8") as f:
        vehicles = json.load(f)

    print(f"  {len(vehicles)} vehicles loaded")

    out = []
    for v in vehicles:
        item = {}
        for k in KEEP_FIELDS:
            if k in v:
                item[k] = v[k]

        # 提取 _detail 中的搜索关键字段
        detail = v.get("_detail", {})
        item["name_eng"] = detail.get("name_eng", "")
        item["name_pinyin"] = detail.get("name_pinyin", "")
        item["name_pinyin_abbr"] = detail.get("name_pinyin_abbr", "")
        item["name_zht"] = detail.get("name_zht", "")

        # 截断描述（最长 200 字，QQ 消息不长）
        desc = item.get("description", "")
        if desc and len(desc) > 200:
            item["description"] = desc[:200] + "…"

        # 涂装数量（不存涂装详情，省空间）
        liveries = v.get("liveries", [])
        item["livery_count"] = len(liveries)

        # 截图只保留第一张（screenshots 是 dict，key 为视角名）
        screenshots = item.get("screenshots", {})
        if isinstance(screenshots, dict) and len(screenshots) > 1:
            first_key = next(iter(screenshots))
            item["screenshots"] = {first_key: screenshots[first_key]}

        out.append(item)

    size_kb = len(json.dumps(out, ensure_ascii=False)) / 1024
    print(f"  Output: {len(out)} vehicles, ~{size_kb:.0f} KB")

    with open(DST, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"  Saved to {DST}")

if __name__ == "__main__":
    build()
