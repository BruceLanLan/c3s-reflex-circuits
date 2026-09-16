"""The Cardputer as a physical confirm key: a relay between the console and the device's
Agent page, over USB serial or — with W11 — over the person's own Wi-Fi.

A model can write `request` and `intent`. It cannot press a key on a device on your
desk. So the device is the tool layer made physical: it shows what is waiting for a
person, and ENTER writes `confirm` (or `confirm_b`) for the selected agent, `b` writes
`blocked`, `u` lifts it. Nothing else crosses the wire in that direction.

Started by console.py when REFLEX_CARDPUTER is set (`1`/`auto` finds /dev/cu.usbmodem*,
or give a port). USB is the default and the stronger arrangement: over a cable the
relay runs in this process, no token exists to steal, and the firmware holds no
credentials. Over Wi-Fi the device carries a **device token** it was paired with (see
"Over Wi-Fi" below), and a provisioned device's flash holds that token and the Wi-Fi
password — so a flash dump of a provisioned device is a secret (docs/FIRMWARE.md in the
circuits repository).

Wire format, one ASCII line each, fields separated by `|` (stripped from the values):

    host -> device, on change and every 2 s
      S|<circuits>|<granted>|<refused>|<chain>|<agents blocked>
      I|<i>|<agent>|<class>|<tick>|<why>|<bit>|<armed>|<effect>  up to four items a person can resolve
                          <effect> is the one-line summary the adapter gave for what the
                          call would do (I-1), empty when it gave none. It is the ninth
                          field: firmware that reads eight ignores it.
      C|<i>|<code>|<agent>|<reason>                         Wi-Fi frames only (see below)
      L|<G or R>|<agent>|<class>|<why or reason>            the latest decision
      E|<items>                                             commit the frame
    device -> host
      K|confirm|<agent>|<i>   K|confirm_b|<agent>|<i>   K|block|<agent>   K|unblock|<agent>
                          <i> is the item the device had selected; the confirm is bound to that
                          item's call, so it cannot be spent on a different one
      K|stop_all|*        K|resume_all|*        (every agent the console knows)
      H|                  the device is present, every 2 s while a console listens

While the device's heartbeat is fresh the console treats it as the `heartbeat` bit for
every agent, so a halt policy with `heartbeat_ticks` stops them all when the device is
unplugged, reset or silent.

A key for an agent the host did not just show is ignored: the device can only act on
what a person could see on its screen.

Over Wi-Fi (W11)
----------------
The device never listens. It connects out to the console, polls the same frame this
file computes and posts a key event only when a physical key is pressed:

    GET  /api/device/frame     X-Reflex-Device-Token  -> the frame above, `wide` (with C| lines)
    POST /api/tool             X-Reflex-Device-Token  -> the person's bit, `for_reason` + `code`
    POST /api/device/pair                             -> claim the open pairing window

Nothing on the network can make the device write anything: there is no inbound server
on it, and the only HTTP it sends that writes is the one a key press produces.

The `C|` line carries what a *write* needs and the `I|` line cannot: the matching code
(I-2) and the agent name and reason as JSON string literals, `ensure_ascii=False` with
`|` escaped as \\u007c. The display fields in `I|` go through `clean()` — ASCII, and
truncated to fit 240 px — and a confirm binds on the reason *exactly*, so a device that
echoed the display text would have its confirm refused for any non-ASCII or long
reason. The device splices the two literals into its POST body verbatim and needs no
JSON writer of its own. These lines are sent only over Wi-Fi: a reason is up to 160
characters and the serial line buffer in the firmware is smaller than that.

A device token is exactly as powerful as the cable is, and no more: it is accepted on
`/api/tool` alone, for `confirm`, `confirm_b` or `blocked` for an agent that is in
`pending[]` right now, with a code that is live for that agent, and for nothing else —
no `/api/policy`, no `/api/stop-all`, no arbitrary agent. Lifting a block (`blocked: 0`)
and writing `heartbeat` are refused over the network: the heartbeat is the device's
presence, which the console works out for itself from the polls, and a dead-man's halt
that a network write could hold open is not a dead-man's halt. So no network, or no
Wi-Fi, never means "assume the person is present" — it means the heartbeat stops.
"""

