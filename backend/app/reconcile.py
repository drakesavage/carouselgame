"""Owner-approved corrections of canonical state, with evidence (roadmap M1).

A correction is never automatic: the owner decides between conflicting records, then `apply_correction` makes that
decision real through the same validated rule-engine ops play uses, in one transaction, after a save point, and keeps
an auditable record: what was decided, by whom, the evidence on each side, the ops applied and their results.
`ledger_edits` (optional) carry the decision into the Git ledger through the reviewed write-back: each names a file, a
YAML path, the value now in the ledger (`expect`) and the new value, and only applies while the ledger still says
`expect` (otherwise the write-back shows a conflict). `sheet_edits` (optional) set a field of a character sheet that no
rule-engine op covers; each records the value it replaced.
"""
from __future__ import annotations

import json
import sqlite3

from .db import now_iso, one, rows, transaction
from .memory.snapshots import create_save_point
from .rules.engine import apply_change

SCHEMA = """
CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  subject TEXT NOT NULL,
  decision TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  ops_json TEXT NOT NULL DEFAULT '[]',
  results_json TEXT NOT NULL DEFAULT '[]',
  ledger_edits_json TEXT NOT NULL DEFAULT '[]',
  decided_by TEXT NOT NULL,
  save_point_id INTEGER,
  created_at TEXT NOT NULL
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def apply_correction(conn: sqlite3.Connection, cid: int, *, subject: str, decision: str, evidence: list[dict], ops: list[dict],
                     decided_by: str, ledger_edits: list[dict] | None = None, sheet_edits: list[dict] | None = None) -> dict:
    if not evidence:
        raise ValueError("a correction needs evidence")
    if not decided_by.strip():
        raise ValueError("a correction needs the owner's decision recorded (decided_by)")
    ensure_schema(conn)
    with transaction(conn):
        sp = create_save_point(conn, cid, f"Before correction: {subject}"[:120], kind="manual")
        results = [apply_change(conn, cid, None, op) for op in ops]  # validated; any failure rolls everything back
        results += [_sheet_edit(conn, cid, e) for e in sheet_edits or []]
        cor_id = conn.execute(
            """INSERT INTO corrections (campaign_id, subject, decision, evidence_json, ops_json, results_json, ledger_edits_json, decided_by,
                                        save_point_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (cid, subject, decision, json.dumps(evidence, ensure_ascii=False), json.dumps(ops, ensure_ascii=False),
             json.dumps(results, ensure_ascii=False, default=str), json.dumps(ledger_edits or [], ensure_ascii=False), decided_by, sp, now_iso())).lastrowid
        conn.execute("INSERT INTO events (campaign_id, type, summary, payload_json, visibility, created_at) VALUES (?,?,?,?,?,?)",
                     (cid, "correction", f"Record corrected: {subject}. {decision}"[:500], json.dumps({"correction_id": cor_id}), "player", now_iso()))
    return {"id": cor_id, "save_point_id": sp, "results": results}


def _sheet_edit(conn: sqlite3.Connection, cid: int, e: dict) -> dict:
    c = one(conn, "SELECT id, name, sheet_json FROM characters WHERE campaign_id = ? AND id = ?", (cid, e["character_id"]))
    if not c:
        raise ValueError(f"no character #{e['character_id']}")
    sheet = c["sheet"] or {}
    node = sheet
    for k in e["path"][:-1]:
        node = node.setdefault(k, {})
    before = node.get(e["path"][-1])
    if "expect" in e and before != e["expect"]:
        raise ValueError(f"{c['name']}: sheet {'.'.join(e['path'])} is {before!r}, not {e['expect']!r}; re-review")
    node[e["path"][-1]] = e["value"]
    conn.execute("UPDATE characters SET sheet_json = ? WHERE id = ?", (json.dumps(sheet, ensure_ascii=False), c["id"]))
    return {"summary": f"{c['name']}: sheet {'.'.join(e['path'])} corrected", "before": before, "after": e["value"]}


def corrections(conn: sqlite3.Connection, cid: int) -> list[dict]:
    ensure_schema(conn)
    return rows(conn, "SELECT * FROM corrections WHERE campaign_id = ? ORDER BY id", (cid,))


