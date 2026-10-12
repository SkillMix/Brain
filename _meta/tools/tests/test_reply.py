"""Answering by email reply: parsed against that morning's list, read back, never applied alone."""
import json

import morning_view
import needs_you
import pages
import reply
from conftest import NOW, make_space, write_page


def test_parse_reads_ranges_lists_edits_and_stops_at_the_quote():
    text = "1-3 yes, 5 edit: the date is the ninth, 6 later\n4 no\n\nOn Monday Ada wrote:\n> 7 yes"
    got = reply.parse(text, 7)
    assert got == {1: ("yes", ""), 2: ("yes", ""), 3: ("yes", ""), 5: ("edit", "the date is the ninth"),
                   6: ("later", ""), 4: ("no", "")}
    assert reply.parse("all yes", 2) == {1: ("yes", ""), 2: ("yes", "")}
    assert reply.parse("thanks, see you", 3) == {}


def test_a_reply_to_the_briefing_becomes_one_answer_to_read_back(tmp_path):
    root = make_space(tmp_path / "ada")
    for n in range(3):
        write_page(root, f"_inbound/candidates/c{n}.md", id=f"p-0000c00{n}", title=f"Candidate {n}",
                   subject="harbor")
    morning_view.run(root, "Ada Okafor", now=NOW)
    listing = json.loads((root / "_meta/state/morning/2026-10-12.json").read_text(encoding="utf-8"))
    assert [i["id"] for i in listing["items"]][:3] == ["p-0000c000", "p-0000c001", "p-0000c002"]
    feed = root / "_meta/state/feed/mail.jsonl"
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text(json.dumps({"id": "m-r1", "kind": "mail", "at": "2026-10-12T11:30:00Z",
                                "from": "owner@example.test", "to": ["owner@example.test"],
                                "subject": "Re: Morning briefing: 3 items need you",
                                "text": "1 yes, 2 edit: Harbor goes live on 10 November, 3 later\n\n> quoted"}) + "\n",
                    encoding="utf-8")
    written = reply.run(root, now=NOW)
    assert len(written) == 1
    fm, body = pages.read(root / written[0])
    assert fm["verdicts"] == {"p-0000c000": "yes", "p-0000c001": ["edit", "Harbor goes live on 10 November"],
                              "p-0000c002": "later"}
    assert "submit" in body and "fingerprint" in body
    assert reply.run(root, now=NOW) == [], "a reply is read once"
    kinds = [i.kind for i in needs_you.collect(root, now=NOW, online=False)]
    assert "answer" in kinds
    # nothing was applied: the candidates are still there, unsigned
    assert all((root / f"_inbound/candidates/c{n}.md").exists() for n in range(3))
