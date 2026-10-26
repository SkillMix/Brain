"""The setup journal: where the first setup stands, so a closed session or a re-run the next day
continues where it stopped; the phases of start.py that write it; the lock that checks its process."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import onboard
import routine
import setup_journal
import start


# ----- the journal ---------------------------------------------------------------------
def test_phases_resume_at_the_first_unfinished_one(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACES_SETUP_JOURNAL", str(tmp_path / "setup.yaml"))
    setup_journal.set_phase("signins", "done", github="ada-okafor")
    setup_journal.set_phase("apps", "running")
    assert setup_journal.next_phase(setup_journal.load()) == "apps"


def test_a_need_is_listed_until_it_is_cleared(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACES_SETUP_JOURNAL", str(tmp_path / "setup.yaml"))
    setup_journal.need("github-code", "Enter this code", "gh auth status", link="https://github.com/login/device", code="4F2A-9C1B")
    assert [n["code"] for n in setup_journal.open_needs(setup_journal.load())] == ["4F2A-9C1B"]
    setup_journal.clear_need("github-code")
    assert setup_journal.open_needs(setup_journal.load()) == []


def test_resume_after_a_closed_session_continues_at_the_first_unfinished_phase(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SPACES_SETUP_JOURNAL", str(tmp_path / "setup.yaml"))
    for p in ("signins", "apps", "research"):
        setup_journal.set_phase(p, "done")
    assert start.main(["resume", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["next"] == "build"


def test_a_start_lock_of_a_dead_process_is_taken_over(tmp_path, monkeypatch):
    lock = tmp_path / "start.lock"
    lock.write_text("999999\n")
    monkeypatch.setattr(routine, "pid_alive", lambda pid: False)
    assert start.take_lock(lock) == lock


def test_the_journal_is_where_the_tests_and_the_machine_say_it_is(tmp_path, monkeypatch):
    assert setup_journal.journal_path() == tmp_path / "_home" / ".spaces" / "setup.yaml"   # conftest's fake home
    monkeypatch.delenv("SPACES_SETUP_JOURNAL")
    assert setup_journal.journal_path() == Path.home() / ".spaces" / "setup.yaml"
    monkeypatch.setenv("SPACES_SETUP_JOURNAL", str(tmp_path / "elsewhere.yaml"))
    assert setup_journal.journal_path() == tmp_path / "elsewhere.yaml"


def test_a_missing_journal_loads_as_nothing_done_and_saving_makes_its_folder(tmp_path, monkeypatch):
    target = tmp_path / "new" / ".spaces" / "setup.yaml"
    monkeypatch.setenv("SPACES_SETUP_JOURNAL", str(target))
    data = setup_journal.load()
    assert data == {} and setup_journal.next_phase(data) == "signins" and setup_journal.open_needs(data) == []
    assert setup_journal.save({"apps": {"state": "done"}}) == target
    assert setup_journal.load() == {"apps": {"state": "done"}}
    assert [p.name for p in target.parent.iterdir()] == ["setup.yaml"]   # no temporary file is left


def test_a_failed_write_keeps_the_old_journal_whole(tmp_path, monkeypatch):
    setup_journal.set_phase("signins", "done", github="ada-okafor")

    def disk_full(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(setup_journal, "_replace", disk_full)
    with pytest.raises(OSError):
        setup_journal.set_phase("signins", "failed")
    assert setup_journal.load()["signins"] == {"state": "done", "github": "ada-okafor"}
    assert [p.name for p in setup_journal.journal_path().parent.iterdir()] == ["setup.yaml"]


def test_a_reader_holding_the_file_for_a_moment_does_not_lose_the_write(monkeypatch):
    real, tries = os.replace, []

    def busy_then_free(src, dst):
        if Path(dst) != setup_journal.journal_path():
            return real(src, dst)
        tries.append(1)
        if len(tries) < 3:
            raise PermissionError("the file is in use")   # Windows, while another process reads it
        return real(src, dst)
    monkeypatch.setattr(setup_journal.os, "replace", busy_then_free)
    monkeypatch.setattr(setup_journal, "_sleep", lambda s: None)
    setup_journal.set_phase("apps", "running")
    assert len(tries) == 3 and setup_journal.load()["apps"]["state"] == "running"


def _filled_journal():
    """A journal worth losing: a finished sign-in, its fact, a running phase and a waiting need."""
    setup_journal.set_phase("signins", "done", github="ada-okafor")
    setup_journal.set_phase("apps", "running")
    setup_journal.need("connect-app", "Connect your mail", "claude mcp list")
    return setup_journal.journal_path().read_bytes()


def _unreadable(patch, times):
    """Reading the journal fails with PermissionError `times` times (always: None), as on Windows
    while another process replaces the file; every other file reads as usual. `patch` is a
    monkeypatch.context(): undoing the test's own monkeypatch would undo its fake home too."""
    real, tries = Path.read_text, []

    def read_text(self, *a, **k):
        if self == setup_journal.journal_path() and (times is None or len(tries) < times):
            tries.append(1)
            raise PermissionError("the file is being replaced")
        return real(self, *a, **k)
    patch.setattr(Path, "read_text", read_text)
    patch.setattr(setup_journal, "_sleep", lambda s: None)
    return tries


