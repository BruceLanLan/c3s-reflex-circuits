"""Shared by the live-console tests: the operator token the console requires on its
person-only endpoints (install rules, write confirm/blocked/heartbeat), and which console
to talk to — so two checkouts can run their suites side by side on different ports."""

import os


def console_url() -> str:
    """REFLEX_CONSOLE, else CONSOLE_PORT on loopback, else the default port."""
    env = os.environ.get("REFLEX_CONSOLE")
    if env:
        return env.rstrip("/")
    return f"http://127.0.0.1:{os.environ.get('CONSOLE_PORT', '8765')}"


def console_netloc() -> tuple[str, int]:
    """(host, port) of the console under test, for the tests that speak raw HTTP."""
    netloc = console_url().split("//", 1)[-1].split("/", 1)[0]
    host, _, port = netloc.partition(":")
    return host or "127.0.0.1", int(port or 80)


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
