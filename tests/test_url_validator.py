from __future__ import annotations

import pytest

from src.url_validator import validate_url


class TestValidateUrl:
    def test_valid_x_com(self):
        handle, url, adapter = validate_url("https://x.com/artistname")
        assert handle == "artistname"
        assert url == "https://x.com/artistname"
        assert adapter.name == "x.com"

    def test_valid_twitter_com(self):
        handle, url, adapter = validate_url("https://twitter.com/artistname")
        assert handle == "artistname"
        assert url == "https://x.com/artistname"
        assert adapter.name == "x.com"

    def test_trailing_slash(self):
        handle, url, _ = validate_url("https://x.com/artistname/")
        assert handle == "artistname"

    def test_underscores_in_handle(self):
        handle, _, _ = validate_url("https://x.com/art_name_123")
        assert handle == "art_name_123"

    def test_rejects_invalid_domain(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_url("https://instagram.com/artist")

    def test_rejects_empty_handle(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_url("https://x.com/")

    def test_rejects_long_handle(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_url("https://x.com/" + "a" * 16)

    def test_rejects_special_chars(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_url("https://x.com/artist-name")

    def test_rejects_path(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            validate_url("https://x.com/artist/status/123")

    def test_strips_whitespace(self):
        handle, _, _ = validate_url("  https://x.com/artist  ")
        assert handle == "artist"

    def test_valid_pixiv(self):
        handle, url, adapter = validate_url("https://www.pixiv.net/users/12345")
        assert handle == "12345"
        assert url == "https://www.pixiv.net/users/12345"
        assert adapter.name == "pixiv"

    def test_valid_deviantart(self):
        handle, url, adapter = validate_url("https://www.deviantart.com/username")
        assert handle == "username"
        assert url == "https://www.deviantart.com/username"
        assert adapter.name == "deviantart"
