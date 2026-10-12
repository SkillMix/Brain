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
