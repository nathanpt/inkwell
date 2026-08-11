from __future__ import annotations

import json
import logging
import random
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from src import db
from src.config_loader import Config, SiteConfig
from src.integrity import MissingRow, check_integrity, consolidate_all_sibling_zips
from src.rate_limiter import (
    get_cooldown_multiplier,
    is_site_paused,
    record_hit,
    record_success,
)
from src.sites.base import SiteAdapter, SiteRegistry

logger = logging.getLogger(__name__)

POST_ID_RE = re.compile(r"^(\d+)")
CHUNK_SIZE = 25
MAX_RATE_LIMIT_RETRIES = 3


@dataclass
class RepairResult:
    missing_before: int = 0
    sibling_entries_recovered: int = 0
    posts_attempted: int = 0
    rows_recovered: int = 0      # exact basename match
    rows_updated: int = 0        # basename changed, row updated
    rows_deleted: int = 0        # confirmed unrecoverable
    rows_ambiguous: int = 0      # multiple candidates; kept, needs manual look
    rows_unsupported: int = 0    # non-numeric ID or adapter can't build a URL
    artists_no_recovery: int = 0  # systemic failure guard; rows kept
    aborted_reason: str | None = None


def extract_post_id(filename: str) -> str | None:
    """Numeric post ID prefix of a basename (X tweet_id / Pixiv id / DA deviation_id)."""
    m = POST_ID_RE.match(Path(filename).name)
    return m.group(1) if m else None


def _run_batch(
    urls: list[str], config: Config, adapter: SiteAdapter
) -> subprocess.CompletedProcess[str]:
    """Run gallery-dl for a batch of post URLs.

    Mirrors ``downloader._run_gallery_dl`` command construction MINUS
    ``--download-archive`` (dedup must not block a repair re-fetch; the existing
    archive entries remain correct afterward).
    """
    cmd = [
        "gallery-dl",
        "--config", str(adapter.get_gallery_dl_config_path()),
        "--dest", config.nas.mount_path,
    ]
    for auth_file in adapter.get_auth_files():
        cmd.extend(["--cookies", str(auth_file)])
    refresh_token = adapter.get_refresh_token()
    if refresh_token:
        cmd.extend(["-o", f"extractor.pixiv.refresh-token={refresh_token}"])
    cmd.extend(urls)

    logger.info("Repair batch (%d URL(s)): %s", len(urls), " ".join(cmd))
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=config.download.timeout
    )


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def repair_missing(
    config: Config, registry: SiteRegistry, max_posts: int | None = None
) -> RepairResult:
    """Re-download missing files via per-post gallery-dl URLs and reconcile rows.

    Runs in a background thread; the connection-per-operation db helpers are
    thread-safe by design. ``max_posts`` caps the total posts attempted this run
    (scheduled auto-repair passes a cap; the manual UI action is uncapped).
    """
    if db.get_state("repair:running") == "1":
        db.insert_log("WARNING", "repair", "Repair already running, aborting")
        return RepairResult(aborted_reason="already running")

    db.set_state("repair:running", "1")
    result = RepairResult()
    try:
        # Sibling-only rows recover with no network — do this first.
        merged, _ = consolidate_all_sibling_zips(config)
        result.sibling_entries_recovered = merged

        report = check_integrity(config)
        result.missing_before = len(report.missing)

        if not report.missing:
            return result

        # Group missing rows by (site, handle), preserving rows per artist.
        groups: dict[tuple[str, str], list[MissingRow]] = {}
        for r in report.missing:
            groups.setdefault((r.site, r.handle), []).append(r)

        nas_path = Path(config.nas.mount_path)

        for (site, handle), rows in groups.items():
            if result.aborted_reason:
                break
            try:
                adapter = registry.get(site)
            except ValueError:
                logger.warning("No adapter for site %s; skipping %d row(s)", site, len(rows))
                result.rows_unsupported += len(rows)
                continue

            if is_site_paused(site, config.rate_limit):
                db.insert_log(
                    "WARNING", "repair",
                    f"Site {site} rate-limit paused; skipping {handle} ({len(rows)} row(s))",
                )
                continue

            # Partition rows by post id; drop non-numeric as unsupported.
            by_pid: dict[str, list[MissingRow]] = {}
            for r in rows:
                pid = extract_post_id(r.filename)
                if pid is None:
                    result.rows_unsupported += 1
                    continue
                by_pid.setdefault(pid, []).append(r)

            # Build the URL list, honouring the global post cap.
            attempted: list[tuple[str, str, list[MissingRow]]] = []  # (url, pid, rows)
            for pid, prows in by_pid.items():
                if max_posts is not None and result.posts_attempted >= max_posts:
                    break
                url = adapter.build_post_url(handle, pid)
                if url is None:
                    result.rows_unsupported += len(prows)
                    continue
                attempted.append((url, pid, prows))
                result.posts_attempted += 1

            if not attempted:
                continue

            cooldown = config.sites.get(site, SiteConfig()).cooldown
            chunk_list = list(_chunks(attempted, CHUNK_SIZE))
            for ci, chunk in enumerate(chunk_list):
                urls = [u for (u, _, _) in chunk]
                handled = False
                for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
                    proc = _run_batch(urls, config, adapter)
                    stderr = proc.stderr or ""
                    if adapter.detect_auth_error(stderr):
                        adapter.mark_auth_invalid()
                        result.aborted_reason = "auth error"
                        db.insert_log(
                            "ERROR", "repair",
                            f"Auth error for {handle} ({site}); aborting repair",
                        )
                        handled = True
                        break
                    if adapter.detect_rate_limit_error(stderr):
                        record_hit(site, config.rate_limit)
                        if attempt < MAX_RATE_LIMIT_RETRIES:
                            backoff = 60 * (2 ** attempt)
                            db.insert_log(
                                "WARNING", "repair",
                                f"Rate limited on {site}; retrying chunk in {backoff}s",
                            )
                            time.sleep(backoff)
                            continue
                        result.aborted_reason = "rate limited"
                        db.insert_log(
                            "WARNING", "repair",
                            f"Rate limit retries exhausted on {site}; aborting repair",
                        )
                        handled = True
                        break
                    record_success(site, config.rate_limit)
                    handled = True
                    break
                if result.aborted_reason:
                    break
                if not handled:
                    break
                # Pace between chunks (not after the last one).
                if ci < len(chunk_list) - 1:
                    delay = random.uniform(*cooldown) * get_cooldown_multiplier(site)
                    time.sleep(delay)

            if result.aborted_reason:
                break

            attempted_rows = [r for (_, _, prows) in attempted for r in prows]
            _reconcile_artist(nas_path, handle, attempted_rows, result)
    finally:
        db.set_state("repair:running", "0")

    _store_result(result)
    return result


