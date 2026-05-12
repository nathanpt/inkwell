from __future__ import annotations

import logging
import time
from pathlib import Path

from src import db

logger = logging.getLogger(__name__)

COOKIES_PATH = Path("/app/data/cookies.txt")


def save_cookies(conn: sqlite3.Connection, content: bytes) -> None:
    """Write uploaded cookies to the named volume and mark auth as valid."""
    import sqlite3

    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOKIES_PATH.write_bytes(content)
    db.set_state(conn, "auth_session_valid", "1")
    logger.info("Cookies uploaded (%d bytes)", len(content))


def get_cookie_info() -> dict:
    """Return cookie file metadata: exists, size, last modified time."""
    if not COOKIES_PATH.exists():
        return {"exists": False, "size": 0, "modified": None}
    stat = COOKIES_PATH.stat()
    return {
        "exists": True,
        "size": stat.st_size,
        "modified": stat.st_mtime,
    }


def is_auth_valid(conn: sqlite3.Connection) -> bool:
    """Check if auth session is marked as valid."""
    return db.get_state(conn, "auth_session_valid") == "1"


def is_cookies_expired(expiry_warning_days: int) -> bool:
    """Check if cookies file is older than the warning threshold."""
    info = get_cookie_info()
    if not info["exists"] or info["modified"] is None:
        return True
    age_seconds = time.time() - info["modified"]
    return age_seconds > (expiry_warning_days * 86400)
