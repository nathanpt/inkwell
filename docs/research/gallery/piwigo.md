# Piwigo

Candidate research card for the gallery-browsing decision. Piwigo is a classic
self-hosted **PHP + MySQL/MariaDB (LAMP)** photo gallery (https://piwigo.org).
Evaluated against Inkwell's verified ground truth: steady-state storage is
per-year **zips** (`{artist}/{year}.zip`) on NFS bind-mounted read-only at
`/nas/inkwell/`; loose files exist only transiently; `gallery-dl` is a
subprocess; Inkwell never touches `archive.db`.

Sources used (all official): requirements
https://piwigo.org/guides/install/requirements ; FTP import & sync
https://doc.piwigo.org/self-hosting-piwigo/importing-and-synchronizing-ftp-photos ;
importing photos (dedup) https://doc.piwigo.org/import-and-manage-photos/importing-photos-into-piwigo ;
official Docker image https://github.com/Piwigo/piwigo-docker ; v16 release note
https://piwigo.org/release-16.0.0 .

---

## 1. Storage handling

Piwigo does **not** read media out of `{artist}/{year}.zip` archives in steady
state. Its FTP/filesystem model expects **loose image files** placed in a
directory tree under `./galleries/`, where "each directory in `./galleries/`
generates an album" and "a file can be a photo if its extension appears in the
list of the `picture_ext` configuration setting"
(verified docs: https://doc.piwigo.org/self-hosting-piwigo/importing-and-synchronizing-ftp-photos).

A `.zip` is never treated as a browseable archive of images. Two documented zip
behaviours, both irrelevant to streaming archive contents:

- **Non-image element + representative:** "a zip file: since the zip file isn't
  an image, the .jpg image with the same name will be displayed in the gallery,
  and the zip will be downloadable through the floppy disk icon"
  (`pwg_representative`) (verified docs: https://doc.piwigo.org/self-hosting-piwigo/importing-and-synchronizing-ftp-photos).
- **Multiple-formats alternate:** `zip` can be listed in
  `$conf['format_ext']` as an *additional* downloadable format attached to a
  photo whose primary loose image already exists
  (verified docs: https://doc.piwigo.org/self-hosting-piwigo/importing-and-synchronizing-ftp-photos).
  This attaches a zip to an existing photo; it does not enumerate a zip's
  contents into the gallery.

The web **Batch Manager → Import** path can unzip a zip the *operator uploads*
into Piwigo's `upload/` directory and register the extracted images
(inferred: from the Batch Manager import workflow described in
https://doc.piwigo.org/import-and-manage-photos/importing-photos-into-piwigo ;
this is an upload-time extract, not steady-state archive reading).

**Implication for Inkwell:** because Inkwell's steady-state form is
`{artist}/{year}.zip`, Piwigo would require a separate **loose-file mirror**
(extracted from the zips) under `galleries/`. It cannot browse the NAS zips
directly.

## 2. Data / ingest model

Two distinct models, neither a clean match:

- **Filesystem scan (the "galleries/" model):** files are copied/FTP'd as loose
  files into `./galleries/{album}/`, then an operator runs **Tools →
  Synchronize** ("Directories + files"), which scans `galleries/`, creates
  albums, and registers files in the MySQL DB. The doc stresses sync is
  **manual and repeatable**: "synchronization is a necessary step whenever you
  add / rename / move / delete any element"
  (verified docs: https://doc.piwigo.org/self-hosting-piwigo/importing-and-synchronizing-ftp-photos).
  There is **no documented filesystem watcher / auto-scan** of a library root.
- **API push (the "upload/" model):** web drag-drop, mobile apps, Piwigo Remote
  Sync desktop app, Lightroom/Digikam/Shotwell/Flickr plugins push into Piwigo's
  own `upload/` storage; "Piwigo Remote Sync … each time you start the
  synchronization, only new photos will be added"
  (verified docs: https://doc.piwigo.org/import-and-manage-photos/importing-photos-into-piwigo).

Fit to Inkwell's "write to NAS, gallery reads" flow: the filesystem-scan model
*is* a "gallery reads a directory" flow, but it (a) wants **loose files**, not
zips, (b) must live under Piwigo's own `galleries/` path, and (c) requires a
**manual Synchronize** to reflect changes (no auto-watch)
(verified docs: https://doc.piwigo.org/self-hosting-piwigo/importing-and-synchronizing-ftp-photos).
The API-push model would mean duplicating data out of Inkwell's NAS zips into
Piwigo's `upload/`.

**Hard naming constraint (alignment risk):** "the name of the directories and
files must only contain letters, numbers, and the '-', '_', or '.' symbols. No
spaces or characters with accents" — violation raises `PWG-UPDATE-1`
(verified docs: https://doc.piwigo.org/self-hosting-piwigo/importing-and-synchronizing-ftp-photos).
Inkwell's `{artist}` directory names may contain spaces/Unicode, so a mirror
would need name sanitization.

**Read-only mount:** the `galleries/` directory is *read* during sync; Piwigo
stores generated "multiple sizes" in "a cache directory of your server" separate
from `galleries/`
(verified docs: https://piwigo.org/guides/install/requirements). So in principle
a read-only `galleries/` bind mount is workable with a writable cache, but this
is **not explicitly documented for a read-only NFS source** (inferred;
unknown — needs hands-on: confirm `galleries/` can be a read-only NFS bind mount
while `_data/i/` derivatives cache is writable, and that `i.php` still serves
derivatives).

## 3. Deployment footprint

Full **LAMP stack**: "PHP 8.2+" (7.4+ runs but unmaintained), "MySQL 5.6+ or
MariaDB 10.1+", a web server (Nginx or Apache), and "a graphic library:
ImageMagick is recommended … but GD … can also do the job"
(verified docs: https://piwigo.org/guides/install/requirements).

Official Docker image `Piwigo/piwigo-docker` is **Alpine + nginx + php-fpm +
MariaDB** as two containers; "all persistent data is stored in `./piwigo-data/`
(`piwigo` files, `mysql` database files, `scripts`)"
(verified docs: https://github.com/Piwigo/piwigo-docker). (A popular
community image `linuxserver/piwigo` also exists
verified docs: https://hub.docker.com/r/linuxserver/piwigo — not the official
one.)

Resource/maintenance: requires running **and backing up** a relational DB,
periodic PHP/Piwigo security upgrades (recent releases are explicitly
security-driven, e.g. "Piwigo 16.4.0 : because security matters"),
(verified docs: https://piwigo.org/release-16.0.0 and
https://piwigo.org forum release banner), plus maintaining the derivatives
cache and ImageMagick/GD. Heavier than a single-process Python gallery; an
external server would be an additional compose service with its own DB.

## 4. Browsable UX

- **Album tree:** hierarchical albums of unlimited depth; each `galleries/`
  subdirectory = an album
  (verified docs: https://doc.piwigo.org/self-hosting-piwigo/importing-and-synchronizing-ftp-photos).
  Maps cleanly to `artist → year`.
- **Thumbnail grid:** per-album thumbnail pages; non-image files get a
  representative (`pwg_representative`)
  (verified docs: https://doc.piwigo.org/self-hosting-piwigo/importing-and-synchronizing-ftp-photos).
- **Full-res / HD:** the original is retained; Piwigo generates multiple display
  sizes and v16 added **3XL and 4XL** sizes "because HD images and screen
  resolutions [got] bigger"; original/"full size" can be shown
  (verified docs: https://piwigo.org/release-16.0.0 and
  https://doc.piwigo.org/import-and-manage-photos/importing-photos-into-piwigo).
  One operator reports ">500K photos - still flies!"
  (inferred scalability from forum: https://piwigo.org/forum/viewtopic.php?pid=189903).
- **Deep zoom:** **not built-in** as a tiled pan/zoom viewer; interactive zoom
  requires a plugin (e.g. Highslide / Lightbox-plus / JQuery-Zoom)
  (inferred: web-search summary of plugins; unknown — needs hands-on: confirm
  there is no native deep-zoom and which current plugins still work on v16).

Suitable for a large set of unrelated single images via album + grid + full-res;
weaker for pixel-peeping illustrations because deep zoom is plugin-dependent.

## 5. Coupling / Inkwell effort

No Inkwell **library** coupling (Piwigo owns its DB; `archive.db` stays
gallery-dl's), but significant **integration/infra** work:

- Inkwell's zips must be **extracted to a loose-file mirror** under
  `galleries/{artist}/{year}/` (since Piwigo won't read zips) — extra storage +
  a job that re-extracts on each new year-zip.
- **Directory/file names must be sanitized** to `[A-Za-z0-9._-]` to avoid
  `PWG-UPDATE-1`
  (verified docs: https://doc.piwigo.org/self-hosting-piwigo/importing-and-synchronizing-ftp-photos).
- A **Synchronize trigger** is required after each change (manual button, a cron
  hitting the admin web API, or the Perl local-folder-sync script). No native
  auto-watch.
- Cleanest seam: Piwigo scans a mirrored `galleries/` tree read-only; Inkwell
  keeps writing zips to the NAS as today and an out-of-band process maintains
  the mirror + triggers sync. Inkwell itself needs no code change if the mirror
  is maintained externally (inferred).

## 6. Dedup / idempotency story

Two separate mechanisms:

- **Web/API import dedup (documented):** "Detecting duplicates during import —
  if you import a file that is identical to another file … Piwigo will add the
  existing file to the import album, but will not duplicate the file." Toggle in
  Configuration > Options > General > Miscellaneous
  (verified docs: https://doc.piwigo.org/import-and-manage-photos/importing-photos-into-piwigo).
- **FTP/galleries sync dedup:** re-running **Synchronize** reconciles the DB to
  current filesystem state ("checks the database against the current filesystem
  state"), and operators report duplicates removed on disk then disappear after a
  re-sync
  (inferred from forum: https://piwigo.org/forum/viewtopic.php?id=25917 and
  https://piwigo.org/forum/viewtopic.php?id=31411). The exact checksum/key used
  for FTP-sync dedup is **not spelled out in the official docs**
  (unknown — needs hands-on: confirm whether FTP-sync dedup is by path, filename,
  or MD5, and whether it is reliable for Inkwell's re-extract workflow).

Re-syncing is **idempotent** for the set of registered files in the steady state
(re-running with no FS changes adds nothing), but rename/move are *not* free —
they require a sync and can change album membership
(verified docs: https://doc.piwigo.org/self-hosting-piwigo/importing-and-synchronizing-ftp-photos).

## 7. Alignment with codebase ground-truth constraints

- **Zip storage (the decisive mismatch):** `src/zipper.py` `zip_year_dir`
  verifies `{artist}/{year}.zip` then `unlink()`s every loose file. Piwigo needs
  loose files in `galleries/`; it cannot browse the zips. A loose-file mirror is
  required → **does not align** with the verified steady-state storage
  (inferred from ground truth vs.
  https://doc.piwigo.org/self-hosting-piwigo/importing-and-synchronizing-ftp-photos).
- **NFS read-only mount:** Piwigo can scan `galleries/` and write derivatives to
  a separate cache, so a read-only source is *plausible* — but unverified for a
  read-only NFS bind, and the zip problem dominates anyway
  (inferred; unknown — needs hands-on).
- **Schema/data:** Piwigo has its own MySQL schema; Inkwell's `files` table and
  `archive.db` are irrelevant to it — **zero coupling there**, which is clean
  (inferred).
- **Dependencies:** no Inkwell dependency change (Piwigo is a separate service)
  — unlike the in-app tab, **Pillow is not added** to Inkwell
  (inferred from ground truth `pyproject.toml`).
- **Deployment:** adds a LAMP service + DB to the compose stack, sharing the NAS
  read-only (inferred from ground truth deployment + Docker image).

## 8. Open questions / what is NOT verifiable from docs (hands-on needed)

- Whether `galleries/` can be a **read-only NFS bind mount** while the
  derivatives cache (`_data/i/`) is writable, and whether `i.php` still serves
  images in that configuration. (Affects the Data/ingest and alignment scores; if it fails, the fit is worse than the current 1.)
- Whether **deep zoom / pan-to-pixel** is achievable on Piwigo 16 via a current,
  maintained plugin (no native tiled zoom confirmed). (Drives UX note.)
- Exact **FTP-sync dedup key** (path vs filename vs MD5) and whether it is
  reliable for an Inkwell re-extract mirror that may change mtimes. (Drives
  Dedup note.)
- **Performance** of Synchronize + on-the-fly derivative generation over NFS for
  a large illustrations library (tens of thousands of images) — docs cite large
  libraries on local/Synology storage, not read-only NFS
  (inferred from forum: https://piwigo.org/forum/viewtopic.php?pid=189903).
- Behaviour of the **directory-name restriction** with CJK/Unicode artist names
  beyond the documented "no accents" rule (unknown — needs hands-on).

## 9. Scores

| # | Criterion | Score | Justification |
|---|-----------|:-----:|---------------|
| 1 | Zip/archive compatibility | **0** | Wants loose files in `galleries/`; a zip is only ever a non-image element or an attached "format" — never a browseable archive of media (verified docs: FTP sync page). |
| 2 | Data/ingest model | **1** | Filesystem-scan model exists and fits "gallery reads a dir", but needs loose files (not zips), lives under Piwigo's `galleries/`, and requires a manual Synchronize (no auto-watch); read-only NFS bind is plausible but unverified. |
| 3 | Deployment footprint | **1** | Containerized with an official image, but it is a full LAMP stack (PHP 8.2 + MySQL/MariaDB + nginx + ImageMagick/GD) — a DB to run, back up, and security-patch (verified docs: requirements + piwigo-docker). |
| 4 | Browsable UX for illustrations | **1** | Strong album tree + thumbnail grid + full-res/3XL/4XL; but deep zoom is plugin-dependent, not native (verified docs: release-16.0.0; inferred: plugins). |
| 5 | Coupling / Inkwell effort | **1** | No Inkwell library/DB coupling (clean), but needs a loose-file mirror of the zips, name sanitization, and an external Synchronize trigger; not a zero-effort seam. |
| 6 | Content-type fit | **2** | A capable general image gallery; single illustrations browse well via artist→year album tree and full-res. Photo-oriented metadata (EXIF/dates/GPS) is a minor mismatch, not a blocker; no sequential-comic support (not needed here). |

**Total: 6 / 12.** Weakness is concentrated in **zip compatibility (0)** — the
single most important ground-truth constraint — compounded by the loose-file +
manual-sync + name-sanitization integration cost. The UX and DB-isolation
aspects are its strengths.
