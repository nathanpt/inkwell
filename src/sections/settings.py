from __future__ import annotations

import streamlit as st

from src import db
from src.cookie_manager import get_file_info, save_file
from src.url_validator import get_registry

from src.sites.xcom import XComAdapter
from src.sites.pixiv import PixivAdapter
from src.sites.deviantart import DeviantArtAdapter


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

    # Per-site auth management
    st.divider()
    st.subheader("Site Authentication")

    _render_xcom_auth(conn, config)
    _render_pixiv_auth(conn, config)
    _render_deviantart_auth(conn, config)


def _render_xcom_auth(conn, config):
    adapter = XComAdapter()
    with st.expander("X.com", expanded=True):
        from src.sites.xcom import COOKIES_PATH
        _render_cookie_section(
            conn, config, adapter, COOKIES_PATH,
            label="X.com cookies.txt",
        )


def _render_pixiv_auth(conn, config):
    adapter = PixivAdapter()
    with st.expander("Pixiv"):
        from src.sites.pixiv import TOKEN_PATH
        info = get_file_info(TOKEN_PATH)
        if info["exists"]:
            import datetime
            modified = datetime.datetime.fromtimestamp(info["modified"])
            st.caption(f"Refresh token uploaded: {modified.strftime('%Y-%m-%d %H:%M')}")
        else:
            st.warning("No Pixiv refresh token uploaded.")

        token = st.text_input("Pixiv refresh token", type="password", key="pixiv_token")
        if st.button("Save Pixiv Token", key="save_pixiv_token"):
            if token:
                save_file(TOKEN_PATH, token.encode(), conn, adapter)
                st.success("Pixiv refresh token saved!")
                st.rerun()


def _render_deviantart_auth(conn, config):
    adapter = DeviantArtAdapter()
    with st.expander("DeviantArt"):
        from src.sites.deviantart import COOKIES_PATH as DA_COOKIES_PATH
        _render_cookie_section(
            conn, config, adapter, DA_COOKIES_PATH,
            label="DeviantArt cookies.txt",
        )


def _render_cookie_section(conn, config, adapter, path, label):
    info = get_file_info(path)
    if info["exists"]:
        import datetime
        modified = datetime.datetime.fromtimestamp(info["modified"])
        st.caption(f"Last uploaded: {modified.strftime('%Y-%m-%d %H:%M')} ({info['size']} bytes)")
    else:
        st.warning(f"No {label} uploaded yet.")

    uploaded = st.file_uploader(
        f"Upload {label}",
        type=["txt"],
        key=f"upload_{adapter.name}",
    )
    if uploaded is not None:
        save_file(path, uploaded.read(), conn, adapter)
        st.success(f"{label} uploaded successfully!")
        st.rerun()
