import datetime as dt
import json

import pytest
import yaml

import needs_you
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


def test_signing_an_edit_of_a_signed_page_keeps_its_id_contested_and_release_fields(tmp_path):
    import ids
    root = make_space(tmp_path / "ada")
    page = root / "knowledge/projects/harbor/harbor.md"
    candidate(root, dest=None)
    sign.apply(root, {"p-00000101": "yes"}, by="Ada Okafor", now=NOW)
    released = {"released_by": "Bo Lind", "released_from": "bo", "released_at": "2026-10-11T08:00:00Z",
                "release_class": "window", "landed_at": "2026-10-11T09:00:00Z", "landed_from": "bo"}
    fm, body = pages.read(page)
    pages.write(page, dict(fm, contested=["conflict-1"], **released), body)
    write_page(root, "_inbound/conflicts/conflict-1.md", id="conflict-1", type="review", title="Conflict",
               conflict_note=True, page="p-00000101", body="Harbor goes live on 9 November.\n")
    # distill's next candidate for the page carries an id of its own
    candidate(root, id="p-00000102", dest=None)
    sign.apply(root, {"p-00000102": ("edit", "Harbor goes live on 9 November.\n")}, by="Ada Okafor", now=NOW)
    fm, body = pages.read(page)
    assert body == "Harbor goes live on 9 November.\n" and fm["status"] == "stable"
    assert fm["id"] == "p-00000101"   # a page's id is permanent: the open conflict note still finds it
    assert fm["contested"] == ["conflict-1"]
    assert {k: fm.get(k) for k in released} == released
    assert ids.scan(root)["p-00000101"] == "knowledge/projects/harbor/harbor.md"


def flagged_page(root, **fm):
    data = {"id": "p-00000101", "type": "entity", "title": "Harbor", "status": "stable",
            "subject": "harbor", "kind": "projects", "evidence": ["m-0", "m-1", "m-2"],
            "set_aside": [{"id": "m-9", "why": "about another client"}],
            "high_stakes": "a commitment the company agrees to"}
    data.update(fm)
    return write_page(root, "knowledge/projects/harbor/harbor.md", body=BODY, **data)


def test_resigning_a_short_draft_keeps_the_pages_records_set_aside_and_flag(tmp_path):
    # a draft that names only its new records and no flag, as an older distill wrote it
    root = make_space(tmp_path / "ada")
    page = flagged_page(root)
    candidate(root, id="p-00000102", evidence=["m-1", "m-3"], body="Harbor goes live on 9 November.\n")
    report = sign.apply(root, {"p-00000102": ("yes", "")}, by="Ada Okafor", now=NOW)
    assert report.signed == ["p-00000102"]
    fm, body = pages.read(page)
    assert body == "Harbor goes live on 9 November.\n" and fm["id"] == "p-00000101"
    assert fm["evidence"] == ["m-1", "m-3", "m-0", "m-2"]   # the draft's, then what only the page had
    assert fm["set_aside"] == [{"id": "m-9", "why": "about another client"}]
    assert fm["high_stakes"] == "a commitment the company agrees to"
    qfm, _ = pages.read(root / "_outbound/queue/p-00000101.md")
    assert qfm["class"] == "window" and qfm["high_stakes"] == "a commitment the company agrees to"
    assert pages.parse_iso(qfm["stage_after"]) == NOW + spaceyaml.load(root).window()
    assert qfm["evidence"] == fm["evidence"]
    assert clean(root) == []


def test_a_draft_s_own_flag_and_set_aside_decide_on_resign(tmp_path):
    root = make_space(tmp_path / "ada")
    page = flagged_page(root, set_aside=[{"id": "m-8", "why": "a duplicate"},
                                         {"id": "m-9", "why": "about another client"}])
    aside = [{"id": "m-2", "why": "about the other Harbor"}]
    candidate(root, id="p-00000102", dest=None, evidence=["m-1", "m-8"], set_aside=aside,
              high_stakes="a price the company agrees to")
    sign.apply(root, {"p-00000102": "yes"}, by="Ada Okafor", now=NOW)
    fm, _ = pages.read(page)
    # set aside by the draft: out of the page's evidence; taken up again by the draft: no longer aside
    assert fm["evidence"] == ["m-1", "m-8", "m-0"]
    assert fm["set_aside"] == aside + [{"id": "m-9", "why": "about another client"}]
    assert fm["high_stakes"] == "a price the company agrees to"


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


