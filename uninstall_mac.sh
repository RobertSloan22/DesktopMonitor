#!/bin/bash
# Stop, remove, and de-register ActivityMonitor on macOS. (Keeps your data.)
PLIST="$HOME/Library/LaunchAgents/com.activitymonitor.plist"

echo "Stopping login agent + app ..."
launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
pkill -f "ActivityMonitor.app/Contents/MacOS/ActivityMonitor" 2>/dev/null || true

echo "Removing app ..."
rm -rf "$HOME/Applications/ActivityMonitor.app"

DATA="$HOME/Library/Application Support/ActivityMonitor"
echo
echo "Uninstalled. Your activity history is kept at:"
echo "  $DATA/activity.db"
echo "Delete that folder too if you want to erase your history."
