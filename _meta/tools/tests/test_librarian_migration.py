"""librarian.py: a shape migration lands as one commit of its own changes."""
import yaml

import librarian
from conftest import NOW, write_page
from test_librarian import HARBOR
from test_sync import git, repo, run, subjects  # noqa: F401  (the repo fixture)


def test_a_shape_migration_lands_as_one_commit_of_its_own_changes(repo, tmp_path):  # noqa: F811
    write_page(repo, "knowledge/projects/harbor/brief.md", type="entity", title="Brief", subject="harbor")
    (repo / "_meta" / "subjects.yaml").write_text(yaml.safe_dump({"subjects": [HARBOR]}), encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "shape_2.py").write_text(
        "from pathlib import Path\n\ndef migrate(root):\n"
        "    page = Path(root) / 'knowledge/projects/harbor/brief.md'\n"
        "    page.write_text(page.read_text(encoding='utf-8').replace('title: Brief', 'title: Harbor brief'),"
        " encoding='utf-8')\n", encoding="utf-8")
    (repo / "work" / "unrelated.md").write_text("another session's edit in progress\n", encoding="utf-8")
    assert librarian.migrate(repo, folder=folder, now=NOW, scorer=lambda r: (0, 0)) == [2]
    assert run(repo, "--min-age-minutes", "0") == 0
    assert subjects(repo)[:2] == ["chore: sync", "chore: structure"]
    moved = git(repo, "show", "--name-status", "--format=", "HEAD~1").stdout
    assert "knowledge/projects/harbor/brief.md" in moved and "space.yaml" in moved
    assert "unrelated" not in moved, "the migration's commit carries only what the migration changed"
