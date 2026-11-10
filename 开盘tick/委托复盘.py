"""委托复盘：他每天发来的委托单 / 成交记录，量「开盘那一下」——单子到得快不快、正滑拿几成、挂在几成、进单多少。

11-05 晚定的（测试/2026-11-05_优化四项/小结.md 第一节）：赚不赚钱几乎全看开盘那一下拿到多少正滑，挂近挂远要看真实进单率，
模型量不准，只能拿委托单攒。每天跑一次，结果攒在 成交/委托记录/ 里（只留本机）。

文件（他发来后放进 成交/委托记录/，文件名照这个写；谁 = 老陆、同事、账号A、账号B）：
  日期_谁_所有委托单.csv   日期_谁_成交记录.csv
  日期 = 交易日（夜盘算下一个交易日，跟快期导出一样）；账号A、B 没有成交记录时用 成交/原始记录/日期_账号A.csv。
跑法（在 开盘tick 里）：python 委托复盘.py 2026-11-05        → 成交/委托记录/复盘/2026-11-05.md
                       python 委托复盘.py --all               把 委托记录/ 里所有日期重算一遍

每次开盘（日期前一个夜盘 21:00、当天 09:00）、每人：
  一单一行（开盘委托.csv）：开盘 0–2 秒报的开仓单里，看板组合的腿1。
    挂价差 = 腿1 报单价 − 腿2 对手价（程序：腿1 价 = 挂价差 + 腿2 最新对手价；开盘那一秒的首单按竞价后那张快照，
             之后几秒的重挂按那一秒 .5 那张，逐单复现/程序规则表.md：秒 S 的单多半用 S.5 那张）；
    几成 =（挂价差 − 昨收价差）÷（猜首笔 − 昨收价差），猜首笔 = 昨收价差 + 0.9 ×（立刻能成交那边 − 昨收价差），和浮层「近」（0.2）「远」（首笔半）同一把尺；
    到达点 a =（开盘那一秒的成交均价 − 腿1 竞价价）÷（开盘第一个半秒末腿1 盘口中间价 − 腿1 竞价价）（arrive.py 的定义，两年回放按 0.55–0.85 算）；
    正滑 w =（开盘那一秒成交均价 − 报单价）÷（腿1 第一个半秒成交均价 − 报单价），只在那半秒均价比报单价好时算（逐单复现的定义）；
    近 / 远够得着：用这张单开盘那一秒的成交均价当「到达时的市场价」，反推浮层「近」「远」换成腿1 限价那一刻够不够得着（只算 1 手，不算抢量）。
  一组一行（开盘组合.csv）：开盘 5 秒内腿1、腿2 的开仓成交合起来的开仓价差，比挂价差好几档（正滑档）。同事那份成交记录没有报单编号，只能按组合合起来算。
  反着挂（11-09）：看板上的腿2 反而先挂、腿1 后追（主力换了、看板还没换，如 11-06 夜 ag2702 已是主力，老陆先买 ag2612 再追卖 ag2702）。
    按报单编号认：腿2 那边最早的单比腿1 所有单都早，前面又没有能配它的反方向开仓单。这时一单一行记真正先挂的那条腿（「挂法」写「腿2先挂」），后追的单不算开盘单；
    挂价差仍按看板方向（腿1 − 腿2）= 腿1 对手价 − 报单价。
  腿2 共用（11-09）：一条腿2 同时配几组（含看板外的，如 rb2701 同时配 rb2705、rb2703、hc2701）时，先按报单找手数正好对得上这组的那几张腿2 单；
    只有一种凑法才算数。凑不出：几组都在看板上就按腿1 手数分（均价近似），有看板外的就写「配不清」、开仓价差留空。
"""
import argparse
import csv
import io
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _f in (sys.stdout, sys.stderr):
    try:
        _f.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
import 开盘tick as tick  # noqa: E402,F401  顺带加载日记库环境里的包（pandas）
import pandas as pd  # noqa: E402

