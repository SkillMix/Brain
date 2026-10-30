import datetime as dt
import json
import re

import pytest

import distill
import link
import pages
import sign
import sign_questions
import spaceyaml
import sweep
import yaml
from conftest import NOW, make_space, mount_brain, write_page
from test_link import ENTRY_A, journal_ws
from test_sweep import walled

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
        {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor",
         "classification": walled("harbor", ["ada"])}])
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


def company(home):
    return {"class": "company", "state": "settled", "home": home, "reason": "company work",
            "decided_by": "Ada Okafor", "at": "2026-10-01T09:00:00Z"}


def test_the_page_at_the_subjects_home_is_the_target_and_its_body_is_kept(tmp_path):
    # the brain is mounted as `brain` but named otherwise, as a company's brain is; a copy left
    # here is older than the page at the home, where the candidate lands
    brain = make_space(tmp_path / "brain", profile="brain", name="northwind", subjects=[
        {"id": "tidewater", "kind": "clients", "title": "Tidewater", "classification": company("northwind")}])
    write_page(brain, "knowledge/clients/tidewater/tidewater.md", type="entity", title="Tidewater Ports",
               status="stable", evidence=["rec-0"], body="# Tidewater Ports\n\nTwo terminals. [src: rec-0]\n")
    write_page(brain, "knowledge/clients/tidewater/index.md", title="Tidewater hub", body="# Tidewater\n")
    root = make_space(tmp_path / "ada")
    mount_brain(root, brain)
    write_page(root, "knowledge/clients/tidewater/notes.md", title="Old notes", body="# Old notes\n")
    witness(root, "rec-1", "tidewater", "The second terminal opens in May.\n", NOW - D)
    distill.run(root, now=NOW)
    path = candidates(root)[0]
    fm, body = pages.read(path)
    assert fm["destination"] == "northwind"
    assert fm["target"] == "knowledge/clients/tidewater/tidewater.md"
    assert fm["type"] == "entity" and fm["title"] == "Tidewater Ports"
    assert body.startswith("# Tidewater Ports\n\nTwo terminals. [src: rec-0]\n")
    # the page's lines keep their sources: its evidence comes along, and is not new to the signer
    assert fm["evidence"] == ["rec-1", "rec-0"]
    assert [line.split("(witness, ")[1] for line in evidence_lines(body)] == ["rec-1)"]
    assert distill.verify(root, path) == []


