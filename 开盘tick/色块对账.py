"""色块对账：真钱按色块对账——浮层贴「重 / 打 / 小 / 关」的格子，他两个号开盘实际做了几格、多少手、真实赚亏多少。

11-10 他定的。规则七（开盘复盘.py 记分卡）按色块统计的是**模拟**（每格一手、程序版·正滑一半）；这里只看真实成交。
跑法（在本文件夹里）：python 色块对账.py        成交复盘.py 跑完会顺带调一次（出错只打印，不影响成交复盘）
结果：成交/复盘网页/色块对账.html（给人看）、成交/配对明细/色块对账.csv（逐格明细）。都在 成交/ 下，只留本机。

一、数据来源（都是现成的，不重新配对）
  成交：成交/配对明细/日期_账号X_配对.json（成交复盘.py 存下的配对结果，当时用的腿2）、日期_账号X_整理.csv（手续费）、账本调整.json。
  色块：开盘tick/复盘数据/开盘复盘.csv 的「59分判断」「浮层标记」「色块」「色块原因」，按 开盘 + 组合 对上。
  当时贴的标记：开盘tick/数据/开盘日期_时间/竞价判断.json 里每格的「标记」（只拿来对照，不参与统计）。
二、开盘单 = 开仓第一笔成交在开盘后 10 秒内（21:00:00–21:00:10、9:00:00–9:00:10）的价差组合，
  和成交复盘网页「开盘」一节同一口径（成交复盘.OPEN_SECONDS、成交复盘.opens，小草定的）。
  一组开仓被先开先平拆成几段时，每段都算；同一号、同一次开盘、同一组合、同一方向合成一格。
  开盘 10 秒内平掉的老仓（他的程序必须先平昨）不算开盘单，另列一张表，只列手数不算钱。
三、盈亏（小草定的口径：价差 × 乘数 + 价差账本，逐笔不用）
  当天平的：配对脚本的「今日开平点数」（带方向的价差点数 × 手数）× 乘数，手续费 = 这一段两腿开平的手续费（文件里记的）。
  隔夜平的：从开仓那天起按价差账本往后滚（成交复盘.roll，和成交复盘网页同一套先开先平），平掉那天的平仓价差 − 开仓价差，× 方向 × 手数 × 乘数；
    手续费 = 平仓那天记的平仓手续费 + 开仓手续费（开仓那天这两条腿开盘 10 秒内开仓的每手手续费 × 手数，估的，配对结果里没平的组合不带手续费）。
  算不了：还没平（成交记录只到最后一天）；滚账本时中间跨过缺成交记录的交易日（10-30，后面平到哪一组说不清）；被账本调整拿掉的；乘数缺的。
四、局限：色块和「浮层标记」是开盘复盘按**现在的规则**重算的（和规则七同口径），不一定是当时屏幕上贴的；✅▲❌ 是当时真贴的。
  当时贴的标记（竞价判断.json）和现在重算的标记不一样的，逐格明细里写出来。样本小，只列数、不下结论；不是买卖建议。
"""
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import 成交复盘 as cf  # noqa: E402  顺带加载 venv 里的 pandas；配对、账本、乘数都用它的
import pandas as pd  # noqa: E402

from 开盘复盘 import Raw, esc, fmt, num, page, table  # noqa: E402  网页样式跟成交复盘一样

PAIRS, PAGES = cf.PAIRS, cf.PAGES
REC_FILE = cf.REC_FILE  # 开盘tick/复盘数据/开盘复盘.csv
DATA = HERE / "数据"
OUT_HTML = PAGES / "色块对账.html"
OUT_CSV = PAIRS / "色块对账.csv"
MISSING_NOTE = cf.TRADE_ROOT / "缺的记录.md"
LEVELS = ["重", "打", "小", "关", "无色块", "没对上"]
LEVEL_NOTE = {"重": "重（上手数）", "打": "打（照平时打）", "小": "小（小手数）", "关": "关（不打）",
              "无色块": "无色块（○ 没针 / ？ 不判断，浮层不贴色块）", "没对上": "没对上（开盘复盘.csv 里没有这一格）"}
SYMBOL = {"真": "✅", "半真": "▲", "假": "❌", "没针": "○", "不判断": "？"}  # 同 竞价判断.SYMBOL
EPS = 1e-9


