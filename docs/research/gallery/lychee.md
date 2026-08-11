# Lychee — Research Card

**Candidate:** Lychee (LycheeOrg/Lychee) — self-hosted PHP photo-management gallery.
**Sources:** official docs `lycheeorg.dev/docs/`, GitHub `github.com/LycheeOrg/Lychee` (source read directly), official Docker image `ghcr.io/lycheeorg/lychee`.

---

## 1. Storage handling

Lychee **does not read media out of `.zip` archives in steady state.** The filesystem-sync import path (`php artisan lychee:sync`) walks a directory tree and treats *individual loose image files* as photos; any file whose extension is not in the supported list is rejected (verified docs: source `app/Actions/Import/Exec.php` `doFiles()` → `isSupportedOrAcceptedFileExtension()`, and `app/Services/Image/FileExtensionService.php`, which enumerates supported image extensions as `.jpg .jpeg .png .gif .webp .avif .heic .heif` plus video extensions — `.zip` is **not** among them) (verified docs: https://github.com/LycheeOrg/Lychee/blob/master/app/Services/Image/FileExtensionService.php).

`.zip` is only a **transport container for the web upload path**, not a library format: a ZIP uploaded via the web UI / `uploads/import` is extracted once and its images are ingested as loose photos, after which the ZIP is discarded (inferred: this is a one-shot upload-time extraction, confirmed by the settings doc note that a title-cleaning setting "Drop file extensions from titles inside ZIP archives, preventing double extensions like `image.jpg.jpg`" applies to ZIP *imports*, not browsing — verified docs: https://lycheeorg.dev/docs/getting-started/settings/). There is no "mount a directory of `.zip` files and serve their contents" mode.

**Implication for Inkwell:** Inkwell's steady-state storage is per-year archives (`{artist}/{year}.zip`, per `src/zipper.py` `zip_year_dir`). Lychee cannot read those. The NAS would have to be re-extracted to loose images (duplicating storage and breaking Inkwell's archive model) before Lychee could ingest anything.

## 2. Data/ingest model

**Filesystem scan (pull) is supported**, via two paths, both reading *loose files*:

- Web UI **"Import from Server"** and the `import_via_symlink` setting ("Create symbolic links instead of copying files during *Import from Server*", verified docs: https://lycheeorg.dev/docs/getting-started/settings/). With `import_via_symlink=1` and the "Symbolic links" flag, Lychee indexes existing files without copying (verified docs: issue context https://github.com/LycheeOrg/Lychee/issues/1139 — bind-mount a host folder into `/pictures` and enable symlinks).
- CLI **`php artisan lychee:sync <paths>`** — "Directories are imported as albums; individual files are imported directly" (verified docs: https://github.com/LycheeOrg/Lychee/blob/master/app/Console/Commands/Sync.php). Supports `--skip_duplicates`, `--delete_imported`, `--import_via_symlink`, `--delete_missing_photos`, `--delete_missing_albums`, `--dry_run`.

There is **no API-push ingest** of the "Inkwell writes a file, calls a webhook" variety; the REST API (`POST /api/Albums::import`, `import_photo_from_url`) exists but still drives the same import-from-server/URL machinery (verified docs: https://chostakovitch.github.io/pychee/pychee.html — `import_photo_from_url(url, album_id)`).

**Fit to Inkwell's "write to NAS, gallery reads" flow:** Partial. Lychee *can* scan a read-only root mounted at `/nas/inkwell/` (read-only is fine for `import_via_symlink`, which doesn't move originals), but it only indexes **loose files**, never the `{artist}/{year}.zip` archives. Inkwell would have to either (a) extract every year zip to a parallel loose-file tree for Lychee, or (b) abandon per-year zips — both fight Inkwell's storage model. Sync is also a manual/scheduled action, not an auto-watch: the docs FAQ notes users ask to "watch a folder for new images" and the answer is the mass-import CLI command, i.e. there is no filesystem watcher (verified docs: https://lycheeorg.dev/docs/faq_general.html via search result summary — "Lychee supports mass import of photos via the command line").

## 3. Deployment footprint

- **Runtime:** PHP 8.4 / 8.5 (README badge "PHP 8.4 & 8.5") served by **FrankenPHP** in the official image ("Lychee with FrankenPHP (the modern, high-performance runtime)", verified docs: https://lycheeorg.dev/docs/getting-started/installation/) (verified docs: https://github.com/LycheeOrg/Lychee).
- **Database:** MySQL/MariaDB (default in the compose template, `mariadb:11`), PostgreSQL (`DB_CONNECTION: pgsql`), or SQLite (`DB_CONNECTION: sqlite`). No Redis/cache dependency in the default compose; background jobs run via a separate **worker container** ("This setup includes a separate worker container for background jobs", verified docs: https://lycheeorg.dev/docs/getting-started/installation/).
- **Image:** `ghcr.io/lycheeorg/lychee:latest` (official) or `lscr.io/linuxserver/lychee` (verified docs: https://github.com/LycheeOrg/Lychee).
- **Resources/maintenance:** Lightweight PHP app (a single web container + DB + optional worker). Far lighter than JVM/comic servers or PostgreSQL+Redis photo stacks, though heavier than a pure-Python tab inside Inkwell. Laravel framework + PHP release cadence is the ongoing maintenance surface; v7.0 carried breaking Docker changes ("Version 7.0 introduces significant changes on the docker image," verified docs: https://github.com/LycheeOrg/Lychee).

Footprint is a **strength** for Lychee — it is one of the lighter external options.

## 4. Browsable UX

- **Grid/album layouts:** Configurable album photo layouts: `square | justified | masonry | grid` (default `justified`) — "justified" preserves aspect ratio with flush edges; "masonry" is Pinterest-style columns; "grid" is uncropped fixed-width columns (verified docs: https://lycheeorg.dev/docs/getting-started/settings/).
- **Albums:** Hierarchical albums (directory → album, subdirectory → sub-album via sync), plus tag albums and smart/flow/timeline home pages (`home_page_default: gallery|flow|timeline`, verified docs: https://lycheeorg.dev/docs/getting-started/settings/).
- **Full-res / zoom:** `grants_full_photo_access` (default `1`) controls access to the original full-resolution file (verified docs: https://lycheeorg.dev/docs/getting-started/settings/). Lychee generates size variants (small/medium/big/original) and serves the big/full variant in the single-photo lightbox.
- **Zoom:** Lychee's lightbox offers keyboard/mouse navigation and pan of large images, but **deep zoom (tiled / pyramid / IIIF-style) is not documented as a core feature** (unknown — needs hands-on: whether the lightbox has true multi-level deep zoom vs. simple fit-to-window pan/scroll on the `big` variant).

For a large set of **unrelated single illustrations** (not sequential comics), Lychee's album/grid/justified model is a good match — better than comic servers' page-sequence model. It lacks the comic/manga reading flow, which is irrelevant here.

## 5. Coupling / Inkwell effort

- **Cleanest seam:** Inkwell would point Lychee's `lychee:sync` (or web "Import from Server" with `import_via_symlink`) at the read-only `/nas/inkwell/` mount. Inkwell needs **no code changes** for this — just an additional compose service mounting the NAS path read-only, plus a scheduled/cron `lychee:sync` invocation.
- **BUT:** this only works if the media is loose files. Because Inkwell stores per-year `.zip`s, the real coupling cost is the **extraction bridge**: Inkwell would need to maintain a parallel loose-image tree (or extract-on-the-fly cache) that Lychee can scan, which is non-trivial and duplicates storage. Without that bridge, Lychee sees empty directories.
- No Inkwell-side dependency is added (unlike the in-app Streamlit tab adding Pillow); Lychee is fully external.

## 6. Dedup / idempotency story

Strong and source-verified. Lychee stores per-photo checksums and dedups on import:

- `skip_duplicates` (default `0`) — "Skip photos that already exist in the gallery during import (duplicate detection by checksum)" (verified docs: https://lycheeorg.dev/docs/getting-started/settings/).
- Mechanism: `FindDuplicate` pipe computes `StreamStat::createFromLocalFile(...)->checksum` and queries `photos` for matching `checksum`, `original_checksum`, or `live_photo_checksum` (verified docs: https://github.com/LycheeOrg/Lychee/blob/master/app/Actions/Photo/Pipes/Init/FindDuplicate.php).
- `skip_duplicates_early` (default `1`) — "Skip duplicates early during `sync` imports by checking photo titles in the target album (faster than checksum-based detection)" (verified docs: https://lycheeorg.dev/docs/getting-started/settings/); implemented in `ImportPhotos::filterExistingPhotos()` which checks basename/filename (and renamed variants) against the album (verified docs: https://github.com/LycheeOrg/Lychee/blob/master/app/Actions/Import/Pipes/ImportPhotos.php).
- `lychee:sync` also supports `--delete_missing_photos` / `--delete_missing_albums` / `--dry_run` for two-way reconciliation (verified docs: https://github.com/LycheeOrg/Lychee/blob/master/app/Console/Commands/Sync.php).

This makes re-running sync idempotent: re-scanning the same loose-image tree will not duplicate photos.

## 7. Alignment with codebase ground-truth constraints

| Ground-truth constraint | Lychee alignment |
|---|---|
| Steady-state storage = per-year `{artist}/{year}.zip` archives (`src/zipper.py`) | **Misaligned.** Lychee ingests loose image files only; it does not read `.zip` as a library format. Requires an extraction bridge. |
| `files` table tracks `job_id, artist_id, filename, year, size_bytes, downloaded_at` | Lychee has its own `photos`/`albums` schema; Inkwell's `files` table is unused by Lychee (gallery reads filesystem, not Inkwell's DB). No coupling either way. |
| `gallery-dl` is a subprocess, `archive.db` owned by gallery-dl | Unaffected — Lychee has no relationship to gallery-dl's archive. |
| NAS mounted read-only at `/nas/inkwell/` | Compatible for read-only scanning (`import_via_symlink` needs no writes to originals; sync reads only). But Lychee's own uploads/DB must live on writable storage outside the read-only mount. |
| Pillow absent; deps minimal | Lychee adds no Python dependency (external PHP service). |
| Production = Docker Compose; external server = additional compose service sharing NAS read-only | Cleanly fits as an extra compose service (web + mariadb + worker). |

**Bottom line:** Lychee fits the deployment and read-only-NAS constraints well, but is fundamentally incompatible with Inkwell's per-year-zip steady state without a bridging extraction layer.

## 8. Open questions / what is NOT verifiable from docs

- **Deep zoom in the lightbox:** Whether Lychee's single-photo viewer offers true multi-level deep zoom (tiled/pyramid) or only fit-to-window pan of the `big` variant. Docs describe `grants_full_photo_access` and size variants but not a deep-zoom engine. → Score below uses `?` only where a capability is genuinely ambiguous; deep zoom specifically is unconfirmed (unknown — needs hands-on: inspect the lightbox on a very large illustration for tile-based zoom behavior).
- **Read-only NAS + symlink import behavior under NFS:** `import_via_symlink` is documented to create symlinks *inside* Lychee's uploads dir pointing at originals; whether this works cleanly when originals live on a read-only NFS bind-mount (permissions, stale-link handling on sync re-runs) is not documented. (unknown — needs hands-on: run `lychee:sync --import_via_symlink=1` against an NFS read-only mount.)
- **Scale with tens of thousands of illustrations:** Lychee is built on Laravel/MySQL and paginates, but real-world performance for very large single-album illustration collections (vs. typical photo albums) over NFS is not documented. (unknown — needs hands-on: load-test a large album.)
- **No filesystem watcher:** Confirmed from docs/FAQ that import is a manual/CLI/cron action, not an auto-watch — not ambiguous, but worth flagging as a fit gap (no live reflection of new Inkwell downloads without a scheduled sync).

## 9. Scores

| Criterion | Score | Justification |
|---|---|---|
| 1. Zip/archive compatibility | **0** | `.zip` is not a supported ingest extension; sync reads loose image files only. A `{artist}/{year}.zip` is invisible to Lychee. (verified docs: `FileExtensionService.php`, `Exec.php`) |
| 2. Data/ingest model | **1** | Filesystem scan (`lychee:sync`, web "Import from Server", `import_via_symlink`) fits "gallery reads the NAS" but only for loose files, and there is no auto-watch (manual/cron sync). |
| 3. Deployment footprint | **2** | Single PHP/FrankenPHP container + MariaDB + optional worker; no Redis; one of the lighter external stacks. (verified docs: installation + settings) |
| 4. Browsable UX for illustrations | **2** | Album grid with square/justified/masonry/grid layouts, full-res access (`grants_full_photo_access`); well-suited to unrelated single images. (Deep-zoom unconfirmed — see Open questions, but core grid/album UX is solid.) |
| 5. Coupling / Inkwell effort | **1** | External service needs no Inkwell code change, but Inkwell's zip steady-state forces an extraction bridge (duplicate loose-image tree) before Lychee can see anything — meaningful indirect coupling. |
| 6. Content-type fit | **2** | Photo/illustration oriented (EXIF/IPTC, size variants, albums) — a natural fit for a large set of single illustrations; no comic-sequencing baggage. |

**Total: 8 / 12** (excluding any `?` — none scored `?` since each criterion was resolvable from source, with residual hands-on items recorded in Open questions.)

**One-line verdict:** Lychee is a light, attractive photo gallery with excellent dedup, but it cannot read Inkwell's per-year `.zip` archives; adopting it requires maintaining a parallel loose-image extraction layer, which undercuts the clean "share NAS read-only" story.
