from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from src import db
from src.config_loader import Config, load_config

logger = logging.getLogger(__name__)

DATA_DIR = Path("/app/data")
CONFIG_DIR = Path("/app/config")
DEFAULTS_DIR = Path("/app/defaults")


_bootstrap_done = False


def bootstrap(config_path: Path | None = None) -> tuple[sqlite3.Connection, Config]:
    import sqlite3

    global _bootstrap_done

    config_file = config_path or (CONFIG_DIR / "config.toml")

    if config_file.is_dir() or not config_file.exists():
        # Docker created a directory because the bind-mounted file didn't exist,
        # or the file is simply missing. Fall back to the defaults baked into
        # the image. We can't replace the bind mount, so read from defaults.
        logger.info("Config %s not available, using image defaults", config_file)
        config_file = DEFAULTS_DIR / "config.toml"

    config = load_config(config_file)

    # 1. Verify /app/data/ is local filesystem (not NFS bind mount)
    _verify_local_storage(DATA_DIR)

    # 2. Init DB schema
    conn = db.connect()
    db.init_schema(conn)

    # 3. Seed state defaults
    db.seed_state(conn)

    # 4. Clean orphaned jobs — only on first bootstrap in this process
    if not _bootstrap_done:
        orphans = db.clean_orphaned_jobs(conn)
        if orphans:
            logger.info("Cleaned %d orphaned job(s)", orphans)
        _bootstrap_done = True

    # 5. Prune old logs
    pruned = db.prune_old_logs(conn, config.retention.log_days)
    if pruned:
        logger.info("Pruned %d old log entries", pruned)

    # 6. Verify config files exist
    _verify_config_files()

    logger.info("Bootstrap complete")
    return conn, config


def _verify_local_storage(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    # Check that the data directory is on a local filesystem by testing
    # file locking — NFS doesn't reliably support POSIX fcntl locks
    try:
        st = os.statvfs(str(data_dir))
        # NFS mount points typically have fstype "nfs" or "nfs4"
        # We do a simpler check: try to create and lock a test file
        test_file = data_dir / ".inkwell_lock_test"
        test_file.write_text("lock-test")
        import fcntl

        with open(test_file, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        test_file.unlink(missing_ok=True)
    except OSError as e:
        logger.warning(
            "Could not verify local storage for %s: %s. Proceeding anyway.",
            data_dir,
            e,
        )


def _verify_config_files() -> None:
    configs = list(CONFIG_DIR.glob("gallery-dl.*.conf"))
    if not configs:
        configs = list(DEFAULTS_DIR.glob("gallery-dl.*.conf"))
    if not configs:
        raise FileNotFoundError(
            f"No gallery-dl site configs found in {CONFIG_DIR} or {DEFAULTS_DIR}."
        )
