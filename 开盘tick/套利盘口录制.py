"""录上期所、上期能源套利指令（标准套利合约，如 SHFE.SP ag2612&ag2702）和相关单腿的五档盘口。只读行情，不下单。
历史 L2 里没有套利盘口，只能从现在起自己攒，用来看开盘第一秒套利盘口有多少量、哪些组合被大量挂单钉住（测试/2026-11-09_方向粗看/下一步研究.md 任务 A）。

全天录（11-09 晚他定的，11-10 早上起）：白天 8:58–15:01、夜盘 20:58–次日 2:31，写进 数据/套利盘口/套利盘口_开盘日期_时间_全天.csv（11-09 晚那份是只录开盘的，文件名带「开盘」）。
看的时候注意：「和上一行比变了才记」，第一张是全部合约，没记的时刻照上一行算。
合约：全部套利合约 + 它们的两条腿 + 看板.txt、合约.txt 里的单腿（fu、bu、hc 没有套利合约，单腿照录）。

平时不用手动跑：Windows 任务计划「套利盘口录制」每天 20:56、8:56 启动，录到 2:31、15:01 自己退出。
用法（在本文件夹里）：
  python 套利盘口录制.py                录当前这一节（开盘前 30 分钟到这一节录完之间才录，接着原文件写；这次没开盘就退出）
  python 套利盘口录制.py --短跑 40       休市时试跑：不看交易日历，写进 --out（默认 测试/2026-11-09_套利盘口录制）
运行情况写在「数据/套利盘口/套利盘口_运行日志.txt」。进程优先级调低了，不和竞价判断抢 CPU。
"""
import argparse
import csv
import ctypes
import re
import sys
import time
from ctypes import wintypes
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "数据" / "套利盘口"
OUT.mkdir(parents=True, exist_ok=True)
LOG_FILE = OUT / "套利盘口_运行日志.txt"
if sys.stdout is None:  # 任务计划用 pythonw 运行，没有控制台，输出写进运行日志
    sys.stdout = sys.stderr = open(LOG_FILE, "a", encoding="utf-8", buffering=1)

import 开盘预估 as pre  # noqa: E402  有没有这次开盘（节假日、节前一晚）
from 套利盘口查询 import EXCHANGES, TqApi, list_combos, load_auth, num  # noqa: E402

TEST_DIR = HERE.parent / "测试" / "2026-11-09_套利盘口录制"
LEVELS = list(enumerate("一二三四五", start=1))
HEAD = ["本机时间", "合约", "行情时间", "最新价", "成交量", "持仓量"] + [
    f"{side}{n}{what}" for _, n in LEVELS for side in "买卖" for what in "价量"]
PLAIN = re.compile(r"\b(?:SHFE|INE)\.[a-z]+\d{4}\b")


def lower_priority() -> None:
    k = ctypes.windll.kernel32
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL


def session(now: datetime) -> tuple[datetime, datetime] | None:
    """现在在哪一节的录制时段里：开盘前 30 分钟到这一节录完，返回 (开盘, 录到)，不在就是 None。夜盘按最晚收盘的品种（2:30）。"""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for day in (today - timedelta(days=1), today):
        for o, end in ((day + timedelta(hours=9), day + timedelta(hours=15, minutes=1)),
                       (day + timedelta(hours=21), day + timedelta(days=1, hours=2, minutes=31))):
            if o - timedelta(minutes=30) <= now < end:
                return o, end
    return None


def board_legs() -> set:
    out = set()
    for f in ("看板.txt", "合约.txt"):
        for line in (HERE / f).read_text(encoding="utf-8").splitlines():
            out |= set(PLAIN.findall(line.split("#")[0]))
    return out


def symbols(api) -> tuple[list, int]:
    """(全部要录的合约, 其中套利合约几个)。单腿先和交易所在挂的合约对一遍：get_quote 传不存在的合约会卡住。"""
    combos = list_combos(api)
    legs = board_legs()
    for s in combos:
        ex, pair = s.split(".SP ")
        legs |= {f"{ex}.{x}" for x in pair.split("&")}
    alive = {x for ex in EXCHANGES for x in api.query_quotes(ins_class="FUTURE", exchange_id=ex, expired=False)}
    return combos + sorted(legs & alive), len(combos)


