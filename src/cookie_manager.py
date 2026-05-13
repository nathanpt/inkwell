from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def save_file(path: Path, content: bytes, adapter=None) -> None:
    """Write auth file to the named volume and mark auth as valid for the adapter's site."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if adapter:
        adapter.mark_auth_valid()
    logger.info("Auth file saved to %s (%d bytes)", path, len(content))


def get_file_info(path: Path) -> dict:
    """Return file metadata: exists, size, last modified time."""
    if not path.exists():
        return {"exists": False, "size": 0, "modified": None}
    stat = path.stat()
    return {
        "exists": True,
        "size": stat.st_size,
        "modified": stat.st_mtime,
    }


def is_file_expired(path: Path, expiry_warning_days: int) -> bool:
    """Check if a file is older than the warning threshold."""
    info = get_file_info(path)
    if not info["exists"] or info["modified"] is None:
        return True
    age_seconds = time.time() - info["modified"]
    return age_seconds > (expiry_warning_days * 86400)
