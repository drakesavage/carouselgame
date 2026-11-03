# Carousel — architecture notes

Local-first web app for a single-player horror roleplaying game set in Carousel, the town from Rob M. Lastrel's
*The Game at Carousel*. Freeform chat with an AI Game Master is the core loop; everything else keeps that GM honest,
consistent and remembered. Forked from Crawler Console (`E:\dccrpg`); the two share nothing at runtime.

## Decisions

| Decision | Why |
|---|---|
| **GM runtime = Codex CLI** for mechanics and Omen turns, **Gemini** for quiet turns (`gm/router.py`) | Uses the player's own sign-ins, no per-token API billing. Each Codex step is an isolated, `--ephemeral`, read-only job with no tools, the GM contract as developer instructions, and a final message that must match a JSON schema. `mock` runs the tests. |
| Tool protocol in JSON, executed by the server | The model *requests* state changes; Python validates and applies them as ops. Narration can't change state. |
| **FastAPI serves the built React app** | Running only needs Python. Node is needed only to rebuild the UI (`build-frontend.cmd`). |
| **Carousel source data is imported, never edited** | `E:\Carousel` is read into a rebuildable `data/reference.db`; campaigns store only stable IDs (`CAR-…`, `OM-…`, `CAR-NPC-…`). |
| **SQLite**: `data/campaign.db` (WAL), `data/reference.db`, `data/rulebooks.db` | Single-user, local, transactional, zero setup. The two reference files are disposable and rebuilt by the importer. |
| Rule search = **FTS5/BM25 + multi-query fusion** over the Carousel bibles | Semantic-ish recall without paid embeddings: the GM sends 2–4 phrasings and the results are rank-fused. |

## Systems

```
 Presentation (frontend/)          React + TS, Radix primitives, lucide icons. Player API only.
        │  HTTP /api (player)   /api/gm (token)  /media
 ┌──────┴──────────────────────────────────────────────────────────────────┐
 │ AI Game Master (backend/app/gm)                                         │
 │   orchestrator.py  turn lifecycle, idempotency, atomic commit            │
 │   providers.py     codex | gemini | mock;  router.py picks per turn      │
 │   tools.py         apply_changes, search_rules, lookup_records, ...      │
 │   prompts.py       the GM contract                                       │
 ├──────────────────────────────────────────────────────────────────────────┤
 │ Rules (backend/app/rules)          validated ops → immutable events      │
 │   engine.py  state_ops.py  travel_ops.py  venue_ops.py  story_ops.py     │
 │   story_venue_ops.py  fate_ops.py  opening_ops.py  resident_ops.py       │
 ├──────────────────────────────────────────────────────────────────────────┤
 │ Carousel (backend/app/carousel)    the town as data and engine           │
 │   importer.py + import_*.py   source packages → reference.db             │
 │   travel.py  interruptions.py  venues.py  stories.py  fate.py            │
 │   residents.py  opening.py  clock.py                                     │
 ├──────────────────────────────────────────────────────────────────────────┤
 │ Campaign memory (backend/app/memory + db.py)                             │
 │   views.py      player vs GM read models (the visibility boundary)       │
 │   context.py    per-turn GM briefing (a bounded slice, not history)      │
 │   snapshots.py  save points, restore, undo-restore                       │
 │   reference/rulebooks.py   the Carousel bibles, indexed by section       │
 └──────────────────────────────────────────────────────────────────────────┘
```

## Reference data

`import-data.cmd` runs `carousel/importer.py`: read every source package, build `reference.db.new` with every importer,
check the sources didn't change while reading, check that every ID a saved campaign uses still resolves (aliases
count), then swap it in. Any failure leaves the old file in place; each run writes `data/import_reports/<time>.json`.
A successful import also rebuilds `rulebooks.db` from the six bibles (System, Role, Trope, Trope Engine, Screenplay,
Production Standard), the same build-then-swap way. Only **installed** data is imported, never staged repair
candidates. Map tiles are served straight from `Illustrated_Map_Production/web_tiles/`.

## A turn

1. `POST /api/campaigns/{id}/turns {text, idempotency_key}`: a repeated key returns the stored result.
2. A global lock serializes turns; the turn runs in a thread and the UI polls `GET /api/turns/{id}`.
3. `memory/context.py` builds the briefing (below).
4. Loop: the provider returns tool calls and/or narration, `ToolRunner` executes them, results go back.
5. **No write transaction is held while a model runs.** Each round's tools run in a short transaction that is rolled
   back (staging); the commit replays every staged call in **one** transaction with the transcript, events and
   autosave, after checking the state fingerprint is unchanged. A failure or a concurrent change commits nothing.
6. Each `apply_changes` call is itself atomic.

## GM briefing

One bounded, structured briefing per GM call: CAMPAIGN · CURRENT SCENE · STORY (or OMENS HERE) · OMEN LEADS (GM-only:
item-bound and special Omens anchored where the group stands) · VENUE · OPENING · FATE · CAMP RETURN · PARTY · AROUND ·
PRESENT · OBJECTIVES · MEMORY · HOUSE RULES · RULEBOOK (auto-retrieved bible passages, "verify before citing") ·
GM-ONLY SECRETS (the active story's sealed truth, never cut) · RECENT TRANSCRIPT. Every section has a character budget;
anything cut is listed in `omissions`. Presence comes from stored state, not name matching, and every op accepts stable
IDs.

## The player/GM boundary

* GM-only material: `gm_secrets`, `visibility = 'gm'` rows (messages, events, lore, clocks, relationships, quests),
  sealed story dossiers, Omen leads and spawn conditions, unreached venue zones, death causes and rescue eligibility.
* Only `/api/gm/*` reads it, and it requires `X-GM-Token` (from `GM_TOKEN` or `data/gm_token.txt`).
* `tests/test_visibility.py` plants unique strings everywhere GM knowledge can live and crawls every player endpoint.
* `tests/test_no_dcc_text.py` keeps Crawler Console wording out of every prompt sent to a model.

## Campaign art (`backend/app/art/`)

`comfy.py` drives a local ComfyUI (SDXL); `codex_image.py` uses Codex image generation. `service.py` builds prompts
from player-visible records only and stores jobs in `art_jobs` (`suggested → queued → running → done | failed |
dismissed`). GM suggestions need the player's approval before anything renders. `art_jobs` is not part of save points.

## Extension points

* **New mechanics** → a pydantic op + `_apply_*` registered in `rules/__init__.py`; the GM sees it through `ops_reference()`.
* **New provider** → `gm/providers.py:make_provider`.
* **Embeddings** → add a ranked list to `reference/rulebooks.search` and fuse it.
* **Map or data fixes** → edit `E:\Carousel`, then `import-data.cmd`; no code change.
