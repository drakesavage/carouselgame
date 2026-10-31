"""Carousel runtime configuration. Everything comes from environment variables (optionally a local .env file).

The runtime GM runs through the Codex CLI (`codex exec`, signed in with the user's ChatGPT account),
so no paid API key is needed. "mock" runs a scripted GM for development and tests.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, existing environment wins."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT_DIR / ".env")


def resolve_codex_bin() -> str | None:
    """CODEX_BIN if it exists, else the Codex desktop app's newest build in %LOCALAPPDATA%\\OpenAI\\Codex\\bin
    (top level or one hashed subfolder), else `codex` on PATH. Same lookup as Barcode Battles uses, re-run on
    every call because the desktop app deletes old hashed build folders when it updates."""
    pinned = os.environ.get("CODEX_BIN")
    if pinned and Path(pinned).exists():
        return pinned
    local = os.environ.get("LOCALAPPDATA")
    if local:
        app_bin = Path(local) / "OpenAI" / "Codex" / "bin"
        if app_bin.exists():
            candidates = [app_bin / "codex.exe", *[d / "codex.exe" for d in app_bin.iterdir() if d.is_dir()]]
            existing = [c for c in candidates if c.exists()]
            if existing:
                return str(max(existing, key=lambda p: p.stat().st_mtime))
    return shutil.which("codex")


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("CAROUSEL_DATA_DIR", ROOT_DIR / "data")))
    # Carousel source packages (read-only to the game; see carousel/sources.py).
    source_dir: Path = field(default_factory=lambda: Path(os.environ.get("CAROUSEL_SOURCE_DIR", r"E:\Carousel")))
    port: int = field(default_factory=lambda: int(os.environ.get("CAROUSEL_PORT", "8797")))
    rulebook_dir: Path | None = field(
        default_factory=lambda: Path(os.environ["RULEBOOK_DIR"]) if os.environ.get("RULEBOOK_DIR") else None
    )
    campaign_ledger_dir: Path | None = field(
        default_factory=lambda: Path(os.environ["CAMPAIGN_LEDGER_DIR"]) if os.environ.get("CAMPAIGN_LEDGER_DIR") else None
    )
    # Default provider when the settings table has none: "codex" or "mock".
    gm_provider: str = field(default_factory=lambda: os.environ.get("GM_PROVIDER", "codex"))
    # Empty = whatever model the user's Codex config defaults to.
    codex_model: str = field(default_factory=lambda: os.environ.get("CODEX_MODEL", ""))
    codex_reasoning_effort: str = field(default_factory=lambda: os.environ.get("CODEX_REASONING_EFFORT", "medium"))
    codex_timeout_s: int = field(default_factory=lambda: int(os.environ.get("CODEX_TIMEOUT_S", "240")))
    # Local ComfyUI for campaign art (art/service.py). The portable install is started on demand if it isn't running.
    comfy_url: str = field(default_factory=lambda: os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188"))
    comfy_checkpoint: str = field(default_factory=lambda: os.environ.get("COMFY_CHECKPOINT", "DreamShaperXL_Turbo_v2_1.safetensors"))
    # Who paints character portraits: "codex" (Codex's image tool; falls back to ComfyUI) or "comfy".
    portrait_engine: str = field(default_factory=lambda: os.environ.get("PORTRAIT_ENGINE", "codex"))
    comfy_timeout_s: int = field(default_factory=lambda: int(os.environ.get("COMFY_TIMEOUT_S", "900")))
    gemini_model: str = field(default_factory=lambda: os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"))
    # Input tokens per minute allowed by your Gemini tier (free tier: 250k for gemini-3.5-flash-lite; see AI Studio).
    gemini_tpm_limit: int = field(default_factory=lambda: int(os.environ.get("GEMINI_TPM_LIMIT", "250000")))
    max_tool_rounds: int = 8   # combat needs evade -> resolve -> attacks -> bookkeeping
    recent_message_window: int = 12
    summarize_every_messages: int = 24
    autosave_keep: int = 25

    @property
    def campaign_db(self) -> Path:
        return self.data_dir / "campaign.db"

    @property
    def reference_db(self) -> Path:
        """Carousel reference data imported from source_dir; disposable and rebuildable (carousel/importer.py)."""
        return self.data_dir / "reference.db"

    @property
    def rulebook_db(self) -> Path:
        return self.data_dir / "rulebooks.db"

    @property
    def portraits_dir(self) -> Path:
        return self.data_dir / "portraits"

    @property
    def codex_jobs_dir(self) -> Path:
        """Scratch folders for GM jobs (git-ignored; access restricted to this account by providers.secure_dir).
        Codex 0.163 fails with "path not found" when its working folder is under LocalAppData, so they stay in data/."""
        if os.environ.get("CODEX_JOBS_DIR"):
            return Path(os.environ["CODEX_JOBS_DIR"])
        return self.data_dir / "codex_jobs"

    @property
    def prompt_capture_file(self) -> Path:
        """Holds the expiry of the opt-in, short-lived full-prompt capture (development only)."""
        return self.data_dir / "prompt_capture.json"

    def prompt_capture_active(self) -> bool:
        try:
            return json.loads(self.prompt_capture_file.read_text(encoding="utf-8")).get("until", 0) > time.time()
        except (OSError, ValueError):
            return False

    def gemini_api_key(self) -> str | None:
        """GEMINI_API_KEY, else the first key file found: GEMINI_API_KEY_FILE, data/gemini_api_key.txt, ./gemini_api_key.txt
        (both file names are git-ignored). Never logged or sent anywhere but the Gemini API header."""
        if os.environ.get("GEMINI_API_KEY"):
            return os.environ["GEMINI_API_KEY"].strip()
        for p in (os.environ.get("GEMINI_API_KEY_FILE"), self.data_dir / "gemini_api_key.txt", ROOT_DIR / "gemini_api_key.txt"):
            if p and Path(p).is_file():
                key = Path(p).read_text(encoding="utf-8").strip()
                if key:
                    return key
        return None

    def context_budgets(self) -> dict:
        """Per-install overrides of memory/context.py DEFAULT_BUDGET, e.g. CONTEXT_BUDGETS={"present_each": 3200}."""
        try:
            raw = json.loads(os.environ.get("CONTEXT_BUDGETS") or "{}")
        except ValueError:
            return {}
        return {k: int(v) for k, v in raw.items() if isinstance(v, (int, float))}

    def gm_token(self) -> str:
        """Token guarding /api/gm. From GM_TOKEN, else generated once into data/gm_token.txt."""
        if os.environ.get("GM_TOKEN"):
            return os.environ["GM_TOKEN"]
        path = self.data_dir / "gm_token.txt"
        if not path.exists():
            self.data_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        return path.read_text(encoding="utf-8").strip()

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.portraits_dir.mkdir(parents=True, exist_ok=True)
