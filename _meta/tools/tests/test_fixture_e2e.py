"""The fixture company end to end: onboard, a scaled week of chains, verdicts, tests.

This is the phase-1 gate of the template: `onboard.py --fixture` must build the six
spaces, run every routine on them, land releases only where the rules allow, and pass
the answer, leak and behaviour tests. It takes about a minute.
"""
from pathlib import Path

import pytest
import yaml

import onboard
import pages

TEMPLATE_ROOT = Path(__file__).resolve().parents[3]


OUTPUT = {}   # what the fixture run printed


@pytest.fixture(scope="module")
def fixture_dir(tmp_path_factory):
    if not (TEMPLATE_ROOT / ".github" / "template-marker").exists():
        pytest.skip("the fixture company is the template's own gate; instances run their answer, "
                    "leak and behaviour tests instead")
    # A fresh interpreter, exactly as a newcomer runs it: no state from other tests (clock
    # overrides, patched modules, cached readers) can reach the fixture company.
    import subprocess
    import sys
    target = tmp_path_factory.mktemp("lantern")
    res = subprocess.run([sys.executable, str(TEMPLATE_ROOT / "_meta" / "tools" / "onboard.py"),
                          "--fixture", str(target)], capture_output=True, text=True,
                         cwd=str(TEMPLATE_ROOT), env={k: v for k, v in __import__("os").environ.items()
                                                      if k != "SPACE_NOW"})
    if res.returncode != 0:
        print(res.stdout[-3000:], res.stderr[-2000:])
    OUTPUT["stdout"] = res.stdout
    return target, res.returncode


def test_fixture_run_is_green(fixture_dir):
    target, code = fixture_dir
    assert code == 0
    for name in ("brain", "ada", "ben", "chloe", "harbor", "beacon", "channel-chloe-harbor", "channel-ada-ben"):
        assert (target / name / "space.yaml").exists()


def test_the_run_ends_with_one_verdict_line_and_no_owner_steps(fixture_dir):
    lines = OUTPUT["stdout"].rstrip().splitlines()
    assert lines[-1] == "fixture: passed" and lines[-2].startswith("cand: candidates waiting")
    assert not [line for line in lines if line.startswith("owner ")], "a sign-in reads like a to-do"


def test_the_fixture_writes_the_onboarding_page_its_top_owner_sees(fixture_dir):
    target, _ = fixture_dir
    html = (target / "onboarding.html").read_text(encoding="utf-8")
    assert "Lantern Works | company brain" in html and "Ada Okafor's workspace" in html
    assert f"page: {target / 'onboarding.html'}" in OUTPUT["stdout"]


def test_at_once_release_landed_in_harbor(fixture_dir):
    target, _ = fixture_dir
    # harbor's own weekly review batch (a contested page) is the one candidate made at home
    landed = [p for p in (target / "harbor" / "_inbound" / "candidates").glob("*.md")
              if not p.name.startswith("review-")]
    # records handed sideways are evidence at their home, never something to sign
    records = [p for p in (target / "harbor" / "knowledge" / "sources").rglob("*.md")
               if pages.read(p)[0].get("landed_from")]
    classes = {pages.read(p)[0].get("release_class") for p in landed + records}
    assert "at_once" in classes
    assert all(pages.read(p)[0].get("released_from") for p in landed + records)
    assert all(pages.read(p)[0].get("status") == "draft" for p in records)


def test_what_nobody_judged_stays_home_until_a_yes(fixture_dir):
    """Without the router nothing reads what a record says: the rate card waits in doubt even
    under Ada's standing choice, and only her yes would let it go."""
    target, _ = fixture_dir
    held = {p.name: pages.read(p)[0] for p in (target / "ada" / "_outbound" / "held").glob("*.md")}
    assert any(fm.get("evidence") == ["m-ada-002"] and fm.get("reason") == "doubt" for fm in held.values())
    assert sorted(fm.get("reason") for fm in held.values()).count("doubt") >= 3
    content = []
    for zone in ("knowledge", "live", "_inbound", "_outbound"):
        content += [p.read_text(encoding="utf-8") for p in (target / "harbor" / zone).rglob("*")
                    if p.is_file() and p.suffix in (".md", ".yaml")]
    harbor_text = "\n".join(content)
    assert "day rate" not in harbor_text.lower() and "EUR 950" not in harbor_text


