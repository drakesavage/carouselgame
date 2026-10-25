"""House-rule coverage matrix (roadmap M2): how the console handles each of the campaign's 104 house rules.

Status, from strongest to weakest:
  tested      the console enforces it and a test exercises it
  untested    the console enforces it, but no test covers it yet
  partial     part is enforced; `gap` says what is not
  gm_ruling   table procedure the GM applies with the existing tools (the rule is in the briefing or search_rules)
  reference   history, authority or bibliography: nothing to enforce
  missing     a mechanic the console should enforce and does not

`priority` ranks the gaps for M2 by what the next Floor 3 sessions need (crafting the Gold Siphon tonight, the recon
group off-screen, the Fallen Watch prison assault): P1 needed for those, P2 soon, P3 rare. Keys are the rule's
section number, or the ruling's title for state/campaign.yaml campaign_rulings (which have no section).
`tests` name test functions in backend/tests; tests/test_coverage.py proves each one exists and that every imported
rule has an entry.
"""
from __future__ import annotations

STATUSES = ("tested", "untested", "partial", "gm_ruling", "reference", "missing")
PLAN_CATEGORY = {"tested": "implemented and tested", "untested": "implemented, untested", "partial": "partly implemented",
                 "gm_ruling": "GM ruling", "reference": "reference only", "missing": "missing"}


def R(status: str, evidence: str = "", tests: tuple[str, ...] = (), gap: str = "", priority: str = "") -> dict:
    assert status in STATUSES
    return {"status": status, "evidence": evidence, "tests": list(tests), "gap": gap, "priority": priority}


