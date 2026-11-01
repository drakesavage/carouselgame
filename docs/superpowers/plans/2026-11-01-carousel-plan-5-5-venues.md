# Carousel Plan 5.5 — Venues (Sub-maps) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Places on the town map open into walkable venues. Arriving at a venue's place puts the group at its entry zone; the group moves between zones only along the venue's links; some Omens are sighted in their own zone instead of from the street; a story can block a passage or add a room as a logged layer; the player sees the zones they've reached and the exits from where they stand, as text.

**Architecture:**
- The 108 venues from `E:\Carousel\sublocations` are imported into `reference.db` like every other package: read-only, rebuildable, and protected on re-import.
- The group's position gains a venue and zone on the campaign row (and on `party_groups`) beside Plan 3's `node_id`.
- `travel` enters and leaves venues. A new `move_zone` op walks zone links; story-scoped `block_passage`, `open_passage` and `add_zone` ops change the links while a story runs and are lifted when it ends.
- The briefing gains a VENUE section. The player gets a venue panel whose zone clicks start a GM turn, the way map clicks do.

**Tech Stack:** Python 3.10, FastAPI, SQLite, pydantic v2, pytest; React/TS (one panel).

**Spec:** [docs/2026-10-30-carousel-game-design.md](../../2026-10-30-carousel-game-design.md) §6 (movement authority is the graph, never the art), §7 (seeing is safe, only the Choice commits). Rules: `E:\Carousel\sublocations\Carousel_Codex_Submap_Package_v1.1\Carousel_Submap_Mechanics_Rules_Addendum_v0.1.md` (SM-01) §§2–5, 7, 9, and its runtime contract's `release_tests` SMR-01, -03, -04, -09, -15, -17, -18, -19, where they agree with the design. Data: `Carousel_Codex_Submap_Spec_v1.json` `maps[*]` (`id`, `title`, `layer`, `members`, `graph.nodes`, `graph.edges`, `portals`, `omens`). Inputs surveyed in the [roadmap](2026-10-31-carousel-roadmap.md) "Phase 5.5 inputs". Builds on Plans 1–5 (branch `carousel`; Plan 5 adds `opening.stage()` and the arrival-lot start).

## Global Constraints

- **Source path:** `sublocations/Carousel_Codex_Submap_Package_v1.1/Carousel_Codex_Submap_Spec_v1.json`, source key `venues`. Only the spec JSON is imported, never the `production/` folder (work in progress, design §10.1: installed data only).
- **Zone IDs are `<venue>:<node id>`**, e.g. `SM-086:n03_lane_4_seating_and_bowl`. A venue's entry zone is its first node (the spec's `ENTRY` portal `arrive_at_node`). Its exit zone is the `EXIT` portal's `depart_from_node`.
- **Which venues can be entered:**
  - `layer` must be `persistent` or `conditional`, and at least one `members` place must have a travel-graph node. Conditional venues need no extra rule, because the travel graph's own `requires` flags already gate reaching their place.
  - `story_instance` and `extradimensional` venues, and venues with no member place on the graph, are imported but not enterable. Each gets an `import_report` line saying why.
- **Links:**
  - `walk` and `vertical_transition` edges are imported as two-way links.
  - `proposed_optional_shortcut` edges (`lock: off_by_default`) are **not imported**; the import reports their count.
- **Moving between zones costs no game time** (SM-01 §4: no universal click-equals-minutes rule). The GM uses `advance_time` for searching or waiting.
- **Zone-level Omen placement applies only when all three hold**; otherwise Plan 3's behaviour (place or roaming interruption, or the GM's `sight_omen`) is unchanged:
  1. the Omen's `ref.omens.spawn_mode` is `fixed`;
  2. its `ref.omens.place_id` is one of the venue's members;
  3. the venue omen's `placement_status` is `DESIGN_RECOMMENDATION_NOT_CANON_COORD`.

  `NEEDS_MANUAL_NODE_REVIEW` Omens are never zone-placed (their seed node is not truth). Where the venue data and the Omen data disagree on the place, the Omen data wins and the import reports it.
