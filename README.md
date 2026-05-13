# Inkwell

Self-hosted media archiver for artists on X.com, Pixiv, and DeviantArt. Downloads high-resolution media to your NAS via [gallery-dl](https://github.com/mikf/gallery-dl), managed through a Streamlit web dashboard.

## Features

- Add/remove artists from a web UI (X.com, Pixiv, DeviantArt)
- Manual and scheduled (nightly) downloads
- Incremental, deduplicated downloads via gallery-dl's archive DB
- Adaptive rate limiting with automatic cooldown on throttled sites
- Download metrics (file count, bytes) per job
- Cookie and token management with expiry warnings
- Auth error detection with dashboard alerts
- Runs as a single Docker container with a pre-built image (no build tools needed)

## Requirements

- Docker + Docker Compose (v2)
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

> **Important:** The `config.toml` and `gallery-dl.*.conf` files must exist in the same directory as `compose.yaml` before starting. Docker will create directories instead of files at bind mount points if the source files are missing.

```bash
docker compose pull && docker compose up -d
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

## Updating

Pull the latest image and restart:

```bash
docker compose pull && docker compose up -d
```

## Development

```bash
# Install dependencies
uv sync --dev

# Run tests
.venv/bin/python -m pytest tests/ -v

# Build locally (uncomment build: . in compose.yaml first)
docker compose up -d --build
```

## License

[MIT](LICENSE)
