"""Which class of circuit a tool call answers to: spend, message, exec or files.

One agent has one circuit per class of tool, because the rules for moving money, for
sending a message, for running a command and for touching a file are different rules.
The adapters decide the class from the tool's *name* — data the framework supplies,
never the model's account of itself — and send it with the request. Unknown tools land
in `exec`, which always has a circuit.

A deployment overrides or extends the list with a file (REFLEX_CLASS_FILE for the hook,
--class-file for the proxy): one `glob class` per line, `#` comments, first match wins,
a line `!default` keeps the built-in rules after your own.

Like the irreversible patterns, this is a heuristic on names. A tool that moves money
but is called `helper` is this layer's miss, not the circuit's.
"""

from __future__ import annotations

import fnmatch

CLASSES = ("spend", "message", "exec", "files")
DEFAULT_CLASS = "exec"

# (glob on the bare tool name, class). Order matters: first match wins.
DEFAULT_RULES: tuple[tuple[str, str], ...] = (
    ("*transfer*", "spend"), ("*pay*", "spend"), ("*swap*", "spend"),
    ("*send_transaction*", "spend"), ("*sign*", "spend"), ("*withdraw*", "spend"), ("approve*", "spend"),
    ("send_*", "message"), ("reply*", "message"), ("post_*", "message"),
    ("create_message*", "message"), ("publish*", "message"), ("*send_email*", "message"),
    ("*send_message*", "message"),
    ("write_*", "files"), ("move_*", "files"), ("delete_*", "files"), ("trash_*", "files"),
    ("create_file*", "files"), ("edit_file*", "files"), ("create_directory*", "files"),
    ("Write", "files"), ("Edit", "files"), ("MultiEdit", "files"), ("NotebookEdit", "files"),
)


# Tool names whose effect cannot be taken back once it happens: a sent message, a
# deleted or moved file, money that left. Matched on the bare name, like the classes.
# The adapters arm the tool-layer bit `irreversible` for these before asking, so a
# policy with `confirm_per_irreversible` needs a person's confirm for each one. A
# heuristic on names, as everything in this file is: extend it with a file
# (REFLEX_IRREVERSIBLE_TOOLS_FILE, one glob per line, `!default` keeps these).
DEFAULT_IRREVERSIBLE_TOOLS: tuple[str, ...] = (
    "send_*", "reply*", "post_*", "publish*", "create_message*", "*send_email*", "*send_message*",
    "forward*", "*_send", "delete_*", "trash_*", "move_*", "remove_*", "*transfer*", "*withdraw*",
    "*swap*", "*pay*", "*send_transaction*", "*sign_transaction*", "approve*",
)


def load_irreversible_tools(path: str | None) -> tuple[str, ...]:
    if not path:
        return DEFAULT_IRREVERSIBLE_TOOLS
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
        return DEFAULT_IRREVERSIBLE_TOOLS  # unreadable must not mean "nothing is irreversible"
    return tuple(own) + (DEFAULT_IRREVERSIBLE_TOOLS if keep_default else ())


def is_irreversible_tool(tool_name: str, patterns: tuple[str, ...] = DEFAULT_IRREVERSIBLE_TOOLS) -> bool:
    name = bare_name(tool_name).lower()
    return any(fnmatch.fnmatchcase(name, p.lower()) for p in patterns)


def bare_name(tool_name: str) -> str:
    """`mcp__server__tool` -> `tool`; anything else unchanged."""
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__", 2)
        if len(parts) == 3:
            return parts[2]
    return tool_name


def load_rules(path: str | None) -> tuple[tuple[str, str], ...]:
    if not path:
        return DEFAULT_RULES
    own, keep_default = [], False
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.split("#", 1)[0].strip()
                if not line:
                    continue
                if line == "!default":
                    keep_default = True
                    continue
                parts = line.split()
                if len(parts) != 2 or parts[1] not in CLASSES:
                    continue  # a bad line is skipped, not fatal: the built-ins still apply below
                own.append((parts[0], parts[1]))
    except OSError:
        return DEFAULT_RULES  # an unreadable list must not silently put everything in exec
    return tuple(own) + (DEFAULT_RULES if keep_default else ())


def classify(tool_name: str, rules: tuple[tuple[str, str], ...] = DEFAULT_RULES) -> str:
    for candidate in (tool_name, bare_name(tool_name)):
        for pattern, cls in rules:
            if fnmatch.fnmatchcase(candidate, pattern) or fnmatch.fnmatchcase(candidate.lower(), pattern.lower()):
                return cls
    return DEFAULT_CLASS
