# Project Progress

**Last assessed:** 2026-08-11
**Repository state:** branch `main` · HEAD `9dc7f00` ("logs: add Export Logs download button"); working tree holds only this untracked `PROGRESS.md` — all code committed and pushed to `origin`.

## Canonical sources

| Role | File | Notes |
|------|------|-------|
| Architecture / system map | `docs/DESIGN.md` | Strong, human-maintained. Partially stale — see "Open tasks". |
| Roadmap / planning | `docs/ROADMAP.md` | Live checklist of planned + completed work. |
| Agent instructions | `AGENTS.md` | Routing contract + project conventions. |
| Onboarding / quickstart | `README.md` | Features, install, config schema, dev commands. |

There is intentionally **no separate `ARCHITECTURE.md`**: `docs/DESIGN.md` already owns that role. Update DESIGN.md rather than forking a second map.

## Confirmed working surfaces

- **Tests:** `.venv/bin/python -m pytest tests/ -q` → **176 passed** (observed 2026-08-11, ~1.3s). Local venv runs Python 3.14.4; production image is `python:3.12-slim` (Dockerfile).
- **Entry point:** `streamlit run src/app.py` (compose `CMD`). `main()` → `bootstrap()` → scheduler setup → dashboard render. See `src/app.py`.
- **Schema:** SQLite, `PRAGMA user_version = 4`. Migrations: v2→v3 added the `files` table; v3→v4 added `idx_files_downloaded`. See `src/db.py`, `src/bootstrap.py`.
- **Download engine:** `gallery-dl` invoked as subprocess from `src/downloader.py`; one artist at a time, per-artist job lock, directory-diff metrics.
- **Multi-site:** adapters in `src/sites/` (`base.py`, `xcom.py`, `pixiv.py`, `deviantart.py`) with per-site gallery-dl configs `gallery-dl.{xcom,pixiv,deviantart}.conf`.
- **Gallery tab:** `src/sections/gallery.py` + `src/gallery_media.py`. Grid is sorted post-chronologically by the numeric post ID in the filename basename (X snowflake / Pixiv illust id / DeviantArt deviation id), asc/desc driven in SQL via `db.get_recent_files(order=...)`.
- **Auto-zip:** `src/zipper.py`, triggered post-job and retroactively from Settings.
- **Scheduling:** APScheduler via `src/scheduler.py`; cron from `config.toml`.
- **Repair & integrity:** `src/integrity.py` reports missing files; `src/repair.py` re-downloads them via per-post gallery-dl URLs and reconciles rows (`_reconcile_artist`: exact basename match + numeric-post-id prefix). Repair now parses gallery-dl PipeOutput stdout (`_downloaded_paths`), relocates files written under a renamed-author dir back into `nas/{handle}/{year}/` (`_relocate_renamed`), and logs per-chunk file counts, a stderr-tail WARNING on unclassified non-zero exits, and a rename hint when 0 rows recover despite gallery-dl success.
- **Logs tab + export:** `src/sections/logs.py` renders the Logs tab; "Export Logs" downloads the filtered entries (level/source/limit) as plain text via `_format_export` (newest-first, `job_id`/`artist_id` inline when present).

## Active work

- **Repair diagnostics + rename-author recovery** (`ff218cb`) and **Export Logs button** (`9dc7f00`): both on `main` and pushed to `origin`; full suite green at 176. Remaining unverified surface is the Streamlit UI itself — the app can't boot in this dev env (hardcodes container path `/app/defaults/config.toml`), so the Export Logs button and the new per-chunk repair logs / rename WARNING must be confirmed on the production container.
- Gallery post-chronological sort (`6d232c3`) shipped earlier; its UI manual smoke (Newest/Oldest first on an X artist) is still unverified on the production container.

## Open tasks and technical debt

Each item is sourced; none is invented.

- [ ] **`docs/DESIGN.md` is partially stale.** Source: direct inspection vs. current tree.
  - §10 Project Structure lists a singular `gallery-dl.conf` and only 4 UI sections; the repo has 3 per-site configs (`gallery-dl.{xcom,pixiv,deviantart}.conf`) and `sections/` now includes `gallery.py`, plus top-level `src/gallery_media.py`, `src/config_loader.py`, `src/rate_limiter.py`, and the `src/sites/` package.
  - §4.1 Data Model omits the `files` table (added v3) and the v4 index.
  - §13 Phase 1–5 implementation checkboxes are all `[ ]` despite the work being done (ROADMAP.md is the live tracker).
  - §14 "Open Decisions: Testing strategy — TBD" is superseded by the 144-test suite.
- [ ] **CI does not run tests.** Source: `.github/workflows/build.yml` only builds and pushes the Docker image to GHCR on push to `main`; there is no test/lint/typecheck step. Local `pytest` is the only verification gate.
- [ ] **No lint/typecheck configured.** Source: `pyproject.toml` defines no `ruff`/`mypy`/formatter config. Not blocking, but unverified.
- [ ] **`docs/DEV_GUIDE.md` is a near-empty stub** (dev-server + test command only). Source: 345-byte file.
- [ ] **`.factory/` is an empty vestigial directory.** Source: `ls .factory` (no contents).
- [ ] **Logs source-filter dropdown is incomplete.** Source: `src/sections/logs.py:51` — options are `["All", "downloader", "scheduler", "bootstrap"]`, omitting `repair` (and integrity). Repair logs only surface when Source = "All", which limits the new Export Logs button for repair debugging.

## Verification status

- **Passing:** `pytest` — 176 passed (full suite, observed this assessment).
- **Not verified in CI:** tests are not part of `build.yml`; a regression can ship to `main` green-image but red-tests.
- **Not run this assessment:** the Streamlit app itself (not launched here), Docker build, gallery-dl subprocess (requires credentials + NAS).

## Decisions, generators, and constraints

- **No generators.** No `GENERATED` / `DO NOT EDIT` / marker-fenced regions exist anywhere in the repo; all docs are human-maintained. Safe to edit any context file directly.
- **Production runs elsewhere.** This machine is not the production server (see `AGENTS.md` → Environment). Do not assume the local Docker daemon runs the app.
- **Storage layout:** `/nas/inkwell/{artist_handle}/{year}/`. Year directories are zipped per `config.toml [zip]`.
- **Two SQLite DBs:** `inkwell.db` (app-owned: artists, jobs, logs, files, state) and `archive.db` (gallery-dl-owned dedup — Inkwell never touches it). Both in the `inkwell-data` named volume, which must stay on local (non-NFS) storage for WAL locking.
- **gallery-dl filename contract:** every site config writes `<numeric_post_id>_<rest>.<ext>` as the leading token. This is the sort key for the gallery and a load-bearing assumption for any future site config.
- **Config files bind-mounted read-only** from the repo (`config.toml`, the three `gallery-dl.*.conf`). Edit on host, restart to apply.
- **Secrets:** `.env` (`INKWELL_PASSWORD`) and `cookies.txt` are runtime-only, never committed. `config.toml [auth].password_hash` holds the bcrypt hash.

## Next useful checks

- The Streamlit app can't boot in this dev env (`bootstrap()` → `load_config()` hardcodes `/app/defaults/config.toml`, a container path), so UI smoke (gallery sort, Export Logs button, repair per-chunk logs / rename WARNING) must run on the production container, not this machine.
- Add a test step to `.github/workflows/build.yml` so regressions are caught before image push.
- Reconcile `docs/DESIGN.md` §4.1/§10/§13/§14 with the current tree (or mark those sections as "see ROADMAP.md for live status").
