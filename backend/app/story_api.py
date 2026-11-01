"""The player's view of Carousel stories: the running story's title, premise, Plot Cycle position, camera, cast and the public
summaries of screenplay revisions; the history of concluded stories; spending a pending stat point.

Nothing from story_packets (sealed truth, screenplay, ending predicates), pressure, screenplay version or revision details
ever reaches these responses: each one is built from an explicit list of fields."""
from __future__ import annotations

from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from .carousel.refdata import has_reference
from .carousel.stories import PHASES, active_story
from .db import one, rows, transaction
from .rules.engine import ChangeError, apply_change


class StatPointIn(BaseModel):
    stat: Literal["mettle", "moxie", "hustle", "savvy", "grit"]


def _cast(conn, sid: int) -> list[dict]:
    return rows(conn, """SELECT c.id, c.name, x.casting, x.screen_moments FROM story_cast x JOIN characters c ON c.id = x.character_id
                         WHERE x.story_id = ? ORDER BY CASE x.casting WHEN 'lead' THEN 0 ELSE 1 END, c.party_order, c.id""", (sid,))


def player_story(conn, cid: int) -> dict:
    s = active_story(conn, cid)
    if not s:
        return {"active": None}
    b = one(conn, "SELECT dossier_json FROM ref.omen_beats WHERE omen_id = ?", (s["omen_id"],)) if has_reference(conn) else None
    return {"active": {"id": s["id"], "title": s["title"], "premise": ((b or {}).get("dossier") or {}).get("player_visible_premise"),
                       "phase": s["phase"], "phases": list(PHASES), "camera": s["camera"], "cast": _cast(conn, s["id"]),
                       "revisions": [r["public_summary"] for r in rows(conn, "SELECT public_summary FROM story_revisions WHERE story_id = ? ORDER BY version",
                                                                         (s["id"],))]}}


def story_history(conn, cid: int) -> list[dict]:
    return [{"id": s["id"], "title": s["title"], "ending": s["ending"], "started_minutes": s["started_minutes"],
             "ended_minutes": s["ended_minutes"], "novelty": s["novelty"], "cast": [c["name"] for c in _cast(conn, s["id"])]}
            for s in rows(conn, "SELECT * FROM stories WHERE campaign_id = ? AND status = 'concluded' ORDER BY id DESC", (cid,))]


def register(app: FastAPI, state, write_lock) -> None:
    def with_conn(fn):
        c = state.conn()
        try:
            return fn(c)
        finally:
            c.close()

    def campaign(c, cid: int) -> None:
        if not one(c, "SELECT id FROM campaigns WHERE id = ?", (cid,)):
            raise HTTPException(404, "campaign not found")

    @app.get("/api/campaigns/{cid}/story")
    def get_story(cid: int):
        return with_conn(lambda c: (campaign(c, cid), player_story(c, cid))[1])

    @app.get("/api/campaigns/{cid}/stories")
    def get_stories(cid: int):
        return with_conn(lambda c: (campaign(c, cid), story_history(c, cid))[1])

    @app.post("/api/campaigns/{cid}/characters/{char_id}/stat-point")
    def spend_stat_point(cid: int, char_id: int, body: StatPointIn, _=Depends(write_lock)):
        def go(c):
            campaign(c, cid)
            if not one(c, "SELECT id FROM characters WHERE id = ? AND campaign_id = ?", (char_id, cid)):
                raise HTTPException(404, "character not found")
            try:
                with transaction(c):
                    return apply_change(c, cid, None, {"op": "spend_stat_point", "character": str(char_id), "stat": body.stat})
            except ChangeError as exc:
                raise HTTPException(422, str(exc))
        return with_conn(go)
