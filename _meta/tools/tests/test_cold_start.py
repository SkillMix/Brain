"""Research first, then the archives: confirmations and candidates, nothing signed."""
import json

import yaml

import cold_start
import needs_you
import pages
import research
import validate
from conftest import NOW, make_space


def test_research_lands_public_pages_and_confirmations(tmp_path):
    root = make_space(tmp_path / "brain", profile="brain", name="brain")
    records = tmp_path / "web.jsonl"
    records.write_text(json.dumps({
        "id": "site-about", "url": "https://lantern.example/about", "title": "About Lantern Works",
        "at": "2026-10-12T08:00:00Z", "about": "identity", "text": "Lantern Works builds harbour software.",
        "claims": [{"think": "Lantern Works builds software for harbours and clinics.", "candidate": "identity/spine.md",
                    "depends": 3}]}) + "\n" + json.dumps({"id": "x", "url": "https://x.example", "about": "gossip"}) + "\n",
        encoding="utf-8")
    out = research.ingest(root, records)
    assert len(out["written"]) == 2 and len(out["skipped"]) == 1
    page = next((root / "knowledge" / "sources").rglob("web-site-about.md"))
    fm, body = pages.read(page)
    assert fm["source"] == "web" and fm["url"] == "https://lantern.example/about" and fm["status"] == "draft"
    q = root / "_inbound" / "questions" / "research-site-about-1.md"
    qfm, _ = pages.read(q)
    assert qfm["question"].startswith("I think: Lantern Works builds software") and qfm["batch"] == "public web"
    assert research.ingest(root, records)["written"] == []
    assert [e for e in validate.validate(root) if not e.startswith("warning:")] == []


def test_cold_start_confirms_from_the_archive_in_batches(tmp_path):
    root = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor"},
        {"id": "beacon", "kind": "projects", "title": "Beacon"},
        {"id": "old", "kind": "projects", "title": "Old", "state": "dormant"}])
    archive = tmp_path / "archive"
    (archive / "projects").mkdir(parents=True)
    (archive / "projects" / "harbor.md").write_text("---\ntitle: Harbor\n---\nHarbor goes live in November.\n",
                                                    encoding="utf-8")
    many = [{"id": f"person-{n}", "think": f"Person {n} works here.", "based_on": "archive/people.md",
             "candidate": "none"} for n in range(30)]
    conf = tmp_path / "confirmations.yaml"
    conf.write_text(yaml.safe_dump({"batches": [
        {"name": "people", "depends": 1, "confirmations": many},
        {"name": "projects", "depends": 2, "confirmations": [
            {"id": "projects", "think": "Harbor and Beacon are active.", "based_on": "archive/projects/harbor.md",
             "candidate": "knowledge/projects/<all>/"}]}]}), encoding="utf-8")
    out = cold_start.run(root, conf, archive=archive, label="old-brain")
    assert out["errors"] == []
    questions = list((root / "_inbound" / "questions").glob("cold-start-*.md"))
    assert len(questions) == 31
    batches = {pages.read(q)[0]["batch"] for q in questions}
    assert {"people (1)", "people (2)", "projects"} <= batches
    candidates = sorted(p.name for p in (root / "_inbound" / "candidates").glob("cold-start-*.md"))
    assert candidates == ["cold-start-beacon.md", "cold-start-harbor.md"], "active subjects only"
    fm, body = pages.read(root / "_inbound" / "candidates" / "cold-start-harbor.md")
    assert fm["status"] == "draft" and fm["evidence"] == ["archive:old-brain/projects/harbor.md"]
    assert "Harbor goes live in November." in body
    order = [i.id for i in needs_you.collect(root, now=NOW, online=False)]
    assert order, "the confirmations reach Needs you"
    again = cold_start.run(root, conf, archive=archive)
    assert again["written"] == [] and len(again["kept"]) == 33


def test_an_identity_confirmation_is_a_question_with_the_archive_and_no_candidate(tmp_path):
    # sign lands pages only under knowledge/, and identity/ is guarded: a candidate for it could never
    # be signed and turned the check red, so the question carries the archive and names the page
    root = make_space(tmp_path / "brain", profile="brain", name="brain")
    archive = tmp_path / "archive"
    (archive / "identity").mkdir(parents=True)
    (archive / "identity" / "values.md").write_text("---\ntitle: Values\n---\nWe say what we do not know.\n",
                                                    encoding="utf-8")
    conf = tmp_path / "confirmations.yaml"
    conf.write_text(yaml.safe_dump({"batches": [{"name": "identity", "depends": 3, "confirmations": [
        {"id": "values", "think": "Honesty about what we do not know is a value.",
         "based_on": "archive/identity/values.md", "candidate": "identity/values.md"}]}]}), encoding="utf-8")
    out = cold_start.run(root, conf, archive=archive, label="old-brain")
    assert out["errors"] == [], out["errors"]
    assert not list((root / "_inbound" / "candidates").glob("cold-start-*.md"))
    fm, body = pages.read(root / "_inbound" / "questions" / "cold-start-values.md")
    assert fm["payload"] == "identity/values.md"
    assert "We say what we do not know." in body
    assert "edit `identity/values.md` yourself" in body
    assert fm["question"].endswith("If yes, edit identity/values.md yourself."), "Needs you shows only this line"
    assert [e for e in validate.validate(root) if not e.startswith("warning:")] == []


def test_an_older_identity_candidate_is_refused_with_what_to_do(tmp_path):
    # a space where an older cold start already drafted one: the check says how to clear it
    root = make_space(tmp_path / "brain", profile="brain", name="brain")
    pages.write(root / "_inbound" / "candidates" / "cold-start-values.md",
                {"id": pages.new_id(), "type": "concept", "title": "Values", "status": "draft",
                 "generated": pages.generated("cold-start", NOW), "kind": "identity",
                 "target": "identity/values.md"}, "# Values\n")
    errors = [e for e in validate.validate(root) if "cold-start-values" in e]
    assert len(errors) == 1 and "move this draft out of _inbound/candidates/" in errors[0], errors
