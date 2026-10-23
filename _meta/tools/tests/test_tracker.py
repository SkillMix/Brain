"""Batch 4: the tracker has one writer, failed chain steps are labelled stale, and the weekly
template sync reverts a change that lowers the test score."""
import json
from pathlib import Path

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


def test_only_a_guarded_template_sync_resets_the_weekly_clock(tmp_path, monkeypatch):
    """Hand syncs do not count: the weekly sync guarded by the tests still comes due."""
    tpl = tmp_path / "tpl"
    (tpl / "_meta" / "tools").mkdir(parents=True)
    (tpl / "_meta" / "tools" / "sweep.py").write_text("print('v2')\n", encoding="utf-8")
    root = make_space(tmp_path / "ada")
    prov = root / "_meta" / "state" / "template-version.yaml"
    prov.parent.mkdir(parents=True, exist_ok=True)
    prov.write_text("repo: x\n", encoding="utf-8")
    runs = root / "_meta" / "state" / "runs.jsonl"
    runs.write_text(json.dumps({"routine": "template-sync", "at": pages.iso(NOW),
                                "notes": {"added": 0, "updated": 3}}) + "\n", encoding="utf-8")
    space = spaceyaml.load(root)
    assert chain._template_sync_due(space, NOW) is True
    monkeypatch.setattr(template_sync, "_failures", lambda root: 0)
    template_sync.guarded(root, source=str(tpl))
    lines = [json.loads(l) for l in runs.read_text(encoding="utf-8").splitlines()]
    assert any((l.get("notes") or {}).get("guarded") for l in lines)
    assert chain._template_sync_due(spaceyaml.load(root), spaceyaml.load(root).now()) is False


def made_issue(root, key="HAR-7", title="Ship the export"):
    rid = Path(tracker.queue(root, title, "Ada Okafor", subject="harbor")).stem
    tracker.record(root, rid, key, url=f"https://tracker.example/{key}", now=NOW)
    return rid


def test_updates_go_only_to_issues_the_tracker_made_and_are_queued_once(tmp_path):
    root = make_space(tmp_path / "ada")
    made_issue(root)
    path = tracker.update(root, "HAR-7", "Ada Okafor", state="In Progress", comment="Started on the export.",
                          evidence="journal-20261012T0900", now=NOW)
    assert path is not None
    assert tracker.update(root, "HAR-7", "Ada Okafor", state="In Progress", evidence="journal-20261012T0900",
                          now=NOW) is None
    try:
        tracker.update(root, "OTH-1", "Ada Okafor", state="Done", now=NOW)
        raise AssertionError("an issue someone else made must be refused")
    except ValueError:
        pass
    kinds = {(p["kind"], p.get("issue")) for p in tracker.pending(root)}
    assert ("update", "HAR-7") in kinds
    upd = next(p for p in tracker.pending(root) if p["kind"] == "update")
    tracker.record(root, upd["id"], "HAR-7", now=NOW)
    assert all(p["kind"] != "update" for p in tracker.pending(root))
    lines = [json.loads(l) for l in (root / tracker.LOG).read_text(encoding="utf-8").splitlines()]
    assert lines[-1]["update"] == {"state": "In Progress", "comment": "Started on the export."}


def test_evidence_moves_the_issues_the_tracker_made(tmp_path):
    """A merged pull request naming the key closes it; a daily-note line naming it with
    'blocked' or 'started' queues that update; each piece of evidence is used once."""
    root = make_space(tmp_path / "ada")
    made_issue(root, "HAR-7")
    made_issue(root, "HAR-8", "Fix the import")
    page = root / "knowledge" / "sources" / "2026" / "10" / "gh-pr-41.md"
    pages.write(page, {"id": pages.new_id(), "type": "source", "title": "Merged: export to CSV (HAR-7)",
                       "status": "draft", "generated": pages.generated("sweep", NOW), "kind": "change",
                       "source": "repository", "record": "gh-pr-41", "at": pages.iso(NOW)}, "Closes HAR-7.\n")
    note = root / "journal" / "2026" / "10" / "2026-10-12.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# 2026-10-12\n\n## 08:00Z: import\n\n- HAR-8 is blocked on the client's data dump.\n",
                    encoding="utf-8")
    queued = tracker.observe(root, now=NOW)
    pend = {p["issue"]: p for p in tracker.pending(root) if p["kind"] == "update"}
    assert pend["HAR-7"]["state"] == "Done" and "export to CSV" in pend["HAR-7"]["comment"]
    assert pend["HAR-8"]["state"] is None and "blocked" in pend["HAR-8"]["comment"]
    assert len(queued) == 2 and tracker.observe(root, now=NOW) == []


def test_a_workspace_onboarding_made_gets_the_template_without_a_provenance_file(tmp_path):
    """Onboarding makes a workspace from the template and stamps no provenance: the weekly sync
    still counts it as due, or that workspace would never receive a template fix."""
    root = make_space(tmp_path / "ada")
    assert chain._template_sync_due(spaceyaml.load(root), NOW) is False   # not made by onboarding
    (root / "_meta" / "state").mkdir(parents=True, exist_ok=True)
    (root / "_meta" / "state" / "onboard.yaml").write_text("case: person\n", encoding="utf-8")
    assert chain._template_sync_due(spaceyaml.load(root), NOW) is True


def test_a_spec_page_queued_with_a_request_gets_the_issue_key_and_moves_with_it(tmp_path):
    """The spec page named at queue time gets `tracker: <key>` once the issue is made, so the
    librarian moves its state; a page that already names an issue, or is gone, is left alone."""
    root = make_space(tmp_path / "ada")
    spec = root / "knowledge" / "plans" / "export.md"
    pages.write(spec, {"id": pages.new_id(), "type": "plan", "title": "Export plan", "status": "draft"},
                "What the export does.\n")
    kept = root / "knowledge" / "plans" / "import.md"
    pages.write(kept, {"id": pages.new_id(), "type": "plan", "title": "Import plan", "status": "draft",
                       "tracker": "HAR-1"}, "What the import does.\n")
    rid = Path(tracker.queue(root, "Ship the export", "Ada Okafor", subject="harbor",
                             spec="knowledge/plans/export.md", now=NOW)).stem
    tracker.record(root, rid, "HAR-7", now=NOW)
    assert pages.read(spec)[0]["tracker"] == "HAR-7"
    rid = Path(tracker.queue(root, "Ship the import", "Ada Okafor", spec="knowledge/plans/import.md", now=NOW)).stem
    tracker.record(root, rid, "HAR-9", now=NOW)
    assert pages.read(kept)[0]["tracker"] == "HAR-1"
    rid = Path(tracker.queue(root, "Ship the report", "Ada Okafor", spec="knowledge/plans/gone.md", now=NOW)).stem
    tracker.record(root, rid, "HAR-10", now=NOW)
    assert not (root / "knowledge" / "plans" / "gone.md").exists()
    try:
        tracker.queue(root, "Escape", "Ada Okafor", spec="../outside.md", now=NOW)
        raise AssertionError("a spec page outside the space must be refused")
    except ValueError:
        pass
    pages.write(root / "knowledge" / "sources" / "2026" / "10" / "lin-har-7.md",
                {"id": pages.new_id(), "type": "source", "title": "HAR-7 moved", "status": "draft",
                 "generated": pages.generated("sweep", NOW), "issue": "HAR-7", "state": "In Progress",
                 "at": pages.iso(NOW)}, "In progress.\n")
    assert status.spec_states(root, NOW) == ["knowledge/plans/export.md"]
    assert pages.read(spec)[0]["state"] == "building"
