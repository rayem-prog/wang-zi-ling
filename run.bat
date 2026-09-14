@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================================
echo   StockPilot v2 正在启动...
echo ========================================================
if not exist .venv (
    echo ⚙️ 首次运行，正在自动创建 Python 虚拟环境...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)
python scripts\app_runner.py
pause
