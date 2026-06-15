#!/bin/bash
# Build a self-contained macOS app bundle (ActivityMonitor.app).
# Run this ON a Mac that has python3 (PyInstaller can't cross-compile).
set -e
cd "$(dirname "$0")"

PY="$(command -v python3)"
echo "Installing build + runtime dependencies..."
"$PY" -m pip install --user -r requirements.txt pyinstaller

echo "Building ActivityMonitor.app ..."
"$PY" -m PyInstaller --noconfirm --windowed --name ActivityMonitor \
  --add-data "static:static" \
  --hidden-import collector_mac \
  --collect-submodules pystray \
  monitor.py

echo
echo "Done.  ->  dist/ActivityMonitor.app"
echo "Run install_mac.sh to install it and start it at login."
echo
echo "NOTE: macOS will ask for permissions on first run:"
echo "  - (optional) Screen Recording  -> needed only for window/page titles"
echo "  The app name, time-per-app, idle, and app open/close work without it."
