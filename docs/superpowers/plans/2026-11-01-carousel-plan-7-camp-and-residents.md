# Carousel Plan 7: Camp Dyer and residents — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every campaign holds all 70 Dyer's Lodge residents from the start. Each one is somewhere in town according to a simple daily schedule around the safer places, down to the zone where a venue exists. The group can meet residents, recruit them and take them into stories, and coming back to camp gives the GM what changed to colour the return.

**Architecture:**
- **The residents:** `carousel/residents.py` imports the 70 templates into each campaign as unmet NPCs. A `met` flag on `npc_profiles` keeps unmet ones out of every player view.
- **The schedule:** `where()` computes a resident's position from overrides first (party, story, group, GM note), then from a schedule. The schedule is built from a small, editable data file of homes and hangouts, plus a game clock.
- **Story rules:** recruitment and story casting check that the resident is actually there.
- **Returning to camp:** the return is a briefing section built from the story history, posters and residents' movements since the group left. Nothing is simulated in the background (design §10).

**Tech Stack:** FastAPI, SQLite, pydantic ops, React/TS, pytest.

**Spec:** [docs/2026-10-30-carousel-game-design.md](../../2026-10-30-carousel-game-design.md) §10 and §11. Read it with the NPC package readme (`E:\Carousel\npcs\Carousel_Camp_NPC_Package_v1\Carousel_Camp_NPC_Readme_v1.md`, runtime controls 1–8) and the venue spec for SM-001, SM-027 and SM-028.

## Global Constraints

- **The residents:**
  - There are 70 (23 `canon_expanded`, 47 `original_adaptation`).
  - Provenance stays on every resident; `gm_only` never reaches a player view (readme controls 2 and 3).
  - Every resident starts alive (control 5).
- **Unmet residents are invisible to the player.** Their name, appearance and role appear in no player endpoint until `introduce_resident` meets them. What a met resident shows is limited to `known_to_new_arrival.after_introduction`, plus what play has recorded.
- **Hangouts are among the safer places only:**
  - Camp Dyer `CAR-046`, Dyer's Lodge `CAR-047`, Town Square `CAR-002` and the Diner `CAR-006`.
  - The bowling alley joins once Track B gives it a place.
  - Places named in a resident's own `usual_location` also count (only Cordoned Cove `CAR-179` today).
  - The Sanctuary (`CAR-230`) is not a hangout: its people are a different population.
- **Hangouts are an adaptation.** The hangout table is Claude's adaptation, not canon. It lives in the game repo as data (`backend/app/carousel/resident_hangouts.json`), marked `"provenance": "adaptation"`, and can be edited without changing code.
- **Game clock:** day 1 starts at 08:00 (`clock.CLOCK_START = 480`). This is Claude's call, and it's one constant.
- **Slots:** `night` 22:00–06:00, `morning` 06:00–12:00, `afternoon` 12:00–18:00, `evening` 18:00–22:00.
- **The world is reactive.** Residents never run stories, die or move off-screen through the engine. Their position is a pure function of the clock and the recorded overrides.
- **Commits:** commit with explicit paths only, end each message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, and leave E:\dccrpg and E:\Carousel untouched.

## Review Focus

1. **The briefing doesn't fill up with 70 residents.** Unmet residents who aren't around the group are left out of PARTY and PRESENT entirely, and the ones around get one line each in AROUND. A test pins the briefing size for a campaign with all 70 residents against a hard budget (Task 3).
2. **Re-imports.** A re-import that drops a resident template is refused while any campaign holds that resident (the existing `npc` reference kind). A re-import that adds one is picked up by `ensure()` the next time the campaign opens (Task 1).
3. **A recruit who isn't there.** `recruit_resident`, and adding a resident through `cast_character` or `begin_story`, are refused unless the resident is around the group, met and not `GONE` (Task 4).
4. **A new group meets the camp again.** After `new_group`, every imported resident is unmet again, but their memories and relationships stay (Task 1).
5. **Clock edges.** At exactly 22:00 the slot is `night`, at 06:00 `morning`, and minute 0 is 08:00 on day 1. The hangout pick changes by day, not by turn (Task 2).

