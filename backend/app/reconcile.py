"""Owner-approved corrections of canonical state, with evidence (roadmap M1).

A correction is never automatic: the owner decides between conflicting records, then `apply_correction` makes that
decision real through the same validated rule-engine ops play uses, in one transaction, after a save point, and keeps
an auditable record: what was decided, by whom, the evidence on each side, the ops applied and their results.
`ledger_edits` (optional) carry the decision into the Git ledger through the reviewed write-back: each names a file, a
YAML path, the value now in the ledger (`expect`) and the new value, and only applies while the ledger still says
`expect` (otherwise the write-back shows a conflict). `sheet_edits` (optional) set a field of a character sheet that no
rule-engine op covers; each records the value it replaced. `resolves` lists the import-report entries the decision
settles (see `report_keys`); `annotate_report` marks every other entry as still open.
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
  resolves_json TEXT NOT NULL DEFAULT '[]',
  decided_by TEXT NOT NULL,
  save_point_id INTEGER,
  created_at TEXT NOT NULL
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    if "resolves_json" not in {r[1] for r in conn.execute("PRAGMA table_info(corrections)")}:
        conn.execute("ALTER TABLE corrections ADD COLUMN resolves_json TEXT NOT NULL DEFAULT '[]'")
    for subject, keys in BACKFILL_RESOLVES.items():  # corrections recorded before `resolves` existed
        conn.execute("UPDATE corrections SET resolves_json = ? WHERE subject = ? AND resolves_json = '[]'", (json.dumps(keys), subject))


def apply_correction(conn: sqlite3.Connection, cid: int, *, subject: str, decision: str, evidence: list[dict], ops: list[dict],
                     decided_by: str, ledger_edits: list[dict] | None = None, sheet_edits: list[dict] | None = None,
                     resolves: list[str] | None = None, position_edits: list[dict] | None = None,
                     rulings: list[dict] | None = None, trap_records: list[dict] | None = None,
                     item_edits: list[dict] | None = None, lore: list[tuple[str, str, str]] | None = None,
                     places: list[dict] | None = None, floor_deadline: int | None = None, constitution: bool = False,
                     gm_notes: list[tuple[str, str]] | None = None) -> dict:
    if not evidence:
        raise ValueError("a correction needs evidence")
    if not decided_by.strip():
        raise ValueError("a correction needs the owner's decision recorded (decided_by)")
    ensure_schema(conn)
    with transaction(conn):
        sp = create_save_point(conn, cid, f"Before correction: {subject}"[:120], kind="manual")
        results = [apply_change(conn, cid, None, op) for op in ops]  # validated; any failure rolls everything back
        results += [_sheet_edit(conn, cid, e) for e in sheet_edits or []]
        results += [_position_edit(conn, cid, e) for e in position_edits or []]
        results += [_item_edit(conn, cid, e) for e in item_edits or []]
        for pl in places or []:  # a place the party knows (memory/places.py): name is its own nesting, most specific first
            if one(conn, "SELECT id FROM locations WHERE campaign_id = ? AND lower(name) = lower(?)", (cid, pl["name"])):
                raise ValueError(f"place {pl['name']!r} is already recorded; re-review")
            conn.execute("INSERT INTO locations (campaign_id, floor, name, description, discovered, visibility, visited) VALUES (?,?,?,?,1,'player',?)",
                         (cid, pl["floor"], pl["name"], pl["description"], int(pl["visited"])))
            results.append({"summary": f"Place recorded: {pl['name']} ({'visited' if pl['visited'] else 'known, not entered'})"})
        if floor_deadline is not None:  # the floor clock (game minute of collapse); no engine op sets it
            before = one(conn, "SELECT floor_deadline_minutes FROM campaigns WHERE id = ?", (cid,))["floor_deadline_minutes"]
            conn.execute("UPDATE campaigns SET floor_deadline_minutes = ? WHERE id = ?", (floor_deadline, cid))
            results.append({"summary": f"Floor collapse set at game minute {floor_deadline}", "before": before, "after": floor_deadline})
        if constitution:  # the default GM constitution for a campaign started without a ledger (gm/constitution.py)
            from .gm import constitution as default_constitution
            results.append({"summary": f"{default_constitution.install(conn, cid)} default constitution sections added"})
        for subject_, content in gm_notes or []:  # GM-only continuity, shown to the GM every turn, never to the player
            conn.execute("INSERT INTO gm_secrets (campaign_id, subject, kind, content, source, created_at) VALUES (?,?,?,?,?,?)",
                         (cid, subject_, "plot", content, "correction", now_iso()))
            results.append({"summary": f"GM note: {subject_}"})
        if lore:  # player-known facts (subject, fact, source); duplicates are skipped
            from .memory.journal import add_player_known
            results.append({"summary": f"{add_player_known(conn, cid, lore)} player-known facts added"})
        if trap_records:  # trap records (CORE 13.7), validated by the trap tool's own model
            from .rules import traps
            results += [traps.record(conn, cid, t["trap"], t["spec"]) for t in trap_records]
        for r in rulings or []:  # an owner's standing ruling, found by search_rules and written back to campaign_rulings
            conn.execute("INSERT INTO rulings (campaign_id, turn_id, topic, ruling, certainty, label, sources_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
                         (cid, None, r["topic"], r["ruling"], "canonical", r.get("label", "CAMPAIGN"), json.dumps(r.get("sources") or []), now_iso()))
            results.append({"summary": f"Ruling recorded ({r.get('label', 'CAMPAIGN')}): {r['topic']}"})
        cor_id = conn.execute(
            """INSERT INTO corrections (campaign_id, subject, decision, evidence_json, ops_json, results_json, ledger_edits_json, resolves_json,
                                        decided_by, save_point_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, subject, decision, json.dumps(evidence, ensure_ascii=False), json.dumps(ops, ensure_ascii=False),
             json.dumps(results, ensure_ascii=False, default=str), json.dumps(ledger_edits or [], ensure_ascii=False),
             json.dumps(resolves or BACKFILL_RESOLVES.get(subject, [])), decided_by, sp, now_iso())).lastrowid
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


def _item_edit(conn: sqlite3.Connection, cid: int, e: dict) -> dict:
    """An item's description and properties (a None property is removed); no engine op rewrites an item's record text."""
    it = one(conn, "SELECT id, name, description, properties_json FROM items WHERE campaign_id = ? AND id = ?", (cid, e["item_id"]))
    if not it:
        raise ValueError(f"no item #{e['item_id']}")
    props = dict(it["properties"] or {})
    for k, v in (e.get("properties") or {}).items():
        if v is None:
            props.pop(k, None)
        else:
            props[k] = v
    desc = e.get("description", it["description"])
    conn.execute("UPDATE items SET description = ?, properties_json = ? WHERE id = ?", (desc, json.dumps(props, ensure_ascii=False), it["id"]))
    return {"summary": f"{it['name']}: record updated", "before": it["description"], "after": desc}


