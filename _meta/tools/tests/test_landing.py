"""Landing at the home (vision section 9): silent, pending, disagreement, duplicate; the sample."""
import datetime as dt
import json

import yaml

import landing
import pages
import validate
from conftest import NOW, make_space, write_page

HARBOR = {"id": "harbor", "kind": "projects", "title": "Harbor", "part_of": "delivery"}
DELIVERY = {"id": "delivery", "kind": "domains", "title": "Delivery", "owner": "Ben Liu"}


def released(root, pid, body, signer="Ada Okafor", subject="harbor", **extra):
    fm = dict(id=pid, type="entity", title=f"Harbor ({pid})", status="stable", body=body,
              subject=subject, kind="projects", released_from="ada", released_by=signer,
              released_at=pages.iso(NOW), release_class="at_once", evidence=["m-1"],
              signed_by=signer, signed_at=pages.iso(NOW), signed_in="ada",
              fingerprint=pages.fingerprint(body))
    fm.update(extra)
    return write_page(root, f"_inbound/candidates/{pid}.md", **fm)


def harbor_space(tmp_path):
    return make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ben Liu",
                      subjects=[DELIVERY, HARBOR])


def errors(root):
    return [e for e in validate.validate(root) if not e.startswith("warning:")]


def test_a_routine_signed_release_lands_silently(tmp_path):
    root = harbor_space(tmp_path)
    released(root, "p-0000a001", "Harbor goes live on 3 November.\n")
    report = landing.land(root, now=NOW)
    assert report.silent == ["p-0000a001"]
    target = root / "knowledge/projects/harbor/p-0000a001.md"
    fm, body = pages.read(target)
    assert fm["status"] == "stable" and fm["signed_by"] == "Ada Okafor"
    assert fm["fingerprint"] == pages.fingerprint(body) and fm["landed_from"] == "ada"
    assert "class" not in fm and "destination" not in fm
    assert not (root / "_inbound/candidates/p-0000a001.md").exists()
    assert "landed p-0000a001 silently" in (root / "log.md").read_text(encoding="utf-8")
    rows = [json.loads(l) for l in (root / "_meta/state/landed.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["id"] == "p-0000a001" and rows[0]["from"] == "ada"
    assert errors(root) == []


def test_unsigned_records_land_as_evidence_once(tmp_path):
    root = harbor_space(tmp_path)
    for pid, who in (("p-0000a002", "ada"), ("p-0000a012", "ben")):
        write_page(root, f"_inbound/candidates/{pid}.md", id=pid, type="source", title="A mail",
                   subject="harbor", released_from=who, release_class="at_once", at="2026-10-11T09:00:00Z")
    report = landing.land(root, now=NOW)
    assert report.silent == [] and report.records == ["p-0000a002"]
    assert report.record_duplicates == ["p-0000a012"]
    assert not list((root / "_inbound" / "candidates").glob("*.md"))
    page = root / "knowledge/sources/2026/10/p-0000a002.md"
    fm, _ = pages.read(page)
    assert fm["status"] == "draft" and fm["landed_from"] == "ada" and fm["also_from"] == ["ben"]


def test_high_stakes_and_trust_land_pending_for_the_domain_owner(tmp_path):
    root = harbor_space(tmp_path)
    released(root, "p-0000a003", "The contract renews in March.\n")
    released(root, "p-0000a004", "Plain fact.\n", signer="Chloe Martin", released_from="chloe",
             subject="harbor")
    (root / "_meta/state/trust.yaml").write_text(yaml.safe_dump({"pending": ["chloe"]}), encoding="utf-8")
    report = landing.land(root, now=NOW)
    assert sorted(report.pending) == ["p-0000a003", "p-0000a004"]
    fm, _ = pages.read(root / "_inbound/candidates/p-0000a003.md")
    assert fm["landing"].startswith("pending: high stakes") and fm["for"] == "Ben Liu"
    fm, _ = pages.read(root / "_inbound/candidates/p-0000a004.md")
    assert fm["landing"].startswith("pending: trust")


def test_duplicate_is_dropped_and_someone_elses_page_waits_for_its_signer(tmp_path):
    root = harbor_space(tmp_path)
    body = "Harbor goes live on 3 November.\n"
    write_page(root, "knowledge/projects/harbor/p-0000a005.md", id="p-0000a005", type="entity",
               title="Harbor", status="stable", subject="harbor", body=body, signed_by="Ben Liu",
               signed_at=pages.iso(NOW), signed_in="harbor", fingerprint=pages.fingerprint(body))
    released(root, "p-0000a005", body, signer="Ben Liu")
    report = landing.land(root, now=NOW)
    assert report.duplicates == ["p-0000a005"]
    assert not (root / "_inbound/candidates/p-0000a005.md").exists()
    released(root, "p-0000a005", "Harbor goes live on 10 November.\n")
    report = landing.land(root, now=NOW)
    assert report.pending == ["p-0000a005"]
    fm, _ = pages.read(root / "_inbound/candidates/p-0000a005.md")
    assert fm["landing"].startswith("pending: someone else's signed page") and fm["for"] == "Ben Liu"


def test_two_releases_for_one_page_disagree(tmp_path):
    root = harbor_space(tmp_path)
    released(root, "p-0000a006", "Go-live: 2026-11-03.\n", target="knowledge/projects/harbor/harbor.md")
    released(root, "p-0000a007", "Go-live: 2026-11-10.\n", signer="Ben Liu", released_from="ben",
             target="knowledge/projects/harbor/harbor.md")
    report = landing.land(root, now=NOW)
    assert sorted(report.disagreements) == ["p-0000a006", "p-0000a007"]
    fm, _ = pages.read(root / "_inbound/candidates/p-0000a006.md")
    assert fm["disagrees_with"] == ["p-0000a007"] and fm["for"] == "Ben Liu"


def test_a_tampered_release_stays_pending(tmp_path):
    root = harbor_space(tmp_path)
    released(root, "p-0000a008", "Original.\n", fingerprint="0" * 64)
    report = landing.land(root, now=NOW)
    assert report.pending == ["p-0000a008"]
    assert pages.read(root / "_inbound/candidates/p-0000a008.md")[0]["landing"] == "pending: fingerprint mismatch"


def test_weekly_sample_item_and_a_failed_sample(tmp_path):
    root = harbor_space(tmp_path)
    for n in range(4):
        released(root, f"p-0000b00{n}", f"Fact {n}.\n")
    landing.land(root, now=NOW)
    item = landing.sample_item(root, now=NOW + dt.timedelta(hours=1), given={})
    assert item["kind"] == "sample" and item["id"].startswith("sample-")
    picked = json.loads(item["payload"])
    assert 1 <= len(picked) <= 5
    assert landing.sample_item(root, now=NOW + dt.timedelta(hours=2), given={item["id"]: {"verb": "yes"}}) is None
    landing.apply_sample(root, item, {"verb": "no", "note": "1"}, now=NOW + dt.timedelta(hours=3))
    rows = [json.loads(l) for l in (root / "_meta/state/samples.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["failed"] == [picked[0]["id"]]
    signals = list((root / "_outbound/signals").glob("*.yaml"))
    assert len(signals) == 1
    signal = yaml.safe_load(signals[0].read_text(encoding="utf-8"))
    assert signal["kind"] == "sample_failed" and signal["destination"] == "ada"
    assert signal["item"] == picked[0]["id"] and "text" not in signal


def test_trust_switch_after_enough_failed_samples(tmp_path):
    root = harbor_space(tmp_path)
    log = root / "_meta/state/samples.jsonl"
    lines = []
    for n in range(6):
        lines.append(json.dumps({"at": pages.iso(NOW - dt.timedelta(days=n)), "items": [f"x{n}"],
                                 "failed": [f"x{n}"] if n < 2 else [], "releasers": {f"x{n}": "ada"}}))
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert landing.recompute_trust(root, now=NOW) == ["ada"]
    assert yaml.safe_load((root / "_meta/state/trust.yaml").read_text(encoding="utf-8"))["pending"] == ["ada"]
