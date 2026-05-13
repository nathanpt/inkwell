from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from src.models import Artist


class SiteAdapter(ABC):
    """Base class for site-specific download adapters."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def match_url(self, url: str) -> bool:
        ...

    @abstractmethod
    def parse_url(self, url: str) -> tuple[str, str]:
        """Return (handle, normalized_url). Raises ValueError on invalid URL."""
        ...

    @abstractmethod
    def get_gallery_dl_config_path(self) -> Path:
        ...

    @abstractmethod
    def get_archive_db_path(self) -> Path:
        ...

    @abstractmethod
    def get_auth_files(self) -> list[Path]:
        ...

    @abstractmethod
    def is_auth_valid(self) -> bool:
        ...

    @abstractmethod
    def mark_auth_invalid(self) -> None:
        ...

    @abstractmethod
    def mark_auth_valid(self) -> None:
        ...

    @abstractmethod
    def detect_auth_error(self, stderr: str) -> bool:
        ...

    @abstractmethod
    def detect_rate_limit_error(self, stderr: str) -> bool:
        ...

    @abstractmethod
    def get_display_handle(self, artist: Artist) -> str:
        ...


class SiteRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, SiteAdapter] = {}

    def register(self, adapter: SiteAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def get(self, site: str) -> SiteAdapter:
        adapter = self._adapters.get(site)
        if not adapter:
            raise ValueError(f"Unknown site: {site}")
        return adapter

    def match_url(self, url: str) -> SiteAdapter | None:
        for adapter in self._adapters.values():
            if adapter.match_url(url):
                return adapter
        return None

    def all_adapters(self) -> list[SiteAdapter]:
        return list(self._adapters.values())


def create_registry() -> SiteRegistry:
    """Create a registry with all built-in site adapters."""
    from src.sites.xcom import XComAdapter
    from src.sites.pixiv import PixivAdapter
    from src.sites.deviantart import DeviantArtAdapter

    registry = SiteRegistry()
    registry.register(XComAdapter())
    registry.register(PixivAdapter())
    registry.register(DeviantArtAdapter())
    return registry
