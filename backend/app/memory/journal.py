"""The campaign's story made navigable: a quest journal, a lore notebook, a truthful chronology and in-game search.

Everything here reads player-visible records only (visibility = 'player', not rolled back). GM secrets, GM companion
sheets and private memories never enter, so nothing here can leak them.

`player_known(ledger_root)` extracts what the ledger itself marks as known to the party but which lives in files the
importer otherwise treats as GM-only or doesn't import: each companion sheet's `player_observation_state.known`, the
campaign's `completed_events` (history whose dates are unknown) and `known_intelligence`.
"""
from __future__ import annotations

import math
import re
import sqlite3
from pathlib import Path

import yaml

from ..db import now_iso, one, rows
from .dossier import _norm
from .views import BOOKKEEPING_EVENTS, player_records

# ------------------------------------------------------------------ what the ledger says the party knows
HISTORY = "history"            # lore_facts.subject for imported completed events (undated, in recorded order)
INTEL = "intelligence: "        # prefix for known_intelligence areas
KNOWN = "known: "               # prefix for a companion sheet's player-known facts (subject = known: <slug>)


def player_known(ledger_root: Path) -> list[tuple[str, str, str]]:
    """(subject, fact, source) for every player-known statement the ledger records outside the imported sheets."""
    root = Path(ledger_root)
    out: list[tuple[str, str, str]] = []
    for f in sorted((root / "gm_state" / "characters").glob("*.yaml")):
        try:
            doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        slug = doc.get("character_id") or f.stem
        for fact in ((doc.get("player_observation_state") or {}).get("known") or []):
            out.append((KNOWN + str(slug), str(fact), f"gm_state/characters/{f.name} player_observation_state.known (marked player-known)"))
    camp_file = root / "state" / "campaign.yaml"
    camp = yaml.safe_load(camp_file.read_text(encoding="utf-8")) if camp_file.exists() else {}
    for n, ev in enumerate(camp.get("completed_events") or []):
        out.append((HISTORY, str(ev), f"state/campaign.yaml completed_events[{n}] (date not recorded)"))
    for area, facts in (camp.get("known_intelligence") or {}).items():
        for fact in facts if isinstance(facts, list) else [facts]:
            out.append((INTEL + str(area).replace("_", " "), str(fact), f"state/campaign.yaml known_intelligence.{area}"))
    return out


def add_player_known(conn: sqlite3.Connection, cid: int, facts: list[tuple[str, str, str]]) -> int:
    have = {(r["subject"], r["fact"]) for r in rows(conn, "SELECT subject, fact FROM lore_facts WHERE campaign_id = ?", (cid,))}
    n = 0
    for subject, fact, source in facts:
        if (subject, fact) in have:
            continue
        conn.execute("INSERT INTO lore_facts (campaign_id, subject, fact, certainty, visibility, source, created_at) VALUES (?,?,?,?,?,?,?)",
                     (cid, subject, fact, "canonical", "player", source, now_iso()))
        n += 1
    return n


# ------------------------------------------------------------------ quest journal
JOURNAL_KINDS = (("quest", "Quests"), ("commitment", "Promises & commitments"), ("reservation", "Reserved items"),
                 ("project", "Projects"), ("crafting", "Crafting"))


def _people(conn: sqlite3.Connection, cid: int) -> list[dict]:
    from ..carousel.residents import unmet_clause
    return rows(conn, f"SELECT id, name, slug FROM characters WHERE campaign_id = ? AND {unmet_clause('characters')}", (cid,))


def _mentioned(text: str, people: list[dict]) -> list[dict]:
    from .dossier import _mentions
    return [{"id": p["id"], "name": p["name"]} for p in people if _mentions(text, p["name"])]


