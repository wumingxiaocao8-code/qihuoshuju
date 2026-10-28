"""成交复盘：每天导出的成交记录（两个号），看开盘开在多少、盘中开仓的方向合不合理。

用法（在本文件夹里）：
  python 成交复盘.py 成交记录_261022.csv "成交记录_261022(1).csv"
      把导出的文件拷进「期货数据/成交/原始记录/」（按交易日改名 2026-10-22_账号A.csv、_账号B.csv，原样不转码），再复盘
  python 成交复盘.py 2026-10-22      「成交/原始记录/」里已经有这一天的文件，直接复盘
结果：成交/复盘网页/2026-10-22_复盘.html（给人看的），配对明细在 成交/配对明细/。

一、配对：用 trade-data-analysis 技能里的配对脚本（先开先平，腿1 先成交、腿2 固定；不去重）。
  腿2 按「合约.txt」里每个品种的腿2；合约.txt 里没有的品种（比如黄金），文件里只有两个合约时脚本自己认先成交的为腿1。
  合约.txt 里的跨品种组合（如 nr2612-ru2701，10-27 小草说要配）两腿当天都有成交时按跨品种配，腿1 是前一个合约；腿1 那个品种当天还有别的合约时不配、会打印提示。
  先后按成交编号（10-27 起导出带这一列）；老文件没有成交编号，同一秒里谁先成交只能按文件顺序（导出是最新的在前，这里倒过来）。
  导出只有时分秒：21:00 以后的算前一个交易日晚上的夜盘，0–3 点算夜里，其余算当天白天。
二、开盘：夜盘 21:00、日盘 9:00 开盘后 10 秒内开的仓（小草定的，没大行情一般就前十秒）。
  列出实际开在多少、平在多少，和 tick 复盘（复盘数据/开盘复盘.csv）里同一次开盘的昨收价差、针、第一次真实成交放一起；
  不在合约.txt 里的组合没取 tick，只列成交。
三、盘中开仓：其余开仓。合理价差 = 这个组合最近 5 个交易日（不含当天）的分钟价差中位数，常见区间 = 中间八成（10%–90%）。
  分钟价差 = 两腿同一分钟的收盘价相减，只用两腿这一分钟都有成交的；分钟线从天勤取（免费版每个合约 10000 根，约五六周）。
  方向：空开（卖腿1、买腿2）等价差往下回，开在偏高处才合理；多开反过来。
    开仓价差在最近 5 天里的分位（比它低的分钟占几成）：空开 ≥70% 合理，50–70% 偏中间，<50% 不合理；多开 ≤30% 合理，30–50% 偏中间，>50% 不合理。
  趋势：最近 5 天的中位数比再往前（最多 15 天）的中位数挪动超过常见区间宽度的一半，算价差在往上/往下走；
    价差在往上走还空开（往下走还多开）提示「逆着走」——这种时候「偏高」可能只是新的正常。
  价差在往下走时空开（往上走时多开）标「顺着走」：赌它接着走，看的是趋势，位置偏中间也说得通。
  只是拿近几天的统计对照，不是买卖建议。
四、盈亏：按价差算，(平仓价差 − 开仓价差) × 手数 × 乘数，多开为正、空开反过来，净盈亏 = 价差盈亏 − 手续费。
  不用逐笔：交易所按单个合约记账，程序先平昨仓时，一条腿会平到别的组合的老仓（10-28 B 号 au2612 平到和 au2708 配的那手），
  逐笔就把老仓攒的盈亏算进了这一组。逐笔放在旁边当参考，和价差差得多会标出来。
五、价差账本：每个号从最早的配对结果往后滚，每天没平的组合记下来带到第二天；第二天「期初仓位平仓」先开先平地对上账本，
  就知道开在多少。账本里最早那天以前开的仓不知道开仓价，显示空的。每天收盘后的账本存成 配对明细/日期_账号X_账本.json。
  没平的组合按结算价（天勤取；拿不到结算价的那天用收盘价）算浮动盈亏。
  今天大约赚亏 = 今天平掉的 + 手上持仓，从昨天结算（今天开的从开仓价差）到今天结算的价差变化 × 乘数，再减今天全部手续费；
  应该和快期的「平仓盈亏 + 持仓盈亏 − 手续费」（盯市）差不多。
"""
import argparse
import importlib.util
import json
import re
import shutil
import site
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRADE_ROOT = HERE.parent / "成交"  # 期货数据/成交/
TRADES = TRADE_ROOT / "原始记录"
PAGES = TRADE_ROOT / "复盘网页"
PAIRS = TRADE_ROOT / "配对明细"
LIST_FILE = HERE / "合约.txt"
REC_FILE = HERE / "复盘数据" / "开盘复盘.csv"
SKILL = Path.home() / ".claude" / "skills" / "trade-data-analysis" / "scripts" / "analyze_trades.py"

