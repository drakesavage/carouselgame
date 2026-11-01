# Carousel Plan 4 — Omens and Stories Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run Carousel's stories. An Omen is sighted and examined; the player's Choice action starts its story; the engine seals the story's truth and endings, tracks the Plot Cycle, camera and cast, lets eligible tickets revise the screenplay through logged revisions, concludes the story with one of four endings, and awards stars, stat points, marks and ticket recharges.

**Architecture:** One `stories` row per story instance, with GM-only `story_packets` (sealed truth plus hash, screenplay revisions, ending profiles) taken from the beat dossier at Choice time. Every change goes through validated ops in `rules/story_ops.py`: phase moves, pressure, camera, casting, revisions, conclusion and rewards. The GM improvises everything between those fixed points. The briefing gets a STORY section (phase, obligations, cast, current screenplay and endings), and the sealed truth goes in GM-ONLY. Players see only the title, premise, Plot Cycle position, camera, cast and outcome.

**Tech Stack:** Python 3.10, FastAPI, SQLite, pydantic v2, pytest; React/TS (small Story-view additions).

**Spec:** [docs/2026-10-30-carousel-game-design.md](../../2026-10-30-carousel-game-design.md) §7 and §7.1, plus §10 for story history. Rules: `E:\Carousel\system\Carousel_System_Bible_v0.4.1.md`:
- §2.2: Plot Cycle.
- §5–5.3: packets, phase advancement, clue fairness, story alteration.
- §8: casting and camera.
- §9: spoilers.
- §10: rewards.
- §4.4: advanced marks and Aspect unlock.

Data: Omen engine policy (`omen/Carousel_Omen_Engine_Data_v0.2.json` `engine_policy`), beat dossiers (`omen2story/…v0_3.json` `dossiers[*]`: `commitment`, `phase_plan`, `outcomes`, `house_story`, `continuity`), Role Bible `dynamic_story_mutation_contract`. Builds on Plans 1–3 (branch `carousel`; Plan 3 adds `omen_states`, `turns.kind`, `travel`).

## Global Constraints

- **Phases, exactly and in order:** `Omen`, `Choice`, `Party`, `First Blood`, `Rebirth`, `Second Blood`, `Finale`, `The End`. A phase moves forward only; skipping forward is allowed with a recorded trigger; there are no backward moves. Pressure is an integer 0–6.
- **Endings, exactly:** `full_success`, `costly_success`, `survive_without_victory`, `defeat`. The dossier's `survival_only` maps to `survive_without_victory` and `failure` maps to `defeat`.
- **A story starts only on its Omen's committed Choice action,** on a turn the player typed (`turns.kind IS NULL`). Never from an arrival, an inspection or a hub record (`record_type` must be `Playable Omen`).
- **Mutual exclusion:** at most one active story per campaign (the design's "a group can't trigger a new story while one is active"), and at most one active instance per `story_lock`.
- **The sealed truth is copied and hashed (sha256 of canonical JSON) at Choice and never changes.** Screenplay revisions append; they never edit the baseline or history.
- **Revisions need an eligible ticket.** A ticket may revise the screenplay only if one of these holds:
  - its definition has `supports_story_mutation: true`;
  - its `tag` is `story_alteration`, `story_structure` or `win_condition`;
  - its `scope` is `preproduction_or_live_story_mutation`;
  - it's a `trope:<id>` with an owner-**approved** ruling (`review='approved'`) tied to it.
- **Allowed operation families:** `insert_future_scene`, `remove_or_delay_future_beat`, `recast_npc`, `introduce_plausible_support`, `shift_pressure`, `reveal_authorized_fact`, `create_obstacle`, `rewrite_conditional_objective`. `spawn_rescue_packet` is Phase 6, and `extraordinary_timeline_branch` is never available from a generic grant.
- **Rewards:** Novelty, Performance and Difficulty stars, each 0–2. Every 5 stars gives 1 pending stat point. Unspent remainder stars decay by 1 after 3 straight concluded stories with no stars gained. Advanced marks: 10 per track. Aspect selection needs the matching base Archetype plus base Plot Armor ≥ 21 plus a recorded feat.
- No dice. Death, fate and restoration are Phase 6: Plan 4 records the ending and cast, nothing about who lives.
- `E:\Carousel` and `E:\dccrpg` are read-only. Port 8797.

## Review Focus

1. **The sealed truth, screenplay or endings leak** through `/story`, `/history`, cards, events or a revision's player summary. (Task 8: the visibility crawl plants `MINI-SEALED-TRUTH`, a revision's private detail and a hidden ending predicate)
2. **The GM tries to start a story from an arrival turn, a hub, an Omen nobody has seen, or while another story runs** → refused, nothing written. (Task 3: `test_begin_story_refusals`)
3. **A revision that's stale (based on an old version), uses an ineligible ticket, or has no uses left** → refused; no use spent, no revision row. (Task 5: `test_revision_refusals_spend_nothing`)
4. **Save restore mid-story** → story, cast, packet, revisions, stars and marks all roll back together. (Task 1: `test_restore_rolls_back_story_state`)
5. **A re-import drops an Omen a story (active or historical) points at** → refused. (Task 1: `test_reimport_protects_story_omens`)

