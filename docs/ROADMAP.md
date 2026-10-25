# Upgrade roadmap progress

Source plan: `CRAWLER_CONSOLE_UPGRADE_PLAN.md` (October 25, 2026). One milestone per commit.

| Milestone | Priority | Status | Dependencies | Commit | Tests | Screenshot review | Open blockers |
|---|---|---|---|---|---|---|---|
| M0 Safe upgrade workflow | P0 | Done | none | 385d7dd | 155 backend; typecheck/build; isolation probe PASS | Settings passcode masked | ledger behind by 2 (see below) |
| M1 Canonical-state reconciliation | P0 | In progress | M0 | (pending) | 156 backend | n/a | remaining conflicts need owner decisions (import report) |
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

**Pending reviewed write-back (Ledger view)**: 41 clean changes, no blockers. `state/campaign.yaml`: clock, dungeon
time, location, saferoom, box count, advancement mirror. `state/roster.yaml`: clock mirror. `state/shared_inventory.yaml`:
Gold and Stabber note. Twelve character sheets: the box reward and the selection note. Not written: that commit is the
owner's.

**Still open in M1**: the importer's remaining 4 conflicts / 18 review items / 44 unimplemented house rules.
