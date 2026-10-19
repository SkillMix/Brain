import datetime as dt
import json

import pytest
import yaml

import pages
import sign
import spaceyaml
import validate
from conftest import NOW, make_space, write_page

BODY = "Harbor goes live on 3 November.\n"


def candidate(root, stem="harbor", dest="harbor", body=BODY, **fm):
    data = {"id": "p-00000101", "type": "entity", "title": "Harbor", "subject": "harbor",
            "kind": "projects", "target": "knowledge/projects/harbor/harbor.md", "evidence": ["m-1"]}
    if dest:
        data["destination"] = dest
    data.update(fm)
    return write_page(root, f"_inbound/candidates/{stem}.md", body=body, **data)


def queue_item(root, item_id, title, stage_after, **fm):
    data = {"class": "window", "destination": "harbor", "subject": "harbor",
            "stage_after": pages.iso(stage_after), "listed": False, "from_space": "ada",
            "kind": "mail", "evidence": ["m-1"]}
    data.update(fm)
    return write_page(root, f"_outbound/queue/{item_id}.md", id=item_id, type="source",
                      title=title, body=f"{title} body.\n", **data)


def check_item(root, item_id="p-00000401"):
    return write_page(root, f"_inbound/checks/{item_id}.md", id=item_id, type="review",
                      title="Harbor page says 15%", check="newer_evidence",
                      page="knowledge/projects/harbor/harbor.md", subject="harbor", evidence=["m-2"],
                      recommendation="20%", route=["Ada Okafor"], body="I think 20%\n")


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def clean(root):
    return [e for e in validate.validate(root) if not e.startswith("warning:")]


def test_apply_yes_signs_the_page_and_queues_a_signed_release(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root)
    report = sign.apply(root, {"p-00000101": ("yes", "")}, by="Ada Okafor", now=NOW)
    assert report.signed == ["p-00000101"]
    fm, body = pages.read(root / "knowledge/projects/harbor/harbor.md")
    assert body == BODY
    assert fm["status"] == "stable" and fm["signed_by"] == "Ada Okafor"
    assert fm["signed_at"] == "2026-10-12T09:00:00Z" and fm["signed_in"] == "ada"
    assert fm["fingerprint"] == pages.fingerprint(body)
    assert fm["id"] == "p-00000101" and fm["evidence"] == ["m-1"]
    assert "target" not in fm and "destination" not in fm
    assert not (root / "_inbound/candidates/harbor.md").exists()
    assert "- 2026-10-12T09:00:00Z yes p-00000101 by Ada Okafor" in (root / "log.md").read_text(encoding="utf-8")
    queued = list((root / "_outbound/queue").glob("*.md"))
    assert len(queued) == 1 and report.released_queued == [queued[0].stem]
    qfm, qbody = pages.read(queued[0])
    assert qfm["class"] == "at_once" and qfm["destination"] == "harbor"
    assert qfm["stage_after"] == "2026-10-12T09:00:00Z" and qfm["from_space"] == "ada"
    assert qfm["status"] == "stable" and qfm["signed_by"] == "Ada Okafor"
    assert qfm["signed_at"] == "2026-10-12T09:00:00Z" and qfm["evidence"] == ["m-1"]
    assert qfm["fingerprint"] == pages.fingerprint(qbody) == fm["fingerprint"]
    assert clean(root) == []


def test_apply_yes_without_destination_queues_nothing(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root, dest=None)
    report = sign.apply(root, {"p-00000101": "yes"}, by="Ada Okafor", now=NOW)
    assert report.signed == ["p-00000101"] and report.released_queued == []
    assert list((root / "_outbound/queue").glob("*.md")) == []


def test_apply_always_adds_the_standing_choice_once(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root)
    report = sign.apply(root, {"p-00000101": ("always", "")}, by="Ada Okafor", now=NOW)
    choice = {"destination": "harbor", "kind": "mail"}
    assert spaceyaml.load(root).standing_choices == [choice]
    assert report.standing_choices_added == [choice] and report.signed == ["p-00000101"]
    candidate(root, stem="harbor-2", id="p-00000102", target="knowledge/projects/harbor/two.md")
    report = sign.apply(root, {"p-00000102": ("always", "")}, by="Ada Okafor", now=NOW)
    assert spaceyaml.load(root).standing_choices == [choice]
    assert report.standing_choices_added == []
    assert not spaceyaml.load(root).errors()


def test_a_routine_actor_or_an_empty_name_raises_and_changes_nothing(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root)
    before = snapshot(root)
    for actor in ("routine:sweep", "Routine:distill", "", "   ", None):
        with pytest.raises(ValueError):
            sign.apply(root, {"p-00000101": ("yes", "")}, by=actor, now=NOW)
    assert snapshot(root) == before


