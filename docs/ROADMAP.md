# Inkwell Roadmap

## In Progress

- [ ] First-run password setup wizard (alternative to `.env` approach)

## Planned

- [ ] MkDocs + Material theme documentation site, deployed to GitHub Pages
- [x] Disk usage stats per artist in the dashboard
- [x] Download history with file count and bytes columns
- [x] Adaptive scheduling when rate limits are frequent

## Phase 5 — Multi-Site (Future)

> No architectural changes needed — the artist model is site-agnostic. Adding new sites requires new URL patterns and auth, not a re-architecture.

- [x] Pixiv support (URL patterns + OAuth refresh tokens)
- [x] DeviantArt support
- [x] Per-site auth adapter interface
- [x] Site-specific gallery-dl config overrides
- [ ] Image gallery integration (Piwigo, Komga)
- [ ] Discord/Telegram notifications on new archival
- [ ] Automated SQLite dumps and NAS snapshot scheduling
