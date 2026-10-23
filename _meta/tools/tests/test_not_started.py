"""A chain that never starts: the sync script, which the OS runs every 15 minutes whatever the
agent scheduler does, names a chain in stale.yaml when its last run line is older than twice the
longest gap in its routine's schedule; the chain clears the entry when it runs again."""
import datetime as dt
import json
from pathlib import Path

import yaml

import chain
import pages
import sync
from conftest import NOW, make_space

STALE = Path("_meta") / "state" / "stale.yaml"


def run_line(root: Path, routine: str, at: dt.datetime) -> None:
    path = root / "_meta" / "state" / "runs.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"routine": routine, "at": pages.iso(at), "space": root.name,
                             "cost_tokens": 0, "notes": "ok"}) + "\n")


def stale(root: Path) -> dict:
    path = root / STALE
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def test_a_daily_chain_older_than_twice_its_cadence_is_named_and_its_next_run_clears_it(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada")
    last = NOW - dt.timedelta(hours=49)   # the daily chain runs once a day: twice is 48 hours
    run_line(root, "daily", last)
    run_line(root, "sweep_chain", NOW - dt.timedelta(hours=5))
    (root / STALE).write_text(yaml.safe_dump({"distill": "2026-10-11T09:00:00Z"}), encoding="utf-8")

    assert sync.not_started(root, NOW) is True
    assert stale(root) == {"distill": "2026-10-11T09:00:00Z", "daily not started": pages.iso(last)}
    before = (root / STALE).read_bytes()
    # idempotent: every later run with nothing new leaves the file as it is
    assert sync.not_started(root, NOW + dt.timedelta(minutes=15)) is False
    assert (root / STALE).read_bytes() == before

    monkeypatch.setattr(chain, "_module", lambda name: None)   # every step skipped, the chain still runs
    chain.daily(root, now=NOW)
    assert "daily not started" not in stale(root)
    assert stale(root)["distill"] == "2026-10-11T09:00:00Z"
    assert sync.not_started(root, NOW + dt.timedelta(minutes=15)) is False


def test_fresh_run_lines_write_nothing(tmp_path):
    root = make_space(tmp_path / "ada")
    run_line(root, "daily", NOW - dt.timedelta(hours=30))
    run_line(root, "sweep_chain", NOW - dt.timedelta(hours=13))   # the overnight gap is 12 hours
    assert sync.not_started(root, NOW) is False
    assert not (root / STALE).exists()


def test_a_sweep_chain_older_than_twice_its_longest_gap_is_named(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada")
    run_line(root, "daily", NOW - dt.timedelta(hours=2))
    last = NOW - dt.timedelta(hours=25)   # three times a day, the longest gap 12 hours
    run_line(root, "sweep_chain", last)
    assert sync.not_started(root, NOW) is True
    assert stale(root) == {"sweep_chain not started": pages.iso(last)}
    monkeypatch.setattr(chain, "_module", lambda name: None)
    chain.sweep_chain(root, now=NOW)
    assert "sweep_chain not started" not in stale(root)


def test_a_chain_that_never_ran_counts_from_the_onboarding(tmp_path):
    root = make_space(tmp_path / "ada")
    assert sync.not_started(root, NOW) is False   # no onboarding and no run: nothing promised a schedule
    assert not (root / STALE).exists()
    onboarded = NOW - dt.timedelta(days=3)
    (root / "_meta" / "state" / "onboard.yaml").write_text(
        yaml.safe_dump({"case": "personal", "at": pages.iso(onboarded)}), encoding="utf-8")
    assert sync.not_started(root, NOW) is True
    assert stale(root) == {"daily not started": pages.iso(onboarded),
                           "sweep_chain not started": pages.iso(onboarded)}
