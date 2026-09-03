from __future__ import annotations

from collections.abc import Iterable

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
# Small bursts keep each chunk within a site's per-window call budget; the
# between-chunk cooldown (and, under sustained rate-limit stress, a full-window
# hold — see _wait_for_rate_window) paces the next burst. Tunable; per-chunk
# logs show the real landing rate. x.com's TweetDetail budget is tight enough
# that 10-post chunks blew it mid-chunk (2026-08-13); 5 keeps a chunk under it.
CHUNK_SIZE = 5
# One retry only: the retry waits out the rate window (see the chunk-retry path),
# but further blind retries into a hot/escalating window just deepen X's lockout
# without recovering files. The next scheduled run picks up the remainder.
MAX_RATE_LIMIT_RETRIES = 1
# gallery-dl stderr substrings that positively identify a dead post. A line
# counts only when it also carries the post's numeric id (see _not_found_pids),
# so 429/401 noise never confirms a removal.
NOT_FOUND_MARKERS = ("404", "not found", "deleted")


@dataclass
class RepairResult:
    missing_before: int = 0
    sibling_entries_recovered: int = 0
    rows_removed_upstream: int = 0  # own URL positively confirmed gone (404/not found/deleted)
    rows_deleted: int = 0        # total deleted: confirmed + inferred unrecoverable
    posts_attempted: int = 0
    rows_recovered: int = 0      # exact basename match
    rows_updated: int = 0        # basename changed, row updated
    rows_deleted: int = 0        # confirmed unrecoverable
    rows_ambiguous: int = 0      # multiple candidates; kept, needs manual look
    rows_unsupported: int = 0    # non-numeric ID or adapter can't build a URL
    artists_no_recovery: int = 0  # systemic failure guard; rows kept
    sites_aborted: list = field(default_factory=list)  # skipped up-front (invalid auth) or mid-run (rate limit/auth error)
    aborted_reason: str | None = None  # hard stop only ("already running")


def extract_post_id(filename: str) -> str | None:
    """Numeric post ID prefix of a basename (X tweet_id / Pixiv id / DA deviation_id)."""
    m = POST_ID_RE.match(Path(filename).name)
    return m.group(1) if m else None



def _not_found_pids(stderr: str, pids: Iterable[str]) -> set[str]:
    """Post ids that gallery-dl reported as gone upstream (404 / not found /
    deleted).

    gallery-dl echoes the failing URL — hence the numeric post id — on its
    error lines, e.g. ``[error][twitter] https://x.com/a/status/123: 404 Not
    Found``. A pid counts only when some line pairs it with a NOT_FOUND_MARKER,
    so rate-limit, auth, and 5xx lines never confirm a removal. Ids are matched
    on word boundaries: pid 123 must not ride along on 1234's error line.
    """
    lines = stderr.lower().splitlines()
    confirmed: set[str] = set()
    for pid in pids:
        pat = re.compile(rf"\b{re.escape(pid)}\b")
        if any(pat.search(line) and m in line for line in lines for m in NOT_FOUND_MARKERS):
            confirmed.add(pid)
    return confirmed


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


