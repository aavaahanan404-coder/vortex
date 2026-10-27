"""Background job that runs ttl_purge on a schedule.

run_once(store, now=None) is the testable unit (the scheduler is the only place the wall clock is read).
start(store, interval_minutes) uses APScheduler when it is installed (optional dependency) and
returns the running scheduler; without APScheduler it raises RuntimeError so a deployment cannot
believe it is purging when it is not.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from modules import ttl_purge


def run_once(store, now: Optional[datetime] = None, log: Optional[Callable[[dict], None]] = None,
             cfg: Optional[dict] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    report = ttl_purge.purge(store, now, cfg=cfg)
    if log:
        for ev in report["events"]:            # append-only audit trail of every deletion
            log(ev)
    return report


def start(store, interval_minutes: int = 60, log: Optional[Callable[[dict], None]] = None):
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError as exc:
        raise RuntimeError("APScheduler is not installed: `pip install apscheduler` to run the purge job") from exc
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(lambda: run_once(store, log=log), "interval", minutes=interval_minutes,
                  id="ttl_purge", max_instances=1, coalesce=True)
    sched.start()
    return sched
