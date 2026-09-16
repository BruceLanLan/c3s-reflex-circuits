"""The tool layer's own map: which class answers a tool, and what cannot be undone.

Unit tests, no console and no network. What they hold to account:

* a rule file is re-read when it changes, for a process that loaded it once (the MCP
  proxy loads at startup and lives for the session; the hook is a fresh process anyway);
* the console's table agrees with the adapters about *which* rule decides a name;
* a tool name is harvested from an adapter's reason and a hand-written reason is not;
* writing the override files never widens anything silently: turning off an irreversible
  mark a built-in glob makes is refused, not done by dropping the glob;
* "write it for me" merges into a settings.json without touching anything else, is
  idempotent, and refuses a file it cannot read back.
"""

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "adapters"))

import reflex_classes as rc  # noqa: E402
import console  # noqa: E402


# ---- the override files are re-read when they change ------------------------------------

def test_no_file_means_the_builtin_rules_exactly(tmp_path, monkeypatch):
    monkeypatch.setenv(rc.CONFIG_DIR_ENV, str(tmp_path))
    assert rc.load_rules(None) == rc.DEFAULT_RULES
    assert rc.load_irreversible_tools(None) == rc.DEFAULT_IRREVERSIBLE_TOOLS
    assert isinstance(rc.load_rules(None), tuple)  # nothing to watch: no live view needed


def test_a_process_that_loaded_the_rules_once_sees_a_later_edit(tmp_path, monkeypatch):
    monkeypatch.setenv(rc.CONFIG_DIR_ENV, str(tmp_path))
    path = Path(rc.default_class_file())
    path.write_text("echo message\n!default\n")
    rules = rc.load_rules(None)                     # loaded once, as the proxy does
    assert rc.classify("echo", rules) == "message"

    time.sleep(rc._LiveList.STAT_EVERY_S + 0.1)
    path.write_text("echo spend\n!default\n")
    assert rc.classify("echo", rules) == "spend"    # same object, no restart
    assert rc.matching_rule("echo", rules) == ("echo", "spend")

    time.sleep(rc._LiveList.STAT_EVERY_S + 0.1)
    path.unlink()
    assert rc.classify("echo", rules) == "exec"     # gone means the built-ins, not nothing
    assert tuple(rules) == rc.DEFAULT_RULES


def test_an_irreversible_list_that_appears_later_is_picked_up(tmp_path, monkeypatch):
    monkeypatch.setenv(rc.CONFIG_DIR_ENV, str(tmp_path))
    path = Path(rc.default_irreversible_file())
    path.write_text("!default\n")
    patterns = rc.load_irreversible_tools(None)
    assert not rc.is_irreversible_tool("echo", patterns)
    time.sleep(rc._LiveList.STAT_EVERY_S + 0.1)
    path.write_text("echo\n!default\n")
    assert rc.is_irreversible_tool("echo", patterns)
    assert rc.matching_irreversible("echo", patterns) == "echo"


def test_an_unreadable_list_is_not_an_empty_list(tmp_path, monkeypatch):
    """The failure that matters: a file nobody can read must not mean "nothing is
    irreversible" or "everything is exec"."""
    monkeypatch.setenv(rc.CONFIG_DIR_ENV, str(tmp_path))
    Path(rc.default_irreversible_file()).mkdir()  # a directory: open() raises OSError
    Path(rc.default_class_file()).mkdir()
    assert tuple(rc.load_irreversible_tools(None)) == rc.DEFAULT_IRREVERSIBLE_TOOLS
    assert tuple(rc.load_rules(None)) == rc.DEFAULT_RULES


# ---- the table and the adapters must name the same rule ---------------------------------

def test_the_deciding_rule_is_found_in_classifys_own_order():
    """The full tool name is tried against every rule before the bare name is tried
    against any, so a rule on the whole `mcp__…` name wins over one on the bare name."""
    rules = (("read_file", "spend"), ("mcp__*", "files"))
    assert rc.classify("mcp__files__read_file", rules) == "files"
    assert rc.matching_rule_index("mcp__files__read_file", rules) == 1
    assert rc.matching_rule("mcp__files__read_file", rules) == ("mcp__*", "files")
    assert rc.classify("read_file", rules) == "spend"


