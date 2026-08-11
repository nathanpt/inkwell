# Komga — research card

Candidate: **Komga** (gotson/komga) — a self-hosted media server for comics, manga, BDs, magazines and eBooks.
Official docs: https://komga.org/docs/introduction · GitHub: https://github.com/gotson/komga · Docker: https://hub.docker.com/r/gotson/komga

Inkwell ground truth this card is evaluated against: steady-state storage is per-year zips at `{nas_mount}/{artist}/{year}.zip` (loose files exist only transiently until `on_job_complete`); NAS is bind-mounted NFS, read-only at `/nas/inkwell/`; `archive.db` is owned by `gallery-dl` (Inkwell never touches it); production is Docker Compose.

---

## 1. Storage handling

Komga natively reads archives and never needs loose files. Supported library file types are `cbz`, `zip`, `cbr`, `rar`, `pdf`, `epub` (verified docs: https://komga.org/docs/guides/libraries#file-types — "Comic Book archives: cbz, zip, cbr, rar"). The scanner can be restricted to any subset of these; `zip` is a first-class type. (verified docs: https://komga.org/docs/introduction — "Comic book archives: CBZ and CBR".)

**Loose image files (jpg/png as standalone library items) are NOT supported.** The supported-types list contains only archives + pdf + epub — no `jpg/png/gif/webp` extensions appear in the scanner's file-type list. The separate "Image Types" guide describes image *formats Komga can decode inside a book* (JPEG, PNG, GIF, WebP, plus JPEG XL/AVIF/HEIF on some platforms), not standalone ingestable files (verified docs: https://komga.org/docs/guides/image-formats). *(A web-search summary claimed Komga serves loose JPG/PNG directly; that is not corroborated by the official file-type list and is treated as incorrect here.)*

**Fit to Inkwell:** this is an excellent match for steady state. Inkwell's `{artist}/{year}.zip` is exactly a `zip` archive Komga reads in place — no unzip, no API push. The transient loose-file window (before `on_job_complete` zips) is invisible to Komga because loose images aren't a supported type; by the next scan the files are already a `zip` (inferred: Inkwell writes the zip on job completion, before any scheduled Komga scan).

## 2. Data/ingest model

**Filesystem scan, no API push.** A Komga library is a root folder; the scanner walks folders/sub-folders and builds a library representation: one *Series* per sub-folder (any depth), one *Book* per archive file, placed in the Series of its parent folder (verified docs: https://komga.org/docs/guides/scan-analysis-refresh#what-happens-during-a-scan).

Scanning options: **Scan on startup**, and a **Scan interval** (`disabled / hourly / every 6 hours / every 12 hours / daily / weekly`) (verified docs: https://komga.org/docs/guides/libraries#scan-interval). There is **no live file-watch / inotify**; new files surface only on the next scheduled or manual "Scan library files" (inferred from the absence of any watch option and the scan-interval/scan-on-startup/deep-scan model). Deep scan forces a full re-compare when filesystems don't update parent-folder mtime reliably.

**Fit to "write to NAS, gallery reads":** near-perfect. Inkwell writes `{artist}/{year}.zip`; Komga scans `/data` and turns `{artist}` → Series, `{year}.zip` → Book. No Inkwell push code, no coupling to Inkwell's DB. Only cost: latency up to one scan-interval before a new zip appears (acceptable; can set hourly).

## 3. Deployment footprint

**Single self-contained JVM container.** Official image `gotson/komga` (also `ghcr.io/gotson/komga`) bundles its own JDK; run with `--user 1000:1000`, port `25600`, bind-mount `/config` (database + Komga config) and `/data` (media) (verified docs: https://komga.org/docs/installation/docker). State is a **local SQLite DB** in `/config` — and the FAQ explicitly warns *"Always use a local filesystem for the /config folder. Do not use any network share like CIFS or NFS"* (verified docs: https://komga.org/docs/installation/docker#parameters and https://komga.org/docs/faq). So the DB must live on the Inkwell host's local disk; only the media path is the read-only NFS mount — compatible with Inkwell's topology.

**Resources:** runs on the JVM; default heap ~¼ of physical RAM, capped via `JAVA_TOOL_OPTIONS=-Xmx<limit>` (e.g. `-Xmx2g`) (verified docs: https://komga.org/docs/installation/docker#increase-memory-limit, https://komga.org/docs/faq#the-memory-consumption-is-huge). Practical floor ~512 MiB RAM, 1 vCPU; recommended ~1 GiB / 2 vCPU (verified docs: https://railway.com/deploy/komga-open-source-comic-and-manga-server--komga — third-party host summary citing project guidance). Analysis/hashing is the resource-intensive phase and "can consume lots of resources on large libraries or slow hardware" (verified docs: https://komga.org/docs/guides/libraries#compute-hash-for-files).

**Maintenance:** actively maintained, multi-arch image (amd64/arm64/arm), one container, no external DB/Redis/cache. Lightest class of external option. Adds one compose service to the production stack.

## 4. Browsable UX

**Browse:** grid of Series and Book cover cards in the web UI, filterable/searchable (inferred from "Browse series, filter, and search" model described across guides; verified docs: https://komga.org/docs/guides/yomu for the browse/filter model on a client, and the Series/Book card model).

**Reader (DIVINA/webreader):** four reading modes — *Left to right, Right to left, Vertical, Webtoon* (continuous vertical strip) — and four **scale types**: *Fit to screen, Fit to width, Fit to height, Original* (full-res). A **Thumbnails explorer** gives a grid overview of all pages for quick navigation (verified docs: https://komga.org/docs/guides/webreader-divina).

**Limitations for illustrations:** there is **no arbitrary/deep zoom** — only the four fixed scale types; an open feature request exists for free zoom (verified docs: https://github.com/gotson/komga/discussions/1596). The reader is fundamentally **page-sequential** (paged or webtoon strip), optimized for reading a comic in order. For a "book" that is really a year-zip of *unrelated* single illustrations, the Thumbnails explorer gives a usable grid, and "Original" gives full-res, but there is no gallery/album UX (no masonry grid of unrelated images, no pan/zoom-into-detail).

## 5. Coupling / Inkwell effort

**Effectively zero Inkwell code.** The seam is pure filesystem: mount the existing read-only NAS path into Komga's `/data`, create one library rooted there, enable an hourly scan. No Inkwell API, no DB bridge, no `gallery-dl`/`archive.db` involvement (Komga owns its own SQLite DB). No new Python dependency (Komga does its own image decoding via Java/NightMonkeys — no Pillow needed).

**Configuration to respect the read-only mount:** leave the optional *Automatically convert to CBZ* and *Automatically repair incorrect file extensions* options **disabled**, since both write into the media folder (verified docs: https://komga.org/docs/guides/libraries#convert-to-cbz, #repair-extensions). With those off, Komga's interaction with `/data` is read + scan (inferred).

## 6. Dedup / idempotency story

- **Idempotent scanning:** scans use the *last-modified time of parent folders* to decide which books to re-compare, so stable `{artist}/{year}.zip` files are not re-analyzed on every scan (verified docs: https://komga.org/docs/guides/scan-analysis-refresh#deep-scan). Deep scan forces a full re-compare when filesystems don't update mtime reliably (relevant for some FUSE/NFS setups — there is an explicit mergerfs mtime FAQ entry, verified docs: https://komga.org/docs/faq#scan-doesnt-pick-up-new-files-under-mergerfs).
- **Duplicate detection (opt-in):** *Compute hash for files* detects duplicate files; *Compute hash for pages* hashes the first/last 3 pages of each `cbz` to detect duplicate pages. Both are off by default and "can consume lots of resources on large libraries or slow hardware" (verified docs: https://komga.org/docs/guides/libraries#compute-hash-for-files, #compute-hash-for-pages).
- **Deletions:** removed files are soft-deleted to a per-library *trash* (restorable), with an optional "Empty trash automatically after every scan" (verified docs: https://komga.org/docs/guides/libraries#auto-empty-trash, https://komga.org/docs/guides/trash). Inkwell never deletes zips, so trash is a non-issue in steady state.

## 7. Alignment with codebase ground-truth constraints

- **Per-year zip steady state → ✅ strong.** Komga reads `{artist}/{year}.zip` natively; the archive model is its native unit. (verified docs: https://komga.org/docs/guides/libraries#file-types)
- **Read-only NFS media mount → ✅ compatible (with caveats).** Core read/scan/thumbnail-gen is read-only on media; the two media-writing options must stay disabled. The SQLite DB `/config` must be on **local** disk, not the NFS share (verified docs: https://komga.org/docs/installation/docker, https://komga.org/docs/faq). Whether thumbnail/analysis artifacts are written under `/config` vs attempted near `/data` is **not confirmed in docs** (see Open questions).
- **`gallery-dl` subprocess / `archive.db` owned by gallery-dl → ✅ independent.** Komga brings its own DB and image stack; no overlap with Inkwell's DB or `gallery-dl`.
- **Folder layout `{artist}/{year}.zip` → ✅ clean mapping.** `{artist}` = Series, `{year}.zip` = Book. (verified docs: https://komga.org/docs/guides/scan-analysis-refresh#what-happens-during-a-scan)
- **No Pillow needed → ✅.** Komga decodes images in-JVM; Inkwell adds no dependency.

## 8. Open questions / what is NOT verifiable from docs (hands-on needed)

- **Thumbnail & analysis artifact location:** confirm generated thumbnails/page data are stored under `/config` (local) and that nothing is written back to the read-only `/data` media path during analysis. Docs don't state the cache path explicitly. *(unknown — needs hands-on: inspect `/config` vs `/data` after first analysis on a read-only-mounted library)*
- **Read-only NFS scan correctness:** confirm scanning + analysis over a read-only NFS `/data` doesn't attempt temp writes near media and doesn't error. *(unknown — needs hands-on: run an analysis pass with `/data` mounted `ro`)*
- **"Read-only library" toggle:** the *Libraries* guide's scanner/options/metadata sections contain no read-only flag; a search summary asserted one exists but it is **not corroborated** by the official libraries/configuration pages read here. Not blocking (read-only-ness is enforced at the mount level by Inkwell), but worth confirming. *(unknown — needs hands-on: check Library edit dialog + https://komga.org/docs/installation/configuration)*
- **Large "book" UX:** whether the Thumbnails explorer and webreader remain usable when one `{year}.zip` contains hundreds/thousands of unrelated illustrations (a "book" far larger than a typical comic). *(unknown — needs hands-on: load a multi-thousand-image zip)*
- **Scan/analysis throughput over NFS** for a large accumulated library; hashing is explicitly resource-heavy. *(unknown — needs hands-on: benchmark scan + optional hashing on NFS)*

## 9. Scores

| # | Criterion | Score | Justification |
|---|-----------|-------|---------------|
| 1 | Zip/archive compatibility | **2** | Natively reads `zip`/`cbz`/`cbr`/`rar` in place — `{artist}/{year}.zip` is read directly, no extraction (https://komga.org/docs/guides/libraries#file-types). |
| 2 | Data/ingest model | **2** | Filesystem-scan library model with scheduled/on-startup scan matches "Inkwell writes zip to NAS, Komga reads" with zero API coupling (https://komga.org/docs/guides/scan-analysis-refresh). |
| 3 | Deployment footprint | **2** | Single self-contained JVM Docker image, local SQLite, no external DB/Redis; modest RAM, multi-arch, actively maintained (https://komga.org/docs/installation/docker). |
| 4 | Browsable UX for illustrations | **1** | Cover grid + thumbnails explorer + full-res "Original" scale exist, but the reader is page-sequential and has **no deep/arbitrary zoom** — a year-zip of unrelated images is browseable, not a true gallery (https://komga.org/docs/guides/webreader-divina, https://github.com/gotson/komga/discussions/1596). |
| 5 | Coupling / Inkwell effort | **2** | Zero Inkwell code; seam is a read-only filesystem mount + one library root + scheduled scan; no Pillow, no DB bridge (https://komga.org/docs/guides/libraries). |
| 6 | Content-type fit | **1** | Purpose-built for sequential comics/manga/BD (Series→Book→Page, ComicInfo.xml, reading direction); it *can* host a zip of unrelated illustrations but every metadata/UX assumption is sequential-narrative (https://komga.org/docs/introduction, https://komga.org/docs/guides/webreader-divina). |
