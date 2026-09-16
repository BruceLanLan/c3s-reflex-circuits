"""`c3s pair` puts the phone's own viewer token in the QR — never the operator token.

Before 2026-09-17 the fragment carried the operator token, and the page then re-sent it on
every poll (docs/REDTEAM-2026-09-17.md F1). The CLI is driven in-process with the console
client stubbed: no console, no network, no QR renderer output to parse.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from c3s_cli import cli, paths  # noqa: E402
from c3s_cli import console_client as client  # noqa: E402

OPERATOR = "op-secret-that-must-stay-home"
VIEWER = "viewer-token-for-this-phone"


@pytest.fixture
def stubbed(monkeypatch):
    calls = []

    def fake_call(method, path, port, body=None, token=None, timeout=10.0):
        calls.append((method, path, body, token))
        if path == "/api/device/viewer":
            return 200, {"device_id": "phone:abc123", "token": VIEWER, "kind": "phone"}
        if path == "/api/device/forget":
            return 200, {"forgotten": True, "device_id": body["device_id"]}
        return 404, {"error": "no such path"}

    monkeypatch.setattr(client, "_call", fake_call)
    monkeypatch.setattr(client, "operator_token", lambda: OPERATOR)
    monkeypatch.setattr(paths, "lan_ipv4", lambda: ["192.168.1.23"])
    monkeypatch.setattr(cli, "_bound_host", lambda: "0.0.0.0")
    monkeypatch.setattr(cli.qr, "render", lambda url, level="M", invert=False: "<qr>")
    return calls


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_pairing_url_carries_the_viewer_token_in_the_fragment_and_never_the_operator_token():
    url = cli._pairing_url(8765, VIEWER, "192.168.1.23")
    assert url == f"http://192.168.1.23:8765/#approvals&viewer={VIEWER}"
    assert "?" not in url and "token=" not in url
    assert cli._pairing_url(8765, None, "192.168.1.23") == "http://192.168.1.23:8765/#approvals"


def test_pair_issues_a_viewer_token_through_the_console_and_keeps_the_operator_token_off_the_link(stubbed, capsys):
    code, out, err = run(capsys, "--port", "8765", "pair")
    assert code == 0, err
    assert OPERATOR not in out                                   # the person's secret is not in the QR, or on screen
    assert f"#approvals&viewer={VIEWER}" in out
    assert [c[1] for c in stubbed] == ["/api/device/viewer"]     # minted by the console, not written here
    assert stubbed[0][3] == OPERATOR                             # with the operator token, over loopback
    assert "phone:abc123" in out and "forget" in out             # how to revoke this phone
    assert "cleartext" in out and "once" not in out.lower()      # the caveat is the true one


def test_pair_no_token_puts_nothing_in_the_link_and_asks_the_console_for_nothing(stubbed, capsys):
    code, out, _ = run(capsys, "pair", "--no-token")
    assert code == 0
    assert "#approvals" in out and "viewer=" not in out and OPERATOR not in out
    assert stubbed == []


def test_pair_forget_revokes_through_the_console(stubbed, capsys):
    code, out, _ = run(capsys, "pair", "--forget", "phone:abc123")
    assert code == 0 and "forgot phone:abc123" in out
    assert stubbed == [("POST", "/api/device/forget", {"device_id": "phone:abc123"}, OPERATOR)]


def test_pair_refuses_to_print_a_tokenless_link_by_accident_when_the_console_is_down(stubbed, capsys, monkeypatch):
    def down(*a, **k):
        raise client.ConsoleDown("no console on 127.0.0.1:8765")

    monkeypatch.setattr(client, "_call", down)
    code, out, err = run(capsys, "pair")
    assert code != 0 and "--no-token" in err and OPERATOR not in out + err
