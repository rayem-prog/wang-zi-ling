"""股票代码、拼音首字母、中文名称智能联想搜索 API。"""

from __future__ import annotations

import re
from typing import Any

import httpx
from fastapi import APIRouter, Query

from config.settings import load_settings
from data import db

router = APIRouter(prefix="/api/stock", tags=["search"])


@router.get("/search")
def search_stocks(q: str = Query(..., min_length=1, max_length=20)) -> list[dict[str, Any]]:
    query = q.strip().lower()
    if not query:
        return []

    results = []
    seen_codes = set()

    # 1. 优先调用新浪毫秒级证券联想接口 (支持拼音、代码、汉字)
    try:
        url = f"https://suggest3.sinajs.cn/suggest/type=11,12&key={query}"
        with httpx.Client(trust_env=False, timeout=3.0) as client:
            resp = client.get(url)
            text = resp.content.decode("gbk", errors="ignore")

        # 格式: var suggestvalue="平安银行,11,000001,sz000001,平安银行,...;浦发银行,...";
        match = re.search(r'"(.*)"', text)
        if match:
            raw_items = match.group(1).split(";")
            for item in raw_items:
                parts = item.split(",")
                if len(parts) >= 4:
                    name = parts[0].strip()
                    code = parts[2].strip()
                    symbol = parts[3].strip()
                    # 过滤A股股票（6位代码）
                    if len(code) == 6 and code.isdigit() and code not in seen_codes:
                        seen_codes.add(code)
                        results.append({
                            "code": code,
                            "name": name,
                            "symbol": symbol,
                            "price": 0.0,
                        })
                if len(results) >= 10:
                    break
    except Exception:
        pass

    # 2. 本地数据库补充或兜底检索
    settings = load_settings()
    try:
        spot = db.load_spot(settings.data.db_path)
        if not spot.empty:
            spot["code"] = spot["code"].astype(str).str.zfill(6)
            price_map = dict(zip(spot["code"], spot["price"]))

            # 为已有搜索结果补充当前最新价
            for r in results:
                if r["code"] in price_map:
                    r["price"] = round(float(price_map[r["code"]]), 2)

            # 若网络检索无果，在本地 spot 模糊匹配
            if not results:
                mask = spot["code"].str.contains(query, case=False) | spot["name"].str.contains(query, case=False)
                matched = spot[mask].head(10)
                for _, row in matched.iterrows():
                    c = str(row["code"]).zfill(6)
                    if c not in seen_codes:
                        seen_codes.add(c)
                        results.append({
                            "code": c,
                            "name": str(row["name"]),
                            "symbol": f"sh{c}" if c.startswith("6") else f"sz{c}",
                            "price": round(float(row.get("price", 0.0)), 2),
                        })
    except Exception:
        pass

    return results
