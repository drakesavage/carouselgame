"""Campaign art: prompts from player-visible data only, GM suggestions wait for approval, the worker renders through
ComfyUI (faked here), installs portraits, and never touches the GM's turn state mid-turn."""
import base64
import uuid

from app.art import service as art
from app.art.service import ArtWorker
from app.db import connect, one, rows, transaction
from app.gm.orchestrator import WRITE_LOCK, TurnService
from app.gm.providers import MockProvider, ProviderResponse, ToolCall

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class FakeComfy:
    def __init__(self):
        self.workflows = []

    def ensure_running(self):
        pass

    def run(self, wf, timeout_s=900):
        self.workflows.append(wf)
        return PNG

    def free(self):
        pass

    def stats(self):
        return {"devices": [{"name": "fake gpu"}]}

    def checkpoints(self):
        return ["DreamShaperXL_Turbo_v2_1.safetensors"]


def _worker(settings):
    w = ArtWorker(settings, WRITE_LOCK)
    w.client = FakeComfy()
    return w


def test_character_prompt_uses_visual_block_and_skips_negations(conn, demo_id):
    c = one(conn, "SELECT id, sheet_json FROM characters WHERE campaign_id = ? AND slug = 'juniper'", (demo_id,))
    conn.execute("UPDATE characters SET sheet_json = json_set(sheet_json, '$.visual', json(?)) WHERE id = ?",
                 ('{"summary": "Wiry courier in a patched raincoat", "signature_gear": ["Shopping cart shield"], '
                  '"illustration_notes": ["Do not show injuries", "Keep the raincoat bright yellow"]}', c["id"]))
    name, desc = art.subject_text(conn, demo_id, "character", c["id"], None)
    prompt = art.build_prompt("character", desc)
    assert "patched raincoat" in prompt and "Shopping cart shield" in prompt and "bright yellow" in prompt
    assert "Do not show" not in prompt and prompt.endswith(art.STYLE)


def test_gm_description_that_repeats_a_secret_is_dropped(conn, settings, demo_id):
    conn.execute("INSERT INTO gm_secrets (campaign_id, subject, kind, content, source, created_at) VALUES (?,?,?,?,?,?)",
                 (demo_id, "Glimmet", "plot", "Glimmet is secretly a mimic wearing a shopkeeper suit made of socks", "test", "now"))
    job = art.create_job(conn, settings, demo_id, "mob", subject_name="Sock Mimic", source="gm", status="suggested",
                         extra="a shopkeeper secretly a mimic wearing a shopkeeper suit made of socks")
    assert job["note"] and "mimic wearing" not in job["prompt"]


def test_gm_suggestion_waits_for_approval_then_renders_and_installs_the_portrait(client, settings, demo_id):
    class Suggests(MockProvider):
        def complete(self, system, steps, tools, *, job_label):
            if not any(s["kind"] == "tool_results" for s in steps):
                return ProviderResponse([ToolCall("suggest_image", {"kind": "character", "subject": "Bix (demo)",
                                                                    "description": "small rat-like companion with bright eyes"})],
                                        "Bix twitches his whiskers.")
            return ProviderResponse([], "Bix twitches his whiskers.")

    client.app_state.turns = TurnService(settings, Suggests, run_async=False)
    body = client.post(f"/api/campaigns/{demo_id}/turns", json={"text": "I look at Bix", "idempotency_key": uuid.uuid4().hex}).json()
    assert body["status"] == "completed", body
    jobs = client.get(f"/api/campaigns/{demo_id}/art").json()
    assert len(jobs) == 1 and jobs[0]["status"] == "suggested" and jobs[0]["source"] == "gm"
    worker = _worker(settings)
    assert worker._drain() == 0  # nothing renders before approval
    assert client.post(f"/api/art/{jobs[0]['id']}/approve").json()["status"] == "queued"
    assert worker._drain() == 1
    done = client.get(f"/api/campaigns/{demo_id}/art").json()[0]
    assert done["status"] == "done" and done["is_current"] == 1 and done["url"]
    assert client.get("/" + done["url"]).status_code == 200
    bix = next(p for p in client.get(f"/api/campaigns/{demo_id}").json()["party"] if p["name"] == "Bix (demo)")
    assert bix["portrait_path"] == f"art-{done['id']}.png"
    wf = worker.client.workflows[0]
    assert wf["1"]["inputs"]["ckpt_name"] == settings.comfy_checkpoint and wf["5"]["inputs"]["steps"] == 8  # turbo settings
    assert (wf["4"]["inputs"]["width"], wf["4"]["inputs"]["height"]) in (art.SIZES["character"], (896, 1152))  # Bix is a rat: animal framing


