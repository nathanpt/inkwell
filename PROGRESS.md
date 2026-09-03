# Project Progress

**Last assessed:** 2026-09-03
**Repository state:** branch `main` · `origin/main` at `9e3da2c`; HEAD leads by 3 local-only commits — two UI passes (`5773140`, `960f931`) + repair rate-limit mitigation (see "Active work"). No-push policy in `AGENTS.md`.

## Canonical sources

| Role | File | Notes |
|------|------|-------|
| Architecture / codemap | `ARCHITECTURE.md` | Stable entry points, directory map, data flow, boundaries, invariants. Read first. |
| Deep design rationale | `docs/DESIGN.md` | The "why" + historical decisions. Partially stale on current structure — see "Open tasks". |
| Roadmap / planning | `docs/ROADMAP.md` | Live checklist of planned + completed work. |
| Agent instructions | `AGENTS.md` | Routing contract + project conventions. |
| Onboarding / quickstart | `README.md` | Features, install, config schema, dev commands. |

`ARCHITECTURE.md` is the stable codemap (current tree, boundaries, invariants); `docs/DESIGN.md` holds the deeper design rationale. Keep structure/boundary facts current in `ARCHITECTURE.md`; defer the "why" and historical decisions to `DESIGN.md`.

## Confirmed working surfaces

- **Tests:** `.venv/bin/python -m pytest tests/ -q` → **227 passed** (observed 2026-09-03, ~1.6s; includes the `streamlit.testing.v1.AppTest` UI tests in `tests/test_artists_ui.py`). Local venv runs Python 3.14.4; production image is `python:3.12-slim` (Dockerfile).
- **Entry point:** `streamlit run src/app.py` (compose `CMD`). `main()` → `bootstrap()` → scheduler setup → dashboard render. See `src/app.py`.
- **Schema:** SQLite, `PRAGMA user_version = 6`. Migrations: v2→v3 added the `files` table; v3→v4 added `idx_files_downloaded`; v4→v5 deduped `(artist_id, filename)` rows and enforced uniqueness; v5→v6 canonicalized x.com artist `source_url`s to `…/media?filter=photo`. See `src/db.py`, `src/bootstrap.py`.
- **Download engine:** `gallery-dl` invoked as subprocess from `src/downloader.py`; one artist at a time, per-artist job lock, directory-diff metrics. gallery-dl stderr is mirrored to the `logs` table (`source='gallery-dl'`), and a download timeout re-raises as a failed job (`timed out after Ns`, not a masked exit code).
- **Multi-site:** adapters in `src/sites/` (`base.py`, `xcom.py`, `pixiv.py`, `deviantart.py`) with per-site gallery-dl configs `gallery-dl.{xcom,pixiv,deviantart}.conf`.
- **Gallery tab:** `src/sections/gallery.py` + `src/gallery_media.py`. Grid is sorted post-chronologically by the numeric post ID in the filename basename (X snowflake / Pixiv illust id / DeviantArt deviation id), asc/desc driven in SQL via `db.get_recent_files(order=...)`.
- **Auto-zip:** `src/zipper.py`, triggered post-job and retroactively from Settings.
- **Scheduling:** APScheduler via `src/scheduler.py`; cron from `config.toml`.
- **Repair & integrity:** `src/integrity.py` reports missing files; `src/repair.py` re-downloads them via per-post gallery-dl URLs and reconciles rows (`_reconcile_artist`: exact basename match + numeric-post-id prefix). Repair parses gallery-dl PipeOutput stdout (`_downloaded_paths`), relocates files written under a renamed-author dir back into `nas/{handle}/{year}/` (`_relocate_renamed`), and logs per-chunk file counts, a stderr-tail WARNING on unclassified non-zero exits, and a rename hint when 0 rows recover despite gallery-dl success. `repair_missing` takes an optional `artist_id` that scopes the run to one artist (the Artists-page per-row **Repair** button; scheduled/Settings runs stay global). A badly-decayed artist (≥50 missing rows and ≥25% of its DB rows) is instead repaired by one timeline re-walk of its `source_url` (~1 API call per ~20 tweets vs 1 per tweet); 429/timeout failures fall back to per-post, a capped run's whole budget is consumed by one re-walk, and duplicate-handle groups scope the re-walk to one artist so the never-walked artist's rows survive the reconcile safeguard. Repair invocations are bounded by `[repair].timeout` (`RepairConfig`, default 5400s) — `subprocess.TimeoutExpired` is converted to an rc=124 failed batch so the fallback engages instead of the raise aborting the run. The stored `integrity:last_check` summary (Artists page Missing %) is folded per chunk AND after a re-walk, then fully refreshed at run end.
- **Logs tab + export:** `src/sections/logs.py` renders the Logs tab; "Export Logs" downloads the filtered entries (level/source/limit) as plain text via `_format_export` (newest-first, `job_id`/`artist_id` inline when present).
- **Rate limiting:** `src/rate_limiter.py` tracks a per-site backoff multiplier; a site "pauses" at `pause_threshold`, but the pause is **time-bounded** by `pause_seconds` (default 900s) since the last hit, so it auto-clears once the upstream window passes. Repair (`src/repair.py`) *waits* for un-pause via `_wait_for_unpause`; the downloader skips (the scheduler retries).

