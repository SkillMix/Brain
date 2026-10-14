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


def test_a_yes_on_an_unsure_item_brings_the_record_in_once(tmp_path):
    import unsure
    root = make_space(tmp_path / "ada", owner_email="ada@lantern.example")
    rec = {"id": "m-77", "feed": "mail", "kind": "mail", "at": "2026-10-11T08:00:00Z", "subject": "Dinner",
           "text": "B."}
    (root / unsure.STORE).parent.mkdir(parents=True, exist_ok=True)
    (root / unsure.STORE).write_text(json.dumps(rec) + "\n", encoding="utf-8")
    key = unsure.code_of(rec)
    (root / "_meta" / "state" / "morning").mkdir(parents=True)
    (root / "_meta" / "state" / "morning" / "2026-10-12.json").write_text(json.dumps(
        {"date": "2026-10-12", "items": [{"id": f"unsure-{key}", "kind": "unsure", "text": "a record to classify"}]}),
        encoding="utf-8")
    feed = root / "_meta" / "state" / "feed" / "mail.jsonl"
    feed.parent.mkdir(parents=True, exist_ok=True)
    for n in (1, 2):
        with feed.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": f"r-{n}", "kind": "mail", "at": "2026-10-12T10:00:00Z",
                                 "from": "ada@lantern.example", "to": ["ada@lantern.example"],
                                 "subject": "Re: Morning briefing: 1 item needs you", "text": f"{key} yes"}) + "\n")
    reply.run(root, now=NOW)
    ids = [json.loads(line)["id"] for line in feed.read_text(encoding="utf-8").splitlines()]
    assert ids.count("m-77") == 1 and unsure.waiting(root) == []
    assert not list((root / "_inbound" / "answers").glob("*.md")), "nothing to sign: applied at once"


def test_a_no_on_an_unsure_item_lets_it_go(tmp_path):
    import unsure
    root = make_space(tmp_path / "ada", owner_email="ada@lantern.example")
    rec = {"id": "m-79", "feed": "mail", "kind": "mail", "at": "2026-10-11T08:00:00Z", "subject": "Gym", "text": "B."}
    (root / unsure.STORE).parent.mkdir(parents=True, exist_ok=True)
    (root / unsure.STORE).write_text(json.dumps(rec) + "\n", encoding="utf-8")
    (root / "_meta" / "state" / "morning").mkdir(parents=True)
    (root / "_meta" / "state" / "morning" / "2026-10-12.json").write_text(json.dumps(
        {"date": "2026-10-12", "items": [{"id": f"unsure-{unsure.key_of(rec)}", "kind": "unsure", "text": "x"}]}),
        encoding="utf-8")
    feed = root / "_meta" / "state" / "feed" / "mail.jsonl"
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text(json.dumps({"id": "r-9", "kind": "mail", "at": "2026-10-12T10:00:00Z",
                                "from": "ada@lantern.example", "to": ["ada@lantern.example"],
                                "subject": "Re: Morning briefing", "text": f"{unsure.code_of(rec)} no"}) + "\n", encoding="utf-8")
    reply.run(root, now=NOW)
    assert unsure.waiting(root) == []
    assert "m-79" not in feed.read_text(encoding="utf-8")


def test_unsure_records_answer_to_their_code_only(tmp_path):
    import unsure
    root = make_space(tmp_path / "ada", owner_email="ada@lantern.example")
    keep = {"id": "m-81", "feed": "mail", "kind": "mail", "at": "2026-10-11T08:00:00Z", "subject": "A", "text": "B."}
    other = dict(keep, id="m-82")
    (root / unsure.STORE).parent.mkdir(parents=True, exist_ok=True)
    (root / unsure.STORE).write_text(json.dumps(keep) + "\n" + json.dumps(other) + "\n", encoding="utf-8")
    (root / "_meta" / "state" / "morning").mkdir(parents=True)
    (root / "_meta" / "state" / "morning" / "2026-10-12.json").write_text(json.dumps(
        {"date": "2026-10-12", "items": [{"id": "p-1", "kind": "candidate", "text": "x"}]}), encoding="utf-8")
    feed = root / "_meta" / "state" / "feed" / "mail.jsonl"
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text(json.dumps({"id": "r-20", "kind": "mail", "at": "2026-10-12T10:00:00Z",
                                "from": "ada@lantern.example", "to": ["ada@lantern.example"],
                                "subject": "Re: Morning briefing", "text": f"all yes\n{unsure.code_of(keep)} yes"}) + "\n",
                    encoding="utf-8")
    reply.run(root, now=NOW)
    ids = [json.loads(line)["id"] for line in feed.read_text(encoding="utf-8").splitlines()]
    assert "m-81" in ids and "m-82" not in ids, "all yes never reaches an unsure record"
    assert [r["id"] for r in unsure.waiting(root)] == ["m-82"]
