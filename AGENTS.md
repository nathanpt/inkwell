# Inkwell Agent Instructions

## Context Sources

Before non-trivial changes, read:

- **`docs/DESIGN.md`** — canonical architecture/system map (stack, data model, components, deployment). Update it rather than forking a second architecture doc.
- **`PROGRESS.md`** — live status: working surfaces, active work, open tech debt, and verification results (test counts, CI gaps).
- **`docs/ROADMAP.md`** — planned vs. completed work (the post-task checklist below updates this).

State your intended scope, affected files, and verification plan before implementing. Preserve established patterns unless there is a documented reason to change them. "It runs" / "it compiles" is a baseline, not completion — run the verification pipeline and report exact commands + results honestly.

## Post-Task Checklist

After completing any roadmap task without errors, update `docs/ROADMAP.md`:

1. Find the relevant roadmap item
2. Change `- [ ]` to `- [x]`
3. If the item doesn't exist on the roadmap, do not add it
4. Commit the changes.

Do not skip this step. The roadmap should always reflect the current state of the project.

## Git

- **Never push to the remote unless the user explicitly instructs you to.** Commits stay local; the operator pushes deliberately to control CI build usage (GitHub Actions minutes are a constrained resource). When work is ready, commit it locally and tell the user it is ready to push — do not run `git push` on your own. This applies even if a prior step or checklist says "commit": commit, then stop and let the user push.

## Environment

- **This machine is not the production server.** The production Docker container runs on a separate server. Do not assume the local Docker daemon is running the app. Diagnostic commands like `docker exec` or `docker logs` cannot be run here — provide them as instructions for the user to run on the production server instead.

## Project Conventions

- **Storage layout:** Media is organized as `/nas/inkwell/{artist_handle}/{year}/`
- **Config files:** Bind-mounted read-only from repo (`config.toml`, `gallery-dl.conf`)
- **Database:** SQLite with WAL mode in a named Docker volume (`/app/data/inkwell.db`)
- **gallery-dl:** Invoked as a subprocess, never as a library
- **archive.db:** Fully owned by gallery-dl — Inkwell never reads or writes it
- **Tests:** Run `.venv/bin/python -m pytest tests/ -v` before committing
