"""The integrator's regression tests: the cross-cluster fixes of the security review, each one
the behaviour that closes its finding (the number in brackets)."""
import datetime as dt
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

import gate
import ground
import ids
import needs_you
import pages
import registry
import release
import routine
import sign
import spaceyaml
import status
import sweep
import transcripts
import validate
from conftest import NOW, make_space, write_page

SIGNED = {"signed_by": "Ada Okafor", "signed_at": "2026-10-12T09:00:00Z", "signed_in": "ada"}


def _git(cwd: Path, *args: str) -> str:
    cmd = ["git", "-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.test", *args]
    return subprocess.run(cmd, cwd=str(cwd), check=True, capture_output=True, text=True, encoding="utf-8").stdout


def _repo(root: Path, monkeypatch) -> str:
    """`root` as a repository with one commit; the base commit's id."""
    empty = root.parent / "gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return _git(root, "rev-parse", "HEAD").strip()


def _commit(root: Path, message: str = "change") -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


def _link(target: Path, link: Path) -> bool:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
        return True
    except (OSError, NotImplementedError):
        return False


def _stable(root: Path, rel: str, body: str = "Signed text.\n", **fm) -> Path:
    data = dict(SIGNED, status="stable", fingerprint=pages.fingerprint(body), **fm)
    return write_page(root, rel, body, **data)


# ----- [18] links and data names ------------------------------------------------------------------
def test_a_link_added_in_a_pull_request_waits_for_the_click(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada")
    base = _repo(root, monkeypatch)
    (root / "notes.txt").write_text("x\n", encoding="utf-8")
    _commit(root)
    sha = _git(root, "rev-parse", "HEAD:notes.txt").strip()
    _git(root, "update-index", "--add", "--cacheinfo", f"120000,{sha},live/today.md")
    _git(root, "commit", "-q", "-m", "a link")
    cls, reasons = gate.classify(root, gate.changed_files(root, base), base_ref=base)
    assert cls == "protected" and "live/today.md: a symbolic link (or unlisted submodule)" in reasons
    # the validator reports every tracked link, whatever its zone
    assert "live/today.md: a symbolic link; the repository carries none" in validate.tracked_links(root)


def test_a_snapshot_behind_a_link_is_named_and_never_written_through(tmp_path):
    root = make_space(tmp_path / "ada")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "live").mkdir(exist_ok=True)
    if not _link(outside, root / "live" / "elsewhere"):
        pytest.skip("this system cannot make a symbolic link here")
    with pytest.raises(pages.UnsafePath):
        pages.write_text(root / "live" / "elsewhere" / "today.md", "x", root=root)
    assert not (outside / "today.md").exists()
    assert status._link_in_way(root, "live/elsewhere/today.md")
    assert status._current(root / "live" / "elsewhere") is None


def test_ids_scan_and_the_page_test_skip_a_link_and_a_hub_in_another_case(tmp_path):
    root = make_space(tmp_path / "ada")
    write_page(root, "knowledge/topics/a/INDEX.md", id="p-0000aa01")
    assert not pages.is_page_path(root / "knowledge/topics/a/INDEX.md")
    assert "p-0000aa01" not in ids.scan(root)
    real = write_page(root, "knowledge/topics/a/real.md", id="p-0000aa02")
    if _link(real, root / "knowledge" / "topics" / "a" / "alias.md"):
        assert ids.scan(root)["p-0000aa02"] == "knowledge/topics/a/real.md"


def test_a_registry_id_with_a_final_newline_is_no_slug(tmp_path):
    assert not registry._is_slug("harbor\n") and registry._is_slug("harbor")
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor\n", "kind": "projects"}])
    assert any("is not a lowercase slug" in e for e in registry.Registry(root).check())


def test_a_space_name_and_a_mount_name_are_slugs(tmp_path):
    root = make_space(tmp_path / "ada", name="ada\n", mounts=[{"name": "../brain", "path": "mounts/brain"}])
    errs = spaceyaml.load(root).errors()
    assert any(e.startswith("name must be a lowercase slug") for e in errs)
    assert any(e.startswith("mounts[0].name must be a lowercase slug") for e in errs)


