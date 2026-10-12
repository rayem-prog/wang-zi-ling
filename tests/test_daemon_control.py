import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from scripts import app_runner


def _helper_project(root):
    """A daemon CLI that only leases and sleeps; no market or notifier code."""
    scripts = root / "scripts"
    scripts.mkdir()
    source_scripts = Path(__file__).resolve().parents[1] / "scripts"
    (scripts / "__init__.py").write_text(f"__path__.append({str(source_scripts)!r})\n")
    (scripts / "intraday_daemon.py").write_text('''
import argparse, signal, time
from scripts.daemon_control import DaemonLease
parser = argparse.ArgumentParser()
parser.add_argument('--project-root')
parser.add_argument('--lease-token')
parser.add_argument('--lease-fd', type=int)
args = parser.parse_args()
def stop(*_):
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
with DaemonLease(args.project_root, args.lease_token, args.lease_fd):
    while True:
        time.sleep(.1)
''')


def test_occupied_port_raises_without_signalling_any_process(monkeypatch):
    # Mock the unsafe legacy discovery path while exercising an actual socket.
    killed = []
    monkeypatch.setattr(app_runner.os, "kill", lambda *args: killed.append(args))
    monkeypatch.setattr(app_runner.subprocess, "run", lambda *a, **k: type("Result", (), {"stdout": ""})())
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(RuntimeError, match=str(port)):
            app_runner.ensure_port_available(port)
    assert killed == []


def test_manual_lease_blocks_managed_start_and_duplicate_lease(tmp_path, monkeypatch):
    from scripts import daemon_control
    starts = []
    original_popen = subprocess.Popen

    def guarded_popen(command, *args, **kwargs):
        if command[0] == "ps":
            return original_popen(command, *args, **kwargs)
        starts.append(command)
        raise AssertionError("a held lease must prevent starting a daemon")

    monkeypatch.setattr(daemon_control.subprocess, "Popen", guarded_popen)
    with daemon_control.DaemonLease(tmp_path):
        result = daemon_control.start_daemon(tmp_path)
        assert result["status"] in ("already_running", "unmanaged_running")
        with pytest.raises(daemon_control.DaemonAlreadyRunning):
            with daemon_control.DaemonLease(tmp_path):
                pass
    assert starts == []


def test_lease_is_released_when_operation_raises(tmp_path):
    from scripts import daemon_control
    with pytest.raises(RuntimeError, match="controlled"):
        with daemon_control.DaemonLease(tmp_path):
            raise RuntimeError("controlled")
    with daemon_control.DaemonLease(tmp_path):
        assert daemon_control.daemon_state(tmp_path)["running"] is True


def test_independent_managers_share_one_child_and_can_stop_it(tmp_path):
    from scripts import daemon_control
    _helper_project(tmp_path)
    first = daemon_control.start_daemon(tmp_path)
    try:
        assert first["status"] == "started"
        second = daemon_control.start_daemon(tmp_path)
        assert second["status"] == "already_running"
        assert second["pid"] == first["pid"]
        result = daemon_control.stop_daemon(tmp_path)
        assert result["status"] == "stopped"
        assert result["killed_pids"] == [first["pid"]]
        assert daemon_control.daemon_state(tmp_path)["running"] is False
    finally:
        daemon_control.stop_daemon(tmp_path)


def test_foreign_pid_metadata_never_authorizes_a_signal(tmp_path, monkeypatch):
    from scripts import daemon_control
    signals = []
    with daemon_control.DaemonLease(tmp_path):
        metadata_path = tmp_path / ".runtime" / "daemon.json"
        metadata = json.loads(metadata_path.read_text())
        # Even a live PID is insufficient: it must match the creation/command
        # identity and project lease token, rather than a heartbeat PID.
        metadata["pid"] = os.getppid()
        metadata_path.write_text(json.dumps(metadata))
        monkeypatch.setattr(daemon_control.os, "kill", lambda *args: signals.append(args))
        assert daemon_control.stop_daemon(tmp_path)["status"] == "identity_mismatch"
    assert signals == []


def test_kernel_releases_lease_after_abrupt_child_exit(tmp_path):
    from scripts import daemon_control
    source = str(Path(__file__).resolve().parents[1])
    code = ("import os, sys; sys.path.insert(0, sys.argv[1]); "
            "from scripts.daemon_control import DaemonLease; "
            "lease = DaemonLease(sys.argv[2]); lease.__enter__(); os._exit(0)")
    subprocess.run([sys.executable, "-c", code, source, str(tmp_path)], check=True)
    with daemon_control.DaemonLease(tmp_path):
        assert daemon_control.daemon_state(tmp_path)["running"] is True


def test_api_uses_same_project_lease_as_independent_manager(tmp_path, monkeypatch):
    from api.routers import settings as api_settings
    from scripts import daemon_control
    _helper_project(tmp_path)
    monkeypatch.setattr(daemon_control, 'project_root', lambda: tmp_path)
    original_popen = subprocess.Popen
    def safe_popen(command, *a, **k):
        if command[0] == 'ps' or ('--project-root' in command and str(tmp_path.resolve()) in command):
            return original_popen(command, *a, **k)
        raise AssertionError('production daemon creation forbidden')
    monkeypatch.setattr(subprocess, 'Popen', safe_popen)
    first = daemon_control.start_daemon(tmp_path)
    try:
        assert api_settings.start_daemon()['status'] == 'already_running'
        stopped = api_settings.stop_daemon()
        assert stopped['killed_pids'] == [first['pid']]
        assert not daemon_control.daemon_state(tmp_path)['running']
    finally:
        daemon_control.stop_daemon(tmp_path)


