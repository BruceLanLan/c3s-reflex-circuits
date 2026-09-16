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
| `POST /api/stop-all`, `POST /api/resume-all` | **a person** | operator token |
| `GET/POST /api/classes` | read: anything local · write: **a person** | operator token to write |
| `GET /api/state`, `GET /api/manifest` | anything local | none |

**Pairing a phone**: the token travels in the URL **fragment**, never the query string —
`http://<LAN-IP>:8765/#approvals&token=<operator token>`. A fragment is not sent to the
server, so it cannot land in the console's log, a proxy's log, or a `Referer`. The page
reads it once, stores it, and clears it from the address bar. (Superseded the earlier
`?token=` form after W1 found that the console logs full request lines.)

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

## Stop everything · `POST /api/stop-all` / `POST /api/resume-all`

Operator token. Optional `note` (≤ 200) and `source` (≤ 20, default `"page"`); returns
`{"stopped": true|false, "agents": [...], "count": n, "source": "..."}`. Writes `blocked` for
every agent the console knows, one tool entry each, carrying the `source` so Activity can say
where the stop came from. It **latches**: resuming is a second, deliberate act — the page makes
the person type the word, and a refusal of `"blocked is high"` offers no confirm, because no
confirm can lift a block.

## Tool classes · `GET /api/classes` / `POST /api/classes`

`GET` returns the tool→class map the adapters use: the built-in rules, the override files, and
every tool name this console has actually seen (harvested from `reason` prefixes), each with
its class and whether it counts as irreversible. `POST` (operator token) writes the override
files the adapters already parse; the adapters are short-lived processes and pick the change up
on their next call. The class decides **which circuit answers**; `irreversible` decides
**whether a person must confirm**. Both are the tool layer's promise, not something the circuit
proves.

## Errors

| status | when |
| --- | --- |
| 400 | malformed body, unknown class, unknown runner, bad `effect` shape |
| 403 | missing/wrong operator token, wrong `code`, wrong agent token, bad `Host`/`Origin` |
| 404 | no such path or task |
| 415 | `Content-Type` is not `application/json` |

Every error body is `{"error": "<one sentence a person can act on>"}`.

## What is deliberately not here

No endpoint performs an action, holds a key, signs, or broadcasts. `GET /api/manifest`
builds an ERC-8004 boundary manifest and prints commands; running them is the user's.
