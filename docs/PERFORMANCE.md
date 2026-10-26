# Performance and long-session reliability (M9)

Measured 2026-10-26 on the owner's PC (Windows 11, SQLite, uvicorn single process).

## Endpoint timings (live campaign, read-only)

Six requests each against the running server; "warm" is the median of requests 2-6.

| Endpoint | median ms (warm) | first ms | size KB |
|---|---|---|---|
| `/api/campaigns/2` | 29 | 15 | 71 |
| `/api/campaigns/2/messages` | 15 | 25 | 7 |
| `/api/campaigns/2/inventory` | 11 | 9 | 61 |
| `/api/campaigns/2/npcs` | 28 | 33 | 38 |
| `/api/campaigns/2/journal` | 26 | 6 | 6 |
| `/api/campaigns/2/chronology` | 4 | 24 | 20 |
| `/api/campaigns/2/lore-notebook` | 16 | 5 | 15 |
| `/api/campaigns/2/places` | 11 | 5 | 2 |
| `/api/campaigns/2/characters` | 16 | 24 | 25 |
| `/api/campaigns/2/search?q=Marcus family` | 30 | 12 | 1 |
| `/api/rules/lookup?q=Stealth` | 19 | 103 (index load) | 2 |
| `/api/rules/browse` | 2 | 23 | 1 |
| `/api/campaigns/2/consistency` | 31 | 36 | 3 |
| `/api/campaigns/2/saves` | 30 | 25 | 3 |

Every player endpoint answers in under 35 ms, so no query work was needed this pass. The one real page-speed problem
found (full-size images on the item and art pages) was fixed in M7: images are served as cached thumbnails
(`/media/...?w=`), pre-warmed at server start.

## GM token use (playtest 2: 33 real Codex turns)

| Per turn | median | p90 | max |
|---|---|---|---|
| Input tokens (all model rounds) | 67,448 | 161,716 | 223,195 |
| Cached input | 2,688 | 8,064 | 27,392 |
| Output tokens | 589 | 1,117 | 3,181 |
| Model rounds | 3 | 5 | 7 |
| Model seconds | 36 | 67 | 138 |
| Briefing size (characters) | 64,730 | 79,314 | 255,522 |

Input is dominated by the briefing, which is re-sent every round of a turn, so input grows with rounds (tool calls).
Only about 5% of input is reported as cached. The GM runs on the owner's ChatGPT plan through Codex CLI, so this costs
plan usage, not API billing. Levers, if usage limits become a problem: trim the briefing's long tails (the 255k
outlier was a turn with a very long rules excerpt), or cap rounds per turn. Neither is needed yet.

## Endurance run (50 turns)

A disposable copy of the live database, served by a real server process with the mock GM; the live game and the
ledger were never touched. After every turn the script checked that the turn completed, game time never went
backwards, and the consistency check (21 checks) still passed.

| Scenario | Result |
|---|---|
| 50 turns | 0 failed turns, 0 consistency issues; median 0.46 s, p95 0.48 s (no model latency) |
| Server killed in the middle of a turn, then restarted | nothing applied (game time unchanged), no turn left "active", resending with the same key ran it once |
| Same idempotency key sent twice | same turn returned, marked replayed |
| Three turns submitted at once | 200, 200, 409. The second request waited for the write lock and ran after the first (turns 24 and 25 did not overlap); the third was refused while one was running and one queued. Turns never run concurrently. |
| Slow GM (2 s per model call) | all turns completed (2.2-4.3 s) |
| Save point → 5 turns → preview → restore → undo | preview listed the changes; restore returned to the saved game time; undo restored the later state |
| Backup → verify → restore into a new folder → serve from a second server | verified; restored copy matches live |

## Reliability features added in M9

- Automatic daily database backup at server start when the newest backup is over a day old (`auto-daily`, keeps 7).
- Backups panel in Settings: list, create (with or without media), verify.
- Restore preview: before restoring a save point, see what changes (time, location, gold, items, quests, hidden messages).
- "Report a problem" on every GM message and failed turn: writes a redacted report to `data/reports/` (no GM secrets,
  no briefing text, no tool arguments, no token or passcode).
