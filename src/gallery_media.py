from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# Image extensions the gallery will thumbnail and display.
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

# Thumbnails live on the LOCAL /app/data volume, never on the NAS. This is the
# steady-state production path; tests monkeypatch this attribute to a tmp dir.
THUMB_DIR = Path("/app/data/thumbnails")
THUMB_MAX = 300  # longest side, px
THUMB_QUALITY = 80


def is_image(filename: str) -> bool:
    """Return True if ``filename`` has a recognized image extension."""
    return Path(filename).suffix.lower() in IMAGE_EXTS


def source_zip_path(nas_path: Path, artist_handle: str, year: str) -> Path:
    """Return the steady-state zip path for an artist/year."""
    return nas_path / artist_handle / f"{year}.zip"


def _zip_entry(year: str, filename: str) -> str:
    """Compute the zip entry name for an artist-relative filename.

    Inkwell zips store each member as its path relative to the YEAR dir (the
    basename for gallery-dl's flat downloads), while ``files.filename`` is
    artist-relative (e.g. ``2024/photo.jpg``). The entry is therefore
    ``filename`` made relative to ``year``. We fall back to the basename if the
    filename is not prefixed with the year (defensive; should not happen).
    """
    try:
        return str(Path(filename).relative_to(year))
    except ValueError:
        return Path(filename).name


def read_media_bytes(
    nas_path: Path, artist_handle: str, year: str, filename: str
) -> bytes | None:
    """Return the raw bytes of a file from loose storage or its year zip.

    Loose files are transient (zipped on job completion); the zip is the steady
    state. Zip reads use ``zipfile`` random-access member reads — the archive is
    never extracted. Returns ``None`` if the file is in neither location or the
    zip entry is absent.
    """
    loose = nas_path / artist_handle / filename
    if loose.is_file():
        return loose.read_bytes()

    zp = source_zip_path(nas_path, artist_handle, year)
    if zp.is_file():
        entry = _zip_entry(year, filename)
        try:
            with zipfile.ZipFile(zp) as zf:
                return zf.read(entry)
        except KeyError:
            logger.warning("Zip entry %r not found in %s", entry, zp)
            return None

    return None


def thumb_path_for(artist_handle: str, year: str, filename: str) -> Path:
    """Return the thumbnail cache path for a file.

    Thumbnails are flattened to the basename within ``artist/year/``; names are
    unique within a year in Inkwell's storage so this cannot collide.
    """
    return THUMB_DIR / artist_handle / year / Path(filename).name


def make_thumbnail(src_bytes: bytes) -> bytes:
    """Generate JPEG thumbnail bytes (longest side ``THUMB_MAX`` px).

    Converts RGBA/palette/gray images to RGB so JPEG encoding succeeds. Raises
    on corrupt or unsupported images; callers treat that as "no thumbnail".
    """
    img = Image.open(io.BytesIO(src_bytes))
    img.thumbnail((THUMB_MAX, THUMB_MAX))
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=THUMB_QUALITY)
    return buf.getvalue()


def get_thumbnail(
    nas_path: Path, artist_handle: str, year: str, filename: str
) -> bytes:
    """Return thumbnail bytes for a file, generating and caching on first use.

    On a cache hit no NAS read occurs. Cache writes are best-effort: a read-only
    or full local volume degrades to recompute-each-view rather than raising.
    Raises if the source media cannot be found, or if the image is corrupt/
    unsupported (``make_thumbnail``); the caller renders a placeholder.
    """
    dest = thumb_path_for(artist_handle, year, filename)
    if dest.is_file():
        return dest.read_bytes()

    src = read_media_bytes(nas_path, artist_handle, year, filename)
    if src is None:
        raise FileNotFoundError(f"Media not found: {artist_handle}/{filename}")

    data = make_thumbnail(src)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    except OSError:
        logger.warning("Could not cache thumbnail at %s; will recompute", dest)
    return data
