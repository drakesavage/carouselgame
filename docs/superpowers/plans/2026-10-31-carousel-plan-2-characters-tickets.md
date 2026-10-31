# Carousel Plan 2 — Characters and Tickets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Crawler Console's character model with Carousel's: five stats and Plot Armor, starting Archetype profiles, trope tickets owned, equipped and used through validated engine ops, and a per-turn GM briefing that carries the full text of every ticket in play.

**Architecture:** Base stats live in explicit columns on `characters`. Temporary changes are `stat_modifiers` rows, and effective Plot Armor is computed, never stored. Owned tickets are `character_tickets` rows that point at a reference ID: a homebrew `hb.ticket.*` or a trope-bible `trope:<id>`. Their rule text is read at play time from `reference.db`, which is ATTACHed to every campaign connection as `ref`. The GM decides outcomes narratively. The engine keeps the books: grants, equips, uses, recharges, modifiers and rulings are ops that write immutable events.

**Tech Stack:** Python 3.10, FastAPI, SQLite (ATTACH), pydantic v2, pytest; React/TS (type-only follow-up).

**Spec:** [docs/2026-10-30-carousel-game-design.md](../../2026-10-30-carousel-game-design.md) §7.1 and §14 step 3; rules from `E:\Carousel\system\Carousel_System_Bible_v0.4.1.md` §2.3, §2.5, §4.1–4.3. Roadmap Phase 2: [2026-10-31-carousel-roadmap.md](2026-10-31-carousel-roadmap.md). Builds on Plan 1 (branch `carousel`, HEAD `cbaffd0`).

## Global Constraints

