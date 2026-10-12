"""Durable generation, process lease recovery and artifact-directory isolation."""
import datetime as dt
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import pytest
from config.settings import Settings
from services.result_store import ResultStore
from api.jobs import DailyJobRegistry


@pytest.fixture
def job_settings(tmp_path, monkeypatch):
    from api import jobs
    settings=replace(Settings(), paths=replace(Settings().paths, artifact_dir=str(tmp_path/'results')))
    monkeypatch.setattr(jobs, 'load_settings', lambda: settings)
    return settings


def terminal(registry, job_id):
    for _ in range(200):
        result=registry.get(job_id)
        if result['status'] in ('succeeded','failed','interrupted'):
            return result
        time.sleep(.01)
    raise AssertionError('job did not finish')


@pytest.mark.parametrize('blocked', [False, True])
def test_terminal_job_survives_registry_restart(job_settings, monkeypatch, blocked):
    from api import jobs
    monkeypatch.setattr(jobs.daily_update, 'run_daily', lambda **kwargs: dict(blocked=blocked,error='controlled' if blocked else None,last_date='2026-10-09'))
    registry=DailyJobRegistry()
    job=registry.start(True,send_notification=False)
    result=terminal(registry,job['job_id'])
    other=DailyJobRegistry()
    persisted=other.get(job['job_id'])
    assert persisted==result
    assert persisted['status']==('failed' if blocked else 'succeeded')
    assert persisted['stage']=='complete' if not blocked else persisted['error_info']['message']=='controlled'


def test_live_owner_not_interrupted_and_deduplicated_across_registries(job_settings, monkeypatch):
    from api import jobs
    entered=threading.Event(); release=threading.Event(); calls=[]
    def work(**kwargs):
        calls.append(1);entered.set();release.wait(3)
        kwargs['progress']('features')
        return {'last_date':'2026-10-09'}
    monkeypatch.setattr(jobs.daily_update,'run_daily',work)
    first, second=DailyJobRegistry(),DailyJobRegistry()
    try:
        job=first.start(True,send_notification=False)
        assert entered.wait(1)
        second.recover(job_settings)
        assert second.get(job['job_id'])['status']=='running'
        retry=second.start(True,send_notification=False)
        assert retry['job_id']==job['job_id'] and len(calls)==1
        with pytest.raises(jobs.JobConflict):
            second.start(False,data_only=True)
    finally:
        release.set()
        terminal(first,job['job_id'])


def test_registry_does_not_mix_artifact_directories(job_settings, tmp_path, monkeypatch):
    from api import jobs
    monkeypatch.setattr(jobs.daily_update,'run_daily',lambda **kwargs: {'last_date':'2026-10-09'})
    registry=DailyJobRegistry();job=registry.start(True,send_notification=False);terminal(registry,job['job_id'])
    other=replace(job_settings,paths=replace(job_settings.paths,artifact_dir=str(tmp_path/'other')))
    monkeypatch.setattr(jobs,'load_settings',lambda: other)
    assert registry.latest_summary() is None
    with pytest.raises(KeyError):
        registry.get(job['job_id'])


@pytest.mark.parametrize('status', ['queued','running'])
def test_killed_child_job_recovers_interrupted_in_new_process(job_settings, status):
    root=Path(__file__).resolve().parents[1]
    source="""
import json, os, sys, time
from api.jobs import GenerationLease, process_identity
from services.result_store import ResultStore
lease=GenerationLease(sys.argv[1]);assert lease.acquire()
ResultStore(sys.argv[1]).save_job(dict(job_id='child-job', status=sys.argv[2], stage='features', owner_pid=os.getpid(), owner_identity=process_identity(os.getpid()), lease_token=lease.token))
print('persisted',flush=True)
time.sleep(60)
"""
    child=subprocess.Popen([sys.executable,'-c',source,job_settings.paths.artifact_dir,status],cwd=root,stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='persisted'
        child.kill();child.wait(timeout=5)
        recovery="""
import sys
from dataclasses import replace
from config.settings import Settings
from api.jobs import DailyJobRegistry
from services.result_store import ResultStore
settings=replace(Settings(),paths=replace(Settings().paths,artifact_dir=sys.argv[1]))
DailyJobRegistry().recover(settings)
job=ResultStore(sys.argv[1]).get_job('child-job')
assert job['status']=='interrupted',job
assert job['error_info']['code']=='owner_interrupted'
"""
        subprocess.run([sys.executable,'-c',recovery,job_settings.paths.artifact_dir],cwd=root,check=True)
    finally:
        if child.poll() is None:
            child.kill();child.wait(timeout=5)


def test_windows_byte_range_lease_reserves_nonempty_file_and_handles_contention(tmp_path, monkeypatch):
    import errno
    from api import jobs
    class WindowsLock:
        LK_NBLCK=1
        LK_UNLCK=0
        held=False
        def locking(self, fd, operation, size):
            assert os.lseek(fd,0,os.SEEK_CUR)==0
            assert os.fstat(fd).st_size>=1 and size==1
            if operation==self.LK_NBLCK:
                if self.held:
                    raise OSError(errno.EACCES,'busy')
                self.held=True
            else:
                self.held=False
    windows=WindowsLock()
    monkeypatch.setattr(jobs,'fcntl',None)
    monkeypatch.setattr(jobs,'msvcrt',windows,raising=False)
    first,second=jobs.GenerationLease(tmp_path),jobs.GenerationLease(tmp_path)
    assert first.acquire()
    try:
        assert not second.acquire()
    finally:
        first.release()
    assert second.acquire()
    second.release()


def test_missing_lock_backend_does_not_break_import_and_refuses_generation(tmp_path, monkeypatch):
    from api import jobs
    monkeypatch.setattr(jobs,'fcntl',None)
    monkeypatch.setattr(jobs,'msvcrt',None,raising=False)
    with pytest.raises(RuntimeError,match='锁|lock'):
        jobs.GenerationLease(tmp_path).acquire()


def test_windows_process_identity_uses_safe_creation_time_handles(monkeypatch):
    import ctypes
    from api import jobs
    closed=[]
    class NativeFunction:
        def __init__(self,callback):
            self.callback=callback
        def __call__(self,*args):
            return self.callback(*args)
    class Kernel:
        OpenProcess=NativeFunction(lambda *args:123)
        CloseHandle=NativeFunction(lambda handle:closed.append(handle) or 1)
        def __init__(self):
            def times(handle,created,exited,kernel,user):
                created._obj.low=100
                created._obj.high=2
                return 1
            self.GetProcessTimes=NativeFunction(times)
    monkeypatch.setattr(jobs,'WINDOWS',True,raising=False)
    monkeypatch.setattr(ctypes,'WinDLL',lambda *args,**kwargs:Kernel(),raising=False)
    monkeypatch.setattr(jobs.subprocess,'run',lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError('Windows must not invoke ps')))
    assert jobs.process_identity(999)=='windows:999:'+str((2<<32)+100)
    assert closed==[123]