DATA = HERE / "数据"
ROOT = HERE.parent / "成交" / "委托记录"
RAW = HERE.parent / "成交" / "原始记录"
OUT_DIR = ROOT / "复盘"
ORDER_CSV, PAIR_CSV = ROOT / "开盘委托.csv", ROOT / "开盘组合.csv"
WHO = ("老陆", "同事", "账号A", "账号B")
NEAR, HALF, HALF_BY, MIX = 0.2, 0.5, {"ag": 1 / 3, "sn": 1 / 3, "ni": 1 / 3}, 0.9  # 同 竞价判断.py 的 NEAR、HALF、HALF_BY、MIX
OPEN_SECS, PAIR_SECS = 2, 5  # 开盘 0–2 秒报的单算「开盘单」；开盘 5 秒内的成交算进开仓价差
SYMBOL = {"真": "✅", "半真": "▲", "假": "❌", "没针": "○", "不判断": "？"}


# ---------- 读文件 ----------

def clean(v):
    return re.sub(r"[\s　\"]+", "", v) if isinstance(v, str) else v


def num(v):
    v = clean(v)
    if v in (None, "", "-", "--"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def read_table(f: Path) -> list[dict]:
    raw = f.read_bytes()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return [{clean(k): clean(v) for k, v in r.items() if k} for r in csv.DictReader(io.StringIO(txt))]


def secs(t: str) -> int | None:
    m = re.match(r"(\d{1,2}):(\d{2}):(\d{2})", t or "")
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) if m else None


def openings(day: str) -> list[tuple[str, Path]]:
    """这个交易日的两次开盘：前一个夜盘 21:00、当天 09:00。
    夜盘只认紧挨着的那一晚（前一天，或周一对上周五）：节后第一天前面没有夜盘，前一晚数据缺了也不往更早的夜盘找（11-05 审查前会错配）。"""
    out = []
    d = datetime.strptime(day, "%Y-%m-%d").date()
    for back in ((1,) if d.weekday() else (3,)):  # 周一的夜盘在上周五晚上
        f = DATA / f"{d - timedelta(days=back):%Y-%m-%d}_2100"
        if f.exists():
            out.append(("21", f))
    if (DATA / f"{day}_0900").exists():
        out.append(("09", DATA / f"{day}_0900"))
    return out


def ticks(folder: Path, contract: str):
    f = next(folder.glob(f"*.{contract}.csv"), None)
    if f is None:
        return None
    d = pd.read_csv(f, encoding="utf-8-sig")
    d["时间"] = pd.to_datetime(d["时间"])
    return d.set_index("时间")


# ---------- 每次开盘 ----------

def board(folder: Path) -> dict:
    """竞价判断.csv：组合（腿1-腿2）→ 那一组的判断和价位。
    11-05 审查前按腿1 建，同一个腿1 配两个腿2（cu2612-cu2701、cu2612-cu2702）时后一组会盖掉前一组。"""
    f = folder / "竞价判断.csv"
    if not f.exists():
        return {}
    out = {}
    for r in read_table(f):
        if r.get("来源") not in (None, "", "实盘"):
            continue
        out[r.get("组合") or f"{r['腿1']}-{r['腿2']}"] = r
    return out


def guess_of(j: dict, s: int) -> float | None:
    """猜首笔：判断里有就用，没有（第 1 步判的 ❌）按混合版用立刻能成交那边补。"""
    g = num(j.get("猜首笔"))
    if g is not None:
        return g
    base, e = num(j.get("昨收价差")), num(j.get("立刻能卖" if s > 0 else "立刻能买"))
    return None if base is None or e is None else base + MIX * (e - base)


