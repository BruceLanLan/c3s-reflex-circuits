# C3S Circuit Agent

**Write down what your AI agent may never do. The rules compile to a circuit, the circuit
is proven, and every tool call your model makes is granted or refused by it.**

Not a prompt, and not a system message the model can be argued out of. A NAND/LATCH
circuit, evaluated on every row of its input domain and proven to hold every rule in every
state it can reach — then asked, once, before each call. Your own model plugs in through an
adapter; a person holds the confirm and blocked keys; every decision can be re-evaluated
read-only on BNB Smart Chain. The console holds no wallet and no key and performs no
action. A grant is a verdict, not an execution.

[中文](README.zh-CN.md) · [Guide](docs/GUIDE.md) · [API](docs/API.md) · [Security](SECURITY.md) · [Channels](docs/CHANNELS.md)

---

## What it looks like when it holds

Install one rule on the `files` class — *an irreversible action needs a person, and one
approval covers one action* — and this is what comes back. Not a promise; the check itself
(abridged: the full reply also carries the netlist bytes, the input names and the
per-rule violation counts, all zero):

```json
{
  "rules": ["nothing is granted while blocked is high",
            "an irreversible grant needs its own confirm, and spends it"],
  "circuit": { "nand": 18, "latch": 1, "bytes": 130, "depth": 11 },
  "checked": { "rows": 64, "matches_reference": true,
               "reachable_states": 2, "states_possible": 2,
               "rows_proven": 256, "every_rule_holds": true },
  "compiled_in_ms": 1
}
```

Sixty-four rows is the whole input domain: there is no input this circuit has not been
shown. Two hundred and fifty-six rows proven is every rule checked in every state the
circuit can reach. `every_rule_holds: true` is not a test passing — it is the absence of a
counterexample.

Then your agent tries to delete something, and a person is holding the key. This is a
recorded run against a console, not a story:

```
tick 1   files   rm -rf ./build     refused — irreversible, and no unspent confirm
         → Approvals shows that call beside a matching code, 71. A person approves it.
tick 2   files   rm -rf build/      refused — irreversible, and no unspent confirm
                                    confirm_waiting_for: ["… rm -rf ./build"]
tick 3   files   rm -rf ./build     GRANTED
tick 4   files   rm -rf ./build     refused — irreversible, and no unspent confirm
```

Tick 2 is the model rewording its retry; the approval did not follow it, and the reply
named the call it was waiting for. Tick 4 is the point: the approval was spent by the one
call a person read and approved. It did not become a standing permission. The refusal at
tick 2 also carried `"chain": {"chain_id": 56, "agrees": true, "deployed": false}` — a
public BNB Smart Chain node evaluated the same netlist and reached the same verdict.

## The one idea worth taking away

A rule is only as strong as **who can satisfy it**.

`request` and `intent` are written by the agent. A rule that rests on them — *four
consecutive ticks of intent before a grant* — is a **cost the agent can always choose to
pay**. `blocked` and `confirm` must come from somewhere the agent cannot write: a person,
the tool runner, a device on the desk. Rules resting on those are **boundaries**.

Most agent guardrails are the first kind wearing the second kind's clothes. No software
can prove which caller is which, so this project does the next best thing: the split is
visible in the shape of the API. `POST /api/request` carries the bits the agent owns.
`POST /api/tool` carries the bits it must not have, and the person's bits there need the
operator token. A deployment that lets the model write its own `confirm` has a cost
dressed up as a boundary, and this README would rather say so now than let you find out
later.

## Install

Two repositories: the circuits (the compiler and its proofs) and this console. Python 3.10
or newer as `python3`; `uv` or `pipx` if you have one, a plain venv otherwise.

```sh
git clone https://github.com/BruceLanLan/c3s-reflex-circuits ~/work/c3s-reflex
git clone <the console repository> reflex-console && cd reflex-console   # not yet published; clone it from wherever you were handed it
sh install.sh
```

`install.sh` puts the `c3s` command in an isolated environment (nothing goes into your
system Python, nothing runs with `sudo`), remembers this checkout, and starts the console.
The page is at http://127.0.0.1:8765. If the circuits are somewhere other than
`~/work/c3s-reflex`, set `C3S_REPO` first; if `c3s` is not found afterwards, `~/.local/bin`
is not on your `PATH`. Fetching the two Python packages is the only network access the
install needs; the console itself talks to the network only for the on-chain second
opinion, and works without it.

On first start the console prints an **operator token** and keeps it in
`~/.c3s-circuit-agent/operator-token` (mode 600). It is the person's key: installing rules
and writing the person's bits (`confirm`, `confirm_b`, `blocked`, `heartbeat`) require it.
The page asks for it once and keeps it in that browser. **Your model's adapters never
receive it** — which is the only reason an agent that can reach the console cannot approve
itself. An agent that can read that file can; run the agent where it cannot
(`docs/ISOLATION.md`).

