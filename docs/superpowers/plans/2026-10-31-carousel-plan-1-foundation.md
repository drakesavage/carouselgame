# Carousel Plan 1 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fork Crawler Console into `E:\CarouselGame`, strip everything DCC-specific (dice included), and build a re-runnable importer that loads every Carousel data package into a rebuildable reference database.

**Architecture:** A git clone of `E:\dccrpg` keeps the orchestrator, providers/router, op engine, memory, visibility boundary and backups. DCC rules, dice, ledger and combat are deleted. Carousel source data is read (never written) from `E:\Carousel` into `data/reference.db`, a disposable DB that sits beside `data/campaign.db` the way `rulebooks.db` already does. Campaign state refers to it only by stable IDs, so a re-import after a map fix can be validated against saved campaigns before it is swapped in.

**Tech Stack:** Python 3.10+, FastAPI, SQLite (WAL), pydantic v2, pytest; React/TypeScript + Vite frontend (unchanged toolchain).

**Spec:** [docs/2026-10-30-carousel-game-design.md](../../2026-10-30-carousel-game-design.md) §3–§5, §14 steps 1–2. Roadmap: [2026-10-31-carousel-roadmap.md](2026-10-31-carousel-roadmap.md).

## Global Constraints

- `E:\dccrpg` must not be modified in any way: no commits, no file changes, no shared data dir. The fork is a `git clone`.
- Never write under `E:\Carousel`. All source access is read-only.
- Port **8797** (Crawler Console uses 8777). Data dir env var `CAROUSEL_DATA_DIR`, default `<repo>/data`. Source dir env var `CAROUSEL_SOURCE_DIR`, default `E:\Carousel`.
- No dice anywhere: no `dice.py`, no `dice_rolls` table, no roll tools, no replay-with-same-dice. The staged atomic commit stays.
- GM providers: `codex`, `gemini`, `mock`, unchanged.
- Coordinates are stored `[north, west]` in 0–1000. Pixels: `x = (1000 − west) × 12`, `y = (1000 − north) × 12` on the 12000² master.
- Omen `Map X / 1000` and `Map Y / 1000` are never imported.
- NPC `gm_only` content and Omen sealed truth never reach a player endpoint.
- Run tests with `run-tests.cmd` (→ `.venv\Scripts\python.exe -m pytest -q` in `backend/`).

## Review Focus

1. **Re-import after a map fix drops or renames an ID a saved campaign uses** → refused with the missing IDs listed; the old `reference.db` keeps serving. (Task 9: `test_reimport_refuses_missing_referenced_id`)
2. **A source file changes while it's being imported** (Codex or the studio writing to `E:\Carousel`) → the hash is checked again after reading, a mismatch aborts, nothing is swapped. (Task 9: `test_import_aborts_if_source_changes_midway`)
3. **Names with typographic or broken apostrophes** (`Patcher’s`, and `U+FFFD` where an encoding went wrong) → anchor matching normalises both, so `Berryman’s Dive` resolves. (Task 6: `test_anchor_normalisation`)
4. **Both games running at once** → different port, data dir, GM token and Codex job folder; nothing defaults to a `dccrpg` path. (Task 1: `test_settings_isolated_from_crawler_console`)
5. **GM-only content reaching the player** through any endpoint, including the new reference status endpoint. (Task 10: `test_visibility.py` planted secrets plus sealed truth and NPC `gm_only` text)

---

## File structure

