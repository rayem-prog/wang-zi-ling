"""数据源封装：优先 AkShare（东财），网络不可达时自动降级腾讯/新浪。输出统一归一化 DataFrame。"""

from __future__ import annotations

import json
import os
import re

import akshare as _ak
import pandas as pd
import requests as _requests


STOCK_BAR_COLUMNS = ["code", "date", "open", "high", "low", "close", "volume", "amount"]
MINUTE_BAR_COLUMNS = ["code", "date", "time", "open", "high", "low", "close", "volume", "amount"]
SPOT_COLUMNS = ["code", "name", "price", "pct_change", "volume", "amount"]


_SESSION = _requests.Session()
_SESSION.trust_env = False  # 避免 macOS 系统代理拦截行情接口
_SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
_EM_AVAILABLE = True
_PREFER = os.environ.get("STOCKPILOT_DATA_SOURCE", "tx")  # tx=腾讯/新浪优先；em=东财优先


def _to_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.date


def _fetch_daily_em(code: str, start: str = "2021-01-01", end: str | None = None, adjust: str = "qfq") -> pd.DataFrame:
    raw = _ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        start_date=start.replace("-", ""),
        end_date=(end or "20300101").replace("-", ""),
        adjust=adjust,
    )
    if raw is None or raw.empty:
        return pd.DataFrame(columns=STOCK_BAR_COLUMNS)
    out = pd.DataFrame(
        {
            "code": code,
            "date": _to_date(raw["日期"]),
            "open": raw["开盘"].astype(float),
            "high": raw["最高"].astype(float),
            "low": raw["最低"].astype(float),
            "close": raw["收盘"].astype(float),
            "volume": raw["成交量"].astype(float),
            "amount": raw["成交额"].astype(float),
        }
    )
    return out.sort_values("date").reset_index(drop=True)


def _fetch_spot_em(codes: list[str]) -> pd.DataFrame:
    if not codes:
        return pd.DataFrame(columns=SPOT_COLUMNS)
    raw = _ak.stock_zh_a_spot_em()
    mask = raw["代码"].astype(str).str.zfill(6).isin([c.zfill(6) for c in codes])
    sub = raw.loc[mask].copy()
    if sub.empty:
        return pd.DataFrame(columns=SPOT_COLUMNS)
    out = pd.DataFrame(
        {
            "code": sub["代码"].astype(str).str.zfill(6),
            "name": sub["名称"],
            "price": sub["最新价"].astype(float),
            "pct_change": sub["涨跌幅"].astype(float),
            "volume": sub["成交量"].astype(float),
            "amount": sub["成交额"].astype(float),
        }
    )
    return out.reset_index(drop=True)


def _fetch_index_daily_em(symbol: str = "sh000300", start: str | None = None, end: str | None = None) -> pd.DataFrame:
    raw = _ak.stock_zh_index_daily_em(symbol=symbol)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=STOCK_BAR_COLUMNS)
    raw = raw.copy()
    raw["date"] = _to_date(raw["date"])
    if start:
        raw = raw[raw["date"] >= pd.Timestamp(start).date()]
    if end:
        raw = raw[raw["date"] <= pd.Timestamp(end).date()]
    out = pd.DataFrame(
        {
            "code": symbol,
            "date": raw["date"],
            "open": raw["open"].astype(float),
            "high": raw["high"].astype(float),
            "low": raw["low"].astype(float),
            "close": raw["close"].astype(float),
            "volume": raw["volume"].astype(float),
            "amount": raw["amount"].astype(float),
        }
    )
    return out.reset_index(drop=True)


def _fetch_universe_ak(base_pool: str = "hs300", extra_codes: tuple[str, ...] = ()) -> list[str]:
    codes: list[str] = []
    if base_pool == "hs300":
        raw = _ak.index_stock_cons_csindex(symbol="000300")
        codes = raw["成分券代码"].astype(str).str.zfill(6).tolist()
    codes += [c.zfill(6) for c in extra_codes]
    return sorted(set(codes))


def _tencent_symbol(code: str) -> str:
    c = str(code).lower().zfill(6)
    if c.startswith(("6", "9")):
        return "sh" + c
    if c.startswith(("0", "2", "3")):
        return "sz" + c
    return "bj" + c


def _rows_to_bars(code: str, rows: list, start=None, end=None) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=STOCK_BAR_COLUMNS)
    records = []
    for r in rows:
        date = str(r[0])
        compact = date.replace("-", "")
        if start and compact < start.replace("-", ""):
            continue
        if end and compact > end.replace("-", ""):
            continue
        open_, close, high, low, volume = float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])
        records.append(
            {
                "code": code,
                "date": pd.to_datetime(date).date(),
                "open": open_, "high": high, "low": low, "close": close,
                "volume": volume, "amount": round(close * volume * 100, 2),
            }
        )
    out = pd.DataFrame(records)
    return out.sort_values("date").reset_index(drop=True) if not out.empty else pd.DataFrame(columns=STOCK_BAR_COLUMNS)