# ------------------------------------------------------------------ decided corrections (M1)
STABBER_NOTE_OLD = "The Personal Stabber is reserved but not owned and no 30-Gold payment has been made."
STABBER_NOTE_NEW = ("The Personal Stabber was prepaid (30 Gold, Gilded Feathers market sweep) and is held by Secondhand Saviors "
                    "until Scout personally claims it; it is not owned or carried by anyone yet.")


def gold_stabber_correction(conn: sqlite3.Connection, cid: int, ledger_root, decided_by: str) -> dict:
    """Owner decision 2026-10-25: the later market-sweep record wins (30 Gold paid, 4,046 -> 4,016); the Stabber is
    paid and held, not owned, until Scout claims it."""
    from pathlib import Path

    import yaml
    gold = one(conn, "SELECT id, quantity FROM items WHERE campaign_id = ? AND owner_character_id IS NULL AND name = 'Gold'", (cid,))
    if not gold:
        raise ValueError("no shared Gold item")
    if gold["quantity"] != 4046:
        raise ValueError(f"shared Gold is {gold['quantity']}, not the 4,046 this correction was decided against; re-review")
    doc = yaml.safe_load((Path(ledger_root) / "state/shared_inventory.yaml").read_text(encoding="utf-8"))
    notes = doc.get("notes") or []
    # the YAML editor sets mapping keys, so the notes list is replaced whole with only the stale note changed
    edits = [{"file": "state/shared_inventory.yaml", "path": ["notes"], "expect": notes,
              "value": [STABBER_NOTE_NEW if n == STABBER_NOTE_OLD else n for n in notes]}] if STABBER_NOTE_OLD in notes else []
    evidence = [
        {"source": "state/shared_inventory.yaml (commit 98e66e4, 2026-09-25 13:04 CDT)",
         "says": "Gold quantity 4046; 'The Personal Stabber is reserved but not owned and no 30-Gold payment has been made.'"},
        {"source": "state/gilded_feathers_market.yaml transaction (commit 4067fa1, 2026-09-25 18:11 CDT)",
         "says": "gold_before 4046, personal_stabber_paid 30, gold_after 4016; 'Cliff explicitly authorized payment only for the Personal "
                 "Stabber'; ownership unassigned until Scout claims it."},
        {"source": "state/advancement.yaml (commit f1a5680, 2026-09-25 18:12 CDT)",
         "says": "'gold_change: 4046 -> 4016; 30 Gold paid for Personal Stabber, held by Secondhand Saviors pending Scout claim.'"},
        {"source": "git history", "says": "The market-sweep commits changed only the market and advancement files; shared_inventory.yaml was "
                                           "never updated after the payment, so its 4,046 and 'no payment' note are stale."},
    ]
    ops = [{"op": "set_quantity", "item_id": gold["id"], "name": "Gold", "character": "shared", "quantity": 4016, "reason": "Correction: Personal Stabber prepaid (30 Gold)"},
           {"op": "quest", "title": "Reserved: Personal Stabber",
            "progress": "Paid 30 Gold; held by Secondhand Saviors until Scout personally claims it.",
            "details": {"payment": "paid", "paid_gold": 30, "held_by": "Secondhand Saviors", "claim": "Scout must claim it in person",
                        "owned": False, "conflict": None,
                        "resolution": "Owner decision 2026-10-25: the later market-sweep record (paid) is canonical."}}]
    return apply_correction(conn, cid, subject="Gold total and Personal Stabber payment",
                            decision="Paid: shared Gold is 4,016; the Personal Stabber is paid and held at Secondhand Saviors, not owned "
                                     "until Scout claims it.", evidence=evidence, ops=ops, decided_by=decided_by, ledger_edits=edits)


