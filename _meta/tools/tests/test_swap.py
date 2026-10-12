"""swap: a project repository keeps its code and history and takes the shape-1 layer."""
from pathlib import Path

import pages
import spaceyaml
import swap
import validate
from conftest import make_space


def code_repo(root: Path) -> Path:
    for rel, text in {"README.md": "# Scheduler\n\nThe app.\n", "backend/app.py": "print('hi')\n",
                      "wiki/plan.md": "# Old plan\n", "live/status.md": "# Old status\n",
                      "_meta/okf.yaml": "x: 1\n", "AGENTS.md": "old rules\n", ".gitignore": "node_modules/\n",
                      ".github/workflows/project-knowledge.yml": "name: old\n",
                      ".github/workflows/ci.yml": "name: app\n"}.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def test_swap_archives_the_old_layer_and_adds_the_shape_without_touching_code(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    repo = code_repo(tmp_path / "scheduler")
    report = swap.run(repo, "scheduler", "Ada Okafor", ["Ada Okafor", "Ben Liu"], brain=None,
                      brain_path=str(brain))
    assert not report.failed(), [s.row() for s in report.failed()]
    assert (repo / "_archive" / "wiki" / "plan.md").exists() and (repo / "_archive" / "live" / "status.md").exists()
    assert (repo / "_archive" / "AGENTS.md").exists() and (repo / "_archive" / "README.md").exists()
    assert (repo / "README.md").read_text(encoding="utf-8").startswith("# Scheduler")
    assert (repo / "backend" / "app.py").exists() and not (repo / "LICENSE").exists()
    assert (repo / "_archive" / ".github" / "workflows" / "project-knowledge.yml").exists()
    assert (repo / ".github" / "workflows" / "ci.yml").exists()
    assert "node_modules/" in (repo / ".gitignore").read_text(encoding="utf-8")
    space = spaceyaml.load(repo)
    assert space.profile == "project" and space.mode == "live" and space.audience == ["Ada Okafor", "Ben Liu"]
    assert (repo / "_meta" / "tools" / "validate.py").exists()
    assert (repo / "_meta" / "subjects.yaml").read_text(encoding="utf-8").strip() == "subjects: []"
    assert not (repo / "_meta" / "tests" / "answers.yaml").exists()
    assert [e for e in validate.validate(repo) if not e.startswith("warning:")] == []
    again = swap.run(repo, "scheduler", "Ada Okafor", ["Ada Okafor"])
    assert again.steps[0].result.startswith("already a space")


def test_dry_run_changes_nothing(tmp_path):
    repo = code_repo(tmp_path / "scheduler")
    before = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*"))
    swap.run(repo, "scheduler", "Ada Okafor", ["Ada Okafor"], dry_run=True)
    assert sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*")) == before