```
E:\CarouselGame\
  start.cmd, serve.cmd, run-tests.cmd, build-frontend.cmd   # port 8797, "Carousel" titles
  docs\                                                     # design, roadmap, this plan (copied in Task 1)
  backend\app\
    config.py            # CAROUSEL_* env, reference_db, source_dir
    db.py                # Carousel campaign schema (DCC tables removed)
    carousel\
      __init__.py
      sources.py         # SOURCE_FILES manifest, hashing, snapshot
      refdb.py           # reference.db schema + connect
      coords.py          # [north, west] <-> pixel transform
      text.py            # name normalisation
      import_places.py   # locations, gazetteer, graph, anchors
      import_omens.py    # registry + beat dossiers + anchor resolution
      import_roles.py    # roles, aspects, advanced, tickets, profiles, trope bible
      import_npcs.py     # 70 resident templates
      importer.py        # build-validate-swap + CLI
      refs.py            # IDs referenced by campaign state
    reference_api.py     # /api/reference/status, /api/gm/reference/*
  backend\tests\
    carousel\conftest.py # real-source fixture (skips if E:\Carousel missing) + tiny synthetic source
    carousel\test_*.py
```

Deleted from the fork: `app/rules/{dice,checks,combat,crafting,traps,advancement,coverage,party}.py`, `app/importer/{ledger,organization}.py`, `app/ledger/`, `app/reconcile.py`, `app/gm/constitution.{py,json}`, `app/memory/{trophies,abilities}.py`, `app/reference/rulecenter.py`, `app/seed/demo.py`, and the tests that only cover them.

---

### Task 1: Fork and isolate

**Files:**
- Create: `E:\CarouselGame` (clone), `docs/` (copies of the design, roadmap and this plan)
- Modify: `backend/app/config.py`, `start.cmd`, `serve.cmd`, `README.md`, `frontend/index.html` (title)
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `Settings.data_dir` (env `CAROUSEL_DATA_DIR`), `Settings.source_dir: Path` (env `CAROUSEL_SOURCE_DIR`, default `Path(r"E:\Carousel")`), `Settings.reference_db -> Path` (`data_dir / "reference.db"`), `Settings.port: int = 8797`.

- [ ] **Step 1: Clone without touching the original**

```bash
git clone E:/dccrpg E:/CarouselGame
cd E:/CarouselGame && git remote rename origin dccrpg && git checkout -b carousel
```
Expected: `git -C E:/dccrpg status --short` prints nothing. Don't copy `data/`, `.venv/`, `gemini_api_key.txt` or `campaign_sources/` (untracked, so the clone leaves them out). Create a fresh venv with `py -3 -m venv .venv` and install `backend/requirements.txt`.

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_config.py
from pathlib import Path
from app.config import ROOT_DIR, Settings

def test_settings_isolated_from_crawler_console(monkeypatch):
    for k in ("CAROUSEL_DATA_DIR", "CRAWLER_DATA_DIR", "CAROUSEL_SOURCE_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CRAWLER_DATA_DIR", r"E:\dccrpg\data")   # must be ignored
    s = Settings()
    assert s.data_dir == ROOT_DIR / "data"
    assert "dccrpg" not in str(s.data_dir).lower()
    assert s.port == 8797
    assert s.source_dir == Path(r"E:\Carousel")
    assert s.reference_db == s.data_dir / "reference.db"
    assert s.codex_jobs_dir.is_relative_to(s.data_dir)
```

- [ ] **Step 3: Run it, expect FAIL** (`port` / `source_dir` missing). Run: `run-tests.cmd tests/test_config.py -v`

- [ ] **Step 4: Implement in `config.py`**: rename the env var to `CAROUSEL_DATA_DIR`, add `source_dir`, `reference_db`, `port`; update the docstring. `start.cmd`/`serve.cmd`: `PORT=8797`, messages say `[Carousel]`. Page title "Carousel".

- [ ] **Step 5: Run the test, expect PASS.** The full suite may fail here; that's fixed in Tasks 2–3.

- [ ] **Step 6: Commit**: `git commit -am "Fork Crawler Console as Carousel: own port, data dir and source dir"`

### Task 2: Remove dice from the turn pipeline

**Files:**
- Modify: `backend/app/gm/orchestrator.py` (drop `ReplayDice`, `DiceRoller`, `record_roll`), `backend/app/gm/tools.py` (drop `skill_check`, `roll_dice`, `grind`, `resolve_floor_end`, combat/crafting/trap tools), `backend/app/gm/prompts.py` (no dice language), `backend/tests/conftest.py` (no `DiceRoller`, no `scripted`)
- Test: `backend/tests/test_turns.py`, `backend/tests/test_gm_runtime.py`, new `backend/tests/test_no_dice.py`

**Interfaces:**
- Produces: `TurnService(settings, provider_factory, run_async=True)` (the `roller` parameter is gone). `ToolRunner` tool names exclude anything dice-related.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_no_dice.py
import importlib, pkgutil
import app
from app.gm.tools import TOOL_SPECS

DICE_TOOLS = {"skill_check", "roll_dice", "grind", "resolve_floor_end"}

def test_no_dice_tools():
    assert DICE_TOOLS.isdisjoint({t["name"] for t in TOOL_SPECS})

def test_no_dice_module():
    names = {m.name for m in pkgutil.walk_packages(app.__path__, "app.")}
    assert "app.rules.dice" not in names and "app.rules.checks" not in names
```

