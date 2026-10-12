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
