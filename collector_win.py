"""Windows activity primitives (Win32 via ctypes + psutil).

Exposes the collector interface used by tracker.py. The original three calls:
  idle_seconds() -> float
  foreground()   -> dict(process_name, exe_path, window_title, is_browser,
                         page_title, hwnd, pid)
  windowed_apps()-> set[str]

...plus the extended logging suite (each is independent and defensive — any
failure returns a neutral/empty result rather than raising):
  window_details()  -> dict(window_class, win_state, win_x/y/w/h, monitor)
  process_stats()   -> dict(cpu_pct, mem_mb, ppid, parent_name, cmdline)
  start_input_monitor() / input_activity()
                    -> dict(key_count, click_count, scroll_count, mouse_dist)
                       COUNTS ONLY — never which keys are pressed.
  start_keystroke_log() / keystroke_log()
                    -> [dict(process_name, window_title, text, char_count,
                       suppressed_count), ...]  OPT-IN literal typed text per
                       window; text in password/auth fields is discarded.
  set_browser_field_sensitive(bool)
                    -> tell the text logger the focused web field is (not) a
                       password/OTP field (relayed from the browser extension).
  session_state()   -> dict(locked, screensaver, session_id)
  power_state()     -> dict(on_battery, battery_pct)
  network_state()   -> dict(ssid, bytes_sent, bytes_recv)  (cumulative bytes)
  device_usage()    -> dict(camera_in_use, mic_in_use)
  all_processes()   -> dict[pid] = (name, exe)
"""

import ctypes
import ctypes.wintypes as wt
import math
import threading

import psutil

import browsers

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

PLATFORM = "Windows"
NEEDS_PERMISSION_NOTE = ""  # nothing special required on Windows

# ctypes is 32-bit by default for handle/pointer-returning calls; declaring
# restypes keeps window handles intact on 64-bit Python.
LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t


def _declare(fn, restype, argtypes):
    fn.restype = restype
    fn.argtypes = argtypes


_declare(user32.GetForegroundWindow, wt.HWND, [])
_declare(user32.GetWindowTextLengthW, ctypes.c_int, [wt.HWND])
_declare(user32.GetWindowTextW, ctypes.c_int, [wt.HWND, wt.LPWSTR, ctypes.c_int])
_declare(user32.GetClassNameW, ctypes.c_int, [wt.HWND, wt.LPWSTR, ctypes.c_int])
_declare(user32.GetWindowThreadProcessId, wt.DWORD,
         [wt.HWND, ctypes.POINTER(wt.DWORD)])
_declare(user32.IsWindowVisible, wt.BOOL, [wt.HWND])
# Handle-returning calls: declare so 64-bit handles aren't truncated to int.
_declare(user32.MonitorFromWindow, wt.HMONITOR, [wt.HWND, wt.DWORD])
_declare(user32.OpenInputDesktop, wt.HANDLE, [wt.DWORD, wt.BOOL, wt.DWORD])
_declare(kernel32.GetModuleHandleW, wt.HMODULE, [wt.LPCWSTR])
kernel32.GetCurrentProcessId.restype = wt.DWORD
kernel32.GetTickCount.restype = wt.DWORD


# --- idle -------------------------------------------------------------------
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def idle_seconds() -> float:
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return (kernel32.GetTickCount() - info.dwTime) / 1000.0


# --- window / process basics ------------------------------------------------
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
                "window_title": "", "is_browser": False, "page_title": None,
                "hwnd": 0, "pid": 0}
    title = _window_title(hwnd)
    pid = _pid_of_window(hwnd)
    name, exe = _proc_info(pid)
    is_browser = name.lower() in browsers.WINDOWS_BROWSERS
    page_title = browsers.clean_title(title) if is_browser else None
    return {"process_name": name, "exe_path": exe, "window_title": title,
            "is_browser": is_browser, "page_title": page_title,
            "hwnd": int(hwnd), "pid": pid}


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


