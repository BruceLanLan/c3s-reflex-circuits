"""`c3s token bind` and `c3s token list` — the command-line path that makes
REFLEX_REQUIRE_AGENT_TOKEN=1 usable at all.

In strict mode the console binds nothing over HTTP (an attacker who can bind can be the
victim — the adversarial review's second pass), so a name has to be bound by a person's own
command on this machine. Until these verbs existed there was no such command, and the
switch the docs point at as the fix for "rename past the stop button" could not be thrown.

Everything here runs in-process against a temporary `agents.json`; no console is started.
The CLI imports the checkout's console.py as its own module instance, so the store path is
pinned twice: in the environment (for the CLI's instance) and on the imported `console`
(for the assertions).
"""

import io
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

console = pytest.importorskip("console")
from c3s_cli import cli, paths  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    f = tmp_path / "agents.json"
    monkeypatch.setenv("REFLEX_AGENTS_FILE", str(f))          # the CLI's own console instance
    monkeypatch.setattr(console, "AGENTS_FILE", f)             # the one the assertions use
    monkeypatch.setattr(paths, "console_dir", lambda remember=False: ROOT)
    monkeypatch.delenv("REFLEX_AGENT_TOKEN", raising=False)
    return f


def run(capsys, *argv) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


# -- bind ------------------------------------------------------------------------------


def test_bind_takes_the_token_from_the_agents_own_environment(store, monkeypatch, capsys):
    monkeypatch.setenv("REFLEX_AGENT_TOKEN", "s3cret-from-env")
    code, out, _ = run(capsys, "token", "bind", "--agent", "claude-code:mine")
    assert code == 0, out
    assert console.check_agent_token("claude-code:mine", "s3cret-from-env") == "ok"
    assert console.check_agent_token("claude-code:mine", "another") == "spoof"
    assert "REFLEX_AGENT_TOKEN" in out and "s3cret-from-env" not in out  # named, never echoed
    assert not console.check_agent_token("claude-code:mine", "", ) == "unbound"  # bound means silence is refused


def test_bind_with_nothing_given_generates_a_token_and_shows_it_once(store, capsys):
    code, out, _ = run(capsys, "token", "bind", "--agent", "fresh")
    assert code == 0, out
    m = re.search(r"REFLEX_AGENT_TOKEN=(\S+)", out)
    assert m, out
    assert console.check_agent_token("fresh", m.group(1)) == "ok"
    assert "ONCE" in out and "written nowhere" in out
    # The file keeps the hash and a time, not the token: the command left no copy behind.
    assert m.group(1) not in store.read_text()


def test_bind_reads_one_line_from_stdin_when_asked(store, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("piped-token\n"))
    code, out, _ = run(capsys, "token", "bind", "--agent", "piped", "--stdin")
    assert code == 0, out
    assert console.check_agent_token("piped", "piped-token") == "ok"
    assert "piped-token" not in out


def test_bind_with_an_empty_stdin_binds_nothing(store, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    code, _, err = run(capsys, "token", "bind", "--agent", "nothing", "--stdin")
    assert code != 0 and "nothing came in" in err
    assert not store.exists()


def test_bind_needs_a_name(store, capsys):
    code, _, err = run(capsys, "token", "bind")
    assert code != 0 and "--agent" in err


def test_a_token_is_never_accepted_as_an_argument(store):
    """An argument is a line in the shell's history, which anything on this machine can
    read; that is the same exposure the operator token file's mode 600 exists to avoid."""
    with pytest.raises(SystemExit) as e:
        cli.main(["token", "bind", "--agent", "x", "--token", "in-history"])
    assert e.value.code == 2
    assert not store.exists()


def test_binding_again_replaces_and_says_so(store, monkeypatch, capsys):
    monkeypatch.setenv("REFLEX_AGENT_TOKEN", "first")
    assert run(capsys, "token", "bind", "--agent", "twice")[0] == 0
    monkeypatch.setenv("REFLEX_AGENT_TOKEN", "second")
    code, out, _ = run(capsys, "token", "bind", "--agent", "twice")
    assert code == 0 and "re-bound" in out and "replaced" in out
    assert console.check_agent_token("twice", "first") == "spoof"
    assert console.check_agent_token("twice", "second") == "ok"


# -- list ------------------------------------------------------------------------------


def test_list_says_when_nothing_is_bound(store, capsys):
    code, out, _ = run(capsys, "token", "list")
    assert code == 0 and "no agent name is bound" in out


def test_list_names_every_bound_agent_and_never_a_hash(store, monkeypatch, capsys):
    monkeypatch.setenv("REFLEX_AGENT_TOKEN", "alpha-token")
    run(capsys, "token", "bind", "--agent", "alpha")
    monkeypatch.setenv("REFLEX_AGENT_TOKEN", "beta-token")
    run(capsys, "token", "bind", "--agent", "beta")
    code, out, _ = run(capsys, "token", "list")
    assert code == 0, out
    assert "2 bound name(s)" in out and "alpha" in out and "beta" in out
    assert "alpha-token" not in out and "beta-token" not in out
    assert not re.search(r"[0-9a-f]{64}", out)  # not the hashes either


def test_list_reports_an_unreadable_store_rather_than_calling_it_empty(store, capsys):
    store.write_text("{not json")
    code, out, err = run(capsys, "token", "list")
    assert code != 0 and "cannot be read" in err and "every request is being refused" in err
    assert "no agent name is bound" not in out


# -- what did not change ----------------------------------------------------------------


def test_rotate_with_an_agent_still_drops_the_binding(store, monkeypatch, capsys):
    monkeypatch.setenv("REFLEX_AGENT_TOKEN", "gone-soon")
    run(capsys, "token", "bind", "--agent", "rot")
    assert console.check_agent_token("rot", "gone-soon") == "ok"
    code, out, _ = run(capsys, "token", "rotate", "--agent", "rot")
    assert code == 0 and "dropped the binding" in out
    assert console.check_agent_token("rot", "") == "unbound"