- **Leaving a venue** by `travel` is allowed only from its entry or exit zone. Otherwise: "leave by <entry label> or <exit label>".
- **Story overlays** need an active story, name the story, and are lifted when that story concludes. The clean baseline in `reference.db` is never changed.
- **The player sees** a venue's title and zone labels only for zones visited or adjacent to the current zone. They never see Omen IDs, zone placements, the director manifest, link modes or unvisited non-adjacent labels.
- No dice. Per-character split positions inside a venue, NPC movement, and death snapshots at a zone are Phases 6 and 7. Art is Phase 8. `E:\Carousel` and `E:\dccrpg` are read-only. Port 8797.

## Review Focus

1. **"Look around" or entering a zone starts a story** → it never does. `move_zone` and arrival only sight; `begin_story` still needs the typed Choice (SMR-01, -04). (Task 4: `test_entering_omen_zone_sights_only`)
2. **The player learns a room they haven't reached** through `/venue`, the map, events or messages. (Task 7: the crawl plants the unreached zone label `MINI-HIDDEN-ROOM`, SMR-19)
3. **The group is stuck:** standing in a story-added room when the story ends, or in a venue whose entry was blocked. The added room is lifted and the group moved to its connecting zone, with a logged event; a blocked move leaves the group where it was (SMR-09). (Task 5: `test_lifting_overlays_returns_the_group`)
4. **Save restore inside a venue** → the venue, zone, visits and overlays roll back together, and the same moves from the save behave the same (SMR-15). (Task 2: `test_restore_rolls_back_venue_state`)
5. **A re-import renames or drops a venue or zone a campaign stands in** → refused, and the old `reference.db` is kept. (Task 2: `test_reimport_protects_venue_and_zone`)

---

## File structure

```
backend/app/
  carousel/import_venues.py   # spec JSON → venues, venue_zones, venue_links, venue_places, venue_omens
  carousel/venues.py          # venue_at(node), entry/exit, neighbours (with overlays), visible_zones, zone_omens
  carousel/refdb.py, sources.py, importer.py, refs.py, interruptions.py
  rules/venue_ops.py          # move_zone, block_passage, open_passage, add_zone; lift_overlays(ctx, story_id)
  rules/travel_ops.py         # enter on arrival, leave only from entry/exit
  rules/story_ops.py          # conclude_story lifts the story's overlays
  db.py, memory/snapshots.py, campaigns.py
  memory/context.py, gm/prompts.py, gm/router.py
  venue_api.py                # GET /venue, POST /venue/move
frontend/src/views/VenuePanel.tsx, Story.tsx, api.ts
backend/tests/carousel/test_import_venues.py, test_venues.py, test_venue_ops.py, test_venue_api.py; mini_data.py
```

---

### Task 1: Import the venues

**Files:**
- Create: `backend/app/carousel/import_venues.py`
- Modify: `backend/app/carousel/sources.py` (`SOURCE_FILES["venues"]`), `backend/app/carousel/refdb.py` (tables), `backend/app/carousel/importer.py` (`IMPORTERS`, `COUNTED`), `backend/tests/carousel/mini_data.py`
- Test: `backend/tests/carousel/test_import_venues.py`

**Interfaces:**
- Tables in `reference.db`:
  - `venues (id TEXT PK, title, layer, enterable INTEGER, entry_zone, exit_zone, raw_json)`
  - `venue_zones (id TEXT PK, venue_id, node_id, label, area_type, floor INTEGER, story_only INTEGER DEFAULT 0)`
  - `venue_links (venue_id, a, b, mode)`: `a < b`, one row per pair.
  - `venue_places (place_id TEXT PK, venue_id)`
  - `venue_omens (omen_id, venue_id, zone_id, placed INTEGER, choice_action, PRIMARY KEY (omen_id, venue_id))`: `placed=1` only under the zone-placement rule.
