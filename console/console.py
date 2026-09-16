"""Boundary console: rules compiled to circuits, and every decision made by them.

    C3S_REPO=~/work/c3s-reflex python console.py        # http://127.0.0.1:8765

You write rules. They compile to a NAND/LATCH circuit, which is checked against a
plain-Python statement of the same rules on every row of its domain, and each rule is
then proven by visiting every state the circuit can reach from reset. Agents make
requests against that circuit; it decides; optionally a public BNB Smart Chain node
re-evaluates the deciding row read-only, with nothing deployed.

One agent, one circuit per class of tool. Rules for spending money, sending messages,
running commands and touching files are different rules, so they are different
circuits: `spend`, `message`, `exec`, `files`. Each is compiled and checked on its own
small domain — rows add rather than multiply — and a class with no circuit installed is
simply not gated (the decision says so). A fifth circuit, `halt`, is shared: it is
ticked first on every request with the same tool-layer bits, and when it refuses, every
class sees `blocked` high. Nothing here verifies a rule that spans two classes.

The default `exec` rules are the ones the fruit-fly escape circuit was already proven
to obey (docs/PROPERTIES.md): at most one grant in any 8 ticks, four ticks of
commitment before one, nothing while blocked. That is where they come from; everything
after that is the user's to set.

Two endpoints on purpose, because the separation is the whole point:

    POST /api/request   request, intent, class                — what an agent may write
    POST /api/tool      blocked, confirm, irreversible, failed,
                        heartbeat, confirm_b                  — what only the layer above it may write

A rule resting on the agent's own bits is a cost it can choose to pay; a rule resting
on the tool layer's bits is a boundary. Nothing here can enforce which caller is which:
that is a property of how this is deployed, and the page says so.

No wallet, no key, nothing signed, nothing broadcast, no action performed.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(os.environ.get("C3S_REPO", Path.home() / "work" / "c3s-reflex")).expanduser()
HOST = os.environ.get("CONSOLE_HOST", "127.0.0.1")
PORT = int(os.environ.get("CONSOLE_PORT", "8765"))
VERIFY_ON_CHAIN = os.environ.get("VERIFY_ON_CHAIN", "1") not in ("0", "", "no")
BSC_RPC = os.environ.get("BSC_RPC", "https://bsc-rpc.publicnode.com")
CHAIN_ADDRESS = "0x000000000000000000000000000000000000c3f5"
STATIC = Path(__file__).resolve().parent / "static"
TRANSCRIPT = 200

sys.path.insert(0, str(REPO))
from c3s import exhaust  # noqa: E402
from c3s.netlist import to_bytes  # noqa: E402
from c3s.policy import AGENT_WRITABLE, MUST_COME_FROM_THE_TOOL_LAYER, Policy  # noqa: E402

# W11 · the Cardputer over Wi-Fi. The device's whole side lives in cardputer_relay.py —
# the token store, the pairing, the frame it polls and the one write it may make — so the
# console keeps a line per hook: `/api/device/*` below, a device token on `/api/tool`, and
# the device's presence as a heartbeat source in __main__. A device token is never an
# operator token: where one is present, the device's rules apply and nothing falls through.
from cardputer_relay import device_auth, device_get, device_post, device_tool, install_device_presence  # noqa: E402
import cardputer_relay as _devices  # noqa: E402  (the phone's viewer token lives in the same store; see viewer_issue)

# Of the tool layer's bits, only `blocked` is a level that stays where it was put. The
# rest are events, each consumed by the one tick it applies to: a confirm (either key)
# authorises the next request; `irreversible` describes the next request; a heartbeat
# is one beat; `failed` reports the outcome of the previous action to the next tick.
CONSUMED_BY_A_TICK = ("confirm", "confirm_b", "irreversible", "heartbeat", "failed")

# Which tool-layer bits are the *person's*, and so need the operator token to write over
# HTTP. `confirm`/`confirm_b` authorise; `blocked` and `heartbeat` are what an agent would
# want to forge to lift a block or hold a dead-man's halt open. `irreversible` and `failed`
# are left tokenless because the adapters (the tool runner) arm them and setting either can
# only make a decision stricter — an agent gains nothing by writing them. The Cardputer
# relay writes the person's bits in-process, never over HTTP, so the token never gates it.
PERSON_BITS = ("confirm", "confirm_b", "blocked", "heartbeat")

# Which refusals a person can lift by writing one of the tool layer's bits, matched against
# the circuit's own words (c3s/policy.py `reasons()`). This is the *one* place that mapping
# lives: the page, the Cardputer relay and the chat bot all read `pending` out of
# /api/state instead of each deriving it again (three copies drifted apart once already).
PERSON_RESOLVES = (
    (re.compile(r"^two keys needed.*confirm_b"), "confirm_b"),
    (re.compile(r"^two keys needed"), "confirm"),
    (re.compile(r"^irreversible"), "confirm"),
    (re.compile(r"^no confirmation"), "confirm"),
    (re.compile(r"^breaker tripped"), "confirm"),
    (re.compile(r"^halted"), "confirm"),
)
# A blocked agent is the one refusal a confirm cannot help with: while `blocked` is high a
# circuit that reads it refuses whatever else arrives, so an entry saying so waits for a
# person to lift the block, not for a confirm. It carries no code.
BLOCKED_SAYS = "blocked is high"
# The other refusal no confirm can lift: a person pressed stop-all. It is not a rule and it
# is not compiled — it is the person withdrawing consent — so it is decided before any
# circuit is asked, for every agent (known or first seen now) and every class (a circuit
# installed, denied or absent). The red team showed a renamed agent walking out from under a
# per-name stop; the UX pass showed an ungated class walking straight through one. A stop
# that depends on which names are known or which circuits are installed is not a stop.
OPERATOR_STOP_SAYS = "stopped: a person pressed stop-all, and every request is refused until a person resumes"

# I-1: the shapes `effect` may take. The console never interprets an effect — it stores it
# and every consumer escapes it — so the only thing checked here is the shape.
EFFECT_KINDS = ("read", "send", "delete", "move", "write", "pay", "exec")
EFFECT_STRINGS = (("target", 120), ("summary", 200), ("amount", 40), ("asset", 40), ("path", 400))

# The classes of tool an agent's calls fall into, each with its own circuit, plus the
# one circuit every class shares. `exec` is where an unclassified call lands, so it
# always has a circuit.
TOOL_CLASSES = ("spend", "message", "exec", "files")
HALT = "halt"
CLASSES = TOOL_CLASSES + (HALT,)
DEFAULT_CLASS = "exec"

# The rules the escape circuit itself was proven to obey; the starting point, not a law.
FLY_DEFAULT = Policy(min_gap_ticks=8, commit_ticks=4, forbid_when_blocked=True)
# Installed rules survive a restart. Without this, restarting the console would quietly
# turn "no transfers" back into "spend has no circuit" — a boundary that resets open.
KEEP_AGENTS, KEEP_AGENTS_DAYS = 500, 30
CONFIG_DIR = Path(os.environ.get("REFLEX_CONFIG_DIR", Path.home() / ".c3s-circuit-agent")).expanduser()
STATE_FILE = Path(os.environ.get("REFLEX_STATE_FILE", CONFIG_DIR / "policies.json")).expanduser()
TOKEN_FILE = Path(os.environ.get("REFLEX_TOKEN_FILE", CONFIG_DIR / "operator-token")).expanduser()
# One name, one token (I-3). Written by the console, read by nothing else; the adapters
# keep their own copy in their own environment and never read this file.
AGENTS_FILE = Path(os.environ.get("REFLEX_AGENTS_FILE", CONFIG_DIR / "agents.json")).expanduser()


def operator_token() -> str:
    """The secret that gates the person's endpoints: installing rules and writing the
    person's bits (confirm, confirm_b, blocked, heartbeat). Generated once, kept in a file
    only the owner can read, and printed to the console's own log. The adapters never
    receive it, so an agent that reaches the console over HTTP still cannot write its own
    confirm or lift a block — unless it reads this file, which is the same-machine limit
    the README names (run the agent where it cannot). REFLEX_OPERATOR_TOKEN overrides."""
    env = os.environ.get("REFLEX_OPERATOR_TOKEN")
    if env:
        return env
    if TOKEN_FILE.exists() and TOKEN_FILE.read_text().strip():
        return TOKEN_FILE.read_text().strip()
    import secrets

    token = secrets.token_urlsafe(24)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token)
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass
    return token

# ---- I-3: the agent's own token ---------------------------------------------------------
#
# The operator token says "a person wrote this". An agent token says "this is the same
# program as last time". They answer different questions, so they are different secrets:
# an agent's token is in that agent's own environment (REFLEX_AGENT_TOKEN) and in no file
# the agent can read, and the console keeps only its SHA-256. What it stops is one agent
# driving another's circuit under its name — spending the confirm a person left for a
# different program, or tripping its breaker. What it cannot stop is a program that can
# read the other's environment: on one machine, one uid, that is always possible (see
# SECURITY.md), and the structural answer is docs/ISOLATION.md.
#
# Trust on first use, deliberately: a name nobody has bound keeps working without a token,
# so today's adapters do not break, and `/api/state` marks it `token_bound: false`. The
# cost of that default is exactly stated: **an unbound name is a name anything local can
# speak for**, which is the adversarial review's first finding. A deployment that wants the
# door shut sets REFLEX_REQUIRE_AGENT_TOKEN=1: there the console binds nothing over HTTP at
# all, and a request whose name a person has not bound with `c3s token bind` is refused
# rather than trusted. (Requiring merely *a* token was not enough — the adversarial review
# sent an arbitrary header value, bound the victim's name on the way through and spent its
# confirm. Authenticating a name and creating one are different powers.)
# `docs/ISOLATION.md` turns it on.
REQUIRE_AGENT_TOKEN = os.environ.get("REFLEX_REQUIRE_AGENT_TOKEN", "") not in ("", "0", "no")

AGENTS_LOCK = threading.Lock()


class BindingsUnreadable(RuntimeError):
    """`agents.json` is there and cannot be read. Not the same as "nobody is bound"."""


def _store_lock():
    """Read-modify-write on the store, held against the other processes too.

    A thread lock is not enough and this was not theoretical: the console and a test
    process were both binding names in the same second, and whichever read first wrote
    last — one of them silently put back a binding the other had just rotated away, and
    another time the reverse. A lost *binding* is the dangerous direction: the name quietly
    becomes unbound again, which is the one state anything local may speak for. `c3s token
    bind` and `c3s token rotate` run in their own processes, so the lock has to be one the
    filesystem keeps. The lock file is separate from the store because the store is
    replaced (a new inode) on every write."""
    import contextlib
    import fcntl

    @contextlib.contextmanager
    def held():
        with AGENTS_LOCK:  # same order everywhere: threads first, then the file
            fd = None
            try:
                AGENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(str(AGENTS_FILE) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError:
                fd = None  # no lock to be had (a read-only home?); a thread-safe write is still better than none
            try:
                yield
            finally:
                if fd is not None:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    finally:
                        os.close(fd)

    return held()


def _token_sha256(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def agent_bindings() -> dict:
    """`{name: {"token_sha256": …, "bound_at": …}}`, re-read from disk every time: another
    process (`c3s token rotate`) may have dropped a binding since the last request, and a
    binding that only takes effect after a restart is not a binding.

    No file means nobody is bound, which is the honest starting state. A file that *is*
    there and does not parse raises instead: reading it as "nothing is bound" would turn
    one bad write into "every agent's token is gone" and keep serving, which is the same
    mistake `Boundary._load` refuses to make for the rules (it exits rather than start with
    none). A boundary must not reset open, and that includes this one.

    One unreadable *record* is not one unreadable file: it is kept as a binding whose token
    nothing can match, so that name refuses everything and every other name is unaffected.
    (The first cut of this raised for the whole store, which turned one odd record into an
    outage for every agent — the adversarial review's 2.2.)"""
    if not AGENTS_FILE.exists():
        return {}
    try:
        data = json.loads(AGENTS_FILE.read_text())
    except (OSError, ValueError) as e:
        raise BindingsUnreadable(f"{AGENTS_FILE} exists and cannot be read ({e})") from e
    if not isinstance(data, dict):
        raise BindingsUnreadable(f"{AGENTS_FILE} is not a map of agent name to binding")
    return {k: (v if isinstance(v, dict) and isinstance(v.get("token_sha256"), str)
                else {"token_sha256": None, "unreadable": True}) for k, v in data.items()}


def _write_bindings(data: dict) -> None:
    """Replaced whole and atomically, mode 600: a half-written file would read as
    "nothing is bound", which is the failure that opens the door."""
    AGENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = AGENTS_FILE.with_suffix(".json.tmp")
    # Opened 600 rather than written and then chmod'ed: the window between the two is
    # small, but it is a window in which another process on this machine could read the
    # file, and the whole point of the file is that it belongs to one user.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, AGENTS_FILE)


def check_agent_token(name: str, given: str, may_bind: bool = True) -> str:
    """One of:

    `unbound`  no token offered, none on file — allowed, and shown as unbound.
    `bound`    a token offered for a name nobody had bound — bound to it now.
    `ok`       the token matches the one on file.
    `spoof`    the name is bound and this is not its token (a wrong one, or none).
    `required` nothing is bound and this caller may not be the one to bind it.

    `may_bind=False` refuses to make a binding at all: trust on first use is trust in
    whoever got there first, which is fine for a program on this machine and not fine for
    whoever is on the Wi-Fi. Callers that are not local pass False, and nothing is written.

    Raises `BindingsUnreadable` if the store is there and unreadable; the caller refuses
    the request rather than treating every agent as unbound.
    """
    import hmac

    given = (given or "").strip()
    with _store_lock():
        data = agent_bindings()
        record = data.get(name)
        if record is None:
            if not may_bind:
                return "required"
            if not given:
                # The default trusts an unbound name: I-3's compatibility clause, and the
                # state of the world before I-3 existed.
                return "unbound"
            data[name] = {"token_sha256": _token_sha256(given), "bound_at": time.time()}
            _write_bindings(data)
            return "bound"
        if not given or not isinstance(record.get("token_sha256"), str):
            return "spoof"  # a record nothing can match is a name that refuses everything
        return "ok" if hmac.compare_digest(_token_sha256(given), record["token_sha256"]) else "spoof"


def bound_token_owner(given: str) -> str | None:
    """Which agent a token belongs to, or None. Used where there is no name to check it
    against — a `GET` from the LAN, which is either the operator's or an agent's or
    nobody's. Constant-time per record, and it never says *which* token was close."""
    import hmac

    given = (given or "").strip()
    if not given:
        return None
    digest = _token_sha256(given)
    with _store_lock():
        for name, record in agent_bindings().items():
            if isinstance(record.get("token_sha256"), str) and hmac.compare_digest(digest, record["token_sha256"]):
                return name
    return None


def token_bind(agent: str, token: str) -> dict:
    """`c3s token bind --agent <name>`: bind a name to a token from a person's hand, before
    any agent runs.

    This is what makes `REFLEX_REQUIRE_AGENT_TOKEN=1` usable. In that mode the console
    binds nothing over HTTP — trust on first use is trust in whoever asks first, and the
    adversarial review's second pass showed what that is worth: with the switch on but
    binding still allowed, an attacker sent *any* header value and bound the victim's name
    on the way to spending its confirm. So the two powers are separated: HTTP may
    authenticate a name, and only the person's own command may create one."""
    token = (token or "").strip()
    if not token:
        raise ValueError("no token: pass the agent's REFLEX_AGENT_TOKEN, the same one that agent will send")
    with _store_lock():
        data = agent_bindings()
        already = data.get(agent)
        data[agent] = {"token_sha256": _token_sha256(token), "bound_at": time.time()}
        _write_bindings(data)
    return {"agent": agent, "bound": True, "replaced": already is not None,
            "next": f"start that agent with REFLEX_AGENT_TOKEN set to this token; any other program using the name "
                    f"{agent!r} is now refused"}


def token_list() -> dict:
    """`c3s token list`: which names are bound, and since when. Never the hashes — a page
    or a person needs to know *that* a name is bound, not what would match it. A store
    that cannot be read is reported as such rather than as "nothing bound", for the same
    reason `agent_bindings` raises: those are different states and one of them is the open
    door."""
    try:
        bindings = agent_bindings()
    except BindingsUnreadable as e:
        return {"bound": [], "store_error": str(e), "file": str(AGENTS_FILE)}
    return {"bound": [{"agent": name, "bound_at": rec.get("bound_at"), "unreadable": bool(rec.get("unreadable"))}
                      for name, rec in sorted(bindings.items())],
            "store_error": None, "file": str(AGENTS_FILE)}


def token_rotate(agent: str) -> dict:
    """`c3s token rotate --agent <name>`: drop the binding so the next request rebinds.

    Deliberately the whole of rotation. There is no "new token" to hand out, because the
    console never had the token — only its hash — and the new one is whatever the agent's
    own environment says next (or whatever `token_bind` is given). Depends on nothing but
    AGENTS_FILE, so a CLI can call it without a console running.

    A store that cannot be read is the one case where this cannot be surgical: the old
    contents are unknown, so rotation is a reset, and it says so rather than raising at the
    person who came here to fix exactly that (the adversarial review's 2.3)."""
    with _store_lock():
        try:
            data = agent_bindings()
        except BindingsUnreadable as e:
            _write_bindings({})
            return {"agent": agent, "rotated": True, "reset": True, "why": str(e),
                    "next": "the store could not be read, so it was reset: every binding is gone and every agent "
                            "rebinds (or is bound again with `c3s token bind`). Nothing else recovers a file "
                            "whose contents are unknown."}
        record = data.pop(agent, None)
        if record is None:
            return {"agent": agent, "rotated": False, "why": "that name was not bound; the next request with a token binds it"}
        _write_bindings(data)
    return {"agent": agent, "rotated": True, "was_bound_at": record.get("bound_at"),
            "next": "start the agent with a new REFLEX_AGENT_TOKEN; its first request binds the name again"}


# ---- the phone's own token (F1, 2026-09-17) ------------------------------------------------
#
# The page used to attach the operator token to its two-second /api/state poll, so with
# --lan on the person's one secret crossed the Wi-Fi in cleartext for as long as the page
# was open, and a passive listener who captured it owned the console. The pairing delivery
# was clean (fragment, never sent to a server); the retransmission was the hole.
#
# A paired phone now holds a token of its own, kept in the same store as a paired Cardputer
# (devices.json, hash only) and accepted by the same device path: it may read what is
# waiting, approve a waiting call whose code matches, and block — and additionally read the
# state and the manifest, which a Cardputer never asks for. It may not install rules, stop,
# resume, unblock, or hold the heartbeat. Its record carries `kind: "phone"`; a record without
# it is a Cardputer and keeps the narrower rights. The operator token now crosses the network
# only on a write a person makes from a phone they have deliberately given it to.
VIEWER_KIND = "phone"


def viewer_issue(name: str = "phone") -> dict:
    """Mint a phone's token: one record in devices.json, `kind: "phone"`, hash only. Called
    by the console for `POST /api/device/viewer` (operator token) — the CLI asks the running
    console rather than writing the store itself, so there is one writer to that file. The
    token is returned once, to go into the pairing QR's fragment, and is kept nowhere."""
    name = str(name or "phone")[:40].strip() or "phone"
    token = secrets.token_urlsafe(24)
    with _devices.DEVICES_LOCK:
        data = _devices._read_devices()
        while True:
            device_id = f"phone:{secrets.token_hex(3)}"
            if device_id not in data["devices"]:
                break
        data["devices"][device_id] = {"name": name, "kind": VIEWER_KIND, "token_sha256": _devices._sha256(token),
                                      "paired_at": time.time()}
        _devices._write_devices(data)
    print(f"phone: issued a viewer token to {device_id} ({name!r}). It reads the console and presses confirm/block "
          f"for calls it can see; it cannot install rules, stop, resume or unblock.", flush=True)
    return {"device_id": device_id, "token": token, "kind": VIEWER_KIND,
            "next": "put the token in the pairing link's fragment (c3s pair does); `python -m cardputer_relay forget "
                    f"{device_id}` revokes it"}


def viewer_auth(token: str) -> str | None:
    """Which paired *phone* this token belongs to, or None. A Cardputer's token is a device
    token too, but it is not a viewer: the frame is all it reads."""
    device_id = device_auth(token or "")
    if device_id is None:
        return None
    record = _devices._read_devices()["devices"].get(device_id) or {}
    return device_id if record.get("kind") == VIEWER_KIND else None


TRANSCRIPTS: deque = deque(maxlen=TRANSCRIPT)
CHAIN = None  # set in __main__ when the chain second opinion is on
TOKEN = None  # set in __main__: the operator secret gating the person's endpoints
STARTED_AT = time.time()

# ---- W3 · the task entrance (I-6) -------------------------------------------------------
#
# A person hands the agent a job, and the job gets a name. Everything the agent then does
# still goes through the circuits one call at a time — that does not change, and must not:
# the boundary is per call, not per task. What a task adds is a thread a person can read
# along: this reply, that deletion and those two refusals belong to "clear out the phishing
# mail", and the page can say what that job has spent.
#
# The one property this whole file has to keep is that **a task is not an authority**.
# Naming a task cannot arm a bit, cannot enter a circuit's inputs, cannot touch a latch.
# So a task id reaches `Boundary.request` as a label that is written onto the transcript
# entry after the tick and nowhere else, and `tests/test_console_task.py` checks the
# stronger statement: the same call with and without a task id leaves the same verdict,
# the same `why`, the same inputs *and* the same circuit state behind it.
#
# A task can only ever subtract. It is refused (never granted) when it is closed or was
# never filed, and `max_grants` closes the task on its own Nth grant so that the ceiling
# a person set arrives as the same plain refusal as closing it by hand — one path, one
# sentence, and no second refusal mechanism competing with the circuits.
TASKS_FILE = Path(os.environ.get("REFLEX_TASKS_FILE", CONFIG_DIR / "tasks.json")).expanduser()
KEEP_TASKS = 200
TASK_TEXT_MAX, TASK_NOTE_MAX = 600, 200


class Tasks:
    """The person's filed jobs, kept next to the rules and read by anything local.

    `state_file` None keeps them in memory only (the tests, and any Boundary built without
    a file). A file that is there and does not parse stops the console, for the same reason
    `Boundary._load` does: carrying on with an empty ledger would quietly turn "this job is
    closed" into "no such job — file a new one", and a closed task is a refusal a person
    put there on purpose."""

    def __init__(self, state_file: Path | None = None) -> None:
        self.lock = threading.Lock()
        self.save_lock = threading.Lock()
        self.state_file = state_file
        self.tasks: dict[str, dict] = {}
        for rec in self._load():
            self.tasks[rec["task_id"]] = rec

    # -- persistence ---------------------------------------------------------------

    def _load(self) -> list[dict]:
        if self.state_file is None or not self.state_file.exists():
            return []
        try:
            data = json.loads(self.state_file.read_text())
            tasks = data["tasks"]
            if not isinstance(tasks, list):
                raise ValueError("tasks is not a list")
            for rec in tasks:
                if not isinstance(rec, dict) or not isinstance(rec.get("task_id"), str):
                    raise ValueError("a task with no id")
                if rec.get("status") not in ("open", "closed"):
                    raise ValueError(f"{rec['task_id']}: status is {rec.get('status')!r}")
            return tasks
        except Exception as e:
            raise SystemExit(f"cannot read the saved tasks in {self.state_file} ({e}); refusing to start without "
                             f"them. A closed task is a refusal a person put there, and starting with an empty "
                             f"ledger would drop it. Fix or move the file to start from no tasks.")

    def _save(self) -> None:
        if self.state_file is None:
            return
        with self.lock:
            # Newest first, capped. An open task is never dropped: forgetting one would
            # turn its id into "no such task", which reads as a mistake rather than a job.
            recent = sorted(self.tasks.values(), key=lambda r: r.get("filed_at", 0), reverse=True)
            keep = [r for r in recent if r["status"] == "open"]
            keep += [r for r in recent if r["status"] != "open"][:KEEP_TASKS]
            body = json.dumps({"format": "c3s.console.tasks/1", "saved_at": time.time(),
                               "tasks": sorted(keep, key=lambda r: r.get("filed_at", 0))}, indent=2)
        with self.save_lock:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(body)
            try:
                tmp.chmod(0o600)  # a person's own words about their own work
            except OSError:
                pass
            tmp.replace(self.state_file)

    # -- filing and closing (the person's acts) -------------------------------------

    def _new_id(self) -> str:
        while True:
            task_id = "t_" + secrets.token_hex(2)
            if task_id not in self.tasks:
                return task_id

    def file(self, text: str, classes: list[str] | None = None, max_grants: int | None = None,
             extra: dict | None = None) -> dict:
        """One filed job. `classes` is what the person expects it to need and `max_grants`
        the ceiling they are willing to give it; everything else handed in (I-5's `runner`,
        `agent`, `reply_to`, `cwd`) is stored verbatim and interpreted by nothing here."""
        text = str(text or "").strip()
        if not text:
            raise ValueError("a task needs text: say what the job is, in your own words")
        if len(text) > TASK_TEXT_MAX:
            raise ValueError(f"the task text is longer than {TASK_TEXT_MAX} characters")
        for cls in classes or []:
            if cls not in TOOL_CLASSES:
                raise ValueError(f"no such class: {cls!r}; one of {', '.join(TOOL_CLASSES)}")
        if max_grants is not None:
            if not isinstance(max_grants, int) or isinstance(max_grants, bool) or max_grants < 1:
                raise ValueError("max_grants is a whole number of grants, at least 1, or leave it out for no ceiling")
        with self.lock:
            task_id = self._new_id()
            rec = {
                "task_id": task_id,
                "text": text[:TASK_TEXT_MAX],
                # A declaration a person reads, never a gate. Refusing a class this list
                # does not mention would be a boundary nothing proved, and a person would
                # soon trust it as one; the class's own circuit is the boundary. The page
                # shows what the task actually spent beside what it said it would need.
                "expects": sorted(set(classes or [])),
                "max_grants": max_grants,
                "status": "open",
                "filed_at": time.time(),
                "closed_at": None,
                "closed_by": None,
                "note": None,
                "granted": 0,
                "refused": 0,
                "by_class": {},
                **{k: v for k, v in (extra or {}).items()},
            }
            self.tasks[task_id] = rec
            out = dict(rec)
        self._save()
        TRANSCRIPTS.appendleft({"at": out["filed_at"], "kind": "task", "task": task_id, "event": "filed",
                                "text": out["text"], "expects": out["expects"], "max_grants": max_grants})
        return out

    def close(self, task_id: str, note: str | None = None, by: str = "person") -> dict:
        """Closing is a person's act. It is also the only thing a task does to a request:
        an id that is closed is refused, with the reason it closed."""
        with self.lock:
            rec = self.tasks.get(task_id)
            if rec is None:
                raise KeyError(task_id)
            if rec["status"] != "open":
                raise ValueError(f"{task_id} is already closed ({self._why_closed(rec)})")
            rec["status"] = "closed"
            rec["closed_at"] = time.time()
            rec["closed_by"] = by
            if note:
                rec["note"] = str(note).strip()[:TASK_NOTE_MAX] or None
            out = dict(rec)
        self._save()
        TRANSCRIPTS.appendleft({"at": out["closed_at"], "kind": "task", "task": task_id, "event": "closed",
                                "text": out["text"], "closed_by": by, **({"note": out["note"]} if out["note"] else {})})
        return out

    @staticmethod
    def _why_closed(rec: dict) -> str:
        if rec.get("closed_by") == "limit":
            return f"it reached the {rec.get('max_grants')} grant(s) it was filed with"
        return "a person closed it"

    # -- what a request may do with an id --------------------------------------------

    def check(self, task_id: str) -> dict:
        """The one gate: an id that is open passes, anything else raises. Called before the
        circuits are asked, so a refusal here costs no tick — a task that is over is not
        this agent's call being judged, it is a call that should not have been sent."""
        with self.lock:
            rec = self.tasks.get(task_id)
            if rec is None:
                raise ValueError(f"no task {task_id!r} was filed here: file one on the Tasks page, or send the "
                                 f"call without a task — the boundary is per call, not per task")
            if rec["status"] != "open":
                raise ValueError(f"task {task_id} is closed ({self._why_closed(rec)}); a closed task takes no more "
                                 f"calls. File a new one, or send the call without a task")
            return dict(rec)

    def record(self, task_id: str, cls: str, granted: bool) -> None:
        """What the job spent, after the circuit has already answered. Counting cannot
        change the verdict it counts: the tick is over. The Nth grant closes the task the
        person capped, so the next call meets the ordinary closed-task refusal."""
        hit_limit = False
        with self.lock:
            rec = self.tasks.get(task_id)
            if rec is None:
                return
            rec["granted" if granted else "refused"] += 1
            per = rec["by_class"].setdefault(cls, {"granted": 0, "refused": 0})
            per["granted" if granted else "refused"] += 1
            cap = rec.get("max_grants")
            hit_limit = bool(cap) and rec["status"] == "open" and rec["granted"] >= cap
        if hit_limit:
            self.close(task_id, by="limit")
        else:
            self._save()

    # -- reporting -------------------------------------------------------------------

    def view(self, task_id: str) -> dict | None:
        with self.lock:
            rec = self.tasks.get(task_id)
            return dict(rec) if rec else None

    def listing(self, only_open: bool = False, limit: int | None = None) -> list[dict]:
        with self.lock:
            recs = sorted(self.tasks.values(), key=lambda r: r.get("filed_at", 0), reverse=True)
            if only_open:
                recs = [r for r in recs if r["status"] == "open"]
            return [dict(r) for r in (recs[:limit] if limit else recs)]


TASKS = Tasks(None)  # in memory until __main__ gives it the file next to the rules


def _settings(policy: Policy) -> dict:
    return {
        "min_gap_ticks": policy.min_gap_ticks,
        "commit_ticks": policy.commit_ticks,
        "forbid_when_blocked": policy.forbid_when_blocked,
        "max_grants": policy.max_grants,
        "confirm_window_ticks": policy.confirm_window_ticks,
        "sticky_block": policy.sticky_block,
        "heartbeat_ticks": policy.heartbeat_ticks,
        "confirm_per_irreversible": policy.confirm_per_irreversible,
        "trip_after_failures": policy.trip_after_failures,
        "trip_after_refusals": policy.trip_after_refusals,
        "two_key": policy.two_key,
    }


@dataclass
class Compiled:
    """One class's circuit: the policy, its step table, its bytes, and what was checked.

    `deny_all` is the one case with no circuit: the class refuses everything outright.
    It exists because `max_grants=0` means *unlimited* in c3s, so "never" needs saying
    some other way, and a rule that can be stated as "no circuit grants here" does not
    need a circuit to prove it."""

    policy: Policy | None
    outs: object
    nxt: object
    netlist: bytes
    n_in: int
    state_bits: int
    summary: dict
    deny_all: bool = False

    @staticmethod
    def build(policy: Policy) -> "Compiled":
        """Compile, check on every row, prove every rule."""
        t0 = time.time()
        circuit = policy.build()
        verify = policy.verify(circuit)
        proofs = policy.properties(circuit)
        outs, nxt = exhaust.step_table(circuit)
        netlist = to_bytes(circuit)
        summary = {
            "rules": policy.describe(),
            "settings": _settings(policy),
            "circuit": {
                "nand": verify["metrics"]["nand"],
                "latch": verify["metrics"]["latch"],
                "bytes": len(netlist),
                "depth": verify["metrics"]["depth"],
                "netlist": "0x" + netlist.hex(),
                "inputs": list(policy.input_names()),
                "agent_writable": list(AGENT_WRITABLE),
                "tool_layer_only": list(MUST_COME_FROM_THE_TOOL_LAYER),
            },
            "checked": {
                "rows": verify["rows"],
                "domain_bits": verify["domain_bits"],
                "matches_reference": verify["outputs_match"] and verify["next_state_matches"],
                "reachable_states": proofs["reachable_states"],
                "states_possible": proofs["states_possible"],
                "configurations_visited": proofs["configurations_visited"],
                "rows_proven": proofs["rows_checked"],
                "every_rule_holds": proofs["holds"],
                "violations": proofs["violations"],
            },
            "deny_all": False,
            "compiled_in_ms": round((time.time() - t0) * 1000),
        }
        return Compiled(policy, outs, nxt, netlist, len(policy.input_names()), policy.state_bits, summary)

    @staticmethod
    def denied() -> "Compiled":
        # Shaped like a real summary so a page written for one does not have to care.
        summary = {
            "rules": ["nothing is ever granted"],
            "settings": _settings(Policy()),
            "circuit": {"nand": 0, "latch": 0, "bytes": 0, "depth": 0, "netlist": "0x", "inputs": [],
                        "agent_writable": list(AGENT_WRITABLE), "tool_layer_only": list(MUST_COME_FROM_THE_TOOL_LAYER)},
            "checked": {"rows": 0, "domain_bits": 0, "matches_reference": True, "reachable_states": 0,
                        "states_possible": 0, "configurations_visited": 0, "rows_proven": 0,
                        "every_rule_holds": True, "violations": {}},
            "deny_all": True,
            "compiled_in_ms": 0,
        }
        return Compiled(None, None, None, b"", 0, 0, summary, deny_all=True)


def _netlist_print(compiled: "Compiled | None") -> str | None:
    if compiled is None or compiled.deny_all:
        return None
    import hashlib
    return hashlib.sha256(compiled.netlist).hexdigest()


def _fresh_class_state() -> dict:
    return {"state": 0, "ticks": 0, "grants": 0, "last_grant_tick": None}


class Boundary:
    """Up to five compiled policies, one per class, and per-agent circuit state per class."""

    heartbeat_source = None  # callable -> bool, set by a tool-layer device (cardputer_relay)

    def __init__(self, default_exec: Policy, state_file: Path | None = None) -> None:
        self.lock = threading.Lock()
        self.save_lock = threading.Lock()
        self.policies: dict[str, Compiled | None] = {c: None for c in CLASSES}
        self.agents: dict[str, dict] = {}
        # I-2: the two-digit code of every entry that is waiting, keyed by the call it
        # belongs to — (agent, reason, bit). Generated once, kept while the entry waits,
        # dropped when it stops waiting. Not a secret and not persisted: it exists so that
        # approving means reading the digits off the same screen the request is on.
        self.codes: dict[tuple[str, str, str], str] = {}
        # The big red button's latch. A level, like `blocked`, and unlike `blocked` it is
        # not a bit any circuit reads: while it is on, `request` refuses before it asks one.
        # Persisted with the rules, because a panic stop that a crash or a restart lifts is
        # not a panic stop.
        self.operator_stop: dict = {"on": False, "since": None, "source": None}
        self.state_file = state_file
        saved = self._load()
        if saved is None:
            self.install(DEFAULT_CLASS, default_exec)
            return
        if saved.get("operator_stop"):
            self.operator_stop = {"on": bool(saved["operator_stop"]["on"]), "since": saved["operator_stop"].get("since"),
                                  "source": saved["operator_stop"].get("source")}
        for cls, entry in saved["classes"].items():  # recompiled and re-proven, not trusted from disk
            if entry.get("deny_all"):
                self._switch(cls, Compiled.denied(), save=False)
            else:
                self._switch(cls, Compiled.build(policy_from(entry["settings"])), save=False)
        if self.policies[DEFAULT_CLASS] is None:
            self._switch(DEFAULT_CLASS, Compiled.build(default_exec), save=False)
        # Agents come back where they were: a spent budget stays spent, a sticky halt stays
        # halted, a blocked agent stays blocked. A class state is restored only onto the very
        # circuit it was saved from; otherwise that class starts from reset, as an install does.
        prints = {c: _netlist_print(p) for c, p in self.policies.items()}
        for name, rec in saved.get("agents", {}).items():
            a = self.agent(name)
            a["armed"]["blocked"] = int(bool(rec.get("blocked")))
            a["ticks"] = int(rec.get("ticks", 0))
            a["seen"] = float(rec.get("seen", 0))
            for cls, cs in (rec.get("classes") or {}).items():
                if cls in CLASSES and prints.get(cls) and cs.get("netlist") == prints[cls]:
                    compiled = self.policies[cls]
                    state = int(cs.get("state", 0))
                    if not 0 <= state < (1 << compiled.state_bits):
                        continue
                    a["classes"][cls] = {"state": state, "ticks": int(cs.get("ticks", 0)),
                                         "grants": int(cs.get("grants", 0)), "last_grant_tick": cs.get("last_grant_tick")}

    def _load(self) -> dict | None:
        """The saved rules, or None when there is no file yet. A file that exists but cannot
        be read or parsed stops the console: starting with no rules would start open."""
        if self.state_file is None or not self.state_file.exists():
            return None
        try:
            data = json.loads(self.state_file.read_text())
            classes = data["classes"]
            if not isinstance(classes, dict) or any(c not in CLASSES for c in classes):
                raise ValueError(f"unknown class in {sorted(classes)}")
            if not isinstance(data.get("agents", {}), dict):
                raise ValueError("agents is not an object")
            # The stop latch must come back exactly as it was left. A field that is there
            # and is not a real boolean is refused, not coerced: bool("false") is True, and
            # a mangled file must not decide either way about whether the person said stop.
            stop = data.get("operator_stop")
            if stop is not None and not (isinstance(stop, dict) and isinstance(stop.get("on"), bool)):
                raise ValueError(f"operator_stop is not an object with a boolean 'on': {stop!r}")
            return data
        except Exception as e:
            raise SystemExit(f"cannot read the saved rules in {self.state_file} ({e}); refusing to start "
                             f"without them. Fix or move the file to start from the defaults.")

    def _save(self) -> None:
        if self.state_file is None:
            return
        with self.lock:
            classes = {c: ({"deny_all": True} if p.deny_all else {"settings": p.summary["settings"]})
                       for c, p in self.policies.items() if p is not None}
            # Per agent: each class's latch state with the hash of the netlist it belongs to
            # (a state is meaningless for any other circuit), and the level bit `blocked`.
            # Events (confirm, irreversible, …) are not kept: they belong to the moment.
            prints = {c: _netlist_print(p) for c, p in self.policies.items()}
            # Kept: agents active in the last KEEP_AGENTS_DAYS, newest first, at most KEEP_AGENTS —
            # but a blocked agent is always kept, so forgetting can never unblock anyone.
            cutoff = time.time() - KEEP_AGENTS_DAYS * 86400
            recent = sorted(self.agents.items(), key=lambda kv: kv[1].get("seen", 0), reverse=True)
            keep = [(n, a) for n, a in recent if a["armed"]["blocked"]]
            keep += [(n, a) for n, a in recent if not a["armed"]["blocked"] and a.get("seen", 0) >= cutoff][:KEEP_AGENTS]
            agents = {
                name: {"blocked": a["armed"]["blocked"], "ticks": a["ticks"], "seen": a.get("seen", 0),
                       "classes": {c: dict(cs, netlist=prints[c]) for c, cs in a["classes"].items() if prints[c]}}
                for name, a in keep
            }
            operator_stop = dict(self.operator_stop)
        body = json.dumps({"format": "c3s.console.policies/1", "saved_at": time.time(), "classes": classes,
                           "agents": agents, "operator_stop": operator_stop}, indent=2)
        with self.save_lock:  # request threads save concurrently; one writer at a time
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(body)
            tmp.replace(self.state_file)  # atomic: a crash mid-write never leaves half a file

    # -- what is installed ---------------------------------------------------

    def install(self, cls: str, policy: Policy) -> dict:
        compiled = Compiled.build(policy)  # the slow part, outside the lock
        return self._switch(cls, compiled)

    def deny_all(self, cls: str) -> dict:
        return self._switch(cls, Compiled.denied())

    def remove(self, cls: str) -> dict:
        if cls == DEFAULT_CLASS:
            raise ValueError(f"{DEFAULT_CLASS} always has a circuit: every unclassified call lands there; install other rules instead")
        self._switch(cls, None)
        return {"class": cls, "installed": False}

    def _switch(self, cls: str, compiled: Compiled | None, save: bool = True) -> dict:
        if cls not in CLASSES:
            raise ValueError(f"no such class: {cls!r}; one of {', '.join(CLASSES)}")
        with self.lock:
            self.policies[cls] = compiled
            for a in self.agents.values():  # a new circuit starts every agent from reset
                a["classes"][cls] = _fresh_class_state()
        if save:
            self._save()
        summary = dict(compiled.summary) if compiled else {"installed": False}
        summary["class"] = cls
        return summary

    # -- agents ----------------------------------------------------------------

    def agent(self, name: str) -> dict:
        return self.agents.setdefault(
            name,
            {"ticks": 0, "seen": time.time(), "armed": {bit: 0 for bit in MUST_COME_FROM_THE_TOOL_LAYER}, "bound": {},
             "classes": {c: _fresh_class_state() for c in CLASSES}},
        )

    def arm(self, name: str, bits: dict, bind_to: str | None = None, note: str | None = None) -> dict:
        """The tool layer's bits, held until the agent's next request reads them. Bits no
        installed policy reads are stored all the same: they describe the agent, and a
        later policy may read them. Armed bits are per agent, shared by its classes."""
        unknown = [k for k in bits if k not in MUST_COME_FROM_THE_TOOL_LAYER]
        if unknown:
            raise ValueError(f"not a tool-layer bit: {', '.join(unknown)}")
        with self.lock:
            a = self.agent(name)
            a["seen"] = time.time()
            for bit, value in bits.items():
                a["armed"][bit] = int(bool(value))
                # A person confirms *that* call, not whatever comes next: a confirm bound to
                # a request's reason is delivered only to a request with the same reason, and
                # waits, unspent, while other calls tick past it.
                if bit in ("confirm", "confirm_b"):
                    if value and bind_to:
                        a["bound"][bit] = bind_to
                    else:
                        a["bound"].pop(bit, None)
            entry = {
                "at": time.time(),
                "kind": "tool",
                "agent": name,
                "armed": dict(a["armed"]),
                "bound": dict(a["bound"]),
                "blocked": a["armed"]["blocked"],
                "confirm": a["armed"]["confirm"],
            }
            # What the person said while writing it. A rule candidate, never a rule: nothing
            # reads it back, it is shown next to the write in the log.
            if note:
                entry["note"] = note
            TRANSCRIPTS.appendleft(entry)
        self._save()
        return entry

    @staticmethod
    def _tick(compiled: Compiled, cs: dict, armed: dict, intent: int, blocked: int) -> tuple[int, dict, list, tuple]:
        """One tick of one circuit. Returns (grant, inputs read, reasons, decisive row)."""
        p = compiled.policy
        names = p.input_names()
        inp = {"request": 1, "intent": intent}
        for bit in names[2:]:
            inp[bit] = blocked if bit == "blocked" else armed[bit]
        inputs = sum(inp[bit] << i for i, bit in enumerate(names))
        state_before = cs["state"]
        row = inputs | (state_before << compiled.n_in)
        grant = int(compiled.outs[row])
        cs["state"] = int(compiled.nxt[row])
        cs["ticks"] += 1
        gap = None
        if grant:
            if cs["last_grant_tick"] is not None:
                gap = cs["ticks"] - cs["last_grant_tick"]
            cs["last_grant_tick"] = cs["ticks"]
            cs["grants"] += 1
        # Descriptive only: the verdict is the circuit's; this reads the same counters
        # it reads and says which rule, in the policy's own words.
        why = p.reasons(inp, state_before)
        decisive = (inputs, state_before, grant, cs["state"], compiled.netlist, compiled.n_in, compiled.state_bits)
        return grant, inp, why, decisive, gap

    def request(self, name: str, intent: int, reason: str, cls: str = DEFAULT_CLASS,
                effect: dict | None = None, task: str | None = None) -> dict:
        """One tick: the agent asks, the shared halt circuit answers first, then the
        class's circuit. Both verdicts are recorded; the class's is the decision.

        Unless a person has pressed stop-all. Then nothing is asked: the request is refused
        before any circuit, whoever the agent is and whatever the class, and the entry says
        so in the person's words (`OPERATOR_STOP_SAYS`, `operator_stop: true`).

        `task` (W3/I-6) is a label and nothing else. It is not read by any circuit, does
        not reach `_tick`, and is written onto the entry after the verdict exists — so a
        call that names a job is judged exactly as the same call without one."""
        if cls not in TOOL_CLASSES:
            raise ValueError(f"no such class: {cls!r}; one of {', '.join(TOOL_CLASSES)}")
        intent = int(bool(intent))
        with self.lock:
            a = self.agent(name)
            a["ticks"] += 1
            a["seen"] = time.time()
            armed = a["armed"]
            if self.operator_stop["on"]:
                # A person said stop. No circuit is asked and none moves: nothing is
                # consumed, no cooldown advances, no latch changes — the request meets a
                # wall, and everything behind the wall is exactly where it was for whoever
                # resumes. `class_installed` still says whether the class was ever gated,
                # because that is still true and a person may want to see it; it grants
                # nothing here.
                compiled = self.policies[cls]
                entry = {
                    "at": time.time(),
                    "kind": "request",
                    "agent": name,
                    "class": cls,
                    "class_installed": compiled is not None,
                    "deny_all": bool(compiled is not None and compiled.deny_all),
                    "inputs": {"request": 1, "intent": intent, "blocked": 1, "confirm": armed["confirm"]},
                    "intent": intent,
                    "blocked": 1,
                    "confirm": armed["confirm"],
                    "reason": reason,
                    "granted": False,
                    "tick": a["ticks"],
                    "class_tick": a["classes"][cls]["ticks"],
                    "ticks_since_previous_grant": None,
                    "why": [OPERATOR_STOP_SAYS],
                    "confirm_waiting_for": [],
                    "halt": None,
                    "operator_stop": True,
                    **({"effect": effect} if effect is not None else {}),
                    **({"task": task} if task else {}),
                    "_decisive": {},
                }
                TRANSCRIPTS.appendleft(entry)
            else:
                entry = self._tick_the_circuits(a, name, intent, reason, cls, effect, task)
        self._save()
        return entry

    def _tick_the_circuits(self, a: dict, name: str, intent: int, reason: str, cls: str,
                           effect: dict | None, task: str | None) -> dict:
        """The tick itself, once no person has said stop: the shared halt circuit answers
        first, then the class's circuit. Called with the lock held; appends the entry to the
        transcript and returns it, and saves nothing (the caller does, outside the lock)."""
        armed = a["armed"]
        # A present device is a heartbeat for every agent: a halt policy with a
        # heartbeat rule stops them all when it is unplugged or goes quiet.
        if self.heartbeat_source is not None and self.heartbeat_source():
            armed["heartbeat"] = 1
        # A bound confirm is not this call's: this tick reads it as absent and leaves it armed.
        held = {bit: armed[bit] for bit, reason_for in a["bound"].items() if armed[bit] and reason_for != reason}
        for bit in held:
            armed[bit] = 0
        for bit in a["bound"].keys() - held.keys():
            if armed[bit]:
                a["bound"].pop(bit, None)  # delivered to its call; spent below with the rest
        decisive: dict[str, tuple] = {}

        halt_entry = None
        halt_ok = 1
        halt_c = self.policies[HALT]
        if halt_c is not None and not halt_c.deny_all:
            h_grant, h_inp, h_why, h_dec, _ = self._tick(halt_c, a["classes"][HALT], armed, 1, armed["blocked"])
            halt_ok = h_grant
            halt_entry = {"granted": bool(h_grant), "why": h_why, "inputs": h_inp}
            decisive[HALT] = h_dec

        compiled = self.policies[cls]
        cs = a["classes"][cls]
        blocked = int(armed["blocked"] or not halt_ok)
        gap = None
        if compiled is None:
            # Not gated: no circuit for this class, so nothing to refuse with — unless
            # the shared halt did.
            grant, inp, why = int(halt_ok), {"request": 1, "intent": intent, "blocked": blocked, "confirm": armed["confirm"]}, []
            cs["ticks"] += 1
        elif compiled.deny_all:
            grant, inp, why = 0, {"request": 1, "intent": intent, "blocked": blocked, "confirm": armed["confirm"]}, [
                "class denied outright: no circuit grants here"]
            cs["ticks"] += 1
        else:
            grant, inp, why, dec, gap = self._tick(compiled, a["classes"][cls], armed, intent, blocked)
            decisive[cls] = dec
        if halt_entry is not None and not halt_ok:
            why = [f"halted (shared halt circuit): {'; '.join(halt_entry['why']) or 'no grant'}"] + why
            grant = 0

        for bit in CONSUMED_BY_A_TICK:
            armed[bit] = 0
        armed.update(held)
        entry = {
            "at": time.time(),
            "kind": "request",
            "agent": name,
            "class": cls,
            "class_installed": compiled is not None,
            "deny_all": bool(compiled is not None and compiled.deny_all),
            "inputs": inp,
            "intent": inp.get("intent", intent),
            "blocked": inp.get("blocked", blocked),
            "confirm": inp.get("confirm", 0),
            "reason": reason,
            "granted": bool(grant),
            "tick": a["ticks"],
            "class_tick": cs["ticks"],
            "ticks_since_previous_grant": gap,
            "why": why,
            # A person approved a different version of this agent's call; saying which
            # lets the agent resend exactly that instead of rewording it again.
            "confirm_waiting_for": [a["bound"][b] for b in held if b in a["bound"]] if not grant else [],
            "halt": halt_entry,
            # Carried through untouched (I-1): the adapter's description of what this
            # call would do, for the card a person reads. It decided nothing.
            **({"effect": effect} if effect is not None else {}),
            # I-6: which job this call belongs to, for the person reading the thread.
            # Written after the verdict above; no circuit ever saw it.
            **({"task": task} if task else {}),
            # Carried out of the lock so the chain re-evaluates the circuits that
            # actually decided, not whichever are installed by the time it asks.
            "_decisive": decisive,
        }
        TRANSCRIPTS.appendleft(entry)
        return entry

    # -- reporting ---------------------------------------------------------------

    def _class_view(self, cls: str, cs: dict) -> dict:
        compiled = self.policies[cls]
        counters = compiled.policy.fields(cs["state"]) if compiled and compiled.policy else {
            "gap": 0, "streak": 0, "spent": 0, "window": 0}
        return {
            "installed": compiled is not None,
            "deny_all": bool(compiled and compiled.deny_all),
            "ticks": cs["ticks"],
            "grants": cs["grants"],
            "cooldown_left": counters.get("gap", 0),
            "intent_streak": counters.get("streak", 0),
            "grants_used": counters.get("spent", 0),
            "confirm_window_left": counters.get("window", 0),
            "counters": counters,
        }

    # -- what waits for a person (I-2) -------------------------------------------

    def _code(self, key: tuple[str, str, str], taken: set[str]) -> str:
        """This call's two digits: the one it already has, or a fresh one no other waiting
        entry is using (10–99, so nothing has a leading zero to lose in a chat)."""
        code = self.codes.get(key)
        if code is None:
            free = [str(n) for n in range(10, 100) if str(n) not in taken]
            code = secrets.choice(free) if free else str(secrets.randbelow(90) + 10)
            self.codes[key] = code
        return code

    def _pending(self) -> list[dict]:
        """What a person has to answer, computed here once instead of in three places.

        One entry per (agent, class): that pair's newest request, and only while it stands
        refused. A refusal a person can lift by writing a bit carries that `bit` and a
        two-digit `code`; anything else — a cooldown, a commitment, a spent budget, a block
        somebody has to lift by hand, a whole class denied — carries `waiting_on_time` and
        no code, because no bit a person can write will change it. Called with the lock."""
        seen: set[tuple[str, str]] = set()
        items: list[dict] = []
        for e in TRANSCRIPTS:
            if e.get("kind") != "request":
                continue
            key = (e["agent"], e.get("class", DEFAULT_CLASS))
            if key in seen:
                continue
            seen.add(key)
            if e.get("granted"):
                continue
            why = list(e.get("why") or [])
            bit = None
            halt = e.get("halt")
            if e.get("operator_stop"):
                # A person said stop; no bit lifts that, only resume-all. And once they
                # have resumed, the refusal is history: the agent has not asked again, so
                # nothing is waiting for anyone. Leaving it in kept a card on the page
                # that said a person had pressed stop, after that stop was lifted, with
                # no button on it — an instruction to do nothing, which is worse on the
                # approvals page than on any other.
                if not self.operator_stop["on"]:
                    continue
            elif halt and not halt.get("granted"):
                # The shared halt refused, and the class's circuit then said "blocked is
                # high" because the halt made it so — not because a person blocked this
                # agent. Reading the class's words here made every halted call a dead end
                # on the page: something waiting for a person that no button could lift.
                # The halt's own words say what would lift it: a confirm, unless the
                # person's own `blocked` is what is holding the halt down.
                h_why = list(halt.get("why") or [])
                own_block = int((halt.get("inputs") or {}).get("blocked", 0)) or any(w.startswith(BLOCKED_SAYS) for w in h_why)
                if not own_block and any(w.startswith("halted") for w in h_why):
                    bit = "confirm"
            elif not any(w.startswith(BLOCKED_SAYS) for w in why):
                for w in why:
                    bit = next((b for rx, b in PERSON_RESOLVES if rx.search(w)), None)
                    if bit:
                        break
            item = {
                "agent": e["agent"], "class": key[1], "tick": e.get("tick", 0),
                "reason": e.get("reason", ""), "why": why, "at": e.get("at", 0),
                "bit": bit, "waiting_on_time": bit is None, "armed": False,
            }
            if e.get("effect") is not None:
                item["effect"] = e["effect"]
            # I-6: the job this call was sent for, so the page, the device and the chat all
            # say "this is part of that" without looking the call up again. A label on the
            # entry the console already computed; it changes nothing about the entry.
            if e.get("task"):
                item["task"] = e["task"]
            items.append(item)
        live: set[tuple[str, str, str]] = set()
        taken = set(self.codes.values())
        for item in items:
            if item["bit"] is None:
                continue
            key3 = (item["agent"], item["reason"], item["bit"])
            live.add(key3)
            item["code"] = self._code(key3, taken)
            taken.add(item["code"])
            # Armed means a confirm is waiting for *this* call: bound to it, or unbound and
            # so good for whatever the agent sends next.
            a = self.agents.get(item["agent"]) or {}
            bound = (a.get("bound") or {}).get(item["bit"])
            item["armed"] = bool((a.get("armed") or {}).get(item["bit"])) and (bound is None or bound == item["reason"])
        self.codes = {k: v for k, v in self.codes.items() if k in live}  # a code lives exactly as long as its entry
        return items

    def code_for_call(self, name: str, reason: str, bit: str) -> str | None:
        """The digits currently shown for this one call, or None if nothing is waiting."""
        with self.lock:
            return self.codes.get((name, reason, bit))

    def stop_all(self, stop: bool, source: str = "page", note: str | None = None) -> list[str]:
        """The big red button, and the deliberate way back.

        Pressing it sets the operator-stop latch: from that moment every request from every
        agent — the names known now, a name first seen later, an agent that renamed itself —
        in every class, gated or not, is refused before any circuit is asked, until a person
        resumes. It also writes `blocked` for every agent the console knows, one entry each,
        so the per-name level a person can read on the switches panel agrees with the latch;
        that write is the old behaviour and is no longer what makes the stop hold. (It used
        to be all there was, and the red team renamed an agent past it: `blocked` is
        per-name, and a name nobody had seen had no bit to be high.)

        Resuming clears the latch, lowers every known agent's `blocked`, and resets every
        agent's shared-halt state. A sticky halt circuit waits for "a confirm" to lift; but
        resume-all *is* the person lifting it (token, and the typed word on the page), and
        the confirm bit belongs to the calls in the classes — arming one here could approve
        an irreversible call that was waiting. Resetting the halt's state is what installing
        a halt circuit does to every agent anyway.

        Both directions write one `stop` entry to the transcript before the per-name ones,
        so the press is on the record even when no agent is known yet."""
        now = time.time()
        with self.lock:
            names = sorted(self.agents)
            self.operator_stop = ({"on": True, "since": now, "source": source} if stop
                                  else {"on": False, "since": None, "source": None})
            press = {"at": now, "kind": "stop", "stopped": stop, "source": source, "agents": names}
            if note:
                press["note"] = note
            TRANSCRIPTS.appendleft(press)
        for name in names:
            self.arm(name, {"blocked": 1 if stop else 0}, note=note)["source"] = source
        if not stop and names:
            with self.lock:
                for name in names:
                    self.agents[name]["classes"][HALT] = _fresh_class_state()
        self._save()  # unconditionally: the latch must reach disk even with nobody known
        return names

    def status(self) -> dict:
        # I-3. Read before the lock (it is a file), and only ever as a boolean and a time:
        # the hash of an agent's token is not something a page needs, and /api/state has
        # no token of its own. `bound` above is the confirm-binding map and is not this.
        try:
            bindings, bindings_error = agent_bindings(), None
        except BindingsUnreadable as e:
            # Every request is being refused; the page must be able to say why rather than
            # go blank, so this is reported instead of raised.
            bindings, bindings_error = {}, str(e)
        with self.lock:
            agents = []
            for name, a in sorted(self.agents.items()):
                by_class = {c: self._class_view(c, a["classes"][c]) for c in CLASSES}
                flat = by_class[DEFAULT_CLASS]
                agents.append(
                    {
                        "agent": name,
                        "ticks": a["ticks"],
                        "grants": flat["grants"],
                        "cooldown_left": flat["cooldown_left"],
                        "intent_streak": flat["intent_streak"],
                        "grants_used": flat["grants_used"],
                        "confirm_window_left": flat["confirm_window_left"],
                        "counters": flat["counters"],
                        "by_class": by_class,
                        "armed": dict(a["armed"]),
                        "bound": dict(a["bound"]),
                        "armed_blocked": a["armed"]["blocked"],
                        "armed_confirm": a["armed"]["confirm"],
                        # I-3: has this name got a token of its own, or is anything on this
                        # machine free to speak as it? `null` when the store could not be
                        # read: "unknown" must not render as "unbound" in a consumer that
                        # has not learned about `agent_tokens.store_error` yet.
                        "token_bound": (name in bindings) if bindings_error is None else None,
                        "token_bound_at": bindings.get(name, {}).get("bound_at"),
                    }
                )
            policies = {c: (dict(p.summary, **{"class": c}) if p else None) for c, p in self.policies.items()}
            return {
                "policy": policies[DEFAULT_CLASS],
                "policies": policies,
                "classes": list(TOOL_CLASSES),
                "halt_class": HALT,
                "default_class": DEFAULT_CLASS,
                "agents": agents,
                # The big red button's latch. When `on`, every request from every agent —
                # including any not in `agents` yet — is refused until resume-all. The page
                # reads this rather than counting blocked names, because the count is not
                # what says whether everything is stopped.
                "operator_stop": dict(self.operator_stop),
                # I-2: one list of what waits for a person, for the page, the device and
                # the chat bot alike. Nothing downstream derives it again.
                "pending": self._pending(),
                # I-6: the jobs a person has filed and not closed. Here rather than behind
                # its own call because this is how an agent's adapter reads what it was
                # asked to do — the same list the page draws, and reading it grants nothing.
                "tasks": TASKS.listing(only_open=True),
                "transcript": [{k: v for k, v in e.items() if not k.startswith("_")} for e in list(TRANSCRIPTS)[:60]],
                "chain": {
                    "enabled": CHAIN is not None,
                    "rpc": BSC_RPC if CHAIN is not None else None,
                    "chain_id": CHAIN.chain_id if CHAIN is not None else None,
                    "deployed": False,
                },
                "started_at": STARTED_AT,
                "default_rules": FLY_DEFAULT.describe(),
                # I-3, at the console level rather than per agent: whether an unbound name
                # is trusted here at all, and whether the store could be read. When
                # `store_error` is set every request is being refused, and a page that
                # shows agents as "unbound" without saying this would be lying.
                "agent_tokens": {"required": REQUIRE_AGENT_TOKEN, "bound": len(bindings),
                                 "store_error": bindings_error},
            }


def status_for_agent(status: dict, name: str) -> dict:
    """`GET /api/state` as one agent's own token sees it over the network (F2, 2026-09-17).

    An agent token was introduced so a name could authenticate its own requests (I-3). The
    LAN rule then let any bound token read the whole console, and the red team read
    `treasury-bot`'s pending `[exec] wire 50000 USDC to 0xBEEF` back with `reader`'s token —
    the exact `reason` string a confirm binds to, which docs/ISOLATION.md argues a contained
    agent cannot obtain. So an agent identity gets its own rows: its agent record, its
    pending calls, and the transcript entries that are about it or about the rules. The rules
    and what was checked about them are not secret and stay. Other agents' calls, notes and
    armed bits go; a `stop` entry keeps the fact of the press and loses the list of names.
    The open tasks stay: they are the person's jobs for the agent to read (I-6), not another
    agent's call. `view` says the reply was narrowed, so a consumer never mistakes it for the
    whole console. Loopback and the operator keep the full view."""
    out = dict(status)
    out["agents"] = [a for a in status.get("agents", []) if a.get("agent") == name]
    out["pending"] = [p for p in status.get("pending", []) if p.get("agent") == name]
    kept = []
    for e in status.get("transcript", []):
        if e.get("agent") == name:
            kept.append(e)
        elif e.get("kind") == "policy":
            kept.append(e)
        elif e.get("kind") == "stop":
            kept.append({k: v for k, v in e.items() if k != "agents"})
    out["transcript"] = kept
    out["view"] = {"scope": "agent", "agent": name}
    return out


class Chain:
    """A public node asked to re-evaluate one row, read-only, with nothing deployed.

    The evaluator's bytecode and selector come from the circuits repository, but the
    netlist is whatever policy is loaded here: a user's own compiled rules go to the
    chain exactly as the published circuit does."""

    def __init__(self, rpc: str) -> None:
        self.rpc = rpc
        doc = json.loads((REPO / "docs" / "sim" / "onchain.json").read_text())
        self.code = doc["evaluator"]["runtime_bytecode"]
        self.selector = doc["evaluator"]["selector"].removeprefix("0x")
        self.chain_id: int | None = None

    def _call(self, method: str, params: list, timeout: int = 20):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        last: Exception | None = None
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    self.rpc, data=body, headers={"content-type": "application/json", "user-agent": "reflex-console"}
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    out = json.loads(resp.read())
                if "error" in out:
                    raise RuntimeError(out["error"].get("message", "rpc error"))
                return out["result"]
            except Exception as e:
                last = e
                if attempt == 0:
                    time.sleep(0.6)
        raise last  # type: ignore[misc]

    def chain(self) -> int:
        if self.chain_id is None:
            self.chain_id = int(self._call("eth_chainId", []), 16)
        return self.chain_id

    def evaluate(self, netlist: bytes, n_in: int, n_out: int, n_state: int, inputs: int, state: int) -> tuple[int, int]:
        word = lambda v: f"{v:064x}"  # noqa: E731
        head = word(6 * 32) + "".join(word(v) for v in (n_in, n_out, n_state, inputs, state))
        body = word(len(netlist)) + netlist.hex() + "00" * ((32 - len(netlist) % 32) % 32)
        data = "0x" + self.selector + head + body
        raw = self._call("eth_call", [{"to": CHAIN_ADDRESS, "data": data}, "latest", {CHAIN_ADDRESS: {"code": self.code}}])[2:]
        return int(raw[:64], 16), int(raw[64:128], 16)


def policy_from(payload: dict) -> Policy:
    """Rules are untrusted input: whole numbers in range, or nothing is installed."""
    def whole(key: str, limit: int) -> int:
        value = payload.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
            raise ValueError(f"{key} must be a whole number")
        value = int(value)
        if not 0 <= value <= limit:
            raise ValueError(f"{key}={value} outside 0..{limit}")
        return value

    def flag(key: str, default: bool) -> bool:
        """Not bool(): the string "false" is truthy, and these decide whether a rule
        that only the tool layer can satisfy is installed at all."""
        value = payload.get(key, default)
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        return value

    return Policy(
        min_gap_ticks=whole("min_gap_ticks", 255),
        commit_ticks=whole("commit_ticks", 255),
        forbid_when_blocked=flag("forbid_when_blocked", True),
        max_grants=whole("max_grants", 255),
        confirm_window_ticks=whole("confirm_window_ticks", 255),
        sticky_block=flag("sticky_block", False),
        heartbeat_ticks=whole("heartbeat_ticks", 255),
        confirm_per_irreversible=flag("confirm_per_irreversible", False),
        trip_after_failures=whole("trip_after_failures", 255),
        # The eleventh rule, and the only one that reads the circuit's own verdict: an
        # agent that keeps asking for what it cannot have is halted rather than left to
        # hammer the boundary. It was held out of this console for part of 2026-09-17,
        # because its reset did not work — while tripped every tick is a refusal, so the
        # tick carrying the person's confirm re-tripped the breaker and no confirm ever
        # lifted it, which a refusal saying "a confirm resets it" made worse than no rule
        # at all. Fixed in the circuits library the same night, and the half that was
        # missing is now a monitor of its own (`violations.refusal_reset`), so the reset
        # is proven and not merely intended.
        trip_after_refusals=whole("trip_after_refusals", 255),
        two_key=flag("two_key", False),
    )


def class_from(payload: dict, allowed: tuple[str, ...]) -> str:
    cls = payload.get("class", DEFAULT_CLASS)
    if not isinstance(cls, str) or cls not in allowed:
        raise ValueError(f"class must be one of {', '.join(allowed)}")
    return cls


def effect_from(payload: dict) -> dict | None:
    """I-1: what the call would do, in the adapter's words, for the card a person approves.

    Display only: it reaches no circuit and cannot change a verdict — `reason` is still the
    identity of the call and the key a confirm binds to. A wrong shape is refused rather
    than trimmed, because trimming would be the console interpreting an effect."""
    effect = payload.get("effect")
    if effect is None:
        return None
    if not isinstance(effect, dict):
        raise ValueError("effect must be an object")
    if effect.get("kind") not in EFFECT_KINDS:
        raise ValueError(f"effect.kind must be one of {', '.join(EFFECT_KINDS)}")
    out: dict = {"kind": effect["kind"]}
    for key, limit in EFFECT_STRINGS:
        value = effect.get(key)
        if value is None:
            out[key] = None
        elif not isinstance(value, str):
            raise ValueError(f"effect.{key} must be a string or null")
        elif len(value) > limit:
            raise ValueError(f"effect.{key} is longer than {limit} characters")
        else:
            out[key] = value
    reversible = effect.get("reversible")
    if reversible is not None and not isinstance(reversible, bool):
        raise ValueError("effect.reversible must be true, false or null")
    out["reversible"] = reversible
    details = effect.get("details")
    if details is not None:
        if not isinstance(details, dict):
            raise ValueError("effect.details must be an object")
        if len(json.dumps(details)) > 1024:
            raise ValueError("effect.details is longer than 1 KB")
        out["details"] = details
    return out


CHAIN_WAIT_S = float(os.environ.get("CHAIN_WAIT_S", "1.5"))


def chain_check(entry: dict, decisive: dict, cls: str) -> None:
    """Re-evaluate the rows that decided, read-only on a public node, and record the result
    in the transcript entry itself (the same dict the transcript holds)."""
    circuits, agrees_all, error = {}, True, None
    for label, (inputs, state, grant, next_state, netlist, n_in, state_bits) in decisive.items():
        try:
            got, got_state = CHAIN.evaluate(netlist, n_in, 1, state_bits, inputs, state)
            ok = (got, got_state) == (grant, next_state)
            circuits[label] = {"grant": got, "agrees": ok}
            agrees_all = agrees_all and ok
        except Exception as e:
            error = str(e)[:120]
            break
    if error is not None:
        entry["chain"] = {"error": error}
    else:
        decided = circuits.get(cls) or circuits.get(HALT) or {}
        entry["chain"] = {"chain_id": CHAIN.chain(), "grant": decided.get("grant"), "agrees": agrees_all,
                          "circuits": circuits, "deployed": False}


def _own_ipv4_addresses() -> set[str]:
    """The IPv4 addresses this machine answers to on a network, computed once at startup.

    Bound to 127.0.0.1 the console is reachable from here only and this set is unused.
    Bound to 0.0.0.0 — which `c3s up --lan` does so that a phone on the same Wi-Fi can
    approve — a request from that phone arrives with `Host: 192.168.x.y:8765`, a name this
    machine really does answer to; refusing it would refuse the phone. Loopback and
    link-local are left out (127.* is already allowed by name, 169.254.* reaches no
    phone), and an IPv6 address is not covered: the pairing URL is IPv4.

    This does not weaken the DNS-rebinding check: another site's page still arrives under
    that site's name, which is not in here. It is the set `c3s_cli/paths.py:lan_ipv4()`
    puts in the pairing QR, computed the same way, so the address a phone scans is one the
    console accepts.
    """
    import socket

    addresses: set[str] = set()
    try:                                     # the address the default route would use
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))  # a documentation address; nothing is sent
            addresses.add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    try:                                     # every interface that has an IPv4 address
        import fcntl
        import struct

        for _index, name in socket.if_nameindex():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                packed = fcntl.ioctl(sock.fileno(), 0xC0206921,  # SIOCGIFADDR
                                     struct.pack("16s16x", name.encode()[:15]))
                addresses.add(socket.inet_ntoa(packed[20:24]))
            except OSError:
                pass
            finally:
                sock.close()
    except (ImportError, OSError):
        pass
    return {a for a in addresses if not a.startswith(("127.", "169.254.")) and a != "0.0.0.0"}


