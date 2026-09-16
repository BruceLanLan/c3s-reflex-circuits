# Reflex console

A duty console for an agent whose actions a verified circuit has to authorise.

A model proposes; the circuit decides. This is the watching end of that arrangement:
a small local service that keeps the circuit's state, records every proposal and
every verdict, and shows them on one page. A Telegram relay is included so the same
two calls can be made from a chat window.

Deliberately a **separate** project from the circuits repository, and deliberately
local by default. The circuits repository is a static, auditable thing that cannot
spend or store anything; this one has a process, a port and, if you turn the relay
on, a token. Keeping them apart is the point.

## What it does not do

* It holds **no wallet and no private key**, signs nothing and broadcasts nothing.
* It never *performs* an action. An authorisation here is a verdict about a proposal;
  what an authorised action would be, and who would carry it out, is outside this
  program.
* It adds no safety property of its own. Everything it enforces comes from the
  circuit's proofs — see `docs/AGENT.md` in the circuits repository for what those do
  and, more importantly, do not give.

## Running it

Needs the circuits repository on disk (for the verified netlist and the encoder):

```sh
export C3S_REPO=~/work/c3s-reflex          # default
python console.py                          # http://127.0.0.1:8765
```

The first start reads the committed core netlist and builds its step tables, which
takes a few seconds and about 64 MB.

Then either use the page, or:

```sh
curl -s localhost:8765/api/propose -H 'content-type: application/json' \
  -d '{"agent": "demo", "l_over_v_ms": 40, "azimuth_deg": -60, "reason": "closing in"}'
```

### Telegram relay (optional)

```sh
export TELEGRAM_TOKEN=...        # never committed; see .env.example
export CONSOLE_URL=http://127.0.0.1:8765
python bot_telegram.py
```

Send `/propose 40 -60 closing in` in the chat and the verdict comes back. The bot
only relays: it has no authority the console does not have, and the console has none
the circuit does not give it.

## Layout

```
console.py         the service: circuit state, proposals, verdicts, transcript
bot_telegram.py    long-polling relay; token from the environment only
static/index.html  the duty page, served by console.py
```
