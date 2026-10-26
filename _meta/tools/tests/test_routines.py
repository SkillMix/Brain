"""Routines as OS tasks, so they outlive the session: the OS starts routine.py at each routine's
times, which runs the prompt through `claude -p`. Done only once the schedule reads back."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import offboard
import onboard
import pytest
import routine
import start
from conftest import fake_run, make_space
from test_sync_task import world


def workspace(tmp_path) -> Path:
    ws = make_space(tmp_path / "ada")
    start.render_routines(ws, "Ada Okafor")
    return ws


def test_a_routine_runs_its_prompt_through_claude_headless(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    seen = {}

    def run(argv, **kw):
        seen.update(argv=argv, **kw)
        return subprocess.CompletedProcess(argv, 0, "chain done\n", "")
    prompt = ws / "_meta" / "state" / "routines" / "daily-chain.md"
    assert routine.main([str(prompt)], run=run) == 0
    assert seen["argv"][:6] == ["claude", "-p", "--permission-mode", "acceptEdits", "--permission-prompts", "none"]
    assert seen["argv"][6] == "--settings" and len(seen["argv"]) == 8
    profile = Path(seen["argv"][7])   # the routine profile, written outside every space
    assert profile.parent == tmp_path / "home" / ".spaces" / "routines" and not profile.is_relative_to(ws)
    assert "Edit(//**/_meta/tools/**)" in json.loads(profile.read_text(encoding="utf-8"))["permissions"]["deny"]
    assert Path(seen["cwd"]) == ws and not seen["input"].startswith("---") and "daily chain" in seen["input"]
    log = (tmp_path / "home" / ".spaces" / "logs" / "daily-chain-ada.log").read_text(encoding="utf-8")
    assert "chain done" in log and "exit 0" in log


def test_without_claude_the_routine_says_so_in_its_log(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(routine, "find_claude", lambda: None)
    prompt = ws / "_meta" / "state" / "routines" / "sweep-chain.md"
    assert routine.main([str(prompt)]) == 127
    assert "claude" in (tmp_path / "home" / ".spaces" / "logs" / "sweep-chain-ada.log").read_text(encoding="utf-8")


def test_cron_times_become_daily_times():
    assert onboard.daily_times("30 6 * * *") == [(6, 30)]
    assert onboard.daily_times("0 7,13,19 * * *") == [(7, 0), (13, 0), (19, 0)]
    assert onboard.daily_times("*/15 * * * *") is None


def test_on_windows_each_routine_is_a_hidden_task_at_its_times(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Windows")
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")   # the machine has the CLI
    calls = []
    registered = "".join(onboard.WINDOWS_TASK)   # the read-back of a task as registered
    monkeypatch.setattr(onboard, "run_cmd", world({"schtasks /Query": (0, registered)}, calls))
    ws = workspace(tmp_path)
    report = onboard.Report("person")
    records = onboard.register_routines(report, ws, offline=False, dry_run=False)
    assert [r["name"] for r in records] == ["space-daily-chain-ada", "space-sweep-chain-ada"]
    xmls = {c[0][c[0].index("/TN") + 1]: c[1][c[0][c[0].index("/XML") + 1]].decode("utf-16")
            for c in calls if c[0][:2] == ["schtasks", "/Create"]}
    daily, sweep = xmls["space-daily-chain-ada"], xmls["space-sweep-chain-ada"]
    assert daily.count("<CalendarTrigger>") == 1 and "T06:30:00</StartBoundary>" in daily
    assert sweep.count("<CalendarTrigger>") == 3 and "T19:00:00</StartBoundary>" in sweep
    for xml in (daily, sweep):
        assert "<DisallowStartIfOnBatteries>false" in xml and "<StartWhenAvailable>true" in xml
        assert "routine.py" in xml and "conhost.exe</Command>" in xml
    assert all(s.done for s in report.steps)
    state = onboard.read_yaml(ws / "_meta" / "state" / "onboard.yaml", {})
    assert [t["name"] for t in state["routine_tasks"]] == ["space-daily-chain-ada", "space-sweep-chain-ada"]


def test_with_cron_each_routine_is_one_marked_line(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Darwin")
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")   # the machine has the CLI
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    state = {"lines": "0 9 * * * /usr/bin/true\n"}

    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        if argv[:2] == ["crontab", "-l"]:
            return subprocess.CompletedProcess(argv, 0, state["lines"], "")
        if argv[0] == "crontab":
            state["lines"] = Path(argv[1]).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(onboard, "run_cmd", fake)
    ws = workspace(tmp_path)
    onboard.register_routines(onboard.Report("person"), ws, offline=False, dry_run=False)
    lines = state["lines"].splitlines()
    assert lines[0] == "0 9 * * * /usr/bin/true" and len(lines) == 3
    assert lines[1].startswith("30 6 * * * cd ") and lines[1].endswith("# space-daily-chain-ada")
    assert lines[2].startswith("0 7,13,19 * * * cd ") and "routine.py" in lines[2]
    onboard.register_routines(onboard.Report("person"), ws, offline=False, dry_run=False)
    assert state["lines"].splitlines() == lines   # again: nothing doubles


def test_without_cron_linux_gets_persistent_timers_at_the_routine_times(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")   # the machine has the CLI
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(onboard, "run_cmd", world({"crontab": (127, ""), "systemctl --user": (0, "")}, []))
    ws = workspace(tmp_path)
    onboard.register_routines(onboard.Report("person"), ws, offline=False, dry_run=False)
    timer = (tmp_path / "home" / ".config" / "systemd" / "user" / "space-sweep-chain-ada.timer").read_text()
    assert "OnCalendar=*-*-* 07:00:00" in timer and "OnCalendar=*-*-* 19:00:00" in timer and "Persistent=true" in timer


def test_check_reads_the_routine_tasks_back(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada")
    onboard.write_yaml(root / "_meta" / "state" / "onboard.yaml", {"case": "person", "offline": True,
                       "routine_tasks": [{"name": "space-daily-chain-ada", "kind": "schtasks",
                                          "check": ["schtasks", "/Query", "/TN", "space-daily-chain-ada", "/XML"]}]})
    monkeypatch.setattr(onboard, "run_cmd", world({"schtasks /Query": (0, "<DisallowStartIfOnBatteries>true")}, []))
    row = next(s for s in onboard.check(root) if s.name == "routine task space-daily-chain-ada")
    assert not row.done and "battery" in row.result


def test_offboard_removes_the_routine_tasks_too(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Windows")
    root = make_space(tmp_path / "ada")
    onboard.write_yaml(root / "_meta" / "state" / "onboard.yaml", {
        "case": "person", "sync_task": {"name": "space-sync-ada"},
        "routine_tasks": [{"name": "space-daily-chain-ada"}, {"name": "space-sweep-chain-ada"}]})
    deleted = []

    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        if argv[:2] == ["schtasks", "/Delete"]:
            deleted.append(argv[-1])
        gone = argv[:2] == ["schtasks", "/Query"] and argv[-1] in deleted
        return subprocess.CompletedProcess(argv, 1 if gone else 0, "", "")
    monkeypatch.setattr(offboard, "run_cmd", fake)
    offboard.run(root, online=True, dry_run=False)
    assert deleted == ["space-sync-ada", "space-daily-chain-ada", "space-sweep-chain-ada"]


def test_claude_is_found_where_its_windows_installer_puts_it(tmp_path, monkeypatch):
    """A scheduled task may start without the user's PATH: the installer's own folder still counts,
    with the .exe Windows gives it."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(routine.shutil, "which", lambda name: None)
    exe = tmp_path / "home" / ".local" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    assert routine.find_claude() == str(exe)


