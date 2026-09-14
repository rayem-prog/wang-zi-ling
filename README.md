# StockPilot — 双引擎 A 股投资决策辅助系统

本地运行的投资辅助工具：LightGBM 多因子（引擎 A）+ 纯规则（引擎 B）双线并行，
动态加权后给出每日建议清单，并结合你的真实资金与持仓输出仓位/加减仓建议。

## 快速开始（Mac）

1. 安装依赖：

   `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`

   macOS 上若 `import lightgbm` 报 `libomp.dylib` 找不到，先执行：

   `export DYLD_LIBRARY_PATH="$PWD/.venv/opt/libomp/lib"`

2. 更新数据（首次约 300 只股票，需几分钟）：

   `python3 scripts/update_data.py`

   数据源自动降级：优先东方财富（AkShare），网络不可达时自动改用腾讯/新浪行情，
   无需手工切换。如你所在网络东财可达且希望强制使用东财数据，
   可设环境变量 `export STOCKPILOT_DATA_SOURCE=em`（默认 `tx`：腾讯/新浪优先，更稳定）。

3. 跑回测看双引擎对比（输出到 artifacts/equity_*.csv 与 backtest_stats.json）：

   `python3 scripts/run_backtest.py`

4. 收盘后全流程：

   `python3 scripts/daily_update.py`

5. 启动看板：

   `python3 scripts/run_dashboard.py`

## 个性化参数（artifacts/user_settings.json）

- risk：单票上限 20%、总仓 80%、止损 7%、止盈 12%
- engine：初始权重 50/50，自动调权区间 20%–80%
- account.cash：你的真实资金

## 测试

`python3 -m pytest`

## 风险提示

历史回测与模拟盘不代表未来收益；本工具不构成投资建议。请务必先完成 M4 模拟盘验证期，
确认绩效跑赢沪深300 且回撤可控后，再考虑投入小额真实资金。
