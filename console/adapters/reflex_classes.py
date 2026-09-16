"""Which class of circuit a tool call answers to: spend, message, exec or files.

One agent has one circuit per class of tool, because the rules for moving money, for
sending a message, for running a command and for touching a file are different rules.
The adapters decide the class from the tool's *name* — data the framework supplies,
never the model's account of itself — and send it with the request. Unknown tools land
in `exec`, which always has a circuit.

A deployment overrides or extends the list with a file (REFLEX_CLASS_FILE for the hook,
--class-file for the proxy): one `glob class` per line, `#` comments, first match wins,
a line `!default` keeps the built-in rules after your own. With neither the env var nor
the flag set, the file the console's "My tools" table writes is used when it exists
(`default_class_file()` below, under REFLEX_CONFIG_DIR) — so a person editing the table
does not also have to edit every adapter's environment.

A file is re-read when it changes, without restarting anything: `load_rules` hands back a
live view of the file, not a snapshot, so the long-lived MCP proxy (which loads its rules
once, at startup) answers the next call by the rules on disk now. The hook is a fresh
process per tool call and was always current.

Like the irreversible patterns, this is a heuristic on names. A tool that moves money
but is called `helper` is this layer's miss, not the circuit's.
"""

from __future__ import annotations

import fnmatch
import os
import time
from typing import Sequence

CLASSES = ("spend", "message", "exec", "files")
DEFAULT_CLASS = "exec"

# (glob on the bare tool name, class). Order matters: first match wins.
DEFAULT_RULES: tuple[tuple[str, str], ...] = (
    ("*transfer*", "spend"), ("*pay*", "spend"), ("*swap*", "spend"),
    ("*send_transaction*", "spend"), ("*sign*", "spend"), ("*withdraw*", "spend"), ("approve*", "spend"),
    ("send_*", "message"), ("reply*", "message"), ("post_*", "message"),
    ("create_message*", "message"), ("publish*", "message"), ("*send_email*", "message"),
    ("*send_message*", "message"),
    # A calendar write is a message: creating, moving or cancelling an event mails
    # everyone invited. W4's recorded run found these landing in `exec` and ungated,
    # which is the wrong class for a call other people receive.
    ("create_event*", "message"), ("update_event*", "message"), ("cancel_event*", "message"),
    ("invite*", "message"), ("respond_to_event*", "message"),
    ("write_*", "files"), ("move_*", "files"), ("delete_*", "files"), ("trash_*", "files"),
    ("overwrite*", "files"), ("truncate*", "files"), ("rename*", "files"),
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
    # Overwriting is not undoable, and the old contents are not anywhere afterwards.
    # `write_file*` and not `write_*`, so Claude Code's own `Write` keeps the hook's
    # path-aware judgement (inside the workspace: reversible; outside it: not).
    "write_file*", "overwrite*", "truncate*", "rename*",
    # A calendar invitation that went out cannot be recalled, and moving a meeting has
    # already told everyone. Same finding as the class rules above.
    "create_event*", "update_event*", "cancel_event*", "invite*", "respond_to_event*",
)


# ---- where the override files live, when nobody said -----------------------------------
#
# One path, agreed by the console (which writes the files) and the adapters (which read
# them). The console defaults its own CONFIG_DIR the same way, from REFLEX_CONFIG_DIR, so
# `c3s up` with a config dir of its own moves both sides together.

CONFIG_DIR_ENV = "REFLEX_CONFIG_DIR"
CLASS_FILE_NAME = "tool-classes.txt"
IRREVERSIBLE_FILE_NAME = "irreversible-tools.txt"


def config_dir() -> str:
    return os.path.expanduser(os.environ.get(CONFIG_DIR_ENV) or os.path.join("~", ".c3s-circuit-agent"))


def default_class_file() -> str:
    return os.path.join(config_dir(), CLASS_FILE_NAME)


def default_irreversible_file() -> str:
    return os.path.join(config_dir(), IRREVERSIBLE_FILE_NAME)


def _chosen(path: str | None, fallback: str) -> str | None:
    """The file to read: what the caller named, else the console's own file if it exists.

    An explicit path that does not exist is still returned, because it may appear later
    and a live view must pick it up then; a *default* that does not exist is None, so a
    deployment that never opened the table keeps exactly today's built-ins."""
    if path:
        return path
    return fallback if os.path.exists(fallback) else None


# ---- reading a list that may change under us -------------------------------------------


class _LiveList(Sequence):
    """The patterns in a file, re-read when the file changes.

    Returned instead of a tuple so that a process which loads its rules once — the MCP
    proxy does, at startup — decides the next call by what is on disk now. Every reader
    here only iterates, so a sequence is a drop-in: it compares equal to the tuple it
    would have been, and `tuple(...)` of it is a snapshot for anyone who wants one.

    `mtime_ns`, `size` and `inode` together are the version: an editor that replaces the
    file (os.replace, which is how the console writes it) changes the inode even when the
    clock has not moved. Stat at most every STAT_EVERY_S so a burst of calls is one stat.
    """

    STAT_EVERY_S = 0.5

    def __init__(self, path: str, parse, fallback: tuple) -> None:
        self._path, self._parse, self._fallback = path, parse, fallback
        self._version: object = None
        self._value: tuple = ()
        self._checked_at = 0.0
        self._current()

    def _stamp(self) -> object:
        try:
            st = os.stat(self._path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def _current(self) -> tuple:
        now = time.monotonic()
        if now - self._checked_at < self.STAT_EVERY_S and self._version is not None:
            return self._value
        self._checked_at = now
        stamp = self._stamp()
        if stamp != self._version or self._version is None:
            self._version = stamp
            self._value = self._fallback if stamp is None else self._parse(self._path, self._fallback)
        return self._value

    def __iter__(self):
        return iter(self._current())

    def __len__(self) -> int:
        return len(self._current())

    def __getitem__(self, i):
        return self._current()[i]

    def __eq__(self, other) -> bool:
        if isinstance(other, _LiveList):
            return tuple(self) == tuple(other)
        if isinstance(other, (tuple, list)):
            return tuple(self) == tuple(other)
        return NotImplemented

    def __hash__(self):
        return hash(tuple(self))

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._path!r}, {tuple(self)!r})"


