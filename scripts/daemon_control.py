"""Project-local daemon ownership shared by CLI, API and application launcher."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


class DaemonAlreadyRunning(RuntimeError):
    pass


def _identity(pid: int) -> str:
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def _metadata(root: Path) -> dict:
    try:
        value = json.loads((root / ".runtime" / "daemon.json").read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _clear_metadata(root: Path, token: str) -> None:
    if _metadata(root).get("token") == token:
        (root / ".runtime" / "daemon.json").unlink(missing_ok=True)


def _locked(root: Path) -> bool:
    path = root / ".runtime" / "daemon.lock"
    if not path.exists():
        return False
    with path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return False


class DaemonLease:
    """Kernel lock survives parent handoff and is released even after a crash.

    Never unlink the lock file: a replacement inode would admit two writers.
    """
    def __init__(self, root=None, token=None, inherited_fd=None, *, publish=True):
        self.root = Path(root or project_root()).resolve()
        self.token = token or uuid.uuid4().hex
        self.inherited_fd = inherited_fd
        self.publish_on_enter = publish
        self.fd = None

    def __enter__(self):
        runtime = self.root / ".runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        self.fd = (self.inherited_fd if self.inherited_fd is not None else
                   os.open(runtime / "daemon.lock", os.O_RDWR | os.O_CREAT, 0o600))
        try:
            # An inherited descriptor shares the parent's locked open-file
            # description; flock preserves the reservation across exec.
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.publish_on_enter:
                self.publish(os.getpid())
            return self
        except BlockingIOError as exc:
            os.close(self.fd)
            self.fd = None
            raise DaemonAlreadyRunning("此项目已有盘中守护进程运行") from exc
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def publish(self, pid):
        identity = _identity(pid)
        if not identity:
            raise RuntimeError("无法核实守护进程身份")
        value = {"pid": pid, "root": str(self.root), "token": self.token,
                 "identity": identity}
        fd, temporary = tempfile.mkstemp(prefix="daemon-", suffix=".json", dir=self.root / ".runtime")
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(value, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.root / ".runtime" / "daemon.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def detach(self):
        """Close the parent's descriptor without unlocking the child's lease."""
        os.close(self.fd)
        self.fd = None

    def __exit__(self, *_):
        if self.fd is not None:
            try:
                _clear_metadata(self.root, self.token)
            finally:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                os.close(self.fd)
                self.fd = None


def _verified(root: Path, metadata: dict) -> bool:
    pid = metadata.get("pid")
    if (not isinstance(pid, int) or pid <= 1 or pid == os.getpid() or
            metadata.get("root") != str(root) or not metadata.get("token")):
        return False
    identity = _identity(pid)
    return bool(identity and identity == metadata.get("identity") and
                "scripts.intraday_daemon" in identity)


def daemon_state(root=None) -> dict:
    root = Path(root or project_root()).resolve()
    metadata = _metadata(root)
    running = _locked(root)
    return {"running": running, "verified": running and _verified(root, metadata),
            "pid": metadata.get("pid") if running else None,
            "token": metadata.get("token") if running else None}


def start_daemon(root=None) -> dict:
    root = Path(root or project_root()).resolve()
    lease = DaemonLease(root, publish=False)
    try:
        lease.__enter__()
    except DaemonAlreadyRunning:
        state = daemon_state(root)
        return {"status": "already_running" if state["verified"] else "unmanaged_running",
                "pid": state["pid"]}
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "scripts.intraday_daemon", "--project-root", str(root),
             "--lease-token", lease.token, "--lease-fd", str(lease.fd)],
            cwd=root, pass_fds=(lease.fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # The child publishes after exec, avoiding a transient parent-observed
        # identity that still names the executable before its command changes.
        deadline = time.monotonic() + 3
        while True:
            metadata = _metadata(root)
            if metadata.get("token") == lease.token and _verified(root, metadata):
                break
            if process.poll() is not None:
                raise RuntimeError("守护进程启动失败，尚未取得项目所有权")
            if time.monotonic() >= deadline:
                raise RuntimeError("守护进程启动超时，尚未确认项目所有权")
            time.sleep(.02)
        lease.detach()
        return {"status": "started", "pid": process.pid, "token": lease.token}
    except BaseException:
        if process is not None and process.poll() is None:
            # This exact Popen child was just created by this operation.
            process.terminate()
            process.wait(timeout=3)
        raise
    finally:
        lease.__exit__(None, None, None)


def stop_daemon(root=None, timeout=3.0, *, expected_token=None) -> dict:
    root = Path(root or project_root()).resolve()
    metadata = _metadata(root)
    if not _locked(root):
        return {"status": "not_running", "killed_pids": []}
    if expected_token is not None and metadata.get("token") != expected_token:
        return {"status": "ownership_changed", "killed_pids": []}
    if not _verified(root, metadata):
        return {"status": "identity_mismatch", "killed_pids": [],
                "message": "无法核实此项目守护进程身份，未发送信号"}
    pid = metadata["pid"]
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"status": "not_running", "killed_pids": []}
    deadline = time.monotonic() + timeout
    while _locked(root) and time.monotonic() < deadline:
        time.sleep(.05)
    if _locked(root):
        current = _metadata(root)
        if current.get("token") != metadata["token"] or not _verified(root, current):
            return {"status": "identity_mismatch", "killed_pids": [pid]}
        os.kill(pid, signal.SIGKILL)
        deadline = time.monotonic() + 1
        while _locked(root) and time.monotonic() < deadline:
            time.sleep(.05)
    if _locked(root):
        return {"status": "stop_failed", "killed_pids": [pid]}
    _clear_metadata(root, metadata["token"])
    return {"status": "stopped", "killed_pids": [pid]}
