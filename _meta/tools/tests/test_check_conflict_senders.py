"""check.py: two spaces' conflict notes about one page both reach its owner."""
import json

import check
import landing
import pages
from conftest import NOW, make_space, mount_brain
from test_check import D, H, signed
from test_distill import witness


def released_to(space, rid, destination="brain"):
    """The record already went to the page's home by the release rule: a note quoting it may follow."""
    with (space / "_meta" / "state" / "released.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "p-0000e0e0", "destination": destination, "class": "at_once",
                             "evidence": [rid]}) + "\n")


def test_two_spaces_contesting_one_page_send_two_notes_that_both_land(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor"}])
    signed(brain, "knowledge/projects/harbor/harbor.md", "# Harbor\n\ngo-live: 2026-11-03\n",
           id="p-0000f001", at=NOW - 3 * D)
    notes = []
    for name, day in (("ada", "2026-11-10"), ("ben", "2026-11-17")):
        space = make_space(tmp_path / name, name=name, subjects=[])
        mount_brain(space, brain)
        witness(space, f"rec-{name}", "harbor", f"Moved again: go-live: {day}\n", NOW - D)
        released_to(space, f"rec-{name}")
        assert check.run(space, now=NOW).conflict_notes == 1
        (item,) = sorted((space / "_outbound" / "queue").glob("*.md"))
        notes.append(pages.read(item))
    assert notes[0][0]["id"] != notes[1][0]["id"], "each sender's note has an id of its own"
    for (fm, body), name in zip(notes, ("ada", "ben")):
        landed = dict(fm, released_from=name, release_class="window")
        for key in ("class", "stage_after", "listed", "destination", "from_space"):
            landed.pop(key, None)
        pages.write(brain / "_inbound" / "candidates" / f"{fm['id']}.md", landed, body)
    rep = landing.land(brain, now=NOW + 2 * H)
    assert sorted(rep.conflicts) == sorted(fm["id"] for fm, _ in notes)
    page, _ = pages.read(brain / "knowledge/projects/harbor/harbor.md")
    assert sorted(page["contested"]) == sorted(fm["id"] for fm, _ in notes)
    bodies = [pages.read(brain / "_inbound" / "conflicts" / f"{fm['id']}.md")[1] for fm, _ in notes]
    assert "2026-11-10" in bodies[0] and "2026-11-17" in bodies[1]


def test_a_later_different_value_from_the_same_space_sends_a_note_of_its_own(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor"}])
    signed(brain, "knowledge/projects/harbor/harbor.md", "# Harbor\n\ngo-live: 2026-11-03\n",
           id="p-0000f001", at=NOW - 3 * D)
    space = make_space(tmp_path / "ada", name="ada", subjects=[])
    mount_brain(space, brain)
    witness(space, "rec-1", "harbor", "Moved again: go-live: 2026-11-10\n", NOW - D)
    released_to(space, "rec-1")
    assert check.run(space, now=NOW).conflict_notes == 1
    witness(space, "rec-2", "harbor", "Moved once more: go-live: 2026-11-17\n", NOW + D)
    released_to(space, "rec-2")
    assert check.run(space, now=NOW + 2 * D).conflict_notes == 1
    notes = [pages.read(p) for p in sorted((space / "_outbound" / "queue").glob("*.md"))]
    assert len({fm["id"] for fm, _ in notes}) == 2, "the later value does not overwrite the earlier note"
    assert sorted("2026-11-10" in body for _, body in notes) == [False, True]
    assert sorted("2026-11-17" in body for _, body in notes) == [False, True]
