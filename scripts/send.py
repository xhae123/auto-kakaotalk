#!/usr/bin/env python3
"""2-phase send.

Phase 1: flip the outbound row drafted -> sending (DB commits before IO).
Phase 2: call the adapter (AppleScript).
Phase 3: flip sending -> sent OR sending -> failed based on adapter exit.

If the process dies between phase 1 and phase 3, the row stays in
'sending' and is picked up by `db.py recover` at next session start,
which conservatively marks it failed. We prefer a false-negative
(human re-sends) over a duplicate (AppleScript sent twice).

Usage:
  send.py --outbound-id <id>
     or
  send.py --reply-to <inbound_id> --text "<draft>"   (reply, creates draft + sends)
     or
  send.py --chat-id <chat_id> --text "<draft>"       (proactive, no inbound)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADAPTERS = ROOT / "scripts" / "adapters"
DB = ["python3", str(ROOT / "scripts" / "db.py")]


def sh(cmd: list[str], check=True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def db_json(args: list[str]):
    return json.loads(sh(DB + args).stdout or "null")


def fetch_outbound(outbound_id: int) -> dict:
    """Delegate to db.py. Keep single-writer principle intact — send.py
    never opens app.db directly."""
    result = sh(DB + ["fetch-outbound", "--id", str(outbound_id)])
    row = json.loads(result.stdout)
    if not row.get("id"):
        raise SystemExit(f"outbound id {outbound_id} not found")
    return row


def send_one(outbound_id: int) -> dict:
    row = fetch_outbound(outbound_id)
    if row["state"] != "drafted":
        return {"ok": False, "skipped": True,
                "reason": f"state is {row['state']}, expected drafted"}

    # Phase 1: mark sending (commits)
    sh(DB + ["mark", "--id", str(outbound_id), "--state", "sending"])

    # Phase 2: actual IO
    adapter = ADAPTERS / f"{row['platform']}.sh"
    try:
        sh([str(adapter), "send", row["display_name"], row["draft_text"]])
    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or "adapter failed")[:500]
        sh(DB + ["mark", "--id", str(outbound_id),
                 "--state", "failed", "--error", err])
        return {"ok": False, "outbound_id": outbound_id, "error": err}

    # Phase 3: mark sent, promote body
    sh(DB + ["mark", "--id", str(outbound_id), "--state", "sent",
             "--body", row["draft_text"]])
    return {"ok": True, "outbound_id": outbound_id}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--outbound-id", type=int)
    p.add_argument("--reply-to", type=int)
    p.add_argument("--chat-id")
    p.add_argument("--text")
    args = p.parse_args()

    if args.reply_to and args.text:
        draft = db_json(["draft", "--reply-to", str(args.reply_to),
                         "--text", args.text])
        outbound_id = draft["outbound_id"]
    elif args.chat_id and args.text:
        draft = db_json(["draft", "--chat-id", args.chat_id,
                         "--text", args.text])
        outbound_id = draft["outbound_id"]
    elif args.outbound_id:
        outbound_id = args.outbound_id
    else:
        p.error("provide --outbound-id OR (--reply-to|--chat-id AND --text)")

    print(json.dumps(send_one(outbound_id), ensure_ascii=False))


if __name__ == "__main__":
    main()
