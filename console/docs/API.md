# C3S Circuit Agent — console API (frozen for v1.0)

Every workstream builds against this. Changing anything here means telling the coordinator
first: five workstreams read it. Endpoints are on `http://127.0.0.1:8765` by default.

## Who may call what

| Endpoint | Caller | Auth |
| --- | --- | --- |
| `POST /api/request` | the agent's adapters (hook, MCP proxy, wallet) | none, plus the agent's own token once bound (I-3) |
| `POST /api/tool` — `irreversible`, `failed` | the adapters (the tool runner describing the call) | none |
| `POST /api/tool` — `confirm`, `confirm_b`, `blocked`, `heartbeat` | **a person** (page, Cardputer, chat) | operator token |
| `POST /api/policy`, `POST /api/task` | **a person** | operator token |
| `POST /api/task/<id>/close` | **a person** | operator token |
| `GET /api/tasks`, `GET /api/task/<id>` | read: **loopback** free · over the LAN: **a person** or a bound agent | as `GET /api/state` |
| `POST /api/stop-all`, `POST /api/resume-all` | **a person** | operator token |
| `GET/POST /api/classes` | read: **loopback** free · over the LAN and to write: **a person** | operator token except from loopback |
| `POST /api/hooks/claude-code` | **a person**, loopback only | operator token |
| `GET /api/device/frame`, `POST /api/tool` from a device | a **paired Cardputer** or a **paired phone** over the LAN | device token, and only as below |
| `POST /api/device/pair` | a device claiming an open window | **none by design** — see below |
| `POST /api/device/pair/begin\|confirm`, `POST /api/device/forget`, `POST /api/device/viewer` | **a person** | operator token |
| `GET /api/state`, `GET /api/manifest` | anything local · over the LAN: a paired phone's viewer token, the operator, or a bound agent's own token (which sees a **narrowed** state — below) | none from loopback |

**Reading over the network** (2026-09-17, F2). `GET /api/state` answers according to who is
reading. From loopback and with the operator token it is the whole console. With a bound
agent's own token (`X-Reflex-Agent-Token`, I-3) it is **that agent's rows only**: its entry in
`agents`, its items in `pending`, and the transcript entries about it or about the rules
(`request`/`tool`/`spoof` for that name, every `policy` entry, and each `stop` entry with the
list of names removed). Other agents' calls, notes and armed bits are not in the reply, so an
agent's token cannot learn the `reason` another agent's confirm is bound to — the string
`docs/ISOLATION.md` relies on it not having. The rules and what was checked about them, the
open `tasks` (the person's jobs, which an adapter reads there — I-6) and the count of bound
names stay. A narrowed reply carries `"view": {"scope": "agent", "agent": "<name>"}`; the full
one carries no `view`. `GET /api/tasks`, `/api/task/<id>` and `/api/manifest` are unchanged.

**Pairing a phone** (revised 2026-09-17, F1): `c3s pair` asks the running console for a
**viewer token** — the phone's own, minted by `POST /api/device/viewer` (operator token) into
`devices.json` with `kind: "phone"`, hash only — and puts *that* in the URL **fragment**, never
the query string: `http://<LAN-IP>:8765/#approvals&viewer=<viewer token>`. A fragment is not
sent to the server, so it cannot land in the console's log, a proxy's log, or a `Referer`. The
page reads it once, stores it, and clears it from the address bar. The page then sends the
viewer token (`X-Reflex-Device-Token`) on its reads and **never sends the operator token on a
read**: it used to attach it to the two-second `/api/state` poll, which over the Wi-Fi put the
person's one secret on the network in cleartext for as long as the page was open. A viewer
token has a paired Cardputer's rights (confirm a waiting call with its code, block; never
unblock, heartbeat, rules, stop, resume, tasks) plus `GET /api/state`, `/api/manifest`,
`/api/tasks`; the page disables the controls it cannot use and says why. A viewer's read is
**not** a heartbeat — only a device's own frame poll is. `c3s pair --forget <device id>` (or
`POST /api/device/forget`) revokes a phone. (The `&token=` fragment form is still read by the
page for a person who types it deliberately; `c3s pair` no longer produces it.)

