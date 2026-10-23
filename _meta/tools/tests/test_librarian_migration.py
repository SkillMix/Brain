"""librarian.py: a shape migration lands as one commit of its own changes."""
import yaml

import librarian
from conftest import NOW, make_space, write_page
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


LOWERING = (
    "from pathlib import Path\n\ndef migrate(root):\n"
    "    root = Path(root)\n"
    "    ran = root.parent / 'ran.txt'\n"
    "    ran.write_text(ran.read_text() + 'x' if ran.exists() else 'x')\n"
    "    page = root / 'knowledge/projects/harbor/brief.md'\n"
    "    page.write_text(page.read_text(encoding='utf-8').replace('title: Brief', 'title: Moved'), encoding='utf-8')\n"
    "    (root / 'knowledge/projects/harbor/parts').mkdir()\n"
    "    (root / 'knowledge/projects/harbor/parts/new.md').write_text('added\\n', encoding='utf-8')\n"
    "    (root / 'work/old.md').unlink()\n"
    "    space = root / 'space.yaml'\n"
    "    space.write_text(space.read_text(encoding='utf-8') + 'migrated: true\\n', encoding='utf-8')\n")


def _template(root, commit):
    """What template_sync.py records when it brings a template commit in."""
    (root / "_meta/state/template-version.yaml").write_text(
        yaml.safe_dump({"repo": "owner/template", "commit": commit, "files": {}}), encoding="utf-8")


def _lowered(root):
    return (0, 0) if (root / "knowledge/projects/harbor/parts/new.md").exists() else (1, 0)


def test_a_migration_that_lowers_the_tests_is_undone_whole_and_held_until_a_template_update(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    write_page(root, "knowledge/projects/harbor/brief.md", type="entity", title="Brief", subject="harbor")
    (root / "work" / "old.md").write_text("an old page\n", encoding="utf-8")
    _template(root, "a" * 40)
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "shape_2.py").write_text(LOWERING, encoding="utf-8")
    before = {rel: (root / rel).read_bytes()
              for rel in ("knowledge/projects/harbor/brief.md", "work/old.md", "space.yaml")}
    assert librarian.migrate(root, folder=folder, now=NOW, scorer=_lowered) == []
    for rel, data in before.items():
        assert (root / rel).read_bytes() == data, f"{rel} is back as it was"
    assert not (root / "knowledge/projects/harbor/parts").exists(), "what the migration added is gone"
    log = (root / "log.md").read_text(encoding="utf-8")
    assert "shape migration 2" in log and "restored" in log and "needs a person" not in log
    held = yaml.safe_load((root / "_meta/state/librarian/migrations.yaml").read_text(encoding="utf-8"))
    assert held[2]["template"] == "a" * 40
    assert not list((root / "_inbound/needs-you").iterdir()) and not list((root / "_inbound/questions").iterdir())
    assert not (root / librarian.CHANGES).exists(), "nothing to land"
    # the same template: not tried again
    assert librarian.migrate(root, folder=folder, now=NOW, scorer=_lowered) == []
    assert (tmp_path / "ran.txt").read_text() == "x"


def test_a_held_migration_is_tried_again_after_a_newer_template_sync(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    write_page(root, "knowledge/projects/harbor/brief.md", type="entity", title="Brief", subject="harbor")
    (root / "work" / "old.md").write_text("an old page\n", encoding="utf-8")
    _template(root, "a" * 40)
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "shape_2.py").write_text(LOWERING, encoding="utf-8")
    assert librarian.migrate(root, folder=folder, now=NOW, scorer=_lowered) == []
    _template(root, "b" * 40)
    assert librarian.migrate(root, folder=folder, now=NOW, scorer=lambda r: (0, 0)) == [2]
    assert (tmp_path / "ran.txt").read_text() == "xx"
    assert (root / "knowledge/projects/harbor/parts/new.md").exists()
    assert yaml.safe_load((root / "space.yaml").read_text(encoding="utf-8"))["shape"] == 2
    held = yaml.safe_load((root / "_meta/state/librarian/migrations.yaml").read_text(encoding="utf-8")) or {}
    assert 2 not in held, "a migration that ran is no longer held"


BREAKING = ("def migrate(root):\n"
            "    (root / 'work/old.md').write_text('half done\\n', encoding='utf-8')\n"
            "    (root / 'work/added.md').write_text('half done\\n', encoding='utf-8')\n"
            "    raise RuntimeError('the migration broke halfway')\n")


def test_a_migration_that_breaks_halfway_is_undone_whole_and_held(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    (root / "work" / "old.md").write_text("an old page\n", encoding="utf-8")
    _template(root, "a" * 40)
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "shape_2.py").write_text(BREAKING, encoding="utf-8")
    assert librarian.migrate(root, folder=folder, now=NOW, scorer=lambda r: (1, 0)) == []
    assert (root / "work/old.md").read_text(encoding="utf-8") == "an old page\n"
    assert not (root / "work/added.md").exists()
    assert "restored shape migration 2" in (root / "log.md").read_text(encoding="utf-8")
    held = yaml.safe_load((root / "_meta/state/librarian/migrations.yaml").read_text(encoding="utf-8"))
    assert held[2]["template"] == "a" * 40
