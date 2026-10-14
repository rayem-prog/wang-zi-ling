"""收盘后全流程：更新数据 → 因子 → 引擎A/B → 加权 → 建议清单落盘。"""

from __future__ import annotations

import datetime as dt
import json
import inspect
import tempfile
import os
from threading import Event, Thread
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd


from config.settings import Settings, load_settings, save_settings, settings_version
from services.result_pipeline import data_version, record_health_projection, decision_payload
from services.result_store import ResultStore
from data import db, pipeline
from data.health import assess_data_health
from features.alpha import FACTOR_COLUMNS, compute_features
from model import trainer
from strategy import holdings, recap
from strategy.notify import build_notifier
from strategy.decision import decide, Decision
from strategy.evaluation import PredictionLedger

MODEL_PATH = "artifacts/model.joblib"
SCORES_PATH = "artifacts/latest_scores.csv"
REC_PATH = "artifacts/recommendations.csv"
ORDERS_PATH = "artifacts/latest_orders.csv"


def _features_for_all(settings: Settings, codes: list[str]) -> pd.DataFrame:
    frames = []
    for code in codes:
        bars = db.load_training_bars(settings.data.db_path, code, adjustment=settings.data.adjust,
                                     gap_policy='segments')
        if len(bars) < 61:
            continue
        features = compute_features(bars)
        frames.append(features[features['feature_ready']])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def run_daily(settings: Settings | None = None, fast_mode: bool = False,
              now: dt.datetime | None = None, send_notification: bool = True, *,
              progress=None, job_id=None, generation_lease=None) -> dict:
    """Sync/CLI producers share the same lease as API workers."""
    from api.jobs import GenerationLease, new_generation_job, patch_job, job_response, utc_now
    settings = settings or load_settings()
    own_lease = generation_lease is None
    lease = generation_lease or GenerationLease(settings.paths.artifact_dir)
    if own_lease and not lease.acquire():
        return {'blocked': True, 'error': '已有结果生成任务正在执行，请等待任务完成'}
    if lease.fd is None or lease.path != (Path(settings.paths.artifact_dir)/'generation.lock').resolve():
        raise ValueError('结果生成租约与请求目录不一致')
    store = ResultStore(settings.paths.artifact_dir)
    own_job = job_id is None
    stopped = Event()
    pulse = None
    effective_progress = progress
    try:
        if own_job:
            job = new_generation_job(lease, fast_mode=fast_mode, status='running')
            job_id = job['job_id']
            store.save_job(job)
            def tracked_progress(stage):
                patch_job(store, job_id, stage=stage)
                if progress:
                    progress(stage)
            effective_progress = tracked_progress
            def heartbeat():
                while not stopped.wait(10):
                    patch_job(store, job_id)
            pulse = Thread(target=heartbeat, daemon=True, name='sync-generation-heartbeat')
            pulse.start()
        # The durable marker also keeps a concurrently finishing health audit
        # from restoring eligibility before this generation has published.
        record_health_projection(settings, dict(ready=False, health_cache_stale=True,
            reason='正在生成新结果，当前操作需等待行情核验完成'), 'pending', now=now)
        result = _run_daily(settings, fast_mode, now, send_notification,
                            progress=effective_progress, job_id=job_id)
        if own_job:
            blocked = bool(result.get('blocked'))
            error = result.get('error') or '行情未通过检查' if blocked else None
            patch_job(store, job_id, status='failed' if blocked else 'succeeded',
                stage=store.get_job(job_id).get('stage') if blocked else 'complete',
                result=job_response(result), finished_at=utc_now(), error=error,
                error_info=dict(code='generation_failed',message=error,retryable=True) if error else None,
                warnings=list(result.get('warnings', [])))
        return result
    except BaseException as exc:
        if own_job and job_id is not None:
            job = store.get_job(job_id)
            if job.get('status') == 'succeeded':
                patch_job(store, job_id, warnings=list(job.get('warnings', []))+[f'发布后处理失败: {exc}'])
            else:
                patch_job(store, job_id, status='failed', error=str(exc), finished_at=utc_now(),
                    error_info=dict(code='generation_failed',message=str(exc) or type(exc).__name__,retryable=True))
        raise
    finally:
        stopped.set()
        if pulse is not None:
            pulse.join(timeout=1)
        if own_lease:
            lease.release()


