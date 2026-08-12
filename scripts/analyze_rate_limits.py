#!/usr/bin/env python3
"""Offline rate-limit / repair analyzer for the Inkwell SQLite DB.

Read-only: point it at any copy of ``inkwell.db`` (e.g. one ``docker cp``-ed off
the production container) to get per-site 429 frequency, aborts, pause waits,
repair chunk throughput, limiter state, and repair-run summaries. Uses only the
standard library and imports nothing from ``src`` so it runs anywhere.

    python3 scripts/analyze_rate_limits.py --db /tmp/inkwell.db [--days 30] [--site x.com]

Signals are parsed from the ``logs`` table against the exact message formats
emitted by ``src/repair.py`` and ``src/downloader.py``; the limiter snapshot comes
from the ``state`` table's ``rate_limit:<site>`` keys.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from collections import defaultdict
from typing import Any

DEFAULT_DB = "/app/data/inkwell.db"

# --- message patterns (literals from src/repair.py / src/downloader.py) ---
# repair: "{site}: rate-limited on {handle} chunk i/n (dt s); backoff b s (attempt a/m)"
REPAIR_429 = re.compile(
    r"^(?P<site>[^:\s]+): rate-limited on \S+ chunk \d+/\d+ "
    r"\([\d.]+s\); backoff \d+s \(attempt \d+/\d+\)$"
)
# downloader: "Rate limited for {handle}, skipping retries" (site via artist_id join)
DOWNLOAD_429 = re.compile(r"^Rate limited for \S+, skipping retries$")
# repair: "{site}: rate-limit retries exhausted; skipping remaining work on site"
REPAIR_ABORT = re.compile(r"^(?P<site>[^:\s]+): rate-limit retries exhausted")
# repair: "Site {site} rate-limit paused; waiting up to {n}s for the rate window to clear"
REPAIR_PAUSE = re.compile(r"^Site (?P<site>\S+) rate-limit paused;")
# repair INFO: "{handle} chunk i/n: {k} post(s) in {dt}s (rc={rc}, {f} file(s) downloaded)"
REPAIR_CHUNK = re.compile(
    r"^(?P<handle>\S+) chunk (?P<i>\d+)/(?P<n>\d+): (?P<k>\d+) post\(s\) "
    r"in (?P<dt>[\d.]+)s \(rc=(?P<rc>-?\d+), (?P<f>\d+) file\(s\) downloaded\)$"
)
# repair: "Repair done in {e}s: {r} recovered/updated ({rate} files/min), {d} deleted, ..."
REPAIR_RUN = re.compile(
    r"^Repair done in (?P<e>[\d.]+)s: (?P<r>\d+) recovered/updated "
    r"\((?P<rate>[\d.]+) files/min\), (?P<d>\d+) deleted"
)
# optional trailing "sites aborted: a, b" on the repair-run line
REPAIR_RUN_ABORT = re.compile(r"sites aborted: (?P<sites>.+)$")


def _load_artist_maps(conn: sqlite3.Connection) -> tuple[dict[int, str], dict[str, str]]:
    """Return (artist_id -> site) and (handle -> site) maps.

    A handle that maps to multiple sites is attributed to the first seen; this is
    a known approximation for chunk-throughput attribution (the chunk log carries
    a handle, not a site). Missing/unknown handles fall back to ``"?"``.
    """
    artist_site: dict[int, str] = {}
    handle_site: dict[str, str] = {}
    try:
        for aid, site, handle in conn.execute(
            "SELECT id, site, handle FROM artists"
        ).fetchall():
            artist_site[aid] = site
            handle_site.setdefault(handle, site)
    except sqlite3.OperationalError:
        pass  # no artists table in a minimal/synthetic DB
    return artist_site, handle_site


def analyze(
    conn: sqlite3.Connection, days: int = 30, site_filter: str | None = None
) -> dict[str, Any]:
    """Aggregate rate-limit signals from the logs/state tables into a report."""
    cutoff = f"datetime('now', '-{int(days)} days')"
    artist_site, handle_site = _load_artist_maps(conn)

    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    repair_runs: list[dict[str, Any]] = []

    def bucket(day: str, site: str) -> dict[str, Any]:
        key = (day, site)
        b = buckets.get(key)
        if b is None:
            b = {
                "hits429": 0,
                "aborts": 0,
                "pause_waits": 0,
                "chunks": 0,
                "posts": 0,
                "files": 0,
                "chunk_seconds": 0.0,
            }
            buckets[key] = b
        return b

    query = (
        "SELECT timestamp, level, source, message, artist_id FROM logs "
        "WHERE source IN ('repair', 'downloader') AND timestamp >= "
        + cutoff
        + " ORDER BY timestamp"
    )
    for timestamp, level, source, message, artist_id in conn.execute(query).fetchall():
        if not message:
            continue
        day = (timestamp or "")[:10]  # "YYYY-MM-DD"

        if source == "repair" and REPAIR_429.match(message):
            bucket(day, REPAIR_429.match(message).group("site"))["hits429"] += 1
        elif DOWNLOAD_429.match(message):
            site = artist_site.get(artist_id, "?")
            bucket(day, site)["hits429"] += 1
        elif source == "repair" and REPAIR_ABORT.match(message):
            bucket(day, REPAIR_ABORT.match(message).group("site"))["aborts"] += 1
        elif source == "repair" and REPAIR_PAUSE.match(message):
            bucket(day, REPAIR_PAUSE.match(message).group("site"))["pause_waits"] += 1
        elif source == "repair" and (mc := REPAIR_CHUNK.match(message)):
            site = handle_site.get(mc.group("handle"), "?")
            b = bucket(day, site)
            b["chunks"] += 1
            b["posts"] += int(mc.group("k"))
            b["files"] += int(mc.group("f"))
            b["chunk_seconds"] += float(mc.group("dt"))
        elif source == "repair" and (mr := REPAIR_RUN.match(message)):
            run = {
                "timestamp": timestamp,
                "elapsed": float(mr.group("e")),
                "resolved": int(mr.group("r")),
                "rate": float(mr.group("rate")),
                "deleted": int(mr.group("d")),
                "aborted": [],
            }
            mab = REPAIR_RUN_ABORT.search(message)
            if mab:
                run["aborted"] = [s.strip() for s in mab.group("sites").split(",") if s.strip()]
            repair_runs.append(run)

    days_out = []
    for (day, site) in sorted(buckets):
        if site_filter and site != site_filter:
            continue
        b = buckets[(day, site)]
        chunks = b["chunks"]
        days_out.append(
            {
                "day": day,
                "site": site,
                "hits429": b["hits429"],
                "aborts": b["aborts"],
                "pause_waits": b["pause_waits"],
                "chunks": chunks,
                "posts": b["posts"],
                "files": b["files"],
                "avg_s_per_chunk": round(b["chunk_seconds"] / chunks, 1) if chunks else 0.0,
            }
        )

    runs_out = []
    for r in repair_runs:
        if site_filter and site_filter not in r["aborted"]:
            continue
        runs_out.append(r)

    limiter = []
    for key, value in conn.execute(
        "SELECT key, value FROM state WHERE key LIKE 'rate_limit:%'"
    ).fetchall():
        site = key.split(":", 1)[1]
        try:
            data = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            continue
        limiter.append(
            {
                "site": site,
                "hit_count": int(data.get("hit_count", 0)),
                "multiplier": float(data.get("cooldown_multiplier", 1.0)),
                "last_hit_ts": float(data.get("last_hit_ts", 0.0)),
            }
        )
    limiter.sort(key=lambda s: s["site"])

    return {
        "generated_at": time.time(),
        "days": days_out,
        "repair_runs": runs_out,
        "limiter_state": limiter,
        "days_window": int(days),
        "site_filter": site_filter,
    }


def _fmt_age(ts: float) -> str:
    if not ts:
        return "never"
    age = time.time() - ts
    if age < 0:
        return "future"
    if age < 60:
        return f"{age:.0f}s ago"
    if age < 3600:
        return f"{age / 60:.1f}m ago"
    if age < 86400:
        return f"{age / 3600:.1f}h ago"
    return f"{age / 86400:.1f}d ago"


def render(report: dict[str, Any]) -> str:
    """Render a report dict as three text sections."""
    lines: list[str] = []
    window = report["days_window"]
    site_filter = report["site_filter"]
    scope = f"site={site_filter}" if site_filter else "all sites"
    lines.append(f"=== Inkwell rate-limit report (last {window} days, {scope}) ===")
    lines.append("")

    # Section 1: limiter state
    lines.append("--- Limiter state (rate_limit:<site>) ---")
    limiter = report["limiter_state"]
    if not limiter:
        lines.append("(no rate_limit state recorded)")
    else:
        lines.append(f"{'site':<14}{'hits':>6}{'multiplier':>12}   {'last hit':<14}")
        for s in limiter:
            lines.append(
                f"{s['site']:<14}{s['hit_count']:>6}{s['multiplier']:>12.2f}   "
                f"{_fmt_age(s['last_hit_ts']):<14}"
            )
    lines.append("")

    # Section 2: per-day activity
    lines.append("--- Per-day activity ---")
    days = report["days"]
    if not days:
        lines.append(f"No rate-limit activity in the last {window} days.")
    else:
        lines.append(
            f"{'date':<12}{'site':<12}{'429':>5}{'aborts':>7}{'pauses':>8}"
            f"{'chunks':>8}{'posts':>7}{'files':>7}{'avg_s/chk':>11}"
        )
        for d in days:
            lines.append(
                f"{d['day']:<12}{d['site']:<12}{d['hits429']:>5}{d['aborts']:>7}"
                f"{d['pause_waits']:>8}{d['chunks']:>8}{d['posts']:>7}{d['files']:>7}"
                f"{d['avg_s_per_chunk']:>11.1f}"
            )
    lines.append("")

    # Section 3: repair runs
    lines.append("--- Repair runs ---")
    runs = report["repair_runs"]
    if not runs:
        lines.append("No repair runs recorded.")
    else:
        lines.append(
            f"{'timestamp':<20}{'elapsed':>9}{'resolved':>10}{'deleted':>9}"
            f"{'files/min':>11}   {'sites aborted'}"
        )
        for r in runs:
            ts = (r["timestamp"] or "")[:19]
            lines.append(
                f"{ts:<20}{int(r['elapsed']):>8}s{r['resolved']:>10}{r['deleted']:>9}"
                f"{r['rate']:>11.1f}   {', '.join(r['aborted'])}"
            )
    lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB, help=f"path to inkwell.db (default: {DEFAULT_DB})")
    parser.add_argument("--days", type=int, default=30, help="lookback window in days (default: 30)")
    parser.add_argument("--site", default=None, help="optional site filter (e.g. x.com)")
    args = parser.parse_args(argv)

    # Read-only open: refuse to write even on a misclick.
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        report = analyze(conn, days=args.days, site_filter=args.site)
    finally:
        conn.close()

    print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
