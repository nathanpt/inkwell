from __future__ import annotations

import json

import pytest
from streamlit.testing.v1 import AppTest

from src import db
from src.models import Artist

# Rendered via substitution so the driver shares the test's tmp DB/NAS paths.
DRIVER = """\
from pathlib import Path

import streamlit as st
from src import db
from src.config_loader import Config, DownloadConfig, NASConfig
from src.sections.artists import render_artists

db.configure(Path("__DB_PATH__"))
if "config" not in st.session_state:
    st.session_state.config = Config(
        nas=NASConfig(mount_path="__NAS_PATH__"),
        download=DownloadConfig(
            retry_attempts=1,
            retry_backoff=[0],
            timeout=5,
            inter_artist_cooldown=[0, 0],
        ),
    )
render_artists()
"""


def _setup_db(tmp_path):
    db_path = tmp_path / "ui.db"
    (tmp_path / "nas").mkdir(exist_ok=True)
    db.configure(db_path)
    conn = db.connect(db_path)
    db.init_schema(conn)
    conn.close()
    db.seed_state()
    return db_path


def _make_at(tmp_path, db_path):
    script = tmp_path / "driver.py"
    script.write_text(
        DRIVER.replace("__DB_PATH__", str(db_path)).replace("__NAS_PATH__", str(tmp_path / "nas"))
    )
    return AppTest.from_file(str(script)).run()


def _seed_artist_with_files(handle: str = "testartist") -> int:
    aid = db.insert_artist(Artist(handle=handle, site="x.com", source_url=f"https://x.com/{handle}"))
    db.insert_file_records(None, aid, [
        ("2024/a.jpg", "2024", 100),
        ("2024/b.jpg", "2024", 100),
        ("2024/c.jpg", "2024", 100),
    ])
    return aid


class TestArtistsTable:
    def test_table_shows_missing_pct(self, tmp_path):
        db_path = _setup_db(tmp_path)
        aid = _seed_artist_with_files()
        db.set_state("integrity:last_check", json.dumps({
            "total": 3, "ok": 2, "missing": 1, "by_artist": {str(aid): 1},
        }))

        at = _make_at(tmp_path, db_path)

        values = [m.value for m in at.markdown]
        assert any("1/3 (33.3%)" in v for v in values)
        captions = [c.value for c in at.caption]
        for header in ("Artist", "Files", "Missing", "Last scan"):
            assert header in captions
        # One artist -> one page -> pagination controls hidden
        with pytest.raises(KeyError):
            at.button(key="artist_page_first")

    def test_missing_pct_dash_without_check(self, tmp_path):
        db_path = _setup_db(tmp_path)
        _seed_artist_with_files()

        at = _make_at(tmp_path, db_path)

        values = [m.value for m in at.markdown]
        # Artist has files, so the lone em dash is the unchecked Missing cell
        assert values.count("—") == 1

    def test_first_last_pagination(self, tmp_path):
        db_path = _setup_db(tmp_path)
        # Distinct added_at values: ORDER BY added_at must not depend on
        # second-granularity timestamp ties. Insert via per-op connections
        # first, then update timestamps in one short-lived transaction.
        for i in range(1, 16):
            db.insert_artist(Artist(handle=f"artist{i}", site="x.com", source_url=f"https://x.com/artist{i}"))
        conn = db.connect(db_path)
        for i in range(1, 16):
            conn.execute(
                "UPDATE artists SET added_at = ? WHERE handle = ?",
                (f"2026-01-01 00:{(i - 1) // 60:02d}:{(i - 1) % 60:02d}", f"artist{i}"),
            )
        conn.commit()
        conn.close()

        at = _make_at(tmp_path, db_path)

        assert at.button(key="artist_page_first") is not None
        assert at.button(key="artist_page_last") is not None
        values = [m.value for m in at.markdown]
        assert any("**@artist1**" in v for v in values)

        at.button(key="artist_page_last").click().run()
        values = [m.value for m in at.markdown]
        assert any("Page 2 of 2" in v for v in values)
        assert any("**@artist11**" in v for v in values)
        assert not any("**@artist1**" in v for v in values)

        at.button(key="artist_page_first").click().run()
        values = [m.value for m in at.markdown]
        assert any("Page 1 of 2" in v for v in values)
        assert any("**@artist1**" in v for v in values)
