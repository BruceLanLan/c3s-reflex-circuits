# Security

This program sits between an agent and the things it can do, and refuses some of them.
That is worth being exact about, because a boundary that is believed to be stronger than
it is does more harm than no boundary at all. What follows is what it stops, what it does
not, and what the two adversarial reviews actually found.

Short version: **the circuits decide honestly, and the decision is only as good as the
layer that reports what the call is and who is asking.** The tool layer's bits
(`confirm`, `blocked`, `irreversible`, `failed`, `heartbeat`) are what make a rule a
boundary rather than a cost the agent can choose to pay, and keeping those out of the
agent's reach is a property of *how you deploy this*, not of this code. `docs/ISOLATION.md`
is the deployment that actually has that property.

## The threat model in plain words

**Who the adversary is.** A capable agent — a language model with tools — running on your
machine, under your user, with a shell. Not a remote attacker: nothing here is exposed to
the internet, the console binds `127.0.0.1` and refuses any request whose `Host` is not
its own. The agent is not assumed malicious; it is assumed *optimising*, which in practice
looks the same when the shortest path to its goal runs through the guard.

**What we are protecting.** Actions that cannot be taken back: money moved, a message
sent, a file deleted, a command run. Concretely: the person's decision about those, and
the record of it.

**What the person has that the agent does not.** One secret (the operator token) and, if
they want it, one piece of hardware (the Cardputer, whose keypress never travels over
HTTP). Every boundary in this system reduces to those two.

## What the boundary stops

Per channel, because each channel is a different promise:

| channel | what is gated | what it cannot see |
| --- | --- | --- |
| `adapters/claude_code_hook.py` (PreToolUse) | every tool call the matcher names, one tick each; the call is denied if the circuit refuses, and Claude Code's own permission flow applies unchanged when it grants | a tool the matcher does not name; anything the model does without a tool call |
| `adapters/claude_code_post_hook.py` (PostToolUse / Failure) | arms `failed` from the framework's own report of the result | a failure the framework does not report as one |
| `adapters/mcp_proxy.py` | client→server `tools/call` for gated tool names; a refusal is answered as a tool error and never reaches the server | a tool the gate list does not match; a second MCP server the client launches directly |
| `adapters/bnbagent_boundary.py` | every `sign_transaction` / `sign_message` / `sign_typed_data` through that object; refuses wallets that broadcast through their own executor rather than handing them an ungated one | another key, a held reference to the inner wallet, a `twak` CLI, any SDK path that signs elsewhere |
| the person's bits (`POST /api/tool`) | `confirm`, `confirm_b`, `blocked`, `heartbeat` need the operator token; the Cardputer writes them in-process, never over HTTP | — |
| the rules (`POST /api/policy`), the task entrance (`POST /api/task`) | operator token | — |
| the agent's own name (I-3) | once a name is bound to an agent token, a request under that name without it is refused *before any circuit is asked*, so it cannot spend that agent's confirm, move its cooldown or trip its breaker; the attempt is recorded as `kind: "spoof"` | a program that can read the other agent's environment (see below) |

Two things hold all of that up, and both are checked by tests against the live console:
a request under a foreign `Host` or `Origin` is refused (DNS rebinding, another site's
page), and a body that is not `application/json` is refused (no form or `text/plain` post
from a browser without a preflight this server never answers).

## What the boundary does not stop

1. **A route nobody gated.** A tool call that no hook matches, an MCP server the client
   launched without the proxy, a shell the agent got some other way: not ticked, not
   refused, not recorded. The console only ever sees what an adapter shows it. If a class
   of tool has no circuit installed, the decision says so (`class_installed: false`) —
   read it.
2. **A same-uid agent reading the operator token.** This is the big one, and it is
   structural, not a bug: the token is a file in your home directory and a variable in the
   console's process. While writing this, we read the live console's own token out of
   `ps eww <pid>` in one command. Anything that can do that can write `confirm` for
   itself. The per-agent token (I-3) does not fix it either — it is in the agent's
   environment, and a sibling process under the same user can read that too. **The answer
   is not another check in the code; it is `docs/ISOLATION.md`.** The token is also
   *printed at startup*, so wherever the console's output goes is as sensitive as the
   token file: the development command in the workstream document writes it to
   `/tmp/console.log`, which is world-readable.
