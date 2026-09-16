"""A second messenger for the person's side of the boundary — WeChat, over a transport.

Run the simulated transport, which needs no account and talks to nothing:

    REFLEX_CONSOLE=http://127.0.0.1:8765 WECHAT_TOOL_LAYER_USERS=me \
        python -m adapters.wechat_relay --sim

It prints each waiting call the way the messenger would show it and reads your replies from
stdin, so a person can play the part of the phone and see exactly what the console accepts.

WeChat has no buttons the way Telegram does, so the person types the two digits shown
beside the call:

    确认 47        confirm that one call        confirm 47
    二次确认 47    the second key, under a two-key rule
    拦截 47        block the agent that call belongs to
    全停           block every agent            stop
    待办           what is waiting              pending

**The code is the binding, not a convenience.** `code` is unique across everything waiting,
is reissued when the call changes, and dies with its entry, so "确认 47" names one exact
call and cannot be spent on another. The relay resolves the digits against what the console
says is waiting *now* and sends `for_reason` with them, which is the same write the page,
the Cardputer and a Telegram press make (docs/API.md, I-2).

WHO IS ON THE OTHER END — the honest problem
--------------------------------------------
The console cannot tell from a message whether a person or the agent wrote it. It never
could: it trusts the *channel*, attested by whoever holds the operator token. Telegram's
answer is `TOOL_LAYER_CHATS` — a chat the agent has no path into. This relay's answer is
`WECHAT_TOOL_LAYER_USERS`, the same shape, and it is worth exactly as much as that premise.

A WeChat deployment is a boundary only if all of these hold:

1. The agent does not hold the messenger's own credential (a WeCom app secret, or a session
   able to post as that account). If it does, it can write its own approval and the
   allowlist is a cost it pays once.
2. The allowlisted id belongs to a person, in a conversation the agent cannot send into.
   A shared group the agent posts status into is not that conversation.
3. The agent cannot read the operator-token file, and cannot reach the callback endpoint
   that feeds this relay. Either one lets it skip the messenger entirely.

If any of the three fails, this channel is theatre: it still *looks* like an approval, and
the reflex arc still refuses everything it is told to refuse, but the confirm it waits for
is one the agent can produce. Say so in the deployment notes rather than hoping.
Personal WeChat, specifically, has no sanctioned bot API — a deployment means WeCom
(企业微信), which is why the real transport below is the only shape offered.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cardputer_relay import pending_items  # noqa: E402  (one definition of "waiting for a person")

CONSOLE = os.environ.get("REFLEX_CONSOLE", os.environ.get("CONSOLE_URL", "http://127.0.0.1:8765")).rstrip("/")
# The person's own ids. Same rule as TOOL_LAYER_CHATS, and the same trap: an id the agent
# can write as belongs to the agent, whatever it is called.
TOOL_LAYER_USERS = {u.strip() for u in os.environ.get("WECHAT_TOOL_LAYER_USERS", "").split(",") if u.strip()}
# Read exactly as bot_telegram.py reads it: off disk, held in memory, never logged, never
# sent anywhere but this console's own endpoints.
_TOKEN_FILE = os.path.expanduser(os.environ.get(
    "REFLEX_TOKEN_FILE",
    os.path.join(os.environ.get("REFLEX_CONFIG_DIR", "~/.c3s-circuit-agent"), "operator-token")))
OPERATOR_TOKEN = os.environ.get("REFLEX_OPERATOR_TOKEN") or (
    open(_TOKEN_FILE).read().strip() if os.path.exists(_TOKEN_FILE) else "")

REFUSED_HERE = ("确认、拦截、全停 由 agent 接触不到的一层写入。这个会话不在 "
                "WECHAT_TOOL_LAYER_USERS 里，所以什么都没有写。")
HELP = ("确认 <两位数> — 只同意那一次调用\n"
        "二次确认 <两位数> — 双钥匙规则下的第二把\n"
        "拦截 <两位数> — 拦住那次调用所属的 agent\n"
        "全停 — 拦住所有 agent（不需要数字）\n"
        "待办 — 现在有什么在等人")
WORDS = {  # what a person may type, in either language, and the bit it asks for
    "确认": "confirm", "confirm": "confirm", "同意": "confirm", "ok": "confirm",
    "二次确认": "confirm_b", "confirm_b": "confirm_b", "第二把": "confirm_b",
    "拦截": "block", "block": "block", "拦住": "block",
    "全停": "stop", "stop": "stop", "停": "stop",
    "待办": "pending", "pending": "pending", "帮助": "help", "help": "help",
}


def call(path: str, payload: dict | None = None, timeout: int = 40) -> dict:
    """The console, and only the console. Carries the operator token, as the bot does."""
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"content-type": "application/json"}
    if OPERATOR_TOKEN:
        headers["x-reflex-token"] = OPERATOR_TOKEN
    req = urllib.request.Request(f"{CONSOLE}{path}", data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


# --------------------------------------------------------------------------- transports

class Transport(ABC):
    """Everything a messenger has to do for this relay, and nothing about which messenger.

    Two implementations: one that needs no account, and one that needs an account nobody
    has, so the second is untested by construction and says so."""

    @abstractmethod
    def send(self, user: str, text: str) -> None:
        """Show `text` to `user`. Never raises on a delivery failure; log and carry on."""

    @abstractmethod
    def poll(self, timeout: float = 1.0) -> list[tuple[str, str]]:
        """Messages that arrived since the last call, as (user id, text)."""


class SimulatedTransport(Transport):
    """A local queue in both directions. No network, no account, no credential.

    This is the transport the tests drive and the `--sim` CLI puts a person in front of. It
    is not a mock of a messenger's wire format — it is the honest shape of what a messenger
    gives this relay: a string from an id, and a string back."""

    def __init__(self) -> None:
        self.outbox: queue.Queue[tuple[str, str]] = queue.Queue()
        self.inbox: queue.Queue[tuple[str, str]] = queue.Queue()
        self.sent: list[tuple[str, str]] = []  # everything shown, for a test to read back

    def send(self, user: str, text: str) -> None:
        self.sent.append((user, text))
        self.outbox.put((user, text))

    def poll(self, timeout: float = 1.0) -> list[tuple[str, str]]:
        out = []
        try:
            out.append(self.inbox.get(timeout=timeout))
        except queue.Empty:
            return out
        while True:  # drain whatever else is already there
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                return out

    def as_person(self, user: str, text: str) -> None:
        """A person typing into the messenger. The relay still checks who that is."""
        self.inbox.put((user, text))

    def shown(self, user: str | None = None) -> list[str]:
        return [t for u, t in self.sent if user is None or u == user]


class WeComTransport(Transport):
    """UNTESTED. The thin adapter for WeCom (企业微信), written from the documented API and
    never once run, because no account exists to run it against.

    Two things a reviewer should know before trusting it:

    * Outbound is a plain POST with an access token this class would have to fetch and
      refresh. That part is ordinary.
    * **Inbound is not a poll.** WeCom pushes messages to a callback URL, signed and
      AES-encrypted with the app's own keys. There is no `getUpdates`. So `poll` here
      raises, and a real deployment must run that callback endpoint itself and hand each
      decrypted message to `Relay.on_message(user, text)`. The signature check on that
      endpoint is load-bearing: it is what stops anything that can reach the URL from
      speaking as the person (premise 3 in this module's header).
    """

    UNTESTED = True

    def __init__(self, corp_id: str | None = None, agent_id: str | None = None, secret: str | None = None) -> None:
        self.corp_id = corp_id or os.environ.get("WECOM_CORP_ID", "")
        self.agent_id = agent_id or os.environ.get("WECOM_AGENT_ID", "")
        self.secret = secret or os.environ.get("WECOM_SECRET", "")
        if not (self.corp_id and self.agent_id and self.secret):
            # Nothing here prompts for a credential. If it is not already in the
            # environment of a deployment that has one, this transport does not exist.
            raise RuntimeError(
                "the WeCom transport needs WECOM_CORP_ID, WECOM_AGENT_ID and WECOM_SECRET in the "
                "environment of a deployment that already has a WeCom app. It has never been run "
                "against a live account; use SimulatedTransport (--sim) to exercise the relay.")
        self._token, self._token_until = "", 0.0

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_until:
            return self._token
        url = ("https://qyapi.weixin.qq.com/cgi-bin/gettoken"
               f"?corpid={self.corp_id}&corpsecret={self.secret}")
        with urllib.request.urlopen(url, timeout=20) as resp:
            body = json.loads(resp.read())
        if body.get("errcode"):
            raise RuntimeError(f"WeCom refused the app credential: {body.get('errmsg')}")
        self._token = body["access_token"]
        self._token_until = time.time() + max(60, int(body.get("expires_in", 7200)) - 120)
        return self._token

    def send(self, user: str, text: str) -> None:
        body = {"touser": user, "msgtype": "text", "agentid": self.agent_id, "text": {"content": text}}
        req = urllib.request.Request(
            f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={self._access_token()}",
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                answer = json.loads(resp.read())
            if answer.get("errcode"):
                print(f"WeCom did not deliver: {answer.get('errmsg')}", flush=True)
        except (urllib.error.URLError, OSError) as e:
            print(f"WeCom unreachable: {e}", flush=True)

    def poll(self, timeout: float = 1.0) -> list[tuple[str, str]]:
        raise NotImplementedError(
            "WeCom pushes to a callback URL and cannot be polled. Run that endpoint, verify its "
            "signature, decrypt the message, and call Relay.on_message(user, text).")


# ------------------------------------------------------------------------------- relay

def card(it: dict) -> str:
    """One waiting call, as a person reads it on a phone. The digits come last, next to
    what they are for, because typing them is the approval."""
    lines = [f"反射弧在等人：{it['agent']} · {it['class']} · tick {it['tick']}"]
    if it.get("reason"):
        lines.append(f"调用：{it['reason']}")
    effect = it.get("effect") or {}
    if effect.get("summary"):
        lines.append(f"影响：{effect['summary']}")
    lines.append(f"原因：{it['why']}")
    word = "确认" if it["bit"] == "confirm" else "二次确认"
    lines.append(f"回复「{word} {it.get('code', '')}」只同意这一次；「拦截 {it.get('code', '')}」拦住它；「全停」全部停下")
    return "\n".join(lines)


class Relay:
    """The console's person-side channel over some messenger.

    It carries no authority of its own: it writes the same `/api/tool` bits the page writes,
    with the operator token it read off disk, and only for a call the console currently says
    is waiting. Everything it refuses, it refuses before writing anything."""

    def __init__(self, transport: Transport, users: set[str] | None = None) -> None:
        self.transport = transport
        self.users = set(users if users is not None else TOOL_LAYER_USERS)
        self.seen: set[tuple[str, str, int]] = set()

    # -- what the person is shown

    def waiting(self) -> list[dict]:
        return pending_items(call("/api/state"))

    def announce(self, first: bool = False) -> list[dict]:
        """Tell the person's ids about anything new. What was already waiting at start-up
        belongs in 待办, not in a ping."""
        fresh = []
        for it in self.waiting():
            key = (it["agent"], it["class"], it["tick"])
            if key in self.seen:
                continue
            self.seen.add(key)
            fresh.append(it)
        if not first:
            for it in fresh:
                for user in self.users:
                    self.transport.send(user, card(it))
        return fresh

    # -- what the person types

    def by_code(self, code: str) -> dict | None:
        """The one waiting call showing these digits. Unique while it waits, reissued when
        the call changes, gone when the call is gone — so this is the binding itself."""
        return next((it for it in self.waiting() if (it.get("code") or "") == code), None)

    def on_message(self, user: str, text: str) -> str:
        """Handle one message and return what was said back (also sent over the transport)."""
        said = self._decide(user, text)
        self.transport.send(user, said)
        return said

    def _decide(self, user: str, text: str) -> str:
        # Who is speaking decides this, before anything is parsed — as in the Telegram bot.
        if str(user) not in self.users:
            return REFUSED_HERE
        parts = (text or "").strip().split()
        if not parts:
            return HELP
        action = WORDS.get(parts[0].lower())
        digits = next((p for p in parts[1:] if p.isdigit()), "")
        if action is None:
            return HELP
        if action == "help":
            return HELP
        if action == "pending":
            waiting = self.waiting()
            return "\n\n".join(card(it) for it in waiting) or "现在没有东西在等人"
        if action == "stop":
            # No digits: an emergency stop must not wait on reading a number off a screen,
            # and it only ever makes the boundary stricter (docs/API.md). 恢复 is deliberate
            # and happens on the console, not here.
            d = call("/api/stop-all", {"source": "wechat"})
            return f"已拦住 {d.get('count', 0)} 个 agent。解除要回到 console，这里做不到。"

        if not digits:
            return f"「{parts[0]}」要带上那次调用旁边的两位数，例如「{parts[0]} 47」。什么都没有写。"
        item = self.by_code(digits)
        if item is None:
            return (f"没有在等人的调用挂着 {digits} 这两位数——可能已经处理过，或者调用变了、"
                    f"数字重发了。什么都没有写，发「待办」看现在等什么。")

        if action == "block":
            call("/api/tool", {"agent": item["agent"], "blocked": 1})
            return f"已拦住 {item['agent']}。解除要回到 console。"
        if item["bit"] != action:
            return f"那次调用现在等的是 {item['bit']}，不是 {action}。什么都没有写。"
        if item.get("armed"):
            return "那次调用已经有一个没花掉的确认在等着了，没有再写一个。"
        body = {"agent": item["agent"], action: 1,
                "for_reason": item.get("reason", ""), "code": item["code"]}
        try:
            call("/api/tool", body)
        except urllib.error.HTTPError as e:
            return "console 拒了这次写入，什么都没有写：" + json.loads(e.read()).get("error", str(e))
        return (f"{action} 已写给 {item['agent']}，只对这一次调用有效："
                f"{item.get('reason') or '(没给原因)'}")

    # -- the loop

    def run(self, interval: float = 2.0) -> None:
        first = True
        while True:
            try:
                self.announce(first=first)
                first = False
            except Exception as e:
                print(f"announce: {e}", flush=True)
            try:
                for user, text in self.transport.poll(timeout=interval):
                    try:
                        self.on_message(user, text)
                    except Exception as e:
                        print(f"handling {text!r} failed: {e}", flush=True)
            except NotImplementedError as e:
                print(f"this transport cannot be polled: {e}", flush=True)
                return


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="The person's WeChat channel for the reflex arc's console.")
    ap.add_argument("--sim", action="store_true",
                    help="the simulated transport: you play the phone, on stdin, with no account")
    ap.add_argument("--user", default=None, help="the id to act as (default: the first allowlisted one)")
    args = ap.parse_args(argv)

    if not args.sim:
        ap.error("only --sim can be run from here: the real transport is WeCom, needs an app nobody has, "
                 "and receives messages on a callback endpoint rather than by polling (see this module's "
                 "docstring). Nothing here will ask you for a credential.")
    users = TOOL_LAYER_USERS or {args.user or "me"}
    me = args.user or sorted(users)[0]
    transport = SimulatedTransport()
    relay = Relay(transport, users)
    print(f"simulated WeChat against {CONSOLE}; you are {me!r} (a person's id). Ctrl-C to stop.\n{HELP}\n",
          flush=True)
    def drain() -> None:
        while not transport.outbox.empty():
            _, text = transport.outbox.get()
            print(f"\n[反射弧 → {me}]\n{text}\n", flush=True)

    relay.announce(first=True)  # what was already waiting is 待办, not a ping
    while True:
        relay.announce()
        drain()
        try:
            line = input(f"{me}> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if line.strip():
            relay.on_message(me, line)
            drain()


if __name__ == "__main__":
    raise SystemExit(main())
