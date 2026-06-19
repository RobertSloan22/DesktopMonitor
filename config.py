"""Central configuration for the activity monitor's logging suite.

Every collector capability is gated by a boolean toggle so the tool can be
deployed across a team with a consistent, auditable policy. Resolution order
(later wins):

  1. Built-in defaults below.
  2. Environment variables  ACTIVITY_LOG_<NAME>=1|0|true|false|yes|no.
  3. A JSON file `config.json` in the per-user app-data dir (same dir as the
     database), e.g. {"input_activity": false, "device_usage": true}.

A machine-wide policy can ship the JSON file pre-populated; per-user env vars
can still override it for debugging.

Privacy posture
---------------
`input_activity` records *counts only* — keystrokes-per-interval, click counts,
scroll ticks, and mouse travel distance. It never records which keys were
pressed or any typed text.

`keystroke_text` is the ONE capability that records the literal text you type
(for desktop-activity review). It is **on by default** in this build; set it to
false (via config.json or the env var) to turn it off. When on, it is wired to
*never* capture text typed into
authentication inputs — Windows password controls (ES_PASSWORD), and browser
password / one-time-code fields when the companion extension is installed.
Keystrokes in those fields are counted as "suppressed" but their contents are
discarded. Ctrl/Alt shortcut combinations are likewise not recorded as text.
There is still no clipboard or screenshot capture in this tool.

Enabling `keystroke_text` means the local database will contain text you typed.
Treat that database as sensitive (it is per-user and local; protect it with
full-disk encryption). This capability is intended for monitoring a machine you
own/administer with the knowledge of anyone using it.
"""

import json
import os

import db

# --- toggle catalog ---------------------------------------------------------
# name -> (default_enabled, human description)
FEATURES = {
    "foreground":     (True,  "focused window: process, title, browser page"),
    "app_events":     (True,  "windowed app open/close events"),
    "window_details": (True,  "window class, geometry, min/max state, monitor"),
    "process_stats":  (True,  "foreground process cpu%, memory, parent, cmdline"),
    "input_activity": (True,  "input INTENSITY counts only (keys/clicks/scroll/"
                              "mouse-distance) — never key contents"),
    "keystroke_text": (True,  "literal typed TEXT per window — suppressed in "
                              "password / auth fields (ON by default)"),
    "session_state":  (True,  "screen locked / screensaver / session id"),
    "power":          (True,  "AC vs battery, battery percentage"),
    "network":        (True,  "active Wi-Fi SSID + bytes sent/received"),
    "device_usage":   (False, "camera / microphone in-use (Windows ConsentStore)"),
    "process_events": (False, "start/stop of ALL processes, incl. non-windowed"),
}


def _env_override(name: str):
    raw = os.environ.get(f"ACTIVITY_LOG_{name.upper()}")
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _file_overrides() -> dict:
    path = os.path.join(db.app_data_dir(), "config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def load() -> dict:
    """Resolve the effective toggle map: {feature_name: bool}."""
    file_cfg = _file_overrides()
    resolved = {}
    for name, (default, _desc) in FEATURES.items():
        value = default
        if name in file_cfg:
            value = bool(file_cfg[name])
        env = _env_override(name)
        if env is not None:
            value = env
        resolved[name] = value
    return resolved


def describe(cfg: dict) -> str:
    """One-line-per-feature summary of what is currently being logged."""
    lines = []
    for name, (_default, desc) in FEATURES.items():
        mark = "on " if cfg.get(name) else "off"
        lines.append(f"  [{mark}] {name:<15} {desc}")
    return "\n".join(lines)


if __name__ == "__main__":
    c = load()
    print("Effective activity-monitor logging configuration:\n")
    print(describe(c))
