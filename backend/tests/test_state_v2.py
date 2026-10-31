"""Kept state ops on the fictional demo campaign: groups, scenes, memory, commitments, item ids, interrupted turns."""
import pytest

from app.db import now_iso, one, rows, transaction
from app.gm.orchestrator import recover_interrupted_turns
from app.memory.context import build_context
from app.rules.engine import ChangeError, apply_change


def ch(conn, slug):
    return one(conn, "SELECT * FROM characters WHERE slug = ?", (slug,))


def do(conn, cid, *changes):
    with transaction(conn):
        return [apply_change(conn, cid, None, c) for c in changes]


def test_groups_move_only_their_members(conn, campaign_id):
    do(conn, campaign_id, {"op": "group", "key": "scouts", "label": "Scouts", "location": "Laundromat", "members": ["jonah"]},
       {"op": "group", "key": "home", "location": "Laundromat", "members": ["mara", "adeline"]})
    do(conn, campaign_id, {"op": "move_group", "key": "scouts", "destination": "Boiler Room", "reason": "sent ahead"})
    assert ch(conn, "jonah")["location"] == "Boiler Room"
    assert ch(conn, "mara")["location"] == "Laundromat" and ch(conn, "adeline")["location"] == "Laundromat"
    with pytest.raises(ChangeError):
        do(conn, campaign_id, {"op": "move_group", "key": "nobody", "destination": "x", "reason": "x"})
    ctx = build_context(conn, campaign_id, "I fold laundry")
    assert ctx["present"] == ["Mara Vance", "Adeline Winter"]   # Jonah is elsewhere
    assert "Scouts @ Boiler Room" in ctx["text"]


def test_scene_close_becomes_episodic_memory(conn, campaign_id):
    do(conn, campaign_id, {"op": "scene", "action": "start", "title": "Dryer heist", "location": "Laundromat", "participants": ["Mara Vance"]})
    do(conn, campaign_id, {"op": "scene", "action": "start", "title": "Aftermath", "summary": "Mara pried open dryer 7 and found a key."})
    mem = one(conn, "SELECT * FROM memories WHERE layer = 'episodic'")
    assert mem["subject"] == "Dryer heist" and "dryer 7" in mem["content"]
    assert "Earlier scenes: Dryer heist" in build_context(conn, campaign_id, "look")["text"]


def test_npc_memories_follow_the_character_into_context(conn, campaign_id):
    do(conn, campaign_id, {"op": "npc_attitude", "npc": "adeline", "delta": 1, "note": "tipped generously"},
       {"op": "remember", "subject": "adeline", "content": "Mara left a lavender soap tip.", "visibility": "player"},
       {"op": "remember", "subject": "adeline", "content": "Privately suspects Mara is after dryer 7.", "visibility": "gm"})
    prof = one(conn, "SELECT * FROM npc_profiles WHERE character_id = ?", (ch(conn, "adeline")["id"],))
    assert prof["attitude"] == 2 and prof["personality"].startswith("Patient")   # relationship evolved, personality kept
    text = build_context(conn, campaign_id, "I chat with Adeline")["text"]
    assert "[party saw] Mara left a lavender soap tip." in text and "[GM-ONLY] Privately suspects" in text


def test_commitments_reservations_and_project_details(conn, campaign_id):
    do(conn, campaign_id, {"op": "quest", "title": "Reserved: Sock Launcher", "kind": "reservation", "details": {"vendor": "Adeline", "price": 40},
                       "due_in_minutes": 90, "owner": "mara"},
       {"op": "quest", "title": "Build a sock-launcher (demo)", "details": {"status": "designed", "contributors": ["Mara", "Jonah"], "build_time": "4h"}})
    do(conn, campaign_id, {"op": "quest", "title": "Build a sock-launcher (demo)", "progress": "frame done", "details": {"status": "under construction"}})
    proj = one(conn, "SELECT * FROM quests WHERE title = 'Build a sock-launcher (demo)'")
    assert proj["details"] == {"status": "under construction", "contributors": ["Mara", "Jonah"], "build_time": "4h"}
    assert not one(conn, "SELECT id FROM items WHERE name LIKE '%Sock Launcher%'")   # a reservation is never an owned item
    text = build_context(conn, campaign_id, "status")["text"]
    assert "[reservation, P2] Reserved: Sock Launcher [due in 1h30m] — holder: Mara Vance" in text


def test_items_by_stable_id(conn, campaign_id):
    potion = one(conn, "SELECT id FROM items WHERE name = 'Healing Potion'")["id"]
    do(conn, campaign_id, {"op": "remove_item", "character": "mara", "name": "anything", "item_id": potion})
    assert one(conn, "SELECT quantity FROM items WHERE id = ?", (potion,))["quantity"] == 1
    with pytest.raises(ChangeError, match="not carried"):
        do(conn, campaign_id, {"op": "remove_item", "character": "jonah", "name": "x", "item_id": potion})


def test_interrupted_turns_are_failed_at_startup(conn, campaign_id):
    conn.execute("INSERT INTO turns (campaign_id, idempotency_key, status, player_input, created_at) VALUES (?,?,?,?,?)",
                 (campaign_id, "k-interrupted", "processing", "x", now_iso()))
    assert recover_interrupted_turns(conn) == 1
    assert rows(conn, "SELECT status, error FROM turns")[0]["error"].startswith("Interrupted")

