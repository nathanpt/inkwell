from __future__ import annotations

from src.sites.base import SiteAdapter, SiteRegistry, create_registry


def validate_url(url: str) -> tuple[str, str, SiteAdapter]:
    """Validate a URL against all registered site adapters.

    Returns (handle, normalized_url, adapter).
    Raises ValueError if no adapter matches.
    """
    registry = _get_registry()
    adapter = registry.match_url(url)
    if not adapter:
        raise ValueError(
            "Invalid URL. Supported sites: x.com, pixiv.net/users, deviantart.com"
        )
    handle, normalized_url = adapter.parse_url(url)
    return handle, normalized_url, adapter


def get_registry() -> SiteRegistry:
    return _get_registry()


def _get_registry() -> SiteRegistry:
    try:
        import streamlit as st
        if "site_registry" not in st.session_state:
            st.session_state.site_registry = create_registry()
        return st.session_state.site_registry
    except Exception:
        return create_registry()
