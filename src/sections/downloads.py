from __future__ import annotations

import threading

import pandas as pd
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

    rows = db.get_jobs_with_artist_info(
        conn,
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


def _artist_label(artist, registry) -> str:
    from src.models import Artist
    try:
        adapter = registry.get(artist.site)
        return adapter.get_display_handle(artist)
    except ValueError:
        return artist.handle


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