# --- window_details: class, geometry, min/max, monitor ----------------------
class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wt.UINT), ("flags", wt.UINT), ("showCmd", wt.UINT),
                ("ptMinPosition", POINT), ("ptMaxPosition", POINT),
                ("rcNormalPosition", RECT)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wt.DWORD)]


_SW_SHOWMINIMIZED = 2
_SW_SHOWMAXIMIZED = 3
_MONITOR_DEFAULTTONEAREST = 2

# Build a stable 0-based index for each physical monitor handle once.
_monitor_index = {}


def _build_monitor_index():
    _monitor_index.clear()
    monitors = []
    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(RECT), wt.LPARAM)

    def cb(hmon, _hdc, _rect, _lp):
        monitors.append(int(hmon))
        return True

    try:
        user32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(cb), 0)
    except Exception:
        pass
    for i, h in enumerate(sorted(monitors)):
        _monitor_index[h] = i


_build_monitor_index()


def window_details() -> dict:
    out = {"window_class": None, "win_state": None,
           "win_x": None, "win_y": None, "win_w": None, "win_h": None,
           "monitor": None}
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return out

        buf = ctypes.create_unicode_buffer(256)
        if user32.GetClassNameW(hwnd, buf, 256):
            out["window_class"] = buf.value

        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(wp)
        if user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            out["win_state"] = {
                _SW_SHOWMINIMIZED: "minimized",
                _SW_SHOWMAXIMIZED: "maximized",
            }.get(wp.showCmd, "normal")

        rect = RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            out["win_x"] = rect.left
            out["win_y"] = rect.top
            out["win_w"] = rect.right - rect.left
            out["win_h"] = rect.bottom - rect.top

        hmon = user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
        if hmon:
            if int(hmon) not in _monitor_index:
                _build_monitor_index()
            out["monitor"] = _monitor_index.get(int(hmon))
    except Exception:
        pass
    return out


# --- process_stats ----------------------------------------------------------
def process_stats() -> dict:
    out = {"cpu_pct": None, "mem_mb": None, "ppid": None,
           "parent_name": None, "cmdline": None}
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return out
        pid = _pid_of_window(hwnd)
        p = psutil.Process(pid)
        # cpu_percent(None) is non-blocking and reports usage since the prior
        # call for this Process object; we keep a cache so the value is real.
        proc = _cpu_cache.get(pid)
        if proc is None or proc.pid != pid:
            proc = p
            proc.cpu_percent(None)  # prime
            _cpu_cache[pid] = proc
        out["cpu_pct"] = round(proc.cpu_percent(None), 1)
        out["mem_mb"] = round(p.memory_info().rss / (1024 * 1024), 1)
        out["ppid"] = p.ppid()
        try:
            out["parent_name"] = p.parent().name() if p.parent() else None
        except (psutil.Error, AttributeError):
            pass
        try:
            cmd = p.cmdline()
            out["cmdline"] = " ".join(cmd)[:1024] if cmd else None
        except psutil.Error:
            pass
    except (psutil.Error, ValueError, OSError):
        pass
    return out


_cpu_cache = {}


# --- input_activity + optional keystroke TEXT -------------------------------
# Two layers share the single low-level keyboard hook:
#   * input_activity : COUNTS ONLY (keys/clicks/scroll/mouse-distance).
#   * keystroke_text : OPT-IN literal typed text, attributed per active window,
#                      with text typed into authentication fields discarded.
# The text layer is dormant unless start_keystroke_log() has been called.
class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD),
                ("flags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD),
                ("hwndActive", wt.HWND), ("hwndFocus", wt.HWND),
                ("hwndCapture", wt.HWND), ("hwndMenuOwner", wt.HWND),
                ("hwndMoveSize", wt.HWND), ("hwndCaret", wt.HWND),
                ("rcCaret", RECT)]


