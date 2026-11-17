"""The flow runs without the model. When no agent started the daily chain (stale.yaml says
`daily not started`), the sync, which the OS runs every 15 minutes, runs the daily's steps that
need no model itself, at most once a day: status with landing and the id index, access, requests,
the release of what an earlier briefing listed, the tests. No morning view (nobody received a
briefing, so no window item is marked listed), no template sync (the person's click), and the
`daily not started` entry stays until an agent's own run."""
import datetime as dt
import json
from pathlib import Path

import pytest
import yaml

import chain
import onboard
import pages
import routine
import sync
from conftest import NOW, make_space
from test_chain import Fakes
from test_landing import ADA_REPO, DELIVERY, HARBOR, PEOPLE, released, vouch
from test_sign import queue_item
from test_sync import head_files, repo, run, run_lines, write  # noqa: F401  (the repo fixture)

STALE = Path("_meta") / "state" / "stale.yaml"
FLAG = "daily not started"


def stale(root: Path) -> dict:
    path = root / STALE
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def ran(root: Path, name: str) -> int:
    return sum(1 for line in run_lines(root) if line.get("routine") == name)


def last_daily(root: Path, at: dt.datetime) -> None:
    with (root / "_meta/state/runs.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        for name, when in (("daily", at), ("sweep_chain", NOW - dt.timedelta(hours=1))):
            fh.write(json.dumps({"routine": name, "at": pages.iso(when), "space": root.name, "cost_tokens": 0,
                                 "notes": "ok"}) + "\n")


def clock(monkeypatch, moment: dt.datetime) -> None:
    monkeypatch.setenv("SPACE_NOW", pages.iso(moment))


@pytest.fixture
def others(monkeypatch, model_free_runs):
    """Status runs for real; every other tool of the daily is a fake that notes it was called."""
    fake, real = Fakes(), chain._module
    monkeypatch.setattr(chain, "_module", lambda name: real(name) if name == "status" else fake.module(name))
    return fake


def test_a_model_free_daily_leaves_out_the_briefing_and_the_template_sync_and_keeps_the_entry(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada")
    (root / STALE).write_text(yaml.safe_dump({FLAG: "2026-10-09T09:00:00Z"}), encoding="utf-8")
    fake = Fakes()
    fake.modules["template_sync"] = type("T", (), {"guarded": staticmethod(fake._fn("template_sync"))})
    monkeypatch.setattr(chain, "_module", fake.module)
    monkeypatch.setattr(chain, "_template_sync_due", lambda space, now: True)

    result = chain.daily(root, now=NOW, model_free=True)
    assert fake.names() == ["status", "release"] and result["errors"] == []
    assert result["routine"] == "daily_model_free" and ran(root, "daily_model_free") == 1 and ran(root, "daily") == 0
    assert stale(root) == {FLAG: "2026-10-09T09:00:00Z"}   # still said: no agent ran the daily

    fake.calls.clear()
    chain.daily(root, now=NOW)   # an agent's own run does all of it, and the entry goes
    assert fake.names() == ["template_sync", "status", "release", "morning_view"]
    assert FLAG not in stale(root) and ran(root, "daily") == 1


def test_the_sync_runs_status_and_landing_itself_when_no_agent_started_the_daily(repo, tmp_path, monkeypatch, others):
    make_space(repo, profile="project", name="harbor", owner="Ben Liu", subjects=[DELIVERY, HARBOR] + PEOPLE,
               mounts=[{"name": "ada", "repo": ADA_REPO}])
    onboard.remember_space(make_space(tmp_path / "ben", name="ben", owner="Ben Liu"))   # the owner's workspace is here
    last_daily(repo, NOW - dt.timedelta(hours=72))
    released(repo, "p-0000a001", "Harbor goes live on 3 November.\n")
    vouch(monkeypatch, repo)
    waiting = queue_item(repo, "q-0000b001", "A note for the crew", NOW + dt.timedelta(hours=20))
    clock(monkeypatch, NOW)

    assert run(repo) == 0
    assert FLAG in stale(repo)   # the sync named it, and its own run does not take it back
    assert ran(repo, "daily_model_free") == 1 and ran(repo, "status") == 1 and ran(repo, "daily") == 1
    assert (repo / "live/health.md").exists() and (repo / "_meta/state/ids.yaml").exists()
    landed, _ = pages.read(repo / "knowledge/projects/harbor/p-0000a001.md")   # the release was filed
    assert landed["landed_from"] == "ada" and not (repo / "_inbound/candidates/p-0000a001.md").exists()
    assert pages.read(waiting)[0]["listed"] is False   # no briefing went out, so nothing ships on one
    assert "morning_view" not in others.names() and "release" in others.names()
    assert [line["notes"].get("model_free") for line in run_lines(repo) if line.get("routine") == "sync"] == [True]

    clock(monkeypatch, NOW + dt.timedelta(minutes=15))   # the next sync: once a day is enough
    assert run(repo) == 0 and ran(repo, "daily_model_free") == 1
    clock(monkeypatch, NOW + dt.timedelta(hours=25))
    assert run(repo) == 0 and ran(repo, "daily_model_free") == 2


def test_a_daily_that_started_is_left_to_its_agent(repo, monkeypatch, others):
    last_daily(repo, NOW - dt.timedelta(hours=30))   # under twice its cadence: nothing says it did not start
    clock(monkeypatch, NOW)
    assert run(repo) == 0
    assert FLAG not in stale(repo) and ran(repo, "daily_model_free") == 0 and others.names() == []


def test_only_the_computer_that_runs_the_daily_runs_it_without_the_model_and_not_while_paused(repo, monkeypatch, others):
    last_daily(repo, NOW - dt.timedelta(hours=72))
    monkeypatch.setattr(routine, "this_machine", lambda: "m-desktop")
    monkeypatch.setattr(routine, "machine_name", lambda: "ADA-DESKTOP")
    routine.claim_host(repo, "os", NOW)
    clock(monkeypatch, NOW)
    monkeypatch.setattr(routine, "this_machine", lambda: "m-laptop")   # another computer's sync
    assert run(repo) == 0 and ran(repo, "daily_model_free") == 0
    monkeypatch.setattr(routine, "this_machine", lambda: "m-desktop")
    routine.set_pause(repo, NOW + dt.timedelta(days=2))   # paused: the person stopped the routines
    assert run(repo) == 0 and ran(repo, "daily_model_free") == 0
    routine.set_pause(repo, None)
    assert run(repo) == 0 and ran(repo, "daily_model_free") == 1


def test_a_dry_run_and_a_run_of_named_paths_leave_it_alone(repo, monkeypatch, others):
    last_daily(repo, NOW - dt.timedelta(hours=72))
    (repo / STALE).write_text(yaml.safe_dump({FLAG: pages.iso(NOW - dt.timedelta(hours=72))}), encoding="utf-8")
    clock(monkeypatch, NOW)
    assert run(repo, "--dry-run") == 0 and run(repo, "--only", "work") == 0
    assert ran(repo, "daily_model_free") == 0 and others.names() == []


def test_a_failure_in_it_is_said_once_and_never_stops_the_landing(repo, monkeypatch, model_free_runs):
    last_daily(repo, NOW - dt.timedelta(hours=72))
    calls = []

    def broken(root, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("status broke")
    monkeypatch.setattr(chain, "daily", broken)
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n")
    clock(monkeypatch, NOW)
    assert run(repo) == sync.ERROR
    assert "work/notes.md" in head_files(repo)   # the landing went ahead
    said = [line["notes"] for line in run_lines(repo) if line.get("routine") == "sync"][-1]
    assert any("without the model" in error for error in said["errors"]) and "model_free" not in said
    clock(monkeypatch, NOW + dt.timedelta(minutes=15))
    assert run(repo) == 0 and len(calls) == 1 and calls[0]["model_free"] is True   # tomorrow, not every run
