# Campaign-integrity audit (2026-10-24)

Scope: the brief's checklist, traced through one full turn: browser → `POST /api/campaigns/{id}/turns` →
`TurnService._process` → `build_context` → Codex rounds → `ToolRunner` → rules engine → one SQLite transaction →
autosave → `GET /api/turns/{id}` → transcript, cards and panels.

Status words: **VERIFIED** (implemented and covered by a test or a live check), **PARTIAL** (works with stated gaps),
**MISSING**, **BROKEN**. "Before" is the state at commit `7d85036`; "after" is this pass.

| Area | Before | After | Evidence |
|---|---|---|---|
| Stats, skills, spells, HP, Mana, statuses, injuries | VERIFIED | VERIFIED | `rules/engine.py`, `rules/checks.py`; `test_rules.py` |
| Skill training / new skills / rank caps | MISSING | VERIFIED | op `skill` (Rank 15 cap on Floors 1–5 unless a cited `cap_override`); `test_state_v2.py`, `test_campaign_regression.py::test_skill_training_follows_house_rules` |
| Level awards (Quest/Boss/grind), Stat-point banking | PARTIAL (cycle checkpoint only) | VERIFIED | op `level` (+3 Stat points on Floor 3+, resets Level-grind progress) |
| AI Favor | MISSING | VERIFIED | op `ai_favor` (cannot go negative) |
| Cooldowns / limited uses | MISSING | VERIFIED | op `use_ability` (per scene/day/floor/rest/minutes); resets on scene change, dungeon day, rest |
| Equipment effects | PARTIAL | PARTIAL | Effects are stored and shown; the engine doesn't auto-apply item bonuses (the GM passes them as modifiers) |
| Inventory, currency, transfers, unknown counts | VERIFIED | VERIFIED | atomic batches; no overspending; unknown counts can't be spent; stable `item_id`; `test_campaign_regression.py::test_inventory_cannot_overspend_or_duplicate` |
| Purchases / reservations | MISSING | VERIFIED | quest kind `reservation` is never an owned item; the stabber is seeded with both conflicting records; `test_reserved_stabber_stays_a_reservation` |
| Floor, location, collapse clock, elapsed time | VERIFIED | VERIFIED | `advance_time`; context shows the clock |
| Dungeon day / daily reset | MISSING | VERIFIED | `day_info`, `dungeon_day` events, daily-use resets; `test_mending_and_dungeon_day_rollover` |
| Rest and mending | MISSING | VERIFIED | op `rest` (saferoom only, slots-based); `advance_time` `mending`; `test_rest_requires_saferoom_and_heals_by_slots` |
| Combat rounds, interrupts, damage, Dying | VERIFIED | VERIFIED | `rules/combat.py`; `test_combat.py`; live Codex round 2026-10-22 |
| Advancement cycles, checkpoint, skill marks | VERIFIED | VERIFIED | `checks.py`; recap must not count a cycle (integrity flag) |
| Grinding, Level-by-grinding, floor-end Rank 5+ checks | MISSING | VERIFIED | tools `grind`, `resolve_floor_end`; `test_grinding_*`, `test_floor_end_*` |
| Party splits, locations, assignments, offscreen activity | MISSING | VERIFIED | `party_groups`, ops `group` / `move_group` / `move_character`; seeded from the roster's daylight assignments; `test_group_departure_moves_only_that_group` |
| Quests, crafting projects, deadlines, promises | PARTIAL | VERIFIED | quest kinds `project` / `crafting` / `commitment` / `reservation`, `details`, `due_in_minutes`, `owner`; siphon design seeded from the checkpoint; `test_margit_siphon_project_keeps_its_design` |
| Relationships, personalities, NPC motivation | PARTIAL (4-character cap dropped present NPCs) | VERIFIED | presence comes from scene and group, not name matching; companion sheets for everyone present; per-NPC memories (`remember`); `test_private_motivation_surfaces_only_when_the_character_is_relevant`, `test_npc_relationship_evolves_*` |
| GM-only secrets | VERIFIED | VERIFIED | `test_visibility.py`; GM memories and diagnostics are token-gated; integrity check flags verbatim leaks |
| House rules, rulings, retrieved references | VERIFIED | VERIFIED | `search_rules`; citations only from retrieved passages (`test_rule_citations_come_from_retrieved_passages`); auto-retrieved passages are labelled "verify before citing" |
| Current scene in the GM context | MISSING | VERIFIED | `scenes` table; scene seeded from the newest active ledger snapshot |
| Memory layers | PARTIAL | VERIFIED | transcript (immutable) · structured state · scene/episodic summaries (`memories` layer `episodic`, `memory_summaries`) · canonical facts (`lore_facts`) · NPC memories (`memories` layer `npc`) · GM knowledge (`gm_secrets`, GM-visibility memories) · rules (`house_rules`, `rulings`, rulebook index) |
| Interrupted turns after a crash | BROKEN (stuck "processing" forever) | VERIFIED | `recover_interrupted_turns` at startup; `test_interrupted_turns_are_failed_at_startup` |
| GM runtime isolation | PARTIAL (read-only sandbox, but a shell was available) | VERIFIED | `shell_tool`, `shell_snapshot`, `multi_agent`, `hooks` and MCP servers disabled; any command execution rejects the result; checked live |
| Malformed model output | PARTIAL | VERIFIED | one automatic retry |
| Diagnostics / integrity report | MISSING | VERIFIED | `turn_diagnostics`, `/api/gm/...diagnostics` (token), Settings → GM diagnostics |

## Codex runtime decision

Keep `codex exec`, stateless per round. The backend rebuilds a bounded briefing every call, so continuity never depends
on a Codex conversation history or its compaction, and a restart loses nothing (`test_restart_keeps_continuity`). The
app-server/SDK would add streaming and persistent threads, but it would also shift continuity into a session the
backend doesn't own. Revisit only if token-by-token streaming becomes a priority.

## Conflicting campaign records (reported, not changed)

* Floor clock: `campaign.yaml` 215h51m vs `gilded_feathers_market.yaml` 214h41m after the market sweep.
* Gold: shared inventory 4,046 vs market transaction 4,016 (30 paid for the Personal Stabber).
* Personal Stabber: `shared_inventory.yaml` says "reserved but not owned and no 30-Gold payment has been made"; the market file says it was prepaid and is held pending Scout's claim.
* Location and saferoom: `campaign.yaml` places the party at The Molting Pigeon (saferoom); the newer market records put the training/market group in Gilded Feathers. The GM briefing now states this mismatch explicitly, and the UI shows "record says saferoom".
* Silver Earth Box status per character (selection note "unopened" vs the later "opened").

## Still open

* Equipment bonuses aren't applied automatically (the GM adds them as modifiers).
* The Intervene, Catcher and Taunt interrupts and Call a Play bonuses are spent as Actions and resolved by the GM.
* New characters (random-first creation) have no op yet.
* Companion-sheet private state is updated through GM memories and notes, not by editing the imported sheet itself.
* Write-back to the ledger doesn't yet include groups, scenes or memories (they stay in the console).
