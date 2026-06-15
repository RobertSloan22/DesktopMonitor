"""Windows activity primitives (user32 via ctypes + psutil).

Exposes the collector interface used by tracker.py:
  idle_seconds() -> float
  foreground()   -> dict(process_name, exe_path, window_title, is_browser, page_title)
  windowed_apps()-> set[str]
"""

import ctypes
import ctypes.wintypes as wt

import psutil

import browsers

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

PLATFORM = "Windows"
NEEDS_PERMISSION_NOTE = ""  # nothing special required on Windows


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def idle_seconds() -> float:
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return (kernel32.GetTickCount() - info.dwTime) / 1000.0


def _window_title(hwnd) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _pid_of_window(hwnd) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _proc_info(pid):
    try:
        p = psutil.Process(pid)
        try:
            exe = p.exe()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            exe = None
        return p.name(), exe
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return "unknown", None


def foreground() -> dict:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return {"process_name": "idle-desktop", "exe_path": None,
                "window_title": "", "is_browser": False, "page_title": None}
    title = _window_title(hwnd)
    name, exe = _proc_info(_pid_of_window(hwnd))
    is_browser = name.lower() in browsers.WINDOWS_BROWSERS
    page_title = browsers.clean_title(title) if is_browser else None
    return {"process_name": name, "exe_path": exe, "window_title": title,
            "is_browser": is_browser, "page_title": page_title}


WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def windowed_apps() -> set:
    found = set()

    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if not _window_title(hwnd):
            return True
        name, _ = _proc_info(_pid_of_window(hwnd))
        if name and name != "unknown":
            found.add(name)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found
