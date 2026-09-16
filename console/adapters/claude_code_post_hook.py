#!/usr/bin/env python3
"""The tool layer for one bit: `failed`. Runs after a tool call, reports whether it
failed, decides nothing.

Wire it on both events, because Claude Code splits them — `PostToolUse` fires after a
tool call succeeds, `PostToolUseFailure` after one fails (code.claude.com/docs/en/hooks,
"hook lifecycle"). A Bash command that exits non-zero can still arrive as PostToolUse
with `tool_response.exit_code != 0` / `is_error: true`; Write and Edit report
`success: false` and an `error` string. All of those count as a failure here.

    {"hooks": {
      "PostToolUse":        [{"matcher": "Bash|Write|Edit", "hooks": [{"type": "command",
                              "command": "python3 /path/to/claude_code_post_hook.py"}]}],
      "PostToolUseFailure": [{"matcher": "Bash|Write|Edit", "hooks": [{"type": "command",
                              "command": "python3 /path/to/claude_code_post_hook.py"}]}]}}

On a failure it arms `failed` for this agent on the console; the agent's next tick reads
it and the console clears it. Under `trip_after_failures=k`, k such ticks in a row trip
the breaker and nothing is granted until a person confirms. The verdict about "did this
fail" comes from the framework's own report of the tool result, never from the model,
which is what makes it a tool-layer bit — with the same honest limit as the irreversible
patterns: a failure the framework does not surface as one is this layer's miss.

This hook prints nothing and always exits 0. The tool has already run; a post hook
cannot and must not pretend otherwise.

Environment: REFLEX_CONSOLE, REFLEX_AGENT as for claude_code_hook.py.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

CONSOLE = os.environ.get("REFLEX_CONSOLE", "http://127.0.0.1:8765").rstrip("/")


def failed(event: dict) -> bool:
    if event.get("hook_event_name") == "PostToolUseFailure":
        return True
    r = event.get("tool_response")
    if not isinstance(r, dict):
        return False
    if r.get("is_error") is True or r.get("success") is False:
        return True
    if r.get("error"):
        return True
    code = r.get("exit_code")
    return isinstance(code, int) and code != 0


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    if not failed(event):
        return
    agent = os.environ.get("REFLEX_AGENT") or f"claude-code:{str(event.get('session_id', ''))[:8]}"
    body = json.dumps({"agent": agent, "failed": 1}).encode()
    req = urllib.request.Request(f"{CONSOLE}/api/tool", data=body,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except OSError:
        pass  # nothing to decide; a lost report is a lost report, and stderr is noise here


if __name__ == "__main__":
    main()
    sys.exit(0)
