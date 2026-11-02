"""Build a review page without changing approval, geography, or production selection."""
from html import escape
import json
from pathlib import Path
import sqlite3

out = Path("E:/Carousel/Asset_Reconciliation")
floor_approval_file = out / "floorplan-approval.json"
floor_approvals = {r["venue_id"]: r for r in json.loads(floor_approval_file.read_text(encoding="utf-8"))["studies"]} if floor_approval_file.exists() else {}
lark_approved = json.loads((out / "lark-exterior-review.json").read_text(encoding="utf-8")).get("exterior_appearance_approved", False)
guides = [
    ("SM-010", "Delta Epsilon Delta fraternity", "Parcel proposal A was accepted as an ADAPTATION assignment. The site point, private approach and logical entrance are bound; the knock Choice guards entry. Physical entrance registration remains unverified. No baseline basement is introduced."),
    ("SM-049", "Olde Hill B&B", "Approved layout now supports a story-entry guard at the gate. The existing 19:23 woman cue remains separately qualified; no timed Omen or story is automatically spawned. Room counts and partitions remain ADAPTATION."),
    ("SM-057", "Lark House", "Basement, ground and upper study levels are separated. Parent repair is staged, not installed; exact outside-target preservation and door registration need acceptance."),
    ("SM-068", "Geist Sanatorium", "Approved two-floor layout now supports text navigation and a discovery-filtered player plan. Records access stays locked; exact physical parent/door geometry remains unverified."),
    ("SM-087", "Allcraft Lofts", "Approved layout now supports a discovery-filtered player plan. Four floors, separate stair systems and rear-stair roof access; Writ coverage is conditional, not universal safety. Physical registration remains unverified."),
]
cards = []
for vid, title, note in guides:
    rel = f"../sublocations/production/maps/{vid}/geometry_study/registered_floor_guide.png"
    assert (out / rel).is_file(), vid
    status = "Explicit human approval recorded for this layout; physical registration remains separate." if vid in floor_approvals else "Awaiting human layout review."
    cards.append(f'<article><h3>{escape(vid)} · {escape(title)}</h3><p>{escape(note)}</p><a href="{rel}"><img loading="lazy" src="{rel}" alt="{escape(title)} registered adaptation guide"></a><p>{status}</p></article>')
with sqlite3.connect("file:E:/CarouselGame/data/reference.db?mode=ro", uri=True) as c:
    rows = c.execute("""SELECT p.id,p.name,GROUP_CONCAT(o.id,', ') FROM omens o JOIN places p ON p.id=o.place_id
        WHERE o.record_type='Playable Omen' AND o.placement='unplaced' GROUP BY p.id,p.name ORDER BY p.name""").fetchall()
table = "".join(f'<tr><td>{escape(name)}</td><td>{escape(pid)}</td><td>{escape(oids)}</td></tr>' for pid, name, oids in rows)
approval_file = out / "human-image-approval.json"
approved_count = json.loads(approval_file.read_text(encoding="utf-8"))["approved_count"] if approval_file.exists() else 0
page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Carousel — decisions remaining</title><style>body{font:17px/1.6 system-ui;background:#171717;color:#eee;max-width:1280px;margin:40px auto;padding:0 24px}a{color:#e5c693}h1,h2,h3{line-height:1.25}article{background:#242424;padding:24px;margin:24px 0;border:1px solid #555}img{display:block;max-width:100%;max-height:680px;margin:auto}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:12px;text-align:left;border-bottom:1px solid #555}.notice{border-left:4px solid #d1ae75;padding:16px;background:#27221b}</style>
<h1>Carousel: decisions remaining</h1><p class="notice">This page records no approvals. Technical validation, your visual approval, physical registration, installation and release are separate steps.</p>
<h2>1. Identity image review</h2><p><a href="review.html">Open the image gallery</a>. APPEARANCE_STATUS Approval of an appearance does not approve a parcel or floorplan. Original and superseded versions remain archived.</p>
<h2>2. Floorplan studies and integration</h2><p>Recorded layout approvals are shown below. Room counts and dimensions remain ADAPTATION. Physical portals, masks and world registration require their own technical checks. <a href="lark-exterior-review.html">Next appearance review: Lark House exterior</a>. <a href="fraternity-parcel-review.html">Accepted fraternity property assignment</a>.</p>
''' + "".join(cards) + '''<h2>3. Unplaced Omen venues</h2><p>The records and relationships exist. Source evidence has not established an exact parcel/state entry for these placements. No coordinate is invented. Provisional designer placement would need to be explicitly labeled ADAPTATION and reviewed before physical installation.</p><table><thead><tr><th>Venue</th><th>Identity</th><th>Omens</th></tr></thead><tbody>''' + table + '''</tbody></table>
<h2>4. Story scope</h2><p>Dead Air remains an original WCRL scouting card, separate from canonical KRSL. A private antagonist/mechanism/ending packet would be original authored content, not recovered source canon.</p>
<p><a href="resolution_ledger.json">Full repair ledger</a> · <a href="verification_summary.json">Technical verification</a> · <a href="route-hold-verification.json">Remaining passage constraints</a></p></html>'''
page = page.replace("Next appearance review: Lark House exterior", "Approved Lark House exterior; bounded repair in preparation" if lark_approved else "Next appearance review: Lark House exterior")
page = page.replace("APPEARANCE_STATUS", f"Explicit user approval recorded for {approved_count} of 23 candidates." if approved_count else "Reply with approved IDs or names and list anything to revise.")
(out / "decisions.html").write_text(page, encoding="utf-8")
(out / "pending-review.json").write_text(json.dumps({"identity_candidates_awaiting_human_review": 23 - approved_count,
    "identity_candidates_human_approved": approved_count,
    "physical_guide_reviews": [{"venue_id": v, "title": t, "limitation": n} for v, t, n in guides if v not in floor_approvals],
    "floorplan_layouts_human_approved": list(floor_approvals),
    "physical_registration_pending": [v for v, _, _ in guides],
    "accepted_property_assignments": ["FRAT-PARCEL-A"] if (out / "fraternity-parcel-acceptance.json").exists() else [],
    "pending_exterior_appearance_reviews": [] if lark_approved else [{"venue_id": "SM-057", "review": "lark-exterior-review.html"}],
    "approved_exterior_appearances": ["SM-057"] if lark_approved else [],
    "unplaced_venue_records": [{"place_id": pid, "name": n, "omen_ids": ids.split(", ")} for pid, n, ids in rows],
    "human_approval_inferred": False}, indent=2), encoding="utf-8")
print(f"Prepared 23-image review link, {len(guides)} physical guides and {len(rows)} unplaced venue records")