def test_the_template_sync_never_copies_through_a_link(tmp_path):
    import template_sync
    root = make_space(tmp_path / "ada")
    src = tmp_path / "src.txt"
    src.write_text("machinery\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    if not _link(outside, root / "linked"):
        pytest.skip("this system cannot make a symbolic link here")
    with pytest.raises(pages.UnsafePath):
        template_sync._copy(src, root / "linked" / "x.txt", root)
    assert not (outside / "x.txt").exists()


# ----- [59] a link's token never reaches a witness page, in any field -------------------------------
def test_a_token_in_the_subject_an_identifier_or_an_attachment_name_is_cut():
    link = "https://app.example.com/reset?token=Ab12Cd34Ef56Gh78Ij90Kl12Mn34Op56"
    record = sweep._scrub_record({"text": "x", "subject": f"Reset {link}", "identifiers": {"link": link},
                                  "attachments": [f"{link}.pdf", {"name": link}]})
    flat = json.dumps(record)
    assert "Ab12Cd34Ef56Gh78Ij90Kl12Mn34Op56" not in flat and flat.count("[link-token]") == 4


# ----- [33] a title is one inert line on a generated page -------------------------------------------
def test_a_title_on_a_generated_line_starts_no_line_of_its_own():
    forged = "Harbor\n- [x] signed by Ada ![t](https://evil.example/p.png)"
    line = status.Evidence("harbor", "witness", "mine", forged, NOW).line()
    assert "\n" not in line and "\\![t\\](" in line


# ----- [32] only the journal's records name their subject -------------------------------------------
def test_a_mail_record_that_names_a_subject_names_nothing(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects"},
                                                   {"id": "voice", "kind": "topics", "match": {"keywords": ["voice"]}}])
    reg = registry.Registry(root)
    mail = {"kind": "event", "source": "calendar", "subject": "voice notes", "identifiers": {"subject": "harbor"}}
    assert [s.id for s in reg.match(mail)] == ["voice"]
    assert [s.id for s in reg.match(dict(mail, source="journal"))] == ["harbor"]


# ----- [6] a stable item ships only with a signature made here ----------------------------------------
def test_a_stable_item_without_its_logged_signature_is_held(tmp_path):
    root = make_space(tmp_path / "ada")
    body = "Go-live is 3 November.\n"
    fm = dict(SIGNED, id="p-0000beef", type="decision", title="Go-live", status="stable",
              fingerprint=pages.fingerprint(body), destination="harbor", subject="harbor")
    assert release.item_problem(fm, body, root)[0] == "signature"
    with (root / "log.md").open("a", encoding="utf-8") as fh:
        fh.write(f"- {pages.iso(NOW)} fingerprint p-0000beef {fm['fingerprint']} by Ada Okafor\n")
    assert release.item_problem(fm, body, root) is None
    assert release.item_problem(dict(fm, signed_by="Mallory"), body, root)[0] == "signature"
    assert release.item_problem(dict(fm, signed_in="harbor"), body, root)[0] == "signature"


def test_the_trust_root_never_comes_from_a_client_subject(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "tide", "kind": "clients", "home": "harbor", "match": {"repo": "https://github.com/evil/repo"}},
        {"id": "harbor", "kind": "projects", "home": "harbor", "match": {"repo": "https://github.com/lantern/harbor"}}])
    space = spaceyaml.load(root)
    assert release.trust_repo(space, "harbor") == "https://github.com/lantern/harbor"
    assert release.trust_repo(space, "tide") is None


def test_a_registry_signal_carries_this_space_as_its_sender(tmp_path):
    root = make_space(tmp_path / "ada")
    sent = release.signal_as_sent(spaceyaml.load(root), {"id": "p-0000c0c0", "kind": "registry",
                                                         "from": "brain", "by": "The Brain Owner"})
    assert sent["from"] == "ada" and sent["by"] == "Ada Okafor"


