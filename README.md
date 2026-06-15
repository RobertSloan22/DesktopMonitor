# 🖥️ Activity Monitor

Automatically keeps track of what you do on your Windows PC all day — **no manual
logging**. It quietly records which app/window is in focus, how long you spend in
each, when you're idle/away, the websites you visit, and when apps open and close.
You review it all on a local web dashboard with charts and a timeline.

Everything stays **100% local** on your machine. Nothing is uploaded anywhere.

---

## What it tracks

| Feature                | Detail                                                         |
|------------------------|---------------------------------------------------------------|
| Active app & window    | Focused process + window title, with time spent in each       |
| Idle / away time       | Marks time with no keyboard/mouse (default 60s) so it isn't counted as work |
| Websites (URLs)        | Real URLs + domains via the optional browser extension        |
| Browser page titles    | Page titles from the window title (works even without the extension) |
| App open / close events| Logs when windowed apps are launched and closed               |

---

## Two ways to use it

- **A) Install the self-contained app** — one `.exe`, no Python needed, runs in the
  system tray and starts at login. Best for everyday use.
- **B) Run from source** — quick to try, needs Windows Python. Good for tinkering.

---

## A) Install the self-contained app (recommended)

You build the `.exe` once on a Windows machine that has Python, then it runs
anywhere with no Python required.

1. **Build it** — double-click **`build.bat`**.
   - Installs the build tools and produces **`dist\ActivityMonitor.exe`**.
2. **Install it** — double-click **`install.bat`**.
   - Copies the exe to `%LOCALAPPDATA%\ActivityMonitor`, adds a Start-Menu entry,
     sets it to **auto-start at login**, and launches it now.
3. Look for the **🖥️ tray icon** near the clock. Click it → **Open Dashboard**
   (or go to <http://localhost:8777>).

**Stop it:** tray icon → *Quit*.
**Uninstall:** double-click **`uninstall.bat`** (keeps your data; tells you where).

> The exe is windowless and lives in the tray — no console window pops up at login.

---

## B) Run from source

Requires **Python 3.8+ for Windows** (from <https://python.org> or the Microsoft
Store). Verify with `python --version` in a Command Prompt.

> ⚠️ Use **Windows Python (`python.exe`)**, not the Python inside WSL — WSL can't
> see your Windows apps and windows.

1. Double-click **`setup.bat`** once (installs dependencies).
2. Double-click **`run.bat`** — starts tracking, serves the dashboard at
   <http://localhost:8777>, and shows a tray icon. Keep it running while you work.

Run pieces individually if you like:

```bat
python monitor.py tray        REM tracker + dashboard + tray icon (default)
python monitor.py both        REM tracker + dashboard, console only (no tray)
python monitor.py tracker     REM only collect data
python monitor.py dashboard   REM only view the dashboard
```

---

## The browser extension (real URLs)

The desktop tracker already sees *that* you're in a browser and the page title.
For full **URLs and domains**, install the lightweight extension. It only talks to
`http://localhost:8777` on your own machine.

**Chrome / Edge / Brave (and other Chromium browsers):**

1. Open the extensions page:
   - Chrome: `chrome://extensions`
   - Edge: `edge://extensions`
   - Brave: `brave://extensions`
2. Turn on **Developer mode** (toggle, top-right).
3. Click **Load unpacked** and select this project's **`extension`** folder.
4. Done. While the monitor is running, the dashboard's **Top websites** and
   **Top pages (URLs)** cards fill in. (Heartbeat is ~30s, so give it a minute.)

> Firefox uses a slightly different extension format and isn't included here — its
> page titles are still captured by the desktop tracker.

---

## The dashboard

- **Day picker** — switch between recorded days.
- **Summary** — total active time, idle time, and your top app.
- **Time per app** — horizontal bar chart in minutes.
- **Timeline** — a 24-hour strip colored by which app you were in (idle dimmed).
- **Top websites / Top pages** — domains and URLs (needs the extension).
- **Browser page titles** — works without the extension.
- **App open / close events** — a feed of launches and closes.

---

## Where your data lives

A single SQLite file:

```
%LOCALAPPDATA%\ActivityMonitor\activity.db
```

To erase your history, delete that file. To stop tracking, quit from the tray (or
close `run.bat`). To remove autostart, run `uninstall.bat`.

---

## Tuning

Edit the constants at the top of **`tracker.py`** (then rebuild if using the exe):

```python
INTERVAL_SEC = 5          # how often it samples the focused window
IDLE_THRESHOLD_SEC = 60   # no input for this long = "idle/away"
```

Add browsers to the `BROWSERS` set in `tracker.py` if yours isn't listed. The
extension's heartbeat interval is `INTERVAL_SEC` in `extension/background.js`.

---

## Files

| File                  | Purpose                                            |
|-----------------------|----------------------------------------------------|
| `monitor.py`          | Entry point (`tray` / `both` / `tracker` / `dashboard`) |
| `tracker.py`          | Windows activity collector                         |
| `dashboard.py`        | Local web server + JSON/ingestion API              |
| `tray.py`             | System-tray icon                                   |
| `db.py`               | SQLite storage                                     |
| `static/index.html`   | Dashboard UI                                       |
| `extension/`          | Browser extension (Chromium)                       |
| `build.bat`           | Build the self-contained `ActivityMonitor.exe`     |
| `install.bat` / `uninstall.bat` | Install/remove + autostart               |
| `setup.bat` / `run.bat` | Run from source                                  |
# DesktopMonitor
