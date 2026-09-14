#!/usr/bin/env bash
# StockPilot v2 一键更新并启动脚本
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "=========================================================="
echo "🔄 正在检查并更新 StockPilot 到最新版本..."
echo "=========================================================="

# 1. 如果是 Git 仓库，自动拉取最新代码（不覆盖本地持仓与个人账本）
if [ -d ".git" ]; then
    echo "📥 正在从远程仓库同步最新代码 (git pull)..."
    git pull --rebase || {
        echo "⚠️ git pull 存在未提交冲突，建议检查本地修改，继续尝试启动..."
    }
else
    echo "ℹ️ 当前未启用 Git 仓库，跳过拉取。"
fi

# 2. 检查 Python 虚拟环境与新依赖
if [ -d ".venv" ]; then
    echo "📦 检查并更新 Python 依赖..."
    ./.venv/bin/pip install -r requirements.txt --quiet || true
else
    echo "⚙️ 首次运行，正在自动创建 Python 虚拟环境..."
    python3 -m venv .venv
    ./.venv/bin/pip install --upgrade pip --quiet
    ./.venv/bin/pip install -r requirements.txt --quiet
fi

# 3. 启动应用
echo "🚀 启动最新版 StockPilot..."
./run.sh "$@"
