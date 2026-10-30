"""竞价浮层：把 竞价判断.py 算出来的 ✅ ▲ ❌ 贴在博易云的每个格子上。

不用手动跑：竞价判断.py 启动时（20:57、8:57）把它带起来，开盘后 1 分钟（21:01、9:01）自己关掉。
它是盖在博易云上面的一层透明窗口（Windows 的分层窗口；不用 tkinter，tkinter 一建窗口就会抢走键盘焦点）：
鼠标点上去直接点到博易云（点不到浮层），不抢键盘焦点，不进任务栏。
格子里是哪组价差、标签贴在哪，看同目录的「看板.txt」。那里的位置按博易云自己的像素写：博易云不认屏幕缩放，
Windows 把它整个放大（这台电脑 150%），贴的时候自动乘上这个倍数。

每个格子贴一个标签，在「套利分析……」那一行的右边，不盖住图（针画在图的右边缘）：
  ✅ 大概率整根开到针附近（真）  ▲ 大概率开不到针、但离昨收有段距离（半真）  ❌ 大概率假
  ○ 没针  ？ 不判断
标签左边并排一个小框，两行写两条腿的竞价成交手数：「sc2701 竞 15」= sc2701 竞价成交 15 手（黄）
（10-24 晚去掉了卖一、买一的挂单手数：两年回放看过，对判断没用）。
大机会（竞价后立刻能成交那边偏出门槛 2 倍以上）时，sc、nr、fu、bu 跨月格子的符号后面多一个紫块「重3.6」（几倍门槛），
再按情况加红框「腿2秒进」（主力竞价比腿1 动得少，腿2 晚两秒才进多半亏）、黄框「量少」（冷门腿竞价不到持仓 0.7‰）。
浮层上一律写短（「首」「半」「腿2」），不写组合名；这几个块一律放第二行首笔区间前面，盘口框放在公式那一行下面（10-29 晚定：每格按品种名四个字、价差四位的最坏情况排，样子统一，见看板.txt 的「公式宽」）。
菜单栏空白处还有一个小提示：等竞价时写「竞价判断：等 20:59」，出错变红字。
紧急关掉（程序出错、挡住要看的东西时）：按 Ctrl + Alt + Q，标签马上全部消失（博易云在不在前面都行）；
或者点菜单栏小提示右边的 ×（点它不抢博易云的焦点），或者双击桌面上的「关竞价浮层」（关浮层.pyw）。关掉以后这次开盘不会再出来，竞价判断的网页和 csv 照样写。
格子对不上就不贴：每次贴之前，把每格标题左边那一小条（合约名）跟 --test 时存的样子比一下（复盘数据/看板样子/），
翻了页、换了图，那格就不贴；被别的窗口挡住的格子也不贴（菜单栏的小提示也一样），切回博易云 1 秒内贴上。

手动跑（在本文件夹里，用原版 Python）：
  python 竞价浮层.py --test            博易云开着时跑：每格贴一个样例标签、画出格子边框，20 秒后消失；
                                       顺带存下每格标题的样子（换了图、挪了窗口、改了看板.txt 都要重跑一次）
  python 竞价浮层.py --mock 截图.png    不开窗口，把标签画在一张博易云全屏截图上，存成「截图_浮层.png」
                                       （加 --json 某次开盘的 竞价判断.json 用那次的判断，不加用样例）
  python 竞价浮层.py --json 某个.json --no-check   预览用：不跟 --test 存的样子比，露在外面的格子都贴（看板换了图时会贴错格子）
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import site
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOARD_FILE = HERE / "看板.txt"
REF_DIR = HERE / "复盘数据" / "看板样子"  # --test 时存的每格标题样子
LOG_FILE = HERE / "运行日志.txt"
if sys.stdout is None:  # 被 pythonw 带起来时没有控制台，输出写进运行日志
    sys.stdout = sys.stderr = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
else:  # 手动跑、输出接到管道里时按 GBK 编码，✅ ▲ 这类印不出来就换成 ?，不报错
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

# 日记库 venv 里的 python.exe 被 Windows 智能应用控制拦了，用原版 Python 跑，从这里加载装好的包（Pillow、numpy）
VENV_SITE = Path.home() / ".venvs" / "vault" / "Lib" / "site-packages"
if VENV_SITE.exists():
    site.addsitedir(str(VENV_SITE))

from PIL import Image, ImageDraw, ImageFont, ImageGrab  # noqa: E402

BG, FG, MUTED, LINE = (24, 28, 36), (240, 240, 240), (160, 160, 160), (90, 96, 110)
COLORS = {"真": (46, 170, 80), "半真": (245, 166, 35), "假": (235, 70, 70), "没针": (130, 130, 130), "不判断": (130, 130, 130)}
FONT_FILE = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc"  # 微软雅黑
BOLD_FILE = FONT_FILE.with_name("msyhbd.ttc")  # 微软雅黑粗体（重仓标记用）
# 位置的默认值（按博易云自己的像素，从窗口里面的左上角算）；看板.txt 里写了就用那边的。
# 博易云不认屏幕缩放（DPI 96），Windows 把它整个放大（这台电脑缩放 150%，放大 1.5 倍），贴到屏幕上时乘这个倍数（dpi_ratio）
DEFAULTS = {"网格左": 19, "网格上": 47, "网格右": 0, "网格下": 20, "标签上": 20, "标签右": 8, "字号": 10,
            "公式宽": 410, "提示左": 560, "提示上": 2, "提示字号": 13}
ROW_H = 17  # 公式那一行字的高度（博易云的像素），量字写到哪用；跟标签字号无关
SECOND = (120, 200, 255)  # 标签第二行（首笔区间）浅蓝
GREY = (170, 170, 170)  # 盘口框里的「竞」
VOL = (235, 200, 80)  # 盘口框：竞价成交手数黄
TAG_COLORS = {"重": (150, 70, 225), "快": (235, 70, 70), "少": VOL, "薄": (235, 120, 20)}  # 重仓标记：「重…」紫底白字、腿2秒进 红框、量少 黄框；薄大 橙底白字（10-30，顶替重）
FILLED = ("重", "薄")  # 实心底、白字的样式
HEAD_W, HEAD_H = 0.27, 15  # 比对用的标题条：格子宽度的前 27%、格子最上面 15 像素（合约名那一行；再宽会带进右边时间的第一个字）
MISMATCH = 0.06  # 标题条跟存的样子有超过 6% 的像素对不上，就算这格换了
GAP = 8  # 标签左边离公式那行字至少空 8 像素；放不下全名就去掉组合名，不盖住公式后面的价差数
LIFE = 600  # 最多开 10 分钟（竞价判断.py 出意外没写结果时，浮层自己关掉）
QUIET = (3, 5)  # 开盘前 3 秒到开盘后 5 秒不截屏比对格子（标签照样显示），不跟开盘那一下抢电脑
SAMPLES = [("真", "首笔 -9.0 ~ -7.6"), ("半真", "首笔 -21 ~ -9"), ("假", "首笔 -8.9 ~ -7.3"),
           ("没针", "首笔 -11 ~ -9"), ("不判断", "竞价没成交")]
TAG_SAMPLES = {"sc": ("首笔 -1.44  首笔半 -0.82\n首笔区间 (-4.0 ~ -0.8)", (("重2.9", "重"), ("腿2秒进", "快"))),  # --test 时 sc、nr 跨月格子贴带重仓标记的 ▲
               "nr": ("首笔 -40 ~ -15", (("重2.3", "重"), ("量少", "少")))}

# 用自己的一份 user32，设参数类型不影响同一进程里别的代码
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, ctypes.c_uint]
user32.GetAncestor.restype = wt.HWND
user32.GetForegroundWindow.restype = wt.HWND
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.GetDpiForWindow.argtypes = [wt.HWND]
user32.GetDpiForWindow.restype = wt.UINT
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.MonitorFromWindow.restype = wt.HMONITOR
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]
GWL_EXSTYLE, GA_ROOT = -20, 2
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x8, 0x20, 0x80, 0x80000, 0x08000000
WS_POPUP, SW_SHOWNOACTIVATE, WM_TIMER, ULW_ALPHA = 0x80000000, 4, 0x0113, 2
HWND_TOPMOST = -1
WM_HOTKEY, MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0312, 0x1, 0x2, 0x4000
WM_MOUSEACTIVATE, MA_NOACTIVATE, WM_LBUTTONUP, SW_HIDE = 0x0021, 3, 0x0202, 0
STOP_KEY = (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("Q"), "Ctrl + Alt + Q")  # 紧急关掉浮层的快捷键
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
                ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
                ("hIconSm", wt.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte), ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wt.ATOM
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), wt.HDC,
                                       ctypes.POINTER(wt.POINT), wt.COLORREF, ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]


# ---------- 看板.txt ----------

def read_board(f: Path = BOARD_FILE) -> dict:
    """看板.txt：格子（一行一排，从左到右，「交易所.代码,交易所.代码」，空格子写 -）和位置参数（像素）。"""
    rows, opts = [], dict(DEFAULTS)
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if not line:
                continue
            if "=" in line:
                k, v = (x.strip() for x in line.split("=", 1))
                opts[k] = float(v)
            else:
                rows.append(line.split())
    board = {"格子": rows, "参数": opts}
    try:  # --test 时量的每排格子实际在哪（博易云的格子不一定一样高）
        lines = json.loads((REF_DIR / "窗口.json").read_text(encoding="utf-8")).get("行线")
        if lines and len(lines) == len(rows) + 1:
            board["行线"] = lines
    except (OSError, ValueError):
        pass
    return board


def pair_key(item: str) -> str:
    """INE.sc2701,INE.sc2612 → sc2701-sc2612（和开盘复盘、竞价判断里的「组合」一样）；空格子 → 空。"""
    legs = item.split(",")
    return "-".join(x.split(".")[-1] for x in legs) if len(legs) == 2 else ""


def label_name(s1: str, s2: str) -> str:
    """标签上的组合名：同品种写 sc2701-2612，跨品种写全 nr2612-ru2701。"""
    c1, c2 = s1.split(".")[-1], s2.split(".")[-1]
    p1, p2 = (re.match(r"[A-Za-z]+", c).group().lower() for c in (c1, c2))
    return f"{c1}-{c2[len(p2):]}" if p1 == p2 else f"{c1}-{c2}"


def board_pairs(board: dict) -> list[tuple[str, str]]:
    """看板上所有的组合 (腿1, 腿2)，按格子顺序。"""
    return [tuple(x.split(",")) for row in board["格子"] for x in row if len(x.split(",")) == 2]


def cells(rect, board: dict) -> list[tuple]:
    """每个格子 (行, 列, 组合, 左, 上, 右, 下)；rect = 博易云窗口里面的 (左, 上, 右, 下)。"""
    o, rows = board["参数"], board["格子"]
    if not rows:
        return []
    n_r, n_c = len(rows), max(len(r) for r in rows)
    x0, y0 = rect[0] + o["网格左"], rect[1] + o["网格上"]
    w, h = (rect[2] - o["网格右"] - x0) / n_c, (rect[3] - o["网格下"] - y0) / n_r
    ys = ([rect[1] + y for y in board["行线"]] if len(board.get("行线") or ()) == n_r + 1
          else [y0 + i * h for i in range(n_r + 1)])  # 有量好的行线就用它，没有就按看板.txt 平分
    return [(i, j, pair_key(item), x0 + j * w, ys[i], x0 + (j + 1) * w, ys[i + 1])
            for i, row in enumerate(rows) for j, item in enumerate(row)]


def find_rows(shot, rect, board: dict) -> list | None:
    """--test 时从博易云的截图量每排格子的分界线（格子之间那条横着的亮线）：在按看板.txt 平分算的位置上下各找 6% 格高，
    一整行大部分像素都亮的就是。返回每排的上边 + 最后一排的下边（相对窗口里面的上边，单位同 rect）；有一条没找到就返回 None（照旧平分）。"""
    import numpy as np
    cs = cells(rect, {"格子": board["格子"], "参数": board["参数"]})
    if not cs:
        return None
    n_r = len(board["格子"])
    left, right = round(min(c[3] for c in cs)), round(max(c[5] for c in cs))
    a = np.asarray(shot.convert("L"), dtype=np.int16)
    tops = sorted({c[4] for c in cs})
    h = (max(c[6] for c in cs) - tops[0]) / n_r
    out = [tops[0] - rect[1]]
    for i in range(1, n_r):
        lo, hi = round(tops[i] - rect[1] - .06 * h), round(tops[i] - rect[1] + .06 * h)
        frac = (a[lo:hi, left - rect[0]:right - rect[0]] > 60).mean(axis=1)
        if not len(frac) or frac.max() < .6:
            return None
        out.append(lo + int(frac.argmax()))
    out.append(max(c[6] for c in cs) - rect[1])
    return out


# ---------- 画 ----------

def font(size: float):
    return ImageFont.truetype(str(FONT_FILE), max(6, round(size)))


def bold(size: float):
    return ImageFont.truetype(str(BOLD_FILE if BOLD_FILE.exists() else FONT_FILE), max(6, round(size)))


def symbol(d, kind: str, x: float, y: float, s: float) -> None:
    """画符号（Tk 和截图都显示不了彩色 emoji，自己画）：✅ 绿底白勾、▲ 橙色三角、❌ 红叉、○ 灰圈、？ 灰问号。
    x, y 是左上角，s 是边长。"""
    c, w = COLORS[kind], max(2, round(s / 7))
    if kind == "真":
        d.rounded_rectangle([x, y, x + s, y + s], radius=s / 5, fill=c)
        d.line([(x + s * .22, y + s * .52), (x + s * .43, y + s * .73), (x + s * .80, y + s * .30)],
               fill=(255, 255, 255), width=w, joint="curve")
    elif kind == "半真":
        d.polygon([(x + s / 2, y + s * .06), (x + s, y + s * .94), (x, y + s * .94)], fill=c)
    elif kind == "假":
        for a, b in (((.15, .15), (.85, .85)), ((.85, .15), (.15, .85))):
            d.line([(x + s * a[0], y + s * a[1]), (x + s * b[0], y + s * b[1])], fill=c, width=w + 1)
    elif kind == "没针":
        d.ellipse([x + s * .12, y + s * .12, x + s * .88, y + s * .88], outline=c, width=w)
    else:
        d.text((x + s / 2, y + s / 2), "?", font=font(s), fill=c, anchor="mm")


def tag_width(d, t: str, size: float) -> float:
    """一个重仓标记（「重3.6」「腿2秒进」「量少」）连同后面的空隙有多宽。"""
    return d.textlength(t, font=bold(size)) + size * 1.0


def badge_width(d, text: str, size: float, tags=(), down: bool = False) -> float:
    """标签整个有多宽。down = 重仓标记挪到第二行（首笔区间后面）。"""
    if "\n" in text:  # 两行的标签（第二行是首笔区间）：取两行里宽的那行
        a, b = text.split("\n", 1)
        return max(badge_width(d, a, size, () if down else tags), badge_width(d, b, size, tags if down else ()))
    pad = size * .45
    return pad + size + pad * .8 + sum(tag_width(d, t, size) for t, _ in tags) + d.textlength(text, font=font(size)) + pad


def row1_width(d, text: str, size: float, tags=(), down: bool = False) -> float:
    """标签在公式那一行占多宽（会不会盖住价差数看这个）。重仓标记没挪下去时整个框一样宽；挪下去了只算第一行。"""
    if down and "\n" in text:
        return badge_width(d, text.split("\n", 1)[0], size)
    return badge_width(d, text, size, tags)


def draw_tags(d, tags, x: float, mid: float, h: float, size: float) -> float:
    """从 x 开始画重仓标记，竖着居中在 mid；返回画完以后的 x。"""
    for t, style in tags:
        tw, c = tag_width(d, t, size) - size * .3, TAG_COLORS.get(style, MUTED)
        box = [x, mid - h * .38, x + tw, mid + h * .38]
        if style in FILLED:
            d.rounded_rectangle(box, radius=size * .25, fill=c)
        else:
            d.rounded_rectangle(box, radius=size * .25, outline=c, width=max(1, round(size / 9)))
        d.text((x + tw / 2, mid), t, font=bold(size), fill=(255, 255, 255) if style in FILLED else c, anchor="mm")
        x += tw + size * .3
    return x


SHORT_TAGS = {"腿2秒进": "腿2"}  # 浮层上写短名（10-29 晚他定的），网页和 csv 还是全称


def short(text: str) -> str:
    """浮层标签第一行写短：「首笔 423.2  首笔半 442.1」→「首423.2 半442.1」；第二行「首笔区间」不动。"""
    first, *rest = text.split("\n", 1)
    first = first.replace("首笔半 ", "半").replace("首笔 ", "首").replace("  ", " ")
    return "\n".join([first] + rest)


def badge(d, kind: str, text: str, right: float, top: float, size: float, tags=(), down: bool = False) -> None:
    """一个标签，右上角对着 (right, top)；size = 字号（像素）。
    tags = 重仓标记 ((字, 样式), …)，画在符号后面：样式「重」紫底白字，「薄」橙底白字（薄大），「快」红框红字，「少」黄框黄字。
    down = 重仓标记挪到第二行首笔区间后面（第一行放不下、会盖住公式后面的价差数时）：
    框成台阶形，第一行窄、第二行宽，第二行在公式那一行下面。"""
    f = font(size)
    pad, s, h = size * .45, size * 1.0, size * 1.5
    full, (text, second) = text, (text.split("\n", 1) + [""])[:2]  # 有第二行（首笔区间）就写在第一行字下面，框跟着变高
    down = down and bool(tags)
    x0 = right - badge_width(d, full, size, tags, down)
    lw = max(1, round(size / 14))
    if down:
        a = right - badge_width(d, text, size)  # 第一行的左边
        b = right - badge_width(d, second, size, tags)  # 第二行的左边
        r = size * .3
        d.rounded_rectangle([a, top, right, top + h + r], radius=r, fill=BG)
        d.rounded_rectangle([b, top + h, right, top + h * 2], radius=r, fill=BG)
        pts = [(a, top + h), (a, top), (right, top), (right, top + h * 2), (b, top + h * 2), (b, top + h), (a, top + h)]
        d.line(pts, fill=COLORS[kind], width=lw, joint="curve")
        x1 = a + pad
    else:
        d.rounded_rectangle([x0, top, right, top + h * (2 if second else 1)], radius=size * .3, fill=BG,
                            outline=COLORS[kind], width=lw)
        x1 = x0 + pad
    symbol(d, kind, x1, top + (h - s) / 2, s)
    x = draw_tags(d, () if down else tags, x1 + s + pad * .8, top + h / 2, h, size)
    d.text((x, top + h / 2), text, font=f, fill=MUTED if kind in ("没针", "不判断") else FG, anchor="lm")
    if second or down:
        x = (b + pad + s + pad * .8) if down else (x0 + pad + s + pad * .8)
        x = draw_tags(d, tags if down else (), x, top + h * 1.5, h, size)
        d.text((x, top + h * 1.5), second, font=f, fill=SECOND, anchor="lm")


def book_cols(d, book, size: float) -> tuple[list, list]:
    """盘口框每行的几段字 [(字, 颜色)] 和每一列的宽度（两行按「合约 / 竞 / 竞价手数」对齐）。
    book = ((合约, 竞价手数), …)，一行一条腿；没有的写 —。上万手写成「3.4万」，框不会太宽。
    以前的 json 是 (合约, 竞价手数, 卖一量, 买一量)，更早的是 (合约, 卖一量, 买一量)：10-24 晚起只画竞价手数，挂单量不画。"""
    def n(x):
        return "—" if x is None else f"{x / 10000:.1f}万" if x >= 10000 else f"{x:.0f}"
    book = [(x[0], None if len(x) == 3 else x[1]) for x in book]
    rows = [[(c, FG), (" 竞 ", GREY), (n(v), VOL)] for c, v in book]
    f = font(size)
    return rows, [max(d.textlength(r[k][0], font=f) for r in rows) for k in range(3)]


def book_width(d, book, size: float) -> float:
    return sum(book_cols(d, book, size)[1]) + size


def book_box(d, book, right: float, top: float, size: float) -> None:
    """标签左边的盘口框：每条腿竞价成交几手，右上角对着 (right, top)。"""
    rows, col = book_cols(d, book, size)
    f, lh, pad = font(size), size * 1.45, size * .5
    x0 = right - sum(col) - pad * 2
    d.rounded_rectangle([x0, top, right, top + lh * len(rows) + pad], radius=size * .3, fill=BG, outline=LINE,
                        width=max(1, round(size / 14)))
    for r, parts in enumerate(rows):
        x, y = x0 + pad, top + pad / 2 + lh * (r + .5)
        for k, (t, c) in enumerate(parts):
            d.text((x + (col[k] - d.textlength(t, font=f) if k == 2 else 0), y), t, font=f, fill=c, anchor="lm")
            x += col[k]


def chip(d, text: str, x: float, y: float, size: float, red: bool = False) -> None:
    """菜单栏空白处的小提示。返回它的框 (左, 上, 右, 下)，× 按钮贴在它右边。"""
    f = font(size)
    pad = size * .5
    box = (x, y, x + d.textlength(text, font=f) + pad * 2, y + size * 1.45)
    d.rounded_rectangle(box, radius=size * .3, fill=BG, outline=COLORS["假"] if red else LINE)
    d.text((x + pad, y + size * .72), text, font=f, fill=(255, 130, 130) if red else FG, anchor="lm")
    return box


def close_button(side: int):
    """× 按钮的图：边长 side 的小方块。"""
    img = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, side - 1, side - 1], radius=side * .2, fill=BG, outline=COLORS["假"], width=max(1, side // 14))
    m, w = side * .3, max(2, round(side / 9))
    d.line([m, m, side - m, side - m], fill=(255, 130, 130), width=w)
    d.line([m, side - m, side - m, m], fill=(255, 130, 130), width=w)
    return img


def scaled(board: dict, scale: float) -> dict:
    """位置参数乘倍数（博易云的像素 → 屏幕像素，或 → 缩小的截图）。"""
    p = {k: v * scale for k, v in board["参数"].items() if k != "倍数"}
    out = {"格子": board["格子"], "参数": p | {"倍数": board["参数"].get("倍数", 1) * scale}}
    if board.get("行线"):
        out["行线"] = [y * scale for y in board["行线"]]
    return out


def draw(img, rect, board: dict, items: dict, note: dict | None, show: set | None, scale: float = 1.0,
         outline: bool = False, ends: dict | None = None, info: dict | None = None) -> dict:
    """在 img 上画标签。rect = 博易云窗口里面在 img 里的位置；items = {组合: (档, 文字, 盘口, 重仓标记)}，盘口见 book_cols
    （空 = 不画盘口框），重仓标记见 badge（没有这一项或空 = 不画）；
    note = 小提示 {"文字", "红"}；show = 要贴的格子 {(行, 列)}（None = 都贴）；scale：截图比真屏幕小时按比例缩；
    outline：画格子边框（--test 用）；ends = 每格公式那行字写到哪（text_ends），
    标签放不下全名时去掉组合名（「sc2701-2612  首笔 …」→「首笔 …」）。
    每格：标签靠右，盘口框在它左边、顶边对齐；盘口框会盖住公式后面的价差数时，往下挪一行（还在标签左边，不挡右边的针）。
    返回贴了标签的格子 {(行, 列): 公式那一行被占到的最左 x}（下次量字时躲开标签用）。"""
    d = ImageDraw.Draw(img)
    b = scaled(board, scale)
    o = b["参数"]
    size = o["字号"]
    lefts = {}
    for i, j, key, x0, y0, x1, y1 in cells(rect, b):
        if outline:
            d.rectangle([x0, y0, x1 - 1, y1 - 1], outline=(0, 200, 255))
        if key in items and (show is None or (i, j) in show):
            kind, text, book, *more = items[key]
            tags = more[0] if more else ()
            right, top = x1 - o["标签右"], y0 + o["标签上"]
            # 10-29 晚他定的：每格都按最坏情况排（品种名四个字，如「合成橡胶2701-合成橡胶2612」，价差四位，如 -999），
            # 不按每格实际的字量，免得每格样子不一样：公式那一行写到离格子左边「公式宽」（看板.txt，博易云的像素）。
            # 所以第一行只写「▲ 首-217.0 半-203.5」（不写组合名），重仓标记一律放第二行，盘口框一律放在公式那一行下面；
            # 第一行还放不下（数字特别长）就把「半…」挪到第二行开头。
            text = short(re.sub(r"^\S+-\S+  ", "", text))
            tags = tuple((SHORT_TAGS.get(t, t), st) for t, st in tags)
            first, *rest = text.split("\n", 1)
            if " 半" in first and right - badge_width(d, first, size) < x0 + o["公式宽"]:
                first, half = first.split(" 半", 1)
                text = "\n".join([first, "半" + half + ("  " + rest[0] if rest else "")])
            down = bool(tags)
            if down and "\n" not in text:
                text += "\n"
            badge(d, kind, text, right, top, size, tags, down)
            left = right - badge_width(d, text, size, tags, down)
            lefts[(i, j)] = right - row1_width(d, text, size, tags, down)
            if book:
                book_box(d, book, left - size * .6, top + ROW_H * o["倍数"], size)
    if note and note.get("文字"):
        box = chip(d, note["文字"], rect[0] + o["提示左"], rect[1] + o["提示上"], o["提示字号"], note.get("红", False))
        if info is not None:
            info["提示"] = box
    return lefts


def product(s: str) -> str:
    """INE.sc2701 → sc"""
    return re.match(r"[A-Za-z]+", s.split(".")[-1]).group().lower()


def sample_items(board: dict) -> dict:
    """--test、--mock 的样例：各种符号轮着来，字的写法跟真的一样（组合名 + 首笔区间，右边盘口框的竞价手数是编的）；
    sc、nr 跨月的格子贴带重仓标记的 ▲（实盘只有大机会时才有）。"""
    out = {}
    for n, x in enumerate(x for row in board["格子"] for x in row if pair_key(x)):
        s1, s2 = x.split(",")
        (kind, text), tags = SAMPLES[n % len(SAMPLES)], ()
        if product(s1) == product(s2) and product(s1) in TAG_SAMPLES:
            kind, (text, tags) = "半真", TAG_SAMPLES[product(s1)]
        book = tuple((leg.split(".")[-1], 15 + 120 * k + n % 9) for k, leg in enumerate((s1, s2)))
        out[pair_key(x)] = (kind, f"{label_name(s1, s2)}  {text}", book, tags)
    return out


def items_of(data: dict) -> tuple[dict, dict | None]:
    """竞价判断.json → (要贴的标签 {组合: (档, 文字, 盘口, 重仓标记)}, 小提示)。"""
    items = ({k: (v["档"], v["文字"], tuple(tuple(x) for x in v.get("盘口") or ()), tuple(tuple(x) for x in v.get("标记") or ()))
              for k, v in (data.get("组合") or {}).items()} if data.get("状态") == "结果" else {})
    return items, {"文字": data.get("提示", ""), "红": bool(data.get("红"))}


# ---------- 找博易云、比对格子 ----------

def dpi_aware() -> None:
    """本进程按屏幕真实像素算位置（不这样的话 Windows 会按缩放 150% 把坐标换算一遍，贴的位置全是错的）。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


