"""check.py: the weekly review batch comes once a week."""
import check
from conftest import NOW, make_space
from test_check import D, signed


def test_the_review_batch_comes_once_a_week(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects", "state": "active"}])
    signed(root, "knowledge/projects/harbor/status.md", "# Status\n\nAll green.\n", type="snapshot", at=NOW - 8 * D)
    assert check.run(root, now=NOW).batch_lines == 1
    batch, = sorted((root / "_inbound" / "candidates").glob("review-*.md"))
    batch.unlink()   # answered
    signed(root, "knowledge/projects/harbor/ports.md", "# Ports\n\nOpen.\n", type="snapshot", at=NOW - 6 * D)
    assert check.run(root, now=NOW + 2 * D).batch_lines == 0, "a page past its window waits for next week's batch"
    assert check.run(root, now=NOW + 8 * D).batch_lines == 2