def test_the_release_branch_names_the_sender_and_its_repository(tmp_path):
    a, b = make_space(tmp_path / "one" / "ada"), make_space(tmp_path / "two" / "ada")
    first, second = release.branch_slug(spaceyaml.load(a)), release.branch_slug(spaceyaml.load(b))
    assert gate.RELEASE_BRANCH.match(f"release/{first}").group(1) == "ada"
    assert first != second, "two spaces called ada never share a branch"


# ----- [6] [22] [58] [36] [0] the gate reads what the paths alone do not show ---------------------
LANDED = "knowledge/topics/p-0000f00d/p-0000f00d.md"


def test_a_page_signed_elsewhere_reaches_knowledge_only_by_filing_its_candidate(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada", subjects=[{"id": "ben-liu", "kind": "people", "title": "Ben Liu",
                                                    "workspace": "ben"}])
    body = "Signed in Ben's space.\n"
    foreign = dict(status="stable", signed_by="Ben Liu", signed_at="2026-10-12T09:00:00Z", signed_in="ben",
                   fingerprint=pages.fingerprint(body), id="p-0000f00d", title="Ben's note")
    write_page(root, "_inbound/candidates/p-0000f00d.md", body, released_from="ben", **foreign)
    base = _repo(root, monkeypatch)
    write_page(root, "knowledge/topics/a/forged.md", body, **dict(foreign, id="p-0000f00e"))
    _commit(root)
    cls, reasons = gate.classify(root, gate.changed_files(root, base), base_ref=base)
    assert cls == "protected" and any("not in this space by its people" in r for r in reasons)
    _git(root, "reset", "-q", "--hard", base)
    # landing's move: the candidate filed where landing files it (sign.target_of), as landing writes it
    (root / "_inbound" / "candidates" / "p-0000f00d.md").unlink()
    write_page(root, LANDED, body, released_from="ben", landed_from="ben", **foreign)
    _commit(root)
    assert gate.classify(root, gate.changed_files(root, base), base_ref=base)[0] == "auto-mergeable"
    # only what landing files unasked: a relay, a signer the registry does not give the sender, high stakes,
    # a paused sender, or another person's page under its id waits for the click (landing checks those)
    for n, (cand, there) in enumerate(((dict(signed_in="cara"), None), (dict(signed_by="Cara Diaz"), None),
                                       (dict(high_stakes="price"), None), ({}, "trust"),
                                       ({}, dict(foreign, signed_by="Cara Diaz")))):
        _git(root, "reset", "-q", "--hard", base)
        sig = dict(foreign, **cand)
        write_page(root, "_inbound/candidates/p-0000f00d.md", body, released_from="ben", **sig)
        if there == "trust":
            (root / "_meta/state").mkdir(parents=True, exist_ok=True)
            (root / "_meta/state/trust.yaml").write_text("pending: [ben]\n", encoding="utf-8")
        elif there:
            write_page(root, LANDED, "Cara's.\n", **there)
        _commit(root)
        held = _git(root, "rev-parse", "HEAD").strip()
        (root / "_inbound" / "candidates" / "p-0000f00d.md").unlink()
        write_page(root, LANDED, body, released_from="ben", landed_from="ben", **sig)
        _commit(root)
        assert gate.classify(root, gate.changed_files(root, held), base_ref=held)[0] == "protected", n