- [ ] **Step 2: Run it, expect FAIL.**
- [ ] **Step 3: Implement.** The commit path keeps its shape: stage each round in a rolled-back transaction, then replay every staged call in one transaction with the transcript, events and autosave after the state-fingerprint check. Only the dice re-seeding goes. Delete the tools listed above and their prompt references.
- [ ] **Step 4: Update `test_turns.py` / `test_gm_runtime.py`** so they build `TurnService` without a roller. The idempotency, concurrent-change and failed-turn-commits-nothing tests must still pass unchanged in what they assert.
- [ ] **Step 5: Run** `run-tests.cmd tests/test_no_dice.py tests/test_turns.py tests/test_gm_runtime.py -v`. Expected: PASS.
- [ ] **Step 6: Commit**: `"Remove dice from the turn pipeline; keep staged atomic commit"`

### Task 3: Strip DCC modules, schema and views

**Files:**
- Delete: the modules listed under "Deleted from the fork", plus `tests/test_{dice,combat,rules,importer,ledger_sync,campaign_regression,rulecenter,abilities}.py` and `tests/fixtures/mini_ledger/`
- Create: `backend/app/seed/minimal.py`
- Modify: `backend/app/db.py`, `backend/app/main.py`, `backend/app/rules/engine.py`, `backend/app/rules/state_ops.py`, `backend/app/memory/{context,views}.py`, `backend/app/campaigns.py`, `backend/app/art/service.py`, `backend/tests/conftest.py`, `frontend/src/App.tsx`, `frontend/src/views/*`

**Interfaces:**
- Produces: `seed_minimal(conn) -> int` creates one campaign (`name="Test Town"`) with one character `"Newcomer"` and returns its id. Conftest fixture `campaign_id` replaces `demo_id`.
- Schema: `SCHEMA_VERSION = 1`, empty `COLUMN_MIGRATIONS` (a new app, so no in-place migrations). `campaigns` loses `current_floor`, `safe_room`, `floor_deadline_minutes`; tables `skills`, `achievements`, `encounters`, `dice_rolls`, `ledger_syncs`, `corrections`, `ability_explanations`, `ability_uses` are dropped. `locations` and `npc_profiles` gain `ref_id TEXT` (the Carousel ID, nullable).
- Engine ops removed: `hp`, `mana`, `achievement`, `open_box`, `encounter`, `allocate_stats`, `initialize_sheet`, `start_floor_clock`, plus `skill`, `level`, `ai_favor`, `use_ability`, `rest` from `state_ops`. Kept: items, status, `advance_time` (no dungeon-day/mending), `set_location`, `quest`, `npc_attitude`, `fact`, `npc`, `set_quantity`, `clock`, `group`, `move_group`, `move_character`, `scene`, `remember`.

