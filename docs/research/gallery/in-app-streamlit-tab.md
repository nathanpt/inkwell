# In-App Streamlit Gallery Tab — Research Card

**Candidate:** `in-app-streamlit-tab` — a "Gallery" tab added to Inkwell's existing Streamlit dashboard (`src/app.py`).
**Sources analyzed (read in full this session):** `src/db.py`; `src/zipper.py`; `src/app.py`; `src/config_loader.py`; `pyproject.toml`; `config.toml`. The prior in-app gallery spec (`docs/specs/gallery-tab.md`) was also analyzed at research time but has since been **removed** from the repo; its design is captured in this card.
**Evidence tags:** `(verified: <file:line>)` = read from code · `(verified: spec ...)` / `(verified: in-app gallery spec ...)` = read from the in-app gallery spec (`docs/specs/gallery-tab.md`), which has since been **removed** — its design is preserved in this card · `(inferred: ...)` = reasoned from code/known behavior · `(unknown — needs hands-on: ...)` = not measurable from docs.

## 1. Storage handling
Reads loose + zipped via `zipfile.ZipFile.open()`. The spec's `resolve_image(artist_handle, year, filename) -> bytes` transparently handles both storage states: loose files at `{nas}/{artist}/{year}/image.jpg` read directly; zipped files at `{nas}/{artist}/{year}.zip` read via `zipfile.ZipFile.open()` into a `BytesIO` (verified: in-app gallery spec [since removed] "Architecture → Image resolution").

This matches Inkwell's actual steady state: `zip_year_dir` creates `{artist_dir}/{year}.zip`, verifies integrity, then `unlink()`s every loose file and removes the year directory (verified: src/zipper.py `zip_year_dir` — creates `{year}.zip` ~L31, verifies then unlinks loose files L52–56, removes year dir ~L65). `[zip] on_job_complete = true` means loose files exist only transiently between download and zipping (verified: config.toml `[zip]`), so in steady state the resolver is almost always on the zip branch.

Thumbnails (300px longest side, JPEG 80%) are generated with Pillow on first browse and cached permanently at `/app/data/thumbnails/{artist}/{year}/{filename}.jpg` in the local Docker volume, skipping regen if the cached thumb is newer than the source (verified: spec "Thumbnail generation" + "Performance Constraints"). Full-resolution view on click re-reads the source (zip or loose) (verified: spec UI Layout).

## 2. Data/ingest model
Neither filesystem-scan nor external API-push: it queries Inkwell's own SQLite `files` table (verified: spec "Filters" uses `SELECT DISTINCT year FROM files`; spec "Performance Constraints" states "1 DB query. No NAS file scanning"). The existing `get_recent_files(artist_id, since, limit)` already implements the core pattern — artist filter, `ORDER BY downloaded_at DESC`, `LIMIT` (verified: src/db.py:392–410, ORDER BY at L407). The gallery needs to extend it with multi-select year/site filters + offset pagination, which the current helper does not support (verified: src/db.py:392–410 has no year/site/offset params) — so `db.py` would in fact need a new/extended query function, contradicting the spec's own "Integration Points: No changes to … db.py" (verified: spec "Integration Points" vs "Implementation Order step 2").

Fit to Inkwell's "write to NAS, gallery reads" flow is maximal: the gallery IS Inkwell, reading Inkwell's own DB (the source of truth) and resolving media from the NAS on demand. Zero external ingest step.

## 3. Deployment footprint
Smallest possible. No new container, no new service, no second DB/cache. Streamlit is already a dependency (verified: pyproject.toml:7; full list below). The only additions are (a) one Python library — `Pillow` — and (b) one SQLite index, both inside the existing Inkwell container. Maintenance burden = same codebase, same deploy (verified: spec "New Dependency" + "DB Changes").

