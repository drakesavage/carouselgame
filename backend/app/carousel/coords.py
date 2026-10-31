"""Authored map coordinates are [north, west] on a 0..1000 plane; the illustrated master is north-up, origin top-left.
x = (1000 - west) * size/1000, y = (1000 - north) * size/1000 (Illustrated_Map_Production/game_handoff/README.md)."""
from __future__ import annotations

MASTER_PIXELS = 12000


def to_pixel(north: float, west: float, size: int = MASTER_PIXELS) -> tuple[float, float]:
    k = size / 1000
    return (1000 - west) * k, (1000 - north) * k


def from_pixel(x: float, y: float, size: int = MASTER_PIXELS) -> tuple[float, float]:
    k = size / 1000
    return 1000 - y / k, 1000 - x / k
