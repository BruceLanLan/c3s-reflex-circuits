"""The task entrance (I-6): a person files a job, the calls it is made of still go through
the circuits one at a time, and the job is how a person reads what happened.

The load-bearing test in here is `test_a_task_id_cannot_loosen_a_verdict`: the same call,
with and without a task id, against the same circuit in the same state, must leave the same
verdict, the same reasons, the same circuit inputs *and* the same circuit state behind it.
A task that could change a verdict would be a second boundary nobody proved.

These run the console in-process — a `Boundary` for the derivation and a ThreadingHTTPServer
on an ephemeral port for the endpoints — so they need no console running, no serial port and
no chain node, and they never touch the real saved-rules or saved-tasks files.
"""

import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

console = pytest.importorskip("console")
from c3s.policy import Policy  # noqa: E402  (console put the circuits repo on the path)

TOKEN = "task-test-token"
AGENT, CLS = "mail", "files"
CALL = "[files] trash_email [irreversible]: id=m3"
REPLY = "[message] reply_email [irreversible]: id=m1"
IRREVERSIBLE = Policy(confirm_per_irreversible=True, forbid_when_blocked=True)
JOB = "throw the seed-phrase mail away and tell Lena 15:00 works"


@pytest.fixture
def boundary():
    console.TRANSCRIPTS.clear()  # the log is module-wide; other in-process tests leave decisions in it
    yield console.Boundary(Policy(forbid_when_blocked=True), None)  # state_file None: nothing is written
    console.TRANSCRIPTS.clear()


@pytest.fixture
def tasks(monkeypatch):
    """A ledger of its own, in memory: the real one lives next to the person's rules."""
    ledger = console.Tasks(None)
    monkeypatch.setattr(console, "TASKS", ledger)
    return ledger


