# Carousel Plan 5 — New-Game Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new campaign opens in the car with three GM-made friends and a lure, then moves through Silas dealing each character their Archetype and two starter tickets (picked from what the conversation showed), the opening Omen, the veterans' escort across town, and the lure's payoff at Dyer's Lodge.

**Architecture:** The opening is a forward-only stage on the campaign row (`car` → `silas` → `opening_omen` → `escort` → `done`), moved only by a validated `advance_opening` op whose preconditions the engine checks. Each stage's work goes through ops: `set_lure`, `drop_friend`, `silas_assigns`, `introduce_resident` and `pay_off_lure`. Friends are made with the existing `npc` op, the opening story uses Plan 4's `begin_story` unchanged, and the escort walks with Plan 3's `travel`. A server-started turn of kind `opening` narrates the car scene. The briefing gets an OPENING section listing the stage, its job and what still blocks the next stage. The player sees a five-step opening bar.

**Tech Stack:** Python 3.10, FastAPI, SQLite, pydantic v2, pytest; React/TS (one bar, one form change).

**Spec:** [docs/2026-10-30-carousel-game-design.md](../../2026-10-30-carousel-game-design.md) §9 (the flow), §10 (the Lodge, residents). Rules: `E:\Carousel\system\Carousel_System_Bible_v0.4.1.md` §4.1 steps 5–7 (11-point arrays, exactly two starter grants, persisted evidence). **Design §9.4 replaces §4.1's scoring rubric:** Silas (the GM) proposes each Archetype with its reason and quoted evidence; the engine validates. NPC package readme: the player's group arrives *instead of* Riley's; residents' `gm_only` never reaches the player. Builds on Plans 1–4 (branch `carousel`).

## Global Constraints

- **Stages, exactly and in order:** `car`, `silas`, `opening_omen`, `escort`, `done`. Forward one step at a time only. `NULL` means the campaign has no opening (seeded and legacy campaigns): every opening guard is off.
- **New campaigns start at the Eastern arrival parking lot `CAR-082`** ("the vast lot of long-abandoned cars"), knowing only that place. The Lodge (`CAR-047`) becomes known when the escort starts.
- **Three tickets per character from Silas:** the Archetype (one of the 13 `base.*` roles with a start profile, via the existing initialize logic), its `starting_signature` ticket (`tickets.relationship='starting_signature' AND source_role_id=<role>`), and exactly one `contextual_shared_starter` ticket. Once per character; never re-dealt.
- **Evidence is quoted, not invented:** each `silas_assigns` carries 1–5 quotes (8–300 chars). Each must appear, after normalising case, whitespace and curly quotes, in a player-visible `player` or `gm` message of this campaign. The quotes and reasons are stored and player-visible.
- **The lure's truth is GM-only** (`gm_secrets kind='lure'`); its belief is player-visible. A resident lure names a real `CAR-NPC-…` template, protected on re-import.
- **Before `opening_omen`** (stages `car` and `silas`), the group cannot travel, begin a story, or be initialised outside `silas_assigns`.
- No dice. Death in the opening story is Phase 6; camp-only save points are Phase 6 (this plan writes one save point, at `done`). Bulk import of all 70 residents and their schedules is Phase 7; this plan adds the one-at-a-time `introduce_resident` that Phase 7 reuses.
- `E:\Carousel` and `E:\dccrpg` are read-only. Port 8797.

## Review Focus

1. **The GM skips ahead** (to Silas with two friends, to the opening Omen with a friend unassigned, to `done` away from the Lodge) → refused, with the blocker named. (Task 4: `test_advance_refusals_name_blockers`)
2. **The lure's truth or an introduced resident's `gm_only` leaks** through the campaign payload, dossier, People, events or messages. (Task 7: the visibility crawl plants `LURE-TRUTH-KILO-11` and introduces the mini resident whose `gm_only` holds `MINI-NPC-SECRET`)
3. **A Silas assignment cites a quote nobody said, or deals an off-pool ticket** → refused, and nothing is written (no stats, no tickets). (Task 3: `test_silas_refusals_write_nothing`)
4. **A refresh or double-click on the new campaign starts the opening turn twice** → the second call returns the first turn. (Task 5: `test_opening_start_is_idempotent`)
5. **Save restore during the opening** → the stage, lure, friends, tickets and evidence roll back together. (Task 1: `test_restore_rolls_back_opening`)

