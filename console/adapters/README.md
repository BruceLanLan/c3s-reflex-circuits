# Adapters

Where the boundary sits in front of an existing agent stack. Each adapter is a thin
client of the console's `POST /api/request`; none of them writes `blocked` or
`confirm`, because those must come from a layer the agent cannot reach.

| Adapter | Covers | Size |
| --- | --- | --- |
| `claude_code_hook.py` | Claude Code and the Claude Agent SDK, including built-in `Bash`/`Write`/`Edit` and every MCP tool, via a `PreToolUse` hook | one file, stdlib only |
| `mcp_proxy.py` | Any MCP client over stdio (Claude Desktop, Claude Code, Cursor, ...): wraps one MCP server and gates its `tools/call` requests; blind to the client's built-in tools | one file, stdlib only |

## Claude Code hook

One matched tool call is one tick. A grant leaves Claude Code's own permission
prompts exactly as they were; a refusal denies the call with the rule's words:

```
refused by the boundary at tick 3: commitment: 2 of 4 consecutive intent ticks.
refused by the boundary at tick 6: cooldown: 7 ticks left of 8.
refused by the boundary at tick 9: blocked is high. The tool layer has blocked this agent; only it can lift that.
```

Add to `~/.claude/settings.json` (all projects) or `.claude/settings.json` (one
project). Put it in a file the agent cannot edit: an agent that can rewrite its own
hook configuration has no boundary, only a habit.

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Write|Edit|mcp__.*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/reflex-console/adapters/claude_code_hook.py",
            "timeout": 20
          }
        ]
      }
    ]
  }
}
```

Try it without installing anything:

```sh
echo '{"session_id":"demo","tool_name":"Bash","tool_input":{"command":"ls","description":"list files"}}' \
  | python3 adapters/claude_code_hook.py; echo "exit $?"
```

If the console is down the hook refuses (exit 2) and says why; set
`REFLEX_FAIL_OPEN=1` to run unchecked instead — that is a choice to have no boundary
while it is set, and the hook will not make it for you.

## MCP proxy

The client launches the proxy; the proxy launches the real server and relays
newline-delimited JSON-RPC between them. One gated `tools/call` is one tick. Everything
else — `initialize`, `notifications/*`, `tools/list`, every response, and any line it
cannot parse — passes through byte for byte in both directions; the server's stderr is
inherited, so its logs still reach the client.

Claude Code, one line — the first `--` ends `claude mcp add`'s own options, the
second ends the proxy's:

```sh
claude mcp add filesystem -- python3 /absolute/path/to/reflex-console/adapters/mcp_proxy.py \
  --gate 'write_file' --gate 'move_file' --gate 'delete_*' \
  -- npx -y @modelcontextprotocol/server-filesystem ~/projects
```

Claude Desktop, Cursor and everything else that reads an `mcpServers` block: replace the
server's `command`/`args` with the proxy's, and put the original command after `--`:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "python3",
      "args": [
        "/absolute/path/to/reflex-console/adapters/mcp_proxy.py",
        "--agent", "mcp:filesystem",
        "--gate-all",
        "--",
        "npx", "-y", "@modelcontextprotocol/server-filesystem", "/Users/me/projects"
      ]
    }
  }
}
```

What is gated: `--gate NAME` (repeatable, `fnmatch` globs allowed), `--gate-file PATH`
(one name or glob per line, `#` comments), or `--gate-all`, which is also the default
when no gate option is given. Names are the server's own bare tool names as they appear
in `params.name` — Claude Code's `mcp__server__tool` prefix does not exist inside the
protocol, so a pattern like `mcp__*` matches nothing. The agent name is `--agent`,
else `REFLEX_AGENT`, else `mcp:<downstream basename>` (launchers such as `npx`, `uvx`
and `python3` are skipped when guessing); name it yourself for anything you will look
for on the console page. `REFLEX_CONSOLE` (or `--console`) points at the console.

What the model sees on a refusal is a normal tool result with `isError: true` and the
rule's words as its text, not a protocol error — the MCP tools spec files execution
failures under `isError` precisely so the model can read the reason and stop:

```json
{"jsonrpc": "2.0", "id": 7, "result": {"isError": true, "content": [{"type": "text",
  "text": "refused by the boundary at tick 6: cooldown: 7 ticks left of 8."}]}}
```

The hints are the hook's: a refusal for want of confirmation says a person can arm one
from the console page; a `blocked` refusal says only the tool layer can lift it. If the
console is unreachable, the gated call is refused with a text that says so; ungated
traffic is unaffected. `REFLEX_FAIL_OPEN=1` forwards gated calls instead, and is the
same choice to have no boundary while it is set.

Try it against the fake server used by the tests:

```sh
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"delete_everything","arguments":{}}}' \
               '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"delete_everything","arguments":{}}}' \
  | python3 adapters/mcp_proxy.py --agent demo -- python3 tests/fake_mcp_server.py
```

The honest limit: the proxy sees only the MCP calls that are routed through it. A
client's built-in tools (Claude Code's `Bash`, `Write`, `Edit`; a desktop app's own file
access) never pass this way, and neither does any MCP server the client was given
directly rather than through the proxy. Use the hook for Claude Code's built-ins; for
other clients there is no boundary on built-ins from here. As with the hook, the config
that names the proxy must live where the agent cannot rewrite it.

## Not here yet

Guardrail shims for the OpenAI Agents SDK, Vercel AI SDK and LangGraph are the next
ones; all are the same HTTP call in a different jacket.
