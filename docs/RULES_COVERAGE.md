# House-rule coverage matrix

Generated from `backend/app/rules/coverage.py` (`python -m app.rules.coverage`); edit the matrix, not this file.

| Status | Rules | Meaning |
|---|---|---|
| implemented and tested | 42 | the console enforces it and a test exercises it |
| implemented, untested | 2 | enforced, but no test yet |
| partly implemented | 11 | part is enforced; the gap column says what is not |
| GM ruling | 37 | table procedure the GM applies with the existing tools |
| reference only | 7 | history, authority or bibliography |
| missing | 5 | a mechanic the console should enforce and does not |

## Gaps by priority

### P1 (6)
- **§7.8 Party Skill Checks** (missing): no party Skill Check: each crawler is rolled separately; 'half succeed' and the Amazing/Major conversions are not computed
- **§10.7 Evade and Interrupts** (partly implemented): Catcher and the other class Interrupts are not tools (Marcus and Terri have Catcher +3)
- **§10.8 Attacks** (partly implemented): firing does not spend ammunition (64 shared bolts, 22 pistol rounds are now counted)
- **§10.9 Call a Play** (missing): Call a Play (2d6 keep high, bonus to the proposed Action's Check or damage) is not a tool
- **§13.7 Traps** (missing): traps are items with a status; deploy, trigger, Check, damage and reset are not modelled
- **§15.2 Off-screen resolution** (GM ruling): needs the party Skill Check (7.8) for a group's outcome

### P2 (6)
- **§— Ai Favor Rerolls** (partly implemented): the reroll itself is not a tool: no reroll window, no 'Natural 1 cannot be rerolled' check, and the Favor is not spent automatically
- **§7.4 Opposed Skill Checks** (implemented, untested): no test of the opposed default
- **§7.7 Help and Intervene** (partly implemented): Intervene (an Action after the roll, +1d6) is not a tool
- **§10.2 Surprise** (implemented, untested): no test of either surprise case
- **§12.3 Looting and salvage** (GM ruling): no salvage step and no 'looted or salvaged once' guard
- **§14.2 Level awards** (partly implemented): award sizes (Quest >= 1; Boss tiers 1-6) are not checked

### P3 (11)
- **§— Recurring Character Completeness** (GM ruling): no check flags null combat-critical fields before an encounter
- **§4.1 Player character** (GM ruling): no integrity flag when narration gives Cliff dialogue or decisions
- **§5.10 Flavor cannot override agency or state** (partly implemented): flags warn only; nothing blocks the turn
- **§10.1 Establish the encounter** (partly implemented): positions, ranges and cover are not tracked
- **§10.11 NPC-versus-NPC resolution** (missing): the published d12 NPC-versus-NPC table is not a tool
- **§11.3 Injuries** (partly implemented): repeated injuries never become long-term
- **§12.2 Hotlist and access** (missing): the hotlist is on the sheets but retrieval Action costs are not enforced
- **§12.4 Achievement and loot-box continuity** (partly implemented): no check that a box is not opened twice or a reward duplicated
- **§13.3 Magical crafting gate** (partly implemented): from Floor 4: crafting tables, tier bonus and enchantment Difficulty are not a tool yet
- **§14.6 Grinding Skills** (implemented and tested): the Endurance Check beyond 5 hours is reminded, not rolled
- **§14.7 Leveling through grinding** (partly implemented): the sole-lowest-Level 1d2 catch-up is not applied

## All rules

| § | Rule | Status | How the console handles it | Tests | Gap | Priority |
|---|---|---|---|---|---|---|
| — | Mending | implemented and tested | advance_time mending -> state_ops.apply_mending | `test_mending_and_dungeon_day_rollover` |  |  |
| — | Dungeon Days | implemented and tested | state_ops.day_info, reset_uses on the 30-hour boundary | `test_mending_and_dungeon_day_rollover` |  |  |
| — | Grinding | implemented and tested | checks.grind: 5 safe hours a day, +1 credited hour with a Neighborhood Map | `test_grinding_triggers_one_check_per_day_and_level_progress` |  |  |
| — | Neighborhood Maps | GM ruling | loot is recorded with add_item |  |  |  |
| — | Ai Favor Rerolls | partly implemented | ai_favor op keeps the count |  | the reroll itself is not a tool: no reroll window, no 'Natural 1 cannot be rerolled' check, and the Favor is not spent automatically | P2 |
| — | Recurring Character Completeness | GM ruling | full sheets are in the briefing for present characters | `test_every_party_dossier_builds_without_gm_material` | no check flags null combat-critical fields before an encounter | P3 |
| — | Rng Fallback | implemented and tested | dice.csprng_die: the OS cryptographic source is the agreed fallback; Real_Random is not used | `test_csprng_covers_all_faces_and_stays_in_range`, `test_rolls_are_recorded_with_rng_source` |  |  |
| — | Transparency | implemented and tested | roll cards with modifiers; GM material never reaches player payloads; integrity flags invented d20s | `test_no_gm_material_in_any_player_response`, `test_player_payloads_never_carry_gm_material` |  |  |
| — | Productive Assistance | GM ruling | Help dice are part of skill_check (see 7.7) |  |  |  |
| — | Correspondence Advancement | implemented and tested | count_cycle tool, state/advancement.yaml as the live counter | `test_advancement_checkpoint`, `test_recap_does_not_advance_time_or_cycles`, `test_write_back_mirrors_the_live_advancement_count_into_campaign_current_state` |  |  |
| — | Legacy Adventure Beat Tracker | reference only | historical; superseded by correspondence advancement |  |  |  |
| 1 | Purpose | reference only |  |  |  |  |
| 2 | Authority and precedence | GM ruling | precedence order is in the briefing |  |  |  |
| 2.1 | Specific beats general | GM ruling |  |  |  |  |
| 2.2 | Canon already established | GM ruling |  |  |  |  |
| 2.3 | Rules labels | GM ruling | record_ruling tool stores rulings |  |  |  |
| 3 | Required GM turn-start procedure | implemented and tested | context builder loads checkpoint, clock, location, party, resources, quests, rules | `test_briefing_carries_what_the_gm_needs_and_the_continuity_report_passes` |  |  |
| 3.1 | Information boundaries | implemented and tested | player/GM visibility on every record; leak check on narration | `test_no_gm_material_in_any_player_response`, `test_frontend_bundle_contains_no_secrets` |  |  |
| 3.2 | Private character continuity and roleplay lookup | implemented and tested | private motives load only for relevant characters | `test_private_motivation_surfaces_only_when_the_character_is_relevant` |  |  |
| 4.1 | Player character | GM ruling | briefing rule |  | no integrity flag when narration gives Cliff dialogue or decisions | P3 |
| 4.2 | Allies and NPCs | GM ruling | NPC profiles and relationships in the briefing | `test_npc_relationship_evolves_without_losing_personality` |  |  |
| 4.3 | Pets and awakened animals | GM ruling | Scout and Vada have full crawler sheets |  |  |  |
| 4.4 | Large group rule | implemented and tested | party groups; only participants scale Mob actions | `test_groups_move_only_their_members`, `test_mob_evade_default_and_actions_for_elites` |  |  |
| 5 | Roleplay and Dungeon flavor are mechanics | GM ruling |  |  |  |  |
| 5.1 | Resolve the room before resolving the plan | GM ruling |  |  |  |  |
| 5.2 | Character presence requirement | GM ruling |  |  |  |  |
| 5.3 | Zoom rules | GM ruling |  |  |  |  |
| 5.4 | Sensory and physical grounding | GM ruling |  |  |  |  |
| 5.5 | The System AI is a recurring character | GM ruling |  |  |  |  |
| 5.6 | Achievement procedure | GM ruling | achievement op records recipient and reward; presentation is narration |  |  |  |
| 5.7 | Tooltips, loot, and System notices | GM ruling | system_notice tool |  |  |  |
| 5.8 | Audience and production pressure | GM ruling |  |  |  |  |
| 5.9 | Tone balance | GM ruling |  |  |  |  |
| 5.10 | Flavor cannot override agency or state | partly implemented | integrity flags narration that spends Gold, deals damage or passes time without a recorded change | `test_counting_a_cycle_on_a_recap_is_flagged` | flags warn only; nothing blocks the turn | P3 |
| 6.1 | Non-LLM RNG | implemented and tested | all chance through the server's dice | `test_csprng_covers_all_faces_and_stays_in_range` |  |  |
| 6.2 | Roll timing | implemented and tested | checks declare Stat, Skill, Advantage and Difficulty before rolling | `test_skill_check_math_default_dc_and_mark` |  |  |
| 6.3 | Roll reporting | implemented and tested | roll card: die + Rank + Mod + other = total -> degree | `test_skill_check_math_default_dc_and_mark` |  |  |
| 6.4 | No invisible fudging | implemented and tested | rolls are immutable; replays reuse them; quoting an unrolled d20 is flagged | `test_dice_are_drawn_once_and_reused_by_every_replay`, `test_retried_request_is_not_applied_twice` |  |  |
| 7 | When to roll | GM ruling |  |  |  |  |
| 7.1 | Core Check | implemented and tested | checks.perform_check | `test_skill_check_math_default_dc_and_mark` |  |  |
| 7.2 | Stat Checks | implemented and tested | Stat Checks: Stat Mod only, no Help | `test_stat_check_cannot_receive_help` |  |  |
| 7.3 | Unopposed Skill Checks | implemented and tested | default_dc unopposed = 10 + 2 x Floor | `test_skill_check_math_default_dc_and_mark` |  |  |
| 7.4 | Opposed Skill Checks | implemented, untested | default_dc opposed = 10 + antagonist Mod + Floor |  | no test of the opposed default | P2 |
| 7.5 | Untrained Skills | implemented and tested | untrained: Disadvantage, Stat Mod only | `test_untrained_check_rolls_disadvantage_without_rank` |  |  |
| 7.6 | Advantage and Disadvantage | implemented and tested | Advantage and Disadvantage cancel one for one | `test_advantage_cancels_disadvantage` |  |  |
| 7.7 | Help and Intervene | partly implemented | Help: 1d6 with Ranks, 1d2 without, rolled per helper | `test_stat_check_cannot_receive_help` | Intervene (an Action after the roll, +1d6) is not a tool | P2 |
| 7.8 | Party Skill Checks | missing |  |  | no party Skill Check: each crawler is rolled separately; 'half succeed' and the Amazing/Major conversions are not computed | P1 |
| 7.9 | Degrees of outcome | implemented and tested | degree_of: Critical Hit to Critical Fail | `test_degrees_of_outcome` |  |  |
| 7.9 | Crazy Dungeon critical flourish | GM ruling | the flourish is narration on a natural 1 or 20 the server rolled |  |  |  |
| 7.10 | Failing forward | GM ruling |  |  |  |  |
| 8.1 | Dungeon time | implemented and tested | advance_time is the only clock; the write-back mirrors it | `test_time_advances_and_statuses_expire` |  |  |
| 8.2 | Narrative units | implemented and tested | 10-second rounds, two Actions each | `test_round_flow_evade_damage_attack_and_actions` |  |  |
| 8.3 | Time estimates | GM ruling |  |  |  |  |
| 8.4 | Parallel activity | GM ruling | one advance_time for the longest concurrent task |  |  |  |
| 8.5 | Montages | GM ruling |  |  |  |  |
| 9.1 | Exploration procedure | GM ruling |  |  |  |  |
| 9.2 | Discovery | GM ruling |  |  |  |  |
| 9.3 | Social play | GM ruling |  |  |  |  |
| 10.1 | Establish the encounter | partly implemented | combat_start records a setup description |  | positions, ranges and cover are not tracked | P3 |
| 10.2 | Surprise | implemented, untested | combat_start surprise: crawlers get one free Action; Mobs-first otherwise |  | no test of either surprise case | P2 |
| 10.3 | Round sequence | implemented and tested | declare -> evade/interrupts -> resolve -> crawler actions -> end round | `test_round_flow_evade_damage_attack_and_actions` |  |  |
| 10.4 | Action economy | implemented and tested | two Actions; an Interrupt spends one | `test_round_flow_evade_damage_attack_and_actions` |  |  |
| 10.5 | Mob Actions | implemented and tested | Elites and Bosses act once per participating crawler | `test_mob_evade_default_and_actions_for_elites` |  |  |
| 10.6 | Targeting | GM ruling | random targeting through roll_dice |  |  |  |
| 10.7 | Evade and Interrupts | partly implemented | Evade: one Check per incoming Attack | `test_round_flow_evade_damage_attack_and_actions`, `test_evade_major_fail_adds_floor_damage_and_injury_after_combat` | Catcher and the other class Interrupts are not tools (Marcus and Terri have Catcher +3) | P1 |
| 10.8 | Attacks | partly implemented | attack vs Evade; natural 20 doubles base dice; Amazing adds Floor damage | `test_book_damage_examples` | firing does not spend ammunition (64 shared bolts, 22 pistol rounds are now counted) | P1 |
| 10.9 | Call a Play | missing |  |  | Call a Play (2d6 keep high, bonus to the proposed Action's Check or damage) is not a tool | P1 |
| 10.10 | Look for Clues | GM ruling | an Unopposed skill_check |  |  |  |
| 10.11 | NPC-versus-NPC resolution | missing |  |  | the published d12 NPC-versus-NPC table is not a tool | P3 |
| 11.1 | Damage order | implemented and tested | combat._mitigate: DR, resistance, vulnerability, immunity, then slots | `test_book_damage_examples` |  |  |
| 11.2 | Zero Health and dying | implemented and tested | Dying countdown from CON, death, healing ends Dying | `test_dying_countdown_death_and_healing` |  |  |
| 11.3 | Injuries | partly implemented | Major Evade failures add an injury after combat; rest removes injuries | `test_evade_major_fail_adds_floor_damage_and_injury_after_combat` | repeated injuries never become long-term | P3 |
| 11.4 | Rest | implemented and tested | rest op: saferoom only; short and long rest heal and clear injuries | `test_rest_requires_saferoom_and_heals_by_slots` |  |  |
| 12.1 | Inventory integrity | implemented and tested | no overspending, no duplicates, unknown counts cannot be spent | `test_inventory_cannot_overspend_or_duplicate`, `test_unknown_quantity_cannot_be_spent_until_established` |  |  |
| 12.2 | Hotlist and access | missing |  |  | the hotlist is on the sheets but retrieval Action costs are not enforced | P3 |
| 12.3 | Looting and salvage | GM ruling | loot and salvage are recorded with add_item/remove_item |  | no salvage step and no 'looted or salvaged once' guard | P2 |
| 12.4 | Achievement and loot-box continuity | partly implemented | achievement op; boxes are items with an opened state |  | no check that a box is not opened twice or a reward duplicated | P3 |
| 13.1 | Declare before crafting | implemented and tested | craft tool: item, Skill, materials, time and Difficulty settled first (Table 43 or a declared project's design); nothing is spent unless every material is on hand with a known count | `test_gold_siphon_is_built_from_its_declared_design`, `test_failed_crafting_consumes_then_salvages_by_margin_and_nothing_is_spent_when_short` |  |  |
| 13.2 | Mundane crafting | implemented and tested | craft: common items cost 1 Misc. Junk; uncommon mundane items take Junk by size and size x size hours, Unopposed Fabricate (Engineering with moving parts) | `test_failed_crafting_consumes_then_salvages_by_margin_and_nothing_is_spent_when_short`, `test_gm_turn_crafts_the_siphon_once_with_one_set_of_rolls` |  |  |
| 13.3 | Magical crafting gate | partly implemented | craft refuses magical crafting below Floor 4 | `test_failed_crafting_consumes_then_salvages_by_margin_and_nothing_is_spent_when_short` | from Floor 4: crafting tables, tier bonus and enchantment Difficulty are not a tool yet | P3 |
| 13.4 | Legacy System-recognized equipment | GM ruling | grandfathered gear keeps its recorded effect on the sheet |  |  |  |
| 13.5 | Failure and salvage | implemented and tested | craft consumes materials on the attempt; Failure salvages by margin (Table 47: 1-2 all, 3-9 half, 10+ none) | `test_failed_crafting_consumes_then_salvages_by_margin_and_nothing_is_spent_when_short` |  |  |
| 13.6 | Assistance | implemented and tested | the lead crafter makes the one Check, with Help dice from assistants; a project's lead is enforced | `test_gold_siphon_is_built_from_its_declared_design` |  |  |
| 13.7 | Traps | missing |  |  | traps are items with a status; deploy, trigger, Check, damage and reset are not modelled | P1 |
| 14.1 | No experience-point total | reference only | there is no XP pool by design |  |  |  |
| 14.2 | Level awards | partly implemented | level op records source (quest, boss, grind, crawler kill) | `test_level_award_banks_stat_points_and_resets_grind_progress` | award sizes (Quest >= 1; Boss tiers 1-6) are not checked | P2 |
| 14.3 | Correspondence-play advancement checkpoints | implemented and tested | count_cycle; checkpoint at 12; overflow carries | `test_advancement_checkpoint`, `test_checkpoint_not_due_is_rejected` |  |  |
| 14.4 | Tutorial Floor Stat points | implemented and tested | banked Stat points from Levels; allocate_stats | `test_allocate_stats_requires_bank_and_keeps_modifier_unless_given` |  |  |
| 14.5 | Skill advancement through use | implemented and tested | marks on trained non-Passive Checks; Rank 0-4 at checkpoints, 5+ at floor end | `test_skill_check_math_default_dc_and_mark`, `test_floor_end_checks_marked_rank_five_plus` |  |  |
| 14.6 | Grinding Skills | implemented and tested | checks.grind | `test_grinding_triggers_one_check_per_day_and_level_progress` | the Endurance Check beyond 5 hours is reminded, not rolled | P3 |
| 14.7 | Leveling through grinding | partly implemented | level-grind counter and reset | `test_grinding_triggers_one_check_per_day_and_level_progress` | the sole-lowest-Level 1d2 catch-up is not applied | P3 |
| 14.8 | Mandatory pre-Floor-3 audit | reference only | one-time pre-Floor-3 audit, completed |  |  |  |
| 15.1 | Declare groups and objectives | implemented and tested | group op: members, location, activity | `test_groups_move_only_their_members`, `test_group_departure_moves_only_that_group` |  |  |
| 15.2 | Off-screen resolution | GM ruling | off-screen groups resolved with checks |  | needs the party Skill Check (7.8) for a group's outcome | P1 |
| 15.3 | Rewards and advancement | GM ruling |  |  |  |  |
| 16.1 | Write immediately after consequence | implemented and tested | every turn commits its validated changes at once | `test_turn_persists_transcript_and_state` |  |  |
| 16.2 | Persistence cadence | implemented and tested | autosave per turn; manual save points | `test_autosaves_are_pruned`, `test_save_change_restore_and_undo_restore` |  |  |
| 16.3 | Checkpoints | implemented and tested | save points; reviewed ledger write-back; hand-written checkpoint protected | `test_save_change_restore_and_undo_restore` |  |  |
| 16.4 | Corrections | implemented and tested | reconcile.py: owner-approved corrections with evidence and a save point | `test_gold_stabber_correction_is_evidenced_reversible_and_reaches_the_ledger` |  |  |
| 17 | GM fairness constraints | GM ruling | fairness rules in the briefing; dice are never the GM's |  |  |  |
| 18 | Compact gameplay checklist | GM ruling | the checklist is always in the briefing |  |  |  |
| 19.1 | Confirmed decisions | reference only |  |  |  |  |
| 19.2 | Required state migration | reference only | state migrations done in M1 |  |  |  |
| 20 | Primary published references consulted | reference only |  |  |  |  |