def _fetch_kline_tencent(symbol: str, start: str | None, end: str | None, adjust: str = "qfq") -> pd.DataFrame:
    start_s = (start or "2021-01-01").replace("/", "-")
    end_s = (end or "2030-01-01").replace("/", "-")
    param = f"{symbol},day,{start_s},{end_s},2000"
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    if adjust == "qfq":
        param += ",qfq"
    else:
        url = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
    r = _SESSION.get(url, params={"param": param}, timeout=30)
    payload = r.json()
    data = payload.get("data", {})
    if not isinstance(data, dict) or symbol not in data:
        raise ValueError(f"腾讯行情返回异常: {str(payload)[:120]}")
    node = data[symbol]
    rows = node.get("qfqday") or node.get("day")
    code = symbol[2:] if symbol[:2] in ("sh", "sz", "bj") else symbol
    return _rows_to_bars(code, rows or [], start=start_s, end=end_s)


def _fetch_daily_tencent(code: str, start: str | None = None, end: str | None = None, adjust: str = "qfq") -> pd.DataFrame:
    return _fetch_kline_tencent(_tencent_symbol(code), start, end, adjust)


def _fetch_spot_tencent(codes: list[str]) -> pd.DataFrame:
    frames = []
    for i in range(0, len(codes), 40):
        chunk = codes[i : i + 40]
        symbols = ",".join(_tencent_symbol(c) for c in chunk)
        r = _SESSION.get("https://qt.gtimg.cn/q=" + symbols, timeout=30)
        r.encoding = "gbk"
        for line in r.text.split(";"):
            if "=" not in line:
                continue
            parts = line.split("=", 1)[1].strip('"').split("~")
            if len(parts) < 38:
                continue
            frames.append(
                {
                    "code": str(parts[2]).zfill(6),
                    "name": parts[1],
                    "price": float(parts[3]),
                    "pct_change": float(parts[32]),
                    "volume": float(parts[36]),
                    "amount": float(parts[37]) * 10_000,
                }
            )
    if not frames:
        return pd.DataFrame(columns=SPOT_COLUMNS)
    return pd.DataFrame(frames)


def _fetch_universe_sina(base_pool: str) -> list[str]:
    if base_pool != "hs300":
        raise ValueError("备用数据源仅支持 hs300 股票池")
    codes: list[str] = []
    for page in range(1, 5):
        r = _SESSION.get(
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData",
            params={
                "page": page,
                "num": 100,
                "sort": "symbol",
                "asc": 1,
                "node": "hs300",
                "symbol": "",
                "_s_r_a": "init",
            },
            timeout=30,
        )
        data = r.json()
        codes.extend(str(item["code"]).zfill(6) for item in data)
        if len(data) < 100:
            break
    return sorted(set(codes))


def fetch_daily(code: str, start: str = "2021-01-01", end: str | None = None, adjust: str = "qfq") -> pd.DataFrame:
    if _PREFER == "tx":
        try:
            df = _fetch_daily_tencent(code, start, end, adjust)
            if not df.empty:
                return df
            raise ValueError("empty")
        except Exception:
            return _fetch_daily_em(code, start, end, adjust)
    try:
        df = _fetch_daily_em(code, start, end, adjust)
        if not df.empty:
            return df
        raise ValueError("empty")
    except Exception:
        return _fetch_daily_tencent(code, start, end, adjust)


def fetch_spot_batch(codes: list[str]) -> pd.DataFrame:
    if not codes:
        return pd.DataFrame(columns=SPOT_COLUMNS)
    if _PREFER == "tx":
        try:
            df = _fetch_spot_tencent(codes)
            if not df.empty:
                return df
            raise ValueError("empty")
        except Exception:
            return _fetch_spot_em(codes)
    try:
        df = _fetch_spot_em(codes)
        if not df.empty:
            return df
        raise ValueError("empty")
    except Exception:
        return _fetch_spot_tencent(codes)


def fetch_index_daily(symbol: str = "sh000300", start: str | None = None, end: str | None = None) -> pd.DataFrame:
    if _PREFER == "tx":
        try:
            df = _fetch_kline_tencent(symbol, start, end, adjust="")
            if not df.empty:
                return df
            raise ValueError("empty")
        except Exception:
            return _fetch_index_daily_em(symbol, start, end)
    try:
        df = _fetch_index_daily_em(symbol, start, end)
        if not df.empty:
            return df
        raise ValueError("empty")
    except Exception:
        return _fetch_kline_tencent(symbol, start, end, adjust="")


def fetch_universe(base_pool: str = "hs300", extra_codes: tuple[str, ...] = ()) -> list[str]:
    codes: list[str] = []
    if base_pool:
        if _PREFER == "tx":
            try:
                codes = _fetch_universe_sina(base_pool)
                if not codes:
                    raise ValueError("empty")
            except Exception:
                codes = _fetch_universe_ak(base_pool, ())
        else:
            try:
                codes = _fetch_universe_ak(base_pool, ())
                if not codes:
                    raise ValueError("empty")
            except Exception:
                codes = _fetch_universe_sina(base_pool)
    codes += [c.zfill(6) for c in extra_codes]
    return sorted(set(codes))