# Only when the console is bound to every interface is a LAN name of this machine a name
# it answers to; on loopback the set stays empty and nothing changes.
LAN_HOSTS = _own_ipv4_addresses() if HOST in ("0.0.0.0", "", "::") else set()


# ======= W2: the tool layer's own map — which tool, which class, what cannot be undone ===
#
# Two questions the circuits cannot answer about themselves:
#
#   which circuit answers a call    the tool's *class*, decided from its name by the
#                                   adapters (spend / message / exec / files)
#   must a person confirm it        whether the call is *irreversible*, which arms the
#                                   tool-layer bit a `confirm_per_irreversible` rule reads
#
# Both are promises made by the layer that runs the tools, not properties the circuit
# proves. The circuit proves "irreversible and no unspent confirm ⇒ no grant"; that
# `send_email` is irreversible is this layer's word, and a person's to correct — which is
# what GET/POST /api/classes is for. The names come from three places, and the answer says
# which: the rules this project ships, the user's own override files, or nowhere at all
# (a name no rule mentions lands in `exec`, which always has a circuit).

CONSOLE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CONSOLE_DIR / "adapters"))
import reflex_classes as tool_classes  # noqa: E402  (the adapters' own module: one map, not two)

# The override files. Defaulted under CONFIG_DIR, where `reflex_classes` looks for them
# when an adapter was started without REFLEX_CLASS_FILE / REFLEX_IRREVERSIBLE_TOOLS_FILE,
# so writing the table here reaches an adapter nobody reconfigured.
CLASS_FILE = Path(os.environ.get("REFLEX_CLASS_FILE") or (CONFIG_DIR / tool_classes.CLASS_FILE_NAME)).expanduser()
IRREVERSIBLE_TOOLS_FILE = Path(os.environ.get("REFLEX_IRREVERSIBLE_TOOLS_FILE")
                               or (CONFIG_DIR / tool_classes.IRREVERSIBLE_FILE_NAME)).expanduser()
