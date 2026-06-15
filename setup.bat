@echo off
REM One-time setup: install the one Python dependency.
echo Installing dependencies...
python -m pip install -r requirements.txt
echo.
echo Done. Now double-click run.bat to start monitoring.
pause