```sh
c3s demo           # a pretend mailbox, calendar and folder, one chore, every call decided — nothing leaves the machine
c3s status         # is it running, what is waiting for a person
c3s stop-all       # refuse everything, now, including agents it has never met; --resume lifts it
c3s up --lan       # also reachable from a phone on the same Wi-Fi — read "Who holds the keys" first
c3s pair           # the QR code for the phone
c3s down
```

## Three steps

1. **Boundaries** — pick a template for each class of tool, or set the rules yourself.
   *Never move money* (`spend`, denied outright). *No deletes without a person* (`files`).
   *Messages need a person* (`message`). *Stop after three failures* (`exec`). *Halt and
   heartbeat* (`halt`, shared by every class). Compile, read the evidence panel — gates,
   rows checked, every rule holding — then install.
2. **Connect** — plug in the model you already use:
   * **Claude Code** — `adapters/claude_code_hook.py` as a PreToolUse hook, plus the
     PostToolUse hook so failures reach the circuit. Covers Bash, Write, Edit and every MCP
     tool.
   * **Any MCP client** — `adapters/mcp_proxy.py -- <your MCP server>` in front of one
     server. Name the calls to gate with `--gate` and reads pass through untouched; with no
     `--gate` every call is a tick.
   * **BNB Agent SDK** — wrap the wallet in `adapters.bnbagent_boundary.BoundaryWalletProvider`.
     Nothing is signed unless the `spend` circuit grants it.
3. **Approvals** — what is waiting for you, in the circuit's own words, with two digits
   beside it. The button on the page carries those digits; from a chat you type them. The
   digits bind your approval to that call and nothing else — the same list reaches a
   Cardputer on your desk and a Telegram chat.

## The rules you can compile

The circuits library (`c3s/policy.py`) knows eleven rules. This console installs ten of
them; the last row says why.

| Rule | What it enforces | Rests on |
| --- | --- | --- |
| `forbid_when_blocked` | nothing is granted while `blocked` is high | the person |
| `sticky_block` | one blocked tick halts everything until a confirm lifts it | the person |
| `confirm_per_irreversible` | an irreversible call needs its own approval, and spends it | the person, and the tool layer that says which calls are irreversible |
| `confirm_window_ticks` | a grant needs an approval within the last *n* ticks | the person |
| `two_key` | every grant needs both `confirm` and `confirm_b`, and spends both | two keys — both written with the person's token; the console cannot prove they are in two hands |
| `heartbeat_ticks` | *n* ticks without a heartbeat halt the agent | the person's token, or the presence of a device on the desk |
| `trip_after_failures` | *n* consecutive failures trip a breaker until a person resets it | the tool layer, reporting the framework's own result |
| `min_gap_ticks` | at least *n* ticks between grants | nothing the agent can change |
| `max_grants` | at most *n* grants, ever | nothing the agent can change |
| `commit_ticks` | *n* consecutive intent ticks before a grant | **the agent — a cost, not a boundary** |
| `trip_after_refusals` | *n* refusals in a row halt the agent instead of letting it hammer the boundary | the circuit's own verdict — **compiled in the library, deliberately not installable here**: its safety half is proven, its reset is broken, and no confirm lifts it (found 2026-09-17) |

"Never" is not a big number: `max_grants=0` means *unlimited*. A class set to **deny all**
compiles to no circuit at all — there is no path to a grant — and the console says so in
those words.

## Who holds the keys

* **The page** — seven views (Overview, Boundaries, Approvals, Tasks, Activity, Agents,
  Connect), in Chinese and English, on your machine only by default. `c3s up --lan` is
  opt-in. `c3s pair` mints the phone **its own viewer token** and puts that in the QR, in
  the URL fragment, which never reaches a server; the page keeps it and clears the address
  bar. **The operator token never leaves this machine**, which is the point: a viewer
  token reads the state and approves a waiting call whose code matches, and that is all —
  it cannot install rules, unblock, stop, resume, or write a heartbeat, and the page
  disables those controls and says why. A phone's read is not a heartbeat either. This is
  new tonight and it replaces a real hole: the page used to send the operator token on
  every two-second poll, in cleartext, for as long as it was open. `c3s pair --forget <id>`
  revokes a phone; a phone paired before tonight gets a 403 and has to be paired again.
  There is still no TLS, so a viewer token is readable on that network — it just is not
  the key to everything.
* **A Cardputer on your desk** — the approvals list on a physical screen, and the physical
  Enter key writes the approval. Over USB the relay runs inside the console and its
  presence is the heartbeat. Over Wi-Fi the device is paired by four digits it shows and
  you type, it only ever polls outward and posts a key press, and its token is **strictly
  weaker than the cable**: it may approve a call that is waiting, with that call's code,
  and it may block — it cannot unblock, cannot forge a heartbeat (its polling *is* the
  heartbeat), and cannot stop or resume everything.
