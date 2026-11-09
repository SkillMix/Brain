"""Security, second round: the cross-file changes the fix and check agents asked for.

- The registry check reports a person's `workspace_repo` that is not a GitHub repository, or one set
  without `workspace`: release.trust_repo ignores such a value, and this makes the mistake visible.
- A new space begins its verdict store with an empty cut-over as soon as its log.md is written, and
  template sync pins the cut-over by running the migration it just copied, never in a cloud run.
- A space made from a template other than the official one records it in
  `_meta/state/template-origin.yaml`, which template sync follows; a space's own remote is never
  taken for its template.
- `status.py close` refuses itself inside a routine or a cloud run, as the other person-only tools do.
- A cloud task refuses a glob or a brace in a command of the tools that hold a person's verb, and any
  shell command that names the run's marker.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

import cloud
import onboard
import registry
import routine
import spaceyaml
import status
import template_sync
from conftest import make_space

OFFICIAL = template_sync.OFFICIAL_TEMPLATE

BS = "\\"


def people(root: Path, **ada) -> None:
    entry = {"id": "ada-okafor", "kind": "people", "title": "Ada Okafor", **ada}
    (root / "_meta" / "subjects.yaml").write_text(yaml.safe_dump({"subjects": [entry]}), encoding="utf-8")


# ----- the registry reports a workspace_repo that would vouch for nothing ----------------------------------------
@pytest.mark.parametrize("value", ["https://example.test/lantern/ada", "../ada-workspace", "C:/repos/ada",
                                   "-u touch PWNED", "lantern/ada\nx"])
def test_a_workspace_repo_that_is_no_github_repository_is_reported(tmp_path, value):
    root = make_space(tmp_path / "brain", profile="brain", name="brain")
    people(root, workspace="ada", workspace_repo=value)
    errs = registry.Registry(root, spaceyaml.load(root)).check()
    assert "registry: `ada-okafor` workspace_repo is not a GitHub repository" in errs


def test_a_workspace_repo_without_a_workspace_is_reported(tmp_path):
    root = make_space(tmp_path / "brain", profile="brain", name="brain")
    people(root, workspace_repo="lantern/ada-workspace")
    errs = registry.Registry(root, spaceyaml.load(root)).check()
    assert "registry: `ada-okafor` workspace_repo is set without workspace" in errs


@pytest.mark.parametrize("value", ["lantern/ada-workspace", "https://github.com/lantern/ada-workspace",
                                   "git@github.com:lantern/ada-workspace.git"])
def test_a_github_workspace_repo_beside_its_workspace_is_no_error(tmp_path, value):
    root = make_space(tmp_path / "brain", profile="brain", name="brain")
    people(root, workspace="ada", workspace_repo=value)
    assert not [e for e in registry.Registry(root, spaceyaml.load(root)).check() if "workspace" in e]


# ----- a new space's verdict store begins with an empty cut-over --------------------------------------------------
def test_a_new_space_begins_its_store_with_an_empty_cutover(tmp_path):
    root = make_space(tmp_path / "harbor", profile="project", name="harbor")
    assert onboard.begin_verdicts(root) is True
    [row] = [json.loads(line) for line in (root / status.VERDICTS).read_text(encoding="utf-8").splitlines()]
    assert row["cutover"] == "log.md" and row["fingerprints"] == []
    with (root / "log.md").open("a", encoding="utf-8") as fh:   # a line written before the first status run
        fh.write("- 2026-11-05T09:00:00Z yes p-1a2b3c4d by Ada Okafor\n")
    assert status.verdicts(root) == {}
    assert onboard.begin_verdicts(root) is False   # pinned once, never moved


def test_setup_space_pins_only_a_space_it_fills_for_the_first_time(tmp_path, monkeypatch):
    pinned = []
    monkeypatch.setattr(onboard, "begin_verdicts", lambda dst: pinned.append(Path(dst)) or True)
    fresh = tmp_path / "harbor"
    fresh.mkdir()
    (fresh / "space.yaml").write_text(onboard.FRESH["space.yaml"], encoding="utf-8")
    values = onboard.space_values({}, "harbor", "Ada Okafor", "ada@example.test", ["Ada Okafor"], "brain")
    onboard.setup_space(onboard.Report("project"), fresh, "project", values, [])
    assert pinned == [fresh] and (fresh / "log.md").is_file()
    configured = make_space(tmp_path / "beacon", profile="project", name="beacon")
    onboard.setup_space(onboard.Report("project"), configured, "project", values, [])
    assert pinned == [fresh]


def stub_migration(root: Path) -> Path:
    script = root / template_sync.CUTOVER_MIGRATION
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("import pathlib, sys\n"
                      "store = pathlib.Path(sys.argv[1]) / '_meta' / 'state' / 'verdicts.jsonl'\n"
                      "store.parent.mkdir(parents=True, exist_ok=True)\n"
                      "store.write_text('{\"cutover\": \"log.md\", \"fingerprints\": []}\\n')\n", encoding="utf-8")
    return script


def test_template_sync_pins_by_running_the_copied_migration(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
    root = make_space(tmp_path / "ada")
    stub_migration(root)
    assert template_sync.pin_verdicts(root) is True
    assert (root / status.VERDICTS).is_file()
    (root / status.VERDICTS).write_text("kept\n", encoding="utf-8")
    assert template_sync.pin_verdicts(root) is False   # a store that has begun is never touched
    assert (root / status.VERDICTS).read_text(encoding="utf-8") == "kept\n"


def test_template_sync_never_pins_in_a_cloud_run_or_without_the_migration(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada")
    assert template_sync.pin_verdicts(root) is False   # no migration in this space
    stub_migration(root)
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    assert template_sync.pin_verdicts(root) is False
    assert not (root / status.VERDICTS).exists()


def test_the_real_migration_pins_a_space_after_a_sync(tmp_path, monkeypatch):
    """The migration the template ships, run as template sync runs it, pins the log's earlier answers."""
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
    monkeypatch.delenv(routine.ROUTINE_MARK, raising=False)
    here = Path(template_sync.__file__).resolve().parents[2]
    root = make_space(tmp_path / "ada")
    for rel in ("_meta/tools", "_meta/migrations"):
        for path in (here / rel).glob("*.py"):
            target = root / rel / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    with (root / "log.md").open("a", encoding="utf-8") as fh:
        fh.write("- 2026-11-01T09:00:00Z yes p-1a2b3c4d by Ada Okafor\n")
    assert template_sync.pin_verdicts(root) is True
    assert status.verdicts(root)["p-1a2b3c4d"]["verb"] == "yes"