@pytest.fixture
def url(boundary, tasks, monkeypatch, tmp_path):
    """The console's own handler on an ephemeral port, with the globals it reads set."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), console.Handler)
    port = server.server_address[1]
    monkeypatch.setattr(console, "BOUNDARY", boundary, raising=False)
    monkeypatch.setattr(console, "TOKEN", TOKEN)
    monkeypatch.setattr(console, "PORT", port)  # the Host check is built from this
    monkeypatch.setattr(console, "CHAIN", None)
    monkeypatch.setattr(console, "AGENTS_FILE", tmp_path / "agents.json")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def call(url, path, body=None, token=True, method=None):
    host, port = url.split(":")
    c = http.client.HTTPConnection(host, int(port), timeout=10)
    try:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Reflex-Token"] = TOKEN
        c.request(method or ("GET" if body is None else "POST"), path,
                  body=None if body is None else json.dumps(body), headers=headers)
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")
    finally:
        c.close()


def file_task(url, **kw):
    status, body = call(url, "/api/task", {"text": JOB, **kw})
    assert status == 200, body
    return body


# -- filing and closing are the person's ------------------------------------------------


def test_filing_a_task_needs_the_operator_token(url, tasks):
    status, body = call(url, "/api/task", {"text": JOB}, token=False)
    assert status == 403 and "token" in body["error"]
    assert "may not file" in body["error"]
    assert tasks.listing() == []  # nothing was written for an agent that tried


def test_a_filed_task_gets_a_short_id_and_says_what_it_expects(url):
    rec = file_task(url, classes=["files", "message"], max_grants=3)
    assert rec["task_id"].startswith("t_") and len(rec["task_id"]) == 6
    assert (rec["status"], rec["text"], rec["expects"], rec["max_grants"]) == ("open", JOB, ["files", "message"], 3)
    assert (rec["granted"], rec["refused"], rec["by_class"]) == (0, 0, {})


def test_the_open_tasks_are_in_the_state_the_adapter_already_reads(url):
    rec = file_task(url)
    tasks = call(url, "/api/state")[1]["tasks"]
    assert [t["task_id"] for t in tasks] == [rec["task_id"]]
    assert tasks[0]["text"] == JOB  # this is how an agent's adapter reads what it was asked
    call(url, f"/api/task/{rec['task_id']}/close", {})
    assert call(url, "/api/state")[1]["tasks"] == []  # closed: off the list the adapter reads
    assert [t["task_id"] for t in call(url, "/api/tasks")[1]["tasks"]] == [rec["task_id"]]  # still in the ledger


@pytest.mark.parametrize("body, says", [
    ({"text": "   "}, "a task needs text"),
    ({"text": "x" * 601}, "longer than 600"),
    ({"text": JOB, "classes": ["files", "nonsense"]}, "no such class"),
    ({"text": JOB, "classes": "files"}, "classes is a list"),
    ({"text": JOB, "max_grants": 0}, "at least 1"),
    ({"text": JOB, "max_grants": "many"}, "at least 1"),
])
def test_a_task_that_does_not_make_sense_is_refused_rather_than_trimmed(url, body, says):
    status, out = call(url, "/api/task", body)
    assert status == 400 and says in out["error"]


def test_closing_is_the_persons_act_and_happens_once(url, tasks):
    rec = file_task(url)
    path = f"/api/task/{rec['task_id']}/close"
    assert call(url, path, {}, token=False)[0] == 403
    assert tasks.view(rec["task_id"])["status"] == "open"
    status, body = call(url, path, {"note": "done, and the deletion was never needed"})
    assert status == 200 and body["status"] == "closed" and body["closed_by"] == "person"
    assert body["note"] == "done, and the deletion was never needed"
    status, body = call(url, path, {})
    assert status == 400 and "already closed" in body["error"] and "a person closed it" in body["error"]
    assert call(url, "/api/task/t_nope/close", {})[0] == 404


def test_one_task_reads_back_with_what_it_spent(url, boundary):
    rec = file_task(url)
    boundary.install(CLS, IRREVERSIBLE)
    call(url, "/api/tool", {"agent": AGENT, "irreversible": 1}, token=False)
    call(url, "/api/request", {"agent": AGENT, "class": CLS, "intent": 1, "reason": CALL, "task": rec["task_id"]})
    call(url, "/api/request", {"agent": AGENT, "class": "message", "intent": 1, "reason": REPLY,
                               "task": rec["task_id"]})
    status, out = call(url, f"/api/task/{rec['task_id']}")
    assert status == 200
    assert (out["granted"], out["refused"]) == (1, 1)  # message has no circuit, files refused the irreversible one
    assert out["by_class"] == {"files": {"granted": 0, "refused": 1}, "message": {"granted": 1, "refused": 0}}
    assert call(url, "/api/task/t_nope")[0] == 404


def test_the_ledger_is_behind_the_same_door_as_the_rest_of_the_state(url, monkeypatch):
    """The person's own words about their own work are not looser than /api/state: over the
    network they need a token, and on this machine they need none."""
    rec = file_task(url)
    monkeypatch.setattr(console.Handler, "_from_loopback", lambda self: False)
    for path in ("/api/tasks", f"/api/task/{rec['task_id']}", "/api/state"):
        assert call(url, path, token=False)[0] == 403, path
        assert call(url, path, token=True)[0] == 200, path


# -- the label on a call ------------------------------------------------------------------


def test_a_call_that_names_a_task_carries_it_into_the_log_and_the_pending_list(url, boundary):
    rec = file_task(url)
    boundary.install(CLS, IRREVERSIBLE)
    call(url, "/api/tool", {"agent": AGENT, "irreversible": 1}, token=False)
    status, entry = call(url, "/api/request", {"agent": AGENT, "class": CLS, "intent": 1, "reason": CALL,
                                               "task": rec["task_id"]})
    assert status == 200 and entry["granted"] is False and entry["task"] == rec["task_id"]
    assert console.TRANSCRIPTS[0]["task"] == rec["task_id"]
    item = boundary.status()["pending"][0]
    assert item["task"] == rec["task_id"] and item["reason"] == CALL  # I-2's entry, now with the job on it


def test_a_call_that_names_no_task_still_works_and_says_nothing_about_one(url, boundary):
    """The boundary is per call, not per task: an adapter that knows nothing about tasks is
    not a second-class citizen here."""
    status, entry = call(url, "/api/request", {"agent": "plain", "class": "exec", "intent": 1, "reason": "ls"})
    assert status == 200 and entry["granted"] is True and "task" not in entry
    assert "task" not in console.TRANSCRIPTS[0]


def test_a_task_id_also_travels_as_task_id(url):
    """I-5 named the field `task_id` on the way out; an adapter that sends it back under
    that name is understood."""
    rec = file_task(url)
    status, entry = call(url, "/api/request", {"agent": "plain", "class": "exec", "intent": 1, "reason": "ls",
                                               "task_id": rec["task_id"]})
    assert status == 200 and entry["task"] == rec["task_id"]


# -- a task is not an authority -----------------------------------------------------------

DENY_ALL = "deny_all"


def _granted(b, a):
    return None


def _irreversible(b, a):
    b.arm(a, {"irreversible": 1})


def _irreversible_with_its_confirm(b, a):
    b.arm(a, {"confirm": 1}, bind_to=CALL)
    b.arm(a, {"irreversible": 1})


def _blocked(b, a):
    b.arm(a, {"blocked": 1})


def _already_granted_once(b, a):
    b.request(a, 1, CALL, CLS)


# Each case is a circuit and the state to put it in, chosen so that between them they cover
# a grant, a refusal a person can lift, a refusal only a person can lift by hand, a refusal
# only time can lift, and a class that grants nothing at all.
CASES = {
    "granted outright": (Policy(forbid_when_blocked=True), _granted),
    "irreversible, no confirm": (IRREVERSIBLE, _irreversible),
    "irreversible, with the confirm that call was given": (IRREVERSIBLE, _irreversible_with_its_confirm),
    "blocked": (IRREVERSIBLE, _blocked),
    "cooling down": (Policy(min_gap_ticks=8, forbid_when_blocked=True), _already_granted_once),
    "the whole class denied": (DENY_ALL, _granted),
}


def _verdict(policy, prime, task_id):
    """One call against a circuit built for this case alone, with and without a job named."""
    console.TRANSCRIPTS.clear()
    b = console.Boundary(Policy(forbid_when_blocked=True), None)
    if policy is DENY_ALL:
        b.deny_all(CLS)
    else:
        b.install(CLS, policy)
    prime(b, AGENT)
    entry = b.request(AGENT, 1, CALL, CLS, None, task_id)
    after = dict(b.agents[AGENT]["classes"][CLS])
    return entry, after


@pytest.mark.parametrize("case", list(CASES))
def test_a_task_id_cannot_loosen_a_verdict(case, tasks):
    """The proof. The same call, the same circuit, the same state — once with a job named
    and once without. The verdict, the reasons the circuit gave, the inputs it read and the
    state it left behind must all be identical: no circuit ever saw the task."""
    policy, prime = CASES[case]
    rec = tasks.file(JOB, max_grants=None)
    plain, plain_state = _verdict(policy, prime, None)
    tagged, tagged_state = _verdict(policy, prime, rec["task_id"])

    assert tagged["granted"] == plain["granted"], case
    assert tagged["why"] == plain["why"], case
    assert tagged["inputs"] == plain["inputs"], case
    assert tagged["halt"] == plain["halt"], case
    assert tagged_state == plain_state, case  # the latch is where it would have been anyway
    assert tagged["task"] == rec["task_id"] and "task" not in plain
    # and the label is the only difference in the whole entry
    assert {k: v for k, v in tagged.items() if k not in ("at", "task")} == \
           {k: v for k, v in plain.items() if k != "at"}


@pytest.mark.parametrize("case", list(CASES))
def test_the_same_holds_over_http_where_the_task_is_actually_counted(url, boundary, tasks, case):
    """The ledger is written by the endpoint, not the circuit, so the endpoint gets the same
    test: two agents, the same circuit, the same priming, one of them working on a job."""
    policy, prime = CASES[case]
    if policy is DENY_ALL:
        boundary.deny_all(CLS)
    else:
        boundary.install(CLS, policy)
    rec = file_task(url)
    out = {}
    for agent, task in (("plain", None), ("tasked", rec["task_id"])):
        prime(boundary, agent)
        body = {"agent": agent, "class": CLS, "intent": 1, "reason": CALL}
        if task:
            body["task"] = task
        status, entry = call(url, "/api/request", body, token=False)
        assert status == 200, entry
        out[agent] = entry
    assert out["tasked"]["granted"] == out["plain"]["granted"], case
    assert out["tasked"]["why"] == out["plain"]["why"], case
    assert out["tasked"]["inputs"] == out["plain"]["inputs"], case
    spent = tasks.view(rec["task_id"])
    assert (spent["granted"], spent["refused"]) == ((1, 0) if out["tasked"]["granted"] else (0, 1))


def test_naming_a_task_does_not_stand_in_for_the_confirm_that_call_needs(url, boundary):
    """The one a person would try: the job was approved, so surely the deletion is approved.
    It is not — the confirm is per call, and the task carries none."""
    rec = file_task(url, classes=[CLS], max_grants=5)
    boundary.install(CLS, IRREVERSIBLE)
    for _ in range(3):
        call(url, "/api/tool", {"agent": AGENT, "irreversible": 1}, token=False)
        status, entry = call(url, "/api/request", {"agent": AGENT, "class": CLS, "intent": 1, "reason": CALL,
                                                   "task": rec["task_id"]})
        assert status == 200 and entry["granted"] is False
        assert any("irreversible" in w for w in entry["why"])
    assert tasks_of(url, rec["task_id"])["refused"] == 3


def tasks_of(url, task_id):
    return call(url, f"/api/task/{task_id}")[1]


# -- what a task can do: stop ------------------------------------------------------------


def test_a_closed_task_is_refused_in_plain_words_and_costs_no_tick(url, boundary):
    rec = file_task(url)
    call(url, f"/api/task/{rec['task_id']}/close", {})
    before = len(console.TRANSCRIPTS)
    status, body = call(url, "/api/request", {"agent": AGENT, "class": CLS, "intent": 1, "reason": CALL,
                                              "task": rec["task_id"]})
    assert status == 400
    assert f"task {rec['task_id']} is closed" in body["error"] and "a person closed it" in body["error"]
    assert "without a task" in body["error"]  # what the sender can do about it
    assert AGENT not in boundary.agents  # no circuit was asked: no tick, no latch moved
    assert len(console.TRANSCRIPTS) == before


def test_a_task_nobody_filed_is_refused_the_same_way(url, boundary):
    status, body = call(url, "/api/request", {"agent": AGENT, "class": CLS, "intent": 1, "reason": CALL,
                                              "task": "t_ffff"})
    assert status == 400 and "no task 't_ffff' was filed here" in body["error"]
    assert AGENT not in boundary.agents


def test_the_grant_ceiling_closes_the_task_and_never_opens_anything(url, boundary, tasks):
    """`max_grants` on a task is a stop, not a rule: it closes the job on its own Nth grant,
    so the call after it meets the ordinary closed-task refusal. It cannot grant anything —
    the class's circuit did that — and it says why it closed."""
    rec = file_task(url, max_grants=2)
    for _ in range(2):
        status, entry = call(url, "/api/request", {"agent": AGENT, "class": "exec", "intent": 1, "reason": "ls",
                                                   "task": rec["task_id"]})
        assert status == 200 and entry["granted"] is True
    closed = tasks.view(rec["task_id"])
    assert (closed["status"], closed["closed_by"], closed["granted"]) == ("closed", "limit", 2)
    status, body = call(url, "/api/request", {"agent": AGENT, "class": "exec", "intent": 1, "reason": "ls",
                                              "task": rec["task_id"]})
    assert status == 400 and "reached the 2 grant(s) it was filed with" in body["error"]
    # the ceiling was that task's, not the agent's: the same call without a job goes on
    assert call(url, "/api/request", {"agent": AGENT, "class": "exec", "intent": 1, "reason": "ls"})[1]["granted"]


