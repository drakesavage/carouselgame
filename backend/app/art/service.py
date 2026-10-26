"""Campaign artwork: prompts from player-visible records, a job queue, and one background worker driving ComfyUI.

Flow: the player presses Generate (status queued) or the GM calls `suggest_image` during a turn (status suggested,
waiting for the player's one-click approval). The worker renders queued jobs one at a time, saves the picture under
data/art/c<campaign>/, and makes it the subject's current picture (a character's also becomes their portrait).
Nothing renders without the player: suggestions wait for approval.

Prompts use only what the player can see (sheets' `visual` blocks, public item and scene text). A GM-written
description is checked against unrevealed GM secrets and dropped if it repeats any of their wording.
"""
from __future__ import annotations

import json
import random
import re
import shutil
import sqlite3
import threading
import time
import traceback
from pathlib import Path

from ..config import Settings
from ..db import connect, now_iso, one, rows, transaction
from .comfy import ComfyClient, ComfyError, default_root, sdxl_txt2img

KINDS = ("character", "item", "mob", "scene", "location")
NAMED = ("mob", "location")  # kinds keyed by name rather than a record id
SIZES = {"character": (832, 1216), "mob": (896, 1152), "item": (1024, 1024), "scene": (1344, 768), "location": (1344, 768)}
STYLE = ("(gritty painted fantasy illustration:1.3), (oil painting:1.2), painterly, tabletop RPG sourcebook art, visible brushstrokes, "
         "dramatic chiaroscuro lighting, grimy dungeon atmosphere, darkly humorous, highly detailed")
KIND_STYLE = {
    "character": "waist-up character portrait, facing the viewer, detailed expressive face, plain dark background",
    "mob": "monster illustration, full body, menacing pose, dungeon background",
    "item": "the whole object in full view with empty space around it, single isolated object, centered, alone on a bare worn dark "
            "wooden surface, simple uncluttered background, nothing else in the frame, no people",
    "scene": "environment illustration, wide establishing shot, no people, no characters",
    "location": "environment illustration of a place, wide establishing shot, no people, no characters",
}
NEGATIVE = ("(photo, photograph, photorealistic:1.3), realistic skin texture, studio portrait, 3d render, anime, manga, cartoon, chibi, blurry, lowres, jpeg artifacts, watermark, "
            "(text, letters, words, writing:1.2), name tag, signature, logo, frame, border, extra limbs, extra fingers, deformed hands, disfigured face, cropped head, "
            "nsfw, nude")
KIND_NEGATIVE = {"scene": "people, person, man, woman, figures, crowd, portrait", "location": "people, person, man, woman, figures, crowd, portrait",
                 "item": "people, person, hands, face, (clutter, multiple objects, still life:1.2), candles, bottles, coins, books, scattered props, "
                         "(close-up, cropped, extreme close-up, texture, pattern, wallpaper:1.2)"}
FIRE_WORDS = re.compile(r"\b(flames?|flaming|burning|ablaze|on fire|lit|smok\w*|embers?|sparks?)\b", re.I)
MAX_PROMPT = 1700  # SDXL reads long prompts in 77-token chunks; the style tail must survive


def checkpoint_name(settings: Settings) -> str:
    return settings.comfy_checkpoint


def sampler_settings(ckpt: str) -> dict:
    """Turbo/Lightning distillations want few steps and low CFG; full SDXL models want the usual settings."""
    if re.search(r"turbo|lightning|hyper", ckpt, re.I):
        return {"steps": 8, "cfg": 2.0, "sampler": "dpmpp_sde", "scheduler": "karras"}
    return {"steps": 30, "cfg": 6.5, "sampler": "dpmpp_2m", "scheduler": "karras"}


# ------------------------------------------------------------------ prompts
def _clean(text: str | None) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text


def _list(v) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else ([str(v)] if v else [])


def _positive_notes(notes: list[str]) -> list[str]:
    # SDXL handles negations badly ("do not show injury" draws injury): keep only affirmative guidance.
    return [n for n in notes if not re.match(r"\s*(do not|don't|avoid|never|no )", n, re.I)]


ANIMALS = re.compile(r"\b(dog|retriever|terrier|shepherd|hound|cat|kitten|rat|mouse|bird|parrot|crow|horse|pig|goat|ferret|"
                     r"rabbit|lizard|snake|monkey|beast|animal)\b", re.I)
SHE = re.compile(r"\b(she|her|hers|herself|woman|girl|female|lady)\b", re.I)
HE = re.compile(r"\b(he|him|his|himself|man|boy|male|guy)\b", re.I)


