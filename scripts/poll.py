#!/usr/bin/env python3
"""Poll every registered target for new messages; ingest inbound into DB.

Idempotent: UNIQUE(platform, platform_msg_id) in messages table dedups.
Cursor advancement only happens AFTER successful ingest, so a crash
re-polls the same window without data loss.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADAPTERS = ROOT / "scripts" / "adapters"
DB = ["python3", str(ROOT / "scripts" / "db.py")]


def sh(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return r.stdout


def db_json(args: list[str]):
    return json.loads(sh(DB + args) or "null")


def poll_target(target: dict) -> int:
    platform = target["platform"]
    chat_id = target["chat_id"]
    adapter = ADAPTERS / f"{platform}.sh"

    cursor = db_json(["get-cursor", "--chat-id", chat_id]) or {}
    since = cursor.get("last_seen_at") or target["registered_at"]

    raw = sh([str(adapter), "poll", chat_id, since])
    msgs = json.loads(raw) if raw.strip() else []
    # helper returns most-recent-first sometimes; normalize oldest->newest.
    msgs = sorted(msgs, key=lambda m: m.get("timestamp") or m.get("sent_at") or "")

    ingested = 0
    latest_ts = since
    latest_id = cursor.get("last_seen_msg_id")

    for m in msgs:
        ts = m.get("timestamp") or m.get("sent_at")
        msg_id = str(m.get("id") or m.get("msg_id") or "")
        # Skip outbound (we sent them) and skip anything at/before cursor.
        if m.get("is_from_me") or m.get("direction") == "out":
            latest_ts = max(latest_ts, ts or "")
            latest_id = msg_id or latest_id
            continue
        if ts and ts <= since:
            continue

        sh(DB + [
            "ingest",
            "--chat-id", chat_id,
            "--platform", platform,
            "--platform-msg-id", msg_id,
            "--sender", m.get("sender") or "",
            "--body", m.get("text") or m.get("body") or "",
            "--sent-at", ts or "",
        ])
        ingested += 1
        latest_ts = max(latest_ts, ts or "")
        latest_id = msg_id or latest_id

    if latest_ts and latest_ts != since:
        sh(DB + [
            "set-cursor",
            "--chat-id", chat_id,
            "--last-seen-at", latest_ts,
            "--last-seen-msg-id", latest_id or "",
        ])
    return ingested


def main():
    targets = db_json(["list-targets", "--json"]) or []
    total = 0
    errors = []
    for t in targets:
        try:
            total += poll_target(t)
        except subprocess.CalledProcessError as e:
            errors.append({"chat_id": t["chat_id"],
                           "stderr": (e.stderr or "")[:500]})
    print(json.dumps({"ingested": total, "errors": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()
