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
    ("D2", "The Collapse", """\
Canon for a campaign that starts on the surface (Atlas p. 3; Game Master's Campaign Toolkit p. 9):
- At the Collapse every structure with a roof is punched into the planet's crust at once, with anyone inside; only
  people outdoors survive. It is not an earthquake and nobody on the surface gets to treat it as one for long.
- In the same moment the System AI addresses every surviving human: phones, radios, speakers and a voice in the
  head. Deliver it with `system_notice` in the turn the buildings go down, in the System's own voice, paraphrasing
  the announcement (read_rule_page Atlas p. 3): the Borant Corporation, the televised World Dungeon of 18 floors,
  reclaiming the planet, parties welcome, find a stairwell before each floor collapses, find a tutorial guild.
- The first level opens about 18 seconds after the announcement ends: glowing dungeon entrances (stairwells down)
  appear in plain view near the survivors. Once the entrances close, no one may enter (Atlas p. 3); when the
  closing time matters, set it as a ruling and keep to it.
- From here the surface is a countdown, not a rescue scenario: no emergency services, no phones that connect, and
  the System, the entrances and other survivors keep the pressure on until the crawlers go down."""),
    ("D3","Entering the World Dungeon (First Floor)", """\
Canon for the opening of a campaign:
- The entrance slams shut behind the crawler and the System speaks: "Welcome, Crawler. Welcome to the First Floor."
  The crawler receives a crawler name and number, is Level 1, assigned the Race of Human ("probably"), with stats
  assigned from their physical and mental profile (Crawlers & Customization p. 8; Atlas p. 14). Play this as a
  System sequence, not narration.
- The First Floor collapses 5 days after the Dungeon opens; crawlers get no Gold on it; stairwells can be entered
  any time; Bosses stay in their chambers (Game Master's Campaign Toolkit p. 19). Keep the floor clock running.
  If the briefing says the floor clock is NOT STARTED, start it with apply_changes op `start_floor_clock` in the turn
  the player character enters the Dungeon (elapsed_minutes for any time already spent inside), and announce it.
- Tutorial Guilds are seeded throughout Floors 1-2 and must be found in the halls (Core p. 17; Atlas p. 14;
  Toolkit p. 11). Do not put one at the foot of the entrance stairs: the halls, the dark and the first Mobs come
  first ("neighborhood trouble", Toolkit p. 8).
- Character creation can happen when the crawler meets their Game Guide (Toolkit p. 8). Record the result with
  apply_changes op `initialize_sheet` (stats, modifiers, Health Bar, Mana, Level) as soon as it is settled.
- Following a Game Guide's advice usually grants 1 AI Favor if the crawler has none (Toolkit p. 11)."""),
    ("D4", "People are records", """\
Every named person who talks with the party or matters to the story gets apply_changes op `npc` in the turn they
are met: name, role (e.g. "Game Guide"), gender, pronouns, apparent age, what the party saw (description), where they
are, personality and voice as observed. Update the same record when the party learns more; use `npc_attitude` for
later shifts. If it is not recorded, the People page and later turns do not know the person exists.

Decide a person's look the moment they appear: everything someone standing in front of them would take in at once
(gender, rough age, build, hair, clothing, one distinguishing detail). Never describe a visible person vaguely ("a
dust-coated adult") or default to "they" to avoid choosing; use the recorded pronouns, which may of course be
they/them when that is who the person is."""),
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