def character_brief(conn: sqlite3.Connection, cid: int, c: dict) -> dict:
    """Physical brief for a character portrait, from the record only.

    * subject: what the picture is of, first in the prompt (SDXL weights early words most): species for animals,
      otherwise woman/man taken from the pronouns the character's own record uses (never guessed from a name);
      "person" when the record doesn't say.
    * description: the sheet's `visual` block: summary, established features, signature gear, a couple of
      illustration notes; record-keeping notes and comparisons with other characters left out.
    * negative: terms that contradict the subject (e.g. "man, beard" for a woman).
    * unestablished: traits the ledger explicitly leaves open (age, face, hair...), for the Appearance field.
    """
    sheet = c.get("sheet") or {}
    vis = sheet.get("visual") or {}
    narrative = sheet.get("narrative") or {}
    race = c.get("race") or c.get("species") or ""
    others = [o["name"].split()[0] for o in rows(conn, "SELECT name FROM characters WHERE campaign_id = ? AND id != ?", (cid, c["id"]))]

    def drawable(line: str) -> bool:
        return not re.search(r"\b(recorded|currently|current condition|no current|uninjured|rested|canonically)\b", line, re.I) \
            and not any(re.search(rf"\b{re.escape(n)}\b", line) for n in others if len(n) > 2) \
            and line.strip(" .").lower() not in (race.lower(), "human")

    ref = vis.get("reference") or {}
    if ref:
        return _reference_brief(ref, vis, others)
    own_text = json.dumps(vis, ensure_ascii=False) + " " + str(narrative.get("personality_summary") or "") + " " + str(narrative.get("current_role") or "")
    she, he = len(SHE.findall(own_text)), len(HE.findall(own_text))
    if ANIMALS.search(race):
        noun = re.sub(r"(?i)\bearth-born\b", "", race).strip()
        subject, negative = f"(portrait of a {noun}:1.3), realistic animal anatomy", "human, person, anthropomorphic, humanoid, standing upright"
    elif she > he:
        subject, negative = f"(portrait of a {race.lower() + ' ' if race and race.lower() != 'human' else ''}woman:1.3)", "man, male, beard, mustache, stubble"
    elif he > she:
        subject, negative = f"(portrait of a {race.lower() + ' ' if race and race.lower() != 'human' else ''}man:1.3)", "woman, female"
    else:
        subject, negative = f"(portrait of a {race.lower() or 'person'}:1.2)", ""
    bits = [_clean(vis.get("summary"))]
    bits += [f for f in _list(vis.get("established_features")) if drawable(f)][:3]
    gear = _list(vis.get("signature_gear"))
    if gear:
        bits.append("carrying " + ", ".join(gear[:3]))
    bits += [n for n in _positive_notes(_list(vis.get("illustration_notes"))) if drawable(n)][:2]
    if not vis:
        bits.append(_clean(narrative.get("current_role")))
    # Class names ("Bodycam Paladin") are game titles, not looks, and pull toward fantasy armor: left out.
    # "X rather than Y" in the record: SDXL would draw Y, so Y moves to the negative prompt.
    avoid = []
    for i, b in enumerate(bits):
        avoid += [m.strip(" .") for m in re.findall(r"rather than (?:replacing it with |being |looking like )?(?:an? |the )?([^.;,]+)", b, re.I)]
        bits[i] = re.sub(r",?\s*rather than [^.;]+", "", b, flags=re.I).strip()
    negative = ", ".join(x for x in [negative, *[a for a in avoid if len(a.split()) >= 2][:4]] if x)
    return {"subject": subject, "description": "; ".join(b for b in bits if b), "negative": negative,
            "unestablished": [str(x) for x in _list(vis.get("unestablished"))]}


AVOID = re.compile(r"(?:,|—|-|;)?\s*\b(?:rather than|not an?|not the)\s+([^.;,—]+)", re.I)


def _reference_brief(ref: dict, vis: dict, others: list[str]) -> dict:
    """Brief from an imported visual reference (importer/visual_reference.py): its demographics lead the prompt."""
    demo = _clean(ref.get("demographic"))
    age, size = _clean(ref.get("age")), _clean(ref.get("size"))
    animal = bool(ANIMALS.search(demo) or re.search(r"\balligator|crocodile\b", demo, re.I))
    low = demo.lower()
    if animal:
        subject = f"(portrait of a {low}:1.3), realistic animal anatomy" + (f", {size.lower()}" if size else "")
        negative = "(humanoid, anthropomorphic, standing upright, bipedal, human torso:1.4), human, person, cartoon, clothing on a reptile"
    else:
        noun = re.sub(r"\bhuman (male|female)\b", lambda m: "man" if m.group(1).lower() == "male" else "woman", low)
        noun = re.sub(r"\bmale\b", "man", re.sub(r"\bfemale\b", "woman", noun))
        lead = f"{age}-year-old {noun}" if age.isdigit() else (f"{noun} in their {age.lower()}" if age else noun)
        lead = re.sub(r"\btheir\b", "his" if re.search(r"\bman\b", noun) else "her" if re.search(r"\bwoman\b", noun) else "their", lead)
        subject = f"(portrait of {'an' if lead[:1].lower() in 'aeiou' else 'a'} {lead}:1.3)"
        negative = "man, male, beard, mustache, stubble" if re.search(r"\bwoman\b", noun) else "woman, female" if re.search(r"\bman\b", noun) else ""

    def keep(sentence: str) -> bool:  # drop instructions to the illustrator and comparisons with other characters
        return not re.search(r"\b(should|must|reference photo|portrait-reference|likeness|LoRA)\b", sentence, re.I) \
            and not any(re.search(rf"\b{re.escape(n)}\b", sentence) for n in others if len(n) > 2)

    negations: list[str] = []

    avoid: list[str] = []
    parts = []
    for label, text in (("", ref.get("physique")), ("", ref.get("outfit")), ("signature: ", ref.get("signature")), ("expression: ", ref.get("expression"))):
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", _clean(text)) if s and keep(s)]
        # "No X, Y." / "Do not ..." / "Never ..." sentences: SDXL would draw X and Y, so they become negatives
        for s in [s for s in sentences if re.match(r"(no|not|do not|don't|never)\b", s, re.I)]:
            negations.append(re.sub(r"^(no|not|do not|don't|never)\s+(an?\s+|the\s+)?", "", s, flags=re.I).rstrip("."))
        sentences = [s for s in sentences if not re.match(r"(no|not|do not|don't|never)\b", s, re.I)]
        body = " ".join(sentences)
        avoid += [m.strip(" .") for m in AVOID.findall(body)]
        body = AVOID.sub("", body).strip()
        if body:
            parts.append(label + body)
    if not animal and not re.search(r"\b(armor|armour|guard|harness|plate|mail)\b", _clean(ref.get("outfit")), re.I):
        negative += ", (plate armor, pauldrons, shoulder armor, chainmail, metal shoulder guards, breastplate:1.5)"  # modern clothes: keep fantasy armor out
    negative = ", ".join(x for x in [negative, *[a for a in avoid if len(a.split()) >= 2][:4], *[n[:120] for n in negations[:3]]] if x)
    return {"subject": subject, "description": "; ".join(parts), "negative": negative,
            "unestablished": [str(x) for x in _list(vis.get("unestablished"))]}


def appearance(conn: sqlite3.Connection, cid: int, kind: str, key: str) -> str:
    """The player's own appearance notes for a subject (kept with the art, not campaign canon)."""
    r = one(conn, "SELECT appearance FROM art_subjects WHERE campaign_id = ? AND kind = ? AND subject_key = ?", (cid, kind, key.lower()))
    return _clean(r["appearance"]) if r else ""


GENERIC_AVOID = {"weapon", "vehicle", "car", "device", "machine", "object", "tool", "item", "kit", "container", "canister", "pouch",
                 "bag", "box", "trap", "equipment", "gear", "building", "place", "structure"}


