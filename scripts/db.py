#!/usr/bin/env python3
"""Single writer to state/app.db.

Why single-writer: UI-automation send path is non-idempotent (AppleScript
keystrokes). A crash mid-cycle must not leave orphan state. All writes
go through one process at a time via short-lived CLI invocations; WAL
lets readers coexist.

Subcommands are the only public surface. Other scripts shell out here;
they never open app.db directly.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "state" / "app.db"
SCHEMA_PATH = ROOT / "scripts" / "schema.sql"


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def cmd_init(args):
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with conn() as c:
        c.executescript(schema)
    print(json.dumps({"ok": True, "db": str(DB_PATH)}))


def cmd_list_targets(args):
    with conn() as c:
        rows = c.execute(
            """
            SELECT t.*,
                   (SELECT MAX(sent_at) FROM messages m WHERE m.chat_id = t.chat_id)
                     AS last_message_at
            FROM targets t
            ORDER BY last_message_at DESC NULLS LAST
            """
        ).fetchall()
    out = [dict(r) for r in rows]
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        for r in out:
            print(f"{r['display_name']}\t{r['platform']}\t{r['last_message_at']}")


def cmd_add_target(args):
    with conn() as c:
        c.execute(
            """
            INSERT INTO targets (chat_id, platform, display_name, registered_at,
                                 baseline_count, persona_path)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
              display_name=excluded.display_name,
              baseline_count=excluded.baseline_count,
              persona_path=excluded.persona_path
            """,
            (args.chat_id, args.platform, args.display_name, now_utc(),
             args.baseline_count, args.persona_path),
        )
    print(json.dumps({"ok": True, "chat_id": args.chat_id}))


def cmd_remove_target(args):
    with conn() as c:
        c.execute("DELETE FROM targets WHERE chat_id = ?", (args.chat_id,))
    print(json.dumps({"ok": True}))


def cmd_ingest(args):
    """Insert inbound message. Idempotent via UNIQUE(platform, platform_msg_id)."""
    with conn() as c:
        try:
            c.execute(
                """
                INSERT INTO messages (chat_id, platform, platform_msg_id,
                                      direction, sender, body, sent_at,
                                      ingested_at, state)
                VALUES (?, ?, ?, 'in', ?, ?, ?, ?, 'queued')
                """,
                (args.chat_id, args.platform, args.platform_msg_id,
                 args.sender, args.body, args.sent_at, now_utc()),
            )
            new_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            print(json.dumps({"ok": True, "id": new_id, "dup": False}))
        except sqlite3.IntegrityError:
            print(json.dumps({"ok": True, "dup": True}))


def cmd_ingest_bulk(args):
    """Bulk insert history messages. Reads JSON array from stdin; each item
    must have: platform_msg_id, direction, sender, body, sent_at.
    State is fixed by --state (default 'learned' for backfill).
    Duplicates are silently skipped (UNIQUE constraint).
    """
    payload = sys.stdin.read()
    items = json.loads(payload) if payload.strip() else []
    now = now_utc()
    inserted = 0
    with conn() as c:
        for m in items:
            try:
                c.execute(
                    """
                    INSERT INTO messages (chat_id, platform, platform_msg_id,
                                          direction, sender, body, sent_at,
                                          ingested_at, state)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (args.chat_id, args.platform, m.get("platform_msg_id") or "",
                     m["direction"], m.get("sender") or "", m.get("body") or "",
                     m["sent_at"], now, args.state),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                pass
    print(json.dumps({"ok": True, "inserted": inserted, "total": len(items)}))


def cmd_fetch_outbound(args):
    """Return a single outbound row joined with its target's display_name.
    Used by send.py to avoid opening app.db directly.
    """
    with conn() as c:
        row = c.execute(
            """
            SELECT m.id, m.chat_id, m.platform, m.draft_text, m.state,
                   t.display_name
            FROM messages m JOIN targets t ON t.chat_id = m.chat_id
            WHERE m.id = ? AND m.direction = 'out'
            """,
            (args.id,),
        ).fetchone()
    if not row:
        print(json.dumps({"ok": False, "error": "outbound not found"}))
        sys.exit(1)
    print(json.dumps(dict(row), ensure_ascii=False))


def cmd_list_pending(args):
    """Inbound messages waiting for a reply, with enough context to draft."""
    with conn() as c:
        rows = c.execute(
            """
            SELECT m.*, t.display_name, t.persona_path
            FROM messages m
            JOIN targets t ON t.chat_id = m.chat_id
            WHERE m.state = 'queued' AND m.direction = 'in'
            ORDER BY m.sent_at ASC
            LIMIT ?
            """,
            (args.limit,),
        ).fetchall()
    print(json.dumps([dict(r) for r in rows], ensure_ascii=False))


def cmd_get_context(args):
    """Recent N messages for a chat, oldest first, for reply drafting."""
    with conn() as c:
        rows = c.execute(
            """
            SELECT direction, sender, body, sent_at
            FROM messages
            WHERE chat_id = ?
            ORDER BY sent_at DESC
            LIMIT ?
            """,
            (args.chat_id, args.limit),
        ).fetchall()
    out = [dict(r) for r in reversed(rows)]
    print(json.dumps(out, ensure_ascii=False))


