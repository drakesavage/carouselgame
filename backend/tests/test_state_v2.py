"""Schema-v2 mechanics on the fictional demo campaign: training, awards, groups, scenes, memory, limited uses, rest,
mending, dungeon days, grinding, floor-end checks, commitments, item ids, interrupted turns."""
import pytest

from app.db import now_iso, one, rows, transaction
from app.gm.orchestrator import recover_interrupted_turns
from app.memory.context import build_context
from app.rules.checks import grind, resolve_floor_end
from app.rules.dice import DiceRoller
from app.rules.engine import ChangeError, apply_change
from conftest import scripted


def ch(conn, slug):
    return one(conn, "SELECT * FROM characters WHERE slug = ?", (slug,))


def do(conn, cid, *changes):
    with transaction(conn):
        return [apply_change(conn, cid, None, c) for c in changes]


def test_training_raises_ranks_with_floor_cap_and_learns_new_skills(conn, demo_id):
    do(conn, demo_id, {"op": "skill", "character": "juniper", "name": "Negotiation", "delta": 1, "source": "training", "reason": "Deathstroke Hall drills"})
    assert one(conn, "SELECT rank FROM skills WHERE name = 'Negotiation'")["rank"] == 4
    do(conn, demo_id, {"op": "skill", "character": "juniper", "name": "Lockpicking", "rank": 1, "source": "training", "reason": "Twilight Tinkerers"})
    assert one(conn, "SELECT rank, key FROM skills WHERE name = 'Lockpicking'")["key"] == "lockpicking"
    with pytest.raises(ChangeError, match="cap"):
        do(conn, demo_id, {"op": "skill", "character": "juniper", "name": "Negotiation", "rank": 16, "source": "training", "reason": "too far"})
    do(conn, demo_id, {"op": "skill", "character": "juniper", "name": "Negotiation", "rank": 16, "source": "class", "reason": "class allowance",
                       "cap_override": "Class benefit: may advance to Rank 20"})
    with pytest.raises(ChangeError, match="correction"):
        do(conn, demo_id, {"op": "skill", "character": "juniper", "name": "Negotiation", "rank": 2, "source": "training", "reason": "oops"})


def test_level_award_banks_stat_points_and_resets_grind_progress(conn, demo_id):
    conn.execute("UPDATE characters SET derived_json = json_set(derived_json, '$.level_progress', json('{\"current\": 4, \"target\": 6}')) WHERE slug = 'juniper'")
    do(conn, demo_id, {"op": "level", "character": "juniper", "delta": 1, "source": "quest", "reason": "Found the stairwell"})
    j = ch(conn, "juniper")
    assert j["level"] == 7 and j["derived"]["unallocated_stat_points"] == 3 and j["derived"]["level_progress"]["current"] == 0
    do(conn, demo_id, {"op": "ai_favor", "character": "juniper", "delta": 1, "reason": "Human level-up"})
    assert ch(conn, "juniper")["ai_favor"] == 2
    with pytest.raises(ChangeError):
        do(conn, demo_id, {"op": "ai_favor", "character": "juniper", "delta": -5, "reason": "reroll"})


def test_groups_move_only_their_members(conn, demo_id):
    do(conn, demo_id, {"op": "group", "key": "scouts", "label": "Scouts", "location": "Laundromat", "members": ["bix"]},
       {"op": "group", "key": "home", "location": "Laundromat", "members": ["juniper", "glimmet"]})
    do(conn, demo_id, {"op": "move_group", "key": "scouts", "destination": "Boiler Room", "reason": "sent ahead"})
    assert ch(conn, "bix")["location"] == "Boiler Room"
    assert ch(conn, "juniper")["location"] == "Laundromat" and ch(conn, "glimmet")["location"] == "Laundromat"
    with pytest.raises(ChangeError):
        do(conn, demo_id, {"op": "move_group", "key": "nobody", "destination": "x", "reason": "x"})
    ctx = build_context(conn, demo_id, "I fold laundry")
    assert ctx["present"] == ["Juniper Vale (demo)", "Proprietor Glimmet (demo)"]   # Bix is elsewhere
    assert "Scouts @ Boiler Room" in ctx["text"]


def test_scene_close_becomes_episodic_memory_and_resets_scene_uses(conn, demo_id):
    do(conn, demo_id, {"op": "scene", "action": "start", "title": "Dryer heist", "location": "Laundromat", "participants": ["Juniper Vale (demo)"]})
    do(conn, demo_id, {"op": "use_ability", "character": "juniper", "ability": "Make It Work", "per": "scene"})
    with pytest.raises(ChangeError, match="already used"):
        do(conn, demo_id, {"op": "use_ability", "character": "juniper", "ability": "Make It Work", "per": "scene"})
    do(conn, demo_id, {"op": "scene", "action": "start", "title": "Aftermath", "summary": "Juniper pried open dryer 7 and found a key."})
    mem = one(conn, "SELECT * FROM memories WHERE layer = 'episodic'")
    assert mem["subject"] == "Dryer heist" and "dryer 7" in mem["content"]
    do(conn, demo_id, {"op": "use_ability", "character": "juniper", "ability": "Make It Work", "per": "scene"})   # new scene: available again
    assert "Earlier scenes: Dryer heist" in build_context(conn, demo_id, "look")["text"]


