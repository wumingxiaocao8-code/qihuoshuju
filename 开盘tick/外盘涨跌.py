"""外盘涨跌：国内休市这段时间，对应的外盘涨跌了多少（%）。

时间段（北京时间）：国内收盘 → 开盘前半小时
  夜盘开盘（21:00）：下午 15:00 收盘 → 20:30
  日盘开盘（9:00）：夜盘收盘 → 8:30。夜盘几点收看品种：金、银、原油 02:30，铜铝锌铅锡镍 01:00，燃料油、20号胶、橡胶 23:00。
                   前一晚没有夜盘（如节后第一天），从上一个交易日 15:00 算起。
哪次开盘、开盘前国内几点收的盘，看东方财富沪银主连的分钟线（夜盘收得最晚），不用维护节假日表。

外盘的价 = 那一刻最近一根分钟线的收盘价；外盘那时没在交易（如新加坡 20 号胶傍晚就收了），就是它上一次收盘的价。
外盘用持仓最大的那个月份（伦敦金属用 3 个月期），不用「连续」：连续合约换月那天会把两个月份之间的价差算成涨跌。

数据：东方财富免费分钟线，只能往回取 5 个交易日，所以每天跟开盘 tick 一起取（开盘tick.py 跑完会调用这里）。
东方财富连不上时用备用源（BACKUP），哪一行用了备用源，「数据源」和「备注」里写着：
  雅虎：金、银、布伦特（纽约商交所挂的布伦特，跟伦敦布伦特走势基本一样），能指定月份，也能往回 5 个交易日；
        雅虎没有持仓，用近 5 天成交量最大的月份。
  新浪：伦敦金属、日胶，还有找开盘用的沪银。只有最近 1023 根分钟线（大约一两天），更早的开盘补不了；
        日胶和沪银是连续合约，换月那天日胶的涨跌不准。新加坡 20 号胶没有备用源。
每次开盘在「数据/开盘日期_时间/」存两个文件：
  外盘涨跌.csv    每个品种一行：外盘、起止时间和价格、涨跌幅、这段时间外盘有成交的分钟数
  外盘分钟线.csv  这段时间外盘的分钟线，以后想换时间段重算时用
已经存过的不覆盖（这次取到的更全才覆盖）。

单独运行（在本文件夹里）：python 外盘涨跌.py    把最近 5 个交易日里还没存的开盘都补上
"""
import csv
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# 代码: (品种, 夜盘几点收, 外盘, 东方财富代码)。关系不明显的品种（不锈钢、螺纹、热卷、沥青、丁二烯橡胶、氧化铝等）不加。
# 东方财富代码 = 市场号.合约代码；以 * 结尾的是「市场号.品种前缀」，每次取持仓最大的月份。伦敦金属是 3 个月期，本来就是连续的。
VARIETIES = {
    "au": ("黄金", "02:30", "COMEX黄金", "101.GC*"),
    "ag": ("白银", "02:30", "COMEX白银", "101.SI*"),
    "sc": ("原油", "02:30", "布伦特原油", "112.B*"),
    "fu": ("燃料油", "23:00", "布伦特原油", "112.B*"),
    "cu": ("铜", "01:00", "伦敦铜", "109.LCPT"),
    "al": ("铝", "01:00", "伦敦铝", "109.LALT"),
    "zn": ("锌", "01:00", "伦敦锌", "109.LZNT"),
    "pb": ("铅", "01:00", "伦敦铅", "109.LLDT"),
    "sn": ("锡", "01:00", "伦敦锡", "109.LTNT"),
    "ni": ("镍", "01:00", "伦敦镍", "109.LNKT"),
    "nr": ("20号胶", "23:00", "新加坡20号胶", "104.TF*"),
    "ru": ("橡胶", "23:00", "日胶", "111.JRU*"),
}
SESSION_REF = "113.agm"  # 沪银主连：夜盘收得最晚（02:30），用它找每次开盘和开盘前国内几点收的盘
# 东方财富连不上时的备用源：东方财富代码 → (来源, 代码)。雅虎的代码是品种 + 交易所，月份由 yahoo_contract 挑。
BACKUP = {
    "101.GC*": ("雅虎", "GC.CMX"),
    "101.SI*": ("雅虎", "SI.CMX"),
    "112.B*": ("雅虎", "BZ.NYM"),
    "109.LCPT": ("新浪", "CAD"),
    "109.LALT": ("新浪", "AHD"),
    "109.LZNT": ("新浪", "ZSD"),
    "109.LLDT": ("新浪", "PBD"),
    "109.LTNT": ("新浪", "SND"),
    "109.LNKT": ("新浪", "NID"),
    "111.JRU*": ("新浪", "RSS3"),
}
BACKUP_NOTE = {
    "雅虎": "东方财富没连上，用的雅虎（成交量最大的月份）",
    "新浪": "东方财富没连上，用的新浪（只有最近一两天的分钟线）",
}
SESSION_REF_BACKUP = "AG0"  # 新浪沪银连续
HEAD = ["品种", "外盘", "从（国内收盘）", "到（开盘前半小时）", "起点价", "起点价时间", "终点价", "终点价时间",
        "涨跌幅%", "有成交的分钟数", "备注", "数据源"]
