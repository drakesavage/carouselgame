"""Rolling transcript summaries so long campaigns don't resend the whole transcript.

After a committed turn, once more than `every` player-visible messages exist beyond the last summary *and*
outside the recent-message window, the oldest batch is summarized (one extra model call per batch)."""
from __future__ import annotations

import sqlite3

from ..db import now_iso, one, rows, transaction


def maybe_summarize(conn: sqlite3.Connection, cid: int, provider, every: int = 24, keep_recent: int = 12) -> int | None:
    last = one(conn, "SELECT COALESCE(MAX(to_message_id), 0) AS m FROM memory_summaries WHERE campaign_id = ?", (cid,))["m"]
    pending = rows(conn, """SELECT id, role, content FROM messages WHERE campaign_id = ? AND id > ? AND rolled_back = 0
                            AND visibility = 'player' ORDER BY id""", (cid, last))
    if len(pending) < every + keep_recent:
        return None
    batch = pending[:every]
    text = "\n".join(f"[{m['role'].upper()}] {m['content'][:2000]}" for m in batch)
    summary = provider.summarize(text)
    with transaction(conn):
        return conn.execute("INSERT INTO memory_summaries (campaign_id, from_message_id, to_message_id, summary, created_at) VALUES (?,?,?,?,?)",
                            (cid, batch[0]["id"], batch[-1]["id"], summary.strip()[:4000], now_iso())).lastrowid
