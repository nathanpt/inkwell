# Candidate: Kavita

> Kavita is a fast, cross-platform self-hosted **reading server** built around Manga/Webtoons/Comics and ebooks.
> Runtime: **.NET** (GPLv3). Current stable: v0.9.0.2. (verified docs: https://github.com/Kareadita/Kavita/blob/main/README.md)
>
> Primary sources: https://github.com/Kareadita/Kavita (README) · https://wiki.kavitareader.com (scanner, FAQ, reader, install docs) · https://hub.docker.com/r/jvmilazz0/kavita (official Docker repo)

---

## 1. Storage handling

Kavita serves image-based media **directly from archives** — it does not require loose files. The README's headline feature list is explicit: it serves "Manga/Webtoons/Comics (**cbr, cbz, zip/rar/rar5, 7zip**, raw images) and Books (epub, pdf)" (verified docs: https://github.com/Kareadita/Kavita/blob/main/README.md). The product homepage confirms the same: "Dedicated, hand-crafted readers for EPUB, PDF, and image-based media (CBZ, loose images)" (verified docs: https://www.kavitareader.com). The comic/manga reader streams pages out of the archive on demand; cover generation "copy[s] images out of the archive and onto the disk" (into Kavita's own cache, not the library) (verified docs: https://wiki.kavitareader.com/guides/scanner — "Refresh Covers").

Mapping to Inkwell's steady-state form `{artist}/{year}.zip`: each `year.zip` is a recognized archive type, so **Kavita reads the media out of the per-year zips directly with zero extraction**. The `{artist}` directory satisfies Kavita's hard rule that "each series be in its folder" and "no files at root level" (verified docs: https://wiki.kavitareader.com/guides/scanner). Files inside a series folder are parsed by filename; a bare number in an otherwise-unmarked name is treated as a *chapter* (e.g. `2020.zip` → chapter 2020) (verified docs: https://wiki.kavitareader.com/guides/scanner/managefiles). So an artist maps to a Kavita *series* and each year.zip maps to a *chapter* of that series — a workable, if time-bucketed, representation.

## 2. Data/ingest model

**Filesystem scan** is the primary ingestion model — there is no required API push. "Scanning a library makes Kavita check its folders and sub-folders for new or removed items (books, archive files, etc). If new media is found, it then pulls it into the library" (verified docs: https://wiki.kavitareader.com/guides/scanner). This is an exact match for Inkwell's "Inkwell writes to NAS, gallery reads" flow: Inkwell's `zip_year_dir` writes `{artist}/{year}.zip` to the mount, and Kavita scans that mount with no code coupling. Kavita also exposes a REST API (verified docs: https://wiki.kavitareader.com/guides/api) usable to trigger scans programmatically if desired.

Auto-rescan via a filesystem watcher is supported: the FAQ documents raising the host `fs.inotify.max_user_watches` for "folder watching" (verified docs: https://wiki.kavitareader.com/troubleshooting/faq). **Caveat for NFS:** `inotify` is kernel-local and does not deliver events for writes performed on the NFS *server* side — so folder-watching will not fire when Inkwell's job completes the zip on the NAS host. Reliable ingestion over the read-only NFS bind-mount would therefore rely on scheduled/interval scans or an API-triggered scan rather than live watch (inferred: inotify semantics over NFS are a well-known platform limitation; Kavita docs confirm it uses inotify but do not address NFS specifically).

## 3. Deployment footprint

**Single Docker container, embedded SQLite, no external database/cache.** Official images: `jvmilazz0/kavita` on Docker Hub (the FAQ directs users to "the new Docker Central Repo … `jvmilazz0/kavita`"; the old `kizaing/kavita` is deprecated) and a LinuxServer.io image `lscr.io/linuxserver/kavita` (verified docs: https://wiki.kavitareader.com/troubleshooting/faq, https://wiki.kavitareader.com/installation/docker/lsio). A minimal compose is one service exposing **port 5000** with a single `/config` volume for its SQLite store and cache (verified docs: https://wiki.kavitareader.com/installation/docker/lsio). There is no MySQL, PostgreSQL, or Redis dependency. DB backups live in `config/backups` (verified docs: https://wiki.kavitareader.com/troubleshooting/faq).

Runtime is **.NET** (README is built with JetBrains Rider/dotTrace; stats payload records `DotNetVersion`) (verified docs: https://github.com/Kareadita/Kavita). Image processing for cover/thumbnail generation uses **NetVips**, which requires the **SSE4.2** CPU instruction set (verified docs: https://wiki.kavitareader.com/troubleshooting/faq — "What is the NetVips dependency"). The target host's Intel N100 (Alder Lake-N) supports SSE4.2, so this is satisfied. Resource profile is modest for a single-container app; the documented operational warning is that **first scans are slow on networked storage** ("First scans are often slow, especially on networked storage and even more so on remote (rclone) storage") (verified docs: https://wiki.kavitareader.com/guides/scanner) — directly relevant to the NFS mount.

## 4. Browsable UX

The library/series level shows a **cover-thumbnail grid** (browse by series = artist). Within an archive, Kavita presents a dedicated Comic/Manga/Image reader offering single-page, double-page, and **webtoon/infinite-scroll** layout modes, LTR/RTL reading direction, and image scaling of Height / Width / **Original** (render at native size) (verified docs: https://wiki.kavitareader.com/guides/readers/comic-manga).

Two limitations matter for a *browsing* use case over unrelated single illustrations (rather than sequential reading): (a) **no zoom in fullscreen** — "Fullscreen: When in fullscreen you cannot zoom" (verified docs: https://wiki.kavitareader.com/guides/readers/comic-manga); users have separately reported that fit-to-width/height scaling disables browser/pinch zoom in fullscreen (verified docs: https://github.com/Kareadita/Kavita/discussions/4222). So there is **no deep-zoom lightbox** for pixel-peeping large illustrations. (b) The reader is fundamentally **page-sequential** (chapter → page → page), designed for narrative comics/manga, not a free-form grid/album view of unrelated images. Within a `{year}.zip` the images page sequentially rather than presenting an explorable gallery of thumbnails.

## 5. Coupling / Inkwell effort

**Near-zero coupling — the cleanest seam is a shared read-only filesystem.** Inkwell would mount the existing NAS path (`{nas_mount}/{artist}/{year}/…` → `{artist}/{year}.zip`) into the Kavita container as a library root and point a Kavita library at it. No Inkwell source changes are required: the ingest model is a passive scan, `gallery-dl` remains an unrelated subprocess, and Inkwell never reads or writes Kavita's `kavita.db` or `archive.db`. The only "effort" is operational — one compose service plus a one-time library/folder-layout check. Inkwell's existing on-disk form already satisfies Kavita's structural rule (series-in-a-folder, no root-level files), so no directory restructuring is needed (inferred: from the scanner's file-layout rules vs. Inkwell's `{artist}/{year}.zip` shape).

## 6. Dedup / idempotency story

The scanner is idempotent and change-driven: "If the file hasn't been modified since the last time Kavita scanned it, it will not do extra processing on the file" (verified docs: https://wiki.kavitareader.com/guides/scanner — "What happens during a Scan?"). The FAQ states the same at file level: "If the underlying file Created/LastModified hasn't been changed since our last scan, we skip it to save time and resources" (verified docs: https://wiki.kavitareader.com/troubleshooting/faq). At folder level, "Kavita first checks if the folder has changed since our last scan. This checks at a minute level, so seconds will be ignored. This validates on the Last Write Time of the folder" — unchanged folders are skipped wholesale (verified docs: https://wiki.kavitareader.com/guides/scanner — "Step 2"). Re-scanning therefore never duplicates content; entities are keyed by path/series. A rewritten `{year}.zip` (new mtime) is reprocessed; one whose mtime is unchanged is skipped.

**NFS caveat:** mtime semantics and the minute-granularity check over NFS can be unreliable — the scanner itself warns that "not every action you can perform on a folder will change its modification time" and suggests touching a file to force an mtime bump (verified docs: https://wiki.kavitareader.com/guides/scanner — "Notes"). Idempotency holds, but change-detection over NFS may occasionally need a forced/library scan.

## 7. Alignment with codebase ground-truth constraints

- **Steady-state = per-year zips (`zip_year_dir` unlinks loose files).** ✅ Kavita reads `.zip` archives natively — no loose files required. This is its strongest point of alignment. (verified docs: README format list)
- **Media root `{nas_mount}/{artist}/{year}/`, NFS bind-mounted read-only at `/nas/inkwell/`.** ⚠️ The scanner only *reads* library folders ("validate that all library folders are not empty and can be accessed") and writes covers/cache to its own `/config` (verified docs: scanner "Step 1"). A **read-only library mount with a writable `/config` is the expected pattern** (inferred: scanner reads library; SQLite/cover cache live in config). However, official docs do **not** explicitly bless a `:ro` library mount, and conflicting community reports exist (unknown — needs hands-on: confirm a read-only library bind-mount + writable config scans correctly). Kavita's own `kavita.db` *must* be writable.
- **`gallery-dl` is a subprocess; `archive.db` is gallery-dl-owned.** ✅ Unaffected — Kavita never touches either.
- **Folder-layout rule.** ✅ Inkwell's `{artist}/{year}.zip` already matches "series in its own folder, no root files." But Kavita identifies series by *filename parsing, not folder structure* ("Kavita uses filenames and internal metadata for parsing and is not designed to use folder structure" — verified docs: FAQ), so a bare `2020.zip` parses as chapter 2020 (workable, not the intended metadata model).
- **inotify folder-watching over NFS.** ⚠️ Will not detect Inkwell's server-side writes; use scheduled/interval scans or API-triggered scans (inferred: NFS + inotify limitation).

## 8. Open questions / what is NOT verifiable from docs (hands-on needed)

- **Read-only library mount** (`:ro`) **+ writable `/config`**: does a scan actually complete and serve content with a read-only library bind-mount? Official docs require only that folders "can be accessed" but never explicitly confirm `:ro` works; conflicting GitHub issues exist. **This is the single biggest deployment unknown** (any `?`-leaning risk lives here).
- **inotify over NFS**: confirm the folder watcher does *not* fire for Inkwell's writes, and verify the available scheduled-scan / library-interval options as the fallback.
- **Bare-year filename parsing**: confirm `2020.zip` parses cleanly as a chapter without odd grouping, and whether `SP##`/ComicInfo would give a cleaner "year" grouping.
- **Large archive as one chapter**: how does the sequential reader behave when a single `{year}.zip` contains hundreds-to-thousands of unrelated images (paging, memory, "jump to page" performance)? Needs hands-on.
- **First-scan / cover-generation throughput over NFS** at scale (tens of thousands of images), and NetVips memory pressure.
- **mtime reliability over NFS** for the change-skip optimization.

## 9. Scores

| # | Criterion | Score | Justification |
|---|-----------|:-----:|---------------|
| 1 | Zip/archive compatibility | **2** | Natively reads `.zip`/`.cbz`/`.cbr`/etc. directly; perfect match for Inkwell's `{artist}/{year}.zip` steady state, no extraction. |
| 2 | Data/ingest model | **2** | Passive filesystem scan (+ optional REST API); ideal "write to NAS, gallery reads" fit. Folder-watch exists but is inert over NFS. |
| 3 | Deployment footprint | **2** | Single .NET Docker container, embedded SQLite, no external DB/cache/Redis; one port (5000), one config volume. NetVips needs SSE4.2 (N100 has it). |
| 4 | Browsable UX for illustrations | **1** | Cover grid + full-res "Original" scaling, but no zoom in fullscreen and a sequential page model meant for comics, not an unrelated-image lightbox/grid. |
| 5 | Coupling / Inkwell effort | **2** | Cleanest possible seam: a shared read-only filesystem. No Inkwell code change; existing layout already conforms to Kavita's folder rule. |
| 6 | Content-type fit | **1** | Comic/manga/ebook-oriented; displays images natively but models them as sequential chapters/volumes — a mismatch for a gallery of unrelated single illustrations. |