def test_a_read_that_meets_a_writers_replace_is_tried_again_and_nothing_is_lost(monkeypatch):
    _filled_journal()
    with monkeypatch.context() as patch:
        tries = _unreadable(patch, times=2)
        setup_journal.set_phase("research", "running")   # load -> change -> save
    assert len(tries) == 2
    data = setup_journal.load()
    assert data["signins"] == {"state": "done", "github": "ada-okafor"} and data["apps"]["state"] == "running"
    assert data["research"]["state"] == "running" and [n["kind"] for n in data["needs"]] == ["connect-app"]
    assert not list(setup_journal.journal_path().parent.glob("*.bad-*"))


def test_a_journal_that_stays_unreadable_is_an_error_not_an_empty_journal(monkeypatch):
    before = _filled_journal()
    with monkeypatch.context() as patch:
        _unreadable(patch, times=None)
        with pytest.raises(PermissionError):
            setup_journal.load()
        with pytest.raises(PermissionError):   # a writer stops where it cannot read: it does not write over
            setup_journal.set_phase("research", "running")
        with pytest.raises(PermissionError):
            setup_journal.need("namespace", "Where should the repositories go?", "gh api user")
    assert setup_journal.journal_path().read_bytes() == before


@pytest.mark.parametrize("name,content", [
    ("broken yaml", "signins: [unclosed\n"),
    ("a list", "- just\n- a list\n"),
    ("a bare word", "done\n"),
    ("not utf-8", b"signins: \xff\xfe\x00 done\n"),
])
def test_a_journal_that_is_no_mapping_is_kept_aside_before_it_is_reset(name, content):
    path = setup_journal.journal_path()
    path.parent.mkdir(parents=True)
    raw = content if isinstance(content, bytes) else content.encode("utf-8")
    path.write_bytes(raw)
    assert setup_journal.load() == {}
    [kept] = path.parent.glob("setup.yaml.bad-*")
    assert kept.read_bytes() == raw
    setup_journal.set_phase("signins", "done")   # the reset the next write makes
    assert setup_journal.load()["signins"]["state"] == "done" and kept.read_bytes() == raw


def test_an_empty_journal_file_holds_nothing_to_keep():
    path = setup_journal.journal_path()
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    assert setup_journal.load() == {} and not list(path.parent.glob("*.bad-*"))


def test_a_phase_keeps_the_facts_it_learned_and_takes_the_new_state():
    setup_journal.set_phase("signins", "waiting", github="ada-okafor")
    record = setup_journal.set_phase("signins", "done", claude="account")
    assert record == {"state": "done", "github": "ada-okafor", "claude": "account"}
    assert setup_journal.load()["signins"] == record
    with pytest.raises(ValueError):
        setup_journal.set_phase("signins", "finished")
    with pytest.raises(ValueError):
        setup_journal.set_phase("sign-ins", "done")


