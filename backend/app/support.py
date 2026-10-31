"""Long-session support (roadmap M9): backups you can see, a save-point restore preview, and a redacted problem report.

* Backups: the list in Saves & Settings, created and verified from the game PC only; an automatic database-only backup
  when the newest one is over a day old (the last AUTO_KEEP are kept). Full backups (with art) stay manual.
* Restore preview: what restoring a save point would change, in player terms (time, place, Gold, items, quests, health),
  computed from the snapshot without touching anything. GM-only tables are never described.
* Problem report: one turn's player-visible exchange, its timing, tool names and outcomes, token use and integrity
  flags, and the current state summary, written to data/reports/. Context, tool arguments, GM secrets, GM-only
  memories, the GM token and the device passcode never go in; a final pass redacts any GM-only text that slipped in.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT_DIR, Settings
from .db import one, rows

AUTO_LABEL = "auto-daily"
AUTO_KEEP = 7
AUTO_EVERY_S = 24 * 3600


def fmt_minutes(m: int | None) -> str:
    return "?" if m is None else f"{m // 60}h{m % 60:02d}m"


# ------------------------------------------------------------------ backups
def list_backups(settings: Settings) -> list[dict]:
    out = []
    root = settings.data_dir / "backups"
    for d in sorted((p for p in root.glob("*") if p.is_dir()), reverse=True) if root.is_dir() else []:
        m = d / "manifest.json"
        info = json.loads(m.read_text(encoding="utf-8")) if m.is_file() else {}
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        out.append({"name": d.name, "label": info.get("label") or d.name.split("-", 2)[-1], "created": _stamp_time(d.name),
                    "size_mb": round(size / 1e6, 1), "has_manifest": m.is_file(), "media": any((d / x).is_dir() for x in ("art", "portraits")),
                    "files": len(info.get("files", {})) if info else None})
    return out


def _stamp_time(name: str) -> str | None:
    m = re.match(r"(\d{8})-(\d{6})", name)
    if not m:
        return None
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").astimezone().isoformat(timespec="seconds")


def newest_age_s(settings: Settings) -> float | None:
    b = list_backups(settings)
    if not b or not b[0]["created"]:
        return None
    return time.time() - datetime.fromisoformat(b[0]["created"]).timestamp()


def auto_backup(settings: Settings) -> Path | None:
    """A database-only backup when the newest backup is over a day old; older automatic ones beyond AUTO_KEEP are removed."""
    from . import backup
    age = newest_age_s(settings)
    if age is not None and age < AUTO_EVERY_S or not settings.campaign_db.exists():
        return None
    made = backup.create(settings, AUTO_LABEL, media=False)
    autos = [d for d in sorted((settings.data_dir / "backups").glob(f"*-{AUTO_LABEL}")) if d.is_dir()]
    for old in autos[:-AUTO_KEEP]:
        import shutil
        shutil.rmtree(old, ignore_errors=True)
    return made


# ------------------------------------------------------------------ restore preview
def restore_preview(conn: sqlite3.Connection, cid: int, save_id: int) -> dict:
    sp = one(conn, "SELECT id, label, created_at, snapshot_json, max_message_id, max_turn_id FROM save_points WHERE campaign_id = ? AND id = ?", (cid, save_id))
    if not sp:
        raise KeyError("save point not found")
    snap = sp["snapshot"]
    then_c, now_c = snap["campaigns"][0], one(conn, "SELECT * FROM campaigns WHERE id = ?", (cid,))
    changes = []

    def diff(label, a, b, fmt=str):
        if a != b:
            changes.append({"what": label, "now": fmt(b) if b is not None else "—", "after_restore": fmt(a) if a is not None else "—"})

    diff("Game time", then_c.get("game_time_minutes"), now_c.get("game_time_minutes"), fmt_minutes)
    diff("Location", then_c.get("current_location"), now_c.get("current_location"))
    gold = lambda items: next((i["quantity"] for i in items if i.get("owner_character_id") is None and str(i["name"]).lower() == "gold"), None)
    now_items = rows(conn, "SELECT name, quantity, owner_character_id FROM items WHERE campaign_id = ?", (cid,))
    diff("Gold", gold(snap.get("items", [])), gold(now_items))
    key = lambda i: (i.get("owner_character_id"), str(i["name"]))
    a, b = {key(i): i.get("quantity") for i in snap.get("items", [])}, {key(i): i.get("quantity") for i in now_items}
    names = {c["id"]: c["name"] for c in snap.get("characters", [])}
    item_changes = []
    for k in sorted(set(a) | set(b), key=str):
        if a.get(k) != b.get(k) and k[1].lower() != "gold":
            who = names.get(k[0], "shared") if k[0] else "shared"
            item_changes.append(f"{k[1]} ({who}): {b.get(k, 'none')} → {a.get(k, 'none')}")
    pq = {q["title"]: q["status"] for q in snap.get("quests", []) if q.get("visibility", "player") == "player"}
    nq = {q["title"]: q["status"] for q in rows(conn, "SELECT title, status FROM quests WHERE campaign_id = ? AND visibility = 'player'", (cid,))}
    quest_changes = [f"{t}: {nq.get(t, 'not yet recorded')} → {pq.get(t, 'not yet recorded')}" for t in sorted(set(pq) | set(nq)) if pq.get(t) != nq.get(t)]
    st_then = {c["name"]: c.get("status") for c in snap.get("characters", [])}
    st_now = {c["name"]: c["status"] for c in rows(conn, "SELECT name, status FROM characters WHERE campaign_id = ?", (cid,))}
    char_changes = [f"{n}: {st_now.get(n)} → {v}" for n, v in sorted(st_then.items()) if st_now.get(n) != v]
    later = one(conn, """SELECT COUNT(*) AS n FROM messages WHERE campaign_id = ? AND id > ? AND rolled_back = 0 AND visibility = 'player'""",
                (cid, sp["max_message_id"]))["n"]
    return {"save_point": {"id": sp["id"], "label": sp["label"], "created_at": sp["created_at"]}, "changes": changes,
            "items": item_changes[:60], "items_total": len(item_changes), "quests": quest_changes, "characters": char_changes,
            "messages_hidden": later,
            "note": "Restoring takes an automatic save point first, so it can be undone; later messages are hidden, not deleted."}


# ------------------------------------------------------------------ problem report
def _version() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT_DIR), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip() or "?"
    except Exception:  # noqa: BLE001 - a report must not fail on this
        return "?"


def _sensitive(conn: sqlite3.Connection, cid: int, settings: Settings) -> list[str]:
    """Everything that must never appear in a report: GM secrets, GM-only memories, events and messages, and the tokens."""
    out = [r["content"] for r in rows(conn, "SELECT content FROM gm_secrets WHERE campaign_id = ?", (cid,))]
    out += [r["content"] for r in rows(conn, "SELECT content FROM memories WHERE campaign_id = ? AND visibility != 'player'", (cid,))]
    out += [r["summary"] for r in rows(conn, "SELECT summary FROM events WHERE campaign_id = ? AND visibility != 'player'", (cid,))]
    out += [r["content"] for r in rows(conn, "SELECT content FROM messages WHERE campaign_id = ? AND visibility != 'player'", (cid,))]
    out += [r["fact"] for r in rows(conn, "SELECT fact FROM lore_facts WHERE campaign_id = ? AND visibility != 'player'", (cid,))]
    for f in (settings.data_dir / "gm_token.txt", settings.data_dir / "access_passcode.txt"):
        if f.is_file():
            out.append(f.read_text(encoding="utf-8").strip())
    return [s for s in out if s and len(s.strip()) >= 6]


def redact(text: str, sensitive: list[str]) -> str:
    for s in sensitive:
        text = text.replace(s, "[redacted]")
        for line in s.splitlines():  # a quoted line of a longer GM-only text
            if len(line.strip()) >= 30:
                text = text.replace(line.strip(), "[redacted]")
    return text


def problem_report(conn: sqlite3.Connection, settings: Settings, cid: int, turn_id: int | None, note: str) -> dict:
    camp = one(conn, "SELECT id, name, current_location, game_time_minutes FROM campaigns WHERE id = ?", (cid,))
    if not camp:
        raise KeyError("campaign not found")
    turn = one(conn, "SELECT * FROM turns WHERE campaign_id = ? AND id = ?", (cid, turn_id)) if turn_id else \
        one(conn, "SELECT * FROM turns WHERE campaign_id = ? ORDER BY id DESC LIMIT 1", (cid,))
    prefs = json.loads((settings.data_dir / "settings.json").read_text(encoding="utf-8")) if (settings.data_dir / "settings.json").is_file() else {}
    lines = [f"# Problem report: {camp['name']}", "",
             f"- When: {datetime.now(timezone.utc).isoformat(timespec='seconds')}  ·  app {_version()}",
             f"- GM: {prefs.get('gm_provider', settings.gm_provider)}"
             f" (Codex model {prefs.get('codex_model') or 'default'}, effort {prefs.get('codex_reasoning_effort', '?')})",
             "", "## What went wrong (owner's note)", "", note.strip() or "(no note)", ""]
    if turn:
        lines += [f"## Turn {turn['id']}", "",
                  f"- Status: {turn['status']}{' (rolled back)' if turn['rolled_back'] else ''}  ·  provider {turn['provider'] or '?'}",
                  f"- Started {turn['created_at']}, finished {turn['completed_at'] or '—'}"]
        if turn["error"]:
            lines.append(f"- Error: {turn['error'][:800]}")
        lines += ["", "**Player:**", "", "> " + turn["player_input"].replace("\n", "\n> "), ""]
        for m in rows(conn, """SELECT role, content, meta_json FROM messages WHERE campaign_id = ? AND turn_id = ? AND visibility = 'player'
                               AND role IN ('gm', 'system') ORDER BY id""", (cid, turn["id"])):
            meta = m.get("meta") or {}
            lines += [f"**{'GM' if m['role'] == 'gm' else 'System'}:**", "", m["content"], ""]
            for r in meta.get("rolls") or []:
                lines.append(f"- Roll #{r.get('roll_id')}: {r.get('reason') or ''} {r.get('expression')} = {r.get('total')}"
                             f"{' vs ' + str(r['target']) if r.get('target') is not None else ''} → {r.get('degree') or r.get('outcome') or ''}")
            for ch in meta.get("changes") or []:
                lines.append(f"- Change: {ch}")
            if meta.get("rules_cited"):
                lines.append(f"- Rules cited: {', '.join(meta['rules_cited'])}")
            lines.append("")
        d = one(conn, "SELECT steps_json, usage_json, integrity_json FROM turn_diagnostics WHERE turn_id = ?", (turn["id"],))
        if d:
            steps = [st for st in d.get("steps") or [] if isinstance(st, dict)]
            names = [c.get("name") for st in steps if st.get("kind") == "gm_request" for c in st.get("tool_calls") or [] if isinstance(c, dict)]
            failed = [r.get("name") for st in steps if st.get("kind") == "tool_results" for r in st.get("results") or []
                      if isinstance(r, dict) and not r.get("ok", True)]
            rounds = (d.get("usage") or {}).get("rounds") or []
            tokens = {k: sum(int((r.get("usage") or {}).get(k) or 0) for r in rounds if isinstance(r, dict))
                      for k in ("input_tokens", "output_tokens", "cached_input_tokens")}
            tokens["model seconds"] = round(sum(float(r.get("duration_s") or 0) for r in rounds if isinstance(r, dict)), 1)
            lines += ["## Diagnostics (redacted: no context, no tool arguments or results)", "",
                      f"- Model rounds: {sum(1 for st in steps if st.get('kind') == 'gm_request') or len(rounds)}  ·  tool calls: {len(names)}"
                      f" ({', '.join(sorted(set(n for n in names if n))) or 'none'})",
                      f"- Failed tool calls: {', '.join(n for n in failed if n) or 'none'}",
                      "- Tokens: " + (", ".join(k.replace("_", " ") + f" {v}" for k, v in tokens.items() if v) or "not recorded")]
            for f in d.get("integrity") or []:
                lines.append(f"- Integrity flag: {(f.get('message') or f.get('detail') or json.dumps(f, default=str)) if isinstance(f, dict) else f}"[:400])
            lines.append("")
    from . import consistency
    groups = rows(conn, "SELECT label, location FROM party_groups WHERE campaign_id = ? AND status = 'active'", (cid,))
    gold = one(conn, "SELECT quantity FROM items WHERE campaign_id = ? AND owner_character_id IS NULL AND lower(name) = 'gold'", (cid,))
    lines += ["## State now", "",
              f"- {camp['current_location']} · game time {fmt_minutes(camp['game_time_minutes'])} · Gold {gold and gold['quantity']}",
              "- Groups: " + ("; ".join(g["label"] + " @ " + (g["location"] or "not recorded") for g in groups) or "none"),
              f"- Record consistency: {consistency.summary(conn, cid)}", ""]
    text = redact("\n".join(lines), _sensitive(conn, cid, settings))
    folder = settings.data_dir / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-campaign{cid}-turn{turn['id'] if turn else 'none'}.md"
    path.write_text(text, encoding="utf-8")
    return {"path": str(path), "markdown": text, "turn_id": turn["id"] if turn else None}
