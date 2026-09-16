"""The Cardputer as a physical confirm key: a USB-serial relay between the console and
the device's Agent page.

A model can write `request` and `intent`. It cannot press a key on a device on your
desk. So the device is the tool layer made physical: it shows what is waiting for a
person, and ENTER writes `confirm` (or `confirm_b`) for the selected agent, `b` writes
`blocked`, `u` lifts it. Nothing else crosses the wire in that direction.

Started by console.py when REFLEX_CARDPUTER is set (`1`/`auto` finds /dev/cu.usbmodem*,
or give a port). USB only: the firmware has no radio stack in use and holds no
credentials, so the only way to press this key is to be at the device.

Wire format, one ASCII line each, fields separated by `|` (stripped from the values):

    host -> device, on change and every 2 s
      S|<circuits>|<granted>|<refused>|<chain>|<agents blocked>
      I|<i>|<agent>|<class>|<tick>|<why>|<bit>|<armed>     up to four items a person can resolve
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
"""

from __future__ import annotations

import glob
import re
import threading
import time

try:
    import serial  # pyserial
except ImportError:  # the console runs without it; only this relay needs it
    serial = None

RESOLVE = [
    (re.compile(r"^two keys needed.*confirm_b"), "confirm_b"),
    (re.compile(r"^two keys needed"), "confirm"),
    (re.compile(r"^irreversible"), "confirm"),
    (re.compile(r"^no confirmation"), "confirm"),
    (re.compile(r"^breaker tripped"), "confirm"),
    (re.compile(r"^halted"), "confirm"),
]
SHORT = {"spend": "sp", "message": "ms", "exec": "ex", "files": "fi", "halt": "ha"}


def clean(value, limit: int) -> str:
    text = str(value).replace("|", "/").replace("\n", " ").replace("\r", " ")
    return text.encode("ascii", "replace").decode("ascii")[:limit]


def pending_items(state: dict) -> list[dict]:
    """The same rule as the console page: newest request per (agent, class); refused;
    and refused for a reason a person can resolve by writing a bit."""
    seen, out = set(), []
    for e in state["transcript"]:
        if e.get("kind") != "request":
            continue
        key = (e["agent"], e.get("class", "exec"))
        if key in seen:
            continue
        seen.add(key)
        if e.get("granted"):
            continue
        for w in e.get("why") or []:
            bit = next((b for rx, b in RESOLVE if rx.search(w)), None)
            if bit:
                out.append({"agent": e["agent"], "class": key[1], "tick": e.get("tick", 0), "why": w, "bit": bit,
                            "reason": e.get("reason", "")})
                break
    return out


def frame(state: dict) -> tuple[list[str], list[dict]]:
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

    armed = {a["agent"]: a.get("armed", {}) for a in state.get("agents", [])}
    items = pending_items(state)[:4]
    for i, it in enumerate(items):
        is_armed = int(bool(armed.get(it["agent"], {}).get(it["bit"])))
        lines.append(f"I|{i}|{clean(it['agent'], 30)}|{it['class']}|{it['tick']}|{clean(it['why'], 60)}|{it['bit']}|{is_armed}")
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
                            elif text.startswith(("brain view", "c3s escape core", "digest ")):
                                self.log(f"cardputer says: {text}")
            except Exception as e:  # unplugged, reset, port busy (e.g. while flashing)
                if self.connected:
                    self.log(f"cardputer: lost the device ({e}); retrying")
                self.connected = False
                buf = b""
                time.sleep(3)
