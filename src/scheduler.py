from __future__ import annotations

import logging
import sqlite3

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src import db
from src.config_loader import Config
from src.downloader import download_all
from src.sites.base import SiteRegistry, create_registry

logger = logging.getLogger(__name__)


def parse_cron(cron_expr: str) -> dict:
    """Parse a cron expression like '0 3 * * *' into APScheduler trigger kwargs."""
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression: {cron_expr}")
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
    }


def create_scheduler(
    conn: sqlite3.Connection, config: Config
) -> BackgroundScheduler:
    """Create and configure the APScheduler with the cron schedule from config."""
    scheduler = BackgroundScheduler()

    cron_kwargs = parse_cron(config.schedule.cron)
    trigger = CronTrigger(**cron_kwargs)

    scheduler.add_job(
        _scheduled_run,
        trigger=trigger,
        id="inkwell_scheduled_download",
        kwargs={"conn": conn, "config": config, "registry": create_registry()},
        replace_existing=True,
    )

    logger.info("Scheduler configured with cron: %s", config.schedule.cron)
    return scheduler


def _scheduled_run(conn: sqlite3.Connection, config: Config, registry: SiteRegistry) -> None:
    """Callback for the scheduled download job."""
    logger.info("Scheduled download run starting")
    db.insert_log(conn, "INFO", "scheduler", "Scheduled download run starting")
    try:
        jobs = download_all(conn, config, registry, triggered_by="scheduled")
        succeeded = sum(1 for j in jobs if j.status == "failed")
        db.insert_log(
            conn,
            "INFO",
            "scheduler",
            f"Scheduled run completed: {len(jobs)} artists, {succeeded} failures",
        )
    except Exception as e:
        logger.exception("Scheduled run failed with exception")
        db.insert_log(conn, "ERROR", "scheduler", f"Scheduled run failed: {e}")
