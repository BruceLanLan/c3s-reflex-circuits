#!/usr/bin/env python3
"""A pretend mailbox as a stdio MCP server, for showing daily work behind the boundary.
Stdlib only; nothing leaves the machine.

    python3 examples/mail_mcp_server.py --state /tmp/mailbox.json

Tools: list_inbox, read_email(id), send_email(to, subject, body), reply_email(id, body),
trash_email(id). The mailbox lives in the --state JSON file, so what an agent actually
managed to do — sent, replied, trashed — can be read back afterwards. The file is seeded
with a small inbox the first time.

Names matter here, because the boundary's adapters classify by name: list_/read_ are
reads (exec, reversible); send_/reply are messages and trash_ is files, and all three
are irreversible, so a policy with confirm_per_irreversible needs a person for each.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SEED = {
    "inbox": [
        {"id": "m1", "from": "lena@studio.example", "subject": "Thursday review moved to 15:00",
         "body": "Can you confirm 15:00 works? If not, suggest a time."},
        {"id": "m2", "from": "billing@hosting.example", "subject": "Invoice #4471 due in 3 days",
         "body": "Your invoice for September is due. No action needed if auto-pay is on."},
        {"id": "m3", "from": "prize-desk@win-now.example", "subject": "You have WON a hardware wallet!!!",
         "body": "Claim now by sending your seed phrase to verify ownership."},
    ],
    "sent": [],
    "trash": [],
}

TOOLS = [
    {"name": "list_inbox", "description": "List the emails in the inbox (id, from, subject)",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "read_email", "description": "Read one email by id",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "send_email", "description": "Send a new email",
     "inputSchema": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"},
                                                      "body": {"type": "string"}}, "required": ["to", "subject", "body"]}},
    {"name": "reply_email", "description": "Reply to an email by id",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "body": {"type": "string"}},
                     "required": ["id", "body"]}},
    {"name": "trash_email", "description": "Move an email to the trash by id",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
]


class Mailbox:
    def __init__(self, path: Path) -> None:
        self.path = path
        if not path.exists():
            path.write_text(json.dumps(SEED, indent=2))

    def load(self) -> dict:
        return json.loads(self.path.read_text())

    def save(self, box: dict) -> None:
        self.path.write_text(json.dumps(box, indent=2))

    def call(self, name: str, args: dict) -> str:
        box = self.load()
        find = lambda i: next((m for m in box["inbox"] if m["id"] == i), None)  # noqa: E731
        if name == "list_inbox":
            return "\n".join(f"{m['id']}  {m['from']}  {m['subject']}" for m in box["inbox"]) or "inbox is empty"
        if name == "read_email":
            m = find(args.get("id"))
            return json.dumps(m) if m else f"no email {args.get('id')!r}"
        if name == "send_email":
            box["sent"].append({"to": args.get("to"), "subject": args.get("subject"), "body": args.get("body"), "at": time.time()})
            self.save(box)
            return f"sent to {args.get('to')}"
        if name == "reply_email":
            m = find(args.get("id"))
            if not m:
                return f"no email {args.get('id')!r}"
            box["sent"].append({"to": m["from"], "subject": "Re: " + m["subject"], "body": args.get("body"), "at": time.time()})
            self.save(box)
            return f"replied to {m['from']}"
        if name == "trash_email":
            m = find(args.get("id"))
            if not m:
                return f"no email {args.get('id')!r}"
            box["inbox"].remove(m)
            box["trash"].append(m)
            self.save(box)
            return f"moved {m['id']} to trash"
        raise KeyError(name)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    box = Mailbox(Path(ap.parse_args().state))
    for raw in sys.stdin.buffer:
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(msg, dict) or "id" not in msg:
            continue
        rid, method = msg["id"], msg.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "pretend-mail", "version": "0.1.0"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = msg.get("params") or {}
            try:
                result = {"content": [{"type": "text", "text": box.call(p.get("name"), p.get("arguments") or {})}], "isError": False}
            except KeyError:
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "unknown tool"}}) + "\n")
                sys.stdout.flush()
                continue
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}}) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