# ---------- 读 ----------

def trade_days() -> dict:
    """{账号: [有配对结果的交易日…]}"""
    out = {}
    for f in sorted(PAIRS.glob("????-??-??_账号*_配对.json")):
        d, acct = f.name[:10], f.stem.split("_账号")[1].split("_")[0]
        out.setdefault(acct, []).append(d)
    return out


def load_rec() -> pd.DataFrame:
    """开盘复盘.csv 全部行（只读）；「有tick」= 真开过盘、有 tick 的（同 开盘复盘.opened），没 tick 的那次开盘色块对不上。"""
    rec = pd.read_csv(REC_FILE, encoding="utf-8-sig", dtype=str)
    rec["有tick"] = rec["针"].notna() | rec["首笔价差"].notna()
    return rec


def day_of_opening(opening: str, day_openings: list[str]) -> str:
    """开盘 → 交易日：09:00 就是当天；21:00 是下一个有日盘开盘的交易日（没有就按下一个工作日）。"""
    d = opening[:10]
    if opening.endswith("09:00"):
        return d
    later = [x for x in day_openings if x > d]
    if later:
        return later[0]
    n = date.fromisoformat(d) + timedelta(days=1)
    while n.weekday() >= 5:
        n += timedelta(days=1)
    return n.isoformat()


def then_tags(opening: str, combo: str):
    """当时浮层贴的标记（竞价判断.json），没有这次的 json 返回 None。"""
    f = DATA / opening.replace(" ", "_").replace(":", "") / "竞价判断.json"
    if not f.exists():
        return None
    try:
        j = json.loads(f.read_text(encoding="utf-8")).get("组合", {}).get(combo)
    except (OSError, ValueError):
        return None
    if j is None:
        return None
    return dict(符号=j.get("符号", ""), 标记=" ".join(t for t, _ in j.get("标记", [])))


def open_fee_per_lot(day: str, acct: str, contract: str, opening: str, cache: dict):
    """开仓那天这条腿开盘 10 秒内开仓的每手手续费（隔夜平的组合估开仓手续费用）。"""
    key = (day, acct)
    if key not in cache:
        f = PAIRS / f"{day}_账号{acct}_整理.csv"
        cache[key] = pd.read_csv(f, encoding="utf-8-sig", dtype=str) if f.exists() else None
    df = cache[key]
    if df is None:
        return None
    t0 = pd.Timestamp(opening)
    t = pd.to_datetime(df["成交时间"], errors="coerce")
    m = (df["合约"] == contract) & (df["开平"] == "开仓") & (t >= t0) & (t < t0 + pd.Timedelta(seconds=cf.OPEN_SECONDS + 1))
    lots = pd.to_numeric(df.loc[m, "成交手数"].str.replace(",", ""), errors="coerce").sum()
    fee = pd.to_numeric(df.loc[m, "手续费"].str.replace(",", ""), errors="coerce").sum()
    return None if lots <= 0 else fee / lots


# ---------- 账本往后滚，找隔夜平的 ----------

def trace_book(acct: str, days: list[str], missing: list[str]) -> tuple[list[dict], list[dict], set, list[dict]]:
    """照 成交复盘.book_before 的滚法（含账本调整），返回 (每次平掉的以前的仓，滚到最后一天的账本，跨过缺记录交易日的仓，被账本调整拿掉的)。"""
    adj_f = PAIRS / "账本调整.json"
    adj = json.loads(adj_f.read_text(encoding="utf-8")) if adj_f.exists() else []
    book, closes, crossed, done, removed = [], [], set(), set(), []
    key = lambda b: (b["组合"], b["方向"], b["开仓日"], b["开仓时间"])
    prev = None
    for d in days:
        if prev and any(prev < m < d for m in missing):
            crossed |= {key(b) for b in book}
        for i, a in enumerate(adj):  # 同 成交复盘.book_before 里的 apply
            if i in done or a["账号"] != acct or a["生效日"] > d:
                continue
            left = float(a["手数"])
            for b in book:
                if left > EPS and b["组合"] == a["组合"] and b["方向"] == a["方向"] and b["开仓日"] == a["开仓日"]:
                    q = min(left, b["手数"])
                    b["手数"] -= q
                    left -= q
                    removed.append(dict(b, 手数=q, 平仓日=d))
            done.add(i)
        book = [b for b in book if b["手数"] > EPS]
        res = json.loads((PAIRS / f"{d}_账号{acct}_配对.json").read_text(encoding="utf-8"))
        book, closed = cf.roll(book, res, d)
        closes += [dict(x, 平仓日=d, 原手数=x["手数"]) for x in closed]
        prev = d
    return closes, book, crossed, removed