def _places(conn: sqlite3.Connection, cid: int) -> list[str]:
    names = {r["location"] for r in rows(conn, "SELECT location FROM party_groups WHERE campaign_id = ? AND location IS NOT NULL", (cid,))}
    names |= {r["location"] for r in rows(conn, "SELECT location FROM scenes WHERE campaign_id = ? AND location IS NOT NULL", (cid,))}
    names |= {r["name"] for r in rows(conn, "SELECT name FROM locations WHERE campaign_id = ? AND visibility = 'player'", (cid,))}
    parts = {p.strip() for n in names for p in re.split(r",|—| - ", n) if len(p.strip()) > 3}
    return sorted(parts | {"Fallen Watch", "The Molting Pigeon", "Gilded Feathers", "Heavensworth", "Secondhand Saviors"}, key=len, reverse=True)


def display_title(q: dict) -> str:
    """Imported titles can be cut mid-word ("...out of trouble, C"); the description then holds the whole line."""
    t, d = (q.get("title") or "").strip(), (q.get("description") or "").strip()
    m = re.match(r"^\w+:\s*", t)
    prefix = m.group(0) if m else ""
    head = t[len(prefix):]
    if d and len(head) >= 20 and d.startswith(head) and len(d) > len(head):
        first = re.match(r"(.+?[.!?])(\s|$)", d)
        return prefix + (first.group(1) if first else d)
    return t


def journal(conn: sqlite3.Connection, cid: int) -> dict:
    people, places = _people(conn, cid), _places(conn, cid)
    camp = one(conn, "SELECT game_time_minutes, objective_quests_json FROM campaigns WHERE id = ?", (cid,))
    now, linked = camp["game_time_minutes"], set(camp.get("objective_quests") or [])
    owner = {p["id"]: p["name"] for p in people}
    out = []
    for q in rows(conn, """SELECT id, kind, title, description, status, priority, progress, giver, details_json, due_at_minutes, owner_character_id,
                                  updated_at FROM quests WHERE campaign_id = ? AND visibility = 'player' ORDER BY status = 'active' DESC, priority, id""", (cid,)):
        d = {k: v for k, v in (q.get("details") or {}).items() if k != "ledger_source" and not str(k).startswith("state/")}
        text = " ".join([q["title"], q.get("description") or "", q.get("progress") or "", q.get("giver") or "", str(d)])
        if (q.get("description") or "").startswith("Ledger objective id:"):
            q["description"] = None  # an import key, not something to read
        out.append({**{k: q[k] for k in ("id", "kind", "status", "priority", "progress", "giver", "description", "updated_at")},
                    "title": display_title(q), "details": d, "immediate": q["id"] in linked,
                    "holder": owner.get(q.get("owner_character_id")), "people": _mentioned(text, people),
                    "places": [p for p in places if p.lower() in text.lower()][:4],
                    "due_in_minutes": q["due_at_minutes"] - now if q.get("due_at_minutes") is not None else None,
                    "depends_on": d.get("depends_on") or d.get("requires") or d.get("timing"),
                    "source": (q.get("details") or {}).get("ledger_source")})
    out.sort(key=lambda q: (q["status"] != "active", not q["immediate"]))  # the immediate objective leads; stable otherwise
    return {"kinds": [{"id": k, "label": label} for k, label in JOURNAL_KINDS], "quests": out}


# ------------------------------------------------------------------ lore notebook
LORE_CATEGORIES = (  # (category, pattern), first match wins; subjects with a known prefix are categorized by it
    ("Threats", r"\b(brain boiler|mob|boss|hostile|danger|threat|murder hornet|attack|ambush|killed|victims?)\b"),
    ("Places", r"\b(saferoom|district|neighborhood|prison|fallen watch|heavensworth|market|stair|route|floor \d|over city|halls?)\b"),
    ("Factions & people", r"\b(grulke|llama|clan|network|faction|guild|manager|guide|contact|friendship|ally|allies)\b"),
    ("The party", r"\b(crawler|party|silver earth box|potion|level|clan|denton)\b"),
)


def lore_category(f: dict) -> str:
    s = f["subject"]
    if s.startswith(KNOWN):
        return "People"
    if s.startswith(INTEL):
        return "Intelligence"
    for cat, pat in LORE_CATEGORIES:
        if re.search(pat, f["fact"], re.I):
            return cat
    return "World"


