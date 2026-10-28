from pathlib import Path

import yaml

import template_sync
from conftest import make_space


def make_template(root: Path, tool_text="print('v1')\n", skill_text="# sweep v1\n") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "_meta" / "tools").mkdir(parents=True)
    (root / "_meta" / "tools" / "sweep.py").write_text(tool_text, encoding="utf-8")
    (root / ".claude" / "skills" / "sweep").mkdir(parents=True)
    (root / ".claude" / "skills" / "sweep" / "SKILL.md").write_text(skill_text, encoding="utf-8")
    (root / ".github").mkdir()
    (root / ".github" / "CODEOWNERS").write_text("* @OWNER-LOGIN\n", encoding="utf-8")
    (root / ".github" / "template-marker").write_text("", encoding="utf-8")
    return root


def test_first_sync_adds_machinery_and_records_provenance(tmp_path):
    tpl = make_template(tmp_path / "tpl")
    space = make_space(tmp_path / "ada")
    report = template_sync.run(space, source=str(tpl))
    assert sorted(report.added) == [".claude/skills/sweep/SKILL.md", "_meta/tools/sweep.py"]
    assert (space / "_meta" / "tools" / "sweep.py").read_text(encoding="utf-8") == "print('v1')\n"
    assert not (space / ".github" / "CODEOWNERS").exists()     # instance-specific, never copied
    assert not (space / ".github" / "template-marker").exists()
    prov = yaml.safe_load((space / "_meta" / "state" / "template-version.yaml").read_text(encoding="utf-8"))
    assert "_meta/tools/sweep.py" in prov["files"]