def _position_edit(conn: sqlite3.Connection, cid: int, e: dict) -> dict:
    """A character's recorded position (None = not recorded); no engine op sets a position to unknown."""
    c = one(conn, "SELECT id, name, location FROM characters WHERE campaign_id = ? AND id = ?", (cid, e["character_id"]))
    if not c or c["location"] != e["expect"]:
        raise ValueError(f"{c and c['name']}: location is {c and c['location']!r}, not {e['expect']!r}; re-review")
    conn.execute("UPDATE characters SET location = ? WHERE id = ?", (e["location"], c["id"]))
    return {"summary": f"{c['name']}: location {c['location']!r} -> {e['location'] or 'not recorded'}", "before": c["location"], "after": e["location"]}


def report_keys(kind: str, entry: dict) -> str:
    """Stable key of an import-report entry: 'conflict:<topic>[:<character>]' or 'review:<record>:<path or issue>'."""
    if kind == "conflict":
        who = (entry.get("values") or {}).get("character")
        return f"conflict:{entry.get('topic')}" + (f":{who}" if who else "")
    return f"review:{entry.get('record')}:{entry.get('path') or entry.get('issue')}"


def _matches(key: str, pattern: str) -> bool:
    return key.startswith(pattern[:-1]) if pattern.endswith("*") else key == pattern


def annotate_report(conn: sqlite3.Connection, cid: int, report: dict) -> dict:
    """The import report with each conflict and review item marked resolved (by which correction) or open."""
    cors = corrections(conn, cid)
    out = dict(report)
    for section, kind in (("conflicts", "conflict"), ("manual_review", "review")):
        marked = []
        for e in report.get(section) or []:
            key = report_keys(kind, e)
            hit = next((c for c in cors if any(_matches(key, p) for p in c.get("resolves") or [])), None)
            marked.append({**e, "key": key, "status": "resolved" if hit else "open",
                           **({"correction": {"id": hit["id"], "subject": hit["subject"], "decision": hit["decision"]}} if hit else {})})
        out[section] = marked
    out["open"] = sum(1 for s in ("conflicts", "manual_review") for e in out.get(s) or [] if e["status"] == "open")
    return out


def corrections(conn: sqlite3.Connection, cid: int) -> list[dict]:
    ensure_schema(conn)
    return rows(conn, "SELECT * FROM corrections WHERE campaign_id = ? ORDER BY id", (cid,))


# ------------------------------------------------------------------ decided corrections (M1)
AMMO_REVIEW = ["review:state/shared_inventory.yaml:'Crossbow Bolt'*", "review:state/shared_inventory.yaml:'Pistol Round'*",
               "review:state/campaign.yaml:current_state.ammunition_count_status",
               "review:state/campaign.yaml:shared_resource_snapshot.crossbow_bolts_carried_available",
               "review:state/campaign.yaml:shared_resource_snapshot.pistol_rounds",
               "review:state/shared_inventory.yaml:items[14].count_status", "review:state/shared_inventory.yaml:items[16].count_status",
               "review:state/shared_inventory.yaml:ammunition_reconciliation.status"]
BACKFILL_RESOLVES = {
    "Gold total and Personal Stabber payment": ["conflict:gold"],
    "Floor clock after the market sweep": ["conflict:floor clock"],
    "Silver Earth Box status and rewards": ["conflict:Silver Earth Box status:*"],
    "Ammunition count": AMMO_REVIEW,
    # his tools stay with him on Floor 1, so their count no longer matters to the party
    "Mad Driscoll's roster category": ["conflict:roster category:mad-driscoll", "review:state/characters/mad-driscoll.yaml:*"],
    "Advancement count mirror": ["conflict:advancement cycles"],
}


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


def _ledger_yaml(root, rel: str) -> dict:
    from pathlib import Path

    import yaml
    return yaml.safe_load((Path(root) / rel).read_text(encoding="utf-8")) or {}


AMMO_RULING = ("Owner ruling 2026-10-25 (Crawler Console correction): the last exact distribution (Floor 2 northeast Warden "
               "checkpoint, 89a4c9c) is current: crossbow bolts Cliff 24, Maya 20, Priya 20 (64 shared); Terri 22 pistol rounds. "
               "Unrecorded Floor 2 shots after that checkpoint are waived.")


def ammunition_correction(conn: sqlite3.Connection, cid: int, ledger_root, decided_by: str) -> dict:
    """Owner ruling 2026-10-25: the last exact ammunition distribution is current (64 shared bolts, 22 pistol rounds)."""
    found = {}
    for name in ("Crossbow Bolt", "Pistol Round"):
        it = one(conn, "SELECT id, quantity FROM items WHERE campaign_id = ? AND owner_character_id IS NULL AND name = ?", (cid, name))
        if not it or it["quantity"] is not None:
            raise ValueError(f"shared {name} is {it and it['quantity']!r}, not the unknown count this ruling was decided against; re-review")
        found[name] = it["id"]
    inv = _ledger_yaml(ledger_root, "state/shared_inventory.yaml")
    camp = _ledger_yaml(ledger_root, "state/campaign.yaml")
    ids = {e.get("id") for e in inv.get("items") or [] if isinstance(e, dict)}
    edits = []
    for iid, semantics in (("crossbow-bolt", "Exact count by owner ruling: 64 shared bolts (Cliff 24, Maya 20, Priya 20 at the last exact distribution)."),
                           ("pistol-round", "Exact count by owner ruling: 22 rounds (Terri's last exact count).")):
        if iid in ids:
            edits.append({"file": "state/shared_inventory.yaml", "path": ["items", {"id": iid}, "quantity_semantics"], "value": semantics})
    if (inv.get("ammunition_reconciliation") or {}).get("status") == "unresolved":
        edits.append({"file": "state/shared_inventory.yaml", "path": ["ammunition_reconciliation", "status"], "expect": "unresolved",
                      "value": "resolved-by-owner-ruling"})
        edits.append({"file": "state/shared_inventory.yaml", "path": ["ammunition_reconciliation", "owner_ruling"], "value": AMMO_RULING})
    if str((camp.get("current_state") or {}).get("ammunition_count_status", "")).startswith("Unresolved"):
        edits.append({"file": "state/campaign.yaml", "path": ["current_state", "ammunition_count_status"], "value": AMMO_RULING})
    snap = camp.get("shared_resource_snapshot") or {}
    for key, n in (("crossbow_bolts_carried_available", 64), ("pistol_rounds", 22)):
        if str(snap.get(key, "")).startswith("unresolved"):
            edits.append({"file": "state/campaign.yaml", "path": ["shared_resource_snapshot", key], "expect": snap[key], "value": n})
    evidence = [
        {"source": "state/shared_inventory.yaml ammunition_reconciliation and historical_floor_2_ammunition_distribution",
         "says": "Last exact distribution at the Floor 2 northeast Warden checkpoint (89a4c9c, Sept 8): bolts Cliff 24, Maya 20, Priya 20; "
                 "Terri 22 pistol rounds; status unresolved; 'No exact current total is invented'; a ruling is required before the "
                 "first ammo-dependent Floor 3 action."},
        {"source": "git history 89a4c9c..0374b26 (Sept 8-10)",
         "says": "The Greenhouse Boss fight and a crawler rescue happened with no ammunition record; the Homogenous Humor fight (two bolts "
                 "lost) was earlier (afea15f, Sept 6), so it is already reflected in the exact count."},
        {"source": "state/characters/cliff.yaml, terri-campion.yaml", "says": "Cliff's crossbow uses shared crossbow bolts; Terri's pistol uses shared pistol rounds."},
        {"source": "state/gilded_feathers_market.yaml", "says": "Bolts and pistol rounds were stocked, not bought (only the 30-Gold Stabber was paid)."},
        {"source": "story history (threads 4, 5)", "says": "No ammunition counts."},
    ]
    ops = [{"op": "set_quantity", "item_id": found["Crossbow Bolt"], "name": "Crossbow Bolt", "character": "shared", "quantity": 64,
            "reason": "Owner ruling: last exact distribution (Cliff 24, Maya 20, Priya 20)"},
           {"op": "set_quantity", "item_id": found["Pistol Round"], "name": "Pistol Round", "character": "shared", "quantity": 22,
            "reason": "Owner ruling: Terri's last exact count"}]
    return apply_correction(conn, cid, subject="Ammunition count",
                            decision="Owner ruling: the last exact distribution is current. 64 shared crossbow bolts (Cliff 24, Maya 20, "
                                     "Priya 20) and 22 pistol rounds; unrecorded Floor 2 shots are waived.",
                            evidence=evidence, ops=ops, decided_by=decided_by, ledger_edits=edits)


