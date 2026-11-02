# Carousel Plan 8: Interface pass — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The game stops looking like Crawler Console. Every screen in design §12 exists in the oxblood/gilt direction of the eight mockups, is bound to real campaign data, and leaks nothing the player hasn't earned: scene (camera state, Plot Cycle), Death Spectator, town map with place dossiers, Red Wallpaper, ticket library, chronicle, Lodge and missing-poster board, saves and settings. The DCC leftovers (dice, combat, floor clock, the Syndicate, inventory and quests tabs) go.

**Architecture:**
- **One theme layer.** `theme-carousel.css` holds the design tokens (UI handoff §4) and replaces `theme-dcc.css`. Fonts: EB Garamond (display and story) and Cabin (controls), from fontsource; JetBrains Mono stays for Behind the Scenes only.
- **The shell drives the mood from engine state only.** `App.tsx` sets `data-camera` (`on`/`off`/`none`), `data-fate` (`deathwatch`/`wiped`/`alive`) and `data-safe` on `<html>`. CSS listens; no component invents camera or fate state.
- **Frontend-only, with two small server additions:** the campaign summary gains the clock (`day`, `time`, `slot`) for the production notes, and a new `GET /places/{id}` serves a place dossier for known places only. Everything else reads endpoints that Plans 3–7 already built and the visibility crawl already covers.
- **No new player powers.** Every button calls an existing endpoint (`turns`, `travel`, `venue/move`, `residents/{id}/talk`, `deathwatch/pace`, `new-group`, `saves`). The interface never changes state on its own.

**Tech Stack:** React/TS (Vite), CSS, lucide-react, Leaflet; FastAPI and pytest for the two server additions. No new frontend libraries beyond two fontsource packages.

**Spec:** [docs/2026-10-30-carousel-game-design.md](../../2026-10-30-carousel-game-design.md) §12, read with the UI handoff `E:\Carousel\interface\CAROUSEL_UI_DESIGN_AND_IMPLEMENTATION.md` (§1, §4–§9, §11) and its eight screenshots (`E:\Carousel\interface\0*.png`, overview in `CONTACT_SHEET.png`). The screenshots set tone and layout; their fiction (*The Town That Remembers*, Mara/Jonah/Evelyn, Film Buff, sample tickets, map layout) is placeholder and must not appear in the game.

## Global Constraints

- **Engine state only.** Camera, Plot Cycle phase, fate, time, place, residents and tickets come from the API. No fixture text, no placeholder names, no invented counts. A missing value renders as an honest empty state ("No story running", "Feature unknown"), never a guess.
- **Nothing GM-only in the bundle or the network.** The frontend still never calls `/api/gm/*` except the existing token-gated Diagnostics panel. New server fields are built from explicit column lists. Unknown places, unseen Omens and unmet residents are absent, not greyed out: no pin, no count, no placeholder slot (handoff §5.3, §6).
- **Feature poster:** the running story's title is already player-visible (story_api). A story's poster art does not exist yet, so the scene's right rail shows a "Feature unknown" card when no story is running and a typographic title card when one is. No poster image is fetched or generated in this plan.
- **Venue art:** none of the 53 venue studies is approved (roadmap, Phase 5.5 inputs). Venues stay text-only; the venue panel gets the new styling and nothing else.
- **Palette rules (handoff §4):** red (`--oxblood`, `--blood`) only for recording, Omen warnings, Deathwatch and danger; brass for navigation and labels; parchment for prose. Pins pair a shape and a text label with colour.
- **Motion:** every transition is ≤ 650 ms, non-blocking and skipped under `reducedMotion()` (prefs.ts). Camera and phase changes are also announced as status text (`aria-live="polite"`).
- **Responsive targets (handoff §9):** ≥ 1440 three columns; 1100–1439 narrower rails, right rail becomes a drawer; 768–1099 single column with toggled rails; < 768 bottom navigation (Scene, Map, Wallpaper, Lodge, More) and a fixed composer. No horizontal scroll at 375 px.
- **The draft survives navigation.** The composer draft is already kept per campaign in `localStorage` (`draft:{cid}`); switching to Map or Wallpaper and back must keep it, and keep the transcript scroll position.
- **Crawler Console untouched.** Nothing in `E:\dccrpg` changes; nothing in `E:\Carousel` is written.
- **Commits:** commit with explicit paths only, end each message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Place dossier leaks (Task 4).** `GET /places/{id}` returns 404 with no body detail for a place the group doesn't know, lists only Omens in `omen_states`, only met residents, and no description for a place flagged `spoiler`. The visibility crawl walks the dossier of every known place.
2. **Camera state is engine-owned (Task 2).** `data-camera` comes from `api.story().active.camera` only. With no active story it's `none`, not `off`.
3. **Spectator mode removes acting (Task 3).** During Deathwatch the composer only asks for cuts, pace or summaries (Plan 6 already routes it); the quick-action buttons that describe the dead character acting are gone.
4. **Dead DCC code is deleted, not hidden (Tasks 1, 2, 7).** Dice, combat, floor clock, Borant, inventory and quests views are removed from the bundle. `npm run build` and the backend suite stay green.
5. **Draft and scroll survive tab switches (Task 1).** Scene stays mounted (hidden) while another screen is open.