def _wait_for_rate_window(site: str, config: Config) -> None:
    """Hold for a full upstream rate window before the next chunk when a site is
    under sustained rate-limit stress (multiplier at/above pause_threshold).

    The normal between-chunk cooldown is far shorter than the upstream window, so
    without this successive chunks pile into the same spent budget even when each
    chunk individually succeeds (observed on x.com 2026-08-13: chunk 1 spent the
    fresh window's TweetDetail budget, chunk 2 immediately 429'd). One chunk per
    window keeps each burst on a fresh budget; successes decay the multiplier
    below the threshold and the normal cooldown resumes automatically.
    """
    window = config.rate_limit.pause_seconds
    db.insert_log(
        "INFO", "repair",
        f"Site {site} under rate-limit stress; holding next chunk for {window}s "
        f"to start on a fresh rate window",
    )
    time.sleep(window)


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

        # Sites whose auth is already flagged invalid: skip all their artists up
        # front instead of discovering the dead session on the first chunk.
        auth_invalid_sites: set[str] = set()
        for site in per_site:
            try:
                site_adapter = registry.get(site)
            except ValueError:
                continue  # unknown sites are reported per-group below as unsupported
            if not site_adapter.is_auth_valid():
                auth_invalid_sites.add(site)
                db.insert_log(
                    "WARNING", "repair",
                    f"Site {site}: auth flagged invalid; skipping "
                    f"{per_site[site]} missing row(s) — re-authenticate in Settings",
                )
        result.sites_aborted.extend(sorted(auth_invalid_sites))

        for (site, handle), rows in groups.items():
            if site in aborted_sites or site in auth_invalid_sites:
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
            dead_pids: set[str] = set()  # pids gallery-dl positively reported gone
            for ci, chunk in enumerate(chunk_list):
                urls = [u for (u, _, _) in chunk]
                run_rows.extend(r for (_, _, prows) in chunk for r in prows)
                for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
                    t0 = time.time()
                    proc = _run_batch(urls, config, adapter)
                    dt = time.time() - t0
                    stderr = proc.stderr or ""
                    dead_pids |= _not_found_pids(stderr, [pid for (_, pid, _) in chunk])
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
                            # Never retry into a still-hot rate window.
                            if is_site_paused(site, config.rate_limit):
                                _wait_for_unpause(site, config)
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
                    if mult >= config.rate_limit.pause_threshold:
                        # Sustained stress: give the next chunk its own fresh
                        # upstream window instead of the short cooldown, which
                        # would pile successive chunks into one spent budget.
                        _wait_for_rate_window(site, config)
                    else:
                        delay = random.uniform(*cooldown) * mult
                        db.insert_log(
                            "INFO", "repair",
                            f"cooldown {delay:.0f}s (multiplier {mult:.1f})",
                        )
                        time.sleep(delay)

            moved = _relocate_renamed(nas_path, handle, downloaded) if downloaded else 0
            resolved_before = result.rows_recovered + result.rows_updated
            removed_before = result.rows_removed_upstream
            _reconcile_artist(nas_path, handle, run_rows, result, dead_pids)
            resolved_gained = (result.rows_recovered + result.rows_updated) - resolved_before
            removed_gained = result.rows_removed_upstream - removed_before
            if resolved_gained == 0 and removed_gained == 0 and had_rc0 and run_rows:
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
    nas_path: Path,
    handle: str,
    rows: list[MissingRow],
    result: RepairResult,
    dead_pids: set[str] | None = None,
) -> None:
    """Match an artist's attempted rows against the loose files gallery-dl wrote.

    Rows whose own post URL was positively reported gone upstream (``dead_pids``,
    from gallery-dl's per-URL not-found errors) are deleted unconditionally.
    Remaining unfetched rows are only deleted under the artist-level safeguard
    below — that inference needs at least one proof of reachability.
    """
    dead_pids = dead_pids or set()
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

    confirmed_dead: list[int] = []  # own URL reported 404/not found/deleted
    to_delete: list[int] = []       # yielded nothing; artist-level inference
    dead_by_pid: dict[str, int] = {}  # pid -> row id, for the audit log
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
        elif pid and pid in dead_pids:
            confirmed_dead.append(r.file_id)
            dead_by_pid[pid] = r.file_id
        else:
            to_delete.append(r.file_id)

    # Positive evidence: the platform itself said these posts are gone. Safe to
    # purge even when nothing else about the artist was reachable this run.
    removed = 0
    if confirmed_dead:
        removed = db.delete_file_records(confirmed_dead)
        result.rows_removed_upstream += removed
        result.rows_deleted += removed
        pids = sorted(dead_by_pid)
        shown = ", ".join(pids[:20]) + ("…" if len(pids) > 20 else "")
        db.insert_log(
            "INFO", "repair",
            f"{handle}: purged {removed} row(s) confirmed removed upstream "
            f"(post id(s): {shown})",
        )

    # Deletion safeguard: only delete when at least one row proved gallery-dl
    # could reach this artist's posts. A systemic failure (rename, auth) keeps
    # all rows so the operator can intervene.
    deleted = removed
    if recovered + updated > 0:
        deleted += db.delete_file_records(to_delete)
        result.rows_deleted += deleted - removed
    elif to_delete:
        result.artists_no_recovery += 1
        db.insert_log(
            "WARNING", "repair",
            f"{handle}: no files recovered from {len(to_delete)} row(s); keeping all (safeguard)",
        )

    db.insert_log(
        "INFO", "repair",
        f"reconcile {handle}: {recovered} recovered, {updated} updated, "
        f"{deleted} deleted ({removed} removed upstream), {ambiguous} ambiguous "
        f"(of {len(rows)} attempted)",
    )


def _store_result(result: RepairResult, run_start: float) -> None:
    db.set_state("repair:last_result", json.dumps(asdict(result)))
    resolved = result.rows_recovered + result.rows_updated
    elapsed = max(time.time() - run_start, 0.001)
    rate = resolved / elapsed * 60
    level = "INFO" if resolved > 0 or result.missing_before == 0 else "WARNING"
    parts = [
        f"Repair done in {elapsed:.0f}s: {resolved} recovered/updated ({rate:.1f} files/min)",
        f"{result.rows_deleted} deleted"
        + (
            f" ({result.rows_removed_upstream} confirmed removed upstream)"
            if result.rows_removed_upstream
            else ""
        ),
        f"{result.rows_ambiguous} ambiguous",
        f"{result.rows_unsupported} unsupported",
    ]
    if result.sites_aborted:
        parts.append("sites aborted: " + ", ".join(sorted(set(result.sites_aborted))))
    if result.aborted_reason:
        parts.append(f"aborted: {result.aborted_reason}")
    db.insert_log(level, "repair", ", ".join(parts))