---

## File structure

```
backend/app/
  carousel/opening.py       # STAGES, ARRIVAL_LOT, stage(), blockers(), friends(), quote_found()
  rules/opening_ops.py      # set_lure, drop_friend, silas_assigns, introduce_resident, pay_off_lure, advance_opening
  rules/__init__.py         # register opening_ops
  rules/carousel_ops.py     # initialize guard; _initialize() helper reused by silas_assigns
  rules/travel_ops.py, rules/story_ops.py   # opening guards
  db.py, carousel/refs.py, campaigns.py
  gm/router.py, gm/orchestrator.py          # 'opening' turn kind; silas stage → mechanics
  memory/context.py, gm/prompts.py          # OPENING section; opening contract
  opening_api.py            # POST /api/campaigns/{cid}/opening/start
  memory/views.py           # campaign_summary gains `opening`
frontend/src/views/OpeningBar.tsx, Story.tsx, Campaigns.tsx, api.ts
backend/tests/carousel/test_opening*.py, mini_data.py
```

---

### Task 1: Opening state and the new start

**Files:**
- Create: `backend/app/carousel/opening.py`
- Modify: `backend/app/db.py` (COLUMN_MIGRATIONS), `backend/app/carousel/refs.py`, `backend/app/campaigns.py`, `backend/tests/carousel/mini_data.py`
- Test: `backend/tests/carousel/test_opening.py`, `backend/tests/test_campaigns.py`

**Interfaces:**
- Columns (all nullable, via `COLUMN_MIGRATIONS`):
  - `campaigns.opening_stage TEXT`, `campaigns.lure_kind TEXT` (`person` | `event` | `other`), `campaigns.lure_belief TEXT`, `campaigns.lure_resident TEXT`, `campaigns.lure_paid_off TEXT` (the payoff text).
  - `characters.role_evidence_json TEXT`: `{"quotes": [...], "contextual_reason": str}`.
  - These ride in the existing `campaigns` and `characters` snapshots. No new tables, so `SNAPSHOT_TABLES` and `delete()` are unchanged.
- `refs.REFERENCE_COLUMNS["lure_resident"] = ("campaigns", "lure_resident")`, checked against `npc_templates` like `npc`.
- `opening.py`:
  - `STAGES = ("car", "silas", "opening_omen", "escort", "done")`
  - `ARRIVAL_LOT = "CAR-082"`
  - `stage(conn, cid) -> str | None`
  - `friends(conn, cid) -> list[dict]`: active `in_party` NPCs with no `npc_profiles.ref_id`, i.e. GM-made, not residents.
  - `quote_found(conn, cid, quote) -> bool`
  - `blockers(conn, cid) -> list[str]` (Task 4 fills it).
- `campaigns.create(conn, *, name, character, location=None, premise=None, opening=True)`:
  - `opening=True`: `opening_stage='car'`, the group at node `CAR-082`, known places `{CAR-082}` only, and `location` is ignored.
  - `opening=False`: today's behaviour (named place or the Lodge).
  - The premise is still stored as a fact; Task 5 passes it to the opening turn.
  - The `POST /api/campaigns` body keeps its fields; the endpoint always passes `opening=True`.
- Mini data: add place `CAR-082` "Eastern arrival parking lot" with a graph node and an edge to `CAR-002`, so the escort can route to the Lodge. Add `CAR-NPC-003` "Valerie Choi" as a second resident with a `gm_only` holding `MINI-NPC-SECRET-2`.

