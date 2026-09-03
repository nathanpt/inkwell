from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from src import db
from src.config_loader import Config, NASConfig
from src.models import Artist
from src.repair import (
    RepairResult,
    _downloaded_paths,
    extract_post_id,
    repair_missing,
)
from src.sites.base import SiteRegistry
from src.sites.xcom import XComAdapter


@pytest.fixture
def setup(tmp_path):
    """Configure a scratch DB + NAS tree + x.com registry."""
    db.configure(tmp_path / "test.db")
    conn = db.connect(tmp_path / "test.db")
    db.init_schema(conn)
    conn.close()
    db.seed_state()

    nas = tmp_path / "nas"
    nas.mkdir()

    config = Config(nas=NASConfig(mount_path=str(nas)))
    registry = SiteRegistry()
    registry.register(XComAdapter())
    return config, registry, nas


def _make_artist(handle="alice", site="x.com"):
    artist = Artist(handle=handle, site=site, source_url=f"https://x.com/{handle}")
    artist.id = db.insert_artist(artist)
    return artist


def _insert_file(artist_id, filename, year, size=0):
    db.insert_file_records(None, artist_id, [(filename, year, size)])


class TestExtractPostId:
    def test_numeric_prefix(self):
        assert extract_post_id("2024/12345_photo.jpg") == "12345"

    def test_non_numeric_returns_none(self):
        assert extract_post_id("2024/hello.jpg") is None