- `import_venues(ref, files) -> None` writes `import_report` lines (category `venues`) for:
  - each not-enterable venue, with its reason (`story_instance`, `extradimensional`, `no member place on the travel graph`);
  - the skipped-shortcut count;
  - each Omen whose venue place disagrees with `ref.omens.place_id`;
  - each Choice action that differs from `ref.omens.choice_trigger` (today only OM-110).
- It must run after `import_places` and `import_omens` in `IMPORTERS`, since it reads `places`, `graph_nodes` and `omens`.
- Mini data, `venues` source with three venues:
  - `SM-T01` "Farm grounds", `persistent`, members `["CAR-086"]`. Zones:
    - `n01_farm_gate` (entry)
    - `n02_barn` (exit)
    - `n03_cellar_door`
    - `n04_MINI-HIDDEN-ROOM`

    Walk links 01–02, 02–03, 03–04, and one `proposed_optional_shortcut` 01–04. New mini Omen `OM-005` "A cellar door breathing" (fixed, place CAR-086, placement `place`, Choice `open:cellar_door`) sits at `n03_cellar_door` with `DESIGN_RECOMMENDATION_NOT_CANON_COORD`; add it to the Omen registry and give it a dossier. Its venue omens also list `OM-001`, at `n01_farm_gate` with `NEEDS_MANUAL_NODE_REVIEW`.
  - `SM-T02` "A train that isn't there", `story_instance`, members `["CAR-016"]`.
  - `SM-T03` "Unplaced alley", `persistent`, no members.

- [ ] **Step 1: Failing tests:**
  - Mini data:
    - SM-T01 is enterable with entry `SM-T01:n01_farm_gate` and exit `SM-T01:n02_barn`;
    - SM-T01 has 3 links (the shortcut is skipped and reported);
    - OM-005 is `placed=1`; OM-001 is `placed=0`;
    - SM-T02 and SM-T03 are not enterable, each with its reason in `import_report`.
  - Real data, using the `ref_real` fixture:
    - 108 venues, 718 zones, 610 links (648 minus 38 shortcuts; if any pairs repeat, pin the deduplicated count and report the repeats);
    - every `venue_places.place_id` resolves;
    - every placed Omen is `fixed` and in a member place;
    - OM-110 is reported.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"Import the 108 venues: zones, links, member places and zone-placed Omens"`

### Task 2: Venue state in the campaign

**Files:**
- Create: `backend/app/carousel/venues.py`
- Modify: `backend/app/db.py`, `backend/app/memory/snapshots.py`, `backend/app/campaigns.py` (`delete`), `backend/app/carousel/refs.py`
- Test: `backend/tests/carousel/test_venues.py`

**Interfaces:**
- Columns: `campaigns.venue_id TEXT`, `campaigns.zone_id TEXT`, `party_groups.venue_id TEXT`, `party_groups.zone_id TEXT`.
- Tables:
  - `zone_visits (campaign_id, zone_id, first_minutes, PRIMARY KEY (campaign_id, zone_id))`
  - `venue_overlays (id PK, campaign_id, story_id, venue_id, kind TEXT CHECK (kind IN ('block','add_zone')), a TEXT, b TEXT, label TEXT, reason TEXT, lifted INTEGER NOT NULL DEFAULT 0, created_minutes)`. For `add_zone`, `a` is the new zone's ID (`<venue>:story-<story_id>-<n>`) and `b` is the zone it connects to.
  - Both go in `SNAPSHOT_TABLES`, the delete order and `campaigns.delete`.
- `REFERENCE_COLUMNS`, one kind per column, as with `node` and `group_node`:
  - `venue` (`campaigns.venue_id`) and `group_venue` (`party_groups.venue_id`), both checked against `venues`;
  - `zone` (`campaigns.zone_id`), `group_zone` (`party_groups.zone_id`) and `visited_zone` (`zone_visits.zone_id`), all checked against `venue_zones`. A story-added zone (`:story-` in the ID) always resolves.
- `venues.py`:
  - `venue_at(conn, node_id) -> dict | None`: the enterable venue whose member place resolves to this node.
  - `zone(conn, zone_id) -> dict | None`: reference zones plus live `add_zone` overlays.
  - `neighbours(conn, cid, zone_id) -> list[dict]`: links plus unlifted added zones, minus unlifted blocks. Each item is `{zone_id, label, blocked_reason | None}`. Blocked links are kept so the GM sees them, marked with the reason.
  - `visible_zones(conn, cid) -> list[str]`: visited ∪ neighbours of the current zone.
  - `zone_omens(conn, zone_id) -> list[str]`: placed Omens.

- [ ] **Step 1: Failing tests:**
  - `neighbours` of `SM-T01:n03_cellar_door` is `[n02_barn, n04_MINI-HIDDEN-ROOM]`.
  - A `block` overlay row between 02 and 03 marks that link blocked.
  - `test_restore_rolls_back_venue_state`: save, set the zone and a visit and an overlay by SQL, restore → all three gone.
  - `test_reimport_protects_venue_and_zone`: a campaign standing in `SM-T01:n02_barn` is re-imported against mini data with that zone renamed → refused, naming the zone.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"Venue position, zone visits and story overlays in campaign state"`

