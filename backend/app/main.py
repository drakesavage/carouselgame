"""FastAPI application: player API (/api), GM-only API (/api/gm, token-guarded), media, and the built frontend.

Layering: routes → memory.views (read models) / gm.orchestrator (turns) / memory.snapshots (saves) /
reference.rulebooks (rules index) / importer.ledger (migration). Routes never build SQL for GM tables except
under /api/gm.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .access import COOKIE, COOKIE_DAYS, AccessGate, lan_addresses, login_page
from .config import ROOT_DIR, Settings, resolve_codex_bin
from .db import connect, init_db, now_iso, one, rows, transaction
from .gm.orchestrator import WRITE_LOCK, BusyError, TurnService, recover_interrupted_turns
from .art import service as art
from .art.service import ArtWorker
from .gm.providers import make_provider, prune_jobs, secure_dir
from .importer.ledger import import_ledger, refresh_mechanical_status, report_markdown
from .ledger import sync as ledger_sync
from .ledger.sync import LedgerError
from .memory import journal, places, snapshots, views
from .reference import rulecenter
from .reference import rulebooks
from .seed.demo import seed_demo
from . import campaigns as campaign_admin

DEFAULT_LEDGER = ROOT_DIR / "campaign_sources" / "dccrepository"
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"
REPORTS_DIR = ROOT_DIR / "reports"


class PrefsIn(BaseModel):
    gm_provider: str | None = None
    codex_model: str | None = None
    gemini_model: str | None = None
    context_profile: str | None = None
    gemini_effort: str | None = None
    gemini_context_profile: str | None = None
    codex_reasoning_effort: str | None = None
    rulebook_dir: str | None = None
    ledger_dir: str | None = None
    active_campaign_id: int | None = None


class ReviewIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject|reopen)$")
    note: str | None = Field(default=None, max_length=500)


class BackupIn(BaseModel):
    label: str = Field(default="manual", max_length=40)
    media: bool = True


class ReportIn(BaseModel):
    turn_id: int | None = None
    note: str = Field(default="", max_length=4000)


class ArtIn(BaseModel):
    kind: str
    engine: str | None = None  # codex | comfy (character portraits; others always comfy)
    subject_id: int | None = None
    subject_name: str | None = None
    prompt: str | None = Field(default=None, max_length=6000)  # Codex portrait briefs carry the visual bible's rules
    extra: str | None = Field(default=None, max_length=500)


class AppearanceIn(BaseModel):
    kind: str
    subject_id: int | None = None
    subject_name: str | None = None
    appearance: str = Field(default="", max_length=600)
    avoid: str | None = Field(default=None, max_length=300)  # what the painter must not draw (None = leave as is)
    features: str | None = Field(default=None, max_length=300)  # what must be visible, weighted up in the prompt (None = leave as is)


class PlaceLooksIn(BaseModel):
    keys: list[str] | None = None
    overwrite: bool = False


class ItemLooksIn(BaseModel):
    item_ids: list[int] | None = None
    overwrite: bool = False


class VisualReferenceIn(BaseModel):
    markdown: str = Field(min_length=10, max_length=200_000)
    source: str = Field(default="visual reference", max_length=120)


class CropIn(BaseModel):
    """Square crop of a portrait: centre (cx, cy) as fractions of the image's width and height, side k as a fraction of
    the image's shorter side, ar = image width / height. null fields reset to the default framing."""
    cx: float | None = Field(default=None, ge=0, le=1)
    cy: float | None = Field(default=None, ge=0, le=1)
    k: float | None = Field(default=None, ge=0.05, le=1)
    ar: float | None = Field(default=None, gt=0.1, lt=10)


class TurnIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    idempotency_key: str = Field(min_length=8, max_length=80)


class SaveIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)


class IndexIn(BaseModel):
    directory: str | None = None


class CampaignIn(BaseModel):
    name: str = Field(max_length=80)
    crawler: str = Field(max_length=60)
    race: str | None = Field(default=None, max_length=60)
    class_name: str | None = Field(default=None, max_length=60)
    floor: int = Field(default=1, ge=1, le=18)
    location: str | None = Field(default=None, max_length=120)
    floor_hours: int | None = Field(default=None, ge=1, le=2000)
    clock_on_entry: bool = False  # the floor clock waits until the crawler enters the Dungeon
    premise: str | None = Field(default=None, max_length=2000)


