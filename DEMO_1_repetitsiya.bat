@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === REPETITSIYA NA KOPII (baza ne menyaetsya) ===
".venv\Scripts\python.exe" rehearse.py --src .
echo.
echo Itog dolzhen byt: VSE SHAGI OK, TOPIC_FULL net
pause
