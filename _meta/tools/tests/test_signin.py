"""signin: the GitHub device flow and the Claude command line's browser sign-in, started in the
first minute and read back by their own commands; the signins phase that lists what the person
does. No test calls the real gh or claude."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import doctor
import routine
import setup_journal
import signin
import start

CODE_LINES = ["\n", "! First copy your one-time code: 4F2A-9C1B\n",
              "Open this URL to continue in your web browser: https://github.com/login/device\n"]
AUTHORIZE = "https://claude.com/cai/oauth/authorize?client_id=x&state=y"
CLAUDE_LINES = ["Opening browser to sign in…\n", f"If the browser didn't open, visit: {AUTHORIZE}\n",
                "Paste code here if prompted > "]


class FakeProc:
    """A Popen stand-in. Its `stderr` and `stdout` yield `lines`; `None` for both, as a real
    Popen has when its output goes to a file. Records whether it was killed."""

    def __init__(self, lines=None, pid=4101, returncode=None):
        self.stderr = iter(lines) if lines is not None else None
        self.stdout = iter(lines) if lines is not None else None
        self.pid, self.returncode, self.killed = pid, returncode, False

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


def machine(answers=None, system="Windows") -> doctor.Machine:
    """A machine whose `run` answers by the first words of the command, like
    test_sync_task.world: `answers` maps them to (code, stdout). Every command is in `.calls`, the
    environment each ran in (None: the process's own) in `.envs`."""
    calls, envs = [], []

    def run(argv, cwd=None, input=None, env=None):
        argv = [str(a) for a in argv]
        calls.append(argv)
        envs.append(env)
        for prefix, (code, out) in (answers or {}).items():
            if " ".join(argv).startswith(prefix):
                return subprocess.CompletedProcess(argv, code, out, "")
        return subprocess.CompletedProcess(argv, 0, "", "")
    m = doctor.Machine(run=run, system=system, arch="x86_64", home=Path.home(), reach=lambda: True)   # the tests' fake home
    m.calls, m.envs = calls, envs
    return m


@pytest.fixture(autouse=True)
def _claude_is_installed_and_no_flow_is_running(monkeypatch):
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    monkeypatch.setattr(routine, "pid_alive", lambda pid: False)   # a recorded number could be anyone's here


def status(logged_in, method, **more) -> str:
    return json.dumps({"loggedIn": logged_in, "authMethod": method, **more})


# ----- the GitHub device flow ------------------------------------------------------------
def test_the_github_code_is_read_from_the_device_flow_and_the_flow_keeps_running():
    proc = FakeProc(lines=["! First copy your one-time code: 4F2A-9C1B\n", "Open this URL to continue in your web browser: https://github.com/login/device\n"])
    got = signin.github_start(machine(), spawn=lambda *a, **k: proc)
    assert got["code"] == "4F2A-9C1B" and got["link"] == "https://github.com/login/device"
    assert not proc.killed


def test_the_device_flow_starts_detached_with_no_prompt_and_no_input():
    seen = []

    def spawn(argv, **kw):
        seen.append((argv, kw))
        return FakeProc(CODE_LINES, pid=4242)
    got = signin.github_start(machine(system="Windows"), spawn=spawn)
    argv, kw = seen[0]
    assert argv == ["gh", "auth", "login", "--web", "--git-protocol", "https", "--hostname", "github.com"]
    assert kw["stdin"] is subprocess.DEVNULL and kw["env"]["GH_PROMPT_DISABLED"] == "1"
    assert kw["creationflags"] & 0x8 and "start_new_session" not in kw   # DETACHED_PROCESS: outlives the console
    assert got["pid"] == 4242
    spawn_unix = []
    signin.github_start(machine(system="Linux"), spawn=lambda argv, **kw: spawn_unix.append(kw) or FakeProc(CODE_LINES))
    assert spawn_unix[0]["start_new_session"] is True and "creationflags" not in spawn_unix[0]


def test_the_sign_in_output_goes_to_a_log_the_flow_owns_not_to_a_pipe_this_process_must_keep_open():
    """A pipe closed when start.py exits would kill gh at its first message after the approval."""
    seen = []

    def spawn(argv, **kw):
        seen.append(kw)
        return FakeProc(CODE_LINES)
    signin.github_start(machine(), spawn=spawn)
    log = Path(seen[0]["stdout"].name)
    assert log.parent == Path.home() / ".spaces" / "logs" and log.name.startswith("gh-login-")
    assert seen[0]["stderr"] is subprocess.STDOUT


def test_the_link_is_the_known_one_when_gh_prints_only_the_code():
    got = signin.github_start(machine(), spawn=lambda *a, **k: FakeProc(CODE_LINES[:2]))
    assert got["code"] == "4F2A-9C1B" and got["link"] == signin.DEVICE_LINK == "https://github.com/login/device"


def test_a_gh_that_prints_nothing_is_stopped_and_reported_not_waited_for():
    proc = FakeProc(lines=None)   # output only in its log, which stays empty
    started = time.monotonic()
    with pytest.raises(signin.SigninError, match="code"):
        signin.github_start(machine(), spawn=lambda *a, **k: proc, timeout_s=0.3)
    assert proc.killed and time.monotonic() - started < 5


def test_a_gh_that_ends_without_a_code_says_what_it_printed():
    proc = FakeProc(["error: the value of the GH_TOKEN environment variable is being used\n"])
    with pytest.raises(signin.SigninError, match="GH_TOKEN"):
        signin.github_start(machine(), spawn=lambda *a, **k: proc)
    assert proc.killed


def test_a_flow_started_for_real_is_read_from_its_log_and_keeps_going_after_the_code(tmp_path):
    """A real process stands in for gh: it prints the code, goes on for a moment and then writes
    what gh writes after the approval, before it stores the token."""
    marker = tmp_path / "stored"
    child = ("import sys, time\n"
             "sys.stderr.write('! First copy your one-time code: 4F2A-9C1B\\n"
             "Open this URL to continue in your web browser: https://github.com/login/device\\n')\n"
             "sys.stderr.flush()\n"
             "time.sleep(1.5)\n"
             "sys.stderr.write('Authentication complete.\\n')\n"
             "open(sys.argv[1], 'w').write('stored')\n")
    procs = []

    def spawn(argv, **kw):
        procs.append(subprocess.Popen([sys.executable, "-c", child, str(marker)], **kw))
        return procs[0]
    got = signin.github_start(machine(system=doctor.platform.system()), spawn=spawn)
    assert got["code"] == "4F2A-9C1B" and got["pid"] == procs[0].pid
    assert procs[0].poll() is None and not marker.exists()   # not waited for
    assert procs[0].wait(timeout=30) == 0 and marker.read_text() == "stored"


# ----- the Claude command line -----------------------------------------------------------
def test_an_inherited_api_key_is_not_a_claude_account(monkeypatch):
    m = machine(answers={"claude": (0, '{"loggedIn": true, "authMethod": "api_key"}')})
    assert signin.claude_state(m) == "api-key"


@pytest.mark.parametrize("code,out,state", [
    (0, status(True, "claude.ai"), "account"),
    (0, status(True, "oauth_token"), "account"),
    (0, status(True, "api_key"), "api-key"),
    (0, status(True, "api_key_helper"), "api-key"),
    (0, status(True, "third_party"), "api-key"),
    (0, status(True, "something-new"), "api-key"),   # signed in, but not known to be a claude.ai account
    (1, status(False, "none"), "signed-out"),        # Windows exits 1 when signed out ...
    (0, status(False, "none"), "signed-out"),        # ... Linux exits 0: the exit code decides nothing
    (1, status(True, "claude.ai"), "account"),
    (0, "Not logged in. Run claude auth login to authenticate.", "signed-out"),
    (127, "", "missing"),
])
def test_the_state_comes_from_what_the_status_says_never_from_the_exit_code(code, out, state):
    assert signin.claude_state(machine({"claude auth status --json": (code, out)})) == state


def test_no_claude_command_is_missing_without_asking_anything(monkeypatch):
    monkeypatch.setattr(routine, "find_claude", lambda: None)
    m = machine()
    assert signin.claude_state(m) == "missing" and m.calls == []


def test_the_account_behind_the_sign_in_is_its_email_and_organisation():
    out = status(True, "claude.ai", email="ada@lanternworks.example", orgName="Lantern Works")
    assert signin.claude_identity(machine({"claude auth status": (0, out)})) == {
        "email": "ada@lanternworks.example", "org": "Lantern Works"}
    assert signin.claude_identity(machine({"claude auth status": (0, status(True, "claude.ai"))})) == {}
    assert signin.claude_identity(machine({"claude auth status": (1, status(False, "none"))})) == {}


def test_the_claude_sign_in_opens_the_browser_itself_and_gives_the_fallback_link():
    seen = []

    def spawn(argv, **kw):
        seen.append((argv, kw))
        return FakeProc(CLAUDE_LINES, pid=4300)
    got = signin.claude_start(machine(), spawn=spawn)
    argv, kw = seen[0]
    assert argv == ["claude", "auth", "login", "--claudeai"] and kw["stdin"] is subprocess.DEVNULL
    assert got == {"link": AUTHORIZE, "pid": 4300}


def test_a_link_wrapped_in_terminal_escapes_is_still_the_link():
    wrapped = f"If the browser didn't open, visit: \x1b]8;;{AUTHORIZE}\x07{AUTHORIZE}\x1b]8;;\x07\n"
    assert signin.claude_start(machine(), spawn=lambda *a, **k: FakeProc([wrapped]))["link"] == AUTHORIZE


def test_a_claude_that_prints_no_link_is_not_an_error_while_it_runs_but_is_when_it_failed():
    still_going = FakeProc(lines=None)
    assert signin.claude_start(machine(), spawn=lambda *a, **k: still_going, timeout_s=0.2)["link"] is None
    assert not still_going.killed
    failed = FakeProc(["Login failed: could not reach the server\n"], returncode=1)
    with pytest.raises(signin.SigninError, match="could not reach"):
        signin.claude_start(machine(), spawn=lambda *a, **k: failed)


# ----- waiting for both -------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.now, self.slept = 0.0, []

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_wait_polls_every_three_seconds_until_both_read_back():
    clock, signed = Clock(), iter([False, False, True])
    m = machine({"claude auth status": (0, status(True, "claude.ai"))})
    real_run = m.run

    def run(argv, **kw):   # GitHub reads back on the third look
        if argv[:3] == ["gh", "auth", "status"]:
            m.calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0 if next(signed) else 1, "", "")
        return real_run(argv, **kw)
    m.run = run
    got = signin.wait(m, sleep=clock.sleep, clock=lambda: clock.now)
    assert got == {"github": True, "claude": "account"} and clock.slept == [3, 3]


