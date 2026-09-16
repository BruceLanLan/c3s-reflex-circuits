#!/usr/bin/env python3
"""A Claude Code PreToolUse hook that asks the boundary console before a tool runs.

Install (user or project settings.json):

    {"hooks": {"PreToolUse": [{"matcher": "Bash|Write|Edit|mcp__.*",
        "hooks": [{"type": "command", "command": "python3 /path/to/claude_code_hook.py",
                   "timeout": 20}]}]}}

Each matched tool call is one tick on the agent's channel: request high, intent high,
with the tool name and a short description as the reason. If the circuit grants, this
hook says nothing and Claude Code's own permission flow applies unchanged — the
boundary only ever narrows what may run, never widens it. If the circuit refuses, the
call is denied and the reason is the rule's own words.

`blocked` and `confirm` are not touched here on purpose. They belong to a layer the
agent cannot write: a person arms them from the console page or POST /api/tool. A
refusal for want of confirmation says so, and the person, not the model, resolves it.

Environment:
    REFLEX_CONSOLE   console URL (default http://127.0.0.1:8765)
    REFLEX_AGENT     agent name (default claude-code:<session id prefix>)
    REFLEX_FAIL_OPEN set to 1 to let tools run when the console is unreachable.
                     Off by default: a boundary that fails open is not one.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

CONSOLE = os.environ.get("REFLEX_CONSOLE", "http://127.0.0.1:8765").rstrip("/")


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    sys.exit(2)  # exit 2 blocks even if the JSON were somehow not read


def describe(tool: str, args: dict) -> str:
    if tool == "Bash":
        return args.get("description") or args.get("command", "")
    if tool in ("Write", "Edit", "NotebookEdit", "Read"):
        return args.get("file_path", "")
    return ", ".join(f"{k}={v}" for k, v in list(args.items())[:3] if isinstance(v, (str, int, float)))


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)  # not a hook payload; take no decision
    tool = str(event.get("tool_name", ""))
    args = event.get("tool_input") or {}
    if not isinstance(args, dict):
        args = {}
    agent = os.environ.get("REFLEX_AGENT") or f"claude-code:{str(event.get('session_id', ''))[:8]}"
    reason = f"{tool}: {describe(tool, args)}"[:160]

    body = json.dumps({"agent": agent, "intent": 1, "reason": reason}).encode()
    req = urllib.request.Request(f"{CONSOLE}/api/request", data=body,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            verdict = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError) as e:
        if os.environ.get("REFLEX_FAIL_OPEN") == "1":
            sys.exit(0)
        deny(f"boundary console unreachable ({e}); refusing rather than running unchecked. "
             f"Start it, or set REFLEX_FAIL_OPEN=1 to accept running without it.")

    if verdict.get("granted"):
        sys.exit(0)  # no decision of our own: the normal permission flow applies
    why = "; ".join(verdict.get("why") or []) or "the circuit did not grant"
    hint = ""
    if any(w.startswith("no confirmation") for w in verdict.get("why") or []):
        hint = " A person can arm a confirmation from the console page; the model cannot."
    if any(w.startswith("blocked") for w in verdict.get("why") or []):
        hint = " The tool layer has blocked this agent; only it can lift that."
    deny(f"refused by the boundary at tick {verdict.get('tick')}: {why}.{hint}")


if __name__ == "__main__":
    main()
