@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Zakroyte okno start.bat pered zapuskom! Baza: sdelaetsya kopiya v backups\
pause
".venv\Scripts\python.exe" seed_demo_slots.py --db diploma.db --uploads uploads
echo.
echo Otkat: .venv\Scripts\python.exe seed_demo_slots.py --db diploma.db --uploads uploads --restore backups\^<fayl^>.db
pause
