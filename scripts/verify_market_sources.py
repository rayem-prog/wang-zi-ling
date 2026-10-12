"""独立新浪未复权历史交叉检查；保存原始响应，不使用 SDK 的填充/去重结果。"""
from pathlib import Path
import hashlib
import json
import time

import pandas as pd
import requests

from data.fetcher import _tencent_symbol
from data.verified_daily import capture


def verify(codes, root='artifacts/market-cross-check'):
    from akshare.stock.stock_zh_a_sina import hk_js_decode,py_mini_racer,zh_sina_a_stock_hist_url
    root=Path(root);root.mkdir(parents=True,exist_ok=True);results={}
    with requests.Session() as session:
        session.trust_env=False
        for code in codes:
            try:
                symbol=_tencent_symbol(code)
                r=session.get(zh_sina_a_stock_hist_url.format(symbol),timeout=30);r.raise_for_status()
                raw=r.content;path=root/(code+'.sina.response');path.write_bytes(raw)
                with py_mini_racer.MiniRacer() as js:
                    js.eval(hk_js_decode)
                    rows=js.call('d',r.text.split('=',1)[1].split(';')[0].replace('"',''))
                sina=pd.DataFrame(rows);sina['date']=sina.date.str[:10]
                sina=sina[(sina.date>='2021-01-01')&(sina.date<='2026-10-09')].set_index('date')
                tx=capture(code,'2021-01-01','2026-10-09','none','artifacts/market-captures').set_index('date')
                common=sina.index.intersection(tx.index);a=sina.loc[common];b=tx.loc[common]
                difference=max(float((a[k]-b[k]).abs().max()) for k in ['open','high','low','close'])
                scales={str(scale):float((a.volume-b.volume*scale).abs().max()) for scale in (1,100)}
                results[code]=dict(rows=len(common),sina_rows=len(sina),tencent_rows=len(tx),
                    ohlc_max_difference=difference,volume_residual=scales,
                    amount_max_difference=float((a.amount-b.amount).abs().max()),
                    only_sina=sorted(set(sina.index)-set(tx.index)),only_tencent=sorted(set(tx.index)-set(sina.index)),
                    raw_sha256=hashlib.sha256(raw).hexdigest(),raw_file=str(path.resolve()))
                (root/(code+'.sina.decoded.json')).write_text(json.dumps(rows,ensure_ascii=False))
            except Exception as exc:
                results[code]={'error':str(exc)}
            print(code,results[code],flush=True);time.sleep(.3)
    (root/'report.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    return results


if __name__=='__main__':
    verify(['000001','600519','300750','688981','688561','000063','000301','600150'])