# ---------- 拼每一格 ----------

def collect(rec: pd.DataFrame) -> tuple[list[dict], list[dict], dict]:
    """返回 (开盘单逐格, 开盘 10 秒内平老仓的, 覆盖情况)。"""
    days = trade_days()
    day_openings = sorted({o[:10] for o in rec["开盘"] if o.endswith("09:00")})
    missing_by_acct = {acct: [d for d in day_openings if ds[0] <= d <= ds[-1] and d not in ds]
                       for acct, ds in days.items()}
    cells, old_closes, fee_cache = {}, [], {}
    for acct, ds in sorted(days.items()):
        missing = missing_by_acct[acct]  # 另一账号有记录，不能补足这个账号的账本缺口
        closes, final, crossed, removed = trace_book(acct, ds, missing)
        last = ds[-1]
        for d in ds:
            res = json.loads((PAIRS / f"{d}_账号{acct}_配对.json").read_text(encoding="utf-8"))
            ops = cf.opens(res, d, acct)  # 先 交易明细，再 未平仓组合，顺序和 res 里一样
            n_closed = len(res["交易明细"])
            for i, o in enumerate(ops):
                if not o["开盘"]:
                    continue
                k = (acct, o["开盘"], o["组合"], o["方向"])
                c = cells.setdefault(k, dict(号=acct, 开盘=o["开盘"], 交易日=d, 组合=o["组合"], 方向=o["方向"], 手数=0.0,
                                             _开=0.0, 算得了手=0.0, 毛=0.0, 费=0.0, 当天平手=0.0, 隔夜平手=0.0,
                                             算不了手=0.0, 算不了原因=[], 平仓日=set(), _平=0.0, _平手=0.0, 开仓时间=[]))
                q = float(o["手数"])
                c["手数"] += q
                c["_开"] += q * float(o["开仓价差"])
                c["开仓时间"].append(o["开仓时间"])
                m = cf.mult(o["组合"])
                if i < n_closed:  # 当天开、当天平
                    x = res["交易明细"][i]
                    assert x["组合"] == o["组合"]
                    g = cf.spread_pnl(o["组合"], x.get("今日开平点数"))
                    if g is None:
                        c["算不了手"] += q; c["算不了原因"].append("乘数缺")
                        continue
                    c["算得了手"] += q; c["当天平手"] += q
                    c["毛"] += g; c["费"] += num(x["手续费"]) or 0
                    c["_平"] += q * float(x["平仓点位"]); c["_平手"] += q
                    c["平仓日"].add(d)
                    continue
                # 当天没平：按账本往后找
                key = (o["组合"], o["方向"], d, o["开仓时间"])
                if key in crossed:
                    c["算不了手"] += q; c["算不了原因"].append("滚账本跨过缺记录的交易日（" + "、".join(missing) + "）")
                    continue
                legs = o["组合"].split("-")
                per = [open_fee_per_lot(d, acct, leg, o["开盘"], fee_cache) for leg in legs]
                left = q
                for x in closes:
                    if left <= EPS or (x["组合"], x["方向"], x["开仓日"], x["开仓时间"]) != key:
                        continue
                    qq = min(left, x["手数"])
                    x["手数"] -= qq  # 同一段可能被几格认领（理论上不会），用掉就扣掉
                    left -= qq
                    if m is None or None in per:
                        c["算不了手"] += qq; c["算不了原因"].append("乘数缺" if m is None else "开仓手续费找不到")
                        continue
                    sg = cf.sign(x["方向"])
                    c["算得了手"] += qq; c["隔夜平手"] += qq
                    c["毛"] += (x["平仓价差"] - float(x["开仓价差"])) * sg * qq * m
                    c["费"] += x["手续费"] * qq / x["原手数"] + sum(per) * qq  # 平仓那天的平仓手续费按手数分 + 估的开仓手续费
                    c["_平"] += qq * x["平仓价差"]; c["_平手"] += qq
                    c["平仓日"].add(x["平仓日"])
                if left > EPS:
                    gone = sum(b["手数"] for b in removed if (b["组合"], b["方向"], b["开仓日"], b["开仓时间"]) == key)
                    still = sum(b["手数"] for b in final if (b["组合"], b["方向"], b["开仓日"], b["开仓时间"]) == key)
                    why = (f"还没平（成交记录只到 {last}）" if still > EPS else "被账本调整拿掉" if gone > EPS else "账本里找不到平仓")
                    c["算不了手"] += left; c["算不了原因"].append(why)
            # 开盘 10 秒内平掉的仓（不算开盘单）
            for x in res["期初仓位平仓"]:
                op = opening_of(x["平仓时间"], d)
                if op:
                    old_closes.append(dict(号=acct, 开盘=op, 组合=x["组合"], 方向=x["平仓方向"], 手数=float(x["手数"]), 来源="平昨（期初仓位）"))
            for x in res["交易明细"]:
                op = opening_of(x["平仓时间"], d)
                if op and opening_of(x["开仓时间"], d) != op:  # 同一次开盘开、10 秒内就平的是开盘单自己止盈 / 止损，不算
                    old_closes.append(dict(号=acct, 开盘=op, 组合=x["组合"], 方向=x["开仓方向"].replace("开", "平"), 手数=float(x["手数"]),
                                           来源=f"平今（{x['开仓时间']} 开的）"))
    cover = dict(days=days, missing=sorted({d for ds in missing_by_acct.values() for d in ds}),
                 missing_by_acct=missing_by_acct, day_openings=day_openings)
    return list(cells.values()), old_closes, cover