def test_claude_is_found_where_the_doctor_installed_it(tmp_path, monkeypatch):
    """The doctor's copy under ~/.spaces/tools counts without being on PATH, as gh's does once
    use_tools has run; the person's own installer is the next place."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(routine.shutil, "which", lambda name: None)
    assert routine.find_claude() is None
    own = tmp_path / "home" / ".local" / "bin" / "claude.exe"
    ours = tmp_path / "home" / ".spaces" / "tools" / "claude" / "claude.exe"
    for exe in (own, ours):
        exe.parent.mkdir(parents=True)
        exe.write_text("", encoding="utf-8")
    assert routine.find_claude() == str(ours)   # first on PATH too once use_tools has run
    ours.unlink()
    assert routine.find_claude() == str(own)


def test_a_routine_does_not_inherit_the_sessions_keys_tokens_or_provider():
    env = {"PATH": "/bin", "ANTHROPIC_API_KEY": "k", "ANTHROPIC_AUTH_TOKEN": "t", "ANTHROPIC_BASE_URL": "https://gateway.example",
           "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1", "CLAUDE_CODE_OAUTH_TOKEN": "o",
           "CLAUDE_CONFIG_DIR": "/home/ada/.claude", "anthropic_api_key": "lower"}
    clean = routine.clean_env(env)
    assert set(clean) == {"PATH", "CLAUDE_CONFIG_DIR"}   # the person's own configuration stays; Windows ignores case
    assert len(env) == 9 and env["ANTHROPIC_API_KEY"] == "k"   # a copy: the caller's environment is not touched


# ----- done only once the schedule reads back ------------------------------------------------------
def test_rendered_routines_are_not_scheduled_routines(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    onboard.write_yaml(ws / "_meta" / "state" / "onboard.yaml", {"case": "person"})
    monkeypatch.setattr(onboard, "run_cmd", world({}, []))
    row = next(s for s in onboard.check(ws) if s.name.startswith("routine"))
    assert row.name == "routines scheduled" and not row.done and not row.needs_owner
    assert row.check.endswith("--os-routines")


def test_a_persistent_harness_task_counts_once_its_read_back_passes(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    onboard.write_yaml(ws / "_meta" / "state" / "onboard.yaml", {"case": "person", "offline": True})
    tasks = [tmp_path / "tasks" / name / "SKILL.md" for name in ("daily", "sweep")]
    checks = [f"test -f {p.as_posix()}" for p in tasks]
    report = onboard.Report("routines")
    assert onboard.record_routine_checks(report, ws, checks[:1]) == []   # one per routine
    assert onboard.record_routine_checks(onboard.Report("routines"), ws, checks) == []   # nothing there yet
    assert onboard.routine_schedule(ws) == []
    for path in tasks:
        path.parent.mkdir(parents=True)
        path.write_text("the routine\n", encoding="utf-8")
    assert len(onboard.record_routine_checks(onboard.Report("routines"), ws, checks)) == 2
    assert [s.done for s in onboard.routine_schedule(ws)] == [True, True]
    tasks[0].unlink()   # the harness lost a task: the check says so
    assert [s.done for s in onboard.routine_schedule(ws)] == [False, True]


# ----- what the command line itself sees ------------------------------------------------------------
def init_line(**more) -> dict:
    init = {"type": "system", "subtype": "init", "cwd": "C:\\ws", "model": "claude-haiku-5-5", "apiKeySource": "none",
            "tools": ["Read", "mcp__claude_ai_Gmail__search_threads"],
            "mcp_servers": [{"name": "claude.ai Gmail", "status": "connected", "source": "claudeai"}]}
    return {**init, **more}


def test_the_probe_lists_the_tools_the_command_line_sees(tmp_path):
    init = {"type": "system", "subtype": "init", "tools": ["Read", "mcp__claude_ai_Gmail__search_threads"], "mcp_servers": [{"name": "claude.ai Gmail", "status": "connected"}], "cwd": str(tmp_path), "model": "claude-opus-5-5"}
    got = routine.probe_tools("claude", tmp_path, run=fake_run(stdout=json.dumps(init) + "\n"))
    assert "mcp__claude_ai_Gmail__search_threads" in got["tools"]


def test_the_probe_finds_init_after_the_hook_lines_and_reads_what_it_says(tmp_path):
    """The person's plugins print hook events first; init is found by what it is, not where."""
    lines = [{"type": "system", "subtype": "hook_started", "hook_name": "SessionStart"},
             {"type": "system", "subtype": "hook_response", "output": "x"},
             init_line(cwd=str(tmp_path), mcp_servers=[
                 {"name": "claude.ai Gmail", "status": "connected", "source": "claudeai"},
                 {"name": "claude.ai Microsoft 365", "status": "needs-auth", "source": "claudeai"}]),
             {"type": "assistant", "message": {"content": [{"type": "text", "text": "OK."}]}},
             {"type": "result", "is_error": False, "result": "OK."}]
    got = routine.probe_tools("claude", tmp_path, run=fake_run(stdout="".join(json.dumps(x) + "\n" for x in lines)))
    assert got == {"tools": ["Read", "mcp__claude_ai_Gmail__search_threads"], "cwd": str(tmp_path),
                   "model": "claude-haiku-5-5", "auth": "none",
                   "mcp_servers": [{"name": "claude.ai Gmail", "status": "connected"},
                                   {"name": "claude.ai Microsoft 365", "status": "needs-auth"}]}


