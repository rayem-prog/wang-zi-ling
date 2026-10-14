"""Verified lifecycle facts for securities present in the saved local universe.

This small override table records official exchange/company notices checked on
2026-09-28. Unknown securities remain unclassified and continue to fail closed.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


SECURITIES = {
    "601995": {
        "suspension_intervals": [
            {"start": "2025-11-20", "end": "2025-12-17", "resumed_on": "2025-12-18",
             "source": "https://paper.cnstock.com/html/2025-12/18/content_2159676.htm",
             "reason": "筹划重大资产重组停牌；临2025-047确认2025-12-18复牌。"},
            {"start": "2026-09-15", "end": "2026-09-22", "resumed_on": "2026-09-23",
             "source": "https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12616622&stockid=601995",
             "start_source": "https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12588539&stockid=601995",
             "reason": "实施A股异议股东收购请求权停牌；公司公告确认2026-09-23复牌。"},
        ],
    },
    "000792": {
        "listed_on": "2021-08-10",
        "source": "https://disc.static.szse.cn/download/disc/disk02/finalpage/2021-08-03/98aaca67-7c8d-4afb-bb61-e1aeaf7118f8.PDF",
        "note": "盐湖股份恢复上市，上市前区间不是行情采集缺口。",
    },
    "001289": {
        "listed_on": "2022-01-24",
        "source": "https://www.szse.cn/disclosure/notice/company/t20220120_590746.html",
        "note": "龙源电力上市日；早于上市日的历史数据不存在。",
    },
    "300957": {
        "listed_on": "2021-03-25",
        "source": "https://www.szse.cn/disclosure/notice/t20210324_585240.html",
        "note": "贝泰妮 A 股上市日；上市前区间不是行情采集缺口。",
    },
    "300979": {
        "listed_on": "2021-04-26",
        "source": "https://www.szse.cn/disclosure/notice/company/t20210423_585633.html",
        "note": "华利集团 A 股上市日；上市前区间不是行情采集缺口。",
    },
    "301269": {
        "listed_on": "2022-07-29",
        "source": "https://disc.static.szse.cn/disc/disk03/finalpage/2022-07-28/f5433f99-033d-4384-8df5-2868cfdc74d0.PDF",
        "note": "华大九天 A 股上市日；上市前区间不是行情采集缺口。",
    },
    "600905": {
        "listed_on": "2021-06-10",
        "source": "https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20210609_81861470.shtml",
        "note": "三峡能源 A 股上市日；上市前区间不是行情采集缺口。",
    },
    "600938": {
        "listed_on": "2022-04-21",
        "source": "https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20220419_5701079.shtml",
        "note": "中国海油 A 股上市日；上市前区间不是行情采集缺口。",
    },
    "600941": {
        "listed_on": "2022-01-05",
        "source": "https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20211231_5673842.shtml",
        "note": "中国移动 A 股上市日；上市前区间不是行情采集缺口。",
    },
    "601728": {
        "listed_on": "2021-08-20",
        "source": "https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20210819_82626883.shtml",
        "note": "中国电信 A 股上市日；上市前区间不是行情采集缺口。",
    },
    "601868": {
        "listed_on": "2021-09-28",
        "source": "https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20210927_83094980.shtml",
        "note": "中国能建 A 股上市日；上市前区间不是行情采集缺口。",
    },
    "605117": {
        "listed_on": "2021-04-20",
        "source": "https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20210419_81155646.shtml",
        "note": "德业股份 A 股上市日；上市前区间不是行情采集缺口。",
    },
    "605499": {
        "listed_on": "2021-05-27",
        "source": "https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20210526_81718214.shtml",
        "note": "东鹏饮料 A 股上市日；上市前区间不是行情采集缺口。",
    },
    "688041": {
        "listed_on": "2022-08-12",
        "source": "https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20220811_86447590.shtml",
        "note": "海光信息 A 股上市日；上市前区间不是行情采集缺口。",
    },
    "688187": {
        "listed_on": "2021-09-07",
        "source": "https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20210906_82909744.shtml",
        "note": "时代电气 A 股上市日；上市前区间不是行情采集缺口。",
    },
    "688223": {
        "listed_on": "2022-01-26",
        "source": "https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20220125_84195967.shtml",
        "note": "晶科能源 A 股上市日；上市前区间不是行情采集缺口。",
    },
    "688271": {
        "listed_on": "2022-08-22",
        "source": "https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20220819_86529668.shtml",
        "note": "联影医疗 A 股上市日；上市前区间不是行情采集缺口。",
    },
    "688303": {
        "listed_on": "2021-07-22",
        "source": "https://big5.sse.com.cn/site/cht/www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20210721_82360639.shtml",
        "note": "大全能源 A 股上市日；上市前区间不是行情采集缺口。",
    },
    "600837": {
        "listed_on": "1994-02-02",
        "suspended_from": "2025-02-06",
        "delisted_on": "2025-03-04",
        "source": "https://www.sse.com.cn/disclosure/announcement/listing/stock/c/c_20250226_10773005.shtml",
        "suspension_source": "https://www1.hkexnews.hk/listedco/listconews/sehk/2025/0121/2025012100990_c.pdf",
        "note": "海通证券 2025-02-05 为最后交易日，2025-02-06 起连续停牌，2025-03-04 终止上市。",
    },
    "601059": {
        "listed_on": "2023-02-01",
        "suspended_from": "2026-09-15",
        "source": "https://www.sse.com.cn/assortment/stock/list/info/announcement/index.shtml?productId=601059",
        "listing_source": "https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20230131_88217306.shtml",
        "note": "信达证券上市日；2026-09-15 起连续停牌直至终止上市。",
    },
    "601238": {
        "listed_on": "2012-03-29",
        "suspended_from": "2026-09-14",
        "source": "https://static.cninfo.com.cn/finalpage/2026-09-15/1225564444.PDF",
        "listing_source": "https://www.hkexnews.hk/listedco/listconews/sehk/2012/1010/ltn20121010410_c.pdf",
        "note": "广汽集团 2026-09-14 开市起停牌筹划重大资产重组。",
    },
    "601989": {
        "listed_on": "2009-12-16",
        "suspended_from": "2025-08-13",
        "delisted_on": "2025-09-05",
        "source": "https://www.sse.com.cn/disclosure/announcement/listing/stock/c/c_20250829_10790128.shtml",
        "note": "中国重工 2025-08-12 为最后交易日，2025-09-05 终止上市。",
    },
}

# Finite historical stops are documented separately from IPO/ongoing lifecycle
# facts. Only reviewed issuer notices are admitted to this shipped registry.
for _interval in json.loads(Path(__file__).with_name('security_suspensions.json').read_text()):
    _metadata = SECURITIES.setdefault(_interval['code'], {})
    _metadata.setdefault('suspension_intervals', []).append(_interval)


def security_metadata(code: str) -> dict | None:
    return SECURITIES.get(str(code).zfill(6))


def inactive_status(code: str, on_date: str) -> dict | None:
    """Return verified exchange status effective on a trade date, if known."""
    meta = security_metadata(code)
    if not meta:
        return None
    day = dt.date.fromisoformat(on_date)
    delisted = meta.get("delisted_on")
    if delisted and day >= dt.date.fromisoformat(delisted):
        return {"code": str(code).zfill(6), "status": "delisted", "effective_date": delisted,
                "reason": meta["note"], "source": meta["source"]}
    suspended = meta.get("suspended_from")
    if suspended and day >= dt.date.fromisoformat(suspended):
        return {"code": str(code).zfill(6), "status": "suspended", "effective_date": suspended,
                "reason": meta["note"], "source": meta.get("suspension_source", meta["source"])}
    for interval in meta.get('suspension_intervals', []):
        if interval['start'] <= on_date <= interval['end']:
            return {"code": str(code).zfill(6), "status": "suspended",
                    "effective_date": interval['start'], "end_date": interval['end'],
                    "resumed_on": interval['resumed_on'], "reason": interval['reason'],
                    "source": interval['source'], "start_source": interval.get('start_source',interval['source'])}
    return None
