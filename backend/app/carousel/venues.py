"""Where a group stands inside a venue, and where it can go from there. Movement authority is the venue's link graph
(ref.venue_links) as changed by the campaign's live story overlays (venue_overlays): a `block` keeps a link but marks it
impassable, an `add_zone` adds a room joined to one zone. Lifted overlays count for nothing. The reference data is
never changed. Story-added zone IDs are `<venue>:story-<story_id>-<n>`.

Story-only venues (STORY_LAYERS) are never entered by arriving at a place: the GM takes the group in during its story
(rules/story_venue_ops.py), and travel waits until the group leaves by the exit or the story ends."""
from __future__ import annotations

import sqlite3

STORY_LAYERS = ("story_instance", "extradimensional")


def venue_at(conn: sqlite3.Connection, node_id: str | None) -> dict | None:
    """The enterable venue whose member place (aliases resolved) is this travel-graph node's place."""
    if not node_id:
        return None
    row = conn.execute("""SELECT v.* FROM ref.graph_nodes g
                          JOIN ref.venue_places vp ON COALESCE((SELECT target_id FROM ref.place_aliases WHERE alias_id = vp.place_id),
                                                               vp.place_id) = g.location_id
                          JOIN ref.venues v ON v.id = vp.venue_id
                          WHERE g.id = ? AND v.enterable = 1 ORDER BY v.id LIMIT 1""", (node_id,)).fetchone()
    return dict(row) if row else None