def test_an_unknown_verb_raises_before_touching_anything(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root)
    before = snapshot(root)
    with pytest.raises(ValueError):
        sign.apply(root, {"p-00000101": ("maybe", "")}, by="Ada Okafor", now=NOW)
    assert snapshot(root) == before


def test_no_on_a_check_records_the_verdict_and_removes_the_item(tmp_path):
    root = make_space(tmp_path / "ada")
    path = check_item(root)
    report = sign.apply(root, {"p-00000401": ("no", "the page is right")}, by="Ben Liu", now=NOW)
    lines = (root / "_meta/state/check-verdicts.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1]) == {"check_id": "p-00000401", "verdict": "no", "by": "Ben Liu",
                                     "at": "2026-10-12T09:00:00Z"}
    assert not path.exists() and report.verdicts_recorded == 1
    assert "- 2026-10-12T09:00:00Z no p-00000401 by Ben Liu" in (root / "log.md").read_text(encoding="utf-8")


def test_edit_on_a_check_keeps_it_with_the_note(tmp_path):
    root = make_space(tmp_path / "ada")
    path = check_item(root)
    sign.apply(root, {"p-00000401": ("edit", "it is 18%, see the second mail")}, by="Ben Liu", now=NOW)
    fm, _ = pages.read(path)
    assert fm["note"] == "it is 18%, see the second mail"
    verdicts = [json.loads(l) for l in (root / "_meta/state/check-verdicts.jsonl").read_text().splitlines()]
    assert verdicts[-1]["verdict"] == "edit"


def test_edit_later_and_no_on_candidates(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root, stem="a", id="p-00000111", target="knowledge/projects/harbor/a.md", dest=None)
    candidate(root, stem="b", id="p-00000112", target="knowledge/projects/harbor/b.md", dest=None)
    candidate(root, stem="c", id="p-00000113", target="knowledge/projects/harbor/c.md", dest=None)
    report = sign.apply(root, {
        "p-00000111": ("edit", "Harbor goes live on 9 November.\n"),
        "p-00000112": ("later", ""),
        "p-00000113": ("no", "duplicate of the brief"),
    }, by="Ada Okafor", now=NOW)
    fm, body = pages.read(root / "knowledge/projects/harbor/a.md")
    assert body == "Harbor goes live on 9 November.\n" and fm["fingerprint"] == pages.fingerprint(body)
    assert fm["status"] == "stable"
    assert report.edited == ["p-00000111"] and "p-00000111" in report.signed
    bfm, _ = pages.read(root / "_inbound/candidates/b.md")
    assert bfm["deadline"] == pages.iso(NOW + dt.timedelta(hours=24))
    assert report.deferred == ["p-00000112"]
    assert not (root / "_inbound/candidates/c.md").exists()
    assert not (root / "knowledge/projects/harbor/c.md").exists()
    log = (root / "log.md").read_text(encoding="utf-8")
    assert "- 2026-10-12T09:00:00Z discarded p-00000113 by Ada Okafor: duplicate of the brief" in log
    assert report.discarded == ["p-00000113"]
    assert clean(root) == []


def test_later_uses_the_scaled_day(tmp_path):
    root = make_space(tmp_path / "ada", mode="fixture", time_scale=48)
    candidate(root, dest=None)
    sign.apply(root, {"p-00000101": ("later", "")}, by="Ada Okafor", now=NOW)
    fm, _ = pages.read(root / "_inbound/candidates/harbor.md")
    assert fm["deadline"] == pages.iso(NOW + dt.timedelta(minutes=30))


def test_leaving_item_no_pulls_it_and_yes_ships_tonight(tmp_path):
    root = make_space(tmp_path / "ada")
    queue_item(root, "p-00000201", "Go-live date", NOW + dt.timedelta(hours=10))
    queue_item(root, "p-00000202", "Kickoff notes", NOW + dt.timedelta(hours=10))
    report = sign.apply(root, {"p-00000201": ("no", "not yet"), "p-00000202": ("yes", "")},
                        by="Ada Okafor", now=NOW)
    assert not (root / "_outbound/queue/p-00000201.md").exists()
    held, _ = pages.read(root / "_outbound/held/p-00000201.md")
    assert held["reason"] == "pulled" and held["why"] == "not yet"
    shipped, _ = pages.read(root / "_outbound/queue/p-00000202.md")
    assert shipped["stage_after"] == "2026-10-12T09:00:00Z" and shipped["listed"] is True
    assert report.discarded == ["p-00000201"] and report.signed == ["p-00000202"]


