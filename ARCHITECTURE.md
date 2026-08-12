# Architecture

> **Lean stable codemap.** Deep design rationale and historical decisions live in
> [`docs/DESIGN.md`](docs/DESIGN.md); live status (test counts, active work, open debt)
> lives in [`PROGRESS.md`](PROGRESS.md). Update *this* file when stable structure, entry
> points, data flow, or external boundaries change; do not hardcode volatile counts here.
>
> `docs/DESIGN.md` predates several features and is partially stale on current structure
> (per-site configs, the `files` table, the tabbed UI, the integrity/repair pipeline). Treat
> it as the "why" and this file as the "what/where" for the tree as it stands today.

## System purpose

Inkwell is a self-hosted media archiver for artists on X.com, Pixiv, and DeviantArt. It
drives [`gallery-dl`](https://github.com/mikf/gallery-dl) as a subprocess to download
high-resolution media to a NAS, exposing artist management, manual/scheduled downloads,
integrity checks, repair, and a gallery through a Streamlit web dashboard — no terminal
required.

## Stack and runtime

- **Language:** Python 3.12 in production (`python:3.12-slim`); the dev venv runs 3.14.
- **UI:** Streamlit single-page app, port 8501, bcrypt password gate.
- **Scheduling:** APScheduler `BackgroundScheduler` (cron-driven), running in a daemon thread.
- **Persistence:** SQLite in WAL mode; `PRAGMA user_version` migrations (no Alembic).
- **Download engine:** `gallery-dl` invoked as a subprocess — **never** imported as a library.
- **Dependencies:** managed with [`uv`](https://github.com/astral-sh/uv) (`pyproject.toml`).
- **Deployment:** single Docker container from a prebuilt GHCR image (`compose.yaml`).

## Entry points and execution flow

- **Container CMD:** `streamlit run src/app.py --server.port=8501 --server.address=0.0.0.0`.
- **`src/app.py::main()`** → `_init_session_state()` (loads config + starts scheduler into
  `st.session_state`) → password gate → `_render_dashboard()` (five tabs: Artists, Downloads,
  Gallery, Settings, Logs — see `src/sections/`).
- **Startup (`src/bootstrap.py::bootstrap()`):** load config → create/migrate schema → enable
  WAL → seed `state` → mark orphaned `running` jobs `failed` → prune old logs. Full sequence
  in `docs/DESIGN.md` §5.6.
- **Scheduled jobs (`src/scheduler.py`):**
  - cron from `[schedule]` → `_scheduled_run` → `download_all` (or `download_stale` when
    `stale_threshold_days`/time-window filtering is set).
  - if `[integrity].enabled`, a weekly cron (`check_cron`) → `_scheduled_integrity`, which
    runs the integrity check and (when `auto_repair`) the repair driver.
- **Request flow:** a UI action or scheduled tick spawns a daemon thread that calls into
  `downloader` / `integrity` / `repair`, which shell out to `gallery-dl` and write to the NAS
  + `inkwell.db`. The Streamlit main thread never blocks on network I/O.

## Directory map

```
src/
  app.py             Streamlit entry: session state, auth gate, dashboard tabs
  bootstrap.py        Startup sequence (config, schema migrate, WAL, state seed, cleanup)
  config_loader.py    TOML → dataclasses (Config + nested configs); defines a RateLimitConfig
  db.py               SQLite access (connection-per-op), schema/migrations, all CRUD + logs/state
  downloader.py       Per-artist download: job lock, gallery-dl subprocess, dir-diff metrics, zip
  scheduler.py        APScheduler: cron download run + weekly integrity check
  integrity.py        Missing-file detection (loose files vs DB rows); sibling-zip consolidation
  repair.py           Re-fetch missing files via per-post gallery-dl URLs; reconcile + relocate
  zipper.py           Per-artist/year ZIP archival (post-job + retroactive)
  rate_limiter.py     Per-site backoff multiplier + time-bounded pause
  gallery_media.py    Gallery tab file retrieval and chronological sorting
  cookie_manager.py   cookies.txt upload + expiry management
  url_validator.py    Per-site URL pattern matching + adapter registry
  nas_monitor.py      NAS mount availability check (pre-flight + retry)
  models.py           Dataclasses (Artist, Job, …)
  sections/           Streamlit tab renderers: artists, downloads, gallery, settings, logs
  sites/              SiteAdapter ABC + SiteRegistry; xcom, pixiv, deviantart adapters
tests/                Pytest suite mirroring src/ (see "Testing" below; conftest.py fixtures)
scripts/             Operational scripts: analyze_rate_limits.py (offline rate-limit/repair analyzer over a copied DB; stdlib-only, imports nothing from src)
docs/                 DESIGN.md (deep design), ROADMAP.md, DEV_GUIDE.md, research/, diagrams
config.toml           App config (bind-mounted read-only)
gallery-dl.{xcom,pixiv,deviantart}.conf   Per-site gallery-dl configs (bind-mounted read-only)
compose.yaml  Dockerfile  .github/workflows/build.yml   Deployment (single container, GHCR)
.streamlit/config.toml   Streamlit-level settings (baked into image)
pyproject.toml           Dependencies (uv)
```

## Core components and data flow

- **Multi-site adapter pattern (`src/sites/base.py`):** `SiteAdapter` is the ABC every site
  implements — URL matching/parsing, gallery-dl config + archive-db + auth-file paths, auth
  validity + auth/rate-limit error detection, display handle, and `build_post_url` (single-post
  re-fetch URL used by repair). `SiteRegistry` maps a site key → adapter; `create_registry()`
  registers all built-ins. Adding a site = new adapter + gallery-dl config, not a re-architecture.
- **Download pipeline (`src/downloader.py`):** one artist at a time behind a per-artist job lock;
  snapshots the artist dir before/after `gallery-dl` to derive `file_count`/`total_bytes`; records
  a success (decaying the rate-limit multiplier) or a rate/auth failure; triggers auto-zip on
  success. `download_all` / `download_stale` are the two entry points; both skip artists whose
  site auth is flagged invalid before creating a job.
- **Integrity + repair pipeline:** `src/integrity.py` reports missing files (DB rows with no loose
  file, accounting for sibling zips); `src/repair.py::repair_missing` re-fetches them in chunks via
  per-post gallery-dl URLs, then `_reconcile_artist` matches landed files back to rows (exact
  basename, then numeric-post-id prefix), and `_relocate_renamed` moves files gallery-dl wrote
  under a renamed-author dir back into the canonical `nas/{handle}/{year}/`. Sites flagged
  `auth_valid:<site> == "0"` are skipped before any fetch, and a chunk that 429s waits out the
  rate window (via `_wait_for_unpause`) before its retry.
- **Rate limiter (`src/rate_limiter.py`):** per-site `cooldown_multiplier` (×`multiplier_step` per
  hit, capped at `max_multiplier`, decays by `decay_rate` per success). A site is "paused" at
  `pause_threshold`; the pause is **time-bounded** by `pause_seconds` since the last hit, so it
  auto-clears once the upstream rate window passes. Repair *waits* for un-pause; the downloader
  *skips* (the scheduler retries).
- **Storage flow:** `gallery-dl` writes to `/nas/inkwell/{handle}/{year}/<post_id>_<rest>.<ext>`;
  the downloader dir-diffs and records rows in `inkwell.db`'s `files` table; the zipper later
  compresses each year dir into `{year}.zip` (dedup is unaffected — gallery-dl tracks URLs in
  `archive.db`, not filesystem paths).

## External boundaries

- **`gallery-dl` subprocess:** the CLI contract (config path, `--dest`, `--cookies`,
  `--write-archive`, per-post URLs) is the integration surface; stdout (`PipeOutput`: bare path per
  file, `# {path}` per skip) is parsed by repair.
- **NAS:** bind-mounted at `/nas/inkwell` (host NFS, mounted `soft,intr`); media only — never the DB.
- **SQLite WAL:** `inkwell.db` lives in the local `inkwell-data` named volume (POSIX locking); it
  must **not** sit on NFS.
- **Two databases:** `inkwell.db` (app-owned: artists, jobs, files, logs, state) and `archive.db`
  (gallery-dl-owned dedup — Inkwell never reads or writes it).
- **Site auth:** x.com `cookies.txt`, Pixiv refresh token, DeviantArt cookies — runtime-only, in the
  named volume, never committed.
- **Docker volumes (`compose.yaml`):** `inkwell-data` (named, RW, local) for DBs + cookies;
  `./config` (bind, RO) for `config.toml` + gallery-dl configs; `/nas/inkwell` (bind, RW) for media.

## Testing and verification surfaces

- **Unit tests:** `.venv/bin/python -m pytest tests/ -v` — the suite mirrors `src/`. Live pass/fail
  count and CI gaps are tracked in `PROGRESS.md` (not hardcoded here).
- **CI (`.github/workflows/build.yml`):** on push to `main`, builds and pushes the Docker image to
  GHCR (`latest` + commit-sha tags). **There is no test/lint/typecheck step** — local `pytest` is the
  only verification gate (open item, see PROGRESS.md).
- **No lint/typecheck** is configured in `pyproject.toml`.
- **UI is not runnable in the dev env:** the app loads config from container paths
  (`/app/config/config.toml` with a `/app/defaults/` fallback baked into the image), so UI changes
  are verified via `streamlit.testing.v1.AppTest` in-process or on the production container.

## Important constraints, generators, and unknowns

**Load-bearing invariants (changing these ripples widely):**
- **Filename contract:** every site config writes `<numeric_post_id>_<rest>.<ext>` as the leading
  token. It is the gallery sort key **and** the repair reconcile key.
- **Storage layout:** `/nas/inkwell/{handle}/{year}/`. Per-site gallery-dl configs build the author
  dir from a *mutable* name (`{author[name]}` / `{user[name]}`), so a renamed artist's re-downloads
  land elsewhere — repair's `_relocate_renamed` exists because of this.
- **gallery-dl is a subprocess, never a library**; `archive.db` is gallery-dl-owned, never touched.
- **SQLite WAL must stay on local (non-NFS) storage** for reliable locking.
- **Connection-per-operation** DB pattern keeps Streamlit reruns + scheduler/background threads safe.

**Harness facts:**
- **No generators, no `DO NOT EDIT` regions** — all docs are human-maintained and safe to edit.
- **Config is bind-mounted read-only**; edit on the host and restart the container to apply.
- Schema is at `PRAGMA user_version = 4`.

**Open items and tech debt** live in `PROGRESS.md` (e.g. `docs/DESIGN.md` staleness, CI has no test
step, a duplicated `RateLimitConfig` across `config_loader.py` and `rate_limiter.py`).