---

### Task 1: Theme, fonts and the shell

**Files:**
- Create: `frontend/src/theme-carousel.css`
- Delete: `frontend/src/theme-dcc.css`
- Modify: `frontend/package.json` (add `@fontsource/eb-garamond` 5.1.0 and `@fontsource/cabin` 5.1.0; remove `@fontsource/chakra-petch`, `@fontsource/ibm-plex-sans`, `@fontsource/literata`), `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/styles.css` (`:root` tokens only), `frontend/index.html` (title "Carousel")

**The tokens** (`theme-carousel.css`, loaded last):

```css
:root {
  --ink: #0E0E12; --panel: #171820; --panel-elevated: #242027; --line: #2E2A31; --line-2: #3D3640;
  --oxblood: #6F2531; --blood: #B63C47; --brass: #D6B57B; --parchment: #E8DECF; --sage: #7EAC95;
  --fog: #A5A0A1; --atlas-paper: #D4BD93; --slate: #5E7A86;
  --display: "EB Garamond", Georgia, serif; --story: "EB Garamond", Georgia, serif;
  --body: "Cabin", "Segoe UI", sans-serif; --mono: "JetBrains Mono", Consolas, monospace;
  /* the old names the existing views use, pointed at the new palette so nothing renders unstyled mid-migration */
  --bg: var(--ink); --bg-2: #121217; --panel-2: var(--panel-elevated); --panel-3: #2B262E;
  --text: var(--parchment); --text-2: #CFC5B6; --muted: var(--fog);
  --cyan: var(--brass); --cyan-dim: #8A7350; --magenta: var(--blood); --magenta-dim: var(--oxblood);
  --amber: var(--brass); --red: var(--blood); --hp: var(--sage); --mana: var(--slate); --violet: #9C8AB4;
}
```

- Story prose: `font: 19px/1.55 var(--story)`, max line length 70ch. Labels: Cabin, uppercase, `letter-spacing: .12em`, 11–12 px. Never uppercase prose.
- `body` background: `--ink` with one soft vignette; grain only on `.wallpaper` and poster surfaces.
- Delete the `-rgb` glow variables the DCC theme used (`--cyan-rgb`, `--magenta-rgb`) and their uses in `styles.css`.

