# Inkwell — Engineering Design Document

## 1. Overview

Inkwell is a self-hosted, lightweight web application deployed via Docker Compose. It automates the archival of high-resolution media from X.com (Twitter) artists to a local NAS, using `gallery-dl` as the core download engine. A Streamlit dashboard provides artist management, manual and scheduled downloads, and download history — no terminal required.

### Key Properties
- **Self-hosted** — single Docker container managed via Dockge
- **NAS-backed** via bind-mounted NFS share (`/nas/inkwell/...`)
- **Incremental and idempotent** — gallery-dl's own archive DB prevents duplicates
- **Sequential** — one artist at a time, reliable, no rate-limit roulette
- **Vibe-coded** — Streamlit + Python 3.12+ for easy maintenance and rapid iteration

---

## 2. Goals & Objectives

| Goal | Success Criterion |
|------|-------------------|
| Centralized control | All artist management and download triggering happens through the web dashboard |
| NAS integration | Media lands at `/nas/inkwell/{artist_handle}/{year}-{month}/` via bind-mounted NFS share |
| Incremental updates | Re-running an artist produces zero duplicate downloads (gallery-dl archive DB) |
| Resilience | Transient errors are retried with backoff; failures surface on the dashboard |
| Multi-site ready | The artist model is site-agnostic — adding Pixiv or DeviantArt later requires new URL patterns and auth, not a re-architecture |

---

## 3. System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Docker Host                           │
│  (managed via Dockge)                                    │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │             inkwell container                       │  │
│  │                                                     │  │
│  │  Streamlit (:8501)                                  │  │
│  │       │                                             │  │
│  │       ▼                                             │  │
│  │  Inkwell Core (Python 3.12+)                        │  │
│  │       │                                             │  │
│  │       ├── APScheduler (scheduling + sequential exec)│  │
│  │       │         │                                   │  │
│  │       │         ▼                                   │  │
│  │       └── gallery-dl ──────────────────┐            │  │
│  │                                        │            │  │
│  │  /app/data/     (named volume)          │            │  │
│  │  ├── inkwell.db                        │            │  │
│  │  ├── archive.db                        │            │  │
│  │  └── cookies.txt                        │            │  │
│  │                                        │            │  │
│  │  /app/config/  (bind-mounted from repo) │            │  │
│  │  ├── config.toml                        │            │  │
│  │  └── gallery-dl.conf                    │            │  │
│  │                                        │            │  │
│  └────────────────────────────────────────│────────────┘  │
│                                           │               │
│                    bind mount ◀───────────┘               │
│                           │                               │
│                    NFS share on host                       │
│                    /nas/inkwell/                           │
└─────────────────────────────────────────────────────────┘
```

### Layers

1. **Frontend (Streamlit):** Single-page dashboard with expandable sections. Protected by a simple password gate. Exposed on host port 8501.
2. **Core (Python):** Orchestrates `gallery-dl` via subprocess, runs scheduled jobs through APScheduler, handles auth (cookies.txt).
3. **Storage (NAS via NFS):** Host-mounted NFS share bind-mounted into the container at `/nas/inkwell/`.
4. **Persistence:** Split strategy — config files bind-mounted from the repo (version-controlled), DBs and cookies in a named Docker volume.

---

## 4. Data Model

### 4.1 Database: `inkwell.db`

Application state for artists, jobs, logs, and dashboard queries.

```sql
CREATE TABLE artists (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    handle        TEXT NOT NULL,
    site          TEXT NOT NULL DEFAULT 'x.com',
    source_url    TEXT NOT NULL UNIQUE,
    added_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_scan_at  TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_id     INTEGER NOT NULL REFERENCES artists(id),
    status        TEXT NOT NULL,           -- "running", "success", "failed"
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    file_count    INTEGER DEFAULT 0,
    total_bytes   INTEGER DEFAULT 0,
    error_message TEXT,
    triggered_by  TEXT NOT NULL DEFAULT 'manual'  -- "manual" or "scheduled"
);

CREATE TABLE logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT NOT NULL DEFAULT (datetime('now')),
    level         TEXT NOT NULL,           -- "INFO", "WARNING", "ERROR"
    source        TEXT NOT NULL,
    message       TEXT NOT NULL,
    job_id        INTEGER REFERENCES jobs(id),
    artist_id     INTEGER REFERENCES artists(id)
);

