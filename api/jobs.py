"""Durable daily jobs, scoped to a result store and protected by a process lease."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import datetime as dt
import errno
try:
    import fcntl
except ImportError:
    fcntl = None
try:
    import msvcrt
except ImportError:
    msvcrt = None
import inspect
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Event, Lock, Thread
from uuid import uuid4

from config.settings import load_settings
from scripts import daily_update
from services.result_store import ResultStore

ACTIVE = {'queued', 'running'}
WINDOWS = sys.platform == 'win32'


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _windows_process_identity(pid):
    """Read-only process query; never use os.kill on Windows for liveness."""
    import ctypes
    class FileTime(ctypes.Structure):
        _fields_ = [('low', ctypes.c_uint32), ('high', ctypes.c_uint32)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(FileTime)]*4
    kernel.GetProcessTimes.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return ''
    try:
        created, exited, kernel_time, user_time = FileTime(), FileTime(), FileTime(), FileTime()
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return ''
        return f'windows:{pid}:{(created.high << 32) | created.low}'
    finally:
        kernel.CloseHandle(handle)


def process_identity(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return ''
    if WINDOWS:
        return _windows_process_identity(pid)
    try:
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart=', '-o', 'command='],
                                capture_output=True, text=True, check=False)
    except OSError:
        return ''
    return result.stdout.strip() if result.returncode == 0 else ''


class JobConflict(RuntimeError):
    """An active job has a different data-only/generation intention."""


class GenerationLease:
    """Never unlink this file: flock protects its inode across process crashes."""
    def __init__(self, artifact_dir):
        self.path = (Path(artifact_dir)/'generation.lock').resolve()
        self.token = uuid4().hex
        self.fd = None

    def acquire(self):
        if fcntl is None and msvcrt is None:
            raise RuntimeError('当前平台没有可用的跨进程生成锁')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                if os.fstat(self.fd).st_size == 0:
                    os.write(self.fd, b'0')
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            os.close(self.fd)
            self.fd = None
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise

    def release(self):
        if self.fd is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(self.fd, fcntl.LOCK_UN)
                elif msvcrt is not None:
                    os.lseek(self.fd, 0, os.SEEK_SET)
                    msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(self.fd)
                self.fd = None


def _all_jobs(store):
    if not store.path.exists():
        return []
    with sqlite3.connect(f'{store.path.as_uri()}?mode=ro', uri=True, timeout=.1) as connection:
        return [json.loads(row[0]) for row in connection.execute('SELECT projection_json FROM result_jobs ORDER BY updated_seq')]


def new_generation_job(lease, *, fast_mode, data_only=False, status='queued'):
    job_id = uuid4().hex
    created = utc_now()
    return dict(job_id=job_id, status=status, stage='queued' if status=='queued' else 'audit',
                result=None, error=None, error_info=None, created_at=created,
                started_at=created if status=='running' else None, finished_at=None,
                heartbeat_at=created, data_only=data_only, fast_mode=fast_mode,
                owner_pid=os.getpid(), owner_identity=process_identity(os.getpid()), lease_token=lease.token)


def patch_job(store, job_id, **fields):
    """Atomic partial update cannot overwrite a concurrently committed publish.

    ResultStore.save_job is a full-projection replacement. Reading and then
    replacing outside a transaction would let a stale heartbeat restore running.
    """
    with store._write() as connection:
        row = connection.execute('SELECT projection_json FROM result_jobs WHERE job_id=?', (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        job = json.loads(row[0])
        if job.get('status')=='succeeded' and fields.get('status') in ('failed','running','interrupted'):
            return job
        job.update(fields, heartbeat_at=utc_now())
        revision = store._revision(connection)
        connection.execute('UPDATE result_jobs SET projection_json=?,updated_seq=? WHERE job_id=?',
            (json.dumps(job, ensure_ascii=False, sort_keys=True, allow_nan=False), revision, job_id))
        return job


def job_response(result, *, content=''):
    blocked = bool(result.get('blocked'))
    return dict(success=not blocked, date=result.get('last_date') if not blocked else None,
                result=result, content=content, reason=(result.get('error') or '行情未通过检查') if blocked else '')


class DailyJobRegistry:
    def __init__(self):
        self._lock = Lock()
        self._jobs = {}
        self._active = {}
        self._paths = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='daily-recap')

    def _save(self, store, job):
        store.save_job(job)
        key = (str(store.path), job['job_id'])
        self._jobs[key] = deepcopy(job)
        for old in list(self._jobs)[:-64]:
            if old[1] not in self._paths:
                del self._jobs[old]

    def _recover_unlocked(self, store):
        # Caller owns flock. A live pid+start identity is never interrupted even
        # if another registry exists in the same process or a lease was handed off.
        for job in _all_jobs(store):
            if job.get('status') not in ACTIVE:
                continue
            identity = process_identity(job.get('owner_pid'))
            if identity and identity == job.get('owner_identity'):
                continue
            message = '服务中断，任务未完成；请重新生成'
            job.update(status='interrupted', finished_at=utc_now(), heartbeat_at=utc_now(),
                       error=message, error_info=dict(code='owner_interrupted', message=message, retryable=True))
            self._save(store, job)

    def recover(self, settings=None):
        settings = settings or load_settings()
        store = ResultStore(settings.paths.artifact_dir)
        if not store.path.exists():
            return
        lease = GenerationLease(store.path.parent)
        if lease.acquire():
            try:
                with self._lock:
                    self._recover_unlocked(store)
            finally:
                lease.release()

    @staticmethod
    def _reuse(job, data_only):
        if job and job.get('status') in ACTIVE and bool(job.get('data_only')) == data_only:
            return deepcopy(job)
        raise JobConflict('已有任务正在执行不同更新操作，请等待任务完成')

    def start(self, fast_mode: bool, *, data_only=False, send_notification=True):
        settings = load_settings()
        store = ResultStore(settings.paths.artifact_dir)
        path = str(store.path)
        with self._lock:
            if path in self._active:
                return self._reuse(store.get_job(self._active[path]), data_only)
            lease = GenerationLease(store.path.parent)
            if not lease.acquire():
                return self._reuse(store.latest_job(), data_only)
            try:
                self._recover_unlocked(store)
                previous = store.latest_job()
                if previous and previous.get('status') in ACTIVE:
                    lease.release()
                    return self._reuse(previous, data_only)
                job = new_generation_job(lease, fast_mode=fast_mode, data_only=data_only)
                job_id = job['job_id']
                self._save(store, job)
                self._active[path] = job_id
                self._paths[job_id] = path
                self._pool.submit(self._run, job_id, settings, fast_mode, data_only, send_notification, lease)
                return deepcopy(job)
            except BaseException:
                lease.release()
                raise

    def latest_summary(self):
        job = ResultStore(load_settings().paths.artifact_dir).latest_job()
        if not job:
            return None
        value={key:job.get(key) for key in ('job_id','status','stage','created_at','started_at',
            'finished_at','heartbeat_at','error','error_info','data_only')}
        value['components']=((job.get('result') or {}).get('result') or {}).get('components')
        return deepcopy(value)

    def get(self, job_id):
        # Current settings select the requested store. In-memory jobs from a
        # previous working directory are never returned for another store.
        return ResultStore(load_settings().paths.artifact_dir).get_job(job_id)

    def _update(self, store, job_id, **fields):
        with self._lock:
            job = patch_job(store, job_id, **fields)
            self._jobs[(str(store.path), job_id)] = deepcopy(job)
            return job

    def _run(self, job_id, settings, fast_mode, data_only, send_notification, lease):
        store = ResultStore(Path(self._paths[job_id]).parent)
        stopped = Event()
        def progress(stage):
            self._update(store, job_id, stage=stage)
        def heartbeat():
            while not stopped.wait(10):
                self._update(store, job_id)
        try:
            self._update(store, job_id, status='running', stage='audit', started_at=utc_now())
            pulse = Thread(target=heartbeat, daemon=True, name='generation-heartbeat')
            pulse.start()
            if data_only:
                from data import pipeline
                from data.health import assess_data_health
                from services.result_pipeline import record_health_projection
                record_health_projection(settings, dict(ready=False, health_cache_stale=True,
                    reason='正在更新行情，当前操作需等待核验完成'), 'pending')
                progress('fetch')
                if 'progress' in inspect.signature(pipeline.update_all).parameters:
                    updated = pipeline.update_all(settings,progress=progress)
                else:
                    updated = pipeline.update_all(settings)
                progress('audit')
                from api.health_cache import market_signature
                from api.health_cache import minute_signature
                from api.health_cache import HealthCache
                from data.timeliness import exchange_now
                audit_started=exchange_now()
                time_key=HealthCache._time_key(settings,audit_started)
                before=market_signature(settings)
                minute_before=minute_signature(settings)
                health = assess_data_health(settings,now=audit_started,expected_codes=updated['requested_codes'])
                after=market_signature(settings)
                audit_finished=exchange_now()
                if minute_before!=minute_signature(settings) and not settings.engine.use_intraday_factors:
                    health.update(minute_stale=True,minute_reason='分钟数据核验期间变化，需单独重新核验')
                if before!=after or time_key!=HealthCache._time_key(settings,audit_finished):
                    health.update(ready=False,training_ready=False,error_code='audit_version_changed',
                                  reason='核验期间行情版本或有效交易时点变化，稍后重新核验')
                health.update(health_checked_at=audit_finished.isoformat(),health_as_of=audit_started.isoformat())
                record_health_projection(settings, health, 'passed' if health['ready'] else 'failed',signature=after)
                from services.collection_status import collection_components
                components,errors=collection_components(updated,health)
                result = dict(update=updated, data_health=health, blocked=not health['ready'],
                              error=('；'.join(f"{e.get('code',e.get('phase'))}: {e['message'][:200]}" for e in errors[:3])
                                     + (f'（共{len(errors)}项，详见分项状态）' if len(errors)>3 else '')) or health['reason'],
                              last_date=health.get('trusted_trade_date'),components=components,
                              collection_success=not errors,collection_errors=errors)
            else:
                kwargs = dict(settings=settings, fast_mode=fast_mode, send_notification=send_notification)
                signature = inspect.signature(daily_update.run_daily)
                variadic = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
                for key, value in (('progress', progress), ('job_id', job_id), ('generation_lease', lease)):
                    if variadic or key in signature.parameters:
                        kwargs[key] = value
                result = daily_update.run_daily(**kwargs)
            blocked = (not result['collection_success'] if data_only and 'collection_success' in result
                       else bool(result.get('blocked')))
            date = result.get('last_date') if not blocked else None
            report = Path(settings.paths.artifact_dir)/'reports'/(str(date)+'.md')
            warnings = list(result.get('warnings', []))
            content = ''
            try:
                if date and report.exists() and not data_only:
                    content = report.read_text(encoding='utf-8')
            except OSError as exc:
                warnings.append(f'读取复盘报告失败: {exc}')
            response = dict(success=not blocked, date=date, result=result, content=content,
                            reason=result.get('error') or '行情未通过检查' if blocked else '')
            status, error = ('failed', response['reason']) if blocked else ('succeeded', None)
            failed_stage=store.get_job(job_id).get('stage')
            if data_only and result.get('collection_errors'):
                phase=result['collection_errors'][0].get('phase')
                failed_stage={'minute':'fetch_minute','raw_daily':'fetch_raw_daily','spot':'fetch_spot'}.get(phase,'fetch_daily')
            self._update(store, job_id, status=status, stage='complete' if not blocked else failed_stage,
                         error=error, error_info=dict(code='generation_failed', message=error, retryable=True) if error else None,
                         result=response, warnings=warnings, finished_at=utc_now())
        except Exception as exc:
            job = store.get_job(job_id)
            if job.get('status') == 'succeeded':
                self._update(store, job_id, warnings=list(job.get('warnings', []))+[f'发布后处理失败: {exc}'])
            else:
                self._update(store, job_id, status='failed', error=str(exc),
                             error_info=dict(code='generation_failed', message=str(exc), retryable=True), finished_at=utc_now())
                if data_only:
                    from services.result_pipeline import record_health_projection
                    previous=store.read_view().get('audit') or {}
                    failed_health=dict(previous.get('health') or {},ready=False,training_ready=False,
                                       error_code='collection_failed',reason=f"采集失败（{job.get('stage')}）: {exc}")
                    record_health_projection(settings,failed_health,'failed')
        finally:
            stopped.set()
            if 'pulse' in locals():
                pulse.join(timeout=1)
            with self._lock:
                lease.release()
                self._active.pop(str(store.path), None)
                self._paths.pop(job_id, None)


daily_jobs = DailyJobRegistry()
