"""forget.py: the listing also finds held items that name the subject as a candidate, signals and
the sign-question ledger's lines."""
import yaml

import forget
import sign_questions
from conftest import NOW, make_space, write_page
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


def test_the_listing_finds_the_subject_s_lines_in_the_sign_question_ledger(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    # 1 asked harbor, 2 asked beacon, 3 answered (names only the harbor question's id),
    # 4 checked harbor, 5 answered beacon, 6 and 7 not ledger lines
    q = sign_questions.ask(root, "harbor", "p-00000101", "Has Harbor gone live?", "why", "rec", [],
                           by="routine:distill", now=NOW)
    other = sign_questions.ask(root, "beacon", "p-00000102", "Has Beacon gone live?", "why", "rec", [],
                               by="routine:distill", now=NOW)
    sign_questions.answer(root, q.id, by="Ada Okafor", text="yes", now=NOW)
    sign_questions.mark_checked(root, "harbor", by="routine:distill", now=NOW)
    sign_questions.answer(root, other.id, by="Ada Okafor", text="no", now=NOW)
    with (root / sign_questions.LEDGER).open("a", encoding="utf-8") as handle:
        handle.write('{"event": "answ\n42\n')
    found = forget.listing(root, "harbor")
    assert found["questions"] == [f"_meta/state/sign-questions.jsonl:{n}" for n in (1, 3, 4)]