- [ ] **Step 1: Delete the modules and tests. Run the suite to list what breaks.** Run: `run-tests.cmd -x -q`. Expected: import errors that name the files to fix.
- [ ] **Step 2: Fix each import site.** Remove endpoints in `main.py` that serve ledger, reconcile, trophies, rulecenter, combat or achievements. Remove context-builder sections for combat, floor clock, dungeon day, AI favor and constitution. `art/service.py` builds portrait prompts from `art_subjects` notes and character rows only (no ledger visual reference).
- [ ] **Step 3: Replace the demo seed** with `seed_minimal` and switch every remaining test to `campaign_id`.
- [ ] **Step 4: Frontend.** Delete views whose endpoints are gone (`Ledger`, `Achievements`, `Report`, the combat parts of `Hud`), and the DCC theme import. `App.tsx` routes only to surviving views. Run `build-frontend.cmd`. Expected: build succeeds with no TypeScript errors.
- [ ] **Step 5: Run the full suite.** Run: `run-tests.cmd -q`. Expected: all pass, including `test_visibility.py`, `test_access.py`, `test_backup.py`, `test_saves.py`, `test_router.py`.
- [ ] **Step 6: Smoke run.** `start.cmd` → `http://127.0.0.1:8797` loads. Confirm Crawler Console can run on 8777 at the same time.
- [ ] **Step 7: Commit**: `"Strip DCC rules, ledger and views; Carousel schema v1"`

### Task 4: Source manifest and reference DB

**Files:**
- Create: `backend/app/carousel/{__init__,sources,refdb,coords,text}.py`
- Test: `backend/tests/carousel/conftest.py`, `backend/tests/carousel/test_sources.py`

**Interfaces:**
- Produces:
  - `SOURCE_FILES: dict[str, str]`: logical name → path relative to `source_dir`. Keys and paths, exactly:
    `locations` → `Illustrated_Map_Production/game_handoff/locations.json`; `gazetteer` → `map/data/gazetteer.json`; `travel_graph` → `Illustrated_Map_Production/game_handoff/travel_graph.json`; `pixel_anchors` → `Illustrated_Map_Production/game_handoff/location_pixel_anchors.json`; `regions` → `Illustrated_Map_Production/game_handoff/regions.geojson`; `roads` → `Illustrated_Map_Production/game_handoff/roads.geojson`; `tile_manifest` → `Illustrated_Map_Production/web_tiles/tile_manifest.json`; `omens` → `omen/Carousel_Omen_Engine_Data_v0.2.json`; `beats` → `omen2story/Carousel_Omen_Story_Beat_Bible_v0_3.json`; `roles` → `roles/Carousel_Role_Bible_v0_4_2_Implementation.json`; `tropes` → `tropes/Carousel_Trope_Bible_Data_v0.1.1.json`; `npcs` → `npcs/Carousel_Camp_NPC_Package_v1/Carousel_Camp_NPC_Database_v1.json`.
  - `@dataclass(frozen=True) class SourceFile: key: str; path: Path; sha256: str; data: Any`
  - `load_sources(source_dir: Path) -> dict[str, SourceFile]`: raises `SourceError` naming every missing file at once.
  - `verify_unchanged(files: dict[str, SourceFile]) -> None`: re-hashes and raises `SourceError` on any mismatch.
  - `refdb.connect(path: Path) -> sqlite3.Connection`, `refdb.init(conn)`. Tables: `manifest(key, path, sha256, imported_at)`, `places`, `place_aliases`, `graph_nodes`, `graph_edges`, `omens`, `omen_beats`, `roles`, `tickets`, `start_profiles`, `tropes`, `npc_templates`, `import_report(category, item_id, message)`.
  - `coords.to_pixel(north: float, west: float, size: int = 12000) -> tuple[float, float]`, `coords.from_pixel(x, y, size=12000) -> tuple[float, float]`
  - `text.norm_name(s: str | None) -> str`: casefold, `’ ‘ U+FFFD` → `'`, collapse whitespace, strip.
- Test fixtures: `real_source` (the `E:\Carousel` path; `pytest.skip` if absent) and `mini_source(tmp_path)` (writes small synthetic versions of every `SOURCE_FILES` entry, used for failure cases).

