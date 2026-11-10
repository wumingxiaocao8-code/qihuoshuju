"""自检：开盘前体检线上脚本能不能 import；取 tick 失败了自动补取一次（11-10 加）。

起因：11-10 早 9:03 开盘复盘.py 里一个名字写错，开盘tick.py 一 import 就崩，整场 tick 没取；后台用 pythonw 跑，屏幕上没有任何提示。
本文件开头只用标准库，不 import 项目里任何模块：别的文件改坏了也连累不到它。

两件事：
  1. 体检（默认）：用子进程（原版 Python）在本文件夹里逐个试 import 任务计划链路上的模块（MODULES），
     全过只在 运行日志.txt 写一行；有失败的写清哪个模块、错误最后几行，并在右下角弹通知（不会几秒就消失，要点「知道了」）。
     开盘预估.py 每次启动时（20:53、8:53、17:30 仓储取数）先在后台带起它，不等它跑完；改完代码后也可以手动跑一次。
  2. 补取（--补取）：看最近一次开盘（21:00 或 9:00）的 数据/开盘日期_时间/ 里，合约.txt 列的每个合约、每组价差的文件在不在；
     都在就什么都不做（日志一行）；缺了用子进程跑一次 开盘tick.py（它不覆盖已存的文件），补完再查一遍，还缺就弹通知、写日志。
     周末不跑；同一次开盘只补一次（看运行日志里有没有「【补取】开盘日期_时间 已补取」）；节假日没开盘时文件夹不存在，会白跑一次取数，日志里写清楚。
     任务计划「开盘tick补取」21:15、9:15 跑（主助手注册）。

手动跑（在本文件夹里，用原版 Python）：
  python 自检.py                 体检，打印结果；有失败退出码 1
  python 自检.py --补取          查最近一次开盘的 tick 缺没缺，缺了补取
  python 自检.py --补取 --dry-run   只查、只说要不要补，不真去取数
测试用：--目录 某个副本文件夹（在那里 import / 查数据）、--模块 a,b（只试这几个）、--日志 某文件（日志写到别处）、--不通知、
        --开盘 2026-11-10_0900（补取时指定哪次开盘）
"""
import argparse
import base64
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # 期货数据（futures_brief.py 在这里）
LOG_FILE = HERE / "运行日志.txt"
CONSOLE = sys.stdout is not None  # 任务计划用 pythonw 跑时没有控制台
if not CONSOLE:
    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")  # 要记的都由 log() 自己写进日志
else:
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

# 任务计划链路上的模块（11-10 核对）：开盘预估（20:53/8:53、17:30 --prep）、竞价判断 + 竞价浮层（20:57/8:57）、
# 开盘tick + 外盘涨跌 + 开盘复盘（21:03/9:03；开盘复盘跑时还会 import 竞价判断、成交模型）、主力表（竞价判断换月用）、
# 套利盘口录制 + 套利盘口查询（20:56/8:56）、日内区间（17:30 --prep）；futures_brief 在上一层（17:30 stock、20:53 新闻）。
# import 它们都没有副作用：不连天勤、不写日志（只有 套利盘口录制 会建一下已有的 数据/套利盘口 文件夹）；开盘tick 会打印天勤的免责声明，不算错。
MODULES = ["开盘预估", "竞价判断", "竞价浮层", "开盘tick", "开盘复盘", "外盘涨跌", "主力表", "成交模型",
           "套利盘口录制", "套利盘口查询", "日内区间"]
UP_MODULES = ["futures_brief"]  # 在 ROOT 里 import 的
IMPORT_TIMEOUT = 90  # 一个模块 import 超过 90 秒算失败（正常 1–3 秒）
WORKERS = 4  # 同时试几个（开盘前别把电脑占满）
FETCH_TIMEOUT = 900  # 补取时 开盘tick.py 最多跑 15 分钟（连天勤失败它自己会每分钟重试，最多 5 次）
DONE_MARK = "【补取】{} 已补取"
TOAST_APP = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"  # 借 PowerShell 的身份弹通知（同 开盘预估.py）
NOISE = ("在使用天勤量化之前",)  # 天勤 import 时打印的免责声明，不算错误输出