_WH_KEYBOARD_LL = 13
_WH_MOUSE_LL = 14
_WM_KEYDOWN = 0x0100
_WM_SYSKEYDOWN = 0x0104
_WM_KEYUP = 0x0101
_WM_SYSKEYUP = 0x0105
_WM_MOUSEMOVE = 0x0200
_WM_MOUSEWHEEL = 0x020A
_WM_MOUSEHWHEEL = 0x020E
_MOUSE_DOWN = {0x0201, 0x0204, 0x0207, 0x020B}  # L / R / M / X button down

# Virtual-key codes we treat specially.
_VK_BACK = 0x08
_VK_TAB = 0x09
_VK_RETURN = 0x0D
_VK_SHIFT, _VK_CONTROL, _VK_MENU, _VK_CAPITAL = 0x10, 0x11, 0x12, 0x14
_VK_LSHIFT, _VK_RSHIFT = 0xA0, 0xA1
_VK_LCONTROL, _VK_RCONTROL = 0xA2, 0xA3
_VK_LMENU, _VK_RMENU = 0xA4, 0xA5
_MODIFIER_VKS = {_VK_SHIFT, _VK_CONTROL, _VK_MENU, _VK_CAPITAL,
                 _VK_LSHIFT, _VK_RSHIFT, _VK_LCONTROL, _VK_RCONTROL,
                 _VK_LMENU, _VK_RMENU}

_ES_PASSWORD = 0x0020   # edit-control style bit for masked input
_GWL_STYLE = -16

# Declarations needed for translation + focused-field inspection.
_declare(user32.GetGUIThreadInfo, wt.BOOL, [wt.DWORD, ctypes.c_void_p])
_declare(user32.GetWindowLongW, wt.LONG, [wt.HWND, ctypes.c_int])
_declare(user32.GetKeyboardLayout, wt.HKL, [wt.DWORD])
_declare(user32.GetKeyState, ctypes.c_short, [ctypes.c_int])
_declare(user32.ToUnicodeEx, ctypes.c_int,
         [wt.UINT, wt.UINT, ctypes.c_void_p, wt.LPWSTR, ctypes.c_int,
          wt.UINT, wt.HKL])

_input_lock = threading.Lock()
_counts = {"key": 0, "click": 0, "scroll": 0, "dist": 0.0}
_last_pt = [None]
_input_started = False
# Hold references so the callbacks/hooks are not garbage-collected.
_hook_refs = []

# --- keystroke-text state (all touched only under _input_lock) --------------
_kl_enabled = False
_kl_segments = []     # finished per-window text bursts awaiting flush
_kl_cur = None        # the burst currently accumulating
_mods = {"shift": False, "ctrl": False, "alt": False, "caps": False}
# A browser auth field is "sensitive until" this tick-count, refreshed by the
# extension via set_browser_field_sensitive(); a TTL guards against a missed
# blur leaving capture stuck on.
_browser_sensitive_deadline = [0]
_BROWSER_SENSITIVE_TTL_MS = 6000
_pid_name_cache = {}  # pid -> (name, exe, is_browser); reset if it grows large

HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)


def _kl_proc_info_cached(pid):
    info = _pid_name_cache.get(pid)
    if info is not None:
        return info
    name, exe = _proc_info(pid)
    info = (name, exe, name.lower() in browsers.WINDOWS_BROWSERS)
    if len(_pid_name_cache) > 256:
        _pid_name_cache.clear()
    _pid_name_cache[pid] = info
    return info


def _focused_is_password() -> bool:
    """True if the focused control is a masked/password edit. Best-effort: this
    only sees native Win32 controls (ES_PASSWORD); browser/Electron password
    fields are covered separately via set_browser_field_sensitive()."""
    try:
        fg = user32.GetForegroundWindow()
        if not fg:
            return False
        tid = user32.GetWindowThreadProcessId(fg, None)
        gti = GUITHREADINFO()
        gti.cbSize = ctypes.sizeof(gti)
        if not user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
            return False
        h = gti.hwndFocus or gti.hwndCaret
        if not h:
            return False
        if user32.GetWindowLongW(h, _GWL_STYLE) & _ES_PASSWORD:
            return True
        buf = ctypes.create_unicode_buffer(64)
        if user32.GetClassNameW(h, buf, 64) and "password" in buf.value.lower():
            return True
    except Exception:
        return False
    return False


