"""Reviewed write-back of console play to the Git campaign ledger.

Flow:  preview → (player reviews diffs, deselects anything) → apply → (optional, separate) push.

* Baseline = the ledger commit this campaign was imported from or last synced to (`git show <commit>:<path>`).
* Three-way per change: console value vs baseline vs the ledger's *current* working tree. If the ledger changed
  the same field independently (e.g. a ChatGPT session committed to it), the change is a conflict and is not
  selected by default; "already applied" changes are dropped.
* Apply refuses when: the working tree is dirty, the branch is behind its upstream, or anything changed since the
  preview (fingerprint). Files are edited surgically (yamledit), validated with the ledger's own
  tools/validate_state.py, optionally the checkpoint is regenerated, then one commit is made. Any failure restores
  every file and commits nothing.
* Push is never automatic.
* GM-only content (gm_state/**) is redacted from the player-facing preview.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from ..db import now_iso, one, rows, transaction
from .changes import Builder, Change, files_needed
from .yamledit import EditError, _render, apply_edits, get, render_jsonl

GIT_TIMEOUT = 60


class LedgerError(RuntimeError):
    pass


def git(root: Path, *args: str, check: bool = True, timeout: int = GIT_TIMEOUT) -> str:
    try:
        p = subprocess.run(["git", "-c", "core.longpaths=true", "-C", str(root), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise LedgerError(f"git {args[0]} timed out") from None
    if check and p.returncode != 0:
        raise LedgerError(f"git {' '.join(args[:2])} failed: {(p.stderr or p.stdout).strip()[:500]}")
    return p.stdout.strip() if p.returncode == 0 else ""


def link(conn: sqlite3.Connection, cid: int) -> dict:
    """Which ledger a campaign came from, and the commit to diff against."""
    rep = one(conn, "SELECT * FROM import_reports WHERE campaign_id = ? ORDER BY id LIMIT 1", (cid,))
    if not rep:
        raise LedgerError("this campaign was not imported from a ledger")
    report = rep["report"]
    root = Path(rep["source_path"])
    last = one(conn, "SELECT * FROM ledger_syncs WHERE campaign_id = ? ORDER BY id DESC LIMIT 1", (cid,))
    base = last["new_commit"] if last else report["source"].get("git_head")
    if not base:
        raise LedgerError("the import did not record a git commit for the ledger; re-import from a git checkout")
    hw = last["hw"] if last else {}
    return {"root": root, "base": base, "hw": hw, "import_dirty": bool(report["source"].get("git_dirty")) and not last, "last_sync": last}


def _show(root: Path, commit: str, rel: str) -> str | None:
    p = subprocess.run(["git", "-C", str(root), "show", f"{commit}:{rel}"], capture_output=True, timeout=GIT_TIMEOUT,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return p.stdout.decode("utf-8") if p.returncode == 0 else None


def repo_status(root: Path, fetch: bool) -> dict:
    if not (root / ".git").exists():
        raise LedgerError(f"{root} is not a git checkout")
    st: dict[str, Any] = {"branch": git(root, "rev-parse", "--abbrev-ref", "HEAD"), "head": git(root, "rev-parse", "HEAD"),
                          "dirty_files": [ln[3:] for ln in git(root, "status", "--porcelain").splitlines() if ln.strip()],
                          "ahead": None, "behind": None, "fetch_error": None}
    upstream = git(root, "rev-parse", "--abbrev-ref", "@{u}", check=False)
    st["upstream"] = upstream or None
    if upstream:
        if fetch:
            try:
                git(root, "fetch", "--quiet", timeout=30)
            except LedgerError as exc:
                st["fetch_error"] = str(exc)
        counts = git(root, "rev-list", "--left-right", "--count", f"HEAD...{upstream}", check=False).split()
        if len(counts) == 2:
            st["ahead"], st["behind"] = int(counts[0]), int(counts[1])
    authors = git(root, "log", "-200", "--format=%an\t%ae", check=False).splitlines()
    top = max(set(authors), key=authors.count) if authors else ""
    st["author_default"] = {"name": top.split("\t")[0], "email": top.split("\t")[1]} if "\t" in top else {"name": "", "email": ""}
    return st


def _classify(c: Change, base: Any, theirs: Any, theirs_text: str | None) -> None:
    if c.kind == "jsonl_append":
        present = set()
        for ln in (theirs_text or "").splitlines():
            try:
                present.add(json.loads(ln).get("provenance"))
            except (json.JSONDecodeError, AttributeError):
                pass
        remaining = [r for r in c.payload if r.get("provenance") not in present]
        c.status = "already_applied" if not remaining else "clean"
        c.payload = remaining
        return
    if c.kind == "create_file":
        c.status = "conflict" if theirs_text is not None else "clean"
        if theirs_text is not None:
            c.detail["conflict"] = "file already exists in the ledger"
        return
    if theirs is None:
        c.status, c.detail["conflict"] = "conflict", "file is missing from the ledger working tree"
        return
    e = c.edit
    if e.op == "append":
        lst = get(theirs, e.path)
        if not isinstance(lst, list):
            c.status, c.detail["conflict"] = "conflict", "target list no longer exists in the ledger"
        elif e.value in lst:
            c.status = "already_applied"
        elif isinstance(e.value, dict) and "id" in e.value and any(isinstance(x, dict) and x.get("id") == e.value["id"] for x in lst):
            c.status, c.detail["conflict"] = "conflict", f"the ledger already has an entry with id {e.value['id']!r}"
        else:
            c.status = "clean"
        return
    parent_path = e.path[:-1]
    if parent_path and get(theirs, parent_path) is None:
        c.status, c.detail["conflict"] = "conflict", "the containing record no longer exists in the ledger"
        return
    base_v, theirs_v = get(base, e.path), get(theirs, e.path)
    if theirs_v == e.value:
        c.status = "already_applied"
    elif theirs_v == base_v:
        c.status = "clean"
    else:
        c.status = "conflict"
        c.detail["conflict"] = "the ledger changed this value since the console's baseline"
        c.detail["ledger_now"] = theirs_v


def _render_files(root: Path, changes: list[Change], selected: set[str]) -> dict[str, tuple[str | None, str]]:
    """file -> (original text or None, new text)."""
    by_file: dict[str, list[Change]] = {}
    for c in changes:
        if c.id in selected and c.status == "clean":
            by_file.setdefault(c.file, []).append(c)
    out = {}
    for f, cs in by_file.items():
        path = root / f
        original = path.read_text(encoding="utf-8") if path.exists() else None
        text = original
        create = [c for c in cs if c.kind == "create_file"]
        if create:
            text = "\n".join(_render(create[0].payload)) + "\n"
        edits = [c.edit for c in cs if c.kind == "yaml"]
        if edits:
            if text is None:
                raise LedgerError(f"{f} does not exist")
            try:
                text = apply_edits(text, edits)
            except EditError as exc:
                raise LedgerError(f"{f}: {exc}") from None
        for c in cs:
            if c.kind == "jsonl_append" and c.payload:
                base_text = text or ""
                if base_text and not base_text.endswith("\n"):
                    base_text += "\n"
                text = base_text + render_jsonl(c.payload)
        out[f] = (original, text)
    return out


def _diff(f: str, old: str | None, new: str) -> str:
    return "".join(difflib.unified_diff((old or "").splitlines(True), new.splitlines(True), f"a/{f}", f"b/{f}", n=2))


def preview(conn: sqlite3.Connection, cid: int, *, fetch: bool = True, redact: bool = True) -> dict:
    ln = link(conn, cid)
    root: Path = ln["root"]
    st = repo_status(root, fetch)
    base, theirs, theirs_text = {}, {}, {}
    for f in files_needed(conn, cid):
        bt = _show(root, ln["base"], f)
        base[f] = yaml.safe_load(bt) if bt else None
        p = root / f
        theirs_text[f] = p.read_text(encoding="utf-8") if p.exists() else None
        theirs[f] = yaml.safe_load(theirs_text[f]) if theirs_text[f] else None
    for f in ("rolls/public.jsonl", "gm_state/rolls.jsonl"):
        p = root / f
        theirs_text[f] = p.read_text(encoding="utf-8") if p.exists() else None
    changes, warnings = Builder(conn, cid, base, ln["hw"]).build()
    for c in changes:
        _classify(c, base.get(c.file), theirs.get(c.file), theirs_text.get(c.file))
    changes = [c for c in changes if c.status != "already_applied"]
    clean_ids = {c.id for c in changes if c.status == "clean"}
    files = []
    try:
        rendered = _render_files(root, changes, clean_ids)
    except LedgerError as exc:
        rendered = {}
        warnings.append(f"Could not render the proposed edits: {exc}")
    for f, (old, new) in sorted(rendered.items()):
        gm = f.startswith("gm_state/")
        d = _diff(f, old, new)
        files.append({"path": f, "visibility": "gm" if gm else "player", "new_file": old is None,
                      "diff": f"(GM-only file — {d.count(chr(10))} diff lines hidden)" if gm and redact else d})
    if ln["import_dirty"]:
        warnings.append("The ledger had uncommitted changes when it was imported; the baseline is the commit, so review conflicts carefully.")
    blockers = []
    if st["dirty_files"]:
        blockers.append(f"The ledger working tree has uncommitted changes ({', '.join(st['dirty_files'][:5])}). Commit or discard them first.")
    if st["behind"]:
        blockers.append(f"The ledger is {st['behind']} commit(s) behind {st['upstream']}. Pull (fast-forward) first, then re-preview.")
    fp_src = json.dumps({"head": st["head"], "base": ln["base"], "files": {f: hashlib.sha256((theirs_text.get(f) or "").encode()).hexdigest() for f in theirs_text},
                         "changes": sorted(c.id for c in changes)}, sort_keys=True)
    out_changes = []
    for c in changes:
        d = {"id": c.id, "file": c.file, "summary": c.summary, "group": c.group, "status": c.status, "visibility": c.visibility, "kind": c.kind,
             "detail": {k: v for k, v in c.detail.items() if k not in ("cat", "link", "links")}}
        if redact and c.visibility == "gm":
            d["summary"], d["detail"] = "GM-only change (content hidden)", {}
        out_changes.append(d)
    groups = sorted({c.group for c in changes if c.status == "clean" and c.visibility == "player"})
    message = (f"Record Crawler Console play ({sum(1 for c in changes if c.status == 'clean')} changes)\n\n"
               + ("Updated: " + ", ".join(groups) + "\n" if groups else "")
               + "Reviewed and written by Crawler Console; validated with tools/validate_state.py.")
    curated = checkpoint_is_curated(root) if changes else None
    return {"ledger_path": str(root), "base_commit": ln["base"], "status": st, "changes": out_changes, "files": files, "suggested_message": message,
            "checkpoint_curated": curated,
            "warnings": warnings, "blockers": blockers, "fingerprint": hashlib.sha256(fp_src.encode()).hexdigest(),
            "last_sync": {k: ln["last_sync"][k] for k in ("new_commit", "created_at")} if ln["last_sync"] else None,
            "_changes": changes}


def checkpoint_is_curated(root: Path) -> bool | None:
    """True when checkpoints/latest.md differs from what tools/build_checkpoint.py generates from the current
    ledger, i.e. it holds hand-written sections that regenerating would drop."""
    tool, current = root / "tools" / "build_checkpoint.py", root / "checkpoints" / "latest.md"
    if not tool.exists() or not current.exists():
        return None
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "latest.md"
        p = subprocess.run([sys.executable, str(tool), "--root", str(root), "--output", str(out)], capture_output=True, timeout=120,
                           cwd=root, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if p.returncode != 0 or not out.exists():
            return None
        return out.read_text(encoding="utf-8").strip() != current.read_text(encoding="utf-8").strip()


def _run_tool(root: Path, script: str) -> tuple[bool, str]:
    path = root / "tools" / script
    if not path.exists():
        return True, f"{script} not present; skipped"
    p = subprocess.run([sys.executable, str(path), "--root", str(root)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120, cwd=root, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def _hw_now(conn: sqlite3.Connection, cid: int) -> dict:
    q = lambda sql: (conn.execute(sql, (cid,)).fetchone()[0] or 0)  # noqa: E731
    return {"roll": q("SELECT MAX(id) FROM dice_rolls WHERE campaign_id = ?"), "event": q("SELECT MAX(id) FROM events WHERE campaign_id = ?"),
            "secret": q("SELECT MAX(id) FROM gm_secrets WHERE campaign_id = ?"), "fact": q("SELECT MAX(id) FROM lore_facts WHERE campaign_id = ?"),
            "ruling": q("SELECT MAX(id) FROM rulings WHERE campaign_id = ?"), "achievement": q("SELECT MAX(id) FROM achievements WHERE campaign_id = ?")}


def apply(conn: sqlite3.Connection, cid: int, *, fingerprint: str, selected: list[str] | None, message: str,
          author_name: str, author_email: str, regenerate_checkpoint: bool = False) -> dict:
    if not author_name.strip() or "@" not in author_email:
        raise LedgerError("a commit author name and email are required")
    pv = preview(conn, cid, fetch=False, redact=False)
    if pv["fingerprint"] != fingerprint:
        raise LedgerError("The ledger or the campaign changed since this preview. Review the new preview before writing.")
    if pv["blockers"]:
        raise LedgerError(" ".join(pv["blockers"]))
    changes: list[Change] = pv["_changes"]
    clean = {c.id for c in changes if c.status == "clean"}
    chosen = clean if selected is None else set(selected) & clean
    if selected is not None and set(selected) - clean:
        raise LedgerError("Only clean changes can be written; resolve conflicts in the ledger and re-preview.")
    if not chosen:
        raise LedgerError("No changes selected.")
    root = Path(pv["ledger_path"])
    rendered = _render_files(root, changes, chosen)
    written: list[str] = []
    try:
        for f, (_, new) in rendered.items():
            p = root / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(new, encoding="utf-8", newline="\n")
            written.append(f)
        ok, out = _run_tool(root, "validate_state.py")
        if not ok:
            raise LedgerError(f"The ledger's validator rejected the change:\n{out}")
        checkpoint_out = None
        if regenerate_checkpoint:
            ok, checkpoint_out = _run_tool(root, "build_checkpoint.py")
            if not ok:
                raise LedgerError(f"Checkpoint regeneration failed:\n{checkpoint_out}")
            if (root / "checkpoints" / "latest.md").exists():
                written.append("checkpoints/latest.md")
        git(root, "add", "--", *written)
        if not git(root, "diff", "--cached", "--name-only", check=False):
            raise LedgerError("Nothing to commit after applying the changes.")
        git(root, "-c", f"user.name={author_name}", "-c", f"user.email={author_email}", "commit", "-q", "-m", message)
    except BaseException:
        for f in written:
            orig = rendered.get(f, (None, None))[0]
            p = root / f
            if f == "checkpoints/latest.md":
                git(root, "checkout", "--", f, check=False)
            elif orig is None:
                p.unlink(missing_ok=True)
            else:
                p.write_text(orig, encoding="utf-8", newline="\n")
        git(root, "reset", "-q", check=False)
        raise
    new_head = git(root, "rev-parse", "HEAD")
    # advance high-water marks only for categories that were written (or had nothing pending)
    old_hw = link(conn, cid)["hw"]
    now = _hw_now(conn, cid)
    cats_pending = {c.detail.get("cat") for c in changes if c.detail.get("cat")}
    cats_written = {c.detail.get("cat") for c in changes if c.id in chosen and c.detail.get("cat")}
    hw = {k: (v if (k not in cats_pending or k in cats_written or k == "event") else old_hw.get(k, 0)) for k, v in now.items()}
    if "fact" in cats_pending and "fact" not in cats_written and "secret" in cats_written:
        hw["fact"] = old_hw.get("fact", 0)
    summary = {"changes": len(chosen), "files": sorted(set(written)), "skipped": len(clean - chosen),
               "conflicts": sum(1 for c in changes if c.status == "conflict")}
    with transaction(conn):
        conn.execute("INSERT INTO ledger_syncs (campaign_id, ledger_path, base_commit, new_commit, hw_json, summary_json, created_at) VALUES (?,?,?,?,?,?,?)",
                     (cid, str(root), pv["base_commit"], new_head, json.dumps(hw), json.dumps(summary), now_iso()))
        conn.execute("""INSERT INTO events (campaign_id, type, summary, payload_json, visibility, game_time_minutes, created_at)
                        VALUES (?,?,?,?,?,(SELECT game_time_minutes FROM campaigns WHERE id = ?),?)""",
                     (cid, "ledger_synced", f"Ledger updated: commit {new_head[:10]} ({len(chosen)} changes)", json.dumps(summary), "player", cid, now_iso()))
        _refresh_sections(conn, cid, root, [f for f in written])
        for c in changes:  # link console rows to the ledger ids they were just written under
            if c.id not in chosen:
                continue
            links = c.detail.get("links") or ([c.detail["link"]] if c.detail.get("link") else [])
            for kind, ids, key in links:
                marks = ",".join("?" * len(ids))
                if kind == "quest":
                    conn.execute(f"UPDATE quests SET description = ? WHERE id IN ({marks})", (f"Ledger objective id: {key}", *ids))
                elif kind == "item":
                    conn.execute(f"UPDATE items SET item_key = ? WHERE id IN ({marks})", (key, *ids))
                elif kind == "skill":
                    conn.execute(f"UPDATE skills SET key = ? WHERE id IN ({marks})", (key, *ids))
    return {"commit": new_head, "summary": summary, "validator": "passed", "checkpoint": checkpoint_out}


def _refresh_sections(conn: sqlite3.Connection, cid: int, root: Path, files: list[str]) -> None:
    from ..importer.ledger import _store_sections
    for f in files:
        for r in rows(conn, "SELECT id FROM source_sections WHERE campaign_id = ? AND document_path = ?", (cid, f)):
            conn.execute("INSERT INTO source_sections_fts(source_sections_fts, rowid, section, content) SELECT 'delete', id, document_path || ' ' || section, content FROM source_sections WHERE id = ?", (r["id"],))
            conn.execute("DELETE FROM source_sections WHERE id = ?", (r["id"],))
        p = root / f
        if p.exists() and p.suffix in (".yaml", ".yml", ".md"):
            _store_sections(conn, cid, f, p.read_text(encoding="utf-8"), "gm" if f.startswith("gm_state/") else "player")


def pull(conn: sqlite3.Connection, cid: int) -> dict:
    root = link(conn, cid)["root"]
    st = repo_status(root, fetch=True)
    if st["dirty_files"]:
        raise LedgerError("The ledger has uncommitted changes; cannot pull.")
    git(root, "pull", "--ff-only", "--quiet", timeout=90)
    return repo_status(root, fetch=False)


def push(conn: sqlite3.Connection, cid: int) -> dict:
    root = link(conn, cid)["root"]
    st = repo_status(root, fetch=True)
    if st["behind"]:
        raise LedgerError("The remote has commits you don't have; pull first.")
    git(root, "push", "--quiet", timeout=120)
    return repo_status(root, fetch=False)


def history(conn: sqlite3.Connection, cid: int) -> list[dict]:
    return rows(conn, "SELECT id, base_commit, new_commit, summary_json, created_at FROM ledger_syncs WHERE campaign_id = ? ORDER BY id DESC", (cid,))
