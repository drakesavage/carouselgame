# Upgrade roadmap progress

Source plan: `CRAWLER_CONSOLE_UPGRADE_PLAN.md` (October 25, 2026). One milestone per commit.

| Milestone | Priority | Status | Dependencies | Commit | Tests | Screenshot review | Open blockers |
|---|---|---|---|---|---|---|---|
| M0 Safe upgrade workflow | P0 | Done | none | 385d7dd | 155 backend; typecheck/build; isolation probe PASS | Settings passcode masked | ledger behind by 2 (see below) |
| M1 Canonical-state reconciliation | P0 | Exit gate met | M0 | (pending) | 166 backend; consistency 21/21 | HUD roster + objectives | 7 uncounted-gear items stay open for owner counts; 44 house rules move to M2/M7 |
| M2 Mechanics reliability & GM gates | P0 | Done | M1 | 4933fed | 187 backend; playtest 2 gate met; isolation probe PASS | n/a | P2 gaps in docs/RULES_COVERAGE.md |
| M3 Main story HUD | P1 | Done | M2 | 82fc1b7 | 187 backend; typecheck/build | live campaign at 1440/1100/375 px; 100-message disposable transcript; owner accepted the gate for this pass | journal "now" chip lacks the immediate flag; phone height gap |
| M4 Characters, party, people | P1 | Done | M3 | d8ebe50 (+ f6b7198 ability explanations) | 188 backend (people payload + GM-leak test); typecheck/build | live People, Characters, Driscoll dossier at 1440 and 768 px | no console interactions recorded yet, so "last interaction" is mostly empty; truncated promise title (M6) |
| M5 Inventory, crafting, economy | P1 | Done | M4 | (this commit) | 195 backend (counts/stages/materials, reserved-gear guard, uncounted ruling); typecheck/build | live inventory grid, list, filters, expanded items at 1440 px | inventory stays read-only (changes go through GM ops); categories are name-based; correction #14 (no uncounted items remain) not yet written back to the ledger |
| M6 Quests, memory, timeline | P1 | Backlog | M3 | | | | |
| M7 Rules center | P1 | Backlog | M2 | | | | |
| M8 World visuals | P2 | Backlog | M3–M6 verified | | | | |
| M9 Long-session reliability | P2 | Backlog | all | | | | |

## M0 report (2026-10-25)

**Baseline verified**
* Application repo `E:\dccrpg` (dccweb) clean and level with `origin/main` at `8e67a73` before this milestone.
* Backend tests: 152 passed before the milestone, 155 after (3 new). Frontend type-check and build clean.
* Live database: schema v2, `PRAGMA integrity_check` ok, WAL. Campaign 2 at game time 1449.
* GM runtime: isolation probe PASS on codex-cli 0.163.0-alpha.11.2 (no tools visible; personal AGENTS.md still
  injected and neutralized, as documented in docs/GM_RUNTIME.md).

**Backup**
* `data/backups/20261025-103157-m0-baseline/`: verified (21 files, 40 tables). Restored into a scratch folder and
  compared with live (campaigns, game time, characters, items, Gold total, events, art): identical. Scratch removed.

**Safety work**
* `backend/app/backup.py`: create / verify / restore (restore only into a new or empty folder). Tests: round trip,
  tampered backup rejected, a copy of the real pre-v2 database migrates in place with every row preserved.
* Ledger write-back now refuses to regenerate `checkpoints/latest.md` when it has hand-written sections; the UI
  checkbox is disabled in that case.
* Device passcode masked in Settings (Show / Copy), rotation asks for confirmation. The API already serves it only to
  this PC.
* `docs/OPERATIONS.md`: repositories, backups, restore, code rollback, ledger rules.

**Ledger divergence (read-only analysis)**
* `dccrepository` is 2 commits behind `origin/main`, nothing local, clean tree: fast-forward possible.
* Incoming: `0abb69b` "Establish reference-locked visual canon for all twelve Denton crawlers" and `353576c`
  "Document visual bible authority and gear-precedence rule", adding `state/characters/VISUAL_BIBLE.md` and 4 lines
  in `state/characters/README.md`. No YAML state files change.
