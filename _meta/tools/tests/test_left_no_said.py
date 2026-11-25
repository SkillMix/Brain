"""A no on an item that already left retracts it at its destination: the read-back says so first."""
import json

import sign
from conftest import NOW, make_space


def test_the_read_back_of_an_item_that_left_says_a_no_retracts_it(tmp_path):
    # Seen live: in the owner's own space, a no on an item that had already left became a
    # retraction in the destination, and the read-back said only "not found".
    root = make_space(tmp_path / "ada")
    (root / "_meta" / "state").mkdir(parents=True, exist_ok=True)
    (root / "_meta" / "state" / "released.jsonl").write_text(json.dumps(
        {"id": "p-0000d001", "at": "2026-10-11T09:00:00Z", "destination": "harbor", "class": "at_once"}) + "\n",
        encoding="utf-8")
    said = sign.read_back(root, ["p-0000d001"])
    assert "not found" not in said
    assert "left for harbor on 2026-10-11" in said
    assert "a no retracts it: harbor withdraws it" in said
    assert "not found" in sign.read_back(root, ["p-0000d002"])