def test_one_suggestion_per_turn_and_no_duplicates(conn, settings, demo_id):
    from app.gm.tools import ToolRunner
    from app.rules.dice import DiceRoller
    r = ToolRunner(conn, None, DiceRoller(), demo_id, 999)
    first = r.run("suggest_image", {"kind": "mob", "subject": "Lint Goblin", "description": "fuzzy grey goblin"})
    assert first["ok"] and first["result"]["suggestion_id"]
    assert not r.run("suggest_image", {"kind": "mob", "subject": "Other", "description": "x"})["ok"]
    r2 = ToolRunner(conn, None, DiceRoller(), demo_id, 1000)
    assert r2.run("suggest_image", {"kind": "mob", "subject": "lint goblin", "description": "again"})["result"].get("skipped")


def test_player_generate_mob_and_switch_current(client, settings, demo_id):
    w = _worker(settings)
    a = client.post(f"/api/campaigns/{demo_id}/art", json={"kind": "mob", "subject_name": "Lint Goblin"}).json()
    b = client.post(f"/api/campaigns/{demo_id}/art", json={"kind": "mob", "subject_name": "Lint Goblin", "prompt": "custom prompt"}).json()
    assert w._drain() == 2
    summary = client.get(f"/api/campaigns/{demo_id}").json()
    assert summary["art"]["mob"]["lint goblin"].endswith(f"mob-{b['id']}.png")
    client.post(f"/api/art/{a['id']}/use")
    assert client.get(f"/api/campaigns/{demo_id}").json()["art"]["mob"]["lint goblin"].endswith(f"mob-{a['id']}.png")
    assert client.get(f"/api/campaigns/{demo_id}/art", params={"kind": "mob"}).json()[0]["prompt"] == "custom prompt"


def test_comfy_unavailable_fails_the_job_and_stops(client, settings, demo_id):
    from app.art.comfy import ComfyError
    w = _worker(settings)

    def down():
        raise ComfyError("ComfyUI is not running at http://127.0.0.1:8188 and no portable install was found")
    w.client.ensure_running = down
    client.post(f"/api/campaigns/{demo_id}/art", json={"kind": "mob", "subject_name": "A"})
    client.post(f"/api/campaigns/{demo_id}/art", json={"kind": "mob", "subject_name": "B"})
    assert w._drain() == 0
    st = [j["status"] for j in client.get(f"/api/campaigns/{demo_id}/art").json()]
    assert st == ["queued", "failed"]  # the second waits instead of failing too


def test_portrait_subject_comes_first_with_gender_from_the_records_own_pronouns(conn, settings, demo_id):
    c = one(conn, "SELECT id FROM characters WHERE campaign_id = ? AND slug = 'juniper'", (demo_id,))
    conn.execute("UPDATE characters SET race = 'Human', sheet_json = json_set(sheet_json, '$.visual', json(?)) WHERE id = ?",
                 ('{"summary": "Former courier; she keeps her raincoat on", "established_features": ["Human."], '
                  '"unestablished": ["Exact age", "Hair"]}', c["id"]))
    job = art.create_job(conn, settings, demo_id, "character", subject_id=c["id"])
    assert job["prompt"].startswith("(portrait of a woman:1.3)") and job["prompt"].index("Former courier") < job["prompt"].index(art.STYLE)
    assert "beard" in one(conn, "SELECT negative FROM art_jobs WHERE id = ?", (job["id"],))["negative"]
    art.set_appearance(conn, demo_id, "character", str(c["id"]), "woman in her forties, short grey-streaked hair")
    again = art.create_job(conn, settings, demo_id, "character", subject_id=c["id"])
    assert "short grey-streaked hair" in again["prompt"].split(";")[1]


def test_animal_companions_get_their_species_not_a_person(conn, demo_id):
    c = one(conn, "SELECT * FROM characters WHERE campaign_id = ? AND slug = 'bix'", (demo_id,))
    c["race"] = "Earth-born Golden Retriever"
    brief = art.character_brief(conn, demo_id, c)
    assert brief["subject"].startswith("(portrait of a Golden Retriever") and "human" in brief["negative"]


def test_appearance_endpoint_and_preview(client, demo_id):
    cid = next(p["id"] for p in client.get(f"/api/campaigns/{demo_id}").json()["party"] if p["slug"] == "juniper")
    assert client.put(f"/api/campaigns/{demo_id}/art/appearance", json={"kind": "character", "subject_id": cid, "appearance": "freckles"}).status_code == 200
    p = client.get(f"/api/campaigns/{demo_id}/art/preview", params={"kind": "character", "subject_id": cid}).json()
    assert p["appearance"] == "freckles" and "freckles" in p["prompt"]