# The one file "write it for me" may touch. Fixed here, at startup, and never taken from a
# request: an endpoint that writes JSON wherever the body says is a file-write primitive.
CLAUDE_SETTINGS = Path(os.environ.get("REFLEX_CLAUDE_SETTINGS") or (Path.home() / ".claude" / "settings.json")).expanduser()

CLASS_FILE_HEADER = (
    "# Which class of circuit answers a tool call: one `glob class` per line, first match\n"
    "# wins, `#` starts a comment, and `!default` keeps the built-in rules after your own.\n"
    "# Written by the console's \"My tools\" table; safe to edit by hand. The adapters\n"
    "# re-read it when it changes — nothing needs restarting.\n"
)
IRREVERSIBLE_FILE_HEADER = (
    "# Tool names whose effect cannot be taken back: one glob per line, `#` comments, and\n"
    "# `!default` keeps the built-in list after your own. A name here arms the tool-layer\n"
    "# bit `irreversible`, which a `confirm_per_irreversible` rule requires a person's\n"
    "# confirm for. Written by the console's \"My tools\" table; safe to edit by hand.\n"
)

# `[class] tool_name [irreversible]: args` — what every adapter writes as the reason
# (claude_code_hook.py:219, mcp_proxy.py:174). The `: ` is required, so a hand-written
# reason without one ("[exec] the one call a person approved") is not mistaken for a tool.
# The bnbagent wallet's reasons name a signing method rather than a tool and do not match,
# by design: its class is configured, not derived from a name.
TOOL_IN_REASON = re.compile(r"^\[(?:spend|message|exec|files)\]\s+(?P<tool>[^\n]{1,80}?)(?:\s+\[irreversible\])?:(?:\s|$)")
GLOB_CHARS = set("*?[")


