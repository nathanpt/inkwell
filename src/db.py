from __future__ import annotations

import sqlite3
from pathlib import Path

from src.models import Artist, Job

DEFAULT_DB_PATH = Path("/app/data/inkwell.db")

SCHEMA_VERSION = 2

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS artists (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    handle        TEXT NOT NULL,
    site          TEXT NOT NULL DEFAULT 'x.com',
    source_url    TEXT NOT NULL UNIQUE,
    added_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_scan_at  TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_id     INTEGER NOT NULL REFERENCES artists(id),
    status        TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    file_count    INTEGER DEFAULT 0,
    total_bytes   INTEGER DEFAULT 0,
    error_message TEXT,
    triggered_by  TEXT NOT NULL DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT NOT NULL DEFAULT (datetime('now')),
    level         TEXT NOT NULL,
    source        TEXT NOT NULL,
    message       TEXT NOT NULL,
    job_id        INTEGER REFERENCES jobs(id),
    artist_id     INTEGER REFERENCES artists(id)
);

CREATE TABLE IF NOT EXISTS state (
    key           TEXT PRIMARY KEY,
    value         TEXT NOT NULL
);
"""


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if current_version == 0:
        conn.executescript(SCHEMA_SQL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
        (key, value, value),
    )
    conn.commit()


def seed_state(conn: sqlite3.Connection) -> None:
    if get_state(conn, "auth_session_valid") is None:
        set_state(conn, "auth_session_valid", "1")
    if get_state(conn, "schema_version") is None:
        set_state(conn, "schema_version", str(SCHEMA_VERSION))


# --- Artist CRUD ---


def insert_artist(conn: sqlite3.Connection, artist: Artist) -> int:
    cur = conn.execute(
        "INSERT INTO artists (handle, site, source_url) VALUES (?, ?, ?)",
        (artist.handle, artist.site, artist.source_url),
    )
    conn.commit()
    return cur.lastrowid


def get_active_artists(conn: sqlite3.Connection) -> list[Artist]:
    rows = conn.execute(
        "SELECT * FROM artists WHERE is_active = 1 ORDER BY added_at"
    ).fetchall()
    return [_row_to_artist(r) for r in rows]


def get_artist_by_url(conn: sqlite3.Connection, source_url: str) -> Artist | None:
    row = conn.execute(
        "SELECT * FROM artists WHERE source_url = ?", (source_url,)
    ).fetchone()
    return _row_to_artist(row) if row else None


def deactivate_artist(conn: sqlite3.Connection, artist_id: int) -> None:
    conn.execute("UPDATE artists SET is_active = 0 WHERE id = ?", (artist_id,))
    conn.commit()


def update_last_scan(conn: sqlite3.Connection, artist_id: int) -> None:
    conn.execute(
        "UPDATE artists SET last_scan_at = datetime('now') WHERE id = ?",
        (artist_id,),
    )
    conn.commit()


def get_all_artists(conn: sqlite3.Connection) -> list[Artist]:
    rows = conn.execute("SELECT * FROM artists ORDER BY added_at").fetchall()
    return [_row_to_artist(r) for r in rows]


# --- Job CRUD ---


def insert_job(conn: sqlite3.Connection, job: Job) -> int:
    cur = conn.execute(
        "INSERT INTO jobs (artist_id, status, started_at, triggered_by) VALUES (?, ?, datetime('now'), ?)",
        (job.artist_id, job.status, job.triggered_by),
    )
    conn.commit()
    return cur.lastrowid


def update_job_completion(
    conn: sqlite3.Connection,
    job_id: int,
    status: str,
    file_count: int,
    total_bytes: int,
    error_message: str | None = None,
) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, finished_at = datetime('now'), file_count = ?, total_bytes = ?, error_message = ? WHERE id = ?",
        (status, file_count, total_bytes, error_message, job_id),
    )
    conn.commit()


def get_running_job_for_artist(conn: sqlite3.Connection, artist_id: int) -> Job | None:
    row = conn.execute(
        "SELECT * FROM jobs WHERE artist_id = ? AND status = 'running'",
        (artist_id,),
    ).fetchone()
    return _row_to_job(row) if row else None


def get_recent_jobs(conn: sqlite3.Connection, limit: int = 50) -> list[Job]:
    rows = conn.execute(
        "SELECT * FROM jobs ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_row_to_job(r) for r in rows]


def get_jobs_by_status(conn: sqlite3.Connection, status: str) -> list[Job]:
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = ? ORDER BY started_at DESC", (status,)
    ).fetchall()
    return [_row_to_job(r) for r in rows]


def get_jobs_with_artist_info(
    conn: sqlite3.Connection, status: str | None = None, limit: int = 50
) -> list[sqlite3.Row]:
    query = (
        "SELECT j.*, a.handle AS artist_handle, a.site AS artist_site "
        "FROM jobs j JOIN artists a ON j.artist_id = a.id"
    )
    params: list = []
    if status:
        query += " WHERE j.status = ?"
        params.append(status)
    query += " ORDER BY j.started_at DESC LIMIT ?"
    params.append(limit)
    return conn.execute(query, params).fetchall()


def clean_orphaned_jobs(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "UPDATE jobs SET status = 'failed', error_message = 'Container restarted mid-run', finished_at = datetime('now') WHERE status = 'running'"
    )
    conn.commit()
    return cur.rowcount


# --- Logs ---


def insert_log(
    conn: sqlite3.Connection,
    level: str,
    source: str,
    message: str,
    job_id: int | None = None,
    artist_id: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO logs (level, source, message, job_id, artist_id) VALUES (?, ?, ?, ?, ?)",
        (level, source, message, job_id, artist_id),
    )
    conn.commit()


def get_logs(
    conn: sqlite3.Connection,
    level: str | None = None,
    source: str | None = None,
    limit: int = 100,
) -> list[sqlite3.Row]:
    query = "SELECT * FROM logs WHERE 1=1"
    params: list = []
    if level:
        query += " AND level = ?"
        params.append(level)
    if source:
        query += " AND source = ?"
        params.append(source)
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    return conn.execute(query, params).fetchall()


def prune_old_logs(conn: sqlite3.Connection, days: int = 90) -> int:
    cur = conn.execute(
        "DELETE FROM logs WHERE timestamp < datetime('now', ?)",
        (f"-{days} days",),
    )
    conn.commit()
    return cur.rowcount


# --- Helpers ---


def _row_to_artist(row: sqlite3.Row) -> Artist:
    return Artist(
        id=row["id"],
        handle=row["handle"],
        site=row["site"],
        source_url=row["source_url"],
        added_at=row["added_at"],
        last_scan_at=row["last_scan_at"],
        is_active=bool(row["is_active"]),
    )


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        artist_id=row["artist_id"],
        status=row["status"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        file_count=row["file_count"],
        total_bytes=row["total_bytes"],
        error_message=row["error_message"],
        triggered_by=row["triggered_by"],
    )