def pins_in(text):
    """{id: {"fingerprint", "base"}} from a read-back of several items."""
    out, current = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            current = out.setdefault(line[3:].split(":", 1)[0], {})
        elif current is not None and line.split(": ", 1)[0] in ("fingerprint", "base"):
            key, value = line.split(": ", 1)
            current.setdefault(key, value)
    return out


def test_cli_apply_requires_the_pin_and_still_takes_bare_verbs_elsewhere(tmp_path, monkeypatch, capsys):
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
    out = json.loads(capsys.readouterr().out)
    # the two pages carry no fingerprint: refused and still waiting; the check's no is recorded
    assert out["signed"] == [] and out["refused"] == [
        {"id": "p-00000101", "why": "no fingerprint: read it back first"},
        {"id": "p-00000102", "why": "no fingerprint: read it back first"}]
    assert out["verdicts_recorded"] == 1
    assert json.loads((root / "_meta/state/check-verdicts.jsonl").read_text().splitlines()[-1])["verdict"] == "no"
    assert (root / "_inbound/candidates/harbor.md").exists() and (root / "_inbound/candidates/beacon.md").exists()
    assert not (root / "knowledge/projects").exists() or not list((root / "knowledge/projects").rglob("*.md"))
    # read back (the edit from its write-up), then submit the pinned verdicts
    (root / "_scratch/sign").mkdir(parents=True)
    (root / "_scratch/sign/p-00000101.md").write_bytes(b"New body.\r\n")
    assert sign.main([str(root), "read-back", "p-00000101", "p-00000102",
                      "--body", "p-00000101=_scratch/sign/p-00000101.md"]) == 0
    back = capsys.readouterr().out
    assert "(your edit)" in back and pages.fingerprint("New body.\n") in back
    pins = pins_in(back)
    verdicts.write_text(yaml.safe_dump({
        "p-00000101": {"verb": "edit", "body_file": "_scratch/sign/p-00000101.md", **pins["p-00000101"]},
        "p-00000102": {"verb": "yes", **pins["p-00000102"]}}), encoding="utf-8")
    assert sign.main([str(root), "apply", "--by", "Ada Okafor", "--verdicts", str(verdicts)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["signed"] == ["p-00000101", "p-00000102"] and out["refused"] == []
    fm, body = pages.read(root / "knowledge/projects/harbor/harbor.md")
    assert body == "New body.\n" and fm["signed_at"] == "2026-10-12T09:00:00Z"
    assert pages.read(root / "knowledge/projects/beacon/beacon.md")[0]["status"] == "stable"
    assert sign.main([str(root), "read-back", "p-00000101"]) == 0
    assert "not found" in capsys.readouterr().out


def pin(root, item_id, body=None):
    text = sign.read_back(root, [item_id], bodies={item_id: body} if body else None)
    get = lambda key: next(l.split(": ", 1)[1] for l in text.splitlines() if l.startswith(key + ": "))
    return {"fingerprint": get("fingerprint"), "base": get("base")}


def test_read_back_shows_the_base_and_an_edit_s_own_fingerprint(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root)
    text = sign.read_back(root, ["p-00000101"], bodies={"p-00000101": "Harbor goes live on 9 November.\n"})
    assert "(your edit)" in text and pages.fingerprint("Harbor goes live on 9 November.\n") in text
    fm, body = pages.read(root / "_inbound/candidates/harbor.md")
    assert f"base: {sign.base_of(fm, body)}" in text


def test_a_pinned_yes_signs(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root)
    report = sign.apply(root, {"p-00000101": {"verb": "yes", **pin(root, "p-00000101")}},
                        by="Ada Okafor", now=NOW, require_pin=True)
    assert report.signed == ["p-00000101"] and report.refused == []


def test_a_draft_refreshed_after_the_read_back_is_refused_and_stays(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root)
    p = pin(root, "p-00000101")
    candidate(root, high_stakes="claims about a named person")      # distill refreshed it in between
    report = sign.apply(root, {"p-00000101": {"verb": "yes", **p}}, by="Ada Okafor", now=NOW, require_pin=True)
    assert report.refused == [{"id": "p-00000101", "why": "the draft changed since the read-back: read it back again"}]
    assert (root / "_inbound/candidates/harbor.md").exists()
    log = (root / "log.md").read_text(encoding="utf-8")
    assert "- 2026-10-12T09:00:00Z refused p-00000101 by Ada Okafor: the draft changed" in log
    assert not needs_you.VERDICT_LINE.match(log.strip().splitlines()[-1])


def test_an_edit_signs_exactly_its_body_file(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root)
    (root / "_scratch/sign").mkdir(parents=True)
    (root / "_scratch/sign/p-00000101.md").write_bytes(b"Harbor goes live on 9 November.\r\n")   # Review Focus 1
    p = pin(root, "p-00000101", body="Harbor goes live on 9 November.\n")
    sign.apply(root, {"p-00000101": {"verb": "edit", "body_file": "_scratch/sign/p-00000101.md", **p}},
               by="Ada Okafor", now=NOW, require_pin=True)
    fm, body = pages.read(root / "knowledge/projects/harbor/harbor.md")
    assert body == "Harbor goes live on 9 November.\n" and fm["fingerprint"] == p["fingerprint"]


def test_a_write_up_changed_after_the_read_back_is_refused(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root)
    (root / "_scratch/sign").mkdir(parents=True)
    (root / "_scratch/sign/p-00000101.md").write_text("Harbor goes live on 10 November.\n", encoding="utf-8")
    p = pin(root, "p-00000101", body="Harbor goes live on 9 November.\n")
    report = sign.apply(root, {"p-00000101": {"verb": "edit", "body_file": "_scratch/sign/p-00000101.md", **p}},
                        by="Ada Okafor", now=NOW)   # a pin given is checked even when not required
    assert report.refused == [{"id": "p-00000101", "why": "the text changed since the read-back"}]
    assert report.signed == [] and (root / "_inbound/candidates/harbor.md").exists()


def test_a_signed_leaving_item_is_pinned_too(tmp_path):
    root = make_space(tmp_path / "ada")
    queue_item(root, "p-00000203", "Go-live date", NOW + dt.timedelta(hours=10),   # ruling R2
               fingerprint=pages.fingerprint("Go-live date body.\n"), signed_by="Ada Okafor")
    assert sign.apply(root, {"p-00000203": "yes"}, by="Ada Okafor", now=NOW, require_pin=True).refused == [
        {"id": "p-00000203", "why": "no fingerprint: read it back first"}]
    p = pin(root, "p-00000203")
    report = sign.apply(root, {"p-00000203": {"verb": "yes", **p}}, by="Ada Okafor", now=NOW, require_pin=True)
    assert report.signed == ["p-00000203"] and report.refused == []


def test_a_bare_yes_on_a_page_is_refused_when_the_pin_is_required(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root)
    queue_item(root, "p-00000202", "Kickoff notes", NOW + dt.timedelta(hours=10))   # unsigned: outside the pin
    report = sign.apply(root, {"p-00000101": "yes", "p-00000202": "yes"}, by="Ada Okafor", now=NOW, require_pin=True)
    assert report.refused == [{"id": "p-00000101", "why": "no fingerprint: read it back first"}]
    assert report.signed == ["p-00000202"]


def test_yes_read_as_true_inside_a_mapping_still_means_yes(tmp_path):   # Review Focus 2
    root = make_space(tmp_path / "ada")
    candidate(root)
    p = pin(root, "p-00000101")
    data = yaml.safe_load(f"p-00000101: {{verb: yes, fingerprint: {p['fingerprint']}, base: {p['base']}}}\n")
    assert data["p-00000101"]["verb"] is True
    assert sign.apply(root, data, by="Ada Okafor", now=NOW, require_pin=True).signed == ["p-00000101"]


def test_a_missing_or_outside_body_file_stops_the_run_before_any_write(tmp_path):   # Review Focus 5
    root = make_space(tmp_path / "ada")
    candidate(root)
    check_item(root)
    (tmp_path / "outside.md").write_text("Harbor goes live on 9 November.\n", encoding="utf-8")   # ruling R4
    before = snapshot(root)
    for where in ("_scratch/sign/none.md", "../outside.md"):
        with pytest.raises(ValueError):   # the check's no comes first and would write: the whole run stops
            sign.apply(root, {"p-00000401": "no",
                              "p-00000101": {"verb": "edit", "body_file": where, "fingerprint": "x", "base": "y"}},
                       by="Ada Okafor", now=NOW, require_pin=True)
    assert snapshot(root) == before


def test_a_body_file_with_a_note_or_under_another_verb_stops_the_run(tmp_path):   # ruling R11
    root = make_space(tmp_path / "ada")
    candidate(root)
    (root / "_scratch/sign").mkdir(parents=True)
    (root / "_scratch/sign/p-00000101.md").write_text("Harbor goes live on 9 November.\n", encoding="utf-8")
    check_item(root)
    before = snapshot(root)
    for verdict in ({"verb": "edit", "body_file": "_scratch/sign/p-00000101.md", "note": "Go-live moves."},
                    {"verb": "yes", "body_file": "_scratch/sign/p-00000101.md"}):
        with pytest.raises(ValueError):
            sign.apply(root, {"p-00000401": "no", "p-00000101": verdict}, by="Ada Okafor", now=NOW)
    assert snapshot(root) == before


def test_cli_read_back_body_stays_inside_the_space(tmp_path, capsys):
    root = make_space(tmp_path / "ada")
    candidate(root)
    (tmp_path / "outside.md").write_text("Harbor goes live on 9 November.\n", encoding="utf-8")
    assert sign.main([str(root), "read-back", "p-00000101", "--body", "p-00000101=../outside.md"]) == 1
    assert "inside the space" in capsys.readouterr().err


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


def test_an_edited_lesson_lands_and_travels_under_its_new_text(tmp_path):
    # a lesson candidate whose line named a client; the signer edits the name out
    root = make_space(tmp_path / "ada")
    old = "knowledge/topics/lessons/beacon-s-outage-taught-us-to-retry.md"
    candidate(root, stem="lesson-beacon-s-outage-taught-us-to-retry", dest="brain",
              body="Beacon's outage taught us to retry.\n", type="concept", kind="topics", subject=None,
              title="Beacon's outage taught us to retry", target=old)
    sign.apply(root, {"p-00000101": ("edit", "Retry webhooks with backoff.")}, by="Ada Okafor", now=NOW)
    page = root / "knowledge/topics/lessons/retry-webhooks-with-backoff.md"
    assert page.exists() and not (root / old).exists()
    (queued,) = (root / "_outbound/queue").glob("*.md")
    qfm, qbody = pages.read(queued)
    assert qfm["title"] == "Retry webhooks with backoff." and "beacon" not in (yaml.safe_dump(qfm) + qbody).lower()
    assert qfm["target"] == "knowledge/topics/lessons/retry-webhooks-with-backoff.md"   # one page per lesson in the brain


@pytest.mark.parametrize("require_pin", [True, False])
@pytest.mark.parametrize("missing", ["fingerprint", "base"])
def test_a_pin_missing_either_half_is_a_missing_pin(tmp_path, missing, require_pin):
    root = make_space(tmp_path / "ada")
    candidate(root)
    half = {k: v for k, v in pin(root, "p-00000101").items() if k != missing}
    report = sign.apply(root, {"p-00000101": {"verb": "yes", **half}}, by="Ada Okafor", now=NOW,
                        require_pin=require_pin)
    assert report.refused == [{"id": "p-00000101", "why": "no fingerprint: read it back first"}]
    assert report.signed == [] and (root / "_inbound/candidates/harbor.md").exists()


def review_batch(root):
    """A weekly review batch: line 1 a waiting draft, line 2 a signed page past its window."""
    write_page(root, "knowledge/projects/harbor/status.md", body="# Status\n\nAll green.\n", id="p-00000301",
               type="snapshot", title="Status", status="stable", signed_by="Ada Okafor")
    return write_page(root, "_inbound/candidates/review-2026-10-12.md", id="p-0000e001", type="review",
                      title="Weekly review 2026-10-12: 2 pages", review_batch=True, body="# Weekly review\n",
                      lines=[{"n": 1, "page": "_inbound/candidates/harbor.md", "id": "p-00000101",
                              "title": "Harbor", "signed": False},
                             {"n": 2, "page": "knowledge/projects/harbor/status.md", "id": "p-00000301",
                              "title": "Status", "signed": True}])


def test_a_review_line_that_would_sign_a_draft_is_refused_when_the_pin_is_required(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root)
    batch = review_batch(root)
    report = sign.apply(root, {"review-2026-10-12#1": "yes", "review-2026-10-12#2": "yes"},
                        by="Ada Okafor", now=NOW, require_pin=True)
    assert report.refused == [{"id": "review-2026-10-12#1", "why": "no fingerprint: read it back first"}]
    assert report.signed == ["review-2026-10-12#2"] and report.skipped == []
    assert (root / "_inbound/candidates/harbor.md").exists()
    assert not (root / "knowledge/projects/harbor/harbor.md").exists()
    assert [l.get("answered") for l in pages.read(batch)[0]["lines"]] == [None, "yes"]   # line 1 stays open
    log = (root / "log.md").read_text(encoding="utf-8")
    assert ("- 2026-10-12T09:00:00Z refused review-2026-10-12#1 by Ada Okafor: "
            "no fingerprint: read it back first") in log
    assert "yes review-2026-10-12#1 by" not in log and "yes review-2026-10-12#2 by Ada Okafor" in log
    # no and later on such a line need no pin, as before
    report = sign.apply(root, {"review-2026-10-12#1": "later"}, by="Ada Okafor", now=NOW, require_pin=True)
    assert report.deferred == ["review-2026-10-12#1"] and report.refused == []
    report = sign.apply(root, {"review-2026-10-12#1": ["no", "out of date"]}, by="Ada Okafor", now=NOW,
                        require_pin=True)
    assert report.discarded == ["p-00000101"] and report.refused == []
    assert not (root / "_inbound/candidates/harbor.md").exists() and not batch.exists()


def test_a_whole_batch_yes_leaves_its_drafts_to_their_own_read_back(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root)
    batch = review_batch(root)
    report = sign.apply(root, {"review-2026-10-12": "yes"}, by="Ada Okafor", now=NOW, require_pin=True)
    assert report.refused == [{"id": "review-2026-10-12#1", "why": "no fingerprint: read it back first"}]
    assert report.signed == ["review-2026-10-12#2"]
    assert pages.read(root / "knowledge/projects/harbor/status.md")[0]["verified_by"] == "Ada Okafor"
    assert (root / "_inbound/candidates/harbor.md").exists()
    assert [l.get("answered") for l in pages.read(batch)[0]["lines"]] == [None, "yes"]
    # the draft is signed by its own id, after its read-back
    report = sign.apply(root, {"p-00000101": {"verb": "yes", **pin(root, "p-00000101")}},
                        by="Ada Okafor", now=NOW, require_pin=True)
    assert report.signed == ["p-00000101"]
    assert pages.read(root / "knowledge/projects/harbor/harbor.md")[0]["status"] == "stable"


def test_a_review_line_still_signs_a_draft_when_the_pin_is_not_required(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root)
    review_batch(root)
    report = sign.apply(root, {"review-2026-10-12#1": "yes"}, by="Ada Okafor", now=NOW)
    assert report.refused == [] and report.signed == ["p-00000101"]
    assert pages.read(root / "knowledge/projects/harbor/harbor.md")[0]["status"] == "stable"


# ----- the final review: read-backs of items that are not one page ---------------------------------
def test_a_first_release_line_reads_back_beside_a_page(tmp_path):   # final review, Important 3
    root = make_space(tmp_path / "ada")
    candidate(root)
    for item_id, title in (("p-00000201", "Go-live date"), ("p-00000202", "Kickoff notes")):
        queue_item(root, item_id, title, NOW + dt.timedelta(hours=10), first_release="harbor")
    text = sign.read_back(root, ["p-00000101", "first-harbor"])
    page, first = text.split("## first-harbor", 1)
    fm, body = pages.read(root / "_inbound/candidates/harbor.md")
    assert BODY in page and f"base: {sign.base_of(fm, body)}" in page
    assert "p-00000201: Go-live date" in first and "p-00000202: Kickoff notes" in first
    assert "fingerprint:" not in first and "base:" not in first   # a bare verb answers it (R10)
    assert pins_in(text) == {"p-00000101": pin(root, "p-00000101"), "first-harbor": {}}
    report = sign.apply(root, {"first-harbor": "yes"}, by="Ada Okafor", now=NOW, require_pin=True)
    assert report.refused == [] and sorted(report.signed) == ["p-00000201", "p-00000202"]


def test_a_read_back_says_when_a_write_up_is_not_used(tmp_path):   # final review, Minor 9
    root = make_space(tmp_path / "ada")
    check_item(root)
    text = sign.read_back(root, ["p-00000401"], bodies={"p-00000401": "Harbor is at 20%.\n"})
    assert "the write-up is not used for this item" in text and "(your edit)" not in text
    assert "Harbor is at 20%." not in text and "I think 20%" in text
