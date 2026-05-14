from __future__ import annotations

import threading

import streamlit as st

from src import db
from src.cookie_manager import get_file_info, save_file
from src.url_validator import get_registry

from src.sites.xcom import XComAdapter
from src.sites.pixiv import PixivAdapter
from src.sites.deviantart import DeviantArtAdapter


def render_settings():
    config = st.session_state.config

    # Config display
    st.subheader("Configuration")
    st.code(f"""
NAS path: {config.nas.display_path or config.nas.mount_path}
Schedule: {config.schedule.cron}
Time window: {config.schedule.time_window_start or 'any'} - {config.schedule.time_window_end or 'any'}
Stale threshold: {config.schedule.stale_threshold_days or 'all artists'}{' days' if config.schedule.stale_threshold_days else ''}
Retry attempts: {config.download.retry_attempts}
Timeout: {config.download.timeout}s
Cookie expiry warning: {config.cookies.expiry_warning_days} days
Log retention: {config.retention.log_days} days
""", language="yaml")

    # Per-site auth management
    st.divider()
    st.subheader("Site Authentication")

    _render_xcom_auth()
    _render_pixiv_auth()
    _render_deviantart_auth()

    # Storage management
    st.divider()
    st.subheader("Storage")

    col_zip, col_info = st.columns([1, 2])
    with col_zip:
        if st.button("Zip All Artists Now", use_container_width=True):
            from src import zipper

            config = st.session_state.config

            def _run():
                try:
                    results = zipper.zip_all_artists(config)
                    total = sum(len(v) for v in results.values())
                    if total:
                        db.insert_log("INFO", "settings", f"Retroactive zip completed: {total} archive(s) across {len(results)} artist(s)")
                    else:
                        db.insert_log("INFO", "settings", "Retroactive zip: nothing to zip")
                except Exception as e:
                    db.insert_log("ERROR", "settings", f"Retroactive zip failed: {e}")

            threading.Thread(target=_run, daemon=True).start()
            st.info("Zip started in background. Check Logs for results.")
    with col_info:
        st.caption("Compresses loose files into per-year ZIP archives for each artist, reducing NAS small-file load.")


def _render_xcom_auth():
    adapter = XComAdapter()
    with st.expander("X.com", expanded=True):
        from src.sites.xcom import COOKIES_PATH
        _render_cookie_section(
            adapter, COOKIES_PATH,
            label="X.com cookies.txt",
        )


def _render_pixiv_auth():
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
                save_file(TOKEN_PATH, token.encode(), adapter)
                st.success("Pixiv refresh token saved!")
                st.rerun()


def _render_deviantart_auth():
    adapter = DeviantArtAdapter()
    with st.expander("DeviantArt"):
        from src.sites.deviantart import COOKIES_PATH as DA_COOKIES_PATH
        _render_cookie_section(
            adapter, DA_COOKIES_PATH,
            label="DeviantArt cookies.txt",
        )


def _render_cookie_section(adapter, path, label):
    info = get_file_info(path)
    if info["exists"]:
        import datetime
        modified = datetime.datetime.fromtimestamp(info["modified"])
        st.caption(f"Last uploaded: {modified.strftime('%Y-%m-%d %H:%M')} ({info['size']} bytes)")
    else:
        st.warning(f"No {label} uploaded yet.")

    key = f"upload_{adapter.name}"

    def _on_upload():
        uploaded = st.session_state[key]
        if uploaded is not None:
            save_file(path, uploaded.read(), adapter)
            st.session_state[key] = None

    st.file_uploader(
        f"Upload {label}",
        type=["txt"],
        key=key,
        on_change=_on_upload,
    )