def read_override_irreversible(path: str) -> tuple[tuple[str, ...], bool] | None:
    """The file's own patterns and whether it keeps the built-in list, or None if it could
    not be read. Separated from the built-ins so the console can say, per tool, whether
    the answer came from the user's file or from the list this project ships."""
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
        return None
    return tuple(own), keep_default


def _parse_irreversible(path: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    read = read_override_irreversible(path)
    if read is None:
        return fallback  # unreadable must not mean "nothing is irreversible"
    own, keep_default = read
    return own + (fallback if keep_default else ())


def load_irreversible_tools(path: str | None) -> Sequence[str]:
    chosen = _chosen(path, default_irreversible_file())
    if chosen is None:
        return DEFAULT_IRREVERSIBLE_TOOLS
    return _LiveList(chosen, _parse_irreversible, DEFAULT_IRREVERSIBLE_TOOLS)


def is_irreversible_tool(tool_name: str, patterns: Sequence[str] = DEFAULT_IRREVERSIBLE_TOOLS) -> bool:
    return matching_irreversible(tool_name, patterns) is not None


def bare_name(tool_name: str) -> str:
    """`mcp__server__tool` -> `tool`; anything else unchanged."""
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__", 2)
        if len(parts) == 3:
            return parts[2]
    return tool_name


def read_override_rules(path: str) -> tuple[tuple[tuple[str, str], ...], bool] | None:
    """The file's own `glob class` rules and whether it keeps the built-ins, or None if it
    could not be read. A malformed line is skipped here exactly as the adapters skip it."""
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
        return None
    return tuple(own), keep_default


def _parse_rules(path: str, fallback: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    read = read_override_rules(path)
    if read is None:
        return fallback  # an unreadable list must not silently put everything in exec
    own, keep_default = read
    return own + (fallback if keep_default else ())


def load_rules(path: str | None) -> Sequence[tuple[str, str]]:
    chosen = _chosen(path, default_class_file())
    if chosen is None:
        return DEFAULT_RULES
    return _LiveList(chosen, _parse_rules, DEFAULT_RULES)


def matching_rule_index(tool_name: str, rules: Sequence[tuple[str, str]]) -> int | None:
    """Which rule decides this name, by position — the one loop every caller shares.

    The full name is tried against every rule before the bare name is tried against any:
    `mcp__files__read_file` is first the whole name a deployment may have written a rule
    for, and only then `read_file`. Everything that needs to know which rule wins — the
    class, the pattern to show a person, whether that pattern came from the user's file —
    asks here, so the console's table cannot drift from the adapters' verdict.
    """
    for candidate in (tool_name, bare_name(tool_name)):
        for i, (pattern, _cls) in enumerate(rules):
            if fnmatch.fnmatchcase(candidate, pattern) or fnmatch.fnmatchcase(candidate.lower(), pattern.lower()):
                return i
    return None


def matching_rule(tool_name: str, rules: Sequence[tuple[str, str]] = DEFAULT_RULES) -> tuple[str, str] | None:
    """The rule that decides this name — the pattern as well as the class — or None when
    no rule matches and the name lands in DEFAULT_CLASS."""
    snapshot = list(rules)  # one read of a live file: the index and the rule must agree
    i = matching_rule_index(tool_name, snapshot)
    return snapshot[i] if i is not None else None


def matching_irreversible(tool_name: str, patterns: Sequence[str] = DEFAULT_IRREVERSIBLE_TOOLS) -> str | None:
    """The pattern that marks this name irreversible, or None. Same test as
    is_irreversible_tool, which is `matching_irreversible(...) is not None`."""
    name = bare_name(tool_name).lower()
    for p in patterns:
        if fnmatch.fnmatchcase(name, p.lower()):
            return p
    return None


def classify(tool_name: str, rules: Sequence[tuple[str, str]] = DEFAULT_RULES) -> str:
    hit = matching_rule(tool_name, rules)
    return hit[1] if hit else DEFAULT_CLASS


# -- where the console is, for the adapters' self-protection guard ---------------------
# Both adapters refuse a call aimed at the console's own host:port before asking any
# circuit. That guard is defence in depth — the operator token is the real defence — but
# it was keyed to literal spellings, and a red-team pass walked past it with `127.1`,
# the decimal form of 127.0.0.1, and a hardwired `:8765` in the stop rule
# (docs/REDTEAM-2026-09-17.md, F3). One definition now, so the two cannot drift apart.

LOOPBACK_ALIASES = (
    "127.0.0.1", "localhost", "0.0.0.0",
    # Spellings the shell and the resolver accept and a substring check does not:
    "127.1", "127.0.1", "2130706433", "0x7f000001", "017700000001",
    "[::1]", "::1", "ip6-localhost",
)


def console_port(console_url: str) -> str:
    from urllib.parse import urlsplit

    u = urlsplit(console_url)
    return str(u.port or (443 if u.scheme == "https" else 80))


def console_markers(console_url: str) -> tuple[str, ...]:
    """`host:port` spellings that mean "this console", lowercased for substring checks."""
    from urllib.parse import urlsplit

    port = console_port(console_url)
    hosts = {(urlsplit(console_url).hostname or "127.0.0.1").lower(), *LOOPBACK_ALIASES}
    return tuple(sorted({f"{h}:{port}" for h in hosts}))
