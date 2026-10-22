"""期货速览：上期所 / 上期能源 仓储数据 + 指定品种近期新闻。

只用 Python 标准库，不需要 pip 安装任何东西。

用法：
    python futures_brief.py stock                 # 最新的仓单日报 + 库存周报（全部品种）
    python futures_brief.py stock 铜 原油          # 同上，另外列出这几个品种的分仓库明细
    python futures_brief.py stock --date 20261016 # 指定日期
    python futures_brief.py stock --history-days 60   # 回看 60 个交易日（默认 20 天、12 周）
    python futures_brief.py spread                # 跨月价差参考（第一次下载约 3 年数据，之后自动更新）
    python futures_brief.py news 铜 原油           # 这几个品种近 3 天的新闻列表
    python futures_brief.py news 铜 --days 7 --limit 30
    python futures_brief.py all 铜 原油            # 仓储 + 新闻一起
    python futures_brief.py factors               # 影响因素：按 因素清单.json 搜新闻 + 近月−下月价差
    python futures_brief.py page                  # 只重新拼网页（AI 写完解读后用）
    python futures_brief.py review                # 复盘：AI 解读说的价差方向，后来真的这样走了吗

结果写到本文件旁边的 output/ 目录；复盘存档和报告在 复盘/ 目录。
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import gzip
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# 上期所官网的数据页（上期能源的品种也在同一张表里）
SHFE_PAGE = "https://www.shfe.com.cn/reports/tradedata/dailyandweeklydata/"
SHFE_STOCK_URL = "https://www.shfe.com.cn/data/tradedata/future/stockdata/{kind}_{date}/ZH/all.html"
REPORTS = {
    "dailystock": "仓单日报",
    "weeklystock": "库存周报",
}

# 这些品种属于上海国际能源交易中心（上期能源 / INE），其余属于上期所
INE_PRODUCTS = ("中质含硫原油", "低硫燃料油", "20号胶", "铜(BC)")

# 表格里的文字列（其余都是数字列）
ID_COLUMNS = ("地区", "仓库", "品级", "油种")

# 表里的品种名 → 交易代码（取自上期所官网 product_config.dat）
PRODUCT_CODES = {
    "铜": "cu", "铜(BC)": "bc", "铝": "al", "锌": "zn", "铅": "pb", "镍": "ni", "锡": "sn",
    "氧化铝": "ao", "铸造铝合金": "ad", "黄金": "au", "白银": "ag",
    "螺纹钢": "rb", "线材": "wr", "热轧卷板": "hc", "不锈钢": "ss",
    "中质含硫原油": "sc", "低硫燃料油": "lu", "燃料油": "fu", "石油沥青": "bu",
    "丁二烯橡胶": "br", "天然橡胶": "ru", "20号胶": "nr", "纸浆": "sp", "胶版印刷纸": "op",
}

# 用户常用叫法 / 代码 → 表里的品种名
PRODUCT_ALIASES = {
    "原油": "中质含硫原油", "低硫": "低硫燃料油", "国际铜": "铜(BC)",
    "橡胶": "天然橡胶", "沥青": "石油沥青", "螺纹": "螺纹钢", "热卷": "热轧卷板",
    "丁二烯": "丁二烯橡胶", "胶版纸": "胶版印刷纸", "铝合金": "铸造铝合金",
    "金": "黄金", "银": "白银",
    **{code: name for name, code in PRODUCT_CODES.items()},
}

# 新闻搜索时，品种名换成更准确的期货叫法（可以再加）
NEWS_ALIASES = {
    "铜": ["沪铜", "铜价"], "铝": ["沪铝", "铝价"], "锌": ["沪锌"], "铅": ["沪铅"], "镍": ["沪镍"],
    "锡": ["沪锡"], "黄金": ["沪金", "黄金期货"], "白银": ["沪银", "白银期货"],
    "原油": ["原油期货", "SC原油"], "中质含硫原油": ["原油期货", "SC原油"],
    "螺纹钢": ["螺纹钢期货"], "热轧卷板": ["热卷期货"], "不锈钢": ["不锈钢期货"],
    "氧化铝": ["氧化铝期货"], "天然橡胶": ["橡胶期货"], "橡胶": ["橡胶期货"],
    "20号胶": ["20号胶"], "燃料油": ["燃料油期货"], "低硫燃料油": ["低硫燃料油"],
    "石油沥青": ["沥青期货"], "沥青": ["沥青期货"], "纸浆": ["纸浆期货"],
    "铸造铝合金": ["铝合金期货"], "丁二烯橡胶": ["丁二烯橡胶期货"],
    "集运欧线": ["集运指数", "欧线期货"], "欧线": ["集运指数", "欧线期货"],
}


# ---------------------------------------------------------------- 通用

def log(msg: str) -> None:
    print(msg, flush=True)


def http_get(url: str, referer: str = "", timeout: int = 30) -> tuple[int, str]:
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, ""
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            time.sleep(1 + attempt)
    raise RuntimeError(f"网络请求失败：{url}（{last_err}）")


def parse_date(s: str | None) -> dt.date:
    if not s:
        return dt.date.today()
    s = s.replace("-", "").replace("/", "")
    return dt.datetime.strptime(s, "%Y%m%d").date()


def to_number(s: str) -> float | None:
    s = s.replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def fmt_number(v: float) -> str:
    return str(int(v)) if v == int(v) else f"{v:g}"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- 解析上期所表格

class _TableParser(HTMLParser):
    """把页面里每个 <table> 拆成行和单元格，保留 rowspan / colspan。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[dict] = []
        self._table: dict | None = None
        self._row: dict | None = None
        self._cell: dict | None = None
        self._in_thead = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._table = {"head": [], "body": []}
            self.tables.append(self._table)
        elif tag == "thead":
            self._in_thead = True
        elif tag == "tr" and self._table is not None:
            self._row = {"cls": a.get("class") or "", "cells": [], "head": self._in_thead}
        elif tag in ("td", "th") and self._row is not None:
            self._cell = {
                "text": "",
                "rowspan": int(a.get("rowspan") or 1),
                "colspan": int(a.get("colspan") or 1),
            }
        elif tag == "br" and self._cell is not None:
            self._cell["text"] += " "

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._cell["text"] = re.sub(r"\s+", " ", self._cell["text"]).strip()
            self._row["cells"].append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            self._table["head" if self._row["head"] else "body"].append(self._row)
            self._row = None
        elif tag == "thead":
            self._in_thead = False
        elif tag == "table":
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["text"] += data


def _expand(rows: list[dict]) -> list[list[str]]:
    """按 rowspan / colspan 把行展开成规整的二维表。"""
    grid: list[list[str]] = []
    carry: dict[int, tuple[str, int]] = {}  # 列号 -> (文字, 还要往下占几行)
    for row in rows:
        out: list[str] = []
        col = 0
        cells = list(row["cells"])
        while cells or any(c >= col for c in carry):
            if col in carry:
                text, left = carry[col]
                out.append(text)
                if left > 1:
                    carry[col] = (text, left - 1)
                else:
                    del carry[col]
                col += 1
                continue
            if not cells:
                break
            cell = cells.pop(0)
            for _ in range(cell["colspan"]):
                out.append(cell["text"])
                if cell["rowspan"] > 1:
                    carry[col] = (cell["text"], cell["rowspan"] - 1)
                col += 1
        grid.append(out)
    return grid


SEP = "·"  # 两层表头拼成一个列名时用的分隔符，如「本周库存·小计」（不是减号）


def _header_names(head_rows: list[dict]) -> list[str]:
    grid = _expand(head_rows)
    if not grid:
        return []
    width = max(len(r) for r in grid)
    names = []
    for c in range(width):
        parts: list[str] = []
        for r in grid:
            if c < len(r) and r[c] and (not parts or parts[-1] != r[c]):
                parts.append(r[c])
        names.append(SEP.join(parts))
    return names


def parse_stock_html(text: str) -> dict:
    """返回 {"title": 期号说明, "tables": [{product, unit, exchange, columns, rows}]}。"""
    p = _TableParser()
    p.feed(text)
    title = ""
    tables = []
    for t in p.tables:
        if not t["head"]:
            # 页首的说明表：第一行是日期和期号
            if not title and t["body"] and t["body"][0]["cells"]:
                title = t["body"][0]["cells"][0]["text"]
            continue
        columns = _header_names(t["head"])  # 如 地区, 仓库, 期货, 增减
        product, unit = "", ""
        body_rows = []
        for row in t["body"]:
            if "special_row_type" in row["cls"]:
                texts = [c["text"] for c in row["cells"]]
                product = texts[0] if texts else ""
                unit = texts[1].replace("单位：", "").replace("单位:", "") if len(texts) > 1 else ""
            else:
                body_rows.append(row)
        # 厂库 / 交割仓库 统一叫「仓库」，方便在 CSV 里筛选
        columns = ["仓库" if c in ("厂库", "交割仓库") else c for c in columns]
        grid = _expand(body_rows)
        id_cols = [c for c in columns if c in ID_COLUMNS]
        rows = []
        for raw, cells in zip(body_rows, grid):
            if not any(cells):
                continue
            rec = dict(zip(columns, cells))
            if "isTotal" in raw["cls"]:
                # 汇总行：「合计 / 总计 / 保税商品总计」可能落在任意一个文字列里，
                # 它前面跨行延续下来的文字（地区或厂库名）就是它所汇总的那一组
                ids = [rec.get(c, "") for c in id_cols]
                label = next((v for v in ids if "计" in v), "合计")
                group = next((v for v in ids if v and v != label), "")
                rec["行类型"] = label
                rec["分组"] = "" if "总计" in label else group
                for c in id_cols:
                    rec[c] = ""
            else:
                rec["行类型"] = "明细"
                rec["分组"] = ""
            rows.append(rec)
        base = re.sub(r"\((仓库|厂库)\)$", "", product)
        tables.append({
            "product": product,
            "base": base,
            "code": PRODUCT_CODES.get(base, ""),
            "unit": unit,
            "exchange": "上期能源" if base in INE_PRODUCTS else "上期所",
            "columns": columns,
            "id_columns": id_cols,
            "rows": rows,
        })
    return {"title": title, "tables": tables}


def row_label(table: dict, r: dict) -> str:
    if r["行类型"] == "明细":
        return " ".join(dict.fromkeys(v for v in (r.get(c, "") for c in table["id_columns"]) if v)) or "全部"
    return f"{r['分组']} {r['行类型']}".strip()


def summary_rows(table: dict) -> list[dict]:
    """每个品种挑出最能代表全貌的行：有「总计」用总计，否则用各「合计」，都没有就列出明细。"""
    rows = table["rows"]
    total = [r for r in rows if r["行类型"] == "总计"]
    if total:
        return total
    subtotal = [r for r in rows if r["行类型"] != "明细"]
    return subtotal or rows


def check_totals(table: dict) -> list[str]:
    """自检：明细行逐列相加应等于交易所给的「总计」，对不上说明表格格式可能变了。"""
    total = [r for r in table["rows"] if r["行类型"] == "总计"]
    if not total:
        return []
    detail = [r for r in table["rows"]
              if r["行类型"] == "明细" and not any(r.get(c) == "小计" for c in table["id_columns"])]
    problems = []
    for c in table["columns"]:
        if c in table["id_columns"]:
            continue
        want = to_number(total[0].get(c, ""))
        vals = [to_number(r.get(c, "")) for r in detail]
        if want is None or any(v is None for v in vals) or not vals:
            continue
        got = sum(vals)
        if abs(got - want) > 1e-6:
            problems.append(f"{table['product']}「{c}」：明细相加 {fmt_number(got)} ≠ 总计 {fmt_number(want)}")
    return problems


# ---------------------------------------------------------------- 仓储数据

CACHE = OUTPUT / "缓存"
SHFE_CALENDAR_URL = "https://www.shfe.com.cn/data/config/js/trade-data.js"
_NON_TRADING: set[str] | None = None


def non_trading_days() -> set[str]:
    """上期所官网的非交易日列表（周末 + 节假日）。取不到就只按周末判断。"""
    global _NON_TRADING
    if _NON_TRADING is None:
        try:
            status, text = http_get(SHFE_CALENDAR_URL, referer=SHFE_PAGE)
        except RuntimeError:
            status, text = 0, ""
        _NON_TRADING = set()
        if status == 200:
            for days in re.findall(r"\d{4}\s*:\s*'([\d,]+)'", text):
                _NON_TRADING.update(days.split(","))
    return _NON_TRADING


def is_trading_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d.strftime("%Y%m%d") not in non_trading_days()


def report_url(kind: str, d: dt.date) -> str:
    return SHFE_STOCK_URL.format(kind=kind, date=d.strftime("%Y%m%d"))


def get_report(kind: str, d: dt.date) -> str | None:
    """取某天的报表；下载过的从 output/缓存 读，历史数据不会再变。"""
    path = CACHE / f"{kind}_{d:%Y%m%d}.html"
    if path.exists():
        return path.read_text(encoding="utf-8")
    status, text = http_get(report_url(kind, d), referer=SHFE_PAGE)
    if status == 200 and "el-table_table" in text:
        log(f"    下载 {REPORTS[kind]} {d:%Y-%m-%d}")
        write_text(path, text)
        return text
    return None


def fetch_stock(kind: str, start: dt.date, max_back: int) -> tuple[dt.date, str, str]:
    """从 start 往前找最近一个有数据的交易日。"""
    for i in range(max_back + 1):
        d = start - dt.timedelta(days=i)
        if not is_trading_day(d):
            continue
        text = get_report(kind, d)
        if text:
            return d, report_url(kind, d), text
    raise RuntimeError(f"{REPORTS[kind]}：从 {start} 往前 {max_back} 天都没找到数据")


def fetch_history(kind: str, latest: dt.date, count: int) -> list[tuple[dt.date, dict]]:
    """取 latest 及之前共 count 期报表（日报按交易日，周报每周一期），按日期从旧到新排列。"""
    found: list[tuple[dt.date, dict]] = []
    d = latest
    misses = 0
    while len(found) < count and misses < 15:
        if is_trading_day(d):
            text = get_report(kind, d)
            if text:
                found.append((d, parse_stock_html(text)))
                misses = 0
                if kind == "weeklystock":
                    # 跳到上一周的周五，再往前找该周最后一个有数据的交易日
                    d = d - dt.timedelta(days=d.weekday() + 3)
                    continue
            else:
                misses += 1
        d -= dt.timedelta(days=1)
    return list(reversed(found))


def resolve_products(names: list[str]) -> list[str]:
    out = []
    for n in names:
        key = n.strip()
        out.append(PRODUCT_ALIASES.get(key.lower(), PRODUCT_ALIASES.get(key, key)))
    return out


def value_columns(table: dict) -> tuple:
    return tuple(c for c in table["columns"] if c not in table["id_columns"])


def summary_groups(tables: list[dict]) -> list[tuple[tuple, list[tuple[dict, dict]]]]:
    """把表头相同的品种归到一组，每组一张汇总表。"""
    groups: dict[tuple, list[tuple[dict, dict]]] = {}
    for t in tables:
        for r in summary_rows(t):
            groups.setdefault(value_columns(t), []).append((t, r))
    return list(groups.items())


# 周报里由脚本算出的两列：后备货 = 合格但还没做成仓单的货
BACKUP_COLS = ("本周后备货（小计−期货）", "后备货周变化")


def has_backup(cols) -> bool:
    return f"本周库存{SEP}小计" in cols and f"本周库存{SEP}期货" in cols


def fmt_signed(v: float | None) -> str:
    return "—" if v is None else f"{'+' if v > 0 else ''}{fmt_number(v)}"


def change_class(n: float | None) -> str:
    return "" if not n else (' class="up"' if n > 0 else ' class="down"')


def pct_of_change(chg: float | None, cur: float | None) -> str:
    """涨跌幅 = 增减 ÷ 上一期数量（上一期 = 本期 − 增减）。"""
    if chg is None or cur is None:
        return "—"
    prev = cur - chg
    if prev == 0:
        return "—" if chg == 0 else "从 0 新增"
    return fmt_pct(chg / prev * 100)


def change_base(cols, c: str) -> str | None:
    """「增减」列对应的本期数量列，用来算涨跌幅。"""
    if c == "增减":
        return first_col(list(cols), ["期货", "本日数量", "本周期货"])
    cand = c.replace("库存增减", "本周库存").replace(SEP + "增减", SEP + "本周")
    return cand if cand in cols else None


def backup_cells(r: dict) -> list[tuple[str, float | None]]:
    """本周后备货 = 本周库存小计 − 本周期货；周变化 = 本周后备货 − 上周后备货。"""
    s, w = to_number(r.get(f"本周库存{SEP}小计", "")), to_number(r.get(f"本周库存{SEP}期货", ""))
    if s is None or w is None:
        return [("—", None), ("—", None)]
    ps, pw = to_number(r.get(f"上周库存{SEP}小计", "")), to_number(r.get(f"上周库存{SEP}期货", ""))
    chg = None if ps is None or pw is None else (s - w) - (ps - pw)
    txt = fmt_signed(chg) if chg is None else f"{fmt_signed(chg)}（{pct_of_change(chg, s - w)}）"
    return [(fmt_number(s - w), None), (txt, chg)]


def summary_cells(kind: str, cols, r: dict) -> tuple[list[str], list[tuple[str, float | None]]]:
    """汇总表一行：列名 + 每格（显示文字, 用来上色的增减数）。
    日报在每个「增减」后面加一列「涨跌幅」；周报把百分比写在增减数后面的括号里；周报再补两列后备货。"""
    heads: list[str] = []
    cells: list[tuple[str, float | None]] = []
    for c in cols:
        v = r.get(c, "")
        heads.append(c)
        if "增减" not in c:
            cells.append((v, None))
            continue
        n = to_number(v)
        base = change_base(cols, c)
        p = pct_of_change(n, to_number(r.get(base, ""))) if base else "—"
        if kind == "dailystock":
            cells += [(v, n), (p, n)]
            heads.append("涨跌幅")
        else:
            cells.append((f"{v}（{p}）" if v and p != "—" else v, n))
    if kind == "weeklystock" and has_backup(cols):
        heads += list(BACKUP_COLS)
        cells += backup_cells(r)
    return heads, cells


def stock_to_markdown(kind: str, day: dt.date, url: str, parsed: dict, wanted: list[str],
                      problems: list[str]) -> str:
    name = REPORTS[kind]
    lines = [
        f"# 上期所 / 上期能源 {name}（{day:%Y-%m-%d}）",
        "",
        f"- 期号：{parsed['title'] or '—'}",
        f"- 来源：{url}",
        f"- 抓取时间：{dt.datetime.now():%Y-%m-%d %H:%M}",
        "- 数据由脚本直接从交易所官网解析，未经 AI 改写。",
        "- 「(仓库)」和「(厂库)」是交易所分开发布的两张表，各自有自己的总计。",
    ]
    if kind == "weeklystock":
        lines.append("- 「小计」= 符合交割品质的货物数量；「期货」= 已制成仓单的数量；「可用库容」= 还能制成仓单的数量。")
        lines.append("- 最后两列「本周后备货（小计−期货）」「后备货周变化」由脚本计算：合格但还没做成仓单、随时可以去注册的货。")
    else:
        lines.append("- 「期货」/「本日数量」= 已制成仓单的货物数量；「增减」= 较上一交易日。")
    if problems:
        lines.append("- ⚠ 自检发现明细与总计对不上（交易所表格格式可能变了，请以官网为准）：")
        lines += [f"  - {p}" for p in problems]
    else:
        lines.append("- 自检通过：每个品种的明细逐列相加都等于交易所给的总计。")
    lines.append("")

    for exchange in ("上期所", "上期能源"):
        tables = [t for t in parsed["tables"] if t["exchange"] == exchange]
        if not tables:
            continue
        lines += [f"## {exchange}：各品种汇总", ""]
        for cols, items in summary_groups(tables):
            heads = summary_cells(kind, cols, items[0][1])[0]
            lines.append("| 品种 | 代码 | 单位 | 汇总行 | " + " | ".join(heads) + " |")
            lines.append("|" + "---|" * (4 + len(heads)))
            for t, r in items:
                vals = [x for x, _ in summary_cells(kind, cols, r)[1]]
                lines.append(f"| {t['product']} | {t['code']} | {t['unit']} | {row_label(t, r)} | "
                             + " | ".join(vals) + " |")
            lines.append("")

    if wanted:
        lines += ["## 指定品种：分仓库明细", ""]
        found = False
        for t in parsed["tables"]:
            if t["base"] not in wanted and t["product"] not in wanted:
                continue
            found = True
            cols = list(t["id_columns"]) + list(value_columns(t))
            lines += [f"### {t['product']} {t['code']}（{t['exchange']}，单位：{t['unit']}）", ""]
            lines.append("| " + " | ".join(cols) + " |")
            lines.append("|" + "---|" * len(cols))
            for r in t["rows"]:
                vals = [r.get(c, "") for c in cols]
                if r["行类型"] != "明细":
                    vals[0] = f"**{row_label(t, r)}**"
                lines.append("| " + " | ".join(vals) + " |")
            lines.append("")
        if not found:
            names = "、".join(sorted({t["base"] for t in parsed["tables"]}))
            lines += [f"（没找到 {'、'.join(wanted)}。表里的品种有：{names}）", ""]
    return "\n".join(lines)


