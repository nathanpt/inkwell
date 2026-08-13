from __future__ import annotations

import pytest
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

    def test_reinsert_larger_size_keeps_one_row_at_larger(self, db_conn):
        """A re-emission of a recorded file updates size_bytes to the larger value."""
        artist = Artist(handle="testartist", site="x.com", source_url="https://x.com/testartist")
        artist.id = db.insert_artist(artist)

        db.insert_file_records(None, artist.id, [("2024/img.jpg", "2024", 100)])
        db.insert_file_records(None, artist.id, [("2024/img.jpg", "2024", 200)])

        rows = db_conn.execute(
            "SELECT filename, size_bytes FROM files"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["size_bytes"] == 200

    def test_reinsert_smaller_size_keeps_stored_size(self, db_conn):
        """A smaller re-download must not shrink the stored (higher-quality) size."""
        artist = Artist(handle="testartist", site="x.com", source_url="https://x.com/testartist")
        artist.id = db.insert_artist(artist)

        db.insert_file_records(None, artist.id, [("2024/img.jpg", "2024", 200)])
        db.insert_file_records(None, artist.id, [("2024/img.jpg", "2024", 100)])

        row = db_conn.execute(
            "SELECT size_bytes FROM files WHERE filename = '2024/img.jpg'"
        ).fetchone()
        assert row["size_bytes"] == 200


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

    def test_filters_by_year(self, db_conn):
        artist = Artist(handle="testartist", site="x.com", source_url="https://x.com/testartist")
        artist.id = db.insert_artist(artist)

        db.insert_file_records(None, artist.id, [
            ("2024/a.jpg", "2024", 100),
            ("2024/b.jpg", "2024", 200),
            ("2025/c.png", "2025", 300),
        ])

        files = db.get_recent_files(artist_id=artist.id, years=["2024"])
        assert {f["filename"] for f in files} == {"2024/a.jpg", "2024/b.jpg"}

        multi = db.get_recent_files(artist_id=artist.id, years=["2024", "2025"])
        assert len(multi) == 3

    def test_offset_skips_newest(self, db_conn):
        artist = Artist(handle="testartist", site="x.com", source_url="https://x.com/testartist")
        artist.id = db.insert_artist(artist)

        # Insert in order; downloaded_at ties resolve by id ASC, so the LIMIT
        # page of 5 returns ids 1..5, and offset 5 returns ids 6..10.
        db.insert_file_records(
            None, artist.id, [(f"2024/{i}.jpg", "2024", i) for i in range(10)]
        )

        first_page = db.get_recent_files(artist_id=artist.id, limit=5, offset=0)
        second_page = db.get_recent_files(artist_id=artist.id, limit=5, offset=5)
        first_ids = {f["id"] for f in first_page}
        second_ids = {f["id"] for f in second_page}
        assert len(first_page) == 5
        assert len(second_page) == 5
        assert first_ids.isdisjoint(second_ids)

    def test_default_args_unchanged(self, db_conn):
        """Existing callers pass only artist_id; defaults must still work."""
        artist = Artist(handle="testartist", site="x.com", source_url="https://x.com/testartist")
        artist.id = db.insert_artist(artist)
        db.insert_file_records(None, artist.id, [("2024/a.jpg", "2024", 100)])

        files = db.get_recent_files(artist_id=artist.id)
        assert len(files) == 1
        assert files[0]["filename"] == "2024/a.jpg"

    def test_numeric_prefix_order_across_digit_lengths(self, db_conn):
        """Numeric, not lexical, sort: a 19-digit ID outranks a 17-digit one
        even though lexical DESC would rank '9' before '1'."""
        artist = Artist(handle="testartist", site="x.com", source_url="https://x.com/testartist")
        artist.id = db.insert_artist(artist)
        db.insert_file_records(None, artist.id, [
            ("2024/99999999999999999_a.jpg", "2024", 100),  # 17-digit
            ("2024/1000000000000000000_b.jpg", "2024", 200),  # 19-digit
        ])
        files = db.get_recent_files(artist_id=artist.id)
        # Default DESC by numeric prefix → 19-digit (b) first. A lexical DESC
        # would put '99999…' first, so this pair discriminates the two.
        assert files[0]["filename"] == "2024/1000000000000000000_b.jpg"
        assert files[1]["filename"] == "2024/99999999999999999_a.jpg"

    def test_asc_and_desc_direction(self, db_conn):
        artist = Artist(handle="testartist", site="x.com", source_url="https://x.com/testartist")
        artist.id = db.insert_artist(artist)
        db.insert_file_records(None, artist.id, [
            ("2024/200_mid.jpg", "2024", 100),
            ("2024/100_old.jpg", "2024", 100),
            ("2024/300_new.jpg", "2024", 100),
        ])
        desc = db.get_recent_files(artist_id=artist.id, order="desc")
        asc = db.get_recent_files(artist_id=artist.id, order="asc")
        assert [f["filename"] for f in desc] == [
            "2024/300_new.jpg",
            "2024/200_mid.jpg",
            "2024/100_old.jpg",
        ]
        assert [f["filename"] for f in asc] == [
            "2024/100_old.jpg",
            "2024/200_mid.jpg",
            "2024/300_new.jpg",
        ]

    def test_asc_pagination_walks_forward_in_time(self, db_conn):
        """'Oldest first' must paginate the true oldest, not reverse a page."""
        artist = Artist(handle="testartist", site="x.com", source_url="https://x.com/testartist")
        artist.id = db.insert_artist(artist)
        db.insert_file_records(
            None,
            artist.id,
            [(f"2024/{i:03d}.jpg", "2024", i) for i in range(30)],
        )
        page0 = db.get_recent_files(artist_id=artist.id, limit=5, offset=0, order="asc")
        page1 = db.get_recent_files(artist_id=artist.id, limit=5, offset=5, order="asc")
        assert [f["filename"] for f in page0] == [
            "2024/000.jpg", "2024/001.jpg", "2024/002.jpg", "2024/003.jpg", "2024/004.jpg",
        ]
        assert [f["filename"] for f in page1] == [
            "2024/005.jpg", "2024/006.jpg", "2024/007.jpg", "2024/008.jpg", "2024/009.jpg",
        ]

    def test_non_numeric_basename_falls_back_to_downloaded_at(self, db_conn):
        """A basename with no numeric prefix (CAST→0) ties on the ID key and
        sorts by downloaded_at — degraded to today's archive-time behavior."""
        artist = Artist(handle="testartist", site="x.com", source_url="https://x.com/testartist")
        artist.id = db.insert_artist(artist)
        db_conn.execute(
            "INSERT INTO files (job_id, artist_id, filename, year, size_bytes, downloaded_at) "
            "VALUES (NULL, ?, '2024/title-one.jpg', '2024', 100, '2024-01-01T00:00:00')",
            (artist.id,),
        )
        db_conn.execute(
            "INSERT INTO files (job_id, artist_id, filename, year, size_bytes, downloaded_at) "
            "VALUES (NULL, ?, '2024/title-two.jpg', '2024', 100, '2025-01-01T00:00:00')",
            (artist.id,),
        )
        db_conn.commit()
        desc = db.get_recent_files(artist_id=artist.id, order="desc")
        asc = db.get_recent_files(artist_id=artist.id, order="asc")
        # DESC → newer downloaded_at first; ASC → older first.
        assert desc[0]["filename"] == "2024/title-two.jpg"
        assert asc[0]["filename"] == "2024/title-one.jpg"

    def test_invalid_order_raises_valueerror(self, db_conn):
        """order is interpolated into SQL, so it must be whitelisted."""
        with pytest.raises(ValueError, match="order must be"):
            db.get_recent_files(order="sideways")


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
        assert version == 6

    def test_v3_to_v4_migration(self, tmp_path):
        """An existing v3 DB gets the downloaded_at index and bumps to v4."""
        db_path = tmp_path / "v3.db"
        db.configure(db_path)
        conn = db.connect(db_path)
        # Stand up a v3 schema: base tables + files table, pinned to v3.
        conn.executescript(db.SCHEMA_SQL)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id        INTEGER REFERENCES jobs(id),
                artist_id     INTEGER NOT NULL REFERENCES artists(id),
                filename      TEXT NOT NULL,
                year          TEXT NOT NULL,
                size_bytes    INTEGER NOT NULL DEFAULT 0,
                downloaded_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_files_artist_year ON files(artist_id, year);
            CREATE INDEX IF NOT EXISTS idx_files_job ON files(job_id);
            """
        )
        conn.execute("PRAGMA user_version = 3")
        conn.commit()

        db.init_schema(conn)

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 6
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_files_downloaded'"
        ).fetchone()
        assert idx is not None
        conn.close()

    def test_v4_to_v5_migration_dedups(self, tmp_path):
        """An existing v4 DB collapses duplicate files and enforces uniqueness."""
        db_path = tmp_path / "v4.db"
        db.configure(db_path)
        conn = db.connect(db_path)
        # Stand up a v4 schema: base tables + files table + downloaded index, pinned to v4.
        conn.executescript(db.SCHEMA_SQL)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id        INTEGER REFERENCES jobs(id),
                artist_id     INTEGER NOT NULL REFERENCES artists(id),
                filename      TEXT NOT NULL,
                year          TEXT NOT NULL,
                size_bytes    INTEGER NOT NULL DEFAULT 0,
                downloaded_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_files_artist_year ON files(artist_id, year);
            CREATE INDEX IF NOT EXISTS idx_files_job ON files(job_id);
            CREATE INDEX IF NOT EXISTS idx_files_downloaded ON files(downloaded_at DESC);
            """
        )
        conn.execute("PRAGMA user_version = 4")
        conn.commit()

        # Seed one artist, then two duplicate rows for the same (artist_id, filename):
        # the duplicate is larger (200) and lands later than the original (100), so the
        # higher-quality row is the one that must survive.
        conn.execute(
            "INSERT INTO artists (handle, site, source_url) VALUES ('a1', 'x.com', 'https://x.com/a1')"
        )
        artist_id = conn.execute("SELECT id FROM artists").fetchone()[0]
        conn.executemany(
            "INSERT INTO files (artist_id, filename, year, size_bytes, downloaded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (artist_id, "2024/img.jpg", "2024", 100, "2024-01-01 00:00:00"),
                (artist_id, "2024/img.jpg", "2024", 200, "2024-01-02 00:00:00"),
            ],
        )
        conn.commit()

        db.init_schema(conn)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_files_artist_filename'"
        ).fetchone()
        assert idx is not None
        rows = conn.execute(
            "SELECT size_bytes FROM files WHERE filename = '2024/img.jpg'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["size_bytes"] == 200
        conn.close()
