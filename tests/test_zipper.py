from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.config_loader import Config, NASConfig, ZipConfig
from src.zipper import zip_all_artists, zip_artist_dirs, zip_year_dir


class TestZipYearDir:
    def test_creates_zip_and_removes_files(self, tmp_path):
        year_dir = tmp_path / "2024"
        year_dir.mkdir()
        (year_dir / "img1.jpg").write_bytes(b"a" * 100)
        (year_dir / "img2.png").write_bytes(b"b" * 200)

        result = zip_year_dir(tmp_path, "2024")

        assert result is not None
        assert result.name == "2024.zip"
        assert not year_dir.exists()
        with zipfile.ZipFile(result) as zf:
            assert set(zf.namelist()) == {"img1.jpg", "img2.png"}

    def test_preserves_subdirectory_structure(self, tmp_path):
        year_dir = tmp_path / "2023"
        sub = year_dir / "thumbs"
        sub.mkdir(parents=True)
        (sub / "small.jpg").write_bytes(b"x" * 50)

        result = zip_year_dir(tmp_path, "2023")

        assert result is not None
        with zipfile.ZipFile(result) as zf:
            assert "thumbs/small.jpg" in zf.namelist()

    def test_skips_nonexistent_dir(self, tmp_path):
        result = zip_year_dir(tmp_path, "2024")
        assert result is None

    def test_skips_empty_dir(self, tmp_path):
        (tmp_path / "2024").mkdir()
        result = zip_year_dir(tmp_path, "2024")
        assert result is None

    def test_does_not_delete_on_verification_failure(self, tmp_path):
        year_dir = tmp_path / "2024"
        year_dir.mkdir()
        (year_dir / "img.jpg").write_bytes(b"data")

        with patch("src.zipper._verify_zip", side_effect=RuntimeError("corrupt")):
            result = zip_year_dir(tmp_path, "2024")

        assert result is None
        assert (year_dir / "img.jpg").exists()
        # Corrupt zip should have been cleaned up
        assert not (tmp_path / "2024.zip").exists()


class TestZipArtistDirs:
    def test_finds_year_dirs(self, tmp_path):
        artist_dir = tmp_path / "someartist"
        (artist_dir / "2024").mkdir(parents=True)
        (artist_dir / "2024" / "a.jpg").write_bytes(b"x" * 50)
        (artist_dir / "2023").mkdir(parents=True)
        (artist_dir / "2023" / "b.png").write_bytes(b"y" * 100)

        result = zip_artist_dirs(tmp_path, "someartist")

        assert len(result) == 2
        assert (artist_dir / "2024.zip").exists()
        assert (artist_dir / "2023.zip").exists()

    def test_ignores_non_year_dirs(self, tmp_path):
        artist_dir = tmp_path / "artist"
        (artist_dir / "thumbnails").mkdir(parents=True)
        (artist_dir / "thumbnails" / "t.jpg").write_bytes(b"x")
        (artist_dir / "2024").mkdir(parents=True)
        (artist_dir / "2024" / "a.jpg").write_bytes(b"x")

        result = zip_artist_dirs(tmp_path, "artist")

        assert len(result) == 1
        assert (artist_dir / "thumbnails").exists()

    def test_skips_if_zip_newer(self, tmp_path):
        artist_dir = tmp_path / "artist"
        year_dir = artist_dir / "2024"
        year_dir.mkdir(parents=True)

        import time
        (year_dir / "old.jpg").write_bytes(b"x")
        # Create zip that is newer than file
        zip_path = artist_dir / "2024.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("old.jpg", "x")
        # Ensure zip is strictly newer
        time.sleep(0.1)
        zip_path.touch()

        result = zip_artist_dirs(tmp_path, "artist")
        assert result == []

    def test_replaces_stale_zip(self, tmp_path):
        artist_dir = tmp_path / "artist"
        year_dir = artist_dir / "2024"
        year_dir.mkdir(parents=True)

        # Write old file to filesystem AND create old zip
        (year_dir / "old.jpg").write_bytes(b"x")
        zip_path = artist_dir / "2024.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("old.jpg", "x")

        # Add a new file to the dir (makes dir mtime > zip mtime)
        import time
        time.sleep(0.1)
        (year_dir / "new.jpg").write_bytes(b"y")

        result = zip_artist_dirs(tmp_path, "artist")

        assert len(result) == 1
        with zipfile.ZipFile(result[0]) as zf:
            assert set(zf.namelist()) == {"old.jpg", "new.jpg"}

    def test_nonexistent_artist_dir(self, tmp_path):
        result = zip_artist_dirs(tmp_path, "ghost")
        assert result == []


