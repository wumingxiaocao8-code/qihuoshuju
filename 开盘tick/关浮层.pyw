"""紧急关掉竞价浮层：双击桌面上的「关竞价浮层」（指向本文件），博易云上的标签马上消失。

跟快捷键 Ctrl + Alt + Q 一样，是给「程序出错、标签挡住要看的东西」时用的；浮层卡住了也关得掉。
做法：找到所有叫「竞价浮层」的窗口，把它们所在的进程结束掉。没开着就什么也不做。只用 Windows 自带的功能，不用装包。
"""
import ctypes
import ctypes.wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [PROC, wt.LPARAM]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
PROCESS_TERMINATE = 0x1


def main() -> None:
    pids = set()

    def each(h, _):
        buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, buf, 64)
        if buf.value == "竞价浮层":  # 竞价浮层.py 建的窗口就叫这个名字
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            pids.add(pid.value)
        return True

    user32.EnumWindows(PROC(each), 0)
    for pid in pids:
        h = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if h:
            kernel32.TerminateProcess(h, 0)
            kernel32.CloseHandle(h)


if __name__ == "__main__":
    main()