def test_next_phase_is_none_when_every_phase_is_done():
    assert setup_journal.next_phase({}) == "signins"
    done = {p: {"state": "done"} for p in setup_journal.PHASES}
    assert setup_journal.next_phase(done) is None
    done["research"]["state"] = "failed"
    assert setup_journal.next_phase(done) == "research"


def test_a_rerun_replaces_the_same_need_and_keeps_a_different_one():
    setup_journal.need("reconnect", "Reconnect Gmail", "claude mcp list")
    setup_journal.need("reconnect", "Reconnect Slack", "claude mcp list")
    link = "https://github.com/login/device"
    setup_journal.need("github-code", "Enter this code", "gh auth status", link=link, code="4F2A-9C1B")
    entry = setup_journal.need("github-code", "Enter this code", "gh auth status", link=link, code="7B1D-0E3F")
    assert entry["kind"] == "github-code" and entry["code"] == "7B1D-0E3F"
    needs = setup_journal.open_needs(setup_journal.load())
    assert [(n["kind"], n["text"], n["code"]) for n in needs] == [
        ("reconnect", "Reconnect Gmail", None), ("reconnect", "Reconnect Slack", None),
        ("github-code", "Enter this code", "7B1D-0E3F")]
    setup_journal.clear_need("reconnect")   # every need of the kind goes
    assert [n["kind"] for n in setup_journal.open_needs(setup_journal.load())] == ["github-code"]


def test_a_need_of_a_kind_nobody_knows_is_refused():
    with pytest.raises(ValueError):
        setup_journal.need("github_code", "Enter this code", "gh auth status")
    with pytest.raises(ValueError):
        setup_journal.clear_need("github_code")


def test_clearing_a_need_that_is_not_there_writes_nothing():
    setup_journal.clear_need("namespace")
    assert not setup_journal.journal_path().exists()


# ----- is the process still running ------------------------------------------------------
def test_pid_alive_tells_a_running_process_from_a_finished_one():
    assert routine.pid_alive(os.getpid())
    running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert routine.pid_alive(running.pid)
    finally:
        running.kill()
        running.wait()
    assert not routine.pid_alive(running.pid)
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    assert not routine.pid_alive(finished.pid)


def test_pid_alive_is_false_for_a_number_that_is_no_process():
    assert not routine.pid_alive(0) and not routine.pid_alive(-4) and not routine.pid_alive(2 ** 40)


# ----- the start lock --------------------------------------------------------------------
def test_a_lock_names_the_process_that_holds_it_and_keeps_others_out(tmp_path):
    lock = tmp_path / "start.lock"
    assert start.take_lock(lock) == lock
    assert lock.read_text().split() == [str(os.getpid())]
    assert start.take_lock(lock) is None   # that process is this one, and it runs


def test_a_lock_of_a_finished_process_is_taken_over_at_once(tmp_path):
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    lock = tmp_path / "start.lock"
    lock.write_text(f"{finished.pid}\n")   # fresh: the hour rule alone would keep it
    assert start.take_lock(lock) == lock
    assert lock.read_text().split() == [str(os.getpid())]


def test_a_lock_of_a_running_process_is_kept(tmp_path, monkeypatch):
    lock = tmp_path / "start.lock"
    lock.write_text("999999\n")
    monkeypatch.setattr(routine, "pid_alive", lambda pid: True)
    old = time.time() - 2 * 3600
    os.utime(lock, (old, old))   # older than the hour a lock without a pid waits, but its process runs
    assert start.take_lock(lock) is None and lock.read_text() == "999999\n"


def test_a_lock_of_a_running_process_is_still_taken_once_it_is_too_old_to_be_the_same_process(tmp_path, monkeypatch):
    """Windows gives a finished process's number to another process: a live pid on a lock older
    than any start can run is that stranger's, not the crashed start's."""
    lock = tmp_path / "start.lock"
    lock.write_text("999999\n")
    monkeypatch.setattr(routine, "pid_alive", lambda pid: True)
    assert start.PID_REUSE_HOURS == 3 and start.PID_REUSE_HOURS > start.LOCK_HOURS
    old = time.time() - (start.PID_REUSE_HOURS * 3600 - 60)
    os.utime(lock, (old, old))
    assert start.take_lock(lock) is None   # one minute short of the bound
    old = time.time() - (start.PID_REUSE_HOURS * 3600 + 60)
    os.utime(lock, (old, old))
    assert start.take_lock(lock) == lock and lock.read_text().split() == [str(os.getpid())]