## 4. Browsable UX
Grid via `st.columns(4)`, 20 images/page (5×4), thumbnails from cache, full-res detail in an expandable view on click with metadata + original-post link (verified: spec "Rendering" + UI Layout). Good fit for a large set of unrelated single illustrations (the actual content). Limitations:
- **No lightbox/modal, no deep zoom/pan** — the spec explicitly lists these out of scope ("Streamlit limitation"; expandable detail is the substitute) (verified: spec "Out of Scope").
- **All-tabs-rerun cost:** `st.tabs()` executes every `with tab:` block on each script run, not only the active tab (inferred: Streamlit execution model — all tab bodies run top-to-bottom each rerun). `app.py` already uses `st.tabs(["Artists","Downloads","Settings","Logs"])` (verified: src/app.py:81–83), so the gallery tab's DB query + thumbnail reads would run on every interaction across all tabs unless explicitly guarded; the spec does not address this.
- **Image payload per rerun:** each `st.image()` re-sends bytes (base64 over the websocket) on every rerun; ~20 thumbs/page ≈ a few hundred KB per render, repeated on every filter/pagination interaction (inferred: Streamlit image transport).
- **Deep pagination:** the spec uses page numbers; SQLite `LIMIT/OFFSET` makes deep pages O(OFFSET). The proposed `idx_files_downloaded` helps the `ORDER BY` but not the `OFFSET` scan (inferred: SQLite query behavior). At the spec's example scale (~12k files ≈ 623 pages) this is still sub-second; at tens of thousands, late pages and the all-tabs rerun compound (unknown — needs hands-on: profile with a realistic tens-of-thousands archive).
- `session_state` churn is moderate — filter selections + current page are native widget state (verified: src/app.py:16–33 session-state pattern).

## 5. Coupling / Inkwell effort
Cleanest possible seam: in-process, no API contract, no separate deploy, no NAS-sharing negotiation. Streamlit is already a dependency (verified: pyproject.toml:7). Integration = one new module `src/sections/gallery.py` wired as a tab in `app.py` (verified: spec "Integration Points" → add `tab_gallery` between Artists/Downloads) plus the index migration and Pillow dependency. Real implementation effort is the thumbnail-cache lifecycle, the zip-vs-loose resolver, cold-cache NFS handling (field 8), and Streamlit-scale tuning (field 4) — moderate, but all internal to Inkwell.

## 6. Dedup / idempotency story
The gallery does no dedup of its own — it is a pure read view over the `files` table. Upstream dedup is gallery-dl's `--download-archive` (`archive.{site}.db`), which prevents re-downloading already-archived media (verified: src/downloader.py:262 `--download-archive` arg). The `files` table is populated post-download via `insert_file_records` (verified: src/db.py:366) and has **no UNIQUE constraint on `(artist_id, filename)`** (verified: src/db.py:98–107 — only PK on `id`), so it relies entirely on gallery-dl not re-fetching. Re-browsing is idempotent: it re-reads the DB and the on-disk thumbnail cache; thumbnail regen is keyed by `{artist}/{year}/{filename}` and skipped when a newer cache entry exists (verified: spec "Thumbnail generation"). If the DB ever held duplicate rows the gallery would display duplicates — there is no gallery-level guard.

## 7. Alignment with the codebase ground-truth constraints
- **Per-year-zip steady state:** ✅ handled by design (`resolve_image` zip branch via `zipfile.ZipFile.open()`), matching `zipper.py` (verified: src/zipper.py ~L31/L52–65).
- **NAS read-only at `/nas/inkwell/`:** ✅ compatible — gallery only reads (verified: config.toml `[nas] mount_path = "/nas/inkwell"`); writes go to the local `/app/data` volume (thumbnails), not the NAS (verified: spec "Thumbnail generation"). Read-only-safe.
- **`files` table shape:** ✅ spec uses only existing columns `artist_id, filename, year, size_bytes, downloaded_at` (verified: src/db.py:98–107).
- **Missing `idx_files_downloaded`:** ⚠️ the index does NOT exist today. Exact index lines present:
  ```sql
  CREATE INDEX IF NOT EXISTS idx_files_artist_year ON files(artist_id, year);  -- src/db.py:108
  CREATE INDEX IF NOT EXISTS idx_files_job ON files(job_id);                    -- src/db.py:109
  ```
  No index on `downloaded_at` (verified). `SCHEMA_VERSION = 3` (src/db.py:12); `init_schema` (src/db.py:91–112) sets `PRAGMA user_version = 3` (src/db.py:111). The spec's "DB Changes" correctly calls for adding the index, but its "Integration Points: No changes to db.py" contradicts its own "Implementation Order step 2" — a real migration is required (deltas in field 9). Note also: a single-column `(downloaded_at DESC)` index is a poor match for the stated query "filtered by artist AND sorted by recency" — neither it nor `(artist_id, year)` covers artist-filter + downloaded_at-sort; a composite `(artist_id, downloaded_at DESC)` would serve it better, and the year filter complicates it further (inferred: SQL query-plan analysis).
