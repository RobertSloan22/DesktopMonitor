@echo off
REM Stop, remove, and de-register ActivityMonitor. (Keeps your data by default.)
set "DEST=%LOCALAPPDATA%\ActivityMonitor"

echo Stopping ActivityMonitor ...
taskkill /IM ActivityMonitor.exe /F >nul 2>&1

echo Removing startup + Start Menu shortcuts ...
powershell -NoProfile -Command ^
  "$w=New-Object -ComObject WScript.Shell;" ^
  "foreach($d in @($w.SpecialFolders('Startup'),$w.SpecialFolders('Programs'))){" ^
  "  $p=Join-Path $d 'ActivityMonitor.lnk'; if(Test-Path $p){Remove-Item $p} }"

echo Removing program file ...
if exist "%DEST%\ActivityMonitor.exe" del /F /Q "%DEST%\ActivityMonitor.exe"

echo.
echo Uninstalled. Your activity history is kept at:
echo   %DEST%\activity.db
echo Delete that file too if you want to erase your history.
pause
