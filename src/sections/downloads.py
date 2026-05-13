from __future__ import annotations

import threading

import streamlit as st

from src import db
from src.downloader import download_all, download_artist
from src.url_validator import get_registry


def render_downloads():
    conn = st.session_state.conn
    config = st.session_state.config
    registry = get_registry()

    # Download controls
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Download All Now"):
            t = threading.Thread(
                target=download_all,
                args=(conn, config, registry, "manual"),
                daemon=True,
            )
            t.start()
            st.info("Download started in background. Refresh to see progress.")
    with col2:
        artists = db.get_active_artists(conn)
        if artists:
            selected = st.selectbox(
                "Single artist",
                options=[(a.id, _artist_label(a, registry)) for a in artists],
                format_func=lambda x: x[1],
                key="single_artist_select",
            )
            if st.button("Download Selected"):
                artist = next(a for a in artists if a.id == selected[0])
                t = threading.Thread(
                    target=download_artist,
                    args=(conn, artist, config, registry, "manual"),
                    daemon=True,
                )
                t.start()
                st.info(f"Download started for {registry.get(artist.site).get_display_handle(artist)}")

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
        artist_row = conn.execute(
            "SELECT handle, site FROM artists WHERE id = ?", (job.artist_id,)
        ).fetchone()
        if artist_row:
            from src.models import Artist
            a = Artist(handle=artist_row["handle"], site=artist_row["site"])
            try:
                adapter = registry.get(a.site)
                display = adapter.get_display_handle(a)
            except ValueError:
                display = a.handle
        else:
            display = "unknown"
        status_emoji = {"success": "✅", "failed": "❌", "running": "⏳"}.get(job.status, "")
        st.markdown(
            f"{status_emoji} **{display}** — {job.status} | "
            f"{job.file_count} file(s) | "
            f"{_format_bytes(job.total_bytes)} | "
            f"triggered: {job.triggered_by} | "
            f"started: {job.started_at}"
        )
        if job.error_message:
            st.caption(f"Error: {job.error_message}")


def _artist_label(artist, registry) -> str:
    from src.models import Artist
    try:
        adapter = registry.get(artist.site)
        return adapter.get_display_handle(artist)
    except ValueError:
        return artist.handle


def _format_bytes(n: int) -> str:
    if n == 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
