# Carousel game — project roadmap

**Date:** 2026-10-31
**Spec:** [docs/2026-10-30-carousel-game-design.md](../../2026-10-30-carousel-game-design.md) (approved 2026-10-31)
**Owner decision 2026-10-31:** the GM runtime stays as designed: Codex CLI for mechanics/Omen turns, Gemini free tier for quiet turns. Codex's tight weekly credits affect asset work, not play.
**Updated 2026-11-02:** Phases 1–9 are built. What remains is Track B data work in `E:\Carousel` and the Later list below.

The design describes eight build phases (§14). Each is too big and too dependent on code that doesn't exist yet to plan in detail today. So this roadmap fixes the order, what each phase delivers and how they connect. Each phase gets its own detailed plan, written when the previous one lands, against the real code.

## Where things stand

| Input | State | Effect on the build |
|---|---|---|
| Approved design | Done | Build can start. |
| Panel map export | **Done**: 144/144 accepted, 770 web tiles, 12000² master (`assembled_map/export_certificate.json`, 2026-10-31). Not human-approved yet. | The "start only after the map is complete" gate is met. |
| Map audit fixes | **Installed** (2026-11-02): routes to the Cold Case Museum (CAR-014) go through City Hall (CAR-003); the bowling alley is `AUD-LOC-BOWLING-ALLEY` at [458.75, 543.33]. | Open items are listed under "Track B: open data items". |
| Asset reconciliation | **Installed** (2026-11-02): `E:\Carousel\Asset_Reconciliation\game_reference.json` is an import source; it is the authority for art, asset links, reconciled Omen places and venue patches. | Re-import after each reconciliation pass. |
| Crawler Console (`E:\dccrpg`) | Clean working tree, history on `origin` (drakesavage/dccweb). | Ready to fork. |

## Map edits: how the game copes with a map that's still changing

The audits will keep changing place data and some panels. The game is built so that those changes are a **re-import, not a code change**:

- All Carousel source data is read from `E:\Carousel` into a separate, rebuildable `data/reference.db`, never edited by hand inside the game. Campaign state stores only stable IDs (`CAR-…`, `OM-…`, `CAR-NPC-…`, role and ticket IDs).
- Re-importing builds a new reference DB beside the old one, validates it, and swaps it in only if every ID that a saved campaign uses still exists (directly or through an alias). Otherwise it refuses and lists the missing IDs, leaving the old DB in place.
- Map tiles are served straight from `Illustrated_Map_Production/web_tiles/`, so a re-exported panel shows up on reload.
- The game imports **installed** data only, never staged repair candidates (design §10.1).

## Phases