def clock_correction(conn: sqlite3.Connection, cid: int, decided_by: str) -> dict:
    """Owner decision 2026-10-25: 70 minutes passed after the DCC-DENTON-015 checkpoint (215h51m -> 214h41m)."""
    camp = one(conn, "SELECT game_time_minutes, floor_deadline_minutes FROM campaigns WHERE id = ?", (cid,))
    if camp["game_time_minutes"] != 1449:
        raise ValueError(f"elapsed floor time is {camp['game_time_minutes']}, not the 1,449 this correction was decided against; re-review")
    evidence = [
        {"source": "state/campaign.yaml and checkpoints/latest.md (commit 98e66e4, 2026-09-25 13:04 CDT)",
         "says": "remaining 12951 minutes (215h51m), elapsed floor time 1449 (24h09m), written at the full checkpoint."},
        {"source": "state/advancement.yaml cycles (commits c2d22c3 15:49, 11d4b24 17:32 CDT)",
         "says": "Two Negotiations before the market sweep (Deathstroke Hall training evaluation, Twilight Tinkerers party rate); "
                 "no clock change recorded."},
        {"source": "state/gilded_feathers_market.yaml clock (commit 4067fa1, 18:11 CDT)",
         "says": "before 12941 (215h41m), 60 minutes elapsed (four Negotiations at 5 minutes each plus 40 minutes of walking and "
                 "inspection), after 12881 (214h41m). The 10-minute gap before it matches two Negotiations at the same 5-minute rate."},
        {"source": "state/advancement.yaml market_resolution (commit f1a5680, 18:12 CDT)", "says": "'clock_change: 215h41m -> 214h41m'."},
    ]
    ops = [{"op": "advance_time", "minutes": 70,
            "reason": "Correction: two pre-market Negotiations (10 min) and the Gilded Feathers market sweep (60 min)"}]
    return apply_correction(conn, cid, subject="Floor clock after the market sweep",
                            decision="70 minutes passed after the checkpoint: 214h41m remain (elapsed 1,519).",
                            evidence=evidence, ops=ops, decided_by=decided_by)


def location_correction(conn: sqlite3.Connection, cid: int, decided_by: str) -> dict:
    """Owner decision 2026-10-25: the campaign location is where Cliff is (Gilded Feathers, not a saferoom)."""
    camp = one(conn, "SELECT current_location, safe_room FROM campaigns WHERE id = ?", (cid,))
    if not (camp["current_location"] or "").startswith("The Molting Pigeon") or not camp["safe_room"]:
        raise ValueError(f"campaign location is {camp['current_location']!r} (saferoom {bool(camp['safe_room'])}), not the Molting Pigeon "
                         "saferoom this correction was decided against; re-review")
    evidence = [
        {"source": "state/campaign.yaml and checkpoints/latest.md (commit 98e66e4, 13:04 CDT)",
         "says": "current_location The Molting Pigeon, safe_room_status true; 'All twelve Denton crawlers are back at The Molting Pigeon saferoom'."},
        {"source": "state/roster.yaml current_daylight_assignments and checkpoints/latest.md",
         "says": "After the checkpoint the party split: prison-recon (Maya, Erin, Scout, Eli, Ghost, Terri), training-market (Cliff, Vada, "
                 "Priya, Marcus, Selina, Nate), saferoom (Jonai, Xaggra, Margit)."},
        {"source": "state/gilded_feathers_market.yaml (commit 4067fa1, 18:11 CDT)",
         "says": "location: Gilded Feathers, Heavensworth; Cliff negotiated in person there."},
        {"source": "git history", "says": "No commit after the split updated campaign.yaml's location or saferoom flag."},
    ]
    ops = [{"op": "set_location", "location": "Gilded Feathers, Heavensworth", "floor": 3, "safe_room": False,
            "description": "Heavensworth market district; Cliff's training/market group is here. The party's saferoom remains The Molting Pigeon."}]
    return apply_correction(conn, cid, subject="Current location and saferoom",
                            decision="The campaign location is where Cliff is: Gilded Feathers, Heavensworth, not a saferoom. The Molting "
                                     "Pigeon remains the party's saferoom, where the support group is; the recon group's position stays unknown.",
                            evidence=evidence, ops=ops, decided_by=decided_by)


BOX_OPENED = {"quantity": 0, "status": "opened-and-resolved"}
BOX_REWARDS_NOW = {"silver_earth_boxes": 0,
                   "ownership": "All twelve personal Silver Earth Boxes were opened at DCC-DENTON-014; see floor_3_molting_pigeon_checkpoint.boxes."}