def _misleads(words: str | None, name: str) -> bool:
    """True when the drafter named at least one word that is really in the name and isn't literal."""
    named = {w.lower() for w in re.findall(r"[A-Za-z]+", name)}
    return any(w.lower() in named for w in re.findall(r"[A-Za-z]+", words or ""))


def clean_avoid(avoid_text: str, look: str) -> str:
    """Drop don't-draw terms that the look itself needs ('dart' for a dart, 'pickup truck' for the pickup) and generic
    category words: in a negative prompt they would erase the very thing being painted."""
    words = {re.sub(r"(es|s)$", "", w) for w in re.findall(r"[a-z]+", look.lower())}
    keep = []
    for term in (t.strip() for t in (avoid_text or "").split(",")):
        tw = [re.sub(r"(es|s)$", "", w) for w in re.findall(r"[a-z]+", term.lower()) if len(w) > 2]
        if not tw or term.lower() in GENERIC_AVOID or any(w in words for w in tw):
            continue
        keep.append(term)
    return ", ".join(dict.fromkeys(keep))


def _draft(settings: Settings, system: str, record: str, schema: dict, label: str) -> str:
    """The look drafter: Codex (better judgment on literal wording; the user's ChatGPT plan, no API billing), else Gemini
    Flash Lite. Returns the raw JSON text."""
    from ..gm.providers import AGENTS_NOTICE, CodexCliProvider, GeminiProvider, ProviderError
    try:
        raw, _ = CodexCliProvider(settings, effort="low")._run(record, schema, label, system + "\nReply only with the JSON object.\n\n" + AGENTS_NOTICE)
        return raw
    except ProviderError:
        from ..gm.router import GeminiQuota
        model = "gemini-3.5-flash-lite"
        if not settings.gemini_api_key() or GeminiQuota(settings).remaining(model) < 1:
            raise ValueError("Codex is unavailable and there is no Gemini Flash Lite quota left today") from None
        raw, _ = GeminiProvider(settings, model=model, effort="low")._generate(system, record, schema, label)
        return raw


def features(conn: sqlite3.Connection, cid: int, kind: str, key: str) -> str:
    r = one(conn, "SELECT features FROM art_subjects WHERE campaign_id = ? AND kind = ? AND subject_key = ?", (cid, kind, key.lower()))
    return (r or {}).get("features") or ""


def set_features(conn: sqlite3.Connection, cid: int, kind: str, key: str, text: str) -> None:
    conn.execute("""INSERT INTO art_subjects (campaign_id, kind, subject_key, appearance, features, updated_at) VALUES (?,?,?,?,?,?)
                    ON CONFLICT(campaign_id, kind, subject_key) DO UPDATE SET features = excluded.features, updated_at = excluded.updated_at""",
                 (cid, kind, key.lower(), "", _clean(text)[:300], now_iso()))


def _with_features(look: str, feats: str) -> str:
    """SDXL weighs early words most and drops details: the must-show features go first, weighted up, then the full look."""
    parts = [f.strip().strip("()") for f in (feats or "").split(",") if f.strip()][:4]
    if not look:
        return ""
    return ", ".join([f"({p}:1.25)" for p in parts] + [look]) if parts else look


def avoid(conn: sqlite3.Connection, cid: int, kind: str, key: str) -> str:
    r = one(conn, "SELECT avoid FROM art_subjects WHERE campaign_id = ? AND kind = ? AND subject_key = ?", (cid, kind, key.lower()))
    return (r or {}).get("avoid") or ""


def set_avoid(conn: sqlite3.Connection, cid: int, kind: str, key: str, text: str) -> None:
    conn.execute("""INSERT INTO art_subjects (campaign_id, kind, subject_key, appearance, avoid, updated_at) VALUES (?,?,?,?,?,?)
                    ON CONFLICT(campaign_id, kind, subject_key) DO UPDATE SET avoid = excluded.avoid, updated_at = excluded.updated_at""",
                 (cid, kind, key.lower(), "", _clean(text)[:300], now_iso()))


def set_appearance(conn: sqlite3.Connection, cid: int, kind: str, key: str, text: str) -> None:
    conn.execute("""INSERT INTO art_subjects (campaign_id, kind, subject_key, appearance, updated_at) VALUES (?,?,?,?,?)
                    ON CONFLICT(campaign_id, kind, subject_key) DO UPDATE SET appearance = excluded.appearance, updated_at = excluded.updated_at""",
                 (cid, kind, key.lower(), text.strip()[:600], now_iso()))


def subject_negative(conn: sqlite3.Connection, cid: int, kind: str, subject_id: int | None, subject_name: str | None = None) -> str:
    if kind == "location" and subject_name:
        return avoid(conn, cid, "location", _named_key("location", subject_name))
    if kind == "item" and subject_id:
        # only the look counts: names like "Heavy Fire Blanket" are what made SDXL set it alight
        fire = "" if FIRE_WORDS.search(appearance(conn, cid, "item", str(subject_id))) else "fire, flames, burning, smoke"
        return ", ".join(x for x in (fire, avoid(conn, cid, "item", str(subject_id))) if x)
    if kind != "character" or not subject_id:
        return ""
    c = one(conn, "SELECT * FROM characters WHERE campaign_id = ? AND id = ?", (cid, subject_id))
    if not c:
        return ""
    from ..importer.visual_reference import bible_guide
    guard = bible_guide(conn, cid).get("negative_guard") if ((c.get("sheet") or {}).get("visual") or {}).get("reference") else None
    return ", ".join(x for x in (character_brief(conn, cid, c)["negative"], (guard or "")[:400]) if x)


