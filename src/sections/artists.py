from __future__ import annotations

import json
import logging
import shutil
import threading
from pathlib import Path

import streamlit as st

from src import db
from src.downloader import download_artist
from src.models import Artist
from src.repair import repair_missing
from src.url_validator import validate_url, get_registry

logger = logging.getLogger(__name__)


SITE_LABELS = {
    "x.com": "X",
    "pixiv": "Pixiv",
    "deviantart": "DeviantArt",
}

PAGE_SIZE = 15

# Artist | Files | Missing | Last scan | Download | Repair | Remove | Delete Files
TABLE_COLS = [2.4, 1.1, 1.1, 1.4, 0.95, 0.95, 0.95, 1.15]


def _run_download(artist):
    """Run download for a single artist in a background thread."""
    config = st.session_state.config
    registry = get_registry()

    def wrapper():
        try:
            download_artist(artist, config, registry, "manual")
        except Exception as e:
            logger.exception("Background download thread crashed: %s", e)
            try:
                db.insert_log("ERROR", "downloader", f"Thread crashed: {e}")
            except Exception:
                pass

    t = threading.Thread(target=wrapper, daemon=True)
    t.start()


def _run_repair(artist):
    """Run targeted repair for a single artist in a background thread."""
    config = st.session_state.config
    registry = get_registry()

    def wrapper():
        try:
            repair_missing(config, registry, artist_id=artist.id)
        except Exception as e:
            logger.exception("Background repair thread crashed: %s", e)
            try:
                db.insert_log("ERROR", "repair", f"Thread crashed: {e}")
            except Exception:
                pass

    t = threading.Thread(target=wrapper, daemon=True)
    t.start()


