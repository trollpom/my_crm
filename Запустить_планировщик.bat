@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Запускаю локальный планировщик...
python run.py
pause
