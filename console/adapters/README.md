# Adapters

Where the boundary sits in front of an existing agent stack. Each adapter is a thin
client of the console's `POST /api/request`; none of them writes `blocked` or
`confirm`, because those must come from a layer the agent cannot reach.

| Adapter | Covers | Size |
| --- | --- | --- |
| `claude_code_hook.py` | Claude Code and the Claude Agent SDK, including built-in `Bash`/`Write`/`Edit` and every MCP tool, via a `PreToolUse` hook | one file, stdlib only |

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

## Not here yet

An MCP proxy (any MCP client, one config line, but blind to built-in tools) and
guardrail shims for the OpenAI Agents SDK, Vercel AI SDK and LangGraph are the next
ones; all are the same HTTP call in a different jacket.
