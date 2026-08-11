from __future__ import annotations

import json
import logging
import re
import urllib.request
from pathlib import Path

from src.models import Artist
from src.sites.base import SiteAdapter

logger = logging.getLogger(__name__)

PIXIV_PATTERN = re.compile(
    r"^https?://www\.pixiv\.net/(?:[a-z]{2}/)?users/(\d+)(?:/[a-zA-Z]+)?/?$"
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
        if CONFIG_PATH.is_dir():
            return Path(f"/app/defaults/{CONFIG_PATH.name}")
        return CONFIG_PATH

    def get_archive_db_path(self) -> Path:
        return ARCHIVE_DB

    def get_auth_files(self) -> list[Path]:
        return []

    def get_refresh_token(self) -> str | None:
        if TOKEN_PATH.exists():
            return TOKEN_PATH.read_text().strip() or None
        return None

    def is_auth_valid(self) -> bool:
        from src import db
        return db.get_state(AUTH_STATE_KEY) != "0"

    def mark_auth_invalid(self) -> None:
        from src import db
        db.set_state(AUTH_STATE_KEY, "0")

    def mark_auth_valid(self) -> None:
        from src import db
        db.set_state(AUTH_STATE_KEY, "1")

    def detect_auth_error(self, stderr: str) -> bool:
        lower = stderr.lower()
        return "token" in lower or "refresh" in lower

    def detect_rate_limit_error(self, stderr: str) -> bool:
        lower = stderr.lower()
        return "429" in lower or "rate limit" in lower

    def get_display_handle(self, artist: Artist) -> str:
        if artist.handle.isdigit():
            return f"#{artist.handle}"
        return artist.handle

    def resolve_handle(self, handle: str) -> str:
        """Resolve a Pixiv user ID to the artist's display name via the AJAX API."""
        return resolve_pixiv_handle(handle)

    def build_post_url(self, handle: str, post_id: str) -> str | None:
        return f"https://www.pixiv.net/artworks/{post_id}"


def resolve_pixiv_handle(user_id: str) -> str:
    """Resolve a Pixiv user ID to the artist's display name via the AJAX API.

    Returns the original user_id if resolution fails.
    """
    url = f"https://www.pixiv.net/ajax/user/{user_id}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Referer": "https://www.pixiv.net/",
        "Accept-Language": "en-US,en;q=0.5",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        name = data.get("body", {}).get("name")
        if name:
            logger.info("Resolved Pixiv user %s to %q", user_id, name)
            return name
    except Exception:
        logger.warning("Could not resolve Pixiv user %s, using numeric ID", user_id, exc_info=True)
    return user_id


def migrate_pixiv_handles() -> int:
    """One-time migration: resolve numeric Pixiv handles to display names.

    Returns the number of artists updated.
    """
    from src import db

    artists = db.get_all_artists()
    updated = 0
    for artist in artists:
        if artist.site == "pixiv" and artist.handle.isdigit():
            new_handle = resolve_pixiv_handle(artist.handle)
            if new_handle != artist.handle:
                db.update_artist_handle(artist.id, new_handle)
                updated += 1
                logger.info("Migrated Pixiv artist %d: %s -> %s", artist.id, artist.handle, new_handle)
    return updated