def tools_seen_in_transcript() -> dict[str, dict]:
    """Every tool name this console has been asked about, from the transcript.

    Since this console started, and no further back: the transcript is in memory and
    bounded (TRANSCRIPT entries), so a name that scrolled off is gone from here too.
    """
    out: dict[str, dict] = {}
    for e in list(TRANSCRIPTS):
        if e.get("kind") != "request":
            continue
        m = TOOL_IN_REASON.match(str(e.get("reason") or ""))
        if not m:
            continue
        tool = m.group("tool").strip()
        if not tool:
            continue
        rec = out.setdefault(tool, {"seen": 0, "agents": [], "last_at": 0.0, "asked_as": []})
        rec["seen"] += 1
        rec["last_at"] = max(rec["last_at"], float(e.get("at") or 0))
        for key, value in (("agents", e.get("agent")), ("asked_as", e.get("class"))):
            if value and value not in rec[key] and len(rec[key]) < 8:
                rec[key].append(value)
    return out


def writable_as_a_rule(tool: str) -> str | None:
    """Why this name cannot be written to the rule files, or None if it can.

    The file format is one rule per line, split on whitespace, `#` starting a comment —
    so a name with a space in it (a name only a hostile or careless caller produces) can
    be *shown*, and classified by the built-in rules, but not written as an override.
    Saying so beats writing a line that would silently parse as something else.
    """
    if not tool or len(tool) > 120:
        return "a tool name is 1 to 120 characters"
    if any(c.isspace() for c in tool):
        return "the rule files split each line on whitespace, so a name with a space cannot be written as a rule"
    if "#" in tool:
        return "`#` starts a comment in the rule files"
    if tool.startswith("!"):
        return "a line starting with `!` is reserved (`!default`)"
    return None


