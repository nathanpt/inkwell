from __future__ import annotations

import logging
import threading

import pandas as pd
import streamlit as st

from src import db
from src.downloader import download_all, download_artist
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

    # Tier 1: Global action
    if st.button("Download All Now", type="primary", use_container_width=True):
        _run_in_thread(download_all, extra_args=(config, registry, "manual"))
        st.info("Download started in background. Refresh to see progress.")

    # Tier 2: Single-artist download
    st.divider()
    artists = db.get_active_artists()
    if artists:
        col_select, col_action = st.columns([3, 1], vertical_alignment="bottom")
        with col_select:
            selected = st.selectbox(
                "Single artist",
                options=[(a.id, _artist_label(a, registry)) for a in artists],
                format_func=lambda x: x[1],
                key="single_artist_select",
            )
        with col_action:
            if st.button("Download Selected", use_container_width=True):
                artist = next(a for a in artists if a.id == selected[0])
                _run_in_thread(download_artist, extra_args=(artist, config, registry, "manual"))
                st.info(f"Download started for {registry.get(artist.site).get_display_handle(artist)}")

    # Job history
    st.divider()
    col_header, col_clear = st.columns([4, 1])
    with col_header:
        st.subheader("Job History")
    with col_clear:
        running = db.get_jobs_by_status("running")
        if running:
            st.markdown(
                '<meta http-equiv="refresh" content="10">',
                unsafe_allow_html=True,
            )
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
