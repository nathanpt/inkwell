from __future__ import annotations

import logging
import math
from pathlib import Path

import streamlit as st

from src import db, gallery_media

logger = logging.getLogger(__name__)

PAGE_SIZE = 20
GRID_COLS = 4


def _format_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def _on_artist_change() -> None:
    """Reset page, year filter, and any open detail when the artist changes."""
    st.session_state["gallery_page"] = 0
    st.session_state["gallery_years"] = []
    st.session_state.pop("gallery_selected", None)


def _reset_page() -> None:
    st.session_state["gallery_page"] = 0


def render_gallery() -> None:
    config = st.session_state.config
    nas_path = Path(config.nas.mount_path)

    artists = db.get_active_artists()
    if not artists:
        st.info("No artists tracked yet. Add one on the Artists tab.")
        return

    handles = {a.id: a.handle for a in artists}
    options = [a.id for a in artists]

    # --- Filters ---
    col_artist, col_year, col_sort = st.columns([2, 2, 1])

    with col_artist:
        if (
            "gallery_artist" not in st.session_state
            or st.session_state["gallery_artist"] not in options
        ):
            st.session_state["gallery_artist"] = options[0]
        aid = st.selectbox(
            "Artist",
            options=options,
            format_func=lambda i: handles[i],
            key="gallery_artist",
            on_change=_on_artist_change,
        )
    handle = handles[aid]

    with col_year:
        years = db.distinct_years(aid)
        selected_years = st.multiselect(
            "Year(s)",
            options=years,
            key="gallery_years",
            on_change=_reset_page,
        )

    with col_sort:
        st.selectbox(
            "Sort",
            options=["Newest first", "Oldest first"],
            key="gallery_sort",
            on_change=_reset_page,
        )
    sort = st.session_state["gallery_sort"]

    year_filter = selected_years or None

    # --- Stats bar ---
    count, total_bytes = db.gallery_stats(aid, years=year_filter)
    st.caption(f"{count:,} files · {_format_bytes(total_bytes)}")

    if count == 0:
        st.info("No media for this selection.")
        return

    # --- Detail view ---
    selected_id = st.session_state.get("gallery_selected")
    if selected_id is not None:
        row = db.get_file(selected_id)
        if row and row["artist_id"] == aid:
            _render_detail(nas_path, handle, row)
        else:
            st.session_state.pop("gallery_selected", None)

    # --- Pagination ---
    total_pages = max(1, math.ceil(count / PAGE_SIZE))
    page = min(st.session_state.get("gallery_page", 0), total_pages - 1)
    st.session_state["gallery_page"] = page

    if total_pages > 1:
        col_prev, col_info, col_next = st.columns([1, 2, 1])
        with col_prev:
            if st.button("Prev", disabled=(page == 0), key="gallery_page_prev"):
                st.session_state.gallery_page = page - 1
                st.rerun()
        with col_info:
            st.markdown(
                f"<div style='text-align:center'>Page {page + 1} of {total_pages}</div>",
                unsafe_allow_html=True,
            )
        with col_next:
            if st.button(
                "Next",
                disabled=(page >= total_pages - 1),
                key="gallery_page_next",
            ):
                st.session_state.gallery_page = page + 1
                st.rerun()

    # --- Thumbnail grid ---
    rows = db.get_recent_files(
        artist_id=aid, years=year_filter, limit=PAGE_SIZE, offset=page * PAGE_SIZE
    )
    if sort == "Oldest first":
        rows = list(reversed(rows))

    image_rows = [r for r in rows if gallery_media.is_image(r["filename"])]
    if not image_rows:
        st.info("No media for this selection.")
        return

    for i in range(0, len(image_rows), GRID_COLS):
        chunk = image_rows[i : i + GRID_COLS]
        cols = st.columns(GRID_COLS)
        for col, row in zip(cols, chunk):
            with col:
                _render_cell(nas_path, handle, row)


def _render_detail(nas_path: Path, handle: str, row: dict) -> None:
    year = row["year"]
    filename = row["filename"]

    _, col_close = st.columns([6, 1])
    if col_close.button("Close", key="gallery_detail_close"):
        st.session_state.pop("gallery_selected", None)
        st.rerun()

    data = gallery_media.read_media_bytes(nas_path, handle, year, filename)
    if data is None:
        st.warning("File not found on disk")
        return

    source = (
        "zip"
        if gallery_media.source_zip_path(nas_path, handle, year).is_file()
        else "live"
    )
    st.image(data, use_container_width=True)
    st.caption(
        f"{filename} · {_format_bytes(row['size_bytes'])} · {year} · source: {source}"
    )


def _render_cell(nas_path: Path, handle: str, row: dict) -> None:
    fid = row["id"]
    year = row["year"]
    filename = row["filename"]
    try:
        thumb = gallery_media.get_thumbnail(nas_path, handle, year, filename)
        st.image(thumb, use_container_width=True)
    except Exception:
        logger.exception("Thumbnail failed for %s/%s", handle, filename)
        st.caption("(unavailable)")
    if st.button("View", key=f"view:{fid}"):
        st.session_state["gallery_selected"] = fid
        st.rerun()