VENV_SITE = Path.home() / ".venvs" / "vault" / "Lib" / "site-packages"
if VENV_SITE.exists():
    site.addsitedir(str(VENV_SITE))
sys.path.insert(0, str(HERE))

import pandas as pd  # noqa: E402

from 开盘复盘 import Raw, esc, fmt, num, page, table  # noqa: E402  网页样式跟开盘复盘一样

OPEN_SECONDS = 10  # 开盘后几秒内开的仓算开盘单
RECENT_DAYS, EARLIER_DAYS = 5, 15  # 合理价差看最近 5 个交易日；趋势拿再往前 15 天比
AFTER_MIN = 30  # 开仓后看多少分钟
MIN_MINUTES = 100  # 最近 5 天两腿都有成交的分钟少于这个数，提示数据少
EXCHANGE = {"sc": "INE", "nr": "INE", "lu": "INE", "bc": "INE", "ec": "INE"}  # 其余默认上期所
# 合约乘数（每手多少吨 / 千克 / 桶），10-28 小草认可的；没写的品种价差盈亏留空
MULT = {"au": 1000, "ag": 15, "cu": 5, "bc": 5, "al": 5, "zn": 5, "pb": 5, "ni": 1, "sn": 1, "ao": 20,
        "rb": 10, "hc": 10, "ss": 5, "wr": 10, "ru": 10, "nr": 10, "br": 5, "bu": 10, "fu": 10, "lu": 10,
        "sc": 1000, "sp": 10}
EPS = 1e-9


# ---------- 读文件 ----------

def day_from_name(name: str) -> str | None:
    """成交记录_261022.csv → 2026-10-22。"""
    m = re.search(r"(\d{2})(\d{2})(\d{2})", name)
    return f"20{m[1]}-{m[2]}-{m[3]}" if m else None


def import_files(paths: list[str]) -> str:
    """拷进「成交/原始记录/」，按交易日 + 账号 A、B…… 改名；已经有的不覆盖。返回交易日。"""
    days = {day_from_name(Path(p).name) for p in paths}
    if len(days) != 1 or None in days:
        sys.exit(f"从文件名看不出是同一个交易日：{[Path(p).name for p in paths]}")
    day = days.pop()
    TRADES.mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(paths):
        dst = TRADES / f"{day}_账号{chr(65 + i)}.csv"
        if dst.exists():
            print(f"{dst.name} 已有，不覆盖")
        else:
            shutil.copy2(p, dst)
            print(f"{Path(p).name} → 成交/原始记录/{dst.name}")
    return day


def night_date(day: str) -> date:
    """夜盘是前一个工作日晚上（周一的夜盘在上周五）。节后第一天没有夜盘，也就不会有夜盘的成交。"""
    d = date.fromisoformat(day) - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def full_time(t: str, day: str) -> pd.Timestamp:
    """导出的时分秒 → 完整时间；配对器返回的完整日期时间直接保留。"""
    t = t.strip()
    if re.match(r"\d{4}-\d{2}-\d{2}[ T]", t):
        return pd.Timestamp(t)
    h = int(t[:2])
    n = night_date(day)
    d = n if h >= 18 else n + timedelta(days=1) if h < 3 else date.fromisoformat(day)
    return pd.Timestamp(f"{d} {t}")


def normalize(src: Path, day: str, dst: Path) -> None:
    """GBK → UTF-8，时间补上日期，顺序倒过来（导出是最新的在前），买卖去掉全角空格。列按名字读，顺序不同没关系。"""
    df = pd.read_csv(src, encoding="gb18030", dtype=str)
    df.columns = [c.strip() for c in df.columns]
    df = df.iloc[::-1]
    df["成交时间"] = [f"{full_time(t, day):%Y-%m-%d %H:%M:%S}" for t in df["成交时间"]]
    df["买卖"] = df["买卖"].str.replace("　", "").str.strip()
    for c in df.columns:
        df[c] = df[c].str.strip()
    dst.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(dst, index=False, encoding="utf-8-sig")


def prod(c: str) -> str:
    return re.match(r"[A-Za-z]+", c)[0].lower()


def list_pairs() -> list[tuple[str, str]]:
    """合约.txt 里的组合 (腿1, 腿2)。"""
    out = []
    for line in LIST_FILE.read_text(encoding="utf-8").splitlines():
        for item in line.split("#")[0].split():
            legs = [x.split(".")[-1] for x in item.split(",")]
            if len(legs) == 2:
                out.append((legs[0], legs[1]))
    return out