def test_signed_page_carries_fingerprint_and_travels(fixture_dir):
    target, _ = fixture_dir
    signed = [p for p in (target / "ada" / "knowledge").rglob("*.md")
              if pages.read(p)[0].get("status") == "stable"]
    assert signed, "Ada's yes should have produced a stable page"
    fm, body = pages.read(signed[0])
    assert fm["fingerprint"] == pages.fingerprint(body)
    assert fm["signed_by"] == "Ada Okafor" and fm["signed_in"] == "ada"


def test_needs_you_shows_doubt_count_and_health_shows_cost(fixture_dir):
    target, _ = fixture_dir
    needs = (target / "ada" / "live" / "needs-you.md").read_text(encoding="utf-8")
    assert "held in doubt" in needs
    health = (target / "ada" / "live" / "health.md").read_text(encoding="utf-8")
    assert "cost" in health and "tokens" in health
    assert "probe walk" in health and "index none" in health and "requests asked" in health
    assert (target / "ben" / "_meta" / "state" / "probe.jsonl").exists()


def test_a_held_record_left_only_after_its_persons_yes(fixture_dir):
    target, _ = fixture_dir
    log = (target / "chloe" / "log.md").read_text(encoding="utf-8")
    assert "released m-chloe-002 from doubt by Chloe Martin" in log
    landed = [pages.read(p)[0] for p in (target / "beacon" / "_inbound" / "candidates").glob("*.md")]
    landed += [pages.read(p)[0] for p in (target / "beacon" / "knowledge" / "sources").rglob("*.md")]
    assert any(fm.get("evidence") == ["m-chloe-002"] and fm.get("acted_for") == "Chloe Martin" for fm in landed)


def test_time_scale_only_in_fixture_mode(fixture_dir):
    target, _ = fixture_dir
    data = yaml.safe_load((target / "ada" / "space.yaml").read_text(encoding="utf-8"))
    assert data["mode"] == "fixture" and data["time_scale"] == 1440


def test_checker_hits_after_signing(fixture_dir):
    """A late record contradicts the page Ada signed: the checker writes one pre-answered hit."""
    target, _ = fixture_dir
    hits = [pages.read(p)[0] for p in (target / "ada" / "_inbound" / "checks").glob("*.md")]
    assert hits, "the checker should have met the late go-live date"
    hit = hits[0]
    assert hit.get("recommendation") and hit.get("route")
    assert hit["route"][0] == "Ada Okafor"


def test_the_librarian_files_strays_and_proposes_merge_and_umbrella(fixture_dir):
    """Ben's misfiled desk folder and stray page are moved and logged; the brain proposes a
    merge (two subjects, one repository) and an umbrella (three topics, one theme), and a
    reviewed proposal applies with its reviewer recorded."""
    import librarian
    import registry
    target, _ = fixture_dir
    ben = target / "ben"
    assert (ben / "work" / "projects" / "beacon" / "export-notes" / "notes.md").exists()
    assert (ben / "knowledge" / "projects" / "beacon" / "beacon-fact.md").exists()
    log = (ben / "log.md").read_text(encoding="utf-8")
    assert "librarian kind" in log and "librarian stray" in log
    props = librarian.proposals(target / "brain")
    kinds = sorted(d["change"]["kind"] for d in props.values())
    assert "merge" in kinds and "umbrella" in kinds
    key = next(k for k, d in props.items() if d["change"]["kind"] == "umbrella")
    assert librarian.apply(target / "brain", key, "fixture reviewer", scorer=lambda r: (0, 0)) == "applied"
    assert registry.Registry(target / "brain").get("first-week").part_of == "onboarding"


