from __future__ import annotations

from src.sections.logs import _format_export


def _entry(level, ts, source, message, job_id=None, artist_id=None):
    return {
        "level": level,
        "timestamp": ts,
        "source": source,
        "message": message,
        "job_id": job_id,
        "artist_id": artist_id,
    }


class TestFormatExport:
    def test_header_records_filters_and_count(self):
        out = _format_export([_entry("INFO", "t", "scheduler", "hi")], "ERROR", None, 50)
        header = out.splitlines()[:5]
        assert header[0] == "# Inkwell log export"
        assert "level=ERROR" in header[2]
        assert "source=All" in header[2]
        assert "limit=50" in header[2]
        assert header[3] == "# Entries: 1 (newest first)"

    def test_one_line_per_entry_newest_first_with_meta(self):
        entries = [
            _entry("ERROR", "2026-08-11 14:25", "repair", "boom", job_id=12, artist_id=3),
            _entry("INFO", "2026-08-11 14:20", "scheduler", "ok"),
        ]
        out = _format_export(entries, None, None, 100)
        # Header is 5 lines (indices 0-4, incl. trailing blank); entries follow.
        body = out.splitlines()[5:]
        assert body == [
            "[ERROR] 2026-08-11 14:25 [repair] boom (job=12) (artist=3)",
            "[INFO] 2026-08-11 14:20 [scheduler] ok",
        ]
        assert out.endswith("\n")

    def test_empty_log_set_still_has_header(self):
        out = _format_export([], None, None, 100)
        assert "# Entries: 0 (newest first)" in out
        # No body entries beyond the header block.
        assert not any(line.startswith("[") for line in out.splitlines())