def _asset_note(e: dict) -> str:
    bits = [str(e[k]) for k in ("status", "quantification") if e.get(k)]
    if e.get("floor_3_installed_systems"):
        bits.append("Installed: " + " ".join(e["floor_3_installed_systems"]))
    for k in ("gasoline_remaining", "prepared_wood_gas_fuel_remaining", "primary_mode", "effect"):
        if e.get(k):
            bits.append(f"{k.replace('_', ' ').capitalize()}: {e[k]}")
    bits.append(f"Full record: state/shared_inventory.yaml historical_floor_2_assets ({e['id']}).")
    return " ".join(b if b.endswith(".") else b + "." for b in bits)


def floor_2_assets_correction(conn: sqlite3.Connection, cid: int, ledger_root, decided_by: str) -> dict:
    """Owner decision 2026-10-25: everything in historical_floor_2_assets came along to Floor 3 and is current shared kit."""
    inv = _ledger_yaml(ledger_root, "state/shared_inventory.yaml")
    assets = [e for e in inv.get("historical_floor_2_assets") or [] if isinstance(e, dict) and e.get("name")]
    ops = []
    for e in assets:
        if one(conn, "SELECT 1 FROM items WHERE campaign_id = ? AND owner_character_id IS NULL AND name = ?", (cid, e["name"])):
            continue  # already current (the Gold Siphon Assembly)
        ops.append({"op": "add_item", "character": "shared", "name": e["name"], "quantity": e.get("quantity") if e.get("quantity") is not None else 1,
                    "description": _asset_note(e)})
    if len(ops) != 10:
        raise ValueError(f"{len(ops)} historical Floor 2 assets are missing from the console, not the 10 this decision covered; re-review")
    evidence = [
        {"source": "state/shared_inventory.yaml (commit 08593db, 2026-09-25 rules audit)",
         "says": "Renamed latest_floor_2_assets to historical_floor_2_assets; the importer skips historical sections, so none of these "
                 "reached the console (the Gold Siphon Assembly was separately copied into items)."},
        {"source": "state/shared_inventory.yaml (commit 98e66e4, Floor 3 checkpoint)",
         "says": "The same section was then updated with current Floor 3 status: the pickup operational at The Molting Pigeon with its "
                 "gasifier, jousting station and kite reel; the Murder Drum speaker mount installed and functional."},
        {"source": "git history since Floor 2", "says": "No record of using, losing, or leaving behind the rune plates, biscuits, caches, toolkit or winch cable."},
    ]
    return apply_correction(conn, cid, subject="Floor 2 assets carried to Floor 3",
                            decision="Everything in historical_floor_2_assets came along and is current shared kit: the pickup, the Murder "
                                     "Drum speaker mount, 3 Explosive Rune Plates, 40 Crawler Biscuits, the arrow / handgun round / shotgun "
                                     "shell / trap stake caches, the Portable Mechanic Toolkit and the Spare Winch Cable.",
                            evidence=evidence, ops=ops, decided_by=decided_by)


def driscoll_correction(conn: sqlite3.Connection, cid: int, ledger_root, decided_by: str) -> dict:
    """Owner decision 2026-10-25: Mad Driscoll is deceased; his sheet's roster category follows the roster."""
    d = one(conn, "SELECT id, provenance, status FROM characters WHERE campaign_id = ? AND name = 'Mad Driscoll'", (cid,))
    if not d or d["status"] != "deceased":
        raise ValueError("Mad Driscoll is not recorded as deceased in the console; re-review")
    rel = d["provenance"].split(":", 1)[1]
    sheet = _ledger_yaml(ledger_root, rel)
    edits = [{"file": rel, "path": ["roster_category"], "expect": "allied_npcs", "value": "deceased_npcs"}] if sheet.get("roster_category") == "allied_npcs" else []
    evidence = [
        {"source": "state/roster.yaml", "says": "Lists mad-driscoll under deceased_npcs; 'Mad Driscoll died in the Floor 1 collapse.'"},
        {"source": rel, "says": "roster_category allied_npcs, but status deceased, died in the Floor 1 collapse; 'cannot act, assist, travel, craft, or appear'."},
    ]
    return apply_correction(conn, cid, subject="Mad Driscoll's roster category",
                            decision="Deceased: his sheet's roster category is deceased_npcs, matching the roster. His trapsmith tools stay "
                                     "with him on Floor 1, not in party inventory.",
                            evidence=evidence, ops=[], decided_by=decided_by, ledger_edits=edits)


def advancement_mirror_record(conn: sqlite3.Connection, cid: int, decided_by: str) -> dict:
    """Owner decision 2026-10-25 (decision 6): the console's 4/12 was already right; write-back mirrors the live counter into
    campaign.yaml current_state. Recorded for the audit trail: no state changes."""
    if one(conn, "SELECT 1 FROM corrections WHERE campaign_id = ? AND subject = 'Advancement count mirror'", (cid,)):
        raise ValueError("already recorded")
    evidence = [
        {"source": "state/advancement.yaml", "says": "'the authoritative live advancement counter'; log 6 -> 11/12 (DCC-DENTON-014), 11 -> 12 -> reset "
                                                     "0/12 (DCC-DENTON-015), 0 -> 4/12 for the four market-sweep decisions (f1a5680)."},
        {"source": "state/campaign.yaml current_state.advancement_cycles", "says": "11/12, left over from DCC-DENTON-014."},
        {"source": "state/roster.yaml, checkpoints/latest.md, floor_3_molting_pigeon_checkpoint",
         "says": "0/12 and 11/12: correct snapshots for their own checkpoints."},
    ]
    return apply_correction(conn, cid, subject="Advancement count mirror",
                            decision="4/12 is current. Write-back keeps campaign.yaml current_state.advancement_cycles equal to the live "
                                     "counter in state/advancement.yaml; checkpoint snapshots stay as recorded.",
                            evidence=evidence, ops=[], decided_by=decided_by)


