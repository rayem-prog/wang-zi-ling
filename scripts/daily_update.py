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


def run_daily(settings: Settings | None = None) -> dict:
    settings = settings or load_settings()
    Path(settings.paths.artifact_dir).mkdir(parents=True, exist_ok=True)
    summary = pipeline.update_all(settings)
    codes = summary["updated"]
    features = _features_for_all(settings, codes)
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
    score_a = _score_a(settings, features, today, codes)
    score_b = rules.score_engine_b(today)
    merged = (
        today[["code", "date", "vol_20"]]
        .merge(score_a, on=["code", "date"], how="left")
        .merge(score_b, on=["code", "date"], how="left")
    )
    wa, wb = bw.current_weights(settings.engine, settings.data.db_path)
    merged = bw.merge_scores(merged, wa)
    merged.to_csv(SCORES_PATH, index=False)
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

    # 2. 选股与仓位约束
    rec = signals.build_recommendations(merged, quotes)
    median_vol = float(today["vol_20"].median()) if "vol_20" in today else 0.02
    plan = sizing.compute_plan(rec, median_vol=median_vol, settings=settings, macro_res=macro_res)
    plan.to_csv(REC_PATH, index=False)

    # 3. 产出次日指令单
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

    # 4. 生成 7 段式每日复盘 Markdown
    recap_md = recap.generate_daily_recap(
        trade_date=str(last_date),
        market_summary={"index_text": f"{settings.data.index_symbol} 温度计得分 {macro_res.score:+.2f}"},
        holdings_summary={"market_value": settings.account.cash, "daily_pnl": 0.0, "daily_pnl_pct": 0.0, "excess_pct": 0.0},
        engine_perf={"engine_a": "运行中", "engine_b": "运行中", "blend": f"动态权重 ({wa:.2f}:{wb:.2f})"},
        orders_df=orders_df,
        macro_res=macro_res,
        save_dir=str(Path(settings.paths.artifact_dir) / "reports"),
    )

    # 5. 发送 P2 收盘通知摘要
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
