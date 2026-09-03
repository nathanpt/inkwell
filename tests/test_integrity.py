from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from src import db
from src.config_loader import Config, NASConfig, load_config
from src.integrity import (
    IntegrityReport,
    check_integrity,
    consolidate_all_sibling_zips,
    file_available,
    find_sibling_zips,
)
from src.models import Artist


class TestFileAvailable:
    def test_loose_file_is_available(self, tmp_path):
        handle = tmp_path / "alice"
        (handle / "2024").mkdir(parents=True)
        (handle / "2024" / "foo.jpg").write_bytes(b"x")

        assert file_available(tmp_path, "alice", "2024", "2024/foo.jpg", {}) is True

    def test_canonical_zip_entry_is_available(self, tmp_path):
        (tmp_path / "alice").mkdir()
        handle = tmp_path / "alice"
        with zipfile.ZipFile(handle / "2024.zip", "w") as zf:
            zf.writestr("foo.jpg", b"x")

        assert file_available(tmp_path, "alice", "2024", "2024/foo.jpg", {}) is True

    def test_sibling_zip_entry_is_available(self, tmp_path):
        (tmp_path / "alice").mkdir()
        handle = tmp_path / "alice"
        with zipfile.ZipFile(handle / "2024 (1).zip", "w") as zf:
            zf.writestr("foo.jpg", b"x")

        assert file_available(tmp_path, "alice", "2024", "2024/foo.jpg", {}) is True

    def test_prefixed_zip_entry_is_not_available(self, tmp_path):
        """An entry stored as <year>/<basename> is unreadable by the gallery."""
        (tmp_path / "alice").mkdir()
        handle = tmp_path / "alice"
        with zipfile.ZipFile(handle / "2024.zip", "w") as zf:
            zf.writestr("2024/foo.jpg", b"x")  # prefixed, not bare basename

        assert file_available(tmp_path, "alice", "2024", "2024/foo.jpg", {}) is False

    def test_missing_everywhere_is_not_available(self, tmp_path):
        (tmp_path / "alice").mkdir()
        assert file_available(tmp_path, "alice", "2024", "2024/nope.jpg", {}) is False

    def test_corrupt_zip_treated_as_empty(self, tmp_path):
        (tmp_path / "alice").mkdir()
        handle = tmp_path / "alice"
        (handle / "2024.zip").write_bytes(b"not a zip")
        assert file_available(tmp_path, "alice", "2024", "2024/foo.jpg", {}) is False


class TestFindSiblingZips:
    def test_finds_siblings_excluding_canonical(self, tmp_path):
        d = tmp_path / "alice"
        d.mkdir()
        (d / "2024.zip").write_bytes(b"")
        (d / "2024 (1).zip").write_bytes(b"")
        (d / "2024(2).zip").write_bytes(b"")
        (d / "2023.zip").write_bytes(b"")  # different year

        sibs = find_sibling_zips(d, "2024")
        assert {p.name for p in sibs} == {"2024 (1).zip", "2024(2).zip"}


