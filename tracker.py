"""Cross-platform activity collector loop.

Auto-detects the OS and loads the matching low-level collector
(collector_win on Windows, collector_mac on macOS). The sampling loop, idle
handling, app open/close diffing, and DB writes are shared across platforms.

Run with the platform's native Python (or the bundled app) so it can read the
live desktop session.
"""

import platform
import sys
import time

import db

# --- config -----------------------------------------------------------------
INTERVAL_SEC = 5          # how often to sample the focused window
IDLE_THRESHOLD_SEC = 60   # no keyboard/mouse for this long => "idle/away"


def _load_collector():
    system = platform.system()
    if system == "Windows":
        import collector_win as col
        return col
    if system == "Darwin":
        try:
            import collector_mac as col
        except ImportError as e:
            sys.exit("macOS collector needs PyObjC. Install with:\n"
                     "  pip install pyobjc-framework-Cocoa pyobjc-framework-Quartz\n"
                     f"(import error: {e})")
        return col
    sys.exit(f"Unsupported platform: {system}. Windows and macOS are supported.")


def run() -> None:
    col = _load_collector()
    db.init()
    conn = db.connect()
    print(f"[tracker] platform={col.PLATFORM}, sampling every {INTERVAL_SEC}s, "
          f"idle after {IDLE_THRESHOLD_SEC}s. Ctrl+C to stop.")
    if col.NEEDS_PERMISSION_NOTE:
        print(f"[tracker] note: {col.NEEDS_PERMISSION_NOTE}")

    prev_apps = col.windowed_apps()
    for name in prev_apps:  # apps already open at startup count as 'start'
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

            is_idle = col.idle_seconds() >= IDLE_THRESHOLD_SEC
            fg = col.foreground()
            db.insert_sample(
                conn, now, duration,
                fg["process_name"], fg["exe_path"], fg["window_title"],
                fg["is_browser"], fg["page_title"], is_idle,
            )

            cur_apps = col.windowed_apps()
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