def read_overrides() -> dict:
    """One read of each override file: the user's own lines, whether the built-ins are
    kept after them, and the effective list the adapters compute from the same file."""
    rules_read = tool_classes.read_override_rules(str(CLASS_FILE)) if CLASS_FILE.exists() else None
    irr_read = tool_classes.read_override_irreversible(str(IRREVERSIBLE_TOOLS_FILE)) if IRREVERSIBLE_TOOLS_FILE.exists() else None
    own_rules, keeps_rules = rules_read if rules_read else ((), True)
    own_irr, keeps_irr = irr_read if irr_read else ((), True)
    return {
        "own_rules": list(own_rules), "keeps_rules": bool(keeps_rules), "rules_file_read": rules_read is not None,
        "own_irreversible": list(own_irr), "keeps_irreversible": bool(keeps_irr),
        "irreversible_file_read": irr_read is not None,
        "rules": list(own_rules) + (list(tool_classes.DEFAULT_RULES) if keeps_rules else []),
        "irreversible": list(own_irr) + (list(tool_classes.DEFAULT_IRREVERSIBLE_TOOLS) if keeps_irr else []),
    }


def tool_row(tool: str, ov: dict, seen: dict | None) -> dict:
    """One row of "My tools": the class, the irreversible flag, and where each came from."""
    idx = tool_classes.matching_rule_index(tool, ov["rules"])
    cls = ov["rules"][idx][1] if idx is not None else tool_classes.DEFAULT_CLASS
    pattern = ov["rules"][idx][0] if idx is not None else None
    source = "unseen" if idx is None else ("override" if idx < len(ov["own_rules"]) else "builtin")

    own_hit = tool_classes.matching_irreversible(tool, ov["own_irreversible"])
    builtin_hit = tool_classes.matching_irreversible(tool, tool_classes.DEFAULT_IRREVERSIBLE_TOOLS) if ov["keeps_irreversible"] else None
    hit = own_hit or builtin_hit
    why_not_writable = writable_as_a_rule(tool)
    return {
        "tool": tool,
        "class": cls,
        "class_pattern": pattern,
        "source": source,               # override | builtin | unseen (no rule names it)
        "irreversible": hit is not None,
        "irreversible_pattern": hit,
        "irreversible_source": "override" if own_hit else ("builtin" if builtin_hit else "unseen"),
        # A built-in *glob* that marks this name cannot be turned off for one tool: the
        # file format has no "not this one". The checkbox says so instead of pretending.
        "locked_irreversible": builtin_hit is not None,
        "writable": why_not_writable is None,
        "why_not_writable": why_not_writable,
        "seen": (seen or {}).get("seen", 0),
        "agents": (seen or {}).get("agents", []),
        "last_at": (seen or {}).get("last_at") or None,
        "asked_as": (seen or {}).get("asked_as", []),
    }


