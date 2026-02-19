"""
Background scheduler for periodic data collection from the Lay Engine.
Uses APScheduler with asyncio job store.
"""

import json
import logging
import asyncio
from datetime import datetime, date, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

log = logging.getLogger("scheduler")

_scheduler: AsyncIOScheduler | None = None
_config = {"poll_interval_minutes": 15, "enabled": True}


async def poll_session_data():
    """Main job: fetch today's data from Lay Engine, upsert into DB."""
    # Deferred imports to avoid circular dependencies
    from db import get_pool

    pool = get_pool()
    if not pool:
        log.warning("No DB pool available — skipping poll")
        return

    today = date.today().isoformat()
    log.info(f"Polling session data for {today}")

    run_id = await pool.fetchval(
        "INSERT INTO scheduler_runs (job_type, started_at) "
        "VALUES ('data_poll', $1) RETURNING id",
        datetime.now(timezone.utc),
    )

    try:
        # Import here to use the singleton from main
        from main import lay_engine, sync_date_data

        stats = await sync_date_data(today)

        await pool.execute(
            "UPDATE scheduler_runs SET completed_at=$1, status='success', "
            "sessions_synced=$2, bets_synced=$3, results_synced=$4 WHERE id=$5",
            datetime.now(timezone.utc),
            stats["sessions"],
            stats["bets"],
            stats["results"],
            run_id,
        )
        log.info(f"Poll complete: {stats}")

    except Exception as e:
        log.error(f"Poll failed: {e}")
        await pool.execute(
            "UPDATE scheduler_runs SET completed_at=$1, status='failed', "
            "error_message=$2 WHERE id=$3",
            datetime.now(timezone.utc),
            str(e),
            run_id,
        )


def start_scheduler(poll_interval_minutes: int = 15, enabled: bool = True):
    """Start the APScheduler with the configured interval."""
    global _scheduler, _config
    _config = {"poll_interval_minutes": poll_interval_minutes, "enabled": enabled}

    _scheduler = AsyncIOScheduler()
    if enabled:
        _scheduler.add_job(
            poll_session_data,
            IntervalTrigger(minutes=poll_interval_minutes),
            id="data_poll",
            replace_existing=True,
        )
        log.info(f"Scheduler started — polling every {poll_interval_minutes} minutes")
    else:
        log.info("Scheduler started — polling disabled")
    _scheduler.start()


def stop_scheduler():
    """Shut down the scheduler."""
    if _scheduler:
        _scheduler.shutdown(wait=False)
        log.info("Scheduler stopped")


def configure_scheduler(poll_interval_minutes: int, enabled: bool):
    """Reconfigure the scheduler at runtime."""
    global _config
    _config = {"poll_interval_minutes": poll_interval_minutes, "enabled": enabled}
    if _scheduler:
        _scheduler.remove_all_jobs()
        if enabled:
            _scheduler.add_job(
                poll_session_data,
                IntervalTrigger(minutes=poll_interval_minutes),
                id="data_poll",
                replace_existing=True,
            )
            log.info(f"Scheduler reconfigured — every {poll_interval_minutes} min")
        else:
            log.info("Scheduler disabled")


def get_scheduler_status() -> dict:
    """Return current scheduler state."""
    jobs = _scheduler.get_jobs() if _scheduler else []
    return {
        "enabled": _config["enabled"],
        "poll_interval_minutes": _config["poll_interval_minutes"],
        "running": _scheduler.running if _scheduler else False,
        "jobs": [
            {"id": j.id, "next_run": str(j.next_run_time)} for j in jobs
        ],
    }
