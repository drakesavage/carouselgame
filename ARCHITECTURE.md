# Crawler Console — architecture notes

Local-first web app for an AI-run Dungeon Crawler Carl campaign. Freeform chat with an AI GM is the core loop;
everything else keeps that GM honest, consistent and remembered.

## Decisions

| Decision | Why |
|---|---|
| **Runtime GM = Codex CLI** (`codex exec`), not the OpenAI API | Uses the player's ChatGPT/Codex sign-in, so no per-token API billing. Each GM step is an isolated, `--ephemeral`, `--sandbox read-only` job with no tools (code-mode gateway disabled), the GM contract as developer instructions, a live event watcher, and a final message that must match a JSON schema (`--output-schema`). See [docs/GM_RUNTIME.md](docs/GM_RUNTIME.md). Same binary lookup as Barcode Battles (desktop app's newest build in `%LOCALAPPDATA%\OpenAI\Codex\bin`, then `CODEX_BIN`, then PATH). |
| Provider abstraction (`gm/providers.py`) | `codex` and `mock` today; a local model or another CLI only needs `complete()` + `summarize()`. |
| Tool protocol in JSON, executed by the server | The model *requests* checks, dice and state changes; Python validates and applies them. Narration can't change state. |
| **FastAPI serves the built React app** | Running only needs Python. Node is needed only to rebuild the UI (the Codex app ships one; `build-frontend.cmd` finds it). |
| **SQLite** (`data/campaign.db`, WAL) + separate `data/rulebooks.db` | Single-user, local, transactional, zero setup. The rulebook index is disposable and rebuildable. |
| Rulebook retrieval = **FTS5/BM25 + multi-query fusion** | Semantic-ish recall without paid embeddings: the GM model sends 2–4 phrasings (synonyms, rule names, plain-language situation) and results are rank-fused. Embeddings can be added later as another ranked list. |
| Importer is read-only and conservative | Hashes every file before and after; unknown values stay NULL; conflicts are reported, never silently resolved; the full ledger is kept verbatim as searchable `source_sections`. |

## Four systems

```
 Presentation (frontend/)          React + TS, Radix primitives, lucide icons. Player API only.
        │  HTTP /api (player)   /api/gm (token)  /media/portraits
 ┌──────┴──────────────────────────────────────────────────────────────┐
 │ AI Game Master (backend/app/gm)                                     │
 │   orchestrator.py  turn lifecycle, idempotency, atomic commit        │
 │   providers.py     codex | mock                                      │
 │   tools.py         skill_check, roll_dice, apply_changes, search_rules│
 │                    lookup_records, record_ruling, gm_note, ...       │
 │   prompts.py       GM contract (house rules come from the campaign)  │
 ├──────────────────────────────────────────────────────────────────────┤
 │ Rules Engine (backend/app/rules)                                     │
 │   dice.py    CSPRNG dice (secrets.randbelow), every roll recorded    │
 │   checks.py  Core Check, default DCs, Adv/Dis, Help, degrees,        │
 │              skill marks, 12-cycle advancement checkpoint            │
 │   engine.py  validated state ops → immutable events                  │
 ├──────────────────────────────────────────────────────────────────────┤
 │ Campaign Memory (backend/app/memory + db.py)                         │
 │   views.py      player vs GM read models (the visibility boundary)   │
 │   context.py    per-turn GM context (relevant slice, not history)    │
 │   snapshots.py  save points, restore, undo-restore                   │
 │   summaries.py  rolling transcript summaries                         │
 │   reference/rulebooks.py   PDF ingestion + retrieval with page refs  │
 │   importer/ledger.py       read-only Git-ledger importer             │
 └──────────────────────────────────────────────────────────────────────┘
```

## A turn

1. `POST /api/campaigns/{id}/turns {text, idempotency_key}` — a repeated key returns the stored result (no re-run).
2. A global lock serializes turns (a Python lock, not a database lock); the turn row is created `processing` and the work runs in a thread.
   The UI polls `GET /api/turns/{id}` for stage and progress.
