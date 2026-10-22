"""取开盘前后的 tick（每 0.5 秒一张快照），用天勤 TqSdk 免费版。

免费版每个合约最多往回取 10000 张快照，活跃合约约 80 分钟，
所以要在开盘后一个多小时内跑：夜盘 21:00 开盘在 22:10 前，日盘 9:00 开盘在 10:10 前。
取到的窗口：开盘前 60 秒（含 20:59 / 8:59 竞价那一张）到开盘后 60 秒。

平时不用手动跑：Windows 任务计划「开盘tick取数」每天 21:03、9:03 自动运行，
那时电脑没开的话，开机登录后会马上补跑。说明见同目录「说明.md」。
跑完会顺带取休市期间的外盘涨跌（同目录「外盘涨跌.py」），存进同一个开盘文件夹，
再把每个开盘文件夹的昨收价存成「收盘价.csv」，最后跑开盘复盘（「开盘复盘.py」）。

账号：读 ~/.tqsdk_auth.json（不在本文件夹，不会上传），格式 {"user": "...", "password": "..."}。

用法（在本文件夹里）：
  python 开盘tick.py                     按「合约.txt」取，存到「数据/开盘日期_时间/」
  python 开盘tick.py SHFE.cu2612,SHFE.cu2701 SHFE.au2612   临时指定合约
  合约写法：交易所.代码，如 SHFE.ag2612、INE.sc2701、DCE.i2701、CZCE.MA701（郑商所三位年月、大写）。
  逗号连起来的是一组价差，价差 = 腿1 − 腿2（先写腿1）。
可选：--seconds 60  开盘后取几秒；--out 目录  默认本文件夹下的「数据」。
"""
import argparse
import json
import site
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIST_FILE = HERE / "合约.txt"
LOG_FILE = HERE / "运行日志.txt"
AUTH_FILE = Path.home() / ".tqsdk_auth.json"

# 任务计划用 pythonw 运行，没有控制台，输出写进运行日志
if sys.stdout is None:
    sys.stdout = sys.stderr = open(LOG_FILE, "a", encoding="utf-8", buffering=1)

# 日记库 venv 里的 python.exe 被 Windows 智能应用控制拦了，用原版 Python 跑，从这里加载装好的包
VENV_SITE = Path.home() / ".venvs" / "vault" / "Lib" / "site-packages"
if VENV_SITE.exists():
    site.addsitedir(str(VENV_SITE))

import pandas as pd  # noqa: E402
from tqsdk import TqApi, TqAuth  # noqa: E402

import 外盘涨跌  # noqa: E402  休市期间的外盘涨跌，跟开盘 tick 一起取
import 开盘复盘  # noqa: E402  开盘那一针记一行、对答案、出记分卡

OPENS = ["21:00:00", "09:00:00"]  # 夜盘、日盘开盘；竞价那张快照在开盘前一分钟内
CLOSE_HEAD = ["合约", "收盘价", "收盘时间", "到期日", "最小变动价位", "合约乘数"]
SESSION_ENDS = {"15:00", "23:00", "01:00", "02:30"}  # 收盘之后才到的那根 1 分钟线，时间就是收盘时刻
COLS = {"last_price": "最新价"}
for _i, _n in enumerate("一二三四五", start=1):  # 五档盘口；上期所、能源中心有五档，大商所、郑商所只有一档、其余为空
    COLS |= {f"bid_price{_i}": f"买{_n}价", f"bid_volume{_i}": f"买{_n}量",
             f"ask_price{_i}": f"卖{_n}价", f"ask_volume{_i}": f"卖{_n}量"}
COLS |= {"成交手数": "成交手数", "均价": "均价", "open_interest": "持仓量", "持仓变化": "持仓变化"}


def load_auth() -> TqAuth:
    if not AUTH_FILE.exists():
        sys.exit(f"找不到账号文件 {AUTH_FILE}")
    a = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    if a.get("user", "").startswith("在这里"):
        sys.exit(f"先用记事本打开 {AUTH_FILE}，把快期账户和密码填进去")
    return TqAuth(a["user"], a["password"])


def connect(tries: int = 5) -> TqApi:
    """刚开机时网络可能还没好，连不上就每分钟重试一次。"""
    for i in range(1, tries + 1):
        try:
            return TqApi(auth=load_auth(), disable_print=True)  # 不打印天勤自带的模拟账户信息
        except Exception as e:
            print(f"第 {i} 次连接天勤失败：{e}")
            if i == tries:
                raise
            time.sleep(60)


def read_list() -> list[str]:
    """合约.txt：一行一组或一个合约，# 后面是注释。"""
    items = []
    for line in LIST_FILE.read_text(encoding="utf-8").splitlines():
        items += line.split("#")[0].split()
    return items


