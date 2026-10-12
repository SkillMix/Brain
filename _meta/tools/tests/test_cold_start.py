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