- [ ] **Step 1: Failing tests:**
  - `test_new_campaign_opens_in_the_car`: create → stage `car`, `node_id == "CAR-082"`, known places == `{"CAR-082"}`.
  - `create(..., opening=False, location="Town Square")` places the group as before. Update the existing `test_campaigns.py` placement tests to pass `opening=False`.
  - `test_restore_rolls_back_opening`: save, set the stage to `silas` and `lure_belief` by SQL, restore → back to `car` with no belief.
  - A re-import that drops the lure's resident is refused, following the Plan 4 story-omen test.
- [ ] **Steps 2–4:** RED → implement → GREEN (`run-tests.cmd`).
- [ ] **Step 5: Commit** `"New campaigns open in the car at the arrival lot; opening stage on the campaign"`

### Task 2: Lure, friends and residents

**Files:**
- Create: `backend/app/rules/opening_ops.py`
- Modify: `backend/app/rules/__init__.py`
- Test: `backend/tests/carousel/test_opening_ops.py`

**Interfaces:**
- `set_lure {kind: person|event|other, belief (1–400), truth (1–1200), resident?: "CAR-NPC-…"}`
  - Stage must be `car`, and the lure is set once ("the lure is already set").
  - `resident` is required when `kind='person'` and must exist in `ref.npc_templates`. A person the GM invents as a new resident is Phase 7.
  - Effects: the campaign's lure columns; `gm_secrets` (`subject='lure'`, `kind='lure'`, `content=truth`, `source='opening'`); a player-visible `lore_facts` row (`subject='Why we came'`, `fact=belief`, `certainty='rumor'`).
  - Event `opening_lure`, player-visible, carrying the belief only.
