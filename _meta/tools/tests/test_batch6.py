"""The 2026-10-13 audit, batch 6: forgetting by id, the seed, agent notes as weak evidence,
mint signals, the vault, the person's own command at offboard."""
import json

import yaml

import forget
import mint
import offboard
import onboard
import pages
import research
import seed
import spaceyaml
import status
from conftest import NOW, make_space, mount_brain, write_page

HARBOR = {"id": "harbor", "kind": "projects", "title": "Harbor", "match": {"label": "Harbor"}}


def test_one_listing_by_id_finds_everything_about_a_subject(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    write_page(root, "knowledge/projects/harbor/plan.md", subject="harbor", title="Plan")
    write_page(root, "knowledge/sources/2026/10/m1.md", type="source", subject="harbor", title="A mail")
    write_page(root, "knowledge/sources/2026/10/m2.md", type="source", mentions=["harbor"], title="Another")
    feed = root / "_meta/state/feed/mail.jsonl"
    feed.parent.mkdir(parents=True)
    feed.write_text(json.dumps({"id": "x", "identifiers": {"labels": ["Harbor"]}}) + "\n", encoding="utf-8")
    found = forget.listing(root, "harbor")
    assert found["registry"] and "knowledge/projects/harbor/" in found["folders"]
    assert {"knowledge/projects/harbor/plan.md", "knowledge/sources/2026/10/m1.md",
            "knowledge/sources/2026/10/m2.md"} <= set(found["pages"])
    assert found["feeds"] == ["_meta/state/feed/mail.jsonl:1"]


def test_the_seed_sweeps_the_backfill_and_proposes_only_what_matters(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[dict(HARBOR, state="active")],
                      sources=[{"name": "mail", "adapter": "files", "audience": "owner"}])
    feed = root / "_meta/state/feed/mail.jsonl"
    feed.parent.mkdir(parents=True)
    feed.write_text(json.dumps({"id": "m1", "kind": "mail", "at": "2026-09-20T08:00:00Z", "subject": "Harbor plan",
                                "text": "The plan.", "identifiers": {"labels": ["Harbor"]}}) + "\n", encoding="utf-8")
    result = seed.run(root, days=90, now=NOW)
    assert result["records"] == 1 and result["witness"] == 1
    assert yaml.safe_load((root / "_meta/state/seed.yaml").read_text(encoding="utf-8"))["days"] == 90


def test_agent_notes_are_weak_evidence_with_a_confirmation(tmp_path):
    root = make_space(tmp_path / "brain", profile="brain", name="brain")
    records = tmp_path / "notes.jsonl"
    records.write_text(json.dumps({"id": "note-1", "weak": True, "about": "identity", "title": "Old memory",
                                   "text": "We sell working systems.",
                                   "claims": [{"think": "We sell working systems, not hours."}]}) + "\n",
                       encoding="utf-8")
    out = research.ingest(root, records)
    page = next(p for p in out["written"] if "notes-" in p)
    fm, body = pages.read(root / page)
    assert fm["weak"] is True and fm["source"] == "agent-notes" and "weak evidence" in body
    q = next(p for p in out["written"] if p.startswith("_inbound/questions/"))
    assert "agent notes, weak evidence" in pages.read(root / q)[0]["question"]


def test_a_second_regular_reader_and_a_recurring_group_propose_a_mint(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[dict(HARBOR, state="active")])
    log = root / "_meta/state/requests.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(json.dumps({"at": "2026-10-1%dT08:00:00Z" % d, "id": f"rq-{d}", "from": "chloe",
                                       "class": "status", "subject": "harbor", "outcome": "answered"}) + "\n"
                           for d in (1, 2, 0)), encoding="utf-8")
    for n in range(5):
        write_page(root, f"knowledge/sources/2026/10/g{n}.md", type="source", subject="harbor", title=f"Call {n}",
                   at=f"2026-10-0{n + 5}T08:00:00Z", kind="call", participants=["Ben Liu", "Mo Reyes"])
    space = spaceyaml.load(root)
    items = status.mint_signals(root, space, __import__("registry").Registry(root, space), NOW)
    ids = {i["id"] for i in items}
    assert "mint-signal-reader-chloe-harbor" in ids and "mint-signal-group-harbor" in ids
    assert status.mint_signals(root, space, __import__("registry").Registry(root, space), NOW) == items  # asked once


def test_a_vault_is_read_by_its_names_only(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    ada = make_space(tmp_path / "ada")
    mount_brain(ada, brain)
    report = mint.vault(ada, "hr", ["Ada Okafor", "Ben Liu"], online=False)
    assert not report.failed(), [s.row() for s in report.failed()]
    space = spaceyaml.load(tmp_path / "hr")
    assert space.data["vault"] is True and space.audience == ["Ada Okafor", "Ben Liu"]
    assert space.profile == "project"


def test_offboard_lists_raw_records_and_removes_them_only_on_the_persons_command(tmp_path, monkeypatch):
    monkeypatch.setattr(offboard, "run_cmd", lambda *a, **k: __import__("subprocess").CompletedProcess(a, 0, "", ""))
    root = make_space(tmp_path / "ada")
    write_page(root, "knowledge/sources/2026/10/m1.md", type="source", title="A mail")
    report = offboard.run(root, online=False)
    assert any(s.name == "raw records listed" for s in report.steps)
    assert (root / "knowledge/sources/2026/10/m1.md").exists()
    report = offboard.run(root, online=False, forget_records=True)
    assert any(s.name == "raw records removed" for s in report.steps)
    assert not (root / "knowledge/sources/2026/10/m1.md").exists()