def test_the_pages_own_evidence_here_is_not_listed_as_new(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor"}])
    witness(root, "rec-0", "harbor", "Two phases.\n", NOW - 2 * D)
    write_page(root, "knowledge/projects/harbor/harbor.md", type="entity", title="Harbor", status="stable",
               evidence=["rec-0"], body="# Harbor\n\nTwo phases. [src: rec-0]\n")
    witness(root, "rec-1", "harbor", "Phase two slips a week.\n", NOW - D)
    distill.run(root, now=NOW)
    path = candidates(root)[0]
    fm, body = pages.read(path)
    assert fm["evidence"] == ["rec-0", "rec-1"]
    assert [line.split("(witness, ")[1] for line in evidence_lines(body)] == ["rec-1)"]
    assert distill.verify(root, path) == []


def test_a_draft_begun_from_the_stub_takes_up_the_home_page_once_found(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="northwind", subjects=[
        {"id": "tidewater", "kind": "clients", "title": "Tidewater", "classification": company("northwind")}])
    root = make_space(tmp_path / "ada")
    mount_brain(root, brain)
    witness(root, "rec-1", "tidewater", "The second terminal opens in May.\n", NOW - D)
    distill.run(root, now=NOW)   # no page at the home yet: the stub
    fm1, body = pages.read(candidates(root)[0])
    assert body.startswith("# Tidewater\n\n## New evidence")
    write_page(brain, "knowledge/clients/tidewater/tidewater.md", type="entity", title="Tidewater Ports",
               status="stable", evidence=["rec-0"], body="# Tidewater Ports\n\nTwo terminals. [src: rec-0]\n")
    witness(root, "rec-2", "tidewater", "Cranes delivered.\n", NOW + H / 2)
    assert distill.run(root, now=NOW + H).refreshed == 1
    fm2, body = pages.read(candidates(root)[0])
    assert fm2["id"] == fm1["id"] and fm2["title"] == "Tidewater Ports"
    assert body.startswith("# Tidewater Ports\n\nTwo terminals. [src: rec-0]\n")
    assert sorted(fm2["evidence"]) == ["rec-0", "rec-1", "rec-2"] and len(evidence_lines(body)) == 2
    # a body the agent wrote is its own: the next refresh keeps it
    pages.write(candidates(root)[0], fm2, "# Tidewater Ports\n\nThree terminals. [src: rec-2]\n")
    witness(root, "rec-3", "tidewater", "Opening date set.\n", NOW + 3 * H / 2)
    distill.run(root, now=NOW + 2 * H)
    fm3, body = pages.read(candidates(root)[0])
    assert body.startswith("# Tidewater Ports\n\nThree terminals. [src: rec-2]\n")
    assert sorted(fm3["evidence"]) == ["rec-0", "rec-1", "rec-2", "rec-3"] and len(evidence_lines(body)) == 3


STAKES = "records the offer the client accepted"


def flagged_page(root, n=4, **subject):
    """Harbor's signed page: one line per record behind it, `n` records, and the check's flag. The
    watermark is past them, so the next run sees only what arrives after."""
    ids = [f"rec-{i}" for i in range(n)]
    for i, rid in enumerate(ids):
        witness(root, rid, "harbor", f"Fact {i}.\n", NOW - (n + 1 - i) * D)
    body = "# Harbor\n\n" + "".join(f"Fact {i}. [src: {rid}]\n\n" for i, rid in enumerate(ids))
    write_page(root, "knowledge/projects/harbor/harbor.md", id="p-0000a001", type="entity", title="Harbor",
               status="stable", evidence=ids, high_stakes=STAKES, body=body)
    distill.write_watermark(root, distill.ROUTINE, {"at": pages.iso(NOW - D)})
    return ids, body


def test_a_draft_over_a_flagged_page_carries_its_evidence_and_its_flag(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects", "title": "Harbor"}])
    ids, page_body = flagged_page(root)
    witness(root, "rec-new", "harbor", "Phase two slips a week.\n", NOW - H)
    distill.run(root, now=NOW)
    path = candidates(root)[0]
    fm, body = pages.read(path)
    assert body.startswith(page_body)
    assert fm["evidence"] == ids + ["rec-new"] and fm["high_stakes"] == STAKES
    assert [line.split("(witness, ")[1] for line in evidence_lines(body)] == ["rec-new)"]
    assert distill.verify(root, path) == []


def test_a_kept_draft_over_the_page_takes_up_the_evidence_it_lacks(tmp_path):
    # a draft an older distill began from the page: the page's body, only the record that was new
    # then, no flag. The body is kept, and the page's records come along.
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects", "title": "Harbor"}])
    ids, page_body = flagged_page(root)
    witness(root, "rec-new", "harbor", "Phase two slips a week.\n", NOW - 2 * H)
    written = page_body + "Phase two slips a week. [src: rec-new]\n"
    path = write_page(root, "_inbound/candidates/harbor.md", id="p-0000c001", type="entity", title="Harbor",
                      subject="harbor", kind="projects", target="knowledge/projects/harbor/harbor.md",
                      evidence=["rec-new"], body=written)
    assert len(distill.verify(root, path)) == len(ids)   # every inherited line: not in evidence
    witness(root, "rec-later", "harbor", "Cranes delivered.\n", NOW - H)
    assert distill.run(root, now=NOW).refreshed == 1
    fm, body = pages.read(path)
    assert body.startswith(written)
    assert fm["evidence"] == ids + ["rec-new", "rec-later"] and fm["high_stakes"] == STAKES
    assert [line.split("(witness, ")[1] for line in evidence_lines(body)] == ["rec-new)", "rec-later)"]
    assert distill.verify(root, path) == []


def test_signing_a_refresh_of_a_flagged_page_keeps_its_records_and_its_window(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor",
         "classification": walled("harbor", ["ada"])}])
    ids, _ = flagged_page(root)
    witness(root, "rec-new", "harbor", "Phase two slips a week.\n", NOW - H)
    distill.run(root, now=NOW)
    path = candidates(root)[0]
    fm, body = pages.read(path)
    assert fm["destination"] == "harbor" and distill.verify(root, path) == []
    report = sign.apply(root, {fm["id"]: ("yes", "")}, by="Ada Okafor", now=NOW + H)
    assert report.signed == [fm["id"]]
    page_fm, _ = pages.read(root / "knowledge/projects/harbor/harbor.md")
    assert page_fm["id"] == "p-0000a001" and page_fm["status"] == "stable"
    assert page_fm["evidence"] == ids + ["rec-new"] and page_fm["high_stakes"] == STAKES
    queued, = (root / "_outbound" / "queue").glob("*.md")
    qfm, _ = pages.read(queued)
    assert qfm["class"] == "window" and qfm["high_stakes"] == STAKES
    assert pages.parse_iso(qfm["stage_after"]) == NOW + H + spaceyaml.load(root).window()
    assert qfm["evidence"] == ids + ["rec-new"]


def test_a_mention_alone_is_never_evidence(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "beacon", "kind": "projects", "title": "Beacon"}])
    witness(root, "rec-9", None, "Beacon and others were discussed.\n", NOW - D,
            mentions=["beacon"])
    report = distill.run(root, now=NOW)
    assert report.candidates_written == 0   # evidence attaches by identifier only (vision section 7)

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


