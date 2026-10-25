# Upgrade roadmap progress

Source plan: `CRAWLER_CONSOLE_UPGRADE_PLAN.md` (October 25, 2026). One milestone per commit.

| Milestone | Priority | Status | Dependencies | Commit | Tests | Screenshot review | Open blockers |
|---|---|---|---|---|---|---|---|
| M0 Safe upgrade workflow | P0 | Done | none | 385d7dd | 155 backend; typecheck/build; isolation probe PASS | Settings passcode masked | ledger behind by 2 (see below) |
| M1 Canonical-state reconciliation | P0 | Exit gate met | M0 | (pending) | 166 backend; consistency 21/21 | HUD roster + objectives | 7 uncounted-gear items stay open for owner counts; 44 house rules move to M2/M7 |
| M2 Mechanics reliability & GM gates | P0 | Backlog | M1 | | | | |
| M3 Main story HUD | P1 | Backlog | M1 underway | | | | |
| M4 Characters, party, people | P1 | Backlog | M3 | | | | |
| M5 Inventory, crafting, economy | P1 | Backlog | M3 | | | | |
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
