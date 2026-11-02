"""The player's view of Camp Dyer's residents: who is around the group, and the Lodge roster. Only met residents are ever
listed (carousel/residents.py); an unmet one is the GM's to narrate. What a met resident shows is their name, camp role,
last seen, whether they're with the group, and what their introduction told the group (the lore facts introduce_resident
wrote). Never the companion sheet, hooks, whereabouts notes or GM-only relationships.

POST /residents/{id}/talk starts a GM turn of kind talk_resident ("I go over to talk to …"), only while they are around."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .carousel import residents
from .carousel.refdata import has_reference
from .db import one, rows
from .gm.orchestrator import BusyError


class TalkIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=80)


def _role(conn, character_id: int) -> str | None:
    r = one(conn, """SELECT t.camp_role FROM npc_profiles p JOIN ref.npc_templates t ON t.id = p.ref_id WHERE p.character_id = ?""",
            (character_id,)) if has_reference(conn) else None
    return r["camp_role"] if r else None


def player_around(conn, cid: int) -> list[dict]:
    if not has_reference(conn):
        return []
    return [{"id": a["id"], "name": a["name"], "role": _role(conn, a["id"])} for a in residents.around(conn, cid) if a["met"]]


def player_roster(conn, cid: int) -> list[dict]:
    out = []
    for r in rows(conn, """SELECT c.id, c.name, c.in_party, p.last_seen FROM characters c JOIN npc_profiles p ON p.character_id = c.id
                           WHERE c.campaign_id = ? AND p.ref_id IS NOT NULL AND p.met = 1 ORDER BY c.name""", (cid,)):
        known = [f["fact"] for f in rows(conn, """SELECT fact FROM lore_facts WHERE campaign_id = ? AND subject = ? AND source = 'introduction'
                                                 AND visibility = 'player' ORDER BY id""", (cid, r["name"]))]
        out.append({"id": r["id"], "name": r["name"], "role": _role(conn, r["id"]), "last_seen": r["last_seen"],
                    "in_party": bool(r["in_party"]), "known": known})
    return out


def register(app: FastAPI, state) -> None:
    def campaign(c, cid: int) -> None:
        if not one(c, "SELECT id FROM campaigns WHERE id = ?", (cid,)):
            raise HTTPException(404, "campaign not found")

    @app.get("/api/campaigns/{cid}/around")
    def get_around(cid: int):
        c = state.conn()
        try:
            campaign(c, cid)
            return player_around(c, cid)
        finally:
            c.close()

    @app.get("/api/campaigns/{cid}/residents")
    def get_residents(cid: int):
        c = state.conn()
        try:
            campaign(c, cid)
            return player_roster(c, cid)
        finally:
            c.close()

    @app.post("/api/campaigns/{cid}/residents/{char_id}/talk")
    def talk(cid: int, char_id: int, body: TalkIn):
        c = state.conn()
        try:
            campaign(c, cid)
            r = one(c, """SELECT c.name, p.met FROM characters c JOIN npc_profiles p ON p.character_id = c.id
                          WHERE c.campaign_id = ? AND c.id = ? AND p.ref_id IS NOT NULL""", (cid, char_id))
            if not r or not r["met"]:
                raise HTTPException(404 if not r else 409, "no such resident" if not r else "you haven't met them")
            if char_id not in {a["id"] for a in player_around(c, cid)}:
                raise HTTPException(409, f"{r['name']} isn't here")
        finally:
            c.close()
        try:
            turn = state.turns.submit(cid, f"I go over to talk to {r['name']}", body.idempotency_key, kind="talk_resident")
        except BusyError:
            raise HTTPException(409, "The GM is still resolving a turn.")
        return {"turn": turn}