MATRIX: dict[str, dict] = {
    # ---------------------------------------------------------------- state/campaign.yaml campaign_rulings
    "Mending": R("tested", "advance_time mending -> state_ops.apply_mending", ("test_mending_and_dungeon_day_rollover",)),
    "Dungeon Days": R("tested", "state_ops.day_info, reset_uses on the 30-hour boundary", ("test_mending_and_dungeon_day_rollover",)),
    "Grinding": R("tested", "checks.grind: 5 safe hours a day, +1 credited hour with a Neighborhood Map",
                  ("test_grinding_triggers_one_check_per_day_and_level_progress",)),
    "Neighborhood Maps": R("gm_ruling", "loot is recorded with add_item"),
    "Ai Favor Rerolls": R("partial", "ai_favor op keeps the count", gap="the reroll itself is not a tool: no reroll window, no "
                          "'Natural 1 cannot be rerolled' check, and the Favor is not spent automatically", priority="P2"),
    "Recurring Character Completeness": R("gm_ruling", "full sheets are in the briefing for present characters",
                                          ("test_every_party_dossier_builds_without_gm_material",),
                                          gap="no check flags null combat-critical fields before an encounter", priority="P3"),
    "Rng Fallback": R("tested", "dice.csprng_die: the OS cryptographic source is the agreed fallback; Real_Random is not used",
                      ("test_csprng_covers_all_faces_and_stays_in_range", "test_rolls_are_recorded_with_rng_source")),
    "Transparency": R("tested", "roll cards with modifiers; GM material never reaches player payloads; integrity flags invented d20s",
                      ("test_no_gm_material_in_any_player_response", "test_player_payloads_never_carry_gm_material")),
    "Productive Assistance": R("gm_ruling", "Help dice are part of skill_check (see 7.7)"),
    "Correspondence Advancement": R("tested", "count_cycle tool, state/advancement.yaml as the live counter",
                                    ("test_advancement_checkpoint", "test_recap_does_not_advance_time_or_cycles",
                                     "test_write_back_mirrors_the_live_advancement_count_into_campaign_current_state")),
    "Legacy Adventure Beat Tracker": R("reference", "historical; superseded by correspondence advancement"),
    # ---------------------------------------------------------------- CORE_MECHANICS.md
    "1": R("reference"),
    "2": R("gm_ruling", "precedence order is in the briefing"),
    "2.1": R("gm_ruling"),
    "2.2": R("gm_ruling"),
    "2.3": R("gm_ruling", "record_ruling tool stores rulings"),
    "3": R("tested", "context builder loads checkpoint, clock, location, party, resources, quests, rules",
           ("test_briefing_carries_what_the_gm_needs_and_the_continuity_report_passes",)),
    "3.1": R("tested", "player/GM visibility on every record; leak check on narration",
             ("test_no_gm_material_in_any_player_response", "test_frontend_bundle_contains_no_secrets")),
    "3.2": R("tested", "private motives load only for relevant characters", ("test_private_motivation_surfaces_only_when_the_character_is_relevant",)),
    "4.1": R("gm_ruling", "briefing rule", gap="no integrity flag when narration gives Cliff dialogue or decisions", priority="P3"),
    "4.2": R("gm_ruling", "NPC profiles and relationships in the briefing", ("test_npc_relationship_evolves_without_losing_personality",)),
    "4.3": R("gm_ruling", "Scout and Vada have full crawler sheets"),
    "4.4": R("tested", "party groups; only participants scale Mob actions",
             ("test_groups_move_only_their_members", "test_mob_evade_default_and_actions_for_elites")),
    "5": R("gm_ruling"), "5.1": R("gm_ruling"), "5.2": R("gm_ruling"), "5.3": R("gm_ruling"), "5.4": R("gm_ruling"),
    "5.5": R("gm_ruling"),
    "5.6": R("gm_ruling", "achievement op records recipient and reward; presentation is narration"),
    "5.7": R("gm_ruling", "system_notice tool"),
    "5.8": R("gm_ruling"), "5.9": R("gm_ruling"),
    "5.10": R("partial", "integrity flags narration that spends Gold, deals damage or passes time without a recorded change",
              ("test_counting_a_cycle_on_a_recap_is_flagged",), gap="flags warn only; nothing blocks the turn", priority="P3"),
    "6.1": R("tested", "all chance through the server's dice", ("test_csprng_covers_all_faces_and_stays_in_range",)),
    "6.2": R("tested", "checks declare Stat, Skill, Advantage and Difficulty before rolling", ("test_skill_check_math_default_dc_and_mark",)),
    "6.3": R("tested", "roll card: die + Rank + Mod + other = total -> degree", ("test_skill_check_math_default_dc_and_mark",)),
    "6.4": R("tested", "rolls are immutable; replays reuse them; quoting an unrolled d20 is flagged",
             ("test_dice_are_drawn_once_and_reused_by_every_replay", "test_retried_request_is_not_applied_twice")),
    "7": R("gm_ruling"),
    "7.1": R("tested", "checks.perform_check", ("test_skill_check_math_default_dc_and_mark",)),
    "7.2": R("tested", "Stat Checks: Stat Mod only, no Help", ("test_stat_check_cannot_receive_help",)),
    "7.3": R("tested", "default_dc unopposed = 10 + 2 x Floor", ("test_skill_check_math_default_dc_and_mark",)),
    "7.4": R("untested", "default_dc opposed = 10 + antagonist Mod + Floor", gap="no test of the opposed default", priority="P2"),
    "7.5": R("tested", "untrained: Disadvantage, Stat Mod only", ("test_untrained_check_rolls_disadvantage_without_rank",)),
    "7.6": R("tested", "Advantage and Disadvantage cancel one for one", ("test_advantage_cancels_disadvantage",)),
    "7.7": R("partial", "Help: 1d6 with Ranks, 1d2 without, rolled per helper", ("test_stat_check_cannot_receive_help",),
             gap="Intervene (an Action after the roll, +1d6) is not a tool", priority="P2"),
    "7.8": R("tested", "party_check tool: everyone rolls vs one Difficulty, no Help, half or more succeed; Amazing/Critical lifts one "
             "failure, Major/Critical Fail drags down another's Success (applied at once: INTERPRETATION)",
             ("test_party_check_tally_half_succeeds_with_conversions", "test_onscreen_party_check_rolls_everyone_and_shows_the_result")),
    "7.9": R("tested", "degree_of: Critical Hit to Critical Fail", ("test_degrees_of_outcome",)),
    "7.9 Crazy Dungeon critical flourish": R("gm_ruling", "the flourish is narration on a natural 1 or 20 the server rolled"),
    "7.10": R("gm_ruling"),
    "8.1": R("tested", "advance_time is the only clock; the write-back mirrors it", ("test_time_advances_and_statuses_expire",)),
    "8.2": R("tested", "10-second rounds, two Actions each", ("test_round_flow_evade_damage_attack_and_actions",)),
    "8.3": R("gm_ruling"), "8.4": R("gm_ruling", "one advance_time for the longest concurrent task"), "8.5": R("gm_ruling"),
    "9.1": R("gm_ruling"), "9.2": R("gm_ruling"), "9.3": R("gm_ruling"),
    "10.1": R("partial", "combat_start records a setup description", gap="positions, ranges and cover are not tracked", priority="P3"),
    "10.2": R("untested", "combat_start surprise: crawlers get one free Action; Mobs-first otherwise",
              gap="no test of either surprise case", priority="P2"),
    "10.3": R("tested", "declare -> evade/interrupts -> resolve -> crawler actions -> end round", ("test_round_flow_evade_damage_attack_and_actions",)),
    "10.4": R("tested", "two Actions; an Interrupt spends one", ("test_round_flow_evade_damage_attack_and_actions",)),
    "10.5": R("tested", "Elites and Bosses act once per participating crawler", ("test_mob_evade_default_and_actions_for_elites",)),
    "10.6": R("gm_ruling", "random targeting through roll_dice"),
    "10.7": R("partial", "Evade: one Check per incoming Attack", ("test_round_flow_evade_damage_attack_and_actions",
                                                                 "test_evade_major_fail_adds_floor_damage_and_injury_after_combat"),
              gap="Catcher and the other class Interrupts are not tools (Marcus and Terri have Catcher +3)", priority="P1"),
    "10.8": R("partial", "attack vs Evade; natural 20 doubles base dice; Amazing adds Floor damage", ("test_book_damage_examples",),
              gap="firing does not spend ammunition (64 shared bolts, 22 pistol rounds are now counted)", priority="P1"),
    "10.9": R("missing", gap="Call a Play (2d6 keep high, bonus to the proposed Action's Check or damage) is not a tool", priority="P1"),
    "10.10": R("gm_ruling", "an Unopposed skill_check"),
    "10.11": R("missing", gap="the published d12 NPC-versus-NPC table is not a tool", priority="P3"),
    "11.1": R("tested", "combat._mitigate: DR, resistance, vulnerability, immunity, then slots", ("test_book_damage_examples",)),
    "11.2": R("tested", "Dying countdown from CON, death, healing ends Dying", ("test_dying_countdown_death_and_healing",)),
    "11.3": R("partial", "Major Evade failures add an injury after combat; rest removes injuries",
              ("test_evade_major_fail_adds_floor_damage_and_injury_after_combat",),
              gap="repeated injuries never become long-term", priority="P3"),
    "11.4": R("tested", "rest op: saferoom only; short and long rest heal and clear injuries", ("test_rest_requires_saferoom_and_heals_by_slots",)),
    "12.1": R("tested", "no overspending, no duplicates, unknown counts cannot be spent",
              ("test_inventory_cannot_overspend_or_duplicate", "test_unknown_quantity_cannot_be_spent_until_established")),
    "12.2": R("missing", gap="the hotlist is on the sheets but retrieval Action costs are not enforced", priority="P3"),
    "12.3": R("gm_ruling", "loot and salvage are recorded with add_item/remove_item",
              gap="no salvage step and no 'looted or salvaged once' guard", priority="P2"),
    "12.4": R("partial", "achievement op; boxes are items with an opened state", gap="no check that a box is not opened twice "
              "or a reward duplicated", priority="P3"),
    "13.1": R("tested", "craft tool: item, Skill, materials, time and Difficulty settled first (Table 43 or a declared project's "
              "design); nothing is spent unless every material is on hand with a known count",
              ("test_gold_siphon_is_built_from_its_declared_design", "test_failed_crafting_consumes_then_salvages_by_margin_and_nothing_is_spent_when_short")),
    "13.2": R("tested", "craft: common items cost 1 Misc. Junk; uncommon mundane items take Junk by size and size x size hours, "
              "Unopposed Fabricate (Engineering with moving parts)",
              ("test_failed_crafting_consumes_then_salvages_by_margin_and_nothing_is_spent_when_short", "test_gm_turn_crafts_the_siphon_once_with_one_set_of_rolls")),
    "13.3": R("partial", "craft refuses magical crafting below Floor 4", ("test_failed_crafting_consumes_then_salvages_by_margin_and_nothing_is_spent_when_short",),
              gap="from Floor 4: crafting tables, tier bonus and enchantment Difficulty are not a tool yet", priority="P3"),
    "13.5": R("tested", "craft consumes materials on the attempt; Failure salvages by margin (Table 47: 1-2 all, 3-9 half, 10+ none)",
              ("test_failed_crafting_consumes_then_salvages_by_margin_and_nothing_is_spent_when_short",)),
    "13.6": R("tested", "the lead crafter makes the one Check, with Help dice from assistants; a project's lead is enforced",
              ("test_gold_siphon_is_built_from_its_declared_design",)),
    "13.4": R("gm_ruling", "grandfathered gear keeps its recorded effect on the sheet"),
    "13.7": R("missing", gap="traps are items with a status; deploy, trigger, Check, damage and reset are not modelled", priority="P1"),
    "14.1": R("reference", "there is no XP pool by design"),
    "14.2": R("partial", "level op records source (quest, boss, grind, crawler kill)", ("test_level_award_banks_stat_points_and_resets_grind_progress",),
              gap="award sizes (Quest >= 1; Boss tiers 1-6) are not checked", priority="P2"),
    "14.3": R("tested", "count_cycle; checkpoint at 12; overflow carries", ("test_advancement_checkpoint", "test_checkpoint_not_due_is_rejected")),
    "14.4": R("tested", "banked Stat points from Levels; allocate_stats", ("test_allocate_stats_requires_bank_and_keeps_modifier_unless_given",)),
    "14.5": R("tested", "marks on trained non-Passive Checks; Rank 0-4 at checkpoints, 5+ at floor end",
              ("test_skill_check_math_default_dc_and_mark", "test_floor_end_checks_marked_rank_five_plus")),
    "14.6": R("tested", "checks.grind", ("test_grinding_triggers_one_check_per_day_and_level_progress",),
              gap="the Endurance Check beyond 5 hours is reminded, not rolled", priority="P3"),
    "14.7": R("partial", "level-grind counter and reset", ("test_grinding_triggers_one_check_per_day_and_level_progress",),
              gap="the sole-lowest-Level 1d2 catch-up is not applied", priority="P3"),
    "14.8": R("reference", "one-time pre-Floor-3 audit, completed"),
    "15.1": R("tested", "group op: members, location, activity", ("test_groups_move_only_their_members", "test_group_departure_moves_only_that_group")),
    "15.2": R("tested", "party_check offscreen: only a group without the player character; objective and stakes declared before "
              "the roll; secret rolls; outcome logged for the GM", ("test_offscreen_recon_is_resolved_secretly_with_declared_stakes",)),
    "15.3": R("gm_ruling"),
    "16.1": R("tested", "every turn commits its validated changes at once", ("test_turn_persists_transcript_and_state",)),
    "16.2": R("tested", "autosave per turn; manual save points", ("test_autosaves_are_pruned", "test_save_change_restore_and_undo_restore")),
    "16.3": R("tested", "save points; reviewed ledger write-back; hand-written checkpoint protected",
              ("test_save_change_restore_and_undo_restore",)),
    "16.4": R("tested", "reconcile.py: owner-approved corrections with evidence and a save point",
              ("test_gold_stabber_correction_is_evidenced_reversible_and_reaches_the_ledger",)),
    "17": R("gm_ruling", "fairness rules in the briefing; dice are never the GM's"),
    "18": R("gm_ruling", "the checklist is always in the briefing"),
    "19.1": R("reference"), "19.2": R("reference", "state migrations done in M1"), "20": R("reference"),
}


