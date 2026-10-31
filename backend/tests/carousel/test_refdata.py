import pytest

from app.carousel.refdata import attach_reference, recharge_of, require_reference, ticket_definition
from app.db import connect, init_db
from app.rules.engine import ChangeError


def test_attach_and_lookup(conn):
    d = ticket_definition(conn, "hb.ticket.base.final-girl")
    assert d["name"] == "[HB] Last One Standing" and d["text"].startswith("Once per story") and d["uses_max"] == 1
    assert d["recharge"] == "story" and d["source"] == "homebrew"
    starter = ticket_definition(conn, "hb.ticket.starter.shared.quick-thinker")
    assert starter["recharge"] == "scene" and starter["uses_max"] == 1
    trope = ticket_definition(conn, "trope:1")
    assert trope["source"] == "trope_bible" and trope["uses_max"] is None and "Doors open" in trope["text"]
    assert ticket_definition(conn, "hb.ticket.nope") is None
    assert ticket_definition(conn, "trope:404") is None


def test_recharge_of():
    assert recharge_of({"recharge": "storyline"}) == "story"
    assert recharge_of({"recharge": "scene"}) == "scene"
    assert recharge_of({"recharge": None, "rule": "Once per scene, do X"}) == "scene"
    assert recharge_of({"recharge": None, "rule": "Once per storyline, do X"}) == "story"
    assert recharge_of({"recharge": None, "rule": "once per story, do X"}) == "story"
    assert recharge_of({"recharge": None, "rule": "When cornered, do X"}) is None


def test_ops_without_reference_data_refuse_cleanly(tmp_path):
    c = connect(tmp_path / "c.db")
    init_db(c)
    assert attach_reference(c, tmp_path / "missing.db") is False
    with pytest.raises(ChangeError, match="import-data.cmd"):
        require_reference(c)
    with pytest.raises(ChangeError, match="import-data.cmd"):
        ticket_definition(c, "hb.ticket.base.final-girl")
    c.close()
