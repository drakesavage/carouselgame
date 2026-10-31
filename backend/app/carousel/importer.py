"""Builds data/reference.db from the Carousel source packages."""
from __future__ import annotations

from .import_places import import_places

IMPORTERS = [import_places]
