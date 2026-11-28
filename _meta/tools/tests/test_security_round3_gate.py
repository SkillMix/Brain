"""Round 3, gate group: the agents get their upkeep back and every guard keeps its property.
The template's machinery lands on green (the release tag is the human gate), the registry holds
only what points trust somewhere new, log.md accepts a union merge, an older release branch and
an older yes still count for one cycle, a space following main moves forwards on its own, and an
approved template release still lands in a space with no further click."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

import gate
import spaceyaml
import template_sync
from conftest import make_space, write_page



_KEY_SIGNED = gate._key_signed   # the real tie, for the one test of its key filter here


@pytest.fixture(autouse=True)
def _computer_signed(monkeypatch):
    """These tests are about the login tie; the computer's key tie has its own (test_signing_key.py)."""
    monkeypatch.setattr(gate, "_key_signed", lambda *args: True)


REPO = Path(__file__).resolve().parents[3]
PROV = "_meta/state/template-version.yaml"
REGISTRY = "_meta/subjects.yaml"
# Every space runs these tests in its checks, but template-sync never carries SECURITY.md or
# .github/CODEOWNERS into one: a test that reads them runs in the template only.
template_only = pytest.mark.skipif(not spaceyaml.is_template_repo(REPO), reason="reads a file only the template has")


def _git(cwd: Path, *args: str, date: str | None = None) -> str:
    env = dict(os.environ)
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    cmd = ["git", "-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.test", *args]
    return subprocess.run(cmd, cwd=str(cwd), check=True, capture_output=True, text=True, encoding="utf-8",
                          env=env).stdout.strip()


@pytest.fixture(autouse=True)
def quiet_git(tmp_path, monkeypatch):
    empty = tmp_path / "gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _init(root: Path) -> str:
    _git(root, "init", "-q", "-b", "main")
    return _commit(root, "base")


def _commit(root: Path, message: str = "change") -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--allow-empty", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _gate(root: Path, base: str, **kw):
    return gate.classify(root, gate.changed_files(root, base), base_ref=base, **kw)


def _registry(root: Path, subjects: list) -> None:
    (root / REGISTRY).write_text(yaml.safe_dump({"subjects": subjects}, sort_keys=False), encoding="utf-8")


# ----- 1. the template: machinery lands on green; the release tag is the human gate -------------------------
def test_a_machinery_pull_request_to_the_template_lands_on_green(tmp_path, monkeypatch):
    monkeypatch.setenv(spaceyaml.TEMPLATE_REPO_ENV, "true")
    root = make_space(tmp_path / "t", profile="template")
    (root / "_meta" / "tools").mkdir(parents=True, exist_ok=True)
    (root / "_meta" / "tools" / "sweep.py").write_text("print('v1')\n", encoding="utf-8")
    base = _init(root)
    for rel, text in (("_meta/tools/sweep.py", "print('v2')\n"), (".github/workflows/checks.yml", "name: c\n"),
                      ("_meta/migrations/0009-x.py", "pass\n"), ("_meta/tests/behaviour/x-1.yaml", "a: 1\n"),
                      (".claude/skills/sweep/SKILL.md", "# sweep\n"), (".claude/settings.json", "{}\n"),
                      ("_meta/templates/AGENTS.md.tpl", "# t\n"), ("_meta/tools/tests/conftest.py", "pass\n")):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    _commit(root)
    cls, reasons = _gate(root, base, author="agent-bot", head_ref="fix/sweep")
    assert cls == "auto-mergeable", reasons
    assert reasons == ["template repository: lands on green; the release tag is the human gate"]


def test_the_template_still_holds_a_case_twin_and_an_odd_name(tmp_path, monkeypatch):
    monkeypatch.setenv(spaceyaml.TEMPLATE_REPO_ENV, "true")
    root = make_space(tmp_path / "t", profile="template")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "notes.md").write_text("a\n", encoding="utf-8")
    base = _init(root)
    # through the index: a checkout that ignores case cannot hold both spellings in its folder
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=str(root), input="b\n",
                          capture_output=True, text=True, check=True).stdout.strip()
    _git(root, "update-index", "--add", "--cacheinfo", f"100644,{blob},docs/Notes.md")
    _git(root, "commit", "-q", "-m", "twin")
    cls, reasons = _gate(root, base)
    assert cls == "protected" and "maintainer" in reasons[0] and any("case is ignored" in r for r in reasons)
    assert gate.classify(root, ["_meta/tools./sync.py"])[0] == "protected"


def test_a_link_in_the_template_still_waits(tmp_path, monkeypatch):
    monkeypatch.setenv(spaceyaml.TEMPLATE_REPO_ENV, "true")
    root = make_space(tmp_path / "t", profile="template")
    base = _init(root)
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=str(root), input="../../etc\n",
                          capture_output=True, text=True, check=True).stdout.strip()
    _git(root, "update-index", "--add", "--cacheinfo", f"120000,{blob},_meta/tools/evil")
    _git(root, "commit", "-q", "-m", "link")
    cls, reasons = _gate(root, base)
    assert cls == "protected" and any("symbolic link" in r for r in reasons)


def test_following_main_is_documented_as_dev_only():
    assert "dev-only mode" in " ".join((template_sync.__doc__ or "").split())
    assert "release tag is the human gate" in " ".join(gate.__doc__.split()) \
        or "the one human gate on what reaches the spaces is the release tag" in " ".join(gate.__doc__.split())


@template_only
def test_security_md_documents_following_main_as_dev_only():
    text = " ".join((REPO / "SECURITY.md").read_text(encoding="utf-8").split())
    assert "`template_updates: main`" in text and "dev-only mode" in text


# ----- 2. the registry holds only what points trust somewhere new ---------------------------------------------
def _personal(tmp_path, subjects) -> tuple[Path, str]:
    root = make_space(tmp_path / "ada", subjects=subjects,
                      mounts=[{"name": "brain", "repo": "https://github.com/northwind/brain", "path": "brain"},
                              {"name": "harbor", "repo": "https://github.com/northwind/harbor",
                               "path": "mounts/harbor"}])
    return root, _init(root)


def test_a_classification_homing_a_subject_in_a_known_space_lands(tmp_path):
    # the brain's space is called `northwind`; its mount here is `brain`, and the base registry already homes
    # a subject there, as every company classification writes it
    rows = [{"id": "lantern", "kind": "clients", "home": "northwind",
             "classification": {"class": "company", "state": "settled", "home": "northwind"}},
            {"id": "tide", "kind": "clients"}, {"id": "reef", "kind": "projects"}, {"id": "kelp", "kind": "deals"}]
    root, base = _personal(tmp_path, rows)
    rows[1].update(home="northwind", classification={"class": "company", "state": "settled", "home": "northwind"})
    rows[2].update(home="harbor", classification={"class": "walled", "state": "settled", "home": "harbor",
                                                  "crew": ["ada-okafor"]})
    rows[3].update(home="ada")
    rows.append({"id": "pier", "kind": "clients", "home": "northwind"})
    _registry(root, rows)
    _commit(root)
    cls, reasons = _gate(root, base)
    assert cls == "auto-mergeable", reasons


def test_a_home_naming_an_unknown_space_or_a_repository_still_waits(tmp_path):
    rows = [{"id": "tide", "kind": "clients"}, {"id": "reef", "kind": "projects", "home": "harbor"}]
    root, base = _personal(tmp_path, rows)
    for change in ({"home": "elsewhere"}, {"classification": {"class": "walled", "home": "elsewhere"}},
                   {"match": {"repo": "https://github.com/example-test/tide"}}):
        _registry(root, [dict(rows[0], **change), rows[1]])
        _commit(root)
        cls, reasons = _gate(root, base)
        assert cls == "protected" and any("subject `tide` changes" in r for r in reasons), (change, reasons)
        _git(root, "reset", "-q", "--hard", base)
    # a home removed, and a new subject that brings its own repository
    _registry(root, [rows[0], {"id": "reef", "kind": "projects"}])
    _commit(root)
    assert _gate(root, base)[0] == "protected"
    _git(root, "reset", "-q", "--hard", base)
    _registry(root, [*rows, {"id": "new", "kind": "projects", "match": {"repo": "https://github.com/e/x"}}])
    _commit(root)
    assert _gate(root, base)[0] == "protected"


def test_repo_owner_is_no_trust_field(tmp_path):
    rows = [{"id": "tide", "kind": "clients"}]
    root, base = _personal(tmp_path, rows)
    _registry(root, [{"id": "tide", "kind": "clients", "match": {"repo_owner": "example-test"}}])
    _commit(root)
    assert _gate(root, base)[0] == "auto-mergeable"


def _brain(tmp_path, subjects, invited=None) -> tuple[Path, str]:
    root = make_space(tmp_path / "brain", profile="brain", name="northwind", owner="Ada Okafor", subjects=subjects)
    for slug, login in (invited or {}).items():
        path = root / "_meta" / "state" / "invitations" / f"{slug}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({"name": slug, "slug": slug, **({"login": login} if login else {})}),
                        encoding="utf-8")
    return root, _init(root)


ADA = {"id": "ada-okafor", "kind": "people", "title": "Ada Okafor", "workspace": "ada",
       "match": {"github": "adaokafor"}}
BEN = {"id": "ben-liu", "kind": "people", "title": "Ben Liu"}
CARA = {"id": "cara-diaz", "kind": "people", "title": "Cara Diaz"}


def test_an_invited_person_fills_their_own_workspace_and_login_once(tmp_path):
    root, base = _brain(tmp_path, [ADA, BEN, CARA], invited={"ben-liu": "benliu"})
    filled = dict(BEN, workspace="ben-liu", workspace_repo="https://github.com/benliu/ben-workspace",
                  match={"github": "benliu"})
    _registry(root, [ADA, filled, CARA])
    _commit(root)
    cls, reasons = _gate(root, base, author="benliu")
    assert cls == "auto-mergeable", reasons
    # the same fill opened by anyone else waits
    for author in ("cara", "mallory", None):
        assert _gate(root, base, author=author)[0] == "protected", author
    # a person nobody invited is never claimed by a login that sets itself
    _git(root, "reset", "-q", "--hard", base)
    _registry(root, [ADA, BEN, dict(CARA, workspace="cara-diaz", match={"github": "benliu"})])
    _commit(root)
    assert _gate(root, base, author="benliu")[0] == "protected"


def test_an_invitation_naming_a_login_binds_the_claim(tmp_path):
    root, base = _brain(tmp_path, [ADA, BEN], invited={"ben-liu": "benliu"})
    for login, cls in (("benliu", "auto-mergeable"), ("mallory", "protected")):
        _registry(root, [ADA, dict(BEN, workspace="ben-liu", match={"github": login})])
        _commit(root)
        assert _gate(root, base, author=login)[0] == cls, login
        _git(root, "reset", "-q", "--hard", base)


def test_a_registered_login_fills_the_rest_of_its_own_row(tmp_path):
    ben = dict(BEN, match={"github": "benliu"})
    root, base = _brain(tmp_path, [ADA, ben, CARA])
    _registry(root, [ADA, dict(ben, workspace="ben-liu", workspace_repo="https://github.com/benliu/ws"), CARA])
    _commit(root)
    assert _gate(root, base, author="benliu")[0] == "auto-mergeable"
    assert _gate(root, base, author="cara")[0] == "protected"