CREATE TABLE state (
    key           TEXT PRIMARY KEY,
    value         TEXT NOT NULL
);
```

The `state` table holds global runtime flags:
- `auth_session_valid` (`"1"` / `"0"`) — set to `"0"` immediately when gallery-dl returns an auth error, regardless of file age. The dashboard reads this flag to display a critical "RE-UPLOAD COOKIES" banner. Set back to `"1"` when new cookies are uploaded.
- `schema_version` — tracks the current DB schema version for migrations.

### 4.2 Database: `archive.db`

Owned entirely by `gallery-dl`. Inkwell never reads or writes this file — it's gallery-dl's dedup mechanism. Inkwell relies on gallery-dl's `--write-archive` flag to prevent duplicate downloads.

### 4.3 File: `config.toml`

Human-editable, version-controlled application configuration. See Section 9 for full schema.

### 4.4 File: `gallery-dl.conf`

gallery-dl download configuration. See Section 9.2 for contents.

### 4.5 Design Decisions

| Decision | Rationale |
|----------|-----------|
| Separate `archive.db` for gallery-dl | gallery-dl owns its own schema and dedup logic. Sharing a DB would require fighting gallery-dl's internal format. |
| SQLite `logs` table | Structured queryability for the dashboard (filter by level, source, date, job). Simpler than parsing log files in Streamlit. |
| `config.toml` (not a DB table) | Version-controllable, human-editable, type-safe via `tomllib`. No string-to-int parsing needed. |
| Soft deletes on artists (`is_active`) | Preserves job history and log records after removal. |
| Split volume strategy | Config files bind-mounted from repo (version-controlled, editable). DBs and cookies in named volume (opaque runtime state). |
| SQLite WAL mode | Streamlit reruns cause concurrent reads while APScheduler writes in a background thread. WAL mode prevents "database is locked" errors. |
| `state` table for runtime flags | `auth_session_valid` needs to be set by the downloader and read by the dashboard. A key-value table is simpler than a config file for mutable runtime state that changes outside user control. |
| `PRAGMA user_version` for migrations | Lightweight alternative to Alembic for a single-file SQLite DB. No extra dependency, and the schema is small enough that manual migration scripts are manageable. |
| Inter-artist cooldown with jitter | X.com applies shadow rate limits to sessions scraping many profiles in succession. Random 30–60s delays between artists avoid this without significantly slowing runs. |
| Per-artist job lock | Prevents manual and scheduled runs from colliding on the same artist, keeping directory-diff metrics atomic. |
| NFS mounted with `soft,intr` | Prevents indefinite I/O hangs on stale mounts — the kernel fails requests after a timeout instead of blocking. Combined with the subprocess timeout for defense-in-depth. |

---

## 5. Key Components

### 5.1 Core Engine — `gallery-dl`

Invoked as a subprocess. All download logic, dedup, naming, and rate limiting is delegated to gallery-dl.

```bash
gallery-dl \
  --config /app/config/gallery-dl.conf \
  --dest "/nas/inkwell/" \
  --write-archive "sqlite:////app/data/archive.db" \
  --cookies /app/data/cookies.txt \
  <source_url>
