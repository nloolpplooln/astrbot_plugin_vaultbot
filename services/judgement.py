"""异常数据检测 — 完整实现 judgement-rules.md（32规则+5奖章）。

返回 [(level, message), ...]，level: "异常" | "存疑"
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def parse_seconds(s: str) -> int:
    """解析 '51d 16h 3m 50s' / '0d 6h 30m' → 秒"""
    sec = 0
    m = re.search(r'(\d+)\s*天|(\d+)d', s); sec += int(m.group(1) or m.group(2) or 0) * 86400 if m else 0
    m = re.search(r'(\d+)\s*小时|(\d+)h', s); sec += int(m.group(1) or m.group(2) or 0) * 3600 if m else 0
    m = re.search(r'(\d+)\s*分|(\d+)m', s); sec += int(m.group(1) or m.group(2) or 0) * 60 if m else 0
    m = re.search(r'(\d+)\s*秒|(\d+)s', s); sec += int(m.group(1) or m.group(2) or 0) if m else 0
    return sec


def parse_number(v: Any) -> float:
    """'$1,373,379' '50.6K' '733.5M' → float"""
    if v is None: return 0.0
    s = str(v).replace('$', '').replace(',', '').replace(' ', '').strip()
    try:
        if s.upper().endswith('K'): return float(s[:-1]) * 1e3
        if s.upper().endswith('M'): return float(s[:-1]) * 1e6
        if s.upper().endswith('B'): return float(s[:-1]) * 1e9
        return float(s)
    except ValueError:
        return 0.0


def parse_speed(v: Any) -> float:
    """'214.46 mph' / '350 km/h' → mph"""
    if v is None: return 0.0
    s = str(v).strip().lower()
    m = re.search(r'([\d.]+)\s*(mph|km/h|kmh)', s)
    if m:
        sp = float(m.group(1))
        return sp / 1.60934 if m.group(2) in ('km/h', 'kmh') else sp
    try: return float(s)
    except ValueError: return 0.0


# ═══════════════════════════════════════════════════════════════
# 主检测
# ═══════════════════════════════════════════════════════════════

def check(player: dict, awards: dict = None) -> List[Tuple[str, str]]:
    findings: List[Tuple[str, str]] = []

    overview = player.get("overview") or {}
    profile = player.get("profile") or {}
    cats = (player.get("stats") or {}).get("categories") or {}

    def g(cat: str, *keys: str) -> Optional[str]:
        items = cats.get(cat) or {}
        for k in keys:
            v = items.get(k)
            if v is not None: return str(v)
        return None

    def gn(cat: str, *keys: str) -> float:
        """取值→数字"""
        return parse_number(g(cat, *keys))

    def gs(cat: str, *keys: str) -> int:
        """取值→秒"""
        v = g(cat, *keys)
        return parse_seconds(v) if v else 0

    # ═══════════════════════════════
    # 1.1 总收入 §1.1
    # ═══════════════════════════════
    online = gs("career", "Time spent in GTA Online", "GTA 在线模式中花费的时间")
    income = gn("career", "Overall income", "总收入")
    if online > 14400 and income > 0:  # 在线>4h
        hours = online / 3600
        # 佩岛理论时薪 800W（15min 200W）
        max_income = hours * 8_000_000
        if income > max_income * 1.2:
            findings.append(("存疑", f"总收入 {g('career','Overall income','总收入')} 超过理论上限"))
        # 老抢劫犯罪之神 6h=1250W → 时薪~208W
        # 佩岛不停刷 时薪~800W

    # ═══════════════════════════════
    # 1.2 收支差距 §1.2
    # ═══════════════════════════════
    expense = gn("career", "Overall expenses", "总花费")
    cash = parse_number(overview.get("cash", "0"))
    bank = parse_number(overview.get("bank", "0"))
    if income > 0:
        gap = expense + cash + bank - income
        # 起步允许 10M，每小时 +1W，封顶 50M
        allowance = min(50_000_000, 10_000_000 + (online / 3600) * 10_000)
        if abs(gap) > allowance and abs(gap) > income * 0.1:
            findings.append(("异常", f"收支差距 {gap:,.0f}（允许±{allowance/1e6:.1f}M）"))

    # ═══════════════════════════════
    # 1.5 差事收入 §1.5
    # ═══════════════════════════════
    job_income = gn("cash", "Earned from Jobs", "差事收入")
    if online > 14400 and job_income > 0:
        hours = online / 3600
        if job_income > hours * 8_000_000 * 1.2:
            findings.append(("存疑", f"差事收入 {g('cash','Earned from Jobs','差事收入')} 超过理论上限"))

    # ═══════════════════════════════
    # 1.7 拾取收入 §1.7
    # ═══════════════════════════════
    picked = gn("cash", "Picked up", "拾取收入")
    if online > 3600 and picked > 0:
        # 理论最多拾取速率约 50W/h（挂钱袋）
        if picked > (online / 3600) * 5_000_000 + 500_000:
            findings.append(("存疑", f"拾取收入 {g('cash','Picked up','拾取收入')} 异常高"))

    # ═══════════════════════════════
    # 1.8 分成收入/给予他人 §1.8
    # ═══════════════════════════════
    given = gn("cash", "Given to others", "给予他人")
    received = gn("cash", "Received from others", "分成收入")
    if given > 0 or received > 0:
        findings.append(("存疑", f"存在早期数据：给予{given:,.0f}/分成{received:,.0f}"))

    # ═══════════════════════════════
    # 1.10 卖车收入 §1.10
    # ═══════════════════════════════
    sell_car = gn("cash", "Earned from selling vehicles", "出售载具收入")
    car_expense = gn("cash", "Spent on vehicles & maintenance", "载具和维护花费")
    if online > 3600 and sell_car > 0 and car_expense > 0:
        # 正常卖车收入应远小于维护花费
        if sell_car > car_expense * 3 and sell_car > 10_000_000:
            findings.append(("存疑", f"卖车收入 {g('cash','Earned from selling vehicles','出售载具收入')} 与维护花费不成比例"))

    # ═══════════════════════════════
    # 1.11 其他收入（表现良好奖励）§1.11
    # ═══════════════════════════════
    good_sport = gn("cash", "Earned from Good Sport reward", "表现良好奖励")
    if online > 3600 and good_sport > 0:
        # 2000/游戏日 ≈ 2000/48min
        max_reward = (online / 2880) * 2000
        if good_sport > max_reward * 3:
            findings.append(("存疑", f"表现良好奖励偏高（应有≤{max_reward:,.0f}，实际{good_sport:,.0f}）"))

    # ═══════════════════════════════
    # 1.9 下注收入 §1.9
    # ═══════════════════════════════
    bet_earn = gn("cash", "Earned from betting", "下注收入")
    bet_spent = gn("cash", "Spent on betting", "下注花费")
    if bet_earn > bet_spent * 10 and bet_earn > 1_000_000:
        findings.append(("存疑", f"下注收入异常（赚{bet_earn:,.0f}/花{bet_spent:,.0f}）"))

    # ═══════════════════════════════
    # 2.1 在线时长不一致 §2.1
    # ═══════════════════════════════
    char_time = gs("general", "Time played as character", "角色使用时间")
    gta_time = online  # from career
    if gta_time > 3600 and char_time > 3600:
        diff = abs(gta_time - char_time)
        if diff / max(gta_time, 1) > 0.1:
            findings.append(("存疑", f"在线时长不一致: GTA{gta_time/3600:.0f}h vs 角色{char_time/3600:.0f}h"))

    # ═══════════════════════════════
    # 2.2 每日在线时长 §2.2 — 用户要求关闭
    # ═══════════════════════════════
    # DISABLED

    # ═══════════════════════════════
    # 3.1 升级速度 §3.1
    # ═══════════════════════════════
    rank = overview.get("rank")
    if rank and char_time > 3600:
        rank_n = parse_number(rank)
        # 仙人掌35W/h + 收集品5W/h = 40W RP/h 理论最大
        needed_rp = rank_n * 80000  # 粗略
        rp_per_h = needed_rp / (char_time / 3600) if char_time > 0 else 0
        if rp_per_h > 500_000:
            findings.append(("存疑", f"升级速度异常 Lv.{rank_n}（{rp_per_h/10000:.0f}W RP/h）"))

    # ═══════════════════════════════
    # 4.2 玩家爆头击杀数被修改 §4.2
    # ═══════════════════════════════
    pk = gn("combat", "Player kills", "玩家击杀数")
    ph = gn("combat", "Player headshot kills", "玩家爆头击杀数")
    if ph > pk and pk >= 0:
        findings.append(("异常", f"爆头击杀({ph:,.0f}) > 玩家击杀({pk:,.0f})"))

    # ═══════════════════════════════
    # 4.3 玩家击杀数被修改 §4.3
    # ═══════════════════════════════
    tpk = gn("career", "Total players killed", "杀死的玩家总数")
    if tpk > 0 and pk > tpk * 1.5:
        findings.append(("存疑", f"玩家击杀不一致: 角色{pk:,.0f} vs 总计{tpk:,.0f}"))

    # ═══════════════════════════════
    # 4.4 PVP 爆头率 §4.4
    # ═══════════════════════════════
    if pk > 100 and ph > 0:
        rate = ph / pk * 100
        if rate > 90:
            findings.append(("存疑", f"PVP爆头率 {rate:.0f}%"))
        elif rate > 70:
            findings.append(("存疑", f"PVP爆头率偏高 {rate:.0f}%"))

    # ═══════════════════════════════
    # 4.5 K/D §4.5
    # ═══════════════════════════════
    pd = gn("career", "Total deaths by players", "被其他玩家杀死的总次数")
    if pk > 100 and pd > 0:
        kd = pk / pd
        if kd > 50: findings.append(("异常", f"KD比 {kd:.1f}"))
        elif kd > 10: findings.append(("存疑", f"KD比 {kd:.1f}"))

    # ═══════════════════════════════
    # 4.6 玩家击杀数不一致 §4.6
    # ═══════════════════════════════
    if pk > 0 and tpk > 0:
        diff_pct = abs(pk - tpk) / max(tpk, 1) * 100
        if diff_pct > 30 and abs(pk - tpk) > 50:
            findings.append(("存疑", f"击杀数不一致 {diff_pct:.0f}%（角色{pk:,.0f} vs 总计{tpk:,.0f}）"))

    # ═══════════════════════════════
    # 4.7 PVP 击杀速度 §4.7
    # ═══════════════════════════════
    if pk > 100 and char_time > 3600:
        kph = pk / (char_time / 3600)
        if kph > 200:
            findings.append(("存疑", f"PVP击杀速度 {kph:.0f}/h"))

    # ═══════════════════════════════
    # 4.8 PVE 爆头率 §4.8
    # ═══════════════════════════════
    headshots = gn("combat", "Headshot kills", "爆头击杀数")
    armed = gn("combat", "Armed kills", "持枪击杀数")
    if armed > 1000 and headshots > 0:
        pve_hs = headshots / armed * 100
        if pve_hs > 80:
            findings.append(("存疑", f"PVE爆头率 {pve_hs:.0f}%"))

    # ═══════════════════════════════
    # 5.1 陆上载具最高速度 §5.1 — 用户要求关闭
    # ═══════════════════════════════
    # DISABLED

    # ═══════════════════════════════
    # 6.1/6.2 飞车特技 §6.1 §6.2
    # ═══════════════════════════════
    found_raw = g("vehicles", "Unique Stunt Jumps found", "发现飞车特技地点")
    done_raw = g("vehicles", "Unique Stunt Jumps completed", "完成飞车特技")
    if found_raw:
        # 格式 "50 / 50" 或纯数字
        found_n = parse_number(found_raw.split('/')[0]) if '/' in str(found_raw) else parse_number(found_raw)
        if found_n > 52:  # 分母50+2
            findings.append(("异常", f"发现特技地点 {found_raw} 超过50"))
    if done_raw and found_raw:
        done_n = parse_number(done_raw.split('/')[0]) if '/' in str(done_raw) else parse_number(done_raw)
        found_n = parse_number(found_raw.split('/')[0]) if '/' in str(found_raw) else parse_number(found_raw)
        if done_n > found_n:
            findings.append(("异常", f"完成特技({done_raw})>发现({found_raw})"))

    # ═══════════════════════════════
    # 7.1 最高生存战波数 §7.1
    # ═══════════════════════════════
    survival = g("general", "Highest Survival wave reached", "最高生存战波次")
    if survival:
        sv = parse_number(survival)
        if sv > 100 and sv != 999:
            findings.append(("异常", f"生存战波数 {survival} 异常"))

    # ═══════════════════════════════
    # 7.2 汽车出口数 §7.2
    # ═══════════════════════════════
    exported = gn("vehicles", "Cars exported", "载具出口数")
    stolen = gn("crimes", "Cars stolen", "载具窃取数")
    if exported > stolen and exported > 0:
        findings.append(("异常", f"汽车出口({exported:,.0f})>窃取({stolen:,.0f})"))
    if exported > 0 and char_time > 3600:
        max_export = (char_time / 2400) * 1  # 最多40min/辆
        if exported > max_export * 2:
            findings.append(("存疑", f"汽车出口数 {exported:,.0f} 超出上限"))

    # ═══════════════════════════════
    # 7.3 被通缉次数 §7.3
    # ═══════════════════════════════
    wanted = gn("crimes", "Times Wanted", "被通缉次数")
    stars = gn("crimes", "Wanted stars attained", "被通缉星数")
    if wanted > 0 and stars > 0:
        if stars < wanted or stars > wanted * 5:
            findings.append(("异常", f"通缉数据异常: 次数{wanted:,.0f} 星数{stars:,.0f}"))

    # ═══════════════════════════════
    # 7.4 逃脱通缉 §7.4
    # ═══════════════════════════════
    evaded = gn("crimes", "Wanted stars evaded", "逃脱通缉星数")
    if evaded > stars and stars > 0:
        findings.append(("异常", f"逃脱星数({evaded:,.0f})>被通缉星数({stars:,.0f})"))

    # ═══════════════════════════════
    # 7.5 被通缉时间 §7.5
    # ═══════════════════════════════
    wanted_time = gs("crimes", "Time spent with a Wanted Level", "被通缉时间")
    star5_time = gs("crimes", "Time spent with a 5 star Wanted Level", "五星通缉时间")
    last_wanted = gs("crimes", "Last Wanted Level duration", "上次被通缉时间")
    longest = gs("crimes", "Longest Wanted Level duration", "最长被通缉时间")
    if star5_time > wanted_time and wanted_time > 0:
        findings.append(("异常", "五星通缉时间>总通缉时间"))
    if wanted_time > char_time and char_time > 3600:
        findings.append(("异常", f"通缉时间超过角色在线时长"))
    if last_wanted > longest and longest > 0:
        findings.append(("异常", "上次通缉时间>最长通缉时间"))
    if longest > char_time and char_time > 3600:
        findings.append(("异常", "最长通缉时间>角色在线时长"))

    # ═══════════════════════════════
    # 奖章检测
    # ═══════════════════════════════
    if awards:
        items = awards.get("_items", []) if isinstance(awards, dict) else []
        ad = {}
        for item in items:
            if len(item) >= 4: ad[item[0].lower()] = (item[1], item[2], item[3])

        # 太空猴 - 不可能正好 3000000/3000000
        ac = ad.get("astrochimp")
        if ac and ac[1] == 3000000 and ac[2] == 3000000:
            findings.append(("异常", "太空猴进度 {}/{}（不可能正好达标）".format(ac[1], ac[2])))

        # 犯罪之神 I-IV 只有 0/29 或 1/29
        for cm in ["criminal mastermind", "criminal mastermind ii",
                    "criminal mastermind iii", "criminal mastermind iv"]:
            c = ad.get(cm)
            if c and c[2] >= 29 and c[1] not in (0, 1):
                findings.append(("异常", f"{cm} 进度 {c[1]}/{c[2]}（应为0或1/29）"))

        # 夜店咖 只有 0/4 或 1/4
        cb = ad.get("clubber")
        if cb and cb[2] >= 4 and cb[1] not in (0, 1):
            findings.append(("异常", f"夜店咖进度 {cb[1]}/{cb[2]}（应为0或1/4）"))

        # Rockstar T恤 - 极少数
        rs = ad.get("rockstar t-shirt")
        if rs and rs[1] >= 1:
            findings.append(("存疑", "拥有 Rockstar T恤 奖章（极少数玩家）"))

        # 必达T恤 - 注册晚于2023
        et = ad.get("elitas t-shirt")
        if et and et[1] >= 1 and created:
            try:
                cd = datetime.strptime(str(created).strip(), "%b %d %Y")
                if cd.year >= 2023:
                    findings.append(("异常", f"必达T恤 + 注册{cd.year}年（早于2023才合法）"))
            except ValueError:
                pass

    return findings
