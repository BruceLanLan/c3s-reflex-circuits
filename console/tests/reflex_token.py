"""Shared by the live-console tests: the operator token the console requires on its
person-only endpoints (install rules, write confirm/blocked/heartbeat)."""

import os


def operator_token() -> str:
    env = os.environ.get("REFLEX_OPERATOR_TOKEN")
    if env:
        return env
    path = os.path.expanduser(os.environ.get("REFLEX_TOKEN_FILE",
                              os.path.join(os.environ.get("REFLEX_CONFIG_DIR", "~/.c3s-circuit-agent"), "operator-token")))
    try:
        return open(path).read().strip()
    except OSError:
        return ""


def json_headers(extra: dict | None = None) -> dict:
    h = {"content-type": "application/json", **(extra or {})}
    tok = operator_token()
    if tok:
        h["x-reflex-token"] = tok
    return h
