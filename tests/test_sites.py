from __future__ import annotations

import pytest

from src.models import Artist
from src.sites.base import SiteRegistry
from src.sites.xcom import XComAdapter
from src.sites.pixiv import PixivAdapter
from src.sites.deviantart import DeviantArtAdapter


@pytest.fixture
def registry():
    reg = SiteRegistry()
    reg.register(XComAdapter())
    reg.register(PixivAdapter())
    reg.register(DeviantArtAdapter())
    return reg


# --- X.com ---


class TestXComAdapter:
    def setup_method(self):
        self.adapter = XComAdapter()

    def test_name(self):
        assert self.adapter.name == "x.com"

    def test_match_valid_x_com(self):
        assert self.adapter.match_url("https://x.com/artistname")
        assert self.adapter.match_url("https://twitter.com/artistname")

    def test_match_invalid(self):
        assert not self.adapter.match_url("https://pixiv.net/users/123")
        assert not self.adapter.match_url("https://x.com/artist/status/123")

    def test_parse_x_com(self):
        handle, url = self.adapter.parse_url("https://x.com/artistname")
        assert handle == "artistname"
        assert url == "https://x.com/artistname"

    def test_parse_twitter_normalizes(self):
        handle, url = self.adapter.parse_url("https://twitter.com/artistname")
        assert handle == "artistname"
        assert url == "https://x.com/artistname"

    def test_parse_rejects_invalid(self):
        with pytest.raises(ValueError):
            self.adapter.parse_url("https://instagram.com/artist")

    def test_detect_auth_error(self):
        assert self.adapter.detect_auth_error("Error: login required")
        assert self.adapter.detect_auth_error("401 Unauthorized")
        assert not self.adapter.detect_auth_error("network timeout")

    def test_detect_rate_limit_error(self):
        assert self.adapter.detect_rate_limit_error("429 Too Many Requests")
        assert self.adapter.detect_rate_limit_error("rate limit exceeded")
        assert not self.adapter.detect_rate_limit_error("login required")
        assert not self.adapter.detect_rate_limit_error("network timeout")

    def test_display_handle(self):
        assert self.adapter.get_display_handle(Artist(handle="artist")) == "@artist"

    def test_config_path(self):
        assert "xcom" in str(self.adapter.get_gallery_dl_config_path())

    def test_archive_db_path(self):
        assert "xcom" in str(self.adapter.get_archive_db_path())


# --- Pixiv ---


class TestPixivAdapter:
    def setup_method(self):
        self.adapter = PixivAdapter()

    def test_name(self):
        assert self.adapter.name == "pixiv"

    def test_match_valid(self):
        assert self.adapter.match_url("https://www.pixiv.net/users/12345")

    def test_match_invalid(self):
        assert not self.adapter.match_url("https://x.com/artist")
        assert not self.adapter.match_url("https://pixiv.net/artworks/123")

    def test_parse(self):
        handle, url = self.adapter.parse_url("https://www.pixiv.net/users/12345")
        assert handle == "12345"
        assert url == "https://www.pixiv.net/users/12345"

    def test_parse_rejects_invalid(self):
        with pytest.raises(ValueError):
            self.adapter.parse_url("https://pixiv.net/artworks/12345")

    def test_detect_auth_error(self):
        assert self.adapter.detect_auth_error("token expired")
        assert self.adapter.detect_auth_error("refresh token invalid")
        assert not self.adapter.detect_auth_error("network timeout")

    def test_detect_rate_limit_error(self):
        assert self.adapter.detect_rate_limit_error("429 Too Many Requests")
        assert self.adapter.detect_rate_limit_error("rate limit exceeded")
        assert not self.adapter.detect_rate_limit_error("token expired")
        assert not self.adapter.detect_rate_limit_error("network timeout")

    def test_display_handle(self):
        assert self.adapter.get_display_handle(Artist(handle="12345")) == "#12345"

    def test_config_path(self):
        assert "pixiv" in str(self.adapter.get_gallery_dl_config_path())


# --- DeviantArt ---


class TestDeviantArtAdapter:
    def setup_method(self):
        self.adapter = DeviantArtAdapter()

    def test_name(self):
        assert self.adapter.name == "deviantart"

    def test_match_valid(self):
        assert self.adapter.match_url("https://www.deviantart.com/username")

    def test_match_invalid(self):
        assert not self.adapter.match_url("https://x.com/artist")

    def test_parse(self):
        handle, url = self.adapter.parse_url("https://www.deviantart.com/username")
        assert handle == "username"
        assert url == "https://www.deviantart.com/username"

    def test_parse_rejects_invalid(self):
        with pytest.raises(ValueError):
            self.adapter.parse_url("https://deviantart.com/")

    def test_detect_auth_error(self):
        assert self.adapter.detect_auth_error("401 Unauthorized")
        assert self.adapter.detect_auth_error("Forbidden access")
        assert not self.adapter.detect_auth_error("network timeout")

    def test_detect_rate_limit_error(self):
        assert self.adapter.detect_rate_limit_error("429 Too Many Requests")
        assert self.adapter.detect_rate_limit_error("rate limit exceeded")
        assert not self.adapter.detect_rate_limit_error("Forbidden access")
        assert not self.adapter.detect_rate_limit_error("network timeout")

    def test_display_handle(self):
        assert self.adapter.get_display_handle(Artist(handle="username")) == "username"


# --- Registry ---


class TestSiteRegistry:
    def test_get_known_site(self, registry):
        adapter = registry.get("x.com")
        assert adapter.name == "x.com"

    def test_get_unknown_site(self, registry):
        with pytest.raises(ValueError, match="Unknown site"):
            registry.get("notasite")

    def test_match_url_x_com(self, registry):
        adapter = registry.match_url("https://x.com/artist")
        assert adapter.name == "x.com"

    def test_match_url_pixiv(self, registry):
        adapter = registry.match_url("https://www.pixiv.net/users/12345")
        assert adapter.name == "pixiv"

    def test_match_url_deviantart(self, registry):
        adapter = registry.match_url("https://www.deviantart.com/username")
        assert adapter.name == "deviantart"

    def test_match_url_unknown(self, registry):
        assert registry.match_url("https://instagram.com/artist") is None

    def test_all_adapters(self, registry):
        names = {a.name for a in registry.all_adapters()}
        assert names == {"x.com", "pixiv", "deviantart"}

    def test_per_site_auth_state(self, registry, db_conn):
        xcom = registry.get("x.com")
        pixiv = registry.get("pixiv")

        # X.com invalid, Pixiv valid
        xcom.mark_auth_invalid()
        assert not xcom.is_auth_valid()
        assert pixiv.is_auth_valid()

        # Pixiv invalid independently
        pixiv.mark_auth_invalid()
        assert not pixiv.is_auth_valid()

        # X.com still invalid
        assert not xcom.is_auth_valid()

        # Restore
        xcom.mark_auth_valid()
        pixiv.mark_auth_valid()
        assert xcom.is_auth_valid()
        assert pixiv.is_auth_valid()
