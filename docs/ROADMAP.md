# Inkwell Roadmap

## Planned

- [x] Show last download time per artist in the single-artist dropdown
- [x] Add per-artist Download button in Artists tab; remove single-artist dropdown from Downloads tab
- [x] Paginate or collapse artist list to prevent UI bloat at scale
- [x] Interactive architecture flow diagram (docs/architecture.html)
- [x] Interactive workflow diagram with JSON-driven flows (docs/workflows.html)
- [ ] MkDocs + Material theme documentation site, deployed to GitHub Pages
- [ ] Image gallery integration (Piwigo, Komga)
- [ ] Discord/Telegram notifications on new archival
- [x] Download scheduling with time windows and stale-only filtering
- [ ] Automated SQLite dumps and NAS snapshot scheduling
- [x] Add functionality to auto-zip downloaded media per artist per year to reduce small-file load on NAS hard drives. This should be able to be performed at the end of a job and retroactively. functionality to disallow re-downloading already downloaded files must be maintained.

## Completed

- [x] Accept Pixiv URLs with sub-section paths (/illustrations, /artworks, etc.)
- [x] Fix gallery-dl archive path to prevent re-downloads after zipping
- [x] Replace expander-based UI with tabs for better navigation
- [x] Add artist search/filter bar and reduce page size to 10
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
