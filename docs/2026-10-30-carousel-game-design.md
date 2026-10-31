# Carousel — game design

**Date:** 2026-10-30
**Status:** Approved by the owner 2026-10-31. Planning only: the build starts after the illustrated panel map is finished.
**Owner:** Cliff (single player; possibly his brother later)

---

## 1. Intent

Carousel is a single-player, AI-game-mastered horror roleplaying game set in the town of Carousel from Rob M. Lastrel's *The Game at Carousel* series. You play a newcomer who has been lured into a town where horror movies happen for real. You live at Camp Dyer with an established community of trapped players. You explore the town on an illustrated map, find Omens, choose whether to enter their stories, and survive them using your Archetype, stats and trope tickets.

**Why:** the owner already has a working AI-GM game, Crawler Console (`E:\dccrpg`), for a *Dungeon Crawler Carl* campaign. He wants the same kind of experience for Carousel: more narrative, less dungeon crawl. A large body of Carousel material has already been produced for it (rules bibles, a town atlas, 201 location illustrations, an illustrated town map, an Omen registry with story beats, and a camp NPC database).

**What success looks like:** you can start a new game, be lured into town, get your tickets from Silas, survive (or not) an opening Omen, reach Dyer's Lodge, then spend an open-ended campaign clicking around the illustrated town, finding Omens, recruiting camp residents and playing through stories. The game should feel like a horror movie that knows you're watching, without lying about the rules.

This is a personal project for the owner's own play; it is not intended for distribution.

## 2. Goals, non-goals, anti-goals

**Goals**
- Reuse Crawler Console's proven engine: AI GM orchestration, validated state changes, GM-only secrets, memory, save points.
- **Storytelling, not dice.** There are no dice rolls. Outcomes are decided narratively by the GM from the fiction, the characters' stats and Plot Armor, and their tropes (owner decision; see §7.1).
- Movement by clicking on the illustrated panel map, with travel time, encounters along the way and an immediate described arrival.
- Stories driven by Omens: high-level beats from the Omen data, heavy improvisation, and trope tickets that can legitimately rewrite a story.
- Book-faithful death rules (one survivor restores everyone; total wipe → missing posters).
- An open-ended campaign in a persistent town.

**Non-goals (for the first version)**
- An endgame or "escape Carousel" arc. Leave room for it; build none of it.
- Multiplayer. Design state so a second group can be added later (see §11), but build single-player only.
- A shared engine library with Crawler Console.
- Generating new art. Location images and the map are final, apart from one-off fixes.

