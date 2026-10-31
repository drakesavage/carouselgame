# Carousel Plan 3 — Maps and Travel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the player move around Carousel by clicking the illustrated panel map. Routes come from the travel graph and respect every flag. The clock advances by distance. Interruptions (an Omen sighted on the way) are decided before the walk starts, and every arrival is narrated by the GM, which is asked to start the moment the player clicks.

**Architecture:** A pure route planner (`carousel/travel.py`) runs Dijkstra over the imported graph, gated by the campaign's flags. A deterministic interruption picker (`carousel/interruptions.py`) cuts a route short at an Omen. One engine op, `travel`, applies the result: position, time, known places, Omen sightings and events. `POST /travel` applies that op, then immediately submits an arrival turn to the GM. Quiet arrivals route to Gemini, interrupted ones to Codex. The frontend shows a Leaflet `CRS.Simple` tile map of the 12000² panel art with a small world-map inset, animates the party marker along the route, and holds the place's identity image on screen until the narration lands.

**Tech Stack:** Python 3.10, FastAPI, SQLite, pytest; React/TS, **Leaflet 1.9.4** (new dependency; design §13 names it).

**Spec:** [docs/2026-10-30-carousel-game-design.md](../../2026-10-30-carousel-game-design.md) §6 (maps and movement), §5 (data sources), §10 (safe places), §11 (world vs group state). Roadmap Phase 3. Builds on Plan 2 (branch `carousel`).

## Global Constraints