def _run_daily(settings, fast_mode, now, send_notification, *, progress=None, job_id=None):
    settings = settings or load_settings()
    artifact_dir = Path(settings.paths.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    def stage(name):
        if progress:
            progress(name)
    if fast_mode:
        codes = db.list_codes(settings.data.db_path)
    else:
        stage('fetch')
        try:
            if 'progress' in inspect.signature(pipeline.update_all).parameters:
                summary = pipeline.update_all(settings,progress=stage)
            else:
                summary = pipeline.update_all(settings)
            codes = summary.get("requested_codes") or summary.get("fresh_codes") or summary.get("updated") or []
        except Exception as exc:
            return {"blocked": True, "error": f"行情更新失败: {exc}"}
    if not codes:
        codes = db.list_codes(settings.data.db_path)

    # 排除大盘基准指数与非个股标的
    codes = [
        c for c in codes
        if c != settings.data.index_symbol
        and not c.startswith("sh000")
        and not c.startswith("sz399")
    ]

    stage('audit')
    before_audit = data_version(settings)
    health = assess_data_health(settings, now=now,
                                expected_codes=codes if codes else None)
    if not health["ready"]:
        record_health_projection(settings, health, 'failed', now=now)
        return {"blocked": True, "error": health["reason"], "data_health": health,
                "failed_codes": summary.get("failed", [])}
    codes = health.get("eligible_codes", codes)
    meta_path = artifact_dir / "latest_recommendations.meta.json"
    frozen_version = data_version(settings)
    if before_audit != frozen_version:
        record_health_projection(settings, dict(health, ready=False, reason='审计期间行情版本发生变化'), 'failed', now=now)
        raise ValueError('审计期间行情版本发生变化，请重新检查')
    parameters_version = settings_version(settings)
    stage('features')

    features = _features_for_all(settings, codes)
    if features.empty:
        return {
            "blocked": True,
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
    last_date = features["date"].max()
    if str(last_date) != health["trade_date"]:
        return {"blocked": True, "error": "可计算因子的交易日与最新行情不一致",
                "data_health": health}
    today = features[features["date"] == last_date]
    # 收盘推荐使用同一交易日的真实收盘价与涨跌幅，不混用过期的实时快照。
    names = db.load_spot(settings.data.db_path)
    name_map = dict(zip(names["code"].astype(str), names["name"])) if not names.empty else {}
    quotes = today[["code", "close", "ret_1", "volume", "amount"]].copy()
    quotes = quotes.rename(columns={"close": "price", "ret_1": "pct_change"})
    quotes["pct_change"] *= 100
    quotes["name"] = quotes["code"].map(name_map).fillna(quotes["code"])
    try:
        current_holdings = holdings.load_positions(settings.data.db_path)
        current_holdings = current_holdings.merge(quotes[["code", "price"]], on="code", how="left")
    except Exception as exc:
        return {"blocked": True, "error": f"持仓读取失败，无法生成安全指令单: {exc}",
                "data_health": health}

    # Evaluate only previously frozen predictions with a complete future horizon.
    bench_bars = db.load_training_bars(settings.data.db_path, settings.data.index_symbol, adjustment="none")
    ledger = PredictionLedger(settings.data.db_path)
    try:
        weight_eval = ledger.advance(features, bench_bars, last_date, settings.engine)
        frozen = ledger.get(str(last_date))
        features = features.merge(quotes[["code", "name"]], on="code", how="left")
        def new_served_decision():
            optional = {}
            signature = inspect.signature(decide)
            if 'progress' in signature.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
                optional['progress'] = stage
            current = decide(features, bench_bars, last_date, settings, weight_eval["new_w_a"], current_holdings, **optional)
            payload = current.to_payload()
            payload["benchmark_close"] = float(bench_bars.sort_values("date").iloc[-1]["close"])
            payload["provenance"] = {"trade_date": str(last_date), "source": health["source"],
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "data_version": frozen_version, "parameters_version": parameters_version,
                "coverage": health["coverage"], "price_source": "daily_bar_close",
                "updated_at": health["updated_at"], "history_coverage": health["history_coverage"]}
            return payload
        if frozen is None:
            frozen = ledger.freeze(str(last_date), new_served_decision())
        elif (frozen.get('provenance', {}).get('data_version') != frozen_version
              or frozen.get('provenance', {}).get('parameters_version') != parameters_version):
            # Predictions used for future performance stay immutable. A separately
            # generated served decision uses the currently audited inputs/settings.
            frozen = new_served_decision()
        decision = Decision.from_payload(frozen)
    finally:
        ledger.close()
    macro_res = decision.macro
    rule_audit = decision.rule_audit
    lgb_audit = decision.model_audit
    # Legacy frozen audits identify a real fitted model by its digest and anchor.
    model_trained = bool(lgb_audit.get('model_trained',
                         lgb_audit.get('model_digest') and lgb_audit.get('training_as_of')))
    merged = decision.scores.copy()
    wa, wb = decision.w_a, round(1-decision.w_a, 4)
    db.save_macro_score(settings.data.db_path, date=str(last_date), score=macro_res.score,
        stance=macro_res.stance, trend=macro_res.trend_score,
        breadth=macro_res.breadth_score, vol=macro_res.vol_score)
    provenance = frozen["provenance"]
    for key, value in provenance.items():
        merged[key] = value


    # 5. 选股与仓位约束
    plan = decision.plan.copy()
    for key, value in provenance.items():
        plan[key] = value


    # 6. 产出次日指令单
    orders_df = decision.orders.copy()
    for key, value in provenance.items():
        orders_df[key] = value
    stage('validate')
    result_payload = decision_payload(settings, decision, provenance, frozen, now=now)
    store = ResultStore(artifact_dir)
    stage('persist')
    snapshot_id = store.stage(result_payload, job_id=job_id)
    stage('publish')
    if data_version(settings) != frozen_version or settings_version(settings) != parameters_version:
        record_health_projection(settings, dict(health, ready=False, reason='生成期间行情版本或参数发生变化'), 'failed', now=now)
        raise ValueError('生成期间行情版本或参数发生变化，请重新生成')
    completed_summary = {
        "snapshot_id": snapshot_id,
        "warnings": [],
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
        "data_health": health,
    }
    from api.jobs import job_response
    publication_response = job_response(completed_summary)
    publication_response["report_pending"] = True
    # Publish and optional job success form one SQLite transaction. Compatibility
    # files and reports are derived only after the immutable head is committed.
    record_health_projection(settings, health, 'passed', now=now)
    store.publish(snapshot_id, expected_versions=result_payload['versions'], job_id=job_id,
                  job_result=publication_response if job_id is not None else None)
    warnings = []
    try:
        for name, frame in (('latest_scores.csv', merged), ('recommendations.csv', plan), ('latest_orders.csv', orders_df)):
            fd, temporary = tempfile.mkstemp(prefix=name+'.', dir=artifact_dir)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as output:
                    frame.to_csv(output, index=False)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, artifact_dir/name)
            finally:
                Path(temporary).unlink(missing_ok=True)
        pending_path = meta_path.with_suffix('.json.tmp')
        pending_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')
        pending_path.replace(meta_path)
    except Exception as exc:
        warnings.append(f'兼容文件导出失败: {exc}')

    # 7. 生成 7 段式每日复盘 Markdown
    mature = weight_eval.get('evaluated_count',0) > 0
    pending_performance = "暂无新成熟预测可评价；不把当日涨跌作为预测收益。"
    engine_perf = {
        "engine_a": f"五交易日预测样本收益 {weight_eval.get('r_a', 0.0):+.2f}%, 超额 {weight_eval.get('er_a', 0.0):+.2f}%, 胜率 {weight_eval.get('win_rate_a', 0.0):.1f}%" if mature else pending_performance,
        "engine_b": f"五交易日预测样本收益 {weight_eval.get('r_b', 0.0):+.2f}%, 超额 {weight_eval.get('er_b', 0.0):+.2f}%, 胜率 {weight_eval.get('win_rate_b', 0.0):.1f}%" if mature else pending_performance,
        "blend": f"自适应动态权重 ({wa:.2f}:{wb:.2f}) · 今日变动 {weight_eval.get('weight_delta', 0.0):+.2%}",
        "weight_audit": (
            f"- **权重演进**: 引擎 A 权重由 `{weight_eval.get('old_w_a', 0.5):.2f}` 调整至 `{wa:.2f}` (变动 `{weight_eval.get('weight_delta', 0.0):+.2%}`)，"
            f"引擎 B 权重调整至 `{wb:.2f}`。\n"
            f"- **调权依据**: 仅依据已冻结历史预测的成熟未来五交易日收益评价，单批预测只调权一次。"
        ),
        "rule_audit": f"**{rule_audit.get('regime_name', '自适应多因子公式')}** —— {rule_audit.get('regime_desc', '')}",
        "model_audit": lgb_audit.get("status", "LightGBM 模型样本已更新，特征收敛正常"),
    }
    try:
        recap_md = recap.generate_daily_recap(
            trade_date=str(last_date),
            market_summary={"index_text": f"{settings.data.index_symbol} 温度计得分 {macro_res.score:+.2f}"},
            holdings_summary={"market_value": (float((current_holdings["shares"] * current_holdings["price"]).sum())
                                             if current_holdings["price"].notna().all() else None)},
            engine_perf=engine_perf,
            orders_df=orders_df,
            macro_res=macro_res,
            save_dir=str(Path(settings.paths.artifact_dir) / "reports"),
        )
    except Exception as exc:
        warnings.append(f'复盘报告生成失败: {exc}')

    # 8. 发送 P2 收盘通知摘要
    try:
        if send_notification:
            notifier = build_notifier(settings)
            summary_text = recap.generate_notification_summary(str(last_date), macro_res=macro_res, orders_df=orders_df)
            notifier.send("每日收盘决策简报", summary_text, level="P2", key=f"recap:{last_date}")
    except Exception as exc:
        warnings.append(f'通知发送失败: {exc}')
    stage('complete')

    completed_summary['warnings'] = warnings
    return completed_summary



def _score_a(settings, features, today, codes):
    """引擎 A：模型存在直接预测；不存在则给出中性 50 分。"""
    model_path = Path(settings.paths.artifact_dir) / "model.joblib"
    if not model_path.exists():
        return pd.DataFrame({"code": codes, "date": today["date"].iloc[0], "score_a": 50.0})
    model = trainer.load_model(str(model_path))
    factor_cols = FACTOR_COLUMNS
    X = today[factor_cols].astype(float).to_numpy()
    scores = trainer.predict_scores(model, X)
    return pd.DataFrame({"code": today["code"].tolist(), "date": today["date"].tolist(), "score_a": scores})


def main() -> None:
    settings = load_settings()
    save_settings(settings)
    print(run_daily(settings))


if __name__ == "__main__":
    main()
