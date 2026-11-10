"""Security regressions for landing, sign, ids and validate: where a release or a candidate may
write, the pinned trust root, the id index as data, single-line log lines, retractions, the
signed fields, and what validate refuses that a pull request could plant."""
import json
import os
from pathlib import Path

import pytest
import yaml

import ids
import landing
import pages
import sign
import spaceyaml
import template_sync
import validate
from conftest import NOW, make_space, write_page

HARBOR = {"id": "harbor", "kind": "projects", "title": "Harbor", "part_of": "delivery"}
DELIVERY = {"id": "delivery", "kind": "domains", "title": "Delivery", "owner": "Ben Liu"}
PEOPLE = [{"id": "ada-okafor", "kind": "people", "title": "Ada Okafor", "workspace": "ada"},
          {"id": "ben-liu", "kind": "people", "title": "Ben Liu", "workspace": "ben"}]
ADA_REPO = "ada/ada-space"
BEN_REPO = "ben/ben-space"


def home(tmp_path, mode="live"):
    """harbor, a project home that knows ada's and ben's repositories and who signs in each."""
    return make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ben Liu", mode=mode,
                      subjects=[DELIVERY, HARBOR] + PEOPLE,
                      mounts=[{"name": "ada", "repo": ADA_REPO}, {"name": "ben", "repo": BEN_REPO}])


def release(root, pid, body="Harbor goes live on 3 November.\n", signer="Ada Okafor", sender="ada", **extra):
    fm = dict(id=pid, type="entity", title=f"Harbor ({pid})", status="stable", subject="harbor",
              kind="projects", released_from=sender, released_by=signer, released_at=pages.iso(NOW),
              release_class="at_once", signed_by=signer, signed_at=pages.iso(NOW), signed_in=sender,
              fingerprint=pages.fingerprint(body), signed_repo=ADA_REPO if sender == "ada" else BEN_REPO)
    fm.update(extra)
    fm = {k: v for k, v in fm.items() if v is not None}
    fm["signed_fields"] = sign.fields_fingerprint(fm)
    write_page(root, f"_inbound/candidates/{pid}.md", body, **fm)
    return fm


def history(*fms):
    """A signer's log.md holding both trust-root lines for each release given."""
    rows = []
    for fm in fms:
        rows.append(f"- {fm['signed_at']} fingerprint {fm['id']} {fm['fingerprint']} by {fm['signed_by']}")
        rows.append(f"- {fm['signed_at']} fields {fm['id']} {fm['signed_fields']} by {fm['signed_by']}")
    return "".join(r + "\n" for r in rows)


def logs(monkeypatch, by_repo: dict):
    """signer_log answers from the given {repo: text}; every repository asked is recorded."""
    asked = []
    monkeypatch.setattr(landing, "signer_log", lambda repo, cache: asked.append(repo) or by_repo.get(repo))
    return asked


def landing_of(root, pid):
    return pages.read(root / f"_inbound/candidates/{pid}.md")[0].get("landing", "")


def errors(root):
    return [e for e in validate.validate(root) if not e.startswith("warning:")]


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}


# ----- 0 and 2: where a release lands ---------------------------------------------------
BAD_TARGETS = [".claude/skills/sweep/SKILL.md", "CLAUDE.md", "knowledge/projects/harbor/CLAUDE.md",
               "_meta/tools/pages.py", "../escape.md", "knowledge/../../escape.md", "C:/x.md",
               "knowledge/projects/.claude/settings.md", "knowledge/sources/2026/10/p-0000aa01.md",
               "knowledge/projects/harbor/index.md", "knowledge/projects/harbor/x.txt",
               "knowledge/nokind/harbor/x.md", "knowledge\\projects\\x.md", "knowledge/projects/nul.md"]


@pytest.mark.parametrize("target", BAD_TARGETS + ["ABSOLUTE"])
def test_a_release_aimed_outside_the_pages_waits_and_writes_nothing(tmp_path, monkeypatch, target):
    root = home(tmp_path)
    (root / "CLAUDE.md").write_text("mine\n", encoding="utf-8")
    if target == "ABSOLUTE":
        target = (tmp_path / "OUTSIDE.md").as_posix()
    fm = release(root, "p-0000aa01", target=target, name="sweep", description="planted")
    logs(monkeypatch, {ADA_REPO: history(fm)})
    before = {k: v for k, v in tree(root).items() if not k.startswith("_inbound/")}
    report = landing.land(root, now=NOW)
    assert report.silent == [] and report.pending == ["p-0000aa01"]
    assert landing_of(root, "p-0000aa01").startswith("pending: bad target")
    after = {k: v for k, v in tree(root).items() if not k.startswith("_inbound/") and k != "log.md"}
    assert after == {k: v for k, v in before.items() if k != "log.md"}
    assert not (tmp_path / "OUTSIDE.md").exists() and not (tmp_path / "escape.md").exists()
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == "mine\n"
    # the required check stops the pull request too (a kind unknown here only warns while the
    # brain mount, which names the company's kinds, is absent)
    assert any("bad target" in e for e in validate.validate(root))


