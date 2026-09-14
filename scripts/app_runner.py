"""StockPilot v2 综合 App 总控调度器：统一管理 FastAPI 服务、盘中守护进程与浏览器唤起。"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import uvicorn


def parse_args():
    parser = argparse.ArgumentParser(description="StockPilot v2 统一集成应用启动器")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="FastAPI 监听地址 (设为 0.0.0.0 可供局域网或其他设备访问)")
    parser.add_argument("--port", type=int, default=8888, help="FastAPI 监听端口 (默认 8888)")
    parser.add_argument("--no-browser", action="store_true", help="不自动拉起浏览器")
    parser.add_argument("--no-daemon", action="store_true", help="不自动拉起盘中守护进程")
    return parser.parse_args()


def ensure_port_available(port: int):
    """检测并自动释放被占用的端口，防止 [Errno 48] address already in use。"""
    try:
        cmd = f"lsof -ti :{port}"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        out = res.stdout.strip()
        if out:
            pids = [int(p) for p in out.split() if p.isdigit()]
            for pid in pids:
                if pid == os.getpid():
                    continue
                print(f"🔄 检测到端口 {port} 已被旧进程占用 (PID: {pid})，正在自动清理释放...")
                try:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(0.4)
                except ProcessLookupError:
                    pass
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            time.sleep(0.5)
            print(f"  ✓ 端口 {port} 已成功清理并恢复就绪")
    except Exception as e:
        print(f"⚠️ 检查端口占用时提示: {e}")


def main():
    args = parse_args()
    root_dir = Path(__file__).resolve().parent.parent
    os.chdir(root_dir)

    print("=" * 60)
    print("🚀 正在启动 StockPilot v2 量化交易终端...")
    print(f"📁 工作根目录: {root_dir}")
    print("=" * 60)

    # 启动前自动确保端口空闲可用
    ensure_port_available(args.port)

    daemon_proc = None

    def cleanup(signum=None, frame=None):
        print("\n🛑 收到退出信号，正在安全停止后台子服务...")
        if daemon_proc and daemon_proc.poll() is None:
            daemon_proc.terminate()
            try:
                daemon_proc.wait(timeout=3)
            except Exception:
                daemon_proc.kill()
            print("  ✓ 盘中守护进程已关停")
        print("  ✓ StockPilot 服务已安全退出。祝您投资顺利！")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # 1. 启动盘中守护进程（后台非阻塞）
    if not args.no_daemon:
        try:
            subprocess.run("pkill -f 'scripts.intraday_daemon' 2>/dev/null || true", shell=True)
            time.sleep(0.3)
        except Exception:
            pass

        print("📡 正在启动盘中风险监控守护进程 (intraday_daemon)...")
        daemon_proc = subprocess.Popen(
            [sys.executable, "-m", "scripts.intraday_daemon"],
            cwd=str(root_dir),
        )
        print(f"  ✓ 守护进程已拉起 (PID: {daemon_proc.pid})")
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
    main()
