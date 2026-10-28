"""Routines as OS tasks, so they outlive the session: the OS starts routine.py at each routine's
times, which runs the prompt through `claude -p`. Done only once the schedule reads back."""
import json
import os
import plistlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

import offboard
import onboard
import pytest
import routine
import start
from conftest import fake_run, make_space
from test_sync_task import split_sh, split_windows, unit_command, unit_setting, world


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
        return subprocess.CompletedProcess(argv, 0, stream(result_event("chain done")), "")
    prompt = ws / "_meta" / "state" / "routines" / "daily-chain.md"
    assert routine.main([str(prompt)], run=run) == 0
    assert seen["argv"][:6] == ["claude", "-p", "--permission-mode", "acceptEdits", "--permission-prompts", "none"]
    assert seen["argv"][6:9] == routine.JSON_OUT and seen["argv"][9:18] == ["--tools", *routine.ROUTINE_BUILTINS]
    assert seen["argv"][18] == "--settings" and len(seen["argv"]) == 20
    profile = Path(seen["argv"][19])   # the routine profile, written outside every space
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
    assert [r["name"] for r in records] == ["space-backfill-night-ada", "space-daily-chain-ada", "space-sweep-chain-ada"]
    xmls = {c[0][c[0].index("/TN") + 1]: c[1][c[0][c[0].index("/XML") + 1]].decode("utf-16")
            for c in calls if c[0][:2] == ["schtasks", "/Create"]}
    night, daily, sweep = xmls["space-backfill-night-ada"], xmls["space-daily-chain-ada"], xmls["space-sweep-chain-ada"]
    assert night.count("<CalendarTrigger>") == 1 and "T01:00:00</StartBoundary>" in night
    assert daily.count("<CalendarTrigger>") == 1 and "T06:30:00</StartBoundary>" in daily
    assert sweep.count("<CalendarTrigger>") == 3 and "T19:00:00</StartBoundary>" in sweep
    for xml in (night, daily, sweep):
        assert "<DisallowStartIfOnBatteries>false" in xml and "<StartWhenAvailable>true" in xml
        assert "routine.py" in xml and "conhost.exe</Command>" in xml
    assert all(s.done for s in report.steps)
    state = onboard.read_yaml(ws / "_meta" / "state" / "onboard.yaml", {})
    assert [t["name"] for t in state["routine_tasks"]] == ["space-backfill-night-ada", "space-daily-chain-ada",
                                                            "space-sweep-chain-ada"]


def test_with_cron_each_routine_is_one_marked_line(tmp_path, monkeypatch):
    """Cron is the fallback of a Linux where `systemctl --user` does not work (no systemd, or no user session)."""
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")   # the machine has the CLI
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    state = {"lines": "0 9 * * * /usr/bin/true\n"}

    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        if argv[:2] == ["systemctl", "--user"]:
            return subprocess.CompletedProcess(argv, 1, "", "Failed to connect to bus")
        if argv[:2] == ["crontab", "-l"]:
            return subprocess.CompletedProcess(argv, 0, state["lines"], "")
        if argv[0] == "crontab":
            state["lines"] = Path(argv[1]).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(onboard, "run_cmd", fake)
    ws = workspace(tmp_path)
    report = onboard.Report("person")
    records = onboard.register_routines(report, ws, offline=False, dry_run=False)
    lines = state["lines"].splitlines()
    assert lines[0] == "0 9 * * * /usr/bin/true" and len(lines) == 4
    assert lines[1].startswith("0 1 * * * cd ") and lines[1].endswith("# space-backfill-night-ada")
    assert lines[2].startswith("30 6 * * * cd ") and lines[2].endswith("# space-daily-chain-ada")
    assert lines[3].startswith("0 7,13,19 * * * cd ") and "routine.py" in lines[3]
    # cron does not catch up: one plain line says so, whatever the number of tasks; it blocks nothing
    waiting = [s for s in report.steps if s.name == onboard.CRON_STEP]
    assert len(waiting) == 1 and waiting[0].done and not waiting[0].needs_owner and not report.failed()
    assert waiting[0].result == ("This computer's scheduler does not catch up on runs it missed while the computer was off "
                                 "or asleep, so a routine can skip a day.")   # one sentence: what else it says was not verified
    assert not any(tool in waiting[0].result.lower() for tool in ("cron", "systemd", "systemctl", "crontab"))   # no tool is named
    import onboarding_page
    assert onboarding_page.STAGES[onboarding_page.stage_of(onboard.CRON_STEP)][0] == "Routines"
    assert [r["waiting"] for r in records] == [onboard.CRON_WAITING] * 3
    onboard.register_routines(onboard.Report("person"), ws, offline=False, dry_run=False)
    assert state["lines"].splitlines() == lines   # again: nothing doubles