@pytest.mark.parametrize("field,value", [("kind", "../../.claude"), ("subject", "../../../.claude/skills/x"),
                                         ("subject", "Harbor"), ("id", "../../../../.claude/skills/route/SKILL")])
def test_a_release_whose_kind_subject_or_id_is_a_path_is_held(tmp_path, monkeypatch, field, value):
    root = home(tmp_path)
    fm = release(root, "p-0000aa02", **{field: value})
    logs(monkeypatch, {ADA_REPO: history(fm)})
    report = landing.land(root, now=NOW)
    assert report.silent == [] and report.pending == ["p-0000aa02"]
    assert landing_of(root, "p-0000aa02").startswith("pending: malformed release")
    assert not (root / ".claude").exists()
    assert [p for p in (root / "knowledge").rglob("*.md")] == []
    assert any("malformed release" in e for e in errors(root))


def test_an_unsigned_record_with_a_path_for_an_id_is_never_filed(tmp_path):
    root = home(tmp_path)
    skill = root / ".claude/skills/status/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("the real skill\n", encoding="utf-8")
    write_page(root, "_inbound/candidates/p-0d0d0d0d.md", "Injected skill text.\n",
               id="../../../../.claude/skills/status/SKILL", type="source", title="A mail",
               released_from="ben", release_class="at_once", at="2026-10-11T09:00:00Z")
    report = landing.land(root, now=NOW)
    assert report.records == [] and report.pending == ["p-0d0d0d0d"]
    assert skill.read_text(encoding="utf-8") == "the real skill\n"
    assert not list((root / "knowledge/sources").rglob("*.md"))


def test_a_retraction_naming_a_path_changes_no_file(tmp_path):
    root = home(tmp_path)
    now_page = write_page(root, "live/now.md", "Now.\n", id="p-0000ee01", type="snapshot", title="Now",
                          landed_from="ben")
    before = now_page.read_bytes()
    write_page(root, "_inbound/candidates/p-0000ee02.md", "", id="p-0000ee02", type="source", title="r",
               released_from="ben", release_class="retraction", retracts="../../../../live/now")
    landing.land(root, now=NOW)
    assert now_page.read_bytes() == before


def test_sign_refuses_a_local_candidate_aimed_at_claude_md_and_says_where_a_yes_lands(tmp_path):
    root = make_space(tmp_path / "ada")
    (root / "CLAUDE.md").write_text("mine\n", encoding="utf-8")
    write_page(root, "_inbound/candidates/evil.md", "Planted instructions.\n", id="p-00000901", type="entity",
               title="Evil", subject="harbor", kind="projects", target="CLAUDE.md")
    write_page(root, "_inbound/candidates/good.md", "Fine.\n", id="p-00000902", type="entity",
               title="Good", subject="harbor", kind="projects")
    back = sign.read_back(root, ["p-00000901", "p-00000902"])
    assert "it cannot land: target 'CLAUDE.md'" in back
    assert "it lands at knowledge/projects/harbor/good.md" in back
    report = sign.apply(root, {"p-00000901": "yes", "p-00000902": "yes"}, by="Ada Okafor", now=NOW)
    assert [r["id"] for r in report.refused] == ["p-00000901"] and report.signed == ["p-00000902"]
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == "mine\n"
    assert (root / "_inbound/candidates/evil.md").exists()   # still on Needs you
    assert "refused p-00000901 by Ada Okafor: it cannot land" in (root / "log.md").read_text(encoding="utf-8")


def test_a_review_line_whose_page_is_a_path_out_is_refused(tmp_path):
    root = make_space(tmp_path / "ada")
    outside = tmp_path / "outside.md"
    outside.write_text("---\nid: p-00000909\n---\nkeep\n", encoding="utf-8")
    write_page(root, "_inbound/candidates/review-2026-10-12.md", id="p-0000e009", type="review",
               title="Weekly review", review_batch=True, body="# Weekly review\n",
               lines=[{"n": 1, "page": "../outside.md", "id": "p-00000909", "title": "x", "signed": True}])
    report = sign.apply(root, {"review-2026-10-12#1": ["edit", "Forged."]}, by="Ada Okafor", now=NOW)
    assert [r["id"] for r in report.refused] == ["review-2026-10-12#1"]
    assert outside.read_text(encoding="utf-8").endswith("keep\n")


