"""Bind sequels to recorded campaign outcomes without inventing a predecessor."""
import hashlib

from ..db import one
from .stories import ENDINGS


def predecessor_context(conn, campaign_id: int, dossier: dict) -> dict | None:
    from ..rules.engine import ChangeError

    policy = dossier.get("continuity") or {}
    predecessor = policy.get("predecessor_omen_id")
    if not predecessor:
        return None
    if policy.get("predecessor_policy") != "require_actual_concluded_run":
        raise ChangeError("this story's continuity policy needs review")
    prior = one(conn, """SELECT s.*, p.sealed_json AS sealed_text, p.sealed_sha256 FROM stories s
        LEFT JOIN story_packets p ON p.story_id=s.id
        WHERE s.campaign_id=? AND s.omen_id=? AND s.status='concluded'
        ORDER BY s.ended_minutes DESC,s.id DESC LIMIT 1""", (campaign_id, predecessor))
    if not prior:
        raise ChangeError("this sequel requires a recorded conclusion of its preceding story")
    if prior["ending"] not in ENDINGS or prior["ended_minutes"] is None or not prior["sealed_text"]:
        raise ChangeError("the preceding story's outcome record is incomplete; continuity needs review")
    if hashlib.sha256(prior["sealed_text"].encode("utf-8")).hexdigest() != prior["sealed_sha256"]:
        raise ChangeError("the preceding story's sealed record failed verification")
    return {"predecessor_story_id": prior["id"], "predecessor_omen_id": predecessor,
            "story_lock": prior["story_lock"], "ending": prior["ending"],
            "ending_reason": prior["ending_reason"], "ended_minutes": prior["ended_minutes"],
            "sealed_sha256": prior["sealed_sha256"],
            "state_policy": "Keep current campaign actors, inventory, knowledge and damage; do not reset or invent prior success."}
