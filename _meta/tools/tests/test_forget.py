"""forget.py: the listing also finds held items that name the subject as a candidate and signals."""
import yaml

import forget
from conftest import make_space, write_page
from test_batch6 import HARBOR


def test_the_listing_finds_a_held_doubt_item_and_a_signal_about_the_subject(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    write_page(root, "_outbound/held/p-0000d001.md", type="source", title="One note for two projects",
               subject=None, candidates=["beacon", "harbor"], reason="doubt")
    signal = root / "_inbound/signals/p-0000d002.yaml"
    signal.parent.mkdir(parents=True, exist_ok=True)
    signal.write_text(yaml.safe_dump({"id": "p-0000d002", "subject": "harbor", "note": "open item moved"}),
                      encoding="utf-8")
    other = root / "_inbound/signals/p-0000d003.yaml"
    other.write_text(yaml.safe_dump({"id": "p-0000d003", "subject": "beacon"}), encoding="utf-8")
    found = forget.listing(root, "harbor")
    assert "_outbound/held/p-0000d001.md" in found["pages"]
    assert "_inbound/signals/p-0000d002.yaml" in found["pages"]
    assert "_inbound/signals/p-0000d003.yaml" not in found["pages"]