def test_a_target_through_a_linked_folder_is_refused(tmp_path, monkeypatch):
    root = home(tmp_path)
    away = tmp_path / "away"
    away.mkdir()
    try:
        os.symlink(away, root / "knowledge" / "projects", target_is_directory=True)
    except (OSError, NotImplementedError):
        try:   # Windows without the symlink privilege still makes a junction
            import _winapi
            _winapi.CreateJunction(str(away), str(root / "knowledge" / "projects"))
        except (ImportError, OSError, AttributeError):
            pytest.skip("this system does not let the test make a link")
    fm = release(root, "p-0000aa03")
    logs(monkeypatch, {ADA_REPO: history(fm)})
    report = landing.land(root, now=NOW)
    assert report.silent == [] and report.pending == ["p-0000aa03"]
    assert list(away.rglob("*")) == []


# ----- 6: the trust root is pinned by the home ------------------------------------------
def test_a_release_without_a_signer_repository_lands_pending(tmp_path, monkeypatch):
    # the repository is the one this space pins for ada, never the release's: with nothing in its
    # history (and no page of the signer's to compare), the release waits for a person
    root = home(tmp_path)
    release(root, "p-0000bb01", signed_repo=None)
    asked = logs(monkeypatch, {})
    report = landing.land(root, now=NOW)
    assert report.silent == [] and report.pending == ["p-0000bb01"]
    assert landing_of(root, "p-0000bb01") == "pending: the signer's history does not show this signature"
    assert asked == [ADA_REPO]


def test_a_fixture_space_files_a_release_without_a_repository_unchecked(tmp_path, monkeypatch):
    root = home(tmp_path, mode="fixture")
    release(root, "p-0000bb02", signed_repo=None)
    logs(monkeypatch, {})
    assert landing.land(root, now=NOW).silent == ["p-0000bb02"]


def test_the_release_never_picks_the_repository_that_vouches_for_it(tmp_path, monkeypatch):
    root = home(tmp_path)
    fm = release(root, "p-0000bb03", signed_repo="ben/evil")
    asked = logs(monkeypatch, {"ben/evil": history(fm), ADA_REPO: history(fm)})
    report = landing.land(root, now=NOW)
    assert report.pending == ["p-0000bb03"] and report.silent == []
    assert "ben/evil" not in asked
    assert landing_of(root, "p-0000bb03") == "pending: it names a repository that is not ada's"


def test_a_space_vouches_only_for_its_own_people(tmp_path, monkeypatch):
    root = home(tmp_path)
    fm = release(root, "p-0000bb04", signer="Ada Okafor", sender="ben")   # ben's repo, Ada's name
    logs(monkeypatch, {BEN_REPO: history(fm)})
    report = landing.land(root, now=NOW)
    assert report.pending == ["p-0000bb04"] and report.silent == []
    assert "not one of the people who sign in ben" in landing_of(root, "p-0000bb04")


def test_an_unknown_signing_space_lands_pending(tmp_path, monkeypatch):
    root = home(tmp_path)
    fm = release(root, "p-0000bb05", sender="mallory", signer="Mallory", signed_repo="mallory/space")
    logs(monkeypatch, {"mallory/space": history(fm)})
    assert landing.land(root, now=NOW).pending == ["p-0000bb05"]
    assert landing_of(root, "p-0000bb05") == "pending: no repository is known here for mallory"


def test_the_log_line_matches_whole_lines_only():
    text = "- 2026-10-12T09:00:00Z fingerprint p-1 abc by Ada Okafor\n"
    assert landing._has_line(text, "fingerprint", "p-1", "abc", "Ada Okafor")
    assert not landing._has_line(text, "fingerprint", "p-1", "abc", "Ada")
    assert not landing._has_line("x - 1 fingerprint p-1 abc by Ada\n", "fingerprint", "p-1", "abc", "Ada")


