from __future__ import annotations

import logging
import threading

import pandas as pd
import streamlit as st

from src import db
from src.downloader import download_all
from src.url_validator import get_registry

logger = logging.getLogger(__name__)


def _run_in_thread(target, extra_args=None):
    """Run target in a background thread."""
    def wrapper():
        try:
            target(*extra_args)
        except Exception as e:
            logger.exception("Background download thread crashed: %s", e)
            try:
                db.insert_log("ERROR", "downloader", f"Thread crashed: {e}")
            except Exception:
                pass

    t = threading.Thread(target=wrapper, daemon=True)
    t.start()
    return t


def render_downloads():
    config = st.session_state.config
    registry = get_registry()

    # Last run summary
    summary = db.get_last_run_summary()
    if summary:
        _render_run_summary(summary, registry)

    # Stale artist summary
    artists = db.get_active_artists()
    never_scanned = [a for a in artists if not a.last_scan_at]
    if never_scanned:
        names = ", ".join(
            registry.get(a.site).get_display_handle(a) for a in never_scanned
        )
        st.warning(f"**Never downloaded:** {names}", icon="⬇️")

    # Global action
    if st.button("Download All Now", type="primary", use_container_width=True):
        _run_in_thread(download_all, extra_args=(config, registry, "manual"))
        st.info("Download started in background. Refresh to see progress.")

    # Job history
    st.divider()
    col_header, col_clear = st.columns([4, 1])
    with col_header:
        st.subheader("Job History")
    with col_clear:
        running = db.get_jobs_by_status("running")
        if running:
            if st.button("Clear Stuck Jobs"):
                cleaned = db.clean_orphaned_jobs()
                if cleaned:
                    st.success(f"Cleared {cleaned} stuck job(s)")
                    st.rerun()

    status_filter = st.selectbox(
        "Filter by status",
        options=["All", "success", "failed", "running"],
        key="job_status_filter",
    )

    rows = db.get_jobs_with_artist_info(
        status=None if status_filter == "All" else status_filter,
        limit=50,
    )

    if not rows:
        st.info("No jobs yet.")
        return

    records = []
    for r in rows:
        artist = _display_handle(r["artist_handle"], r["artist_site"], registry)
        records.append({
            "Artist": artist,
            "Status": r["status"],
            "Files": r["file_count"],
            "Size": _format_bytes(r["total_bytes"]),
            "Triggered By": r["triggered_by"],
            "Started": r["started_at"],
        })

    df = pd.DataFrame(records)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Show errors for failed jobs
    failed = [r for r in rows if r["status"] == "failed" and r["error_message"]]
    for r in failed:
        artist = _display_handle(r["artist_handle"], r["artist_site"], registry)
        st.caption(f"Error ({artist}): {r['error_message']}")


def _render_run_summary(summary: dict, registry) -> None:
    """Render a card summarizing the last completed download run."""
    trigger_label = "Scheduled" if summary["triggered_by"] == "scheduled" else "Manual"
    files_str = f"{summary['total_files']:,} new files" if summary["total_files"] else "no new files"
    size_str = _format_bytes(summary["total_bytes"]) if summary["total_bytes"] else ""

    parts = [files_str]
    if size_str:
        parts.append(size_str)
    detail = " | ".join(parts)

    header = f"**Last run** ({trigger_label}) — {summary['started_at'][:19]} to {summary['finished_at'][:19]}"
    stats = f"{summary['total_artists']} artists: {summary['succeeded']} succeeded, {summary['failed']} failed | {detail}"

    if summary["failed"] > 0:
        st.warning(f"{header}\n\n{stats}", icon=":material/warning:")
        for handle, error in summary["errors"]:
            st.caption(f"- {handle}: {error}")
    else:
        st.success(f"{header}\n\n{stats}", icon=":material/check_circle:")


def _display_handle(handle: str, site: str, registry) -> str:
    from src.models import Artist
    try:
        return registry.get(site).get_display_handle(Artist(handle=handle))
    except ValueError:
        return handle


def _format_bytes(n: int) -> str:
    if n == 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
