# Carousel Plan 9: Loose ends — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close what Phases 1–8 left dangling, so the game plays on the real data without dead ends: 41 of 93 playable Omens can't reach the player, 7 venues can never be entered, the GM's rule search has nothing indexed, and the AI still gets Crawler Console wording in some prompts. This plan adds no new features; each task fixes one of these.

**Found on 2026-11-02** (at commit `1e94337`, with the real `data/reference.db` imported 2026-11-02):

| Gap | Evidence | Task |
|---|---|---|
| Omens the engine never surfaces | Interruptions only use `fixed` + `placement='place'` and `roaming` (`carousel/interruptions.py:52,64`). Never surfaced: 5 fixed-area (4 at Town Square, 1 Downtown), 9 item-bound and 4 special Omens at places with coordinates. Nothing tells the GM they exist. | 1 |
| Story-only venues can't be entered | 5 `story_instance` and 2 `extradimensional` venues are imported with `enterable = 0` and have no way in (Plan 5.5). Two of them hold Omens: OM-052/054 in SM-096, OM-104/105 in SM-073. | 2 |
| GM rule search is empty | `search_rules`/`read_rule_page` index PDFs from `RULEBOOK_DIR`. There is no `data/rulebooks.db`, `KNOWN_TITLES` names the DCC books, and the Carousel bibles are Markdown. Design §13 left "repoint FTS at the bibles" to the builder. | 3 |
| DCC wording sent to models | Character generation (`memory/generate.py:49–72`), art prompts (`art/service.py:31,34,378,412`, `art/codex_image.py:136`), the `apply_changes` tool text (`gm/tools.py:29`: `use_ability`, saferoom rests and Health/Mana mending, none of which exist), the briefing header (`memory/context.py:4,158`), the integrity check "dungeon time" (`gm/integrity.py:116`). | 4 |
| Dead DCC server code | The frontend no longer calls `inventory`, `quests` or `journal` (Plan 8). Also the `AdvanceTime.mending` field, the `floor` shortcut (`memory/places.py:34`), DCC event types in `campaign_summary.recent_events`, the `FloorClock`/`floor_clock` type, `package.json` name `crawler-console`, and README/ARCHITECTURE describe Crawler Console. | 5 |
| No run on real data | Every test runs on `mini_data`. Nothing checks that the real reference DB (311 places, 93 playable Omens, 108 venues, 70 residents) works end to end. | 6 |

**Before starting:** the working tree holds another thread's uncommitted asset-reconciliation work: `Asset_Reconciliation/game_reference.json` as a new source, `validate_assets.py`, `map_api.py` "unplaced" places, and MapView/PlaceDossier changes, plus `scripts/`. **It must be committed by its owner first.** Plan 9 builds on it and never edits or reverts those files while they're uncommitted. Task 6 assumes the reconciled import. If that work isn't committed yet, stop and ask.

**Tech Stack:** FastAPI, SQLite (FTS5), pydantic ops, pytest; a little React/TS (Task 5 only).

**Spec:** [docs/2026-10-30-carousel-game-design.md](../../2026-10-30-carousel-game-design.md) §6, §7, §13; the roadmap's "Corrections" (spawn modes 49/24/15/5); Plan 5.5's binding rule for non-enterable venues.

## Global Constraints

