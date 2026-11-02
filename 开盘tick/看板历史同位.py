"""看板历史同位：看板每组价差在过去 3 年「同一位置」（两条腿离当月一样远）时在哪，写进 output/因素/看板历史同位.json。
用 futures_brief.py 缓存的上期所 / 上期能源日行情结算价（output/缓存/历史/kx_*.dat.gz，跑 stock 会更新）。
- 月底同位：每月 24 号以后的日子（离到期一样远），按「价差 ÷ 腿2价格」统计，再按今天的腿2价格换算成元（跨品种直接用元）。
- 往年 10 月下旬：同一日历位置，每年一个中位数（月份跟着今天变）。
给 AI 写 经验总结/看板合理区间.md 当参考，不是预测。用法：python 看板历史同位.py
"""
import gzip, json, re, statistics as st
from datetime import date
from pathlib import Path
H = Path(r"C:\Users\Administrator\Desktop\期货数据\output\缓存\历史")
BOARD_FILE = Path(__file__).resolve().parent / "看板.txt"


def board_combos() -> list[tuple[str, str, int, str, int]]:
    """看板.txt 的格子 → [(组合, 品种1, 年月1, 品种2, 年月2)]；年月写成 2701 这种。"""
    out = []
    for ln in BOARD_FILE.read_text(encoding="utf-8").splitlines():
        ln = ln.split("#")[0]
        if "=" in ln:
            continue
        for item in ln.split():
            legs = [x.split(".")[-1] for x in item.split(",")]
            if len(legs) == 2:
                m = [re.fullmatch(r"([a-z]+)(\d{4})", x) for x in legs]
                out.append(("-".join(legs), m[0][1], int(m[0][2]), m[1][1], int(m[1][2])))
    return out


def offset(yymm: int, d: date) -> int:
    """合约月份离 d 所在月份几个月（10 月时 2612 = 2）。"""
    return (2000 + yymm // 100 - d.year) * 12 + yymm % 100 - d.month
def ym(d, off):
    m = d.month - 1 + off
    return f"{(d.year + m // 12) % 100:02d}{m % 12 + 1:02d}"
Q = {}
for f in sorted(H.glob("kx_*.dat.gz")):
    ds = f.name[3:11]; d = date(int(ds[:4]), int(ds[4:6]), int(ds[6:]))
    try: js = json.loads(gzip.open(f).read())
    except Exception: continue
    q = {}
    for x in js.get("o_curinstrument") or []:
        if str(x.get("PRODUCTCLASS")) != "1": continue
        try: q[(x["PRODUCTGROUPID"].strip(), str(x["DELIVERYMONTH"]).strip())] = (float(x["SETTLEMENTPRICE"]), float(x["OPENINTEREST"]))
        except Exception: pass
    Q[d] = q
def pct(v, p): v = sorted(v); return v[min(len(v) - 1, int(p * len(v)))]
TODAY = max(Q)
out = {}
for cb, p1, m1, p2, m2 in board_combos():
    o1, o2 = offset(m1, TODAY), offset(m2, TODAY)
    t = Q[TODAY]; a = t.get((p1, ym(TODAY, o1))); b = t.get((p2, ym(TODAY, o2)))
    now = a[0] - b[0]; price2 = b[0]
    rows = []
    for d, q in Q.items():
        if d >= TODAY.replace(day=1): continue
        x = q.get((p1, ym(d, o1))); y = q.get((p2, ym(d, o2)))
        if not x or not y or x[0] <= 0 or y[0] <= 0: continue
        rows.append((d, x[0] - y[0], (x[0] - y[0]) / y[0], min(x[1], y[1])))
    same = [r for r in rows if r[0].day >= 24]                       # 每月月底这几天（同样离到期多远）
    oct_ = [r for r in rows if r[0].month == TODAY.month and abs(r[0].day - TODAY.day) <= 7]  # 往年同月、日期前后一周
    yr = {}
    for r in oct_: yr.setdefault(r[0].year, []).append(r)
    mx = max(rows, key=lambda r: r[1]); mn = min(rows, key=lambda r: r[1])
    cross = p1 != p2
    conv = (lambda r: r[1]) if cross else (lambda r: r[2] * price2)  # 跨品种直接用元，同品种按今天腿2价格换算
    sv = [conv(r) for r in same]
    o = dict(现在=now, 腿2价=price2, 腿1持仓=a[1], 腿2持仓=b[1],
             月底同位_样本=len(same), 月底同位_10_50_90=[round(pct(sv, .1)), round(st.median(sv)), round(pct(sv, .9))] if sv else None,
             现在在月底同位里的位置=round(100 * sum(v < now for v in sv) / len(sv)) if sv else None,
             往年同期={y: dict(中位元=round(st.median(r[1] for r in v), 1), 中位折今=round(st.median(conv(r) for r in v)), 持仓=round(st.median(r[3] for r in v)), 天=len(v)) for y, v in sorted(yr.items())},
             三年最高=(round(mx[1], 1), str(mx[0])), 三年最低=(round(mn[1], 1), str(mn[0])),
             利息3pct=round(0.03 * abs(o1 - o2) / 12 * price2, 1))
    out[cb] = o
    print(cb, json.dumps(o, ensure_ascii=False))
json.dump(dict(数据日期=str(TODAY), 组合=out), open(H.parent.parent / "因素" / "看板历史同位.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
