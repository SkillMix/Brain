"""Batch 4: the tracker has one writer, failed chain steps are labelled stale, and the weekly
template sync reverts a change that lowers the test score."""
import json

import yaml

import chain
import needs_you
import pages
import sign
import spaceyaml
import status
import template_sync
import tracker
from conftest import NOW, make_space


def test_a_session_queues_and_the_routine_creates_and_records(tmp_path):
    root = make_space(tmp_path / "ada")
    path = tracker.queue(root, "Fix the export", "Ada Okafor", subject="harbor", body="It drops rows.", now=NOW)
    rid = pages.read(path)[0]["id"]
    assert [p["id"] for p in tracker.pending(root)] == [rid]
    tracker.record(root, rid, "HAR-12", url="https://tracker.example/HAR-12", now=NOW)
    fm, _ = pages.read(path)
    assert fm["status"] == "created" and fm["issue"] == "HAR-12" and fm["path"] == "routine:tracker"
    assert tracker.pending(root) == []
    row = json.loads((root / "_meta/state/tracker.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["acted_for"] == "Ada Okafor" and row["issue"] == "HAR-12"


def test_at_propose_a_request_waits_for_a_persons_yes(tmp_path):
    root = make_space(tmp_path / "ada")
    data = yaml.safe_load((root / "space.yaml").read_text(encoding="utf-8"))
    data["authority"] = {"write_outside_system": "propose"}
    (root / "space.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    path = tracker.queue(root, "Fix the export", "Ada Okafor", now=NOW)
    rid = pages.read(path)[0]["id"]
    assert tracker.pending(root) == []
    assert any(i.kind == "tracker" and i.id == rid for i in needs_you.collect(root, now=NOW, online=False))
    sign.apply(root, {rid: "yes"}, by="Ada Okafor", now=NOW)
    assert [p["id"] for p in tracker.pending(root)] == [rid]


def test_a_failed_step_is_labelled_stale_until_it_runs_again(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada")
    real = chain._module

    def broken(name):
        if name == "check":
            class Boom:
                @staticmethod
                def run(*a, **k):
                    raise RuntimeError("no connector")
            return Boom
        return real(name)
    monkeypatch.setattr(chain, "_module", broken)
    chain.sweep_chain(root, now=NOW)
    stale = yaml.safe_load((root / "_meta/state/stale.yaml").read_text(encoding="utf-8"))
    assert "check" in stale
    status.run(root, now=NOW)
    assert "stale: check since 2026-10-12" in (root / "live/health.md").read_text(encoding="utf-8")
    monkeypatch.setattr(chain, "_module", real)
    chain.sweep_chain(root, now=NOW)
    assert "check" not in (yaml.safe_load((root / "_meta/state/stale.yaml").read_text(encoding="utf-8")) or {})


def test_the_guarded_template_sync_puts_back_a_change_that_lowers_the_score(tmp_path, monkeypatch):
    tpl = tmp_path / "tpl"
    (tpl / "_meta" / "tools").mkdir(parents=True)
    (tpl / "_meta" / "tools" / "sweep.py").write_text("print('v2')\n", encoding="utf-8")
    root = make_space(tmp_path / "ada")
    (root / "_meta" / "tools").mkdir(parents=True, exist_ok=True)
    (root / "_meta" / "tools" / "sweep.py").write_text("print('v1')\n", encoding="utf-8")
    scores = iter([0, 1])
    monkeypatch.setattr(template_sync, "_failures", lambda root: next(scores))
    report = template_sync.guarded(root, source=str(tpl))
    assert report.reverted is True
    assert (root / "_meta" / "tools" / "sweep.py").read_text(encoding="utf-8") == "print('v1')\n"
    scores = iter([1, 1])
    monkeypatch.setattr(template_sync, "_failures", lambda root: next(scores))
    report = template_sync.guarded(root, source=str(tpl))
    assert report.reverted is False
    assert (root / "_meta" / "tools" / "sweep.py").read_text(encoding="utf-8") == "print('v2')\n"