class TestZipAllArtists:
    def test_zips_multiple_artists(self, tmp_path):
        for artist in ("alice", "bob"):
            year = tmp_path / artist / "2024"
            year.mkdir(parents=True)
            (year / "img.jpg").write_bytes(b"data")

        config = Config(nas=NASConfig(mount_path=str(tmp_path)), zip=ZipConfig())
        result = zip_all_artists(config)

        assert "alice" in result
        assert "bob" in result
        assert (tmp_path / "alice" / "2024.zip").exists()
        assert (tmp_path / "bob" / "2024.zip").exists()

    def test_empty_nas(self, tmp_path):
        config = Config(nas=NASConfig(mount_path=str(tmp_path)), zip=ZipConfig())
        result = zip_all_artists(config)
        assert result == {}

    def test_missing_nas_path(self, tmp_path):
        config = Config(nas=NASConfig(mount_path=str(tmp_path / "nope")), zip=ZipConfig())
        result = zip_all_artists(config)
        assert result == {}


class TestPostJobZipIntegration:
    def test_zip_called_on_success(self, tmp_path):
        from src import db
        from src.downloader import download_artist
        from src.models import Artist

        db.configure(tmp_path / "test.db")
        conn = db.connect(tmp_path / "test.db")
        db.init_schema(conn)
        conn.close()
        db.seed_state()

        from src.config_loader import DownloadConfig
        config = Config(
            nas=NASConfig(mount_path=str(tmp_path / "nas")),
            download=DownloadConfig(retry_attempts=1, retry_backoff=[0], timeout=10, inter_artist_cooldown=[0, 0]),
            zip=ZipConfig(enabled=True, on_job_complete=True),
        )
        (tmp_path / "nas").mkdir()

        from src.sites.xcom import XComAdapter
        from src.sites.base import SiteRegistry
        registry = SiteRegistry()
        registry.register(XComAdapter())

        artist = Artist(handle="testz", site="x.com", source_url="https://x.com/testz")
        artist.id = db.insert_artist(artist)

        # Create pre-existing year dir with files
        year_dir = tmp_path / "nas" / "testz" / "2024"
        year_dir.mkdir(parents=True)
        (year_dir / "old.jpg").write_bytes(b"x" * 50)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("src.downloader._run_gallery_dl", return_value=mock_result):
            job = download_artist(artist, config, registry)

        assert job.status == "success"
        # Year dir should have been zipped
        assert (tmp_path / "nas" / "testz" / "2024.zip").exists()
        assert not year_dir.exists()

    def test_zip_disabled_skips(self, tmp_path):
        from src import db
        from src.downloader import download_artist
        from src.models import Artist

        db.configure(tmp_path / "test.db")
        conn = db.connect(tmp_path / "test.db")
        db.init_schema(conn)
        conn.close()
        db.seed_state()

        from src.config_loader import DownloadConfig
        config = Config(
            nas=NASConfig(mount_path=str(tmp_path / "nas")),
            download=DownloadConfig(retry_attempts=1, retry_backoff=[0], timeout=10, inter_artist_cooldown=[0, 0]),
            zip=ZipConfig(enabled=False),
        )
        (tmp_path / "nas").mkdir()

        from src.sites.xcom import XComAdapter
        from src.sites.base import SiteRegistry
        registry = SiteRegistry()
        registry.register(XComAdapter())

        artist = Artist(handle="testz", site="x.com", source_url="https://x.com/testz")
        artist.id = db.insert_artist(artist)

        year_dir = tmp_path / "nas" / "testz" / "2024"
        year_dir.mkdir(parents=True)
        (year_dir / "old.jpg").write_bytes(b"x" * 50)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("src.downloader._run_gallery_dl", return_value=mock_result):
            job = download_artist(artist, config, registry)

        assert job.status == "success"
        # Year dir should NOT have been zipped
        assert year_dir.exists()
        assert not (tmp_path / "nas" / "testz" / "2024.zip").exists()