## Active work

- **Repair rate-limit mitigation** (local-only, newest commit): targeted x.com repairs died to rate limits/timeouts because per-post repair burns 1 TweetDetail call per tweet (vs ~1 per 20 for a timeline walk) and two of gallery-dl's internal ~15-min 429 sleeps exceed the shared 1200s download timeout. Three mitigations: (1) **pacing** — `gallery-dl.xcom.conf` `sleep-request` 4-10s → 15-30s (community-validated, gallery-dl #8864) and `[sites.xcom]` cooldown 60-120s → 90-180s, plus `[download].timeout` 1200 → 3600s so standard walks fit the slower pacing (the plan accepted slower downloads; 1200s only fit the old 4-10s spacing); (2) **`[repair].timeout`** (new `RepairConfig`, default 5400s) bounds repair invocations, with timeout kills converted to rc=124 failed batches that trigger the fallback; (3) **timeline re-walk** for badly-decayed artists (see Repair & integrity above). A post-code simplify pass (3 parallel reviewers) hardened it: shared `_abort_site_auth`, rate-limiter symmetry (`record_success` on re-walk success, `record_hit` only on detected 429s + pause-wait before fallback), stderr-tail diagnostics on generic re-walk failures, summary fold on re-walk success, and duplicate-handle group scoping (a `(site, handle)` group can span artist rows — e.g. `/alice` + `/alice/likes` — and the unscoped version could purge the never-walked artist's rows). Suite 227 passed. **Production verification pending:** after deploy + container restart (both configs bind-mounted), Logs tab source=repair — Drowsy_sheep should log `timeline re-walk` and stop dying mid-chunk; standard downloads adopt the slower pacing automatically.
- **Download timeout diagnostics** (`c970c0f`, local-only): production downloads of large/cold x.com timelines hit Inkwell's gallery-dl timeout and died as the opaque `exited with code -9` with no stderr visibility. `_run_gallery_dl` (`src/downloader.py`) now re-raises `subprocess.TimeoutExpired` after kill+reap — previously it caught the timeout, SIGKILLed the process (returncode `-9`), and returned a `CompletedProcess`, leaving `download_artist`'s timeout branch dead and the job logging `gallery-dl exited with code -9`. Jobs now log `gallery-dl timed out after Ns`. gallery-dl stderr is mirrored to the `logs` table (`source='gallery-dl'`, `INFO`, tagged with `job_id`/`artist_id`) so the Logs tab / Export Logs surface the real 429s during a run. `[download] timeout` raised 600 → 1200s so cold `UserMedia` walks (API-paced enumeration, not skipped by `archive.db`) finish in one pass. Full suite **204 passed** (two new downloader tests).
- **Previously local-only work has shipped to `origin/main` (`9e3da2c`):** the rate-limit-wait fix (`063eccf`), up-front auth-invalid site skips + x.com pacing + the offline analyzer (`e412a9d`), the x.com repair call-budget tightening (`fb7db18`), jittered (humanlike) download pacing for all providers (`eddc20e`), x.com URL canonicalization to `media?filter=photo` (`36ab655`), and the Logs refresh button (`9e3da2c`). Their UI smoke (Export Logs, per-chunk repair logs, rename WARNING, rate-limit-wait, gallery sort) is still pending verification on the production container — the app can't boot in this dev env.

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
- [ ] **Logs source-filter dropdown is incomplete.** Source: `src/sections/logs.py:62` — options are `["All", "downloader", "scheduler", "bootstrap"]`, omitting `repair`, `gallery-dl`, and integrity. Repair and gallery-dl (mirrored stderr) logs only surface when Source = "All", which limits the Export Logs button for debugging.
- [ ] **Duplicated `RateLimitConfig`.** Source: `src/config_loader.py:48` and `src/rate_limiter.py:14` each define `RateLimitConfig`; `Config.rate_limit` uses the `config_loader` one while the limiter functions access it duck-typed. They must be kept in sync — adding `pause_seconds` to only one broke a downloader test this session. Collapse to a single definition.
- [ ] **`README.md` config schema is stale.** Source: `README.md:95-99` — the `[rate_limit]` example omits `pause_seconds` (added this session); the onboarding config block otherwise lags `config.toml`.
- [ ] **`archive.db` re-emits already-recorded files (wasted re-downloads).** Source: the `files`-table dedup work (v5 migration + upsert) was triggered by duplicate rows where gallery-dl re-downloaded a file that was already on disk. The dedup collapses the duplicate rows and prevents new ones, but does NOT address the root cause: gallery-dl's `archive.db` (fully owned by gallery-dl; Inkwell never reads/writes it) has a gap that causes the re-emission in the first place. Each re-emission re-downloads bytes that already landed. Investigate the `archive.db` gap (e.g. post-id / post-date filename contract vs. archive key, or a zip-vs-loose path mismatch) as a separate decision; out of scope for the dedup work.