def positions_correction(conn: sqlite3.Connection, cid: int, ledger_root, decided_by: str) -> dict:
    """Owner decision 2026-10-25: characters are where their group is; a group with no recorded position leaves its members
    unknown. Fixes the checkpoint's 'The Molting Pigeon' left on every crawler after the daylight split."""
    groups = {g["key"]: g for g in rows(conn, "SELECT key, label, location, activity FROM party_groups WHERE campaign_id = ? AND status = 'active'", (cid,))}
    edits, ledger, moved = [], [], []
    for c in rows(conn, """SELECT id, name, provenance, assignment, location FROM characters WHERE campaign_id = ? AND assignment IS NOT NULL
                           AND status NOT IN ('deceased','dead') ORDER BY party_order, id""", (cid,)):
        g = groups.get(c["assignment"])
        if not g or (g["location"] or None) == (c["location"] or None) or (g["location"] and c["location"] and g["location"].startswith(c["location"])):
            continue
        edits.append({"character_id": c["id"], "expect": c["location"], "location": g["location"]})
        moved.append(c["name"])
        if not g["location"] and (c["provenance"] or "").startswith("ledger:"):
            rel = c["provenance"].split(":", 1)[1]
            if _ledger_yaml(ledger_root, rel).get("location") == c["location"]:
                ledger.append({"file": rel, "path": ["location"], "expect": c["location"],
                               "value": f"Not recorded: with the {g['label']} group ({g['activity'] or 'assignment'})"[:200]})
    if len(moved) != 12:
        raise ValueError(f"{len(moved)} crawlers are out of step with their group, not the 12 this decision covered; re-review")
    evidence = [
        {"source": "state/characters/*.yaml location, checkpoints/latest.md (98e66e4, 13:04 CDT)",
         "says": "'The Molting Pigeon' on every sheet, written when all twelve were back at the saferoom."},
        {"source": "state/roster.yaml current_daylight_assignments", "says": "After the checkpoint the party split: prison-recon, training-market, saferoom."},
        {"source": "console groups", "says": "training-market at Gilded Feathers, Heavensworth (decision 4); prison-recon position not recorded."},
        {"source": "rules/state_ops.py group op", "says": "Joining a group with no recorded location kept each member's old location; fixed in the same change."},
    ]
    return apply_correction(conn, cid, subject="Character positions follow their groups",
                            decision="Each crawler is where their group is: the training-market group at Gilded Feathers, Heavensworth; the "
                                     "prison-recon group's position is not recorded, so its members' is unknown; the saferoom group at The Molting Pigeon.",
                            evidence=evidence, ops=[], decided_by=decided_by, ledger_edits=ledger, position_edits=edits)


def objective_correction(conn: sqlite3.Connection, cid: int, decided_by: str) -> dict:
    """Owner decision 2026-10-25: the immediate objective is its quest records, not a separate string."""
    want = ["Use current daylight for skill training and weapon/armor shopping.",
            "Complete final reconnaissance and plan the Fallen Watch prison attack without co",
            "Build the Margit-assisted Pass-Pulse Gold Siphon after nightfall."]
    found = []
    for prefix in want:
        q = one(conn, "SELECT id, title FROM quests WHERE campaign_id = ? AND status = 'active' AND title LIKE ?", (cid, prefix + "%"))
        if not q:
            raise ValueError(f"no active quest {prefix!r}; re-review")
        found.append(q)
    camp = one(conn, "SELECT active_objective, objective_quests_json AS j FROM campaigns WHERE id = ?", (cid,))
    if json.loads(camp["j"] or "[]"):
        raise ValueError("the immediate objective is already linked to quests")
    evidence = [
        {"source": "state/campaign.yaml active_objective (console campaigns.active_objective)", "says": camp["active_objective"] or ""},
        {"source": "console quests", "says": "; ".join(f"#{q['id']} {q['title']}" for q in found) + ": the same three goals as separate records."},
    ]
    ops = [{"op": "quest", "title": q["title"], "immediate": True} for q in found]
    return apply_correction(conn, cid, subject="Immediate objective linked to quests",
                            decision="The immediate objective is quests " + ", ".join(f"#{q['id']}" for q in found) + " in that order; the "
                                     "hand-written string stays as history. Finishing a quest drops it from the objective.",
                            evidence=evidence, ops=ops, decided_by=decided_by)


def erin_arrows_correction(conn: sqlite3.Connection, cid: int, ledger_root, decided_by: str) -> dict:
    """Owner decision 2026-10-25: Erin's arrows are the 15-arrow recovered cache, the only count the records support."""
    cache = one(conn, "SELECT id, quantity FROM items WHERE campaign_id = ? AND owner_character_id IS NULL AND name = 'Recovered Arrow Cache'", (cid,))
    if not cache or cache["quantity"] != 15:
        raise ValueError(f"the shared Recovered Arrow Cache is {cache and cache['quantity']!r}, not the 15 this was decided against; re-review")
    if one(conn, "SELECT 1 AS x FROM items i JOIN characters c ON c.id = i.owner_character_id WHERE c.campaign_id = ? AND c.name = 'Erin' AND i.name = 'Arrow'", (cid,)):
        raise ValueError("Erin already has an Arrow item; re-review")
    note = "Arrow supply: 15 arrows, the recovered Floor 2 arrow cache, assigned to Erin by owner ruling 2026-10-25."
    sheet = _ledger_yaml(ledger_root, "state/characters/erin.yaml")
    old = (sheet.get("equipment") or {}).get("ammunition_note")
    edits = [{"file": "state/characters/erin.yaml", "path": ["equipment", "ammunition_note"], "expect": old, "value": note}] if old else []
    evidence = [
        {"source": "state/characters/erin.yaml (since DCC-DENTON-009)", "says": "'Arrow supply'; 'exact arrow count was not preserved and is not being invented'; fights record only 'all recovered'."},
        {"source": "state/shared_inventory.yaml historical_floor_2_assets (0374b26)", "says": "Recovered Arrow Cache, quantity 15: arrows found in the marked Floor 2 treasure."},
        {"source": "rulebooks", "says": "No default arrow supply; the pre-generated archers carry 16-20; Gilded Feathers sells 20 arrows for 10 Gold."},
    ]
    ops = [{"op": "remove_item", "item_id": cache["id"], "name": "Recovered Arrow Cache", "character": "shared", "quantity": 15, "cause": "becomes Erin's arrows"},
           {"op": "add_item", "character": "Erin", "name": "Arrow", "quantity": 15, "description": "Erin's arrows: the recovered Floor 2 arrow cache (owner ruling 2026-10-25)."}]
    return apply_correction(conn, cid, subject="Erin's arrow supply",
                            decision="Erin's arrows are the 15-arrow recovered cache; she can buy more in play (Gilded Feathers: 20 for 10 Gold).",
                            evidence=evidence, ops=ops, decided_by=decided_by, ledger_edits=edits)


