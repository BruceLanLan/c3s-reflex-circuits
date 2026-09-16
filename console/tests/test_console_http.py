"""Another site's page must not be able to use the console: no cross-origin POST, no
text/plain or form body, no request under a foreign Host name (DNS rebinding). The
adapters and the console's own page still get in. Runs against the live console."""

import http.client
import json

import pytest

HOST, PORT = "127.0.0.1", 8765


def post(path, body, headers):
    c = http.client.HTTPConnection(HOST, PORT, timeout=10)
    try:
        c.request("POST", path, body=body, headers=headers)
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


@pytest.fixture(autouse=True)
def console_up():
    try:
        c = http.client.HTTPConnection(HOST, PORT, timeout=3)
        c.request("GET", "/api/state")
        c.getresponse().read()
        c.close()
    except OSError as e:
        pytest.skip(f"console not running: {e}")


CONFIRM = json.dumps({"agent": "csrf-victim", "confirm": 1})


def test_a_text_plain_post_from_another_site_is_refused():
    status, _ = post("/api/tool", CONFIRM, {"Content-Type": "text/plain", "Origin": "https://evil.example"})
    assert status == 403


def test_a_text_plain_post_without_origin_is_refused():
    status, _ = post("/api/tool", CONFIRM, {"Content-Type": "text/plain"})
    assert status == 415


def test_a_json_post_from_another_origin_is_refused():
    status, _ = post("/api/tool", CONFIRM, {"Content-Type": "application/json", "Origin": "https://evil.example"})
    assert status == 403


def test_a_request_under_a_rebound_host_name_is_refused():
    status, _ = post("/api/tool", CONFIRM, {"Content-Type": "application/json", "Host": "evil.example:8765"})
    assert status == 403
    c = http.client.HTTPConnection(HOST, PORT, timeout=10)
    c.request("GET", "/api/state", headers={"Host": "evil.example:8765"})
    assert c.getresponse().status == 403
    c.close()


def test_the_consoles_own_page_and_the_adapters_get_in():
    for origin in (None, f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        status, body = post("/api/tool", json.dumps({"agent": "csrf-ok", "blocked": 0}), headers)
        assert status == 200, (origin, body)