### Task 3: Entering and leaving with travel

**Files:**
- Modify: `backend/app/rules/travel_ops.py`, `backend/app/carousel/interruptions.py`, `backend/app/campaigns.py` (`_place_on_map` and the Plan 5 opening start)
- Test: `backend/tests/carousel/test_venues.py`

**Interfaces:**
- `venues.enter(ctx_or_conn, cid, node_id, group_id=None) -> dict | None`:
  - If `venue_at(node)` exists, set the venue and its entry zone on the campaign and the PC's group, insert the visit, and return `{venue_id, zone_id, label}`.
  - Otherwise clear both fields and return `None`.
- `_apply_travel` before planning, while the group is in a venue: refuse unless the current zone is the venue's entry or exit zone. Error: "leave by <entry label> or <exit label>". An unlifted block doesn't affect this check, because blocks are on links, not exits.
- `_apply_travel` after arriving, call `enter(...)`:
  - Add `venue: {venue_id, title, zone_label}` to the result and the `travel` event payload. The payload carries the title and entry label only.
  - The arrival narration's prompt line in `map_api.arrival` names the entry zone.
- `interruptions._unseen` excludes Omens with a `venue_omens.placed=1` row, so they are never sighted from the street.
- `campaigns.create`: a start node with a venue (Plan 5's CAR-082 is in SM-048) enters it. So does `_place_on_map`.

- [ ] **Step 1: Failing tests:**
  - Travel to CAR-086 puts the group at `SM-T01:n01_farm_gate` with one visit, and OM-001 still interrupts as before.
  - OM-005 is never chosen by `decide` (`test_interruptions.py` still passes unchanged).
  - With the zone set to `n03_cellar_door` by SQL (`move_zone` arrives in Task 4), travel out is refused with the leave message. From `n02_barn` it succeeds and clears the venue.
  - Travel to CAR-016 enters nothing, because SM-T02 is a story instance.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"Arriving at a venue's place enters its entry zone; leaving only by its entry or exit"`

### Task 4: Moving between zones

**Files:**
- Create: `backend/app/rules/venue_ops.py`
- Modify: `backend/app/rules/__init__.py`
- Test: `backend/tests/carousel/test_venue_ops.py`

**Interfaces:**
- `move_zone {to, group?}`: `to` is a zone ID or an exact label in the current venue; `group` defaults to the PC's group.
  - Refused when:
    - not in a venue;
    - unknown zone;
    - not adjacent ("no way from <here> to <there>");
    - blocked ("<there> is blocked: <reason>"; the group stays put).
  - Also refused while Plan 5's `opening.stage()` is `car` or `silas`.
  - A refusal writes nothing, like Plan 3's `travel`. The player sees the refusal message (Task 7), and the GM sees the block in the VENUE section.
- Effects:
  - Set the zone; insert the visit.
  - Event `zone_entered`, player-visible: "The group moves to <label>".
  - For each placed Omen in the new zone with no `omen_states` row: insert `visible` at the campaign's node and write the same `omen_sighted` event shape as `travel`, with `"zone": <label>`.
  - Return `{summary, zone_id, sighted: [omen_id]}`.
- No game time passes.

- [ ] **Step 1: Failing tests:**
  - From the gate, `move_zone` to `n03_cellar_door` is refused (not adjacent). Gate → barn → cellar door succeeds.
  - `test_entering_omen_zone_sights_only`: entering the cellar door sights OM-005; no story row exists; the clock is unchanged.
  - Moving with a block overlay on 02–03 leaves the group in the barn.
  - Re-entering the cellar door sights nothing new.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"move_zone: walk venue links; Omens in a zone are sighted on entry, never committed"`

### Task 5: Story overlays

**Files:**
- Modify: `backend/app/rules/venue_ops.py`, `backend/app/rules/story_ops.py` (`_apply_conclude`)
- Test: `backend/tests/carousel/test_venue_ops.py`

**Interfaces:**
- All three ops need `active_story()`, refuse otherwise ("a venue changes only inside a story"), and record `story_id`:
  - `block_passage {a, b, reason (1–300)}`: the link must exist and not already be blocked. Event `passage_blocked`, player-visible, with the two labels and reason.
  - `open_passage {a, b, reason}`: lifts an unlifted block of the active story. A baseline link can't be "opened" if it was never blocked, and new links are not created; new routes use `add_zone`.
  - `add_zone {label (1–80), connect_to, reason}`: creates a zone `<venue>:story-<story_id>-<n>` connected to `connect_to` in the current venue. Event `zone_appeared`, player-visible, with the label.
- `lift_overlays(ctx, story_id) -> int`, called at the end of `_apply_conclude`:
  - Sets `lifted=1` on the story's overlays.
  - Any group standing in a lifted added zone moves to its `b` zone, with event `zone_vanished` ("<label> is gone; the group is back in <b label>").
  - Returns the number lifted.

- [ ] **Step 1: Failing tests** (with OM-005 begun on a typed turn, as in Plan 4):
  - Blocking barn ↔ cellar refuses that move.
  - Opening it again allows it.
  - `add_zone` "A second cellar" off the cellar door is reachable by `move_zone`.
  - All three are refused with no active story.
  - `test_lifting_overlays_returns_the_group`: conclude the story while standing in the added zone → the group is at `n03_cellar_door`, both overlays are lifted, and the baseline in `ref.venue_links` is unchanged.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"Story overlays: block, reopen and add rooms inside a story; lifted when it ends"`

### Task 6: The GM's briefing and contract

**Files:**
- Modify: `backend/app/memory/context.py`, `backend/app/gm/prompts.py`
- Test: `backend/tests/carousel/test_venue_briefing.py`

**Interfaces:**
- A `VENUE` section (`"state"` priority), present only while in a venue:
  - `<venue title> — you are at <zone label> (<area_type>)`.
  - `Ways on:` one line per neighbour, `<label>` or `<label> [blocked: reason]`.
  - `Zones seen:` the visited labels.
  - `Entry: <label>. Exit: <label>.`
  - `Overlays from this story:` the unlifted overlays.
  - GM-ONLY part: `Cues placed in this venue:` one line per `venue_omens` row for this venue, `<omen_id> at <zone label>: <ref description> (Choice <choice_trigger>)`, with `placed=0` rows marked "(GM places it)". This is how the GM knows the other Omens in the building without the engine pretending to know where they sit.
- `prompts.py`, one line: "Inside a venue the group moves only by move_zone along the ways listed; never place them in a room that isn't adjacent. Looking or walking never commits a Choice; a story's changes to the place go through block_passage, open_passage and add_zone."

- [ ] **Step 1: Failing tests:**
  - At the barn, the briefing lists `n03_cellar_door`'s label under Ways on, and the gate under Zones seen.
  - `MINI-HIDDEN-ROOM` is absent until the cellar door is reached.
  - The OM-005 cue line is in the GM-only part, never in the player part.
  - There is no VENUE section on the road.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"Briefing: VENUE section with ways on, seen zones, overlays and GM-only cues"`

### Task 7: What the player sees

**Files:**
- Create: `backend/app/venue_api.py`, `frontend/src/views/VenuePanel.tsx`
- Modify: `backend/app/main.py` (register), `backend/app/gm/router.py`, `frontend/src/views/Story.tsx`, `frontend/src/api.ts`, `frontend/src/storybar.css`, `backend/tests/test_visibility.py`
- Test: `backend/tests/carousel/test_venue_api.py`, `backend/tests/test_visibility.py`

**Interfaces:**
- `GET /api/campaigns/{cid}/venue` returns `null` when not in a venue, otherwise:
  ```
  {venue_id, title, zone: {id, label},
   ways: [{id, label, blocked: str | null}],
   seen: [{id, label}],
   entry: label, exit: label}
  ```
  Only `visible_zones` appear. No Omen IDs, modes or cues.
- `POST /api/campaigns/{cid}/venue/move {to, idempotency_key}`: the same pattern as `map_api.travel`.
  - `prepare` applies `move_zone` and returns `(text, kind)`, where `text` is "(The group moves to <label>.)" and `kind` is `zone_omen` if something was sighted, else `zone`.
  - It submits a server turn; a `ChangeError` maps to 422 with its message.
  - Returns `{move, turn}`.
- `router.SERVER_TURN_ROUTES` adds `zone: ("talk", "a quiet move inside a venue")` and `zone_omen: ("action", "an Omen in this room")`. The map was renamed in Plan 5; if Plan 5 kept the name `ARRIVAL_ROUTES`, add the entries there.
- `VenuePanel({ cid, version })`:
  - Shows the title and "You are at <label>".
  - Shows buttons for each way, disabled with the reason if blocked.
  - Lists the zones seen.
  - Shows "Leave by <entry>/<exit>" as plain text; leaving happens from the map.
  - Each button posts a move with a fresh idempotency key.
  - Rendered in `Story.tsx` under the StoryBar when `/venue` is non-null.
- Visibility crawl, in `test_no_gm_material_in_any_player_response`: after its existing travel to CAR-086, `POST /venue/move` to `SM-T01:n02_barn`. Add `MINI-HIDDEN-ROOM`, `OM-005` and `open:cellar_door` to `SECRETS`, and add `/venue` to `_player_urls`.

- [ ] **Step 1: Failing tests:**
  - At the barn, `/venue` shows ways `[gate, cellar door]` and no hidden room.
  - A move to the hidden room from the barn → 422 "no way from …".
  - The same idempotency key twice → one turn.
  - A move into the cellar door → turn kind `zone_omen`.
  - The crawl finds none of the three new secrets.
- [ ] **Steps 2–4:** RED → implement → GREEN. Then `build-frontend.cmd`, and `test_frontend_bundle_contains_no_secrets` passes.
- [ ] **Step 5: Manual check** with the mock GM on port 8799: travel to a real venue place (Town Square, SM-001), walk two zones, reload; the panel shows the same zone with no second turn.
- [ ] **Step 6: Commit** `"Players walk venues: a text panel of ways on and zones seen; zone clicks start the GM's turn"`
