"""check.py: a verdict holds for the value it answered."""
import pages
import check
from conftest import NOW, make_space
from test_check import D, H, checks, signed, verdicts
from test_distill import witness


def test_an_answered_hit_does_not_silence_a_later_different_value(tmp_path):
    root = make_space(tmp_path / "ada", owner="Ada Okafor", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor"}])
    signed(root, "knowledge/projects/harbor/plan.md", "# Plan\n\ngo-live date: 2026-11-03\n", at=NOW - 3 * D)
    witness(root, "rec-1", "harbor", "The port says go-live date: 2026-11-10.\n", NOW - D)
    first, = check.run(root, now=NOW).hits
    verdicts(root, [{"check_id": first.hit_id, "verdict": "yes", "by": "Ben Liu", "at": pages.iso(NOW)}])
    checks(root)[0].unlink()   # answered: sign removes the item
    witness(root, "rec-2", "harbor", "Again: go-live date: 2026-11-10.\n", NOW + H)
    assert check.run(root, now=NOW + 2 * H).hits == [], "the same value, answered once, stays answered"
    witness(root, "rec-3", "harbor", "Moved once more: go-live date: 2026-11-24.\n", NOW + 3 * H)
    later = check.run(root, now=NOW + 4 * H).hits
    assert [h.check for h in later] == ["newer_evidence"] and "2026-11-24" in later[0].recommendation