# ----- a space made from another template keeps following it -----------------------------------------------------
@pytest.mark.parametrize("tpl", ["lantern/brain-template", "https://github.com/lantern/brain-template.git"])
def test_a_space_made_from_another_template_records_it_and_template_sync_follows(tmp_path, monkeypatch, tpl):
    root = make_space(tmp_path / "ada")
    monkeypatch.setattr(template_sync, "made_from", lambda r: pytest.fail("GitHub is never asked"))
    assert onboard.record_template_origin(root, tpl) is True
    assert yaml.safe_load((root / template_sync.ORIGIN).read_text(encoding="utf-8")) == {"repo": "lantern/brain-template"}
    assert template_sync.pinned(spaceyaml.load(root)) == "lantern/brain-template"


@pytest.mark.parametrize("tpl", [OFFICIAL, OFFICIAL.lower(), None, "", "C:/templates/brain",
                                 "/srv/brain-template", "https://example.test/lantern/brain"],
                         ids=["official", "official-lower", "none", "empty", "windows-path", "posix-path", "other-host"])
def test_the_official_template_or_a_local_copy_records_nothing(tmp_path, tpl):
    root = make_space(tmp_path / "ada")
    assert onboard.record_template_origin(root, tpl) is False
    assert not (root / template_sync.ORIGIN).exists()


def test_a_dry_run_records_no_template(tmp_path):
    root = make_space(tmp_path / "ada")
    assert onboard.record_template_origin(root, "lantern/brain-template", dry_run=True) is False
    assert not (root / template_sync.ORIGIN).exists()


def test_an_offline_copy_records_the_template_it_was_made_from(tmp_path):
    src = make_space(tmp_path / "template", profile="personal", name="template")
    report = onboard.Report("company")
    dst, _ = onboard.create_space(report, src, "harbor", "harbor", True, None, tmp_path / "spaces",
                                  "lantern/brain-template", False)
    assert yaml.safe_load((dst / template_sync.ORIGIN).read_text(encoding="utf-8")) == {"repo": "lantern/brain-template"}


