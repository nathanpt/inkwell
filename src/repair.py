from __future__ import annotations

import json
import logging
import os
import random
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
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
# Smaller bursts land more files before a site's rate window trips; the between-
# chunk cooldown then paces the next burst. Tunable — the per-chunk logs show the
# actual landing rate so this can be dialed in.
CHUNK_SIZE = 10
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
    sites_aborted: list = field(default_factory=list)  # sites skipped mid-run (rate/auth)
    aborted_reason: str | None = None  # hard stop only ("already running")


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


def _downloaded_paths(stdout: str, nas_path: Path) -> list[Path]:
    """Parse gallery-dl stdout for file paths under nas_path.

    With piped stdout gallery-dl uses PipeOutput: each downloaded file is a bare
    absolute-path line; skipped (already-on-disk) files are "# {path}". Both
    indicate a real file on disk. Anything else on stdout is ignored.
    """
    prefix = str(nas_path) + os.sep
    paths: list[Path] = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("# "):
            line = line[2:].strip()
        if line.startswith(prefix):
            paths.append(Path(line))
    return paths

def _relocate_renamed(nas_path: Path, handle: str, downloaded: set[Path]) -> int:
    """Move files gallery-dl wrote outside the canonical handle dir back into
    nas/{handle}/{year}/, so reconcile (and the gallery) see them. Returns the
    number of files moved.

    Files already under the canonical dir are ignored. Name collisions are
    skipped — the existing canonical file wins and the row will match it.
    Emptied source dirs are removed; anything else is left in place.
    """
    canonical = nas_path / handle
    by_src: dict[str, list[Path]] = {}
    for p in downloaded:
        try:
            rel = p.relative_to(nas_path)
        except ValueError:
            continue
        if len(rel.parts) < 3 or rel.parts[0] == handle:
            continue
        by_src.setdefault(rel.parts[0], []).append(p)

    moved = 0
    for src_name, paths in by_src.items():
        src_moved = 0
        for p in paths:
            dest = canonical / p.parent.name / p.name
            if dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                p.rename(dest)
                moved += 1
                src_moved += 1
            except OSError:
                logger.warning("Failed to relocate %s -> %s", p, dest)
        db.insert_log(
            "WARNING", "repair",
            f"{handle}: files landed under renamed author dir '{src_name}'; "
            f"relocated {src_moved}/{len(paths)} file(s) into canonical dir",
        )
        # Remove now-empty year/author dirs; rmdir fails harmlessly if non-empty.
        for p in paths:
            try:
                p.parent.rmdir()
            except OSError:
                pass
        try:
            (nas_path / src_name).rmdir()
        except OSError:
            pass
    return moved


def _wait_for_unpause(site: str, config: Config) -> None:
    """Wait for a rate-limit pause to clear instead of abandoning the artist.

    The pause auto-expires once the rate window since the last hit passes (see
    ``is_site_paused``); this polls until it does, bounded by ``pause_seconds``
    so a run never blocks indefinitely.
    """
    pause_seconds = config.rate_limit.pause_seconds
    db.insert_log(
        "INFO", "repair",
        f"Site {site} rate-limit paused; waiting up to {pause_seconds}s "
        f"for the rate window to clear",
    )
    deadline = time.time() + pause_seconds
    while time.time() < deadline and is_site_paused(site, config.rate_limit):
        time.sleep(30)


