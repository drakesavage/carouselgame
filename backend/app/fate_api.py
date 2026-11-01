"""The player's view of fate (design §8): Deathwatch while the player's character can't act, the wiped flag that offers a
new group, and the missing-poster board outside the Diner. Death causes, rescue eligibility and a Rescue's posters are
GM-only and never leave the server from here."""
from __future__ import annotations

from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from .carousel import fate
from .carousel.stories import active_story
from .db import now_iso, one, rows, transaction

AXES = ("physical_condition", "wallpaper_status", "participation", "persistent_fate")


class PaceIn(BaseModel):
    pace: Literal["full", "condensed"]


def player_fate(conn, cid: int) -> dict:
    s = active_story(conn, cid)
    watch = fate.deathwatch(conn, cid)
    pc = one(conn, "SELECT c.persistent_fate FROM campaigns k JOIN characters c ON c.id = k.active_character_id WHERE k.id = ?", (cid,))
    cast = rows(conn, f"""SELECT c.id, c.name, {', '.join('c.' + a for a in AXES)} FROM story_cast x JOIN characters c ON c.id = x.character_id
                          WHERE x.story_id = ? ORDER BY c.party_order, c.id""", (s["id"],)) if s else []
    return {"deathwatch": {"pace": watch["pace"]} if watch else None,
            "wiped": bool(pc) and pc["persistent_fate"] in ("missing_postered", "permanently_lost"), "cast": cast}


def player_posters(conn, cid: int) -> list[dict]:
    """The board, once the group has been to the Diner (or stands there): every poster, taken or found ones marked."""
    camp = one(conn, "SELECT node_id FROM campaigns WHERE id = ?", (cid,))
    visited = one(conn, "SELECT 1 AS x FROM known_places WHERE campaign_id = ? AND place_id = ? AND how = 'visited'", (cid, fate.DINER))
    if not visited and (camp or {}).get("node_id") != fate.DINER:
        return []
    return [{"id": p["id"], "name": p["name"], "plot_armor": p["plot_armor"], "archetype": p["archetype"], "last_seen": p["last_seen"],
             "posted_minutes": p["posted_minutes"], "held_by": p["held_by"], "found": p["rescued_minutes"] is not None}
            for p in rows(conn, """SELECT p.*, h.name AS held_by FROM missing_posters p LEFT JOIN characters h ON h.id = p.held_by_character_id
                                   WHERE p.campaign_id = ? ORDER BY p.id""", (cid,))]


def register(app: FastAPI, state, write_lock) -> None:
    def with_campaign(cid: int, fn):
        c = state.conn()
        try:
            if not one(c, "SELECT id FROM campaigns WHERE id = ?", (cid,)):
                raise HTTPException(404, "campaign not found")
            return fn(c)
        finally:
            c.close()

    @app.get("/api/campaigns/{cid}/fate")
    def get_fate(cid: int):
        return with_campaign(cid, lambda c: player_fate(c, cid))

    @app.get("/api/campaigns/{cid}/posters")
    def get_posters(cid: int):
        return with_campaign(cid, lambda c: player_posters(c, cid))

    @app.post("/api/campaigns/{cid}/deathwatch/pace")
    def set_pace(cid: int, body: PaceIn, _=Depends(write_lock)):
        def go(c):
            watch = fate.deathwatch(c, cid)
            if not watch:
                raise HTTPException(409, "Deathwatch is on only while your character can't act")
            label = "Watch it all" if body.pace == "full" else "Condensed"
            with transaction(c):
                c.execute("UPDATE stories SET deathwatch_pace = ? WHERE id = ?", (body.pace, watch["story_id"]))
                c.execute("""INSERT INTO events (campaign_id, type, summary, payload_json, visibility, game_time_minutes, created_at)
                             VALUES (?, 'deathwatch_pace', ?, ?, 'player', (SELECT game_time_minutes FROM campaigns WHERE id = ?), ?)""",
                          (cid, f"Deathwatch pace: {label}", f'{{"pace": "{body.pace}"}}', cid, now_iso()))
            return {"pace": body.pace}
        return with_campaign(cid, go)