def dpi_ratio(h) -> float:
    """博易云的 1 个像素在屏幕上是几个像素：屏幕的 DPI ÷ 博易云窗口的 DPI（这台电脑 144 ÷ 96 = 1.5）。"""
    try:
        x, y = wt.UINT(), wt.UINT()
        ctypes.windll.shcore.GetDpiForMonitor(user32.MonitorFromWindow(h, 2), 0, ctypes.byref(x), ctypes.byref(y))
        return x.value / max(1, user32.GetDpiForWindow(h))
    except (AttributeError, OSError):
        return 1.0


def exe_name(pid: int) -> str:
    h = kernel32.OpenProcess(0x1000, False, pid)  # 只查询有限信息
    if not h:
        return ""
    try:
        buf, n = ctypes.create_unicode_buffer(520), wt.DWORD(520)
        return buf.value if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
    finally:
        kernel32.CloseHandle(h)


def client_rect(h) -> tuple | None:
    """窗口里面（不含边框、标题栏）的屏幕坐标。"""
    r = wt.RECT()
    if not user32.GetClientRect(h, ctypes.byref(r)):
        return None
    p = wt.POINT(0, 0)
    user32.ClientToScreen(h, ctypes.byref(p))
    return p.x, p.y, p.x + r.right, p.y + r.bottom