def test_the_probe_names_an_api_key_that_took_over(tmp_path):
    out = json.dumps(init_line(apiKeySource="ANTHROPIC_API_KEY")) + "\n"
    assert routine.probe_tools("claude", tmp_path, run=fake_run(stdout=out))["auth"] == "ANTHROPIC_API_KEY"


def test_the_probe_is_one_cheap_turn_as_a_routine_starts_it(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "secret")
    seen = {}
    folders = [tmp_path / "ada", tmp_path / "brain"]
    routine.probe_tools("claude", tmp_path, folders, run=fake_run(seen, stdout=json.dumps(init_line()) + "\n"))
    argv = seen["argv"]
    assert argv[:2] == ["claude", "-p"] and argv[2:2 + len(routine.REFUSE)] == routine.REFUSE
    assert " ".join(routine.JSON_OUT) in " ".join(argv)
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"] == [str(f) for f in folders]
    assert argv[argv.index("--max-turns") + 1] == "1" and "--no-session-persistence" in argv
    assert seen["input"] == "Reply OK." and "Reply OK." not in argv   # on stdin: a flag with several values would swallow it
    assert seen["cwd"] == str(tmp_path) and seen["timeout"] <= 300
    assert "ANTHROPIC_API_KEY" not in seen["env"] and "CLAUDE_CODE_OAUTH_TOKEN" not in seen["env"]
    seen.clear()
    routine.probe_tools("claude", tmp_path, run=fake_run(seen, stdout=json.dumps(init_line()) + "\n"))
    assert "--add-dir" not in seen["argv"]


