from __future__ import annotations

import streamlit as st

from src import db


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

    for entry in logs:
        level_colors = {"INFO": "🟢", "WARNING": "🟡", "ERROR": "🔴"}
        icon = level_colors.get(entry["level"], "")
        st.markdown(
            f"{icon} **{entry['level']}** [{entry['source']}] "
            f"{entry['timestamp']}: {entry['message']}"
        )
