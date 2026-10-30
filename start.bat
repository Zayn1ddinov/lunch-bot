@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist venv (
    echo Устанавливаю зависимости...
    python -m venv venv
    venv\Scripts\pip install -r requirements.txt
)
:loop
venv\Scripts\python bot.py
echo Бот остановился, перезапуск через 10 секунд...
timeout /t 10 >nul
goto loop