3. `memory/context.py` builds the context: position and floor clock, the PC's full mechanics, compact party, quests,
   shared stash, always-apply constitution sections, recent rulings, summaries, the last 12 messages, and, for
   characters named in the input or the recent scene, their sheet plus **GM-only companion sheet**.
4. Loop (max 5 rounds): the provider returns tool calls and/or narration → `ToolRunner` executes them → results go
   back. Bookkeeping-only calls plus narration finish in one round, so a typical turn is 1–2 Codex calls.
5. **No write transaction is held while Codex runs.** Each round's tools run in a short transaction that is rolled
   back (staging); the commit replays every staged call with the same dice (`ReplayDice`) in **one** transaction with
   the transcript, events and autosave, after checking the state fingerprint is unchanged. A failure or a concurrent
   change commits nothing; drawn dice are recorded against the failed/interrupted turn so retrying can't fish for rolls.
6. Each `apply_changes` call is itself atomic: drinking a potion removes it *and* heals, or neither.

## Data model highlights

* State tables (rendered by the UI, captured by save points): campaigns, characters, skills, items,
  status_effects, achievements, npc_profiles, relationships, locations, quests, encounters, clocks, house_rules,
  rulings, lore_facts, gm_secrets, memory_summaries.
* Append-only logs (never deleted; restore only flags `rolled_back`): turns, messages, events, dice_rolls.
* `items.quantity IS NULL` means the count is not established (the ledger's "unresolved/unquantified").
  The engine refuses to spend it until `set_quantity` establishes a count.
* Visibility: `gm_secrets` (GM-only table) plus `visibility` columns on messages, events, lore_facts, clocks,
  relationships, locations, quests, encounters and source_sections; `hidden` on dice_rolls. Only `/api/gm/*`
  reads GM rows, and it requires `X-GM-Token` (from `GM_TOKEN` or `data/gm_token.txt`).
  `tests/test_visibility.py` plants secrets everywhere and crawls every player endpoint.

## GM briefing and memory layers (schema v2)

`memory/context.py` builds one bounded, structured briefing per GM call (about 15k tokens for the Denton campaign):
CAMPAIGN (identity, state version, clock, dungeon day, cycles, record conflicts) · CURRENT SCENE · PARTY (every group:
location, activity, members) · PRESENT (full sheets for the PC and everyone in the scene or the PC's group, plus
anyone named, with GM-only companion sheets and NPC memories) · OBJECTIVES (quests, projects, commitments, reservations,
due times) · SHARED STASH · ACTIVE COMBAT · MEMORY (scene summaries, transcript summaries, relevant canonical facts) ·
RULES (always-apply sections, matching house rules, recent rulings, auto-retrieved rulebook passages labelled "verify")
· GM-ONLY SECRETS (relevance-ranked) · RECENT TRANSCRIPT. The player's exact input is a separate step.

Presence comes from stored state (`scenes.participants`, `characters.assignment`), not name matching, and every op
accepts stable ids. Memory layers: immutable transcript (`messages`) · authoritative state · episodic scene summaries
(`memories` layer `episodic`, written when a scene closes) and transcript summaries (`memory_summaries`) · canonical
facts (`lore_facts`) · NPC memories (`memories` layer `npc`, visibility player or gm) · GM knowledge (`gm_secrets`) ·
rules (`house_rules`, `rulings`, rulebook index).

New validated ops (`rules/state_ops.py`): `skill`, `level`, `ai_favor`, `group`, `move_group`, `move_character`, `scene`,
`remember`, `use_ability`, `rest`; `advance_time` gained `mending` and dungeon-day resets; `quest` gained commitment and
reservation kinds plus `details`, `due_in_minutes` and `owner`; item ops accept `item_id`. Tools `grind` and
`resolve_floor_end` handle the dice-driven advancement rules. Existing databases migrate in place (`COLUMN_MIGRATIONS`);
`importer/organization.py` seeds groups, the scene, project details, reservations and promises from ledger text, once.

**Diagnostics:** every turn stores its context packet, tool rounds, Codex usage, game-time delta and a heuristic
integrity report (`gm/integrity.py`) in `turn_diagnostics`, readable only via `/api/gm/...` with the GM token
(*Saves & Settings → GM diagnostics*). Codex runs with no shell, hooks, sub-agents or MCP servers, and a result that ran
any command is rejected.

## Combat (`backend/app/rules/combat.py`)

The encounter row's `combatants_json` holds `{round, phase, floor, declared, combatants}`. Party combatants
reference character rows (Health lives only there); foes carry their stat block (slot values, Evade, DR,
resistances, Actions, attacks). Phases: `surprise | mob_declaration → crawler_reaction → crawler_action → (end_round)`.
Each GM tool (`combat_start`, `combat_declare_mob_attacks`, `combat_evade`, `combat_resolve_mob_attacks`,
`combat_attack`, `combat_action`, `combat_damage`, `combat_end_round`, `combat_end`, `heal_slots`) validates the
phase and the Action economy, rolls with the CSPRNG, and records every roll. `public_encounter()` is the
player-safe projection (names, foe HB %, own Actions/HP); the GM context gets the full state.

## Ledger write-back (`backend/app/ledger/`)

```
 campaign.db ──changes.py──► field-level edits ──sync.py──► three-way check ──yamledit.py──► surgical text edits
 (console state)   inverse of importer        base = last import/sync commit    re-parse must equal intent
                                              theirs = ledger working tree      ──► validate_state.py ──► git commit
```

* **Baseline**: `git show <commit>:<path>` for the commit recorded at import (`import_reports`) or the last sync
  (`ledger_syncs.new_commit`). Only fields whose console value differs from the baseline become edits, so the
  write-back never rewrites history it didn't change. An untouched campaign proposes zero edits (tested on the real ledger).
* **Three-way**: ledger value == baseline → clean; == console value → already applied (dropped); otherwise conflict
  (shown, never written).
* **Surgical YAML** (`yamledit.py`): ruamel position data locates each node; only that entry's lines are replaced
  with a freshly rendered fragment (width 120, sequences not indented, `null`, matching the ledger's style). The
  result is re-parsed with PyYAML and must equal the baseline data with the edits applied structurally, or nothing is
  written. Flow collections (`{a: 1}`) are rewritten whole.
* **Append-only logs** (`rolls/*.jsonl`, `gm_state/console_notes.yaml`, reconciliation entries) use high-water ids
  stored per sync, so each roll, note or ruling is written once. Rows created in the console (quests, items, skills)
  are linked to their new ledger ids after the commit.
* **Apply guards**: the preview fingerprint (HEAD, file hashes, change ids) must still match; the working tree must
  be clean and not behind upstream; author name and email are required. Validator failure → files restored, index
  reset, no commit. Push and pull are explicit, separate endpoints.

## Campaign art (`backend/app/art/`)

`comfy.py` is a small ComfyUI client: starts the portable install if needed, queues an SDXL text-to-image graph, polls
`/history`, fetches the image through `/view` and calls `/free` when the queue empties (same server and pattern as
Barcode Battles). `service.py` builds prompts from player-visible records only, stores jobs in `art_jobs`
(`suggested → queued → running → done | failed | dismissed`), and runs one worker thread. Installing a character
portrait takes the GM's turn lock, so a render finishing mid-turn can't change the turn's state fingerprint.
`art_jobs` is not part of save points (pictures survive restores). GM suggestions are checked against unrevealed GM
secrets (6-word overlap) and need the player's approval before anything renders.

## Extension points

* **New mechanics** → a pydantic op + `_apply_*` in `rules/engine.py` (combat rounds, crafting, grinding, mending,
  AI-Favor rerolls); the GM sees new ops automatically through `ops_reference()`.
* **New provider** → `gm/providers.py:make_provider`.
* **Embeddings** → add a ranked list to `reference/rulebooks.search` and fuse it.
* **More write-back fields** → add a mapping in `ledger/changes.py` (and its inverse in `importer/ledger.py`).
* **Images** → optional, never per turn: portraits are uploaded locally today. A Codex image job can be added
  behind a button (Codex supports image generation; it's disabled for GM jobs).
