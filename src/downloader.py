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
from src.rate_limiter import record_hit, record_success, is_site_paused, get_cooldown_multiplier
from src.sites.base import SiteAdapter, SiteRegistry
from src import zipper

logger = logging.getLogger(__name__)


def download_artist(
    artist: Artist,
    config: Config,
    registry: SiteRegistry,
    triggered_by: str = "manual",
) -> Job:
    """Download media for a single artist. Returns the completed Job."""
    adapter = registry.get(artist.site)

    # Job lock: check for existing running job
    existing = db.get_running_job_for_artist(artist.id)
    if existing:
        logger.warning("Artist %s already has a running job (id=%d)", artist.handle, existing.id)
        return existing

    # Create job record
    job = Job(artist_id=artist.id, status="running", triggered_by=triggered_by)
    job.id = db.insert_job(job)

    # Snapshot directory before download
    artist_dir = Path(config.nas.mount_path) / artist.handle
    before_snapshot = _snapshot_directory(artist_dir)

    # Attempt download with retries
    last_error = None
    for attempt in range(config.download.retry_attempts):
        try:
            db.insert_log("INFO", "downloader", f"Attempt {attempt + 1}/{config.download.retry_attempts} for {artist.handle}", job_id=job.id, artist_id=artist.id)
            result = _run_gallery_dl(
                source_url=artist.source_url,
                config=config,
                adapter=adapter,
                progress_cb=lambda fc, tb: db.update_job_progress(job.id, fc, tb),
                progress_before=before_snapshot,
                progress_artist_dir=artist_dir,
            )

            # Always compute what was actually downloaded
            after_snapshot = _snapshot_directory(artist_dir)
            file_count, total_bytes = _diff_snapshots(before_snapshot, after_snapshot)

            if result.returncode == 0:
                db.update_job_completion(
                    job.id, "success", file_count, total_bytes
                )
                db.update_last_scan(artist.id)
                record_success(artist.site, config.rate_limit)
                db.insert_log("INFO", "downloader", f"Downloaded {file_count} file(s) for {artist.handle}", job_id=job.id, artist_id=artist.id)

                if config.zip.enabled and config.zip.on_job_complete:
                    try:
                        zipped = zipper.zip_artist_dirs(
                            Path(config.nas.mount_path), artist.handle, config.zip.compression_level
                        )
                        if zipped:
                            db.insert_log("INFO", "downloader", f"Zipped {len(zipped)} year(s) for {artist.handle}", job_id=job.id, artist_id=artist.id)
                    except Exception:
                        logger.exception("Post-job zip failed for %s", artist.handle)
                        db.insert_log("WARNING", "downloader", f"Post-job zip failed for {artist.handle}", job_id=job.id, artist_id=artist.id)

                job.status = "success"
                job.file_count = file_count
                job.total_bytes = total_bytes
                return job

            # Non-zero exit — categorize error
            stderr = result.stderr or ""

            # Check rate-limit first
            if adapter.detect_rate_limit_error(stderr):
                record_hit(artist.site, config.rate_limit)
                db.update_job_completion(job.id, "failed", file_count, total_bytes, error_message="Rate limited by site")
                db.insert_log("WARNING", "downloader", f"Rate limited for {artist.handle}, skipping retries", job_id=job.id, artist_id=artist.id)
                job.status = "failed"
                job.error_message = "Rate limited"
                job.file_count = file_count
                job.total_bytes = total_bytes
                return job

            if adapter.detect_auth_error(stderr):
                adapter.mark_auth_invalid()
                db.update_job_completion(job.id, "failed", file_count, total_bytes, error_message="Auth error: credentials may be expired")
                db.insert_log("ERROR", "downloader", f"Auth error for {artist.handle}: credentials invalid", job_id=job.id, artist_id=artist.id)
                job.status = "failed"
                job.error_message = "Auth error"
                job.file_count = file_count
                job.total_bytes = total_bytes
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
            db.insert_log("WARNING", "downloader", f"Retry {attempt + 1} for {artist.handle}: {last_error}", job_id=job.id, artist_id=artist.id)
            time.sleep(delay)

    # All retries exhausted
    # Final snapshot to capture any partial downloads
    after_snapshot = _snapshot_directory(artist_dir)
    file_count, total_bytes = _diff_snapshots(before_snapshot, after_snapshot)
    db.update_job_completion(job.id, "failed", file_count, total_bytes, error_message=last_error)
    db.insert_log("ERROR", "downloader", f"All retries exhausted for {artist.handle}: {last_error}", job_id=job.id, artist_id=artist.id)
    job.status = "failed"
    job.error_message = last_error
    return job


