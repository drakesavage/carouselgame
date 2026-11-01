# Carousel Plan 6: Death and fate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Characters can die, be written off or vanish through logged engine ops. A story's end restores everyone if anyone survived, or posts the whole group as missing posters. Posters can be taken from the Diner board and used to attempt a rescue. A wiped player starts a new group in the same town. The dead player watches the rest of the story (Deathwatch). Players can save only at camp.

**Architecture:** Five fate columns on `characters`. Three new campaign tables: `death_events`, `missing_posters` and `danger_warnings`. New ops live in `rules/fate_ops.py`, and `conclude_story` calls one resolver that runs in the System Bible order: objective, fates, restoration or posters, rescue, rewards. The camp-only save rule is a single function, `fate.at_camp`, used by the three player-facing save paths.

**Tech Stack:** FastAPI, SQLite, pydantic ops (`rules/engine.py` CHANGE_OPS), React/TS, pytest.

**Spec:** [docs/2026-10-30-carousel-game-design.md](../../2026-10-30-carousel-game-design.md) §8, §8.1 and §11. Read it with System Bible v0.4.1 §2.6 and §11 (`E:\Carousel\system\Carousel_System_Bible_v0.4.1.md`), Addendum SM-01 §10 (`E:\Carousel\sublocations\Carousel_Codex_Submap_Package_v1.1\`), and the Rescue tropes in the Trope Bible (`ref.tropes`: 13 have `trope_type` containing `Rescue`).

## Global Constraints

- **No dice.** Nothing in this plan rolls, and the Bible's "RNG" lines are read as "the GM's narrative judgement from the ledger" (design §7.1).
- **Separate axes.** The five axes are never inferred from one another:
  - `physical_condition`: `alive|injured|dying|dead|uncertain`
  - `wallpaper_status`: `active|off_screen|dead|written_off|unknown`
  - `participation`: `full|limited|observer|posthumous_limited|none`
  - `persistent_fate`: `ongoing|restored|missing_postered|permanently_lost|unresolved`
  - `rescue_eligibility`: `not_applicable|possible|active|completed|failed|ineligible`
  - Two Bible values are not built: `alternate_handoff` (participation) and `locked` (rescue).
- **Who sets what:**
  - `participation` is computed by `fate.rights()` and is never set directly.
  - `persistent_fate` changes only in the end-of-story resolver, `permanent_loss`, rescue and `new_group`.
- **The group** means everyone in the story's `story_cast`, recruited residents included (design §8).
- **Restoration rule:** if any cast member is not physically `dead` and not `permanently_lost` at The End, everyone not `permanently_lost` is restored. Otherwise everyone not `permanently_lost` is postered.
- **Who sees what:**
  - GM-only: death `cause` (it can name the antagonist), `rescue_eligibility`, and a story's rescue posters. These must never appear in player views.
  - Player-visible: posters (name, Plot Armor, Archetype, last seen) and warnings.
- **The camp:**
  - Camp means `campaigns.node_id = 'CAR-047'`, with no active story and an opening stage of NULL or `done`.
  - A campaign with `node_id IS NULL` (no map) is treated as camp, so legacy and no-reference tests keep saving.
- **Diner:** the poster board is outside the Diner, `CAR-006`.
- **Commits:** this repo is edited by other threads too. Commit with explicit paths only, end each message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, and leave E:\dccrpg and E:\Carousel untouched.

## Review Focus

1. **A death retried on the next turn.** A second `set_fate physical=dead` on a dead character is refused with "already dead", and no second `death_events` row is written. This is tested in Task 2.
2. **A story ended while a fate is `uncertain`.** `conclude_story` refuses and names the character: the GM must decide first. This is tested in Task 3.
3. **A wipe during the opening Omen.** The opening blocker for the new group only counts that group's own stories (`stories.group_no`). Otherwise the dead group's story would wave the new group's opening Omen through. This is tested in Task 6.
4. **Saving from the save menu while away from camp.** `POST /saves` returns 409 with "You can only save at camp", and `save_game` gives the GM the same refusal. Restoring stays allowed from anywhere. This is tested in Task 5.
5. **A dead lead player-typed turn that tries to use a trope.** `use_trope` by a character whose participation is `observer` or `none` is refused. The only exception is the one ticket a `posthumous_window` names. This is tested in Task 2.

---

### Task 1: Fate state, records and the mini Rescue tropes

**Files:**
- Modify: `backend/app/db.py` (COLUMN_MIGRATIONS, SCHEMA)
- Create: `backend/app/carousel/fate.py`
- Modify: `backend/app/memory/snapshots.py` (SNAPSHOT_TABLES + delete order)
- Modify: the five `status NOT IN ('deceased','dead')` sites: `gm/orchestrator.py:394`, `memory/context.py:406,414,425`, `memory/generate.py:116` and `memory/views.py:83`. Each uses `fate.GONE`.
- Modify: `backend/tests/carousel/mini_data.py` (two tropes)
- Test: `backend/tests/carousel/test_fate.py`

**Interfaces:**
- Produces:
  - **New columns on `characters`:** `physical_condition TEXT NOT NULL DEFAULT 'alive'`, `wallpaper_status TEXT NOT NULL DEFAULT 'active'`, `participation TEXT NOT NULL DEFAULT 'full'`, `persistent_fate TEXT NOT NULL DEFAULT 'ongoing'`, `rescue_eligibility TEXT NOT NULL DEFAULT 'not_applicable'`, `rights_basis TEXT` (the ticket of an open posthumous window).
  - **Other new columns:** `campaigns.group_no INTEGER NOT NULL DEFAULT 1`, `stories.group_no INTEGER NOT NULL DEFAULT 1`, `stories.rescue_json TEXT` (GM-only: `{"character_id", "ticket_ref", "poster_ids": [...]}`) and `stories.deathwatch_pace TEXT NOT NULL DEFAULT 'full'` (`full|condensed`).
  - **Table `death_events`:** `id, campaign_id, character_id, story_id, phase, cause, last_action, earned_effects_json, witnesses_json, node_id, venue_id, zone_id, items_json, minutes, turn_id`. Rows are append-only, and a later restore or rescue never deletes one (Bible §11.9).
  - **Table `missing_posters`:** `id, campaign_id, character_id, group_no, name, plot_armor, archetype, last_seen, story_id, story_lock, death_event_id, posted_minutes, held_by_character_id, rescued_minutes`.
  - **Table `danger_warnings`:** `id, campaign_id, story_id, character_id (NULL = whole group), rule, warning, turn_id, minutes`.
  - **`fate.py`:**
    - `GONE = ("deceased", "dead", "missing", "lost")` and `DINER = "CAR-006"`.
    - `rights(physical: str, wallpaper: str, basis: str | None) -> str`.
    - `at_camp(conn, cid) -> bool`.
    - `deathwatch(conn, cid) -> dict | None`: `{"story_id", "pace"}` when the active character is in the running story's cast with participation `observer` or `none`.
    - `ticket_kinds(conn, ticket_ref: str) -> set[str]`: the `trope_type` parts split on `/`, plus `tags`, from `ref.tropes.variants_json` for `trope:<id>` refs, and an empty set otherwise.
    - `set_rights(conn, character_id) -> str`, which recomputes and stores `participation`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("phys,wall,basis,want", [
    ("alive", "active", None, "full"), ("injured", "active", None, "full"), ("dying", "active", None, "limited"),
    ("alive", "dead", None, "limited"),          # cinematic Dead on a living body (Bible §11.10 test 10)
    ("alive", "off_screen", None, "limited"), ("alive", "written_off", None, "none"),
    ("dead", "dead", None, "observer"), ("dead", "dead", "trope:3", "posthumous_limited"), ("uncertain", "unknown", None, "limited")])
def test_rights_follow_the_axes(phys, wall, basis, want): assert fate.rights(phys, wall, basis) == want

def test_new_characters_start_alive_and_ongoing(conn, pc):
    # every axis has its default; rescue not_applicable

def test_at_camp(conn, campaign_id):
    # seed is at CAR-002 → False; node CAR-047 → True; plus an active story → False; opening_stage 'escort' → False;
    # node_id NULL → True

def test_ticket_kinds_reads_trope_type_and_tags(conn):
    assert {"Rescue", "Story alteration"} <= fate.ticket_kinds(conn, "trope:2")
    assert "Death/sacrifice" in fate.ticket_kinds(conn, "trope:3") and fate.ticket_kinds(conn, "hb.ticket.base.final-girl") == set()

def test_fate_tables_roll_back_with_save_points(conn, campaign_id):
    # insert a missing_posters row after a save point, restore, row gone; same for death_events and danger_warnings
```

- [ ] **Step 2: Run them.** Run `cd backend && python -m pytest tests/carousel/test_fate.py -v`. They should fail because the `fate` module doesn't exist yet.
- [ ] **Step 3: Implement the schema, `fate.py` and the snapshot entries.**
  - Mini tropes: `{"id": "2", "name": "MINI-RESCUE-TICKET", "variants": [{"trope_type": "Rescue", "tags": ["Rescue", "Story alteration"], "description": "Rescue: enter the storyline holding the missing posters of players who died in it."}]}` and `{"id": "3", "name": "MINI-LAST-WORDS", "variants": [{"trope_type": "Action", "tags": ["Death/sacrifice"], "description": "Once, after death, one sentence reaches the living."}]}`. Both have `full_player: True`.
  - Bump the counts that `test_import_*` pins.
- [ ] **Step 4: Run tests.** Run `python -m pytest tests -q`. It passes when everything is green, including the visibility crawl.
- [ ] **Step 5: Commit.** `git commit -m "Fate state: five axes per character, death records, missing posters, warnings" -- <paths>`.

### Task 2: Harm, warnings, permanent loss and posthumous windows

**Files:**
- Create: `backend/app/rules/fate_ops.py`
- Modify: `backend/app/rules/__init__.py` (register)
- Modify: `backend/app/rules/carousel_ops.py` (`_apply_use` guard)
- Test: `backend/tests/carousel/test_fate_ops.py`

**Interfaces:**
- Consumes: from Task 1, `fate.rights`, `fate.set_rights` and `fate.ticket_kinds`; from story_ops, `_running` and `_cast_member`; from venues, `position`.
- Produces these ops:
  - `set_fate {character, physical?, wallpaper?, cause?, last_action?, earned_effects: list[str] = [], witnesses: list[str] = [], reason}`.
  - `warn_irreversible {rule, warning, character?}`.
  - `permanent_loss {character, rule, reason}`.
  - `posthumous_window {character, ticket, effect}`.
- Rules for `set_fate`:
  - It works only on a cast member of the running story ("fates change only inside a story; restoration happens at The End").
  - It needs at least one of `physical` and `wallpaper`.
  - Moving to physical `dead`:
    - It needs `cause` and `last_action`.
    - It writes one `death_events` row: the phase, the group's `node_id`/`venue_id`/`zone_id` (taken from the character's `party_groups` row if it has one, otherwise from the campaign), the names of items the character owns, and witnesses resolved to ids.
    - Events: a player event `character_died` ("Mara Vance dies (First Blood)"), whose payload has no `cause`, and a GM event `death_record` that has the cause and the earned effects.
  - Refused:
    - `physical=dead` on someone already dead.
    - Any `physical` change away from `dead` ("no resurrection mid-story; The End restores, or a rescue").
  - Wallpaper `dead` on a living body writes `wallpaper_changed` only, never a death record (SMR-13).
  - Every change ends with `set_rights`.
