# Changelog

All notable changes to Inkwell. Format based on
[Keep a Changelog](https://keepachangelog.com/1.1.0/). This project has no released
versions; entries group commits on `main`, where each push builds a Docker image tagged
`latest` + commit SHA (`.github/workflows/build.yml`). For live status see
[`PROGRESS.md`](PROGRESS.md); for planning see [`docs/ROADMAP.md`](docs/ROADMAP.md). Older
history predating this file lives in the Git log.

## [Unreleased]

### Added
- **Artists tab: table layout with a Missing column and First/Last pagination.** The
  artist list renders as a subtle table (caption header row, hairline dividers) with
  per-artist Files, Missing, and Last-scan columns; the Missing cell shows
  `missing/total (p%)` from the last integrity check — `check_integrity` now persists
  per-artist missing counts under `integrity:last_check` (`by_artist`), showing `0/…`
  for clean artists and `—` when no check has run or the artist has no files. Pagination
  gains First/Last buttons alongside Prev/Next.
- **Artists table columns are sortable.** Artist / Files / Missing / Last-scan
  headers are now click-to-sort buttons: first click sorts ascending, clicking again
  toggles direction (arrow shown on the active column), switching columns resets to
  ascending and back to page 1. Unchecked artists ("—") sort below 0 missing;
  "Never" sorts as the oldest scan. Search, pagination, and actions unchanged.


### Fixed
- **Download timeout no longer masked as `exited with code -9`.** `_run_gallery_dl`
  (`src/downloader.py`) caught `subprocess.TimeoutExpired`, SIGKILLed the process
  (returncode `-9`), and returned a `CompletedProcess` instead of re-raising — so
  `download_artist`'s dedicated timeout branch was unreachable and jobs logged the
  opaque `gallery-dl exited with code -9` against an empty stderr. It now re-raises
  `TimeoutExpired` after kill/reap/thread-joins, producing the clear
  `gallery-dl timed out after Ns`. (`c970c0f`)

### Changed
- **gallery-dl stderr is mirrored to the `logs` table during downloads.** Each non-empty
  stderr line is persisted with `source='gallery-dl'`, level `INFO`, tagged with the
  running `job_id`/`artist_id`, so the Logs tab and Export Logs surface the real
  429s / rate-limit notices that precede a failure. (`c970c0f`)
- **Download timeout raised from 600s (10 min) to 1200s (20 min)** so cold x.com
  `UserMedia` walks — whose enumeration is API-paced (`sleep-request`) and is not
  skipped by `archive.db` — finish in one pass. (`c970c0f`)
- **Dashboard switches to Streamlit's wide layout** (`st.set_page_config(layout="wide")`
  in `src/app.py`). The default centered container (~734px) truncated the Artists table's
  action buttons (`Download` → `Do…`) and wrapped the Files / Last-scan cells; wide gives
  every column room on normal desktop widths. Also sets the browser tab title to "Inkwell".
