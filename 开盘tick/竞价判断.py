"""竞价判断：20:59 / 8:59 集合竞价一出结果，判断每组价差的针会怎么开，标签贴在博易云的每个格子上，再写一份网页。

平时不用手动跑：Windows 任务计划「竞价判断」每天 20:57、8:57 自动运行（电脑那时没开就不补跑，过了开盘没意义）。
全是脚本算的，不调用 AI。规则是 2026-10-23 从 4 次开盘、15 根偏出门槛的针里看出来的（v1，试运行），常量都在下面，攒多了再调。

符号（针真假是小草定的，门槛见 开盘复盘.py 的 NEEDLE_GATE）：
  ✅ 大概率整根开到针附近（真：首笔离针 1 档以内，或比针走得还远）
  ▲ 大概率开不到整根、但离昨收有段距离（半真：首笔偏出门槛、比针小，照样能开到）
  ❌ 大概率假（首笔在门槛以内，几乎没动）
  ○ 没针（针本身没偏出门槛）  ？ 不判断（有一腿竞价没成交、没收到快照，或还没定门槛的跨品种组合）
两步：
  1. 先挑 ❌：竞价量分 + 盘口分，合计 ≤ −1 就是 ❌（盘口缺一边时不判 ❌：多半是涨跌停锁住，sc 两年里两腿都锁住的 3 根针都整根开到；
     立刻能成交那边偏出门槛 2 倍以上时也不判 ❌：sc、nr、lu 两年里这样被判 ❌ 的 5 根全开到了门槛外，见 HEAVY）。
     竞价量分：冷门腿（开盘前持仓少的那条腿）竞价手数 ÷ 开盘前持仓，≥ 1.5‰ 得 +1，< 0.7‰ 得 −1；
               竞价后持仓减少超过竞价手数一半（竞价里平仓为主），竞价量再大也算 −1。
     盘口分：竞价后的挂单还托不托得住针。针往上看「立刻能卖」的价差（腿1买一 − 腿2卖一），针往下看「立刻能买」（腿1卖一 − 腿2买一），
             它离昨收价差的距离是针的几成：≥ 75% 得 +1，40%–75% 得 0，< 40% 得 −1；盘口缺一边得 0。
  2. 剩下的：针不到门槛的 1.5 倍 → ✅，否则 ▲。
     sc 换成混合版（2026-10-24，sc 两年 L2 回放定的，见 MIX）：第 1 步照旧；剩下的猜首笔 = 昨收价差 + 0.9 ×（立刻能成交那边 − 昨收价差），
     按针真假的定义分成 ✅ ▲ ❌（猜的首笔往反方向偏出门槛也算 ❌）。两年 153 根针：三档说对 59% → 71%，开不开得到 66% → 76%。
     10-24 晚放宽（SLACK）：猜的首笔差门槛 5 档以内也算 ▲、不判 ❌（他定的：真针判成 ❌ 最怕，假针判成 ▲ 无所谓）。
     两年 153 根：真针判成 ❌ 7 → 3 根，判 ❌ 的里面真开不到的 74% → 83%；代价是假针少认出 16 根，三档说对 72%（修好压线那根后）→ 66%。
     v1 会判成什么另记一列（v1档），开盘复盘的规则五两个对照着看。
首笔区间（第一次真实成交大概在哪），按一档取整（下沿往下、上沿往上）：
  sc（FIT）：最近 150 次开盘（两年回放 + 实盘）的八成范围。顺着针的方向数，离昨收价差几档：下沿 = a + b × 立刻能成交那边偏几档 + c × 针偏几档，
             上沿同样一条，用分位回归（P10、P90）每次开盘前重算。两年回放的考试段：包住约八成（原来九成），针偏出门槛的宽度中位 29 → 23 档。
  其他品种：「竞价后盘口立刻能成交的价差，两头各放 1 档」和「昨收价差 + 针变化 × 保留比例的八成范围」合在一起。
  保留比例 = 首笔偏几档 ÷ 针偏几档，用开盘复盘.csv 里这次以前、同品种组合、针偏 3 档以上的；
  判成同一种符号的攒够 8 根就只用这种的，否则用全部（够 10 根），再不够用 17%–131%（2026-10-23 从 4 次开盘 30 根针算的）。
重仓标记（2026-10-24 晚，sc、nr、lu 两年 L2 回放：分析/2026-10-24/大波动指标/小结.md 第 7 节），csv 的「重仓标记」：
  立刻能成交那边偏出门槛 2 倍以上 = 大机会，记「重3.6」（几倍门槛）；再看两件事：
  主力（腿2）竞价跳空比腿1 小 → 加「腿2秒进」（腿2 晚两秒才进多半亏，10.8 那种）；
  冷门腿竞价不到持仓 0.7‰ → 加「量少」（开盘第一个半秒能成交的手数只有其他大机会的一半到三分之二）。
  浮层上只在 HEAVY_SHOW 的品种（sc、nr）跨月格子上贴，别的品种只记进 csv 和网页，攒几周对过再说。

结果：数据/开盘日期_时间/竞价判断.csv（开盘复盘对答案用）、竞价判断/开盘日期_时间.html（网页），
      数据/开盘日期_时间/竞价判断.json（给浮层读）。开盘后 1 分钟（21:01、9:01）标签自动消失。

手动跑（在本文件夹里）：
  python 竞价判断.py                          判断下一次开盘（只在开盘前 1.5–10 分钟里跑，加 --force 不管时间）
  python 竞价判断.py --replay 2026-10-21_2100   回放：用存好的 tick 算那次开盘，结果放「复盘数据/回放/」，不碰正式的；
                                              标签照样贴在博易云上 60 秒（--hold 改秒数）
  --no-overlay  不贴标签，只写文件
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_FILE = HERE / "运行日志.txt"
if sys.stdout is None:  # 任务计划用 pythonw 运行，没有控制台，输出写进运行日志
    sys.stdout = sys.stderr = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
else:  # 手动跑、输出接到管道里时按 GBK 编码，✅ ▲ 这类印不出来就换成 ?，不报错
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

import 开盘tick as tick  # noqa: E402  天勤账号（不读出来）、合约清单（顺带加载日记库环境里的包）
import 开盘复盘 as rv  # noqa: E402  门槛、最小变动价位、读 tick、网页样式
import 开盘预估 as pre  # noqa: E402  下一次开盘、这次有没有开盘
import 竞价浮层 as fl  # noqa: E402  看板.txt

import pandas as pd  # noqa: E402

PAGE_DIR = HERE / "竞价判断"  # 每次开盘的网页：竞价判断/2026-10-23_2100.html
REPLAY_DIR = rv.REVIEW / "回放"
CSV_NAME, JSON_NAME = "竞价判断.csv", "竞价判断.json"
# ---- 判断规则 v1（2026-10-23，4 次开盘、15 根针；攒多了再调）----
RATIO_LO, RATIO_HI = rv.RATIO_BINS[0][0], rv.RATIO_BINS[1][0]  # 冷门腿竞价占持仓 0.7‰、1.5‰（跟记分卡规则四同一组线）
CLOSE_OUT = 0.5  # 竞价后持仓减少超过竞价手数的一半 = 竞价里平仓为主
HOLD_HI, HOLD_LO = 0.75, 0.40  # 盘口托住针几成
BIG = 1.5  # 针 ≥ 门槛的 1.5 倍：大概率开不到整根（▲）
RET_MIN, RET_N, RET_KIND_N = 3, 10, 8  # 保留比例：用针偏 3 档以上的；全部够 10 根、同一种符号够 8 根才用
RET_DEFAULT = (0.17, 1.31)  # 2026-10-23 从 4 次开盘 30 根针算的八成范围
# ---- sc 换成混合版（2026-10-24，sc 两年 L2 回放：分析/2026-10-24/sc两年tick回放/小结.md）----
MIX = {"sc": 0.9}  # 第 2 步改成猜首笔 = 昨收价差 + 0.9 ×（立刻能成交那边 − 昨收价差）；0.7–0.95 成绩差不多
SLACK = {"sc": 5}  # 混合版猜的首笔差门槛 5 档以内也算 ▲、不判 ❌（10-24 晚他定的：真针判成 ❌ 最怕，假针判成 ▲ 无所谓）
FIT = ("sc",)  # 首笔区间用最近 150 次开盘的分位回归的品种（别的品种没有历史，还用老办法）
FIT_N, FIT_MIN, FIT_Q = 150, 60, (0.1, 0.9)  # 最近 150 次开盘，不够 60 次用老办法；八成范围（P10–P90）
SEED = rv.REVIEW / "两年回放_{}.csv"  # 两年回放切出来的开盘（只留本机），跟开盘复盘.csv 的实盘行一起用
# ---- 重仓标记（2026-10-24 晚，sc、nr、lu 两年 L2 回放：分析/2026-10-24/大波动指标/小结.md 第 7 节）----
HEAVY = 2.0  # 立刻能成交那边偏出门槛 2 倍以上 = 大机会：不判 ❌（两年 5 次被判 ❌ 的全开到了门槛外），记「重仓标记」
HEAVY_SHOW = ("sc", "nr")  # 浮层只在这几个品种的跨月格子上贴重仓标记（只验了这两个），别的品种只记进 csv 和网页
TAG_STYLE = {"腿2秒进": "快", "量少": "少"}  # 浮层按样式上色：「重…」紫底、快 红框、少 黄框
SYMBOL = {"真": "✅", "半真": "▲", "假": "❌", "没针": "○", "不判断": "？"}
SAY = {"真": "大概率整根开到", "半真": "大概率开到一部分", "假": "大概率开不到（假）"}
KINDS = tuple(SYMBOL)
# ---- 实盘的时间 ----
SHOW = 60  # 标签开盘后再留 60 秒
EARLIEST, LATEST = timedelta(minutes=10), timedelta(seconds=90)  # 开盘前 10 分钟以内、HH:58:30 以前才跑
AUCTION = timedelta(seconds=60)  # 竞价那张快照在开盘前一分钟里（20:59:00.5）
GIVE_UP = timedelta(seconds=20)  # HH:59:40 还没收到竞价快照，就当这次没开盘
SETTLE = 3  # 第一张竞价快照到了以后，最多再等 3 秒等其余的
NTP = ("ntp.aliyun.com", "ntp.tencent.com", "cn.pool.ntp.org")  # 问标准时间（10-23 晚发现这台电脑的时钟慢了 14.7 秒）
CLOCK_WARN = 3  # 电脑时钟差 3 秒以上，提示里说一声
CSV_COLS = ["开盘", "组合", "腿1", "腿2", "品种", "档", "符号", "首笔下沿", "首笔上沿", "理由", "昨收价差", "腿1竞价", "腿2竞价",
            "针", "针偏几档", "门槛", "冷门腿", "冷门腿竞价手数", "冷门腿竞价占持仓‰", "冷门腿竞价持仓变化", "竞价量分",
            "立刻能卖", "立刻能买", "托住几成", "盘口分", "合计", "保留比例下", "保留比例上", "保留比例依据", "腿1盘口宽档",
            "腿1竞价手数", "腿1卖一量", "腿1买一量", "腿2竞价手数", "腿2卖一量", "腿2买一量", "一档", "文字", "快照时间", "算好时间", "来源",
            "v1档", "猜首笔", "区间依据", "立刻能成交几倍门槛", "腿1跳空档", "腿2跳空档", "主力动得少", "重仓标记"]


# ---------- 判断（实盘、回放、开盘复盘共用） ----------

def half_up(x: float) -> int:
    """四舍五入到整档（0.5 往远离 0 的方向），跟 开盘复盘.needle_truth 一样。"""
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def decimals(t: float) -> int:
    """一档 0.1 → 1 位小数，0.02 → 2 位，1、5 → 整数。"""
    return max(0, math.ceil(-math.log10(t) - 1e-9))


def price(x, t: float) -> str:
    return "" if x is None else f"{x:.{decimals(t)}f}"


def judge(a: dict | None, b: dict | None, close1, close2, t: float, gate, names=("腿1", "腿2"), mix=None, slack=0) -> dict:
    """a、b = 两腿竞价那张快照 {竞价价（竞价没成交是 None）, 竞价手数, 持仓（竞价后）, 持仓变化, 买一, 卖一, 买一量, 卖一量}；
    close1、close2 = 两腿昨收；t = 价差的一档；gate = 门槛（档，None = 还没定）；names = 两腿的合约名（写理由用）；
    mix = 混合版的系数（MIX，None = v1）；slack = 混合版猜的首笔差门槛几档以内也不判 ❌（SLACK）。
    返回 档（真 / 半真 / 假 / 没针 / 不判断）、v1档、理由和每一步算的数。
    首笔区间另外算（first_range、fit_range）。"""
    out = {"档": "不判断"}
    if a is None or b is None:
        out["理由"] = "、".join(f"{n} 没收到竞价快照" for n, x in zip(names, (a, b)) if x is None)
        return out
    out["腿1竞价"], out["腿2竞价"] = a["竞价价"], b["竞价价"]
    if a["买一"] is not None and b["卖一"] is not None:
        out["立刻能卖"] = round(a["买一"] - b["卖一"], 6)
    if a["卖一"] is not None and b["买一"] is not None:
        out["立刻能买"] = round(a["卖一"] - b["买一"], 6)
    if a["买一"] is not None and a["卖一"] is not None:
        out["腿1盘口宽档"] = round((a["卖一"] - a["买一"]) / t, 1)
    if close1 is None or close2 is None:
        out["理由"] = "没有昨收价"
        return out
    base = round(close1 - close2, 6)
    out["昨收价差"] = base
    if a["竞价价"] is None or b["竞价价"] is None:
        out["理由"] = "、".join(f"{n} 竞价没成交" for n, x in zip(names, (a, b)) if x["竞价价"] is None)
        return out
    needle = round(a["竞价价"] - b["竞价价"], 6)
    dev = (needle - base) / t
    d = half_up(dev)
    out |= {"针": needle, "针偏几档": round(dev, 1), "门槛": gate}
    g1, g2 = round((a["竞价价"] - close1) / t, 6), round((b["竞价价"] - close2) / t, 6)  # 两腿竞价比昨收跳了几档
    out |= {"腿1跳空档": round(g1, 1), "腿2跳空档": round(g2, 1), "主力动得少": abs(g2) < abs(g1)}  # 腿2 = 主力（看板上都是次主力 − 主力）
    if gate is None:
        out["理由"] = f"还没定门槛（针偏 {d:+d} 档）"
        return out
    if abs(d) < gate:
        out |= {"档": "没针", "理由": f"针偏 {d:+d} 档，没出门槛 {gate} 档"}
        return out
    # 第 1 步：竞价量分 + 盘口分，合计 ≤ −1 → 假
    why, vol, few = [], 0, False
    legs = []
    for n, x in zip(names, (a, b)):
        if x["持仓"] is not None and x["持仓变化"] is not None and x["持仓"] - x["持仓变化"] > 0:
            legs.append((x["持仓"] - x["持仓变化"], n, x))  # 开盘前持仓 = 竞价后持仓 − 持仓变化
    if legs:
        oi, n, x = min(legs, key=lambda z: z[0])
        ratio = round(x["竞价手数"] * 1000 / oi, 6)  # 取到 6 位小数，正好压线时不会差一点点（同下面托住几成）
        vol = 1 if ratio >= RATIO_HI else -1 if ratio < RATIO_LO else 0
        few = ratio < RATIO_LO  # 重仓标记的「量少」：只看竞价量占持仓，不管平仓为主
        out |= {"冷门腿": n, "冷门腿竞价手数": int(x["竞价手数"]), "冷门腿竞价占持仓‰": round(ratio, 2),
                "冷门腿竞价持仓变化": int(x["持仓变化"])}
        why.append(f"冷门腿 {n} 竞价 {int(x['竞价手数'])} 手、占持仓 {ratio:.2f}‰（{vol:+d}）")
        if x["持仓变化"] < -CLOSE_OUT * x["竞价手数"]:  # 竞价里平仓为主：量再大也不算数（10-22 夜盘 fu2702 竞价 3054 手，假针）
            vol = -1
            why.append(f"竞价后持仓少了 {-int(x['持仓变化'])} 手，平仓为主，竞价量分算 -1")
    else:
        why.append("没有持仓数，竞价量不打分（0）")
    out["竞价量分"] = vol
    k = "立刻能卖" if d > 0 else "立刻能买"
    book, times = 0, None
    if k in out:
        hold = round((out[k] - base) / (needle - base), 6)  # 按价格相除，正好 75% 会算成 74.99…%，先取到 6 位小数（2026-04-22 早盘因此误判 ❌）
        book = 1 if hold >= HOLD_HI else 0 if hold >= HOLD_LO else -1
        out["托住几成"] = round(hold, 2)
        times = round((out[k] - base) / t, 6) * (1 if d > 0 else -1) / gate  # 立刻能成交那边顺着针的方向偏出几倍门槛
        out["立刻能成交几倍门槛"] = round(times, 2)
        why.append(f"竞价后{k}的价差 {price(out[k], t)}，托住针 {hold * 100:.0f}%（{book:+d}），偏出门槛 {times:.1f} 倍")
    else:
        why.append(f"盘口缺一边，看不到{k}的价差（0）")
    out["盘口分"] = book
    out["合计"] = vol + book
    heavy = times is not None and times >= HEAVY - 1e-9
    if vol + book <= -1 and k in out and not heavy:
        out["档"] = out["v1档"] = "假"
        why.append(f"合计 {vol + book:+d} → 大概率假")
        out["理由"] = "；".join(why)
        return out
    # 盘口缺一边（sc 两年里 7 次全是竞价涨跌停锁住）：看不到托不托得住，光凭竞价量分不判 ❌（两腿都锁住的 3 根针都整根开到）；
    # 立刻能成交那边偏出门槛 2 倍以上：也不判 ❌（sc、nr、lu 两年里这样被判 ❌ 的 5 根全开到了门槛外，红利 8–54 档）
    locked = ("" if vol + book > -1 else
              f"，但立刻能成交那边偏出门槛 {times:.1f} 倍（≥ {HEAVY:g} 倍，两年回放里这样的全开到了门槛外），不判 ❌" if heavy else
              "，但盘口缺一边（多半是涨跌停锁住），光凭竞价量分不判 ❌")
    v1 = out["v1档"] = "真" if abs(d) < BIG * gate else "半真"
    v1_why = f"针偏 {abs(d)} 档，{'不到' if v1 == '真' else '到了'}门槛 {gate} 档的 {BIG} 倍"
    if mix is not None and k in out:
        # 混合版：竞价后立刻能成交的价差打个折当首笔，按针真假的定义分（往反方向偏出门槛的也开不到，算假）
        g = round(mix * round((out[k] - base) / t, 6), 6)  # 猜的首笔偏几档（先取到 6 位小数，免得 13.5 档算成 13.4999…）
        guess = base + g * t
        kind = rv.needle_truth(dev, g, gate)
        out["档"] = "假" if kind == "反向" else kind
        m = half_up(g) if d > 0 else -half_up(g)  # 猜的首笔顺着针的方向偏几档
        near = out["档"] == "假" and m >= gate - slack  # 差门槛 slack 档以内：算 ▲，不判 ❌
        if near:
            out["档"] = "半真"
        out["猜首笔"] = round(guess, decimals(t) + 1)
        why.append(f"合计 {vol + book:+d}{locked}；猜首笔 = 昨收价差 + {mix} ×（{k} − 昨收价差）= {guess:.{decimals(t) + 1}f}，偏 {half_up(g):+d} 档"
                   f"（针 {d:+d} 档、门槛 {gate} 档{f'，差门槛 {gate - m} 档、在 {slack} 档以内，不判 ❌' if near else ''}）"
                   f"→ {SAY[out['档']]}；v1 会判 {SYMBOL[v1]}（{v1_why}）")
    else:
        out["档"] = v1
        why.append(f"合计 {vol + book:+d}{locked}；{v1_why} → {SAY[v1]}" + ("（盘口缺一边，按 v1）" if mix is not None else ""))
    if heavy and out["档"] in ("真", "半真"):  # 大机会：重仓标记（浮层上贴不贴看 HEAVY_SHOW）
        out["重仓标记"] = " ".join([f"重{times:.1f}"] + ["腿2秒进"] * out["主力动得少"] + ["量少"] * few)
        why.append(f"重仓标记「{out['重仓标记']}」：立刻能成交那边偏出门槛 {times:.1f} 倍"
                   + (f"；主力竞价跳了 {g2:+.0f} 档、腿1 {g1:+.0f} 档，主力动得少，腿2 要秒进" if out["主力动得少"] else "")
                   + (f"；冷门腿竞价只占持仓 {out['冷门腿竞价占持仓‰']:.2f}‰，能成交的手数少" if few else ""))
    out["理由"] = "；".join(why)
    return out


def retention(past, before: datetime, kind: str | None = None) -> tuple:
    """首笔保留针几成的八成范围 (下, 上, 依据)：开盘复盘.csv 里 before 以前的开盘、同品种组合、针偏 3 档以上的。"""
    if past is None or not len(past) or "首笔偏几档" not in past:
        return RET_DEFAULT + ("默认（还没有开盘复盘）",)
    p = past[pd.to_datetime(past["开盘"], format="%Y-%m-%d %H:%M", errors="coerce") < pd.Timestamp(before)]
    p = p[~p["品种"].astype(str).str.contains("-") & p["针偏几档"].notna() & p["首笔偏几档"].notna()]
    p = p[p["针偏几档"].abs() >= RET_MIN]
    r = p["首笔偏几档"] / p["针偏几档"]
    if kind and "59分判断" in p:
        k = r[p["59分判断"] == kind]
        if len(k) >= RET_KIND_N:
            return float(k.quantile(.1)), float(k.quantile(.9)), f"过去判成 {SYMBOL[kind]} 的 {len(k)} 根针"
    if len(r) >= RET_N:
        return float(r.quantile(.1)), float(r.quantile(.9)), f"过去 {len(r)} 根针"
    return RET_DEFAULT + (f"默认（过去只有 {len(r)} 根针，不够 {RET_N} 根）",)


def first_range(j: dict, t: float, ret: tuple) -> tuple | None:
    """首笔区间：盘口立刻能成交的价差两头各放 1 档，和 昨收价差 + 针变化 × 保留比例的八成范围，合在一起；按一档取整。"""
    bands = []
    if "立刻能卖" in j and "立刻能买" in j:
        bands.append((min(j["立刻能卖"], j["立刻能买"]) - t, max(j["立刻能卖"], j["立刻能买"]) + t))
    if "针" in j and "昨收价差" in j:
        m = j["针"] - j["昨收价差"]
        x, y = j["昨收价差"] + m * ret[0], j["昨收价差"] + m * ret[1]
        bands.append((min(x, y), max(x, y)))
    if not bands:
        return None
    lo, hi = min(b[0] for b in bands), max(b[1] for b in bands)
    return snap_out(lo, hi, t)


def snap_out(lo: float, hi: float, t: float) -> tuple:
    """按一档取整：下沿往下、上沿往上。"""
    nd = decimals(t)
    return round(math.floor(lo / t + 1e-6) * t, nd), round(math.ceil(hi / t - 1e-6) * t, nd)


def axes(dev, base, sell, buy, t: float) -> tuple:
    """顺着针的方向数档（针往下就把正负号翻过来）：X = 针偏几档，E = 立刻能成交那边偏几档（针往上看立刻能卖、往下看立刻能买）。
    返回 (方向 ±1, X, E)；立刻能成交那边没有就是 None。"""
    s = -1 if dev < 0 else 1
    e = num(sell if s > 0 else buy)
    return s, abs(dev), None if e is None else s * (e - base) / t


_FITS = {}  # (品种, 开盘) → 拟合结果，同一次开盘只算一次


def range_fit(past, before: datetime, product: str) -> dict | None:
    """首笔区间的分位回归：两年回放（SEED）+ 开盘复盘.csv 里 before 以前的开盘，取最近 FIT_N 次。
    顺着针的方向，首笔偏几档 Y 的 P10、P90 各拟合一条 a + b × E + c × X。不够 FIT_MIN 次、或算不出来，返回 None（用老办法）。"""
    key = (product, before)
    if key in _FITS:
        return _FITS[key]
    _FITS[key] = None
    t = rv.TICK.get(product)
    try:
        live = past is not None and {"立刻能卖", "立刻能买", "针偏几档", "首笔偏几档"} <= set(past.columns)
        parts = [p for p in (rv.read_csv(Path(str(SEED).format(product))), past[past["品种"] == product] if live else None)
                 if p is not None and len(p)]
        if not parts or not t:
            return None
        d = pd.concat(parts, ignore_index=True)  # 两年回放在前、实盘在后：同一次开盘两边都有的，留实盘的
        d = d[pd.to_datetime(d["开盘"], format="%Y-%m-%d %H:%M", errors="coerce") < pd.Timestamp(before)]
        d = d.dropna(subset=["昨收价差", "针偏几档", "首笔偏几档", "立刻能卖", "立刻能买"])
        d = d.drop_duplicates(["开盘", "组合"], keep="last").sort_values("开盘").tail(FIT_N)
        if len(d) < FIT_MIN:
            return None
        rows = [axes(r["针偏几档"], r["昨收价差"], r["立刻能卖"], r["立刻能买"], t) + (r["首笔偏几档"],) for _, r in d.iterrows()]
        A = [[1.0, e, x] for s, x, e, y in rows]
        Y = [s * y for s, x, e, y in rows]
        lo, hi = (qreg(A, Y, q) for q in FIT_Q)
    except Exception as e:  # 种子文件坏了、scipy 没装好、算不出来：用老办法，不耽误判断
        print(f"{product} 首笔区间的分位回归没算出来，用老办法：{type(e).__name__} {e}")
        return None
    _FITS[key] = {"下": lo, "上": hi, "次数": len(d), "起": d["开盘"].iloc[0], "止": d["开盘"].iloc[-1]}
    return _FITS[key]


def qreg(A: list, y: list, q: float) -> list:
    """分位回归（第 q 分位的直线）：解一个线性规划，让落在线下面的正好约 q 成。"""
    import numpy as np
    from scipy.optimize import linprog
    A, y = np.asarray(A, float), np.asarray(y, float)
    n, p = A.shape
    c = np.r_[np.zeros(p), q * np.ones(n), (1 - q) * np.ones(n)]
    r = linprog(c, A_eq=np.c_[A, np.eye(n), -np.eye(n)], b_eq=y, bounds=[(None, None)] * p + [(0, None)] * (2 * n),
                method="highs")
    if not r.success:
        raise RuntimeError(r.message)
    return [float(v) for v in r.x[:p]]


def fit_range(j: dict, t: float, fit: dict | None) -> tuple | None:
    """sc 的首笔区间：顺着针的方向，下沿 = a + b × E + c × X 档（P10），上沿同样（P90），换回价差、按一档取整。
    没有拟合结果、没有针或立刻能成交那边，返回 None（用老办法）。"""
    if fit is None or "针" not in j or "昨收价差" not in j:
        return None
    base = j["昨收价差"]
    s, x, e = axes((j["针"] - base) / t, base, j.get("立刻能卖"), j.get("立刻能买"), t)
    if e is None:
        return None
    lo, hi = (c[0] + c[1] * e + c[2] * x for c in (fit["下"], fit["上"]))
    a, b = base + s * min(lo, hi) * t, base + s * max(lo, hi) * t
    return snap_out(min(a, b), max(a, b), t)


def fit_note(fit: dict) -> str:
    a, b = fit["下"], fit["上"]
    return (f"最近 {fit['次数']} 次开盘（{fit['起'][:10]} ~ {fit['止'][:10]}）的八成范围：顺着针的方向，"
            f"下沿 {a[0]:+.1f} {a[1]:+.2f}×E {a[2]:+.2f}×X 档、上沿 {b[0]:+.1f} {b[1]:+.2f}×E {b[2]:+.2f}×X 档"
            "（E = 立刻能成交那边偏几档，X = 针偏几档）")


def evaluate(o: datetime, pairs: list, snaps: dict, closes: dict, ticks: dict, past, source: str,
             missing: dict | None = None) -> list[dict]:
    """每组一行：判断 + 首笔区间 + 标签上的字。snaps = {合约: 快照}，closes = {合约: 昨收}，ticks = {合约: 最小变动价位}，
    missing = {合约: 为什么没有快照（已到期、订阅不了）}。"""
    rows, missing = [], missing or {}
    for s1, s2 in pairs:
        c1, c2 = s1.split(".")[-1], s2.split(".")[-1]
        p1, p2 = rv.product_of(s1), rv.product_of(s2)
        t = ticks.get(s1) or rv.TICK.get(p1) or 1
        if p1 != p2:  # 跨品种：两腿一档不一样时，价差按小的那个算
            t = min(t, ticks.get(s2) or rv.TICK.get(p2) or t)
        bad = [f"{s.split('.')[-1]} {missing[s]}" for s in (s1, s2) if s in missing]
        if bad:
            j = {"档": "不判断", "理由": "、".join(bad)}
        else:
            j = judge(snaps.get(s1), snaps.get(s2), closes.get(s1), closes.get(s2), t,
                      rv.NEEDLE_GATE.get(p1) if p1 == p2 else None, (c1, c2), MIX.get(p1) if p1 == p2 else None,
                      SLACK.get(p1, 0) if p1 == p2 else 0)
        fit = range_fit(past, o, p1) if p1 == p2 and p1 in FIT else None
        rg = fit_range(j, t, fit)
        row = {"开盘": f"{o:%Y-%m-%d %H:%M}", "组合": f"{c1}-{c2}", "腿1": c1, "腿2": c2,
               "品种": p1 if p1 == p2 else f"{p1}-{p2}", **j, "符号": SYMBOL[j["档"]], "一档": t, "来源": source}
        if rg:
            row["区间依据"] = fit_note(fit)
        else:  # 老办法：盘口两头各 1 档 + 保留比例
            ret = retention(past, o, j["档"] if j["档"] in ("真", "半真", "假") else None)
            rg = first_range(j, t, ret)
            row |= {"保留比例下": round(ret[0], 2), "保留比例上": round(ret[1], 2), "保留比例依据": ret[2],
                    "区间依据": f"盘口立刻能成交的价差两头各 1 档 + 保留比例（{ret[2]}）"}
        if rg:
            row |= {"首笔下沿": rg[0], "首笔上沿": rg[1]}
        name = fl.label_name(s1, s2)
        row["文字"] = f"{name}  首笔 {price(rg[0], t)} ~ {price(rg[1], t)}" if rg else f"{name}  {j['理由']}"
        for n, s in (("腿1", s1), ("腿2", s2)):  # 竞价成交手数（浮层上标签左边的小框）和竞价后的盘口挂单手数（只记进 csv）
            x = snaps.get(s) or {}
            row[f"{n}竞价手数"], row[f"{n}卖一量"], row[f"{n}买一量"] = x.get("竞价手数"), x.get("卖一量"), x.get("买一量")
        snap_t = [x["时间"] for x in (snaps.get(s1), snaps.get(s2)) if x and x.get("时间")]
        if snap_t:
            row["快照时间"] = f"{max(snap_t):%H:%M:%S.%f}"[:-5]
        rows.append(row)
    return rows


# ---------- 快照 ----------

def num(x):
    return rv.num(x)


def when(x):
    """天勤的 datetime 字符串（2026-10-23 20:59:00.500000）→ datetime；空的返回 None。"""
    try:
        return pd.Timestamp(x).to_pydatetime() if x else None
    except (ValueError, TypeError):
        return None


def snap_from_ticks(df, o: datetime) -> dict | None:
    """回放：tick 文件里竞价那张快照（开盘前一分钟里最后一张）。开盘tick.py 存的 成交手数、持仓变化 已经是跟上一张比的。"""
    if df is None:
        return None
    w = df[(df.index >= pd.Timestamp(o - AUCTION)) & (df.index < pd.Timestamp(o))]
    if not len(w):
        return None
    r = w.iloc[-1]
    vol = num(r.get("成交手数")) or 0
    return {"时间": w.index[-1].to_pydatetime(), "竞价价": num(r["最新价"]) if vol > 0 else None, "竞价手数": vol,
            "持仓": num(r.get("持仓量")), "持仓变化": num(r.get("持仓变化")), "买一": num(r.get("买一价")),
            "卖一": num(r.get("卖一价")), "买一量": num(r.get("买一量")), "卖一量": num(r.get("卖一量"))}


def before_of(q) -> dict:
    """竞价前那一刻（上一节收盘）的状态：时间、最新价、成交量、持仓。"""
    return {"时间": when(q.datetime), "最新价": num(q.last_price), "成交量": num(q.volume), "持仓": num(q.open_interest)}


def before_from_ticks(api, s: str, o: datetime) -> dict | None:
    """连上天勤时竞价快照已经到了：从最近的 tick 里找竞价前最后一张。"""
    df = api.get_tick_serial(s, data_length=30)
    df = df.dropna(subset=["datetime"])
    t = pd.to_datetime(df["datetime"].astype("int64"), utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
    df = df[(t < pd.Timestamp(o - AUCTION)).to_numpy()]
    if not len(df):
        return None
    r = df.iloc[-1]
    return {"时间": pd.Timestamp(int(r["datetime"]), tz="UTC").tz_convert("Asia/Shanghai").tz_localize(None).to_pydatetime(),
            "最新价": num(r["last_price"]), "成交量": num(r["volume"]), "持仓": num(r["open_interest"])}


def snap_from_quote(q, before: dict, o: datetime, carry: bool = False) -> dict:
    """实盘：竞价那张快照。夜盘开盘是新的交易日，成交量从 0 算起，成交量就是竞价手数；
    日盘开盘、前面有夜盘的（carry），成交量接着夜盘累计，要减掉竞价前的。竞价前那张可能是夜盘收盘那张，
    也可能是开盘前几分钟交易所又推的一张（8:5x，10-23 晚夜盘就有合约是这样），两种都要减。"""
    vol = num(q.volume) or 0
    bt = before.get("时间")
    if (carry and bt is not None and (bt.hour >= 20 or bt.hour < 3 or bt.date() == o.date())
            and before.get("成交量") is not None and vol >= before["成交量"]):
        vol -= before["成交量"]
    oi = num(q.open_interest)
    chg = oi - before["持仓"] if oi is not None and before.get("持仓") is not None else None
    return {"时间": when(q.datetime), "竞价价": num(q.last_price) if vol > 0 else None, "竞价手数": vol, "持仓": oi,
            "持仓变化": chg, "买一": num(q.bid_price1), "卖一": num(q.ask_price1),
            "买一量": num(q.bid_volume1), "卖一量": num(q.ask_volume1)}


def close_of(api, s: str, before: dict, o: datetime):
    """昨收：竞价前最后一张快照的最新价（就是上一节收盘价，跟开盘复盘用的 1 分钟线收盘一样）。
    万一那张快照是开盘前几分钟才推的（不是收盘那张），改用 1 分钟线。"""
    bt = before.get("时间")
    if before.get("最新价") is not None and bt is not None and bt < o - timedelta(minutes=6):
        return before["最新价"], "收盘快照"
    try:
        c = tick.close_before(tick.minute_klines(api, s, 30), o)
        if c:
            return c[0], "1 分钟线"
    except Exception as e:
        print(f"{s} 1 分钟线没取到：{type(e).__name__} {e}")
    return before.get("最新价"), "快照"


# ---------- 输出 ----------

def put(f: Path, data: dict) -> None:
    """写给浮层的 json：先写临时文件再换过去，浮层不会读到写了一半的。"""
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    for _ in range(50):
        try:
            os.replace(tmp, f)
            return
        except PermissionError:  # 浮层正好在读
            time.sleep(0.02)
    print(f"{f.name} 一直写不进去（被占用）")


def result_json(rows: list, state: dict, note: str, page: Path) -> dict:
    return state | {"状态": "结果", "红": False, "提示": note, "网页": str(page),
                    "组合": {r["组合"]: {"档": r["档"], "符号": r["符号"], "文字": r["文字"], "盘口": book_of(r), "标记": tags_of(r)}
                           for r in rows}}


def book_of(r: dict) -> list:
    """浮层盘口框：[[腿1, 竞价手数], [腿2, 竞价手数]]（10-24 晚起框里只写竞价成交手数，卖一买一挂单量没用，照样记进 csv）；
    两腿都没有快照（没订阅上、已到期）返回空，不画框。"""
    legs = [[r[n], r.get(f"{n}竞价手数")] for n in ("腿1", "腿2")]
    return legs if any(x[1] is not None for x in legs) else []


def tags_of(r: dict) -> list:
    """浮层上的重仓标记 [[字, 样式], …]，贴在符号后面；只有 HEAVY_SHOW 的品种（同品种跨月）贴，别的返回空。"""
    t = r.get("重仓标记")
    if r.get("品种") not in HEAVY_SHOW or not isinstance(t, str) or not t:
        return []
    return [[w, "重" if w.startswith("重") else TAG_STYLE.get(w, "少")] for w in t.split()]


def counts(rows: list) -> str:
    return "、".join(f"{SYMBOL[k]} {n}" for k in KINDS if (n := sum(r["档"] == k for r in rows)))


def overseas(folder: Path) -> dict:
    """开盘预估 20:53 记下的外盘涨跌（预估.csv），网页上对照看；{组合: 文字}。"""
    df = rv.read_csv(folder / "预估.csv")
    if df is None:
        return {}
    return {r["组合"]: f"{rv.fmt(r['外盘涨跌%'], 2, plus=True)}% {r['外盘方向']}·{r['分档']}"
            for _, r in df.iterrows() if num(r.get("外盘涨跌%")) is not None}


def history_table() -> str:
    """过去判断过的（开盘复盘.csv 的 59分判断 对 针真假）：判成什么、实际开成什么。"""
    rec = rv.read_csv(rv.REC_FILE)
    if rec is None or "59分判断" not in rec:
        return "<p class='note'>还没有对过答案的开盘（开盘复盘.py 下次跑完就有）。</p>"
    s = rec[rec["59分判断"].isin(["真", "半真", "假"]) & rec["针真假"].isin(["真", "半真", "假", "反向"])]
    if not len(s):
        return "<p class='note'>还没有对过答案的开盘。</p>"
    rows = []
    for src in ("实盘", "回放"):
        for k in ("真", "半真", "假"):
            g = s[(s["判断来源"] == src) & (s["59分判断"] == k)]
            if len(g):
                c = {x: int((g["针真假"] == x).sum()) for x in ("真", "半真", "假", "反向")}
                inside = g["首笔在区间里"].isin(["是", "否"])
                rows.append([src, f"{SYMBOL[k]} {k}", len(g), c["真"], c["半真"], c["假"], c["反向"],
                             f"{(g['首笔在区间里'] == '是').sum()} / {inside.sum()}", rv.small(g["开盘"].nunique())])
    return rv.table(["来源", "判成", "根数", "实际真", "半真", "假", "反向", "首笔在区间里", "样本"], rows)


def build_page(rows: list, o: datetime, info: dict) -> str:
    ov = info.get("外盘", {})
    css = (".k{font-weight:700;font-size:16px}.k真{color:#2eaa50}.k半真{color:#f5a623}.k假{color:#eb4646}"
           ".k没针,.k不判断{color:var(--muted)}.why{display:block;white-space:normal;text-align:left;min-width:300px;max-width:560px}")
    body = [f"<style>{css}</style><h1>竞价判断 {o:%Y-%m-%d %H:%M}（{rv.session_of(o)}{'，回放' if info['回放'] else ''}）</h1>",
            f"<p class='meta'>{rv.esc(info['说明'])}。{counts(rows)}</p>",
            "<p class='note'>✅ 大概率整根开到针附近（真）；▲ 大概率开不到整根、但离昨收有段距离（半真）；❌ 大概率假；○ 没针；？ 不判断。"
            f"{'、'.join(MIX)} 用混合版（2026-10-24 从两年回放定的），其他品种用 v1（2026-10-23 从 4 次开盘、15 根针看出来的）；"
            "都只是线索，每次开盘后在开盘复盘的规则五里对答案。</p>"]
    order = {k: i for i, k in enumerate(("真", "半真", "假", "不判断", "没针"))}
    tab = []
    for r in sorted(rows, key=lambda r: order[r["档"]]):
        t = r["一档"]
        v1 = r.get("v1档")
        sym = r["符号"] + (f"<br><span class='note'>v1 {SYMBOL[v1]}</span>" if v1 and v1 != r["档"] else "")
        tab.append([rv.Raw(f"<span class='k k{r['档']}'>{sym}</span>"), r["组合"],
                    f"{price(r.get('首笔下沿'), t)} ~ {price(r.get('首笔上沿'), t)}" if r.get("首笔下沿") is not None else "",
                    price(r.get("猜首笔"), t / 10), price(r.get("昨收价差"), t), price(r.get("针"), t),
                    f"{rv.fmt(r.get('针偏几档'), 1, plus=True)}（门槛 {r['门槛']}）" if r.get("门槛") else rv.fmt(r.get("针偏几档"), 1, plus=True),
                    rv.fmt(r.get("冷门腿竞价占持仓‰"), 2), rv.fmt(r.get("冷门腿竞价持仓变化"), 0, plus=True),
                    price(r.get("立刻能卖"), t), price(r.get("立刻能买"), t),
                    f"{r['托住几成'] * 100:.0f}%" if r.get("托住几成") is not None else "",
                    rv.fmt(r.get("立刻能成交几倍门槛"), 1), rv.fmt(r.get("合计"), 0, plus=True),
                    f"{rv.fmt(r['腿1跳空档'], 1, plus=True)} / {rv.fmt(r['腿2跳空档'], 1, plus=True)}" if r.get("腿1跳空档") is not None else "",
                    (r["重仓标记"] + ("" if tags_of(r) else "（浮层不贴，只记录）")) if r.get("重仓标记") else "",
                    ov.get(r["组合"], ""), rv.Raw(f"<span class='why'>{rv.esc(r.get('理由', ''))}</span>")])
    body.append(rv.table(["", "组合", "首笔区间", "猜首笔", "昨收价差", "针", "针偏几档", "冷门腿竞价占持仓‰", "竞价后持仓变化",
                          "立刻能卖", "立刻能买", "托住针", "立刻能成交几倍门槛", "合计", "跳空档 腿1 / 主力", "重仓标记", "外盘", "理由"], tab))
    mixed = "、".join(f"{p} 系数 {k}" for p, k in MIX.items())
    fits = list(dict.fromkeys(r["区间依据"] for r in rows if r.get("区间依据") and r.get("保留比例依据") is None))
    olds = list(dict.fromkeys(r["保留比例依据"] for r in rows if r.get("保留比例依据")))
    body.append("<h2>怎么判断的</h2><p class='note'>先挑 ❌：竞价量分 + 盘口分，合计 ≤ −1 就是 ❌。"
                f"竞价量分：冷门腿（开盘前持仓少的那条腿）竞价手数 ÷ 开盘前持仓，≥ {RATIO_HI}‰ 得 +1，< {RATIO_LO}‰ 得 −1；"
                "竞价后持仓减少超过竞价手数一半（竞价里平仓为主），竞价量再大也算 −1。"
                "盘口分：针往上看「立刻能卖」的价差（腿1买一 − 腿2卖一），针往下看「立刻能买」（腿1卖一 − 腿2买一），"
                f"它离昨收价差的距离是针的几成：≥ {HOLD_HI * 100:.0f}% 得 +1，{HOLD_LO * 100:.0f}%–{HOLD_HI * 100:.0f}% 得 0，"
                f"< {HOLD_LO * 100:.0f}% 得 −1。盘口缺一边（多半是涨跌停锁住）时，光凭竞价量分不判 ❌；"
                f"立刻能成交那边偏出门槛 {HEAVY:g} 倍以上也不判 ❌（sc、nr、lu 两年回放里这样被判 ❌ 的 5 根全开到了门槛外）。</p>"
                f"<p class='note'>剩下的：混合版（{mixed}）猜首笔 = 昨收价差 + 系数 ×（立刻能成交那边 − 昨收价差），"
                "按针真假的定义分：离针 1 档以内或更远 → ✅，偏出门槛 → ▲，在门槛以内或往反方向偏 → ❌；"
                f"差门槛 {'、'.join(f'{p} {k} 档' for p, k in SLACK.items())}以内的也算 ▲，不判 ❌（真针判成 ❌ 最怕）。"
                "v1（其他品种）：针不到门槛的 "
                f"{BIG} 倍 → ✅，否则 ▲。混合版的格子上，v1 判得不一样时符号下面小字写着 v1 的。</p>"
                "<p class='note'>首笔区间："
                + (f"{'、'.join(FIT)} 用{rv.esc('；'.join(fits))}。" if fits else "")
                + ("其他品种：竞价后盘口立刻能成交的价差（两头各放 1 档），和「昨收价差 + 针变化 × 过去首笔保留针几成的八成范围」"
                   f"合在一起，这次的保留比例：{rv.esc('；'.join(olds))}（每组用的见 csv）。" if olds else "")
                + "</p>"
                f"<p class='note'>重仓标记（2026-10-24 晚，sc、nr、lu 两年回放）：立刻能成交那边偏出门槛 {HEAVY:g} 倍以上记「重X.X」（几倍门槛，"
                "3 倍以上更强）；主力（腿2）竞价跳空比腿1 小，加「腿2秒进」（腿2 晚两秒才进多半亏，10.8 那种）；"
                f"冷门腿竞价不到持仓 {RATIO_LO}‰，加「量少」（开盘第一个半秒能成交的手数只有其他大机会的一半到三分之二）。"
                f"浮层上只在 {'、'.join(HEAVY_SHOW)} 的跨月格子贴，别的品种只记在这里和 csv。都只是线索，不是买卖建议。</p>")
    body.append("<h2>过去判得准不准</h2>" + history_table())
    return rv.page(f"竞价判断 {o:%m-%d %H:%M}", "".join(body))


def write_outputs(rows: list, o: datetime, folder: Path, page: Path, info: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).reindex(columns=CSV_COLS).to_csv(folder / CSV_NAME, index=False, encoding="utf-8-sig")
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(build_page(rows, o, info), encoding="utf-8")


def start_overlay(jf: Path):
    """浮层单独一个进程（pythonw，不弹黑窗口）；它看 json 的「显示到」自己关。"""
    exe = Path(sys.executable).with_name("pythonw.exe")
    exe = exe if exe.exists() else Path(sys.executable)
    return subprocess.Popen([str(exe), str(HERE / "竞价浮层.py"), "--json", str(jf)], cwd=HERE,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def wait_overlay(proc) -> None:
    if proc is None:
        return
    try:
        proc.wait(timeout=fl.LIFE + 30)
    except subprocess.TimeoutExpired:
        print("浮层到时间还没关（它自己最多开 10 分钟）")


def board_and_list() -> list:
    """要判断的组合：看板上的（按格子顺序）+ 合约.txt 里的，去重。"""
    pairs = [tuple(p) for p in fl.board_pairs(fl.read_board())]
    pairs += [tuple(x.split(",")) for x in tick.read_list() if "," in x]
    return list(dict.fromkeys(pairs))


# ---------- 回放 ----------

def replay_pair(folder: Path, o: datetime, s1: str, s2: str, closes: dict, past) -> dict:
    """开盘复盘用：开盘那天没有实盘判断（竞价判断.csv）时，用 tick 回放算一组。"""
    snaps = {s: snap_from_ticks(rv.read_ticks(folder / f"{s}.csv"), o) for s in (s1, s2)}
    cl = {s: closes.get(s, {}).get("收盘价") for s in (s1, s2)}
    tk = {s: closes.get(s, {}).get("最小变动价位") for s in (s1, s2)}
    return evaluate(o, [(s1, s2)], snaps, cl, tk, past, "回放")[0]


def read_live(folder: Path) -> dict:
    """开盘复盘用：实盘那天 20:59 写的 竞价判断.csv → {组合: 一行}。"""
    df = rv.read_csv(folder / CSV_NAME)
    if df is None or "来源" not in df:
        return {}
    return {r["组合"]: r.to_dict() for _, r in df.iterrows() if r["来源"] == "实盘"}


def review_cols(j: dict, first) -> dict:
    """开盘复盘.csv 里加的几列：59分判断、判断得分、托住几成、预计首笔区间、首笔在区间里、判断来源、
    v1判断（混合版之前的实盘没有这一列，就是 59分判断）、立刻能卖、立刻能买（以后拟合首笔区间用）；首笔上下沿不写进表格。"""
    lo, hi = num(j.get("首笔下沿")), num(j.get("首笔上沿"))
    v1 = j.get("v1档")
    out = {"59分判断": j.get("档", ""), "判断得分": num(j.get("合计")), "托住几成": num(j.get("托住几成")),
           "判断来源": j.get("来源", ""), "首笔下沿": lo, "首笔上沿": hi,
           "v1判断": v1 if isinstance(v1, str) and v1 else j.get("档", ""),
           "立刻能卖": num(j.get("立刻能卖")), "立刻能买": num(j.get("立刻能买"))}
    if lo is not None and hi is not None:
        out["预计首笔区间"] = f"{rv.fmt(lo, 4)} ~ {rv.fmt(hi, 4)}"
        f = num(first)
        if f is not None:
            out["首笔在区间里"] = "是" if lo - 1e-9 <= f <= hi + 1e-9 else "否"
    return out


def replay(name: str, overlay: bool, hold: float) -> None:
    folder = rv.DATA / name
    o = datetime.strptime(name, "%Y-%m-%d_%H%M")
    files = sorted(folder.glob("价差_*.csv"))
    if not files:
        print(f"{folder} 里没有 tick（价差_*.csv），回放不了")
        return
    have = [tuple(f.stem[len("价差_"):].split("-")) for f in files]
    board = [p for p in board_and_list() if p in have]
    pairs = board + [p for p in have if p not in board]
    closes = rv.load_closes(folder, rv.read_csv(folder / "预估.csv"))
    symbols = list(dict.fromkeys(s for pr in pairs for s in pr))
    snaps = {s: snap_from_ticks(rv.read_ticks(folder / f"{s}.csv"), o) for s in symbols}
    cl = {s: closes.get(s, {}).get("收盘价") for s in symbols}
    tk = {s: closes.get(s, {}).get("最小变动价位") for s in symbols}
    rows = evaluate(o, pairs, snaps, cl, tk, rv.read_csv(rv.REC_FILE), "回放")
    out = REPLAY_DIR / name
    page = out / "竞价判断.html"
    write_outputs(rows, o, out, page, {"回放": True, "外盘": overseas(folder),
                                       "说明": f"回放：用 数据/{name}/ 里存的 tick 算的，{datetime.now():%Y-%m-%d %H:%M} 算"})
    for r in rows:
        print(f"{r['符号']} {r['文字']}　｜ {r.get('理由', '')}")
    print(f"{counts(rows)}；写好 {page}")
    jf = out / JSON_NAME  # 不贴标签也写（竞价浮层.py --mock 用它画示意图）
    until = datetime.now() + timedelta(seconds=hold)
    state = {"开盘": f"{o:%Y-%m-%d %H:%M}", "显示到": f"{until:%Y-%m-%d %H:%M:%S}"}
    put(jf, result_json(rows, state, f"竞价判断 回放 {o:%m-%d %H:%M}（{hold:.0f} 秒后消失）", page))
    if overlay:
        print(f"标签贴在博易云上，{hold:.0f} 秒后消失")
        wait_overlay(start_overlay(jf))


# ---------- 实盘 ----------

def clock_offset() -> tuple[float | None, str]:
    """这台电脑的时钟比标准时间慢几秒（正数 = 慢；交易所时间 ≈ 电脑时间 + 它）。问网上的时间服务器（NTP），
    一个问不到换下一个，都问不到返回 (None, "")。只是问一下，不改电脑的时钟。"""
    import socket
    import struct
    for host in NTP:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(1.5)
                t0 = time.time()
                s.sendto(b"\x1b" + bytes(47), (host, 123))
                data = s.recv(48)
                t1 = time.time()
            sec, frac = struct.unpack("!II", data[40:48])
            off = sec - 2208988800 + frac / 2 ** 32 - (t0 + t1) / 2
            # 服务器自己没对准（第 0 层、第 16 层以上、「没同步」标记）或差出一小时以上：不信，换下一个
            if 1 <= data[1] <= 15 and data[0] >> 6 != 3 and sec and abs(off) < 3600:
                return off, host
        except (OSError, struct.error, IndexError):
            continue
    return None, ""


def clock_note(skew: float) -> str:
    return f"电脑时钟{'慢' if skew > 0 else '快'} {abs(skew):.0f} 秒，记得同步" if abs(skew) >= CLOCK_WARN else ""


def connect_until(o: datetime, jf: Path, state: dict, clock):
    """连天勤：连不上每 5 秒重试一次，到 HH:59:30 为止（开盘tick.connect 每分钟才重试一次，这里等不起）。clock() = 标准时间的现在。"""
    stop, n = o - timedelta(seconds=30), 0
    while True:
        n += 1
        try:
            return tick.TqApi(auth=tick.load_auth(), disable_print=True)
        except Exception as e:
            print(f"第 {n} 次连天勤失败：{type(e).__name__} {e}")
            put(jf, state | {"状态": "等待", "红": True, "提示": f"竞价判断：连不上天勤，重试中（{clock():%H:%M:%S}）"})
            if clock() + timedelta(seconds=5) >= stop:
                raise
            time.sleep(5)


def live(o: datetime, pairs: list, jf: Path, state: dict, page: Path, skew: float = 0.0) -> tuple[list, dict] | None:
    """连天勤、记下竞价前的状态和昨收、等竞价快照、算，算好马上交给浮层（断开天勤之前）。
    skew = 电脑时钟慢几秒：时间都按标准时间（≈ 交易所时间）算、记。返回 (每组一行, 时间记录)；这次没开盘返回 None。"""
    def clock():
        return datetime.now() + timedelta(seconds=skew)

    note = clock_note(skew)
    api = connect_until(o, jf, state, clock)
    log = {"连上": clock()}
    try:
        cal = api.get_trading_calendar(start_dt=(o - timedelta(days=20)).date(), end_dt=(o + timedelta(days=20)).date())
        days = {d.date() for d in cal[cal["trading"]]["date"]}
        if not pre.has_opening(o, days):
            print("交易日历显示这次没有开盘（周末、节假日或节前一晚）")
            return None
        # 日盘开盘：上一个交易日晚上有没有夜盘（有的话成交量接着夜盘累计；节后第一天没有夜盘，从 0 算起）
        prev = max((d for d in days if d < o.date()), default=None)
        carry = o.hour < 12 and prev is not None and pre.has_opening(datetime.combine(prev, datetime.min.time()).replace(hour=21), days)
        symbols = list(dict.fromkeys(s for pr in pairs for s in pr))
        quotes, missing = {}, {}
        try:
            quotes = dict(zip(symbols, api.get_quote_list(symbols)))
        except Exception:
            for s in symbols:
                try:
                    quotes[s] = api.get_quote(s)
                except Exception as e:
                    missing[s] = "订阅不了"
                    print(f"{s} 订阅不了：{type(e).__name__} {e}")
        for s, q in list(quotes.items()):
            if q.expired:
                missing[s] = "已到期"
                del quotes[s]
        if not quotes:
            raise RuntimeError("一个合约都没订阅上：" + "、".join(f"{s} {w}" for s, w in missing.items()))
        before, late = {}, []
        for s, q in quotes.items():
            t = when(q.datetime)
            if t is not None and t >= o - AUCTION:
                late.append(s)  # 连上时竞价快照已经到了
                try:
                    b = before_from_ticks(api, s, o)
                except Exception as e:
                    b = None
                    print(f"{s} 没取到竞价前的 tick：{type(e).__name__} {e}")
                if b:
                    before[s] = b
            else:
                before[s] = before_of(q)
        log["记下竞价前"] = clock()
        # 昨收、过去的开盘复盘在竞价前就准备好：有的合约昨收要联网取 1 分钟线（10-23 晚放在竞价后取，出结果晚了约 0.9 秒）
        closes, src = {}, {}
        for s in before:
            closes[s], src[s] = close_of(api, s, before[s], o)
        past = rv.read_csv(rv.REC_FILE)
        for p in sorted({rv.product_of(s) for s in symbols} & set(FIT)):  # sc 首笔区间的系数也先算好（要 import scipy，约 1 秒）
            range_fit(past, o, p)
        log["取好昨收"] = clock()
        print(f"连上天勤 {log['连上']:%H:%M:%S}，订阅 {len(quotes)} 个合约" + (f"（{len(late)} 个连上时竞价已经出来了）" if late else "")
              + f"，{log['取好昨收']:%H:%M:%S} 取好昨收"
              + (f"；{'、'.join(f'{s} {w}' for s, w in missing.items())}" if missing else ""))
        put(jf, state | {"状态": "等待", "红": False, "提示": f"竞价判断：等 {o - AUCTION:%H:%M}" + (f"（{note}）" if note else "")})
        got, first, end = {}, None, o - GIVE_UP
        while True:
            api.wait_update(deadline=time.time() + 0.5)
            now = clock()
            for s, q in quotes.items():
                if s not in got:
                    t = when(q.datetime)
                    if t is not None and o - AUCTION <= t < o:
                        got[s] = now
            if got and first is None:
                first = time.time()
            if len(got) == len(quotes) or (first and time.time() - first >= SETTLE) or now >= end:
                break
        if not got:
            print(f"到 {end:%H:%M:%S} 还没收到竞价快照，当这次没开盘")
            return None
        log["快照到"] = (min(got.values()), max(got.values()))
        lag = sorted((got[s] - when(quotes[s].datetime)).total_seconds() for s in got if when(quotes[s].datetime))
        if lag:
            log["晚到"] = lag[len(lag) // 2]  # 按标准时间，快照比它自己标的时间晚到几秒（中位数；时间戳以半秒为单位，可能是负的）
        for s in quotes:
            if s not in got:
                missing[s] = "没收到竞价快照"
            elif s not in before:
                missing[s] = "没取到竞价前的状态"
        snaps = {s: snap_from_quote(quotes[s], before[s], o, carry) for s in got if s in before}
        ticks = {s: num(q.price_tick) for s, q in quotes.items()}
        rows = evaluate(o, pairs, snaps, closes, ticks, past, "实盘", missing)
        log["算好"] = clock()
        for r in rows:
            r["算好时间"] = f"{log['算好']:%H:%M:%S.%f}"[:-3]
        put(jf, result_json(rows, state, f"竞价判断 {log['算好']:%H:%M:%S}（{o + timedelta(seconds=SHOW):%H:%M} 消失）"
                            + (f"；{note}" if note else ""), page))
        log["交给浮层"] = clock()
        other = sorted({v for v in src.values()} - {"收盘快照"})
        if other:
            log["昨收"] = f"有的合约昨收用的{'、'.join(other)}"
        return rows, log
    finally:
        t = time.time()
        api.close()
        log["断开"] = clock()
        log["断开用时"] = time.time() - t


def run_live(force: bool, overlay: bool) -> None:
    off, host = clock_offset()
    skew = off or 0.0
    now = datetime.now() + timedelta(seconds=skew)  # 按标准时间（电脑时钟不准也不影响）
    o = pre.next_opening(now)
    print(f"\n===== {now:%Y-%m-%d %H:%M:%S} 竞价判断：{o:%m-%d %H:%M} 开盘 =====")
    print(f"电脑时钟比标准时间{'慢' if skew > 0 else '快'} {abs(skew):.1f} 秒（问的 {host}），下面的时间都是标准时间"
          if off is not None else "没问到标准时间（NTP），按电脑时钟算")
    if not force and not (LATEST <= o - now <= EARLIEST):
        print("不在开盘前 1.5–10 分钟里，不跑（要跑加 --force）")
        return
    if o.weekday() >= 5:  # 周六、周日没有开盘（周五晚上的夜盘算下周一的，那天是星期五）
        print("周末，没有开盘")
        return
    name = f"{o:%Y-%m-%d_%H%M}"
    folder, page = rv.DATA / name, PAGE_DIR / f"{name}.html"
    folder.mkdir(parents=True, exist_ok=True)
    jf = folder / JSON_NAME
    # 浮层按电脑时钟看「显示到」，所以减掉时钟差；「时差」给浮层算开盘那几秒（那几秒不截屏）
    state = {"开盘": f"{o:%Y-%m-%d %H:%M}", "时差": round(skew, 2),
             "显示到": f"{o + timedelta(seconds=SHOW - skew):%Y-%m-%d %H:%M:%S}"}
    note = clock_note(skew)
    put(jf, state | {"状态": "等待", "红": False, "提示": f"竞价判断：等 {o - AUCTION:%H:%M}（正在连天勤{'；' + note if note else ''}）"})
    proc = start_overlay(jf) if overlay else None
    try:
        res = live(o, board_and_list(), jf, state, page, skew)
        if res is None:
            put(jf, state | {"状态": "没开盘"})
        else:
            rows, log = res
            a, b = log["快照到"]
            shown = log["交给浮层"]
            write_outputs(rows, o, folder, page, {"回放": False, "外盘": overseas(folder),
                                                  "说明": f"实盘：竞价快照 {a:%H:%M:%S.%f}"[:-3] + f" 到，{log['算好']:%H:%M:%S} 算好"
                                                          + (f"；{log['昨收']}" if "昨收" in log else "")})
            print(f"{counts(rows)}；快照 {a:%H:%M:%S.%f}"[:-3] + f"–{b:%H:%M:%S.%f}"[:-3]
                  + f" 到，{log['算好']:%H:%M:%S.%f}"[:-3] + f" 算好，{shown:%H:%M:%S.%f}"[:-3] + " 交给浮层，"
                  + f"{log['断开']:%H:%M:%S} 断开天勤（用了 {log['断开用时']:.1f} 秒）；网页 {page}"
                  + (f"；快照到达 − 快照上的时间 = {log['晚到']:+.2f} 秒" if "晚到" in log else "")
                  + (f"；{log['昨收']}" if "昨收" in log else ""))
            for r in rows:
                print(f"  {r['符号']} {r['文字']}")
    except Exception as e:
        import traceback
        print(f"竞价判断出错：{type(e).__name__} {e}\n{traceback.format_exc()}")
        put(jf, state | {"状态": "出错", "红": True, "提示": f"竞价判断出错（{type(e).__name__}），这次没有判断，看运行日志"})
    wait_overlay(proc)
    print(f"===== {datetime.now() + timedelta(seconds=skew):%H:%M:%S} 竞价判断结束 =====")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--replay", help="回放：数据/ 里的开盘文件夹名，如 2026-10-21_2100")
    p.add_argument("--force", action="store_true", help="不管离开盘多久都跑")
    p.add_argument("--no-overlay", action="store_true", help="不贴标签，只写文件")
    p.add_argument("--hold", type=float, default=60, help="回放时标签留几秒")
    a = p.parse_args()
    if a.replay:
        replay(a.replay, not a.no_overlay, a.hold)
    else:
        run_live(a.force, not a.no_overlay)


if __name__ == "__main__":
    main()