---

## File structure

```
backend/app/
  rules/story_ops.py        # sight_omen, examine_omen, begin_story, advance_phase, pressure, camera, cast_character,
                            #   revise_screenplay, conclude_story, spend_stat_point, award_mark, select_aspect, confirm_spoiler
  carousel/stories.py       # PHASES, ENDINGS, packet building + hashing, eligibility, novelty, active_story()
  db.py, memory/snapshots.py, carousel/refs.py
  gm/orchestrator.py        # story_active() reads stories
  memory/context.py         # STORY section; sealed truth in GM-ONLY; Omens here
  gm/prompts.py             # story contract
  story_api.py              # GET /story, /stories; POST /characters/{id}/stat-point
frontend/src/views/Story.tsx, StoryBar.tsx, Dossier.tsx, api.ts
backend/tests/carousel/test_stories.py, test_story_ops.py, test_revisions.py, test_rewards.py, test_story_briefing.py, test_story_api.py
```

---

### Task 1: Story schema

**Files:**
- Create: `backend/app/carousel/stories.py`
- Modify: `backend/app/db.py`, `backend/app/memory/snapshots.py`, `backend/app/carousel/refs.py`, `backend/app/gm/orchestrator.py` (`story_active`), `backend/tests/carousel/mini_data.py`
- Test: `backend/tests/carousel/test_stories.py`

**Interfaces:**
- **Schema** (plus `COLUMN_MIGRATIONS` rows):
  - `stories(id, campaign_id, omen_id, story_lock, title, status CHECK IN ('active','concluded'), phase, pressure INTEGER DEFAULT 0, camera CHECK IN ('on','off') DEFAULT 'off', version INTEGER DEFAULT 0, choice_action, started_minutes, ended_minutes, ending, ending_reason, novelty INTEGER, started_turn_id, ended_turn_id)`.
  - Two partial unique indexes: `ON stories(campaign_id) WHERE status='active'` and `ON stories(campaign_id, story_lock) WHERE status='active'`.
  - `story_packets(story_id PRIMARY KEY, sealed_json, sealed_sha256, screenplay_json, endings_json)`: GM-only. `screenplay_json` is the current revision's `{version, phase_plan, notes}`; `endings_json` is the current ending profiles.
  - `story_cast(story_id, character_id, casting CHECK IN ('lead','support','witness','sacrifice','outsider'), joined_phase, screen_moments INTEGER DEFAULT 0, PRIMARY KEY(story_id, character_id))`.
  - `story_revisions(id, story_id, version, character_id, ticket_ref, scope, operations_json, before_json, after_json, public_summary, reason, turn_id)`: GM-only except `public_summary`.
  - `story_spoilers(campaign_id, story_lock, element, source, PRIMARY KEY(campaign_id, story_lock, element))`.
  - `characters`: add `stars INTEGER DEFAULT 0`, `pending_stat_points INTEGER DEFAULT 0`, `starless_streak INTEGER DEFAULT 0`.
  - `advanced_marks(character_id, track_role_id, marks INTEGER, unlocked INTEGER DEFAULT 0, PRIMARY KEY(character_id, track_role_id))`.
  - `mark_awards(story_id, character_id, track_role_id, feat, PRIMARY KEY(story_id, character_id, track_role_id))`: one mark per track per story.
