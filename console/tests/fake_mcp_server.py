#!/usr/bin/env python3
"""A minimal stdio MCP server for the proxy tests. Stdlib only.

Answers `initialize`, `tools/list` (two tools: `echo`, `delete_everything`) and
`tools/call` (echo returns its `text` argument; delete_everything returns "deleted").
Notifications are ignored; unknown methods get a -32601 error. Any line that is not
JSON is reported on stderr as `unparsed: <line>` so a test can see it arrived intact.
"""

import json
import sys

TOOLS = [
    {"name": "echo", "description": "Echo the text argument",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
    {"name": "delete_everything", "description": "Pretend to delete everything",
     "inputSchema": {"type": "object", "properties": {}}},
]


def reply(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def handle(msg: dict) -> None:
    method = msg.get("method")
    if "id" not in msg:
        return  # a notification
    rid = msg["id"]
    if method == "initialize":
        reply({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-mcp-server", "version": "0.0.1"}}})
    elif method == "tools/list":
        reply({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        params = msg.get("params") or {}
        name, args = params.get("name"), params.get("arguments") or {}
        if name == "echo":
            text = str(args.get("text", ""))
        elif name == "delete_everything":
            text = "deleted"
        else:
            reply({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"Unknown tool: {name}"}})
            return
        reply({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}], "isError": False}})
    else:
        reply({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Method not found: {method}"}})


def main() -> None:
    for raw in sys.stdin.buffer:
        try:
            msg = json.loads(raw)
        except ValueError:
            sys.stderr.write("unparsed: " + raw.decode("utf-8", "replace").rstrip("\n") + "\n")
            sys.stderr.flush()
            continue
        if isinstance(msg, dict):
            handle(msg)


if __name__ == "__main__":
    main()
