#!/bin/bash
# macOS launcher — double-click in Finder to start the Activity Monitor.
# Runs from source (most reliable on macOS for the permissions model).
cd "$(dirname "$0")" || exit 1

PY="$(command -v python3)"
if [ -z "$PY" ]; then
  echo "python3 not found. Install it from https://python.org or via 'xcode-select --install'."
  read -r -p "Press Return to close."
  exit 1
fi

echo "Checking dependencies..."
"$PY" -m pip install --quiet --user -r requirements.txt

echo "Starting Activity Monitor (tray icon will appear in the menu bar)."
echo "Dashboard: http://localhost:8777"
echo "To expose it to your local network, run instead:"
echo "    ACTIVITY_MONITOR_HOST=0.0.0.0 ./run.command"
echo
"$PY" monitor.py tray