def silver_earth_box_correction(conn: sqlite3.Connection, cid: int, ledger_root, decided_by: str) -> dict:
    """Owner decision 2026-10-25: every Silver Earth Box is opened; the selection-time 'unopened' notes are stale; each
    box's physical reward is in its crawler's inventory."""
    from pathlib import Path

    import yaml
    root = Path(ledger_root)
    boxed = []
    for c in rows(conn, "SELECT id, name, provenance, sheet_json FROM characters WHERE campaign_id = ? AND provenance LIKE 'ledger:%' ORDER BY id", (cid,)):
        box = (c["sheet"] or {}).get("floor_3_silver_earth_box")
        if isinstance(box, dict) and box.get("status") == "opened":
            boxed.append(c)
    if len(boxed) != 12:
        raise ValueError(f"{len(boxed)} crawlers have an opened-box record, not the 12 this correction was decided against; re-review")
    ops, sheet_edits, edits = [], [], []
    for c in boxed:
        sheet = c["sheet"]
        sel = (sheet.get("floor_3_selection") or {}).get("silver_earth_box")
        if sel != {"quantity": 1, "status": "unopened"}:
            raise ValueError(f"{c['name']}: selection-time box note is {sel!r}, not the stale 'unopened' note; re-review")
        reward = sheet["floor_3_silver_earth_box"].get("physical_reward") or ""
        name = reward[:1].upper() + reward[1:]
        if not name or one(conn, "SELECT 1 FROM items WHERE campaign_id = ? AND owner_character_id = ? AND name = ?", (cid, c["id"], name)):
            raise ValueError(f"{c['name']}: no physical reward recorded, or it is already in inventory; re-review")
        ops.append({"op": "add_item", "character": c["name"], "name": name, "quantity": 1,
                    "description": "Physical reward from this crawler's Floor 3 Silver Earth Box. Whether it has since been used, eaten or "
                                   "given away is not recorded."})
        sheet_edits.append({"character_id": c["id"], "path": ["floor_3_selection", "silver_earth_box"], "expect": sel, "value": BOX_OPENED})
        f = c["provenance"].split(":", 1)[1]
        doc = yaml.safe_load((root / f).read_text(encoding="utf-8"))
        if (doc.get("floor_3_selection") or {}).get("silver_earth_box") == sel:
            edits.append({"file": f, "path": ["floor_3_selection", "silver_earth_box"], "expect": sel, "value": BOX_OPENED})
    camp = yaml.safe_load((root / "state/campaign.yaml").read_text(encoding="utf-8"))
    old = (camp.get("floor_3_selection_resolution") or {}).get("unopened_rewards")
    if isinstance(old, dict) and old.get("silver_earth_boxes") == 12:
        edits.append({"file": "state/campaign.yaml", "path": ["floor_3_selection_resolution", "unopened_rewards"], "expect": old, "value": BOX_REWARDS_NOW})
    evidence = [
        {"source": "state/characters/*.yaml floor_3_selection.silver_earth_box, state/campaign.yaml floor_3_selection_resolution (commit 2f89e56, 2026-09-22)",
         "says": "Written at class selection: one unopened Silver Earth Box per crawler; 'unopened_rewards: silver_earth_boxes: 12'."},
        {"source": "state/characters/*.yaml floor_3_silver_earth_box and inventory silver-earth-box-floor-3 (commit 98e66e4, 2026-09-25)",
         "says": "Every crawler: status opened, Earth Hobby Potion consumed, final hobby Skill granted, physical reward named; inventory box "
                 "quantity 0, opened-and-resolved."},
        {"source": "state/campaign.yaml floor_3_molting_pigeon_checkpoint.boxes, state/roster.yaml, checkpoints/latest.md (98e66e4)",
         "says": "'All twelve Silver Earth Boxes opened; no Silver Earth Box remains unopened.'"},
        {"source": "console skills", "says": "All twelve hobby Skills are on the console sheets at the recorded ranks (Erin's Geocaching 4 after DCC-DENTON-015)."},
        {"source": "physical rewards", "says": "Named only on the sheets, never added to any inventory in the ledger or the console."},
    ]
    return apply_correction(conn, cid, subject="Silver Earth Box status and rewards",
                            decision="All twelve Silver Earth Boxes are opened; the selection-time 'unopened' notes are stale. Each box's "
                                     "physical reward is added to its crawler's inventory (use not recorded).",
                            evidence=evidence, ops=ops, decided_by=decided_by, ledger_edits=edits, sheet_edits=sheet_edits)
