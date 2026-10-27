@echo off
rem Double-click to start the demo. Add -Keep to reuse the data of the last run:  start_demo.bat -Keep
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_demo.ps1" %*
echo.
pause
