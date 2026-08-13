from __future__ import annotations

from src import db


def _insert_artist(conn, handle: str, site: str, source_url: str) -> int:
    cur = conn.execute(
        "INSERT INTO artists (handle, site, source_url) VALUES (?, ?, ?)",
        (handle, site, source_url),
    )
    conn.commit()
    return cur.lastrowid


def _source_url(conn, handle: str) -> str:
    row = conn.execute(
        "SELECT source_url FROM artists WHERE handle = ?", (handle,)
    ).fetchone()
    return row["source_url"]


def test_xcom_url_canonicalization_migration(tmp_path):
    db_path = tmp_path / "migration.db"
    db.configure(db_path)
    conn = db.connect(db_path)
    db.init_schema(conn)

    # Seed the two shapes the old normalizer could have stored, plus two rows
    # that the migration must leave byte-identical (non-media tab, other site).
    _insert_artist(conn, "xsbare", "x.com", "https://x.com/xsbare")
    _insert_artist(conn, "xsmedia", "x.com", "https://x.com/xsmedia/media")
    _insert_artist(conn, "xswr", "x.com", "https://x.com/xswr/with_replies")
    _insert_artist(conn, "pxuser", "pixiv", "https://www.pixiv.net/users/12345")

    # Pretend the DB predates this migration.
    conn.execute("PRAGMA user_version = 5")
    conn.commit()

    # Run the migration.
    db.init_schema(conn)

    assert _source_url(conn, "xsbare") == "https://x.com/xsbare/media?filter=photo"
    assert _source_url(conn, "xsmedia") == "https://x.com/xsmedia/media?filter=photo"
    assert _source_url(conn, "xswr") == "https://x.com/xswr/with_replies"
    assert _source_url(conn, "pxuser") == "https://www.pixiv.net/users/12345"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 6

    # Idempotent: re-running the migration changes nothing.
    db.init_schema(conn)
    assert _source_url(conn, "xsbare") == "https://x.com/xsbare/media?filter=photo"
    assert _source_url(conn, "xsmedia") == "https://x.com/xsmedia/media?filter=photo"
    assert _source_url(conn, "xswr") == "https://x.com/xswr/with_replies"
    assert _source_url(conn, "pxuser") == "https://www.pixiv.net/users/12345"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 6

    conn.close()