def test_the_decisions_index_answers_the_decision_question(fixture_dir):
    target, _ = fixture_dir
    data = yaml.safe_load((target / "chloe" / "_meta" / "state" / "decisions.yaml").read_text(encoding="utf-8"))
    assert any("one quarter" in str(r.get("line")) for r in data["decisions"])
    import json as _json
    rows = [_json.loads(l) for l in (target / "chloe" / "_meta" / "state" / "probe.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert any((r["kinds"].get("decision") or {}).get("decisions") == [1, 1] for r in rows)


def test_requests_through_the_corridors(fixture_dir):
    """Chloe's status request is answered at once by Harbor's standing choice and lands as
    evidence; correspondence is never answered; the detail waited for Harbor's person and
    expired; Ben's request to Ada's workspace waited for Ada and expired."""
    import json as _json
    target, _ = fixture_dir
    answers = list((target / "chloe" / "knowledge" / "sources").rglob("answer-rq-*.md"))
    assert len(answers) == 1
    fm, body = pages.read(answers[0])
    assert fm["approved_by"].startswith("standing choice: status") and "EUR 950" not in body
    outcomes = {_json.loads(l)["outcome"] for l in (target / "harbor" / "_meta/state/requests.jsonl")
                .read_text(encoding="utf-8").splitlines()}
    assert {"answered", "never", "asked", "expired"} <= outcomes
    ben_answers = [p.read_text(encoding="utf-8") for p in (target / "ben" / "_inbound" / "answers").glob("rq-*.md")]
    assert ben_answers and "No answer arrived" in ben_answers[0]
    health = (target / "chloe" / "live" / "health.md").read_text(encoding="utf-8")
    assert "requests asked 3 answered 1 expired 2" in health
    assert "received" in (target / "harbor" / "live" / "health.md").read_text(encoding="utf-8")


def test_weekly_batch_and_a_conflict_note(fixture_dir):
    """Ada's page passes its shortened window: her weekly batch lists it. Ben hears another
    go-live date than the page Ada's release filed at harbor: a conflict note travels there
    after its window and marks that page contested."""
    target, _ = fixture_dir
    batches = sorted((target / "ada" / "_inbound" / "candidates").glob("review-*.md"))
    assert batches, "the weekly batch should list Ada's Harbor page"
    fm, _ = pages.read(batches[0])
    assert any("knowledge/projects/harbor/" in l["page"] for l in fm["lines"])
    log = (target / "harbor" / "log.md").read_text(encoding="utf-8")
    assert "conflict note" in log and "contests" in log
    contested = [pages.read(p)[0] for p in (target / "harbor" / "knowledge" / "projects" / "harbor").glob("p-*.md")
                 if pages.read(p)[0].get("contested")]
    assert contested and contested[0]["status"] == "stable"


def test_signed_release_lands_silently_at_its_home_and_the_sample_draws(fixture_dir):
    """Ada's signed Harbor page travels to harbor and is filed there without a second
    signature; harbor's librarian lists the week's sample for its domain owner."""
    target, _ = fixture_dir
    log = (target / "harbor" / "log.md").read_text(encoding="utf-8")
    assert "silently at knowledge/projects/harbor/" in log
    filed = [pages.read(p)[0] for p in (target / "harbor" / "knowledge" / "projects" / "harbor").glob("p-*.md")]
    assert any(fm.get("status") == "stable" and fm.get("signed_by") == "Ada Okafor"
               and fm.get("landed_from") == "ada" for fm in filed)
    items = yaml.safe_load((target / "harbor" / "_inbound" / "needs-you" / "librarian.yaml").read_text(
        encoding="utf-8"))["items"]
    assert any(i["kind"] == "sample" for i in items)
