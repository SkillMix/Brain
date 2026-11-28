"""One record arriving twice from two rooms: the script pairs, an agent judges, a duplicate counts once."""
import datetime as dt
import json

import pytest

import distill
import gate
import landing
import needs_you
import pages
from conftest import make_space, write_page

HARBOR = {"id": "harbor", "kind": "projects", "title": "Harbor", "important": True}
BEACON = {"id": "beacon", "kind": "projects", "title": "Beacon", "important": True}
ONE_CALL = ("[00:00:01] Ada: Harbor goes live on the third, if the ferry data is in.\n"
            "[00:00:09] Ben: The ferry data lands Monday.\n")
# the same call from a second notetaker: its own transcription errors, a line more
OTHER_EARS = ("[00:00:02] Speaker 1: Harbour goes life on the third if the fairy data is in.\n"
              "[00:00:10] Speaker 2: The fairy data lands Monday.\n"
              "[00:00:15] Speaker 1: Good, then we are set.\n")


def brain(tmp_path):
    return make_space(tmp_path / "brain", profile="brain", name="brain", subjects=[HARBOR, BEACON])


def arrived(root, pid, kind, at, record, room, body="Text.\n", where="knowledge/sources/2026/11", **fm):
    """A record another room released, as landing files it (or as it waits, under `_inbound/candidates`)."""
    fm = dict(dict(type="source", title=f"{kind} {pid}", subject="harbor", kind=kind, at=at, evidence=[record],
                   released_from=room, released_at=at, release_class="at_once"), **fm)
    if where.startswith("knowledge/"):
        fm["landed_from"] = room
    return write_page(root, f"{where}/{pid}.md", body, id=pid, **fm)


def counts(root) -> dict:
    return {sid: sorted(ev.record for ev in evs) for sid, evs in distill.evidence_by_subject(root).items()}


def listed(root, capsys) -> list:
    assert landing.main([str(root), "duplicates"]) == 0
    return [(p["a"], p["b"], p["why"]) for p in json.loads(capsys.readouterr().out)]


def test_two_recordings_of_one_meeting_pair_and_count_once(tmp_path, capsys):
    root = brain(tmp_path)
    arrived(root, "p-0000c001", "call", "2026-11-20T10:00:00Z", "01JD8K2M4NQ", "ada", ONE_CALL)
    arrived(root, "p-0000c002", "call", "2026-11-20T10:04:00Z", "01JD8K9Q7RX", "ben", OTHER_EARS)
    assert listed(root, capsys) == [("p-0000c001", "p-0000c002", "two calls 4 minutes apart")]
    assert counts(root) == {"harbor": ["p-0000c001", "p-0000c002"]}
    # the fuller copy is the main; the other is marked, never deleted
    assert landing.main([str(root), "mark", "p-0000c002", "p-0000c001", "--by", "pair-checker"]) == 0
    capsys.readouterr()
    fm, body = pages.read(root / "knowledge/sources/2026/11/p-0000c001.md")
    assert fm["duplicate_of"]["id"] == "p-0000c002" and fm["duplicate_of"]["by"] == "pair-checker"
    assert body == ONE_CALL
    assert counts(root) == {"harbor": ["p-0000c002"]}
    assert listed(root, capsys) == []
    assert "marked p-0000c001 a duplicate of p-0000c002 by pair-checker" in (root / "log.md").read_text(encoding="utf-8")


def test_the_main_takes_the_subjects_of_its_copy(tmp_path):
    """This room's own page of the record, routed to two subjects here, marked a copy of an arrival."""
    root = brain(tmp_path)
    arrived(root, "p-0000c002", "call", "2026-11-20T10:04:00Z", "01JD8K9Q7RX", "ben", OTHER_EARS)
    write_page(root, "knowledge/sources/2026/11/01JD8K9Q7RX.md", ONE_CALL, id="p-0000c001", type="source",
               kind="call", at="2026-11-20T10:04:00Z", record="rec:01JD8K9Q7RX",
               routing={"decision": "route", "routes": ["brain/harbor", "brain/beacon"]})
    assert [(p["a"], p["b"]) for p in landing.duplicate_pairs(root)] == [("p-0000c001", "p-0000c002")]
    landing.mark_duplicate(root, "p-0000c002", "p-0000c001", "pair-checker")
    assert counts(root) == {"harbor": ["p-0000c002"], "beacon": ["p-0000c002"]}