3. **`POST /api/tool` needs no token for the adapter's own bits** (`irreversible`,
   `failed`), and is not gated by the agent token at all, because setting either can only
   make a decision stricter. The honest consequence: anything local can arm `failed` on a
   *bound* agent's name repeatedly and trip that agent's breaker, or arm `irreversible` so
   its next call needs a confirm. That is denial of service against an agent, it fails
   safe, and it is accepted for v1.0. Closing it means requiring the agent token on
   adapter bits too, which is a change to `docs/API.md` and every adapter — the
   coordinator's call, not this workstream's.
4. **First use is trusted.** An agent name nobody has bound keeps working with no token
   (compatibility, by design in I-3), and `/api/state` needs no token, so any local
   process can list the unbound names and bind one before the real agent starts — after
   which the real agent is the one getting `403`. It is loud rather than silent (the
   refusal is in the transcript, the page shows the name as bound), and recoverable
   (`c3s token rotate --agent <name>`), but it is trust on first use. Bind the name at
   install time, from the same place that starts the agent.
5. **The tool layer's flags are only as honest as the tool layer.** "This call is
   irreversible" is decided by a pattern list in the hook and a name list in the proxy;
   "this failed" is the framework's own report. The circuit proves *"irreversible and no
   confirm ⇒ no grant"*. It cannot prove that your `rm` was recognised. A destructive
   command the list does not name is the adapter's miss, and the adapter says so in its
   own docstring.
6. **`REFLEX_FAIL_OPEN=1` turns the boundary off** when the console is unreachable. It
   exists so that the choice is explicit; the default is closed.
7. **Ticks are not time.** Nothing here binds a rule to a clock. A cooldown of 8 ticks is
   8 requests, and an agent that makes none waits forever without being stopped.
8. **No TLS, no service supervision, no audit log you could take to court.** The transcript
   is 200 entries in memory plus what the page shows. `~/.c3s-circuit-agent/policies.json`
   survives a restart; the transcript does not.
9. **It never performs an action, holds a key, or signs anything.** A grant is a verdict.
   Whoever carries the action out is outside this program, and the on-chain enforcement
   (`ReflexModule`, a Safe module in the circuits repository) is the only place a verdict
   is enforced by the thing holding the funds.

## The recommendation

Run the agent in a container with no route to the console and no sight of
`~/.c3s-circuit-agent`, talking to one endpoint through one narrow path:
**`docker-compose.yml` + `docs/ISOLATION.md`**, which also carries the proofs (writing
`confirm` from inside the container fails even while holding the operator token; the
console is unreachable by address and by name; the configuration directory is not there).

If you will not do that, the next best things, in order: run the agent as a *second OS
user* that cannot read your home directory — and then check where the console's log goes,
because the token is printed at startup and the documented dev command sends that to
`/tmp/console.log`, which on this machine is mode 644 and readable by every user on it
(`ls -l /tmp/console.log` → `-rw-r--r--`; the token is on line 11). A second user only
helps if the log is mode 600 and somewhere that user cannot read; keep `REFLEX_FAIL_OPEN`
unset; give each
adapter its own `REFLEX_AGENT_TOKEN` so a spoofed name is refused and recorded; and put a
physical key in the loop (the Cardputer's `confirm` never travels over HTTP, so no amount
of local access forges it).

## Reporting a problem

This is a local tool on a local machine; there is no service to take down and no user data
to breach. If you find a way for an agent to get a grant it should not have, or to write a
person's bit:

* Open an issue with the smallest reproduction you have — the request or tool call, and
  what you expected the verdict to be. A public issue is fine and preferred: there is
  nothing here to exploit remotely.
* If you would rather not post it, say so in an issue without the details and a
  maintainer will arrange somewhere else.
* Please do include: the console's version (commit), the adapter, and whether the console
  had the class's circuit installed (`/api/state` → `policies`).
* Out of scope, and already written down above: reading the operator token as the same
  user, an ungated tool, a container escape, `REFLEX_FAIL_OPEN=1`.

There is no bounty. Nothing here is deployed, and no wallet, key or seed phrase exists in
this repository or on any machine that runs it.

