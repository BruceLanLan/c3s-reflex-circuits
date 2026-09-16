"""Talking to a running console over HTTP, the way the page does.

Always to 127.0.0.1, never to the LAN address, so the `Host` header is a loopback name the
console accepts whatever it is bound to, and the operator token never leaves this machine.
"""

from __future__ import annotations

import http.client
import json
import os
import time

from . import paths


class ConsoleDown(Exception):
    """No console is answering on that port."""


def _call(method: str, path: str, port: int, body: dict | None = None,
          token: str | None = None, timeout: float = 10.0) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    headers = {}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Reflex-Token"] = token
    try:
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        try:
            return response.status, json.loads(raw or b"{}")
        except ValueError:
            return response.status, {"error": raw.decode("utf-8", "replace")[:200]}
    except OSError as e:
        raise ConsoleDown(f"no console on 127.0.0.1:{port} ({e})") from e
    finally:
        conn.close()


def state(port: int | None = None, timeout: float = 10.0) -> dict:
    status, body = _call("GET", "/api/state", port or paths.default_port(), timeout=timeout)
    if status != 200:
        raise ConsoleDown(f"the console answered {status}: {body.get('error', body)}")
    return body


def is_up(port: int | None = None, timeout: float = 2.0) -> bool:
    try:
        state(port, timeout=timeout)
        return True
    except (ConsoleDown, Exception):
        return False


def wait_until_up(port: int, deadline_s: float = 90.0) -> float:
    """Poll /api/state until it answers 200. Returns the seconds it took.

    The first start compiles the circuits and checks every rule on every row of their
    domain, which is the slow part; later starts reload the same rules and do it again.
    """
    started = time.monotonic()
    while time.monotonic() - started < deadline_s:
        try:
            state(port, timeout=2.0)
            return time.monotonic() - started
        except Exception:
            time.sleep(0.2)
    raise ConsoleDown(f"the console did not answer on :{port} within {deadline_s:.0f}s — see {paths.LOG_FILE}")


def serves_pairing_path(port: int) -> bool:
    """Does this console serve the page under `/?token=…`, the path the QR points at?

    `do_GET` matches the request path exactly, so a console that has not had the pairing
    route added answers 404 to `/?token=…` and a phone that scanned the QR sees nothing.
    Checked rather than assumed, so `c3s up` and `c3s pair` can say which is the case.
    """
    try:
        status, _ = _call("GET", "/?token=probe", port, timeout=3.0)
        return status == 200
    except Exception:
        return False


def operator_token() -> str | None:
    """The operator token, from the environment or the file the console writes."""
    env = os.environ.get("REFLEX_OPERATOR_TOKEN")
    if env:
        return env
    if paths.TOKEN_FILE.is_file():
        text = paths.TOKEN_FILE.read_text().strip()
        return text or None
    return None


def write_person_bit(agent: str, bits: dict, port: int, token: str,
                     for_reason: str | None = None) -> tuple[int, dict]:
    """POST /api/tool with a person's bit (blocked, confirm, …): needs the operator token."""
    body = {"agent": agent, **bits}
    if for_reason:
        body["for_reason"] = for_reason
    return _call("POST", "/api/tool", port, body=body, token=token)
