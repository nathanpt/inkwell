# Gallery-Browsing — Recommendation

**Decision:** how Inkwell users browse archived media.
**Recommended:** `in-app-streamlit-tab` (the existing in-app spec,
`docs/specs/gallery-tab.md`).
**Fallback:** `komga`, if the in-app performance / deep-zoom unknowns prove
blocking.

## Why in-app-streamlit-tab

**1. It satisfies the decisive constraint and fits the actual content.** The
verified steady-state storage is per-year zips (`src/zipper.py` `zip_year_dir`
unlinks every loose file after building `{artist}/{year}.zip`). Only three
candidates read those zips in place (criterion 1 = 2): in-app, komga, kavita. The
other four are eliminated by zip = 0 — each would force Inkwell to abandon its
zip steady state or maintain a parallel extracted loose-image tree.

Among the three zip-capable options, **in-app is the only one purpose-built for
the actual content** — single illustrations. It scores content-type fit **2** vs
komga/kavita's **1** (their comic-sequential model treats each year-zip as a paged
"book" of unrelated images). That content-fit advantage is the differentiator: the
media is illustrations, not comics.

**2. Cleanest coupling, smallest footprint — and it ties the alternatives on
those axes anyway.** in-app scores coupling **2** (it IS Inkwell: reads its own
`files` SQLite table, resolves media from the NAS on demand, no API contract, no
separate deploy) and footprint **2** (no new container or DB; Streamlit is already
a dependency). komga/kavita also score 2/2 here (single container, zero code), so
on coupling and footprint the shortlist ties. in-app's edge is content-fit (2 vs
1) plus the absence of a second stateful service to run, back up, and
security-patch.

**3. The cost is bounded and one-time.** in-app requires concrete, internal
deltas — not an external dependency to operate:
- Add `Pillow` to `pyproject.toml` (currently absent).
- Schema v3→v4 migration adding the `idx_files_downloaded` index (the
  `downloaded_at` column already exists; only the index is missing). The card
  notes the spec's single-column `(downloaded_at DESC)` is a poor match for
  "filter by artist, sort by recency" — a composite `(artist_id, downloaded_at
  DESC)` would serve the query better.
- A gallery query helper beyond `get_recent_files` (multi-select year/site +
  offset pagination). The spec's "No changes to db.py" is **incorrect** — a
  migration + helper are required (confirmed in the card).
- Thumbnail-cache lifecycle (generate-on-first-browse, cache in the local
  `/app/data` volume, freshness check).

All of this is inside Inkwell; no second service to run.

## The honest weakness — and the fallback

in-app scores **UX 1**. Limitations: no zoom/lightbox (explicitly out of scope in
the spec), Streamlit `st.tabs()` reruns every tab body on each interaction, and
SQLite `LIMIT/OFFSET` makes deep pages `O(OFFSET)`. None of this is measured.
These are the **ranking-changing unknowns** (see below): if NFS cold-cache
thumbnailing or Streamlit rerun cost is unacceptable at scale, in-app drops below
komga/kavita.

**Komga is the fallback.** Zero Inkwell code; reads `zip`/`cbz` natively; single
JVM container with local SQLite (no external DB/Redis); scheduled scan. You accept
the comic-sequential UX — a Thumbnails-explorer grid + full-res "Original" scale,
but no arbitrary zoom and a page-sequential reader. **Kavita** is the alternative
fallback with the same profile; its distinguishing risk is that a read-only NFS
library mount is unverified (its biggest open question).

## Why the photo galleries are out

Lychee (8), PhotoPrism (6), Piwigo (6), and Immich (5) all score **zip = 0**.
Each reads loose files only and would force a storage-model change or a parallel
extraction tree — fighting the verified `zip_year_dir` steady state. Lychee is the
strongest of the four (light, strong checksum dedup, good illustration grid UX),
but the zip blocker is disqualifying. Immich additionally carries the heaviest
footprint (multi-container, PostgreSQL + Redis, ≥6 GB RAM, ML container).

## Scores of `?` / unknowns that could change the ranking

**No criterion was scored `?`** — every score resolved from official sources or
the codebase. The ranking-changing items are "unknown — needs hands-on"
Open-questions, not `?` scores, ordered by leverage:

1. **(highest leverage) in-app NFS cold-cache + Streamlit-scale performance** —
   `in-app-streamlit-tab.md` §8 #1–#4. If a hands-on benchmark over the real NFS
   mount shows unacceptable thumbnail-generation or rerun cost at tens of
   thousands of images, in-app drops below komga/kavita.
   *Resolve:* benchmark first-browse + "Regenerate Thumbnails" over the real NFS
   mount with a representative multi-thousand-image zip; profile a Streamlit
   session at target scale.

2. **komga/kavita read-only NFS scan correctness** — `komga.md` §8 #1–#2,
   `kavita.md` §8 #1. If a read-only (`:ro`) NFS library mount fails to
   scan/analyze, their zero-effort appeal collapses.
   *Resolve:* run an analysis pass with the media path mounted read-only.

3. **komga/kavita large-"book" reader UX** — `komga.md` §8 #4, `kavita.md` §8 #4.
   If a multi-thousand-image year-zip is unresponsive in the reader /
   thumbnails-explorer, their UX drops further.
   *Resolve:* load a representative large zip and time paging/grid navigation.

4. **PhotoPrism zip-indexing behavior** — `photoprism.md` §8 #2. If PhotoPrism
   unexpectedly surfaces inner images of a `.zip` during indexing (docs say it
   treats a zip as a single document and does not), its zip score could rise from
   0. Unlikely, but one hands-on check before final dismissal.
   *Resolve:* drop a `{artist}/{year}.zip` into `originals`, index, observe.

These are the only items that could reorder the top three; resolving #1 is the
gating step before implementation.