* Conflict of authority: the console currently has 20 pending visual write-back changes (`visual.reference` blocks in
  12 character YAMLs). Upstream makes `VISUAL_BIBLE.md` the visual canon and says it supersedes `visual.unestablished`.
  Writing the pending changes would duplicate that canon. Recommendation: pull the 2 commits, do not write the visual
  changes, and in M1 make the console read the bible as its visual source.
* Not pulled: pulling is the owner's call (Ledger view → Pull).

**Passcode**: if the Settings screenshot with the passcode was shared, rotate it (Settings → New passcode).

## M1 progress (2026-10-25)

**Decision 1: ledger pull.** Owner chose: pull, and make the bible the visual source.
* Ledger fast-forwarded through the app's pull (`f1a5680` → `353576c`); no local changes existed.
* `state/characters/VISUAL_BIBLE.md` is now loaded into each crawler's `visual.reference` at import, after every pull,
  and at startup (skipped when unchanged). Its negative-continuity guard and global art rules feed portrait prompts.
* Write-back no longer copies visual references into character YAMLs (the 20 pending visual edits are gone).

**Decision 2: Gold and Personal Stabber.** Owner chose: paid, 4,016 Gold.
* Evidence: `shared_inventory.yaml` (4,046, "no payment") was written at 13:04 CDT on Sept 25 (`98e66e4`); the market
  sweep recorded "gold_before 4046, personal_stabber_paid 30, gold_after 4016" at 18:11 (`4067fa1`, `f1a5680`) and never
  touched `shared_inventory.yaml`, so its values were stale.
* Applied as correction #1 (`app/reconcile.py`): owner-approved, evidence recorded, save point #13 taken first, applied
  through the validated rule engine. Shared Gold 4,016; the reservation is paid and held by Secondhand Saviors, not
  owned until Scout claims it.
* Pending reviewed write-back (Ledger view): 2 clean changes to `state/shared_inventory.yaml` (Gold quantity and the
  stale note). Not written yet: that commit is the owner's.

**Decision 3: floor clock.** Owner chose: 214h41m (70 minutes passed after the checkpoint).
* Evidence: `campaign.yaml` says 215h51m remaining / 1,449 elapsed at the 13:04 checkpoint (`98e66e4`). Two
  Negotiations followed (`c2d22c3`, `11d4b24`) with no clock entry. The market sweep's own clock runs 215h41m → 214h41m
  (`4067fa1`, `f1a5680`); its 10-minute gap before the sweep matches two Negotiations at its 5-minute rate.
* Applied as correction #2 (save point #14): `advance_time` 70 minutes, no mending, no day rollover. Elapsed 1,519,
  daily reset in 4h41m, collapse in 214h41m.

**Decision 4: location and saferoom.** Owner chose: where Cliff is.
* Evidence: the checkpoint placed all twelve at The Molting Pigeon (saferoom). The roster then split the party
  (prison-recon, training-market, saferoom), and the market sweep happened at Gilded Feathers, Heavensworth. Nothing
  after the split updated `campaign.yaml`.
* Applied as correction #3 (save point #15): `set_location` Gilded Feathers, Heavensworth, not a saferoom. The Molting
  Pigeon stays the saferoom group's location; the recon group's position stays unknown. The GM context no longer
  carries the "records disagree" location note.

**Decision 5: Silver Earth Boxes.** Owner chose: opened, fix console and ledger; add the physical rewards to inventory.
* Evidence: the only "unopened" records are the class-selection notes of Sept 22 (`2f89e56`): `floor_3_selection.silver_earth_box`
  on all twelve sheets and `unopened_rewards: silver_earth_boxes: 12` in `campaign.yaml`. Everything later (`98e66e4`,
  Sept 25) says opened: each sheet's `floor_3_silver_earth_box`, the inventory box (quantity 0, opened-and-resolved), the
  checkpoint, the roster, and the twelve hobby Skills already on the console sheets.