- **No new player powers, no new screens.** Every fix is engine, briefing, prompt text or deletion. The one frontend change is deleting dead types and calls (Task 5).
- **Unplaced Omens stay out.** About 23 Omens sit at `AUD-LOC-*` places without coordinates (import report `omen_placement_pending`). Track B fixes those. The game must never invent a position or a pin for them.
- **The GM decides item and special conditions.** The engine doesn't model "holds the cursed item" or "19:23 at the Olde Hill gate". It tells the GM, GM-only, that such an Omen is anchored here and what its condition is. The GM sights it with the existing `sight_omen` op when play meets the condition.
- **GM-only stays GM-only.** Omen leads, spawn conditions and story-venue zones never reach a player endpoint. The visibility crawl gets new planted strings for each.
- **Source precedence for rule search** (design §3): System Bible v0.4.1, Role Bible v0.4.2, Trope Bible v0.1.1, Trope Engine v0.2.1, Storyline Screenplay Bible v0.1.1, Production Standard v0.2. Read-only from `E:\Carousel`.
- **Crawler Console untouched;** nothing written into `E:\Carousel`.
- **Commits:** commit with explicit paths only, end each message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Area Omens don't flood a place (Task 1).** Town Square anchors four fixed-area Omens. Each arrival surfaces at most one (the existing one-interruption-per-route rule), in Omen-id order. The other three wait for later visits.
2. **Omen leads stay GM-only (Task 1).** The planted spawn text `SPAWN-PAPA-15` appears in the briefing and in no player response.
3. **Travel out of a story venue is refused (Task 2).** While the group is inside a `story_instance` or `extradimensional` venue, `travel` is refused with a clear reason. `conclude_story` always brings the group back to the town venue at its node, so a story can never strand it.
4. **The rule index is rebuilt by the importer (Task 3).** `import-data.cmd` rebuilds `data/rulebooks.db` from the bibles. A changed bible changes the index on the next import, and nothing indexes from `RULEBOOK_DIR` any more.
5. **A test catches DCC wording in prompts (Task 4).** It scans the prompt constants that are actually sent to a model, so a reintroduced "crawler" fails the suite.

---

### Task 1: Omens the engine never surfaces

**Files:**
- Modify: `backend/app/carousel/interruptions.py` (fixed `area` Omens interrupt at their anchor place)
- Modify: `backend/app/memory/context.py` (new GM-only `OMEN LEADS` section; register it in `CATEGORIES`)
- Modify: `backend/tests/carousel/mini_data.py` (one fixed-area Omen anchored on a district or the Town Square, so that import gives it `placement='area'`; one `item` Omen and one `special` Omen at Town Square with `"Spawn / Timing": "SPAWN-PAPA-15 …"`); then re-pin the mini-data counts it changes (`test_import_omens.py`)
- Modify: `backend/tests/test_visibility.py` (add `SPAWN-PAPA-15`)
- Test: `backend/tests/carousel/test_omen_leads.py`, `backend/tests/carousel/test_interruptions.py`

**Area Omens.** In `decide()`, the fixed lookup takes both placements:

```python
for o in _unseen(conn, cid, "fixed"):            # place- and area-anchored alike; unplaced ones have no place row
    if o["north"] is not None:                   # an AUD-LOC record without a parcel is never a stop
        fixed.setdefault(o["place_id"], o["id"])
```

`_unseen` already joins `ref.places`. Check `import_omens.py` for how it sets `placement` and add the mini Omen to match. Roaming area Omens already work, because the roaming query takes every placement with coordinates. Leave them as they are.

**Omen leads.** This is a briefing section the GM sees only while the group stands at a place that anchors one:

```
OMEN LEADS (GM only; never say these exist. Sight one with sight_omen only when play meets its condition)
- OM-032 [special] {description}. Condition: At 19:23 at the Olde Hill gate; exact recurrence per Atlas.
- OM-072 [item] … Condition: Must acquire/retain the specified item first; object can travel. The Choice occurs at …
```

- **Which Omens:** Playable Omens with `spawn_mode IN ('item','special')` whose resolved `place_id` is the group's current place. Left out: ones already in `omen_states`, ones placed in a venue zone (`venue_omens.placed = 1`, which the zone sighting handles), and ones held by an active story lock.
- **Limits:** at most 4 lines. The condition is the raw `Spawn / Timing` text, cut at 200 characters. The current time is already in the CAMPAIGN line.
- **What it never adds:** the Omen's sealed truth, beats or story title. Only `id`, `spawn_mode`, `description` and the condition.
- **An item-bound Omen whose item travels** (raw text "object can travel") is listed at its anchor place only. Following the object is the GM's job, through `sight_omen` wherever it shows up. This is noted in the section's header comment, not engine-tracked.

