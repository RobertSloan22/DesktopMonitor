"""macOS activity primitives (PyObjC: AppKit + Quartz).

Exposes the same collector interface as collector_win.py:
  idle_seconds() -> float
  foreground()   -> dict(process_name, exe_path, window_title, is_browser, page_title)
  windowed_apps()-> set[str]

Permissions:
  - App name, idle time, app open/close  -> no permission required
  - Window TITLES (incl. browser page titles without the extension)
        -> require "Screen Recording" permission for this app/Terminal in
           System Settings > Privacy & Security. Without it, titles are blank
           but everything else still works.
"""

from AppKit import NSWorkspace
from Quartz import (
    CGEventSourceSecondsSinceLastEventType,
    CGWindowListCopyWindowInfo,
    kCGEventSourceStateHIDSystemState,
    kCGNullWindowID,
    kCGWindowListExcludeDesktopElements,
    kCGWindowListOptionOnScreenOnly,
)

import browsers

PLATFORM = "macOS"
NEEDS_PERMISSION_NOTE = (
    "For window/browser page titles, grant this app (or Terminal) "
    "Screen Recording permission in System Settings > Privacy & Security."
)

# kCGAnyInputEventType isn't always exported by name; its value is ~0.
_ANY_INPUT = 0xFFFFFFFF


def idle_seconds() -> float:
    return float(CGEventSourceSecondsSinceLastEventType(
        kCGEventSourceStateHIDSystemState, _ANY_INPUT))


def _on_screen_windows():
    opts = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
    return CGWindowListCopyWindowInfo(opts, kCGNullWindowID) or []


def _frontmost_app():
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None, None, None
    name = app.localizedName()
    pid = app.processIdentifier()
    exe = None
    try:
        url = app.executableURL()
        exe = url.path() if url is not None else None
    except Exception:
        exe = None
    return name, pid, exe


def foreground() -> dict:
    name, pid, exe = _frontmost_app()
    if not name:
        return {"process_name": "idle-desktop", "exe_path": None,
                "window_title": "", "is_browser": False, "page_title": None}

    # Find the frontmost on-screen window owned by that app (layer 0 = normal).
    # The window list is front-to-back, so the first match is the active one.
    title = ""
    for w in _on_screen_windows():
        if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer", 1) == 0:
            title = w.get("kCGWindowName") or ""   # empty without Screen Recording
            break

    is_browser = name in browsers.MAC_BROWSERS
    page_title = browsers.clean_title(title) if is_browser else None
    return {"process_name": name, "exe_path": exe, "window_title": title,
            "is_browser": is_browser, "page_title": page_title}


def windowed_apps() -> set:
    """Apps that currently own a normal on-screen window."""
    found = set()
    for w in _on_screen_windows():
        if w.get("kCGWindowLayer", 1) != 0:
            continue
        owner = w.get("kCGWindowOwnerName")
        if owner:
            found.add(owner)
    return found
