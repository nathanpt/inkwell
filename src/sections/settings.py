from __future__ import annotations

import streamlit as st

from src import db
from src.cookie_manager import get_cookie_info, save_cookies


def render_settings():
    conn = st.session_state.conn
    config = st.session_state.config

    # Config display
    st.subheader("Configuration")
    st.code(f"""
NAS path: {config.nas.mount_path}
Schedule: {config.schedule.cron}
Retry attempts: {config.download.retry_attempts}
Timeout: {config.download.timeout}s
Cookie expiry warning: {config.cookies.expiry_warning_days} days
Log retention: {config.retention.log_days} days
""", language="yaml")

    # Cookie management
    st.divider()
    st.subheader("Cookies")

    cookie_info = get_cookie_info()
    if cookie_info["exists"]:
        import datetime
        modified = datetime.datetime.fromtimestamp(cookie_info["modified"])
        st.caption(f"Last uploaded: {modified.strftime('%Y-%m-%d %H:%M')} ({cookie_info['size']} bytes)")
    else:
        st.warning("No cookies uploaded yet.")

    uploaded = st.file_uploader(
        "Upload cookies.txt",
        type=["txt"],
        key="cookie_upload",
    )
    if uploaded is not None:
        save_cookies(conn, uploaded.read())
        st.success("Cookies uploaded successfully!")
        st.rerun()