def test_a_release_naming_the_signer_over_their_page_from_another_space_waits(tmp_path, monkeypatch):
    root = home(tmp_path)
    body = "Go-live is 3 November.\n"
    write_page(root, "knowledge/projects/harbor/p-0000bb06.md", body, id="p-0000bb06", type="entity",
               title="Harbor", status="stable", subject="harbor", signed_by="Ada Okafor",
               signed_at=pages.iso(NOW), signed_in="ada", fingerprint=pages.fingerprint(body))
    fm = release(root, "p-0000bb06", body="Go-live is cancelled.\n", signer="Ada Okafor", sender="ben",
                 signed_in="ben")
    logs(monkeypatch, {BEN_REPO: history(fm)})
    report = landing.land(root, now=NOW)
    assert report.silent == [] and report.pending == ["p-0000bb06"]
    assert landing_of(root, "p-0000bb06") == "pending: someone else's signed page"
    assert pages.read(root / "knowledge/projects/harbor/p-0000bb06.md")[1] == body


def test_a_genuine_change_by_the_same_signer_lands_silently(tmp_path, monkeypatch):
    root = home(tmp_path)
    body = "Go-live is 3 November.\n"
    write_page(root, "knowledge/projects/harbor/p-0000bb07.md", body, id="p-0000bb07", type="entity",
               title="Harbor", status="stable", subject="harbor", signed_by="Ada Okafor",
               signed_at=pages.iso(NOW), signed_in="ada", fingerprint=pages.fingerprint(body))
    fm = release(root, "p-0000bb07", body="Go-live is 10 November.\n")
    asked = logs(monkeypatch, {ADA_REPO: history(fm)})
    assert landing.land(root, now=NOW).silent == ["p-0000bb07"] and asked == [ADA_REPO]


# ----- 15: the id index is data ---------------------------------------------------------
def test_a_forged_index_and_conflict_notes_change_no_file_outside_the_pages(tmp_path):
    root = home(tmp_path)
    settings = root / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"permissions": {}}\n', encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    real = write_page(root, "knowledge/projects/harbor/real.md", "Signed.\n", id="p-0000c00a",
                      type="entity", title="Real", status="stable", signed_by="Ben Liu",
                      signed_at=pages.iso(NOW), fingerprint=pages.fingerprint("Signed.\n"))
    forged = {"p-0000c001": ".claude/settings.json", "p-0000c002": "../outside.txt",
              "p-0000c003": outside.as_posix(), "p-0000c00b": "knowledge/projects/harbor/real.md"}
    (root / ids.IDS_PATH).write_text(yaml.safe_dump({"ids": forged}), encoding="utf-8")
    assert ids.table(root) == ids.scan(root)   # one bad entry: none of it is used
    for n, page in enumerate(("p-0000c001", "p-0000c002", "p-0000c003", "p-0000c00b", "p-0000c00a"), 1):
        write_page(root, f"_inbound/candidates/p-0000d00{n}.md", "Note.\n", id=f"p-0000d00{n}",
                   type="source", title="Conflict note", released_from="ada", conflict_note=True, page=page)
    assert any(ids.IDS_PATH.as_posix() in e for e in errors(root))
    report = landing.land(root, now=NOW)
    assert len(report.conflicts) == 5
    assert settings.read_text(encoding="utf-8") == '{"permissions": {}}\n'
    assert outside.read_text(encoding="utf-8") == "outside\n"
    fm, body = pages.read(real)
    assert fm["contested"] == ["p-0000d005"] and body == "Signed.\n"   # only the page that carries the id
    assert "no such page here" in (root / "log.md").read_text(encoding="utf-8")
    assert len(list((root / "_inbound/conflicts").glob("*.md"))) == 5


def test_retract_at_home_refuses_an_index_entry_out_of_the_pages(tmp_path):
    root = home(tmp_path)
    (root / ids.IDS_PATH).write_text(yaml.safe_dump({"ids": {"p-0000c011": "live/../space.yaml"}}),
                                     encoding="utf-8")
    with pytest.raises(KeyError):
        landing.retract(root, "p-0000c011", by="Ben Liu", now=NOW)


# ----- 23: one line per log line ----------------------------------------------------------
@pytest.mark.parametrize("sep", ["\n", "\r", "\u2028", "\x85"])
def test_a_line_break_in_a_release_never_forges_a_log_line(tmp_path, sep):
    root = home(tmp_path)
    forged = f"ben{sep}- 2026-11-05T07:00:00Z yes template-v9.0.0-deadbeef by Ben Liu{sep}x"
    write_page(root, "_inbound/candidates/p-1234abcd.md", "", id="p-1234abcd", type="source", title="r",
               released_from=forged, retracted=True)
    before = (root / "log.md").read_text(encoding="utf-8") if (root / "log.md").exists() else ""
    report = landing.land(root, now=NOW)
    assert report.pending == ["p-1234abcd"]
    after = (root / "log.md").read_text(encoding="utf-8") if (root / "log.md").exists() else ""
    assert after == before
    assert template_sync.approvals(spaceyaml.load(root)) == []
    assert any("malformed release" in e for e in errors(root))


