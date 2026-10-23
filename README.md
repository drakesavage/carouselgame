# Crawler Console

A local, browser-based HUD for playing the Denton *Dungeon Crawler Carl* campaign with an AI Game Master.
You type what Cliff does, freeform, and the GM narrates. Behind it, real dice, a rules engine, and the campaign
database keep the story consistent and auditable.

- **AI GM through the Codex CLI**, using your ChatGPT/Codex sign-in. No OpenAI API key, no per-token billing.
- **Real dice**: OS cryptographically secure RNG, every roll logged, never a model-generated number.
- **Validated state**: HP, Mana, items, time, quests and Levels change only through checked operations.
- **Your campaign imported read-only** from the Git ledger (`drakesavage/dccrepository`), including private
  GM companion sheets, which never reach the player view.
- **Rulebook search** across your local DCC PDFs, with book and page references.
- Save points, rollback (with undo), autosave every turn.

See [ARCHITECTURE.md](ARCHITECTURE.md) for how it fits together.

## Run it (Windows)

Double-click **`start.cmd`**, or from a terminal:

```bat
start.cmd
```

It creates `.venv` and installs Python packages on first run, builds the interface if needed, starts the server and
opens <http://127.0.0.1:8777>. Stop it with Ctrl+C.

On first start the app seeds a clearly labelled **DEMO** campaign, imports the ledger from `CAMPAIGN_LEDGER_DIR`
as a new campaign, and indexes the PDFs in `RULEBOOK_DIR` (about 10 seconds).

### Requirements

| Need | Notes |
|---|---|
| Python 3.10+ | Required. |
| Codex app/CLI, signed in | For real play. Found automatically (Codex desktop app build, or `CODEX_BIN`, or `codex` on PATH). Switch to **Mock GM** in *Saves & Settings* to test offline. |
| Node.js | Only to rebuild the UI after changing `frontend/`. `build-frontend.cmd` uses the Node bundled with the Codex app if there's no system Node. |
| **No API keys** | None are needed. |

### Configuration (`.env`, git-ignored; see `.env.example`)

```ini
GM_PROVIDER=codex                       # or mock
CODEX_MODEL=                            # blank = your Codex default
CODEX_REASONING_EFFORT=medium
RULEBOOK_DIR=C:\Users\cliff\Downloads\DCC Rulebooks
CAMPAIGN_LEDGER_DIR=E:\dccrpg\campaign_sources\dccrepository
```

Provider, model and effort can also be changed live in *Saves & Settings*.

### Local files

| Path | What |
|---|---|
| `data/campaign.db` | All campaign state, transcript, events, dice audit, save points. Back this file up. |
| `data/rulebooks.db` | Rulebook text index (rebuildable; never commit). |
| `data/portraits/` | Character portraits you upload from a character sheet. |
| `data/gm_token.txt` | Token for the GM-only API (`/api/gm/...`, header `X-GM-Token`). |
| `data/codex_jobs/` | The last 60 Codex prompts/outputs, for debugging. They include GM secrets, so keep them local. |
| `reports/migration_report.md` | What the importer brought in, conflicts, and what needs review. |
| `campaign_sources/dccrepository` | A separate read-only clone of the ledger (your original clone is untouched). |

## Tests

```bat
run-tests.cmd
```

These cover dice integrity and the audit log, checks and degrees, the advancement checkpoint, validated state
changes, turn persistence, idempotent retries, failed-turn rollback, atomic change batches, save/restore/undo,
autosave pruning, GM/player data separation (including the built JS bundle), rulebook ingestion and page references,
campaign import (fixture ledger plus your real ledger when present), and ledger write-back (minimal diffs,
idempotence, conflicts, validator rollback, and a round trip through your real ledger's own validator, run on throwaway copies).

## Playing

* **Story**: type anything and press **Act** (Ctrl+Enter). Quick actions only pre-fill text. **Previously on…** asks
  for a recap. Roll cards expand to show dice, modifiers, helpers and DC. *Ledger updates* list exactly what changed.
  Every GM message ends with the floor clock.
* **Characters / People**: click anyone for the full sheet (stats, mods, skills with advancement marks, spells,
  attacks, gear, the full imported record). Upload portraits there.
* **Inventory**: items whose count the ledger never established show **qty unknown**. The GM must establish a count
  before spending them (your ammunition is in this state).
* **Rules**: search house rules and the PDFs together, open full pages, and browse the constitution with each
  section's mechanical-implementation status.
* **Records**: the imported ledger, verbatim and searchable (player-visible files only).
* **Saves & Settings**: manual saves, restore (always reversible), GM provider, re-import, latest migration report.
* **Ledger**: writes play back to the Git campaign ledger (`dccrepository`) as one reviewed commit. See below.

## Writing play back to the ledger

The ledger stays the source of truth for your other sessions (e.g. ChatGPT). After a play session, open **Ledger**:

1. **Review.** The console compares the campaign with the ledger commit it last imported or synced, and lists each
   change: HP and Mana, items and counts, Skill ranks and advancement marks, statuses, Levels, Stat points, floor
   clock and dungeon-day fields (plus the roster mirror), location, objectives, world notes, rulings, achievements,
   the advancement-cycle reconciliation entry, and roll records (`rolls/public.jsonl`). GM-only notes and hidden
   rolls go to `gm_state/` and are hidden in this screen.
2. **Conflicts.** If the ledger changed the same field since the last sync (another session committed to it), that
   change is marked as a conflict and is not written. Reconcile it by hand, then refresh.
3. **Write & commit.** Only the checked changes are written. Edits are surgical: untouched lines stay byte-for-byte
   identical. The ledger's own `tools/validate_state.py` must pass, or every file is restored and nothing is committed.
4. **Push** is a separate button with a confirmation. Nothing leaves your machine until you press it.
   If the remote has newer commits, **Pull (fast-forward)** first.

`checkpoints/latest.md` is **not** regenerated unless you tick the box. Your current checkpoint has hand-written
sections (e.g. the DCC-DENTON-015 advancement resolution) that `tools/build_checkpoint.py` would replace; the screen
warns about this.

Not written automatically yet (the screen lists any that apply): equipment-slot changes, NPC attitude, encounters,
and characters created in the console.
