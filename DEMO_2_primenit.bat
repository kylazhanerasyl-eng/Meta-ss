@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Zakryvayu server na 8002 (esli zapushchen) ===
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8002 ^| findstr LISTENING') do taskkill /PID %%p /F
timeout /t 2 >nul
".venv\Scripts\python.exe" seed_demo_slots.py --db diploma.db --uploads uploads
echo Itog: kod 0 = gotovo. Otkat: seed_demo_slots.py --restore backups\<fayl>.db
pause