| # | Plan | Delivers (working, testable) | Depends on |
|---|---|---|---|
| 1 | **Done.** Foundation: fork, strip DCC, import Carousel data. [Plan 1](2026-10-31-carousel-plan-1-foundation.md) | `E:\CarouselGame` runs on its own port with no dice or DCC modules. A re-runnable importer builds `reference.db` from every Carousel package. GM-only reference endpoints; tiles served. | none |
| 2 | **Done.** Characters and tickets. [Plan 2](2026-10-31-carousel-plan-2-characters-tickets.md) | Five stats + Plot Armor; 13 starting profiles; ticket ownership/equip/uses ops; `use_trope` op; the per-turn trope briefing (full ticket text for everyone present); rulings for vague tickets. | 1 |
| 3 | **Done.** Maps and travel. [Plan 3](2026-10-31-carousel-plan-3-maps-travel.md) | World map + Leaflet panel tile map; knowledge-gated pins; Dijkstra over `travel_graph` with every `requires` flag; clock advance by distance; interruptions decided at route time; arrival establishing shot with GM call started on click. | 1, 2 (party position) |
| 4 | **Done.** Omen and story runner. [Plan 4](2026-10-31-carousel-plan-4-omens-stories.md) | Omen lifecycle, story locks, Plot Cycle phases as engine events, On/Off Screen, sealed truth GM-only, ending profiles, ticket-driven screenplay revisions, rewards and story history. | 2, 3 |
| 5 | **Done.** New-game flow. [Plan 5](2026-10-31-carousel-plan-5-new-game.md) | Lure, car conversation, Silas assigning Archetypes from that conversation, opening Omen, escort, Lodge payoff. | 3, 4 |
| 5.5 | **Done.** Venues (sub-maps). [Plan 5.5](2026-11-01-carousel-plan-5-5-venues.md) | Import the 108 venue maps from `E:\Carousel\sublocations` (zones, zone links, which zone each Omen sits in) into `reference.db`, read-only. The group has a current zone inside a venue and moves between zones only along the venue's links. Omens are sighted per zone. Entering a place on the town map puts the group at its venue's entry zone. Venues are shown as text (zone list, exits, what's visible) until approved art exists. Story overlays (locked doors, added rooms) are logged events on top of a clean copy of the venue. Rules: Addendum SM-01 v0.1, where it agrees with the design. | 3, 4 |
| 6 | **Done.** Death and fate. [Plan 6](2026-11-01-carousel-plan-6-death-and-fate.md) | Five separate fate tracks, survivor-restores-all, missing-poster board, Death Spectator, rescue eligibility, new group in the same town. Death records the zone (Addendum SM-01 §10). Camp-only save points. | 4, 5.5 |
| 7 | **Done.** Camp Dyer and residents. [Plan 7](2026-11-01-carousel-plan-7-camp-and-residents.md) | 70 residents imported per campaign, hangout schedules around the safer places (zone-level where a venue exists), recruitment into stories, reactive colour on return. | 3, 4, 5.5 |
| 8 | **Done.** Interface pass. [Plan 8](2026-11-01-carousel-plan-8-interface.md) | All screens from design §12 in the oxblood/gilt direction, including venue art where approved. | all |
| 9 | **Done.** Loose ends. [Plan 9](2026-11-02-carousel-plan-9-loose-ends.md) | Area Omens interrupt at their anchor; item and special Omens reach the GM as leads; story-only venues are entered through their story; rule search over the Carousel bibles; no DCC wording sent to models; dead DCC code removed; a smoke run on the real data. | all |

Phases 6 and 7 are independent of each other once 5.5 lands, and can run in parallel. Plan 5 does not depend on 5.5: the opening works at the place level, and zones layer on top of it later.

### Phase 5.5 inputs (surveyed 2026-11-01)

- **Source:** `sublocations/Carousel_Codex_Submap_Package_v1.1/Carousel_Codex_Submap_Spec_v1.json`: 108 venues, 718 zones, 648 zone links. 61 are `persistent`, 40 `conditional`, 5 `story_instance` and 2 `extradimensional`. The venues cover 142 places and 103 Omens. Rules: `Carousel_Submap_Mechanics_Rules_Addendum_v0.1.md` and `Carousel_Submap_Runtime_Rules_Contract_v0.1.json`, whose 20 SMR test cases are pinned as tests where they apply.
- **Fit with the reference data:** all 142 member place IDs resolve. 119 of 120 Omen placements use the same Choice action as the Omen data; the exception is OM-110, which has a Choice action in the sub-maps and none in the Omen data. The Omen data stays the authority.
- **Not ready yet:**
  - All 216 entries and exits are `requires_parent_map_matching`.
  - 50 Omen placements are `NEEDS_MANUAL_NODE_REVIEW`; 70 are design recommendations only.
  - 13 venues have no member place, including the bowling alley (SM-086), Look Back alley (SM-094) and Turnpike gate (SM-108).
  - Art exists for 53 of 108 venues, as unapproved studies.
  - `release_ready: false`; no runtime tests run. Production is ongoing (Codex), so the plan is written against the files as they stand when the phase starts.
- **Binding rule:** a venue with member places gets its entry and exit at those places' town-map nodes, which the import derives. A venue with no member place stays unreachable until Track B gives it a place. An Omen marked for manual review is placed at the venue's entry zone, never at its suggested zone.
- **Smaller option kept in reserve:** list each venue's zones in the GM's briefing as description only, with nothing enforced by the engine.

## Track B: map data fixes (outside the game repo)

These are edits to `E:\Carousel` source data. The game picks them up by re-importing (`import-data.cmd`); none needs game code.

1. **Cold Case repair: done.** Checked 2026-11-02 on the installed data: the route from the Lodge to the Cold Case Museum (CAR-014) ends `… → CAR-003 (City Hall) → CAR-014`.
2. **Bowling alley: done** as `AUD-LOC-BOWLING-ALLEY` (record and position). The Bowlers' hangout uses it.
3. **Possible gaps** (design §10.1: marina, catacombs, sewers, train station, …): add records only as source review confirms them.
4. **Owner review of the panel map:** still `human_approved: false` in `assembled_map/export_certificate.json`. Visual only, so it doesn't block play.

### Track B: open data items

From the import report and the real-data smoke run (`backend/tests/carousel/test_real_data.py`, 2026-11-02). These are Codex/Claude data jobs in `E:\Carousel`, not game code.

- **22 playable Omens waiting for a parcel** (their place has no coordinates, so they have no pin and never surface): OM-007, OM-008, OM-017, OM-044, OM-048, OM-049, OM-058, OM-060, OM-061, OM-062, OM-063, OM-064, OM-065, OM-068, OM-077, OM-078, OM-079, OM-080, OM-081, OM-082, OM-083, OM-100. A 23rd, OM-052, also has no parcel but is reachable through its story venue (SM-096). The other 70 reach play: 57 through the engine, 13 as GM leads.
- **12 venues waiting for a reviewed runtime graph** (import report: "Production source/topology correction requires reviewed runtime graph"), plus 1 whose parent-map repair is unverified.
- **11 venues with no member place on the travel graph:** SM-079, SM-088, SM-089, SM-090, SM-091, SM-092, SM-093, SM-094, SM-095, SM-097, SM-108.

## Corrections to the design found while planning

- Design §5 says 19 Omens link by `Gazetteer ID` and 35 by `Map Anchor`. The current registry gives **34 by ID and 59 by anchor name; all 93 playable Omens resolve.** 48 of those anchors name a district (35) or the Town Square (13) rather than a building, so the Omen sits somewhere in that area. Spawn mode comes from `Spawn / Timing`: **49 fixed visible, 24 roaming, 15 item-bound, 5 special** (time-of-day, event, timed gate). Plan 1 pins these counts.

## Later

Left out on purpose by Plans 1–9 and not built since. Each is worth its own small plan once playtests show it is needed.

- **Stories and rewards:** one-of-three ticket reward offers in the UI; difficulty credit scaled automatically for overlevelled characters; the mystery-graph proposition states (unknown, suspected, supported, confirmed) as tracked data.
- **Omens:** engine-tracked items for item-bound Omens, and clock triggers for time-of-day and special Omens (e.g. OM-032 at 19:23). The GM gets both as leads today.
- **Fate:** alternate-character handoff (Bible §11.4) and the `locked` rescue value; the Damsel's `spawn_rescue_packet`; the "Have You Seen Them?" rescue that starts a story without an Omen; moving a dropped item to the body's zone; enforcing the Deathwatch firewall in memories.
- **Residents:** residents acting on their own (off-screen stories, deaths, schedule drift); legal tickets for original residents; faction mechanics.
- **Venues and maps:** per-character positions inside a venue (the group moves as one); drawing submaps (odyssey, sanctuary, terradomes, …) beyond the town panel map.
- **Interface:** player notes and suspicion markers on the map; GM tools (Omen anchor editor, reveal-as-player); evidence with provenance and confidence; locked, undiscovered and evolving tickets; story poster art and venue art; new sound cues.
- **Housekeeping:** renaming internal identifiers only developers see (`app.state.crawler`, the `CRAWLER_` env scrub prefix, the `crawler:announce` event name).