def _browser_sensitive_active() -> bool:
    return kernel32.GetTickCount() < _browser_sensitive_deadline[0]


def set_browser_field_sensitive(sensitive: bool,
                                ttl_ms: int = _BROWSER_SENSITIVE_TTL_MS) -> None:
    """Signal (from the browser extension, relayed by the dashboard) that the
    focused web field is — or is no longer — a password / one-time-code input.
    While active, keystrokes in a foreground browser are not recorded as text."""
    _browser_sensitive_deadline[0] = (
        kernel32.GetTickCount() + ttl_ms if sensitive else 0)


def _update_mod(vk, is_down) -> None:
    if vk in (_VK_SHIFT, _VK_LSHIFT, _VK_RSHIFT):
        _mods["shift"] = is_down
    elif vk in (_VK_CONTROL, _VK_LCONTROL, _VK_RCONTROL):
        _mods["ctrl"] = is_down
    elif vk in (_VK_MENU, _VK_LMENU, _VK_RMENU):
        _mods["alt"] = is_down
    elif vk == _VK_CAPITAL and is_down:
        _mods["caps"] = not _mods["caps"]


def _translate(vk, scan) -> str:
    """Virtual key -> the character(s) it would produce, honouring the tracked
    Shift / Caps / AltGr state. Returns '' for non-text keys and dead keys."""
    state = (ctypes.c_ubyte * 256)()
    if _mods["shift"]:
        state[_VK_SHIFT] = 0x80
    if _mods["caps"]:
        state[_VK_CAPITAL] = 0x01
    if _mods["ctrl"] and _mods["alt"]:           # AltGr
        state[_VK_CONTROL] = 0x80
        state[_VK_MENU] = 0x80
    fg = user32.GetForegroundWindow()
    tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    hkl = user32.GetKeyboardLayout(tid)
    buf = ctypes.create_unicode_buffer(8)
    # wFlags bit 2 (0x4) = don't disturb the keyboard's dead-key state (Win10+).
    n = user32.ToUnicodeEx(vk, scan, state, buf, 8, 0x4, hkl)
    return buf.value[:n] if n > 0 else ""


def _kl_new_segment():
    fg = user32.GetForegroundWindow()
    hwnd = int(fg) if fg else 0
    pid = _pid_of_window(fg) if fg else 0
    title = _window_title(fg) if fg else ""
    if pid:
        name, exe, is_browser = _kl_proc_info_cached(pid)
    else:
        name, exe, is_browser = "idle-desktop", None, False
    return {"hwnd": hwnd, "pid": pid, "process_name": name, "exe_path": exe,
            "window_title": title, "is_browser": is_browser,
            "chars": [], "dropped": 0}


def _kl_flush_current() -> None:
    global _kl_cur
    if _kl_cur and (_kl_cur["chars"] or _kl_cur["dropped"]):
        _kl_segments.append(_kl_cur)
    _kl_cur = None


def _kl_capture(vk, scan) -> None:
    """Append one keystroke's text to the current window's burst, unless it
    belongs to an authentication field or is a command shortcut."""
    global _kl_cur
    fg = user32.GetForegroundWindow()
    hwnd = int(fg) if fg else 0
    if _kl_cur is None or _kl_cur["hwnd"] != hwnd:
        _kl_flush_current()
        _kl_cur = _kl_new_segment()
    cur = _kl_cur

    # Suppression. Fail CLOSED: if we cannot determine the field, don't record.
    try:
        suppressed = _focused_is_password() or (
            cur["is_browser"] and _browser_sensitive_active())
    except Exception:
        suppressed = True
    # A Ctrl shortcut (without AltGr) is a command, not typed text.
    if _mods["ctrl"] and not _mods["alt"]:
        suppressed = True
    if suppressed:
        cur["dropped"] += 1
        return

    if vk == _VK_BACK:
        if cur["chars"]:
            cur["chars"].pop()
    elif vk == _VK_RETURN:
        cur["chars"].append("\n")
    elif vk == _VK_TAB:
        cur["chars"].append("\t")
    else:
        ch = _translate(vk, scan)
        if ch:
            cur["chars"].extend(ch)


