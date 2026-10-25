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

KINDS = ("character", "item", "mob", "scene")
SIZES = {"character": (832, 1216), "mob": (896, 1152), "item": (1024, 1024), "scene": (1344, 768)}
STYLE = ("gritty painted fantasy illustration, tabletop RPG sourcebook art, oil painting texture, visible brushwork, "
         "dramatic chiaroscuro lighting, grimy dungeon atmosphere, darkly humorous, highly detailed")
KIND_STYLE = {
    "character": "character portrait, waist-up, facing the viewer, expressive face, plain dark background",
    "mob": "monster illustration, full body, menacing pose, dungeon background",
    "item": "single item illustration, centered object on a worn dark wooden table, no people",
    "scene": "environment illustration, wide establishing shot, no people, no characters",
}
NEGATIVE = ("photo, photograph, photorealistic, 3d render, anime, manga, cartoon, chibi, blurry, lowres, jpeg artifacts, watermark, "
            "text, letters, signature, logo, frame, border, extra limbs, extra fingers, deformed hands, disfigured face, cropped head, "
            "nsfw, nude")
KIND_NEGATIVE = {"scene": "people, person, man, woman, figures, crowd, portrait", "item": "people, person, hands, face"}
MAX_PROMPT = 900


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


def subject_text(conn: sqlite3.Connection, cid: int, kind: str, subject_id: int | None, subject_name: str | None) -> tuple[str, str]:
    """(display name, visual description) from player-visible records only."""
    if kind == "character":
        c = one(conn, "SELECT * FROM characters WHERE campaign_id = ? AND id = ?", (cid, subject_id)) if subject_id else \
            one(conn, "SELECT * FROM characters WHERE campaign_id = ? AND lower(name) = lower(?)", (cid, subject_name or ""))
        if not c:
            raise ValueError("unknown character")
        sheet = c.get("sheet") or {}
        vis = sheet.get("visual") or {}
        others = [o["name"].split()[0] for o in rows(conn, "SELECT name FROM characters WHERE campaign_id = ? AND id != ?", (cid, c["id"]))]

        def drawable(line: str) -> bool:  # record-keeping notes and comparisons with other characters confuse the model
            return not re.search(r"\b(recorded|currently|current condition|no current|uninjured|rested)\b", line, re.I) \
                and not any(re.search(rf"\b{re.escape(n)}\b", line) for n in others if len(n) > 2)
        bits = [" ".join(x for x in (c.get("race") or c.get("species"), c.get("class_name")) if x)]
        bits.append(_clean(vis.get("summary")))
        bits += [f for f in _list(vis.get("established_features")) if drawable(f)][:4]
        gear = _list(vis.get("signature_gear"))
        if gear:
            bits.append("signature gear: " + ", ".join(gear[:4]))
        bits += [n for n in _positive_notes(_list(vis.get("illustration_notes"))) if drawable(n)][:3]
        if not vis:
            narrative = sheet.get("narrative") or {}
            bits.append(_clean(narrative.get("current_role")))
        return c["name"], "; ".join(b for b in bits if b)
    if kind == "item":
        i = one(conn, "SELECT * FROM items WHERE campaign_id = ? AND id = ?", (cid, subject_id)) if subject_id else \
            one(conn, "SELECT * FROM items WHERE campaign_id = ? AND lower(name) = lower(?)", (cid, subject_name or ""))
        if not i:
            raise ValueError("unknown item")
        props = i.get("properties") or {}
        bits = [i["name"], _clean(i.get("description"))] + [str(e) for e in _list(props.get("effects"))[:2]]
        return i["name"], "; ".join(b for b in bits if b)
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
        return name, name
    raise ValueError(f"kind must be one of {', '.join(KINDS)}")


def build_prompt(kind: str, description: str, extra: str | None = None) -> str:
    body = "; ".join(x for x in (_clean(extra), description) if x)
    return f"{STYLE}, {KIND_STYLE[kind]}, {body}"[:MAX_PROMPT]


