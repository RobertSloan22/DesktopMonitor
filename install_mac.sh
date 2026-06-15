#!/bin/bash
# Install ActivityMonitor.app and set it to start at login (LaunchAgent).
set -e
cd "$(dirname "$0")"

APP="dist/ActivityMonitor.app"
if [ ! -d "$APP" ]; then
  echo "Could not find $APP — run ./build_mac.sh first."
  exit 1
fi

echo "Installing to ~/Applications ..."
mkdir -p "$HOME/Applications"
rm -rf "$HOME/Applications/ActivityMonitor.app"
cp -R "$APP" "$HOME/Applications/"

PLIST="$HOME/Library/LaunchAgents/com.activitymonitor.plist"
echo "Creating login agent at $PLIST ..."
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.activitymonitor</string>
  <key>ProgramArguments</key>
  <array>
    <string>$HOME/Applications/ActivityMonitor.app/Contents/MacOS/ActivityMonitor</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
PLISTEOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
open "$HOME/Applications/ActivityMonitor.app"

echo
echo "Installed. It will start automatically each time you log in."
echo "Look for the tray icon in the menu bar; click it -> Open Dashboard."
echo "To uninstall: ./uninstall_mac.sh"
