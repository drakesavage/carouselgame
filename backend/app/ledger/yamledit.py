"""Surgical YAML editing for the campaign ledger.

Re-dumping a whole file (even with ruamel round-trip) reflows hundreds of hand-edited lines, which would bury the
real change in a review diff. Instead each edit locates the target node's line span with ruamel's position data and
replaces only that span with a freshly rendered fragment, leaving every other byte untouched.

Safety net: `apply_edits` re-parses the edited text with PyYAML (the parser the ledger's own tools use) and
requires it to equal the original data with the edits applied structurally. Any mismatch raises EditError, so a
bad splice can never be written.

Paths are lists of mapping keys, sequence indexes, or {"id": value} selectors for lists of mappings.
"""
from __future__ import annotations

import copy
import io
import re
from dataclasses import dataclass, field
from typing import Any

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

WIDTH = 120  # the ledger's existing wrap width (measured on the real files)


class EditError(ValueError):
    pass


@dataclass
class Edit:
    op: str                      # "set" (replace or create a key) | "append" (to a list)
    path: list[Any]              # for append: path of the list
    value: Any
    meta: dict = field(default_factory=dict)


def _ruamel() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = WIDTH
    y.indent(mapping=2, sequence=2, offset=0)
    y.representer.add_representer(type(None), lambda rep, _: rep.represent_scalar("tag:yaml.org,2002:null", "null"))
    return y


def _render(value: Any) -> list[str]:
    """Render a plain Python value as block YAML lines (no trailing document marker)."""
    buf = io.StringIO()
    _ruamel().dump(_plain_to_commented(value), buf)
    text = buf.getvalue()
    if text.endswith("...\n"):
        text = text[:-4]
    return text.rstrip("\n").split("\n")


def _plain_to_commented(v: Any) -> Any:
    if isinstance(v, dict):
        m = CommentedMap()
        for k, x in v.items():
            m[k] = _plain_to_commented(x)
        return m
    if isinstance(v, list):
        s = CommentedSeq()
        s.extend(_plain_to_commented(x) for x in v)
        return s
    return v


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def resolve(data: Any, path: list[Any]) -> list[Any]:
    """Turn {"id": x} selectors into indexes against `data`."""
    out, cur = [], data
    for seg in path:
        if isinstance(seg, dict):
            (k, v), = seg.items()
            if not isinstance(cur, list):
                raise EditError(f"selector {seg} applied to a non-list")
            idx = next((i for i, x in enumerate(cur) if isinstance(x, dict) and x.get(k) == v), None)
            if idx is None:
                raise EditError(f"no list item with {k}={v!r}")
            out.append(idx)
            cur = cur[idx]
        else:
            out.append(seg)
            cur = cur[seg] if not isinstance(cur, dict) or seg in cur else None
    return out


def get(data: Any, path: list[Any], default: Any = None) -> Any:
    cur = data
    for seg in path:
        if isinstance(seg, dict):
            (k, v), = seg.items()
            if not isinstance(cur, list):
                return default
            cur = next((x for x in cur if isinstance(x, dict) and x.get(k) == v), None)
        elif isinstance(cur, dict):
            cur = cur.get(seg)
        elif isinstance(cur, list) and isinstance(seg, int) and seg < len(cur):
            cur = cur[seg]
        else:
            return default
        if cur is None:
            return default
    return cur


def _apply_structural(data: Any, e: Edit) -> None:
    path = resolve(data, e.path) if e.op == "append" else resolve(data, e.path[:-1]) + [e.path[-1]]
    if e.op == "append":
        target = data
        for seg in path:
            target = target[seg]
        target.append(copy.deepcopy(e.value))
        return
    parent = data
    for seg in path[:-1]:
        parent = parent[seg]
    parent[path[-1]] = copy.deepcopy(e.value)


def _entry_end(lines: list[str], key_line: int, key_col: int, seq_same_indent: bool) -> int:
    """Last line belonging to the mapping entry that starts at key_line."""
    end = key_line
    i = key_line + 1
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        ind = _indent(ln)
        if ind > key_col or (seq_same_indent and ind == key_col and ln.lstrip().startswith("- ")):
            end = i
            i += 1
            continue
        break
    return end