def find_pobo() -> tuple | None:
    """博易云的主窗口：pobo.exe 里可见、没最小化、最大的那个。返回 (hwnd, 窗口里面的屏幕坐标)；没开或最小化了返回 None。"""
    found, names = [], {}

    @WNDENUMPROC
    def cb(h, _):
        if not user32.IsWindowVisible(h) or user32.IsIconic(h):
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value not in names:
            names[pid.value] = os.path.basename(exe_name(pid.value)).lower()
        if names[pid.value] == "pobo.exe":
            r = client_rect(h)
            if r and (r[2] - r[0]) * (r[3] - r[1]) > 400 * 300:
                found.append((h, r))
        return True

    user32.EnumWindows(cb, 0)
    return max(found, key=lambda x: (x[1][2] - x[1][0]) * (x[1][3] - x[1][1])) if found else None


def monitor_of(h) -> tuple:
    """h 所在的那块屏幕（没有 h 就是主屏幕）的 (左, 上, 右, 下)。"""
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(user32.MonitorFromWindow(h, 1), ctypes.byref(mi))  # 1 = 找不到就用主屏幕
    r = mi.rcMonitor
    return r.left, r.top, r.right, r.bottom


def visible_cells(hwnd, rect, board: dict) -> set:
    """标签那个位置露出来的是博易云的格子（没被别的窗口挡住）。浮层本身点不到，WindowFromPoint 会穿过它。"""
    out, o = set(), board["参数"]
    for i, j, key, x0, y0, x1, y1 in cells(rect, board):
        h = user32.WindowFromPoint(wt.POINT(int(x1 - o["标签右"] - 40), int(y0 + o["标签上"] + o["字号"] * .75)))
        if h and user32.GetAncestor(h, GA_ROOT) == hwnd:
            out.add((i, j))
    return out


