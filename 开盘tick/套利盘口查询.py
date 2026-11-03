"""套利盘口查询：上期所、上期能源套利指令（标准套利合约，如 SHFE.SP ag2612&ag2702）的五档盘口，
筛出「盘口薄、价差档位大」的。只读行情，不下单。

双击同目录「套利盘口查询.bat」就行：后台连天勤、订阅全部套利合约，自动用浏览器打开 http://127.0.0.1:8765 。
筛选在网页上改（看到第几档、卖N − 买N 至少几档、挂单手数合计少于几手、品种），改了立刻生效，浏览器会记住。
关掉黑窗口就停了。

用法（在本文件夹里）：
  python 套利盘口查询.py                 开网页
  python 套利盘口查询.py --once          不开网页，按默认规则打印一次（--depth 2 --ticks 4 --vol 30 可改）
"""
import argparse
import json
import math
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import 开盘tick as tick  # 账号、天勤、venv 包路径都用它的

HERE = Path(__file__).resolve().parent
PAGE = HERE / "套利盘口查询.html"
PORT = 8765
EXCHANGES = ("SHFE", "INE")
RELIST_SEC = 30 * 60  # 每 30 分钟重查一次合约列表，交易所加了新套利合约能自动带上
NAMES = {"cu": "铜", "bc": "国际铜", "al": "铝", "zn": "锌", "pb": "铅", "ni": "镍", "sn": "锡", "ao": "氧化铝",
         "au": "黄金", "ag": "白银", "rb": "螺纹钢", "hc": "热卷", "ss": "不锈钢", "sc": "原油", "lu": "低硫燃油",
         "fu": "燃料油", "bu": "沥青", "ru": "天然橡胶", "nr": "20号胶", "br": "丁二烯橡胶", "sp": "纸浆",
         "op": "胶版纸", "ad": "铸造铝合金", "wr": "线材", "ec": "集运欧线"}

lock = threading.Lock()
state = {"rows": [], "状态": "正在连天勤…", "红": False, "更新": ""}


def num(x):
    """NaN、0 价的档都当没挂单。"""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def product(sym: str) -> str:
    leg1 = sym.split(" ", 1)[1].split("&")[0]  # "SHFE.SP ag2612&ag2702" → ag2612
    return leg1.rstrip("0123456789")


def row(sym: str, q) -> dict:
    bids, asks = [], []
    for i in range(1, 6):
        bp, bv = num(q[f"bid_price{i}"]), num(q[f"bid_volume{i}"]) or 0
        ap, av = num(q[f"ask_price{i}"]), num(q[f"ask_volume{i}"]) or 0
        bids.append([bp, int(bv)] if bp is not None and bv > 0 else [None, 0])
        asks.append([ap, int(av)] if ap is not None and av > 0 else [None, 0])
    p = product(sym)
    return {"合约": sym, "品种": p, "名称": NAMES.get(p, p), "交易所": sym.split(".")[0],
            "跳": num(q["price_tick"]), "时间": str(q["datetime"])[5:19], "买": bids, "卖": asks}


def judge(r: dict, depth: int, ticks: float, vol: float):
    """和网页同一套规则：返回 (是否符合, 档数或 None=缺档, 手数合计)。网页那边改规则时这里一起改。"""
    b, a = r["买"][depth - 1][0], r["卖"][depth - 1][0]
    total = sum(v for _, v in r["买"][:depth]) + sum(v for _, v in r["卖"][:depth])
    if r["买"][0][0] is None and r["卖"][0][0] is None:
        return False, None, total  # 完全没人挂单，不显示
    gap = round((a - b) / r["跳"]) if b is not None and a is not None and r["跳"] else None
    return (gap is None or gap >= ticks) and total < vol, gap, total


def list_combos(api) -> list:
    out = []
    for ex in EXCHANGES:
        out += api.query_quotes(ins_class="COMBINE", exchange_id=ex, expired=False)
    return sorted(out)