def opening_windows(df: pd.DataFrame, seconds: int) -> list[tuple[pd.Timestamp, pd.DataFrame, bool]]:
    """切出每次开盘的窗口：开盘前 60 秒到开盘后 seconds 秒。
    返回 (开盘时间, 窗口, 是否完整)；序列开头晚于窗口开头，说明往回取不够，窗口前半段缺了。"""
    out = []
    for day in sorted(set(df.index.normalize())):
        for t in OPENS:
            open_ts = pd.Timestamp(f"{day:%Y-%m-%d} {t}")
            start = open_ts - pd.Timedelta(60, unit="s")
            w = df[(df.index >= start) & (df.index <= open_ts + pd.Timedelta(seconds, unit="s"))]
            if (w.index >= open_ts).any():
                out.append((open_ts, w, df.index.min() <= start))
    return out


def save(w: pd.DataFrame, f: Path) -> str:
    """已有的文件行数不比这次少就不覆盖，免得晚跑一次拿到的半截数据盖掉完整的。"""
    if f.exists() and len(pd.read_csv(f, encoding="utf-8-sig")) >= len(w):
        return "已有，跳过"
    f.parent.mkdir(parents=True, exist_ok=True)
    w.to_csv(f, encoding="utf-8-sig", index_label="时间")
    return f"已存（{len(w)} 行）"


def fetch(api: TqApi, symbol: str) -> pd.DataFrame:
    ticks = api.get_tick_serial(symbol, data_length=10000)
    df = ticks.dropna(subset=["datetime"]).copy()
    # datetime 是纳秒时间戳，转成北京时间（不带时区，方便看）
    df.index = pd.DatetimeIndex(pd.to_datetime(df["datetime"].astype("int64"), utc=True)).tz_convert("Asia/Shanghai").tz_localize(None)
    # 天勤给同一时刻的多张快照加了几纳秒区分，对到 0.5 秒格子上，同一格取最后一张，两条腿才对得齐
    df.index = df.index.floor("500ms")
    df = df[~df.index.duplicated(keep="last")].copy()
    d = df["volume"].diff()
    df["成交手数"] = d.mask(d < 0, df["volume"])  # 新交易日成交量从 0 重新累计
    amt = df["amount"].diff()
    amt = amt.mask(d < 0, df["amount"])
    # 这半秒的平均成交价 = 成交额增量 ÷ (成交手数 × 合约乘数)；快照之间的真实成交只能这样看
    mult = api.get_quote(symbol).volume_multiple
    df["均价"] = (amt / (df["成交手数"] * mult)).where(df["成交手数"] > 0).round(2)
    df["持仓变化"] = df["open_interest"].diff()
    return df[list(COLS)].rename(columns=COLS)


def minute_klines(api: TqApi, symbol: str, length: int = 10000) -> pd.DataFrame:
    """1 分钟线（时间是这一分钟开始的时刻，北京时间）。免费版最多 10000 根，活跃合约约两三周。"""
    k = api.get_kline_serial(symbol, 60, data_length=length)
    k = k[k["datetime"] > 0].dropna(subset=["close"]).copy()
    k.index = pd.DatetimeIndex(pd.to_datetime(k["datetime"].astype("int64"), utc=True)).tz_convert("Asia/Shanghai").tz_localize(None)
    return k[["close", "volume"]]


def close_before(k: pd.DataFrame, open_ts, now=None):
    """open_ts 这次开盘之前国内最后一次收盘：(收盘价, 收盘时刻)。
    = 开盘前 60 秒以前的最后一根 1 分钟线（竞价那根不算）；now 是回放用的「现在」，还没走完的分钟线不算。"""
    w = k[k.index < pd.Timestamp(open_ts) - pd.Timedelta(60, unit="s")]
    if now is not None:
        w = w[w.index + pd.Timedelta(60, unit="s") <= pd.Timestamp(now)]
    if not len(w):
        return None
    t = w.index[-1]
    end = t if t.strftime("%H:%M") in SESSION_ENDS else t + pd.Timedelta(60, unit="s")
    return float(w["close"].iloc[-1]), end.to_pydatetime()


def contract_info(api: TqApi, symbol: str) -> dict:
    q = api.get_quote(symbol)
    ex = datetime.fromtimestamp(q.expire_datetime) if q.expire_datetime == q.expire_datetime and q.expire_datetime else None
    return {"到期日": f"{ex:%Y-%m-%d}" if ex else "", "最小变动价位": q.price_tick, "合约乘数": q.volume_multiple}