def chip_visible(hwnd, rect, board: dict) -> bool:
    """菜单栏小提示的位置露出来的是博易云（没被别的窗口挡住）；挡住了就不画，免得浮在别的窗口上。"""
    o = board["参数"]
    h = user32.WindowFromPoint(wt.POINT(int(rect[0] + o["提示左"] + o["提示字号"]), int(rect[1] + o["提示上"] + o["提示字号"] * .7)))
    return bool(h) and user32.GetAncestor(h, GA_ROOT) == hwnd


def head_boxes(rect, board: dict) -> dict:
    """每格标题左边那一小条（合约名），相对博易云窗口里面左上角的坐标。"""
    k = board["参数"].get("倍数", 1)
    out = {}
    for i, j, key, x0, y0, x1, y1 in cells(rect, board):
        if key:  # 宽高按固定值取整，免得行线的小数让同一格每次截的大小差 1 像素
            left, top = round(x0 - rect[0] + 2 * k), round(y0 - rect[1] + k)
            out[(i, j)] = (left, top, left + round((x1 - x0) * HEAD_W - 2 * k), top + round((HEAD_H - 1) * k))
    return out


def grab(rect):
    """截博易云窗口里面。浮层贴着的标签也会被截进来（这台电脑上分层窗口照样截得到）：
    比对格子用的标题条在标签上面，不受影响；量公式那行字写到哪时要躲开标签（text_ends 的 clip）。"""
    return ImageGrab.grab(bbox=rect, all_screens=True)