class TestRepairHappyPath:
    def test_exact_match_recovers_row(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/12345_photo.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def writer(urls):
            ydir = nas / "alice" / "2024"
            ydir.mkdir(parents=True, exist_ok=True)
            for url in urls:
                pid = url.rsplit("/", 1)[-1]
                (ydir / f"{pid}_photo.jpg").write_bytes(b"data")

        def fake_batch(urls, config, adapter):
            writer(urls)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_recovered == 1
        assert result.rows_deleted == 0
        # Row still present
        rows = db.get_all_file_rows()
        assert len(rows) == 1


class TestRepairRenamedMedia:
    def test_renamed_basename_updates_row(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/12345_old.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def writer(urls):
            ydir = nas / "alice" / "2024"
            ydir.mkdir(parents=True, exist_ok=True)
            for url in urls:
                pid = url.rsplit("/", 1)[-1]
                (ydir / f"{pid}_new.jpg").write_bytes(b"renamed")

        def fake_batch(urls, config, adapter):
            writer(urls)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_updated == 1
        row = db.get_all_file_rows()[0]
        assert row["filename"] == "2024/12345_new.jpg"
        assert row["size_bytes"] == len(b"renamed")


class TestRepairUnrecoverable:
    def test_unrecovered_row_deleted_when_sibling_recovers(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/111_a.jpg", "2024")  # will recover
        _insert_file(artist.id, "2024/222_b.jpg", "2024")  # unrecoverable

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def writer(urls):
            ydir = nas / "alice" / "2024"
            ydir.mkdir(parents=True, exist_ok=True)
            for url in urls:
                pid = url.rsplit("/", 1)[-1]
                if pid == "111":
                    (ydir / "111_a.jpg").write_bytes(b"x")

        def fake_batch(urls, config, adapter):
            writer(urls)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_recovered == 1
        assert result.rows_deleted == 1
        rows = db.get_all_file_rows()
        assert len(rows) == 1
        assert rows[0]["filename"] == "2024/111_a.jpg"


class TestRepairSafeguard:
    def test_no_recovery_keeps_rows(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/999_missing.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def fake_batch(urls, config, adapter):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_recovered == 0
        assert result.rows_deleted == 0
        assert result.artists_no_recovery == 1
        # Row kept by safeguard
        assert len(db.get_all_file_rows()) == 1


class TestRepairUnsupported:
    def test_non_numeric_filename_unsupported(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/nonnumeric.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        called = {"n": 0}

        def fake_batch(urls, config, adapter):
            called["n"] += 1
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_unsupported == 1
        assert called["n"] == 0  # _run_batch never invoked
        assert len(db.get_all_file_rows()) == 1


class TestRepairRateLimit:
    def test_rate_limit_records_hit_and_aborts(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/12345_photo.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def fake_batch(urls, config, adapter):
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="HTTP 429 Too Many Requests"
            )

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.aborted_reason is None  # per-site now, not a hard stop
        assert result.sites_aborted == ["x.com"]
        # record_hit raised the x.com multiplier above 1.0
        from src.rate_limiter import get_cooldown_multiplier
        assert get_cooldown_multiplier("x.com") > 1.0


class TestRepairPerSiteContinue:
    def test_other_site_continues_after_one_aborts(self, setup, monkeypatch):
        config, registry, nas = setup
        # Register pixiv alongside x.com
        from src.sites.pixiv import PixivAdapter
        registry.register(PixivAdapter())

        alice = _make_artist(handle="alice", site="x.com")
        _insert_file(alice.id, "2024/111_a.jpg", "2024")
        bob = _make_artist(handle="bob", site="pixiv")
        _insert_file(bob.id, "2024/555_art.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def fake_batch(urls, config, adapter):
            if adapter.name == "x.com":
                return subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr="HTTP 429 Too Many Requests"
                )
            # pixiv: write the expected file
            ydir = nas / "bob" / "2024"
            ydir.mkdir(parents=True, exist_ok=True)
            for url in urls:
                pid = url.rsplit("/", 1)[-1]
                (ydir / f"{pid}_art.jpg").write_bytes(b"x")
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        # x.com aborted; pixiv kept going and recovered bob's row
        assert result.sites_aborted == ["x.com"]
        assert result.rows_recovered == 1
        filenames = {r["filename"] for r in db.get_all_file_rows()}
        assert "2024/555_art.jpg" in filenames   # pixiv recovered
        assert "2024/111_a.jpg" in filenames      # x.com kept by safeguard


class TestRepairGuard:
    def test_already_running_aborts(self, setup):
        config, registry, nas = setup
        db.set_state("repair:running", "1")

        with patch("src.repair._run_batch") as mock_batch:
            result = repair_missing(config, registry)

        assert result.aborted_reason == "already running"
        assert mock_batch.call_count == 0


class TestRepairDiagnosticsRename:
    def test_downloaded_paths_parses_stdout(self, tmp_path):
        nas = tmp_path / "nas"
        inside1 = nas / "alice" / "2024" / "111_a.jpg"
        inside2 = nas / "bob" / "2023" / "222_b.png"
        outside = tmp_path / "other" / "x.jpg"
        stdout = "\n".join(
            ["some log noise", str(inside1), f"# {inside2}", str(outside)]
        )
        result = _downloaded_paths(stdout, nas)
        assert set(result) == {inside1, inside2}

    def test_renamed_dir_relocated_and_recovers(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/111_a.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def fake_batch(urls, config, adapter):
            ren_dir = nas / "Renamed" / "2024"
            ren_dir.mkdir(parents=True, exist_ok=True)
            lines = []
            for url in urls:
                pid = url.rsplit("/", 1)[-1]
                p = ren_dir / f"{pid}_a.jpg"
                p.write_bytes(b"data")
                lines.append(str(p))
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="\n".join(lines), stderr=""
            )

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_recovered == 1
        assert (nas / "alice" / "2024" / "111_a.jpg").exists()
        assert not (nas / "Renamed").exists()

    def test_relocate_collision_keeps_existing(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/111_a.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def fake_batch(urls, config, adapter):
            # Canonical file already present (e.g. recovered earlier in this
            # run) — it must win on collision; the renamed-dir duplicate stays.
            canon = nas / "alice" / "2024" / "111_a.jpg"
            canon.parent.mkdir(parents=True, exist_ok=True)
            canon.write_bytes(b"orig")
            ren_dir = nas / "Renamed" / "2024"
            ren_dir.mkdir(parents=True, exist_ok=True)
            dup = ren_dir / "111_a.jpg"
            dup.write_bytes(b"dup")
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout=str(dup), stderr=""
            )

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_recovered == 1
        assert (nas / "alice" / "2024" / "111_a.jpg").read_bytes() == b"orig"
        # Renamed-dir duplicate left in place (skip-on-collision).
        assert (nas / "Renamed" / "2024" / "111_a.jpg").exists()

    def test_unclassified_failure_logs_stderr_tail(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/111_a.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def fake_batch(urls, config, adapter):
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Unexpected download error"
            )

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_recovered == 0
        assert len(db.get_all_file_rows()) == 1  # safeguard keeps the row
        msgs = [
            lg["message"]
            for lg in db.get_logs(source="repair")
            if lg["level"] == "WARNING"
        ]
        assert any("stderr tail:" in m for m in msgs)

    def test_zero_recovery_after_success_logs_rename_hint(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/111_a.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def fake_batch(urls, config, adapter):
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_recovered == 0
        assert len(db.get_all_file_rows()) == 1  # safeguard keeps the row
        msgs = [
            lg["message"]
            for lg in db.get_logs(source="repair")
            if lg["level"] == "WARNING"
        ]
        assert any("Possible author rename" in m and "find" in m for m in msgs)


class TestRepairRateLimitWait:
    def test_paused_site_waited_for_not_skipped(self, setup, monkeypatch):
        import time as _time
        from src.rate_limiter import RateLimitConfig, record_hit
        config, registry, nas = setup
        config.rate_limit = RateLimitConfig(
            multiplier_step=2.0, max_multiplier=16.0,
            pause_threshold=6.0, pause_seconds=60,
        )
        artist = _make_artist()
        _insert_file(artist.id, "2024/111_a.jpg", "2024")

        # Shared fake clock so the rate window elapses during repair's wait.
        clock = [1000.0]
        monkeypatch.setattr(_time, "time", lambda: clock[0])
        for _ in range(3):
            record_hit("x.com", config.rate_limit)  # paused, last_hit_ts=1000

        # Repair's sleep advances the clock so _wait_for_unpause completes.
        monkeypatch.setattr(
            "src.repair.time.sleep", lambda s: clock.__setitem__(0, clock[0] + s)
        )

        def fake_batch(urls, config, adapter):
            ydir = nas / "alice" / "2024"
            ydir.mkdir(parents=True, exist_ok=True)
            for url in urls:
                pid = url.rsplit("/", 1)[-1]
                (ydir / f"{pid}_a.jpg").write_bytes(b"data")
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_recovered == 1
        msgs = [lg["message"] for lg in db.get_logs(source="repair")]
        assert any("waiting up to" in m for m in msgs)
        assert not any("still rate-limit paused after waiting" in m for m in msgs)

    def test_chunk_retry_waits_out_window(self, setup, monkeypatch):
        import time as _time
        from src.rate_limiter import RateLimitConfig
        config, registry, nas = setup
        config.rate_limit = RateLimitConfig(
            multiplier_step=8.0, max_multiplier=16.0,
            pause_threshold=6.0, pause_seconds=120,
        )
        artist = _make_artist()
        _insert_file(artist.id, "2024/111_a.jpg", "2024")

        # Shared fake clock; repair's sleep advances it so the rate window
        # elapses during the chunk-retry wait (no pre-seeded hits, so the
        # artist-level check does not pause).
        clock = [1000.0]
        monkeypatch.setattr(_time, "time", lambda: clock[0])
        monkeypatch.setattr(
            "src.repair.time.sleep", lambda s: clock.__setitem__(0, clock[0] + s)
        )

        calls = {"n": 0}

        def fake_batch(urls, config, adapter):
            calls["n"] += 1
            if calls["n"] == 1:
                return subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr="HTTP 429 Too Many Requests"
                )
            ydir = nas / "alice" / "2024"
            ydir.mkdir(parents=True, exist_ok=True)
            for url in urls:
                pid = url.rsplit("/", 1)[-1]
                (ydir / f"{pid}_a.jpg").write_bytes(b"data")
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_recovered == 1
        assert result.sites_aborted == []
        msgs = [lg["message"] for lg in db.get_logs(source="repair")]
        assert sum("waiting up to" in m for m in msgs) == 1
        assert sum("backoff 60s (attempt 1/2)" in m for m in msgs) == 1

    def test_between_chunk_stress_wait(self, setup, monkeypatch):
        import time as _time
        from src.rate_limiter import RateLimitConfig, record_hit
        from src.repair import CHUNK_SIZE
        config, registry, nas = setup
        config.rate_limit = RateLimitConfig(
            multiplier_step=8.0, max_multiplier=16.0,
            pause_threshold=6.0, pause_seconds=120,
        )
        artist = _make_artist()
        # Two chunks' worth of missing rows.
        for i in range(CHUNK_SIZE * 2):
            _insert_file(artist.id, f"2024/{i}_a.jpg", "2024")

        # Seed x.com into sustained stress (multiplier 8.0) but with an ANCIENT
        # last hit, so the artist-entry is_site_paused check (multiplier AND a
        # recent hit) does not fire, while the between-chunk stress gate
        # (multiplier alone) does.
        clock = [100000.0]
        monkeypatch.setattr(_time, "time", lambda: clock[0])
        clock[0] = 1000.0
        record_hit("x.com", config.rate_limit)  # multiplier 8.0, last_hit=1000
        clock[0] = 100000.0                      # last_hit now ancient -> not paused
        monkeypatch.setattr(
            "src.repair.time.sleep", lambda s: clock.__setitem__(0, clock[0] + s)
        )

        def fake_batch(urls, config, adapter):
            ydir = nas / "alice" / "2024"
            ydir.mkdir(parents=True, exist_ok=True)
            for url in urls:
                pid = url.rsplit("/", 1)[-1]
                (ydir / f"{pid}_a.jpg").write_bytes(b"data")
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_recovered == CHUNK_SIZE * 2
        assert result.sites_aborted == []
        msgs = [lg["message"] for lg in db.get_logs(source="repair")]
        # Exactly one between-chunk stress hold (between chunk 1 and 2).
        assert sum("holding next chunk" in m for m in msgs) == 1
        # The short cooldown path is skipped while stressed.
        assert not any(m.startswith("cooldown ") for m in msgs)


class TestRepairAuthSkip:
    def test_invalid_auth_site_skipped_without_fetch(self, setup, monkeypatch):
        config, registry, nas = setup
        from src.sites.pixiv import PixivAdapter
        registry.register(PixivAdapter())

        artist = _make_artist(handle="bob", site="pixiv")
        _insert_file(artist.id, "2024/555_art.jpg", "2024")
        db.set_state("auth_valid:pixiv", "0")

        with patch("src.repair._run_batch") as mock_batch:
            result = repair_missing(config, registry)

        assert mock_batch.call_count == 0
        assert "pixiv" in result.sites_aborted
        assert len(db.get_all_file_rows()) == 1
        warns = [
            lg["message"]
            for lg in db.get_logs(source="repair")
            if lg["level"] == "WARNING"
        ]
        assert any("auth flagged invalid" in m for m in warns)

    def test_valid_site_still_runs_when_other_invalid(self, setup, monkeypatch):
        config, registry, nas = setup
        from src.sites.pixiv import PixivAdapter
        registry.register(PixivAdapter())

        bob = _make_artist(handle="bob", site="pixiv")
        _insert_file(bob.id, "2024/555_art.jpg", "2024")
        alice = _make_artist(handle="alice", site="x.com")
        _insert_file(alice.id, "2024/111_a.jpg", "2024")
        db.set_state("auth_valid:pixiv", "0")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def fake_batch(urls, config, adapter):
            if adapter.name == "x.com":
                ydir = nas / "alice" / "2024"
                ydir.mkdir(parents=True, exist_ok=True)
                for url in urls:
                    pid = url.rsplit("/", 1)[-1]
                    (ydir / f"{pid}_a.jpg").write_bytes(b"x")
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            raise AssertionError("pixiv must not be fetched when auth is invalid")

        with patch("src.repair._run_batch", side_effect=fake_batch) as mock_batch:
            result = repair_missing(config, registry)

        assert result.sites_aborted == ["pixiv"]
        assert result.rows_recovered == 1
        sites_called = [call.args[2].name for call in mock_batch.call_args_list]
        assert sites_called == ["x.com"]
        filenames = {r["filename"] for r in db.get_all_file_rows()}
        assert "2024/111_a.jpg" in filenames   # x.com recovered
        assert "2024/555_art.jpg" in filenames  # pixiv row left untouched


class TestRemovedUpstream:
    """Rows whose own post URL is positively confirmed dead are purged even
    when the artist yields zero recoveries (the all-deleted-artist case)."""

    def test_not_found_pids_parsing(self):
        from src.repair import _not_found_pids

        stderr = "\n".join([
            "[error][twitter] https://x.com/alice/status/111: 404 Not Found ('Not Found')",
            "[error][twitter] https://x.com/alice/status/222: http 404",
            "[error][pixiv] https://www.pixiv.net/artworks/333: The illustration has been deleted",
            "[warning][twitter] https://x.com/alice/status/444: 429 Too Many Requests",
            "[error][twitter] https://x.com/alice/status/555: 401 Unauthorized",
        ])
        confirmed = _not_found_pids(stderr, ["111", "222", "333", "444", "555", "666"])
        assert confirmed == {"111", "222", "333"}

    def test_pid_word_boundary_no_false_ride_along(self):
        from src.repair import _not_found_pids

        stderr = "[error][twitter] https://x.com/alice/status/1234: 404 Not Found"
        assert _not_found_pids(stderr, ["123", "1234"]) == {"1234"}

    def test_all_deleted_artist_rows_purged(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/111_a.jpg", "2024")
        _insert_file(artist.id, "2024/222_b.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def fake_batch(urls, config, adapter):
            stderr = "\n".join(
                f"[error][twitter] https://x.com/alice/status/{url.rsplit('/', 1)[-1]}: "
                f"404 Not Found ('Not Found')"
                for url in urls
            )
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=stderr)

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_recovered == 0
        assert result.rows_removed_upstream == 2
        assert result.rows_deleted == 2
        assert result.artists_no_recovery == 0
        assert db.get_all_file_rows() == []
        msgs = [lg["message"] for lg in db.get_logs(source="repair")]
        assert any("confirmed removed upstream" in m for m in msgs)
        # All rows accounted for as removed -> no misleading rename hint
        assert not any("Possible author rename" in m for m in msgs)

    def test_confirmed_dead_mixed_with_recovered_and_inferred(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/111_a.jpg", "2024")  # will recover
        _insert_file(artist.id, "2024/222_b.jpg", "2024")  # 404-confirmed
        _insert_file(artist.id, "2024/333_c.jpg", "2024")  # yields nothing, no confirmation

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def fake_batch(urls, config, adapter):
            ydir = nas / "alice" / "2024"
            ydir.mkdir(parents=True, exist_ok=True)
            stderr = ""
            for url in urls:
                pid = url.rsplit("/", 1)[-1]
                if pid == "222":
                    stderr += (
                        f"[error][twitter] https://x.com/alice/status/{pid}: "
                        f"404 Not Found ('Not Found')\n"
                    )
                elif pid == "111":
                    (ydir / f"{pid}_a.jpg").write_bytes(b"data")
                # 333: exists upstream in the scenario, but yields nothing here
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=stderr)

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_recovered == 1
        assert result.rows_removed_upstream == 1
        # 222 confirmed + 333 inferred (sibling 111 recovered proves reachability)
        assert result.rows_deleted == 2
        filenames = {r["filename"] for r in db.get_all_file_rows()}
        assert filenames == {"2024/111_a.jpg"}

    def test_unconfirmed_failure_keeps_safeguard(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/111_a.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def fake_batch(urls, config, adapter):
            # 5xx mentioning the URL: an error, but NOT positive removal evidence
            stderr = "\n".join(
                f"[error][twitter] https://x.com/alice/status/{url.rsplit('/', 1)[-1]}: "
                f"503 Service Unavailable"
                for url in urls
            )
            return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_removed_upstream == 0
        assert result.rows_deleted == 0
        assert len(db.get_all_file_rows()) == 1  # safeguard keeps the row


class TestTargetedRepair:
    """``artist_id`` scopes the run to one artist; mutating runs refresh the
    stored integrity summary that feeds the Artists page Missing %."""

    def test_targeted_scopes_to_artist(self, setup, monkeypatch):
        config, registry, nas = setup
        alice = _make_artist(handle="alice")
        _insert_file(alice.id, "2024/111_a.jpg", "2024")
        bob = _make_artist(handle="bob")
        _insert_file(bob.id, "2024/222_b.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        seen_urls = []

        def fake_batch(urls, config, adapter):
            seen_urls.extend(urls)
            for url in urls:
                handle = url.split("/")[3]
                pid = url.rsplit("/", 1)[-1]
                ydir = nas / handle / "2024"
                ydir.mkdir(parents=True, exist_ok=True)
                (ydir / f"{pid}_a.jpg").write_bytes(b"data")
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry, artist_id=alice.id)

        assert result.rows_recovered == 1
        assert seen_urls and all("/alice/" in u for u in seen_urls)
        filenames = {r["filename"] for r in db.get_all_file_rows()}
        assert filenames == {"2024/111_a.jpg", "2024/222_b.jpg"}  # bob untouched

    def test_targeted_no_missing_returns_early(self, setup, monkeypatch):
        config, registry, nas = setup
        alice = _make_artist(handle="alice")
        _insert_file(alice.id, "2024/111_a.jpg", "2024")
        ydir = nas / "alice" / "2024"
        ydir.mkdir(parents=True)
        (ydir / "111_a.jpg").write_bytes(b"data")
        bob = _make_artist(handle="bob")
        _insert_file(bob.id, "2024/222_b.jpg", "2024")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        with patch("src.repair._run_batch") as mock_batch:
            result = repair_missing(config, registry, artist_id=alice.id)

        mock_batch.assert_not_called()
        assert result.missing_before == 0
        filenames = {r["filename"] for r in db.get_all_file_rows()}
        assert filenames == {"2024/111_a.jpg", "2024/222_b.jpg"}

    def test_summary_refreshed_after_purge(self, setup, monkeypatch):
        config, registry, nas = setup
        artist = _make_artist()
        _insert_file(artist.id, "2024/111_a.jpg", "2024")  # present on disk
        _insert_file(artist.id, "2024/222_b.jpg", "2024")  # missing -> 404 upstream
        ydir = nas / "alice" / "2024"
        ydir.mkdir(parents=True)
        (ydir / "111_a.jpg").write_bytes(b"data")

        monkeypatch.setattr("src.repair.time.sleep", lambda *_: None)

        def fake_batch(urls, config, adapter):
            stderr = "\n".join(
                f"[error][twitter] https://x.com/alice/status/{url.rsplit('/', 1)[-1]}: "
                f"404 Not Found ('Not Found')"
                for url in urls
            )
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=stderr)

        with patch("src.repair._run_batch", side_effect=fake_batch):
            result = repair_missing(config, registry)

        assert result.rows_deleted == 1  # purge happened -> refresh must have too
        summary = json.loads(db.get_state("integrity:last_check"))
        assert summary["total"] == 1
        assert summary["by_artist"] == {str(artist.id): 0}
