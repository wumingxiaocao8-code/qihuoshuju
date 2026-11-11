"""竞价判断：20:59 / 8:59 集合竞价一出结果，判断每组价差的针会怎么开，标签贴在博易云的每个格子上，再写一份网页。

平时不用手动跑：Windows 任务计划「竞价判断」每天 20:57、8:57 自动运行（电脑那时没开就不补跑，过了开盘没意义）。
全是脚本算的，不调用 AI。规则是 2026-10-23 从 4 次开盘、15 根偏出门槛的针里看出来的（v1，试运行），常量都在下面，攒多了再调。

符号（针真假是小草定的，门槛见 开盘复盘.py 的 NEEDLE_GATE）：
  ✅ 大概率整根开到针附近（真：首笔离针 1 档以内，或比针走得还远）
  ▲ 大概率开不到整根、但离昨收有段距离（半真：首笔偏出门槛、比针小，照样能开到）
  ❌ 大概率假（首笔在门槛以内，几乎没动）
  ○ 没针（针本身没偏出门槛）  ？ 不判断（有一腿竞价没成交、没收到快照，或还没定门槛的跨品种组合；11-10 起有腿进了交割月的也是，见 DELIVERY_SKIP）
两步：
  1. 先挑 ❌：竞价量分 + 盘口分，合计 ≤ −1 就是 ❌（盘口缺一边时不判 ❌：多半是涨跌停锁住，sc 两年里两腿都锁住的 3 根针都整根开到；
     立刻能成交那边偏出门槛 2 倍以上时也不判 ❌：sc、nr、lu 两年里这样被判 ❌ 的 5 根全开到了门槛外，见 HEAVY）。
     竞价量分：冷门腿（开盘前持仓少的那条腿）竞价手数 ÷ 开盘前持仓，≥ 1.5‰ 得 +1，< 0.7‰ 得 −1；
               竞价后持仓减少超过竞价手数一半（竞价里平仓为主），竞价量再大也算 −1。
     盘口分：竞价后的挂单还托不托得住针。针往上看「立刻能卖」的价差（腿1买一 − 腿2卖一），针往下看「立刻能买」（腿1卖一 − 腿2买一），
             它离昨收价差的距离是针的几成：≥ 75% 得 +1，40%–75% 得 0，< 40% 得 −1；盘口缺一边得 0。
  2. 剩下的：针不到门槛的 1.5 倍 → ✅，否则 ▲。
     混合版（2026-10-24 用 sc 两年 L2 回放定的，同一晚推到所有定了门槛的品种，见 MIX）：第 1 步照旧；剩下的猜首笔 = 昨收价差 + 0.9 ×（立刻能成交那边 − 昨收价差），
     按针真假的定义分成 ✅ ▲ ❌（猜的首笔往反方向偏出门槛也算 ❌）。两年 153 根针：三档说对 59% → 71%，开不开得到 66% → 76%。
     10-24 晚放宽（SLACK）：猜的首笔差门槛 5 档以内（sc；别的品种按门槛的三分之一）也算 ▲、不判 ❌（他定的：真针判成 ❌ 最怕，假针判成 ▲ 无所谓）。
     nr、lu 两年回放（放宽 2 档）：三档说对 57% → 64%、51% → 70%，能不能开到 62% → 71%、59% → 78%，真针判成 ❌ 14 → 15、2 → 2。
     fu、bu（10-27）、有色七个和 ao br sp rb hc ru ss（10-31）后来用上期所两年 L2 原样重判过：「开不开得到」说对 61–77%，都比瞎猜好；
     但骨架（打分线、k 0.9、放宽三分之一、2 倍不判 ❌）是照 sc 定的搬过去，只按品种调了门槛、RELAX、首笔半，没有按品种重新设计。
     两年 153 根：真针判成 ❌ 7 → 3 根，判 ❌ 的里面真开不到的 74% → 83%；代价是假针少认出 16 根，三档说对 72%（修好压线那根后）→ 66%。
     v1 会判成什么另记一列（v1档），开盘复盘的规则五两个对照着看。
首笔区间（第一次真实成交大概在哪），按一档取整（下沿往下、上沿往上）：
  sc、nr 和攒够 60 次开盘的品种（FIT）：最近 150 次开盘（两年回放 + 实盘）的八成范围。顺着针的方向数，离昨收价差几档：下沿 = a + b × 立刻能成交那边偏几档 + c × 针偏几档，
             上沿同样一条，用分位回归（P10、P90）每次开盘前重算。两年回放的考试段：包住约八成（原来九成），针偏出门槛的宽度中位 29 → 23 档。
  fu、bu（10-27 晚他定的）：同一个办法，但用九成范围（P5、P95），两年回放种子已放好。fu、bu 两年回放：针出门槛的包住 83–89%、
             宽度中位 22–26 档（老办法 83–93%、23–31 档），首笔没走到区间的 7–14% → 5–9%（分析/2026-10-27/fu和bu回测/小结.md 第六节）。
  还没攒够的品种：「竞价后盘口立刻能成交的价差，两头各放 1 档」和「昨收价差 + 针变化 × 保留比例的八成范围」合在一起。
  保留比例 = 首笔偏几档 ÷ 针偏几档，用开盘复盘.csv 里这次以前、同品种组合、针偏 3 档以上的；
  判成同一种符号的攒够 8 根就只用这种的，否则用全部（够 10 根），再不够用 17%–131%（2026-10-23 从 4 次开盘 30 根针算的）。
重仓标记（2026-10-24 晚，sc、nr、lu 两年 L2 回放：分析/2026-10-24/大波动指标/小结.md 第 7 节），csv 的「重仓标记」：
  立刻能成交那边偏出门槛 2 倍以上 = 大机会，记「重3.6」（几倍门槛）；再看两件事：
  主力（腿2）竞价跳空比腿1 小 → 加「腿2秒进」（腿2 晚两秒才进多半亏，10.8 那种）；
  冷门腿竞价不到持仓 0.7‰ → 加「量少」（开盘第一个半秒能成交的手数只有其他大机会的一半到三分之二）。
  「腿2秒进」11-06 起不跟着重仓标记走：有针、主力动得少就在浮层上贴（LEG2_ALWAYS），csv 的「重仓标记」照旧只在大机会时写。
  浮层上只在 HEAVY_SHOW 的品种（sc、nr，10-27 晚起加 fu、bu，10-31 起加有色 ag cu al zn pb sn ni 和 ao br sp rb hc ru ss，11-05 晚加跨品种 nr-ru）的格子上贴，别的品种只记进 csv 和网页。
跨品种 nr-ru（11-05 晚）：门槛 8 档，混合版、差门槛 1/3（3 档）不判 ❌、≥ 2 倍不判 ❌ 照同品种那套，不放宽；首笔区间用两年 L2 种子、八成范围（后一年包住 84%）。
  见 测试/2026-11-05_nr-ru门槛/小结.md。别的跨品种组合没定门槛，照旧「？」。
  有色（10-31，上期所两年回放：分析/2026-10-31/有色开盘/小结.md 第 5、9 节）：七个品种都是「重 ≥ 3 倍」最赚，不重扣完费只剩 1–2.5 档。
  cu 手续费一组约 3.3 档（开 27、平今 54 元/手），不重的每手扣费后约 1 档、腿2 晚半秒就亏 → FEE_SKIP：cu 判 ✅▲ 但不重时浮层贴灰框「不重」。
第 1 步放宽（10-31，RELAX：ag、al、zn、pb）：只有「竞价量分 −1、盘口分 0」的不判 ❌，交给第 2 步猜首笔。有色冷门腿竞价量本来就少
  （针出门槛时冷门腿竞价占持仓中位 0.36–0.84‰，54–72% 的针都扣这 1 分），盘口分 0 时量分 −1 / 0 / +1 开得到的比例差不多（cu 52 / 51 / 59%），
  这时量分分不出真假，却把一半能开到的针判成 ❌。两年回放：真针判 ❌ 少一半左右，改判的那些扣费后还略赚。cu 不放：救回来的针每手 3.1 档，被 3.3 档手续费吃掉。
  10-31 加 br、rb、hc、ao（上期所两年回放：分析/2026-10-31/其余品种回放/小结.md 第 4 节）：真针判 ❌ 少三到四成，「说对」不降，改判的扣费后还赚；
  同时 ao 门槛 5 → 8（开盘复盘.NEEDLE_GATE）。sp、ss、ru 不放：放宽后改判回来的大多是假针，「说对」反而降。
  这七个品种也都是「重 ≥ 3 倍」最赚（每手扣费后 12–19 档，不重 1–4.5 档），首笔区间用两年种子、九成范围（FIT_Q_BY）。
薄大（2026-10-30，sc 两年 L2 回放：分析/2026-10-26/腿2控价/小结.md 第 12、14 节），csv 的「薄大」：
  腿2（主力）要吃那边（针往上 = 价差空单 = 买腿2 → 卖盘；针往下 → 买盘）第二档离第一档 ≥ 5 档（他定的「控价大」），
  而且那边五档量不到平时（同场次前 40 次开盘、两边五档量平均的中位数；sc 两年回放种子 + 实盘）→ 判 ✅▲ 时记「薄大」。
  sc 两年 17 次，腿2 同时打 4 手约打平、腿2 晚半秒 −8 万，跟「重」一起出现时也是亏的，所以浮层上「薄大」顶替「重」那一串。
  只在 BOOK_SHOW 的品种（sc）判：nr、fu、bu、lu 回放里薄大不亏（bu 反而最赚），不加。所有格子的台阶、五档量、平时都记进 csv 攒着。

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
# ---- 混合版（2026-10-24，sc 两年 L2 回放：分析/2026-10-24/sc两年tick回放/小结.md；10-24 晚他定的：所有定了门槛的品种都换）----
# 照 sc 定、sc nr lu 先验（nr、lu 见 分析/2026-10-24/大波动指标/现规则对照输出.txt）；fu bu、有色、ao br sp rb hc ru ss 后来用上期所两年 L2 原样重判过（见文件开头），骨架没按品种重新设计
MIX = {p: 0.9 for p in rv.NEEDLE_GATE}  # 第 2 步改成猜首笔 = 昨收价差 + 0.9 ×（立刻能成交那边 − 昨收价差）；sc 0.7–0.95 成绩差不多
# 混合版猜的首笔差门槛几档以内也算 ▲、不判 ❌（他定的：真针判成 ❌ 最怕，假针判成 ▲ 无所谓）：sc 5 档，别的品种按门槛的三分之一
SLACK = {p: round(g / 3) for p, g in rv.NEEDLE_GATE.items()}
NEAR = 0.2  # csv 和网页的「近」= 昨收价差 + NEAR ×（猜首笔 − 昨收价差），只作对照（11-05 晚到 11-09 浮层上也写「近」；11-09 他定的：浮层只留「首笔估」= 首笔半）
HALF = 0.5  # 标签上的「首笔半」= 昨收价差 + HALF ×（猜首笔 − 昨收价差）
HALF_BY = {"ag": 1 / 3, "sn": 1 / 3, "ni": 1 / 3}  # 按品种另设（10-31 他定的）：ag、sn、ni 挂三分之一，两年回放比挂一半多赚 4–12%（腿2 晚半秒），判断不变
# 远档（11-02 夜他定的，全部 18 个品种）：每组挂两张单，首笔半 n1 手 + 远档 n2 手；远档 = 昨收价差 + 系数 ×（猜首笔 − 昨收价差）。
# 品种：(远档系数, 首笔半手数, 远档手数)；两年回放、新止盈止损下进单率不少、四种口径都比只挂首笔半多赚（分析/2026-11-02/挂单远近与波动/各品种挂法_新止盈.md）。
# 没列的品种（lu 等，没回放过）只挂首笔半。
FAR = {"sc": (0.55, 5, 5), "nr": (0.6, 7, 3), "fu": (0.55, 5, 5), "bu": (0.55, 5, 5), "ag": (0.5, 7, 3),
       "cu": (0.65, 5, 5), "al": (0.6, 5, 5), "zn": (0.6, 5, 5), "pb": (0.6, 5, 5), "sn": (0.5, 5, 5), "ni": (0.5, 5, 5),
       "ao": (0.55, 5, 5), "br": (0.6, 5, 5), "sp": (0.65, 5, 5), "rb": (0.6, 5, 5), "hc": (0.6, 3, 7), "ru": (0.6, 5, 5),
       "ss": (0.6, 5, 5)}
FIT = tuple(rv.NEEDLE_GATE)  # 首笔区间用最近 150 次开盘的分位回归（有两年回放种子的 sc、nr 马上用；别的品种攒够 60 次开盘前还用老办法）
FIT_N, FIT_MIN, FIT_Q = 150, 60, (0.1, 0.9)  # 最近 150 次开盘，不够 60 次用老办法；八成范围（P10–P90）
FIT_Q_BY = {p: (0.05, 0.95) for p in ("fu", "bu", "ao", "br", "sp", "rb", "hc", "ru", "ss")}  # 按品种另设九成：fu、bu 10-27 晚他定的（八成窄了但少包住，九成跟老办法包住得差不多、更窄）；ao br sp rb hc ru ss 10-31 同理（两年回放九成包住 90–96%）
SEED = rv.REVIEW / "两年回放_{}.csv"  # 两年回放切出来的开盘（只留本机），跟开盘复盘.csv 的实盘行一起用
# ---- 重仓标记（2026-10-24 晚，sc、nr、lu 两年 L2 回放：分析/2026-10-24/大波动指标/小结.md 第 7 节）----
HEAVY = 2.0  # 立刻能成交那边偏出门槛 2 倍以上 = 大机会：不判 ❌（两年 5 次被判 ❌ 的全开到了门槛外），记「重仓标记」
HEAVY_SHOW = ("sc", "nr", "fu", "bu", "ag", "cu", "al", "zn", "pb", "sn", "ni", "ao", "br", "sp", "rb", "hc", "ru", "ss", "nr-ru")  # 浮层只在这几个品种的格子上贴重仓标记（验过的：sc、nr 10-24，fu、bu 10-27，有色和 ao br sp rb hc ru ss 10-31 上期所两年回放，nr-ru 11-05 两年 L2：门槛 8 时重 22 次、每次约是不重的两倍），别的品种只记进 csv 和网页
FEE_SKIP = ("cu",)  # 手续费贵、不重的扣完费几乎白干的品种：判 ✅▲ 但不重时浮层贴灰框「不重」（10-31，cu 一组开平约 3.3 档）
RELAX = ("ag", "al", "zn", "pb", "br", "rb", "hc", "ao")  # 第 1 步只有「竞价量分 −1、盘口分 0」时不判 ❌，交给第 2 步（10-31 有色和 br rb hc ao 两年回放；cu、sp、ss、ru 不放，见文件开头）
TAG_STYLE = {"腿2秒进": "快", "量少": "少", "不重": "灰", "小针": "针", "换月": "换", "交割月": "灰"}  # 交割月（11-10）：灰框，贴在 ？ 后面  # 浮层按样式上色：「重…」紫底、快 红框、少 黄框、不重 灰框、小针 青框、换月 红框（跟腿2秒进一样）
# 换月（11-07 他定的，测试/2026-11-06_br专项/小结.md）：腿1 开盘前持仓 ÷ 腿2（主力）开盘前持仓 ≥ 这个数 = 持仓已经搬到后月、按成交量算的主力还没换过来，
# 判 ✅▲ 的针贴红框「换月」、色块关。br 近两年判 ✅▲ 正滑一半：持仓比 < 1 +0.1 / +2.7 档，≥ 1 −1.3 / −1.8 档（2026 前 / 后），21 个换月周期 15 个亏，重 ≥ 3 倍也亏
ROLL_OFF = {"br": 1.0}
# 换月分级（11-07 他定的，测试/2026-11-07_换月腿2/小结.md）：所有同品种跨月格子看三条，都是开盘前就知道的：
#   A 腿1 开盘前持仓 ≥ 腿2；B 腿1 上一交易日成交量 ≥ ROLL_VOL × 腿2；C 收盘回看主力缩量、次主力放量（各比前 ROLL_DAYS 个交易日均量少 / 多 ≥ ROLL_SHRINK）。
#   A、B 只占一条 →「换月」，色块降一级；A、B 都有 →「换月2」，色块关；A、B、C 都有 →「换月！」，色块关；只有 C 不贴。br 照旧一条就关（ROLL_OFF）。
#   两年 L2 18 品种判 ✅▲（程序版：挂首笔半、腿2 晚半秒、正滑一半，单位门槛几成）：0 条 −0.05、1 条 −0.26、2 条 −0.69、3 条 −0.86；换月时腿2 头半秒跑 ≥ 3 档 47%（平时 38%）。
#   成交量用 output/缓存/历史/kx_*.dat.gz（17:30 仓储取数顺带下的，主力表.py 同一口径：夜盘用当天、日盘用前一交易日）。False = 只有 br 看持仓（老样子）
ROLL_ALL = True
ROLL_VOL, ROLL_SHRINK, ROLL_DAYS = 0.75, 0.2, 5
# 小针（11-06 他定的，测试/2026-11-06_fu专项/小结.md）：针没出门槛，但竞价后立刻能成交那边顺着针偏 ≥ 这么多档 → 判 ▲、贴「小针」，色块「小」。
# fu 两年回放（正滑一半、挂首笔半）：早 5 年每次 +1.9 档、近两年 +4.1 档（9 个季度全赚），约一周一次；偏 ≤ 4 档的怎么算都亏
# 11-08 昨收换成收盘前 30 秒均价后 fu 改 10 档（测试/2026-11-08_fu新规则/小结.md：12 档是按最后一笔挑的，换昨收后掉了约两成，10 档补回来、前后两段都赚）
SMALL = {}  # 11-08 他定的：fu 小针也不要了，自己判断（原来 {"fu": 10}，规则代码留着，想要回来填回去）
LEG2_ALWAYS = True  # 11-06 他定的：「腿2秒进」不管针真假、重不重都贴（以前只跟着「重…」贴）；False = 回到老样子
# 腿2秒进！（11-07 他定的，测试/2026-11-07_腿2秒进档位/小结.md）：腿2秒进且主力（腿2）竞价自己跳了 ≥ 这么多倍门槛 → 浮层写「腿2秒进！」（短名「腿2！」）。
# 只改字，色块、手数照旧。两年回放这类针腿2 晚半秒平均少 8.2 档，别的腿2秒进只少 0.6 档；None = 不分
LEG2_STRONG = 2.0
# 交割月不判断（11-10 他定的）：有一条腿已进交割月的格子（如 11 月的 cu2611-cu2612）一律不判断：档 = 不判断、符号 ？，
# 不出猜首笔、首笔半、首笔区间和任何标记 / 色块（竞价手数小框照旧），浮层在 ？ 后面贴灰框「交割月」，csv 的「交割月」列记是哪条腿。
# 两年回放定规则时去掉了交割月腿，规则从没在这类格子上验过；实盘 11-09 早、11-10 早 cu2611-2612 都判 ▲ 贴「打」，模拟 −27、−42 档。
# 交割月按这次开盘所属交易日的月份算（trade_day：夜盘属于下一个交易日，11-30 周一 21:00 的夜盘属于 12-01，那时 cu2612 已进交割月）；
# 开盘复盘.in_delivery 是按开盘那个月算的（只给记分卡另列用），月末那一晚夜盘两边会差一个月。False = 回到老样子
DELIVERY_SKIP = True
# ---- 薄大（2026-10-30，分析/2026-10-26/腿2控价/小结.md 第 12、14 节）----
BOOK_SHOW = ("sc",)  # 只有 sc 判薄大（nr、fu、bu、lu 回放里薄大不亏）
THIN_STEP = 5  # 要吃那边第二档离第一档 ≥ 5 档 = 控价大（他定的）
THIN_N, THIN_MIN = 40, 10  # 平时 = 同场次前 40 次开盘腿2 两边五档量平均的中位数，不够 10 次不判
BOOK_SEED = rv.REVIEW / "腿2盘口_{}.csv"  # 两年回放切出来的腿2 五档量（开盘、腿2五档均；只留本机），跟 数据/ 里的实盘开盘一起用
SYMBOL = {"真": "✅", "半真": "▲", "假": "❌", "没针": "○", "不判断": "？"}
SAY = {"真": "大概率整根开到", "半真": "大概率开到一部分", "假": "大概率开不到（假）"}
KINDS = tuple(SYMBOL)
# ---- 实盘的时间 ----
SHOW = 60  # 标签开盘后再留 60 秒
EARLIEST, LATEST = timedelta(minutes=10), timedelta(seconds=90)  # 开盘前 10 分钟以内、HH:58:30 以前才跑
AUCTION = timedelta(seconds=60)  # 竞价那张快照在开盘前一分钟里（20:59:00.5）
SUBSCRIBE_STOP = timedelta(seconds=100)  # 离开盘 100 秒（HH:58:20）还没订阅上的合约不再逐个试，留时间重连、按时写结果（11-09）
GIVE_UP = timedelta(seconds=20)  # HH:59:40 还没收到竞价快照，就当这次没开盘
SETTLE = 3  # 第一张竞价快照到了以后，最多再等 3 秒等其余的
CLOSE_BY = timedelta(seconds=20)  # 取 30 秒均价要的 tick 最晚等到竞价前 20 秒（HH:58:40），没到的合约昨收退回收盘快照（11-08 审查）
CLOSE_AVG = 30  # 昨收 = 上一节收盘前 30 秒成交均价（11-08 起，测试/2026-11-08_昨收算法/小结.md；5 秒效果只有约三分之一）；0 = 回到最后一笔
NTP = ("ntp.aliyun.com", "ntp.tencent.com", "cn.pool.ntp.org")  # 问标准时间（10-23 晚发现这台电脑的时钟慢了 14.7 秒）
CLOCK_WARN = 3  # 电脑时钟差 3 秒以上，提示里说一声
CSV_COLS = ["开盘", "组合", "腿1", "腿2", "品种", "档", "符号", "首笔下沿", "首笔上沿", "理由", "昨收价差", "腿1竞价", "腿2竞价",
            "针", "针偏几档", "门槛", "冷门腿", "冷门腿竞价手数", "冷门腿竞价占持仓‰", "冷门腿竞价持仓变化", "竞价量分",
            "立刻能卖", "立刻能买", "托住几成", "盘口分", "合计", "保留比例下", "保留比例上", "保留比例依据", "腿1盘口宽档",
            "腿1竞价手数", "腿1卖一量", "腿1买一量", "腿2竞价手数", "腿2卖一量", "腿2买一量", "一档", "文字", "快照时间", "算好时间", "来源",
            "v1档", "猜首笔", "区间依据", "立刻能成交几倍门槛", "腿1跳空档", "腿2跳空档", "主力动得少", "重仓标记", "近", "首笔半",
            "腿2台阶2", "腿2要吃五档", "腿2五档均", "腿2平时", "腿2厚薄倍", "腿2平时依据", "薄大", "不重", "远档", "两层手数", "小针", "持仓比", "量比", "换月条件", "换月", "换月缺数", "昨收来源", "交割月"]


# ---------- 判断（实盘、回放、开盘复盘共用） ----------

def half_up(x: float) -> int:
    """四舍五入到整档（0.5 往远离 0 的方向），跟 开盘复盘.needle_truth 一样。"""
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def decimals(t: float) -> int:
    """一档 0.1 → 1 位小数，0.02 → 2 位，1、5 → 整数。"""
    return max(0, math.ceil(-math.log10(t) - 1e-9))


def price(x, t: float) -> str:
    return "" if x is None else f"{x:.{decimals(t)}f}"


def judge(a: dict | None, b: dict | None, close1, close2, t: float, gate, names=("腿1", "腿2"), mix=None, slack=0,
          relax=False, small=None) -> dict:
    """a、b = 两腿竞价那张快照 {竞价价（竞价没成交是 None）, 竞价手数, 持仓（竞价后）, 持仓变化, 买一, 卖一, 买一量, 卖一量}；
    close1、close2 = 两腿昨收；t = 价差的一档；gate = 门槛（档，None = 还没定）；names = 两腿的合约名（写理由用）；
    mix = 混合版的系数（MIX，None = v1）；slack = 混合版猜的首笔差门槛几档以内也不判 ❌（SLACK）；
    relax = 第 1 步只有「竞价量分 −1、盘口分 0」时不判 ❌、交给第 2 步（RELAX 的品种）；
    small = 针没出门槛、但立刻能成交那边顺着针偏到这么多档时判 ▲、记「小针」（SMALL 的品种，None = 不看）。
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
        k = "立刻能卖" if d > 0 else "立刻能买"
        e = half_up(round((out[k] - base) / t, 6)) * (1 if d > 0 else -1) if small and d and k in out else None
        if e is not None and e >= small:  # 小针（SMALL）：针不大，但竞价后盘口已经顺着针托出去这么远
            guess = base + (mix or 0.9) * (out[k] - base)
            out |= {"档": "半真", "小针": "小针", "猜首笔": round(guess, decimals(t) + 1),
                    "理由": f"针偏 {d:+d} 档，没出门槛 {gate} 档；但竞价后{k}的价差 {price(out[k], t)}，顺着针偏 {e} 档（≥ {small} 档），"
                            f"算小针 ▲（两年回放这种针挂首笔半也赚，测试/2026-11-06_fu专项/小结.md）"}
            return out
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
    soft = relax and vol == -1 and book == 0 and mix is not None  # 只是量少、盘口还托得住四到七成：交给第 2 步
    if vol + book <= -1 and k in out and not heavy and not soft:
        out["档"] = out["v1档"] = "假"
        why.append(f"合计 {vol + book:+d} → 大概率假")
        out["理由"] = "；".join(why)
        return out
    # 盘口缺一边（sc 两年里 7 次全是竞价涨跌停锁住）：看不到托不托得住，光凭竞价量分不判 ❌（两腿都锁住的 3 根针都整根开到）；
    # 立刻能成交那边偏出门槛 2 倍以上：也不判 ❌（sc、nr、lu 两年里这样被判 ❌ 的 5 根全开到了门槛外，红利 8–54 档）
    locked = ("" if vol + book > -1 else
              f"，但立刻能成交那边偏出门槛 {times:.1f} 倍（≥ {HEAVY:g} 倍，两年回放里这样的全开到了门槛外），不判 ❌" if heavy else
              "，但只是竞价量少、盘口还托得住，不直接判 ❌（冷门腿竞价量本来就少，这时量分分不出真假），按猜首笔判" if soft else
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
        # 倍数往下截一位（不四舍五入）：浮层拿这个字判「重」（≥ 3 倍），2.96 不能写成 3.0（11-08 审查）
        out["重仓标记"] = " ".join([f"重{math.floor(times * 10 + 1e-9) / 10:.1f}"] + ["腿2秒进"] * out["主力动得少"] + ["量少"] * few)
        why.append(f"重仓标记「{out['重仓标记']}」：立刻能成交那边偏出门槛 {times:.1f} 倍"
                   + (f"；主力竞价跳了 {g2:+.0f} 档、腿1 {g1:+.0f} 档，主力动得少，腿2 要秒进" if out["主力动得少"] else "")
                   + (f"；冷门腿竞价只占持仓 {out['冷门腿竞价占持仓‰']:.2f}‰，能成交的手数少" if few else ""))
    out["理由"] = "；".join(why)
    return out


def retention(past, before: datetime, kind: str | None = None, product: str | None = None) -> tuple:
    """首笔保留针几成的八成范围 (下, 上, 依据)：开盘复盘.csv 里 before 以前的开盘、同品种组合（product）、针偏 3 档以上的。
    11-05 审查前没按 product 过滤，混用了别的品种。"""
    if past is None or not len(past) or "首笔偏几档" not in past:
        return RET_DEFAULT + ("默认（还没有开盘复盘）",)
    p = past[pd.to_datetime(past["开盘"], format="%Y-%m-%d %H:%M", errors="coerce") < pd.Timestamp(before)]
    p = p[~p["品种"].astype(str).str.contains("-") & p["针偏几档"].notna() & p["首笔偏几档"].notna()]
    if product is not None:
        p = p[p["品种"].astype(str) == product]
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
    """首笔区间的分位回归：两年回放（SEED）+ 开盘复盘.csv 里 before 以前的开盘，取最近 FIT_N 次独立开盘的全部有效组合。
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
        d = d.drop_duplicates(["开盘", "组合"], keep="last").sort_values("开盘")
        openings = d["开盘"].drop_duplicates().tail(FIT_N)  # 同场多个组合只算一次开盘，窗口内保留全部组合
        if len(openings) < FIT_MIN:
            return None
        d = d[d["开盘"].isin(openings)]
        rows = [axes(r["针偏几档"], r["昨收价差"], r["立刻能卖"], r["立刻能买"], t) + (r["首笔偏几档"],) for _, r in d.iterrows()]
        A = [[1.0, e, x] for s, x, e, y in rows]
        Y = [s * y for s, x, e, y in rows]
        qs = FIT_Q_BY.get(product, FIT_Q)
        lo, hi = (qreg(A, Y, q) for q in qs)
    except Exception as e:  # 种子文件坏了、scipy 没装好、算不出来：用老办法，不耽误判断
        print(f"{product} 首笔区间的分位回归没算出来，用老办法：{type(e).__name__} {e}")
        return None
    _FITS[key] = {"下": lo, "上": hi, "次数": len(openings), "起": d["开盘"].iloc[0], "止": d["开盘"].iloc[-1],
                  "几成": f"{'零一二三四五六七八九十'[round((qs[1] - qs[0]) * 10)]}成"}
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
    return (f"最近 {fit['次数']} 次开盘（{fit['起'][:10]} ~ {fit['止'][:10]}）的{fit.get('几成', '八成')}范围：顺着针的方向，"
            f"下沿 {a[0]:+.1f} {a[1]:+.2f}×E {a[2]:+.2f}×X 档、上沿 {b[0]:+.1f} {b[1]:+.2f}×E {b[2]:+.2f}×X 档"
            "（E = 立刻能成交那边偏几档，X = 针偏几档）")


def trade_day(o: datetime):
    """这次开盘属于哪个交易日（11-10 加，交割月用）：日盘 = 当天；夜盘（21:00）= 下一个工作日（周五夜盘 → 下周一）。
    不查节假日：长假前一晚没有夜盘，碰不到。"""
    d = o.date()
    if o.hour >= 18:
        d += timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
    return d


def delivery_legs(o: datetime, c1: str, c2: str) -> list[str]:
    """已进交割月的腿：合约 YYMM ≤ 这次开盘所属交易日的 YYMM（cu2611 在 11 月的交易日就算）。DELIVERY_SKIP = False 时返回空。"""
    if not DELIVERY_SKIP:
        return []
    ym = f"{trade_day(o):%y%m}"
    return [c for c in (c1, c2) if c[-4:].isdigit() and c[-4:] <= ym]


def evaluate(o: datetime, pairs: list, snaps: dict, closes: dict, ticks: dict, past, source: str,
             missing: dict | None = None) -> list[dict]:
    """每组一行：判断 + 首笔区间 + 标签上的字。snaps = {合约: 快照}，closes = {合约: 昨收}，ticks = {合约: 最小变动价位}，
    missing = {合约: 为什么没有快照（已到期、订阅不了）}。"""
    rows, missing = [], missing or {}
    for s1, s2 in pairs:
        c1, c2 = s1.split(".")[-1], s2.split(".")[-1]
        p1, p2 = rv.product_of(s1), rv.product_of(s2)
        key = p1 if p1 == p2 else f"{p1}-{p2}"  # 跨品种组合按「nr-ru」查门槛、混合版、首笔区间（11-05 晚 nr-ru 定了 8 档；没定的照旧不判断）
        t = ticks.get(s1) or rv.TICK.get(p1) or 1
        if p1 != p2:  # 跨品种：两腿一档不一样时，价差按小的那个算
            t = min(t, ticks.get(s2) or rv.TICK.get(p2) or t)
        bad = [f"{s.split('.')[-1]} {missing[s]}" for s in (s1, s2) if s in missing]
        dlv = delivery_legs(o, c1, c2)  # 交割月不判断（11-10，DELIVERY_SKIP）
        if bad:
            j = {"档": "不判断", "理由": "、".join(bad)}
        elif dlv:  # 不出猜首笔、区间、换月、薄大、重仓这些：bad 置成真，下面换月、薄大两段（not bad）就跳过；区间看 dlv
            j = {"档": "不判断", "交割月": "、".join(dlv),
                 "理由": f"{'、'.join(dlv)} 已进交割月，不判断：规则没在交割月腿上验过（两年回放定规则时去掉了交割月腿）"}
            bad = True
        else:
            j = judge(snaps.get(s1), snaps.get(s2), closes.get(s1), closes.get(s2), t,
                      rv.NEEDLE_GATE.get(key), (c1, c2), MIX.get(key), SLACK.get(key, 0), key in RELAX, SMALL.get(key))
            if p1 == p2 and p1 in FEE_SKIP and j.get("档") in ("真", "半真") and not j.get("重仓标记"):
                j["不重"] = "不重"
                j["理由"] = (j.get("理由", "") + f"；不重：{p1} 一组开平手续费约 3.3 档，两年回放不重的每手扣费后约 1 档、腿2 晚半秒就亏，"
                            "浮层贴灰框「不重」")
        if p1 == p2 and not bad and (ROLL_ALL or p1 in ROLL_OFF):
            try:
                rc = roll_conds(p1, c1, c2, o, snaps.get(s1), snaps.get(s2))
            except Exception as e:  # 换月只是加个标记，出错也不能耽误判断
                rc = {"条件": [], "说明": [], "持仓比": None, "量比": None, "缺数": f"换月没算成（{type(e).__name__}）"}
            j |= {k: rc[k] for k in ("持仓比", "量比") if rc.get(k) is not None}
            if rc.get("缺数"):
                j["换月缺数"] = rc["缺数"]
                if j.get("档") in ("真", "半真"):
                    j["理由"] = j.get("理由", "") + f"；{rc['缺数']}"
            if rc["条件"]:
                j["换月条件"] = "+".join(rc["条件"])
            label = roll_label(rc["条件"]) if (ROLL_ALL or "A" in rc["条件"]) else None
            if label and j.get("档") in ("真", "半真"):
                j["换月"] = label
                off = label != "换月" or p1 in ROLL_OFF
                j["理由"] = (j.get("理由", "") + f"；{label}：" + "、".join(rc["说明"])
                            + "，两年回放换月时判 ✅▲ 的针腿2 头半秒跑得多、现实成交偏亏（条件越多越亏），"
                            + ("浮层色块关" if off else "浮层色块降一级"))
        if p1 == p2 and not bad:
            j |= leg2_book(j, snaps.get(s2), t, o, p1)
            if j.get("薄大"):
                side = "卖" if j["针偏几档"] > 0 else "买"
                j["理由"] = (j.get("理由", "") + f"；薄大：腿2 要吃{side}盘，第二档离第一档 {j['腿2台阶2']:g} 档（≥ {THIN_STEP}），"
                            f"{side}一到{side}五 {j['腿2要吃五档']} 手、是平时（{j['腿2平时']:g} 手）的 {j['腿2厚薄倍']:.2f} 倍"
                            "（sc 两年回放这种腿2 晚半秒多半亏，浮层顶替「重」）")
        fit = range_fit(past, o, key) if key in FIT else None
        rg = fit_range(j, t, fit)
        row = {"开盘": f"{o:%Y-%m-%d %H:%M}", "组合": f"{c1}-{c2}", "腿1": c1, "腿2": c2,
               "品种": key, **j, "符号": SYMBOL[j["档"]], "一档": t, "来源": source}
        if rg:
            row["区间依据"] = fit_note(fit)
        elif not dlv:  # 老办法：盘口两头各 1 档 + 保留比例（交割月的格子不出区间）
            ret = retention(past, o, j["档"] if j["档"] in ("真", "半真", "假") else None, key)
            rg = first_range(j, t, ret)
            row |= {"保留比例下": round(ret[0], 2), "保留比例上": round(ret[1], 2), "保留比例依据": ret[2],
                    "区间依据": f"盘口立刻能成交的价差两头各 1 档 + 保留比例（{ret[2]}）"}
        if rg:
            row |= {"首笔下沿": rg[0], "首笔上沿": rg[1]}
        name = fl.label_name(s1, s2)
        guess = f"{j['猜首笔']:.{decimals(t) + 1}f}" if j.get("猜首笔") is not None else ""  # 混合版猜的首笔，写在区间前面
        if guess:  # 首笔半 = 昨收价差 + (猜首笔 − 昨收价差) × 0.5（10-25 他定的，跟猜首笔并排写；ag、sn、ni × 1/3，见 HALF_BY）
            row["首笔半"] = round(j["昨收价差"] + (j["猜首笔"] - j["昨收价差"]) * half_of(key), decimals(t) + 1)
            row["近"] = round(j["昨收价差"] + (j["猜首笔"] - j["昨收价差"]) * NEAR, decimals(t) + 1)
            far = FAR.get(key)
            if far:  # 远档（11-02 夜加）：首笔半 n1 手 + 远档 n2 手
                row["远档"] = round(j["昨收价差"] + (j["猜首笔"] - j["昨收价差"]) * far[0], decimals(t) + 1)
                row["两层手数"] = f"首笔半 {far[1]} 手 + 远档 {far[2]} 手"
        nd = decimals(t) + 1  # 11-09 起文字只写「首笔估」= 首笔半（csv 的「近」「远档」列照算、不进文字）
        row["文字"] = (f"{name}  首笔 {guess}  首笔估 {row['首笔半']:.{nd}f}\n"  # 两行：浮层把第二行写在下面
                      f"首笔区间 ({price(rg[0], t)} ~ {price(rg[1], t)})" if rg and guess else
                      f"{name}  首笔 {price(rg[0], t)} ~ {price(rg[1], t)}" if rg else
                      f"{name}  " if dlv else f"{name}  {j['理由']}")  # 交割月：浮层去掉组合名后只剩「？」+ 灰框「交割月」（标记见 tags_of）
        for n, s in (("腿1", s1), ("腿2", s2)):  # 竞价成交手数（浮层上标签左边的小框）和竞价后的盘口挂单手数（只记进 csv）
            x = snaps.get(s) or {}
            row[f"{n}竞价手数"], row[f"{n}卖一量"], row[f"{n}买一量"] = x.get("竞价手数"), x.get("卖一量"), x.get("买一量")
        snap_t = [x["时间"] for x in (snaps.get(s1), snaps.get(s2)) if x and x.get("时间")]
        if snap_t:
            row["快照时间"] = f"{max(snap_t):%H:%M:%S.%f}"[:-5]
        rows.append(row)
    return rows


# ---------- 薄大：腿2 盘口厚薄和台阶 ----------

def five_avg(x: dict | None):
    """快照里腿2 两边五档量的平均（买 + 卖 ÷ 2）；没有五档或全是 0 返回 None。"""
    if not x:
        return None
    v = [x.get(f"{w}{k}量") for k, _ in LEVELS for w in ("买", "卖")]
    if any(y is None for y in v) or not sum(v):
        return None
    return sum(v) / 2


_NORM = {}


def book_norm(o: datetime, product: str) -> tuple:
    """腿2「平时」盘口量 (值, 依据)：o 以前同场次（夜盘 / 日盘）最近 THIN_N 次开盘、腿2 两边五档量平均的中位数，不够 THIN_MIN 次返回 None。
    来源：BOOK_SEED（两年回放）+ 数据/ 里每次开盘的 竞价判断.csv（有「腿2五档均」就用，没有就读那次腿2 的 tick 文件）。"""
    key = (o, product)
    if key in _NORM:
        return _NORM[key]
    night = o.hour >= 20
    vals = {}
    seed = rv.read_csv(Path(str(BOOK_SEED).format(product)))
    if seed is not None and len(seed):
        for _, r in seed.iterrows():
            t = datetime.strptime(str(r["开盘"]), "%Y-%m-%d %H:%M")
            if num(r.get("腿2五档均")):
                vals[t] = num(r["腿2五档均"])
    for d in sorted(rv.DATA.glob("20??-??-??_????")):
        try:
            t = datetime.strptime(d.name, "%Y-%m-%d_%H%M")
        except ValueError:
            continue
        if t >= o or (t.hour >= 20) != night:
            continue
        j = rv.read_csv(d / CSV_NAME)
        if j is None or "品种" not in j:
            continue
        j = j[(j["品种"] == product) & j["腿2"].notna()]
        if not len(j):
            continue
        v = num(j.iloc[0].get("腿2五档均")) if "腿2五档均" in j else None
        if v is None:
            files = list(d.glob(f"*.{j.iloc[0]['腿2']}.csv"))
            v = five_avg(snap_from_ticks(rv.read_ticks(files[0]), t)) if files else None
        if v:
            vals[t] = v
    past = [v for t, v in sorted(vals.items()) if t < o and (t.hour >= 20) == night][-THIN_N:]
    out = (None, f"同场次只有 {len(past)} 次开盘（要 {THIN_MIN} 次）") if len(past) < THIN_MIN else (
        float(pd.Series(past).median()), f"{'夜盘' if night else '日盘'}前 {len(past)} 次开盘的中位数")
    _NORM[key] = out
    return out


def leg2_book(j: dict, x: dict | None, t: float, o: datetime, product: str) -> dict:
    """腿2 要吃那边的台阶和厚薄（记进 csv）；BOOK_SHOW 的品种判 ✅▲ 时再看薄大。
    针往上 = 价差空单 = 买腿2 → 吃卖盘，台阶2 = 卖二 − 卖一；针往下 = 价差多单 = 卖腿2 → 吃买盘，台阶2 = 买一 − 买二。"""
    out = {}
    avg = five_avg(x)
    if avg is not None:
        out["腿2五档均"] = round(avg, 1)
    dev = j.get("针偏几档")
    if not x or dev is None or dev == 0:
        return out
    w = "卖" if dev > 0 else "买"
    p1, p2 = x.get(f"{w}1价"), x.get(f"{w}2价")
    if p1 is not None and p2 is not None:
        out["腿2台阶2"] = round((p2 - p1) / t * (1 if dev > 0 else -1), 1)
    vol = [x.get(f"{w}{k}量") for k, _ in LEVELS]
    if all(v is not None for v in vol):
        out["腿2要吃五档"] = int(sum(vol))
    if product not in BOOK_SHOW:
        return out
    norm, why = book_norm(o, product)
    out["腿2平时依据"] = why
    if norm is None or "腿2要吃五档" not in out:
        return out
    out["腿2平时"] = round(norm, 1)
    out["腿2厚薄倍"] = round(out["腿2要吃五档"] / norm, 2)
    if (j.get("档") in ("真", "半真") and out.get("腿2台阶2") is not None and out["腿2台阶2"] >= THIN_STEP - 1e-9
            and out["腿2厚薄倍"] < 1):
        out["薄大"] = "薄大"
    return out


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
    """回放：竞价窗口累计成交与最新盘口（成交手数、持仓变化在 tick 中是增量）。"""
    r = rv.auction_snapshot(df, o)
    if r is None:
        return None
    vol = num(r.get("成交手数")) or 0
    return {"时间": r.name.to_pydatetime(), "竞价价": num(r["最新价"]) if vol > 0 else None, "竞价手数": vol,
            "持仓": num(r.get("持仓量")), "持仓变化": num(r.get("持仓变化")), "买一": num(r.get("买一价")),
            "卖一": num(r.get("卖一价")), "买一量": num(r.get("买一量")), "卖一量": num(r.get("卖一量")),
            **{f"{w}{k}{x}": num(r.get(f"{w}{c}{x}")) for k, c in LEVELS for w in ("买", "卖") for x in ("价", "量")}}


LEVELS = [(1, "一"), (2, "二"), (3, "三"), (4, "四"), (5, "五")]  # 快照里五档：买1价…卖5量（tick 文件里叫 买一价…卖五量）


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
            "买一量": num(q.bid_volume1), "卖一量": num(q.ask_volume1),
            **{f"{w}{k}{x}": num(getattr(q, f"{a}_{y}{k}", None)) for k, _ in LEVELS
               for w, a in (("买", "bid"), ("卖", "ask")) for x, y in (("价", "price"), ("量", "volume"))}}


def prefetch_ticks(api, symbols, until: datetime, clock) -> dict:
    """一次订阅所有合约的 tick 序列（算 30 秒均价用），最多等到 until（按 clock 的标准时间）。返回 {合约: tick 序列}，到点没收全的不在里面。
    11-08 审查：原来逐个同步 get_tick_serial，一个合约卡住最多等 158 秒，会拖过竞价、整场判断作废。
    在 task 里调用 get_tick_serial 不阻塞，只发订阅，数据到没到用 is_serial_ready 看。"""
    dfs = {}

    async def sub():
        for s in symbols:
            try:
                dfs[s] = api.get_tick_serial(s, data_length=400)
            except Exception as e:
                print(f"{s} 订阅 tick 序列失败：{type(e).__name__} {e}")

    task = api.create_task(sub())
    while True:
        left = (until - clock()).total_seconds()
        if left <= 0 or (task.done() and all(api.is_serial_ready(d) for d in dfs.values())):
            break
        api.wait_update(deadline=time.time() + min(0.5, left))
    ready = {s: d for s, d in dfs.items() if api.is_serial_ready(d)}
    late = [s for s in symbols if s not in ready]
    if late:
        print(f"到 {until:%H:%M:%S} 还没收全 tick 序列：{'、'.join(late)}，这些合约昨收退回收盘快照")
    return ready


def close_avg(api, s: str, o: datetime, n: int = None, df: pd.DataFrame = None):
    """昨收（11-08 起）：上一节收盘前最后 n 秒（CLOSE_AVG）的成交量加权均价 = 成交额增量 ÷ (成交手数 × 合约乘数)，
    和 测试/2026-11-08_昨收算法/a_收盘价.py 的 B30 同一个算法（窗口 = (收盘时刻 − n 秒, 收盘时刻]）。
    这段没成交、取不到 tick、找不到收盘那节，返回 None，交给 close_of 用最后一笔。"""
    n = n or CLOSE_AVG
    if df is None:
        return None
    df = df[df["datetime"] > 0].dropna(subset=["datetime", "volume", "amount"])
    if not len(df):
        return None
    t = pd.to_datetime(df["datetime"].astype("int64"), utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None).to_numpy()
    df = df.assign(t=t)
    df = df[df["t"] < pd.Timestamp(o - AUCTION)]  # 竞价那张和之后的不要
    ends = df["t"].dt.ceil("min")
    hit = ends.dt.strftime("%H:%M").isin(tick.SESSION_ENDS) & (df["t"] > ends - pd.Timedelta(minutes=2))
    if not hit.any():
        return None
    e = ends[hit].max()  # 上一节收盘时刻（15:00 / 23:00 / 01:00 / 02:30）
    dv, da, gap = df["volume"].diff(), df["amount"].diff(), df["t"].diff()
    # 冷门合约的序列可能跨节：夜盘最后一笔在 22:58，却仍留着下午 14:59 的快照。
    # 候选收盘以后已经有成交（含新交易日累计量重置），它就不是上一节的昨收；交给 close_of 退回最后一笔。
    # 只有时间更新、累计量额没变的盘前快照不算新成交。
    if ((df["t"] > e) & (df["volume"] > 0) & ((dv != 0) | (da != 0))).any():
        return None
    w = (df["t"] > e - pd.Timedelta(seconds=n)) & (df["t"] <= e) & (dv > 0) & (da > 0) & (gap <= pd.Timedelta(minutes=10))
    v = dv[w].sum()
    mult = num(api.get_quote(s).volume_multiple)
    if not v or not mult:
        return None
    return round(float(da[w].sum() / (v * mult)), 6)


def close_of(api, s: str, before: dict, o: datetime, df: pd.DataFrame = None, late: bool = False):
    """昨收：先用收盘前 30 秒成交均价（close_avg，11-08 起，两年回放比最后一笔好：真针判 ❌ 少约一成、盈亏 18 个品种 15 个变好，
    见 测试/2026-11-08_昨收算法/小结.md）；那段没成交 / 取不到，退回竞价前最后一张快照的最新价（上一节收盘价）。
    万一那张快照是开盘前几分钟才推的（不是收盘那张），改用 1 分钟线（late = 已过 CLOSE_BY 就不再联网取，免得拖过竞价）。
    df = prefetch_ticks 取好的 tick 序列，没有就不算均价。"""
    if CLOSE_AVG:
        try:
            c = close_avg(api, s, o, df=df)
            if c is not None:
                return c, f"{CLOSE_AVG}秒均价"
        except Exception as e:
            print(f"{s} 收盘均价没算出来：{type(e).__name__} {e}")
    bt = before.get("时间")
    if before.get("最新价") is not None and bt is not None and bt < o - timedelta(minutes=6):
        return before["最新价"], "收盘快照"
    if late:
        return before.get("最新价"), "快照"
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
                    "组合": {r["组合"]: {"档": r["档"], "符号": r["符号"], "文字": r["文字"], "盘口": book_of(r), "标记": tags_of(r), "腿2倍": leg2_times(r)}
                           for r in rows}}


def book_of(r: dict) -> list:
    """浮层盘口框：[[腿1, 竞价手数], [腿2, 竞价手数]]（10-24 晚起框里只写竞价成交手数，卖一买一挂单量没用，照样记进 csv）；
    两腿都没有快照（没订阅上、已到期）返回空，不画框。"""
    legs = [[r[n], r.get(f"{n}竞价手数")] for n in ("腿1", "腿2")]
    return legs if any(x[1] is not None for x in legs) else []


def grouped(d: dict) -> dict:
    """{品种: 值} → {值: [品种, …]}，网页上同样的值写在一起。"""
    out = {}
    for p, v in d.items():
        out.setdefault(v, []).append(p)
    return out


def tags_of(r: dict) -> list:
    """浮层上的重仓标记 [[字, 样式], …]，贴在符号后面；只有 HEAVY_SHOW 的品种（同品种跨月）贴「重…」那一串。
    「腿2秒进」11-06 他定的：只要主力动得少，不管针判真假、重不重、哪个品种都贴（LEG2_ALWAYS）。
    交割月不判断的格子（11-10，DELIVERY_SKIP）只贴一个灰框「交割月」（csv 读回来空格是 NaN，所以要看是不是字）。"""
    if r.get("档") == "不判断" and isinstance(r.get("交割月"), str) and r.get("交割月"):
        return [["交割月", TAG_STYLE["交割月"]]]
    t = r.get("重仓标记")
    if r.get("品种") in BOOK_SHOW and r.get("薄大") == "薄大":  # 薄大顶替重仓那一串（sc 两年回放：重 + 薄大 也是亏的）
        out = [["薄大", "薄"]]
    elif r.get("品种") in FEE_SKIP and r.get("不重") == "不重":
        out = [["不重", "灰"]]
    elif r.get("品种") in HEAVY_SHOW and isinstance(t, str) and t:
        out = [[w, "重" if w.startswith("重") else TAG_STYLE.get(w, "少")] for w in t.split()]
    else:
        out = []
    if r.get("小针") == "小针":
        out.append(["小针", TAG_STYLE["小针"]])
    if r.get("换月") in ("换月", "换月2", "换月！"):
        out.append([r["换月"], TAG_STYLE["换月"]])
    if LEG2_ALWAYS and leg2_slow(r) and "腿2秒进" not in [w for w, _ in out]:
        out.append(["腿2秒进", TAG_STYLE["腿2秒进"]])
    if leg2_strong(r):
        out = [["腿2秒进！", st] if w == "腿2秒进" else [w, st] for w, st in out]
    return out


def oi_ratio(a: dict | None, b: dict | None) -> float | None:
    """腿1 开盘前持仓 ÷ 腿2 开盘前持仓（开盘前持仓 = 竞价后持仓 − 持仓变化）；缺数返回 None。"""
    pre = []
    for x in (a, b):
        if not x or x.get("持仓") is None or x.get("持仓变化") is None or x["持仓"] - x["持仓变化"] <= 0:
            return None
        pre.append(x["持仓"] - x["持仓变化"])
    return pre[0] / pre[1]


def roll_conds(p: str, c1: str, c2: str, o, a: dict | None, b: dict | None) -> dict:
    """换月三条（见 ROLL_ALL）：{"条件": ["A", "B", "C" 里满足的], "说明": [...], "持仓比", "量比", "缺数"}；缺数的那条当不满足。
    成交量只用这次开盘该用的那天（主力表.ref_day）；那天没下到就不用更早的冒充，B、C 记成未知、写进「缺数」（11-07 审查）。
    读的是两条腿自己的量（主力表._raw，交割月还在交易的 cu2611 也有），不是挑主力那张去掉交割月的表。"""
    import 主力表 as zl
    out = {"条件": [], "说明": [], "持仓比": None, "量比": None, "缺数": ""}
    m1, m2 = c1[-4:], c2[-4:]
    ds, why = zl.ref_day(o)
    if not ds:
        out["缺数"] = f"换月 B、C 未知：{why}"
    day = zl._raw(ds).get(p, {}) if ds else {}
    v1, o1 = day.get(m1, (None, None))
    v2, o2 = day.get(m2, (None, None))
    ratio = oi_ratio(a, b)
    if ratio is None and o1 and o2:
        ratio = o1 / o2
    if ratio is not None:
        out["持仓比"] = round(ratio, 2)
        if ratio >= 1:
            out["条件"].append("A")
            out["说明"].append(f"{c1} 开盘前持仓是 {c2} 的 {ratio:.2f} 倍")
    if v1 is not None and v2:
        out["量比"] = round(v1 / v2, 2)
        if v1 / v2 >= ROLL_VOL:
            out["条件"].append("B")
            out["说明"].append(f"{ds} 成交量 {c1} 是 {c2} 的 {v1 / v2:.0%}")
        days = zl._days()
        if ds not in days:  # 刚在后台下好的
            zl._days.cache_clear()
            days = zl._days()
        i = days.index(ds)
        past = days[max(0, i - ROLL_DAYS):i]

        def avg(m):
            v = [zl._raw(d).get(p, {}).get(m, (None,))[0] for d in past]
            v = [x for x in v if x is not None]
            return sum(v) / len(v) if len(v) >= 3 else None
        a1, a2 = avg(m1), avg(m2)
        if a1 and a2 and v2 <= a2 * (1 - ROLL_SHRINK) and v1 >= a1 * (1 + ROLL_SHRINK):
            out["条件"].append("C")
            out["说明"].append(f"{ds} 收盘 {c2} 缩量到前 {ROLL_DAYS} 日均量的 {v2 / a2:.0%}、{c1} 放量到 {v1 / a1:.0%}")
    return out


def roll_label(conds: list) -> str | None:
    """A、B 一条 → 换月；A、B 都有 → 换月2；三条都有 → 换月！；只有 C 或都没有 → None。"""
    ab = sum(c in conds for c in ("A", "B"))
    if ab == 0:
        return None
    if ab == 2:
        return "换月！" if "C" in conds else "换月2"
    return "换月"


def leg2_slow(r: dict) -> bool:
    """有针（判了 ✅ ▲ ❌）且主力（腿2）竞价跳空比腿1 小。"""
    return r.get("档") in ("真", "半真", "假") and r.get("主力动得少") == True  # noqa: E712（csv 读回来是 numpy 布尔，NaN 也要当否）


def leg2_strong(r: dict) -> bool:
    """腿2秒进，且主力（腿2）竞价自己跳了 ≥ LEG2_STRONG 倍门槛：腿2 开盘那半秒多半还在跑，晚进吃亏最多。"""
    g2, gate = r.get("腿2跳空档"), r.get("门槛")
    try:
        return LEG2_STRONG is not None and leg2_slow(r) and float(gate) > 0 and abs(float(g2)) >= LEG2_STRONG * float(gate)
    except (TypeError, ValueError):
        return False


def leg2_times(r: dict) -> float | None:
    """贴腿2秒进的格子：主力竞价自己跳了几倍门槛（11-08 他定的，浮层写在腿2 后面，跟「重」一样往下取一位小数）；没贴返回 None。"""
    g2, gate = r.get("腿2跳空档"), r.get("门槛")
    try:
        return math.floor(abs(float(g2)) / float(gate) * 10 + 1e-9) / 10 if leg2_slow(r) and float(gate) > 0 else None
    except (TypeError, ValueError):
        return None


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
            + ("有一条腿已进交割月（按这次开盘所属交易日的月份，夜盘算下一个交易日）的格子一律 ？ 不判断、浮层贴灰框「交割月」（11-10 起：规则没在交割月腿上验过）。"
               if DELIVERY_SKIP else "") +
            "定了门槛的品种都用混合版（2026-10-24 从 sc 两年回放定的，同一晚推到所有品种；只有 sc、nr、lu 用两年回放验过）；"
            "都只是线索，每次开盘后在开盘复盘的规则五里对答案。</p>"]
    order = {k: i for i, k in enumerate(("真", "半真", "假", "不判断", "没针"))}
    tab = []
    for r in sorted(rows, key=lambda r: order[r["档"]]):
        t = r["一档"]
        v1 = r.get("v1档")
        sym = r["符号"] + (f"<br><span class='note'>v1 {SYMBOL[v1]}</span>" if v1 and v1 != r["档"] else "")
        tab.append([rv.Raw(f"<span class='k k{r['档']}'>{sym}</span>"), r["组合"],
                    f"{price(r.get('首笔下沿'), t)} ~ {price(r.get('首笔上沿'), t)}" if r.get("首笔下沿") is not None else "",
                    price(r.get("猜首笔"), t / 10), price(r.get("近"), t / 10), price(r.get("首笔半"), t / 10), price(r.get("远档"), t / 10), r.get("两层手数") or "",
                    price(r.get("昨收价差"), t), price(r.get("针"), t),
                    f"{rv.fmt(r.get('针偏几档'), 1, plus=True)}（门槛 {r['门槛']}）" if r.get("门槛") else rv.fmt(r.get("针偏几档"), 1, plus=True),
                    rv.fmt(r.get("冷门腿竞价占持仓‰"), 2), rv.fmt(r.get("冷门腿竞价持仓变化"), 0, plus=True),
                    price(r.get("立刻能卖"), t), price(r.get("立刻能买"), t),
                    f"{r['托住几成'] * 100:.0f}%" if r.get("托住几成") is not None else "",
                    rv.fmt(r.get("立刻能成交几倍门槛"), 1), rv.fmt(r.get("合计"), 0, plus=True),
                    f"{rv.fmt(r['腿1跳空档'], 1, plus=True)} / {rv.fmt(r['腿2跳空档'], 1, plus=True)}" if r.get("腿1跳空档") is not None else "",
                    f"交割月（{r['交割月']}，不判断，浮层贴灰框）" if tags_of(r) == [["交割月", TAG_STYLE["交割月"]]] else
                    ("薄大" + (f"（顶替 {r['重仓标记']}）" if r.get("重仓标记") else "")) if r.get("薄大") == "薄大" else
                    (r["重仓标记"] + ("" if r.get("品种") in HEAVY_SHOW else "（浮层不贴，只记录）")) if r.get("重仓标记") else
                    "不重（手续费贵，浮层贴灰框）" + ("、腿2秒进" if LEG2_ALWAYS and leg2_slow(r) else "") if r.get("不重") == "不重" else
                    "小针（针没出门槛、盘口托得远，浮层贴青框、色块小）" + ("、腿2秒进" if LEG2_ALWAYS and leg2_slow(r) else "") if r.get("小针") == "小针" else
                    ("腿2秒进！（主力动得少、腿2 自己跳 ≥ 2 倍门槛）" if leg2_strong(r) else "腿2秒进（主力动得少）") if LEG2_ALWAYS and leg2_slow(r) else "",
                    ov.get(r["组合"], ""), rv.Raw(f"<span class='why'>{rv.esc(r.get('理由', ''))}</span>")])
    body.append(rv.table(["", "组合", "首笔区间", "猜首笔", "近（浮层「近」）", "首笔半（浮层「远」）", "远档", "两层手数", "昨收价差", "针", "针偏几档", "冷门腿竞价占持仓‰", "竞价后持仓变化",
                          "立刻能卖", "立刻能买", "托住针", "立刻能成交几倍门槛", "合计", "跳空档 腿1 / 主力", "重仓标记", "外盘", "理由"], tab))
    mixed = "，".join(f"{'、'.join(ps)} 系数 {k}" for k, ps in grouped(MIX).items())
    fits = list(dict.fromkeys(f"{r['品种']}：{r['区间依据']}" for r in rows if r.get("区间依据") and r.get("保留比例依据") is None))
    olds = list(dict.fromkeys(r["保留比例依据"] for r in rows if r.get("保留比例依据")))
    body.append("<h2>怎么判断的</h2><p class='note'>先挑 ❌：竞价量分 + 盘口分，合计 ≤ −1 就是 ❌。"
                f"竞价量分：冷门腿（开盘前持仓少的那条腿）竞价手数 ÷ 开盘前持仓，≥ {RATIO_HI}‰ 得 +1，< {RATIO_LO}‰ 得 −1；"
                "竞价后持仓减少超过竞价手数一半（竞价里平仓为主），竞价量再大也算 −1。"
                "盘口分：针往上看「立刻能卖」的价差（腿1买一 − 腿2卖一），针往下看「立刻能买」（腿1卖一 − 腿2买一），"
                f"它离昨收价差的距离是针的几成：≥ {HOLD_HI * 100:.0f}% 得 +1，{HOLD_LO * 100:.0f}%–{HOLD_HI * 100:.0f}% 得 0，"
                f"< {HOLD_LO * 100:.0f}% 得 −1。盘口缺一边（多半是涨跌停锁住）时，光凭竞价量分不判 ❌；"
                f"立刻能成交那边偏出门槛 {HEAVY:g} 倍以上也不判 ❌（sc、nr、lu 两年回放里这样被判 ❌ 的 5 根全开到了门槛外）；"
                f"{'、'.join(RELAX)} 只是竞价量分 −1、盘口分 0 时也不直接判 ❌，交给下面猜首笔（10-31 有色和 br rb hc ao 两年回放：冷门腿竞价量本来就少，这时量分分不出真假）。</p>"
                f"<p class='note'>剩下的：混合版（{mixed}）猜首笔 = 昨收价差 + 系数 ×（立刻能成交那边 − 昨收价差），"
                "按针真假的定义分：离针 1 档以内或更远 → ✅，偏出门槛 → ▲，在门槛以内或往反方向偏 → ❌；"
                f"差门槛（{'，'.join(f'{chr(12289).join(ps)} {k} 档' for k, ps in grouped(SLACK).items())}）以内的也算 ▲，不判 ❌（真针判成 ❌ 最怕）。"
                "盘口缺一边时按 v1：针不到门槛的 "
                f"{BIG} 倍 → ✅，否则 ▲。混合版的格子上，v1 判得不一样时符号下面小字写着 v1 的。"
                f"浮层只写「首笔估」（11-09 起）= 首笔半；表里的「近」= 昨收价差 + {NEAR:g} ×（猜首笔 − 昨收价差），只作对照。"
                f"首笔半 = 昨收价差 + {HALF:g} ×（猜首笔 − 昨收价差），{'、'.join(HALF_BY)} 是 1/3（10-31 有色两年回放：挂近一点成交多、更赚）。"
                "远档 = 昨收价差 + 远档系数 ×（猜首笔 − 昨收价差），每组挂两张单（首笔半 + 远档，手数见「两层手数」）："
                + "，".join(f"{p} {k:g}（{a}+{b}）" for p, (k, a, b) in FAR.items())
                + "（11-02 夜定，两年回放：进单率不少、比只挂首笔半多赚，见 分析/2026-11-02/挂单远近与波动/各品种挂法_新止盈.md）。</p>"
                "<p class='note'>首笔区间："
                + (f"有两年回放或攒够 60 次开盘的品种用分位回归——{rv.esc('；'.join(fits))}。" if fits else "")
                + ("还没攒够的品种：竞价后盘口立刻能成交的价差（两头各放 1 档），和「昨收价差 + 针变化 × 过去首笔保留针几成的八成范围」"
                   f"合在一起，这次的保留比例：{rv.esc('；'.join(olds))}（每组用的见 csv）。" if olds else "")
                + "</p>"
                f"<p class='note'>重仓标记（2026-10-24 晚，sc、nr、lu 两年回放）：立刻能成交那边偏出门槛 {HEAVY:g} 倍以上记「重X.X」（几倍门槛，"
                "3 倍以上更强）；主力（腿2）竞价跳空比腿1 小，加「腿2秒进」（腿2 晚两秒才进多半亏，10.8 那种）；"
                f"冷门腿竞价不到持仓 {RATIO_LO}‰，加「量少」（开盘第一个半秒能成交的手数只有其他大机会的一半到三分之二）。"
                f"浮层上只在 {'、'.join(HEAVY_SHOW)} 的跨月格子贴，别的品种只记在这里和 csv。"
                f"{'、'.join(FEE_SKIP)} 手续费贵（一组开平约 3.3 档），判 ✅▲ 但不重时贴灰框「不重」（两年回放不重的扣费后每手约 1 档、腿2 晚半秒就亏）。"
                "都只是线索，不是买卖建议。</p>"
                f"<p class='note'>薄大（2026-10-30，sc 两年回放）：腿2 要吃那边（针往上看卖盘、往下看买盘）第二档离第一档 ≥ {THIN_STEP} 档，"
                f"而且那边五档量不到平时（同场次前 {THIN_N} 次开盘两边五档量平均的中位数），判 ✅▲ 时记「薄大」，浮层上顶替「重」。"
                "sc 两年 17 次：腿2 同时约打平、腿2 晚半秒多半亏。只判 " + "、".join(BOOK_SHOW) + "；只是线索，不是买卖建议。</p>")
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


def half_of(prod) -> float:
    """首笔半的系数：HALF_BY 里有的品种按它，别的 HALF。"""
    return HALF_BY.get(prod, HALF)


def review_cols(j: dict, first, prod=None) -> dict:
    """开盘复盘.csv 里加的几列：59分判断、判断得分、托住几成、预计首笔区间、首笔在区间里、判断来源、
    v1判断（混合版之前的实盘没有这一列，就是 59分判断）、立刻能卖、立刻能买（以后拟合首笔区间用）、
    猜首笔、首笔半、远档按当前参数重算（含历史开盘）；实盘首笔半、实盘远档单列原始保存值，不补算。"""
    lo, hi = num(j.get("首笔下沿")), num(j.get("首笔上沿"))
    v1 = j.get("v1档")
    guess, base = num(j.get("猜首笔")), num(j.get("昨收价差"))
    half = round(base + (guess - base) * half_of(prod), 4) if guess is not None and base is not None else None  # 没有猜首笔（v1 或没判）就空着
    far = round(base + (guess - base) * FAR[prod][0], 4) if half is not None and prod in FAR else None  # 远档（11-02 夜加）
    out = {"59分判断": j.get("档", ""), "判断得分": num(j.get("合计")), "托住几成": num(j.get("托住几成")),
           "判断来源": j.get("来源", ""), "首笔下沿": lo, "首笔上沿": hi,
           "v1判断": v1 if isinstance(v1, str) and v1 else j.get("档", ""),
           "立刻能卖": num(j.get("立刻能卖")), "立刻能买": num(j.get("立刻能买")),
           "猜首笔": guess, "首笔半": half, "远档": far, "判断昨收价差": base,
           "实盘首笔半": num(j.get("首笔半")) if j.get("来源") == "实盘" else None,
           "实盘远档": num(j.get("远档")) if j.get("来源") == "实盘" else None}
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
            return tick.TqApi(auth=tick.load_auth(), disable_print=False)  # 11-09：天勤自己的通知（连接建立 / 断开等）写进运行日志，出事有线索
        except Exception as e:
            print(f"第 {n} 次连天勤失败：{type(e).__name__} {e}")
            put(jf, state | {"状态": "等待", "红": True, "提示": f"竞价判断：连不上天勤，重试中（{clock():%H:%M:%S}）"})
            if clock() + timedelta(seconds=5) >= stop:
                raise
            time.sleep(5)


class NoQuotes(RuntimeError):
    """连上了天勤，但重连一次后还是一个合约的行情都没来。"""


def subscribe(api, symbols: list, stop: datetime, clock) -> tuple[dict, dict]:
    """订阅行情，返回 (quotes, missing)：先一次全订；不行再逐个试（一个合约有毛病不拖累别的）。
    11-09：逐个试原来没有总时限，天勤不回话时每个合约等 30 秒，几十个合约能卡过开盘、把任务计划占住，下一场被跳过。
    现在过了 stop 就不试了；第一个就超时、一个都没成，说明是天勤没回话，再试别的也一样，直接回去让上面重连。"""
    try:
        return dict(zip(symbols, api.get_quote_list(symbols))), {}
    except Exception as e:
        print(f"一次订阅 {len(symbols)} 个合约没成：{type(e).__name__} {e}")
    quotes, missing = {}, {}
    for s in symbols:
        if clock() >= stop:
            missing[s] = "来不及订阅"
            continue
        try:
            quotes[s] = api.get_quote(s)
        except Exception as e:
            missing[s] = "订阅不了"
            print(f"{s} 订阅不了：{type(e).__name__} {e}")
            if type(e).__name__ == "TqTimeoutError" and not quotes:
                return quotes, missing | {x: "天勤没回话，没试" for x in symbols if x not in missing}
    return quotes, missing


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
        stop = o - SUBSCRIBE_STOP
        quotes, missing = subscribe(api, symbols, stop, clock)
        if not quotes:  # 连上了但行情不来（有同事的电脑 11-03、11-09 各出过一次）：断开重连一次，多半能救回来
            print(f"{clock():%H:%M:%S} 一个合约都没订阅上，断开重连一次")
            put(jf, state | {"状态": "等待", "红": True, "提示": f"竞价判断：天勤没行情，重连一次（{clock():%H:%M:%S}）"})
            try:
                api.close()
            except Exception as e:
                print(f"断开出错（接着重连）：{type(e).__name__} {e}")
            api = connect_until(o, jf, state, clock)
            quotes, missing = subscribe(api, symbols, stop, clock)
        for s, q in list(quotes.items()):
            if q.expired:
                missing[s] = "已到期"
                del quotes[s]
        if not quotes:
            raise NoQuotes("天勤没行情（重连后还是一个合约都没订阅上）：" + "、".join(f"{s} {w}" for s, w in missing.items()))
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
        until = max(o - AUCTION - CLOSE_BY, clock() + timedelta(seconds=3))
        dfs = prefetch_ticks(api, list(before), until, clock) if CLOSE_AVG else {}
        for s in before:
            closes[s], src[s] = close_of(api, s, before[s], o, dfs.get(s), clock() >= until)
        past = rv.read_csv(rv.REC_FILE)
        keys = {rv.product_of(a) if rv.product_of(a) == rv.product_of(b) else f"{rv.product_of(a)}-{rv.product_of(b)}" for a, b in pairs}
        for p in sorted(keys & set(FIT)):  # sc 首笔区间的系数也先算好（要 import scipy，约 1 秒；跨品种按 nr-ru 这样的名字）
            range_fit(past, o, p)
        for p in sorted({rv.product_of(s) for s in symbols} & set(BOOK_SHOW)):  # 薄大要的腿2「平时」盘口量也先算好
            book_norm(o, p)
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
        short = {s.split(".")[-1]: v for s, v in src.items()}  # 结果行里的腿是 ag2612，src 的键是 SHFE.ag2612（11-08 审查：原来永远对不上）
        for r in rows:
            r["昨收来源"] = "/".join(short.get(r.get(n), "") for n in ("腿1", "腿2"))
        log["算好"] = clock()
        for r in rows:
            r["算好时间"] = f"{log['算好']:%H:%M:%S.%f}"[:-3]
        put(jf, result_json(rows, state, f"竞价判断 {log['算好']:%H:%M:%S}（{o + timedelta(seconds=SHOW):%H:%M} 消失）"
                            + (f"；{note}" if note else ""), page))
        log["交给浮层"] = clock()
        other = sorted({v for v in src.values()} - {f"{CLOSE_AVG}秒均价" if CLOSE_AVG else "收盘快照"})
        if other:
            log["昨收"] = f"有的合约昨收用的{'、'.join(other)}"
        return rows, log
    finally:
        t = time.time()
        try:
            api.close()
        except Exception as e:  # 判断已经算好、浮层已经贴上：断开出错只记一笔，不能把结果冲掉（11-05 审查）
            log["断开出错"] = f"{type(e).__name__}: {e}"
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
    if ROLL_ALL:  # 换月要上一交易日的成交量：后台补下上期所每日行情（同事版没有 17:30 仓储取数），不耽误连天勤；59 分时没下完就用已有的
        import threading
        import 主力表 as zl

        def _fetch():
            try:
                res = zl.ensure(o)
                ds, why = zl.ref_day(o)
                tried = "；".join(f"{d} {st}" for d, st in res.items())
                print(f"换月用的每日行情：用 {ds}" if ds else f"换月用的每日行情缺：{why}（换月 B、C 会记成未知）",
                      (f"（这次试了：{tried}）" if tried else "（本来就齐）"), flush=True)
            except Exception as e:
                print(f"换月用的每日行情没下成（不影响判断，只是换月少看成交量那两条）：{type(e).__name__} {e}", flush=True)
        threading.Thread(target=_fetch, daemon=True).start()
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
                  + (f"；断开时出错（不影响结果）：{log['断开出错']}" if "断开出错" in log else "")
                  + (f"；快照到达 − 快照上的时间 = {log['晚到']:+.2f} 秒" if "晚到" in log else "")
                  + (f"；{log['昨收']}" if "昨收" in log else ""))
            for r in rows:
                print(f"  {r['符号']} {r['文字']}")
    except Exception as e:
        import traceback
        print(f"竞价判断出错：{type(e).__name__} {e}\n{traceback.format_exc()}")
        why = "天勤没行情（重连也没用）" if isinstance(e, NoQuotes) else f"竞价判断出错（{type(e).__name__}）"
        put(jf, state | {"状态": "出错", "红": True, "提示": f"{why}，这次没有判断，看运行日志"})
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