def test_a_row_says_where_each_answer_came_from(tmp_path, monkeypatch):
    monkeypatch.setenv(rc.CONFIG_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(console, "CLASS_FILE", tmp_path / "tool-classes.txt")
    monkeypatch.setattr(console, "IRREVERSIBLE_TOOLS_FILE", tmp_path / "irreversible-tools.txt")
    console.CLASS_FILE.write_text("read_file spend\n!default\n")
    ov = console.read_overrides()

    mine = console.tool_row("read_file", ov, {"seen": 3})
    assert (mine["class"], mine["source"], mine["class_pattern"]) == ("spend", "override", "read_file")
    assert mine["seen"] == 3 and mine["writable"] is True

    shipped = console.tool_row("send_email", ov, None)
    assert (shipped["class"], shipped["source"]) == ("message", "builtin")
    assert shipped["irreversible"] is True and shipped["irreversible_source"] == "builtin"
    assert shipped["locked_irreversible"] is True  # a built-in glob: not a per-tool choice

    nobodys = console.tool_row("frobnicate", ov, None)
    assert (nobodys["class"], nobodys["source"]) == ("exec", "unseen")
    assert nobodys["irreversible"] is False


def test_a_name_no_rule_file_could_hold_is_shown_but_not_writable(tmp_path, monkeypatch):
    monkeypatch.setattr(console, "CLASS_FILE", tmp_path / "tool-classes.txt")
    monkeypatch.setattr(console, "IRREVERSIBLE_TOOLS_FILE", tmp_path / "irreversible-tools.txt")
    row = console.tool_row("<img src=x onerror=alert(1)>", console.read_overrides(), {"seen": 1})
    assert row["class"] == "exec" and row["writable"] is False
    assert "whitespace" in row["why_not_writable"]
    with pytest.raises(ValueError, match="cannot be written as a rule"):
        console.write_overrides([{"tool": "<img src=x onerror=alert(1)>", "class": "files"}])


# ---- harvesting names from the transcript -----------------------------------------------

@pytest.mark.parametrize("reason, tool", [
    ("[message] send_email [irreversible]: to=lena@studio.example", "send_email"),
    ("[files] trash_email [irreversible]: id=m3", "trash_email"),
    ("[exec] Bash: command=ls -la", "Bash"),
    ("[exec] mcp__files__read_file: path=/tmp/a.txt", "mcp__files__read_file"),
    ("[exec] <img src=x onerror=alert(1)>: hostile=1", "<img src=x onerror=alert(1)>"),
    ("[spend] sign_transaction: to=0x1 value=0", "sign_transaction"),
])
def test_a_tool_name_is_read_out_of_an_adapters_reason(reason, tool):
    m = console.TOOL_IN_REASON.match(reason)
    assert m and m.group("tool").strip() == tool


@pytest.mark.parametrize("reason", [
    "[exec] the one call a person approved",     # a person's words, no tool call
    "x", "", "[wallet] send_email: to=x",        # not a class this console has
    "sign_transaction: to=0x1 value=0",          # the wallet's reason: no class prefix
])
def test_something_that_is_not_a_tool_call_is_not_harvested(reason):
    assert console.TOOL_IN_REASON.match(reason) is None


def test_the_map_lists_what_was_seen_and_what_the_files_name(tmp_path, monkeypatch):
    monkeypatch.setattr(console, "CLASS_FILE", tmp_path / "tool-classes.txt")
    monkeypatch.setattr(console, "IRREVERSIBLE_TOOLS_FILE", tmp_path / "irreversible-tools.txt")
    console.CLASS_FILE.write_text("draft_invoice spend\n!default\n")
    console.TRANSCRIPTS.appendleft({"kind": "request", "agent": "t:1", "class": "message", "at": 1.0,
                                    "reason": "[message] send_email [irreversible]: to=x"})
    try:
        m = console.class_map()
    finally:
        console.TRANSCRIPTS.popleft()
    names = [t["tool"] for t in m["tools"]]
    assert "send_email" in names          # seen
    assert "draft_invoice" in names       # named by the user's file, never seen
    assert m["override"]["rules"] == [["draft_invoice", "spend"]]
    assert m["builtin"]["rules"][0] == ["*transfer*", "spend"]
    assert m["classes"] == list(console.TOOL_CLASSES) and m["default_class"] == "exec"


# ---- writing the override files ---------------------------------------------------------

@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(console, "CLASS_FILE", tmp_path / "tool-classes.txt")
    monkeypatch.setattr(console, "IRREVERSIBLE_TOOLS_FILE", tmp_path / "irreversible-tools.txt")
    return console.CLASS_FILE, console.IRREVERSIBLE_TOOLS_FILE


def test_a_change_is_written_in_the_format_the_adapters_parse(files):
    classes, irreversible = files
    out = console.write_overrides([{"tool": "read_file", "class": "spend", "irreversible": True}])
    assert out["written"] == [str(classes), str(irreversible)]
    assert rc.read_override_rules(str(classes)) == ((("read_file", "spend"),), True)
    assert rc.read_override_irreversible(str(irreversible)) == (("read_file",), True)
    # and the adapters' own loader, reading those files, now answers the new way
    rules = rc.load_rules(str(classes))
    assert rc.classify("read_file", rules) == "spend"
    assert rc.is_irreversible_tool("read_file", rc.load_irreversible_tools(str(irreversible)))


def test_a_class_the_builtin_rules_already_give_needs_no_line_of_your_own(files):
    classes, _ = files
    console.write_overrides([{"tool": "send_email", "class": "files"}])
    assert rc.read_override_rules(str(classes))[0] == (("send_email", "files"),)
    console.write_overrides([{"tool": "send_email", "class": "message"}])  # what send_* says anyway
    assert rc.read_override_rules(str(classes))[0] == ()


def test_turning_off_an_irreversible_mark_a_builtin_glob_makes_is_refused(files):
    _, irreversible = files
    with pytest.raises(ValueError) as e:
        console.write_overrides([{"tool": "send_email", "irreversible": False}])
    assert "send_*" in str(e.value) and "never seen" in str(e.value)
    assert not irreversible.exists()  # nothing was written: no glob was quietly dropped
    assert rc.is_irreversible_tool("send_sms")  # and every other send_* is still marked


def test_an_irreversible_mark_of_your_own_can_be_taken_off_again(files):
    _, irreversible = files
    console.write_overrides([{"tool": "echo", "irreversible": True}])
    assert rc.read_override_irreversible(str(irreversible))[0] == ("echo",)
    console.write_overrides([{"tool": "echo", "irreversible": False}])
    assert rc.read_override_irreversible(str(irreversible))[0] == ()


def test_an_unknown_class_is_refused(files):
    with pytest.raises(ValueError, match="class must be one of"):
        console.write_overrides([{"tool": "echo", "class": "wallet"}])


# ---- "write it for me" ------------------------------------------------------------------

def test_the_hook_block_names_real_files_and_this_agent():
    block = console.claude_settings_block("claude-code:mine")
    assert sorted(block["hooks"]) == ["PostToolUse", "PreToolUse"]
    pre = block["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "REFLEX_AGENT=claude-code:mine" in pre
    assert str(console.CLASS_FILE) in pre and str(console.IRREVERSIBLE_TOOLS_FILE) in pre
    assert Path(pre.split()[-1]).is_file() and pre.split()[-1].endswith("claude_code_hook.py")
    assert Path(pre.split()[-2]).is_file()  # the interpreter, absolute


def test_merging_keeps_everything_else_and_repeats_cleanly():
    existing = {"model": "opus", "hooks": {
        "PreToolUse": [{"matcher": "Grep|Glob", "hooks": [{"type": "command", "command": "other.py"}]}],
        "Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "stop.py"}]}]}}
    block = console.claude_settings_block("claude-code:mine")
    once, added = console.merge_claude_settings(existing, block)
    assert len(added) == 2
    assert once["model"] == "opus" and once["hooks"]["Stop"] == existing["hooks"]["Stop"]
    assert [e["matcher"] for e in once["hooks"]["PreToolUse"]] == ["Grep|Glob", console.HOOK_MATCHER]
    twice, added_again = console.merge_claude_settings(once, block)
    assert added_again == [] and twice == once  # idempotent
    assert existing["hooks"]["PreToolUse"] == [{"matcher": "Grep|Glob",
                                                "hooks": [{"type": "command", "command": "other.py"}]}]


def test_a_second_command_joins_an_existing_matcher_rather_than_a_second_entry():
    block = console.claude_settings_block("claude-code:mine")
    existing = {"hooks": {"PreToolUse": [{"matcher": console.HOOK_MATCHER,
                                          "hooks": [{"type": "command", "command": "someone-elses.py"}]}]}}
    merged, added = console.merge_claude_settings(existing, block)
    entries = merged["hooks"]["PreToolUse"]
    assert len(entries) == 1 and len(entries[0]["hooks"]) == 2
    assert entries[0]["hooks"][0]["command"] == "someone-elses.py"
    assert any("existing matcher" in a for a in added)


@pytest.mark.parametrize("existing", [{"hooks": []}, {"hooks": {"PreToolUse": {"matcher": "*"}}}])
def test_a_settings_shape_the_console_cannot_reason_about_is_refused(existing):
    with pytest.raises(ValueError, match="will not rewrite it"):
        console.merge_claude_settings(existing, console.claude_settings_block("a"))


def test_the_diff_shows_the_lines_that_would_be_added():
    block = console.claude_settings_block("claude-code:mine")
    after, _ = console.merge_claude_settings({"model": "opus"}, block)
    diff = console.settings_diff({"model": "opus"}, after)
    assert diff.startswith("---") and "+++" in diff
    assert '+    "PreToolUse": [' in diff and "claude_code_post_hook.py" in diff
    assert "-" not in diff.split("\n")[3]  # nothing is removed


def test_the_bnbagent_snippet_is_read_from_the_adapters_own_docstring():
    snippet = console.bnbagent_snippet()
    assert "BoundaryWalletProvider(EVMWalletProvider(...)" in snippet
    assert snippet.splitlines()[0] == "from bnbagent.wallets import EVMWalletProvider"
    assert "import" in (ROOT / "adapters" / "bnbagent_boundary.py").read_text()[:400]
