"""外盘涨跌：国内休市这段时间，对应的外盘涨跌了多少（%）。

时间段（北京时间）：国内收盘 → 开盘前半小时
  夜盘开盘（21:00）：下午 15:00 收盘 → 20:30
  日盘开盘（9:00）：夜盘收盘 → 8:30。夜盘几点收看品种：金、银、原油 02:30，铜铝锌铅锡镍 01:00，燃料油、20号胶、橡胶 23:00。
                   前一晚没有夜盘（如节后第一天），从上一个交易日 15:00 算起。
哪次开盘、开盘前国内几点收的盘，看东方财富沪银主连的分钟线（夜盘收得最晚），不用维护节假日表。

外盘的价 = 那一刻最近一根分钟线的收盘价；外盘那时没在交易（如新加坡 20 号胶傍晚就收了），就是它上一次收盘的价。
外盘用持仓最大的那个月份（伦敦金属用 3 个月期），不用「连续」：连续合约换月那天会把两个月份之间的价差算成涨跌。

数据：东方财富免费分钟线，只能往回取 5 个交易日，所以每天跟开盘 tick 一起取（开盘tick.py 跑完会调用这里）。
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
import urllib.request
from bisect import bisect_right
from datetime import datetime, timedelta
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
HEAD = ["品种", "外盘", "从（国内收盘）", "到（开盘前半小时）", "起点价", "起点价时间", "终点价", "终点价时间",
        "涨跌幅%", "有成交的分钟数", "备注", "东方财富代码"]
RAW_HEAD = ["外盘", "东方财富代码", "时间", "收", "高", "低", "成交量"]  # 往日分钟线的开盘价东方财富给的是 0，不存
WAYS = ["https", "http", "curl"]  # 东方财富时不时直接断开 Python 的 https 连接（同一时间 curl 却正常），轮着试；上次成功的排最前


def fetch(way: str, url: str) -> dict:
    if way == "curl":  # Windows 10 起自带 curl；CREATE_NO_WINDOW：任务计划用 pythonw 跑时不弹黑窗口
        r = subprocess.run(["curl", "-sS", "-m", "20", "-A", UA, url], capture_output=True, timeout=40,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode:
            raise RuntimeError(f"curl 返回 {r.returncode}")
        return json.loads(r.stdout.decode("utf-8"))
    u = url if way == "https" else url.replace("https://", "http://", 1)
    req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def get_json(url: str) -> dict:
    errs = []
    for attempt in range(2):
        for way in list(WAYS):
            try:
                d = fetch(way, url)
                WAYS.remove(way)
                WAYS.insert(0, way)
                return d
            except Exception as e:
                errs.append(f"{way} {type(e).__name__}")
        time.sleep(3)
    raise RuntimeError("、".join(errs))


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
    rows, used = [], {}  # used: 外盘代码 → 最早用到的那根分钟线时间
    for code, (name, night_close, ext, spec) in VARIETIES.items():
        start = close_time(night_close, last)
        row = {"品种": f"{name} {code}", "外盘": ext, "从（国内收盘）": f"{start:%m-%d %H:%M}",
               "到（开盘前半小时）": f"{end:%m-%d %H:%M}"}
        m = markets.get(spec)
        if isinstance(m, str):
            row["备注"] = f"外盘没取到：{m}"
        else:
            secid, label, bars, times = m
            row["外盘"], row["东方财富代码"] = label, secid
            a, b = price_at(bars, times, start), price_at(bars, times, end)
            if a is None:
                row["备注"] = "外盘数据不够早（只能往回取 5 个交易日）"
            elif not (a[2] and b[2]):
                row["备注"] = "外盘价格是 0，数据有问题"
            else:
                row |= {"起点价": a[2], "起点价时间": f"{a[0]:%m-%d %H:%M}", "终点价": b[2],
                        "终点价时间": f"{b[0]:%m-%d %H:%M}", "涨跌幅%": round((b[2] / a[2] - 1) * 100, 2),
                        "有成交的分钟数": sum(1 for x in bars if start < x[0] <= end and x[5] > 0)}
                if a[0] == b[0]:
                    row["备注"] = "这段时间外盘没交易"
                used[secid] = min(used.get(secid, a[0]), a[0])
        rows.append(row)
    raw = []
    for spec, m in markets.items():
        if isinstance(m, str) or m[0] not in used:
            continue
        secid, label, bars, _ = m
        raw += [[label, secid, f"{x[0]:%Y-%m-%d %H:%M}", *x[2:]] for x in bars if used[secid] <= x[0] <= o]
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
        _, ref = minute_bars(SESSION_REF)
        markets = {}
        for spec in dict.fromkeys(v[3] for v in VARIETIES.values()):
            try:
                secid = main_contract(spec)
                name, bars = minute_bars(secid)
                label = VARIETIES[next(k for k, v in VARIETIES.items() if v[3] == spec)][2]
                month = re.search(r"\d{4}$", name)
                markets[spec] = (secid, label + (month.group() if month else ""), bars, [x[0] for x in bars])
            except Exception as e:
                markets[spec] = f"{type(e).__name__} {e}"
                print(f"外盘 {spec} 没取到：{markets[spec]}")
            time.sleep(0.5)
        saved = 0
        for o, last in openings(ref):
            rows, raw = compute(o, last, markets)
            n = sum("涨跌幅%" in r for r in rows)
            folder = out / f"{o:%Y-%m-%d_%H%M}"
            f = folder / "外盘涨跌.csv"
            if f.exists() and sum(bool(r.get("涨跌幅%")) for r in read_rows(f)) >= n:
                continue
            write_rows(f, HEAD, rows)
            g = folder / "外盘分钟线.csv"
            if not g.exists() or len(read_rows(g)) < len(raw):
                write_rows(g, RAW_HEAD, raw)
            saved += 1
            print(f"外盘 {o:%m-%d %H:%M} 开盘：已存（{len(rows)} 个品种，{n} 个有涨跌幅）")
        if not saved:
            print("外盘：最近 5 个交易日的开盘都已存过")
    except Exception as e:
        print(f"外盘涨跌出错：{type(e).__name__} {e}")


if __name__ == "__main__":
    run()
