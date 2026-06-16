"""Local web dashboard + ingestion API. Standard library only.

  GET  /                 -> the dashboard UI
  GET  /api/days         -> list of recorded days
  GET  /api/day?date=... -> full report for a day
  POST /api/browser      -> browser extension posts the active tab here

Open http://localhost:8777 once it's running.
"""

import json
import os
import socket
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import db

PORT = int(os.environ.get("ACTIVITY_MONITOR_PORT", "8777"))

# Bind address. Defaults to localhost-only (private). Set
# ACTIVITY_MONITOR_HOST=0.0.0.0 to expose the dashboard to your local network.
BIND_HOST = os.environ.get("ACTIVITY_MONITOR_HOST", "127.0.0.1")


def lan_ip() -> str:
    """Best-effort primary LAN IP of this machine (no traffic actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def resource_dir() -> str:
    """Where bundled assets live (handles the PyInstaller one-file bundle)."""
    if getattr(sys, "frozen", False):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


STATIC_DIR = os.path.join(resource_dir(), "static")


# --- helpers ----------------------------------------------------------------
def fmt_secs(s):
    s = int(s)
    h, rem = divmod(s, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def fmt_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        return host[4:] if host.startswith("www.") else host
    except ValueError:
        return ""


# --- queries ----------------------------------------------------------------
def list_days(conn):
    rows = conn.execute(
        """SELECT day FROM (
               SELECT DISTINCT day FROM samples
               UNION SELECT DISTINCT day FROM web_samples
           ) ORDER BY day DESC"""
    ).fetchall()
    return [r["day"] for r in rows]


def day_report(conn, day):
    totals = conn.execute(
        """SELECT
             COALESCE(SUM(CASE WHEN is_idle=0 THEN duration_sec END),0) AS active,
             COALESCE(SUM(CASE WHEN is_idle=1 THEN duration_sec END),0) AS idle
           FROM samples WHERE day=?""",
        (day,),
    ).fetchone()

    apps = conn.execute(
        """SELECT process_name AS name, SUM(duration_sec) AS sec
           FROM samples WHERE day=? AND is_idle=0
           GROUP BY process_name ORDER BY sec DESC LIMIT 25""",
        (day,),
    ).fetchall()

    pages = conn.execute(
        """SELECT page_title AS title, SUM(duration_sec) AS sec
           FROM samples
           WHERE day=? AND is_browser=1 AND is_idle=0
                 AND page_title IS NOT NULL AND page_title<>''
           GROUP BY page_title ORDER BY sec DESC LIMIT 20""",
        (day,),
    ).fetchall()

    # Real URLs / domains from the browser extension
    domains = conn.execute(
        """SELECT domain, SUM(duration_sec) AS sec
           FROM web_samples WHERE day=? AND domain<>''
           GROUP BY domain ORDER BY sec DESC LIMIT 20""",
        (day,),
    ).fetchall()
    urls = conn.execute(
        """SELECT url, domain, MAX(title) AS title, SUM(duration_sec) AS sec
           FROM web_samples WHERE day=?
           GROUP BY url ORDER BY sec DESC LIMIT 25""",
        (day,),
    ).fetchall()

    events = conn.execute(
        """SELECT ts, process_name AS name, event
           FROM app_events WHERE day=? ORDER BY ts DESC LIMIT 200""",
        (day,),
    ).fetchall()

    raw = conn.execute(
        """SELECT ts, duration_sec, process_name, is_idle
           FROM samples WHERE day=? ORDER BY ts ASC""",
        (day,),
    ).fetchall()
    segments = []
    for r in raw:
        label = "(idle)" if r["is_idle"] else r["process_name"]
        if segments and segments[-1]["label"] == label and \
                r["ts"] - segments[-1]["end"] < 30:
            segments[-1]["end"] = r["ts"] + r["duration_sec"]
        else:
            segments.append({"label": label, "start": r["ts"],
                             "end": r["ts"] + r["duration_sec"],
                             "idle": bool(r["is_idle"])})

    # --- extended logging suite aggregates (all columns are nullable) -------
    inp = conn.execute(
        """SELECT COALESCE(SUM(key_count),0) AS keys,
                  COALESCE(SUM(click_count),0) AS clicks,
                  COALESCE(SUM(scroll_count),0) AS scrolls,
                  COALESCE(SUM(mouse_dist),0) AS dist
           FROM samples WHERE day=?""", (day,)).fetchone()

    powr = conn.execute(
        """SELECT COALESCE(SUM(CASE WHEN on_battery=1 THEN duration_sec END),0) AS batt,
                  COALESCE(SUM(CASE WHEN on_battery=0 THEN duration_sec END),0) AS ac,
                  MAX(battery_pct) AS pct_max, MIN(battery_pct) AS pct_min
           FROM samples WHERE day=?""", (day,)).fetchone()

    sess = conn.execute(
        """SELECT COALESCE(SUM(CASE WHEN locked=1 THEN duration_sec END),0) AS locked,
                  COALESCE(SUM(CASE WHEN screensaver=1 THEN duration_sec END),0) AS saver
           FROM samples WHERE day=?""", (day,)).fetchone()

    dev = conn.execute(
        """SELECT COALESCE(SUM(CASE WHEN camera_in_use=1 THEN duration_sec END),0) AS cam,
                  COALESCE(SUM(CASE WHEN mic_in_use=1 THEN duration_sec END),0) AS mic
           FROM samples WHERE day=?""", (day,)).fetchone()

    monitors = conn.execute(
        """SELECT monitor, SUM(duration_sec) AS sec
           FROM samples WHERE day=? AND monitor IS NOT NULL AND is_idle=0
           GROUP BY monitor ORDER BY sec DESC""", (day,)).fetchall()

    net = conn.execute(
        """SELECT COALESCE(SUM(bytes_sent),0) AS sent,
                  COALESCE(SUM(bytes_recv),0) AS recv
           FROM net_samples WHERE day=?""", (day,)).fetchone()
    ssids = conn.execute(
        """SELECT ssid, SUM(duration_sec) AS sec
           FROM samples WHERE day=? AND ssid IS NOT NULL AND ssid<>''
           GROUP BY ssid ORDER BY sec DESC LIMIT 10""", (day,)).fetchall()

    proc_events = conn.execute(
        """SELECT ts, process_name AS name, event
           FROM proc_events WHERE day=? ORDER BY ts DESC LIMIT 200""",
        (day,)).fetchall()

    return {
        "day": day,
        "active_sec": totals["active"],
        "idle_sec": totals["idle"],
        "active_human": fmt_secs(totals["active"]),
        "idle_human": fmt_secs(totals["idle"]),
        "input": {"keys": int(inp["keys"]), "clicks": int(inp["clicks"]),
                  "scrolls": int(inp["scrolls"]),
                  "dist_px": int(inp["dist"])},
        "power": {"battery_human": fmt_secs(powr["batt"]),
                  "ac_human": fmt_secs(powr["ac"]),
                  "battery_sec": powr["batt"], "ac_sec": powr["ac"],
                  "pct_min": powr["pct_min"], "pct_max": powr["pct_max"]},
        "session": {"locked_human": fmt_secs(sess["locked"]),
                    "screensaver_human": fmt_secs(sess["saver"]),
                    "locked_sec": sess["locked"]},
        "devices": {"camera_human": fmt_secs(dev["cam"]),
                    "mic_human": fmt_secs(dev["mic"]),
                    "camera_sec": dev["cam"], "mic_sec": dev["mic"]},
        "monitors": [{"monitor": m["monitor"], "sec": m["sec"],
                      "human": fmt_secs(m["sec"])} for m in monitors],
        "network": {"sent_human": fmt_bytes(net["sent"]),
                    "recv_human": fmt_bytes(net["recv"]),
                    "sent": net["sent"], "recv": net["recv"],
                    "ssids": [{"ssid": s["ssid"], "human": fmt_secs(s["sec"])}
                              for s in ssids]},
        "proc_events": [{"ts": e["ts"], "name": e["name"], "event": e["event"]}
                        for e in proc_events],
        "apps": [{"name": a["name"], "sec": a["sec"],
                  "human": fmt_secs(a["sec"])} for a in apps],
        "pages": [{"title": p["title"], "sec": p["sec"],
                   "human": fmt_secs(p["sec"])} for p in pages],
        "domains": [{"domain": d["domain"], "sec": d["sec"],
                     "human": fmt_secs(d["sec"])} for d in domains],
        "urls": [{"url": u["url"], "domain": u["domain"], "title": u["title"],
                  "sec": u["sec"], "human": fmt_secs(u["sec"])} for u in urls],
        "events": [{"ts": e["ts"], "name": e["name"], "event": e["event"]}
                   for e in events],
        "segments": segments,
    }


# --- http -------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # quiet

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        conn = db.connect()
        try:
            if path in ("/", "/index.html"):
                return self._serve_static("index.html", "text/html")
            if path == "/api/days":
                return self._send(200, json.dumps(list_days(conn)))
            if path == "/api/day":
                qs = parse_qs(parsed.query)
                days = list_days(conn)
                day = qs.get("date", [days[0] if days else ""])[0]
                if not day:
                    return self._send(200, json.dumps({"empty": True}))
                return self._send(200, json.dumps(day_report(conn, day)))
            return self._send(404, json.dumps({"error": "not found"}))
        except sqlite3.OperationalError:
            return self._send(200, json.dumps({"empty": True}))
        finally:
            conn.close()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/browser":
            return self._send(404, json.dumps({"error": "not found"}))
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, json.dumps({"error": "bad json"}))

        items = payload if isinstance(payload, list) else [payload]
        conn = db.connect()
        try:
            now = time.time()
            n = 0
            for it in items:
                url = (it.get("url") or "").strip()
                if not url.startswith(("http://", "https://")):
                    continue
                dur = float(it.get("interval", 30))
                db.insert_web_sample(conn, now, dur, url,
                                     domain_of(url), it.get("title") or "")
                n += 1
            return self._send(200, json.dumps({"ok": True, "stored": n}))
        finally:
            conn.close()

    def _serve_static(self, name, ctype):
        try:
            with open(os.path.join(STATIC_DIR, name), "rb") as f:
                self._send(200, f.read(), ctype)
        except FileNotFoundError:
            self._send(404, "missing static file", "text/plain")


def make_server() -> ThreadingHTTPServer:
    db.init()
    return ThreadingHTTPServer((BIND_HOST, PORT), Handler)


def urls_banner() -> str:
    """Human-readable line(s) describing where the dashboard is reachable."""
    if BIND_HOST in ("0.0.0.0", "::"):
        return (f"on THIS computer:  http://localhost:{PORT}\n"
                f"            [dashboard] on the local network: "
                f"http://{lan_ip()}:{PORT}  "
                f"(visible to other devices on your LAN)")
    return f"http://localhost:{PORT}  (this computer only)"


def run():
    server = make_server()
    print(f"[dashboard] {urls_banner()}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] stopped.")


if __name__ == "__main__":
    run()