def fills_of(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        q, p = num(r.get("成交手数")), num(r.get("成交价格"))
        if q and p is not None:
            out.append({"合约": r.get("合约", ""), "买卖": (r.get("买卖") or "").strip(), "开平": r.get("开平", ""),
                        "价": p, "手": q, "秒": secs(r.get("成交时间", "")), "报单编号": r.get("报单编号", "")})
    return out


def vwap(fs: list[dict]):
    q = sum(f["手"] for f in fs)
    return (sum(f["价"] * f["手"] for f in fs) / q, q) if q else (None, 0)


def oid(o: dict) -> int:
    return int(re.sub(r"\D", "", o.get("报单编号", "") or "") or 0)


def unique_orders(fs: list[dict], q: float, limit: int = 18) -> list[dict] | None:
    """成交按报单编号分堆，找手数加起来正好 = q 的那几张单；只有一种凑法才算数（多种凑法、单太多、没报单编号 → None，分不清不硬配）。"""
    by = {}
    for f in fs:
        by.setdefault(f["报单编号"], []).append(f)
    if "" in by or not q or len(by) > limit:
        return None
    items = [(k, sum(f["手"] for f in v)) for k, v in by.items()]
    hits = []

    def walk(i, left, cur):
        if len(hits) > 1:
            return
        if abs(left) < 1e-9:
            hits.append(cur)
        elif i < len(items) and left > 0:
            walk(i + 1, left - items[i][1], cur + [items[i][0]])
            walk(i + 1, left, cur)

    walk(0, q, [])
    return [f for k in hits[0] for f in by[k]] if len(hits) == 1 else None


def one_opening(day: str, sess: str, folder: Path, who: str, orders: list[dict], fills: list[dict] | None,
                notes: list | None = None) -> tuple[list, list]:
    """fills = None：这人没有成交记录（成交只能看委托单上的成交手数、均价）。notes：对不上组合的提醒写进这里。"""
    b = board(folder)
    if not b:
        return [], []
    leg2_of = {k: v["腿2"] for k, v in b.items()}
    by_leg1 = {}
    for k, v in b.items():
        by_leg1.setdefault(v["腿1"], []).append(k)
    two = {c: ks for c, ks in by_leg1.items() if len(ks) > 1}  # 同一个腿1 有两组：单子上看不出是哪组，不硬配
    if two and notes is not None:
        notes.append(f"{who} {folder.name}：" + "；".join(f"{c} 同时是 {'、'.join(ks)} 的腿1，这条腿的单子对不上是哪一组，没算" for c, ks in two.items()))
    t0 = secs(f"{sess}:00:00")
    o_ts = datetime.strptime(folder.name, "%Y-%m-%d_%H%M")
    in_win = lambda s, n: s is not None and t0 <= s <= t0 + n
    tk = {}
    # 反着挂：见文件开头。rev = 真正先挂的单 → 组合，chased = 后追的单（不算开盘单）
    opens = [o for o in orders if o.get("开平") == "开仓" and in_win(secs(o.get("报单时间", "")), OPEN_SECS) and oid(o)]
    is_sell = lambda o: "卖" in o.get("买卖", "")
    product = lambda c: re.match(r"[A-Za-z]*", c)[0].lower()
    mates = {}
    for v in b.values():
        mates.setdefault(v["腿1"], set()).add(v["腿2"])
        mates.setdefault(v["腿2"], set()).add(v["腿1"])
    cand = {}
    for k, j in b.items():
        c1, c2 = j["腿1"], j["腿2"]
        if c1 in two:
            continue
        for sell in (True, False):
            chase = [o for o in opens if o["合约"] == c1 and is_sell(o) == sell]
            lead = [o for o in opens if o["合约"] == c2 and is_sell(o) != sell]
            if not chase or not lead or min(map(oid, lead)) > min(map(oid, chase)):
                continue
            first = min(map(oid, lead))
            if any(oid(o) < first and is_sell(o) == sell and (product(o["合约"]) == product(c2) or o["合约"] in mates[c2]) for o in opens):
                continue  # 它前面有能配的反方向开仓单：它是别的组的腿2，不是反着挂
            cand.setdefault((c2, sell), []).append((k, chase, [o for o in lead if oid(o) < min(map(oid, chase))]))
    rev, chased = {}, set()
    for (c2, _), got in cand.items():
        if len(got) > 1:
            if notes is not None:
                notes.append(f"{who} {folder.name}：{c2} 先挂，但 {'、'.join(g[0] for g in got)} 都有后追的单，对不上是哪一组，这几组仍按看板方向算")
            continue
        k, chase, lead = got[0]
        rev.update({o["报单编号"]: k for o in lead})
        chased.update(o["报单编号"] for o in chase)

    def tick_of(c):
        if c not in tk:
            tk[c] = ticks(folder, c)
        return tk[c]

    def at(c, ts):
        d = tick_of(c)
        if d is None or ts not in d.index:
            return None
        return d.loc[ts]

    auc_ts, half_ts = o_ts - timedelta(seconds=59.5), o_ts + timedelta(seconds=0.5)
    linked = bool(fills) and bool(fills[0]["报单编号"])  # 成交记录带报单编号，才对得上是哪张单
    by_order = {}
    for f in fills or []:
        if f["报单编号"]:
            by_order.setdefault(f["报单编号"], []).append(f)
    rows = []
    for o in orders:
        c = o.get("合约", "")
        k_rev = rev.get(o.get("报单编号", ""))
        if o.get("报单编号", "") in chased:
            continue
        if k_rev is None and (len(by_leg1.get(c, ())) != 1 or o.get("开平") != "开仓" or not in_win(secs(o.get("报单时间", "")), OPEN_SECS)):
            continue
        j = b[k_rev or by_leg1[c][0]]
        mate = j["腿1"] if k_rev else j["腿2"]  # 另一条腿
        so = 1 if "卖" in o.get("买卖", "") else -1  # 这张单：卖 = 价越高越好
        s = -so if k_rev else so  # 价差方向（看板的 腿1 − 腿2）：卖腿1 = 卖价差（空开）
        t = num(j.get("一档")) or 1
        base, needle = num(j.get("昨收价差")), num(j.get("针"))
        p = num(o.get("报单价格"))
        sec = secs(o.get("报单时间", ""))
        first_one = sec == t0  # 开盘那一秒的首单：按竞价后那张定价；之后的重挂按那一秒 .5 那张（没有就往前找最近一张）
        if first_one:
            a2 = at(mate, auc_ts)
        else:
            d2 = tick_of(mate)
            k = None if d2 is None else d2[d2.index <= o_ts + timedelta(seconds=sec - t0 + 0.5)]
            a2 = None if k is None or not len(k) else k.iloc[-1]
        opp2 = None if a2 is None else num(a2["卖一价"] if so > 0 else a2["买一价"])  # 另一条腿的对手价
        sp = None if p is None or opp2 is None else (opp2 - p if k_rev else p - opp2)
        g = guess_of(j, s)
        frac = None
        if sp is not None and base is not None and g is not None and abs(g - base) >= t - 1e-9:
            frac = (sp - base) / (g - base)
        own = by_order.get(o.get("报单编号", ""), [])
        filled = num(o.get("成交手数"))
        if linked:  # 成交记录对得上：开盘那一秒（0–1 秒）的成交
            x, q1 = vwap([f for f in own if in_win(f["秒"], 1)])
        elif filled is not None:  # 只有委托单：没成交 = 0；最后一笔成交也在开盘那一秒里，整张的均价就是开盘那一秒的
            x, q1 = (None, 0) if filled == 0 else (num(o.get("成交均价")), filled) if in_win(secs(o.get("最后成交时间", "")), 1) else (None, None)
        else:  # 同事那种：委托单没手数、成交记录没报单编号，不知道
            x, q1 = None, None
        leg1 = at(c, half_ts)
        start = num(j.get("腿2竞价" if k_rev else "腿1竞价"))
        if start is None:
            a1 = at(c, auc_ts)
            start = None if a1 is None else (num(a1["买一价"]) + num(a1["卖一价"])) / 2 if num(a1["买一价"]) and num(a1["卖一价"]) else None
        end = None if leg1 is None or not num(leg1["买一价"]) or not num(leg1["卖一价"]) else (num(leg1["买一价"]) + num(leg1["卖一价"])) / 2
        tick1 = t  # 腿1 一档：同品种跨月价差的一档就是腿1 的一档
        a = (x - start) / (end - start) if None not in (x, start, end) and abs(end - start) >= 2 * tick1 - 1e-9 else None
        avg1 = None if leg1 is None else num(leg1.get("均价"))
        w = (x - p) / (avg1 - p) if None not in (x, p, avg1) and so * (avg1 - p) > 1e-9 else None
        half = HALF_BY.get(j.get("品种"), HALF)
        reach = {}
        for lab, k in (("近", NEAR), ("远", half)):
            if None in (x, opp2, base, g):
                reach[lab] = ""
                continue
            level = round((base + k * (g - base)) / t) * t
            lim = opp2 - level if k_rev else level + opp2
            reach[lab] = "是" if so * (x - lim) >= -1e-9 else "否"
        rows.append({
            "日期": day, "开盘": f"{o_ts:%Y-%m-%d %H:%M}", "谁": who, "报单编号": o.get("报单编号", ""), "报单时间": o.get("报单时间", ""),
            "组合": j.get("组合"), "品种": j.get("品种"), "腿1": c, "挂法": "腿2先挂" if k_rev else "", "买卖": "卖" if so > 0 else "买", "价差方向": "卖" if s > 0 else "买",
            "顺针": "" if needle is None or base is None or needle == base else ("是" if (needle > base) == (s > 0) else "否"),
            "判": SYMBOL.get(j.get("档"), j.get("档")), "重仓标记": j.get("重仓标记", ""), "针偏几档": num(j.get("针偏几档")),
            "一档": t, "昨收价差": base, "猜首笔": g, "腿2对手价": opp2, "报单价": p, "挂价差": sp,
            "首单": "是" if first_one else "重挂", "挂离昨收档": None if sp is None or base is None else round(s * (sp - base) / t, 1), "几成": None if frac is None else round(frac, 2),
            "报单手数": num(o.get("报单手数")), "成交手数": filled if filled is not None else (vwap(own)[1] if linked else None),
            "开盘秒成交手数": q1, "开盘秒均价": x,
            "挂单状态": o.get("挂单状态", ""), "腿1竞价": start, "腿1半秒末中间价": end, "腿1半秒均价": avg1,
            "到达点a": None if a is None else round(a, 2), "正滑w": None if w is None else round(w, 2),
            "正滑档(腿1)": None if None in (x, p) else round(so * (x - p) / tick1, 1),
            "近够得着": reach["近"], "远够得着": reach["远"]})
    # 一组一行：开盘 5 秒内腿1、腿2 开仓成交合起来。几组共用同一个腿2、方向也一样（bu2612、bu2702 都买 bu2701）时见文件开头「腿2 共用」。
    if fills is None:  # 没有成交记录，算不了开仓价差
        return rows, []
    tmp = []
    for k, j in b.items():
        c = j["腿1"]
        if c in two:
            continue
        mine = sorted((r for r in rows if r["组合"] == j.get("组合")), key=lambda r: (r["报单时间"], int(re.sub(r"\D", "", r["报单编号"]) or 0)))
        if mine:  # 按报单时间、报单编号排，mine[0] 是开盘首单
            s = 1 if mine[0]["价差方向"] == "卖" else -1
        elif not orders:  # 没有委托单（11-05 审查前这种全跳过）：方向看腿1 开盘 5 秒内的开仓成交，买卖都有就不算
            sides = {"卖" in f["买卖"] for f in fills if f["合约"] == c and f["开平"] == "开仓" and in_win(f["秒"], PAIR_SECS)}
            if len(sides) != 1:
                continue
            s = 1 if sides.pop() else -1
        else:
            continue
        f1 = [f for f in fills if f["合约"] == c and f["开平"] == "开仓" and in_win(f["秒"], PAIR_SECS) and ("卖" in f["买卖"]) == (s > 0)]
        tmp.append((k, j, mine, s, *vwap(f1)))
    pairs = []
    for k, j, mine, s, p1, q1 in tmp:
        share = [x for x in tmp if leg2_of[x[0]] == leg2_of[k] and x[3] == s]
        f2 = [f for f in fills if f["合约"] == leg2_of[k] and f["开平"] == "开仓" and in_win(f["秒"], PAIR_SECS) and ("买" in f["买卖"]) == (s > 0)]
        p2, q2 = vwap(f2)
        shared = "是" if len(share) > 1 else ""
        if q1 and (len(share) > 1 or q2 > q1 + 1e-9):  # 腿2 成交比这组多：别的组（可能在看板外）也用这条腿
            exact = unique_orders(f2, q1)
            tot = sum(x[5] for x in share)
            if exact:
                p2, q2 = vwap(exact)
                shared = "是"
            elif len(share) > 1 and q2 <= tot + 1e-9:
                q2 = min(q1, q2 * q1 / tot)
            else:
                p2, q2, shared = None, None, "配不清"
        elif len(share) > 1:  # 这组腿1 没成交
            p2, q2 = None, 0
        m0 = mine[0] if mine else {}
        sp, t = m0.get("挂价差"), m0.get("一档") or num(j.get("一档")) or 1
        opened = None if p1 is None or p2 is None else p1 - p2
        if not mine and not q1:
            continue
        pairs.append({"日期": day, "开盘": f"{o_ts:%Y-%m-%d %H:%M}", "谁": who, "组合": j.get("组合"), "品种": j.get("品种"), "挂法": m0.get("挂法", ""), "买卖价差": "卖" if s > 0 else "买",
                      "判": SYMBOL.get(j.get("档"), j.get("档")), "重仓标记": j.get("重仓标记", ""), "开盘单数": len(mine) if mine else None,
                      "报单手数": sum(r["报单手数"] or 0 for r in mine) or None,
                      "挂价差(首单)": sp, "几成(首单)": m0.get("几成"), "腿1 5秒成交手数": q1, "腿1均价": p1, "腿2 5秒成交手数": None if q2 is None else round(q2, 1), "腿2均价": p2,
                      "腿2共用": shared, "开仓价差": opened,
                      "正滑档": None if opened is None or sp is None else round(s * (opened - sp) / t, 1),
                      "腿2少成": None if not q1 or q2 is None else round(q1 - q2, 1)})
    return rows, pairs


# ---------- 一天、汇总 ----------

def files_of(day: str) -> dict:
    out = {}
    for who in WHO:
        o = ROOT / f"{day}_{who}_所有委托单.csv"
        f = ROOT / f"{day}_{who}_成交记录.csv"
        if not f.exists() and who.startswith("账号") and (RAW / f"{day}_{who}.csv").exists():
            f = RAW / f"{day}_{who}.csv"
        if o.exists() or f.exists():
            out[who] = (o if o.exists() else None, f if f.exists() else None)
    return out


def run_day(day: str) -> tuple[list, list, list]:
    rows, pairs, notes = [], [], []
    got = files_of(day)
    if not got:
        notes.append(f"{day}：委托记录/ 里没有这天的文件")
    for who, (of, ff) in got.items():
        orders = read_table(of) if of else []
        fills = fills_of(read_table(ff)) if ff else []
        if not of:
            notes.append(f"{who}：没有委托单，只能按组合算开仓价差")
        else:
            cols = set(orders[0]) if orders else set()
            lack = [c for c in ("报单手数", "成交手数", "成交均价") if c not in cols]
            if lack:
                notes.append(f"{who}：委托单缺「{'、'.join(lack)}」，进单率 / 每单正滑只能靠成交记录补" + ("" if fills and fills[0]["报单编号"] else "（成交记录也没有报单编号，补不上）"))
        if ff and fills and not fills[0]["报单编号"]:
            notes.append(f"{who}：成交记录没有报单编号，成交对不上是哪张单")
        if of and not orders:
            notes.append(f"{who}：委托单是空的")
        ops = openings(day)
        night = [x for x in [f["秒"] for f in fills] + [secs(o.get("报单时间", "")) for o in orders] if x is not None and x >= 20 * 3600]
        if night and not any(sess == "21" for sess, _ in ops):
            notes.append(f"{who}：有 {len(night)} 条夜盘的单子 / 成交，但 数据/ 里没有前一晚的 21:00 开盘数据，夜盘没算")
        for sess, folder in ops:
            r, p = one_opening(day, sess, folder, who, orders, fills if ff else None, notes)
            rows += r
            pairs += p
    return rows, pairs, notes


def merge(f: Path, new: list, day: str) -> pd.DataFrame:
    old = pd.DataFrame()
    if f.exists():
        try:
            old = pd.read_csv(f, encoding="utf-8-sig", dtype={"报单编号": str})
        except pd.errors.EmptyDataError:  # 以前空日写出的没有表头的空文件（11-05 审查）
            pass
    if len(old):
        old = old[old["日期"].astype(str) != day]
    d = pd.concat([old, pd.DataFrame(new)], ignore_index=True)
    if len(d):
        d = d.sort_values(["开盘", "谁"] + (["报单时间", "报单编号"] if "报单编号" in d else ["组合"]))
    elif not len(d.columns):
        return d  # 第一次跑就是空日：不写没有表头的空文件，下次读会报错
    d.to_csv(f, index=False, encoding="utf-8-sig")
    return d


def md_table(df: pd.DataFrame) -> str:
    if df is None or not len(df):
        return "（没有）\n"
    df = df.astype(object).where(df.notna(), "")
    head = "| " + " | ".join(map(str, df.columns)) + " |\n|" + "---|" * len(df.columns) + "\n"
    return head + "".join("| " + " | ".join(f"{v:g}" if isinstance(v, float) else str(v) for v in r) + " |\n" for r in df.itertuples(index=False))


def describe(x: pd.Series) -> str:
    x = pd.to_numeric(x, errors="coerce").dropna()
    return "" if not len(x) else f"{x.median():.2f}（中间一半 {x.quantile(.25):.2f}–{x.quantile(.75):.2f}，{len(x)} 单）"


def summary(O: pd.DataFrame, P: pd.DataFrame) -> str:
    out = []
    days = O["日期"].nunique() if len(O) else 0
    out.append(f"## 累计（{days} 个交易日、{O['开盘'].nunique() if len(O) else 0} 次开盘、{len(O)} 张开盘单）\n")
    if not len(O):
        return "".join(out)
    rows = []
    for who, g in [("全部", O)] + list(O.groupby("谁")):
        filled = g[pd.to_numeric(g["开盘秒成交手数"], errors="coerce") > 0]
        rows.append({"谁": who, "开盘单": len(g), "开盘那一秒有成交": len(filled), "到达点 a 中位": describe(g["到达点a"]),
                     "正滑 w 中位": describe(g["正滑w"]), "挂在几成 中位": describe(g["几成"])})
    out.append("### 到达点 a、正滑 w、挂在几成\n\n两年回放按 a = 0.55 / 0.7 / 0.85 三种算过：a 越小（单子到得越快）越赚；w ≥ 0.5 赚、w = 0 不赚（逐单复现小结）。\n\n" + md_table(pd.DataFrame(rows)) + "\n")
    bins = pd.cut(pd.to_numeric(O["几成"], errors="coerce"), [-9, 0.1, 0.3, 0.6, 9], labels=["≤0.1 成", "0.1–0.3 成", "0.3–0.6 成", "> 0.6 成"])
    rows = []
    for lab, g in O.groupby(bins, observed=True):
        rq, fq = pd.to_numeric(g["报单手数"], errors="coerce"), pd.to_numeric(g["开盘秒成交手数"], errors="coerce")
        k = rq.notna() & fq.notna()
        rows.append({"挂在": lab, "单数": len(g), "有报单手数的": int(k.sum()), "报单手数": int(rq[k].sum()), "开盘那一秒成交": int(fq[k].sum()),
                     "进单率": f"{fq[k].sum() / rq[k].sum() * 100:.0f}%" if rq[k].sum() else "", "整张开盘就成完": int((fq[k] >= rq[k]).sum())})
    out.append("### 挂在几成 vs 进单（开盘那一秒成交手数 ÷ 报单手数）\n\n0.2 成 ≈ 浮层「近」，0.5 成 ≈「远」（首笔半）。老陆、同事多挂 0–0.3 成，挂得远的样本要靠账号 A、B。\n\n" + md_table(pd.DataFrame(rows)) + "\n")
    rows = []
    for lab in ("近", "远"):
        v = O[f"{lab}够得着"].astype(str)
        n = v.isin(["是", "否"]).sum()
        rows.append({"浮层档": lab, "能反推的单": int(n), "够得着": int((v == "是").sum()), "够得着比例": f"{(v == '是').sum() / n * 100:.0f}%" if n else ""})
    out.append("### 反推：浮层「近」「远」那一刻够不够得着（只算 1 手，用开盘那一秒的成交价当到达价）\n\n" + md_table(pd.DataFrame(rows)) + "\n")
    if len(P):
        rows = []
        for (lab, col) in (("谁", "谁"), ("判", "判")):
            for k, g in P.groupby(col):
                x = pd.to_numeric(g["正滑档"], errors="coerce").dropna()
                rows.append({"按": lab, "分组": k, "组数": len(g), "有开仓价差": len(x), "正滑档 中位": f"{x.median():+.1f}" if len(x) else "",
                             "正滑档 平均": f"{x.mean():+.1f}" if len(x) else "", "腿2 少成手数合计": int(pd.to_numeric(g["腿2少成"], errors="coerce").fillna(0).sum())})
        out.append("### 开仓价差比挂价差好几档（开盘 5 秒内两腿开仓合起来）\n\n正的 = 比挂的价好（正滑，额外利润）；腿2 少成 = 腿1 成了、腿2 5 秒内没追上的手数。\n\n" + md_table(pd.DataFrame(rows)) + "\n")
    out.append("样本少的时候只当线索：攒两三周（约 400 张开盘单）再看 a 和进单率。\n")
    return "".join(out)


DAY_COLS = ["开盘", "谁", "组合", "挂法", "判", "重仓标记", "买卖", "顺针", "报单时间", "首单", "挂价差", "几成", "报单手数", "开盘秒成交手数", "开盘秒均价",
            "到达点a", "正滑w", "正滑档(腿1)", "近够得着", "远够得着", "挂单状态"]
PAIR_SHOW = ["开盘", "谁", "组合", "挂法", "判", "买卖价差", "开盘单数", "挂价差(首单)", "几成(首单)", "腿1 5秒成交手数", "腿2 5秒成交手数", "腿2共用", "开仓价差", "正滑档", "腿2少成"]


def report(day: str, rows: list, pairs: list, notes: list) -> Path:
    ROOT.mkdir(parents=True, exist_ok=True)
    O = merge(ORDER_CSV, rows, day)
    P = merge(PAIR_CSV, pairs, day)
    OUT_DIR.mkdir(exist_ok=True)
    d, p = pd.DataFrame(rows), pd.DataFrame(pairs)
    text = [f"# 委托复盘 {day}\n\n生成于 {datetime.now():%Y-%m-%d %H:%M}，脚本 开盘tick/委托复盘.py。数字都是脚本算的。\n\n"]
    if notes:
        text.append("## 数据提示\n\n" + "".join(f"- {n}\n" for n in notes) + "\n")
    text.append(f"## 这天的开盘单（{len(d)} 张）\n\n" + md_table(d[DAY_COLS] if len(d) else d) + "\n")
    text.append(f"## 这天按组合（{len(p)} 组）\n\n" + md_table(p[PAIR_SHOW] if len(p) else p) + "\n")
    text.append(summary(O, P))
    f = OUT_DIR / f"{day}.md"
    f.write_text("".join(text), encoding="utf-8")
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("day", nargs="?", help="交易日 2026-11-05")
    ap.add_argument("--all", action="store_true", help="委托记录/ 里所有日期重算")
    a = ap.parse_args()
    days = sorted({f.name[:10] for f in ROOT.glob("????-??-??_*.csv")}) if a.all else [a.day]
    if not days or days == [None]:
        ap.error("给一个日期，或 --all")
    for day in days:
        rows, pairs, notes = run_day(day)
        f = report(day, rows, pairs, notes)
        print(f"{day}：开盘单 {len(rows)} 张、{len(pairs)} 组 → {f}")
        for n in notes:
            print("  提示：", n)


if __name__ == "__main__":
    main()