def subject_text(conn: sqlite3.Connection, cid: int, kind: str, subject_id: int | None, subject_name: str | None) -> tuple[str, str]:
    """(display name, visual description) from player-visible records only."""
    if kind == "character":
        c = one(conn, "SELECT * FROM characters WHERE campaign_id = ? AND id = ?", (cid, subject_id)) if subject_id else \
            one(conn, "SELECT * FROM characters WHERE campaign_id = ? AND lower(name) = lower(?)", (cid, subject_name or ""))
        if not c:
            raise ValueError("unknown character")
        d = character_brief(conn, cid, c)
        return c["name"], "; ".join(b for b in (d["subject"], appearance(conn, cid, "character", str(c["id"])), d["description"]) if b)
    if kind == "item":
        i = one(conn, "SELECT * FROM items WHERE campaign_id = ? AND id = ?", (cid, subject_id)) if subject_id else \
            one(conn, "SELECT * FROM items WHERE campaign_id = ? AND lower(name) = lower(?)", (cid, subject_name or ""))
        if not i:
            raise ValueError("unknown item")
        # Name and look only: notes, effects and status lines are inventory bookkeeping (counts, provenance, rules) that
        # an image model draws literally or not at all. Looks come from the Appearance notes (draft_item_looks).
        # The look alone, without the name: an image model draws names literally ("Porcupine Pod" painted a porcupine,
        # "Accordion Barricade" an accordion). The name is used only while no look has been written.
        return i["name"], _with_features(appearance(conn, cid, "item", str(i["id"])), features(conn, cid, "item", str(i["id"]))) or i["name"]
    if kind == "scene":
        s = one(conn, "SELECT * FROM scenes WHERE campaign_id = ? AND id = ?", (cid, subject_id)) if subject_id else \
            one(conn, "SELECT * FROM scenes WHERE campaign_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1", (cid,))
        if not s:
            raise ValueError("no scene")
        # Imported situations carry ledger bookkeeping (gold and clock changes, payments): keep only what can be seen.
        clauses = re.split(r"[;\n]|(?<=\.)\s", str(s.get("situation") or ""))
        bookkeeping = re.compile(r"\.yaml|record|->|→|\bgold\b|\bclock\b|\bpaid\b|\bpayment|\bminutes?\b|\bhours?\b|\bsnapshot|\bchange:", re.I)
        situation = " ".join(c.strip() for c in clauses if c.strip() and not bookkeeping.search(c)) if s.get("source") == "play" else ""
        bits = [s.get("location") or s["title"], _clean(situation)[:250], _clean(s.get("conditions"))]
        return s["title"], "; ".join(b for b in bits if b)
    if kind == "mob":
        name = _clean(subject_name)
        if not name:
            raise ValueError("a mob needs a name")
        return name, "; ".join(x for x in (name, appearance(conn, cid, "mob", name)) if x)
    if kind == "location":
        # only a place on the party's map: a picture never depicts a discovery the party hasn't made (memory/places.py)
        from ..memory.places import chain, key, known_world
        world = known_world(conn, cid)
        k = key((chain(subject_name) or [subject_name or ""])[-1])
        node = world["nodes"].get(k)
        if not node:
            raise ValueError(f"{subject_name!r} isn't a place the party knows")
        where, n = [], node
        while n["parent"]:
            n = world["nodes"][n["parent"]]
            where.append(n["name"])
        # the look (drafted from the place's player-known record, art only); its record text names people and plans, which
        # a painter would draw, so without a look the prompt is just the place and its district
        look = _with_features(appearance(conn, cid, "location", k), features(conn, cid, "location", k))
        return node["name"], look or (node["name"] + (f", in {', '.join(where)}" if where else ""))
    raise ValueError(f"kind must be one of {', '.join(KINDS)}")


ANIMAL_STYLE = "full-body animal portrait, natural pose, all four feet on the ground, eye level, dark background"
BIG_ANIMAL_STYLE = "full-body wildlife painting, side view, body low on the ground, natural pose, murky water and mud"
BIG_ANIMAL = re.compile(r"\b(alligator|crocodile|horse|bear|bull|cow|boar|moose|elk)\b", re.I)


def subject_layout(conn: sqlite3.Connection, cid: int, kind: str, subject_id: int | None) -> tuple[str, tuple[int, int]]:
    """Framing and size: a tall waist-up portrait suits people; animals get a full-body pose (a tall frame makes SDXL
    stand a quadruped upright like a person), and big low animals a wide frame."""
    if kind == "character" and subject_id:
        c = one(conn, "SELECT race, sheet_json FROM characters WHERE campaign_id = ? AND id = ?", (cid, subject_id))
        if c:
            ref = ((c.get("sheet") or {}).get("visual") or {}).get("reference") or {}
            what = f"{ref.get('demographic') or ''} {c.get('race') or ''}"
            if BIG_ANIMAL.search(what):
                return BIG_ANIMAL_STYLE, (1216, 832)
            if ANIMALS.search(what):
                return ANIMAL_STYLE, (896, 1152)
    return KIND_STYLE[kind], SIZES[kind]


