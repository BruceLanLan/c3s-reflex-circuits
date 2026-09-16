# Running the agent where it cannot reach the boundary

The README admits a limit, and it is the honest one to admit: **an agent that runs as you
can read the operator token.** It is a file in your home directory (`~/.c3s-circuit-agent/
operator-token`, mode 600) or a variable in the console's environment, and on one machine,
under one user, `cat` and `ps eww` are not privileges you can take away. A boundary whose
bits the agent can write is not a boundary — it is a suggestion.

The pattern check in `adapters/claude_code_hook.py` is not the answer either. Today's
adversarial review took ten minutes to write `127.0.0.1:8765/api/to''ol`, which the shell
reassembles into the path the list was looking for. The operator token is what actually
stopped it (SECURITY.md records that finding as the worked example). But the token only
holds while the agent cannot read it.

So this is the structural answer: the agent runs in a container that has **no route to the
console and no sight of its files**, and one narrow path to the one endpoint an agent is
supposed to use. `docker-compose.yml` in the repository root is the whole recipe, with the
reasoning inline; this page is how to run it and how to check that it is true.

```
   ┌──────────── your Mac / Linux box, your user ─────────────────────────────┐
   │                                                                          │
   │   console.py  127.0.0.1:8765        ~/.c3s-circuit-agent/                │
   │   the person's program               operator-token · policies.json      │
   │   (also the USB Cardputer)           agents.json                         │
   │        ▲                                                                 │
   │        │ POST /api/request only, Host fixed, X-Reflex-Token stripped     │
   │   ┌────┴─────────┐     uplink (a normal bridge)                          │
   │   │ reflex-      │                                                       │
   │   │ gateway      │  nginx: one location, one method, one upstream path    │
   │   └────▲─────────┘                                                       │
   │        │ agentnet — internal: true, no default route, no host, no LAN    │
   │   ┌────┴─────────┐                                                       │
   │   │ agent        │  REFLEX_CONSOLE=http://reflex-gateway:8080            │
   │   │ container    │  REFLEX_AGENT_TOKEN=… (environment only)              │
   │   │              │  /work = your project · /opt/reflex/adapters = ro     │
   │   └──────────────┘  no ~/.c3s-circuit-agent, no ~/.claude, no socket     │
   └──────────────────────────────────────────────────────────────────────────┘
```

## Run it

```sh
export REFLEX_AGENT_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
export REFLEX_WORKSPACE=~/work/the-one-project-the-agent-may-touch
export REFLEX_AGENT_NAME=claude-code:container
# Docker Desktop: nothing more to set. colima / Lima: the host is not `host-gateway`.
export REFLEX_HOST_IP=192.168.5.2        # see "which address is the host" below

docker compose up -d
docker compose exec agent bash           # run Claude Code or your MCP client in here
```

**Bind the name, and start the console with `REFLEX_REQUIRE_AGENT_TOKEN=1`.** Two steps,
and both are needed:

```sh
python -c "import console; print(console.token_bind('claude-code:container', '$REFLEX_AGENT_TOKEN'))"
REFLEX_REQUIRE_AGENT_TOKEN=1 python console.py
```

(`c3s token bind --agent <name>` is the same thing from the CLI.)

Without this, the one call the container is allowed to make is a blank cheque on every
agent name *nobody has bound yet* — see "what the container can still do" below. With it,
the console binds nothing over HTTP at all: a request for a name no person has bound is
refused, whatever token it carries, and nothing is written, so the container cannot claim
another name even by asking first. It is off by default because I-3 promises in
`docs/API.md` that an unbound name keeps working; a deployment that has put its agent in a
container has no use for that promise.

The adversarial review is why it is shaped that way. Requiring merely *a* token was not
enough: it sent `X-Reflex-Agent-Token: literally-anything` for a name nobody had bound,
which bound the name on the way through and spent the confirm a person had left for it.
Authenticating a name and creating one are different powers, and only a person holds the
second.

Inside the container, point the hooks at `/opt/reflex/adapters/claude_code_hook.py` and
`claude_code_post_hook.py` exactly as `adapters/README.md` says; `REFLEX_CONSOLE` and
`REFLEX_AGENT_TOKEN` are already in the environment. The first request binds the agent name
to that token (I-3), and from then on the console refuses that name to anything that cannot
send it — including anything running outside the container.

Keep the token in the container's environment and nowhere else. `docker compose` reads it
from your shell or from `.env`, which `.gitignore` already excludes. A token in a file is a
token every other program on the machine can send.