def _shingles(text: str, n: int = 6) -> set[str]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def leaks_secret(conn: sqlite3.Connection, cid: int, text: str) -> bool:
    secrets = " ".join(r["content"] for r in rows(conn, "SELECT content FROM gm_secrets WHERE campaign_id = ? AND revealed = 0", (cid,)))
    return bool(_shingles(text) & _shingles(secrets))


# ------------------------------------------------------------------ jobs
def create_job(conn: sqlite3.Connection, settings: Settings, cid: int, kind: str, *, subject_id: int | None = None,
               subject_name: str | None = None, prompt: str | None = None, extra: str | None = None, source: str = "player",
               status: str = "queued", reason: str | None = None, turn_id: int | None = None) -> dict:
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
    w, h = SIZES[kind]
    final = (prompt or "").strip()[:MAX_PROMPT] or build_prompt(kind, desc, extra)
    jid = conn.execute("""INSERT INTO art_jobs (campaign_id, kind, subject_id, subject_name, prompt, negative, width, height, seed, status,
                          source, reason, note, turn_id, checkpoint, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (cid, kind, subject_id, name, final, ", ".join(x for x in (NEGATIVE, KIND_NEGATIVE.get(kind)) if x), w, h, random.randint(1, 2**31 - 1), status, source, reason, note,
                        turn_id, checkpoint_name(settings), now_iso())).lastrowid
    return job_view(one(conn, "SELECT * FROM art_jobs WHERE id = ?", (jid,)))


def job_view(j: dict) -> dict:
    out = {k: j[k] for k in ("id", "campaign_id", "kind", "subject_id", "subject_name", "prompt", "width", "height", "seed", "status",
                             "source", "reason", "note", "error", "is_current", "created_at", "finished_at", "checkpoint")}
    out["url"] = f"media/art/{j['campaign_id']}/{j['file']}" if j.get("file") else None
    return out


def current_art(conn: sqlite3.Connection, cid: int) -> dict:
    """Player view: the current picture per subject, for the HUD, sheets, inventory and combat panel."""
    out: dict[str, dict] = {k: {} for k in KINDS}
    for j in rows(conn, "SELECT kind, subject_id, subject_name, file FROM art_jobs WHERE campaign_id = ? AND is_current = 1 AND status = 'done'", (cid,)):
        url = f"media/art/{cid}/{j['file']}"
        key = j["subject_name"].lower() if j["kind"] == "mob" else str(j["subject_id"])
        out[j["kind"]][key] = url
    return out


def make_current(conn: sqlite3.Connection, settings: Settings, job_id: int) -> dict:
    """Mark a finished picture as its subject's current one; for a character also install it as the portrait.
    Call inside a transaction while holding the turn lock (characters are part of the turn's state fingerprint)."""
    j = one(conn, "SELECT * FROM art_jobs WHERE id = ?", (job_id,))
    if not j or j["status"] != "done":
        raise ValueError("only a finished picture can be used")
    same = "subject_name = ? COLLATE NOCASE" if j["kind"] == "mob" else "subject_id = ?"
    conn.execute(f"UPDATE art_jobs SET is_current = 0 WHERE campaign_id = ? AND kind = ? AND {same}",
                 (j["campaign_id"], j["kind"], j["subject_name"] if j["kind"] == "mob" else j["subject_id"]))
    conn.execute("UPDATE art_jobs SET is_current = 1 WHERE id = ?", (job_id,))
    if j["kind"] == "character" and j["subject_id"]:
        settings.portraits_dir.mkdir(parents=True, exist_ok=True)
        name = f"art-{j['id']}.png"
        shutil.copyfile(art_dir(settings, j["campaign_id"]) / j["file"], settings.portraits_dir / name)
        conn.execute("UPDATE characters SET portrait_path = ? WHERE id = ?", (name, j["subject_id"]))
    return job_view(one(conn, "SELECT * FROM art_jobs WHERE id = ?", (job_id,)))


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
                    png = self._render(j)
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

    def _render(self, j: dict) -> bytes:
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