def test_updated_file_is_applied_but_a_local_change_is_kept(tmp_path):
    tpl = make_template(tmp_path / "tpl")
    space = make_space(tmp_path / "ada")
    template_sync.run(space, source=str(tpl))
    # the space changes its skill; the template changes both files
    (space / ".claude" / "skills" / "sweep" / "SKILL.md").write_text("# sweep, our way\n", encoding="utf-8")
    (tpl / "_meta" / "tools" / "sweep.py").write_text("print('v2')\n", encoding="utf-8")
    (tpl / ".claude" / "skills" / "sweep" / "SKILL.md").write_text("# sweep v2\n", encoding="utf-8")
    report = template_sync.run(space, source=str(tpl))
    assert report.updated == ["_meta/tools/sweep.py"]
    assert report.kept == [".claude/skills/sweep/SKILL.md"]
    assert (space / "_meta" / "tools" / "sweep.py").read_text(encoding="utf-8") == "print('v2')\n"
    assert (space / ".claude" / "skills" / "sweep" / "SKILL.md").read_text(encoding="utf-8") == "# sweep, our way\n"
    lines = (space / "_meta" / "state" / "runs.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert '"routine": "template-sync"' in lines[-1]


def test_check_changes_nothing(tmp_path):
    tpl = make_template(tmp_path / "tpl")
    space = make_space(tmp_path / "ada")
    report = template_sync.run(space, source=str(tpl), check=True)
    assert len(report.added) == 2
    assert not (space / "_meta" / "tools" / "sweep.py").exists()
    assert not (space / "_meta" / "state" / "template-version.yaml").exists()


def test_caches_in_the_template_tree_are_not_machinery(tmp_path):
    tpl = make_template(tmp_path / "tpl")
    (tpl / "_meta" / "tools" / ".pytest_cache" / "v").mkdir(parents=True)
    (tpl / "_meta" / "tools" / ".pytest_cache" / "v" / "nodeids").write_text("[]", encoding="utf-8")
    (tpl / "_meta" / "tools" / "__pycache__").mkdir()
    (tpl / "_meta" / "tools" / "__pycache__" / "sweep.cpython-312.pyc").write_bytes(b"\x00")
    (tpl / "_meta" / "tools" / "stray.pyc").write_bytes(b"\x00")
    space = make_space(tmp_path / "ada")
    report = template_sync.run(space, source=str(tpl))
    assert sorted(report.added) == [".claude/skills/sweep/SKILL.md", "_meta/tools/sweep.py"]
    assert not (space / "_meta" / "tools" / ".pytest_cache").exists()
    assert not (space / "_meta" / "tools" / "__pycache__").exists()
    assert not (space / "_meta" / "tools" / "stray.pyc").exists()


def test_a_plugin_folder_and_a_marketplace_file_are_not_machinery(tmp_path):
    """The guard plugin is gone: a plugin folder or a marketplace file in the template is not carried, and one a space
    still has is left as it is (nothing is ever deleted)."""
    assert not {"_meta/plugins", ".claude-plugin"} & set(template_sync.MACHINERY)
    tpl = make_template(tmp_path / "tpl")
    (tpl / "_meta" / "plugins" / "spaces-guard").mkdir(parents=True)
    (tpl / "_meta" / "plugins" / "spaces-guard" / "guard.py").write_text("print('guard')\n", encoding="utf-8")
    (tpl / ".claude-plugin").mkdir()
    (tpl / ".claude-plugin" / "marketplace.json").write_text('{"name": "spaces"}\n', encoding="utf-8")
    space = make_space(tmp_path / "ada")
    (space / ".claude-plugin").mkdir()
    (space / ".claude-plugin" / "marketplace.json").write_text('{"name": "ours"}\n', encoding="utf-8")
    report = template_sync.run(space, source=str(tpl))
    assert sorted(report.added) == [".claude/skills/sweep/SKILL.md", "_meta/tools/sweep.py"]
    assert not (space / "_meta" / "plugins").exists()
    assert (space / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8") == '{"name": "ours"}\n'


def test_help_prints_usage_and_touches_nothing(capsys):
    assert template_sync.main(["--help"]) == 0
    assert "template-version.yaml" in capsys.readouterr().out


def test_a_space_without_provenance_keeps_its_own_readme_and_gets_no_licence(tmp_path):
    tpl = make_template(tmp_path / "tpl")
    (tpl / "README.md").write_text("# The template\n", encoding="utf-8")
    (tpl / "LICENSE").write_text("Template licence\n", encoding="utf-8")
    space = make_space(tmp_path / "scheduler", profile="project")
    (space / "README.md").write_text("# Scheduler app\n", encoding="utf-8")
    report = template_sync.run(space, source=str(tpl))
    assert (space / "README.md").read_text(encoding="utf-8") == "# Scheduler app\n"
    assert not (space / "LICENSE").exists()
    assert "README.md" not in report.updated and "LICENSE" not in report.added
    prov = yaml.safe_load((space / "_meta" / "state" / "template-version.yaml").read_text(encoding="utf-8"))
    assert "README.md" not in prov["files"] and "LICENSE" not in prov["files"]


def test_a_readme_received_from_the_template_still_follows_it(tmp_path):
    tpl = make_template(tmp_path / "tpl")
    (tpl / "README.md").write_text("# v1\n", encoding="utf-8")
    space = make_space(tmp_path / "ada")
    (space / "README.md").unlink(missing_ok=True)
    template_sync.run(space, source=str(tpl))
    prov_path = space / "_meta" / "state" / "template-version.yaml"
    prov = yaml.safe_load(prov_path.read_text(encoding="utf-8"))
    prov["files"]["README.md"] = template_sync.sha1(tpl / "README.md")
    prov_path.write_text(yaml.safe_dump(prov), encoding="utf-8")
    (space / "README.md").write_text("# v1\n", encoding="utf-8")
    (tpl / "README.md").write_text("# v2\n", encoding="utf-8")
    report = template_sync.run(space, source=str(tpl))
    assert "README.md" in report.updated
    assert (space / "README.md").read_text(encoding="utf-8") == "# v2\n"


# ----- releases: a space follows the newest release its owner said yes to -------------------
import os  # noqa: E402
import subprocess  # noqa: E402

import chain  # noqa: E402
import pages  # noqa: E402
import spaceyaml  # noqa: E402

PROV = Path("_meta") / "state" / "template-version.yaml"
ITEMS = Path("_inbound") / "needs-you" / "template-release.yaml"
CHANGELOG = "# Changelog\n\n## [0.2.0] - 2026-10-20\n\nFaster sweeps.\nFewer clicks.\n\n### Added\n\n- x\n\n" \
            "## [0.1.0] - 2026-10-18\n\nThe first release.\n"


def _git(cwd: Path, *args: str, date: str | None = None) -> str:
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1")
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    cmd = ["git", "-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.test", *args]
    return subprocess.run(cmd, cwd=str(cwd), check=True, capture_output=True, text=True, encoding="utf-8",
                          env=env).stdout.strip()


def _release(tpl: Path, tag: str | None, tool: str, date: str) -> str:
    """Commit the template with `tool` as its sweep tool on `date`, tagged `tag` when given."""
    (tpl / "_meta" / "tools" / "sweep.py").write_text(tool, encoding="utf-8")
    _git(tpl, "add", "-A")
    _git(tpl, "commit", "-q", "-m", tool.strip(), date=date)
    if tag:
        _git(tpl, "tag", "-a", tag, "-m", tag, date=date)
    return _git(tpl, "rev-parse", "HEAD")


def git_template(root: Path) -> Path:
    make_template(root)
    (root / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    return root


def _yes(root: Path, tag: str, commit: str, by: str = "Ada Okafor") -> None:
    with (root / "log.md").open("a", encoding="utf-8") as fh:
        fh.write(f"\n- 2026-10-18T09:00:00Z yes template-{tag}-{commit[:8]} by {by}\n")


def _items(root: Path) -> list:
    path = root / ITEMS
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("items") or [] if path.exists() else []


def _prov(root: Path) -> dict:
    return yaml.safe_load((root / PROV).read_text(encoding="utf-8"))


def test_no_release_yet_changes_nothing(tmp_path):
    tpl = git_template(tmp_path / "tpl")
    _release(tpl, None, "print('v1')\n", "2026-10-01T09:00:00Z")
    space = make_space(tmp_path / "ada")
    report = template_sync.run(space, source=tpl.as_uri())
    assert report.note == "no release yet" and not report.added
    assert not (space / "_meta" / "tools" / "sweep.py").exists() and not (space / PROV).exists()
    assert _items(space) == []


def test_a_newer_release_asks_once_and_a_yes_moves_the_space(tmp_path):
    tpl = git_template(tmp_path / "tpl")
    first = _release(tpl, "v0.1.0", "print('v1')\n", "2026-10-01T09:00:00Z")
    space = make_space(tmp_path / "ada")
    for _ in range(2):
        report = template_sync.run(space, source=tpl.as_uri())
        assert report.note == "v0.1.0 waits for a yes" and not report.added
    items = _items(space)
    assert [i["id"] for i in items] == [f"template-v0.1.0-{first[:8]}"]
    assert items[0]["text"] == "Update to v0.1.0? The first release."
    assert not (space / "_meta" / "tools" / "sweep.py").exists()
    _yes(space, "v0.1.0", first)
    report = template_sync.run(space, source=tpl.as_uri())
    assert report.release == "v0.1.0" and "_meta/tools/sweep.py" in report.added
    prov = _prov(space)
    assert prov["release"] == "v0.1.0" and prov["commit"] == first
    assert prov["approved"] == {"release": "v0.1.0", "commit": first, "by": "Ada Okafor",
                                "at": "2026-10-18T09:00:00Z", "in": "ada"}
    assert _items(space) == []
    # a newer release: the space stays where it is and asks again, with the new summary
    second = _release(tpl, "v0.2.0", "print('v2')\n", "2026-10-02T09:00:00Z")
    report = template_sync.run(space, source=tpl.as_uri())
    assert report.release == "v0.1.0" and not report.updated
    assert (space / "_meta" / "tools" / "sweep.py").read_text(encoding="utf-8") == "print('v1')\n"
    assert _items(space)[0]["id"] == f"template-v0.2.0-{second[:8]}"
    assert _items(space)[0]["text"] == "Update to v0.2.0? Faster sweeps. Fewer clicks."


def test_someone_elses_yes_or_another_commits_does_not_count(tmp_path):
    tpl = git_template(tmp_path / "tpl")
    first = _release(tpl, "v0.1.0", "print('v1')\n", "2026-10-01T09:00:00Z")
    space = make_space(tmp_path / "ada")
    _yes(space, "v0.1.0", first, by="Bea Lund")
    _yes(space, "v0.1.0", "1" * 40)
    report = template_sync.run(space, source=tpl.as_uri())
    assert report.note == "v0.1.0 waits for a yes" and not report.added


def test_a_yes_in_the_owners_workspace_covers_their_project_space(tmp_path):
    tpl = git_template(tmp_path / "tpl")
    first = _release(tpl, "v0.1.0", "print('v1')\n", "2026-10-01T09:00:00Z")
    workspace = make_space(tmp_path / "ada")
    project = make_space(tmp_path / "harbor", profile="project")
    machine = Path.home() / ".spaces"
    machine.mkdir(parents=True, exist_ok=True)
    (machine / "spaces.yaml").write_text(yaml.safe_dump({"spaces": [{"root": str(workspace)}, {"root": str(project)}]}),
                                         encoding="utf-8")
    report = template_sync.run(project, source=tpl.as_uri())
    assert report.note == "v0.1.0 waits for a yes" and _items(project) == []   # the workspace asks
    _yes(workspace, "v0.1.0", first)
    report = template_sync.run(project, source=tpl.as_uri())
    assert report.release == "v0.1.0" and _prov(project)["approved"]["in"] == "ada"


def test_template_updates_main_follows_main_as_before(tmp_path):
    tpl = git_template(tmp_path / "tpl")
    _release(tpl, "v0.1.0", "print('v1')\n", "2026-10-01T09:00:00Z")
    head = _release(tpl, None, "print('main')\n", "2026-10-02T09:00:00Z")
    space = make_space(tmp_path / "ada", template_updates="main")
    report = template_sync.run(space, source=tpl.as_uri())
    assert (space / "_meta" / "tools" / "sweep.py").read_text(encoding="utf-8") == "print('main')\n"
    prov = _prov(space)
    assert prov["commit"] == head and "release" not in prov and "approved" not in prov
    assert report.note == "" and _items(space) == []


def test_a_space_never_moves_backwards(tmp_path):
    tpl = git_template(tmp_path / "tpl")
    first = _release(tpl, "v0.1.0", "print('v1')\n", "2026-10-01T09:00:00Z")
    head = _release(tpl, None, "print('main')\n", "2026-10-02T09:00:00Z")
    space = make_space(tmp_path / "ada")
    _yes(space, "v0.1.0", first)
    # synced main after the release: it waits for a release that descends from what it has
    (space / PROV).write_text(yaml.safe_dump({"repo": tpl.as_uri(), "commit": head, "files": {}}), encoding="utf-8")
    report = template_sync.run(space, source=tpl.as_uri())
    assert report.note == "this space is at or past v0.1.0" and not report.added and _items(space) == []
    # a commit the template's history does not hold (a rewritten history): the sync time decides
    for synced_at, ahead in (("2026-10-03T00:00:00Z", True), ("2026-09-30T00:00:00Z", False)):
        (space / PROV).write_text(yaml.safe_dump({"repo": tpl.as_uri(), "commit": "f" * 40, "synced_at": synced_at,
                                                   "files": {}}), encoding="utf-8")
        report = template_sync.run(space, source=tpl.as_uri())
        assert (report.release == "") is ahead, synced_at


def test_changelog_summary_reads_the_release_section():
    assert template_sync.changelog_summary(CHANGELOG, "v0.2.0") == "Faster sweeps. Fewer clicks."
    assert template_sync.changelog_summary(CHANGELOG, "v0.1.0") == "The first release."
    assert template_sync.changelog_summary(CHANGELOG, "v0.3.0") == ""
    assert template_sync.changelog_summary("## v1.0.0\n\nPlain heading.\n", "v1.0.0") == "Plain heading."


def test_a_new_yes_makes_the_sync_due_once(tmp_path):
    space_root = make_space(tmp_path / "ada")
    now = pages.parse_iso("2026-10-18T12:00:00Z")
    with (space_root / "_meta" / "state" / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"routine": "template-sync", "at": "2026-10-17T12:00:00Z", "notes": {"guarded": true}}\n')
    (space_root / PROV).write_text(yaml.safe_dump({"repo": "o/n", "commit": "a" * 40, "files": {}}), encoding="utf-8")
    space = spaceyaml.load(space_root)
    assert chain._template_sync_due(space, now) is False
    _yes(space_root, "v0.2.0", "b" * 40)   # 2026-10-18T09:00Z, after the last guarded run
    assert chain._template_sync_due(space, now) is True
    with (space_root / "_meta" / "state" / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"routine": "template-sync", "at": "2026-10-18T10:00:00Z", "notes": {"guarded": true}}\n')
    assert chain._template_sync_due(space, now) is False
