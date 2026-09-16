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

This hook is also the tool layer for one bit: `irreversible`. Before asking, it looks
at the call Claude Code is about to make — the tool name and arguments the framework
supplies, not anything the model says about itself — and if the call matches a pattern
for an action that cannot be undone (`rm`, `git push`, `git reset --hard`, `sudo`, a
Write outside the working directory, ...) it arms `irreversible` on the console first.
Under `confirm_per_irreversible` the circuit then refuses unless a person has left an
unspent confirm. That classification is the tool layer's promise, exactly the kind
docs/AGENT.md means: the circuit proves "irreversible and no confirm ⇒ no grant", and
this file is what decides which calls are irreversible. The pattern list is a
heuristic; a destructive command it does not recognise is this layer's miss, not the
circuit's, and a person who wants a stricter list writes one (REFLEX_IRREVERSIBLE_FILE).

`blocked`, `confirm` and `confirm_b` are never touched here. They belong to a person: the
console page or POST /api/tool. A refusal for want of confirmation says so, and the
person, not the model, resolves it. `failed` is armed by the companion PostToolUse hook.

Environment:
    REFLEX_CONSOLE            console URL (default http://127.0.0.1:8765)
    REFLEX_AGENT              agent name (default claude-code:<session id prefix>)
    REFLEX_FAIL_OPEN          set to 1 to let tools run when the console is unreachable.
                              Off by default: a boundary that fails open is not one.
    REFLEX_IRREVERSIBLE_FILE  one fnmatch glob per line, `#` comments; replaces the
                              built-in list unless a line reads `!default`, which keeps it
"""

from __future__ import annotations

import fnmatch
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reflex_classes import classify, is_irreversible_tool, load_irreversible_tools, load_rules  # noqa: E402  (sibling file)

CONSOLE = os.environ.get("REFLEX_CONSOLE", "http://127.0.0.1:8765").rstrip("/")

# Matched case-insensitively against the raw Bash command and against the describe()
# line. Globs, so `*` crosses spaces. Kept short on purpose: this is the floor, not the
# ceiling, and a deployment adds its own.
DEFAULT_IRREVERSIBLE = (
    "rm *", "* rm *", "*rm -r*", "*rm -f*",
    "git push*", "* git push*", "git reset --hard*", "* git reset --hard*",
    "git clean*", "* git clean*", "git branch -D*", "git checkout -- *",
    "*--force*", "*--hard*",
    "sudo *", "* sudo *",
    "curl *| *sh*", "wget *| *sh*",
    "chmod -R *", "chown -R *",
    "*drop table*", "*drop database*", "*truncate *", "*delete from *",
    "mkfs*", "dd if=*", "*> /dev/sd*", "*> /dev/disk*", "*> /dev/nvme*",
)


def irreversible_patterns() -> tuple[str, ...]:
    path = os.environ.get("REFLEX_IRREVERSIBLE_FILE")
    if not path:
        return DEFAULT_IRREVERSIBLE
    own, keep_default = [], False
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.split("#", 1)[0].strip()
                if line == "!default":
                    keep_default = True
                elif line:
                    own.append(line)
    except OSError:
        return DEFAULT_IRREVERSIBLE  # an unreadable list must not silently mean "nothing is irreversible"
    return (DEFAULT_IRREVERSIBLE if keep_default else ()) + tuple(own)


def is_irreversible(tool: str, args: dict, cwd: str) -> bool:
    """Whether the call Claude Code is about to make cannot be undone, by pattern."""
    if tool in ("Write", "Edit", "NotebookEdit", "MultiEdit"):
        path = os.path.realpath(str(args.get("file_path", "")))
        root = os.path.realpath(cwd) if cwd else ""
        return bool(root) and not (path == root or path.startswith(root + os.sep))
    if tool == "Bash":
        texts = [str(args.get("command", "")).lower(), describe(tool, args).lower()]
        pats = irreversible_patterns()
        return any(fnmatch.fnmatchcase(t, p.lower()) for t in texts for p in pats)
    if tool.startswith("mcp__"):
        return is_irreversible_tool(tool, load_irreversible_tools(os.environ.get("REFLEX_IRREVERSIBLE_TOOLS_FILE")))
    return False


def arm(agent: str, **bits: int) -> None:
    """Write tool-layer bits. Only ever called with `irreversible` from this file."""
    body = json.dumps({"agent": agent, **bits}).encode()
    req = urllib.request.Request(f"{CONSOLE}/api/tool", data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    sys.exit(2)  # exit 2 blocks even if the JSON were somehow not read


def describe(tool: str, args: dict) -> str:
    # The call itself, not the model's account of it: this line is what a person approves,
    # and a confirm is bound to it, so it must be the same when the same call comes back.
    # Bash's `description` is free text the model rewrites on every attempt.
    if tool == "Bash":
        return args.get("command", "")
    if tool in ("Write", "Edit", "NotebookEdit", "Read"):
        return args.get("file_path", "")
    return ", ".join(f"{k}={v}" for k, v in list(args.items())[:3] if isinstance(v, (str, int, float)))


# ---- the guard protects itself ------------------------------------------------------
#
# An agent with a shell on the same machine could otherwise write its own tool-layer bits
# (`curl localhost:8765/api/tool -d '{"confirm":1}'`), replace the installed rules, edit
# the file that keeps them, or rewrite the hook configuration that routes it here. Those
# calls are refused outright, before any circuit is asked, whatever the rules say. This is
# a pattern check on the call — the agent can still find a way this list does not name
# (another port forward, an encoded URL, a second user's shell). The structural answer is
# to run the agent where it cannot reach the console or these files at all: another OS
# user, a container, a sandbox without network to localhost.

def _console_markers() -> list[str]:
    from urllib.parse import urlsplit

    u = urlsplit(CONSOLE)
    port = u.port or (443 if u.scheme == "https" else 80)
    hosts = {u.hostname or "127.0.0.1", "127.0.0.1", "localhost", "0.0.0.0", "[::1]", "::1"}
    return sorted({f"{h}:{port}" for h in hosts})


GUARDED_PATHS = (".c3s-circuit-agent", "reflex-console", "cardputer_relay", "claude_code_hook", "claude_code_post_hook",
                 "mcp_proxy", "reflex_classes", ".claude/settings", "claude/settings.json", "REFLEX_")
GUARDED_API = ("/api/tool", "/api/policy")


def reaches_the_guard(tool: str, args: dict) -> str | None:
    """Why this call would tamper with the boundary itself, or None."""
    text = json.dumps(args, ensure_ascii=False)
    lower = text.lower()
    if any(m in lower for m in _console_markers()) and any(a in lower for a in GUARDED_API):
        return "it writes to the boundary console's tool-layer or rules endpoint"
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = str(args.get("file_path", ""))
        if any(g in path for g in GUARDED_PATHS):
            return f"it edits the boundary's own files or hook configuration ({path})"
    if tool == "Bash":
        cmd = str(args.get("command", ""))
        if any(a in cmd for a in GUARDED_API) and ("curl" in cmd or "wget" in cmd or "http" in cmd or "python" in cmd):
            return "it calls the boundary console's tool-layer or rules endpoint"
        writes = (">", "tee ", "sed -i", "mv ", "rm ", "cp ", "chmod ", "truncate", "python", "perl -i", "kill ")
        if any(g in cmd for g in GUARDED_PATHS) and any(w in cmd for w in writes):
            return "it would modify or stop the boundary's own files, process or hook configuration"
        if ("kill" in cmd or "pkill" in cmd) and ("console.py" in cmd or ":8765" in cmd or "lsof" in cmd):
            return "it would stop the boundary console"
    return None


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
    tamper = reaches_the_guard(tool, args)
    if tamper:
        deny(f"refused before any circuit was asked: {tamper}. The boundary's tool layer belongs to a person; "
             "an agent may not write its own confirm, change its rules, or stop its guard.")
    irreversible = is_irreversible(tool, args, str(event.get("cwd", "")))
    # Which circuit answers: one per class of tool (spend / message / exec / files),
    # decided from the tool's name — framework data, not the model's — and sent along.
    cls = classify(tool, load_rules(os.environ.get("REFLEX_CLASS_FILE")))
    reason = f"[{cls}] {tool}{' [irreversible]' if irreversible else ''}: {describe(tool, args)}"[:160]

    body = json.dumps({"agent": agent, "intent": 1, "reason": reason, "class": cls}).encode()
    req = urllib.request.Request(f"{CONSOLE}/api/request", data=body,
                                 headers={"content-type": "application/json"})
    try:
        if irreversible:
            arm(agent, irreversible=1)  # the bit describes the next tick, and that tick consumes it
        with urllib.request.urlopen(req, timeout=15) as resp:
            verdict = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError) as e:
        if os.environ.get("REFLEX_FAIL_OPEN") == "1":
            sys.exit(0)
        deny(f"boundary console unreachable ({e}); refusing rather than running unchecked. "
             f"Start it, or set REFLEX_FAIL_OPEN=1 to accept running without it.")

    if verdict.get("granted"):
        sys.exit(0)  # no decision of our own: the normal permission flow applies
    why_list = verdict.get("why") or []
    why = "; ".join(why_list) or "the circuit did not grant"
    hint = ""
    if any(w.startswith("no confirmation") for w in why_list):
        hint = " A person can arm a confirmation from the console page; the model cannot."
    if any(w.startswith("irreversible") for w in why_list):
        hint = (" This call looks irreversible and needs a fresh confirm from a person; the"
                " model cannot supply one. Do not retry until it has been given.")
    if any(w.startswith("breaker") for w in why_list):
        hint = " Too many consecutive failures; a person must reset the breaker. Stop and report."
    if any(w.startswith("blocked") or w.startswith("halted") for w in why_list):
        hint = " The tool layer has blocked this agent; only it can lift that."
    held = verdict.get("confirm_waiting_for") or []
    if held and not verdict.get("granted"):
        hint += (f" A person has approved one specific call, and this is not it: {held[0]!r}. If that is what you"
                 " meant, send exactly that call; any other call needs its own approval.")
    deny(f"refused by the boundary at tick {verdict.get('tick')}: {why}.{hint}")


if __name__ == "__main__":
    main()