def venue(conn: sqlite3.Connection, venue_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM ref.venues WHERE id = ?", (venue_id,)).fetchone()
    return dict(row) if row else None


def zone(conn: sqlite3.Connection, zone_id: str, cid: int | None = None) -> dict | None:
    """A reference zone, or a live story-added one (`story_only` = 1)."""
    row = conn.execute("SELECT id, venue_id, node_id, label, area_type, floor, story_only FROM ref.venue_zones WHERE id = ?",
                       (zone_id,)).fetchone()
    if row:
        return dict(row)
    row = conn.execute("""SELECT a AS id, venue_id, NULL AS node_id, label, 'story' AS area_type, NULL AS floor, 1 AS story_only
                          FROM venue_overlays WHERE kind = 'add_zone' AND a = ? AND lifted = 0
                          AND (? IS NULL OR campaign_id=?)""", (zone_id, cid, cid)).fetchone()
    return dict(row) if row else None


def label(conn: sqlite3.Connection, zone_id: str | None, cid: int | None = None) -> str | None:
    z = zone(conn, zone_id, cid) if zone_id else None
    return z["label"] if z else None


def _blocks(conn: sqlite3.Connection, cid: int) -> dict[frozenset, str]:
    return {frozenset((r["a"], r["b"])): r["reason"] for r in conn.execute(
        "SELECT a, b, reason FROM venue_overlays WHERE campaign_id = ? AND kind = 'block' AND lifted = 0 ORDER BY id", (cid,))}


def departure_block(conn: sqlite3.Connection, cid: int, zone_id: str | None) -> str | None:
    """A story can close an exterior portal without changing its world anchor."""
    return _blocks(conn, cid).get(frozenset((zone_id,))) if zone_id else None


def neighbours(conn: sqlite3.Connection, cid: int, zone_id: str) -> list[dict]:
    """Every zone one link away, blocked ones included and marked with the reason, in zone-ID order."""
    ids = {r[0] for r in conn.execute("SELECT b FROM ref.venue_links WHERE a = ? UNION SELECT a FROM ref.venue_links WHERE b = ?",
                                      (zone_id, zone_id))}
    ids |= {r[0] for r in conn.execute("""SELECT a FROM venue_overlays WHERE campaign_id = ? AND kind = 'add_zone' AND lifted = 0 AND b = ?
                                          UNION SELECT b FROM venue_overlays WHERE campaign_id = ? AND kind = 'add_zone' AND lifted = 0 AND a = ?""",
                                       (cid, zone_id, cid, zone_id))}
    blocks = _blocks(conn, cid)
    for r in conn.execute("SELECT venue_id,a,b,source_lock FROM ref.venue_links WHERE source_lock IS NOT NULL AND source_lock!='' AND (a=? OR b=?)",
                          (zone_id, zone_id)):
        rule = r["source_lock"]
        reason = "There is no usable way through here yet."
        if rule.startswith("entry_story:"):
            _, outside, lock = rule.split(":", 2)
            if zone_id != r["venue_id"] + ":" + outside:
                continue  # Always allow leaving; story overlays may independently block it.
            active = conn.execute("SELECT 1 FROM stories WHERE campaign_id=? AND status='active' AND story_lock=?", (cid, lock)).fetchone()
            if active:
                continue
            reason = "The door is closed."
        blocks.setdefault(frozenset((r["a"], r["b"])), reason)
    return [{"zone_id": z, "label": label(conn, z, cid), "blocked_reason": blocks.get(frozenset((zone_id, z)))} for z in sorted(ids)]


def position(conn: sqlite3.Connection, cid: int) -> tuple[str | None, str | None]:
    row = conn.execute("SELECT venue_id, zone_id FROM campaigns WHERE id = ?", (cid,)).fetchone()
    return (row["venue_id"], row["zone_id"]) if row else (None, None)


def visited(conn: sqlite3.Connection, cid: int, venue_id: str) -> list[str]:
    return [r[0] for r in conn.execute("SELECT zone_id FROM zone_visits WHERE campaign_id = ? AND zone_id LIKE ? ORDER BY first_minutes, zone_id",
                                       (cid, venue_id + ":%"))]


def visible_zones(conn: sqlite3.Connection, cid: int) -> list[str]:
    """Zones of the current venue the group may know: those it has been in, plus those one link from where it stands."""
    venue_id, here = position(conn, cid)
    if not venue_id or not here:
        return []
    out = visited(conn, cid, venue_id)
    for n in neighbours(conn, cid, here):
        if n["zone_id"] not in out:
            out.append(n["zone_id"])
    return out


def zone_omens(conn: sqlite3.Connection, zone_id: str) -> list[str]:
    """Omens the engine places in this zone (ref.venue_omens.placed)."""
    return [r[0] for r in conn.execute("SELECT omen_id FROM ref.venue_omens WHERE zone_id = ? AND placed = 1 ORDER BY omen_id", (zone_id,))]


def in_story_venue(conn: sqlite3.Connection, cid: int) -> dict | None:
    """The story-only venue the group is inside, if any."""
    venue_id, _ = position(conn, cid)
    v = venue(conn, venue_id) if venue_id else None
    return v if v and v["layer"] in STORY_LAYERS else None


def members_at(conn: sqlite3.Connection, venue_id: str, node_id: str | None) -> bool:
    """Whether this travel-graph node's place is one of the venue's member places (aliases resolved)."""
    return bool(node_id) and conn.execute("""SELECT 1 FROM ref.graph_nodes g
                          JOIN ref.venue_places vp ON COALESCE((SELECT target_id FROM ref.place_aliases WHERE alias_id = vp.place_id),
                                                               vp.place_id) = g.location_id
                          WHERE g.id = ? AND vp.venue_id = ?""", (node_id, venue_id)).fetchone() is not None


def enter_story(conn: sqlite3.Connection, cid: int, venue_id: str, group_id: int | None = None) -> dict:
    """Put the campaign (and the PC's group) at a story venue's entry zone. The caller checks the story allows it."""
    return _put(conn, cid, venue(conn, venue_id), group_id)


def enter(conn: sqlite3.Connection, cid: int, node_id: str | None, group_id: int | None = None) -> dict | None:
    """Put the campaign (and the PC's group) at the entry zone of the venue at this node, or clear the venue if there is
    none. Returns {venue_id, zone_id, label} or None."""
    return _put(conn, cid, venue_at(conn, node_id), group_id)


def _put(conn: sqlite3.Connection, cid: int, v: dict | None, group_id: int | None) -> dict | None:
    vid, zid = (v["id"], v["entry_zone"]) if v else (None, None)
    conn.execute("UPDATE campaigns SET venue_id = ?, zone_id = ? WHERE id = ?", (vid, zid, cid))
    if group_id is not None:
        conn.execute("UPDATE party_groups SET venue_id = ?, zone_id = ? WHERE id = ?", (vid, zid, group_id))
    if not v:
        return None
    conn.execute("""INSERT OR IGNORE INTO zone_visits (campaign_id, zone_id, first_minutes)
                    SELECT id, ?, game_time_minutes FROM campaigns WHERE id = ?""", (zid, cid))
    return {"venue_id": vid, "zone_id": zid, "label": label(conn, zid)}