def test_a_question_counts_whatever_tier_its_answer_had(tmp_path):
    """The query skill logs witness, working or desk answers; any logged question about a
    subject makes it 'asked about'."""
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "ferry-notes", "kind": "topics", "stateful": False}])
    witness(root, "rec-1", "ferry-notes", "A note.\n", NOW - D)
    (root / "_meta" / "questions.log").write_text("# Question log\n" + json.dumps({
        "at": pages.iso(NOW - H), "question": "what about the ferry notes?",
        "subject": "ferry-notes", "tier": "desk", "pages": []}) + "\n", encoding="utf-8")
    distill.run(root, now=NOW)
    assert [p.name for p in candidates(root)] == ["ferry-notes.md"]


def test_daily_note_entries_that_name_a_subject_become_evidence(tmp_path):
    """What a session captured in the daily note reaches distill: an entry joins a subject by a
    `subject:` line or a desk path, never by a word it merely mentions."""
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor", "important": True},
        {"id": "ferry", "kind": "projects", "title": "Ferry", "important": True}])
    note = root / "journal" / "2026" / "10" / "2026-10-04.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "# 2026-10-04\n\n"
        "## 10:00Z: go-live agreed\n\n- Agreed with Ben: Harbor goes live on 3 November.\n- subject: harbor\n\n"
        "## 11:30Z: kickoff notes filed\n\n- Notes in `work/projects/harbor/kickoff/`.\n\n"
        "## 12:00Z: ferry mentioned only\n\n- The ferry came up in passing.\n", encoding="utf-8")
    distill.run(root, now=NOW)
    assert [p.name for p in candidates(root)] == ["harbor.md"]
    _, body = pages.read(root / "_inbound" / "candidates" / "harbor.md")
    assert "go-live agreed" in body and "kickoff notes filed" in body and "journal" in body
    assert "Harbor goes live" not in body   # titles only, never the entry's text


def test_an_informs_decision_becomes_a_decision_candidate(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects", "title": "Harbor"}])
    write_page(root, "knowledge/sources/2026/10/p1.md", type="source", title="Kickoff", subject="harbor",
               at="2026-10-11T09:00:00Z", record="p1", body="[00:01:00] Chloe: forty leads.\n",
               informs=[{"kind": "decision", "text": "The first list holds forty leads"}])
    distill.run(root, now=NOW)
    cands = list((root / "_inbound" / "candidates").glob("*forty-leads*.md"))
    assert len(cands) == 1
    fm, body = pages.read(cands[0])
    assert fm["type"] == "decision" and fm["subject"] == "harbor" and "forty leads." in body


def test_a_record_that_lands_after_its_window_is_new_evidence(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects", "title": "Harbor",
                                                   "important": True}])
    witness(root, "rec-1", "harbor", "Kickoff agreed.\n", NOW - 2 * D)
    distill.run(root, now=NOW)
    # generated before that run, released after it: the window held it in the other space
    pages.write(root / "_inbound" / "candidates" / "p-0000aaaa.md",
                {"id": "p-0000aaaa", "type": "source", "title": "Scope", "status": "draft",
                 "generated": pages.generated("sweep", NOW - D), "record": "rec-2", "subject": "harbor",
                 "at": pages.iso(NOW - 3 * D), "released_from": "bob", "released_at": pages.iso(NOW + H)},
                "Scope confirmed.\n")
    second = distill.run(root, now=NOW + 2 * H)
    assert second.refreshed == 1
    fm, _ = pages.read(root / "_inbound" / "candidates" / "harbor.md")
    assert "rec-2" in fm["evidence"]


def test_a_routed_page_is_evidence_for_its_subject_here(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects", "title": "Harbor",
                                                   "important": True}])
    write_page(root, "knowledge/sources/2026/10/c1.md", type="source", title="Kickoff", at="2026-10-11T09:00:00Z",
               record="c1", body="Scope agreed.\n", routing={"decision": "route", "routes": ["ada/harbor"]})
    distill.run(root, now=NOW)
    assert (root / "_inbound" / "candidates" / "harbor.md").exists()


