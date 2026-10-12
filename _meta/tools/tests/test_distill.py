import datetime as dt
import json

import pages
import distill
from conftest import NOW, make_space, write_page

H = dt.timedelta(hours=1)
D = dt.timedelta(days=1)


def witness(root, rid, subject, text, at, title=None, mentions=None, generated_at=None):
    rel = f"knowledge/sources/{at:%Y}/{at:%m}/{rid}.md"
    fm = {"id": pages.new_id(), "type": "source", "title": title or text.strip()[:40],
          "status": "draft", "generated": pages.generated("sweep", generated_at or at),
          "record": rid, "source": "mail", "kind": "mail", "at": pages.iso(at),
          "from": "Ben Liu", "audience": "owner", "identifiers": {"thread": "t-1"}}
    if subject:
        fm["subject"] = subject
    if mentions:
        fm["mentions"] = list(mentions)
    return pages.write(root / rel, fm, text)


def candidates(root):
    return sorted(p for p in (root / "_inbound" / "candidates").glob("*.md"))


def evidence_lines(body):
    section = body.split("## New evidence", 1)[1]
    return [line for line in section.splitlines() if line.startswith("- ")]


def test_two_evidence_pages_yield_one_candidate(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor", "state": "active"}])
    witness(root, "rec-1", "harbor", "Kickoff agreed for next week.\n", NOW - 2 * D,
            title="Kickoff")
    witness(root, "rec-2", "harbor", "Scope confirmed: two phases.\n", NOW - D)
    report = distill.run(root, now=NOW)
    assert report.candidates_written == 1
    assert report.refreshed == 0
    assert report.subjects == ["harbor"]
    files = candidates(root)
    assert [p.name for p in files] == ["harbor.md"]
    fm, body = pages.read(files[0])
    assert fm["type"] == "entity"
    assert fm["status"] == "draft"
    assert fm["subject"] == "harbor"
    assert fm["kind"] == "projects"
    assert fm["target"] == "knowledge/projects/harbor/harbor.md"
    assert fm["generated"]["by"] == "distill"
    assert sorted(fm["evidence"]) == ["rec-1", "rec-2"]
    assert "destination" not in fm
    assert body.startswith("# Harbor")
    lines = evidence_lines(body)
    assert len(lines) == 2
    assert lines[0].startswith(f"- {(NOW - 2 * D):%Y-%m-%d}: Kickoff (witness, rec-1)")
    assert "(witness, rec-2)" in lines[1]
    # watermark and run line
    assert (root / "_meta" / "state" / "distill-watermark.yaml").exists()
    runs = (root / "_meta" / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    line = json.loads(runs[-1])
    assert line["routine"] == "distill" and line["space"] == "ada"
    assert line["cost_tokens"] > 0


def test_second_run_refreshes_the_same_candidate(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor"}])
    witness(root, "rec-1", "harbor", "One.\n", NOW - 2 * D)
    witness(root, "rec-2", "harbor", "Two.\n", NOW - D)
    first = distill.run(root, now=NOW)
    assert first.candidates_written == 1
    path = candidates(root)[0]
    fm1, _ = pages.read(path)
    witness(root, "rec-3", "harbor", "Three.\n", NOW + H / 2)
    second = distill.run(root, now=NOW + H)
    assert second.refreshed == 1
    assert second.candidates_written == 0
    files = candidates(root)
    assert len(files) == 1 and files[0] == path
    fm2, body = pages.read(path)
    assert fm2["id"] == fm1["id"]
    assert sorted(fm2["evidence"]) == ["rec-1", "rec-2", "rec-3"]
    assert len(evidence_lines(body)) == 3
    # a quiet third run proposes nothing
    third = distill.run(root, now=NOW + 2 * H)
    assert third.candidates_written == 0 and third.refreshed == 0


def test_dormant_subject_yields_nothing(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "old-deal", "kind": "projects", "state": "dormant"}])
    witness(root, "rec-1", "old-deal", "Someone asked about it.\n", NOW - D)
    report = distill.run(root, now=NOW)
    assert report.candidates_written == 0
    assert candidates(root) == []
    assert report.skipped >= 1


def test_not_stateful_not_important_is_skipped_unless_asked(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "ferry-notes", "kind": "topics", "stateful": False},
        {"id": "clinic-notes", "kind": "topics", "stateful": False}])
    witness(root, "rec-1", "ferry-notes", "A note.\n", NOW - D)
    witness(root, "rec-2", "clinic-notes", "Another note.\n", NOW - D)
    log = root / "_meta" / "questions.log"
    log.write_text("# Question log\n" + json.dumps({
        "at": pages.iso(NOW - H), "question": "what about the clinic notes?",
        "subject": "clinic-notes", "tier": "evidence", "pages": [],
        "needed_steering": False, "answered_by_request": False}) + "\n", encoding="utf-8")
    report = distill.run(root, now=NOW)
    assert [p.name for p in candidates(root)] == ["clinic-notes.md"]
    assert report.skipped == 1


