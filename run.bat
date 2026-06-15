@echo off
REM Start the activity monitor (tracker + dashboard) on Windows.
cd /d "%~dp0"
echo Starting Activity Monitor...
echo The dashboard will open at http://localhost:8777
echo Keep this window open while you work. Close it (or Ctrl+C) to stop.
echo.
python monitor.py both
pause