def repair_missing(
    config: Config, registry: SiteRegistry, max_posts: int | None = None
) -> RepairResult:
    """Re-download missing files via per-post gallery-dl URLs and reconcile rows.

    Runs in a background thread; the connection-per-operation db helpers are
    thread-safe by design. ``max_posts`` caps the total posts attempted this run
    (scheduled auto-repair passes a cap; the manual UI action is uncapped).

    Rate-limit and auth failures are handled per site: a site that exhausts its
    retries is skipped for the rest of the run while remaining sites continue.
    """
    if db.get_state("repair:running") == "1":
        db.insert_log("WARNING", "repair", "Repair already running, aborting")
        return RepairResult(aborted_reason="already running")

    db.set_state("repair:running", "1")
    result = RepairResult()
    run_start = time.time()
    try:
        # Sibling-only rows recover with no network — do this first.
        merged, _ = consolidate_all_sibling_zips(config)
        result.sibling_entries_recovered = merged

        report = check_integrity(config)
        result.missing_before = len(report.missing)

        if not report.missing:
            _store_result(result, run_start)
            return result

        # Group missing rows by (site, handle), preserving rows per artist.
        groups: dict[tuple[str, str], list[MissingRow]] = {}
        per_site: dict[str, int] = {}
        for r in report.missing:
            groups.setdefault((r.site, r.handle), []).append(r)
            per_site[r.site] = per_site.get(r.site, 0) + 1

        site_summary = ", ".join(f"{s}: {n}" for s, n in sorted(per_site.items()))
        db.insert_log(
            "INFO", "repair",
            f"Repair starting: {result.missing_before} missing across "
            f"{len(per_site)} site(s) ({site_summary}); chunk size {CHUNK_SIZE}",
        )

        nas_path = Path(config.nas.mount_path)
        aborted_sites: set[str] = set()

        for (site, handle), rows in groups.items():
            if site in aborted_sites:
                continue
            try:
                adapter = registry.get(site)
            except ValueError:
                logger.warning("No adapter for site %s; skipping %d row(s)", site, len(rows))
                result.rows_unsupported += len(rows)
                continue

            if is_site_paused(site, config.rate_limit):
                _wait_for_unpause(site, config)
                if is_site_paused(site, config.rate_limit):
                    db.insert_log(
                        "WARNING", "repair",
                        f"Site {site} still rate-limit paused after waiting; "
                        f"skipping {handle} ({len(rows)} row(s))",
                    )
                    continue

            # Partition rows by post id; drop non-numeric as unsupported.
            by_pid: dict[str, list[MissingRow]] = {}
            unsupported_here = 0
            for r in rows:
                pid = extract_post_id(r.filename)
                if pid is None:
                    unsupported_here += 1
                    continue
                by_pid.setdefault(pid, []).append(r)
            if unsupported_here:
                result.rows_unsupported += unsupported_here

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

            db.insert_log(
                "INFO", "repair",
                f"{handle} ({site}): {len(rows)} row(s), {len(attempted)} post(s) to fetch"
                + (f", {unsupported_here} unsupported" if unsupported_here else ""),
            )

            cooldown = config.sites.get(site, SiteConfig()).cooldown
            chunk_list = list(_chunks(attempted, CHUNK_SIZE))
            # Only rows whose chunk actually executed reach reconciliation, so a
            # site abort mid-artist never deletes rows we never tried to fetch.
            run_rows: list[MissingRow] = []
            site_aborted = False
            downloaded: set[Path] = set()
            had_rc0 = False
            for ci, chunk in enumerate(chunk_list):
                urls = [u for (u, _, _) in chunk]
                run_rows.extend(r for (_, _, prows) in chunk for r in prows)
                for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
                    t0 = time.time()
                    proc = _run_batch(urls, config, adapter)
                    dt = time.time() - t0
                    stderr = proc.stderr or ""
                    chunk_paths = _downloaded_paths(proc.stdout or "", nas_path)
                    downloaded.update(chunk_paths)
                    had_rc0 = had_rc0 or proc.returncode == 0
                    if adapter.detect_auth_error(stderr):
                        adapter.mark_auth_invalid()
                        aborted_sites.add(site)
                        result.sites_aborted.append(site)
                        db.insert_log(
                            "ERROR", "repair",
                            f"{site}: auth error on {handle} chunk {ci + 1}/{len(chunk_list)} "
                            f"({dt:.1f}s); marking auth invalid, skipping site",
                        )
                        site_aborted = True
                        break
                    if adapter.detect_rate_limit_error(stderr):
                        record_hit(site, config.rate_limit)
                        if attempt < MAX_RATE_LIMIT_RETRIES:
                            backoff = 60 * (2 ** attempt)
                            db.insert_log(
                                "WARNING", "repair",
                                f"{site}: rate-limited on {handle} chunk {ci + 1}/{len(chunk_list)} "
                                f"({dt:.1f}s); backoff {backoff}s "
                                f"(attempt {attempt + 1}/{MAX_RATE_LIMIT_RETRIES + 1})",
                            )
                            time.sleep(backoff)
                            continue
                        aborted_sites.add(site)
                        result.sites_aborted.append(site)
                        db.insert_log(
                            "WARNING", "repair",
                            f"{site}: rate-limit retries exhausted; skipping remaining work on site",
                        )
                        site_aborted = True
                        break
                    if proc.returncode != 0 and stderr.strip():
                        tail = "\n".join(stderr.strip().splitlines()[-5:])[:500]
                        db.insert_log(
                            "WARNING", "repair",
                            f"{handle} chunk {ci + 1}/{len(chunk_list)}: rc={proc.returncode}, "
                            f"stderr tail: {tail}",
                        )
                    record_success(site, config.rate_limit)
                    db.insert_log(
                        "INFO", "repair",
                        f"{handle} chunk {ci + 1}/{len(chunk_list)}: {len(urls)} post(s) "
                        f"in {dt:.1f}s (rc={proc.returncode}, {len(chunk_paths)} file(s) downloaded)",
                    )
                    break
                if site_aborted:
                    break
                # Pace between chunks (not after the last one).
                if ci < len(chunk_list) - 1:
                    mult = get_cooldown_multiplier(site)
                    delay = random.uniform(*cooldown) * mult
                    db.insert_log(
                        "INFO", "repair",
                        f"cooldown {delay:.0f}s (multiplier {mult:.1f})",
                    )
                    time.sleep(delay)

            moved = _relocate_renamed(nas_path, handle, downloaded) if downloaded else 0
            resolved_before = result.rows_recovered + result.rows_updated
            _reconcile_artist(nas_path, handle, run_rows, result)
            resolved_gained = (result.rows_recovered + result.rows_updated) - resolved_before
            if resolved_gained == 0 and had_rc0 and run_rows:
                pid = extract_post_id(run_rows[0].filename) or "?"
                db.insert_log(
                    "WARNING", "repair",
                    f"{handle}: 0 of {len(run_rows)} attempted rows recovered despite "
                    f"gallery-dl success. Possible author rename. Manual check: "
                    f"find {nas_path} -name '{pid}_*'",
                )
    finally:
        db.set_state("repair:running", "0")

    _store_result(result, run_start)
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
    recovered = updated = ambiguous = 0
    for r in rows:
        base = Path(r.filename).name
        pid = extract_post_id(r.filename)
        actual = actual_by_year[r.year]
        claimed = claimed_by_year[r.year]

        if base in actual:
            claimed.add(base)
            recovered += 1
            result.rows_recovered += 1
            continue

        candidates = [
            n for n in actual
            if n not in claimed and pid and n.startswith(f"{pid}_")
        ]
        if len(candidates) == 1:
            cand = candidates[0]
            claimed.add(cand)
            db.update_file_row(r.file_id, f"{r.year}/{cand}", r.year, actual[cand])
            updated += 1
            result.rows_updated += 1
        elif len(candidates) > 1:
            ambiguous += 1
            result.rows_ambiguous += 1
            logger.warning(
                "Ambiguous recovery for %s row %d: %d candidates", handle, r.file_id, len(candidates)
            )
        else:
            to_delete.append(r.file_id)

    # Deletion safeguard: only delete when at least one row proved gallery-dl
    # could reach this artist's posts. A systemic failure (rename, auth) keeps
    # all rows so the operator can intervene.
    deleted = 0
    if recovered + updated > 0:
        deleted = db.delete_file_records(to_delete)
        result.rows_deleted += deleted
    elif to_delete:
        result.artists_no_recovery += 1
        db.insert_log(
            "WARNING", "repair",
            f"{handle}: no files recovered from {len(to_delete)} row(s); keeping all (safeguard)",
        )

    db.insert_log(
        "INFO", "repair",
        f"reconcile {handle}: {recovered} recovered, {updated} updated, "
        f"{deleted} deleted, {ambiguous} ambiguous (of {len(rows)} attempted)",
    )


def _store_result(result: RepairResult, run_start: float) -> None:
    db.set_state("repair:last_result", json.dumps(asdict(result)))
    resolved = result.rows_recovered + result.rows_updated
    elapsed = max(time.time() - run_start, 0.001)
    rate = resolved / elapsed * 60
    level = "INFO" if resolved > 0 or result.missing_before == 0 else "WARNING"
    parts = [
        f"Repair done in {elapsed:.0f}s: {resolved} recovered/updated ({rate:.1f} files/min)",
        f"{result.rows_deleted} deleted",
        f"{result.rows_ambiguous} ambiguous",
        f"{result.rows_unsupported} unsupported",
    ]
    if result.sites_aborted:
        parts.append("sites aborted: " + ", ".join(sorted(set(result.sites_aborted))))
    if result.aborted_reason:
        parts.append(f"aborted: {result.aborted_reason}")
    db.insert_log(level, "repair", ", ".join(parts))