def python_exe() -> str:
    """子进程用的 Python：同一个文件夹里的 python.exe（任务计划用 pythonw 跑本文件时也要有输出的那个）。"""
    p = Path(sys.executable).with_name("python.exe")
    return str(p if p.exists() else Path(sys.executable))


def log(msg: str, log_file: Path, console: bool = True) -> None:
    """写进运行日志（追加），手动跑时同时打印。"""
    if CONSOLE and console:
        print(msg)
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError as e:
        if CONSOLE:
            print(f"（日志写不进去：{e}）")


def notify(title: str, lines: list[str], open_file: Path) -> bool:
    """右下角弹通知（抄自 开盘预估.notify，自己带着，不 import 开盘预估）。
    scenario="reminder" + 一个「知道了」按钮：通知停在屏幕上，点了才消失（普通通知几秒就收进通知中心）；点通知本身打开 open_file。"""
    xml = (f'<toast scenario="reminder" activationType="protocol" launch="{xml_escape(open_file.resolve().as_uri())}">'
           '<visual><binding template="ToastGeneric">'
           f"<text>{xml_escape(title)}</text>" + "".join(f"<text>{xml_escape(x)}</text>" for x in lines if x)
           + '</binding></visual><actions><action content="知道了" arguments="dismiss" activationType="system"/></actions></toast>')
    ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null\n"
          "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null\n"
          "$x = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
          f"$x.LoadXml(@'\n{xml}\n'@)\n"
          "$t = [Windows.UI.Notifications.ToastNotification]::new($x)\n"
          f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{TOAST_APP}').Show($t)\n")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-EncodedCommand", base64.b64encode(ps.encode("utf-16-le")).decode()],
                           capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except Exception:
        return False


# ---------- 1. 体检：逐个试 import ----------

