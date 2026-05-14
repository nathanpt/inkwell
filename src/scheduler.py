from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config_loader import Config
from src.downloader import download_all, download_stale
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


def create_scheduler(config: Config) -> BackgroundScheduler:
    """Create and configure the APScheduler with the cron schedule from config."""
    from src import db

    scheduler = BackgroundScheduler()

    cron_kwargs = parse_cron(config.schedule.cron)
    trigger = CronTrigger(**cron_kwargs)

    scheduler.add_job(
        _scheduled_run,
        trigger=trigger,
        id="inkwell_scheduled_download",
        kwargs={"config": config, "registry": create_registry()},
        replace_existing=True,
    )

    logger.info("Scheduler configured with cron: %s", config.schedule.cron)
    return scheduler


def _is_in_time_window(config: Config) -> bool:
    """Check if the current time is within the configured time window."""
    start = config.schedule.time_window_start.strip()
    end = config.schedule.time_window_end.strip()
    if not start or not end:
        return True

    try:
        now = datetime.now()
        start_h, start_m = map(int, start.split(":"))
        end_h, end_m = map(int, end.split(":"))
        start_min = start_h * 60 + start_m
        end_min = end_h * 60 + end_m
        now_min = now.hour * 60 + now.minute
        if start_min <= end_min:
            return start_min <= now_min < end_min
        else:
            # Window wraps midnight, e.g. 22:00-06:00
            return now_min >= start_min or now_min < end_min
    except (ValueError, AttributeError):
        logger.warning("Invalid time window format: %s-%s", start, end)
        return True


def _scheduled_run(config: Config, registry: SiteRegistry) -> None:
    """Callback for the scheduled download job."""
    from src import db

    logger.info("Scheduled download run starting")

    if not _is_in_time_window(config):
        logger.info("Outside time window (%s-%s), skipping", config.schedule.time_window_start, config.schedule.time_window_end)
        db.insert_log("INFO", "scheduler", f"Outside time window ({config.schedule.time_window_start}-{config.schedule.time_window_end}), skipping")
        return

    try:
        threshold = config.schedule.stale_threshold_days
        if threshold > 0:
            logger.info("Running stale-only download (threshold=%d days)", threshold)
            db.insert_log("INFO", "scheduler", f"Stale-only download run starting (threshold={threshold} days)")
            jobs = download_stale(config, registry, threshold, triggered_by="scheduled")
        else:
            db.insert_log("INFO", "scheduler", "Scheduled download run starting")
            jobs = download_all(config, registry, triggered_by="scheduled")

        succeeded = sum(1 for j in jobs if j.status == "failed")
        db.insert_log(
            "INFO",
            "scheduler",
            f"Scheduled run completed: {len(jobs)} artists, {succeeded} failures",
        )
    except Exception as e:
        logger.exception("Scheduled run failed with exception")
        db.insert_log("ERROR", "scheduler", f"Scheduled run failed: {e}")
