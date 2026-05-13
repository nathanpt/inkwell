from __future__ import annotations

import os
import shutil
from pathlib import Path

import streamlit as st

from src import db
from src.models import Artist
from src.url_validator import validate_url, get_registry


SITE_LABELS = {
    "x.com": "X",
    "pixiv": "Pixiv",
    "deviantart": "DeviantArt",
}

PAGE_SIZE = 20


def render_artists():
    config = st.session_state.config
    registry = get_registry()

    # Add artist form — input and button on the same row
    with st.form("add_artist"):
        col_input, col_btn = st.columns([4, 1], vertical_alignment="bottom")
        with col_input:
            url = st.text_input(
                "Artist URL",
                placeholder="https://x.com/handle, https://www.pixiv.net/users/123, https://www.deviantart.com/name",
            )
        with col_btn:
            submitted = st.form_submit_button("Add Artist", use_container_width=True)
        if submitted and url:
            try:
                handle, normalized_url, adapter = validate_url(url)
                existing = db.get_artist_by_url(normalized_url)
                if existing and existing.is_active:
                    st.error(f"Artist {adapter.get_display_handle(Artist(handle=handle))} is already tracked")
                elif existing and not existing.is_active:
                    db.deactivate_artist(existing.id)
                    st.success(f"Reactivated {adapter.get_display_handle(Artist(handle=handle))}")
                    st.rerun()
                else:
                    artist = Artist(handle=handle, site=adapter.name, source_url=normalized_url)
                    db.insert_artist(artist)
                    st.success(f"Added {adapter.get_display_handle(Artist(handle=handle))} ({SITE_LABELS.get(adapter.name, adapter.name)})")
                    st.rerun()
            except ValueError as e:
                st.error(str(e))

    # Artist list
    artists = db.get_active_artists()
    if not artists:
        st.info("No artists tracked yet. Add one above.")
        return

    disk_usage = _scan_disk_usage(Path(config.nas.mount_path))

    # Total summary
    total_files = sum(disk_usage.get(a.handle, (0, 0))[0] for a in artists)
    total_bytes = sum(disk_usage.get(a.handle, (0, 0))[1] for a in artists)
    st.caption(f"Total: {total_files:,} files · {_format_bytes(total_bytes)} across {len(artists)} artist(s)")

    # Pagination
    total_pages = max(1, -(-len(artists) // PAGE_SIZE))  # ceil division
    page = st.session_state.get("artist_page", 0)
    page = min(page, total_pages - 1)
    page_start = page * PAGE_SIZE
    page_end = page_start + PAGE_SIZE
    page_artists = artists[page_start:page_end]

    if total_pages > 1:
        col_prev, col_info, col_next = st.columns([1, 2, 1])
        with col_prev:
            if st.button("Prev", disabled=(page == 0), key="artist_page_prev"):
                st.session_state.artist_page = page - 1
                st.rerun()
        with col_info:
            st.markdown(f"<div style='text-align:center'>Page {page + 1} of {total_pages}</div>", unsafe_allow_html=True)
        with col_next:
            if st.button("Next", disabled=(page >= total_pages - 1), key="artist_page_next"):
                st.session_state.artist_page = page + 1
                st.rerun()

    for artist in page_artists:
        adapter = registry.get(artist.site)
        display = adapter.get_display_handle(artist)
        site_label = SITE_LABELS.get(artist.site, artist.site)

        col_info, col_rm, col_del = st.columns([0.6, 0.2, 0.2], vertical_alignment="center")
        with col_info:
            last_scan = artist.last_scan_at or "Never"
            count, size = disk_usage.get(artist.handle, (0, 0))
            meta = f"{count:,} file(s) · {_format_bytes(size)}" if count > 0 else "No files"
            st.markdown(f"**{display}** ({site_label}) — last scan: {last_scan}  \n{meta}")
        with col_rm:
            if st.button("Remove", key=f"rm_{artist.id}", use_container_width=True):
                db.deactivate_artist(artist.id)
                st.success(f"Removed {display} from queue")
                st.rerun()
        with col_del:
            if st.button("Delete Files", key=f"del_{artist.id}", use_container_width=True):
                db.deactivate_artist(artist.id)
                artist_dir = Path(config.nas.mount_path) / artist.handle
                if artist_dir.exists():
                    shutil.rmtree(artist_dir)
                st.success(f"Removed {display} and deleted files")
                st.rerun()


def _scan_disk_usage(nas_path: Path) -> dict[str, tuple[int, int]]:
    """Walk NAS mount once, return {artist_handle: (file_count, total_bytes)}."""
    usage: dict[str, tuple[int, int]] = {}
    if not nas_path.exists():
        return usage
    for entry in os.scandir(nas_path):
        if not entry.is_dir(follow_symlinks=False):
            continue
        file_count = 0
        total_bytes = 0
        for root, _, files in os.walk(entry.path):
            for f in files:
                try:
                    total_bytes += os.path.getsize(os.path.join(root, f))
                    file_count += 1
                except OSError:
                    continue
        if file_count > 0:
            usage[entry.name] = (file_count, total_bytes)
    return usage


def _format_bytes(n: int) -> str:
    if n == 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