def _is_flow(node: Any) -> bool:
    return isinstance(node, (CommentedMap, CommentedSeq)) and bool(node.fa.flow_style())


def _splice_set(text: str, parsed: Any, path: list[Any], value: Any) -> str:
    # Inside a flow collection ({a: 1} / [x, y]) line surgery is unsafe: rewrite the outermost flow node instead.
    node = parsed
    for i, seg in enumerate(path[:-1]):
        node = node[seg]
        if _is_flow(node):
            outer = path[: i + 1]
            new_outer = _to_plain(node)
            target = new_outer
            for s in path[i + 1:-1]:
                target = target[s]
            target[path[-1]] = value
            return _splice_set(text, parsed, outer, new_outer)
    lines = text.split("\n")
    parent = parsed
    for seg in path[:-1]:
        parent = parent[seg]
    key = path[-1]
    if not isinstance(parent, CommentedMap):
        raise EditError(f"can only set keys inside mappings (path {path})")
    frag = _render({key: value})
    if key in parent:
        key_line, key_col = parent.lc.key(key)
        old = parent[key]
        end = _entry_end(lines, key_line, key_col, isinstance(old, CommentedSeq))
        prefix = lines[key_line][:key_col]
        new = [prefix + frag[0]] + [" " * key_col + f for f in frag[1:]]
        lines[key_line:end + 1] = new
    else:
        if not parent:
            raise EditError(f"cannot add a key to an empty mapping at {path[:-1]}")
        last = list(parent.keys())[-1]
        first = list(parent.keys())[0]
        col = parent.lc.key(first)[1]
        last_line, last_col = parent.lc.key(last)
        end = _entry_end(lines, last_line, last_col, isinstance(parent[last], CommentedSeq))
        lines[end + 1:end + 1] = [" " * col + f for f in frag]
    return "\n".join(lines)


def _splice_append(text: str, parsed: Any, path: list[Any], value: Any) -> str:
    lines = text.split("\n")
    seq = parsed
    for seg in path:
        seq = seq[seg]
    if not isinstance(seq, CommentedSeq):
        raise EditError(f"append target {path} is not a list")
    if len(seq) == 0 or getattr(seq.fa, "_flow_style", False):
        parent = parsed
        for seg in path[:-1]:
            parent = parent[seg]
        return _splice_set(text, parsed, path, [*list(_to_plain(seq)), value])
    last_idx = len(seq) - 1
    item_line, item_col = seq.lc.item(last_idx)
    dash_col = item_col - 2
    # end of the last item: following lines indented deeper than the dash
    end = item_line
    i = item_line + 1
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        if _indent(ln) > dash_col:
            end = i
            i += 1
            continue
        break
    frag = _render([value])
    lines[end + 1:end + 1] = [" " * dash_col + f for f in frag]
    return "\n".join(lines)


def _to_plain(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _to_plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_to_plain(x) for x in v]
    return v


def apply_edits(text: str, edits: list[Edit]) -> str:
    """Apply edits one at a time (re-parsing between them) and verify the semantic result."""
    expected = yaml.safe_load(text)
    for e in edits:
        _apply_structural(expected, e)
    for e in edits:
        parsed = _ruamel().load(text)
        if e.op == "append":
            text = _splice_append(text, parsed, resolve(_to_plain(parsed), e.path), e.value)
        elif e.op == "set":
            plain = _to_plain(parsed)
            path = resolve(plain, e.path[:-1]) + [e.path[-1]]
            text = _splice_set(text, parsed, path, e.value)
        else:
            raise EditError(f"unknown edit op {e.op}")
    actual = yaml.safe_load(text)
    if actual != expected:
        raise EditError("edited YAML does not match the intended change; nothing was written")
    return text


def render_jsonl(records: list[dict]) -> str:
    import json
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records)


_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    return _SLUG.sub("-", name.lower()).strip("-") or "item"
