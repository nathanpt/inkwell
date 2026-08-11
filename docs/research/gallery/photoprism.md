# PhotoPrism — Gallery Candidate Card

Self-hosted, AI-powered photo/video gallery (Go, AGPL Community Edition). Source under
`photoprism/photoprism`; official Docker image `photoprism/photoprism`; docs at
`docs.photoprism.app`. (verified docs: https://docs.photoprism.app/getting-started/)

Evaluated against the verified Inkwell ground truth: steady-state storage is
`{artist}/{year}.zip` per-year archives on an NFS path bind-mounted read-only at
`/nas/inkwell/`; the `files` table tracks loose files only transiently; `gallery-dl` is a
subprocess and `archive.db` is owned by it; production is Docker Compose.

---

## 1. Storage handling

PhotoPrism indexes **loose files** from a configured `originals` folder. The supported file
format table lists `ZIP` as an "Archive" type (`.zip`), but PhotoPrism does **not** auto-extract
the image contents of an arbitrary `.zip` during indexing — the archive is treated as a single
indexable document, and `.zip` is also the container PhotoPrism *writes* when you download an
album/collection. (verified docs: https://docs.photoprism.app/developer-guide/media/) /
(verified docs: https://www.photoprism.app/kb/file-formats/) /
(verified docs: https://docs.photoprism.app/user-guide/settings/library/ — "ZIP archives when
downloading full collections")

The indexer scans `/photoprism/originals`, reads EXIF/XMP/proprietary metadata, and generates
JPEG/PNG **preview sidecars** + thumbnails in a separate `storage` folder; originals are left
untouched. (verified docs: https://docs.photoprism.app/user-guide/library/originals/) /
(verified docs: https://docs.photoprism.app/getting-started/faq/ — "a JPEG or PNG sidecar file
is automatically created for videos and images in other formats")

**Read-only originals are supported**: `PHOTOPRISM_READONLY=true` disables WebDAV upload/delete
and file import (both require write access to `originals`), but indexing still runs, so a
read-only NFS mount of `/nas/inkwell/` as `originals` is a valid configuration.
(verified docs: https://docs.photoprism.app/getting-started/docker-compose/ — read-only mode) /
(verified docs: https://docs.photoprism.app/user-guide/library/import/ — "Import is not possible
in read-only mode because it requires write permissions to the folder of originals")

**Consequence for Inkwell:** in steady state PhotoPrism would *see* `{artist}/{year}.zip`
files, not the illustrations inside them. To surface the images, Inkwell would have to keep
loose files (contradicts the verified `zip_year_dir` storage model) or add an extraction step
that unpacks each year zip into a separate loose-file tree PhotoPrism scans. (inferred: docs
describe only loose-file indexing/import; no archive-extraction-on-ingest feature is documented)

Two NFS-relevant caveats from the official docs:
- "Never store database files on … a shared network folder" — the DB/cache (`storage`) must live
  on local SSD, not on the NFS originals mount. (verified docs:
  https://docs.photoprism.app/getting-started/docker-compose/ — Database section)
- The free-storage safety threshold is disabled by default precisely because "the underlying
  probe cannot reliably report free space on some filesystems — for example network mounts, FUSE
  layers, and container overlays." (verified docs:
  https://docs.photoprism.app/user-guide/library/originals/ — Free Storage Threshold)

## 2. Data/ingest model

**Filesystem scan**, with optional WebDAV upload and a browser/web upload that imports on
upload. Two modes: (a) *index* — scan `originals` in place, names unchanged; (b) *import* —
copy/move from an `import` folder into `originals`, assigning canonical names sorted by
year/month. (verified docs: https://docs.photoprism.app/user-guide/library/originals/) /
(verified docs: https://docs.photoprism.app/user-guide/library/import/) /
(verified docs: https://docs.photoprism.app/user-guide/library/)

Auto-rescan can be triggered on a 5-minute safety delay after `originals` change via WebDAV
(`PHOTOPRISM_AUTO_INDEX`), and scheduled rescans are supported (`PHOTOPRISM_*` schedule config)
since release 240523. (verified docs:
https://docs.photoprism.app/user-guide/library/originals/ — Scheduled and Automatic Indexing)

Fit to Inkwell's "write to NAS, gallery reads" flow is **strong in shape**: the gallery owns no
ingest API contract with Inkwell — it just scans a mounted root. But because Inkwell's steady
state is zipped, the scan would index `.zip` documents rather than illustrations unless an
extraction sidecar is added. (inferred: scan sees whatever loose files exist in `originals`)

## 3. Deployment footprint

- **Runtime:** single Go binary in the official `photoprism/photoprism` Docker image, plus a
  **MariaDB** service (recommended for concurrency); SQLite is the bundled fallback (capped to 4
  index workers). (verified docs:
  https://docs.photoprism.app/getting-started/docker-compose/ — Database section) /
  (verified docs: https://docs.photoprism.app/getting-started/advanced/databases)
- **Compose shape:** typically **two services** (photoprism + mariadb) with bind mounts for
  `originals`, `storage` (local SSD), and DB data. (verified docs:
  https://docs.photoprism.app/getting-started/docker-compose/ — Volumes section)
- **Resources:** minimum 2 cores + 3 GB RAM; 4 GB recommended; "amount of RAM should match the
  number of CPU cores"; 4 GB swap recommended; local SSD for DB/cache. Indexing large/RAW files
  and panoramas may exceed the minimum. (verified docs:
  https://docs.photoprism.app/getting-started/ — system requirements) /
  (verified docs: https://docs.photoprism.app/getting-started/docker-compose/ — Troubleshooting)
- **Heavy features:** TensorFlow image classification, face recognition, and on-demand video
  transcoding (FFmpeg) add CPU/RAM during indexing. Disable via feature flags if resources are
  tight. (verified docs: https://www.photoprism.app/features/) /
  (verified docs: https://docs.photoprism.app/getting-started/docker-compose/ — Troubleshooting)
- **Maintenance:** image upgrades + `MARIADB_AUTO_UPGRADE` schema bumps; periodic rescan after
  major releases recommended. (verified docs:
  https://docs.photoprism.app/user-guide/library/originals/ — When should "Complete Rescan")

Medium footprint for an N100-class host: a real second service (MariaDB), several GB of RAM, and
local-SSD storage for cache/DB/thumbnails on top of the read-only NFS originals.
(inferred: resource math vs. the documented minimums)

## 4. Browsable UX

- **Views:** grid *Browse* (filterable), *Mosaic*, *Cards*, *List*, plus *Folders*, *Albums*,
  *Calendar*, *Places* (6 map styles), *Labels*, *People*. (verified docs:
  https://www.photoprism.app/features/) / (verified docs:
  https://docs.photoprism.app/user-guide/library/originals/ — "Keep Folder Structure")
- **Search:** combinable filters — label, location, resolution, color, chroma, quality, camera,
  artist, keywords, date. (verified docs: https://www.photoprism.app/features/ — Powerful Search)
- **Viewer:** full-screen image viewer serves originals/full-res (max 900 MP CE/Plus), with
  preview sidecars generated for non-JPEG/PNG. (verified docs:
  https://www.photoprism.app/features/ — compare table "Maximum Resolution") / (verified docs:
  https://docs.photoprism.app/user-guide/settings/library/ — Generate Previews)
- **Deep zoom:** no tiled/DeepZoom (OpenSeadragon-style) viewer is documented; the viewer shows
  the full-resolution image, which is workable for large illustrations but not a pan/zoom reader.
  (unknown — needs hands-on: confirm whether the full-screen viewer offers zoom/pan on very
  large illustrations and what the payload size is over a read-only NFS originals mount)

For a large set of **unrelated single illustrations**, the folder/album grid is usable, but the
value-add metadata is photo-centric (faces, places, camera EXIF, "quality" review) and largely
irrelevant or noisy for illustrations. (inferred: features page lists photo/video/RAW workflows;
no illustration/manga concept)

## 5. Coupling / Inkwell effort

**Clean seam in principle** — Inkwell keeps writing to the NAS; PhotoPrism mounts
`/nas/inkwell/` read-only as `originals` and scans it. Inkwell would need only a compose service
entry and (optionally) a config-loader hook analogous to the prior Komga spike. (inferred: no
API contract required; read-only index mode is documented)

**But the zip model forces real coupling:** because PhotoPrism won't read images out of
`{artist}/{year}.zip`, one of these is required:
- change Inkwell's storage to retain loose files (contradicts verified `zip_year_dir` behavior),
- add an out-of-band extractor that unpacks year zips into a separate loose-file tree PhotoPrism
  scans (extra Inkwell code + disk + a place to write it, since the NAS mount is read-only), or
- intercept between download and `on_job_complete` to also keep a loose copy for browsing.

So the minimal-config dream is undermined by the storage-model mismatch. (inferred from the
storage-handling analysis above)

No shared schema coupling: PhotoPrism owns its own DB (SQLite/MariaDB) and never touches
Inkwell's `files` table or `archive.db`. (verified docs:
https://docs.photoprism.app/getting-started/docker-compose/ — Database section)

## 6. Dedup / idempotency story

Exact duplicates are detected by **SHA-1 checksum + file size** and skipped during indexing
(hide by default in search) and import ("Move" also deletes the source copy).
(verified docs: https://docs.photoprism.app/user-guide/library/duplicates/) / (verified docs:
https://www.photoprism.app/features/ — Duplicate Detection)

Re-indexing unchanged files is idempotent (manual metadata like labels/people/titles is
preserved across rescans). "Complete Rescan" re-indexes even unchanged files when needed.
(verified docs: https://docs.photoprism.app/user-guide/library/originals/ — Complete Rescan)

Related files (raw+jpg+xmp, jpg+json, live photos) are **stacked** by same-name / unique-ID /
place+time rather than deduped. (verified docs:
https://docs.photoprism.app/user-guide/settings/library/ — Stacks)

Dedup is exact-hash only; near-duplicates (resaved/re-encoded illustrations with different
bytes) are not merged. (inferred: "exact duplicates … SHA1 checksums and sizes")

## 7. Alignment with codebase ground-truth constraints

| Ground-truth constraint | PhotoPrism alignment |
|---|---|
| Steady state = `{artist}/{year}.zip` | **Mismatch.** Indexes loose files; will not read images out of the year zips. Needs extraction or a storage-model change. (inferred) |
| NFS originals read-only at `/nas/inkwell/` | **OK.** `PHOTOPRISM_READONLY=true` scans a read-only mount. DB/cache/`storage` must be local SSD (docs warn against DB on network folders). (verified docs: docker-compose) |
| Inkwell writes to NAS, gallery reads | **OK in shape** — filesystem scan, no API push required. But scan sees zips, not illustrations. (inferred) |
| `files` table / `archive.db` untouched | **OK.** PhotoPrism uses its own DB; no coupling to Inkwell schema or `gallery-dl`'s `archive.db`. (verified docs: databases) |
| Compose deployment, separate server | **OK.** Official `photoprism/photoprism` image + MariaDB compose stack; shares the NAS path read-only as `originals`. (verified docs: docker-compose) |
| Pillow absent / Streamlit stack | **N/A** — PhotoPrism is independent; adds Go + MariaDB, not Python deps. |

## 8. Open questions / what is NOT verifiable from docs (hands-on needed)

- Does the full-screen viewer offer true pan/zoom (DeepZoom) for very large illustrations, or
  only fit-to-screen full-res? — affects Browsable UX score. (unknown — needs hands-on)
- Does indexing a directory of `.zip` files actually surface *any* of the inner images, or only
  index the zip as a single document? Docs list ZIP as a format but describe no extraction-on-
  ingest; needs a hands-on test to confirm zip contents are not browsable. (unknown — needs
  hands-on: drop a `{artist}/{year}.zip` into `originals`, index, observe whether inner images
  appear)
- Real RAM/CPU cost of TensorFlow classification + face recognition on a large illustration set
  (tens of thousands of single images) on N100-class hardware, and whether disabling them
  degrades the browse experience. (unknown — needs hands-on)
- Performance of thumbnail/preview generation when `originals` is an NFS mount (docs only warn
  about the DB on network storage, not the read path for originals). (unknown — needs hands-on)
- Whether the AI label classifier produces useful or noisy labels for illustrations (it is
  photo-trained). (unknown — needs hands-on)

## 9. Scores

| Criterion | Score | One-line justification |
|---|---|---|
| 1. Zip/archive compatibility | **0** | Indexes loose files only; `{artist}/{year}.zip` is treated as a document, not auto-extracted — steady-state zips are not browsable. (verified docs: developer-guide/media, library/originals) |
| 2. Data/ingest model | **2** | Pure filesystem scan of a mounted root + optional WebDAV/upload, with read-only originals support — an exact match for Inkwell's "write to NAS, gallery reads" shape. (verified docs: library/originals, library/import, docker-compose read-only mode) |
| 3. Deployment footprint | **1** | Go single container is lean, but prod wants a second MariaDB service, several GB RAM, local-SSD cache, and TF/face-recognition load — a real stack on top of Inkwell. (verified docs: getting-started, docker-compose) |
| 4. Browsable UX for illustrations | **1** | Strong grid/search/album/folder browsing and full-res viewer, but no deep-zoom reader and the metadata value-add (faces/places/camera/quality) is photo-oriented and weak for illustrations. (verified docs: features; ? on deep-zoom in Open questions) |
| 5. Coupling / Inkwell effort | **1** | No API/schema coupling, but the zip storage model forces an extraction pipeline or a storage-model change before PhotoPrism can see the illustrations. (inferred from storage analysis) |
| 6. Content-type fit | **1** | Photo/video/RAW/Live-Photo product; illustrations aren't first-class, AI tagging is photo-trained, and there is no sequential/comic reading model. (verified docs: features, file-formats) |

**Total: 6 / 12.** Strongest on the ingest/scan model and independence from Inkwell's schema;
weakest on the hard blocker that Inkwell's steady-state is per-year zips, which PhotoPrism will
not read in place.