TRAINING_RULING = ("Paid training at a guild that teaches the Skill: +1 Rank costs 4 hours and 500 Gold (the Core Rulebook's Floor 3 guild rate, "
                   "p. 333), within the Rank cap (skill op, source training). A shorter or cheaper paid block is safe grinding: its hours count "
                   "as grinding hours toward the Skill's advancement check (grind tool) with no Mob or complication risk, and they count toward "
                   "the 5-hour daily safe-grinding limit. Deathstroke Hall is a training guild for melee and nonmagical warriors (p. 340).")


def training_ruling(conn: sqlite3.Connection, cid: int, decided_by: str) -> dict:
    """Owner decision 2026-10-25: how paid training works."""
    if one(conn, "SELECT 1 AS x FROM rulings WHERE campaign_id = ? AND topic = 'Paid training at guilds'", (cid,)):
        raise ValueError("the paid-training ruling is already recorded")
    evidence = [
        {"source": "Core Rulebook p. 340", "says": "'Deathstroke Hall is a training guild for all manner of melee and nonmagical warriors to gather and train their skills.'"},
        {"source": "Core Rulebook p. 333 (Table 76, Third Floor)", "says": "a guild: 'four hours training and haggling and 500 gold' gains or advances one Rank."},
        {"source": "CORE_MECHANICS 14.6", "says": "Up to 5 grinding hours per 30-hour day are safe; grinding hours equal to the Skill's Rank trigger its advancement check."},
        {"source": "playtest 2, turn 8", "says": "The GM ruled a 2-hour, 40-Gold block as 2 grinding hours (ONE-SCENE); the owner made it standing."},
    ]
    return apply_correction(conn, cid, subject="Paid training at guilds", decision=TRAINING_RULING, evidence=evidence, ops=[],
                            decided_by=decided_by, rulings=[{"topic": "Paid training at guilds", "ruling": TRAINING_RULING, "label": "CAMPAIGN",
                                                             "sources": ["Core Rulebook pp. 333, 340", "CORE_MECHANICS 14.6"]}])


SOURCE = "owner ruling 2026-10-25 (docs/trap-records-draft.md, approved as written)"
LEDGER = "from the ledger build record (state/shared_inventory.yaml murder-drum-battery)"
STAKES = {"item": "Recovered Trap Stake Cache", "per_trigger": 1}
TRAP_RECORDS = [
    {"trap": "Snap-Deploy Stake Thrower", "spec": {
        "components": "spring-loaded stake launcher (two loose units)", "ammunition": STAKES, "deploy": {"actions": 1, "minutes": 10},
        "trigger": "tripline or remote pull", "area": "one 30-ft lane", "check": {"kind": "attack", "attack_bonus": 10},
        "damage": "1d8 Piercing", "effects": None, "reset": {"actions": 1, "minutes": 1}, "reset_notes": "reload one stake",
        "bulk": "Small", "label": "CAMPAIGN", "source": SOURCE}},
    {"trap": "Porcupine Pod", "spec": {
        "components": "spring pod with one paralytic dart", "ammunition": {"item": "Paralytic Dart", "per_trigger": 1},
        "deploy": {"actions": 1, "minutes": 10}, "trigger": "pressure plate", "area": "one 5-ft square",
        "check": {"kind": "attack", "attack_bonus": 10}, "damage": "1d4 Piercing",
        "effects": "on a hit the target makes a CON Stat Check vs 13 (10 + Floor) or is Paralyzed until it succeeds at the end of a later round",
        "reset": {"actions": 2, "minutes": 5}, "reset_notes": "load a dart and re-cock the spring", "bulk": "Small", "label": "CAMPAIGN", "source": SOURCE}},
    {"trap": "Scatterburst Trap", "spec": {
        "components": "tripwire shrapnel charge (two units)", "ammunition": {"item": "Miscellaneous Junk", "per_trigger": 1},
        "deploy": {"actions": 1, "minutes": 10}, "trigger": "tripwire", "area": "10-ft burst (Area: a successful Evade halves)",
        "check": {"kind": "attack", "attack_bonus": 10}, "damage": "2d6 Piercing", "effects": "Area Attack: a target that Evades takes half",
        "reset": {"actions": 0, "minutes": 10}, "reset_notes": "repack shrapnel: 1 Misc. Junk per firing", "bulk": "Small", "label": "CAMPAIGN", "source": SOURCE}},
    {"trap": "Cinch Trap", "spec": {
        "components": "ground snare loop", "ammunition": None, "deploy": {"actions": 1, "minutes": 10}, "trigger": "a foot in the loop",
        "area": "one 5-ft square", "check": {"kind": "attack", "attack_bonus": 10}, "damage": None,
        "effects": "Held (a limb snared) until the target escapes with Escape Artist or a STR Stat Check vs 16 as an Action",
        "reset": {"actions": 1, "minutes": 1}, "reset_notes": "re-lay the loop", "bulk": "Tiny", "label": "CAMPAIGN", "source": SOURCE}},
    {"trap": "Snapweb Bloom", "spec": {
        "components": "spring-deployed cable web (two units)", "ammunition": None, "deploy": {"actions": 1, "minutes": 1},
        "trigger": "thrown or spring-deployed across an opening", "area": "one 10-ft opening", "check": {"kind": "none"}, "damage": None,
        "effects": "a creature crossing must succeed on Escape Artist or a STR Stat Check vs 16 or its movement stops there; a Major Fail "
                   "leaves it Held (it does not Hold automatically)",
        "reset": {"actions": 0, "minutes": 5}, "reset_notes": "re-tension the web", "bulk": "Small", "label": "CAMPAIGN", "source": SOURCE}},
    {"trap": "Caltrop/Wedge Bundle", "spec": {
        "components": "caltrops and wedges (two bundles)", "ammunition": None, "deploy": {"actions": 1, "minutes": 1},
        "trigger": "scattered on the ground", "area": "one 10-ft area", "check": {"kind": "none"}, "damage": None,
        "effects": "ground creatures entering at more than half Move take 1d4 Piercing and their Move is halved this round",
        "reset": {"actions": 0, "minutes": 10}, "reset_notes": "gather them up", "bulk": "Tiny", "label": "CAMPAIGN", "source": SOURCE}},
    {"trap": "Mechanical Trip Alarm", "spec": {
        "components": "tripwire noisemaker (two units)", "ammunition": None, "deploy": {"actions": 1, "minutes": 5},
        "trigger": "tripwire", "area": "one doorway or lane", "check": {"kind": "none"}, "damage": None,
        "effects": "a loud alarm the party hears; no damage", "reset": {"actions": 1, "minutes": 1}, "reset_notes": "re-string the wire",
        "bulk": "Tiny", "label": "CAMPAIGN", "source": SOURCE}},
    {"trap": "Accordion Barricade", "spec": {
        "components": "repaired folding wall", "ammunition": None, "deploy": {"actions": 2, "minutes": 5}, "trigger": "set across a lane",
        "area": "one 10-ft lane", "check": {"kind": "none"}, "damage": None,
        "effects": "cover behind it (Core Rulebook cover rules); to pass, a creature climbs it (Climbing vs 16) or breaks it (the GM sets its "
                   "sturdiness when it first matters, and records it)",
        "reset": {"actions": 0, "minutes": 5}, "reset_notes": "fold it back", "bulk": "Medium", "label": "CAMPAIGN", "source": SOURCE}},
    {"trap": "Murder Drum Stake Battery Mk II", "spec": {
        "components": "Crib Daddy's damage-immune drum-kit chassis, three mounted Snap-Deploy Stake Throwers, forward Anti-Air Net Launcher "
                      "(Selina's Net loaded), Skyhook Harpoon, Kill-Lane Wings", "ammunition": STAKES, "deploy": {"actions": 1, "minutes": 10},
        "trigger": "Cliff's prepared Portable Trap package; quick deployment places the drum (it does not fire the Skyhook or the Wings)",
        "area": "spread: three approach lanes; converged: one kill zone", "check": {"kind": "attack", "attack_bonus": 10}, "damage": "1d8 Piercing",
        "effects": "Volley: trigger once per mounted stake thrower (3), one stake each. Anti-Air Net Launcher: attack +10, on a hit the target is "
                   "Held under Selina's Net. Skyhook Harpoon (" + LEDGER + "): an Action, operator's Crossbow + DEX (Cliff +10), range 40 ft, 1d6 "
                   "Piercing, on a hit Tethered; a later Action, Engineering (INT) opposed by the target's STR, winches it 10 ft closer; a grounded "
                   "flyer can become Held; rewind 1 Action. Kill-Lane Wings (" + LEDGER + "): from a deployed drum, 1 Action fires and tensions two "
                   "cable fences up to 30 ft on suitable anchors; no damage; a creature crossing must defeat it (Escape Artist or STR), on a Fail its "
                   "movement stops, a bad failure may leave it Held; very strong or large creatures may break anchors.",
        "reset": {"actions": 1, "minutes": 5}, "reset_notes": "reload: 1 Action per mounted thrower", "bulk": "Large", "label": "CAMPAIGN", "source": SOURCE}},
]