* **Telegram** — a chat listed in `TOOL_LAYER_CHATS` is a person's, and only there do
  `/confirm`, `/block`, `/stop` and the buttons work; a press carries the exact call and
  its digits. A chat your agent can type into must never be listed, for the reason the
  whole project is about. `docs/CHANNELS.md` says what each channel proves and what it
  cannot — none of them proves a person is on the other end; each proves the write came
  from a layer the agent has no path into.

## On chain, and honestly

* Every decision can be **re-evaluated read-only on BNB Smart Chain**: the same netlist,
  evaluated by a public node with `eth_call` and a state override, agreeing or disagreeing
  with the local verdict in the reply. Nothing is deployed and there is no wallet to
  deploy with.
* Your boundary can be published as an **ERC-8004 boundary manifest** that anyone can
  fetch and re-check, scoring 100 or 0 — there is no partial credit for a boundary. The
  builder (`c3s/erc8004.py`) lives on the circuits repository's `boundary` branch, which
  is not published yet; a console running against the published `main` has no manifest.
* Software enforcement ends where your agent finds another route. The structural answer
  is **`ReflexModule`**, a Safe module on that same unpublished branch: a transaction the
  boundary did not grant does not execute. It is yours to deploy; we hold no key and will
  not deploy it for you.

## What it does not do

* **It performs no action.** A grant is a verdict. What an allowed action does, and who
  carries it out, is outside this program.
* **It holds no wallet and no private key.** It signs nothing and broadcasts nothing.
* **It cannot stop a same-machine agent that finds a route its checks do not name.** The
  hook and the proxy refuse the direct attempts — writing to the person's endpoints,
  editing the rule files or the hook configuration, killing the console — and that is
  pattern matching, keyed to the default port and literal spellings: defence in depth, not
  the defence. The defence is the operator token, and the token holds only while the agent
  cannot read it. Run your agent where it cannot reach the console's port or its files:
  another OS user, a container, a sandbox without localhost. `docs/ISOLATION.md` does
  exactly that, and shows how to check it.
* **It cannot prove a person is on the other end of any channel.** The page proves the
  presser holds the token file; the Cardputer proves someone is beside that device; a
  chat proves the message came from an allowlisted conversation. Each is a boundary
  exactly as long as the agent has no path into that layer.
* **The stop button stops everything, including what it has not met.** It is a latch the
  console checks before it consults any circuit, so a brand-new agent name and a class
  with no circuit installed are both refused while it is down — measured, because it used
  to be a list of names an agent walked out of by renaming itself. The cost is the other
  side of the same coin: a session that starts while the stop is down is dead until a
  person resumes, and resuming is deliberately a second act.
* **"Irreversible" is decided from tool names and arguments.** That is the tool layer's
  promise, not the circuit's proof. A tool named `helper` that sends mail is this layer's
  miss.
* **Ticks are not time.** A cooldown of eight ticks is eight calls, not eight seconds.
  Nothing here binds a rule to the wall clock.
* **It adds no safety property of its own.** Everything it enforces comes from the
  compiled rules and their proofs. `docs/AGENT.md` in the circuits repository says what
  those prove and, more usefully, what they do not.

## Where the circuits came from

The circuit family was not invented for this. It began as the **escape reflex arc of a
fruit fly** — a small, complete, real piece of wiring, reconstructed from the MaleCNS
connectome (CC-BY) and rebuilt as NAND gates so that it could be checked exhaustively
rather than argued about: 173 gates, all 8,388,608 rows. That work lives in the circuits
repository, which is a static, auditable thing that cannot spend or store anything.

This repository is deliberately separate, and deliberately local. It has a process, a
port, and — if you turn the relays on — a serial device and a token. Keeping the two
apart is the point.

## Layout

```
console.py           the service: one circuit per class, decisions, approvals, tasks, manifests
static/index.html    the page (Overview, Boundaries, Approvals, Tasks, Activity, Agents, Connect)
adapters/            Claude Code hooks, MCP proxy, BNB SDK wallet, tool classes, WeChat relay
cardputer_relay.py   the Cardputer as a confirm key and a stop button, over USB and Wi-Fi
bot_telegram.py      Telegram: ask from any chat; approve, block and stop from a person's
c3s_cli/             the `c3s` command, the launchd service, the pairing QR
menubar/             the macOS menu bar app (optional extra)
examples/            the pretend workplace behind `c3s demo`, and a recorded run with a real agent
docker-compose.yml   the agent in a container with no route to the console (docs/ISOLATION.md)
install.sh           the one-command install
docs/                the guide, the API, install, channels, isolation, the red-team report
design/              the approved design canvas the page was built from
```

The full walkthrough, for the person who installs it and the person who changes it:
**[docs/GUIDE.md](docs/GUIDE.md)**.
