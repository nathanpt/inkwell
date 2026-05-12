from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src.nas_monitor import _write_check, check_nas_available, check_nas_with_retry


class TestWriteCheck:
    def test_available_dir(self, tmp_path):
        assert _write_check(tmp_path) is True

    def test_nonexistent_dir(self, tmp_path):
        assert _write_check(tmp_path / "nope") is True  # mkdir creates it

    def test_readonly_dir(self, tmp_path):
        readonly = tmp_path / "readonly"
        readonly.mkdir()
        readonly.chmod(0o444)
        try:
            assert _write_check(readonly) is False
        finally:
            readonly.chmod(0o755)


class TestCheckNasWithRetry:
    def test_available_first_try(self, tmp_path):
        assert check_nas_with_retry(tmp_path) is True

    @patch("src.nas_monitor.NAS_CHECK_RETRIES", 2)
    @patch("src.nas_monitor.NAS_CHECK_BACKOFF", [0, 0])
    @patch("src.nas_monitor._write_check", return_value=False)
    def test_unavailable_after_retries(self, mock_check, tmp_path):
        assert check_nas_with_retry(tmp_path) is False
        assert mock_check.call_count == 2

    @patch("src.nas_monitor.NAS_CHECK_RETRIES", 3)
    @patch("src.nas_monitor.NAS_CHECK_BACKOFF", [0, 0, 0])
    def test_succeeds_on_second_attempt(self, tmp_path):
        with patch("src.nas_monitor._write_check", side_effect=[False, True]):
            assert check_nas_with_retry(tmp_path) is True