**The shell (`App.tsx`):**
- Brand: "CAROUSEL" in the display face with the small line "A game of horror movies". Remove "Dungeon Crawler World", "Crawler Console", the `Live` bug and its Syndicate tooltip, and the GM provider chip (it moves to Saves & Settings).
- Tabs, in this order: `scene` Scene, `map` Town Map, `wallpaper` Red Wallpaper, `tickets` Tickets, `chronicle` Chronicle, `lodge` Lodge, `settings` Saves & Settings. Old stored tab ids map: `story`→`scene`, `timeline`→`chronicle`, `people`/`characters`→`lodge`, anything else → `scene`.
- A status strip under the nav: chapter identity (the running story's title, else the current place), the clock (`Day 2 · 21:40`, Task 2), the last save ("Saved at camp, 14 min ago" from `campaign.last_save`; "Not saved since leaving camp" otherwise) and the campaign selector.
- Engine-driven attributes on `<html>`: `data-camera` (`on` | `off` | `none`), `data-fate` (`deathwatch` | `wiped` | `alive`), `data-safe` (`1` when `campaign.safe_room`). Delete the `mood`/`urgency` block and everything that reads `floor_clock`.
- Keep the Scene mounted: render `StoryView` always and hide it with `hidden` when another tab is open, so the draft, scroll position and any turn in flight survive (Review Focus 5). Other screens mount on demand as now.
- The search dialog's `go()`: `quest` and `item` hits go to `chronicle`; `person` opens the dossier; everything else as now.
- Keyboard: `Esc` closes overlays (Radix already does), `Ctrl+K` search stays, `Alt+1`…`Alt+7` switch tabs, `Ctrl+/` focuses the composer.

**Steps:**
- [ ] Add the two fontsource packages, drop the three DCC ones, `npm install`, update the imports in `main.tsx` (EB Garamond 400/400-italic/600, Cabin 400/600).
- [ ] Write `theme-carousel.css`; import it last in `main.tsx`; delete `theme-dcc.css` and its import.
- [ ] Rebuild the topbar, tabs and status strip in `App.tsx`; wire the three `<html>` data attributes (camera comes from `api.story`, fetched on every `version` change).
- [ ] Keep `StoryView` mounted across tabs.
- [ ] `npm run build` passes; open the preview (`carousel-preview`, port 8799) and confirm every tab renders with the new palette and no DCC wording in the shell.
- [ ] Commit: `Carousel theme: oxblood and gilt tokens, Garamond and Cabin, the seven-screen shell`.

---

### Task 2: The scene, On Screen and Off Screen

**Files:**
- Create: `frontend/src/views/SceneRail.tsx` (right rail: feature card, production notes, leads), `frontend/src/scene.css`
- Modify: `frontend/src/views/Story.tsx`, `frontend/src/views/Hud.tsx`, `frontend/src/views/StoryBar.tsx`, `frontend/src/storybar.css`, `frontend/src/views/Announce.tsx`
- Delete: `frontend/src/story-v3.css` rules for dice/combat (the file shrinks; keep transcript and composer rules)
- Modify: `backend/app/memory/views.py:campaign_summary` (add `clock`)
- Test: `backend/tests/carousel/test_clock_summary.py`

**Server: the clock in the campaign summary.**

```python
from ..carousel import clock
camp["clock"] = {"day": clock.day(camp["game_time_minutes"]), "slot": clock.slot(camp["game_time_minutes"]),
                 "time": f"{clock.hour(camp['game_time_minutes']):02d}:{(clock.CLOCK_START + camp['game_time_minutes']) % 60:02d}"}
```

Tests: minute 0 → `{"day": 1, "slot": "morning", "time": "08:00"}`; minute 840 → `day 1, "night", "22:00"`; minute 960 → `day 2, "night", "00:00"`. Add `clock?: { day: number; slot: string; time: string }` to `Campaign` in `api.ts`.

**Layout (handoff §5.1):** left rail (cast, ~275 px), story column, right rail (~310 px), sticky composer.

- **Left rail, cast:** `RosterPanel` keeps its grouping logic. Each tile shows the portrait (initial on a parchment square when there is none), first name, Archetype/role (`roleLabel`), Plot Armor (`PA 4`), and a line for the fate axes that aren't default (`physical_condition`, `wallpaper_status`, `participation`). Remove the DCC `ROLE` labels "Manager" and the HP/mana `Bar`. "Contacts & elsewhere" becomes "Elsewhere".
- **Story column, top to bottom:**
  1. Camera line: `OFF SCREEN — The camera is away.` or `● REC ON SCREEN — The camera is rolling.` (red dot only here). With no story: no camera line at all.
  2. Plot Cycle (StoryBar), only while a story is active: eight phases, past ones dimmed, the current one lit in brass (Off Screen) or blood (On Screen). The phase list comes from `story.phases`.
  3. Establishing shot: `SceneBanner` restyled as a 16:9 frame with the caption `{place} · Day {d}, {time}`. Missing art shows a dark projection card with the place name, never a blank box.
  4. Opening bar, Deathwatch (Task 3), venue panel and Around panel, restyled as quiet parchment-on-panel strips.
  5. Transcript: GM prose in the story face; player lines in Cabin italic with the speaker's name; dialogue keeps `StoryText`.
  6. Composer: placeholder `What does {first name} do? Say anything.`; quick actions Look around, Talk to…, Check in with the group, Rest at camp (only when `data-safe`), Ask about the rules. Remove `COMBAT_QUICK`. "Previously on…" sends `Give me a "Previously on Carousel" recap…` (the existing prompt with the DCC title replaced).
- **On Screen frame:** with `data-camera="on"` the story column gets viewfinder corners (four 18 px L-shapes in `--blood`, CSS only) and the establishing shot a thin red border. The transition is a 450 ms light shift on the frame, nothing moves in the transcript, and the composer keeps focus and draft.
- **Right rail (`SceneRail.tsx`):**
  - **Feature card:** no active story → "CAROUSEL PRESENTS / FEATURE UNKNOWN / No confirmed storyline". Active → the story title set in the display face, the player-visible premise underneath, and the cast list (`name — casting`).
  - **Production notes:** Camera, Plot Cycle phase, Storyline (title or "Unconfirmed"), Location (`current_location`, plus venue zone when there is one), Time (`Day 2 · 21:40 · evening`).
  - **Revisions:** the story's public revision summaries, newest first.
  - **Behind the Scenes** link to Saves & Settings › Behind the Scenes (Task 7).
  - `CampaignSidebar` (floor objective, quests, alerts) is deleted.
- **Mechanics → Behind the Scenes.** The per-message `Mechanics` block is renamed "Behind the Scenes", closed by default, and lists state changes and rules consulted only. Delete `RollPill`, `RollView`, `natural`, `CHECK_KINDS`, the roll part of `broadcastMoments`, `CombatPanel`, `PHASES` (combat), `Objective`, `urgentAlerts`, `QUIPS` and the stage label "tools & dice" (now "rules"). Working line: `The projector warms up…` with the elapsed seconds.
- **Announcements:** `SystemNotice` loses "SYSTEM NOTIFICATION"/"NEW ACHIEVEMENT"; notices render as a title card with the Cabin label `NOTICE` or `WARNING`. `THEATRICAL` keeps `warning` only, plus a new `phase` style announced when the Plot Cycle phase changes (driven by the story payload changing between two fetches, never by narration text). Delete the Borant regex, `achievement`, `loot`, `levelup` and `critfail` styles.
- **Status for screen readers:** a visually hidden `aria-live="polite"` region says "On screen. The camera is rolling.", "Off screen.", or "Plot Cycle: First Blood." when those change.

**Steps:**
- [ ] Write the clock tests; add `clock` to `campaign_summary`; run `pytest backend/tests/carousel/test_clock_summary.py`.
- [ ] Rebuild the left rail tiles; delete the DCC role labels and bars.
- [ ] Rebuild the story column (camera line, Plot Cycle, establishing shot, composer) and the On Screen frame.
- [ ] Write `SceneRail.tsx`; delete `CampaignSidebar` and its helpers.
- [ ] Rename Mechanics to Behind the Scenes; delete every dice and combat component.
- [ ] Rework announcements and the live status region.
- [ ] `npm run build`; full backend suite. In the preview, with a seeded story, flip the camera through the GM op and confirm the frame and status text change, and that the draft stays.
- [ ] Commit: `Scene: camera state and Plot Cycle from the engine, Feature Unknown rail, Behind the Scenes for mechanics; dice and combat UI gone`.

---

### Task 3: Death Spectator and the wiped screen

**Files:**
- Create: `frontend/src/views/Spectator.tsx`
- Modify: `frontend/src/views/DeathwatchBar.tsx` (folded into Spectator; delete the file once nothing imports it), `frontend/src/views/Story.tsx`, `frontend/src/scene.css`

Plan 6 already owns the rules: Deathwatch while the player's character can't act, the pace control, the wiped screen and `new-group`. This task is presentation only.

- **Deathwatch** (`campaign.fate.deathwatch`): the story column keeps the transcript but swaps its header for a spectator frame: corners in fog grey, the line `YOUR CHARACTER HAS LEFT THE SCENE`, and `The movie isn't over.` in the display face. Under it: the pace control (Watch it all / Condensed), "Open the Chronicle" (switches tab), and the Plot Cycle if a story is running. The composer stays, with Plan 6's placeholder; the quick actions are replaced by "Cut to the others", "Summarise" and "Keep watching", which prefill the composer and go through the same turn pipeline.
- **Never show a cause.** The spectator frame shows nothing the fate API doesn't send: no cause, no rescue eligibility, no "they will win" copy. A sacrifice is never labelled a success.
- **Wiped** (`campaign.fate.wiped`): a full-screen title card, `THE END`, then the group's posters (PosterBoard, Task 7 styling) and the new-group form Plan 6 built. Save menu stays reachable.
- **Transition:** entering Deathwatch fades the transcript to 70 % for 600 ms and back (skipped under reduced motion). The live region says "Your character has left the scene. Spectator view."

**Steps:**
- [ ] Write `Spectator.tsx` from the existing `DeathwatchBar` logic; switch `Story.tsx` to it; delete `DeathwatchBar.tsx`.
- [ ] Restyle the wiped screen.
- [ ] `npm run build`. In the preview, set the player character's fate through the GM op (`set_fate`) and check the spectator frame, the pace control and that no cause text appears; then wipe the group and check the end card and new-group flow.
- [ ] Commit: `Death Spectator: the camera keeps rolling; spectator controls and the end card, no cause or rescue shown`.

---

### Task 4: The town map and place dossiers

**Files:**
- Create: `backend/app/place_api.py`, `frontend/src/views/PlaceDossier.tsx`
- Modify: `backend/app/main.py` (register), `backend/tests/carousel/mini_data.py` (`_place(..., description=None)`; Town Square gets `"A square of shops round a bandstand."`, CAR-099 gets `"SPOILERPLACE-OSCAR-14"`), `backend/tests/test_visibility.py` (add the secret; crawl the dossier of every known place and of CAR-099)
- Modify: `frontend/src/api.ts`, `frontend/src/views/MapView.tsx`, `frontend/src/map.css`
- Test: `backend/tests/carousel/test_place_api.py`

**Server: `GET /api/campaigns/{cid}/places/{place_id}`.** Resolves aliases with `resolve_place`. 404 `"unknown place"` (the same text for a real unknown place and a made-up id) unless the place is in `known_places`.

```python
def place_dossier(conn, cid: int, pid: str) -> dict | None:
    pid = resolve_place(conn, pid) or pid
    k = one(conn, "SELECT how, known_at_minutes FROM known_places WHERE campaign_id = ? AND place_id = ?", (cid, pid))
    if not k or not has_reference(conn):
        return None
    p = one(conn, "SELECT id, name, category, region, spoiler, raw_json FROM ref.places WHERE id = ?", (pid,))
    raw = json.loads(p["raw_json"])
    img = place_image(conn, pid)
    visits = rows(conn, """SELECT game_time_minutes FROM events WHERE campaign_id = ? AND type = 'travel' AND rolled_back = 0
                           AND json_extract(payload_json, '$.arrived') = ? ORDER BY id""", (cid, pid))
    return {
        "id": pid, "name": p["name"], "category": p["category"], "region": p["region"],
        "image": f"/location-art/{img}" if img else None, "safe": pid in SAFE_PLACE_IDS,
        "known": {"how": k["how"], "minutes": k["known_at_minutes"]},
        "description": None if p["spoiler"] else (raw.get("description") or None),
        "visits": {"count": len(visits), "last_minutes": visits[-1]["game_time_minutes"] if visits else None},
        "omens": [{"id": o["omen_id"], "description": o["description"], "stage": o["stage"], "seen_minutes": o["seen_at_minutes"]}
                  for o in rows(conn, """SELECT s.omen_id, s.stage, s.seen_at_minutes, o.description FROM omen_states s
                                         JOIN ref.omens o ON o.id = s.omen_id WHERE s.campaign_id = ? AND o.place_id = ?
                                         ORDER BY s.seen_at_minutes""", (cid, pid))],
        "stories": [{"title": s["title"], "ending": s["ending"], "ended_minutes": s["ended_minutes"]}
                    for s in rows(conn, """SELECT x.title, x.ending, x.ended_minutes FROM stories x JOIN ref.omens o ON o.id = x.omen_id
                                           WHERE x.campaign_id = ? AND x.status = 'concluded' AND o.place_id = ? ORDER BY x.id""", (cid, pid))],
        # mark_seen writes "{place}[, {zone}] at day {d} {slot}": match the place name followed by ", " or " at day "
        "people": [r["name"] for r in rows(conn, f"""SELECT c.name FROM characters c JOIN npc_profiles n ON n.character_id = c.id
                                                    WHERE c.campaign_id = ? AND {unmet_clause('c')}
                                                    AND (n.last_seen LIKE ? || ', %' OR n.last_seen LIKE ? || ' at day %')
                                                    ORDER BY c.name""", (cid, p["name"], p["name"]))],
    }
```

The travel event's `arrived` is the graph node the group stopped at; place nodes share their place's id (`travel_ops.py:141`), so a trip that ends on the road never counts as a visit.

Tests (`test_place_api.py`, mini data):
- An unknown place (CAR-099) and a made-up id both return 404 with the same body, and the body doesn't contain the place name.
- The arrival place returns `name`, `category`, `known.how`, and `visits.count` goes up by one after `travel` to it and back.
- Town Square's description is served; CAR-099, made known by inserting into `known_places`, serves `description: None` (it's flagged `spoiler`).
- OM-001's place lists no Omens before `sight_omen` and lists OM-001 with its description after; an unseen Omen at the same place is absent.
- An unmet resident whose `last_seen` is the place is absent from `people`; after `introduce_resident` they appear.
- After a story on OM-001 concludes, `stories` lists its title and ending, and nothing from its packet.

