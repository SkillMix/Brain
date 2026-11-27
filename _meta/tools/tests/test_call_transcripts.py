"""Call records: the transcript is the source, the notetaker's output a map (any notetaker).

The fixtures are three shapes notetakers deliver, inline so the tests run in every space (the
template's `_meta/tests/fixture/` never reaches one): speaker names, generic speaker labels, and
the person's notes turned into AI notes plus the transcript.
"""
import datetime as dt
import json

import distill
import pages
import sweep
from conftest import NOW, make_space

AT = "2026-10-12T08:00:00Z"
PEOPLE = ["ada@lantern.example", "ben@lantern.example", "chloe@harbor.example"]

# Speaker names; the notetaker's action list gives the task to the wrong person.
NAMED = "\n".join([
    "Notetaker's summary (Recorder, AI-written):",
    "Harbor kickoff. The team agreed the pilot scope: forty leads in the first list.",
    "Notetaker's action items (Recorder, AI-written):",
    "- Ada Okafor: send the first lead list by Friday",
    "Transcript:",
    "[00:00:04] Chloe Martin: Shall we start with the scope?",
    "[00:12:40] Ada Okafor: Forty leads in the first list works for us.",
    "[00:36:26] Chloe Martin: Ben, can you send me the first list by Friday?",
    "[00:36:31] Ben Liu: Yeah, I can send it Friday.",
    "[00:41:02] Chloe Martin: Great, thanks all.",
])
# Generic labels: the speaker is worked out from the participants and the words.
GENERIC = "\n".join([
    "Notetaker's summary (Recorder, AI-written):",
    "Weekly check-in on the Harbor pilot.",
    "Transcript:",
    "[00:00:10] Speaker 1: Morning, Chloe here. How is the list going?",
    "[00:00:18] Speaker 2: Ada speaking: twenty of forty done.",
    "[00:05:51] Speaker 1: Then the 13th for the rest?",
])
# The person's notes turned into AI notes by the notetaker, then the transcript.
NOTES = "\n".join([
    "Notetaker's summary (Notes app, AI-written):",
    "- Scope: forty leads",
    "- Chloe to confirm the port contacts",
    "Transcript:",
    "[01:02] Ada: Chloe, could you confirm the port contacts?",
    "[01:09] Chloe: I will check with the harbour master first.",
])


def space(tmp_path):
    return make_space(tmp_path / "ada", name="ada", owner_email="ada@lantern.example", routing={},
                      sources=[{"name": "calls", "adapter": "recorder", "audience": "owner"}])


def call(rid, text=NAMED, **over):
    rec = {"id": rid, "source": "calls", "kind": "call", "at": AT, "from": "ada@lantern.example", "to": [],
           "subject": "Harbor kickoff", "text": text, "participants": PEOPLE, "identifiers": {"thread": rid}}
    rec.update(over)
    return rec


def feed(root, records):
    path = root / "_meta" / "state" / "feed" / "calls.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in records))
    return path


def page(root, rid):
    found = [p for p in (root / "knowledge" / "sources").rglob("*.md") if pages.read(p)[0].get("record") == rid]
    return pages.read(found[0]) if found else None


def test_every_notetaker_shape_with_its_transcript_is_swept(tmp_path):
    root = space(tmp_path)
    feed(root, [call("c-named", NAMED, transcript="complete"), call("c-generic", GENERIC, transcript="complete"),
                call("c-notes", NOTES, transcript="complete")])
    report = sweep.run(root, now=NOW)
    assert report.witness_written == 3 and report.refused == 0
    for rid, text in (("c-named", NAMED), ("c-generic", GENERIC), ("c-notes", NOTES)):
        fm, body = page(root, rid)
        assert fm["transcript"] == "complete" and body.strip() == text.strip(), "verbatim, never corrected"