from __future__ import annotations

import glob
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

try:
    import serial  # pyserial
except ImportError:  # the console runs without it; only this relay needs it
    serial = None

SHORT = {"spend": "sp", "message": "ms", "exec": "ex", "files": "fi", "halt": "ha"}


def clean(value, limit: int) -> str:
    text = str(value).replace("|", "/").replace("\n", " ").replace("\r", " ")
    return text.encode("ascii", "replace").decode("ascii")[:limit]


def pending_items(state: dict) -> list[dict]:
    """What waits for a person, as the console computed it (I-2: `pending` in /api/state).

    A thin shim, not a second derivation: it keeps only the entries a bit can resolve and
    flattens `why` into one string, which is the shape this device and `bot_telegram.py`
    already read. Everything about *which* entries wait, and their matching codes, now
    comes from one place — the console."""
    out = []
    for it in state.get("pending") or []:
        if it.get("waiting_on_time") or not it.get("bit"):
            continue
        why = it.get("why")
        out.append({**it, "why": "; ".join(why) if isinstance(why, list) else str(why or "")})
    return out


def json_literal(value: str) -> str:
    """A JSON string literal the device can splice into a request body without escaping
    anything itself, and that survives a `|`-separated line: `|` becomes \\u007c, which
    JSON reads back as `|`. Newlines and quotes json.dumps already escapes."""
    return json.dumps(str(value), ensure_ascii=False).replace("|", "\\u007c")


def frame(state: dict, wide: bool = False) -> tuple[list[str], list[dict]]:
    policies = state.get("policies", {})
    parts = []
    for cls in ("spend", "message", "exec", "files", "halt"):
        p = policies.get(cls)
        if p is None:
            parts.append(f"{SHORT[cls]}:-")
        elif p.get("deny_all"):
            parts.append(f"{SHORT[cls]}:DENY")
        else:
            parts.append(f"{SHORT[cls]}:{p['circuit']['nand']}+{p['circuit']['latch']}")
    requests = [e for e in state["transcript"] if e.get("kind") == "request"]
    granted = sum(1 for e in requests if e.get("granted"))
    chain = "off"
    if state.get("chain", {}).get("enabled"):
        latest = next((e for e in requests if e.get("chain")), None)
        chain = ("idle" if latest is None else "..." if latest["chain"].get("pending")
                 else "err" if latest["chain"].get("error") else "ok" if latest["chain"].get("agrees") else "DISAGREE")
    blocked = sum(1 for a in state.get("agents", []) if a.get("armed", {}).get("blocked"))
    lines = [f"S|{clean(' '.join(parts), 40)}|{granted}|{len(requests) - granted}|{chain}|{blocked}"]

    items = pending_items(state)[:4]
    for i, it in enumerate(items):
        # "given" on the device means a confirm is waiting for *this* call, not merely
        # armed; the console works that out (`armed`) along with the rest of the entry.
        is_armed = int(bool(it.get("armed")))
        effect = (it.get("effect") or {}).get("summary") or ""
        lines.append(f"I|{i}|{clean(it['agent'], 30)}|{it['class']}|{it['tick']}|{clean(it['why'], 60)}|{it['bit']}"
                     f"|{is_armed}|{clean(effect, 60)}")
        if wide:
            # What a write needs, next to the item it belongs to: the code, and the agent
            # and reason as they really are (`I|` carries display text, which is cleaned
            # and truncated, and a confirm binds on the reason exactly).
            lines.append(f"C|{i}|{it.get('code') or ''}|{json_literal(it['agent'])}|{json_literal(it.get('reason') or '')}")
    if requests:
        e = requests[0]
        said = "; ".join(e.get("why") or []) or e.get("reason", "")
        lines.append(f"L|{'G' if e.get('granted') else 'R'}|{clean(e['agent'], 30)}|{e.get('class', 'exec')}|{clean(said, 60)}")
    lines.append(f"E|{len(items)}")
    return lines, items


