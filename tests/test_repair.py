from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from src import db
from src.config_loader import Config, NASConfig
from src.models import Artist
from src.repair import RepairResult, extract_post_id, repair_missing
from src.sites.base import SiteRegistry
from src.sites.xcom import XComAdapter


@pytest.fixture
def setup(tmp_path):
    """Configure a scratch DB + NAS tree + x.com registry."""
    db.configure(tmp_path / "test.db")
    conn = db.connect(tmp_path / "test.db")
    db.init_schema(conn)
    conn.close()
    db.seed_state()

    nas = tmp_path / "nas"
    nas.mkdir()

    config = Config(nas=NASConfig(mount_path=str(nas)))
    registry = SiteRegistry()
    registry.register(XComAdapter())
    return config, registry, nas


def _make_artist(handle="alice", site="x.com"):
    artist = Artist(handle=handle, site=site, source_url=f"https://x.com/{handle}")
    artist.id = db.insert_artist(artist)
    return artist


def _insert_file(artist_id, filename, year, size=0):
    db.insert_file_records(None, artist_id, [(filename, year, size)])


class TestExtractPostId:
    def test_numeric_prefix(self):
        assert extract_post_id("2024/12345_photo.jpg") == "12345"

    def test_non_numeric_returns_none(self):
        assert extract_post_id("2024/hello.jpg") is None


class TestRepairHappyPath:
    def test_exact_match_recovers_row(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/12345_photo.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def writer(urls):
            ydir = nas / "alice" / "2024"
            ydir.mkdir(parents=True, exist_ok=True)
            for url in urls:
                pid = url.rsplit("/", 1)[-1]
                (ydir / f"{pid}_photo.jpg").write_bytes(b"data")

        def fake_batch(urls, config, adapter):
            writer(urls)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_recovered == 1
        assert result.rows_deleted == 0
        # Row still present
        rows = db.get_all_file_rows()
        assert len(rows) == 1


class TestRepairRenamedMedia:
    def test_renamed_basename_updates_row(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/12345_old.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def writer(urls):
            ydir = nas / "alice" / "2024"
            ydir.mkdir(parents=True, exist_ok=True)
            for url in urls:
                pid = url.rsplit("/", 1)[-1]
                (ydir / f"{pid}_new.jpg").write_bytes(b"renamed")

        def fake_batch(urls, config, adapter):
            writer(urls)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_updated == 1
        row = db.get_all_file_rows()[0]
        assert row["filename"] == "2024/12345_new.jpg"
        assert row["size_bytes"] == len(b"renamed")


class TestRepairUnrecoverable:
    def test_unrecovered_row_deleted_when_sibling_recovers(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/111_a.jpg", "2024")  # will recover
        _insert_file(artist.id, "2024/222_b.jpg", "2024")  # unrecoverable

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def writer(urls):
            ydir = nas / "alice" / "2024"
            ydir.mkdir(parents=True, exist_ok=True)
            for url in urls:
                pid = url.rsplit("/", 1)[-1]
                if pid == "111":
                    (ydir / "111_a.jpg").write_bytes(b"x")

        def fake_batch(urls, config, adapter):
            writer(urls)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_recovered == 1
        assert result.rows_deleted == 1
        rows = db.get_all_file_rows()
        assert len(rows) == 1
        assert rows[0]["filename"] == "2024/111_a.jpg"


class TestRepairSafeguard:
    def test_no_recovery_keeps_rows(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/999_missing.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def fake_batch(urls, config, adapter):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_recovered == 0
        assert result.rows_deleted == 0
        assert result.artists_no_recovery == 1
        # Row kept by safeguard
        assert len(db.get_all_file_rows()) == 1


class TestRepairUnsupported:
    def test_non_numeric_filename_unsupported(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/nonnumeric.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        called = {"n": 0}

        def fake_batch(urls, config, adapter):
            called["n"] += 1
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_unsupported == 1
        assert called["n"] == 0  # _run_batch never invoked
        assert len(db.get_all_file_rows()) == 1


class TestRepairRateLimit:
    def test_rate_limit_records_hit_and_aborts(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/12345_photo.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def fake_batch(urls, config, adapter):
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="HTTP 429 Too Many Requests"
            )

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.aborted_reason == "rate limited"
        # record_hit raised the x.com multiplier above 1.0
        from src.rate_limiter import get_cooldown_multiplier
        assert get_cooldown_multiplier("x.com") > 1.0


class TestRepairGuard:
    def test_already_running_aborts(self, setup):
        config, registry, nas = setup
        db.set_state("repair:running", "1")

        with patch("src.repair._run_batch") as mock_batch:
            result = repair_missing(config, registry)

        assert result.aborted_reason == "already running"
        assert mock_batch.call_count == 0
