#!/usr/bin/env python3
"""Register a chat as an automation target.

Deterministic steps only. The script pulls past messages and stores
them; any LLM-authored work (reading the history, noting style) is
left to the Claude session.

Steps:
  1. Pull past messages via the adapter.
  2. Backfill messages table with state='learned' so get-context works
     from cycle 0.
  3. Upsert the target row. persona_path points at an empty file
     state/personas/<chat_id>.md — user-editable free-form overrides,
     may stay empty.
  4. Emit a JSON summary.

Usage:
  register.py --chat-id "<id>" --display-name "<UI label>" \
              [--platform kakao] [--history-limit 500]
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ["python3", str(ROOT / "scripts" / "db.py")]
ADAPTERS = ROOT / "scripts" / "adapters"


def sh(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True,
                          check=True).stdout


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--chat-id", required=True)
    p.add_argument("--display-name", required=True)
    p.add_argument("--platform", default="kakao")
    p.add_argument("--history-limit", type=int, default=500)
    args = p.parse_args()

    sh(DB + ["init"])

    adapter = ADAPTERS / f"{args.platform}.sh"
    raw = sh([str(adapter), "history", args.chat_id, str(args.history_limit)])
    msgs = json.loads(raw) if raw.strip() else []
    msgs = sorted(msgs, key=lambda m: m.get("timestamp") or m.get("sent_at") or "")

    # Normalize to db.py ingest-bulk shape and delegate. Single-writer
    # principle: register never opens app.db directly.
    normalized = []
    for m in msgs:
        ts = m.get("timestamp") or m.get("sent_at") or ""
        msg_id = str(m.get("id") or m.get("msg_id") or "")
        direction = "out" if (m.get("is_from_me") or m.get("direction") == "out") else "in"
        normalized.append({
            "platform_msg_id": msg_id,
            "direction": direction,
            "sender": m.get("sender") or "",
            "body": m.get("text") or m.get("body") or "",
            "sent_at": ts,
        })

    bulk = subprocess.run(
        DB + ["ingest-bulk", "--chat-id", args.chat_id,
              "--platform", args.platform, "--state", "learned"],
        input=json.dumps(normalized, ensure_ascii=False),
        capture_output=True, text=True, check=True,
    )
    ingest_result = json.loads(bulk.stdout)

    # Initial cursor: latest seen timestamp so poll.py only picks up *new*.
    if msgs:
        latest_ts = max((m.get("timestamp") or m.get("sent_at") or "") for m in msgs)
        latest_id = str(msgs[-1].get("id") or msgs[-1].get("msg_id") or "")
        sh(DB + [
            "set-cursor",
            "--chat-id", args.chat_id,
            "--last-seen-at", latest_ts,
            "--last-seen-msg-id", latest_id,
        ])

    persona_rel = f"state/personas/{args.chat_id}.md"
    persona_abs = ROOT / persona_rel
    persona_abs.parent.mkdir(parents=True, exist_ok=True)
    if not persona_abs.exists():
        persona_abs.write_text("", encoding="utf-8")

    sh(DB + [
        "add-target",
        "--chat-id", args.chat_id,
        "--platform", args.platform,
        "--display-name", args.display_name,
        "--baseline-count", str(len(msgs)),
        "--persona-path", persona_rel,
    ])

    out_msgs = [m for m in msgs if (m.get("is_from_me") or m.get("direction") == "out")]

    print(json.dumps({
        "ok": True,
        "chat_id": args.chat_id,
        "display_name": args.display_name,
        "platform": args.platform,
        "history_messages": len(msgs),
        "own_messages": len(out_msgs),
        "persona_path": persona_rel,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
