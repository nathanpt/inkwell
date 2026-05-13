from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from src.models import Artist
from src.sites.base import SiteAdapter

X_COM_PATTERN = re.compile(
    r"^https?://(x\.com|twitter\.com)/([a-zA-Z0-9_]{1,15})/?$"
)

CONFIG_PATH = Path("/app/config/gallery-dl.xcom.conf")
ARCHIVE_DB = Path("/app/data/archive.xcom.db")
COOKIES_PATH = Path("/app/data/cookies.txt")
AUTH_STATE_KEY = "auth_valid:x.com"


class XComAdapter(SiteAdapter):

    @property
    def name(self) -> str:
        return "x.com"

    def match_url(self, url: str) -> bool:
        return bool(X_COM_PATTERN.match(url.strip()))

    def parse_url(self, url: str) -> tuple[str, str]:
        url = url.strip()
        match = X_COM_PATTERN.match(url)
        if not match:
            raise ValueError(
                "Invalid URL. Must be https://x.com/{handle} or https://twitter.com/{handle}"
            )
        handle = match.group(2)
        normalized_url = f"https://x.com/{handle}"
        return handle, normalized_url

    def get_gallery_dl_config_path(self) -> Path:
        return CONFIG_PATH

    def get_archive_db_path(self) -> Path:
        return ARCHIVE_DB

    def get_auth_files(self) -> list[Path]:
        return [COOKIES_PATH] if COOKIES_PATH.exists() else []

    def is_auth_valid(self, conn: sqlite3.Connection) -> bool:
        from src import db
        return db.get_state(conn, AUTH_STATE_KEY) != "0"

    def mark_auth_invalid(self, conn: sqlite3.Connection) -> None:
        from src import db
        db.set_state(conn, AUTH_STATE_KEY, "0")

    def mark_auth_valid(self, conn: sqlite3.Connection) -> None:
        from src import db
        db.set_state(conn, AUTH_STATE_KEY, "1")

    def detect_auth_error(self, stderr: str) -> bool:
        lower = stderr.lower()
        return "login" in lower or "unauthorized" in lower or "401" in lower

    def detect_rate_limit_error(self, stderr: str) -> bool:
        lower = stderr.lower()
        return "429" in lower or "rate limit" in lower

    def get_display_handle(self, artist: Artist) -> str:
        return f"@{artist.handle}"
