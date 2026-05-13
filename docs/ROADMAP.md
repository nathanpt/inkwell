# Inkwell Roadmap

## Planned

- [ ] MkDocs + Material theme documentation site, deployed to GitHub Pages
- [ ] Image gallery integration (Piwigo, Komga)
- [ ] Discord/Telegram notifications on new archival
- [ ] Automated SQLite dumps and NAS snapshot scheduling

## Completed

- [x] Fix concurrent SQLite writes from progress polling thread (use separate connection)
- [x] Fix Docker Compose buildx warning (pre-built GHCR image + CI workflow)
- [x] Adaptive scheduling when rate limits are frequent
- [x] Download history with file count and bytes columns
- [x] Disk usage stats per artist in the dashboard
- [x] Pixiv support (URL patterns + OAuth refresh tokens)
- [x] DeviantArt support
- [x] Per-site auth adapter interface
- [x] Site-specific gallery-dl config overrides
