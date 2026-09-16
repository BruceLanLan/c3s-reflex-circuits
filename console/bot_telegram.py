"""Relay the duty console into a Telegram chat. It carries no authority of its own.

    TELEGRAM_TOKEN=... CONSOLE_URL=http://127.0.0.1:8765 python bot_telegram.py

Commands:
    /propose <l/v ms> <azimuth deg> [why]   ask the circuit about a stimulus
    /state                                  the refractory countdown per agent

The token is read from the environment and never written anywhere. The bot only
forwards to the console's two endpoints, so it can do nothing the console cannot, and
the console can do nothing the circuit does not allow.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CONSOLE = os.environ.get("CONSOLE_URL", "http://127.0.0.1:8765").rstrip("/")
API = f"https://api.telegram.org/bot{TOKEN}"
HELP = "/propose <l/v ms> <azimuth deg> [why] — ask the circuit\n/state — refractory countdown"


def call(url: str, payload: dict | None = None, timeout: int = 40) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def say(chat_id: int, text: str) -> None:
    try:
        call(f"{API}/sendMessage", {"chat_id": chat_id, "text": text})
    except urllib.error.URLError as e:
        print(f"could not reply: {e}", flush=True)


def verdict_line(v: dict) -> str:
    if v.get("error"):
        return f"refused before the circuit saw it: {v['error']}"
    gap = v.get("ticks_since_previous_authorisation")
    tail = f", {gap} ticks after the last authorisation" if gap is not None else ""
    head = "AUTHORISED" if v["authorised"] else "refused"
    return f"{head} — {v['action']} after {v['ticks_used']} ticks{tail}"


def handle(chat_id: int, text: str) -> None:
    parts = text.split()
    command = parts[0].split("@")[0] if parts else ""
    if command == "/state":
        s = call(f"{CONSOLE}/api/state")
        c = s["circuit"]
        lines = [f"{c['name']} · {c['nand']} NAND + {c['latch']} LATCH · refractory {s['refractory_ticks']} ticks (proven)"]
        for a in s["agents"] or []:
            left = a["refractory_ticks_left"]
            lines.append(f"{a['agent']}: {a['ticks']} ticks, {a['authorisations']} authorised"
                         + (f", {left} refractory ticks left" if left else ""))
        say(chat_id, "\n".join(lines) or "no proposals yet")
    elif command == "/propose":
        try:
            lv, az = float(parts[1]), float(parts[2])
        except (IndexError, ValueError):
            say(chat_id, HELP)
            return
        payload = {"agent": f"tg:{chat_id}", "l_over_v_ms": lv, "azimuth_deg": az, "reason": " ".join(parts[3:])}
        try:
            say(chat_id, verdict_line(call(f"{CONSOLE}/api/propose", payload)))
        except urllib.error.HTTPError as e:
            say(chat_id, verdict_line(json.loads(e.read())))
    else:
        say(chat_id, HELP)


def main() -> None:
    if not TOKEN:
        sys.exit("set TELEGRAM_TOKEN (see .env.example); it is never stored by this program")
    print(f"relaying {CONSOLE} into Telegram; Ctrl-C to stop", flush=True)
    offset = 0
    while True:
        try:
            updates = call(f"{API}/getUpdates?timeout=30&offset={offset}", timeout=45).get("result", [])
        except Exception as e:
            print(f"poll failed, retrying: {e}", flush=True)
            time.sleep(3)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            message = u.get("message") or u.get("channel_post") or {}
            text = message.get("text")
            if text:
                handle(message["chat"]["id"], text)


if __name__ == "__main__":
    main()