def leg2_map() -> dict:
    """合约.txt 里每个品种的腿2（第二个合约）；跨品种组合不算在里面，另外处理。"""
    out = {}
    for l1, l2 in list_pairs():
        if prod(l1) == prod(l2):
            out.setdefault(prod(l2), l2)
    return out


def cross_leg2(df: pd.DataFrame, leg2: dict) -> dict:
    """合约.txt 里的跨品种组合（如 nr2612-ru2701），两腿当天都有成交、腿1 那个品种当天又只有这一个合约时，
    腿1 品种的腿2 就是另一个品种的合约（小草 10-27 说 nr-ru 要配）。
    跨品种一打开，配对脚本就不再替只有两个合约的品种自己认腿2，这里按同样的规矩（先成交的是腿1）先认好。"""
    have = set(df["合约"])
    out = {}
    for l1, l2 in list_pairs():
        if prod(l1) == prod(l2) or l1 not in have or l2 not in have:
            continue
        if {c for c in have if prod(c) == prod(l1)} != {l1}:
            print(f"{l1}-{l2}：{prod(l1)} 当天还有别的合约，跨品种没配")
            continue
        out[prod(l1)] = l2
    if not out:
        return {}
    order = df.sort_values("成交编号", key=lambda x: pd.to_numeric(x, errors="coerce"), kind="stable", na_position="last") if "成交编号" in df else df
    taken = set(out.values())
    for p in {prod(c) for c in have} - set(out) - set(leg2):
        members = {c for c in have if prod(c) == p}
        if len(members) == 2 and not members <= taken:
            first = order[order["合约"].map(prod) == p]["合约"].iloc[0]
            out[p] = next(c for c in members if c != first)
    return out