def test_on_macos_each_routine_is_a_launch_agent_at_its_times(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Darwin")
    monkeypatch.setattr(onboard, "user_id", lambda: 501)
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")   # the machine has the CLI
    monkeypatch.setenv("PATH", "/opt/homebrew/bin:/usr/bin")
    calls = []
    monkeypatch.setattr(onboard, "run_cmd", world({"crontab": (1, "")}, calls))
    ws = workspace(tmp_path)
    report = onboard.Report("person")
    records = onboard.register_routines(report, ws, offline=False, dry_run=False)
    agents = Path.home() / "Library" / "LaunchAgents"
    plist = (agents / "com.spaces.space-sweep-chain-ada.plist").read_text(encoding="utf-8")
    assert plist.count("<key>Hour</key>") == 3 and "routine.py" in plist
    daily = plistlib.loads((agents / "com.spaces.space-daily-chain-ada.plist").read_bytes())
    assert daily["StartCalendarInterval"] == [{"Hour": 6, "Minute": 30}] and daily["WorkingDirectory"] == str(ws)
    assert daily["ProgramArguments"][1:] == [str(ws / "_meta" / "tools" / "routine.py"),
                                              str(ws / "_meta" / "state" / "routines" / "daily-chain.md")]
    assert daily["EnvironmentVariables"]["PATH"] == "/opt/homebrew/bin:/usr/bin"
    assert Path(daily["StandardOutPath"]) == Path.home() / ".spaces" / "logs" / "space-daily-chain-ada.log"
    # a copy loaded before is let go, then the agent is loaded into the person's own session, then read back
    mine = [c[0][1:] for c in calls if c[0][0] == "launchctl" and "sweep-chain" in " ".join(c[0])]
    assert mine == [["bootout", "gui/501/com.spaces.space-sweep-chain-ada"],
                    ["bootstrap", "gui/501", str(agents / "com.spaces.space-sweep-chain-ada.plist")],
                    ["print", "gui/501/com.spaces.space-sweep-chain-ada"]]
    assert all(s.done for s in report.steps) and not any(s.name == onboard.CRON_STEP for s in report.steps)
    assert not [c for c in calls if c[0][0] == "crontab" and c[0][1] != "-l"]   # no cron line on a Mac
    assert [(r["kind"], r["label"], r["log"]) for r in records] == [
        ("launchd", "com.spaces.space-backfill-night-ada", "backfill-night-ada"),
        ("launchd", "com.spaces.space-daily-chain-ada", "daily-chain-ada"),
        ("launchd", "com.spaces.space-sweep-chain-ada", "sweep-chain-ada")]
    state = onboard.read_yaml(ws / "_meta" / "state" / "onboard.yaml", {})
    assert state["routine_tasks"][2]["check"] == ["launchctl", "print", "gui/501/com.spaces.space-sweep-chain-ada"]


def test_a_launch_agent_that_will_not_load_is_a_failed_step_after_three_tries(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Darwin")
    monkeypatch.setattr(onboard, "user_id", lambda: 501)
    slept, calls = [], []
    monkeypatch.setattr(onboard, "_sleep", slept.append)
    monkeypatch.setattr(onboard, "run_cmd", world({"launchctl bootstrap": (5, "")}, calls))   # "Input/output error"
    report = onboard.Report("person")
    onboard.register_task(report, "routine task", "space-sweep-chain-ada", tmp_path / "ada", ["/x/routine.py"], times=[(7, 0)])
    assert [c[0][1] for c in calls if c[0][0] == "launchctl"].count("bootstrap") == 3 and slept == [1, 1]
    assert not report.steps[-1].done and "failed" in report.steps[-1].result
    # the first bootstrap right after a bootout can lose a race with it: a second try is enough
    answers = iter([(5, ""), (0, "")])
    monkeypatch.setattr(onboard, "run_cmd", lambda argv, dry_run=False, cwd=None: subprocess.CompletedProcess(
        argv, *(next(answers) if argv[1] == "bootstrap" else (0, "")), ""))
    report = onboard.Report("person")
    onboard.register_task(report, "routine task", "space-sweep-chain-ada", tmp_path / "ada", ["/x/routine.py"], times=[(7, 0)])
    assert report.steps[-1].done


def test_a_dry_run_on_macos_writes_no_launch_agent(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(onboard, "SYSTEM", "Darwin")
    monkeypatch.setattr(onboard, "user_id", lambda: 501)
    report = onboard.Report("person")
    onboard.register_task(report, "routine task", "space-sweep-chain-ada", tmp_path / "ada", ["/x/routine.py"],
                          times=[(7, 0)], dry_run=True)
    assert "dry-run: launchctl bootstrap gui/501" in capsys.readouterr().out
    assert report.steps[-1].done and not (Path.home() / "Library").exists() and not (Path.home() / ".spaces").exists()


def test_linux_prefers_a_persistent_systemd_timer_over_cron(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")   # the machine has the CLI
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    ws = workspace(tmp_path)
    older = onboard.cron_entry("0 7,13,19 * * *", ws, sys.executable, [ws / "_meta" / "tools" / "routine.py"],
                               "space-sweep-chain-ada", "/bin")   # registered before systemd came first
    state = {"lines": f"0 9 * * * /usr/bin/true\n{older}\n"}
    calls = []

    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        calls.append(argv)
        if argv[:2] == ["crontab", "-l"]:
            return subprocess.CompletedProcess(argv, 0, state["lines"], "")
        if argv[0] == "crontab":
            state["lines"] = Path(argv[1]).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")   # `systemctl --user show-environment` works: a user session
    monkeypatch.setattr(onboard, "run_cmd", fake)
    report = onboard.Report("person")
    records = onboard.register_routines(report, ws, offline=False, dry_run=False)
    units = tmp_path / "home" / ".config" / "systemd" / "user"
    assert "Persistent=true" in (units / "space-daily-chain-ada.timer").read_text(encoding="utf-8")
    sweep = (units / "space-sweep-chain-ada.timer").read_text(encoding="utf-8")
    assert [l for l in sweep.splitlines() if l.startswith("OnCalendar")] == [
        "OnCalendar=*-*-* 07:00:00", "OnCalendar=*-*-* 13:00:00", "OnCalendar=*-*-* 19:00:00"]
    assert ["systemctl", "--user", "show-environment"] in calls
    assert ["systemctl", "--user", "enable", "--now", "space-sweep-chain-ada.timer"] in calls
    # exactly one scheduler: the cron line of the same task is gone, someone else's stays
    assert state["lines"].splitlines() == ["0 9 * * * /usr/bin/true"]
    assert "old cron line removed" in report.steps[2].result and "old cron line" not in report.steps[1].result
    assert all(s.done for s in report.steps) and not any(s.name == onboard.CRON_STEP for s in report.steps)
    assert [r["kind"] for r in records] == ["systemd"] * 3 and not any("waiting" in r for r in records)


def test_a_systemd_timer_that_will_not_start_leaves_the_cron_line_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    older = onboard.cron_entry("0 7 * * *", tmp_path, sys.executable, [tmp_path / "routine.py"], "space-sweep-chain-ada", "/bin")
    state, writes = {"lines": older + "\n"}, []

    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        if argv[:3] == ["systemctl", "--user", "enable"]:
            return subprocess.CompletedProcess(argv, 1, "", "Failed to enable unit")
        if argv[:2] == ["crontab", "-l"]:
            return subprocess.CompletedProcess(argv, 0, state["lines"], "")
        if argv[0] == "crontab":
            writes.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(onboard, "run_cmd", fake)
    report = onboard.Report("person")
    onboard.register_task(report, "routine task", "space-sweep-chain-ada", tmp_path, [tmp_path / "routine.py"], times=[(7, 0)])
    assert not report.steps[-1].done and writes == []   # the schedule that worked is kept until the new one does


def test_check_reads_a_launch_agent_back_with_launchctl(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada")
    ident = "com.spaces.space-daily-chain-ada"
    onboard.write_yaml(root / "_meta" / "state" / "onboard.yaml", {"case": "person", "offline": True, "routine_tasks": [
        {"name": "space-daily-chain-ada", "kind": "launchd", "label": ident, "check": ["launchctl", "print", f"gui/501/{ident}"]}]})
    monkeypatch.setattr(onboard, "run_cmd", world({"launchctl print": (0, "state = not running")}, []))
    assert next(s for s in onboard.check(root) if s.name == "routine task space-daily-chain-ada").done
    monkeypatch.setattr(onboard, "run_cmd", world({"launchctl print": (113, "")}, []))
    row = next(s for s in onboard.check(root) if s.name == "routine task space-daily-chain-ada")
    assert not row.done and f"gui/501/{ident}" in row.check


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


def test_offboard_removes_the_launch_agents_too(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Darwin")
    monkeypatch.setattr(onboard, "user_id", lambda: 501)
    root = make_space(tmp_path / "ada")
    names = ["space-sync-ada", "space-daily-chain-ada", "space-sweep-chain-ada"]
    onboard.write_yaml(root / "_meta" / "state" / "onboard.yaml", {
        "case": "person", "sync_task": {"name": names[0], "kind": "launchd"},
        "routine_tasks": [{"name": n, "kind": "launchd"} for n in names[1:]]})
    agents = Path.home() / "Library" / "LaunchAgents"
    for ident in [*(f"com.spaces.{n}" for n in names), "com.spaces.space-sync-bob", "com.example.other"]:
        onboard.write_text(agents / f"{ident}.plist", "<plist/>")
    loaded = {f"gui/501/com.spaces.{n}" for n in names}
    calls = []

    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        calls.append(argv)
        if argv[:2] == ["launchctl", "bootout"]:
            loaded.discard(argv[2])
        return subprocess.CompletedProcess(argv, 0 if argv[:2] != ["launchctl", "print"] or argv[2] in loaded else 113, "", "")
    monkeypatch.setattr(offboard, "run_cmd", fake)
    report = offboard.run(root, online=True, dry_run=False)
    assert [c for c in calls if c[:2] == ["launchctl", "bootout"]] == [
        ["launchctl", "bootout", f"gui/501/com.spaces.{n}"] for n in names]   # each is let go before its file goes
    assert sorted(p.name for p in agents.glob("*.plist")) == ["com.example.other.plist", "com.spaces.space-sync-bob.plist"]   # no one else's
    assert not [c for c in calls if c[0] == "crontab" and c[1] != "-l"]
    assert all(s.done for s in report.steps if s.name.endswith("removed")) and not loaded


def test_offboard_removes_a_systemd_timer_and_its_two_unit_files(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    root = make_space(tmp_path / "ada")
    units = Path.home() / ".config" / "systemd" / "user"
    for suffix in ("timer", "service"):
        onboard.write_text(units / f"space-sync-ada.{suffix}", "[Unit]\n")
    state = {"enabled": True}

    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        if argv[:3] == ["systemctl", "--user", "disable"]:
            state["enabled"] = False
        return subprocess.CompletedProcess(argv, 0 if argv[2] != "is-enabled" or state["enabled"] else 1, "", "")
    monkeypatch.setattr(offboard, "run_cmd", fake)
    report = onboard.Report("offboard")
    offboard.remove_sync_task(report, root, {}, "space-sync-ada", False)
    assert report.steps[-1].done and not list(units.glob("*"))


# ----- every scheduler hands the program exactly the arguments it was given ---------------------------
FOLDERS = ["José Pérez", "Ada & Co's 100% $HOME `id`",
           pytest.param('say "hi" back\\slash (a;b)', marks=pytest.mark.skipif(os.name == "nt", reason="Windows allows no quote in a name"))]
PATH_VALUE = "/opt/homebrew/bin:/opt/my bin:/opt/100%/bin:/usr/bin"


def argv_of_registered_task(system, root, monkeypatch) -> tuple:
    """Register one routine as `system` does, then read back, from the text its scheduler was given alone,
    what that scheduler would run: (argv, working folder or None, PATH or None)."""
    kind, _, flavour = system.partition(" ")
    monkeypatch.setattr(onboard, "SYSTEM", kind)
    monkeypatch.setattr(onboard, "user_id", lambda: 501)
    monkeypatch.setenv("PATH", PATH_VALUE)
    calls, crontab = [], {"text": ""}
    outside = world({"systemctl --user show-environment": (0 if flavour == "systemd" else 1, ""),
                     "schtasks /Query": (0, "".join(onboard.WINDOWS_TASK))}, calls)

    def fake(argv, dry_run=False, cwd=None):   # the outside, with a crontab that keeps what it is given
        done = outside(argv, dry_run, cwd)
        if [str(a) for a in argv][:2] == ["crontab", "-l"]:
            return subprocess.CompletedProcess(argv, 0, crontab["text"], "")
        if str(argv[0]) == "crontab":
            crontab["text"] = Path(argv[1]).read_text(encoding="utf-8")
        return done
    monkeypatch.setattr(onboard, "run_cmd", fake)
    script, prompt = root / "_meta" / "tools" / "routine.py", root / "_meta" / "state" / "routines" / "sweep-chain.md"
    report = onboard.Report("person")
    onboard.register_task(report, "routine task", "space-sweep-chain-ada", root, [script, prompt],
                          times=[(7, 0), (13, 0), (19, 0)], cron="0 7,13,19 * * *")
    assert report.steps[0].done, report.steps[0].result
    home = Path.home()
    if kind == "Windows":
        _, files = next(c for c in calls if c[0][:2] == ["schtasks", "/Create"])
        task = ET.fromstring(next(iter(files.values())))   # UTF-16, as the file is read
        action = task.find(".//{*}Exec")
        assert action.find("{*}Command").text.lower().endswith("conhost.exe")
        words = split_windows(action.find("{*}Arguments").text)
        assert words[0] == "--headless"
        return words[1:], None, None
    if kind == "Darwin":
        agent = plistlib.loads((home / "Library" / "LaunchAgents" / "com.spaces.space-sweep-chain-ada.plist").read_bytes())
        return agent["ProgramArguments"], agent["WorkingDirectory"], agent["EnvironmentVariables"]["PATH"]
    if flavour == "systemd":
        service = (home / ".config" / "systemd" / "user" / "space-sweep-chain-ada.service").read_text(encoding="utf-8")
        (path,) = unit_setting(service, "Environment")
        return unit_command(service), unit_setting(service, "WorkingDirectory")[0], path.removeprefix("PATH=")
    _, files = next(c for c in calls if c[0][0] == "crontab" and c[0][1] != "-l")
    line = next(iter(files.values())).decode("utf-8").splitlines()[-1]
    words = split_sh(line.split(None, 5)[5].replace("\\%", "%"))   # cron turns \% back into %
    assert words[0] == "cd" and words[2] == "&&" and words[3].startswith("PATH=")
    assert words[-3:] == [">>", "$HOME/.spaces/logs/space-sweep-chain-ada.log", "2>&1"]
    return words[4:-3], words[1], words[3].removeprefix("PATH=")


@pytest.mark.parametrize("folder", FOLDERS)
@pytest.mark.parametrize("system", ["Windows", "Darwin", "Linux systemd", "Linux cron"])
def test_paths_with_spaces_and_accents_survive_every_scheduler(tmp_path, monkeypatch, system, folder):
    if system == "Windows" and '"' in folder:   # on any machine: a Windows path never holds one (`onboard._argline`)
        pytest.skip("Windows allows no quote in a name, so its scheduler never meets one")
    root = tmp_path / folder / "spaces" / "ada-okafor-workspace"
    argv, cwd, path = argv_of_registered_task(system, root, monkeypatch)
    assert argv == [sys.executable, str(root / "_meta" / "tools" / "routine.py"),
                    str(root / "_meta" / "state" / "routines" / "sweep-chain.md")]
    if not system.startswith("Windows"):   # a Windows task starts where the program says, by absolute paths
        assert cwd == str(root) and path == PATH_VALUE


def test_a_unit_file_escapes_what_systemd_would_read_as_its_own(tmp_path, monkeypatch):
    """% is a specifier and $ a variable in ExecStart=; a backslash and a quote escape inside quotes."""
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    monkeypatch.setattr(onboard, "run_cmd", world({}, []))
    root = tmp_path / "100% $HOME \\ \"q\""
    onboard.register_task(onboard.Report("person"), "routine task", "space-sweep-chain-ada", root, [root / "r.py"], times=[(7, 0)])
    service = (Path.home() / ".config" / "systemd" / "user" / "space-sweep-chain-ada.service").read_text(encoding="utf-8")
    exec_start = next(l for l in service.splitlines() if l.startswith("ExecStart="))
    assert "%%" in exec_start and "$$HOME" in exec_start and '\\"q\\"' in exec_start and "\\\\" in exec_start
    assert next(l for l in service.splitlines() if l.startswith("WorkingDirectory=")) == "WorkingDirectory=" + str(root).replace("%", "%%")


def test_systemd_reads_the_program_word_differently_from_the_arguments():
    """Found with `systemd-analyze --user verify` (systemd 255): the program word is no place for `$$`, and a
    program path holding a quote or a backslash is a fatal "special characters" error."""
    args, calendars = ["/home/ada/ws/routine.py", "/home/ada/100% ws/p.md", "--flag"], ["*:0/15"]
    service, _ = onboard.systemd_units("space-x", "/home/ada/ws", "/home/Ada $HOME/100% bin/python3", args, "/usr/bin", calendars)
    assert 'ExecStart="/home/Ada $HOME/100%% bin/python3" "/home/ada/ws/routine.py" "/home/ada/100%% ws/p.md" --flag\n' in service
    for program in ("/home/Sean O'Brien/py", '/home/q"/py', "/home/back\\slash/py"):
        service, _ = onboard.systemd_units("space-x", "/home/ada/ws", program, args, "/usr/bin", calendars)
        assert unit_setting(service, "ExecStart") == ["/bin/sh", "-c", 'exec "$0" "$@"', program, *args]   # sh makes it an argument
        assert next(l for l in service.splitlines() if l.startswith("ExecStart=")).startswith('ExecStart=/bin/sh -c "exec \\"$$0\\" \\"$$@\\"" "')


def test_the_windows_arguments_survive_an_ampersand_an_apostrophe_and_angle_brackets(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Windows")
    calls = []
    monkeypatch.setattr(onboard, "run_cmd", world({"schtasks /Query": (0, "".join(onboard.WINDOWS_TASK))}, calls))
    root = tmp_path / "Ada & Co's <100%> José"
    report = onboard.Report("person")
    onboard.register_task(report, "routine task", "space-sweep-chain-ada", root, [root / "r.py"], times=[(7, 0)])
    _, files = next(c for c in calls if c[0][:2] == ["schtasks", "/Create"])
    raw = next(iter(files.values()))
    assert raw.startswith((b"\xff\xfe", b"\xfe\xff")) and report.steps[-1].done   # UTF-16 with its mark
    text = raw.decode("utf-16")
    assert "&amp;" in text and "&lt;100%&gt;" in text and "Ada & Co" not in text   # well-formed XML
    assert split_windows(ET.fromstring(raw).find(".//{*}Arguments").text)[-1] == str(root / "r.py")


def test_a_folder_name_with_a_line_break_cannot_be_a_cron_line(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    calls = []
    monkeypatch.setattr(onboard, "run_cmd", world({"systemctl --user": (1, "")}, calls))
    report = onboard.Report("person")
    onboard.register_task(report, "routine task", "space-sweep-chain-ada", tmp_path / "a\nb", [tmp_path / "r.py"], times=[(7, 0)])
    assert not report.steps[-1].done and "line break" in report.steps[-1].result
    assert not [c for c in calls if c[0][0] == "crontab" and c[0][1] != "-l"]


# ----- run a task now ------------------------------------------------------------------------------------
def test_run_now_starts_the_task_on_each_scheduler(monkeypatch):
    calls = []
    monkeypatch.setattr(onboard, "run_cmd", world({}, calls))
    monkeypatch.setattr(onboard, "user_id", lambda: 501)
    assert onboard.run_now({"kind": "schtasks", "name": "space-sweep-chain-ada"})
    assert calls[-1][0] == ["schtasks", "/Run", "/TN", "space-sweep-chain-ada"]
    assert onboard.run_now({"kind": "launchd", "name": "space-sweep-chain-ada", "label": "com.spaces.space-sweep-chain-ada"})
    assert calls[-1][0] == ["launchctl", "kickstart", "gui/501/com.spaces.space-sweep-chain-ada"]
    assert onboard.run_now({"kind": "launchd", "name": "space-daily-chain-ada"})   # a record without its label
    assert calls[-1][0] == ["launchctl", "kickstart", "gui/501/com.spaces.space-daily-chain-ada"]
    # a routine runs for up to an hour: do not wait for it (a oneshot service's `start` would)
    assert onboard.run_now({"kind": "systemd", "name": "space-sweep-chain-ada"})
    assert calls[-1][0] == ["systemctl", "--user", "start", "--no-block", "space-sweep-chain-ada.service"]


def cron_record(root, task="space-sweep-chain-ada", py="/usr/bin/python3", args=None, path="/usr/bin:/bin", when="0 7 * * *"):
    """What register_task records for a cron task: the line and the fields it was built from."""
    args = [str(root / "r.py")] if args is None else args
    return {"name": task, "kind": "cron", "root": str(root), "python": py, "args": args, "path": path, "when": when,
            "line": onboard.cron_entry(when, root, py, args, task, path)}


def test_run_now_starts_the_crons_command_detached_the_way_cron_would(monkeypatch):
    started = []
    monkeypatch.setattr(onboard, "spawn_cmd", lambda argv, dry_run=False, env=None: started.append((argv, env)) or 4242)
    root = PurePosixPath("/home/ada/100% ada")
    record = cron_record(root)
    assert onboard.run_now(record)
    argv, env = started[-1]
    assert argv[:2] == ["sh", "-c"] and argv[2].startswith(f'cd "{root}" && PATH="/usr/bin:/bin" "/usr/bin/python3"')
    assert ">> \"$HOME/.spaces/logs/space-sweep-chain-ada.log\" 2>&1" in argv[2]   # the log the line itself writes
    assert "\\%" not in argv[2] and "100% ada" in argv[2]   # cron's escape is cron's: sh gets the plain %
    assert env["HOME"] == str(Path.home()) and set(env) <= {"HOME", "LOGNAME", "SHELL", "PATH"}   # as cron starts it, not as the session has it
    monkeypatch.setattr(onboard, "spawn_cmd", lambda *a, **k: None)   # the shell would not start
    assert not onboard.run_now(record)


def test_run_now_runs_a_registered_cron_task_and_never_text_that_is_not_what_was_registered(tmp_path, monkeypatch):
    """The record is read from a file a merge can change: the command is rebuilt from its fields, and a line
    that is not exactly that command is refused."""
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    monkeypatch.setattr(onboard, "run_cmd", world({"systemctl --user": (1, "")}, []))
    started = []
    monkeypatch.setattr(onboard, "spawn_cmd", lambda argv, dry_run=False, env=None: started.append(argv) or 1)
    record = onboard.register_task(onboard.Report("person"), "routine task", "space-sweep-chain-ada", tmp_path / "ada",
                                   [tmp_path / "ada" / "r.py"], times=[(7, 0)], cron="0 7 * * *")
    assert [record[k] for k in ("python", "args", "when", "root")] == [
        sys.executable, [str(tmp_path / "ada" / "r.py")], "0 7 * * *", str(tmp_path / "ada")] and record["path"] == os.environ["PATH"]
    onboard.write_yaml(tmp_path / "r.yaml", record)   # as onboard.yaml keeps it, and reads it back
    assert onboard.run_now(onboard.read_yaml(tmp_path / "r.yaml")) and started[-1][:2] == ["sh", "-c"]
    started.clear()
    for tampered in (dict(record, line=record["line"].replace("# space-", "; touch /tmp/PWNED # space-")),
                     dict(record, line="0 7 * * * touch /tmp/PWNED  # space-sweep-chain-ada"),
                     dict(record, args=["-c", "touch /tmp/PWNED"]),               # fields changed, the line not
                     dict(record, python="/bin/sh"), dict(record, when="* * * * *"), dict(record, root="/tmp"),
                     {k: v for k, v in record.items() if k != "args"},        # a record from before the fields were kept
                     {k: v for k, v in record.items() if k != "line"}):
        assert not onboard.run_now(tampered), tampered
    assert started == []


def test_run_now_is_false_when_the_scheduler_says_no_or_does_not_know_the_task(monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", world({"schtasks": (1, ""), "launchctl": (113, ""), "systemctl": (5, "")}, []))
    monkeypatch.setattr(onboard, "user_id", lambda: 501)
    for kind in ("schtasks", "launchd", "systemd"):
        assert not onboard.run_now({"kind": kind, "name": "space-sweep-chain-ada"})
    assert not onboard.run_now({"kind": "carrier-pigeon", "name": "x"}) and not onboard.run_now({})
    assert not onboard.run_now({"kind": "cron", "name": "x", "line": None}) and not onboard.run_now({"kind": "cron", "name": "x"})


def test_a_dry_run_of_run_now_says_what_it_would_do_and_starts_nothing(monkeypatch, capsys):
    monkeypatch.setattr(onboard, "user_id", lambda: 501)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("started a process in a dry run"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("started a process in a dry run"))
    cron = cron_record(PurePosixPath("/ada"), task="space-x")
    for record in ({"kind": "schtasks", "name": "space-x"}, {"kind": "launchd", "name": "space-x"}, {"kind": "systemd", "name": "space-x"},
                   cron):
        assert onboard.run_now(record, dry_run=True)
    out = capsys.readouterr().out
    assert out.count("dry-run:") == 4 and "schtasks /Run /TN space-x" in out and "launchctl kickstart gui/501/com.spaces.space-x" in out


@pytest.mark.skipif(os.name == "nt", reason="a detached shell, as cron has")
def test_spawn_cmd_starts_a_command_in_a_session_of_its_own(tmp_path):
    pid = onboard.spawn_cmd(["sh", "-c", "sleep 1"], env={"PATH": os.environ["PATH"]})
    assert isinstance(pid, int)
    assert os.getsid(pid) == pid and os.getsid(pid) != os.getsid(0)   # a session leader: it outlives the process that asked
    os.waitpid(pid, 0)
    assert onboard.spawn_cmd(["/no/such/program-for-spawn-test"]) is None


# ----- fix round 1: exactly one scheduler per task, nothing of anyone else's touched ------------------------
class Outside:
    """The schedulers of a machine as far as a registration touches them, with state: a crontab that keeps
    what it is given, launch agents that are loaded, systemd timers that are enabled. Every call is kept.
    `fail` names commands (by their first words) that exit 1 without changing anything."""

    def __init__(self, systemd=True, crontab="", fail=(), crontab_error=None):
        self.calls, self.systemd, self.crontab, self.fail = [], systemd, crontab, tuple(fail)
        self.crontab_error = crontab_error   # `crontab -l` fails with this message, whatever the crontab holds
        self.loaded, self.enabled = set(), set()

    def __call__(self, argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        self.calls.append(argv)

        def done(code=0, out=""):
            return subprocess.CompletedProcess(argv, code, out, "" if code == 0 else "failed")
        if any(" ".join(argv).startswith(prefix) for prefix in self.fail):
            return done(1)
        if argv[:2] == ["schtasks", "/Query"]:
            return done(0, "".join(onboard.WINDOWS_TASK))   # a task as registered
        if argv[:2] == ["crontab", "-l"] and self.crontab_error:
            return subprocess.CompletedProcess(argv, 1, "", self.crontab_error)
        if argv[:2] == ["crontab", "-l"] and self.crontab is None:
            return subprocess.CompletedProcess(argv, 1, "", "crontab: no crontab for ada")
        if argv[:2] == ["crontab", "-l"]:
            return done(0, self.crontab)
        if argv[0] == "crontab":
            self.crontab = Path(argv[1]).read_text(encoding="utf-8")
        elif argv[:3] == ["systemctl", "--user", "show-environment"]:
            return done(0 if self.systemd else 1)
        elif argv[:3] == ["systemctl", "--user", "enable"]:
            self.enabled.add(argv[-1])
        elif argv[:3] == ["systemctl", "--user", "disable"]:
            self.enabled.discard(argv[-1])
        elif argv[:3] in (["systemctl", "--user", "is-active"], ["systemctl", "--user", "is-enabled"]):
            return done(0 if argv[-1] in self.enabled else 1)
        elif argv[:2] == ["launchctl", "bootstrap"]:
            self.loaded.add(f"{argv[2]}/{Path(argv[3]).stem}")
        elif argv[:2] == ["launchctl", "bootout"]:
            was_loaded = argv[2] in self.loaded
            self.loaded.discard(argv[2])
            return done(0 if was_loaded else 113)
        elif argv[:2] == ["launchctl", "print"]:
            return done(0 if argv[2] in self.loaded else 113)
        return done()

    def ran(self, *words):
        """The calls that begin with these words."""
        return [c for c in self.calls if c[:len(words)] == list(words)]

    def wrote_crontab(self):
        return [c for c in self.calls if c[0] == "crontab" and c[1] != "-l"]


TASK = "space-sweep-chain-ada"


def register_sweep(root):
    report = onboard.Report("person")
    record = onboard.register_task(report, "routine task", TASK, root, [Path(root) / "r.py"], times=[(7, 0)], cron="0 7 * * *")
    return report, record


def test_is_task_line_is_the_marker_or_an_exact_legacy_sync_line(tmp_path):
    ws, two = tmp_path / "ws", tmp_path / "ws-two"
    sync = lambda root: f"*/15 * * * * /usr/bin/python3 {root}/_meta/tools/sync.py --root {root} --release-leg"
    marked = onboard.cron_line(15, "/py", ws / "_meta" / "tools" / "sync.py", ws, "space-sync-ws", "/bin")
    assert onboard.is_task_line(marked, "space-sync-ws", ws)
    assert onboard.is_task_line(sync(ws), "space-sync-ws", ws)                                   # written before the marker existed
    assert not onboard.is_task_line(sync(two), "space-sync-ws", ws)                              # a root that shares a prefix
    assert not onboard.is_task_line(marked.replace("space-sync-ws", "space-sync-two"), "space-sync-ws", ws)   # another task's marker
    two_marked = onboard.cron_line(15, "/py", two / "_meta" / "tools" / "sync.py", two, "space-sync-two", "/bin")
    assert not onboard.is_task_line(two_marked, "space-sync-ws", ws)
    hand = f"0 3 * * * cd {ws} && python3 {ws}/_meta/tools/sync.py --root {ws} --dry-run"
    assert not onboard.is_task_line(hand, "space-sync-ws", ws)                                   # the person's own line
    assert not onboard.is_task_line(sync(ws) + " --dry-run", "space-sync-ws", ws)
    assert not onboard.is_task_line(sync(ws), "space-sweep-chain-ws", ws)                       # the legacy shape is the sync task's only


def test_moving_a_task_off_cron_removes_its_own_line_and_no_one_elses(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    ws, two = tmp_path / "ws", tmp_path / "ws-two"
    mine = onboard.cron_entry("0 7 * * *", ws, sys.executable, [ws / "r.py"], TASK, "/bin")
    keep = ["0 9 * * * /usr/bin/true",
            onboard.cron_line(15, "/py", two / "_meta" / "tools" / "sync.py", two, "space-sync-ws-two", "/bin"),   # a sibling space's
            onboard.cron_entry("0 7 * * *", two, sys.executable, [two / "r.py"], TASK, "/bin"),   # the same marker, another folder
            f"0 3 * * * cd {ws} && python3 {ws}/_meta/tools/sync.py --root {ws} --dry-run",       # the person's, by hand
            f"*/15 * * * * /py {ws}/_meta/tools/sync.py --root {ws} --release-leg"]               # no marker: not provably this task's
    outside = Outside(crontab="\n".join([keep[0], mine, *keep[1:]]) + "\n")
    monkeypatch.setattr(onboard, "run_cmd", outside)
    report, record = register_sweep(ws)
    assert record["kind"] == "systemd" and outside.crontab.splitlines() == keep
    assert "old cron line removed" in report.steps[-1].result and report.steps[-1].done
    # a line marked for this task that is not provably this folder's stays, and the step says so (it may be a copy)
    after = report.steps[-1].result.partition("after sleep)")[2].replace("its old cron line removed", "")
    assert report.steps[-1].done and "starts in a different folder" in after and no_tool_named(after)


def test_a_cron_registration_replaces_only_its_own_line(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    ws, two = tmp_path / "ws", tmp_path / "ws-two"
    sibling = onboard.cron_line(15, "/py", two / "_meta" / "tools" / "sync.py", two, "space-sync-ws-two", "/bin")
    hand = f"0 3 * * * cd {ws} && python3 {ws}/_meta/tools/sync.py --root {ws} --dry-run"
    older = f"*/15 * * * * /py {ws}/_meta/tools/sync.py --root {ws} --release-leg"
    outside = Outside(systemd=False, crontab="\n".join([sibling, hand, older]) + "\n")
    monkeypatch.setattr(onboard, "run_cmd", outside)
    record = onboard.register_sync(onboard.Report("person"), ws, "ws", 15, offline=False, dry_run=False)
    assert outside.crontab.splitlines() == [sibling, hand, record["line"]]   # its pre-marker line is replaced, theirs stay


def test_the_crontab_is_written_by_one_helper(tmp_path):
    seen = []
    run = lambda argv, dry_run=False: seen.append((argv, dry_run, Path(argv[1]).read_text(encoding="utf-8"))) or subprocess.CompletedProcess(argv, 0, "", "")
    onboard.write_crontab(["0 9 * * * a", "0 10 * * * b"], run=run)
    onboard.write_crontab([], dry_run=True, run=run)
    assert [(s[1], s[2]) for s in seen] == [(False, "0 9 * * * a\n0 10 * * * b\n"), (True, "")]
    assert not Path(seen[0][0][1]).exists()   # the file it handed to crontab is gone


def test_an_old_cron_line_that_cannot_be_removed_is_a_step_the_person_sees(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    older = onboard.cron_entry("0 7 * * *", tmp_path, sys.executable, [tmp_path / "r.py"], TASK, "/bin")
    outside = Outside(crontab=older + "\n")

    def fake(argv, dry_run=False, cwd=None):
        refuse = [str(a) for a in argv][0] == "crontab" and str(argv[1]) != "-l"   # the terminal may not edit the crontab
        return subprocess.CompletedProcess(argv, 1, "", "permission denied") if refuse else outside(argv, dry_run, cwd)
    monkeypatch.setattr(onboard, "run_cmd", fake)
    report, _ = register_sweep(tmp_path)
    row = report.steps[-1]
    assert not row.done and row.needs_owner and "could not be removed" in row.result and no_tool_named(row.result)
    assert "permission denied" in row.result and TASK in row.result   # the reason, and the note that finds the job


def test_a_fallback_to_cron_takes_the_tasks_timer_out_first(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    units = Path.home() / ".config" / "systemd" / "user"
    for suffix in ("timer", "service"):
        onboard.write_text(units / f"{TASK}.{suffix}", "[Unit]\n")
    onboard.write_text(units / "space-other.timer", "[Unit]\n")
    outside = Outside(systemd=False)   # no user session today
    outside.enabled.add(f"{TASK}.timer")
    monkeypatch.setattr(onboard, "run_cmd", outside)
    report, record = register_sweep(tmp_path)
    assert record["kind"] == "cron" and [l for l in outside.crontab.splitlines() if l.endswith(f"# {TASK}")]
    assert not outside.enabled and sorted(p.name for p in units.glob("*")) == ["space-other.timer"]   # not theirs
    assert outside.ran("systemctl", "--user", "disable", "--now", f"{TASK}.timer") and all(s.done for s in report.steps)


def test_a_timer_that_cannot_be_stopped_keeps_cron_out(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    units = Path.home() / ".config" / "systemd" / "user"
    for suffix in ("timer", "service"):
        onboard.write_text(units / f"{TASK}.{suffix}", "[Unit]\n")
    outside = Outside(systemd=False, fail=("systemctl --user disable",))   # "Failed to connect to bus"
    outside.enabled.add(f"{TASK}.timer")
    monkeypatch.setattr(onboard, "run_cmd", outside)
    report, _ = register_sweep(tmp_path)
    row = report.steps[-1]
    assert not row.done and row.needs_owner and "could not be stopped" in row.result and no_tool_named(row.result)
    assert outside.wrote_crontab() == [] and (units / f"{TASK}.timer").exists()   # the timer is still the one scheduler


def test_a_fallback_to_cron_lets_go_of_a_launch_agent_of_the_task(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    monkeypatch.setattr(onboard, "user_id", lambda: 501)
    plist = Path.home() / "Library" / "LaunchAgents" / f"com.spaces.{TASK}.plist"
    onboard.write_text(plist, "<plist/>")
    onboard.write_text(plist.with_name("com.spaces.space-other.plist"), "<plist/>")
    outside = Outside(systemd=False)
    outside.loaded.add(f"gui/501/com.spaces.{TASK}")
    monkeypatch.setattr(onboard, "run_cmd", outside)
    report, record = register_sweep(tmp_path)
    assert record["kind"] == "cron" and not outside.loaded and all(s.done for s in report.steps)
    assert sorted(p.name for p in plist.parent.glob("*")) == ["com.spaces.space-other.plist"]


def test_registering_a_launch_agent_again_leaves_a_running_job_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Darwin")
    monkeypatch.setattr(onboard, "user_id", lambda: 501)
    monkeypatch.setenv("PATH", "/opt/homebrew/bin:/usr/bin")
    outside = Outside()
    monkeypatch.setattr(onboard, "run_cmd", outside)
    register_sweep(tmp_path)
    assert [c[1] for c in outside.ran("launchctl")] == ["bootout", "bootstrap", "print"]
    outside.calls.clear()
    report, record = register_sweep(tmp_path)   # the same plist, still loaded: a job that is mid-run is not booted out
    assert [c[1] for c in outside.ran("launchctl")] == ["print"] and report.steps[-1].done
    assert record["kind"] == "launchd" and record["check"][1] == "print"
    outside.calls.clear()
    monkeypatch.setenv("PATH", "/usr/bin")      # another PATH is another plist: loaded again
    register_sweep(tmp_path)
    assert [c[1] for c in outside.ran("launchctl")] == ["bootout", "bootstrap", "print"]
    outside.calls.clear()
    outside.loaded.clear()                      # the same plist but no longer loaded (a logout): loaded again
    report, _ = register_sweep(tmp_path)
    assert [c[1] for c in outside.ran("launchctl")][-2:] == ["bootstrap", "print"] and report.steps[-1].done


def test_registering_a_timer_again_does_not_restart_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    outside = Outside()
    monkeypatch.setattr(onboard, "run_cmd", outside)
    register_sweep(tmp_path)
    assert outside.ran("systemctl", "--user", "daemon-reload") and outside.ran("systemctl", "--user", "enable", "--now", f"{TASK}.timer")
    outside.calls.clear()
    report, _ = register_sweep(tmp_path)   # the same units, the timer active: nothing is told to start, stop or reload
    assert [c[2] for c in outside.ran("systemctl")] == ["show-environment", "is-active"] and report.steps[-1].done
    outside.calls.clear()
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")   # other units: written and enabled again
    register_sweep(tmp_path)
    assert outside.ran("systemctl", "--user", "daemon-reload") and outside.ran("systemctl", "--user", "enable", "--now", f"{TASK}.timer")
    outside.calls.clear()
    outside.enabled.clear()                                  # the same units but the timer is off: enabled again
    register_sweep(tmp_path)
    assert outside.ran("systemctl", "--user", "enable", "--now", f"{TASK}.timer")


def test_a_windows_folder_with_a_word_between_percent_signs_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Windows")
    monkeypatch.setenv("ADA_PROJECTS", "D:\\work")
    monkeypatch.delenv("NOT_A_SET_VARIABLE_9", raising=False)
    outside = Outside()
    monkeypatch.setattr(onboard, "run_cmd", outside)
    for folder in ("x%ADA_PROJECTS%y", "Ada's %ADA_PROJECTS% co", "%NOT_A_SET_VARIABLE_9%ADA_PROJECTS%", "%ada_projects%"):
        outside.calls.clear()
        report, record = register_sweep(tmp_path / folder)
        row = report.steps[-1]
        assert not row.done and "percent signs" in row.result and "rename" in row.result, folder
        assert not outside.ran("schtasks", "/Create") and record["kind"] == "schtasks"
    for folder in ("100%", "50% off, 100%", "%NOT_A_SET_VARIABLE_9%", "Ada $ADA_PROJECTS", "a%b"):   # nothing here is swapped
        report, _ = register_sweep(tmp_path / folder)
        assert report.steps[-1].done, folder


@pytest.mark.parametrize("what", ["root", "path", "python"])
def test_systemd_refuses_a_control_character_in_plain_words(tmp_path, monkeypatch, what):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    outside = Outside()
    monkeypatch.setattr(onboard, "run_cmd", outside)
    if what == "path":
        monkeypatch.setenv("PATH", "/usr/bin:/a\tb")
    elif what == "python":
        monkeypatch.setattr(sys, "executable", "/usr/bin/py\tthon")
    report, _ = register_sweep(tmp_path / ("a\tb" if what == "root" else "ada"))
    row = report.steps[-1]
    assert not row.done and "control character" in row.result and no_tool_named(row.result)
    assert {"root": "the folder name", "path": "the list of folders this computer searches for programs",
            "python": "the location of the Python program"}[what] in row.result
    assert not (Path.home() / ".config" / "systemd" / "user" / f"{TASK}.service").exists()
    assert not outside.ran("systemctl", "--user", "enable") and outside.wrote_crontab() == []


def test_what_a_unit_file_cannot_carry_is_named(tmp_path):
    trouble = onboard.unit_trouble
    assert "backslash" in trouble("/home/ada/ws\\", "/usr/bin/python3", "/usr/bin", [])
    assert "control character" in trouble("/home/ada/ws", "/usr/bin/python3", "/usr/bin", ["/home/ada/\x7fx"])   # DEL too
    assert "name of a file" in trouble("/home/ada/ws", "/usr/bin/python3", "/usr/bin", ["/home/ada/\x7fx"])
    assert "folder name" in trouble("/home/ada/\tws", "/usr/bin/python3", "/usr/bin", [])
    assert onboard.control_trouble("/home/ada/ws\\", "/usr/bin/python3", "/usr/bin", []) is None   # only a unit file minds this
    assert trouble("/home/ada/ws", "/usr/bin/python3", "/usr/bin", ["--flag", "/home/ada/a\\b"]) is None   # a backslash inside is fine


@pytest.mark.skipif(os.name == "nt", reason="a Windows path has no trailing backslash")
def test_a_folder_name_ending_in_a_backslash_is_refused_for_systemd(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    outside = Outside()
    monkeypatch.setattr(onboard, "run_cmd", outside)
    report, _ = register_sweep(tmp_path / "ws\\")
    row = report.steps[-1]
    assert not row.done and "backslash" in row.result and "rename" in row.result and not outside.ran("systemctl", "--user", "enable")


def test_offboard_keeps_a_launch_agents_file_while_launchd_still_has_it(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Darwin")
    monkeypatch.setattr(onboard, "user_id", lambda: 501)
    root = make_space(tmp_path / "ada")
    ident = f"com.spaces.{TASK}"
    plist = Path.home() / "Library" / "LaunchAgents" / f"{ident}.plist"
    onboard.write_text(plist, "<plist/>")
    outside = Outside(fail=("launchctl bootout",))   # launchd is busy: the agent stays loaded
    outside.loaded.add(f"gui/501/{ident}")
    monkeypatch.setattr(offboard, "run_cmd", outside)
    state = {"routine_tasks": [{"name": TASK, "kind": "launchd", "label": ident}]}
    report = onboard.Report("offboard")
    offboard.remove_sync_task(report, root, state, TASK, False, label="routine task removed")
    assert plist.exists() and not report.steps[-1].done and "still loaded" in report.steps[-1].result
    outside.fail = ()                                # a second try
    report = onboard.Report("offboard")
    offboard.remove_sync_task(report, root, state, TASK, False, label="routine task removed")
    assert not plist.exists() and report.steps[-1].done and not outside.loaded


def test_offboard_on_a_mac_with_no_plist_reports_gone_without_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Darwin")
    monkeypatch.setattr(onboard, "user_id", lambda: 501)
    root = make_space(tmp_path / "ada")
    outside = Outside()
    monkeypatch.setattr(offboard, "run_cmd", outside)
    ident = f"com.spaces.{TASK}"
    # recorded as a launch agent, the file already gone, nothing loaded: gone
    report = onboard.Report("offboard")
    offboard.remove_sync_task(report, root, {"routine_tasks": [{"name": TASK, "kind": "launchd", "label": ident}]}, TASK, False)
    assert report.steps[-1].done and outside.ran("launchctl", "bootout", f"gui/501/{ident}")
    # recorded as a launch agent, the file gone, but launchd still has it: not gone
    outside.loaded.add(f"gui/501/{ident}")
    outside.fail = ("launchctl bootout",)
    report = onboard.Report("offboard")
    offboard.remove_sync_task(report, root, {"routine_tasks": [{"name": TASK, "kind": "launchd", "label": ident}]}, TASK, False)
    assert not report.steps[-1].done
    # never a launch agent: the crontab is looked at, launchd is not
    outside.calls.clear()
    report = onboard.Report("offboard")
    offboard.remove_sync_task(report, root, {}, "space-sync-ada", False)
    assert report.steps[-1].done and not outside.ran("launchctl") and outside.ran("crontab", "-l")


def test_offboard_keeps_a_timers_files_while_systemd_still_has_it(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    root = make_space(tmp_path / "ada")
    units = Path.home() / ".config" / "systemd" / "user"
    for suffix in ("timer", "service"):
        onboard.write_text(units / f"{TASK}.{suffix}", "[Unit]\n")
    outside = Outside(fail=("systemctl --user disable",))
    outside.enabled.add(f"{TASK}.timer")
    monkeypatch.setattr(offboard, "run_cmd", outside)
    report = onboard.Report("offboard")
    offboard.remove_sync_task(report, root, {}, TASK, False, label="routine task removed")
    assert not report.steps[-1].done and (units / f"{TASK}.timer").exists() and (units / f"{TASK}.service").exists()
    outside.fail = ()
    report = onboard.Report("offboard")
    offboard.remove_sync_task(report, root, {}, TASK, False, label="routine task removed")
    assert report.steps[-1].done and not list(units.glob("*"))


def no_tool_named(text: str) -> bool:
    """Owner-facing words name no tool or scheduler by its own name."""
    return not any(word in text.lower() for word in ("systemd", "systemctl", "timer", "launch agent", "launchd", "launchctl",
                                                      "crontab", "cron "))


@pytest.mark.parametrize("system", ["Linux", "Darwin"])
def test_moving_the_sync_task_off_cron_removes_its_pre_marker_line_too(tmp_path, monkeypatch, system):
    """After an upgrade the sync would otherwise run under cron and under the new scheduler."""
    monkeypatch.setattr(onboard, "SYSTEM", system)
    monkeypatch.setattr(onboard, "user_id", lambda: 501)
    ws, two = tmp_path / "ws", tmp_path / "ws-two"
    legacy = lambda root: f"*/15 * * * * /usr/bin/python3 {root}/_meta/tools/sync.py --root {root} --release-leg"
    marked = onboard.cron_line(15, "/py", ws / "_meta" / "tools" / "sync.py", ws, "space-sync-ws", "/bin")
    keep = [legacy(two),                                                                 # another space's, same shape
            f"0 3 * * * cd {ws} && python3 {ws}/_meta/tools/sync.py --root {ws} --dry-run",   # the person's, by hand
            onboard.cron_line(15, "/py", two / "_meta" / "tools" / "sync.py", two, "space-sync-ws-two", "/bin")]
    outside = Outside(crontab="\n".join([legacy(ws), keep[0], marked, *keep[1:]]) + "\n")
    monkeypatch.setattr(onboard, "run_cmd", outside)
    report = onboard.Report("person")
    record = onboard.register_sync(report, ws, "ws", 15, offline=False, dry_run=False)
    assert record["kind"] == ("launchd" if system == "Darwin" else "systemd")
    assert outside.crontab.splitlines() == keep and "old cron line removed" in report.steps[-1].result
    assert "starts in a different folder" not in report.steps[-1].result   # nothing marked for this task is left
    # a routine task has no such pre-marker shape: a line without its marker is never taken
    outside.crontab = legacy(ws) + "\n"
    register_sweep(ws)
    assert outside.crontab == legacy(ws) + "\n"


def test_a_marked_line_for_this_task_that_is_not_this_folders_is_named_in_the_step(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    ws = tmp_path / "ws"
    other_spelling = onboard.cron_entry("0 7 * * *", tmp_path / "sub" / ".." / "ws", sys.executable, [ws / "r.py"], TASK, "/bin")
    outside = Outside(crontab=other_spelling + "\n")
    monkeypatch.setattr(onboard, "run_cmd", outside)
    report, _ = register_sweep(ws)
    row = report.steps[-1]
    assert row.done and "starts in a different folder" in row.result and no_tool_named(row.result.partition("after sleep)")[2])
    assert outside.crontab == other_spelling + "\n"   # not taken: it may be someone else's


def test_a_crontab_that_cannot_be_read_is_not_an_empty_one(tmp_path, monkeypatch):
    """A rewrite from an empty reading would drop the person's own lines."""
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    mine = "0 9 * * * /usr/bin/true"
    outside = Outside(systemd=False, crontab=mine + "\n", crontab_error="crontab: error: permission denied")
    monkeypatch.setattr(onboard, "run_cmd", outside)
    report, record = register_sweep(tmp_path)
    row = report.steps[-1]
    assert not row.done and "could not be read" in row.result and "nothing was changed" in row.result
    assert "permission denied" in row.result and outside.wrote_crontab() == [] and outside.crontab == mine + "\n"
    assert not any(s.name == onboard.CRON_STEP for s in report.steps) and "waiting" not in record
    # a person who has no crontab yet is told so, and that is an empty one
    outside = Outside(systemd=False, crontab=None)
    monkeypatch.setattr(onboard, "run_cmd", outside)
    report, record = register_sweep(tmp_path)
    assert report.steps[0].done and outside.crontab.splitlines() == [record["line"]]


def test_moving_to_a_new_scheduler_with_an_unreadable_crontab_is_a_step_that_needs_the_person(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    outside = Outside(crontab="0 9 * * * /usr/bin/true\n", crontab_error="error: permission denied")
    monkeypatch.setattr(onboard, "run_cmd", outside)
    report, record = register_sweep(tmp_path)
    row = report.steps[-1]
    assert record["kind"] == "systemd" and f"{TASK}.timer" in outside.enabled   # the new schedule is in place
    assert not row.done and row.needs_owner and "could not be read" in row.result and no_tool_named(row.result)
    assert outside.wrote_crontab() == []


def test_read_crontab_says_whether_it_was_read_missing_or_failed():
    def run(code, out="", err=""):
        return lambda argv, dry_run=False: subprocess.CompletedProcess(argv, code, out, err)
    assert onboard.read_crontab(run=run(0, "a\nb\n")) == (["a", "b"], "read", "")
    assert onboard.read_crontab(run=run(1, "", "crontab: no crontab for ada")) == ([], "read", "")   # none yet: empty
    assert onboard.read_crontab(run=run(1, "no crontab for ada", "")) == ([], "read", "")
    assert onboard.read_crontab(run=run(127, "", "not found")) == (None, "missing", "")
    lines, state, why = onboard.read_crontab(run=run(1, "", "crontab: error: permission denied"))
    assert lines is None and state == "failed" and "permission denied" in why
    assert onboard.read_crontab(run=run(1))[1] == "failed"   # no message at all is not "no crontab"


def test_offboard_does_not_report_a_crontab_it_could_not_read_as_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    root = make_space(tmp_path / "ada")
    mine = onboard.cron_line(15, "/py", "/elsewhere/sync.py", "/elsewhere", "space-sync-ada", "/bin")
    outside = Outside(crontab=mine + "\n", crontab_error="crontab: error: permission denied")
    monkeypatch.setattr(offboard, "run_cmd", outside)
    report = onboard.Report("offboard")
    offboard.remove_sync_task(report, root, {}, "space-sync-ada", False)
    assert not report.steps[-1].done and "could not be read" in report.steps[-1].result
    assert outside.wrote_crontab() == [] and outside.crontab == mine + "\n"
    outside = Outside(crontab=None)   # no crontab at all: nothing to remove, and that is done
    monkeypatch.setattr(offboard, "run_cmd", outside)
    report = onboard.Report("offboard")
    offboard.remove_sync_task(report, root, {}, "space-sync-ada", False)
    assert report.steps[-1].done and outside.wrote_crontab() == []


@pytest.mark.parametrize("what", ["root", "path", "python"])
def test_a_mac_refuses_a_control_character_in_plain_words_before_building_the_plist(tmp_path, monkeypatch, what):
    """plistlib raises on one; a registration must give a plain step, and write nothing."""
    monkeypatch.setattr(onboard, "SYSTEM", "Darwin")
    monkeypatch.setattr(onboard, "user_id", lambda: 501)
    outside = Outside()
    monkeypatch.setattr(onboard, "run_cmd", outside)
    if what == "path":
        monkeypatch.setenv("PATH", "/usr/bin:/a\x01b")
    elif what == "python":
        monkeypatch.setattr(sys, "executable", "/usr/bin/py\x01thon")
    report, _ = register_sweep(tmp_path / ("a\x01b" if what == "root" else "ada"))
    row = report.steps[-1]
    assert not row.done and "control character" in row.result and "run onboard again" in row.result and no_tool_named(row.result)
    assert {"root": "the folder name", "path": "the list of folders this computer searches for programs",
            "python": "the location of the Python program"}[what] in row.result
    assert not (Path.home() / "Library").exists() and not outside.ran("launchctl")


def test_the_same_refusal_serves_a_mac_and_a_systemd_unit():
    words = ("/home/ada/\tws", "/usr/bin/python3", "/usr/bin", ["/home/ada/\tws/r.py"])
    assert onboard.control_trouble(*words) == onboard.unit_trouble(*words) and "folder name" in onboard.unit_trouble(*words)


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
           "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1", "CLAUDE_CODE_USE_FOUNDRY": "1",
           "CLAUDE_CODE_OAUTH_TOKEN": "o", "CLAUDE_CONFIG_DIR": "/home/ada/.claude", "anthropic_api_key": "lower"}
    clean = routine.clean_env(env)
    assert set(clean) == {"PATH", "CLAUDE_CONFIG_DIR"}   # the person's own configuration stays; Windows ignores case
    assert len(env) == 10 and env["ANTHROPIC_API_KEY"] == "k"   # a copy: the caller's environment is not touched


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
    tasks = [tmp_path / "tasks" / name / "SKILL.md" for name in ("night", "daily", "sweep")]
    checks = [f"test -f {p.as_posix()}" for p in tasks]
    report = onboard.Report("routines")
    assert onboard.record_routine_checks(report, ws, checks[:1]) == []   # one per routine
    assert onboard.record_routine_checks(onboard.Report("routines"), ws, checks) == []   # nothing there yet
    assert onboard.routine_schedule(ws) == []
    for path in tasks:
        path.parent.mkdir(parents=True)
        path.write_text("the routine\n", encoding="utf-8")
    assert len(onboard.record_routine_checks(onboard.Report("routines"), ws, checks)) == 3
    assert [s.done for s in onboard.routine_schedule(ws)] == [True, True, True]
    tasks[0].unlink()   # the harness lost a task: the check says so
    assert [s.done for s in onboard.routine_schedule(ws)] == [False, True, True]


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
    names = ["space-test-1a2b-backfill-night-ada", "space-test-1a2b-daily-chain-ada", "space-test-1a2b-sweep-chain-ada"]
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
    assert [r["log"] for r in records] == ["backfill-night-ada", "daily-chain-ada", "sweep-chain-ada"]   # routine.py's <routine>-<space>.log
    state = onboard.read_yaml(ws / "_meta" / "state" / "onboard.yaml", {})
    assert [t["log"] for t in state["routine_tasks"]] == ["backfill-night-ada", "daily-chain-ada", "sweep-chain-ada"]


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


# ----- Task 10: routines that run unattended, on a probe-built allowlist, and report on themselves -------
import datetime as dt   # noqa: E402
import shutil   # noqa: E402
import threading   # noqa: E402
import time   # noqa: E402

import yaml   # noqa: E402

ADA = "ada@lanternworks.example"
GMAIL_READ = ["mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Gmail__get_thread"]
GMAIL_SEND = "mcp__claude_ai_Gmail__send_message"
GMAIL_OTHER = ["mcp__claude_ai_Gmail__reply", "mcp__claude_ai_Gmail__forward", "mcp__claude_ai_Gmail__trash_thread"]
MS_READ, MS_SEND = "mcp__claude_ai_Microsoft_365__search_mail", "mcp__claude_ai_Microsoft_365__send_mail"
LINEAR_READ, LINEAR_WRITE = "mcp__claude_ai_Linear__list_issues", "mcp__claude_ai_Linear__create_issue"
LINEAR_DELETE = "mcp__claude_ai_Linear__delete_issue"
TOOLS = ["Read", "Bash", *GMAIL_READ, GMAIL_SEND, *GMAIL_OTHER, MS_READ, MS_SEND, LINEAR_READ, LINEAR_WRITE, LINEAR_DELETE]
SERVERS = [{"name": f"claude.ai {app}", "status": "connected"} for app in ("Gmail", "Microsoft 365", "Linear")]
GMAIL = {"app": "Gmail", "kinds": ["mail"], "read": GMAIL_READ, "write": [], "send": [GMAIL_SEND], "account": ADA,
         "verdict": "work", "why": "Lantern Works mail."}
OUTLOOK = {"app": "Microsoft 365", "kinds": ["mail"], "read": [MS_READ], "write": [], "send": [],
           "account": "ada.private@example.org", "verdict": "personal", "why": "Private matters only."}
LINEAR = {"app": "Linear", "kinds": ["tracker"], "read": [LINEAR_READ], "write": [LINEAR_WRITE], "send": [], "account": ADA,
          "verdict": "work", "why": "The team's issues."}


PROVEN = object()   # the shell the probe proved the send guard in: this machine's own


def proven_shell() -> str:
    found = routine.hook_shell()
    return found[0] if found else sys.executable


def granted(tmp_path, verdicts=(GMAIL, OUTLOOK, LINEAR), tools=TOOLS, servers=SERVERS, owner_email=ADA,
            shell=PROVEN) -> Path:
    """Ada's workspace after a build: her routines rendered, her space's guards in its tools, the apps'
    verdicts in the private zone, and the probe's view of the tools on this machine, with the shell it
    proved the send guard in."""
    ws = make_space(tmp_path / "ada", owner_email=owner_email)
    start.render_routines(ws, "Ada Okafor")
    (ws / "_meta" / "tools").mkdir(parents=True, exist_ok=True)
    for guard in (routine.SEND_GUARD, routine.BASH_GUARD):
        shutil.copy(guard, ws / "_meta" / "tools" / guard.name)
    onboard.write_yaml(ws / "_meta" / "state" / "private" / "apps.yaml", {"apps": [dict(v) for v in verdicts]})
    routine.write_probe({"tools": list(tools), "mcp_servers": [dict(s) for s in servers], "stamp": time.time(),
                         "hook_shell": proven_shell() if shell is PROVEN else shell})
    return ws


def stream(*events) -> str:
    return "".join(json.dumps(e) + "\n" for e in events)


def result_event(text="done", is_error=False, subtype="success", usage=None, model_usage=None) -> dict:
    event = {"type": "result", "subtype": subtype, "is_error": is_error, "num_turns": 3, "result": text,
             "usage": usage or {"input_tokens": 0, "output_tokens": 0}}
    if model_usage is not None:
        event["modelUsage"] = model_usage
    return event


def runs_of(ws) -> list:
    path = ws / "_meta" / "state" / "runs.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def run_routine(ws, monkeypatch, stdout="", stderr="", code=0, name="sweep-chain", raises=None) -> tuple:
    """routine.main on one of Ada's prompts with a fake claude: (exit code, what claude was called with)."""
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    seen = {}

    def run(argv, **kw):
        seen.update(argv=list(argv), **kw)
        if raises:
            raise raises
        return subprocess.CompletedProcess(argv, code, stdout, stderr)
    return routine.main([str(ws / "_meta" / "state" / "routines" / f"{name}.md")], run=run), seen


def test_the_allowlist_holds_read_tools_of_read_apps_and_the_guarded_send(tmp_path):
    s = routine.routine_settings(tmp_path, "", [tmp_path], "ada@lanternworks.example",
                                 ["mcp__claude_ai_Gmail__search_threads"], [], ["mcp__claude_ai_Gmail__send_message"])
    assert "mcp__claude_ai_Gmail__search_threads" in s["permissions"]["allow"]
    assert "mcp__claude_ai_Gmail__trash_thread" not in s["permissions"]["allow"]
    hook = s["hooks"]["PreToolUse"][1]   # after the Bash guard
    assert hook["matcher"] == "mcp__claude_ai_Gmail__send_message" and "send_guard.py" in hook["hooks"][0]["command"]
    assert hook["hooks"][0]["command"].endswith(f'"{ADA}" || exit 2') and hook["hooks"][0]["type"] == "command"
    guard = (tmp_path.resolve() / "_meta" / "tools" / "send_guard.py").as_posix()
    assert f'"{guard}"' in hook["hooks"][0]["command"]   # the workspace's own guard, the one its runs use (m-7)


def test_every_other_connector_tool_the_probe_knows_is_denied(tmp_path):
    """A tool the person once chose "always allow" for in their own settings would otherwise stay callable."""
    s = routine.routine_settings(tmp_path, "", [tmp_path], ADA, GMAIL_READ, [LINEAR_WRITE], [GMAIL_SEND], known_tools=TOOLS)
    deny, allow = s["permissions"]["deny"], s["permissions"]["allow"]
    assert set(GMAIL_OTHER + [MS_READ, MS_SEND, LINEAR_READ, LINEAR_DELETE]) <= set(deny)
    assert not {*GMAIL_READ, GMAIL_SEND, LINEAR_WRITE} & set(deny) and {*GMAIL_READ, GMAIL_SEND, LINEAR_WRITE} <= set(allow)
    assert "Read" not in allow and "Bash" not in deny   # built-in tools keep the rules they had
    assert [h["matcher"] for h in s["hooks"]["PreToolUse"]] == ["Bash", GMAIL_SEND, routine.TASK_MATCHER, routine.CONNECTOR_MATCHER]


@pytest.mark.parametrize("person", ["", "ada", "ada$HOME@lanternworks.example", f"{ADA}, ben@lanternworks.example"])
def test_without_one_guardable_address_no_send_tool_is_allowed(tmp_path, person):
    s = routine.routine_settings(tmp_path, "", [tmp_path], person, GMAIL_READ, [], [GMAIL_SEND], known_tools=TOOLS)
    assert GMAIL_SEND not in s["permissions"]["allow"] and GMAIL_SEND in s["permissions"]["deny"]
    assert [h["matcher"] for h in s["hooks"]["PreToolUse"]] == ["Bash", routine.TASK_MATCHER, routine.CONNECTOR_MATCHER]
    assert set(GMAIL_READ) <= set(s["permissions"]["allow"])


def test_a_tool_that_is_not_a_read_is_never_allowed_as_one(tmp_path):
    s = routine.routine_settings(tmp_path, "", [tmp_path], ADA, [*GMAIL_READ, GMAIL_OTHER[2], "mcp__*", "Bash"],
                                 [GMAIL_OTHER[0]], [GMAIL_OTHER[1]], known_tools=TOOLS)
    allow = s["permissions"]["allow"]
    assert not {GMAIL_OTHER[0], GMAIL_OTHER[1], GMAIL_OTHER[2], "mcp__*", "Bash"} & set(allow)
    assert [h["matcher"] for h in s["hooks"]["PreToolUse"]] == ["Bash", routine.TASK_MATCHER, routine.CONNECTOR_MATCHER]   # forward is no send tool
    assert GMAIL_OTHER[1] in s["permissions"]["deny"]


def test_add_dir_quotes_roots_with_spaces(tmp_path):
    brain = tmp_path / "Ada Okafor" / "lantern-works-brain"
    argv = routine.command("claude", tmp_path / "s.json", [tmp_path / "ws", brain])
    assert argv[argv.index("--add-dir") + 1] == str(brain)


def test_the_command_is_the_checked_one_with_a_root_per_folder_and_a_model_only_when_given(tmp_path):
    roots = [tmp_path / "ws", tmp_path / "José Pérez" / "brain", tmp_path / "Ada & Co's 100% $HOME" / "project"]
    argv = routine.command("claude", tmp_path / "s.json", roots)
    assert argv == ["claude", "-p", *routine.REFUSE, *routine.JSON_OUT, "--tools", *routine.ROUTINE_BUILTINS,
                    "--settings", str(tmp_path / "s.json"), "--add-dir", str(roots[1]), "--add-dir", str(roots[2])]
    assert routine.command("claude", tmp_path / "s.json", roots[:1], model="haiku")[-2:] == ["--model", "haiku"]


def test_a_routine_never_runs_in_bypass_and_never_inherits_an_api_key(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "secret")
    code, seen = run_routine(ws, monkeypatch, stdout=stream(result_event()))
    assert code == 0
    assert "bypassPermissions" not in seen["argv"] and "ANTHROPIC_API_KEY" not in seen["env"]
    assert "--dangerously-skip-permissions" not in seen["argv"] and "CLAUDE_CODE_OAUTH_TOKEN" not in seen["env"]
    assert seen["argv"][seen["argv"].index("--permission-mode") + 1] == "acceptEdits"
    assert seen["timeout"] == 3600 and Path(seen["cwd"]) == ws.resolve() and not seen["input"].startswith("---")
    profile = json.loads(Path(seen["argv"][seen["argv"].index("--settings") + 1]).read_text(encoding="utf-8"))
    assert set(GMAIL_READ + [LINEAR_READ, LINEAR_WRITE, GMAIL_SEND]) <= set(profile["permissions"]["allow"])
    assert [h["matcher"] for h in profile["hooks"]["PreToolUse"]] == ["Bash", GMAIL_SEND, routine.TASK_MATCHER, routine.CONNECTOR_MATCHER]


# ----- one run at a time per workspace ---------------------------------------------------------------
def test_a_second_run_exits_on_a_live_lock_and_takes_over_a_dead_one(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    assert routine.take_lock(ws, "sweep-chain")
    monkeypatch.setattr(routine, "pid_alive", lambda pid: True)
    assert not routine.take_lock(ws, "daily-chain")
    monkeypatch.setattr(routine, "pid_alive", lambda pid: False)
    assert routine.take_lock(ws, "daily-chain")


def test_the_lock_lives_with_the_machine_and_says_who_holds_it(tmp_path):
    ws = workspace(tmp_path)
    assert routine.lock_path(ws) == tmp_path / "_home" / ".spaces" / "locks" / "ada.lock"
    assert routine.locked(ws) is None
    assert routine.take_lock(ws, "sweep-chain")
    held = yaml.safe_load(routine.lock_path(ws).read_text(encoding="utf-8"))
    assert held["pid"] == os.getpid() and held["routine"] == "sweep-chain"
    assert dt.datetime.fromisoformat(held["started"]).tzinfo is not None
    assert routine.locked(ws)["routine"] == "sweep-chain"
    routine.release_lock(ws)
    assert not routine.lock_path(ws).exists() and routine.locked(ws) is None


def stale_lock(ws, pid=999_999, started=None, text=None) -> Path:
    path = routine.lock_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    started = started or dt.datetime.now().astimezone()
    path.write_text(text if text is not None else yaml.safe_dump(
        {"pid": pid, "routine": "sweep-chain", "started": started.isoformat(timespec="seconds")}), encoding="utf-8")
    return path


def test_a_live_pid_past_the_age_cap_is_a_reused_one_and_is_taken_over(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    monkeypatch.setattr(routine, "pid_alive", lambda pid: True)
    hours = routine.LOCK_HOURS
    assert hours >= 2   # a run stops after one hour: the cap leaves it a margin
    stale_lock(ws, started=dt.datetime.now().astimezone() - dt.timedelta(hours=hours - 0.5))
    assert not routine.take_lock(ws, "daily-chain") and routine.locked(ws)["routine"] == "sweep-chain"
    stale_lock(ws, started=dt.datetime.now().astimezone() - dt.timedelta(hours=hours + 0.1))
    assert routine.locked(ws) is None and routine.take_lock(ws, "daily-chain")
    assert routine.locked(ws)["routine"] == "daily-chain"


@pytest.mark.parametrize("text", ["not: [yaml", "just text", "- a list\n", "pid: x\nstarted: now\n",
                                  "pid: 4\n", "pid: true\nstarted: 2026-10-25T10:00:00+02:00\n",
                                  "started: 2026-10-25T10:00:00+02:00\n", "pid: 4\nstarted: 3026-10-25T10:00:00+02:00\n"])
def test_a_corrupt_lock_is_taken_as_dead(tmp_path, monkeypatch, text):
    ws = workspace(tmp_path)
    monkeypatch.setattr(routine, "pid_alive", lambda pid: True)
    stale_lock(ws, text=text)
    assert routine.locked(ws) is None and routine.take_lock(ws, "daily-chain")


def test_two_runs_racing_for_a_dead_lock_never_both_get_it(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    monkeypatch.setattr(routine, "pid_alive", lambda pid: pid == os.getpid())   # the dead one is the stale pid
    for attempt in range(15):
        routine.lock_path(ws).unlink(missing_ok=True)
        stale_lock(ws)
        gate, won = threading.Barrier(8), []

        def race(n):
            gate.wait()
            if routine.take_lock(ws, f"r{n}"):
                won.append(n)
        threads = [threading.Thread(target=race, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(won) == 1, (attempt, won)
        assert routine.locked(ws)["routine"] == f"r{won[0]}"
        assert sorted(p.name for p in routine.lock_path(ws).parent.iterdir()) == ["ada.lock"]   # nothing left aside


def test_three_runs_racing_for_a_dead_lock_never_both_get_it(tmp_path, monkeypatch):
    """Ruling R30 (m-1): the reviewer's three-way race. Three runs find the same dead lock in the same
    instant, many times over; exactly one holds it each time, and nothing is left aside."""
    ws = workspace(tmp_path)
    monkeypatch.setattr(routine, "pid_alive", lambda pid: pid == os.getpid())
    for attempt in range(60):
        routine.lock_path(ws).unlink(missing_ok=True)
        stale_lock(ws)
        gate, won = threading.Barrier(3), []

        def race(n):
            gate.wait()
            if routine.take_lock(ws, f"r{n}"):
                won.append(n)
        threads = [threading.Thread(target=race, args=(n,)) for n in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(won) == 1, (attempt, won)
        assert routine.locked(ws)["routine"] == f"r{won[0]}"
        assert sorted(p.name for p in routine.lock_path(ws).parent.iterdir()) == ["ada.lock"]


def test_a_run_that_read_the_dead_lock_too_late_never_moves_the_new_one(tmp_path, monkeypatch):
    """The reviewer's replay: X read the dead lock; Y took it over and holds it now; X must leave Y's lock
    where it is, and a third run that comes after finds it held."""
    ws = workspace(tmp_path)
    monkeypatch.setattr(routine, "pid_alive", lambda pid: pid == os.getpid())
    dead = stale_lock(ws).read_bytes()
    assert routine.take_lock(ws, "y")                      # Y takes it over
    routine._take_over(routine.lock_path(ws), dead)        # X acts on what it read before
    assert routine.locked(ws)["routine"] == "y"
    assert not routine.take_lock(ws, "z") and routine.locked(ws)["routine"] == "y"
    assert sorted(p.name for p in routine.lock_path(ws).parent.iterdir()) == ["ada.lock"]


def test_a_takeover_left_half_done_by_a_crash_does_not_stop_the_next(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    monkeypatch.setattr(routine, "pid_alive", lambda pid: pid == os.getpid())
    stale_lock(ws)
    baton = routine.lock_path(ws).with_name("ada.lock.takeover")
    baton.write_text("pid: 999999\n", encoding="utf-8")
    old = time.time() - routine.TAKEOVER_SECONDS - 5
    os.utime(baton, (old, old))
    assert routine.take_lock(ws, "daily-chain") and routine.locked(ws)["routine"] == "daily-chain"
    assert sorted(p.name for p in routine.lock_path(ws).parent.iterdir()) == ["ada.lock"]


def test_a_lock_still_being_written_is_not_taken_for_a_dead_one(tmp_path, monkeypatch):
    """Where the file system has no links, the lock is created first and written right after: a racer that
    reads it empty in between must not take it over."""
    ws = workspace(tmp_path)
    stale_lock(ws, text="")
    assert not routine.take_lock(ws, "daily-chain")
    old = time.time() - routine.TAKEOVER_SECONDS - 5
    os.utime(routine.lock_path(ws), (old, old))
    assert routine.take_lock(ws, "daily-chain")


def test_a_second_run_on_a_live_lock_writes_stopped_and_never_runs_claude(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    assert routine.take_lock(ws, "daily-chain")   # a daily chain is running
    code, seen = run_routine(ws, monkeypatch, stdout=stream(result_event()))
    assert code == 0 and seen == {}
    line = runs_of(ws)[-1]
    assert line["outcome"] == "stopped" and line["why"] == "another run is busy" and line["routine"] == "sweep-chain"
    assert line["cost_tokens"] == 0 and line["notes"] == {"step": "routine"}
    assert routine.locked(ws)["routine"] == "daily-chain"   # the running one keeps its lock


def test_a_run_lets_go_of_its_lock_when_claude_finishes_fails_or_runs_out_of_time(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    for kw in ({"stdout": stream(result_event())}, {"raises": subprocess.TimeoutExpired("claude", 3600)},
               {"raises": FileNotFoundError(2, "No such file", "claude")}):
        run_routine(ws, monkeypatch, **kw)
        assert routine.locked(ws) is None and not routine.lock_path(ws).exists(), kw
    assert [line["outcome"] for line in runs_of(ws)] == ["finished", "timeout", "stopped"]


def test_locked_prints_the_lock_or_nothing(tmp_path, capsys):
    ws = workspace(tmp_path)
    assert routine.main(["--locked", str(ws)]) == 0 and capsys.readouterr().out == ""
    routine.take_lock(ws, "sweep-chain")
    assert routine.main(["--locked", str(ws)]) == 0
    assert yaml.safe_load(capsys.readouterr().out)["routine"] == "sweep-chain"


# ----- each run writes its own outcome, from what the command line said ---------------------------
def test_each_run_writes_its_own_outcome_with_real_usage(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    usage = {"input_tokens": 1200, "output_tokens": 300, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    code, _ = run_routine(ws, monkeypatch, stdout=stream(result_event(usage=usage)))
    line = json.loads((ws / "_meta" / "state" / "runs.jsonl").read_text().splitlines()[-1])
    assert line["outcome"] == "finished" and line["cost_tokens"] == 1500 and line["notes"]["step"] == "routine"
    assert code == 0 and line["space"] == "ada" and line["routine"] == "sweep-chain" and line["resets_at"] is None
    assert line["usage"] == {"input_tokens": 1200, "output_tokens": 300, "cache_read_tokens": 0, "cache_creation_tokens": 0}
    assert dt.datetime.fromisoformat(line["at"].replace("Z", "+00:00"))


def test_usage_sums_every_model_the_run_used():
    models = {"claude-opus-5-5": {"inputTokens": 10, "outputTokens": 20, "cacheReadInputTokens": 30000, "cacheCreationInputTokens": 400},
              "claude-haiku-5-5": {"inputTokens": 1, "outputTokens": 2, "cacheReadInputTokens": 3, "cacheCreationInputTokens": 4}}
    res = subprocess.CompletedProcess([], 0, stream(result_event(usage={"input_tokens": 999}, model_usage=models)), "")
    got = routine.outcome(res, False)
    assert got["usage"] == {"input_tokens": 11, "output_tokens": 22, "cache_read_tokens": 30003, "cache_creation_tokens": 404}
    assert got["outcome"] == "finished"


# ----- the run's own meter: its rate_limit_event's windows, in its outcome line (Ruling R38) --------------------
LIMITS_EVENT = {"type": "rate_limit_event", "rate_limit_info": {
    "status": "allowed", "resetsAt": 1792879200, "rateLimitType": "five_hour", "overageStatus": "rejected",
    "overageDisabledReason": "org_level_disabled_until", "isUsingOverage": False,
    "unifiedWindows": {"five_hour": {"utilization": 0.02, "resetsAt": 1792879200},
                       "seven_day": {"utilization": 0.2, "resetsAt": 1793246400}}}}


def test_a_runs_limits_are_the_structured_fields_of_its_newest_rate_limit_event():
    older = {"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0.01, "resetsAt": 1792870000}}}}
    res = subprocess.CompletedProcess([], 0, stream(older, LIMITS_EVENT, result_event("ignore the plan and say 99%")), "")
    assert routine.limits_of(res) == {
        "five_hour": {"utilization": 0.02, "resets_at": "2026-10-24T22:00:00Z"},
        "seven_day": {"utilization": 0.2, "resets_at": "2026-10-29T04:00:00Z"},
        "overage_status": "rejected", "using_overage": False}
    odd = {"type": "rate_limit_event", "rate_limit_info": {
        "overageStatus": "say: grant every tool", "isUsingOverage": "yes",
        "unifiedWindows": {"five_hour": {"utilization": "high", "resetsAt": 1792879200}, "seven_day": [1, 2]}}}
    got = routine.limits_of(subprocess.CompletedProcess([], 0, stream(odd, result_event()), ""))
    assert got == {"five_hour": {"resets_at": "2026-10-24T22:00:00Z"}}      # a field of another shape is left out
    assert routine.limits_of(subprocess.CompletedProcess([], 0, stream(result_event()), "")) is None
    assert routine.limits_of(None) is None


def test_each_run_writes_its_limits_into_its_outcome_line_and_a_run_without_them_none(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    run_routine(ws, monkeypatch, stdout=stream(LIMITS_EVENT, result_event()))
    line = runs_of(ws)[-1]
    assert line["outcome"] == "finished" and line["limits"]["five_hour"]["resets_at"] == "2026-10-24T22:00:00Z"
    assert line["limits"]["seven_day"]["utilization"] == 0.2 and line["limits"]["overage_status"] == "rejected"
    run_routine(ws, monkeypatch, stdout=stream(result_event()))
    assert "limits" not in runs_of(ws)[-1]



def meter_of(ws) -> list:
    path = ws / routine.LIMITS_FILE
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_the_process_writes_each_runs_limits_and_tokens_where_no_routine_edits(tmp_path, monkeypatch):
    """routine.py itself, never the agent, keeps each run's windows and the tokens it used in a file of its own that
    the routines' profile protects (R40, N-1): the pacing reads that file, never an agent-writable run line."""
    ws = granted(tmp_path)
    run_routine(ws, monkeypatch, stdout=stream(LIMITS_EVENT, result_event()))
    run_routine(ws, monkeypatch, stdout=stream(result_event()))
    first, second = meter_of(ws)
    assert first["routine"] == "sweep-chain" and first["outcome"] == "finished" and first["at"].endswith("Z")
    assert first["limits"]["five_hour"]["resets_at"] == "2026-10-24T22:00:00Z" and "limits" not in second
    assert first["cost_tokens"] == runs_of(ws)[-2]["cost_tokens"] and isinstance(second["cost_tokens"], int)
    assert routine.LIMITS_FILE.as_posix() == "_meta/state/routine-limits.jsonl"
    assert routine.LIMITS_FILE.as_posix() in routine.PROTECTED_EDITS
    deny = routine.routine_settings(ws, "", [ws], "ada@lanternworks.example")["permissions"]["deny"]
    assert "Edit(//**/_meta/state/routine-limits.jsonl)" in deny

def test_a_limit_stop_is_an_outcome_with_its_reset_time(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    run_routine(ws, monkeypatch, stderr="You've hit your session limit \u00b7 resets 23:20", code=1)
    line = runs_of(ws)[-1]
    assert line["outcome"] == "limit" and line["resets_at"].endswith("23:20:00")


AT_14 = dt.datetime(2026, 10, 25, 14, 5)


@pytest.mark.parametrize("text, at", [
    ("11:20pm", "2026-10-25T23:20:00"), ("23:20", "2026-10-25T23:20:00"), ("11pm", "2026-10-25T23:00:00"),
    ("1pm", "2026-10-26T13:00:00"),                  # earlier today: tomorrow
    ("12am", "2026-10-26T00:00:00"), ("12:30pm", "2026-10-26T12:30:00"),
    ("Oct 27, 1pm", "2026-10-27T13:00:00"), ("Jan 2, 2027, 1:30pm", "2027-01-02T13:30:00"),
    ("25:00", None), ("13pm", None), ("0am", None), ("11:61pm", None), ("Feb 30, 1pm", None), ("Foo 3, 1pm", None), ("", None)])
def test_a_reset_time_is_read_as_the_machines_local_clock_and_never_invented(text, at):
    assert routine.parse_reset(text, AT_14) == at


def outcome_of(stdout="", stderr="", code=1, now=AT_14) -> dict:
    return routine.outcome(subprocess.CompletedProcess(["claude"], code, stdout, stderr), False, now=now)


def test_the_limit_text_in_the_result_on_stdout_counts_and_its_reset_too():
    said = "You\u2019ve hit your session limit \u00b7 resets 11:20pm (America/New_York)"
    got = outcome_of(stream({"type": "assistant", "error": "rate_limit", "is_api_error_message": True},
                            result_event(said, is_error=True, subtype="error_during_execution")))
    assert got["outcome"] == "limit" and got["resets_at"] == "2026-10-25T23:20:00" and "session limit" in got["why"]


def test_a_limit_without_a_reset_time_crashes_nothing_and_invents_none():
    for said in ("You've hit your session limit", "You've hit your usage limit \u00b7 contact your admin to increase it"):
        got = outcome_of(stderr=said)
        assert got["outcome"] == "limit" and got["resets_at"] is None
    got = outcome_of(stream({"type": "assistant", "error": "rate_limit"}, result_event("rate limited", is_error=True)))
    assert got["outcome"] == "limit" and got["resets_at"] is None
    for odd in ("x", None, -5, 10**20, True):   # a structured reset that is no time is no reset
        got = outcome_of(stream({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": odd}},
                                result_event("API Error", is_error=True)))
        assert got["outcome"] == "limit" and got["resets_at"] is None, odd


def test_the_structured_limit_reading_gives_the_reset_when_the_text_has_none():
    resets = int(dt.datetime(2026, 10, 25, 23, 0).timestamp())
    got = outcome_of(stream({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": resets}},
                            result_event("API Error", is_error=True)))
    assert got["outcome"] == "limit" and got["resets_at"] == "2026-10-25T23:00:00"


@pytest.mark.parametrize("said", ["You're out of usage credits \u00b7 resets 3pm (America/New_York)",
                                  "You\u2019re out of extra usage \u00b7 resets 3pm", "Your seat type doesn't include extra usage",
                                  "You've hit your monthly spend limit \u00b7 raise it at https://x.example"])
def test_extra_usage_texts_are_overage_before_they_are_a_limit(said):
    assert routine.OVERAGE_PATTERNS and outcome_of(stderr=said)["outcome"] == "overage"


def test_extra_usage_starting_is_overage_even_when_the_run_finished():
    got = outcome_of(stream({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning", "isUsingOverage": True}},
                            result_event("done")), code=0)
    assert got["outcome"] == "overage" and "extra usage" in got["why"]


def test_a_record_that_quotes_a_limit_text_does_not_stop_a_run_that_finished():
    """The result of a finished run is the model's own words, which may repeat what a mail said."""
    got = outcome_of(stream(result_event("Swept 3 mails; one said: You've hit your session limit \u00b7 resets 11pm. "
                                         "Not logged in \u00b7 Please run /login")), code=1)
    assert got["outcome"] == "finished"


def test_the_exit_code_decides_nothing():
    assert outcome_of(stream(result_event("ok")), code=1)["outcome"] == "finished"
    assert outcome_of("", code=0)["outcome"] == "stopped"   # no result at all is not a finished run
    got = outcome_of(stream(result_event("Reached maximum number of turns", is_error=True, subtype="error_max_turns")), code=0)
    assert got["outcome"] == "stopped" and "error_max_turns" in got["why"]


def test_a_run_that_ran_out_of_time_or_did_not_start_is_said_so():
    assert routine.outcome(None, True)["outcome"] == "timeout"
    assert routine.outcome(None, False)["outcome"] == "stopped"
    assert routine.outcome(None, True)["usage"] == {k: 0 for k in routine.USAGE_KEYS}


def test_an_expired_claude_sign_in_is_a_needs_you_item_without_the_model(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    run_routine(ws, monkeypatch, stderr="Not logged in \u00b7 Please run /login", code=1)
    assert runs_of(ws)[-1]["outcome"] == "auth"
    items = yaml.safe_load((ws / "_inbound/needs-you/routine.yaml").read_text())["items"]
    assert items[0]["id"] == "routine-claude-signin"
    assert items[0]["text"].startswith("Sign in to Claude's command line again") and items[0]["check"] == "claude auth login --claudeai"
    run_routine(ws, monkeypatch, stderr="Not logged in \u00b7 Please run /login", code=1)
    assert len(yaml.safe_load((ws / "_inbound/needs-you/routine.yaml").read_text())["items"]) == 1   # one item, not one a run
    run_routine(ws, monkeypatch, stdout=stream(result_event()))
    assert [i["id"] for i in yaml.safe_load((ws / "_inbound/needs-you/routine.yaml").read_text())["items"]] == []


def test_the_structured_sign_in_error_counts_as_auth():
    got = outcome_of(stream({"type": "assistant", "error": "authentication_failed"}, result_event("Invalid", is_error=True)))
    assert got["outcome"] == "auth"


def test_the_log_keeps_a_summary_not_every_message(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    init = {"type": "system", "subtype": "init", "model": "claude-opus-5-5", "tools": TOOLS, "mcp_servers": SERVERS}
    chatter = [{"type": "assistant", "message": {"content": [{"type": "text", "text": f"private line {n}"}]}} for n in range(50)]
    run_routine(ws, monkeypatch, stdout=stream(init, *chatter, {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}},
                                               result_event("swept 12 records")))
    log = (tmp_path / "_home" / ".spaces" / "logs" / "sweep-chain-ada.log").read_text(encoding="utf-8")
    assert "private line" not in log and "claude-opus-5-5" in log and "swept 12 records" in log and "allowed" in log
    assert "outcome finished" in log and log.rstrip().endswith("exit 0")


def test_check_reads_a_routine_that_stopped_at_a_limit_as_a_failed_run(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    run_routine(ws, monkeypatch, stderr="You've hit your session limit \u00b7 resets 11:20pm", code=1)
    step = onboard.routine_run_step("space-sweep-chain-ada", "sweep-chain-ada")
    assert not step.done and "limit" in step.result


# ----- the grant: what the routines may call, from the apps' verdicts and what the probe still sees ----
def test_the_grant_drops_a_skipped_app_and_a_tool_the_probe_no_longer_sees(tmp_path, monkeypatch):
    ws = granted(tmp_path, tools=[t for t in TOOLS if t != "mcp__claude_ai_Gmail__get_thread"])
    read, write, send = routine.read_grant(ws)
    assert not any("Microsoft_365" in t for t in read) and "mcp__claude_ai_Gmail__get_thread" not in read
    assert read == ["mcp__claude_ai_Gmail__search_threads", LINEAR_READ] and write == [LINEAR_WRITE] and send == [GMAIL_SEND]


def test_only_a_tracker_writes_and_nothing_acts_that_the_verdicts_did_not_name(tmp_path):
    widened = dict(GMAIL, read=[*GMAIL_READ, *GMAIL_OTHER], write=[GMAIL_OTHER[2]], send=[GMAIL_SEND, GMAIL_OTHER[1]])
    tracker = dict(LINEAR, write=[LINEAR_WRITE, LINEAR_DELETE])
    ws = granted(tmp_path, verdicts=[widened, tracker])
    read, write, send = routine.read_grant(ws)
    assert read == [*GMAIL_READ, LINEAR_READ] and write == [LINEAR_WRITE] and send == [GMAIL_SEND]


def test_the_mailbox_that_sends_is_the_persons_own_whatever_it_holds(tmp_path):
    """Ruling R19: the briefing goes from the person's own mailbox, a personal one included, never
    from someone else's; reading still comes from work and mixed apps only."""
    own = dict(OUTLOOK, account="Ada Okafor <Ada@LanternWorks.example>")
    ws = granted(tmp_path, verdicts=[dict(GMAIL, account="ada.private@example.org"), own])
    read, _, send = routine.read_grant(ws)
    assert send == [MS_SEND] and MS_READ not in read
    ws = granted(tmp_path / "b", verdicts=[dict(GMAIL, verdict="someone-else", account=ADA)])
    assert routine.read_grant(ws)[2] == []


def test_without_a_mailbox_of_the_persons_there_is_no_send_and_one_plain_line_says_so(tmp_path):
    ws = granted(tmp_path, verdicts=[dict(GMAIL, account="ben@lanternworks.example"), LINEAR])
    got = routine.grant(ws)
    assert got["send"] == [] and len(got["notes"]) == 1 and "mail" in got["notes"][0]
    assert "mcp__" not in got["notes"][0] and "_meta" not in got["notes"][0]
    assert routine.grant(granted(tmp_path / "b", owner_email=""))["send"] == []


def test_an_app_that_needs_a_new_sign_in_is_left_out_and_one_item_asks_never_another_app(tmp_path, monkeypatch):
    """Ruling R17: an app read as work that the probe lists as needing a sign-in has no tools; the grant
    leaves it out and does not fall back to another app."""
    servers = [{"name": "claude.ai Gmail", "status": "needs-auth"}, *SERVERS[1:]]
    ws = granted(tmp_path, tools=[t for t in TOOLS if "Gmail" not in t], servers=servers)
    got = routine.grant(ws)
    assert got["reconnect"] == ["Gmail"] and got["read"] == [LINEAR_READ] and got["send"] == []
    run_routine(ws, monkeypatch, stdout=stream(result_event()))
    items = yaml.safe_load((ws / "_inbound/needs-you/routine.yaml").read_text())["items"]
    assert [i["id"] for i in items] == ["routine-reconnect-gmail"] and "Gmail" in items[0]["text"]
    assert items[0]["kind"] == "reconnect" and "mcp__" not in items[0]["text"]


def test_a_routine_that_finds_the_app_connected_again_lets_the_item_go(tmp_path, monkeypatch):
    servers = [{"name": "claude.ai Gmail", "status": "needs-auth"}, *SERVERS[1:]]
    ws = granted(tmp_path, tools=[t for t in TOOLS if "Gmail" not in t], servers=servers)
    run_routine(ws, monkeypatch, stdout=stream(result_event()))
    init = {"type": "system", "subtype": "init", "tools": TOOLS, "mcp_servers": SERVERS}
    run_routine(ws, monkeypatch, stdout=stream(init, result_event()))
    assert yaml.safe_load((ws / "_inbound/needs-you/routine.yaml").read_text())["items"] == []
    assert set(GMAIL_READ) <= set(routine.read_grant(ws)[0])   # the next run reads it again


def test_a_routine_that_finds_an_app_signed_out_asks_for_it_at_once(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    init = {"type": "system", "subtype": "init", "tools": [t for t in TOOLS if "Gmail" not in t],
            "mcp_servers": [{"name": "claude.ai Gmail", "status": "needs-auth"}, *SERVERS[1:]]}
    run_routine(ws, monkeypatch, stdout=stream(init, result_event()))
    items = yaml.safe_load((ws / "_inbound/needs-you/routine.yaml").read_text())["items"]
    assert [i["id"] for i in items] == ["routine-reconnect-gmail"]
    assert routine.grant(ws)["reconnect"] == ["Gmail"] and not set(GMAIL_READ) & set(routine.read_grant(ws)[0])


def test_a_grant_from_a_broken_or_missing_file_is_empty_not_wide(tmp_path):
    ws = granted(tmp_path)
    (ws / "_meta" / "state" / "private" / "apps.yaml").write_text("apps: [unclosed", encoding="utf-8")
    assert routine.read_grant(ws) == ([], [], [])
    (ws / "_meta" / "state" / "private" / "apps.yaml").unlink()
    assert routine.read_grant(ws) == ([], [], [])
    ws = granted(tmp_path / "b")
    routine.probe_path().unlink()
    assert routine.read_grant(ws) == ([], [], [])


def test_no_routine_may_edit_the_grant_it_runs_under(tmp_path):
    s = routine.routine_settings(tmp_path, "", [tmp_path])
    assert "Edit(//**/_meta/state/private/apps.yaml)" in s["permissions"]["deny"]
    assert "Edit(~/.spaces/**)" in s["permissions"]["deny"]   # the probe and the settings themselves


# ----- a prompt that only runs while something is true ------------------------------------------
def test_a_backfill_prompt_costs_nothing_without_a_backfill(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    prompt = ws / "_meta" / "state" / "routines" / "backfill-night.md"
    prompt.write_text("---\nname: backfill-night\nschedule: \"0 1 * * *\"\nonly_while: backfill\n---\nRead the next part.\n",
                      encoding="utf-8")
    called = []
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    assert routine.main([str(prompt)], run=lambda *a, **k: called.append(a)) == 0 and called == []
    assert not routine.lock_path(ws).exists()
    prompt.write_text("---\nname: odd\nonly_while: the moon is full\n---\nx\n", encoding="utf-8")
    assert routine.main([str(prompt)], run=lambda *a, **k: called.append(a)) == 0 and called == []


# ----- the probe: what the scheduler's own run sees, and three checks ------------------------------
def probe_run(tools=TOOLS, servers=SERVERS):
    init = {"type": "system", "subtype": "init", "cwd": "x", "model": "claude-haiku-5-5", "apiKeySource": "none",
            "tools": list(tools), "mcp_servers": [dict(s) for s in servers]}
    return fake_run(stdout=stream(init, result_event("OK.")))


def test_the_probe_writes_what_it_saw_and_its_three_checks(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    before = time.time()
    assert routine.main(["--probe", str(ws)], run=probe_run()) == 0
    seen = json.loads(routine.probe_path().read_text(encoding="utf-8"))
    assert seen["tools"] == TOOLS and seen["ok"] is True and seen["stamp"] >= before and seen["workspace"] == str(ws.resolve())
    names = [c["name"] for c in seen["checks"]]
    assert names == ["reads Gmail", "reads Linear", "folder ada", "mail only to you"], names
    assert all(c["ok"] for c in seen["checks"]) and seen["notes"] == []


def test_the_send_check_proves_the_guard_refuses_a_stranger_and_lets_the_person_through(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    asked = []

    def hook(command, payload, shell=None):
        to = json.loads(payload)["tool_input"]["to"]
        asked.append((to, shell))
        return 0 if to == [ADA] else 2
    monkeypatch.setattr(routine, "run_hook", hook)
    assert routine.main(["--probe", str(ws)], run=probe_run()) == 0
    assert ([ADA], [proven_shell(), "-c"]) in asked and any(to != [ADA] for to, _ in asked)
    for broken in (lambda c, p, shell=None: 2, lambda c, p, shell=None: 0, lambda c, p, shell=None: 1,
                   lambda c, p, shell=None: None):   # blocks all, lets all through, crashes, no shell
        monkeypatch.setattr(routine, "run_hook", broken)
        assert routine.main(["--probe", str(ws)], run=probe_run()) == 1
        check = next(c for c in json.loads(routine.probe_path().read_text())["checks"] if c["name"] == "mail only to you")
        assert not check["ok"] and "send" in check["why"]


def test_without_a_mailbox_to_send_from_the_send_check_passes_and_says_why(tmp_path, monkeypatch):
    ws = granted(tmp_path, verdicts=[dict(GMAIL, account="ben@lanternworks.example"), LINEAR])
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    assert routine.main(["--probe", str(ws)], run=probe_run()) == 0
    seen = json.loads(routine.probe_path().read_text(encoding="utf-8"))
    check = next(c for c in seen["checks"] if c["name"] == "mail only to you")
    assert check["ok"] and len(seen["notes"]) == 1 and seen["notes"][0] in check["why"]


def test_a_routine_settings_file_without_the_guard_fails_the_send_check(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    paths = routine.write_allowlist(ws)
    assert paths and all(p.is_file() for p in paths)
    data = json.loads(paths[0].read_text(encoding="utf-8"))
    data.pop("hooks")
    paths[0].write_text(json.dumps(data), encoding="utf-8")
    assert routine.main(["--probe", str(ws)], run=probe_run()) == 1
    check = next(c for c in json.loads(routine.probe_path().read_text())["checks"] if c["name"] == "mail only to you")
    assert not check["ok"]


def test_an_allowlist_that_lacks_an_apps_reads_fails_its_check(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    path = routine.write_allowlist(ws)[0]
    data = json.loads(path.read_text(encoding="utf-8"))
    data["permissions"]["deny"].append(GMAIL_READ[0])
    data["permissions"]["deny"].append(GMAIL_READ[1])
    path.write_text(json.dumps(data), encoding="utf-8")
    assert routine.main(["--probe", str(ws)], run=probe_run()) == 1
    check = next(c for c in json.loads(routine.probe_path().read_text())["checks"] if c["name"] == "reads Gmail")
    assert not check["ok"]


def test_an_app_the_routines_cannot_read_fails_its_check_in_plain_words(tmp_path, monkeypatch):
    servers = [{"name": "claude.ai Gmail", "status": "needs-auth"}, *SERVERS[1:]]
    ws = granted(tmp_path)
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    assert routine.main(["--probe", str(ws)], run=probe_run([t for t in TOOLS if "Gmail" not in t], servers)) == 1
    seen = json.loads(routine.probe_path().read_text(encoding="utf-8"))
    check = next(c for c in seen["checks"] if c["name"] == "reads Gmail")
    assert not check["ok"] and "Gmail" in check["why"] and "mcp__" not in check["why"]
    items = yaml.safe_load((ws / "_inbound/needs-you/routine.yaml").read_text())["items"]
    assert [i["id"] for i in items] == ["routine-reconnect-gmail"]


def test_a_folder_the_routines_cannot_write_in_fails_its_check(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    real = Path.write_bytes

    def refuse(self, data):
        if self.name.startswith(".probe-"):
            raise PermissionError(13, "Permission denied")
        return real(self, data)
    monkeypatch.setattr(Path, "write_bytes", refuse)
    assert routine.main(["--probe", str(ws)], run=probe_run()) == 1
    check = next(c for c in json.loads(routine.probe_path().read_text())["checks"] if c["name"] == "folder ada")
    assert not check["ok"] and "write" in check["why"]
    assert not [p for p in (ws / "_meta" / "state" / "private").iterdir() if p.name.startswith(".probe-")]


def test_a_probe_that_cannot_run_says_why_and_keeps_the_tools_it_knew(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    monkeypatch.setattr(routine, "find_claude", lambda: None)
    assert routine.main(["--probe", str(ws)]) == 1
    seen = json.loads(routine.probe_path().read_text(encoding="utf-8"))
    assert seen["ok"] is False and seen["error"] and seen["tools"] == TOOLS and seen["checks"] == []
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    assert routine.main(["--probe", str(ws)], run=fake_run(stdout="Not logged in \u00b7 Please run /login\n", code=1)) == 1
    assert "init" in json.loads(routine.probe_path().read_text(encoding="utf-8"))["error"]


def test_the_probe_runs_as_a_routine_starts_with_every_root_added(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    brain = make_space(tmp_path / "José Pérez" / "lantern-works-brain", profile="brain", owner="Ada Okafor")
    onboard.remember_space(brain)
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    seen = {}
    init = {"type": "system", "subtype": "init", "tools": TOOLS, "mcp_servers": SERVERS}
    assert routine.main(["--probe", str(ws)], run=fake_run(seen, stdout=stream(init))) == 0
    assert seen["argv"][seen["argv"].index("--add-dir") + 1] == str(brain.resolve()) and Path(seen["cwd"]) == ws.resolve()
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    names = [c["name"] for c in json.loads(routine.probe_path().read_text(encoding="utf-8"))["checks"]]
    assert "folder lantern-works-brain" in names


def test_the_hook_shell_on_windows_is_git_bash_never_another_bash(tmp_path, monkeypatch):
    """Claude Code runs hooks with Git Bash on Windows; `bash` on the PATH there can be WSL's, which cannot
    start a Windows program by its path."""
    git = tmp_path / "Program Files" / "Git"
    (git / "cmd").mkdir(parents=True)
    (git / "bin").mkdir()
    (git / "cmd" / "git.exe").write_bytes(b"")
    (git / "bin" / "bash.exe").write_bytes(b"")
    monkeypatch.delenv("CLAUDE_CODE_GIT_BASH_PATH", raising=False)
    monkeypatch.setattr(routine.shutil, "which", lambda name: str(git / "cmd" / "git.exe") if name == "git" else
                        "C:/Windows/System32/bash.exe")
    assert routine.hook_shell(windows=True) == [str(git / "bin" / "bash.exe"), "-c"]
    monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", str(git / "bin" / "bash.exe"))
    monkeypatch.setattr(routine.shutil, "which", lambda name: None)
    assert routine.hook_shell(windows=True) == [str(git / "bin" / "bash.exe"), "-c"]
    monkeypatch.delenv("CLAUDE_CODE_GIT_BASH_PATH")
    assert routine.hook_shell(windows=True) is None   # no Git Bash: the guard cannot be proved, so the check fails
    assert routine.hook_shell(windows=False) == ["/bin/sh", "-c"]


# ----- the build says done only after the routines proved, twice, that they run on their own -------
from conftest import args   # noqa: E402
from test_start import finished_setup, no_outside   # noqa: E402

PASSED = [{"name": "reads Gmail", "ok": True, "why": "Gmail can be read"},
          {"name": "folder ada-okafor", "ok": True, "why": "read and written"},
          {"name": "mail only to you", "ok": True, "why": "the guard refuses a stranger"}]
SEND_FAILED = PASSED[:2] + [{"name": "mail only to you", "ok": False,
                             "why": "the guard that lets routines send mail only to you did not refuse a stranger"}]


def probing(monkeypatch, *runs) -> dict:
    """The one-shot probe task as the scheduler would run it: registered, started at once (each start
    writes the next of `runs` to the probe file, as `routine.py --probe` does; None writes nothing, and
    False is a scheduler that says no), and removed. Returns what happened, in order."""
    seen = {"events": [], "registered": [], "allowlist_before_second": None}
    runs = list(runs)

    def register(report, label, task, root, argv, **kw):
        seen["events"].append("register")
        seen["registered"].append((task, [str(a) for a in argv], kw))
        report.add(label, True, f"schtasks /Query /TN {task}", task)
        return {"name": task, "kind": "schtasks", "check": ["schtasks", "/Query", "/TN", task], "root": str(root)}

    def run_now(record, dry_run=False):
        seen["events"].append("start")
        if seen["events"].count("start") == 2:
            seen["allowlist_before_second"] = sorted(p.name for p in (Path.home() / ".spaces" / "routines").glob("*.json"))
        checks = runs.pop(0)
        if checks is False:
            return False
        if checks is not None:
            routine.write_probe({"tools": [], "mcp_servers": [], "checks": checks, "notes": [],
                                 "ok": all(c["ok"] for c in checks), "stamp": time.time()})
        return True

    def remove(report, root, state, task, dry_run, label="sync task removed"):
        seen["events"].append("remove")
        report.add(label, True, f"schtasks /Query /TN {task}", "removed")

    monkeypatch.setattr(onboard, "register_task", register)
    monkeypatch.setattr(onboard, "run_now", run_now)
    monkeypatch.setattr(offboard, "remove_sync_task", remove)
    monkeypatch.setattr(start, "_sleep", lambda seconds: None)
    return seen


def online_build(tmp_path, monkeypatch, slim_template):
    """A build that takes the online path to its end, while the spaces themselves are made offline (no
    host, no scheduler): only the routines' proof is online."""
    no_outside(monkeypatch)
    finished_setup()
    real = start.start
    monkeypatch.setattr(start, "start", lambda *a, **k: real(*a, **{**k, "offline": True}))
    return args(tmp_path, offline=False)


def test_build_says_done_only_after_the_second_probe_passes(tmp_path, monkeypatch, slim_template):
    given = online_build(tmp_path, monkeypatch, slim_template)
    seen = probing(monkeypatch, PASSED, SEND_FAILED)
    result = start.phase_build(given)
    assert result["state"] == "failed" and result["why"] == start.PROBE_FAILED   # plain; the check's words: the journal's
    assert "send" in result["detail"]                                            # detail (R45, Minor 3)
    assert seen["events"] == ["register", "start", "start", "remove"]
    rows = [s for s in result["failed"] if "mail only to you" in s["name"]]
    assert len(rows) == 1 and "refuse a stranger" in rows[0]["result"]   # the failed check is one step that names it
    assert result["probe"]["ok"] is False


def test_build_is_done_when_both_probes_pass_and_the_allowlist_was_written_between_them(tmp_path, monkeypatch, slim_template):
    given = online_build(tmp_path, monkeypatch, slim_template)
    seen = probing(monkeypatch, PASSED, PASSED)
    result = start.phase_build(given)
    assert result["state"] == "done", result
    assert result["probe"]["ok"] is True and [c["name"] for c in result["probe"]["checks"]] == [c["name"] for c in PASSED]
    (task, argv, when), = seen["registered"]
    workspace = Path(result["workspace"])
    assert task == "space-probe-ada-okafor" and argv == [str(workspace / "_meta" / "tools" / "routine.py"), "--probe", str(workspace)]
    assert when.get("times") and when.get("cron")
    assert seen["allowlist_before_second"] == ["backfill-night-ada-okafor.settings.json", "daily-chain-ada-okafor.settings.json",
                                               "sweep-chain-ada-okafor.settings.json"]
    assert seen["events"][-1] == "remove"


def test_the_probe_task_takes_the_isolated_runs_prefix(tmp_path, monkeypatch, slim_template):
    monkeypatch.setenv("SPACES_TASK_PREFIX", "space-test-1a2b-")
    given = online_build(tmp_path, monkeypatch, slim_template)
    seen = probing(monkeypatch, PASSED, PASSED)
    start.phase_build(given)
    assert seen["registered"][0][0] == "space-test-1a2b-probe-ada-okafor"


def test_a_scheduler_that_will_not_start_the_probe_is_one_plain_line_and_the_task_still_goes(tmp_path, monkeypatch, slim_template):
    given = online_build(tmp_path, monkeypatch, slim_template)
    seen = probing(monkeypatch, False)
    result = start.phase_build(given)
    assert result["state"] == "failed" and "scheduler" in result["why"] and "schtasks" not in result["why"]
    assert seen["events"] == ["register", "start", "remove"]


def test_a_probe_that_never_reports_fails_after_the_wait_and_the_task_still_goes(tmp_path, monkeypatch, slim_template):
    given = online_build(tmp_path, monkeypatch, slim_template)
    seen = probing(monkeypatch, None)
    clock = [0.0]
    monkeypatch.setattr(start, "_clock", lambda: clock[0])
    monkeypatch.setattr(start, "_sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    result = start.phase_build(given)
    minutes = start.PROBE_WAIT // 60
    assert result["state"] == "failed" and f"{minutes} minutes" in result["why"]
    assert start.PROBE_WAIT <= clock[0] <= start.PROBE_WAIT + 20 and seen["events"] == ["register", "start", "remove"]


def test_an_old_probe_file_is_not_taken_for_this_runs(tmp_path, monkeypatch, slim_template):
    given = online_build(tmp_path, monkeypatch, slim_template)
    routine.write_probe({"tools": [], "checks": PASSED, "ok": True, "stamp": time.time() - 60})
    probing(monkeypatch, None)
    clock = [0.0]
    monkeypatch.setattr(start, "_clock", lambda: clock[0])
    monkeypatch.setattr(start, "_sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    assert start.phase_build(given)["state"] == "failed"


def test_a_probe_that_could_not_run_claude_fails_the_build_with_its_reason(tmp_path, monkeypatch, slim_template):
    given = online_build(tmp_path, monkeypatch, slim_template)
    probing(monkeypatch, PASSED)
    real = onboard.run_now

    def errored(record, dry_run=False):
        real(record)
        routine.write_probe({"tools": [], "error": "claude printed no init event", "checks": [], "ok": False,
                             "stamp": time.time()})
        return True
    monkeypatch.setattr(onboard, "run_now", errored)
    result = start.phase_build(given)
    assert result["state"] == "failed" and "Claude" in result["why"]


def test_an_offline_build_proves_nothing_and_registers_no_probe(tmp_path, monkeypatch, slim_template):
    no_outside(monkeypatch)
    finished_setup()
    seen = probing(monkeypatch)
    result = start.phase_build(args(tmp_path))
    assert result["state"] == "done" and seen["events"] == [] and result["probe"] is None


def test_the_journal_says_in_one_plain_line_when_no_mailbox_can_send(tmp_path, monkeypatch, slim_template):
    import setup_journal
    given = online_build(tmp_path, monkeypatch, slim_template)
    note = "No mailbox of yours is connected, so the routines send no mail."
    checks = PASSED[:2] + [{"name": "mail only to you", "ok": True, "why": note}]
    seen = probing(monkeypatch, checks, checks)
    real = onboard.run_now

    def with_note(record, dry_run=False):
        real(record)
        data = json.loads(routine.probe_path().read_text(encoding="utf-8"))
        routine.write_probe({**data, "notes": [note]})
        return True
    monkeypatch.setattr(onboard, "run_now", with_note)
    result = start.phase_build(given)
    assert result["state"] == "done" and result["probe"]["notes"] == [note]
    assert setup_journal.load()["build"]["routines_note"] == note and seen["events"][-1] == "remove"


def test_a_first_probe_that_fails_a_check_still_gets_the_allowlist_and_the_second_decides(tmp_path, monkeypatch, slim_template):
    """The first probe may have read an allowlist an earlier build wrote: the one written between the two counts."""
    given = online_build(tmp_path, monkeypatch, slim_template)
    seen = probing(monkeypatch, SEND_FAILED, PASSED)
    result = start.phase_build(given)
    assert result["state"] == "done" and seen["events"] == ["register", "start", "start", "remove"]
    assert seen["allowlist_before_second"]


def test_without_a_shell_to_run_the_guard_in_no_send_tool_is_allowed(tmp_path, monkeypatch):
    """On a Windows without Git Bash, Claude Code runs a hook with PowerShell, where the guard's line does not
    parse and the call is not blocked: no send tool then, and one plain line says why."""
    ws = granted(tmp_path, shell=None)
    monkeypatch.setattr(routine, "hook_shell", lambda windows=None: None)
    got = routine.grant(ws)
    assert got["send"] == [] and got["notes"] == [routine.NO_SHELL] and "mcp__" not in got["notes"][0]
    s = routine.routine_settings(ws, "", [ws], ADA, GMAIL_READ, [], [GMAIL_SEND], known_tools=TOOLS)
    assert GMAIL_SEND in s["permissions"]["deny"] and GMAIL_SEND not in s["permissions"]["allow"]
    assert [h["matcher"] for h in s["hooks"]["PreToolUse"]] == ["Bash", routine.TASK_MATCHER, routine.CONNECTOR_MATCHER]


def test_a_run_lets_go_only_of_its_own_lock(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    monkeypatch.setattr(routine, "pid_alive", lambda pid: True)
    stale_lock(ws, pid=999_999)
    routine.release_lock(ws)
    assert routine.locked(ws)["pid"] == 999_999   # another run's lock stays
    assert routine.main(["--locked", str(ws)]) == 0


def test_a_run_whose_routine_cannot_start_claude_writes_its_outcome_and_holds_no_lock(tmp_path, monkeypatch):
    ws = granted(tmp_path)
    monkeypatch.setattr(routine, "find_claude", lambda: None)
    assert routine.main([str(ws / "_meta" / "state" / "routines" / "sweep-chain.md")]) == 127
    line = runs_of(ws)[-1]
    assert line["outcome"] == "stopped" and "not installed" in line["why"] and not routine.lock_path(ws).exists()


# ----- fix round 1 (Ruling R30) ---------------------------------------------------------------------
def test_a_probe_that_no_longer_sees_an_app_grants_none_of_its_tools(tmp_path, monkeypatch):
    """I-1: Ada removes Gmail from Claude. The next real `--probe` lists neither its server nor its tools:
    no Gmail read or send tool is granted any more, the check says Gmail is not connected, and every Gmail
    tool stays denied (`seen`), so it is never let through by being forgotten."""
    ws = granted(tmp_path, shell=None)
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    assert routine.main(["--probe", str(ws)], run=probe_run()) == 0
    read, _, send = routine.read_grant(ws)
    assert set(GMAIL_READ) <= set(read) and send == [GMAIL_SEND]
    gone = [t for t in TOOLS if "Gmail" not in t]
    assert routine.main(["--probe", str(ws)], run=probe_run(gone, SERVERS[1:])) == 1
    read, write, send = routine.read_grant(ws)
    assert not [t for t in read + write + send if "Gmail" in t] and read == [LINEAR_READ]
    data = json.loads(routine.probe_path().read_text(encoding="utf-8"))
    assert data["tools"] == gone and [s["name"] for s in data["mcp_servers"]] == ["claude.ai Microsoft 365", "claude.ai Linear"]
    assert set(GMAIL_READ + [GMAIL_SEND] + GMAIL_OTHER) <= set(data["seen"]) and "Read" not in data["seen"]
    check = next(c for c in data["checks"] if c["name"] == "reads Gmail")
    assert not check["ok"] and "not connected" in check["why"]
    for path in routine.write_allowlist(ws):
        profile = json.loads(path.read_text(encoding="utf-8"))
        assert set(GMAIL_READ + [GMAIL_SEND] + GMAIL_OTHER) <= set(profile["permissions"]["deny"])
        assert not [t for t in profile["permissions"]["allow"] if "Gmail" in t]


def test_a_tool_the_connector_stopped_offering_leaves_the_grant_at_the_next_probe(tmp_path, monkeypatch):
    ws = granted(tmp_path, shell=None)
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    routine.main(["--probe", str(ws)], run=probe_run([t for t in TOOLS if t != GMAIL_READ[1]]))
    assert routine.read_grant(ws)[0] == [GMAIL_READ[0], LINEAR_READ]
    assert GMAIL_READ[1] in json.loads(routine.write_allowlist(ws)[0].read_text(encoding="utf-8"))["permissions"]["deny"]


def test_a_runs_own_init_adds_what_it_saw_and_forgets_nothing(tmp_path, monkeypatch):
    """A run's init leaves out what its profile denies, so it cannot say a tool is gone: it only adds, and its
    servers' states count. Only `--probe` replaces the list."""
    ws = granted(tmp_path)
    new = "mcp__claude_ai_Linear__get_issue"
    init = {"type": "system", "subtype": "init", "tools": ["Read", LINEAR_READ, new], "mcp_servers": SERVERS}
    run_routine(ws, monkeypatch, stdout=stream(init, result_event()))
    data = json.loads(routine.probe_path().read_text(encoding="utf-8"))
    assert set(TOOLS) | {new} == set(data["tools"]) and new in data["seen"] and data["hook_shell"] == proven_shell()


def test_every_routine_profile_and_the_files_readers_keep_hooks_on(tmp_path):
    """I-2: a person who switched every hook off in their own settings would run a routine without its guards;
    the profile's own `"disableAllHooks": false` (command-line settings outrank theirs) keeps them on."""
    import reader
    ws = granted(tmp_path)
    paths = routine.write_allowlist(ws)
    assert paths and all(json.loads(p.read_text(encoding="utf-8"))["disableAllHooks"] is False for p in paths)
    folder = tmp_path / "reader"
    folder.mkdir()
    data = json.loads(reader._files_settings(folder, folder, []).read_text(encoding="utf-8"))
    assert data["disableAllHooks"] is False and data["hooks"]["PreToolUse"][0]["matcher"] == "Read"


@pytest.mark.parametrize("hooks_off", [True, None])
def test_a_profile_that_lets_hooks_be_switched_off_fails_the_send_check(tmp_path, monkeypatch, hooks_off):
    ws = granted(tmp_path)
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    path = routine.write_allowlist(ws)[0]
    data = json.loads(path.read_text(encoding="utf-8"))
    if hooks_off is None:
        data.pop("disableAllHooks")
    else:
        data["disableAllHooks"] = hooks_off
    path.write_text(json.dumps(data), encoding="utf-8")
    assert routine.main(["--probe", str(ws)], run=probe_run()) == 1
    seen = json.loads(routine.probe_path().read_text(encoding="utf-8"))
    check = next(c for c in seen["checks"] if c["name"] == "mail only to you")
    assert not check["ok"] and "switched off" in check["why"]


def test_a_stale_profile_fails_the_first_probe_but_the_allowlist_written_after_it_passes_the_second(tmp_path, monkeypatch):
    """What the build does: an older profile on disk fails the first probe's send check, but the guard a run
    will carry, in the shell Claude Code will use, is proven all the same; the allowlist written between the
    two probes then sends, and the second probe passes."""
    ws = granted(tmp_path, shell=None)
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    stale = json.loads(routine.write_allowlist(ws)[0].read_text(encoding="utf-8"))
    assert GMAIL_SEND in stale["permissions"]["deny"]   # written before any probe proved the shell
    assert routine.main(["--probe", str(ws)], run=probe_run()) == 1
    assert json.loads(routine.probe_path().read_text(encoding="utf-8"))["hook_shell"] == proven_shell()
    routine.write_allowlist(ws)
    assert routine.main(["--probe", str(ws)], run=probe_run()) == 0


def test_a_guard_that_does_not_hold_in_the_shell_proves_nothing(tmp_path, monkeypatch):
    ws = granted(tmp_path, shell=None)
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    monkeypatch.setattr(routine, "run_hook", lambda command, payload, shell=None: 0)   # lets a stranger through
    assert routine.main(["--probe", str(ws)], run=probe_run()) == 1
    assert json.loads(routine.probe_path().read_text(encoding="utf-8"))["hook_shell"] is None
    assert routine.grant(ws)["send"] == [] and routine.grant(ws)["notes"] == [routine.NOT_PROVEN]


def test_the_probe_writes_the_shell_it_proved_the_guard_in_and_a_run_pins_it(tmp_path, monkeypatch):
    """I-2: the send check runs the profile's own command in the shell `hook_shell` finds; once it passed,
    that shell is written down and each run's claude gets CLAUDE_CODE_GIT_BASH_PATH set to it on Windows."""
    ws = granted(tmp_path, shell=None)
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    assert routine.main(["--probe", str(ws)], run=probe_run()) == 0
    shell = json.loads(routine.probe_path().read_text(encoding="utf-8"))["hook_shell"]
    assert shell == routine.hook_shell()[0] and routine.grant(ws)["shell"] == shell
    monkeypatch.setenv("claude_code_git_bash_path", "C:/Other/bin/bash.exe")
    code, seen = run_routine(ws, monkeypatch, stdout=stream(result_event()))
    pinned = {k: v for k, v in seen["env"].items() if k.upper() == "CLAUDE_CODE_GIT_BASH_PATH"}
    if os.name == "nt":
        assert pinned == {"CLAUDE_CODE_GIT_BASH_PATH": shell}
    else:
        assert "CLAUDE_CODE_GIT_BASH_PATH" not in pinned


def test_routine_env_pins_the_proven_bash_on_windows_only():
    env = {"PATH": "/bin", "Claude_Code_Git_Bash_Path": "C:/Other/bash.exe", "ANTHROPIC_API_KEY": "k"}
    assert routine.routine_env(env, "C:/Git/bin/bash.exe", windows=True) == {"PATH": "/bin",
                                                                         "CLAUDE_CODE_GIT_BASH_PATH": "C:/Git/bin/bash.exe"}
    assert routine.routine_env(env, "/bin/sh", windows=False) == {"PATH": "/bin", "Claude_Code_Git_Bash_Path": "C:/Other/bash.exe"}
    assert routine.routine_env(env, "", windows=True) == {"PATH": "/bin", "Claude_Code_Git_Bash_Path": "C:/Other/bash.exe"}


def test_without_a_proven_shell_there_is_no_send_and_one_plain_line_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(routine, "hook_shell", lambda windows=None: [sys.executable, "-c"])
    for shell in (None, str(tmp_path / "gone" / "bash.exe")):
        got = routine.grant(granted(tmp_path / str(bool(shell)), shell=shell))
        assert got["send"] == [] and got["notes"] == [routine.NOT_PROVEN] and got["shell"] == ""
        assert "mcp__" not in got["notes"][0] and "_meta" not in got["notes"][0]
    ws = granted(tmp_path / "c", shell=None)
    profile = json.loads(routine.write_allowlist(ws)[0].read_text(encoding="utf-8"))
    assert GMAIL_SEND in profile["permissions"]["deny"] and [h["matcher"] for h in profile["hooks"]["PreToolUse"]] == [
        "Bash", routine.TASK_MATCHER, routine.CONNECTOR_MATCHER]


def test_a_routine_has_only_the_built_in_tools_it_needs(tmp_path):
    """I-5: `--tools` names them; the profile denies the rest by name, so the person's own allows for web,
    PowerShell or agents never reach a routine."""
    s = routine.routine_settings(tmp_path, "", [tmp_path], ADA, known_tools=TOOLS)
    for name in ("WebFetch", "WebSearch", "PowerShell", "NotebookEdit", "Monitor", "CronCreate", "Skill"):
        assert name in s["permissions"]["deny"], name
    assert not set(routine.ROUTINE_BUILTINS) & set(s["permissions"]["deny"])
    assert routine.ROUTINE_BUILTINS == ("Bash", "Read", "Edit", "Write", "Glob", "Grep", "ToolSearch", "Task")
    assert "Task" not in s["permissions"]["deny"]   # the second agent that checks route and distill decisions


def test_a_fault_inside_a_run_still_writes_its_outcome_and_exit_line(tmp_path, monkeypatch):
    """m-5: whatever goes wrong inside a run, its own line says it stopped, in plain words, and the log ends
    with the exit line; the lock goes."""
    ws = granted(tmp_path)
    real = routine.write_settings

    def broken(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(routine, "write_settings", broken)
    code, seen = run_routine(ws, monkeypatch, stdout=stream(result_event()))
    assert code == 1 and seen == {}
    line = runs_of(ws)[-1]
    assert line["outcome"] == "stopped" and "fault of its own" in line["why"] and "OSError" in line["why"]
    log = (Path.home() / ".spaces" / "logs" / "sweep-chain-ada.log").read_text(encoding="utf-8")
    assert log.rstrip().endswith("exit 1") and "outcome stopped" in log and not routine.lock_path(ws).exists()
    monkeypatch.setattr(routine, "write_settings", real)
    ws = granted(tmp_path / "b")
    monkeypatch.setattr(routine, "log_summary", lambda res: 1 / 0)   # after the outcome was written: written once
    code, _ = run_routine(ws, monkeypatch, stdout=stream(result_event()))
    assert code == 1 and [r["outcome"] for r in runs_of(ws)] == ["finished"]


def test_the_probe_runs_under_the_workspaces_lock(tmp_path, monkeypatch):
    """m-8: the probe writes probe.json and Needs you under the lock a run takes; while a run holds it, the
    probe asks nothing and says so."""
    ws = granted(tmp_path)
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    held = {}
    inner = probe_run()

    def run(argv, **kw):
        held.update(routine.locked(ws) or {})
        return inner(argv, **kw)
    assert routine.main(["--probe", str(ws)], run=run) == 0
    assert held["routine"] == "probe" and not routine.lock_path(ws).exists()
    assert routine.take_lock(ws, "daily-chain")
    called = []
    assert routine.main(["--probe", str(ws)], run=lambda *a, **k: called.append(a)) == 1 and called == []
    data = json.loads(routine.probe_path().read_text(encoding="utf-8"))
    assert data["busy"] is True and "busy" in data["error"] and data["tools"] == TOOLS
    assert routine.locked(ws)["routine"] == "daily-chain"   # the run keeps its lock
    routine.release_lock(ws)
    assert routine.main(["--probe", str(ws)], run=probe_run()) == 0
    assert "busy" not in json.loads(routine.probe_path().read_text(encoding="utf-8"))


# ----- I-3: the probe task is recorded before it exists, so a stray one is always found again ------------
class Killed(BaseException):
    """The build's process ended here: nothing after it runs, not even a `finally`."""


def scheduler(monkeypatch, kill=False, tasks=()) -> dict:
    """A scheduler that keeps its tasks: register adds, remove takes away. With `kill`, the build dies right
    after its probe task was started, before it removed it."""
    state = {"tasks": set(tasks), "events": [], "recorded_at_register": None}

    def register(report, label, task, root, argv, **kw):
        found = onboard.read_yaml(Path(root) / "_meta" / "state" / "onboard.yaml", {}) or {}
        state["recorded_at_register"] = found.get("probe_task")
        state["tasks"].add(task)
        state["events"].append(("register", task))
        report.add(label, True, f"schtasks /Query /TN {task}", task)
        return {"name": task, "kind": "schtasks", "check": ["schtasks", "/Query", "/TN", task], "root": str(root)}

    def run_now(record, dry_run=False):
        state["events"].append(("start", record["name"]))
        if kill:
            raise Killed()
        routine.write_probe({"tools": [], "mcp_servers": [], "checks": PASSED, "notes": [], "ok": True,
                             "stamp": time.time()})
        return True

    def remove(report, root, recorded, task, dry_run, label="sync task removed"):
        if kill:
            raise Killed()
        state["events"].append(("remove", task))
        state["tasks"].discard(task)
        report.add(label, True, f"schtasks /Query /TN {task}", "removed")

    monkeypatch.setattr(onboard, "register_task", register)
    monkeypatch.setattr(onboard, "run_now", run_now)
    monkeypatch.setattr(offboard, "remove_sync_task", remove)
    monkeypatch.setattr(start, "_sleep", lambda seconds: None)
    return state


def probe_record(ws):
    return (onboard.read_yaml(Path(ws) / "_meta" / "state" / "onboard.yaml", {}) or {}).get("probe_task")


def test_the_probe_task_is_recorded_before_it_is_registered_and_forgotten_once_removed(tmp_path, monkeypatch, slim_template):
    given = online_build(tmp_path, monkeypatch, slim_template)
    state = scheduler(monkeypatch)
    result = start.phase_build(given)
    assert result["state"] == "done", result
    assert state["recorded_at_register"] == {"name": "space-probe-ada-okafor"}
    assert state["tasks"] == set() and probe_record(result["workspace"]) is None


def test_a_build_killed_between_register_and_removal_is_cleaned_up_by_the_next_build(tmp_path, monkeypatch, slim_template):
    given = online_build(tmp_path, monkeypatch, slim_template)
    killed = scheduler(monkeypatch, kill=True)
    with pytest.raises(Killed):
        start.phase_build(given)
    assert killed["tasks"] == {"space-probe-ada-okafor"}   # the stray task: registered, never removed
    state = scheduler(monkeypatch, tasks=killed["tasks"])
    result = start.phase_build(given)
    assert result["state"] == "done", result
    assert state["events"][0] == ("remove", "space-probe-ada-okafor")   # the stray goes before a new one is made
    assert state["events"][1][0] == "register" and state["events"][-1] == ("remove", "space-probe-ada-okafor")
    assert state["tasks"] == set() and probe_record(result["workspace"]) is None


def stray(tmp_path, name="space-probe-ada") -> Path:
    root = make_space(tmp_path / "ada")
    onboard.write_yaml(root / "_meta" / "state" / "onboard.yaml", {
        "case": "person", "sync_task": {"name": "space-sync-ada"}, "routine_tasks": [{"name": "space-daily-chain-ada"}],
        "probe_task": {"name": name, "kind": "schtasks"}})
    return root


def windows_scheduler(monkeypatch, present) -> list:
    """schtasks as offboard and --check meet it: the `present` tasks answer /Query until they are deleted."""
    monkeypatch.setattr(onboard, "SYSTEM", "Windows")
    deleted = []

    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        if argv[:2] == ["schtasks", "/Delete"]:
            deleted.append(argv[-1])
        name = argv[argv.index("/TN") + 1] if "/TN" in argv else ""
        gone = argv[:2] == ["schtasks", "/Query"] and (name in deleted or name not in present)
        return subprocess.CompletedProcess(argv, 1 if gone else 0, "", "")
    monkeypatch.setattr(offboard, "run_cmd", fake)
    monkeypatch.setattr(onboard, "run_cmd", fake)
    return deleted


def test_offboard_removes_a_stray_probe_task_too(tmp_path, monkeypatch):
    root = stray(tmp_path)
    deleted = windows_scheduler(monkeypatch, {"space-sync-ada", "space-daily-chain-ada", "space-probe-ada"})
    report = offboard.run(root, online=True, dry_run=False)
    assert deleted == ["space-sync-ada", "space-daily-chain-ada", "space-probe-ada"]
    row = next(s for s in report.steps if s.name == "routine probe task space-probe-ada removed")
    assert row.done and probe_record(root) is None


def test_check_removes_a_stray_probe_task(tmp_path, monkeypatch):
    root = stray(tmp_path)
    deleted = windows_scheduler(monkeypatch, {"space-probe-ada"})
    monkeypatch.setattr(onboard.doctor, "diagnose", lambda *a, **k: [])
    rows = [s for s in onboard.check(root) if "probe" in s.name]
    assert deleted == ["space-probe-ada"]
    assert [(s.name, s.done) for s in rows] == [("routine probe task space-probe-ada removed", True)]
    assert probe_record(root) is None
    assert not [s for s in onboard.check(root) if "probe" in s.name]   # once gone, nothing more to say


@pytest.mark.parametrize("name", ["spaces--sync-ada", "space-sync-ada", "space-probe-someone-else", "OneDrive Update", ""])
def test_a_probe_record_that_is_not_this_spaces_probe_task_removes_nothing(tmp_path, monkeypatch, name):
    """onboard.yaml is a file a merge can change: only `<prefix>probe-<this space>` is ever removed by it."""
    root = stray(tmp_path, name=name)
    deleted = windows_scheduler(monkeypatch, {name})
    report = onboard.Report(case="check")
    offboard.remove_probe_task(report, root)
    assert deleted == [] and report.steps == []


def test_the_build_waits_longer_than_the_probe_may_take():
    """m-2: the probe's own claude may take PROBE_SECONDS; the build gives it more before it says it never reported."""
    assert start.PROBE_WAIT >= routine.PROBE_SECONDS + 60


def test_a_probe_error_is_a_plain_step_and_its_check_quotes_the_paths(tmp_path, monkeypatch, slim_template):
    """m-6: the step says what happened in plain words, never what claude printed; the check command can be
    pasted into a shell for a folder whose name has a space."""
    given = online_build(tmp_path, monkeypatch, slim_template)
    probing(monkeypatch, PASSED)
    real = onboard.run_now

    def errored(record, dry_run=False):
        real(record)
        routine.write_probe({"tools": [], "error": "claude printed no init event (exit 1): Traceback ...", "checks": [],
                             "ok": False, "stamp": time.time()})
        return True
    monkeypatch.setattr(onboard, "run_now", errored)
    result = start.phase_build(given)
    row = next(s for s in result["failed"] if s["name"] == start.PROBE_STEP)
    assert row["result"] == start.PROBE_ERROR and "init" not in row["result"] and "exit" not in row["result"]
    ws = Path(result["workspace"]).as_posix()
    assert f'"{ws}/_meta/tools/routine.py" --probe "{ws}"' in row["check"]


def test_a_probe_that_found_a_routine_running_says_so_in_plain_words(tmp_path, monkeypatch, slim_template):
    given = online_build(tmp_path, monkeypatch, slim_template)
    probing(monkeypatch, PASSED)
    real = onboard.run_now

    def busy(record, dry_run=False):
        real(record)
        routine.write_probe({"tools": [], "error": "another run is busy: sweep-chain", "busy": True, "checks": [],
                             "ok": False, "stamp": time.time()})
        return True
    monkeypatch.setattr(onboard, "run_now", busy)
    result = start.phase_build(given)
    assert result["state"] == "failed" and result["why"] == start.PROBE_BUSY


# ----- one computer, one runner, and a pause (Task 14) ------------------------------------------------
import onboarding_page   # noqa: E402
from conftest import NOW   # noqa: E402


def nothing_outside(monkeypatch):
    """claude is installed and no scheduler is asked anything: a test that would reach one fails."""
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    monkeypatch.setattr(onboard, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))


def on(monkeypatch, machine: str) -> None:
    monkeypatch.setattr(routine, "this_machine", lambda: machine)


def test_a_second_computer_syncs_but_schedules_no_routines(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    monkeypatch.setattr(routine, "this_machine", lambda: "m-desktop")
    routine.claim_host(ws, "os", NOW)
    monkeypatch.setattr(routine, "this_machine", lambda: "m-laptop")
    nothing_outside(monkeypatch)
    monkeypatch.setattr(onboard, "register_task", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    report = onboard.Report(case="test")
    assert onboard.register_routines(report, ws) == []
    assert "run my routines here" in report.steps[-1].result
    assert report.steps[-1].name.startswith("routine") and report.steps[-1].done   # filed under Routines, failing nothing
    assert routine.host(ws)["machine"] == "m-desktop"   # the laptop took nothing


def test_a_run_on_a_machine_that_is_not_the_host_never_calls_claude(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    monkeypatch.setattr(routine, "this_machine", lambda: "m-desktop"); routine.claim_host(ws, "os", NOW)
    monkeypatch.setattr(routine, "this_machine", lambda: "m-laptop")
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    called = []
    assert routine.main([str(ws / "_meta/state/routines/sweep-chain.md")], run=lambda *a, **k: called.append(a)) == 0
    assert called == [] and not (ws / "_meta/state/runs.jsonl").exists()
    log = (Path.home() / ".spaces" / "logs" / "sweep-chain-ada.log").read_text(encoding="utf-8")
    assert len(log.strip().splitlines()) == 1 and "runs on" in log   # one log line, no run line
    assert routine.locked(ws) is None


def test_routines_here_moves_the_host_and_the_old_machine_stops_on_its_own(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    monkeypatch.setattr(routine, "this_machine", lambda: "m-desktop"); routine.claim_host(ws, "os", NOW)
    monkeypatch.setattr(routine, "this_machine", lambda: "m-laptop")
    nothing_outside(monkeypatch)
    seen = probing(monkeypatch, PASSED, PASSED)
    assert onboard.main([str(ws), "--routines-here"]) == 0
    assert seen["events"].count("start") == 2   # the probe ran twice, as the build runs it
    names = [task for task, _, _ in seen["registered"]]
    assert "probe" in names[0] and len(names) == 4 and not any("probe" in n for n in names[1:])   # proved, then registered
    assert routine.host(ws)["machine"] == "m-laptop"
    monkeypatch.setattr(routine, "this_machine", lambda: "m-desktop")
    assert routine.gate(ws, NOW).startswith("runs on")


def test_an_install_on_desktop_scheduled_tasks_is_not_scheduled_twice(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    onboard.write_yaml(ws / "_meta/state/onboard.yaml", {"routine_checks": [{"name": "routine scheduled 1", "check": "test -f x"}]})
    nothing_outside(monkeypatch)
    assert onboard.register_routines(onboard.Report(case="test"), ws) == []
    assert routine.host(ws) is None   # nothing claimed: the app's own tasks run them, wherever they are


def harness_tasks(tmp_path, ws) -> list:
    """Three of the app's own scheduled tasks hold the routines, recorded with their read-backs."""
    paths = [tmp_path / "scheduled-tasks" / name / "SKILL.md" for name in ("night", "daily", "sweep")]
    for path in paths:
        path.parent.mkdir(parents=True)
        path.write_text("the routine\n", encoding="utf-8")
    onboard.write_yaml(ws / "_meta/state/onboard.yaml", {"routine_checks": [
        {"name": f"routine scheduled {n}", "check": f"test -f {p.as_posix()}"} for n, p in enumerate(paths, 1)]})
    return [p.as_posix() for p in paths]


def test_harness_tasks_are_named_for_turning_off_only_after_the_probe_passes(tmp_path, monkeypatch, capsys):
    ws = workspace(tmp_path)
    paths = harness_tasks(tmp_path, ws)
    before = onboard.read_yaml(ws / "_meta/state/onboard.yaml")
    nothing_outside(monkeypatch)
    seen = probing(monkeypatch, PASSED, SEND_FAILED)
    assert onboard.main([str(ws), "--routines-here"]) == 1
    assert "turn off" not in capsys.readouterr().out
    assert [task for task, _, _ in seen["registered"] if "probe" not in task] == []   # a failed probe registers nothing
    assert routine.host(ws) is None and onboard.read_yaml(ws / "_meta/state/onboard.yaml") == before   # the old runner stays
    probing(monkeypatch, PASSED, PASSED)
    assert onboard.main([str(ws), "--routines-here"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [line for line in out if line.startswith("turn off")] == [f"turn off: {p}" for p in paths]
    assert routine.host(ws)["runner"] == "os" and routine.gate(ws, NOW) is None
    state = onboard.read_yaml(ws / "_meta/state/onboard.yaml")
    assert "routine_checks" not in state and len(state["routine_tasks"]) == 3   # the app's tasks are no longer read back


def test_a_pause_until_morning_skips_runs_and_shows_under_waiting(tmp_path, monkeypatch):
    ws = workspace(tmp_path); routine.claim_host(ws, "os", NOW)
    onboard.main([str(ws), "--pause"])
    assert routine.gate(ws, NOW).startswith("paused until")
    texts = [r["text"] for r in onboarding_page.sections(ws, {}, NOW)["waiting"]]
    assert any(t.startswith("Routines paused until") for t in texts)
    onboard.main([str(ws), "--resume"])
    assert routine.gate(ws, NOW) is None


def test_a_paused_run_writes_paused_and_never_calls_claude_nor_reads_the_history(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    routine.claim_host(ws, "os", NOW)
    routine.set_pause(ws, NOW + dt.timedelta(hours=8))
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    monkeypatch.setattr(routine, "next_part", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a part was read")))
    monkeypatch.setattr(routine, "_now", lambda: NOW + dt.timedelta(hours=1))
    called = []
    assert routine.main([str(ws / "_meta/state/routines/sweep-chain.md")], run=lambda *a, **k: called.append(a)) == 0
    line, = runs_of(ws)
    assert called == [] and line["outcome"] == "paused" and line["routine"] == "sweep-chain" and line["cost_tokens"] == 0
    assert line["why"].startswith("paused until")
    monkeypatch.setattr(routine, "_now", lambda: NOW + dt.timedelta(hours=9))   # the pause is over
    monkeypatch.setattr(routine, "next_part", lambda *a, **k: (None, ""))
    routine.main([str(ws / "_meta/state/routines/sweep-chain.md")], run=fake_run(stdout=stream(result_event())))
    assert [r["outcome"] for r in runs_of(ws)] == ["paused", "finished"]


def test_a_pause_that_has_passed_is_no_pause(tmp_path):
    ws = workspace(tmp_path)
    routine.claim_host(ws, "os", NOW)
    routine.set_pause(ws, NOW - dt.timedelta(minutes=1))
    assert routine.gate(ws, NOW) is None
    assert not any(r["text"].startswith("Routines paused") for r in onboarding_page.sections(ws, {}, NOW)["waiting"])


class _OnWindows(dt.datetime):
    """A moment as Windows turns it into its own local time: past the year 3000 it cannot (OSError)."""

    def astimezone(self, tz=None):
        if tz is None and self.year > 3000:
            raise OSError(22, "Invalid argument")
        return super().astimezone(tz)


def test_a_pause_past_what_this_computers_clock_can_say_still_holds_the_run(tmp_path, monkeypatch):
    """A pause far ahead holds the run, the safe side, said as the record keeps it, and never crashes the hold."""
    ws = workspace(tmp_path)
    routine.claim_host(ws, "os", NOW)
    routine.set_pause(ws, dt.datetime(3001, 1, 2, 6, 30, tzinfo=dt.timezone.utc))
    real = routine._moment
    monkeypatch.setattr(routine, "_moment", lambda value: (lambda m: m and _OnWindows.fromisoformat(m.isoformat()))(
        real(value)))
    with pytest.raises(OSError):
        routine.paused_until(routine.host(ws), NOW).astimezone()   # what the hold met on Windows
    assert routine._held(ws, NOW) == ("paused", "paused until 3001-01-02 06:30")
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    called = []
    assert routine.main([str(ws / "_meta/state/routines/sweep-chain.md")], run=lambda *a, **k: called.append(a)) == 0
    line, = runs_of(ws)
    assert called == [] and line["outcome"] == "paused" and line["why"] == "paused until 3001-01-02 06:30"


@pytest.mark.parametrize("error", [OSError(22, "Invalid argument"), OverflowError("date value out of range")])
def test_a_pause_without_a_zone_this_computers_clock_cannot_place_reads_as_unclear(tmp_path, monkeypatch, error):
    """A moment written without a zone is this computer's own time, and its clock may not place one (Windows raises
    OSError for 9999-12-31T00:00:00): that pause is no moment, so the record reads as unclear and holds every run,
    never a crash in reading the record or in the hold."""
    import types

    class Unplaced(dt.datetime):
        def astimezone(self, tz=None):
            if tz is None and self.tzinfo is None:
                raise error
            return super().astimezone(tz)
    ws = workspace(tmp_path)
    routine.claim_host(ws, "os", NOW)
    path = ws / routine.HOST
    naive = path.read_text(encoding="utf-8").replace("paused_until: null", 'paused_until: "9999-12-31T00:00:00"')
    path.write_text(naive, encoding="utf-8")
    monkeypatch.setattr(routine, "dt", types.SimpleNamespace(**{**vars(dt), "datetime": Unplaced}))
    assert routine._moment("9999-12-31T00:00:00") is None
    assert routine._moment("9999-12-31T00:00:00+00:00") is not None   # a moment with its zone needs no clock
    assert routine.host(ws) == {"unclear": True}
    assert routine._held(ws, NOW) == ("away", routine.UNCLEAR)


def test_the_record_is_one_line_per_key_with_the_claim_apart_from_the_pause(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    monkeypatch.setattr(routine, "machine_name", lambda: "Ada's \"desk\" PC: 100% \\ yes")
    routine.claim_host(ws, "os", NOW)
    routine.set_pause(ws, NOW + dt.timedelta(hours=2))
    text = (ws / routine.HOST).read_text(encoding="utf-8").splitlines()
    lines = [line for line in text if not line.startswith("#")]
    assert [line.split(":", 1)[0] for line in lines] == ["machine", "name", "runner", "since", "paused_until"]
    assert text.index(lines[-1]) - text.index(lines[3]) == 2   # a line between the claim and the pause
    got = routine.host(ws)
    assert got["machine"] == routine.this_machine() and got["runner"] == "os" and "desk" in got["name"]
    assert " " not in got["name"] and routine.paused_until(got, NOW) is not None


@pytest.mark.parametrize("merged", [
    # two computers claimed before either synced, merged line by line keeping both (a union merge)
    'machine: "m-desktop"\nname: "DESK"\nrunner: "os"\nsince: "2026-10-12T09:00:00+00:00"\n'
    'machine: "m-laptop"\nname: "LAPTOP"\nrunner: "os"\nsince: "2026-10-12T09:05:00+00:00"\npaused_until: null\n',
    # a merge git left half done
    '<<<<<<< HEAD\nmachine: "m-desktop"\n=======\nmachine: "m-laptop"\n>>>>>>> origin/main\nrunner: "os"\n',
    'machine: "m-desktop"\nrunner: "cron"\n', 'machine: 42\nrunner: "os"\n', "- machine\n", "",
    'machine: "m-desktop"\nrunner: "os"\npaused_until: "next week"\n'])
def test_a_record_that_is_not_one_record_runs_nowhere_and_says_so(tmp_path, monkeypatch, merged):
    ws = workspace(tmp_path)
    (ws / routine.HOST).write_text(merged, encoding="utf-8")
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    for machine in ("m-desktop", "m-laptop"):
        on(monkeypatch, machine)
        assert routine.host(ws) == {"unclear": True}
        called = []
        assert routine.main([str(ws / "_meta/state/routines/daily-chain.md")], run=lambda *a, **k: called.append(a)) == 0
        assert called == [] and not (ws / "_meta/state/runs.jsonl").exists()
    texts = [r["text"] for r in onboarding_page.sections(ws, {}, NOW)["waiting"]]
    assert any("unclear" in t and "run my routines here" in t for t in texts)


def test_routines_the_apps_own_tasks_run_never_run_as_os_tasks_too(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    routine.claim_host(ws, "harness", NOW)
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    called = []
    assert routine.main([str(ws / "_meta/state/routines/daily-chain.md")], run=lambda *a, **k: called.append(a)) == 0
    assert called == [] and routine.gate(ws, NOW) is not None


def test_a_move_keeps_a_pause_still_to_come(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    on(monkeypatch, "m-desktop")
    routine.claim_host(ws, "os", NOW)
    routine.set_pause(ws, NOW + dt.timedelta(days=2))
    on(monkeypatch, "m-laptop")
    routine.claim_host(ws, "os", NOW + dt.timedelta(hours=1))
    assert routine.gate(ws, NOW + dt.timedelta(hours=1)).startswith("paused until")
    assert routine.paused_until(routine.host(ws), NOW + dt.timedelta(days=3)) is None


def test_the_probe_runs_on_any_computer(tmp_path, monkeypatch):
    """`--routines-here` probes a computer before it is the host: the probe is never gated."""
    ws = workspace(tmp_path)
    on(monkeypatch, "m-desktop"); routine.claim_host(ws, "os", NOW)
    on(monkeypatch, "m-laptop")
    probed = []
    monkeypatch.setattr(routine, "probe", lambda workspace, run=None: probed.append(workspace) or 0)
    assert routine.main(["--probe", str(ws)]) == 0 and probed == [ws]


def test_no_routine_may_move_pause_or_unregister_the_routines(tmp_path):
    ws = workspace(tmp_path)
    rules = routine.routine_settings(ws, "", [ws])["permissions"]
    for path in ("_meta/state/routine-host.yaml", "_meta/state/onboard.yaml"):
        assert path in routine.PROTECTED_EDITS and f"Edit(//**/{path})" in rules["deny"]


def test_no_routine_may_edit_a_mounts_git_file(tmp_path):
    """A mount's .git is a file that names its git folder; a status in the root runs that folder's config."""
    ws = workspace(tmp_path)
    assert "Edit(//**/.git)" in routine.routine_settings(ws, "", [ws])["permissions"]["deny"]


def test_without_a_record_a_build_claims_this_computer_once_its_tasks_are_registered(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    nothing_outside(monkeypatch)
    probing(monkeypatch)
    records = onboard.register_routines(onboard.Report(case="test"), ws)
    assert len(records) == 3
    got = routine.host(ws)
    assert got["machine"] == routine.this_machine() and got["runner"] == "os" and got["paused_until"] is None
    since = got["since"]
    onboard.register_routines(onboard.Report(case="test"), ws)
    assert routine.host(ws)["since"] == since   # a record of this computer is kept as it is


def test_a_failed_move_with_nothing_recorded_registers_no_routine_task(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    nothing_outside(monkeypatch)
    seen = probing(monkeypatch, PASSED, SEND_FAILED)
    assert onboard.main([str(ws), "--routines-here"]) == 1
    assert [task for task, _, _ in seen["registered"] if "probe" not in task] == []   # nothing runs beside the old runner
    assert routine.host(ws) is None and "routine_tasks" not in (onboard.read_yaml(ws / "_meta/state/onboard.yaml", {}) or {})


def desktop_hosts(ws, monkeypatch) -> list:
    """The desktop runs Ada's routines through its launch agents; this computer is the laptop."""
    on(monkeypatch, "m-desktop")
    routine.claim_host(ws, "os", NOW)
    desk = [{"name": "space-daily-chain-ada", "kind": "launchd", "label": "com.spaces.space-daily-chain-ada"}]
    state = onboard.read_yaml(ws / "_meta/state/onboard.yaml", {}) or {}
    onboard.write_yaml(ws / "_meta/state/onboard.yaml", {**state, "routine_tasks": desk})
    on(monkeypatch, "m-laptop")
    return desk


def test_a_failed_move_leaves_the_host_and_its_task_records_alone(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    desk = desktop_hosts(ws, monkeypatch)
    nothing_outside(monkeypatch)
    probing(monkeypatch, PASSED, SEND_FAILED)
    assert onboard.main([str(ws), "--routines-here"]) == 1
    assert routine.host(ws)["machine"] == "m-desktop"
    assert onboard.read_yaml(ws / "_meta/state/onboard.yaml")["routine_tasks"] == desk


def test_a_registration_that_fails_after_the_probe_passed_claims_nothing_and_takes_its_tasks_back(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    desk = desktop_hosts(ws, monkeypatch)
    nothing_outside(monkeypatch)
    seen = probing(monkeypatch, PASSED, PASSED)
    registering = onboard.register_task

    def refusing(report, label, task, root, argv, **kw):
        if "daily-chain" in task:
            report.add(label, False, f"schtasks /Query /TN {task}", "Access is denied.")
            return {"name": task, "kind": "schtasks"}
        return registering(report, label, task, root, argv, **kw)
    monkeypatch.setattr(onboard, "register_task", refusing)
    removed, removing = [], offboard.remove_sync_task
    monkeypatch.setattr(offboard, "remove_sync_task",
                        lambda report, root, state, task, dry_run, label="": removed.append(task) or
                        removing(report, root, state, task, dry_run, label))
    assert onboard.main([str(ws), "--routines-here"]) == 1
    made = [task for task, _, _ in seen["registered"] if "probe" not in task]
    assert made and all(task in removed for task in made)   # what it did register is taken back
    assert routine.host(ws)["machine"] == "m-desktop"
    assert onboard.read_yaml(ws / "_meta/state/onboard.yaml")["routine_tasks"] == desk


CLAIM = 'machine: "m-desktop"\nname: "DESK"\nrunner: "os"\nsince: "2026-10-12T09:00:00+00:00"\n'
LATER, EARLIER = 'paused_until: "2026-10-27T06:00:00+00:00"\n', 'paused_until: "2026-10-25T06:00:00+00:00"\n'


@pytest.mark.parametrize("pauses, kept", [(EARLIER + LATER, "2026-10-27T06:00:00+00:00"),
                                          (LATER + EARLIER, "2026-10-27T06:00:00+00:00"),
                                          ('paused_until: null\n' + EARLIER, "2026-10-25T06:00:00+00:00")])
def test_two_pauses_merged_keep_the_later_one(tmp_path, pauses, kept):
    """Two computers paused before either synced: a union merge keeps both lines, and the later pause stands."""
    ws = workspace(tmp_path)
    (ws / routine.HOST).write_text(CLAIM + pauses, encoding="utf-8")
    got = routine.host(ws)
    assert got["machine"] == "m-desktop" and got["paused_until"] == kept


def test_a_pause_twice_beside_a_claim_twice_or_a_pause_that_is_no_moment_is_still_unclear(tmp_path):
    ws = workspace(tmp_path)
    for text in (CLAIM + CLAIM.replace("desktop", "laptop") + 'paused_until: null\n' + EARLIER,
                 CLAIM + EARLIER + 'paused_until: "soon"\n'):
        (ws / routine.HOST).write_text(text, encoding="utf-8")
        assert routine.host(ws) == {"unclear": True}


def test_setup_and_reader_runs_drop_the_desktop_sessions_own_claude_variables():
    """A run started from the desktop session gets none of that session's own settings (its effort, entry point,
    child-session and SDK variables), so setup's runs are the runs the routines and cli-facts saw; Git Bash's path and
    the person's configuration folder stay (R45, I-3; dry run B M-5). Every caller goes through `clean_env`."""
    env = {"PATH": "/bin", "CLAUDECODE": "1", "CLAUDE_EFFORT": "xhigh", "CLAUDE_CODE_ENTRYPOINT": "claude-desktop",
           "CLAUDE_CODE_CHILD_SESSION": "1", "claude_code_sse_port": "4000", "CLAUDE_AGENT_SDK_VERSION": "0.2",
           "CLAUDE_CODE_GIT_BASH_PATH": "C:/Git/bin/bash.exe", "CLAUDE_CONFIG_DIR": "/home/ada/.claude",
           "CLAUDE_CODE_OAUTH_TOKEN": "o", "CLAUDEX": "kept"}
    assert set(routine.clean_env(env)) == {"PATH", "CLAUDE_CODE_GIT_BASH_PATH", "CLAUDE_CONFIG_DIR", "CLAUDEX"}
    pinned = routine.routine_env(env, shell="C:/Program Files/Git/bin/bash.exe", windows=True)
    assert pinned["CLAUDE_CODE_GIT_BASH_PATH"] == "C:/Program Files/Git/bin/bash.exe" and "CLAUDE_EFFORT" not in pinned


def test_a_client_certificate_the_network_asks_for_stays_in_every_run():
    """NB-4: dropping every CLAUDE_CODE_ name took the client certificate a company network may require (mTLS)
    with it, and then no run reaches the API at all; those three names stay, beside Git Bash and the config."""
    mtls = {"CLAUDE_CODE_CLIENT_CERT": "/etc/ada.pem", "CLAUDE_CODE_CLIENT_KEY": "/etc/ada.key",
            "CLAUDE_CODE_CLIENT_KEY_PASSPHRASE": "p"}
    assert routine.clean_env({"PATH": "/bin", "CLAUDE_CODE_ENTRYPOINT": "claude-desktop", **mtls}) == {"PATH": "/bin", **mtls}




# ----- the final review (Ruling R45): guard hooks with a time limit, the lock's process, one machine id -------------
def test_every_guard_hook_has_its_own_time_limit_well_above_the_guards(tmp_path):
    """C-1: a hook Claude Code cuts off at its time limit lets the call through (dry run A); each guard hook says how
    long it may take, and each guard ends itself, refusing, after a third of that."""
    ws = granted(tmp_path)
    paths = routine.write_allowlist(ws)
    assert paths
    for path in paths:
        entries = json.loads(path.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        assert [e["matcher"] for e in entries] == ["Bash", GMAIL_SEND, routine.TASK_MATCHER, routine.CONNECTOR_MATCHER]
        assert all(h["timeout"] == routine.GUARD_TIMEOUT for e in entries for h in e["hooks"])
    assert routine.GUARD_TIMEOUT == 30


def lock_of(ws, hours_ago: float, pid_started=None, pid=999_999) -> Path:
    """A lock a run wrote `hours_ago`, with its process's start time when given (a lock written before R45 has none)."""
    held = {"pid": pid, "routine": "sweep-chain",
            "started": (dt.datetime.now().astimezone() - dt.timedelta(hours=hours_ago)).isoformat(timespec="seconds")}
    path = routine.lock_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({**held, **({"pid_started": pid_started} if pid_started else {})}), encoding="utf-8")
    return path


def test_a_lock_keeps_its_processs_start_time_beside_its_pid(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    monkeypatch.setattr(routine, "process_started", lambda pid: "t-1" if pid == os.getpid() else None)
    assert routine.take_lock(ws, "sweep-chain")
    held = yaml.safe_load(routine.lock_path(ws).read_text(encoding="utf-8"))
    assert held["pid"] == os.getpid() and held["pid_started"] == "t-1"
    assert "pid_started" not in routine.locked(ws)   # what `--locked` prints stays as it was


def test_a_run_whose_lid_was_closed_for_hours_keeps_its_lock(tmp_path, monkeypatch):
    """I-8: the run's process still lives, started when it did, however old its lock: a catch-up run on waking
    finds it held and leaves."""
    ws = workspace(tmp_path)
    monkeypatch.setattr(routine, "pid_alive", lambda pid: True)
    monkeypatch.setattr(routine, "process_started", lambda pid: "t-1")
    lock_of(ws, hours_ago=routine.LOCK_HOURS + 5, pid_started="t-1")
    assert routine.locked(ws)["routine"] == "sweep-chain" and not routine.take_lock(ws, "daily-chain")
    lock_of(ws, hours_ago=-1, pid_started="t-1")   # a clock set back while it ran: still that process
    assert routine.locked(ws)["routine"] == "sweep-chain"


def test_a_live_pid_that_started_at_another_time_is_a_reused_one(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    monkeypatch.setattr(routine, "pid_alive", lambda pid: True)
    monkeypatch.setattr(routine, "process_started", lambda pid: "t-2" if pid == 999_999 else "t-self")
    lock_of(ws, hours_ago=0.1, pid_started="t-1")   # fresh, but its number is another process's now
    assert routine.locked(ws) is None and routine.take_lock(ws, "daily-chain")
    assert routine.locked(ws)["routine"] == "daily-chain"


def test_a_lock_whose_process_is_gone_is_dead_whatever_its_start_time(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    monkeypatch.setattr(routine, "pid_alive", lambda pid: pid == os.getpid())
    monkeypatch.setattr(routine, "process_started", lambda pid: "t-1")
    lock_of(ws, hours_ago=0.1, pid_started="t-1")
    assert routine.locked(ws) is None and routine.take_lock(ws, "daily-chain")


def test_without_a_start_time_to_compare_the_age_rule_decides(tmp_path, monkeypatch):
    """A lock written before R45 has no start time, and a system may not say one: the two-hour rule stays."""
    ws = workspace(tmp_path)
    monkeypatch.setattr(routine, "pid_alive", lambda pid: True)
    for written, now in (("t-1", None), (None, "t-1"), (None, None)):
        monkeypatch.setattr(routine, "process_started", lambda pid, now=now: now)
        lock_of(ws, hours_ago=routine.LOCK_HOURS - 0.5, pid_started=written)
        assert routine.locked(ws)["routine"] == "sweep-chain", (written, now)
        lock_of(ws, hours_ago=routine.LOCK_HOURS + 0.1, pid_started=written)
        assert routine.locked(ws) is None, (written, now)


def test_a_processs_start_time_is_read_from_the_system():
    mine = routine.process_started(os.getpid())
    assert isinstance(mine, str) and mine and routine.process_started(os.getpid()) == mine
    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
    try:
        other = routine.process_started(child.pid)
        assert isinstance(other, str) and other and other != mine
    finally:
        child.stdin.close()
        child.wait(timeout=30)
    for pid in (0, -4, 2 ** 40):
        assert routine.process_started(pid) is None


def test_this_computer_keeps_one_id_made_once_whatever_its_name_becomes(tmp_path, monkeypatch):
    """Minor 6: a Mac whose name follows the network would stop matching its own host record. The id is made once,
    from today's value (so every record already written still matches), and kept under ~/.spaces."""
    import socket
    import sync
    monkeypatch.setattr(socket, "gethostname", lambda: "Adas-MacBook.local")
    before = sync.hostname()
    kept = Path.home() / ".spaces" / "machine-id"
    assert not kept.exists()
    assert routine.this_machine() == before and kept.read_text(encoding="utf-8").strip() == before
    ws = workspace(tmp_path)
    routine.claim_host(ws, "os", NOW)
    monkeypatch.setattr(socket, "gethostname", lambda: "Adas-MacBook-2.home")   # the network renamed it
    assert sync.hostname() != before and routine.this_machine() == before
    assert routine.gate(ws, NOW) is None and routine.host(ws)["machine"] == before


def test_a_machine_id_file_that_holds_no_id_is_made_again(tmp_path):
    import sync
    kept = Path.home() / ".spaces" / "machine-id"
    kept.parent.mkdir(parents=True, exist_ok=True)
    for text in ("", "not an id\n", "m-\n", "m-" + "x" * 80 + "\n", "m-ok\nm-two\n",
                 f"{routine.CLOUD_MACHINE}\n"):   # the cloud's machine is no computer's id: a move would record it
        kept.write_text(text, encoding="utf-8")
        assert routine.this_machine() == sync.hostname() and kept.read_text(encoding="utf-8").strip() == sync.hostname()
    kept.write_text("m-1a2b3c4d\n", encoding="utf-8")   # one made on this computer before: kept as it is
    assert routine.this_machine() == "m-1a2b3c4d"


def test_the_probe_sees_the_built_in_tools_a_routine_sees(tmp_path):
    """F-8 (dry run A): without `--tools` the probe's init listed 32 built-ins, a routine has eight; it now asks with
    the routine's own, so a version that drops or renames one shows in the probe."""
    seen = {}
    routine.probe_tools("claude", tmp_path, [tmp_path / "brain"], run=fake_run(seen, stdout=json.dumps(init_line()) + "\n"))
    argv, count = seen["argv"], len(routine.ROUTINE_BUILTINS)
    at = argv.index("--tools")
    assert argv[at + 1:at + 1 + count] == list(routine.ROUTINE_BUILTINS) and argv[at + 1 + count].startswith("--")
    assert argv[:at] == ["claude", "-p", *routine.REFUSE, *routine.JSON_OUT]


def test_a_run_held_back_by_its_condition_or_a_pause_leaves_its_line_and_starts_no_claude(tmp_path, monkeypatch):
    """F-11 (dry run A): a nightly run with nothing to read left no trace, and a paused workspace's history run
    wrote no `paused`. Both are now said in the log first, the pause in the run's line too, and no claude starts."""
    ws = granted(tmp_path)
    prompt = ws / "_meta" / "state" / "routines" / "backfill-night.md"
    prompt.write_text("---\nname: backfill-night\nonly_while: backfill\n---\nRead the next part.\n", encoding="utf-8")
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    called = []
    log = Path.home() / ".spaces" / "logs" / f"backfill-night-{routine.spaceyaml.load(ws).name}.log"
    assert routine.main([str(prompt)], run=lambda *a, **k: called.append(a)) == 0 and called == []
    assert "nothing to read" in log.read_text(encoding="utf-8") and runs_of(ws) == []
    routine.claim_host(ws, "os", routine._now())
    routine.set_pause(ws, routine._now() + dt.timedelta(hours=8))
    assert routine.main([str(prompt)], run=lambda *a, **k: called.append(a)) == 0 and called == []
    line, = runs_of(ws)
    assert line["outcome"] == "paused" and line["routine"] == "backfill-night" and "paused until" in log.read_text(encoding="utf-8")
    routine.set_pause(ws, None)
    prompt.write_text("---\nname: odd\nonly_while: the moon is full\n---\nx\n", encoding="utf-8")
    assert routine.main([str(prompt)], run=lambda *a, **k: called.append(a)) == 0 and called == []
    assert log.read_text(encoding="utf-8").count("nothing to read") == 2 and not routine.lock_path(ws).exists()


# ----- the cloud runner: two scheduled tasks every computer steps aside for --------------------------------
CLOUD_TASKS = {"sweep": "trig_01AAAAAAAA", "daily": "trig_01BBBBBBBB"}
CLOUD_ROOTS = ["ada/ada-space", "ada/ada-brain"]
CLOUD_GRANT = {"read": ["mcp__mail__get_thread"], "write": ["mcp__tracker__create_issue"],
               "send": ["mcp__mail__send_message"]}


def cloud_hosted(ws, zone: str = "Europe/Amsterdam", now=NOW) -> dict:
    return routine.claim_cloud(ws, CLOUD_TASKS, zone, CLOUD_ROOTS, CLOUD_GRANT, now)


def test_a_cloud_claim_reads_back_with_its_tasks_zone_roots_and_grant(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    rec = cloud_hosted(ws)
    assert (rec["runner"], rec["machine"], rec["name"], rec["tasks"]["daily"], rec["timezone"], rec["roots"]) == \
           ("cloud", "m-cloud", "the cloud", "trig_01BBBBBBBB", "Europe/Amsterdam", CLOUD_ROOTS)
    assert rec["grant"] == CLOUD_GRANT and rec["paused_until"] is None and routine.host(ws) == rec
    assert routine.RUNNERS == ("os", "harness", "cloud") and routine.MACHINE.fullmatch(routine.CLOUD_MACHINE)
    lines = [line for line in (ws / routine.HOST).read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
    assert [line.split(":", 1)[0] for line in lines] == ["machine", "name", "runner", "since", "tasks", "timezone",
                                                         "roots", "grant", "computer", "paused_until"]
    on(monkeypatch, "m-desktop")
    back = routine.claim_host(ws, "os", NOW)   # the routines taken back: a computer's record carries none of it
    assert back["runner"] == "os" and not {*routine.CLOUD_KEYS, "computer"} & set(back)


def test_a_cloud_claim_names_the_computer_the_move_ran_on(tmp_path, monkeypatch):
    """The move names the computer it ran on (`computer`, its id), the one that keeps the computer's leg
    (`sync.Run.keeps_leg`), never the cloud's machine; a pause made on another computer keeps it."""
    ws = workspace(tmp_path)
    on(monkeypatch, "m-desktop")
    rec = cloud_hosted(ws)
    assert (rec["machine"], rec["computer"]) == (routine.CLOUD_MACHINE, "m-desktop") and routine.host(ws) == rec
    on(monkeypatch, "m-laptop")
    paused = routine.set_pause(ws, NOW + dt.timedelta(hours=2))
    assert paused["computer"] == "m-desktop" and routine.paused_until(paused, NOW) is not None
    assert routine.set_pause(ws, None)["computer"] == "m-desktop"
    again = cloud_hosted(ws, now=NOW + dt.timedelta(hours=1))   # moved again, from this computer: it keeps the leg now
    assert again["computer"] == "m-laptop" and routine.host(ws) == again


def test_a_cloud_record_that_names_no_computer_is_still_clear(tmp_path, monkeypatch):
    """A record the move wrote before it named a computer: every computer keeps the leg (`sync.Run.keeps_leg`), and a
    pause writes no computer into it."""
    ws = workspace(tmp_path)
    cloud_hosted(ws)
    path = ws / routine.HOST
    path.write_text("".join(f"{line}\n" for line in path.read_text(encoding="utf-8").splitlines()
                            if not line.startswith("computer:")), encoding="utf-8")
    rec = routine.host(ws)
    assert rec["runner"] == "cloud" and "computer" not in rec and routine.cloud_gate(ws, NOW) is None
    assert "computer" not in routine.set_pause(ws, NOW + dt.timedelta(hours=2))
    assert "computer:" not in path.read_text(encoding="utf-8")


def test_a_computer_claim_never_names_the_cloud(tmp_path):
    ws = workspace(tmp_path)
    with pytest.raises(ValueError, match="claim_cloud"):
        routine.claim_host(ws, "cloud", NOW)
    assert routine.host(ws) is None


def test_every_computer_steps_aside_for_the_cloud(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    cloud_hosted(ws)
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    for machine in ("m-desktop", "m-laptop", routine.CLOUD_MACHINE):
        on(monkeypatch, machine)
        assert routine.gate(ws, NOW) == "they run in the cloud"
        called = []
        assert routine.main([str(ws / "_meta/state/routines/sweep-chain.md")], run=lambda *a, **k: called.append(a)) == 0
        assert called == [] and not (ws / "_meta/state/runs.jsonl").exists()   # another runner's: no line here
    routine.set_pause(ws, NOW + dt.timedelta(hours=8))
    assert routine.gate(ws, NOW) == "they run in the cloud"   # paused or not, no computer runs them


@pytest.mark.parametrize("key, line", [
    ("tasks", 'tasks: {"sweep": null, "daily": "trig_01BBBBBBBB"}'), ("tasks", 'tasks: {"sweep": "trig_01AAAAAAAA"}'),
    ("tasks", 'tasks: {"sweep": "trig_", "daily": "trig_01BBBBBBBB"}'), ("grant", "grant: null"),
    ("grant", 'grant: {"read": [], "write": []}'), ("grant", 'grant: {"read": ["Bash"], "write": [], "send": []}'),
    ("roots", 'roots: ["not a repository"]'), ("roots", "roots: []"), ("timezone", "timezone: 7"),
    ("timezone", 'timezone: "Mars; rm -rf /"'), ("roots", None), ("machine", 'machine: "m-desktop"'),
    ("computer", 'computer: "m-cloud"'), ("computer", 'computer: "desktop"'), ("computer", "computer: null"),
    ("computer", 'computer: ["m-desktop"]'), ("computer", "computer: 7")])
def test_a_cloud_task_id_or_grant_that_is_none_makes_the_record_unclear(tmp_path, key, line):
    ws = workspace(tmp_path)
    cloud_hosted(ws)
    path = ws / routine.HOST
    kept = [line if old.startswith(f"{key}:") else old for old in path.read_text(encoding="utf-8").splitlines()]
    path.write_text("".join(f"{old}\n" for old in kept if old is not None), encoding="utf-8")
    assert routine.host(ws) == {"unclear": True}
    assert routine.gate(ws, NOW) == routine.UNCLEAR and routine.cloud_gate(ws, NOW) == routine.UNCLEAR


def test_the_clouds_machine_with_a_computers_runner_is_unclear(tmp_path):
    ws = workspace(tmp_path)
    (ws / routine.HOST).write_text('machine: "m-cloud"\nname: "the cloud"\nrunner: "os"\nsince: "x"\n', encoding="utf-8")
    assert routine.host(ws) == {"unclear": True}


def test_a_cloud_claim_of_the_wrong_shape_is_refused_and_writes_nothing(tmp_path):
    ws = workspace(tmp_path)
    for tasks, zone, roots, grant in (({"sweep": None, "daily": "trig_01BBBBBBBB"}, "UTC", CLOUD_ROOTS, CLOUD_GRANT),
                                      (CLOUD_TASKS, "", CLOUD_ROOTS, CLOUD_GRANT),
                                      (CLOUD_TASKS, "UTC", ["ada"], CLOUD_GRANT),
                                      (CLOUD_TASKS, "UTC", CLOUD_ROOTS, None)):
        with pytest.raises(ValueError):
            routine.claim_cloud(ws, tasks, zone, roots, grant, NOW)
    assert routine.host(ws) is None


def test_a_pause_keeps_the_cloud_claims_keys(tmp_path):
    ws = workspace(tmp_path)
    rec = cloud_hosted(ws)
    paused = routine.set_pause(ws, NOW + dt.timedelta(hours=2))
    assert routine.paused_until(paused, NOW) is not None
    assert {k: paused[k] for k in routine.CLOUD_KEYS} == {k: rec[k] for k in routine.CLOUD_KEYS}
    again = routine.claim_cloud(ws, {"sweep": "trig_02CCCCCCCC", "daily": "trig_02DDDDDDDD"}, "Europe/Amsterdam",
                                CLOUD_ROOTS, CLOUD_GRANT, NOW + dt.timedelta(hours=1))
    assert again["tasks"]["sweep"] == "trig_02CCCCCCCC" and again["paused_until"] == paused["paused_until"]   # no resume
    resumed = routine.set_pause(ws, None)
    assert resumed["paused_until"] is None and resumed["grant"] == CLOUD_GRANT and resumed["roots"] == CLOUD_ROOTS


def test_the_cloud_gate_goes_on_only_for_a_cloud_record_not_paused(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    assert routine.cloud_gate(ws, NOW) == "nothing says your routines run in the cloud"
    on(monkeypatch, "m-desktop")
    monkeypatch.setattr(routine, "machine_name", lambda: "ADA-DESKTOP")
    routine.claim_host(ws, "os", NOW)
    assert routine.cloud_gate(ws, NOW) == "they run on ADA-DESKTOP"
    (ws / routine.HOST).write_text("machine: [\n", encoding="utf-8")
    assert routine.cloud_gate(ws, NOW) == routine.UNCLEAR
    cloud_hosted(ws)
    assert routine.cloud_gate(ws, NOW) is None
    routine.set_pause(ws, NOW + dt.timedelta(hours=2))
    assert routine.cloud_gate(ws, NOW).startswith("paused until")
    assert routine.cloud_gate(ws, NOW + dt.timedelta(hours=3)) is None


def _zone_data(name: str) -> bool:
    """Whether this computer knows the zone (Windows has no zone data without the tzdata package)."""
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo(name)
    except Exception:   # noqa: BLE001 - no zone data at all, or not this zone
        return False
    return True


def test_a_pause_set_in_local_time_stops_a_utc_cloud_run_until_then(tmp_path):
    ws = workspace(tmp_path)
    cloud_hosted(ws, "America/New_York")
    new_york = dt.timezone(dt.timedelta(hours=-4))   # New York on 28 October 2026, without zone data
    routine.set_pause(ws, dt.datetime(2026, 10, 28, 6, 0, tzinfo=new_york))
    before = dt.datetime(2026, 10, 28, 9, 59, tzinfo=dt.timezone.utc)
    assert routine.cloud_gate(ws, before) == "paused until 2026-10-28 06:00"   # said in the person's own time
    assert routine.cloud_gate(ws, dt.datetime(2026, 10, 28, 10, 1, tzinfo=dt.timezone.utc)) is None
    routine.set_pause(ws, dt.datetime(2026, 10, 28, 10, 0, tzinfo=dt.timezone.utc))   # the same moment, kept in UTC
    if _zone_data("America/New_York"):
        assert routine.cloud_gate(ws, before) == "paused until 2026-10-28 06:00"
    else:
        assert routine.cloud_gate(ws, before) == "paused until 2026-10-28 10:00"   # the moment as it was kept


def test_two_merged_claims_one_cloud_one_os_leave_nobody_running(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    cloud_hosted(ws)
    cloud = (ws / routine.HOST).read_text(encoding="utf-8")
    on(monkeypatch, "m-desktop")
    routine.claim_host(ws, "os", NOW)
    (ws / routine.HOST).write_text(cloud + (ws / routine.HOST).read_text(encoding="utf-8"), encoding="utf-8")
    assert routine.host(ws) == {"unclear": True}   # a union merge kept both claims
    assert routine.gate(ws, NOW) == routine.UNCLEAR and routine.cloud_gate(ws, NOW) == routine.UNCLEAR


def test_the_extra_allow_rules_join_only_a_guarded_profile(tmp_path):
    import cloud
    ws = workspace(tmp_path)
    extra = (*cloud.EXTRA_ALLOW, "Bash(gh api orgs/*)")   # the cloud's own, and one more: each joins in its order
    allow = routine.routine_settings(ws, "", [ws], extra_allow=extra)["permissions"]["allow"]
    last_tool = max(i for i, rule in enumerate(allow) if "_meta/tools/" in rule)   # they come right after it
    assert [allow.index(rule) for rule in extra] == list(range(last_tool + 1, last_tool + 1 + len(extra)))
    others = ("mcp__mail__trash_message", "Edit(//**)", "Bash")   # only Bash shapes join
    assert not set(others) & set(routine.routine_settings(ws, "", [ws], extra_allow=others)["permissions"]["allow"])
    unguarded = routine.routine_settings(ws, "", [ws], profile=tmp_path / "a$b" / "p.json", extra_allow=extra)
    assert not set(extra) & set(unguarded["permissions"]["allow"]) and "Bash" in unguarded["permissions"]["deny"]


def guarded(path: Path, data: dict):
    """bash_guard's answer for a command under the profile `data`, written to `path`: None when it runs."""
    import bash_guard
    path.write_text(json.dumps(data), encoding="utf-8")
    return lambda command: bash_guard.check(path, {"tool_input": {"command": command}})


def test_a_cloud_profile_runs_sync_only_as_its_chains_landing(tmp_path):
    """With `cloud_chain` (a cloud run's begin) sync.py runs only as that run's landing, `--root <root> --cloud
    <chain>`, each root as itself; without it (a computer's routine) the landing takes its arguments as before."""
    ws, other = workspace(tmp_path), make_space(tmp_path / "brain")
    path = tmp_path / "cloud.json"
    check = guarded(path, routine.routine_settings(ws, "", [ws, other], profile=path, cloud_chain="daily"))
    sync_py = lambda root: f"python3 {root.as_posix()}/_meta/tools/sync.py"   # noqa: E731
    for place in (ws, other):
        assert check(f"{sync_py(place)} --root {place.as_posix()} --cloud daily") is None, place
    for command in (f"{sync_py(ws)} --root {ws.as_posix()}", f"{sync_py(ws)} --root {ws.as_posix()} --cloud sweep",
                    f"{sync_py(ws)} --root {other.as_posix()} --cloud daily",
                    f"{sync_py(ws)} --root {ws.as_posix()} --release-leg",
                    f"python3 _meta/tools/sync.py --root {ws.as_posix()} --cloud daily"):
        assert check(command) is not None, command
    assert check(f"python3 {ws.as_posix()}/_meta/tools/status.py {ws.as_posix()} --json") is None   # the rest as before
    path = tmp_path / "computer.json"
    check = guarded(path, routine.routine_settings(ws, "", [ws, other], profile=path))
    for command in (f"{sync_py(ws)} --root {ws.as_posix()}", f"{sync_py(ws)} --root {ws.as_posix()} --release-leg",
                    "python3 _meta/tools/sync.py --root . --release-leg"):
        assert check(command) is None, command
    with pytest.raises(ValueError):
        routine.routine_settings(ws, "", [ws], profile=path, cloud_chain="nightly")


def test_no_routine_runs_the_merge_driver_from_its_shell(tmp_path):
    """`sync.py --merge-latest` is the driver git runs itself mid-merge, outside every hook; from a routine's shell it
    would write any file it is given, space.yaml or the rules among them, past the edit rules. However it is spelled
    (an abbreviation argparse takes, a quote the shell drops), a routine's shell never runs it, on a computer or in
    the cloud."""
    ws = workspace(tmp_path)
    sync_py = f"python3 {ws.as_posix()}/_meta/tools/sync.py"
    target = f"{ws.as_posix()}/space.yaml"
    for chain in (None, "sweep"):
        path = tmp_path / f"{chain}.json"
        check = guarded(path, routine.routine_settings(ws, "", [ws], profile=path, cloud_chain=chain))
        for command in (f"{sync_py} --merge-latest a {target} b", f"{sync_py} --merge a {target} b",
                        f"{sync_py} --mer a {target} b", f"{sync_py} '--merge-latest' a {target} b",
                        f"{sync_py} --'merge'-latest a {target} b", f'{sync_py} "--merge-latest" a {target} b',
                        f"{sync_py} --root {ws.as_posix()} --merge-latest a {target} b",
                        f"python3 _meta/tools/sync.py --merge-latest a {target} b",
                        f"{sync_py} {{--m,X}}erge-latest a {target} b", f"{sync_py} --me[r]ge-latest a {target} b",
                        f"{sync_py} --me?ge-latest a {target} b", f"{sync_py} --me* a {target} b",
                        f"{sync_py} --root {ws.as_posix()} {{--m,X}}erge-latest a {target} b",
                        f"python3 _meta/tools/sync.py --root . {{--m,X}}erge-latest a {target} b"):
            assert check(command) is not None, (chain, command)


def test_a_computers_routine_runs_sync_only_as_its_landing(tmp_path):
    """On a computer sync.py runs as the landing and its release leg, each root as itself, and nothing else: no
    word a shell could expand into another flag gets past an exact command."""
    ws, other = workspace(tmp_path), make_space(tmp_path / "brain")
    path = tmp_path / "computer.json"
    check = guarded(path, routine.routine_settings(ws, "", [ws, other], profile=path))
    sync_py = lambda root: f"python3 {root.as_posix()}/_meta/tools/sync.py"   # noqa: E731
    for place in (ws, other):
        for leg in ("", " --release-leg"):
            assert check(f"{sync_py(place)} --root {place.as_posix()}{leg}") is None, (place, leg)
    for root in (".", ws.as_posix()):
        assert check(f"python3 _meta/tools/sync.py --root {root} --release-leg") is None, root
    for command in (f"{sync_py(ws)} --root {other.as_posix()}", f"{sync_py(ws)} --root {ws.as_posix()} --no-push",
                    f"{sync_py(ws)} --root {ws.as_posix()} --only space.yaml", f"{sync_py(ws)} --root {ws.as_posix()} "
                    "--message x", f"{sync_py(ws)} --root {ws.as_posix()} --release-leg --root {other.as_posix()}",
                    f"{sync_py(ws)} --root {ws.as_posix()} --cloud daily", f"{sync_py(ws)} --root {ws.as_posix()} "
                    "{--release-leg,--no-push}", f"{sync_py(ws)} --root {ws.as_posix()} --release-leg*",
                    f"python3 _meta/tools/sync.py --root {other.as_posix()} --release-leg"):
        assert check(command) is not None, command


def test_a_routine_reads_a_codebase_named_for_graphql_and_never_graphql_itself(tmp_path):
    """GraphQL is refused by its endpoint word, never by a word in a repository's name."""
    ws = workspace(tmp_path)
    path = tmp_path / "p.json"
    check = guarded(path, routine.routine_settings(ws, "", [ws], profile=path))
    for read in ("gh api search/issues'?q=repo:acme/graphql-gateway+is:pr+is:merged&per_page=100'",
                 "gh api repos/acme/graphql-gateway/pulls/5", "gh api repos/acme/graphql-gateway/pulls/5/files"):
        assert check(read) is None, read
    for call in ("gh api graphql", "gh api graphql -f query=x", "gh api /graphql", "gh api repos/../graphql/pulls/5",
                 "gh api search/issues/../../graphql", "gh api https://api.github.com/graphql",
                 "gh api HTTPS://api.github.com/graphql", "gh api repos/%2e%2e/graphql", "gh api repos/%2E%2E/graphql",
                 "gh api ./graphql", "gh api .//graphql", "gh api ././graphql", "gh api //graphql"):
        assert check(call) is not None, call
    import bash_guard
    denied = bash_guard._patterns(routine.REST_ONE_WORD, False)   # layer 2 alone, whatever the allow list says
    for call in ("gh api https://api.github.com/graphql", "gh api HTTPS://api.github.com/graphql",
                 "gh api repos/%2e%2e/graphql", "gh api repos/%2E%2E/graphql", "gh api ./graphql", "gh api .//graphql",
                 "gh api ././graphql", "gh api //graphql"):
        assert any(rule.fullmatch(call) for rule in denied), call
    assert not any("*graphql" in rule for rule in routine.REST_ONE_WORD)


def test_no_cloud_run_edits_the_guard_plugin_or_its_marketplace(tmp_path):
    ws = workspace(tmp_path)
    deny = routine.routine_settings(ws, "", [ws])["permissions"]["deny"]
    for pattern in ("_meta/plugins/**", ".claude-plugin/**"):
        assert pattern in routine.PROTECTED_EDITS and f"Edit(//**/{pattern})" in deny


def test_a_root_whose_owner_or_name_is_only_dots_is_no_repository(tmp_path):
    """A repository is owner/name with neither part dots alone, the rule `ghrest` reads an origin by."""
    import ghrest
    refused = ("./ada-space", "../ada-space", ".../ada-space", "ada/.", "ada/..", "ada/...", "../..")
    taken = ("ada/ada-space", "ada/.github", "ada.dev/ada..space", "_a-da/x_", ".ada/..x")
    for slug in (*refused, *taken):
        same = all(ghrest.SEGMENT.fullmatch(part) for part in slug.split("/"))
        assert bool(routine.SLUG.fullmatch(slug)) == same == (slug in taken), slug
    ws = workspace(tmp_path)
    with pytest.raises(ValueError):
        routine.claim_cloud(ws, CLOUD_TASKS, "UTC", ["ada/.."], CLOUD_GRANT, NOW)
    assert routine.host(ws) is None
    cloud_hosted(ws)
    path = ws / routine.HOST
    path.write_text(path.read_text(encoding="utf-8").replace('"ada/ada-brain"', '"../ada-brain"'), encoding="utf-8")
    assert routine.host(ws) == {"unclear": True}


def test_an_extra_allow_rule_that_runs_over_a_line_is_dropped(tmp_path):
    ws = workspace(tmp_path)
    split = ("Bash(gh api repos/*\n)", "Bash(gh api repos/*)\n", "Bash(gh api\rrepos/*)", "Bash(gh api repos/*\r)",
             "Bash(\r\nrm -rf /)")
    allow = routine.routine_settings(ws, "", [ws], extra_allow=(*split, "Bash(gh api repos/*)"))["permissions"]["allow"]
    assert "Bash(gh api repos/*)" in allow and not set(split) & set(allow)
    assert not any("\n" in rule or "\r" in rule for rule in allow)


@pytest.mark.parametrize("wrong, said", [
    (("tasks",), "The cloud's record needs its tasks in the shape the record keeps."),
    (("timezone", "roots"), "The cloud's record needs its timezone and roots in the shape the record keeps."),
    (("tasks", "timezone", "roots"), "The cloud's record needs its tasks, timezone and roots in the shape the record "
                                     "keeps."),
    (("tasks", "timezone", "roots", "grant"), "The cloud's record needs its tasks, timezone, roots and grant in the "
                                              "shape the record keeps.")])
def test_a_cloud_claim_of_the_wrong_shape_says_so_in_one_sentence(tmp_path, wrong, said):
    bad = {"tasks": {"sweep": None}, "timezone": "", "roots": ["ada"], "grant": None}
    given = {"tasks": CLOUD_TASKS, "timezone": "UTC", "roots": CLOUD_ROOTS, "grant": CLOUD_GRANT}
    given.update({key: bad[key] for key in wrong})
    with pytest.raises(ValueError) as refused:
        routine.claim_cloud(workspace(tmp_path), given["tasks"], given["timezone"], given["roots"], given["grant"], NOW)
    assert str(refused.value) == said