REF = """# Visual Reference

## Juniper Vale (demo)
**Black American woman | 37 | Courier**

Approximately 5'8", athletic. Deep-brown skin, braided hair in a low bun.

Battered yellow raincoat over a navy shirt. Her look should come from reference photos.

**Signature:** Shopping-cart shield, not a fantasy knight in armor.

**Expression:** Watchful and wry.

## Bix (demo)
**Male brown rat | Approximately 1 pound | Scout**

Glossy brown fur, one torn ear.

**Signature:** Tiny satchel.

**Expression:** Nervous.

## Nobody Here
**Human | 20 | None**

Nothing.
"""


def test_visual_reference_parses_and_imports_with_a_save_point(client, settings, demo_id):
    from app.importer.visual_reference import parse
    es = parse(REF)
    assert [e["name"] for e in es] == ["Juniper Vale (demo)", "Bix (demo)", "Nobody Here"]
    assert es[0]["age"] == "37" and es[1]["size"] == "Approximately 1 pound" and "raincoat" in es[0]["outfit"]
    r = client.post(f"/api/campaigns/{demo_id}/visual-reference", json={"markdown": REF, "source": "test"}).json()
    assert r["updated"] == ["Juniper Vale (demo)", "Bix (demo)"] and r["unmatched"] == ["Nobody Here"] and r["save_point_id"]
    c = connect(settings.campaign_db)
    jid = one(c, "SELECT id FROM characters WHERE campaign_id = ? AND slug = 'juniper'", (demo_id,))["id"]
    job = art.create_job(c, settings, demo_id, "character", subject_id=jid)
    p = job["prompt"]
    assert p.startswith("(portrait of a 37-year-old black american woman:1.3)") and "yellow raincoat" in p and p.endswith(art.STYLE)
    assert "reference photos" not in p and "fantasy knight" not in p
    neg = one(c, "SELECT negative FROM art_jobs WHERE id = ?", (job["id"],))["negative"]
    assert "beard" in neg and "fantasy knight in armor" in neg
    bix = one(c, "SELECT id FROM characters WHERE campaign_id = ? AND slug = 'bix'", (demo_id,))["id"]
    assert art.create_job(c, settings, demo_id, "character", subject_id=bix)["prompt"].startswith("(portrait of a male brown rat:1.3)")
    from app.memory.context import build_context
    assert "looks: Black American woman, 37" in build_context(c, demo_id, "hello")["text"]
    sheet = client.get(f"/api/campaigns/{demo_id}/characters/{jid}").json()["sheet"]
    assert sheet["visual"]["reference"]["expression"] == "Watchful and wry."


def test_codex_paints_portraits_with_reference_photos_and_falls_back_to_comfy(client, settings, demo_id, monkeypatch):
    from app.art import codex_image
    monkeypatch.setattr(art, "engine_for", lambda s, kind, req: "codex" if kind == "character" and req != "comfy" else "comfy")
    jid = one(connect(settings.campaign_db), "SELECT id FROM characters WHERE campaign_id = ? AND slug = 'juniper'", (demo_id,))["id"]
    up = client.post(f"/api/campaigns/{demo_id}/characters/{jid}/art-refs", files={"file": ("me.png", PNG, "image/png")})
    assert up.status_code == 200 and client.get(f"/api/campaigns/{demo_id}/characters/{jid}/art-refs").json()[0]["url"]
    seen = {}

    def fake_paint(s, prompt, refs, label):
        seen.update(prompt=prompt, refs=refs)
        return PNG
    monkeypatch.setattr(codex_image, "paint", fake_paint)
    job = client.post(f"/api/campaigns/{demo_id}/art", json={"kind": "character", "subject_id": jid}).json()
    assert job["engine"] == "codex" and job["prompt"].startswith("Paint one portrait of Juniper")
    w = _worker(settings)
    assert w._drain() == 1 and len(seen["refs"]) == 1 and "Reference photos are attached" in seen["prompt"]
    assert not w.client.workflows  # ComfyUI not used

    def broken(s, prompt, refs, label):
        raise codex_image.CodexImageError("image limit reached")
    monkeypatch.setattr(codex_image, "paint", broken)
    job2 = client.post(f"/api/campaigns/{demo_id}/art", json={"kind": "character", "subject_id": jid}).json()
    assert w._drain() == 1
    done = next(j for j in client.get(f"/api/campaigns/{demo_id}/art").json() if j["id"] == job2["id"])
    assert done["status"] == "done" and done["engine"] == "comfy" and "image limit" in done["note"] and w.client.workflows
    name = client.get(f"/api/campaigns/{demo_id}/characters/{jid}/art-refs").json()[0]["name"]
    assert client.delete(f"/api/campaigns/{demo_id}/characters/{jid}/art-refs/{name}").status_code == 200


