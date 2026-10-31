import pkgutil

import app
from app.gm.tools import TOOL_SPECS

DICE_TOOLS = {"skill_check", "roll_dice", "grind", "resolve_floor_end"}


def test_no_dice_tools():
    assert DICE_TOOLS.isdisjoint({t["name"] for t in TOOL_SPECS})


def test_no_dice_module():
    names = {m.name for m in pkgutil.walk_packages(app.__path__, "app.")}
    assert "app.rules.dice" not in names and "app.rules.checks" not in names