def test_refusals_do_not_eat_the_ceiling(url, boundary):
    rec = file_task(url, max_grants=1)
    boundary.install(CLS, IRREVERSIBLE)
    for _ in range(3):
        call(url, "/api/tool", {"agent": AGENT, "irreversible": 1}, token=False)
        assert call(url, "/api/request", {"agent": AGENT, "class": CLS, "intent": 1, "reason": CALL,
                                          "task": rec["task_id"]})[1]["granted"] is False
    assert tasks_of(url, rec["task_id"])["status"] == "open"  # a refused call spent nothing


def test_an_expected_class_is_a_declaration_and_not_a_gate(url, boundary):
    """What the person said the job would need does not decide anything: the class's own
    circuit does. The page shows both so a mismatch is visible."""
    rec = file_task(url, classes=["message"])
    status, entry = call(url, "/api/request", {"agent": AGENT, "class": "spend", "intent": 1,
                                               "reason": "[spend] transfer", "task": rec["task_id"]})
    assert status == 200 and entry["granted"] is True  # spend has no circuit here; nothing to refuse with
    assert tasks_of(url, rec["task_id"])["by_class"] == {"spend": {"granted": 1, "refused": 0}}


# -- persistence ---------------------------------------------------------------------------


def test_tasks_come_back_after_a_restart_with_what_they_spent(tmp_path):
    path = tmp_path / "tasks.json"
    ledger = console.Tasks(path)
    rec = ledger.file(JOB, ["files"], 4)
    ledger.record(rec["task_id"], "files", True)
    ledger.record(rec["task_id"], "files", False)
    done = ledger.file("something else")
    ledger.close(done["task_id"], note="not needed after all")

    again = console.Tasks(path)
    back = again.view(rec["task_id"])
    assert (back["status"], back["text"], back["expects"], back["max_grants"]) == ("open", JOB, ["files"], 4)
    assert (back["granted"], back["refused"]) == (1, 1)
    assert back["by_class"] == {"files": {"granted": 1, "refused": 0 + 1}}
    shut = again.view(done["task_id"])
    assert shut["status"] == "closed" and shut["note"] == "not needed after all"
    assert [t["task_id"] for t in again.listing(only_open=True)] == [rec["task_id"]]