def _kbd_proc(nCode, wParam, lParam):
    if nCode == 0:
        is_down = wParam in (_WM_KEYDOWN, _WM_SYSKEYDOWN)
        is_up = wParam in (_WM_KEYUP, _WM_SYSKEYUP)
        if is_down or is_up:
            try:
                ks = ctypes.cast(
                    lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk, scan = ks.vkCode, ks.scanCode
            except Exception:
                return user32.CallNextHookEx(0, nCode, wParam, lParam)
            with _input_lock:
                _update_mod(vk, is_down)
                if is_down:
                    _counts["key"] += 1  # intensity count (never the key)
                    if _kl_enabled and vk not in _MODIFIER_VKS:
                        try:
                            _kl_capture(vk, scan)
                        except Exception:
                            pass
    return user32.CallNextHookEx(0, nCode, wParam, lParam)


def _mouse_proc(nCode, wParam, lParam):
    if nCode == 0:
        with _input_lock:
            if wParam in _MOUSE_DOWN:
                _counts["click"] += 1
            elif wParam in (_WM_MOUSEWHEEL, _WM_MOUSEHWHEEL):
                _counts["scroll"] += 1
            elif wParam == _WM_MOUSEMOVE:
                try:
                    ms = ctypes.cast(
                        lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    x, y = ms.pt.x, ms.pt.y
                    if _last_pt[0] is not None:
                        dx = x - _last_pt[0][0]
                        dy = y - _last_pt[0][1]
                        _counts["dist"] += math.hypot(dx, dy)
                    _last_pt[0] = (x, y)
                except Exception:
                    pass
    return user32.CallNextHookEx(0, nCode, wParam, lParam)


def _input_thread():
    _declare(user32.CallNextHookEx, LRESULT,
             [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM])
    _declare(user32.SetWindowsHookExW, wt.HHOOK,
             [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD])
    kb = HOOKPROC(_kbd_proc)
    ms = HOOKPROC(_mouse_proc)
    _hook_refs.extend((kb, ms))
    hmod = kernel32.GetModuleHandleW(None)
    user32.SetWindowsHookExW(_WH_KEYBOARD_LL, kb, hmod, 0)
    user32.SetWindowsHookExW(_WH_MOUSE_LL, ms, hmod, 0)
    msg = wt.MSG()
    # Low-level hooks deliver via this thread's message queue.
    while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def start_input_monitor() -> None:
    global _input_started
    if _input_started:
        return
    _input_started = True
    t = threading.Thread(target=_input_thread, daemon=True,
                         name="input-activity-hook")
    t.start()


def input_activity() -> dict:
    """Read and reset the per-interval input-intensity counters."""
    with _input_lock:
        out = {"key_count": _counts["key"], "click_count": _counts["click"],
               "scroll_count": _counts["scroll"],
               "mouse_dist": round(_counts["dist"], 1)}
        _counts["key"] = _counts["click"] = _counts["scroll"] = 0
        _counts["dist"] = 0.0
    return out


def start_keystroke_log() -> None:
    """Enable literal keystroke-text capture. Shares the keyboard hook with the
    intensity counter, so this also starts the hook if it isn't running yet."""
    global _kl_enabled
    start_input_monitor()
    with _input_lock:
        _mods["caps"] = bool(user32.GetKeyState(_VK_CAPITAL) & 1)
        _kl_enabled = True


def keystroke_log() -> list:
    """Drain the buffered per-window typed-text bursts since the last call.

    Returns a list of dicts: process_name, exe_path, window_title, is_browser,
    text, char_count, suppressed_count. Text from authentication fields is never
    present — those keystrokes are tallied in `suppressed_count` only."""
    if not _kl_enabled:
        return []
    with _input_lock:
        _kl_flush_current()
        segs = _kl_segments[:]
        _kl_segments.clear()
    out = []
    for s in segs:
        text = "".join(s["chars"])
        out.append({"process_name": s["process_name"],
                    "exe_path": s["exe_path"],
                    "window_title": s["window_title"],
                    "is_browser": s["is_browser"],
                    "text": text, "char_count": len(text),
                    "suppressed_count": s["dropped"]})
    return out


# --- session_state: locked / screensaver / session id -----------------------
_SPI_GETSCREENSAVERRUNNING = 0x0072
_MAXIMUM_ALLOWED = 0x02000000
_UOI_NAME = 2


def session_state() -> dict:
    out = {"locked": None, "screensaver": None, "session_id": None}
    try:
        running = wt.BOOL()
        if user32.SystemParametersInfoW(_SPI_GETSCREENSAVERRUNNING, 0,
                                        ctypes.byref(running), 0):
            out["screensaver"] = 1 if running.value else 0
    except Exception:
        pass
    try:
        # When the workstation is locked, the input desktop is the secure
        # "Winlogon" desktop (or OpenInputDesktop fails outright).
        hdesk = user32.OpenInputDesktop(0, False, _MAXIMUM_ALLOWED)
        if not hdesk:
            out["locked"] = 1
        else:
            needed = wt.DWORD()
            buf = ctypes.create_unicode_buffer(256)
            user32.GetUserObjectInformationW(
                hdesk, _UOI_NAME, buf, 256, ctypes.byref(needed))
            out["locked"] = 0 if buf.value.lower() == "default" else 1
            user32.CloseDesktop(hdesk)
    except Exception:
        pass
    try:
        sid = wt.DWORD()
        if kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(),
                                         ctypes.byref(sid)):
            out["session_id"] = sid.value
    except Exception:
        pass
    return out


# --- power_state ------------------------------------------------------------
def power_state() -> dict:
    out = {"on_battery": None, "battery_pct": None}
    try:
        batt = psutil.sensors_battery()
        if batt is not None:
            out["on_battery"] = 0 if batt.power_plugged else 1
            out["battery_pct"] = round(batt.percent, 1)
    except Exception:
        pass
    return out


# --- network_state: Wi-Fi SSID + cumulative byte counters -------------------
class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class DOT11_SSID(ctypes.Structure):
    _fields_ = [("uSSIDLength", ctypes.c_ulong),
                ("ucSSID", ctypes.c_ubyte * 32)]


class WLAN_ASSOCIATION_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("dot11Ssid", DOT11_SSID), ("dot11BssType", ctypes.c_int),
                ("dot11Bssid", ctypes.c_ubyte * 6),
                ("dot11PhyType", ctypes.c_int), ("uDot11PhyIndex", ctypes.c_ulong),
                ("wlanSignalQuality", ctypes.c_ulong),
                ("ulRxRate", ctypes.c_ulong), ("ulTxRate", ctypes.c_ulong)]