def test_a_probe_that_saw_no_init_raises_instead_of_reporting_no_tools(tmp_path):
    """No tools would read as 'no apps connected'; the person would be told to connect them."""
    for out in ("", "Not logged in \u00b7 Please run /login\n", json.dumps({"type": "result", "result": "OK."}) + "\n"):
        with pytest.raises(routine.ProbeError):
            routine.probe_tools("claude", tmp_path, run=fake_run(stdout=out, code=1))


def test_a_probe_that_runs_out_of_time_raises(tmp_path):
    def slow(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"])
    with pytest.raises(routine.ProbeError):
        routine.probe_tools("claude", tmp_path, run=slow)


def test_a_probe_whose_command_will_not_start_raises_the_same_error(tmp_path):
    def missing(argv, **kw):
        raise FileNotFoundError(2, "No such file or directory", argv[0])
    with pytest.raises(routine.ProbeError):
        routine.probe_tools("claude", tmp_path, run=missing)


def test_the_probe_is_written_where_the_machine_keeps_it(tmp_path):
    assert routine.probe_path() == tmp_path / "_home" / ".spaces" / "probe.json"   # through Path.home()


# ----- the command-line facts the routines and readers share ---------------------------------------
def test_the_flags_are_the_ones_the_command_line_was_checked_with():
    assert routine.REFUSE == ["--permission-mode", "acceptEdits", "--permission-prompts", "none"]
    assert routine.JSON_OUT == ["--output-format", "stream-json", "--verbose"]
    assert not hasattr(routine, "CLAUDE")   # `-p` is added where the command is built, with REFUSE


def first(patterns, text):
    return next((m for m in (re.search(p, text) for p in patterns) if m), None)


def test_the_limit_texts_the_command_line_prints_match_with_their_reset_when_there_is_one():
    m = first(routine.LIMIT_PATTERNS, "You\u2019ve hit your session limit \u00b7 resets 11:20pm (America/New_York)")
    assert m.group("limit") == "session" and m.group("resets") == "11:20pm" and m.group("tz") == "America/New_York"
    m = first(routine.LIMIT_PATTERNS, "You've hit your weekly limit \u00b7 resets Oct 27, 1pm (America/New_York)")
    assert m.group("limit") == "weekly" and m.group("resets") == "Oct 27, 1pm"
    m = first(routine.LIMIT_PATTERNS, "You've hit your session limit")
    assert m.groupdict().get("resets") is None and m.group("limit") == "session"
    assert first(routine.LIMIT_PATTERNS, "You've hit your usage limit \u00b7 contact your admin to increase it").group("limit") == "usage"
    assert first(routine.LIMIT_PATTERNS, "A limit is a limit") is None


@pytest.mark.parametrize("text", ["You're now using usage credits", "You\u2019re now using your usage allocation",
                                  "Now using extra usage", "You're out of usage credits \u00b7 resets 3pm (America/New_York)",
                                  "Your org is out of usage \u00b7 add funds to continue",
                                  "You've hit your monthly spend limit \u00b7 raise it at https://x.example",
                                  "You've hit your team\u2019s shared budget \u00b7 ask your admin",
                                  "You've reached your Fable limit.        Fable 5 requires usage credits."])
def test_the_extra_usage_texts_match(text):
    assert first(routine.OVERAGE_PATTERNS, text)


@pytest.mark.parametrize("text", ["Not logged in \u00b7 Please run /login", "Not logged in \u00b7 Run /login",
                                  "OAuth token revoked \u00b7 Please run /login",
                                  "Login expired \u00b7 Run /login to sign in again, or re-authenticate your Anthropic profile",
                                  "Authentication required \u00b7 Sign in again to continue",
                                  "Invalid API key \u00b7 Fix external API key", "Invalid auth token \u00b7 Fix external auth token",
                                  "Your organization has disabled API key authentication \u00b7 Unset ANTHROPIC_API_KEY",
                                  "Your organization has disabled Claude subscription access for Claude Code \u00b7 Use an Anthropic API key",
                                  "Session expired. Please run /login to sign in again.",
                                  "Your session has expired. Please run /login to sign in again."])
def test_the_sign_in_texts_match(text):
    assert first(routine.AUTH_PATTERNS, text)


def test_the_connector_texts_name_the_server_and_the_structured_kinds_map_to_the_two_kinds():
    for text in ('MCP server "claude.ai Gmail" needs you to sign in again (run /mcp to re-authenticate)',
                 'MCP server "claude.ai Gmail" needs to be connected in claude.ai (run /mcp to connect it)',
                 'MCP server "claude.ai Gmail" needs additional permissions (scope: "mail.read") - run /mcp',
                 'The MCP server "claude.ai Gmail" needs you to sign in; run /mcp to authenticate.'):
        assert first(routine.CONNECTOR_PATTERNS, text).group("server") == "claude.ai Gmail"
    assert routine.ERROR_KINDS["authentication_failed"] == "auth" and routine.ERROR_KINDS["rate_limit"] == "limit"


# ----- an isolated run names its OS tasks under a prefix of its own (SPACES_TASK_PREFIX) ---------
def test_the_task_prefix_is_space_unless_the_environment_says_a_prefix_of_ours(monkeypatch):
    monkeypatch.delenv("SPACES_TASK_PREFIX", raising=False)
    assert onboard.task_prefix() == "space-"
    monkeypatch.setenv("SPACES_TASK_PREFIX", "")
    assert onboard.task_prefix() == "space-"
    for prefix in ("space-", "space-test-1a2b-", "space-test-"):
        monkeypatch.setenv("SPACES_TASK_PREFIX", prefix)
        assert onboard.task_prefix() == prefix


@pytest.mark.parametrize("prefix", ["test-", "spaces--", "spaces--x-", "Space-x-", "space-Test-", "space_x-",
                                    "space-x y-", "space-x/", "space-x\n", "xspace-", " space-"])
def test_a_prefix_that_is_not_space_and_lower_case_words_is_refused_in_plain_words(monkeypatch, prefix):
    monkeypatch.setenv("SPACES_TASK_PREFIX", prefix)
    with pytest.raises(ValueError, match=r"SPACES_TASK_PREFIX must start with space-"):
        onboard.task_prefix()


def test_the_routine_tasks_take_the_prefix_and_are_read_back_by_the_name_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Windows")
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    monkeypatch.setenv("SPACES_TASK_PREFIX", "space-test-1a2b-")
    calls = []
    monkeypatch.setattr(onboard, "run_cmd", world({"schtasks /Query": (0, "".join(onboard.WINDOWS_TASK))}, calls))
    ws = workspace(tmp_path)
    records = onboard.register_routines(onboard.Report("person"), ws, offline=False, dry_run=False)
    names = ["space-test-1a2b-daily-chain-ada", "space-test-1a2b-sweep-chain-ada"]
    assert [r["name"] for r in records] == names
    assert [c[0][c[0].index("/TN") + 1] for c in calls if c[0][:2] == ["schtasks", "/Create"]] == names
    # --check finds them by the recorded name, whatever the environment says then
    monkeypatch.delenv("SPACES_TASK_PREFIX")
    queried = []
    monkeypatch.setattr(onboard, "run_cmd", world({"schtasks /Query": (0, "".join(onboard.WINDOWS_TASK))}, queried))
    steps = onboard.routine_schedule(ws)
    assert [s.name for s in steps] == [f"routine task {n}" for n in names] and all(s.done for s in steps)
    assert [q[0][q[0].index("/TN") + 1] for q in queried] == names


def test_the_sync_task_takes_the_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACES_TASK_PREFIX", "space-test-1a2b-")
    root = make_space(tmp_path / "ada")
    record = onboard.register_sync(onboard.Report("person"), root, "ada", 15, offline=True, dry_run=False)
    assert record["name"] == "space-test-1a2b-sync-ada"
    assert "space-test-1a2b-sync-ada" in (root / "_meta" / "state" / "sync-task.txt").read_text(encoding="utf-8")
    monkeypatch.delenv("SPACES_TASK_PREFIX")
    assert onboard.register_sync(onboard.Report("person"), root, "ada", 15, True, False)["name"] == "space-sync-ada"


def test_a_registered_routine_task_records_its_log_name(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Windows")
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    monkeypatch.setenv("SPACES_TASK_PREFIX", "space-test-1a2b-")
    monkeypatch.setattr(onboard, "run_cmd", world({"schtasks /Query": (0, "".join(onboard.WINDOWS_TASK))}, []))
    ws = workspace(tmp_path)
    records = onboard.register_routines(onboard.Report("person"), ws, offline=False, dry_run=False)
    assert [r["log"] for r in records] == ["daily-chain-ada", "sweep-chain-ada"]   # routine.py's <routine>-<space>.log
    state = onboard.read_yaml(ws / "_meta" / "state" / "onboard.yaml", {})
    assert [t["log"] for t in state["routine_tasks"]] == ["daily-chain-ada", "sweep-chain-ada"]


def test_the_run_of_a_task_is_found_by_its_recorded_log_not_by_the_environment(tmp_path, monkeypatch):
    """routine.py keeps `<routine>-<space>.log`; the prefix is no part of it."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    log = tmp_path / "home" / ".spaces" / "logs" / "sweep-chain-ada.log"
    log.parent.mkdir(parents=True)
    log.write_text("\n== 2026-10-14T16:00:00 sweep-chain\nclaude is not installed or not on PATH\nexit 127\n", encoding="utf-8")
    for env in (None, "space-test-1a2b-", "space-other-", "spaces--x-", "not even a prefix"):   # the variable is no input
        if env is None:
            monkeypatch.delenv("SPACES_TASK_PREFIX", raising=False)
        else:
            monkeypatch.setenv("SPACES_TASK_PREFIX", env)
        step = onboard.routine_run_step("space-test-1a2b-sweep-chain-ada", "sweep-chain-ada")
        assert not step.done and "claude is not installed" in step.result, env
    # a record from before the log name was kept: the default prefix, as it was registered
    assert not onboard.routine_run_step("space-sweep-chain-ada").done


@pytest.mark.parametrize("env", [None, "space-other-", "spaces--x-", "not even a prefix"])
def test_check_reads_a_prefixed_routine_back_whatever_the_environment_says(tmp_path, monkeypatch, env):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    root = make_space(tmp_path / "ada")
    log = tmp_path / "home" / ".spaces" / "logs" / "sweep-chain-ada.log"
    log.parent.mkdir(parents=True)
    log.write_text("\n== 2026-10-14T16:00:00 sweep-chain\nclaude is not installed or not on PATH\nexit 127\n", encoding="utf-8")
    onboard.write_yaml(root / "_meta" / "state" / "onboard.yaml", {"case": "person", "offline": True, "routine_tasks": [
        {"name": "space-test-1a2b-sweep-chain-ada", "log": "sweep-chain-ada", "kind": "cron"}]})
    if env is None:
        monkeypatch.delenv("SPACES_TASK_PREFIX", raising=False)
    else:
        monkeypatch.setenv("SPACES_TASK_PREFIX", env)
    row = next(s for s in onboard.check(root) if s.name == "routine space-test-1a2b-sweep-chain-ada runs")
    assert not row.done and "claude is not installed" in row.result
    assert onboard.main([str(root), "--check"]) == 1   # a failing routine fails the check; a bad variable does not break it


@pytest.mark.parametrize("argv", [["--register-sync", "--offline"], ["--os-routines", "--offline"], []])
def test_a_prefix_that_is_not_ours_stops_every_entry_that_registers_before_anything_is_made(tmp_path, monkeypatch, capsys, argv):
    monkeypatch.setenv("SPACES_TASK_PREFIX", "spaces--x-")
    root = make_space(tmp_path / "ada")
    assert onboard.main([str(root), *argv, "--home", str(tmp_path / "made")]) == 1
    out = capsys.readouterr().out
    assert "SPACES_TASK_PREFIX must start with space-" in out and "Traceback" not in out
    assert not (tmp_path / "made").exists() and not (root / "_meta" / "state" / "sync-task.txt").exists()
    assert not (root / "_meta" / "state" / "onboard.yaml").exists()


def test_a_prefix_that_is_not_ours_stops_a_run_before_it_creates_a_space(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACES_TASK_PREFIX", "Space-x-")
    root = make_space(tmp_path / "brain", profile="brain")
    with pytest.raises(ValueError, match="SPACES_TASK_PREFIX must start with space-"):
        onboard.run(root, {"company": "Lantern Works", "owner": "Ada Okafor"}, offline=True, home=tmp_path / "made")
    assert not (tmp_path / "made").exists()


def test_mint_refuses_a_prefix_that_is_not_ours_before_it_makes_the_space(tmp_path, monkeypatch, capsys):
    import mint
    monkeypatch.setenv("SPACES_TASK_PREFIX", "spaces--x-")
    root = make_space(tmp_path / "ada")
    assert mint.main([str(root), "--vault", "--name", "board", "--audience", "Ada Okafor", "--offline",
                      "--home", str(tmp_path / "made")]) == 1
    assert "SPACES_TASK_PREFIX must start with space-" in capsys.readouterr().out
    assert not (tmp_path / "made").exists()