def test_npc_memories_follow_the_character_into_context(conn, demo_id):
    do(conn, demo_id, {"op": "npc_attitude", "npc": "glimmet", "delta": 1, "note": "tipped generously"},
       {"op": "remember", "subject": "glimmet", "content": "Juniper left a lavender soap tip.", "visibility": "player"},
       {"op": "remember", "subject": "glimmet", "content": "Privately suspects Juniper is after dryer 7.", "visibility": "gm"})
    prof = one(conn, "SELECT * FROM npc_profiles WHERE character_id = ?", (ch(conn, "glimmet")["id"],))
    assert prof["attitude"] == 2 and prof["personality"].startswith("Fussy")   # relationship evolved, personality kept
    text = build_context(conn, demo_id, "I chat with Glimmet")["text"]
    assert "[party saw] Juniper left a lavender soap tip." in text and "[GM-ONLY] Privately suspects" in text


def test_rest_requires_saferoom_and_heals_by_slots(conn, demo_id):
    conn.execute("UPDATE characters SET hp_current = 10, mana_current = 2 WHERE slug = 'juniper'")
    conn.execute("INSERT INTO status_effects (character_id, name, polarity) VALUES (?, 'Minor Injury', 'debuff')", (ch(conn, "juniper")["id"],))
    t0 = one(conn, "SELECT game_time_minutes FROM campaigns WHERE id = ?", (demo_id,))["game_time_minutes"]
    do(conn, demo_id, {"op": "rest", "kind": "short", "characters": ["juniper"], "reason": "nap"})
    j = ch(conn, "juniper")
    # CON Mod 2 -> 2-point slots: 10 HP = 5 slots, +5 slots = 10 slots = 20 HP; Mana +half of 12
    assert (j["hp_current"], j["mana_current"]) == (20, 8)
    assert not one(conn, "SELECT id FROM status_effects WHERE name = 'Minor Injury'")
    assert one(conn, "SELECT game_time_minutes FROM campaigns WHERE id = ?", (demo_id,))["game_time_minutes"] == t0 + 120
    conn.execute("UPDATE campaigns SET safe_room = 0 WHERE id = ?", (demo_id,))
    with pytest.raises(ChangeError, match="saferoom"):
        do(conn, demo_id, {"op": "rest", "kind": "long", "reason": "in the open"})


def test_mending_and_dungeon_day_rollover(conn, demo_id):
    conn.execute("UPDATE characters SET hp_current = 20, mana_current = 0 WHERE slug = 'juniper'")
    conn.execute("UPDATE campaigns SET game_time_minutes = 1700 WHERE id = ?", (demo_id,))
    do(conn, demo_id, {"op": "use_ability", "character": "juniper", "ability": "Rank-2 untrained", "per": "day"})
    do(conn, demo_id, {"op": "advance_time", "minutes": 180, "reason": "quiet tinkering", "mending": ["juniper"]})
    j = ch(conn, "juniper")
    assert (j["hp_current"], j["mana_current"]) == (26, 12)   # 3 hours: +3 slots (2 each), +15 Mana capped at 12
    assert one(conn, "SELECT summary FROM events WHERE type = 'dungeon_day'")["summary"].startswith("Dungeon day 2")
    do(conn, demo_id, {"op": "use_ability", "character": "juniper", "ability": "Rank-2 untrained", "per": "day"})   # reset at the new day


def test_grinding_triggers_one_check_per_day_and_level_progress(conn, demo_id):
    conn.execute("UPDATE characters SET derived_json = json_set(derived_json, '$.level_progress', json('{\"current\": 4, \"target\": 6}')) WHERE slug = 'juniper'")
    with transaction(conn):
        out = grind(conn, conn, DiceRoller(source=scripted([12])), demo_id, None, "juniper", "Engineering", 2)
    assert one(conn, "SELECT rank FROM skills WHERE name = 'Engineering'")["rank"] == 3      # 2h reached Rank 2 -> d20 12 >= 2
    assert ch(conn, "juniper")["level"] == 7                                                 # level grind 4+2 >= 6
    with transaction(conn):
        again = grind(conn, conn, DiceRoller(source=scripted([20])), demo_id, None, "juniper", "Engineering", 6)
    assert "already had its grinding advancement check today" in " ".join(again["lines"])
    assert "beyond the safe 5" in " ".join(again["lines"])
    assert out["excess_hours"] == 0