def trap_records_correction(conn: sqlite3.Connection, cid: int, decided_by: str) -> dict:
    """Owner approval 2026-10-25: the drafted trap records (docs/trap-records-draft.md) as written."""
    for t in TRAP_RECORDS:
        it = one(conn, "SELECT properties_json FROM items WHERE campaign_id = ? AND owner_character_id IS NULL AND name = ?", (cid, t["trap"]))
        if not it:
            raise ValueError(f"no shared {t['trap']!r}; re-review")
        if (it.get("properties") or {}).get("trap"):
            raise ValueError(f"{t['trap']} already has a trap record; re-review")
    evidence = [
        {"source": "state/shared_inventory.yaml deployed_assets (since DCC-DENTON-009)", "says": "the traps are recorded by name and 'ready' only; no trigger, Check, damage or reset."},
        {"source": "state/shared_inventory.yaml murder-drum-battery", "says": "full build records for the Skyhook Harpoon and the Kill-Lane Wings (copied as written)."},
        {"source": "docs/trap-records-draft.md", "says": "the drafted records, anchored on Difficulty 10 + 2 x Floor, a +10 trap attack and the published weapon dice; approved as written."},
    ]
    return apply_correction(conn, cid, subject="Trap records", decision="The party's traps get the approved records (CORE 13.7): deployable, locked while deployed.",
                            evidence=evidence, ops=[], decided_by=decided_by, trap_records=TRAP_RECORDS)


UNCOUNTED_RULING = ("Proxy-GM rulings (owner delegated 2026-10-26) for the six items held at an unknown count: Recovered Electrical "
                    "Cabling 1 partial coil; Better-quality liquor 4 bottles; Cliff's Basic carried gear 1 everyday kit; Eli's Converted "
                    "Needle-Spears 3; Nate's Personal Maintenance Tools 1 kit; Mad Driscoll's trapsmith kit lost with him in the Floor 1 collapse.")
UNCOUNTED = [  # (owner, item name, count or None = lost, reason, new description or None, properties to set)
    ("shared", "Recovered Electrical Cabling", 1,
     "half the industrial reel was recovered, then the Skyhook and both Kill-Lane Wings attempts used the cable they required: one partial coil remains",
     "One partial coil of recovered industrial electrical cabling (proxy-GM ruling 2026-10-26): enough for one more modest rigging or "
     "trap build. Using it in a build consumes the coil.", {"note": None, "quantity_status": "ruled: 1 partial coil"}),
    ("shared", "Better-quality liquor", 4, "a modest stash supplemented from cleared nests: 4 bottles",
     "Four bottles of better-quality liquor (proxy-GM ruling 2026-10-26), the existing stock plus what the cleared nests yielded.",
     {"note": None, "quantity_status": "ruled: 4 bottles"}),
    ("Cliff Atkinson", "Basic carried gear", 1, "a single everyday kit, not a countable stock",
     "Cliff's everyday carried basics (pack, water, small tools and sundries) treated as one bundle (proxy-GM ruling 2026-10-26). "
     "Nothing specific is drawn from it without a ruling.", {"quantity_status": "ruled: one bundle"}),
    ("Eli Ramirez", "Converted Needle-Spears usable as Javelins", 3, "the only established figure: three encounter throws were recovered",
     None, {"quantity_status": "ruled: 3 (the three recovered encounter throws)"}),
    ("Nate Holloway", "Personal Maintenance Tools", 1, "his own personal tool kit",
     None, {"quantity_status": "ruled: one personal kit"}),
    ("Mad Driscoll", "Trapsmith tools and components", None, "Driscoll died in the Floor 1 collapse; the kit he carried was lost with him",
     None, {}),
]


