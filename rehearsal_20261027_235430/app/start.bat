@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "META_SEED_DEMO=1"
set "META_ALLOW_DEMO_RESET=0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 pause
endlocal