def test_floor_end_checks_marked_rank_five_plus(conn, demo_id):
    conn.execute("UPDATE skills SET rank = 6, marked = 1 WHERE name = 'Stealth'")
    with transaction(conn):
        out = resolve_floor_end(conn, conn, DiceRoller(source=scripted([3, 9])), demo_id, None, "floor 3 ends",
                                advantage=[{"character": "bix", "skill": "Stealth"}])
    s = one(conn, "SELECT rank, marked FROM skills WHERE name = 'Stealth'")
    assert (s["rank"], s["marked"]) == (7, 0) and "Advantage" in out["lines"][0]   # keep 9 >= 6


def test_commitments_reservations_and_project_details(conn, demo_id):
    do(conn, demo_id, {"op": "quest", "title": "Reserved: Sock Launcher", "kind": "reservation", "details": {"vendor": "Glimmet", "price": 40},
                       "due_in_minutes": 90, "owner": "juniper"},
       {"op": "quest", "title": "Build a sock-launcher (demo)", "details": {"status": "designed", "contributors": ["Juniper", "Bix"], "build_time": "4h"}})
    do(conn, demo_id, {"op": "quest", "title": "Build a sock-launcher (demo)", "progress": "frame done", "details": {"status": "under construction"}})
    proj = one(conn, "SELECT * FROM quests WHERE title = 'Build a sock-launcher (demo)'")
    assert proj["details"] == {"status": "under construction", "contributors": ["Juniper", "Bix"], "build_time": "4h"}
    assert not one(conn, "SELECT id FROM items WHERE name LIKE '%Sock Launcher%'")   # a reservation is never an owned item
    text = build_context(conn, demo_id, "status")["text"]
    assert "[reservation, P2] Reserved: Sock Launcher [due in 1h30m] — holder: Juniper Vale (demo)" in text


def test_items_by_stable_id(conn, demo_id):
    potion = one(conn, "SELECT id FROM items WHERE name = 'Healing Potion'")["id"]
    do(conn, demo_id, {"op": "remove_item", "character": "juniper", "name": "anything", "item_id": potion})
    assert one(conn, "SELECT quantity FROM items WHERE id = ?", (potion,))["quantity"] == 1
    with pytest.raises(ChangeError, match="not carried"):
        do(conn, demo_id, {"op": "remove_item", "character": "bix", "name": "x", "item_id": potion})


def test_interrupted_turns_are_failed_at_startup(conn, demo_id):
    conn.execute("INSERT INTO turns (campaign_id, idempotency_key, status, player_input, created_at) VALUES (?,?,?,?,?)",
                 (demo_id, "k-interrupted", "processing", "x", now_iso()))
    assert recover_interrupted_turns(conn) == 1
    assert rows(conn, "SELECT status, error FROM turns")[0]["error"].startswith("Interrupted")


def test_a_rest_is_counted_once_on_the_clock(conn, demo_id):
    from app.rules.engine import ChangeError, apply_change
    from app.db import one, transaction
    with transaction(conn):
        conn.execute("UPDATE campaigns SET safe_room = 1 WHERE id = ?", (demo_id,))
        tid = conn.execute("INSERT INTO turns (campaign_id, idempotency_key, player_input, status, created_at) VALUES (?, hex(randomblob(8)), 'rest', 'processing', '2026')", (demo_id,)).lastrowid
    t = lambda: one(conn, "SELECT game_time_minutes FROM campaigns WHERE id = ?", (demo_id,))["game_time_minutes"]
    t0 = t()
    with transaction(conn):   # the GM advanced the night first, then applied the rest: 8 hours, not 16 (playtest 1, H1)
        apply_change(conn, demo_id, tid, {"op": "advance_time", "minutes": 480, "reason": "overnight long rest"})
        apply_change(conn, demo_id, tid, {"op": "rest", "kind": "long", "reason": "overnight"})
    assert t() == t0 + 480
    with transaction(conn):
        tid2 = conn.execute("INSERT INTO turns (campaign_id, idempotency_key, player_input, status, created_at) VALUES (?, hex(randomblob(8)), 'rest', 'processing', '2026')", (demo_id,)).lastrowid
    with transaction(conn):   # rest first, then an extra advance for the same rest is refused
        apply_change(conn, demo_id, tid2, {"op": "rest", "kind": "short", "reason": "patch up"})
        with pytest.raises(ChangeError, match="already advanced"):
            apply_change(conn, demo_id, tid2, {"op": "advance_time", "minutes": 120, "reason": "short rest"})
        apply_change(conn, demo_id, tid2, {"op": "advance_time", "minutes": 30, "reason": "walk to the market"})   # other time still passes
    assert t() == t0 + 480 + 120 + 30
