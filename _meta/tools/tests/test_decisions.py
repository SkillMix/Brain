"""The decisions index: built from signed decisions, in force first, read across mounts."""
import yaml

import decisions
import pages
import status
import tests_run
from conftest import NOW, make_space, mount_brain, write_page


def decision(root, name, pid, title, body, at, **fm):
    return write_page(root, f"knowledge/decisions/{name}.md", id=pid, type="decision", title=title,
                      status="stable", body=body, signed_by="Chloe Martin", signed_at=at,
                      signed_in=root.name, fingerprint=pages.fingerprint(body), subject="sales", **fm)


def test_build_and_find_put_the_decision_in_force_first(tmp_path):
    root = make_space(tmp_path / "chloe", subjects=[{"id": "sales", "kind": "domains", "title": "Sales"}])
    decision(root, "2026-10-01-pilots-one-quarter", "p-0000d101", "Pilots run one quarter",
             "Decision: pilots run for one quarter.\n", "2026-10-01T10:00:00Z")
    decision(root, "2026-10-09-pilots-two-quarters", "p-0000d102", "Pilots run two quarters",
             "Decision: pilots run for two quarters.\n", "2026-10-09T10:00:00Z", supersedes="p-0000d101")
    rows = decisions.build(root)
    assert len(rows) == 2
    data = yaml.safe_load((root / "_meta/state/decisions.yaml").read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in data["decisions"]}
    assert by_id["p-0000d101"]["superseded_by"] == "p-0000d102"
    found = decisions.find(root, text="What did we decide about pilots?")
    assert [r["id"] for r in found] == ["p-0000d102", "p-0000d101"]
    assert decisions.find(root, subject="sales")[0]["id"] == "p-0000d102"
    assert decisions.find(root, text="ferries") == []
    assert decisions.main([str(root), "find", "--text", "pilots"]) == 0


def test_find_reads_the_mounts_and_feeds_the_probe(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", subjects=[
        {"id": "sales", "kind": "domains", "title": "Sales"}])
    decision(brain, "2026-10-01-pilots-one-quarter", "p-0000d201", "Pilots run one quarter",
             "Decision: pilots run for one quarter.\n", "2026-10-01T10:00:00Z")
    decisions.build(brain)
    root = make_space(tmp_path / "chloe")
    mount_brain(root, brain)
    found = decisions.find(root, text="pilots")
    assert found and found[0]["layer"] == "brain"
    path = root / "_meta" / "tests" / "answers.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"questions": [
        {"question": "What did we decide about pilots?", "subject": "sales", "kind": "decision",
         "expect": ["one quarter"], "asker": "owner"}]}), encoding="utf-8")
    report = tests_run.probe(root, now=NOW)
    assert report.kinds["decision"]["decisions"] == [1, 1]


def test_status_rebuilds_the_index(tmp_path):
    root = make_space(tmp_path / "chloe", subjects=[{"id": "sales", "kind": "domains", "title": "Sales"}])
    decision(root, "2026-10-01-pilots-one-quarter", "p-0000d301", "Pilots run one quarter",
             "Decision: pilots run for one quarter.\n", "2026-10-01T10:00:00Z")
    status.run(root, now=NOW)
    data = yaml.safe_load((root / "_meta/state/decisions.yaml").read_text(encoding="utf-8"))
    assert [r["id"] for r in data["decisions"]] == ["p-0000d301"]
