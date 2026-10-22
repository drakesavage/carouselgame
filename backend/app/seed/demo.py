"""Clearly labelled FICTIONAL demo campaign for mock-mode testing. It contains no real campaign facts and uses
made-up characters, so experiments never touch the imported Denton campaign."""
from __future__ import annotations

import json
import sqlite3

from ..db import now_iso, transaction
from ..memory.snapshots import create_save_point

DEMO_NAME = "DEMO — Fictional Sample Crawl"


def seed_demo(conn: sqlite3.Connection) -> int:
    existing = conn.execute("SELECT id FROM campaigns WHERE name = ? AND is_demo = 1", (DEMO_NAME,)).fetchone()
    if existing:
        return existing["id"]
    t = now_iso()
    with transaction(conn):
        cid = conn.execute(
            """INSERT INTO campaigns (name, is_demo, data_source, current_floor, current_location, safe_room, game_time_minutes,
                                      floor_deadline_minutes, active_objective, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (DEMO_NAME, 1, "demo", 3, "Laundromat of Infinite Socks (demo)", 1, 600, 600 + 8 * 1800,
             "Demo objective: find the stairwell before the sponsor loses interest.", t, t)).lastrowid

        def char(slug, name, kind, cat, race, cls, level, hp, mana, mods, order, bio, in_party=1):
            stats = {k: 10 + 2 * v for k, v in mods.items()}
            return conn.execute(
                """INSERT INTO characters (campaign_id, slug, name, kind, roster_category, species, race, class_name, level, status,
                                           in_party, party_order, hp_current, hp_max, mana_current, mana_max, ai_favor, stats_json,
                                           derived_json, sheet_json, bio, provenance)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, slug, name, kind, cat, race, race, cls, level, "active", in_party, order, hp[0], hp[1], mana[0], mana[1], 1,
                 json.dumps({"stats": stats, "modifiers": mods}), json.dumps({"evade": 3, "damage_resistance": 1, "move": 20, "step": 10}),
                 json.dumps({"narrative": {"current_role": bio}}), bio, "demo")).lastrowid

        pc = char("juniper", "Juniper Vale (demo)", "crawler", "player_character", "Human", "Coupon Necromancer", 6, (24, 30), (10, 12),
                  {"strength": 1, "intelligence": 4, "constitution": 2, "dexterity": 3, "charisma": 3}, 0, "Fictional demo crawler.")
        bix = char("bix", "Bix (demo)", "crawler", "pets_companions", "Awakened Ferret", "Pocket Saboteur", 6, (18, 18), (6, 6),
                   {"strength": 0, "intelligence": 2, "constitution": 1, "dexterity": 5, "charisma": 2}, 1, "Fictional demo companion.")
        glim = char("glimmet", "Proprietor Glimmet (demo)", "npc", "allied_npcs", "Gnome", None, None, (None, None), (None, None),
                    {}, 99, "Runs the demo laundromat saferoom.", in_party=0)
        conn.execute("UPDATE campaigns SET active_character_id = ? WHERE id = ?", (pc, cid))
        for owner, name, rank, kind, cost in [(pc, "Investigation", 4, "skill", None), (pc, "Negotiation", 3, "skill", None),
                                              (pc, "Engineering", 2, "skill", None), (pc, "Raise Receipt", 1, "spell", 3),
                                              (bix, "Stealth", 5, "skill", None), (bix, "Pickpocket", 3, "skill", None)]:
            conn.execute("INSERT INTO skills (character_id, kind, key, name, rank, mana_cost) VALUES (?,?,?,?,?,?)",
                         (owner, kind, name.lower().replace(" ", "-"), name, rank, cost))
        for owner, name, qty, eq, slot, desc in [(pc, "Folding Shopping Cart Shield", 1, 1, "worn", "+1 DR. Squeaks."),
                                                 (pc, "Healing Potion", 2, 0, None, "Restores 2d4+2 Health."),
                                                 (bix, "Tiny Lockpicks", 1, 1, "carried", None),
                                                 (None, "Gold", 120, 0, None, None), (None, "Bandage", 4, 0, None, None),
                                                 (None, "Mystery Detergent", None, 0, None, "Count unknown (demo of an unquantified item).")]:
            conn.execute("""INSERT INTO items (campaign_id, owner_character_id, name, quantity, equipped, slot, description)
                            VALUES (?,?,?,?,?,?,?)""", (cid, owner, name, qty, eq, slot, desc))
        conn.execute("INSERT INTO npc_profiles (character_id, personality, voice, known_motivation, attitude) VALUES (?,?,?,?,?)",
                     (glim, "Fussy, generous with soap, stingy with words.", "Clipped, speaks in laundry metaphors.", "Keep the machines running.", 1))
        conn.execute("INSERT INTO gm_secrets (campaign_id, subject, kind, content, created_at) VALUES (?,?,?,?,?)",
                     (cid, "glimmet", "motivation", "DEMO SECRET: Glimmet is hiding the stairwell key inside dryer #7.", t))
        for title, kind, prio in [("Find the stairwell (demo)", "quest", 1), ("Build a sock-launcher (demo)", "project", 2)]:
            conn.execute("INSERT INTO quests (campaign_id, kind, title, status, priority, updated_at) VALUES (?,?,?,?,?,?)",
                         (cid, kind, title, "active", prio, t))
        conn.execute("INSERT INTO clocks (campaign_id, key, label, kind, current, maximum, unit) VALUES (?,?,?,?,?,?,?)",
                     (cid, "advancement_cycles", "Advancement cycles", "advancement_checkpoint", 3, 12, "cycles"))
        conn.execute("""INSERT INTO house_rules (campaign_id, section, title, text, label, always_include, mechanical_status, source)
                        VALUES (?,?,?,?,?,?,?,?)""",
                     (cid, "D1", "Demo house rule", "Demo only: socks count as improvised ammunition.", "CAMPAIGN", 1, "narrative_only", "demo"))
        conn.execute("""INSERT INTO messages (campaign_id, role, content, meta_json, created_at) VALUES (?,?,?,?,?)""",
                     (cid, "system", "**DEMO CAMPAIGN.** Everything here is fictional sample data for testing the interface and mock GM. "
                                     "Your imported campaign is separate.", json.dumps({"style": "notice"}), t))
        create_save_point(conn, cid, "Demo start", kind="manual")
    return cid