Operator token: header `X-Reflex-Token` (or `"token"` in the body), compared with
`hmac.compare_digest`. Generated once into `~/.c3s-circuit-agent/operator-token` (mode 600),
printed at startup, overridable with `REFLEX_OPERATOR_TOKEN`. Every request must also pass
the local-only check: `Host` is one of the console's own names, and a browser `Origin`, when
present, is the console's own.

---

## I-1 · `effect` — what the approval card shows

`POST /api/request` gains an optional `effect` object. The adapters fill in what they know;
the console stores it on the decision entry verbatim and never interprets it.

```json
{
  "agent": "claude-code:mine", "class": "message", "intent": 1,
  "reason": "[message] send_email [irreversible]: to=lena@studio.example",
  "effect": {
    "kind": "send",
    "target": "lena@studio.example",
    "summary": "Reply to Lena: 15:00 on Thursday works",
    "amount": null, "asset": null, "path": null,
    "reversible": false,
    "details": {"subject": "Re: Thursday review moved to 15:00"}
  }
}
```

| field | type | meaning |
| --- | --- | --- |
| `kind` | `read` `send` `delete` `move` `write` `pay` `exec` | one word for the shape of the effect |
| `target` | string, ≤ 120 | who or what receives it: an address, a channel, a contract |
| `summary` | string, ≤ 200 | one line a person can approve on, in the person's language |
| `amount`, `asset` | string / null | for `pay`: the amount and the token, as decoded, never wei |
| `path` | string / null | for file effects, absolute |
| `reversible` | bool / null | the adapter's honest assessment; `null` = unknown |
| `details` | object, ≤ 1 KB | anything else worth showing, rendered as key/value |

Rules:
- **`reason` is still the identity of the call** and the key a confirm binds to (I-2). `effect`
  is display only; changing `effect` must never change a verdict.
- Every consumer degrades: no `effect` → show `reason`, exactly as today.
- The console escapes everything from `effect` before rendering. It is agent-supplied text.

## I-2 · `pending[]` and the two-digit code

The console computes what waits for a person — one place, not three. `GET /api/state` gains:

```json
"pending": [
  {"agent": "claude-code:mine", "class": "files", "tick": 7,
   "reason": "[files] trash_email [irreversible]: id=m3",
   "effect": {"kind": "delete", "...": "..."},
   "why": ["irreversible, and no unspent confirm"],
   "bit": "confirm", "code": "47", "at": 1758000000.0,
   "armed": false, "waiting_on_time": false}
]
```

- One entry per (agent, class): the newest refused request whose refusal a person can
  resolve by writing a bit (`irreversible`, `two keys`, `no confirmation`, `breaker tripped`,
  `halted`). Refusals only time can fix (cooldown, commitment, budget) get
  `"waiting_on_time": true` and no `code`.
- `code` is two digits, generated when the entry first appears, stable while it waits, bound
  to `(agent, reason, bit)`. It is **not a secret** — it is there so that approving requires
  seeing the same screen the request is on (CISA number matching), not merely being in an
  allowlist.
- `armed` is true once a confirm bound to that exact call is waiting.

Writing a person's bit:

```json
POST /api/tool
{"agent": "claude-code:mine", "confirm": 1,
 "for_reason": "[files] trash_email [irreversible]: id=m3",
 "code": "47", "note": "yes, that one is a scam"}
```

- `for_reason` binds the confirm to that one call (already shipped).
- `code` is **required from a chat channel** (Telegram, WeChat) and optional from the page
  and the Cardputer, which are already in the person's hands. A wrong code → `403`.
- `note` (≤ 200 chars) is stored on the tool entry and shown in Activity; it is a rule
  candidate, not a rule.
