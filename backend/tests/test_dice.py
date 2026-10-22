import collections

import pytest

from app.db import rows
from app.rules.dice import RNG_NAME, DiceError, DiceRoller, record_roll
from conftest import scripted


def test_parses_and_sums_with_modifiers():
    r = DiceRoller(source=scripted([3, 5])).roll("2d6+3")
    assert [d.value for d in r.dice] == [3, 5]
    assert r.modifier == 3 and r.total == 11


def test_keep_highest_and_lowest():
    kh = DiceRoller(source=scripted([2, 17])).roll("2d20kh1")
    assert kh.total == 17 and [d.kept for d in kh.dice] == [False, True]
    kl = DiceRoller(source=scripted([2, 17])).roll("2d20kl1")
    assert kl.total == 2
    assert DiceRoller(source=scripted([6, 1, 4, 3])).roll("4d6kh3").total == 13


def test_negative_terms_and_mixed_dice():
    r = DiceRoller(source=scripted([4, 2])).roll("1d8-1d4-1")
    assert r.total == 4 - 2 - 1


def test_target_sets_outcome():
    assert DiceRoller(source=scripted([10])).roll("1d20+5", target=15).outcome == "success"
    assert DiceRoller(source=scripted([9])).roll("1d20+5", target=15).outcome == "failure"


@pytest.mark.parametrize("bad", ["", "d", "2d1", "3", "1d20++2", "1d20 2", "0d6", "101d6", "1d1001", "4d6kh5", "abc"])
def test_rejects_invalid_expressions(bad):
    with pytest.raises(DiceError):
        DiceRoller().roll(bad)


def test_out_of_range_source_is_rejected():
    with pytest.raises(DiceError):
        DiceRoller(source=lambda sides: sides + 1).roll("1d6")


def test_csprng_covers_all_faces_and_stays_in_range():
    roller = DiceRoller()
    counts = collections.Counter(roller.roll("1d6").total for _ in range(3000))
    assert set(counts) == {1, 2, 3, 4, 5, 6}
    assert all(350 < n < 650 for n in counts.values())  # loose uniformity sanity check


def test_rolls_are_recorded_with_rng_source(conn, demo_id):
    r = DiceRoller().roll("2d8+1", target=9, reason="test")
    record_roll(conn, demo_id, r, turn_id=None, character_id=None, hidden=True)
    stored = rows(conn, "SELECT * FROM dice_rolls WHERE id = ?", (r.id,))[0]
    assert stored["total"] == r.total and stored["rng"] == RNG_NAME and stored["hidden"] == 1
    assert [d["value"] for d in stored["dice"]] == [d.value for d in r.dice]