def hook_command(script: str, agent: str) -> str:
    """The command a Claude Code hook entry runs, with real absolute paths.

    The class and irreversible files are named explicitly even though `reflex_classes`
    would find them anyway: a hook installed today must keep working if someone later
    starts the console with a different config dir."""
    return (f"REFLEX_AGENT={agent} REFLEX_CONSOLE=http://127.0.0.1:{PORT} "
            f"REFLEX_CLASS_FILE={CLASS_FILE} REFLEX_IRREVERSIBLE_TOOLS_FILE={IRREVERSIBLE_TOOLS_FILE} "
            f"{sys.executable} {CONSOLE_DIR / 'adapters' / script}")


HOOK_MATCHER = "Bash|Write|Edit|MultiEdit|NotebookEdit|mcp__.*"


def claude_settings_block(agent: str) -> dict:
    """Exactly what goes in settings.json for both hooks — the block the page shows, the
    block the clipboard gets, and the block "write it for me" merges. One source."""
    return {"hooks": {
        "PreToolUse": [{"matcher": HOOK_MATCHER, "hooks": [
            {"type": "command", "timeout": 20, "command": hook_command("claude_code_hook.py", agent)}]}],
        "PostToolUse": [{"matcher": HOOK_MATCHER, "hooks": [
            {"type": "command", "timeout": 10, "command": hook_command("claude_code_post_hook.py", agent)}]}],
    }}


def bnbagent_snippet() -> str:
    """The indented example at the top of adapters/bnbagent_boundary.py's own docstring,
    read from the file rather than copied: importing it would need the bnbagent package,
    which is not in this console's environment."""
    try:
        text = (CONSOLE_DIR / "adapters" / "bnbagent_boundary.py").read_text(encoding="utf-8")
    except OSError:
        return ""
    out: list[str] = []
    for line in text.splitlines()[1:]:
        if line.startswith("    "):
            out.append(line[4:])
        elif out and not line.strip():
            out.append("")
        elif out:
            break
    return "\n".join(out).strip("\n")


def merge_claude_settings(existing: dict, block: dict) -> tuple[dict, list[str]]:
    """`existing` plus the hooks in `block`, without touching anything else.

    A matcher that is already there gets the command appended to its own hooks list; an
    identical command is left alone (so this is idempotent and never duplicates). Nothing
    is removed, reordered or rewritten: the user's file is the user's.
    """
    merged = json.loads(json.dumps(existing))  # a copy: the caller keeps its "before"
    added: list[str] = []
    hooks = merged.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("`hooks` in that file is not an object; the console will not rewrite it")
    for event, entries in block["hooks"].items():
        current = hooks.setdefault(event, [])
        if not isinstance(current, list):
            raise ValueError(f"`hooks.{event}` in that file is not a list; the console will not rewrite it")
        for entry in entries:
            mine = entry["hooks"][0]
            same_matcher = next((c for c in current if isinstance(c, dict) and c.get("matcher") == entry["matcher"]
                                 and isinstance(c.get("hooks"), list)), None)
            if same_matcher is None:
                current.append(json.loads(json.dumps(entry)))
                added.append(f"{event}: a new matcher {entry['matcher']!r} running {Path(mine['command'].split()[-1]).name}")
                continue
            if any(isinstance(h, dict) and h.get("command") == mine["command"] for h in same_matcher["hooks"]):
                continue  # already exactly this command
            same_matcher["hooks"].append(json.loads(json.dumps(mine)))
            added.append(f"{event}: one more command under the existing matcher {entry['matcher']!r}")
    return merged, added


def settings_diff(before: dict | None, after: dict) -> str:
    import difflib

    old = (json.dumps(before, indent=2, ensure_ascii=False) + "\n").splitlines(keepends=True) if before is not None else []
    new = (json.dumps(after, indent=2, ensure_ascii=False) + "\n").splitlines(keepends=True)
    label = str(CLAUDE_SETTINGS)
    return "".join(difflib.unified_diff(old, new, fromfile=f"{label} (now)" if before is not None
                                        else f"{label} (does not exist)", tofile=f"{label} (after)", n=3))


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_overrides(changes: list[dict]) -> dict:
    """Apply the table's changes to the two override files, or explain why one cannot be.

    A class is written as an exact-name line ahead of the built-ins (first match wins), and
    dropped again when the built-ins already give that answer — so the file stays the list
    of the user's *differences*, and the table can honestly say where each answer came from.

    Turning irreversible *off* for a name a built-in glob covers is refused rather than
    done, because the only way to express it in this format is to drop the glob, which
    would also unmark every tool the glob covers that this console has never seen. That is
    exactly the silent widening the whole project exists to avoid; a person who means it
    edits the file by hand.
    """
    ov = read_overrides()
    own_rules = list(ov["own_rules"])
    own_irr = list(ov["own_irreversible"])
    changed: list[str] = []

    for change in changes:
        tool = str(change.get("tool", ""))
        why = writable_as_a_rule(tool)
        if why:
            raise ValueError(f"{tool!r} cannot be written as a rule: {why}")
        if "class" in change and change["class"] is not None:
            cls = str(change["class"])
            if cls not in TOOL_CLASSES:
                raise ValueError(f"class must be one of {', '.join(TOOL_CLASSES)}; {cls!r} is not")
            rest = [r for r in own_rules if r[0] != tool]
            without = rest + (list(tool_classes.DEFAULT_RULES) if ov["keeps_rules"] else [])
            if tool_classes.classify(tool, without) == cls:
                if len(rest) != len(own_rules):
                    own_rules = rest  # the rules already say this: no line of our own needed
                    changed.append(f"{tool}: class {cls} (the built-in rules already say so; the override line is gone)")
            else:
                own_rules = [(tool, cls)] + rest
                changed.append(f"{tool}: class {cls}")
        if "irreversible" in change and change["irreversible"] is not None:
            want = bool(change["irreversible"])
            builtin_hit = (tool_classes.matching_irreversible(tool, tool_classes.DEFAULT_IRREVERSIBLE_TOOLS)
                           if ov["keeps_irreversible"] else None)
            has_own = tool_classes.matching_irreversible(tool, own_irr) is not None
            if want:
                if not builtin_hit and not has_own:
                    own_irr = own_irr + [tool]
                    changed.append(f"{tool}: irreversible — a person must confirm it")
            else:
                if builtin_hit:
                    raise ValueError(
                        f"{tool!r} is irreversible because of the built-in pattern {builtin_hit!r}, and this file "
                        f"format has no way to say \"not this one\". Turning it off for {tool!r} alone would mean "
                        f"dropping {builtin_hit!r}, which also unmarks every tool it covers that this console has "
                        f"never seen. Edit {IRREVERSIBLE_TOOLS_FILE} by hand — remove the `!default` line and write "
                        f"the list you want — if that is really what you mean.")
                if has_own:
                    own_irr = [p for p in own_irr if p != tool]
                    changed.append(f"{tool}: reversible again (the override line is gone)")

    written: list[str] = []
    if own_rules != ov["own_rules"] or (own_rules and not ov["rules_file_read"]):
        body = "".join(f"{pattern} {cls}\n" for pattern, cls in own_rules)
        _atomic_write(CLASS_FILE, CLASS_FILE_HEADER + "\n" + body + ("!default\n" if ov["keeps_rules"] else ""))
        written.append(str(CLASS_FILE))
    if own_irr != ov["own_irreversible"] or (own_irr and not ov["irreversible_file_read"]):
        body = "".join(f"{pattern}\n" for pattern in own_irr)
        _atomic_write(IRREVERSIBLE_TOOLS_FILE,
                      IRREVERSIBLE_FILE_HEADER + "\n" + body + ("!default\n" if ov["keeps_irreversible"] else ""))
        written.append(str(IRREVERSIBLE_TOOLS_FILE))
    return {"written": written, "changed": changed}


def class_map(handler: "Handler | None" = None, agent: str = "claude-code:mine") -> dict:
    """GET /api/classes: the whole tool→class map, and what a person needs to connect.

    Read fresh from the files every time, never cached: an adapter picks the files up
    without a restart, and an answer here that lagged behind them would be a lie about
    what the next call will be judged by.
    """
    ov = read_overrides()
    seen = tools_seen_in_transcript()
    names = set(seen) | {p for p, _c in ov["own_rules"] if not (set(p) & GLOB_CHARS)} \
        | {p for p in ov["own_irreversible"] if not (set(p) & GLOB_CHARS)}
    rows = [tool_row(tool, ov, seen.get(tool)) for tool in sorted(names)]
    local = bool(handler is not None and handler.client_address and handler.client_address[0] in ("127.0.0.1", "::1"))
    return {
        "classes": list(TOOL_CLASSES),
        "default_class": tool_classes.DEFAULT_CLASS,
        "class_file": str(CLASS_FILE), "class_file_exists": CLASS_FILE.exists(),
        "irreversible_file": str(IRREVERSIBLE_TOOLS_FILE), "irreversible_file_exists": IRREVERSIBLE_TOOLS_FILE.exists(),
        "builtin": {"rules": [list(r) for r in tool_classes.DEFAULT_RULES],
                    "irreversible": list(tool_classes.DEFAULT_IRREVERSIBLE_TOOLS)},
        "override": {"rules": [list(r) for r in ov["own_rules"]], "irreversible": list(ov["own_irreversible"]),
                     "keeps_builtin_rules": ov["keeps_rules"], "keeps_builtin_irreversible": ov["keeps_irreversible"]},
        "effective": {"rules": [list(r) for r in ov["rules"]], "irreversible": list(ov["irreversible"])},
        "tools": rows,
        "what_it_decides": {
            "class": "which circuit answers the call (spend / message / exec / files); an unclassified name lands in exec",
            "irreversible": "whether a person must confirm it, under a `confirm_per_irreversible` rule",
            "promise": "both are the tool layer's promise about a name, not something the circuit proves",
        },
        "hot_reload": ("the hook is a fresh process per tool call, and the MCP proxy re-reads these files when they "
                       "change, so a change here applies to the next call without restarting anything"),
        "connect": {
            "local": local,
            "console_url": f"http://127.0.0.1:{PORT}",
            "agent": agent,
            "python": sys.executable,
            "repo": str(CONSOLE_DIR),
            "hook_pre": str(CONSOLE_DIR / "adapters" / "claude_code_hook.py"),
            "hook_post": str(CONSOLE_DIR / "adapters" / "claude_code_post_hook.py"),
            "proxy": str(CONSOLE_DIR / "adapters" / "mcp_proxy.py"),
            "bnbagent": str(CONSOLE_DIR / "adapters" / "bnbagent_boundary.py"),
            "claude_settings": str(CLAUDE_SETTINGS),
            "claude_settings_exists": CLAUDE_SETTINGS.exists(),
            "claude_settings_block": claude_settings_block(agent),
            "bnbagent_snippet": bnbagent_snippet(),
        },
    }