def cell_name(i: int, j: int) -> str:
    return f"{i + 1}行{j + 1}列"


def text_ends(img, rect, board: dict, clip: dict | None = None) -> dict:
    """每格公式那一行（标签要贴的那一条）的字写到哪：{(行, 列): 离格子左边几像素}。rect = 博易云窗口里面在 img 里的位置。
    clip = {(行, 列): 已经贴着的标签左边在 img 里的 x}：截屏会把标签自己也截进来，这些格子只看标签左边那段。
    字一直写到标签边上（比如开盘后价差数变长了），量出来就是标签左边，标签会去掉组合名、把地方让出来。"""
    import numpy as np
    o, out = board["参数"], {}
    for i, j, key, x0, y0, x1, y1 in cells(rect, board):
        top = y0 + o["标签上"] + 2
        right = round(x1) - 2
        if clip and (i, j) in clip:
            right = min(right, round(clip[(i, j)]) - 2)
        if right <= round(x0):
            out[(i, j)] = 0
            continue
        band = img.crop((round(x0), round(top), right, round(top + ROW_H * o["倍数"] - 4)))
        cols = np.flatnonzero((np.asarray(band.convert("L")) > 100).any(axis=0))
        out[(i, j)] = int(cols[-1]) + 1 if len(cols) else 0
    return out


