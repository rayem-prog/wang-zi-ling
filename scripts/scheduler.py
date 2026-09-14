"""每交易日 16:10（Asia/Shanghai）自动执行 daily_update。"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apscheduler.schedulers.blocking import BlockingScheduler

from scripts import daily_update


def job() -> None:
    print(f"{datetime.now().isoformat()} running daily update")
    daily_update.main()


def main() -> None:
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(job, "cron", day_of_week="mon-fri", hour=16, minute=10)
    print("scheduler started: mon-fri 16:10 Asia/Shanghai")
    scheduler.start()


if __name__ == "__main__":
    main()
