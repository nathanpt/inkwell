# Immich — Gallery Candidate Card

Self-hosted photo/video gallery with timeline, map, smart (CLIP) search, face recognition, and external-library filesystem scanning.
Candidate slug: `immich`. Evaluated against Inkwell's verified ground truth (per-year `{artist}/{year}.zip` steady-state storage, NFS media root at `/nas/inkwell/`, `files` table, Docker-Compose deployment).

---

## 1. Storage handling

Immich reads **loose media files on disk**, not archives. External Libraries use "import paths" that "are scanned recursively," and "each import file must be a readable directory that exists on the filesystem" (verified docs: https://docs.immich.app/features/libraries). There is no statement anywhere in the core Immich docs that it reads `.zip`/`.cbz`/archive contents in place; the scanner traverses directories of image/video files.

The only zip-handling found is in **`immich-go`**, a *third-party* community CLI (not part of Immich core) whose `from-folder` sub-command "accepts both regular directories and ZIP archives" and **uploads** their contents via the Immich API (verified docs: https://github.com/simulot/immich-go/blob/main/docs/commands/upload.md). That is an API *push* of extracted files, not in-place reading of `{artist}/{year}.zip` as they sit on the NAS.

> Verdict: Immich **cannot read media out of `{artist}/{year}.zip` archives directly in steady state.** It would require either keeping loose files on the NAS (contradicting Inkwell's `zip_year_dir` steady state) or a pre-extract / API-push step. (inferred: core docs describe only loose-file directory scanning)

## 2. Data/ingest model

Both models exist:
- **API push**: mobile apps, web upload, and the official Immich CLI upload assets into Immich-managed storage (verified docs: https://docs.immich.app/FAQ; https://docs.immich.app/features/command-line-interface).
- **Filesystem scan (External Libraries)**: Immich scans mounted import paths and creates assets for files on disk without copying them into its upload location. "When the external library is scanned, Immich will load videos and photos from disk and create the corresponding assets" (verified docs: https://docs.immich.app/features/libraries). Import paths are configured per-library and support glob exclusion patterns. A read-only mount is explicitly supported: the docs show `:ro` bind mounts and note "`ro` … only gives read-only access" (verified docs: https://docs.immich.app/features/libraries).

This fits Inkwell's "write to NAS, gallery reads" flow **only for loose files**. Because Inkwell's steady state is zipped, the natural seam (read-only External Library over `/nas/inkwell/`) does not work without changing Inkwell's storage model or adding an extraction layer.

## 3. Deployment footprint

**Heavy, multi-container stack** — the most resource-intensive candidate in this set.
- Requires **Docker + Docker Compose plugin** (`docker compose`, not deprecated `docker-compose`) (verified docs: https://docs.immich.app/install/requirements).
- Depends on **PostgreSQL** and **Redis** (or Valkey): "an existing Redis and PostgreSQL 14 container" is required (verified docs: https://docs.immich.app/install/unraid). The Postgres data "should ideally use local SSD storage, and never a network share of any kind" and "requires at least 2GB of RAM" under Docker limits (verified docs: https://docs.immich.app/install/requirements).
- Core services: `immich-server`, `immich-machine-learning`, a database container (PostgreSQL), and a Redis/Valkey container. (Older compose samples also split a separate `immich-web`; the web frontend is now served by `immich-server`.) (verified docs: https://docs.immich.app/install/unraid; inferred: service consolidation from requirements/system-settings pages that reference `immich-server` and `immich-machine-learning:3003`).
- **Hardware**: "Minimum 6GB, recommended 8GB" RAM; minimum 2 CPU cores, 4 recommended; amd64/arm64; thumbnails/transcodes add ~10–20% storage (verified docs: https://docs.immich.app/install/requirements). The ML container (CLIP smart search, face detection/duplicate detection) is CPU/RAM-hungry; since `v3` amd64 requires `x86-64-v2` (verified docs: https://docs.immich.app/install/requirements).

Maintenance burden is real: multi-container upgrades, Postgres DB on local SSD (not NFS), Redis, ML model management, and version-matched mobile apps.

## 4. Browsable UX

Strong, modern photo-gallery UX — the best browsing experience among the external candidates for a large set of individual images:
- **Timeline** grid of all assets plus an optional **Folder view** "similar to a file explorer" for navigating library folders (verified docs: https://docs.immich.app/features/libraries).
- **Map** with GPS pins and reverse geocoding (verified docs: https://docs.immich.app/administration/system-settings).
- **Smart search** via CLIP embeddings and metadata search (verified docs: https://docs.immich.app/administration/system-settings; https://docs.immich.app/FAQ).
- **Full-res viewing**: generates thumbnails (small webp), large previews (jpeg/webp, default 1440p), and blurred thumbhash; the asset viewer serves the preview/original. Originals are always preserved (verified docs: https://docs.immich.app/administration/system-settings; https://docs.immich.app/FAQ).
- Zoom exists in the asset viewer (the FAQ describes mouse-wheel zoom when setting a profile picture) (verified docs: https://docs.immich.app/FAQ) — `(unknown — needs hands-on: depth/quality of zoom for very large illustrations, e.g. pan/zoom to 1:1)`.

Excellent for browsing large numbers of unrelated single images. No sequential/comic reading mode (page-by-page), but that is not required for illustrations.

## 5. Coupling / Inkwell effort

The cleanest seam is a **read-only External Library mount** of the NAS root: no Inkwell code changes are strictly required to *expose* the data, and Immich treats it as a normal library (verified docs: https://docs.immich.app/features/libraries).

However, this seam **breaks against Inkwell's zip steady state**: Immich scans loose files, so Inkwell would have to either (a) stop zipping per year and keep loose files under `{artist}/{year}/`, (b) maintain a parallel loose-file mirror for Immich, or (c) extract zips before/for Immich. Each option either changes Inkwell's storage contract (`src/zipper.py` `zip_year_dir`) or duplicates storage. There is no API hook Immich exposes for "here is a zip, index its entries."

So: **integration seam is clean in protocol (read-only mount) but misaligned in data shape.** Real effort is on the Inkwell side to make loose files available, or to accept a second copy of the media. (inferred: from the absence of any archive-reading API/feature in core docs)

## 6. Dedup / idempotency story

- **Upload libraries**: dedup by file hash, **per library** ("Duplicate checking only exists for upload libraries, using the file hash. Furthermore, duplicate checking is not global, but per library") (verified docs: https://docs.immich.app/FAQ).
- **External libraries**: **no upload-hash dedup** — "a situation where the same file appears twice in the timeline is possible, especially for external libraries" (verified docs: https://docs.immich.app/FAQ).
- **Separate Duplicate Detection utility**: ML-based (CLIP embeddings) visual-similarity grouping, enabled by default, surfaced in a "Review duplicates" page; configurable max detection distance (verified docs: https://docs.immich.app/features/duplicates-utility; https://docs.immich.app/administration/system-settings). This is *visual* near-duplicate detection, not exact-hash idempotency, and it relies on the ML container.

For Inkwell's External-Library path there is therefore **no built-in idempotency guarantee**; rescans are idempotent by path (a file in an import path is added once; removal from disk moves it to trash) (verified docs: https://docs.immich.app/features/libraries), but identical files in multiple paths/libraries can coexist.

## 7. Alignment with codebase ground-truth constraints

- **Per-year `{artist}/{year}.zip` steady state — MISALIGNED.** Immich reads loose files; it does not index archive contents. This is the single biggest mismatch and is the reason zip-compat scores 0. (verified docs: https://docs.immich.app/features/libraries)
- **NFS media root (`/nas/inkwell/` read-only) — PARTIALLY ALIGNED.** A read-only External Library mount is supported, but (a) automatic file-watching "likely won't work" over a network drive, forcing periodic cron scans (verified docs: https://docs.immich.app/features/libraries); (b) the **Postgres DB must be on local SSD, never a network share** (verified docs: https://docs.immich.app/install/requirements), so Immich brings its own stateful DB that cannot live on the NAS.
- **`files` table / Inkwell DB — ORTHOGONAL.** Immich has its own Postgres asset DB and does not read Inkwell's SQLite `files` table; no shared schema. Inkwell's missing `idx_files_downloaded` is irrelevant to Immich.
- **Pillow absent from Inkwell — IRRELEVANT.** Immich generates its own thumbnails/previews; Inkwell would not add Pillow for Immich's sake.
- **Docker-Compose deployment — ALIGNED in mechanism.** Adding Immich as compose services alongside Inkwell is straightforward *mechanically*, but it adds 4+ containers and ≥6GB RAM to the host (verified docs: https://docs.immich.app/install/requirements).
- **`gallery-dl`/`archive.db` ownership — UNAFFECTED.** Immich does not touch `gallery-dl` or `archive.db`.

## 8. Open questions / what is NOT verifiable from docs (hands-on needed)

- **Archive ingestion**: Is there *any* path (FUSE/avfs-style transparent archive mount, plugin) to make External Libraries see inside `{artist}/{year}.zip`? Core docs say no; hands-on would confirm whether a FUSE-expanded view is scannable and stable. (drives the `?`-adjacent zip score — scored 0, not `?`, because docs are explicit that scanning is of directories/files.)
- **NFS periodic-scan performance**: How does a scheduled External Library scan perform over a read-only NFS mount containing tens of thousands of illustration files (scan duration, thumbnail-generation load on the ML/microservices container)? (unknown — needs hands-on)
- **Network-drive file-watcher**: Docs say inotify watching "likely won't work" on network drives (verified docs: https://docs.immich.app/features/libraries); hands-on could confirm whether periodic cron scanning alone is acceptable latency for Inkwell's post-zip updates. (unknown — needs hands-on)
- **Illustration zoom quality**: Depth/quality of pan/zoom to 1:1 for very large illustrations in the asset viewer. (unknown — needs hands-on)
- **ML value for illustrations**: Whether CLIP smart search / face detection produce useful results for non-photo illustrations, and the CPU cost of running those jobs at scale. (unknown — needs hands-on)

## 9. Scores

| # | Criterion | Score | Justification |
|---|-----------|-------|---------------|
| 1 | Zip/archive compatibility | **0** | Reads loose files via directory scan only; no core support for reading `{artist}/{year}.zip` in place (zip ingest exists only via third-party `immich-go` API push). (verified docs: https://docs.immich.app/features/libraries) |
| 2 | Data/ingest model | **1** | Supports both API push and filesystem-scan (External Libraries) — the scan model matches "write NAS, gallery reads," but only for loose files, which conflicts with Inkwell's zip steady state. (verified docs: https://docs.immich.app/features/libraries) |
| 3 | Deployment footprint | **0** | Heaviest option: multi-container (server + machine-learning + PostgreSQL + Redis/Valkey), ≥6GB RAM min, Postgres on local SSD, ML/CPU-heavy. (verified docs: https://docs.immich.app/install/requirements) |
| 4 | Browsable UX for illustrations | **2** | Timeline grid, folder view, map, CLIP smart search, full-res previews/thumbnails — excellent for large sets of unrelated single images. (verified docs: https://docs.immich.app/features/libraries; https://docs.immich.app/administration/system-settings) |
| 5 | Coupling / Inkwell effort | **1** | Read-only External Library mount is a clean protocol seam, but Immich's loose-file requirement forces an Inkwell storage-model change or a parallel loose copy. (inferred: absence of archive-reading in core docs) |
| 6 | Content-type fit | **1** | Photo-first; illustrations work as individual images and the grid UX suits them, but ML features (faces, smart search) are tuned for photos and there are no album/comic semantics. (verified docs: https://docs.immich.app/features/duplicates-utility; https://docs.immich.app/administration/system-settings) |

**Total: 5 / 12.** Strongest on UX, weakest on storage compatibility and footprint. The zip-steady-state mismatch is disqualifying unless Inkwell changes how it stores media or maintains a loose-file mirror for Immich.