**Tests:**
- `test_interruptions.py`: a route through the mini area Omen's anchor stops there with `kind="fixed"`. The next route through the same place, after `sight_omen`, doesn't stop. An Omen whose place has no coordinates never stops a route.
- `test_omen_leads.py`:
  - At Town Square, the briefing contains `OMEN LEADS`, both mini Omens and `SPAWN-PAPA-15`.
  - At the Lodge, there's no `OMEN LEADS` section.
  - After `sight_omen` on the item Omen, only the special one is listed.
  - Planted sealed text for those Omens never appears.
- The visibility crawl stays green with `SPAWN-PAPA-15` planted.

**Steps:**
- [ ] Add the three mini Omens; re-pin the mini import counts.
- [ ] Write the interruption tests and the omen-lead tests; see them fail.
- [ ] Change `decide()`; write the `OMEN LEADS` section.
- [ ] Add the secret to the crawl; run the carousel suite and `test_visibility.py`.
- [ ] Commit: `Fixed area Omens stop the group at their anchor; item and special Omens reach the GM as leads, GM-only`.

---

### Task 2: Story-only venues

**Files:**
- Create: `backend/app/rules/story_venue_ops.py` (register in `CHANGE_OPS`)
- Modify: `backend/app/carousel/venues.py` (`enter_story(conn, cid, venue_id, group_id)` and `in_story_venue(conn, cid) -> dict | None`), `backend/app/rules/travel_ops.py` (refuse), `backend/app/rules/story_ops.py` (`conclude_story` leaves), `backend/app/memory/context.py` (VENUE section names the story venue and its exit)
- Check: `backend/app/venue_api.py` and `rules/venue_ops.py:move_zone`. If either filters on `enterable`, let a story-layer venue through while the campaign is inside it.
- Modify: `backend/tests/carousel/mini_data.py` (a `story_instance` venue with 3 zones whose `omen_ids` hold OM-001, members `["CAR-086"]`)
- Test: `backend/tests/carousel/test_story_venues.py`

**Ops:**

```python
class EnterStoryVenue(BaseModel):
    op: Literal["enter_story_venue"]
    venue: str                     # SM-…; layer story_instance or extradimensional
    reason: str = Field(min_length=1, max_length=200)

class LeaveStoryVenue(BaseModel):
    op: Literal["leave_story_venue"]
```

- **`enter_story_venue` is refused unless:**
  - a story is active;
  - the venue's layer is `story_instance` or `extradimensional`;
  - the active story's Omen is in that venue's `venue_omens`, or the group's current place is one of the venue's members (aliases resolved);
  - nobody in the PC's group is `GONE`-only. A wiped group can't enter.
- **What it does:**
  - puts the campaign and the PC's group at `entry_zone`;
  - records a `zone_visits` row;
  - writes the event `story_venue_entered` (player-visible summary "The group goes into {title}."; the `reason` is GM-only).
- **`leave_story_venue`** is allowed only from the venue's `exit_zone`. It calls `venues.enter(conn, cid, node_id, group_id)` to restore the town venue at the group's node. Event `story_venue_left`.
- **`travel` while `in_story_venue`:** `ChangeError("the group is inside {title}: leave by its exit ({exit label}) or end the story first")`.
- **`conclude_story`:** if the group is inside a story venue, it leaves it as part of the same change, whatever the ending.
- **The GM briefing VENUE section** says: `Inside {title} (story venue; travel is closed until the group leaves by {exit label} or the story ends).`

**Tests:**
- Enter is refused with no active story, for a persistent venue, and for a story venue that holds neither the story's Omen nor the group's place.
- Enter works for the mini story venue during OM-001's story: the position is `entry_zone` and `GET /venue` shows the venue's title and entry zone.
- `travel` is refused inside; `move_zone` works along the venue's links.
- `leave_story_venue` is refused away from the exit and works at the exit. The town venue at the node is restored.
- `conclude_story` from inside leaves automatically.
- A story venue's zone labels never appear in a player response before the group enters (crawl).

**Steps:**
- [ ] Add the mini story venue; write the tests; see them fail.
- [ ] Write the helpers, the two ops, the travel refusal and the conclude hook.
- [ ] Extend the VENUE briefing line; check `venue_api`/`move_zone` for the `enterable` filter.
- [ ] Run the carousel suite and the crawl.
- [ ] Commit: `Story-only venues: the GM takes the group in during its story; travel waits; the story's end always brings the group back`.