def _reconcile_artist(
    nas_path: Path, handle: str, rows: list[MissingRow], result: RepairResult
) -> None:
    """Match an artist's attempted rows against the loose files gallery-dl wrote."""
    if not rows:
        return

    years = {r.year for r in rows}
    actual_by_year: dict[str, dict[str, int]] = {}
    claimed_by_year: dict[str, set[str]] = {}
    for y in years:
        ydir = nas_path / handle / y
        files: dict[str, int] = {}
        if ydir.is_dir():
            for f in ydir.rglob("*"):
                if f.is_file():
                    files[f.name] = f.stat().st_size
        actual_by_year[y] = files
        claimed_by_year[y] = set()

    to_delete: list[int] = []
    recovered_or_updated = 0
    for r in rows:
        base = Path(r.filename).name
        pid = extract_post_id(r.filename)
        actual = actual_by_year[r.year]
        claimed = claimed_by_year[r.year]

        if base in actual:
            claimed.add(base)
            result.rows_recovered += 1
            recovered_or_updated += 1
            continue

        candidates = [
            n for n in actual
            if n not in claimed and pid and n.startswith(f"{pid}_")
        ]
        if len(candidates) == 1:
            cand = candidates[0]
            claimed.add(cand)
            db.update_file_row(r.file_id, f"{r.year}/{cand}", r.year, actual[cand])
            result.rows_updated += 1
            recovered_or_updated += 1
        elif len(candidates) > 1:
            result.rows_ambiguous += 1
            logger.warning(
                "Ambiguous recovery for %s row %d: %d candidates", handle, r.file_id, len(candidates)
            )
        else:
            to_delete.append(r.file_id)

    # Deletion safeguard: only delete when at least one row proved gallery-dl
    # could reach this artist's posts. A systemic failure (rename, auth) keeps
    # all rows so the operator can intervene.
    if recovered_or_updated > 0:
        result.rows_deleted += db.delete_file_records(to_delete)
    elif to_delete:
        result.artists_no_recovery += 1
        logger.warning(
            "No recovery for %s; keeping %d row(s) (safeguard)", handle, len(to_delete)
        )


def _store_result(result: RepairResult) -> None:
    db.set_state("repair:last_result", json.dumps(asdict(result)))
    resolved = result.rows_recovered + result.rows_updated
    level = "INFO" if resolved > 0 or result.missing_before == 0 else "WARNING"
    db.insert_log(
        level,
        "repair",
        f"Repair done: {resolved} recovered/updated, {result.rows_deleted} deleted, "
        f"{result.rows_ambiguous} ambiguous, {result.rows_unsupported} unsupported"
        + (f" (aborted: {result.aborted_reason})" if result.aborted_reason else ""),
    )