* Applied as correction #4 (save point #16): the twelve selection notes now read opened-and-resolved (a new audited
  `sheet_edits` step in `reconcile.py`, since no engine op edits sheet fields), and each box's physical reward is
  in its crawler's inventory (quantity 1; whether edible ones were eaten is not recorded). This settles the import
  report's twelve "Silver Earth Box status" conflicts.

**Decision 6: advancement count.** Owner chose: mirror the live count.
* Evidence: `state/advancement.yaml` calls itself the authoritative live counter, and its log runs 6 → 11/12
  (DCC-DENTON-014), 11 → 12 → reset to 0/12 (DCC-DENTON-015), then 0 → 4/12 for the four market-sweep decisions
  (`f1a5680`). The console and GM context already show 4/12. The roster's and checkpoint's 0/12 and
  `floor_3_molting_pigeon_checkpoint`'s 11/12 are correct snapshots for their checkpoints and stay as recorded. The only
  stale live-looking value is `campaign.yaml current_state.advancement_cycles: 11/12`, which the GM could still read
  through lookup_records.
* Write-back now keeps `current_state.advancement_cycles` equal to the live counter on every write (like the roster's
  floor-clock mirror): 11/12 → 4/12 now. No correction record was needed; console state was already right.

**Import report review.** The 4 remaining conflicts: advancement, floor clock and Gold were already settled by
decisions 6, 3 and 2; Mad Driscoll is decision 9. The 18 review items: 9 ammunition entries (decision 7), 8 uncounted
items (pending the owner's counts), and the request to flag Passive Skills, which is a mechanics feature, not a state
conflict: moved to M2.

**Decision 7: ammunition.** Owner ruling: the last exact distribution is current.
* Evidence: exact count at the Floor 2 northeast Warden checkpoint (`89a4c9c`, Sept 8, after the Homogenous Humor fight
  that lost two bolts): bolts Cliff 24, Maya 20, Priya 20; Terri 22 pistol rounds. The Greenhouse Boss fight and a rescue
  followed with no ammunition record (`0374b26`); the ledger refused to invent a total and asked for a ruling. Chat
  history has no counts; the market only listed stock.
* Applied as correction #5 (save point #17): 64 shared crossbow bolts and 22 pistol rounds (the sheets draw both from
  the shared pool). Write-back marks both counts established and records the ruling in `ammunition_reconciliation` and
  `campaign.yaml`.

**Decision 8: Floor 2 assets.** Owner chose: everything in `historical_floor_2_assets` came along.
* Evidence: the Sept 25 rules audit (`08593db`) renamed `latest_floor_2_assets` to `historical_floor_2_assets`, which
  the importer skips, but the Floor 3 checkpoint (`98e66e4`) then wrote current status into it (pickup operational at The
  Molting Pigeon; speaker mount installed). Nothing records using or losing the rest.
* Applied as correction #6 (save point #18): ten shared items added: the 1987 Toyota Pickup (status, fuel, installed
  systems in its note), the Reversible Murder Drum Speaker Mount, 3 Explosive Rune Plates, 40 Crawler Biscuits, the
  15-arrow, 15-handgun-round, 9-shotgun-shell and 6-trap-stake caches, the Portable Mechanic Toolkit and the Spare Winch
  Cable. Write-back adds them to `items`; the historical section keeps the full records.

**Decision 9: Mad Driscoll.** Owner chose: deceased; fix the sheet. Correction #7 (save point #19): his sheet's
`roster_category` becomes `deceased_npcs`, matching the roster; his trapsmith tools stay with him on Floor 1.

