"""Smallest synthetic source packages that keep the real files' shapes (used for failure-path tests)."""

MINI = {
    "locations": [],
    "gazetteer": [],
    "travel_graph": {"schema_version": "1.0", "nodes": [], "edges": [], "aliases": {}, "policy": ""},
    "pixel_anchors": {"locations": []},
    "regions": {"type": "FeatureCollection", "features": []},
    "roads": {"type": "FeatureCollection", "features": []},
    "tile_manifest": {},
    "omens": {"registry": [], "hub_seed_pools": []},
    "beats": {"dossiers": []},
    "roles": {},
    "tropes": {"tropes": []},
    "npcs": {"residents": []},
}
