"""开盘预估：开盘前 7 分钟（20:53、8:53）按休市期间的外盘涨跌估一下怎么开，写成网页，右下角弹个通知。

平时不用手动跑：Windows 任务计划「开盘预估」每天 20:53、8:53 自动运行，十几秒跑完（电脑那时没开就不补跑，过了开盘再估没意义）。
全是脚本算的，不调用 AI。
  预估开盘价 = 昨收 ×（1 + 系数 × 外盘涨跌）
    昨收：国内上一次收盘的价（夜盘开盘看 15:00，日盘开盘看夜盘收盘），天勤 1 分钟线；天勤连不上用新浪分钟线。
    外盘涨跌：从这个品种国内收盘那一刻到现在。东方财富 → 雅虎（晚 10 分钟左右，用新浪实时价补上最后几分钟）→ 新浪。
    系数：「复盘/系数历史.csv」里这个品种最近 250 次夜盘开盘，国内跳空 ≈ 系数 × 外盘涨跌（最小二乘，不加截距）。
          日盘攒够 20 次之前借夜盘的；20 号胶没有新加坡胶的历史分钟线，攒够 20 次实盘开盘才有系数。
    八成范围：最近 60 次的误差，中间八成。
    报警：最近 20 次误差的中位数比 250 次的大一倍以上（误差变大），或 20 次里至少 15 次偏同一边（偏一边）。
  外盘分档（小草定的）：0–1% 没波动，1–3% 小，3–6% 大，≥6% 超大；外盘涨、跌分开查历史。
  针在哪边：外盘这个方向、这一档，过去针（竞价价差）和第一次真实成交是同向还是反向（小草定的：外盘涨、价差往上 = 同向，跌、往下也是同向）——
    开盘复盘记下的 tick（2026-10-21 起），和两年日线粗估（复盘/针_日线基线.csv；日线的开盘价不一定是竞价价，只当参考）。
  背景（照抄，不参与计算）：期货速览最新的仓储风险提示（output/仓储/）、长期事件（output/因素/长期事件.json）、
    国内收盘以后的新闻标题（东方财富资讯搜索，按关键词抓、按标题筛，没经过 AI）。
不锈钢这种没有对应外盘的品种不估。
结果：预估/开盘日期_时间.html（网页，通知点开的就是它；没看到通知就去这个文件夹找）、
      数据/开盘日期_时间/预估.csv（复盘对答案用）。

手动跑（在本文件夹里）：
  python 开盘预估.py                          估下一次开盘（离开盘超过 30 分钟不估，加 --force 照估）
  python 开盘预估.py --at "2026-10-21 20:53"    回放：当作现在是那个时候，结果放「复盘/回放/」，不碰正式的
  python 开盘预估.py --prep                   只取仓储数据（跑一次 futures_brief.py stock；任务计划「仓储取数」每个交易日 17:30 自动跑）
  --no-notify  不弹通知
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # 期货数据（期货速览 futures_brief.py 和它的 output/ 在这里）
LOG_FILE = HERE / "运行日志.txt"
if sys.stdout is None:  # 任务计划用 pythonw 运行，没有控制台，输出写进运行日志
    sys.stdout = sys.stderr = open(LOG_FILE, "a", encoding="utf-8", buffering=1)

import 开盘tick as tick  # noqa: E402  天勤连接、合约清单、1 分钟线取昨收（顺带加载日记库环境里的包）
import 开盘复盘 as rv  # noqa: E402  分档、系数、过去的针
import 外盘涨跌 as wp  # noqa: E402  外盘数据源

import pandas as pd  # noqa: E402

FC_FILE = "预估.csv"
SINA_LIVE = {"101.GC*": "GC", "101.SI*": "SI", "112.B*": "OIL"}  # 新浪实时外盘（连续合约）
TOAST_APP = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"  # 借 PowerShell 的身份弹通知
EM_DOWN = []  # 东方财富这次已经连不上，后面的直接用备用源
FC_HEAD = ["开盘", "场次", "组合", "腿1", "腿2", "品种", "外盘", "外盘来源", "外盘时段", "外盘涨跌%", "外盘方向", "分档", "系数",
           "系数说明", "腿1昨收", "腿2昨收", "收盘时间", "腿1到期日", "腿2到期日", "昨收价差", "腿1预估", "腿2预估", "腿2下沿", "腿2上沿",
           "预估价差", "针参考下沿", "针参考中位", "针参考上沿", "预测方向", "仓储提示", "长期事件", "报警", "生成时间"]
# 新闻：东方财富搜索词，和标题里至少要有的一个词（搜索是模糊匹配，不按标题筛会混进很多不相干的）
NEWS = {
    "ag": (["白银", "沪银", "贵金属"], "白银|沪银|银价|贵金属|金银|金价|美联储|降息|加息|非农|美国CPI|美元指数|美债"),
    "au": (["黄金", "沪金", "贵金属"], "黄金期货|沪金|金价|贵金属|金银|美联储|降息|加息|非农|美国CPI|美元指数|美债|购金"),
    "sc": (["原油", "油价", "布伦特"], "原油|油价|布伦特|WTI|欧佩克|OPEC|沙特|伊朗|霍尔木兹|中东|EIA|炼厂|成品油"),
    "fu": (["燃料油", "燃油", "原油"], "燃料油|燃油|高硫|低硫|船燃|原油|油价|布伦特|欧佩克|OPEC|伊朗|霍尔木兹|红海"),
    "nr": (["20号胶", "橡胶", "天然橡胶"], "橡胶|胶价|20号胶|天胶|割胶|泰国|东南亚"),
    "ru": (["橡胶", "天然橡胶", "日胶"], "橡胶|胶价|天胶|割胶|泰国|东南亚|日胶"),
    "cu": (["沪铜", "铜价", "伦铜"], "铜"), "al": (["沪铝", "铝价"], "铝"), "zn": (["沪锌", "锌价"], "锌"),
    "pb": (["沪铅", "铅价"], "铅"), "sn": (["沪锡", "锡价"], "锡"), "ni": (["沪镍", "镍价"], "镍"),
}
NEWS_SKIP = "概念股|涨停|连板|板块|港股|A股"  # 标题里有这些的多是股票新闻
NEWS_N = 6  # 每个品种最多几条


def next_opening(now: datetime) -> datetime:
    """now 之后最近的一次开盘：21:00 或 9:00。"""
    day = now.replace(second=0, microsecond=0)
    cands = [day.replace(hour=h, minute=0) + timedelta(days=d) for d in (0, 1) for h in (9, 21)]
    return min(c for c in cands if c > now)


def has_opening(o: datetime, days: set) -> bool:
    """这次开盘有没有：日盘看今天是不是交易日；夜盘还要明天是下一个交易日（周五晚上对周一），节假日前一晚没有夜盘。"""
    d = o.date()
    if d not in days:
        return False
    if o.hour < 12:
        return True
    nxt = min((x for x in days if x > d), default=None)
    return nxt is not None and ((nxt - d).days == 1 or (d.weekday() == 4 and (nxt - d).days == 3))


def domestic(symbols: list, o: datetime, now: datetime, live: bool) -> dict:
    """天勤：这次开盘有没有、每条腿的昨收和到期日。天勤连不上，昨收用新浪分钟线，交易日按周一到周五猜。"""
    out = {"合约": {s: {} for s in symbols}, "有开盘": None, "交易日": None, "备注": []}
    try:
        api = tick.connect(tries=1)
    except Exception as e:
        api = None
        out["备注"].append(f"天勤没连上（{type(e).__name__}），昨收用的新浪分钟线，节假日判断不了")
    if api is not None:
        try:
            cal = api.get_trading_calendar(start_dt=(o - timedelta(days=20)).date(), end_dt=(o + timedelta(days=20)).date())
            out["交易日"] = {d.date() for d in cal[cal["trading"]]["date"]}
            out["有开盘"] = has_opening(o, out["交易日"])
            for s in symbols:
                try:
                    k = tick.minute_klines(api, s, 400 if live else 10000)
                    out["合约"][s] |= tick.contract_info(api, s)
                    c = tick.close_before(k, o, None if live else now)
                    if c:
                        out["合约"][s] |= {"昨收": c[0], "收盘时间": c[1], "昨收来源": "天勤"}
                except Exception as e:
                    out["备注"].append(f"{s} 天勤没取到昨收：{type(e).__name__} {e}")
        finally:
            api.close()
    for s in symbols:
        if "昨收" in out["合约"][s]:
            continue
        try:  # 新浪：最近 1023 根，时间是这一分钟结束的时刻
            bars = [b for b in wp.sina_bars(s.split(".")[1].upper(), domestic=True)
                    if b[0] <= o - timedelta(minutes=1) and (live or b[0] <= now)]
            if bars:
                out["合约"][s] |= {"昨收": bars[-1][2], "收盘时间": bars[-1][0], "昨收来源": "新浪"}
        except Exception as e:
            out["备注"].append(f"{s} 新浪也没取到昨收：{type(e).__name__} {e}")
    if out["有开盘"] is None:
        out["有开盘"] = o.weekday() < 5
    return out


def load_overseas(spec: str, live: bool):
    """外盘分钟线：东方财富 → 雅虎 → 新浪连续。雅虎晚 10 分钟左右，现场预估时另取新浪实时补最后几分钟。
    返回 dict（外盘、数据源、分钟线……）或出错原因。"""
    label = next(v[2] for v in wp.VARIETIES.values() if v[3] == spec)
    errs, m = [], None
    if EM_DOWN:
        errs.append("东方财富连不上")
    else:
        try:
            secid = wp.main_contract(spec)
            name, bars = wp.minute_bars(secid)
            month = re.search(r"\d{4}$", name)
            m = {"数据源": f"东方财富 {secid}", "外盘": label + (month.group() if month else ""), "bars": bars, "备注": ""}
        except Exception as e:
            errs.append(f"东方财富 {type(e).__name__}")
            if isinstance(e, wp.NetError):
                EM_DOWN.append(1)
    if m is None and spec in wp.BACKUP:
        try:
            src, name, bars, _, note = wp.backup_market(spec, label)
            m = {"数据源": src, "外盘": name, "bars": bars, "备注": note}
        except Exception as e:
            errs.append(f"备用源 {type(e).__name__} {e}")
    if m is None and spec in SINA_LIVE:
        try:
            m = {"数据源": f"新浪 {SINA_LIVE[spec]}", "外盘": label + "（连续）", "bars": wp.sina_bars(SINA_LIVE[spec]),
                 "备注": "东方财富、雅虎都没取到，用的新浪连续合约（换月那天不准）"}
        except Exception as e:
            errs.append(f"新浪 {type(e).__name__}")
    if m is None:
        return "；".join(errs + ([] if spec in wp.BACKUP or spec in SINA_LIVE else [f"{label}没有备用源"]))
    m["times"] = [b[0] for b in m["bars"]]
    if live and spec in SINA_LIVE and not m["数据源"].startswith(("东方财富", "新浪")):
        try:
            sb = wp.sina_bars(SINA_LIVE[spec])
            m["实时"] = (sb, [b[0] for b in sb], SINA_LIVE[spec])
        except Exception:
            pass
    return m


def overseas_move(m, start: datetime, now: datetime, live: bool):
    """国内收盘 start → now 的外盘涨跌。现场预估时把正在走的这一分钟也算上。"""
    if isinstance(m, str):
        return None, f"外盘没取到：{m}"
    end = now + timedelta(minutes=1) if live else now
    a, b = wp.price_at(m["bars"], m["times"], start), wp.price_at(m["bars"], m["times"], end)
    if a is None or b is None or not a[2] or not b[2]:
        return None, f"外盘数据不够早（{m['数据源']} 最早到 {m['times'][0]:%m-%d %H:%M}）"
    r, end_t, src = b[2] / a[2], b[0], m["数据源"]
    if "实时" in m and b[0] < now - timedelta(minutes=2):  # 雅虎晚了：b 之后的涨跌用新浪实时补上
        sb, st, code = m["实时"]
        s1, s2 = wp.price_at(sb, st, b[0]), wp.price_at(sb, st, end)
        if s1 and s2 and s1[2] and s2[2] and s1[0] >= b[0] - timedelta(minutes=3) and s2[0] > s1[0]:
            r *= s2[2] / s1[2]
            src += f"，{b[0]:%H:%M} 以后用新浪实时 {code} 补到 {min(s2[0], now):%H:%M}"
            end_t = min(s2[0], now)
        else:
            src += f"（只到 {b[0]:%H:%M}，新浪实时没补上）"
    return {"外盘": m["外盘"], "外盘涨跌%": round((r - 1) * 100, 3), "起点": a[0], "终点": min(end_t, now),
            "来源": src, "备注": m.get("备注", "")}, ""


def round_tick(x: float, t) -> float:
    t = t or 1
    return round(round(x / t) * t, 4)


# ---------- 背景：新闻、仓储、长期事件（照抄，不参与计算） ----------

def fetch_news(products: list, since: datetime, now: datetime) -> dict:
    """东方财富资讯搜索（用期货速览 futures_brief.py 里的搜索函数）：每个品种几个搜索词，标题筛一遍、去重，按时间倒序。
    返回 {品种: [新闻]}；出错的写进「_错误」。"""
    out, cache, errs = {}, {}, []
    try:
        if str(ROOT) not in sys.path:
            sys.path.append(str(ROOT))
        import futures_brief as fb
    except Exception as e:
        return {"_错误": [f"futures_brief.py 导入失败：{type(e).__name__} {e}"]}
    for p in products:
        if p not in NEWS:
            continue
        kws, must = NEWS[p]
        seen, items = set(), []
        for kw in kws:
            if kw not in cache:
                cache[kw] = []
                try:
                    for page in (1, 2):  # 一页 50 条；常见词一页只够几个小时，不够就翻第二页
                        r = fb.search_eastmoney(kw, page_size=50, page=page)
                        cache[kw] += r
                        if not r or r[-1]["time"][:16] < f"{since:%Y-%m-%d %H:%M}":
                            break
                except Exception as e:
                    errs.append(f"「{kw}」{type(e).__name__} {e}")
            for it in cache[kw]:
                try:
                    t = datetime.strptime(it["time"][:19], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                key = re.sub(r"\W", "", it["title"])
                if not since <= t <= now or key in seen or not re.search(must, it["title"]) or re.search(NEWS_SKIP, it["title"]):
                    continue
                seen.add(key)
                items.append(it | {"t": t})
        out[p] = sorted(items, key=lambda x: x["t"], reverse=True)
    if errs:
        out["_错误"] = errs
    return out


def load_warehouse(now: datetime) -> dict:
    """期货速览最新一份仓储趋势（output/仓储/日期/仓储趋势_日期.md）的风险提示，按品种代码照抄。回放时只看那天以前的。"""
    files = [f for f in sorted((ROOT / "output" / "仓储").glob("????-??-??/仓储趋势_*.md")) if f.parent.name <= f"{now:%Y-%m-%d}"]
    if not files:
        return {}
    f = files[-1]
    text = f.read_text(encoding="utf-8")
    m = re.search(r"截至 (\d{4}-\d{2}-\d{2})", text)
    parts = text.split("## 风险提示", 1)
    tips = {}
    if len(parts) == 2:
        for block in parts[1].split("\n## ", 1)[0].split("\n### ")[1:]:
            title, *lines = block.strip().splitlines()
            c = re.search(r"\s([a-z]{1,3})（", title)
            if c:
                tips[c.group(1)] = [x[2:].strip() for x in lines if x.startswith("- ")]
    return {"日期": m.group(1) if m else f.parent.name, "文件": f, "提示": tips,
            "自检": [x.strip("> ").strip() for x in parts[0].splitlines() if "⚠" in x]}


def data_day(o: datetime, days) -> str:
    """这次开盘前应该能看到哪天的仓单日报：夜盘看当天（下午三点半以后发），日盘看上一个交易日。"""
    d = o.date()
    if o.hour >= 20:
        return f"{d:%Y-%m-%d}"
    prev = [x for x in days or [] if x < d]
    if prev:
        return f"{max(prev):%Y-%m-%d}"
    d -= timedelta(days=1)
    while d.weekday() >= 5:  # 没有交易日历，按周一到周五猜
        d -= timedelta(days=1)
    return f"{d:%Y-%m-%d}"


def load_events() -> tuple[list, str]:
    """长期事件（output/因素/长期事件.json，影响因素那边 AI 按新闻维护的）：进行中、缓和的。"""
    f = ROOT / "output" / "因素" / "长期事件.json"
    try:
        ev = json.loads(f.read_text(encoding="utf-8")).get("事件", [])
    except (OSError, ValueError):
        return [], ""
    ev = [e for e in ev if e.get("状态") in ("进行中", "缓和")]
    return ev, max((str(e.get("更新日期", "")) for e in ev), default="")


def forecast(o: datetime, now: datetime, live: bool) -> dict:
    session = rv.session_of(o)
    pairs = [tuple(x.split(",")) for x in tick.read_list() if "," in x]
    use = [(s1, s2) for s1, s2 in pairs if rv.product_of(s1) in wp.VARIETIES]
    skipped = [f"{s1.split('.')[-1]}-{s2.split('.')[-1]}" for s1, s2 in pairs if rv.product_of(s1) not in wp.VARIETIES]
    products = list(dict.fromkeys(rv.product_of(s1) for s1, _ in use))
    specs = list(dict.fromkeys(wp.VARIETIES[p][3] for p in products))
    symbols = list(dict.fromkeys(s for pr in use for s in pr))
    with ThreadPoolExecutor(2) as ex:  # 外盘、新闻和天勤同时取；外盘按顺序取（数据源的重试状态是共用的）
        job = ex.submit(lambda: {s: load_overseas(s, live) for s in specs})
        news_job = ex.submit(fetch_news, products, now - timedelta(hours=20), now)
        dom = domestic(symbols, o, now, live)
        markets, news = job.result(), news_job.result()
    wh, (events, ev_day) = load_warehouse(now), load_events()
    if wh:
        wh["应有"] = data_day(o, dom["交易日"])
    res = {"开盘": o, "现在": now, "场次": session, "回放": not live, "有开盘": dom["有开盘"], "备注": dom["备注"],
           "不估": skipped, "品种": [], "仓储": wh, "长期事件更新": ev_day, "新闻错误": news.get("_错误", [])}
    coef, rec = rv.load_coef(), rv.read_csv(rv.REC_FILE)
    for p in products:
        name = wp.VARIETIES[p][0]
        legs = [(s1, s2) for s1, s2 in use if rv.product_of(s1) == p]
        main = legs[0][1]
        info = dom["合约"][main]
        start = info.get("收盘时间") or now - timedelta(hours=12)
        item = {"品种": p, "名称": name, "组合": [], "腿": {}, "报警": [], "提示": [], "外盘方向": "", "分档": "",
                "仓储": wh["提示"].get(p, []) if wh else None, "长期事件": [e for e in events if p in e.get("影响品种", [])],
                "新闻": [x for x in news.get(p, []) if x["t"] >= start][:NEWS_N], "新闻起": start}
        if "昨收" not in info:
            item["提示"].append(f"{main} 没有昨收价，不估")
            res["品种"].append(item)
            continue
        if live and now - start < timedelta(minutes=2):
            item["提示"].append("国内这一节还没收盘，昨收用的是现在的价")
        mv, why = overseas_move(markets[wp.VARIETIES[p][3]], start, now, live)
        item["外盘"] = mv
        if mv is None:
            item["提示"].append(why)
        move = mv["外盘涨跌%"] if mv else None
        item["分档"], item["外盘方向"] = rv.bucket(move), rv.direction_of(move)
        group = f"外盘{item['外盘方向']}·{item['分档']}" if item["外盘方向"] else ""
        f = rv.fit(coef, p, session, o, move)
        item["系数"] = f
        if f is None:
            item["提示"].append("还没有系数：" + ("新加坡 20 号胶没有历史分钟线，攒够 20 次实盘开盘才有" if p == "nr"
                                                 else "系数历史里不到 20 次开盘"))
        else:
            item["报警"] = f["报警"]
            if f["说明"]:
                item["提示"].append(f["说明"])
        if item["分档"] == "超大":
            s = (f"（{group}这一档过去 {f['这一档次数']} 次，国内跟了 {rv.fmt(f['这一档跟了'], 2)} 倍，中位）"
                 if f and f.get("这一档次数") else f"（{group}这一档过去没有样本）")
            item["提示"].append(f"外盘超大波动：历史上国内往往只跟一部分{s}，预估可能偏大")
        gap = f["系数"] * move if f and move is not None else None  # 预估跳空 %
        item["预估跳空%"] = gap
        for s in dict.fromkeys(x for pr in legs for x in pr):
            c = dom["合约"][s]
            leg = {"昨收": c.get("昨收"), "收盘时间": c.get("收盘时间"), "昨收来源": c.get("昨收来源", ""),
                   "到期日": c.get("到期日", ""), "tick": c.get("最小变动价位") or 1}
            if leg["昨收"] is not None and gap is not None:
                leg["预估"] = round_tick(leg["昨收"] * (1 + gap / 100), leg["tick"])
                leg["下沿"] = round_tick(leg["昨收"] * (1 + (gap + f["误差下沿%"]) / 100), leg["tick"])
                leg["上沿"] = round_tick(leg["昨收"] * (1 + (gap + f["误差上沿%"]) / 100), leg["tick"])
            if leg["到期日"]:
                leg["离到期"] = (datetime.strptime(leg["到期日"], "%Y-%m-%d").date() - o.date()).days
            item["腿"][s] = leg
        for s1, s2 in legs:
            l1, l2 = item["腿"][s1], item["腿"][s2]
            off = rv.offset_of(s1, s2)
            row = {"组合": f"{s1.split('.')[-1]}-{s2.split('.')[-1]}", "腿1": s1, "腿2": s2, "相对主力": off}
            if l1["昨收"] is not None and l2["昨收"] is not None:
                row["昨收价差"] = round(l1["昨收"] - l2["昨收"], 4)
                if "预估" in l1 and "预估" in l2:
                    row["预估价差"] = round(l1["预估"] - l2["预估"], 4)
                base = rv.needle_base(p, off, item["外盘方向"], item["分档"])
                row["日线"] = base
                if base:  # 针 = 昨收价差 + 外盘这个方向、这一档的针变化（占主力昨收 %）× 主力昨收
                    row["针参考"] = tuple(round(row["昨收价差"] + base[k] * l2["昨收"] / 100, 2)
                                       for k in ("针变化P10%", "针变化中位%", "针变化P90%"))
                    # 预测方向：两年日线里外盘这个方向、这一档，针变化的中位数往哪边（价差比昨收往上 / 往下 / 不变）
                    mid = round(base["针变化中位%"] * l2["昨收"] / 100, 2)
                    row["预测方向"] = "往上" if mid > 0 else "往下" if mid < 0 else "不变"
            row["tick"] = rv.tick_history(rec, p, off, item["外盘方向"], item["分档"], o)
            item["组合"].append(row)
        res["品种"].append(item)
    return res


# ---------- 输出 ----------

def write_csv(res: dict, folder: Path) -> None:
    rows = []
    for it in res["品种"]:
        mv, f = it.get("外盘"), it.get("系数")
        for row in it["组合"]:
            l1, l2 = it["腿"][row["腿1"]], it["腿"][row["腿2"]]
            nr = row.get("针参考") or ("", "", "")
            rows.append({
                "开盘": f"{res['开盘']:%Y-%m-%d %H:%M}", "场次": res["场次"], "组合": row["组合"], "腿1": row["腿1"], "腿2": row["腿2"],
                "品种": it["品种"], "外盘": mv["外盘"] if mv else "", "外盘来源": mv["来源"] if mv else "",
                "外盘时段": f"{mv['起点']:%H:%M}→{mv['终点']:%H:%M}" if mv else "",
                "外盘涨跌%": mv["外盘涨跌%"] if mv else "", "外盘方向": it["外盘方向"], "分档": it["分档"],
                "系数": round(f["系数"], 4) if f else "",
                "系数说明": f"最近 {f['次数']} 次{'夜盘（日盘借夜盘的）' if f['说明'] else res['场次']}开盘" if f else "",
                "腿1昨收": l1["昨收"], "腿2昨收": l2["昨收"],
                "收盘时间": f"{l2['收盘时间']:%Y-%m-%d %H:%M}" if l2.get("收盘时间") else "",
                "腿1到期日": l1["到期日"], "腿2到期日": l2["到期日"], "昨收价差": row.get("昨收价差", ""),
                "腿1预估": l1.get("预估", ""), "腿2预估": l2.get("预估", ""), "腿2下沿": l2.get("下沿", ""), "腿2上沿": l2.get("上沿", ""),
                "预估价差": row.get("预估价差", ""), "针参考下沿": nr[0], "针参考中位": nr[1], "针参考上沿": nr[2], "预测方向": row.get("预测方向", ""),
                "仓储提示": "；".join(it["仓储"] or []), "长期事件": "、".join(e["名称"] for e in it["长期事件"]),
                "报警": "；".join(it["报警"]), "生成时间": f"{datetime.now():%Y-%m-%d %H:%M:%S}"})
    wp.write_rows(folder / FC_FILE, FC_HEAD, rows)


def sides_text(d: dict) -> str:
    if not d:
        return ""
    n = sum(d.values())
    if not n:
        return "没有"
    return f"同向 {d.get('同向', 0)}、反向 {d.get('反向', 0)}" + (f"、不变 {d['不变']}" if d.get("不变") else "")


def bullets(items: list) -> str:
    return "<ul class='list'>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>"


def background(it: dict, res: dict) -> str:
    """品种卡片下面的背景：仓储提示、长期事件、新闻（都是照抄，不参与计算）。"""
    out, wh = [], res["仓储"]
    if wh:
        src = f"上期所 {wh['日期'][5:]} 的仓单日报、库存周报，期货速览的风险提示原样照抄"
        out.append(f"<h4>仓储提示（{rv.esc(src)}）</h4>")
        out.append(bullets([rv.esc(x) for x in it["仓储"]]) if it["仓储"] else "<p class='note'>未触发风险规则</p>")
    else:
        out.append("<h4>仓储提示</h4><p class='note'>没有找到期货速览的仓储趋势文件（output/仓储/）</p>")
    if it["长期事件"]:
        out.append(f"<h4>长期事件（影响因素那边 AI 按新闻维护的，最后更新 {rv.esc(res['长期事件更新'])}）</h4>")
        lines = []
        for e in it["长期事件"]:
            links = " ".join(f"<a href='{rv.esc(x.get('链接', ''))}'>{rv.esc(x.get('标题', '链接'))}</a>"
                             for x in (e.get("链接") or [])[:2] if x.get("链接"))
            lines.append(f"<b>{rv.esc(e['名称'])}</b>（{rv.esc(e.get('状态', ''))}，{rv.esc(e.get('更新日期', ''))} 更新）："
                         f"{rv.esc(e.get('最新进展', ''))} {links}")
        out.append(bullets(lines))
    out.append(f"<h4>新闻（{it['新闻起']:%m-%d %H:%M} 国内收盘以后，按关键词抓、按标题筛，没经过 AI，不一定相关）</h4>")
    if it["新闻"]:
        out.append(bullets([f"{x['t']:%m-%d %H:%M} {rv.esc(x['source'] or '—')}："
                            f"<a href='{rv.esc(x['url'])}'>{rv.esc(x['title'])}</a>" for x in it["新闻"]]))
    else:
        out.append("<p class='note'>没有搜到" + ("（回放时只搜得到最近的新闻）" if res["回放"] else "") + "</p>")
    return "".join(out)


def build_page(res: dict, page_file: Path) -> str:
    o, now = res["开盘"], res["现在"]
    lead = int(round((o - now).total_seconds() / 60))
    review = Path(os.path.relpath(rv.PAGE, page_file.parent)).as_posix()
    body = [f"<h1>开盘预估 · {o:%m-%d %H:%M} {res['场次']}</h1>",
            f"<p class='meta'>{'回放：当作 ' if res['回放'] else ''}{now:%m-%d %H:%M} 估的（离开盘 {lead} 分钟）· 生成于 {datetime.now():%H:%M:%S}"
            f" · 预估 = 昨收 ×（1 + 系数 × 外盘涨跌）· <a href='{review}'>开盘复盘</a></p>"]
    if not res["有开盘"]:
        body.append("<div class='warn'>交易日历显示这次没有开盘（周末、节假日或节前一晚），下面的数只当练习。</div>")
    for x in res["备注"]:
        body.append(f"<div class='warn'>{rv.esc(x)}</div>")
    wh = res["仓储"]
    if wh and wh["日期"] < wh["应有"]:
        body.append(f"<div class='warn'>仓储数据是 {wh['日期']} 的，{wh['应有']} 的还没取到"
                    "（任务计划「仓储取数」每个交易日 17:30 跑 futures_brief.py stock，可能没跑或交易所还没发）。</div>")
    for x in (wh or {}).get("自检", []):
        body.append(f"<div class='warn'>期货速览的自检警告（原样照抄）：{rv.esc(x)}</div>")
    if res["新闻错误"]:
        body.append(f"<div class='warn'>新闻有一部分没搜到：{rv.esc('；'.join(res['新闻错误']))}</div>")
    rows = []  # 总览：一眼看完
    for it in res["品种"]:
        mv, f = it.get("外盘"), it.get("系数")
        main = it["组合"][0]["腿2"] if it["组合"] else ""
        leg = it["腿"].get(main, {})
        flags = [x.split("：")[0] for x in it["报警"]]
        flags += ["超大波动，国内往往只跟一部分"] if it["分档"] == "超大" else []
        flags += ["外盘没取到"] if not mv else []
        flags += ["还没有系数"] if f is None else (["日盘借夜盘系数"] if f["说明"] else [])
        flags += [f"{s.split('.')[-1]} 临近到期" for s, l in it["腿"].items() if l.get("离到期", 99) <= 5]
        high = sum("【高】" in x for x in it["仓储"] or [])
        flags += [f"仓储【高】{high} 条"] if high else []
        move = rv.Raw(f"<span class='{'up' if mv['外盘涨跌%'] > 0 else 'down' if mv['外盘涨跌%'] < 0 else ''}'>"
                      f"{rv.fmt(mv['外盘涨跌%'], 2, plus=True)}%</span>") if mv else "没取到"
        rng = f"{rv.fmt(leg['下沿'], 2)} ~ {rv.fmt(leg['上沿'], 2)}" if "下沿" in leg else ""
        rows.append([f"{it['名称']} {it['品种']}", move, f"{it['外盘方向']}·{it['分档']}" if it["外盘方向"] else it["分档"],
                     main.split(".")[-1], rv.fmt(leg.get("昨收"), 2),
                     rv.Raw(f"<span class='big'>{rv.fmt(leg.get('预估'), 2)}</span>"), rng, "、".join(flags)])
    body.append(rv.table(["品种", "外盘", "方向·分档", "主力", "昨收", "预估开盘", "八成落在", "留意"], rows))
    for it in res["品种"]:
        mv, f = it.get("外盘"), it.get("系数")
        group = f"外盘{it['外盘方向']}·{it['分档']}" if it["外盘方向"] else ""
        head = f"{it['名称']} {it['品种']}"
        if mv:
            head += f" · {mv['外盘']} {rv.fmt(mv['外盘涨跌%'], 2, plus=True)}%（{group or it['分档']}）"
        card = [f"<div class='card'><h3>{rv.esc(head)}</h3>"]
        if mv:
            card.append(f"<p class='note'>外盘 {mv['起点']:%m-%d %H:%M} → {mv['终点']:%H:%M}（国内收盘 → 现在）· {rv.esc(mv['来源'])}"
                        f"{'；' + rv.esc(mv['备注']) if mv['备注'] else ''}</p>")
        if f:
            ext = ""
            if f.get("这一档次数"):
                ext = f"；{group}这一档过去 {f['这一档次数']} 次国内跟了 {rv.fmt(f['这一档跟了'], 2)} 倍（中位）"
            card.append(f"<p class='note'>系数 <b>{f['系数']:.2f}</b>（最近 {f['次数']} 次{'夜盘' if f['说明'] else res['场次']}开盘，"
                        f"{f['起']:%Y-%m} 起，{rv.esc(f['来源'])}）→ 主力预估跳空 <b>{rv.fmt(it['预估跳空%'], 2, plus=True)}%</b>"
                        f"；八成范围 {f['误差下沿%']:+.2f}% ~ {f['误差上沿%']:+.2f}%{ext}</p>")
        for x in it["报警"]:
            card.append(f"<div class='warn'>⚠ {rv.esc(x)}</div>")
        for x in it["提示"]:
            card.append(f"<div class='warn'>{rv.esc(x)}</div>")
        rows = []
        for s, l in it["腿"].items():
            rng = f"{rv.fmt(l['下沿'], 2)} ~ {rv.fmt(l['上沿'], 2)}" if "下沿" in l else ""
            exp = f"{l['离到期']} 天" if "离到期" in l else ""
            if "离到期" in l and l["离到期"] <= 5:
                exp += "（临近到期）"
            rows.append([s.split(".")[-1], rv.fmt(l["昨收"], 2), rv.Raw(f"<span class='big'>{rv.fmt(l.get('预估'), 2)}</span>"), rng, exp])
        if rows:
            card.append(rv.table(["合约", "昨收", "预估开盘", "八成落在", "离到期"], rows))
        rows = []
        for row in it["组合"]:
            base, th = row.get("日线"), row["tick"]
            daily = f"同向 {base['同向%']:.0f}%、反向 {base['反向%']:.0f}%（{int(base['次数'])} 次）" if base else ""
            nr = row.get("针参考")
            needle = f"{rv.fmt(nr[0], 2)} ~ {rv.fmt(nr[2], 2)}（中位 {rv.fmt(nr[1], 2)}）" if nr else ""
            n = th.get("次数", 0)
            rows.append([row["组合"], rv.fmt(row.get("昨收价差"), 2), rv.fmt(row.get("预估价差"), 2), daily, needle,
                         row.get("预测方向", ""),
                         f"{sides_text(th.get('针方向'))}（{n} 次）" if n else "还没有",
                         f"{sides_text(th.get('首笔方向'))}（{n} 次）" if n else "还没有"])
        if rows and group:
            card.append(f"<p class='note'>下面按「{group}」查历史：</p>")
        if rows:
            card.append(rv.table(["组合", "昨收价差", "预估价差", "两年日线：针", "针八成落在（日线粗估）", "价差预测方向",
                                  "tick 记录：针", "tick 记录：第一次真实成交"], rows))
        card.append(background(it, res) + "</div>")
        body.append("".join(card))
    if res["不估"]:
        body.append(f"<p class='note'>不估：{rv.esc('、'.join(res['不估']))}（没有对应的外盘）。</p>")
    body.append(
        "<div class='card'><h3>怎么看</h3><p class='note'>"
        "外盘分档（小草定的）：0–1% 没波动，1–3% 小，3–6% 大，≥6% 超大；外盘涨、跌分开查历史。<br>"
        "同向 / 反向（小草定的）：外盘涨、价差（腿1 − 腿2，次主力 − 主力）比昨收价差往上 = 同向，外盘跌、价差往下也是同向；"
        "反过来是反向。队里的说法是大涨大跌时第一次真实成交同向，小草觉得同向基本只在超大波动，平常反向多一点——记分卡在开盘复盘页。<br>"
        "针八成落在：外盘这个方向、这一档，两年日线里针的变化（按主力昨收换算）加在昨收价差上；"
        "日线的开盘价不一定是竞价价，冷门合约更是，只当参考。"
        "tick 记录是 2026-10-21 起每次开盘真的记下来的，少于 10 次只是线索。<br>"
        "仓储提示、长期事件、新闻只是背景，照抄期货速览和新闻网站，不参与计算。<br>"
        "这些都是历史统计，不是预测，也不是买卖建议。</p></div>")
    return rv.page(f"开盘预估 {o:%m-%d %H:%M}", "".join(body))


def toast_lines(res: dict) -> list[str]:
    parts = []
    for it in res["品种"]:
        mv = it.get("外盘")
        if not mv:
            parts.append(f"{it['名称']} 外盘没取到")
            continue
        main = it["组合"][0]["腿2"] if it["组合"] else ""
        leg = it["腿"].get(main, {})
        s = f"{it['名称']} {rv.fmt(mv['外盘涨跌%'], 2, plus=True)}%{it['分档']}"
        if "预估" in leg:
            s += f"→{main.split('.')[-1][-4:]} {rv.fmt(leg['预估'], 2)}"
        if it["报警"]:
            s += "⚠"
        parts.append(s)
    return ["；".join(parts[:2]), "；".join(parts[2:])] if len(parts) > 2 else ["；".join(parts)]


def notify(title: str, lines: list[str], page: Path) -> None:
    """右下角弹通知（不抢焦点、不弹窗口），点一下打开网页。"""
    xml = (f'<toast activationType="protocol" launch="{xml_escape(page.resolve().as_uri())}"><visual><binding template="ToastGeneric">'
           f"<text>{xml_escape(title)}</text>" + "".join(f"<text>{xml_escape(x)}</text>" for x in lines if x)
           + "</binding></visual></toast>")
    ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null\n"
          "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null\n"
          "$x = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
          f"$x.LoadXml(@'\n{xml}\n'@)\n"
          "$t = [Windows.UI.Notifications.ToastNotification]::new($x)\n"
          f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{TOAST_APP}').Show($t)\n")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-EncodedCommand", base64.b64encode(ps.encode("utf-16-le")).decode()],
                       capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode:
        print(f"通知没弹出来：{r.stderr.decode('gbk', errors='replace')[:300]}")


def prep() -> None:
    """任务计划「仓储取数」（每个交易日 17:30）：跑一次期货速览的 stock，把当天的仓单日报、库存周报取下来，晚上的预估直接读。"""
    t0 = time.time()
    print(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 仓储取数（futures_brief.py stock）=====")
    py = Path(sys.executable).with_name("python.exe")  # 任务计划用的是 pythonw，同一个文件夹里的 python 才有输出
    try:  # 先算日内参考区间（天勤分钟线），stock 生成跨月价差表时一起显示；失败不影响取仓储
        r = subprocess.run([str(py), str(HERE / "日内区间.py")], cwd=HERE, capture_output=True, timeout=600,
                           env=os.environ | {"PYTHONIOENCODING": "utf-8"},
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = r.stdout.decode("utf-8", errors="replace").strip().splitlines()
        err = r.stderr.decode("utf-8", errors="replace").strip().splitlines()  # 天勤的免责声明也在 stderr
        print(out[-1] if r.returncode == 0 and out else "日内区间出错：" + "\n".join((out + err)[-5:]))
    except subprocess.TimeoutExpired:
        print("日内区间 10 分钟还没算完，跳过")
    try:
        r = subprocess.run([str(py), str(ROOT / "futures_brief.py"), "stock"], cwd=ROOT, capture_output=True, timeout=1500,
                           env=os.environ | {"PYTHONIOENCODING": "utf-8"},
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        print("25 分钟还没跑完，停了")
        return
    lines = (r.stdout + r.stderr).decode("utf-8", errors="replace").strip().splitlines()
    print("\n".join(lines[-8:]))
    print(f"仓储取数{'完成' if r.returncode == 0 else f'出错（返回 {r.returncode}）'}（{time.time() - t0:.0f} 秒）")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--at", help='回放：当作现在是这个时候，如 "2026-10-21 20:53"')
    p.add_argument("--force", action="store_true", help="离开盘超过 30 分钟也估")
    p.add_argument("--no-notify", action="store_true", help="不弹通知")
    p.add_argument("--prep", action="store_true", help="只取仓储数据（futures_brief.py stock），不估")
    a = p.parse_args()
    if a.prep:
        prep()
        return
    live = a.at is None
    now = datetime.now() if live else datetime.strptime(a.at, "%Y-%m-%d %H:%M")
    o = next_opening(now)
    t0 = time.time()
    print(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 开盘预估：{o:%m-%d %H:%M} 开盘{'' if live else f'（回放 {now:%m-%d %H:%M}）'} =====")
    if o - now > timedelta(minutes=30) and not a.force:
        print("离开盘还有半小时以上，不估（要估加 --force）")
        return
    res = forecast(o, now, live)
    if live and not res["有开盘"]:
        print("交易日历显示这次没有开盘（周末、节假日或节前一晚），不估")
        return
    name = f"{o:%Y-%m-%d_%H%M}"
    if live:  # 网页放「预估/」（没看到通知就去那里找），预估.csv 放开盘文件夹（复盘对答案用）
        folder, page = HERE / "数据" / name, rv.FORECAST_DIR / f"{name}.html"
    else:
        folder = rv.REVIEW / "回放" / name
        page = folder / "开盘预估.html"
    folder.mkdir(parents=True, exist_ok=True)
    page.parent.mkdir(parents=True, exist_ok=True)
    write_csv(res, folder)
    page.write_text(build_page(res, page), encoding="utf-8")
    if live:  # 预估也马上写进 复盘/开盘复盘.csv、.xlsx（这次开盘那几行先只有预估，开盘取完 tick 再补全）
        rv.run()
    for it in res["品种"]:
        mv = it.get("外盘")
        legs = "、".join(f"{s.split('.')[-1]} {rv.fmt(l.get('预估'), 2)}" for s, l in it["腿"].items() if "预估" in l)
        print(f"{it['名称']}：外盘 {rv.fmt(mv['外盘涨跌%'], 2, plus=True) + '%' if mv else '没取到'} {it['外盘方向']}{it['分档']}"
              f"{'，' + legs if legs else ''}{'；' + '；'.join(it['报警'] + it['提示']) if it['报警'] or it['提示'] else ''}")
    print(f"写好 {page}（{time.time() - t0:.0f} 秒）")
    if not a.no_notify:
        notify(f"开盘预估 {o:%H:%M}{'（回放）' if not live else ''}", toast_lines(res), page)


if __name__ == "__main__":
    main()