- **Snapshots:** every new table goes into `SNAPSHOT_TABLES` and `_DELETE_ORDER`.
- **`refs`:** add `"story_omen": ("stories", "omen_id")`, checked like `omen`.
- **`stories.py` produces:**
  - `PHASES: tuple[str, ...]`, `ENDINGS: tuple[str, ...]`, `DOSSIER_ENDING = {"full_success": "full_success", "costly_success": "costly_success", "survival_only": "survive_without_victory", "failure": "defeat"}`
  - `active_story(conn, cid) -> dict | None`
  - `seal(dossier: dict) -> tuple[str, str]`: `(sealed_json, sha256)`. Canonical JSON uses `sort_keys=True, ensure_ascii=False`.
  - `endings_from(dossier) -> dict[str, dict]`: the four profiles under the design's names.
  - `ticket_can_revise(conn, cid, ticket_ref) -> bool`: the eligibility rule from Global Constraints.
  - `novelty(conn, cid, story_lock) -> int`: 2 on the first concluded run of the lock in this campaign, 1 on the second, 0 after that. Minus 1 per `story_spoilers` row for the lock, never below 0.
- `orchestrator.story_active(conn, cid)` returns `active_story(...) is not None`.
- **Mini data:** give OM-001's beat dossier a full `commitment` (`trigger_operator`/`trigger_target` matching the registry), 8 `phase_plan` entries, 4 `outcomes` (`full_success`, `costly_success`, `survival_only`, `failure`) and `house_story.secret_truth = "MINI-SEALED-TRUTH"`. Add the homebrew ticket `hb.ticket.aspect.soldier.commando` (`supports_story_mutation: true`, `uses: 1`, `scope: storyline`), an advanced role `advanced.adventurer` with its ticket, an aspect `aspect.final-girl.screamer` (already present), and a hub record `OM-009` (`Record Type: Hub / generator`) at CAR-002.

- [ ] **Step 1: Failing tests:** the schema exists. Two active stories in one campaign violate the unique index. `seal` is stable: the same dossier gives the same hash, and reordering the keys doesn't change it. `endings_from` maps the names. `novelty` gives 2, 1, 0, and a spoiler lowers it. `ticket_can_revise` is true for the commando ticket, false for the final-girl ticket, false for `trope:1` until an approved ruling on it exists, then true. `test_restore_rolls_back_story_state` inserts a story, cast, packet, a revision and stars after a save point, restores, and finds them all gone. `test_reimport_protects_story_omens` (a concluded story on `OM-404` blocks the import). `story_active` follows the table.
- [ ] **Steps 2–4:** RED → implement → GREEN (full suite).
- [ ] **Step 5: Commit** `"Story schema: stories, sealed packets, cast, revisions, rewards"`

### Task 2: Sighting and examining Omens

**Files:**
- Create: `backend/app/rules/story_ops.py` (registered in `rules/__init__.py` like `travel_ops`)
- Test: `backend/tests/carousel/test_story_ops.py`

**Interfaces:**
- `sight_omen {omen, how}`: makes any playable Omen visible when the fiction surfaces it. This covers area-placed, item and special Omens (Plan 3 handles only fixed and roaming ones on routes). It refuses non-`Playable Omen` records except hubs, which may be sighted but never started. It's idempotent: an Omen that's already visible gives "already seen". Event `omen_sighted`, player-visible, with the registry description.
- `examine_omen {omen}`: `visible` → `examined`. The result and event include the dossier's `player_visible_premise` (from `omen_beats.dossier_json`, never `sealed_json`). It refuses unseen Omens.

