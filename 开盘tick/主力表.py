"""主力 = 成交量最大的合约（11-06 小草定的，原来按持仓最大是错的）。

开盘那一刻当天成交量还没出来，所以用「开盘前最近一个已收盘交易日」的全天成交量（含夜盘）：
  夜盘 D 日 21:00 开盘 → 用 D 日的成交量；日盘 D 日 9:00 开盘 → 用 D 日之前最近一个交易日的。
数据来自 期货数据/output/缓存/历史/kx_YYYYMMDD.dat.gz（上期所每日行情，含上期能源品种，futures_brief.py 下载的，2023-10 起）。
已进交割月的合约不算（跟原来一样）。成交量一样时按持仓大的，再一样按月份早的。

用法：
  import 主力表
  主力表.main_month("sc", pd.Timestamp("2026-11-05 21:00"))  → "2612"（交割月 YYMM），没数据返回 None
  主力表.ranked("sc", 开盘时间) → [(交割月, 成交量, 持仓), …] 按成交量从大到小
"""
import datetime as dt
import gzip
import json
import os
import re
import urllib.error
import urllib.request
from bisect import bisect_left
from functools import lru_cache
from pathlib import Path

KX_DIR = Path(__file__).resolve().parent.parent / "output" / "缓存" / "历史"
SHFE_KX_URL = "https://www.shfe.com.cn/data/tradedata/future/dailydata/kx{date}.dat"  # 跟 futures_brief.py 同一个地址、同一种存法
SHFE_PAGE = "https://www.shfe.com.cn/reports/tradedata/dailyandweeklydata/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


@lru_cache(maxsize=1)
def _days() -> list[str]:
    return sorted(p.name[3:11] for p in KX_DIR.glob("kx_*.dat.gz"))


@lru_cache(maxsize=None)
def _raw(ds: str) -> dict[str, dict[str, tuple[float, float]]]:
    """{品种: {交割月: (成交量, 持仓)}}，只要期货，所有挂牌合约都在（包括已进交割月、还在交易的）。
    换月看的是看板上那两条腿自己的量，要用这个（11-07 审查：原来跟挑主力共用 _day，交割月的 cu2611 拿不到量）。"""
    try:
        d = json.loads(gzip.open(KX_DIR / f"kx_{ds}.dat.gz").read())
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, tuple[float, float]]] = {}
    for x in d.get("o_curinstrument") or []:
        c, m = str(x.get("PRODUCTGROUPID", "")).strip().lower(), str(x.get("DELIVERYMONTH", "")).strip()
        if not c or not m.isdigit() or str(x.get("PRODUCTCLASS")) != "1":
            continue
        try:
            out.setdefault(c, {})[m] = (float(x.get("VOLUME") or 0), float(x.get("OPENINTEREST") or 0))
        except ValueError:
            continue
    return out


@lru_cache(maxsize=None)
def _day(ds: str) -> dict[str, dict[str, tuple[float, float]]]:
    """挑主力用：跟 _raw 一样，但去掉已进交割月的（交割月 <= 数据日期的月份）。"""
    return {c: {m: v for m, v in q.items() if m > ds[2:6]} for c, q in _raw(ds).items()}


HOLIDAY_URL = "https://files.shinnytech.com/shinny_chinese_holiday.json"  # 天勤自己用的法定节假日表（工作日里不开盘的日子）
HOLIDAY_FILE = KX_DIR / "休市日.json"


@lru_cache(maxsize=None)
def _holidays(year: int) -> frozenset | None:
    """法定节假日（YYYYMMDD）。先读本地 休市日.json，没有或不含这一年就从天勤下一次存起来；都不行返回 None（不知道）。
    11-07 版用「缓存里有更晚的日子」推断休市，分不清休市和漏下（11-08 审查），改用这张表。"""
    def load(raw):
        days = json.loads(raw)
        return frozenset(d.replace("-", "") for d in days), int(days[-1][:4])
    try:
        got = load(HOLIDAY_FILE.read_text(encoding="utf-8"))
        if got[1] >= year:
            return got[0]
    except (OSError, ValueError, IndexError):
        pass
    try:
        with urllib.request.urlopen(urllib.request.Request(HOLIDAY_URL, headers={"User-Agent": UA}), timeout=5) as r:
            raw = r.read().decode("utf-8")
        got = load(raw)
        KX_DIR.mkdir(parents=True, exist_ok=True)
        HOLIDAY_FILE.write_text(raw, encoding="utf-8")
        return got[0] if got[1] >= year else None
    except Exception:
        return None


STATUS: dict[str, str] = {}  # 这次运行里 fetch 过的日子：{YYYYMMDD: "已有" / "新下" / "休市" / "还没结算价" / "下载失败（…）"}


def fetch_status(ds: str, timeout: float = 8) -> str:
    """下载一天的上期所每日行情存成 kx_YYYYMMDD.dat.gz，返回结果（也记进 STATUS）：
    已有 / 新下 / 休市（交易所回 404）/ 还没结算价（收盘前取到的，不存）/ 下载失败（网络不通、超时等，不存）。"""
    path = KX_DIR / f"kx_{ds}.dat.gz"
    if path.exists():
        st = "已有"
    else:
        req = urllib.request.Request(SHFE_KX_URL.format(date=ds), headers={"User-Agent": UA, "Referer": SHFE_PAGE})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            text, st = "", "休市" if e.code == 404 else f"下载失败（HTTP {e.code}）"
        except Exception as e:
            text, st = "", f"下载失败（{type(e).__name__}）"
        else:
            if "o_curinstrument" not in text or not re.search(r'"SETTLEMENTPRICE":\d', text):
                st = "还没结算价"
            else:
                KX_DIR.mkdir(parents=True, exist_ok=True)
                path.write_bytes(gzip.compress(text.encode("utf-8")))
                st = "新下"
    STATUS[ds] = st
    return st