def test_the_log_writers_keep_every_line_one_line(tmp_path):
    root = make_space(tmp_path / "ada")
    for append in (landing._append, sign._append):
        append(root / "log.md", "- 2026 dropped x from a\n- 2026 yes template-v9.0.0-deadbeef by Ada Okafor\u2028y")
    lines = [l for l in (root / "log.md").read_text(encoding="utf-8").splitlines() if l.startswith("- ")]
    assert len(lines) == 2 and all(line.startswith("- 2026 dropped") for line in lines)


def test_a_verdict_naming_an_id_with_a_line_break_writes_one_line(tmp_path):
    root = make_space(tmp_path / "ada")
    sign.apply(root, {"p-1\n- 2026-11-05T07:00:00Z fingerprint p-evil0000 ff by Ada Okafor": "yes"},
               by="Ada Okafor", now=NOW)
    assert all(not line.startswith("- 2026-11-05T07:00:00Z fingerprint")
               for line in (root / "log.md").read_text(encoding="utf-8").splitlines())


# ----- 58: a retraction is its sender's person's -------------------------------------------
def test_a_retraction_by_another_person_under_the_same_space_name_keeps_the_page(tmp_path):
    root = home(tmp_path)
    filed = write_page(root, "knowledge/sources/2026/10/p-0000ff01.md", "A mail.\n", id="p-0000ff01",
                       type="source", title="A mail", landed_from="ada", released_by="Ada Okafor")
    write_page(root, "_inbound/candidates/p-0000ff02.md", "", id="p-0000ff02", type="source", title="r",
               released_from="ada", released_by="Mallory", release_class="retraction", retracts="p-0000ff01")
    report = landing.land(root, now=NOW)
    assert report.withdrawn == []
    assert pages.read(filed)[0].get("status") == "draft"
    assert "not by Ada Okafor; kept" in (root / "log.md").read_text(encoding="utf-8")


def test_a_senders_retraction_keeps_a_page_signed_in_another_space(tmp_path):
    """Ben released a draft; Cara signed it here (sign keeps landed_from/released_*). Ben's
    retraction withdraws only what was signed in his space, as the gate tests it (`signed_in`)."""
    root = home(tmp_path)
    body = "Cara's claim.\n"
    for pid, signed_in in (("p-1a2b3c4d", "harbor"), ("p-1a2b3c4e", "ben")):
        write_page(root, f"knowledge/projects/harbor/{pid}.md", body, id=pid, type="concept", title=pid,
                   status="stable", subject="harbor", signed_by="Cara Diaz", signed_in=signed_in,
                   signed_at=pages.iso(NOW), fingerprint=pages.fingerprint(body), landed_from="ben",
                   released_from="ben", released_by="Ben Liu")
        write_page(root, f"_inbound/candidates/p-0000ee{pid[-2:]}.md", "", id=f"p-0000ee{pid[-2:]}",
                   type="source", status="draft", retracts=pid, released_from="ben", released_by="Ben Liu",
                   release_class="retraction")
    report = landing.land(root, now=NOW)
    kept, _ = pages.read(root / "knowledge/projects/harbor/p-1a2b3c4d.md")
    assert kept["status"] == "stable" and not kept.get("retracted_by") and kept["contested"] == ["p-0000ee4d"]
    assert "signed in harbor by Cara Diaz; kept, contested" in (root / "log.md").read_text(encoding="utf-8")
    gone, _ = pages.read(root / "knowledge/projects/harbor/p-1a2b3c4e.md")   # signed in ben's own space
    assert gone["status"] == "deprecated" and gone["retracted_by"] == "Ben Liu"
    assert report.withdrawn == ["p-1a2b3c4e"] and not list((root / "_inbound/candidates").glob("p-0000ee*.md"))