def test_copies_for_different_subjects_are_not_listed(tmp_path):
    """Two routers sent parts of one record for two subjects: each subject counts it once already."""
    root = brain(tmp_path)
    arrived(root, "p-0000d001", "mail", "2026-11-20T09:00:00Z", "18c2f4a5b6d7e8f9", "ada")
    arrived(root, "p-0000d002", "mail", "2026-11-20T09:00:00Z", "18c2f4a5b6d7e8f9", "ben", subject="beacon")
    arrived(root, "p-0000d003", "call", "2026-11-20T10:00:00Z", "01JD8K2M4NQ", "ada")
    arrived(root, "p-0000d004", "call", "2026-11-20T10:04:00Z", "01JD8K9Q7RX", "ben", subject="beacon")
    assert landing.duplicate_pairs(root) == []


def test_a_candidate_drops_a_record_marked_since(tmp_path):
    root = brain(tmp_path)
    arrived(root, "p-0000c001", "call", "2026-11-20T10:00:00Z", "01JD8K2M4NQ", "ada", ONE_CALL)
    arrived(root, "p-0000c002", "call", "2026-11-20T10:04:00Z", "01JD8K9Q7RX", "ben", OTHER_EARS)
    distill.run(root, now=dt.datetime(2026, 11, 21, tzinfo=dt.timezone.utc))
    assert pages.read(root / "_inbound/candidates/harbor.md")[0]["evidence"] == ["p-0000c001", "p-0000c002"]
    landing.mark_duplicate(root, "p-0000c002", "p-0000c001", "pair-checker")
    arrived(root, "p-0000c003", "call", "2026-11-22T08:00:00Z", "01JD9A1B2CD", "ada")   # the next record moves it
    distill.run(root, now=dt.datetime(2026, 11, 23, tzinfo=dt.timezone.utc))
    fm, body = pages.read(root / "_inbound/candidates/harbor.md")
    assert fm["evidence"] == ["p-0000c002", "p-0000c003"] and "p-0000c001" not in body


@pytest.mark.parametrize("kind, one, other", [
    ("message", "C0BQ5611FLZ/1795558392.689769", "slack:C0BQ5611FLZ:1795558392.689769"),
    ("event", "gcal-7kq2m9d0v1r5＠v3", "7kq2m9d0v1r5@example.test"),
])
def test_one_id_written_two_ways_pairs(tmp_path, capsys, kind, one, other):
    root = brain(tmp_path)
    arrived(root, "p-0000d001", kind, "2026-11-20T09:00:00Z", one, "ada")
    arrived(root, "p-0000d002", kind, "2026-11-20T11:30:00Z", other, "ben")
    arrived(root, "p-0000d003", kind, "2026-11-20T09:00:00Z", "C0BQ5611FLZ/1795558392.689770", "ben")
    assert listed(root, capsys) == [("p-0000d001", "p-0000d002", "the same original id")]


def test_one_mail_from_two_mailboxes_pairs(tmp_path, capsys):
    root = brain(tmp_path)
    arrived(root, "p-0000e001", "mail", "2026-11-20T09:14:05Z", "18c2f4a5b6d7e8f9", "ada",
            **{"from": "Chloe Martin <Chloe@client.example>"})
    arrived(root, "p-0000e002", "mail", "2026-11-20T09:14:41Z", "19d3a5b6c7e8f9a0", "ben",
            **{"from": "chloe@client.example"})
    arrived(root, "p-0000e003", "mail", "2026-11-20T09:15:02Z", "1ae4b6c7d8f9a0b1", "ben",
            **{"from": "chloe@client.example"})   # her next mail, a minute on
    assert listed(root, capsys) == [("p-0000e001", "p-0000e002", "mail from one sender in one minute")]


def test_two_meetings_close_together_pair_and_keep_both_leaves_them(tmp_path, capsys):
    root = brain(tmp_path)
    one = arrived(root, "p-0000f001", "call", "2026-11-20T10:00:00Z", "01JD8K2M4NQ", "ada", ONE_CALL)
    two = arrived(root, "p-0000f002", "call", "2026-11-20T10:26:00Z", "01JD8M1B2CD", "ada",
                  "[00:00:01] Cara: The Harbor budget review.\n", title="Budget review")
    arrived(root, "p-0000f003", "call", "2026-11-20T10:31:00Z", "01JD8M5X6YZ", "ada",
            "[00:00:01] Dan: Harbor hiring.\n")   # 31 minutes after the first, 5 after the second
    before = {p: p.read_bytes() for p in (one, two)}
    pairs = listed(root, capsys)
    assert ("p-0000f001", "p-0000f002", "two calls 26 minutes apart") in pairs
    assert ("p-0000f001", "p-0000f003", "two calls 31 minutes apart") not in pairs
    assert landing.main([str(root), "keep-both", "p-0000f001", "p-0000f002", "--by", "pair-checker"]) == 0
    assert {p: p.read_bytes() for p in (one, two)} == before
    assert ("p-0000f001", "p-0000f002", "two calls 26 minutes apart") not in listed(root, capsys)
    assert counts(root) == {"harbor": ["p-0000f001", "p-0000f002", "p-0000f003"]}


