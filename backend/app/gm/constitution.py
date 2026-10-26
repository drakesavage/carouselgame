"""The default GM constitution for a campaign started in the console (campaigns.py), so a new campaign gets the same
procedure an imported ledger brings with its CORE_MECHANICS.md.

constitution.json holds the universal sections of the owner's Denton constitution, generalized (the player character
instead of Cliff; the app's records, save points and tools instead of ledger files and Git). Sections flagged `always`
go to the GM every turn; the rest are found with search_rules. Two sections written for the app from the published
books add what a fresh campaign needs on top: the canonical First Floor opening and recording the people met.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SOURCE = "Crawler Console default constitution"

APP_SECTIONS: list[tuple[str, str, str]] = [  # (section, title, text); always included
    ("D3", "Entering the World Dungeon (First Floor)", """\
Canon for the opening of a campaign:
- The entrance slams shut behind the crawler and the System speaks: "Welcome, Crawler. Welcome to the First Floor."
  The crawler receives a crawler name and number, is Level 1, assigned the Race of Human ("probably"), with stats
  assigned from their physical and mental profile (Crawlers & Customization p. 8; Atlas p. 14). Play this as a
  System sequence, not narration.
- The First Floor collapses 5 days after the Dungeon opens; crawlers get no Gold on it; stairwells can be entered
  any time; Bosses stay in their chambers (Game Master's Campaign Toolkit p. 19). Keep the floor clock running.
- Tutorial Guilds are seeded throughout Floors 1-2 and must be found in the halls (Core p. 17; Atlas p. 14;
  Toolkit p. 11). Do not put one at the foot of the entrance stairs: the halls, the dark and the first Mobs come
  first ("neighborhood trouble", Toolkit p. 8).
- Character creation can happen when the crawler meets their Game Guide (Toolkit p. 8). Record the result with
  apply_changes op `initialize_sheet` (stats, modifiers, Health Bar, Mana, Level) as soon as it is settled.
- Following a Game Guide's advice usually grants 1 AI Favor if the crawler has none (Toolkit p. 11)."""),
    ("D4", "People are records", """\
Every named person who talks with the party or matters to the story gets apply_changes op `npc` in the turn they
are met: name, role (e.g. "Game Guide"), what the party saw (description), where they are, personality and voice
as observed. Update the same record when the party learns more; use `npc_attitude` for later shifts. If it is not
recorded, the People page and later turns do not know the person exists."""),
]


def sections() -> list[dict]:
    data = json.loads((Path(__file__).with_name("constitution.json")).read_text(encoding="utf-8"))
    return data + [{"section": s, "title": t, "text": x, "label": "CAMPAIGN", "always": True} for s, t, x in APP_SECTIONS]


def install(conn: sqlite3.Connection, cid: int) -> int:
    """Give a campaign the default constitution, replacing an older copy of it. A campaign with its own constitution (an
    imported CORE_MECHANICS.md) is left alone. Returns how many sections are now installed (0 when left alone)."""
    if conn.execute("SELECT 1 FROM house_rules WHERE campaign_id = ? AND source = 'CORE_MECHANICS.md' LIMIT 1", (cid,)).fetchone():
        return 0
    conn.execute("DELETE FROM house_rules WHERE campaign_id = ? AND source = ?", (cid, SOURCE))
    rules = sections()
    for r in rules:
        conn.execute("""INSERT INTO house_rules (campaign_id, section, title, text, label, always_include, status, mechanical_status, source)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (cid, None if r["section"] == "CR" else r["section"], r["title"], r["text"], r["label"], int(r["always"]),
                      "canonical", "narrative_only", SOURCE))
    return len(rules)
