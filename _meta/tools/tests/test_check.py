import datetime as dt
import json

import pages
import check
from conftest import NOW, make_space, write_page
from test_distill import witness

H = dt.timedelta(hours=1)
D = dt.timedelta(days=1)


def signed(root, rel, body, by="Ben Liu", at=None, **fm):
    at = at or (NOW - 2 * D)
    fm.setdefault("type", "entity")
    return write_page(root, rel, body=body, status="stable", signed_by=by, signed_at=pages.iso(at),
                      signed_in="ada", fingerprint=pages.fingerprint(body), **fm)


def checks(root):
    return sorted(p for p in (root / "_inbound" / "checks").glob("*.md"))


def verdicts(root, rows):
    path = root / "_meta" / "state" / "check-verdicts.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_newer_evidence_hit_routes_to_signer_first(tmp_path):
    root = make_space(tmp_path / "ada", owner="Ada Okafor", subjects=[
        {"id": "sales", "kind": "domains", "owner": "Chloe Martin"},
        {"id": "harbor", "kind": "projects", "title": "Harbor", "part_of": "sales"}])
    signed(root, "knowledge/projects/harbor/terms.md", "# Terms\n\nStandard discount: 15%\n",
           by="Ben Liu", at=NOW - 3 * D)
    witness(root, "rec-1", "harbor", "They now get discount: 20% as agreed.\n", NOW - D)
    report = check.run(root, now=NOW)
    hits = [h for h in report.hits if h.check == "newer_evidence"]
    assert len(hits) == 1
    hit = hits[0]
    assert hit.page == "knowledge/projects/harbor/terms.md"
    assert hit.route[0] == "Ben Liu"
    assert hit.route == ["Ben Liu", "Chloe Martin", "Ada Okafor"]
    files = checks(root)
    assert len(files) == 1 and files[0].name == hit.hit_id + ".md"
    assert hit.hit_id.startswith("chk-")
    fm, body = pages.read(files[0])
    assert fm["type"] == "review" and fm["status"] == "draft"
    assert fm["check"] == "newer_evidence"
    assert fm["check_id"] == hit.hit_id
    assert fm["page"] == "knowledge/projects/harbor/terms.md"
    assert fm["subject"] == "harbor"
    assert fm["evidence"] and "rec-1" in fm["evidence"][0]
    assert fm["route"] == hit.route
    assert "15%" in fm["recommendation"] and "20%" in fm["recommendation"]
    assert "I think 20%" in body
    runs = (root / "_meta" / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    line = json.loads(runs[-1])
    assert line["routine"] == "check" and line["cost_tokens"] > 0


def test_past_window_on_the_window_pass_with_a_scaled_clock(tmp_path):
    root = make_space(tmp_path / "ada", mode="fixture", time_scale=24, subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor", "state": "active"}])
    # 8 scaled days = 8 real hours under time_scale 24; the snapshot window is 7 scaled days.
    signed(root, "knowledge/projects/harbor/status.md", "# Status\n\nAll green.\n",
           type="snapshot", at=NOW - 8 * H)
    report = check.run(root, now=NOW)
    # window findings bundle into the weekly review batch, never a separate item
    assert [h for h in report.hits if h.check == "past_window"] == []
    assert "past_window" in report.checks_run
    assert report.batch_lines == 1
    batch, = sorted((root / "_inbound" / "candidates").glob("review-*.md"))
    fm, _ = pages.read(batch)
    assert fm["lines"][0]["page"] == "knowledge/projects/harbor/status.md"
    # a second run one scaled hour later does not repeat the window pass or the batch
    again = check.run(root, now=NOW + dt.timedelta(minutes=10))
    assert again.hits == [] and again.batch_lines == 0
    assert checks(root) == []


def test_fresh_snapshot_is_not_past_window(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects"}])
    signed(root, "knowledge/projects/harbor/status.md", "# Status\n", type="snapshot", at=NOW - 2 * D)
    report = check.run(root, now=NOW)
    assert [h for h in report.hits if h.check == "past_window"] == []


def test_dormant_subject_past_window_is_quiet(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "harbor", "kind": "projects", "state": "dormant"}])
    signed(root, "knowledge/projects/harbor/status.md", "# Status\n", type="snapshot", at=NOW - 30 * D)
    report = check.run(root, now=NOW)
    assert report.hits == []


def test_disagree_between_two_signed_pages(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects"}])
    signed(root, "knowledge/projects/harbor/plan.md", "# Plan\n\nbudget: 40k\n", at=NOW - 10 * D)
    signed(root, "knowledge/projects/harbor/update.md", "# Update\n\nbudget: 45k\n", at=NOW - 2 * D)
    witness(root, "rec-1", "harbor", "Nothing numeric here.\n", NOW - D)
    report = check.run(root, now=NOW)
    hits = [h for h in report.hits if h.check == "disagree"]
    assert len(hits) == 1
    assert hits[0].label == "budget"
    assert hits[0].page == "knowledge/projects/harbor/plan.md"
    assert "knowledge/projects/harbor/update.md" in hits[0].evidence
    assert "I think 45" in hits[0].recommendation


def test_rerun_does_not_duplicate_open_items(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects"}])
    signed(root, "knowledge/projects/harbor/terms.md", "discount: 15%\n", at=NOW - 3 * D)
    witness(root, "rec-1", "harbor", "discount: 20%\n", NOW - D)
    first = check.run(root, now=NOW)
    assert len(first.hits) == 1
    # the same evidence arrives again (a re-sweep rewrote the witness page)
    witness(root, "rec-1", "harbor", "discount: 20%\n", NOW - D, generated_at=NOW + H / 2)
    second = check.run(root, now=NOW + H)
    assert second.hits == []
    assert len(checks(root)) == 1
    # an item with a verdict is not re-opened either
    checks(root)[0].unlink()
    verdicts(root, [{"check_id": first.hits[0].hit_id, "verdict": "no", "by": "Ben Liu",
                     "at": pages.iso(NOW + H)}])
    witness(root, "rec-1", "harbor", "discount: 20%\n", NOW - D, generated_at=NOW + 2 * H)
    third = check.run(root, now=NOW + 3 * H)
    assert third.hits == []
    assert checks(root) == []


def test_verdict_recorded_under_the_page_id_also_closes_the_item(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects"}])
    signed(root, "knowledge/projects/harbor/terms.md", "discount: 15%\n", at=NOW - 3 * D)
    witness(root, "rec-1", "harbor", "discount: 20%\n", NOW - D)
    first = check.run(root, now=NOW)
    fm, _ = pages.read(checks(root)[0])
    assert fm["id"] == "p-" + first.hits[0].hit_id[4:]
    checks(root)[0].unlink()
    verdicts(root, [{"check_id": fm["id"], "verdict": "yes", "by": "Ben Liu", "at": pages.iso(NOW + H)}])
    witness(root, "rec-1", "harbor", "discount: 20%\n", NOW - D, generated_at=NOW + H)
    assert check.run(root, now=NOW + 2 * H).hits == []
    assert checks(root) == []


def test_person_claim_against_the_registry(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "ben-liu", "kind": "people", "title": "Ben Liu", "role": "delivery lead"}])
    signed(root, "knowledge/people/ben-liu/profile.md", "# Ben Liu\n\nRole: sales lead\n", by="Ada Okafor")
    witness(root, "rec-1", "ben-liu", "Ben joined the call.\n", NOW - D)
    report = check.run(root, now=NOW)
    hits = [h for h in report.hits if h.check == "person_claim"]
    assert len(hits) == 1
    assert "delivery lead" in hits[0].recommendation


def test_named_number_across_subjects_routes_to_space_owner(tmp_path):
    root = make_space(tmp_path / "ada", owner="Ada Okafor", subjects=[
        {"id": "harbor", "kind": "projects", "owner": "Ben Liu"},
        {"id": "beacon", "kind": "projects", "owner": "Chloe Martin"}])
    signed(root, "knowledge/projects/harbor/plan.md", "headcount: 4\n", by="Ben Liu")
    signed(root, "knowledge/projects/beacon/plan.md", "headcount: 6\n", by="Chloe Martin")
    witness(root, "rec-1", "harbor", "Note.\n", NOW - D)
    witness(root, "rec-2", "beacon", "Note.\n", NOW - D)
    report = check.run(root, now=NOW)
    hits = [h for h in report.hits if h.check == "named_number"]
    assert len(hits) == 1
    assert hits[0].route == ["Ada Okafor"]


def test_a_label_inside_a_prose_sentence_is_not_a_page_label(tmp_path):
    root = make_space(tmp_path / "ada", owner="Ada Okafor", subjects=[
        {"id": "harbor", "kind": "projects", "owner": "Ben Liu"},
        {"id": "beacon", "kind": "projects", "owner": "Chloe Martin"}])
    signed(root, "knowledge/projects/harbor/plan.md",
           "# Plan\n\nThe pilot ran well and the next step was a follow up: 3 weeks later. [src: rec-1]\n")
    signed(root, "knowledge/projects/beacon/plan.md",
           "# Plan\n\n- After the kick-off came a follow up: 5 weeks on. [src: rec-2]\n")
    witness(root, "rec-1", "harbor", "Note.\n", NOW - D)
    witness(root, "rec-2", "beacon", "Note.\n", NOW - D)
    report = check.run(root, now=NOW)
    assert report.hits == [], "a label mid-sentence on a signed page is prose, not a value to compare"
    assert report.labelled_pages == 0
    runs = (root / "_meta" / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(runs[-1])["notes"]["labelled_pages"] == 0


def test_labelled_pages_counts_the_pages_the_value_checks_can_read(tmp_path):
    root = make_space(tmp_path / "ada", owner="Ada Okafor", subjects=[
        {"id": "harbor", "kind": "projects", "owner": "Ben Liu"}])
    signed(root, "knowledge/projects/harbor/plan.md",
           "# Plan\n\n- The go-live moves to 3 November (go-live date: 2026-11-03). [src: rec-1]\n")
    witness(root, "rec-1", "harbor", "Note.\n", NOW - D)
    assert check.run(root, now=NOW).labelled_pages == 1, "a label that opens a parenthesis is a label"


def test_hit_rate_and_self_narrowing(tmp_path):
    root = make_space(tmp_path / "ada", mode="fixture", time_scale=24, subjects=[
        {"id": "harbor", "kind": "projects", "state": "active"}])
    assert check.hit_rate(root, 30, now=NOW) is None
    rows = [{"check_id": f"chk-{i:08x}", "verdict": "no" if i < 9 else "yes", "by": "Ada Okafor",
             "at": pages.iso(NOW - dt.timedelta(minutes=i + 1))} for i in range(10)]
    verdicts(root, rows)
    assert abs(check.hit_rate(root, 30, now=NOW) - 0.1) < 1e-9
    signed(root, "knowledge/projects/harbor/status.md", "# Status\n", type="snapshot", at=NOW - 8 * H)
    report = check.run(root, now=NOW)
    assert "past_window" in report.disabled and "named_number" in report.disabled
    assert [h for h in report.hits if h.check == "past_window"] == []
    runs = (root / "_meta" / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(runs[-1])["notes"]["disabled"] == ["past_window", "named_number"]


def test_too_few_verdicts_do_not_narrow(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects"}])
    verdicts(root, [{"check_id": "chk-00000001", "verdict": "no", "by": "Ada Okafor",
                     "at": pages.iso(NOW - H)}])
    report = check.run(root, now=NOW)
    assert report.disabled == []


def test_cli_runs(tmp_path, capsys):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects"}])
    assert check.main([str(root)]) == 0
    assert "hits" in capsys.readouterr().out