- `source` (≤ 20 chars, optional) says which channel wrote the bit — `"telegram"`,
  `"page"` — and is stamped on the tool entry as `source`, the same shape `/api/stop-all`
  takes and the same field the Wi-Fi Cardputer path fills with `cardputer-wifi`. It is a
  label, never a bit: it is excluded from the bits before they are armed, and when the
  caller sends none the entry carries no `source` (an adapter's `irreversible` write is not
  "the page"). Added 2026-09-17 so a chat channel can say where a write came from.

## I-3 · the agent's own token

`POST /api/request` accepts header `X-Reflex-Agent-Token`. The console keeps
`~/.c3s-circuit-agent/agents.json`: `{"claude-code:mine": {"token_sha256": "…", "bound_at": …}}`.

- First request from an unknown agent name **with** a token binds that name to it.
- Afterwards that name **requires** the same token: a mismatch is `403` and the attempt is
  recorded in the transcript as `kind: "spoof"`.
- An agent name that was never bound keeps working without a token (compatibility), and the
  page marks it "unbound". Adapters read the token from `REFLEX_AGENT_TOKEN` only — never
  from a file, so a token meant for one agent cannot be picked up by another.
- Rotation: `c3s token rotate --agent <name>` drops the binding; the next request rebinds.

This closes agent-name spoofing (one agent driving another's circuit or spending its
confirm), which the adversarial review filed as its third finding.

## I-4 · `trip_after_refusals` — shipped

`c3s/policy.py` (commit `4cb7476`): `trip_after_refusals: int`, R refused requests in a row
halt the agent until a `confirm` resets it. 51 NAND + 3 LATCH over 128 rows alone. Appears in
`settings`, `describe()`, `reasons()` (`"halted after N refusals in a row; a confirm resets it"`)
and `properties()` (`violations.refusal_breaker`) like every other rule. Consumers only need to
know it is a tenth rule; no API shape changed.

## I-5 · `/api/task` — the task entrance

> **Read I-6 first if you are building against this.** The runner described below — the
> part that starts `claude -p` as a subprocess — is **not built**. The request shape is
> real and stable; the thing behind it is a ledger. I-6 says exactly what ships.

```json
POST /api/task        (operator token)
{"text": "把收件箱里骗 seed phrase 的邮件扔垃圾箱，给 Lena 回信说周四 15:00 可以",
 "runner": "claude-code",
 "agent": "tg:12345",
 "reply_to": {"channel": "telegram", "chat": "12345"},
 "cwd": "/Users/me/work/mailbox-demo"}
→ 202 {"task_id": "t_9f2a", "agent": "tg:12345", "status": "running"}

GET /api/task/t_9f2a  → {"task_id": "t_9f2a", "status": "running|done|error|cancelled",
                         "started_at": …, "ended_at": null, "summary": null,
                         "events": [{"at": …, "kind": "text|tool|decision|error", "…": "…"}]}
GET /api/tasks        → the last 20, newest first
POST /api/task/t_9f2a/cancel   (operator token)
```

- `runner` is `"claude-code"` for v1.0 (`claude -p … --output-format stream-json`), run as a
  subprocess with `REFLEX_AGENT=<agent>`, both hooks wired to this console, and the user's MCP
  configuration. Other runners later; an unknown runner is `400`.
- The console never edits the user's real `~/.claude/settings.json`: the runner writes a
  temporary settings file for that subprocess only.
- `events` are append-only and bounded (≤ 500 per task); `kind: "decision"` events carry the
  same `tick`/`granted`/`why` as a transcript entry so a channel can render a decision without
  a second call.
- A task's tool calls are ordinary requests against that agent's circuits — **the task
  entrance grants nothing on its own**, and a task blocked on an approval simply waits.
- `reply_to` is stored for the channel to consume; the console itself never sends to a chat.

## I-6 · tasks as a ledger — shipped