class WLAN_CONNECTION_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("isState", ctypes.c_int), ("wlanConnectionMode", ctypes.c_int),
                ("strProfileName", wt.WCHAR * 256),
                ("wlanAssociationAttributes", WLAN_ASSOCIATION_ATTRIBUTES),
                ("wlanSecurityAttributes", ctypes.c_byte * 16)]


class WLAN_INTERFACE_INFO(ctypes.Structure):
    _fields_ = [("InterfaceGuid", GUID),
                ("strInterfaceDescription", wt.WCHAR * 256),
                ("isState", ctypes.c_int)]


class WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
    _fields_ = [("dwNumberOfItems", wt.DWORD), ("dwIndex", wt.DWORD),
                ("InterfaceInfo", WLAN_INTERFACE_INFO * 1)]


_WLAN_INTF_OPCODE_CURRENT_CONNECTION = 7


def _wifi_ssid():
    try:
        wlanapi = ctypes.windll.wlanapi
    except Exception:
        return None
    handle = wt.HANDLE()
    negotiated = wt.DWORD()
    if wlanapi.WlanOpenHandle(2, None, ctypes.byref(negotiated),
                              ctypes.byref(handle)) != 0:
        return None
    ssid = None
    try:
        plist = ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)()
        if wlanapi.WlanEnumInterfaces(handle, None, ctypes.byref(plist)) != 0:
            return None
        try:
            ilist = plist.contents
            for i in range(ilist.dwNumberOfItems):
                info = ilist.InterfaceInfo[i]
                if info.isState != 1:   # 1 = wlan_interface_state_connected
                    continue
                data_size = wt.DWORD()
                pdata = ctypes.c_void_p()
                if wlanapi.WlanQueryInterface(
                        handle, ctypes.byref(info.InterfaceGuid),
                        _WLAN_INTF_OPCODE_CURRENT_CONNECTION, None,
                        ctypes.byref(data_size), ctypes.byref(pdata), None) != 0:
                    continue
                try:
                    attrs = ctypes.cast(
                        pdata, ctypes.POINTER(WLAN_CONNECTION_ATTRIBUTES)).contents
                    dot = attrs.wlanAssociationAttributes.dot11Ssid
                    if dot.uSSIDLength:
                        ssid = bytes(dot.ucSSID[:dot.uSSIDLength]).decode(
                            "utf-8", "replace")
                        break
                finally:
                    wlanapi.WlanFreeMemory(pdata)
        finally:
            wlanapi.WlanFreeMemory(plist)
    finally:
        wlanapi.WlanCloseHandle(handle, None)
    return ssid