```

Note: `--dest` sets the base download directory. The subfolder structure (`{artist_handle}/{year}-{month}/`) is configured in `gallery-dl.conf` via path templates — `--dest` does not interpolate variables.

### 5.2 Scheduler — APScheduler

APScheduler handles both scheduling and sequential execution. No separate thread pool.

- **Default schedule:** nightly at 03:00 (configurable in `config.toml`)
- **Execution model:** APScheduler fires the job; artists are processed sequentially in a loop
- **Per-run flow:** NAS availability check (once), then call gallery-dl for each active artist one at a time
- **Inter-artist cooldown:** A random sleep of 30–60 seconds between artists. X.com applies "shadow" rate limits to sessions that scrape many profiles in rapid succession — even sequentially. This jitter keeps the session under the threshold without significantly slowing runs.

### 5.2.1 Job Lock

Only one job may run per artist at a time. Before creating a job, `downloader.py` checks:

```sql
SELECT 1 FROM jobs WHERE artist_id = ? AND status = 'running'
```

If a row exists, the request is rejected with a clear message. This prevents manual and scheduled runs from colliding and keeps directory-diff metrics atomic.

### 5.3 NAS Availability Check

The NFS share is mounted on the Docker host and bind-mounted into the container. Inkwell does not manage the NFS mount itself. A single pre-flight check runs at the start of each download run (not per-artist):

1. Check the bind-mounted path is writable (stat + touch test file)
2. On failure: retry with backoff (1s, 2s, 4s, 8s — max 4 attempts)
3. If all retries fail: abort the entire run, log error, surface alert on dashboard

If the mount drops mid-run, individual gallery-dl failures are caught per-artist by the error handler.

**Stale NFS mounts:** NFS mounts can enter a "stale" state where the directory exists but I/O hangs indefinitely. Two layers of defense:

1. **Host-side:** Mount the NFS share with `soft,intr` options. This tells the kernel to fail I/O requests after a timeout rather than blocking forever, allowing gallery-dl to return an error instead of hanging.
2. **Container-side:** The subprocess `timeout` parameter (Section 7.3) acts as a hard ceiling — if gallery-dl hangs on stale NFS I/O, the process is killed after the timeout expires.

### 5.4 Configuration Files

| File | Location | Owner | Purpose |
|------|----------|-------|---------|
| `config.toml` | `/app/config/` (bind mount) | Inkwell | App settings: NAS path, schedule, retry count, password hash |
| `gallery-dl.conf` | `/app/config/` (bind mount) | gallery-dl | Download settings: path templates, naming, rate limits, metadata |
| `cookies.txt` | `/app/data/` (named volume) | Inkwell (manages) / gallery-dl (reads) | X.com session cookies |

### 5.5 Download Metrics

`file_count` and `total_bytes` in the `jobs` table are populated by diffing the artist's directory before and after the gallery-dl run:

1. Before: walk the directory, record existing files and their sizes
2. After: walk again, diff against the snapshot
3. New files = `file_count`, sum of new file sizes = `total_bytes`

This is reliable because gallery-dl is the only writer and the job lock (Section 5.2.1) ensures only one download runs per artist at a time.

### 5.6 Startup Initialization

On every startup, Inkwell runs a bootstrap sequence:

1. **Verify storage location** — confirm `/app/data/` is on the container's local filesystem (not a bind mount to NFS). SQLite WAL mode requires POSIX-compliant file locking, which NFS does not reliably provide. Docker named volumes default to local storage — this is correct and must not be changed.
2. **Schema creation or migration** — check `PRAGMA user_version` against the expected schema version. If the DB doesn't exist, create all tables and set `user_version` to the current version. If `user_version` is behind, run migration scripts to add columns/tables and update the version. This ensures clean schema evolution across releases (e.g., adding `pixiv_token` to `artists` in Phase 5).
3. **Enable WAL mode** — `PRAGMA journal_mode=WAL` on the SQLite connection.
4. **Initialize state** — if `state` table is empty, seed `auth_session_valid = "1"` and `schema_version`.
5. **Clean orphaned jobs** — mark any jobs with `status = "running"` as `status = "failed"` with `error_message = "Container restarted mid-run"`. This handles ungraceful shutdowns.
6. **Prune old logs** — delete rows from `logs` older than 90 days (configurable in `config.toml`).
7. **Verify config** — ensure `config.toml` and `gallery-dl.conf` are present and parseable; abort with a clear error if not.

---

## 6. Web Dashboard (Streamlit)

### 6.1 Layout

Single-page app with expandable sections (`st.expander`):

| Section | Contents |
|---------|----------|
| **Artists** | Tracked artists with add/remove controls. Each row: handle, site, last scan date, file count. |
| **Downloads** | "Download Now" button. Job history table with status and date filters. |
| **Settings** | NAS path, retry count, schedule expression, cookies.txt upload + expiry status. |
| **Logs** | Filterable log viewer (level, source, date). Auto-refreshes during active downloads. |

### 6.2 Authentication

- Simple password gate (single shared password)
- Password stored as bcrypt hash in `config.toml`
- Set on first launch via environment variable or setup wizard
- Session-based via Streamlit's `st.session_state`

### 6.3 URL Validation

Accepted formats (X.com only for now):

| Pattern | Example |
|---------|---------|
| `https://x.com/{handle}` | `https://x.com/artistname` |
| `https://twitter.com/{handle}` | `https://twitter.com/artistname` |

Rules:
- Must match a known pattern (regex)
- Handle must be non-empty, valid characters only
- Duplicate URLs rejected with a clear message
- Future sites (Pixiv, DeviantArt) add new patterns — no architectural change needed

### 6.4 Artist Removal

Two options presented on removal:

