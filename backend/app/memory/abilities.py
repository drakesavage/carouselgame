"""What each skill, spell and class/race ability in a dossier actually does.

Explanations are derived reference text, cached per campaign in `ability_explanations` (not campaign canon, so not part
of save points). Sources, in order of authority:

* template  — a plain bonus line ("+2 Bow", "+3 Dexterity +2 Intelligence") explained without a model.
* rulebook  — the ability's own entry in an indexed rulebook (found by its heading); Codex paraphrases it briefly and
              cites the page. Rulebook text is licensed, so the stored explanation is a short paraphrase, never a copy.
* record    — the ledger defines it (a class feature, a skill description); Codex restates it concretely.
* homebrew  — nothing defines it; Codex writes a conservative interpretation consistent with the core rules, labelled
              as such so the owner can correct it.

Codex runs through the isolated GM runner (the user's ChatGPT sign-in; no API billing). Only player-visible sheet text
and rulebook passages are sent, never GM secrets.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

from ..db import now_iso, rows, transaction
from .dossier import _norm

STATS = {"strength", "intelligence", "constitution", "dexterity", "charisma"}
BONUS = re.compile(r"([+-]\d+)\s+([A-Za-z][A-Za-z' ’-]*?)(?=\s*(?:[+-]\d|;|,|$| and ))")
# Rulebooks searched for an entry heading, most authoritative first (documents.title prefixes).
BOOK_ORDER = ("Dungeon Crawler Carl RPG — Core Rulebook", "Tutorial Floors — Crawlers & Customization", "Tutorial Floors — Rules for Survival",
              "Tutorial Floors — Game Master's Campaign Toolkit", "Par for the Course")
HEADING = re.compile(r"\n[A-Z][A-Z0-9’'&/ ,\-]{2,40}\n")
BATCH = 15


@dataclass
class Ability:
    key: str
    kind: str          # skill | spell | gear skill | class feature | class benefit | race benefit | specialty | perk | upgrade
    name: str
    record: str | None = None
    owners: list[str] = field(default_factory=list)
    context: str = ""  # class / race / parent skill


def key_for(kind: str, name: str) -> str:
    group = {"spell": "skill", "gear skill": "skill", "class benefit": "feature", "class feature": "feature"}.get(kind, kind)
    return f"{group}:{_norm(name)}"


# ------------------------------------------------------------------ what the dossiers show
def collect(conn: sqlite3.Connection, cid: int) -> dict[str, Ability]:
    from .dossier import build
    out: dict[str, Ability] = {}

    def add(kind: str, name: str, record: str | None, owner: str, context: str = "") -> None:
        if not name or not name.strip():
            return
        k = key_for(kind, name)
        a = out.setdefault(k, Ability(k, kind, name.strip(), None, [], context))
        if record and len(record) > len(a.record or ""):
            a.record = record
        if owner not in a.owners:
            a.owners.append(owner)

    for (char_id,) in conn.execute("SELECT id FROM characters WHERE campaign_id = ?", (cid,)).fetchall():
        d = build(conn, cid, char_id)
        if not d:
            continue
        who, klass, race = d["name"], d["build"]["class_name"] or "", d["build"]["race"] or ""
        for h in d["build"]["highlights"]:
            if h["source"] in ("skill specialty", "skill perk"):
                # "Engineering specialty (Rank 8)" names the parent skill; it doesn't define the specialty
                add("specialty" if h["source"] == "skill specialty" else "perk", h["title"], None, who, h["detail"] or "")
            else:
                add(h["source"], h["title"], h["detail"], who, f"{klass} class")
        for b in d["build"]["class_benefits"]:
            if b:
                add("class benefit", str(b), None, who, f"{klass} class")
        for b in d["build"]["race_benefits"]:
            add("race benefit", b, None, who, f"{race} race")
        for s in d["skills"]:
            kind = "spell" if s["kind"] == "spell" else "gear skill" if s["gear_granted"] else "skill"
            add(kind, s["name"], s["description"], who)
            for u in s["upgrades"]:
                add("upgrade", f"{s['name']}: {u}", u, who, s["name"])
    return out


def template(a: Ability) -> tuple[str, str] | None:
    """Plain bonus lines need no model: '+3 Dexterity +2 Intelligence', '+2 Bow', '+1 DR Buff' stays for the rules."""
    text = a.name.strip()
    if a.kind not in ("class benefit", "race benefit") or not re.match(r"^[+-]\d", text):
        return None
    parts = BONUS.findall(text + " ")
    if not parts or re.sub(r"[+-]\d+\s+[A-Za-z][A-Za-z' ’-]*|[;,]|\band\b|\s", "", text):
        return None
    lines = []
    for n, target in parts:
        target = target.strip()
        if target.lower().startswith(("to ", "all ")):
            return None  # "+2 to all Stats": explained in words
        if re.search(r"\b(dr|buff|aura|move|favor)\b", target, re.I):
            return None  # not a plain Stat or Skill bonus: explained from the rules
        if target.lower() in STATS:
            lines.append(f"{n} to {target.title()} (a Stat; it also raises that Stat's modifier where it crosses a threshold)")
        else:
            lines.append(f"{n} Rank{'s' if abs(int(n)) != 1 else ''} in the {target} Skill")
    source = "class build" if a.kind == "class benefit" else "race"
    summary = "; ".join(f"{n} {t.strip()}" for n, t in parts)
    return summary, f"A {source} bonus applied when the {source.split()[0]} was chosen: " + "; ".join(lines) + "."


# ------------------------------------------------------------------ rulebook entries
class RuleIndex:
    """Ability entries in the rulebooks, found by their all-caps heading ('STEALTH', 'HOT STUFF AURA')."""

    def __init__(self, rc: sqlite3.Connection | None):
        self.chunks: list[tuple[str, int, str]] = []
        if rc is None:
            return
        rank = {t: i for i, t in enumerate(BOOK_ORDER)}
        docs = {r["id"]: r["title"] for r in rc.execute("SELECT id, title FROM documents")}
        got = rc.execute("SELECT document_id, page, seq, text FROM chunks ORDER BY document_id, page, seq").fetchall()
        order = sorted(got, key=lambda r: (min((i for t, i in rank.items() if docs[r["document_id"]].startswith(t)), default=99), r["document_id"], r["page"], r["seq"]))
        self.chunks = [(docs[r["document_id"]], r["page"], r["text"]) for r in order]

    def entry(self, name: str) -> tuple[str, str] | None:
        head = re.escape(name.upper().replace("'", "’")).replace("’", "['’]")
        pat = re.compile(rf"(^|\n){head}\s*\n")
        for i, (doc, page, text) in enumerate(self.chunks):
            m = pat.search(text)
            if not m:
                continue
            body = text[m.end():]
            if len(body) < 600 and i + 1 < len(self.chunks) and self.chunks[i + 1][0] == doc:
                body += "\n" + self.chunks[i + 1][2]
            nxt = HEADING.search(body, 120)
            return f"{name.upper()}\n{body[: nxt.start() if nxt else 1400][:1400].strip()}", f"{doc}, p. {page}"
        return None


# ------------------------------------------------------------------ Codex
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["key", "summary", "explanation", "basis", "citation"],
    "properties": {"key": {"type": "string"}, "summary": {"type": "string"}, "explanation": {"type": "string"},
                   "basis": {"type": "string", "enum": ["rulebook", "record", "homebrew"]}, "citation": {"type": "string"}}}}}}

DEVELOPER = """You write the in-game help text for abilities in a Dungeon Crawler Carl tabletop RPG campaign app.
For each ability in the user message, return one item with the same key:
- summary: one line, at most 14 words, saying what it does.
- explanation: 2-4 plain sentences (at most 70 words) a player can act on: what it does, when or how it is used
  (Action, Interrupt, Passive, once per scene...), and its limits or costs. Use the game's terms (Skill Check, Rank,
  Advantage, Health Bar slots, DR, AI Favor) correctly.
