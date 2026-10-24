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
from .db import connect, init_db, now_iso, one, rows
from .gm.orchestrator import WRITE_LOCK, BusyError, TurnService, recover_interrupted_turns
from .gm.providers import make_provider, prune_jobs, secure_dir
from .importer.ledger import import_ledger, refresh_mechanical_status, report_markdown
from .ledger import sync as ledger_sync
from .ledger.sync import LedgerError
from .memory import snapshots, views
from .reference import rulebooks
from .seed.demo import seed_demo

DEFAULT_LEDGER = ROOT_DIR / "campaign_sources" / "dccrepository"
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"
REPORTS_DIR = ROOT_DIR / "reports"


class PrefsIn(BaseModel):
    gm_provider: str | None = None
    codex_model: str | None = None
    gemini_model: str | None = None
    codex_reasoning_effort: str | None = None
    rulebook_dir: str | None = None
    ledger_dir: str | None = None
    active_campaign_id: int | None = None


class TurnIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    idempotency_key: str = Field(min_length=8, max_length=80)


class SaveIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)


class IndexIn(BaseModel):
    directory: str | None = None


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

    # Preferences live in a small JSON file (not the DB) so they can change while a turn holds the DB write lock.
    def prefs(self) -> dict:
        base = {"gm_provider": self.settings.gm_provider, "codex_model": self.settings.codex_model, "gemini_model": self.settings.gemini_model,
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
        model = p.get("gemini_model") if p["gm_provider"] == "gemini" else p.get("codex_model")
        return make_provider(p["gm_provider"], self.settings, model=model or "", effort=p.get("codex_reasoning_effort"))

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
        secure_dir(settings.codex_jobs_dir)
        prune_jobs(settings)  # temporary GM job folders (full prompts) never outlive a run unless capture is on
        from .importer.organization import backfill
        for line in backfill(c):
            print(f"[schema v2 backfill] {line}")
        if bootstrap:
            _bootstrap(state, c)
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
        return {**p, "codex_cli_path": resolve_codex_bin(), "providers": ["codex", "gemini", "mock"], "gemini_key_present": bool(settings.gemini_api_key()),
                "efforts": ["minimal", "low", "medium", "high"], "data_dir": str(settings.data_dir)}

    @app.put("/api/settings")
    def put_settings(body: PrefsIn):
        p = state.prefs()
        upd = body.model_dump(exclude_none=True)
        if "gm_provider" in upd and upd["gm_provider"] not in ("codex", "gemini", "mock"):
            raise HTTPException(422, "gm_provider must be codex, gemini or mock")
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
        c.execute("UPDATE characters SET portrait_path = ? WHERE id = ?", (name, char_id))
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

    @app.get("/media/assets/{name}")
    def asset_file(name: str):
        base = (settings.data_dir / "assets").resolve()
        path = (base / name).resolve()
        if path.parent != base or not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"):
            raise HTTPException(404)
        return FileResponse(path)

    @app.get("/media/portraits/{name}")
    def portrait(name: str):
        path = (settings.portraits_dir / name).resolve()
        if path.parent != settings.portraits_dir.resolve() or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    for route, fn in {"inventory": views.inventory, "quests": views.quests, "npcs": views.npcs, "timeline": views.timeline,
                      "rolls": views.player_rolls, "house-rules": views.house_rules, "rulings": views.rulings, "lore": views.lore}.items():
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
            out.append({"id": r["id"], "campaign_id": r["campaign_id"], "source_path": r["source_path"], "created_at": r["created_at"],
                        "report": player_safe_report(r["report"])})
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
            return FileResponse(FRONTEND_DIST / "index.html")
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


app = create_app()
