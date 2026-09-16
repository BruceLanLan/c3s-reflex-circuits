# C3S Circuit Agent — console

The local back office of C3S Circuit Agent: you write the rules an agent must obey, they
compile to a NAND/LATCH circuit checked on every row and proven over every reachable
state, your own model is plugged in through an adapter, and every call it makes is
granted or refused by that circuit — with a person holding the keys, on this page, on a
Cardputer on the desk, or in a Telegram chat. [中文](README.zh-CN.md)

## Start here

```sh
export C3S_REPO=~/work/c3s-reflex                 # the circuits repository (c3s.policy)
python console.py                                 # http://127.0.0.1:8765
REFLEX_CARDPUTER=1 python console.py              # the same, with a Cardputer on USB as the confirm key
```

1. **Boundaries** — pick a template per class of tool: *no transfers* (spend, refused
   outright), *no deletes without a person* (files), *messages need a person* (message),
   *stop after failures* (exec), *halt + heartbeat* (shared). Compile and install; the
   evidence panel shows the gates, the rows checked and every rule holding.
2. **Connect** — plug your model in:
   * Claude Code: `adapters/claude_code_hook.py` as a PreToolUse hook (plus the
     PostToolUse hook for failures) — covers Bash, Write, Edit and every MCP tool.
   * Any MCP client: `adapters/mcp_proxy.py -- <your MCP server>` — gate the calls that
     change something; reads pass.
   * BNBAgent SDK (BNB Agent Studio): wrap the wallet in
     `adapters/bnbagent_boundary.BoundaryWalletProvider` — nothing is signed unless the
     spend circuit grants.
3. **Approvals** — what waits for a person, in the circuit's own words. A confirm is for
   that call only. The same list is on the Cardputer (menu 3) and in Telegram
   (`bot_telegram.py`, a person's chat listed in `TOOL_LAYER_CHATS`).
4. **Activity** — every decision, the bits the circuit read, and a read-only
   re-evaluation on BNB Smart Chain with nothing deployed.
5. **Publish** — Connect → step 4 builds an ERC-8004 boundary manifest anyone can
   re-check and score (`c3s-reflex/scripts/validate_boundary.py`); the on-chain Safe
   module is yours to deploy (`c3s-reflex/docs/ONCHAIN-SELF-DEPLOY.md`).

A worked run with a real agent and a pretend mailbox is in `examples/README.md`.

Deliberately a **separate** project from the circuits repository, and deliberately
local by default. The circuits repository is a static, auditable thing that cannot
spend or store anything; this one has a process, a port and, if you turn the relays
on, a serial device and a token. Keeping them apart is the point.

## Two channels, because a rule is only as good as who can satisfy it

`request` and `intent` are written by the agent. A rule resting on those alone — a
commitment of *n* consecutive intent ticks — is a **cost the agent can always choose
to pay**. `blocked` and `confirm` must come from a layer the agent cannot write (the
tool runner, a person, a signed feed); rules resting on those are **boundaries**.

Nothing in the software can enforce which caller is which, so the split is at least
visible in the shape of the API: `POST /api/request` carries the agent's own bits and
`POST /api/tool` carries the ones it must not have. A deployment that lets the model
write its own `confirm` has a commitment cost dressed up as a boundary.

## What it does not do

* It holds **no wallet and no private key**, signs nothing and broadcasts nothing. The
  optional chain check is a read-only `eth_call` with nothing deployed.
* It never *performs* an action. A grant here is a verdict; what an allowed action
  would be, and who would carry it out, is outside this program.
* It adds no safety property of its own. Everything it enforces comes from the
  compiled rules and their proofs — see `docs/AGENT.md` in the circuits repository for
  what those do and, more importantly, do not give. Ticks advance only when something
  drives them; nothing here binds a rule to wall-clock time.

## Running it

Needs the circuits repository on disk (for `c3s.policy` and the evaluator):

```sh
export C3S_REPO=~/work/c3s-reflex          # default
python console.py                          # http://127.0.0.1:8765
```

Then either use the page, or:

```sh
# install rules: 8 ticks between grants, 4 consecutive intent ticks, and never while blocked
curl -s localhost:8765/api/policy -H 'content-type: application/json' \
  -d '{"min_gap_ticks": 8, "commit_ticks": 4, "forbid_when_blocked": true,
       "max_grants": 0, "confirm_window_ticks": 0}'

# the agent's own channel: one tick
curl -s localhost:8765/api/request -H 'content-type: application/json' \
  -d '{"agent": "demo", "intent": 1, "reason": "closing in"}'

# the tool layer's channel: bits the agent must not be able to write
curl -s localhost:8765/api/tool -H 'content-type: application/json' \
  -d '{"agent": "demo", "blocked": 1}'

curl -s localhost:8765/api/state
```

`/api/policy` returns what was compiled and what was checked about it: the gate and
latch counts, the number of rows, whether the circuit matched the reference on all of
them, how many states are reachable, and whether every rule held in each.

### Telegram relay (optional)

```sh
export TELEGRAM_TOKEN=...        # never committed; see .env.example
export CONSOLE_URL=http://127.0.0.1:8765
python bot_telegram.py
```

`/ask`, `/intent`, `/rules` and `/state` work in any chat. `/pending`, `/confirm`,
`/block`, `/stop` and `/resume` work only in a chat listed in `TOOL_LAYER_CHATS`, which
is also told when something starts waiting — because a chat the agent can type into is
the agent's own channel, and a boundary whoever is typing can satisfy is not a boundary.

## Layout

```
console.py           the service: one circuit per class, decisions, approvals, manifests
static/index.html    the console page (Overview, Boundaries, Approvals, Activity, Agents, Connect)
adapters/            Claude Code hooks, MCP proxy, BNBAgent SDK wallet, tool classes
cardputer_relay.py   the Cardputer as a confirm key and stop button, over USB serial
bot_telegram.py      Telegram: ask from any chat; approve, block and stop from a person's chat
examples/            a pretend mailbox and a recorded run with a real agent
design/              the approved design canvas the page was built from
docs/                the product plan
```
