"""The Carousel source packages the game reads, by logical name. Every file is read once as bytes: the hash and the
parsed data come from the same bytes, so a report's hash always describes what was imported. Nothing here writes."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SOURCE_FILES: dict[str, str] = {
    "locations": "Illustrated_Map_Production/game_handoff/locations.json",
    "gazetteer": "map/data/gazetteer.json",
    "travel_graph": "Illustrated_Map_Production/game_handoff/travel_graph.json",
    "pixel_anchors": "Illustrated_Map_Production/game_handoff/location_pixel_anchors.json",
    "regions": "Illustrated_Map_Production/game_handoff/regions.geojson",
    "roads": "Illustrated_Map_Production/game_handoff/roads.geojson",
    "tile_manifest": "Illustrated_Map_Production/web_tiles/tile_manifest.json",
    "omens": "omen/Carousel_Omen_Engine_Data_v0.2.json",
    "beats": "omen2story/Carousel_Omen_Story_Beat_Bible_v0_3.json",
    "roles": "roles/Carousel_Role_Bible_v0_4_2_Implementation.json",
    "tropes": "tropes/Carousel_Trope_Bible_Data_v0.1.1.json",
    "location_art": "Location_Art_Production/data/review_reference_index.json",
    "npcs": "npcs/Carousel_Camp_NPC_Package_v1/Carousel_Camp_NPC_Database_v1.json",
}


class SourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceFile:
    key: str
    path: Path
    sha256: str
    data: Any


def load_sources(source_dir: Path) -> dict[str, SourceFile]:
    """Read and parse every package; raises SourceError naming every missing or unreadable file at once."""
    out: dict[str, SourceFile] = {}
    problems: list[str] = []
    for key, rel in SOURCE_FILES.items():
        path = Path(source_dir) / rel
        try:
            raw = path.read_bytes()
            out[key] = SourceFile(key, path, hashlib.sha256(raw).hexdigest(), json.loads(raw.decode("utf-8-sig")))
        except FileNotFoundError:
            problems.append(f"{key}: missing ({path})")
        except (OSError, ValueError) as exc:
            problems.append(f"{key}: unreadable ({path}): {exc}")
    if problems:
        raise SourceError("Carousel source packages not usable: " + "; ".join(problems))
    return out


def verify_unchanged(files: dict[str, SourceFile]) -> None:
    """Re-hash every file; raises SourceError if any changed since it was read (e.g. edited mid-import)."""
    changed = []
    for f in files.values():
        try:
            now = hashlib.sha256(f.path.read_bytes()).hexdigest()
        except OSError:
            now = None
        if now != f.sha256:
            changed.append(f.key)
    if changed:
        raise SourceError(f"source files changed during the import: {', '.join(changed)}")