class Relay(threading.Thread):
    def __init__(self, boundary, port: str, log=print) -> None:
        super().__init__(daemon=True, name="cardputer-relay")
        self.boundary, self.want, self.log = boundary, port, log
        self.shown: list[dict] = []
        self.connected = False
        self.last_key, self.last_key_at = None, 0.0
        self.heard_at = 0.0
        boundary.heartbeat_source = self.present

    HEARTBEAT_FRESH_S = 5.0

    def present(self) -> bool:
        return self.connected and time.time() - self.heard_at < self.HEARTBEAT_FRESH_S

    def _port(self) -> str | None:
        if self.want not in ("1", "auto", "yes"):
            return self.want
        found = sorted(glob.glob("/dev/cu.usbmodem*"))
        return found[0] if found else None

    def _open(self):
        path = self._port()
        if not path:
            return None
        ser = serial.Serial()
        ser.port, ser.baudrate, ser.timeout = path, 115200, 0.2
        # Opening a USB CDC port with DTR/RTS asserted can reset an ESP32-S3.
        ser.dtr = False
        ser.rts = False
        ser.open()
        return ser

    def _key(self, line: str) -> None:
        parts = line.split("|")
        if len(parts) not in (3, 4) or parts[0] != "K":
            return
        action, agent = parts[1], parts[2]
        index = int(parts[3]) if len(parts) == 4 and parts[3].isdigit() else None
        if action in ("stop_all", "resume_all") and agent == "*":
            now = time.time()
            if self.last_key == (action, agent) and now - self.last_key_at < 0.5:
                return
            self.last_key, self.last_key_at = (action, agent), now
            value = 1 if action == "stop_all" else 0
            names = [a["agent"] for a in self.boundary.status()["agents"]]
            for name in names:
                self.boundary.arm(name, {"blocked": value})["source"] = "cardputer"
            self.log(f"cardputer: {action} for {len(names)} agent(s) (a person pressed a key)")
            return
        # The same key for the same agent twice within half a second is one press.
        now = time.time()
        if self.last_key == (action, agent) and now - self.last_key_at < 0.5:
            return
        self.last_key, self.last_key_at = (action, agent), now
        bits = {"confirm": {"confirm": 1}, "confirm_b": {"confirm_b": 1},
                "block": {"blocked": 1}, "unblock": {"blocked": 0}}.get(action)
        if bits is None:
            return
        # Block/unblock: any known agent. Confirm: only an item the device was just shown,
        # and bound to that item's call.
        known = {a["agent"] for a in self.boundary.status()["agents"]}
        bind_to = None
        if action in ("confirm", "confirm_b"):
            mine = [it for it in self.shown if it["agent"] == agent]
            if index is not None and 0 <= index < len(self.shown) and self.shown[index]["agent"] == agent:
                item = self.shown[index]
            else:
                item = mine[0] if mine else None  # older firmware sends no index: the newest item shown
            if item is None:
                self.log(f"cardputer: ignored {action} for {agent!r} (not on its screen)")
                return
            bind_to = item.get("reason") or None
        elif agent not in known:
            self.log(f"cardputer: ignored {action} for {agent!r} (unknown agent)")
            return
        entry = self.boundary.arm(agent, bits, bind_to)
        entry["source"] = "cardputer"
        self.log(f"cardputer: {action} for {agent} (a person pressed a key)")

    def run(self) -> None:
        if serial is None:
            self.log("cardputer: pyserial is not installed; relay off")
            return
        buf = b""
        while True:
            try:
                ser = self._open()
                if ser is None:
                    time.sleep(3)
                    continue
                self.connected = True
                self.log(f"cardputer: relaying on {ser.port}")
                last, sent = 0.0, None
                while True:
                    now = time.time()
                    if now - last >= 0.5:
                        lines, shown = frame(self.boundary.status())
                        # Send on change, and every 2 s as a heartbeat: less traffic for a
                        # device that is also animating.
                        if lines != sent or now - last >= 2.0:
                            ser.write(("\n".join(lines) + "\n").encode("ascii"))
                            sent, self.shown, last = lines, shown, now
                    chunk = ser.read(256)
                    if chunk:
                        buf += chunk
                        while b"\n" in buf:
                            raw, buf = buf.split(b"\n", 1)
                            text = raw.decode("ascii", "replace").strip()
                            if text.startswith("H|"):
                                self.heard_at = time.time()
                            elif text.startswith("K|"):
                                self._key(text)
                            elif text.startswith(("c3s escape core", "reflex arc", "digest ")):
                                self.log(f"cardputer says: {text}")
            except Exception as e:  # unplugged, reset, port busy (e.g. while flashing)
                if self.connected:
                    self.log(f"cardputer: lost the device ({e}); retrying")
                self.connected = False
                buf = b""
                time.sleep(3)


