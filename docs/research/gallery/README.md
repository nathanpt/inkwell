# Gallery-Browsing Research

**Decision being made:** how Inkwell users browse the archived media — a built-in
in-app Streamlit gallery tab vs. an external gallery/comic server.

This folder holds the research that informs that decision. It modifies **no
application source**; implementing the winner is a separate, later effort.

## Contents

- **[comparison.md](comparison.md)** — score matrix (6 criteria × 7 candidates),
  ranking, and the top-3 shortlist with reasoning.
- **[recommendation.md](recommendation.md)** — the recommended option, the
  fallback, and the `?`/unknown items that could change the ranking.
- Candidate cards (one per option):
  - [in-app-streamlit-tab.md](in-app-streamlit-tab.md) — an in-app Streamlit gallery tab
  - [komga.md](komga.md) — comic/media server
  - [kavita.md](kavita.md) — comic/manga/ebook server
  - [immich.md](immich.md) — self-hosted photo/video gallery
  - [photoprism.md](photoprism.md) — AI-powered photo gallery
  - [piwigo.md](piwigo.md) — classic PHP photo gallery
  - [lychee.md](lychee.md) — lightweight PHP photo gallery

## Rubric

Every card is scored against a fixed contract: 6 criteria, each `0 | 1 | 2` (2 =
fully satisfies, 1 = partial, 0 = fails; `?` only when evidence is insufficient
and recorded in Open questions). The 6 criteria:

1. **Zip/archive compatibility** — reads media out of `{artist}/{year}.zip`
   archives directly in steady state.
2. **Data/ingest model** — filesystem scan vs. API push vs. both; fit to
   Inkwell's "write to NAS, gallery reads" flow.
3. **Deployment footprint** — container/runtime, resources, maintenance.
4. **Browsable UX for illustrations** — grid/album, deep zoom, full-res; for a
   large set of unrelated single images.
5. **Coupling / Inkwell effort** — Inkwell code/integration required.
6. **Content-type fit** — illustrations vs. photos vs. sequential comics/manga.

Each card also covers Storage handling, Dedup/idempotency, alignment with the
verified codebase ground truth, and Open questions. The full contract lives at
`local://gallery-research-rubric.md`.

## Headline result

The decisive constraint is zip compatibility: only **in-app-streamlit-tab**,
**komga**, and **kavita** read Inkwell's per-year zips in place (the four photo
galleries are blocked by zip = 0). See
[recommendation.md](recommendation.md) for the pick and the gating hands-on check.