### Which address is the host

`host.docker.internal` is resolved by Docker Desktop itself, and `host-gateway` (the
default here) is right. Under colima or Lima the host's own loopback is reachable at
`192.168.5.2` and `host-gateway` is the VM's bridge, which is not your Mac. One line tells
you which you have:

```sh
$ docker run --rm curlimages/curl -s -o /dev/null -w '%{http_code}\n' --max-time 4 \
    http://192.168.5.2:8765/api/state
200
```

If that prints `200`, set `REFLEX_HOST_IP=192.168.5.2`. If it fails and
`host.docker.internal` works, leave `REFLEX_HOST_IP` unset.

## Why this path, and only this path

The gateway is an nginx with one `location = /api/request`, one method, and
`proxy_pass …/api/request` with the URI spelled out, so nothing can be smuggled through by
re-encoding the path. It fixes `Host: 127.0.0.1:8765` (the console answers only to its own
name), clears `Origin`, and **strips `X-Reflex-Token`** — a person's authority cannot travel
this way even if the container somehow learns the secret. `X-Reflex-Agent-Token` passes
through untouched, because that is the agent's own.

`/api/tool` is deliberately *not* on the path. The console accepts the operator token in the
JSON body as well as in a header, and nginx cannot read a body honestly — a JSON string can
spell `confirm` as `confirm`, and a body filter would be exactly the kind of pattern
check this project keeps proving wrong. A path that carries only `/api/request` needs no
filter: whatever the body says, no person's bit can be written through it.

That has a price, and it is the deployment's to accept:

* the pre-hook cannot arm `irreversible`, so it refuses an irreversible call outright
  rather than ticking it as if it were reversible (see the last proof below) — stricter,
  not weaker, but it does mean an isolated agent cannot do irreversible work at all, even
  with a person's confirm waiting;
* the post-hook cannot report `failed`, so `trip_after_failures` does not trip for an agent
  in a container.

If you want those bits, add a second `location = /api/tool` to the gateway config and read
the third entry in SECURITY.md's "what this does not stop" first.

## Prove it

Every output below is from this machine, 2026-09-16, colima 0.9 / Docker 29.7.1 / compose
5.5.1, console running on the host with `REFLEX_OPERATOR_TOKEN=test-operator-token`. The
`probe` service is the same network and the same (absence of) mounts as the agent, with
`curl` in it; `docker compose --profile proof run --rm probe '<shell>'` runs it.

**Writing a person's bit fails — even holding the operator token.** This is the negative
the whole recipe exists to prove:

```sh
$ curl -s -i -X POST http://reflex-gateway:8080/api/tool \
    -H "content-type: application/json" \
    -H "X-Reflex-Token: test-operator-token" \
    -d '{"agent":"claude-code:container","confirm":1}'
HTTP/1.1 403 Forbidden
Server: nginx/1.27.5
Content-Type: application/json
Content-Length: 201

{"error":"this path carries POST /api/request only: the tool layer (/api/tool), the rules
(/api/policy), the task entrance (/api/task) and the console page are not reachable from
inside the container"}
```

**The console itself is not reachable at all**, by address or by name, from the agent
container (`python3` is what that image has; the `probe` gets `curl exit=7` and `28` for the
same two):

```
('192.168.5.2', 8765)            unreachable: OSError [Errno 101] Network is unreachable
('host.docker.internal', 8765)   unreachable: gaierror [Errno -3] Temporary failure in name resolution
('127.0.0.1', 8765)              unreachable: ConnectionRefusedError [Errno 111] Connection refused
```

**Nothing else on the gateway answers, by any method**, and the path tricks that beat the
hook's pattern list do not beat an exact `location` (the last one is nginx refusing a NUL
in the URI):

```
/api/policy    POST 403  GET 403
/api/task      POST 403  GET 403
/api/state     POST 403  GET 403
/              POST 403  GET 403

/api/request/../api/tool             403
/api/request/..%2f..%2fapi%2ftool    403
/api/to%27%27ol                      403
//api/tool                           403
/api/request%00/api/tool             400
```

**The agent's own endpoint works**, and the answer is the circuit's, not the gateway's:

```sh
$ curl -s -X POST http://reflex-gateway:8080/api/request \
    -H "content-type: application/json" \
    -H "X-Reflex-Agent-Token: $REFLEX_AGENT_TOKEN" \
    -d '{"agent":"claude-code:container","class":"exec","intent":1,"reason":"[exec] ls -la"}'
{"at": 1789563810.12, "kind": "request", "agent": "claude-code:container", "class": "exec",
 "granted": false, "tick": 1, "why": ["commitment: 0 of 4 consecutive intent ticks"], …}
```