class CampaignEditIn(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    archived: bool | None = None


class CampaignDeleteIn(BaseModel):
    confirm_name: str


class ImportIn(BaseModel):
    path: str | None = None



class LedgerApplyIn(BaseModel):
    fingerprint: str
    selected: list[str] | None = None
    message: str = Field(min_length=3, max_length=4000)
    author_name: str = Field(min_length=1, max_length=120)
    author_email: str = Field(min_length=3, max_length=200)
    regenerate_checkpoint: bool = False


class AppState:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_dirs()
        self.prefs_path = settings.data_dir / "settings.json"
        self.index_status: dict = {"state": "idle"}
        self.turns = TurnService(settings, self.make_provider)
        self.art = ArtWorker(settings, WRITE_LOCK)

    # Preferences live in a small JSON file (not the DB) so they can change while a turn holds the DB write lock.
    def prefs(self) -> dict:
        base = {"gm_provider": self.settings.gm_provider, "codex_model": self.settings.codex_model, "gemini_model": self.settings.gemini_model, "context_profile": "standard",
                "codex_reasoning_effort": self.settings.codex_reasoning_effort,
                "rulebook_dir": str(self.settings.rulebook_dir) if self.settings.rulebook_dir else "",
                "ledger_dir": str(self.settings.campaign_ledger_dir or DEFAULT_LEDGER), "active_campaign_id": None}
        try:
            base.update(json.loads(self.prefs_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
        return base

    def save_prefs(self, p: dict) -> None:
        self.prefs_path.write_text(json.dumps(p, indent=2), encoding="utf-8")

    def make_provider(self):
        p = self.prefs()
        if p["gm_provider"] == "auto":  # gm/router.py: Gemini for questions and conversation, Codex for mechanics
            from .gm.router import AutoGM
            return AutoGM(self.settings, codex_model=p.get("codex_model") or "", effort=p.get("codex_reasoning_effort"),
                          gemini_effort=p.get("gemini_effort") or "medium", gemini_profile=p.get("gemini_context_profile") or "large",
                          codex_profile=p.get("context_profile") or "standard")
        model = p.get("gemini_model") if p["gm_provider"] == "gemini" else p.get("codex_model")
        prov = make_provider(p["gm_provider"], self.settings, model=model or "", effort=p.get("codex_reasoning_effort"))
        prov.context_profile = p.get("context_profile") or "standard"  # briefing size: standard | large (memory/context.py PROFILES)
        return prov

    def conn(self):
        return connect(self.settings.campaign_db)


def create_app(settings: Settings | None = None, *, bootstrap: bool = True, local_hosts: set[str] | None = None) -> FastAPI:
    settings = settings or Settings()
    state = AppState(settings)
    gate = AccessGate(settings.data_dir, local_hosts)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.gm_token()  # make sure data/gm_token.txt exists for the GM-only API
        c = state.conn()
        init_db(c)
        refresh_mechanical_status(c)
        if (n := recover_interrupted_turns(c)):
            print(f"[startup] marked {n} interrupted turn(s) as failed")
        for camp in rows(c, "SELECT id FROM campaigns WHERE is_demo = 0"):
            try:
                vb = ledger_sync.load_visual_bible(c, camp["id"])
                if vb.get("updated"):
                    print(f"[startup] visual bible loaded for {len(vb['updated'])} characters ({vb['source']})")
            except Exception as exc:  # never block startup on art metadata
                print(f"[startup] visual bible not loaded: {exc}")
        secure_dir(settings.codex_jobs_dir)
        prune_jobs(settings)  # temporary GM job folders (full prompts) never outlive a run unless capture is on
        from .importer.organization import backfill
        for line in backfill(c):
            print(f"[schema v2 backfill] {line}")
        if bootstrap:
            _bootstrap(state, c)
            state.art.start()  # background renderer for campaign art (idle until a picture is queued)
        c.close()
        yield

    app = FastAPI(title="Crawler Console", lifespan=lifespan)
    app.state.crawler = state
    app.state.gate = gate

    def _client(request: Request) -> tuple[bool, str]:
        peer = request.client.host if request.client else None
        fwd = request.headers.get("x-forwarded-for")
        return gate.is_local(peer, fwd), gate.client_address(peer, fwd)

    def _prefix(request: Request) -> str:
        p = request.headers.get("x-forwarded-prefix", "").rstrip("/")
        return p if p.startswith("/") and all(ch.isalnum() or ch in "/-_" for ch in p) else ""

    @app.middleware("http")
    async def passcode_gate(request: Request, call_next):
        local, _ = _client(request)
        path = request.url.path
        if local or path in ("/login", "/favicon.svg") or gate.has_valid_cookie(request.cookies.get(COOKIE)):
            return await call_next(request)
        if path.startswith(("/api/", "/media/")):
            return JSONResponse({"detail": "passcode required"}, status_code=401)
        return RedirectResponse(f"{_prefix(request)}/login", status_code=303)

    @app.get("/login", include_in_schema=False)
    def login_form(request: Request):
        local, client = _client(request)
        if local or gate.has_valid_cookie(request.cookies.get(COOKIE)):
            return RedirectResponse(f"{_prefix(request)}/", status_code=303)
        wait = gate.locked_for(client)
        return HTMLResponse(login_page(_prefix(request), f"Too many attempts; try again in {wait // 60 + 1} min." if wait else None))

    @app.post("/login", include_in_schema=False)
    def login_submit(request: Request, passcode: str = Form("")):
        _, client = _client(request)
        prefix = _prefix(request)
        if not gate.check(client, passcode):
            wait = gate.locked_for(client)
            msg = f"Too many attempts; try again in {wait // 60 + 1} min." if wait else "That passcode didn't match."
            return HTMLResponse(login_page(prefix, msg), status_code=401)
        resp = RedirectResponse(f"{prefix}/", status_code=303)
        resp.set_cookie(COOKIE, gate.cookie_value(), max_age=COOKIE_DAYS * 86400, httponly=True, samesite="lax",
                        path=f"{prefix}/" if prefix else "/", secure=request.headers.get("x-forwarded-proto") == "https")
        return resp

    def local_only(request: Request):
        if not _client(request)[0]:
            raise HTTPException(403, "only available on the game PC")

    @app.get("/api/access")
    def access_info(_=Depends(local_only)):
        return {"passcode": gate.passcode(), "lan_addresses": lan_addresses(), "path": "/dcc/", "port": 8787,
                "fixed_by_env": bool(os.environ.get("CRAWLER_PASSCODE"))}

    @app.post("/api/access/regenerate")
    def access_regenerate(_=Depends(local_only)):
        try:
            return {"passcode": gate.regenerate()}
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    def db():
        c = state.conn()
        try:
            yield c
        finally:
            c.close()

    def campaign_or_404(c, cid: int) -> dict:
        camp = one(c, "SELECT id FROM campaigns WHERE id = ?", (cid,))
        if not camp:
            raise HTTPException(404, "campaign not found")
        return camp

    def write_lock():
        if not WRITE_LOCK.acquire(timeout=1.5):
            raise HTTPException(409, "The GM is resolving a turn; try again when it finishes.")
        try:
            yield
        finally:
            WRITE_LOCK.release()

    def gm_auth(x_gm_token: str | None = Header(default=None)):
        if not x_gm_token or not secrets.compare_digest(x_gm_token, settings.gm_token()):
            raise HTTPException(403, "GM token required")

    # ------------------------------------------------------------------ meta / settings
    @app.get("/api/health")
    def health(c=Depends(db)):
        p = state.prefs()
        return {"ok": True, "time": now_iso(), "provider": p["gm_provider"], "codex_cli": resolve_codex_bin(),
                "rulebook_index": settings.rulebook_db.exists(), "campaigns": len(views.list_campaigns(c))}


    @app.get("/api/settings")
    def get_settings():
        p = state.prefs()
        return {**p, "codex_cli_path": resolve_codex_bin(), "providers": ["auto", "codex", "gemini", "mock"], "gemini_key_present": bool(settings.gemini_api_key()),
                "efforts": ["minimal", "low", "medium", "high"], "data_dir": str(settings.data_dir)}

    @app.put("/api/settings")
    def put_settings(body: PrefsIn):
        p = state.prefs()
        upd = body.model_dump(exclude_none=True)
        if "gm_provider" in upd and upd["gm_provider"] not in ("auto", "codex", "gemini", "mock"):
            raise HTTPException(422, "gm_provider must be auto, codex, gemini or mock")
        if "context_profile" in upd and upd["context_profile"] not in ("standard", "large"):
            raise HTTPException(422, "context_profile must be standard or large")
        if "codex_reasoning_effort" in upd and upd["codex_reasoning_effort"] not in ("minimal", "low", "medium", "high"):
            raise HTTPException(422, "invalid reasoning effort")
        p.update(upd)
        state.save_prefs(p)
        return get_settings()

    # ------------------------------------------------------------------ campaigns
    @app.get("/api/campaigns")
    def campaigns(c=Depends(db)):
        return views.list_campaigns(c)

    @app.get("/api/campaigns/{cid}")
    def campaign(cid: int, c=Depends(db)):
        s = views.campaign_summary(c, cid)
        if not s:
            raise HTTPException(404, "campaign not found")
        return s

    @app.post("/api/campaigns/demo")
    def make_demo(c=Depends(db), _=Depends(write_lock)):
        return {"campaign_id": seed_demo(c)}

    @app.get("/api/campaigns-manage")
    def campaigns_manage(c=Depends(db)):
        return campaign_admin.summary(c)

    @app.post("/api/campaigns")
    def campaign_create(body: CampaignIn, c=Depends(db), _=Depends(write_lock)):
        try:
            return {"campaign_id": campaign_admin.create(c, **body.model_dump())}
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.patch("/api/campaigns/{cid}")
    def campaign_edit(cid: int, body: CampaignEditIn, c=Depends(db), _=Depends(write_lock)):
        campaign_or_404(c, cid)
        try:
            if body.name is not None:
                campaign_admin.rename(c, cid, body.name)
            if body.archived is not None:
                campaign_admin.set_archived(c, cid, body.archived)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return {"ok": True}

    @app.post("/api/campaigns/{cid}/delete")
    def campaign_delete(cid: int, body: CampaignDeleteIn, c=Depends(db), _=Depends(write_lock), __=Depends(local_only)):
        try:
            out = campaign_admin.delete(c, settings, cid, body.confirm_name)
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))
        p = state.prefs()
        if p.get("active_campaign_id") == cid:
            p["active_campaign_id"] = None
            state.save_prefs(p)
        return out

    @app.get("/api/campaigns/{cid}/characters/{char_id}/dossier")
    def character_dossier(cid: int, char_id: int, c=Depends(db)):
        from .memory.dossier import build as build_dossier
        from .memory.abilities import attach
        d = build_dossier(c, cid, char_id)
        if not d:
            raise HTTPException(404, "character not found")
        return attach(c, cid, d)

    explaining: dict[int, dict] = {}

    @app.post("/api/campaigns/{cid}/abilities/explain")
    def explain_abilities(cid: int):
        """Write explanations for dossier abilities that have none (template, rulebook, record or homebrew; Codex in the background)."""
        if explaining.get(cid, {}).get("state") == "running":
            return explaining[cid]
        explaining[cid] = {"state": "running", "started_at": now_iso(), "progress": ""}

        def work():
            from .memory.abilities import explain_missing
            conn = state.conn()
            try:
                counts = explain_missing(conn, cid, settings, log=lambda m: explaining[cid].update(progress=m))
                explaining[cid] = {"state": "done", "finished_at": now_iso(), "counts": counts}
            except Exception as exc:  # surfaced in the dossier
                traceback.print_exc()
                explaining[cid] = {"state": "failed", "error": str(exc)[:300]}
            finally:
                conn.close()

        threading.Thread(target=work, daemon=True, name=f"abilities-{cid}").start()
        return explaining[cid]

    @app.get("/api/campaigns/{cid}/abilities/explain")
    def explain_status(cid: int):
        return explaining.get(cid, {"state": "idle"})

    @app.put("/api/campaigns/{cid}/characters/{char_id}/portrait-crop")
    def portrait_crop(cid: int, char_id: int, body: CropIn, c=Depends(db), _=Depends(write_lock)):
        if not one(c, "SELECT id FROM characters WHERE campaign_id = ? AND id = ?", (cid, char_id)):
            raise HTTPException(404, "unknown character")
        crop = body.model_dump() if None not in body.model_dump().values() else None
        c.execute("UPDATE characters SET portrait_crop_json = ? WHERE id = ?", (json.dumps(crop) if crop else None, char_id))
        return {"portrait_crop": crop}

    @app.get("/api/campaigns/{cid}/characters")
    def characters(cid: int, c=Depends(db)):
        campaign_or_404(c, cid)
        return views.list_characters(c, cid)

    @app.get("/api/campaigns/{cid}/characters/{char_id}")
    def character(cid: int, char_id: int, c=Depends(db)):
        s = views.character_sheet(c, cid, char_id)
        if not s:
            raise HTTPException(404, "character not found")
        return s

    @app.post("/api/campaigns/{cid}/characters/{char_id}/portrait")
    async def upload_portrait(cid: int, char_id: int, file: UploadFile = File(...), c=Depends(db), _=Depends(write_lock)):
        ch = one(c, "SELECT slug FROM characters WHERE campaign_id = ? AND id = ?", (cid, char_id))
        if not ch:
            raise HTTPException(404, "character not found")
        ext = Path(file.filename or "").suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            raise HTTPException(422, "portrait must be png, jpg, webp or gif")
        data = await file.read()
        if len(data) > 15 * 1024 * 1024:
            raise HTTPException(413, "portrait too large (15 MB max)")
        name = f"c{cid}-{ch['slug']}-{uuid.uuid4().hex[:6]}{ext}"
        (settings.portraits_dir / name).write_bytes(data)
        c.execute("UPDATE characters SET portrait_path = ?, portrait_crop_json = NULL WHERE id = ?", (name, char_id))
        return {"portrait_path": name}

    @app.get("/api/assets/manifest")
    def asset_manifest():
        """Local artwork map (data/assets/manifest.json): {"locations": {"Gilded Feathers": "file.jpg"}, "characters": {"slug": "file.png"}}.
        Missing art falls back to generated placeholders in the UI; images are never generated per turn."""
        path = settings.data_dir / "assets" / "manifest.json"
        if not path.exists():
            (settings.data_dir / "assets").mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"locations": {}, "characters": {}, "_help": "Put images in this folder and map names to files."}, indent=2), encoding="utf-8")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"locations": {}, "characters": {}, "error": "manifest.json is not valid JSON"}

    def picture(path: Path, w: int | None, cache: str) -> FileResponse:
        """A local picture, or a cached scaled-down copy when ?w= asks for one (app/media.py)."""
        if w:
            from .media import thumbnail
            small, media_type = thumbnail(path, w, settings.data_dir / "thumbs")
            return FileResponse(small, media_type=media_type, headers={"Cache-Control": cache})
        return FileResponse(path, headers={"Cache-Control": cache})

    @app.get("/media/assets/{name}")
    def asset_file(name: str, w: int | None = Query(None, ge=16, le=2048)):
        base = (settings.data_dir / "assets").resolve()
        path = (base / name).resolve()
        if path.parent != base or not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"):
            raise HTTPException(404)
        return picture(path, w, "public, max-age=86400")

    # ------------------------------------------------------------------ art (local ComfyUI)
    @app.post("/api/campaigns/{cid}/visual-reference")
    def import_visual_reference(cid: int, body: VisualReferenceIn, c=Depends(db), _=Depends(write_lock)):
        """Import physical descriptions (## Name / **demographic | age | class** / paragraphs / Signature / Expression) into
        the characters' visual blocks. A save point is taken first, so the import can be undone from Saves."""
        from .importer.visual_reference import apply as apply_reference
        campaign_or_404(c, cid)
        with transaction(c):
            sp = snapshots.create_save_point(c, cid, "Before visual reference import", kind="manual")
            result = apply_reference(c, cid, body.markdown, body.source)
        return {**result, "save_point_id": sp}

    @app.get("/api/campaigns/{cid}/consistency")
    def campaign_consistency(cid: int, c=Depends(db)):
        """Read-only: does each fact have one value across the records, the HUD and the GM briefing? (player-visible values only)"""
        from .consistency import summary
        campaign_or_404(c, cid)
        return summary(c, cid)

    @app.get("/api/campaigns/{cid}/corrections")
    def campaign_corrections(cid: int, c=Depends(db)):
        """Owner-approved record corrections, with their evidence (player-visible: they change visible state)."""
        from .reconcile import corrections
        campaign_or_404(c, cid)
        return corrections(c, cid)

    @app.get("/api/gemini/quota")
    def gemini_quota():
        from .gm.router import GeminiQuota
        return {**GeminiQuota(settings).report(), "key_present": bool(settings.gemini_api_key())}

    @app.post("/api/campaigns/{cid}/art/draft-appearance")
    def art_draft_appearance(cid: int, body: AppearanceIn, c=Depends(db)):
        """Ask Gemini Flash Lite to propose appearance notes for traits the record leaves open. Returned, never saved."""
        from .gm.providers import GeminiProvider, ProviderError
        from .gm.router import GeminiQuota
        campaign_or_404(c, cid)
        if body.kind != "character" or not body.subject_id:
            raise HTTPException(422, "drafts are for characters")
        if not settings.gemini_api_key() or GeminiQuota(settings).remaining("gemini-3.5-flash-lite") < 1:
            raise HTTPException(409, "No Gemini key or Flash Lite quota left today")
        ch = one(c, "SELECT * FROM characters WHERE campaign_id = ? AND id = ?", (cid, body.subject_id))
        if not ch:
            raise HTTPException(404, "unknown character")
        brief = art.character_brief(c, cid, ch)
        record = json.dumps({"name": ch["name"], "race": ch.get("race"), "visual": (ch.get("sheet") or {}).get("visual"),
                             "public_persona": ((ch.get("sheet") or {}).get("narrative") or {}).get("personality_summary")}, ensure_ascii=False)
        system = ("You help a tabletop player keep character portraits consistent. Propose a short physical description (under 45 "
                  "words, comma-separated) covering only the traits the record leaves open: " + (", ".join(brief["unestablished"]) or
                  "age, face, hair, build") + ". Stay consistent with everything the record does say. Plain visual words, no backstory.")
        schema = {"type": "object", "additionalProperties": False, "required": ["appearance"], "properties": {"appearance": {"type": "string"}}}
        try:
            raw, _ = GeminiProvider(settings, model="gemini-3.5-flash-lite", effort="low")._generate(system, record, schema, "appearance")
        except ProviderError as exc:
            raise HTTPException(502, str(exc))
        return {"appearance": json.loads(raw)["appearance"].strip()}

    @app.post("/api/campaigns/{cid}/art/draft-item-looks")
    def art_draft_item_looks(cid: int, body: ItemLooksIn, c=Depends(db)):
        """Gemini drafts a physical look for items without one; saved as their Appearance notes (art only) for review."""
        campaign_or_404(c, cid)
        try:
            return art.draft_item_looks(c, settings, cid, body.item_ids, body.overwrite)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/campaigns/{cid}/art/draft-place-looks")
    def art_draft_place_looks(cid: int, body: PlaceLooksIn, c=Depends(db)):
        """Gemini drafts a look (and a don't-draw list) for each known place; saved as Appearance notes (art only) for review."""
        campaign_or_404(c, cid)
        try:
            return art.draft_place_looks(c, settings, cid, body.keys, body.overwrite)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/campaigns/{cid}/art/appearances")
    def art_appearances(cid: int, kind: str, c=Depends(db)):
        campaign_or_404(c, cid)
        return art.appearances(c, cid, kind)

    @app.get("/api/art/status")
    def art_status():
        return state.art.status()

    @app.get("/api/campaigns/{cid}/art")
    def art_list(cid: int, kind: str | None = None, subject_id: int | None = None, subject_name: str | None = None, c=Depends(db)):
        campaign_or_404(c, cid)
        sql, args = "SELECT * FROM art_jobs WHERE campaign_id = ? AND status != 'dismissed'", [cid]
        if kind:
            sql += " AND kind = ?"; args.append(kind)
        if subject_id is not None:
            sql += " AND subject_id = ?"; args.append(subject_id)
        if subject_name:
            sql += " AND lower(subject_name) = lower(?)"; args.append(subject_name)
        return [art.job_view(j) for j in rows(c, sql + " ORDER BY id DESC LIMIT 200", tuple(args))]

    @app.get("/api/campaigns/{cid}/art/preview")
    def art_preview(cid: int, kind: str, subject_id: int | None = None, subject_name: str | None = None, engine: str | None = None, c=Depends(db)):
        campaign_or_404(c, cid)
        try:
            name, desc = art.subject_text(c, cid, kind, subject_id, subject_name)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        key = art._named_key(kind, subject_name or name) if kind in art.NAMED else str(subject_id or "")
        unestablished = []
        if kind == "character":
            ch = one(c, "SELECT * FROM characters WHERE campaign_id = ? AND (id = ? OR lower(name) = lower(?))", (cid, subject_id or -1, subject_name or ""))
            key, unestablished = str(ch["id"]), art.character_brief(c, cid, ch)["unestablished"]
        style, size = art.subject_layout(c, cid, kind, int(key) if kind == "character" and key.isdigit() else None)
        eng = art.engine_for(settings, kind, engine)
        prompt = art.codex_prompt(c, settings, cid, int(key)) if eng == "codex" else art.build_prompt(kind, desc, style=style)
        return {"subject_name": name, "prompt": prompt, "engine": eng, "negative": art.NEGATIVE, "size": size,
                "appearance": art.appearance(c, cid, kind, key) if key else "", "avoid": art.avoid(c, cid, kind, key) if key else "",
                "features": art.features(c, cid, kind, key) if key else "",
                "unestablished": unestablished}

    @app.put("/api/campaigns/{cid}/art/appearance")
    def art_set_appearance(cid: int, body: AppearanceIn, c=Depends(db)):
        campaign_or_404(c, cid)
        if body.kind not in art.KINDS:
            raise HTTPException(422, "unknown kind")
        key = art._named_key(body.kind, body.subject_name or "") if body.kind in art.NAMED else str(body.subject_id or "")
        if not key:
            raise HTTPException(422, "subject_id (or subject_name for a Mob or a place) is required")
        art.set_appearance(c, cid, body.kind, key, body.appearance)
        if body.avoid is not None:
            art.set_avoid(c, cid, body.kind, key, body.avoid)
        if body.features is not None:
            art.set_features(c, cid, body.kind, key, body.features)
        return {"ok": True}

    @app.post("/api/campaigns/{cid}/art")
    def art_create(cid: int, body: ArtIn, c=Depends(db)):
        campaign_or_404(c, cid)
        try:
            job = art.create_job(c, settings, cid, body.kind, subject_id=body.subject_id, subject_name=body.subject_name,
                                 prompt=body.prompt, extra=body.extra, engine=body.engine)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        state.art.poke()
        return job

    @app.post("/api/art/{job_id}/{action}")
    def art_action(job_id: int, action: str, c=Depends(db)):
        j = one(c, "SELECT * FROM art_jobs WHERE id = ?", (job_id,))
        if not j:
            raise HTTPException(404, "no such picture")
        if action == "approve" and j["status"] == "suggested":
            c.execute("UPDATE art_jobs SET status = 'queued' WHERE id = ?", (job_id,))
        elif action == "dismiss" and j["status"] in ("suggested", "failed", "done", "queued"):
            c.execute("UPDATE art_jobs SET status = 'dismissed', is_current = 0 WHERE id = ?", (job_id,))
        elif action == "retry" and j["status"] in ("failed", "done"):
            new = art.create_job(c, settings, j["campaign_id"], j["kind"], subject_id=j["subject_id"], subject_name=j["subject_name"],
                                 prompt=j["prompt"])
            state.art.poke()
            return new
        elif action == "use" and j["status"] == "done":
            if not WRITE_LOCK.acquire(timeout=1.5):
                raise HTTPException(409, "The GM is resolving a turn; try again when it finishes.")
            try:
                with transaction(c):
                    return art.make_current(c, settings, job_id)
            finally:
                WRITE_LOCK.release()
        else:
            raise HTTPException(422, f"can't {action} a picture that is {j['status']}")
        state.art.poke()
        return art.job_view(one(c, "SELECT * FROM art_jobs WHERE id = ?", (job_id,)))

    # reference photos for Codex portraits (a real person's face); kept under data/art/c<cid>/refs/, never sent to ComfyUI
    @app.get("/api/campaigns/{cid}/characters/{char_id}/art-refs")
    def art_refs(cid: int, char_id: int, c=Depends(db)):
        campaign_or_404(c, cid)
        return [{"name": p.name, "url": f"media/art-refs/{cid}/{char_id}/{p.name}"} for p in art.list_refs(settings, cid, char_id)]

    @app.post("/api/campaigns/{cid}/characters/{char_id}/art-refs")
    async def art_ref_upload(cid: int, char_id: int, file: UploadFile = File(...), c=Depends(db)):
        campaign_or_404(c, cid)
        if not one(c, "SELECT id FROM characters WHERE campaign_id = ? AND id = ?", (cid, char_id)):
            raise HTTPException(404, "unknown character")
        ext = Path(file.filename or "").suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp"):
            raise HTTPException(422, "reference photos must be png, jpg or webp")
        data = await file.read()
        if len(data) > 15 * 1024 * 1024:
            raise HTTPException(413, "photo too large (15 MB max)")
        if len(art.list_refs(settings, cid, char_id)) >= 4:
            raise HTTPException(409, "at most 4 reference photos per character")
        folder = art.refs_dir(settings, cid, char_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{uuid.uuid4().hex[:12]}{ext}").write_bytes(data)
        return art_refs(cid, char_id, c)

    @app.delete("/api/campaigns/{cid}/characters/{char_id}/art-refs/{name}")
    def art_ref_delete(cid: int, char_id: int, name: str, c=Depends(db)):
        folder = art.refs_dir(settings, cid, char_id).resolve()
        path = (folder / name).resolve()
        if path.parent != folder or not path.is_file():
            raise HTTPException(404, "no such photo")
        path.unlink()
        return art_refs(cid, char_id, c)

    @app.get("/media/art-refs/{cid}/{char_id}/{name}")
    def art_ref_file(cid: int, char_id: int, name: str, w: int | None = Query(None, ge=16, le=2048)):
        folder = art.refs_dir(settings, cid, char_id).resolve()
        path = (folder / name).resolve()
        if path.parent != folder or not path.is_file():
            raise HTTPException(404, "no such photo")
        return picture(path, w, "private, max-age=3600")

    @app.get("/media/art/{cid}/{name}")
    def art_file(cid: int, name: str, w: int | None = Query(None, ge=16, le=2048)):
        folder = art.art_dir(settings, cid).resolve()
        path = (folder / name).resolve()
        if path.parent != folder or not path.is_file():
            raise HTTPException(404, "no such picture")
        return picture(path, w, "public, max-age=31536000, immutable")

    @app.get("/media/portraits/{name}")
    def portrait(name: str, w: int | None = Query(None, ge=16, le=2048)):
        path = (settings.portraits_dir / name).resolve()
        if path.parent != settings.portraits_dir.resolve() or not path.is_file():
            raise HTTPException(404)
        return picture(path, w, "public, max-age=86400")  # uploaded and generated portraits get new file names

    for route, fn in {"inventory": views.inventory, "quests": views.quests, "npcs": views.npcs, "timeline": views.timeline,
                      "rolls": views.player_rolls, "house-rules": views.house_rules, "rulings": rulecenter.rulings, "lore": views.lore,
                      "journal": journal.journal, "lore-notebook": journal.lore_notebook, "chronology": journal.chronology,
                      "places": places.known_world}.items():
        def make(fn=fn):
            def handler(cid: int, c=Depends(db)):
                campaign_or_404(c, cid)
                return fn(c, cid)
            return handler
        app.get(f"/api/campaigns/{{cid}}/{route}")(make())

    @app.get("/api/campaigns/{cid}/messages")
    def messages(cid: int, before: int | None = None, limit: int = Query(80, le=300), c=Depends(db)):
        campaign_or_404(c, cid)
        return views.messages(c, cid, before, limit)

    @app.get("/api/campaigns/{cid}/search")
    def search(cid: int, q: str = Query(..., min_length=2, max_length=200), c=Depends(db)):
        """In-game search over player-known story, people, quests, lore, items, events and records (never GM text)."""
        campaign_or_404(c, cid)
        return journal.search(c, cid, q)

    @app.get("/api/campaigns/{cid}/records")
    def records(cid: int, q: str | None = None, c=Depends(db)):
        campaign_or_404(c, cid)
        return views.player_records(c, cid, q)

    @app.get("/api/campaigns/{cid}/records/{sid}")
    def record(cid: int, sid: int, c=Depends(db)):
        r = views.player_record(c, cid, sid)
        if not r:
            raise HTTPException(404, "record not found")
        return r

    # ------------------------------------------------------------------ turns

    @app.post("/api/campaigns/{cid}/turns")
    def submit_turn(cid: int, body: TurnIn):
        try:
            return state.turns.submit(cid, body.text, body.idempotency_key)
        except BusyError as exc:
            raise HTTPException(409, str(exc))
        except KeyError:
            raise HTTPException(404, "campaign not found")
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.get("/api/turns/{turn_id}")
    def get_turn(turn_id: int):
        try:
            return state.turns.get(turn_id)
        except KeyError:
            raise HTTPException(404, "turn not found")

    @app.get("/api/campaigns/{cid}/turns/active")
    def active_turn(cid: int, c=Depends(db)):
        t = one(c, "SELECT * FROM turns WHERE campaign_id = ? AND status = 'processing' ORDER BY id DESC LIMIT 1", (cid,))
        return state.turns.describe(t) if t else None

    # ------------------------------------------------------------------ saves
    # ------------------------------------------------------------------ backups, restore preview, problem reports (M9)
    @app.get("/api/backups")
    def backups_list(_=Depends(local_only)):
        from . import support
        return {"backups": support.list_backups(settings), "newest_age_s": support.newest_age_s(settings)}

    @app.post("/api/backups")
    def backups_create(body: BackupIn, _=Depends(local_only)):
        from . import backup
        made = backup.create(settings, body.label or "manual", media=body.media)
        return {"name": made.name, "verify": backup.verify(made)}

    @app.post("/api/backups/{name}/verify")
    def backups_verify(name: str, _=Depends(local_only)):
        from . import backup
        root = (settings.data_dir / "backups").resolve()
        folder = (root / name).resolve()
        if folder.parent != root or not (folder / "manifest.json").is_file():
            raise HTTPException(404, "no verifiable backup by that name")
        return backup.verify(folder)

    @app.get("/api/campaigns/{cid}/saves/{save_id}/preview")
    def save_preview(cid: int, save_id: int, c=Depends(db)):
        """What restoring this save point would change, in player terms; nothing is changed."""
        from . import support
        campaign_or_404(c, cid)
        try:
            return support.restore_preview(c, cid, save_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc))

    @app.post("/api/campaigns/{cid}/reports")
    def problem_report(cid: int, body: ReportIn, c=Depends(db)):
        """A redacted report of one turn (player-visible exchange, timing, tool names, tokens, flags) saved to data/reports."""
        from . import support
        campaign_or_404(c, cid)
        try:
            return support.problem_report(c, settings, cid, body.turn_id, body.note)
        except KeyError as exc:
            raise HTTPException(404, str(exc))

    @app.get("/api/campaigns/{cid}/saves")
    def saves(cid: int, c=Depends(db)):
        campaign_or_404(c, cid)
        return snapshots.list_save_points(c, cid)


    @app.post("/api/campaigns/{cid}/saves")
    def create_save(cid: int, body: SaveIn, c=Depends(db), _=Depends(write_lock)):
        campaign_or_404(c, cid)
        return {"save_point_id": snapshots.create_save_point(c, cid, body.label, kind="manual")}

    @app.post("/api/campaigns/{cid}/saves/{sid}/restore")
    def restore(cid: int, sid: int, c=Depends(db), _=Depends(write_lock)):
        try:
            return snapshots.restore_save_point(c, cid, sid)
        except KeyError as exc:
            raise HTTPException(404, str(exc))

    # ------------------------------------------------------------------ rulebooks
    def rule_conn():
        if not settings.rulebook_db.exists():
            return None
        return rulebooks.open_db(settings.rulebook_db)

    @app.get("/api/rules/documents")
    def rule_documents():
        rc = rule_conn()
        if rc is None:
            return {"documents": [], "index_status": state.index_status}
        try:
            return {"documents": rulebooks.list_documents(rc), "index_status": state.index_status}
        finally:
            rc.close()

    @app.get("/api/rules/search")
    def rule_search(q: str = Query(min_length=2, max_length=300), document_id: int | None = None, cid: int | None = None, c=Depends(db)):
        out = {"campaign": [], "official": []}
        if cid:
            terms = [t.strip('"') for t in views.fts_query(q).split(" OR ")]
            for h in views.house_rules(c, cid):
                score = sum(t in (h["title"] + " " + h["text"]).lower() for t in terms)
                if score >= max(1, min(2, len(terms))):
                    out["campaign"].append({**h, "score": score, "text": h["text"][:900]})
            out["campaign"] = sorted(out["campaign"], key=lambda h: -h["score"])[:5]
        rc = rule_conn()
        if rc is not None:
            try:
                for p in rulebooks.search(rc, q, limit=10, document_id=document_id):
                    doc_id = rc.execute("SELECT document_id FROM chunks WHERE id = ?", (p.chunk_id,)).fetchone()[0]
                    out["official"].append({"chunk_id": p.chunk_id, "document_id": doc_id, "document": p.document, "page": p.page,
                                            "ref": p.ref(), "text": p.text, "score": p.score})
            finally:
                rc.close()
        return out

    def rules_index(rc):
        """The rules-center index, rebuilt when the rulebook index file changes."""
        f = settings.rulebook_db
        return rulecenter.index(rc, f"{f}:{f.stat().st_mtime_ns}")

    @app.get("/api/rules/browse")
    def rules_browse(book_id: int | None = None):
        rc = rule_conn()
        if rc is None:
            return {"books": []} if book_id is None else {"book": None}
        try:
            return rulecenter.browse(rules_index(rc), book_id)
        finally:
            rc.close()

    @app.get("/api/rules/suggest")
    def rules_suggest(q: str = Query(min_length=1, max_length=100), cid: int | None = None, c=Depends(db)):
        rc = rule_conn()
        try:
            return rulecenter.suggest(rules_index(rc) if rc else None, c, cid, q)
        finally:
            if rc:
                rc.close()

    @app.get("/api/rules/lookup")
    def rules_lookup(q: str = Query(min_length=2, max_length=200), cid: int | None = None, c=Depends(db)):
        """Where a rule is written, what overrides it, which rulings stand on it, and whether the console enforces it."""
        rc = rule_conn()
        try:
            return rulecenter.lookup(rules_index(rc) if rc else None, c, cid, q)
        finally:
            if rc:
                rc.close()

    @app.get("/api/rules/cite")
    def rules_cite(ref: str = Query(min_length=3, max_length=300)):
        rc = rule_conn()
        if rc is None:
            raise HTTPException(404, "no rulebook index")
        try:
            out = rulecenter.cite(rules_index(rc), ref)
        finally:
            rc.close()
        if not out:
            raise HTTPException(404, "citation not found in the index")
        return out

    @app.post("/api/campaigns/{cid}/rulings/{rid}/review")
    def review_ruling(cid: int, rid: int, body: ReviewIn, c=Depends(db)):
        """The owner's review of a GM ruling: approved rulings become precedent in later turns; rejected ones are dropped."""
        campaign_or_404(c, cid)
        if not one(c, "SELECT id FROM rulings WHERE campaign_id = ? AND id = ?", (cid, rid)):
            raise HTTPException(404, "ruling not found")
        review = {"approve": "approved", "reject": "rejected", "reopen": None}[body.decision]
        with transaction(c):
            c.execute("UPDATE rulings SET review = ?, review_note = ?, reviewed_at = ? WHERE id = ?",
                      (review, body.note, now_iso() if review else None, rid))
        return next(r for r in rulecenter.rulings(c, cid) if r["id"] == rid)

    @app.get("/api/rules/page")
    def rule_page(document_id: int, page: int):
        rc = rule_conn()
        if rc is None:
            raise HTTPException(404, "no rulebook index")
        try:
            doc = rc.execute("SELECT title, page_count FROM documents WHERE id = ?", (document_id,)).fetchone()
            if not doc:
                raise HTTPException(404, "document not found")
            return {"document": doc["title"], "page": page, "page_count": doc["page_count"], "text": rulebooks.page_text(rc, document_id, page)}
        finally:
            rc.close()


    @app.post("/api/rules/index")
    def rule_index(body: IndexIn):
        directory = body.directory or state.prefs().get("rulebook_dir")
        if not directory or not Path(directory).is_dir():
            raise HTTPException(422, "rulebook directory not found; set it in Settings")
        if state.index_status.get("state") == "running":
            raise HTTPException(409, "indexing already running")
        p = state.prefs()
        p["rulebook_dir"] = directory
        state.save_prefs(p)
        _start_index(state, Path(directory))
        return state.index_status

    # ------------------------------------------------------------------ import

    @app.post("/api/import")
    def run_import(body: ImportIn, c=Depends(db), _=Depends(write_lock)):
        path = Path(body.path or state.prefs()["ledger_dir"])
        if not path.is_dir():
            raise HTTPException(422, f"ledger directory not found: {path}")
        cid, report = import_ledger(c, path)
        _write_report(report)
        return {"campaign_id": cid, "report": player_safe_report(report)}

    @app.get("/api/import/reports")
    def import_reports(c=Depends(db)):
        out = []
        for r in rows(c, "SELECT id, campaign_id, source_path, report_json, created_at FROM import_reports ORDER BY id DESC"):
            from .reconcile import annotate_report
            out.append({"id": r["id"], "campaign_id": r["campaign_id"], "source_path": r["source_path"], "created_at": r["created_at"],
                        "report": annotate_report(c, r["campaign_id"], player_safe_report(r["report"]))})
        return out

    # ------------------------------------------------------------------ ledger write-back
    def ledger_call(fn):
        try:
            return fn()
        except LedgerError as exc:
            raise HTTPException(409, str(exc))

    def public_preview(pv: dict) -> dict:
        pv = dict(pv)
        pv.pop("_changes", None)
        return pv

    @app.get("/api/campaigns/{cid}/ledger/preview")
    def ledger_preview(cid: int, fetch: bool = True, c=Depends(db)):
        campaign_or_404(c, cid)
        return public_preview(ledger_call(lambda: ledger_sync.preview(c, cid, fetch=fetch, redact=True)))

    @app.post("/api/campaigns/{cid}/ledger/apply")
    def ledger_apply(cid: int, body: LedgerApplyIn, c=Depends(db), _=Depends(write_lock)):
        campaign_or_404(c, cid)
        return ledger_call(lambda: ledger_sync.apply(c, cid, fingerprint=body.fingerprint, selected=body.selected, message=body.message,
                                                     author_name=body.author_name, author_email=body.author_email,
                                                     regenerate_checkpoint=body.regenerate_checkpoint))

    @app.post("/api/campaigns/{cid}/ledger/pull")
    def ledger_pull(cid: int, c=Depends(db), _=Depends(write_lock)):
        return ledger_call(lambda: ledger_sync.pull(c, cid))

    @app.post("/api/campaigns/{cid}/ledger/push")
    def ledger_push(cid: int, c=Depends(db), _=Depends(write_lock)):
        return ledger_call(lambda: ledger_sync.push(c, cid))

    @app.get("/api/campaigns/{cid}/ledger/history")
    def ledger_history(cid: int, c=Depends(db)):
        campaign_or_404(c, cid)
        return ledger_sync.history(c, cid)

    @app.get("/api/gm/campaigns/{cid}/ledger/preview")
    def gm_ledger_preview(cid: int, c=Depends(db), _=Depends(gm_auth)):
        return public_preview(ledger_call(lambda: ledger_sync.preview(c, cid, fetch=False, redact=False)))

    # ------------------------------------------------------------------ GM only
    gm = {"secrets": views.gm_secrets, "messages": views.gm_messages, "events": views.gm_events, "rolls": views.gm_rolls,
          "lore": views.gm_lore, "turns": views.gm_turns}
    for route, fn in gm.items():
        def make_gm(fn=fn):
            def handler(cid: int, c=Depends(db), _=Depends(gm_auth)):
                campaign_or_404(c, cid)
                return fn(c, cid)
            return handler
        app.get(f"/api/gm/campaigns/{{cid}}/{route}")(make_gm())

    @app.get("/api/gm/campaigns/{cid}/diagnostics")
    def gm_diagnostics_list(cid: int, c=Depends(db), _=Depends(gm_auth)):
        out = []
        for r in rows(c, """SELECT d.turn_id, d.usage_json, d.integrity_json, d.created_at, t.status, substr(t.player_input, 1, 120) AS input
                            FROM turn_diagnostics d JOIN turns t ON t.id = d.turn_id WHERE d.campaign_id = ? ORDER BY d.turn_id DESC LIMIT 30""", (cid,)):
            rounds = (r["usage"] or {}).get("rounds") or []
            out.append({"turn_id": r["turn_id"], "status": r["status"], "input": r["input"], "created_at": r["created_at"],
                        "rounds": len(rounds), "flags": len(r["integrity"] or []),
                        "input_tokens": sum(((x.get("usage") or {}).get("input_tokens") or 0) for x in rounds)})
        return out

    @app.get("/api/gm/turns/{turn_id}/diagnostics")
    def gm_diagnostics(turn_id: int, c=Depends(db), _=Depends(gm_auth)):
        from .gm.tools import TOOL_NAMES
        from .rules.engine import CHANGE_OPS
        d = one(c, "SELECT * FROM turn_diagnostics WHERE turn_id = ?", (turn_id,))
        if not d:
            raise HTTPException(404, "no diagnostics for this turn")
        t = one(c, "SELECT id, status, player_input, error, provider, created_at, completed_at FROM turns WHERE id = ?", (turn_id,))
        rolls_ = rows(c, "SELECT id, expression, dice_json, total, target, outcome, degree, reason, hidden, rng FROM dice_rolls WHERE turn_id = ? ORDER BY id", (turn_id,))
        events_ = rows(c, "SELECT id, type, summary, visibility, rolled_back FROM events WHERE turn_id = ? ORDER BY id", (turn_id,))
        return {"turn": t, "context_text": d["context_text"], "steps": d["steps"], "usage": d["usage"], "integrity": d["integrity"],
                "rolls": rolls_, "events": events_, "available_tools": TOOL_NAMES, "available_ops": sorted(CHANGE_OPS),
                "context_chars": len(d["context_text"])}

    @app.get("/api/gm/runtime")
    def gm_runtime(_=Depends(gm_auth)):
        """GM runtime isolation: the last probe report and whether full-prompt capture is on."""
        probe = settings.data_dir / "diagnostics" / "isolation_probe.json"
        cap = {}
        try:
            cap = json.loads(settings.prompt_capture_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        return {"probe": json.loads(probe.read_text(encoding="utf-8")) if probe.exists() else None,
                "prompt_capture": {"active": settings.prompt_capture_active(), "until": cap.get("until")}}

    @app.post("/api/gm/runtime/prompt-capture")
    def gm_prompt_capture(body: dict, _=Depends(gm_auth)):
        """Development only: keep complete GM prompts (they contain GM secrets) for at most 60 minutes."""
        minutes = max(0, min(int(body.get("minutes", 0)), 60))
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.prompt_capture_file.write_text(json.dumps({"until": time.time() + minutes * 60 if minutes else 0}), encoding="utf-8")
        if not minutes:
            prune_jobs(settings)
        return {"active": settings.prompt_capture_active(), "minutes": minutes}

    @app.get("/api/gm/import/reports")
    def gm_import_reports(c=Depends(db), _=Depends(gm_auth)):
        return rows(c, "SELECT * FROM import_reports ORDER BY id DESC")

    # ------------------------------------------------------------------ frontend
    if FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith(("api/", "media/")):
                raise HTTPException(404)
            f = (FRONTEND_DIST / path).resolve()
            if path and f.is_file() and FRONTEND_DIST.resolve() in f.parents:
                return FileResponse(f)
            # revalidate the page so a rebuilt frontend loads at once (its hashed assets stay cacheable)
            return FileResponse(FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-cache"})
    else:
        @app.get("/", include_in_schema=False)
        def no_frontend():
            return JSONResponse({"message": "Frontend not built. Run build-frontend.cmd, or use the API at /docs."})

    return app


def player_safe_report(report: dict) -> dict:
    """Drop entries that concern GM-only files; keep counts."""
    r = dict(report)
    r["manual_review"] = [m for m in report.get("manual_review", []) if not str(m.get("record", "")).startswith("gm_state")]
    r["conflicts"] = [x for x in report.get("conflicts", []) if "gm_state" not in json.dumps(x)]
    return r


def _write_report(report: dict) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / "migration_report.md").write_text(report_markdown(report), encoding="utf-8")
    (REPORTS_DIR / "migration_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def _start_index(state: AppState, directory: Path) -> None:
    state.index_status = {"state": "running", "directory": str(directory), "started_at": now_iso()}

    def work():
        try:
            rc = rulebooks.open_db(state.settings.rulebook_db)
            result = rulebooks.ingest_directory(rc, directory)
            rc.close()
            state.index_status = {"state": "done", "finished_at": now_iso(), **result}
        except Exception as exc:  # surfaced in the UI
            traceback.print_exc()
            state.index_status = {"state": "failed", "error": str(exc)}

    threading.Thread(target=work, daemon=True, name="rulebook-index").start()


def _quietly(fn, *args) -> None:
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 - a background chore must never take the server down
        traceback.print_exc()


def _bootstrap(state: AppState, c) -> None:
    """First run: seed the demo campaign, import the ledger if present, index rulebooks if configured."""
    if not views.list_campaigns(c):
        seed_demo(c)
        ledger = Path(state.prefs()["ledger_dir"])
        if (ledger / "state" / "campaign.yaml").exists():
            cid, report = import_ledger(c, ledger)
            _write_report(report)
            if cid:
                p = state.prefs()
                p["active_campaign_id"] = cid
                state.save_prefs(p)
    rb_dir = state.prefs().get("rulebook_dir")
    if rb_dir and Path(rb_dir).is_dir() and not state.settings.rulebook_db.exists():
        _start_index(state, Path(rb_dir))
    # a database-only backup when the newest is over a day old (support.py), off the request path
    from .support import auto_backup
    threading.Thread(target=lambda: _quietly(auto_backup, state.settings), daemon=True, name="auto-backup").start()
    # small copies of portraits and art for the roster, inventory and story (cached on disk; existing ones are skipped)
    from .media import warm
    s = state.settings
    folders = [s.portraits_dir, s.data_dir / "assets", *sorted((s.data_dir / "art").glob("c*"))]
    threading.Thread(target=warm, args=(folders, s.data_dir / "thumbs"), daemon=True, name="thumbnails").start()


app = create_app()