- Rules for `warn_irreversible`: it writes a player-visible `danger_warnings` row and event ("Warning: …").
- Rules for `permanent_loss`:
  - It needs a `danger_warnings` row with the same `rule` and a `turn_id` lower than this turn's. With no turn, it is refused.
  - Effects: fate `permanently_lost`, rescue `ineligible`, participation `none`, status `lost`, `in_party 0`.
  - It is allowed outside a story (for example, trying to leave town).
- Rules for `posthumous_window`:
  - The character must be physically `dead` and own the ticket equipped with uses left.
  - The ticket must be `hb.ticket.advanced.departed`, `hb.ticket.aspect.final-girl.scream-queen`, or have the tag `Death/sacrifice` (from `ticket_kinds`).
  - Effects: it sets `rights_basis` to the ticket, so participation becomes `posthumous_limited`.
- The `use_trope` guard:
  - It refuses when participation is `observer` or `none`.
  - With `posthumous_limited`, it accepts only the `rights_basis` ticket, then clears `rights_basis` (rights go back to `observer`).

- [ ] **Step 1: Write the failing tests**

```python
def test_death_writes_one_record_and_hides_the_cause(conn, campaign_id, running, pc):
    r = on_turn(conn, campaign_id, turn(conn, campaign_id), {"op": "set_fate", "character": "Mara Vance", "physical": "dead",
        "wallpaper": "dead", "cause": "CAUSE-SECRET-LIMA-7", "last_action": "held the barn door", "earned_effects": ["pursuer delayed 3 beats"],
        "witnesses": ["Jonah Reyes"], "reason": "the scarecrow"})
    d = one(conn, "SELECT * FROM death_events WHERE character_id = ?", (pc,))
    assert d["phase"] == "Party" and d["node_id"] == "CAR-002" and json.loads(d["items_json"]) == ["Flashlight", "Healing Potion"]
    [died] = events(conn, campaign_id, "character_died"); assert died["visibility"] == "player" and "CAUSE-SECRET" not in json.dumps(died)
    assert events(conn, campaign_id, "death_record")[0]["visibility"] == "gm"
    assert char(conn, "mara")["participation"] == "observer"

def test_dying_twice_is_refused_and_writes_nothing(...):          # "already dead"; still one death_events row
def test_no_resurrection_mid_story(...):                           # dead → alive refused
def test_cinematic_dead_is_not_a_death(...):                       # wallpaper dead only: no death_events row, participation limited
def test_fate_changes_need_a_running_story_and_a_cast_member(...): # no story → refused; Adeline (not cast) → refused
def test_permanent_loss_needs_an_earlier_warning(...):             # same-turn warning refused; earlier-turn warning accepted; status lost
def test_dead_cannot_use_tropes_except_the_window(...):            # observer use_trope refused; window with trope:3 → one use, then observer
def test_window_needs_a_death_ticket(...):                         # trope:2 (Rescue, no Death/sacrifice tag) refused
```