- No dice, ever. Ticket text that says "+2 to a check" is a relative edge the GM weighs narratively. The engine never rolls or compares numbers to decide an outcome.
- The stat names, exactly: `Mettle`, `Moxie`, `Hustle`, `Savvy`, `Grit`. Columns are lowercase.
- `plot_armor_base = mettle + moxie + hustle + savvy + grit`. Starting budget 11, base PA 11, cap 5 per stat at creation (Role Bible v0.4.2 `v042_audit_resolution`). Ruleset string `"SB-0.4.1/role-0.4.2"`.
- Effective PA = sum of current stats (base + additive stat modifiers), then Plot Armor multipliers applied in modifier-id order, then floored to an int. A drawback never deletes base points (System Bible §4.2).
- Equip limit **4** tickets per character (`EQUIP_LIMIT = 4`, System Bible §4.3: a provisional balance constant).
- A ticket's text comes from reference data, never from the model. Owned-ticket refs are `hb.ticket.*` (reference `tickets`) or `trope:<id>` (reference `tropes`).
- Every state change is an engine op that writes an event. Narration never changes stats, tickets, uses or modifiers.
- Hidden tickets (an NPC's unrevealed tropes) and their names never reach a player endpoint.
- `E:\Carousel` and `E:\dccrpg` are read-only. Port 8797.

## Review Focus

1. **reference.db is missing or was re-imported mid-session** → ticket ops fail with "reference data not imported; run import-data.cmd" and change nothing; the briefing says the text is unavailable instead of crashing. (Task 1: `test_ops_without_reference_data_refuse_cleanly`)
2. **A ticket's reference ID disappears in a re-import** → the Plan 1 safety check must protect `character_tickets.ticket_ref`, just like places and residents. (Task 4: `test_reimport_protects_owned_tickets`)
3. **The GM uses a spent or unequipped ticket, or one the character doesn't own** → `use_trope` refuses, the batch rolls back, nothing is spent. (Task 5: `test_use_trope_refusals`)
4. **An NPC's hidden trope leaks** through the briefing's player sections, the character card, the dossier or an event. (Task 8: visibility crawl with a planted hidden ticket)
5. **Restore after a scene close or a modifier**: save points must capture and restore `character_tickets` and `stat_modifiers`, so uses and buffs roll back with everything else (design §8.1). (Task 2: `test_restore_rolls_back_tickets_and_modifiers`)

---

## File structure

```
backend/app/
  carousel/refdata.py     # ATTACH reference.db as `ref`; ticket text and recharge lookups
  carousel/sheet.py       # stats, base/current/effective Plot Armor, ticket listing for a character
  rules/carousel_ops.py   # initialize_character, add_modifier, remove_modifier, grant_ticket, equip_ticket,
                          #   unequip_ticket, use_trope, reveal_ticket; recharge(); registered into CHANGE_OPS
  db.py                   # Carousel character columns, character_tickets, stat_modifiers, rulings.ticket_ref;
                          #   DCC character/campaign columns and tables removed
  memory/context.py       # PRESENT block rewritten around sheet.py; hidden tickets in GM-ONLY
  memory/views.py, memory/dossier.py, memory/snapshots.py, campaigns.py, support.py, gm/integrity.py,
  memory/generate.py      # DCC fields removed (Task 3)
  gm/tools.py             # record_ruling gains `ticket`
  gm/prompts.py           # how to read ticket numbers without dice
backend/tests/
  fixtures/carousel_seed.py   # replaces demo_seed.py
  carousel/test_refdata.py, test_sheet.py, test_carousel_ops.py, test_tickets.py, test_briefing.py
frontend/src/api.ts, views/*  # DCC character fields optional/removed so the build passes (Task 4)
```

---

### Task 1: Reference data at play time

**Files:**
- Create: `backend/app/carousel/refdata.py`
- Modify: `backend/app/gm/orchestrator.py` (`TurnService._conn` and the escalation re-connect), `backend/app/main.py` (`AppState.conn`), `backend/tests/conftest.py`, `backend/tests/carousel/mini_data.py`
- Test: `backend/tests/carousel/test_refdata.py`

**Interfaces:**
- Produces:
  - `attach_reference(conn, path: Path) -> bool`: `ATTACH DATABASE ? AS ref` when the file exists. Returns whether it attached. It runs right after `connect()`, never inside a transaction.
  - `open_campaign(settings) -> sqlite3.Connection`: `connect(settings.campaign_db)` plus `attach_reference(...)`. Used by `TurnService._conn`, the orchestrator's escalation connection and `AppState.conn`.
  - `require_reference(conn) -> None`: raises `ChangeError("reference data not imported; run import-data.cmd")` unless `ref` is attached.
  - `ticket_definition(conn, ref: str) -> dict | None`: returns `{"ref", "name", "text", "uses_max", "recharge", "source"}`. For `hb.ticket.*`, the source is `ref.tickets`; `uses_max` and `recharge` come from `raw_json.original_ticket_definition` (or `shared_pool_entry`). For `trope:<id>`, the source is `ref.tropes`; `text` is the variants' descriptions joined; `uses_max` and `recharge` are `None`, and the GM applies the text.
  - `recharge_of(defn: dict) -> Literal["scene", "story"] | None`: the explicit `recharge` (`"storyline"` → `"story"`), else the rule text matched against `^Once per (scene|story(?:line)?)\b` (case-insensitive), else `None`.
- Fixtures: the conftest `conn` fixture opens through `open_campaign` against a session-built mini reference DB. Extend `mini_data.py` with: a second base role; ticket `hb.ticket.starter.shared.quick-thinker` plus its pool entry (rule "Once per scene, …"); a ticket with no `recharge` whose rule starts "Once per storyline"; and rename the trope-bible trope (id `"1"`) to **`HIDDENTROPE-GOLF-7`**, with two variants. The visibility crawl uses that name as its marker.

- [ ] **Step 1: Write the failing tests**

```python
def test_attach_and_lookup(conn):
    d = ticket_definition(conn, "hb.ticket.base.final-girl")
    assert d["name"] == "[HB] Last One Standing" and d["text"].startswith("Once per story") and d["uses_max"] == 1
    assert ticket_definition(conn, "trope:1")["source"] == "trope_bible"
    assert ticket_definition(conn, "hb.ticket.nope") is None

def test_recharge_of():
    assert recharge_of({"recharge": "storyline"}) == "story"
    assert recharge_of({"recharge": None, "rule": "Once per scene, do X"}) == "scene"
    assert recharge_of({"recharge": None, "rule": "Once per storyline, do X"}) == "story"
    assert recharge_of({"recharge": None, "rule": "When cornered, do X"}) is None

def test_ops_without_reference_data_refuse_cleanly(tmp_path):
    c = connect(tmp_path / "c.db"); init_db(c)
    assert attach_reference(c, tmp_path / "missing.db") is False
    with pytest.raises(ChangeError, match="import-data.cmd"):
        require_reference(c)
```

- [ ] **Step 2: Run them; expect FAIL** (module missing).
- [ ] **Step 3: Implement `refdata.py`, then switch the three connection sites to `open_campaign`.**
- [ ] **Step 4: Run the full suite; expect PASS.** The existing tests must not notice the attachment.
- [ ] **Step 5: Commit** `"Attach Carousel reference data to campaign connections"`

### Task 2: Carousel sheet schema and Plot Armor

**Files:**
- Create: `backend/app/carousel/sheet.py`
- Modify: `backend/app/db.py`, `backend/app/memory/snapshots.py` (`SNAPSHOT_TABLES`, `_DELETE_ORDER`)
- Test: `backend/tests/carousel/test_sheet.py`

**Interfaces:**
- Schema (new DB only; add the matching `COLUMN_MIGRATIONS` rows, including Plan 1's missing `locations.ref_id` and `npc_profiles.ref_id`):
  - `characters` gains `mettle, moxie, hustle, savvy, grit INTEGER` (NULL = not yet assigned), `role_id TEXT, aspect_id TEXT, advanced_role_id TEXT, ruleset_version TEXT, role_reason TEXT`.
  - `character_tickets(id, campaign_id, character_id, ticket_ref TEXT NOT NULL, equipped INTEGER DEFAULT 0, uses_left INTEGER, uses_max INTEGER, recharge TEXT, hidden INTEGER DEFAULT 0, source TEXT, acquired_turn_id INTEGER, UNIQUE(character_id, ticket_ref))`.
  - `stat_modifiers(id, campaign_id, character_id, target TEXT CHECK(target IN ('mettle','moxie','hustle','savvy','grit','plot_armor')), op TEXT CHECK(op IN ('add','multiply')), value REAL NOT NULL, source TEXT NOT NULL, scope TEXT CHECK(scope IN ('scene','story','until_removed')), turn_id INTEGER)`.
  - `rulings` gains `ticket_ref TEXT`.
  - Both new tables go into `SNAPSHOT_TABLES` and `_DELETE_ORDER` (children before `characters`).
- Produces: `sheet(conn, character_id: int) -> dict` = `{"stats": {"Mettle": int|None, …}, "current": {…}, "plot_armor": {"base": int|None, "current": int|None, "effective": int|None}, "modifiers": [rows]}`. Any NULL stat makes all three PA values `None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_plot_armor_layers(conn, campaign_id, pc):
    set_stats(conn, pc, mettle=1, moxie=3, hustle=1, savvy=5, grit=1)                 # Film Buff, base 11
    add_mod(conn, pc, "moxie", "add", 1, "pep talk", "scene")
    add_mod(conn, pc, "plot_armor", "multiply", 0.5, "Trope Master", "until_removed")
    s = sheet(conn, pc)
    assert s["plot_armor"] == {"base": 11, "current": 12, "effective": 6}
    assert s["stats"]["Moxie"] == 3 and s["current"]["Moxie"] == 4                      # base points untouched

def test_unassigned_stats_have_no_plot_armor(conn, pc):
    assert sheet(conn, pc)["plot_armor"] == {"base": None, "current": None, "effective": None}

def test_restore_rolls_back_tickets_and_modifiers(conn, campaign_id, pc):
    before = count(conn, "character_tickets")
    sp = create_save_point(conn, campaign_id, "before")
    add_mod(conn, pc, "grit", "add", 2, "adrenaline", "scene")
    conn.execute("INSERT INTO character_tickets (campaign_id, character_id, ticket_ref) VALUES (?,?,?)", (campaign_id, pc, "hb.ticket.starter.shared.quick-thinker"))
    restore_save_point(conn, campaign_id, sp)
    assert count(conn, "stat_modifiers") == 0 and count(conn, "character_tickets") == before
```
(`set_stats`, `add_mod` and `count` are tiny SQL helpers in the test module; `restore_save_point` is the existing `snapshots` restore. Until Task 3's seed exists, these tests insert their own campaign and character, then switch to the `campaign_id`/`pc` fixtures in Task 3.)

- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement.** **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** `"Carousel character sheet: five stats, Plot Armor layers, tickets and modifiers tables"`

### Task 3: Remove the DCC character model; Carousel test seed

**Files:**
- Create: `backend/tests/fixtures/carousel_seed.py`
- Delete: `backend/tests/fixtures/demo_seed.py`
- Modify: `backend/app/db.py`, `campaigns.py`, `memory/views.py`, `memory/dossier.py`, `memory/context.py`, `memory/snapshots.py`, `memory/generate.py`, `support.py`, `gm/integrity.py`, `gm/orchestrator.py`, `gm/providers.py`, `gm/tools.py`, `main.py`, `rules/engine.py`, and every test that used `demo_id`
- Test: the whole existing suite, on the new seed

**Interfaces:**
- Removed from the schema: `characters.{crawler_number, race, class_name, level, xp, hp_current, hp_max, mana_current, mana_max, ai_favor, derived_json}`, tables `skills`, `achievements`, `encounters`, `ability_uses`, `campaigns.{current_floor, floor_deadline_minutes, floor_clock_pending_minutes, day_length_minutes}`, and `locations.floor`. Keep `stats_json` / `sheet_json` only if something still reads them; otherwise drop them too. `kind` values become `pc | npc | contact` (the DCC `crawler` becomes `pc`).
- `campaigns.create(conn, *, name, character, location=None, premise=None) -> int`: the floor and race parameters are gone. `CampaignIn` in `main.py` matches (`character`, not `crawler`).
- `seed_carousel(conn) -> int`: campaign "Test Town" at "Town Square" (location `ref_id` "CAR-001"). It holds PC **Mara Vance** (stats 1/3/1/5/1, role `base.final-girl` from the mini reference, owns and has equipped `hb.ticket.base.final-girl`); companion **Jonah Reyes**; NPC **Adeline Winter** (`npc_profiles.ref_id` "CAR-NPC-001") with one hidden ticket `trope:1`; a gm_secret containing `"DEMO SECRET"`; a Healing Potion ×2 on Mara; a player-visible quest; and one house rule. Conftest fixtures become `campaign_id`, `pc` (Mara), `companion` (Jonah) and `npc` (Adeline), each the row id; test modules use `apply(conn, cid, change)` = `apply_change` inside `transaction`. Tests that asserted on DCC-only data (HP, levels, skills) are rewritten against the Carousel fields or deleted.

- [ ] **Step 1: Write `carousel_seed.py` and switch the conftest. Run the suite and list the failures.** Expected: failures that name every DCC field still read.
- [ ] **Step 2: Remove the DCC columns and tables, and fix each reader the failures name.** The character card and dossier expose `sheet()` output instead of HP/level/skills.
- [ ] **Step 3: Run the full suite; expect PASS.** Also run `grep -rnE "hp_current|mana_|ai_favor|class_name|current_floor|skills|achievements|encounters|ability_uses" backend/app`. Expected: no matches.
- [ ] **Step 4: Commit** `"Replace the DCC character model with Carousel's; Carousel test seed"`

### Task 4: Frontend follow-up and re-import protection for tickets

**Files:**
- Modify: `frontend/src/api.ts`, `frontend/src/views/{Hud,Sheet,Dossier,Journals,People,Story}.tsx` (only what `tsc` flags), `backend/app/carousel/refs.py`
- Test: `backend/tests/carousel/test_importer.py`; `build-frontend.cmd`

**Interfaces:**
- `api.ts` `CharacterCard` drops the DCC fields and gains `sheet: { stats, current, plot_armor, … }` and `tickets: { ref, name, equipped, uses_left, uses_max, recharge }[]`. Views show the PA trio in place of HP and the ticket names in place of skills. No new screens; the Red Wallpaper sheet is Phase 8.
- `refs.REFERENCE_COLUMNS` gains `"ticket": ("character_tickets", "ticket_ref")`. `unresolved` checks `"ticket"` against `ref.tickets` (`hb.ticket.*`) or `ref.tropes` (`trope:<id>`). Save-point snapshots are covered by the existing loop.

- [ ] **Step 1: Write the failing test**

```python
def test_reimport_protects_owned_tickets(settings_mini, campaign_conn):
    cid = campaign_conn.execute("INSERT INTO characters (campaign_id, slug, name, kind) VALUES (1, 'm', 'M', 'pc')").lastrowid
    campaign_conn.execute("INSERT INTO character_tickets (campaign_id, character_id, ticket_ref) VALUES (1, ?, 'hb.ticket.base.gone')", (cid,))
    campaign_conn.commit()
    r = run_import(settings_mini)
    assert not r.ok and r.missing == {"ticket": ["hb.ticket.base.gone"]}
```
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement `refs.py`. Fix `api.ts` and the views until `build-frontend.cmd` passes.** **Step 4: Run the suite; expect PASS.** Open the app in the browser pane on a seeded campaign. Expected: no console errors.
- [ ] **Step 5: Commit** `"Character cards show Carousel stats; re-import protects owned tickets"`

### Task 5: Character creation, modifiers and ticket ops

**Files:**
- Create: `backend/app/rules/carousel_ops.py` (registered into `CHANGE_OPS` at import, like `state_ops.register`)
- Modify: `backend/app/rules/state_ops.py` (`_close` calls `recharge(ctx, "scene")` and drops `scope='scene'` modifiers; the `ability_uses` reset line goes)
- Test: `backend/tests/carousel/test_carousel_ops.py`, `backend/tests/carousel/test_tickets.py`

**Interfaces (op names and fields, exactly):**
- `initialize_character {character, role, reason}`: `role` must be a `kind='base'` role in `ref.roles` with a `ref.start_profiles` row. It copies the profile's five stats, sets `role_id`, `role_reason` and `ruleset_version="SB-0.4.1/role-0.4.2"`, and writes event `character_stats_initialized` (payload: role, stats, base_pa, ruleset). It refuses if the character already has stats ("stats are set once; changes go through modifiers or rewards").
- `add_modifier {character, target, op, value, source, scope}` / `remove_modifier {modifier_id, reason}`: `value` must be > 0 for multiply, and nonzero for add.
- `grant_ticket {character, ticket, source, hidden?=false}`: the ref must resolve through `ticket_definition`. It copies `uses_max`, sets `uses_left = uses_max`, and copies `recharge`. A duplicate is refused ("already owns"). The event is `ticket_granted`, with visibility `gm` when `hidden`.
- `equip_ticket {character, ticket}` / `unequip_ticket {character, ticket}`: equipping a 5th ticket is refused ("equip limit 4; unequip one first").
- `use_trope {character, ticket, effect}`: the ticket must be owned and equipped, and `uses_left` must be NULL or above 0. It decrements `uses_left` when not NULL and writes event `trope_used` with the effect text (visibility `gm` when the ticket is hidden). The result returns `uses_left`.
- `reveal_ticket {character, ticket, how}`: sets `hidden = 0` and writes a player event "X's trope Y is revealed (how)".
- `recharge(ctx, scope: "scene" | "story") -> int`: sets `uses_left = uses_max` where `recharge = scope`. Phase 4 calls the `"story"` form at The End.
- Characters resolve through `engine.resolve_character`; tickets by ref or by exact name among that character's tickets. Every op starts with `require_reference(ctx.conn)`.

- [ ] **Step 1: Write the failing tests** (on the Carousel seed)

```python
def test_initialize_character_once(conn, campaign_id, companion):
    apply(conn, campaign_id, {"op": "initialize_character", "character": "Jonah Reyes", "role": "base.final-girl", "reason": "kept the group together on the drive"})
    assert sheet(conn, companion)["plot_armor"]["base"] == 11
    with pytest.raises(ChangeError, match="set once"):
        apply(conn, campaign_id, {"op": "initialize_character", "character": "Jonah Reyes", "role": "base.final-girl", "reason": "again"})

def test_initialize_rejects_non_starting_role(conn, campaign_id): ...   # an aspect id → ChangeError

def test_use_trope_spends_and_scene_close_recharges(conn, campaign_id, pc):
    r = apply(conn, campaign_id, {"op": "use_trope", "character": "Mara Vance", "ticket": "hb.ticket.base.final-girl", "effect": "walks out of the fire"})
    assert r["uses_left"] == 0
    # its recharge is "story" (rule text "Once per story…"): a scene close does not refill it
    apply(conn, campaign_id, {"op": "scene", "action": "start", "title": "A"}); apply(conn, campaign_id, {"op": "scene", "action": "start", "title": "B", "summary": "x"})
    assert uses_left(conn, pc, "hb.ticket.base.final-girl") == 0

def test_use_trope_refusals(conn, campaign_id):
    for change, why in [({"ticket": "trope:1"}, "does not own"),                  # Adeline's, not Mara's
                        ({"ticket": "hb.ticket.starter.shared.quick-thinker"}, "does not own")]:
        with pytest.raises(ChangeError, match=why): ...
    # unequipped → "not equipped"; spent → "no uses left"; and a failing use inside apply_changes rolls back the whole batch

def test_equip_limit(conn, campaign_id): ...                                  # grant 5 tickets, equip 4, 5th → "equip limit 4"

def test_hidden_ticket_events_are_gm_only(conn, campaign_id): ...             # Adeline's trope:1 use → event visibility 'gm'
```
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement.** **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** `"Engine ops for character creation, modifiers and trope tickets"`

### Task 6: The per-turn ticket briefing

**Files:**
- Modify: `backend/app/memory/context.py` (`_full_char` rewritten around `sheet()`; a new `_tickets_block`; the GM-ONLY section gains hidden tickets), `backend/app/gm/prompts.py`
- Test: `backend/tests/carousel/test_briefing.py`

**Interfaces:**
- A present character's block lists: role and reason, the five stats (base → current when they differ), `Plot Armor base/current/effective`, active modifiers with their sources, statuses, carried items, then **every owned, non-hidden ticket with its full text**, marked `[EQUIPPED]` or `[owned, not equipped]`, with `uses left n/max (recharges per scene|story)` or `GM-adjudicated uses`. Precedent and provisional rulings with `ticket_ref` in play are printed under their ticket.
- Hidden tickets of present characters go to `GM-ONLY SECRETS` as `"<name> secretly holds <ticket>: <text>"`.
- Ticket text is never cut by the per-character budget. If a block runs over, the cut lands on items and memories first, and `omissions` says so.
- If `ref` isn't attached, ticket lines read `"(ticket text unavailable: reference data not imported)"`.
- Prompt addition (exact copy): `"Numbers in ticket text ("+2 to a check") describe a relative edge for you to weigh in the fiction. There are no dice. When a ticket fires, call apply_changes op use_trope; when its text is vague, interpret it in its spirit and call record_ruling with its ticket so later turns stay consistent."`

- [ ] **Step 1: Write the failing tests**

```python
def test_present_character_brief_has_full_ticket_text_and_pa(conn, campaign_id):
    text = build_context(conn, campaign_id, "look around")["text"]
    assert "Plot Armor 11/11/11" in text and "[EQUIPPED]" in text
    assert "Once per story, survive one certain death." in text          # mini reference ticket text, verbatim

def test_hidden_npc_ticket_only_in_gm_section(conn, campaign_id):
    ctx = build_context(conn, campaign_id, "Adeline, a word?")["text"]
    gm = ctx.split("## GM-ONLY SECRETS")[1]
    assert "secretly holds" in gm and "secretly holds" not in ctx.split("## GM-ONLY SECRETS")[0]

def test_ticket_rulings_travel_with_the_ticket(conn, campaign_id): ...   # an approved ruling with ticket_ref appears under that ticket

def test_ticket_text_survives_tight_budgets(conn, campaign_id):
    text = build_context(conn, campaign_id, "x", budgets={"present_each": 300})["text"]
    assert "Once per story, survive one certain death." in text
```
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement.** **Step 4: Run the full suite; expect PASS.**
- [ ] **Step 5: Commit** `"GM briefing carries stats, Plot Armor and full ticket text for everyone present"`

### Task 7: Rulings tied to tickets

**Files:**
- Modify: `backend/app/gm/tools.py` (`record_ruling` takes `ticket?`), `backend/app/memory/rulings.py` (returns `ticket_ref`)
- Test: `backend/tests/carousel/test_tickets.py`

**Interfaces:** `record_ruling {topic, ruling, label, certainty?, sources?, ticket?}`. `ticket` must resolve through `ticket_definition` (else the tool errors) and is stored in `rulings.ticket_ref`. The owner's review endpoint is unchanged.

- [ ] **Step 1: Failing test**: a `record_ruling` with `ticket="hb.ticket.base.final-girl"` stores `ticket_ref`; an unknown ticket returns `ok: False`.
- [ ] **Steps 2–4:** RED → implement → GREEN (full suite).
- [ ] **Step 5: Commit** `"Rulings can interpret a specific ticket"`

### Task 8: Player view and visibility

**Files:**
- Modify: `backend/app/memory/views.py` (`character_card` adds `sheet` and visible `tickets`), `backend/app/memory/dossier.py`, `backend/tests/test_visibility.py`
- Test: `backend/tests/test_visibility.py`, `backend/tests/carousel/test_tickets.py`

**Interfaces:** `character_card(conn, c)["tickets"]` = owned tickets with `hidden = 0`: `{ref, name, text, equipped, uses_left, uses_max, recharge}`. Hidden ones are omitted entirely, with no count and no placeholder. Revealed NPC tickets appear.

- [ ] **Step 1: Failing tests:** the visibility crawl plants a hidden ticket name (`"HIDDENTROPE-GOLF-7"` as a trope name in the mini reference, owned hidden by Adeline) and asserts it never appears in any player URL, including after a `use_trope` turn. A card test asserts that after `reveal_ticket` it appears.
- [ ] **Steps 2–4:** RED → implement → GREEN (full suite), plus `build-frontend.cmd`.
- [ ] **Step 5: Commit** `"Players see their stats, Plot Armor and tickets; hidden tropes stay hidden"`

---

## Done when

- A seeded campaign shows each character's five stats, Plot Armor (base/current/effective) and tickets on the API, and the frontend builds and renders it without console errors.
- The GM briefing for any turn includes the full text of every ticket held by everyone present, with equip state, uses, recharge and the rulings on it; hidden tropes appear only in GM-ONLY.
- Every change to stats, modifiers and tickets goes through an op with an event, and save points roll them back.
- No DCC character or floor fields remain in `backend/app` (the Task 3 grep returns nothing).
- Full suite green. Next: Plan 3 (maps and travel).

## Not in this plan (by design)

- Silas choosing the Archetype from the car conversation: Phase 5 calls `initialize_character` and `grant_ticket`. This plan provides the ops and validation.
- Aspect unlock (PA ≥ 21 plus a feat), advanced-role marks (10 per track), five-star stat rewards and ticket rewards: Phase 4, with story rewards. The `aspect_id` and `advanced_role_id` columns exist for it.
- Screenplay revisions from tickets that rewrite a story: Phase 4. `use_trope` records the effect text, which the story runner will extend.
- `"story"` recharge at The End: Phase 4 calls `recharge(ctx, "story")`.

## Left to the builder

- Exact wording of the briefing lines beyond the strings the tests pin.
- Whether `stats_json` / `sheet_json` survive Task 3 (drop them if nothing reads them).
- How the frontend lays out the PA trio and ticket list within the existing card (Phase 8 redesigns it).
