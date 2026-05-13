from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from src.models import Artist
from src.sites.base import SiteAdapter

PIXIV_PATTERN = re.compile(
    r"^https?://www\.pixiv\.net/users/(\d+)/?$"
)

CONFIG_PATH = Path("/app/config/gallery-dl.pixiv.conf")
ARCHIVE_DB = Path("/app/data/archive.pixiv.db")
TOKEN_PATH = Path("/app/data/pixiv_refresh_token.txt")
AUTH_STATE_KEY = "auth_valid:pixiv"


class PixivAdapter(SiteAdapter):

    @property
    def name(self) -> str:
        return "pixiv"

    def match_url(self, url: str) -> bool:
        return bool(PIXIV_PATTERN.match(url.strip()))

    def parse_url(self, url: str) -> tuple[str, str]:
        url = url.strip()
        match = PIXIV_PATTERN.match(url)
        if not match:
            raise ValueError(
                "Invalid URL. Must be https://www.pixiv.net/users/{user_id}"
            )
        user_id = match.group(1)
        normalized_url = f"https://www.pixiv.net/users/{user_id}"
        return user_id, normalized_url

    def get_gallery_dl_config_path(self) -> Path:
        return CONFIG_PATH

    def get_archive_db_path(self) -> Path:
        return ARCHIVE_DB

    def get_auth_files(self) -> list[Path]:
        return [TOKEN_PATH] if TOKEN_PATH.exists() else []

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
        return "rate limit" in lower or "token" in lower or "refresh" in lower

    def get_display_handle(self, artist: Artist) -> str:
        return f"#{artist.handle}"