# ---- W11: the same key over Wi-Fi ------------------------------------------------------
#
# Everything below exists because a cable is a cable. The device stays a physical key: it
# connects out, it polls, and it posts only what a key press produced. What changes is that
# over Wi-Fi the console cannot see a cable, so the device has to say who it is — with a
# token it was paired with once, in front of the person, by matching a code.
#
# The rules the store and the two handlers enforce are the ones in the module docstring;
# they are written here rather than in console.py so that the console keeps one line per
# hook and this file stays the whole of the device's side.

CONFIG_DIR = Path(os.environ.get("REFLEX_CONFIG_DIR", Path.home() / ".c3s-circuit-agent")).expanduser()
DEVICES_FILE = Path(os.environ.get("REFLEX_DEVICES_FILE", CONFIG_DIR / "devices.json")).expanduser()

# The bits a paired device may write: the person's four, exactly the cable's set. The two
# that could only ever loosen a decision — lifting a block, and holding a dead-man's
# heartbeat open — are refused over the network all the same (see `device_tool`).
DEVICE_BITS = ("confirm", "confirm_b", "blocked", "heartbeat")
PAIR_WINDOW_S = 180.0        # how long a pairing window stays open for a device to claim
DEVICE_FRESH_S = 6.0         # a poll this recent means the device is present
DEVICES_LOCK = threading.Lock()

# Presence, in memory only: a heartbeat that survived a restart of the console would be a
# heartbeat that says "the person is there" about a device nobody has heard from.
_LAST_POLL: dict[str, float] = {}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pair_code(token: str) -> str:
    """The four digits both sides show, derived from the token itself, so that matching
    codes mean the two sides hold the same token — the I-2 idea, applied to pairing."""
    return f"{int(_sha256('c3s-pair:' + token)[:8], 16) % 10000:04d}"


def _read_devices() -> dict:
    """Re-read from disk every time, like the agent bindings: a `c3s pair-device` running
    in another process is the normal way in, and a pairing that only took effect after a
    restart would not be a pairing."""
    try:
        data = json.loads(DEVICES_FILE.read_text())
    except (OSError, ValueError):
        return {"devices": {}, "pending": None}
    if not isinstance(data, dict):
        return {"devices": {}, "pending": None}
    devices = data.get("devices") if isinstance(data.get("devices"), dict) else {}
    devices = {k: v for k, v in devices.items() if isinstance(v, dict) and isinstance(v.get("token_sha256"), str)}
    pending = data.get("pending") if isinstance(data.get("pending"), dict) else None
    return {"devices": devices, "pending": pending}


def _write_devices(data: dict) -> None:
    """Replaced whole and atomically, mode 600. It holds no token — only each token's
    SHA-256 — so a copy of this file cannot press the key."""
    DEVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DEVICES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"format": "c3s.console.devices/1", **data}, indent=2, sort_keys=True) + "\n")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    os.replace(tmp, DEVICES_FILE)


def _live_pending(data: dict) -> dict | None:
    p = data.get("pending")
    return p if p and float(p.get("expires_at", 0)) > time.time() else None