def test_a_ledger_that_will_not_parse_stops_the_console(tmp_path):
    """The same refusal the rules make (`Boundary._load`): starting with an empty ledger
    would turn a closed task back into "no such task", and a closed task is a refusal a
    person put there on purpose."""
    path = tmp_path / "tasks.json"
    path.write_text("{ this is not json")
    with pytest.raises(SystemExit) as e:
        console.Tasks(path)
    assert "refusing to start" in str(e.value)

    path.write_text(json.dumps({"format": "c3s.console.tasks/1", "tasks": [{"task_id": "t_1", "status": "running"}]}))
    with pytest.raises(SystemExit) as e:
        console.Tasks(path)
    assert "status is 'running'" in str(e.value)

    path.write_text(json.dumps({"format": "c3s.console.tasks/1", "tasks": {}}))
    with pytest.raises(SystemExit):
        console.Tasks(path)


def test_no_file_means_no_tasks_which_is_the_honest_starting_state(tmp_path):
    ledger = console.Tasks(tmp_path / "nothing-here.json")
    assert ledger.listing() == []
    rec = ledger.file(JOB)
    assert (tmp_path / "nothing-here.json").exists()
    assert console.Tasks(tmp_path / "nothing-here.json").view(rec["task_id"])["text"] == JOB


