"""The 2026-10-13 audit, batch 1: held items answered, records filed, high stakes kept waiting,
named people flagged, signals read at the home, silent landings counted."""
import datetime as dt
import json

import yaml

import landing
import morning_view
import needs_you
import pages
import registry
import release
import sign
import spaceyaml
import status
import sweep
from conftest import NOW, make_space, write_page

HARBOR = {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor"}
BEN = {"id": "ben", "kind": "people", "title": "Ben Liu"}


def held(root, pid, **fm):
    data = {"type": "source", "title": "A mail about Harbor", "kind": "mail", "subject": "harbor",
            "destination": "harbor", "class": "doubt", "from_space": "ada", **fm}
    return release.hold(root, dict(id=pid, **data), "The mail.\n", "doubt", "call with outsiders")


def test_doubt_items_open_one_by_one_and_take_the_five_verbs(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR], owner_email="ada@lantern.example")
    for pid in ("p-0000f001", "p-0000f002", "p-0000f003"):
        held(root, pid)
    items = needs_you.doubt_items(root)
    assert [i.id for i in items] == ["p-0000f001", "p-0000f002", "p-0000f003"]
    assert all(i.recommendation.startswith("no") and "call with outsiders" in i.text for i in items)
    report = sign.apply(root, {"p-0000f001": "yes", "p-0000f002": "no", "p-0000f003": "always"},
                        by="Ada Okafor", now=NOW)
    assert sorted(report.signed) == ["p-0000f001", "p-0000f003"] and report.discarded == ["p-0000f002"]
    fm, _ = pages.read(root / "_outbound/queue/p-0000f001.md")
    assert fm["class"] == "at_once" and fm["path"] == "approved:Ada Okafor"
    assert needs_you.doubt_count(root) == 0      # the kept one is answered, not counted again
    assert {"destination": "harbor", "kind": "mail"} in spaceyaml.load(root).standing_choices


def test_a_high_stakes_item_still_waits_its_window_after_a_yes(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    held(root, "p-0000f011", title="The contract renews")
    sign.apply(root, {"p-0000f011": "yes"}, by="Ada Okafor", now=NOW)
    fm, _ = pages.read(root / "_outbound/queue/p-0000f011.md")
    assert fm["class"] == "window" and pages.parse_iso(fm["stage_after"]) > NOW
    write_page(root, "_inbound/candidates/p-0000f012.md", "Our pricing changes in March.\n",
               id="p-0000f012", type="concept", title="Pricing", subject="harbor", destination="harbor")
    sign.apply(root, {"p-0000f012": "yes"}, by="Ada Okafor", now=NOW)
    fm, _ = pages.read(root / "_outbound/queue/p-0000f012.md")
    assert fm["class"] == "window" and fm["high_stakes"] is True


def test_a_record_is_filed_as_evidence_never_signed(tmp_path):
    root = make_space(tmp_path / "harbor", profile="project", name="harbor")
    write_page(root, "_inbound/candidates/p-0000f021.md", id="p-0000f021", type="source",
               title="A mail", at="2026-10-11T08:00:00Z")
    sign.apply(root, {"p-0000f021": "yes"}, by="Ada Okafor", now=NOW)
    fm, _ = pages.read(root / "knowledge/sources/2026/10/p-0000f021.md")
    assert fm["status"] == "draft" and "signed_by" not in fm


def test_an_outside_mail_naming_a_person_is_high_stakes_and_a_sent_quote_is_not(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR, BEN], owner_email="ada@lantern.example")
    space = spaceyaml.load(root)
    reg = registry.Registry(root, space)
    subject = reg.get("harbor")
    outside = {"id": "m1", "kind": "mail", "from": "ops@tidewater.example", "at": "2026-10-11T08:00:00Z",
               "text": "Ben Liu missed the review twice."}
    verdict = sweep.classify(space, outside, subject, "harbor", None, "owner", NOW)
    assert verdict.cls == "window" and verdict.extra.get("high_stakes") and "Ben Liu" in verdict.why
    colleague = dict(outside, id="m2", **{"from": "chloe@lantern.example"})
    assert not (sweep.classify(space, colleague, subject, "harbor", None, "owner", NOW).extra or {}).get(
        "high_stakes")
    quote = {"id": "m3", "kind": "mail", "from": "Ada Okafor <ada@lantern.example>",
             "at": "2026-10-11T08:00:00Z", "text": "As agreed, the price list for the pilot is attached."}
    assert not (sweep.classify(space, quote, subject, "harbor", None, "owner", NOW).extra or {}).get(
        "high_stakes")


def test_a_signal_flags_the_homes_status_page(tmp_path):
    root = make_space(tmp_path / "harbor", profile="project", name="harbor",
                      subjects=[dict(HARBOR, state="active")])
    folder = root / "_inbound" / "signals"
    folder.mkdir(parents=True)
    (folder / "s1.yaml").write_text(yaml.safe_dump({"id": "s1", "subject": "harbor", "destination": "harbor",
                                                    "at": "2026-10-12T07:00:00Z", "note": "open item moved",
                                                    "from": "ada"}), encoding="utf-8")
    status.run(root, now=NOW)
    text = (root / "live/subjects/harbor.md").read_text(encoding="utf-8")
    assert "News from ada: something here moved; the details follow" in text


def test_disagreeing_releases_carry_a_disagreements_line(tmp_path):
    root = make_space(tmp_path / "harbor", profile="project", name="harbor", subjects=[HARBOR])
    for pid, text, who in (("p-0000f031", "Go-live is 3 November.\n", "Ada Okafor"),
                           ("p-0000f032", "Go-live is 9 November.\n", "Ben Liu")):
        write_page(root, f"_inbound/candidates/{pid}.md", text, id=pid, type="concept",
                   title="Go-live", subject="harbor", target="knowledge/projects/harbor/go-live.md",
                   status="stable", signed_by=who, signed_at="2026-10-11T08:00:00Z", signed_in="ada",
                   fingerprint=pages.fingerprint(text), released_from="ada")
    report = landing.land(root, now=NOW)
    assert sorted(report.disagreements) == ["p-0000f031", "p-0000f032"]
    _, body = pages.read(root / "_inbound/candidates/p-0000f031.md")
    assert "## Disagreements" in body and "p-0000f032" in body


def test_the_morning_view_counts_the_weeks_silent_landings(tmp_path):
    root = make_space(tmp_path / "ada", owner_email="ada@lantern.example")
    path = root / "_meta/state/landed.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"at": pages.iso(NOW - dt.timedelta(days=d)), "id": f"p-{d}"} for d in (1, 2, 9)]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    space = spaceyaml.load(root)
    assert morning_view._landed_this_week(root, space, NOW) == 2