# ----- set aside, rebuild, the archive switch (understand-first design 4.7) --------------------
def test_set_aside_ids_and_the_checks_flag_stay_after_a_refresh(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor"}])
    witness(root, "rec-1", "harbor", "One.\n", NOW - 2 * D)
    witness(root, "rec-2", "harbor", "About another client.\n", NOW - D, title="Other client")
    distill.run(root, now=NOW)
    path = candidates(root)[0]
    fm, body = pages.read(path)
    aside = [{"id": "rec-2", "why": "about another client"}]
    pages.write(path, dict(fm, evidence=["rec-1"], set_aside=aside, high_stakes="a contract we agree to"), body)
    # the record is swept again (a re-export) and a new one arrives
    witness(root, "rec-2", "harbor", "About another client.\n", NOW - D, title="Other client",
            generated_at=NOW + H / 2)
    witness(root, "rec-3", "harbor", "Three.\n", NOW + H / 2)
    report = distill.run(root, now=NOW + H)
    assert report.refreshed == 1
    fm2, body2 = pages.read(path)
    assert fm2["evidence"] == ["rec-1", "rec-3"]
    assert fm2["set_aside"] == aside and fm2["high_stakes"] == "a contract we agree to"
    assert "rec-2" not in "\n".join(evidence_lines(body2))
    assert len(evidence_lines(body2)) == 2


def test_rebuild_drops_the_old_body_and_the_merged_list_and_keeps_the_id(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor"}])
    witness(root, "rec-1", "harbor", "One.\n", NOW - 2 * D)
    witness(root, "rec-2", "harbor", "Two.\n", NOW - D)
    distill.run(root, now=NOW)
    path = candidates(root)[0]
    fm, body = pages.read(path)
    wrong = body.replace("# Harbor\n", "# Harbor\n\nA line about another client. [src: rec-old]\n")
    pages.write(path, dict(fm, evidence=["rec-1", "rec-old"], set_aside=[{"id": "rec-2", "why": "x"}],
                           checked={"by": "Ben Liu", "at": pages.iso(NOW)}), wrong)
    assert distill.main([str(root), "--rebuild", "--subject", "harbor"]) == 0
    fm2, body2 = pages.read(path)
    assert fm2["id"] == fm["id"]
    assert fm2["evidence"] == ["rec-1", "rec-2"]   # as it is now, not merged with the old list
    assert "set_aside" not in fm2 and "checked" not in fm2
    assert "another client" not in body2
    assert body2.startswith("# Harbor\n\n## New evidence\n")
    assert len(evidence_lines(body2)) == 2


def test_rebuild_without_a_subject_rewrites_what_the_run_would_write(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor"},
        {"id": "ferry", "kind": "projects", "title": "Ferry"}])
    witness(root, "rec-1", "harbor", "One.\n", NOW - 2 * D)
    witness(root, "rec-f", "ferry", "Ferry one.\n", NOW - 2 * D)
    distill.run(root, now=NOW)
    for name in ("harbor", "ferry"):
        target = root / "_inbound" / "candidates" / f"{name}.md"
        fm, body = pages.read(target)
        pages.write(target, fm, body.replace("\n## New evidence", "\nOld text.\n\n## New evidence"))
    witness(root, "rec-2", "harbor", "Two.\n", NOW + H / 2)
    report = distill.run(root, now=NOW + H, rebuild=True)
    assert report.refreshed == 1 and report.subjects == ["harbor"]
    _, harbor = pages.read(root / "_inbound" / "candidates" / "harbor.md")
    _, ferry = pages.read(root / "_inbound" / "candidates" / "ferry.md")
    assert "Old text." not in harbor and len(evidence_lines(harbor)) == 2
    assert "Old text." in ferry   # no new evidence: the run would not write it


def archive_space(tmp_path, archives):
    extra = {"routing": {"archives": True}} if archives else {}
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor"}], **extra)
    witness(root, "rec-1", "harbor", "Own record.\n", NOW - 2 * D)
    path = witness(root, "rec-a", "harbor", "Read from the archive.\n", NOW - D)
    fm, body = pages.read(path)
    pages.write(path, dict(fm, source="archive"), body)
    write_page(root, "knowledge/projects/harbor/harbor.md", type="entity", title="Harbor",
               generated=pages.generated("cold-start", NOW - 3 * D),
               body="# Harbor\n\nDrafted from the archive.\n")
    return root


def test_archive_derived_evidence_is_ignored_with_archives_off(tmp_path):
    root = archive_space(tmp_path, archives=False)
    distill.run(root, now=NOW)
    fm, body = pages.read(root / "_inbound" / "candidates" / "harbor.md")
    assert fm["evidence"] == ["rec-1"]
    assert fm["target"] == "knowledge/projects/harbor/harbor.md"
    assert "Drafted from the archive" not in body
    assert body.startswith("# Harbor\n\n## New evidence\n")


def test_archive_derived_evidence_is_used_with_archives_on(tmp_path):
    root = archive_space(tmp_path, archives=True)
    distill.run(root, now=NOW)
    fm, body = pages.read(root / "_inbound" / "candidates" / "harbor.md")
    assert fm["evidence"] == ["rec-1", "rec-a"]
    assert "Drafted from the archive" in body


# ----- verify and checked ------------------------------------------------------------------------
CLEAN = (
    "# Harbor\n\n"
    "## What it is\n\n"
    "A booking tool for a ferry operator, built in two phases\n"
    "and handed over in the spring. [src: rec-1, rec-2]\n\n"
    "## People and roles\n\n"
    "- Ben Liu, project lead on the client side. [src: rec-1]\n"
    "- Chloe Martin, designer, joined for phase two\n"
    "  and left after the handover. [src: rec-2]\n\n"
    "## New evidence\n"
    "- 2026-10-10: Kickoff (witness, rec-1)\n"
    "- 2026-10-11: no marker here and [unknown] is fine (witness, rec-2)\n"
)


def candidate_file(root, body, **extra):
    fm = {"id": "p-0000c0de", "type": "entity", "title": "Harbor", "status": "draft",
          "generated": pages.generated("distill", NOW), "subject": "harbor", "kind": "projects",
          "target": "knowledge/projects/harbor/harbor.md", "evidence": ["rec-1", "rec-2"]}
    fm.update(extra)
    return pages.write(root / "_inbound" / "candidates" / "harbor.md", fm, body)


def test_verify_passes_a_clean_candidate(tmp_path, capsys):
    root = make_space(tmp_path / "ada")
    path = candidate_file(root, CLEAN, destination="harbor")
    assert distill.verify(root, path) == []
    assert distill.main([str(root), "verify", str(path)]) == 0
    assert capsys.readouterr().out.strip() == "clean"


def test_a_candidate_is_named_by_its_path_or_its_subject(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate_file(root, CLEAN, destination="harbor")
    assert distill.verify(root, "_inbound/candidates/harbor.md") == distill.verify(root, "harbor") == []


def test_verify_flags_a_line_without_a_source(tmp_path, capsys):
    root = make_space(tmp_path / "ada")
    body = CLEAN.replace("## People and roles\n\n",
                         "## People and roles\n\nThe team met weekly.\n\n")
    path = candidate_file(root, body)
    problems = distill.verify(root, path)
    assert len(problems) == 1 and "source" in problems[0] and "The team met weekly" in problems[0]
    assert distill.main([str(root), "verify", str(path)]) == 1
    assert capsys.readouterr().out.strip().splitlines() == problems


def test_verify_wants_the_marker_to_close_the_paragraph(tmp_path):
    root = make_space(tmp_path / "ada")
    body = CLEAN.replace("A booking tool for a ferry operator, built in two phases\n"
                         "and handed over in the spring. [src: rec-1, rec-2]\n",
                         "A booking tool for a ferry operator. [src: rec-1]\n"
                         "It was handed over in the spring.\n")
    problems = distill.verify(root, candidate_file(root, body))
    assert len(problems) == 1 and "source" in problems[0]


def test_verify_flags_a_source_not_in_evidence(tmp_path):
    root = make_space(tmp_path / "ada")
    body = CLEAN.replace("[src: rec-1]\n- Chloe", "[src: rec-1, rec-9]\n- Chloe")
    problems = distill.verify(root, candidate_file(root, body))
    assert len(problems) == 1 and "rec-9" in problems[0]


def test_verify_accepts_an_answered_question_of_the_subject(tmp_path):
    root = make_space(tmp_path / "ada")
    q = sign_questions.ask(root, "harbor", "p-00000101", "Has Harbor gone live?", "why", "rec", [],
                           by="routine:distill", now=NOW)
    path = candidate_file(root, f"- Harbor went live on 3 November. [src: {q.id}]\n", evidence=[])
    assert any(p.endswith(f"source {q.id} is not in evidence") for p in distill.verify(root, path))
    sign_questions.answer(root, q.id, by="Ada Okafor", text="yes, 3 November", now=NOW)
    assert distill.verify(root, path) == []


def test_verify_refuses_a_question_of_another_subject(tmp_path):
    root = make_space(tmp_path / "ada")
    q = sign_questions.ask(root, "beacon", "p-00000102", "Has Beacon gone live?", "why", "rec", [],
                           by="routine:distill", now=NOW)
    sign_questions.answer(root, q.id, by="Ada Okafor", text="yes, 3 November", now=NOW)
    path = candidate_file(root, f"- Harbor went live on 3 November. [src: {q.id}]\n", evidence=[])
    assert any("is not in evidence" in p for p in distill.verify(root, path))


def test_verify_checks_a_write_up_against_the_candidate(tmp_path, capsys):
    root = make_space(tmp_path / "ada")
    candidate_file(root, "- Kept as it is. [src: m-1]\n", evidence=["m-1"])
    file = tmp_path / "write-up.md"
    file.write_text("- Line one. [src: m-1]\n- Line two.\n", encoding="utf-8")
    assert distill.main([str(root), "verify", "harbor", "--body", str(file)]) == 1
    assert "no source: - Line two." in capsys.readouterr().out
    file.write_text("- Line one. [src: m-1]\n", encoding="utf-8")
    assert distill.main([str(root), "verify", "harbor", "--body", str(file)]) == 0


def test_verify_reads_a_write_up_saved_with_a_bom(tmp_path, capsys):   # final review, Minor 1
    root = make_space(tmp_path / "ada")
    candidate_file(root, "- Kept as it is. [src: m-1]\n", evidence=["m-1"])
    q = sign_questions.ask(root, "harbor", "p-0000c0de", "Has Harbor gone live?", "why", "rec", [],
                           by="routine:distill", now=NOW)
    sign_questions.answer(root, q.id, by="Ada Okafor", text="yes, 3 November", now=NOW)
    file = tmp_path / "write-up.md"
    file.write_text(f"# Harbor\n\n- Harbor went live on 3 November. [src: {q.id}]\n", encoding="utf-8-sig")
    assert distill.main([str(root), "verify", "harbor", "--body", str(file)]) == 0
    assert capsys.readouterr().out.strip() == "clean"


def test_verify_checks_facts_and_leaves_topics_to_the_page_check(tmp_path):
    """Never-leaves and left-out matter is the page check's judgement (a second agent reading
    the lists); a word in a line is no problem for verify."""
    root = make_space(tmp_path / "ada")
    body = CLEAN.replace("project lead on the client side.", "project lead, equity and day rate work.")
    assert distill.verify(root, candidate_file(root, body)) == []
    assert distill.verify(root, candidate_file(root, body, destination="harbor")) == []


def test_verify_flags_a_bracketed_placeholder(tmp_path):
    root = make_space(tmp_path / "ada")
    for mark in ("[unknown name]", "[?]", "(who?]", "[TODO: ask]", "[tbd]"):
        body = CLEAN.replace("project lead on the client side.", f"project lead {mark}.")
        problems = distill.verify(root, candidate_file(root, body))
        assert len(problems) == 1 and "placeholder" in problems[0], mark


def test_checked_stamps_a_clean_candidate(tmp_path):
    root = make_space(tmp_path / "ada")
    path = candidate_file(root, CLEAN)
    before_fm, before_body = pages.read(path)
    assert distill.main([str(root), "checked", str(path), "--by", "Ben Liu"]) == 0
    fm, body = pages.read(path)
    assert fm["checked"]["by"] == "Ben Liu"
    assert pages.parse_iso(fm["checked"]["at"]) and str(fm["checked"]["at"]).endswith("Z")
    assert {k: v for k, v in fm.items() if k != "checked"} == before_fm
    assert body == before_body


def test_checked_refuses_when_verify_finds_problems(tmp_path, capsys):
    root = make_space(tmp_path / "ada")
    path = candidate_file(root, CLEAN.replace("## New evidence", "No source here.\n\n## New evidence"))
    before = path.read_text(encoding="utf-8")
    assert distill.main([str(root), "checked", str(path), "--by", "Ben Liu"]) == 1
    assert path.read_text(encoding="utf-8") == before
    assert "source" in capsys.readouterr().out


# ----- prepare: which pages need their questions prepared --------------------------------------
def test_prepare_lists_subject_candidates_only(tmp_path, capsys):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor", "state": "active"}])
    candidate_file(root, CLEAN)
    old = pages.generated("test", NOW - 5 * D)   # none of these is evidence that arrived after a check
    # each of these has a `subject` and still is not a subject candidate: a lesson (by its name), a
    # decision, a review batch, a released record and a source
    for name, extra in (("lesson-retry-webhooks", {}), ("2026-10-05-use-a-queue", {"type": "decision"}),
                        ("review-2026-w41", {"review_batch": True}), ("m-released", {"released_from": "m-9"}),
                        ("m-source", {"type": "source"})):
        write_page(root, f"_inbound/candidates/{name}.md", "Text.\n", subject="harbor", generated=old, **extra)
    rows = distill.to_prepare(root)
    assert [row["candidate"] for row in rows] == ["harbor"]
    assert rows[0] == {"candidate": "harbor", "subject": "harbor", "why": "never checked"}
    sign_questions.mark_checked(root, "harbor", by="routine:distill", now=NOW)
    assert distill.to_prepare(root) == []
    assert distill.main([str(root), "prepare"]) == 0 and "nothing to prepare" in capsys.readouterr().out
    witness(root, "m-3", "harbor", "Harbor went live.\n", NOW + H)
    assert [(r["candidate"], r["why"]) for r in distill.to_prepare(root)] == [
        ("harbor", "new records since the last check")]
    assert distill.main([str(root), "prepare"]) == 0
    assert capsys.readouterr().out.splitlines() == ["harbor\tharbor\tnew records since the last check"]


def test_prepare_writes_nothing_and_takes_no_argument(tmp_path, capsys):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor", "state": "active"}])
    path = candidate_file(root, CLEAN)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert distill.main([str(root), "prepare"]) == 0
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    capsys.readouterr()
    # `prepare` takes no argument; `verify` and `checked` keep exactly one; `--body` stays verify's
    assert distill.main([str(root), "prepare", "harbor"]) == 2
    assert distill.main([str(root), "verify"]) == 2 and distill.main([str(root), "checked", "--by", "Ben Liu"]) == 2
    assert distill.main([str(root), "prepare", "--body", str(path)]) == 2


def candidate_text(root, name):
    return (root / "_inbound" / "candidates" / f"{name}.md").read_text(encoding="utf-8")


def evidence_ids(text):
    """The record ids a candidate's evidence lines name."""
    return re.findall(r"^- .*\((?:landed|journal|witness), ([^)]+)\)$", text, re.M)


def test_distill_counts_a_journal_entry_once(tmp_path):
    # entry A at 10:15 on the `now` day is journal evidence and, after the sweep, a witness page with the same record id
    ws = journal_ws(tmp_path)
    link.journal_records(ws)
    sweep.run(ws, now=NOW)
    distill.run(ws, now=NOW)
    record = f"journal-{NOW:%Y%m%d}T1015"
    assert evidence_ids(candidate_text(ws, "harbor-voice-agent")).count(record) == 1


# ----- pointers and lessons (codebase links, Task 7) --------------------------------------------
LESSON = "- lesson: retry webhooks with backoff\n- from: beacon-app\n"


def add_entry(ws, day, hhmm, body):
    """Append an entry at `hhmm` to ada's daily note for `day`."""
    note = ws / "journal" / f"{day:%Y}" / f"{day:%m}" / f"{day:%Y-%m-%d}.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    text = note.read_text(encoding="utf-8") if note.exists() else f"# {day:%Y-%m-%d}\n"
    note.write_text(f"{text}\n## {hhmm}Z: entry at {hhmm}\n\n{body}", encoding="utf-8")


def lesson_ws(tmp_path, entry_a=ENTRY_A + LESSON):
    """journal_ws with the brain (named `brain`) mounted."""
    ws = journal_ws(tmp_path, entry_a=entry_a)
    mount_brain(ws, make_space(tmp_path / "brain", profile="brain"))
    return ws


def lesson_candidates(root):
    return sorted((root / "_inbound" / "candidates").glob("lesson-*.md"))


def lesson_rows(root):
    path = root / distill.LESSONS_PROPOSED
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_evidence_line_lists_refs(tmp_path):
    ws = journal_ws(tmp_path)
    distill.run(ws, now=NOW)
    text = candidate_text(ws, "harbor-voice-agent")
    assert f"call-flow spec written (refs: northwind/harbor-voice:docs/spec.md@abc) (journal, journal-{NOW:%Y%m%d}T1015)" in text
    assert f"pilot scope (journal, journal-{NOW:%Y%m%d}T1100)" in text   # no ref: the title only
    mail = distill.Evidence(record="m-1", rel="x.md", at=NOW, arrived=NOW, title="Invoice",
                            body="Ref: INV-2026\n", subjects=["harbor"], landed=False)
    assert distill.evidence_line(mail) == f"- {NOW:%Y-%m-%d}: Invoice (witness, m-1)"   # no pointer form


def test_lesson_line_becomes_a_brain_candidate_without_the_client(tmp_path):
    ws = lesson_ws(tmp_path)
    distill.run(ws, now=NOW)
    fm, body = pages.read(ws / "_inbound/candidates/lesson-retry-webhooks-with-backoff.md")
    assert fm["destination"] == "brain" and fm["target"] == "knowledge/topics/lessons/retry-webhooks-with-backoff.md"
    assert (fm["type"], fm["status"], fm["kind"], fm["title"]) == ("concept", "draft", "topics", "retry webhooks with backoff")
    assert fm["evidence"] == [f"journal-{NOW:%Y%m%d}T1015"] and body.strip() == "retry webhooks with backoff"
    assert "beacon" not in (yaml.safe_dump(fm) + body).lower() and "subject" not in fm
    row, = lesson_rows(ws)
    assert row == {"slug": "retry-webhooks-with-backoff", "from": "beacon-app", "candidate": fm["id"], "at": pages.iso(NOW)}
    distill.run(ws, now=NOW + H)                                   # nothing new: no second row
    assert len(lesson_rows(ws)) == 1 and len(lesson_candidates(ws)) == 1


def test_same_lesson_from_two_subjects_merges(tmp_path):
    ws = lesson_ws(tmp_path)
    add_entry(ws, NOW, "13:00", "- lesson: Retry webhooks with backoff.\n- from: lighthouse-voice\n- subject: ada-notes\n")
    distill.run(ws, now=NOW)
    fm, _ = pages.read(lesson_candidates(ws)[0])
    assert len(fm["evidence"]) == 2 and len(lesson_candidates(ws)) == 1


def test_rejected_lesson_not_proposed_again_from_same_source(tmp_path):
    ws = lesson_ws(tmp_path)
    distill.run(ws, now=NOW)
    fm, _ = pages.read(lesson_candidates(ws)[0])
    sign.apply(ws, {fm["id"]: ("no", "")}, by="Ada Okafor", now=NOW + H)
    later = NOW + D
    add_entry(ws, later, "09:00", LESSON + "- subject: harbor-voice-agent\n")
    distill.run(ws, now=later + H)
    assert lesson_candidates(ws) == []
    add_entry(ws, later, "10:00", "- lesson: retry webhooks with backoff\n- from: lighthouse-voice\n"
                                  "- subject: harbor-voice-agent\n")
    distill.run(ws, now=later + 2 * H)
    path, = lesson_candidates(ws)
    assert pages.read(path)[0]["evidence"] == [f"journal-{later:%Y%m%d}T1000"]


def test_lesson_without_from(tmp_path):
    ws = lesson_ws(tmp_path, entry_a=ENTRY_A + "- lesson: pin the model version\n")
    add_entry(ws, NOW, "13:00", "- lesson: log every webhook\n- from: nobody-registered\n- subject: ada-notes\n")
    distill.run(ws, now=NOW)
    assert len(lesson_candidates(ws)) == 2
    assert sorted(str(r["from"]) for r in lesson_rows(ws)) == ["None", "nobody-registered"]


def test_a_lesson_is_proposed_once_per_source_whatever_the_answer(tmp_path):
    # signed, or discarded by its file name: a later write to the day's note proposes nothing again
    for verdict in ("yes", "no"):
        ws = lesson_ws(tmp_path / verdict)
        distill.run(ws, now=NOW)
        path, = lesson_candidates(ws)
        item = path.stem if verdict == "no" else pages.read(path)[0]["id"]
        sign.apply(ws, {item: (verdict, "")}, by="Ada Okafor", now=NOW + H)
        add_entry(ws, NOW, "15:00", "- a later note\n- subject: harbor-voice-agent\n")
        distill.run(ws, now=NOW + 2 * H)
        assert lesson_candidates(ws) == [], verdict


def test_a_from_after_the_lesson_is_its_source_and_never_its_text(tmp_path):
    ws = lesson_ws(tmp_path, entry_a=ENTRY_A + "- lesson: cap the transfer at thirty seconds; from: beacon-app\n")
    distill.run(ws, now=NOW)
    path, = lesson_candidates(ws)
    fm, body = pages.read(path)
    assert path.name == "lesson-cap-the-transfer-at-thirty-seconds.md"
    assert "beacon" not in (yaml.safe_dump(fm) + body).lower() and lesson_rows(ws)[0]["from"] == "beacon-app"
    assert link.FROM_LINE.findall("- from: beacon-app, lighthouse") == ["beacon-app"]


def test_two_entries_of_one_minute_are_two_records(tmp_path):
    # the spec entry at 10:15 and, in the same minute, the pull request opened for it
    ws = journal_ws(tmp_path)
    add_entry(ws, NOW, "10:15", "- The pull request for the spec.\n"
                                "- ref: https://github.com/northwind/harbor-voice/pull/7\n- subject: harbor-voice-agent\n")
    ids = [ev.record for ev in distill.journal_evidence(ws)]
    assert f"journal-{NOW:%Y%m%d}T1015" in ids and f"journal-{NOW:%Y%m%d}T1015-2" in ids
    assert link.journal_records(ws) == 2


def test_a_heading_with_a_rough_time_keeps_its_time(tmp_path):
    ws = make_space(tmp_path / "ada")
    add_entry(ws, NOW, "~09:30", "- a note\n- subject: harbor-voice-agent\n")
    (ev,) = distill.journal_evidence(ws)
    assert ev.at.strftime("%H:%M") == "09:30" and ev.title == "entry at ~09:30"


def test_a_journal_entry_sits_on_the_notes_own_day(tmp_path):
    # the note is named for the person's own date; its heading is UTC and can fall on the next or last UTC day
    offset = NOW.astimezone().utcoffset()
    if abs(offset) < dt.timedelta(hours=2):
        pytest.skip("this machine's clock is within two hours of UTC")
    hhmm = "01:30" if offset < dt.timedelta(0) else "22:30"
    ws = make_space(tmp_path / "ada")
    add_entry(ws, NOW, hhmm, "- a late note\n- subject: harbor-voice-agent\n")
    (ev,) = distill.journal_evidence(ws)
    assert ev.at.astimezone().date() == NOW.date() and ev.at.strftime("%H:%M") == hhmm
    # the record id comes from the note's date and heading, so another time zone never makes it a new record
    assert ev.record == f"journal-{NOW:%Y%m%d}T{hhmm.replace(':', '')}"