ITEM_LOOK_SYSTEM = """You write short visual descriptions for item illustrations in a Dungeon Crawler Carl tabletop campaign. The
crawlers are contemporary Earth survivors in a magical dungeon: much of their gear is ordinary modern equipment, scavenged junk,
or something they built from it, and some items are System-made or enchanted. For each item, write one or two plain sentences
(under 40 words) saying what it physically looks like: shape, material, colours, size, condition, and container for a bundle or
cache. Real-world objects look like their real-world counterparts (a fire blanket is a folded grey fibreglass sheet in a red
pouch with pull tabs). Use any condition the record gives (scorched, repaired, makeshift). Do not invent glows, magical effects,
text, labels or logos unless the record says so. No numbers of uses, rules, owners, backstory or people.
Begin with the kind of object in everyday words, its overall silhouette, its size next to a person and how it stands or lies
("a waist-high freestanding folding barrier, standing open in a zigzag, seen from a three-quarter angle"); a painter given
only surface words ("slats", "panel", "wall", "folded flat") paints a flat texture that fills the frame, so describe the whole
object's outline.
Write it for a painter who never sees the item's name and takes every word literally. Use plain physical words only: no
metaphors, nicknames or comparisons, and never a word that names an animal, a musical instrument, a person or another object
unless the thing literally is one. A "Porcupine Pod" trap is "a squat dented steel canister on a round spring base, its lid
studded with short sharp steel spikes, one thin dart tip showing in a hole on top", not "quills"; an "Accordion Barricade" is "a
tall folding wall of riveted steel slats in a zigzag lattice, chipped yellow and black hazard paint", not "concertina". Use the
record's construction (trap components, attachments, materials) to say what it is made of.
Also give "features": two to four short comma-separated phrases naming what makes it recognisable and must be visible (for
the Porcupine Pod: "dozens of short sharp steel spikes sticking out of the lid, squat steel canister on a coil spring"; for
the Accordion Barricade: "freestanding zigzag folding steel barrier, yellow and black hazard stripes"). The first feature is
the object's overall shape. Plain words, no commas inside a phrase.
Also give "misleading": the words of the item's NAME that are not literally what it is ("Porcupine" in Porcupine Pod,
"Accordion" in Accordion Barricade, "Crawler" in Crawler Biscuit, "Murder" in Murder Drum), or empty when every word is
literal ("Plastic alligator dashboard bobblehead" really is a plastic alligator; a "golden-retriever ornament" really is a
dog figurine; "Recovered Shotgun Shell Cache" really is shotgun shells).
Then "avoid": only what those misleading words could make a painter draw instead, and empty when "misleading" is empty (for the Porcupine Pod:
"porcupine, hedgehog, animal, fur, quills"; for the Accordion Barricade: "accordion, musical instrument, piano keys,
bellows"). Never list the item itself, what it really is, or any word your look uses (a dart's avoid list never says "dart";
a pickup truck's never says "truck" or "vehicle"), and never anything that resembles a feature it must show: a negative
prompt suppresses look-alikes too, so a spiked pod's list leaves out "quills" and "spines" (they would erase its spikes). Leave it empty when the name already says plainly what it is. If a
"current_look" is given, keep what it gets right and rewrite misleading, figurative or vague wording (like "quills" or
"concertina"); don't add glows or magic the record doesn't give. Return every item id you were given."""

PLACE_LOOK_SYSTEM = """You write short visual descriptions for location illustrations (wide establishing shots, no people) in a
Dungeon Crawler Carl tabletop campaign. The Third Floor is the Over City: a sprawling, decaying fantasy city built by the
System, full of mismatched districts, markets, guildhalls, inns and ruins, under a strange sky. For each place, write two or
three plain sentences (under 60 words) of what the painter should see: architecture, materials, scale, street or room layout,
light, weather and mood. Use only what the record says plus ordinary details that fit it (a saferoom is an inn or shelter
with a warm, protected look; a market district has stalls, awnings and signage without readable text). For a place the party
has only seen from outside ("visited": false), describe its exterior as seen from a distance. Never include people, crowds,
named characters, monsters or plans; no text or logos. Write for a painter who takes every word literally: no metaphors.
Also give "features": two to four short comma-separated phrases a painter must show (for a saferoom inn: "warm hearth
fire, heavy barred door").
Also give "avoid": a short comma list of things the NAME could make a painter wrongly draw instead ("The Molting Pigeon":
"pigeon, bird, feathers"; "Fallen Watch": "wristwatch, clock"), or empty; never a word your look uses. Return every place
key you were given."""


def _item_record(i: dict) -> dict:
    props = {k: v for k, v in (i.get("properties") or {}).items()
             if not re.search(r"gm|secret|owner|mechanical|reference|quantity|count|stock", k, re.I)}
    rec = {"id": i["id"], "name": i["name"], "rarity": i.get("rarity"), "slot": i.get("slot"), "note": _clean(i.get("description")), **props}
    text = json.dumps({k: v for k, v in rec.items() if v not in (None, "", [], {})}, ensure_ascii=False, default=str)
    return json.loads(text) if len(text) <= 900 else {"id": i["id"], "name": i["name"], "summary": text[:900]}


def draft_place_looks(conn: sqlite3.Connection, settings: Settings, cid: int, keys: list[str] | None = None, overwrite: bool = False) -> dict:
    """A look and an avoid list for each place on the party's map, from its player-known record (art only, not canon)."""
    from ..gm.providers import GeminiProvider, ProviderError
    from ..gm.router import GeminiQuota
    from ..memory.places import known_world
    world = known_world(conn, cid)
    todo = [n for k, n in world["nodes"].items() if (not keys or k in keys) and (overwrite or not appearance(conn, cid, "location", k))]
    if not todo:
        return {"drafted": [], "skipped": 0, "remaining": 0}
    def where(n):
        out, p = [], n["parent"]
        while p:
            out.append(world["nodes"][p]["name"])
            p = world["nodes"][p]["parent"]
        return ", ".join(out)
    facts = rows(conn, "SELECT fact FROM lore_facts WHERE campaign_id = ? AND visibility = 'player'", (cid,))
    record = json.dumps([{"key": n["key"], "name": n["name"], "in": where(n), "visited": n["visited"], "saferoom": n["saferoom"],
                          "record": _clean(n["description"]),
                          "lore": [f["fact"][:300] for f in facts if n["name"].lower() in f["fact"].lower()][:3],
                          "current_look": appearance(conn, cid, "location", n["key"]) or None} for n in todo], ensure_ascii=False)
    schema = {"type": "object", "additionalProperties": False, "required": ["places"], "properties": {"places": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["key", "look", "features", "avoid"],
        "properties": {"key": {"type": "string"}, "look": {"type": "string"}, "features": {"type": "string"}, "avoid": {"type": "string"}}}}}}
    try:
        raw = _draft(settings, PLACE_LOOK_SYSTEM, record, schema, "place-looks")
    except ProviderError as exc:
        raise ValueError(str(exc)) from None
    names = {n["key"]: n["name"] for n in todo}
    drafted, skipped = [], 0
    for d in json.loads(raw).get("places") or []:
        look = _clean(d.get("look"))[:700]
        if d.get("key") not in names or not look or leaks_secret(conn, cid, look):
            skipped += 1
            continue
        av = clean_avoid(d.get("avoid") or "", look)
        set_appearance(conn, cid, "location", d["key"], look)
        set_avoid(conn, cid, "location", d["key"], av)
        set_features(conn, cid, "location", d["key"], d.get("features") or "")
        drafted.append({"key": d["key"], "name": names[d["key"]], "appearance": look, "features": d.get("features") or "", "avoid": av})
    return {"drafted": drafted, "skipped": skipped, "remaining": len(todo) - len(drafted) - skipped}