def _fetch_minute_sina(code: str, count: int = 240) -> pd.DataFrame:
    symbol = _tencent_symbol(code)
    url = (
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data="
        f"/CN_MarketDataService.getKLineData?symbol={symbol}&scale=1&ma=no&datalen={count}"
    )
    r = _SESSION.get(url, timeout=30)
    match = re.search(r"\((.*)\)", r.text, re.S)
    if not match:
        return pd.DataFrame(columns=MINUTE_BAR_COLUMNS)
    raw = json.loads(match.group(1))
    if not raw:
        return pd.DataFrame(columns=MINUTE_BAR_COLUMNS)
    records = []
    for item in raw:
        day_str = str(item.get("day", ""))
        if " " in day_str:
            d_part, t_part = day_str.split(" ", 1)
            t_part = t_part[:5]
        else:
            d_part = day_str
            t_part = "09:30"
        records.append(
            {
                "code": str(code).zfill(6),
                "date": d_part,
                "time": t_part,
                "open": float(item.get("open", 0.0)),
                "high": float(item.get("high", 0.0)),
                "low": float(item.get("low", 0.0)),
                "close": float(item.get("close", 0.0)),
                "volume": float(item.get("volume", 0.0)),
                "amount": float(item.get("amount", 0.0)),
            }
        )
    df = pd.DataFrame(records)
    return df[MINUTE_BAR_COLUMNS].sort_values(["date", "time"]).reset_index(drop=True)


def _fetch_minute_tencent(code: str) -> pd.DataFrame:
    symbol = _tencent_symbol(code)
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={symbol}"
    r = _SESSION.get(url, timeout=30)
    data = r.json().get("data", {}).get(symbol, {}).get("data", {})
    date_str = str(data.get("date", ""))
    if len(date_str) == 8:
        date_formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    else:
        date_formatted = date_str
    lines = data.get("data", [])
    if not lines:
        return pd.DataFrame(columns=MINUTE_BAR_COLUMNS)
    records = []
    prev_vol = 0.0
    prev_amt = 0.0
    for line in lines:
        parts = line.strip().split()
        if len(parts) < 4:
            continue
        raw_t = parts[0]
        time_str = f"{raw_t[:2]}:{raw_t[2:4]}" if len(raw_t) >= 4 else raw_t
        price = float(parts[1])
        cum_vol = float(parts[2])
        cum_amt = float(parts[3])
        bar_vol = max(0.0, cum_vol - prev_vol)
        bar_amt = max(0.0, cum_amt - prev_amt)
        prev_vol = cum_vol
        prev_amt = cum_amt
        records.append(
            {
                "code": str(code).zfill(6),
                "date": date_formatted,
                "time": time_str,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": bar_vol,
                "amount": bar_amt,
            }
        )
    df = pd.DataFrame(records)
    return df[MINUTE_BAR_COLUMNS].sort_values(["date", "time"]).reset_index(drop=True)


def _fetch_minute_em(code: str, count: int = 240) -> pd.DataFrame:
    raw = _ak.stock_zh_a_hist_min_em(symbol=code.zfill(6), period="1", adjust="qfq")
    if raw is None or raw.empty:
        return pd.DataFrame(columns=MINUTE_BAR_COLUMNS)
    records = []
    for _, r in raw.tail(count).iterrows():
        dt_str = str(r["时间"])
        if " " in dt_str:
            d_part, t_part = dt_str.split(" ", 1)
            t_part = t_part[:5]
        else:
            d_part = dt_str
            t_part = "09:30"
        records.append(
            {
                "code": str(code).zfill(6),
                "date": d_part,
                "time": t_part,
                "open": float(r["开盘"]),
                "high": float(r["最高"]),
                "low": float(r["最低"]),
                "close": float(r["收盘"]),
                "volume": float(r["成交量"]),
                "amount": float(r["成交额"]),
            }
        )
    df = pd.DataFrame(records)
    return df[MINUTE_BAR_COLUMNS].sort_values(["date", "time"]).reset_index(drop=True)


def fetch_minute_bars(code: str, date: str | None = None, count: int = 240) -> pd.DataFrame:
    df = pd.DataFrame(columns=MINUTE_BAR_COLUMNS)
    try:
        df = _fetch_minute_sina(code, count=count)
        if df.empty:
            raise ValueError("empty sina")
    except Exception:
        try:
            df = _fetch_minute_tencent(code)
            if df.empty:
                raise ValueError("empty tencent")
        except Exception:
            try:
                df = _fetch_minute_em(code, count=count)
            except Exception:
                df = pd.DataFrame(columns=MINUTE_BAR_COLUMNS)

    if not df.empty and date is not None:
        df = df[df["date"] == str(date)].reset_index(drop=True)
    return df