**Conflict report and consistency check (exit gate).**
* Each correction records which import-report entries it settles; Settings shows every conflict and review item as
  resolved (with its correction number) or open. Now: 16/16 conflicts resolved; 10/18 review items resolved, 8 open
  (7 uncounted items awaiting the owner's counts, and the Passive-Skills flag, moved to M2). Decision 6 is recorded as
  correction #8 (no state change) so it has an audit entry.
* `app/consistency.py` compares each fact as every surface serves it (records, HUD summary, GM briefing): Gold, floor
  clock, advancement, location and saferoom, the player character's group, every assigned character's position, and
  reservations. Settings shows it; a regression test runs it on a disposable copy. It found the stale positions below.

**Decision 10: positions.** Owner chose: characters follow their group.
* Evidence: every crawler sheet kept the 13:04 checkpoint's "The Molting Pigeon" after the daylight split, so the GM was
  told e.g. "Scout NOT PRESENT (group prison-recon, The Molting Pigeon)". Cause: joining a group with no recorded
  location kept the member's old location.
* Correction #9 (save point #21): training-market members at Gilded Feathers, Heavensworth; prison-recon members'
  position not recorded (their sheets get "Not recorded: with the Prison Recon group (...)"); saferoom unchanged. Engine
  fix: group members take the group's location, unknown included. Write-back now carries known character locations.

**Decision 11: immediate objective.** Owner chose: link it to the quests.
* Correction #10 (save point #22): the immediate objective is quests #3, #5, #4 in the old string's order; the string is
  kept as history. The quest op takes `immediate: true/false`, so the GM can change it in play; a finished quest drops
  out. The HUD tags those quests "now" instead of repeating the text; the GM briefing lists them by quest number.

**Decision 12: party labels.** Owner chose: With Cliff / Elsewhere. The HUD roster and the Characters page group people
by party group ("With Cliff", "Elsewhere: <group> @ <place or position not recorded>"), counts come from the groups
("6 with Cliff · 9 elsewhere"), and System-party membership is its own chip.

**Pending reviewed write-back (Ledger view)**: 75 clean changes, no blockers. Not written: that commit is the owner's.

**Still open in M1** (visible in Settings): counts for the uncounted gear (Cliff's basic carried gear, Eli's converted
needle-spear javelins, Nate's personal maintenance tools, recovered electrical cabling, better-quality liquor). The
Passive-Skills flag and the 44 unimplemented house rules belong to M2 (mechanics) and M7 (rules center).

## M2 progress (2026-10-25)

**Coverage matrix.** `backend/app/rules/coverage.py` classifies every one of the 104 imported house rules (93 from
CORE_MECHANICS.md, 11 campaign rulings) against today's code and tests; `docs/RULES_COVERAGE.md` is generated from it.
A regression test fails if a rule has no entry, if an entry cites a test that does not exist, if a "tested" rule cites
none, or if a gap has no priority. The house-rule labels the GM briefing shows now come from the matrix (they were a
hand-kept list of section numbers, and stale: Mending and Rest said "pending").

| Status | Rules |
|---|---|
| implemented and tested | 38 |
| implemented, untested | 2 |
| partly implemented | 12 |
| GM ruling | 37 |
| reference only | 7 |
| missing | 8 |

Of the import report's 44 "awaiting implementation": 14 are implemented and tested, 1 implemented but untested, 10
partial, 9 GM rulings, 2 reference only, and 8 missing.

**P1 gaps (what the next Floor 3 sessions need)**: crafting (13.1 declare, 13.2 mundane crafting Check, 13.5 materials
consumed and salvage, 13.6 assistance) for tonight's Gold Siphon; party Skill Checks (7.8) and off-screen group
resolution (15.2) for the recon team; ammunition spent by attacks (10.8), Call a Play (10.9) and Catcher/class
Interrupts (10.7) for the prison assault; traps (13.7). P2 and P3 are listed in the matrix.

**Crafting (13.1, 13.2, 13.5, 13.6).** New GM tool `craft` (`backend/app/rules/crafting.py`), from Core Rulebook pp.
221-227. One call is one attempt, declared in full first: the item, Skill, materials, time and Difficulty come from
Table 43 by size or from a declared project's design (the Gold Siphon's: Cliff leads, Engineering 16, 8h, 8 Misc. Junk,
Advantage from Margit). Nothing is spent unless every material is on hand with a known count. Then the materials are
consumed, the clock advances size x size hours, the lead crafter rolls Fabricate (Engineering with moving parts) with
Help, and Success adds the item and completes the project, while Failure salvages by the margin (Table 47). Common
household items cost 1 Misc. Junk; magical crafting is refused below Floor 4. Tests on the disposable campaign copy
cover the Siphon build, a wrong lead, a second build, half salvage, a natural 1, a short stash (nothing spent, no time
passes), and a full GM turn that crafts once with one set of rolls. Live campaign state was not touched. Coverage:
42 tested, 5 missing; P1 now 6.

**Party Skill Checks and off-screen groups (7.8, 15.2).** New GM tool `party_check` (`backend/app/rules/party.py`; Core
Rulebook p. 61, Rules for Survival p. 18). Every participant (a list, or a party group's members) rolls the Skill against
one Difficulty, with no Help; the party succeeds when half or more succeed; each Amazing Success or Critical Hit lifts one
teammate's failure, each Major or Critical Fail drags down another teammate's Success. INTERPRETATION (the book does not
order them): both apply at once against the original results, lifting the narrowest miss and dragging down the narrowest
success. `offscreen` resolves a group without the player character: its objective and the stakes of Failure must be
declared before the roll, the rolls are secret by default, and the outcome is logged as a GM-only event for the GM to
apply with the ordinary ops. Refused calls roll nothing. Tests: the book's own example (2 of 5 fails), the conversion
edge cases, the recon group resolved secretly on the disposable copy, and the guards (Cliff's group, undeclared stakes,
one participant, outsiders). Also fixed a flaky crafting test that assumed the real-dice Check always succeeded.
Coverage: 44 tested, 4 missing; P1 now 4 (ammunition, Call a Play, Catcher/Interrupts, traps).

**Combat completion (10.8, 10.9, 10.7, 7.7)** in `backend/app/rules/combat.py`, from Core Rulebook pp. 84, 88, 93, 183,
189. Bow, Crossbow and Handgun Attacks each spend one round of the supply the sheet names, whose count must be
established (64 bolts and 22 pistol rounds now are; Erin's "Arrow supply" is not, so her bow is refused until it is);
a Crossbow fires once per round; a Handgun needs a Reload Action after a Major Fail; shots are tallied and combat end
prompts the recovery ruling (CORE 12.3). New tools: `combat_call_play` (an Action, 2d6 keep high, applied to the ally's
Attack Check if it needs it, otherwise to damage; INTERPRETATION: lasts through the next round), `combat_intervene`
(+1d6 to another crawler's missed Attack), and `combat_catch` (Catcher Ranks required, direct Attacks only, no Evade,
+5 DR per 5 Ranks). Every combat step is now atomic, so a refused Attack spends nothing. Tested on the disposable copy
in one scripted fight: no firing before the ammunition ruling, the Catcher and Area guards, bolt and round counts, the
Crossbow cooldown, Erin's unestablished arrows, a Called Play and an Intervene each turning a miss into a hit, the
Handgun reload, and the fired-ammunition tally. Coverage: 47 tested, 3 missing; P1 now 1 (traps).

**Traps (13.7, CAMPAIGN)** in `backend/app/rules/traps.py`; the books give no general procedure for crawler-built traps
(Trap Engineer only covers crafting them, Core Rulebook p. 203), so the console enforces the campaign rule's record-keeping
and timing instead of inventing statistics. New GM tool `trap`: `record` sets a trap's full record (components, ammunition,
deploy cost, trigger, area, Check, damage, effects, reset, bulk, label and source) and is refused while the trap is
deployed; `deploy` requires a record and spends its cost (Actions in combat, otherwise time); `trigger` rolls its Check
(fixed attack bonus vs Evade, an operator's Skill, or a target's save), applies damage, spends ammunition and leaves it
triggered (or spent); `reset` and `stow` follow its record; `status` lists every trap's record and condition. Tested on
the disposable copy through one fight. None of the party's existing traps has a record yet: the ledger holds full module
details only for the Murder Drum Battery's Skyhook Harpoon and Kill-Lane Wings. Trap condition is not yet written back
to the ledger (P2). Coverage: 47 tested, 2 missing; no P1 gaps remain. All Core Rulebook citations added in M2 now use
the rulebook index's page numbers (what search_rules and read_rule_page return).

**Playtest 1 (exit gate: not met).** 47 turns on a disposable copy (auto routing; live campaign and ledger untouched); full
report in `docs/playtests/M2-playtest-1.md`. Gold, Misc. Junk, ammunition, inventory and HP all matched the narration.
Not explained: time (a long rest counted twice), advancement cycles (inflated to a premature checkpoint), and positions
(campaign vs group, group vs members). Issues: 5 high (H1 double rest time, H2 cycle inflation, H3 Gemini inventing
off-screen outcomes, H4 campaign/group location drift, H5 group moves leaving members behind), 7 medium, 3 low.

**M2.1 integrity repair and playtest 2.** All five high issues and the medium/low issues from playtest 1 fixed, each
with a regression test on a disposable copy (185 backend tests): a rest is the only clock for its own hours (H1);
the server decides cycle eligibility, at most one per turn and one per combat, with an audit of what qualified (H2);
contact with or reports from absent characters route to the mechanics GM, the conversation path cannot record facts,
and narration of an absent character speaking without an off-screen resolution is flagged (H3); one location model:
moving the campaign moves the player character's group and members, moving that group moves the campaign, a group
move carries its members, saferoom status follows known saferooms only (H4/H5); Intervene reaches any eligible miss
this round; a Called Play's roll records the boosted total; scene aliases; memory importance clamped; a checkpoint
leaves an explicit Level-up task; `save_game` creates a real save point; the post-turn lock race; quoted prices and
offers no longer flagged; a sheet's named ammunition cannot be swapped without an owner ruling; Codex's usage limit
is reported plainly. Playtest 2 (`docs/playtests/M2-playtest-2.md`, 30 turns including inventive non-linear play,
save/load and restarts): zero unexplained changes to Gold, items, ammunition, HP, Mana, location, time, cycles or
Levels, and no fabricated canonical outcomes. Pending owner rulings: Erin's arrows, paid training, trap records.

**Owner rulings (2026-10-25).** Erin's arrows: the 15-arrow recovered cache becomes her supply (correction #11, save
point #23); her bow now fires from it. Paid training (correction #12, save point #24, a CAMPAIGN ruling found by
search_rules and written back to campaign_rulings): +1 Rank costs 4 hours and 500 Gold at a guild that teaches the
Skill (Core Rulebook p. 333, within the Rank cap); a shorter paid block counts as safe grinding hours within the 5-hour
daily limit. Trap records: the Skyhook Harpoon and Kill-Lane Wings come from the ledger; the other traps are drafted
in `docs/trap-records-draft.md` for the owner's approval. Pending ledger write-back: 77 clean changes.

**Trap records approved** as drafted and applied as correction #13 (save point #25): all nine traps are now deployable
under CORE 13.7; out of combat a trap's attack takes the target's Evade (`target_number`). Records and condition live in
the console; writing them back to the ledger is the remaining P2 gap.

**M2 closed (2026-10-25).** Isolation probe re-run after the M2 upgrades: PASS (no tools visible to the GM; no file
written, canary not leaked, the watcher saw no unauthorized events). Exit gate met by playtest 2. Carried to the backlog:
the P2 gaps in `docs/RULES_COVERAGE.md` (trap record/condition write-back, the AI Favor reroll, opposed and surprise
tests, Taunt and the Healing Interrupt, salvage, level-award sizes) and the owner's counts for the uncounted gear.
