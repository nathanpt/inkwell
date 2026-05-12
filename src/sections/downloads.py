from __future__ import annotations

import threading

import streamlit as st

from src import db
from src.downloader import download_all, download_artist


def render_downloads():
    conn = st.session_state.conn
    config = st.session_state.config

    # Download controls
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Download All Now"):
            t = threading.Thread(
                target=download_all,
                args=(conn, config, "manual"),
                daemon=True,
            )
            t.start()
            st.info("Download started in background. Refresh to see progress.")
    with col2:
        artists = db.get_active_artists(conn)
        selected = st.selectbox(
            "Single artist",
            options=[(a.id, f"@{a.handle}") for a in artists],
            format_func=lambda x: x[1],
            key="single_artist_select",
        )
        if st.button("Download Selected"):
            artist = next(a for a in artists if a.id == selected[0])
            t = threading.Thread(
                target=download_artist,
                args=(conn, artist, config, "manual"),
                daemon=True,
            )
            t.start()
            st.info(f"Download started for @{artist.handle}")

    # Job history
    st.divider()
    st.subheader("Job History")

    status_filter = st.selectbox(
        "Filter by status",
        options=["All", "success", "failed", "running"],
        key="job_status_filter",
    )

    if status_filter == "All":
        jobs = db.get_recent_jobs(conn, limit=50)
    else:
        jobs = db.get_jobs_by_status(conn, status_filter)

    if not jobs:
        st.info("No jobs yet.")
        return

    for job in jobs:
        artist = conn.execute(
            "SELECT handle FROM artists WHERE id = ?", (job.artist_id,)
        ).fetchone()
        handle = artist["handle"] if artist else "unknown"
        status_emoji = {"success": "✅", "failed": "❌", "running": "⏳"}.get(job.status, "")
        st.markdown(
            f"{status_emoji} **@{handle}** — {job.status} | "
            f"{job.file_count} file(s) | "
            f"{_format_bytes(job.total_bytes)} | "
            f"triggered: {job.triggered_by} | "
            f"started: {job.started_at}"
        )
        if job.error_message:
            st.caption(f"Error: {job.error_message}")


def _format_bytes(n: int) -> str:
    if n == 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