---

### Task 3: Rule search on the Carousel bibles

**Files:**
- Modify: `backend/app/reference/rulebooks.py` (Markdown ingest; `BIBLES` replaces `KNOWN_TITLES`; delete the PDF path: `pick_variants`, `_clean_pages`, `ingest_pdf`, `ingest_directory`)
- Modify: `backend/app/carousel/importer.py` (rebuild `rulebooks.db` after a successful reference import), `backend/app/main.py` (`/api/rules/index` re-runs the bible index; drop `rulebook_dir`), `backend/app/config.py` (drop `rulebook_dir`/`RULEBOOK_DIR`), `backend/requirements.txt` (drop `pymupdf`)
- Modify: `backend/app/gm/tools.py` (`search_rules`/`read_rule_page` say "the Carousel bibles"; `page` is a section number)
- Modify: `frontend/src/views/Reference.tsx` (the books panel says "sections", and "Index" re-runs the bible index; no directory input)
- Test: `backend/tests/test_rulebooks.py` (rewrite)

**The documents** (paths under the source dir; a missing file is a warning in the index status, not a failure):

```python
BIBLES = [
    ("system/Carousel_System_Bible_v0.4.1.md", "System Bible v0.4.1"),
    ("roles/Carousel_Role_Bible_v0_4_2_Implementation.md", "Role Bible v0.4.2"),
    ("tropes/Carousel_Trope_Bible_v0.1.1.md", "Trope Bible v0.1.1"),
    ("trope engine/Game_at_Carousel_Trope_Engine_v0.2.1.md", "Trope Engine v0.2.1"),
    ("storyline/Storyline_Screenplay_Bible_v0_1_1.md", "Storyline Screenplay Bible v0.1.1"),
    ("storyline/Carousel_Storyline_Production_Standard_v0_2.md", "Storyline Production Standard v0.2"),
]
```

- **Sections as pages:** split each file at `#`/`##` headings. Each heading starts a new "page" (1-based), and the heading text is kept at the top of its page. Chunk pages as now (`CHUNK_CHARS`, `CHUNK_OVERLAP`), and keep `sha256` per document.
- **Search order** follows the BIBLES order on equal scores, because that is the source precedence. A passage's `ref` reads `System Bible v0.4.1 §12 Plot Armor`.
- **Rebuild:** into `rulebooks.db.new`, then swap it in (same pattern as `reference.db`).

**Tests** (with a temporary source dir holding two tiny Markdown bibles):
- Each `##` heading becomes a page, and `page_text` returns its heading and body.
- `search("plot armor")` returns the passage with the ref `{title} §{n} {heading}`.
- A changed file changes the index after a rebuild; a missing file is reported, and the others are still indexed.
- The real-source test (skipped without `E:\Carousel`, like `tests/carousel/conftest.py`): all six bibles are indexed, and searching "Trope Master" hits the Trope Bible or the Trope Engine.

**Steps:**
- [ ] Rewrite `test_rulebooks.py`; see it fail.
- [ ] Rewrite the indexer for Markdown; delete the PDF code and `pymupdf`.
- [ ] Hook the rebuild into the importer; simplify `/api/rules/index` and the Rules panel.
- [ ] Re-run `import-data.cmd`; in the preview, Behind the Scenes › Rules finds "Plot Armor".
- [ ] Commit: `Rule search indexes the Carousel bibles by section; the importer rebuilds it; PDF rulebooks removed`.

---

### Task 4: No Crawler Console wording in what the AI is told

**Files:**
- Modify: `backend/app/memory/generate.py` (`DEVELOPER` and the docstring), `backend/app/art/service.py` (style string, `mob` kind, `ITEM_LOOK_SYSTEM`, the place-look system prompt at :412, the job path label `crawler-console/` → `carousel/`), `backend/app/art/codex_image.py:136`, `backend/app/gm/tools.py` (`apply_changes` description), `backend/app/memory/context.py` (header comment and the line at :158), `backend/app/gm/integrity.py` (check renamed "game time"; drop the floor-clock clause), `backend/app/memory/journal.py:126` (topic regex), `backend/app/campaigns.py:27` (slug fallback `"campaign"`)
- Test: `backend/tests/test_no_dcc_text.py`