1. **Remove from queue only** — `is_active = 0`, files remain on NAS
2. **Remove and delete files** — `is_active = 0` + recursive delete of `/nas/inkwell/{artist_handle}/`

Confirmation dialog required for option 2. Job history and logs are always preserved.

### 6.5 Cookies.txt Management

- **Upload:** file picker in Settings section. Written to `/app/data/cookies.txt` inside the named volume. On upload, set `auth_session_valid = "1"` in the `state` table.
- **Status:** shows last upload timestamp and file size
- **Expiry warning:** banner alert when cookies are older than a configurable threshold (default: 30 days)
- **Auth failure banner:** a critical "RE-UPLOAD COOKIES" banner shown immediately when `auth_session_valid = "0"` in the `state` table. This catches the case where cookies look fresh but have been invalidated by X.com (password reset, session logout, etc.) — no need to wait for the 30-day timer

### 6.6 Streamlit Configuration

Inkwell uses a `.streamlit/config.toml` (baked into the Docker image) for Streamlit-level settings:

```toml
[server]
maxUploadSize = 1  # cookies.txt is small, 1MB is generous

[runner]
fastReruns = true
```

---

## 7. Error Handling

### 7.1 Per-Artist Retry

On gallery-dl failure for a single artist:

1. Record failure in `jobs` table with error message
2. Retry up to configurable attempts (default: 3) with exponential backoff (5s, 15s, 45s)
3. On final failure: mark as `"failed"`, log the error, continue to next artist

### 7.2 Error Categories

| Error | Detection | Response |
|-------|-----------|----------|
| NAS unavailable | Pre-flight write check fails | Abort run after retries exhausted |
| gallery-dl non-zero exit | `subprocess.returncode != 0` | Per-artist retry with backoff |
| Auth expiry | gallery-dl stderr contains auth error | Fail immediately, set `auth_session_valid = 0` in `state` table, show critical "RE-UPLOAD COOKIES" banner |
| Timeout | `subprocess.TimeoutExpired` | Per-artist retry (also catches stale NFS hangs) |

### 7.3 Subprocess Execution

```python
result = subprocess.run(
    ["gallery-dl", ...args],
    capture_output=True,
    text=True,
    timeout=600,
)
```

- 10-minute default timeout (configurable in `config.toml`)
- stdout/stderr captured and parsed for error categorization

---

## 8. Storage Layout

```
/nas/inkwell/
├── artist_name/
│   └── 2025/
│       ├── 001_image.jpg
│       ├── 002_artwork.png
│       └── ...
└── another_artist/
    └── 2025/
```

- **Path template:** `/nas/inkwell/{artist_handle}/{year}/{filename}` — configured in `gallery-dl.conf`
- **Permissions:** `644` for files, `755` for directories (or as dictated by NFS mount options)

---

## 9. Configuration

### 9.1 `config.toml` — Inkwell Application Config

```toml
[nas]
mount_path = "/nas/inkwell"  # bind-mounted from host NFS

[schedule]
cron = "0 3 * * *"    # nightly at 03:00

[download]
retry_attempts = 3
retry_backoff = [5, 15, 45]  # seconds
timeout = 600                 # per-artist subprocess timeout
inter_artist_cooldown = [30, 60]  # random seconds between artists (jitter)

[cookies]
expiry_warning_days = 30

[auth]
password_hash = ""  # bcrypt hash, set on first launch

[retention]
log_days = 90  # prune logs older than this on startup
```

### 9.2 `gallery-dl.conf` — Download Engine Config

```json
{
  "base-directory": "/nas/inkwell/",
  "directory": {
    "extractor": "{author}"
  },
  "filename": {
    "extractor": "{tweet_id}_{filename}.{extension}"
  },
  "path-restrict": "auto",
  "sleep": 2,
  "sleep-extractor": 3
}
```

This config tells gallery-dl to:
- Download to `/nas/inkwell/` as the base directory
- Create a subdirectory per `{author}` (the artist handle)
- Name files as `{tweet_id}_{original_filename}.{ext}` for uniqueness
- Auto-sanitize path characters
- Sleep 2s between downloads and 3s between extractor calls (rate limit courtesy)

Note: The `{year}` subdirectory structure from the storage layout is achieved by a custom `directory` format `"{author}/{date:%Y}"` in `gallery-dl.conf`.

---

## 10. Tech Stack

