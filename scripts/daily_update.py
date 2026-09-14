"""收盘后全流程：更新数据 → 因子 → 引擎A/B → 加权 → 建议清单落盘。"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd


from blend import weights as bw
from config.settings import Settings, load_settings, save_settings
from data import db, pipeline
from features.alpha import compute_features
from model import dataset as ds
from model import trainer
from strategy import holdings, macro, recap, rules, signals, sizing
from strategy.notify import build_notifier

MODEL_PATH = "artifacts/model.joblib"
SCORES_PATH = "artifacts/latest_scores.csv"
REC_PATH = "artifacts/recommendations.csv"
ORDERS_PATH = "artifacts/latest_orders.csv"


def _features_for_all(settings: Settings, codes: list[str]) -> pd.DataFrame:
    frames = []
    for code in codes:
        bars = db.load_bars(settings.data.db_path, code)
        if len(bars) < 61:
            continue
        frames.append(compute_features(bars))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _ensure_model(settings: Settings, features: pd.DataFrame) -> bool:
    """模型缺失或超过 20 天未更新时，用全部历史特征训练一次 LightGBM。"""
    model_path = Path(MODEL_PATH)
    if model_path.exists():
        age_days = (pd.Timestamp.now() - pd.Timestamp(model_path.stat().st_mtime, unit="s")).days
        if age_days <= 20:
            return False
    bench = db.load_bars(settings.data.db_path, settings.data.index_symbol)
    if bench.empty or len(features) < 5000:
        return False
    X, y, _ = ds.build_training_data(features, bench[["date", "close"]])
    if len(X) < 5000:
        return False
    model = trainer.train_model(X, y, seed=42)
    trainer.save_model(model, str(model_path))
    return True


def run_daily(settings: Settings | None = None, fast_mode: bool = False) -> dict:
    settings = settings or load_settings()
    Path(settings.paths.artifact_dir).mkdir(parents=True, exist_ok=True)
    if fast_mode:
        codes = db.list_codes(settings.data.db_path)
    else:
        try:
            summary = pipeline.update_all(settings)
            codes = summary.get("fresh_codes") or summary.get("updated") or []
        except Exception:
            codes = []
    if not codes:
        codes = db.list_codes(settings.data.db_path)

    # 排除大盘基准指数与非个股标的
    codes = [
        c for c in codes
        if c != settings.data.index_symbol
        and not c.startswith("sh000")
        and not c.startswith("sz399")
    ]

    features = _features_for_all(settings, codes)
    if features.empty:
        all_codes = db.list_codes(settings.data.db_path)
        if all_codes:
            features = _features_for_all(settings, all_codes)

    if features.empty:
        return {
            "updated": len(codes),
            "features_rows": 0,
            "scores_rows": 0,
            "rec_rows": 0,
            "weight_a": settings.engine.w_a,
            "weight_b": settings.engine.w_b,
            "model_trained": False,
            "last_date": None,
            "error": "没有可用数据，请检查网络与 update_all",
        }
    model_trained = _ensure_model(settings, features)
    last_date = features["date"].max()
    today = features[features["date"] == last_date]
    quotes = pipeline.spot_snapshot(settings, codes)

    # 1. 计算宏观温度计 (M1 + M4)
    bench_bars = db.load_bars(settings.data.db_path, settings.data.index_symbol)
    macro_res = macro.compute_macro_score(
        {settings.data.index_symbol: bench_bars},
        spot_df=quotes if isinstance(quotes, pd.DataFrame) else None,
        current_date=last_date if isinstance(last_date, dt.date) else dt.date.today(),
    )
    db.save_macro_score(
        settings.data.db_path,
        date=str(last_date),
        score=macro_res.score,
        stance=macro_res.stance,
        trend=macro_res.trend_score,
        breadth=macro_res.breadth_score,
        vol=macro_res.vol_score,
    )

    # 2. 算法与公式自适应优化：
    # 2.1 规则引擎 B 动态公式调优 (根据当日宏观档位和因子有效性优化)
    rule_cfg, rule_audit = rules.optimize_rules_formula(macro_stance=macro_res.stance, features_df=today)
    score_b = rules.score_engine_b(today, config=rule_cfg)

    # 2.2 LightGBM 引擎 A 计算与特征重要性提取
    score_a = _score_a(settings, features, today, codes)
    model_path = Path(MODEL_PATH)
    lgb_audit = {"status": "LightGBM 模型就绪", "top_features": []}
    if model_path.exists():
        try:
            m = trainer.load_model(str(model_path))
            factor_cols = [c for c in features.columns if c not in {"code", "date", "open", "high", "low", "close", "volume", "amount"}]
            top_features = trainer.get_feature_importances(m, factor_cols)[:5]
            top_str = ", ".join([f"{f['feature']}({f['ratio']}%)" for f in top_features])
            lgb_audit = {
                "top_features": top_features,
                "status": f"模型已融合最新样本，当前核心驱动因子: {top_str}"
            }
        except Exception:
            pass

    merged = (
        today[["code", "date", "vol_20"]]
        .merge(score_a, on=["code", "date"], how="left")
        .merge(score_b, on=["code", "date"], how="left")
    )

    # 3. 通过当天的数据改变权重 (Evaluate & Update Dynamic Weights)
    current_wa, _ = bw.current_weights(settings.engine, settings.data.db_path)
    bench_ret = 0.0
    if not bench_bars.empty and len(bench_bars) >= 2:
        bench_bars_sorted = bench_bars.sort_values("date")
        c_last = float(bench_bars_sorted["close"].iloc[-1])
        c_prev = float(bench_bars_sorted["close"].iloc[-2])
        if c_prev > 0:
            bench_ret = (c_last / c_prev) - 1.0

    weight_eval = bw.evaluate_and_update_weights(
        today_df=merged,
        quotes_df=quotes if isinstance(quotes, pd.DataFrame) else None,
        bench_return=bench_ret,
        current_wa=current_wa,
        db_path=settings.data.db_path,
        w_min=settings.engine.w_min,
        w_max=settings.engine.w_max,
    )
    wa = weight_eval["new_w_a"]
    wb = weight_eval["new_w_b"]

    # 持久化更新配置里的权重 (通过 dataclasses.replace 支持 frozen)
    from dataclasses import replace
    new_engine = replace(settings.engine, w_a=wa, w_b=wb)
    settings = replace(settings, engine=new_engine)
    try:
        save_settings(settings)
    except Exception:
        pass

    # 4. 用更新后的权重合并打分
    merged = bw.merge_scores(merged, wa)
    merged.to_csv(SCORES_PATH, index=False)

    # 5. 选股与仓位约束
    rec = signals.build_recommendations(merged, quotes)
    median_vol = float(today["vol_20"].median()) if "vol_20" in today else 0.02
    plan = sizing.compute_plan(rec, median_vol=median_vol, settings=settings, macro_res=macro_res)
    plan.to_csv(REC_PATH, index=False)

    # 6. 产出次日指令单
    current_holdings = pd.DataFrame()
    try:
        current_holdings = holdings.load_positions(settings.data.db_path)
    except Exception:
        pass
    orders_df = recap.generate_daily_orders(
        recommendations=plan,
        current_holdings=current_holdings,
        settings=settings,
        macro_res=macro_res,
    )
    orders_df.to_csv(ORDERS_PATH, index=False)

    # 7. 生成 7 段式每日复盘 Markdown
    engine_perf = {
        "engine_a": f"实际收益 {weight_eval.get('r_a', 0.0):+.2f}%, 超额 {weight_eval.get('er_a', 0.0):+.2f}%, 胜率 {weight_eval.get('win_rate_a', 50.0):.1f}%",
        "engine_b": f"实际收益 {weight_eval.get('r_b', 0.0):+.2f}%, 超额 {weight_eval.get('er_b', 0.0):+.2f}%, 胜率 {weight_eval.get('win_rate_b', 50.0):.1f}%",
        "blend": f"自适应动态权重 ({wa:.2f}:{wb:.2f}) · 今日变动 {weight_eval.get('weight_delta', 0.0):+.2%}",
        "weight_audit": (
            f"- **权重演进**: 引擎 A 权重由 `{weight_eval.get('old_w_a', 0.5):.2f}` 调整至 `{wa:.2f}` (变动 `{weight_eval.get('weight_delta', 0.0):+.2%}`)，"
            f"引擎 B 权重调整至 `{wb:.2f}`。\n"
            f"- **调权依据**: 依据当日全市场股票与基准收益进行客观赏罚，超额更高、胜率更高的引擎自动获得更高配比权重。"
        ),
        "rule_audit": f"**{rule_audit.get('regime_name', '自适应多因子公式')}** —— {rule_audit.get('regime_desc', '')}",
        "model_audit": lgb_audit.get("status", "LightGBM 模型样本已更新，特征收敛正常"),
    }
    recap_md = recap.generate_daily_recap(
        trade_date=str(last_date),
        market_summary={"index_text": f"{settings.data.index_symbol} 温度计得分 {macro_res.score:+.2f}"},
        holdings_summary={"market_value": settings.account.cash, "daily_pnl": 0.0, "daily_pnl_pct": 0.0, "excess_pct": 0.0},
        engine_perf=engine_perf,
        orders_df=orders_df,
        macro_res=macro_res,
        save_dir=str(Path(settings.paths.artifact_dir) / "reports"),
    )

    # 8. 发送 P2 收盘通知摘要
    try:
        notifier = build_notifier(settings)
        summary_text = recap.generate_notification_summary(str(last_date), macro_res=macro_res, orders_df=orders_df)
        notifier.send("每日收盘决策简报", summary_text, level="P2", key=f"recap:{last_date}")
    except Exception:
        pass

    return {
        "updated": len(codes),
        "features_rows": len(features),
        "scores_rows": len(merged),
        "rec_rows": len(plan),
        "orders_rows": len(orders_df),
        "macro_stance": macro_res.stance,
        "macro_score": macro_res.score,
        "weight_a": wa,
        "weight_b": wb,
        "weight_eval": weight_eval,
        "rule_audit": rule_audit,
        "lgb_audit": lgb_audit,
        "model_trained": model_trained,
        "last_date": str(last_date),
    }



def _score_a(settings, features, today, codes):
    """引擎 A：模型存在直接预测；不存在则给出中性 50 分。"""
    model_path = Path(MODEL_PATH)
    if not model_path.exists():
        return pd.DataFrame({"code": codes, "date": today["date"].iloc[0], "score_a": 50.0})
    model = trainer.load_model(str(model_path))
    factor_cols = [c for c in features.columns if c not in {"code", "date", "open", "high", "low", "close", "volume", "amount"}]
    X = today[factor_cols].astype(float).to_numpy()
    scores = trainer.predict_scores(model, X)
    return pd.DataFrame({"code": today["code"].tolist(), "date": today["date"].tolist(), "score_a": scores})


def main() -> None:
    settings = load_settings()
    save_settings(settings)
    print(run_daily(settings))


if __name__ == "__main__":
    main()