def opening_of(t: str, day: str):
    """平仓时间 → 落在哪次开盘的 10 秒里（同 成交复盘.opens 的算法）。"""
    if not t:
        return None
    ts = cf.full_time(str(t).split("–")[0], day)
    sec = (ts - ts.normalize()).total_seconds()
    return next((f"{ts:%Y-%m-%d} {h:02d}:00" for h in (21, 9) if 0 <= sec - h * 3600 < cf.OPEN_SECONDS + 1), None)


# ---------- 对上色块 ----------

def product_of_combo(combo: str) -> str:
    p = [cf.prod(c) for c in combo.split("-")]
    return p[0] if p[0] == p[1] else "-".join(p)


def text(x) -> str:
    return x if isinstance(x, str) else ""


def attach(cells: list[dict], rec: pd.DataFrame) -> None:
    """每格对上 开盘复盘.csv 的色块、判断、针，和当时贴的标记。"""
    import 竞价判断 as jd
    idx = {(r["开盘"], r["组合"]): r for _, r in rec.iterrows()}
    live = {}  # 按开盘缓存当时的判断基准，兼容未保存「判断昨收价差」的旧复盘 CSV
    for c in cells:
        r = idx.get((c["开盘"], c["组合"]))
        c["开在"] = c["_开"] / c["手数"] if c["手数"] > EPS else None
        c["平在"] = c["_平"] / c["_平手"] if c["_平手"] > EPS else None
        c["净"] = c["毛"] - c["费"] if c["算得了手"] > EPS else None
        if c["算得了手"] <= EPS:
            c["毛"] = c["费"] = None
        c["品种"] = product_of_combo(c["组合"])
        c["判断昨收价差"], c["顺针基准来源"] = None, ""
        if r is None or not r["有tick"]:
            why = "开盘复盘.csv 没有这个组合（当时不在合约.txt，或主力换了组合名对不上）" if r is None else "那次开盘没取到 tick，开盘复盘没判断"
            c |= dict(色块="没对上", 判断="", 判="", 色块原因=why, 浮层标记="", 判断来源="", 针=None, 昨收价差=None, 顺针="")
        else:
            kind = text(r["59分判断"])
            c |= dict(色块=text(r["色块"]) or "无色块", 判断=kind, 判=SYMBOL.get(kind, kind), 色块原因=text(r["色块原因"]),
                      浮层标记=text(r["浮层标记"]), 判断来源=text(r["判断来源"]), 针=num(r["针"]), 昨收价差=num(r["昨收价差"]))
            if text(r["品种"]):
                c["品种"] = r["品种"]
            b = num(r.get("判断昨收价差"))
            if b is not None:
                c["顺针基准来源"] = "复盘保存的判断基准"
            else:
                op = c["开盘"]
                if op not in live:
                    folder = DATA / op.replace(" ", "_").replace(":", "")
                    try:
                        live[op] = jd.read_live(folder)
                    except (OSError, ValueError):
                        live[op] = {}
                b = num(live[op].get(c["组合"], {}).get("昨收价差"))
                if b is not None:
                    c["顺针基准来源"] = "当次竞价判断.csv"
            if b is None:
                b = c["昨收价差"]
                c["顺针基准来源"] = "旧昨收（判断基准缺失）"
            c["判断昨收价差"] = b
            n = c["针"]
            c["顺针"] = "" if n is None or b is None or abs(n - b) < EPS else ("是" if (n > b) == (c["方向"] == "空开") else "否")
        t = then_tags(c["开盘"], c["组合"])
        c["当时符号"] = "" if t is None else t["符号"]
        c["当时标记"] = "" if t is None else t["标记"]
        if t is None:
            c["标记对照"] = "没有当时的记录"
        elif c["判断"] not in ("真", "半真", "假"):
            c["标记对照"] = "—（没判 ✅▲❌，不重算标记）"
        else:
            same = sorted(c["当时标记"].split()) == sorted(c["浮层标记"].split())
            c["标记对照"] = "一样" if same else "不一样"
        how = []
        if c["当天平手"] > EPS:
            how.append(f"当天平 {fmt(c['当天平手'], 0)} 手")
        if c["隔夜平手"] > EPS:
            how.append(f"隔夜平 {fmt(c['隔夜平手'], 0)} 手（{'、'.join(sorted(d for d in c['平仓日'] if d != c['交易日']))}）")
        if c["算不了手"] > EPS:
            how.append(f"算不了 {fmt(c['算不了手'], 0)} 手：{'；'.join(dict.fromkeys(c['算不了原因']))}")
        c["怎么平的"] = "；".join(how)


