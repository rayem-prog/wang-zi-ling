"""Isolated real-TCP verification for GET /api/signals/snapshot.

Run `python docs/signals-repair-20261012/http_acceptance.py --benchmark` for
three 5,000-row HTTP latency scenes, or `... --browser-server` to inspect the
built frontend against a temporary result database. No production settings,
market database, account, or notifications are used.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import math
import os
import platform
import socket
import statistics
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from config.settings import Settings, settings_version
from services.result_store import ResultStore

DIST = ROOT / "frontend" / "dist"
NOW = datetime.now(timezone.utc).astimezone()


def fixture_settings(directory: Path) -> Settings:
    base = Settings()
    return replace(base,
        data=replace(base.data, db_path=str(directory / "market.db")),
        paths=replace(base.paths, artifact_dir=str(directory / "artifacts")),
        notify=replace(base.notify, backend="mock"))


def make_payload(settings: Settings, count: int) -> dict:
    rows = []
    for index in range(count):
        price = 5. + (index % 400) * .5
        bracket = "low" if price <= 20 else "mid" if price <= 50 else "high" if price <= 100 else "top"
        rows.append({"code": f"{index + 1:06d}", "name": f"Fixture {index + 1:05d}",
                     "price": price, "score_blend": float(100 - index % 101),
                     "price_bracket": bracket, "rank": index + 1,
                     "hand_cost": round(price * 100, 2), "score_lgb": 61.2,
                     "score_linear": 57.9, "why_recommended": {"reason": "isolated HTTP fixture"}})
    return {"rows": rows, "orders": [], "trade_date": "2026-10-09",
            "data_as_of": "2026-10-09T15:00:00+08:00",
            "generated_at": NOW.isoformat(),
            "versions": {"data": "fixture-data-v1", "strategy": "fixture-strategy-v1",
                         "parameters": settings_version(settings)},
            "valid_until": (NOW + timedelta(hours=4)).isoformat(),
            "allow_new_buy": True,
            "selection": {"funnel": [{"name": "fixture", "input": count, "output": count,
                                         "excluded_by_reason": {"none": 0}}]}}


def apply_scene(store: ResultStore, settings: Settings, scene: str, snapshot_id: str) -> None:
    current = NOW
    if scene == "valid":
        audit = {"state": "passed", "ready": True, "data_version": "fixture-data-v1",
                 "parameters_version": settings_version(settings), "checked_at": current.isoformat(),
                 "valid_until": (current + timedelta(minutes=2)).isoformat(), "error": None,
                 "allow_new_buy": True}
        job = {"job_id": "fixture-job", "status": "succeeded", "stage": "complete",
               "data_only": False, "finished_at": current.isoformat()}
    elif scene == "historical":
        audit = {"state": "failed", "ready": False, "data_version": "fixture-data-v1",
                 "parameters_version": settings_version(settings), "checked_at": current.isoformat(),
                 "valid_until": None,
                 "error": {"code": "fixture_audit_failed", "message": "隔离验收：行情核验失败", "retryable": True}}
        job = {"job_id": "fixture-job", "status": "failed", "stage": "audit", "data_only": False,
               "error": "隔离验收：生成失败", "error_info": {"code": "fixture_failed",
               "message": "隔离验收：生成失败", "retryable": True}, "finished_at": current.isoformat()}
    elif scene == "updating":
        audit = {"state": "pending", "ready": False, "data_version": "fixture-data-v1",
                 "parameters_version": settings_version(settings), "checked_at": current.isoformat(),
                 "valid_until": None, "error": None}
        job = {"job_id": "fixture-job", "status": "running", "stage": "train", "data_only": False,
               "started_at": current.isoformat(), "heartbeat_at": current.isoformat()}
    else:
        raise ValueError(f"unknown scene: {scene}")
    store.record_audit(audit)
    store.save_job(job)


def make_app(settings: Settings, store: ResultStore, *, browser: bool = False) -> FastAPI:
    from api.routers import signals as signals_router
    signals_router.load_settings = lambda: settings
    app = FastAPI(lifespan=None)
    app.include_router(signals_router.router)
    if browser:
        if not DIST.joinpath("index.html").is_file():
            raise RuntimeError(f"built frontend missing: {DIST / 'index.html'}")
        app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

        @app.get("/")
        def index(request: Request):
            scene = request.query_params.get("scenario", "historical")
            apply_scene(store, settings, scene, store.read_view()["snapshot"]["snapshot_id"])
            return FileResponse(DIST / "index.html")
    return app


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def serve_thread(app):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off", access_log=False))
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if server.started:
            return server, thread, port
        if not thread.is_alive():
            raise RuntimeError("isolated uvicorn exited before startup")
        time.sleep(.02)
    raise TimeoutError("isolated uvicorn did not start")


def assert_no_heavy_get_work():
    """Poison the known audit/train/fetch entry points during HTTP reads."""
    from api import health_cache
    from data import pipeline
    from scripts import daily_update
    def forbidden(*args, **kwargs):
        raise AssertionError("snapshot GET called audit, signature, training, or fetch")
    health_cache.market_signature = forbidden
    health_cache.health_cache.get = forbidden
    health_cache.health_cache.get_background = forbidden
    daily_update.assess_data_health = forbidden
    daily_update._features_for_all = forbidden
    pipeline.update_all = forbidden


def run_benchmark() -> dict:
    with tempfile.TemporaryDirectory(prefix="signals-http-bench-") as temporary:
        settings = fixture_settings(Path(temporary))
        store = ResultStore(settings.paths.artifact_dir)
        snapshot_id = store.publish(store.stage(make_payload(settings, 5000)))
        app = make_app(settings, store)
        assert_no_heavy_get_work()
        server, thread, port = serve_thread(app)
        base = f"http://127.0.0.1:{port}/api/signals/snapshot"
        scenes = {}
        try:
            for scene in ("valid", "historical", "updating"):
                apply_scene(store, settings, scene, snapshot_id)
                with httpx.Client(timeout=30) as warm:
                    for _ in range(20):
                        response = warm.get(base)
                        response.raise_for_status()
                        if len(response.json().get("rows", [])) != 5000:
                            raise AssertionError(f"unexpected row count in {scene}")

                def worker(request_count):
                    timings, sizes, states = [], [], set()
                    with httpx.Client(timeout=30) as client:
                        for _ in range(request_count):
                            start = time.perf_counter()
                            response = client.get(base)
                            elapsed = (time.perf_counter() - start) * 1000
                            response.raise_for_status()
                            body = response.json()
                            if len(body["rows"]) != 5000:
                                raise AssertionError(f"unexpected row count in {scene}")
                            timings.append(elapsed)
                            sizes.append(len(response.content))
                            states.add(body["result_state"])
                    return timings, sizes, states

                start = time.perf_counter()
                with ThreadPoolExecutor(max_workers=5) as pool:
                    batches = list(pool.map(worker, [200] * 5))
                duration = time.perf_counter() - start
                timings = [value for batch, _, _ in batches for value in batch]
                sizes = [value for _, batch, _ in batches for value in batch]
                states = sorted({value for _, _, batch in batches for value in batch})
                scenes[scene] = {"requests": len(timings), "concurrency": 5, "rows": 5000,
                    "states": states, "response_bytes_mean": round(statistics.mean(sizes)),
                    "duration_seconds": round(duration, 3),
                    "throughput_rps": round(len(timings) / duration, 2),
                    "latency_ms": {"p50": round(percentile(timings, .50), 2),
                                   "p95": round(percentile(timings, .95), 2),
                                   "max": round(max(timings), 2)}}
        finally:
            server.should_exit = True
            thread.join(timeout=5)
        return {"measured_at": datetime.now(timezone.utc).isoformat(),
                "platform": platform.platform(), "python": platform.python_version(),
                "backend": "isolated FastAPI signals router over TCP; app lifespan disabled",
                "data": "temporary ResultStore only; no market DB or external network",
                "no_heavy_get_work_spies": "passed", "scenes": scenes}


def serve_browser() -> None:
    with tempfile.TemporaryDirectory(prefix="signals-browser-") as temporary:
        settings = fixture_settings(Path(temporary))
        store = ResultStore(settings.paths.artifact_dir)
        snapshot_id = store.publish(store.stage(make_payload(settings, 6)))
        apply_scene(store, settings, "historical", snapshot_id)
        app = make_app(settings, store, browser=True)
        print(f"Isolated browser URL: http://127.0.0.1:18888/?scenario=historical", flush=True)
        print("Scenes: ?scenario=historical | ?scenario=updating | ?scenario=failed", flush=True)
        print("Temporary data directory; stop this process to remove it.", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=18888, log_level="warning", lifespan="off")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--benchmark", action="store_true")
    mode.add_argument("--browser-server", action="store_true")
    args = parser.parse_args()
    if args.benchmark:
        print(json.dumps(run_benchmark(), indent=2, ensure_ascii=False))
    else:
        serve_browser()
