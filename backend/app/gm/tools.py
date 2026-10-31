"""GM tools: the only way the model affects the game. Each tool validates its arguments and runs real code
(rules engine, retrieval). Results go back to the model; player-visible effects are collected for the UI."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ..db import now_iso, one, rows
from ..memory.rulings import ruling_status
from ..memory.views import fts_query
from ..reference import rulebooks
from ..rules.engine import ChangeError, apply_change, ops_reference, resolve_character

# Tools whose results the model does not need before narrating (see OUTPUT_PROTOCOL).
BOOKKEEPING_TOOLS = {"apply_changes", "record_ruling", "gm_note", "system_notice", "reveal_secret", "suggest_image", "save_game"}

TOOL_SPECS: list[dict] = [
    {"name": "apply_changes",
     "description": "Validate and apply durable state changes. Each change is an object with an `op`. Characters may be referenced by "
                    "name, slug, numeric id or 'player'; items by name or item_id. character null/'shared' = shared stash. "
                    "Use group/move_group/move_character for party splits (never teleport "
                    "people: only move who actually goes), scene to keep the current scene current, remember for NPC memories and private "
                    "beats, quest kind commitment/reservation for promises and held items (a reservation is never an owned item: when it is "
                    "claimed, complete the reservation quest earlier in the same batch, then add_item), "
                    "use_ability for limited uses, rest in saferooms (a rest advances the clock itself: never also advance_time for it), advance_time mending for calm hours. Scene fields, quest details, groups "
                    "and remember(visibility player) are SHOWN TO THE PLAYER: put hidden material only in gm_note or remember(visibility gm). Ops:\n" + ops_reference(),
     "parameters": {"changes": "[change objects]"}},
    {"name": "search_rules",
     "description": "Search campaign house rules, established rulings, and the indexed official rulebooks (with page refs). Give 2-4 "
                    "phrasings: the rule's name, synonyms, and the situation in plain words. Campaign results outrank official ones.",
     "parameters": {"queries": "[str]", "limit": "int? (default 5)"}},
    {"name": "read_rule_page",
     "description": "Read the full text of an official rulebook page found by search_rules (use the document_id and page).",
     "parameters": {"document_id": "int", "page": "int"}},
    {"name": "lookup_records",
     "description": "Search imported campaign records (ledger YAML/Markdown, including GM-only companion sheets), lore facts, and the "
                    "event log. Returns section ids and snippets; use read_record for full text.",
     "parameters": {"query": "str", "limit": "int? (default 6)"}},
    {"name": "read_record", "description": "Read one imported record section in full.", "parameters": {"section_id": "int"}},
    {"name": "record_ruling",
     "description": "Record an adjudication so it stays consistent. label: BOOK | CAMPAIGN | INTERPRETATION | ONE-SCENE | OPEN.",
     "parameters": {"topic": "str", "ruling": "str", "label": "str", "certainty": "canonical|provisional|uncertain?", "sources": "[str]?"}},
    {"name": "gm_note", "description": "Store GM-only continuity (hidden consequence, NPC private reaction/plan, secret clock). Never shown to the player.",
     "parameters": {"subject": "str", "content": "str", "kind": "plot|motivation|surprise|note?"}},
    {"name": "reveal_secret",
     "description": "Mark a GM secret revealed after the player legitimately learned it, recording only the part they learned as a player-visible fact.",
     "parameters": {"secret_id": "int", "player_visible_fact": "str", "subject": "str"}},
    {"name": "system_notice",
     "description": "Emit an out-of-fiction notice to the player (style: notice | warning | tooltip), e.g. a ticket being dealt or a "
                    "rule the town just enforced.",
     "parameters": {"text": "str (Markdown)", "style": "str?"}},
    {"name": "suggest_image",
     "description": "Suggest a picture for the player to approve (rendered locally by ComfyUI; nothing renders without their click). "
                    "Use sparingly, at most one per turn, when the party first meets a notable character or monster, gets a notable "
                    "item, or enters a striking new place. kind: character | mob | item | scene (scene = the current scene) | "
                    "location (a place on the party's map, by name; reused for every scene there). "
                    "description: what it LOOKS like, only what the party has seen; never GM-only material. Skip it if the "
                    "subject already has a picture.",
     "parameters": {"kind": "str", "subject": "str (name; ignored for scene)", "description": "str (visual only, <= 60 words)", "reason": "str?"}},
]
TOOL_SPECS.append(
    {"name": "save_game",
     "description": "The player asked to save: creates a named manual save point when this turn commits (restore it from Saves & Settings).",
     "parameters": {"label": "str"}})
TOOL_NAMES = [t["name"] for t in TOOL_SPECS]



class _Search(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=6)
    limit: int = Field(default=5, ge=1, le=10)


class _Ruling(BaseModel):
    topic: str
    ruling: str
    label: str = "INTERPRETATION"
    certainty: str = "provisional"
    sources: list[str] = Field(default_factory=list)


@dataclass
class TurnEffects:
    """Everything the player should see from this turn."""
    changes: list[str] = field(default_factory=list)
    notices: list[dict] = field(default_factory=list)
    rules_cited: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    save_label: str | None = None                            # save_game: a manual save point after the turn commits


class ToolRunner:
    def __init__(self, conn: sqlite3.Connection, rule_conn: sqlite3.Connection | None, campaign_id: int, turn_id: int, settings=None):
        self.conn, self.rule_conn = conn, rule_conn
        self.settings = settings  # the app's Settings (art suggestions pick their painter from it); None = from the environment
        self.cid, self.turn_id = campaign_id, turn_id
        self.effects = TurnEffects()

    def run(self, name: str, args: dict[str, Any]) -> dict:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return {"ok": False, "result": {"error": f"unknown tool {name!r}"}}
        try:
            return {"ok": True, "result": fn(args)}
        except (ChangeError, ValidationError, KeyError, ValueError, TypeError) as exc:
            msg = str(exc) if not isinstance(exc, ValidationError) else json.dumps(exc.errors(include_url=False), default=str)[:800]
            self.effects.errors.append(f"{name}: {msg}")
            return {"ok": False, "result": {"error": msg}}

    def t_save_game(self, a: dict) -> dict:
        self.effects.save_label = (str(a.get("label") or "").strip() or "Player save")[:120]
        return {"summary": f"Save point '{self.effects.save_label}' will be created when this turn commits"}

    # -- state
    def t_apply_changes(self, a: dict) -> dict:
        changes = a.get("changes")
        if not isinstance(changes, list) or not changes:
            raise ValueError("changes must be a non-empty list")
        # One call = one atomic batch: if any change is invalid, none apply (drinking a potion must both
        # remove it and heal, or do neither).
        results, applied = [], []
        self.conn.execute("SAVEPOINT batch")
        for ch in changes:
            try:
                r = apply_change(self.conn, self.cid, self.turn_id, ch if isinstance(ch, dict) else {})
                results.append({"ok": True, "summary": r["summary"]})
                applied.append(r)
            except ChangeError as exc:
                results.append({"ok": False, "op": ch.get("op") if isinstance(ch, dict) else None, "error": str(exc)})
        if not all(r["ok"] for r in results):
            self.conn.execute("ROLLBACK TO batch")
            self.conn.execute("RELEASE batch")
            errors = "; ".join(r["error"] for r in results if not r["ok"])
            raise ChangeError(f"no changes applied (batch is atomic): {errors}")
        self.conn.execute("RELEASE batch")
        self.effects.changes.extend(r["summary"] for r in applied if r.get("visibility", "player") == "player")
        return {"results": results}

    # -- knowledge
    def t_search_rules(self, a: dict) -> dict:
        req = _Search.model_validate(a)
        q = fts_query(" ".join(req.queries))
        campaign = rows(self.conn, """SELECT h.id, h.section, h.title, h.label, substr(h.text, 1, 1200) AS text FROM house_rules h
                                      WHERE h.campaign_id = ? AND h.id IN (SELECT id FROM house_rules WHERE campaign_id = ?)
                                      ORDER BY h.id""", (self.cid, self.cid))
        terms = [t.strip('"') for t in q.split(" OR ")]
        scored = sorted(((sum(t in (h["title"] + " " + h["text"]).lower() for t in terms), h) for h in campaign), key=lambda x: -x[0])
        campaign_hits = [{"source": "CAMPAIGN HOUSE RULE", **h} for s, h in scored[:3] if s >= 2]
        rulings = [{"source": "PRECEDENT RULING" if st == "precedent" else "PROVISIONAL RULING (not precedent)",
                    **{k: r[k] for k in ("topic", "ruling", "label", "certainty")}}
                   for r in rows(self.conn, "SELECT topic, ruling, label, certainty, review FROM rulings WHERE campaign_id = ? ORDER BY id DESC LIMIT 200", (self.cid,))
                   if (st := ruling_status(r)) != "rejected" and sum(t in (r["topic"] + " " + r["ruling"]).lower() for t in terms) >= 2][:3]
        official = []
        if self.rule_conn is not None:
            for p in rulebooks.search(self.rule_conn, req.queries, limit=req.limit):
                doc_id = self.rule_conn.execute("SELECT document_id FROM chunks WHERE id = ?", (p.chunk_id,)).fetchone()[0]
                official.append({"source": "OFFICIAL RULEBOOK", "ref": p.ref(), "document_id": doc_id, "page": p.page, "text": p.text[:1100]})
                self.effects.rules_cited.append(p.ref())
        return {"campaign": campaign_hits, "rulings": rulings, "official": official,
                "note": "Campaign rulings/house rules outrank official text where they apply." if campaign_hits or rulings else None,
                "index_status": "no rulebooks indexed" if self.rule_conn is None else None}

    def t_read_rule_page(self, a: dict) -> dict:
        if self.rule_conn is None:
            raise ValueError("no rulebook index")
        doc = self.rule_conn.execute("SELECT title FROM documents WHERE id = ?", (int(a["document_id"]),)).fetchone()
        if not doc:
            raise ValueError("unknown document_id")
        text = rulebooks.page_text(self.rule_conn, int(a["document_id"]), int(a["page"]))
        self.effects.rules_cited.append(f"{doc['title']}, p. {a['page']}")
        return {"ref": f"{doc['title']}, p. {a['page']}", "text": text[:4000]}

    def t_lookup_records(self, a: dict) -> dict:
        query = str(a.get("query", ""))
        limit = min(int(a.get("limit") or 6), 12)
        q = fts_query(query)
        sections = rows(self.conn, """SELECT s.id, s.document_path, s.section, s.visibility,
                                             snippet(source_sections_fts, 1, '«', '»', ' … ', 48) AS snippet
                                      FROM source_sections_fts JOIN source_sections s ON s.id = source_sections_fts.rowid
                                      WHERE source_sections_fts MATCH ? AND s.campaign_id = ? ORDER BY rank LIMIT ?""", (q, self.cid, limit))
        for s in sections:
            if s["visibility"] == "gm":
                s["visibility"] = "GM-ONLY"
        terms = [t.strip('"') for t in q.split(" OR ")][:8]
        like = " OR ".join("lower(summary) LIKE ?" for _ in terms) or "0"
        events = rows(self.conn, f"SELECT id, type, summary, visibility, game_time_minutes FROM events WHERE campaign_id = ? AND rolled_back = 0 AND ({like}) ORDER BY id DESC LIMIT 8",
                      (self.cid, *[f"%{t}%" for t in terms]))
        flike = " OR ".join("lower(subject || ' ' || fact) LIKE ?" for _ in terms) or "0"
        facts = rows(self.conn, f"SELECT subject, fact, certainty, visibility FROM lore_facts WHERE campaign_id = ? AND ({flike}) ORDER BY id DESC LIMIT 8",
                     (self.cid, *[f"%{t}%" for t in terms]))
        return {"record_sections": sections, "events": events, "facts": facts}

    def t_read_record(self, a: dict) -> dict:
        s = one(self.conn, "SELECT id, document_path, section, content, visibility FROM source_sections WHERE campaign_id = ? AND id = ?", (self.cid, int(a["section_id"])))
        if not s:
            raise ValueError("unknown section_id")
        if s["visibility"] == "gm":
            s["visibility"] = "GM-ONLY — never reveal directly"
        s["content"] = s["content"][:9000]
        return s

    def t_record_ruling(self, a: dict) -> dict:
        r = _Ruling.model_validate(a)
        label = r.label.upper()
        if label not in {"BOOK", "CAMPAIGN", "INTERPRETATION", "ONE-SCENE", "OPEN"}:
            raise ValueError("label must be BOOK, CAMPAIGN, INTERPRETATION, ONE-SCENE or OPEN")
        if r.certainty not in {"canonical", "provisional", "uncertain"}:
            raise ValueError("certainty must be canonical, provisional or uncertain")
        self.conn.execute("INSERT INTO rulings (campaign_id, turn_id, topic, ruling, certainty, label, sources_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
                          (self.cid, self.turn_id, r.topic, r.ruling, r.certainty, label, json.dumps(r.sources), now_iso()))
        self.effects.changes.append(f"Ruling recorded ({label}): {r.topic}")
        return {"recorded": True}

    def t_suggest_image(self, a: dict) -> dict:
        from ..art.service import create_job
        from ..config import Settings
        kind = str(a.get("kind", "")).strip().lower()
        subject = str(a.get("subject", "")).strip()
        made = one(self.conn, "SELECT COUNT(*) AS n FROM art_jobs WHERE campaign_id = ? AND turn_id = ? AND source = 'gm'", (self.cid, self.turn_id))
        if made["n"] >= 1:
            raise ValueError("one picture suggestion per turn")
        if kind in ("character", "item"):
            table = "characters" if kind == "character" else "items"
            row = one(self.conn, f"SELECT id FROM {table} WHERE campaign_id = ? AND lower(name) = lower(?)", (self.cid, subject))
            if not row and kind == "character":
                row = {"id": resolve_character(self.conn, self.cid, subject)["id"]}
            if not row:
                raise ValueError(f"no {kind} named {subject!r}")
            sid, name = row["id"], None
        elif kind == "scene":
            sc = one(self.conn, "SELECT id FROM scenes WHERE campaign_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1", (self.cid,))
            if not sc:
                raise ValueError("no active scene; start one with apply_changes op scene first")
            sid, name = sc["id"], None
        elif kind in ("mob", "location"):
            sid, name = None, subject
        else:
            raise ValueError("kind must be character, mob, item, scene or location")
        if one(self.conn, f"""SELECT id FROM art_jobs WHERE campaign_id = ? AND kind = ? AND status IN ('suggested','queued','running','done')
                               AND {'lower(subject_name) = lower(?)' if kind in ('mob', 'location') else 'subject_id = ?'}""", (self.cid, kind, name if kind in ("mob", "location") else sid)):
            return {"skipped": "that subject already has a picture or a pending suggestion"}
        job = create_job(self.conn, self.settings or Settings(), self.cid, kind, subject_id=sid, subject_name=name or subject,
                         extra=str(a.get("description") or "")[:500], source="gm", status="suggested", reason=a.get("reason"), turn_id=self.turn_id)
        self.effects.changes.append(f"Picture suggested: {job['subject_name']} ({kind}) — approve it in the Art queue")
        return {"suggestion_id": job["id"], "note": job["note"]}

    def t_gm_note(self, a: dict) -> dict:
        subject, content = str(a.get("subject", "")).strip(), str(a.get("content", "")).strip()
        if not subject or not content:
            raise ValueError("subject and content are required")
        sid = self.conn.execute("INSERT INTO gm_secrets (campaign_id, subject, kind, content, source, created_at) VALUES (?,?,?,?,?,?)",
                                (self.cid, subject, str(a.get("kind") or "note"), content, f"turn {self.turn_id}", now_iso())).lastrowid
        return {"secret_id": sid}

    def t_reveal_secret(self, a: dict) -> dict:
        s = one(self.conn, "SELECT * FROM gm_secrets WHERE campaign_id = ? AND id = ?", (self.cid, int(a["secret_id"])))
        if not s:
            raise ValueError("unknown secret_id")
        fact = str(a.get("player_visible_fact", "")).strip()
        if not fact:
            raise ValueError("player_visible_fact is required (only what the player actually learned)")
        self.conn.execute("UPDATE gm_secrets SET revealed = 1 WHERE id = ?", (s["id"],))
        r = apply_change(self.conn, self.cid, self.turn_id, {"op": "fact", "subject": str(a.get("subject") or s["subject"]), "fact": fact,
                                                             "certainty": "canonical", "visibility": "player"})
        self.effects.changes.append(r["summary"])
        return {"revealed": True}

    def t_system_notice(self, a: dict) -> dict:
        text = str(a.get("text", "")).strip()
        if not text:
            raise ValueError("text is required")
        style = str(a.get("style") or "notice")
        if style not in {"notice", "warning", "tooltip"}:
            style = "notice"
        self.effects.notices.append({"text": text[:3000], "style": style})
        return {"queued": True}
