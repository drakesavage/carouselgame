# Crawler Console — architecture notes

Local-first web app for an AI-run Dungeon Crawler Carl campaign. Freeform chat with an AI GM is the core loop;
everything else keeps that GM honest, consistent and remembered.

## Decisions

| Decision | Why |
|---|---|
| **Runtime GM = Codex CLI** (`codex exec`), not the OpenAI API | Uses the player's ChatGPT/Codex sign-in, so no per-token API billing. Each GM step is an isolated, `--ephemeral`, `--sandbox read-only` job whose final message must match a JSON schema (`--output-schema`). Same binary lookup as Barcode Battles (desktop app's newest build in `%LOCALAPPDATA%\OpenAI\Codex\bin`, then `CODEX_BIN`, then PATH). |
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
2. A global write lock serializes turns; the turn row is created `processing` and the work runs in a thread.
   The UI polls `GET /api/turns/{id}` for stage and progress.
3. `memory/context.py` builds the context: position and floor clock, the PC's full mechanics, compact party, quests,
   shared stash, always-apply constitution sections, recent rulings, summaries, the last 12 messages, and, for
   characters named in the input or the recent scene, their sheet plus **GM-only companion sheet**.
4. Loop (max 5 rounds): the provider returns tool calls and/or narration → `ToolRunner` executes them → results go
   back. Bookkeeping-only calls plus narration finish in one round, so a typical turn is 1–2 Codex calls.
5. Everything is staged in **one SQLite transaction**: state changes, events, transcript, autosave. On failure it
   all rolls back, except dice, which are re-recorded against the failed turn so retrying can't fish for rolls.
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

## Extension points

* **New mechanics** → a pydantic op + `_apply_*` in `rules/engine.py` (combat rounds, crafting, grinding, mending,
  AI-Favor rerolls); the GM sees new ops automatically through `ops_reference()`.
* **New provider** → `gm/providers.py:make_provider`.
* **Embeddings** → add a ranked list to `reference/rulebooks.search` and fuse it.
* **More write-back fields** → add a mapping in `ledger/changes.py` (and its inverse in `importer/ledger.py`).
* **Images** → optional, never per turn: portraits are uploaded locally today. A Codex image job can be added
  behind a button (Codex supports image generation; it's disabled for GM jobs).
