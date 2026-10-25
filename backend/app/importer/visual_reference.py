"""Import a "visual reference" document (one `## Name` section per character) into the characters' `visual` blocks.

Expected shape per character (the format the player's reference document uses):

    ## Terri Campion
    **Black American woman | 37 | Bodycam Paladin**
    <physical description paragraph>
    <outfit paragraph>
    **Signature:** ...
    **Expression:** ...

The first header field is the demographic (or species), the second the age (or size for animals), the third the class.
Paragraphs before Signature are physique first, then outfit. Everything goes into sheet.visual under `reference`, which
the portrait prompts and the GM briefing read first; the ledger-derived fields stay as they were. Traits the reference
settles are removed from `unestablished`.
"""
from __future__ import annotations

import json
import re
import sqlite3

from ..db import now_iso, one, rows

ANIMAL = re.compile(r"\b(terrier|retriever|dog|cat|alligator|crocodile|bird|rat|horse|pig|goat|ferret|rabbit|lizard|snake|monkey)\b", re.I)


def parse(markdown: str) -> list[dict]:
    out = []
    for block in re.split(r"^##\s+", markdown, flags=re.M)[1:]:
        lines = block.strip().splitlines()
        name = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        entry: dict = {"name": name}
        m = re.match(r"\*\*(.+?)\*\*", body)
        if m:
            parts = [p.strip() for p in m.group(1).split("|")]
            entry["demographic"] = parts[0] if parts else ""
            second = parts[1] if len(parts) > 1 else ""
            if re.search(r"\b(pounds?|lbs?|feet|foot|adult|kg)\b", second, re.I) or ANIMAL.search(entry["demographic"]):
                entry["size"] = second
            else:
                entry["age"] = second
            if len(parts) > 2:
                entry["class_title"] = parts[2]
            body = body[m.end():].strip()
        for label in ("Signature", "Expression"):
            mm = re.search(rf"\*\*{label}:\*\*\s*(.+?)(?=\n\s*\*\*\w+:\*\*|\Z)", body, re.S)
            if mm:
                entry[label.lower()] = " ".join(mm.group(1).split())
                body = body[:mm.start()] + body[mm.end():]
        paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", body) if p.strip()]
        if paras:
            entry["physique"] = paras[0]
        if len(paras) > 1:
            entry["outfit"] = " ".join(paras[1:])
        out.append(entry)
    return out


SETTLED = {"age": ("age",), "physique": ("face", "facial", "hair", "ethnicity", "height", "build", "skin", "eye"),
           "outfit": ("uniform", "clothing", "outfit", "pieces")}


def _match(chars: list[dict], name: str) -> dict | None:
    low = name.lower()
    for c in chars:
        if c["name"].lower() == low:
            return c
    first = [c for c in chars if c["name"].split()[0].lower() == low.split()[0]]
    return first[0] if len(first) == 1 else None


def apply(conn: sqlite3.Connection, cid: int, markdown: str, source: str) -> dict:
    """Write parsed entries into sheet.visual.reference. Call inside a transaction (the caller takes a save point)."""
    chars = rows(conn, "SELECT id, name, sheet_json FROM characters WHERE campaign_id = ?", (cid,))
    done, unmatched = [], []
    for e in parse(markdown):
        c = _match(chars, e["name"])
        if not c:
            unmatched.append(e["name"])
            continue
        sheet = c.get("sheet") or {}
        vis = dict(sheet.get("visual") or {})
        vis["reference"] = {k: v for k, v in e.items() if k != "name"} | {"source": source, "imported_at": now_iso()}
        settled = [w for k, words in SETTLED.items() if e.get(k) for w in words]
        if vis.get("unestablished"):
            vis["unestablished"] = [u for u in vis["unestablished"] if not any(w in str(u).lower() for w in settled)]
        sheet["visual"] = vis
        conn.execute("UPDATE characters SET sheet_json = ? WHERE id = ?", (json.dumps(sheet, ensure_ascii=False), c["id"]))
        done.append(c["name"])
    if done:
        conn.execute("INSERT INTO events (campaign_id, type, summary, visibility, created_at) VALUES (?,?,?,?,?)",
                     (cid, "visual_reference", f"Visual reference imported for {len(done)} characters ({source})", "player", now_iso()))
    return {"updated": done, "unmatched": unmatched}


def reference_line(sheet: dict | None) -> str:
    """One line for the GM briefing: what this character looks like."""
    ref = ((sheet or {}).get("visual") or {}).get("reference") or {}
    if not ref:
        return ""
    head = ", ".join(x for x in (ref.get("demographic"), ref.get("age") or ref.get("size")) if x)
    first = (ref.get("physique") or "").split(". ")[0]
    outfit = (ref.get("outfit") or "").split(". ")[0]
    return "looks: " + "; ".join(x for x in (head, first, outfit) if x)[:420]
