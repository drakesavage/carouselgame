import json

from app.carousel.refdb import count


def strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from strings(x)


def test_npc_counts(ref_real):
    assert count(ref_real, "npc_templates") == 70
    got = dict(ref_real.execute("SELECT provenance, COUNT(*) FROM npc_templates GROUP BY 1").fetchall())
    assert got == {"canon_expanded": 23, "original_adaptation": 47}


def test_gm_only_split(ref_real):
    n = 0
    for p, g in ref_real.execute("SELECT player_json, gm_json FROM npc_templates"):
        assert "gm_only" not in p
        for secret in strings(json.loads(g)):
            if len(secret) > 12:     # skip short shared words ("alive", names)
                assert secret not in p
                n += 1
    assert n > 70                    # every resident has GM-only material, and it was all separated
