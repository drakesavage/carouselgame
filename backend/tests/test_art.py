"""Campaign art: prompts from player-visible data only, GM suggestions wait for approval, the worker renders through
ComfyUI (faked here), installs portraits, and never touches the GM's turn state mid-turn."""
import base64
import uuid

from app.art import service as art
from app.art.service import ArtWorker
from app.db import connect, one, rows
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
    assert (wf["4"]["inputs"]["width"], wf["4"]["inputs"]["height"]) == art.SIZES["character"]


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