def api_classes_get(h: "Handler") -> None:
    from urllib.parse import parse_qs, urlsplit

    # Open on this machine, and closed from the network to everyone but the operator — a
    # step stricter than `/api/state`, because this answer names absolute paths on the
    # console's machine (where the adapters are, where the user's settings.json is). A
    # paired phone's viewer token does not open it; the page says so instead of drawing an
    # empty map. One locality rule for the whole handler: `_from_loopback`.
    if not h._from_loopback() and not h._has_token({}):
        h._json(403, {"error": "reading the tool map over the network needs the operator token (X-Reflex-Token); "
                               "a phone's viewer token does not open it, because it names paths on this machine. "
                               "On this machine it needs none"})
        return
    agent = (parse_qs(urlsplit(h.path).query).get("agent") or ["claude-code:mine"])[0][:40] or "claude-code:mine"
    h._json(200, class_map(h, agent))


def api_classes_post(h: "Handler", payload: dict) -> None:
    """Writing the map is the person's: an agent that could put its own tools in a class
    with no circuit, or unmark them as irreversible, would be writing its own boundary."""
    if not h._has_token(payload):
        h._json(403, {"error": "changing what a tool is and whether it needs a confirm is the operator's: "
                               "send the token (see the console's log)"})
        return
    changes = payload.get("tools")
    if not isinstance(changes, list) or len(changes) > 500 or not all(isinstance(c, dict) for c in changes):
        h._json(400, {"error": "send {\"tools\": [{\"tool\": \"send_email\", \"class\": \"message\", "
                               "\"irreversible\": true}, …]}, at most 500 entries"})
        return
    try:
        result = write_overrides(changes)
    except ValueError as e:
        h._json(400, {"error": str(e)})
        return
    except OSError as e:
        h._json(400, {"error": f"could not write the rule files: {e}"})
        return
    if result["written"]:
        print(f"tool classes: {'; '.join(result['changed'])} → {', '.join(result['written'])} "
              f"(a person changed them from the page)", flush=True)
    h._json(200, dict(result, **{"map": class_map(h)}))