def test_undo_restores_both(tmp_path, capsys):
    root = brain(tmp_path)
    arrived(root, "p-0000c001", "call", "2026-11-20T10:00:00Z", "01JD8K2M4NQ", "ada", ONE_CALL)
    dup = arrived(root, "p-0000c002", "call", "2026-11-20T10:04:00Z", "01JD8K9Q7RX", "ben", OTHER_EARS)
    before = dup.read_bytes()
    landing.mark_duplicate(root, "p-0000c001", "p-0000c002", "pair-checker")
    assert counts(root) == {"harbor": ["p-0000c001"]}
    assert landing.main([str(root), "undo", "p-0000c002", "--by", "Ada Okafor"]) == 0
    capsys.readouterr()
    assert dup.read_bytes() == before
    assert counts(root) == {"harbor": ["p-0000c001", "p-0000c002"]}
    assert listed(root, capsys) == []   # a mark taken back: the pair is kept as two
    assert "took back the mark on p-0000c002" in (root / "log.md").read_text(encoding="utf-8")


def test_three_copies_point_at_one_main(tmp_path):
    root = brain(tmp_path)
    for n, room in enumerate(("ada", "ben", "cara"), start=1):
        arrived(root, f"p-0000a00{n}", "message", "2026-11-20T09:00:00Z", "C0BQ5611FLZ/1795558392.689769", room)
    landing.mark_duplicate(root, "p-0000a001", "p-0000a002", "pair-checker")
    landing.mark_duplicate(root, "p-0000a003", "p-0000a001", "pair-checker")   # the third is the fullest
    marks = {p: pages.duplicate_of(pages.read(root / f"knowledge/sources/2026/11/{p}.md")[0])
             for p in ("p-0000a001", "p-0000a002", "p-0000a003")}
    assert marks == {"p-0000a001": "p-0000a003", "p-0000a002": "p-0000a003", "p-0000a003": None}
    assert counts(root) == {"harbor": ["p-0000a003"]}
    assert landing.duplicate_pairs(root) == []
    with pytest.raises(ValueError):
        landing.mark_duplicate(root, "p-0000a002", "p-0000a003", "pair-checker")   # a duplicate is no main


def test_own_records_alone_are_not_listed(tmp_path):
    """Two of this room's own recordings are the router's to group (route.episodes), not an arrival."""
    root = brain(tmp_path)
    for pid, at in (("p-0000b001", "2026-11-20T10:00:00Z"), ("p-0000b002", "2026-11-20T10:03:00Z")):
        write_page(root, f"knowledge/sources/2026/11/{pid}.md", id=pid, type="source", kind="call", at=at,
                   record=f"rec-{pid}", subject="harbor")
    assert landing.duplicate_pairs(root) == []


def test_a_marked_arrival_waiting_is_not_asked_to_be_filed(tmp_path):
    root = brain(tmp_path)
    waits = dict(where="_inbound/candidates", landing="pending: high stakes (pricing)", **{"for": "Ada Okafor"})
    arrived(root, "p-0000e001", "mail", "2026-11-20T09:14:05Z", "18c2f4a5b6d7e8f9", "ada", **waits)
    dup = arrived(root, "p-0000e002", "mail", "2026-11-20T09:14:41Z", "19d3a5b6c7e8f9a0", "ben",
                  **{"from": "chloe@client.example"}, **waits)
    before = dup.read_text(encoding="utf-8")
    assert sorted(i.id for i in needs_you._candidates(root)) == ["p-0000e001", "p-0000e002"]
    landing.mark_duplicate(root, "p-0000e001", "p-0000e002", "pair-checker")
    assert [i.id for i in needs_you._candidates(root)] == ["p-0000e001"]
    # the receiving room's own mark on a release that waits: the gate lets its sync push carry it
    assert gate.marks_only(before, dup.read_text(encoding="utf-8"))


def test_a_refused_verdict_is_one_line(tmp_path, capsys):
    root = brain(tmp_path)
    arrived(root, "p-0000c001", "call", "2026-11-20T10:00:00Z", "01JD8K2M4NQ", "ada")
    assert landing.main([str(root), "mark", "p-0000c001", "p-0000c009", "--by", "x"]) == 1
    assert landing.main([str(root), "undo", "p-0000c001", "--by", "x"]) == 1
    assert landing.main([str(root), "keep-both", "p-0000c001", "p-0000c001", "--by", "x"]) == 1
    err = capsys.readouterr().err
    assert err.count("refused:") == 3 and "Traceback" not in err
