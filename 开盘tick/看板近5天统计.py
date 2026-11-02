"""看板近 5 天统计：看板.txt 上每格价差最近 5 个交易日的分钟价差中位数和常见区间，写进 output/因素/看板近5天统计.md。
给 AI 写「看板合理区间」（经验总结/看板合理区间.md，结合仓单、历史、新闻的判断）当参考数据之一，本身不是合理区间。

用法（在本文件夹里）：python 看板合理区间.py
算法和成交复盘的「盘中开仓」一样：两腿同一分钟都有成交的分钟价差（天勤 1 分钟线），
合理价差 = 最近 5 个交易日的中位数，常见区间 = 中间八成（10%–90%）；最近 5 天比再往前 15 天挪动超过常见区间一半宽，算往上 / 往下走。
只是统计，不是预测；价差刚走了一段趋势时，「偏高 / 偏低」可能只是新的正常。
"""

import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import 成交复盘 as cf  # noqa: E402  分钟价差、分位、常量都用成交复盘的，两边口径一样（它也负责把 venv 的包加进来）
import 开盘tick as tk  # noqa: E402
import pandas as pd  # noqa: E402

BOARD_FILE = HERE / "看板.txt"
OUT = HERE.parent / "output" / "因素" / "看板近5天统计.md"
NEAR_DELIVERY = {"cu", "al", "zn", "pb", "ni", "sn", "ao", "ag", "au", "rb", "hc", "ss", "ru", "nr", "br", "sp", "bu"}  # 交割月 15 日前后到期的品种


def board_cells() -> list[tuple[str, str]]:
    """看板.txt 的格子：[(第几排第几格, 腿1-腿2)]，空格子跳过。"""
    cells = []
    rows = [ln.split("#")[0].split() for ln in BOARD_FILE.read_text(encoding="utf-8").splitlines()]
    rows = [r for r in rows if r and "=" not in r[0] and "=" not in "".join(r)]
    for i, r in enumerate(rows, 1):
        for j, item in enumerate(r, 1):
            legs = item.split(",")
            if len(legs) == 2:
                cells.append((f"{i}-{j}", "-".join(x.split(".")[-1] for x in legs)))
    return cells


def near_delivery(combo: str, today: datetime) -> str:
    """腿里有本月或下个月交割的合约就提醒一句。"""
    out = []
    for c in combo.split("-"):
        m = re.fullmatch(r"([a-z]+)(\d{2})(\d{2})", c)
        if not m:
            continue
        y, mo = 2000 + int(m[2]), int(m[3])
        months = (y - today.year) * 12 + mo - today.month
        if m[1] in ("sc", "fu", "lu"):
            months -= 1  # 原油、燃料油在交割月前一个月月底就到期
        if months <= 0 and m[1] in NEAR_DELIVERY | {"sc", "fu", "lu"}:
            out.append(f"{c} 快到期，价差容易走偏")
    return "；".join(out)


def stats(s: pd.DataFrame) -> dict:
    hist = s.dropna(subset=["交易日"])
    tdays = sorted(hist["交易日"].unique())
    recent_days = tdays[-cf.RECENT_DAYS:]
    recent = hist[hist["交易日"].isin(recent_days)]["价差"]
    earlier_days = tdays[-cf.RECENT_DAYS - cf.EARLIER_DAYS:-cf.RECENT_DAYS]
    earlier = hist[hist["交易日"].isin(earlier_days)]["价差"]
    if recent.empty:
        return {}
    lo, mid, hi = recent.quantile(0.1), recent.median(), recent.quantile(0.9)
    last_t, last = s.index[-1], s["价差"].iloc[-1]  # 最后一分钟（周五夜盘也算）
    trend = ""
    if len(earlier) >= cf.MIN_MINUTES:
        shift = mid - earlier.median()
        if abs(shift) > (hi - lo) / 2:
            trend = f"往{'上' if shift > 0 else '下'}走"
    daily = " → ".join(cf.fmt(hist[hist["交易日"] == d]["价差"].median()) for d in recent_days)
    return dict(mid=mid, lo=lo, hi=hi, mn=recent.min(), mx=recent.max(), n=len(recent),
                last=last, last_t=last_t, rank=cf.pct_rank(recent, last),
                earlier=earlier.median() if len(earlier) else None, trend=trend, daily=daily,
                days=f"{pd.Timestamp(recent_days[0]):%m-%d} ~ {pd.Timestamp(recent_days[-1]):%m-%d}")


def main():
    cells = board_cells()
    legs = sorted({c for _, cb in cells for c in cb.split("-")})
    api = tk.connect()
    try:
        k = {c: tk.minute_klines(api, cf.symbol(c)) for c in legs}
    finally:
        api.close()
    now = datetime.now()
    lines, notes, span = [], [], ""
    for pos, cb in cells:
        a, b = cb.split("-")
        r = stats(cf.spread_minutes(k[a], k[b]))
        if not r:
            lines.append(f"| {pos} | {cb} | 没有分钟数据 |  |  |  |  |  |  |  |")
            continue
        span = span or r["days"]
        note = [x for x in (near_delivery(cb, now), "两腿同时成交的分钟少，只当参考" if r["n"] < cf.MIN_MINUTES else "") if x]
        if r["last"] < r["lo"] or r["last"] > r["hi"]:
            note.append("最新价差在常见区间外")
        lines.append(
            f"| {pos} | {cb} | **{cf.fmt(r['mid'])}** | {cf.fmt(r['lo'])} ~ {cf.fmt(r['hi'])} | {cf.fmt(r['mn'])} ~ {cf.fmt(r['mx'])} "
            f"| {r['daily']} | {cf.fmt(r['last'])}（{r['last_t']:%m-%d %H:%M}，{r['rank']:.0f}%） "
            f"| {cf.fmt(r['earlier'])} | {r['trend']} | {'；'.join(note)} |")
    md = f"""# 看板近 5 天统计（不是合理区间）

更新于 {now:%Y-%m-%d %H:%M}，统计的是最近 5 个交易日（{span}）。重新算：在 `期货数据/开盘tick` 里跑 `看板近5天统计.py`。真正的合理区间（AI 结合仓单、历史、新闻写的）在 `经验总结/看板合理区间.md`。

- **5 天中位** = 最近 5 个交易日、两腿同一分钟都有成交的分钟价差的中位数；**常见区间** = 这些分钟的中间八成（10%–90%）。价差 = 腿1 − 腿2，和看板一样。
- **逐日中位**：这 5 天每天的中位数，从左到右是从早到晚，看价差在往哪边挪。
- **最新价差**：最后一分钟两腿都有成交的价差，括号里是时间和它在这 5 天里的位置（比它低的分钟占几成）。
- **趋势**：最近 5 天中位比再往前 15 天中位挪动超过常见区间一半宽，算往上 / 往下走。有趋势时「偏高 / 偏低」可能只是新的正常（交易.md：不要逆趋势的逢高空或逢低多）。
- 口径和成交复盘的「盘中开仓」一样。只是统计，不是预测，也不是买卖建议。

| 格子（排-格） | 组合 | 5 天中位 | 常见区间 | 5 天最低 ~ 最高 | 逐日中位 | 最新价差 | 前 15 天中位 | 趋势 | 备注 |
|---|---|---|---|---|---|---|---|---|---|
""" + "\n".join(lines) + "\n"
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(md, encoding="utf-8")
    print(f"写好了：{OUT}")


if __name__ == "__main__":
    main()