| Component | Technology | Notes |
|-----------|-----------|-------|
| Language | Python | 3.12+ |
| Package manager | uv | — |
| Frontend | Streamlit | latest |
| Scheduler | APScheduler 3.x | BackgroundScheduler, sequential execution |
| Database | SQLite (stdlib `sqlite3`) | Two files: `inkwell.db` + `archive.db`, WAL mode enabled |
| Download engine | gallery-dl | latest, installed via pip |
| Auth | bcrypt + Streamlit session | Single shared password |
| Config | TOML (stdlib `tomllib`) | Python 3.11+ |
| Deployment | Docker Compose | Managed via Dockge |

### Project Structure

```
inkwell/
├── pyproject.toml            # dependencies including gallery-dl
├── Dockerfile
├── compose.yaml
├── config.toml               # bind-mounted into container
├── gallery-dl.conf           # bind-mounted into container
├── .streamlit/
│   └── config.toml           # Streamlit server config (baked into image)
├── src/
│   ├── __init__.py
│   ├── app.py                # Streamlit entry point
│   ├── bootstrap.py          # First-run init, schema creation, orphan cleanup
│   ├── db.py                 # SQLite connection, schema, queries
│   ├── models.py             # Artist, Job dataclasses
│   ├── downloader.py         # gallery-dl subprocess wrapper + retry + metrics
│   ├── scheduler.py          # APScheduler setup
│   ├── nas_monitor.py        # NAS availability check
│   ├── url_validator.py      # URL parsing and validation
│   ├── cookie_manager.py     # Cookie upload + expiry tracking
│   └── sections/             # Streamlit UI sections
│       ├── artists.py
│       ├── downloads.py
│       ├── settings.py
│       └── logs.py
├── tests/
│   ├── test_downloader.py
│   ├── test_url_validator.py
│   ├── test_nas_monitor.py
│   └── conftest.py
└── docs/
    └── DESIGN.md
```

---

## 11. Docker Deployment

### 11.1 Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy source and Streamlit config
COPY src/ src/
COPY .streamlit/ .streamlit/

VOLUME /app/data
EXPOSE 8501