- [ ] **Step 2: Run them.** Run `python -m pytest tests/carousel/test_fate_ops.py -v`. They should fail with "unknown op set_fate".
- [ ] **Step 3: Implement `rules/fate_ops.py` and the `_apply_use` guard.**
- [ ] **Step 4: Run tests.** Run `python -m pytest tests -q`. It passes when everything is green.
- [ ] **Step 5: Commit.** `"Fate ops: deaths with a frozen snapshot, cinematic status, warnings, permanent loss, posthumous windows"`.

### Task 3: The End: restoration or missing posters

**Files:**
- Modify: `backend/app/rules/story_ops.py` (`_apply_conclude`)
- Modify: `backend/app/rules/fate_ops.py` (`resolve_fates`)
- Test: `backend/tests/carousel/test_fate_end.py`

**Interfaces:**
- Consumes: Task 1's tables and Task 2's columns.
- Produces: `resolve_fates(ctx, story: dict, cast: list[dict]) -> dict`, which returns `{"outcome": "restored" | "postered", "restored": [ids], "posters": [ids]}`.
- `_apply_conclude` order:
  1. Validate the ending.
  2. Refuse if any cast member has physical `uncertain`.
  3. Update the story row and emit `story_concluded`.
  4. Call `resolve_fates` (emits `fate_restored` or `poster_created` per character).
  5. Rescue (Task 4).
  6. Pay stars.
  7. Lift the overlays.