def draft_item_looks(conn: sqlite3.Connection, settings: Settings, cid: int, item_ids: list[int] | None = None,
                     overwrite: bool = False, batch: int = 20) -> dict:
    """Ask Gemini Flash Lite for a physical look and an avoid list per item, saved as the item's Appearance notes (art only,
    not canon; the owner reviews and edits them). Existing notes are kept unless overwrite, which revises them (the current
    look is sent along, so what it gets right is kept). Batches keep the quota use to a few calls."""
    from ..gm.providers import GeminiProvider, ProviderError
    from ..gm.router import GeminiQuota
    from ..importer.visual_reference import bible_guide
    sql, args = "SELECT * FROM items WHERE campaign_id = ? AND (quantity IS NULL OR quantity > 0)", [cid]
    if item_ids:
        sql += f" AND id IN ({','.join('?' * len(item_ids))})"
        args += list(item_ids)
    items = [i for i in rows(conn, sql + " ORDER BY id", args)
             if i["name"].lower() != "gold" and (overwrite or not appearance(conn, cid, "item", str(i["id"])))]
    if not items:
        return {"drafted": [], "skipped": 0, "remaining": 0}
    chunks = [items[k:k + batch] for k in range(0, len(items), batch)]
    canon = bible_guide(conn, cid).get("shared_hardware")
    system = ITEM_LOOK_SYSTEM + (f"\nEstablished look of a shared item: {json.dumps(canon, ensure_ascii=False)}" if canon else "")
    schema = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "look", "features", "misleading", "avoid"],
        "properties": {"id": {"type": "integer"}, "look": {"type": "string"}, "features": {"type": "string"}, "misleading": {"type": "string"},
                       "avoid": {"type": "string"}}}}}}
    drafted, skipped = [], 0
    for chunk in chunks:
        record = json.dumps([{**_item_record(i), "current_look": appearance(conn, cid, "item", str(i["id"])) or None} for i in chunk], ensure_ascii=False)
        try:
            raw = _draft(settings, system, record, schema, "item-looks")
        except ProviderError as exc:
            if drafted:
                break  # keep what was drafted; report the rest as remaining
            raise ValueError(str(exc)) from None
        names = {i["id"]: i["name"] for i in chunk}
        for d in json.loads(raw).get("items") or []:
            look = _clean(d.get("look"))[:600]
            if d.get("id") not in names or not look or leaks_secret(conn, cid, look):
                skipped += 1
                continue
            # a don't-draw list only where the name misleads: for a literal name it can only suppress the thing itself
            av = clean_avoid(d.get("avoid") or "", look) if _misleads(d.get("misleading"), names[d["id"]]) else ""
            set_appearance(conn, cid, "item", str(d["id"]), look)
            set_avoid(conn, cid, "item", str(d["id"]), av)
            set_features(conn, cid, "item", str(d["id"]), d.get("features") or "")
            drafted.append({"id": d["id"], "name": names[d["id"]], "appearance": look, "features": d.get("features") or "", "avoid": av})
    return {"drafted": drafted, "skipped": skipped, "remaining": len(items) - len(drafted) - skipped}


def appearances(conn: sqlite3.Connection, cid: int, kind: str) -> dict[str, str]:
    return {r["subject_key"]: r["appearance"] for r in rows(conn, "SELECT subject_key, appearance FROM art_subjects WHERE campaign_id = ? AND kind = ? "
                                                                   "AND appearance != ''", (cid, kind))}


def build_prompt(kind: str, description: str, extra: str | None = None, style: str | None = None) -> str:
    """Subject first (SDXL weights the start of a prompt most), then the GM's or player's extra words, then style. A short
    medium anchor leads, so a long description (weighted features, a full look) can't push the painting style out of the
    first 77-token chunk and leave a photograph."""
    body = "; ".join(x for x in (description, _clean(extra)) if x)
    anchor = "(oil painting:1.2) of " if kind in ("item", "location") else ""  # portraits keep their tuned subject-first start
    return f"{anchor}{body}, {style or KIND_STYLE[kind]}, {STYLE}"[:MAX_PROMPT]


def _shingles(text: str, n: int = 6) -> set[str]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def leaks_secret(conn: sqlite3.Connection, cid: int, text: str) -> bool:
    secrets = " ".join(r["content"] for r in rows(conn, "SELECT content FROM gm_secrets WHERE campaign_id = ? AND revealed = 0", (cid,)))
    return bool(_shingles(text) & _shingles(secrets))


# ------------------------------------------------------------------ portraits by Codex, reference photos
def refs_dir(settings: Settings, cid: int, char_id: int) -> Path:
    return art_dir(settings, cid) / "refs" / f"character-{char_id}"


