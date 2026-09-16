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

Each waiting item is sent with buttons, so approving from a phone is one press rather than
a typed line. A press carries exactly what a typed /confirm carries: the call it was shown
against (`for_reason`) and that call's two digits (`code`). Telegram's callback_data is 64
bytes, far too small for a `reason`, so the button carries a digest of (agent, reason, bit)
and the press is resolved by recomputing that digest over what the console says is waiting
*now*. A button therefore cannot approve "whatever is waiting" — if the call is gone, the
code was reissued, or someone already approved it, the press is refused and writes nothing.
Buttons are the person's, exactly as the typed commands are: a press from a chat outside
TOOL_LAYER_CHATS is refused before anything is read.

A chat the agent itself can type into must never be listed: a confirm that whoever is
typing can give is a commitment cost dressed up as a boundary (docs/AGENT.md).

The token is read from the environment and never written anywhere. The bot only
forwards to the console's endpoints, so it can do nothing the console cannot, and the
console can do nothing the compiled rules do not allow.
"""

from __future__ import annotations

import hashlib
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
# `CONSOLE_URL` is what a deployment sets, and it wins. `REFLEX_CONSOLE` and
# `CONSOLE_PORT` are what everything else here reads, so they are honoured too: without
# that, a test run pointed at its own console still talked to whatever owned 8765, got a
# 403 from a console it had no token for, and blamed the code under test.
CONSOLE = (os.environ.get("CONSOLE_URL") or os.environ.get("REFLEX_CONSOLE")
           or f"http://127.0.0.1:{os.environ.get('CONSOLE_PORT', '8765')}").rstrip("/")
TOOL_LAYER_CHATS = {c.strip() for c in os.environ.get("TOOL_LAYER_CHATS", "").split(",") if c.strip()}
# The operator token gates installing rules and writing the person's bits. The bot is a
# person's tool, so it reads the token the console wrote; a person's chat then holds the keys.
_TOKEN_FILE = os.path.expanduser(
    os.environ.get("REFLEX_TOKEN_FILE", os.path.join(os.environ.get("REFLEX_CONFIG_DIR", "~/.c3s-circuit-agent"), "operator-token")))
OPERATOR_TOKEN = os.environ.get("REFLEX_OPERATOR_TOKEN") or (
    open(os.path.expanduser(_TOKEN_FILE)).read().strip() if os.path.exists(os.path.expanduser(_TOKEN_FILE)) else "")
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
    "/stop — block every agent · /resume\n"
    "Each waiting item also arrives with buttons; a press is bound to that one call and its "
    "two digits, so it cannot be spent on a different call."
)
REFUSED_HERE = ("blocked, confirm and stop are written by a layer the agent cannot reach. This chat is "
                "an agent's channel, so the bot will not send them from here; list the chat id in "
                "TOOL_LAYER_CHATS only if it really is a person's, separate from the agent.")


def call(path_or_url: str, payload: dict | None = None, timeout: int = 40) -> dict:
    url = path_or_url if path_or_url.startswith("http") else f"{CONSOLE}{path_or_url}"
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"content-type": "application/json"}
    # The person's endpoints (install rules, write confirm/blocked) carry the token.
    if OPERATOR_TOKEN and not str(url).startswith("https://api.telegram.org"):
        headers["x-reflex-token"] = OPERATOR_TOKEN
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def say(chat_id, text: str, buttons: list[list[dict]] | None = None) -> None:
    body = {"chat_id": chat_id, "text": text}
    if buttons:
        body["reply_markup"] = {"inline_keyboard": buttons}
    try:
        call(f"{API}/sendMessage", body)
    except urllib.error.URLError as e:
        print(f"could not reply: {e}", flush=True)


def answer(callback_id: str, text: str, alert: bool = False) -> None:
    """Telegram requires every callback query to be answered, or the button spins forever.

    The text is the whole reply on a phone when `alert` is false, so it says what happened
    to the boundary — written, refused, or nothing at all — not merely "ok"."""
    try:
        call(f"{API}/answerCallbackQuery",
             {"callback_query_id": callback_id, "text": text[:200], "show_alert": alert})
    except urllib.error.URLError as e:
        print(f"could not answer a press: {e}", flush=True)


def strip_keyboard(chat_id, message_id) -> None:
    """Take the buttons off an item that has been dealt with, so the next press cannot be a
    second attempt at a call that is over. Best effort: the refusal on a stale press is what
    actually protects the boundary, not the absence of the button."""
    if message_id is None:
        return
    try:
        call(f"{API}/editMessageReplyMarkup",
             {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}})
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"could not clear buttons: {e}", flush=True)


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
    code_line = f"\n  code {it['code']} — the same two digits as on the console's page" if it.get("code") else ""
    return (f"{head}{it['agent']} · {it['class']} · tick {it['tick']}{call_line}\n  {it['why']}{code_line}\n"
            f"  /{it['bit']} {n if n is not None else it['agent']}")


CB = "c3s1"  # this bot's own callback_data, versioned so an old button from a restart is recognisably old


def call_digest(agent: str, reason: str, bit: str) -> str:
    """Twelve hex characters standing for one exact call.

    Telegram allows 64 bytes of callback_data and a `reason` is routinely longer, so the
    button carries this instead of the reason. It is not a secret and not an authorisation:
    the press is resolved by recomputing it over the entries the console says are waiting
    now, so a digest that matches nothing waiting approves nothing."""
    return hashlib.sha256(f"{agent}\n{reason}\n{bit}".encode()).hexdigest()[:12]


# What this bot last showed for a digest. Used only to name the agent when a *block* is
# pressed on an item that has since gone: blocking only ever makes the boundary stricter.
# A confirm is never resolved from here — only from what the console says is waiting.
_SHOWN: dict[str, dict] = {}


def remember(it: dict) -> str:
    digest = call_digest(it["agent"], it.get("reason", ""), it["bit"])
    _SHOWN[digest] = {"agent": it["agent"], "reason": it.get("reason", ""), "bit": it["bit"]}
    for stale in list(_SHOWN)[:-200]:  # a bounded memory of what was on screen
        _SHOWN.pop(stale, None)
    return digest


def keyboard(it: dict) -> list[list[dict]]:
    """The four things a person does about one waiting call, as four buttons.

    Every button names the call it was shown against; none of them means "whatever is
    waiting now". The code rides along so the press can be refused if the console has
    since reissued it."""
    digest = remember(it)
    code = it.get("code") or ""
    bit = it["bit"]
    grant = "approve this call" if bit == "confirm" else f"{bit} — the second key"
    return [
        [{"text": f"{grant} · code {code}", "callback_data": f"{CB}|{bit}|{code}|{digest}"}],
        [{"text": f"block {it['agent']}", "callback_data": f"{CB}|block|{code}|{digest}"}],
        [{"text": "stop everything", "callback_data": f"{CB}|stop|{code}|{digest}"},
         {"text": "ignore", "callback_data": f"{CB}|ignore|{code}|{digest}"}],
    ]


def live_item(digest: str) -> dict | None:
    """The entry the console is waiting on right now that this button was made for, if any.

    Recomputed rather than remembered, which is what makes a press as narrow as a typed
    /confirm: the item's reason and bit have to still hash to the same twelve characters."""
    for it in pending_items(call("/api/state")):
        if call_digest(it["agent"], it.get("reason", ""), it["bit"]) == digest:
            return it
    return None