# ---------- 汇总 ----------

def summary(cs: list[dict]) -> dict:
    ok = [c for c in cs if c["算得了手"] > EPS]
    lots = sum(c["算得了手"] for c in ok)
    gross = sum(c["毛"] for c in ok)
    fee = sum(c["费"] for c in ok)
    return dict(格=len({(c["开盘"], c["组合"]) for c in cs}), 全部手=sum(c["手数"] for c in cs), 手=lots,
                毛=gross if ok else None, 费=fee if ok else None, 净=gross - fee if ok else None,
                每手净=(gross - fee) / lots if lots > EPS else None,
                算不了手=sum(c["算不了手"] for c in cs), 算不了格=len({(c["开盘"], c["组合"]) for c in cs if c["算不了手"] > EPS}))


def money(x) -> Raw:
    return cf.money(x)


def lots(x) -> str:
    return fmt(x, 0) if x else "0"


def sum_row(name, acct, s) -> list:
    return [name, acct, s["格"], lots(s["全部手"]), lots(s["手"]), money(s["毛"]), fmt(s["费"]), Raw(f"<b>{money(s['净'])}</b>"),
            money(s["每手净"]), f"{s['算不了格']} 格 / {lots(s['算不了手'])} 手" if s["算不了手"] > EPS else ""]


SUM_HEAD = ["色块", "号", "做了几格", "手数（全部）", "算得了的手数", "毛盈亏（元）", "手续费", "净盈亏（元）", "每手净（元）", "算不了"]


def cell_row(c: dict, full: bool = False) -> list:
    row = [c["开盘"], c["号"], c["组合"], c["判"], c["色块"] if c["色块"] in ("重", "打", "小", "关") else "—", c["色块原因"],
           c["方向"], c["顺针"], lots(c["手数"]), fmt(c["开在"], 4), fmt(c["平在"], 4), money(c["毛"]), fmt(c["费"]),
           Raw(f"<b>{money(c['净'])}</b>"), money(None if c["净"] is None else c["净"] / c["算得了手"]), c["怎么平的"]]
    if full:
        row += [c["浮层标记"], c["当时符号"] + " " + c["当时标记"], c["标记对照"]]
    return row


