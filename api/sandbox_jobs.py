"""Nonblocking, idempotent sandbox tasks; all mutations run on one worker."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Lock
from uuid import uuid4


class SandboxJobRegistry:
    def __init__(self):
        self._lock = Lock()
        self._jobs = {}
        self._requests = {}
        self._active = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='sandbox')

    def start(self, request_id, operation, work, fingerprint=None):
        with self._lock:
            if request_id in self._requests:
                existing = self._jobs[self._requests[request_id]]
                if existing['operation'] != operation or existing['fingerprint'] != fingerprint:
                    raise ValueError('请求编号已用于不同参数')
                return self._summary(existing)
            if self._active:
                raise RuntimeError('已有推演任务正在执行，请等待完成')
            job_id = uuid4().hex
            self._jobs[job_id] = dict(job_id=job_id, request_id=request_id, operation=operation,
                fingerprint=fingerprint, status='queued', progress=dict(message='等待执行',completed=0,total=0),
                result=None, error=None)
            self._requests[request_id] = job_id
            self._active = job_id
            self._pool.submit(self._run, job_id, work)
            return self._summary(self._jobs[job_id])

    @staticmethod
    def _summary(job):
        return deepcopy({k:v for k,v in job.items() if k != 'fingerprint'})

    def current(self):
        with self._lock:
            return self._summary(self._jobs[self._active]) if self._active else None

    def get(self, job_id):
        with self._lock:
            return self._summary(self._jobs[job_id])

    def _run(self, job_id, work):
        def progress(message, completed=0, total=0):
            with self._lock:
                self._jobs[job_id]['progress'] = dict(message=message,completed=completed,total=total)
        with self._lock:
            self._jobs[job_id]['status'] = 'running'
        try:
            result = work(progress)
            status, error = 'succeeded', None
        except Exception as exc:
            result, status, error = None, 'failed', str(exc)
        with self._lock:
            self._jobs[job_id].update(status=status,result=result,error=error)
            self._active = None
            for old in list(self._jobs)[:-32]:
                self._requests.pop(self._jobs[old]['request_id'], None)
                del self._jobs[old]

    def close(self):
        self._pool.shutdown(wait=True)


sandbox_jobs = SandboxJobRegistry()