---

### Task 1: All 70 residents in every campaign, unmet until introduced

**Files:**
- Create: `backend/app/carousel/residents.py`
- Modify: `backend/app/db.py` (`npc_profiles.met INTEGER NOT NULL DEFAULT 1`)
- Modify: `backend/app/campaigns.py` (`create` and `new_group` call it), `backend/app/carousel/refdata.py:open_campaign` or the campaign-open path (`ensure` per campaign once per process), `backend/app/rules/opening_ops.py` (`introduce_resident` meets)
- Modify: `backend/app/memory/views.py` (`npcs`, `campaign_summary` cards) and `backend/app/memory/context.py:379` (`chars`)
- Modify: `backend/tests/test_visibility.py`
- Test: `backend/tests/carousel/test_residents.py`

**Interfaces:**
- Produces:
  - `ensure(conn, cid) -> int`, which inserts every template the campaign lacks and returns how many it added.
  - `unmet_clause(alias: str = "c") -> str`, the SQL fragment `NOT EXISTS (… npc_profiles … met = 0)` used by the player views.
  - `in_briefing(conn, cid, chars: list[dict]) -> list[dict]`, which drops unmet residents who aren't around. Task 3 widens what "around" means; until then it means never.
- Each inserted resident gets:
  - a `characters` row (`kind 'npc'`, `roster_category 'allied_npcs'`, status `active`, `in_party 0`, `bio` = the template's `appearance`, `provenance 'resident <id>'`);
  - an `npc_profiles` row (`met 0`, `ref_id`, `personality`);
  - a `gm_secrets` `companion_sheet` holding the template's `gm_json`, and a `resident_public` secret holding `known_to_new_arrival.after_introduction`. That secret is GM-side until the resident is met.
- Relationships: the template's `relationships` become `relationships` rows between the residents, with `visibility 'gm'`, once both residents are in the campaign. Meeting a resident doesn't reveal them.
- `introduce_resident`:
  - It now sets `met = 1` (plus `in_party` and `location`) on the existing row instead of inserting one, and writes `after_introduction` lines as player-visible `lore_facts` (subject = the name).
  - It is refused when the resident is already met ("already met").
  - The current `_resident_in_campaign` insert path is used only when `ensure` hasn't run (no reference data).
- `new_group` sets `met = 0` on every resident row and `in_party 0` on residents.
- The seeded test campaign keeps its hand-made Adeline (`met` defaults to 1). `ensure` adds only the residents the seed lacks.

- [ ] **Step 1: Write the failing tests**

```python
def test_create_imports_every_resident_unmet(conn):        # mini: 2 templates → 2 rows, met 0, gm companion sheets present
def test_ensure_is_idempotent_and_adds_new_templates(...)  # second call → 0; a template inserted into ref → 1
def test_unmet_residents_stay_out_of_player_views(client, ...)  # GET npcs / campaign summary: no MINI resident name
def test_introduce_meets_the_existing_row(...)             # same character id; met 1; npc_met event; lore facts; second call refused
def test_relationships_import_gm_only(...)
def test_new_group_unmeets_everyone(...)                   # met 0; memories rows unchanged
def test_real_data_imports_70(real_ref_conn, ...)          # 70 rows; 23 canon_expanded by provenance of template
```

Crawl case: an unmet mini resident's name and its `after_introduction` text appear in no player endpoint.

- [ ] **Step 2: Run them.** Run `cd backend && python -m pytest tests/carousel/test_residents.py -v`. They should fail because the module doesn't exist yet.
- [ ] **Step 3: Implement it.** Every player view that lists characters gets `unmet_clause`. The five `GONE` sites from Plan 6 stay as they are.
- [ ] **Step 4: Run tests.** Run `python -m pytest tests -q`. It passes when everything is green, `test_opening_ops.py` and `test_campaigns.py` included. Update any character counts those tests pin.
- [ ] **Step 5: Commit.** `"Every campaign holds all 70 residents, unmet and unseen until introduced"`.

### Task 2: The game clock and resident schedules

**Files:**
- Create: `backend/app/carousel/clock.py` and `backend/app/carousel/resident_hangouts.json`
- Modify: `backend/app/carousel/residents.py` (`home`, `hangouts`, `where`), `backend/app/db.py` (table `resident_whereabouts`)
- Create: `backend/app/rules/resident_ops.py` (op `resident_whereabouts`)
- Modify: `backend/app/memory/snapshots.py` (SNAPSHOT_TABLES)
- Test: `backend/tests/carousel/test_schedules.py`

**Interfaces:**
- Produces:
  - `clock.hour(minutes) -> int`, `clock.slot(minutes) -> Literal["night","morning","afternoon","evening"]` and `clock.day(minutes) -> int` (day 1 = minute 0).
  - `residents.where(conn, cid, character_id) -> dict | None`, returning `{"place_id", "zone_id" | None, "why"}`. `why` is one of `party|story|group|note|home|hangout`, and `None` means `GONE`.
  - The op `resident_whereabouts {resident, place, zone?, until_minutes, reason}`, which writes a GM override, for example "Adeline is at the Diner until tonight".
- Table `resident_whereabouts`: `campaign_id, character_id, place_id, zone_id, until_minutes, reason`. One row per resident, replaced on each write.
- `where()` order, first match wins:
  1. `GONE` status gives `None`.
  2. `in_party` gives the group's position (`campaigns.node_id`/`zone_id`, or its `party_groups` row).
  3. In the running story's cast gives the group's position.
  4. An `assignment` gives that group's position.
  5. An unexpired `resident_whereabouts` row.
  6. Otherwise the schedule: `night`, `morning` and `evening` give `home`. `afternoon` gives `hangouts[hash(ref_id, day) % len(hangouts)]`, or `home` if there are none. The hash is `int(sha256(f"{ref_id}:{day}").hexdigest(), 16)`.
- `resident_hangouts.json` shape:
  - `{"provenance": "adaptation", "default_home": {"place": "CAR-047", "zone": "SM-028:n02_shared_living_common_ro"}, "homes": [{"match": "...", "place": ..., "zone": ...}], "usual_hangouts": [{"match": "...", "place": ...}], "affiliations": {"<affiliation>": [{"place": ..., "zone": ...}]}}`.
  - Matching is case-insensitive substring against `usual_location`, and the first match wins.
  - At load, entries whose place or zone isn't in the reference are dropped and reported with `refdb.report`-style lines in `GET /api/reference/status`. For example, the bowling alley has no place yet.

**`homes`, in order** (Lodge zones are in `SM-028`, camp zones in `SM-027`):

| Match | Zone |
|---|---|
| `library`, `archive`, `planning nook` | `n06_upper_private_rooms` |
| `map room`, `maps room`, `clue board` | `n04_main_hall_and_bedrooms` |
| `kitchen` | `n03_kitchen_and_pantry` |
| `storeroom` | `n07_storage_basement_provis` |
| `porch`, `watch desk` | `n01_lodge_front_steps` |
| `deck` | `n05_rear_door_to_service_de` |
| `common room`, `bulletin board` | `n02_shared_living_common_ro` |
| `cabin`, `quarters`, `bowler camp` | CAR-046, `n03_communal_campfire_squar` |
| `gate`, `arrival road`, `trailhead` | CAR-046, `n01_camp_main_road_entrance` |
| `challenge course`, `training`, `exercise`, `trails` | CAR-046, `n06_trail_to_abandoned_cabi` |
| `perimeter` | CAR-046, `n07_woods_boundary_trail` |
| `shed`, `workshop`, `woodshed` | CAR-046, `n08_camp_maintenance_back_g` |

**`usual_hangouts`:**

| Match | Place |
|---|---|
| `cordoned cove` | `CAR-179` |
| `town errands`, `offsite` | `CAR-002`, zone `SM-001:n01_square_central_approach` |
| `bowling alley` | the bowling alley place, once it exists (dropped and reported until then) |

**`affiliations`** (`CAR-002` → `SM-001:n01_square_central_approach`, `CAR-006` → `SM-001:n02_diner_front_and_interio`):

| Affiliation | Hangouts |
|---|---|
| Atlas Table | `CAR-002`, `CAR-006` |
| Free Agents | `CAR-006`, `CAR-002` |
| Lantern Team | `CAR-002` |
| Kitchen Table | `CAR-006` |
| Travis's Crew | `CAR-002` |
| social specialists | `CAR-006`, `CAR-002` |
| researchers | `CAR-002` |
| independent | `CAR-006` |
| The Bowlers | the bowling alley, once it exists |
| Lodge Operations, camp leadership, Boundary Watch, Clinic Circle, veteran training | none (camp only) |

A resident's hangouts are their `usual_hangouts` matches followed by their affiliation's list, with duplicates removed.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("minutes,slot", [(0, "morning"), (4*60, "afternoon"), (14*60, "night"), (22*60, "morning"), (10*60 - 1, "afternoon")])
def test_slots(minutes, slot): assert clock.slot(minutes) == slot    # 08:00 + minutes; 22:00 is night, 06:00 morning

def test_where_follows_overrides_in_order(...)     # party > story cast > group > note > schedule; GONE → None
def test_afternoon_hangout_is_stable_within_a_day(...)   # same day, two times → same place; another day may differ
def test_note_expires(...)                         # until_minutes passed → schedule again
def test_real_data_every_resident_has_a_home(real_ref_conn)   # all 70 resolve; pin the default_home fallback count
def test_bowling_alley_is_reported_until_it_exists(real_ref_conn)   # report line names the Bowlers; Bowlers' afternoon = home
```

- [ ] **Step 2: Run them.** Run `python -m pytest tests/carousel/test_schedules.py -v`. They should fail.
- [ ] **Step 3: Implement it.** Load the JSON once per reference DB path, and validate it against `ref.places` and `ref.venue_zones`.
- [ ] **Step 4: Run tests.** Run `python -m pytest tests -q`. It passes when everything is green.
- [ ] **Step 5: Commit.** `"Residents keep a daily schedule: home zones at camp, afternoon hangouts at the safer places"`.

### Task 3: Who's around

**Files:**
- Modify: `backend/app/carousel/residents.py` (`around`)
- Modify: `backend/app/memory/context.py` (an `AROUND` section with `CATEGORIES["AROUND"] = "state"`, GM-ONLY lines, and `in_briefing`)
- Modify: `backend/app/rules/travel_ops.py` and `backend/app/rules/venue_ops.py` (update `last_seen` on arrival and on zone entry)
- Test: `backend/tests/carousel/test_around.py`

**Interfaces:**
- Consumes: `where`.
- Produces: `around(conn, cid) -> list[dict]`. It lists residents (met or not) whose `where()` matches the group's `node_id`, and its `zone_id` when the group is in a venue and the resident has a zone. Each entry is `{id, name, met, why}`, and party members are left out.
- **AROUND section:**
  - A met resident: `"Adeline Winter (met) — Camp leader; newcomer orientation and arbitration"`.
  - An unmet one: `"Not yet met: CAR-NPC-003 — <appearance>; <camp_role>. Introduce with introduce_resident if they come into the scene."`
  - At most 12 lines; past that, the rest are shown as a count.
- **GM-ONLY lines:** for those around, the companion sheet's `adaptation_hook`, nothing else.
- **`in_briefing`** now keeps residents who are around.
- **`last_seen`:** on `travel` arrival and `move_zone`, every met resident around has `npc_profiles.last_seen` set to `"<place name>[, <zone label>] at day N <slot>"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_around_matches_place_and_zone(...)          # CAR-047 common room at 09:00 → Adeline (home); group in kitchen zone → not
def test_around_section_and_gm_hook(...)             # AROUND lists met and unmet lines; hook only in GM-ONLY SECRETS
def test_briefing_stays_small_with_70_residents(real_ref_conn, ...)   # all 70 ensured, group at CAR-047 at 09:00:
                                                     # AROUND ≤ 13 lines; PRESENT has no unmet resident; total briefing < budget
def test_last_seen_updates_on_arrival(...)
```

- [ ] **Step 2: Run them.** Run `python -m pytest tests/carousel/test_around.py -v`. They should fail.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run tests.** Run `python -m pytest tests -q`. It passes when everything is green.
- [ ] **Step 5: Commit.** `"Briefing: residents around the group, one line each; last seen updates as the group moves"`.

### Task 4: Recruiting residents into the group and into stories

**Files:**
- Modify: `backend/app/rules/resident_ops.py` (`recruit_resident`, `dismiss_resident`)
- Modify: `backend/app/rules/story_ops.py` (presence check in `_apply_begin` and `_apply_cast`)
- Test: `backend/tests/carousel/test_recruit.py`

**Interfaces:**
- Consumes: `around`, `where`, `fate.GONE`.
- Produces: the ops `recruit_resident {resident, reason}` and `dismiss_resident {resident, where?: place, reason}`.
- `recruit_resident` is refused when:
  - the resident isn't met ("meet them first: introduce_resident");
  - they aren't around ("Adeline Winter is at Dyer's Lodge, not here");
  - they are `GONE`;
  - their `npc_profiles.attitude` is below 0 ("won't come: attitude -1").
- Effects of `recruit_resident`: `in_party 1` and the player event "Adeline Winter joins the group".
- `dismiss_resident` sets `in_party 0`, writes a `resident_whereabouts` note (default `where` is their home, until the next 06:00) and the event "Adeline Winter heads back to camp".
- **Story casting:**
  - A resident added to a cast must be in the party or around. The PC and non-resident NPCs are unchanged.
  - Recruited residents count as the group for Plan 6's fate resolver, since they are in `story_cast`.

- [ ] **Step 1: Write the failing tests**

```python
def test_recruit_needs_met_present_and_willing(...)
def test_recruit_then_cast_into_story(...)            # recruited Adeline cast via cast_character; dies with the group → postered with them
def test_cast_refuses_an_absent_resident(...)         # begin_story with CAR-NPC-003 not around → refused
def test_dismiss_sends_them_home_until_morning(...)
```

- [ ] **Step 2: Run them.** Run `python -m pytest tests/carousel/test_recruit.py -v`. They should fail.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run tests.** Run `python -m pytest tests -q`. It passes when everything is green.
- [ ] **Step 5: Commit.** `"Residents join the group only when met, present and willing; story casting checks the same"`.

### Task 5: Returning to camp

**Files:**
- Modify: `backend/app/db.py` (`campaigns.left_camp_minutes INTEGER`, `campaigns.left_camp_event_id INTEGER`)
- Modify: `backend/app/rules/travel_ops.py` (set both when the group leaves CAR-046 or CAR-047; on arrival there, `effects` mark a camp return)
- Modify: `backend/app/memory/context.py` (`CAMP RETURN` section, `CATEGORIES["CAMP RETURN"] = "state"`)
- Test: `backend/tests/carousel/test_camp_return.py`

**Interfaces:**
- Produces: `residents.camp_return(conn, cid) -> dict | None`. It returns data when the group stands at CAR-046 or CAR-047, `left_camp_minutes` is set, and the latest player event since `left_camp_event_id` is the arrival. The dict is `{"away_minutes", "stories": [{title, ending, cast_names}], "posters": [names], "with_group": [names], "around": [names]}`.
- `left_camp_minutes` is cleared on the first turn after the return, so the section shows once.
- **CAMP RETURN section** uses this exact copy:

  > The group is back at camp after {h}h{mm}. Since they left: {stories or "no stories"}; new posters: {names or "none"}. Residents around now: {names}. You may add colour (who noticed, rumours, who asks about the story), recorded with record_fact or remember. Nothing happened that the records don't show.

- [ ] **Step 1: Write the failing tests**

```python
def test_leaving_camp_starts_the_clock(...)
def test_return_section_lists_what_happened(...)     # leave, run and conclude a story, travel back → section with title and ending
def test_return_section_shows_once(...)
def test_no_section_when_never_left(...)
```

- [ ] **Step 2: Run them.** Run `python -m pytest tests/carousel/test_camp_return.py -v`. They should fail.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run tests.** Run `python -m pytest tests -q`. It passes when everything is green.
- [ ] **Step 5: Commit.** `"Coming back to camp briefs the GM on what changed while the group was away"`.

### Task 6: Player view: people around and the Lodge roster

**Files:**
- Create: `backend/app/resident_api.py` (register in `main.py`)
- Create: `frontend/src/views/AroundPanel.tsx`; modify the people/Lodge view and `frontend/src/api.ts`
- Modify: `backend/tests/test_visibility.py`
- Test: `backend/tests/carousel/test_resident_api.py`

**Interfaces:**
- **Endpoints:**
  - `GET /api/campaigns/{cid}/around` returns `[{"id", "name", "role"}]`, for met residents around only. Unmet residents are never listed, and the GM narrates them.
  - `GET /api/campaigns/{cid}/residents` returns `[{"id", "name", "role", "last_seen", "in_party", "known": [after_introduction lines]}]`, for met residents.
  - `POST /api/campaigns/{cid}/residents/{id}/talk` starts a GM turn of kind `talk_resident` (`SERVER_TURN_ROUTES["talk_resident"] = ("talk", "the player walks over to a resident")`), with the input "I go over to talk to {name}". It returns 409 unless they are around.
- **AroundPanel:** "Around you" with each met resident's name and role and a Talk button. It shows next to the VenuePanel, or under the map when the group isn't in a venue.
- **Lodge roster:** met residents with their last seen and whether they're with the group.
- **Crawl additions:** an unmet resident's name, an `adaptation_hook`, a `resident_whereabouts.reason` and a GM-only relationship's `kind` appear in no player endpoint.

- [ ] **Step 1: Write the failing tests**

```python
def test_around_lists_met_residents_only(...)
def test_roster_shows_last_seen_and_known_lines(...)
def test_talk_needs_them_here(...)                    # 409 away; 200 at camp, turn kind talk_resident
```

- [ ] **Step 2: Run them.** Run `python -m pytest tests/carousel/test_resident_api.py tests/test_visibility.py -v`. The new tests should fail.
- [ ] **Step 3: Implement the API and the views.** Build the frontend with `cd frontend && npm run build`. It passes when there are no type errors.
- [ ] **Step 4: Check it in the preview.**
  1. Start `carousel-preview` (8799) with a campaign at CAR-047 at 09:00.
  2. Introduce Adeline via the API and check that she appears in Around you.
  3. Click Talk and check that a GM turn starts.
  4. Advance to the afternoon and travel to the Diner, then check that the Atlas Table residents met so far show there.
- [ ] **Step 5: Run tests.** Run `python -m pytest tests -q`. It passes when everything is green.
- [ ] **Step 6: Commit.** `"Players see the residents around them and the Lodge roster; unmet residents stay unseen"`.

---

## Left out on purpose

- **Not built:**
  - Residents acting on their own: no off-screen stories, deaths or schedule drift (design §10).
  - Per-character positions inside a venue: the group still moves as one.
  - Picking legal tickets for original residents (readme control 4). Their tickets are granted in play with `grant_ticket` when they matter.
  - Faction mechanics.
- **The bowling alley:** the Bowlers' hangout waits for Track B to give it a place, then needs only a re-import.
- **Time-of-day Omens:** they could use `clock.slot` later. They aren't wired up here.