CELL_HEAD = ["开盘", "号", "组合", "判", "色块", "色块原因", "方向", "顺针", "手", "开在（均）", "平在（均）", "毛盈亏", "手续费",
             "净盈亏（元）", "每手净", "怎么平的"]


# ---------- 输出 ----------

def write_csv(cells: list[dict]) -> None:
    cols = ["开盘", "交易日", "号", "组合", "品种", "方向", "顺针", "判", "判断", "判断来源", "色块", "色块原因", "浮层标记",
            "当时符号", "当时标记", "标记对照", "手数", "算得了手", "当天平手", "隔夜平手", "算不了手", "开在", "平在",
            "毛", "费", "净", "怎么平的", "针", "昨收价差", "判断昨收价差", "顺针基准来源"]
    df = pd.DataFrame([{k: c.get(k) for k in cols} for c in sorted(cells, key=lambda c: (c["开盘"], c["组合"], c["号"]))], columns=cols)
    df = df.rename(columns={"毛": "毛盈亏", "费": "手续费", "净": "净盈亏", "开在": "开在（均）", "平在": "平在（均）"})
    for k in ("毛盈亏", "手续费", "净盈亏"):
        df[k] = df[k].map(lambda v: None if v is None else round(v, 2))
    for k in ("开在（均）", "平在（均）"):
        df[k] = df[k].map(lambda v: None if v is None else round(v, 4))
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")