**Map screen (handoff §5.3):**
- **Layout:** left rail (map layers and a place list), the tile map, right rail (the selected place's dossier). The world-map inset stays in the corner and keeps its job (pan the panel map).
- **Layers / filters**, each with its count, all from `api.map`: All known places, Visited (`how = visited` or `visits > 0` once the dossier is loaded; use `how` for the count), Heard of (`how = told`), Safe places, Omens seen. Filter state is kept in `sessionStorage` under `map-filters:{cid}`.
- **Pins:** safe place = tent icon on sage; ordinary known place = round brass pin; place with a seen Omen = triangle with `!` on blood; the group = a gilt ring. Every pin has its name as a label at zoom ≥ 2 and as `aria-label` always.
- **Place list:** the known places sorted by name, filtered with the layers, each a button that selects the place and pans to it. This is the keyboard and screen-reader alternative to the map.
- **Dossier panel (`PlaceDossier.tsx`):** image (or a projection card with the name), name, category and region, safe badge, `Known since Day 1 · 08:00 (visited 3 times)`, description, "Omens seen here" (description and Visible/Examined, nothing else), "People last seen here", "Stories here" (title and ending), and **Travel here**, which calls the existing `api.travel` and plays the existing walk and establishing shot. A refusal (422/409) shows the server's message in the panel; the button never moves the marker itself.
- Selection, zoom and centre survive a refresh of the map data (Omen sighted, travel finished): keep them in component state and don't re-create the Leaflet map on `version` changes.

**Steps:**
- [ ] Add `description` to `_place`; write `test_place_api.py`; run it and see it fail.
- [ ] Write `place_api.py`; register it; the tests pass.
- [ ] Add the secret and the place URLs to the visibility crawl; run `pytest backend/tests/test_visibility.py`.
- [ ] Add `PlaceDossier` type and `api.place()`; build the layers, pins, list and dossier panel in `MapView.tsx`.
- [ ] `npm run build`. In the preview: select a known place from the list with the keyboard, travel there, confirm the selection and zoom survive the refresh and the dossier's visit count goes up.
- [ ] Commit: `Town map: layers, labelled pins, a place list and place dossiers for known places only`.

---

### Task 5: The Red Wallpaper

**Files:**
- Create: `frontend/src/views/Wallpaper.tsx`, `frontend/src/wallpaper.css`
- Modify: `frontend/src/App.tsx`

The player character's screen. Built from `api.character(cid, active_character_id)`, `api.story(cid)` and `campaign.fate`; no new server code.

- **Surface:** a full-bleed oxblood damask (an inline SVG pattern at 6 % contrast over `--oxblood`, no image download), a gilt frame inset 24 px, and grain. Opening it: the pattern spreads from behind the shell in 500 ms, then the cards fade in (skipped under reduced motion).
- **Columns:**
  1. **Your window into the game:** anchors to the sections below (Poster, Plot Armor, Plot Cycle, Tickets, Revealed).
  2. **The poster:** the portrait (or a silhouette) in a gilt frame, the Archetype as the poster title (`roleLabel`), and the character's name as the billing line. Advanced role and aspect, when set, as the tagline.
  3. **Your part in the story:** Plot Armor large (`effective`, with `base → current → effective` only when they differ and the modifiers that cause it, by `source`), the five stats (base and current when a buff changes them), the fate axes in plain words (physical condition, Wallpaper status, participation, persistent fate) where not default, and pending stat points with Plan 4's spend button.
  4. **What the Wallpaper reveals:** the running story's public revision summaries and the premise. Empty: `Nothing has been revealed to you yet.` There is no count of hidden things and no "obscured" rows: absent means absent (handoff §5.4).
- **Plot Cycle strip** along the bottom, only with an active story, the current phase lit.
- **Equipped tickets** as a short list linking to the Tickets screen.
- **Esc** returns to the previous tab.

**Steps:**
- [ ] Write `Wallpaper.tsx` and `wallpaper.css`.
- [ ] Add the tab; Esc returns to the previous tab.
- [ ] `npm run build`. In the preview, check Plot Armor and stats against `GET /characters/{id}`, and that a buff shows both numbers.
- [ ] Commit: `Red Wallpaper: the player's poster, Plot Armor, stats, fate and what has been revealed, nothing more`.

---

### Task 6: The ticket library

**Files:**
- Create: `frontend/src/views/Tickets.tsx`, `frontend/src/tickets.css`
- Modify: `frontend/src/App.tsx`

Read-only. Tickets come from the party's character cards (`api.characters`), which already leave hidden tickets out (`visible_tickets`).

- **Ticket cards:** cardstock (`--atlas-paper`), a punched half-circle notch on each side, a perforated stub on the right with the serial (`ticket.ref`, e.g. `TKT-0042`), the name in the display face, the holder's first name, `EQUIPPED` stamp when equipped, and uses as punched holes (`uses_left` of `uses_max`, filled/empty) when limited. `recharge` prints under the uses.
- **Filters:** holder (All, then each party member), Equipped / Carried. No rarity colours, no categories the data doesn't have.
- **Detail panel:** the selected ticket large, with its full `text` exactly as served (it's the rule), holder, equipped state, uses and recharge.
- **Empty state:** `No tickets yet. Silas hands them out.` before the opening's Silas stage, `No tickets.` after.
- Locked, undiscovered and evolution states from the mockup are **not** built: the engine has no such data yet (left out).

**Steps:**
- [ ] Write `Tickets.tsx` and `tickets.css`; add the tab.
- [ ] `npm run build`; in the preview compare every card's text to `GET /characters/{id}`.
- [ ] Commit: `Ticket library: physical ticket cards with exact text, holder, equipped state and uses`.

---

### Task 7: Chronicle, Lodge, posters, saves and Behind the Scenes; delete the DCC views

**Files:**
- Modify: `frontend/src/views/Journal.tsx` (`ChronicleView`), `frontend/src/journal.css`, `frontend/src/views/People.tsx`, `frontend/src/views/AroundPanel.tsx` (`LodgeRoster`), `frontend/src/views/PosterBoard.tsx`, `frontend/src/people.css`, `frontend/src/views/Settings.tsx`, `frontend/src/views/Reference.tsx`, `frontend/src/views/Search.tsx`, `frontend/src/views/Dossier.tsx`, `frontend/src/App.tsx`
- Delete: `frontend/src/views/Inventory.tsx`, `frontend/src/inventory.css`, `QuestsView` and `QuestCard` from `Journal.tsx`, `frontend/src/views/Journals.tsx` (`CharactersView`, replaced by the Lodge), `frontend/src/trophies.css`, the `Equipment` tab of `Dossier.tsx`

- **Chronicle (handoff §5.7):** keeps its Timeline and Stories tabs. Timeline: grouped by day (`Day 2`), each entry stamped with the clock time, a Cabin label for its kind (Travel, Scene, Story, Resident, Poster), the summary in the story face. Filters: All, Travel, Stories, People, Omens, Saves. Search box filters the loaded entries client-side. Bookkeeping events stay behind a "Show record-keeping" toggle, off by default. Stories tab: one card per concluded story (title, ending, cast, Day/time span). The right rail shows the current chapter (running story or place), the last save and a "Saves" link.
- **Lodge:** one screen with three sections: **The group** (party cards, opening the character dossier), **The Lodge roster** (`LodgeRoster`: met residents, last seen, in-party badge, what's known), **The board outside the Diner** (`PosterBoard`, posters as aged paper with `MISSING` in the display face). People's filters drop the DCC categories; the attitude meter stays.
- **Character dossier (dialog):** Overview, Story and History tabs; Equipment goes. Overview shows Archetype, Plot Armor, the five stats and tickets (shared ticket card component from Task 6).
- **Saves & Settings:** sections Saves (camp save points, restore preview, backups), Campaigns, Device (sound, motion, announcements), GM provider (the chip moved here), and **Behind the Scenes**: Rules (rule search, rulings review), Records, Diagnostics (token-gated as now), Consistency. These keep their code; only their headings and styling change.
- **Deletions:** every import of the deleted views, the `inventory` and `quests` entries in `api.ts` and their types if nothing else uses them, and the DCC icons map entries. Keep the backend endpoints (the visibility crawl still covers them; removing them is server cleanup, left out).

**Steps:**
- [ ] Rebuild the Chronicle with day groups, filters and search.
- [ ] Build the Lodge screen from the group, the roster and the poster board.
- [ ] Trim the character dossier; share the ticket card.
- [ ] Restructure Saves & Settings with Behind the Scenes.
- [ ] Delete the DCC views, CSS and API entries; `npm run build` with no unused-import errors; `grep -ri "crawler\|dungeon\|syndicate\|borant\|d20" frontend/src` returns nothing user-facing.
- [ ] Commit: `Chronicle, Lodge and Saves in the Carousel style; rules and diagnostics under Behind the Scenes; DCC inventory, quests and trophies views removed`.

---

### Task 8: Responsive, accessibility and the visual check

**Files:**
- Modify: `frontend/src/theme-carousel.css`, `frontend/src/App.tsx` (bottom navigation), `frontend/src/scene.css`, `frontend/src/map.css`

- **Breakpoints (handoff §9):**
  - ≥ 1440: three columns as built.
  - 1100–1439: rails 240/280 px; the scene's right rail becomes a drawer opened from the status strip ("Production notes").
  - 768–1099: single column; cast and production notes are toggled panels; the map's list and dossier become a drawer over the map.
  - < 768: bottom navigation `Scene · Map · Wallpaper · Lodge · More` (More holds Tickets, Chronicle, Saves & Settings); the composer is fixed above it with `env(safe-area-inset-bottom)`; the map dossier is a bottom sheet; ticket cards scroll in one column with 44 px hit areas.
- **Accessibility:** visible focus rings in brass; logical tab order (nav → status → rails → story → composer); every icon button has a label; contrast AA for prose and controls (check parchment on panel, fog on ink, brass on panel); camera, phase and fate changes announced in the live region (Tasks 2 and 3).
- **Reduced motion:** with the device setting on Reduce, no transition runs and nothing is hidden behind one.

**Steps:**
- [ ] Implement the breakpoints and the bottom navigation.
- [ ] In the preview, at 1920×1080, 1280×800, 900×1100 and 375×812 (`resize_window`), screenshot Scene (Off and On Screen), Map with a dossier open, Wallpaper, Tickets, Chronicle, Spectator and Lodge. Compare each 1920 shot with its mockup for layout and tone; confirm no horizontal scroll at 375.
- [ ] Type a draft, switch to Map and Wallpaper and back; the draft and scroll position are still there.
- [ ] Turn motion to Reduce and repeat the camera flip; nothing animates and the status text still changes.
- [ ] Full backend suite and `npm run build` green.
- [ ] Commit: `Interface: laptop, tablet and phone layouts, bottom navigation, focus and contrast, reduced motion`.

---

## Left out on purpose

- **Player notes and suspicion markers on the map** (handoff §5.3). New player-authored state; worth its own small plan once the map is in use.
- **GM tools** (Omen anchor editor, Reveal-as-player mode; handoff §7). GM authoring stays in ops and the GM prompt.
- **Evidence with provenance and confidence** (handoff §5.6). The engine records facts with `certainty` but not the observed/claimed/deduced split; the dossier shows what's recorded, nothing graded.
- **Locked, undiscovered and evolving tickets.** No data for them in the engine yet.
- **Story poster art and venue art.** No approved art exists; title cards and text stand in.
- **Sound cues.** The existing synthesized sounds stay off by default; no new stingers.
- **Removing the DCC backend endpoints** (inventory, quests, house rules). Frontend only here; the server cleanup is separate.
