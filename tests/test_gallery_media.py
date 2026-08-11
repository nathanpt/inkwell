from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from src import db, gallery_media
from src.models import Artist


@pytest.fixture(autouse=True)
def _local_thumb_dir(tmp_path, monkeypatch):
    """Redirect thumbnail caching to a tmp dir (never the real /app/data)."""
    monkeypatch.setattr(gallery_media, "THUMB_DIR", tmp_path / "thumbs")


def _png_bytes(size=(800, 600), color=(8, 16, 24, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


class TestIsImage:
    @pytest.mark.parametrize("name", ["a.jpg", "b.JPEG", "c.png", "d.gif", "e.webp", "f.bmp"])
    def test_accepts_image_extensions(self, name):
        assert gallery_media.is_image(name)

    @pytest.mark.parametrize("name", ["a.txt", "b.zip", "c.mp4", "noext", "2024/photo"])
    def test_rejects_non_image(self, name):
        assert not gallery_media.is_image(name)


class TestReadMediaBytes:
    def test_reads_loose_file(self, tmp_path):
        nas = tmp_path / "nas"
        (nas / "tester" / "2024").mkdir(parents=True)
        data = _png_bytes()
        (nas / "tester" / "2024" / "photo.jpg").write_bytes(data)

        got = gallery_media.read_media_bytes(nas, "tester", "2024", "2024/photo.jpg")
        assert got == data

    def test_reads_zip_with_basename_entry(self, tmp_path):
        """Zip entries are the path relative to the YEAR dir (the basename),
        exactly as src/zipper._create_zip writes them."""
        nas = tmp_path / "nas"
        year_dir = nas / "tester" / "2024"
        year_dir.mkdir(parents=True)
        data = _png_bytes()
        (year_dir / "photo.jpg").write_bytes(data)

        # Build the zip like _create_zip: arcname = file relative_to(year_dir)
        with zipfile.ZipFile(
            nas / "tester" / "2024.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=6
        ) as zf:
            zf.write(year_dir / "photo.jpg", "photo.jpg")

        # Loose files are gone in steady state
        (year_dir / "photo.jpg").unlink()
        year_dir.rmdir()

        got = gallery_media.read_media_bytes(nas, "tester", "2024", "2024/photo.jpg")
        assert got == data

    def test_missing_returns_none(self, tmp_path):
        nas = tmp_path / "nas"
        assert gallery_media.read_media_bytes(nas, "tester", "2024", "2024/none.jpg") is None

    def test_zip_missing_entry_returns_none(self, tmp_path):
        nas = tmp_path / "nas"
        (nas / "tester").mkdir(parents=True)
        with zipfile.ZipFile(nas / "tester" / "2024.zip", "w") as zf:
            zf.writestr("other.jpg", b"x")
        assert (
            gallery_media.read_media_bytes(nas, "tester", "2024", "2024/photo.jpg")
            is None
        )


class TestThumbnails:
    def _seed_loose(self, nas: Path) -> bytes:
        (nas / "tester" / "2024").mkdir(parents=True)
        data = _png_bytes(size=(1500, 900))
        (nas / "tester" / "2024" / "photo.jpg").write_bytes(data)
        return data

    def test_generates_jpeg_within_max_side(self, tmp_path):
        nas = tmp_path / "nas"
        self._seed_loose(nas)

        thumb = gallery_media.get_thumbnail(nas, "tester", "2024", "2024/photo.jpg")
        assert thumb[:3] == b"\xff\xd8\xff"  # JPEG SOI marker
        im = Image.open(io.BytesIO(thumb))
        assert max(im.size) <= gallery_media.THUMB_MAX
        assert im.format == "JPEG"

    def test_cache_hit_avoids_reread(self, tmp_path):
        nas = tmp_path / "nas"
        self._seed_loose(nas)

        first = gallery_media.get_thumbnail(nas, "tester", "2024", "2024/photo.jpg")
        cached = gallery_media.thumb_path_for("tester", "2024", "2024/photo.jpg")
        assert cached.is_file(), "thumbnail was cached on disk"

        # Remove the source entirely; a cache hit must still serve the thumbnail
        # without touching the (now missing) NAS file.
        import shutil

        shutil.rmtree(nas / "tester")
        second = gallery_media.get_thumbnail(nas, "tester", "2024", "2024/photo.jpg")
        assert second == first

    def test_missing_source_raises(self, tmp_path):
        nas = tmp_path / "nas"
        with pytest.raises(FileNotFoundError):
            gallery_media.get_thumbnail(nas, "tester", "2024", "2024/none.jpg")


class TestGalleryQueries:
    def test_count_and_stats_respect_year_filter(self, db_conn):
        artist = Artist(handle="a", site="x.com", source_url="https://x.com/a")
        artist.id = db.insert_artist(artist)
        db.insert_file_records(
            None,
            artist.id,
            [
                ("2024/a.jpg", "2024", 1000),
                ("2024/b.jpg", "2024", 2000),
                ("2025/c.png", "2025", 4000),
            ],
        )

        assert db.count_files(artist_id=artist.id) == 3
        assert db.count_files(artist_id=artist.id, years=["2024"]) == 2
        assert db.count_files(artist_id=artist.id, years=["2025"]) == 1

        n_all, bytes_all = db.gallery_stats(artist_id=artist.id)
        assert (n_all, bytes_all) == (3, 7000)

        n_24, bytes_24 = db.gallery_stats(artist_id=artist.id, years=["2024"])
        assert (n_24, bytes_24) == (2, 3000)

    def test_distinct_years_newest_first(self, db_conn):
        artist = Artist(handle="a", site="x.com", source_url="https://x.com/a")
        artist.id = db.insert_artist(artist)
        db.insert_file_records(
            None,
            artist.id,
            [("2023/a.jpg", "2023", 1), ("2025/b.png", "2025", 1), ("2024/c.jpg", "2024", 1)],
        )
        assert db.distinct_years(artist_id=artist.id) == ["2025", "2024", "2023"]

    def test_get_file_by_id(self, db_conn):
        artist = Artist(handle="a", site="x.com", source_url="https://x.com/a")
        artist.id = db.insert_artist(artist)
        db.insert_file_records(None, artist.id, [("2024/a.jpg", "2024", 100)])
        f = db.get_file(1)
        assert f is not None
        assert f["filename"] == "2024/a.jpg"
        assert db.get_file(99999) is None