def test_portrait_crop_is_saved_shown_on_cards_and_reset_by_a_new_portrait(client, settings, demo_id):
    jid = next(p["id"] for p in client.get(f"/api/campaigns/{demo_id}").json()["party"] if p["slug"] == "juniper")
    crop = {"cx": 0.5, "cy": 0.3, "k": 0.6, "ar": 0.684}
    assert client.put(f"/api/campaigns/{demo_id}/characters/{jid}/portrait-crop", json=crop).json()["portrait_crop"] == crop
    card = next(p for p in client.get(f"/api/campaigns/{demo_id}").json()["party"] if p["id"] == jid)
    assert card["portrait_crop"] == crop
    assert client.put(f"/api/campaigns/{demo_id}/characters/{jid}/portrait-crop", json={"cx": 2, "cy": 0.3, "k": 0.6, "ar": 1}).status_code == 422
    client.post(f"/api/campaigns/{demo_id}/characters/{jid}/portrait", files={"file": ("p.png", PNG, "image/png")})
    assert next(p for p in client.get(f"/api/campaigns/{demo_id}").json()["party"] if p["id"] == jid)["portrait_crop"] is None
    client.put(f"/api/campaigns/{demo_id}/characters/{jid}/portrait-crop", json=crop)
    assert client.put(f"/api/campaigns/{demo_id}/characters/{jid}/portrait-crop", json={}).json()["portrait_crop"] is None


def test_bible_rules_are_trimmed_to_the_subject():
    guide = {"global_rules": ["Earth survivors, not a guild.",
                              "Casting notes below are choices. For Cliff, **his LoRA outranks styling**. Do not replace his likeness."],
             "negative_guard": "No medieval costume, humanoid Ghost, size inflation of Scout, or extra limbs. No future gear."}
    g = art.guide_for_subject(guide, ["Cliff", "Scout"])
    assert g["global_rules"] == ["Earth survivors, not a guild."]
    assert g["negative_guard"] == "No medieval costume, humanoid Ghost or extra limbs. No future gear."


def test_item_looks_are_drafted_in_batches_and_replace_bookkeeping_in_prompts(conn, settings, demo_id, monkeypatch):
    import json as _json
    from app.gm import providers, router
    calls = []

    def fake_generate(self, system, prompt, schema, label):
        items = _json.loads(prompt)
        calls.append(len(items))
        return _json.dumps({"items": [{"id": i["id"], "look": f"a plain {i['name'].lower()}"} for i in items]}), {}
    monkeypatch.setattr(providers.GeminiProvider, "_generate", fake_generate)
    monkeypatch.setattr(type(settings), "gemini_api_key", lambda self: "test-key")
    monkeypatch.setattr(router.GeminiQuota, "remaining", lambda self, model: 50)
    with transaction(conn):
        iid = conn.execute("INSERT INTO items (campaign_id, name, quantity, description, properties_json) VALUES (?,?,?,?,?)",
                           (demo_id, "Heavy Fire Blanket", 1, "Three previously held; see state/x.yaml.", '{"status": "scorched"}')).lastrowid
    n = one(conn, "SELECT count(*) AS n FROM items WHERE campaign_id = ? AND (quantity IS NULL OR quantity > 0) AND lower(name) != 'gold'", (demo_id,))["n"]
    out = art.draft_item_looks(conn, settings, demo_id, batch=3)
    assert len(out["drafted"]) == n and calls and max(calls) <= 3 and len(calls) == -(-n // 3)
    name, desc = art.subject_text(conn, demo_id, "item", iid, None)
    assert desc == "Heavy Fire Blanket; a plain heavy fire blanket" and ".yaml" not in desc
    assert "fire, flames" in art.subject_negative(conn, demo_id, "item", iid)
    again = art.draft_item_looks(conn, settings, demo_id)  # existing looks are kept
    assert again["drafted"] == [] and len(calls) == -(-n // 3)


def test_draft_item_looks_endpoint_takes_a_json_body(client, demo_id, monkeypatch):
    seen = {}
    monkeypatch.setattr(art, "draft_item_looks", lambda conn, settings, cid, ids, overwrite: seen.update(ids=ids, overwrite=overwrite) or
                        {"drafted": [], "skipped": 0, "remaining": 0})
    r = client.post(f"/api/campaigns/{demo_id}/art/draft-item-looks", json={"item_ids": [3], "overwrite": True})
    assert r.status_code == 200 and seen == {"ids": [3], "overwrite": True}