I-5 above describes the task entrance with a runner behind it. The runner is **not built**
(W3, 2026-09-16); what is built is the half that makes a task mean something whether or not
a subprocess ever exists: **a job a person files, and the thread their agent's calls are read
along.** A runner, when one lands, files through this same endpoint and inherits the id, so
nothing in I-5's request shape changed — `runner`, `agent`, `reply_to` and `cwd` are accepted
and stored verbatim, interpreted by nothing.

```json
POST /api/task          (operator token)
{"text": "把收件箱里骗 seed phrase 的邮件扔垃圾箱，给 Lena 回信说周四 15:00 可以",
 "classes": ["files", "message"],      // what the person expects it to need — a note, not a gate
 "max_grants": 4}                      // the ceiling they are willing to give it
→ 200 {"task_id": "t_9f2a", "status": "open", "text": "…", "expects": ["files", "message"],
       "max_grants": 4, "filed_at": …, "granted": 0, "refused": 0, "by_class": {}}

GET  /api/tasks               → {"tasks": [ … the last 20, newest first, closed ones included ]}
GET  /api/task/t_9f2a         → that one task and what it has spent
POST /api/task/t_9f2a/close   (operator token) {"note": "the reply went out"}
```

`GET /api/state` gains `"tasks": [ … the open ones … ]`. That is deliberately where an
agent's adapter reads its work: the same list the page draws, on a call that already exists,
and reading it grants nothing.

**Naming a task on a call.** `POST /api/request` takes an optional `task` (or `task_id`).
The id is written onto the transcript entry and onto that call's `pending[]` item, so the
page, the Cardputer and a chat channel can all say *this approval is part of that job*
without a second lookup. A call that names **no** task works exactly as before: the boundary
is per call, not per task — the task is how a person reads what happened.

**A task is not an authority.** This is the property the endpoint exists to keep, so it is
stated as a constraint rather than a hope:

- The id never reaches a circuit. It is not an input, it moves no latch, and it is written
  onto the entry *after* the verdict exists.
- `tests/test_console_task.py::test_a_task_id_cannot_loosen_a_verdict` runs the same call
  twice against the same circuit in the same state — once naming an open task, once naming
  none — over a grant, an irreversible call with and without its confirm, a blocked agent, a
  cooldown and a denied class, and requires the same `granted`, the same `why`, the same
  `inputs` **and the same circuit state afterwards**. The HTTP path, where the counting
  happens, gets the same test.
- The only thing a task can do to a call is **stop** it: an id that is closed, or that was
  never filed, is refused with `400` and a sentence saying which, **before any circuit is
  asked** — so it costs no tick and moves no latch. Stricter, never looser.

**`max_grants` is a brake, not a rule.** The Nth granted call naming the task closes the
task (`closed_by: "limit"`), so the call after it meets the ordinary closed-task refusal —
one path and one sentence, rather than a second refusal mechanism competing with the
circuits. Refused calls cost nothing against it. A real ceiling on an agent is `max_grants`
on the class, under Boundaries, where a circuit proves it.

**`classes` is a declaration.** It is shown beside what the job actually spent, and it gates
nothing: refusing a class the person did not list would be a boundary nothing proved, and one
a person would soon trust as if it were. The class's own circuit is the boundary.

Tasks persist in `~/.c3s-circuit-agent/tasks.json` (`REFLEX_TASKS_FILE`, mode 600), beside
the rules and with the same refusal: **a file that will not parse stops the console** rather
than starting with an empty ledger, because a closed task is a refusal a person put there and
starting empty would turn it back into "no such task". Open tasks are never dropped; closed
ones keep the last 200.

**What it deliberately does not do.** It runs nothing — no subprocess, no model, no tool — and
holds no key. It never sends to a chat: `reply_to` is stored for a channel to consume. It
cannot make any call more likely to be granted. It is not a queue the agent may write to: an
agent can read the open tasks but may neither file nor close one, because a task is the account
a person reads and an agent that could write it could also lift the ceiling it was filed with.

## Stop everything · `POST /api/stop-all` / `POST /api/resume-all`

