"""解析层：从 Social Club 页面 HTML 中抽取嵌入的玩家 JSON。

R星把数据藏在 <script> 里，历史上有多种形态，本模块用「多策略」抽取以兼容页面改版：

  策略 A —— sl:translate_json 包裹（老 Social Club 形态）:
      settings.VehiclesJson = /*<sl:translate_json>*/{...}/*</sl:translate_json>*/;
  策略 B —— Next.js 数据岛（并入主站后的现代形态）:
      <script id="__NEXT_DATA__" type="application/json">{...}</script>
  策略 C —— 直接赋值的 JSON:
      settings.xxx = {...};

真实页面结构需在阶段0用 probe_login.py 抓到样本后校准 map_player() 的字段映射。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

try:
    from bs4 import BeautifulSoup
except ImportError:  # 允许在未装依赖时被导入（回退到正则策略）
    BeautifulSoup = None  # type: ignore


# 策略 A：<sl:translate_json> ... </sl:translate_json>（注释包裹）
_SL_JSON = re.compile(
    r"/\*<sl:translate_json>\*/\s*(?P<json>[\[{].*?[\]}])\s*/\*</sl:translate_json>\*/",
    re.DOTALL,
)
# 策略 C：settings.Xxx = {...} 或 [...] ;
_ASSIGN_JSON = re.compile(
    r"settings\.(?P<key>\w+)\s*=\s*(?P<json>[\[{].*?[\]}])\s*;",
    re.DOTALL,
)


def _script_texts(html: str) -> List[str]:
    """取出所有 <script> 的文本内容。优先用 BeautifulSoup，回退到正则。"""
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        return [node.get_text() or "" for node in soup.find_all("script")]
    return re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL | re.IGNORECASE)


def _try_json(raw: str) -> Optional[Any]:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def extract_embedded_json(html: str) -> Dict[str, Any]:
    """尽力从页面中抽出所有可解析的 JSON 块。

    返回形如 {"sl_translate": [...], "assign": {key: value}, "next_data": {...}}。
    上层可据此定位玩家字段；解析失败的策略对应键缺省。
    """
    result: Dict[str, Any] = {}
    scripts = _script_texts(html)
    joined = "\n".join(scripts)

    # 策略 A
    sl_blocks = [obj for m in _SL_JSON.finditer(joined) if (obj := _try_json(m.group("json"))) is not None]
    if sl_blocks:
        result["sl_translate"] = sl_blocks

    # 策略 C
    assigns: Dict[str, Any] = {}
    for m in _ASSIGN_JSON.finditer(joined):
        obj = _try_json(m.group("json"))
        if obj is not None:
            assigns[m.group("key")] = obj
    if assigns:
        result["assign"] = assigns

    # 策略 B：Next.js 数据岛
    next_match = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(?P<json>\{.*?\})</script>',
        html,
        re.DOTALL,
    )
    if next_match:
        obj = _try_json(next_match.group("json"))
        if obj is not None:
            result["next_data"] = obj

    return result


def is_auth_blocked(html: str, final_url: str = "") -> bool:
    """判断响应是否是「未登录/登录失效」被挡。

    以最终 URL 是否被重定向到登录/拦截页为准（可靠信号）；
    不用页面内容里是否出现 signin 脚本判断——正常页面头部也含这些，会误判。
    """
    lu = final_url.lower()
    if "blocker/authcheck" in lu or "signin.rockstargames.com" in lu:
        return True
    # 兜底：整页几乎只是登录表单（无实际内容）
    return 'id="signInForm"' in html and len(html) < 4000


def map_player(embedded: Dict[str, Any]) -> Dict[str, Any]:
    """把抽出的 JSON 映射成统一玩家字段。

    ⚠️ 字段映射基于空桑公开的输出字段与老文档推断，真实键名待阶段0样本校准。
    映射不到的字段留空，不抛错，方便先跑通链路再迭代。
    """
    player: Dict[str, Any] = {
        "nickname": None,
        "rockstar_id": None,
        "crew": None,
        "former_names": [],
        "linked_accounts": [],
        "location": None,
        "games_owned": [],
        "raw_keys": sorted(embedded.keys()),  # 便于调试：这次命中了哪些策略
    }
    # TODO(阶段0): 拿到真实样本后，在此按实际 JSON 结构填充上述字段。
    return player


def parse_gtav_overview(html: str) -> Dict[str, str]:
    """解析 overviewAjax HTML，提取等级/RP/现金/银行存款/在线时长/帮会。

    R* 把数据放在 inline-block 结构中，用 CSS class+文本混合。直接用 regex 从可见文本抽取。
    实测(2026-07)数据格式（slot=Freemode 时）:
      370 11.1M RP Play Time: 51d 15h 6m ... Cash $1,366,649 Bank $25,517,361 Dahua Empire dhei ...
    注意：HTML 开头有 <script class="update-settings"> 含平台/昵称 JSON，需先剥离。
    """
    import re
    result: Dict[str, str] = {}
    # 剥离 script 标签内容，避免 JSON 中的数字干扰等级匹配
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    # 去掉剩余 HTML 标签
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    # 等级：剥离 script 后文本最开头的数字（如 "370"）
    m = re.match(r"(\d{2,4})\s", text)
    if m:
        result["rank"] = m.group(1)
    # RP：如 "11.1M RP值" 或 "11.1M RP"
    m = re.search(r"([\d.]+[KMB]?)\s*RP", text)
    if m:
        result["rp"] = m.group(1)
    # 在线时长（中/英）— 中文格式: "游戏时间：51 天 15 小时 6 分 4 秒"
    m = re.search(r"(?:Play Time|游戏时间)[:：]\s*(\d+\s*天\s*\d+\s*小时\s*\d+\s*分(?:\s*\d+\s*秒)?)", text)
    if not m:
        m = re.search(r"Play Time:\s*(\d+d\s*\d+h\s*\d+m)", text)
    if m:
        result["play_time"] = m.group(1).strip()
    # 现金（中/英）
    m = re.search(r"(?:Cash|现金)\s+\$?([\d,]+(?:\.\d+)?)", text)
    if m:
        result["cash"] = "$" + m.group(1)
    # 银行存款（中/英）
    m = re.search(r"(?:Bank|银行)\s+\$?([\d,]+(?:\.\d+)?)", text)
    if m:
        result["bank"] = "$" + m.group(1)
    # 帮会名（Bank/银行 金额之后）— 中英文通用
    m = re.search(r"(?:Bank|银行)\s+\$?[\d,]+\s+(\S+(?:\s+\S+){0,3})\s+(?:Competitive|dhei|\w\w\w\w|$)", text)
    if m:
        crew = m.group(1).strip()
        if crew and not crew[0].isdigit() and len(crew) > 1:
            result["crew_name"] = crew
    return result


def parse_gtav_stats(html: str) -> Dict[str, Dict[str, str]]:
    """解析 StatsAjax 页面 HTML，返回 {分类: {项名: 值}}。

    结构（已从真实样本确认）：
      每个 `.tab-pane`（id 为分类，如 career/skills/...）内：
      - 普通项：<tr><td>项名</td><td>值</td></tr>
      - 技能项：<div class="left">项名</div><div class="right">…progress-bar…</div>
    深度数据（总收入/在线时长/K-D/技能/犯罪/载具等）都在这里。
    """
    if BeautifulSoup is None:
        raise RuntimeError("需要 beautifulsoup4 才能解析 StatsAjax HTML")
    soup = BeautifulSoup(html, "html.parser")
    result: Dict[str, Dict[str, str]] = {}
    for pane in soup.select(".tab-pane"):
        cat = pane.get("id") or "unknown"
        items: Dict[str, str] = {}
        # 普通表格行（排除内部是技能 left/right 结构的）
        for tr in pane.select("tr"):
            tds = tr.find_all("td", recursive=False)
            if len(tds) >= 2 and not tds[0].select_one(".left"):
                label = tds[0].get_text(" ", strip=True)
                val = tds[1].get_text(" ", strip=True)
                if label and val:
                    items[label] = val
        # 技能项 left/right
        for cf in pane.select(".clearfix"):
            left = cf.select_one(".left")
            right = cf.select_one(".right")
            if left and right:
                label = left.get_text(" ", strip=True)
                val = right.get_text(" ", strip=True)
                if label and val:
                    items[label] = val
        if items:
            result[cat] = items
    return result


def parse_gtav_awards(html: str) -> Dict[str, list]:
    """解析 AwardsAjax HTML，返回 {分类: [(奖章名, 完成数, 总数), ...]}。

    纯正则实现，兼容 Python 3.14。
    分类概览: awardsAchieved + title + 数字/
    单项奖章: data-name + award-name + medal color
    """
    import re
    result: Dict[str, list] = {}

    # 1. 分类概览
    for m in re.finditer(
        r'<p[^>]*class="[^"]*awardsAchieved[^"]*"[^>]*>'
        r'.*?<span[^>]*class="[^"]*title[^"]*"[^>]*>(?P<cat>[^<]+)</span>'
        r'\s*(?P<done>\d+)\s*<span[^>]*>\s*/\s*(?P<total>\d+)\s*</span>\s*</p>',
        html, re.DOTALL | re.IGNORECASE
    ):
        result[m.group("cat").strip()] = [("完成", int(m.group("done")), int(m.group("total")))]
    # 兜底：无 span 包裹斜杠
    if not result:
        for m in re.finditer(
            r'<p[^>]*class="[^"]*awardsAchieved[^"]*"[^>]*>'
            r'.*?<span[^>]*class="[^"]*title[^"]*"[^>]*>(?P<cat>[^<]+)</span>'
            r'\s*(?P<done>\d+)\s*/\s*(?P<total>\d+)',
            html, re.DOTALL | re.IGNORECASE
        ):
            result[m.group("cat").strip()] = [("完成", int(m.group("done")), int(m.group("total")))]

    # 2. 单项奖章：直接用 data-value / data-target
    for li in re.finditer(
        r'<li[^>]*data-name="(?P<name>[^"]+)"'
        r'[^>]*data-award="(?P<medal>[^"]+)"'
        r'(?P<attrs>[^>]*)>',
        html, re.DOTALL
    ):
        name = li.group("name").replace("&#39;", "'").replace("&amp;", "&")
        medal = li.group("medal")
        attrs = li.group("attrs")

        if len(name) < 2:
            continue

        # 用 data-value / data-target 获取进度
        done = 0
        total = 0
        dv = re.search(r'data-value="(\d+)"', attrs)
        dt = re.search(r'data-target="(\d+)"', attrs)
        if dv:
            done = int(dv.group(1))
        if dt:
            total = int(dt.group(1))

        # 无 data-target → 单层奖章，用 data-complete 判断
        if total == 0:
            total = 1
            dc = re.search(r'data-complete="(True|False)"', attrs)
            if dc and dc.group(1) == "True":
                done = 1

        result.setdefault("_items", []).append((name, medal, done, total))

    return result