RAW_HEAD = ["外盘", "数据源", "时间", "收", "高", "低", "成交量"]  # 往日分钟线的开盘价东方财富给的是 0，不存
WAYS = ["https", "http", "curl"]  # 东方财富时不时直接断开 Python 的 https 连接（同一时间 curl 却正常），轮着试；上次成功的排最前
SINA_REFERER = "https://finance.sina.com.cn"  # 新浪不带这个会拒绝
MONTH_CODES = "FGHJKMNQUVXZ"  # 期货月份字母：F=1 月 … Z=12 月
# 雅虎从本月往后第几个月开始挑。布伦特 X 月的合约在 X−2 月月底到期，快到期的那个成交量大、持仓却已经移走了，不选
YAHOO_FIRST_MONTH = {"BZ": 3}


class NetError(RuntimeError):
    """几种方式都连不上（不是对方说没有数据）。"""


def fetch(way: str, url: str, referer: str = "") -> str:
    if way == "curl":  # Windows 10 起自带 curl；CREATE_NO_WINDOW：任务计划用 pythonw 跑时不弹黑窗口
        cmd = ["curl", "-sS", "-m", "20", "-A", UA] + (["-e", referer] if referer else []) + [url]
        r = subprocess.run(cmd, capture_output=True, timeout=40,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode:
            raise RuntimeError(f"curl 返回 {r.returncode}")
        return r.stdout.decode("utf-8", errors="replace")
    u = url if way == "https" else url.replace("https://", "http://", 1)
    headers = {"User-Agent": UA, "Accept": "*/*"} | ({"Referer": referer} if referer else {})
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=headers), timeout=20) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:  # 雅虎：没有这个月份的合约，返回的正文里写着，交给调用的地方判断
            return e.read().decode("utf-8", errors="replace")
        raise


def get_text(url: str, referer: str = "", tries: int = 2) -> str:
    errs = []
    for attempt in range(tries):
        for way in list(WAYS):
            try:
                d = fetch(way, url, referer)
                WAYS.remove(way)
                WAYS.insert(0, way)
                return d
            except Exception as e:
                errs.append(f"{way} {type(e).__name__}")
        if attempt + 1 < tries:
            time.sleep(3)
    raise NetError("、".join(errs))


def get_json(url: str, tries: int = 2) -> dict:
    return json.loads(get_text(url, tries=tries))


def main_contract(spec: str) -> str:
    """「101.GC*」→ 持仓最大的月份，如 101.GC26Z；写死代码的原样返回。"""
    if not spec.endswith("*"):
        return spec
    market, prefix = spec[:-1].split(".")
    d = get_json("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=100&po=1&np=1&fltt=2&invt=2"
                 f"&fid=f108&fs=m:{market}&fields=f12,f14,f108")  # 按持仓从大到小排
    for x in (d.get("data") or {}).get("diff") or []:
        code = x["f12"]
        if code.startswith(prefix) and code[len(prefix):len(prefix) + 2].isdigit():  # GC26Z 是，GC00Y、MGC26Z 不是
            return f"{market}.{code}"
    raise RuntimeError(f"{spec} 找不到持仓最大的月份")


