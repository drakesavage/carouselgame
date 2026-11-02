"""The game clock: campaigns.game_time_minutes counted from 08:00 on day 1 (CLOCK_START, Claude's call). Slots are
night 22:00–06:00, morning 06:00–12:00, afternoon 12:00–18:00, evening 18:00–22:00."""
from __future__ import annotations

from typing import Literal

CLOCK_START = 480   # minute 0 is 08:00 on day 1
Slot = Literal["night", "morning", "afternoon", "evening"]


def hour(minutes: int) -> int:
    return (CLOCK_START + minutes) // 60 % 24


def day(minutes: int) -> int:
    return (CLOCK_START + minutes) // 1440 + 1


def slot(minutes: int) -> Slot:
    h = hour(minutes)
    return "morning" if 6 <= h < 12 else "afternoon" if 12 <= h < 18 else "evening" if 18 <= h < 22 else "night"


def next_morning(minutes: int) -> int:
    """The game minute of the next 06:00 after `minutes`."""
    into_day = (CLOCK_START + minutes) % 1440
    return minutes + ((6 * 60 - into_day) % 1440 or 1440)
