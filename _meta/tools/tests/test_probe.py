"""The probe: recall per method (walk, search, decisions) and per kind of question."""
import json

import yaml

import tests_run
from conftest import NOW, make_space, write_page


def answers_file(root, questions):
    path = root / "_meta" / "tests" / "answers.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"questions": questions}, sort_keys=False), encoding="utf-8")
    return path


def setup(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor"},
        {"id": "beacon", "kind": "projects", "title": "Beacon"}])
    write_page(root, "knowledge/projects/harbor/harbor.md", type="entity", title="Harbor",
               subject="harbor", body="Harbor goes live on 3 November.\n")
    write_page(root, "live/subjects/beacon.md", type="snapshot", title="Beacon: status",
               subject="beacon", body="## Timeline\n- BEACON-12 closed (witness, mine, 2026-10-04)\n")
    # reachable by search only: a desk note no hub links
    (root / "work" / "topics" / "misc").mkdir(parents=True, exist_ok=True)
    (root / "work" / "topics" / "misc" / "note.md").write_text("The pilot budget is 40k.\n", encoding="utf-8")
    answers_file(root, [
        {"question": "When does Harbor go live?", "subject": "harbor", "expect": ["3 November"],
         "asker": "owner", "kind": "walk"},
        {"question": "What is going on with Beacon?", "subject": "beacon", "expect": ["BEACON-12 closed"],
         "asker": "owner", "kind": "status"},
        {"question": "What is the pilot budget?", "subject": "misc", "expect": ["40k"],
         "asker": "owner"}])
    return root


def test_probe_records_recall_per_method_and_kind(tmp_path):
    root = setup(tmp_path)
    report = tests_run.probe(root, now=NOW)
    kinds = report.kinds
    assert kinds["walk"]["walk"] == [1, 2] and kinds["walk"]["search"] == [2, 2]
    assert kinds["status"]["walk"] == [1, 1] and kinds["status"]["search"] == [1, 1]
    assert kinds["walk"]["decisions"] is None or kinds["walk"]["decisions"][1] == 2
    line = json.loads((root / "_meta" / "state" / "probe.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert line["at"] == "2026-10-12T09:00:00Z" and line["kinds"]["status"]["walk"] == [1, 1]
    summary = tests_run.probe_summary(root)
    assert summary["walk"] == [2, 3] and summary["search"] == [3, 3]


def test_probe_cli_and_answers_still_pass_by_either_method(tmp_path):
    root = setup(tmp_path)
    assert tests_run.main([str(root), "probe"]) == 0
    report = tests_run.answers(root, now=NOW)
    assert report.passed == 3 and report.failed == 0