def test_a_lock_without_a_readable_pid_waits_out_the_hour(tmp_path, monkeypatch):
    monkeypatch.setattr(routine, "pid_alive", lambda pid: pytest.fail("no pid to ask about"))
    lock = tmp_path / "start.lock"
    for text in ("", "not a pid\n"):
        lock.write_text(text)
        assert start.take_lock(lock) is None   # fresh
        old = time.time() - 2 * 3600
        os.utime(lock, (old, old))
        assert start.take_lock(lock) == lock   # older than LOCK_HOURS
        lock.unlink()


# ----- the phases ------------------------------------------------------------------------
def test_every_phase_has_its_function():
    for phase in setup_journal.PHASES:
        assert callable(getattr(start, f"phase_{phase}"))


def test_the_old_flat_flags_run_the_build(monkeypatch):
    seen = []
    monkeypatch.setattr(start, "phase_build", lambda args: seen.append(args) or {"phase": "build", "state": "done"})
    assert start.main(["--company", "Lantern Works", "--offline"]) == 0
    assert start.main(["build", "--company", "Lantern Works", "--offline"]) == 0
    flat, named = seen
    assert flat == named and flat.phase == "build" and flat.company == "Lantern Works" and flat.offline
    assert setup_journal.load()["build"]["state"] == "done"


def test_a_word_that_is_no_phase_is_an_error_not_a_build(capsys):
    with pytest.raises(SystemExit) as exc:
        start.main(["sweep"])
    assert exc.value.code == 2 and "invalid choice" in capsys.readouterr().err


def test_a_phase_is_running_while_it_works_and_has_its_state_after(monkeypatch, capsys):
    during = []

    def apps(args):
        during.append(setup_journal.load()["apps"]["state"])
        return {"phase": "apps", "state": "waiting"}
    monkeypatch.setattr(start, "phase_apps", apps)
    assert start.main(["apps", "--json"]) == 0
    assert during == ["running"] and setup_journal.load()["apps"]["state"] == "waiting"
    assert json.loads(capsys.readouterr().out) == {"phase": "apps", "state": "waiting", "needs": []}