def device_pair_begin(minutes: float = PAIR_WINDOW_S / 60) -> dict:
    """Open a pairing window. A person does this — `c3s pair-device` (W1) calls it, and so
    does `POST /api/device/pair/begin` with the operator token. Outside a window the
    console hands out no token at all, so a device on the network cannot pair itself."""
    with DEVICES_LOCK:
        data = _read_devices()
        data["pending"] = {"opened_at": time.time(), "expires_at": time.time() + minutes * 60}
        _write_devices(data)
    return {"open": True, "seconds": int(minutes * 60),
            "next": "on the device: Network > Pair with console. Then read the four digits it shows and "
                    "run `c3s pair-device --confirm <digits>`."}


def device_pair_claim(device_id: str, name: str) -> dict:
    """The device asking for its token, inside an open window. One window, one claim: a
    second claimant is refused rather than queued, so that the four digits on the screen
    are unambiguously the ones belonging to the token this console is about to trust."""
    device_id = str(device_id or "")[:64].strip()
    name = str(name or "cardputer")[:40].strip() or "cardputer"
    if not device_id:
        return {"error": "a device has to say which device it is"}
    with DEVICES_LOCK:
        data = _read_devices()
        window = _live_pending(data)
        if window is None:
            return {"error": "no pairing window is open; a person opens one with `c3s pair-device` on the console"}
        if window.get("device_id"):
            return {"error": "this pairing window is already taken by another device; ask the person to open a new one"}
        token = secrets.token_urlsafe(24)
        code = pair_code(token)
        window.update({"device_id": device_id, "name": name, "token_sha256": _sha256(token),
                       "code": code, "claimed_at": time.time()})
        data["pending"] = window
        _write_devices(data)
    print(f"cardputer: {name!r} ({device_id}) claimed the pairing window; it is showing four digits. "
          f"Confirm they match with `c3s pair-device --confirm <digits>`.", flush=True)
    # The token goes back over the loopback-or-LAN reply to the device's own outbound
    # request, and is accepted for nothing until a person confirms the code.
    return {"device_id": device_id, "token": token, "code": code, "status": "provisional",
            "expires_at": window["expires_at"]}


def device_pair_confirm(code: str) -> dict:
    """The person types in the digits the *device* is showing. The console does not print
    them anywhere first: if it did, "do they match" could be answered without looking at
    the device, and the check would be theatre. One attempt — a wrong code closes the
    window, so four digits cannot be worked through."""
    given = str(code or "").strip()
    with DEVICES_LOCK:
        data = _read_devices()
        window = _live_pending(data)
        if window is None or not window.get("device_id"):
            return {"error": "nothing is waiting to be paired; open a window and claim it from the device first"}
        if not hmac.compare_digest(given, str(window.get("code", ""))):
            data["pending"] = None  # one attempt
            _write_devices(data)
            return {"error": "those are not the digits the device is showing. The pairing is cancelled — if you "
                              "did not expect a mismatch, something else claimed the window. Open a new one."}
        device_id = window["device_id"]
        data["devices"][device_id] = {"name": window.get("name", "cardputer"),
                                      "token_sha256": window["token_sha256"], "paired_at": time.time()}
        data["pending"] = None
        _write_devices(data)
    print(f"cardputer: paired {device_id} over Wi-Fi. It may write the person's bits for a call it can see, "
          f"and nothing else.", flush=True)
    return {"paired": True, "device_id": device_id, "devices": len(data["devices"])}


def device_pair_status() -> dict:
    """What the console will say about a pairing in progress — deliberately not the code."""
    with DEVICES_LOCK:
        data = _read_devices()
        window = _live_pending(data)
    return {"window_open": window is not None,
            "claimed_by": (window or {}).get("device_id"),
            "claimed_name": (window or {}).get("name"),
            "seconds_left": int(max(0, float((window or {}).get("expires_at", 0)) - time.time())),
            "devices": device_list()}


def device_list() -> list[dict]:
    data = _read_devices()
    return [{"device_id": k, "name": v.get("name"), "paired_at": v.get("paired_at"),
             "present": time.time() - _LAST_POLL.get(k, 0) < DEVICE_FRESH_S}
            for k, v in sorted(data["devices"].items())]


