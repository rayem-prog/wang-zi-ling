"""Next-session limit execution and daily mark-to-market, including unfilled orders."""
from __future__ import annotations

import math

import pandas as pd

from strategy.risk import positive


class Portfolio:
    def __init__(self, cash, cost_rate=.002):
        if not math.isfinite(cash) or cash <= 0 or not 0 <= cost_rate < 1:
            raise ValueError('初始资金必须为正数，费用率需在[0,1)')
        self.initial_cash = float(cash)
        self.cash = float(cash)
        self.cost_rate = cost_rate
        self.positions = {}
        self.audit = []
        self.stale_codes = []
        self._executed = set()

    @property
    def equity(self):
        return self.cash + sum(p['shares']*p['current_price'] for p in self.positions.values())

    def holdings(self):
        return pd.DataFrame([dict(code=c, name=p['name'], shares=p['shares'], cost=p['avg_cost'],
                                  price=p['current_price']) for c,p in sorted(self.positions.items())],
                            columns=['code','name','shares','cost','price'])

    def mark(self, bars):
        quotes = bars.set_index('code').close.to_dict() if not bars.empty else {}
        self.stale_codes = []
        for code, pos in self.positions.items():
            price = positive(quotes.get(code))
            if price:
                pos['current_price'] = price
            else:
                self.stale_codes.append(code)

    def execute(self, orders, bars, date, max_total_position=1., max_positions=None):
        if orders is None or orders.empty:
            return
        date = str(date)
        if 'date' not in orders or (pd.to_datetime(orders.date) >= pd.Timestamp(date)).any():
            raise ValueError('订单必须来自当前交易日之前的冻结决策')
        quotes = {str(r.code):r for _,r in bars.iterrows()} if not bars.empty else {}
        # Validate every order before mutating any positions or cash.
        for idx, row in orders.reset_index(drop=True).iterrows():
            key = (str(row.date), str(row.code), str(row.action), idx)
            if key in self._executed:
                continue
            self._executed.add(key)
            code = str(row.code)
            side = '买入' if row.action in ('买入','加仓') else '卖出'
            requested = int(positive(row.shares))
            audit = dict(date=date, decision_date=str(row.date), code=code, name=row.get('name',code),
                         side=side, action=row.action, shares=requested, filled_shares=0,
                         status='unfilled', price=None, amount=0., fee=0., pnl=0.,
                         reason='下一交易日未成交，订单到期；资金保留')
            from data.trading_calendar import expected_sessions
            sessions = expected_sessions(str(pd.Timestamp(row.date).date()),date)
            future_sessions = [d for d in sessions if d > str(pd.Timestamp(row.date).date())]
            if future_sessions != [date]:
                audit.update(status='expired',reason='已超过订单有效的下一交易日，未执行')
                self.audit.append(audit)
                continue
            quote = quotes.get(code)
            if quote is None or not positive(quote.get('open')) or not positive(quote.get('volume')):
                audit['reason'] = '无有效开盘行情或停牌，订单到期；资金保留'
                self.audit.append(audit)
                continue
            opening = float(quote.open)
            low_limit = positive(row.get('limit_low')) or opening
            high_limit = positive(row.get('limit_high')) or opening
            ref = positive(row.get('price_ref'))
            # Conservative daily-bar model: do not assume queue fills at a locked limit.
            locked = positive(quote.get('high')) == positive(quote.get('low'))
            if locked and ref and ((side == '买入' and opening >= ref*1.098) or
                                   (side == '卖出' and opening <= ref*.902)):
                audit['reason'] = '封板无法确认成交，订单到期'
                self.audit.append(audit)
                continue
            unit = quote.get('volume_unit','lots')
            if unit not in ('lots','shares'):
                audit['reason'] = '成交量单位未知，无法核实成交上限'
                self.audit.append(audit)
                continue
            liquidity = int(float(quote.volume)*(100 if unit == 'lots' else 1))//100*100
            fee_rate = self.cost_rate/2
            if side == '买入':
                if opening > high_limit:
                    self.audit.append(audit)
                    continue
                price = min(opening, high_limit)
                if max_positions is not None and code not in self.positions and len(self.positions) >= max_positions:
                    audit['reason'] = '已达到最大持仓数量，订单到期'
                    self.audit.append(audit)
                    continue
                # Opening revaluation constrains new cash deployment even after overnight gaps.
                held_value = sum(p['shares']*((positive(quotes[c].get('open')) or p['current_price']) if c in quotes else p['current_price'])
                                 for c,p in self.positions.items())
                capacity = max(0., (self.cash+held_value)*max_total_position-held_value)
                budget = min(self.cash, capacity)
                shares = min(requested//100*100,liquidity,int(budget//(price*(1+fee_rate)*100))*100)
                if shares < 100:
                    audit['reason'] = '整手资金或仓位额度不足，订单到期'
                    self.audit.append(audit)
                    continue
                amount, fee = shares*price, shares*price*fee_rate
                self.cash -= amount+fee
                old = self.positions.get(code, dict(shares=0, avg_cost=0., name=row.get('name',code)))
                cost = old['shares']*old['avg_cost']+amount+fee
                self.positions[code] = dict(name=old['name'], shares=old['shares']+shares,
                    avg_cost=cost/(old['shares']+shares), current_price=price, tier=old.get('tier','mid'))
            else:
                if code not in self.positions or opening < low_limit:
                    self.audit.append(audit)
                    continue
                price = max(opening, low_limit)
                pos = self.positions[code]
                shares = min(requested,pos['shares'],liquidity)
                if shares <= 0:
                    self.audit.append(audit)
                    continue
                amount, fee = shares*price, shares*price*fee_rate
                self.cash += amount-fee
                audit['pnl'] = amount-fee-shares*pos['avg_cost']
                pos['shares'] -= shares
                if not pos['shares']:
                    del self.positions[code]
            audit.update(status='filled' if shares == requested else 'partial', filled_shares=shares,
                         price=price, amount=amount, fee=fee, reason=row.get('reason','冻结订单成交'))
            self.audit.append(audit)
