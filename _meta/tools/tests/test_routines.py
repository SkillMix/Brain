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
    assert lines[0] == "0 9 * * * /usr/bin/true" and len(lines) == 3
    assert lines[1].startswith("30 6 * * * cd ") and lines[1].endswith("# space-daily-chain-ada")
    assert lines[2].startswith("0 7,13,19 * * * cd ") and "routine.py" in lines[2]
    # cron does not catch up: one plain line says so, whatever the number of tasks; it blocks nothing
    waiting = [s for s in report.steps if s.name == onboard.CRON_STEP]
    assert len(waiting) == 1 and waiting[0].done and not waiting[0].needs_owner and not report.failed()
    assert waiting[0].result == ("This computer's scheduler does not catch up on runs it missed while the computer was off "
                                 "or asleep, so a routine can skip a day.")   # one sentence: what else it says was not verified
    assert not any(tool in waiting[0].result.lower() for tool in ("cron", "systemd", "systemctl", "crontab"))   # no tool is named
    import onboarding_page
    assert onboarding_page.STAGES[onboarding_page.stage_of(onboard.CRON_STEP)][0] == "Routines"
    assert [r["waiting"] for r in records] == [onboard.CRON_WAITING] * 2
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
        ("launchd", "com.spaces.space-daily-chain-ada", "daily-chain-ada"),
        ("launchd", "com.spaces.space-sweep-chain-ada", "sweep-chain-ada")]
    state = onboard.read_yaml(ws / "_meta" / "state" / "onboard.yaml", {})
    assert state["routine_tasks"][1]["check"] == ["launchctl", "print", "gui/501/com.spaces.space-sweep-chain-ada"]


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
    assert "old cron line removed" in report.steps[1].result and "old cron line" not in report.steps[0].result
    assert all(s.done for s in report.steps) and not any(s.name == onboard.CRON_STEP for s in report.steps)
    assert [r["kind"] for r in records] == ["systemd", "systemd"] and not any("waiting" in r for r in records)


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