def device_forget(device_id: str) -> dict:
    """Drop a device's token. The device keeps the useless string until someone chooses
    Forget on it; there is no network write that tells it, on purpose."""
    with DEVICES_LOCK:
        data = _read_devices()
        gone = data["devices"].pop(str(device_id), None)
        if gone is None:
            return {"forgotten": False, "why": f"no device {device_id!r} is paired"}
        _write_devices(data)
    _LAST_POLL.pop(str(device_id), None)
    return {"forgotten": True, "device_id": device_id}


def device_auth(token: str) -> str | None:
    """Which paired device this token belongs to, or None. compare_digest over the hashes,
    as for an agent's token (I-3), so a wrong guess leaks nothing through timing."""
    token = (token or "").strip()
    if not token:
        return None
    want = _sha256(token)
    for device_id, record in _read_devices()["devices"].items():
        if hmac.compare_digest(want, str(record.get("token_sha256", ""))):
            return device_id
    return None


def device_present() -> bool:
    """True while some paired device has polled recently. This — not a bit the device can
    write — is the heartbeat over Wi-Fi: when the radio drops, the polls stop, and a halt
    policy with `heartbeat_ticks` stops every agent. Wi-Fi going away cannot mean "assume
    the person is there"."""
    now = time.time()
    return any(now - at < DEVICE_FRESH_S for at in _LAST_POLL.values())


def install_device_presence(boundary) -> None:
    """Add Wi-Fi presence to whatever already answers for presence (the USB relay sets
    itself as the source when it starts). Either link being live is the device being
    present; both being gone is the halt."""
    previous = boundary.heartbeat_source
    boundary.heartbeat_source = lambda: device_present() or bool(previous and previous())


# -- the two things a device does over HTTP ------------------------------------------------


def device_get(handler, boundary) -> bool:
    """`GET /api/device/*`. Returns True when it answered. Called from console.py."""
    if handler.path.split("?", 1)[0] != "/api/device/frame":
        handler._json(404, {"error": "no such path"})
        return True
    device_id = device_auth(handler.headers.get("X-Reflex-Device-Token") or "")
    if device_id is None:
        handler._json(403, {"error": "this device is not paired with the console; pair it from the device's "
                                     "Network page while a person has a pairing window open"})
        return True
    _LAST_POLL[device_id] = time.time()  # the poll *is* the heartbeat
    lines, _ = frame(boundary.status(), wide=True)
    handler._send(200, ("\n".join(lines) + "\n").encode("utf-8"), "text/plain; charset=utf-8")
    return True


def device_post(handler, payload: dict, boundary) -> bool:
    """`POST /api/device/*`. Pairing only — nothing here writes a bit."""
    if handler.path == "/api/device/pair":
        answer = device_pair_claim(payload.get("device_id", ""), payload.get("name", ""))
        handler._json(403 if answer.get("error") else 200, answer)
        return True
    if handler.path in ("/api/device/pair/begin", "/api/device/pair/confirm", "/api/device/forget"):
        # A person's actions, so the operator token, exactly as installing a rule is.
        if not handler._has_token(payload):
            handler._json(403, {"error": "pairing a device is the operator's: send the token (see the console's log)"})
            return True
        if handler.path == "/api/device/pair/begin":
            answer = device_pair_begin()
        elif handler.path == "/api/device/pair/confirm":
            answer = device_pair_confirm(payload.get("code", ""))
        else:
            answer = device_forget(payload.get("device_id", ""))
        handler._json(403 if answer.get("error") else 200, answer)
        return True
    handler._json(404, {"error": "no such path"})
    return True


