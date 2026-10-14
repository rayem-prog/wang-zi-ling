"""API 启动补采及收盘后刷新，与手动复盘共享单任务注册表。"""
import asyncio
import datetime as dt
import logging

from config.settings import load_settings
from data.health import _expected_session
from api.health_cache import health_cache
from data.trading_calendar import calendar_verified


class RefreshPolicy:
    def __init__(self):
        self.last_attempt=None

    def due(self,now,latest, *, job=None):
        if job and job.get('status') in ('queued','running'):
            return False
        target=_expected_session(now).isoformat()
        if not calendar_verified(now.date()) or (latest and latest>=target):
            return False
        if job and job.get('finished_at'):
            try:
                finished=dt.datetime.fromisoformat(job['finished_at'])
                if finished.tzinfo and (now-finished).total_seconds()<1800:
                    return False
            except (ValueError,TypeError):
                pass
        return self.last_attempt is None or (now-self.last_attempt).total_seconds()>=1800

    def submitted(self,now):
        self.last_attempt=now


async def refresh_loop():
    from api.jobs import daily_jobs
    policy=RefreshPolicy()
    while True:
        try:
            s=load_settings();now=dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
            # 以全池可信检查判断补采，旧缓存和仅指数更新不能掩盖个股缺口。
            job=daily_jobs.latest_summary()
            if job and job.get('status') in ('queued','running'):
                await asyncio.sleep(60)
                continue
            health=await asyncio.to_thread(health_cache.get,s,wait=True)
            from data.health import health_conditions
            conditions=health_conditions(health)
            latest=health.get('trusted_trade_date') if health['ready'] and conditions['minute']['ready'] else None
            if policy.due(now,latest,job=job):
                daily_jobs.start(False, data_only=True);policy.submitted(now)
        except Exception:
            logging.getLogger('stockpilot.refresh').exception('行情自动更新检查失败')
        await asyncio.sleep(60)
