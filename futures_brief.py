"""期货速览：上期所 / 上期能源 仓储数据 + 指定品种近期新闻。

只用 Python 标准库，不需要 pip 安装任何东西。

用法：
    python futures_brief.py stock                 # 最新的仓单日报 + 库存周报（全部品种）
    python futures_brief.py stock 铜 原油          # 同上，另外列出这几个品种的分仓库明细
    python futures_brief.py stock --date 20261016 # 指定日期
    python futures_brief.py stock --history-days 60   # 回看 60 个交易日（默认 20 天、12 周）
    python futures_brief.py news 铜 原油           # 这几个品种近 3 天的新闻列表
    python futures_brief.py news 铜 --days 7 --limit 30
    python futures_brief.py all 铜 原油            # 仓储 + 新闻一起

结果写到本文件旁边的 output/ 目录。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
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
        names.append("-".join(parts))
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
            lines.append("| 品种 | 代码 | 单位 | 汇总行 | " + " | ".join(cols) + " |")
            lines.append("|" + "---|" * (4 + len(cols)))
            for t, r in items:
                vals = [r.get(c, "") for c in cols]
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
            parts.append('<div class="wrap"><table><thead><tr><th>品种</th><th>代码</th><th>单位</th><th>汇总行</th>'
                         + "".join(f"<th>{html.escape(c)}</th>" for c in cols) + "</tr></thead><tbody>")
            for t, r in items:
                tds = []
                for c in cols:
                    v = r.get(c, "")
                    n = to_number(v)
                    cls = ""
                    if n and "增减" in c:
                        cls = ' class="up"' if n > 0 else ' class="down"'
                    tds.append(f"<td{cls}>{html.escape(v)}</td>")
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
}

MEANING_DOWN = ("仓单 / 库存减少：交易所仓库里能拿去交割的货变少，现货偏紧。离交割月越近、持仓越大，"
                "空头越难交货，价格容易被推高（俗称「挤仓」风险）。")
MEANING_UP = "仓单 / 库存增加：可交割的货变多，供应偏宽松，对价格有压力；空头交货更容易。"

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
            wcol, scol = first_col(cols, ["期货", "本日数量"]), None
        else:
            wcol = first_col(cols, ["本周库存-期货", "本周期货", "本周库存"])
            scol = first_col(cols, ["本周库存-小计"])
        m = out.setdefault(t["base"], {"code": t["code"], "unit": t["unit"], "exchange": t["exchange"],
                                       "warrant": None, "stock": None, "parts": []})
        m["parts"].append(t["product"])
        for key, col in (("warrant", wcol), ("stock", scol)):
            v = table_total(t, col) if col else None
            if v is not None:
                m[key] = (m[key] or 0) + v
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
                      f"| 品种 | 代码 | 单位 | 指标 | 本周 | 周变化 | {nw}周变化 | 连续下降周数 |",
                      "|---|---|---|---|---|---|---|---|"]
            for p in witems:
                s = p["weekly_stats"]
                lines.append(f"| {display_name(p)} | {p['code']} | {p['unit']} | {p['weekly_label']} | "
                             f"{fmt_number(s['latest'])} | {fmt_chg(s['latest'], s['prev'])} | "
                             f"{fmt_chg(s['latest'], s['first'])} | {s['streak']} |")
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
                     f"<th>近 {len(wd)} 周走势</th></tr></thead><tbody>")
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
                f"<td>{sparkline(p['weekly'], p['unit']) if ws else ''}</td></tr>")
        parts.append("</tbody></table></div>")
    parts.append("</section>")
    return "\n".join(parts)


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
</style></head><body>
<h1>上期所 / 上期能源 仓储速览</h1>
<p class="note">生成时间 {now}。数字由脚本直接从交易所官网解析；变化红色为增加、绿色为减少。完整分仓库明细和历史数据见 output/仓储/日期/ 里的 CSV 文件。</p>
{sections}
</body></html>
"""


def run_stock(args) -> list[Path]:
    start = parse_date(args.date)
    wanted = resolve_products(args.products)
    made: list[Path] = []
    sections = []
    latest: dict[str, dt.date] = {}
    for kind, max_back in (("dailystock", 10), ("weeklystock", 14)):
        name = REPORTS[kind]
        log(f"正在获取{name}……")
        day, url, text = fetch_stock(kind, start, max_back)
        parsed = parse_stock_html(text)
        if not parsed["tables"]:
            raise RuntimeError(f"{name}：页面格式可能变了，没解析出任何表格（{url}）")
        latest[kind] = day
        folder = OUTPUT / "仓储" / f"{day:%Y-%m-%d}"
        stamp = day.strftime("%Y%m%d")
        md = folder / f"{name}_{stamp}.md"
        problems = [msg for t in parsed["tables"] for msg in check_totals(t)]
        write_text(md, stock_to_markdown(kind, day, url, parsed, wanted, problems))
        csv_path = folder / f"{name}_{stamp}_分仓库明细.csv"
        stock_to_csv(csv_path, parsed)
        made += [md, csv_path]
        sections.append(stock_to_html_section(kind, day, url, parsed, problems))
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

    page = OUTPUT / "仓储速览.html"
    write_text(page, HTML_PAGE.format(date=f"{latest['dailystock']:%Y-%m-%d}",
                                      now=f"{dt.datetime.now():%Y-%m-%d %H:%M}", sections="\n".join(sections)))
    made.append(page)
    log(f"  网页版 → {page.relative_to(ROOT)}")
    if args.open:
        webbrowser.open(page.as_uri())
    return made


# ---------------------------------------------------------------- 新闻

EM_SEARCH = "https://search-api-web.eastmoney.com/search/jsonp"


def search_eastmoney(keyword: str, page_size: int) -> list[dict]:
    param = {
        "uid": "", "keyword": keyword, "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "time",
                                       "pageIndex": 1, "pageSize": page_size, "preTag": "", "postTag": ""}},
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
        p.add_argument("--open", action="store_true", help="完成后用浏览器打开网页版")

    def add_news_opts(p):
        p.add_argument("--days", type=int, default=3, help="只要最近几天的新闻（默认 3）")
        p.add_argument("--limit", type=int, default=15, help="每个品种最多几条（默认 15）")

    p = sub.add_parser("stock", help="仓单日报 + 库存周报")
    p.add_argument("products", nargs="*", help="可选：要看分仓库明细的品种")
    add_stock_opts(p)
    p = sub.add_parser("news", help="品种新闻列表")
    p.add_argument("products", nargs="+", help="品种，如 铜 原油")
    add_news_opts(p)
    p = sub.add_parser("all", help="仓储 + 新闻")
    p.add_argument("products", nargs="+", help="品种，如 铜 原油")
    add_stock_opts(p)
    add_news_opts(p)
    args = ap.parse_args()

    made: list[Path] = []
    try:
        if args.cmd in ("stock", "all"):
            made += run_stock(args)
        if args.cmd in ("news", "all"):
            made += run_news(args)
    except RuntimeError as e:
        log(f"出错了：{e}")
        return 1
    log("\n完成。生成的文件：")
    for m in made:
        log(f"  {m.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
