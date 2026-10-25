"""Minimal ComfyUI client: start the local install on demand, queue a workflow, wait, fetch the image, free VRAM.

Mirrors Barcode Battles' padRepair.ts (same install and port, so the two apps share one ComfyUI server). ComfyUI runs
on this machine only (127.0.0.1); nothing is uploaded anywhere.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, url: str, root: Path | None):
        self.url = url.rstrip("/")
        self.root = root

    # ------------------------------------------------------------ server
    def _get(self, path: str, timeout: float = 10) -> bytes:
        with urllib.request.urlopen(self.url + path, timeout=timeout) as r:
            return r.read()

    def _post(self, path: str, body: dict, timeout: float = 30) -> dict:
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as exc:
            raise ComfyError(f"ComfyUI rejected the request ({exc.code}): {exc.read().decode('utf-8', 'replace')[:600]}") from None
        return json.loads(raw) if raw.strip() else {}

    def running(self) -> bool:
        try:
            self._get("/system_stats", timeout=3)
            return True
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    def stats(self) -> dict | None:
        try:
            return json.loads(self._get("/system_stats", timeout=3))
        except (urllib.error.URLError, OSError, TimeoutError, ValueError):
            return None

    def checkpoints(self) -> list[str]:
        try:
            info = json.loads(self._get("/object_info/CheckpointLoaderSimple", timeout=10))
            return list(info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0])
        except (urllib.error.URLError, OSError, TimeoutError, ValueError, KeyError, IndexError):
            return []

    def ensure_running(self, wait_s: int = 420) -> None:  # a cold start takes ~4 minutes on this 16 GB machine
        """Start the portable ComfyUI (python_embeded) if it isn't up; same flags Barcode Battles uses."""
        if self.running():
            return
        if not self.root or not (self.root / "python_embeded" / "python.exe").exists():
            raise ComfyError(f"ComfyUI is not running at {self.url} and no portable install was found at {self.root}")
        subprocess.Popen([str(self.root / "python_embeded" / "python.exe"), "-s", str(self.root / "ComfyUI" / "main.py"),
                          "--listen", "127.0.0.1", "--port", str(urllib.parse.urlparse(self.url).port or 8188), "--disable-auto-launch"],
                         cwd=self.root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0))
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if self.running():
                return
            time.sleep(2)
        raise ComfyError(f"ComfyUI did not start within {wait_s}s")

    # ------------------------------------------------------------ jobs
    def run(self, workflow: dict, timeout_s: int = 900) -> bytes:
        """Queue a workflow and return the first output image's bytes."""
        queued = self._post("/prompt", {"prompt": workflow, "client_id": uuid.uuid4().hex})
        pid = queued.get("prompt_id")
        if not pid:
            raise ComfyError(f"ComfyUI did not accept the workflow: {json.dumps(queued)[:400]}")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            time.sleep(1.5)
            try:
                hist = json.loads(self._get(f"/history/{pid}"))
            except (urllib.error.URLError, OSError, TimeoutError, ValueError):
                continue
            entry = hist.get(pid)
            if not entry:
                continue
            status = entry.get("status") or {}
            if status.get("status_str") == "error":
                msgs = [m for m in status.get("messages") or [] if m and m[0] == "execution_error"]
                detail = msgs[0][1].get("exception_message") if msgs else "unknown error"
                raise ComfyError(f"ComfyUI failed: {str(detail)[:400]}")
            for out in (entry.get("outputs") or {}).values():
                for img in out.get("images") or []:
                    q = urllib.parse.urlencode({"filename": img["filename"], "subfolder": img.get("subfolder", ""), "type": img.get("type", "output")})
                    return self._get(f"/view?{q}", timeout=60)
            if status.get("completed"):
                raise ComfyError("ComfyUI finished without an image")
        raise ComfyError(f"ComfyUI took longer than {timeout_s}s")

    def free(self) -> None:
        """Unload models so the GPU and RAM are free for other work (Barcode Battles does the same)."""
        try:
            self._post("/free", {"unload_models": True, "free_memory": True}, timeout=10)
        except (ComfyError, urllib.error.URLError, OSError, TimeoutError):
            pass


def sdxl_txt2img(checkpoint: str, positive: str, negative: str, width: int, height: int, seed: int, steps: int, cfg: float,
                 sampler: str, scheduler: str, prefix: str) -> dict:
    """Plain SDXL text-to-image graph (API format)."""
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": positive}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": negative}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
                                                    "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler, "scheduler": scheduler,
                                                    "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": prefix}},
    }


def default_root() -> Path | None:
    p = os.environ.get("COMFYUI_ROOT") or r"E:\barcodebattles\ComfyUI"
    return Path(p) if p else None