def test_json_is_one_object_with_the_open_needs(monkeypatch, capsys):
    def signins(args):
        setup_journal.need("github-code", "Enter this code", "gh auth status", code="4F2A-9C1B")
        return {"phase": "signins", "state": "waiting", "needs": ["ignored: the journal's list wins"]}
    monkeypatch.setattr(start, "phase_signins", signins)
    assert start.main(["signins", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)   # nothing but the object on stdout
    assert [n["code"] for n in result["needs"]] == ["4F2A-9C1B"] and result["state"] == "waiting"


def test_a_phase_that_names_no_state_is_done_unless_the_person_is_still_needed(monkeypatch, capsys):
    monkeypatch.setattr(start, "phase_research", lambda args: {"card": "Lantern Works"})
    assert start.main(["research", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "done"
    setup_journal.need("connect-app", "Connect your mail", "claude mcp list")
    assert start.main(["research", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "waiting"
    assert setup_journal.load()["research"]["state"] == "waiting"


def test_a_phase_that_crashes_is_left_failed_not_running(monkeypatch):
    def boom(args):
        raise RuntimeError("the network went away")
    monkeypatch.setattr(start, "phase_history", boom)
    with pytest.raises(RuntimeError):
        start.main(["history"])
    assert setup_journal.load()["history"]["state"] == "failed"


def test_a_dry_run_leaves_the_journal_as_it_was(monkeypatch):
    setup_journal.set_phase("build", "done")
    monkeypatch.setattr(start, "phase_build", lambda args: {"phase": "build", "state": "pending"})
    assert start.main(["build", "--dry-run"]) == 0
    assert setup_journal.load()["build"]["state"] == "done"


@pytest.mark.parametrize("result", [{"state": "failed"}, {"state": "waiting", "stopped": True}])
def test_a_build_that_failed_or_stopped_exits_1(monkeypatch, result):
    monkeypatch.setattr(start, "phase_build", lambda args: {"phase": "build", **result})
    assert start.main(["build"]) == 1


def test_resume_says_where_the_setup_stands_and_what_waits_for_the_person(capsys):
    setup_journal.set_phase("signins", "done", github="ada-okafor")
    setup_journal.need("connect-app", "Connect your mail", "claude mcp list",
                       link="https://claude.ai/settings/connectors")
    assert start.main(["resume", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["next"] == "apps" and [n["kind"] for n in result["needs"]] == ["connect-app"]
    assert list(result["phases"]) == list(setup_journal.PHASES)
    assert result["phases"]["signins"] == {"state": "done", "github": "ada-okafor"}
    assert result["phases"]["history"] == {"state": "pending"}
    assert start.main(["resume"]) == 0
    text = capsys.readouterr().out
    assert "next: apps" in text and "yours: connect-app: Connect your mail" in text and "claude.ai/settings" in text


def test_resume_with_every_phase_done_has_nothing_next(capsys):
    for p in setup_journal.PHASES:
        setup_journal.set_phase(p, "done")
    assert start.main(["resume", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["next"] is None
    assert start.main(["resume"]) == 0
    assert "every phase is done" in capsys.readouterr().out


# ----- the build phase wraps today's start ----------------------------------------------------
def _built(monkeypatch, *rows, stopped=False):
    report = onboard.Report(case="start")
    for name, done, owner in rows:
        report.add(name, done, "check", "result", needs_owner=owner)
    report.stopped = stopped
    monkeypatch.setattr(start, "start", lambda *a, **k: report)
    return report


@pytest.mark.parametrize("rows,stopped,dry,state", [
    ([("create lantern/ada-workspace", True, False)], False, False, "done"),
    ([("create lantern/ada-workspace", False, False)], False, False, "failed"),
    ([("namespace", False, True)], True, False, "waiting"),
    ([("create lantern/ada-workspace", True, False), ("sign in", False, True)], False, False, "waiting"),
    ([("create lantern/ada-workspace", True, False)], False, True, "pending"),   # nothing was created
    ([("one start at a time", False, False)], False, True, "failed"),   # a dry run that failed did fail
])
def test_the_build_state_comes_from_what_the_start_reported(monkeypatch, rows, stopped, dry, state):
    _built(monkeypatch, *rows, stopped=stopped)
    result = start.phase_build(start.parser().parse_args(["build", "--json"] + (["--dry-run"] if dry else [])))
    assert result["phase"] == "build" and result["state"] == state
    assert set(result) >= {"workspace", "spaces", "routines", "plan", "stopped", "page", "done", "failed", "yours"}


def test_a_dry_run_that_failed_still_exits_1_and_still_leaves_the_journal_alone(monkeypatch, capsys):
    """Another start holds the lock: not `stopped`, a failed row. It exited 1 before the phases."""
    _built(monkeypatch, ("one start at a time", False, False))
    assert start.main(["--dry-run", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "failed" and [r["name"] for r in result["failed"]] == ["one start at a time"]
    assert not setup_journal.journal_path().exists()


def test_the_build_still_prints_its_steps_unless_json_is_asked_for(monkeypatch, capsys):
    _built(monkeypatch, ("create lantern/ada-workspace", True, False))
    assert start.main(["--offline"]) == 0
    assert "create lantern/ada-workspace" in capsys.readouterr().out
    assert start.main(["--offline", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["done"] == 1


def test_the_parser_gives_a_namespace_for_any_phase():
    args = start.parser().parse_args(["history", "--wait", "--json"])
    assert isinstance(args, argparse.Namespace) and args.phase == "history" and args.wait and args.json