Operator token. Optional `note` (≤ 200) and `source` (≤ 20, default `"page"`); returns
`{"stopped": true|false, "operator_stop": true|false, "agents": [...], "count": n, "source": "..."}`.

**`stop-all` sets the operator-stop latch** (2026-09-17). While it is on, **every**
`POST /api/request` is refused before any circuit is asked — from every agent, the names known
at press time and any first seen afterwards (a renamed agent included), in every class, whether
that class has a circuit installed, denied, or none at all. The refusal entry is
`granted: false`, `blocked: 1`, `halt: null`, `operator_stop: true`, with
`why: ["stopped: a person pressed stop-all, and every request is refused until a person resumes"]`;
`class_installed` still says whether the class was ever gated. Nothing behind the wall moves:
no circuit ticks, no cooldown advances, nothing armed is consumed, so whoever resumes finds the
circuits exactly where they were. The latch is **persisted** with the rules (`operator_stop` in
`policies.json`) and comes back after a restart or a crash; a file whose `operator_stop` is not
an object with a boolean `on` stops the console rather than being guessed at.

It also writes `blocked` for every agent the console knows, one tool entry each carrying the
`source`, so the per-name level on the switches panel agrees with the latch — but that write is
no longer what makes the stop hold. Before the latch, it was all there was, and the red-team
pass renamed an agent past it (granted on tick 4 while the page showed "stopped"); the UX pass
found an ungated class walking through one (`blocked` was read through no circuit). Both are
closed by deciding the stop before the circuits. The cost, stated plainly: **a session started
during a stop is refused until someone resumes** — the correct trade for a panic button, and it
will surprise whoever starts a fresh agent mid-incident.

Each press writes one transcript entry `{"kind": "stop", "stopped": true|false, "source", "agents":
[...], "note"?}` before the per-name ones, so the press is on the record even when no agent is
known. `GET /api/state` gains `"operator_stop": {"on": bool, "since": ts|null, "source": str|null}`;
the page reads that rather than counting blocked names. A `pending[]` item for a request refused
by the latch has `bit: null`, `waiting_on_time: true` and no code: nothing a person can write
lifts it but the resume.

`resume-all` clears the latch, lowers every known agent's `blocked`, and resets every agent's
**shared halt** state. A `halt` circuit installed with `sticky_block` waits for "a confirm" to
lift; the person's resume is that act, and arming a confirm bit here could approve an
irreversible call waiting in a class, so the halt's state is reset instead — the same thing
installing a halt circuit does to every agent (wrap-up, 2026-09-16).

Per-name `blocked` (`POST /api/tool {"blocked": 1}`) stays for surgical blocks of one agent, and
`REFLEX_REQUIRE_AGENT_TOKEN=1` with `c3s token bind` stays for deployments that nail names down.

