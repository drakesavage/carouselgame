import pytest

from app.db import one, rows, transaction
from app.rules.checks import CheckRequest, degree_of, perform_check, resolve_advancement_checkpoint
from app.rules.dice import DiceRoller
from app.rules.engine import ChangeError, apply_change
from conftest import scripted


@pytest.mark.parametrize("nat,total,dc,expected", [
    (20, 5, 30, "Critical Hit"), (1, 40, 10, "Critical Fail"), (15, 30, 20, "Amazing Success"), (10, 20, 20, "Success"),
    (10, 18, 20, "Near Miss"), (10, 17, 20, "Fail"), (10, 11, 20, "Fail"), (10, 10, 20, "Major Fail")])
def test_degrees_of_outcome(nat, total, dc, expected):
    assert degree_of(nat, total, dc) == expected


def _pc(conn, cid):
    return one(conn, "SELECT active_character_id AS id FROM campaigns WHERE id = ?", (cid,))["id"]


def test_skill_check_math_default_dc_and_mark(conn, demo_id):
    roller = DiceRoller(source=scripted([12, 4]))  # d20 12, help d6 4
    req = CheckRequest(character="player", skill="Investigation", stat="int", dc_kind="unopposed", reason="search",
                       help=[{"helper": "Bix", "dice": "1d6"}], modifiers=[{"source": "Lanyard", "value": 1}])
    out = perform_check(conn, conn, roller, demo_id, None, req)
    # demo: Investigation 4, INT mod 4, Floor 3 -> unopposed DC 16; 12 + 4 + 4 + 1 + help 4 = 25
    assert out["dc"] == 16 and out["total"] == 25 and out["degree"] == "Success"
    assert "Bix's Help 4" in out["summary"] and "Lanyard" in out["summary"]
    assert out["skill_marked"] is True
    assert one(conn, "SELECT marked FROM skills WHERE character_id = ? AND name = 'Investigation'", (_pc(conn, demo_id),))["marked"] == 1


def test_untrained_check_rolls_disadvantage_without_rank(conn, demo_id):
    roller = DiceRoller(source=scripted([18, 3]))
    out = perform_check(conn, conn, roller, demo_id, None, CheckRequest(character="player", skill="Juggling", stat="dex", dc=10, reason="x"))
    assert out["natural"] == 3 and out["total"] == 3 + 3  # keep lower, DEX mod 3, no Rank
    assert out["skill_marked"] is False


def test_advantage_cancels_disadvantage(conn, demo_id):
    roller = DiceRoller(source=scripted([7]))
    out = perform_check(conn, conn, roller, demo_id, None, CheckRequest(character="player", skill="Juggling", stat="dex", dc=10,
                                                                        advantage=1, reason="x"))
    assert out["natural"] == 7  # untrained disadvantage cancelled by one advantage -> single d20


def test_stat_check_cannot_receive_help(conn, demo_id):
    with pytest.raises(ChangeError):
        perform_check(conn, conn, DiceRoller(), demo_id, None,
                      CheckRequest(character="player", stat="str", help=[{"helper": "Bix", "dice": "1d6"}], reason="lift"))


def test_engine_validates_and_records_events(conn, demo_id):
    with transaction(conn):
        r = apply_change(conn, demo_id, None, {"op": "hp", "character": "player", "delta": 100})
        assert r["after"] == 30  # clamped to max
        with pytest.raises(ChangeError):
            apply_change(conn, demo_id, None, {"op": "remove_item", "character": "player", "name": "Healing Potion", "quantity": 5})
        apply_change(conn, demo_id, None, {"op": "transfer_item", "from_character": "player", "to_character": "shared",
                                           "name": "Healing Potion", "quantity": 1})
    assert one(conn, "SELECT quantity FROM items WHERE owner_character_id IS NULL AND name = 'Healing Potion'")["quantity"] == 1
    assert one(conn, "SELECT quantity FROM items WHERE owner_character_id IS NOT NULL AND name = 'Healing Potion'")["quantity"] == 1
    types = [e["type"] for e in rows(conn, "SELECT type FROM events WHERE campaign_id = ? ORDER BY id", (demo_id,))]
    assert types == ["hp_changed", "item_transferred"]  # the rejected change left no event