def connect():
    n = 0
    while True:
        n += 1
        try:
            return tick.TqApi(auth=tick.load_auth(), disable_print=True)
        except Exception as e:
            print(f"第 {n} 次连天勤失败：{type(e).__name__} {e}")
            with lock:
                state.update({"状态": f"连不上天勤，重试中（第 {n} 次）", "红": True})
            time.sleep(min(60, 5 * n))


def feed():
    """主线程：一直订阅、刷新快照；断了就重连。"""
    while True:
        api = connect()
        try:
            syms, listed = list_combos(api), time.time()
            quotes = dict(zip(syms, api.get_quote_list(syms)))
            print(f"已订阅 {len(quotes)} 个套利合约")
            while True:
                if time.time() - listed > RELIST_SEC:
                    syms, listed = list_combos(api), time.time()
                    quotes = dict(zip(syms, api.get_quote_list(syms)))
                with lock:
                    state.update({"rows": [row(s, q) for s, q in quotes.items()], "状态": "正常", "红": False,
                                  "更新": datetime.now().strftime("%H:%M:%S")})
                api.wait_update(deadline=time.time() + 1)
        except Exception as e:
            print(f"天勤断开：{type(e).__name__} {e}，重连")
            with lock:
                state.update({"状态": f"天勤断开，重连中（{datetime.now():%H:%M:%S}）", "红": True})
            try:
                api.close()
            except Exception:
                pass
            time.sleep(5)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/data"):
            with lock:
                body = json.dumps(state, ensure_ascii=False).encode("utf-8")
            ctype = "application/json; charset=utf-8"
        elif self.path in ("/", "/index.html"):
            body, ctype = PAGE.read_bytes(), "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 不刷屏
        pass


def serve(browser: bool = True):
    url = f"http://127.0.0.1:{PORT}"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"{url} 已经在开着（另一个窗口在跑），直接打开网页")
        if browser:
            webbrowser.open(url)
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"网页：{url}（关掉这个窗口就停）")
    if browser:
        webbrowser.open(url)
    feed()


def once(depth: int, ticks: float, vol: float):
    api = tick.connect()
    try:
        syms = list_combos(api)
        quotes = dict(zip(syms, api.get_quote_list(syms)))
        api.wait_update(deadline=time.time() + 2)
        rows = [row(s, q) for s, q in quotes.items()]
    finally:
        api.close()
    hit = []
    for r in rows:
        ok, gap, total = judge(r, depth, ticks, vol)
        if ok:
            hit.append((gap is None, -(gap or 0), r, gap, total))
    hit.sort(key=lambda x: x[:2])
    fmt = lambda pv: f"{pv[0]:g}×{pv[1]}" if pv[0] is not None else "—"  # noqa: E731
    print(f"共 {len(rows)} 个套利合约；买{depth}到卖{depth} ≥ {ticks:g} 档、手数合计 < {vol:g}：{len(hit)} 个")
    for *_, r, gap, total in hit:
        book = " ".join(fmt(x) for x in r["买"][:depth][::-1]) + " | " + " ".join(fmt(x) for x in r["卖"][:depth])
        g1 = judge(r, 1, 0, math.inf)[1]
        show = lambda g: "缺档" if g is None else f"{g} 档"  # noqa: E731
        print(f"{r['合约']:<24} 一档 {show(g1):>5}  {depth}档 {show(gap):>5}  {total:>4} 手  {book}  {r['时间']}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="不开网页，打印一次")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    p.add_argument("--depth", type=int, default=2, choices=range(1, 6))
    p.add_argument("--ticks", type=float, default=4)
    p.add_argument("--vol", type=float, default=30)
    a = p.parse_args()
    if a.once:
        once(a.depth, a.ticks, a.vol)
    else:
        serve(not a.no_browser)


if __name__ == "__main__":
    main()
