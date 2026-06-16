"""Cross-platform activity collector loop.

Auto-detects the OS and loads the matching low-level collector
(collector_win on Windows, collector_mac on macOS). The sampling loop, idle
handling, app open/close diffing, and DB writes are shared across platforms.

Which signals are actually recorded is governed by config.py (per-feature
toggles via env vars or config.json). Any collector capability that the loaded
platform collector does not implement is simply skipped — the loop never
depends on an optional function existing.

Run with the platform's native Python (or the bundled app) so it can read the
live desktop session.
"""

import platform
import sys
import time

import config
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


def _call(col, name):
    """Call an optional collector function if the platform implements it,
    returning {} (or None for scalars) when it doesn't or it errors out."""
    fn = getattr(col, name, None)
    if fn is None:
        return {}
    try:
        return fn()
    except Exception as e:  # never let one metric break the loop
        print(f"[tracker] {name}() failed: {e}")
        return {}


def run() -> None:
    col = _load_collector()
    cfg = config.load()
    db.init()
    conn = db.connect()
    print(f"[tracker] platform={col.PLATFORM}, sampling every {INTERVAL_SEC}s, "
          f"idle after {IDLE_THRESHOLD_SEC}s. Ctrl+C to stop.")
    print("[tracker] logging configuration:\n" + config.describe(cfg))
    if col.NEEDS_PERMISSION_NOTE:
        print(f"[tracker] note: {col.NEEDS_PERMISSION_NOTE}")

    # Input-intensity hook (counts only) runs in its own thread; start once.
    if cfg["input_activity"] and hasattr(col, "start_input_monitor"):
        try:
            col.start_input_monitor()
            print("[tracker] input-intensity monitor started (counts only).")
        except Exception as e:
            print(f"[tracker] input monitor unavailable: {e}")
            cfg["input_activity"] = False

    prev_apps = col.windowed_apps() if cfg["app_events"] else set()
    for name in prev_apps:  # apps already open at startup count as 'start'
        db.insert_event(conn, time.time(), name, "start")

    prev_procs = {}
    if cfg["process_events"] and hasattr(col, "all_processes"):
        prev_procs = _call(col, "all_processes") or {}

    prev_net = None  # (bytes_sent, bytes_recv) for per-interval deltas

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

            extra = {}
            if cfg["window_details"]:
                extra.update(_call(col, "window_details"))
            if cfg["process_stats"]:
                extra.update(_call(col, "process_stats"))
            if cfg["input_activity"]:
                extra.update(_call(col, "input_activity"))
            if cfg["session_state"]:
                extra.update(_call(col, "session_state"))
            if cfg["power"]:
                extra.update(_call(col, "power_state"))

            net = _call(col, "network_state") if cfg["network"] else {}
            if net:
                extra["ssid"] = net.get("ssid")
            if cfg["device_usage"]:
                extra.update(_call(col, "device_usage"))

            db.insert_sample(
                conn, now, duration,
                fg["process_name"], fg["exe_path"], fg["window_title"],
                fg["is_browser"], fg["page_title"], is_idle, extra,
            )

            # Network: store per-interval byte deltas + SSID in its own table.
            if cfg["network"] and net:
                cur_net = (net.get("bytes_sent", 0), net.get("bytes_recv", 0))
                if prev_net is not None:
                    sent = max(0, cur_net[0] - prev_net[0])
                    recv = max(0, cur_net[1] - prev_net[1])
                    db.insert_net_sample(conn, now, duration,
                                         net.get("ssid"), sent, recv)
                prev_net = cur_net

            # Windowed-app open/close diff.
            if cfg["app_events"]:
                cur_apps = col.windowed_apps()
                for name in cur_apps - prev_apps:
                    db.insert_event(conn, now, name, "start")
                for name in prev_apps - cur_apps:
                    db.insert_event(conn, now, name, "stop")
                prev_apps = cur_apps

            # Full process lifecycle (incl. non-windowed) diff.
            if cfg["process_events"] and hasattr(col, "all_processes"):
                cur_procs = _call(col, "all_processes") or {}
                for pid in cur_procs.keys() - prev_procs.keys():
                    name, exe = cur_procs[pid]
                    db.insert_proc_event(conn, now, pid, name, exe, "start")
                for pid in prev_procs.keys() - cur_procs.keys():
                    name, exe = prev_procs[pid]
                    db.insert_proc_event(conn, now, pid, name, exe, "stop")
                prev_procs = cur_procs
    except KeyboardInterrupt:
        print("\n[tracker] stopped.")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
