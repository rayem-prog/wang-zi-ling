#!/usr/bin/env bash
# StockPilot v2 一键启动脚本
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# 检查 Python 虚拟环境
if [ ! -d ".venv" ]; then
    echo "❌ 未找到 .venv 虚拟环境，请先初始化环境。"
    exit 1
fi

# 检查前端构建产物
if [ ! -d "frontend/dist" ]; then
    echo "📦 首次运行，正在编译前端静态终端..."
    (cd frontend && npm run build)
fi

# 检查并释放可能被旧进程占用的 8888 端口
PORT_PIDS=$(lsof -ti :8888 2>/dev/null || true)
if [ -n "$PORT_PIDS" ]; then
    echo "🔄 正在自动释放被占用的端口 8888 (PID: $PORT_PIDS)..."
    kill -9 $PORT_PIDS 2>/dev/null || true
    sleep 0.4
fi

# 启动综合应用
exec ./.venv/bin/python scripts/app_runner.py "$@"