def network_state() -> dict:
    out = {"ssid": None, "bytes_sent": 0, "bytes_recv": 0}
    try:
        out["ssid"] = _wifi_ssid()
    except Exception:
        pass
    try:
        io = psutil.net_io_counters()
        out["bytes_sent"] = io.bytes_sent
        out["bytes_recv"] = io.bytes_recv
    except Exception:
        pass
    return out


# --- device_usage: camera / microphone --------------------------------------
import winreg  # noqa: E402  (Windows-only, imported lazily near use)

_CONSENT_BASE = (r"Software\Microsoft\Windows\CurrentVersion"
                 r"\CapabilityAccessManager\ConsentStore")


def _device_in_use(kind: str) -> bool:
    """A capability is in use when some app's LastUsedTimeStop is 0."""
    path = f"{_CONSENT_BASE}\\{kind}"
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        return False
    try:
        return _scan_consent(root)
    finally:
        winreg.CloseKey(root)


def _scan_consent(key) -> bool:
    i = 0
    while True:
        try:
            sub = winreg.EnumKey(key, i)
        except OSError:
            break
        i += 1
        try:
            with winreg.OpenKey(key, sub) as sk:
                if sub == "NonPackaged":
                    if _scan_consent(sk):
                        return True
                    continue
                try:
                    stop, _ = winreg.QueryValueEx(sk, "LastUsedTimeStop")
                    if stop == 0:
                        return True
                except OSError:
                    pass
        except OSError:
            continue
    return False


def device_usage() -> dict:
    out = {"camera_in_use": None, "mic_in_use": None}
    try:
        out["camera_in_use"] = 1 if _device_in_use("webcam") else 0
    except Exception:
        pass
    try:
        out["mic_in_use"] = 1 if _device_in_use("microphone") else 0
    except Exception:
        pass
    return out


# --- all_processes: full lifecycle (incl. non-windowed) ---------------------
def all_processes() -> dict:
    """Map pid -> (name, exe) for every running process."""
    procs = {}
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            info = p.info
            procs[info["pid"]] = (info.get("name") or "unknown",
                                  info.get("exe"))
        except (psutil.Error, KeyError):
            continue
    return procs