def handle_press(chat_id, data: str, callback_id: str, message_id=None) -> None:
    # A press is written by whoever can type in this chat, so it is gated exactly as
    # /confirm is. A chat an agent can reach must never be in TOOL_LAYER_CHATS.
    if str(chat_id) not in TOOL_LAYER_CHATS:
        answer(callback_id, "this chat is an agent's channel; a person's keys are not pressed from here", alert=True)
        say(chat_id, REFUSED_HERE)
        return
    parts = data.split("|")
    if len(parts) != 4 or parts[0] != CB:
        answer(callback_id, "this button was not made by this bot", alert=True)
        return
    _, action, code, digest = parts

    if action == "ignore":
        answer(callback_id, "left waiting — nothing was written")
        strip_keyboard(chat_id, message_id)
        return

    if action == "stop":
        # The big red button. Deliberately not gated on reading the code: an emergency stop
        # must never wait on two digits, and it only ever makes the boundary stricter
        # (docs/API.md, why blocking needs no code). Lifting it is still /resume, typed.
        d = call("/api/stop-all", {"source": "telegram"})
        answer(callback_id, f"blocked {d.get('count', 0)} agent(s)")
        say(chat_id, f"blocked {d.get('count', 0)} agent(s) from Telegram; /resume lifts it")
        return

    if action == "block":
        name = (live_item(digest) or _SHOWN.get(digest) or {}).get("agent")
        if not name:
            answer(callback_id, "this button is older than this bot; use /block <agent>", alert=True)
            say(chat_id, "that button predates a restart, so the bot no longer knows which agent it named. "
                         "Nothing was written — /pending, or /block <agent>.")
            return
        call("/api/tool", {"agent": name, "blocked": 1})
        answer(callback_id, f"{name} blocked")
        say(chat_id, f"{name} blocked; /unblock {name} lifts it")
        strip_keyboard(chat_id, message_id)
        return

    if action not in ("confirm", "confirm_b"):
        answer(callback_id, f"this bot does not do {action!r}", alert=True)
        return

    # A confirm, and therefore the strict path: the call must still be waiting, under the
    # same two digits, and not already approved. Anything else writes nothing.
    item = live_item(digest)
    if item is None:
        answer(callback_id, "that call is no longer waiting — nothing was written", alert=True)
        say(chat_id, "that call is not waiting for a person any more, so the press was refused and nothing "
                     "was written. /pending shows what is.")
        strip_keyboard(chat_id, message_id)
        return
    if item["bit"] != action:
        answer(callback_id, f"that call now waits for {item['bit']}, not {action}", alert=True)
        say(chat_id, f"that call now waits for {item['bit']}, not {action}; nothing was written. See /pending.")
        return
    if (item.get("code") or "") != code:
        answer(callback_id, "the two digits beside that call have changed — read them again", alert=True)
        say(chat_id, "the console has reissued the code beside that call, which means it is not the call this "
                     "button was shown against. Nothing was written — /pending, and press the new button.")
        strip_keyboard(chat_id, message_id)
        return
    if item.get("armed"):
        answer(callback_id, "already approved — a second one was not written", alert=True)
        say(chat_id, f"a {action} for that exact call is already waiting to be spent; nothing was written again.")
        strip_keyboard(chat_id, message_id)
        return
    try:
        call("/api/tool", {"agent": item["agent"], action: 1,
                           "for_reason": item.get("reason", ""), "code": item["code"]})
    except urllib.error.HTTPError as e:
        why = json.loads(e.read()).get("error", str(e))
        answer(callback_id, f"refused: {why}", alert=True)
        say(chat_id, f"the console refused that press and wrote nothing: {why}")
        return
    answer(callback_id, f"{action} written for this call only")
    say(chat_id, f"{action} written for {item['agent']}, for this call only: "
                 f"{item.get('reason') or '(no reason given)'}")
    strip_keyboard(chat_id, message_id)


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
            if not waiting:
                say(chat_id, "nothing is waiting for a person")
                return
            # One message per item, each with its own buttons, so /pending also rebuilds
            # the buttons after a restart instead of leaving only typed commands.
            for i, it in enumerate(waiting):
                say(chat_id, pending_line(it, i + 1), keyboard(it))
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
            # A chat channel sends the code as well (docs/API.md, I-2): the console only
            # checks a code it is given, so it is this bot's job to always give one, and a
            # typed confirm is then bound as tightly as a press.
            body = {"agent": item["agent"], bit: 1, "for_reason": item.get("reason", "")}
            if item.get("code"):
                body["code"] = item["code"]
            try:
                call("/api/tool", body)
            except urllib.error.HTTPError as e:
                say(chat_id, "the console refused that confirm and wrote nothing: "
                             + json.loads(e.read()).get("error", str(e)))
                return
            say(chat_id, f"{bit} written for {item['agent']}, for this call only: {item.get('reason') or '(no reason given)'}")
        elif command in ("/block", "/unblock"):
            if not rest or rest[0] not in agents:
                say(chat_id, f"usage: {command} <agent>  (known: {', '.join(agents) or 'none'})")
                return
            call("/api/tool", {"agent": rest[0], "blocked": int(command == "/block")})
            say(chat_id, f"{rest[0]} {'blocked' if command == '/block' else 'unblocked'}")
        else:
            # The same big red button as the page and the CLI, so it does the same thing:
            # one `blocked` entry per agent carrying where the press came from, and the
            # way back also resets the shared halt latch (see docs/API.md, stop-all).
            stop = command == "/stop"
            d = call("/api/stop-all" if stop else "/api/resume-all", {"source": "telegram"})
            say(chat_id, f"{'blocked' if stop else 'unblocked'} {d.get('count', len(agents))} agent(s)")
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
                        say(chat, "waiting for a person\n" + pending_line(it), keyboard(it))
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
            press = u.get("callback_query")
            if press:
                where = press.get("message") or {}
                try:
                    handle_press(where.get("chat", {}).get("id"), press.get("data") or "",
                                 press["id"], where.get("message_id"))
                except Exception as e:
                    print(f"handling a press failed: {e}", flush=True)
                    answer(press["id"], "that press could not be carried out; nothing was written", alert=True)
                continue
            message = u.get("message") or u.get("channel_post") or {}
            text = message.get("text")
            if text:
                try:
                    handle(message["chat"]["id"], text)
                except Exception as e:
                    print(f"handling {text!r} failed: {e}", flush=True)


if __name__ == "__main__":
    main()