def fetch(ds: str, timeout: float = 8) -> bool:
    """老接口：存下了（或本来就有）返回 True。"""
    return fetch_status(ds, timeout) in ("已有", "新下")


def _start(when) -> dt.date:
    """开盘时间 → 从哪天往前找：夜盘（≥ 16 点）从当天，日盘从前一天。"""
    d = when.date() if hasattr(when, "date") else when
    return d if getattr(when, "hour", 0) >= 16 else d - dt.timedelta(days=1)


def ensure(when, back: int = 14) -> dict[str, str]:
    """开盘前把要用的每日行情补齐（11-07 加：同事版没有 17:30 仓储取数，竞价判断开头在后台调它）：
    夜盘要当天的、日盘要前一交易日的，再往前几天给「换月」比 5 日均量。不知道交易日历，就把 back 天里的工作日都试一遍，
    已有的不下，节假日交易所回 404 也不存。返回这次试过的 {日期: 结果}（不含本来就有的）。"""
    start, res = _start(when), {}
    for i in range(back):
        x = start - dt.timedelta(days=i)
        ds = f"{x:%Y%m%d}"
        if x.weekday() < 5 and not (KX_DIR / f"kx_{ds}.dat.gz").exists():
            res[ds] = fetch_status(ds)
    if any(v == "新下" for v in res.values()):
        _days.cache_clear()
        _raw.cache_clear()
        _day.cache_clear()
    return res


def ref_day(when, back: int = 14) -> tuple[str | None, str]:
    """这次开盘「应该」用哪天的每日行情：(YYYYMMDD, "")；该用的那天没有就是 (None, 为什么)，不拿更早的日子冒充（11-07 审查）。
    从 _start 往前一个个工作日看：缓存里有 → 就是它；没有但确定休市（交易所回过 404，或在法定节假日表里）→ 再往前；
    别的情况（下载失败、漏下、还没结算价、后台还没下完、节假日表取不到）→ 不知道。"""
    start = _start(when)
    for i in range(back):
        x = start - dt.timedelta(days=i)
        ds = f"{x:%Y%m%d}"
        if x.weekday() >= 5:
            continue
        if (KX_DIR / f"kx_{ds}.dat.gz").exists():
            return ds, ""
        st = STATUS.get(ds)
        if st == "休市" or ds in (_holidays(x.year) or ()):
            continue
        return None, f"{ds} 的每日行情{st or '还没下到'}"
    return None, f"往前 {back} 天都没有每日行情"


def prev_day(when) -> str | None:
    """开盘时间 → 用哪一天的成交量（YYYYMMDD）：夜盘（≥ 16 点）用当天，日盘用前一个交易日。"""
    d = when.date() if hasattr(when, "date") else when
    key = f"{d:%Y%m%d}"
    days = _days()
    i = bisect_left(days, key)  # days[i] >= key
    if getattr(when, "hour", 0) >= 16 and i < len(days) and days[i] == key:
        return key
    return days[i - 1] if i > 0 else None


def ranked(code: str, when) -> list[tuple[str, float, float]]:
    ds = prev_day(when)
    q = _day(ds).get(code.lower(), {}) if ds else {}
    return sorted(((m, v, oi) for m, (v, oi) in q.items() if v > 0), key=lambda r: (-r[1], -r[2], r[0]))


def main_month(code: str, when) -> str | None:
    r = ranked(code, when)
    return r[0][0] if r else None


OLD_OI = os.environ.get("ZHULI_BY") == "oi"  # 对照用：ZHULI_BY=oi 时退回老办法（持仓最大），只给回放 A/B 对比
MISSES: list = []  # 成交量主力不在候选合约里、退回持仓最大的 (品种, 开盘, 成交量主力)


def pick(code: str, when, syms, fallback):
    """候选合约（如 L2 的 'fu2701'、天勤的 'SHFE.fu2701'）里挑成交量主力；找不到（没数据 / 那个合约没 tick）退回 max(syms, key=fallback) 并记进 MISSES。"""
    if OLD_OI:
        return max(syms, key=fallback)
    m = main_month(code, when)
    hit = [s for s in syms if str(s)[-4:] == m]
    if hit:
        return hit[0]
    MISSES.append((code, str(when), m))
    return max(syms, key=fallback)


def pick_after(code: str, when, syms, fallback=None):
    """候选合约里成交量最大的那个（用来挑「主力后面成交量最大」的次主力）；都没成交量数据时返回 None。"""
    if OLD_OI and fallback:
        return max(syms, key=fallback)
    vol = {m: v for m, v, _ in ranked(code, when)}
    have = [s for s in syms if str(s)[-4:] in vol]
    return max(have, key=lambda s: vol[str(s)[-4:]]) if have else None


if __name__ == "__main__":
    import sys
    now = dt.datetime.now()
    for c in sys.argv[1:] or ["sc", "cu"]:
        print(c, prev_day(now), ranked(c, now)[:3])
