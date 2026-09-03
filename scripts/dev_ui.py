"""Boot the Streamlit UI on a dev box (no Docker, no /app) with a scratch
config/DB/NAS under /tmp/inkwell-ui.

    .venv/bin/python -m streamlit run scripts/dev_ui.py --server.port 8502

First run creates the scratch tree: config.toml (NAS -> scratch dir, zip
off, empty password hash) and copies the repo's gallery-dl.*.conf — the app
refuses to boot without them. The DB starts empty; add artists in the UI or
seed with db.insert_artist / insert_file_records. Login is bypassed (dev
harness only; the empty hash already makes any password work).

Edits under src/ are NOT hot-reloaded: Streamlit only watches the main
script's folder, and this launcher lives in scripts/. Restart the process
after code changes.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRATCH = Path("/tmp/inkwell-ui")
sys.path.insert(0, str(REPO))

CONFIG_TEMPLATE = """\
[nas]
mount_path = "/tmp/inkwell-ui/nas"

[schedule]
cron = "0 3 * * *"

[download]
retry_attempts = 3
retry_backoff = [5, 15, 45]
timeout = 1200
inter_artist_cooldown = [30, 60]

[cookies]
expiry_warning_days = 30

[auth]
password_hash = ""

[retention]
log_days = 90

[rate_limit]
multiplier_step = 1.5
max_multiplier = 8.0
pause_threshold = 6.0
decay_rate = 0.5
pause_seconds = 900

[zip]
enabled = false
on_job_complete = false
compression_level = 6

[sites.xcom]
cooldown = [60, 120]

[sites.pixiv]
cooldown = [5, 15]

[sites.deviantart]
cooldown = [5, 15]

[integrity]
enabled = true
check_cron = "0 4 * * 0"
auto_repair = true
max_posts_per_run = 200
"""


def _first_run_setup() -> None:
    cfg_dir = SCRATCH / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (SCRATCH / "data").mkdir(parents=True, exist_ok=True)
    (SCRATCH / "nas").mkdir(parents=True, exist_ok=True)

    confs = sorted(REPO.glob("gallery-dl.*.conf"))
    if not confs:
        raise SystemExit("No gallery-dl.*.conf found in repo root")
    for conf in confs:
        dst = cfg_dir / conf.name
        if not dst.exists():
            shutil.copy(conf, dst)

    cfg = cfg_dir / "config.toml"
    if not cfg.exists():
        cfg.write_text(CONFIG_TEMPLATE)


def main() -> None:
    _first_run_setup()

    import src.bootstrap as bootstrap_mod
    import src.db as db_mod

    bootstrap_mod.DATA_DIR = SCRATCH / "data"
    bootstrap_mod.CONFIG_DIR = SCRATCH / "config"
    bootstrap_mod.DEFAULTS_DIR = SCRATCH / "defaults"

    db_path = SCRATCH / "data" / "inkwell.db"
    db_mod.DEFAULT_DB_PATH = db_path
    db_mod.configure(db_path)
    orig_connect = db_mod.connect
    db_mod.connect = lambda *a, **k: orig_connect(db_path)

    import streamlit as st

    import src.app as app_mod

    orig_init = app_mod._init_session_state

    def _init_session_state():
        orig_init()
        st.session_state.authenticated = True  # dev harness: skip login gate

    app_mod._init_session_state = _init_session_state
    app_mod.main()


main()
