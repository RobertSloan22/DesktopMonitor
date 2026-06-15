@echo off
REM Build a single self-contained ActivityMonitor.exe (no Python needed to run).
REM Run this once on a Windows machine that has Python installed.
cd /d "%~dp0"

echo Installing build + runtime dependencies...
python -m pip install -r requirements.txt pyinstaller || goto :err

echo.
echo Building ActivityMonitor.exe ...
pyinstaller --noconfirm --onefile --noconsole --name ActivityMonitor ^
  --add-data "static;static" ^
  --hidden-import pystray._win32 ^
  --hidden-import collector_win ^
  --collect-submodules pystray ^
  monitor.py || goto :err

echo.
echo Done.  ->  dist\ActivityMonitor.exe
echo Next: run install.bat to install it and start it at login.
pause
exit /b 0

:err
echo.
echo Build failed. Make sure Python 3.8+ is installed and on PATH.
pause
exit /b 1
