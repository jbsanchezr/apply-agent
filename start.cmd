@echo off
rem Double-click launcher for start.ps1 (PowerShell scripts do not run on double-click).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
pause