def download_all(
    config: Config,
    registry: SiteRegistry,
    triggered_by: str = "manual",
) -> list[Job]:
    """Download all active artists sequentially with NAS pre-flight check."""
    artists = db.get_active_artists()
    if not artists:
        logger.info("No active artists to download")
        return []

    # NAS pre-flight check
    if not check_nas_with_retry(Path(config.nas.mount_path)):
        db.insert_log("ERROR", "downloader", "NAS unavailable, aborting download run")
        return []

    jobs: list[Job] = []
    for i, artist in enumerate(artists):
        # Check if site is paused due to rate limiting
        if is_site_paused(artist.site, config.rate_limit):
            logger.warning("Skipping %s — site %s is rate-limit paused", artist.handle, artist.site)
            db.insert_log("WARNING", "downloader", f"Skipping {artist.handle}: site {artist.site} is rate-limit paused")
            continue

        logger.info("Downloading %s (%d/%d)", artist.handle, i + 1, len(artists))
        job = download_artist(artist, config, registry, triggered_by)
        jobs.append(job)

        # Inter-artist cooldown with jitter, scaled by rate-limit multiplier
        if i < len(artists) - 1:
            base_cooldown = random.randint(
                config.download.inter_artist_cooldown[0],
                config.download.inter_artist_cooldown[1],
            )
            multiplier = get_cooldown_multiplier(artist.site)
            cooldown = int(base_cooldown * multiplier)
            logger.debug("Inter-artist cooldown: %ds (multiplier=%.1f)", cooldown, multiplier)
            time.sleep(cooldown)

    return jobs


def download_stale(
    config: Config,
    registry: SiteRegistry,
    stale_threshold_days: int,
    triggered_by: str = "scheduled",
) -> list[Job]:
    """Download only artists whose last_scan_at is older than the threshold or has never been scanned."""
    from datetime import datetime, timedelta, timezone

    artists = db.get_active_artists()
    if not artists:
        logger.info("No active artists to download")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_threshold_days)
    stale = []
    for a in artists:
        if a.last_scan_at is None:
            stale.append(a)
            continue
        try:
            scanned = datetime.fromisoformat(a.last_scan_at)
            if scanned.tzinfo is None:
                scanned = scanned.replace(tzinfo=timezone.utc)
            if scanned < cutoff:
                stale.append(a)
        except (ValueError, TypeError):
            stale.append(a)

    if not stale:
        logger.info("No stale artists to download")
        return []

    logger.info("Found %d stale artist(s) out of %d total", len(stale), len(artists))

    # NAS pre-flight check
    if not check_nas_with_retry(Path(config.nas.mount_path)):
        db.insert_log("ERROR", "downloader", "NAS unavailable, aborting stale download run")
        return []

    jobs: list[Job] = []
    for i, artist in enumerate(stale):
        if is_site_paused(artist.site, config.rate_limit):
            logger.warning("Skipping %s — site %s is rate-limit paused", artist.handle, artist.site)
            db.insert_log("WARNING", "downloader", f"Skipping {artist.handle}: site {artist.site} is rate-limit paused")
            continue

        logger.info("Downloading stale %s (%d/%d)", artist.handle, i + 1, len(stale))
        job = download_artist(artist, config, registry, triggered_by)
        jobs.append(job)

        if i < len(stale) - 1:
            base_cooldown = random.randint(
                config.download.inter_artist_cooldown[0],
                config.download.inter_artist_cooldown[1],
            )
            multiplier = get_cooldown_multiplier(artist.site)
            cooldown = int(base_cooldown * multiplier)
            logger.debug("Inter-artist cooldown: %ds (multiplier=%.1f)", cooldown, multiplier)
            time.sleep(cooldown)

    return jobs


def _run_gallery_dl(
    source_url: str,
    config: Config,
    adapter: SiteAdapter,
    progress_cb=None,
    progress_before: dict | None = None,
    progress_artist_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        "gallery-dl",
        "--config", str(adapter.get_gallery_dl_config_path()),
        "--dest", config.nas.mount_path,
        "--download-archive", str(adapter.get_archive_db_path()),
    ]
    for auth_file in adapter.get_auth_files():
        cmd.extend(["--cookies", str(auth_file)])
    cmd.append(source_url)

    logger.info("Running: %s", " ".join(cmd))

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Read stdout and stderr concurrently to avoid pipe buffer deadlock
    import threading as _threading

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def _read_stdout():
        if proc.stdout:
            for line in proc.stdout:
                stdout_lines.append(line.rstrip())

    def _read_stderr():
        if proc.stderr:
            for line in proc.stderr:
                line = line.rstrip()
                if line:
                    logger.info("gallery-dl: %s", line)
                stderr_lines.append(line)

    t_out = _threading.Thread(target=_read_stdout, daemon=True)
    t_err = _threading.Thread(target=_read_stderr, daemon=True)
    t_out.start()
    t_err.start()

    # Periodically update job progress while gallery-dl runs
    _stop_progress = _threading.Event()

    def _poll_progress():
        if not progress_cb or progress_before is None or progress_artist_dir is None:
            return
        while not _stop_progress.wait(10):
            snap = _snapshot_directory(progress_artist_dir)
            fc, tb = _diff_snapshots(progress_before, snap)
            if fc > 0:
                progress_cb(fc, tb)

    t_prog = _threading.Thread(target=_poll_progress, daemon=True)
    if progress_cb:
        t_prog.start()

    proc.wait(timeout=config.download.timeout)
    _stop_progress.set()
    t_out.join(timeout=5)
    t_err.join(timeout=5)
    if progress_cb:
        t_prog.join(timeout=5)

    return subprocess.CompletedProcess(
        args=cmd, returncode=proc.returncode,
        stdout="\n".join(stdout_lines), stderr="\n".join(stderr_lines),
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