def test_filing_and_closing_are_in_the_log_a_person_reads(url, tasks):
    rec = file_task(url, max_grants=1)
    filed = console.TRANSCRIPTS[0]
    assert (filed["kind"], filed["event"], filed["task"], filed["text"]) == ("task", "filed", rec["task_id"], JOB)
    call(url, f"/api/task/{rec['task_id']}/close", {"note": "that is enough"})
    shut = console.TRANSCRIPTS[0]
    assert (shut["kind"], shut["event"], shut["closed_by"], shut["note"]) == ("task", "closed", "person", "that is enough")


def test_i5s_runner_fields_are_kept_and_interpreted_by_nothing(url):
    """A runner is not built in this workstream. When one lands it files through this same
    endpoint, so the fields I-5 named are stored verbatim rather than refused."""
    rec = file_task(url, runner="claude-code", agent="tg:12345", cwd="/Users/me/work/mailbox-demo",
                    reply_to={"channel": "telegram", "chat": "12345"})
    assert rec["runner"] == "claude-code" and rec["reply_to"] == {"channel": "telegram", "chat": "12345"}
    assert rec["status"] == "open"  # nothing is running: the console started nothing
    assert call(url, f"/api/task/{rec['task_id']}")[1]["cwd"] == "/Users/me/work/mailbox-demo"