class TestCheckIntegrity:
    @pytest.fixture
    def configured_db(self, tmp_path):
        db.configure(tmp_path / "test.db")
        conn = db.connect(tmp_path / "test.db")
        db.init_schema(conn)
        conn.close()
        db.seed_state()
        return tmp_path / "test.db"

    def test_classifies_ok_missing_and_sibling(self, tmp_path, configured_db):
        nas = tmp_path / "nas"
        alice = nas / "alice"
        (alice / "2024").mkdir(parents=True)

        # OK: loose file
        (alice / "2024" / "ok.jpg").write_bytes(b"ok")
        # sibling-only recoverable
        with zipfile.ZipFile(alice / "2023 (1).zip", "w") as zf:
            zf.writestr("sib.jpg", b"sib")
        # missing: nothing on disk for "missing.jpg"

        artist = type("A", (), {"handle": "alice", "site": "x.com", "source_url": "https://x.com/alice"})()
        artist_id = db.insert_artist(artist)
        db.insert_file_records(None, artist_id, [
            ("2024/ok.jpg", "2024", 2),
            ("2024/missing.jpg", "2024", 0),
            ("2023/sib.jpg", "2023", 3),
        ])

        config = Config(nas=NASConfig(mount_path=str(nas)))
        report = check_integrity(config)

        assert report.total == 3
        assert report.ok == 2
        assert len(report.missing) == 1
        assert report.missing[0].filename == "2024/missing.jpg"
        assert report.sibling_zips == 1

        raw = db.get_state("integrity:last_check")
        assert raw is not None
        data = json.loads(raw)
        assert data["total"] == 3
        assert data["ok"] == 2
        assert data["missing"] == 1
        assert data["sibling_zips"] == 1

    def test_persists_per_artist_missing_counts(self, tmp_path, configured_db):
        nas = tmp_path / "nas"
        ok_dir = nas / "okartist" / "2024"
        bad_dir = nas / "badartist" / "2024"
        ok_dir.mkdir(parents=True)
        bad_dir.mkdir(parents=True)
        (ok_dir / "a.jpg").write_bytes(b"a")
        (ok_dir / "b.jpg").write_bytes(b"b")
        (bad_dir / "c.jpg").write_bytes(b"c")

        ok_id = db.insert_artist(Artist(handle="okartist", site="x.com", source_url="https://x.com/okartist"))
        bad_id = db.insert_artist(Artist(handle="badartist", site="x.com", source_url="https://x.com/badartist"))
        db.insert_file_records(None, ok_id, [("2024/a.jpg", "2024", 1), ("2024/b.jpg", "2024", 1)])
        db.insert_file_records(None, bad_id, [("2024/c.jpg", "2024", 1), ("2024/missing.jpg", "2024", 0)])

        check_integrity(Config(nas=NASConfig(mount_path=str(nas))))

        data = json.loads(db.get_state("integrity:last_check"))
        assert data["by_artist"] == {str(ok_id): 0, str(bad_id): 1}


class TestConsolidateSiblingZips:
    def test_merges_two_siblings_and_dedups(self, tmp_path):
        artist = tmp_path / "alice"
        artist.mkdir()
        # Canonical already holds a.jpg
        with zipfile.ZipFile(artist / "2024.zip", "w") as zf:
            zf.writestr("a.jpg", b"a")
        # Sibling 1: b.jpg
        with zipfile.ZipFile(artist / "2024 (1).zip", "w") as zf:
            zf.writestr("b.jpg", b"b")
        # Sibling 2: a.jpg (dup) + c.jpg
        with zipfile.ZipFile(artist / "2024 (2).zip", "w") as zf:
            zf.writestr("a.jpg", b"a2")
            zf.writestr("c.jpg", b"c")

        config = Config(nas=NASConfig(mount_path=str(tmp_path)))
        merged, removed = consolidate_all_sibling_zips(config)

        assert removed == 2
        assert merged == 2  # b.jpg + c.jpg (a.jpg deduped against canonical)
        with zipfile.ZipFile(artist / "2024.zip") as zf:
            assert set(zf.namelist()) == {"a.jpg", "b.jpg", "c.jpg"}
        assert not (artist / "2024 (1).zip").exists()
        assert not (artist / "2024 (2).zip").exists()

    def test_corrupt_sibling_left_in_place(self, tmp_path):
        artist = tmp_path / "alice"
        artist.mkdir()
        with zipfile.ZipFile(artist / "2024.zip", "w") as zf:
            zf.writestr("a.jpg", b"a")
        # Valid sibling
        with zipfile.ZipFile(artist / "2024 (1).zip", "w") as zf:
            zf.writestr("b.jpg", b"b")
        # Corrupt sibling
        (artist / "2024 (2).zip").write_bytes(b"garbage")

        config = Config(nas=NASConfig(mount_path=str(tmp_path)))
        merged, removed = consolidate_all_sibling_zips(config)

        # Only the valid sibling consolidated; corrupt one stays
        assert removed == 1
        assert merged == 1
        assert (artist / "2024 (2).zip").exists()
        assert not (artist / "2024 (1).zip").exists()


class TestIntegrityConfigDefaults:
    def test_defaults_parse_via_load_config(self, tmp_path):
        toml = tmp_path / "config.toml"
        toml.write_text('[nas]\nmount_path = "/x"\n')
        c = load_config(toml)
        assert c.integrity.enabled is True
        assert c.integrity.check_cron == "0 4 * * 0"
        assert c.integrity.auto_repair is True
        assert c.integrity.max_posts_per_run == 200