- **Restored:**
  - Every cast member not `permanently_lost` goes to physical `alive`, wallpaper `active`, `rights_basis NULL` and participation `full`.
  - `persistent_fate` becomes `restored` for anyone who was `dead`, `dying` or `injured`; for everyone else it stays as it was.
  - Every `polarity='debuff'` status effect of those characters is removed, listed in the event.
  - Nobody moves: survivors stay where they are (SM-01 §10, SMR-14).
- **Postered:**
  - Every cast member not `permanently_lost` gets fate `missing_postered`, rescue `possible`, status `missing`, `in_party 0` and `assignment NULL`.
  - Each gets one `missing_posters` row: `plot_armor` = `sheet()["plot_armor"]["effective"]`, `archetype` = the role's name, and `last_seen` = the place name plus the zone label if there is one, taken from that character's `death_events` row (or from the group's position if there is none).
  - The player event is "A missing poster goes up: Mara Vance".
- Rewards are unchanged. A dead PC still needs its award.

- [ ] **Step 1: Write the failing tests**

```python
def test_one_survivor_restores_everyone(...):         # Mara dead, Jonah alive → defeat ending → Mara alive/active/full, fate restored,
                                                      # Jonah fate ongoing; Mara's "Hobbled" debuff gone; death_events row kept
def test_restored_group_stays_where_it_is(...):       # node unchanged, not snapped to CAR-047
def test_whole_group_dead_makes_posters(...):         # both dead → two posters, PA/archetype/last seen "Town Square"; status missing
def test_victory_after_a_wipe_still_posters(...):     # Finale + full_success with everyone dead → posters (Bible test 3/4)
def test_uncertain_fate_blocks_the_end(...):          # "decide Jonah Reyes's fate first"
def test_permanently_lost_is_never_restored(...):     # Jonah lost, Mara alive → Mara restored, Jonah stays lost, no poster
def test_end_events_follow_the_bible_order(...):      # story_concluded < fate_restored|poster_created < stat_point_earned (event ids)
```

- [ ] **Step 2: Run them.** Run `python -m pytest tests/carousel/test_fate_end.py -v`. They should fail.
- [ ] **Step 3: Implement `resolve_fates` and reorder `_apply_conclude`.**
- [ ] **Step 4: Run tests.** Run `python -m pytest tests -q`. It passes when everything is green, `test_rewards.py` included.
- [ ] **Step 5: Commit.** `"The End: one survivor restores the group; a wipe puts everyone on the missing-poster board"`.

### Task 4: Taking posters and rescue stories

**Files:**
- Modify: `backend/app/rules/fate_ops.py` (`take_poster`, `resolve_rescue`)
- Modify: `backend/app/rules/story_ops.py` (`BeginStory.rescue`, the `_apply_revise` takeover, and a conclude hook)
- Test: `backend/tests/carousel/test_rescue.py`

**Interfaces:**
- Consumes: Task 3's posters, `ticket_kinds`, and `_owned` and `_apply_use` from carousel_ops.
- Produces: the op `take_poster {poster: int, character}`; the `BeginStory` field `rescue: RescueEntry | None` with `RescueEntry {character, ticket, posters: list[int]}`; and `resolve_rescue(ctx, story, ending) -> dict | None`.
- Rules for `take_poster`:
  - The group must be at `CAR-006`, the poster must be on the board (`held_by_character_id IS NULL` and not rescued), and the taker must not be in `GONE`.
  - The player event is "Mara Vance takes down Jonah Reyes's poster".
- Rules for `begin_story` with `rescue`:
  - The rescuer is in the cast and owns the ticket equipped with uses left, and the ticket has kind `Rescue`.
  - Each poster is held by that rescuer, has the story's `story_lock`, and its character has rescue `possible`.
  - Effects: one use is spent through `_apply_use`, `stories.rescue_json` is set, and the posters' characters get rescue `active`.
  - The GM event lists the poster ids; the player event is "This story is a Rescue".
- **Takeover:** in a story with `rescue_json`, `revise_screenplay` is refused for every ticket except the rescue ticket (the Rescue tropes' Takeover clause).
- Rules for `resolve_rescue`, called after `resolve_fates`:
  - `full_success` or `costly_success` rescues each poster's character: rescue `completed`, fate `restored`, physical `alive`, wallpaper `active`, participation `full`, status `active`, and `rescued_minutes` set on the poster. The player event is "Jonah Reyes is found".
  - Any other ending sets rescue `failed`, which is final.
  - In both cases the poster's `held_by_character_id` is cleared.

- [ ] **Step 1: Write the failing tests**

```python
def test_take_poster_only_at_the_diner(...):            # at CAR-002 refused; travel to CAR-006 then accepted; second take refused
def test_rescue_needs_a_rescue_ticket_and_the_poster(...): # trope:3 refused; poster of another story_lock refused; unheld refused
def test_rescue_success_brings_them_back(...):          # wipe in OM-001 → new PC (Task 6 helper not needed: insert a pc by SQL) holds
                                                        # Mara's poster, trope:2 equipped → begin OM-001 with rescue → Finale → full_success
                                                        # → Mara active/alive/restored, rescue completed, death_events rows still there
def test_failed_rescue_is_final(...):                   # defeat → rescue failed; a second rescue with that poster refused
def test_rescue_takeover_blocks_other_revisions(...):   # revise_screenplay with Mara's final-girl ticket refused in a rescue story
def test_rescue_posters_stay_gm_only(...):              # stories.rescue_json and rescue_eligibility absent from player views
```

- [ ] **Step 2: Run them.** Run `python -m pytest tests/carousel/test_rescue.py -v`. They should fail.
- [ ] **Step 3: Implement it.** `spawn_rescue_packet` (the Damsel's ticket) stays out of scope. Update the comment in `story_ops.py:23` to say so.
- [ ] **Step 4: Run tests.** Run `python -m pytest tests -q`. It passes when everything is green.
- [ ] **Step 5: Commit.** `"Missing posters can be taken from the Diner board and carried into a Rescue of the same story"`.

### Task 5: Save points only at camp

**Files:**
- Modify: `backend/app/gm/orchestrator.py:238-240`, `backend/app/gm/tools.py:116-119` (`save_game`) and `backend/app/main.py:796-798`
- Test: `backend/tests/carousel/test_camp_saves.py`, plus updates to `backend/tests/test_saves.py`

**Interfaces:**
- Consumes: `fate.at_camp`.
- Behaviour:
  - Autosave after a turn runs only if `at_camp` is true.
  - `save_game` away from camp returns the tool error "You can only save at camp (Dyer's Lodge, between stories)", and `effects.save_label` stays unset.
  - `POST /saves` away from camp returns 409 with the same text.
  - System saves that call `create_save_point` directly are unchanged: "Campaign started", "Arrived at Dyer's Lodge", pre-restore saves and the new-group save.
  - Restore is unchanged.

- [ ] **Step 1: Write the failing tests**

```python
def test_no_autosave_away_from_camp(client, conn, campaign_id):   # seed at CAR-002: a turn adds no auto save
def test_autosave_at_camp(...):                                   # UPDATE node_id = 'CAR-047': a turn adds one auto save
def test_manual_save_refused_mid_town_and_mid_story(...):         # 409 at CAR-002; 409 at CAR-047 with a running story; 200 at camp
def test_save_game_tool_refused_away_from_camp(...):              # tool result carries the text; no manual save after the turn
def test_restore_works_from_anywhere(...):                        # save at camp, travel away, restore → back at CAR-047
```

`test_saves.py` uses the seed at CAR-002. Set `node_id = 'CAR-047'` in both of its tests, so they still test what they did before.

- [ ] **Step 2: Run them.** Run `python -m pytest tests/carousel/test_camp_saves.py tests/test_saves.py -v`. The new tests should fail.
- [ ] **Step 3: Implement the three checks.**
- [ ] **Step 4: Run tests.** Run `python -m pytest tests -q`. It passes when everything is green.
- [ ] **Step 5: Commit.** `"Save points only at camp: autosave, save_game and the save menu check fate.at_camp"`.

### Task 6: A new group in the same town

**Files:**
- Modify: `backend/app/campaigns.py` (`new_group`)
- Modify: `backend/app/carousel/opening.py` (`blockers`, opening_omen stage)
- Modify: `backend/app/rules/story_ops.py` (`_apply_begin` stamps `group_no`)
- Modify: `backend/app/opening_api.py` (`POST /api/campaigns/{cid}/new-group`)
- Test: `backend/tests/carousel/test_new_group.py`

**Interfaces:**
- Consumes: `_start_opening`, `slug` and `create_save_point` in campaigns.py.
- Produces: `new_group(conn, cid, character: str) -> int`, which returns the new PC's id.
- Rules for `new_group`:
  - It is refused unless the active character's fate is `missing_postered` or `permanently_lost`, or while a story runs or a turn is processing (the API holds the write lock).
  - Inside one transaction it:
    1. Increments `campaigns.group_no`.
    2. Inserts the PC (kind `pc`, `in_party 1`).
    3. Sets `active_character_id`.
    4. Sets `in_party 0` on every other character still in the party.
    5. Clears the group's knowledge (`known_places`, `omen_states` and `zone_visits` for the campaign) and the lure columns.
    6. Calls `_start_opening` (stage `car`, at CAR-082).
    7. Writes the save point "A new group arrives".
  - World state stays: the clock, posters, `stories`, `story_spoilers`, residents and their memories, `gm_secrets` and `lore_facts`.
- Opening blocker: the opening_omen check counts only stories with `group_no` = the campaign's `group_no`.
- API: `POST /new-group {character}` returns `{"character_id"}`, or 409 with the refusal text.

- [ ] **Step 1: Write the failing tests**

```python
def test_new_group_only_after_a_wipe(...):                 # active PC ongoing → refused
def test_new_group_keeps_the_town(...):                    # after a wipe: posters, stories, game time, Adeline unchanged; known_places
                                                           # only CAR-082; opening_stage car; group_no 2; old PC status missing
def test_new_groups_opening_omen_needs_its_own_story(...): # advance to opening_omen with group 2: blocker "no story has started"
                                                           # despite group 1's concluded story
def test_new_group_api(client, ...):                       # 409 before a wipe; 200 after; GET /api/campaigns/{cid} shows the new PC
```

- [ ] **Step 2: Run them.** Run `python -m pytest tests/carousel/test_new_group.py -v`. They should fail.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run tests.** Run `python -m pytest tests -q`. It passes when everything is green.
- [ ] **Step 5: Commit.** `"A wiped player starts a new group in the same town: posters, residents and story history stay"`.

### Task 7: GM briefing: FATE and DEATHWATCH

**Files:**
- Modify: `backend/app/memory/context.py` (`_fate_section`, a `CATEGORIES` entry `"FATE": "state"`, and GM-only lines in the `GM-ONLY SECRETS` section)
- Modify: `backend/app/gm/prompts.py` (op rules)
- Test: `backend/tests/carousel/test_fate_briefing.py`

**Interfaces:**
- Consumes: `fate.deathwatch` and the Task 1 tables.
- **FATE section** (only when a story runs, or when posters exist):
  - One line per cast member with any axis off its default: `"Mara Vance: body dead · wallpaper dead · participation observer"`.
  - Then `"Warnings given: <rule>: <warning>"` for this story.
  - Then `"Board outside the Diner: N posters (names)"`.
- **DEATHWATCH lines** (when `deathwatch()` is set) use this exact copy:

  > The player's character can't act. The player is watching. Treat what they type as requests for a cut, a pace or a summary, never as orders to the survivors. Survivors act on their own knowledge, goals and fear. No convenient rescue, no forced tragedy; the story runs to a real ending. Pace: {full|condensed} (narration length only).

- **GM-only lines:** death causes for this story; each poster's rescue eligibility; and, for a rescue story, which posters it is for and the line "Takeover: only the rescue ticket may revise this story".
- **Firewall line:** when the active PC has a `death_events` row in the last concluded story and was restored, add a line to the CAMPAIGN section: "Mara died at <phase> in <title>; she doesn't know what happened after that unless told". Use the character's recorded pronouns, falling back to "they".
- **Op rules in prompts:**
  - `set_fate` only for what happened in the fiction.
  - `warn_irreversible` before any rule that can cause permanent loss.
  - `permanent_loss` only after a warning on an earlier turn.

- [ ] **Step 1: Write the failing tests**

```python
def test_fate_section_lists_changed_axes_and_warnings(...)
def test_deathwatch_copy_appears_only_while_the_pc_cant_act(...)
def test_causes_and_eligibility_are_gm_only(...)     # in GM-ONLY SECRETS, not in FATE
def test_restored_pc_firewall_line(...)
```

- [ ] **Step 2: Run them.** Run `python -m pytest tests/carousel/test_fate_briefing.py -v`. They should fail.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run tests.** Run `python -m pytest tests -q`. It passes when everything is green.
- [ ] **Step 5: Commit.** `"Briefing: FATE section, Deathwatch rules, GM-only causes and rescue eligibility"`.

### Task 8: Player view: Deathwatch, the poster board and a new group

**Files:**
- Create: `backend/app/fate_api.py` (register in `main.py`, like `venue_api.py`)
- Modify: `backend/app/memory/views.py` (character cards add the four player-visible axes; `campaign_summary.fate`)
- Create: `frontend/src/views/DeathwatchBar.tsx` and `frontend/src/views/PosterBoard.tsx`
- Modify: the story view (mount DeathwatchBar), the people/Lodge view (mount PosterBoard), `frontend/src/api.ts`
- Modify: `backend/tests/test_visibility.py` (crawl)
- Test: `backend/tests/carousel/test_fate_api.py`

**Interfaces:**
- **Endpoints:**
  - `GET /api/campaigns/{cid}/fate` returns `{"deathwatch": {"pace"} | null, "wiped": bool, "cast": [{"id", "name", "physical_condition", "wallpaper_status", "participation", "persistent_fate"}]}`.
  - `POST /api/campaigns/{cid}/deathwatch/pace {pace: "full"|"condensed"}` is 409 unless Deathwatch is on. It writes `stories.deathwatch_pace` and a player event.
  - `GET /api/campaigns/{cid}/posters` returns `[{"id", "name", "plot_armor", "archetype", "last_seen", "posted_minutes", "held_by", "found": bool}]`, but only once `known_places` has CAR-006 with `how='visited'` or the group stands there; otherwise `[]`.
- **DeathwatchBar:** "You're watching. {name} can't act." plus two pace buttons (Watch it all / Condensed), and the input placeholder becomes "Ask for a cut, a pace, or a summary".
- **Wiped screen:** "Your group is on the missing-poster board." with a name field and a "Start a new group" button that calls `POST /new-group`, and a link to the save list (restoring the last camp save is the other way back).
- **PosterBoard:** a grid of poster cards (name, Plot Armor, Archetype, last seen), with "Taken by {name}" or "Found" when those apply.
- **Crawl secrets:** `CAUSE-SECRET-LIMA-7` (a death cause), the word `possible` from rescue eligibility (check for the key `rescue_eligibility`, not the word), and a rescue story's `rescue_json`.

- [ ] **Step 1: Write the failing tests**

```python
def test_fate_endpoint_shows_axes_never_rescue(...)
def test_pace_needs_deathwatch(...)                  # 409 alive; 200 after Mara dies; stories.deathwatch_pace condensed
def test_poster_board_needs_a_diner_visit(...)       # [] before; one card after travel to CAR-006; no cause, no eligibility
def test_wiped_flag(...)
```

Add the crawl case: seed a dead PC with cause `CAUSE-SECRET-LIMA-7`, a poster, and a rescue story. Then assert that the cause, `rescue_eligibility` and `rescue_json` appear in no player endpoint.

- [ ] **Step 2: Run them.** Run `python -m pytest tests/carousel/test_fate_api.py tests/test_visibility.py -v`. The new tests should fail.
- [ ] **Step 3: Implement the API and the views.** Build the frontend with `cd frontend && npm run build`. It passes when there are no type errors.
- [ ] **Step 4: Check it in the preview.**
  1. Start `carousel-preview` (8799) and kill the PC in a seeded story via the API.
  2. Check that DeathwatchBar shows, then conclude with everyone dead.
  3. Check that the wiped screen shows and a new group starts in the car.
  4. Travel to the Diner and check that the board lists the old group.
- [ ] **Step 5: Run tests.** Run `python -m pytest tests -q`. It passes when everything is green.
- [ ] **Step 6: Commit.** `"Players see Deathwatch, the missing-poster board and the new-group screen; causes and rescue stay GM-only"`.

---

## Left out on purpose

- **Not built:**
  - Alternate-character handoff (Bible §11.4) and the `locked` rescue value.
  - The Damsel's `spawn_rescue_packet`.
  - The "Have You Seen Them?" style of rescue, which starts a story without an Omen.
  - Moving a dropped item to the body's zone (SMR-12 is covered only as far as recording the items in the death snapshot).
  - Enforcing the Deathwatch firewall in memories; the briefing line asks the GM instead.
- **Left to the existing ops:** injuries and conditions such as Hobbled stay `status_effects`; the axes don't replace them.
- **Later phases:** per-character positions inside a venue (Phase 7), and residents reacting to posters (Phase 7 colour).