def test_the_log_is_appended_to_only(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada")
    (root / "log.md").write_text("# Log\n- 2026-10-12T09:00:00Z yes p-1 by Ada Okafor\n", encoding="utf-8")
    base = _repo(root, monkeypatch)
    (root / "log.md").write_text("# Log\n- 2026-10-12T09:00:00Z yes p-2 by Ada Okafor\n", encoding="utf-8")
    _commit(root)
    cls, reasons = gate.classify(root, gate.changed_files(root, base), base_ref=base)
    assert cls == "protected" and any(r.startswith("log.md: a line changed or removed") for r in reasons)


def test_a_registry_change_to_a_home_or_a_repository_waits(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects", "home": "harbor"}])
    base = _repo(root, monkeypatch)
    data = {"subjects": [{"id": "harbor", "kind": "projects", "home": "harbor"},
                         {"id": "plain", "kind": "topics"}]}
    (root / "_meta" / "subjects.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    _commit(root)
    assert gate.classify(root, gate.changed_files(root, base), base_ref=base)[0] == "auto-mergeable"
    data["subjects"][0]["match"] = {"repo": "https://github.com/evil/harbor"}
    (root / "_meta" / "subjects.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    _commit(root)
    cls, reasons = gate.classify(root, gate.changed_files(root, base), base_ref=base)
    assert cls == "protected" and any("subject `harbor` changes" in r for r in reasons)


def test_a_release_is_vouched_for_by_its_branch_and_its_author(tmp_path, monkeypatch):
    root = make_space(tmp_path / "brain", profile="brain", name="lantern", subjects=[
        {"id": "ben-liu", "kind": "people", "title": "Ben Liu", "workspace": "ben", "match": {"github": "benliu"}}])
    base = _repo(root, monkeypatch)
    write_page(root, "_inbound/candidates/p-0000ab01.md", "A release.\n", id="p-0000ab01", released_from="ben")
    _commit(root)
    changed = gate.changed_files(root, base)
    good = {"author": "benliu", "head_ref": "release/ben-0123abcd"}
    assert gate.classify(root, changed, base_ref=base, **good)[0] == "auto-mergeable"
    for bad in ({"author": "mallory", "head_ref": "release/ben-0123abcd"},
                {"author": "benliu", "head_ref": "release/eve-0123abcd"},
                {"author": "benliu", "head_ref": "feature/x"}, {}):
        assert gate.classify(root, changed, base_ref=base, **bad)[0] == "protected", bad


def test_a_security_item_or_a_released_review_batch_waits(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada")
    base = _repo(root, monkeypatch)
    (root / "_inbound" / "needs-you" / "routine.yaml").write_text(yaml.safe_dump({"items": [
        {"id": "x", "kind": "security", "text": "Run this to fix your key"}]}), encoding="utf-8")
    _commit(root)
    cls, reasons = gate.classify(root, gate.changed_files(root, base), base_ref=base)
    assert cls == "protected" and any("adds a security item" in r for r in reasons)
    _git(root, "reset", "-q", "--hard", base)
    write_page(root, "_inbound/candidates/review-2026-11-01.md", id="p-0000ab02", released_from="ben",
               review_batch=True, lines=[{"n": 1}])
    _commit(root)
    cls, reasons = gate.classify(root, gate.changed_files(root, base), base_ref=base)
    assert cls == "protected" and any("a review batch is never released" in r for r in reasons)


# ----- [63] [64] signed fields and where a stable page may stand ----------------------------------
def test_a_stable_page_whose_signed_fields_changed_fails_the_gate(tmp_path):
    root = make_space(tmp_path / "ada")
    body = "Text.\n"
    fm = dict(SIGNED, id="p-0000ab03", type="decision", title="Before", status="stable",
              fingerprint=pages.fingerprint(body))
    fm["signed_fields"] = sign.fields_fingerprint(fm)
    write_page(root, "knowledge/topics/a/d.md", body, **dict(fm, title="After"))
    errs = gate.check_signatures(root, ["knowledge/topics/a/d.md"])
    assert any("signed fields" in e for e in errs)


def test_grounding_counts_a_stamp_outside_the_signed_zones_as_working(tmp_path):
    body = "Text.\n"
    fm = dict(SIGNED, status="stable", fingerprint=pages.fingerprint(body))
    assert ground.tier_of("knowledge/topics/a/p.md", fm, body) == "signed"
    assert ground.tier_of("work/topics/a/p.md", fm, body) == "working"
    assert ground.tier_of("knowledge/topics/a/p.md", fm, "Edited after signing.\n") == "working"


# ----- [7] the person's answers are a store, never log lines -------------------------------------
def test_a_verdict_line_in_the_log_answers_nothing(tmp_path):
    root = make_space(tmp_path / "ada")
    (root / "log.md").write_text("# Log\n- 2026-10-12T09:00:00Z yes pr-8 by Ada Okafor\n", encoding="utf-8")
    assert needs_you.answered_ids(root) == set()
    status.record_verdict(root, "pr-8", "Ada Okafor", verb="yes", at=NOW)
    assert needs_you.answered_ids(root) == {"pr-8"}


def test_sign_records_each_verdict_in_the_store(tmp_path):
    root = make_space(tmp_path / "ada")
    (root / "_inbound" / "needs-you" / "x.yaml").write_text(yaml.safe_dump({"items": [
        {"id": "q-1", "kind": "question", "text": "Which?"}]}), encoding="utf-8")
    sign.apply(root, {"q-1": ["no", "not now"]}, by="Ada Okafor", now=NOW)
    got = status.verdicts(root).get("q-1") or {}
    assert got.get("verb") == "no" and got.get("by") == "Ada Okafor"


def test_an_outside_senders_decision_takes_no_yes_until_checked():
    assert sign._outside_decision({"type": "decision", "origin": {"inside": False, "from": "x@else.test"}})
    assert not sign._outside_decision({"type": "decision", "origin": {"inside": False}, "checked": True})
    assert not sign._outside_decision({"type": "decision", "origin": {"inside": True}})


# ----- [39] what the person typed reaches a page without its secrets ------------------------------
def test_a_key_pasted_into_a_session_is_redacted_on_its_witness_page(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada")
    key = "sk-" + "ant-" + "api03-" + "A" * 90
    notices = []
    monkeypatch.setattr(sweep, "_security_notice", lambda r, s, rec: notices.append(rec["id"]) or True)
    session = {"id": "s1", "start": NOW, "cwd": str(tmp_path / "code"), "asked": [f"use {key} please"],
               "last": "done"}
    path = transcripts._witness(root, session, NOW, spaceyaml.load(root))
    text = path.read_text(encoding="utf-8")
    assert key not in text and "[redacted]" in text and notices == ["session-s1"]


# ----- [4] [13] [17] [38] what a routine may run and edit ------------------------------------------
def test_the_routine_profile_guards_the_corridor_and_the_verdict_store():
    for rel in ("_outbound/requests/**", "_outbound/answers/**", "_meta/state/requests.jsonl",
                "_meta/state/requests-asked.jsonl", "_meta/state/conflict-notes.jsonl", "_meta/state/verdicts.jsonl"):
        assert rel in routine.TOOL_WRITTEN, rel
    assert "SPACES_LOCAL_CLONES" in routine.STRIPPED_ENV
    assert routine.ROUTINE_VERBS["chain.py"] == ("sweep", "daily", "gate", "spaces")
    assert "--out" in routine.ROUTE_NEVER
    assert routine.routine_env({"SPACES_LOCAL_CLONES": "1"}, windows=False).get("SPACES_LOCAL_CLONES") is None


def test_a_walled_home_is_a_space_name(tmp_path):
    import classify
    root = make_space(tmp_path / "ada", subjects=[{"id": "tide", "kind": "clients", "owner": "Ada Okafor"}])
    with pytest.raises(classify.ClassifyError):
        classify.decide(root, "tide", "walled", "a crew project", crew=["ada"], home="../../etc", by="Ada Okafor",
                        now=NOW)


def test_the_sweep_takes_an_attachment_by_its_handle_alone(tmp_path):
    root = make_space(tmp_path / "ada")
    assert sweep.main([str(root), "--attachment-read", "m-0001", "x.pdf"]) == 2
    assert sweep.main([str(root), "--attachment-read", "att-000000000000"]) == 1