**The path cannot carry a person's authority.** Sending the operator token, a foreign
`Origin` and a `Host` of the container's choosing *through* the gateway, and recording what
reaches the upstream (a copy of this same nginx config with a recorder in the console's
place, so the headers can be read):

```
$ curl -X POST http://<gateway>/api/request -H 'X-Reflex-Token: test-operator-token' \
    -H 'X-Reflex-Agent-Token: w6-container-token' -H 'Origin: https://evil.example' \
    -d '{"agent":"redteam-check","class":"exec","intent":1,"reason":"x","token":"test-operator-token"}'
http=200

what arrived upstream:
  path: /api/request
  Host: '127.0.0.1:8765'          <- rewritten, so the console's own check passes
  Origin: None                    <- cleared
  X-Reflex-Token: None            <- stripped: a person's token cannot travel this way
  X-Reflex-Agent-Token: 'w6-container-token'   <- the agent's own, passed through
```

Note the `"token"` field still in the body: header stripping is not body filtering, and
that is precisely why `/api/tool` — the only endpoint that reads a token out of a body — is
not on this path.

**The name belongs to the token, not to the container.** The same name with another token,
from the same place, is refused before any circuit is asked, and the attempt is in the
transcript as `kind: "spoof"`:

```sh
$ curl -s -o /dev/null -w 'http=%{http_code}\n' -X POST http://reflex-gateway:8080/api/request \
    -H "content-type: application/json" -H "X-Reflex-Agent-Token: guessed" \
    -d '{"agent":"claude-code:container","class":"exec","intent":1,"reason":"[exec] spoof"}'
http=403
```

**The configuration directory is not in the container**, and there is no file with the
operator token in it:

```
$ ls -a /root/
.  ..  .bashrc  .profile  .wget-hsts
$ find / -name operator-token -o -name agents.json ; echo "(nothing = nothing found)"
(nothing = nothing found)
```

**The real hook, in the container, through the gateway** — a reversible call gets the
circuit's own words, and an irreversible one is refused because the tool-layer bit cannot
be recorded on this path:

```
$ echo '{"tool_name":"Bash","tool_input":{"command":"ls -la"},"cwd":"/work"}' \
    | python3 /opt/reflex/adapters/claude_code_hook.py
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
 "permissionDecisionReason": "refused by the boundary at tick 3: commitment: 2 of 4 consecutive intent ticks."}}

$ echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /work/x"},"cwd":"/work"}' \
    | python3 /opt/reflex/adapters/claude_code_hook.py
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
 "permissionDecisionReason": "refusing an irreversible call the tool layer would not record:
 the console answered HTTP 403 to `irreversible` for 'claude-code:container'. …"}}
```

## What the container can still do

The adversarial review of this workstream found this, and it is the most important line on
the page. The container's one permitted call carries a JSON body, and the body says which
agent the request is for. The console trusts that name — that is I-3's compatibility
clause: **a name nobody has bound is a name anything can speak for**, and "anything"
includes the container. So by default, from inside the isolation recipe, an agent can:

* tick another (unbound) agent's circuit under its name, moving its cooldown and streak;
* **spend a confirm a person left for another unbound agent**, if it can produce the exact
  `reason` that confirm was bound to;
* bind a name the real agent has not claimed yet and lock it out (`403` for the owner
  afterwards, until someone rotates it).

Two things limit it, and neither is a defence you should rely on: `/api/state` is not
reachable through the gateway, so it cannot *read* what another agent has waiting (it would
have to guess the `reason` string, and a confirm is bound to one); and every one of those
requests is in the transcript under the name it used.

All three are closed by the two steps above — `REFLEX_REQUIRE_AGENT_TOKEN=1` **and** binding
the names with `c3s token bind`. The proofs below are the review's own attacks, re-run
against a console in that mode with an empty store:

```
a fresh console with REFLEX_REQUIRE_AGENT_TOKEN=1, nothing bound:
  unbound name, no token           : http=403 'redteam-victim' is not a bound agent name, and this
                                     console only answers to names a person has bound …
  unbound name, ARBITRARY token    : http=403  (same refusal — this is the one that worked before)
  one token, three names (a)       : http=403
  one token, three names (b)       : http=403
nothing was written:  log policies.json          <- no agents.json: no name was claimed

a person binds one name out of band:
  {'agent': 'bound-name', 'bound': True, 'replaced': False, …}
  bound name, the wrong token      : http=403 'bound-name' is bound to its own agent token and this is not it
  bound name, its own token        : http=200 granted=False tick=1

and the confirm a person left for an unbound name survives the attack:
  armed confirm for redteam-victim : [1]
  attacker tries to spend it       : http=403
  armed confirm after the attempt  : [1]
```

