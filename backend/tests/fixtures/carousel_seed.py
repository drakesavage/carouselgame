"""Test-only Carousel campaign: player character Mara, companion Jonah, resident Adeline (with a hidden trope),
on the mini reference data (tests/carousel/mini_data.py), standing on the Town Square and knowing the Lodge and the Diner."""
from __future__ import annotations

import json
import sqlite3

from app.db import next_campaign_id, now_iso, transaction
from app.memory.snapshots import create_save_point

NAME = "Test Town"


def seed_carousel(conn: sqlite3.Connection) -> int:
    t = now_iso()
    with transaction(conn):
        cid = next_campaign_id(conn)
        conn.execute("""INSERT INTO campaigns (id, name, is_demo, data_source, current_location, safe_room, game_time_minutes, active_objective,
                                               node_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                     (cid, NAME, 1, "test", "Town Square", 1, 600, "Get everyone to Dyer's Lodge before dark.", "CAR-002", t, t))
        for place in ("CAR-047", "CAR-002", "CAR-006"):
            conn.execute("INSERT INTO known_places (campaign_id, place_id, how, known_at_minutes) VALUES (?,?,?,?)", (cid, place, "start", 600))

        def char(slug, name, kind, cat, stats, role, order, bio, in_party=1):
            return conn.execute(
                """INSERT INTO characters (campaign_id, slug, name, kind, roster_category, status, in_party, party_order,
                                           mettle, moxie, hustle, savvy, grit, role_id, role_reason, ruleset_version, bio, provenance)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, slug, name, kind, cat, "active", in_party, order, *(stats or (None,) * 5), role,
                 "Kept her head when the radio died." if role else None, "SB-0.4.1/role-0.4.2" if role else None, bio, "test")).lastrowid

        pc = char("mara", "Mara Vance", "pc", "player_character", (2, 2, 3, 2, 2), "base.final-girl", 0, "Fictional test newcomer.")
        jonah = char("jonah", "Jonah Reyes", "pc", "core_party", None, None, 1, "Fictional test friend.")
        adeline = char("adeline", "Adeline Winter", "npc", "allied_npcs", None, None, 99, "Leads the camp.", in_party=0)
        conn.execute("UPDATE campaigns SET active_character_id = ? WHERE id = ?", (pc, cid))
        conn.execute("""INSERT INTO character_tickets (campaign_id, character_id, ticket_ref, equipped, uses_left, uses_max, recharge, source)
                        VALUES (?,?,?,?,?,?,?,?)""", (cid, pc, "hb.ticket.base.final-girl", 1, 1, 1, "story", "starting_signature"))
        conn.execute("""INSERT INTO character_tickets (campaign_id, character_id, ticket_ref, equipped, hidden, source)
                        VALUES (?,?,?,?,?,?)""", (cid, adeline, "trope:1", 1, 1, "test"))
        for owner, name, qty, eq, slot, desc in [(pc, "Flashlight", 1, 1, "carried", "Flickers when it matters."),
                                                 (pc, "Healing Potion", 2, 0, None, "Camp-brewed; tastes of cough syrup."),
                                                 (jonah, "Pocket Knife", 1, 1, "carried", None),
                                                 (None, "Gold", 120, 0, None, None), (None, "Bandage", 4, 0, None, None),
                                                 (None, "Mystery Detergent", None, 0, None, "Count unknown (an unquantified item).")]:
            conn.execute("""INSERT INTO items (campaign_id, owner_character_id, name, quantity, equipped, slot, description)
                            VALUES (?,?,?,?,?,?,?)""", (cid, owner, name, qty, eq, slot, desc))
        conn.execute("INSERT INTO npc_profiles (character_id, personality, voice, known_motivation, attitude, ref_id) VALUES (?,?,?,?,?,?)",
                     (adeline, "Patient in public, hard underneath.", "Measured, never hurried.", "Keep the camp alive.", 1, "CAR-NPC-001"))
        conn.execute("INSERT INTO locations (campaign_id, name, description, ref_id) VALUES (?,?,?,?)",
                     (cid, "Town Square", "A safe zone between stories.", "CAR-002"))
        conn.execute("INSERT INTO gm_secrets (campaign_id, subject, kind, content, created_at) VALUES (?,?,?,?,?)",
                     (cid, "adeline", "motivation", "DEMO SECRET: Adeline knows the lodge key is hidden in the boathouse.", t))
        for title, kind, prio in [("Reach Dyer's Lodge", "quest", 1), ("Build a Sock Launcher", "project", 2)]:
            conn.execute("INSERT INTO quests (campaign_id, kind, title, status, priority, updated_at) VALUES (?,?,?,?,?,?)",
                         (cid, kind, title, "active", prio, t))
        conn.execute("""INSERT INTO house_rules (campaign_id, section, title, text, label, always_include, mechanical_status, source)
                        VALUES (?,?,?,?,?,?,?,?)""",
                     (cid, "T1", "Test house rule", "Test only: the diner is neutral ground.", "CAMPAIGN", 1, "narrative_only", "test"))
        conn.execute("INSERT INTO messages (campaign_id, role, content, meta_json, created_at) VALUES (?,?,?,?,?)",
                     (cid, "system", "**TEST CAMPAIGN.** Fictional sample data.", json.dumps({"style": "notice"}), t))
        create_save_point(conn, cid, "Test start", kind="manual")
    return cid
