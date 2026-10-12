"""Content upkeep (vision section 13): the weekly review batch, conflict notes, spec state, ages."""
import datetime as dt
import json

import yaml

import check
import ground
import landing
import pages
import sign
import status
import validate
from conftest import NOW, make_space, mount_brain, write_page
from test_check import D, H, signed
from test_distill import witness


def batch_files(root):
    return sorted((root / "_inbound" / "candidates").glob("review-*.md"))


def errors(root):
    return [e for e in validate.validate(root) if not e.startswith("warning:")]


def test_window_findings_go_to_one_weekly_batch_not_separate_items(tmp_path):
    root = make_space(tmp_path / "ada", mode="fixture", time_scale=24, subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor"}])
    signed(root, "knowledge/projects/harbor/status.md", "# Status\n\nAll green.\n", type="snapshot",
           at=NOW - 8 * H)
    signed(root, "knowledge/projects/harbor/brief.md", "# Brief\n\nPilot.\n", type="snapshot",
           at=NOW - 9 * H)
    report = check.run(root, now=NOW)
    assert [h for h in report.hits if h.check == "past_window"] == []
    (path,) = batch_files(root)
    fm, body = pages.read(path)
    assert fm["type"] == "review" and fm["review_batch"] is True and len(fm["lines"]) == 2
    assert {l["page"] for l in fm["lines"]} == {"knowledge/projects/harbor/status.md",
                                                "knowledge/projects/harbor/brief.md"}
    assert "1." in body and "2." in body
    again = check.run(root, now=NOW + 2 * H)
    assert len(batch_files(root)) == 1, "one batch a scaled week"
    assert errors(root) == []


def test_batch_verdicts_by_line_verify_edit_and_retire(tmp_path):
    root = make_space(tmp_path / "ada", mode="fixture", time_scale=24, subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor"}])
    for name in ("a", "b", "c"):
        signed(root, f"knowledge/projects/harbor/{name}.md", f"# {name}\n\nOld {name}.\n",
               type="snapshot", at=NOW - 8 * H, title=name)
    check.run(root, now=NOW)
    (path,) = batch_files(root)
    fm, _ = pages.read(path)
    by_page = {l["page"].rsplit("/", 1)[-1]: l["n"] for l in fm["lines"]}
    bid = path.stem
    report = sign.apply(root, {f"{bid}#{by_page['a.md']}": "yes",
                               f"{bid}#{by_page['b.md']}": ["edit", "# b\n\nNew b.\n"],
                               f"{bid}#{by_page['c.md']}": "no"}, by="Ada Okafor", now=NOW + H)
    a, _ = pages.read(root / "knowledge/projects/harbor/a.md")
    assert a["verified_by"] == "Ada Okafor" and a["status"] == "stable"
    b, b_body = pages.read(root / "knowledge/projects/harbor/b.md")
    assert "New b." in b_body and b["signed_by"] == "Ada Okafor" and b["fingerprint"] == pages.fingerprint(b_body)
    c, _ = pages.read(root / "knowledge/projects/harbor/c.md")
    assert c["status"] == "deprecated"
    assert not path.exists(), "a fully answered batch is done"
    assert len(report.signed) + len(report.edited) + len(report.discarded) >= 3
    assert errors(root) == []


def test_conflict_note_travels_and_marks_the_page_contested(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor"}])
    signed(brain, "knowledge/projects/harbor/harbor.md", "# Harbor\n\ngo-live: 2026-11-03\n",
           id="p-0000f001", at=NOW - 3 * D)
    ada = make_space(tmp_path / "ada", name="ada", subjects=[])
    mount_brain(ada, brain)
    witness(ada, "rec-9", "harbor", "Moved again: go-live: 2026-11-10\n", NOW - D)
    report = check.run(ada, now=NOW)
    assert report.conflict_notes == 1
    (item,) = sorted((ada / "_outbound" / "queue").glob("*.md"))
    fm, body = pages.read(item)
    assert fm["conflict_note"] is True and fm["destination"] == "brain" and fm["class"] == "window"
    assert fm["page"] == "p-0000f001" and "2026-11-10" in body
    assert check.run(ada, now=NOW + H).conflict_notes == 0
    # it lands at the brain: the page is marked contested and the note waits for the batch
    landed = dict(fm, released_from="ada", release_class="window")
    for key in ("class", "stage_after", "listed", "destination", "from_space"):
        landed.pop(key, None)
    pages.write(brain / "_inbound" / "candidates" / f"{fm['id']}.md", landed, body)
    rep = landing.land(brain, now=NOW + 2 * H)
    assert rep.conflicts == [fm["id"]]
    page, _ = pages.read(brain / "knowledge/projects/harbor/harbor.md")
    assert page["contested"] == [fm["id"]] and page["status"] == "stable"
    assert (brain / "_inbound" / "conflicts" / f"{fm['id']}.md").exists()
    assert errors(brain) == []


def test_spec_state_follows_the_tracker(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "brain-rebuild", "kind": "topics", "title": "Brain"}])
    write_page(root, "knowledge/topics/brain-rebuild/spec.md", type="concept", title="Spec",
               subject="brain-rebuild", tracker="SM-12", state="proposed")
    for n, (st, at) in enumerate((("Todo", NOW - 3 * D), ("In Progress", NOW - D))):
        rel = f"knowledge/sources/2026/10/t-{n}.md"
        write_page(root, rel, type="source", title=f"SM-12 {st}", record=f"t-{n}", source="tracker",
                   kind="document", at=pages.iso(at), issue="SM-12", state=st, audience="owner",
                   identifiers={"tracker": "SM"})
    status.run(root, now=NOW)
    fm, _ = pages.read(root / "knowledge/topics/brain-rebuild/spec.md")
    assert fm["state"] == "building"


def test_ground_lines_carry_tier_age_window_and_the_pin(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects", "title": "Harbor"}])
    signed(root, "knowledge/projects/harbor/old.md", "# Old\n", type="snapshot", at=NOW - 40 * D,
           id="p-0000f101", title="Old status")
    write_page(root, "knowledge/sources/2026/10/m-1.md", id="p-0000f102", type="source", title="A mail",
               at=pages.iso(NOW - D), record="m-1", source="mail", kind="mail", audience="owner")
    lines = ground.lines(root, ["p-0000f101", "p-0000f102"], now=NOW)
    assert lines[0].startswith("- Old status (signed, mine, ") and "40 days" in lines[0]
    assert "past window" in lines[0] and "confidence low" in lines[0]
    assert "(witness, mine, " in lines[1] and "1 day" in lines[1]
    assert any(l.startswith("index:") for l in lines)
