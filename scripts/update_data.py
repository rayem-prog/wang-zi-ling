"""可续跑的数据补采入口；只采集行情，不触发推荐或通知。"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_settings
from data import db, pipeline
from data.health import assess_data_health


def main() -> None:
    parser = argparse.ArgumentParser(description="补采真实行情并报告剩余缺口")
    parser.add_argument("--codes", help="逗号分隔的股票代码；缺省为配置的股票池")
    parser.add_argument("--local-pool", action="store_true", help="使用数据库已有快照中的股票池续跑")
    parser.add_argument("--minute", action="store_true", help="采集真实分钟线")
    parser.add_argument("--date", help="分钟线日期 YYYY-MM-DD")
    parser.add_argument("--current-only", action="store_true", help="兼容旧参数；可信复权数据仍按完整历史更新")
    parser.add_argument("--history-only", action="store_true", help="兼容旧参数；可信复权数据仍按完整历史更新")
    parser.add_argument("--no-jitter", action="store_true", help="跳过请求间隔（仅限测试）")
    args = parser.parse_args()
    if args.current_only and args.history_only:
        parser.error("--current-only 和 --history-only 不能同时使用")
    settings = load_settings()
    db.init_db(settings.data.db_path)
    if args.codes:
        codes = [code.strip().zfill(6) for code in args.codes.split(",") if code.strip()]
    elif args.local_pool:
        spot = db.load_spot(settings.data.db_path)
        codes = spot["code"].astype(str).str.zfill(6).tolist() if not spot.empty else []
        if not codes:
            codes = [code for code in db.list_codes(settings.data.db_path)
                     if code != settings.data.index_symbol]
    else:
        codes = None
    if args.minute:
        result = pipeline.update_minute_bars(settings, codes=codes, date=args.date,
                                             sleep_jitter=not args.no_jitter)
    else:
        from data.verified_daily import update_verified_daily
        result = update_verified_daily(settings, codes=codes)
    print(json.dumps({"result": result, "health": assess_data_health(settings)},
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