def stock_to_csv(path: Path, parsed: dict) -> None:
    id_cols = [c for c in ID_COLUMNS if any(c in t["id_columns"] for t in parsed["tables"])]
    val_cols: list[str] = []
    for t in parsed["tables"]:
        for c in value_columns(t):
            if c not in val_cols:
                val_cols.append(c)
    header = ["交易所", "品种", "代码", "单位", "行类型", "分组"] + id_cols + val_cols
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig：Excel 双击打开时中文不乱码
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        for t in parsed["tables"]:
            for r in t["rows"]:
                w.writerow([t["exchange"], t["product"], t["code"], t["unit"], r["行类型"], r["分组"]]
                           + [r.get(c, "") for c in id_cols + val_cols])


def stock_to_html_section(kind: str, day: dt.date, url: str, parsed: dict, problems: list[str]) -> str:
    name = REPORTS[kind]
    parts = [f'<section><h2>{name} <small>{day:%Y-%m-%d} · {html.escape(parsed["title"])}</small></h2>',
             f'<p class="src">来源：<a href="{url}">{url}</a></p>']
    if problems:
        parts.append('<p class="warn">⚠ 自检发现明细与总计对不上，交易所表格格式可能变了，请以官网为准：<br>'
                     + "<br>".join(html.escape(p) for p in problems) + "</p>")
    for exchange in ("上期所", "上期能源"):
        tables = [t for t in parsed["tables"] if t["exchange"] == exchange]
        if not tables:
            continue
        parts.append(f"<h3>{exchange}</h3>")
        for cols, items in summary_groups(tables):
            heads = summary_cells(kind, cols, items[0][1])[0]
            parts.append('<div class="wrap"><table><thead><tr><th>品种</th><th>代码</th><th>单位</th><th>汇总行</th>'
                         + "".join(f"<th>{html.escape(c)}</th>" for c in heads) + "</tr></thead><tbody>")
            for t, r in items:
                tds = [f"<td{change_class(n)}>{html.escape(v)}</td>" for v, n in summary_cells(kind, cols, r)[1]]
                parts.append(f"<tr><td>{html.escape(t['product'])}</td><td>{t['code']}</td><td>{html.escape(t['unit'])}</td>"
                             f"<td>{html.escape(row_label(t, r))}</td>" + "".join(tds) + "</tr>")
            parts.append("</tbody></table></div>")
    parts.append("</section>")
    return "\n".join(parts)


# ---------------------------------------------------------------- 历史走势与风险提示

# 风险提示的判断标准（百分比）。想更敏感或更迟钝，改这里的数字即可。
RISK_RULES = {
    "5日降_高": -30,     # 近 5 个交易日仓单下降超过 30% → 【高】
    "5日降_中": -15,     # 近 5 个交易日下降超过 15% → 【中】
    "区间降_高": -50,    # 整个回看期下降超过 50% → 【高】
    "区间降_中": -30,    # 整个回看期下降超过 30% → 【中】
    "单日降_中": -15,    # 单日下降超过 15% → 【中】
    "低位_中": 20,       # 只剩回看期最高值的 20% 以下 → 【中】
    "5日增_提示": 50,    # 近 5 个交易日增加超过 50% → 【提示】
    "周连降_中": 3,      # 周度库存连续下降至少 3 周 → 【中】
    "周连降_最小降幅": 10,  # ……且这几周累计降幅至少 10%，否则不算
    "周区间降_高": -30,  # 回看期内周度库存下降超过 30% → 【高】
    "周区间增_提示": 50,  # 回看期内周度库存增加超过 50% → 【提示】
    # 近月覆盖率（仓单 ÷ 近月持仓）跟过去几年「每月同一时段」比，排名越低越紧。只看跨月价差那 11 个品种。
    "覆盖率同期_高": 5,    # 比 95% 的同期日子都低 → 【高】
    "覆盖率同期_中": 15,   # 比 85% 的同期日子都低 → 【中】
    "覆盖率_上限": 100,    # 覆盖率在 100% 以上（仓单够交全部近月持仓）不提示
}

MEANING_DOWN = ("仓单 / 库存减少：交易所仓库里能拿去交割的货变少，现货偏紧。离交割月越近、持仓越大，"
                "空头越难交货，价格容易被推高（俗称「挤仓」风险）。")
MEANING_UP = "仓单 / 库存增加：可交割的货变多，供应偏宽松，对价格有压力；空头交货更容易。"
MEANING_COVER = ("近月覆盖率低：仓单相对近月合约持仓偏少。如果多头一直拿到交割，空头可能凑不齐货，"
                 "近月价格容易比下月更贵（挤仓风险）。")

LEVEL_ORDER = {"高": 0, "中": 1, "提示": 2}


def first_col(cols: list[str], names: list[str]) -> str | None:
    return next((n for n in names if n in cols), None)


def table_total(t: dict, col: str) -> float | None:
    """一张表某列的总数：有「总计」行用总计，否则把明细行相加（自检已保证两者一致）。"""
    total = [r for r in t["rows"] if r["行类型"] == "总计"]
    if total:
        return to_number(total[0].get(col, ""))
    vals = [to_number(r.get(col, "")) for r in t["rows"]
            if r["行类型"] == "明细" and not any(r.get(c) == "小计" for c in t["id_columns"])]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def product_metrics(parsed: dict, kind: str) -> dict[str, dict]:
    """按品种汇总：仓单（已制成仓单的数量），周报另有库存小计。同一品种的仓库 + 厂库相加。"""
    out: dict[str, dict] = {}
    for t in parsed["tables"]:
        cols = t["columns"]
        if kind == "dailystock":
            wcol, scol, ccol = first_col(cols, ["期货", "本日数量"]), None, None
        else:
            wcol = first_col(cols, [f"本周库存{SEP}期货", "本周期货", "本周库存"])
            scol = first_col(cols, [f"本周库存{SEP}小计"])
            ccol = first_col(cols, [f"可用库容量{SEP}本周", f"理论可用库容量{SEP}本周"])
        m = out.setdefault(t["base"], {"code": t["code"], "unit": t["unit"], "exchange": t["exchange"],
                                       "warrant": None, "stock": None, "backup": None, "capacity": None,
                                       "parts": []})
        m["parts"].append(t["product"])
        vals = {}
        for key, col in (("warrant", wcol), ("stock", scol), ("capacity", ccol)):
            v = vals[key] = table_total(t, col) if col else None
            if v is not None:
                m[key] = (m[key] or 0) + v
        # 后备货 = 同一张表里的「小计 − 期货」（厂库表没有小计，不算）
        if vals["stock"] is not None and vals["warrant"] is not None:
            m["backup"] = (m["backup"] or 0) + vals["stock"] - vals["warrant"]
    return out


def pct(new: float | None, old: float | None) -> float | None:
    if new is None or not old:
        return None
    return (new - old) / old * 100


def fmt_pct(p: float | None) -> str:
    return "—" if p is None else f"{p:+.1f}%"


def fmt_chg(new: float | None, old: float | None) -> str:
    if new is None or old is None:
        return "—"
    d = new - old
    return f"{'+' if d > 0 else ''}{fmt_number(d)}（{fmt_pct(pct(new, old))}）"


def analyze_daily(series: list[float]) -> tuple[dict, list[tuple[str, str, str]]]:
    """series：回看期内每天的仓单（旧→新）。返回统计数字和风险提示 [(等级, 方向, 文字)]。"""
    n = len(series)
    v = series[-1]
    p1 = series[-2] if n >= 2 else None
    p5 = series[-6] if n >= 6 else series[0]
    lo, hi = min(series), max(series)
    stats = {"latest": v, "p1": p1, "p5": p5, "first": series[0], "lo": lo, "hi": hi, "n": n,
             "pos": None if hi == lo else (v - lo) / (hi - lo) * 100}
    alerts: list[tuple[str, str, str]] = []
    if n < 2 or hi == 0:
        return stats, alerts
    R = RISK_RULES
    if v == 0:
        alerts.append(("高", "↓", f"仓单已降到 0（近 {n} 个交易日最高 {fmt_number(hi)}）"))
        return stats, alerts
    c5, cn, c1 = pct(v, p5), pct(v, series[0]), pct(v, p1)
    if c5 is not None and c5 <= R["5日降_中"]:
        lvl = "高" if c5 <= R["5日降_高"] else "中"
        alerts.append((lvl, "↓", f"近 5 个交易日仓单下降 {abs(c5):.0f}%（{fmt_number(p5)} → {fmt_number(v)}）"))
    if n > 6 and cn is not None and cn <= R["区间降_中"]:
        lvl = "高" if cn <= R["区间降_高"] else "中"
        alerts.append((lvl, "↓", f"近 {n} 个交易日仓单下降 {abs(cn):.0f}%（{fmt_number(series[0])} → {fmt_number(v)}）"))
    if c1 is not None and c1 <= R["单日降_中"]:
        alerts.append(("中", "↓", f"单日下降 {abs(c1):.0f}%（{fmt_number(p1)} → {fmt_number(v)}）"))
    if n >= 10 and v == lo and hi > lo:
        alerts.append(("提示", "↓", f"创近 {n} 个交易日新低"))
    if v / hi * 100 <= R["低位_中"]:
        alerts.append(("中", "↓", f"只剩近 {n} 个交易日最高值的 {v / hi * 100:.0f}%（最高 {fmt_number(hi)}）"))
    if c5 is not None and c5 >= R["5日增_提示"]:
        alerts.append(("提示", "↑", f"近 5 个交易日仓单增加 {c5:.0f}%（{fmt_number(p5)} → {fmt_number(v)}）"))
    elif p5 == 0:
        alerts.append(("提示", "↑", f"仓单从 0 增加到 {fmt_number(v)}"))
    if n >= 10 and v == hi and hi > lo:
        alerts.append(("提示", "↑", f"创近 {n} 个交易日新高"))
    return stats, alerts


def analyze_weekly(series: list[float]) -> tuple[dict, list[tuple[str, str, str]]]:
    n = len(series)
    v = series[-1]
    streak = 0
    for i in range(n - 1, 0, -1):
        if series[i] < series[i - 1]:
            streak += 1
        else:
            break
    stats = {"latest": v, "prev": series[-2] if n >= 2 else None, "first": series[0], "n": n, "streak": streak}
    alerts: list[tuple[str, str, str]] = []
    if n < 2 or max(series) == 0:
        return stats, alerts
    R = RISK_RULES
    cn = pct(v, series[0])
    start = series[-1 - streak]
    drop = pct(v, start)
    if streak >= R["周连降_中"] and drop is not None and drop <= -R["周连降_最小降幅"]:
        alerts.append(("中", "↓", f"连续 {streak} 周下降，累计 {abs(drop):.0f}%"
                                  f"（{fmt_number(start)} → {fmt_number(v)}）"))
    if cn is not None and cn <= R["周区间降_高"]:
        alerts.append(("高", "↓", f"近 {n} 周下降 {abs(cn):.0f}%（{fmt_number(series[0])} → {fmt_number(v)}）"))
    if cn is not None and cn >= R["周区间增_提示"]:
        alerts.append(("提示", "↑", f"近 {n} 周增加 {cn:.0f}%（{fmt_number(series[0])} → {fmt_number(v)}）"))
    return stats, alerts


def build_trend(daily_hist: list[tuple[dt.date, dict]], weekly_hist: list[tuple[dt.date, dict]]) -> dict:
    """把历史报表整理成每个品种的序列、统计和风险提示。"""
    d_metrics = [(d, product_metrics(p, "dailystock")) for d, p in daily_hist]
    w_metrics = [(d, product_metrics(p, "weeklystock")) for d, p in weekly_hist]
    latest_d = d_metrics[-1][1]
    latest_w = w_metrics[-1][1] if w_metrics else {}
    products = []
    for base, info in latest_d.items():
        item = {"base": base, "code": info["code"], "unit": info["unit"], "exchange": info["exchange"],
                "parts": info["parts"], "alerts": []}
        # 日度仓单：某天缺这个品种就跳过那天
        d_series = [(d, m[base]["warrant"]) for d, m in d_metrics if base in m and m[base]["warrant"] is not None]
        item["daily"] = d_series
        if d_series:
            item["daily_stats"], alerts = analyze_daily([v for _, v in d_series])
            item["alerts"] += [(lvl, arrow, "仓单（日报）：" + txt) for lvl, arrow, txt in alerts]
        # 周度：有「库存小计」用小计，没有就用仓单
        wi = latest_w.get(base, {})
        key = "stock" if wi.get("stock") is not None else "warrant"
        item["weekly_label"] = "仓单" if key == "warrant" else (
            "库存小计（仅仓库）" if len(info["parts"]) > 1 else "库存小计")  # 厂库表没有「小计」
        w_series = [(d, m[base][key]) for d, m in w_metrics if base in m and m[base][key] is not None]
        item["weekly"] = w_series
        b_series = [m[base]["backup"] for _, m in w_metrics if base in m and m[base]["backup"] is not None]
        item["backup"] = (b_series[-1], b_series[-2] if len(b_series) >= 2 else None) if b_series else None
        if w_series:
            item["weekly_stats"], alerts = analyze_weekly([v for _, v in w_series])
            item["alerts"] += [(lvl, arrow, f"{item['weekly_label']}（周报）：" + txt) for lvl, arrow, txt in alerts]
        item["alerts"].sort(key=lambda a: LEVEL_ORDER[a[0]])
        products.append(item)
    return {
        "daily_dates": [d for d, _ in daily_hist],
        "weekly_dates": [d for d, _ in weekly_hist],
        "products": products,
    }


def display_name(item: dict) -> str:
    name = item["base"]
    if len(item["parts"]) > 1:
        name += "（仓库+厂库）"
    return name


def flagged_products(trend: dict) -> list[dict]:
    return sorted([p for p in trend["products"] if p["alerts"]],
                  key=lambda x: min(LEVEL_ORDER[a[0]] for a in x["alerts"]))


def position_text(s: dict) -> str:
    if s["pos"] is None:
        return "—"
    return "最低" if s["pos"] == 0 else "最高" if s["pos"] == 100 else f"{s['pos']:.0f}%"


def trend_to_markdown(trend: dict, wanted: list[str]) -> str:
    dd, wd = trend["daily_dates"], trend["weekly_dates"]
    nd, nw = len(dd), len(wd)
    lines = [
        f"# 上期所 / 上期能源 仓储趋势与风险提示（截至 {dd[-1]:%Y-%m-%d}）",
        "",
        f"- 日度仓单：近 {nd} 个交易日（{dd[0]:%Y-%m-%d} ~ {dd[-1]:%Y-%m-%d}），来自每日仓单日报。",
        (f"- 周度库存：近 {nw} 周（{wd[0]:%Y-%m-%d} ~ {wd[-1]:%Y-%m-%d}），来自每周库存周报。" if wd
         else "- 周度库存：无数据。"),
        "- 所有数字由脚本从交易所官网报表计算，同一品种的仓库和厂库已相加（品种名后注明）。",
        "- 风险提示由固定规则自动判断（规则见文末），只说明数据变化，**不构成买卖建议**。",
        "",
        "## 风险提示",
        "",
        f"**怎么理解**：{MEANING_DOWN}",
        f"{MEANING_UP}",
        f"{MEANING_COVER}",
        "",
    ]
    flagged = flagged_products(trend)
    if not flagged:
        lines += ["没有品种触发风险规则。", ""]
    for p in flagged:
        lines.append(f"### {display_name(p)} {p['code']}（{p['exchange']}，单位：{p['unit']}）")
        for lvl, arrow, txt in p["alerts"]:
            lines.append(f"- 【{lvl}】{arrow} {txt}")
        lines.append("")

    for exchange in ("上期所", "上期能源"):
        items = [p for p in trend["products"] if p["exchange"] == exchange and p.get("daily")]
        if not items:
            continue
        lines += [f"## {exchange}：日度仓单走势（近 {nd} 个交易日）", "",
                  "「位置」= 当前值在回看期最低到最高之间的位置，0% 是最低、100% 是最高。", "",
                  f"| 品种 | 代码 | 单位 | 最新仓单 | 日增减 | 5日变化 | {nd}日变化 | 区间最低 | 区间最高 | 位置 |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for p in items:
            s = p["daily_stats"]
            lines.append(f"| {display_name(p)} | {p['code']} | {p['unit']} | {fmt_number(s['latest'])} | "
                         f"{fmt_chg(s['latest'], s['p1'])} | {fmt_chg(s['latest'], s['p5'])} | "
                         f"{fmt_chg(s['latest'], s['first'])} | {fmt_number(s['lo'])} | {fmt_number(s['hi'])} | "
                         f"{position_text(s)} |")
        lines.append("")
        witems = [p for p in items if p.get("weekly")]
        if witems:
            lines += [f"## {exchange}：周度库存走势（近 {nw} 周）", "",
                      "后备货 = 库存小计 − 仓单：合格但还没做成仓单、随时可以去注册的货（只算有「小计」的仓库表）。", "",
                      f"| 品种 | 代码 | 单位 | 指标 | 本周 | 周变化 | {nw}周变化 | 连续下降周数 | 后备货 | 后备货周变化 |",
                      "|---|---|---|---|---|---|---|---|---|---|"]
            for p in witems:
                s = p["weekly_stats"]
                b = p.get("backup")
                lines.append(f"| {display_name(p)} | {p['code']} | {p['unit']} | {p['weekly_label']} | "
                             f"{fmt_number(s['latest'])} | {fmt_chg(s['latest'], s['prev'])} | "
                             f"{fmt_chg(s['latest'], s['first'])} | {s['streak']} | "
                             f"{fmt_number(b[0]) if b else '—'} | {fmt_chg(*b) if b else '—'} |")
            lines.append("")

    picked = [p for p in trend["products"] if p["base"] in wanted]
    if picked:
        lines += ["## 指定品种：逐日 / 逐周数据", ""]
        for p in picked:
            lines += [f"### {display_name(p)} {p['code']}（单位：{p['unit']}）", "",
                      "| 日期 | 仓单 | 较前一天 |", "|---|---|---|"]
            prev = None
            for d, v in p["daily"]:
                lines.append(f"| {d:%Y-%m-%d} | {fmt_number(v)} | {fmt_chg(v, prev)} |")
                prev = v
            if p["weekly"]:
                lines += ["", f"| 周报日期 | {p['weekly_label']} | 较上周 |", "|---|---|---|"]
                prev = None
                for d, v in p["weekly"]:
                    lines.append(f"| {d:%Y-%m-%d} | {fmt_number(v)} | {fmt_chg(v, prev)} |")
                    prev = v
            lines.append("")

    R = RISK_RULES
    lines += [
        "## 风险规则（可在 futures_brief.py 里的 RISK_RULES 修改）", "",
        f"- 【高】近 5 个交易日仓单降幅超过 {abs(R['5日降_高'])}%；或回看期降幅超过 {abs(R['区间降_高'])}%；"
        f"或仓单降到 0；或周度库存回看期降幅超过 {abs(R['周区间降_高'])}%。",
        f"- 【中】近 5 个交易日降幅超过 {abs(R['5日降_中'])}%；回看期降幅超过 {abs(R['区间降_中'])}%；"
        f"单日降幅超过 {abs(R['单日降_中'])}%；只剩回看期最高值的 {R['低位_中']}% 以下；"
        f"周度库存连续下降 {R['周连降_中']} 周及以上且累计降幅超过 {R['周连降_最小降幅']}%。",
        f"- 【提示】创回看期新低或新高；近 5 个交易日增幅超过 {R['5日增_提示']}%；仓单从 0 开始增加；"
        f"周度库存回看期增幅超过 {R['周区间增_提示']}%。",
        "- 回看期内一直是 0 的品种不提示。数量本身很小的品种（如几百吨），百分比会被放大，请结合绝对数量看。",
        f"- 近月覆盖率（只看 {'、'.join(SPREAD_CODES)}）：覆盖率 = 仓单（换算成手）÷ 近月合约持仓，"
        f"跟过去几年每月同一时段（月内日期前后 {SPREAD_RULES['日期邻域']} 天，不含本月；"
        f"{'、'.join(MAIN_MONTHS)} 按离近月交割月的天数前后 {SPREAD_RULES['日期邻域']} 天）的日子比；"
        f"比 {100 - R['覆盖率同期_高']}% 的同期日子都低 →【高】，比 {100 - R['覆盖率同期_中']}% 都低 →【中】。"
        f"覆盖率在 {R['覆盖率_上限']}% 以上、或同期样本少于 {SPREAD_RULES['最少样本']} 天时不提示。"
        "需要先运行过一次 python futures_brief.py spread。",
        "",
    ]
    return "\n".join(lines)


def trend_to_csv(path: Path, trend: dict, which: str) -> None:
    """宽表：每行一个日期，每列一个品种，方便在 Excel 里画图。"""
    dates = trend["daily_dates"] if which == "daily" else trend["weekly_dates"]
    items = [p for p in trend["products"] if p.get(which)]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["日期"] + [f"{display_name(p)} {p['code']}（{p['unit']}"
                               + ("" if which == "daily" else f"，{p['weekly_label']}") + "）" for p in items])
        maps = [dict(p[which]) for p in items]
        for d in dates:
            w.writerow([d.strftime("%Y-%m-%d")] + [fmt_number(m[d]) if d in m else "" for m in maps])


def sparkline(points: list[tuple[dt.date, float]], unit: str, width: int = 110, height: int = 28) -> str:
    """一条小折线：看形状就知道是涨是跌；鼠标停在点上显示日期和数值。"""
    if len(points) < 2:
        return ""
    vals = [v for _, v in points]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    pad = 4
    xs = [pad + i * (width - 2 * pad) / (len(points) - 1) for i in range(len(points))]
    ys = [height - pad - (v - lo) / span * (height - 2 * pad) for v in vals]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(zip(xs, ys)))
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" class="hit"><title>{d:%Y-%m-%d}：{fmt_number(v)} '
        f'{html.escape(unit)}</title></circle>'
        for (d, v), x, y in zip(points, xs, ys))
    return (f'<svg class="spark" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="走势 {fmt_number(vals[0])} 到 {fmt_number(vals[-1])}">'
            f'<path d="{path}" class="line"/><circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="3" class="end"/>'
            f'{dots}</svg>')