def uncounted_correction(conn: sqlite3.Connection, cid: int, ledger_root, decided_by: str) -> dict:
    """Owner delegation 2026-10-26 ("resolve any item held at an unknown count; I trust you as proxy GM")."""
    ops, edits, item_edits = [], [], []
    for owner, name, count, why, desc, props in UNCOUNTED:
        sql = "SELECT i.id, i.quantity FROM items i LEFT JOIN characters c ON c.id = i.owner_character_id WHERE i.campaign_id = ? AND i.name = ? AND "
        it = one(conn, sql + ("i.owner_character_id IS NULL" if owner == "shared" else "c.name = ?"), (cid, name) if owner == "shared" else (cid, name, owner))
        if not it or it["quantity"] is not None:
            raise ValueError(f"{owner}'s {name} is {it and it['quantity']!r}, not the unknown count this ruling was decided against; re-review")
        ops.append({"op": "set_quantity", "item_id": it["id"], "name": name, "character": owner, "quantity": count or 1, "reason": f"Proxy-GM ruling: {why}"})
        if count is None:
            ops.append({"op": "remove_item", "item_id": it["id"], "name": name, "character": owner, "quantity": 1, "cause": why})
        elif desc or props:
            item_edits.append({"item_id": it["id"], **({"description": desc} if desc else {}), "properties": props})
    for file, iid, value in (("state/characters/eli-ramirez.yaml", "converted-needle-spears", {"quantity": 3, "quantity_status": "ruled: 3 (the three recovered encounter throws), proxy GM 2026-10-26"}),
                             ("state/characters/nate-holloway.yaml", "personal-maintenance-tools", {"quantity": 1, "quantity_status": "ruled: one personal kit, proxy GM 2026-10-26"}),
                             ("state/characters/mad-driscoll.yaml", "trapsmith-tools", {"quantity": 0, "quantity_status": "lost with Driscoll in the Floor 1 collapse (proxy GM 2026-10-26)"})):
        sheet = _ledger_yaml(ledger_root, file)
        carried = (sheet.get("equipment") or {}).get("carried") or []
        if any(isinstance(e, dict) and e.get("id") == iid for e in carried):
            for k, v in value.items():
                edits.append({"file": file, "path": ["equipment", "carried", {"id": iid}, k], "value": v})
    evidence = [
        {"source": "state/shared_inventory.yaml unquantified_items and notes",
         "says": "Cabling: half the reel recovered; some consumed by the Skyhook, the failed Wings prototype and the completed Wings; residual unquantified. "
                 "Liquor: 'existing stock was supplemented from cleared nests; source gives no numeric amount.'"},
        {"source": "state/characters/eli-ramirez.yaml equipment.carried", "says": "Converted needle-spears: 'unknown-total; three encounter throws were recovered'."},
        {"source": "state/characters/mad-driscoll.yaml", "says": "Deceased in the Floor 1 collapse; carried 'Trapsmith tools and components', unquantified."},
        {"source": "state/characters/nate-holloway.yaml, cliff.yaml", "says": "Nate's personal maintenance tools (his property); Cliff's 'Basic carried gear' (a placeholder line)."},
        {"source": "story history (threads 4, 5)", "says": "No counts for any of these."},
        {"source": "owner, 2026-10-26", "says": "'Any item that's being held out by an unknown count, go ahead and resolve. I trust you to make decisions as a proxy GM.'"},
    ]
    return apply_correction(conn, cid, subject="Uncounted items", decision=UNCOUNTED_RULING, evidence=evidence, ops=ops,
                            decided_by=decided_by, ledger_edits=edits, item_edits=item_edits,
                            rulings=[{"topic": "Counts for previously uncounted items", "ruling": UNCOUNTED_RULING, "label": "CAMPAIGN",
                                      "sources": ["owner delegation 2026-10-26"]}])


MARCUS_PROMISE = "Promise: Cliff will help Marcus find his wife and daughter"
MARGIT_HELP = "Commitment: Margit helps build the Pass-Pulse Gold Siphon without a labor fee"


def story_known_correction(conn: sqlite3.Connection, cid: int, ledger_root, decided_by: str) -> dict:
    """M6 (owner delegation 2026-10-26): import what the ledger marks player-known, and record the two promises it describes
    but no quest holds. Only player-known statements and player-visible sheet text are used; GM-only beats stay GM-only."""
    from pathlib import Path
    from .memory.journal import player_known
    have = {r["title"] for r in rows(conn, "SELECT title FROM quests WHERE campaign_id = ?", (cid,))}
    if MARCUS_PROMISE in have or MARGIT_HELP in have:
        raise ValueError("the promise records already exist; re-review")
    facts = player_known(Path(ledger_root))
    ops = [
        {"op": "quest", "title": MARCUS_PROMISE, "kind": "commitment", "owner": "Cliff", "giver": "Marcus", "priority": 1,
         "description": "Marcus's wife was at work and his daughter should have been at school when the world collapsed; finding them remains his "
                        "major personal motivation, and Cliff promised to help find them.",
         "progress": "Open: no concrete search step has been taken yet.",
         "details": {"made_by": "Cliff", "made_to": "Marcus", "known_since": "before Floor 3",
                     "source": "state/characters/marcus.yaml narrative; gm_state/characters/marcus.yaml player_observation_state.known (player-known)"}},
        {"op": "quest", "title": MARGIT_HELP, "kind": "commitment", "giver": "Margit Winchgrinder", "priority": 2,
         "description": "Margit accepted the party's no-strings stout and agreed to help build the Pass-Pulse Gold Siphon without a labor fee; "
                        "the build is deferred until nightfall.",
         "details": {"made_by": "Margit Winchgrinder", "made_to": "Cliff and the party", "terms": "no labor fee",
                     "linked_project": "Build the Margit-assisted Pass-Pulse Gold Siphon after nightfall.",
                     "source": "state/campaign.yaml world_state_notes; floor_3_molting_pigeon_checkpoint.pass_pulse_gold_siphon"}},
    ]
    evidence = [
        {"source": "gm_state/characters/*.yaml player_observation_state.known",
         "says": "Each GM companion sheet lists what the party has observed ('known'); the importer had filed the whole sheet as a GM secret, so these "
                 "player-known facts were hidden. Only the 'known' lists are imported; everything else in those sheets stays GM-only."},
        {"source": "state/campaign.yaml completed_events, known_intelligence", "says": "Player-known history (no dates recorded) and area intelligence, never imported."},
        {"source": "state/characters/marcus.yaml narrative; gm_state/characters/marcus.yaml known",
         "says": "'His wife was at work and his daughter should have been at school; finding them remains a major personal motivation.' "
                 "Known: 'Cliff promised to help find them.' No quest held the promise."},
        {"source": "state/campaign.yaml world_state_notes", "says": "Margit accepted the no-strings stout and agreed to help build the Siphon without a labor fee."},
        {"source": "owner, 2026-10-26", "says": "'I trust you to make decisions as a proxy GM.' (M6: promises and relationships retrievable and visible when player-known)"},
    ]
    return apply_correction(conn, cid, subject="Player-known story records", evidence=evidence, ops=ops, decided_by=decided_by, lore=facts,
                            decision=f"Imported {len(facts)} player-known statements (companion sheets' known lists, completed events, known "
                                     "intelligence) and recorded Marcus's family-search promise and Margit's no-fee build help as commitments.")


FLOOR_3_PLACES = [
    {"name": "The Molting Pigeon, Heavensworth, Over City", "floor": 3, "visited": True,
     "description": "The party's genuine System-protected saferoom in Heavensworth; Jonai, Xaggra and Margit are stationed there."},
    {"name": "Heavensworth, Over City", "floor": 3, "visited": True,
     "description": "The Over City district where the party is based: Gilded Feathers, The Molting Pigeon and the old Fallen Watch prison."},
    {"name": "Secondhand Saviors, Gilded Feathers, Heavensworth, Over City", "floor": 3, "visited": True,
     "description": "A Gilded Feathers shop; it holds the prepaid Personal Stabber until Scout claims it in person."},
    {"name": "Fallen Watch, Heavensworth, Over City", "floor": 3, "visited": False,
     "description": "The old prison: conspicuously open gates and a strong Brain Boiler presence. Under reconnaissance, not entered; a credible "
                    "high-risk objective, not a confirmed stairwell."},
]


