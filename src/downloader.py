from __future__ import annotations

import logging
import os
import random
import subprocess
import time
from pathlib import Path

from src import db
from src.config_loader import Config
from src.models import Artist, Job
from src.nas_monitor import check_nas_with_retry
from src.sites.base import SiteAdapter, SiteRegistry

logger = logging.getLogger(__name__)


def download_artist(
    conn: sqlite3.Connection,
    artist: Artist,
    config: Config,
    registry: SiteRegistry,
    triggered_by: str = "manual",
) -> Job:
    """Download media for a single artist. Returns the completed Job."""
    adapter = registry.get(artist.site)

    # Job lock: check for existing running job
    existing = db.get_running_job_for_artist(conn, artist.id)
    if existing:
        logger.warning("Artist %s already has a running job (id=%d)", artist.handle, existing.id)
        return existing

    # Create job record
    job = Job(artist_id=artist.id, status="running", triggered_by=triggered_by)
    job.id = db.insert_job(conn, job)

    # Snapshot directory before download
    artist_dir = Path(config.nas.mount_path) / artist.handle
    before_snapshot = _snapshot_directory(artist_dir)

    # Attempt download with retries
    last_error = None
    for attempt in range(config.download.retry_attempts):
        try:
            result = _run_gallery_dl(artist.source_url, config, adapter)
            if result.returncode == 0:
                # Success — compute metrics
                after_snapshot = _snapshot_directory(artist_dir)
                file_count, total_bytes = _diff_snapshots(before_snapshot, after_snapshot)

                db.update_job_completion(
                    conn, job.id, "success", file_count, total_bytes
                )
                db.update_last_scan(conn, artist.id)
                db.insert_log(conn, "INFO", "downloader", f"Downloaded {file_count} file(s) for {artist.handle}", job_id=job.id, artist_id=artist.id)
                job.status = "success"
                job.file_count = file_count
                job.total_bytes = total_bytes
                return job

            # Non-zero exit — categorize error
            stderr = result.stderr or ""
            if adapter.detect_auth_error(stderr):
                adapter.mark_auth_invalid(conn)
                db.update_job_completion(conn, job.id, "failed", 0, 0, error_message="Auth error: credentials may be expired")
                db.insert_log(conn, "ERROR", "downloader", f"Auth error for {artist.handle}: credentials invalid", job_id=job.id, artist_id=artist.id)
                job.status = "failed"
                job.error_message = "Auth error"
                return job

            last_error = stderr[:500] or f"gallery-dl exited with code {result.returncode}"

        except subprocess.TimeoutExpired:
            last_error = f"gallery-dl timed out after {config.download.timeout}s"

        # Retry with backoff (unless last attempt)
        if attempt < config.download.retry_attempts - 1:
            delay = config.download.retry_backoff[attempt]
            logger.warning(
                "Attempt %d/%d failed for %s, retrying in %ds: %s",
                attempt + 1,
                config.download.retry_attempts,
                artist.handle,
                delay,
                last_error,
            )
            db.insert_log(conn, "WARNING", "downloader", f"Retry {attempt + 1} for {artist.handle}: {last_error}", job_id=job.id, artist_id=artist.id)
            time.sleep(delay)

    # All retries exhausted
    db.update_job_completion(conn, job.id, "failed", 0, 0, error_message=last_error)
    db.insert_log(conn, "ERROR", "downloader", f"All retries exhausted for {artist.handle}: {last_error}", job_id=job.id, artist_id=artist.id)
    job.status = "failed"
    job.error_message = last_error
    return job


def download_all(
    conn: sqlite3.Connection,
    config: Config,
    registry: SiteRegistry,
    triggered_by: str = "manual",
) -> list[Job]:
    """Download all active artists sequentially with NAS pre-flight check."""
    artists = db.get_active_artists(conn)
    if not artists:
        logger.info("No active artists to download")
        return []

    # NAS pre-flight check
    if not check_nas_with_retry(Path(config.nas.mount_path)):
        db.insert_log(conn, "ERROR", "downloader", "NAS unavailable, aborting download run")
        return []

    jobs: list[Job] = []
    for i, artist in enumerate(artists):
        logger.info("Downloading %s (%d/%d)", artist.handle, i + 1, len(artists))
        job = download_artist(conn, artist, config, registry, triggered_by)
        jobs.append(job)

        # Inter-artist cooldown with jitter
        if i < len(artists) - 1:
            cooldown = random.randint(
                config.download.inter_artist_cooldown[0],
                config.download.inter_artist_cooldown[1],
            )
            logger.debug("Inter-artist cooldown: %ds", cooldown)
            time.sleep(cooldown)

    return jobs


def _run_gallery_dl(
    source_url: str, config: Config, adapter: SiteAdapter
) -> subprocess.CompletedProcess[str]:
    cmd = [
        "gallery-dl",
        "--config", str(adapter.get_gallery_dl_config_path()),
        "--dest", config.nas.mount_path,
        "--write-archive", f"sqlite:///{adapter.get_archive_db_path()}",
    ]
    for auth_file in adapter.get_auth_files():
        cmd.extend(["--cookies", str(auth_file)])
    cmd.append(source_url)

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=config.download.timeout,
    )


def _snapshot_directory(path: Path) -> dict[str, int]:
    """Walk directory and return {relative_path: file_size}."""
    snapshot: dict[str, int] = {}
    if not path.exists():
        return snapshot
    for root, _, files in os.walk(path):
        for f in files:
            full = Path(root) / f
            try:
                rel = str(full.relative_to(path))
                snapshot[rel] = full.stat().st_size
            except OSError:
                continue
    return snapshot


def _diff_snapshots(
    before: dict[str, int], after: dict[str, int]
) -> tuple[int, int]:
    """Return (new_file_count, total_new_bytes) from diffing two snapshots."""
    new_files = set(after.keys()) - set(before.keys())
    total_bytes = sum(after[f] for f in new_files)
    return len(new_files), total_bytes