def test_a_value_that_was_there_changes_only_with_the_click(tmp_path):
    ben = dict(BEN, workspace="ben", match={"github": "benliu"})
    root, base = _brain(tmp_path, [ADA, ben, CARA])
    for change in ({"workspace": "ben-two"}, {"match": {"github": "benliu2"}}, {"workspace": None},
                   {"match": {"github": ["benliu", "other"]}}):
        _registry(root, [ADA, {k: v for k, v in dict(ben, **change).items() if v is not None}, CARA])
        _commit(root)
        assert _gate(root, base, author="benliu")[0] == "protected", change
        _git(root, "reset", "-q", "--hard", base)


def test_a_fill_never_takes_a_name_a_workspace_or_a_login_another_row_has(tmp_path):
    invited = {"ben-liu": "benliu", "ben-liu-2": "benliu"}
    for n, (base_rows, head_rows) in enumerate([
            ([ADA, BEN], [ADA, dict(BEN, workspace="ada", match={"github": "benliu"})]),      # Ada's workspace
            ([ADA, BEN], [ADA, dict(BEN, match={"github": "adaokafor"})]),                     # Ada's login
            ([ADA, BEN, dict(BEN, id="ben-liu-2")],                                            # two rows, one name
             [ADA, dict(BEN, match={"github": "benliu"}), dict(BEN, id="ben-liu-2")])]):
        root, base = _brain(tmp_path / str(n), base_rows, invited=invited)
        _registry(root, head_rows)
        _commit(root)
        author = "adaokafor" if n == 1 else "benliu"
        assert _gate(root, base, author=author)[0] == "protected", head_rows


def test_the_owners_own_row_is_never_a_self_fill(tmp_path):
    ada = {k: v for k, v in ADA.items() if k not in ("match", "workspace")}
    root, base = _brain(tmp_path, [ada, BEN], invited={"ada-okafor": "mallory"})
    _registry(root, [dict(ada, match={"github": "mallory"}), BEN])
    _commit(root)
    assert _gate(root, base, author="mallory")[0] == "protected"


def test_a_person_with_a_login_never_changes_name_on_green(tmp_path):
    ben = dict(BEN, match={"github": "benliu"})
    root, base = _brain(tmp_path, [ADA, ben])
    _registry(root, [ADA, dict(ben, title="Ada Okafor")])
    _commit(root)
    assert _gate(root, base, author="adaokafor")[0] == "protected"


def test_a_registered_colleague_lands_upkeep_in_the_brain(tmp_path):
    ben = dict(BEN, workspace="ben", match={"github": "benliu"})
    tide = {"id": "tide", "kind": "clients", "home": "northwind", "owner": "Ada Okafor"}
    root, base = _brain(tmp_path, [ADA, ben, tide])
    _registry(root, [ADA, ben, dict(tide, summary="Weekly call moved to Tuesday"),
                     {"id": "reef", "kind": "projects", "home": "northwind"}, {"id": "kelp", "kind": "topics"}])
    _commit(root)
    cls, reasons = _gate(root, base, author="benliu")
    assert cls == "auto-mergeable", reasons
    assert _gate(root, base, author="adaokafor")[0] == "auto-mergeable"
    # a login the registry gives no person may not
    assert _gate(root, base, author="mallory")[0] == "protected"


@pytest.mark.parametrize("change", [
    {"owner": "Ben Liu"}, {"crew": ["ben-liu"]}, {"part_of": "kelp"}, {"title": "Tide Ltd"},
    {"classification": {"class": "walled", "home": "northwind", "crew": ["ben-liu"]}},
    {"match": {"domain": "example.test"}}])
def test_a_colleague_changes_no_owner_field_of_another_subject(tmp_path, change):
    ben = dict(BEN, workspace="ben", match={"github": "benliu"})
    tide = {"id": "tide", "kind": "clients", "home": "northwind", "owner": "Ada Okafor"}
    root, base = _brain(tmp_path, [ADA, ben, tide, {"id": "kelp", "kind": "topics"}])
    _registry(root, [ADA, ben, dict(tide, **change), {"id": "kelp", "kind": "topics"}])
    _commit(root)
    cls, reasons = _gate(root, base, author="benliu")
    assert cls == "protected" and any("not its owner" in r for r in reasons), reasons
    assert _gate(root, base, author="adaokafor")[0] == "auto-mergeable"


def test_a_colleague_adds_no_person_and_removes_no_subject(tmp_path):
    ben = dict(BEN, workspace="ben", match={"github": "benliu"})
    root, base = _brain(tmp_path, [ADA, ben, {"id": "tide", "kind": "clients"}])
    for rows in ([ADA, ben, {"id": "tide", "kind": "clients"}, CARA], [ADA, ben],
                 [ADA, dict(ben, state="archived"), {"id": "tide", "kind": "clients"}]):
        _registry(root, rows)
        _commit(root)
        assert _gate(root, base, author="benliu")[0] == "protected", rows
        _git(root, "reset", "-q", "--hard", base)


# ----- 3. log.md: no line removed or changed, a union merge accepted ------------------------------------------
def test_a_union_merge_of_the_log_is_appended_only(tmp_path):
    root = make_space(tmp_path / "ada")
    (root / ".gitattributes").write_text("log.md merge=union\n", encoding="utf-8")
    (root / "log.md").write_text("# Log\n- 2026-11-01T09:00:00Z run sweep\n", encoding="utf-8")
    base = _init(root)
    # the default branch moves on: another computer appended first
    with (root / "log.md").open("a", encoding="utf-8") as fh:
        fh.write("- 2026-11-02T09:00:00Z run sweep (cloud)\n")
    tip = _commit(root, "cloud")
    # this computer's branch, from the older base, appends its own line, then merges the default branch
    _git(root, "checkout", "-q", "-b", "sync/m-desk", base)
    with (root / "log.md").open("a", encoding="utf-8") as fh:
        fh.write("- 2026-11-02T10:00:00Z run sweep (desk)\n")
    _commit(root, "desk")
    _git(root, "merge", "-q", "--no-edit", "main")
    head = (root / "log.md").read_text(encoding="utf-8")
    tip_log = _git(root, "show", f"{tip}:log.md") + "\n"
    assert not head.startswith(tip_log)          # union wrote this side's line first
    assert gate.appended_only(tip_log, head)
    cls, reasons = _gate(root, "main")
    assert not any(r.startswith("log.md") for r in reasons), reasons
    assert cls == "auto-mergeable", reasons


@pytest.mark.parametrize("old,new,ok", [
    ("a\nb\n", "a\nb\nc\n", True), ("a\nb\n", "a\nx\nb\n", True), ("a\nb\n", "a\nc\n", False),
    ("a\nb\n", "b\na\n", False), ("a\nb\n", "a\n", False), ("a\nb", "a\nb- c\n", True), ("", "x\n", True),
    ("a\r\nb\r\n", "a\nb\nc\n", True)])
def test_appended_only_is_an_ordered_subsequence(old, new, ok):
    assert gate.appended_only(old, new) is ok


# ----- 4. a release on the older branch name, for one cycle ---------------------------------------------------
def test_a_release_on_the_older_branch_name_lands_for_its_sender(tmp_path):
    ben = dict(BEN, workspace="ben", match={"github": "benliu"})
    root, base = _brain(tmp_path, [ADA, ben])
    write_page(root, "_inbound/candidates/p-0000ab01.md", "A release.\n", id="p-0000ab01", released_from="ben")
    _commit(root)
    assert _gate(root, base, author="benliu", head_ref="release/ben")[0] == "auto-mergeable"
    assert _gate(root, base, author="benliu", head_ref="release/ben-0123abcd")[0] == "auto-mergeable"
    for bad in ({"author": "mallory", "head_ref": "release/ben"}, {"author": "benliu", "head_ref": "release/cara"},
                {"author": "benliu", "head_ref": "release/Ben"}, {"author": "benliu", "head_ref": "release/ben/x"}):
        assert _gate(root, base, **bad)[0] == "protected", bad
    # a duplicate mark is the receiving room's to write (landing.py same): one a sender wrote is protected
    write_page(root, "_inbound/candidates/p-0000ab01.md", "A release.\n", id="p-0000ab01", released_from="ben",
               duplicate_of={"id": "p-0000ab09", "by": "ben", "at": "2026-10-12T09:00:00Z"})
    _commit(root)
    cls, reasons = _gate(root, base, author="benliu", head_ref="release/ben-0123abcd")
    assert cls == "protected" and any("duplicate mark" in r for r in reasons)
    _git(root, "reset", "-q", "--hard", "HEAD~1")
    # every other condition holds for the older name too: nothing outside _inbound/
    (root / "work").mkdir(exist_ok=True)
    (root / "work" / "x.md").write_text("x\n", encoding="utf-8")
    _commit(root)
    cls, reasons = _gate(root, base, author="benliu", head_ref="release/ben")
    assert cls == "protected" and any("outside _inbound/" in r for r in reasons)
    assert "REMOVE" in Path(gate.__file__).read_text(encoding="utf-8").split("LEGACY_RELEASE_BRANCH =")[0][-400:]


def test_the_owners_agent_marks_a_waiting_release_on_its_sync_branch(tmp_path):
    ben = dict(BEN, workspace="ben", match={"github": "benliu"})
    root, _ = _brain(tmp_path, [ADA, ben])
    rel = "_inbound/candidates/p-0000ab02.md"
    write_page(root, rel, "A release.\n", id="p-0000ab02", released_from="ben", released_by="Ben Liu")
    base = _commit(root, "the release landed")
    marks = {"landing": "pending: Ada Okafor to sign", "for": "Ada Okafor", "held_at": "2026-10-12"}
    write_page(root, rel, "A release.\n", id="p-0000ab02", released_from="ben", released_by="Ben Liu", **marks)
    _commit(root, "chore: sync")
    assert _gate(root, base, author="adaokafor", head_ref="sync/m-1a2b3c4d")[0] == "auto-mergeable"
    assert _gate(root, base, author="benliu", head_ref="sync/m-1a2b3c4d")[0] == "protected"
    # the person's `later` on it (sign._candidate writes `deadline` in place) is the owner's own mark too
    _git(root, "reset", "-q", "--hard", base)
    write_page(root, rel, "A release.\n", id="p-0000ab02", released_from="ben", released_by="Ben Liu",
               deadline="2026-11-07T09:00:00Z")
    _commit(root, "chore: sync")
    assert _gate(root, base, author="adaokafor", head_ref="sync/m-1a2b3c4d")[0] == "auto-mergeable"
    assert _gate(root, base, author="benliu", head_ref="sync/m-1a2b3c4d")[0] == "protected"
    # so is the agent's mark that it is one record arrived twice (landing.py same)
    _git(root, "reset", "-q", "--hard", base)
    write_page(root, rel, "A release.\n", id="p-0000ab02", released_from="ben", released_by="Ben Liu",
               duplicate_of={"id": "p-0000ab09", "by": "pair-checker", "at": "2026-11-20T09:00:00Z"})
    _commit(root, "chore: sync")
    assert _gate(root, base, author="adaokafor", head_ref="sync/m-1a2b3c4d")[0] == "auto-mergeable"
    for changed in ({"body": "Another text.\n"}, {"released_from": "cara"}, {"released_by": "Cara Diaz"}):
        _git(root, "reset", "-q", "--hard", base)
        fields = dict(id="p-0000ab02", released_from="ben", released_by="Ben Liu", **marks)
        fields.update({k: v for k, v in changed.items() if k != "body"})
        write_page(root, rel, changed.get("body", "A release.\n"), **fields)
        _commit(root, "chore: sync")
        assert _gate(root, base, author="adaokafor", head_ref="sync/m-1a2b3c4d")[0] == "protected", changed


