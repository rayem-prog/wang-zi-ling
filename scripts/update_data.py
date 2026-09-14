"""M1 验收脚本：python3 scripts/update_data.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_settings
from data import pipeline


def main() -> None:
    settings = load_settings()
    result = pipeline.update_all(settings)
    print(f"updated={len(result['updated'])} failed={result['failed']}")
    print(f"bars_added={result['bars_added']} bench_updated={result['bench_updated']}")


if __name__ == "__main__":
    main()
