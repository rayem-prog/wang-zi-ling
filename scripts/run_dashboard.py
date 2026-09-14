"""启动看板：python3 scripts/run_dashboard.py"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
subprocess.run([sys.executable, "-m", "streamlit", "run", str(root / "app" / "main.py")])
