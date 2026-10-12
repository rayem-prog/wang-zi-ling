"""Work package A diagnostic only; no application code or real artifacts are mutated.
Run from repo root: .venv/bin/python docs/work-package-a-20261012/baseline_probe.py
"""
import datetime as dt
from dataclasses import replace
import hashlib
import json
import math
import platform
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.health_cache import HealthCache, market_signature
from api.jobs import DailyJobRegistry
from api.routers import signals as route
from config.settings import Settings
from data.health import assess_data_health, recommendation_state
from services.recommendations import load_signals
from strategy.sizing import compute_plan

FIXED = dt.datetime(2026, 10, 12, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
DAY = '2026-10-09'

def timed(fn, n=100):
    values = []
    for _ in range(n):
        start = time.perf_counter()
        fn()
        values.append((time.perf_counter()-start)*1000)
    values.sort()
    return dict(n=n, min_ms=values[0], p50_ms=values[math.ceil(.5*n)-1],
                p95_ms=values[math.ceil(.95*n)-1], max_ms=values[-1])

def main():
    report = dict(commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                  fixed_now=FIXED.isoformat(), platform=platform.platform(), python=sys.version,
                  cpu=subprocess.check_output(['sysctl','-n','machdep.cpu.brand_string'],text=True).strip(),
                  memory_bytes=int(subprocess.check_output(['sysctl','-n','hw.memsize'],text=True)),
                  scenarios={}, timings={}, limitations=['In-process HTTP; no TCP or browser rendering.',
                    'Generation and long audit are controlled substitutes, not real training runtimes.',
                    'Real market database is read-only but not frozen; signature stability is recorded.',
                    'Job registry timestamps use wall clock; fixed time applies to health/session decisions.'])
    with tempfile.TemporaryDirectory(prefix='stockpilot-wpa-') as temp:
        base = Settings()
        settings = replace(base, data=replace(base.data, db_path=str(Path(temp)/'unused.db')),
                           paths=replace(base.paths, artifact_dir=temp))
        rows = pd.DataFrame([dict(code=f'{i:06d}', name=f'Fixture{i}', price=p,
                                 score_blend=90-i, score_a=80, score_b=90)
                             for i,p in enumerate([10,20,50,100,101],1)])
        csv = Path(temp)/'recommendations.csv'
        meta = Path(temp)/'latest_recommendations.meta.json'
        def seed(empty=False):
            rows.iloc[:0 if empty else len(rows)].to_csv(csv,index=False)
            meta.write_text(json.dumps(dict(trade_date=DAY,source='fixture',generated_at=FIXED.isoformat(),coverage=1)))
        report['fixture_sha256'] = hashlib.sha256(rows.to_csv(index=False).encode()).hexdigest()
        clock = [0.]
        entered, release = threading.Event(), threading.Event()
        fail = [False]
        def audit(_):
            entered.set()
            release.wait(5)
            if fail[0]:
                raise RuntimeError('controlled audit failure')
            return dict(ready=True,training_ready=True,trade_date=DAY)
        cache = HealthCache(audit=audit,signature=lambda _: 'fixture-v1',clock=lambda: clock[0])
        app = FastAPI()
        app.include_router(route.router)
        client = TestClient(app)  # No application lifespan, market loops, or daemon startup.
        with patch.object(route,'load_settings',lambda:settings), \
             patch('api.health_cache.health_cache',cache), \
             patch('api.health_cache.exchange_now',lambda:FIXED):
            release.set()
            cache.get(settings,wait=True)
            report['scenarios']['first_start'] = client.get('/api/signals/stats').json()
            seed()
            report['scenarios']['valid'] = dict(rows=len(client.get('/api/signals').json()),stats=client.get('/api/signals/stats').json())
            for _ in range(10): client.get('/api/signals')
            report['timings']['valid_list_http'] = timed(lambda:client.get('/api/signals').raise_for_status())
            report['timings']['valid_stats_http'] = timed(lambda:client.get('/api/signals/stats').raise_for_status())
            clock[0] = 121.
            entered.clear(); release.clear()
            start = time.perf_counter()
            pending = client.get('/api/signals').json()
            assert entered.wait(1)
            report['scenarios']['expired_audit'] = dict(rows=len(pending),stats=client.get('/api/signals/stats').json())
            report['timings']['expired_list_http'] = timed(lambda:client.get('/api/signals').raise_for_status())
            fail[0] = True; release.set()
            cache._pool.shutdown(wait=True)
            report['timings']['controlled_audit_wall_ms'] = (time.perf_counter()-start)*1000
            report['scenarios']['audit_failure'] = dict(health=cache.get(settings,wait=True),files_retained=csv.exists() and meta.exists())
            report['scenarios']['normal_empty'] = None
            seed(empty=True)
            report['scenarios']['normal_empty'] = recommendation_state(settings,dict(ready=True,trade_date=DAY))
            seed(); meta.unlink()
            report['scenarios']['generation_metadata_gap'] = recommendation_state(settings,dict(ready=True,trade_date=DAY))
        seed()
        plan = compute_plan(rows, .02, settings, SimpleNamespace(allow_new_buy=False))
        report['scenarios']['risk_forbids_buy'] = dict(plan_rows=len(plan),weights=list(plan.suggested_weight),
            displayed_rows=len(load_signals(settings,health=dict(ready=True,trade_date=DAY))))
        registry = DailyJobRegistry()
        job_enter, job_release = threading.Event(), threading.Event()
        def work(**_):
            job_enter.set(); job_release.wait(5)
            raise RuntimeError('controlled generation failure')
        with patch('api.jobs.load_settings',lambda:settings), patch('api.jobs.daily_update.run_daily',work):
            start=time.perf_counter(); job=registry.start(True,send_notification=False)
            report['timings']['job_enqueue_ms']=(time.perf_counter()-start)*1000
            assert job_enter.wait(1)
            report['scenarios']['running_job']=registry.get(job['job_id'])
            job_release.set(); registry._pool.shutdown(wait=True)
            report['scenarios']['failed_job']=registry.get(job['job_id'])
        restored = DailyJobRegistry()
        report['scenarios']['registry_restart']=dict(previous_job_found=job['job_id'] in restored._jobs, latest=restored.latest_summary())
        restored._pool.shutdown(wait=True)
        fresh = HealthCache(audit=lambda _:dict(ready=True,trade_date=DAY),signature=lambda _:'fixture',clock=lambda:0.)
        with patch('api.health_cache.exchange_now',lambda:FIXED):
            restart=fresh.get_background(settings)
        fresh._pool.shutdown(wait=True)
        report['scenarios']['health_restart_first_response']=restart
    real = replace(Settings(),data=replace(Settings().data,db_path=str(ROOT/'data/market.db')))
    with sqlite3.connect(f'file:{real.data.db_path}?mode=ro',uri=True) as conn:
        conn.execute('BEGIN')
        report['real_data'] = dict(db_bytes=Path(real.data.db_path).stat().st_size,
            bars=conn.execute('SELECT COUNT(*) FROM bars').fetchone()[0],
            codes=conn.execute('SELECT COUNT(DISTINCT code) FROM bars').fetchone()[0],
            minute_bars=conn.execute('SELECT COUNT(*) FROM minute_bars').fetchone()[0],
            dates=conn.execute('SELECT MIN(date),MAX(date) FROM bars').fetchone())
    start=time.perf_counter(); before=market_signature(real)
    report['timings']['real_signature_ms']=(time.perf_counter()-start)*1000
    start=time.perf_counter(); health=assess_data_health(real,now=FIXED)
    report['timings']['real_audit_ms']=(time.perf_counter()-start)*1000
    report['real_health']={k:health.get(k) for k in ('ready','reason','trade_date','coverage','training_ready','expected_codes','trusted_daily_rows')}
    report['real_signature_stable']=before==market_signature(real)
    real = replace(real, paths=replace(real.paths, artifact_dir=str(ROOT/'artifacts')))
    real_cache = HealthCache(audit=lambda _:health, signature=market_signature, clock=lambda:0.)
    app = FastAPI(); app.include_router(route.router); client = TestClient(app)
    with patch.object(route, 'load_settings', lambda:real), patch('api.health_cache.health_cache',real_cache), patch('api.health_cache.exchange_now',lambda:FIXED):
        real_cache.get(real,wait=True)
        report['real_result_rows']=len(client.get('/api/signals').json())
        for _ in range(10): client.get('/api/signals'); client.get('/api/signals/stats')
        report['timings']['real_cached_list_http']=timed(lambda:client.get('/api/signals').raise_for_status())
        report['timings']['real_cached_stats_http']=timed(lambda:client.get('/api/signals/stats').raise_for_status())
    real_cache._pool.shutdown(wait=True)
    report['real_signature_stable_after_http']=before==market_signature(real)
    report['captured_at']=dt.datetime.now(dt.timezone.utc).isoformat()
    destination=Path(__file__).parent/'baseline-evidence.json'
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__ == '__main__': main()
