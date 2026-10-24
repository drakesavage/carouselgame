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

### From other devices (through Barcode Battles)

Barcode Battles forwards `/dcc` on its port to Crawler Console, so the household uses one address:

* This PC: <http://localhost:8787/dcc/> works without a passcode, as does <http://127.0.0.1:8777>.
* Phones and other devices: `http://<this PC's address>:8787/dcc/`. They need the **passcode** once, and a cookie
  remembers it. *Saves & Settings → Other devices* shows the exact address and the passcode, and can set a new one,
  which signs every device out. To fix the passcode yourself, set `CRAWLER_PASSCODE` in `.env`.
* Both apps must be running. If Crawler Console is stopped, `/dcc` shows a page saying so.
* Crawler Console itself listens only on 127.0.0.1. Barcode always replaces `X-Forwarded-For` with the real client
  address, so another device can't pretend to be this PC. Eight wrong passcodes lock that device out for 10 minutes.
  GM-only pages still need the GM token.

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
`test_state_v2.py` covers the v2 ops; `test_campaign_regression.py` replays the Denton campaign rules (skills, splits,
reservations, the siphon project, NPC privacy, restart continuity) on a copy of your real ledger, never the live database.

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

The Story view is a HUD: a scene banner (title, location, conditions, threats, who is present), dialogue with
portraits, system announcements, discovery cards for loot, quests and level-ups, and a separate *Mechanics* block for
rolls and ledger updates. The sidebar groups the party by where each group is and what it's doing, with role badges.
Animations respect the OS "reduce motion" setting. Optional art goes in `data/assets/` and is listed in
`data/assets/manifest.json` (created on first start); nothing is generated per turn.

**Party splits, scenes and memory.** The GM tracks groups (`group`, `move_group`, `move_character`), the current scene
(`scene`; closing one writes a summary into memory), NPC memories (`remember`), limited-use abilities (`use_ability`),
rest (`rest`, saferooms only), skill training (`skill`) and level awards (`level`). Projects, promises and reservations
are quests with details and due times, so a reserved item is never counted as owned. On first start after upgrading,
the importer seeds groups, the scene, the siphon project, the Personal Stabber reservation and Scout's promise from
the ledger text. See [docs/AUDIT.md](docs/AUDIT.md) for what is verified and what is still open.

**GM runtime.** GM jobs run with no tools (shell, files, web, agents and apps all disabled; the code-mode gateway fails
closed), a live watcher that kills a job at its first unauthorized event, and no prompt files kept on disk. Check it after
any Codex update with `..\.venv\Scripts\python -m app.gm.isolation_probe` (from `backend\`). Details, measurements and the
remaining exposure (your global `~/.codex/AGENTS.md` is still injected, but neutralized) are in
[docs/GM_RUNTIME.md](docs/GM_RUNTIME.md).

**Gemini (optional).** *Saves & Settings → AI Game Master → Gemini API* uses Google's Gemini API instead of Codex.
Put the key in `GEMINI_API_KEY` or `data\gemini_api_key.txt` (git-ignored). The free tier is rate-limited, and Google
may use free-tier inputs, which include GM secrets, to improve its products. See docs/GM_RUNTIME.md §6.

**GM diagnostics** (*Saves & Settings → GM diagnostics*, needs the GM token from `data/gm_token.txt`): each turn's
full context packet, tool rounds, Codex usage, game time elapsed, and an integrity report that flags rejected changes,
gold/HP/item/time mentions that don't match the ledger updates, counted cycles on a recap, and leaked GM-only text.

## Combat

Fights run on the book's round sequence (CORE_MECHANICS §10–11). The GM declares the Mob Attacks and then stops to
ask what Cliff does: **Evade**, another Interrupt, or nothing. Then everything resolves in code:

* **Evade** spends an Action: d20 + your Evade vs each Attack's to-hit. Success avoids it (Area Attacks: half
  damage). A Natural 20 or Amazing Success gives Advantage on your next Attack against that Mob. A Major Fail adds
  Floor damage plus a Minor Injury after combat; a Critical Fail doubles the damage.
* **Attacks** use the attack lines on your sheet: d20 + Skill Rank + Stat Mod vs the Mob's Evade. A Natural 20
  doubles the base damage dice; an Amazing Success adds the Floor number. The Skill gets its advancement mark.
* **Damage** applies DR, then resistances, then whole Health Bar slots (damage smaller than one slot is lost,
  Core p. 94). Mobs die at 0%; crawlers become Dying, and healing (always by slots) brings them back.
* Two Actions per crawler per round are enforced. Low-quality Mobs get one Attack; Elites and Bosses get one Action
  per participating crawler.
* The combat panel shows the round, phase, your Action pips, and foes' Health Bar % only. Stat blocks stay GM-only.

**Open rules question:** the Dying countdown is CON on Core p. 96 but CON Mod in Table 11 (p. 99). The app uses
CON Mod for now (labelled INTERPRETATION); tell the GM if your table uses CON.

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
