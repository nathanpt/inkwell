from __future__ import annotations

from datetime import datetime

import streamlit as st

from src import db


def _format_export(
    logs: list[dict],
    level: str | None,
    source: str | None,
    limit: int,
) -> str:
    """Render filtered logs as a plain-text export for sharing/debugging.

    Newest-first to match the on-screen order. job_id/artist_id are included
    inline when present so exported logs cross-reference jobs/artists.
    """
    lines = [
        "# Inkwell log export",
        f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"# Filters: level={level or 'All'} source={source or 'All'} limit={limit}",
        f"# Entries: {len(logs)} (newest first)",
        "",
    ]
    for e in logs:
        meta = ""
        if e.get("job_id"):
            meta += f" (job={e['job_id']})"
        if e.get("artist_id"):
            meta += f" (artist={e['artist_id']})"
        lines.append(
            f"[{e['level']}] {e['timestamp']} [{e['source']}] {e['message']}{meta}"
        )
    return "\n".join(lines) + "\n"


def render_logs():
    col1, col2 = st.columns(2)
    with col1:
        level_filter = st.selectbox(
            "Level",
            options=["All", "INFO", "WARNING", "ERROR"],
            key="log_level_filter",
        )
    with col2:
        source_filter = st.selectbox(
            "Source",
            options=["All", "downloader", "scheduler", "bootstrap"],
            key="log_source_filter",
        )

    limit = st.slider("Max entries", min_value=10, max_value=500, value=100, step=10)

    level = None if level_filter == "All" else level_filter
    source = None if source_filter == "All" else source_filter
    logs = db.get_logs(level=level, source=source, limit=limit)

    if not logs:
        st.info("No log entries.")
        return

    st.download_button(
        "Export Logs",
        data=_format_export(logs, level, source, limit),
        file_name=f"inkwell-logs-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt",
        mime="text/plain",
        help="Download the currently-filtered log entries as a plain-text file.",
    )

    for entry in logs:
        level_colors = {"INFO": "🟢", "WARNING": "🟡", "ERROR": "🔴"}
        icon = level_colors.get(entry["level"], "")
        st.markdown(
            f"{icon} **{entry['level']}** [{entry['source']}] "
            f"{entry['timestamp']}: {entry['message']}"
        )