def test_home_elsewhere_sets_destination(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor"}])
    witness(root, "rec-1", "harbor", "Go-live moved.\n", NOW - D)
    distill.run(root, now=NOW)
    fm, _ = pages.read(candidates(root)[0])
    assert fm["destination"] == "harbor"


def test_existing_page_is_the_target_and_its_body_is_kept(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor"}])
    write_page(root, "knowledge/projects/harbor/brief.md", type="snapshot", title="Harbor brief",
               body="# Harbor brief\n\nTwo phases.\n")
    write_page(root, "knowledge/projects/harbor/index.md", title="Harbor hub", body="# Harbor\n")
    witness(root, "rec-1", "harbor", "Phase two slips a week.\n", NOW - D)
    distill.run(root, now=NOW)
    fm, body = pages.read(candidates(root)[0])
    assert fm["target"] == "knowledge/projects/harbor/brief.md"
    assert fm["type"] == "snapshot"
    assert body.startswith("# Harbor brief\n\nTwo phases.\n")
    assert "## New evidence" in body


def test_mentions_count_as_evidence(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "beacon", "kind": "projects", "title": "Beacon"}])
    witness(root, "rec-9", None, "Beacon and others were discussed.\n", NOW - D,
            mentions=["beacon"])
    report = distill.run(root, now=NOW)
    assert report.candidates_written == 1
    fm, _ = pages.read(candidates(root)[0])
    assert fm["evidence"] == ["rec-9"]


def test_decision_witness_becomes_its_own_candidate(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor"}])
    at = dt.datetime(2026, 10, 2, 9, 0, tzinfo=dt.timezone.utc)
    witness(root, "rec-d", "harbor", "Decision: Harbor ships in two phases\n\nAgreed on the call.\n", at)
    report = distill.run(root, now=NOW)
    names = [p.name for p in candidates(root)]
    assert "harbor.md" in names
    assert "2026-10-02-harbor-ships-in-two-phases.md" in names
    fm, body = pages.read(root / "_inbound" / "candidates" / "2026-10-02-harbor-ships-in-two-phases.md")
    assert fm["type"] == "decision"
    assert fm["title"] == "Harbor ships in two phases"
    assert fm["target"] == "knowledge/decisions/2026-10-02-harbor-ships-in-two-phases.md"
    assert fm["kind"] == "decisions"
    assert fm["subject"] == "harbor"
    assert fm["evidence"] == ["rec-d"]
    assert body.startswith("Decision: Harbor ships in two phases")
    assert report.candidates_written == 2


def test_never_writes_stable(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor"}])
    witness(root, "rec-1", "harbor", "Decision: ship it\n", NOW - D)
    witness(root, "rec-2", "harbor", "Plain evidence.\n", NOW - D)
    distill.run(root, now=NOW)
    for path in candidates(root):
        fm, _ = pages.read(path)
        assert fm["status"] == "draft"


def test_cli_runs(tmp_path, capsys):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects"}])
    witness(root, "rec-1", "harbor", "Hello.\n", NOW - D)
    assert distill.main([str(root)]) == 0
    assert "candidates" in capsys.readouterr().out