**`pending[]` and the shared halt** (same date): when the shared halt refuses, the class's circuit
also reports `blocked is high` because the halt made it so. `pending[]` used to read the class's
words and list the call as waiting with no `bit` and no `code` — a dead end. It now reads the
halt's own words: a halt that a confirm lifts (`halted until a confirm lifts it`, `halted: N
ticks without a heartbeat`) carries `bit: "confirm"` and a code; a halt holding because the
person's own `blocked` is high still carries none, since only lifting the block helps.

## Tool classes · `GET /api/classes` / `POST /api/classes`

`GET` returns the tool→class map the adapters use: the built-in rules, the override files, and
every tool name this console has actually seen (harvested from `reason` prefixes), each with
its class and whether it counts as irreversible. `POST` (operator token) writes the override
files the adapters already parse; the adapters are short-lived processes and pick the change up
on their next call. Two deviations from this document's first draft, both endorsed (W2, 2026-09-16):

- **`GET` is free from loopback but needs the operator token over the LAN**, because the
  response names local absolute paths (the interpreter, the rule files, the user's
  `settings.json`). Stricter than the table's first draft on purpose.
- **The override files have default locations**: with no `REFLEX_CLASS_FILE` /
  `REFLEX_IRREVERSIBLE_TOOLS_FILE` set, the adapters now read
  `$REFLEX_CONFIG_DIR/tool-classes.txt` and `.../irreversible-tools.txt` when those exist, so
  an edit made on the page takes effect without anyone exporting a variable. The cost is
  explicit: **any deployment with those files present has adapters that follow them.** W4 must
  know they exist before checking `send_*` / `trash_*` classification.
- `load_rules` returns a live view of the file (stat-throttled), not a snapshot, because the
  MCP proxy is a long-lived process and loaded its rules once at start-up — the hook, being a
  fresh process per call, was already correct. A missing file falls back to the built-ins,
  never to an empty list.

**`POST /api/hooks/claude-code`** (operator token, loopback only) previews and writes the two
hook blocks into one path fixed at start-up (`REFLEX_CLAUDE_SETTINGS`, default
`~/.claude/settings.json`): it backs the file up first, replaces atomically, is idempotent, and
**refuses a file it cannot parse** rather than touching it. Writing a user's file is a separate
concern from describing tool classes, so it is a separate endpoint.

The class decides **which circuit answers**; `irreversible` decides
**whether a person must confirm**. Both are the tool layer's promise, not something the circuit
proves.

## The Cardputer over Wi-Fi · device tokens

Header `X-Reflex-Device-Token`. `~/.c3s-circuit-agent/devices.json` (mode 600) keeps only the
sha256. Pairing: a person opens a window (`POST /api/device/pair/begin`), the device claims it
(`POST /api/device/pair`, unauthenticated **on purpose** — whoever claims it must then show the
person four digits, and a mismatch is an alarm, not a retry: the window is cancelled), the
person types the digits the **device** shows (`POST /api/device/pair/confirm`). The console never
displays those digits.

A **phone** is a device too (`POST /api/device/viewer`, operator token → `{"device_id":
"phone:…", "token": …}` once; record `kind: "phone"`). It has every right in the left column
below plus `GET /api/state`, `/api/manifest` and `/api/tasks`; a record without `kind` is a
Cardputer and is refused those. A phone's press is stamped `source: "phone"`.

A Cardputer's device token is **exactly as powerful as the cable and no more**:

| over the network a device may | and may not |
| --- | --- |
| `GET /api/device/frame` — the same ~400 B frame the cable carries | read `/api/state` (a Cardputer; a phone may) |
| `confirm` / `confirm_b` for an agent **in `pending[]`**, with that entry's `code` and `for_reason` | write them for any other agent or call |
| `blocked: 1` — block, for any agent the console knows, **no code needed** | `blocked: 0` — lifting a block happens on the console or over the cable |
| — | `heartbeat` — the heartbeat *is* the device's polling, which the console computes itself; a beat a network write could forge would not be a dead man's switch |
| — | `irreversible`, `failed`, `/api/policy`, `/api/task`, `/api/stop-all` |

Why blocking needs no code (coordinator's call, 2026-09-16): an emergency stop must never be
gated on reading a digit off a screen, and blocking is the fail-safe direction — it only ever
makes the boundary stricter. Lifting is the direction that needs a person at the console.

Why a device endpoint instead of polling `/api/state`: the frame is the cable's own 400 bytes
(state is tens of kilobytes), the device needs no JSON parser (the same `onHostLine` parser
reads it), and **a token-carrying poll is the heartbeat** — none of which `/api/state` can give.

## Errors

| status | when |
| --- | --- |
| 400 | malformed body, unknown class, unknown runner, bad `effect` shape, a task that is closed or was never filed, a task already closed |
| 403 | missing/wrong operator token, wrong `code`, wrong agent token, bad `Host`/`Origin` |
| 404 | no such path or task |
| 415 | `Content-Type` is not `application/json` |

Every error body is `{"error": "<one sentence a person can act on>"}`.

## What is deliberately not here

No endpoint performs an action, holds a key, signs, or broadcasts. `GET /api/manifest`
builds an ERC-8004 boundary manifest and prints commands; running them is the user's.
