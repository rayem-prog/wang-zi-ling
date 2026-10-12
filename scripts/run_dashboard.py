"""兼容旧启动命令，启动统一 StockPilot 终端。"""
from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.app_runner import main
    main()