def save_refs(shot, rect, board: dict, lines: list | None = None) -> int:
    """--test 时存下每格标题的样子，开盘前比对用。shot = 博易云窗口里面的截图；lines = 量好的行线（博易云的像素）。"""
    REF_DIR.mkdir(parents=True, exist_ok=True)
    keys = {}
    for (i, j), box in head_boxes(rect, board).items():
        shot.crop(box).save(REF_DIR / f"{cell_name(i, j)}.png")
        keys[cell_name(i, j)] = pair_key(board["格子"][i][j])
    (REF_DIR / "窗口.json").write_text(json.dumps({"大小": [rect[2] - rect[0], rect[3] - rect[1]], "格子": keys,
                                                   "存于": f"{datetime.now():%Y-%m-%d %H:%M}"}
                                                  | ({"行线": [round(y, 3) for y in lines]} if lines else {}),
                                                  ensure_ascii=False),
                                       encoding="utf-8")
    return len(keys)


def same(a, b) -> bool:
    """两张标题条是不是同一个：只看字（亮的像素），底色变了（选中的格子标题会变色）也认得出。"""
    import numpy as np
    if abs(a.width - b.width) > 2 or abs(a.height - b.height) > 2:
        return False
    x, y = np.asarray(a.convert("L")) > 100, np.asarray(b.convert("L")) > 100
    h, w = min(x.shape[0], y.shape[0]) - 2, min(x.shape[1], y.shape[1]) - 2  # 大小差一两个像素（取整）就按小的那个比
    # 上下左右各容许错开 1 像素（行线有小数，取整后位置可能差 1 像素，细笔画的字一错开就对不上了）
    return min(float((x[1:1 + h, 1:1 + w] != y[1 + dy:1 + dy + h, 1 + dx:1 + dx + w]).mean())
               for dy in (-1, 0, 1) for dx in (-1, 0, 1)) <= MISMATCH


def check_layout(shot, rect, board: dict, vis: set | None = None) -> tuple[set | None, str]:
    """跟 --test 时存的样子比：返回 (对得上的格子, 说明)；没存过返回 (None, 说明)，都贴。shot = 博易云窗口里面的截图。
    vis = 露在外面的格子：被别的窗口挡住的格子截到的是别的窗口，不比（反正也不贴），免得报「对不上」。"""
    meta_f = REF_DIR / "窗口.json"
    if not meta_f.exists():
        return None, "还没跑过 --test，没比对格子"
    meta = json.loads(meta_f.read_text(encoding="utf-8"))
    if meta.get("大小") != [rect[2] - rect[0], rect[3] - rect[1]]:
        return set(), "博易云窗口大小变了，标签先不贴（重跑 --test）"
    ok, bad = set(), []
    for (i, j), box in head_boxes(rect, board).items():
        if vis is not None and (i, j) not in vis:
            continue
        ref = REF_DIR / f"{cell_name(i, j)}.png"
        if (ref.exists() and meta.get("格子", {}).get(cell_name(i, j)) == pair_key(board["格子"][i][j])
                and same(shot.crop(box), Image.open(ref))):
            ok.add((i, j))
        else:
            bad.append(cell_name(i, j))
    if len(bad) > 3:
        return ok, f"{len(bad)} 格跟存的样子对不上，没贴（换了图就改看板.txt、重跑 --test）"
    return ok, (f"{'、'.join(bad)}跟存的样子对不上，没贴" if bad else "")


# ---------- 浮层窗口 ----------

def title_of(h) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(h, buf, 256)
    return buf.value


