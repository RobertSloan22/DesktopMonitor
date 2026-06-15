"""Single entry point.

  ActivityMonitor.exe            -> tracker + dashboard + tray icon (default)
  python monitor.py             -> same, from source
  python monitor.py tracker     -> only collect activity
  python monitor.py dashboard   -> only serve the web dashboard
  python monitor.py both        -> tracker + dashboard, console only (no tray)

Run with WINDOWS python / the bundled exe so the tracker can see your real
desktop. The dashboard lives at http://localhost:8777
"""

import sys
import threading
import webbrowser

import dashboard
import db


def _start_tracker_thread():
    # Import here so the dashboard-only mode works on non-Windows machines too.
    import tracker
    t = threading.Thread(target=tracker.run, daemon=True)
    t.start()
    return t


def run_both(use_tray: bool):
    db.init()
    _start_tracker_thread()
    server = dashboard.make_server()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://localhost:{dashboard.PORT}"
    print(f"[monitor] running. Dashboard: {dashboard.urls_banner()}")

    if use_tray:
        import tray
        if tray.available():
            try:
                webbrowser.open(url)
            except Exception:
                pass
            tray.run(lambda: webbrowser.open(url), server.shutdown)
            return
        print("[monitor] tray unavailable (install pystray+pillow); "
              "running in console. Ctrl+C to stop.")

    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        threading.Event().wait()  # block until Ctrl+C
    except KeyboardInterrupt:
        print("\n[monitor] stopping.")
        server.shutdown()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "tray"
    if mode == "tracker":
        import tracker
        tracker.run()
    elif mode == "dashboard":
        dashboard.run()
    elif mode == "both":
        run_both(use_tray=False)
    elif mode == "tray":
        run_both(use_tray=True)
    else:
        sys.exit(f"Unknown mode '{mode}'. Use: tray | both | tracker | dashboard")


if __name__ == "__main__":
    main()
