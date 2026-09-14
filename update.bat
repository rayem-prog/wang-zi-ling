@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================================
echo   StockPilot v2 正在拉取最新代码并更新...
echo ========================================================
git pull --rebase
if not exist .venv (
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt --quiet
)
python scripts\app_runner.py
pause