# ----- 5. code-owner review: the template only ----------------------------------------------------------------
@template_only
def test_security_md_recommends_no_code_owner_review_in_a_space():
    text = " ".join((REPO / "SECURITY.md").read_text(encoding="utf-8").split())
    assert "No code-owner review in a space" in text and "never lets the author approve" in text
    assert "Code-owner review in the template is optional" in text
    head = " ".join(line.lstrip("# ") for line in (REPO / ".github" / "CODEOWNERS").read_text(
        encoding="utf-8").splitlines() if line.startswith("#"))
    assert "A space turns on no code-owner review" in head


# ----- 6. a space following main moves forwards on its own ----------------------------------------------------
def _main_template(tmp_path) -> tuple[Path, str]:
    tpl = tmp_path / "tpl"
    (tpl / "_meta" / "tools").mkdir(parents=True)
    (tpl / "_meta" / "tools" / "sweep.py").write_text("print('v1')\n", encoding="utf-8")
    (tpl / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    _git(tpl, "init", "-q", "-b", "main")
    return tpl, _commit(tpl, "first")


def _main_space(tmp_path, tpl: Path, commit: str, updates="main") -> tuple[Path, str]:
    space = make_space(tmp_path / "ada", template_updates=updates)
    (space / PROV).write_text(yaml.safe_dump({"repo": tpl.as_uri(), "commit": commit, "files": {}}), encoding="utf-8")
    return space, _init(space)


def _move_to(space: Path, commit: str) -> None:
    data = yaml.safe_load((space / PROV).read_text(encoding="utf-8"))
    data.update(commit=commit, synced_at="2026-11-05T09:00:00Z")
    (space / PROV).write_text(yaml.safe_dump(data), encoding="utf-8")
    _commit(space, "sync")


def test_a_provenance_moving_forwards_along_main_lands(tmp_path):
    tpl, first = _main_template(tmp_path)
    (tpl / "CHANGELOG.md").write_text("# Changelog\n\nnotes\n", encoding="utf-8")
    second = _commit(tpl, "docs only")
    space, base = _main_space(tmp_path, tpl, first)
    _move_to(space, second)
    cls, reasons = _gate(space, base)
    assert cls == "auto-mergeable", reasons
    assert any("follows the template's main" in r for r in reasons)


def test_a_provenance_moving_back_or_off_main_or_with_machinery_waits(tmp_path):
    tpl, first = _main_template(tmp_path)
    (tpl / "CHANGELOG.md").write_text("# Changelog\n\nnotes\n", encoding="utf-8")
    second = _commit(tpl, "docs only")
    _git(tpl, "checkout", "-q", "-b", "side", first)
    (tpl / "CHANGELOG.md").write_text("# Changelog\n\nside\n", encoding="utf-8")
    side = _commit(tpl, "nobody merged this")
    _git(tpl, "checkout", "-q", "main")
    # backwards
    space, base = _main_space(tmp_path, tpl, second)
    _move_to(space, first)
    cls, reasons = _gate(space, base)
    assert cls == "protected" and any("the template release it records changed" in r for r in reasons)
    # onto a branch nobody merged
    _git(space, "reset", "-q", "--hard", base)
    (space / PROV).write_text(yaml.safe_dump({"repo": tpl.as_uri(), "commit": first, "files": {}}), encoding="utf-8")
    base = _commit(space, "back to first")
    _move_to(space, side)
    assert _gate(space, base)[0] == "protected"
    # forwards, but with machinery in the same pull request
    _git(space, "reset", "-q", "--hard", base)
    (space / "_meta" / "tools").mkdir(parents=True, exist_ok=True)
    (space / "_meta" / "tools" / "sweep.py").write_text("print('v1')\n", encoding="utf-8")
    _move_to(space, second)
    assert gate.follows_main(space, gate.rules_at(space, base), yaml.safe_load((space / PROV).read_text(
        encoding="utf-8")), gate.changed_files(space, base)) is None


def test_a_space_on_releases_never_moves_its_commit_this_way(tmp_path):
    tpl, first = _main_template(tmp_path)
    (tpl / "CHANGELOG.md").write_text("# Changelog\n\nnotes\n", encoding="utf-8")
    second = _commit(tpl, "docs only")
    space, base = _main_space(tmp_path, tpl, first, updates="releases")
    _move_to(space, second)
    assert _gate(space, base)[0] == "protected"


# ----- 7. the stay path accepts an older 8-character yes for the release it is on -----------------------------
def _tags_template(tmp_path) -> Path:
    from test_template_sync import git_template
    return git_template(tmp_path / "tpl")


def test_an_older_yes_keeps_a_space_on_its_release_and_never_moves_it(tmp_path):
    from test_template_sync import _release
    tpl = _tags_template(tmp_path)
    first = _release(tpl, "v0.1.0", "print('v1')\n", "2026-10-01T09:00:00Z")
    space = make_space(tmp_path / "ada")
    (space / PROV).write_text(yaml.safe_dump({"repo": tpl.as_uri(), "release": "v0.1.0", "commit": first,
                                               "approved": {"release": "v0.1.0", "commit": first,
                                                            "by": "Ada Okafor"}, "files": {}}), encoding="utf-8")
    with (space / "log.md").open("a", encoding="utf-8") as fh:
        fh.write(f"\n- 2026-10-18T09:00:00Z yes template-v0.1.0-{first[:8]} by Ada Okafor\n")
    report = template_sync.run(space, source=tpl.as_uri())
    assert report.release == "v0.1.0" and "has no recorded yes" not in report.note, report.note
    assert (space / "_meta" / "tools" / "sweep.py").read_text(encoding="utf-8") == "print('v1')\n"
    # an 8-character yes to a newer release never moves the space
    second = _release(tpl, "v0.2.0", "print('v2')\n", "2026-10-02T09:00:00Z")
    with (space / "log.md").open("a", encoding="utf-8") as fh:
        fh.write(f"\n- 2026-10-19T09:00:00Z yes template-v0.2.0-{second[:8]} by Ada Okafor\n")
    report = template_sync.run(space, source=tpl.as_uri())
    assert report.release == "v0.1.0" and report.note.startswith("v0.2.0 waits for a yes"), report.note
    # nor does an older yes keep a release the provenance never recorded as approved
    (space / PROV).write_text(yaml.safe_dump({"repo": tpl.as_uri(), "release": "v0.1.0", "commit": first,
                                               "files": {}}), encoding="utf-8")
    report = template_sync.run(space, source=tpl.as_uri())
    assert "v0.1.0 has no recorded yes" in report.note
    # an older yes binds only its 8 characters, so the stay path also asks that the recorded approval names the
    # tag's full commit; and someone else's older yes does not count
    loaded = spaceyaml.load(space)
    assert template_sync.kept_yes(loaded, "v0.1.0", first)
    assert not template_sync.kept_yes(loaded, "v0.3.0", first)
    other = make_space(tmp_path / "bea", owner="Bea Novak")
    with (other / "log.md").open("a", encoding="utf-8") as fh:
        fh.write(f"\n- 2026-10-18T09:00:00Z yes template-v0.1.0-{first[:8]} by Ada Okafor\n")
    assert not template_sync.kept_yes(spaceyaml.load(other), "v0.1.0", first)


# ----- an approved template release still lands in a space with no further click ------------------------------
def test_an_approved_release_lands_end_to_end_with_no_click(tmp_path):
    from test_template_sync import _release, _yes
    tpl = _tags_template(tmp_path)
    first = _release(tpl, "v0.1.0", "print('v1')\n", "2026-10-01T09:00:00Z")
    space = make_space(tmp_path / "ada")
    _yes(space, "v0.1.0", first)
    assert template_sync.run(space, source=tpl.as_uri()).release == "v0.1.0"
    base = _init(space)
    # a new release; the owner's one yes in Needs you; the weekly sync copies it and records the approval
    (tpl / ".claude" / "skills" / "sweep" / "SKILL.md").write_text("# sweep v2\n", encoding="utf-8")
    second = _release(tpl, "v0.2.0", "print('v2')\n", "2026-10-02T09:00:00Z")
    report = template_sync.run(space, source=tpl.as_uri())
    assert report.release == "v0.1.0" and report.note.startswith("v0.2.0 waits for a yes")
    _yes(space, "v0.2.0", second)
    report = template_sync.run(space, source=tpl.as_uri())
    assert report.release == "v0.2.0" and "_meta/tools/sweep.py" in report.updated
    _commit(space, "template sync")
    changed = gate.changed_files(space, base)
    assert "_meta/tools/sweep.py" in changed and ".claude/skills/sweep/SKILL.md" in changed and PROV in changed
    cls, reasons = gate.classify(space, changed, base_ref=base, author="adaokafor", head_ref="sync/m-desk-protected")
    assert cls == "auto-mergeable", reasons
    assert any(r.startswith("release v0.2.0 approved by Ada Okafor: 2 protected file(s) identical") for r in reasons)
    # one byte off the release and it waits
    (space / "_meta" / "tools" / "sweep.py").write_text("print('v2 edited')\n", encoding="utf-8")
    _commit(space, "edited")
    assert gate.classify(space, gate.changed_files(space, base), base_ref=base)[0] == "protected"


# ----- verifier round: each fill and each upkeep keeps its guard -----------------------------------------------
HARBOR = {"id": "harbor", "kind": "projects", "home": "harbor", "owner": "Ada Okafor",
          "classification": {"class": "walled", "state": "settled", "home": "harbor", "crew": ["ada-okafor"]}}
TIDE = {"id": "tide", "kind": "clients", "home": "northwind", "owner": "Ada Okafor"}


@pytest.mark.parametrize("workspace", ["harbor", "northwind", "cara-diaz", "tide", "ben"])
def test_a_fill_never_names_a_space_a_subject_or_another_persons_slug_as_a_workspace(tmp_path, workspace):
    ben = dict(BEN, match={"github": "benliu"})
    root, base = _brain(tmp_path, [ADA, ben, CARA, HARBOR, TIDE], invited={"cara-diaz": "caradiaz"})
    _registry(root, [ADA, dict(ben, workspace=workspace), CARA, HARBOR, TIDE])
    _commit(root)
    cls, reasons = _gate(root, base, author="benliu")
    assert cls == "protected" and any("subject `ben-liu` changes where trust points" in r for r in reasons), reasons
    # and the release corridor of that name stays closed to Ben
    _git(root, "reset", "-q", "--hard", base)
    (root / "_inbound" / "signals").mkdir(parents=True, exist_ok=True)
    (root / "_inbound" / "signals" / "s-1.yaml").write_text(f"from: {workspace}\nnote: workspace\n", encoding="utf-8")
    _commit(root)
    assert _gate(root, base, author="benliu", head_ref=f"release/{workspace}-0123abcd")[0] == "protected"


def test_a_fill_takes_the_rows_own_slug_and_a_repository_under_its_own_login_only(tmp_path):
    ben = dict(BEN, match={"github": "benliu"})
    other = dict(CARA, workspace="cara-diaz", workspace_repo="https://github.com/caradiaz/cara-workspace",
                 match={"github": "caradiaz"})
    root, base = _brain(tmp_path, [ADA, ben, other, HARBOR])
    for repo, cls in (("https://github.com/benliu/ben-workspace", "auto-mergeable"),
                      ("https://github.com/northwind-co/ben-workspace", "protected"),   # an organisation's: classify's
                      ("https://github.com/CaraDiaz/Cara-Workspace.git", "protected"),  # another row's, in another case
                      ("../ben", "protected")):
        _registry(root, [ADA, dict(ben, workspace="ben-liu", workspace_repo=repo), other, HARBOR])
        _commit(root)
        assert _gate(root, base, author="benliu")[0] == cls, repo
        _git(root, "reset", "-q", "--hard", base)


def test_an_invitation_without_a_login_lets_no_login_claim_the_row(tmp_path):
    root, base = _brain(tmp_path, [ADA, BEN, CARA], invited={"ben-liu": None, "cara-diaz": None})
    # another invitee claims Ben's row with their own login
    _registry(root, [ADA, dict(BEN, workspace="ben-liu", match={"github": "caradiaz"}), CARA])
    _commit(root)
    cls, reasons = _gate(root, base, author="caradiaz")
    assert cls == "protected" and any("subject `ben-liu`" in r for r in reasons), reasons
    # nor does Ben himself land it through an invitation that names no login: the owner's click, or invite --login
    _git(root, "reset", "-q", "--hard", base)
    _registry(root, [ADA, dict(BEN, workspace="ben-liu", match={"github": "benliu"}), CARA])
    _commit(root)
    assert _gate(root, base, author="benliu")[0] == "protected"


@pytest.mark.parametrize("sid,change", [
    ("tide", {"repos": [{"repo": "https://github.com/example-test/tide"}]}),
    ("tide", {"obligations": []}),
    ("tide", {"state": "archived"}),
    ("tide", {"client": "kelp"}),
    ("ada-okafor", {"aliases": ["mallory@example.test"]}),
    ("ada-okafor", {"email": "mallory@example.test"}),
    ("ben-liu", {"aliases": ["ada@example.test"]}),     # their own row, but another person's address
    ("ben-liu", {"email": "Ada Okafor"}),              # their own row, but another person's name
])
def test_a_colleague_changes_no_identity_attach_or_archive_field(tmp_path, sid, change):
    ada = dict(ADA, email="ada@example.test")
    ben = dict(BEN, workspace="ben-liu", match={"github": "benliu"})
    tide = dict(TIDE, obligations=[{"kind": "nda", "date": "2026-10-01"}])
    rows = {"ada-okafor": ada, "ben-liu": ben, "tide": tide, "kelp": {"id": "kelp", "kind": "topics"}}
    root, base = _brain(tmp_path, list(rows.values()))
    _registry(root, [dict(r, **change) if k == sid else r for k, r in rows.items()])
    _commit(root)
    cls, reasons = _gate(root, base, author="benliu")
    assert cls == "protected" and any("not its owner" in r for r in reasons), reasons


def test_a_colleague_keeps_their_own_names_and_a_subjects_upkeep(tmp_path):
    ben = dict(BEN, workspace="ben-liu", match={"github": "benliu"})
    root, base = _brain(tmp_path, [ADA, ben, TIDE])
    _registry(root, [ADA, dict(ben, aliases=["Ben L."], email="ben@example.test"),
                     dict(TIDE, state="dormant", summary="Paused until spring")])
    _commit(root)
    cls, reasons = _gate(root, base, author="benliu")
    assert cls == "auto-mergeable", reasons


@pytest.mark.parametrize("extra", [
    {"owner": "Ben Liu"}, {"crew": ["ben-liu"]}, {"part_of": "tide"}, {"match": {"domain": "tide.example"}},
    {"classification": {"class": "walled", "state": "settled", "home": "northwind", "crew": ["ben-liu"]}},
    {"repos": [{"repo": "https://github.com/example-test/tide"}]}, {"aliases": ["Tide"]}])
def test_a_colleague_adds_only_a_plain_subject(tmp_path, extra):
    ben = dict(BEN, workspace="ben-liu", match={"github": "benliu"})
    tide = dict(TIDE, match={"domain": "tide.example"})
    root, base = _brain(tmp_path, [ADA, ben, tide])
    _registry(root, [ADA, ben, tide, {"id": "tide-2", "kind": "clients", "title": "Tide Two", **extra}])
    _commit(root)
    cls, reasons = _gate(root, base, author="benliu")
    assert cls == "protected" and any("subject `tide-2` added with" in r for r in reasons), reasons
    # a plain one lands, with a key that attaches nothing too
    _git(root, "reset", "-q", "--hard", base)
    _registry(root, [ADA, ben, tide, {"id": "tide-2", "kind": "clients", "title": "Tide Two", "home": "northwind",
                                      "match": {"repo_owner": "example-test"}}])
    _commit(root)
    assert _gate(root, base, author="benliu")[0] == "auto-mergeable"


def test_a_decided_classification_changes_only_with_the_click(tmp_path):
    reef = {"id": "reef", "kind": "projects", "home": "harbor",
            "classification": {"class": "walled", "state": "settled", "home": "harbor", "crew": ["ada-okafor"]}}
    lantern = {"id": "lantern", "kind": "clients", "home": "northwind",
               "classification": {"class": "company", "state": "settled", "home": "northwind"}}
    root, base = _personal(tmp_path, [reef, lantern])
    for change in ({"home": "northwind", "classification": {"class": "company", "state": "settled", "home": "northwind"}},
                   {"classification": dict(reef["classification"], crew=["ada-okafor", "ben-liu"])},
                   {"classification": dict(reef["classification"], home="ada")}, {"home": "ada"},
                   {"classification": None}):
        _registry(root, [{k: v for k, v in dict(reef, **change).items() if v is not None}, lantern])
        _commit(root)
        cls, reasons = _gate(root, base)
        assert cls == "protected" and any("once decided" in r for r in reasons), (change, reasons)
        _git(root, "reset", "-q", "--hard", base)
    # a re-opening, an ask that keeps the decision as `previous`, and re-settling it as it was all land
    opened = dict(lantern, classification=dict(lantern["classification"], state="open"))
    asking = dict(lantern, classification={"state": "asking", "asked": ["ben-liu"],
                                           "previous": {"class": "company", "home": "northwind"}})
    for row in (opened, asking, dict(lantern, classification=dict(lantern["classification"], at="2026-11-05"))):
        _registry(root, [reef, row])
        _commit(root)
        cls, reasons = _gate(root, base)
        assert cls == "auto-mergeable", (row, reasons)
        _git(root, "reset", "-q", "--hard", base)
    # the decision that governs a child from its walled umbrella moves only with the click too: re-parented
    # under a company umbrella, or the umbrella dropped and re-added as company under a new id
    keyed = dict(reef, match={"domain": "reef.example"})
    kelp = {"id": "kelp", "kind": "deals", "part_of": "reef", "match": {"domain": "kelp.example"}}
    root2, base2 = _personal(tmp_path / "b", [keyed, lantern, kelp])
    for rows in ([keyed, lantern, dict(kelp, part_of="lantern")],
                 [dict(keyed, id="reef-co", home="northwind", classification=lantern["classification"]),
                  lantern, kelp],
                 [lantern, dict(kelp, id="kelp-co", part_of="lantern")]):
        _registry(root2, rows)
        _commit(root2)
        cls, reasons = _gate(root2, base2)
        assert cls == "protected", (rows, reasons)
        _git(root2, "reset", "-q", "--hard", base2)
    # a new child under the walled umbrella, and a re-parenting under another umbrella of the same decision, land
    for rows in ([keyed, lantern, kelp, {"id": "kelp-2", "kind": "deals", "part_of": "reef",
                                         "match": {"domain": "reef.example"}}],
                 [keyed, lantern, dict(kelp, part_of="cove"), {"id": "cove", "kind": "projects", "home": "harbor",
                                                               "classification": reef["classification"]}]):
        _registry(root2, rows)
        _commit(root2)
        cls, reasons = _gate(root2, base2)
        assert cls == "auto-mergeable", (rows, reasons)
        _git(root2, "reset", "-q", "--hard", base2)


def test_a_space_gains_no_signer_through_the_registry_on_green(tmp_path):
    rows = [{"id": "ada-okafor", "kind": "people", "title": "Ada Okafor"},
            {"id": "ben-liu", "kind": "people", "title": "Ben Liu"},
            {"id": "reef", "kind": "projects", "home": "harbor", "owner": "Ada Okafor"}]
    root, base = _personal(tmp_path, rows)
    for added in ({"id": "decoy", "kind": "topics", "home": "harbor", "owner": "Mallory Outsider"},
                  {"id": "decoy", "kind": "topics", "home": "harbor", "owner": "Ben Liu"},     # a colleague, for harbor
                  {"id": "decoy", "kind": "topics", "part_of": "reef", "owner": "Mallory Outsider"},
                  {"id": "decoy", "kind": "topics", "home": "harbor", "crew": ["ben-liu"]}):
        _registry(root, [*rows, added])
        _commit(root)
        cls, reasons = _gate(root, base)
        assert cls == "protected" and any("harbor gains a signer" in r for r in reasons), (added, reasons)
        _git(root, "reset", "-q", "--hard", base)
    # the space's owner may be one anywhere
    _registry(root, [*rows, {"id": "pier", "kind": "topics", "home": "harbor", "owner": "Ada Okafor"}])
    _commit(root)
    assert _gate(root, base)[0] == "auto-mergeable"
    _git(root, "reset", "-q", "--hard", base)
    # landing signs per subject, its umbrellas' crew counted: re-parenting reef under a subject homed elsewhere
    # makes that subject's crew signers of reef's pages in harbor
    cara = {"id": "cara-diaz", "kind": "people", "title": "Cara Diaz"}
    pier = {"id": "pier", "kind": "projects", "home": "northwind", "crew": ["cara-diaz"]}
    root2, base2 = _personal(tmp_path / "b", [*rows, cara, pier])
    for reef in (dict(rows[2], part_of="pier"),        # an umbrella with a crew elsewhere
                 dict(rows[2], part_of="brain-only")):  # an umbrella only the brain mount's registry holds
        _registry(root2, [*rows[:2], reef, cara, pier])
        _commit(root2)
        cls, reasons = _gate(root2, base2)
        assert cls == "protected" and any("harbor" in r for r in reasons), (reef, reasons)
        _git(root2, "reset", "-q", "--hard", base2)


def test_the_brain_gains_registered_signers_only(tmp_path):
    ben = dict(BEN, workspace="ben-liu", match={"github": "benliu"})
    root, base = _brain(tmp_path, [ADA, ben])
    for owner, cls in (("Ben Liu", "auto-mergeable"), ("Mallory Outsider", "protected")):
        _registry(root, [ADA, ben, {"id": "sales", "kind": "domains", "owner": owner},
                         {"id": "tide", "kind": "clients", "home": "northwind", "owner": owner}])
        _commit(root)
        got, reasons = _gate(root, base, author="adaokafor")
        assert got == cls, (owner, reasons)
        _git(root, "reset", "-q", "--hard", base)


def _stable(root: Path, by: str, signed_in: str, subject: str | None = None) -> None:
    import pages
    body = "A claim.\n"
    write_page(root, "knowledge/domains/pilots/p-1a2b3c4d.md", body, id="p-1a2b3c4d", type="concept",
               status="stable", signed_by=by, signed_in=signed_in, signed_at="2026-11-05T09:00:00Z",
               fingerprint=pages.fingerprint(body), **({"subject": subject} if subject else {}))


def test_a_signature_lands_only_from_the_signers_own_login(tmp_path):
    ben = dict(BEN, workspace="ben-liu", match={"github": "benliu"})
    cara = dict(CARA, workspace="cara-diaz", match={"github": "caradiaz"})
    pilots = {"id": "pilots", "kind": "domains", "title": "Pilots", "owner": "Cara Diaz"}
    root, base = _brain(tmp_path, [ADA, ben, cara, pilots])
    for by, subject, author, want in (("Ada Okafor", None, "benliu", "protected"),     # the owner's, forged
                                      ("Cara Diaz", "pilots", "benliu", "protected"),  # the subject's owner's, forged
                                      ("Cara Diaz", "pilots", "caradiaz", "auto-mergeable"),
                                      ("Ada Okafor", None, "adaokafor", "auto-mergeable"),
                                      ("Ada Okafor", None, None, "protected")):        # no author named
        _stable(root, by, "northwind", subject)
        _commit(root)
        cls, reasons = _gate(root, base, author=author, head_ref="x/notes")
        assert cls == want, (by, author, reasons)
        _git(root, "reset", "-q", "--hard", base)
    # a personal space: its owner's signature lands while the registry gives the owner no login, and only
    # from that login once it does
    for rows, author, want in (([], "anyone", "auto-mergeable"), ([ADA], "adaokafor", "auto-mergeable"),
                               ([ADA], "benliu", "protected")):
        own = make_space(tmp_path / f"p-{len(rows)}-{author}", owner="Ada Okafor", subjects=rows)
        name = yaml.safe_load((own / "space.yaml").read_text(encoding="utf-8"))["name"]
        own_base = _init(own)
        _stable(own, "Ada Okafor", name)
        _commit(own)
        cls, reasons = _gate(own, own_base, author=author)
        assert cls == want, (rows, author, reasons)


def test_a_release_from_a_colleague_with_no_workspace_yet_lands_on_their_own_slug(tmp_path):
    ben = dict(BEN, match={"github": "benliu"})
    root, base = _brain(tmp_path / "a", [ADA, ben, CARA])
    write_page(root, "_inbound/candidates/p-0000ab02.md", "A release.\n", id="p-0000ab02", released_from="ben-liu")
    _commit(root)
    for ref in ("release/ben-liu", "release/ben-liu-0123abcd"):
        assert _gate(root, base, author="benliu", head_ref=ref)[0] == "auto-mergeable", ref
    assert _gate(root, base, author="caradiaz", head_ref="release/ben-liu")[0] == "protected"
    # an archived person, or a slug another row already names as its workspace, never falls back
    for n, rows in enumerate(([ADA, dict(ben, state="archived"), CARA],
                              [ADA, ben, dict(CARA, workspace="ben-liu", match={"github": "caradiaz"})])):
        other, obase = _brain(tmp_path / f"b{n}", rows)
        write_page(other, "_inbound/candidates/p-0000ab02.md", "A release.\n", id="p-0000ab02",
                   released_from="ben-liu")
        _commit(other)
        assert _gate(other, obase, author="benliu", head_ref="release/ben-liu")[0] == "protected", rows


# ----- the release tag is checked, not trusted ----------------------------------------------------------------
def _ruleset(**kw):
    rs = {"id": 7, "name": "release tags", "target": "tag", "enforcement": "active",
          "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
          "rules": [{"type": "creation"}, {"type": "update"}, {"type": "deletion"}],
          "current_user_can_bypass": "never"}
    rs.update(kw)
    return rs


def test_the_release_job_publishes_only_while_the_tag_ruleset_is_there():
    import template_archive
    assert template_archive.tag_ruleset_problem([_ruleset()]) is None
    assert template_archive.tag_ruleset_problem([_ruleset(conditions={"ref_name": {"include": ["~ALL"]}})]) is None
    for bad in ([], [_ruleset(target="branch")], [_ruleset(enforcement="evaluate")],
                [_ruleset(conditions={"ref_name": {"include": ["refs/tags/v*"], "exclude": ["refs/tags/v*-rc*"]}})],
                [_ruleset(conditions={"ref_name": {"include": ["refs/tags/release-*"]}})],
                [_ruleset(rules=[{"type": "creation"}])], [_ruleset(current_user_can_bypass="always")],
                [_ruleset(bypass_actors=[{"actor_id": 15368, "actor_type": "Integration", "bypass_mode": "always"}])]):
        problem = template_archive.tag_ruleset_problem(bad)
        assert problem and "only a maintainer may create or move a release tag" in problem, bad
    flow = yaml.safe_load((REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    steps = flow["jobs"]["release"]["steps"]
    names = [s.get("name") for s in steps]
    check = steps[names.index("Only a maintainer can make a release tag")]
    assert "template_archive.py ruleset" in check["run"] and "${{" not in check["run"]
    assert names.index("Only a maintainer can make a release tag") < names.index("Build the archive and its checksum")


@template_only
def test_security_md_names_the_tag_ruleset_check():
    text = " ".join((REPO / "SECURITY.md").read_text(encoding="utf-8").split())
    assert "`template_archive.py ruleset`" in text


@template_only
def test_security_md_says_github_does_not_enforce_the_hold():
    text = " ".join((REPO / "SECURITY.md").read_text(encoding="utf-8").split())
    assert "GitHub does not enforce the hold itself" in text and "agents never merge" in text


# ----- round 3, second pass: who may sign what, the answers the tools act on ------------------------------------
def _page_at(root: Path, rel: str, by: str, subject: str | None, body: str) -> None:
    import pages
    write_page(root, rel, body, id=Path(rel).stem, type="concept", title="Pilots", status="stable", signed_by=by,
               signed_in="northwind", signed_at="2026-11-05T09:00:00Z", fingerprint=pages.fingerprint(body),
               **({"subject": subject} if subject else {}))


def test_a_subjects_people_sign_only_that_subjects_pages(tmp_path):
    ben = dict(BEN, workspace="ben-liu", match={"github": "benliu"})
    cara = dict(CARA, workspace="cara-diaz", match={"github": "caradiaz"})
    pilots = {"id": "pilots", "kind": "domains", "title": "Pilots", "owner": "Cara Diaz"}
    sales = {"id": "sales", "kind": "domains", "title": "Sales", "owner": "Ben Liu"}
    root = make_space(tmp_path / "brain", profile="brain", name="northwind", owner="Ada Okafor",
                      subjects=[ADA, ben, cara, pilots, sales])
    _page_at(root, "knowledge/domains/pilots/p-1a2b3c4d.md", "Cara Diaz", "pilots", "Cara's claim.\n")
    _page_at(root, "knowledge/decisions/d-1a2b3c4d.md", "Ada Okafor", None, "Old decision.\n")
    base = _init(root)
    # Ben re-points Cara's page, or the owner's decision, at his own subject and signs it himself
    for rel in ("knowledge/domains/pilots/p-1a2b3c4d.md", "knowledge/decisions/d-1a2b3c4d.md"):
        _page_at(root, rel, "Ben Liu", "sales", "Pilots are cancelled.\n")
        _commit(root)
        cls, reasons = _gate(root, base, author="benliu", head_ref="ben/notes")
        assert cls == "protected" and any(rel in r for r in reasons), (rel, reasons)
        _git(root, "reset", "-q", "--hard", base)
    # he deletes them, or moves Cara's page into his own folder under its id and signs it there
    import pages
    for rel in ("knowledge/domains/pilots/p-1a2b3c4d.md", "knowledge/decisions/d-1a2b3c4d.md"):
        (root / rel).unlink()
        _commit(root)
        cls, reasons = _gate(root, base, author="benliu", head_ref="ben/notes")
        assert cls == "protected" and any(r.startswith(rel) for r in reasons), (rel, reasons)
        _git(root, "reset", "-q", "--hard", base)
    (root / "knowledge/domains/pilots/p-1a2b3c4d.md").unlink()
    _page_at(root, "knowledge/domains/sales/p-1a2b3c4d.md", "Ben Liu", "sales", "Pilots are cancelled.\n")
    _commit(root)
    cls, reasons = _gate(root, base, author="benliu", head_ref="ben/notes")
    assert cls == "protected" and any(r.startswith("knowledge/domains/sales/p-1a2b3c4d.md") for r in reasons), reasons
    _git(root, "reset", "-q", "--hard", base)
    # or signs, in his own folder, a decision that supersedes the owner's
    body = "We reverse it.\n"
    write_page(root, "knowledge/domains/sales/d-2b3c4d5e.md", body, id="d-2b3c4d5e", type="decision", status="stable",
               subject="sales", supersedes="d-1a2b3c4d", signed_by="Ben Liu", signed_in="northwind",
               signed_at="2026-11-06T09:00:00Z", fingerprint=pages.fingerprint(body))
    _commit(root)
    assert _gate(root, base, author="benliu", head_ref="ben/notes")[0] == "protected"
    _git(root, "reset", "-q", "--hard", base)
    # or unsigns them instead: Cara's page made a draft with a new body, the owner's decision deprecated
    write_page(root, "knowledge/domains/pilots/p-1a2b3c4d.md", "Pilots are cancelled.\n", id="p-1a2b3c4d",
               type="concept", title="Pilots", status="draft", subject="pilots")
    _page_at(root, "knowledge/decisions/d-1a2b3c4d.md", "Ada Okafor", None, "Old decision.\n")
    fm, text = pages.read(root / "knowledge/decisions/d-1a2b3c4d.md")
    pages.write(root / "knowledge/decisions/d-1a2b3c4d.md", dict(fm, status="deprecated"), text)
    _commit(root)
    cls, reasons = _gate(root, base, author="benliu", head_ref="ben/notes")
    assert cls == "protected" and all(any(r.startswith(rel) for r in reasons) for rel in (
        "knowledge/domains/pilots/p-1a2b3c4d.md", "knowledge/decisions/d-1a2b3c4d.md")), reasons
    _git(root, "reset", "-q", "--hard", base)
    # or points the owner's decision at another by a key outside the signature; the owner's own edit lands
    _page_at(root, "knowledge/decisions/d-1a2b3c4d.md", "Ada Okafor", None, "Old decision.\n")
    fm, text = pages.read(root / "knowledge/decisions/d-1a2b3c4d.md")
    pages.write(root / "knowledge/decisions/d-1a2b3c4d.md", dict(fm, type="decision", supersedes="d-9a8b7c6d"), text)
    _commit(root)
    assert _gate(root, base, author="benliu", head_ref="ben/notes")[0] == "protected"
    assert _gate(root, base, author="adaokafor", head_ref="ada/notes")[0] == "auto-mergeable"
    _git(root, "reset", "-q", "--hard", base)
    # a withdrawal waits for the owner's click whoever pushes it, the signer and the owner too: a cloud run
    # pushes as its person (gate.withdrawal_reasons)
    for rel, author in (("knowledge/domains/pilots/p-1a2b3c4d.md", "caradiaz"),
                        ("knowledge/domains/pilots/p-1a2b3c4d.md", "adaokafor"),
                        ("knowledge/decisions/d-1a2b3c4d.md", "adaokafor")):
        (root / rel).unlink()
        _commit(root)
        cls, reasons = _gate(root, base, author=author, head_ref="x/notes")
        assert cls == "protected" and any(r.startswith(rel) for r in reasons), (rel, author, reasons)
        _git(root, "reset", "-q", "--hard", base)
    # he writes who released Cara's page (`landed_from`, `released_from`, `released_by`): readers act on it
    rel = root / "knowledge/domains/pilots/p-1a2b3c4d.md"
    for key, value in (("landed_from", "ben-liu"), ("released_from", "ben-liu"), ("released_by", "Ben Liu")):
        fm, text = pages.read(rel)
        pages.write(rel, dict(fm, **{key: value}), text)
        _commit(root)
        assert _gate(root, base, author="benliu", head_ref="ben/notes")[0] == "protected", key
        _git(root, "reset", "-q", "--hard", base)
    # suppose it landed anyway: his own released retraction withdraws nothing landing applies, as the page
    # was signed in northwind, not in his workspace; it is marked contested and no withdrawal reaches the gate
    import landing
    fm, text = pages.read(rel)
    pages.write(rel, dict(fm, landed_from="ben-liu"), text)
    write_page(root, "_inbound/candidates/p-0000ee01.md", "", id="p-0000ee01", type="retraction", status="draft",
               retracts="p-1a2b3c4d", released_from="ben-liu", release_class="retraction")
    base2 = _commit(root, "suppose both landed")
    landing.land(root)
    _commit(root)
    kept = pages.read(rel)[0]
    assert not pages.withdrawn(kept) and kept["status"] == "stable" and kept["contested"] == ["p-0000ee01"]
    cls, reasons = _gate(root, base2, author="adaokafor", head_ref="sync/m-1")
    assert not any("withdrawn" in r for r in reasons), reasons
    _git(root, "reset", "-q", "--hard", base)
    # a page filed under his own subject still lands from his own login
    _page_at(root, "knowledge/domains/sales/p-2b3c4d5e.md", "Ben Liu", "sales", "Sales claim.\n")
    _commit(root)
    assert _gate(root, base, author="benliu", head_ref="ben/notes")[0] == "auto-mergeable"


@template_only
def test_the_owner_login_onboard_writes_into_codeowners_lands_the_owners_signature(tmp_path, monkeypatch):
    import onboard
    ada = {k: v for k, v in ADA.items() if k != "match"}   # an owner's row with no login, as older brains have it
    root = make_space(tmp_path / "brain", profile="brain", name="northwind", owner="Ada Okafor", subjects=[ada])
    (root / ".github").mkdir(exist_ok=True)
    (root / ".github" / "CODEOWNERS").write_text((REPO / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
                                                 + "/docs/ @northwind/editors\n", encoding="utf-8")
    monkeypatch.setattr(onboard, "run_cmd", lambda argv, *a, **k: subprocess.CompletedProcess(
        argv, 0, "adaokafor\n" if list(argv[:3]) == ["gh", "api", "user"] else "", ""))
    onboard.platform_settings(onboard.Report(case="test"), root, "northwind/brain", None, False)
    assert "@adaokafor" in (root / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    base = _init(root)
    assert gate._code_owner_logins(root, base) == {"adaokafor"}     # never the team
    for author, want in (("adaokafor", "auto-mergeable"), ("benliu", "protected"), (None, "protected")):
        _stable(root, "Ada Okafor", "northwind")
        _commit(root)
        assert _gate(root, base, author=author, head_ref="sync/m-1")[0] == want, author
        _git(root, "reset", "-q", "--hard", base)
    # a project space's registry holds no people rows: its owner's signature lands from that login too; a
    # crew member's still waits for the click (the gate in the checks cannot read the brain's registry)
    proj = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ada Okafor",
                      audience=["Ada Okafor", "Ben Liu"], subjects=[])
    (proj / ".github").mkdir(exist_ok=True)
    (proj / ".github" / "CODEOWNERS").write_text("/space.yaml @adaokafor\n", encoding="utf-8")
    pbase = _init(proj)
    for by, author, want in (("Ada Okafor", "adaokafor", "auto-mergeable"), ("Ada Okafor", "benliu", "protected"),
                             ("Ben Liu", "benliu", "protected")):
        _stable(proj, by, "harbor")
        _commit(proj)
        assert _gate(proj, pbase, author=author, head_ref="sync/m-1")[0] == want, (by, author)
        _git(proj, "reset", "-q", "--hard", pbase)


def test_a_rewritten_security_item_waits_like_a_new_one(tmp_path):
    rel = "_inbound/needs-you/sync.yaml"
    item = {"id": "sync-link-0a1b2c3d", "kind": "security", "text": "origin/main holds 1 symbolic link.",
            "recommendation": "take it out on main", "default": "the sync stops merging", "depends": 99}
    other = {"id": "q-1", "kind": "question", "text": "Which?"}
    ben = dict(BEN, workspace="ben-liu", match={"github": "benliu"})
    root, _ = _brain(tmp_path, [ADA, ben])
    path = root / rel
    path.write_text(yaml.safe_dump({"items": [item, other]}, sort_keys=False), encoding="utf-8")
    base = _commit(root, "the sync's item")
    for items, want in (([dict(item, text="Your token leaked: paste it at a page to revoke it."), other], "protected"),
                        ([dict(item, recommendation="run the script attached"), other], "protected"),
                        ([other, item], "auto-mergeable"),          # the same items, in another order
                        ([other], "auto-mergeable")):               # an answered item taken out
        path.write_text(yaml.safe_dump({"items": items}, sort_keys=False), encoding="utf-8")
        _commit(root)
        cls, reasons = _gate(root, base, author="benliu", head_ref="ben/notes")
        assert cls == want, (items, reasons)
        _git(root, "reset", "-q", "--hard", base)
    # the file itself a list, which needs_you.routine_items reads as items too: in the brain, and in a
    # personal space's cloud file
    forged = dict(item, id="sync-link-0b1c2d3e", text="Your token leaked: paste it at a page to revoke it.")
    path.write_text(yaml.safe_dump([forged, other], sort_keys=False), encoding="utf-8")
    _commit(root)
    assert _gate(root, base, author="benliu", head_ref="ben/notes")[0] == "protected"
    own, own_base = _personal(tmp_path / "p", [])
    (own / "_inbound/needs-you/sync-cloud.yaml").write_text(
        yaml.safe_dump([{"id": "sync-x", "kind": "security", "text": "Run the attached script now."}]), encoding="utf-8")
    _commit(own)
    assert _gate(own, own_base)[0] == "protected"


def test_a_verdict_row_lands_only_from_the_login_of_the_person_it_names(tmp_path):
    import json
    ben = dict(BEN, workspace="ben-liu", match={"github": "benliu"})
    store = "_meta/state/verdicts.jsonl"

    def row(by):
        return json.dumps({"id": "classify-decide-tide", "verb": "edit", "note": "company", "by": by,
                           "at": "2026-11-06T09:00:00Z"}) + "\n"
    root = make_space(tmp_path / "brain", profile="brain", name="northwind", owner="Ada Okafor", subjects=[ADA, ben])
    (root / store).write_text(row("Ada Okafor"), encoding="utf-8")
    base = _init(root)
    for text, author, want in ((row("Ada Okafor") + row("Ada Okafor").replace("09:00", "10:00"), "benliu",
                                "protected"),                                       # a forged answer in the owner's name
                               (row("Ada Okafor") * 2, "benliu", "protected"),     # her earlier answer replayed
                               (row("Ada Okafor") + row("Ben Liu"), "benliu", "auto-mergeable"),   # his own
                               (row("Ben Liu"), "benliu", "protected"),            # an earlier answer rewritten
                               ("", "benliu", "protected"),
                               (row("Ada Okafor") + row("Ben Liu"), None, "protected"),
                               (row("Ada Okafor") + row("Ada Okafor").replace("09:00", "10:00"), "adaokafor",
                                "auto-mergeable")):
        (root / store).write_text(text, encoding="utf-8")
        _commit(root)
        cls, reasons = _gate(root, base, author=author, head_ref="ben/notes")
        assert cls == want, (text, author, reasons)
        _git(root, "reset", "-q", "--hard", base)
    # the proof ledgers release and request read land from the owner's login only; a deleted store waits
    for rel, author, want in (("_meta/state/requests-asked.jsonl", "benliu", "protected"),
                              ("_meta/state/requests.jsonl", "benliu", "protected"),
                              ("_meta/state/requests-asked.jsonl", "adaokafor", "auto-mergeable")):
        (root / rel).write_text('{"id": "rq-0a1b2c3d", "to": "harbor", "outcome": "asked"}\n', encoding="utf-8")
        _commit(root)
        assert _gate(root, base, author=author, head_ref="ben/notes")[0] == want, (rel, author)
        _git(root, "reset", "-q", "--hard", base)
    (root / store).unlink()
    _commit(root)
    assert _gate(root, base, author="benliu", head_ref="ben/notes")[0] == "protected"
    # a personal space has one writer, its owner: its ledgers land as before
    own = make_space(tmp_path / "ada", owner="Ada Okafor", subjects=[])
    own_base = _init(own)
    (own / store).write_text(row("Ada Okafor"), encoding="utf-8")
    _commit(own)
    assert _gate(own, own_base, author="anyone")[0] == "auto-mergeable"


def test_a_walled_decision_does_not_escape_over_several_green_pull_requests(tmp_path):
    reef = {"id": "reef", "kind": "projects", "home": "harbor", "match": {"domain": "reef.example"},
            "classification": {"class": "walled", "state": "settled", "home": "harbor", "crew": ["ada-okafor"]}}
    lantern = {"id": "lantern", "kind": "clients", "home": "northwind",
               "classification": {"class": "company", "state": "settled", "home": "northwind"}}
    kelp = {"id": "kelp", "kind": "deals", "part_of": "reef", "match": {"domain": "kelp.example"}}
    opened = dict(reef, classification=dict(reef["classification"], state="open"))
    asking = dict(reef, classification={"state": "asking", "previous": reef["classification"]})
    detached = dict({k: v for k, v in kelp.items() if k != "part_of"}, home="harbor")
    for n, steps in enumerate((
            [([reef, lantern, detached], "protected")],          # the child detached, its home kept
            # the umbrella re-opened (lands), then the child re-parented under a company umbrella
            [([opened, lantern, kelp], "auto-mergeable"), ([opened, lantern, dict(kelp, part_of="lantern")], "protected")],
            # an ask that keeps the decision as `previous` (lands), then the child detached
            [([asking, lantern, kelp], "auto-mergeable"), ([asking, lantern, detached], "protected")],
            # the governed child dropped (waits), so that it cannot come back under a company umbrella next
            [([reef, lantern], "protected")])):
        root, base = _personal(tmp_path / str(n), [reef, lantern, kelp])
        for rows, want in steps:
            _registry(root, rows)
            _commit(root)
            cls, reasons = _gate(root, base)
            assert cls == want, (rows, reasons)
            base = _git(root, "rev-parse", "HEAD")   # it landed: the next pull request's base


# ----- round 3, repair two: crew logins, withdrawals from the cloud, private zones, a fill from a note ----------
def test_a_crew_member_signs_in_a_project_space_from_the_login_its_space_yaml_lists(tmp_path):
    import json
    proj = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ada Okafor",
                      audience=["Ada Okafor", "Ben Liu"], logins={"Ben Liu": "benliu", "Mallory Outsider": "mallory"},
                      subjects=[])
    (proj / ".github").mkdir(exist_ok=True)
    (proj / ".github" / "CODEOWNERS").write_text("/space.yaml @adaokafor\n", encoding="utf-8")
    pbase = _init(proj)
    for by, author, want in (("Ben Liu", "benliu", "auto-mergeable"), ("Ben Liu", "caradiaz", "protected"),
                             ("Ada Okafor", "benliu", "protected")):
        _stable(proj, by, "harbor")
        _commit(proj)
        assert _gate(proj, pbase, author=author, head_ref="sync/m-1")[0] == want, (by, author)
        _git(proj, "reset", "-q", "--hard", pbase)
    # his own verdict row lands from his login; a name outside the audience is never given one
    store = proj / "_meta/state/verdicts.jsonl"
    for by, author, want in (("Ben Liu", "benliu", "auto-mergeable"), ("Mallory Outsider", "mallory", "protected")):
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps({"id": "q-1", "verb": "yes", "by": by, "at": "2026-11-06T09:00:00Z"}) + "\n",
                         encoding="utf-8")
        _commit(proj)
        assert _gate(proj, pbase, author=author, head_ref="sync/m-1")[0] == want, by
        _git(proj, "reset", "-q", "--hard", pbase)


def _landed(root: Path, rel: str = "knowledge/domains/pilots/p-0000d201.md") -> None:
    import pages
    write_page(root, rel, "A claim.\n", id="p-0000d201", type="concept", status="stable", subject="pilots",
               signed_by="Ada Okafor", signed_in="bea", signed_at="2026-11-05T09:00:00Z",
               fingerprint=pages.fingerprint("A claim.\n"), landed_from="bea")


def test_a_withdrawal_waits_for_the_click_on_any_branch(tmp_path):
    import landing
    import pages
    root = make_space(tmp_path / "ada", owner="Ada Okafor", name="ada", subjects=[])
    _landed(root)
    base = _init(root)
    landing.retract(root, "p-0000d201", "Ada Okafor", "wrong")     # the person's own word: a page and a signal
    _commit(root)
    # a cloud shell pushes as its person to any branch name it picks: no branch or author vouches
    for ref in ("cloud/daily", "sync/m-1", "ada/notes", "cloudy"):
        cls, reasons = _gate(root, base, author="adaokafor", head_ref=ref)
        assert cls == "protected" and any(r.startswith("knowledge/domains/pilots/p-0000d201.md") for r in reasons) \
            and any(r.startswith("_outbound/signals/") for r in reasons), (ref, reasons)
    _git(root, "reset", "-q", "--hard", base)
    # a `retracted` flag alone on a page left stable waits too, so a signal cannot ride in on a later pull request
    rel = root / "knowledge/domains/pilots/p-0000d201.md"
    fm, body = pages.read(rel)
    pages.write(rel, dict(fm, retracted=True), body)
    _commit(root)
    assert _gate(root, base, head_ref="sync/m-1")[0] == "protected"
    base2 = _commit(root, "suppose it landed anyway")
    (root / "_outbound/signals").mkdir(parents=True, exist_ok=True)
    (root / "_outbound/signals/s-1.yaml").write_text(yaml.safe_dump(
        {"id": "p-0000e301", "kind": "sample_failed", "item": "p-0000d201", "destination": "bea"}), encoding="utf-8")
    _commit(root)
    assert _gate(root, base2, head_ref="sync/m-1")[0] == "protected"   # the base page is still stable
    _git(root, "reset", "-q", "--hard", base)
    # a move keeps the page signed: no withdrawal (page_reasons judges it at its new path)
    own = make_space(tmp_path / "ada2", owner="Ada Okafor", name="ada", subjects=[])
    _stable(own, "Ada Okafor", "ada")
    obase = _init(own)
    target = own / "knowledge/topics/pilots/p-1a2b3c4d.md"
    target.parent.mkdir(parents=True)
    (own / "knowledge/domains/pilots/p-1a2b3c4d.md").rename(target)
    _commit(own)
    cls, reasons = _gate(own, obase, author="adaokafor", head_ref="sync/m-1")
    assert cls == "auto-mergeable", reasons
    # a cloud run deleting it, or a forged sample_failed signal on its own, waits too
    (root / "knowledge/domains/pilots/p-0000d201.md").unlink()
    _commit(root)
    assert _gate(root, base, head_ref="cloud/daily")[0] == "protected"
    _git(root, "reset", "-q", "--hard", base)
    (root / "_outbound/signals").mkdir(parents=True, exist_ok=True)
    (root / "_outbound/signals/s-1.yaml").write_text(yaml.safe_dump(
        {"id": "p-0000e301", "kind": "sample_failed", "item": "p-0000d201", "destination": "bea"}), encoding="utf-8")
    _commit(root)
    assert _gate(root, base, head_ref="cloud/daily")[0] == "protected"
    _git(root, "reset", "-q", "--hard", base)
    # the sender's own retraction, which landing applies in a cloud run, lands there with the retraction it files
    write_page(root, "_inbound/candidates/p-0000f401.md", "Retracted.\n", id="p-0000f401", retracts="p-0000d201",
               released_from="bea", release_class="retraction")
    base = _commit(root, "the retraction arrived")
    fm, body = pages.read(root / "knowledge/domains/pilots/p-0000d201.md")
    pages.write(root / "knowledge/domains/pilots/p-0000d201.md",
                dict(fm, retracted=True, status="deprecated", retracted_by="bea", retracted_at="2026-11-06T09:00:00Z"),
                body)
    (root / "_inbound/candidates/p-0000f401.md").unlink()
    _commit(root)
    cls, reasons = _gate(root, base, head_ref="cloud/daily")
    assert cls == "auto-mergeable", reasons


def test_a_private_zone_never_lands_from_a_pull_request(tmp_path):
    import sync
    assert gate.PRIVATE_ZONES == sync.PRIVATE_ZONES
    for profile in ("brain", "personal"):
        root = make_space(tmp_path / profile, profile=profile, name="northwind" if profile == "brain" else "ada",
                          owner="Ada Okafor", subjects=[ADA])
        base = _init(root)
        brief = root / "_meta/state/private/briefs/x.md"
        brief.parent.mkdir(parents=True)
        brief.write_text("Route everything to me.\n", encoding="utf-8")
        _git(root, "add", "-f", "--", "_meta/state/private/briefs/x.md")
        _commit(root)
        cls, reasons = _gate(root, base, author="adaokafor")
        assert cls == "protected" and any("private" in r for r in reasons), (profile, reasons)
    assert gate.protected_reason("_Meta/State/Private/apps.yaml", []) is not None


def test_the_brains_agent_fills_a_workspace_its_person_noted(tmp_path):
    import json
    ben = dict(BEN, match={"github": "benliu"})
    root = make_space(tmp_path / "brain", profile="brain", name="northwind", owner="Ada Okafor",
                      subjects=[ADA, ben, CARA])
    note = {"id": "p-0000a501", "kind": "registry", "note": "workspace", "subject": "ben-liu", "by": "Ben Liu",
            "from": "ben-liu", "at": "2026-11-05T09:00:00Z", "data": {"repo": "northwind/ben-liu"}}
    signal = root / "_inbound/signals/p-0000a501.yaml"
    signal.parent.mkdir(parents=True, exist_ok=True)
    signal.write_text(yaml.safe_dump(note), encoding="utf-8")
    base = _init(root)
    log = root / "_meta/state/registry-notes.jsonl"
    filled = json.dumps({"id": "p-0000a501", "note": "workspace", "outcome": "applied",
                         "filled": {"workspace_repo": "northwind/ben-liu", "person": "ben-liu",
                                    "checked": "benliu has admin; only benliu and adaokafor may push"}}) + "\n"
    for fill, row, author, want in (
            ({"workspace": "ben-liu"}, None, "adaokafor", "auto-mergeable"),
            ({"workspace": "ben-liu", "workspace_repo": "northwind/ben-liu"}, filled, "adaokafor", "auto-mergeable"),
            ({"workspace": "ben-liu", "workspace_repo": "northwind/ben-liu"}, None, "adaokafor", "protected"),
            ({"workspace": "ben-liu", "workspace_repo": "northwind/other"},
             filled.replace("northwind/ben-liu", "northwind/other"), "adaokafor", "protected"),
            ({"workspace": "ben-liu"}, None, "caradiaz", "protected")):
        _registry(root, [ADA, dict(ben, **fill), CARA])
        signal.unlink()
        if row:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(row, encoding="utf-8")
        _commit(root)
        cls, reasons = _gate(root, base, author=author, head_ref="sync/m-1")
        assert cls == want, (fill, author, reasons)
        _git(root, "reset", "-q", "--hard", base)
    # no note from that person on main: another row given the slug, or a note the pull request brings itself
    _registry(root, [ADA, ben, dict(CARA, workspace="ben-liu")])
    _commit(root)
    assert _gate(root, base, author="adaokafor", head_ref="sync/m-1")[0] == "protected"
    _git(root, "reset", "-q", "--hard", base)
    fresh = make_space(tmp_path / "b2", profile="brain", name="northwind", owner="Ada Okafor", subjects=[ADA, ben])
    fbase = _init(fresh)
    _registry(fresh, [ADA, dict(ben, workspace="ben-liu")])
    (fresh / "_inbound/signals").mkdir(parents=True, exist_ok=True)
    (fresh / "_inbound/signals/p-0000a501.yaml").write_text(yaml.safe_dump(note), encoding="utf-8")
    _commit(fresh)
    assert _gate(fresh, fbase, author="adaokafor", head_ref="sync/m-1")[0] == "protected"


def test_a_release_from_or_into_a_project_space_lands_from_its_peoples_logins(tmp_path):
    # into a project space, whose registry holds no people rows: the sender's login from space.yaml's `logins`
    proj = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ada Okafor",
                      audience=["Ada Okafor", "Ben Liu"], logins={"Ben Liu": "benliu"},
                      subjects=[{"id": "pilots", "kind": "projects", "title": "Pilots", "home": "harbor"}])
    (proj / ".github").mkdir(exist_ok=True)
    (proj / ".github" / "CODEOWNERS").write_text("/space.yaml @adaokafor\n", encoding="utf-8")
    base = _init(proj)
    for slug, author, want in (("ben-liu", "benliu", "auto-mergeable"), ("ada-okafor", "adaokafor", "auto-mergeable"),
                               ("ben-liu", "adaokafor", "protected"), ("ben-liu", "eve-x", "protected")):
        write_page(proj, "_inbound/candidates/p-0000ab02.md", "A release.\n", id="p-0000ab02", released_from=slug,
                   subject="pilots")
        _commit(proj)
        cls, reasons = _gate(proj, base, author=author, head_ref=f"release/{slug}-0123abcd")
        assert cls == want, (slug, author, reasons)
        _git(proj, "reset", "-q", "--hard", base)
    # from a project space into the brain: the brain owner's login, and the project's owner's and crew's
    brain = make_space(tmp_path / "brain", profile="brain", name="northwind", owner="Ada Okafor", subjects=[
        dict(ADA, workspace="ada-okafor"), dict(BEN, workspace="ben-liu", match={"github": "benliu"}),
        dict(CARA, workspace="cara-diaz", match={"github": "caradiaz"}),
        {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor", "owner": "Ada Okafor",
         "crew": ["ben-liu"]},
        {"id": "pilots", "kind": "topics", "title": "Pilots", "home": "northwind"}])
    bbase = _init(brain)
    write_page(brain, "_inbound/candidates/p-0000ab03.md", "A release.\n", id="p-0000ab03", released_from="harbor",
               subject="pilots")
    _commit(brain)
    for author, want in (("adaokafor", "auto-mergeable"), ("benliu", "auto-mergeable"), ("caradiaz", "protected"),
                         ("eve-x", "protected")):
        cls, reasons = _gate(brain, bbase, author=author, head_ref="release/harbor-0123abcd")
        assert cls == want, (author, reasons)


def test_a_colleague_cannot_file_a_relayed_signature_before_landing_runs(tmp_path):
    """A release sits unmarked in main until landing runs; a signature is a hash anyone computes. A
    colleague relays a page 'signed by' another colleague, then deletes the candidate and writes the
    stable page: that waits for the click, as landing would have checked the signer's own history."""
    import pages
    import sign
    ben = dict(BEN, workspace="ben-liu", match={"github": "benliu"})
    cara = dict(CARA, workspace="cara-diaz", match={"github": "caradiaz"})
    pilots = {"id": "pilots", "kind": "domains", "title": "Pilots", "owner": "Cara Diaz"}
    root, base = _brain(tmp_path, [ADA, ben, cara, pilots])
    body = "Pilots are cancelled.\n"
    for signer, signed_in, want in (("Cara Diaz", "cara-diaz", "protected"), ("Ben Liu", "ben-liu", "auto-mergeable")):
        _git(root, "reset", "-q", "--hard", base)
        fm = dict(id="p-0000aa01", type="concept", title="Pilots", subject="pilots", kind="domains", status="stable",
                  signed_by=signer, signed_in=signed_in, signed_at="2026-11-05T09:00:00Z",
                  fingerprint=pages.fingerprint(body))
        fm["signed_fields"] = sign.fields_fingerprint(fm)
        write_page(root, "_inbound/candidates/p-0000aa01.md", body, **fm, released_from="ben-liu", released_by="Ben Liu")
        _commit(root)
        assert _gate(root, base, author="benliu", head_ref="release/ben-liu-0123abcd")[0] == "auto-mergeable"
        held = _git(root, "rev-parse", "HEAD")
        (root / "_inbound/candidates/p-0000aa01.md").unlink()
        write_page(root, "knowledge/domains/pilots/p-0000aa01.md", body, **fm, landed_from="ben-liu",
                   released_from="ben-liu", released_by="Ben Liu")   # as landing files it
        _commit(root)
        cls, reasons = _gate(root, held, author="benliu", head_ref="ben/notes")
        assert cls == want, (signer, reasons)


def test_a_colleague_files_their_own_release_only_as_landing_would(tmp_path):
    """The filing shortcut holds only for the page landing writes: the candidate's keys (no `supersedes`
    it never carried), at the candidate's own target, and never as a decision."""
    import decisions
    import pages
    import sign
    ben = dict(BEN, workspace="ben-liu", match={"github": "benliu"})
    pilots = {"id": "pilots", "kind": "domains", "title": "Pilots", "owner": "Ada Okafor"}
    root, _ = _brain(tmp_path, [ADA, ben, CARA, pilots])
    ada_body = "Pilots continue in Q1.\n"
    afm = dict(id="d-0000bb02", type="decision", title="Pilots go on", subject="pilots", kind="domains",
               status="stable", signed_by="Ada Okafor", signed_in="northwind", signed_at="2026-11-01T09:00:00Z",
               fingerprint=pages.fingerprint(ada_body))
    afm["signed_fields"] = sign.fields_fingerprint(afm)
    write_page(root, "knowledge/decisions/d-0000bb02.md", ada_body, **afm)
    base = _commit(root)
    body = "Pilots are cancelled.\n"
    fm = dict(id="p-0000aa01", type="concept", title="Pilots", subject="pilots", kind="domains", status="stable",
              signed_by="Ben Liu", signed_in="ben-liu", signed_at="2026-11-05T09:00:00Z",
              fingerprint=pages.fingerprint(body))
    fm["signed_fields"] = sign.fields_fingerprint(fm)
    sent = dict(fm, released_from="ben-liu", released_by="Ben Liu")
    for candidate, rel, extra, want in (
            (sent, "knowledge/domains/pilots/p-0000aa01.md", {}, "auto-mergeable"),
            (sent, "knowledge/decisions/p-0000aa01.md", {"supersedes": "d-0000bb02"}, "protected"),
            (sent, "knowledge/domains/pilots/p-0000aa01.md", {"supersedes": "d-0000bb02"}, "protected"),
            (sent, "knowledge/decisions/p-0000aa01.md", {}, "protected"),
            (dict(sent, supersedes="d-0000bb02"), "knowledge/domains/pilots/p-0000aa01.md", {}, "protected")):
        _git(root, "reset", "-q", "--hard", base)
        write_page(root, "_inbound/candidates/p-0000aa01.md", body, **candidate)
        _commit(root)
        held = _git(root, "rev-parse", "HEAD")
        (root / "_inbound/candidates/p-0000aa01.md").unlink()
        write_page(root, rel, body, **dict(candidate, **extra), landed_from="ben-liu")
        _commit(root)
        cls, reasons = _gate(root, held, author="benliu", head_ref="ben/notes")
        assert cls == want, (rel, extra, reasons)
        if extra.get("supersedes") and "/decisions/" in rel:   # what it would have done on green
            assert {r["id"]: r for r in decisions.scan(root)}["d-0000bb02"].get("superseded_by") == "p-0000aa01"


_PINNED = ("- 2026-10-01T09:00:00Z yes classify-decide-tide by Ada Okafor\n"
           "- 2026-10-02T09:00:00Z no classify-decide-tide by Ada Okafor\n"
           f"- 2026-10-03T09:00:00Z fingerprint p-1a2b3c4d {'f' * 64} by Ada Okafor\n")


@pytest.mark.parametrize("personal", [False, True])
def test_deleting_log_md_is_protected(tmp_path, personal):
    """A pull request that deletes log.md is judged: the next one could re-add it reordered, and
    status.verdicts reads pinned lines by their first place in the log."""
    import status
    import sync
    from test_signing_key import _git as _sgit
    if personal:   # the keyed opt-in: the base holds .github/allowed_signers, the push is unsigned
        root = make_space(tmp_path / "pilot", profile="personal", name="pilot", owner="Ada Okafor",
                          subjects=[dict(ADA)])
        _sgit(root, "init", "-q", "-b", "main")
        assert sync.make_signing_key(root) == sync.OK
        author, ref = "adaokafor", "cloud/daily"
    else:          # a registered colleague who is not the owner
        root, _ = _brain(tmp_path, [ADA, dict(BEN, workspace="ben-liu", match={"github": "benliu"}), CARA])
        author, ref = "benliu", "ben/notes"
    (root / "log.md").write_text("# Log\n" + _PINNED, encoding="utf-8", newline="\n")
    assert status.pin_cutover(root)
    base = _commit(root, "base: history and store")
    (root / "log.md").unlink()
    _commit(root, "drop the history")
    cls, reasons = _gate(root, base, author=author, head_ref=ref)
    assert cls == "protected" and any(r.startswith("log.md:") for r in reasons), reasons


@pytest.mark.parametrize("personal", [False, True])
def test_a_copy_of_a_later_pinned_answer_above_an_earlier_one_is_protected(tmp_path, personal):
    """Every base line kept in order, plus a copy of the later `no` above the earlier `yes`: status.verdicts
    applies a pinned line at its first place, so the copy would flip the owner's answer to `yes`."""
    import status
    import sync
    from test_signing_key import _git as _sgit
    if personal:
        root = make_space(tmp_path / "pilot", profile="personal", name="pilot", owner="Ada Okafor",
                          subjects=[dict(ADA)])
        _sgit(root, "init", "-q", "-b", "main")
        assert sync.make_signing_key(root) == sync.OK
        author, ref = "adaokafor", "cloud/daily"
    else:
        root, _ = _brain(tmp_path, [ADA, dict(BEN, workspace="ben-liu", match={"github": "benliu"}), CARA])
        author, ref = "benliu", "ben/notes"
    yes, no, sig = _PINNED.splitlines(keepends=True)
    (root / "log.md").write_text("# Log\n" + _PINNED, encoding="utf-8", newline="\n")
    assert status.pin_cutover(root)
    base = _commit(root, "base: history and store")
    (root / "log.md").write_text("# Log\n" + no + yes + no + sig + "- 2026-11-06T09:00:00Z a new line\n",
                                 encoding="utf-8", newline="\n")
    _commit(root, "tidy the history")
    assert gate.appended_only(_git(root, "show", f"{base}:log.md"), (root / "log.md").read_text(encoding="utf-8"))
    cls, reasons = _gate(root, base, author=author, head_ref=ref)
    assert cls == "protected" and "log.md: repeats an earlier line; the history is appended to only" in reasons


def test_an_rsa_line_in_allowed_signers_verifies_nothing(tmp_path):
    """An ssh-rsa modulus's low bytes can be chosen, so its key can end in this computer's ed25519 key's end:
    the gate reads only `sync ssh-ed25519 <key>` lines, the one type the sync makes."""
    import sync
    from test_signing_key import PAGE, _commit as _scommit, _git as _sgit, _signed
    root = make_space(tmp_path / "pilot", profile="personal", name="pilot", owner="Ada Okafor")
    _sgit(root, "init", "-q", "-b", "main")
    _scommit(root, "base")
    assert sync.make_signing_key(root) == sync.OK
    other = tmp_path / "rsa_key"
    subprocess.run(["ssh-keygen", "-q", "-t", "rsa", "-b", "2048", "-N", "", "-f", str(other)], check=True)
    forged = " ".join(Path(f"{other}.pub").read_text(encoding="utf-8").split()[:2])
    os.replace(other, sync.signing_key())   # the commit below is signed with the RSA key
    os.replace(f"{other}.pub", f"{sync.signing_key()}.pub")
    (root / ".github").mkdir(exist_ok=True)
    (root / gate.ALLOWED_SIGNERS).write_text(f"sync {forged}\n", encoding="utf-8")
    base = _scommit(root, "a click on the rsa line")
    _signed(root, "Signed with the RSA key.\n")
    _scommit(root, "chore: sync", signed=True)
    plain = tmp_path / "plain_signers"   # git itself verifies the commit against that line
    plain.write_text(f"sync {forged}\n", encoding="utf-8")
    assert subprocess.run(["git", "-c", "gpg.format=ssh", "-c", f"gpg.ssh.allowedSignersFile={plain.as_posix()}",
                           "verify-commit", "HEAD"], cwd=str(root), capture_output=True).returncode == 0
    assert not _KEY_SIGNED(root, base, "HEAD", PAGE)


@pytest.mark.parametrize("personal", [False, True])
def test_taking_log_md_s_last_line_break_away_is_protected(tmp_path, personal):
    """An open last line is continued by the next append (a pull request's or a tool's own), which changes the
    pinned `no`'s fingerprint, so status.verdicts falls back to the earlier `yes`."""
    import status
    import sync
    from test_signing_key import _git as _sgit
    if personal:
        root = make_space(tmp_path / "pilot", profile="personal", name="pilot", owner="Ada Okafor",
                          subjects=[dict(ADA)])
        _sgit(root, "init", "-q", "-b", "main")
        assert sync.make_signing_key(root) == sync.OK
        author, ref = "adaokafor", "cloud/daily"
    else:
        root, _ = _brain(tmp_path, [ADA, dict(BEN, workspace="ben-liu", match={"github": "benliu"}), CARA])
        author, ref = "benliu", "ben/notes"
    yes, no, _sig = _PINNED.splitlines(keepends=True)
    (root / "log.md").write_text("# Log\n" + yes + no, encoding="utf-8", newline="\n")
    assert status.pin_cutover(root)
    base = _commit(root, "base: history and store")
    (root / "log.md").write_text("# Log\n" + yes + no.rstrip("\n"), encoding="utf-8", newline="\n")
    _commit(root, "trim the history")
    cls, reasons = _gate(root, base, author=author, head_ref=ref)
    assert cls == "protected" and \
        "log.md: its last line loses its line break; the history is appended to only" in reasons, reasons
