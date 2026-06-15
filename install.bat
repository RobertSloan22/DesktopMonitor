@echo off
REM Install the built ActivityMonitor.exe and set it to start at login.
cd /d "%~dp0"

if not exist "dist\ActivityMonitor.exe" (
  echo Could not find dist\ActivityMonitor.exe
  echo Run build.bat first.
  pause
  exit /b 1
)

set "DEST=%LOCALAPPDATA%\ActivityMonitor"
echo Installing to "%DEST%" ...
if not exist "%DEST%" mkdir "%DEST%"
copy /Y "dist\ActivityMonitor.exe" "%DEST%\ActivityMonitor.exe" >nul

echo Creating Start Menu and login-startup shortcuts ...
powershell -NoProfile -Command ^
  "$w=New-Object -ComObject WScript.Shell;" ^
  "$startup=$w.SpecialFolders('Startup');" ^
  "$programs=$w.SpecialFolders('Programs');" ^
  "foreach($dir in @($startup,$programs)){" ^
  "  $lnk=$w.CreateShortcut(\"$dir\ActivityMonitor.lnk\");" ^
  "  $lnk.TargetPath=\"%DEST%\ActivityMonitor.exe\";" ^
  "  $lnk.WorkingDirectory=\"%DEST%\";" ^
  "  $lnk.Save() }"

echo.
echo Installed. Starting it now (look for the tray icon near the clock)...
start "" "%DEST%\ActivityMonitor.exe"
echo It will also start automatically each time you log in.
echo To uninstall: run uninstall.bat
pause