- [ ] **Step 1: Write the failing tests**

```python
def test_load_sources_real(real_source):
    files = load_sources(real_source)
    assert set(files) == set(SOURCE_FILES)
    assert all(len(f.sha256) == 64 for f in files.values())

def test_missing_files_reported_together(mini_source):
    (mini_source / SOURCE_FILES["omens"]).unlink()
    (mini_source / SOURCE_FILES["npcs"]).unlink()
    with pytest.raises(SourceError) as e:
        load_sources(mini_source)
    assert "omens" in str(e.value) and "npcs" in str(e.value)

def test_pixel_transform():
    assert to_pixel(1000, 1000) == (0, 0)
    assert to_pixel(0, 0) == (12000, 12000)
    assert to_pixel(425, 570) == (5160, 6900)          # flea market CAR-035
    assert from_pixel(*to_pixel(485, 490)) == (485, 490)

def test_norm_name():
    assert norm_name("Patcher’s Family Farm") == norm_name("Patcher\ufffds  family farm") == "patcher's family farm"
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Read each file once as bytes, hash those bytes and parse the same bytes, so the hash always matches what was parsed.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit**: `"Carousel source manifest, reference DB schema, coordinate and name helpers"`

### Task 5: Import places and the travel graph

**Files:**
- Create: `backend/app/carousel/import_places.py`
- Test: `backend/tests/carousel/test_import_places.py`

**Interfaces:**
- Consumes: `load_sources`, `refdb`, `to_pixel`, `norm_name`.
- Produces: `import_places(ref: sqlite3.Connection, files: dict[str, SourceFile]) -> None` fills `places` (every gazetteer record: `id, name, category, region, map_id, north, west, px, py, requires_json, spoiler, status, raw_json`; the `locations.json` fields merge onto the same ID), `place_aliases(alias_id, target_id)` from `travel_graph.aliases`, `graph_nodes`, `graph_edges` (`id, from_id, to_id, kind, directed, requires_json, cost, length_units, geometry_json`). Also `resolve_place(ref, id_or_alias) -> str | None` and `place_by_name(ref, name) -> str | None` (normalised name or alias).

- [ ] **Step 1: Write the failing tests** (real source)

```python
def test_place_counts(ref_real):
    assert count(ref_real, "places") == 234
    assert count(ref_real, "places", "map_id = 'town'") >= 191
    assert count(ref_real, "graph_nodes") == 511
    assert count(ref_real, "graph_edges") == 546
    assert count(ref_real, "place_aliases") == 8

def test_aliases_resolve(ref_real):
    assert resolve_place(ref_real, "CAR-017") == "CAR-016"
    for (target,) in ref_real.execute("SELECT target_id FROM place_aliases"):
        assert resolve_place(ref_real, target) == target   # every target exists

def test_flags_preserved(ref_real):
    e = ref_real.execute("SELECT requires_json, directed FROM graph_edges WHERE id='EDGE-00001'").fetchone()
    assert json.loads(e[0]) == [] and e[1] == 0

def test_pixels_match_handoff(ref_real, real_files):
    # spot-check against location_pixel_anchors.json for every place it lists
    ...  # assert abs(px - anchor_x) < 0.5 and abs(py - anchor_y) < 0.5