def try_import(name: str, folder: Path) -> tuple[str, bool, str, float]:
    """在 folder 里用子进程 import name：(模块, 成功, 错误最后几行, 秒)。"""
    t0 = time.time()
    try:
        r = subprocess.run([python_exe(), "-c", f"import {name}"], cwd=folder, capture_output=True, timeout=IMPORT_TIMEOUT,
                           env=os.environ | {"PYTHONIOENCODING": "utf-8"},
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return name, False, f"import 超过 {IMPORT_TIMEOUT} 秒没完（卡住了）", time.time() - t0
    except Exception as e:
        return name, False, f"子进程起不来：{type(e).__name__} {e}", time.time() - t0
    err = [x for x in r.stderr.decode("utf-8", errors="replace").splitlines() if x.strip() and not x.startswith(NOISE)]
    return name, r.returncode == 0, "\n".join(err[-4:]), time.time() - t0


def check(folder: Path, modules: list[str], up: list[str]) -> list[tuple]:
    """全部试一遍，返回 [(模块, 成功, 错误, 秒)]，按 MODULES 的顺序。"""
    jobs = [(m, folder) for m in modules] + [(m, folder.parent) for m in up]
    jobs = [(m, d) for m, d in jobs if (d / f"{m}.py").exists()]  # 没有这个文件的不试（同事版只有一部分脚本）
    with ThreadPoolExecutor(WORKERS) as ex:
        return list(ex.map(lambda a: try_import(*a), jobs))


def run_check(folder: Path, modules: list[str], up: list[str], log_file: Path, notify_on: bool, who: str) -> int:
    t0 = time.time()
    res = check(folder, modules, up)
    bad = [r for r in res if not r[1]]
    stamp = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    src = f"（{who}）" if who else ""
    if not bad:
        log(f"[自检 {stamp}]{src} {len(res)} 个模块都能 import（{time.time() - t0:.1f} 秒）", log_file)
        return 0
    lines = [f"\n[自检 {stamp}]{src} ⚠ {len(bad)} 个模块 import 失败（共 {len(res)} 个，{time.time() - t0:.1f} 秒）："]
    for m, _, err, sec in bad:
        lines.append(f"  ✗ {m}：\n" + "\n".join("      " + x for x in (err or "（没有错误输出）").splitlines()))
    lines.append("  用到这些模块的任务计划（开盘预估、竞价判断、开盘tick取数、仓储取数、套利盘口录制）可能跑不起来；改好后手动跑 python 自检.py 再查一遍")
    log("\n".join(lines), log_file)
    if notify_on:
        last = [f"{m}：{(err or '').splitlines()[-1][:80] if err else '没有错误输出'}" for m, _, err, _ in bad]
        ok = notify(f"⚠ 开盘前自检：{len(bad)} 个脚本 import 失败", ["；".join(last[:3]), "详情看 开盘tick/运行日志.txt"], log_file)
        if not ok:
            log("  （通知没弹出来）", log_file)
    return 1


# ---------- 2. 补取 ----------

def last_opening(now: datetime) -> datetime:
    """now 以前（含）最近的一次开盘：9:00 或 21:00。"""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cands = [day + timedelta(days=d, hours=h) for d in (-1, 0) for h in (9, 21)]
    return max(c for c in cands if c <= now)


def read_list(f: Path) -> list[str]:
    """合约.txt：一行一组或一个合约，# 后面是注释（同 开盘tick.read_list）。"""
    items = []
    for line in f.read_text(encoding="utf-8").splitlines():
        items += line.split("#")[0].split()
    return items


def expected_files(items: list[str]) -> list[str]:
    """合约.txt 里每个合约一个「合约.csv」，每组价差一个「价差_腿1-腿2.csv」（同 开盘tick.save_ticks 的文件名）。"""
    pairs = [tuple(x.split(",")) for x in items if "," in x]
    symbols = list(dict.fromkeys([s for pr in pairs for s in pr] + [x for x in items if "," not in x]))
    return [f"{s}.csv" for s in symbols] + [f"价差_{a}-{b}.csv" for a, b in pairs]


def missing_files(folder: Path, files: list[str]) -> list[str]:
    return [f for f in files if not (folder / f).exists()]


def already_done(name: str, log_file: Path) -> bool:
    """运行日志最后 2 MB 里有没有这次开盘的「已补取」记号（同一次开盘只补一次，不另建状态文件）。"""
    try:
        with open(log_file, "rb") as f:
            f.seek(max(0, f.seek(0, 2) - 2_000_000))
            return DONE_MARK.format(name).encode("utf-8") in f.read()
    except OSError:
        return False


def run_fetch(folder: Path) -> tuple[int | None, list[str]]:
    """子进程跑一次 开盘tick.py（按 合约.txt，存到正式的 数据/），返回 (返回码, 输出各行)；超时返回码 None。"""
    try:
        r = subprocess.run([python_exe(), str(folder / "开盘tick.py")], cwd=folder, capture_output=True, timeout=FETCH_TIMEOUT,
                           env=os.environ | {"PYTHONIOENCODING": "utf-8"},
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return None, [f"开盘tick.py {FETCH_TIMEOUT // 60} 分钟还没跑完，停了"]
    lines = (r.stdout + r.stderr).decode("utf-8", errors="replace").splitlines()
    return r.returncode, [x for x in lines if x.strip() and not x.startswith(NOISE)]


def run_refetch(folder: Path, log_file: Path, notify_on: bool, dry: bool, name: str | None = None,
                now: datetime | None = None) -> int:
    """--补取：0 = 不用补 / 补齐了 / 没开盘；1 = 补完还缺；2 = 这次不跑（周末、已补过）。"""
    now = now or datetime.now()
    o = datetime.strptime(name, "%Y-%m-%d_%H%M") if name else last_opening(now)
    name = f"{o:%Y-%m-%d_%H%M}"
    stamp = f"[补取 {now:%Y-%m-%d %H:%M:%S}]"
    dry_note = "（--dry-run，只查不取）" if dry else ""
    if o.weekday() >= 5:
        log(f"{stamp} {name} 是周末，没有开盘，不查", log_file)
        return 2
    data = folder / "数据" / name
    files = expected_files(read_list(folder / "合约.txt"))
    miss = missing_files(data, files)
    if data.exists() and not miss:
        log(f"{stamp} {name} 的 {len(files)} 个文件都在，不用补{dry_note}", log_file)
        return 0
    why = (f"文件夹 数据/{name}/ 不存在（这次开盘一个文件都没存：21:03 / 9:03 那次没跑成，或者节假日本来就没开盘）" if not data.exists()
           else f"缺 {len(miss)} 个文件（共 {len(files)} 个）：{'、'.join(miss)}")
    if already_done(name, log_file):
        log(f"{stamp} {name} {why}；这次开盘已经补取过一次，不再补（同一次开盘只补一次）", log_file)
        return 2
    if dry:
        log(f"{stamp} {name} {why} → 要补取：会跑一次 开盘tick.py{dry_note}", log_file)
        return 1
    log(f"{stamp} {name} {why} → 跑一次 开盘tick.py 补取（不覆盖已存的文件）", log_file)
    log(DONE_MARK.format(name) + "（开始）", log_file, console=False)  # 先记下，补取半路出事下次也不再重复取
    code, out = run_fetch(folder)
    log("  开盘tick.py 输出（最后 25 行）：\n" + "\n".join("    " + x for x in out[-25:]), log_file)
    gone = sorted({x.split(":")[0].strip() for x in out if "交易所没有这个合约" in x})  # 写错或已到期：补不来，不算缺
    miss2 = [f for f in missing_files(data, files) if not any(g in f for g in gone)]  # 这个合约和带它的价差都不算缺
    if data.exists() and not miss2:
        log(f"{stamp} {name} 补取后都齐了（开盘tick.py 返回 {code}）"
            + (f"；{'、'.join(gone)} 交易所没有（写错或已到期，改 合约.txt）" if gone else ""), log_file)
        return 0
    if not data.exists() and any("里没有开盘" in x for x in out) and code == 0:
        log(f"{stamp} {name} 补取后还是没有这次开盘的文件夹：天勤往回取到的这段里没有这次开盘，多半是节假日没开盘，不通知", log_file)
        return 0
    left = miss2 if data.exists() else [f"整个 数据/{name}/"]
    log(f"{stamp} ⚠ {name} 补取后还缺 {len(left)} 个：{'、'.join(left)}（开盘tick.py 返回 {code}）", log_file)
    if notify_on:
        notify(f"⚠ 开盘 tick 补取后还缺（{name}）", [f"缺：{'、'.join(left)[:120]}",
               "天勤免费版只能往回取约 80 分钟，详情看 开盘tick/运行日志.txt"], log_file)
    return 1


def main():
    p = argparse.ArgumentParser(description="开盘前体检 / tick 补取")
    p.add_argument("--补取", dest="refetch", action="store_true", help="查最近一次开盘的 tick 缺没缺，缺了跑一次 开盘tick.py")
    p.add_argument("--dry-run", action="store_true", help="补取时只查，不真去取数")
    p.add_argument("--开盘", dest="name", help="补取时指定开盘，如 2026-11-10_0900（默认最近一次）")
    p.add_argument("--目录", dest="folder", default=str(HERE), help="在这个文件夹里 import / 查数据（测试用副本）")
    p.add_argument("--模块", dest="modules", help="只试这几个模块，逗号分开（默认 MODULES + futures_brief）")
    p.add_argument("--日志", dest="log", help="日志写到这个文件（默认 目录/运行日志.txt）")
    p.add_argument("--不通知", dest="quiet", action="store_true", help="失败也不弹通知")
    p.add_argument("--来自", dest="who", default="", help="谁带起来的（写进日志）")
    a = p.parse_args()
    folder = Path(a.folder).resolve()
    log_file = Path(a.log) if a.log else folder / "运行日志.txt"
    if a.refetch:
        sys.exit(run_refetch(folder, log_file, not a.quiet, a.dry_run, a.name))
    if a.modules:
        mods, up = a.modules.split(","), []
    else:
        mods, up = MODULES, (UP_MODULES if folder == HERE else [])
    sys.exit(run_check(folder, mods, up, log_file, not a.quiet, a.who))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # 自检自己出错也要留个记号
        import traceback
        log(f"[自检 {datetime.now():%Y-%m-%d %H:%M:%S}] 自检.py 自己出错：{type(e).__name__} {e}\n{traceback.format_exc()}", LOG_FILE)
        sys.exit(3)
