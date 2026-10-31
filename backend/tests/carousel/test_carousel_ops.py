import pytest

from app.carousel.sheet import sheet
from app.db import one
from app.rules.engine import ChangeError
from tests.conftest import apply


def test_initialize_character_once(conn, campaign_id, companion):
    r = apply(conn, campaign_id, {"op": "initialize_character", "character": "Jonah Reyes", "role": "base.film-buff",
                                  "reason": "quoted three slasher rules on the drive"})
    s = sheet(conn, companion)
    assert s["plot_armor"] == {"base": 11, "current": 11, "effective": 11} and s["stats"]["Savvy"] == 5
    c = one(conn, "SELECT role_id, role_reason, ruleset_version FROM characters WHERE id = ?", (companion,))
    assert c == {"role_id": "base.film-buff", "role_reason": "quoted three slasher rules on the drive", "ruleset_version": "SB-0.4.1/role-0.4.2"}
    ev = one(conn, "SELECT payload_json FROM events WHERE type = 'character_stats_initialized' ORDER BY id DESC LIMIT 1")
    assert ev["payload"]["role"] == "base.film-buff" and ev["payload"]["base_pa"] == 11 and "Film Buff" in r["summary"]
    with pytest.raises(ChangeError, match="set once"):
        apply(conn, campaign_id, {"op": "initialize_character", "character": "Jonah Reyes", "role": "base.film-buff", "reason": "again"})


def test_initialize_rejects_non_starting_role(conn, campaign_id):
    for role in ("aspect.final-girl.screamer", "base.nonexistent"):
        with pytest.raises(ChangeError, match="starting Archetype"):
            apply(conn, campaign_id, {"op": "initialize_character", "character": "Jonah Reyes", "role": role, "reason": "x"})


def test_modifiers_change_plot_armor_and_scene_ones_end_with_the_scene(conn, campaign_id, pc):
    apply(conn, campaign_id, {"op": "add_modifier", "character": "Mara Vance", "target": "grit", "op_kind": "add", "value": 2,
                              "source": "adrenaline", "scope": "scene"})
    r = apply(conn, campaign_id, {"op": "add_modifier", "character": "Mara Vance", "target": "plot_armor", "op_kind": "multiply",
                                  "value": 0.5, "source": "Trope Master", "scope": "until_removed"})
    assert sheet(conn, pc)["plot_armor"] == {"base": 11, "current": 13, "effective": 6}
    apply(conn, campaign_id, {"op": "scene", "action": "start", "title": "A"})
    apply(conn, campaign_id, {"op": "scene", "action": "start", "title": "B", "summary": "x"})     # closes A
    assert sheet(conn, pc)["plot_armor"] == {"base": 11, "current": 11, "effective": 5}
    apply(conn, campaign_id, {"op": "remove_modifier", "modifier_id": r["modifier_id"], "reason": "no longer reading tropes"})
    assert sheet(conn, pc)["plot_armor"]["effective"] == 11


def test_modifier_validation(conn, campaign_id):
    bad = [{"target": "hp", "op_kind": "add", "value": 1}, {"target": "grit", "op_kind": "add", "value": 0},
           {"target": "plot_armor", "op_kind": "multiply", "value": 0}]
    for b in bad:
        with pytest.raises(ChangeError):
            apply(conn, campaign_id, {"op": "add_modifier", "character": "Mara Vance", "source": "x", "scope": "scene", **b})


def test_ops_need_reference_data(conn, campaign_id):
    conn.execute("DETACH DATABASE ref")
    with pytest.raises(ChangeError, match="import-data.cmd"):
        apply(conn, campaign_id, {"op": "grant_ticket", "character": "Jonah Reyes", "ticket": "hb.ticket.base.film-buff", "source": "x"})
