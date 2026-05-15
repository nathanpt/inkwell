from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from src import db
from src.url_validator import get_registry

logger = logging.getLogger(__name__)


def render_downloads():
    registry = get_registry()

    # Job history
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