def device_tool(handler, payload: dict, boundary) -> None:
    """A key press that arrived over Wi-Fi instead of down the cable.

    The gate, in order: a paired token; only the person's bits; an agent that is waiting
    for a person *right now*; a code that is live for that agent; and, for a confirm, the
    exact call it was shown against. A device token is no use for anything else — this is
    the only path that takes one."""
    device_id = device_auth(handler.headers.get("X-Reflex-Device-Token") or "")
    if device_id is None:
        handler._json(403, {"error": "this device is not paired with the console"})
        return
    bits = {k: v for k, v in payload.items() if k not in ("agent", "for_reason", "token", "code", "note")}
    extra = sorted(b for b in bits if b not in DEVICE_BITS)
    if not bits or extra:
        handler._json(403, {"error": f"a device may write {', '.join(DEVICE_BITS)} and nothing else"
                                     + (f"; not {', '.join(extra)}" if extra else "")})
        return
    # Loosening over a radio: refused. `blocked: 0` would let a network write lift a block,
    # and `heartbeat` would let one hold a dead-man's halt open. Both are USB-only, and the
    # heartbeat over Wi-Fi is the device's presence, which this console works out itself.
    loosening = [b for b, v in bits.items() if (b == "heartbeat" and v) or (b == "blocked" and not v)]
    if loosening:
        why = {"blocked": "a block is lifted on the console or over USB, not over the network",
               "heartbeat": "the heartbeat is this device's presence, which the console keeps itself — a beat a "
                            "network write could forge would not be a dead man's switch"}
        handler._json(403, {"error": f"{', '.join(sorted(loosening))} may not be written over the network: "
                                     + "; ".join(why[b] for b in sorted(loosening))})
        return
    name = str(payload.get("agent", ""))[:40]
    waiting = [it for it in (boundary.status().get("pending") or []) if it["agent"] == name]
    if not waiting:
        handler._json(403, {"error": f"{name!r} is not waiting for a person right now; a device can only act on a "
                                     "call it can see on its screen"})
        return
    given = str(payload.get("code") or "").strip()
    bind_to = str(payload.get("for_reason"))[:160] if payload.get("for_reason") else None
    keys = [b for b in ("confirm", "confirm_b") if bits.get(b)]
    if keys:
        want = boundary.code_for_call(name, bind_to or "", keys[0])
        if not bind_to or want is None or not hmac.compare_digest(given, want):
            handler._json(403, {"error": "those two digits are not the ones shown beside this call; read the code "
                                         "again on the screen the request is on"})
            return
    else:
        # `blocked: 1`, and clearing a confirm: stricter either way, but still only for an
        # agent whose own code the device can read off its screen.
        live = {it.get("code") for it in waiting if it.get("code")}
        if not given or given not in live:
            handler._json(403, {"error": "send the two digits shown beside one of this agent's waiting calls"})
            return
    note = payload.get("note")
    if note is not None and not isinstance(note, str):
        handler._json(400, {"error": "note must be a string of at most 200 characters"})
        return
    try:
        entry = boundary.arm(name, bits, bind_to, (note or "").strip()[:200] or None)
    except ValueError as e:
        handler._json(400, {"error": str(e)})
        return
    # The transcript says where the person's bit came from, as it does for the cable.
    entry["source"] = "cardputer-wifi"
    entry["device"] = device_id
    print(f"cardputer over Wi-Fi: {', '.join(sorted(bits))} for {name} from {device_id} "
          f"(a person pressed a key)", flush=True)
    handler._json(200, entry)


if __name__ == "__main__":  # `python -m cardputer_relay …`: pairing without a console running
    import argparse

    ap = argparse.ArgumentParser(description="Pair a Cardputer with this console over Wi-Fi.")
    ap.add_argument("command", choices=("open", "confirm", "status", "forget"))
    ap.add_argument("value", nargs="?", help="the four digits for `confirm`, the device id for `forget`")
    args = ap.parse_args()
    if args.command == "open":
        print(json.dumps(device_pair_begin(), indent=2))
    elif args.command == "confirm":
        print(json.dumps(device_pair_confirm(args.value or ""), indent=2))
    elif args.command == "forget":
        print(json.dumps(device_forget(args.value or ""), indent=2))
    else:
        print(json.dumps(device_pair_status(), indent=2))