def key_for(rule: dict) -> str:
    """'<section> <title>' when two rules share a section number (7.9), else the section, else the ruling's title."""
    both = f"{rule.get('section')} {rule.get('title')}"
    return both if both in MATRIX else rule.get("section") or rule.get("title") or ""


def classify(rule: dict) -> dict:
    return MATRIX.get(key_for(rule)) or R("missing", gap="not yet classified")


def mechanical_label(entry: dict) -> str:
    """The house_rules.mechanical_status vocabulary the briefing already uses."""
    return {"tested": "implemented", "untested": "implemented", "partial": "partial", "gm_ruling": "narrative_only",
            "reference": "narrative_only", "missing": "pending"}[entry["status"]]


def markdown(rules: list[dict]) -> str:
    """docs/RULES_COVERAGE.md from the imported rules (section, title) and this matrix."""
    from collections import Counter
    rows_ = [(r, classify(r)) for r in rules]
    count = Counter(e["status"] for _, e in rows_)
    out = ["# House-rule coverage matrix", "",
           "Generated from `backend/app/rules/coverage.py` (`python -m app.rules.coverage`); edit the matrix, not this file.", "",
           "| Status | Rules | Meaning |", "|---|---|---|"]
    for s in STATUSES:
        out.append(f"| {PLAN_CATEGORY[s]} | {count.get(s, 0)} | {_MEANING[s]} |")
    out += ["", "## Gaps by priority", ""]
    for p in ("P1", "P2", "P3"):
        gaps = [(r, e) for r, e in rows_ if e["priority"] == p]
        out.append(f"### {p} ({len(gaps)})")
        out += [f"- **§{r.get('section') or '—'} {r['title']}** ({PLAN_CATEGORY[e['status']]}): {e['gap']}" for r, e in gaps] + [""]
    out += ["## All rules", "", "| § | Rule | Status | How the console handles it | Tests | Gap | Priority |", "|---|---|---|---|---|---|---|"]
    for r, e in rows_:
        cell = lambda t: str(t).replace("|", "/")
        out.append(f"| {r.get('section') or '—'} | {cell(r['title'])} | {PLAN_CATEGORY[e['status']]} | {cell(e['evidence'])} | "
                   f"{', '.join(f'`{t}`' for t in e['tests'])} | {cell(e['gap'])} | {e['priority']} |")
    return "\n".join(out) + "\n"


_MEANING = {"tested": "the console enforces it and a test exercises it", "untested": "enforced, but no test yet",
            "partial": "part is enforced; the gap column says what is not", "gm_ruling": "table procedure the GM applies with the existing tools",
            "reference": "history, authority or bibliography", "missing": "a mechanic the console should enforce and does not"}


if __name__ == "__main__":
    import sqlite3
    import sys
    from pathlib import Path
    db = Path(sys.argv[1] if len(sys.argv) > 1 else "../data/campaign.db")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cid = conn.execute("SELECT id FROM campaigns WHERE is_demo = 0 ORDER BY id LIMIT 1").fetchone()[0]
    rules = [dict(r) for r in conn.execute("SELECT section, title FROM house_rules WHERE campaign_id = ? ORDER BY id", (cid,))]
    Path("../docs/RULES_COVERAGE.md").write_text(markdown(rules), encoding="utf-8")
    print(f"wrote docs/RULES_COVERAGE.md ({len(rules)} rules)")
