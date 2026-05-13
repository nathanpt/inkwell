from __future__ import annotations

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


def render_artists():
    conn = st.session_state.conn
    config = st.session_state.config
    registry = get_registry()

    # Add artist form
    with st.form("add_artist"):
        url = st.text_input(
            "Artist URL",
            placeholder="https://x.com/handle, https://www.pixiv.net/users/123, https://www.deviantart.com/name",
        )
        submitted = st.form_submit_button("Add Artist")
        if submitted and url:
            try:
                handle, normalized_url, adapter = validate_url(url)
                existing = db.get_artist_by_url(conn, normalized_url)
                if existing and existing.is_active:
                    st.error(f"Artist {adapter.get_display_handle(Artist(handle=handle))} is already tracked")
                elif existing and not existing.is_active:
                    conn.execute(
                        "UPDATE artists SET is_active = 1 WHERE id = ?", (existing.id,)
                    )
                    conn.commit()
                    st.success(f"Reactivated {adapter.get_display_handle(Artist(handle=handle))}")
                    st.rerun()
                else:
                    artist = Artist(handle=handle, site=adapter.name, source_url=normalized_url)
                    db.insert_artist(conn, artist)
                    st.success(f"Added {adapter.get_display_handle(Artist(handle=handle))} ({SITE_LABELS.get(adapter.name, adapter.name)})")
                    st.rerun()
            except ValueError as e:
                st.error(str(e))

    # Artist list
    artists = db.get_active_artists(conn)
    if not artists:
        st.info("No artists tracked yet. Add one above.")
        return

    for artist in artists:
        adapter = registry.get(artist.site)
        display = adapter.get_display_handle(artist)
        site_label = SITE_LABELS.get(artist.site, artist.site)

        col1, col2, col3 = st.columns([3, 2, 1])
        with col1:
            last_scan = artist.last_scan_at or "Never"
            st.markdown(f"**{display}** ({site_label}) — last scan: {last_scan}")
        with col2:
            artist_dir = Path(config.nas.mount_path) / artist.handle
            if artist_dir.exists():
                file_count = sum(1 for _ in artist_dir.rglob("*") if _.is_file())
                st.caption(f"{file_count} file(s)")
        with col3:
            if st.button("Remove", key=f"rm_{artist.id}"):
                db.deactivate_artist(conn, artist.id)
                st.success(f"Removed {display} from queue")
                st.rerun()

            if st.button("Delete Files", key=f"del_{artist.id}"):
                db.deactivate_artist(conn, artist.id)
                if artist_dir.exists():
                    shutil.rmtree(artist_dir)
                st.success(f"Removed {display} and deleted files")
                st.rerun()