def remote(root: Path, url: str) -> None:
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / ".git" / "config").write_text(f'[remote "origin"]\n\turl = {url}\n', encoding="utf-8")


def test_a_spaces_own_remote_is_never_its_template(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(onboard, "run_cmd", lambda argv, *a, **k: subprocess.CompletedProcess(argv, 1, "", "offline"))
    space = make_space(tmp_path / "ada")
    remote(space, "https://github.com/lantern/ada-workspace.git")
    assert onboard.template_repo(space) is None
    assert onboard.template_repo(space, offline=True) is None
    copy = tmp_path / "template-copy"   # the copy the agent cloned of the template: its remote is the template
    copy.mkdir()
    (copy / "space.yaml").write_text("shape: 1\nprofile: template\n", encoding="utf-8")
    remote(copy, "https://github.com/lantern/brain-template.git")
    assert onboard.template_repo(copy, offline=True) == "lantern/brain-template"


# ----- status.py close is the person's word ----------------------------------------------------------------------
@pytest.mark.parametrize("marker", ["mark", "cloud"])
def test_status_close_refuses_inside_a_routine(tmp_path, monkeypatch, marker, capsys):
    root = make_space(tmp_path / "ada")
    if marker == "mark":
        monkeypatch.setenv(routine.ROUTINE_MARK, "daily-chain")
    else:
        monkeypatch.delenv(routine.ROUTINE_MARK, raising=False)
        cloud.state_path().parent.mkdir(parents=True, exist_ok=True)
        cloud.state_path().write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="routine|cloud run"):
        status.close(root, "harbor", "p-0000ee11", by="Ada Okafor")
    assert status.main([str(root), "close", "harbor", "p-0000ee11", "--by", "Ada Okafor"]) == 1
    assert "refused" in capsys.readouterr().err
    assert not (root / status.CLOSED).exists()


def test_status_close_still_runs_for_the_person(tmp_path, monkeypatch):
    monkeypatch.delenv(routine.ROUTINE_MARK, raising=False)
    root = make_space(tmp_path / "ada")
    with pytest.raises(ValueError, match="no evidence page"):   # past the refusal, to the page that is not there
        status.close(root, "harbor", "p-0000ee11", by="Ada Okafor")


# ----- a cloud task: no glob or brace splice, and the marker stays where it is -----------------------------------
def test_a_cloud_task_refuses_a_glob_or_brace_splice_and_any_command_on_its_marker(tmp_path):
    import bash_guard
    ws = make_space(tmp_path / "ada-space")
    denied = bash_guard._patterns(cloud.disallowed(ws, ["ada/ada-space"], [], []), False)
    tools = "/home/user/ada-space/_meta/tools"
    for command in (f"python3 {tools}/route.py /home/user/ada-space answe? m-1 --yes --by Ada",
                    f"python3 {tools}/route.py /home/user/ada-space [a]nswer m-1 --yes --by Ada",
                    f"python3 {tools}/librarian.py /home/user/ada-space appl{{y,}} lib-1 --reviewed-by x",
                    f"python3 {tools}/chain.py virtua? --days 1 --roots /home/user/ada-space",
                    f"python3 {tools}/status.py /home/user/ada-space clos? harbor i-1",
                    "rm ~/.spaces/routines/cloud.json", "mv /home/user/.spaces/routines/cloud.json /tmp/x",
                    "echo {} > ~/.spaces/routines/cloud.json"):
        assert any(p.fullmatch(command) for p in denied), command
    for command in (f"python3 {tools}/chain.py sweep /home/user/ada-space --cloud",
                    f"python3 {tools}/status.py /home/user/ada-space",
                    f"python3 {tools}/route.py /home/user/ada-space record _meta/state/private/decisions/*.yaml"):
        assert not any(p.fullmatch(command) for p in denied), command


def test_the_splice_signs_cover_every_tool_that_holds_a_verb():
    for tool in ("librarian.py", "chain.py", "route.py", "status.py"):
        for sign in ("'", '"', BS, "$", "?", "[", "{"):
            assert f"{tool}*{sign}" in cloud.PERSON_ONLY_SPLICE
    assert sys.modules["cloud"].MARKER_DENY