## Verification status

- **Passing:** `pytest` — 227 passed (full suite, observed this assessment).
- **Not verified in CI:** tests are not part of `build.yml`; a regression can ship to `main` green-image but red-tests.

## Decisions, generators, and constraints

- **No generators.** No `GENERATED` / `DO NOT EDIT` / marker-fenced regions exist anywhere in the repo; all docs are human-maintained. Safe to edit any context file directly.
- **Production runs elsewhere.** This machine is not the production server (see `AGENTS.md` → Environment). Do not assume the local Docker daemon runs the app.
- **Storage layout:** `/nas/inkwell/{artist_handle}/{year}/`. Year directories are zipped per `config.toml [zip]`.
- **Two SQLite DBs:** `inkwell.db` (app-owned: artists, jobs, logs, files, state) and `archive.db` (gallery-dl-owned dedup — Inkwell never touches it). Both in the `inkwell-data` named volume, which must stay on local (non-NFS) storage for WAL locking.
- **gallery-dl filename contract:** every site config writes `<numeric_post_id>_<rest>.<ext>` as the leading token. This is the sort key for the gallery and a load-bearing assumption for any future site config.
- **Config files bind-mounted read-only** from the repo (`config.toml`, the three `gallery-dl.*.conf`). Edit on host, restart to apply.
- **Secrets:** `.env` (`INKWELL_PASSWORD`) and `cookies.txt` are runtime-only, never committed. `config.toml [auth].password_hash` holds the bcrypt hash.

## Next useful checks

- The Streamlit app boots locally for UI work via `scripts/dev_ui.py` (see Verification status), replacing the old "must smoke on the production container" constraint for pure-UI checks. Behavior smoke that needs real gallery-dl runs (per-chunk repair logs, rename WARNING, rate-limit-wait, gallery thumbnails over real media) still belongs on the production container.
- Add a test step to `.github/workflows/build.yml` so regressions are caught before image push.
- Reconcile `docs/DESIGN.md` §4.1/§10/§13/§14 with the current tree (or mark those sections as "see ROADMAP.md for live status").
