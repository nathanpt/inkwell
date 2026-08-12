"""Tests for scripts/analyze_rate_limits.py (loaded by path; stdlib-only module)."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "analyze_rate_limits.py"

_spec = importlib.util.spec_from_file_location("analyze_rate_limits", SCRIPT)
analyze_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(analyze_mod)


@pytest.fixture
def conn(tmp_path):
    """Minimal DB mirroring the logs/state/artists tables used by the analyzer."""
    db_path = tmp_path / "an.db"
    c = sqlite3.connect(db_path)
    c.execute(
        """CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            level TEXT NOT NULL,
            source TEXT NOT NULL,
            message TEXT NOT NULL,
            job_id INTEGER,
            artist_id INTEGER
        )"""
    )
    c.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    c.execute(
        """CREATE TABLE IF NOT EXISTS artists (
            id INTEGER PRIMARY KEY,
            handle TEXT,
            site TEXT,
            source_url TEXT,
            added_at TEXT,
            last_scan_at TEXT,
            is_active INTEGER
        )"""
    )
    c.commit()
    yield c
    c.close()


def _insert_log(c, message, source="repair", level="WARNING", artist_id=None):
    c.execute(
        "INSERT INTO logs (level, source, message, artist_id) VALUES (?, ?, ?, ?)",
        (level, source, message, artist_id),
    )
    c.commit()


class TestCounts429Hits:
    def test_counts_429_hits_per_site_day(self, conn):
        conn.execute(
            "INSERT INTO artists (id, handle, site, source_url, is_active) "
            "VALUES (1, 'alice', 'x.com', 'https://x.com/alice', 1)"
        )
        conn.commit()
        msg = (
            "x.com: rate-limited on alice chunk 1/3 (33.2s); "
            "backoff 60s (attempt 1/4)"
        )
        _insert_log(conn, msg)
        _insert_log(conn, msg)
        _insert_log(
            conn,
            "Rate limited for alice, skipping retries",
            source="downloader",
            artist_id=1,
        )

        report = analyze_mod.analyze(conn, days=30)

        x_rows = [d for d in report["days"] if d["site"] == "x.com"]
        assert len(x_rows) == 1
        assert x_rows[0]["hits429"] == 3


class TestParsesRepairRunSummary:
    def test_parses_observed_run_line(self, conn):
        _insert_log(
            conn,
            "Repair done in 1138s: 41 recovered/updated (2.2 files/min), "
            "4 deleted, 0 ambiguous, 0 unsupported, sites aborted: pixiv, x.com",
            level="INFO",
        )

        report = analyze_mod.analyze(conn, days=30)
        runs = report["repair_runs"]
        assert len(runs) == 1
        run = runs[0]
        assert run["elapsed"] == 1138
        assert run["resolved"] == 41
        assert run["deleted"] == 4
        assert run["rate"] == 2.2
        assert run["aborted"] == ["pixiv", "x.com"]


class TestReadsLimiterState:
    def test_exposes_multiplier_and_hit_count(self, conn):
        conn.execute(
            "INSERT INTO state (key, value) VALUES (?, ?)",
            (
                "rate_limit:x.com",
                json.dumps(
                    {"hit_count": 12, "cooldown_multiplier": 7.5, "last_hit_ts": 0.0}
                ),
            ),
        )
        conn.commit()

        report = analyze_mod.analyze(conn, days=30)
        x = [s for s in report["limiter_state"] if s["site"] == "x.com"]
        assert len(x) == 1
        assert x[0]["hit_count"] == 12
        assert x[0]["multiplier"] == 7.5

        rendered = analyze_mod.render(report)
        assert "7.50" in rendered


class TestEmptyDbRenders:
    def test_no_rows_no_crash(self, conn):
        report = analyze_mod.analyze(conn, days=30)
        rendered = analyze_mod.render(report)
        assert "Limiter state" in rendered
        assert "Per-day activity" in rendered
        assert "Repair runs" in rendered
        assert report["days"] == []
        assert report["repair_runs"] == []
        assert report["limiter_state"] == []