def test_unknown_quantity_cannot_be_spent_until_established(conn, demo_id):
    with transaction(conn):
        with pytest.raises(ChangeError, match="does not establish"):
            apply_change(conn, demo_id, None, {"op": "remove_item", "character": "shared", "name": "Mystery Detergent"})
        apply_change(conn, demo_id, None, {"op": "set_quantity", "character": "shared", "name": "Mystery Detergent", "quantity": 3, "reason": "counted"})
        apply_change(conn, demo_id, None, {"op": "remove_item", "character": "shared", "name": "Mystery Detergent"})
    assert one(conn, "SELECT quantity FROM items WHERE name = 'Mystery Detergent'")["quantity"] == 2


def test_time_advances_and_statuses_expire(conn, demo_id):
    with transaction(conn):
        apply_change(conn, demo_id, None, {"op": "add_status", "character": "player", "name": "Sticky", "duration_minutes": 30})
        apply_change(conn, demo_id, None, {"op": "advance_time", "minutes": 45, "reason": "nap"})
    assert one(conn, "SELECT COUNT(*) AS n FROM status_effects WHERE name = 'Sticky'")["n"] == 0
    assert one(conn, "SELECT game_time_minutes FROM campaigns WHERE id = ?", (demo_id,))["game_time_minutes"] == 645


def test_allocate_stats_requires_bank_and_keeps_modifier_unless_given(conn, demo_id):
    with transaction(conn):
        with pytest.raises(ChangeError):
            apply_change(conn, demo_id, None, {"op": "allocate_stats", "character": "player", "stat": "constitution", "points": 3})


def test_advancement_checkpoint(conn, demo_id):
    pc = _pc(conn, demo_id)
    conn.execute("UPDATE skills SET marked = 1, rank = 2 WHERE character_id = ? AND name = 'Negotiation'", (pc,))
    conn.execute("UPDATE skills SET marked = 1, rank = 5 WHERE character_id = ? AND name = 'Engineering'", (pc,))
    conn.execute("UPDATE clocks SET current = 13 WHERE campaign_id = ?", (demo_id,))
    with transaction(conn):
        out = resolve_advancement_checkpoint(conn, conn, DiceRoller(source=scripted([2])), demo_id, None, "saferoom")
    assert out["cycles_remaining"] == 1  # overflow carries
    neg = one(conn, "SELECT rank, marked FROM skills WHERE character_id = ? AND name = 'Negotiation'", (pc,))
    eng = one(conn, "SELECT rank, marked FROM skills WHERE character_id = ? AND name = 'Engineering'", (pc,))
    assert (neg["rank"], neg["marked"]) == (3, 0)   # d20 2 >= Rank 2
    assert (eng["rank"], eng["marked"]) == (5, 1)   # Rank 5+ stays banked for floor end
    assert one(conn, "SELECT level FROM characters WHERE id = ?", (pc,))["level"] == 7
    assert one(conn, "SELECT derived_json FROM characters WHERE id = ?", (pc,))["derived"]["unallocated_stat_points"] == 3


def test_checkpoint_not_due_is_rejected(conn, demo_id):
    with pytest.raises(ChangeError):
        resolve_advancement_checkpoint(conn, conn, DiceRoller(), demo_id, None, "early")


def test_party_check_tally_half_succeeds_with_conversions():
    from app.rules.party import tally
    r = lambda name, degree, margin: {"character": name, "id": name, "degree": degree, "margin": margin,
                                      "success": degree in ("Critical Hit", "Amazing Success", "Success")}
    # BOOK p. 91 example: 2 of 5 met the Difficulty -> Failure
    assert tally([r("a", "Success", 1), r("b", "Success", 0), r("c", "Fail", -3), r("d", "Near Miss", -1), r("e", "Fail", -5)])[2] is False
    # half or more: 2 of 4 succeeds
    assert tally([r("a", "Success", 1), r("b", "Success", 0), r("c", "Fail", -3), r("d", "Fail", -4)]) == (2, 2, True)
    # an Amazing Success lifts the failure that missed by least
    res = [r("a", "Amazing Success", 11), r("b", "Fail", -6), r("c", "Near Miss", -1), r("d", "Major Fail", -12)]
    s, need, ok = tally(res)
    assert res[2]["final"] and not res[1]["final"]
    # ...and the Major Fail drags down another's Success (the weakest one that was not converted, never its own roll)
    assert res[0]["converted"].startswith("Failure") and (s, ok) == (1, False)
    # a Critical Fail with no Success to drag down changes nothing; a Critical Hit with no failure to lift changes nothing
    assert tally([r("a", "Critical Fail", -15), r("b", "Fail", -3)]) == (0, 1, False)
    assert tally([r("a", "Critical Hit", 9), r("b", "Success", 2)]) == (2, 1, True)
