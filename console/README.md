# Boundary console

A duty console for an agent whose actions a compiled, verified rule-set has to allow.

You write the rules — a cooldown, a commitment, a forbidding flag, a budget, a
confirmation window. They are compiled to a NAND/LATCH circuit, checked against a
plain-Python statement of the same rules on **every row** of the circuit's domain, and
then proven: a search from reset visits every state the circuit can reach under every
input, with monitors that count independently of its latches. Only then does the
circuit become the thing that answers requests. This is the watching end of that
arrangement: a small local service that keeps one circuit state per agent, records
every request and every verdict with the reason in the rules' own words, and shows it
on one page. A Telegram relay is included.

Deliberately a **separate** project from the circuits repository, and deliberately
local by default. The circuits repository is a static, auditable thing that cannot
spend or store anything; this one has a process, a port and, if you turn the relay
on, a token. Keeping them apart is the point.

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

`/ask`, `/intent`, `/rules` and `/state` work in any chat. `/block` and `/confirm` are
refused unless that chat's id is listed in `TOOL_LAYER_CHATS`, because a chat is the
agent's own channel and a boundary whoever is typing can satisfy is not a boundary.

## Layout

```
console.py         the service: compiles rules, keeps circuit state, answers requests
bot_telegram.py    long-polling relay; token from the environment only
static/index.html  the duty page, served by console.py
```
