"""StockPilot v2 综合 App 总控调度器：统一管理 FastAPI 服务、盘中守护进程与浏览器唤起。"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import uvicorn
import requests

from scripts import daemon_control


def parse_args():
    parser = argparse.ArgumentParser(description="StockPilot v2 统一集成应用启动器")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="FastAPI 监听地址 (设为 0.0.0.0 可供局域网或其他设备访问)")
    parser.add_argument("--port", type=int, default=8888, help="FastAPI 监听端口 (默认 8888)")
    parser.add_argument("--no-browser", action="store_true", help="不自动拉起浏览器")
    parser.add_argument("--no-daemon", action="store_true", help="不自动拉起盘中守护进程")
    return parser.parse_args()


def ensure_port_available(port: int):
    """Report an occupied port without sending signals to unrelated processes."""
    with socket.socket() as probe:
        try:
            probe.bind(("0.0.0.0", port))
        except OSError as exc:
            raise RuntimeError(f"端口 {port} 已被占用，请关闭占用服务或使用 --port 选择其他端口") from exc


def existing_app_url(port: int, root_dir: Path) -> str | None:
    """确认监听进程属于同一目录，且 HTTP 服务确为 StockPilot，才复用。"""
    url = f"http://127.0.0.1:{port}"
    try:
        listeners = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=2, check=True,
        ).stdout.split()
        if not listeners or not all(pid.isdigit() for pid in listeners):
            return None
        for pid in set(listeners):
            cwd = subprocess.run(
                ["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                capture_output=True, text=True, timeout=2, check=True,
            ).stdout.splitlines()
            if "n" + str(root_dir.resolve()) not in cwd:
                return None
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(url + "/openapi.json", timeout=3)
            response.raise_for_status()
            schema = response.json()
        if (schema.get("info", {}).get("title") == "StockPilot v2 API"
                and "/api/status" in schema.get("paths", {})):
            return url
    except (OSError, subprocess.SubprocessError, requests.RequestException, ValueError):
        pass
    return None


def main():
    args = parse_args()
    root_dir = Path(__file__).resolve().parent.parent
    os.chdir(root_dir)

    print("=" * 60)
    print("🚀 正在启动 StockPilot v2 量化交易终端...")
    print(f"📁 工作根目录: {root_dir}")
    print("=" * 60)

    try:
        ensure_port_available(args.port)
    except RuntimeError as exc:
        existing_url = existing_app_url(args.port, root_dir)
        if existing_url:
            print(f"✓ StockPilot 已在运行，打开现有页面：{existing_url}")
            if not args.no_browser:
                webbrowser.open(existing_url)
            return 0
        print(f"❌ {exc}")
        return 2

    daemon_token = None

    def cleanup(signum=None, frame=None):
        print("\n🛑 收到退出信号，正在安全停止后台子服务...")
        if daemon_token:
            result = daemon_control.stop_daemon(root_dir, expected_token=daemon_token)
            print(f"  ✓ 盘中守护进程: {result['status']}")
        print("  ✓ StockPilot 服务已安全退出。祝您投资顺利！")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # 1. 启动盘中守护进程（后台非阻塞）
    if not args.no_daemon:
        print("📡 正在启动盘中风险监控守护进程 (intraday_daemon)...")
        result = daemon_control.start_daemon(root_dir)
        if result["status"] == "started":
            daemon_token = result["token"]
        print(f"  ✓ 守护进程: {result['status']} (PID: {result.get('pid')})")
    else:
        print("⚠️ 跳过自动启动守护进程 (--no-daemon)")

    # 2. 延迟拉起系统默认浏览器
    app_url = f"http://localhost:{args.port}"
    if not args.no_browser:
        def open_browser():
            time.sleep(1.2)
            print(f"🌐 自动打开浏览器: {app_url}")
            webbrowser.open(app_url)

        import threading
        threading.Thread(target=open_browser, daemon=True).start()

    print(f"✨ 终端主控面板地址: {app_url}")
    print("💡 按 Ctrl+C 可一键安全关停所有组件\n")

    # 3. 启动 FastAPI Web 容器（主线程阻塞）
    try:
        sys.path.insert(0, str(root_dir))
        uvicorn.run(
            "api.main:app",
            host=args.host,
            port=args.port,
            log_level="info",
            access_log=False,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        cleanup()


if __name__ == "__main__":
    raise SystemExit(main() or 0)
