# C3S Circuit Agent

**Write down what your AI agent may never do. It compiles to a circuit, the circuit is
proven, and every tool call the agent makes is granted or refused by it.**

Not a prompt. Not a system message the model can be talked out of. A NAND/LATCH circuit,
checked on every row of its input domain and proven to hold every rule in every state it
can reach — then asked, once, before each call your model tries to make.

[中文](README.zh-CN.md) · [Guide](docs/GUIDE.md) · [API](docs/API.md) · [Security](SECURITY.md)

---

## What it looks like when it holds

Install one rule — *an irreversible action needs a person, and one approval covers one
action* — and this is what the console prints back. Not a promise that it works; the
proof, in the reply:

```json
{
  "rules": ["nothing is granted while blocked is high",
            "an irreversible grant needs its own confirm, and spends it"],
  "circuit": { "nand": 18, "latch": 1, "bytes": 130, "depth": 11 },
  "checked": { "rows": 64, "matches_reference": true,
               "reachable_states": 2, "states_possible": 2,
               "rows_proven": 256, "every_rule_holds": true }
}
```

Sixty-four rows is the whole input domain: there is no input this circuit has not been
shown. Two hundred and fifty-six rows proven is that rule checked in every state the
circuit can reach. `every_rule_holds: true` is not a test passing — it is the absence of
a counterexample.

Then your agent tries to delete something, and a person is holding the key:

```
tick 1   files   rm -rf ./build     refused — irreversible, and no unspent confirm
         ↑ a person approves that exact call, on the page, the Cardputer, or Telegram
tick 2   files   rm -rf ./build     GRANTED
tick 3   files   rm -rf ./build     refused — irreversible, and no unspent confirm
```

The third line is the point. The approval was spent by the call a person read and
approved. It did not become a standing permission, and it could not be carried to a
different call — an approval is bound to the exact call it was shown against.

## Install

```sh
git clone <this repository> && cd reflex-console
sh install.sh            # installs the `c3s` command in an isolated environment, then starts it
```

The page is at http://127.0.0.1:8765. Nothing is installed into your system Python, and
nothing runs with `sudo`.

```sh
c3s up --lan       # also reachable from your phone on the same Wi-Fi
c3s pair           # a QR code for the phone
c3s status         # is it running, what is waiting for a person
c3s stop-all       # block every agent, now
c3s down
```

On first run the console prints an **operator token** and keeps it in
`~/.c3s-circuit-agent/operator-token` (mode 600). It is the person's key: installing
rules and writing the person's bits (`confirm`, `confirm_b`, `blocked`, `heartbeat`)
require it. The page asks once and keeps it in that browser. **Your model's adapters
never receive it** — which is the only reason an agent that can reach the console cannot
approve itself.

## Three steps

1. **Boundaries** — pick what each class of tool may do. *Never move money* (spend,
   refused outright). *No deletes without a person* (files). *Messages need a person*
   (message). *Stop after failures* (exec). *Halt and heartbeat* (shared across all
   classes). Install, and the evidence panel shows the gates, the rows checked, and
   every rule holding.
2. **Connect** — plug in the model you already use:
   * **Claude Code** — `adapters/claude_code_hook.py` as a PreToolUse hook (plus the
     PostToolUse hook, so failures reach the circuit). Covers Bash, Write, Edit and
     every MCP tool.
   * **Any MCP client** — `adapters/mcp_proxy.py -- <your MCP server>`. Gate the calls
     that change something; reads pass through untouched.
   * **BNB Agent SDK** — wrap the wallet in
     `adapters/bnbagent_boundary.BoundaryWalletProvider`. Nothing is signed unless the
     spend circuit grants it.
3. **Approvals** — what is waiting for you, in the circuit's own words, with two digits
   next to it. You approve by reading those digits off the same screen the request is
   on, which is what binds your approval to that call and nothing else. The same list
   reaches a Cardputer on your desk and a Telegram chat.

## The one idea worth stealing

A rule is only as strong as **who can satisfy it**.

`request` and `intent` are written by the agent. A rule that rests on those — *four
consecutive ticks of intent before a grant* — is a **cost the agent can always choose to
pay**. `blocked` and `confirm` must come from somewhere the agent cannot write: the tool
runner, a person, a signed feed. Rules resting on those are **boundaries**.

Most agent guardrails are the first kind wearing the second kind's clothes. No software
can prove which caller is which, so this project does the next best thing: the split is
visible in the shape of the API. `POST /api/request` carries the bits the agent owns.
`POST /api/tool` carries the bits it must not have, and needs the person's token. A
deployment that lets the model write its own `confirm` has a cost dressed up as a
boundary, and this README would rather say so than let you find out later.

## The rules you can compile