def load_skill():
    spec = importlib.util.spec_from_file_location("analyze_trades", SKILL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def pair(day: str, acct: str, skill, leg2: dict) -> dict:
    src = TRADES / f"{day}_账号{acct}.csv"
    norm = PAIRS / f"{day}_账号{acct}_整理.csv"
    normalize(src, day, norm)
    df = pd.read_csv(norm, encoding="utf-8-sig", dtype=str)
    products = {prod(c) for c in df["合约"]}
    use = {p: leg2[p] for p in products if p in leg2}
    cross = cross_leg2(df, use)
    use |= cross
    res = skill.analyze(str(norm), "utf-8-sig", {}, 0, bool(cross), use, False)
    (PAIRS / f"{day}_账号{acct}_配对.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (PAIRS / f"{day}_账号{acct}_配对.md").write_text(skill.render_markdown(res), encoding="utf-8")
    return res


# ---------- 价差账本 ----------

def mult(combo: str):
    """组合的乘数；跨品种两腿乘数不一样时没法按价差算，返回 None。"""
    m = {MULT.get(prod(c)) for c in combo.split("-")}
    return m.pop() if len(m) == 1 else None


def sign(direction: str) -> int:
    """多开（多平）价差往上赚钱，空开（空平）往下赚钱。"""
    return 1 if direction.startswith("多") else -1


def roll(book: list[dict], res: dict, day: str) -> tuple[list[dict], list[dict]]:
    """拿一天的配对结果滚账本：期初仓位平仓按先开先平平掉账本里同组合、同方向的仓，当天没平的记进去。
    返回 (收盘后的账本, 今天平掉的以前的仓)。对不上账本的（账本最早那天以前开的）开仓价差是 None。"""
    book = [dict(b) for b in book]
    closed = []
    for x in res["期初仓位平仓"]:
        want = "空开" if x["平仓方向"] == "空平" else "多开"
        total = left = float(x["手数"])
        base = dict(组合=x["组合"], 方向=want, 平仓时间=x["平仓时间"], 平仓价差=x["平仓点位"])
        share = lambda q: dict(手续费=(num(x["平仓手续费"]) or 0) * q / total,
                               逐笔=None if num(x.get("成交回报盈亏")) is None else num(x["成交回报盈亏"]) * q / total)
        for b in book:
            if left <= EPS:
                break
            if b["组合"] == x["组合"] and b["方向"] == want and b["手数"] > EPS:
                q = min(left, b["手数"])
                b["手数"] -= q
                left -= q
                closed.append(base | share(q) | dict(手数=q, 开仓日=b["开仓日"], 开仓时间=b["开仓时间"], 开仓价差=b["开仓价差"]))
        if left > EPS:
            closed.append(base | share(left) | dict(手数=left, 开仓日=None, 开仓时间="", 开仓价差=None))
    book = [b for b in book if b["手数"] > EPS]
    book += [dict(组合=x["组合"], 方向=x["方向"], 手数=float(x["未平手数"]), 开仓日=day, 开仓时间=x["开仓时间"],
                  开仓价差=x["开仓点位"]) for x in res["未平仓组合"]]
    return book, closed


def book_before(day: str, acct: str) -> tuple[list[dict], str | None]:
    """day 之前所有配对结果滚出来的账本（day 开盘前手上的价差组合），和账本从哪天开始记。
    用存下来的配对.json（当时用的腿2），不重新配对。"""
    book, first = [], None
    for f in sorted(PAIRS.glob(f"*_账号{acct}_配对.json")):
        d = f.name[:10]
        if d >= day:
            break
        first = first or d
        book, _ = roll(book, json.loads(f.read_text(encoding="utf-8")), d)
    return book, first


# ---------- 分钟线 ----------

def symbol(c: str) -> str:
    return f"{EXCHANGE.get(re.match(r'[a-z]+', c)[0], 'SHFE')}.{c}"


def fetch_market(minute: set, daily: set, day: str) -> tuple[dict, dict, str | None]:
    """分钟线（盘中开仓判断用）和每个合约逐日的价（算持仓浮动用）：{合约: {交易日: 价}}，还有 day 的前一个交易日。
    天勤报价里有这一天、前一天的结算价就用结算价（和快期盯市一样），更早的用日线收盘价。"""
    import 开盘tick as tk  # 天勤账号、连接、分钟线都用开盘tick.py 里的
    api = tk.connect()
    try:
        k = {c: tk.minute_klines(api, symbol(c)) for c in sorted(minute)}
        marks, prev = {}, None
        for c in sorted(daily):
            d = api.get_kline_serial(symbol(c), 86400, data_length=60)
            d = d[d["datetime"] > 0].dropna(subset=["close"])
            days = [f"{t:%Y-%m-%d}" for t in pd.to_datetime(d["datetime"].astype("int64"), utc=True).dt.tz_convert("Asia/Shanghai")]
            m = dict(zip(days, d["close"].astype(float)))
            q = api.get_quote(symbol(c))
            if days:
                if num(q.settlement) is not None and days[-1] == day:
                    m[day] = float(q.settlement)
                if num(q.pre_settlement) is not None and len(days) >= 2:
                    m[days[-2]] = float(q.pre_settlement)
            before = [x for x in days if x < day]
            prev = max(prev or "", before[-1]) if before else prev
            marks[c] = m
        return k, marks, prev
    finally:
        api.close()


def spread_at(combo: str, marks: dict, d: str | None):
    if d is None:
        return None
    p = [marks.get(c, {}).get(d) for c in combo.split("-")]
    return None if None in p else round(p[0] - p[1], 6)


def spread_minutes(k1: pd.DataFrame, k2: pd.DataFrame) -> pd.DataFrame:
    """两腿同一分钟都有成交的分钟价差，标上属于哪个交易日（夜盘、夜里算下一个白天）。"""
    j = k1.add_suffix("1").join(k2.add_suffix("2"), how="inner")  # 两边列名一样（close、volume），先各加后缀
    j = j[(j["volume1"] > 0) & (j["volume2"] > 0)]
    s = pd.DataFrame({"价差": (j["close1"] - j["close2"]).round(4)})
    day_session = (s.index.hour >= 9) & (s.index.hour < 16)
    days = sorted(set(s.index[day_session].normalize()))
    base = s.index.normalize() - pd.to_timedelta((s.index.hour < 9).astype(int), unit="D")
    td = []
    for t, b, ds in zip(s.index, base, day_session):
        if ds:
            td.append(t.normalize())
        else:
            nxt = [d for d in days if d > b]
            td.append(nxt[0] if nxt else pd.NaT)
    s["交易日"] = td
    return s


def pct_rank(x: pd.Series, v: float) -> float:
    return float((x < v).mean() * 100 + (x == v).mean() * 50)


# ---------- 判断 ----------

def spread_pnl(combo: str, points):
    """价差盈亏（元）= 带方向的价差点数 × 手数（配对脚本的「今日开平点数」，或自己算的）× 乘数。"""
    m, p = mult(combo), num(points)
    return None if m is None or p is None else round(p * m, 2)


def net(pnl, fee):
    """净盈亏 = 价差盈亏 − 手续费。"""
    p, f = num(pnl), num(fee)
    return None if p is None or f is None else round(p - f, 2)


def opens(res: dict, day: str, acct: str) -> list[dict]:
    out = []
    for x in res["交易明细"]:
        out.append(dict(号=acct, 组合=x["组合"], 方向=x["开仓方向"], 手数=x["手数"], 开仓时间=x["开仓时间"],
                        开仓价差=x["开仓点位"], 平仓时间=x["平仓时间"], 平仓价差=x["平仓点位"], 净盈亏=net(spread_pnl(x["组合"], x.get("今日开平点数")), x["手续费"]),
                        状态="已平"))
    for x in res["未平仓组合"]:
        out.append(dict(号=acct, 组合=x["组合"], 方向=x["方向"], 手数=x["未平手数"], 开仓时间=x["开仓时间"],
                        开仓价差=x["开仓点位"], 平仓时间="", 平仓价差=None, 净盈亏=None, 状态="没平"))
    for o in out:
        o["开始"] = full_time(o["开仓时间"].split("–")[0], day)
        sec = (o["开始"] - o["开始"].normalize()).total_seconds()
        o["开盘"] = next((f"{o['开始']:%Y-%m-%d} {h:02d}:00" for h in (21, 9) if 0 <= sec - h * 3600 < OPEN_SECONDS + 1), None)
    return out


def judge(o: dict, s: pd.DataFrame, day: str) -> dict:
    hist = s[s["交易日"] < pd.Timestamp(day)]
    tdays = sorted(hist["交易日"].dropna().unique())
    recent = hist[hist["交易日"].isin(tdays[-RECENT_DAYS:])]["价差"]
    earlier = hist[hist["交易日"].isin(tdays[-RECENT_DAYS - EARLIER_DAYS:-RECENT_DAYS])]["价差"]
    p, short = o["开仓价差"], o["方向"] == "空开"
    r = dict(天数=len(tdays[-RECENT_DAYS:]), 分钟数=len(recent))
    if recent.empty:
        return r | dict(判断="没有分钟数据")
    lo, mid, hi = recent.quantile(0.1), recent.median(), recent.quantile(0.9)
    rank = pct_rank(recent, p)
    good = rank if short else 100 - rank  # 都换成「越大越合理」
    verdict = "合理" if good >= 70 else "偏中间" if good >= 50 else "不合理"
    trend = ""
    if len(earlier) >= MIN_MINUTES:
        shift = mid - earlier.median()
        if abs(shift) > (hi - lo) / 2:
            trend = f"往{'上' if shift > 0 else '下'}走（前 {len(tdays[-RECENT_DAYS - EARLIER_DAYS:-RECENT_DAYS])} 天中位 {fmt(earlier.median())}）"
            if (shift > 0) == short:
                verdict += "，逆着走"
            else:
                verdict += f"，顺着往{'上' if shift > 0 else '下'}走"
    start = o["开始"].floor("min")
    today = s[s["交易日"] == pd.Timestamp(day)]
    before = today[today.index < start]["价差"]
    after = s[(s.index >= start) & (s.index < start + pd.Timedelta(AFTER_MIN, unit="min"))]["价差"]
    note = "最近 5 天两腿都有成交的分钟少，只当参考" if len(recent) < MIN_MINUTES else ""
    return r | dict(合理价差=mid, 常见区间=f"{fmt(lo)} ~ {fmt(hi)}", 分位=rank, 判断=verdict, 趋势=trend,
                    开仓前=f"{fmt(before.quantile(0.1))} ~ {fmt(before.quantile(0.9))}" if len(before) >= 5 else "",
                    开仓后=f"{fmt(after.quantile(0.1))} ~ {fmt(after.quantile(0.9))}" if len(after) >= 5 else "",
                    备注=note)


# ---------- 网页 ----------

def money(x) -> Raw:
    v = num(x)
    if v is None:
        return Raw("")
    return Raw(f'<span class="{"up" if v > 0 else "down" if v < 0 else ""}">{v:+,.2f}</span>')


def verdict_cell(v: str) -> Raw:
    cls = "up" if v.startswith("合理") else "down" if v.startswith("不合理") else ""
    return Raw(f'<b class="{cls}">{esc(v)}</b>')


def opening_section(rows: list[dict]) -> str:
    if not rows:
        return f"<p>今天没有开盘 {OPEN_SECONDS} 秒内开的仓。</p>"
    rec = pd.read_csv(REC_FILE, encoding="utf-8-sig") if REC_FILE.exists() else pd.DataFrame(columns=["开盘", "组合"])
    out = []
    for o in rows:
        t = rec[(rec["开盘"] == o["开盘"]) & (rec["组合"] == o["组合"])]
        t = t.iloc[0] if len(t) else None
        first = None if t is None else num(t["首笔价差"])
        # 开得比第一次真实成交好多少：空开越高越好，多开越低越好
        better = None if first is None else (o["开仓价差"] - first) * (1 if o["方向"] == "空开" else -1)
        out.append([o["号"], o["组合"], o["方向"], o["手数"], o["开仓时间"], Raw(f"<b>{fmt(o['开仓价差'], 4)}</b>"),
                    o["平仓时间"] or "没平", fmt(o["平仓价差"], 4), money(o["净盈亏"]),
                    "" if t is None else fmt(t["昨收价差"]), "" if t is None else fmt(t["针"]),
                    "" if t is None else fmt(first), money(better) if better is not None else "",
                    "" if t is None else fmt(t["价差5秒"]),
                    "" if t is None else f"{fmt(t['首分钟买价差最低'])} / {fmt(t['首分钟卖价差最高'])}",
                    "没取 tick（不在合约.txt）" if t is None else ""])
    return table(["号", "组合", "方向", "手", "开仓", "开在", "平仓", "平在", "净盈亏（元）",
                  "昨收价差", "针", "第一次真实成交", "比第一次真实成交好", "5 秒", "第一分钟买价差最低 / 卖价差最高", "备注"], out)


def intraday_section(rows: list[dict]) -> str:
    if not rows:
        return "<p>今天没有盘中开仓。</p>"
    out = []
    for o in rows:
        j = o["判断结果"]
        out.append([o["号"], o["组合"], o["方向"], o["手数"], o["开仓时间"], Raw(f"<b>{fmt(o['开仓价差'], 4)}</b>"),
                    fmt(j.get("合理价差")), j.get("常见区间", ""), f"{j['分位']:.0f}%" if "分位" in j else "",
                    verdict_cell(j["判断"]), j.get("趋势", ""), j.get("开仓前", ""), j.get("开仓后", ""),
                    "没平" if o["状态"] == "没平" else f"{o['平仓时间']} 平在 {fmt(o['平仓价差'], 4)}", money(o["净盈亏"]),
                    j.get("备注", "")])
    return table(["号", "组合", "方向", "手", "开仓", "开在", "合理价差（5 天中位）", "常见区间", "分位", "判断", "趋势",
                  "当天开仓前", f"开仓后 {AFTER_MIN} 分钟", "平仓", "净盈亏（元）", "备注"], out)


def add(a, b):
    """两个数相加，有一个不知道就是不知道。"""
    return None if a is None or b is None else a + b


def account_day(acct: str, res: dict, bk: dict, marks: dict, day: str, prev: str | None) -> dict:
    """一个号今天的账：各组合平掉的价差盈亏、收盘后的持仓和浮动、今天大约赚亏。"""
    g, today, notes = {}, 0.0, []
    for x in res["交易明细"]:  # 今天开、今天平
        s = g.setdefault(x["组合"], dict(手数=0.0, 价差=0.0, 费=0.0, 逐笔=0.0))
        p = spread_pnl(x["组合"], x.get("今日开平点数"))
        s["手数"] += x["手数"]; s["价差"] = add(s["价差"], p); s["费"] += num(x["手续费"]) or 0
        s["逐笔"] = add(s["逐笔"], num(x.get("成交回报盈亏")))
        today = add(today, p)
    for x in bk["closed"]:  # 以前开、今天平
        s = g.setdefault(x["组合"], dict(手数=0.0, 价差=0.0, 费=0.0, 逐笔=0.0))
        sg, q = sign(x["方向"]), x["手数"]
        o = num(x["开仓价差"])
        p = None if o is None else spread_pnl(x["组合"], (x["平仓价差"] - o) * sg * q)
        y = spread_at(x["组合"], marks, prev)
        s["手数"] += q; s["价差"] = add(s["价差"], p); s["费"] += x["手续费"]; s["逐笔"] = add(s["逐笔"], x["逐笔"])
        today = add(today, None if y is None else spread_pnl(x["组合"], (x["平仓价差"] - y) * sg * q))
        if o is None:
            notes.append(f"账号{acct}：{x['组合']} {x['方向']} {fmt(q, 0)} 手是账本开始（{bk['first'] or day}）以前开的，不知道开在多少，价差盈亏空着。")
    combos = [[acct, k, fmt(s["手数"], 0), money(s["价差"]), fmt(s["费"]), money(add(s["价差"], -s["费"])),
               money(s["逐笔"]),
               "差得多：有腿平到了别的组合的仓（多半是先平昨仓），以价差为准"
               if s["价差"] is not None and s["逐笔"] is not None and abs(s["价差"] - s["逐笔"]) > 100 else ""]
              for k, s in sorted(g.items())]
    hold, float_sum = [], 0.0
    for b in bk["after"]:
        sg, q, o = sign(b["方向"]), b["手数"], num(b["开仓价差"])
        t = spread_at(b["组合"], marks, day)
        ref = o if b["开仓日"] == day else spread_at(b["组合"], marks, prev)
        fl = None if t is None or o is None else spread_pnl(b["组合"], (t - o) * sg * q)
        float_sum = add(float_sum, fl)
        today = add(today, None if t is None or ref is None else spread_pnl(b["组合"], (t - ref) * sg * q))
        hold.append([acct, b["组合"], b["方向"], fmt(q, 0), b["开仓日"] or "", Raw(f"<b>{fmt(o, 4)}</b>"), fmt(t, 4), money(fl)])
    raw = pd.read_csv(PAIRS / f"{day}_账号{acct}_整理.csv", encoding="utf-8-sig", dtype=str)
    tot = lambda c: pd.to_numeric(raw[c].str.replace(",", ""), errors="coerce").sum()
    fee = float(tot("手续费"))
    return dict(combos=combos, hold=hold, float=float_sum, today=add(today, -fee), fee=fee,
                per_trade=float(tot("平仓盈亏(逐笔)")) - fee, notes=notes)


def summary_section(results: dict, books: dict, marks: dict, day: str, prev: str | None) -> str:
    """盈亏汇总（按价差）、收盘后的持仓（价差账本）、每个号今天大约赚亏。"""
    accs = {a: account_day(a, res, books[a], marks, day, prev) for a, res in results.items()}
    combos = [r for a in accs.values() for r in a["combos"]]
    hold = [r for a in accs.values() for r in a["hold"]]
    days = [[Raw(f"<b>{a}</b>"), Raw(f"<b>{money(x['today'])}</b>"), money(x["float"]), fmt(x["fee"]), money(x["per_trade"])]
            for a, x in accs.items()]
    notes = [n for a in accs.values() for n in a["notes"]]
    notes += [f"⚠ 账号{a}：{len(res['异常'])} 条没配上，见 成交/配对明细/ 里的配对.md。" for a, res in results.items() if res["异常"]]
    return ("<h3>今天大约赚亏</h3>"
            + table(["号", "今天大约赚亏（元）", "手上持仓的浮动盈亏（从开仓算）", "今天手续费", "参考：逐笔整列 − 手续费"], days)
            + f"<p class='note'>今天大约赚亏 = 今天平掉的和手上拿着的价差组合，从昨天（{prev or '无'}）结算价差到今天结算价差（今天开的从开仓价差算起）的变化 × 乘数，"
            "再减今天全部手续费。应该和快期「平仓盈亏 + 持仓盈亏 − 手续费」（盯市）差不多；天勤没拿到结算价的那天用收盘价，会差一点。"
            "逐笔整列只当参考：先平昨仓时，一条腿会平到别的组合的老仓，逐笔会把老仓以前攒的盈亏一次算进今天。</p>"
            + "<h3>平掉的组合（按价差算）</h3>"
            + table(["号", "组合", "平掉的手数", "价差盈亏", "手续费", "净盈亏（元）", "参考：逐笔", "备注"], combos)
            + "<p class='note'>价差盈亏 = (平仓价差 − 开仓价差) × 手数 × 乘数，空开反过来。以前开、今天平的仓，开仓价差从价差账本里找；"
            "手续费是今天交的（以前开的仓，开仓那一半手续费已经算在开仓那天）。</p>"
            + "<h3>收盘后手上的价差组合（价差账本）</h3>"
            + (table(["号", "组合", "方向", "手", "开仓日", "开在", "今天结算价差", "浮动盈亏（从开仓算）"], hold) if hold else "<p>都平了。</p>")
            + "".join(f"<p class='note'>{esc(n)}</p>" for n in notes))


def write_page(day: str, results: dict, all_opens: list[dict], books: dict, marks: dict, prev: str | None) -> Path:
    op = [o for o in all_opens if o["开盘"]]
    intra = [o for o in all_opens if not o["开盘"]]
    body = [f"<h1>成交复盘 {day}</h1><p class='meta'>交易日 {day}（{night_date(day)} 晚上夜盘到 {day} 15:00），"
            f"账号 {'、'.join(results)}。生成于 {datetime.now():%Y-%m-%d %H:%M}。配对明细在 成交/配对明细/。</p>",
            "<p class='note'>价差 = 腿1 − 腿2。空开 = 卖腿1、买腿2，等价差往下；多开 = 买腿1、卖腿2，等价差往上。</p>",
            f"<h2>开盘（开盘后 {OPEN_SECONDS} 秒内开的仓）</h2>", opening_section(op),
            "<p class='note'>昨收价差、针、第一次真实成交、5 秒、第一分钟来自 tick 复盘（复盘数据/开盘复盘.csv）。"
            "「比第一次真实成交好」：空开开得比它高、多开开得比它低为正，单位是价差的点。</p>",
            "<h2>盘中开仓：方向合不合理</h2>", intraday_section(intra),
            f"<p class='note'>合理价差 = 最近 {RECENT_DAYS} 个交易日（不含今天）两腿都有成交的分钟价差的中位数；常见区间 = 中间八成。"
            "分位 = 最近 5 天里比开仓价差低的分钟占几成。空开 ≥70% 合理、50–70% 偏中间、&lt;50% 不合理；多开反过来。"
            "趋势：最近 5 天中位比之前挪动超过常见区间宽度的一半；逆着走时「偏高 / 偏低」可能只是新的正常。"
            "顺着走：价差在往下走时空开（往上走时多开），赌它接着走，这时看的是趋势，位置偏中间也说得通。"
            "当天开仓前、开仓后也是中间八成。只是统计对照，不是买卖建议。</p>",
            "<h2>盈亏汇总</h2>", summary_section(results, books, marks, day, prev),
            "<p class='note'>盈亏按价差算（上面「净盈亏」也是）：交易所按单个合约记账，不知道哪两条腿是一组，逐笔、盯市单看都可能把别的组合的盈亏算进来。"
            "乘数：" + "、".join(f"{k} {v}" for k, v in MULT.items()) + "。按先开先平配对（trade-data-analysis 的配对脚本）。手续费用文件里记的；"
            + ("先后按成交编号（编号小的先成交）。</p>"
               if all("成交编号" in r["数据概况"]["成交顺序"] for r in results.values())
               else "有的导出没有成交编号，同一秒的先后按文件顺序。</p>")]
    PAGES.mkdir(parents=True, exist_ok=True)
    f = PAGES / f"{day}_复盘.html"
    f.write_text(page(f"成交复盘 {day}", "".join(body)), encoding="utf-8")
    return f


def main():
    ap = argparse.ArgumentParser(description="成交复盘")
    ap.add_argument("args", nargs="+", help="交易日（2026-10-22），或导出的成交文件")
    ap.add_argument("--leg2", default="", help="临时指定腿2，覆盖合约.txt：au=au2612,br=br2612")
    a = ap.parse_args()
    if len(a.args) == 1 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.args[0]):
        day = a.args[0]
    else:
        day = import_files(a.args)
    accts = sorted(p.stem.split("_账号")[1] for p in TRADES.glob(f"{day}_账号*.csv"))
    if not accts:
        sys.exit(f"成交/原始记录/ 里没有 {day} 的文件")
    skill, leg2 = load_skill(), leg2_map()
    leg2.update(dict(x.split("=", 1) for x in a.leg2.replace("，", ",").split(",") if "=" in x))
    results = {acct: pair(day, acct, skill, leg2) for acct in accts}
    all_opens = [o for acct, res in results.items() for o in opens(res, day, acct)]
    books = {}
    for acct, res in results.items():
        before, first = book_before(day, acct)
        after, closed = roll(before, res, day)
        books[acct] = dict(before=before, after=after, closed=closed, first=first or day)
        (PAIRS / f"{day}_账号{acct}_账本.json").write_text(json.dumps(after, ensure_ascii=False, indent=1), encoding="utf-8")
    need = {c for o in all_opens if not o["开盘"] for c in o["组合"].split("-")}
    held = {c for b in books.values() for x in b["before"] + b["after"] + b["closed"] for c in x["组合"].split("-")}
    k, marks, prev = fetch_market(need, held, day)
    if need:
        cache = {}
        for o in all_opens:
            if not o["开盘"]:
                a1, a2 = o["组合"].split("-")
                if o["组合"] not in cache:
                    cache[o["组合"]] = spread_minutes(k[a1], k[a2])
                o["判断结果"] = judge(o, cache[o["组合"]], day)
    f = write_page(day, results, all_opens, books, marks, prev)
    for acct, res in results.items():
        print(f"账号{acct}：配对 {len(res['交易明细'])} 笔、没平 {len(res['未平仓组合'])} 笔、没配上 {len(res['异常'])} 条")
    print(f"网页：{f}")


if __name__ == "__main__":
    main()
