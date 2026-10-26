"""Rules center (roadmap M7): browse the rulebooks by book and chapter, autocomplete, and one lookup that says where a rule
is written, what overrides it, which rulings stand on it and whether the console enforces it.

Everything is read from the local rulebook index (data/rulebooks.db) and the campaign's own records; nothing is sent
anywhere. Page numbers are the indexed PDF pages, the same ones every other citation in the app uses.

Precedence (the same as the GM's briefing): an approved ruling > campaign state > a campaign house rule > the official
rulebooks > GM judgment. Rulings are precedents only when approved (or recorded as the owner's CAMPAIGN / BOOK rulings);
provisional ones (ONE-SCENE, INTERPRETATION, OPEN) are listed but never cited as precedent, rejected ones not at all.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from collections import Counter, defaultdict

from ..db import rows
from ..rules import coverage

BOOK_ORDER = ("Dungeon Crawler Carl RPG — Core Rulebook", "Tutorial Floors — Crawlers & Customization", "Tutorial Floors — Rules for Survival",
              "Tutorial Floors — Game Master's Campaign Toolkit", "Tutorial Floors — Atlas", "Par for the Course")
TOC = re.compile(r"^(.{3,80}?)\s*\.{4,}\s*(\d{1,3})\s*$")
HEAD = re.compile(r"^[A-Z][A-Z0-9’'&/ ,\-]{3,38}$")
NOT_ENTRY = re.compile(r"^(TABLE|ROLL|CHOOSE|DESCRIPTION|SKILLS?|UPGRADES|CHAPTER|RANK|LEVEL|NOTE|EXAMPLE|SPELLS?|STAT|CLASS TYPE|BENEFITS?|"
                       r"OR |AND |THE END)\b")
PRECEDENTS = ("BOOK", "CAMPAIGN")
PROVISIONAL = ("INTERPRETATION", "ONE-SCENE", "OPEN")

# What the console's engine does for broad rule families that no house rule covers (evidence for "implemented").
ENGINE = (
    (r"\b(skill check|stat check|advantage|disadvantage|difficulty|help)\b", "tested", "Skill/Stat Checks: checks.perform_check with Help, Advantage and degrees"),
    (r"\b(ammunition|ammo|reload|arrows|bolts)\b", "tested", "attacks spend ammunition from the wielder's named supply (rules/combat.py)"),
    (r"\b(attack|evade|combat|initiative|interrupt|catcher|intervene|surprise|damage|health bar|wrasslin|held|grapple|toss)\b", "tested",
     "combat engine (rules/combat.py): rounds, Actions, Interrupts, Evade, damage, DR and Health Bar slots"),
    (r"\b(craft|crafting|engineering|fabricate|salvage|blueprint)\b", "tested", "crafting tool (rules/crafting.py) with Table 47 salvage"),
    (r"\b(trap|traps)\b", "tested", "trap tool (rules/traps.py): records, deploy, trigger, reset"),
    (r"\b(rest|mending|dungeon day|grind|grinding|level up|advancement)\b", "tested", "rest, mending, grinding and advancement (rules/state_ops.py, checks.py)"),
    (r"\b(mana|spell)\b", "partial", "mana spend and limited uses are tracked; spell effects are applied by the GM"),
)


# ------------------------------------------------------------------ the index (built once per process)
class Index:
    def __init__(self, rc: sqlite3.Connection):
        self.books: list[dict] = []
        self.entries: list[dict] = []          # {name, norm, doc_id, book, page, chapter, text}
        rank = {t: i for i, t in enumerate(BOOK_ORDER)}
        docs = rc.execute("SELECT id, title FROM documents").fetchall()
        docs = sorted(docs, key=lambda d: min((i for t, i in rank.items() if d["title"].startswith(t)), default=99))
        for d in docs:
            pages: dict[int, list[str]] = defaultdict(list)
            for r in rc.execute("SELECT page, text FROM chunks WHERE document_id = ? ORDER BY page, seq", (d["id"],)):
                pages[r["page"]].append(r["text"])
            lines = {p: "\n".join(t).splitlines() for p, t in pages.items()}
            offset = Counter(p - int(ls[-k]) for p, ls in lines.items() for k in (1, 2, 3)
                             if len(ls) >= k and ls[-k].strip().isdigit() and 0 < int(ls[-k]) < 600).most_common(1)
            off = offset[0][0] if offset and offset[0][1] >= 20 else 0
            toc = [(m.group(1).strip(" ."), int(m.group(2)) + off) for p, ls in lines.items() if p < 15 for ln in ls
                   if (m := TOC.match(ln.strip())) and m.group(1).strip(" .")]
            chapters = []
            for title, page in toc:
                if re.match(r"(CHAPTER \d+|INTRODUCTION|APPENDIX|INDEX)", title, re.I):
                    chapters.append({"title": title.title().replace("Chapter", "Chapter"), "page": page, "sections": []})
                elif chapters and {"title": title, "page": page} not in chapters[-1]["sections"]:  # TOC lines repeat across its page breaks
                    chapters[-1]["sections"].append({"title": title, "page": page})
            seen = set()
            book_entries = []
            for p in sorted(lines):
                ls = lines[p]
                for i, ln in enumerate(ls):
                    s = ln.strip()
                    if not HEAD.match(s) or NOT_ENTRY.match(s) or any(len(w) < 2 for w in s.split()) or (len(s.split()) == 1 and len(s) < 4):
                        continue
                    if not re.search(r"[a-z]{3}", " ".join(ls[i + 1:i + 3])) or (s, p) in seen:
                        continue
                    if i and (ls[i - 1].rstrip().endswith("-") or HEAD.match(ls[i - 1].strip())):
                        continue  # the tail of a hyphenated or wrapped heading ("COM-" / "PLICATION")
                    seen.add((s, p))
                    body = "\n".join(ls[i + 1:])
                    if len(body) < 500 and p + 1 in lines:
                        body += "\n" + "\n".join(lines[p + 1])
                    nxt = re.search(r"\n[A-Z][A-Z0-9’'&/ ,\-]{3,38}\n", body[100:])
                    text = body[: 100 + nxt.start() if nxt else 1500][:1500].strip()
                    ch = next((c["title"] for c in reversed(chapters) if c["page"] <= p), None)
                    e = {"name": s.title().replace("’S", "’s").replace("'S", "'s"), "norm": _norm(s), "doc_id": d["id"], "book": d["title"],
                         "page": p, "chapter": ch, "text": text}
                    book_entries.append(e)
            self.entries += book_entries
            self.books.append({"id": d["id"], "title": d["title"], "page_offset": off, "chapters": chapters, "entry_count": len(book_entries)})

    def find(self, name: str) -> list[dict]:
        n = _norm(name)
        return [e for e in self.entries if e["norm"] == n]


_INDEX: dict[str, Index] = {}
_LOCK = threading.Lock()


def index(rc: sqlite3.Connection, key: str) -> Index:
    with _LOCK:
        if key not in _INDEX:
            _INDEX[key] = Index(rc)
        return _INDEX[key]


def _reflow(text: str) -> str:
    """PDF line breaks inside a sentence become spaces; breaks after a sentence, before a bullet or a short type line stay."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out = []
    for ln in lines:
        if out and not re.search(r"[.:!?)\]”]$", out[-1]) and not ln.startswith(("•", "-")) and len(out[-1]) > 30:
            out[-1] = (out[-1][:-1] if out[-1].endswith("-") else out[-1] + " ") + ln
        else:
            out.append(ln)
    return "\n".join(out)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower().replace("’", "'").replace("'", "")).strip()


