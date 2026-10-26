# Operations: backups, restore and rollback

Two Git repositories are involved, and they are never mixed:

| Repository | Path | Remote | Holds |
|---|---|---|---|
| **Application** (Crawler Console) | `E:\dccrpg` | `drakesavage/dccweb` | code, tests, docs. `data/` is git-ignored |
| **Campaign ledger** | `E:\dccrpg\campaign_sources\dccrepository` | `drakesavage/dccrepository` | the curated YAML story bible, rolls, checkpoints |

SQLite (`data/campaign.db`) is authoritative during play. The ledger receives play only through the reviewed
write-back (Ledger view: preview → select → commit), never automatically.

## Backups

```bat
cd backend
..\.venv\Scripts\python -m app.backup create before-something
..\.venv\Scripts\python -m app.backup verify ..\data\backups\<folder>
```

A backup (`data/backups/<timestamp>-<label>/`) is a consistent online copy of `campaign.db` (safe while the server
runs) plus `art/`, `portraits/`, `assets/`, `visual_reference/` and `settings.json`, with a manifest of SHA-256 hashes
and per-table row counts. `verify` re-hashes every file, runs `PRAGMA integrity_check` and compares row counts. The GM
token and the device passcode are not copied.

Take one before every milestone, schema change, import or ledger write-back. Separately, the app keeps in-database save
points (Saves & Settings), which roll campaign state back without touching code or media.

## Restoring

`restore` only writes into a new or empty folder, so it can never overwrite live data by accident:

```bat
..\.venv\Scripts\python -m app.backup restore ..\data\backups\<folder> E:\dccrpg\data-restored
```

To switch the live game to the restored copy:

1. Stop the server (close the preview / `run.cmd` window).
2. Rename `data` to `data-broken-<date>` (keep it until you're sure).
3. Rename `data-restored` to `data`, and copy back `gm_token.txt` and the passcode file from the old folder if you
   want the same GM token and device passcode.
4. Start the server. On startup it applies any schema migrations in place and marks interrupted turns as failed.

Alternatively, point the server at the restored folder without renaming anything: set `CRAWLER_DATA_DIR` in `.env`.

## Managing campaigns

Saves & Settings → Campaigns (or "Manage campaigns…" in the header picker): start a new campaign, open, rename, archive
(hidden from the picker, nothing removed) and delete. A new campaign has one crawler and an opening save point; stats,
skills and gear start empty for character creation in play.

Delete works only on the game PC, needs the exact name typed, and is refused while a turn is running. It first makes a
database backup named `before-delete-<name>` and moves the campaign's art (`data/art/c<id>`) and portraits into that
backup folder. To get a deleted campaign back, restore that backup into a new folder as above and copy
`art/c<id>` and `portraits/` back into the restored `data`. Campaign ids are never reused, and the Git ledger is never
touched by a delete.

## Rolling back code

Each milestone is one commit on `main` in dccweb. To return to the previous version:

```bat
git log --oneline -10
git revert <commit>        (preferred: keeps history)
```

Schema migrations only add tables and columns (`db.py COLUMN_MIGRATIONS`), so older code runs against a newer
database. If a migration ever changes existing data, restore the backup taken before it.

## Ledger safety rules (enforced)

* Write-back refuses when the ledger working tree is dirty, behind its upstream, or changed since the preview.
* `checkpoints/latest.md` is never regenerated when it contains hand-written sections. Edit it by hand instead.
* Pull and push are separate, explicit buttons. Nothing is pushed automatically.
