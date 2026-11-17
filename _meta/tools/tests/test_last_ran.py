"""Every stage says when it last ran, and one that stopped is said where the sync can say it.
A stage whose last run line is older than its window is named in stale.yaml as `<stage> stale`,
beside the chains that did not start; the sync, which the OS runs whatever the agents do, puts one
item on Needs you for all of them and names them in its run line. A mount it could not move for a
week is one of them. The health line shows the last run of each stage."""
import datetime as dt
import json
from pathlib import Path

import yaml

import needs_you
import pages
import routine
import status
import sync
from conftest import NOW, make_space
from test_model_free import others  # noqa: F401  (status for real, the other tools of the daily as fakes)
from test_sync import git, mounted, repo, run, run_lines, write  # noqa: F401  (the repo fixture)

STALE = Path("_meta") / "state" / "stale.yaml"
ITEMS = Path("_inbound") / "needs-you" / "sync.yaml"
HOUR = dt.timedelta(hours=1)


def line(root: Path, name: str, at: dt.datetime, **notes) -> None:
    with (root / "_meta/state/runs.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"routine": name, "at": pages.iso(at), "space": root.name, "cost_tokens": 0,
                             "notes": notes or "ok"}) + "\n")


def routed(root: Path, at: dt.datetime) -> None:
    with (root / "_meta/state/routing.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"at": pages.iso(at), "record": "m-1", "decision": "private", "by": "router"}) + "\n")


def stale(root: Path) -> dict:
    path = root / STALE
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def asked(root: Path) -> list:
    return [i for i in needs_you.collect(root, online=False) if i.id.startswith(sync.STALE_ITEM)]


def clock(monkeypatch, moment: dt.datetime) -> None:
    monkeypatch.setenv("SPACE_NOW", pages.iso(moment))


def test_the_last_run_of_every_stage_is_read_from_what_the_stages_write(tmp_path):
    root = make_space(tmp_path / "ada")
    assert sync.last_ran(root) == {}
    line(root, "status", NOW - 30 * HOUR)
    line(root, "status", NOW - 3 * HOUR)
    line(root, "sweep", NOW - 5 * HOUR, witness=2)
    (root / "_meta/state/runs.jsonl").open("a", encoding="utf-8").write("not json\n")
    routed(root, NOW - 4 * HOUR)
    assert sync.last_ran(root) == {"status": NOW - 3 * HOUR, "sweep": NOW - 5 * HOUR, "route": NOW - 4 * HOUR}


def test_a_stage_past_its_window_is_named_and_a_fresh_run_takes_it_back(tmp_path):
    root = make_space(tmp_path / "ada")
    last = NOW - 49 * HOUR   # status and release: two days
    line(root, "status", last)
    line(root, "release", NOW - 47 * HOUR)
    line(root, "sweep", NOW - 25 * HOUR)   # the sweep's stages: twice its longest gap, 12 hours
    line(root, "distill", NOW - 23 * HOUR)
    assert sync.not_started(root, NOW) is True
    assert stale(root) == {"status stale": pages.iso(last), "sweep stale": pages.iso(NOW - 25 * HOUR)}
    assert sync.not_started(root, NOW) is False   # written only when it changes
    line(root, "status", NOW)
    line(root, "sweep", NOW)
    assert sync.not_started(root, NOW) is True and stale(root) == {}


def test_a_stage_that_never_ran_here_is_not_judged(tmp_path):
    root = make_space(tmp_path / "harbor", profile="project", name="harbor")
    line(root, "status", NOW - HOUR)
    assert sync.not_started(root, NOW) is False and not (root / STALE).exists()


def test_routing_is_stale_when_records_arrived_and_none_was_routed_since(tmp_path):
    root = make_space(tmp_path / "ada", routing={"since": "2026-09-01"})
    routed(root, NOW - 90 * HOUR)
    line(root, "sweep", NOW - 80 * HOUR)                # nothing new came in: nothing to route
    line(root, "sweep", NOW - 2 * HOUR)
    assert sync.not_started(root, NOW) is False
    fed = NOW - 30 * HOUR
    line(root, "sweep", fed, witness=3)                 # records arrived, and no decision followed
    assert sync.not_started(root, NOW) is True and stale(root) == {"route stale": pages.iso(fed)}
    routed(root, NOW - HOUR)
    assert sync.not_started(root, NOW) is True and stale(root) == {}
    off = make_space(tmp_path / "ben", name="ben")      # no `routing:` in space.yaml: no router to wait for
    line(off, "sweep", fed, witness=3)
    line(off, "sweep", NOW - HOUR)
    assert sync.not_started(off, NOW) is False


def test_a_stale_stage_is_one_item_on_needs_you_and_in_the_run_line_until_it_runs_again(repo, monkeypatch):
    clock(monkeypatch, NOW)
    line(repo, "status", NOW - 72 * HOUR)
    line(repo, "daily", NOW - HOUR)
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n")   # something to commit: a run line
    assert run(repo) == 0
    assert stale(repo) == {"status stale": pages.iso(NOW - 72 * HOUR)}
    items = asked(repo)
    assert len(items) == 1 and "status" in items[0].text and "2026-10-09" in items[0].text
    assert run_lines(repo)[-1]["notes"]["stale"] == ["status stale"]
    before = (repo / ITEMS).read_bytes()
    clock(monkeypatch, NOW + dt.timedelta(minutes=15))
    assert run(repo) == 0 and (repo / ITEMS).read_bytes() == before   # the same item, not written again

    line(repo, "status", NOW + dt.timedelta(minutes=20))
    clock(monkeypatch, NOW + dt.timedelta(minutes=30))
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\nline three\n")
    assert run(repo) == 0
    assert stale(repo) == {} and asked(repo) == [] and "stale" not in run_lines(repo)[-1]["notes"]


def test_a_chain_that_did_not_start_is_on_the_same_item_and_a_pause_asks_nothing(repo, monkeypatch):
    clock(monkeypatch, NOW)
    monkeypatch.setattr(routine, "this_machine", lambda: "m-desktop")
    monkeypatch.setattr(routine, "machine_name", lambda: "ADA-DESKTOP")
    line(repo, "daily", NOW - 72 * HOUR)
    line(repo, "status", NOW - 72 * HOUR)
    line(repo, "sweep_chain", NOW - HOUR)
    assert run(repo) == 0
    (item,) = asked(repo)
    assert "daily" in item.text and "status" in item.text
    routine.claim_host(repo, "os", NOW)
    routine.set_pause(repo, NOW + dt.timedelta(days=2))   # the person stopped the routines: no alarm
    assert run(repo) == 0 and asked(repo) == []
    assert "daily not started" in stale(repo)   # still said on the health line


def test_a_stage_the_sync_ran_itself_in_this_run_is_not_called_stale(repo, monkeypatch, others):  # noqa: F811
    clock(monkeypatch, NOW)
    line(repo, "daily", NOW - 72 * HOUR)
    line(repo, "status", NOW - 72 * HOUR)
    line(repo, "sweep_chain", NOW - HOUR)
    assert run(repo) == 0   # no agent started the daily: the sync ran status itself
    assert stale(repo) == {"daily not started": pages.iso(NOW - 72 * HOUR)}
    (item,) = asked(repo)
    assert "daily" in item.text and "status" not in item.text


def test_a_mount_the_sync_could_not_move_for_a_week_is_a_stale_stage(repo, tmp_path, monkeypatch):
    origin = tmp_path / "brain.git"
    git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    work = tmp_path / "brain-work"
    git(tmp_path, "clone", "-q", str(origin), str(work))
    write(work / "index.md", "# Brain\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "one")
    git(work, "push", "-q", "-u", "origin", "main")
    mounted(repo, origin, "brain")
    write(repo / "brain" / "stray.md", "a file something wrote into the copy\n")
    clock(monkeypatch, NOW)
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n")
    assert run(repo) == 0
    assert run_lines(repo)[-1]["notes"]["pins_held"] == ["brain"] and stale(repo) == {} and asked(repo) == []
    clock(monkeypatch, NOW + dt.timedelta(days=8))
    assert run(repo) == 0
    assert stale(repo) == {"brain pin stale": pages.iso(NOW)}
    (item,) = asked(repo)
    assert "brain" in item.text
    (repo / "brain" / "stray.md").unlink()
    clock(monkeypatch, NOW + dt.timedelta(days=8, minutes=15))
    assert run(repo) == 0 and stale(repo) == {} and asked(repo) == []


def test_the_next_run_says_how_long_the_sync_itself_had_stopped(repo, monkeypatch):
    clock(monkeypatch, NOW)
    assert run(repo) == 0
    clock(monkeypatch, NOW + dt.timedelta(minutes=15))
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n")
    assert run(repo) == 0 and "gap_hours" not in run_lines(repo)[-1]["notes"]
    clock(monkeypatch, NOW + 9 * HOUR)   # the computer slept, or nothing started the sync
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\nline three\n")
    assert run(repo) == 0 and run_lines(repo)[-1]["notes"]["gap_hours"] == 9


def test_the_health_line_shows_the_last_run_of_each_stage(tmp_path):
    root = make_space(tmp_path / "ada")
    line(root, "sweep", NOW - 5 * HOUR)
    line(root, "status", NOW - 80 * HOUR)
    cell = status._health_line(spaceyaml_of(root), "ada", root, NOW)
    assert "last ran: sweep 10-12, status 10-09" in cell


def spaceyaml_of(root):
    import spaceyaml
    return spaceyaml.load(root)