```
(The last test iterates `location_pixel_anchors.json["locations"]` and asserts agreement for every record; read one record for its key names.)

- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** Keep rejected and alias records; never drop a CAR ID. **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit**: `"Import gazetteer, places, aliases and travel graph"`

### Task 6: Import Omens and story beats with anchor resolution

**Files:**
- Create: `backend/app/carousel/import_omens.py`
- Test: `backend/tests/carousel/test_import_omens.py`

**Interfaces:**
- Consumes: `place_by_name`, `resolve_place` (Task 5).
- Produces: `import_omens(ref, files) -> None`. `omens` columns: `id, record_type, description, story_lock, playable_title, place_id, anchor_method ('gazetteer_id'|'anchor_name'|NULL), placement ('place'|'area'), spawn_mode ('fixed'|'roaming'|'item'|'special'), choice_trigger ('<operator>:<target>' from Trigger Operator/Target), beats_json (hook/escalation/climax/ending requirement), player_json, gm_json, raw_json`. There is deliberately no Map X/Y column. `omen_beats(omen_id, dossier_json, sealed_json)`: the dossier's sealed truth / clue solutions go in `sealed_json`. Every unresolved Omen goes to `import_report`.
- Rules:
  - `place_id` = `Gazetteer ID` if present (method `gazetteer_id`), else `place_by_name(Map Anchor)` (method `anchor_name`).
  - `placement = 'area'` when the resolved place's category is `District`, `Public square`, `Rural zone` or `Riverside district`; else `'place'`.
  - `spawn_mode` from `Spawn / Timing`: starts with "Visible as an inspectable anomaly" → `fixed`; "Spawn as a visible moving encounter" → `roaming`; "Must acquire/retain the specified item" → `item`; anything else → `special`.
  - Hubs (`Record Type = 'Hub / generator'`) and their `hub_seed_pools` are stored too, with `record_type` set, so nothing launches from them.

- [ ] **Step 1: Write the failing tests**

```python
def test_playable_omen_resolution(ref_real):
    q = lambda w: count(ref_real, "omens", f"record_type='Playable Omen' AND {w}")
    assert q("1=1") == 93
    assert q("anchor_method='gazetteer_id'") == 34
    assert q("anchor_method='anchor_name'") == 59
    assert q("place_id IS NULL") == 0

def test_spawn_modes(ref_real):
    got = dict(ref_real.execute("SELECT spawn_mode, COUNT(*) FROM omens WHERE record_type='Playable Omen' GROUP BY 1"))
    assert got == {"fixed": 49, "roaming": 24, "item": 15, "special": 5}

def test_area_placement(ref_real):
    assert count(ref_real, "omens", "record_type='Playable Omen' AND placement='area'") == 50

def test_no_map_xy(ref_real):
    cols = {r[1] for r in ref_real.execute("PRAGMA table_info(omens)")}
    assert not any("map_x" in c or "map_y" in c for c in cols)
    assert all("Map X / 1000" not in r[0] for r in ref_real.execute("SELECT raw_json FROM omens"))

def test_anchor_normalisation(ref_real):
    oid = ref_real.execute("SELECT id FROM omens WHERE raw_json LIKE '%Berryman%Dive%'").fetchone()[0]
    assert ref_real.execute("SELECT place_id FROM omens WHERE id=?", (oid,)).fetchone()[0] is not None

def test_every_playable_omen_has_beats(ref_real):
    assert count(ref_real, "omen_beats") == 93
```
(`raw_json` stores the registry row with the Map X/Y keys removed.)

- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS.** If a count differs, inspect the data before changing the test: the counts were measured on 2026-10-31.
- [ ] **Step 5: Commit**: `"Import Omens and beat dossiers; resolve anchors from the gazetteer"`

### Task 7: Import roles, tickets, starting profiles and the trope bible

**Files:**
- Create: `backend/app/carousel/import_roles.py`
- Test: `backend/tests/carousel/test_import_roles.py`

**Interfaces:**
- Produces: `import_roles(ref, files) -> None`.
  - `roles(id, name, kind 'base'|'aspect'|'advanced', parent, raw_json)` from `v04_playable_overlay.{base_roles,aspects,advanced_roles}`.
  - `tickets(id, name, source_role_id, relationship, text, raw_json)` from `v041_integration.trope_registry_overlay` (81 entries, 10 of them `hb.ticket.starter.shared.*`); `text` is the rule text the GM will apply, taken from each entry's `original_ticket_definition` (for shared starters, the matching `shared_starting_trope_pool` entry's `rule`).
  - `start_profiles(role_id, mettle, moxie, hustle, savvy, grit, base_pa, provenance)` from `v042_audit_resolution.starting_stat_profiles_by_role_id`.
  - `tropes(id, name, full_player, has_enemy, reference_only, variants_json)` from the trope bible.

- [ ] **Step 1: Write the failing tests**

```python
def test_role_counts(ref_real):
    got = dict(ref_real.execute("SELECT kind, COUNT(*) FROM roles GROUP BY 1"))
    assert got == {"base": 13, "aspect": 39, "advanced": 19}