def floor_3_places_correction(conn: sqlite3.Connection, cid: int, decided_by: str) -> dict:
    """M8 (owner delegation 2026-10-26): record the Floor 3 places the party knows, so the map shows them; only player-known ones."""
    evidence = [
        {"source": "state/campaign.yaml world_state_notes (lore)", "says": "The Molting Pigeon is the party's genuine System-protected saferoom in "
                 "Heavensworth; Heavensworth's Gilded Feathers district is mapped; the old Fallen Watch prison is a credible high-risk objective, "
                 "not a confirmed stairwell; Brain Boiler presence."},
        {"source": "quests (journal)", "says": "Reserved: Personal Stabber, held by Secondhand Saviors; Complete final reconnaissance of the Fallen "
                 "Watch prison without committing to the fight."},
        {"source": "party groups", "says": "Saferoom group at The Molting Pigeon, Heavensworth, Over City; Prison Recon assigned to Fallen Watch."},
        {"source": "owner, 2026-10-26", "says": "'I trust you to make decisions as a proxy GM.' Fallen Watch is recorded as known, not entered."},
    ]
    have = {r["name"].lower() for r in rows(conn, "SELECT name FROM locations WHERE campaign_id = ?", (cid,))}
    todo = [p for p in FLOOR_3_PLACES if p["name"].lower() not in have]
    return apply_correction(conn, cid, subject="Floor 3 places", evidence=evidence, ops=[], decided_by=decided_by, places=todo,
                            decision="Recorded the Floor 3 places the party knows for the map: The Molting Pigeon (saferoom), Heavensworth, "
                                     "Secondhand Saviors, and Fallen Watch as known but not entered.")


SAVAGE_STATS = {"strength": 4, "intelligence": 2, "constitution": 3, "dexterity": 4, "charisma": 5}
SAVAGE_MODS = {"strength": 2, "intelligence": 1, "constitution": 2, "dexterity": 2, "charisma": 2}


def savage_opening_correction(conn: sqlite3.Connection, cid: int, decided_by: str) -> dict:
    """Audit of the Savage Crawling opening (owner request 2026-10-26): what the first 61 turns settled but could not
    record (Marn, the starting sheet), what the books owed (floor clock, Game Guide AI Favor), and the GM procedure a
    console-started campaign lacked. Nothing is invented: every value is from the transcript or a cited page."""
    camp = one(conn, "SELECT name, active_character_id, floor_deadline_minutes FROM campaigns WHERE id = ?", (cid,))
    if not camp or camp["name"] != "Savage Crawling":
        raise ValueError("this correction is for the Savage Crawling campaign")
    pc = one(conn, "SELECT * FROM characters WHERE id = ?", (camp["active_character_id"],))
    if (pc.get("stats") or {}).get("stats") or pc["hp_max"] is not None or pc["ai_favor"]:
        raise ValueError(f"{pc['name']}'s sheet is no longer blank; re-review")
    if one(conn, "SELECT id FROM characters WHERE campaign_id = ? AND lower(name) = 'marn'", (cid,)):
        raise ValueError("Marn is already recorded; re-review")
    ops = [
        {"op": "npc", "name": "Marn", "role": "Game Guide", "category": "allied_npcs", "location": "Tutorial Guild — reception",
         "description": "A squat, gray-skinned woman in a mustard-colored vest, spectacles on a chain around her neck; keeps burnt "
                        "coffee on a hot plate behind the reception counter.",
         "personality": "Dry, exact and patient; corrects herself when she was fishing; protective of her furniture.",
         "voice": "Clipped and deadpan; short warnings with a sting in the tail.",
         "known_motivation": "Get Drake to leave reception knowing his next step and how to come back alive."},
        {"op": "initialize_sheet", "character": "player", "stats": SAVAGE_STATS, "modifiers": SAVAGE_MODS, "health_slots": 10,
         "health_per_slot": 2, "mana_max": 2, "level": 1, "race": "Human",
         "source": "rolled in play (turn 45; modifiers Crawlers & Customization p. 18; Health and Mana turn 46, Core p. 113); "
                   "Level 1, Race Human (Atlas p. 14)"},
        {"op": "ai_favor", "character": "player", "delta": 1,
         "reason": "followed his Game Guide's advice (Heal on the Hotlist, turn 64) with no AI Favor; Toolkit p. 11"},
    ]
    owed = ("The opening skipped the System's entry sequence and every tutorial achievement. Owed, and due at the next natural "
            "beat (the System 'catching up on processing' is fine): the entrance slamming shut and 'Welcome, Crawler. Welcome "
            "to the First Floor.'; crawler name and number; Level 1, Race Human 'probably' (C&C p. 8, Atlas p. 14); and the "
            "starter achievements the opening earned (entering the Dungeon, scavenging an armory from a tailgate, character "
            "creation, first Inventory and Hotlist use, the Evade drill), each recorded with op achievement and any box as an "
            "unopened item (Atlas p. 24: plentiful and generous). The Tutorial Guild being at the foot of the stairs stands "
            "as played (Marn called it a favor); later guilds are found in the halls.")
    evidence = [
        {"source": "turns 22-69 (Tutorial Guild reception)", "says": "Marn introduced herself as Drake's Game Guide and ran character "
                                                                     "creation; no op recorded her, so People and the GM briefing did not know her."},
        {"source": "turns 45-46; GM note #16", "says": "Stats rolled STR 4, INT 2, CON 3, DEX 4, CHA 5 (+2,+1,+2,+2,+2); 10 Health slots "
                                                     "of 2; Mana 2. The engine had no way to record a new sheet (allocate_stats needs banked points)."},
        {"source": "Game Master's Campaign Toolkit p. 11", "says": "Following a Game Guide's advice usually grants a crawler 1 AI Favor if they have none."},
        {"source": "Game Master's Campaign Toolkit p. 19", "says": "The First Floor: Time to Collapse 5 Days. The campaign had no floor clock; "
                                                                  "the Dungeon opened at game minute 12 (turn 13)."},
        {"source": "Atlas p. 24; Core Rulebook p. 17; Crawlers & Customization p. 8",
         "says": "Tutorial achievements are plentiful on the first two floors; Tutorial Guilds are seeded through the floor; the "
                 "System welcomes each crawler on entry. None of it happened: a campaign started in the console had no constitution."},
        {"source": "owner, 2026-10-26", "says": "Asked for an audit of the beginning of the game: Marn missing from People, the tutorial "
                                                "door, and no achievements, announcements, rewards or flavor."},
    ]
    return apply_correction(conn, cid, subject="Savage Crawling opening audit", evidence=evidence, ops=ops, decided_by=decided_by,
                            floor_deadline=12 + 5 * 24 * 60, constitution=True, gm_notes=[("Owed: the System's opening", owed)],
                            decision="Recorded Marn (Game Guide) and Drake's starting sheet from the transcript, granted the Game "
                                     "Guide's 1 AI Favor, started the 5-day First Floor clock from the opening, added the default "
                                     "GM constitution, and left the GM a note that the entry sequence and tutorial achievements are owed.")
