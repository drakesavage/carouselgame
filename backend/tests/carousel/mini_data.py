"""Smallest synthetic source packages that keep the real files' shapes: a small town graph (lodge, junction, square, diner,
farm, a flagged spoiler place, an old diner with an alias, one directed flagged shortcut), three playable Omens and a hub, a fully specified story dossier (OM-001), two starting roles, an aspect, an advanced role, a story-altering ticket with its ticket and profile, one trope, two residents, the arrival lot."""


def _place(pid, name, north, west, status="active", category="Landmark", requires=(), spoiler=False):
    return {"id": pid, "name": name, "category": category, "region": "Central", "map_id": "town", "coordinates": [north, west],
            "status": status, "requires": list(requires), "spoiler": spoiler}


def _node(nid, north, west, requires=()):
    return {"id": nid, "coordinates": [north, west], "map_id": "town", "location_id": None if nid.startswith("J-") else nid,
            "requires": list(requires)}


def _edge(eid, a, b, cost, kind="road", directed=False, requires=()):
    pa, pb = COORDS[a], COORDS[b]
    return {"id": eid, "from": a, "to": b, "kind": kind, "directed": directed, "requires": list(requires), "geometry": [pa, pb],
            "length_units": round(((pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2) ** 0.5, 2), "cost": cost}


COORDS = {"CAR-047": [620, 440], "J-1": [560, 440], "CAR-002": [500, 440], "CAR-006": [480, 460], "CAR-086": [560, 520],
          "CAR-099": [580, 560], "CAR-016": [495, 445], "CAR-082": [470, 400]}
PLACES = [_place("CAR-047", "Dyer's Lodge", *COORDS["CAR-047"], category="Lodge"),
          _place("CAR-002", "Town Square", *COORDS["CAR-002"]),
          _place("CAR-006", "Diner", *COORDS["CAR-006"], category="Restaurant"),
          _place("CAR-086", "Farm", *COORDS["CAR-086"], category="Farm"),
          _place("CAR-099", "UNKNOWNPLACE-HOTEL-8", *COORDS["CAR-099"], requires=["akers_story"], spoiler=True),
          _place("CAR-016", "Old diner", *COORDS["CAR-016"], category="Restaurant"),
          _place("CAR-082", "Eastern arrival parking lot", *COORDS["CAR-082"], category="Parking")]
FIXED = "Visible as an inspectable anomaly at its parent venue."
ROAMING = "Spawn as a visible moving encounter near its anchor."


def _omen(oid, desc, place, anchor, spawn):
    return {"ID": oid, "Record Type": "Playable Omen", "Omen / Description": desc, "Gazetteer ID": place, "Map Anchor": anchor,
            "Map X / 1000": 1, "Map Y / 1000": 2, "Story Lock Key": f"MINI-{oid}", "Playable Story Title": f"Mini {oid}",
            "Trigger Operator": "enter", "Trigger Target": anchor.lower(), "Spawn / Timing": spawn}


def _dossier(oid, premise, truth):
    return {"omen_id": oid, "title": f"Mini {oid}", "story_lock_key": f"MINI-{oid}", "scouting": {"player_visible_premise": premise},
            "house_story": {"secret_truth": truth}}


PHASE_NAMES = ("Omen", "Choice", "Party", "First Blood", "Rebirth", "Second Blood", "Finale", "The End")
FULL_DOSSIER = {   # OM-001: everything a story needs (commitment, 8 phases, 4 outcomes, the sealed house story)
    **_dossier("OM-001", "Something is wrong at the farm.", "MINI-SEALED-TRUTH"),
    "commitment": {"implementation_event": "enter:farm", "trigger_operator": "enter", "trigger_target": "farm"},
    "phase_plan": [{"phase": p, "obligation": f"MINI-OBLIGATION {p}"} for p in PHASE_NAMES],
    "outcomes": {"full_success": {"predicate": "The scarecrow is burned."}, "costly_success": {"predicate": "Burned, but someone is lost."},
                 "survival_only": {"predicate": "Someone escapes the farm."}, "failure": {"predicate": "The farm keeps everyone."}},
    "house_story": {"secret_truth": "MINI-SEALED-TRUTH", "actionable_counterplay": "MINI-COUNTERPLAY",
                    "antagonist_behavior": {"policy": "MINI-ANTAGONIST"}, "clue_routes": [{"id": "c1", "evidence": "MINI-CLUE"}]},
    "continuity": {"shared_story_lock": "MINI-OM-001"},
}


def _venue(vid, title, layer, members, zones=(), links=(), omens=()):
    """A venue in the sub-map spec's shape: zones are (node id, label, area type); the first is the entry, the second the exit."""
    nodes = [{"id": n, "label": label, "floor_hint": 0, "area_type": area} for n, label, area in zones]
    edges = [{"from": a, "to": b, "mode": mode, "two_way": True, "lock": "off_by_default" if mode == "proposed_optional_shortcut" else None}
             for a, b, mode in links]
    portals = [{"id": f"{vid}-ENTRY", "arrive_at_node": zones[0][0]}, {"id": f"{vid}-EXIT", "depart_from_node": zones[1][0]}] if zones else []
    return {"id": vid, "title": title, "layer": layer, "members": list(members), "graph": {"nodes": nodes, "edges": edges}, "portals": portals,
            "omens": [{"omen_id": o, "recommended_node": n, "placement_status": st, "choice_action": ch} for o, n, st, ch in omens]}


VENUES = {"maps": [
    _venue("SM-T01", "Farm grounds", "persistent", ["CAR-086"],
           zones=[("n01_farm_gate", "farm gate", "exterior"), ("n02_barn", "barn", "interior"),
                  ("n03_cellar_door", "cellar door", "interior"), ("n04_MINI-HIDDEN-ROOM", "MINI-HIDDEN-ROOM", "interior")],
           links=[("n01_farm_gate", "n02_barn", "walk"), ("n02_barn", "n03_cellar_door", "walk"),
                  ("n03_cellar_door", "n04_MINI-HIDDEN-ROOM", "vertical_transition"),
                  ("n01_farm_gate", "n04_MINI-HIDDEN-ROOM", "proposed_optional_shortcut")],
           omens=[("OM-005", "n03_cellar_door", "DESIGN_RECOMMENDATION_NOT_CANON_COORD", "open:cellar_door"),
                  ("OM-001", "n01_farm_gate", "NEEDS_MANUAL_NODE_REVIEW", "enter:farm")]),
    _venue("SM-T02", "A train that isn't there", "story_instance", ["CAR-016"],
           zones=[("n01_platform", "platform", "exterior"), ("n02_carriage", "carriage", "interior")],
           links=[("n01_platform", "n02_carriage", "walk")]),
    _venue("SM-T03", "Unplaced alley", "persistent", [],
           zones=[("n01_mouth", "alley mouth", "exterior"), ("n02_end", "dead end", "exterior")],
           links=[("n01_mouth", "n02_end", "walk")]),
]}

MINI = {
    "locations": PLACES,
    "gazetteer": [*PLACES, _place("CAR-017", "The Diner (old name)", *COORDS["CAR-016"], status="alias")],
    "travel_graph": {"schema_version": "1.0", "policy": "deny unless flags",
                     "nodes": [_node(n, *c, requires=["akers_story"] if n == "CAR-099" else ()) for n, c in COORDS.items()],
                     "edges": [_edge("EDGE-1", "CAR-047", "J-1", 10), _edge("EDGE-2", "J-1", "CAR-002", 5),
                               _edge("EDGE-3", "CAR-002", "CAR-006", 4), _edge("EDGE-4", "J-1", "CAR-086", 12),
                               _edge("EDGE-5", "CAR-002", "CAR-016", 1),
                               _edge("EDGE-6", "CAR-002", "CAR-086", 2, kind="scenario_path", directed=True, requires=["shortcut_story"]),
                               _edge("EDGE-7", "CAR-086", "CAR-099", 3), _edge("EDGE-8", "CAR-082", "CAR-002", 6)],
                     "aliases": {"CAR-017": "CAR-016"}},
    "pixel_anchors": {"locations": []},
    "regions": {"type": "FeatureCollection", "features": []},
    "roads": {"type": "FeatureCollection", "features": []},
    "tile_manifest": {"levels": []},
    "omens": {"registry": [_omen("OM-001", "A scarecrow facing the wrong way", "CAR-086", "Farm", FIXED),
                           _omen("OM-002", "A figure keeps pace behind the hedges", "CAR-002", "Town Square", ROAMING),
                           _omen("OM-003", "A chair knocked over", "CAR-006", "Diner", FIXED),
                           {**_omen("OM-005", "A cellar door breathing", "CAR-086", "Farm", FIXED),
                            "Trigger Operator": "open", "Trigger Target": "cellar_door"},
                           {**_omen("OM-009", "Flyers for every attraction in town", "CAR-002", "Town Square", FIXED),
                            "Record Type": "Hub / generator"}],
              "hub_seed_pools": []},
    "beats": {"dossiers": [FULL_DOSSIER,
                           _dossier("OM-002", "Someone is following.", "MINI-SEALED-TRUTH-2"),
                           _dossier("OM-003", "Something is wrong at the diner.", "MINI-SEALED-TRUTH-3"),
                           _dossier("OM-005", "The cellar door is breathing.", "MINI-SEALED-TRUTH-5")]},
    "roles": {"v04_playable_overlay": {"base_roles": [{"id": "base.final-girl", "name": "Final Girl"}, {"id": "base.film-buff", "name": "Film Buff"}],
                                       "aspects": [{"id": "aspect.final-girl.screamer", "name": "Screamer", "base_archetype": "Final Girl"}],
                                       "advanced_roles": [{"id": "advanced.adventurer", "name": "Adventurer"}],
                                       "shared_starting_trope_pool": [{"id": "hb.ticket.starter.shared.quick-thinker", "name": "[HB] Think on Your Feet",
                                                                       "rule": "Once per scene, improvise with an object already in the scene.",
                                                                       "uses": 1, "recharge": "scene"}]},
              "v041_integration": {"trope_registry_overlay": [
                  {"trope_id": "hb.ticket.base.final-girl", "trope_name": "[HB] Last One Standing", "source_role_id": "base.final-girl",
                   "relationship": "starting_signature",
                   "original_ticket_definition": {"rule": "Once per story, survive one certain death.", "uses": 1}},
                  {"trope_id": "hb.ticket.base.film-buff", "trope_name": "[HB] Seen This One", "source_role_id": "base.film-buff",
                   "relationship": "starting_signature",
                   "original_ticket_definition": {"rule": "Once per storyline, name the rule the movie is following.", "uses": 1, "recharge": None}},
                  {"trope_id": "hb.ticket.aspect.soldier.commando", "trope_name": "[HB] Change of Plan", "source_role_id": None,
                   "relationship": "aspect_signature",
                   "original_ticket_definition": {"rule": "Once per storyline, rewrite one future scene of the plan.", "uses": 1,
                                                  "scope": "storyline", "supports_story_mutation": True}},
                  {"trope_id": "hb.ticket.advanced.adventurer", "trope_name": "[HB] Expedition Leader", "source_role_id": "advanced.adventurer",
                   "relationship": "advanced_signature",
                   "original_ticket_definition": {"rule": "Once per story, find the supplies the party needs.", "uses": 1}},
                  {"trope_id": "hb.ticket.starter.shared.quick-thinker", "trope_name": "[HB] Think on Your Feet", "source_role_id": None,
                   "relationship": "contextual_shared_starter", "original_ticket_definition": None}]},
              "v042_audit_resolution": {"starting_stat_profiles_by_role_id": {
                  "base.final-girl": {"stats": {"Mettle": 2, "Moxie": 2, "Hustle": 3, "Savvy": 2, "Grit": 2}, "base_pa": 11},
                  "base.film-buff": {"stats": {"Mettle": 1, "Moxie": 3, "Hustle": 1, "Savvy": 5, "Grit": 1}, "base_pa": 11}}}},
    "tropes": {"tropes": [{"id": "1", "name": "HIDDENTROPE-GOLF-7", "full_player": True, "has_enemy": True, "reference_only": False,
                           "variants": [{"name": "HIDDENTROPE-GOLF-7", "description": "The house always knows where you hid."},
                                        {"name": "HIDDENTROPE-GOLF-7", "description": "Doors open for it alone."}]},
                          {"id": "2", "name": "MINI-RESCUE-TICKET", "full_player": True,
                           "variants": [{"trope_type": "Rescue", "tags": ["Rescue", "Story alteration"],
                                         "description": "Rescue: enter the storyline holding the missing posters of players who died in it."}]},
                          {"id": "3", "name": "MINI-LAST-WORDS", "full_player": True,
                           "variants": [{"trope_type": "Action", "tags": ["Death/sacrifice"],
                                         "description": "Once, after death, one sentence reaches the living."}]}]},
    "location_art": {"created_at": "2026-10-30", "purpose": "test", "references": [
        *[{"location_id": p, "name": p, "relative_image_path": f"assets/identity_images/{p}_identity_v001.png"}
          for p in ("CAR-016", "CAR-047", "CAR-002", "CAR-006", "CAR-086")],
        {"location_id": "PROV-LOC-001", "name": "Unrecorded place", "relative_image_path": "assets/identity_images/PROV-LOC-001_identity_v001.png"}]},
    "venues": VENUES,
    "npcs": {"residents": [{"id": "CAR-NPC-001", "name": "Adeline Winter", "provenance": "canon_expanded", "canon_appearance": True,
                            "at_start": {"status": "alive"}, "camp_role": "Camp leader", "usual_location": "Dyer's Lodge",
                            "gm_only": {"canonical_secret": "MINI-NPC-SECRET"}},
                           {"id": "CAR-NPC-003", "name": "Valerie Choi", "provenance": "original_adaptation", "canon_appearance": False,
                            "at_start": {"status": "alive"}, "camp_role": "Scout", "usual_location": "Dyer's Lodge",
                            "appearance": "Wiry, in a patched field jacket.", "personality": "Dry, watchful, quick to laugh.",
                            "gm_only": {"canonical_secret": "MINI-NPC-SECRET-2"}}]},
}