| Rule | What it enforces | Rests on |
| --- | --- | --- |
| `forbid_when_blocked` | nothing is granted while blocked is high | the person |
| `sticky_block` | one blocked tick halts everything until a confirm lifts it | the person |
| `confirm_per_irreversible` | an irreversible call needs its own approval, and spends it | the person + tool layer |
| `confirm_window_ticks` | a grant needs an approval within the last *n* ticks | the person |
| `two_key` | every grant needs two different people | two people |
| `heartbeat_ticks` | *n* ticks without a heartbeat halt the agent | the tool layer |
| `trip_after_failures` | *n* consecutive failures trip a breaker until a person resets it | the tool layer |
| `trip_after_refusals` | *n* consecutive refusals halt the agent, instead of letting it hammer the boundary | the circuit's own verdict |
| `min_gap_ticks` | at least *n* ticks between grants | nothing the agent can change |
| `max_grants` | at most *n* grants, ever | nothing the agent can change |
| `commit_ticks` | *n* consecutive intent ticks before a grant | **the agent — a cost, not a boundary** |

"Never" is not a big number. A class set to **deny all** compiles to a circuit with no
path to a grant, and the console says so in those words.

## Who holds the keys

* **The page** — six views, in Chinese and English, on your machine by default. Over
  the Wi-Fi it needs the operator token, which the pairing QR carries in the URL
  fragment: the fragment never reaches the server, and the page takes it out of the
  address bar as soon as it has it.
* **A Cardputer on your desk** — the approvals list on a physical device, and the
  physical Enter key writes the approval. Over USB, or over Wi-Fi after a pairing where
  the device shows four digits and you type them. The device only ever polls outward and
  posts a key press; it never listens. Its token is exactly as powerful as the cable:
  it may approve a call that is waiting with the matching code, and it may block — but
  it can never unblock.
* **Telegram** — a chat listed in `TOOL_LAYER_CHATS` is a person's, and only there do
  approvals, blocks and stops work. A chat your agent can type into must never be
  listed, for the reason the whole project is about.

## On chain, and honestly

* Every decision can be **re-evaluated read-only on BNB Smart Chain** — the same netlist
  evaluated by a public node, agreeing or disagreeing with the local verdict. Nothing is
  deployed and no wallet exists to deploy it with.
* Your boundary can be published as an **ERC-8004 boundary manifest** that anyone can
  fetch and re-check, scoring 100 or 0 — there is no partial credit for a boundary.
* Software enforcement ends where your agent finds another route. The structural answer
  is **`ReflexModule`**, a Safe module in the circuits repository
  (`docs/ONCHAIN-SELF-DEPLOY.md`): a transaction that the boundary did not grant does
  not execute. It is yours to deploy. We hold no key and will not deploy it for you.

## What it does not do

* **It performs no action.** A grant is a verdict. What an allowed action does, and who
  carries it out, is outside this program.
* **It holds no wallet and no private key.** It signs nothing and broadcasts nothing.
* **It cannot stop a same-machine agent that finds a route its checks do not name.** The
  hook and the proxy refuse the direct attempts — writing to the person's endpoints,
  editing the rule files or the hook configuration, killing the console — and that is
  pattern matching, which is defence in depth, not the defence. The defence is the
  operator token, and the structural answer is to run your agent where it cannot reach
  the console's port or those files at all: another OS user, a container, a sandbox
  without localhost. `docs/ISOLATION.md` does exactly that, and measures it.
* **"Irreversible" is decided from tool names.** That is the tool layer's promise, not
  the circuit's proof. A tool named `helper` that sends mail is this layer's miss.
* **Ticks are not time.** A cooldown of eight ticks is eight calls, not eight seconds.
  Nothing here binds a rule to the wall clock.
* **It adds no safety property of its own.** Everything it enforces comes from the
  compiled rules and their proofs. `docs/AGENT.md` in the circuits repository says what
  those do and, more usefully, what they do not.

## Where the circuits came from

The circuit family is not invented for this. It began as the **escape reflex arc of a
fruit fly** — a small, complete, real piece of wiring, reconstructed from the MaleCNS
connectome (CC-BY) and rebuilt as NAND gates so that it could be checked exhaustively
rather than argued about. That work lives in the circuits repository, which is a static,
auditable thing that cannot spend or store anything.

This repository is deliberately separate, and deliberately local. It has a process, a
port, and — if you turn the relays on — a serial device and a token. Keeping the two
apart is the point.

## Layout

```
console.py           the service: one circuit per class, decisions, approvals, manifests
static/index.html    the page (Overview, Boundaries, Approvals, Activity, Agents, Connect)
adapters/            Claude Code hooks, MCP proxy, BNB SDK wallet, tool classes
cardputer_relay.py   the Cardputer as a confirm key and a stop button, over USB
bot_telegram.py      Telegram: ask from any chat; approve, block and stop from a person's
c3s_cli/             the `c3s` command, the launchd service, the pairing QR
examples/            a pretend workplace and recorded runs with a real agent
docs/                the guide, the API, isolation, security, the plan
design/              the approved design canvas the page was built from
```

Full walkthrough for both people and programmers: **[docs/GUIDE.md](docs/GUIDE.md)**.
