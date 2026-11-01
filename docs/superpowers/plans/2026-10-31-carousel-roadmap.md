# Carousel game — project roadmap

**Date:** 2026-10-31
**Spec:** [docs/2026-10-30-carousel-game-design.md](../../2026-10-30-carousel-game-design.md) (approved 2026-10-31)
**Owner decision 2026-10-31:** the GM runtime stays as designed: Codex CLI for mechanics/Omen turns, Gemini free tier for quiet turns. Codex's tight weekly credits affect asset work, not play.

The design describes eight build phases (§14). Each is too big and too dependent on code that doesn't exist yet to plan in detail today. So this roadmap fixes the order, what each phase delivers and how they connect. Each phase gets its own detailed plan, written when the previous one lands, against the real code.

## Where things stand

| Input | State | Effect on the build |
|---|---|---|
| Approved design | Done | Build can start. |
| Panel map export | **Done**: 144/144 accepted, 770 web tiles, 12000² master (`assembled_map/export_certificate.json`, 2026-10-31). Not human-approved yet. | The "start only after the map is complete" gate is met. |
| Map audit fixes | **Not installed.** Cold Case → City Hall repair is staged in `audits/20261031-reconciliation/repair_packages/cold_case`. The bowling alley has no gazetteer record, position, image or painted building. Registry status `WORK_REMAINS`. | Doesn't block the build (see "Map edits" below). |
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
| 1 | **Foundation**: fork, strip DCC, import Carousel data. [Detailed plan](2026-10-31-carousel-plan-1-foundation.md) | `E:\CarouselGame` runs on its own port with no dice or DCC modules. A re-runnable importer builds `reference.db` from every Carousel package. GM-only reference endpoints; tiles served. | none |
| 2 | Characters and tickets | Five stats + Plot Armor; 13 starting profiles; ticket ownership/equip/uses ops; `use_trope` op; the per-turn trope briefing (full ticket text for everyone present); rulings for vague tickets. | 1 |
| 3 | Maps and travel | World map + Leaflet panel tile map; knowledge-gated pins; Dijkstra over `travel_graph` with every `requires` flag; clock advance by distance; interruptions decided at route time; arrival establishing shot with GM call started on click. | 1, 2 (party position) |
| 4 | Omen and story runner | Omen lifecycle, story locks, Plot Cycle phases as engine events, On/Off Screen, sealed truth GM-only, ending profiles, ticket-driven screenplay revisions, rewards and story history. | 2, 3 |
| 5 | New-game flow. [Detailed plan](2026-10-31-carousel-plan-5-new-game.md) | Lure, car conversation, Silas assigning Archetypes from that conversation, opening Omen, escort, Lodge payoff. | 3, 4 |
| 5.5 | Venues (sub-maps) | Import the 108 venue maps from `E:\Carousel\sublocations` (zones, zone links, which zone each Omen sits in) into `reference.db`, read-only. The group has a current zone inside a venue and moves between zones only along the venue's links. Omens are sighted per zone. Entering a place on the town map puts the group at its venue's entry zone. Venues are shown as text (zone list, exits, what's visible) until approved art exists. Story overlays (locked doors, added rooms) are logged events on top of a clean copy of the venue. Rules: Addendum SM-01 v0.1, where it agrees with the design. | 3, 4 |
| 6 | Death and fate | Five separate fate tracks, survivor-restores-all, missing-poster board, Death Spectator, rescue eligibility, new group in the same town. Death records the zone (Addendum SM-01 §10). Camp-only save points. | 4, 5.5 |
| 7 | Camp Dyer and residents | 70 residents imported per campaign, hangout schedules around the safer places (zone-level where a venue exists), recruitment into stories, reactive colour on return. | 3, 4, 5.5 |
| 8 | Interface pass | All screens from design §12 in the oxblood/gilt direction, including venue art where approved. | all |

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

These are edits to `E:\Carousel` source data. The game picks them up by re-importing. None blocks Phases 1–3. Phase 7 needs the bowling alley, and Phase 5.5 can only reach the 13 venues that have no place (SM-086–097, SM-108) once they get one.

1. **Install the Cold Case repair.** The candidate gazetteer and travel-graph JSON are already built and validated (7 structural + 5 routing checks). This is a data edit, not new art, so Claude can do it without Codex. Do it before Phase 3 so routing to the museum goes through City Hall.
2. **Bowling alley:** gazetteer record + authored position near the flea market (CAR-035, [425, 570]). The painted building and the location image need image generation, which is Codex's job when credits allow. Until then the record can exist with a placeholder image and no painted building.
3. **Possible gaps** (design §10.1: marina, catacombs, sewers, train station, …): add records only as source review confirms them.
4. **Owner review of the panel map** (`human_approved: false`). Visual only, so it doesn't block the build.

## Corrections to the design found while planning

- Design §5 says 19 Omens link by `Gazetteer ID` and 35 by `Map Anchor`. The current registry gives **34 by ID and 59 by anchor name; all 93 playable Omens resolve.** 48 of those anchors name a district (35) or the Town Square (13) rather than a building, so the Omen sits somewhere in that area. Spawn mode comes from `Spawn / Timing`: **49 fixed visible, 24 roaming, 15 item-bound, 5 special** (time-of-day, event, timed gate). Plan 1 pins these counts.
