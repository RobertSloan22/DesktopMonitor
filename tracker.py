"""Activity collector. MUST run under Windows Python (python.exe), not WSL
Python, because it reads the live Windows desktop session via user32.

Every `interval` seconds it records:
  - the focused window's process + title  -> samples table
  - whether you've been idle (no input)   -> samples.is_idle
  - browser page titles                    -> samples.page_title
  - which windowed apps opened / closed    -> app_events table
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time

import db

try:
    import psutil
except ImportError:
    sys.exit("Missing dependency. Run:  pip install psutil")

# --- config -----------------------------------------------------------------
INTERVAL_SEC = 5          # how often to sample the focused window
IDLE_THRESHOLD_SEC = 60   # no keyboard/mouse for this long => "idle/away"

BROWSERS = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
    "opera.exe", "vivaldi.exe", "iexplore.exe", "arc.exe",
}
# Browser window-title suffixes to strip so we keep just the page title.
BROWSER_SUFFIXES = [
    " - Google Chrome", " - Microsoft​ Edge", " - Microsoft Edge",
    " — Mozilla Firefox", " - Mozilla Firefox", " - Brave",
    " - Opera", " - Vivaldi", " - Internet Explorer",
]

# --- win32 plumbing ---------------------------------------------------------
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input, session-wide."""
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    millis = kernel32.GetTickCount() - info.dwTime
    return millis / 1000.0


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
    """(process_name, exe_path) for a pid, tolerant of access errors."""
    try:
        p = psutil.Process(pid)
        return p.name(), (p.exe() if _safe_exe(p) else None)
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return "unknown", None


def _safe_exe(p):
    try:
        return p.exe()
    except (psutil.AccessDenied, psutil.NoSuchProcess, FileNotFoundError, OSError):
        return None


def foreground() -> dict:
    """Describe the currently focused window."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return {"process_name": "idle-desktop", "exe_path": None,
                "window_title": "", "is_browser": False, "page_title": None}
    title = _window_title(hwnd)
    pid = _pid_of_window(hwnd)
    name, exe = _proc_info(pid)
    is_browser = name.lower() in BROWSERS
    page_title = _clean_browser_title(title) if is_browser else None
    return {"process_name": name, "exe_path": exe, "window_title": title,
            "is_browser": is_browser, "page_title": page_title}


def _clean_browser_title(title: str) -> str:
    t = title
    for suffix in BROWSER_SUFFIXES:
        if t.endswith(suffix):
            t = t[: -len(suffix)]
            break
    # Drop leading unread-count badge like "(3) "
    if t.startswith("(") and ")" in t[:6]:
        t = t[t.index(")") + 1:].lstrip()
    return t.strip() or "(new tab / blank)"


# Enumerate top-level visible windows to know which *apps* are open.
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def windowed_apps() -> set:
    """Set of process names that currently own a visible, titled window."""
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


# --- main loop --------------------------------------------------------------
def run() -> None:
    db.init()
    conn = db.connect()
    print(f"[tracker] sampling every {INTERVAL_SEC}s, "
          f"idle after {IDLE_THRESHOLD_SEC}s. Ctrl+C to stop.")

    prev_apps = windowed_apps()
    for name in prev_apps:  # treat apps already open at startup as 'start'
        db.insert_event(conn, time.time(), name, "start")

    last = time.time()
    try:
        while True:
            time.sleep(INTERVAL_SEC)
            now = time.time()
            delta = now - last
            last = now
            # Clamp so machine sleep / hibernation isn't counted as activity.
            duration = min(delta, INTERVAL_SEC * 3)

            is_idle = idle_seconds() >= IDLE_THRESHOLD_SEC
            fg = foreground()
            db.insert_sample(
                conn, now, duration,
                fg["process_name"], fg["exe_path"], fg["window_title"],
                fg["is_browser"], fg["page_title"], is_idle,
            )

            cur_apps = windowed_apps()
            for name in cur_apps - prev_apps:
                db.insert_event(conn, now, name, "start")
            for name in prev_apps - cur_apps:
                db.insert_event(conn, now, name, "stop")
            prev_apps = cur_apps
    except KeyboardInterrupt:
        print("\n[tracker] stopped.")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
