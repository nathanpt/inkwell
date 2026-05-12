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


def bootstrap(config_path: Path | None = None) -> tuple[sqlite3.Connection, Config]:
    import sqlite3

    config = load_config(config_path or (CONFIG_DIR / "config.toml"))

    # 1. Verify /app/data/ is local filesystem (not NFS bind mount)
    _verify_local_storage(DATA_DIR)

    # 2. Init DB schema
    conn = db.connect()
    db.init_schema(conn)

    # 3. Seed state defaults
    db.seed_state(conn)

    # 4. Clean orphaned jobs from ungraceful shutdowns
    orphans = db.clean_orphaned_jobs(conn)
    if orphans:
        logger.info("Cleaned %d orphaned job(s)", orphans)

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
    gallery_dl_conf = CONFIG_DIR / "gallery-dl.conf"
    if not gallery_dl_conf.exists():
        raise FileNotFoundError(
            f"gallery-dl.conf not found at {gallery_dl_conf}. "
            "Ensure it is bind-mounted into the container."
        )