# ----- 63: what a signature binds besides the body ---------------------------------------
def test_signing_binds_the_fields_and_logs_them(tmp_path, monkeypatch):
    monkeypatch.setattr(sign, "_own_repo", lambda root: "acme/ada-workspace")
    root = make_space(tmp_path / "ada")
    write_page(root, "_inbound/candidates/harbor.md", "Go-live is 3 November.\n", id="p-00000a01",
               type="entity", title="Harbor", subject="harbor", kind="projects", destination="harbor",
               high_stakes="the client renews")
    sign.apply(root, {"p-00000a01": "yes"}, by="Ada Okafor", now=NOW)
    page = root / "knowledge/projects/harbor/harbor.md"
    fm, body = pages.read(page)
    qfm, _ = pages.read(root / "_outbound/queue/p-00000a01.md")
    assert fm["signed_fields"] == sign.fields_fingerprint(fm) and qfm["signed_fields"] == sign.fields_fingerprint(qfm)
    log = (root / "log.md").read_text(encoding="utf-8")
    assert f" fields p-00000a01 {fm['signed_fields']} by Ada Okafor" in log
    assert f" fields p-00000a01 {qfm['signed_fields']} by Ada Okafor" in log
    assert errors(root) == []
    for key, value in (("title", "Harbor: contract terms agreed"), ("high_stakes", None),
                       ("signed_by", "Ben Liu"), ("type", "decision")):
        pages.write(page, dict(fm, **{key: value}), body)
        assert any("signed fields" in e for e in errors(root)), key
    pages.write(page, dict(fm, verified_at=pages.iso(NOW), verified_by="Ada Okafor", contested=["p-1"]), body)
    assert not any("signed fields" in e for e in errors(root))


def test_a_release_whose_high_stakes_flag_was_dropped_in_flight_waits(tmp_path, monkeypatch):
    root = home(tmp_path)
    fm = release(root, "p-0000ab01", high_stakes="the client renews")
    path = root / "_inbound/candidates/p-0000ab01.md"
    cur, body = pages.read(path)
    cur.pop("high_stakes")
    pages.write(path, cur, body)   # the digest still names the flag
    logs(monkeypatch, {ADA_REPO: history(fm)})
    report = landing.land(root, now=NOW)
    assert report.silent == [] and report.pending == ["p-0000ab01"]
    assert landing_of(root, "p-0000ab01") == "pending: fingerprint mismatch"
    cur, body = pages.read(path)
    cur.pop("landing")
    cur.pop("for")
    cur["signed_fields"] = sign.fields_fingerprint(cur)   # recomputed: the signer's history lacks it
    pages.write(path, cur, body)
    assert landing.land(root, now=NOW).pending == ["p-0000ab01"]
    assert landing_of(root, "p-0000ab01") == "pending: the signer's history does not show its signed fields"


def test_a_release_signed_before_the_fields_were_bound_waits(tmp_path, monkeypatch):
    root = home(tmp_path)
    fm = release(root, "p-0000ab02")
    path = root / "_inbound/candidates/p-0000ab02.md"
    cur, body = pages.read(path)
    cur.pop("signed_fields")
    pages.write(path, cur, body)
    logs(monkeypatch, {ADA_REPO: history(fm)})
    assert landing.land(root, now=NOW).pending == ["p-0000ab02"]


# ----- 64: what validate refuses beside the pages ----------------------------------------
def test_validate_refuses_a_signed_page_on_the_desk_a_zone_in_another_case_and_an_upper_case_suffix(tmp_path):
    root = make_space(tmp_path / "ada")
    write_page(root, "work/projects/harbor/terms/desk-terms.md", "Terms.\n", status="stable")
    (root / "Docs").mkdir()
    write_page(root, "knowledge/projects/harbor/x.MD", "Unsigned.\n", status="stable")
    found = errors(root)
    assert any(e.startswith("warning: work/projects/harbor/terms/desk-terms.md") and "never signed" in e
               for e in validate.validate(root))
    assert any(e.startswith("Docs/") for e in found)
    assert any("x.MD" in e for e in found)


