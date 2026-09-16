"""The build must refuse an image that carries a Wi-Fi network, a password or a token.

W11 gives the device a radio, so the firmware now has somewhere for a credential to be
written by mistake — and a firmware source is committed, published and built into the
release image. `scripts/check_credentials.py` runs before every `pio run` (the
`extra_scripts` line in firmware/cardputer/platformio.ini) and these tests are the same
check on the shapes a credential takes, plus the shapes that are *not* credentials and
must keep building."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_credentials as cc  # noqa: E402

FIRMWARE = ROOT / "firmware" / "cardputer"


def test_this_repository_carries_no_credential():
    assert cc.check(ROOT) == []


def test_the_build_runs_the_check_before_it_compiles_anything():
    ini = (FIRMWARE / "platformio.ini").read_text()
    assert "extra_scripts = pre:pio_check_credentials.py" in ini
    pre = (FIRMWARE / "pio_check_credentials.py").read_text()
    assert "from check_credentials import check" in pre and "Exit(1)" in pre


def test_an_assignment_a_define_and_a_radio_call_are_all_caught():
    source = """
      const char *WIFI_SSID = "TheRouter";
      #define WIFI_PASSWORD "hunter2"
      static char psk[64] = "hunter2";
      void go() { WiFi.begin("TheRouter", WIFI_PASSWORD); }
      void save() { store.putString("sec", "hunter2"); }
    """
    found = cc.findings_in(source, "src/x.cpp")
    assert len(found) == 5
    assert all("src/x.cpp:" in line for line in found)


def test_a_credential_in_the_build_file_is_caught_too():
    ini = "build_flags =\n  -DWIFI_PASS=hunter2\n  -DARDUINO_USB_MODE=1\n"
    found = cc.findings_in(ini, "platformio.ini")
    assert len(found) == 1 and "WIFI_PASS" in found[0]


def test_the_firmwares_own_shapes_are_not_credentials():
    """An empty buffer is the absence of a credential, a key name is a key name, and the
    token header is a header. If this test failed, the check would refuse its own tree."""
    source = """
      char g_network[33] = "";
      char g_secret[65] = "";
      store.getString("sec", g_secret, sizeof g_secret);
      store.putString(key, value);
      http.addHeader("X-Reflex-Device-Token", g_token);
      WiFi.begin(g_network, g_secret);
      const char *kStore = "c3s-net";
      // WIFI_PASSWORD = "not-a-credential-in-a-comment"
    """
    assert cc.findings_in(source, "src/x.cpp") == []


def test_the_message_says_what_to_do_instead():
    found = cc.findings_in('const char *ssid = "TheRouter";', "src/x.cpp")
    assert "type it on the device instead" in found[0]
