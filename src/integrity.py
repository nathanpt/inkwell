from __future__ import annotations

import json
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src import db
from src.config_loader import Config

logger = logging.getLogger(__name__)

# Matches a year optionally followed by a sibling suffix: "2026", "2026 (1)", "2026(2)"
YEAR_STEM_RE = re.compile(r"^(\d{4})(?: \(\d+\)|\(\d+\))?$")


@dataclass
class MissingRow:
    file_id: int
    artist_id: int
    handle: str
    site: str
    year: str
    filename: str  # artist-relative, e.g. "2026/<basename>"


@dataclass
class IntegrityReport:
    total: int
    ok: int
    missing: list[MissingRow] = field(default_factory=list)
    sibling_zips: int = 0


def find_sibling_zips(artist_dir: Path, year: str) -> list[Path]:
    """Sibling zips like ``<year> (N).zip`` / ``<year>(N).zip`` (never the canonical zip)."""
    return sorted(
        p
        for p in artist_dir.glob(f"{year}*.zip")
        if p.name != f"{year}.zip"
        and YEAR_STEM_RE.match(p.stem)
        and p.stem != year
    )


def _zip_names(zip_path: Path, cache: dict) -> set[str]:
    """Cached namelist for a zip path; empty set if unreadable."""
    cached = cache.get(str(zip_path))
    if cached is not None:
        return cached
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
    except (zipfile.BadZipFile, OSError):
        names = set()
    cache[str(zip_path)] = names
    return names


def _sibling_paths(artist_dir: Path, year: str, cache: dict) -> list[Path]:
    """Memoized sibling-zip list for an (artist_dir, year) combo."""
    key = ("sib", str(artist_dir), year)
    cached = cache.get(key)
    if cached is not None:
        return cached
    sibs = find_sibling_zips(artist_dir, year)
    cache[key] = sibs
    return sibs


def file_available(
    nas_path: Path, handle: str, year: str, filename: str, cache: dict
) -> bool:
    """True if the file is reachable by the gallery: loose, in the canonical zip,
    or in a sibling zip.

    Checks only the bare basename of ``filename``: an entry stored as
    ``<year>/<basename>`` is unreadable by ``gallery_media.read_media_bytes``, so
    it counts as missing and repair re-fetches it. ``cache`` maps zip path →
    namelist set (mandatory: otherwise every row re-opens the zip).
    """
    base = Path(filename).name
    artist_dir = nas_path / handle

    # (a) loose file on disk
    if (artist_dir / filename).is_file():
        return True

    # (b) canonical year zip
    canonical = artist_dir / f"{year}.zip"
    if base in _zip_names(canonical, cache):
        return True

    # (c) any sibling zip
    for sib in _sibling_paths(artist_dir, year, cache):
        if base in _zip_names(sib, cache):
            return True

    return False


def check_integrity(config: Config) -> IntegrityReport:
    """Scan every files row and classify it as OK or missing (unreachable on disk).

    Stores a summary under the ``integrity:last_check`` state key and logs it.
    """
    nas_path = Path(config.nas.mount_path)
    cache: dict = {}
    rows = db.get_all_file_rows()

    missing: list[MissingRow] = []
    ok = 0
    sibling_zips_seen: set[str] = set()

    for row in rows:
        handle = row["artist_handle"]
        year = row["year"]
        artist_dir = nas_path / handle
        for sib in _sibling_paths(artist_dir, year, cache):
            sibling_zips_seen.add(str(sib))

        if file_available(nas_path, handle, year, row["filename"], cache):
            ok += 1
        else:
            missing.append(
                MissingRow(
                    file_id=row["id"],
                    artist_id=row["artist_id"],
                    handle=handle,
                    site=row["artist_site"],
                    year=year,
                    filename=row["filename"],
                )
            )

    # Per-artist missing counts: every scanned artist seeded to 0, so an artist
    # absent from this dict means "not present at last check" (UI shows "—").
    by_artist: dict[str, int] = {str(row["artist_id"]): 0 for row in rows}
    for m in missing:
        by_artist[str(m.artist_id)] += 1

    report = IntegrityReport(
        total=len(rows),
        ok=ok,
        missing=missing,
        sibling_zips=len(sibling_zips_seen),
    )

    summary = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "total": report.total,
        "ok": report.ok,
        "missing": len(report.missing),
        "sibling_zips": report.sibling_zips,
        "by_artist": by_artist,
    }
    db.set_state("integrity:last_check", json.dumps(summary))
    level = "WARNING" if report.missing else "INFO"
    db.insert_log(
        level,
        "integrity",
        f"Integrity check: {report.ok}/{report.total} OK, "
        f"{len(report.missing)} missing, {report.sibling_zips} sibling zip(s)",
    )
    logger.info("Integrity check complete: %s", summary)
    return report