What remains, honestly: a person who binds nothing and runs with the switch off is where
this section started; the switch without binding refuses every agent, including the real
one; and a program that can read the container's environment still has the container's own
token (limitation 2 of `SECURITY.md`, which is what the container is for).

## What this does not do

* **The agent still needs the network for its model.** This recipe gives the agent *no*
  egress at all, which is right for a local runner and wrong for anything that calls an
  API. Adding a second, non-internal network gives it egress — and, on both Docker Desktop
  and colima, the same gateway address that carries that egress is the one that fronts the
  host. If you need egress, put an HTTP proxy container on the internal network that
  allows only your model's host, and keep `agentnet` internal. **Not verified here; no
  configuration for it is shipped.**
* **The container is not a sandbox against the kernel.** `cap_drop: ALL`,
  `no-new-privileges` and a read-only adapters mount are here, but a container escape is a
  container escape. If you are defending against that, use a VM.
* **It does not protect the workspace from the agent.** Whatever you mount at `/work` is
  the agent's to change, subject to the circuits. Mount one project, not your home
  directory.
* **The gateway is trusted.** It is on both networks by definition. Nobody has a shell in
  it, it runs one nginx with one location, and it publishes no port — but it is the piece
  whose compromise would matter.

---

## 中文摘要

README 承认的那条限制（同一台机器、同一个用户下，agent 能读到操作者令牌）不是靠模式匹配
补得上的——今天的对抗审查用 `127.0.0.1:8765/api/to''ol` 十分钟就绕过了钩子里的名单。结构性
的答案就是这份配方：

* agent 跑在容器里，唯一的网络 `agentnet` 是 `internal: true`：**没有默认路由，到不了宿主
  机的 127.0.0.1:8765，也到不了局域网**；`~/.c3s-circuit-agent` 不挂载，令牌、规则、
  `agents.json` 都不是它能读的文件。
* 它和后台之间只有一条路：`reflex-gateway`（nginx）只接 `POST /api/request`，
  upstream 路径写死，`Host` 固定，**把 `X-Reflex-Token` 头剥掉**——就算容器里的东西学到了
  操作者令牌，这条路也带不了人的权限。`X-Reflex-Agent-Token`（I-3，agent 自己的令牌）原样
  透传。
* 为什么不放 `/api/tool`：后台允许令牌写在 JSON 体里，而 nginx 读不懂 body（`confirm` 可以
  写成 `confirm`），做 body 模式匹配正是本项目一再证明不可靠的东西。代价写在上面：
  容器里的钩子记不了 `irreversible`，于是不可逆调用直接被拒（更严，不是更松）；`failed`
  也报不上去。
* 上面每一段输出都是本机 2026-09-16 真跑出来的：带着操作者令牌写 `confirm` 仍然 403；
  按地址和按名字都连不上后台；换个令牌冒充同一个 agent 名 403 并进流水 `kind: "spoof"`。
* **对抗审查在这个配方上找到的高危**：那唯一一条允许的请求里，agent 名字是 body 说的，
  而"没人绑过的名字任何人都能用"是 I-3 写在 `docs/API.md` 里的兼容承诺——于是容器里的 agent
  默认可以顶着别的**未绑定** agent 的名字发请求、花掉人给它留的 confirm（需要猜中那条
  `reason`）、甚至抢先把名字绑走把真身锁在外面。修法是**两步**：`c3s token bind` 先把名字绑好，
  后台再带 `REFLEX_REQUIRE_AGENT_TOKEN=1` 启动——这个模式下后台**不再通过 HTTP 绑定任何名字**，
  没被人绑过的名字带什么令牌都拒。只要求"带个令牌"是不够的：审查第二轮随便发一个
  `X-Reflex-Agent-Token: literally-anything` 就顺手把受害者的名字绑了、confirm 也花掉了。
  "证明"一节里贴的就是用它原样的 PoC 重跑的输出。
* 还没解决的：容器**完全没有出网**，需要访问模型 API 的 agent 得自己加一层只放行模型域名的
  代理（未验证、未提供配置）；容器不是对抗内核逃逸的沙箱；挂进 `/work` 的东西 agent 仍然
  能改。