def cmd_draft(args):
    """Record a drafted outbound. Either --reply-to (ties to inbound) or
    --chat-id (proactive, no inbound)."""
    if not args.reply_to and not args.chat_id:
        print(json.dumps({"ok": False, "error": "need --reply-to or --chat-id"}))
        sys.exit(2)
    with conn() as c:
        if args.reply_to:
            inbound = c.execute(
                "SELECT chat_id, platform FROM messages WHERE id = ?",
                (args.reply_to,),
            ).fetchone()
            if not inbound:
                print(json.dumps({"ok": False, "error": "inbound not found"}))
                sys.exit(1)
            chat_id, platform, reply_to = inbound["chat_id"], inbound["platform"], args.reply_to
        else:
            target = c.execute(
                "SELECT platform FROM targets WHERE chat_id = ?",
                (args.chat_id,),
            ).fetchone()
            if not target:
                print(json.dumps({"ok": False, "error": "target not found"}))
                sys.exit(1)
            chat_id, platform, reply_to = args.chat_id, target["platform"], None
        c.execute(
            """
            INSERT INTO messages (chat_id, platform, direction, body, sent_at,
                                  ingested_at, state, reply_to_id, draft_text)
            VALUES (?, ?, 'out', '', ?, ?, 'drafted', ?, ?)
            """,
            (chat_id, platform, now_utc(), now_utc(), reply_to, args.text),
        )
        new_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    print(json.dumps({"ok": True, "outbound_id": new_id}))


def cmd_mark(args):
    """Generic state transition. Used by send.py."""
    fields = {"state": args.state}
    if args.error:
        fields["error"] = args.error
    if args.state == "sent":
        fields["body"] = args.body or ""
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [args.id]
    with conn() as c:
        c.execute(f"UPDATE messages SET {sets} WHERE id = ?", vals)
        # When an outbound is sent, flip the inbound it replies to.
        if args.state == "sent":
            c.execute(
                """
                UPDATE messages SET state = 'responded'
                WHERE id = (SELECT reply_to_id FROM messages WHERE id = ?)
                """,
                (args.id,),
            )
    print(json.dumps({"ok": True}))


def cmd_get_cursor(args):
    with conn() as c:
        row = c.execute(
            "SELECT last_seen_at, last_seen_msg_id FROM cursors WHERE chat_id = ?",
            (args.chat_id,),
        ).fetchone()
    print(json.dumps(dict(row) if row else {}, ensure_ascii=False))


def cmd_set_cursor(args):
    with conn() as c:
        c.execute(
            """
            INSERT INTO cursors (chat_id, last_seen_at, last_seen_msg_id)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
              last_seen_at = excluded.last_seen_at,
              last_seen_msg_id = excluded.last_seen_msg_id
            """,
            (args.chat_id, args.last_seen_at, args.last_seen_msg_id),
        )
    print(json.dumps({"ok": True}))


def cmd_recover(args):
    """Reconcile orphan states on session start.

    If a row is stuck in 'sending' we don't know if AppleScript actually sent.
    Conservative default: mark failed with a note; human reviews.
    """
    with conn() as c:
        cur = c.execute(
            "UPDATE messages SET state = 'failed', error = 'orphaned sending on recovery' "
            "WHERE state = 'sending'"
        )
        n = cur.rowcount
    print(json.dumps({"ok": True, "orphans_marked_failed": n}))


def build_parser():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    s = sub.add_parser("list-targets")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_list_targets)

    s = sub.add_parser("add-target")
    s.add_argument("--chat-id", required=True)
    s.add_argument("--platform", default="kakao")
    s.add_argument("--display-name", required=True)
    s.add_argument("--baseline-count", type=int, default=0)
    s.add_argument("--persona-path")
    s.set_defaults(func=cmd_add_target)

    s = sub.add_parser("remove-target")
    s.add_argument("--chat-id", required=True)
    s.set_defaults(func=cmd_remove_target)

    s = sub.add_parser("ingest")
    s.add_argument("--chat-id", required=True)
    s.add_argument("--platform", default="kakao")
    s.add_argument("--platform-msg-id")
    s.add_argument("--sender")
    s.add_argument("--body", required=True)
    s.add_argument("--sent-at", required=True)
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("ingest-bulk",
                       help="Bulk insert history messages (JSON array on stdin)")
    s.add_argument("--chat-id", required=True)
    s.add_argument("--platform", default="kakao")
    s.add_argument("--state", default="learned",
                   choices=["learned", "queued", "responded", "skipped"])
    s.set_defaults(func=cmd_ingest_bulk)

    s = sub.add_parser("fetch-outbound",
                       help="Return one outbound row + display_name")
    s.add_argument("--id", type=int, required=True)
    s.set_defaults(func=cmd_fetch_outbound)

    s = sub.add_parser("list-pending")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_list_pending)

    s = sub.add_parser("get-context")
    s.add_argument("--chat-id", required=True)
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(func=cmd_get_context)

    s = sub.add_parser("draft")
    s.add_argument("--reply-to", type=int)
    s.add_argument("--chat-id")
    s.add_argument("--text", required=True)
    s.set_defaults(func=cmd_draft)

    s = sub.add_parser("mark")
    s.add_argument("--id", type=int, required=True)
    s.add_argument("--state", required=True,
                   choices=["sending", "sent", "failed", "skipped"])
    s.add_argument("--body")
    s.add_argument("--error")
    s.set_defaults(func=cmd_mark)

    s = sub.add_parser("get-cursor")
    s.add_argument("--chat-id", required=True)
    s.set_defaults(func=cmd_get_cursor)

    s = sub.add_parser("set-cursor")
    s.add_argument("--chat-id", required=True)
    s.add_argument("--last-seen-at", required=True)
    s.add_argument("--last-seen-msg-id")
    s.set_defaults(func=cmd_set_cursor)

    sub.add_parser("recover").set_defaults(func=cmd_recover)

    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