- **The travel graph is the only movement authority.** An edge is usable only if every flag in its `requires` is set for the campaign, *and* the destination node's `requires` flags are set too. Directed edges go one way only. Coordinates and painted roads never create connections (graph policy).
- Coordinates are `[north, west]` in 0–1000. Pixels: `x = (1000 − west) × 12`, `y = (1000 − north) × 12` (use `carousel/coords.py`).
- `MINUTES_PER_TICK = 3`: game minutes per graph cost tick (builder's call, design §13). Lodge (CAR-047) → Town Square (CAR-002) is 14.8 ticks, about 44 minutes.
- `SAFE_PLACE_IDS = {"CAR-046", "CAR-047", "CAR-002", "CAR-006"}`: Camp Dyer, Dyer's Lodge, Town Square, the Diner (design §10 table; the bowling alley has no record yet).
- Interruptions are deterministic: decided when the route is planned, from campaign state plus a travel sequence number. Never at random during the walk, and never a dice roll for an outcome.
- Seeing an Omen is always safe. Travel can make an Omen *visible*; a story only ever starts on its Choice action (Phase 4).
- Only *known* places reach the player. Unknown places, spoiler records and unseen Omens never appear in a player endpoint.
- No dice. `E:\Carousel` and `E:\dccrpg` are read-only. Port 8797.

## Review Focus

1. **The destination is behind a flag, or unreachable from where the party stands** → refused with a reason ("no route without: akers_story"), nothing moves, no turn is submitted. (Task 3: `test_flagged_edges_and_nodes_need_flags`; Task 6: `test_travel_refused_changes_nothing`)
2. **Restoring a save, then travelling the same way again** → the same interruption decision (the sequence number is restored with the save). (Task 4: `test_interruption_is_reproducible_after_restore`)
3. **An unknown or spoiler place leaks** through the map API, the route geometry, an event or the arrival turn. (Task 7: visibility crawl with an unknown place name planted)
4. **The player clicks travel while a GM turn is running** → 409, nothing moves. (Task 6: `test_travel_refused_while_a_turn_runs`)
5. **A re-import drops a node the party stands on, a place they know, or an Omen they've seen** → the import is refused (Plan 1 safety check). (Task 2: `test_reimport_protects_map_state`)

---

## File structure

```
backend/app/
  carousel/sources.py          # + location_art index
  carousel/import_art.py       # place_images from Location_Art_Production/data/review_reference_index.json
  carousel/travel.py           # graph load, nearest_node, plan_route
  carousel/interruptions.py    # decide()
  carousel/refs.py             # + node, known place, omen references
  rules/travel_ops.py          # travel, learn_place, set_flag, clear_flag (registered like carousel_ops)
  map_api.py                   # GET /map, POST /travel, /location-art mount
  db.py, memory/snapshots.py   # campaigns.node_id, party_groups.node_id, known_places, campaign_flags, omen_states, travel_seq
  gm/router.py, gm/orchestrator.py  # arrival turns: route by kind
frontend/
  package.json                 # leaflet 1.9.4, @types/leaflet 1.9.12
  src/views/MapView.tsx, src/map.css   # replaces the World tab
```

---

### Task 1: Location art in the reference data

**Files:**
- Create: `backend/app/carousel/import_art.py`
- Modify: `carousel/sources.py` (`SOURCE_FILES["location_art"] = "Location_Art_Production/data/review_reference_index.json"`), `carousel/refdb.py` (table `place_images(place_id, kind, rel_path, PRIMARY KEY(place_id, kind))`), `carousel/importer.py` (`IMPORTERS` + `COUNTED`), `tests/carousel/mini_data.py`
- Test: `backend/tests/carousel/test_import_art.py`

**Interfaces:**
- `import_art(ref, files) -> None`: one row per index `references[*]`, with `kind = "identity"` and `rel_path = relative_image_path`. Only `location_id`s that exist in `places` are kept; the rest go to `import_report` as `art_unmatched`.
- `place_image(conn_with_ref, place_id) -> str | None` (in `refdata.py`): the identity image's `rel_path`.

- [ ] **Step 1: Failing tests:** on real data, `count(ref, "place_images") >= 191` and `place_image(…, "CAR-002")` ends with `.png`; on mini data, an index row for an unknown ID lands in `import_report`.
- [ ] **Steps 2–4:** RED → implement → GREEN (full suite).
- [ ] **Step 5: Commit** `"Import the location art index"`

### Task 2: Campaign map state

**Files:**
- Modify: `backend/app/db.py`, `backend/app/memory/snapshots.py`, `backend/app/carousel/refs.py`, `backend/tests/fixtures/carousel_seed.py`, `backend/tests/carousel/mini_data.py` (a richer town graph; see below)
- Test: `backend/tests/carousel/test_map_state.py`, `backend/tests/carousel/test_importer.py`

**Interfaces:**
- Schema (plus `COLUMN_MIGRATIONS` rows):
  - `campaigns.node_id TEXT`: the player character's group's graph node. `campaigns.travel_seq INTEGER NOT NULL DEFAULT 0`.
  - `party_groups.node_id TEXT`.
  - `known_places(campaign_id, place_id, how TEXT, known_at_minutes INTEGER, PRIMARY KEY(campaign_id, place_id))`.
  - `campaign_flags(campaign_id, flag, set_at_minutes INTEGER, source TEXT, PRIMARY KEY(campaign_id, flag))`: GM-only (no player endpoint lists them).
  - `omen_states(campaign_id, omen_id, stage TEXT CHECK(stage IN ('visible','examined')), seen_at_node TEXT, seen_at_minutes INTEGER, PRIMARY KEY(campaign_id, omen_id))`. Phase 4 extends the stages.
  - The three new tables go in `SNAPSHOT_TABLES` and `_DELETE_ORDER`. `campaigns` is already snapshotted, so `node_id` and `travel_seq` restore with it.
- `refs.REFERENCE_COLUMNS` gains `"node": ("campaigns", "node_id")`, `"group_node": ("party_groups", "node_id")` (both checked against `ref.graph_nodes`), `"known_place": ("known_places", "place_id")` (as places) and `"omen": ("omen_states", "omen_id")` (against `ref.omens`).
- Mini data: replace the 2-node graph with this town graph:

  | Node | Place | Notes |
  |---|---|---|
  | `CAR-047` | Lodge | safe |
  | `J-1` | junction | — |
  | `CAR-002` | Town Square | safe |
  | `CAR-006` | Diner | safe |
  | `CAR-086` | Farm | — |
  | `CAR-099` | Secret place | spoiler, `requires: ["akers_story"]` |

  | `CAR-016` | Old diner | target of the existing alias CAR-017 |

  Undirected unflagged edges: Lodge–J-1 (cost 10), J-1–Square (5), Square–Diner (4), J-1–Farm (12), Square–Old diner (1). Directed edge Square→Farm (cost 2, kind `scenario_path`, `requires: ["shortcut_story"]`). Edge Farm–Secret (cost 3). Add matching gazetteer and locations records (categories: Diner `Restaurant`, Farm `Farm`). Omens: OM-001 fixed at CAR-086, OM-002 roaming anchored at CAR-002, OM-003 fixed at CAR-006.
- Seed: the party starts at `node_id = "CAR-002"`; known places are CAR-047, CAR-002 and CAR-006.

- [ ] **Step 1: Failing tests:** the schema has the columns and tables; save point → change `node_id`, add a known place, set a flag → restore → all three are back. Then `test_reimport_protects_map_state`: a campaign standing on `J-404` and knowing `CAR-404` makes `run_import` refuse with `{"node": ["J-404"], "known_place": ["CAR-404"]}`.
- [ ] **Steps 2–4:** RED → implement → GREEN (full suite; update tests that counted mini places or edges).
- [ ] **Step 5: Commit** `"Campaign map state: position, known places, flags, Omen sightings"`

### Task 3: Route planner

**Files:**
- Create: `backend/app/carousel/travel.py`
- Test: `backend/tests/carousel/test_travel.py`

**Interfaces:**
- `@dataclass(frozen=True) class Route: nodes: list[str]; edges: list[str]; ticks: float; minutes: int; geometry_px: list[tuple[float, float]]` (the edges' geometry concatenated, converted with `to_pixel`, oriented in travel direction).
- `plan_route(conn, start: str, goal: str, flags: set[str]) -> Route | None`: Dijkstra on `ref.graph_edges`. An edge is usable iff `set(requires) <= flags`, and also `set(node.requires) <= flags` for the node it leads into. Respect `directed` (from → to only). `start`/`goal` may be place IDs or aliases (resolve through `resolve_place`; graph node IDs equal place IDs for places). `minutes = round(ticks × MINUTES_PER_TICK)`. Returns `None` when unreachable.
- `missing_flags(conn, start, goal, flags) -> list[str]`: when `plan_route` is `None`, the sorted flags that a fully-unlocked search would need on its cheapest path (used for the refusal message).
- `nearest_node(conn, north, west, max_units=25.0) -> str | None`: the closest node on the `town` map.
- `MINUTES_PER_TICK = 3`.

- [ ] **Step 1: Failing tests (mini graph)**

```python
def test_cheapest_unflagged_route(conn):
    r = plan_route(conn, "CAR-047", "CAR-006", set())
    assert r.nodes == ["CAR-047", "J-1", "CAR-002", "CAR-006"] and r.ticks == 19 and r.minutes == 57
    assert r.geometry_px[0] == to_pixel(*coords_of("CAR-047"))

def test_flagged_edges_and_nodes_need_flags(conn):
    assert plan_route(conn, "CAR-002", "CAR-086", set()).ticks == 17                 # via J-1
    assert plan_route(conn, "CAR-002", "CAR-086", {"shortcut_story"}).ticks == 2
    assert plan_route(conn, "CAR-086", "CAR-002", {"shortcut_story"}).ticks == 17    # directed: no way back on it
    assert plan_route(conn, "CAR-002", "CAR-099", set()) is None                     # destination node requires akers_story
    assert missing_flags(conn, "CAR-002", "CAR-099", set()) == ["akers_story"]

def test_alias_destination(conn):
    assert plan_route(conn, "CAR-002", "CAR-017", set()).nodes == ["CAR-002", "CAR-016"]
def test_nearest_node_snaps_within_25_units(conn): ...

def test_real_graph_lodge_to_square(real_ref_conn):                                  # skips without E:\Carousel
    r = plan_route(real_ref_conn, "CAR-047", "CAR-002", set())
    assert abs(r.ticks - 14.8) < 0.01 and r.minutes == 44
```
(`real_ref_conn`: a new session fixture in `tests/carousel/conftest.py`. It opens `sqlite3.connect(":memory:")` and calls `attach_reference` with the reference DB the session builds from the real source, i.e. the file behind Plan 1's `ref_real` fixture, so `ref.`-prefixed queries work exactly as on a campaign connection.)

- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"Route planner over the travel graph, gated by campaign flags"`

### Task 4: Interruptions

**Files:**
- Create: `backend/app/carousel/interruptions.py`
- Test: `backend/tests/carousel/test_interruptions.py`

**Interfaces:**
- `@dataclass(frozen=True) class Interruption: omen_id: str; node: str; kind: Literal["fixed", "roaming"]`
- `decide(conn, cid: int, route: Route, seq: int) -> Interruption | None`. Rules, in order:
  1. **Fixed:** walk `route.nodes[1:]` in order. The first node whose place has a `Playable Omen` with `spawn_mode='fixed'` and `placement='place'` that has no `omen_states` row yet interrupts there.
  2. **Roaming:** only if no fixed one. Candidates are playable `spawn_mode='roaming'` Omens with no `omen_states` row whose anchor place lies within `ROAM_RADIUS = 120` units of any route node. Chance = `min(0.5, route.ticks × 0.02)`. Draw `h = int(sha256(f"{cid}:{seq}").hexdigest()[:8], 16) / 0xFFFFFFFF`. Interrupt iff `h < chance`; the candidate is `sorted(ids)[int(h * 1e6) % len]`; the node is the route node closest to its anchor.
  3. Never interrupt at the start node.
- `truncate(route, node) -> Route`: the route up to and including `node` (ticks, minutes and geometry recomputed).

- [ ] **Step 1: Failing tests:** Lodge→Diner on the mini graph stops at CAR-006 for OM-003 (fixed). Once OM-003 is visible, the same trip has no fixed stop. Roaming is deterministic (the same `seq` gives the same answer twice; a sweep over `seq` 0..199 gives a hit rate within ±0.1 of the chance). `test_interruption_is_reproducible_after_restore`: save, then `travel_seq` and `decide`, then restore, then the same decision.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"Deterministic Omen interruptions decided at route time"`

### Task 5: Travel ops

**Files:**
- Create: `backend/app/rules/travel_ops.py` (registered in `rules/__init__.py`)
- Modify: `backend/app/gm/prompts.py`
- Test: `backend/tests/carousel/test_travel_ops.py`

**Interfaces (op names and fields, exactly):**
- `travel {to?: str, north?: float, west?: float, reason?: str}`: `to` is a place ID or alias. Otherwise `north`/`west` snaps through `nearest_node`, and refuses "no road there" if nothing is within 25 units. The op:
  1. Plans from `campaigns.node_id` with the campaign's flags. Refusals: "already here"; "no route without: f1, f2"; "no route".
  2. Increments `travel_seq` and calls `decide(…, seq)`. If interrupted, truncates the route.
  3. Sets `campaigns.node_id` and `current_location` (the place name, or "on the road near <nearest known place>" for a junction), and `safe_room = node in SAFE_PLACE_IDS`. Moves the PC's party group (existing `pc_group` logic) and its members.
  4. Advances `game_time_minutes` by `route.minutes` through the existing `advance_time` applier, so expiries still run.
  5. Marks the arrival place known (`how='visited'`). If interrupted, inserts `omen_states(stage='visible')`.
  6. Writes events: `travel` (player-visible, payload `{route_nodes, minutes, arrived, interrupted}`) and, if interrupted, `omen_sighted` with the Omen's registry description.
  7. Returns `{summary, arrived, interrupted: {omen_id, description}|None, minutes, geometry_px}`.
- `learn_place {place, how}`: adds a known place ("a resident mentioned it"); refuses unknown IDs.
- `set_flag {flag, reason}` / `clear_flag {flag, reason}`: events with visibility `gm`.
- Prompt addition (exact): `"Movement happens on the town's travel graph: use apply_changes op travel when the group goes somewhere; never narrate arriving somewhere without it. When a turn starts with [arrival], the move has already been applied: describe the arrival, do not call travel again."`

- [ ] **Step 1: Failing tests** (each test first inserts `omen_states` rows for the Omens it isn't about, so the roaming OM-002 can't interfere):
  - travel Square→Lodge moves the node, advances 45 minutes (15 ticks × 3), sets `safe_room=1` and marks Lodge known;
  - travel Square→Farm arrives at the Farm and sights OM-001 there (the arrival and the sighting coincide);
  - travel Lodge→Diner is interrupted at CAR-006 by OM-003 (fixed takes precedence over roaming), and `omen_states` shows it visible;
  - refusals ("already here", "no route without: akers_story") change nothing: same node, time, `travel_seq` and event count;
  - a `north`/`west` snap to the Diner's coordinates works, and a snap 200 units from any node is refused;
  - after `set_flag akers_story`, CAR-099 is reachable.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"Travel, learn_place and story flags as engine ops"`

### Task 6: Travel endpoint and arrival turns

**Files:**
- Create: `backend/app/map_api.py` (`register(app, settings, state, db, write_lock)`, called before the SPA catch-all like `reference_api`)
- Modify: `backend/app/gm/orchestrator.py` (`submit(..., kind: str | None = None)` stores `kind` on the turn and passes it to routing), `backend/app/gm/router.py` (`route(text, story_active, absent, kind=None)`: `kind="arrival"` → the `talk` route, `kind="arrival_omen"` → `action`), `backend/app/db.py` (`turns.kind TEXT`)
- Test: `backend/tests/carousel/test_map_api.py`

**Interfaces:**
- `POST /api/campaigns/{cid}/travel {to? | north?/west?, idempotency_key}`: under the existing `write_lock` dependency (409 while a turn runs). In one transaction it applies op `travel`; a `ChangeError` gives 422 and changes nothing. It then submits the arrival turn with text `"[arrival] We arrive at <place>."` (or `"[arrival] On the way to <dest>, we stop: <omen description>."`) and `kind` `arrival` / `arrival_omen`. It returns `{travel: <op result>, turn: <TurnService.describe>}`. A repeated `idempotency_key` returns the stored turn without moving again: the travel and the turn share the key (`turns.idempotency_key` = key; `travel` is only applied when no turn with that key exists).
- The GM's context already shows the `travel` event and the new location; no new context section is needed.

- [ ] **Step 1: Failing tests:** a travel returns 200 with an arrival turn completed by the mock GM, and the node has moved. `test_travel_refused_changes_nothing` (422; node, time and turn count unchanged). `test_travel_refused_while_a_turn_runs` (hold `WRITE_LOCK` → 409). The same `idempotency_key` twice moves once. An interrupted travel routes as `arrival_omen` (assert the `kind` on the turn row).
- [ ] **Steps 2–4:** RED → implement → GREEN (full suite).
- [ ] **Step 5: Commit** `"Travel endpoint starts the GM's arrival turn on click"`

### Task 7: Map API and visibility

**Files:**
- Modify: `backend/app/map_api.py`, `backend/tests/test_visibility.py`
- Test: `backend/tests/carousel/test_map_api.py`

**Interfaces:**
- `GET /api/campaigns/{cid}/map` returns:
  - `position: {node_id, place_id|null, px, py, label}`
  - `known: [{id, name, category, px, py, safe, image: "/location-art/<rel_path>"|null}]`: only `known_places` rows, never `spoiler` places unless known
  - `omens: [{id, description, px, py, stage}]`: only `omen_states` rows; `description` is the registry's observable anomaly
  - `last_route: {geometry_px, interrupted}|null`: from the latest `travel` event
  - `tiles: {template: "/tiles/{z}/{x}/{y}.webp", levels: <tile_manifest levels>, size: 12000}`
- Static mount `/location-art` → `<source_dir>/Location_Art_Production/assets` (read-only), next to `/tiles`.
- `GET /api/campaigns/{cid}/places` (the old DCC "known world" from `memory/places.py`) is removed with its view. Ruling recorded if anything still needs it.

- [ ] **Step 1: Failing tests:** the map returns the 3 seed-known places only. After travelling to the Farm, the Farm appears. The Secret place (CAR-099, spoiler, unknown) never appears. Omens appear only after a sighting. The visibility crawl adds `/map` and plants the unknown place's name (rename mini CAR-099 to `UNKNOWNPLACE-HOTEL-8`) as a secret.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"Player map API: position, known places, sighted Omens, tiles"`

### Task 8: Map view

**Files:**
- Create: `frontend/src/views/MapView.tsx`, `frontend/src/map.css`
- Delete: `frontend/src/views/World.tsx`, `frontend/src/world.css` (and `memory/places.py` if Task 7 removed its route)
- Modify: `frontend/package.json` (`leaflet@1.9.4`, `@types/leaflet@1.9.12`), `frontend/src/App.tsx` (the "Map" tab replaces "World"), `frontend/src/api.ts` (`map()`, `travel()`)
- Test: `build-frontend.cmd`; browser check

**Interfaces / behavior:**
- **Panel map:** `L.map` with `crs: L.CRS.Simple` and bounds `[[-12000, 0], [0, 12000]]` (y negated). A custom `L.TileLayer` maps Leaflet zoom to the manifest's 6 levels. `minZoom`/`maxZoom` come from the levels; tiles outside a level's columns or rows are not requested.
- **Pins:** known places (safe places marked), a party marker, and sighted Omens as anomaly markers. Clicking a pin opens a small card (name, image thumbnail, "Go here"). Clicking a road while holding Alt asks "Walk here?", then posts `{north, west}`.
- **Travel:** `POST /travel`. The marker animates along `geometry_px` (duration `min(4 s, minutes × 50 ms)`), then the **establishing shot** shows: a full-panel overlay with the destination's identity image (or the Omen place's). The narration fades in when the turn completes. The shot holds while the turn is still processing (polling `/api/turns/{id}` like the Story view).
- **World map inset:** a 220 px corner panel drawn from zoom-1 tiles (2×2), with the party dot and known pins. Clicking it pans the panel map there; you never travel from it.
- Refusals (422) show the server's reason in the card; 409 says "The GM is still resolving a turn."

- [ ] **Step 1:** Install the dependencies (`pnpm add leaflet@1.9.4 @types/leaflet@1.9.12`, as `build-frontend.cmd` does), then write the view.
- [ ] **Step 2: Build.** Run `build-frontend.cmd`. Expected: success.
- [ ] **Step 3: Browser check** on a seeded scratch campaign with the real reference data. Tiles render; the 3 known pins show; Lodge → Square travel animates, shows the identity image, and the mock narration appears; no console errors apart from network noise. Take a screenshot for the record.
- [ ] **Step 4: Commit** `"Map view: panel tile map, world inset, click to travel, establishing shot"`

---

## Done when

- From a seeded campaign, clicking a known place walks the party there over the graph. The clock advances by distance, the place becomes known, and the GM's arrival narration appears over the place's image.
- Flags gate routes exactly as the graph says, and refusals change nothing.
- Fixed Omens on the way stop travel and become visible. Roaming interceptions are deterministic and survive a save restore.
- No unknown place, spoiler record or unseen Omen reaches a player endpoint.
- Full suite green; the frontend builds; the browser check passes.

## Not in this plan

- Examining an Omen, its Choice, and starting a story: Phase 4.
- Area-placed Omens (`placement='area'`, district-wide) as interruptions: Phase 4 decides how a district Omen surfaces.
- Residents' schedules around town: Phase 7.
- Submaps (odyssey, sanctuary, terradomes, …): the planner already routes into them when flags allow, but only the town panel map is drawn.

## Left to the builder

- Marker, pin and overlay styling (Phase 8 restyles), and the exact animation easing.
- How the Leaflet zoom maps onto the 6 manifest levels (any mapping that requests only existing tiles).