- **Pillow absent:** ⚠️ confirmed NOT a dependency. Exact list (pyproject.toml:6–11):
  ```toml
  dependencies = [
      "streamlit",
      "apscheduler~=3.10",
      "bcrypt",
      "gallery-dl",
  ]
  ```
  No `Pillow`. Spec correctly flags adding it.
- **No `[gallery]` config:** `config_loader.py`'s `Config` dataclass has `nas, schedule, download, cookies, auth, retention, rate_limit, zip, sites` — **no gallery section** (verified: src/config_loader.py `Config` + `ZipConfig`). The spec needs no config, but if thumbnail-cache toggles/regen knobs are wanted later, a new dataclass + field is the extension point (inferred).
- **No gallery code in tree today:** confirmed — a `gallery` search matches only `gallery-dl` subprocess references; the prior external-server spike was reverted (verified).

## 8. Open questions / what is NOT verifiable from docs (hands-on needed)
- **NFS cold-cache cost is inferred, not measured:** each uncached thumbnail generation reads a full-resolution member out of a per-year zip over NFS (`zipfile` reads the central directory at the tail, seeks to the member offset, pulls the compressed bytes, then Pillow decodes the full image) (inferred). For a large archive a first-time/regenerate pass = one full image read + decode per file, potentially tens of thousands of NFS reads. Needs hands-on: benchmark first-browse and "Regenerate Thumbnails" over the real NFS mount with a representative zip.
- **`zipfile.ZipFile.open()` latency per member over NFS:** random-access into a (possibly large) zip over NFS incurs RPC round-trips per seek/read; magnitude unknown (unknown — needs hands-on: time resolving N members from a multi-hundred-MB zip on NFS).
- **Cache-freshness check for zipped sources:** the spec compares thumbnail mtime to "source" mtime, but zip members expose only `date_time` (2-second granularity), not an OS mtime — the comparison semantics are undefined and could cause spurious regen or stale thumbs (unknown — needs hands-on: define/verify the freshness rule for zip-backed sources).
- **Streamlit all-tabs rerun overhead at scale** (field 4): not measured (unknown — needs hands-on: profile a session with the gallery tab added and a large `files` table).
- **Deep-pagination latency** at tens of thousands of rows: direction is known (O(OFFSET)) but the user-visible threshold is not (unknown — needs hands-on).

## 9. Scores

| Criterion | Score | Justification |
|---|---|---|
| 1. Zip/archive compatibility | **2** | `resolve_image()` reads `{year}.zip` members directly via `zipfile.ZipFile.open()` (no extraction), matching the per-year-zip steady state; capability fully met (perf caveats are a separate axis). |
| 2. Data/ingest model | **2** | Reads Inkwell's own `files` SQLite table (source of truth) + resolves media from NAS on demand; zero external ingest — the tightest possible fit. |
| 3. Deployment footprint | **2** | No new service/container/DB; Streamlit already a dependency; only adds Pillow (lib) + one SQLite index inside the existing container. |
| 4. Browsable UX for illustrations | **1** | Grid + full-res detail suits single illustrations, but no zoom/lightbox (spec out-of-scope) and Streamlit all-tabs-rerun + O(OFFSET) deep pagination degrade at tens-of-thousands scale. |
| 5. Coupling / Inkwell effort | **2** | In-process, no API/second-deploy; clean internal seam; effort is one module + one migration + one dep. |
| 6. Content-type fit | **2** | Purpose-built for single-image illustrations (the actual content); photos also fine; only weak for sequential comics (not the use case; video/audio explicitly out of scope). |

**Exact deltas required (verified against code):**
- *Dependency:* add `"Pillow"` to `dependencies` in `pyproject.toml` (currently `["streamlit", "apscheduler~=3.10", "bcrypt", "gallery-dl"]`, pyproject.toml:6–11).
- *Migration:* the `downloaded_at` column already exists (src/db.py:106); only the index is missing. Bump `SCHEMA_VERSION` 3→4 (src/db.py:12) and add an `if current_version < 4:` block in `init_schema` (src/db.py:91) with `CREATE INDEX IF NOT EXISTS idx_files_downloaded ON files(downloaded_at DESC)` + `PRAGMA user_version = 4`. The migration framework supports per-version `if current_version < N` blocks (verified: src/db.py:93 + :96). Consider `(artist_id, downloaded_at DESC)` instead (inferred).
- *Query helper:* extend `get_recent_files` or add a gallery query supporting multi-select year/site (join `artists.site`) + offset pagination (src/db.py:392–410 lacks these today).
