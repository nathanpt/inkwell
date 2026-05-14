from __future__ import annotations

import logging
import os
import re
import zipfile
from pathlib import Path

from src.config_loader import Config

logger = logging.getLogger(__name__)

YEAR_PATTERN = re.compile(r"^\d{4}$")


def zip_year_dir(
    artist_dir: Path, year: str, compression_level: int = 6
) -> Path | None:
    """Zip a year subdirectory under an artist dir.

    Creates {artist_dir}/{year}.zip, verifies integrity, then deletes
    the loose files and removes the directory. Returns the zip path or None.
    """
    year_dir = artist_dir / year
    if not year_dir.is_dir():
        return None

    files = [
        f
        for f in year_dir.rglob("*")
        if f.is_file()
    ]
    if not files:
        return None

    zip_path = artist_dir / f"{year}.zip"

    try:
        _create_zip(zip_path, year_dir, files, compression_level)
        _verify_zip(zip_path, year_dir, files)
    except Exception:
        logger.exception("ZIP creation/verification failed for %s", zip_path)
        # Remove potentially corrupt zip so we can retry later
        zip_path.unlink(missing_ok=True)
        return None

    # Verification passed — safe to delete source files
    for f in files:
        try:
            f.unlink()
        except OSError:
            logger.warning("Failed to delete %s after zipping", f)

    # Remove empty directories left behind
    for root, dirs, _ in os.walk(year_dir, topdown=False):
        for d in dirs:
            try:
                (Path(root) / d).rmdir()
            except OSError:
                pass
    try:
        year_dir.rmdir()
    except OSError:
        logger.warning("Could not remove year dir %s (may not be empty)", year_dir)

    logger.info("Zipped %d file(s) into %s", len(files), zip_path)
    return zip_path


def _create_zip(
    zip_path: Path, base_dir: Path, files: list[Path], compression_level: int
) -> None:
    with zipfile.ZipFile(
        zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=compression_level
    ) as zf:
        for f in files:
            arcname = str(f.relative_to(base_dir))
            zf.write(f, arcname)


def _verify_zip(
    zip_path: Path, base_dir: Path, files: list[Path]
) -> None:
    with zipfile.ZipFile(zip_path, "r") as zf:
        # Test ZIP integrity
        bad = zf.testzip()
        if bad is not None:
            raise RuntimeError(f"Corrupt entry in ZIP: {bad}")

        # Verify all expected files are present
        names_in_zip = set(zf.namelist())
        expected = {str(f.relative_to(base_dir)) for f in files}
        if names_in_zip != expected:
            missing = expected - names_in_zip
            raise RuntimeError(f"ZIP missing files: {missing}")


def zip_artist_dirs(
    nas_path: Path, artist_handle: str, compression_level: int = 6
) -> list[Path]:
    """Zip all eligible year directories for an artist.

    Skips year dirs that already have a matching .zip with a newer mtime.
    Returns list of created zip paths.
    """
    artist_dir = nas_path / artist_handle
    if not artist_dir.is_dir():
        return []

    created: list[Path] = []
    for entry in sorted(artist_dir.iterdir()):
        if not entry.is_dir() or not YEAR_PATTERN.match(entry.name):
            continue

        zip_path = artist_dir / f"{entry.name}.zip"

        # Skip if zip exists and is newer than the directory
        if zip_path.exists() and zip_path.stat().st_mtime >= _dir_mtime(entry):
            continue

        result = zip_year_dir(artist_dir, entry.name, compression_level)
        if result:
            created.append(result)

    return created


def zip_all_artists(config: Config) -> dict[str, list[Path]]:
    """Retroactively zip all artist directories on the NAS.

    Returns {artist_handle: [zip_paths]}.
    """
    nas_path = Path(config.nas.mount_path)
    if not nas_path.is_dir():
        logger.warning("NAS path %s does not exist", nas_path)
        return {}

    results: dict[str, list[Path]] = {}
    for entry in sorted(nas_path.iterdir()):
        if not entry.is_dir():
            continue
        zipped = zip_artist_dirs(nas_path, entry.name, config.zip.compression_level)
        if zipped:
            results[entry.name] = zipped

    return results


def _dir_mtime(path: Path) -> float:
    """Return the most recent mtime of any file in a directory tree."""
    latest = path.stat().st_mtime
    for f in path.rglob("*"):
        if f.is_file():
            latest = max(latest, f.stat().st_mtime)
    return latest