def test_a_clean_space_stays_clean(tmp_path, monkeypatch):
    root = home(tmp_path)
    fm = release(root, "p-0000ac01")
    logs(monkeypatch, {ADA_REPO: history(fm)})
    assert landing.land(root, now=NOW).silent == ["p-0000ac01"]
    ids.build(root)
    assert errors(root) == []
    row = json.loads((root / "_meta/state/landed.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["target"] == "knowledge/projects/harbor/p-0000ac01.md"


# ----- re-test: what the first fixes left open --------------------------------------------
def signed_here(root, rel, pid, body, signer="Ada Okafor", signed_in="ada"):
    """A page already landed here, signed by `signer` in `signed_in`."""
    fm = dict(id=pid, type="entity", title=f"Harbor ({pid})", status="stable", subject="harbor",
              kind="projects", signed_by=signer, signed_at=pages.iso(NOW), signed_in=signed_in,
              fingerprint=pages.fingerprint(body))
    fm["signed_fields"] = sign.fields_fingerprint(fm)
    return write_page(root, rel, body, **fm)


def test_a_genuine_signature_replayed_over_another_page_of_the_same_signer_waits(tmp_path, monkeypatch):
    # the signature binds an id and a text, not a place: a release that carries page one's
    # genuine signature with page two's path as its target never writes over page two
    root = home(tmp_path)
    one = signed_here(root, "knowledge/projects/harbor/p-0000ba01.md", "p-0000ba01", "Page one.\n")
    two = signed_here(root, "knowledge/projects/harbor/p-0000ba02.md", "p-0000ba02", "The contract terms.\n")
    fm = release(root, "p-0000ba01", body="Page one.\n", target="knowledge/projects/harbor/p-0000ba02.md")
    logs(monkeypatch, {ADA_REPO: history(fm)})
    report = landing.land(root, now=NOW)
    assert report.silent == [] and report.pending == ["p-0000ba01"]
    assert landing_of(root, "p-0000ba01") == ("pending: its id is already the page at "
                                              "knowledge/projects/harbor/p-0000ba01.md")
    assert pages.read(two)[1] == "The contract terms.\n" and pages.read(one)[1] == "Page one.\n"


def test_a_release_aimed_at_a_page_with_another_id_waits(tmp_path, monkeypatch):
    root = home(tmp_path)
    two = signed_here(root, "knowledge/projects/harbor/terms.md", "p-0000ba04", "The contract terms.\n")
    fm = release(root, "p-0000ba03", body="Something else.\n", target="knowledge/projects/harbor/terms.md")
    logs(monkeypatch, {ADA_REPO: history(fm)})
    report = landing.land(root, now=NOW)
    assert report.pending == ["p-0000ba03"]
    assert landing_of(root, "p-0000ba03") == "pending: another page is at its target"
    assert pages.read(two)[1] == "The contract terms.\n"


def test_a_release_never_gives_an_id_a_second_page(tmp_path, monkeypatch):
    root = home(tmp_path)
    signed_here(root, "knowledge/projects/harbor/p-0000ba05.md", "p-0000ba05", "Page.\n")
    fm = release(root, "p-0000ba05", body="Page, changed.\n", target="knowledge/projects/other/p-0000ba05.md")
    logs(monkeypatch, {ADA_REPO: history(fm)})
    assert landing.land(root, now=NOW).pending == ["p-0000ba05"]
    assert not (root / "knowledge/projects/other").exists()


@pytest.mark.parametrize("name", ["INDEX.md", "Index.md", "LOG.md", "Readme.md"])
def test_a_target_naming_a_hub_or_a_log_in_another_case_is_refused(tmp_path, monkeypatch, name):
    root = home(tmp_path)
    hub = root / "knowledge/projects/harbor/index.md"
    hub.parent.mkdir(parents=True, exist_ok=True)
    hub.write_text("hub\n", encoding="utf-8")
    fm = release(root, "p-0000ba06", target=f"knowledge/projects/harbor/{name}")
    logs(monkeypatch, {ADA_REPO: history(fm)})
    assert landing.land(root, now=NOW).pending == ["p-0000ba06"]
    assert landing_of(root, "p-0000ba06").startswith("pending: bad target")
    assert hub.read_text(encoding="utf-8") == "hub\n"
    with pytest.raises(pages.UnsafePath):
        sign.check_target(f"knowledge/projects/harbor/{name}")


def test_a_signed_page_that_names_no_signing_space_counts_as_signed_here(tmp_path, monkeypatch):
    # an older page without `signed_in`: the same name signing in another space is not the
    # same signature, so the change waits for that page's signer
    root = home(tmp_path)
    body = "Old.\n"
    page = write_page(root, "knowledge/projects/harbor/p-0000ba07.md", body, id="p-0000ba07", type="entity",
                      title="H", status="stable", subject="harbor", signed_by="Ben Liu",
                      signed_at=pages.iso(NOW), fingerprint=pages.fingerprint(body))
    fm = release(root, "p-0000ba07", body="New.\n", signer="Ben Liu", sender="ben")
    logs(monkeypatch, {BEN_REPO: history(fm)})
    assert landing.land(root, now=NOW).pending == ["p-0000ba07"]
    assert landing_of(root, "p-0000ba07") == "pending: someone else's signed page"
    assert pages.read(page)[1] == body


def test_a_record_naming_the_id_of_a_record_already_here_never_overwrites_it(tmp_path):
    root = home(tmp_path)
    filed = write_page(root, "knowledge/sources/2026/10/p-0000ba08.md", "The real mail.\n", id="p-0000ba08",
                       type="source", title="A mail", landed_from="ben", at="2026-10-11T09:00:00Z")
    signed = signed_here(root, "knowledge/topics/harbor/p-0000ba09.md", "p-0000ba09", "Signed.\n")
    for pid, stem in (("p-0000ba08", "p-0000ba10"), ("p-0000ba09", "p-0000ba11")):
        write_page(root, f"_inbound/candidates/{stem}.md", "Planted text.\n", id=pid, type="source",
                   title="Another mail", released_from="ada", release_class="at_once", at="2026-10-11T10:00:00Z")
    report = landing.land(root, now=NOW)
    assert report.records == [] and sorted(report.pending) == ["p-0000ba08", "p-0000ba09"]
    assert pages.read(filed)[1] == "The real mail.\n" and pages.read(signed)[1] == "Signed.\n"
    assert not (root / "knowledge/sources/2026/10/p-0000ba09.md").exists()
    assert landing_of(root, "p-0000ba10") == "pending: its id is already a page here"


def test_the_same_record_from_two_releasers_still_lands_once(tmp_path):
    root = home(tmp_path)
    for stem, sender in (("p-0000ba12", "ada"), ("p-0000ba13", "ben")):
        write_page(root, f"_inbound/candidates/{stem}.md", "The mail.\n", id=stem, type="source",
                   title="A mail", released_from=sender, release_class="at_once", at="2026-10-11T10:00:00Z")
    report = landing.land(root, now=NOW)
    assert report.records == ["p-0000ba12"] and report.record_duplicates == ["p-0000ba13"]


def test_the_read_back_says_which_page_a_yes_signs_over(tmp_path):
    root = make_space(tmp_path / "ada")
    write_page(root, "knowledge/projects/harbor/terms.md", "Terms.\n", id="p-0000ba14", type="entity",
               title="Harbor terms", status="stable", signed_by="Ada Okafor", signed_at=pages.iso(NOW),
               fingerprint=pages.fingerprint("Terms.\n"))
    write_page(root, "_inbound/candidates/p-0000ba15.md", "New terms.\n", id="p-0000ba15", type="entity",
               title="Harbor", subject="harbor", kind="projects", target="knowledge/projects/harbor/terms.md")
    back = sign.read_back(root, ["p-0000ba15"])
    assert ("it lands at knowledge/projects/harbor/terms.md, over p-0000ba14 there (Harbor terms), "
            "which keeps its id") in back


def test_a_yes_on_a_held_record_never_writes_over_the_record_with_its_id(tmp_path):
    root = make_space(tmp_path / "ada")
    filed = write_page(root, "knowledge/sources/2026/10/p-0000ba16.md", "The real mail.\n", id="p-0000ba16",
                       type="source", title="A mail", at="2026-10-11T09:00:00Z")
    write_page(root, "_inbound/candidates/p-0000ba17.md", "Planted text.\n", id="p-0000ba16", type="source",
               title="Another mail", released_from="ben", at="2026-10-11T10:00:00Z")
    report = sign.apply(root, {"p-0000ba17": "yes"}, by="Ada Okafor", now=NOW)
    assert [r["id"] for r in report.refused] == ["p-0000ba17"]
    assert "its id is already the page at knowledge/sources/2026/10/p-0000ba16.md" in report.refused[0]["why"]
    assert pages.read(filed)[1] == "The real mail.\n"
    assert (root / "_inbound/candidates/p-0000ba17.md").exists()


def test_a_released_review_batch_never_verifies_or_deprecates_a_page(tmp_path):
    # a review batch names pages here for one verb: another space could pick them
    root = home(tmp_path)
    page = signed_here(root, "knowledge/projects/harbor/p-0000ba18.md", "p-0000ba18", "Signed.\n",
                       signer="Ben Liu", signed_in="harbor")
    write_page(root, "_inbound/candidates/review-2026-10-12.md", "# Weekly review\n", id="p-0000ba19",
               type="review", title="Weekly review", review_batch=True, released_from="ada",
               lines=[{"n": 1, "page": "knowledge/projects/harbor/p-0000ba18.md", "id": "p-0000ba18"}])
    report = landing.land(root, now=NOW)
    assert report.pending == ["review-2026-10-12"]   # a malformed release is named by its file
    assert landing_of(root, "review-2026-10-12") == "pending: malformed release (a review batch is never released)"
    for verdict in ({"p-0000ba19": "no"}, {"review-2026-10-12#1": "no"}, {"review-2026-10-12#1": ["edit", "Forged."]}):
        report = sign.apply(root, verdict, by="Ben Liu", now=NOW)
        assert report.discarded == [] and report.edited == [] and report.refused, verdict
    fm, body = pages.read(page)
    assert fm["status"] == "stable" and body == "Signed.\n" and "verified_by" not in fm
