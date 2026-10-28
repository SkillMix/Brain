"""Landing at the home (vision section 9): silent, pending, disagreement, duplicate; the sample."""
import datetime as dt
import json
import re
import subprocess
import typing

import yaml

import ghrest
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
    released(root, "p-0000a003", "The contract renews in March.\n", high_stakes="the client renews for a year")
    released(root, "p-0000a004", "Plain fact.\n", signer="Chloe Martin", released_from="chloe",
             subject="harbor")
    released(root, "p-0000a009", "The contract rules for the parser are fixed.\n")   # a word, not flagged
    (root / "_meta/state/trust.yaml").write_text(yaml.safe_dump({"pending": ["chloe"]}), encoding="utf-8")
    report = landing.land(root, now=NOW)
    assert sorted(report.pending) == ["p-0000a003", "p-0000a004"] and report.silent == ["p-0000a009"]
    fm, _ = pages.read(root / "_inbound/candidates/p-0000a003.md")
    assert fm["landing"] == "pending: high stakes (the client renews for a year)" and fm["for"] == "Ben Liu"
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


def test_a_high_stakes_record_lands_pending_not_filed(tmp_path):
    """An unsigned record flagged high stakes waits for the domain owner's yes instead of being
    filed silently as evidence; a second run leaves it so. A high-stakes word alone flags nothing."""
    root = harbor_space(tmp_path)
    write_page(root, "_inbound/candidates/p-0000a020.md", "The API errors again.\n", id="p-0000a020",
               type="source", title="Alert", subject="harbor", released_from="ada",
               release_class="window", at="2026-10-11T09:00:00Z", high_stakes="a security incident at the client")
    write_page(root, "_inbound/candidates/p-0000a021.md", "The contract renews in March.\n",
               id="p-0000a021", type="source", title="A mail", subject="harbor", released_from="ada",
               release_class="window", at="2026-10-11T09:00:00Z")
    report = landing.land(root, now=NOW)
    assert report.pending == ["p-0000a020"] and report.records == ["p-0000a021"]
    fm, _ = pages.read(root / "_inbound/candidates/p-0000a020.md")
    assert fm["landing"] == "pending: high stakes (a security incident at the client)" and fm["for"] == "Ben Liu"
    again = landing.land(root, now=NOW)
    assert again.records == [] and again.pending == []


def test_the_domain_owners_yes_files_a_pending_record_as_evidence(tmp_path):
    import sign
    root = harbor_space(tmp_path)
    write_page(root, "_inbound/candidates/p-0000a022.md", "The API errors again.\n", id="p-0000a022",
               type="source", title="Alert", subject="harbor", released_from="ada",
               release_class="window", at="2026-10-11T09:00:00Z", high_stakes=True)
    landing.land(root, now=NOW)
    sign.apply(root, {"p-0000a022": "yes"}, by="Ben Liu", now=NOW)
    filed = list((root / "knowledge" / "sources").rglob("p-0000a022.md"))
    assert len(filed) == 1
    fm, _ = pages.read(filed[0])
    assert fm["status"] == "draft"
    assert not (root / "_inbound/candidates/p-0000a022.md").exists()


def signer_gh(monkeypatch, code, stdout="", stderr=""):
    """The signer's `log.md` as `gh api` answers it; every other command runs as it would."""
    real = subprocess.run

    def run(args, **kwargs):
        if args[0] == "gh":
            return subprocess.CompletedProcess(args, code, stdout, stderr)
        return real(args, **kwargs)
    monkeypatch.setattr(subprocess, "run", run)


def test_a_signer_history_the_run_cannot_reach_leaves_the_release_waiting(tmp_path, monkeypatch):
    root = harbor_space(tmp_path)
    path = released(root, "p-0000a301", "Harbor goes live on 3 November.\n", signed_repo="ada/ada-space")
    before = path.read_bytes()
    signer_gh(monkeypatch, 1, stderr="GitHub access to this repository is not enabled for this session (HTTP 404)\n")
    assert landing.verified(pages.read(path)[0], {}) == "unknown"
    report = landing.land(root, now=NOW)
    assert report.silent == [] and report.pending == []
    assert path.read_bytes() == before   # neither landed nor marked: a later run that can ask decides
    assert not (root / "knowledge/projects/harbor/p-0000a301.md").exists()


def test_the_signer_log_names_its_three_answers_and_reads_ghrests_one_pattern(monkeypatch):
    assert typing.get_type_hints(landing.signer_log)["return"] == str | type(landing.UNREACHABLE) | None
    monkeypatch.setattr(ghrest, "NOT_ENABLED", re.compile(r"refused for this run"))
    signer_gh(monkeypatch, 1, stderr="gh: refused for this run (HTTP 404)\n")
    assert landing.signer_log("ada/ada-space", {}) is landing.UNREACHABLE


def test_a_signer_history_that_cannot_be_read_still_waits_pending(tmp_path, monkeypatch):
    root = harbor_space(tmp_path)
    path = released(root, "p-0000a302", "Harbor goes live on 3 November.\n", signed_repo="ada/ada-space")
    signer_gh(monkeypatch, 1, stderr="gh: Not Found (HTTP 404)\n")
    assert landing.land(root, now=NOW).pending == ["p-0000a302"]
    assert "signer's history" in pages.read(path)[0]["landing"]