def test_tickets(ref_real):
    assert count(ref_real, "tickets") == 81
    pat = re.compile(r"^hb\.ticket\.(base|aspect|advanced|starter\.shared)\.[a-z0-9-]+(?:\.[a-z0-9-]+)*$")
    assert all(pat.match(i) for (i,) in ref_real.execute("SELECT id FROM tickets"))
    assert count(ref_real, "tickets", "text IS NULL OR text = ''") == 0

def test_start_profiles(ref_real):
    rows = ref_real.execute("SELECT role_id, mettle+moxie+hustle+savvy+grit, base_pa, "
                            "max(mettle,moxie,hustle,savvy,grit) FROM start_profiles").fetchall()
    assert len(rows) == 13
    assert all(total == 11 and pa == 11 and cap <= 5 for _, total, pa, cap in rows)
    assert ref_real.execute("SELECT savvy FROM start_profiles WHERE role_id='base.athlete'").fetchone()[0] == 0

def test_trope_bible(ref_real):
    assert count(ref_real, "tropes") == 627
```

- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit**: `"Import roles, tickets, starting profiles and trope bible"`

### Task 8: Import camp resident templates

**Files:**
- Create: `backend/app/carousel/import_npcs.py`
- Test: `backend/tests/carousel/test_import_npcs.py`

**Interfaces:**
- Produces: `import_npcs(ref, files) -> None` fills `npc_templates(id, name, provenance, canon_appearance, status, camp_role, usual_location, player_json, gm_json, raw_json)`. `gm_json` holds every `gm_only` field (at any depth), and `player_json` is the record with those removed. Phase 7 copies templates into campaign `npc_profiles`; this task only stores them.

- [ ] **Step 1: Write the failing tests**

```python
def test_npc_counts(ref_real):
    assert count(ref_real, "npc_templates") == 70
    got = dict(ref_real.execute("SELECT provenance, COUNT(*) FROM npc_templates GROUP BY 1"))
    assert got == {"canon_expanded": 23, "original_adaptation": 47}

def test_gm_only_split(ref_real):
    for p, g in ref_real.execute("SELECT player_json, gm_json FROM npc_templates"):
        assert "gm_only" not in p
        for secret in iter_strings(json.loads(g)):
            assert secret not in p
```

- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit**: `"Import camp resident templates with gm_only split"`

### Task 9: Build–validate–swap importer and CLI

**Files:**
- Create: `backend/app/carousel/importer.py`, `backend/app/carousel/refs.py`, `import-data.cmd`
- Modify: `start.cmd` (run the import when `data/reference.db` is missing)
- Test: `backend/tests/carousel/test_importer.py`

**Interfaces:**
- Produces:
  - `referenced_ids(campaign_conn) -> dict[str, set[str]]`: `{"place": ..., "npc": ...}` from `locations.ref_id` and `npc_profiles.ref_id`. Later phases add their columns here (positions, Omen instances, owned tickets).
  - `@dataclass class ImportResult: ok: bool; counts: dict[str, int]; missing: dict[str, list[str]]; report_path: Path`
  - `run_import(settings: Settings) -> ImportResult`: `load_sources` → build into `reference.db.new` → run all four importers → `verify_unchanged` → check every `referenced_ids` value resolves (aliases count) → `os.replace` onto `reference.db`. Any failure leaves `reference.db` untouched, deletes `.new`, and writes `data/import_reports/<timestamp>.json`.
  - CLI: `python -m app.carousel.importer` prints counts or the refusal. `import-data.cmd` wraps it.

- [ ] **Step 1: Write the failing tests**

```python
def test_import_builds_reference_db(settings_real):
    r = run_import(settings_real)
    assert r.ok and r.counts["places"] == 234 and r.counts["omens"] >= 93
    assert settings_real.reference_db.exists()