def test_launcher_preserves_preexisting_daemon(monkeypatch):
    from types import SimpleNamespace
    from scripts import daemon_control
    starts, stops = [], []
    monkeypatch.setattr(app_runner, 'parse_args', lambda: SimpleNamespace(host='127.0.0.1', port=8888, no_browser=True, no_daemon=False))
    monkeypatch.setattr(app_runner, 'ensure_port_available', lambda _: None)
    monkeypatch.setattr(app_runner.os, 'chdir', lambda _: None)
    monkeypatch.setattr(app_runner.signal, 'signal', lambda *a: None)
    monkeypatch.setattr(app_runner.uvicorn, 'run', lambda *a, **k: None)
    monkeypatch.setattr(daemon_control, 'start_daemon', lambda root: starts.append(root) or {'status': 'already_running', 'pid': 4242})
    monkeypatch.setattr(daemon_control, 'stop_daemon', lambda *a, **k: stops.append((a,k)) or {'status': 'stopped'})
    monkeypatch.setattr(app_runner.subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(AssertionError('global kill forbidden')))
    monkeypatch.setattr(app_runner.subprocess, 'Popen', lambda *a, **k: (_ for _ in ()).throw(AssertionError('unmanaged start forbidden')))
    with pytest.raises(SystemExit):
        app_runner.main()
    assert len(starts) == 1
    assert stops == []


def test_launcher_only_stops_lease_it_created(monkeypatch):
    from types import SimpleNamespace
    from scripts import daemon_control
    stops = []
    monkeypatch.setattr(app_runner, 'parse_args', lambda: SimpleNamespace(host='127.0.0.1', port=8888, no_browser=True, no_daemon=False))
    monkeypatch.setattr(app_runner, 'ensure_port_available', lambda _: None)
    monkeypatch.setattr(app_runner.os, 'chdir', lambda _: None)
    monkeypatch.setattr(app_runner.signal, 'signal', lambda *a: None)
    monkeypatch.setattr(app_runner.uvicorn, 'run', lambda *a, **k: None)
    monkeypatch.setattr(daemon_control, 'start_daemon', lambda root: {'status': 'started', 'pid': 4242, 'token': 'owned'})
    monkeypatch.setattr(daemon_control, 'stop_daemon', lambda *a, **k: stops.append((a,k)) or {'status': 'stopped'})
    monkeypatch.setattr(app_runner.subprocess, 'run', lambda *a, **k: None)
    monkeypatch.setattr(app_runner.subprocess, 'Popen', lambda *a, **k: (_ for _ in ()).throw(AssertionError('unmanaged start forbidden')))
    with pytest.raises(SystemExit):
        app_runner.main()
    assert len(stops) == 1
    assert stops[0][1]['expected_token'] == 'owned'


def test_wrong_lease_token_never_authorizes_stop(tmp_path, monkeypatch):
    from scripts import daemon_control
    signals = []
    with daemon_control.DaemonLease(tmp_path):
        monkeypatch.setattr(daemon_control.os, 'kill', lambda *a: signals.append(a))
        result = daemon_control.stop_daemon(tmp_path, expected_token='old-lease')
        assert result['status'] == 'ownership_changed'
    assert signals == []


def test_run_script_leaves_an_occupied_port_process_alive(tmp_path):
    import shutil
    # Run the real shell launcher in a disposable project and replace only its
    # final Python exec; the listener belongs to a harmless child helper.
    source = Path(__file__).resolve().parents[1] / 'run.sh'
    shutil.copy(source, tmp_path / 'run.sh')
    (tmp_path / 'frontend' / 'dist').mkdir(parents=True)
    (tmp_path / '.venv' / 'bin').mkdir(parents=True)
    fake_python = tmp_path / '.venv' / 'bin' / 'python'
    fake_python.write_text('#!/bin/sh\nexit 0\n')
    fake_python.chmod(0o755)
    child = subprocess.Popen([sys.executable, '-u', '-c',
        "import socket,time; s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); print(s.getsockname()[1],flush=True); time.sleep(10)"], stdout=subprocess.PIPE, text=True)
    try:
        port = child.stdout.readline().strip()
        # Supply a deterministic lsof fixture to exercise the legacy branch
        # without allowing it to target a real service on the fixed port.
        fake_lsof = tmp_path / 'lsof'
        fake_lsof.write_text(f'#!/bin/sh\necho {child.pid}\n')
        fake_lsof.chmod(0o755)
        result = subprocess.run(['bash', str(tmp_path / 'run.sh')], env={**os.environ, 'PATH': f'{tmp_path}:' + os.environ['PATH']}, capture_output=True, text=True)
        assert result.returncode == 0
        assert port.isdigit()
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=3)