def change_cell(p: float | None) -> str:
    cls = "" if not p else (' class="up"' if p > 0 else ' class="down"')
    return f"<td{cls}>{fmt_pct(p)}</td>"


def trend_to_html_section(trend: dict) -> str:
    dd, wd = trend["daily_dates"], trend["weekly_dates"]
    parts = [f'<section><h2>趋势与风险提示 <small>日度近 {len(dd)} 个交易日 · 周度近 {len(wd)} 周</small></h2>',
             f'<p class="note">{html.escape(MEANING_DOWN)}<br>{html.escape(MEANING_UP)}<br>'
             f'{html.escape(MEANING_COVER)}<br>'
             '以下由固定规则自动判断，只说明数据变化，不构成买卖建议。走势线上的点可以用鼠标悬停查看日期和数值。</p>']
    flagged = flagged_products(trend)
    if flagged:
        parts.append('<ul class="alerts">')
        for p in flagged:
            items = "".join(f'<li><span class="lvl lvl-{lvl}">{lvl}</span> {arrow} {html.escape(txt)}</li>'
                            for lvl, arrow, txt in p["alerts"])
            parts.append(f"<li><b>{html.escape(display_name(p))} {p['code']}</b>"
                         f"（{p['exchange']}，{html.escape(p['unit'])}）<ul>{items}</ul></li>")
        parts.append("</ul>")
    else:
        parts.append("<p>没有品种触发风险规则。</p>")
    for exchange in ("上期所", "上期能源"):
        items = [p for p in trend["products"] if p["exchange"] == exchange and p.get("daily")]
        if not items:
            continue
        parts.append(f"<h3>{exchange}</h3><div class='wrap'><table><thead><tr>"
                     "<th>品种</th><th>代码</th><th>单位</th><th>最新仓单</th><th>日增减</th><th>5日变化</th>"
                     f"<th>近 {len(dd)} 日仓单走势</th><th>周度指标</th><th>本周</th><th>周变化</th>"
                     f"<th>近 {len(wd)} 周走势</th><th>后备货</th><th>后备货周变化</th></tr></thead><tbody>")
        for p in items:
            s = p["daily_stats"]
            ws = p.get("weekly_stats")
            parts.append(
                f"<tr><td>{html.escape(display_name(p))}</td><td>{p['code']}</td><td>{html.escape(p['unit'])}</td>"
                f"<td>{fmt_number(s['latest'])}</td>{change_cell(pct(s['latest'], s['p1']))}"
                f"{change_cell(pct(s['latest'], s['p5']))}"
                f"<td>{sparkline(p['daily'], p['unit'])}</td>"
                f"<td>{p['weekly_label'] if ws else '—'}</td><td>{fmt_number(ws['latest']) if ws else '—'}</td>"
                f"{change_cell(pct(ws['latest'], ws['prev']) if ws else None)}"
                f"<td>{sparkline(p['weekly'], p['unit']) if ws else ''}</td>"
                f"<td>{fmt_number(p['backup'][0]) if p.get('backup') else '—'}</td>"
                f"{change_cell(pct(*p['backup']) if p.get('backup') else None)}</tr>")
        parts.append("</tbody></table></div>")
    parts.append("</section>")
    return "\n".join(parts)


# ---------------------------------------------------------------- 跨月价差参考（仓单覆盖率 × 历史百分位）

# 做跨月价差统计的品种（用户指定）
SPREAD_CODES = ["au", "al", "ao", "rb", "hc", "ru", "pb", "zn", "sn", "cu", "nr", "br"]
# 每手数量和单位，取自上期所官网 product_config.dat 的 contract_number / unit_cn
LOT_SIZE = {"au": (1000, "克"), "al": (5, "吨"), "ao": (20, "吨"), "rb": (10, "吨"), "hc": (10, "吨"),
            "ru": (10, "吨"), "pb": (5, "吨"), "zn": (5, "吨"), "sn": (1, "吨"), "cu": (5, "吨"), "nr": (10, "吨"), "br": (5, "吨")}
# 这几个品种只有 1、5、10 月（天然橡胶 1、5、9 月）的合约成交活跃（用户指定）：近月 / 下月从这几个月份里取，
# 如 10 月份时是 2701 / 2705；「同类日子」按离近月交割月还有几天来找，不按月内日期
MAIN_MONTHS = {"rb": (1, 5, 10), "hc": (1, 5, 10), "ru": (1, 5, 9)}
# 这两个品种跳过当前月份之后的第一个月：近月 / 下月 = 往后第 2、3 个月（如 10 月份时是 2612 / 2701；用户指定）
PAIR_AHEAD = {"nr": (2, 3), "br": (2, 3)}
# 这些品种的价差用「下月 − 近月」（远减近；用户指定），其余品种是「近月 − 下月」
FAR_MINUS_NEAR = {"rb", "hc", "ru", "nr", "br"}
CODE_TO_NAME = {code: name for name, code in PRODUCT_CODES.items()}
SPREAD_RULES = {
    "百分位邻域": 10,  # 覆盖率历史百分位上下 10 个百分点以内算"同样的百分位"
    "日期邻域": 5,     # 月内日期前后 5 天以内算"离交割差不多远"
    "最少样本": 20,    # 同类样本少于 20 天，标「参考性弱」
    "成交少": 100,     # 近月或下月当天成交少于 100 手，标「成交少」
}
HISTORY_DIR = CACHE / "历史"
SPREAD_SUMMARY = CACHE / "价差历史汇总.json"
SPREAD_SUMMARY_CODES = CACHE / "价差历史汇总_品种.txt"  # 汇总里存了哪些品种；品种变了要重算（只读本地缓存）
SHFE_OLD_STOCK_URL = "https://www.shfe.com.cn/data/tradedata/future/dailydata/{date}dailystock.dat"
SHFE_KX_URL = "https://www.shfe.com.cn/data/tradedata/future/dailydata/kx{date}.dat"
NEW_FORMAT_FROM = dt.date(2025, 11, 1)  # 大约从这时起仓单日报换成新格式，之前是旧格式
OLD_UNITS = {"1": "千克", "2": "吨", "3": "桶"}


def fetch_gz(url: str, path: Path, valid) -> str | None:
    """下载后压缩保存（约省 90% 空间）；下载过的直接读本地。交易所没有这个文件时返回 None。"""
    if path.exists():
        return gzip.decompress(path.read_bytes()).decode("utf-8")
    status, text = http_get(url, referer=SHFE_PAGE)
    if status == 200 and valid(text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(text.encode("utf-8")))
        return text
    return None


def warrants_old(text: str) -> dict[str, tuple[float, str]]:
    """旧格式仓单日报（JSON）：每个品种的仓单总数，仓库 + 厂库相加。"""
    groups: dict[str, list] = {}
    for r in json.loads(text).get("o_cursor") or []:
        groups.setdefault(str(r.get("VARNAME", "")).split("$$")[0].strip(), []).append(r)
    out: dict[str, tuple[float, str]] = {}
    for name, rows in groups.items():
        code = PRODUCT_CODES.get(re.sub(r"(仓库|厂库)$", "", name))
        if code not in LOT_SIZE:
            continue
        label = lambda r, k: str(r.get(k) or "").split("$$")[0].strip()
        total = [r for r in rows if label(r, "WHABBRNAME") == "总计"]
        if total:
            v = float(total[0].get("WRTWGHTS") or 0)
        else:
            v = sum(float(r.get("WRTWGHTS") or 0) for r in rows
                    if "计" not in label(r, "REGNAME") + label(r, "WHABBRNAME"))
        unit = OLD_UNITS.get(str(rows[0].get("WGHTUNIT")), "")
        out[code] = (out.get(code, (0.0, unit))[0] + v, unit)
    return out


def warrants_new(text: str) -> dict[str, tuple[float, str]]:
    """新格式仓单日报（HTML）：沿用上面的表格解析。"""
    out = {}
    for base, m in product_metrics(parse_stock_html(text), "dailystock").items():
        code = PRODUCT_CODES.get(base)
        if code in LOT_SIZE and m["warrant"] is not None:
            out[code] = (m["warrant"], m["unit"])
    return out


def quotes_from_kx(text: str, codes=LOT_SIZE) -> dict[str, dict[str, list]]:
    """每日行情：{代码: {交割月: [结算价, 持仓量, 成交量]}}，只要期货。codes=None 表示所有品种。"""
    out: dict[str, dict[str, list]] = {}
    for x in json.loads(text).get("o_curinstrument") or []:
        code = str(x.get("PRODUCTGROUPID", "")).strip()
        month = str(x.get("DELIVERYMONTH", "")).strip()
        if (codes is not None and code not in codes) or not code or not month.isdigit()                 or str(x.get("PRODUCTCLASS")) != "1":
            continue
        out.setdefault(code, {})[month] = [to_number(str(x.get(k, ""))) for k in
                                           ("SETTLEMENTPRICE", "OPENINTEREST", "VOLUME")]
    return out


def day_summary(d: dt.date) -> dict | None:
    """某个交易日的仓单 + 各合约行情。交易所没有数据时返回 None；网络出错会抛 RuntimeError。"""
    ds = d.strftime("%Y%m%d")
    kx = fetch_gz(SHFE_KX_URL.format(date=ds), HISTORY_DIR / f"kx_{ds}.dat.gz",
                  lambda t: "o_curinstrument" in t)
    if kx is None:
        return None
    recent = CACHE / f"dailystock_{ds}.html"
    if recent.exists():
        w = warrants_new(recent.read_text(encoding="utf-8"))
    else:
        new = lambda: fetch_gz(report_url("dailystock", d), HISTORY_DIR / f"dailystock_{ds}.html.gz",
                               lambda t: "el-table_table" in t)
        old = lambda: fetch_gz(SHFE_OLD_STOCK_URL.format(date=ds), HISTORY_DIR / f"dailystock_{ds}.dat.gz",
                               lambda t: '"o_cursor"' in t)
        w = None
        for fmt, getter, parse in ((("new", new, warrants_new), ("old", old, warrants_old))
                                   if d >= NEW_FORMAT_FROM else
                                   (("old", old, warrants_old), ("new", new, warrants_new))):
            text = getter()
            if text:
                w = parse(text)
                break
        if w is None:
            return None
    q = quotes_from_kx(kx)
    return {code: {"w": w[code][0], "unit": w[code][1], "q": q.get(code, {})} for code in LOT_SIZE if code in w}


