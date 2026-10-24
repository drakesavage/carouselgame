"""Seed party organization, the current scene, project details and commitments from the ledger (schema v2).

Everything here is copied from ledger records (verbatim text, with its source path); nothing is inferred to fill a
gap. Used by the importer for new imports and once as a backfill for campaigns imported before schema v2.

* Groups: roster.yaml current_state_override.current_daylight_assignments (+ campaign.yaml current_state proposed_*).
* Scene: the newest "active-*" state snapshot (e.g. state/gilded_feathers_market.yaml) if any, else campaign.yaml current_state.
* Project details: a checkpoint block whose key matches an open objective's id (e.g. pass_pulse_gold_siphon).
* Reservations: shared_inventory notes "The X is reserved ..." (a reservation is never an owned item).
* Commitments: "promised" lines in checkpoints/latest.md (labelled as coming from the derived checkpoint).
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from ..db import now_iso, one, rows


def _words(s: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", s.lower()) if w}


def _find_blocks(doc: Any, path: str = "") -> list[tuple[str, dict]]:
    out = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            p = f"{path}.{k}" if path else str(k)
            if isinstance(v, dict):
                out.append((p, v))
            out.extend(_find_blocks(v, p))
    return out


def seed_play_state(conn: sqlite3.Connection, cid: int, docs: dict[str, Any], latest_md: str | None) -> list[str]:
    notes: list[str] = []
    camp = docs.get("state/campaign.yaml") or {}
    roster = docs.get("state/roster.yaml") or {}
    shared = docs.get("state/shared_inventory.yaml") or {}
    pc = one(conn, "SELECT c.id, c.slug FROM campaigns k JOIN characters c ON c.id = k.active_character_id WHERE k.id = ?", (cid,))
    by_slug = {r["slug"]: r["id"] for r in rows(conn, "SELECT id, slug FROM characters WHERE campaign_id = ?", (cid,))}
    current = camp.get("current_state") or {}
    override = roster.get("current_state_override") or {}

    # active snapshot (latest dated record of where play actually is)
    snapshot_path, snapshot = None, None
    for path, d in sorted(docs.items()):
        if path.startswith("state/") and isinstance(d, dict) and str(d.get("status", "")).startswith("active") and d.get("location"):
            snapshot_path, snapshot = path, d

    # groups
    assignments = override.get("current_daylight_assignments") or {}
    pc_group = None
    for key, members in assignments.items():
        if not isinstance(members, list):
            continue
        activity = _assignment_activity(latest_md, [str(r["name"]) for r in rows(conn, f"SELECT name FROM characters WHERE campaign_id = ? AND slug IN ({','.join('?' * len(members))})", (cid, *members))] if members else [])
        location = None
        if key == "saferoom":
            location = override.get("location")
        known = [m for m in members if m in by_slug]
        if pc and pc["slug"] in known:
            pc_group = key
            if snapshot:
                location = snapshot["location"]
        conn.execute("INSERT OR IGNORE INTO party_groups (campaign_id, key, label, location, activity, status, source) VALUES (?,?,?,?,?,?,?)",
                     (cid, key, key.replace("-", " ").title(), location, activity, "active",
                      "state/roster.yaml current_state_override.current_daylight_assignments"))
        for m in known:
            conn.execute("UPDATE characters SET assignment = ? WHERE id = ?", (key, by_slug[m]))
        missing = [m for m in members if m not in by_slug]
        notes.append(f"Group '{key}': {len(known)} members" + (f" @ {location}" if location else " (location not recorded)")
                     + (f"; no sheet for {missing}" if missing else ""))

    # scene
    if not one(conn, "SELECT id FROM scenes WHERE campaign_id = ? AND status = 'active'", (cid,)):
        adv = docs.get("state/advancement.yaml") or {}
        last = (adv.get("reconciliations") or [{}])[-1]
        if snapshot:
            parts = [f"Latest record ({snapshot_path}): {last.get('basis', '')}".strip()]
            for k, v in (last.get("market_resolution") or {}).items():
                if isinstance(v, str):
                    parts.append(f"{k.replace('_', ' ')}: {v}")
            parts += [str(x) for x in snapshot.get("continuity") or []]
            title = re.sub(r"\.ya?ml$", "", snapshot_path.split("/")[-1]).replace("_", " ").title()
            participants = [r["name"] for r in rows(conn, "SELECT name FROM characters WHERE campaign_id = ? AND assignment = ?", (cid, pc_group))] if pc_group else []
            conn.execute("""INSERT INTO scenes (campaign_id, title, location, group_key, situation, participants_json, status, started_game_minutes, source, created_at)
                            VALUES (?,?,?,?,?,?,?,(SELECT game_time_minutes FROM campaigns WHERE id = ?),?,?)""",
                         (cid, title, snapshot["location"], pc_group, "\n".join(p for p in parts if p), json.dumps(participants), "active", cid,
                          f"{snapshot_path} + state/advancement.yaml", now_iso()))
            notes.append(f"Current scene seeded from {snapshot_path}")
        elif current.get("scene"):
            conn.execute("""INSERT INTO scenes (campaign_id, title, location, situation, status, started_game_minutes, source, created_at)
                            VALUES (?,?,?,?,?,(SELECT game_time_minutes FROM campaigns WHERE id = ?),?,?)""",
                         (cid, str(current.get("phase") or "Current scene").replace("-", " ").title(), camp.get("current_location"),
                          str(current["scene"]), "active", cid, "state/campaign.yaml current_state", now_iso()))
            notes.append("Current scene seeded from state/campaign.yaml current_state")

    # project details from checkpoint blocks matching objective ids
    blocks = _find_blocks(camp)
    for q in rows(conn, "SELECT id, title, description, details_json FROM quests WHERE campaign_id = ? AND description LIKE 'Ledger objective id: %'", (cid,)):
        oid = q["description"].split(": ", 1)[1]
        want = _words(oid)
        match = next(((p, b) for p, b in blocks if want and want <= _words(p.split(".")[-1]) and "status" in b), None)
        if match and not q["details"]:
            p, b = match
            conn.execute("UPDATE quests SET details_json = ? WHERE id = ?", (json.dumps({**b, "source": f"state/campaign.yaml {p}"}, default=str), q["id"]))
            notes.append(f"Objective '{oid}' details from {p}")

    # reservations (never owned items)
    for n in shared.get("notes") or []:
        m = re.match(r"^The (.+?) is reserved\b(.*)$", str(n))
        if not m:
            continue
        item = m.group(1)
        title = f"Reserved: {item}"
        if one(conn, "SELECT id FROM quests WHERE campaign_id = ? AND title = ?", (cid, title)):
            continue
        details: dict[str, Any] = {"ledger_source": "state/shared_inventory.yaml notes", "state/shared_inventory.yaml notes": str(n)}
        for path, d in docs.items():
            tx = d.get("transaction") if isinstance(d, dict) else None
            if isinstance(tx, dict) and item.lower() in json.dumps(tx).lower():
                details[f"{path} transaction"] = tx
                details["conflict"] = "Records disagree on whether this was paid for; confirm in play before it changes hands."
        conn.execute("INSERT INTO quests (campaign_id, kind, title, description, status, priority, details_json, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (cid, "reservation", title, str(n), "active", 2, json.dumps(details, default=str), now_iso()))
        notes.append(f"Reservation seeded: {item}" + (" (conflicting records)" if "conflict" in details else ""))

    # commitments stated in the derived checkpoint
    for line in (latest_md or "").splitlines():
        if re.search(r"\bpromised\b", line, re.I):
            text = line.strip("-* ").strip()
            title = f"Promise: {text[:70]}"
            if not one(conn, "SELECT id FROM quests WHERE campaign_id = ? AND description = ?", (cid, text)):
                conn.execute("INSERT INTO quests (campaign_id, kind, title, description, status, priority, details_json, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                             (cid, "commitment", title, text, "active", 2, json.dumps({"ledger_source": "checkpoints/latest.md (derived view)"}), now_iso()))
                notes.append("Commitment seeded from checkpoints/latest.md")
    return notes


def needs_backfill(conn: sqlite3.Connection, cid: int) -> bool:
    return not one(conn, "SELECT id FROM party_groups WHERE campaign_id = ?", (cid,)) and not one(conn, "SELECT id FROM scenes WHERE campaign_id = ?", (cid,))


def backfill(conn: sqlite3.Connection) -> list[str]:
    """Seed v2 play state for campaigns imported before schema v2, from the ledger commit they are based on."""
    import yaml
    from ..db import transaction
    from ..ledger.sync import LedgerError, _show, link
    out = []
    for c in rows(conn, "SELECT id FROM campaigns WHERE data_source = 'ledger_import'"):
        if not needs_backfill(conn, c["id"]):
            continue
        try:
            ln = link(conn, c["id"])
        except LedgerError:
            continue
        paths = [f"state/{name}" for name in re.findall(r"^([\w.-]+\.ya?ml)$", _ls_state(ln["root"], ln["base"]), re.M)]
        docs = {}
        for path in paths:
            text = _show(ln["root"], ln["base"], path)
            if text:
                docs[path] = yaml.safe_load(text)
        latest = _show(ln["root"], ln["base"], "checkpoints/latest.md")
        with transaction(conn):
            notes = seed_play_state(conn, c["id"], docs, latest)
        out.append(f"campaign {c['id']}: " + "; ".join(notes))
    return out


def _ls_state(root, commit: str) -> str:
    import subprocess
    p = subprocess.run(["git", "-C", str(root), "ls-tree", "--name-only", f"{commit}:state"], capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return p.stdout if p.returncode == 0 else ""


def _assignment_activity(latest_md: str | None, member_names: list[str]) -> str | None:
    """The checkpoint bullet ('- **Label:** names. what they are doing') naming most of this group's members."""
    if not latest_md or not member_names:
        return None
    firsts = {n.split(" ")[0].lower() for n in member_names}
    best, score = None, 0
    in_section = False
    for line in latest_md.splitlines():
        if line.startswith("## "):
            in_section = "assignment" in line.lower()
            continue
        m = re.match(r"^- \*\*(.+?):?\*\*:?\s*(.*)$", line) if in_section else None
        if m:
            hits = sum(1 for w in _words(m.group(2)) if w in firsts)
            if hits > score:
                best, score = f"{m.group(1).rstrip(':')}: {m.group(2)} (checkpoints/latest.md)", hits
    return best if score >= max(1, len(firsts) // 2) else None
