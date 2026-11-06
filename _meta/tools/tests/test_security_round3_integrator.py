"""Round 3, the integrator: the requests the five groups left for each other, wired together. A key
note lands in the brain beside the workspace's own note, the daily chain keeps the mount keys, a
page its subject's owner signs in the brain lands, setup tests PyYAML the way the guards import
it, the health page counts what the sync keeps on the computer, and the agent's own upkeep files
are the tools' to write."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import chain
import classify
import gate
import onboard
import registry
import routine
import spaceyaml
import start
import status
from conftest import make_space, write_page

PUBLIC = "ssh-ed25519 " + "A" * 68


def _git(cwd: Path, *args: str) -> str:
    cmd = ["git", "-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.test", *args]
    return subprocess.run(cmd, cwd=str(cwd), check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()


@pytest.fixture(autouse=True)
def quiet_git(tmp_path, monkeypatch):
    empty = tmp_path / "gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _brain(tmp_path, **ben):
    people = [{"id": "ada", "kind": "people", "title": "Ada Okafor", "match": {"github": "ada-gh"}, "workspace": "ada"},
              {"id": "ben", "kind": "people", "title": "Ben Liu", "match": {"github": "ben-lw"}, **ben}]
    return make_space(tmp_path / "brain", profile="brain", name="brain", subjects=people)


def _signal(root: Path, sid: str, note: str, data: dict, at: str) -> None:
    folder = root / classify.SIGNALS_IN
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{note}-{sid}.yaml").write_text(yaml.safe_dump(
        {"id": f"{note}-{sid}", "kind": "registry", "note": note, "subject": sid, "at": at, "by": "Ben Liu",
         "from": "ben", "data": data}, sort_keys=False), encoding="utf-8")


# ----- classify: the `key` note --------------------------------------------------------------------------
def test_a_key_note_in_the_same_batch_as_the_workspace_note_is_queued_for_the_brains_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "merged_note", lambda root, signal: True)   # both notes came through main
    brain = _brain(tmp_path)   # Ben has a login and no workspace yet: his workspace's note is in this batch
    _signal(brain, "ben", "workspace", {}, "2026-11-05T09:00:00Z")
    _signal(brain, "ben", "key", {"consumer": "ben-lw/ben-workspace", "mount": "northwind-co/brain",
                                  "key": PUBLIC}, "2026-11-05T09:01:00Z")
    out = classify.land_notes(brain)
    assert out == {"applied": 2, "refused": 0}
    [request] = list((brain / onboard.KEY_REQUESTS).glob("*.yaml"))
    held = yaml.safe_load(request.read_text(encoding="utf-8"))
    assert held["person"] == "ben" and held["from"] == "ben" and held["key"] == PUBLIC
    assert registry.Registry(brain).subjects["ben"].extra["workspace"] == "ben"


def test_a_key_note_is_the_brains_alone_and_a_stranger_is_refused(tmp_path):
    ws = make_space(tmp_path / "ada", subjects=[{"id": "ben", "kind": "people", "title": "Ben Liu"}])
    _signal(ws, "ben", "key", {"consumer": "ben-lw/ben-workspace", "mount": "northwind-co/brain", "key": PUBLIC},
            "2026-11-05T09:00:00Z")
    assert classify.land_notes(ws) == {"applied": 0, "refused": 1}
    assert not (ws / onboard.KEY_REQUESTS).exists()
    brain = _brain(tmp_path, workspace="ben")
    signal = {"note": "key", "by": "Ben Liu", "from": "mallory", "at": "2026-11-05T09:00:00Z",
              "data": {"consumer": "ben-lw/ben-workspace", "mount": "northwind-co/brain", "key": PUBLIC}}
    reg = registry.Registry(brain)
    assert classify.apply_note(brain, spaceyaml.load(brain), reg, signal, dt.datetime.now(dt.timezone.utc))[0] \
        == "refused"


def test_a_workspace_asks_for_its_key_about_its_own_person_by_registry_id(tmp_path, monkeypatch):
    ws = make_space(tmp_path / "ben", name="ben", owner="Ben Liu",
                    subjects=[{"id": "ben-liu", "kind": "people", "title": "Ben Liu"}])
    asked = []
    monkeypatch.setattr(classify, "note", lambda root, sid, kind, data, **kw: asked.append((sid, kind)))
    assert onboard.request_key(ws, "northwind-co/brain", "ben-lw/ben-workspace", PUBLIC) is None
    assert asked == [("ben-liu", "key")]


# ----- chain: the daily keeps the mount keys -------------------------------------------------------------
def test_the_daily_keeps_the_mount_keys_of_a_live_space_on_github_only(tmp_path, monkeypatch):
    brain = _brain(tmp_path)
    space = spaceyaml.load(brain)
    assert not chain._mount_keys_due(space, brain, cloud=False)   # no repository on GitHub is known
    onboard.write_yaml(brain / "_meta" / "state" / "onboard.yaml", {"case": "company", "repo": "northwind-co/brain"})
    assert chain._mount_keys_due(space, brain, cloud=False)
    assert not chain._mount_keys_due(space, brain, cloud=True)
    fixture = make_space(tmp_path / "fix", profile="brain", mode="fixture")
    onboard.write_yaml(fixture / "_meta" / "state" / "onboard.yaml", {"repo": "northwind-co/fixture"})
    assert not chain._mount_keys_due(spaceyaml.load(fixture), fixture, cloud=False)
    ran = []
    monkeypatch.setattr(onboard, "keys_upkeep", lambda root, **kw: ran.append(Path(root)) or onboard.Report("x"))
    ctx = chain.daily(brain)
    assert ran == [brain] and "mount_keys" in ctx["steps"]


def test_the_daily_prompt_works_the_holds_that_need_the_agent():
    prompt = (Path(chain.__file__).resolve().parents[1] / "templates" / "routines" / "daily-chain.md").read_text(
        encoding="utf-8")
    assert "release.py <root> set-aside <id>" in prompt and "The holds you work through" in prompt
    assert "do not repair\n   a failed step by hand" in prompt


# ----- gate: a page its subject's people sign ----------------------------------------------------------
def _signed(root: Path, by: str, subject: str):
    return write_page(root, "knowledge/domains/pilots/p-1a2b3c4d.md", id="p-1a2b3c4d", type="concept",
                      status="stable", subject=subject, signed_by=by, signed_in="brain",
                      signed_at="2026-11-05T09:00:00Z", fingerprint="0" * 64)


def test_a_page_its_subjects_owner_signs_in_the_brain_lands_and_another_subjects_crew_waits(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", owner="Ada Okafor", subjects=[
        {"id": "ada", "kind": "people", "title": "Ada Okafor"}, {"id": "ben", "kind": "people", "title": "Ben Liu"},
        {"id": "cat", "kind": "people", "title": "Cat Moss"},
        {"id": "pilots", "kind": "domains", "title": "Pilots", "owner": "Ben Liu"},
        {"id": "garden", "kind": "domains", "title": "Garden", "crew": ["cat"]}])
    _git(brain, "init", "-q", "-b", "main")
    base = _commit(brain)
    changes = [(None, None, "A", "knowledge/domains/pilots/p-1a2b3c4d.md")]
    _signed(brain, "Ben Liu", "pilots")   # the domain's owner signs in the brain, whose audience is everyone
    assert gate.page_reasons(brain, base, _commit(brain), changes, "Ada Okafor") == []
    assert not gate._signed_here(yaml.safe_load((brain / "knowledge/domains/pilots/p-1a2b3c4d.md").read_text(
        encoding="utf-8").split("---")[1]), yaml.safe_load((brain / "space.yaml").read_text(encoding="utf-8")))
    _signed(brain, "Cat Moss", "pilots")   # Cat is crew of garden, never of pilots
    assert gate.page_reasons(brain, base, _commit(brain), changes, "Ada Okafor")
    _signed(brain, "Ben Liu", "")   # a page about no subject adds no one
    assert gate.page_reasons(brain, base, _commit(brain), changes, "Ada Okafor")


def _commit(root: Path) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--allow-empty", "-m", "change")
    return _git(root, "rev-parse", "HEAD")


# ----- start: PyYAML as the guards import it ----------------------------------------------------------
def test_setup_tests_pyyaml_the_way_the_routines_guards_import_it(tmp_path):
    calls = []

    def run(argv, **kw):
        calls.append([str(a) for a in argv])
        return subprocess.CompletedProcess(argv, 0, "", "")
    assert start.ensure_yaml([], venv=tmp_path / "venv", run=run, windows=False) is None
    assert calls == [[sys.executable, "-I", "-c", "import yaml"]]
    calls.clear()

    def only_user_site(argv, **kw):   # a `pip --user` install the isolated import never sees
        calls.append([str(a) for a in argv])
        return subprocess.CompletedProcess(argv, 1 if argv[1:3] == ["-I", "-c"] else 0, "", "")
    start.ensure_yaml([], venv=tmp_path / "venv", run=only_user_site, windows=False)
    assert any("venv" in c for c in calls), calls   # on to the venv the profiles then name


# ----- status: the health page counts what the sync keeps on the computer ------------------------------
def test_the_health_line_counts_the_files_the_sync_keeps_on_this_computer(tmp_path):
    import sync
    root = make_space(tmp_path / "ada")
    space = spaceyaml.load(root)
    now = dt.datetime.now(dt.timezone.utc)
    assert "left local" not in status._health_line(space, space.name, root, now)   # nothing kept back, no cell
    (root / sync.LEFT_LOCAL).parent.mkdir(parents=True, exist_ok=True)
    (root / sync.LEFT_LOCAL).write_text(json.dumps({"files": {"work/x.zip": {"since": "2026-10-28T09:00:00Z",
                                                                           "why": ["password"]}}}), encoding="utf-8")
    line = status._health_line(space, space.name, root, now)
    assert "left local 1 (since 2026-10-28" in line and "work/x.zip" not in line
    other = make_space(tmp_path / "brain", profile="brain")
    assert "left local" not in status._health_line(space, "brain", other, now)   # a mount's is its own computer's


# ----- routine: the agent's own upkeep files are the tools' ---------------------------------------------
def test_the_upkeep_files_the_tools_read_back_are_denied_to_a_routines_edits(tmp_path):
    root = make_space(tmp_path / "ada")
    deny = routine.routine_settings(tmp_path, "", [root], "Ada Okafor")["permissions"]["deny"]
    for pattern in ("_meta/state/health/**", "_meta/state/private/key-requests/**",
                    "_meta/state/mount-keys-attached.yaml"):
        assert pattern in routine.TOOL_WRITTEN and f"Edit(//**/{pattern})" in deny