- [ ] **Step 1: Failing tests:** sighting OM-003 creates `omen_states(visible)` and a player event with its description. Sighting it twice is refused. Examining shows the premise, and the event has no `MINI-SEALED-TRUTH`. Examining an unseen Omen is refused.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"Sight and examine Omens"`

### Task 3: Beginning a story

**Files:**
- Modify: `backend/app/rules/story_ops.py`
- Test: `backend/tests/carousel/test_story_ops.py`

**Interfaces:**
- `begin_story {omen, action, cast: [{character, casting}], reason, endings?}`, validated in order (tests apply it through `apply_change` with a real `turn_id`, since the conftest `apply` helper passes none):
  1. Reference data is present; the record type is `Playable Omen`; the Omen is visible or examined in this campaign.
  2. The turn was typed by the player: `ctx.turn_id` is set and its `turns.kind IS NULL`. Otherwise: "a story starts only on the player's own Choice".
  3. `action` equals the Omen's `choice_trigger` (`<operator>:<target>`). If the Omen has none, any action is accepted and recorded with `reason`.
  4. There is no active story in the campaign, and none with this `story_lock`. Otherwise: "another story is running: <title>".
  5. The cast is non-empty and includes the player character; each casting is one of the 5 functions.
- Effects:
  - Insert the story with `phase='Party'`, `pressure=1`, `camera='on'` and `title` = the playable title.
  - Insert the packet: `seal(dossier)`, `screenplay_json = {version: 0, phase_plan, notes: []}`, `endings_json = endings_from(dossier)`.
  - Insert the cast rows. Routes a story opens (graph flags such as `akers_story`) are set by the GM with Plan 3's `set_flag` when the fiction opens them, not inferred from the lock.
  - If the dossier has no `outcomes`, `begin_story` requires an `endings` field (`{ending_name: {predicate}}` covering all four endings). It is sealed into `endings_json` exactly like dossier endings (design §7: the GM drafts them at Choice, sealed before play continues).
  - Write events `story_omen` → `story_choice` → `story_phase` (to Party), all player-visible, with titles and the action, and nothing from the packet.
  - Return `{summary, story_id, phase}`.

- [ ] **Step 1: Failing tests:** beginning OM-001 on a player turn (insert a `turns` row with `kind NULL`, then apply with that `turn_id`) creates the story, packet, hash and cast. `test_begin_story_refusals` covers each, and each leaves the row counts unchanged:
  - an arrival turn (`kind='arrival'`);
  - no turn at all;
  - the hub OM-009;
  - an unseen OM-003;
  - the wrong action;
  - a cast without the PC;
  - a second story while one is active.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"Begin a story on the player's Choice: sealed packet, cast, Plot Cycle"`

### Task 4: Plot Cycle, pressure, camera and casting

**Files:**
- Modify: `backend/app/rules/story_ops.py`
- Test: `backend/tests/carousel/test_story_ops.py`

**Interfaces:**
- `advance_phase {to, trigger}`: `to` must be a later phase than the current one and not `The End` ("conclude the story to end it"). Pressure is raised to at least the phase's floor: Party 1, First Blood 2, Rebirth 3, Second Blood 4, Finale 5. Event `story_phase`, player-visible: "Plot Cycle: <from> → <to>".
- `pressure {delta, reason}`: clamped to 0–6. The event is GM-only, because the players never see the number (System Bible §5.1).
- `camera {on, focus: [character], reason}`: sets `stories.camera` and adds 1 `screen_moments` to each focused cast member. The event is player-visible ("Camera on: Mara").
- `cast_character {character, casting}`: adds a cast member mid-story (a recruited resident) with `joined_phase` = the current phase, or changes an existing member's casting.
- All four refuse when no story is active.