class Overlay:
    """盖住整块屏幕的透明窗口。直接用 Windows 的分层窗口，不用 tkinter（tkinter 一建窗口就会抢走键盘焦点，哪怕窗口是藏着的）。
    点不到（鼠标穿过去）、不抢焦点、不进任务栏、总在最上面。show(图) 把画好的图贴上去：透明的地方 alpha = 0，画的东西都是不透明的。"""

    def __init__(self, mon, click=None):
        """click = None：盖住整块屏幕、点不到的浮层；给一个函数：点得到的小按钮（× 关掉），左键松开时调它。
        小按钮一样不抢焦点：点它的时候博易云还是前台窗口，键盘照样打在博易云里。"""
        self.mon = mon
        inst = kernel32.GetModuleHandleW(None)
        cls = "竞价浮层" if click is None else "竞价浮层按钮"

        def proc(h, m, w, p):
            if click is not None:
                if m == WM_MOUSEACTIVATE:  # 点它不激活（不把焦点从博易云抢过来）
                    return MA_NOACTIVATE
                if m == WM_LBUTTONUP:
                    click()
                    return 0
            return user32.DefWindowProcW(h, m, w, p)
        self._proc = WNDPROC(proc)  # 留着引用，别被回收
        wc = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._proc, hInstance=inst, lpszClassName=cls)
        user32.RegisterClassExW(ctypes.byref(wc))  # 同一进程第二次注册会失败，不要紧
        ex = WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST | (WS_EX_TRANSPARENT if click is None else 0)
        self.hwnd = user32.CreateWindowExW(ex, cls, cls, WS_POPUP, mon[0], mon[1], mon[2] - mon[0], mon[3] - mon[1],
                                           None, None, inst, None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.shown = False

    def show(self, img, at: tuple | None = None) -> None:
        """贴图；at = 图左上角在屏幕上的位置（不给 = 这块屏幕的左上角）。"""
        w, h = img.size
        at = at or (self.mon[0], self.mon[1])
        bmi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h, biPlanes=1, biBitCount=32)
        screen = user32.GetDC(None)
        mem = gdi32.CreateCompatibleDC(screen)
        bits = ctypes.c_void_p()
        bmp = gdi32.CreateDIBSection(screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        try:
            ctypes.memmove(bits, img.tobytes("raw", "BGRA"), w * h * 4)
            old = gdi32.SelectObject(mem, bmp)
            blend = BLENDFUNCTION(0, 0, 255, 1)  # 按每个像素自己的 alpha 叠上去
            user32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(wt.POINT(int(at[0]), int(at[1]))),
                                       ctypes.byref(wt.SIZE(w, h)), mem, ctypes.byref(wt.POINT(0, 0)), 0,
                                       ctypes.byref(blend), ULW_ALPHA)
            gdi32.SelectObject(mem, old)
        finally:
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(None, screen)
        if not self.shown:
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            self.shown = True
        self.top()

    def hide(self) -> None:
        if self.shown:
            user32.ShowWindow(self.hwnd, SW_HIDE)
            self.shown = False

    def top(self) -> None:
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def loop(self, step, every: int = 250) -> None:
        """每 every 毫秒调一次 step()，step 返回 False 就关窗口、结束。"""
        user32.SetTimer(self.hwnd, 1, every, None)
        msg = wt.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_TIMER:
                    if not step():
                        break
                else:
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.DestroyWindow(self.hwnd)


def stop_key() -> None:
    """紧急关掉：单独一个线程等快捷键，按下就把整个浮层进程结束掉（主线程卡住了也关得掉）。
    只在浮层开着的这几分钟里占用这个快捷键；被别的程序占了就写进运行日志，还能点 × 或双击桌面上的「关竞价浮层」。"""
    def wait():
        mods, vk, name = STOP_KEY
        if not user32.RegisterHotKey(None, 1, mods, vk):
            print(f"快捷键 {name} 被别的程序占了，紧急关掉改点小提示右边的 ×，或双击桌面上的「关竞价浮层」")
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                print(f"{datetime.now():%H:%M:%S} 按了 {name}，浮层关掉")
                os._exit(0)
    threading.Thread(target=wait, daemon=True).start()