- `drop_friend {character, reason}`: stage `car`; the character must be in `friends()`. Sets `in_party=0`, `status='dormant'`, `roster_category='cut_from_opening'`. Event `opening_friend_dropped`. This is the player's veto or adjustment; adjusting a kept friend uses the existing `npc` op.
- `introduce_resident {resident: "CAR-NPC-…", in_party: bool = False, location?: str}`:
  - Refuses an unknown template or one already in the campaign (`npc_profiles.ref_id`).
  - Creates the character the way `_apply_npc` does: name, appearance → `bio`, `camp_role` → narrative `current_role`, category `allied_npcs` or `core_party` when `in_party`.
  - Its `npc_profiles` row gets `personality` and `ref_id`.
  - The template's `gm_json` (its `gm_only`, as JSON text) goes to `gm_secrets` (`kind='companion_sheet'`, `subject=<slug>`, `source='resident template'`).
  - Stats stay empty (Phase 7 decides residents' sheets).
  - Event `npc_met`, player-visible, with the name and camp role only.
- `pay_off_lure {how (1–600)}`:
  - Stage must be `escort`, and the group must be at node `CAR-047`.
  - For a person lure, that resident must already be introduced.
  - Sets `lure_paid_off`; event `opening_lure_paid_off`, player-visible. The truth stays GM-only; the GM reveals any of it with the existing secret-reveal path.
- Register the ops in `rules/__init__.py` as `story_ops` are. The GM's `apply_changes` accepts any registered op, so the GM tool schema needs no change.

- [ ] **Step 1: Failing tests** (on a fresh `create()` campaign):
  - The lure is set once.
  - A person lure without a resident, or with `CAR-NPC-999`, is refused.
  - The truth is in `gm_secrets`, never in `events`.
  - Dropping a friend removes them from `friends()`.
  - Introducing `CAR-NPC-003` twice is refused; its companion sheet holds `MINI-NPC-SECRET-2`.
  - `pay_off_lure` is refused at the lot, and also at the Lodge while the stage is still `car`.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"Opening ops: the lure, cutting a friend, introducing a resident, the lure's payoff"`

### Task 3: Silas deals the tickets

**Files:**
- Modify: `backend/app/rules/opening_ops.py`, `backend/app/rules/carousel_ops.py`, `backend/app/rules/travel_ops.py`, `backend/app/rules/story_ops.py`
- Test: `backend/tests/carousel/test_silas.py`

**Interfaces:**
- Refactor `carousel_ops._apply_initialize` into `_initialize(ctx, char, role_id, reason) -> dict` plus the op wrapper, and `_apply_grant` into `_grant(ctx, char, ticket_ref, source, hidden=False)`. Behaviour and events are unchanged.
- `silas_assigns {character, role, reason (1–400), evidence: [quote] (1–5), contextual_ticket, contextual_reason (1–300)}`. Stage must be `silas`. Validated in order, all before any write:
  1. The character is the PC or one of `friends()`, with no `role_id` yet.
  2. `role` is a `base.*` role with a start profile.
  3. Each quote passes `quote_found`. Otherwise: "Silas can only cite what happened: <quote> was never said".
  4. `contextual_ticket` has `relationship='contextual_shared_starter'`.
  5. The signature ticket for the role exists.
- Effects:
  - `_initialize`, then `_grant` the signature ticket (`source='Silas'`) and the contextual ticket (`source='Silas'`).
  - `role_evidence_json`.
  - One extra event `silas_deals`, player-visible: "Silas deals <name> three tickets: <Role> (<reason>), <signature name>, <contextual name>".
- Guards, all of which apply only while `opening.stage()` is `car` or `silas`:
  - `initialize_character`: refused with "during the opening, Silas assigns Archetypes (silas_assigns)".
  - `travel`: refused with "you haven't left the car park yet".
  - `begin_story`: refused with "the opening Omen comes after Silas".

- [ ] **Step 1: Failing tests:**
  - Insert player and GM `messages` rows containing "I'll check the map first".
  - `silas_assigns` Mara → `base.final-girl` with that quote and `hb.ticket.starter.shared.quick-thinker` gives the stats (2, 2, 3, 2, 2), 2 tickets and the evidence stored.
  - `test_silas_refusals_write_nothing` covers each, with characters, ticket and event counts unchanged:
    - an unsaid quote;
    - the signature ticket offered as contextual;
    - `base.unknown`;
    - a second deal to Mara;
    - a resident;
    - stage `car`.
  - The three guards refuse at `car` and pass once the stage is `opening_omen`.
- [ ] **Steps 2–4:** RED → implement → GREEN. The Plan 2 tests for `initialize_character` must still pass, since seeded campaigns have stage `NULL`.
- [ ] **Step 5: Commit** `"Silas assigns Archetypes from quoted evidence and deals the two starter tickets"`

### Task 4: Moving through the opening

**Files:**
- Modify: `backend/app/rules/opening_ops.py`, `backend/app/carousel/opening.py`
- Test: `backend/tests/carousel/test_opening_ops.py`

**Interfaces:**
- `blockers(conn, cid) -> list[str]`: what stops the *next* stage, in plain words, one line per problem:
  - `car`: the lure is not set; the friend count is not exactly 3 ("2 friends; the car holds 3").
  - `silas`: each PC or friend without a `role_id` ("Jonah has no Archetype yet").
  - `opening_omen`: a story is still running. If no story has started since the opening began, the move needs a `reason` (the player refused every Omen).
  - `escort`: the group isn't at `CAR-047`; the lure isn't paid off; no resident (`ref_id`) is in the party.
  - `done`: nothing comes after it.
- `advance_opening {to, reason?}`:
  - `to` must be the next stage, and `blockers()` must be empty. Otherwise: "can't move to <to>: <blockers joined>".
  - Effects by stage reached:
    - `escort`: insert known place `CAR-047` (`how='escort'`).
    - `done`: `create_save_point(conn, cid, "Arrived at Dyer's Lodge", kind="manual", turn_id=ctx.turn_id)`.
  - Event `opening_stage`, player-visible, with the stage's display label:
    - `silas` → "The drive in"
    - `opening_omen` → "Silas the Showman"
    - `escort` → "The first Omen"
    - `done` → "Dyer's Lodge"
  - A refused skip reason is GM-only (event `opening_skip_reason`, `visibility='gm'`).

- [ ] **Step 1: Failing tests:**
  - `test_full_opening_walkthrough` runs the whole flow with ops:
    - lure;
    - 3 friends (`npc` with `in_party`);
    - advance to `silas`;
    - 4× `silas_assigns`;
    - advance to `opening_omen`;
    - sight, begin and conclude OM-001 on a typed turn. As in Plan 4, insert a `turns` row with `kind NULL` and apply `begin_story` through `apply_change` with its `turn_id`. Conclude with `survive_without_victory`, which is allowed from any phase.
    - advance to `escort` (Lodge now known);
    - `introduce_resident` CAR-NPC-003 with `in_party`;
    - `travel` to CAR-047. The roaming OM-002 may stop the walk at CAR-002; if the result says interrupted, travel again.
    - `pay_off_lure`;
    - advance to `done`, which leaves a save point.
  - `test_advance_refusals_name_blockers`, each with the expected blocker text in the error:
    - skipping `car` → `opening_omen`;
    - `car` → `silas` with 2 friends;
    - `silas` → `opening_omen` with Jonah unassigned;
    - `escort` → `done` at the lot.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"advance_opening: forward-only stages with engine-checked blockers"`

### Task 5: The opening turn and its routing

**Files:**
- Create: `backend/app/opening_api.py`
- Modify: `backend/app/main.py` (register), `backend/app/gm/router.py`, `backend/app/gm/orchestrator.py`
- Test: `backend/tests/carousel/test_opening_api.py`, `backend/tests/test_router.py`

**Interfaces:**
- `POST /api/campaigns/{cid}/opening/start {idempotency_key}`:
  - Refuses with 409 unless the stage is `car` and the campaign has no completed turn.
  - Submits a server turn through `state.turns.submit(cid, text, key, kind="opening")`, where `text` is `"(The game begins in the car.)"` plus `" Player's wishes: " + premise` when the campaign has a premise fact.
  - Returns `{turn}`.
  - The same key returns the same turn. That is the existing idempotency path, and the test pins it.
- `router.ARRIVAL_ROUTES["opening"] = ("action", "the opening sets the lure and the cast")`. Rename the map to `SERVER_TURN_ROUTES`, since it is no longer arrival-only.
- `orchestrator`: where it calls `route(...)`, pass `story_active(conn, cid) or opening.stage(conn, cid) == "silas"`, so Silas's mechanics go to Codex. The car conversation (stage `car`, player-typed turns) routes normally and may use Gemini.

- [ ] **Step 1: Failing tests:**
  - `test_opening_start_is_idempotent`: start twice with the same key → one turn, `kind='opening'`.
  - A different key once a turn exists → 409.
  - A seeded campaign (stage `NULL`) → 409.
  - Router: `classify_turn("hi", False, turn_kind="opening")[0] == "action"`.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"Opening turn: the server starts the car scene; Silas's turns route to Codex"`

### Task 6: The GM's briefing and contract

**Files:**
- Modify: `backend/app/memory/context.py`, `backend/app/gm/prompts.py`
- Test: `backend/tests/carousel/test_opening_briefing.py`

**Interfaces:**
- An `OPENING` section (a `"state"` priority like STORY), present only while the stage is not `NULL` and not `done`:
  - `Stage: <stage> — <job>` with these exact jobs:
    - `car`: "Choose the lure (set_lure), create the three friends to fit it (npc, in_party), and open on the drive with everyone talking. Let the player adjust or cut a friend (drop_friend) before the conversation gets going. Give each person natural moments to show who they are."
    - `silas`: "Signal and radio die, the road ends in the abandoned lot, everyone walks. Silas deals each character three tickets with silas_assigns, quoting what they said or did in the car."
    - `opening_omen`: "The group is pulled toward a story almost at once: sight an Omen near them. Only the player's own Choice starts it."
    - `escort`: "Veterans (introduce_resident, in_party) walk the group across town to Dyer's Lodge, dodging Omens. At the Lodge, pay off the lure (pay_off_lure)."
  - `Next stage blocked by:` followed by `blockers()`, or `ready: advance_opening to <next>`.
  - The lure belief, and the friends with their Archetype or "unassigned".
  - At `car` and `silas` only, a compact Silas table:
    - the 13 starting roles, one line each: `id, name: play_identity`, plus the five stats;
    - the 10 contextual starter tickets, one line each: `id, name: text`.
    - Read from `ref.roles`, `ref.start_profiles` and `ref.tickets`, so the briefing stays correct after a re-import.
- The lure truth already reaches GM-ONLY through the existing `gm_secrets` listing. The test only pins that it does.
- `prompts.py`, one paragraph: "New game: follow the OPENING section. Never assign an Archetype by menu or ask the player to pick one; Silas judges from conduct, not demographics, and every reason quotes the conversation. The friends are people with their own wants, not prompts. A quiet player is not penalised: if the evidence is thin, give one more moment in the car before Silas."

- [ ] **Step 1: Failing tests:**
  - At `car`, the briefing contains the exact `car` job, `the car holds 3`, `base.final-girl` and `hb.ticket.starter.shared.quick-thinker`.
  - At `opening_omen`, there is no Silas table.
  - At `done` or `NULL`, there is no OPENING section.
  - The lure truth appears in the GM-ONLY part only.
- [ ] **Steps 2–4:** RED → implement → GREEN.
- [ ] **Step 5: Commit** `"Briefing: OPENING section with stage job, blockers and Silas's options"`

### Task 7: What the player sees

**Files:**
- Create: `frontend/src/views/OpeningBar.tsx`
- Modify: `backend/app/memory/views.py` (`campaign_summary`), `frontend/src/views/Story.tsx`, `frontend/src/views/Campaigns.tsx`, `frontend/src/api.ts`, `frontend/src/storybar.css`, `backend/tests/test_visibility.py`
- Test: `backend/tests/carousel/test_opening_api.py`, `backend/tests/test_visibility.py`

**Interfaces:**
- `campaign_summary` gains `opening: {stage, belief, paid_off} | null`. It is `null` when the stage is `NULL`. `paid_off` is the payoff text. It never includes the truth or the resident ID.
- `OpeningBar({ campaign })`:
  - Five steps labelled "The drive in", "Silas the Showman", "The first Omen", "The escort", "Dyer's Lodge". The current step is highlighted and finished steps are ticked.
  - Below the steps, "Why we came: <belief>".
  - Rendered above the StoryBar in `Story.tsx` while `opening` is set and the stage is not `done`.
- `Campaigns.tsx` new-campaign form:
  - Remove "Starting location".
  - Relabel the premise field "What kind of lure or opening would you like? (optional)".
  - After create, navigate to the game and `POST /opening/start` with a fresh idempotency key stored in `sessionStorage` under `opening-<cid>`, wrapped in try/catch, so a reload reuses the key.
- Visibility crawl `_plant`:
  - On a second campaign made with `create()`: `set_lure` (person, `CAR-NPC-003`, truth `LURE-TRUTH-KILO-11`) and `introduce_resident CAR-NPC-003`.
  - Add `LURE-TRUTH-KILO-11` and `MINI-NPC-SECRET-2` to `SECRETS`.
  - Crawl that campaign's URLs too.

- [ ] **Step 1: Failing tests:**
  - `/api/campaigns/{cid}` shows `opening.stage == "car"` and the belief.
  - The crawl finds neither new secret.
- [ ] **Steps 2–4:** RED → implement → GREEN. Then `build-frontend.cmd`, and `test_frontend_bundle_contains_no_secrets` passes.
- [ ] **Step 5: Manual check** with the mock GM on port 8799 (`.claude/launch.json` `carousel-preview`):
  - Create a campaign: the bar shows "The drive in", and one opening turn is narrated.
  - Reload: no second turn.
- [ ] **Step 6: Commit** `"Players see the opening: five-step bar, the lure they believe, a new-game form that starts in the car"`
