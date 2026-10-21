"""librarian.py: a loose page right under knowledge/ is filed with its subject."""
import librarian
from conftest import NOW, make_space, write_page
from test_librarian import HARBOR


def test_a_loose_page_right_under_knowledge_is_filed_with_its_subject(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    write_page(root, "knowledge/port-notes.md", type="entity", title="Port notes", subject="harbor")
    write_page(root, "knowledge/index.md", type="index", title="Knowledge", subject="harbor")
    report = librarian.run(root, now=NOW, scorer=lambda r: (0, 0))
    assert report.applied and (root / "knowledge/projects/harbor/port-notes.md").exists()
    assert not (root / "knowledge/port-notes.md").exists()
    assert "knowledge/port-notes.md filed with its subject" in (root / "log.md").read_text(encoding="utf-8")
    assert (root / "knowledge/index.md").exists(), "an index is not a loose page"
