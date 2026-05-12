from __future__ import annotations

import logging

import bcrypt
import streamlit as st

from src.bootstrap import bootstrap
from src.cookie_manager import is_auth_valid, is_cookies_expired

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _init_session_state():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "conn" not in st.session_state:
        conn, config = bootstrap()
        st.session_state.conn = conn
        st.session_state.config = config


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
    conn = st.session_state.conn
    config = st.session_state.config

    if not is_auth_valid(conn):
        st.error("**RE-UPLOAD COOKIES** — Authentication session is invalid. Upload fresh cookies in Settings.", icon="🚨")
    elif is_cookies_expired(config.cookies.expiry_warning_days):
        st.warning(f"Cookies are older than {config.cookies.expiry_warning_days} days. Consider re-uploading.", icon="⚠️")


def _render_dashboard():
    _render_auth_banner()
    st.title("Inkwell")

    from src.sections.artists import render_artists
    from src.sections.downloads import render_downloads
    from src.sections.logs import render_logs
    from src.sections.settings import render_settings

    with st.expander("Artists", expanded=True):
        render_artists()

    with st.expander("Downloads"):
        render_downloads()

    with st.expander("Settings"):
        render_settings()

    with st.expander("Logs"):
        render_logs()


def main():
    _init_session_state()

    if not st.session_state.authenticated:
        _render_login()
        return

    _render_dashboard()


if __name__ == "__main__":
    main()
