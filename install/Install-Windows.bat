@echo off
rem Double-click this to install the program on Windows.
rem It runs install\windows.ps1, which explains every step as it goes.
cd /d "%~dp0\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows.ps1" %*
echo.
pause
