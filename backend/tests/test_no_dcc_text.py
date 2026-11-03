"""Nothing sent to a model may speak Crawler Console: every module-level string of the prompt modules, every tool
description, and a rendered briefing are scanned for Dungeon Crawler Carl wording."""
import importlib
import re

import pytest

DCC = re.compile(r"\b(crawlers?|dungeons?|syndicates?|borant|saferooms?|mana|floor clocks?|over city)\b", re.I)
MODULES = ["app.gm.prompts", "app.gm.tools", "app.memory.generate", "app.art.service", "app.art.codex_image", "app.memory.context"]


def strings(value, where):
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from strings(v, f"{where}[{k!r}]")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from strings(v, f"{where}[{i}]")


def hits(pairs):
    return [f"{where}: {m.group(0)!r}" for where, text in pairs for m in DCC.finditer(text)]


@pytest.mark.parametrize("name", MODULES)
def test_prompt_constants_speak_carousel(name):
    mod = importlib.import_module(name)
    pairs = [p for k, v in vars(mod).items() if k.isupper() for p in strings(v, f"{name}.{k}")]
    assert not hits(pairs)


def test_tool_descriptions_and_ops_reference_speak_carousel():
    from app.gm.tools import TOOL_SPECS
    assert not hits(strings(TOOL_SPECS, "TOOL_SPECS"))


def test_a_briefing_speaks_carousel(conn, campaign_id):
    from app.memory.context import build_context
    text = build_context(conn, campaign_id, "We look around the square.")["text"]
    assert not hits([("briefing", text)])
