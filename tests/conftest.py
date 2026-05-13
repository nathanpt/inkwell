from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from src import db
from src.config_loader import Config, DownloadConfig, NASConfig
from src.sites.base import SiteRegistry
from src.sites.xcom import XComAdapter


@pytest.fixture
def test_registry():
    reg = SiteRegistry()
    reg.register(XComAdapter())
    return reg


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def db_conn(tmp_path):
    db_path = tmp_path / "test_inkwell.db"
    conn = db.connect(db_path)
    db.init_schema(conn)
    db.seed_state(conn)
    yield conn
    conn.close()


@pytest.fixture
def test_config(tmp_path):
    nas_path = tmp_path / "nas"
    nas_path.mkdir()
    return Config(
        nas=NASConfig(mount_path=str(nas_path)),
        download=DownloadConfig(
            retry_attempts=2,
            retry_backoff=[0, 0],
            timeout=10,
            inter_artist_cooldown=[0, 0],
        ),
    )


@pytest.fixture
def artist_dir(tmp_path):
    d = tmp_path / "nas" / "testartist"
    d.mkdir(parents=True)
    return d
