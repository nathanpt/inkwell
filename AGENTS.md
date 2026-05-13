# Inkwell Agent Instructions

## Post-Task Checklist

After completing any task without errors, update `docs/ROADMAP.md`:

1. Find the relevant roadmap item
2. Change `- [ ]` to `- [x]`
3. If the item doesn't exist on the roadmap, add it under the appropriate section before marking it complete
4. Commit the changes.

Do not skip this step. The roadmap should always reflect the current state of the project.

## Project Conventions

- **Storage layout:** Media is organized as `/nas/inkwell/{artist_handle}/{year}/`
- **Config files:** Bind-mounted read-only from repo (`config.toml`, `gallery-dl.conf`)
- **Database:** SQLite with WAL mode in a named Docker volume (`/app/data/inkwell.db`)
- **gallery-dl:** Invoked as a subprocess, never as a library
- **archive.db:** Fully owned by gallery-dl — Inkwell never reads or writes it
- **Tests:** Run `.venv/bin/python -m pytest tests/ -v` before committing
