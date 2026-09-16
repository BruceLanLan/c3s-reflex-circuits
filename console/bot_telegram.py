"""The boundary console in Telegram: an approvals channel for the person, and a way to
ask for the agent. It carries no authority of its own.

    TELEGRAM_TOKEN=... CONSOLE_URL=http://127.0.0.1:8765 \
    TOOL_LAYER_CHATS=<your chat id> python bot_telegram.py

Two kinds of chat, because the separation is the whole point:

Any chat is an agent's channel. It may write what an agent may write:
    /ask [class] [why]      one tick without intent       class: spend message exec files
    /intent [class] [why]   one tick with intent high     (default exec)
    /rules [class]          the installed rules and what was checked about them
    /state                  each agent's counters

A chat listed in TOOL_LAYER_CHATS is the person's. Only there:
    /pending                what is waiting for a person, in the circuits' own words
    /confirm [n|agent]      confirm one waiting call: its /pending number, or an agent's
                            newest; the confirm is held for that exact call and cannot be
                            spent on a different one
    /confirm_b [n|agent]    the second key, under a two-key rule
    /block <agent>          /unblock <agent>
    /stop                   block every agent          /resume   lift every block
and those chats are told, once, whenever something new starts waiting for a person.

A chat the agent itself can type into must never be listed: a confirm that whoever is
typing can give is a commitment cost dressed up as a boundary (docs/AGENT.md).

The token is read from the environment and never written anywhere. The bot only
forwards to the console's endpoints, so it can do nothing the console cannot, and the
console can do nothing the compiled rules do not allow.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cardputer_relay import pending_items  # noqa: E402  (same "waiting for a person" rule as the page and the device)

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CONSOLE = os.environ.get("CONSOLE_URL", "http://127.0.0.1:8765").rstrip("/")
TOOL_LAYER_CHATS = {c.strip() for c in os.environ.get("TOOL_LAYER_CHATS", "").split(",") if c.strip()}
API = f"https://api.telegram.org/bot{TOKEN}"
CLASSES = ("spend", "message", "exec", "files")

HELP_AGENT = (
    "/ask [class] [why] — one tick, no intent\n"
    "/intent [class] [why] — one tick with intent\n"
    "/rules [class] — the installed rules\n"
    "/state — counters per agent"
)
HELP_PERSON = (
    "\n\nThis chat is a person's (tool layer):\n"
    "/pending — what waits for you\n"
    "/confirm [n|agent] · /confirm_b [n|agent] — for that call only\n"
    "/block <agent> · /unblock <agent>\n"
    "/stop — block every agent · /resume"
)
REFUSED_HERE = ("blocked, confirm and stop are written by a layer the agent cannot reach. This chat is "
                "an agent's channel, so the bot will not send them from here; list the chat id in "
                "TOOL_LAYER_CHATS only if it really is a person's, separate from the agent.")


def call(path_or_url: str, payload: dict | None = None, timeout: int = 40) -> dict:
    url = path_or_url if path_or_url.startswith("http") else f"{CONSOLE}{path_or_url}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def say(chat_id, text: str) -> None:
    try:
        call(f"{API}/sendMessage", {"chat_id": chat_id, "text": text})
    except urllib.error.URLError as e:
        print(f"could not reply: {e}", flush=True)


def verdict_line(v: dict) -> str:
    if v.get("error"):
        return f"refused before the circuit saw it: {v['error']}"
    head = "GRANTED" if v.get("granted") else "refused"
    lines = [f"{head} — {v.get('class', 'exec')} · tick {v.get('tick')}"]
    if v.get("why"):
        lines.append("; ".join(v["why"]))
    chain = v.get("chain") or {}
    if chain.get("pending"):
        lines.append("BSC re-check still running — see the console's log")
    elif chain.get("error"):
        lines.append(f"chain check unavailable: {chain['error']}")
    elif chain:
        lines.append(f"BSC {'agrees' if chain.get('agrees') else 'DISAGREES'} — read-only, nothing deployed")
    return "\n".join(lines)


def pending_line(it: dict, n: int | None = None) -> str:
    head = f"{n}. " if n is not None else ""
    call_line = f"\n  call: {it['reason']}" if it.get("reason") else ""
    return (f"{head}{it['agent']} · {it['class']} · tick {it['tick']}{call_line}\n  {it['why']}\n"
            f"  /{it['bit']} {n if n is not None else it['agent']}")


def split_class(rest: list[str]) -> tuple[str, str]:
    if rest and rest[0] in CLASSES:
        return rest[0], " ".join(rest[1:])
    return "exec", " ".join(rest)


def handle(chat_id, text: str) -> None:
    parts = text.split()
    command = parts[0].split("@")[0] if parts else ""
    rest = parts[1:]
    person = str(chat_id) in TOOL_LAYER_CHATS

    if command in ("/ask", "/intent"):
        cls, why = split_class(rest)
        body = {"agent": f"tg:{chat_id}", "intent": int(command == "/intent"), "reason": why, "class": cls}
        try:
            say(chat_id, verdict_line(call("/api/request", body)))
        except urllib.error.HTTPError as e:
            say(chat_id, verdict_line(json.loads(e.read())))
    elif command == "/rules":
        cls = rest[0] if rest and rest[0] in CLASSES + ("halt",) else "exec"
        p = call("/api/state")["policies"].get(cls)
        if p is None:
            say(chat_id, f"{cls}: no circuit installed — not gated")
            return
        if p.get("deny_all"):
            say(chat_id, f"{cls}: denied outright — no circuit grants here")
            return
        c, checked = p["circuit"], p["checked"]
        lines = [f"{cls}: {c['nand']} NAND + {c['latch']} LATCH"]
        lines += [f"· {r}" for r in p["rules"]]
        lines.append(f"every one of {checked['rows']:,} rows matches the reference: "
                     f"{'yes' if checked['matches_reference'] else 'NO'}")
        lines.append(f"every rule holds over {checked['reachable_states']} reachable states: "
                     f"{'yes' if checked['every_rule_holds'] else 'NO'}")
        say(chat_id, "\n".join(lines))
    elif command == "/state":
        s = call("/api/state")
        lines = []
        for a in s["agents"]:
            used = [f"{c} {v['grants']}/{v['ticks']}" for c, v in a["by_class"].items() if v["ticks"]]
            flag = " · BLOCKED" if a["armed"].get("blocked") else ""
            lines.append(f"{a['agent']}: " + (", ".join(used) or "no ticks") + flag)
        say(chat_id, "\n".join(lines) or "no agents yet")
    elif command in ("/pending", "/confirm", "/confirm_b", "/block", "/unblock", "/stop", "/resume"):
        if not person:
            say(chat_id, REFUSED_HERE)
            return
        s = call("/api/state")
        agents = [a["agent"] for a in s["agents"]]
        waiting = pending_items(s)
        if command == "/pending":
            say(chat_id, "\n\n".join(pending_line(it, i + 1) for i, it in enumerate(waiting)) or "nothing is waiting for a person")
        elif command in ("/confirm", "/confirm_b"):
            # A confirm is for one call: the item by its /pending number, or an agent's
            # newest waiting item; the console holds it for that exact call.
            bit = command[1:]
            mine = [it for it in waiting if it["bit"] == bit]
            if rest and rest[0].isdigit():
                n = int(rest[0])
                item = waiting[n - 1] if 1 <= n <= len(waiting) and waiting[n - 1]["bit"] == bit else None
            elif rest:
                item = next((it for it in mine if it["agent"] == rest[0]), None)
            else:
                item = mine[0] if mine else None
            if item is None:
                say(chat_id, f"nothing waiting for {bit}" + (f" matches {rest[0]}" if rest else "") + "; see /pending")
                return
            call("/api/tool", {"agent": item["agent"], bit: 1, "for_reason": item.get("reason", "")})
            say(chat_id, f"{bit} written for {item['agent']}, for this call only: {item.get('reason') or '(no reason given)'}")
        elif command in ("/block", "/unblock"):
            if not rest or rest[0] not in agents:
                say(chat_id, f"usage: {command} <agent>  (known: {', '.join(agents) or 'none'})")
                return
            call("/api/tool", {"agent": rest[0], "blocked": int(command == "/block")})
            say(chat_id, f"{rest[0]} {'blocked' if command == '/block' else 'unblocked'}")
        else:
            value = int(command == "/stop")
            for name in agents:
                call("/api/tool", {"agent": name, "blocked": value})
            say(chat_id, f"{'blocked' if value else 'unblocked'} {len(agents)} agent(s)")
    else:
        say(chat_id, HELP_AGENT + (HELP_PERSON if person else ""))


def new_waiting(state: dict, seen: set) -> list[dict]:
    """Items waiting for a person that have not been announced yet (by agent, class, tick)."""
    fresh = []
    for it in pending_items(state):
        key = (it["agent"], it["class"], it["tick"])
        if key not in seen:
            seen.add(key)
            fresh.append(it)
    return fresh


def announce_forever(interval: float = 3.0) -> None:
    seen: set = set()
    first = True
    while True:
        try:
            fresh = new_waiting(call("/api/state"), seen)
            if not first:  # what was already waiting at start-up is in /pending, not a ping
                for it in fresh:
                    for chat in TOOL_LAYER_CHATS:
                        say(chat, "waiting for a person\n" + pending_line(it))
            first = False
        except Exception as e:
            print(f"announce: {e}", flush=True)
        time.sleep(interval)


def main() -> None:
    if not TOKEN:
        sys.exit("set TELEGRAM_TOKEN (see .env.example); it is never stored by this program")
    print(f"relaying {CONSOLE} into Telegram; Ctrl-C to stop", flush=True)
    if TOOL_LAYER_CHATS:
        print(f"a person's chats (tool layer): {len(TOOL_LAYER_CHATS)}; they are told when something waits", flush=True)
        threading.Thread(target=announce_forever, daemon=True).start()
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
                try:
                    handle(message["chat"]["id"], text)
                except Exception as e:
                    print(f"handling {text!r} failed: {e}", flush=True)


if __name__ == "__main__":
    main()
