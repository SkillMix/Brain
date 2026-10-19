"""Routines as OS tasks, so they outlive the session: the OS starts routine.py at each routine's
times, which runs the prompt through `claude -p`. Done only once the schedule reads back."""
import os
import subprocess
import sys
from pathlib import Path

import offboard
import onboard
import routine
import start
from conftest import make_space
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
    assert seen["argv"] == ["claude", "-p", "--permission-mode", "acceptEdits", "--permission-prompts", "none"]
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
