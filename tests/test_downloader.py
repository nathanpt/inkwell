from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

from src import db
from src.downloader import _diff_snapshots, _snapshot_directory, download_artist
from src.models import Artist, Job


class TestDiffSnapshots:
    def test_empty_snapshots(self):
        count, size = _diff_snapshots({}, {})
        assert count == 0
        assert size == 0

    def test_new_files_detected(self):
        before = {}
        after = {"a.jpg": 100, "b.png": 200}
        count, size = _diff_snapshots(before, after)
        assert count == 2
        assert size == 300

    def test_no_new_files(self):
        snap = {"a.jpg": 100}
        count, size = _diff_snapshots(snap, snap)
        assert count == 0
        assert size == 0

    def test_mixed_new_and_existing(self):
        before = {"old.jpg": 50}
        after = {"old.jpg": 50, "new.png": 200}
        count, size = _diff_snapshots(before, after)
        assert count == 1
        assert size == 200


class TestSnapshotDirectory:
    def test_nonexistent_dir(self, tmp_path):
        snap = _snapshot_directory(tmp_path / "nope")
        assert snap == {}

    def test_captures_files(self, artist_dir):
        (artist_dir / "img.jpg").write_bytes(b"x" * 100)
        (artist_dir / "sub").mkdir()
        (artist_dir / "sub" / "pic.png").write_bytes(b"y" * 50)
        snap = _snapshot_directory(artist_dir)
        assert "img.jpg" in snap
        assert str(Path("sub") / "pic.png") in snap
        assert snap["img.jpg"] == 100


class TestJobLock:
    def test_rejects_duplicate_running_job(self, db_conn, test_config, test_registry):
        artist = Artist(handle="test", site="x.com", source_url="https://x.com/test")
        artist.id = db.insert_artist(db_conn, artist)

        # Insert a running job manually
        job = Job(artist_id=artist.id, status="running", triggered_by="manual")
        db.insert_job(db_conn, job)

        # Attempt to download same artist should return existing job
        with patch("src.downloader._run_gallery_dl"):
            result = download_artist(db_conn, artist, test_config, test_registry)
        assert result.status == "running"
        # Should not have created a new job
        jobs = db_conn.execute("SELECT COUNT(*) FROM jobs WHERE artist_id = ?", (artist.id,)).fetchone()
        assert jobs[0] == 1


class TestDownloadArtist:
    def test_success_records_metrics(self, db_conn, test_config, artist_dir, test_registry):
        artist = Artist(handle="testartist", site="x.com", source_url="https://x.com/testartist")
        artist.id = db.insert_artist(db_conn, artist)

        # Pre-create a file to show it's not counted
        (artist_dir / "old.jpg").write_bytes(b"x" * 50)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        def _mock_download(*args, **kwargs):
            # Simulate gallery-dl creating a new file during the run
            (artist_dir / "new.png").write_bytes(b"y" * 200)
            return mock_result

        with patch("src.downloader._run_gallery_dl", side_effect=_mock_download):
            job = download_artist(db_conn, artist, test_config, test_registry)

        assert job.status == "success"
        assert job.file_count == 1
        assert job.total_bytes == 200

    def test_auth_error_sets_state(self, db_conn, test_config, test_registry):
        artist = Artist(handle="locked", site="x.com", source_url="https://x.com/locked")
        artist.id = db.insert_artist(db_conn, artist)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Error: login required"

        with patch("src.downloader._run_gallery_dl", return_value=mock_result):
            job = download_artist(db_conn, artist, test_config, test_registry)

        assert job.status == "failed"
        assert db.get_state(db_conn, "auth_valid:x.com") == "0"

    def test_retries_on_transient_failure(self, db_conn, test_config, test_registry):
        artist = Artist(handle="flaky", site="x.com", source_url="https://x.com/flaky")
        artist.id = db.insert_artist(db_conn, artist)

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stderr = "network error"

        success_result = MagicMock()
        success_result.returncode = 0
        success_result.stderr = ""

        with patch("src.downloader._run_gallery_dl", side_effect=[fail_result, success_result]):
            job = download_artist(db_conn, artist, test_config, test_registry)

        assert job.status == "success"