def test_a_call_that_does_not_say_what_it_holds_is_refused_until_its_line_is_fixed(tmp_path):
    root = space(tmp_path)
    path = feed(root, [call("c-silent"),                                              # says nothing
                       call("c-claims", "Notetaker's summary (Recorder, AI-written): scope.",
                            transcript="complete"),                                 # no utterance
                       call("c-why", transcript="pending")])                         # no reason
    report = sweep.run(root, now=NOW)
    assert report.refused == 3 and report.witness_written == 0
    assert sum(line.startswith("refuse ") for line in report.plan) == 3
    runs = (root / "_meta" / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(runs[-1])["notes"]["refused"] == 3, "the run sees the refusal"
    # the export fixes the lines in place; the mark stayed before them, so the next run reads them
    path.write_text("".join(json.dumps(r) + "\n" for r in [
        call("c-silent", transcript="complete"),
        call("c-claims", "Notetaker's summary (Recorder, AI-written): scope.", transcript="none",
             transcript_why="the notetaker offers no transcript for this meeting"),
        call("c-why", transcript="pending", transcript_why="the notetaker is still processing")]),
        encoding="utf-8", newline="\n")
    report = sweep.run(root, now=NOW + dt.timedelta(hours=1))
    assert report.refused == 0 and report.witness_written == 3
    assert page(root, "c-claims")[0]["transcript_why"].startswith("the notetaker offers no")


def test_a_pending_transcript_is_listed_every_run_until_it_is_fetched(tmp_path):
    root = space(tmp_path)
    summary = NAMED.split("Transcript:")[0]
    feed(root, [call("c-late", summary, transcript="pending", transcript_why="not exported in this run")])
    sweep.run(root, now=NOW)
    assert page(root, "c-late")[0]["transcript"] == "pending"
    row = ("c-late", "", "transcript")
    assert row in sweep.attachments(root)
    (root / "_meta" / "state" / "feed" / "calls.jsonl").unlink()   # a cloud run's clone goes; the list stays
    assert row in sweep.attachments(root), "a fresh clone lists it again"
    # fetched later: a call record of its own, swept although the mark has passed its time
    feed(root, [call("c-late/transcript", "Transcript:\n" + NAMED.split("Transcript:\n")[1],
                     transcript="complete")])
    report = sweep.run(root, now=NOW + dt.timedelta(hours=8))
    assert report.witness_written == 1 and page(root, "c-late/transcript")[0]["transcript"] == "complete"
    assert row not in sweep.attachments(root), "drops off once fetched"
    assert page(root, "c-late")[1].strip() == summary.strip(), "the first page keeps what came"


def candidate(root, body, evidence):
    fm = {"id": "p-0000c0de", "type": "entity", "title": "Harbor", "status": "draft",
          "generated": pages.generated("distill", NOW), "subject": "harbor", "kind": "projects",
          "target": "knowledge/projects/harbor/harbor.md", "evidence": evidence}
    return pages.write(root / "_inbound" / "candidates" / "harbor.md", fm, body)


def test_verify_checks_a_cited_moment_against_the_transcript(tmp_path):
    root = space(tmp_path)
    feed(root, [call("c-named", NAMED, transcript="complete")])
    sweep.run(root, now=NOW)
    good = candidate(root, "# Harbor\n\nChloe asked Ben for the first list by Friday. [src: c-named@00:36:26]\n"
                           "The kickoff set the pilot scope. [src: c-named]\n"
                           "The tracker issue moved. [src: SM-1@2026-10-02T15:46:23Z]\n",
                     ["c-named", "SM-1@2026-10-02T15:46:23Z"])
    assert distill.verify(root, good) == [], "a moment that exists, a plain citation, an id with @ in it"
    bad = candidate(root, "# Harbor\n\nBen sends the list. [src: c-named@01:59:00]\n"
                          "Someone else said it. [src: c-other@00:36:26]\n", ["c-named"])
    problems = distill.verify(root, bad)
    assert len(problems) == 2
    assert "no utterance at 01:59:00" in problems[0] and "c-other@00:36:26 is not in evidence" in problems[1]


def test_the_open_item_carries_the_transcripts_owner_and_the_conflict(tmp_path):
    """Behaviour scenario route-2 in full: the notetaker's action list names Ada, the transcript has
    Chloe ask Ben; the router's decision follows the transcript and the tool keeps both on the item."""
    import route
    from test_route import captured, decision, queued, space_set
    root, _, _ = space_set(tmp_path)
    captured(root, text=NAMED, participants=PEOPLE, title="Harbor kickoff")
    clause = "The notetaker's action list names Ada; in the transcript (00:36:26) Chloe asks for it from Ben."
    route.record(root, decision(routes=[{
        "space": "harbor", "subject": "harbor", "tier": "at_once", "why": "pilot work", "content": "parts",
        "parts": [{"text": "[00:36:26] Chloe Martin: Ben, can you send me the first list by Friday?"}],
        "informs": [{"kind": "open_item", "text": f"Send Chloe the first list by Friday. {clause}",
                     "owner": "Ben Liu", "direction": "we owe"}]}]))
    (fm, body), = queued(root)
    item, = fm["informs"]
    assert item["owner"] == "Ben Liu" and clause in item["text"]
    assert "Ada Okafor: send the first lead list" not in body, "the notetaker's list stays home"
    assert "- Ada Okafor: send the first lead list by Friday" in page(root, "c-1")[1], "the page keeps it as it came"
