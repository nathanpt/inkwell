# SQLite Threading Error: `ProgrammingError` in Streamlit UI

## Status: Open

## Error

```
sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread.
The object was created in thread id 134722772522688 and this is thread id 134722143364800.
```

## Traceback (from production container)

```
File "/app/src/app.py", line 102, in <module>
    main()
File "/app/src/app.py", line 98, in main
    _render_dashboard()
File "/app/src/app.py", line 70, in _render_dashboard
    _render_auth_banner()
File "/app/src/app.py", line 61, in _render_auth_banner
    if not adapter.is_auth_valid(conn):
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/app/src/sites/xcom.py", line 51, in is_auth_valid
    return db.get_state(conn, AUTH_STATE_KEY) != "0"
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/app/src/db.py", line 70, in get_state
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
```

The error is intermittent but frequent. It happens on page load / Streamlit rerun.

## Context

### Architecture

- **UI framework**: Streamlit (runs as a web server, reruns the script on each user interaction)
- **Database**: SQLite with WAL mode, stored at `/app/data/inkwell.db`
- **Deployment**: Docker container using pre-built GHCR image (`ghcr.io/nathanpt/inkwell:latest`)
- **Background threads**: APScheduler for scheduled downloads, threading for on-demand downloads
- **Python**: 3.12-slim base image

### Streamlit's Threading Model

Streamlit reruns the app script on each user interaction. These reruns can execute in different threads from a thread pool. The same `session_state` dict is shared across threads for a given session.

### Key Observation: Traceback Line Numbers Don't Match Source

The traceback consistently shows line numbers that **do not match the current source code**. For example:

- Traceback says `app.py:61` is `if not adapter.is_auth_valid(conn)`
- But in the current `app.py`, line 61 is inside `_init_session_state()`, and `is_auth_valid` is at line 86

This strongly suggests the running container is executing **stale code** that was baked into a previous image build. Despite `check_same_thread=False` being present in all `sqlite3.connect()` calls in the current source, the container may be running an older version without that flag.

**However**, the user has confirmed they are running the latest GHCR image tagged with the latest commit SHA. This needs investigation -- possible causes include:
1. Docker image cache not invalidated during CI build
2. `docker compose pull` not actually pulling the new image
3. Streamlit caching the compiled `.pyc` bytecode across image updates

## What Has Been Tried (Two Prior Attempts)

### Attempt 1 (commit `a6a9bc7`)

Changed `app.py` to store `db_path` in `st.session_state` instead of a `sqlite3.Connection`. Added `_get_conn()` helper that creates a fresh connection via `db.get_connection()`. Updated all section files, scheduler, and download threads.

**Problem**: The new `db.get_connection()` function called `sqlite3.connect(str(db_path))` **without** `check_same_thread=False`.

### Attempt 2 (commit `de60c46`)

Added `check_same_thread=False` to both `db.get_connection()` and `_update_job_progress()` in `downloader.py`.

**Problem**: Error persists. Traceback line numbers still don't match source, suggesting stale code is running in the container.

## Files Involved

| File | Role |
|------|------|
| `src/app.py` | Streamlit entry point, stores `db_path` in session state, calls `_get_conn()` |
| `src/db.py` | DB layer -- `connect()`, `get_connection()`, all CRUD functions take `conn` param |
| `src/bootstrap.py` | Creates initial connection for schema init, returns `(conn, config)` |
| `src/scheduler.py` | APScheduler callback, creates own connection via `db.get_connection()` |
| `src/downloader.py` | Download logic, `_update_job_progress()` creates own connection |
| `src/sections/downloads.py` | Streamlit section, spawns background threads with own connections |
| `src/sections/artists.py` | Streamlit section, creates fresh connection via `db.get_connection()` |
| `src/sections/settings.py` | Streamlit section, creates fresh connection via `db.get_connection()` |
| `src/sections/logs.py` | Streamlit section, creates fresh connection via `db.get_connection()` |
| `src/sites/xcom.py` | Site adapter, `is_auth_valid()` takes `conn` and calls `db.get_state()` |

## Possible Root Causes to Investigate

1. **Stale Docker image**: The traceback line numbers don't match the current source. Verify with `docker exec inkwell head -n 70 /app/src/app.py` to see what code is actually running in the container.

2. **Streamlit connection reuse across reruns**: Even with `check_same_thread=False`, if a connection object from a previous rerun's thread is somehow retained (e.g., via Streamlit's caching or a closure), it could fail when accessed from a new thread. Check if any closure or cached function captures a `conn` reference.

3. **`bootstrap()` connection leak**: `bootstrap()` calls `db.connect()` which creates a connection with `check_same_thread=False`. This connection is then closed in `_init_session_state()`. But `_bootstrap_done` is a module-level flag -- if the module is re-imported or the process restarts, this could interact unexpectedly.

4. **Streamlit's `@st.cache_resource` or similar**: If Streamlit caches any function that holds a `conn` reference, it could serve a stale connection object from a different thread. Check for any cached functions.

5. **APScheduler thread sharing**: The scheduler creates its own connection in `_scheduled_run()` and closes it in a `finally` block. Verify this connection isn't leaking into the Streamlit thread.

## Suggested Fix Approach

1. **First**: SSH into the container and verify what code is actually running (`docker exec inkwell cat /app/src/app.py | head -n 70`). If it doesn't match the repo, the issue is deployment, not code.

2. **If code matches**: Consider switching to a connection-per-call pattern where no `conn` object is ever stored or passed -- each `db.*` function creates and closes its own connection internally. This eliminates all threading risk at the cost of connection overhead (mitigated by SQLite WAL mode).

3. **Alternative**: Use a thread-local connection pool via `threading.local()` to ensure each thread gets exactly one connection that it reuses.