- [ ] **Step 1: Failing tests:** advancing Party → First Blood → Finale (a skip, with a trigger) works and pressure ≥ 5. Moving backwards or to The End is refused. Pressure is clamped and its event is `gm`. The camera counts screen moments. A cast member added mid-story records its phase.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"Plot Cycle phases, pressure, camera and casting as engine ops"`

### Task 5: Screenplay revisions from tickets

**Files:**
- Modify: `backend/app/rules/story_ops.py`
- Test: `backend/tests/carousel/test_revisions.py`

**Interfaces:**
- `revise_screenplay` takes these fields:
  - `character`, `ticket`, `base_version`;
  - `scope`: one of `scene`, `arc`, `premise`, `ending_conditions`;
  - `operations: [{family, detail}]`, 1–5 items;
  - `costs: [str]`, `new_dangers: [str]`, `preserved_facts: [str]`;
  - `public_summary`, `reason`;
  - `new_endings?: {ending_name: {predicate}}`.
- Validation, in order:
  1. A story is active, and the character is in its cast.
  2. The character owns the ticket and has it equipped; `uses_left` is NULL or above 0.
  3. `ticket_can_revise`.
  4. Every `family` is in the allowed eight.
  5. `base_version == stories.version`. Otherwise: "the screenplay changed (now version N); propose against it". This is optimistic concurrency, per the Role Bible.
  6. Scope `ending_conditions` requires `new_endings`, non-empty `costs` and non-empty `new_dangers`. No automatic victory (hard invariant).
  7. `new_endings` keys must be among the four endings.
- Commit:
  - Insert `story_revisions` with `before_json`/`after_json` (the packet's screenplay and endings).
  - Append the operations to `screenplay_json.notes` and bump `version`.
  - Merge `new_endings` into `endings_json`.
  - Spend the ticket use through the same path as `use_trope` (event `trope_used`, effect `revises the screenplay: <public_summary>`).
  - Write event `screenplay_revised`. The player-visible version carries `public_summary` only; a second, GM-only event carries the operations and `reason`.
- A refusal spends no use and writes no row.

- [ ] **Step 1: Failing tests:** Jonah gets and equips the commando ticket, then revises with scope `arc` and family `insert_future_scene`. The version goes 0 → 1, there is one revision row, the use is spent, and the player event has only the public summary. `test_revision_refusals_spend_nothing` covers each, and `uses_left` and `story_revisions` are unchanged after each:
  - a stale `base_version`;
  - the final-girl ticket (not eligible);
  - `extraordinary_timeline_branch`;
  - `ending_conditions` without costs;
  - no uses left;
  - not in the cast.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"Ticket-driven screenplay revisions with versioned, logged commits"`

### Task 6: Concluding a story and rewards

**Files:**
- Modify: `backend/app/rules/story_ops.py`, `backend/app/rules/carousel_ops.py` (reuse `recharge`), `backend/app/carousel/sheet.py` (no change expected)
- Test: `backend/tests/carousel/test_rewards.py`

**Interfaces:**
- `conclude_story {ending, reason, awards: [{character, performance, difficulty}]}`:
  - **Allowed phases:** `survive_without_victory` and `defeat` from any phase. `full_success` and `costly_success` only from `Finale`.
  - **Story state:** set `status='concluded'`, `phase='The End'`, `ending`, `ending_reason`, `ended_minutes` and `novelty = novelty(...)` (computed before the row counts as concluded).
  - **Ticket and modifier cleanup:** `recharge(ctx, "story")`; delete `stat_modifiers` with `scope='story'` for the cast.
  - **Awards:** one award row per cast member with `kind='pc'`, each with `performance` and `difficulty` in 0–2. NPC cast earn nothing. A missing PC award is refused.
  - **Stars per PC** = novelty + performance + difficulty. Add them to `stars`; while `stars >= 5`, subtract 5 and add 1 to `pending_stat_points` (event `stat_point_earned`). If a PC gained 0 stars, increment `starless_streak`; at 3, if `stars > 0`, decrement `stars` by 1 and reset the streak (event `stars_decayed`). Any gain resets the streak.
  - **Events:** `story_phase` (→ The End) and `story_concluded`. The payload has `{story_id, ending, cast: [ids], stars: {id: n}}`; it's the hook Phase 6 subscribes to.