def row(s: str, q) -> list:
    def show(x):
        x = num(x)
        return "" if x is None else f"{x:g}"
    out = [s, str(q.datetime)[:23], show(q.last_price), show(q.volume), show(q.open_interest)]
    for i, _ in LEVELS:
        out += [show(q[f"bid_price{i}"]), show(q[f"bid_volume{i}"]), show(q[f"ask_price{i}"]), show(q[f"ask_volume{i}"])]
    return out


class Sink:
    """一个 csv：和这个合约上一次写的一样就不写。重连后 reset，重新写一张全量。"""

    def __init__(self, path: Path):
        self.path, self.n, self.last = path, 0, {}
        new = not path.exists()
        self.f = path.open("a", encoding="utf-8-sig" if new else "utf-8", newline="", buffering=1 << 16)
        self.w = csv.writer(self.f)
        if new:
            self.w.writerow(HEAD)

    def reset(self):
        self.last = {}

    def write(self, quotes: dict):
        stamp = f"{datetime.now():%Y-%m-%d %H:%M:%S.%f}"[:-3]
        for s, q in quotes.items():
            r = row(s, q)
            if self.last.get(s) != r:
                self.last[s] = r
                self.w.writerow([stamp] + r)
                self.n += 1
        self.f.flush()

    def close(self):
        self.f.close()


def has_opening(api, o: datetime) -> bool:
    cal = api.get_trading_calendar(start_dt=(o - timedelta(days=20)).date(), end_dt=(o + timedelta(days=20)).date())
    return pre.has_opening(o, {d.date() for d in cal[cal["trading"]]["date"]})


def run(o: datetime, open_from: datetime, end: datetime, folder: Path, check: bool) -> None:
    stem = f"套利盘口_{o:%Y-%m-%d_%H%M}"
    sink = Sink(folder / f"{stem}_全天.csv")
    try:
        while datetime.now() < end:
            try:
                api = TqApi(auth=load_auth(), disable_print=True)
            except Exception as e:
                print(f"{datetime.now():%H:%M:%S} 连天勤失败：{type(e).__name__} {e}", flush=True)
                time.sleep(15)
                continue
            try:
                if check:
                    if not has_opening(api, o):
                        print(f"{datetime.now():%H:%M:%S} {o:%m-%d %H:%M} 这次没有开盘，退出", flush=True)
                        return
                    check = False
                syms, n = symbols(api)
                quotes = dict(zip(syms, api.get_quote_list(syms)))
                sink.reset()
                print(f"{datetime.now():%H:%M:%S} 订阅 {len(syms)} 个合约（套利 {n}、单腿 {len(syms) - n}），开盘 {o:%m-%d %H:%M}，录到 {end:%m-%d %H:%M}", flush=True)
                while (now := datetime.now()) < end:
                    api.wait_update(deadline=time.time() + 0.25)  # 休市时没有新行情，一定要带期限
                    if now >= open_from:
                        sink.write(quotes)
            except Exception as e:
                print(f"{datetime.now():%H:%M:%S} 天勤断开：{type(e).__name__} {e}，重连", flush=True)
                time.sleep(5)
            finally:
                try:
                    api.close()
                except Exception as e:
                    print(f"断开出错：{type(e).__name__} {e}", flush=True)
    finally:
        sink.close()
        print(f"{datetime.now():%H:%M:%S} {stem} 结束：{sink.n} 行", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--短跑", dest="short", type=int, help="休市试跑多少秒")
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args()
    lower_priority()
    now = datetime.now()
    if a.short:
        folder = a.out or TEST_DIR
        folder.mkdir(parents=True, exist_ok=True)
        start = now + timedelta(seconds=8)
        run(start, start, now + timedelta(seconds=a.short), folder, False)
        return
    found = session(now)
    if found is None:
        print(f"{now:%H:%M:%S} 现在不在录制时段，不录", flush=True)
        return
    o, end = found
    # 按本机时钟，前后各留的余量够盖住十几秒的时钟误差：竞价那张在开盘前 1 分钟内
    run(o, o - timedelta(minutes=2), end, a.out or OUT, True)


if __name__ == "__main__":
    main()
