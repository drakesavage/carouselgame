import re

from app.carousel.refdb import count

TICKET_ID = re.compile(r"^hb\.ticket\.(base|aspect|advanced|starter\.shared)\.[a-z0-9-]+(?:\.[a-z0-9-]+)*$")


def test_role_counts(ref_real):
    got = dict(ref_real.execute("SELECT kind, COUNT(*) FROM roles GROUP BY 1").fetchall())
    assert got == {"base": 13, "aspect": 39, "advanced": 19}
    # every aspect hangs off one of the 13 starting archetypes
    assert count(ref_real, "roles", "kind = 'aspect' AND parent NOT IN (SELECT id FROM roles WHERE kind = 'base')") == 0


def test_tickets(ref_real):
    assert count(ref_real, "tickets") == 81
    assert count(ref_real, "tickets", "id LIKE 'hb.ticket.starter.shared.%'") == 10
    assert all(TICKET_ID.match(i) for (i,) in ref_real.execute("SELECT id FROM tickets"))
    assert count(ref_real, "tickets", "text IS NULL OR trim(text) = ''") == 0
    text = ref_real.execute("SELECT text FROM tickets WHERE id = 'hb.ticket.base.athlete'").fetchone()[0]
    assert text.startswith("Once per scene")


def test_start_profiles(ref_real):
    rows = ref_real.execute("SELECT role_id, mettle + moxie + hustle + savvy + grit, base_pa, "
                            "max(mettle, moxie, hustle, savvy, grit) FROM start_profiles").fetchall()
    assert len(rows) == 13
    assert all(total == 11 and pa == 11 and cap <= 5 for _, total, pa, cap in rows)
    assert ref_real.execute("SELECT savvy FROM start_profiles WHERE role_id = 'base.athlete'").fetchone()[0] == 0
    assert count(ref_real, "start_profiles", "role_id NOT IN (SELECT id FROM roles WHERE kind = 'base')") == 0


def test_trope_bible(ref_real):
    assert count(ref_real, "tropes") == 627
