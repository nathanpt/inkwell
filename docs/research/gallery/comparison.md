# Gallery-Browsing — Candidate Comparison

**Decision being made:** how Inkwell users browse archived media. Seven candidates
evaluated against the verified Inkwell ground truth: steady-state storage is
per-year zips (`{artist}/{year}.zip`, per `src/zipper.py` `zip_year_dir`); media
root `{nas_mount}/{artist}/{year}/` on NFS bind-mounted read-only at
`/nas/inkwell/`; `files` SQLite table; `gallery-dl` is a subprocess and
`archive.db` is gallery-dl-owned; production is Docker Compose.

Scoring: each of the 6 criteria scored `0|1|2` (2 = fully satisfies, 1 = partial,
0 = fails). **No criterion was scored `?`** — every score resolved from official
sources or the codebase. Source detail + justifications live in each card.

## Score matrix (rows = 6 criteria; columns = 7 candidates)

| Criterion (max 2) | in-app-streamlit-tab | komga | kavita | immich | photoprism | piwigo | lychee |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1. Zip/archive compatibility | **2** | **2** | **2** | 0 | 0 | 0 | 0 |
| 2. Data/ingest model | **2** | **2** | **2** | 1 | 2 | 1 | 1 |
| 3. Deployment footprint | **2** | **2** | **2** | 0 | 1 | 1 | 2 |
| 4. Browsable UX for illustrations | 1 | 1 | 1 | **2** | 1 | 1 | **2** |
| 5. Coupling / Inkwell effort | **2** | **2** | **2** | 1 | 1 | 1 | 1 |
| 6. Content-type fit | **2** | 1 | 1 | 1 | 1 | 2 | **2** |
| **Total / 12** | **11** | **10** | **10** | 5 | 6 | 6 | 8 |

## Dedup / idempotency (qualitative — not scored)

| Candidate | Dedup / idempotency story (summary) |
|---|---|
| in-app-streamlit-tab | None of its own — a pure read view over the `files` table; relies on gallery-dl's `--download-archive` upstream. |
| komga | Idempotent scan (parent-folder mtime); opt-in per-file/per-page hash dedup; soft-delete trash. |
| kavita | Idempotent change-driven scan (file + folder mtime, minute granularity); skips unchanged. |
| immich | **External libraries have no dedup** (FAQ explicit); upload-libraries dedup by hash per-library; separate ML near-duplicate utility. |
| photoprism | SHA-1 checksum + size exact-match on index/import; stacks related variants; no near-duplicate merge. |
| piwigo | Documented dedup on web/API import; FTP/`galleries`-sync dedup key undocumented (path/filename/md5 unknown). |
| lychee | Strong checksum-based dedup (`skip_duplicates`, source-verified in `FindDuplicate.php`); idempotent `lychee:sync`. |

## Ranking

By total score: **in-app (11) > komga = kavita (10) > lychee (8) > photoprism = piwigo (6) > immich (5)**.

But the total is not the whole story. The **decisive constraint is zip/archive
compatibility (criterion 1)**, because Inkwell's verified steady state is per-year
zips. It splits the field cleanly:

- **Zip-capable (criterion 1 = 2):** `in-app-streamlit-tab`, `komga`, `kavita`.
  These read `{artist}/{year}.zip` in place — no storage-model change, no
  extraction bridge, no duplicated loose-file tree.
- **Zip-blocked (criterion 1 = 0):** `immich`, `photoprism`, `piwigo`, `lychee`.
  Each reads **loose files only** and would force Inkwell to either abandon
  `zip_year_dir`'s per-year-zip behavior or maintain a parallel extracted
  loose-image tree (duplicating storage and adding an extraction job). This fights
  the verified storage model and is the single reason all four trail.

The realistic choice is therefore among the **top three**.

## Shortlist (top 3) — reasoning

### 1. `in-app-streamlit-tab` — 11/12 (highest; best content fit)
- Reads zips natively (`zipfile.ZipFile.open()`); **content-fit 2** — the only
  shortlisted option purpose-built for single illustrations (vs komga/kavita's
  comic-sequential model). Coupling 2 and footprint 2: it IS Inkwell (reads its
  own `files` table, no second service/DB, Streamlit already a dependency).
- Weakness: **UX 1** — no zoom/lightbox (spec out-of-scope), and Streamlit
  all-tabs-rerun + `LIMIT/OFFSET` deep pagination degrade at tens-of-thousands
  scale. These are unverified (see card §8: NFS cold-cache thumbnailing cost,
  `zipfile` per-member latency over NFS).
- Cost: real but bounded — add `Pillow`, schema v3→v4 index migration
  (`idx_files_downloaded`; the card notes a composite `(artist_id, downloaded_at
  DESC)` serves the query better than the spec's single-column index), and a
  gallery query helper beyond `get_recent_files` (the spec's "No changes to
  db.py" is incorrect).

### 2. `komga` — 10/12 (zero-effort, comic-sequential)
- Reads `zip`/`cbz` natively (criterion 1 = 2); **zero Inkwell code** — seam is a
  read-only filesystem mount + one library root + scheduled scan. Single JVM
  container, local SQLite, no external DB/Redis (footprint 2, coupling 2).
- Weakness: **content-fit 1 + UX 1** — a sequential comic reader; no arbitrary
  zoom (open request gotson/komga#1596); a year-zip of unrelated images is a
  "book" you page through, with a Thumbnails-explorer grid as the only
  gallery-like view.
- Unverified: read-only NFS scan correctness, thumbnail/analysis artifact
  location (`/config` vs `/data`), and large-"book" reader performance.

### 3. `kavita` — 10/12 (zero-effort, comic-sequential)
- Same profile as Komga: reads `.zip` natively, single .NET container with
  embedded SQLite (no external DB/cache), zero Inkwell code (footprint 2,
  coupling 2, criterion 1 = 2). Folder-watch exists but is inert over NFS → use
  scheduled/API-triggered scans.
- Weakness: identical comic-sequential UX limits (content-fit 1, UX 1; "no zoom
  in fullscreen"). Distinguishing risk: a **read-only (`:ro`) library bind-mount
  is not explicitly blessed by docs** — its biggest open question. Also,
  bare-year filenames (`2020.zip`) parse as a chapter.

## Best of the eliminated (photo galleries)
**Lychee (8/12)** is the strongest zip-blocked candidate — lightest footprint (2),
strong checksum dedup, good grid/album UX (2) and content-fit (2) for
illustrations — but it is still eliminated by zip = 0 (cannot read
`{artist}/{year}.zip`; needs an extraction bridge). PhotoPrism (6) and Piwigo (6)
have clean scan models but photo-oriented metadata and the same zip blocker.
Immich (5) has the best UX (2) but the heaviest footprint (multi-container,
PostgreSQL + Redis, ≥6 GB RAM, 0) on top of zip = 0.
