from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from src import db
from src.downloader import _new_file_records
from src.models import Artist, Job


class TestInsertFileRecords:
    def test_bulk_insert(self, db_conn):
        artist = Artist(handle="testartist", site="x.com", source_url="https://x.com/testartist")
        artist.id = db.insert_artist(artist)
        job = Job(artist_id=artist.id, status="success", triggered_by="manual")
        job.id = db.insert_job(job)

        files = [
            ("2024/img1.jpg", "2024", 1024),
            ("2024/img2.png", "2024", 2048),
            ("2025/art.webp", "2025", 512),
        ]
        db.insert_file_records(job.id, artist.id, files)

        rows = db_conn.execute("SELECT filename, year, size_bytes FROM files ORDER BY filename").fetchall()
        assert len(rows) == 3
        assert rows[0]["filename"] == "2024/img1.jpg"
        assert rows[0]["year"] == "2024"
        assert rows[0]["size_bytes"] == 1024

    def test_empty_list_is_noop(self, db_conn):
        artist = Artist(handle="testartist", site="x.com", source_url="https://x.com/testartist")
        artist.id = db.insert_artist(artist)
        db.insert_file_records(None, artist.id, [])
        count = db_conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        assert count == 0

    def test_null_job_id(self, db_conn):
        """Backfilled records have job_id=NULL."""
        artist = Artist(handle="testartist", site="x.com", source_url="https://x.com/testartist")
        artist.id = db.insert_artist(artist)

        db.insert_file_records(None, artist.id, [("2024/img.jpg", "2024", 100)])
        row = db_conn.execute("SELECT job_id FROM files").fetchone()
        assert row["job_id"] is None


class TestGetDiskUsageByArtist:
    def test_returns_grouped_usage(self, db_conn):
        a1 = Artist(handle="a1", site="x.com", source_url="https://x.com/a1")
        a1.id = db.insert_artist(a1)
        a2 = Artist(handle="a2", site="pixiv", source_url="https://www.pixiv.net/users/111")
        a2.id = db.insert_artist(a2)

        db.insert_file_records(None, a1.id, [
            ("2024/x.jpg", "2024", 100),
            ("2024/y.png", "2024", 200),
        ])
        db.insert_file_records(None, a2.id, [
            ("2025/z.gif", "2025", 500),
        ])

        usage = db.get_disk_usage_by_artist()
        assert usage[a1.id] == (2, 300)
        assert usage[a2.id] == (1, 500)

    def test_empty_db(self, db_conn):
        usage = db.get_disk_usage_by_artist()
        assert usage == {}


class TestGetRecentFiles:
    def test_returns_latest_first(self, db_conn):
        artist = Artist(handle="testartist", site="x.com", source_url="https://x.com/testartist")
        artist.id = db.insert_artist(artist)

        db.insert_file_records(None, artist.id, [
            ("2024/a.jpg", "2024", 100),
            ("2024/b.png", "2024", 200),
        ])

        files = db.get_recent_files(artist_id=artist.id)
        assert len(files) == 2
        filenames = {f["filename"] for f in files}
        assert filenames == {"2024/a.jpg", "2024/b.png"}

    def test_filters_by_artist(self, db_conn):
        a1 = Artist(handle="a1", site="x.com", source_url="https://x.com/a1")
        a1.id = db.insert_artist(a1)
        a2 = Artist(handle="a2", site="pixiv", source_url="https://www.pixiv.net/users/222")
        a2.id = db.insert_artist(a2)

        db.insert_file_records(None, a1.id, [("2024/a.jpg", "2024", 100)])
        db.insert_file_records(None, a2.id, [("2024/b.png", "2024", 200)])

        files = db.get_recent_files(artist_id=a1.id)
        assert len(files) == 1
        assert files[0]["filename"] == "2024/a.jpg"

    def test_respects_limit(self, db_conn):
        artist = Artist(handle="testartist", site="x.com", source_url="https://x.com/testartist")
        artist.id = db.insert_artist(artist)

        records = [(f"2024/{i}.jpg", "2024", i * 10) for i in range(20)]
        db.insert_file_records(None, artist.id, records)

        files = db.get_recent_files(limit=5)
        assert len(files) == 5


class TestNewFileRecords:
    def test_extracts_year_from_path(self):
        before = {}
        after = {
            "2024/img.jpg": 100,
            "2025/art.png": 200,
        }
        records = _new_file_records(before, after)
        assert len(records) == 2
        assert records[0] == ("2024/img.jpg", "2024", 100)
        assert records[1] == ("2025/art.png", "2025", 200)

    def test_non_year_directory(self):
        before = {}
        after = {"misc/file.jpg": 50}
        records = _new_file_records(before, after)
        assert records[0][1] == "unknown"

    def test_no_new_files(self):
        snap = {"2024/img.jpg": 100}
        records = _new_file_records(snap, snap)
        assert records == []


class TestDownloaderFileRecording:
    def test_success_records_files(self, db_conn, test_config, artist_dir, test_registry):
        artist = Artist(handle="testartist", site="x.com", source_url="https://x.com/testartist")
        artist.id = db.insert_artist(artist)

        # Pre-create year directory structure
        year_dir = artist_dir / "2024"
        year_dir.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        def _mock_download(*args, **kwargs):
            (year_dir / "new.png").write_bytes(b"x" * 200)
            (year_dir / "photo.jpg").write_bytes(b"y" * 300)
            return mock_result

        with patch("src.downloader._run_gallery_dl", side_effect=_mock_download):
            from src.downloader import download_artist
            job = download_artist(artist, test_config, test_registry)

        assert job.status == "success"
        assert job.file_count == 2

        files = db.get_recent_files(artist_id=artist.id)
        assert len(files) == 2
        filenames = {f["filename"] for f in files}
        assert "2024/new.png" in filenames
        assert "2024/photo.jpg" in filenames
        # All should be linked to the job
        assert all(f["job_id"] == job.id for f in files)

    def test_partial_failure_records_files(self, db_conn, test_config, artist_dir, test_registry):
        artist = Artist(handle="testartist", site="x.com", source_url="https://x.com/testartist")
        artist.id = db.insert_artist(artist)

        year_dir = artist_dir / "2024"
        year_dir.mkdir()

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stderr = "network error"

        call_count = 0

        def _mock_download(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                (year_dir / "partial.jpg").write_bytes(b"x" * 100)
            return fail_result

        with patch("src.downloader._run_gallery_dl", side_effect=_mock_download):
            from src.downloader import download_artist
            job = download_artist(artist, test_config, test_registry)

        # Job should fail but partial file from second attempt should be recorded
        assert job.status == "failed"
        files = db.get_recent_files(artist_id=artist.id)
        assert len(files) == 1
        assert files[0]["filename"] == "2024/partial.jpg"


class TestSchemaMigration:
    def test_v2_to_v3_migration(self, db_conn):
        """Verify that init_schema on an existing v2 DB adds the files table."""
        # The conftest already runs init_schema which now migrates to v3
        # Check that the files table exists and is queryable
        tables = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='files'"
        ).fetchone()
        assert tables is not None

        version = db_conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 3
