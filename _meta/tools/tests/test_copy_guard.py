"""A read-only copy of another space takes no write. A mount (a folder that a space.yaml or a
.gitmodules above it lists) and a nested git folder nobody listed are copies: git fetches them and
nothing commits what a tool writes there, so a verdict written into one never reaches its space
and the copy stops following it. Every page write under a copy and every writer run with a copy as
its root is refused before anything is written. Reading a copy stays as it was."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

import access
import chain
import check
import classify
import decisions
import distill
import landing
import librarian
import link
import morning_view
import needs_you
import onboard
import pages
import questions_log
import registry
import request
import route
import sign
import sign_questions
import spaceyaml
import status
import sweep
import template_sync
import tests_run
import transcripts
from conftest import NOW, make_space, mount_brain

GIT = ["git", "-c", "protocol.file.allow=always", "-c", "commit.gpgsign=false", "-c", "user.name=t",
       "-c", "user.email=t@example.test"]
CANDIDATE = ("---\nid: p-00000101\ntype: entity\ntitle: Harbor\nstatus: draft\nsubject: harbor\nkind: projects\n"
             "target: knowledge/projects/harbor/harbor.md\nevidence: [m-1]\n---\nHarbor goes live on 3 November.\n")


def git(cwd: Path, *args: str) -> None:
    subprocess.run([*GIT, *args], cwd=str(cwd), check=True, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(root).parts}


def lists(space: Path, **mount) -> None:
    data = yaml.safe_load((space / "space.yaml").read_text(encoding="utf-8"))
    data["mounts"] = [{"name": "brain", **mount}]
    (space / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def mounted(tmp_path: Path, path: str = "brain", listed: bool = True) -> tuple:
    """Ada's space with a copy of the company's brain inside it, holding one draft that names her.
    The copy is built before the space lists it, as git would have fetched it: plain files."""
    ada = make_space(tmp_path / "ada", name="ada")
    copy = make_space(ada / path, profile="brain", name="lantern")
    (copy / "_inbound/candidates/harbor.md").write_text(CANDIDATE, encoding="utf-8", newline="\n")
    if listed:
        lists(ada, **({} if path == "brain" else {"path": path}))
    return ada, copy


def test_no_page_is_written_inside_a_mount(tmp_path):
    ada, copy = mounted(tmp_path)
    before = snapshot(ada)
    for write in (lambda: pages.write(copy / "knowledge/topics/pricing/rule.md", {"id": "p-0000aaaa"}, "Body.\n"),
                  lambda: pages.write_text(copy / "log.md", "a line\n"),
                  lambda: pages.append_text(copy / "_meta/state/runs.jsonl", "{}\n"),
                  lambda: pages.write_text(copy / "log.md", "a line\n", root=copy)):   # whatever root the caller names
        with pytest.raises(pages.UnsafePath, match="read-only copy of 'brain'"):
            write()
    assert snapshot(ada) == before
    assert pages.copy_of(copy) == "brain" and pages.copy_of(copy / "knowledge") == "brain"


def test_reading_a_mount_stays_as_it_was_so_an_item_answered_there_is_not_asked_again(tmp_path):
    ada = make_space(tmp_path / "ada", name="ada")
    copy = make_space(ada / "brain", profile="brain", name="lantern", owner="Ada Okafor")
    (copy / "_inbound/needs-you/onboard.yaml").write_text(yaml.safe_dump({"items": [
        {"id": "onboard-identity", "kind": "question", "text": "The identity is drafted: sign it"},
        {"id": "onboard-domains", "kind": "question", "text": "Which domains?"}]}), encoding="utf-8")
    # answered in that space's own checkout, and fetched with the rest
    status.record_verdict(copy, "onboard-domains", "Ada Okafor", verb="yes", at="2026-10-12T09:00:00Z")
    lists(ada)
    assert status._store(copy)[1] == "present" and "onboard-domains" in needs_you.answered_ids(copy)
    mine = {i.id: i for i in needs_you.collect(ada, now=NOW, online=False)}
    assert "onboard-domains" not in mine and mine["onboard-identity"].text.startswith("[brain]")


def test_a_mount_at_a_path_of_its_own_and_one_only_gitmodules_lists_are_copies(tmp_path):
    ada, copy = mounted(tmp_path, path="mounts/company")
    with pytest.raises(pages.UnsafePath, match="read-only copy of 'brain'"):
        pages.write_text(copy / "log.md", "a line\n")
    ben, held = mounted(tmp_path / "ben", path="mounts/lantern", listed=False)
    (ben / ".gitmodules").write_text('[submodule "mounts/lantern"]\n\tpath = mounts/lantern\n\turl = x\n',
                                     encoding="utf-8")
    with pytest.raises(pages.UnsafePath, match="read-only copy of 'lantern'"):
        pages.write_text(held / "log.md", "a line\n")


def test_the_space_itself_and_a_brain_kept_beside_it_stay_writable(tmp_path):
    ada, copy = mounted(tmp_path)
    pages.write_text(ada / "work/notes.md", "mine\n")
    pages.write_text(ada / "brainstorm/notes.md", "a folder that only starts like the mount\n")
    assert pages.copy_of(ada) is None
    ben = make_space(tmp_path / "ben", name="ben")
    brain = make_space(tmp_path / "lantern", profile="brain", name="lantern")
    mount_brain(ben, brain)   # the mount is the brain's own checkout, beside the space: no copy
    pages.write_text(brain / "log.md", "a line\n")
    assert pages.copy_of(brain) is None
    assert status.run(brain, now=NOW) is not None


def test_a_session_worktree_is_writable_and_a_nested_git_folder_nobody_listed_is_not(tmp_path):
    ada = make_space(tmp_path / "ada", name="ada")
    tool = tmp_path / "tool"
    for repo in (ada, tool):
        repo.mkdir(exist_ok=True)
        git(repo, "init", "-q", "--initial-branch=main")
        (repo / "readme.md").write_text("x\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "one")
    git(ada, "worktree", "add", "-q", ".claude/worktrees/w")
    pages.write_text(ada / ".claude/worktrees/w/work/notes.md", "a session's own checkout\n")
    assert pages.copy_of(ada / ".claude/worktrees/w") is None
    git(ada, "submodule", "add", "-q", str(tool), "mounts/tool")
    (ada / ".gitmodules").unlink()   # neither space.yaml nor .gitmodules lists it
    assert pages.copy_of(ada / "mounts/tool") == "tool"
    with pytest.raises(pages.UnsafePath, match="read-only copy of 'tool'"):
        pages.write_text(ada / "mounts/tool/notes.md", "x\n")
    assert not (ada / "mounts/tool/notes.md").exists()


WRITERS = {
    "sign.apply": lambda root: sign.apply(root, {"p-00000101": ("yes", "")}, by="Ada Okafor", now=NOW),
    "status.run": lambda root: status.run(root, now=NOW),
    "landing.land": lambda root: landing.land(root, now=NOW),
    "landing.retract": lambda root: landing.retract(root, "p-00000101", by="Ada Okafor", now=NOW),
    "distill.run": lambda root: distill.run(root, now=NOW),
    "distill.checked": lambda root: distill.checked(root, "harbor", by="Ada Okafor", now=NOW),
    "sweep.run": lambda root: sweep.run(root, now=NOW),
    "check.run": lambda root: check.run(root, now=NOW),
    "route.record": lambda root: route.record(root, {"record": "m-1", "decision": "private"}, now=NOW),
    "route.answer": lambda root: route.answer(root, "m-1", True, by="Ada Okafor", now=NOW),
    "route.retract": lambda root: route.retract(root, "m-1", by="Ada Okafor", now=NOW),
    "request.ask": lambda root: request.ask(root, "harbor", "status", "Where does it stand?", now=NOW),
    "request.collect": lambda root: request.collect(root, now=NOW),
    "request.answer": lambda root: request.answer(root, now=NOW),
    "librarian.run": lambda root: librarian.run(root, now=NOW),
    "librarian.apply": lambda root: librarian.apply(root, "k-1", "Ben Liu", now=NOW),
    "librarian.answer": lambda root: librarian.answer(root, "k-1", "Ada Okafor", now=NOW),
    "classify.apply_verdicts": lambda root: classify.apply_verdicts(root, now=NOW),
    # the tools that write files by other means than the page writers
    "registry.save": lambda root: registry.Registry(root, spaceyaml.load(root)).save(),
    "classify.pending": lambda root: classify.pending(root, now=NOW),
    "classify._save_items": lambda root: classify._save_items(root, []),
    "classify._propose": lambda root: classify._propose(root, "c-1", {}),
    "questions_log.append": lambda root: questions_log.append(root, "Where does Harbor stand?", "harbor",
                                                              "signed", [], now=NOW),
    "decisions.build": lambda root: decisions.build(root),
    "transcripts.scan": lambda root: transcripts.scan(root, now=NOW),
    "link.check": lambda root: link.check(root, now=NOW),
    "link._needs_you": lambda root: link._needs_you(root, None),
    "access.write_needs_you": lambda root: access.write_needs_you(root, access.AccessReport()),
    "tests_run.set_aside": lambda root: tests_run.set_aside(root, now=NOW),
    "tests_run.hold": lambda root: tests_run.hold(root, {"Where does Harbor stand?": ["3 November"]}, "sync", now=NOW),
    "tests_run.probe": lambda root: tests_run.probe(root, now=NOW),
    "morning_view.run": lambda root: morning_view.run(root, "Ada Okafor", now=NOW),
    "template_sync._copy": lambda root: template_sync._copy(root / "space.yaml", root / "rules.md", root),
    "sign_questions.ask": lambda root: sign_questions.ask(root, "harbor", "p-00000101", "Is the date final?", "the page says so",
                                                         "yes", [], "Ada Okafor", NOW),
}


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_a_writer_run_with_a_mount_as_its_root_stops_and_writes_nothing(tmp_path, name):
    ada, copy = mounted(tmp_path)
    before = snapshot(ada)
    with pytest.raises(pages.UnsafePath, match="read-only copy of 'brain'"):
        WRITERS[name](copy)
    assert snapshot(ada) == before


def test_the_sign_command_says_so_in_one_line_for_a_read_back_and_an_apply(tmp_path, capsys):
    ada, copy = mounted(tmp_path)
    verdicts = tmp_path / "verdicts.yaml"
    verdicts.write_text("p-00000101: yes\n", encoding="utf-8")
    before = snapshot(ada)
    for argv in ([str(copy), "read-back", "p-00000101"],
                 [str(copy), "apply", "--by", "Ada Okafor", "--verdicts", str(verdicts)]):
        assert sign.main(argv) == 1
        said = capsys.readouterr()
        assert said.err.startswith("error: ") and "read-only copy of 'brain'" in said.err
        assert "own checkout" in said.err and said.out == ""
    assert snapshot(ada) == before


def test_the_checkout_where_a_mounted_spaces_item_is_answered(tmp_path, capsys):
    ada, copy = mounted(tmp_path)
    assert chain.checkout_of(ada, "brain") is None   # this computer has none: the verdict becomes a request
    assert chain.main(["checkout", str(ada), "brain"]) == 1
    assert "request" in capsys.readouterr().err
    onboard.remember_space(copy)   # a copy on the machine's list by mistake is still no checkout
    assert chain.checkout_of(ada, "brain") is None
    real = make_space(tmp_path / "lantern", profile="brain", name="lantern")
    other = make_space(tmp_path / "harbor", profile="project", name="harbor")
    for root in (other, real):
        onboard.remember_space(root)
    assert chain.checkout_of(ada, "brain") == real.resolve()
    assert chain.checkout_of(ada, "harbor") is None   # no such mount here
    assert chain.main(["checkout", str(ada), "brain"]) == 0
    assert capsys.readouterr().out.strip() == real.resolve().as_posix()


def test_a_mount_kept_beside_its_space_is_the_checkout_itself(tmp_path):
    ben = make_space(tmp_path / "ben", name="ben")
    beside = make_space(tmp_path / "company", profile="brain", name="lantern")   # on no list of this machine
    mount_brain(ben, beside)
    assert chain.checkout_of(ben, "brain") == beside.resolve()


def test_two_spaces_of_one_name_are_told_apart_by_the_repository_the_mount_names(tmp_path):
    ada, copy = mounted(tmp_path)
    lists(ada, repo="lantern-works/brain")
    for folder, origin in (("old", "https://github.com/lantern-archive/brain.git"),
                           ("live", "https://github.com/Lantern-Works/brain.git")):
        root = make_space(tmp_path / folder, profile="brain", name="lantern")
        git(root, "init", "-q", "--initial-branch=main")
        git(root, "remote", "add", "origin", origin)
        onboard.remember_space(root)
    assert chain.checkout_of(ada, "brain") == (tmp_path / "live").resolve()