def render_artists():
    config = st.session_state.config
    registry = get_registry()

    # Add artist form — collapsed by default: it's used once per artist, while
    # the table below is the page's daily surface.
    with st.expander("Add artist"):
        with st.form("add_artist"):
            col_input, col_btn = st.columns([4, 1], vertical_alignment="bottom")
            with col_input:
                url = st.text_input(
                    "Artist URL",
                    placeholder="https://x.com/handle/media?filter=photo, https://www.pixiv.net/users/123, https://www.deviantart.com/name",
                )
            with col_btn:
                submitted = st.form_submit_button("Add Artist", use_container_width=True)
            if submitted and url:
                try:
                    handle, normalized_url, adapter = validate_url(url)
                    handle = adapter.resolve_handle(handle)
                    existing = db.get_artist_by_url(normalized_url)
                    if existing and existing.is_active:
                        st.error(f"Artist {adapter.get_display_handle(Artist(handle=handle))} is already tracked")
                    elif existing and not existing.is_active:
                        db.reactivate_artist(existing.id)
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

    disk_usage = db.get_disk_usage_by_artist()

    by_artist: dict[str, int] = {}
    raw = db.get_state("integrity:last_check")
    if raw:
        try:
            by_artist = json.loads(raw).get("by_artist") or {}
        except ValueError:
            by_artist = {}

    total_files = sum(disk_usage.get(a.id, (0, 0))[0] for a in artists)
    total_bytes = sum(disk_usage.get(a.id, (0, 0))[1] for a in artists)
    # Total summary + search share one row — as two full-width blocks they
    # pushed the first artist row below the fold.
    col_search, col_total = st.columns([3, 2])
    with col_search:
        search = st.text_input(
            "Search artists", placeholder="Filter by name or site...",
            key="artist_search", label_visibility="collapsed",
        )
    with col_total:
        st.markdown(
            f"<div style='text-align:right; color:#808495; padding-top:0.35rem'>"
            f"Total: {total_files:,} files · {_format_bytes(total_bytes)} across {len(artists)} artist(s)</div>",
            unsafe_allow_html=True,
        )
    if search:
        search_lower = search.lower()
        artists = [a for a in artists if search_lower in a.handle.lower() or search_lower in SITE_LABELS.get(a.site, a.site).lower()]

    if not artists:
        st.info("No artists match your search.")
        return

    # Column sort (set by the header buttons below); unset = db order (added_at)
    sort_spec = st.session_state.get("artist_sort")
    if sort_spec is not None:
        sort_key, sort_asc = sort_spec

        def _sort_value(a):
            if sort_key == "artist":
                return registry.get(a.site).get_display_handle(a).lower()
            if sort_key == "files":
                return disk_usage.get(a.id, (0, 0))[0]
            if sort_key == "missing":
                # Unchecked artists ("—") sort below 0 missing
                return by_artist.get(str(a.id), -1)
            return a.last_scan_at or ""  # scan: "Never" sorts as oldest

        artists = sorted(
            artists,
            key=lambda a: (_sort_value(a), a.handle.lower(), a.id or 0),
            reverse=not sort_asc,
        )

    # Pagination
    total_pages = max(1, -(-len(artists) // PAGE_SIZE))  # ceil division
    page = st.session_state.get("artist_page", 0)
    page = min(page, total_pages - 1)
    page_start = page * PAGE_SIZE
    page_end = page_start + PAGE_SIZE
    page_artists = artists[page_start:page_end]

    hdr = st.columns(TABLE_COLS)
    with hdr[0]:
        _sort_header("artist", "Artist")
    with hdr[1]:
        _sort_header("files", "Files")
    with hdr[2]:
        _sort_header("missing", "Missing")
    with hdr[3]:
        _sort_header("scan", "Last scan")
    st.divider()

    repair_running = db.get_state("repair:running") == "1"

    for i, artist in enumerate(page_artists):
        if i:
            st.divider()
        adapter = registry.get(artist.site)
        display = adapter.get_display_handle(artist)
        site_label = SITE_LABELS.get(artist.site, artist.site)
        count, size = disk_usage.get(artist.id, (0, 0))

        col_info, col_files, col_missing, col_scan, col_dl, col_rep, col_rm, col_del = st.columns(
            TABLE_COLS, vertical_alignment="center"
        )
        with col_info:
            st.markdown(f"**{display}** · {site_label}")
        with col_files:
            st.markdown(f"{count:,} · {_format_bytes(size)}" if count else "—")
        with col_missing:
            st.markdown(_missing_cell(by_artist, artist.id, count))
        with col_scan:
            st.markdown(artist.last_scan_at or "Never")
        with col_dl:
            if st.button("Download", key=f"dl_{artist.id}", use_container_width=True):
                _run_download(artist)
                st.toast(f"Download started for {display}")
        with col_rep:
            if st.button(
                "Repair", key=f"rep_{artist.id}", use_container_width=True,
                disabled=repair_running,
                help="Re-fetch this artist's missing files; purges rows whose posts are gone upstream.",
            ):
                _run_repair(artist)
                st.toast(f"Repair started for {display}")
        with col_rm:
            if st.button("Remove", key=f"rm_{artist.id}", use_container_width=True):
                db.deactivate_artist(artist.id)
                st.toast(f"Removed {display} from queue")
                st.rerun()
        with col_del:
            if st.button("Delete Files", key=f"del_{artist.id}", use_container_width=True):
                db.deactivate_artist(artist.id)
                artist_dir = Path(config.nas.mount_path) / artist.handle
                if artist_dir.exists():
                    shutil.rmtree(artist_dir)
                st.toast(f"Removed {display} and deleted files")
                st.rerun()

    st.divider()

    if total_pages > 1:
        col_first, col_prev, col_info, col_next, col_last = st.columns([1, 1, 2, 1, 1])
        with col_first:
            if st.button("First", disabled=(page == 0), key="artist_page_first"):
                st.session_state.artist_page = 0
                st.rerun()
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
        with col_last:
            if st.button("Last", disabled=(page >= total_pages - 1), key="artist_page_last"):
                st.session_state.artist_page = total_pages - 1
                st.rerun()


def _sort_header(col: str, label: str) -> None:
    """Clickable column header: first click sorts ascending, next toggles
    direction; changing columns resets to ascending and to page 1."""
    key, asc = st.session_state.get("artist_sort", (None, True))
    arrow = "" if key != col else (" ↑" if asc else " ↓")
    if st.button(f"{label}{arrow}", key=f"artist_sort_{col}", use_container_width=True):
        st.session_state.artist_sort = (col, not asc) if key == col else (col, True)
        st.session_state.artist_page = 0
        st.rerun()


def _missing_cell(by_artist: dict[str, int], artist_id: int | None, total: int) -> str:
    """'m/t (p%)' from the last integrity check; '—' when unchecked or fileless."""
    if not total or artist_id is None:
        return "—"
    missing = by_artist.get(str(artist_id))
    if missing is None:
        return "—"
    return f"{missing}/{total} ({missing / total:.1%})"


def _format_bytes(n: int) -> str:
    if n == 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
