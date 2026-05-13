# Inkwell Roadmap

## Planned

- [ ] MkDocs + Material theme documentation site, deployed to GitHub Pages
- [ ] Image gallery integration (Piwigo, Komga)
- [ ] Discord/Telegram notifications on new archival
- [ ] Automated SQLite dumps and NAS snapshot scheduling

## Completed

- [x] Accept localized Pixiv URLs (e.g. /en/users/12345)
- [x] Refactor Streamlit UI layout (horizontal button alignment, two-tier downloads, vertical_alignment)
- [x] Fix cookies.txt upload rerun loop (file_uploader retained state across reruns)
- [x] Refactor to connection-per-operation pattern (eliminate SQLite threading errors)
- [x] Fix SQLite threading error — stop sharing connection across Streamlit threads
- [x] Fix concurrent SQLite writes from progress polling thread (use separate connection)
- [x] Fix Docker Compose buildx warning (pre-built GHCR image + CI workflow)
- [x] Adaptive scheduling when rate limits are frequent
- [x] Download history with file count and bytes columns
- [x] Disk usage stats per artist in the dashboard
- [x] Pixiv support (URL patterns + OAuth refresh tokens)
- [x] DeviantArt support
- [x] Per-site auth adapter interface
- [x] Site-specific gallery-dl config overrides
