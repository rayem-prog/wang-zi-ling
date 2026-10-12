"""获取独立未复权历史库，避免用前复权历史价格当作实际成交价。"""
from dataclasses import replace
import json
from pathlib import Path
from config.settings import load_settings
from data.verified_daily import update_verified_daily
from data import db

if __name__ == '__main__':
    settings=load_settings()
    codes=[c for c in db.list_codes(settings.data.db_path) if c!=settings.data.index_symbol]
    raw_settings=replace(settings,data=replace(settings.data,db_path=str(Path(settings.data.db_path).with_name('raw-' + Path(settings.data.db_path).name)),adjust='none'))
    result=update_verified_daily(raw_settings,codes=codes)
    print(json.dumps(result,ensure_ascii=False,indent=2))
