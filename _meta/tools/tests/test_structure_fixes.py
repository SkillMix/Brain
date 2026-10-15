"""The 2026-10-13 audit, batch 5: the validator's audience, parent and window checks; the
checker's departed person and one hit rate; declared sources; other clients and mixed
passages; at-once answers in the log; pull-backs pause; retraction at the home."""
import datetime as dt
import json

import yaml

import check
import landing
import librarian
import pages
import release
import request
import sign
import spaceyaml
import sweep
import validate
from conftest import NOW, make_space, mount_brain, write_page
from test_request import H, world


def set_yaml(root, **fields):
    data = yaml.safe_load((root / "space.yaml").read_text(encoding="utf-8"))
    data.update(fields)
    (root / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_the_validator_checks_audience_parent_and_windows(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    set_yaml(brain, audience=["Ada Okafor"])
    assert any("the brain's audience is `everyone`" in e for e in validate.validate(brain))
    ada = make_space(tmp_path / "ada", audience=["Ben Liu"], parent="sales")
    errs = validate.validate(ada)
    assert any("audience names its owner" in e for e in errs)
    assert any("parent `sales` is not mounted" in e for e in errs)
    write_page(ada, "knowledge/topics/old.md", type="policy", status="stable", signed_by="Ada Okafor",
               signed_at="2020-01-01T00:00:00Z", signed_in="ada", fingerprint=pages.fingerprint("Body.\n"))
    write_page(ada, "knowledge/topics/bad.md", review_every="weekly")
    errs = validate.validate(ada)
    assert any("old.md: past its 365-day window" in e and e.startswith("warning:") for e in errs)
    assert any("bad.md: review_every is a whole number of days" in e for e in errs)


def test_the_checker_names_a_person_who_left_and_uses_one_rate(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "ben", "kind": "people", "title": "Ben Liu",
                                                   "state": "archived"}])
    write_page(root, "knowledge/people/ben/role.md", "Ben leads delivery.\n", type="entity", subject="ben",
               status="stable", signed_by="Ada Okafor", signed_at="2026-10-01T00:00:00Z", signed_in="ada",
               fingerprint=pages.fingerprint("Ben leads delivery.\n"))
    report = check.run(root, now=NOW)
    texts = [str(h) for h in getattr(report, "hits", [])] + [
        p.read_text(encoding="utf-8") for p in (root / "_inbound" / "checks").glob("*.md")]
    assert any("has left" in t for t in texts)
    rows = [{"verdict": v} for v in ("yes", "no", "edit", "retire", "later")]
    assert check.real_share(rows) == 0.6


def test_a_brain_sweeps_nothing_and_a_space_only_its_declared_feeds(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    feed = brain / "_meta/state/feed/mail.jsonl"
    feed.parent.mkdir(parents=True)
    feed.write_text(json.dumps({"id": "m1", "kind": "mail", "at": "2026-10-11T08:00:00Z", "text": "x"}) + "\n",
                    encoding="utf-8")
    assert sweep.run(brain, now=NOW).records_seen == 0
    ada = make_space(tmp_path / "ada", sources=[{"name": "mail", "adapter": "files", "audience": "owner"}])
    for name in ("mail", "chat"):
        path = ada / f"_meta/state/feed/{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"id": f"{name}-1", "kind": "message", "at": "2026-10-11T08:00:00Z",
                                    "text": "x"}) + "\n", encoding="utf-8")
    assert sweep.run(ada, now=NOW).records_seen == 1


def test_an_at_once_answer_is_logged_and_a_failed_sample_pauses_its_choice(tmp_path):
    roots = world(tmp_path)
    request.ask(roots["chloe"], "harbor", "status", "Where does Harbor stand?", asked_by="Chloe Martin", now=NOW)
    release.ship(roots["chloe"], now=NOW, offline_roots=roots)
    request.answer(roots["harbor"], now=NOW + H, offline_roots=roots)
    log = (roots["harbor"] / "log.md").read_text(encoding="utf-8")
    assert "at_once (standing choice: status" in log
    rid = json.loads((roots["harbor"] / "_meta/state/requests.jsonl").read_text(encoding="utf-8").splitlines()[-1])["id"]
    landing.apply_sample(roots["harbor"], {"payload": json.dumps([{"id": rid, "from": "a standing choice"}])},
                         {"verb": "no", "by": "Ben Liu"}, now=NOW + 2 * H)
    assert release.is_paused(roots["harbor"], "request_choice:chloe:status", NOW + 3 * H)


def test_a_pull_back_before_shipping_pauses_the_path(tmp_path):
    root = make_space(tmp_path / "ada")
    write_page(root, "_outbound/queue/p-0000d101.md", id="p-0000d101", type="source", title="A mail",
               destination="harbor", stage_after="2026-10-12T10:00:00Z", listed=False,
               path="standing_choice:harbor:mail", **{"class": "at_once"})
    sign.apply(root, {"p-0000d101": "no"}, by="Ada Okafor", now=NOW)
    assert release.is_paused(root, "standing_choice:harbor:mail", NOW + dt.timedelta(days=1))


def test_the_subjects_owner_retracts_at_the_home_and_the_releaser_is_told(tmp_path):
    root = make_space(tmp_path / "harbor", profile="project", name="harbor")
    write_page(root, "knowledge/projects/harbor/go-live.md", id="p-0000d201", type="concept",
               title="Go-live", status="stable", signed_by="Ada Okafor", signed_at="2026-10-11T00:00:00Z",
               signed_in="ada", fingerprint=pages.fingerprint("Body.\n"), landed_from="ada")
    __import__("ids").build(root)
    landing.retract(root, "p-0000d201", by="Ben Liu", why="the date moved", now=NOW)
    fm, _ = pages.read(root / "knowledge/projects/harbor/go-live.md")
    assert fm["status"] == "deprecated" and fm["retracted_by"] == "Ben Liu"
    signal = yaml.safe_load(next((root / "_outbound/signals").glob("*.yaml")).read_text(encoding="utf-8"))
    assert signal["destination"] == "ada" and signal["item"] == "p-0000d201"


def test_a_routine_never_reviews_its_own_proposal(tmp_path):
    root = make_space(tmp_path / "ada")
    try:
        librarian.apply(root, "anything", reviewed_by="librarian")
    except ValueError as exc:
        assert "never the routine" in str(exc)
    else:
        raise AssertionError("a routine reviewed its own proposal")
