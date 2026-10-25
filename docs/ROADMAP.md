# Upgrade roadmap progress

Source plan: `CRAWLER_CONSOLE_UPGRADE_PLAN.md` (October 25, 2026). One milestone per commit.

| Milestone | Priority | Status | Dependencies | Commit | Tests | Screenshot review | Open blockers |
|---|---|---|---|---|---|---|---|
| M0 Safe upgrade workflow | P0 | Done | none | 385d7dd | 155 backend; typecheck/build; isolation probe PASS | Settings passcode masked | ledger behind by 2 (see below) |
| M1 Canonical-state reconciliation | P0 | Ready | M0 | | | | needs owner decisions on conflicts |
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