def update_spread_history(latest: dt.date, years: int) -> dict[str, dict]:
    """补齐回看期内每个交易日的数据（只下载缺的），返回 {日期: 当天数据}。"""
    summary: dict[str, dict | None] = {}
    if SPREAD_SUMMARY.exists():
        summary = json.loads(SPREAD_SUMMARY.read_text(encoding="utf-8"))
    codes_key = ",".join(sorted(LOT_SIZE))
    old_codes = SPREAD_SUMMARY_CODES.read_text(encoding="utf-8").strip() if SPREAD_SUMMARY_CODES.exists() else ""
    if summary and old_codes != codes_key:
        log("  品种有变化，按本地缓存重算价差历史汇总……")
        summary = {k: v for k, v in summary.items() if v is None}
    start = latest - dt.timedelta(days=round(365.25 * years))
    days = [start + dt.timedelta(days=i) for i in range((latest - start).days + 1)]
    todo = [d for d in days if is_trading_day(d) and d.strftime("%Y%m%d") not in summary]
    if todo:
        log(f"  需要补 {len(todo)} 个交易日的仓单和行情（第一次会比较久，之后只补新的）……")
    failed = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(day_summary, d): d for d in todo}
        for i, job in enumerate(as_completed(jobs), 1):
            d = jobs[job]
            try:
                res = job.result()
            except RuntimeError:
                failed += 1  # 网络问题：不记录，下次再试
                continue
            if res is not None:
                summary[d.strftime("%Y%m%d")] = res
            elif (dt.date.today() - d).days > 3:
                summary[d.strftime("%Y%m%d")] = None  # 交易所确实没有这天的数据，以后不再重试
            if i % 50 == 0 or i == len(todo):
                log(f"    进度 {i}/{len(todo)}")
                write_text(SPREAD_SUMMARY, json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    write_text(SPREAD_SUMMARY, json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    write_text(SPREAD_SUMMARY_CODES, codes_key)
    if failed:
        log(f"  ⚠ 有 {failed} 天因为网络问题没取到，下次运行会自动补")
    return {k: v for k, v in summary.items() if v and start <= parse_date(k) <= latest}


def month_code(d: dt.date, ahead: int) -> str:
    """d 所在月份往后 ahead 个月的合约月份，如 2026-10 往后 1 个月 → 2611。"""
    m = d.month - 1 + ahead
    return f"{(d.year + m // 12) % 100:02d}{m % 12 + 1:02d}"


def spread_pair(code: str, d: dt.date) -> tuple[str, str]:
    """d 这天用来算跨月价差的近月、下月。MAIN_MONTHS 里的品种只在活跃月份里取（2026-10 → 2701 / 2705）。"""
    months = MAIN_MONTHS.get(code)
    if not months:
        a, b = PAIR_AHEAD.get(code, (1, 2))
        return month_code(d, a), month_code(d, b)
    picks = []
    ahead = 0
    while len(picks) < 2:
        ahead += 1
        mc = month_code(d, ahead)
        if int(mc[2:]) in months:
            picks.append(mc)
    return picks[0], picks[1]


def spread_phase(code: str, d: dt.date, near: str) -> int:
    """找「同类日子」用的时段：普通品种 = 月内日期；MAIN_MONTHS 品种 = 离近月交割月 1 号还有几天。"""
    if code not in MAIN_MONTHS:
        return d.day
    return (dt.date(2000 + int(near[:2]), int(near[2:]), 1) - d).days


def phase_text(code: str, cur: dict) -> str:
    if code not in MAIN_MONTHS:
        return f"过去每月 {cur['date'].day} 号前后 {SPREAD_RULES['日期邻域']} 天"
    return (f"过去离近月交割月同样远（今天距 {code}{cur['near']} 交割月还有 {cur['phase']} 天）"
            f"前后 {SPREAD_RULES['日期邻域']} 天")


def pair_spread(code: str, near: float, far: float) -> float:
    return far - near if code in FAR_MINUS_NEAR else near - far


def pair_text(code: str, cur: dict) -> str:
    """价差的算法，如「cu2611 − cu2612」「rb2705 − rb2701」。"""
    a, b = (cur["far"], cur["near"]) if code in FAR_MINUS_NEAR else (cur["near"], cur["far"])
    return f"{code}{a} − {code}{b}"


def spread_rows(history: dict[str, dict], code: str) -> list[dict]:
    lot, lot_unit = LOT_SIZE[code]
    rows = []
    for ds in sorted(history):
        day = history[ds].get(code)
        if not day:
            continue
        d = parse_date(ds)
        near, far = spread_pair(code, d)
        row = {"date": d, "near": near, "far": far, "phase": spread_phase(code, d, near),
               "warrant": day["w"], "unit": day["unit"]}
        row["lots"] = day["w"] * (1000 if day["unit"] == "千克" and lot_unit == "克" else 1) / lot
        qn, qf = day["q"].get(row["near"]), day["q"].get(row["far"])
        if qn and qf and qn[0] and qf[0]:
            row.update(near_settle=qn[0], far_settle=qf[0], near_oi=qn[1], near_vol=qn[2], far_vol=qf[2],
                       spread=pair_spread(code, qn[0], qf[0]), spread_pct=pair_spread(code, qn[0], qf[0]) / qf[0] * 100,
                       coverage=row["lots"] / qn[1] * 100 if qn[1] else None)
        rows.append(row)
    return rows


def percentile(vals: list[float], q: float) -> float:
    s = sorted(vals)
    k = (len(s) - 1) * q / 100
    lo = int(k)
    return s[lo] + (s[min(lo + 1, len(s) - 1)] - s[lo]) * (k - lo)


def coverage_alerts(code: str, hist: list[dict], cur: dict, res: dict) -> list[tuple[str, str, str]]:
    """近月覆盖率跟过去每月同一时段比：排名越低 = 仓单相对近月持仓越少。"""
    R, S = RISK_RULES, SPREAD_RULES
    same = sorted(r["coverage"] for r in hist
                  if r["date"] < cur["date"] and r["near"] != cur["near"]
                  and abs(r["phase"] - cur["phase"]) <= S["日期邻域"])
    if len(same) < S["最少样本"]:
        return []
    rank = bisect.bisect_right(same, cur["coverage"]) / len(same) * 100
    res.update(same_rank=rank, same_n=len(same), same_median=percentile(same, 50))
    if cur["coverage"] >= R["覆盖率_上限"]:
        return []
    lvl = "高" if rank <= R["覆盖率同期_高"] else "中" if rank <= R["覆盖率同期_中"] else None
    if not lvl:
        return []
    lower = "比全部同期日子都低" if rank == 0 else f"比 {100 - rank:.0f}% 的同期日子都低"
    return [(lvl, "↓", f"近月覆盖率（{code}{cur['near']}）：{cur['coverage']:.1f}%"
             f"（仓单 {cur['lots']:.0f} 手 ÷ 近月持仓 {fmt_number(cur['near_oi'])} 手），"
             f"跟{phase_text(code, cur)}的 {len(same)} 个交易日比，{lower}"
             f"（同期中位数 {res['same_median']:.1f}%）")]


def analyze_spread(code: str, rows: list[dict]) -> dict:
    res = {"code": code, "name": CODE_TO_NAME.get(code, code), "rows": rows}
    if not rows:
        res["status"] = "无数据"
        return res
    cur = rows[-1]
    res["cur"] = cur
    if "spread" not in cur:
        res["status"] = "none"  # 近月或下月合约不存在
        return res
    priced = [r for r in rows if "spread" in r]
    res.update(hist_max=max(priced, key=lambda r: r["spread"]), hist_min=min(priced, key=lambda r: r["spread"]))
    hist = [r for r in rows if r.get("coverage") is not None]
    if cur.get("coverage") is None or len(hist) < 2:
        res["status"] = "近月持仓为 0"
        return res
    covs = sorted(r["coverage"] for r in hist)
    for r in hist:
        r["rank"] = bisect.bisect_right(covs, r["coverage"]) / len(covs) * 100
    R = SPREAD_RULES
    # 只看覆盖率：覆盖率排名相近的日子（不管月内日期）；同类 = 在此基础上再要求月内日期相近
    # （MAIN_MONTHS 品种是离近月交割月的天数相近）。都不含今天这组近月合约的日子（普通品种即本月）
    cov_peers = [r for r in hist
                 if r["near"] != cur["near"]
                 and abs(r["rank"] - cur["rank"]) <= R["百分位邻域"]]
    peers = [r for r in cov_peers if abs(r["phase"] - cur["phase"]) <= R["日期邻域"]]
    res.update(status="ok", rank=cur["rank"], n_hist=len(hist), peers=peers, cov_peers=cov_peers,
               cov_lo=min(r["coverage"] for r in cov_peers) if cov_peers else None,
               cov_hi=max(r["coverage"] for r in cov_peers) if cov_peers else None)
    res["alerts"] = coverage_alerts(code, hist, cur, res)
    if cov_peers:
        cp = [r["spread_pct"] for r in cov_peers]
        res.update({f"cy{q}": percentile(cp, q) / 100 * cur["far_settle"] for q in (10, 50, 90)})
    notes = []
    if peers:
        pcts = [r["spread_pct"] for r in peers]
        p10, p50, p90 = (percentile(pcts, q) for q in (10, 50, 90))
        to_yuan = lambda p: p / 100 * cur["far_settle"]
        res.update(p10=p10, p50=p50, p90=p90, y10=to_yuan(p10), y50=to_yuan(p50), y90=to_yuan(p90),
                   peer_pos=sum(1 for v in pcts if v < cur["spread_pct"]) / len(pcts) * 100)
        dear = "下月" if code in FAR_MINUS_NEAR else "近月"
        if cur["spread_pct"] > p90:
            res["verdict"] = f"偏高（{dear}比历史同类情况更贵）"
        elif cur["spread_pct"] < p10:
            res["verdict"] = f"偏低（{dear}比历史同类情况更便宜）"
        else:
            res["verdict"] = "在区间内"
        if len(peers) < R["最少样本"]:
            notes.append("样本少，参考性弱")
    else:
        res["verdict"] = "没有可比的历史日子"
    if min(cur.get("near_vol") or 0, cur.get("far_vol") or 0) < R["成交少"]:
        notes.append("成交少，价差可能失真")
    res["notes"] = notes
    return res


PRICE_DECIMALS = {"au": 2, "sc": 1, "ec": 1}  # 报价带小数的品种（最小变动价位：黄金 0.02、原油 0.1、集运 0.1）


def fmt_price(v: float | None, code: str, signed: bool = False) -> str:
    if v is None:
        return "—"
    s = f"{v:{'+' if signed else ''}.{PRICE_DECIMALS.get(code, 0)}f}"
    return s.lstrip("+-") if float(s) == 0 else s


def peer_pos_text(r: dict) -> str:
    return "—" if r.get("peer_pos") is None else f"{r['peer_pos']:.0f}%"


def extreme_text(r: dict, key: str) -> str:
    """历史最高 / 最低价差，带日期，如「+2340（2024-05-20）」。"""
    x = r.get(key)
    return f"{fmt_price(x['spread'], r['code'], True)}（{x['date']:%Y-%m-%d}）" if x else "—"


def cov_range_text(r: dict) -> str:
    """只看覆盖率的价差区间，带上覆盖率范围和样本天数。"""
    if r.get("cy10") is None:
        return "—"
    c = r["code"]
    return (f"{fmt_price(r['cy10'], c, True)} ~ {fmt_price(r['cy90'], c, True)}"
            f"（覆盖率 {r['cov_lo']:.1f}%~{r['cov_hi']:.1f}%，{len(r['cov_peers'])} 天）")


def build_spread(latest: dt.date, years: int) -> tuple[list[dict], dict]:
    history = update_spread_history(latest, years)
    results = [analyze_spread(code, spread_rows(history, code)) for code in SPREAD_CODES]
    dates = sorted(history)
    span = (parse_date(dates[0]), parse_date(dates[-1]), len(dates)) if dates else None
    return results, {"span": span, "years": years}


SPREAD_EXPLAIN = [
    "**覆盖率** = 仓单（换算成手）÷ 近月合约持仓。覆盖率低 = 能交的货相对等着交割的合约少，近月更容易被挤。",
    "**近月 / 下月**：当前月份之后最近的交割月 / 再往后一个月（如 10 月份时是 2611 / 2612）。合约不存在写 none。"
    "螺纹钢、热轧卷板只在 1、5、10 月合约里取，天然橡胶只在 1、5、9 月合约里取（如 10 月份时是 2701 / 2705）；"
    "20号胶、丁二烯橡胶跳过最近的一个月，用往后第 2、3 个月（如 10 月份时是 2612 / 2701）；"
    "这三个品种的「月内日期相近」换成「离近月交割月的天数相近」。",
    "**价差** = 近月结算价 − 下月结算价，正数 = 近月比下月贵；螺纹钢、热轧卷板、天然橡胶、20号胶、丁二烯橡胶反过来用"
    "下月结算价 − 近月结算价（远减近），正数 = 下月比近月贵。「价差算法」一列写着每个品种怎么减。",
    "**历史最高 / 最低**：回看期内每天的价差（按上面的算法，用当天的近月和下月合约）里最大和最小的一天，括号里是日期。"
    "不同年份价格水平不一样，元数直接比只能大概参考。",
    "**覆盖率排名**：今天的覆盖率在回看期里排第几（20% = 比 80% 的日子都低）。它只说覆盖率，跟价差无关。",
    "**价差在同类中的位置**：现在的价差比多少「同类日子」的价差高。100% = 比所有同类日子都高，0% = 比所有都低。",
    "**历史同类区间**：在回看期里找覆盖率排名相差不超过 {pct} 个百分点、而且月内日期相差不超过 {day} 天的日子"
    "（不含本月），把那些日子的价差按「占下月价格的百分比」统计，取中间 80%（第 10~90 百分位），再按今天的下月价格换算成元。",
    "**只看覆盖率的区间**：和上面一样算，但不管月内日期，只要覆盖率排名相差不超过 {pct} 个百分点就算（括号里是这些日子的覆盖率范围和天数）。"
    "样本更多，但混进了离交割远近不同的日子；判断「偏高 / 偏低」仍以「历史同类区间」为准。",
    "这是历史统计，不是预测；行情环境变了，区间也会失效。",
]


def spread_to_markdown(results: list[dict], meta: dict) -> str:
    span = meta["span"]
    lines = ["# 跨月价差参考：仓单覆盖率 × 历史同类情况", ""]
    if span:
        lines += [f"- 回看期：{span[0]:%Y-%m-%d} ~ {span[1]:%Y-%m-%d}，共 {span[2]} 个交易日（数据来自上期所仓单日报和每日行情）。"]
    R = SPREAD_RULES
    lines += [f"- {t.format(pct=R['百分位邻域'], day=R['日期邻域'])}" for t in SPREAD_EXPLAIN] + [""]
    lines += ["| 品种 | 代码 | 价差算法 | 仓单(手) | 近月持仓(手) | 覆盖率 | 覆盖率排名 | 现在价差 | 历史最高（日期） | 历史最低（日期） | "
              "价差在同类中的位置 | 历史同类区间（中间80%） | 同类中位数 | 样本天数 | 只看覆盖率的区间（中间80%） | 判断 | 备注 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        c = r["code"]
        cur = r.get("cur")
        if r["status"] != "ok":
            pair = pair_text(c, cur) if cur else "—"
            lines.append(f"| {r['name']} | {c} | {pair} | — | — | — | — | none | {extreme_text(r, 'hist_max')} | "
                         f"{extreme_text(r, 'hist_min')} | — | — | — | — | — | — | {r['status']} |")
            continue
        rng = f"{fmt_price(r['y10'], c, True)} ~ {fmt_price(r['y90'], c, True)}" if "y10" in r else "—"
        lines.append(
            f"| {r['name']} | {c} | {pair_text(c, cur)} | {cur['lots']:.0f} | {fmt_number(cur['near_oi'])} | "
            f"{cur['coverage']:.1f}% | {r['rank']:.0f}% | {fmt_price(cur['spread'], c, True)} | "
            f"{extreme_text(r, 'hist_max')} | {extreme_text(r, 'hist_min')} | {peer_pos_text(r)} | {rng} | "
            f"{fmt_price(r.get('y50'), c, True)} | {len(r['peers'])} | {cov_range_text(r)} | {r['verdict']} | {'；'.join(r['notes']) or '—'} |")
    lines += ["", "单位：价差为元/吨（黄金为元/克）。", "", "## 近月覆盖率风险提示", "",
              "跟过去每月同一时段比，覆盖率偏低就提示（规则见「仓储趋势」文件末尾；这些提示也已并入那里的风险提示）。", ""]
    alerts = [(r, a) for r in results for a in r.get("alerts", [])]
    lines += [f"- {r['name']} {r['code']}：【{lvl}】{arrow} {txt}" for r, (lvl, arrow, txt) in alerts] or ["- 没有品种触发。"]
    lines.append("")
    return "\n".join(lines)


def spread_to_csv(path: Path, results: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["日期", "代码", "近月", "下月", "仓单", "仓单单位", "仓单(手)", "近月持仓(手)", "覆盖率%",
                    "近月结算价", "下月结算价", "价差（rb、hc、ru、nr、br 为下月−近月，其余为近月−下月）", "价差占下月%", "近月成交", "下月成交"])
        for r in results:
            for row in r["rows"]:
                g = lambda k, fmt="{:g}": fmt.format(row[k]) if row.get(k) is not None else ""
                w.writerow([row["date"].strftime("%Y-%m-%d"), r["code"], row["near"], row["far"], g("warrant"),
                            row["unit"], g("lots", "{:.0f}"), g("near_oi"), g("coverage", "{:.2f}"),
                            g("near_settle"), g("far_settle"), g("spread"), g("spread_pct", "{:.3f}"),
                            g("near_vol"), g("far_vol")])


def spread_to_html_section(results: list[dict], meta: dict) -> str:
    span = meta["span"]
    R = SPREAD_RULES
    explain = "<br>".join(html.escape(re.sub(r"\*\*", "", t.format(pct=R["百分位邻域"], day=R["日期邻域"])))
                          for t in SPREAD_EXPLAIN)
    parts = [f"<section><h2>跨月价差参考 <small>{span[0]:%Y-%m-%d} ~ {span[1]:%Y-%m-%d} · {span[2]} 个交易日</small></h2>"
             if span else "<section><h2>跨月价差参考</h2>",
             f'<p class="note">{explain}</p><div class="wrap"><table><thead><tr>'
             "<th>品种</th><th>代码</th><th>价差算法</th><th>覆盖率</th><th>覆盖率排名</th><th>现在价差</th>"
             "<th>历史最高（日期）</th><th>历史最低（日期）</th><th>价差在同类中的位置</th><th>同类区间（中间80%）</th><th>同类中位数</th><th>样本</th><th>只看覆盖率的区间（中间80%）</th><th>判断</th><th>备注</th></tr></thead><tbody>"]
    for r in results:
        c = r["code"]
        cur = r.get("cur")
        if r["status"] != "ok":
            pair = pair_text(c, cur) if cur else "—"
            parts.append(f"<tr><td>{r['name']}</td><td>{c}</td><td>{pair}</td><td>—</td><td>—</td><td>none</td>"
                         f"<td>{extreme_text(r, 'hist_max')}</td><td>{extreme_text(r, 'hist_min')}</td>"
                         f"<td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>{html.escape(r['status'])}</td></tr>")
            continue
        rng = f"{fmt_price(r['y10'], c, True)} ~ {fmt_price(r['y90'], c, True)}" if "y10" in r else "—"
        cls = ' class="up"' if r["verdict"].startswith("偏高") else ' class="down"' if r["verdict"].startswith("偏低") else ""
        parts.append(
            f"<tr><td>{r['name']}</td><td>{c}</td><td>{pair_text(c, cur)}</td>"
            f"<td>{cur['coverage']:.1f}%</td><td>{r['rank']:.0f}%</td><td>{fmt_price(cur['spread'], c, True)}</td>"
            f"<td>{extreme_text(r, 'hist_max')}</td><td>{extreme_text(r, 'hist_min')}</td>"
            f"<td{cls}>{peer_pos_text(r)}</td><td>{rng}</td><td>{fmt_price(r.get('y50'), c, True)}</td><td>{len(r['peers'])}</td>"
            f"<td>{html.escape(cov_range_text(r))}</td><td{cls}>{html.escape(r['verdict'])}</td><td>{html.escape('；'.join(r['notes']) or '—')}</td></tr>")
    parts.append("</tbody></table></div>"
                 '<p class="note">价差单位：元/吨（黄金为元/克）。逐日明细见 output/仓储/日期/ 里的「价差历史」CSV。</p></section>')
    return "\n".join(parts)


def write_spread_outputs(latest: dt.date, years: int, built=None) -> tuple[list[Path], str]:
    results, meta = built or build_spread(latest, years)
    folder = OUTPUT / "仓储" / f"{latest:%Y-%m-%d}"
    stamp = latest.strftime("%Y%m%d")
    md = folder / f"跨月价差_{stamp}.md"
    write_text(md, spread_to_markdown(results, meta))
    csv_path = folder / f"价差历史_{stamp}.csv"
    spread_to_csv(csv_path, results)
    ok = sum(1 for r in results if r["status"] == "ok")
    log(f"  跨月价差：{ok}/{len(results)} 个品种算出区间 → {md.relative_to(ROOT)}")
    return [md, csv_path], spread_to_html_section(results, meta)


# ---------------------------------------------------------------- 品种历史查询（指定代码 + 时间段）

SHFE_OLD_WEEKLY_URL = "https://www.shfe.com.cn/data/tradedata/future/weeklydata/{date}weeklystock.dat"
HISTORY_OUT = OUTPUT / "历史查询"


def _old_label(r: dict, key: str) -> str:
    return str(r.get(key) or "").split("$$")[0].strip()


def old_metrics(text: str, kind: str, stock_bases: set[str]) -> dict[str, dict]:
    """旧格式（JSON，约 2025-11 以前）日报 / 周报 → 和 product_metrics 一样的按品种汇总，仓库 + 厂库相加。
    周报字段：SPOTWGHTS = 小计，WRTWGHTS = 期货（仓单），WHSTOCKS = 可用库容（与新格式逐个核对过）。"""
    j = json.loads(text)
    groups: dict[str, list] = {}
    for key in ("o_cursor", "o_cursorine", "o_cursorfu"):
        for r in j.get(key) or []:
            groups.setdefault(_old_label(r, "VARNAME"), []).append(r)
    out: dict[str, dict] = {}
    for name, rows in groups.items():
        base = re.sub(r"[(（]?(仓库|厂库)[)）]?$", "", name).strip()
        code = PRODUCT_CODES.get(base)
        if not code:
            continue
        total = [r for r in rows if _old_label(r, "WHABBRNAME") == "总计"]
        use = total[:1] or [r for r in rows if "计" not in _old_label(r, "REGNAME") + _old_label(r, "WHABBRNAME")]

        def num(k: str) -> float | None:
            got = [r.get(k) for r in use if str(r.get(k, "")).strip() != ""]
            return sum(float(v) for v in got) if got else None

        m = out.setdefault(base, {"code": code, "unit": OLD_UNITS.get(str(rows[0].get("WGHTUNIT")), ""),
                                  "warrant": None, "stock": None, "backup": None, "capacity": None, "parts": []})
        m["parts"].append(name)
        vals = {"warrant": num("WRTWGHTS"), "stock": None, "capacity": None}
        if kind == "weeklystock":
            vals["capacity"] = num("WHSTOCKS")
            if base in stock_bases and "厂库" not in name:  # 只有新格式里也有「小计」的品种才用
                vals["stock"] = num("SPOTWGHTS")
        for k, v in vals.items():
            if v is not None:
                m[k] = (m[k] or 0) + v
        if vals["stock"] is not None and vals["warrant"] is not None:
            m["backup"] = (m["backup"] or 0) + vals["stock"] - vals["warrant"]
    for r in j.get("o_cursorau") or []:  # 周报里黄金单独一张表，只有仓单
        out["黄金"] = {"code": "au", "unit": OLD_UNITS.get(str(r.get("WGHTUNIT")), "千克"),
                     "warrant": float(r.get("WRTWGHTS") or 0), "stock": None, "backup": None,
                     "capacity": None, "parts": ["黄金"]}
    return out


def history_metrics(kind: str, d: dt.date, stock_bases: set[str]) -> dict[str, dict] | None:
    """某天的日报 / 周报按品种汇总。下载过的读本地；交易所没有这天返回 None。"""
    ds = d.strftime("%Y%m%d")

    def new() -> dict | None:
        recent = CACHE / f"{kind}_{ds}.html"
        text = recent.read_text(encoding="utf-8") if recent.exists() else fetch_gz(
            report_url(kind, d), HISTORY_DIR / f"{kind}_{ds}.html.gz", lambda t: "el-table_table" in t)
        return product_metrics(parse_stock_html(text), kind) if text else None

    def old() -> dict | None:
        url = (SHFE_OLD_STOCK_URL if kind == "dailystock" else SHFE_OLD_WEEKLY_URL).format(date=ds)
        text = fetch_gz(url, HISTORY_DIR / f"{kind}_{ds}.dat.gz", lambda t: '"o_cursor"' in t)
        return old_metrics(text, kind, stock_bases) if text else None

    for getter in ((new, old) if d >= NEW_FORMAT_FROM else (old, new)):
        m = getter()
        if m:
            return m
    return None


def collect_history(start: dt.date, end: dt.date, stock_bases: set[str]) -> tuple[dict, dict, int]:
    """返回（日报 {日期: 汇总}，周报 {日期: 汇总}，缺数据的交易日数）。每周取该周最后一个有周报的交易日。"""
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    trading = [d for d in days if is_trading_day(d)]
    weeks: dict[tuple, list[dt.date]] = {}
    for d in trading:
        weeks.setdefault(d.isocalendar()[:2], []).append(d)
    daily, weekly = {}, {}
    log(f"  需要 {len(trading)} 个交易日的日报、{len(weeks)} 期周报（下载过的直接读本地）……")
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(history_metrics, "dailystock", d, stock_bases): ("d", d) for d in trading}
        jobs.update({pool.submit(history_metrics, "weeklystock", ds[-1], stock_bases): ("w", ds[-1])
                     for ds in weeks.values()})
        for i, job in enumerate(as_completed(jobs), 1):
            which, d = jobs[job]
            m = job.result()
            if m:
                (daily if which == "d" else weekly)[d] = m
            if i % 100 == 0:
                log(f"    进度 {i}/{len(jobs)}")
    for ds in weeks.values():  # 周末那天没有周报（如节假日调休），往前找同一周的
        if ds[-1] not in weekly:
            for d in reversed(ds[:-1]):
                m = history_metrics("weeklystock", d, stock_bases)
                if m:
                    weekly[d] = m
                    break
    return daily, dict(sorted(weekly.items())), len(trading) - len(daily)


def series_stats(points: list[tuple[dt.date, float]]) -> list[str]:
    """起点、终点、变化、最高（日期）、最低（日期）。"""
    if not points:
        return ["—"] * 5
    (d0, v0), (d1, v1) = points[0], points[-1]
    hi = max(points, key=lambda x: x[1])
    lo = min(points, key=lambda x: x[1])
    return [f"{fmt_number(v0)}（{d0:%Y-%m-%d}）", f"{fmt_number(v1)}（{d1:%Y-%m-%d}）", fmt_chg(v1, v0),
            f"{fmt_number(hi[1])}（{hi[0]:%Y-%m-%d}）", f"{fmt_number(lo[1])}（{lo[0]:%Y-%m-%d}）"]


def history_product(base: str, daily: dict, weekly: dict) -> dict:
    pick = lambda src, key: [(d, m[base][key]) for d, m in sorted(src.items())
                             if base in m and m[base].get(key) is not None]
    info = next((m[base] for _, m in sorted(daily.items(), reverse=True) if base in m), None) or \
        next((m[base] for _, m in sorted(weekly.items(), reverse=True) if base in m), None)
    parts = sorted({p for src in (daily, weekly) for m in src.values() if base in m for p in m[base]["parts"]})
    return {"base": base, "code": PRODUCT_CODES.get(base, ""), "unit": info["unit"] if info else "",
            "combined": any("厂库" in p for p in parts),
            "daily": pick(daily, "warrant"), "w_warrant": pick(weekly, "warrant"), "w_stock": pick(weekly, "stock"),
            "w_backup": pick(weekly, "backup"), "w_capacity": pick(weekly, "capacity"),
            "weeks": [d for d, m in sorted(weekly.items()) if base in m],
            "wmap": {d: m[base] for d, m in weekly.items() if base in m}}


def history_sections(p: dict) -> list[tuple[str, list[tuple[dt.date, float]]]]:
    stock_label = "库存小计（周报，仅仓库）" if p["combined"] else "库存小计（周报）"
    return [("仓单（日报）", p["daily"]), (stock_label, p["w_stock"]),
            ("后备货 = 小计 − 仓单（周报）", p["w_backup"]), ("可用库容（周报）", p["w_capacity"])]


def history_to_markdown(products: list[dict], start: dt.date, end: dt.date, missing: int) -> str:
    lines = [f"# 仓储历史查询（{start:%Y-%m-%d} ~ {end:%Y-%m-%d}）", "",
             "- 数据来源：上期所官网仓单日报、库存周报（约 2025-11 以前是旧格式，脚本已核对新旧格式在重叠日期完全一致）。",
             "- 同一品种的仓库和厂库已相加（品种名后注明）；厂库没有「小计」，所以库存小计和后备货只算仓库。",
             f"- 回看期内交易所缺数据的交易日：{missing} 天。" if missing else "- 回看期内每个交易日都有日报。",
             "- 涨跌幅 = 增减 ÷ 上一期数量。只是数据整理，不构成买卖建议。", ""]
    for p in products:
        name = p["base"] + ("（仓库+厂库）" if p["combined"] else "")
        lines += [f"## {name} {p['code']}" + (f"（单位：{p['unit']}）" if p["unit"] else ""), ""]
        if not p["daily"] and not p["weeks"]:
            lines += ["这段时间没有这个品种的数据。", ""]
            continue
        lines += ["| 指标 | 起点 | 终点 | 变化 | 区间最高 | 区间最低 |", "|---|---|---|---|---|---|"]
        lines += [f"| {label} | " + " | ".join(series_stats(pts)) + " |" for label, pts in history_sections(p) if pts]
        lines += ["", "### 逐日仓单（新的在上）", "", "| 日期 | 仓单 | 增减 | 涨跌幅 |", "|---|---|---|---|"]
        pts = p["daily"]
        for i in range(len(pts) - 1, -1, -1):
            d, v = pts[i]
            prev = pts[i - 1][1] if i else None
            chg = None if prev is None else v - prev
            lines.append(f"| {d:%Y-%m-%d} | {fmt_number(v)} | {fmt_signed(chg)} | {pct_of_change(chg, v)} |")
        if p["weeks"]:
            lines += ["", "### 逐周库存（新的在上）", "",
                      "| 周报日期 | 库存小计 | 小计增减 | 仓单 | 仓单增减 | 后备货 | 后备货增减 | 可用库容 |",
                      "|---|---|---|---|---|---|---|---|"]
            ws = p["weeks"]
            for i in range(len(ws) - 1, -1, -1):
                m, pm = p["wmap"][ws[i]], (p["wmap"][ws[i - 1]] if i else {})
                cells = []
                for key in ("stock", "warrant", "backup"):
                    v, pv = m.get(key), pm.get(key)
                    cells.append("—" if v is None else fmt_number(v))
                    cells.append("—" if v is None or pv is None else f"{fmt_signed(v - pv)}（{pct_of_change(v - pv, v)}）")
                cells.append("—" if m.get("capacity") is None else fmt_number(m["capacity"]))
                lines.append(f"| {ws[i]:%Y-%m-%d} | " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


def history_to_csv(folder: Path, stamp: str, products: list[dict]) -> list[Path]:
    made = []
    for p in products:
        if not p["daily"] and not p["weeks"]:
            continue
        path = folder / f"历史_{p['code']}_日度_{stamp}.csv"
        folder.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["日期", f"仓单（{p['unit']}）"])
            w.writerows([[d.strftime("%Y-%m-%d"), fmt_number(v)] for d, v in p["daily"]])
        made.append(path)
        if p["weeks"]:
            path = folder / f"历史_{p['code']}_周度_{stamp}.csv"
            with path.open("w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["周报日期", "库存小计", "仓单", "后备货", "可用库容"])
                for d in p["weeks"]:
                    m = p["wmap"][d]
                    w.writerow([d.strftime("%Y-%m-%d")] + ["" if m.get(k) is None else fmt_number(m[k])
                                                           for k in ("stock", "warrant", "backup", "capacity")])
            made.append(path)
    return made


def history_to_html(products: list[dict], start: dt.date, end: dt.date, missing: int) -> str:
    parts = [f'<p class="note">{start:%Y-%m-%d} ~ {end:%Y-%m-%d}。仓库和厂库已相加；库存小计、后备货只算仓库。'
             f'走势线上的点可以用鼠标悬停看日期和数值。{"缺数据的交易日：" + str(missing) + " 天。" if missing else ""}'
             "逐日 / 逐周明细见同名 .md 文件和 CSV。</p>"]
    for p in products:
        name = p["base"] + ("（仓库+厂库）" if p["combined"] else "")
        parts.append(f"<section><h2>{html.escape(name)} {p['code']} <small>{'单位：' + html.escape(p['unit']) if p['unit'] else ''}</small></h2>")
        if not p["daily"] and not p["weeks"]:
            parts.append("<p>这段时间没有这个品种的数据。</p></section>")
            continue
        parts.append("<div class='wrap'><table><thead><tr><th>指标</th><th>走势</th><th>起点</th><th>终点</th>"
                     "<th>变化</th><th>区间最高</th><th>区间最低</th></tr></thead><tbody>")
        for label, pts in history_sections(p):
            if pts:
                s = series_stats(pts)
                parts.append(f"<tr><td>{html.escape(label)}</td><td>{sparkline(pts, p['unit'], 360, 70)}</td>"
                             + "".join(f"<td>{html.escape(x)}</td>" for x in s) + "</tr>")
        parts.append("</tbody></table></div></section>")
    return "\n".join(parts)


def run_history(args) -> list[Path]:
    bases = resolve_products(args.products)
    unknown = [b for b in bases if b not in PRODUCT_CODES]
    if unknown:
        raise RuntimeError(f"不认识的品种：{'、'.join(unknown)}。可以写代码（如 al、cu）或中文名（如 铝、铜）")
    log("正在确定最新交易日……")
    # 哪些品种的周报有「小计」：以最新一期周报为准（旧格式里别的品种这一栏是空的或 0）
    _, _, wtext = fetch_stock("weeklystock", dt.date.today(), 14)
    stock_bases = {t["base"] for t in parse_stock_html(wtext)["tables"] if f"本周库存{SEP}小计" in t["columns"]}
    if args.end:
        end = parse_date(args.end)
        while not is_trading_day(end):
            end -= dt.timedelta(days=1)
    else:
        end, _, _ = fetch_stock("dailystock", dt.date.today(), 10)
    start = parse_date(args.start) if args.start else end - dt.timedelta(days=args.days)
    if start > end:
        raise RuntimeError(f"开始日期 {start} 晚于结束日期 {end}")
    log(f"正在整理 {'、'.join(bases)} 的仓储历史（{start:%Y-%m-%d} ~ {end:%Y-%m-%d}）……")
    daily, weekly, missing = collect_history(start, end, stock_bases)
    if not daily and not weekly:
        raise RuntimeError("这段时间一份报表都没取到，可能是网络问题，请稍后再试")
    products = [history_product(b, daily, weekly) for b in bases]
    stamp = f"{'_'.join(p['code'] for p in products)}_{start:%Y%m%d}_{end:%Y%m%d}"
    md = HISTORY_OUT / f"历史_{stamp}.md"
    write_text(md, history_to_markdown(products, start, end, missing))
    page = HISTORY_OUT / f"历史_{stamp}.html"
    write_text(page, HTML_PAGE.format(date=f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d}",
                                      now=f"{dt.datetime.now():%Y-%m-%d %H:%M}",
                                      sections=history_to_html(products, start, end, missing))
               .replace("<title>仓储速览", "<title>仓储历史")
               .replace("<h1>上期所 / 上期能源 仓储速览</h1>",
                        f"<h1>仓储历史：{html.escape('、'.join(p['base'] for p in products))}</h1>"))
    made = [md, page] + history_to_csv(HISTORY_OUT, f"{start:%Y%m%d}_{end:%Y%m%d}", products)
    log(f"  {len(daily)} 个交易日日报 + {len(weekly)} 期周报 → {md.relative_to(ROOT)}")
    if args.open:
        webbrowser.open(page.as_uri())
    return made


HTML_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>仓储速览 {date}</title>
<style>
body {{ font-family: "Microsoft YaHei", system-ui, sans-serif; margin: 24px; color: #1f2328; background: #fff; }}
h1 {{ font-size: 22px; }} h2 {{ margin-top: 32px; font-size: 18px; }} h2 small {{ color: #666; font-weight: normal; font-size: 13px; }}
h3 {{ font-size: 15px; margin: 18px 0 6px; }}
.src {{ color: #666; font-size: 12px; }}
.wrap {{ overflow-x: auto; }}
table {{ border-collapse: collapse; font-size: 13px; margin-bottom: 10px; }}
th, td {{ border: 1px solid #d0d7de; padding: 4px 8px; text-align: right; white-space: nowrap; }}
th {{ background: #f6f8fa; }} td:nth-child(-n+4), th:nth-child(-n+4) {{ text-align: left; }}
td.up {{ color: #cf222e; }} td.down {{ color: #1a7f37; }}
.note {{ color: #666; font-size: 12px; }}
.warn {{ color: #9a6700; background: #fff8c5; padding: 8px; font-size: 13px; }}
.spark {{ display: block; overflow: visible; }}
.spark .line {{ fill: none; stroke: #0969da; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }}
.spark .end {{ fill: #0969da; stroke: #fff; stroke-width: 2; }}
.spark .hit {{ fill: transparent; }} .spark .hit:hover {{ fill: #0969da33; }}
td:has(.spark) {{ padding: 2px 6px; }}
ul.alerts {{ padding-left: 18px; font-size: 13px; line-height: 1.7; }} ul.alerts ul {{ padding-left: 16px; }}
.lvl {{ display: inline-block; min-width: 2.4em; text-align: center; border-radius: 4px; padding: 0 4px; font-size: 12px; color: #fff; }}
.lvl-高 {{ background: #cf222e; }} .lvl-中 {{ background: #bf8700; }} .lvl-提示 {{ background: #6e7781; }}
.tabs {{ display: flex; gap: 4px; border-bottom: 1px solid #d0d7de; margin-top: 12px; }}
.tabs button {{ font: inherit; font-size: 15px; padding: 6px 18px; border: 1px solid transparent; border-bottom: none;
  background: none; color: #57606a; cursor: pointer; border-radius: 6px 6px 0 0; margin-bottom: -1px; }}
.tabs button.on {{ border-color: #d0d7de; background: #fff; color: #1f2328; font-weight: bold; }}
table.wraptext td {{ white-space: normal; min-width: 4em; max-width: 36em; text-align: left; }}
details.prod, details.factor {{ border: 1px solid #d0d7de; border-radius: 6px; margin: 8px 0; padding: 6px 12px; }}
details.prod details.factor {{ border-color: #eaeef2; }}
details > summary {{ cursor: pointer; font-weight: bold; }} details > summary small {{ color: #666; font-weight: normal; }}
.mech {{ color: #57606a; font-size: 13px; background: #f6f8fa; padding: 6px 8px; border-radius: 4px; }}
ol.news {{ font-size: 13px; line-height: 1.6; padding-left: 22px; }} .meta {{ color: #666; }}
.snip {{ color: #57606a; font-size: 12px; }}
.ai {{ border-left: 3px solid #8250df; background: #fbefff; padding: 6px 10px; margin: 8px 0; font-size: 13px; line-height: 1.7; }}
.flag {{ color: #cf222e; font-weight: normal; }}
.tag {{ display: inline-block; font-size: 12px; padding: 0 6px; border-radius: 4px; background: #8250df; color: #fff; }}
.dir {{ display: inline-block; font-size: 12px; padding: 0 6px; border-radius: 4px; border: 1px solid #8c959f; color: #57606a; }}
.dir-变大 {{ color: #cf222e; border-color: #cf222e; }} .dir-变小 {{ color: #1a7f37; border-color: #1a7f37; }}
.chips a {{ display: inline-block; margin: 2px 4px 2px 0; padding: 1px 10px; border: 1px solid #d0d7de;
  border-radius: 12px; font-size: 13px; text-decoration: none; }}
</style></head><body>
<h1>上期所 / 上期能源 仓储速览</h1>
<p class="note">生成时间 {now}。数字由脚本直接从交易所官网解析；变化红色为增加、绿色为减少。完整分仓库明细和历史数据见 output/仓储/日期/ 里的 CSV 文件。</p>
{sections}
</body></html>
"""


COVERAGE_HEADS = ["品种", "代码", "近月合约", "仓单(手)", "近月持仓(手)", "近月覆盖率", "同期排名", "同期中位数", "风险提示"]
COVERAGE_NOTE = ("近月覆盖率 = 仓单（换算成手）÷ 近月合约持仓，越高越不容易被挤。月中低于 100% 很常见（很多持仓到月底前会平仓或换月），"
                 "所以跟自己过去 3 年「每月同一时段」（月内日期前后 {day} 天，不含本月；螺纹钢、热轧卷板、天然橡胶的近月是 1/5/10 或 1/5/9 月合约，改成离近月交割月天数前后 {day} 天、不含同一个近月合约）比：同期排名 = 有多少同期日子的覆盖率不高于今天，"
                 "0% = 同期最低；同期中位数 = 同期日子覆盖率的中间值。排名 ≤{hi}% 提示【高】，≤{mid}% 提示【中】。")


def coverage_rows(results: list[dict]) -> list[list[str]]:
    rows = []
    for r in results:
        c, cur = r["code"], r.get("cur")
        if r["status"] != "ok" or not cur:
            rows.append([r["name"], c, f"{c}{cur['near']}" if cur else "—", "—", "—", "—", "—", "—", r["status"]])
            continue
        lvl = "、".join(f"【{a[0]}】" for a in r.get("alerts", [])) or "—"
        rank = "—" if r.get("same_rank") is None else f"{r['same_rank']:.0f}%"
        med = "—" if r.get("same_median") is None else f"{r['same_median']:.1f}%"
        rows.append([r["name"], c, f"{c}{cur['near']}", f"{cur['lots']:.0f}", fmt_number(cur["near_oi"]),
                     f"{cur['coverage']:.1f}%", rank, med, lvl])
    return rows


def coverage_note() -> str:
    R = RISK_RULES
    return COVERAGE_NOTE.format(day=SPREAD_RULES["日期邻域"], hi=R["覆盖率同期_高"], mid=R["覆盖率同期_中"])


def coverage_to_markdown(results: list[dict]) -> str:
    lines = [f"## 近月覆盖率（{'、'.join(SPREAD_CODES)}）", "", coverage_note(), "",
             "| " + " | ".join(COVERAGE_HEADS) + " |", "|" + "---|" * len(COVERAGE_HEADS)]
    lines += ["| " + " | ".join(row) + " |" for row in coverage_rows(results)]
    return "\n".join(lines + [""])


def coverage_to_html(results: list[dict]) -> str:
    body = "".join("<tr>" + "".join(f"<td>{html.escape(x)}</td>" for x in row) + "</tr>" for row in coverage_rows(results))
    return (f"<h3>近月覆盖率</h3><p class='note'>{html.escape(coverage_note())}</p><div class='wrap'><table><thead><tr>"
            + "".join(f"<th>{h}</th>" for h in COVERAGE_HEADS) + f"</tr></thead><tbody>{body}</tbody></table></div>")


def add_coverage_alerts(trend: dict, results: list[dict]) -> None:
    by_code = {p["code"]: p for p in trend["products"]}
    for r in results:
        p = by_code.get(r["code"])
        if p and r.get("alerts"):
            p["alerts"] = sorted(p["alerts"] + r["alerts"], key=lambda a: LEVEL_ORDER[a[0]])


def run_stock(args) -> list[Path]:
    start = parse_date(args.date)
    wanted = resolve_products(args.products)
    made: list[Path] = []
    sections = []
    latest: dict[str, dt.date] = {}
    fetched = []
    for kind, max_back in (("dailystock", 10), ("weeklystock", 14)):
        name = REPORTS[kind]
        log(f"正在获取{name}……")
        day, url, text = fetch_stock(kind, start, max_back)
        parsed = parse_stock_html(text)
        if not parsed["tables"]:
            raise RuntimeError(f"{name}：页面格式可能变了，没解析出任何表格（{url}）")
        latest[kind] = day
        fetched.append((kind, day, url, parsed))

    # 先算跨月价差：里面的近月覆盖率要写进日报，风险提示要并进趋势文件
    spread_built = None
    if SPREAD_SUMMARY.exists():
        log("正在更新跨月价差参考……")
        spread_built = build_spread(latest["dailystock"], args.years)

    for kind, day, url, parsed in fetched:
        name = REPORTS[kind]
        folder = OUTPUT / "仓储" / f"{day:%Y-%m-%d}"
        stamp = day.strftime("%Y%m%d")
        md = folder / f"{name}_{stamp}.md"
        problems = [msg for t in parsed["tables"] for msg in check_totals(t)]
        text = stock_to_markdown(kind, day, url, parsed, wanted, problems)
        section = stock_to_html_section(kind, day, url, parsed, problems)
        if kind == "dailystock" and spread_built:
            text += "\n" + coverage_to_markdown(spread_built[0])
            section = section.removesuffix("</section>") + coverage_to_html(spread_built[0]) + "</section>"
        write_text(md, text)
        csv_path = folder / f"{name}_{stamp}_分仓库明细.csv"
        stock_to_csv(csv_path, parsed)
        made += [md, csv_path]
        sections.append(section)
        log(f"  {name}：{day:%Y-%m-%d}，{len(parsed['tables'])} 张品种表 → {md.relative_to(ROOT)}")
        for msg in problems:
            log(f"  ⚠ 自检：{msg}")

    if args.history_days > 1:
        log(f"正在整理历史：近 {args.history_days} 个交易日仓单、近 {args.history_weeks} 周库存"
            "（第一次需要下载，以后只下载新的）……")
        daily_hist = fetch_history("dailystock", latest["dailystock"], args.history_days)
        weekly_hist = fetch_history("weeklystock", latest["weeklystock"], args.history_weeks) \
            if args.history_weeks > 1 else []
        for d, p in daily_hist[:-1] + weekly_hist[:-1]:
            for t in p["tables"]:
                for msg in check_totals(t):
                    log(f"  ⚠ 自检（{d:%Y-%m-%d}）：{msg}")
        trend = build_trend(daily_hist, weekly_hist)
        if spread_built:
            add_coverage_alerts(trend, spread_built[0])
        folder = OUTPUT / "仓储" / f"{latest['dailystock']:%Y-%m-%d}"
        stamp = latest["dailystock"].strftime("%Y%m%d")
        md = folder / f"仓储趋势_{stamp}.md"
        write_text(md, trend_to_markdown(trend, wanted))
        trend_to_csv(folder / f"仓单历史_日度_{stamp}.csv", trend, "daily")
        made += [md, folder / f"仓单历史_日度_{stamp}.csv"]
        if weekly_hist:
            trend_to_csv(folder / f"库存历史_周度_{stamp}.csv", trend, "weekly")
            made.append(folder / f"库存历史_周度_{stamp}.csv")
        sections.insert(0, trend_to_html_section(trend))
        n_alert = sum(1 for p in trend["products"] if p["alerts"])
        log(f"  {len(daily_hist)} 个交易日 + {len(weekly_hist)} 周，{n_alert} 个品种有风险提示 → {md.relative_to(ROOT)}")

    if spread_built:
        files, section = write_spread_outputs(latest["dailystock"], args.years, spread_built)
        made += files
        sections.insert(1 if args.history_days > 1 else 0, section)
    else:
        log("  （提示：运行一次 python futures_brief.py spread 可生成跨月价差参考，之后每次自动更新）")

    save_stock_part(f"{latest['dailystock']:%Y-%m-%d}", "\n".join(sections))
    page = build_page("stock")
    made.append(page)
    log(f"  网页版 → {page.relative_to(ROOT)}")
    if args.open:
        webbrowser.open(page.as_uri())
    return made


def run_spread(args) -> list[Path]:
    log("正在确定最新交易日……")
    day, _, _ = fetch_stock("dailystock", parse_date(args.date), 10)
    log(f"正在生成跨月价差参考（截至 {day:%Y-%m-%d}，回看 {args.years} 年）……")
    files, _ = write_spread_outputs(day, args.years)
    log("  网页版会在下次运行 stock（或双击 .bat）时一起显示。")
    return files


# ---------------------------------------------------------------- 新闻

EM_SEARCH = "https://search-api-web.eastmoney.com/search/jsonp"


def search_eastmoney(keyword: str, page_size: int, page: int = 1, sort: str = "time") -> list[dict]:
    """sort="time" 按时间倒序；"default" 按相关度（冷门词更容易搜到标题里真有这个词的新闻）。"""
    param = {
        "uid": "", "keyword": keyword, "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": sort,
                                       "pageIndex": page, "pageSize": page_size, "preTag": "", "postTag": ""}},
    }
    url = EM_SEARCH + "?cb=cb&param=" + urllib.parse.quote(json.dumps(param, ensure_ascii=False))
    status, text = http_get(url, referer="https://so.eastmoney.com/")
    if status != 200 or "(" not in text:
        raise RuntimeError(f"东方财富搜索失败（HTTP {status}）")
    data = json.loads(text[text.index("(") + 1:text.rindex(")")])
    items = (data.get("result") or {}).get("cmsArticleWebOld") or []
    out = []
    for a in items:
        out.append({
            "time": a.get("date", ""),
            "source": a.get("mediaName", ""),
            "title": html.unescape(re.sub("<[^>]+>", "", a.get("title", ""))).strip(),
            "snippet": html.unescape(re.sub("<[^>]+>", "", a.get("content", ""))).strip(),
            "url": (a.get("url") or "").replace("http://", "https://"),
            "keyword": keyword,
        })
    return out


def run_news(args) -> list[Path]:
    if not args.products:
        raise SystemExit("news 需要至少一个品种，例如：python futures_brief.py news 铜 原油")
    now = dt.datetime.now()
    since = now - dt.timedelta(days=args.days)
    folder = OUTPUT / "新闻" / f"{now:%Y-%m-%d}"
    made: list[Path] = []
    for typed in args.products:
        # 输入代码（如 au）时换成中文名再搜
        product = PRODUCT_ALIASES.get(typed.lower(), typed) if typed.isascii() else typed
        keywords = NEWS_ALIASES.get(product, [product])
        log(f"正在搜索「{product}」新闻（关键词：{'、'.join(keywords)}）……")
        seen_url, seen_title, items = set(), set(), []
        errors = []
        for kw in keywords:
            try:
                results = search_eastmoney(kw, page_size=max(args.limit * 2, 20))
            except RuntimeError as e:
                errors.append(str(e))
                continue
            for it in results:
                try:
                    t = dt.datetime.strptime(it["time"][:19], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if t < since:
                    continue
                title_key = re.sub(r"\W", "", it["title"])
                if it["url"] in seen_url or title_key in seen_title:
                    continue
                seen_url.add(it["url"])
                seen_title.add(title_key)
                items.append(it)
        items.sort(key=lambda x: x["time"], reverse=True)
        items = items[:args.limit]

        lines = [
            f"# 「{product}」近 {args.days} 天新闻列表",
            "",
            f"- 搜索关键词：{'、'.join(keywords)}（来源：东方财富资讯搜索，按时间倒序，已去重）",
            f"- 抓取时间：{now:%Y-%m-%d %H:%M}，共 {len(items)} 条",
            "- 这是原始列表，没有经过 AI 筛选；摘要栏是网站给的正文开头。",
            "",
        ]
        if errors:
            lines += [f"> 部分关键词搜索失败：{'；'.join(errors)}", ""]
        for i, it in enumerate(items, 1):
            snippet = it["snippet"][:160] + ("…" if len(it["snippet"]) > 160 else "")
            lines += [
                f"## {i}. {it['title']}",
                f"- 时间：{it['time']}　来源：{it['source'] or '—'}",
                f"- 链接：{it['url']}",
                f"- 摘要：{snippet or '—'}",
                "",
            ]
        safe = re.sub(r'[\\/:*?"<>|]', "_", product)
        md = folder / f"新闻_{safe}.md"
        write_text(md, "\n".join(lines))
        write_text(folder / f"新闻_{safe}.json", json.dumps(items, ensure_ascii=False, indent=2))
        made.append(md)
        log(f"  {len(items)} 条 → {md.relative_to(ROOT)}")
    return made


# ---------------------------------------------------------------- 影响因素

FACTOR_LIST = ROOT / "因素清单.json"
FACTOR_OUT = OUTPUT / "因素"
FACTOR_LATEST = FACTOR_OUT / "最新数据.json"
FACTOR_AI = FACTOR_OUT / "AI解读.json"            # AI 写：各品种的解读
FACTOR_EVENTS = FACTOR_OUT / "长期事件.json"       # AI 写：持续几周到几个月的大事
PAGE_STOCK_PART = CACHE / "页面片段_仓储.json"     # 仓储标签页的内容，拼网页时用
FACTOR_SPREAD_DAYS = 20                            # 价差回看多少个交易日
FACTOR_PAGES = 2                                   # 每个搜索词先取几页新闻（每页 50 条）
FACTOR_MAX_PAGES = 20                              # 热门话题几页还不够 7 天时，最多翻到第几页（搜索最多给 1000 条）
FACTOR_RELEVANT_PAGES = 2                          # 另外按相关度取几页：冷门词按时间排会被大量不相关的新闻挤掉
FACTOR_YEARS = 3                                   # 价差历史最高 / 最低看几年
ALL_SPREAD_SUMMARY = CACHE / "全品种价差历史.json"  # 每天的近月 − 下月价差（全部品种），算历史极值用
EXPIRE_PREV_MONTH = ("sc", "lu", "fu")             # 这几个品种在交割月的前一个月月底就停止交易（其它品种是交割月 15 日前后）
NEAR_EXPIRY_DAYS = 5                               # 近月合约剩下几个交易日以内算「临近到期」
LINK_CHECK = CACHE / "链接核对.json"                # AI 引用链接的核对结果（打得开的记下来，下次不用再查）
DIRECTIONS = ("价差变大", "价差变小", "不明确")       # AI解读里「指向」只能写这三种
PUBLIC_KINDS = ("宏观", "政策", "地缘")             # 这几类因素影响面广，网页里单独放在「公共因素」
FACTOR_EXPLAIN = [
    "**价差** = 近月结算价 − 下月结算价。正数 = 近月比下月贵（反向结构，常说明眼前的货紧）；"
    "负数 = 近月比下月便宜（正向结构，常见于货充足、或囤货成本高的时候）。",
    "**近月 / 下月**：当前月份之后第一个 / 第二个挂牌的合约（如 10 月份时多数品种是 2611 / 2612；"
    "天然橡胶没有 12 月合约，是 2611 / 2701）。回看期内一直用今天这两个合约比较，不换月。",
    "**原油、低硫燃料油、燃料油**的合约在交割月的前一个月月底就停止交易（如 2611 合约 10 月底到期），"
    f"剩下 {NEAR_EXPIRY_DAYS} 个交易日以内会在备注里写「临近到期」：这时成交和持仓变少，价差容易失真。",
    f"**{FACTOR_YEARS} 年最高 / 最低**：过去 {FACTOR_YEARS} 年每天的「近月 − 下月」价差（用当天的近月和下月合约）里最大和最小的一天；"
    f"**在 {FACTOR_YEARS} 年里的位置**：现在的价差比这 {FACTOR_YEARS} 年里百分之多少的日子高（100% = 最高）。"
    "不同年份价格水平不一样，元数直接比只能大概参考。",
    "**通常怎么影响价差**：行业里常见的说法，未经验证，不同时期可能不灵。",
    "新闻按「因素清单.json」里的搜索词搜，再要求标题里有「必含」词，仍可能有不相关的条目。",
]


def load_factor_list() -> tuple[dict[str, str], list[dict]]:
    """读 因素清单.json，返回（{代码: 品种名}, 因素列表）。"""
    try:
        cfg = json.loads(FACTOR_LIST.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RuntimeError(f"找不到 {FACTOR_LIST.name}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"{FACTOR_LIST.name} 格式有误（第 {e.lineno} 行）：{e.msg}")
    names = {k.lower(): v for k, v in (cfg.get("品种") or {}).items()}
    factors = []
    for i, f in enumerate(cfg.get("因素") or []):
        codes = list(names) if f.get("品种") == "全部" else [c.lower() for c in f.get("品种") or []]
        unknown = [c for c in codes if c not in names]
        if unknown:
            raise RuntimeError(f"{FACTOR_LIST.name}：「{f.get('名称')}」里的 {'、'.join(unknown)} 不在「品种」列表里")
        if not f.get("名称") or not f.get("搜索词"):
            raise RuntimeError(f"{FACTOR_LIST.name}：第 {i + 1} 个因素缺少「名称」或「搜索词」")
        factors.append({**f, "品种": codes, "id": f"k{i + 1}"})
    return names, factors


def resolve_factor_codes(typed: list[str], names: dict[str, str]) -> list[str]:
    if not typed:
        return list(names)
    by_name = {v: k for k, v in names.items()}
    out = []
    for t in typed:
        c = t.lower()
        if c not in names:
            c = by_name.get(t) or PRODUCT_CODES.get(PRODUCT_ALIASES.get(t, t), "")
        if c not in names:
            raise RuntimeError(f"因素清单里没有「{t}」。可以写代码（如 cu、sc）或中文名（如 铜、原油）")
        if c not in out:
            out.append(c)
    return out


def news_matches(it: dict, groups: list[list[str]], exclude: list[str]) -> bool:
    """「必含」：每一组里至少有一个词出现在标题里（摘要常是整篇正文的开头，什么词都可能有，不看）；
    「排除」：标题里有这些词就不要。"""
    text = it["title"].lower()
    return all(any(w.lower() in text for w in g) for g in groups) and not any(w.lower() in text for w in exclude)


def news_time(it: dict) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(it["time"][:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, KeyError):
        return None


def collect_factor_news(factors: list[dict], days: int, limit: int) -> list[str]:
    """给每个因素填上「新闻」；返回失败的搜索。
    热门话题（如伊朗）一页 50 条可能只够一天，所以先取 FACTOR_PAGES 页，还没翻到 days 天前的再往后翻，
    最多到 FACTOR_MAX_PAGES 页；仍然不够的，在因素里记下「只搜到哪天以后的」。
    另外每个词按相关度取 FACTOR_RELEVANT_PAGES 页：搜索是模糊匹配（「双胶纸」也会搜出只含「纸」的新闻），
    按时间排时冷门词的新闻会被大量不相关的挤掉。"""
    since = dt.datetime.now() - dt.timedelta(days=days)
    queries = sorted({q for f in factors for q in f["搜索词"]})
    log(f"正在按 {len(factors)} 个因素搜索新闻（共 {len(queries)} 个搜索词）……")
    results: dict[str, list] = {}
    pages: dict[str, int] = {}  # 每个搜索词实际取到第几页
    errors = []

    timed: dict[str, list] = {}  # 只有按时间排的结果，用来判断翻没翻到 days 天前

    def fetch(todo: list[tuple[str, int, str]]) -> None:
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = {pool.submit(search_eastmoney, q, 50, page, sort): (q, page, sort) for q, page, sort in todo}
            for job in as_completed(jobs):
                q, page, sort = jobs[job]
                try:
                    got = job.result()
                except (RuntimeError, ValueError) as e:
                    errors.append(f"{q}（{e}）")
                    continue
                results.setdefault(q, []).extend(got)
                if sort == "time":
                    timed.setdefault(q, []).extend(got)
                    pages[q] = max(pages.get(q, 0), page)

    def not_enough(q: str) -> bool:
        """取满了每一页、最旧的一条还在 days 天以内 = 更早的还没搜到。"""
        times = [t for t in map(news_time, timed.get(q, [])) if t]
        return len(timed.get(q, [])) >= 50 * pages.get(q, 0) > 0 and bool(times) and min(times) >= since

    fetch([(q, page, "time") for q in queries for page in range(1, FACTOR_PAGES + 1)]
          + [(q, page, "default") for q in queries for page in range(1, FACTOR_RELEVANT_PAGES + 1)])
    for page in range(FACTOR_PAGES + 1, FACTOR_MAX_PAGES + 1):
        more = [q for q in queries if pages.get(q) == page - 1 and not_enough(q)]
        if not more:
            break
        fetch([(q, page, "time") for q in more])
    errors = sorted(set(errors))
    if queries and not results:
        raise RuntimeError("新闻搜索全部失败：" + errors[0])
    short = {q: min(t for t in map(news_time, timed[q]) if t) for q in queries if not_enough(q)}
    for f in factors:
        seen_url, seen_title, items = set(), set(), []
        for q in f["搜索词"]:
            for it in results.get(q, []):
                t = news_time(it)
                if t is None:
                    continue
                title_key = re.sub(r"\W", "", it["title"])
                if t < since or not news_matches(it, f.get("必含") or [], f.get("排除") or []) \
                        or it["url"] in seen_url or title_key in seen_title:
                    continue
                seen_url.add(it["url"])
                seen_title.add(title_key)
                items.append(it)
        items.sort(key=lambda x: x["time"], reverse=True)
        f["命中"] = len(items)
        f["新闻"] = items[:limit]
        cut = [short[q] for q in f["搜索词"] if q in short]
        if cut:  # 至少有一个搜索词没翻到 days 天前：只能说搜到了这个时间以后的
            f["只搜到"] = f"{max(cut):%m-%d %H:%M}"
    return errors


def kx_quotes(d: dt.date) -> dict | None:
    ds = d.strftime("%Y%m%d")
    text = fetch_gz(SHFE_KX_URL.format(date=ds), HISTORY_DIR / f"kx_{ds}.dat.gz", lambda t: "o_curinstrument" in t)
    return quotes_from_kx(text, None) if text else None


def factor_spreads(codes: list[str], names: dict[str, str]) -> tuple[dict, str, list[str]]:
    """每个品种近月 − 下月价差和近 FACTOR_SPREAD_DAYS 个交易日的变化。返回（{代码: 结果}, 数据日期, 警告）。"""
    log(f"正在整理近 {FACTOR_SPREAD_DAYS} 个交易日的近月 − 下月价差……")
    days, warns = [], []
    d, misses = dt.date.today(), 0
    while len(days) < FACTOR_SPREAD_DAYS + 1 and misses < 15:  # 多取 1 天，才算得出「20 日变化」
        if is_trading_day(d):
            try:
                q = kx_quotes(d)
            except RuntimeError as e:
                warns.append(f"行情下载失败：{e}")
                break
            if q:
                days.append((d, q))
                misses = 0
            else:
                misses += 1
        d -= dt.timedelta(days=1)
    if not days:
        return {}, "", warns + ["近期行情一份都没取到，价差无数据"]
    days.reverse()
    latest, lq = days[-1]
    left = days_left_in_month(latest)
    out = {}
    for code in codes:
        near, far = front_pair(lq.get(code, {}), latest)
        r = {"code": code, "name": names[code], "near": near, "far": far}
        qn, qf = lq.get(code, {}).get(near), lq.get(code, {}).get(far)
        if not (qn and qf and qn[0] and qf[0]):
            r["status"] = "none"
            out[code] = r
            continue
        series = []
        for day, q in days:
            a, b = q.get(code, {}).get(near), q.get(code, {}).get(far)
            if a and b and a[0] and b[0]:
                series.append((day.strftime("%Y-%m-%d"), round(a[0] - b[0], 4)))
        spread = round(qn[0] - qf[0], 4)
        r.update(status="ok", near_settle=qn[0], far_settle=qf[0], spread=spread,
                 spread_pct=round(spread / qf[0] * 100, 3), near_oi=qn[1], near_vol=qn[2], far_vol=qf[2],
                 series=series, chg5=round(spread - series[-6][1], 4) if len(series) >= 6 else None,
                 chg_n=round(spread - series[0][1], 4) if len(series) >= 2 else None, n=len(series) - 1,
                 notes=["成交少，价差可能失真"] if min(qn[2] or 0, qf[2] or 0) < SPREAD_RULES["成交少"] else [])
        if expiring(code, near, latest, left):
            r["notes"].insert(0, f"临近到期：近月 {near} 本月底停止交易（含今天还剩 {left} 个交易日）")
        out[code] = r
    try:
        add_spread_extremes(out, latest)
    except RuntimeError as e:
        warns.append(f"历史价差没取全，{FACTOR_YEARS} 年最高 / 最低暂缺：{e}")
    return out, latest.strftime("%Y-%m-%d"), warns


def front_pair(months: dict, d: dt.date) -> list:
    """d 这天的近月、下月：当前月份之后第一个、第二个挂牌的合约（没有就是 None）。"""
    ms = sorted(m for m in months if m > month_code(d, 0))
    return (ms + [None, None])[:2]


def days_left_in_month(d: dt.date) -> int:
    """d 所在月份从 d（含）到月底还有几个交易日。
    交易所日历把今天以后的日子都算成非交易日，所以今天以后只按周一到周五数（节假日数不出来）。"""
    n, x, today = 0, d, dt.date.today()
    while x.month == d.month:
        n += is_trading_day(x) if x <= today else x.weekday() < 5
        x += dt.timedelta(days=1)
    return n


def expiring(code: str, near: str | None, d: dt.date, left: int) -> bool:
    """原油等品种的近月合约本月底停止交易，且只剩 NEAR_EXPIRY_DAYS 个交易日以内。"""
    return code in EXPIRE_PREV_MONTH and near == month_code(d, 1) and left <= NEAR_EXPIRY_DAYS


def update_all_spreads(latest: dt.date) -> dict[str, dict]:
    """过去 FACTOR_YEARS 年每个交易日、每个品种的 [近月, 下月, 价差]（只补缺的天），返回 {日期: {代码: …}}。"""
    summary: dict[str, dict | None] = {}
    if ALL_SPREAD_SUMMARY.exists():
        try:
            summary = json.loads(ALL_SPREAD_SUMMARY.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            summary = {}  # 文件坏了：从缓存的行情重新整理一遍（几秒钟）
    start = latest - dt.timedelta(days=round(365.25 * FACTOR_YEARS))
    days = [start + dt.timedelta(days=i) for i in range((latest - start).days + 1)]
    todo = [d for d in days if is_trading_day(d) and d.strftime("%Y%m%d") not in summary]
    if len(todo) > 5:
        log(f"  正在整理 {len(todo)} 个交易日的全品种价差（第一次会久一点，之后只补新的）……")

    def one(d: dt.date) -> dict | None:
        q = kx_quotes(d)
        if q is None:
            return None
        out = {}
        for code, months in q.items():
            near, far = front_pair(months, d)
            a, b = months.get(near), months.get(far)
            if a and b and a[0] and b[0]:
                out[code] = [near, far, round(a[0] - b[0], 4)]
        return out

    failed = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(one, d): d for d in todo}
        for job in as_completed(jobs):
            d = jobs[job]
            try:
                res = job.result()
            except RuntimeError:
                failed += 1  # 网络问题：不记录，下次再试
                continue
            if res is not None:
                summary[d.strftime("%Y%m%d")] = res
            elif (dt.date.today() - d).days > 3:
                summary[d.strftime("%Y%m%d")] = None  # 交易所确实没有这天的数据
    if todo:
        write_text(ALL_SPREAD_SUMMARY, json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    if failed:
        raise RuntimeError(f"有 {failed} 天因为网络问题没取到，下次运行会自动补")
    return {k: v for k, v in summary.items() if v and start <= parse_date(k) <= latest}


def add_spread_extremes(out: dict, latest: dt.date) -> None:
    """给每个品种加上过去 FACTOR_YEARS 年的价差最高、最低（带日期）和现在的位置。
    原油等月底到期的品种，去掉近月最后 NEAR_EXPIRY_DAYS 个交易日（那几天价差容易失真）。"""
    hist = update_all_spreads(latest)
    dates = sorted(hist)
    left = {}  # 每一天：同一个月里从这天（含）到月底还有几个交易日（过去的月份用汇总里的日期数）
    this_month = latest.strftime("%Y%m")
    for ds in dates:
        left[ds] = (days_left_in_month(parse_date(ds)) if ds[:6] == this_month  # 这个月还没过完，汇总里没有后面的日子
                    else sum(1 for x in dates if x[:6] == ds[:6] and x >= ds))
    for code, r in out.items():
        if r.get("status") != "ok":
            continue
        pts = [(ds, hist[ds][code][2]) for ds in dates if code in hist[ds]
               and not expiring(code, hist[ds][code][0], parse_date(ds), left[ds])]
        if len(pts) < 20:
            continue
        hi, lo = max(pts, key=lambda p: p[1]), min(pts, key=lambda p: p[1])
        r["hist"] = {"max": hi[1], "max_date": f"{parse_date(hi[0]):%Y-%m-%d}", "min": lo[1],
                     "min_date": f"{parse_date(lo[0]):%Y-%m-%d}", "n": len(pts),
                     "start": f"{parse_date(pts[0][0]):%Y-%m-%d}",
                     "pos": round(sum(1 for p in pts if p[1] < r["spread"]) / len(pts) * 100, 1)}


def structure_text(spread: float) -> str:
    return "近月贵（反向）" if spread > 0 else "近月便宜（正向）" if spread < 0 else "持平"


def spread_cells(r: dict) -> list[str]:
    """价差总览一行：近月、下月、近月结算、下月结算、价差、结构、5日变化、N日变化、历史最高、历史最低、位置、备注。"""
    c = r["code"]
    if r.get("status") != "ok":
        return [r.get("near") or "none", r.get("far") or "none", "—", "—", "none", "—", "—", "—", "—", "—", "—",
                "近月或下月合约不存在"]
    h = r.get("hist")
    ext = ([f"{fmt_price(h['max'], c, True)}（{h['max_date']}）", f"{fmt_price(h['min'], c, True)}（{h['min_date']}）",
            f"{h['pos']:.0f}%"] if h else ["—", "—", "—"])
    return [r["near"], r["far"], fmt_price(r["near_settle"], c), fmt_price(r["far_settle"], c),
            f"{fmt_price(r['spread'], c, True)}（{r['spread_pct']:+.2f}%）", structure_text(r["spread"]),
            fmt_price(r["chg5"], c, True), fmt_price(r["chg_n"], c, True), *ext, "；".join(r["notes"]) or ""]


def factor_spread_heads(n: int) -> list[str]:
    return ["品种", "代码", "近月", "下月", "近月结算", "下月结算", "价差（占下月价格）", "结构", "5日变化",
            f"{n}日变化", f"{FACTOR_YEARS}年最高（日期）", f"{FACTOR_YEARS}年最低（日期）", f"在{FACTOR_YEARS}年里的位置", "备注"]


def news_line(it: dict) -> str:
    return f"【{it['time'][5:16]} {it['source'] or '—'}】{it['title']}"


def factors_to_markdown(data: dict) -> str:
    names, spreads, factors = data["品种"], data["价差"], data["因素"]
    n = max((r.get("n") or 0 for r in spreads.values()), default=0)
    lines = [f"# 影响因素原始资料（{data['生成时间']} 生成）", "",
             f"- 新闻：近 {data['天数']} 天，东方财富资讯搜索；按「因素清单.json」的搜索词搜、要求标题里有「必含」词，"
             f"每个因素最多 {data['每因素条数']} 条，按时间倒序。没经过 AI 筛选，可能有不相关的。",
             f"- 价差：数据日期 {data['价差日期'] or '无数据'}，上期所 / 上期能源每日行情（结算价）。",
             *[f"- {t}" for t in FACTOR_EXPLAIN], ""]
    for w in data["警告"]:
        lines.append(f"> ⚠ {w}")
    if data["警告"]:
        lines.append("")
    lines += ["## 近月 − 下月价差", "", "| " + " | ".join(factor_spread_heads(n)) + " |",
              "|" + "---|" * len(factor_spread_heads(n))]
    for c in names:
        if c in spreads:
            lines.append(f"| {names[c]} | {c} | " + " | ".join(spread_cells(spreads[c])) + " |")
    lines.append("")

    def factor_block(f: dict, level: str) -> list[str]:
        codes = [c for c in f["品种"] if c in names]
        out = [f"{level} {f['名称']}（{f.get('类别', '')}；影响：{'、'.join(f'{names[c]} {c}' for c in codes)}）", "",
               f"通常怎么影响价差（常见说法，未经验证）：{f.get('通常怎么影响价差', '—')}", ""]
        if f.get("只搜到"):
            out += [f"> 注：搜索词太常见，按时间只翻到 {f['只搜到']}（搜索最多给 1000 条）；已按相关度补搜，"
                    f"更早的新闻可能有遗漏。", ""]
        if not f["新闻"]:
            out += [f"近 {data['天数']} 天无相关新闻。", ""]
        for i, it in enumerate(f["新闻"], 1):
            snippet = it["snippet"][:140] + ("…" if len(it["snippet"]) > 140 else "")
            out += [f"{i}. {news_line(it)}", f"   - 链接：{it['url']}", f"   - 摘要：{snippet or '—'}"]
        return out + [""]

    public = [f for f in factors if f.get("类别") in PUBLIC_KINDS]
    if public:
        lines += ["## 公共因素（宏观、政策、地缘）", ""]
        for f in public:
            lines += factor_block(f, "###")
    for c in names:
        own = [f for f in factors if c in f["品种"] and f.get("类别") not in PUBLIC_KINDS]
        pub = [f["名称"] for f in public if c in f["品种"]]
        lines += [f"## {names[c]} {c}", ""]
        r = spreads.get(c)
        if r and r.get("status") == "ok":
            lines.append(f"价差：{c}{r['near']} − {c}{r['far']} = {fmt_price(r['spread'], c, True)}"
                         f"，{structure_text(r['spread'])}，5 日变化 {fmt_price(r['chg5'], c, True)}，"
                         f"{r['n']} 日变化 {fmt_price(r['chg_n'], c, True)}"
                         + (f"；{FACTOR_YEARS} 年最高 {fmt_price(r['hist']['max'], c, True)}（{r['hist']['max_date']}）、"
                            f"最低 {fmt_price(r['hist']['min'], c, True)}（{r['hist']['min_date']}），"
                            f"现在比 {r['hist']['pos']:.0f}% 的日子高" if r.get("hist") else "")
                         + (f"；{'；'.join(r['notes'])}" if r["notes"] else ""))
        else:
            lines.append("价差：none（近月或下月合约不存在）")
        lines += [f"相关公共因素：{'、'.join(pub) or '无'}（见上方）", ""]
        for f in own:
            lines += factor_block(f, "###")
    return "\n".join(lines)


def load_ai_file(path: Path) -> tuple[dict | None, str]:
    """读 AI 写的 JSON；格式不对时返回错误说明，不让整个网页挂掉。"""
    if not path.exists():
        return None, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("最外层应该是 { }")
        for key in ("品种",):  # AI解读.json 的「品种」必须是 {代码: …}
            if key in data and not isinstance(data[key], dict):
                raise ValueError(f"「{key}」应该是 {{ }}，写成了 {type(data[key]).__name__}")
        if "事件" in data and not isinstance(data["事件"], list):
            raise ValueError("「事件」应该是 [ ]")
        return data, ""
    except (json.JSONDecodeError, ValueError) as e:
        return None, f"{path.name} 格式有误，没有显示：{e}"


def safe_link(url, text: str, tip: str = "") -> str:
    url = str(url or "")
    if not re.match(r"https?://", url):
        return html.escape(text)
    tip = f' title="网页标题：{html.escape(tip, quote=True)}"' if tip else ""
    return f'<a href="{html.escape(url, quote=True)}"{tip} target="_blank" rel="noopener">{html.escape(text)}</a>'


def page_title(text: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    t = html.unescape(m.group(1)).strip() if m else ""
    return re.split(r"\s*[_|]\s*东方财富", t)[0].strip()


def check_links(urls: set[str]) -> dict[str, dict]:
    """真的去打开 AI 引用的链接，看打不打得开、网页标题是什么。
    返回 {链接: {"状态": "ok" / "打不开" / "没核对上", "标题": …, "说明": …}}。
    打得开的和确定打不开的（404 等）记在 LINK_CHECK 里，下次不再查；网络出错的下次再试。"""
    cache: dict[str, dict] = {}
    if LINK_CHECK.exists():
        try:
            cache = json.loads(LINK_CHECK.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cache = {}
    todo = sorted(u for u in urls if u not in cache and re.match(r"https?://", u))
    if not todo:
        return cache

    def one(u: str) -> dict:
        req = urllib.request.Request(u, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return {"状态": "ok", "标题": page_title(resp.read().decode("utf-8", errors="replace"))}
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return {"状态": "打不开", "说明": f"HTTP {e.code}"}
            return {"状态": "没核对上", "说明": f"HTTP {e.code}"}
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            return {"状态": "没核对上", "说明": f"网络问题：{e}"}

    log(f"  正在核对 {len(todo)} 个 AI 引用的链接……")
    with ThreadPoolExecutor(max_workers=6) as pool:
        for u, res in zip(todo, pool.map(one, todo)):
            cache[u] = {**res, "核对时间": f"{dt.datetime.now():%Y-%m-%d %H:%M}"}
    write_text(LINK_CHECK, json.dumps({u: v for u, v in cache.items() if v["状态"] != "没核对上"},
                                      ensure_ascii=False, indent=1))
    return cache


def title_matches(claimed: str, real: str) -> bool:
    """AI 写的标题和网页真实标题是不是同一篇：AI 标题里七成以上的字出现在真实标题里就算。"""
    a = set(re.sub(r"\W", "", re.sub(r"（[^）]*）|\([^)]*\)", "", claimed)))
    b = set(re.sub(r"\W", "", real))
    return not a or not b or len(a & b) / len(a) >= 0.7


def link_html(url, text: str, checks: dict, claimed_title: str = "") -> str:
    """链接 + 核对结果：打不开、或网页标题对不上，标红。"""
    url = str(url or "")
    c = checks.get(url) or {}
    out = safe_link(url, text, c.get("标题", ""))
    if c.get("状态") == "打不开":
        out += f' <b class="flag">⚠ 链接打不开（{html.escape(c.get("说明", ""))}），请核对</b>'
    elif c.get("状态") == "ok" and claimed_title and not title_matches(claimed_title, c.get("标题", "")):
        out += f' <b class="flag">⚠ 网页标题是「{html.escape(c.get("标题", ""))}」，和这里写的不一样，请核对</b>'
    elif c.get("状态") == "没核对上":
        out += f' <span class="note">（没核对上：{html.escape(c.get("说明", "")[:40])}）</span>'
    return out


def ai_links(ai: dict | None, events: dict | None) -> set[str]:
    """AI解读和长期事件里引用的所有链接。"""
    urls = set()
    for e in ((ai or {}).get("品种") or {}).values():
        for it in (e.get("条目") or []) if isinstance(e, dict) else []:
            if isinstance(it, dict) and it.get("链接"):
                urls.add(str(it["链接"]))
    for e in (events or {}).get("事件") or []:
        for l in (e.get("链接") or []) if isinstance(e, dict) else []:
            if isinstance(l, dict) and l.get("链接"):
                urls.add(str(l["链接"]))
    return urls


def direction_html(v) -> str:
    if not v:
        return ""
    if v not in DIRECTIONS:
        return f' <b class="flag">⚠ 「指向」写的是「{html.escape(str(v))}」，只能写 {" / ".join(DIRECTIONS)}</b>'
    return f' <span class="dir dir-{v[-2:]}">指向：{v}</span>'


def news_list_html(items: list[dict], days: int) -> str:
    if not items:
        return f'<p class="note">近 {days} 天无相关新闻。</p>'
    lis = []
    for it in items:
        snippet = it["snippet"][:120] + ("…" if len(it["snippet"]) > 120 else "")
        lis.append(f'<li><span class="meta">{html.escape(it["time"][5:16])} {html.escape(it["source"] or "—")}</span> '
                   f'{safe_link(it["url"], it["title"])}<br><span class="snip">{html.escape(snippet)}</span></li>')
    return f'<ol class="news">{"".join(lis)}</ol>'


def factor_html(f: dict, names: dict, days: int, open_: bool = False) -> str:
    codes = "、".join(f"{names[c]}" for c in f["品种"] if c in names)
    more = f"，共 {f['命中']} 条，显示最新 {len(f['新闻'])} 条" if f["命中"] > len(f["新闻"]) else ""
    cut = (f'<p class="note">注：搜索词太常见，按时间只翻到 {html.escape(f["只搜到"])}（搜索最多给 1000 条）；'
           f'已按相关度补搜，更早的新闻可能有遗漏。</p>' if f.get("只搜到") else "")
    return (f'<details class="factor" id="{f["id"]}"{" open" if open_ else ""}><summary>{html.escape(f["名称"])} '
            f'<small>{html.escape(f.get("类别", ""))} · {len(f["新闻"])} 条新闻{more}</small></summary>'
            f'<p class="note">影响：{html.escape(codes)}</p>{cut}'
            f'<p class="mech">通常怎么影响价差（常见说法，未经验证）：{html.escape(f.get("通常怎么影响价差", "—"))}</p>'
            f'{news_list_html(f["新闻"], days)}</details>')


def ai_block_html(entry: dict, known_urls: set[str], checks: dict) -> str:
    """known_urls：脚本这次抓到的新闻链接。AI 引用的链接不在里面、又没标「补充搜索」，就标 ⚠ 提醒核对；
    checks：真的打开链接的核对结果（打不开标红）。"""
    if not isinstance(entry, dict):
        return ""
    parts = ['<div class="ai"><span class="tag">AI 解读</span> ']
    if entry.get("小结"):
        parts.append(f'<b>{html.escape(str(entry["小结"]))}</b>')
    parts.append(direction_html(entry.get("指向")).replace("指向：", "因素合起来指向："))
    items = entry.get("条目") or []
    if items:
        parts.append("<ul>")
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            src = " ".join(str(it.get(k, "")) for k in ("时间", "来源")).strip()
            link = link_html(it.get("链接"), "原文", checks) if it.get("链接") else ""  # 引用仓储数据时没有链接
            if it.get("链接") and str(it["链接"]) not in known_urls and "补充搜索" not in str(it):
                link += ' <b class="flag">⚠ 这个链接不在脚本抓到的新闻里，请核对</b>'
            parts.append(f'<li><b>{html.escape(str(it.get("因素", "")))}</b>：{html.escape(str(it.get("事实", "")))}'
                         f' <span class="meta">（{html.escape(src)}{"，" + link if link else ""}）</span>'
                         f'<br>→ 对价差：{html.escape(str(it.get("对价差", "")))}{direction_html(it.get("指向"))}</li>')
        parts.append("</ul>")
    parts.append("</div>")
    return "".join(parts)


def events_html(events: dict | None, names: dict, checks: dict) -> str:
    rows = (events or {}).get("事件") or []
    if not rows:
        return ('<p class="note">还没有记录长期事件。对 AI 说「更新影响因素」，它会把伊朗局势这类持续几周以上的大事记在这里。</p>')
    out = ['<div class="wrap"><table class="wraptext"><thead><tr><th>事件</th><th>状态</th><th>影响品种</th>'
           '<th>最新进展</th><th>开始</th><th>更新</th><th>链接</th></tr></thead><tbody>']
    order = {"进行中": 0, "缓和": 1, "已结束": 2}
    for e in sorted((e for e in rows if isinstance(e, dict)), key=lambda e: order.get(e.get("状态"), 1)):
        codes = "、".join(names.get(str(c).lower(), str(c)) for c in e.get("影响品种") or [])
        links = "<br>".join(link_html(l.get("链接"), l.get("标题") or "原文", checks, str(l.get("标题") or ""))
                            for l in e.get("链接") or [] if isinstance(l, dict))
        out.append(f'<tr><td>{html.escape(str(e.get("名称", "")))}</td><td>{html.escape(str(e.get("状态", "")))}</td>'
                   f'<td>{html.escape(codes)}</td><td>{html.escape(str(e.get("最新进展", "")))}</td>'
                   f'<td>{html.escape(str(e.get("开始", "")))}</td><td>{html.escape(str(e.get("更新日期", "")))}</td>'
                   f'<td>{links or "<b class=flag>⚠ 没有链接</b>"}</td></tr>')
    out.append("</tbody></table></div>")
    return "".join(out)


def factors_to_html() -> str:
    if not FACTOR_LATEST.exists():
        return ('<section><h2>影响因素</h2><p>还没有数据。双击「一键更新数据.bat」，'
                '或运行 <code>python futures_brief.py factors</code>。</p></section>')
    data = json.loads(FACTOR_LATEST.read_text(encoding="utf-8"))
    names, spreads, factors, days = data["品种"], data["价差"], data["因素"], data["天数"]
    ai, ai_err = load_ai_file(FACTOR_AI)
    events, ev_err = load_ai_file(FACTOR_EVENTS)
    n = max((r.get("n") or 0 for r in spreads.values()), default=0)
    parts = [f'<section><h2>影响因素 <small>新闻 {html.escape(data["生成时间"])} 更新（近 {days} 天）· '
             f'价差数据 {html.escape(data["价差日期"] or "无")}</small></h2>',
             '<p class="note">' + "<br>".join(html.escape(t.replace("**", "")) for t in FACTOR_EXPLAIN) +
             "<br>新闻和价差由脚本获取；紫色「AI 解读」由 AI 写，只讲机制，不构成买卖建议。</p>"]
    for w in data["警告"] + [e for e in (ai_err, ev_err) if e]:
        parts.append(f'<p class="warn">⚠ {html.escape(w)}</p>')
    ai_products = (ai or {}).get("品种") or {}
    known_urls = {it["url"] for f in factors for it in f["新闻"]}
    checks = check_links(ai_links(ai, events) - known_urls)  # 脚本抓到的新闻链接本来就是真的，不用再查
    bad = sum(1 for u in ai_links(ai, events) if (checks.get(u) or {}).get("状态") == "打不开")
    if bad:
        parts.append(f'<p class="warn">⚠ AI 引用的链接里有 {bad} 个打不开，下面已标红，请核对。</p>')
    if data.get("部分更新"):
        parts.append(f'<p class="note">{html.escape(data["部分更新"])}</p>')
    if ai:
        stamp = str(ai.get("更新时间", ""))
        stale = stamp[:16] < data["生成时间"][:16]
        ai_date = str(ai.get("价差日期", ""))
        parts.append(f'<p class="note"><span class="tag">AI 解读</span> 更新于 {html.escape(stamp or "（未写时间）")}'
                     + (f"，用的是 {html.escape(ai_date)} 的价差" if ai_date else "")
                     + ("，<b>早于上面的新闻，可能已过时</b>；对 AI 说「更新影响因素」可以刷新。" if stale else "。")
                     + "</p>")
        if ai_date and data["价差日期"] and ai_date != data["价差日期"] and not stale:
            parts.append(f'<p class="warn">⚠ AI 解读用的是 {html.escape(ai_date)} 的价差，'
                         f'下面的表是 {html.escape(data["价差日期"])} 的，数字可能对不上。</p>')
    else:
        parts.append('<p class="note">还没有 AI 解读。对 AI 说「更新影响因素」，它会筛选新闻并写出每个品种的价差解读。</p>')

    if ai_products:
        get = lambda e, k: str(e.get(k, "") or "") if isinstance(e, dict) else ""
        chg = lambda v: "" if not v else (' class="up"' if v > 0 else ' class="down"')
        rows = []
        for c in names:
            if c not in ai_products:
                continue
            r, e = spreads.get(c) or {}, ai_products[c]
            ok = r.get("status") == "ok"
            rows.append(f'<tr><td><a href="#f-{c}">{html.escape(names[c])}</a></td><td>{c}</td>'
                        f'<td>{fmt_price(r["spread"], c, True) if ok else "none"}</td>'
                        f'<td{chg(r.get("chg_n")) if ok else ""}>{fmt_price(r.get("chg_n"), c, True) if ok else "—"}</td>'
                        f'<td>{direction_html(get(e, "指向")).replace("指向：", "") or "—"}</td>'
                        f'<td>{html.escape(get(e, "小结"))}</td></tr>')
        missing = [names[c] for c in names if c not in ai_products]
        parts.append("<h3>AI 解读一览 <small>点品种名看详细条目</small></h3><div class='wrap'><table class='wraptext'>"
                     f"<thead><tr><th>品种</th><th>代码</th><th>价差（脚本）</th><th>{n}日变化（脚本）</th>"
                     f"<th>因素指向（AI）</th><th>小结（AI）</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
                     '<p class="note">「价差」「变化」两列是脚本算的，用来核对小结里的数字；对不上以脚本为准。'
                     "「因素指向」是 AI 按常见机制判断这些因素通常把价差往哪边推，不是预测，记下来是为了以后复盘。</p>"
                     + (f'<p class="note">没有 AI 解读的品种：{html.escape("、".join(missing))}</p>' if missing else ""))
    parts.append(review_note_html())

    parts += ["<h3>长期事件 <small>（AI 维护）</small></h3>", events_html(events, names, checks)]

    heads = factor_spread_heads(n)
    parts.append(f"<h3>近月 − 下月价差</h3><div class='wrap'><table><thead><tr>"
                 + "".join(f"<th>{h}</th>" for h in heads[:10])
                 + f"<th>近 {n} 日走势</th>" + "".join(f"<th>{h}</th>" for h in heads[10:])
                 + "</tr></thead><tbody>")
    for c in names:
        if c not in spreads:
            continue
        r, cells = spreads[c], spread_cells(spreads[c])
        chg = lambda v: "" if not v else (' class="up"' if v > 0 else ' class="down"')
        spark = sparkline([(parse_date(d), v) for d, v in r.get("series", [])], "价差") if r.get("series") else ""
        parts.append(f'<tr><td><a href="#f-{c}">{html.escape(names[c])}</a></td><td>{c}</td>'
                     + "".join(f"<td>{html.escape(x)}</td>" for x in cells[:6])
                     + f'<td{chg(r.get("chg5"))}>{cells[6]}</td><td{chg(r.get("chg_n"))}>{cells[7]}</td>'
                     + f"<td>{spark}</td>" + "".join(f"<td>{html.escape(x)}</td>" for x in cells[8:]) + "</tr>")
    parts.append("</tbody></table></div>"
                 '<p class="note">变化：红色 = 价差变大（近月相对走强），绿色 = 价差变小（近月相对走弱）。</p>')

    public = [f for f in factors if f.get("类别") in PUBLIC_KINDS]
    if public:
        parts.append("<h3>公共因素 <small>宏观、政策、地缘：影响多个品种</small></h3>")
        parts += [factor_html(f, names, days) for f in public]

    parts.append("<h3>各品种</h3><p class='chips'>" + "".join(
        f'<a href="#f-{c}">{html.escape(names[c])}{" ✦" if c in ai_products else ""}</a>' for c in names) +
        "</p><p class='note'>✦ = 有 AI 解读。点品种名展开。</p>")
    for c in names:
        own = [f for f in factors if c in f["品种"] and f.get("类别") not in PUBLIC_KINDS]
        pub = [f for f in public if c in f["品种"]]
        r = spreads.get(c) or {}
        head = (f"价差 {fmt_price(r['spread'], c, True)}，{structure_text(r['spread'])}" if r.get("status") == "ok"
                else "价差 none")
        n_news = sum(len(f["新闻"]) for f in own)
        parts.append(f'<details class="prod" id="f-{c}"><summary>{html.escape(names[c])} {c} '
                     f'<small>{head} · 专属因素新闻 {n_news} 条{" · ✦ AI 解读" if c in ai_products else ""}</small>'
                     f'</summary>{ai_block_html(ai_products.get(c), known_urls, checks)}')
        if pub:
            parts.append('<p class="note">相关公共因素：' + "、".join(
                f'<a href="#{f["id"]}">{html.escape(f["名称"])}</a>（{len(f["新闻"])} 条）' for f in pub) + "</p>")
        parts += [factor_html(f, names, days) for f in own]
        parts.append("</details>")
    parts.append("</section>")
    return "\n".join(parts)


TABS_HTML = """<nav class="tabs"><button data-tab="stock">仓储</button><button data-tab="factors">影响因素</button></nav>
<div class="tab" id="tab-stock">{stock}</div>
<div class="tab" id="tab-factors" hidden>{factors}</div>
<script>
(function () {{
  var DEFAULT = "{default}";
  function show(name) {{
    document.querySelectorAll(".tab").forEach(function (t) {{ t.hidden = t.id !== "tab-" + name; }});
    document.querySelectorAll(".tabs button").forEach(function (b) {{ b.classList.toggle("on", b.dataset.tab === name); }});
  }}
  function fromHash() {{
    var h = decodeURIComponent(location.hash.slice(1));
    var el = h && document.getElementById(h);
    if (el && el.closest("#tab-factors")) {{
      show("factors");
      for (var d = el; d; d = d.parentElement) if (d.tagName === "DETAILS") d.open = true;
      el.scrollIntoView();
    }} else show(h === "stock" || h === "factors" ? h : DEFAULT);
  }}
  document.querySelectorAll(".tabs button").forEach(function (b) {{
    b.onclick = function () {{ history.replaceState(null, "", "#" + b.dataset.tab); show(b.dataset.tab); }};
  }});
  window.addEventListener("hashchange", fromHash);
  fromHash();
}})();
</script>"""


def save_stock_part(date: str, sections: str) -> None:
    write_text(PAGE_STOCK_PART, json.dumps({"date": date, "now": f"{dt.datetime.now():%Y-%m-%d %H:%M}",
                                            "sections": sections}, ensure_ascii=False))


def build_page(default_tab: str) -> Path:
    """把仓储和影响因素两个标签页拼成 output/仓储速览.html。两部分各自更新，互不覆盖。"""
    archive_ai()  # AI 解读改过就顺手存档（复盘用，也是备份）
    part = json.loads(PAGE_STOCK_PART.read_text(encoding="utf-8")) if PAGE_STOCK_PART.exists() else None
    stock = part["sections"] if part else ('<section><p>还没有仓储数据。双击「一键更新数据.bat」，'
                                           '或运行 <code>python futures_brief.py stock</code>。</p></section>')
    page = OUTPUT / "仓储速览.html"
    write_text(page, HTML_PAGE.format(
        date=part["date"] if part else "", now=part["now"] if part else "（仓储数据还没获取）",
        sections=TABS_HTML.format(stock=stock, factors=factors_to_html(), default=default_tab)))
    return page


FACTOR_KEYS = ("id", "名称", "类别", "品种", "通常怎么影响价差", "搜索词", "命中", "新闻", "只搜到")


def merge_factor_data(new: dict, names: dict[str, str], all_factors: list[dict]) -> dict:
    """只更新了部分品种时，和上次的 最新数据.json 合并：没更新的品种保留上次的价差和新闻，
    这样网页上其它品种（和 AI 解读引用的链接）不会消失。"""
    try:
        old = json.loads(FACTOR_LATEST.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return new
    codes = set(new["品种"]) | set(old.get("品种") or {})
    merged = dict(new)
    merged["品种"] = {c: names[c] for c in names if c in codes}
    merged["价差"] = {**{c: v for c, v in (old.get("价差") or {}).items() if c in names}, **new["价差"]}
    old_f = {f["名称"]: f for f in old.get("因素") or []}
    new_f = {f["名称"]: f for f in new["因素"]}
    merged["因素"] = []
    for f in all_factors:  # 按清单顺序；品种恢复成清单里的完整列表（只留合并后有的品种）
        src = new_f.get(f["名称"]) or old_f.get(f["名称"])
        if src:
            merged["因素"].append({**src, "id": f["id"], "品种": [c for c in f["品种"] if c in codes]})
    old_time = old.get("全部更新时间") or old.get("生成时间", "")
    merged["全部更新时间"] = old_time
    merged["部分更新"] = (f"这次只更新了：{'、'.join(names[c] for c in new['品种'])}（{new['生成时间']}）；"
                       f"其余品种的新闻和价差是 {old_time} 的。")
    if old.get("价差日期") and old["价差日期"] != new["价差日期"]:
        merged["警告"] = new["警告"] + [f"没更新的品种价差还是 {old['价差日期']} 的数据"]
    return merged


def run_factors(args) -> list[Path]:
    names, all_factors = load_factor_list()
    codes = resolve_factor_codes(args.products, names)
    factors = [dict(f) for f in all_factors if any(c in codes for c in f["品种"])]
    for f in factors:
        f["品种"] = [c for c in f["品种"] if c in codes]
    errors = collect_factor_news(factors, args.days, args.limit)
    spreads, spread_date, warns = factor_spreads(codes, names)
    if errors:
        warns.append(f"有 {len(errors)} 个搜索词失败：{'、'.join(errors)}")
    now = dt.datetime.now()
    data = {"生成时间": f"{now:%Y-%m-%d %H:%M}", "天数": args.days, "每因素条数": args.limit,
            "价差日期": spread_date, "品种": {c: names[c] for c in codes}, "价差": spreads,
            "因素": [{k: f[k] for k in FACTOR_KEYS if k in f} for f in factors],
            "警告": warns}
    partial = set(codes) != set(names)
    folder = FACTOR_OUT / f"{now:%Y-%m-%d}"
    stem = f"影响因素_{now:%Y%m%d}" + ("_" + "_".join(codes) if partial else "")  # 只更新部分品种时不覆盖当天的全量文件
    md = folder / f"{stem}.md"
    write_text(md, factors_to_markdown(data))
    write_text(folder / f"{stem}.json", json.dumps(data, ensure_ascii=False, indent=1))
    if partial:
        data = merge_factor_data(data, names, all_factors)
    else:
        data["全部更新时间"] = data["生成时间"]
    write_text(FACTOR_LATEST, json.dumps(data, ensure_ascii=False, indent=1))
    for w in warns:
        log(f"  ⚠ {w}")
    hit = sum(1 for f in factors if f["新闻"])
    log(f"  {len(factors)} 个因素里 {hit} 个有近 {args.days} 天的新闻 → {md.relative_to(ROOT)}")
    if partial:
        log("  只更新了部分品种：网页里其余品种保留上次的数据")
    try:
        log(f"  复盘报告 → {build_review()[0].relative_to(ROOT)}")
    except RuntimeError as e:
        log(f"  ⚠ 复盘报告没更新：{e}")
    page = build_page("factors")
    log(f"  网页版 → {page.relative_to(ROOT)}（「影响因素」标签页）")
    if args.open:
        webbrowser.open(page.as_uri())
    return [md, page]


def run_page(args) -> list[Path]:
    folder = archive_ai()
    if folder:
        log(f"  AI 解读已存档 → {folder.relative_to(ROOT)}")
    try:
        build_review()
    except RuntimeError as e:
        log(f"  ⚠ 复盘报告没更新：{e}")
    page = build_page("factors")
    _, err = load_ai_file(FACTOR_AI)
    _, err2 = load_ai_file(FACTOR_EVENTS)
    for e in (err, err2):
        if e:
            log(f"  ⚠ {e}")
    log(f"  网页版 → {page.relative_to(ROOT)}")
    if args.open:
        webbrowser.open(page.as_uri())
    return [page]


# ---------------------------------------------------------------- 复盘（AI 解读说的方向，后来价差真的这样走了吗）

REVIEW_DIR = ROOT / "复盘"
REVIEW_ARCHIVE = REVIEW_DIR / "存档"                # 每次 AI 解读的存档：存档/更新时间/AI解读.json、长期事件.json
REVIEW_STATS = REVIEW_DIR / "复盘结果.json"          # 汇总数字，网页上的一句话提示用
REVIEW_HORIZONS = (5, 20)                          # 看几个交易日之后
REVIEW_FLAT = 0.05                                 # 价差变化不到下月价格的 0.05%，算「基本没变」
REVIEW_MIN_N = 10                                  # 少于这么多次不算命中率
REVIEW_EXPLAIN = [
    "AI 每次「更新影响因素」后，解读会自动存档到 `复盘/存档/`。每条解读带一个「指向」：AI 按常见机制判断，"
    "这个因素通常把「近月 − 下月」价差往哪边推（价差变大 / 价差变小 / 不明确）。它不是预测，这里只是拿来对答案。",
    f"**对答案**：从 AI 当时用的价差日期算起，往后数 {' 个、'.join(map(str, REVIEW_HORIZONS))} 个交易日，"
    "用同样两个合约（当时的近月和下月）看价差实际变大还是变小。"
    f"变化不到下月价格的 {REVIEW_FLAT}% 算「基本没变」，不算对也不算错。",
    f"**命中率** = 对 ÷（对 + 错）。少于 {REVIEW_MIN_N} 次时只列次数、不算命中率：次数太少，对几次错几次都可能是运气。",
    "同一个品种、同一个因素、同一个价差日期只算一次（以最后一次存档为准）；但同一件事在相隔几天的几次解读里出现，会被算多次。",
    "原油、低硫燃料油、燃料油的近月在交割月前一个月月底停止交易：到那天如果近月已到期或只剩几个交易日，不对答案。",
    "价差本身有惯性（在扩大的往往继续扩大一阵），所以命中率高不一定说明这个因素真的起作用，要结合机制和样本数一起看。"
    "「不明确」的条目不对答案，只计数。",
]


def ai_stamp_folder(ai: dict) -> str | None:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})", str(ai.get("更新时间", "")))
    return f"{m[1]}-{m[2]}-{m[3]}_{m[4]}{m[5]}" if m else None


def archive_ai() -> Path | None:
    """把当前的 AI解读.json、长期事件.json 复制到 复盘/存档/更新时间/（备份 + 以后复盘用）。"""
    ai, _ = load_ai_file(FACTOR_AI)
    name = ai_stamp_folder(ai) if ai else None
    if not name:
        return None
    folder = REVIEW_ARCHIVE / name
    for src in (FACTOR_AI, FACTOR_EVENTS):
        if src.exists():
            text = src.read_text(encoding="utf-8")
            dst = folder / src.name
            if not dst.exists() or dst.read_text(encoding="utf-8") != text:
                write_text(dst, text)
    return folder


def review_base_date(ai: dict, snap: str) -> dt.date | None:
    """AI 当时用的价差日期：AI解读里写了「价差日期」就用它，否则用存档时间之前最近的交易日。"""
    try:
        return parse_date(str(ai["价差日期"]))
    except (KeyError, ValueError):
        d = dt.datetime.strptime(snap[:10], "%Y-%m-%d").date() - dt.timedelta(days=1)
        while not is_trading_day(d):
            d -= dt.timedelta(days=1)
        return d


def review_records() -> tuple[list[dict], int]:
    """读所有存档，返回（要对答案的记录, 存档次数）。"""
    if not REVIEW_ARCHIVE.exists():
        return [], 0
    latest: dict[tuple, dict] = {}
    snaps = 0
    for folder in sorted(p for p in REVIEW_ARCHIVE.iterdir() if p.is_dir()):
        ai, _ = load_ai_file(folder / "AI解读.json")
        if not ai:
            continue
        snaps += 1
        base = review_base_date(ai, folder.name)
        for code, e in (ai.get("品种") or {}).items():
            if not isinstance(e, dict):
                continue
            rows = [("（整体）", e.get("指向"))] + [(str(it.get("因素", "")), it.get("指向"))
                                                  for it in e.get("条目") or [] if isinstance(it, dict)]
            for factor, direction in rows:
                if direction:
                    latest[(code.lower(), factor, base)] = {"存档": folder.name, "code": code.lower(),
                                                             "因素": factor, "指向": direction, "base": base}
    return sorted(latest.values(), key=lambda r: (r["base"], r["code"], r["因素"] != "（整体）", r["因素"])), snaps


def review_outcomes(records: list[dict]) -> None:
    """给每条记录填上 N 个交易日后的价差和对错。行情用缓存，缺的才下载。"""
    quotes: dict[dt.date, dict | None] = {}
    today = dt.date.today()

    def q(d: dt.date) -> dict | None:
        if d not in quotes:
            quotes[d] = kx_quotes(d) if is_trading_day(d) else None
        return quotes[d]

    after: dict[dt.date, list[dt.date]] = {}
    for base in sorted({r["base"] for r in records}):
        days, d = [], base
        while len(days) < max(REVIEW_HORIZONS) and d < today:
            d += dt.timedelta(days=1)
            if q(d):
                days.append(d)
        after[base] = days
    for r in records:
        bq = (q(r["base"]) or {}).get(r["code"], {})
        near, far = front_pair(bq, r["base"])
        a, b = bq.get(near), bq.get(far)
        r.update(near=near, far=far, results={})
        if not (a and b and a[0] and b[0]):
            r["base_spread"] = None
            continue
        r.update(base_spread=round(a[0] - b[0], 4), far_settle=b[0])
        for h in REVIEW_HORIZONS:
            days = after[r["base"]]
            if len(days) < h:
                r["results"][h] = {"结果": "还没到", "还差": h - len(days)}
                continue
            d = days[h - 1]
            x, y = (q(d) or {}).get(r["code"], {}).get(near), (q(d) or {}).get(r["code"], {}).get(far)
            if not (x and y and x[0] and y[0]):
                r["results"][h] = {"结果": "合约已到期", "日期": d}
                continue
            if expiring(r["code"], near, d, days_left_in_month(d)):  # 原油等：近月最后几天价差容易失真，不算
                r["results"][h] = {"结果": "临近到期", "日期": d}
                continue
            chg = round(x[0] - y[0] - r["base_spread"], 4)
            actual = ("基本没变" if abs(chg) < b[0] * REVIEW_FLAT / 100 else "价差变大" if chg > 0 else "价差变小")
            verdict = ("不对答案" if r["指向"] not in ("价差变大", "价差变小") else
                       "基本没变" if actual == "基本没变" else "对" if actual == r["指向"] else "错")
            r["results"][h] = {"结果": verdict, "实际": actual, "日期": d, "价差": round(x[0] - y[0], 4), "变化": chg}


def review_stats(rows: list[dict]) -> dict:
    out = {"次数": len(rows)}
    for h in REVIEW_HORIZONS:
        res = [r["results"].get(h, {}).get("结果") for r in rows]
        right, wrong, flat = res.count("对"), res.count("错"), res.count("基本没变")
        out[h] = {"对": right, "错": wrong, "基本没变": flat, "还没到": res.count("还没到"),
                  "命中率": right / (right + wrong) if right + wrong >= REVIEW_MIN_N else None}
    return out


def stat_cells(s: dict) -> list[str]:
    cells = [str(s["次数"])]
    for h in REVIEW_HORIZONS:
        x = s[h]
        cells.append(f"{x['对']} / {x['错']} / {x['基本没变']}" + (f"（{x['还没到']} 次还没到）" if x["还没到"] else ""))
        cells.append("—" if x["命中率"] is None else f"{x['命中率']:.0%}")
    return cells


def stat_heads(first: list[str]) -> list[str]:
    heads = first + ["有方向的次数"]
    for h in REVIEW_HORIZONS:
        heads += [f"{h}日后 对 / 错 / 基本没变", f"{h}日命中率"]
    return heads


def review_tables(records: list[dict], names: dict) -> list[tuple[str, list[str], list[list[str]], str]]:
    """（标题, 表头, 行, 说明）"""
    scored = [r for r in records if r["指向"] in ("价差变大", "价差变小") and r.get("base_spread") is not None]
    by_factor: dict[str, list] = {}
    for r in scored:
        if r["因素"] != "（整体）":
            by_factor.setdefault(r["因素"], []).append(r)
    rows_f = [[f, "、".join(dict.fromkeys(names.get(r["code"], r["code"]) for r in rs)), *stat_cells(review_stats(rs))]
              for f, rs in sorted(by_factor.items(), key=lambda kv: -len(kv[1]))]
    by_code: dict[str, list] = {}
    for r in scored:
        if r["因素"] == "（整体）":
            by_code.setdefault(r["code"], []).append(r)
    rows_c = [[names.get(c, c), c, *stat_cells(review_stats(rs))] for c, rs in by_code.items()]
    unclear = sum(1 for r in records if r["指向"] == "不明确")
    detail = []
    for r in records:
        c = r["code"]
        cells = [r["存档"].replace("_", " "), names.get(c, c), r["因素"], r["指向"]]
        if r.get("base_spread") is None:
            detail.append(cells + [f"{r['base']:%m-%d} 没有价差"] + ["—"] * len(REVIEW_HORIZONS))
            continue
        cells.append(f"{r['base']:%m-%d} {c}{r['near']}−{c}{r['far']} = {fmt_price(r['base_spread'], c, True)}")
        for h in REVIEW_HORIZONS:
            x = r["results"].get(h, {})
            if x.get("结果") == "还没到":
                cells.append(f"还没到（再过 {x['还差']} 个交易日）")
            elif x.get("结果") in ("合约已到期", "临近到期"):
                cells.append(f"{x['日期']:%m-%d} 近月{x['结果']}，不对答案")
            else:
                cells.append(f"{x['日期']:%m-%d} {fmt_price(x['价差'], c, True)}（{fmt_price(x['变化'], c, True)}，"
                             f"{x['实际']}）→ {x['结果']}")
        detail.append(cells)
    return [
        ("按因素", stat_heads(["因素", "涉及品种"]), rows_f, "AI 解读里每一条的「指向」对答案。"),
        ("按品种（整体指向）", stat_heads(["品种", "代码"]), rows_c, "每个品种的「因素合起来指向」对答案。"),
        ("明细", ["存档时间", "品种", "因素", "指向", "当时价差"] + [f"{h}个交易日后" for h in REVIEW_HORIZONS], detail,
         f"「不明确」的有 {unclear} 条，不对答案。变化 = 之后的价差 − 当时价差（同样两个合约）。"),
    ]


def build_review() -> list[Path]:
    """生成 复盘/复盘报告.md 和 .html。"""
    archive_ai()
    records, snaps = review_records()
    review_outcomes(records)
    names, _ = load_factor_list()
    now = f"{dt.datetime.now():%Y-%m-%d %H:%M}"
    tables = review_tables(records, names)
    head = (f"已存档 {snaps} 次 AI 解读" + (f"（最早 {records[0]['存档'].replace('_', ' ')}）" if records else "")
            + f"，有方向的记录 {sum(1 for r in records if r['指向'] in ('价差变大', '价差变小'))} 条。")
    md = [f"# 影响因素复盘（{now} 生成）", "", head, "", *[f"- {t}" for t in REVIEW_EXPLAIN], ""]
    body = [f"<h1>影响因素复盘</h1><p class='note'>{now} 生成。{html.escape(head)}</p><p class='note'>"
            + "<br>".join(html.escape(t.replace("**", "").replace("`", "")) for t in REVIEW_EXPLAIN) + "</p>"]
    for title, heads, rows, note in tables:
        md += [f"## {title}", "", note, ""]
        body.append(f"<h2>{title}</h2><p class='note'>{html.escape(note)}</p>")
        if not rows:
            md += ["还没有可以对答案的记录。", ""]
            body.append("<p>还没有可以对答案的记录。</p>")
            continue
        md += ["| " + " | ".join(heads) + " |", "|" + "---|" * len(heads)]
        md += ["| " + " | ".join(x.replace("|", "/") for x in r) + " |" for r in rows] + [""]
        body.append("<div class='wrap'><table class='wraptext'><thead><tr>" + "".join(f"<th>{h}</th>" for h in heads)
                    + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{html.escape(x)}</td>" for x in r)
                                                       + "</tr>" for r in rows) + "</tbody></table></div>")
    style = HTML_PAGE.split("<style>")[1].split("</style>")[0].replace("{{", "{").replace("}}", "}")
    md_path, html_path = REVIEW_DIR / "复盘报告.md", REVIEW_DIR / "复盘报告.html"
    write_text(md_path, "\n".join(md))
    write_text(html_path, f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                          f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>影响因素复盘</title>'
                          f'<style>{style}</style></head><body>{"".join(body)}'
                          f'<p class="note"><a href="../output/仓储速览.html#factors">← 回到影响因素</a></p></body></html>')
    done = {h: sum(1 for r in records if r["results"].get(h, {}).get("结果") in ("对", "错", "基本没变"))
            for h in REVIEW_HORIZONS}
    write_text(REVIEW_STATS, json.dumps({"生成时间": now, "存档次数": snaps, "已对答案": done,
                                         "最早": records[0]["存档"] if records else ""}, ensure_ascii=False, indent=1))
    return [md_path, html_path]


def review_note_html() -> str:
    """影响因素标签页上关于复盘的一句话。"""
    link = '<a href="../复盘/复盘报告.html" target="_blank">打开复盘报告</a>（在「复盘」文件夹里）'
    try:
        s = json.loads(REVIEW_STATS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return ('<p class="note"><b>复盘</b>：AI 每次更新解读都会自动存档到「复盘/存档/」，'
                f'{"、".join(map(str, REVIEW_HORIZONS))} 个交易日后可以对答案，看哪些因素真的和价差走向一致。</p>')
    done = "、".join(f"{h} 日后 {s['已对答案'].get(str(h), 0)} 条" for h in REVIEW_HORIZONS)
    return (f'<p class="note"><b>复盘</b>：已存档 {s["存档次数"]} 次 AI 解读；已经能对答案的：{done}。{link}'
            f'（{html.escape(s["生成时间"])} 更新）</p>')


def run_review(args) -> list[Path]:
    made = build_review()
    log(f"  复盘报告 → {made[0].relative_to(ROOT)}、{made[1].relative_to(ROOT)}")
    if args.open:
        webbrowser.open(made[1].as_uri())
    return made


# ---------------------------------------------------------------- 入口

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="上期所/上期能源仓储数据 + 品种新闻")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_stock_opts(p):
        p.add_argument("--date", help="日期，如 20261016；默认今天，自动往前找最近有数据的一天")
        p.add_argument("--history-days", type=int, default=20, help="回看多少个交易日的仓单（默认 20；0 = 不看历史）")
        p.add_argument("--history-weeks", type=int, default=12, help="回看多少周的库存周报（默认 12）")
        p.add_argument("--years", type=int, default=3, help="跨月价差参考回看几年（默认 3）")
        p.add_argument("--open", action="store_true", help="完成后用浏览器打开网页版")

    def add_news_opts(p):
        p.add_argument("--days", type=int, default=3, help="只要最近几天的新闻（默认 3）")
        p.add_argument("--limit", type=int, default=15, help="每个品种最多几条（默认 15）")

    p = sub.add_parser("stock", help="仓单日报 + 库存周报")
    p.add_argument("products", nargs="*", help="可选：要看分仓库明细的品种")
    add_stock_opts(p)
    p = sub.add_parser("spread", help="跨月价差参考（第一次要下载约 3 年数据）")
    p.add_argument("--date", help="日期，如 20261016；默认今天，自动往前找最近有数据的一天")
    p.add_argument("--years", type=int, default=3, help="回看几年（默认 3）")
    p = sub.add_parser("news", help="品种新闻列表")
    p.add_argument("products", nargs="+", help="品种，如 铜 原油")
    add_news_opts(p)
    p = sub.add_parser("history", help="指定品种的仓单日报 + 库存周报历史，可自选时间段")
    p.add_argument("products", nargs="+", help="品种代码或名字，如 al 或 铝（可写多个）")
    p.add_argument("--days", type=int, default=30, help="往前看多少天（自然日，默认 30）")
    p.add_argument("--start", help="开始日期，如 20250101（写了就不看 --days）")
    p.add_argument("--end", help="结束日期，如 20251231；默认今天，自动往前找最近有数据的一天")
    p.add_argument("--open", action="store_true", help="完成后用浏览器打开网页版")
    p = sub.add_parser("factors", help="影响因素：按因素清单搜新闻 + 各品种近月−下月价差")
    p.add_argument("products", nargs="*", help="可选：只看这几个品种（默认清单里全部品种）")
    p.add_argument("--days", type=int, default=7, help="只要最近几天的新闻（默认 7）")
    p.add_argument("--limit", type=int, default=6, help="每个因素最多几条（默认 6）")
    p.add_argument("--open", action="store_true", help="完成后用浏览器打开网页版")
    p = sub.add_parser("page", help="只重新拼网页（AI 改完 AI解读.json / 长期事件.json 后用）")
    p.add_argument("--open", action="store_true", help="完成后用浏览器打开网页版")
    p = sub.add_parser("review", help="复盘：AI 解读说的价差方向，后来 5 / 20 个交易日真的这样走了吗")
    p.add_argument("--open", action="store_true", help="完成后用浏览器打开复盘报告")
    p = sub.add_parser("all", help="仓储 + 新闻")
    p.add_argument("products", nargs="+", help="品种，如 铜 原油")
    add_stock_opts(p)
    add_news_opts(p)
    args = ap.parse_args()

    made: list[Path] = []
    try:
        if args.cmd in ("stock", "all"):
            made += run_stock(args)
        if args.cmd == "spread":
            made += run_spread(args)
        if args.cmd == "history":
            made += run_history(args)
        if args.cmd in ("news", "all"):
            made += run_news(args)
        if args.cmd == "factors":
            made += run_factors(args)
        if args.cmd == "page":
            made += run_page(args)
        if args.cmd == "review":
            made += run_review(args)
    except RuntimeError as e:
        log(f"出错了：{e}")
        return 1
    log("\n完成。生成的文件：")
    for m in made:
        log(f"  {m.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