def lore_notebook(conn: sqlite3.Connection, cid: int) -> list[dict]:
    people = {f"{KNOWN}{p['slug']}": p for p in _people(conn, cid)}
    out = []
    for f in rows(conn, "SELECT id, subject, fact, certainty, source FROM lore_facts WHERE campaign_id = ? AND visibility = 'player' AND subject != ?", (cid, HISTORY)):
        who = people.get(f["subject"])
        topic = who["name"] if who else f["subject"][len(INTEL):].title() if f["subject"].startswith(INTEL) else None
        out.append({**f, "category": lore_category(f), "topic": topic, "character_id": who["id"] if who else None})
    return out


# ------------------------------------------------------------------ chronology
def chronology(conn: sqlite3.Connection, cid: int) -> dict:
    """Dated events by day of game time (24-hour days), and the imported history whose dates the records don't give."""
    length = 1440
    events = rows(conn, """SELECT id, type, summary, game_time_minutes, created_at FROM events WHERE campaign_id = ? AND visibility = 'player'
                           AND rolled_back = 0 ORDER BY COALESCE(game_time_minutes, -1) DESC, id DESC LIMIT 500""", (cid,))
    days: dict[int | None, list] = {}
    for e in events:
        day = e["game_time_minutes"] // length + 1 if e["game_time_minutes"] is not None else None
        days.setdefault(day, []).append({**e, "bookkeeping": e["type"] in BOOKKEEPING_EVENTS or e["type"] == "correction"})
    undated = rows(conn, "SELECT id, fact, source FROM lore_facts WHERE campaign_id = ? AND visibility = 'player' AND subject = ? ORDER BY id", (cid, HISTORY))
    return {"day_length": length,
            "days": [{"day": d, "events": evs} for d, evs in sorted(days.items(), key=lambda kv: -1 if kv[0] is None else kv[0], reverse=True)],
            "imported_history": undated}


# ------------------------------------------------------------------ search
STOP = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with", "is", "was", "what", "who", "where", "did", "does", "about", "my", "our"}
SYNONYMS = {"promise": {"promised", "commitment", "promises", "vow"}, "family": {"wife", "daughter", "son", "kids", "children", "husband"},
            "recon": {"reconnaissance", "scout", "scouting", "survey"}, "help": {"assist", "assisted", "cooperation", "agreed"},
            "cooperation": {"help", "assist", "agreed", "accepted"}, "money": {"gold"}, "buy": {"bought", "purchase", "paid"},
            "prison": {"fallen watch"}}


