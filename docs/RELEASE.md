# Release and rollback checklist

Use this for every milestone or any change that touches the schema, the GM, or campaign data.
Details for each backup/restore step are in [OPERATIONS.md](OPERATIONS.md).

## Before

1. Take a backup and verify it (Settings → Backups → "Back up now", or `python -m app.backup create before-<change>`).
2. Note the current commit: `git log --oneline -1`.
3. Make sure no turn is processing (the story view shows no active turn).

## Test

1. Backend: `cd backend && ..\.venv\Scripts\python -m pytest -q` (all pass).
2. Frontend: `cd frontend && npm run build` (typecheck + build pass).
3. Anything that changes campaign state is tried on a disposable copy first, never on `data/campaign.db`.
4. Browser check of the changed views at desktop and phone widths; the consistency check (Settings) is all green.

## Release

1. Commit one milestone per commit on `main`; push to dccweb.
2. Restart the server. Startup applies column/table migrations in place and marks interrupted turns as failed.
3. Hard-refresh the browser (index.html is served no-cache, so a normal reload is usually enough).
4. Play one turn and confirm it completes; open the consistency check.

## If something is wrong

| Problem | Roll back |
|---|---|
| A bad turn or GM mistake | Story: undo the turn, or restore a save point (preview first) |
| Code regression | `git revert <commit>`, restart. Migrations only add tables/columns, so older code runs on the newer database |
| Damaged or wrong data | Stop the server, restore the pre-release backup into a new folder, swap folders (OPERATIONS.md → Restoring) |
| Ledger write-back mistake | Revert the write-back commit in dccrepository; the app database is unaffected |

Keep the broken `data` folder until the restored one has been played for a while.

## Never

- Commit `data/`, `.env`, `campaign_sources/`, story history, PDFs, or key files.
- Write to the ledger repo except through the reviewed write-back.
- Test against the live database.