- `spend_stat_point {character, stat}`: needs `pending_stat_points > 0`; base stat +1 (base PA +1); event.
- `award_mark {character, track, feat}`: `track` is an `advanced` role. One per track per story (`mark_awards`). At 10 marks, set `unlocked=1` and write event `advanced_role_unlocked` once. Marks are never tickets, stars or currency.
- `select_aspect {character, aspect, feat}`: the aspect's `parent` must equal `characters.role_id`, base Plot Armor must be ≥ 21, and `aspect_id` must still be empty. Sets `aspect_id`. It does not grant a ticket.
- `confirm_spoiler {story_lock, element, source}`: records a confirmed spoiler (world state).

- [ ] **Step 1: Failing tests:**
  - concluding with `full_success` from Party is refused, and allowed from Finale;
  - first-run novelty is 2, so Mara with performance 2 and difficulty 1 gets 5 stars → 1 pending point and 0 stars;
  - the story-recharge ticket refills, and story modifiers are gone;
  - a replay gives novelty 1;
  - three starless stories decay 1 star;
  - `spend_stat_point` raises base PA by 1;
  - 10 `award_mark` calls across 10 stories unlock the track exactly once, and a second mark in the same story is refused;
  - `select_aspect` is refused below PA 21 and works at 21;
  - a missing PC award is refused.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"Conclude stories: endings, novelty, stars, stat points, marks, Aspects"`

### Task 7: The GM's story briefing

**Files:**
- Modify: `backend/app/memory/context.py`, `backend/app/gm/prompts.py`
- Test: `backend/tests/carousel/test_story_briefing.py`

**Interfaces:**
- **New section `STORY`,** only while a story is active:
  - title, phase, pressure, camera, version;
  - each cast member with casting and screen moments, so the GM can bring quiet characters on-screen;
  - the current phase's obligation and the next phase's obligation, from `screenplay_json.phase_plan`;
  - all revision notes;
  - the current ending profiles (the predicates);
  - the Omen's `choice_trigger` and `story_lock`.
- **The `GM-ONLY SECRETS` section gains `Sealed truth (story #id, sha256 <first 12>)`:** `secret_truth`, `actionable_counterplay`, `antagonist_behavior` and `clue_routes` from `sealed_json.house_story`. These are never cut by the budget; if the budget is too tight, other secrets are cut first and the omission is noted.
- **When no story is active, a section `OMENS HERE`** lists Omens with `omen_states` rows whose place is the party's current place. Each entry shows its description, stage and Choice action, so the GM knows what commits.
- **Prompt addition (exact):** `"Stories: never start one yourself. When the player's own action matches an Omen's Choice action, call apply_changes op begin_story with that action and the cast. Move the Plot Cycle with advance_phase only when a trigger is met, never on a timer, and never kill a character to meet a phase. The sealed truth never changes: a good deduction that fits it succeeds. Story-altering tickets revise the future only through revise_screenplay. End every story with conclude_story and honest Performance and Difficulty stars (0-2 each)."`

- [ ] **Step 1: Failing tests:**
  - with an active story the briefing has a `## STORY` section with phase, cast and the current obligation text;
  - `MINI-SEALED-TRUTH` appears only after `## GM-ONLY SECRETS`;
  - after a revision, its note appears;
  - with no story, at the Farm with OM-001 visible, `## OMENS HERE` lists OM-001 and its action;
  - the sealed truth survives `budgets={"secrets": 200}`;
  - the prompt contains the exact sentence.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"GM briefing: story state, sealed truth, Omens at hand"`

### Task 8: Player story view and visibility

**Files:**
- Create: `backend/app/story_api.py` (registered before the SPA catch-all), `frontend/src/views/StoryBar.tsx`
- Modify: `backend/app/memory/views.py` (the card gains `stars`, `pending_stat_points`), `backend/tests/test_visibility.py`, `frontend/src/api.ts`, `frontend/src/views/Story.tsx`, `frontend/src/views/Dossier.tsx`
- Test: `backend/tests/carousel/test_story_api.py`, `test_visibility.py`, `build-frontend.cmd`, browser check

**Interfaces:**
- **`GET /api/campaigns/{cid}/story`** → `{active: {id, title, premise, phase, phases: PHASES, camera, cast: [{id, name, casting, screen_moments}], revisions: [public_summary]} | null}`. `premise` = `player_visible_premise`. No packet field, pressure, version or ending predicate ever appears.
- **`GET /api/campaigns/{cid}/stories`** → story history: `[{id, title, ending, started_minutes, ended_minutes, novelty, cast: [names]}]`.
- **`POST /api/campaigns/{cid}/characters/{char_id}/stat-point {stat}`** under `write_lock`: applies `spend_stat_point`.
- **Frontend:**
  - `StoryBar`: an 8-step Plot Cycle strip with the current phase lit, plus a camera indicator ("ON SCREEN" / "OFF SCREEN"), above the Story view's transcript when a story is active.
  - The Chronicle's history list shows past stories with their endings.
  - The Dossier shows stars and a "Spend stat point" control when `pending_stat_points > 0`.
- **Visibility crawl:**
  - add `/story` and `/stories`;
  - plant a revision whose `operations_json` and `reason` contain `"REVISION-INDIA-9"` (the public summary doesn't);
  - plant an ending predicate `"ENDING-JULIET-10"`;
  - run with an active story; `MINI-SEALED-TRUTH` is already planted.

- [ ] **Step 1: Failing tests:**
  - the story API shape, with no forbidden keys (assert the key sets exactly);
  - the history after a conclusion;
  - stat-point spending: 409 while a turn runs; 422 with no points;
  - the crawl finds none of the planted strings.
- [ ] **Steps 2–4:** RED → implement backend → GREEN (full suite). Then build the frontend and do a browser check on a scratch campaign with a story begun through ops: the bar shows Party lit, the camera shows on, and there are no console errors.
- [ ] **Step 5: Commit** `"Players see the Plot Cycle, camera, cast and story history; story secrets stay sealed"`

---

## Done when

- A campaign can sight, examine and begin a story on the player's Choice. Its Plot Cycle, pressure, camera and cast move only through ops. Eligible tickets revise its screenplay as versioned, logged commits. It concludes with one of four endings and awards novelty-aware stars, stat points and marks.
- The GM's briefing carries the story state and the sealed truth (GM-only). Players see the Plot Cycle bar, camera, cast and history, and never the packet.
- Save restore and re-import protection cover every new table.
- Full suite green; the frontend builds; the browser check passes.

## Not in this plan

- Who lives and dies, the survivor-restores-everyone rule, missing posters and rescue packets: Phase 6 (it hooks `story_concluded`).
- Recruiting residents and their schedules: Phase 7 (`cast_character` is ready for it).
- The opening Omen of a new game: Phase 5 calls `sight_omen` and the same begin/conclude ops.
- One-of-three ticket reward offers in the UI: the GM grants rewards with `grant_ticket source=story_reward` for now.
- Difficulty credit scaled automatically for overlevelled characters: the GM judges it when awarding, per System Bible §10.
- The mystery-graph proposition states (unknown, suspected, supported, confirmed) as tracked data: the GM keeps them in play; tracking can follow if playtests need it.

## Left to the builder

- The wording of events and briefing lines beyond the strings the tests pin.
- The StoryBar's look (Phase 8 restyles it).
- Whether `stories.title` uses the playable or the canonical title when they differ (keep the other in the packet).
