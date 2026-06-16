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


# --- input_activity (COUNTS ONLY — no key contents) -------------------------
class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


_WH_KEYBOARD_LL = 13
_WH_MOUSE_LL = 14
_WM_KEYDOWN = 0x0100
_WM_SYSKEYDOWN = 0x0104
_WM_MOUSEMOVE = 0x0200
_WM_MOUSEWHEEL = 0x020A
_WM_MOUSEHWHEEL = 0x020E
_MOUSE_DOWN = {0x0201, 0x0204, 0x0207, 0x020B}  # L / R / M / X button down

_input_lock = threading.Lock()
_counts = {"key": 0, "click": 0, "scroll": 0, "dist": 0.0}
_last_pt = [None]
_input_started = False
# Hold references so the callbacks/hooks are not garbage-collected.
_hook_refs = []

HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)


def _kbd_proc(nCode, wParam, lParam):
    if nCode == 0 and wParam in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
        with _input_lock:
            _counts["key"] += 1  # count only; vkCode is deliberately ignored
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