def minute_bars(secid: str) -> tuple[str, list[tuple]]:
    """最近 5 个交易日的分钟线：(名称, [(时间, 开, 收, 高, 低, 成交量)])。
    时间是北京时间、这一分钟结束的时刻（9:01 那根是 9:00–9:01）。"""
    d = get_json("https://push2his.eastmoney.com/api/qt/stock/trends2/get?secid=" + secid +
                 "&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
                 "&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=5")
    data = d.get("data") or {}
    bars = []
    for t in data.get("trends") or []:
        f = t.split(",")
        bars.append((datetime.strptime(f[0], "%Y-%m-%d %H:%M"), *(float(x) for x in f[1:6])))
    if not bars:
        raise RuntimeError(f"{secid} 没有分钟线")
    return data.get("name") or secid, bars


def sina_bars(symbol: str, domestic: bool = False) -> list[tuple]:
    """新浪最近 1023 根分钟线，格式同 minute_bars。时间也是这一分钟结束的时刻（02:30 是夜盘最后一根）。
    domestic=True 是国内期货（AG0 = 沪银连续），否则是外盘（CAD = 伦敦铜）。"""
    url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20t=/InnerFuturesNewService.getFewMinLine"
           if domestic else "https://gu.sina.cn/ft/api/jsonp.php/var%20t=/GlobalService.getMink")
    t = get_text(f"{url}?symbol={symbol}&type=1", SINA_REFERER)
    m = re.search(r"\((\[.*\])\)", t, re.S)
    if not m:
        raise RuntimeError(f"新浪 {symbol} 没有分钟线")
    bars = [(datetime.strptime(x["d"], "%Y-%m-%d %H:%M:%S"), *(float(x[k]) for k in "ochlv"))
            for x in json.loads(m.group(1))]
    return [b for b in bars if b[5] > 0] if domestic else bars  # 国内周末会冒出成交量 0 的空 K 线，会被当成一次开盘