# ------------------------------------------------------------------ browse and autocomplete
def browse(ix: Index, book_id: int | None = None) -> dict:
    if book_id is None:
        return {"books": [{k: b[k] for k in ("id", "title", "entry_count")} | {"chapters": len(b["chapters"])} for b in ix.books]}
    b = next((x for x in ix.books if x["id"] == book_id), None)
    if not b:
        return {"book": None}
    entries = [{"name": e["name"], "page": e["page"], "chapter": e["chapter"]} for e in ix.entries if e["doc_id"] == book_id]
    return {"book": {k: b[k] for k in ("id", "title", "page_offset")}, "chapters": b["chapters"], "entries": entries}


def suggest(ix: Index | None, conn: sqlite3.Connection, cid: int | None, q: str, limit: int = 12) -> list[dict]:
    n = _norm(q)
    if len(n) < 2:
        return []
    out, seen = [], set()

    def add(kind: str, label: str, extra: dict) -> None:
        key = (kind, _norm(label))
        if key in seen:
            return
        seen.add(key)
        out.append({"kind": kind, "label": label, **extra})

    if cid is not None:
        for h in rows(conn, "SELECT id, section, title FROM house_rules WHERE campaign_id = ?", (cid,)):
            if n in _norm(h["title"]):
                add("house_rule", h["title"], {"id": h["id"], "section": h["section"]})
    if ix is not None:
        hits = sorted((e for e in ix.entries if any(w.startswith(n) for w in e["norm"].split()) or e["norm"].startswith(n)),
                      key=lambda e: (not e["norm"].startswith(n), len(e["norm"])))
        for e in hits:
            add("entry", e["name"], {"ref": f"{e['book']}, p. {e['page']}"})
            if len(out) >= limit:
                break
    return out[:limit]


# ------------------------------------------------------------------ rulings
def ruling_status(r: dict) -> str:
    """precedent | provisional | rejected: only precedents are cited in later turns."""
    if r.get("review") == "rejected":
        return "rejected"
    if r.get("review") == "approved" or (r.get("label") in PRECEDENTS and r.get("certainty") == "canonical"):
        return "precedent"
    return "provisional"