## Adversarial review history

**2026-09-16 · the pattern check, and why patterns are not the defence.** A read-only
adversarial review was run against the live console with instructions to get a grant it
should not have. Its best finding: the hook's tamper check (which refuses a tool call that
reaches the console's own `host:port` together with `/api/tool` or `/api/policy`) was
beaten by writing the path so that the *shell*, not the hook, assembled it:

```sh
curl 127.0.0.1:8765/api/to''ol -H 'content-type: application/json' -d '{"agent":"x","confirm":1}'
```

`/api/to''ol` is not `/api/tool` to a substring check, and is exactly `/api/tool` by the
time `curl` sees it. The fix was not a longer list. The list was widened to refuse *any*
net tool aimed at the console's own address, endpoint or not — and, the actual defence, the
person's bits were put behind the **operator token**, so the request that got through was
refused by the console itself. The pattern check remains, downgraded in its own comment to
"an early, clear no" for the cheap tries. Keep that story in mind before adding a check
that matches on text: the same review's second finding was that an agent can simply write
its own `irreversible` bit (it can; it only makes its own life harder), and its third was
that the `agent` field is self-reported, so one agent could drive another's circuit —
which is what the per-agent token (I-3) now closes.

**2026-09-16 · the W6 review of per-agent tokens and the isolation recipe.** Findings and
what was done with them are in the workstream report; nothing critical or high was left
unfixed. The review was read-only, ran its proofs against the live console under agent
names prefixed `redteam:`, and installed nothing.

---

## 中文摘要

**威胁模型**：对手是跑在你机器上、你这个用户下、有 shell 的 agent（不是远程攻击者——后台只
绑 `127.0.0.1`，`Host`/`Origin` 不对就拒）。要保护的是不可逆动作上"人的那一次决定"。人手里
比 agent 多的东西只有两样：操作者令牌，和（可选的）Cardputer 那颗按键。

**挡得住**：四个适配器各自门住的调用（钩子的工具调用、MCP 的 `tools/call`、钱包的三个签名
方法）；人的位（`confirm`/`confirm_b`/`blocked`/`heartbeat`）、规则、任务入口都要操作者令牌；
agent 名一旦绑定令牌（I-3），别人用这个名字发请求**在问电路之前**就被拒，花不掉它的 confirm，
并记成 `kind: "spoof"`。

**挡不住**（逐条写在上面）：没被门住的路径；**同一个用户下 agent 能读到操作者令牌**——写这份
文档时我们自己用 `ps eww <pid>` 一条命令就读到了跑着的后台的令牌，这不是代码能补的洞，答案
是 `docs/ISOLATION.md`；`/api/tool` 的适配器位（`irreversible`/`failed`）不要令牌，因此本机
任何东西都能反复给某个 agent 记 `failed` 把它的断路器打跳（只能变严，属拒绝服务，v1.0 接受）；
首次使用是信任的（没人绑过的名字可被抢先绑走，会在流水里吵，可用 `c3s token rotate` 恢复）；
"不可逆"和"失败"这两个标签只有工具层那么诚实；`REFLEX_FAIL_OPEN=1` 会把边界关掉；拍不是时间；
没有 TLS、没有长期审计日志；本程序不执行动作、不持私钥、不签名、不广播。

**建议**：把 agent 放进容器里跑（`docker-compose.yml` + `docs/ISOLATION.md`，含真实证据）。
做不到的话，退一步的顺序是：换第二个系统用户跑 agent → 不要开 `REFLEX_FAIL_OPEN` → 给每个
适配器独立的 `REFLEX_AGENT_TOKEN` → 把物理按键放进回路。

**报告问题**：开 issue 附最小复现（请求/工具调用 + 你期望的裁决）；本机工具、无远程可利用面、
无赏金；上面已写明的限制不算漏洞。

**对抗审查历史**：2026-09-16 第一轮最好的发现是钩子的模式检查被 `127.0.0.1:8765/api/to''ol`
绕过（shell 拼回真路径，子串匹配看不出来）——真正堵住它的是操作者令牌，不是更长的名单；这就
是"模式不是防线"的原型案例。同日 W6 一轮针对 I-3 与隔离配方的只读审查，详见工作流报告。
