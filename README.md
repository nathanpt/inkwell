# Inkwell

Self-hosted media archiver for X.com (Twitter) artists. Downloads high-resolution media to your NAS via [gallery-dl](https://github.com/mikf/gallery-dl), managed through a Streamlit web dashboard.

## Features

- Add/remove X.com artists from a web UI
- Manual and scheduled (nightly) downloads
- Incremental, deduplicated downloads via gallery-dl's archive DB
- Download metrics (file count, bytes) per job
- Cookie management with expiry warnings
- Auth error detection with dashboard alerts
- Runs as a single Docker container

## Requirements

- Docker + Docker Compose (v2)
- Docker Buildx plugin (included in Docker Desktop; on Linux, install via `sudo apt install docker-buildx-plugin`)
- NFS share mounted on the host (e.g., `/nas/inkwell/`)

## Quickstart

### 1. Clone and configure

```bash
git clone https://github.com/nathanpt/inkwell.git
cd inkwell
```

### 2. Set a password

Create a `.env` file in the project root:

```bash
echo "INKWELL_PASSWORD=your_password_here" > .env
```

This is required on first launch. The password is hashed with bcrypt and stored in `config.toml`.

### 3. Start the container

```bash
docker compose up -d
```

### 4. Open the dashboard

Navigate to `http://<host-ip>:8501` and log in with your password.

### 5. Upload cookies

1. Export cookies from your browser for `x.com` using a cookie export extension (e.g., [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc))
2. In the dashboard, go to **Settings** and upload the `cookies.txt` file

### 6. Add artists

Paste an X.com profile URL (e.g., `https://x.com/artistname`) in the **Artists** section and click **Add Artist**.

## Configuration

### `config.toml`

Application settings, bind-mounted into the container. Edit on the host and restart to apply.

```toml
[nas]
mount_path = "/nas/inkwell"          # NFS share path (bind-mounted)

[schedule]
cron = "0 3 * * *"                   # Nightly at 03:00

[download]
retry_attempts = 3                   # Per-artist retries on failure
retry_backoff = [5, 15, 45]          # Seconds between retries
timeout = 600                        # Per-artist subprocess timeout (seconds)
inter_artist_cooldown = [30, 60]     # Random seconds between artists (jitter)

[cookies]
expiry_warning_days = 30             # Warn when cookies are older than this

[auth]
password_hash = ""                   # Set automatically from INKWELL_PASSWORD

[retention]
log_days = 90                        # Prune logs older than this on startup
```

### `gallery-dl.conf`

Download engine configuration. Controls file naming, directory structure, and rate limiting.

### NAS mount

Mount the NFS share on your Docker host with `soft,intr` options to prevent indefinite I/O hangs:

```
nas-host:/export/inkwell  /nas/inkwell  nfs  soft,intr  0 0
```

## Storage Layout

Downloaded media is organized on the NAS:

```
/nas/inkwell/
├── artist_name/
│   └── 2025/
│       ├── 001_image.jpg
│       └── 002_artwork.png
└── another_artist/
    └── 2025/
```

## Usage

| Action | How |
|--------|-----|
| Add artist | Paste URL in Artists section |
| Download now | Click "Download Now" or "Download All" |
| Remove artist | Click "Remove" (keeps files) or "Delete Files" (removes files) |
| View logs | Expand the Logs section, filter by level/source |
| Update cookies | Settings section, upload new `cookies.txt` |

## Architecture

See [docs/DESIGN.md](docs/DESIGN.md) for the full engineering design document.

## Development

```bash
# Install dependencies
uv sync --dev

# Run tests
.venv/bin/python -m pytest tests/ -v

# Rebuild container after code changes
docker compose up -d --build
```

### "docker compose build" warns about missing buildx

Install the plugin:

```bash
sudo apt install docker-buildx-plugin
```

## License

Private project. All rights reserved.
