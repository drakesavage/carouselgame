"""Smallest synthetic source packages that keep the real files' shapes: two places and an alias, one road, one
playable Omen, one starting role with its ticket and profile, one trope, one resident."""


def _place(pid, name, north, west, status="active"):
    return {"id": pid, "name": name, "category": "Landmark", "region": "Central", "map_id": "town", "coordinates": [north, west],
            "status": status, "requires": [], "spoiler": False}


MINI = {
    "locations": [_place("CAR-001", "Town Square", 500, 440), _place("CAR-016", "Diner", 480, 460)],
    "gazetteer": [_place("CAR-001", "Town Square", 500, 440), _place("CAR-016", "Diner", 480, 460),
                  _place("CAR-017", "The Diner (old name)", 480, 460, status="alias")],
    "travel_graph": {"schema_version": "1.0", "policy": "deny unless flags",
                     "nodes": [{"id": "CAR-001", "coordinates": [500, 440], "map_id": "town", "location_id": "CAR-001", "requires": []},
                               {"id": "CAR-016", "coordinates": [480, 460], "map_id": "town", "location_id": "CAR-016", "requires": []}],
                     "edges": [{"id": "EDGE-1", "from": "CAR-001", "to": "CAR-016", "kind": "road", "directed": False, "requires": [],
                                "geometry": [[500, 440], [480, 460]], "length_units": 28.0, "cost": 1.1}],
                     "aliases": {"CAR-017": "CAR-016"}},
    "pixel_anchors": {"locations": []},
    "regions": {"type": "FeatureCollection", "features": []},
    "roads": {"type": "FeatureCollection", "features": []},
    "tile_manifest": {"levels": []},
    "omens": {"registry": [{"ID": "OM-001", "Record Type": "Playable Omen", "Omen / Description": "A chair knocked over",
                            "Gazetteer ID": "CAR-016", "Map Anchor": "Diner", "Map X / 1000": 1, "Map Y / 1000": 2,
                            "Story Lock Key": "MINI", "Playable Story Title": "Mini", "Trigger Operator": "enter", "Trigger Target": "diner",
                            "Spawn / Timing": "Visible as an inspectable anomaly at its parent venue."}],
              "hub_seed_pools": []},
    "beats": {"dossiers": [{"omen_id": "OM-001", "title": "Mini", "story_lock_key": "MINI",
                            "scouting": {"player_visible_premise": "Something is wrong at the diner."},
                            "house_story": {"secret_truth": "MINI-SEALED-TRUTH"}}]},
    "roles": {"v04_playable_overlay": {"base_roles": [{"id": "base.final-girl", "name": "Final Girl"}, {"id": "base.film-buff", "name": "Film Buff"}],
                                       "aspects": [{"id": "aspect.final-girl.screamer", "name": "Screamer", "base_archetype": "Final Girl"}],
                                       "advanced_roles": [],
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
                  {"trope_id": "hb.ticket.starter.shared.quick-thinker", "trope_name": "[HB] Think on Your Feet", "source_role_id": None,
                   "relationship": "contextual_shared_starter", "original_ticket_definition": None}]},
              "v042_audit_resolution": {"starting_stat_profiles_by_role_id": {
                  "base.final-girl": {"stats": {"Mettle": 2, "Moxie": 2, "Hustle": 3, "Savvy": 2, "Grit": 2}, "base_pa": 11},
                  "base.film-buff": {"stats": {"Mettle": 1, "Moxie": 3, "Hustle": 1, "Savvy": 5, "Grit": 1}, "base_pa": 11}}}},
    "tropes": {"tropes": [{"id": "1", "name": "HIDDENTROPE-GOLF-7", "full_player": True, "has_enemy": True, "reference_only": False,
                           "variants": [{"name": "HIDDENTROPE-GOLF-7", "description": "The house always knows where you hid."},
                                        {"name": "HIDDENTROPE-GOLF-7", "description": "Doors open for it alone."}]}]},
    "npcs": {"residents": [{"id": "CAR-NPC-001", "name": "Adeline Winter", "provenance": "canon_expanded", "canon_appearance": True,
                            "at_start": {"status": "alive"}, "camp_role": "Camp leader", "usual_location": "Dyer's Lodge",
                            "gm_only": {"canonical_secret": "MINI-NPC-SECRET"}}]},
}