def consolidate_all_sibling_zips(config: Config) -> tuple[int, int]:
    """Merge every ``<year> (N).zip`` sibling into its canonical ``<year>.zip``.

    Returns ``(entries_merged, siblings_removed)``. On any failure for a sibling
    it is logged and left in place — never deleted.
    """
    nas_path = Path(config.nas.mount_path)
    if not nas_path.is_dir():
        return (0, 0)

    level = config.zip.compression_level
    entries_merged = 0
    siblings_removed = 0

    for artist_dir in sorted(p for p in nas_path.iterdir() if p.is_dir()):
        # Group sibling zips by the year parsed from their stem.
        for sib in sorted(artist_dir.glob("*.zip")):
            m = YEAR_STEM_RE.match(sib.stem)
            if not m or sib.stem == m.group(1):
                continue  # canonical zip or non-year name
            year = m.group(1)
            try:
                merged = _merge_sibling(sib, artist_dir / f"{year}.zip", level)
            except Exception:
                logger.exception("Failed to consolidate sibling zip %s", sib)
                continue
            entries_merged += merged
            try:
                sib.unlink()
                siblings_removed += 1
            except OSError:
                logger.warning("Could not remove consolidated sibling %s", sib)

    db.insert_log(
        "INFO",
        "integrity",
        f"Sibling-zip consolidation: {entries_merged} entry/entries merged, "
        f"{siblings_removed} sibling(s) removed",
    )
    return (entries_merged, siblings_removed)


def _merge_sibling(sib: Path, canonical: Path, compression_level: int) -> int:
    """Append a sibling's entries into the canonical zip, dedup by name, verify,
    and return how many entries were written. Raises on any failure (caller
    leaves the sibling in place)."""
    with zipfile.ZipFile(sib, "r") as szf:
        if szf.testzip() is not None:
            raise RuntimeError(f"sibling {sib} has a corrupt entry")
        sib_infos = szf.infolist()
        existing: set[str] = set()
        if canonical.is_file():
            try:
                with zipfile.ZipFile(canonical, "r") as czf:
                    existing = set(czf.namelist())
            except (zipfile.BadZipFile, OSError) as e:
                raise RuntimeError(f"canonical {canonical} unreadable: {e}")

        written_names: list[str] = []
        with zipfile.ZipFile(
            canonical,
            "a",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=compression_level,
        ) as azf:
            current = set(azf.namelist())
            existing |= current
            for info in sib_infos:
                if info.filename in existing:
                    continue
                azf.writestr(info, szf.read(info.filename))
                existing.add(info.filename)
                written_names.append(info.filename)

    # Verify: canonical opens, tests clean, and every written name is present.
    with zipfile.ZipFile(canonical, "r") as czf:
        if czf.testzip() is not None:
            raise RuntimeError(f"canonical {canonical} corrupt after merge")
        names = set(czf.namelist())
    for name in written_names:
        if name not in names:
            raise RuntimeError(f"merged entry {name!r} missing from {canonical}")

    return len(written_names)
