"""Single daily job per registry; browser retry cannot run a second generation."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import datetime as dt
from pathlib import Path
from threading import Lock
from uuid import uuid4

from config.settings import load_settings
from scripts import daily_update


class DailyJobRegistry:
    def __init__(self):
        self._lock = Lock()
        self._jobs = {}
        self._active = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='daily-recap')

    def start(self, fast_mode: bool, *, data_only: bool = False, send_notification: bool = True) -> dict:
        settings = load_settings()
        with self._lock:
            if self._active:
                return deepcopy(self._jobs[self._active])
            job_id = uuid4().hex
            self._jobs[job_id] = dict(job_id=job_id, status='queued', result=None, error=None,
                                     created_at=dt.datetime.now(dt.timezone.utc).isoformat(), data_only=data_only)
            self._active = job_id
            # Capture the exact configuration requested, not later edits.
            self._pool.submit(self._run, job_id, settings, fast_mode, data_only, send_notification)
            return deepcopy(self._jobs[job_id])

    def latest_summary(self):
        with self._lock:
            if not self._jobs:
                return None
            job = self._jobs[self._active or next(reversed(self._jobs))]
            return deepcopy({key: job.get(key) for key in
                ('job_id', 'status', 'created_at', 'finished_at', 'error', 'data_only')})

    def get(self, job_id: str) -> dict:
        with self._lock:
            return deepcopy(self._jobs[job_id])

    def _run(self, job_id, settings, fast_mode, data_only=False, send_notification=True):
        with self._lock:
            self._jobs[job_id]['status'] = 'running'
        try:
            if data_only:
                from data import pipeline
                from data.health import assess_data_health
                updated = pipeline.update_all(settings)
                health = assess_data_health(settings, expected_codes=updated['requested_codes'])
                result = dict(update=updated, data_health=health, blocked=not health['ready'],
                              error=health['reason'], last_date=health['trusted_trade_date'])
            else:
                result = daily_update.run_daily(settings=settings, fast_mode=fast_mode,
                                                send_notification=send_notification)
            blocked = bool(result.get('blocked'))
            date = result.get('last_date') if not blocked else None
            report = Path(settings.paths.artifact_dir) / 'reports' / (str(date) + '.md')
            response = dict(success=not blocked, date=date, result=result,
                            content=report.read_text(encoding='utf-8') if date and report.exists() and not data_only else '',
                            reason=result.get('error', '行情未通过检查') if blocked else '')
            status, error = ('failed', response['reason']) if blocked else ('succeeded', None)
        except Exception as exc:
            status, error, response = 'failed', str(exc), None
        with self._lock:
            self._jobs[job_id].update(status=status, error=error, result=response,
                finished_at=dt.datetime.now(dt.timezone.utc).isoformat())
            self._active = None
            for old in list(self._jobs)[:-64]:
                del self._jobs[old]


daily_jobs = DailyJobRegistry()
