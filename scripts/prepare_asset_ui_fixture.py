"""Create a clearly labeled UI-only scene in the isolated candidate database."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.carousel import venues
from app.carousel.refdata import open_campaign
from app.campaigns import create
from app.config import Settings
from app.db import init_db, transaction

out = Path("E:/Carousel/Asset_Reconciliation")
s = Settings(data_dir=out / "runtime_candidate", source_dir=out.parent, gm_provider="mock")
assert s.data_dir.resolve() == (out / "runtime_candidate").resolve()
c = open_campaign(s)
init_db(c)
name = "Isolated exit barrier UI fixture"
old = c.execute("SELECT id FROM campaigns WHERE name=?", (name,)).fetchone()
cid = old[0] if old else create(c, name=name, character="UI Inspector", location="Allcraft Lofts", opening=False)
with transaction(c):
    c.execute("UPDATE campaigns SET node_id='CAR-202',current_location='Allcraft Lofts',safe_room=0 WHERE id=?", (cid,))
    entry = venues.enter(c, cid, "CAR-202")
    c.execute("INSERT OR IGNORE INTO known_places(campaign_id,place_id,how,known_at_minutes) VALUES (?,'CAR-202','isolated UI fixture',0)", (cid,))
    if not c.execute("SELECT 1 FROM stories WHERE campaign_id=? AND status='active'", (cid,)).fetchone():
        sid = c.execute("""INSERT INTO stories (campaign_id,omen_id,story_lock,title,status,phase,pressure,camera)
            VALUES (?,'OM-019','DEATH_TOLL','Isolated UI barrier fixture','active','Party',1,'on')""", (cid,)).lastrowid
        c.execute("INSERT INTO venue_overlays(campaign_id,story_id,venue_id,kind,a,b,reason,created_minutes) VALUES (?,?,'SM-087','block',?,?,'The storm door is barricaded.',0)", (cid, sid, entry["zone_id"], entry["zone_id"]))
c.close()
with open_campaign(s) as c:
    san_name = "Isolated sanatorium plan UI fixture"
    old = c.execute("SELECT id FROM campaigns WHERE name=?", (san_name,)).fetchone()
    san_id = old[0] if old else create(c, name=san_name, character="Plan Inspector", location="Geist Sanatorium", opening=False)
    with transaction(c):
        c.execute("UPDATE campaigns SET node_id='CAR-117',current_location='Geist Sanatorium',safe_room=0 WHERE id=?", (san_id,))
        venues.enter(c, san_id, "CAR-117")
        c.execute("UPDATE campaigns SET zone_id='SM-068:guide_L0_hall' WHERE id=?", (san_id,))
        c.execute("INSERT OR IGNORE INTO zone_visits(campaign_id,zone_id,first_minutes) VALUES (?,'SM-068:guide_L0_hall',0)", (san_id,))
    for label, pid in [("Accepted fraternity parcel UI fixture", "CAR-027"), ("B&B story gate UI fixture", "CAR-085")]:
        old = c.execute("SELECT id FROM campaigns WHERE name=?", (label,)).fetchone()
        ident = old[0] if old else create(c, name=label, character="Property Inspector", opening=False)
        with transaction(c):
            place = c.execute("SELECT name FROM ref.places WHERE id=?", (pid,)).fetchone()[0]
            c.execute("UPDATE campaigns SET node_id=?,current_location=?,safe_room=0 WHERE id=?", (pid, place, ident))
            venues.enter(c, ident, pid)
            c.execute("INSERT OR IGNORE INTO known_places(campaign_id,place_id,how,known_at_minutes) VALUES (?,?,'isolated property fixture',0)", (ident, pid))
(out / "ui-test-fixture.json").write_text(json.dumps({"campaign_id": cid, "url": "http://127.0.0.1:8796", "isolated": True}, indent=2), encoding="utf-8")
print(cid)