def rulings(conn: sqlite3.Connection, cid: int) -> list[dict]:
    out = rows(conn, """SELECT id, turn_id, topic, ruling, certainty, label, sources_json, review, review_note, reviewed_at, created_at
                        FROM rulings WHERE campaign_id = ? ORDER BY id DESC""", (cid,))
    for r in out:
        r["status"] = ruling_status(r)
    return out


# ------------------------------------------------------------------ lookup
def _words(s: str) -> set[str]:
    return {w for w in _norm(s).split() if len(w) > 2 and w not in {"the", "and", "for", "with", "rule", "rules"}}


def lookup(ix: Index | None, conn: sqlite3.Connection, cid: int | None, q: str) -> dict:
    words = _words(q)
    entries = ix.find(q) if ix else []
    if ix and not entries and words:  # no exact entry: best headings by word overlap
        scored = sorted(((len(words & set(e["norm"].split())), e) for e in ix.entries), key=lambda x: -x[0])
        entries = [e for s, e in scored if s and s >= max(1, len(words) - 1)][:3]
    book = [{"name": e["name"], "ref": f"{e['book']}, p. {e['page']}", "document_id": e["doc_id"], "page": e["page"],
             "chapter": e["chapter"], "excerpt": _reflow(e["text"])} for e in entries[:4]]
    house, rl = [], []
    if cid is not None:
        for h in rows(conn, "SELECT id, section, title, text, label, mechanical_status, source FROM house_rules WHERE campaign_id = ?", (cid,)):
            score = len(words & _words(h["title"])) * 2 + len(words & _words(h["text"][:2000]))
            if score >= max(2, len(words)):
                cov = coverage.classify({"section": h["section"], "title": h["title"]})
                house.append({**h, "text": h["text"][:1200], "score": score, "coverage": {k: cov[k] for k in ("status", "evidence", "gap", "priority")}})
        house = sorted(house, key=lambda h: -h["score"])[:4]
        for r in rulings(conn, cid):
            if r["status"] != "rejected" and len(words & _words(r["topic"] + " " + r["ruling"])) >= max(1, min(2, len(words))):
                rl.append(r)
        rl = rl[:5]
    # what kind of rule it is comes from the query and the entry's own type line ("Dexterity", "Melee Attack, STR", "Passive, Interrupt"),
    # never from words anywhere in its text (a Drowning entry that mentions "rounds" is not about ammunition)
    kind = (book[0]["excerpt"].splitlines() or [""])[0] if book else ""
    if re.match(r"^(Strength|Dexterity|Intelligence|Constitution|Charisma|None)\b", kind):
        kind += " skill check"
    engine = next(({"status": s, "evidence": ev} for pat, s, ev in ENGINE
                   if re.search(pat, " ".join([q, book[0]["name"] if book else "", kind]), re.I)), None)
    if house:
        status = house[0]["coverage"]  # the best-matching house rule's row in the coverage matrix
        implementation = {"status": status["status"], "evidence": status["evidence"] or (engine or {}).get("evidence"), "gap": status["gap"], "source": "coverage matrix"}
    elif engine:
        implementation = {**engine, "gap": "", "source": "engine"}
    else:
        implementation = {"status": "no_code", "evidence": "", "gap": "No dedicated code: the GM applies the rule text with the general tools.", "source": None}
    layers = []
    precedents = [r for r in rl if r["status"] == "precedent"]
    if precedents:
        layers.append({"layer": "ruling", "says": f"Ruling '{precedents[0]['topic']}' ({precedents[0]['label']}) governs first."})
    if house:
        layers.append({"layer": "house_rule", "says": f"House rule {('§' + house[0]['section'] + ' ') if house[0]['section'] else ''}{house[0]['title']} "
                                                      f"overrides the book where they differ."})
    if book:
        layers.append({"layer": "book", "says": f"{book[0]['ref']} applies where nothing above changes it."})
    if not layers:
        layers.append({"layer": "gm", "says": "Nothing in the books or records matches: the GM makes a ruling and records it."})
    if any(r["status"] == "provisional" for r in rl):
        layers.append({"layer": "note", "says": "Provisional rulings are listed for context only; they are not precedent until approved."})
    return {"query": q, "book": book, "house_rules": house, "rulings": rl, "implementation": implementation, "precedence": layers}


def cite(ix: Index, ref: str) -> dict | None:
    """'Dungeon Crawler Carl RPG — Core Rulebook, p. 201' -> the document id and page, for opening a story citation."""
    m = re.match(r"^(.*),\s*p\.\s*(\d+)\s*$", ref.strip())
    if not m:
        return None
    b = next((b for b in ix.books if b["title"] == m.group(1).strip()), None)
    return {"document_id": b["id"], "page": int(m.group(2)), "document": b["title"]} if b else None