def run(json_file: Path | None, test: bool = False, hold: float = 20, check: bool = True) -> None:
    """浮层：盖住博易云所在的那块屏幕，透明、点不到、不抢焦点；每 0.25 秒看一次 json 和博易云窗口，有变化再重画。"""
    dpi_aware()
    stop_key()
    raw = read_board()
    pobo = find_pobo()
    board = scaled(raw, dpi_ratio(pobo[0]) if pobo else 1.0)
    shot = None
    if test:
        if pobo is None:
            print("博易云没打开或最小化了：先把博易云打开、放在最前面，再跑 --test")
            return
        keys = {(i, j) for i, j, key, *_ in cells(pobo[1], board) if key}
        hidden = keys - visible_cells(pobo[0], pobo[1], board)
        if hidden:
            print(f"博易云被别的窗口挡住了（{len(hidden)} 格看不到）：先把博易云放到最前面，再跑 --test")
            return
        shot = grab(pobo[1])
        k = board["参数"]["倍数"]
        raw.pop("行线", None)
        lines = find_rows(shot, (0, 0, shot.width, shot.height), scaled(raw, k))
        if lines:
            raw["行线"] = [y / k for y in lines]
            board = scaled(raw, k)
            print("每排格子的上边（博易云的像素）：" + "、".join(f"{y:.0f}" for y in raw["行线"]))
        else:
            print("没量出每排格子的分界线，按看板.txt 平分")
        print(f"存了 {save_refs(shot, pobo[1], board, raw.get('行线'))} 个格子的标题样子（{REF_DIR}）")
    mon = monitor_of(pobo[0] if pobo else None)
    t0 = time.time()
    fg = user32.GetForegroundWindow()
    ov = Overlay(mon)

    def close():
        print(f"{datetime.now():%H:%M:%S} 点了 ×，浮层关掉")
        os._exit(0)
    btn = Overlay(mon, close)
    st = {"data": {}, "mtime": None, "sig": None, "ok": None, "why": "", "next_check": 0.0,
          "next_find": 0.0, "pobo": pobo, "board": board, "vis": set(), "chip": False, "ends": {}, "lefts": {}}
    if shot is not None:
        st["ends"] = text_ends(shot, (0, 0, shot.width, shot.height), board)

    def step():
        now = time.time()
        if json_file is not None:
            try:
                m = json_file.stat().st_mtime
                if m != st["mtime"]:
                    st["data"], st["mtime"], st["next_check"] = json.loads(json_file.read_text(encoding="utf-8")), m, 0
            except (OSError, ValueError):
                pass
        data = st["data"]
        until = data.get("显示到")
        if (test and now - t0 > hold) or now - t0 > LIFE or data.get("状态") == "没开盘" or (
                until and datetime.now() >= datetime.strptime(until, "%Y-%m-%d %H:%M:%S")):
            return False
        if now >= st["next_find"]:  # 每秒：博易云挪没挪、哪些格子露在外面，浮层保持在最上面
            st["next_find"] = now + 1
            st["pobo"] = find_pobo()
            if st["pobo"]:
                st["board"] = scaled(raw, dpi_ratio(st["pobo"][0]))
                vis = visible_cells(st["pobo"][0], st["pobo"][1], st["board"])
                if vis - st["vis"]:  # 有格子刚露出来（从别的窗口切回博易云）：马上比一次格子，不等 3 秒
                    st["next_check"] = 0
                st["vis"] = vis
                st["chip"] = chip_visible(st["pobo"][0], st["pobo"][1], st["board"])
            else:
                st["vis"], st["chip"] = set(), False
            ov.top()
            if btn.shown:
                btn.top()
        b = st["board"]
        if test:
            items, note, outline = sample_items(raw), {"文字": f"竞价浮层 示例：位置对不对？{hold:.0f} 秒后消失"}, True
        else:
            items, note = items_of(data)
            outline = False
        quiet = False
        try:
            if data.get("开盘"):
                gap = (datetime.now() + timedelta(seconds=float(data.get("时差") or 0))  # 时差：电脑时钟慢几秒（竞价判断.py 问标准时间算的）
                       - datetime.strptime(data["开盘"], "%Y-%m-%d %H:%M")).total_seconds()
                quiet = -QUIET[0] <= gap <= QUIET[1]
        except (ValueError, TypeError):
            pass
        if items and st["pobo"] and st["vis"] and now >= st["next_check"] and not test and not quiet:  # 有标签要贴时，每 3 秒比一次格子
            st["next_check"] = now + 3
            try:
                shot = grab(st["pobo"][1])
                dx = st["pobo"][1][0] - mon[0]  # 浮层图里的 x → 截图里的 x
                clip = {k: x - dx for k, x in st["lefts"].items()}  # 截图里有自己贴的标签，量字时躲开
                st["ends"] = text_ends(shot, (0, 0, shot.width, shot.height), b, clip)
                st["ok"], st["why"] = check_layout(shot, st["pobo"][1], b, st["vis"]) if check else (None, "")
            except Exception as e:
                st["ok"], st["why"] = None, f"比对格子出错（{type(e).__name__}），都贴了"
        show = st["vis"] if st["ok"] is None else st["vis"] & st["ok"]
        if note and st["why"] and items:
            note = note | {"文字": f"{note['文字']}；{st['why']}"}
        if not st["chip"]:  # 小提示那块被别的窗口挡住了（或博易云没开）：不画
            note = None
        rect = st["pobo"][1] if st["pobo"] else (mon[0], mon[1], mon[2], mon[3])
        sig = (rect, tuple(sorted(items.items())), tuple(sorted((note or {}).items())), tuple(sorted(show)),
               tuple(sorted(st["ends"].items())), b["参数"]["倍数"])
        if sig != st["sig"]:
            st["sig"] = sig
            img = Image.new("RGBA", (mon[2] - mon[0], mon[3] - mon[1]), (0, 0, 0, 0))
            r = (rect[0] - mon[0], rect[1] - mon[1], rect[2] - mon[0], rect[3] - mon[1])
            info = {}
            st["lefts"] = draw(img, r, b, items if st["pobo"] else {}, note, show,
                               outline=outline and bool(st["pobo"]), ends=st["ends"], info=info)
            ov.show(img)
            if "提示" in info:  # × 按钮贴在小提示右边（小提示没画就不出来，免得浮在别的窗口上）
                x0, y0, x1, y1 = info["提示"]
                side = max(12, round(y1 - y0))
                btn.show(close_button(side), (mon[0] + x1 + side * .3, mon[1] + y0))
            else:
                btn.hide()
            if test and not st.get("told"):
                st["told"] = True
                now_fg = user32.GetForegroundWindow()
                print(f"前台窗口：出来前「{title_of(fg)}」，出来后「{title_of(now_fg)}」{'（没变）' if now_fg == fg else '（变了！）'}")
        return True

    if step():
        ov.loop(step)


# ---------- 截图示意 ----------

def mock(shot: Path, json_file: Path | None = None, out: Path | None = None, screen_w: int = 2560,
         work_h: int = 1392) -> Path:
    """不开窗口：把标签画在一张博易云全屏截图上（博易云最大化；截图比屏幕小就按比例缩）。"""
    img = Image.open(shot).convert("RGB")
    scale = img.width / screen_w
    board = read_board()
    if json_file is None:
        items, note = sample_items(board), {"文字": "竞价判断 21:00：示例"}
    else:
        items, note = items_of(json.loads(Path(json_file).read_text(encoding="utf-8")))
    rect = (0, 0, img.width, round(work_h * scale))
    draw(img, rect, board, items, note, None, scale, ends=text_ends(img, rect, scaled(board, scale)))
    out = out or shot.with_name(f"{shot.stem}_浮层.png")
    img.save(out)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--json", help="竞价判断.py 写的 竞价判断.json")
    p.add_argument("--test", action="store_true", help="每格贴样例标签、存下格子的样子")
    p.add_argument("--hold", type=float, default=20, help="--test 时标签留几秒")
    p.add_argument("--mock", help="把标签画在这张截图上，不开窗口")
    p.add_argument("--out", help="--mock 存到哪")
    p.add_argument("--no-check", action="store_true", help="预览用：不跟 --test 存的样子比，格子都贴")
    a = p.parse_args()
    if a.mock:
        print(f"画好了：{mock(Path(a.mock), Path(a.json) if a.json else None, Path(a.out) if a.out else None)}")
        return
    if not a.test and not a.json:
        p.error("要么 --test，要么 --json（平时由 竞价判断.py 带起来）")
    try:
        run(Path(a.json) if a.json else None, a.test, a.hold, not a.no_check)
    except Exception as e:
        import traceback
        print(f"竞价浮层出错：{type(e).__name__} {e}\n{traceback.format_exc()}")


if __name__ == "__main__":
    main()
