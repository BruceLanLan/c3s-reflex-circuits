"""Relay the boundary console into a Telegram chat. It carries no authority of its own.

    TELEGRAM_TOKEN=... CONSOLE_URL=http://127.0.0.1:8765 python bot_telegram.py

Commands:
    /ask [why]        one tick on the agent channel, without intent
    /intent [why]     one tick on the agent channel, with intent high
    /rules            the installed rules, and what was checked about them
    /state            each agent's counters as the circuit sees them

A chat is the agent's channel. `blocked` and `confirm` are bits that only a layer the
agent cannot reach may write, so this program refuses to send them from a chat unless
that chat's id is listed in TOOL_LAYER_CHATS — otherwise a rule meant as a boundary
would be satisfiable by whoever is typing, which is the failure docs/AGENT.md names.

    TOOL_LAYER_CHATS=-1001234567890 python bot_telegram.py
    /block on | /block off | /confirm     (those chats only)

The token is read from the environment and never written anywhere. The bot only
forwards to the console's endpoints, so it can do nothing the console cannot, and the
console can do nothing the compiled rules do not allow.
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
TOOL_LAYER_CHATS = {c.strip() for c in os.environ.get("TOOL_LAYER_CHATS", "").split(",") if c.strip()}
API = f"https://api.telegram.org/bot{TOKEN}"
HELP = (
    "/ask [why] — one tick, no intent\n"
    "/intent [why] — one tick with intent high\n"
    "/rules — the installed rules\n"
    "/state — counters per agent"
)


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
    head = "GRANTED" if v.get("granted") else "refused"
    why = "; ".join(v.get("why") or [])
    gap = v.get("ticks_since_previous_grant")
    lines = [f"{head} — tick {v.get('tick')}" + (f", {gap} ticks after the last grant" if gap is not None else "")]
    if why:
        lines.append(why)
    chain = v.get("chain") or {}
    if chain.get("error"):
        lines.append(f"chain check unavailable: {chain['error']}")
    elif chain:
        agrees = "agrees" if chain.get("agrees") else "DISAGREES"
        lines.append(f"chain {chain.get('chain_id')} {agrees} — read-only, nothing deployed")
    return "\n".join(lines)


def request(chat_id: int, intent: int, reason: str) -> None:
    payload = {"agent": f"tg:{chat_id}", "intent": intent, "reason": reason}
    try:
        say(chat_id, verdict_line(call(f"{CONSOLE}/api/request", payload)))
    except urllib.error.HTTPError as e:
        say(chat_id, verdict_line(json.loads(e.read())))


def handle(chat_id: int, text: str) -> None:
    parts = text.split()
    command = parts[0].split("@")[0] if parts else ""
    rest = " ".join(parts[1:])

    if command == "/rules":
        p = call(f"{CONSOLE}/api/state")["policy"]
        c, checked = p["circuit"], p["checked"]
        lines = [f"{c['nand']} NAND + {c['latch']} LATCH, compiled from these rules:"]
        lines += [f"· {r}" for r in p["rules"]] or ["· nothing is granted, ever"]
        lines.append(f"checked on all {checked['rows']:,} rows of its domain: "
                     f"{'matches the reference' if checked['matches_reference'] else 'DOES NOT MATCH'}")
        lines.append(f"every rule holds across {checked['reachable_states']} reachable states: "
                     f"{'yes' if checked['every_rule_holds'] else 'NO'}")
        say(chat_id, "\n".join(lines))
    elif command == "/state":
        s = call(f"{CONSOLE}/api/state")
        lines = []
        for a in s["agents"] or []:
            bits = [f"{a['ticks']} ticks", f"{a['grants']} granted"]
            if a["cooldown_left"]:
                bits.append(f"{a['cooldown_left']} cooldown ticks left")
            if a["intent_streak"]:
                bits.append(f"intent streak {a['intent_streak']}")
            if a["confirm_window_left"]:
                bits.append(f"confirm window {a['confirm_window_left']}")
            lines.append(f"{a['agent']}: " + ", ".join(bits))
        say(chat_id, "\n".join(lines) or "no requests yet")
    elif command == "/ask":
        request(chat_id, 0, rest)
    elif command == "/intent":
        request(chat_id, 1, rest)
    elif command in ("/block", "/confirm"):
        if str(chat_id) not in TOOL_LAYER_CHATS:
            say(chat_id, "blocked and confirm may only be written by a layer the agent cannot reach. "
                         "This chat is the agent's own channel, so the bot will not send them from here; "
                         "list the chat id in TOOL_LAYER_CHATS if it is in fact a separate, trusted one.")
            return
        body = {"agent": f"tg:{chat_id}"}
        if command == "/confirm":
            body["confirm"] = 1
        else:
            body["blocked"] = 0 if rest.strip() == "off" else 1
        armed = call(f"{CONSOLE}/api/tool", body)
        say(chat_id, f"tool layer wrote blocked={armed['blocked']} confirm={armed['confirm']}")
    else:
        say(chat_id, HELP)


def main() -> None:
    if not TOKEN:
        sys.exit("set TELEGRAM_TOKEN (see .env.example); it is never stored by this program")
    print(f"relaying {CONSOLE} into Telegram; Ctrl-C to stop", flush=True)
    if TOOL_LAYER_CHATS:
        print(f"tool-layer bits accepted from {len(TOOL_LAYER_CHATS)} chat(s)", flush=True)
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