def test_unknown_ids_are_skipped_not_fatal(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root, dest=None)
    report = sign.apply(root, {"p-ffffffff": "yes", "p-00000101": "yes"}, by="Ada Okafor", now=NOW)
    assert report.skipped == ["p-ffffffff"] and report.signed == ["p-00000101"]


def test_read_back_shows_the_exact_text_fingerprint_and_destination(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root)
    queue_item(root, "p-00000201", "Go-live date", NOW + dt.timedelta(hours=10))
    text = sign.read_back(root, ["p-00000101", "p-00000201", "p-ffffffff"])
    assert BODY in text and pages.fingerprint(BODY) in text
    assert "it will go to harbor" in text
    assert "Go-live date body." in text and pages.fingerprint("Go-live date body.\n") in text
    assert "p-ffffffff: not found" in text


PAGE = "# Harbor\n\n## Work\n- Harbor goes live on 3 November. [src: m-1]\n"
EVIDENCE = ("\n## New evidence\n"
            "- 2026-10-02: Harbor quote at EUR 48,000, paid in 30 days (witness, m-1)\n"
            "- 2026-10-03: Beacon asks for the same rate (witness, m-2)\n")


def test_signing_leaves_the_tool_kept_evidence_list_on_the_candidate(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root, body=PAGE + EVIDENCE, evidence=["m-1", "m-2"])
    back = sign.read_back(root, ["p-00000101"])
    assert pages.fingerprint(PAGE) in back
    sign.apply(root, {"p-00000101": "yes"}, by="Ada Okafor", now=NOW)
    fm, body = pages.read(root / "knowledge/projects/harbor/harbor.md")
    assert body == PAGE and "[src: m-1]" in body
    assert fm["fingerprint"] == pages.fingerprint(PAGE) and fm["evidence"] == ["m-1", "m-2"]
    qfm, qbody = pages.read(root / "_outbound/queue/p-00000101.md")
    assert qbody == PAGE and qfm["fingerprint"] == fm["fingerprint"]
    for text in (body, qbody, back):
        assert "EUR 48,000" not in text and "Beacon" not in text and "## New evidence" not in text
    assert clean(root) == []


def test_cli_apply_reads_a_verdicts_file_with_bare_yes_and_no(tmp_path, monkeypatch, capsys):
    root = make_space(tmp_path / "ada")
    candidate(root, dest=None)
    candidate(root, stem="beacon", id="p-00000102", target="knowledge/projects/beacon/beacon.md", dest=None)
    check_item(root)
    verdicts = tmp_path / "verdicts.yaml"
    # As a person types it: a bare yes or no (which YAML reads as a boolean) and a [verb, note] pair.
    verdicts.write_text("p-00000101: [edit, \"New body.\\n\"]\np-00000102: yes\np-00000401: no\n",
                        encoding="utf-8")
    assert yaml.safe_load(verdicts.read_text(encoding="utf-8"))["p-00000102"] is True
    monkeypatch.setenv("SPACE_NOW", "2026-10-12T09:00:00Z")
    assert sign.main([str(root), "apply", "--by", "routine:x", "--verdicts", str(verdicts)]) == 1
    assert (root / "_inbound/candidates/harbor.md").exists()
    assert sign.main([str(root), "apply", "--by", "Ada Okafor", "--verdicts", str(verdicts)]) == 0
    fm, body = pages.read(root / "knowledge/projects/harbor/harbor.md")
    assert body == "New body.\n" and fm["signed_at"] == "2026-10-12T09:00:00Z"
    assert pages.read(root / "knowledge/projects/beacon/beacon.md")[0]["status"] == "stable"
    out = json.loads(capsys.readouterr().out)
    assert out["signed"] == ["p-00000101", "p-00000102"] and out["verdicts_recorded"] == 1
    assert json.loads((root / "_meta/state/check-verdicts.jsonl").read_text().splitlines()[-1])["verdict"] == "no"
    assert sign.main([str(root), "read-back", "p-00000101"]) == 0
    assert "not found" in capsys.readouterr().out


def test_a_question_a_routine_wrote_takes_a_verdict_without_a_page(tmp_path):
    root = make_space(tmp_path / "ada")
    base = root / "_inbound" / "needs-you"
    base.mkdir(parents=True, exist_ok=True)
    (base / "owner.yaml").write_text(yaml.safe_dump({"items": [
        {"id": "name-it", "kind": "question", "text": "Name it?"},
        {"id": "later-one", "kind": "question", "text": "Later?"}]}), encoding="utf-8")
    report = sign.apply(root, {"name-it": "yes", "later-one": ["later", "another time"]},
                        by="Ada Okafor", now=NOW)
    assert report.verdicts_recorded == 1 and report.deferred == ["later-one"]
    log = (root / "log.md").read_text(encoding="utf-8")
    assert "yes name-it by Ada Okafor" in log