def yahoo_chart(symbol: str, interval: str) -> dict | None:
    t = get_text(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range=5d", tries=1)
    return ((json.loads(t).get("chart") or {}).get("result") or [None])[0]


def yahoo_contract(code: str) -> tuple[str, str]:
    """「GC.CMX」→ 近 5 天成交量最大的月份：(雅虎代码 GCZ26.CMX, 月份 2612)。雅虎没有持仓数，用成交量代替。"""
    root, exchange = code.split(".")
    now, best = datetime.now(), (0, "", "")
    first = YAHOO_FIRST_MONTH.get(root, 0)
    for i in range(first, first + 7):  # 往后 7 个月里挑
        y, m = now.year + (now.month - 1 + i) // 12, (now.month - 1 + i) % 12 + 1
        symbol = f"{root}{MONTH_CODES[m - 1]}{y % 100:02d}.{exchange}"
        try:
            d = yahoo_chart(symbol, "1d")
        except Exception:
            continue
        vol = sum(v or 0 for v in (d or {}).get("indicators", {}).get("quote", [{}])[0].get("volume") or [])
        if vol > best[0]:
            best = (vol, symbol, f"{y % 100:02d}{m:02d}")
        time.sleep(0.3)
    if not best[1]:
        raise RuntimeError(f"雅虎 {code} 找不到有成交的月份")
    return best[1], best[2]


def yahoo_bars(symbol: str) -> list[tuple]:
    """雅虎最近 5 个交易日的分钟线，格式同 minute_bars。雅虎标的是这一分钟开始的时刻，加 1 分钟跟东方财富对齐。"""
    d = yahoo_chart(symbol, "1m")
    if not d:
        raise RuntimeError(f"雅虎 {symbol} 没有分钟线")
    q, bj = d["indicators"]["quote"][0], timezone(timedelta(hours=8))
    r = lambda x: round(x, 3) if x is not None else 0.0  # 雅虎的价是单精度，4158.4 会变成 4158.39990234375
    bars = [(datetime.fromtimestamp(t, bj).replace(tzinfo=None) + timedelta(minutes=1), r(o), r(c), r(h), r(lo), v or 0)
            for t, o, c, h, lo, v in zip(d["timestamp"], q["open"], q["close"], q["high"], q["low"], q["volume"])
            if c is not None]  # 休市那段雅虎给的是空值
    if not bars:
        raise RuntimeError(f"雅虎 {symbol} 没有分钟线")
    return bars


def backup_market(spec: str, label: str) -> tuple:
    """用备用源取一个外盘：(数据源, 外盘名称, 分钟线, 时间列表, 备注)。"""
    if spec not in BACKUP:
        raise RuntimeError("没有备用源")
    source, code = BACKUP[spec]
    if source == "雅虎":
        symbol, month = yahoo_contract(code)
        bars, name = yahoo_bars(symbol), label + month
    else:
        symbol, bars = code, sina_bars(code)
        name = label + ("（连续）" if spec.endswith("*") else "")
    return f"{source} {symbol}", name, bars, [x[0] for x in bars], BACKUP_NOTE[source]


def openings(bars: list[tuple]) -> list[tuple[datetime, datetime]]:
    """从国内分钟线找每次开盘：[(开盘时间, 开盘前最后一根分钟线的时间，也就是上一次收盘)]。
    收盘处分钟线会断开，下一根在 21:00 前后（夜盘）或 9:01 前后（日盘）；10:15、11:30 的盘中休息不算。"""
    out = []
    for prev, cur in zip(bars, bars[1:]):
        p, c = prev[0], cur[0]
        if c - p < timedelta(hours=1):
            continue
        for hour in (21, 9):
            o = c.replace(hour=hour, minute=0)
            if o - timedelta(minutes=5) <= c <= o + timedelta(minutes=1):
                out.append((o, p))
    return out


def close_time(night_close: str, last: datetime) -> datetime:
    """这个品种开盘前国内收盘的时刻。last = 沪银主连开盘前最后一根分钟线（夜盘收得最晚的那个品种的收盘）。"""
    if 3 < last.hour < 21:  # 开盘前国内最后收的是下午 15:00 那次：夜盘开盘，或者前一晚没有夜盘
        return last
    night = last.date() if last.hour >= 21 else last.date() - timedelta(days=1)  # 夜盘是哪天晚上开始的
    h, m = map(int, night_close.split(":"))
    t = datetime(night.year, night.month, night.day, h, m) + timedelta(days=1 if h < 12 else 0)  # 过了半夜才收的算第二天
    return min(t, last)


def price_at(bars: list[tuple], times: list[datetime], t: datetime):
    """t 那一刻最近的一根分钟线（时间 ≤ t）；数据不够早就返回 None。"""
    i = bisect_right(times, t)
    return bars[i - 1] if i else None


def compute(o: datetime, last: datetime, markets: dict) -> tuple[list[dict], list[list]]:
    """一次开盘：每个品种一行涨跌，加上用到的外盘分钟线。"""
    end = o - timedelta(minutes=30)
    rows, used = [], {}  # used: 数据源 → 最早用到的那根分钟线时间
    for code, (name, night_close, ext, spec) in VARIETIES.items():
        start = close_time(night_close, last)
        row = {"品种": f"{name} {code}", "外盘": ext, "从（国内收盘）": f"{start:%m-%d %H:%M}",
               "到（开盘前半小时）": f"{end:%m-%d %H:%M}"}
        m = markets.get(spec)
        if isinstance(m, str):
            row["备注"] = f"外盘没取到：{m}"
        else:
            src, label, bars, times, note = m
            row["外盘"], row["数据源"] = label, src
            notes = [note] if note else []
            a, b = price_at(bars, times, start), price_at(bars, times, end)
            if a is None:
                notes.append("外盘数据不够早" + ("" if note else "（只能往回取 5 个交易日）"))
            elif not (a[2] and b[2]):
                notes.append("外盘价格是 0，数据有问题")
            else:
                row |= {"起点价": a[2], "起点价时间": f"{a[0]:%m-%d %H:%M}", "终点价": b[2],
                        "终点价时间": f"{b[0]:%m-%d %H:%M}", "涨跌幅%": round((b[2] / a[2] - 1) * 100, 2),
                        "有成交的分钟数": sum(1 for x in bars if start < x[0] <= end and x[5] > 0)}
                if a[0] == b[0]:
                    notes.append("这段时间外盘没交易")
                used[src] = min(used.get(src, a[0]), a[0])
            row["备注"] = "；".join(notes)
        rows.append(row)
    raw = []
    for spec, m in markets.items():
        if isinstance(m, str) or m[0] not in used:
            continue
        src, label, bars, _, _ = m
        raw += [[label, src, f"{x[0]:%Y-%m-%d %H:%M}", *x[2:]] for x in bars if used[src] <= x[0] <= o]
    return rows, raw


def read_rows(f: Path) -> list[dict]:
    with open(f, encoding="utf-8-sig", newline="") as fp:
        return list(csv.DictReader(fp))


def write_rows(f: Path, head: list[str], rows: list) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(head)
        w.writerows([[r.get(k, "") for k in head] if isinstance(r, dict) else r for r in rows])


def run(out: Path = HERE / "数据") -> None:
    """把最近 5 个交易日里还没存（或存得不全）的开盘都算一遍。出错只打印，不抛出，免得影响开盘 tick。"""
    try:
        em_ok = True  # 东方财富一次连不上，这一轮剩下的就直接用备用源，不再一个个等它断开
        try:
            _, ref = minute_bars(SESSION_REF)
        except Exception as e:
            print(f"东方财富没连上（{e}），改用备用源")
            em_ok, ref = False, sina_bars(SESSION_REF_BACKUP, domestic=True)
        markets = {}
        for spec in dict.fromkeys(v[3] for v in VARIETIES.values()):
            label = VARIETIES[next(k for k, v in VARIETIES.items() if v[3] == spec)][2]
            err = "东方财富没连上"
            if em_ok:
                try:
                    secid = main_contract(spec)
                    name, bars = minute_bars(secid)
                    month = re.search(r"\d{4}$", name)
                    markets[spec] = (f"东方财富 {secid}", label + (month.group() if month else ""), bars,
                                     [x[0] for x in bars], "")
                    continue
                except Exception as e:
                    err = f"东方财富 {type(e).__name__} {e}"
                    em_ok = not isinstance(e, NetError)  # 只是这个品种没数据（不是连不上）就继续用东方财富
                finally:
                    time.sleep(0.5)
            try:
                markets[spec] = backup_market(spec, label)
                print(f"外盘 {spec}：{err}，用了 {markets[spec][0]}")
            except Exception as e:
                markets[spec] = f"{err}；备用源 {type(e).__name__} {e}"
                print(f"外盘 {spec} 没取到：{markets[spec]}")
        saved = 0
        for o, last in openings(ref):
            rows, raw = compute(o, last, markets)
            n = sum("涨跌幅%" in r for r in rows)
            n_backup = sum(not r.get("数据源", "东方财富").startswith("东方财富") for r in rows)
            folder = out / f"{o:%Y-%m-%d_%H%M}"
            f = folder / "外盘涨跌.csv"
            if f.exists():
                old = read_rows(f)
                old_n = sum(bool(r.get("涨跌幅%")) for r in old)
                old_backup = sum(not (r.get("数据源") or "东方财富").startswith("东方财富") for r in old)
                if old_n > n or (old_n == n and old_backup <= n_backup):  # 更全，或者一样全但少用了备用源，才覆盖
                    continue
            write_rows(f, HEAD, rows)
            g = folder / "外盘分钟线.csv"
            old_raw = read_rows(g) if g.exists() else []
            if len(old_raw) < len(raw) or {r.get("数据源") for r in old_raw} != {x[1] for x in raw}:
                write_rows(g, RAW_HEAD, raw)
            saved += 1
            print(f"外盘 {o:%m-%d %H:%M} 开盘：已存（{len(rows)} 个品种，{n} 个有涨跌幅）")
        if not saved:
            print("外盘：最近 5 个交易日的开盘都已存过")
    except Exception as e:
        print(f"外盘涨跌出错：{type(e).__name__} {e}")


if __name__ == "__main__":
    run()
