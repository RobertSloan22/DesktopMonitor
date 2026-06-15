"""SQLite storage layer for the activity monitor.

Tables:
  - samples:      one row per poll interval describing the focused window
  - app_events:   a row whenever a windowed app opens or closes
  - web_samples:  one row per browser-extension heartbeat (real URL + domain)

Timestamps are stored as epoch seconds (float, UTC) plus a `day` text column
holding the machine-local calendar date (YYYY-MM-DD) so grouping a day's
activity is a trivial, timezone-correct query.

The database lives in a per-user, writable app-data directory so it works the
same whether you run from source or from the bundled ActivityMonitor.exe.
"""

import os
import sqlite3
import sys
from datetime import datetime


def app_data_dir() -> str:
    """A stable, writable per-user directory for the database."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "ActivityMonitor")
    elif sys.platform == "darwin":
        d = os.path.join(os.path.expanduser("~"),
                         "Library", "Application Support", "ActivityMonitor")
    else:
        d = os.path.join(os.path.expanduser("~"), ".activity-monitor")
    os.makedirs(d, exist_ok=True)
    return d


DB_PATH = os.path.join(app_data_dir(), "activity.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL    NOT NULL,
    day          TEXT    NOT NULL,
    duration_sec REAL    NOT NULL,
    process_name TEXT    NOT NULL,
    exe_path     TEXT,
    window_title TEXT,
    is_browser   INTEGER NOT NULL DEFAULT 0,
    page_title   TEXT,
    is_idle      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_samples_day ON samples(day);

CREATE TABLE IF NOT EXISTS app_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL    NOT NULL,
    day          TEXT    NOT NULL,
    process_name TEXT    NOT NULL,
    event        TEXT    NOT NULL          -- 'start' or 'stop'
);
CREATE INDEX IF NOT EXISTS idx_events_day ON app_events(day);

CREATE TABLE IF NOT EXISTS web_samples (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL    NOT NULL,
    day          TEXT    NOT NULL,
    duration_sec REAL    NOT NULL,
    url          TEXT    NOT NULL,
    domain       TEXT    NOT NULL,
    title        TEXT
);
CREATE INDEX IF NOT EXISTS idx_web_day ON web_samples(day);
"""


def local_day(ts: float) -> str:
    """Calendar date (local time) for an epoch timestamp."""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def connect() -> sqlite3.Connection:
    """Open a connection. Each thread should call this for its own handle."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def insert_sample(conn, ts, duration_sec, process_name, exe_path,
                  window_title, is_browser, page_title, is_idle) -> None:
    conn.execute(
        """INSERT INTO samples
           (ts, day, duration_sec, process_name, exe_path,
            window_title, is_browser, page_title, is_idle)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (ts, local_day(ts), duration_sec, process_name, exe_path,
         window_title, 1 if is_browser else 0, page_title, 1 if is_idle else 0),
    )
    conn.commit()


def insert_event(conn, ts, process_name, event) -> None:
    conn.execute(
        "INSERT INTO app_events (ts, day, process_name, event) VALUES (?,?,?,?)",
        (ts, local_day(ts), process_name, event),
    )
    conn.commit()


def insert_web_sample(conn, ts, duration_sec, url, domain, title) -> None:
    conn.execute(
        """INSERT INTO web_samples (ts, day, duration_sec, url, domain, title)
           VALUES (?,?,?,?,?,?)""",
        (ts, local_day(ts), duration_sec, url, domain, title),
    )
    conn.commit()


if __name__ == "__main__":
    init()
    print(f"Initialized database at {DB_PATH}")
