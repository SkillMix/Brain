"""check.py: a page signed since the last pass is read like new evidence."""
import check
from conftest import NOW, make_space
from test_check import D, H, signed


def test_a_newly_signed_page_that_contradicts_another_is_checked_without_new_evidence(tmp_path):
    root = make_space(tmp_path / "ada", owner="Ada Okafor", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor"}])
    signed(root, "knowledge/projects/harbor/plan.md", "# Plan\n\ngo-live date: 2026-11-03\n", at=NOW - 3 * D)
    assert check.run(root, now=NOW).hits == []
    signed(root, "knowledge/projects/harbor/cutover.md", "# Cutover\n\ngo-live date: 2026-11-24\n",
           by="Ada Okafor", at=NOW + H)
    assert check.run(root, now=NOW + 2 * H).hits == [], "between window passes nothing new is read"
    hits = check.run(root, now=NOW + 26 * H).hits
    assert [h.check for h in hits] == ["disagree"], "the next window pass reads what was signed since"
    assert hits[0].page == "knowledge/projects/harbor/plan.md" and "2026-11-24" in hits[0].recommendation