def list_refs(settings: Settings, cid: int, char_id: int) -> list[Path]:
    d = refs_dir(settings, cid, char_id)
    return sorted(p for p in d.glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")) if d.exists() else []


def engine_for(settings: Settings, kind: str, requested: str | None) -> str:
    """Character portraits go to Codex's image tool unless asked otherwise; everything else to ComfyUI."""
    from ..config import resolve_codex_bin
    if kind != "character" or (requested or settings.portrait_engine) != "codex":
        return "comfy"
    return "codex" if resolve_codex_bin() else "comfy"


def guide_for_subject(guide: dict, others: list[str]) -> dict:
    """The visual bible's campaign-wide rules, minus what is about someone else: sentences naming another character,
    instructions about reference photos or LoRAs (the brief has its own photo line), sentences about one person (he/his/
    she/her), and 'Never' items naming others."""
    def other(text: str) -> bool:
        return any(re.search(rf"\b{re.escape(n)}\b", text) for n in others if len(n) > 2)

    def keep(sentence: str) -> bool:
        return not other(sentence) and not re.search(r"\b(LoRA|reference photo|below|he|his|him|she|her)\b",
                                                     sentence, re.I)

    rules = []
    for rule in guide.get("global_rules") or []:
        text = " ".join(s for s in re.split(r"(?<=[.!?])\s+", rule.replace("**", "")) if s and keep(s))
        if text:
            rules.append(text)
    neg = []
    for sentence in re.split(r"(?<=[.!?])\s+", guide.get("negative_guard") or ""):
        items = [i for i in re.split(r",\s+(?:or\s+)?|\s+or\s+", sentence.rstrip(".")) if i and not other(i)]
        if items:
            neg.append((", ".join(items[:-1]) + " or " + items[-1] if len(items) > 2 else " or ".join(items)) + ".")
    return {**guide, "global_rules": rules, "negative_guard": " ".join(neg)}


def codex_prompt(conn: sqlite3.Connection, settings: Settings, cid: int, char_id: int, extra: str | None = None) -> str:
    from .codex_image import portrait_prompt
    c = one(conn, "SELECT * FROM characters WHERE campaign_id = ? AND id = ?", (cid, char_id))
    if not c:
        raise ValueError("unknown character")
    ref = ((c.get("sheet") or {}).get("visual") or {}).get("reference") or {}
    brief = character_brief(conn, cid, c)
    style, (w, h) = subject_layout(conn, cid, "character", char_id)
    look = "; ".join(x for x in (appearance(conn, cid, "character", str(char_id)), _clean(extra)) if x)
    from ..importer.visual_reference import bible_guide
    guide = bible_guide(conn, cid) if ref.get("source", "").startswith("state/characters/VISUAL_BIBLE") else {}
    others = [o["name"].split()[0] for o in rows(conn, "SELECT name FROM characters WHERE campaign_id = ? AND id != ?", (cid, char_id))]
    guide = guide_for_subject(guide, others)
    return portrait_prompt(c["name"], ref, brief, look, animal=style != KIND_STYLE["character"], wide=w > h,
                           has_refs=bool(list_refs(settings, cid, char_id)), guide=guide)


# ------------------------------------------------------------------ jobs
def create_job(conn: sqlite3.Connection, settings: Settings, cid: int, kind: str, *, subject_id: int | None = None,
               subject_name: str | None = None, prompt: str | None = None, extra: str | None = None, source: str = "player",
               status: str = "queued", reason: str | None = None, turn_id: int | None = None, engine: str | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    name, desc = subject_text(conn, cid, kind, subject_id, subject_name)
    if kind == "character" and not subject_id:
        subject_id = one(conn, "SELECT id FROM characters WHERE campaign_id = ? AND name = ?", (cid, name))["id"]
    if kind == "item" and not subject_id:
        subject_id = one(conn, "SELECT id FROM items WHERE campaign_id = ? AND name = ?", (cid, name))["id"]
    if kind == "scene" and not subject_id:
        subject_id = one(conn, "SELECT id FROM scenes WHERE campaign_id = ? AND title = ? ORDER BY id DESC LIMIT 1", (cid, name))["id"]
    note = None
    if source == "gm" and extra and leaks_secret(conn, cid, extra):
        extra, note = None, "GM description dropped: it repeated wording from a GM-only record"
    style, (w, h) = subject_layout(conn, cid, kind, subject_id)
    eng = engine_for(settings, kind, engine)
    if eng == "codex":
        final = (prompt or "").strip()[:4000] or codex_prompt(conn, settings, cid, subject_id, extra)
    else:
        final = (prompt or "").strip()[:MAX_PROMPT] or build_prompt(kind, desc, extra, style)
    jid = conn.execute("""INSERT INTO art_jobs (campaign_id, kind, subject_id, subject_name, prompt, negative, width, height, seed, status,
                          source, reason, note, turn_id, checkpoint, created_at, engine) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (cid, kind, subject_id, name, final, ", ".join(x for x in (NEGATIVE, KIND_NEGATIVE.get(kind), subject_negative(conn, cid, kind, subject_id, name)) if x), w, h, random.randint(1, 2**31 - 1), status, source, reason, note,
                        turn_id, checkpoint_name(settings) if eng == "comfy" else "codex image tool", now_iso(), eng)).lastrowid
    return job_view(one(conn, "SELECT * FROM art_jobs WHERE id = ?", (jid,)))


def job_view(j: dict) -> dict:
    out = {k: j[k] for k in ("id", "campaign_id", "kind", "subject_id", "subject_name", "prompt", "width", "height", "seed", "status",
                             "source", "reason", "note", "error", "is_current", "created_at", "finished_at", "checkpoint", "engine")}
    out["url"] = f"media/art/{j['campaign_id']}/{j['file']}" if j.get("file") else None
    return out


def current_art(conn: sqlite3.Connection, cid: int) -> dict:
    """Player view: the current picture per subject, for the HUD, sheets, inventory and combat panel."""
    out: dict[str, dict] = {k: {} for k in KINDS}
    for j in rows(conn, "SELECT kind, subject_id, subject_name, file FROM art_jobs WHERE campaign_id = ? AND is_current = 1 AND status = 'done'", (cid,)):
        url = f"media/art/{cid}/{j['file']}"
        key = _named_key(j["kind"], j["subject_name"]) if j["kind"] in NAMED else str(j["subject_id"])
        out[j["kind"]][key] = url
    return out


def make_current(conn: sqlite3.Connection, settings: Settings, job_id: int) -> dict:
    """Mark a finished picture as its subject's current one; for a character also install it as the portrait.
    Call inside a transaction while holding the turn lock (characters are part of the turn's state fingerprint)."""
    j = one(conn, "SELECT * FROM art_jobs WHERE id = ?", (job_id,))
    if not j or j["status"] != "done":
        raise ValueError("only a finished picture can be used")
    same = "subject_name = ? COLLATE NOCASE" if j["kind"] in NAMED else "subject_id = ?"
    conn.execute(f"UPDATE art_jobs SET is_current = 0 WHERE campaign_id = ? AND kind = ? AND {same}",
                 (j["campaign_id"], j["kind"], j["subject_name"] if j["kind"] in NAMED else j["subject_id"]))
    conn.execute("UPDATE art_jobs SET is_current = 1 WHERE id = ?", (job_id,))
    if j["kind"] == "character" and j["subject_id"]:
        settings.portraits_dir.mkdir(parents=True, exist_ok=True)
        name = f"art-{j['id']}.png"
        shutil.copyfile(art_dir(settings, j["campaign_id"]) / j["file"], settings.portraits_dir / name)
        conn.execute("UPDATE characters SET portrait_path = ?, portrait_crop_json = NULL WHERE id = ?", (name, j["subject_id"]))
    return job_view(one(conn, "SELECT * FROM art_jobs WHERE id = ?", (job_id,)))


def _named_key(kind: str, name: str) -> str:
    """A Mob by its lower-case name; a place by its map key ('the molting pigeon' -> 'molting pigeon')."""
    if kind == "location":
        from ..memory.places import key
        return key(name)
    return name.lower()


def art_dir(settings: Settings, cid: int) -> Path:
    return settings.data_dir / "art" / f"c{cid}"


# ------------------------------------------------------------------ worker
class ArtWorker:
    """One background thread; renders queued jobs in order. `turn_lock` is the GM's WRITE_LOCK: installing a portrait
    waits for any turn in progress so it never invalidates the turn's state fingerprint."""

    def __init__(self, settings: Settings, turn_lock: threading.Lock | None = None):
        self.settings = settings
        self.turn_lock = turn_lock
        self.client = ComfyClient(settings.comfy_url, default_root())
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self.current: int | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        conn = connect(self.settings.campaign_db)
        try:
            conn.execute("UPDATE art_jobs SET status = 'queued', started_at = NULL WHERE status = 'running'")  # interrupted renders
        finally:
            conn.close()
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="art-worker")
            self._thread.start()
        self._wake.set()

    def poke(self) -> None:
        self._wake.set()

    def status(self) -> dict:
        stats = self.client.stats()
        conn = connect(self.settings.campaign_db)
        try:
            queued = one(conn, "SELECT COUNT(*) AS n FROM art_jobs WHERE status IN ('queued','running')")["n"]
            waiting = one(conn, "SELECT COUNT(*) AS n FROM art_jobs WHERE status = 'suggested'")["n"]
        finally:
            conn.close()
        ckpts = self.client.checkpoints() if stats else []
        root = default_root()
        installed = bool(root and (root / "python_embeded" / "python.exe").exists())
        on_disk = bool(root and (root / "ComfyUI" / "models" / "checkpoints" / self.settings.comfy_checkpoint).exists())
        return {"comfy_url": self.settings.comfy_url, "running": bool(stats), "installed": installed, "root": str(root) if root else None,
                "checkpoint": self.settings.comfy_checkpoint, "checkpoint_available": (self.settings.comfy_checkpoint in ckpts) if stats else on_disk,
                "gpu": ((stats or {}).get("devices") or [{}])[0].get("name") if stats else None,
                "queued": queued, "suggested": waiting, "rendering": self.current, "last_error": self.last_error}

    def _loop(self) -> None:
        while True:
            self._wake.wait(timeout=30)
            self._wake.clear()
            try:
                rendered = self._drain()
                if rendered:
                    self.client.free()
            except Exception:  # never let the worker die
                traceback.print_exc()

    def _drain(self) -> int:
        done = 0
        while True:
            conn = connect(self.settings.campaign_db)
            try:
                j = one(conn, "SELECT * FROM art_jobs WHERE status = 'queued' ORDER BY id LIMIT 1")
                if not j:
                    return done
                conn.execute("UPDATE art_jobs SET status = 'running', started_at = ?, error = NULL WHERE id = ?", (now_iso(), j["id"]))
                self.current = j["id"]
                try:
                    png = self._render(conn, j)
                    folder = art_dir(self.settings, j["campaign_id"])
                    folder.mkdir(parents=True, exist_ok=True)
                    fname = f"{j['kind']}-{j['id']}.png"
                    (folder / fname).write_bytes(png)
                    conn.execute("UPDATE art_jobs SET status = 'done', file = ?, finished_at = ? WHERE id = ?", (fname, now_iso(), j["id"]))
                    self._install(conn, j["id"])
                    self.last_error = None
                    done += 1
                except (ComfyError, OSError, ValueError) as exc:
                    self.last_error = str(exc)[:500]
                    conn.execute("UPDATE art_jobs SET status = 'failed', error = ?, finished_at = ? WHERE id = ?", (str(exc)[:1000], now_iso(), j["id"]))
                    if "not running" in str(exc) or "did not start" in str(exc):
                        return done  # don't burn through the queue while ComfyUI is unavailable
                finally:
                    self.current = None
            finally:
                conn.close()

    def _render(self, conn: sqlite3.Connection, j: dict) -> bytes:
        if j.get("engine") == "codex":
            from .codex_image import CodexImageError, paint
            try:
                return paint(self.settings, j["prompt"], list_refs(self.settings, j["campaign_id"], j["subject_id"]), f"art{j['id']}")
            except CodexImageError as exc:
                # Codex unavailable, over its image limit or stopped by the watcher: paint it locally instead.
                name, desc = subject_text(conn, j["campaign_id"], j["kind"], j["subject_id"], j["subject_name"])
                style, (w, h) = subject_layout(conn, j["campaign_id"], j["kind"], j["subject_id"])
                j = {**j, "engine": "comfy", "prompt": build_prompt(j["kind"], desc, None, style), "width": w, "height": h,
                     "checkpoint": self.settings.comfy_checkpoint}
                conn.execute("UPDATE art_jobs SET engine = 'comfy', prompt = ?, checkpoint = ?, note = ? WHERE id = ?",
                             (j["prompt"], j["checkpoint"], f"Codex couldn't paint it ({str(exc)[:200]}); painted by ComfyUI instead", j["id"]))
        self.client.ensure_running()
        ckpt = j.get("checkpoint") or self.settings.comfy_checkpoint
        s = sampler_settings(ckpt)
        wf = sdxl_txt2img(ckpt, j["prompt"], j["negative"], j["width"], j["height"], j["seed"], s["steps"], s["cfg"], s["sampler"], s["scheduler"],
                          f"crawler-console/c{j['campaign_id']}-{j['kind']}-{j['id']}")
        return self.client.run(wf, timeout_s=self.settings.comfy_timeout_s)

    def _install(self, conn: sqlite3.Connection, job_id: int) -> None:
        lock = self.turn_lock
        if lock is not None:
            lock.acquire()
        try:
            with transaction(conn):
                make_current(conn, self.settings, job_id)
        finally:
            if lock is not None:
                lock.release()