def _stem(w: str) -> str:
    for suf in ("ations", "ation", "ings", "ing", "ies", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _terms(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in STOP and len(w) > 1}


def _query_terms(q: str) -> list[set[str]]:
    """One set per query word: the word and its synonyms (stemmed); a document matches a word if it has any of the set."""
    out = []
    for w in re.findall(r"[a-z0-9']+", q.lower()):
        if w in STOP or len(w) < 2:
            continue
        alts = {w} | SYNONYMS.get(w, set()) | {k for k, v in SYNONYMS.items() if w in v}
        out.append({_stem(a) for x in alts for a in x.split()})
    return out


def _snippet(text: str, words: list[set[str]], width: int = 220) -> str:
    low = text.lower()
    hits = [low.find(w) for ws in words for w in ws if low.find(w) >= 0]
    at = max(0, min(hits) - 60) if hits else 0
    return ("…" if at else "") + text[at:at + width].strip() + ("…" if at + width < len(text) else "")


def search(conn: sqlite3.Connection, cid: int, q: str, limit: int = 40) -> list[dict]:
    words = _query_terms(q)
    if not words:
        return []
    need = max(1, math.ceil(len(words) * 0.6))
    found: list[dict] = []

    def consider(kind: str, ref: dict, title: str, text: str, boost: float = 0.0) -> None:
        terms = _terms(title + " " + text)
        hit = sum(1 for ws in words if ws & terms)
        if hit < need:
            return
        phrase = 0.5 if q.lower().strip() in (title + " " + text).lower() else 0.0
        found.append({"type": kind, **ref, "title": title, "snippet": _snippet(text or title, words), "score": round(hit / len(words) + phrase + boost, 3)})

    from ..carousel.residents import unmet_clause
    for c in rows(conn, f"""SELECT c.id, c.name, c.status, n.personality, n.known_motivation FROM characters c
                            LEFT JOIN npc_profiles n ON n.character_id = c.id WHERE c.campaign_id = ? AND {unmet_clause('c')}""", (cid,)):
        consider("person", {"id": c["id"]}, c["name"], " ".join(filter(None, [c["personality"], c["known_motivation"], c["status"]])), 0.3)
    for q_ in journal(conn, cid)["quests"]:
        consider("quest", {"id": q_["id"], "status": q_["status"], "kind": q_["kind"]}, q_["title"],
                 " ".join(filter(None, [q_["description"], q_["progress"], q_["giver"], " ".join(p["name"] for p in q_["people"]),
                                        " ".join(f"{k}: {v}" for k, v in q_["details"].items())])),
                 0.6 if q_["status"] == "active" else 0)
    for g in rows(conn, "SELECT key, label, location, activity FROM party_groups WHERE campaign_id = ? AND status = 'active'", (cid,)):
        consider("group", {"key": g["key"]}, g["label"], " ".join(filter(None, [g["location"], g["activity"]])), 0.5)
    names = {p["slug"]: p["name"] for p in _people(conn, cid)}
    for f in rows(conn, "SELECT id, subject, fact, source FROM lore_facts WHERE campaign_id = ? AND visibility = 'player'", (cid,)):
        s = f["subject"]
        title = (f"What the party knows: {names.get(s[len(KNOWN):], s[len(KNOWN):])}" if s.startswith(KNOWN) else
                 f"Intelligence: {s[len(INTEL):].title()}" if s.startswith(INTEL) else "Earlier history" if s == HISTORY else s.capitalize())
        consider("history" if s == HISTORY else "lore", {"id": f["id"]}, title, f["fact"], 0.1)
    for i in rows(conn, "SELECT i.id, i.name, i.description, c.name AS owner FROM items i LEFT JOIN characters c ON c.id = i.owner_character_id WHERE i.campaign_id = ?", (cid,)):
        consider("item", {"id": i["id"], "owner": i["owner"]}, i["name"], " ".join(filter(None, [i["description"], i["owner"]])))
    for e in rows(conn, f"""SELECT id, type, summary, game_time_minutes FROM events WHERE campaign_id = ? AND visibility = 'player' AND rolled_back = 0
                            AND type NOT IN ({','.join('?' * len(BOOKKEEPING_EVENTS))}) AND type NOT LIKE 'quest_%' AND type != 'correction'
                            ORDER BY id DESC LIMIT 400""", (cid, *BOOKKEEPING_EVENTS)):
        consider("event", {"id": e["id"], "game_time_minutes": e["game_time_minutes"]}, e["type"].replace("_", " "), e["summary"])
    for m in rows(conn, """SELECT id, role, content FROM messages WHERE campaign_id = ? AND visibility = 'player' AND rolled_back = 0
                           AND role IN ('gm','player') ORDER BY id DESC LIMIT 600""", (cid,)):
        consider("story", {"id": m["id"], "role": m["role"]}, "Story" if m["role"] == "gm" else "You said", m["content"])
    for r in player_records(conn, cid, q, limit=12):  # the imported ledger's player-visible files (FTS)
        consider("record", {"id": r["id"], "document": r["document_path"]}, r["section"], re.sub(r"[\[\]]", "", r.get("snippet") or ""), -0.1)
    found.sort(key=lambda x: -x["score"])
    seen, out = set(), []
    for f in found:  # the same passage imported into several records shows once
        key = (f["type"], f["snippet"][:120])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out[:limit]