def test_wait_gives_the_last_reading_when_the_time_is_up():
    clock = Clock()
    m = machine({"gh auth status": (1, ""), "claude auth status": (0, status(False, "none"))})
    got = signin.wait(m, timeout_s=10, sleep=clock.sleep, clock=lambda: clock.now)
    assert got == {"github": False, "claude": "signed-out"} and clock.slept == [3, 3, 3, 3]


def test_wait_stops_asking_for_a_sign_in_once_it_has_read_back(monkeypatch):
    clock, asked = Clock(), []
    m = machine({"gh auth status": (0, ""), "claude auth status": (0, status(False, "none"))})
    signed_out = m.run

    def run(argv, **kw):
        asked.append(argv[0])
        return signed_out(argv, **kw)
    m.run = run
    signin.wait(m, timeout_s=6, sleep=clock.sleep, clock=lambda: clock.now)
    assert asked.count("gh") == 1 and asked.count("claude") == 3


def test_wait_reads_the_commands_never_the_journal(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the journal is not polled")
    monkeypatch.setattr(setup_journal, "load", refuse)
    clock = Clock()
    m = machine({"gh auth status": (0, ""), "claude auth status": (0, status(True, "claude.ai"))})
    assert signin.wait(m, sleep=clock.sleep, clock=lambda: clock.now)["claude"] == "account"


def test_a_flow_is_live_only_while_its_process_runs_and_is_young_enough_to_be_it(monkeypatch):
    monkeypatch.setattr(routine, "pid_alive", lambda pid: pid == 4101)
    now = 1_000_000
    assert signin.flow_alive(4101, now - 60, now=now)
    assert not signin.flow_alive(4102, now - 60, now=now)                              # gone
    assert not signin.flow_alive(4101, now - signin.FLOW_MINUTES * 60 - 1, now=now)   # the code has expired
    assert not signin.flow_alive(None, None, now=now) and not signin.flow_alive(4101, None, now=now)


# ----- the signins phase -----------------------------------------------------------------
GH_AND_CLAUDE = [doctor.Row("gh", True, "gh 2.101.0", "gh --version"),
                 doctor.Row("claude command line", True, "2.1.303 (Claude Code)", "claude --version")]


def world(monkeypatch, github=False, claude="signed-out", login="ada-okafor", orgs=(), rows=None, email="ada@lanternworks.example",
          real_rows=False):
    """The host and both command lines faked: what was started is in the returned list. `rows`
    are the doctor's rows by name; a tool without one gets the first (the network's, say).
    `real_rows`: the doctor's own `tool_row`, for a test that drives a fake machine."""
    started = []
    named = {r.name: r for r in (GH_AND_CLAUDE if rows is None else rows)}
    if not real_rows:
        monkeypatch.setattr(start.doctor, "tool_row", lambda name, m=None, fix=True: named.get(name) or next(iter(named.values())))
    monkeypatch.setenv("PATH", os.environ["PATH"])   # the doctor puts the tools it installs on PATH: undone after the test
    monkeypatch.setattr(signin, "github_signed_in", lambda m: github)
    monkeypatch.setattr(signin, "claude_state", lambda m: claude)
    monkeypatch.setattr(signin, "claude_identity", lambda m: {"email": email, "org": "Lantern Works"})

    def github_start(m, **kw):
        started.append("github")
        return {"code": f"4F2A-9C1{len(started)}", "link": "https://github.com/login/device", "pid": 4101}

    def claude_start(m, **kw):
        started.append("claude")
        return {"link": AUTHORIZE, "pid": 4102}
    monkeypatch.setattr(signin, "github_start", github_start)
    monkeypatch.setattr(signin, "claude_start", claude_start)
    monkeypatch.setattr(start, "identity", lambda *a, **k: {"name": "Ada Okafor", "email": email, "login": login})
    monkeypatch.setattr(start, "member_orgs", lambda: list(orgs))
    return started


def signins(capsys, *flags):
    code = start.main(["signins", "--json", *flags])
    return code, json.loads(capsys.readouterr().out)


def test_signins_gives_both_needs_in_one_answer(tmp_path, monkeypatch, capsys):
    started = world(monkeypatch)
    assert start.main(["signins", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [n["kind"] for n in result["needs"]] == ["github-code", "claude-signin"]
    github, claude = result["needs"]
    assert github["code"] == "4F2A-9C11" and github["link"] == "https://github.com/login/device"
    assert github["check"] == "gh auth status" and claude["link"] == AUTHORIZE
    assert claude["check"] == "claude auth status --json" and started == ["github", "claude"]
    assert result["state"] == "waiting" and result["github"] is False and result["claude"] == "signed-out"
    assert result["namespace"] is None
    assert setup_journal.load()["signins"]["state"] == "waiting"


def test_nothing_is_started_for_a_sign_in_that_already_reads_back(monkeypatch, capsys):
    started = world(monkeypatch, github=True, claude="account")
    code, result = signins(capsys)
    assert code == 0 and started == [] and result["state"] == "done" and result["needs"] == []
    assert result["github"] is True and result["claude"] == "account" and result["namespace"] == "ada-okafor"


def test_the_account_and_the_namespace_are_facts_in_the_local_journal(monkeypatch, capsys):
    world(monkeypatch, github=True, claude="account")
    signins(capsys)
    record = setup_journal.load()["signins"]
    assert record["state"] == "done" and record["github"] == "ada-okafor" and record["claude"] == "account"
    assert record["claude_email"] == "ada@lanternworks.example" and record["claude_org"] == "Lantern Works"
    assert record["namespace"] == "ada-okafor"


def test_only_the_sign_in_that_is_missing_is_started(monkeypatch, capsys):
    started = world(monkeypatch, github=True, claude="api-key")
    code, result = signins(capsys)
    assert started == ["claude"] and [n["kind"] for n in result["needs"]] == ["claude-signin"]
    assert result["claude"] == "api-key" and result["state"] == "waiting"
    started = world(monkeypatch, github=False, claude="account")
    code, result = signins(capsys)
    assert started == ["github"] and [n["kind"] for n in result["needs"]] == ["github-code"]


def test_a_rerun_keeps_a_live_device_flow_and_replaces_a_dead_one(monkeypatch, capsys):
    started = world(monkeypatch)
    signins(capsys)
    live = {4101, 4102}
    monkeypatch.setattr(routine, "pid_alive", lambda pid: pid in live)
    code, result = signins(capsys)   # the person came back: both flows still run
    assert started == ["github", "claude"] and [n["code"] for n in result["needs"] if n["kind"] == "github-code"] == ["4F2A-9C11"]
    live.clear()   # the code expired and gh ended
    code, result = signins(capsys)
    assert started == ["github", "claude", "github", "claude"]
    codes = [n["code"] for n in result["needs"] if n["kind"] == "github-code"]
    assert codes == ["4F2A-9C13"]   # one need, with the new code


def test_wait_stays_until_both_read_back_and_then_clears_the_needs(monkeypatch, capsys):
    started = world(monkeypatch)
    waited = []
    monkeypatch.setattr(signin, "wait", lambda m, **kw: waited.append(1) or {"github": True, "claude": "account"})
    code, result = signins(capsys)
    assert waited == [] and result["state"] == "waiting"
    code, result = signins(capsys, "--wait")
    assert waited == [1] and result["state"] == "done" and result["needs"] == []
    assert result["github"] is True and result["claude"] == "account"
    assert setup_journal.load()["signins"]["state"] == "done"


def test_wait_ends_with_the_needs_still_open_when_the_person_has_not_finished(monkeypatch, capsys):
    world(monkeypatch)
    monkeypatch.setattr(signin, "wait", lambda m, **kw: {"github": True, "claude": "signed-out"})
    code, result = signins(capsys, "--wait")
    assert code == 0 and result["state"] == "waiting" and result["github"] is True
    assert [n["kind"] for n in result["needs"]] == ["claude-signin"]


def test_a_sign_in_that_will_not_start_is_reported_and_the_other_still_starts(monkeypatch, capsys):
    started = world(monkeypatch)

    def refused(m, **kw):
        raise signin.SigninError("GitHub did not give a sign-in code")
    monkeypatch.setattr(signin, "github_start", refused)
    code, result = signins(capsys, "--wait")   # nothing to wait for: it reports at once
    assert code == 1 and result["state"] == "failed" and "did not give a sign-in code" in result["why"]
    assert started == ["claude"] and [n["kind"] for n in result["needs"]] == ["claude-signin"]
    assert setup_journal.load()["signins"]["state"] == "failed"


def test_a_tool_the_doctor_could_not_install_fails_the_phase_with_its_sentence(monkeypatch, capsys):
    started = world(monkeypatch, rows=[
        doctor.Row("gh", False, "not found", "gh --version", yours="install GitHub's command line tool once"),
        doctor.Row("claude command line", True, "2.1.303 (Claude Code)", "claude --version")])
    code, result = signins(capsys)
    assert code == 1 and result["state"] == "failed" and "install GitHub's command line tool" in result["why"]
    assert started == ["claude"]


def test_no_network_is_one_sentence_not_two(monkeypatch, capsys):
    started = world(monkeypatch, rows=[doctor.Row("network", False, "github.com unreachable", "https://api.github.com",
                                                  yours="there is no connection to github.com: reconnect")])
    code, result = signins(capsys)
    assert code == 1 and result["why"] == "there is no connection to github.com: reconnect" and started == []


def test_a_dry_run_reads_and_starts_nothing(monkeypatch, capsys):
    started = world(monkeypatch)
    fixed = []
    named = {r.name: r for r in GH_AND_CLAUDE}
    monkeypatch.setattr(start.doctor, "tool_row", lambda name, m=None, fix=True: fixed.append(fix) or named[name])
    code, result = signins(capsys, "--dry-run")
    assert code == 0 and started == [] and fixed == [False, False] and result["state"] == "pending"
    assert not setup_journal.journal_path().exists()


def test_offline_has_nothing_to_sign_in_to(monkeypatch, capsys):
    monkeypatch.setattr(start.doctor, "tool_row", lambda *a, **k: pytest.fail("offline asks nothing"))
    code, result = signins(capsys, "--offline")
    assert code == 0 and result["state"] == "done" and result["needs"] == []


def test_the_phase_prints_what_it_found_in_plain_lines(monkeypatch, capsys):
    world(monkeypatch)
    assert start.main(["signins"]) == 0
    out = capsys.readouterr().out
    assert "signins: waiting" in out and "4F2A-9C11" in out and "github.com/login/device" in out


def test_a_failed_phase_says_why_in_its_first_line(monkeypatch, capsys):
    world(monkeypatch, rows=[doctor.Row("network", False, "github.com unreachable", "https://api.github.com",
                                        yours="there is no connection to github.com: reconnect")])
    assert start.main(["signins"]) == 1
    assert capsys.readouterr().out.splitlines()[0] == "signins: failed: there is no connection to github.com: reconnect"


def test_one_matching_org_is_picked_and_two_are_a_choice():
    assert start.pick_namespace("ada-okafor", [], "Lantern Works", "lanternworks.example") == ("ada-okafor", [])
    assert start.pick_namespace("ada-okafor", ["lantern-works", "ada-side"], "Lantern Works", None) == ("lantern-works", [])
    assert start.pick_namespace("ada-okafor", ["acme", "beacon-labs"], "Lantern Works", None) == (None, ["acme", "beacon-labs", "ada-okafor"])


def test_the_mail_domain_names_the_organisation_when_the_company_is_not_known_yet():
    orgs = ["lanternworks", "ada-side"]
    assert start.pick_namespace("ada-okafor", orgs, None, "lanternworks.example") == ("lanternworks", [])
    assert start.pick_namespace("ada-okafor", ["acme"], None, "lanternworks.example") == (None, ["acme", "ada-okafor"])
    assert start.pick_namespace("ada-okafor", ["acme"], None, None) == (None, ["acme", "ada-okafor"])


def test_hyphens_and_capitals_do_not_hide_a_match_and_two_matches_are_still_a_choice():
    assert start.pick_namespace("ada", ["Lantern-Works", "other"], "Lantern Works", None) == ("Lantern-Works", [])
    assert start.pick_namespace("ada", ["lantern-works", "lanternworks"], "Lantern Works", None) == (
        None, ["lantern-works", "lanternworks", "ada"])
    assert start.pick_namespace("ada", ["lantern-works", "lantern-works"], "Lantern Works", None) == ("lantern-works", [])


def test_an_ambiguous_account_asks_once_with_the_choices_and_not_again_once_answered(monkeypatch, capsys):
    world(monkeypatch, github=True, claude="account", login="tess-v", orgs=["acme", "other-co"])
    code, result = signins(capsys)
    assert code == 0 and result["state"] == "waiting" and result["namespace"] is None
    assert result["choices"] == ["acme", "other-co", "tess-v"]
    [need] = result["needs"]
    assert need["kind"] == "namespace" and "tess-v" in need["text"] and "acme, other-co" in need["text"]
    code, result = signins(capsys)   # asked again: still one need
    assert [n["kind"] for n in result["needs"]] == ["namespace"]
    code, result = signins(capsys, "--org", "acme")   # answered with the sentence's own word
    assert result["state"] == "done" and result["namespace"] == "acme" and result["needs"] == []
    code, result = signins(capsys)   # remembered
    assert result["state"] == "done" and result["namespace"] == "acme"


# ----- the environment a routine has ----------------------------------------------------------
STRIPPED = ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_OAUTH_TOKEN"]


@pytest.mark.parametrize("variable", STRIPPED)
def test_the_status_is_read_in_the_environment_a_routine_has(monkeypatch, variable):
    monkeypatch.setenv(variable, "made-up")
    monkeypatch.setenv("LANTERN_KEPT", "kept")
    m = machine({"claude auth status": (0, status(False, "none"))})
    signin.claude_state(m)
    signin.claude_identity(m)
    assert len(m.envs) == 2
    assert all(env is not None and variable not in env and env["LANTERN_KEPT"] == "kept" for env in m.envs)


@pytest.mark.parametrize("variable", STRIPPED)
def test_the_sign_in_is_started_in_the_environment_a_routine_has(monkeypatch, variable):
    monkeypatch.setenv(variable, "made-up")
    seen = []
    signin.claude_start(machine(), spawn=lambda argv, **kw: seen.append(kw["env"]) or FakeProc(CLAUDE_LINES))
    assert variable not in seen[0] and "PATH" in seen[0]


def test_a_token_in_the_session_is_not_a_sign_in_a_routine_would_have(monkeypatch):
    """With an empty configuration the made-up token reads as a signed-in `oauth_token`, to a
    command that sees it. A routine does not, so the state is read without it."""
    def run(argv, cwd=None, input=None, env=None):
        seen = os.environ if env is None else env
        out = status(True, "oauth_token") if "CLAUDE_CODE_OAUTH_TOKEN" in seen else status(False, "none")
        return subprocess.CompletedProcess(argv, 0, out, "")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "made-up")
    m = machine()
    m.run = run
    assert signin.claude_state(m) == "signed-out"


def test_gh_debug_traffic_does_not_reach_the_log(monkeypatch):
    monkeypatch.setenv("GH_DEBUG", "api")
    seen = []
    signin.github_start(machine(), spawn=lambda argv, **kw: seen.append(kw["env"]) or FakeProc(CODE_LINES))
    assert "GH_DEBUG" not in seen[0] and seen[0]["GH_PROMPT_DISABLED"] == "1"


# ----- the order, and a run that is closed while it waits -------------------------------------
def test_the_github_flow_starts_before_claude_is_installed(monkeypatch, capsys):
    events, installed = [], []
    world(monkeypatch, real_rows=True)
    monkeypatch.setattr(signin, "github_start", lambda m, **kw: events.append("github flow") or {
        "code": "4F2A-9C1B", "link": signin.DEVICE_LINK, "pid": 4101})
    monkeypatch.setattr(signin, "claude_start", lambda m, **kw: events.append("claude flow") or {
        "link": AUTHORIZE, "pid": 4102})
    m = machine()
    run = m.run

    def claude_missing_until_installed(argv, **kw):
        if list(argv[:2]) == ["claude", "--version"] and not installed:
            events.append("claude missing")
            return subprocess.CompletedProcess(argv, 127, "", "not found")
        return run(argv, **kw)
    m.run = claude_missing_until_installed

    def install(tool, machine_):
        events.append(f"install {tool}")
        installed.append(tool)
        return Path("tools") / tool, "installed"
    monkeypatch.setattr(start.doctor, "Machine", lambda: m)
    monkeypatch.setattr(start.doctor, "install", install)
    code, result = signins(capsys)
    assert events == ["github flow", "claude missing", "install claude", "claude flow"]
    assert [n["kind"] for n in result["needs"]] == ["github-code", "claude-signin"]


def test_only_the_two_tools_are_made_sure_of_in_the_signins_phase(monkeypatch, capsys):
    """The rest of the doctor's walk is the build's."""
    world(monkeypatch)
    monkeypatch.setattr(start.doctor, "diagnose", lambda **kw: pytest.fail("the whole walk is the build's"))
    assert signins(capsys)[0] == 0


def test_a_run_closed_while_it_waits_does_not_start_a_second_flow_when_run_again(monkeypatch, capsys):
    started = world(monkeypatch)

    def closed(m, **kw):
        raise RuntimeError("the session was closed")
    monkeypatch.setattr(signin, "wait", closed)
    with pytest.raises(RuntimeError):
        start.main(["signins", "--wait", "--json"])
    record = setup_journal.load()["signins"]
    assert record["github_pid"] == 4101 and record["claude_pid"] == 4102   # written when each started
    assert started == ["github", "claude"]
    monkeypatch.setattr(routine, "pid_alive", lambda pid: pid in (4101, 4102))   # both flows still run
    code, result = signins(capsys)
    assert started == ["github", "claude"]   # no second flow, no second browser window
    assert [n["kind"] for n in result["needs"]] == ["github-code", "claude-signin"]
    assert result["needs"][0]["code"] == "4F2A-9C11"   # the code the person was shown


# ----- display names ------------------------------------------------------------------------------
def test_a_display_name_matches_like_a_login_and_the_choices_stay_logins():
    orgs = [{"login": "lw", "name": "Lantern Works"}, {"login": "acme", "name": "Acme Inc"}]
    assert start.pick_namespace("ada", orgs, "Lantern Works", None) == ("lw", [])
    assert start.pick_namespace("ada", orgs, None, "lanternworks.example") == ("lw", [])
    assert start.pick_namespace("ada", orgs, "Beacon Labs", None) == (None, ["lw", "acme", "ada"])
    assert start.pick_namespace("ada", ["acme", {"login": "lw", "name": "Lantern Works"}], "Lantern Works", None) == ("lw", [])
    # the login and the name of one organisation both match: still one organisation
    assert start.pick_namespace("ada", [{"login": "lantern-works", "name": "Lantern Works"}, "other"],
                                "Lantern Works", None) == ("lantern-works", [])
    # two organisations match, one by its login and one by its name: a choice
    two = [{"login": "lantern-works", "name": ""}, {"login": "lw", "name": "Lantern Works"}]
    assert start.pick_namespace("ada", two, "Lantern Works", None) == (None, ["lantern-works", "lw", "ada"])
    # an organisation without a name, or a company without one, matches nothing
    assert start.pick_namespace("ada", [{"login": "x", "name": None}], "", None) == (None, ["x", "ada"])


def test_display_names_are_one_lookup_each_for_ten_organisations_and_a_failed_one_is_its_login(monkeypatch):
    asked = []
    logins = [f"org-{n}" for n in range(12)]

    def out(argv, cwd=None):
        asked.append(argv[2])
        if "memberships" in argv[2]:
            return "\n".join(logins)
        return {"orgs/org-1": "", "orgs/org-2": "null"}.get(argv[2], f"Name of {argv[2][5:]}")
    monkeypatch.setattr(start, "_out", out)
    orgs = start.member_orgs()
    assert [o["login"] for o in orgs] == logins and len(asked) == 1 + start.MAX_ORG_NAMES
    names = [o["name"] for o in orgs]
    assert names[0] == "Name of org-0" and names[1] == "" and names[2] == "" and names[10:] == ["", ""]


def test_an_organisation_named_like_the_company_is_picked_though_its_login_is_not(monkeypatch, capsys):
    real = start.member_orgs
    world(monkeypatch, github=True, claude="account", login="ada-okafor")
    monkeypatch.setattr(start, "member_orgs", real)
    monkeypatch.setattr(start, "_out", lambda argv, cwd=None: "lw\nacme" if "memberships" in argv[2]
                        else {"orgs/lw": "Lantern Works"}.get(argv[2], ""))
    code, result = signins(capsys, "--company", "Lantern Works")
    assert code == 0 and result["state"] == "done" and result["namespace"] == "lw" and result["needs"] == []
