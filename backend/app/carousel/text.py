"""Name matching across the Carousel packages, which mix typographic apostrophes and, in places, U+FFFD."""
from __future__ import annotations

import re

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "�": "'", "ʼ": "'"})


def norm_name(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").translate(_APOSTROPHES)).strip().casefold()