def api_write_claude_settings(h: "Handler", payload: dict) -> None:
    """The one file-writing action in this console, and the narrowest one it could be:
    one fixed path, chosen at startup; the operator's token; only from this machine's
    loopback; a backup first; and a refusal, never a guess, if the file is not JSON we
    can read back. `preview` shows the exact diff and writes nothing."""
    if not h._has_token(payload):
        h._json(403, {"error": "writing your settings.json is the operator's: send the token (see the console's log)"})
        return
    if not h.client_address or h.client_address[0] not in ("127.0.0.1", "::1"):
        h._json(403, {"error": "this writes a file on the console's own machine, so it is offered only to a browser "
                               "on that machine; copy the block instead"})
        return
    action = str(payload.get("action", "preview"))
    if action not in ("preview", "write"):
        h._json(400, {"error": "action is 'preview' or 'write'"})
        return
    agent = str(payload.get("agent", "claude-code:mine"))[:40] or "claude-code:mine"
    before: dict | None = None
    if CLAUDE_SETTINGS.exists():
        try:
            raw = CLAUDE_SETTINGS.read_text(encoding="utf-8")
        except OSError as e:
            h._json(400, {"error": f"cannot read {CLAUDE_SETTINGS}: {e}"})
            return
        try:
            before = json.loads(raw) if raw.strip() else {}
        except ValueError as e:
            h._json(400, {"error": f"{CLAUDE_SETTINGS} is not valid JSON ({e}); the console will not touch a file it "
                                   f"cannot read back. Fix or move it, or copy the block in by hand."})
            return
        if not isinstance(before, dict):
            h._json(400, {"error": f"{CLAUDE_SETTINGS} is not a JSON object; the console will not rewrite it"})
            return
    try:
        after, added = merge_claude_settings(before or {}, claude_settings_block(agent))
    except ValueError as e:
        h._json(400, {"error": str(e)})
        return
    diff = settings_diff(before, after)
    out = {"path": str(CLAUDE_SETTINGS), "exists": before is not None, "added": added,
           "already_installed": not added, "diff": diff, "written": False, "backup": None}
    if action == "preview" or not added:
        h._json(200, out)
        return
    backup = None
    if before is not None:
        backup = CLAUDE_SETTINGS.with_name(CLAUDE_SETTINGS.name + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            backup.write_text(CLAUDE_SETTINGS.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError as e:
            h._json(400, {"error": f"refusing to write without a backup first ({e})"})
            return
    try:
        _atomic_write(CLAUDE_SETTINGS, json.dumps(after, indent=2, ensure_ascii=False) + "\n")
    except OSError as e:
        h._json(400, {"error": f"could not write {CLAUDE_SETTINGS}: {e}"})
        return
    print(f"wrote both hooks into {CLAUDE_SETTINGS} (backup {backup}); a person pressed it on the page", flush=True)
    h._json(200, dict(out, written=True, backup=str(backup) if backup else None))


# ======= end W2 block ====================================================================


# ======= W3 block: the task entrance over HTTP (I-6) =====================================
#
# Two person's acts and nothing else: file a job, close a job. There is deliberately no
# endpoint here that runs anything, and none that an agent may call — an agent that could
# file its own task would be writing the account a person reads, and one that could close a
# task could lift the ceiling that task was filed with.

# I-5's fields, accepted and stored verbatim so that a runner (which this workstream does
# not build) files through this same endpoint and inherits the id, without this console
# growing an opinion about what any of them mean.
TASK_PASSTHROUGH = ("runner", "agent", "reply_to", "cwd")


def api_task(h: "Handler", payload: dict) -> None:
    path = h.path.split("?", 1)[0].rstrip("/")
    if not h._has_token(payload):
        self_says = ("filing a job" if path == "/api/task" else "closing a job")
        h._json(403, {"error": f"{self_says} is the person's: send the token (see the console's log). An agent "
                               f"may not file or close its own tasks."})
        return
    if path == "/api/task":
        classes = payload.get("classes")
        if classes is not None and not (isinstance(classes, list) and all(isinstance(c, str) for c in classes)):
            h._json(400, {"error": "classes is a list of tool class names, or leave it out"})
            return
        extra = {k: payload[k] for k in TASK_PASSTHROUGH if payload.get(k) is not None}
        try:
            rec = TASKS.file(payload.get("text", ""), classes, payload.get("max_grants"), extra)
        except ValueError as e:
            h._json(400, {"error": str(e)})
            return
        cap = f" · at most {rec['max_grants']} grant(s)" if rec["max_grants"] else ""
        print(f"task {rec['task_id']} filed by a person: {rec['text'][:80]!r}{cap}", flush=True)
        h._json(200, rec)
        return
    if path.endswith("/close"):
        task_id = path[len("/api/task/"):-len("/close")].strip("/")
        try:
            h._json(200, TASKS.close(task_id, payload.get("note")))
        except KeyError:
            h._json(404, {"error": f"no such task: {task_id}"})
        except ValueError as e:
            h._json(400, {"error": str(e)})
        return
    h._json(404, {"error": "no such path: a task is filed at POST /api/task and closed at "
                           "POST /api/task/<id>/close"})


# ======= end W3 block ====================================================================


class Handler(BaseHTTPRequestHandler):
    server_version = "reflex-console"

    def log_message(self, fmt, *args):
        # The request line goes to the console's log, and a log is not a secret store: a
        # pairing link with `?token=…` in it would sit there in plaintext for anything that
        # can read the file (on this machine, /tmp/console.log is world-readable). So the
        # query string never reaches the log. The pairing link puts the token in the URL
        # *fragment* for the same reason — a fragment is never sent to a server at all.
        import re

        line = re.sub(r"\?[^\s\"]*", "?<redacted>", fmt % args)
        print(f"{self.address_string()} {line}", flush=True)

    def _send(self, code: int, body: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, (json.dumps(obj) + "\n").encode(), "application/json")

    def _body(self) -> dict:
        raw = self.rfile.read(int(self.headers.get("content-length", "0"))) or b"{}"
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("expected a JSON object")
        return payload

    def _local_only(self) -> bool:
        """Only this machine's own page and its own programs may talk to the console.

        Host must be the console's own name (a page on another site that re-points its DNS at
        127.0.0.1 sends its own name here). A browser always sends Origin on a POST, so a POST
        from any other site's page is refused; programs (the adapters, curl) send none. And a
        POST must say application/json, which a page on another site cannot send without a
        preflight this server never answers — no plain-form or text/plain request gets in.

        "Its own name" includes this machine's own LAN addresses when the console is bound
        to every interface (LAN_HOSTS, computed at startup), because that is the name a
        phone that scanned the pairing QR arrives under."""
        host = (self.headers.get("Host") or "").lower()
        allowed_hosts = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}", f"{HOST}:{PORT}"}
        allowed_hosts |= {f"{ip}:{PORT}" for ip in LAN_HOSTS}
        if host not in allowed_hosts:
            self._json(403, {"error": "host not allowed: the console answers only to this machine by its own name"})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin.lower() not in {f"http://{h}" for h in allowed_hosts}:
            self._json(403, {"error": "origin not allowed: another site's page may not use the console"})
            return False
        return True

    def _has_token(self, payload: dict) -> bool:
        """The operator token, from the header or the body. compare_digest, not ==, so a
        wrong guess leaks nothing through timing."""
        import hmac

        if not TOKEN:
            return True  # no token configured (e.g. a test Boundary with no server)
        given = self.headers.get("X-Reflex-Token") or str(payload.get("token", ""))
        return hmac.compare_digest(given, TOKEN)

    # -- who is at the other end of the socket -------------------------------------------
    #
    # Everything above asks *what* is being requested. These two ask where from, because a
    # console bound to 0.0.0.0 for the pairing QR is reachable by everyone on the Wi-Fi,
    # and the endpoints that need no token — GET /api/state, GET /api/manifest, POST
    # /api/request — would then let a stranger tick your circuits and read what your agent
    # is doing. The rule is per connection, not per bind address: on loopback nothing
    # changes, whatever the console is bound to.

    def _from_loopback(self) -> bool:
        import ipaddress

        raw = ((self.client_address[0] if self.client_address else "") or "").split("%")[0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return False  # cannot tell who this is; treat it as far away
        return bool((getattr(ip, "ipv4_mapped", None) or ip).is_loopback)

    def _agent_token(self) -> str:
        return (self.headers.get("X-Reflex-Agent-Token") or "").strip()

    def _reader_identity(self) -> tuple[str | None, str | None]:
        """Who is reading, for the GETs that are open on this machine and closed from the
        network. One of:

        `("local", None)`      a connection from loopback — this machine's own page and programs;
        `("operator", None)`   the operator token, wherever it came from;
        `("viewer", id)`       a paired phone's own token (F1) — the person's page on the Wi-Fi;
        `("agent", name)`      a bound agent's own token (I-3) — that agent's adapter;
        `(None, None)`         nobody the console can name.

        Asked in that order, so a request that carries two credentials is read as the more
        powerful one. The reply a reader gets depends on which of these they are: the
        first three see the whole console, an agent sees its own rows (F2, 2026-09-17).
        A viewer's read is not a heartbeat: the Cardputer's presence is its polling of its
        own frame, and a phone tab left open in a pocket must not hold a dead-man's halt open."""
        if self._from_loopback():
            return "local", None
        if self._has_token({}):
            return "operator", None
        phone = viewer_auth(self.headers.get("X-Reflex-Device-Token") or "")
        if phone:
            return "viewer", phone
        owner = bound_token_owner(self._agent_token())
        if owner:
            return "agent", owner
        return None, None

    def _reader_may_look(self, path: str) -> bool:
        """`GET /api/state`, `/api/manifest` and the task ledger are open on this machine
        and closed from the network: over the LAN they need the operator's token (the phone
        that scanned the QR has it) or an agent token that is actually bound (its own
        adapter). The tasks are the person's own words about their own work, so they are
        behind the same check as the rest of the state and not a step looser."""
        if path not in ("/api/state", "/api/tasks") and not path.startswith(("/api/manifest", "/api/task/")):
            return True
        if self._reader_identity()[0] is not None:
            return True
        self._json(403, {"error": "reading the console over the network needs a token: a paired phone's own "
                                  "(X-Reflex-Device-Token — the pairing link from `c3s pair` carries it), the "
                                  "operator's (X-Reflex-Token), or a bound agent's own. On this machine it needs none."})
        return False

    def do_GET(self) -> None:
        if not self._local_only():
            return
        # One place where the query string comes off. Routes match on the path alone, so
        # `/?token=…` and `/api/state?t=1` reach what they name; /api/manifest still reads
        # its own `?class=` out of self.path.
        path = self.path.split("?", 1)[0]
        if not self._reader_may_look(path):
            return
        if path == "/api/classes":  # W2: the tool→class map (see the W2 block above)
            return api_classes_get(self)
        if path == "/api/tasks":  # W3/I-6: every job, newest first, closed ones included
            return self._json(200, {"tasks": TASKS.listing(limit=20)})
        if path.startswith("/api/task/"):  # W3/I-6: one job and what it has spent
            task_id = path[len("/api/task/"):].strip("/")
            rec = TASKS.view(task_id)
            return self._json(200, rec) if rec else self._json(404, {"error": f"no such task: {task_id}"})
        if path.startswith("/api/device/"):  # W11
            device_get(self, BOUNDARY)
        elif path in ("/", "/index.html"):
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/state":
            who, name = self._reader_identity()
            status = BOUNDARY.status()
            self._json(200, status_for_agent(status, name) if who == "agent" else status)
        elif path.startswith("/api/manifest"):
            self._manifest()
        elif path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._json(404, {"error": "no such path"})

    def _manifest(self) -> None:
        """GET /api/manifest?class=spend — the installed circuit of one class as an
        ERC-8004 boundary manifest (c3s/erc8004.py), with its keccak256 and the
        registration file that would advertise it. Built here, signed and sent nowhere."""
        from urllib.parse import parse_qs, urlsplit

        # The manifest builder lives on a branch of the circuits repository that is not
        # published yet, so a stranger who clones the public one has a console whose page
        # asks for a manifest and gets nothing at all: the ImportError killed this handler
        # thread, with no status line and no message. Answer it instead, and say which
        # repository is missing what (release audit, 2026-09-17).
        try:
            from c3s import erc8004
        except ImportError as e:
            self._json(501, {"error": f"this console's circuits repository has no boundary manifest builder "
                                      f"({e}). `c3s/erc8004.py` is on the circuits repository's `boundary` "
                                      f"branch; a checkout of `main` compiles and proves circuits but cannot "
                                      f"publish a manifest. Everything else on this page works."})
            return

        cls = (parse_qs(urlsplit(self.path).query).get("class") or [DEFAULT_CLASS])[0]
        if cls not in CLASSES:
            self._json(400, {"error": f"no such class: {cls!r}"})
            return
        with BOUNDARY.lock:
            compiled = BOUNDARY.policies[cls]
        if compiled is None or compiled.deny_all or compiled.policy is None:
            why = "denied outright: there is no circuit to publish" if compiled is not None else "no circuit installed"
            self._json(400, {"error": f"{cls}: {why}"})
            return
        manifest = erc8004.build_manifest(compiled.policy)
        try:
            digest, hash_error = erc8004.manifest_hash(manifest), None
        except Exception as e:  # keccak256 needs Foundry's cast
            digest, hash_error = None, str(e)
        uri = "https://<where-you-host>/manifest.json"
        self._json(200, {
            "class": cls,
            "manifest": manifest,
            # The bytes that are hashed and must be hosted as they are: a pretty-printed
            # copy is a different file with a different hash.
            "manifest_canonical": erc8004.canonical_bytes(manifest).decode("utf-8"),
            "keccak256": digest,
            "hash_error": hash_error,
            "registration_file": erc8004.registration_file(
                f"my-agent-{cls}", "An agent whose actions pass through this compiled boundary.", uri, digest or "0x", None),
            "next": "host manifest.json byte for byte, then run scripts/boundary_manifest.py in c3s-reflex for the "
                    "cast send templates; anyone can re-check it with scripts/validate_boundary.py",
        })

    def do_POST(self) -> None:
        if not self._local_only():
            return
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            self._json(415, {"error": "content-type must be application/json"})
            return
        try:
            payload = self._body()
        except Exception as e:
            self._json(400, {"error": f"malformed request: {e}"})
            return
        name = str(payload.get("agent", "anonymous"))[:40] or "anonymous"

        if self.path == "/api/classes":          # W2: what a tool is, and whether a person must confirm it
            return api_classes_post(self, payload)
        if self.path == "/api/hooks/claude-code":  # W2: "write it for me" (one fixed file, backed up first)
            return api_write_claude_settings(self, payload)
        if self.path == "/api/policy":
            if not self._has_token(payload):
                self._json(403, {"error": "installing rules is the operator's: send the token (see the console's log)"})
                return
            try:
                cls = class_from(payload, CLASSES)
                if payload.get("remove") is True:
                    summary = BOUNDARY.remove(cls)
                elif payload.get("deny_all") is True:
                    summary = BOUNDARY.deny_all(cls)
                else:
                    summary = BOUNDARY.install(cls, policy_from(payload))
            except Exception as e:
                self._json(400, {"error": str(e)})
                return
            note = {"at": time.time(), "kind": "policy", "class": cls}
            if summary.get("installed") is False:
                note["rules"] = ["no circuit: not gated"]
            else:
                note.update({"rules": summary["rules"],
                             "circuit": {k: summary["circuit"][k] for k in ("nand", "latch")},
                             "rows": summary["checked"]["rows"],
                             "every_rule_holds": summary["checked"]["every_rule_holds"]})
            TRANSCRIPTS.appendleft(note)
            self._json(200, summary)
        elif self.path.split("?", 1)[0] == "/api/task" or self.path.split("?", 1)[0].startswith("/api/task/"):
            # W3/I-6. Filing a job and closing one are both the person's: a task is how a
            # person reads what their agent did, and an agent that could file or close its
            # own would be writing that account itself. Neither grants anything.
            return api_task(self, payload)
        elif self.path == "/api/device/viewer":
            # F1: mint a phone's own token. A person's act, like opening a pairing window, so
            # the operator token — and the only place that token is needed to pair a phone.
            if not self._has_token(payload):
                self._json(403, {"error": "issuing a phone's viewer token is the operator's: send the token (see the console's log)"})
                return
            self._json(200, viewer_issue(payload.get("name", "phone")))
        elif self.path.startswith("/api/device/"):  # W11: pairing; nothing here writes a bit
            device_post(self, payload, BOUNDARY)
        elif self.path == "/api/tool":
            if self.headers.get("X-Reflex-Device-Token"):  # W11: a key press that came over Wi-Fi
                # A phone's press goes down the device path, which stamps `cardputer-wifi`.
                # If it was a phone, say so before the entry is serialized — the person
                # reading Activity should not think a Cardputer they do not own pressed the
                # key. The dict handed to _json is the transcript's own, so both agree.
                phone = viewer_auth(self.headers.get("X-Reflex-Device-Token") or "")
                if phone:
                    reply = self._json

                    def stamped(code: int, obj: dict) -> None:
                        if code == 200 and obj.get("device") == phone:
                            obj["source"] = "phone"
                        reply(code, obj)

                    self._json = stamped  # type: ignore[method-assign]
                device_tool(self, payload, BOUNDARY)
                return
            # `source` says which channel the write came from (a chat button, the page), so
            # that Activity can tell a Telegram press from a click — the Wi-Fi Cardputer path
            # stamps its own. It is a label on the entry, never a bit: the same ≤ 20-character
            # shape `/api/stop-all` takes, and left off when the caller says nothing.
            source = str(payload.get("source") or "")[:20].strip() or None
            bits = {k: v for k, v in payload.items() if k not in ("agent", "for_reason", "token", "code", "note", "source")}
            # The person's bits need the operator token; the adapter's own (irreversible,
            # failed) do not, because setting either can only make a decision stricter.
            if any(b in PERSON_BITS for b in bits) and not self._has_token(payload):
                self._json(403, {"error": f"{', '.join(b for b in bits if b in PERSON_BITS)} are the person's: "
                                          "send the token (see the console's log). An agent may not write these."})
                return
            bind_to = str(payload.get("for_reason"))[:160] if payload.get("for_reason") else None
            # I-2: the two digits beside the request. Optional from the page and the device,
            # which are already in the person's hands; a chat channel sends them so that
            # approving means having read the same screen the request is on. The code is not
            # a secret, and getting it wrong is refused rather than guessed at.
            if payload.get("code") is not None:
                keys = [b for b in ("confirm", "confirm_b") if bits.get(b)]
                if bind_to is None or not keys:
                    self._json(400, {"error": "a matching code belongs to one call: send for_reason and the "
                                              "confirm it is for, or leave the code out"})
                    return
                want = BOUNDARY.code_for_call(name, bind_to, keys[0])
                # compare_digest as the device path does (cardputer_relay.device_tool): the
                # code is not a secret, but two checks of the same digits should read alike.
                if want is None or not hmac.compare_digest(str(payload["code"]).strip(), want):
                    self._json(403, {"error": "those two digits are not the ones shown beside this call; read the "
                                              "code again on the screen the request is on"})
                    return
            note = payload.get("note")
            if note is not None and not isinstance(note, str):
                self._json(400, {"error": "note must be a string of at most 200 characters"})
                return
            try:
                entry = BOUNDARY.arm(name, bits, bind_to, (note or "").strip()[:200] or None)
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            if source:
                entry["source"] = source  # the same dict the transcript holds, so Activity sees it too
            self._json(200, entry)
        elif self.path in ("/api/stop-all", "/api/resume-all"):
            # The big red button, and the deliberate way back. Both are the person's: an
            # agent that could call resume-all could lift its own block.
            if not self._has_token(payload):
                self._json(403, {"error": "stopping and resuming every agent is the operator's: send the token "
                                          "(see the console's log)"})
                return
            stop = self.path == "/api/stop-all"
            note = payload.get("note")
            source = str(payload.get("source", "page"))[:20] or "page"
            names = BOUNDARY.stop_all(stop, source, (str(note).strip()[:200] or None) if note else None)
            print(f"{'stop-all' if stop else 'resume-all'}: operator stop {'ON — every request from every agent is refused' if stop else 'off'}; "
                  f"blocked={int(stop)} for {len(names)} known agent(s), from {source} (a person pressed it)", flush=True)
            self._json(200, {"stopped": stop, "operator_stop": stop, "agents": names, "count": len(names), "source": source})
        elif self.path == "/api/request":
            # Before anything ticks: a request under a bound name with the wrong token must
            # not spend that agent's confirm, move its cooldown, or count towards its
            # breaker. It is not that agent's request at all.
            given = self._agent_token()
            # Who may *create* a binding, as opposed to use one. Not a request from the
            # network — that is trust in whoever got there first, and over the Wi-Fi that
            # is a stranger squatting the name your agent has not used yet. And not anyone
            # at all when this console requires tokens: there, names are bound by a
            # person's own command (`c3s token bind`), because an attacker who can bind is
            # an attacker who can be the victim (the review's second pass). Nothing is
            # written for a request that may not bind.
            loopback = self._from_loopback()
            try:
                state = check_agent_token(name, given, may_bind=loopback and not REQUIRE_AGENT_TOKEN)
            except BindingsUnreadable as e:
                # Fail closed and say so. Serving on as if nothing were bound would turn
                # one bad write into "every agent's token is gone", quietly.
                print(f"refusing every request: {e}", flush=True)
                self._json(403, {"error": f"the console cannot read its agent-token store ({e}). It will not "
                                          "treat every agent as unbound: fix the file, or remove it to start "
                                          "from nothing bound."})
                return
            # From the network, a name speaks only if it is already bound *and* sends its
            # token: I-3's "an unbound name keeps working" is a compatibility promise to
            # the programs on this machine, not an invitation to the Wi-Fi. Binding is
            # loopback-only too — first use over the LAN would let a stranger claim a name
            # before the real agent ever ran.
            if not loopback and state != "ok":
                state, lan_why = "spoof", (
                    f"{name!r} came from {self.client_address[0] if self.client_address else 'elsewhere'}, not this "
                    "machine: over the network only an agent name that is already bound, sending its own token, "
                    "may make a request")
            else:
                lan_why = None
            if state in ("spoof", "required"):
                asked = payload.get("class")
                why = lan_why or ((f"{name!r} is not a bound agent name, and this console only answers to names a "
                                   "person has bound (REFLEX_REQUIRE_AGENT_TOKEN): `c3s token bind --agent "
                                   f"{name}`") if state == "required" else
                                  f"{name!r} is bound to its own agent token and this is not it")
                TRANSCRIPTS.appendleft({
                    "at": time.time(),
                    "kind": "spoof",
                    "agent": name,
                    "class": asked if asked in TOOL_CLASSES else None,
                    "reason": str(payload.get("reason", ""))[:160],
                    "granted": False,
                    "tick": None,
                    "token": "missing" if not given else "wrong",
                    "why": [f"{why}: refused without asking any circuit"],
                })
                self._json(403, {"error": f"{why}. Send that agent's REFLEX_AGENT_TOKEN in X-Reflex-Agent-Token; "
                                          "a person can drop a binding with `c3s token rotate --agent <name>`."})
                return
            # I-6: which job this call belongs to, if the adapter knows. A call that names
            # no task is an ordinary call — the boundary is per call, not per task — and a
            # call that names one is judged by the same circuit in the same state. The only
            # thing the id can do is stop the call: an id that is closed or was never filed
            # is refused here, before any circuit is asked, so it costs no tick and moves
            # no latch. Nothing about a task can make a verdict more permissive.
            task = str(payload.get("task") or payload.get("task_id") or "")[:40] or None
            try:
                cls = class_from(payload, TOOL_CLASSES)
                if task is not None:
                    TASKS.check(task)
                entry = BOUNDARY.request(name, payload.get("intent", 0), str(payload.get("reason", ""))[:160], cls,
                                         effect_from(payload), task)
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            if task is not None:
                # After the verdict, never before: counting cannot change what it counts.
                TASKS.record(task, cls, bool(entry.get("granted")))
            decisive = entry.pop("_decisive", None) or {}
            if CHAIN is not None and decisive:
                # The second opinion never holds up the decision: wait briefly so a quick
                # node's answer comes back in this response, and otherwise let it land in
                # the transcript entry when the node gets there.
                entry["chain"] = {"pending": True, "deployed": False}
                worker = threading.Thread(target=chain_check, args=(entry, decisive, cls), daemon=True)
                worker.start()
                worker.join(CHAIN_WAIT_S)
            self._json(200, entry)
        else:
            self._json(404, {"error": "no such path"})


if __name__ == "__main__":
    print(f"compiling the default rules (the ones the fly circuit obeys) from {REPO} …", flush=True)
    BOUNDARY = Boundary(FLY_DEFAULT, STATE_FILE)
    print(f"rules are kept in {STATE_FILE}", flush=True)
    # W3/I-6: the filed jobs, beside the rules. Like the rules, a file that will not parse
    # stops the console here rather than starting with an empty ledger.
    TASKS = Tasks(TASKS_FILE)
    _open_tasks = TASKS.listing(only_open=True)
    print(f"tasks are kept in {TASKS_FILE} ({len(_open_tasks)} open)", flush=True)
    for cls, compiled in BOUNDARY.policies.items():
        if compiled is not None and cls != DEFAULT_CLASS:
            print(f"  {cls}: {'denied outright' if compiled.deny_all else '; '.join(compiled.summary['rules'])}", flush=True)
    s = BOUNDARY.policies[DEFAULT_CLASS].summary
    print(f"  {DEFAULT_CLASS}: {'; '.join(s['rules'])}")
    print(f"  {s['circuit']['nand']} NAND + {s['circuit']['latch']} LATCH · {s['checked']['rows']} rows checked · "
          f"every rule holds: {s['checked']['every_rule_holds']}", flush=True)
    print(f"  other classes ({', '.join(c for c in TOOL_CLASSES if c != DEFAULT_CLASS)}, {HALT}): no circuit until one is installed", flush=True)
    CHAIN = None
    if VERIFY_ON_CHAIN:
        try:
            CHAIN = Chain(BSC_RPC)
            print(f"second opinion: {BSC_RPC} — read-only eth_call, nothing deployed, no wallet", flush=True)
        except Exception as e:
            print(f"no second opinion ({e}); decisions are checked locally only", flush=True)
    if os.environ.get("REFLEX_CARDPUTER"):
        from cardputer_relay import Relay

        Relay(BOUNDARY, os.environ["REFLEX_CARDPUTER"], log=lambda m: print(m, flush=True)).start()
    install_device_presence(BOUNDARY)  # W11: a device present over Wi-Fi is a heartbeat too
    TOKEN = operator_token()
    print(f"\noperator token (the person's key to install rules and confirm/block over HTTP):\n"
          f"  {TOKEN}\n"
          f"  kept in {TOKEN_FILE}. The page asks for it once; the Telegram bot reads the file.\n"
          f"  the model's adapters never get it, so an agent cannot write its own confirm — "
          f"unless it can read this file. Run the agent where it cannot.\n", flush=True)
    print(f"boundary console on http://{HOST}:{PORT}  (no wallet, no key, nothing signed)", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
