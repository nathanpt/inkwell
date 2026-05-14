from __future__ import annotations

import logging

import bcrypt
import streamlit as st

from src import db
from src.bootstrap import bootstrap
from src.url_validator import get_registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _init_session_state():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "bootstrapped" not in st.session_state:
        conn, config = bootstrap()
        db.configure(db.DEFAULT_DB_PATH)
        conn.close()

        st.session_state.config = config
        st.session_state.bootstrapped = True

        # Initialize site registry
        get_registry()

        from src.scheduler import create_scheduler

        scheduler = create_scheduler(config)
        scheduler.start()
        st.session_state.scheduler = scheduler


def _check_password(password: str) -> bool:
    config = st.session_state.config
    if not config.auth.password_hash:
        return True
    return bcrypt.checkpw(
        password.encode(), config.auth.password_hash.encode()
    )


def _render_login():
    st.title("Inkwell")
    st.subheader("Enter password to continue")
    password = st.text_input("Password", type="password", key="login_password")
    if st.button("Login"):
        if _check_password(password):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Invalid password")


def _render_auth_banner():
    config = st.session_state.config
    registry = get_registry()

    from src.cookie_manager import is_file_expired

    for adapter in registry.all_adapters():
        if not adapter.is_auth_valid():
            st.error(f"**RE-AUTHENTICATE {adapter.name.upper()}** — Credentials are invalid. Upload fresh credentials in Settings.", icon="🚨")
        else:
            for auth_file in adapter.get_auth_files():
                if is_file_expired(auth_file, config.cookies.expiry_warning_days):
                    st.warning(f"{adapter.name}: credentials are older than {config.cookies.expiry_warning_days} days. Consider re-uploading.", icon="⚠️")


def _render_dashboard():
    _render_auth_banner()
    st.title("Inkwell")

    from src.sections.artists import render_artists
    from src.sections.downloads import render_downloads
    from src.sections.logs import render_logs
    from src.sections.settings import render_settings

    tab_artists, tab_downloads, tab_settings, tab_logs = st.tabs(
        ["Artists", "Downloads", "Settings", "Logs"]
    )

    with tab_artists:
        render_artists()

    with tab_downloads:
        render_downloads()

    with tab_settings:
        render_settings()

    with tab_logs:
        render_logs()


def main():
    _init_session_state()

    if not st.session_state.authenticated:
        _render_login()
        return

    _render_dashboard()


if __name__ == "__main__":
    main()
