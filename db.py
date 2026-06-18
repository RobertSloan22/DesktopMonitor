"""SQLite storage layer for the activity monitor.

Tables:
  - samples:      one row per poll interval describing the focused window plus
                  the machine state during that interval (geometry, process
                  stats, input-intensity counts, lock/power/network/device)
  - app_events:   a row whenever a windowed app opens or closes
  - web_samples:  one row per browser-extension heartbeat (real URL + domain)
  - net_samples:  per-interval network counters + active Wi-Fi SSID
  - proc_events:  start/stop of ALL processes (incl. non-windowed) when enabled
  - key_events:   literal typed text per active window, ONLY when the optional
                  `keystroke_text` feature is enabled. Text typed into password
                  / authentication fields is never stored (counted separately as
                  `suppressed_count`).

New optional columns on `samples` are added by an idempotent migration so an
existing database from an older build keeps working and simply gains the new
fields (NULL for historical rows).

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

CREATE TABLE IF NOT EXISTS net_samples (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL    NOT NULL,
    day          TEXT    NOT NULL,
    duration_sec REAL    NOT NULL,
    ssid         TEXT,
    bytes_sent   INTEGER NOT NULL DEFAULT 0,
    bytes_recv   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_net_day ON net_samples(day);

CREATE TABLE IF NOT EXISTS proc_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL    NOT NULL,
    day          TEXT    NOT NULL,
    pid          INTEGER,
    process_name TEXT    NOT NULL,
    exe_path     TEXT,
    event        TEXT    NOT NULL          -- 'start' or 'stop'
);
CREATE INDEX IF NOT EXISTS idx_proc_events_day ON proc_events(day);

CREATE TABLE IF NOT EXISTS key_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               REAL    NOT NULL,
    day              TEXT    NOT NULL,
    process_name     TEXT    NOT NULL,
    exe_path         TEXT,
    window_title     TEXT,
    is_browser       INTEGER NOT NULL DEFAULT 0,
    text             TEXT    NOT NULL,        -- literal typed text for this window burst
    char_count       INTEGER NOT NULL DEFAULT 0,
    suppressed_count INTEGER NOT NULL DEFAULT 0  -- keys dropped in auth fields / shortcuts
);
CREATE INDEX IF NOT EXISTS idx_key_events_day ON key_events(day);
"""

# Optional per-interval columns added to `samples` by migration. Keeping them
# nullable lets historical rows and disabled features coexist cleanly.
SAMPLE_EXTRA_COLUMNS = [
    # window_details
    ("window_class",  "TEXT"),
    ("win_state",     "TEXT"),     # 'normal' | 'maximized' | 'minimized'
    ("win_x",         "INTEGER"),
    ("win_y",         "INTEGER"),
    ("win_w",         "INTEGER"),
    ("win_h",         "INTEGER"),
    ("monitor",       "INTEGER"),  # 0-based index of the monitor showing it
    # process_stats
    ("cpu_pct",       "REAL"),
    ("mem_mb",        "REAL"),
    ("ppid",          "INTEGER"),
    ("parent_name",   "TEXT"),
    ("cmdline",       "TEXT"),
    # input_activity (counts only — never key contents)
    ("key_count",     "INTEGER"),
    ("click_count",   "INTEGER"),
    ("scroll_count",  "INTEGER"),
    ("mouse_dist",    "REAL"),
    # session_state
    ("locked",        "INTEGER"),
    ("screensaver",   "INTEGER"),
    ("session_id",    "INTEGER"),
    # power
    ("on_battery",    "INTEGER"),
    ("battery_pct",   "REAL"),
    # network (denormalized snapshot alongside the sample)
    ("ssid",          "TEXT"),
    # device_usage
    ("camera_in_use", "INTEGER"),
    ("mic_in_use",    "INTEGER"),
]


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


def _existing_columns(conn, table) -> set:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn) -> None:
    """Add any optional `samples` columns introduced after first release."""
    have = _existing_columns(conn, "samples")
    for name, sqltype in SAMPLE_EXTRA_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE samples ADD COLUMN {name} {sqltype}")
    conn.commit()


def init() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        migrate(conn)
        conn.commit()
    finally:
        conn.close()


# Core columns always written, in order, followed by any optional `extra` keys.
_CORE_SAMPLE_COLS = ("ts", "day", "duration_sec", "process_name", "exe_path",
                     "window_title", "is_browser", "page_title", "is_idle")
_VALID_EXTRA = {name for name, _ in SAMPLE_EXTRA_COLUMNS}


def insert_sample(conn, ts, duration_sec, process_name, exe_path,
                  window_title, is_browser, page_title, is_idle,
                  extra=None) -> None:
    """Insert a sample row. `extra` is an optional dict of any of the
    SAMPLE_EXTRA_COLUMNS keys; unknown keys are ignored and missing ones stay
    NULL, so callers only supply what their enabled features collected."""
    cols = list(_CORE_SAMPLE_COLS)
    vals = [ts, local_day(ts), duration_sec, process_name, exe_path,
            window_title, 1 if is_browser else 0, page_title,
            1 if is_idle else 0]
    if extra:
        for k, v in extra.items():
            if k in _VALID_EXTRA and v is not None:
                cols.append(k)
                vals.append(v)
    placeholders = ",".join("?" * len(vals))
    conn.execute(
        f"INSERT INTO samples ({','.join(cols)}) VALUES ({placeholders})", vals)
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


def insert_net_sample(conn, ts, duration_sec, ssid, bytes_sent, bytes_recv) -> None:
    conn.execute(
        """INSERT INTO net_samples
           (ts, day, duration_sec, ssid, bytes_sent, bytes_recv)
           VALUES (?,?,?,?,?,?)""",
        (ts, local_day(ts), duration_sec, ssid, int(bytes_sent), int(bytes_recv)),
    )
    conn.commit()


def insert_proc_event(conn, ts, pid, process_name, exe_path, event) -> None:
    conn.execute(
        """INSERT INTO proc_events (ts, day, pid, process_name, exe_path, event)
           VALUES (?,?,?,?,?,?)""",
        (ts, local_day(ts), pid, process_name, exe_path, event),
    )
    conn.commit()


def insert_key_event(conn, ts, process_name, exe_path, window_title,
                     is_browser, text, char_count, suppressed_count) -> None:
    """Store one window's worth of typed text. Callers must already have
    excluded authentication-field contents; `suppressed_count` records how many
    keystrokes were dropped (auth fields / shortcuts) so the count stays honest
    without retaining the sensitive characters."""
    conn.execute(
        """INSERT INTO key_events
           (ts, day, process_name, exe_path, window_title, is_browser,
            text, char_count, suppressed_count)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (ts, local_day(ts), process_name, exe_path, window_title,
         1 if is_browser else 0, text, int(char_count), int(suppressed_count)),
    )
    conn.commit()


if __name__ == "__main__":
    init()
    print(f"Initialized database at {DB_PATH}")