def write_html(cells: list[dict], olds: list[dict], cover: dict, rec: pd.DataFrame) -> None:
    accts = sorted(cover["days"])
    by = lambda f: [c for c in cells if f(c)]
    body = [f"<h1>真钱按色块对账</h1><p class='meta'>生成于 {datetime.now():%Y-%m-%d %H:%M}。"
            f"成交记录：{'；'.join(f'账号{a} {v[0]} ~ {v[-1]}（{len(v)} 个交易日）' for a, v in cover['days'].items())}。"
            "逐格明细另存 成交/配对明细/色块对账.csv。</p>"]
    body.append(
        "<h2>先看这里</h2><ul class='note'>"
        f"<li><b>开盘单</b> = 开仓第一笔成交在开盘后 {cf.OPEN_SECONDS} 秒内（21:00:00–21:00:{cf.OPEN_SECONDS:02d}、9:00:00–9:00:{cf.OPEN_SECONDS:02d}）的价差组合，"
        "和成交复盘网页「开盘」一节同一口径。同一个号、同一次开盘、同一组合、同一方向算一格；合计行的格数是两个号合起来、同一格只算一次。</li>"
        "<li><b>盈亏按价差算</b>（不用逐笔）：(平仓价差 − 开仓价差) × 手数 × 乘数，空开反过来，再减手续费。当天平的用配对结果；"
        "隔夜平的按价差账本往后找到平掉那天（和成交复盘网页同一本账，先开先平）；隔夜平的开仓手续费是按开仓那天开盘 10 秒内这两条腿的每手手续费估的。</li>"
        "<li><b>算不了</b>：还没平、中间跨过缺成交记录的交易日、被账本调整拿掉的，不算进钱里，单独写手数。</li>"
        "<li><b>色块是按现在的规则重算的</b>（开盘复盘.csv 的「色块」，和记分卡规则七同一口径），不一定是当时屏幕上贴的；✅▲❌ 是当时真贴的。"
        "当时贴的标记（数据/开盘时间/竞价判断.json）和现在重算的不一样的，最下面的逐格明细里写着。</li>"
        "<li>样本很小，这里只列数，不下结论，也不是买卖建议。</li></ul>")
    # 表一
    rows = []
    for lv in LEVELS:
        sub = by(lambda c, lv=lv: c["色块"] == lv)
        if not sub and lv in ("无色块", "没对上"):
            continue
        for a in accts:
            rows.append(sum_row(LEVEL_NOTE[lv] if a == accts[0] else "", f"账号{a}", summary([c for c in sub if c["号"] == a])))
        rows.append(["", Raw("<b>合计</b>")] + sum_row("", "", summary(sub))[2:])
    body += ["<h2>一、按色块：实际做了几格、赚亏多少</h2>", table(SUM_HEAD, rows)]
    tot = [sum_row("全部开盘单", f"账号{a}", summary(by(lambda c, a=a: c["号"] == a))) for a in accts]
    tot.append(["", Raw("<b>合计</b>")] + sum_row("", "", summary(cells))[2:])
    body += [table(SUM_HEAD, tot),
             "<p class='note'>每手净 = 净盈亏 ÷ 算得了的手数（一手 = 一组价差，两条腿各一手）。「无色块」= 那一格没针或不判断，浮层只贴 ○ / ？。</p>"]
    # 表二
    bad = sorted(by(lambda c: c["色块"] == "关" or c["判断"] in ("假", "不判断")), key=lambda c: (c["开盘"], c["组合"], c["号"]))
    body += ["<h2>二、贴「关」/ 判 ❌ / ？ 却做了的</h2>",
             table(CELL_HEAD, [cell_row(c) for c in bad]) if bad else "<p>没有。</p>",
             "<p class='note'>顺针：空开时针在判断基准上面、多开时针在下面 = 是。优先使用当次竞价判断的昨收，缺失才退回旧昨收；逐格 CSV 的「顺针基准来源」列可核对。</p>"]
    # 表三
    heavy = rec[rec["色块"] == "重"]
    rows = []
    for _, r in heavy.sort_values(["开盘", "组合"]).iterrows():
        td = day_of_opening(r["开盘"], cover["day_openings"])
        st = []
        for a in accts:
            if td not in cover["days"][a]:
                st.append("没有成交记录")
                continue
            did = [c for c in cells if c["号"] == a and c["开盘"] == r["开盘"] and c["组合"] == r["组合"]]
            st.append(f"做了 {lots(sum(c['手数'] for c in did))} 手" if did else "没做")
        if any(s == "没做" for s in st):
            rows.append([r["开盘"], r["组合"], SYMBOL.get(r["59分判断"], r["59分判断"]), text(r["色块原因"])] + st
                        + [fmt(r["程序半每手净元"]) if num(r["程序半每手净元"]) is not None else
                           fmt(r["程序半每手毛元"]) + "（毛）" if num(r["程序半每手毛元"]) is not None else
                           "模拟没成交" if text(r["程序半成交"]) == "否" else text(r["程序半成交"])])
    body += ["<h2>三、贴「重」却没做的</h2>",
             table(["开盘", "组合", "判", "色块原因"] + [f"账号{a}" for a in accts] + ["参考：规则七模拟每手净元（程序半）"], rows)
             if rows else "<p>没有（有成交记录的开盘里，贴「重」的格子至少一个号做了）。</p>",
             "<p class='note'>只列至少一个号「没做」的；「没有成交记录」= 那天的成交记录缺。最后一列是开盘复盘里同一格的模拟（每格一手、正滑一半），只当参考，没手续费的品种是毛的或空的。</p>"]
    # 表四
    rows = []
    for p in sorted({c["品种"] for c in cells}):
        sub = by(lambda c, p=p: c["品种"] == p)
        row = [p]
        for a in accts:
            s = summary([c for c in sub if c["号"] == a])
            row += [s["格"], lots(s["手"]), money(s["净"])]
        s = summary(sub)
        row += [s["格"], lots(s["手"]), money(s["毛"]), fmt(s["费"]), Raw(f"<b>{money(s['净'])}</b>"), money(s["每手净"]),
                lots(s["算不了手"]) if s["算不了手"] > EPS else ""]
        rows.append(row)
    head = ["品种"] + [x for a in accts for x in (f"账号{a} 格", f"账号{a} 手", f"账号{a} 净")] + ["合计 格", "合计 手", "毛盈亏", "手续费", "净盈亏", "每手净", "算不了手数"]
    body += ["<h2>四、按品种</h2>", table(head, rows), "<p class='note'>手 = 算得了的手数；净 = 净盈亏（元）。</p>"]
    # 表五
    idx = {(r["开盘"], r["组合"]): r for _, r in rec.iterrows()}
    g = {}
    for x in olds:
        k = (x["号"], x["开盘"], x["组合"], x["方向"], x["来源"].split("（")[0])
        g[k] = g.get(k, 0) + x["手数"]
    rows = []
    for (a, op, combo, d, src), q in sorted(g.items(), key=lambda kv: (kv[0][1], kv[0][2], kv[0][0])):
        r = idx.get((op, combo))
        r = r if r is not None and r["有tick"] else None
        blk = "没对上" if r is None else (text(r["色块"]) or "无色块")
        rows.append([op, f"账号{a}", combo, d, src, lots(q), "" if r is None else SYMBOL.get(r["59分判断"], r["59分判断"]), blk])
    body += ["<h2>五、开盘 10 秒内平掉的仓（不算开盘单，只列手数）</h2>",
             table(["开盘", "号", "组合", "方向", "平的是", "手", "判", "色块"], rows) if rows else "<p>没有。</p>",
             "<p class='note'>他的程序必须先平昨：开盘那一下要是和手上的老仓方向相反，会先平掉老仓。这些单开盘那一下也成交了，"
             "但赚亏算在老仓开仓那天，这里不算钱。</p>"]
    # 覆盖
    ops = sorted(set(rec["开盘"]))
    tick_ops = set(rec.loc[rec["有tick"], "开盘"])
    st = {}
    for op in ops:
        td = day_of_opening(op, cover["day_openings"])
        have = [a for a in accts if td in cover["days"][a]]
        st[op] = ("有" if len(have) == len(accts) else "缺" if td in cover["missing"] else
                  "还没有成交记录" if all(td > max(cover["days"][a]) for a in accts) else
                  "只有 " + "、".join(f"账号{a}" for a in have) if have else "缺")
    covered = [op for op in ops if st[op] == "有"]
    no_tick = [op for op in covered if op not in tick_ops]
    body += ["<h2>六、覆盖了哪些开盘</h2>",
             f"<p>有成交记录（两个号都有）的开盘 {len(covered)} 次：{covered[0] if covered else ''} ~ {covered[-1] if covered else ''}"
             + (f"；其中 {'、'.join(no_tick)} 没取到 tick，那次的单色块对不上（算在「没对上」）" if no_tick else "") + "。</p>",
             table(["开盘", "交易日", "成交记录"], [[op, day_of_opening(op, cover["day_openings"]), st[op]] for op in ops if st[op] != "有"]),
             "<p class='note'>缺的交易日（分账号）：" + "；".join(
                 f"账号{a}：{'、'.join(ds) or '无'}" for a, ds in sorted(cover["missing_by_acct"].items())) + "。"
             "缺记录那天的开盘不知道做没做；那天前后拿着的仓，平在哪一组也说不清，算进「算不了」。</p>"]
    # 逐格
    diff = [c for c in cells if c["标记对照"] == "不一样"]
    judged = [c for c in cells if c["标记对照"] in ("一样", "不一样")]
    sym_diff = [c for c in cells if c["当时符号"] and c["判"] and c["当时符号"] != c["判"]]
    bang = [c for c in diff if sorted(c["当时标记"].replace("！", "").split()) == sorted(c["浮层标记"].replace("！", "").split())]
    body += ["<h2>七、逐格明细</h2>",
             f"<p class='note'>判 ✅▲❌ 的 {len(judged)} 格里，当时贴的标记和现在重算的不一样的 {len(diff)} 格"
             f"（其中 {len(bang)} 格只差「腿2秒进」后面的「！」，11-07 才有，色块不受影响）；"
             f"当时的符号和开盘复盘记的 59 分判断不一样的 {len(sym_diff)} 格。"
             "「现在的标记」是开盘复盘按现在的规则重算的，色块跟着它；「当时贴的」是竞价判断.json 里存的。</p>",
             table(CELL_HEAD + ["现在的标记", "当时贴的", "标记一样吗"],
                   [cell_row(c, True) for c in sorted(cells, key=lambda c: (c["开盘"], c["组合"], c["号"]))])]
    PAGES.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(page("真钱按色块对账", "".join(body)), encoding="utf-8")


def run() -> Path:
    rec = load_rec()
    cells, olds, cover = collect(rec)
    attach(cells, rec)
    write_csv(cells)
    write_html(cells, olds, cover, rec)
    return OUT_HTML


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    print(f"网页：{run()}")
    print(f"明细：{OUT_CSV}")
