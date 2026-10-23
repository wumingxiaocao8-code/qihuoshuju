"""跨月价差的「日内参考区间」：用天勤 1 分钟 K 线统计每组价差近 10 个交易日的日内振幅。

做法：两条腿同一分钟都有成交才算，价差 = 腿1 收盘价 − 腿2 收盘价（和跨月价差表的「价差算法」一样减）；
每个交易日（夜盘归下一个交易日）取最高 − 最低 = 当天振幅；
区间 = 最新一分钟的价差 ± 近 10 天振幅中位数的一半。它只说「平常一天会摆多大」，是统计，不判断价差该在哪。

价差组合照抄最新「价差历史」CSV 最后一天的「价差算法」，换月后第二天自动跟上。
结果写到 output/因素/日内区间.json，futures_brief.py 的跨月价差表读它，显示在「日内参考区间」一列。

平时不用手动跑：任务计划「仓储取数」（每个交易日 17:30，开盘预估.py --prep）会先跑它，再跑 futures_brief.py stock。
手动：python 日内区间.py（用原版 Python，见「说明.md」）。
"""
import csv
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import 开盘tick  # noqa: F401  加载 venv 的包、账号和连接函数
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_FILE = ROOT / "output" / "因素" / "日内区间.json"
DAYS = 10  # 统计最近几个交易日
MIN_BARS = 30  # 两腿都有成交的分钟数少于这个的交易日不算
WILD = 2.0  # 最新一天振幅超过中位数的几倍，备注「参考性差」
INE = {"sc", "lu", "nr", "bc", "ec"}


def latest_pairs() -> list[str]:
    files = sorted((ROOT / "output" / "仓储").glob("*/价差历史_*.csv"))
    if not files:
        sys.exit("找不到 output/仓储/*/价差历史_*.csv，先跑一次 futures_brief.py stock")
    with files[-1].open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    last = max(r["日期"] for r in rows)
    return list(dict.fromkeys(r["价差算法"] for r in rows if r["日期"] == last and r["价差算法"]))


def symbol(contract: str) -> str:
    code = contract.rstrip("0123456789")
    return ("INE." if code in INE else "SHFE.") + contract


def trading_day(ts: datetime) -> date:
    """夜盘（18 点以后、过零点到凌晨）归下一个交易日；只跳周末，节假日前没有夜盘，不影响。"""
    d = ts.date()
    if ts.hour >= 18:
        d += timedelta(days=1)
    if ts.hour >= 18 or ts.hour < 3:
        while d.weekday() >= 5:
            d += timedelta(days=1)
    return d


def bars(k) -> pd.DataFrame:
    t = pd.to_datetime(k.datetime, unit="ns", utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
    return pd.DataFrame({"t": t, "close": k.close, "vol": k.volume})


def one(pair: str, k: dict) -> dict:
    a, b = [s.strip() for s in pair.split("−")]
    df = bars(k[symbol(a)]).merge(bars(k[symbol(b)]), on="t", suffixes=("_a", "_b"))
    df = df[(df.vol_a > 0) & (df.vol_b > 0)].copy()
    if df.empty:
        return {"备注": "近期两条腿没有同一分钟都成交的时候"}
    df["s"] = df.close_a - df.close_b
    df["d"] = df.t.map(trading_day)
    g = df.groupby("d").s.agg(["min", "max", "last", "count"])
    g = g[g["count"] >= MIN_BARS].tail(DAYS)
    if g.empty:
        return {"备注": f"每天两腿都有成交的分钟数不到 {MIN_BARS}，成交太少"}
    amp = g["max"] - g["min"]
    med, center = float(amp.median()), float(g["last"].iloc[-1])
    notes = []
    if len(g) < DAYS:
        notes.append(f"只有 {len(g)} 天数据")
    if med > 0 and amp.iloc[-1] > WILD * med:
        notes.append(f"最近一天振幅 {amp.iloc[-1]:g} 是平时的 {amp.iloc[-1] / med:.1f} 倍，行情异常，区间参考性差")
    return {"区间": [center - med / 2, center + med / 2], "中心": center, "振幅中位": med, "振幅最大": float(amp.max()),
            "天数": len(g), "交易日": f"{g.index[-1]:%Y-%m-%d}", "取到": f"{df.t.iloc[-1]:%m-%d %H:%M}", "备注": "；".join(notes)}


def main():
    pairs = latest_pairs()
    api = 开盘tick.connect(tries=3)
    try:
        syms = sorted({symbol(c.strip()) for p in pairs for c in p.split("−")})
        k = {s: api.get_kline_serial(s, 60, data_length=8000) for s in syms}
        api.wait_update()
        res = {}
        for p in pairs:
            try:
                res[p] = one(p, k)
            except Exception as e:  # 一组出错不影响别的
                res[p] = {"备注": f"计算出错：{type(e).__name__} {e}"}
    finally:
        api.close()
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({
        "更新时间": f"{datetime.now():%Y-%m-%d %H:%M}",
        "说明": f"脚本生成（开盘tick/日内区间.py），不要手改。区间 = 最新分钟价差 ± 近 {DAYS} 个交易日日内振幅中位数的一半；统计，不是预测。",
        "价差": res}, ensure_ascii=False, indent=1), encoding="utf-8")
    for p, r in res.items():
        rng = f"{r['区间'][0]:g} ~ {r['区间'][1]:g}（振幅中位 {r['振幅中位']:g}）" if "区间" in r else "—"
        print(f"  {p}：{rng} {r.get('备注', '')}")
    print(f"日内区间 → {OUT_FILE}")


if __name__ == "__main__":
    main()