def save_closes(info: dict, out: Path) -> None:
    """每个开盘文件夹存一份「收盘价.csv」：文件夹里有 tick 的合约，开盘前国内最后一次收盘的价。
    已经记过的合约不再改；1 分钟线够得着的旧文件夹也补上。"""
    for folder in sorted(out.glob("????-??-??_????")):
        open_ts = datetime.strptime(folder.name, "%Y-%m-%d_%H%M")
        f = folder / "收盘价.csv"
        rows = 外盘涨跌.read_rows(f) if f.exists() else []
        have = {r["合约"] for r in rows}
        new = []
        for s, (k, extra) in info.items():
            if s in have or not (folder / f"{s}.csv").exists():
                continue
            c = close_before(k, open_ts)
            if c:
                new.append({"合约": s, "收盘价": c[0], "收盘时间": f"{c[1]:%Y-%m-%d %H:%M}"} | extra)
        if new:
            外盘涨跌.write_rows(f, CLOSE_HEAD, rows + new)
            print(f"收盘价 {folder.name}：补了 {len(new)} 个合约")


def save_ticks(items: list[str], seconds: int, out: Path) -> None:
    # 「腿1,腿2」是一组价差，单独写的是单个合约
    pairs = [tuple(x.split(",")) for x in items if "," in x]
    symbols = list(dict.fromkeys([s for pr in pairs for s in pr] + [x for x in items if "," not in x]))

    api = connect()
    data, info = {}, {}
    try:
        # 先对交易所合约清单：代码写错或已到期时 get_tick_serial 会让整个连接卡住
        valid = set()
        for ex in {s.split(".")[0] for s in symbols}:
            valid |= set(api.query_quotes(ins_class="FUTURE", exchange_id=ex, expired=False))
        for s in symbols:
            if s in valid:
                data[s] = fetch(api, s)
            else:
                print(f"{s}: 交易所没有这个合约（写错了或已到期），跳过。请改「合约.txt」")
        for s in data:  # 昨收价从 1 分钟线取（tick 只能往回 80 分钟，1 分钟线能往回两三周，旧文件夹也补得上）
            try:
                info[s] = (minute_klines(api, s), contract_info(api, s))
            except Exception as e:
                print(f"{s}: 1 分钟线没取到（{type(e).__name__} {e}），这次不存昨收价")
    finally:
        api.close()

    for s, df in data.items():
        wins = opening_windows(df, seconds)
        if not wins:
            print(f"{s}: 往回能取到的这段（{df.index.min():%m-%d %H:%M} 起）里没有开盘")
        for open_ts, w, whole in wins:
            note = "" if whole else "，⚠ 不完整：往回取不够，缺了开盘前一段"
            print(f"{s} {open_ts:%m-%d %H:%M} 开盘：{save(w, out / f'{open_ts:%Y-%m-%d_%H%M}' / f'{s}.csv')}{note}")

    for s1, s2 in pairs:  # 价差：两腿按 0.5 秒对齐
        if s1 not in data or s2 not in data:
            continue
        raw = pd.concat([data[s1].add_prefix("腿1"), data[s2].add_prefix("腿2")], axis=1).sort_index()
        both = raw.ffill()  # 盘口沿用上一张；均价只属于那半秒，不能沿用
        both = pd.DataFrame({
            "腿1": both["腿1最新价"], "腿2": both["腿2最新价"],
            "价差": both["腿1最新价"] - both["腿2最新价"],
            "均价价差": raw["腿1均价"] - raw["腿2均价"],  # 这半秒两腿真实成交均价之差，有一腿没成交就空着
            "买价差": both["腿1卖一价"] - both["腿2买一价"],  # 现在立刻买价差（买腿1、卖腿2）的价
            "卖价差": both["腿1买一价"] - both["腿2卖一价"],  # 现在立刻卖价差（卖腿1、买腿2）的价
        }).round(4)
        start = max(data[s1].index.min(), data[s2].index.min())
        for open_ts, w, _ in opening_windows(both, seconds):
            whole = start <= open_ts - pd.Timedelta(60, unit="s")
            note = "" if whole else "，⚠ 不完整"
            print(f"价差 {s1}-{s2} {open_ts:%m-%d %H:%M}：{save(w, out / f'{open_ts:%Y-%m-%d_%H%M}' / f'价差_{s1}-{s2}.csv')}{note}")

    save_closes(info, out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("symbols", nargs="*")
    p.add_argument("--seconds", type=int, default=60)
    p.add_argument("--out", default=str(HERE / "数据"))
    args = p.parse_args()
    out = Path(args.out)
    print(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 开始 =====")
    try:
        save_ticks(args.symbols or read_list(), args.seconds, out)
    finally:
        外盘涨跌.run(out)  # 只用东方财富（备用雅虎、新浪）的公开数据，跟天勤无关：天勤那边出错也照样取
        开盘复盘.run(out)  # 只读「数据/」里的文件，出错只打印
        print(f"===== {datetime.now():%H:%M:%S} 结束 =====")


if __name__ == "__main__":
    main()
