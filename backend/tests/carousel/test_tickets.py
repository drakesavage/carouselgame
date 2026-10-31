import pytest

from app.db import one, rows, transaction
from app.rules.engine import ChangeError, apply_change
from tests.conftest import apply

FG = "hb.ticket.base.final-girl"
QT = "hb.ticket.starter.shared.quick-thinker"


def ticket(conn, char_id, ref):
    return one(conn, "SELECT * FROM character_tickets WHERE character_id = ? AND ticket_ref = ?", (char_id, ref))


def test_grant_copies_uses_and_recharge_and_refuses_duplicates(conn, campaign_id, companion):
    apply(conn, campaign_id, {"op": "grant_ticket", "character": "Jonah Reyes", "ticket": QT, "source": "contextual_shared_starter"})
    t = ticket(conn, companion, QT)
    assert (t["uses_left"], t["uses_max"], t["recharge"], t["equipped"], t["hidden"]) == (1, 1, "scene", 0, 0)
    with pytest.raises(ChangeError, match="already owns"):
        apply(conn, campaign_id, {"op": "grant_ticket", "character": "Jonah Reyes", "ticket": QT, "source": "again"})
    with pytest.raises(ChangeError, match="no such ticket"):
        apply(conn, campaign_id, {"op": "grant_ticket", "character": "Jonah Reyes", "ticket": "hb.ticket.nope", "source": "x"})


def test_use_trope_spends_and_story_recharge_survives_scene_close(conn, campaign_id, pc):
    r = apply(conn, campaign_id, {"op": "use_trope", "character": "Mara Vance", "ticket": FG, "effect": "walks out of the fire"})
    assert r["uses_left"] == 0
    ev = one(conn, "SELECT summary, visibility FROM events WHERE type = 'trope_used' ORDER BY id DESC LIMIT 1")
    assert "walks out of the fire" in ev["summary"] and ev["visibility"] == "player"
    apply(conn, campaign_id, {"op": "scene", "action": "start", "title": "A"})
    apply(conn, campaign_id, {"op": "scene", "action": "start", "title": "B", "summary": "x"})
    assert ticket(conn, pc, FG)["uses_left"] == 0                          # "Once per story": a scene close doesn't refill it
    from app.rules.carousel_ops import recharge
    from app.rules.engine import Ctx
    with transaction(conn):
        assert recharge(Ctx(conn, campaign_id, None), "story") == 1
    assert ticket(conn, pc, FG)["uses_left"] == 1


def test_scene_recharge(conn, campaign_id, companion):
    apply(conn, campaign_id, {"op": "grant_ticket", "character": "Jonah Reyes", "ticket": QT, "source": "x"})
    apply(conn, campaign_id, {"op": "equip_ticket", "character": "Jonah Reyes", "ticket": QT})
    apply(conn, campaign_id, {"op": "use_trope", "character": "Jonah Reyes", "ticket": "[HB] Think on Your Feet", "effect": "a mop handle"})
    with pytest.raises(ChangeError, match="no uses left"):
        apply(conn, campaign_id, {"op": "use_trope", "character": "Jonah Reyes", "ticket": QT, "effect": "again"})
    apply(conn, campaign_id, {"op": "scene", "action": "start", "title": "A"})
    apply(conn, campaign_id, {"op": "scene", "action": "start", "title": "B", "summary": "x"})
    assert ticket(conn, companion, QT)["uses_left"] == 1


def test_use_trope_refusals(conn, campaign_id, companion):
    for t in ("trope:1", QT):                                               # Adeline's, and one nobody gave Mara
        with pytest.raises(ChangeError, match="does not own"):
            apply(conn, campaign_id, {"op": "use_trope", "character": "Mara Vance", "ticket": t, "effect": "x"})
    apply(conn, campaign_id, {"op": "grant_ticket", "character": "Jonah Reyes", "ticket": QT, "source": "x"})
    with pytest.raises(ChangeError, match="not equipped"):
        apply(conn, campaign_id, {"op": "use_trope", "character": "Jonah Reyes", "ticket": QT, "effect": "x"})
    # a failing use inside one apply_changes batch rolls back the whole batch
    from app.gm.tools import ToolRunner
    with transaction(conn):
        out = ToolRunner(conn, None, campaign_id, 1).run("apply_changes", {"changes": [
            {"op": "use_trope", "character": "Mara Vance", "ticket": FG, "effect": "fine"},
            {"op": "use_trope", "character": "Jonah Reyes", "ticket": QT, "effect": "not equipped"}]})
    assert not out["ok"] and ticket(conn, conn.execute("SELECT id FROM characters WHERE slug = 'mara'").fetchone()[0], FG)["uses_left"] == 1


def test_equip_limit(conn, campaign_id, companion):
    for i in range(5):
        conn.execute("INSERT INTO character_tickets (campaign_id, character_id, ticket_ref) VALUES (?,?,?)", (campaign_id, companion, f"trope:x{i}"))
    for i in range(4):
        conn.execute("UPDATE character_tickets SET equipped = 1 WHERE ticket_ref = ?", (f"trope:x{i}",))
    apply(conn, campaign_id, {"op": "grant_ticket", "character": "Jonah Reyes", "ticket": QT, "source": "x"})
    with pytest.raises(ChangeError, match="equip limit 4"):
        apply(conn, campaign_id, {"op": "equip_ticket", "character": "Jonah Reyes", "ticket": QT})
    apply(conn, campaign_id, {"op": "unequip_ticket", "character": "Jonah Reyes", "ticket": "trope:x0"})
    apply(conn, campaign_id, {"op": "equip_ticket", "character": "Jonah Reyes", "ticket": QT})
    assert ticket(conn, companion, QT)["equipped"] == 1


def test_hidden_ticket_events_are_gm_only_until_revealed(conn, campaign_id, npc):
    apply(conn, campaign_id, {"op": "use_trope", "character": "Adeline Winter", "ticket": "trope:1", "effect": "knew where they hid"})
    ev = one(conn, "SELECT visibility FROM events WHERE type = 'trope_used' ORDER BY id DESC LIMIT 1")
    assert ev["visibility"] == "gm"
    apply(conn, campaign_id, {"op": "reveal_ticket", "character": "Adeline Winter", "ticket": "trope:1", "how": "Trope Master"})
    assert ticket(conn, npc, "trope:1")["hidden"] == 0
    assert rows(conn, "SELECT summary FROM events WHERE type = 'ticket_revealed' AND visibility = 'player'")


def test_record_ruling_can_name_a_ticket(conn, campaign_id):
    from app.gm.tools import ToolRunner
    from app.memory.rulings import rulings
    with transaction(conn):
        ok = ToolRunner(conn, None, campaign_id, 1).run("record_ruling", {"topic": "certain death", "ruling": "Drowning counts.",
                                                                          "label": "INTERPRETATION", "ticket": FG})
        bad = ToolRunner(conn, None, campaign_id, 1).run("record_ruling", {"topic": "x", "ruling": "y", "ticket": "hb.ticket.nope"})
    assert ok["ok"] and not bad["ok"] and "no such ticket" in bad["result"]["error"]
    assert [r["ticket_ref"] for r in rulings(conn, campaign_id)] == [FG]