**Anti-goals (it fails even if it technically works when…)**
- The AI's narration silently changes state: every change to stats, tickets, fate, story phase, position or time must go through a logged engine op, even though the GM decides outcomes narratively.
- The GM ignores a trope that is in play, or applies one beyond its written text (e.g. forgets Trope Master halves the Film Buff's Plot Armor).
- Stories run on rails, as scripts the player can't deviate from.
- Movement is driven by the artwork rather than the travel graph (walking through buildings or across water because the painting looks open).
- Spoilers or GM-only truth leak into anything the player sees.
- Crawler Console's DCC game is broken or changed in any way.

## 3. Constraints

- **Crawler Console must keep working untouched.** The owner still plays it.
- **Start only after the panel map is complete.** At the time of writing, 121 of 144 panels have been generated and 104 technically accepted. `Illustrated_Map_Production/scripts/export_map.py` refuses to export until all 144 are accepted. The game consumes that export.
- **Source material under `E:\Carousel` is read-only to the game.** The game reads data and art from it; it never writes into those folders.
- **AI runtime:** Codex CLI (owner's sign-in, no API billing) and Gemini free-tier quota, both already supported by Crawler Console's provider router.
- **Windows desktop, local only.** Python 3.10+, FastAPI, SQLite, React/TypeScript, as in Crawler Console.
- **Source precedence** (from the bibles themselves): System Bible v0.4.1 for gameplay contracts and stats; Role Bible v0.4.2 for role definitions; Trope Bible v0.1.1 for canonical trope identity; Storyline Bible v0.1.1 / Production Standard v0.2 for episode authoring; Omen engine data v0.2 plus Story Beat Bible v0.3 for Omens.
- **Everything the owner supplied is fair game** for the GM, including later web-serial material. No spoiler holdback beyond in-game player knowledge.

## 4. Approach

Three ways were considered:

| Approach | Trade-off |
|---|---|
| **A. Fork Crawler Console into a new project and rework it** (chosen) | Fastest to a working game; DCC game untouched. Code bases drift apart; fixes can be cherry-picked because history is shared. |
| B. Extract a shared core used by both games | Fixes help both, but requires refactoring the live DCC game, and the two games' needs diverge (combat/floors vs stories/camera). |
| C. Write a new app, borrowing patterns only | Cleanest, but rebuilds weeks of solved runtime work (Codex isolation, staged commits, idempotent turns, leak tests). |

**The owner chose A:** "no need to reinvent the wheel or over complicate things trying to be too clever."

### 4.1 What the fork keeps, removes and adds

Clone `E:\dccrpg` with its git history into a new folder (proposed `E:\CarouselGame`). It gets its own port, `data/` directory, database, Codex job folder and GM token, so both apps can run at once.

**Keep (Carousel-agnostic machinery)**
- `gm/orchestrator.py`: turn lifecycle, idempotency keys, staged tool rounds, replay-with-same-dice atomic commit.
- `gm/providers.py`, `gm/router.py`: Codex and Gemini providers and turn routing (conversation turns on Gemini, mechanics on Codex, `escalate`).
- `gm/tools.py` framework and `rules/engine.py` op pattern (pydantic op + `_apply_*`, immutable events).
- `memory/` (context builder, views/visibility boundary, snapshots, summaries, journal, NPC memories), `backup.py`, `access.py`.
- `art/` pipeline (location images come pre-made; portraits may still use it).
- Visibility tests that plant secrets and crawl every player endpoint.

**Remove (DCC-specific)**
- All dice: `rules/dice.py`, `rules/checks.py`, the dice audit, and the orchestrator's replay-with-same-dice (the staged atomic commit itself stays). Also `combat.py`, `crafting.py`, `traps.py`, floor clock and dungeon-day logic, AI-favor, loot boxes and achievements as DCC defines them. The System Bible §7 probability model is **not** used.
- `importer/ledger.py`, `ledger/` write-back, `reconcile.py`, the DCC constitution, the PDF rulebook index content.
- The DCC broadcast theme.

**Add (Carousel)**
- Data importers for the Carousel packages (§5).
- Carousel rules: five stats and Plot Armor, Archetypes/Aspects/Advanced tracks, trope ticket bookkeeping (§7.1). No checks or dice.
- Map and travel services (§6).
- Omen and story runner, Plot Cycle, On/Off Screen (§7).
- Death and fate states, missing-poster board (§8).
- New-game flow: lure, Silas, opening Omen, escort to the Lodge (§9).
- Camp Dyer hub and recruitment (§10).
- A new interface (§12).

The rulebook search (FTS5) can be repointed at the Carousel bibles so the GM can cite them; that reuse is the builder's call.

## 5. Data sources

All paths relative to `E:\Carousel`.

| Data | Source | Notes |
|---|---|---|
| Town places (191) | `Illustrated_Map_Production/game_handoff/locations.json` | `[north, west]` coordinates in 0–1000; spoiler flags, `requires` gates, confidence fields. |
| Full gazetteer (234, incl. submaps) | `map/data/gazetteer.json` | Submaps: odyssey, sanctuary, terradomes, historical, etc. Preserve every CAR ID including aliases and rejected records. |
| Roads, regions, travel graph | `Illustrated_Map_Production/game_handoff/{roads,regions}.geojson`, `travel_graph.json` | **Movement authority.** Edges and destinations carry flags; adjacency never implies permission. |
| Panel map art | `Illustrated_Map_Production` export (`web_tiles/`, `assembled_map/lossless_panels/`, `game_handoff/illustrated_map.json`) | 12000² master, six-level 512px WebP pyramid. Exists only after completion. |
| World map (overview) | `map/art/carousel-north-up-base-6000.png` / `-2000.webp` or the 3000² overview from the panel export | Builder picks whichever reads best as the zoomed-out view. |
| Location images (201 + interiors/variants) | `Location_Art_Production/assets/`, `data/review_reference_index.json` | Final. Approval status in the studio DB is not a gate for the game. |
| Omens (111 records; 93 playable) | `omen/Carousel_Omen_Engine_Data_v0.2.json` | **Ignore its Map X/Y fields** (older reconstruction with a different axis convention). Position comes from the linked gazetteer place: 19 via `Gazetteer ID`, 35 by matching `Map Anchor` to a gazetteer name; persist the matched IDs at import. 24 roam, 15 are item-bound. |
| Story beats | `omen2story/Carousel_Omen_Story_Beat_Bible_v0_3.json` and crosswalk | High-level hooks/escalation/climax and ending requirements per story. |
| Roles | `roles/Carousel_Role_Bible_v0_4_2_Implementation.json` | 71 roles, 81 homebrew ticket IDs; follow its Claude handoff (11-point starting profiles, base PA 11). |
| Tropes | `tropes/Carousel_Trope_Bible_Data_v0.1.1.json`, Role Bible tickets | 627 names. Ticket **text** is what the GM applies (§7.1). The Trope Engine's typed-predicate executor is not used. |
| Rules | `system/Carousel_System_Bible_v0.4.1.md` | Gameplay contract, checks, death protocol, rewards. |
| Storyline authoring | `storyline/` | Episode packet structure, ending profiles. |
| Camp NPCs (70) | `npcs/Carousel_Camp_NPC_Package_v1/Carousel_Camp_NPC_Database_v1.json` | 23 canon-expanded, 47 original. `gm_only` never reaches player-visible output. Excludes Riley's group and other same-day arrivals. |
| Interface direction | `interface/` | Mockups are a loose direction only, freely changeable. |

Coordinate rule everywhere: stored `[north, west]`; image pixels `x = (1000 − west) × W/1000`, `y = (1000 − north) × H/1000`; north-up display.

## 6. Maps and movement

**Two maps, two jobs.**
- **World map:** a zoomed-out reference of the whole town. Shows your position, places you know, your current route. Clicking it jumps the panel map to that area. You never travel from it.
- **Panel map:** the illustrated 12000² town, shown as a zoomable tile map. This is where you move.

**Clicking to travel.** You click a known place or a point on a road. The server plans a route over `travel_graph.json` (Dijkstra; respect every edge and destination flag; conditional routes need story flags). Your party marker animates along the route. The game clock advances by distance. Only known places show as pins (knowledge-gated per the UI doc); undiscovered places exist in data but are not marked.

**Interruptions are deterministic and decided up front.** When the route is planned, the engine also decides whether anything interrupts it: approaching a fixed Omen's place makes it visible as an inspectable anomaly; a roaming Omen may intercept on the road. An interruption stops travel at that point. Seeing an Omen is always safe; a story only starts on the Omen's committed Choice action (e.g. `cross_threshold:corn_maze_entrance`).

**Arrival is always described** (owner requirement). To hide model latency:
- The GM call starts the moment you click, because route, time and interruptions are already known and applied.
- The marker animation plays, then a cinematic cut to the place's identity image (the "establishing shot"); narration fades in over it. If the GM is still working, the shot simply holds.
- Long trips can start from the world map and zoom into the panel map.
- Quiet arrivals are description-only and can go to Gemini; Omen interruptions go to Codex.

## 7. Omens and stories

**Omen lifecycle** (from the Omen engine policy): hidden → visible → examined → Choice accepted → casting/Party → active beats → concluded (win/partial/fail). Hubs never open a Plot Cycle themselves; they pick a child Omen or an original seed.

**Story runner.** The engine holds a story's fixed points; the GM improvises everything between them.
- The engine tracks the current Plot Cycle phase (Omen → Choice → Party → First Blood → Rebirth → Second Blood → Finale → The End), the story's genre and shape from its beat entry, its ending requirement, its sealed truth and protected facts, and On/Off Screen camera state.
- **Beats are direction, not a script** (owner requirement: "high level enough that they leave room for a lot of improvisation, especially since tropes can change stories pretty dramatically").
- **Tropes can rewrite the story**, but only through tickets. Each ticket's allowed screenplay operations come from the Role/Trope data; using one creates a logged screenplay revision against the current story version and can change beats, ending conditions or protected facts. The GM never rewrites the story on its own authority.
- Phase changes are validated engine events; the UI's Plot Cycle bar and camera indicator read the engine, not the narration.
- Each story's sealed truth (antagonist, mechanism, clue solutions) is stored as GM-only and never appears in player views. The beat bible already provides a sealed truth, clue routes and a Choice trigger per Omen.
- Stories need ending profiles at minimum `full_success`, `costly_success`, `survive_without_victory`, `defeat` (Storyline Bible). Where the beat bible lacks them, the GM drafts them at Choice time and they are sealed before play continues.

**Stories are replayable.** Nothing is "used up". At most one active instance of each story lock exists at a time. Replays earn little Novelty; confirmed spoilers reduce Novelty further (System Bible §10). The world keeps a **story history** (who ran what, when, outcome, what was learned) that feeds Novelty, Atlas entries and camp gossip.

### 7.1 Tropes and outcomes without dice

Owner decision: "the game is storytelling, not mechanical like DCC. There are no dice rolls. Every trope should work because it should be built into the info given to the LLM for every turn."

This is consistent with the books, which show stat and Plot Armor comparisons and narrative status changes rather than rolls (System Bible §3 notes there is no canonical d20 or HP).

- **Every turn's GM briefing includes the full ticket text** for every trope owned by characters present in the scene, with equipped/ready state, uses left and drawbacks; plus the stats and current effective Plot Armor of everyone present; plus any enemy tropes the player has legitimately revealed (e.g. via Trope Master), and the hidden enemy tropes in the GM-only section. Only tickets in play are included, not the whole 627-entry bible.
- **The GM decides outcomes narratively** from the fiction, stats, Plot Armor and tropes. Higher relevant stats and Plot Armor make success and survival more likely; tropes do what their text says.
- **The engine still keeps the books.** Ownership, equipping, uses/cooldowns, Plot Armor changes, status and fate changes, and story revisions are engine ops. When a trope fires, the GM calls a `use_trope` op naming the ticket and its effect, so uses are spent and the chronicle shows it. A trope that rewrites the story records a screenplay revision (§7).
- **Incomplete ticket text:** where a Trope Bible entry is vague or flagged, the GM interprets it in the spirit of its text and records the interpretation as a ruling, so later turns stay consistent. Rulings are visible to the owner and can be corrected.

**Rewards** follow System Bible §10: star ratings on Novelty, Performance, Difficulty; five stars → one stat point; tickets and money as awarded; advanced-role marks are a separate track (10 per track).

## 8. Death and fate

Confirmed from the books:
- **If anyone in the story survives to The End, everyone in it is restored** unharmed (*The Bystander* ch. 3). Memories and psychological effects persist.
- **If the whole group dies, they become missing posters** on the board outside the diner (*The Bystander* chs. 10, 38). Posters show name, Plot Armor, Archetype, last seen.
- **Rescue** of postered players was once possible through rescue tropes, which vanished years before the books (*The Invitation* ch. 3); a rescue-type ticket returns later (*The Atlas* ch. 33). Rescue is possible but rare, and can fail.
- **Vanishing without a poster** happens through specific rules (e.g. trying to leave town). That is permanent loss.

**Implementation**, per System Bible §11: track `physical_condition`, `red_wallpaper_status`, `participation_rights`, `persistent_fate` and `rescue_eligibility` separately. "The group" means everyone who entered that story, including recruited camp residents. Resolve the ending first, then restoration.

**After the player dies mid-story,** the Death Spectator view lets the player watch the rest. Survivors act on their own knowledge and goals, judged with the same narrative standard as before the death; no ghost puppeteering, no convenient rescues or forced tragedies (System Bible §11.3–11.5, minus the dice).

### 8.1 Save points

Owner decision (agreed):
- Save points exist **only at camp**, never mid-story.
- Restoring rolls back **everything**, world and group together (single-player). A wipe still has weight: restoring loses everything since the last return to camp.
- Revisit when a second player joins, together with shared-clock design (§11).

**If the player's whole group is postered or permanently lost,** the player creates a new group, which is lured into the same persistent town. Old posters stay on the board; residents remember; a later group can attempt a rescue.

## 9. New game: lure, arrival, Silas

Modelled on *The Bystander* chs. 1–7, adapted. Per the NPC package, the player's group arrives *instead of* Riley's group.

1. **The lure.** There is always a lure. The GM chooses it: a relative or friend already trapped at camp, an event like the horror convention, or something new. The lure is saved as a fact. If it's a person, they are a camp resident (one of the 70, or a new one the GM adds). The newcomers' belief about the lure is player-visible; the truth behind it is GM-only. It pays off on arrival at the Lodge and can recur later.
2. **The game opens in the car** (owner decision). The GM creates the three friends to fit the lure and introduces everyone through conversation on the drive, the way a horror movie's opening establishes its cast. The player takes part in that conversation as their own character. The player can adjust or veto a friend before the conversation gets going *(Claude's call)*.
3. **The drive in.** Signal and radio fail; the vast lot of long-abandoned cars; the road blocked, so everyone walks.
4. **Silas the Mechanical Showman** deals each character three tickets: Minor Archetype with its 11-point stat profile, a signature trope and a contextual starter ticket (System Bible §4, Role Bible v0.4.2). **Silas picks each Archetype from what came out of the car conversation** (owner decision): who acted like the Film Buff, the Final Girl, the Skeptic, and so on. The GM proposes each assignment with its reason; the engine validates it against the 13 starting roles and records the reason and the evidence from the conversation. The contextual starter ticket is chosen the same way. This replaces System Bible §4.1's scoring rubric.
5. **The opening Omen.** The group is pulled into a story almost immediately.
6. **The escort.** Veterans walk the group across town to Dyer's Lodge, dodging Omens.
7. **The Lodge.** The community welcomes them; the lure pays off.

## 10. Camp Dyer and the campaign

- **Open-ended.** No endgame in the first version. The GM must not invent answers about the town's nature or a way out that would constrain a future endgame.
- **Dyer's Lodge** is the safe hub. The 70 residents are the pool for recruitment into stories, alongside your three friends.
- **The residents live in the whole town, not just the Lodge** (owner decision: they "should make the town feel more alive"). Each resident has a few hangouts among Carousel's safer places. Where a resident is at a given time comes from a simple schedule (home base + hangouts by time of day), so you can run into them around town.
- **Safer places come from the book text**, not from guesswork. Established so far:

  | Place | ID | What the books say |
  |---|---|---|
  | Dyer's Lodge / Camp Dyer | CAR-047 | Main player hub; out of town, so a refuge even during town-wide Apocalypse events (*The Atlas* ch. 6). |
  | Town Square | CAR-002 | A known safe zone players head to between stories (*The Bystander* ch. 25). |
  | The Diner | CAR-006 | Players treat it as safe for meetings (serial); the missing-poster board is outside it. |
  | Bowling alley | in the 2026-10-31 reconciled registry only (no gazetteer ID yet) | Safe only because a veteran team, the Bowlers, clears it of Omens on a timed plan so they can spend the day there (*The Bystander* chs. 32, 40; *The Atlas* ch. 4; serial Book Six ch. 66). Safety here is earned, not built in. West-central, a block from the flea market (CAR-035) (serial Book Five ch. 63). The rooftop sign has been torn off; "Memory Lanes" is only a character's guess. |
  | Sanctuary neighborhood | CAR-230 (sanctuary submap) | A separate neighborhood of meta-aware NPCs with no storylines (serial). |

  The runtime should also honour two book rules: a group can't trigger a new story while one is active, and some stories leave a temporarily safe place after they're beaten (serial).
- **The bowling alley is still missing** from the gazetteer, the town map data and the location art. The 2026-10-31 audit (`locations/20261031-reconciliation-checkpoint/`) found its rough position but no building on the painted panels that can be identified as it. It needs a gazetteer record, an authored position, a location image and a building on the illustrated map. Settle this before the last map panels are finished.

### 10.1 Place data after the 2026-10-31 audit

The audit in `locations/20261031-reconciliation-checkpoint/` (report at `locations/report.md`) extends the inventory to a 302-row registry and stages, but does not install, one data repair. **The game imports place data only after these fixes are installed into the gazetteer and travel graph**, not from the staged candidates.
- **Cold Case Museum (CAR-014)** is in City Hall's basement, not a separate building (serial Arc II chs. 28–29). Arrival goes through City Hall. A repair is staged in `repair_packages/cold_case`.
- **New records** include the Strings Attached mansion in Carousel Hills and its rooms, a clock tower near Town Square (it may exist only in some story states), Mayor Morrow's offices, and others.
- **Possible gaps neither audit has settled** (Claude's spot check of its own partial name scan against the registry):
  - the fairgrounds near a church (*The Bystander* chs. 35–36), unless it is the existing carnival record;
  - the Lake Dyer marina and its restaurant (serial chs. 37, 42);
  - a motel across from a park bench, with a trailhead down to the river (serial ch. 71);
  - the catacombs (*The Bystander* ch. 40);
  - the town's sewer network, which the books say is full of storylines (*The Invitation* ch. 9, *The Atlas* ch. 22); only a sewer outlet is recorded;
  - the train station, with its misleading destination map (*The Invitation*/*The Atlas* ch. 2);
  - the asylum and manor on a private property (serial chs. 47, 65);
  - the Historical Society, which runs Halle Castle's museum (*The Bystander* chs. 11–12), as an organisation and possibly an office.
- **The world is otherwise reactive** (owner decision). Residents don't run stories off-screen. On returning to camp or meeting residents around town, the GM may add colour (who went out, rumours), recorded as facts, but nothing is simulated in the background.
- NPCs use Crawler Console's people model: profile, relationship, NPC memories, GM-only companion sheet. Import all 70, with provenance (canon-expanded vs original) preserved. Canon residents keep their archetype, personality core and documented relationships.
- Residents can be at camp or out with a party; party groups can be in different places (existing group/assignment model).

## 11. State: world vs group

Single-player now, but keep these apart from day one so a second group can be added later (the brother would run his own group in the same town, not share a party):
- **World state:** town clock, camp residents and their memories, missing-poster board, story history, active story locks, changes to places.
- **Group state:** characters, party, current story, position, inventory.

How two players share the clock when playing at different times is explicitly deferred.

## 12. Interface

The eight mockups in `interface/` set the tone (oxblood, gilt, ticket stock, letterboxed camera framing, "a movie that has learned the player is watching") but are **freely changeable**: layout, components and copy are up to the builder. Screens needed: scene/story (with camera state and Plot Cycle), world map, panel map, Red Wallpaper (character poster, stats, Plot Armor, tickets), tickets library, location/Omen dossier, chronicle, Death Spectator, missing-poster board, Lodge/people, saves & settings. The mockups' example fiction (*The Town That Remembers*, Mara/Jonah/Evelyn, sample tickets) is placeholder, not canon.

## 13. Left to the builder

- Folder name, port numbers.
- Which overview image serves as the world map.
- Tile-map library (Leaflet with CRS.Simple is already used by the atlas).
- Animation and transition styling.
- Exact travel speed and how time maps to distance.
- Whether to repoint FTS rulebook search at the bibles.
- How to phase the build (§14 is a suggestion, not a fixed order).

## 14. Suggested phase order

*Builder's call (Claude's, not the owner's): the new-game flow depends on travel and the story runner, so those come first rather than stubbing the opening with throwaway scripts.*

1. Fork, rename, isolate (port/data/token); strip DCC modules; keep tests that still apply green.
2. Carousel data import and validation (places, graph, Omens with resolved anchors, roles, tropes, NPCs).
3. Characters, stats, Plot Armor, ticket bookkeeping and the per-turn trope briefing.
4. Maps: world map, panel tile map, click-to-travel, interruptions, arrival transition.
5. Omen/story runner, Plot Cycle, camera, ticket-driven screenplay revisions, rewards.
6. New-game flow: lure, Silas, opening Omen, escort, Lodge (built on 4 and 5).
7. Death/fate, Death Spectator, missing-poster board, new-group continuation.
8. Interface pass across all screens.