**Replacements (wording is the builder's; these are the facts each must carry):**
- **Character generation:**
  - The NPC is someone in or around Carousel, the town from Rob M. Lastrel's *The Game at Carousel*, where horror movies happen for real.
  - Most residents are trapped "players" who arrived like the PC. Locals and townsfolk exist too.
  - The background is a life, not a sheet. Never list Mettle/Moxie/Hustle/Savvy/Grit, Plot Armor or tickets (the engine owns them).
  - Keep the existing output JSON shape.
- **Art:**
  - Style: warm small-town Americana photographed like a prestige horror film, practical lighting, film grain, unsettling but not gory.
  - Kind `mob` becomes "something from a horror movie, full figure, in a town setting". Keep the key `mob` so stored jobs still load.
  - Item looks: everyday small-town objects that may be wrong in some way.
  - Place looks: Carousel's places, never "the Over City".
- **`apply_changes` description:**
  - Remove `use_ability`, saferoom rests and Health/Mana mending.
  - Mention `travel`/`move_zone` for movement, `quest` kind `commitment` for promises, `remember` and `gm_note` for hidden material.
  - Keep the "SHOWN TO THE PLAYER" warning.
- **Briefing header:** the section list in the module docstring matches the real sections. "a person in front of the crawler" → "in front of the group".

**Test (`test_no_dcc_text.py`):**
- Import the modules and collect every module-level `str` constant from `gm.prompts`, `gm.tools` (including the `TOOL_SPECS` descriptions), `memory.generate`, `art.service`, `art.codex_image` and `memory.context`.
- Assert that none matches `\b(crawler|dungeon|syndicate|borant|saferoom|mana|floor clock|over city)\b`, case-insensitive.
- Also render one briefing for the seeded campaign and assert the same regex finds nothing in it.

**Steps:**
- [ ] Write the test; see it fail and list every hit.
- [ ] Rewrite each string; keep the JSON shapes and kind keys.
- [ ] Full backend suite.
- [ ] Commit: `Prompts sent to the GM, character generation and art speak Carousel; a test keeps DCC wording out`.

---

### Task 5: Delete the dead DCC server code

**Files:**
- Modify: `backend/app/main.py:690` (drop the `inventory`, `quests` and `journal` routes), `backend/app/memory/views.py` (drop `inventory`, `quests`, the DCC event types in `recent_events`, and the `"dungeon"` mention), `backend/app/rules/engine.py` (drop `AdvanceTime.mending` and its comment; drop `"dungeon_staff"` from the NPC category literal if no row uses it, checked against `db.py` and the importer), `backend/app/memory/places.py` (drop the `floor` shortcut), `backend/app/rules/state_ops.py:260` (empty "rest and mending" header)
- Delete: `backend/app/memory/inventory.py` if nothing else imports it (check `consistency.py:36`, which reads gold; drop that check too)
- Modify: `backend/tests/test_visibility.py` (drop the removed URLs), and any tests that call the removed routes
- Modify: `frontend/src/api.ts` (drop `FloorClock`, `floor_clock`, `Quest`/`quests`, `Achievements` and the types used only by deleted views), `frontend/package.json` (`"name": "carousel"`), `frontend/src/prefs.ts` (the `achievement` cue name becomes `notice` if nothing else uses it)
- Rewrite: `README.md`, `ARCHITECTURE.md` for Carousel: what it is, `start.cmd`/`import-data.cmd`/`build-frontend.cmd`/`run-tests.cmd`, port 8799, the source dir, the reference DB swap, the player/GM boundary. No DCC history beyond one line saying it was forked from Crawler Console.

**Kept on purpose:** the `quests` table and the `quest` op (the GM uses `commitment`), items and the item ops, house rules and rulings, the ledger importer modules, the `achievements` table (schema migrations stay additive), and the access cookie name (renaming it would sign the player out for no gain).

**Steps:**
- [ ] Remove the routes and functions; run the full backend suite and fix what referenced them.
- [ ] Trim `api.ts` and `package.json`; build the frontend (`build-frontend.cmd`) with no type errors.
- [ ] Rewrite README and ARCHITECTURE.
- [ ] Commit: `Remove dead DCC server routes, fields and types; README and ARCHITECTURE describe Carousel`.

---

### Task 6: A smoke run on the real data, and the roadmap brought up to date

**Files:**
- Create: `backend/tests/carousel/test_real_data.py` (skipped when `E:\Carousel` or `data/reference.db` is missing, using the same guard as `tests/carousel/conftest.py`; it runs against a copy of the real reference DB in a temp dir)
- Modify: `docs/superpowers/plans/2026-10-31-carousel-roadmap.md`

**What the smoke run checks**, on a fresh campaign over the real reference DB, with the `mock` GM provider (no model calls):
1. `create` gives 70 unmet residents and the arrival lot; the opening stages advance with their ops to `done` at the Lodge.
2. `travel` reaches each safe place (CAR-046, CAR-047, CAR-002, CAR-006) and the bowling alley (`AUD-LOC-BOWLING-ALLEY`). Each arrival either enters its venue's entry zone or reports no venue.
3. For every enterable venue: entering at its member node puts the group at `entry_zone`, and every zone is reachable from `entry_zone` over its links (no orphan rooms).
4. Every Playable Omen is in exactly one of these buckets, and the counts are printed in the test output:
   - surfaced by the engine (fixed or roaming with coordinates, or venue-placed);
   - a GM lead (item or special with coordinates);
   - inside a story venue;
   - waiting on Track B (no coordinates).

   The test fails if an Omen is in none of them.
5. Over 24 game hours (four `advance_time` steps), `where()` gives every resident a known place, and the briefing stays under the Plan 7 budget at each step.
6. The visibility crawl's player URLs, run against this campaign, return 200 and contain no `gm_only` text from five sampled residents.

**Roadmap:**
- Mark Phases 5.5–9 done, linking each plan.
- **Track B:**
  - Item 2 (bowling alley) is installed as `AUD-LOC-BOWLING-ALLEY`.
  - Record the reconciliation package as the art and asset source.
  - Check that the Cold Case route still goes through City Hall (item 1), and mark it done or open accordingly.
  - The panel map still has `human_approved: 0`.
- **New "Track B: open data items" list**, from the import report and the smoke run's buckets:
  - the Omens waiting for a parcel;
  - the 20 venues waiting for a reviewed runtime graph;
  - the 11 venues with no member place on the graph.

  These are Codex/Claude data jobs in `E:\Carousel`, not game code.
- Move the plans' "Left out on purpose" lists into a single **Later** section, deduplicated. The bowling-alley item is now done.

**Steps:**
- [ ] Write `test_real_data.py`; run it (`run-tests.cmd tests/carousel/test_real_data.py`). Fix any real-data failure here only if it's a game bug. A data gap goes on the Track B list instead.
- [ ] Update the roadmap.
- [ ] Full backend suite and the frontend build, both green.
- [ ] Commit: `Real-data smoke run across opening, travel, venues, Omens and residents; roadmap brought up to date`.

---

## Left out on purpose

- **Data fixes in `E:\Carousel`.** Omen parcels, venue topology reviews and map approval are Track B, listed in the roadmap by Task 6.
- **Engine-tracked items for item-bound Omens,** and an engine clock trigger for special Omens (e.g. OM-032 at 19:23). The GM gets the condition as a lead. Wiring `clock` triggers is worth doing only if playtests show the GM misses them.
- **The "Later" features from Plans 6–8:** player map notes, GM tools, alternate-character handoff, the Damsel's rescue packet, locked and evolving tickets, poster and venue art, and per-character venue positions.
- **Renaming internal identifiers** that only developers see (`app.state.crawler`, the `CRAWLER_` env scrub prefix, the `crawler:announce` event name). They change nothing for play.
