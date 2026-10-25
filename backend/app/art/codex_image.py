"""Character portraits painted by Codex's built-in image tool (your ChatGPT plan; no API billing).

Same isolation as GM jobs (gm/providers.py) except two switches, verified with escape probes against codex-cli 0.163:
`image_generation` on, and `code_mode_host` on with `code_mode` itself off. In that setup the job sees only the image
tool plus apply_patch (refused by the read-only sandbox), an empty MCP resource list and a clock: no shell, no file
reads, no network, no sub-agents, and no node_repl (which comes back if `code_mode` is on). The live event watcher
kills the job at its first tool event other than the image itself, and the turn falls back to ComfyUI.

The picture lands in $CODEX_HOME/generated_images/<thread id>/; it is copied out and the job folder deleted.
Reference photos (optional, per character) are attached with --image so a face can follow a real person.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from ..config import Settings, resolve_codex_bin
from ..gm.providers import DISABLED_FEATURES, GM_CONFIG, CodexCliProvider, ProviderError, _toml_str, child_env, secure_dir

IMAGE_KEEP = {"image_generation", "code_mode_host"}
IMAGE_CONFIG = ["mcp_servers.node_repl.enabled=false"]
ILLUSTRATOR = """You are the portrait illustrator for a tabletop RPG campaign. Use your image generation tool exactly once to
paint the picture described in the user message, then reply with one short sentence. Do not use any other tool. If
reference photos are attached, they show the real person the character is based on: match their face, features,
coloring and build closely, but take clothing, gear, pose and setting only from the description."""


class CodexImageError(RuntimeError):
    pass


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def image_exec_args(binary: str, job: Path, final: Path, refs: list[Path]) -> list[str]:
    args = [binary, "exec", "--strict-config", "--skip-git-repo-check", "--ephemeral", "--json", "--sandbox", "read-only", "--cd", str(job)]
    for f in DISABLED_FEATURES:
        if f not in IMAGE_KEEP:
            args += ["--disable", f]
    args += ["--enable", "image_generation"]
    for kv in GM_CONFIG + IMAGE_CONFIG:
        args += ["-c", kv]
    args += ["-c", f"developer_instructions={_toml_str(ILLUSTRATOR)}"]
    for r in refs:
        args.append(f"--image={r}")
    args += ["-o", str(final), "-"]
    return args


def paint(settings: Settings, prompt: str, refs: list[Path], label: str) -> bytes:
    """Run one isolated image job and return the PNG bytes."""
    binary = resolve_codex_bin()
    if not binary:
        raise CodexImageError("Codex CLI not found")
    root = settings.codex_jobs_dir
    secure_dir(root)
    job = root / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}-{uuid.uuid4().hex[:6]}"
    job.mkdir(mode=0o700)
    meta: dict = {}
    try:
        local_refs = []
        for i, r in enumerate(refs[:4]):  # copies inside the job folder, so the sandbox can read them
            dst = job / f"ref-{i}{r.suffix.lower()}"
            shutil.copyfile(r, dst)
            local_refs.append(dst)
        final = job / "final.txt"
        args = image_exec_args(binary, job, final, local_refs)
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=job, env=child_env(),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        err_chunks: list[bytes] = []
        reader = threading.Thread(target=lambda: err_chunks.append(proc.stderr.read()), daemon=True)  # type: ignore[union-attr]
        reader.start()
        timer = threading.Timer(settings.comfy_timeout_s, proc.kill)
        timer.start()
        try:
            proc.stdin.write(prompt.encode("utf-8"))  # type: ignore[union-attr]
            proc.stdin.close()  # type: ignore[union-attr]
            lines, violation = CodexCliProvider(settings)._watch(proc, meta)
            proc.wait()
        finally:
            timer.cancel()
        reader.join(5)
        if violation:
            raise CodexImageError(f"the image job tried another tool ({violation}) and was stopped")
        thread = next((l.split('"thread_id":"')[1].split('"')[0] for l in lines if '"thread.started"' in l and '"thread_id":"' in l), None)
        stderr = b"".join(err_chunks).decode("utf-8", "replace")
        folder = codex_home() / "generated_images" / (thread or "-")
        pngs = sorted(folder.glob("*.png"), key=lambda p: p.stat().st_mtime) if thread and folder.exists() else []
        if not pngs:
            said = final.read_text(encoding="utf-8")[:200] if final.exists() else ""
            tail = " ".join(stderr.strip().splitlines()[-3:])[:300]
            raise CodexImageError(f"Codex made no image ({said or tail or 'exit code ' + str(proc.returncode)})")
        return pngs[-1].read_bytes()
    except (OSError, ProviderError) as exc:
        raise CodexImageError(str(exc)) from None
    finally:
        shutil.rmtree(job, ignore_errors=True)


def portrait_prompt(name: str, ref: dict, brief: dict, appearance: str, animal: bool, wide: bool, has_refs: bool) -> str:
    """A plain-language brief for the image model (it reads full sentences well, unlike SDXL)."""
    lines = [f"Paint one portrait of {name}."]
    if ref:
        head = ", ".join(x for x in (ref.get("demographic"), ref.get("age") or ref.get("size")) if x)
        lines.append(f"Who: {head}.")
        for label, key in (("Body and face", "physique"), ("Clothing and gear", "outfit"), ("Signature", "signature"), ("Expression", "expression")):
            text = " ".join(s for s in re.split(r"(?<=[.!?])\s+", ref.get(key) or "")
                            if has_refs or not re.search(r"reference[- ]photo|portrait-reference", s, re.I))  # no photos: don't ask for them
            if text:
                lines.append(f"{label}: {text}")
    else:
        lines.append("Description: " + brief["description"].replace("(", "").replace(":1.3)", "").replace(":1.2)", ""))
    if appearance:
        lines.append(f"Appearance notes: {appearance}")
    if has_refs:
        lines.append("Reference photos are attached: the face, features and coloring must match the person in them.")
    frame = ("Full-body view of the animal in a natural pose, wide landscape frame." if wide else
             "Full-body view of the animal in a natural pose, portrait frame." if animal else
             "Waist-up, facing the viewer, portrait orientation (2:3), with the head and shoulders in the upper third so a "
             "square crop of the top shows the face; large gear may be partly cut off at the edges.")
    lines.append("Composition: " + frame)
    lines.append("Style: gritty painted fantasy illustration for a tabletop RPG sourcebook; visible oil-paint brushwork, dramatic "
                 "chiaroscuro lighting, grimy dungeon background, a darkly humorous tone. Keep every listed detail of clothing and gear "
                 "(colors included) and nothing that contradicts them. No text, letters, logos, borders or watermarks.")
    return "\n".join(lines)