def test_reimport_refuses_missing_referenced_id(settings_mini, campaign_conn):
    assert run_import(settings_mini).ok
    before = settings_mini.reference_db.read_bytes()
    campaign_conn.execute("UPDATE locations SET ref_id='CAR-999' WHERE id=(SELECT min(id) FROM locations)")
    campaign_conn.commit()
    r = run_import(settings_mini)
    assert not r.ok and r.missing == {"place": ["CAR-999"]}
    assert settings_mini.reference_db.read_bytes() == before

def test_reimport_accepts_alias(settings_mini, campaign_conn):
    ...  # ref_id set to an alias ID whose target exists -> ok

def test_import_aborts_if_source_changes_midway(settings_mini, monkeypatch):
    # monkeypatch import_npcs to append a byte to the omens file, then expect not ok, no swap
    ...
```
(`settings_mini` points `source_dir` at the synthetic source from Task 4. A `locations` row needs to exist for the refusal test; insert one in the fixture.)

- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS.**
- [ ] **Step 5: Run the real import once.** `import-data.cmd`. Expected: counts printed and `data/reference.db` created. Confirm no file under `E:\Carousel` has an mtime newer than the run's start.
- [ ] **Step 6: Commit**: `"Re-runnable Carousel import with reference safety check"`

### Task 10: Reference API, tile serving and visibility

**Files:**
- Create: `backend/app/reference_api.py`
- Modify: `backend/app/main.py` (include router; mount tiles), `backend/tests/test_visibility.py`
- Test: `backend/tests/carousel/test_reference_api.py`

**Interfaces:**
- Produces:
  - `GET /api/reference/status` (player): `{imported_at, counts, sources: {key: sha256[:12]}}`. No content.
  - `GET /api/gm/reference/{places|omens|roles|tickets|npcs}` and `/{kind}/{id}` (requires `X-GM-Token`): full rows including `gm_json` / `sealed_json`.
  - Static mount `/tiles` → `settings.source_dir / "Illustrated_Map_Production/web_tiles"` (read-only `StaticFiles`). `/tiles/tile_manifest.json` must load.
  - Player-facing place/Omen endpoints are **not** added here; knowledge gating arrives with Phase 3.

- [ ] **Step 1: Write the failing tests**

```python
def test_status_has_no_content(client_ref):
    body = client_ref.get("/api/reference/status").json()
    assert body["counts"]["places"] == 234 and "gm_json" not in json.dumps(body)

def test_gm_reference_requires_token(client_ref):
    assert client_ref.get("/api/gm/reference/omens").status_code in (401, 403)

def test_tiles_served(client_ref):
    assert client_ref.get("/tiles/tile_manifest.json").status_code == 200
```
Extend `test_visibility.py`: plant a marker string in one `omen_beats.sealed_json` and one `npc_templates.gm_json`, then crawl every player endpoint (the test's existing crawl) and assert neither marker appears.

- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run the full suite**: `run-tests.cmd -q`. Expected: all pass. `build-frontend.cmd` succeeds.
- [ ] **Step 5: Commit**: `"Reference status and GM reference API; serve map tiles; extend visibility crawl"`

---

## Done when

- `E:\CarouselGame` on branch `carousel` starts on 8797 alongside Crawler Console on 8777. `E:\dccrpg` shows no changes.
- `import-data.cmd` builds `reference.db` with 234 places, 511 nodes / 546 edges, 93 playable Omens all placed, 71 roles, 81 tickets, 13 profiles, 627 tropes and 70 residents.
- Full test suite green, including the visibility crawl with sealed truth and NPC `gm_only` planted.
- Next: write Plan 2 (characters and tickets) against this code.