CMD ["streamlit", "run", "src/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

Notes:
- `gallery-dl` is installed via pip as a dependency in `pyproject.toml`, not via apt. This ensures the latest version for X.com compatibility.
- `/app/config/` is NOT a Dockerfile `VOLUME` — it's bind-mounted at runtime via compose.yaml.
- Source is copied after dependencies for Docker layer caching.

### 11.2 compose.yaml

```yaml
services:
  inkwell:
    build: .
    container_name: inkwell
    ports:
      - "8501:8501"
    volumes:
      - inkwell-data:/app/data              # DBs + cookies (named volume)
      - ./config.toml:/app/config/config.toml:ro   # app config (bind mount)
      - ./gallery-dl.conf:/app/config/gallery-dl.conf:ro  # download config (bind mount)
      - /nas/inkwell:/nas/inkwell:rw        # NFS media share (bind mount)
    environment:
      - INKWELL_PASSWORD=${INKWELL_PASSWORD}
    restart: unless-stopped

volumes:
  inkwell-data:
```

Notes:
- Config files are bind-mounted read-only from the repo. Edit on the host, restart to apply.
- Named volume `inkwell-data` holds `inkwell.db`, `archive.db`, and `cookies.txt`. This volume **must** remain on the Docker host's local filesystem (the default). Do not symlink or move it to the NFS share — SQLite WAL mode requires POSIX file locking that NFS does not reliably support.
- NFS share bind-mounted read-write for media downloads.

### 11.3 Volume Layout

```
/app/data/              (named volume: inkwell-data)
├── inkwell.db          # artists, jobs, logs
├── archive.db          # gallery-dl dedup
└── cookies.txt         # X.com session cookies

/app/config/            (bind-mounted from repo)
├── config.toml         # Inkwell configuration
└── gallery-dl.conf     # gallery-dl configuration

/nas/inkwell/           (bind-mounted from host NFS)
├── artist_name/
│   └── 2025/
└── ...
```

### 11.4 Host Prerequisites

- NFS share mounted on the Docker host at `/nas/inkwell/` with `soft,intr` options to prevent indefinite I/O hangs on stale mounts
- Docker + Dockge installed
- `.env` file with `INKWELL_PASSWORD` for initial setup (or set via Dockge UI)

### 11.5 Lifecycle

| Action | Command |
|--------|---------|
| Start | `docker compose up -d` (or via Dockge) |
| Stop | `docker compose down` |
| View logs | `docker compose logs -f inkwell` |
| Rebuild after code change | `docker compose up -d --build` |
| Backup data | `docker run --rm -v inkwell-data:/data -v $(pwd):/backup alpine tar czf /backup/inkwell-backup.tar.gz /data` |

---

## 12. Workflow

### 12.1 Adding an Artist

1. User pastes an X.com profile URL into the Artists section.
2. Inkwell validates the URL, extracts the handle, checks for duplicates.
3. Artist record inserted into `inkwell.db`.
4. Dashboard shows the new artist with "never scanned" status.

### 12.2 Manual Download

1. User clicks "Download Now" on an artist (or "Download All").
2. NAS availability check runs (once per run).
3. gallery-dl executes sequentially — one artist at a time.
4. Directory diff calculates `file_count` and `total_bytes`.
5. Job records updated on completion; dashboard shows results.

### 12.3 Scheduled Download

1. APScheduler triggers the nightly job at the configured time.
2. Same flow as manual, but `triggered_by = "scheduled"`.
3. Results visible in the Downloads section the next morning.

---

## 13. Implementation Plan

### Phase 1 — Foundation
- [ ] Project scaffolding (`pyproject.toml`, uv, directory structure, Dockerfile, compose.yaml)
- [ ] `config.toml` and `gallery-dl.conf` schemas + loaders
- [ ] SQLite schema creation, `state` table, WAL mode, schema migration via `PRAGMA user_version` (`db.py`, `bootstrap.py`)
- [ ] Startup bootstrap: schema init/migration, orphan cleanup, log pruning, local-storage check (`bootstrap.py`)
- [ ] gallery-dl subprocess wrapper with retry, inter-artist cooldown with jitter, directory-diff metrics, and job lock (`downloader.py`)
- [ ] NAS availability check with stale-mount awareness (`nas_monitor.py`)
- [ ] URL validation for X.com (`url_validator.py`)
- [ ] Auth state tracking: set `auth_session_valid = 0` on auth errors, reset on cookie upload (`cookie_manager.py`, `downloader.py`)

### Phase 2 — Dashboard
- [ ] Streamlit skeleton with password gate (`app.py`, `.streamlit/config.toml`)
- [ ] Artists section: add, list, remove
- [ ] Downloads section: manual trigger, job history
- [ ] Settings section: config display, cookies upload

### Phase 3 — Scheduling
- [ ] APScheduler integration with cron from `config.toml`
- [ ] Sequential job execution with per-artist tracking
- [ ] Logs section: filterable log viewer

### Phase 4 — Polish
- [ ] Disk usage stats per artist
- [ ] Download history with file count and bytes
- [ ] Cookies expiry monitoring and dashboard alerts
- [ ] Critical "RE-UPLOAD COOKIES" banner based on `auth_session_valid` state

### Phase 5 — Multi-Site (Future)
- [ ] URL patterns for Pixiv and DeviantArt
- [ ] Per-site auth adapter (Pixiv uses OAuth tokens, not cookies)
- [ ] Site-specific gallery-dl config overrides
- [ ] Image gallery integration (Piwigo, Komga)
- [ ] Discord/Telegram notifications

---

## 14. Open Decisions

| Decision | Status | Notes |
|----------|--------|-------|
| Testing strategy | TBD | Unit tests minimum; integration tests need a test account |

---

## 15. Security

- **Password** stored as bcrypt hash in `config.toml` — never plaintext
- **Initial password** set via environment variable (not committed to source)
- **cookies.txt** stored inside a Docker named volume, not on the host filesystem
- **gallery-dl** runs as the container user — no elevated privileges, no host access beyond bind mounts
- **SQL injection** prevented by parameterized queries throughout
- **Input sanitization** — strict regex validation on URLs; no raw user input passed to subprocess args
- **Config files** bind-mounted read-only — container cannot modify its own config
- **Container** should run as non-root user (add `USER` directive to Dockerfile before production)

---

## 16. Future Considerations

- **Multi-site auth:** Pixiv requires OAuth refresh tokens, not browser cookies. An auth adapter interface will be needed.
- **Image gallery integration:** Piwigo or Komga for browsing with metadata
- **Notifications:** Discord/Telegram webhook on new art archival
- **Backup:** Automated SQLite dumps and NAS snapshot scheduling
- **Rate limit awareness:** Adaptive scheduling when rate limits are frequent