- basis and citation:
  * A "rulebook entry" is given: paraphrase it in your own words (never copy sentences); basis "rulebook";
    citation exactly the given reference.
  * Only a "record" from the campaign ledger is given: restate it concretely without changing what it says;
    basis "record"; citation "campaign record".
  * Neither, or the record is only a name: this is homebrew. Write a conservative interpretation that fits the
    name, the owner's class or race, and the core rules below. Do not invent large numbers. basis "homebrew";
    citation "homebrew interpretation".
  If both a record and a rulebook entry are given, the record wins where they differ (it is the campaign's version).
Class and race benefits are bought at character creation and are permanent, always-on features unless their text says
otherwise (a "+1 DR Buff" benefit is a lasting +1 DR, not a temporary condition the GM switches on).
Core rules for context: a Skill Check is d20 + Skill Rank + Stat modifier vs a Difficulty; Advantage rolls twice and
keeps the higher. In combat each crawler has Actions per round and may use Interrupts on others' turns. Damage
removes Health Bar slots after Damage Resistance (DR) is subtracted. Buffs and Debuffs are temporary conditions.
Reply only with the JSON object required by the schema."""


def _prompt(batch: list[tuple[Ability, tuple[str, str] | None, list[tuple[str, str]]]]) -> str:
    out = []
    for a, rule, related in batch:
        lines = [f"## key: {a.key}", f"name: {a.name}", f"kind: {a.kind}", f"owners: {', '.join(a.owners)}"]
        if a.context:
            lines.append(f"context: {a.context}")
        if a.record:
            lines.append(f"record: {a.record}")
        if rule:
            lines.append(f"rulebook entry ({rule[1]}):\n{rule[0]}")
        for text, ref in related:
            lines.append(f"possibly related rulebook passage ({ref}; use only if it really is about this ability):\n{text}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def explain_missing(conn: sqlite3.Connection, cid: int, settings, *, rc: sqlite3.Connection | None = None, provider=None,
                    limit: int | None = None, log=print) -> dict:
    """Explain every dossier ability that has no explanation yet. Returns counts by basis."""
    have = {r["key"] for r in rows(conn, "SELECT key FROM ability_explanations WHERE campaign_id = ?", (cid,))}
    todo = [a for a in collect(conn, cid).values() if a.key not in have]
    counts: dict[str, int] = {}

    def save(a: Ability, summary: str, text: str, basis: str, citation: str | None, model: str) -> None:
        with transaction(conn):
            conn.execute("""INSERT OR REPLACE INTO ability_explanations (campaign_id, key, name, summary, explanation, basis, citation, model, created_at)
                            VALUES (?,?,?,?,?,?,?,?,?)""", (cid, a.key, a.name, summary.strip(), text.strip(), basis, citation, model, now_iso()))
        counts[basis] = counts.get(basis, 0) + 1

    rest = []
    for a in todo:
        t = template(a)
        if t:
            save(a, t[0], t[1], "template", f"{a.context or 'build'} record", "template")
        else:
            rest.append(a)
    if limit is not None:
        rest = rest[:limit]
    if not rest:
        return counts
    if rc is None and settings.rulebook_db.exists():
        from ..reference.rulebooks import open_db
        rc = open_db(settings.rulebook_db)
    index = RuleIndex(rc)
    work = []
    for a in rest:
        rule = None if a.kind in ("specialty", "perk", "upgrade") else index.entry(a.name)
        related: list[tuple[str, str]] = []
        if rule is None and rc is not None and a.kind in ("class benefit", "race benefit", "class feature"):
            from ..reference.rulebooks import search
            related = [(p.text[:700], p.ref()) for p in search(rc, a.name, limit=2)]
        work.append((a, rule, related))
    if provider is None:
        from ..gm.providers import CodexCliProvider
        provider = CodexCliProvider(settings, effort="low")
    from ..gm.providers import AGENTS_NOTICE
    for i in range(0, len(work), BATCH):
        batch = work[i:i + BATCH]
        raw, _ = provider._run(_prompt(batch), SCHEMA, "abilities", DEVELOPER + "\n\n" + AGENTS_NOTICE)
        items = {x["key"]: x for x in json.loads(raw)["items"]}
        for a, rule, related in batch:
            x = items.get(a.key)
            if not x or not x["explanation"].strip():
                continue
            basis = x["basis"] if x["basis"] in ("rulebook", "record", "homebrew") else "homebrew"
            given = ([rule[1]] if rule else []) + [ref for _, ref in related]
            if basis == "rulebook" and not given:
                basis = "record" if a.record else "homebrew"  # a citation must come from a passage it was given
            cited = x["citation"] if x["citation"] in given else given[0] if given else None
            citation = cited if basis == "rulebook" else "campaign record" if basis == "record" else "homebrew interpretation"
            save(a, x["summary"], x["explanation"], basis, citation, "codex")
        log(f"abilities: {min(i + BATCH, len(work))}/{len(work)} explained")
    return counts


def lookup(conn: sqlite3.Connection, cid: int) -> dict[str, dict]:
    return {r["key"]: r for r in rows(conn, """SELECT key, summary, explanation, basis, citation FROM ability_explanations
                                               WHERE campaign_id = ?""", (cid,))}


def attach(conn: sqlite3.Connection, cid: int, d: dict) -> dict:
    """Give each ability in a built dossier its explanation (`explain`, or None when not written yet)."""
    known = lookup(conn, cid)
    for h in d["build"]["highlights"]:
        kind = {"skill specialty": "specialty", "skill perk": "perk"}.get(h["source"], h["source"])
        h["explain"] = known.get(key_for(kind, h["title"]))
    d["build"]["race_explain"] = {b: known.get(key_for("race benefit", b)) for b in d["build"]["race_benefits"]}
    d["build"]["class_explain"] = {str(b): known.get(key_for("class benefit", str(b))) for b in d["build"]["class_benefits"] if b}
    for s in d["skills"]:
        s["explain"] = known.get(key_for("skill", s["name"]))
        s["upgrade_explain"] = [known.get(key_for("upgrade", f"{s['name']}: {u}")) for u in s["upgrades"]]
    for e in d["effects"]:
        e["explain"] = known.get(key_for({"class": "class feature", "race": "race benefit"}.get(e["type"], e["type"]), e["name"]))
    missing = sum(1 for h in d["build"]["highlights"] if not h["explain"]) + sum(1 for s in d["skills"] if not s["explain"])
    d["explanations_missing"] = missing
    return d


if __name__ == "__main__":  # python -m app.memory.abilities <campaign_id> [limit]
    import sys
    from ..config import Settings
    from ..db import connect, init_db
    s = Settings()
    conn = connect(s.campaign_db)
    init_db(conn)
    print(explain_missing(conn, int(sys.argv[1]), s, limit=int(sys.argv[2]) if len(sys.argv) > 2 else None))
