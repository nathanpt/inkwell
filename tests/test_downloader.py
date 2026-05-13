from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

from src import db
from src.config_loader import Config, RateLimitConfig
from src.downloader import _diff_snapshots, _snapshot_directory, download_artist, download_all
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
        artist.id = db.insert_artist(artist)

        # Insert a running job manually
        job = Job(artist_id=artist.id, status="running", triggered_by="manual")
        db.insert_job(job)

        # Attempt to download same artist should return existing job
        with patch("src.downloader._run_gallery_dl"):
            result = download_artist(artist, test_config, test_registry)
        assert result.status == "running"
        # Should not have created a new job
        jobs = db_conn.execute("SELECT COUNT(*) FROM jobs WHERE artist_id = ?", (artist.id,)).fetchone()
        assert jobs[0] == 1


class TestDownloadArtist:
    def test_success_records_metrics(self, db_conn, test_config, artist_dir, test_registry):
        artist = Artist(handle="testartist", site="x.com", source_url="https://x.com/testartist")
        artist.id = db.insert_artist(artist)

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
            job = download_artist(artist, test_config, test_registry)

        assert job.status == "success"
        assert job.file_count == 1
        assert job.total_bytes == 200

    def test_auth_error_sets_state(self, db_conn, test_config, test_registry):
        artist = Artist(handle="locked", site="x.com", source_url="https://x.com/locked")
        artist.id = db.insert_artist(artist)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Error: login required"

        with patch("src.downloader._run_gallery_dl", return_value=mock_result):
            job = download_artist(artist, test_config, test_registry)

        assert job.status == "failed"
        assert db.get_state("auth_valid:x.com") == "0"

    def test_retries_on_transient_failure(self, db_conn, test_config, test_registry):
        artist = Artist(handle="flaky", site="x.com", source_url="https://x.com/flaky")
        artist.id = db.insert_artist(artist)

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stderr = "network error"

        success_result = MagicMock()
        success_result.returncode = 0
        success_result.stderr = ""

        with patch("src.downloader._run_gallery_dl", side_effect=[fail_result, success_result]):
            job = download_artist(artist, test_config, test_registry)

        assert job.status == "success"


class TestRateLimitDetection:
    def test_rate_limit_skips_retries(self, db_conn, test_config, test_registry):
        artist = Artist(handle="limited", site="x.com", source_url="https://x.com/limited")
        artist.id = db.insert_artist(artist)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "429 Too Many Requests"

        call_count = 0

        def _count_calls(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return mock_result

        with patch("src.downloader._run_gallery_dl", side_effect=_count_calls):
            job = download_artist(artist, test_config, test_registry)

        assert job.status == "failed"
        assert job.error_message == "Rate limited"
        # Should have called gallery-dl only once (no retries)
        assert call_count == 1

    def test_rate_limit_records_hit(self, db_conn, test_config, test_registry):
        artist = Artist(handle="limited", site="x.com", source_url="https://x.com/limited")
        artist.id = db.insert_artist(artist)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "429 Too Many Requests"

        with patch("src.downloader._run_gallery_dl", return_value=mock_result):
            download_artist(artist, test_config, test_registry)

        from src.rate_limiter import get_cooldown_multiplier
        assert get_cooldown_multiplier("x.com") > 1.0

    def test_success_decays_multiplier(self, db_conn, test_config, test_registry):
        artist = Artist(handle="ok", site="x.com", source_url="https://x.com/ok")
        artist.id = db.insert_artist(artist)

        # First, record a hit to bump the multiplier
        from src.rate_limiter import record_hit
        record_hit("x.com", test_config.rate_limit)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("src.downloader._run_gallery_dl", return_value=mock_result):
            download_artist(artist, test_config, test_registry)

        from src.rate_limiter import get_cooldown_multiplier
        assert get_cooldown_multiplier("x.com") < test_config.rate_limit.multiplier_step


class TestJobsWithArtistInfo:
    def test_returns_joined_data(self, db_conn):
        from src.models import Artist, Job
        artist = Artist(handle="testuser", site="x.com", source_url="https://x.com/testuser")
        artist.id = db.insert_artist(artist)

        job = Job(artist_id=artist.id, status="running", triggered_by="manual")
        job.id = db.insert_job(job)
        db.update_job_completion(job.id, "success", 3, 1024)

        rows = db.get_jobs_with_artist_info()
        assert len(rows) == 1
        r = rows[0]
        assert r["artist_handle"] == "testuser"
        assert r["artist_site"] == "x.com"
        assert r["status"] == "success"
        assert r["file_count"] == 3
        assert r["total_bytes"] == 1024

    def test_filters_by_status(self, db_conn):
        from src.models import Artist, Job
        artist = Artist(handle="a1", site="x.com", source_url="https://x.com/a1")
        artist.id = db.insert_artist(artist)

        db.insert_job(Job(artist_id=artist.id, status="running", triggered_by="manual"))
        job2 = Job(artist_id=artist.id, status="running", triggered_by="manual")
        job2.id = db.insert_job(job2)
        db.update_job_completion(job2.id, "failed", 0, 0, "err")

        rows = db.get_jobs_with_artist_info(status="failed")
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"

    def test_empty_result(self, db_conn):
        rows = db.get_jobs_with_artist_info()
        assert rows == []


class TestSitePause:
    def test_paused_site_skipped(self, db_conn, test_config, test_registry):
        from src.sites.pixiv import PixivAdapter
        test_registry.register(PixivAdapter())

        artist1 = Artist(handle="paused1", site="x.com", source_url="https://x.com/paused1")
        artist1.id = db.insert_artist(artist1)
        artist2 = Artist(handle="ok2", site="pixiv", source_url="https://www.pixiv.net/users/111")
        artist2.id = db.insert_artist(artist2)

        # Pause x.com by hitting it enough times to reach pause_threshold (6.0)
        # With step=1.5: 1.5 -> 2.25 -> 3.375 -> 5.0625 -> 7.59 (paused)
        from src.rate_limiter import record_hit
        for _ in range(5):
            record_hit("x.com", test_config.rate_limit)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("src.downloader._run_gallery_dl", return_value=mock_result):
            jobs = download_all(test_config, test_registry)

        # Only the pixiv artist should have been downloaded
        assert len(jobs) == 1
        assert jobs[0].artist_id == artist2.id
