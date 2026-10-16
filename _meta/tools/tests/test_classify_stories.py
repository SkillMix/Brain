"""The classification gate, three stories end to end (spec section 13): a brain, the owner's
workspace, a colleague's and a late colleague's, notes shipped offline and landed by the brain."""
import json

import pytest
import yaml

import classify
import landing
import pages
import registry
import release
import route
import spaceyaml
import sweep
from conftest import NOW, make_space, mount_brain

PEOPLE = [{"id": "ada", "kind": "people", "title": "Ada Okafor", "workspace": "ada", "email": "ada@lantern.example"},
          {"id": "ben", "kind": "people", "title": "Ben Liu", "workspace": "ben", "email": "ben@lantern.example"},
          {"id": "chloe", "kind": "people", "title": "Chloe Martin", "email": "chloe@lantern.example"}]
NAMES = {"ada": "Ada Okafor", "ben": "Ben Liu", "chloe": "Chloe Martin"}
CALL = "\n".join(["Summary: the ferry timetable work.",
                  "[00:01:00] Dana: The new timetable starts in March.",
                  "[00:02:00] Ben: We send the draft by Friday."])


def company(tmp_path, client_owner="Ada Okafor", *names):
    """The brain lantern (its owner Ada is the company owner) with the client tide, registered
    with the brain as its home and no classification yet; one workspace per name."""
    brain = make_space(tmp_path / "lantern", profile="brain", name="lantern", owner="Ada Okafor",
                       subjects=PEOPLE + [{"id": "tide", "kind": "clients", "title": "Tide", "owner": client_owner,
                                           "home": "lantern", "match": {"domain": "tide.example"}}])
    spaces = {}
    for name in names:
        ws = make_space(tmp_path / name, name=name, owner=NAMES[name], owner_email=f"{name}@lantern.example",
                        routing={}, sources=[{"name": "calls", "adapter": "files", "audience": "owner"}])
        mount_brain(ws, brain)
        spaces[name] = ws
    return brain, spaces


def relay(space_root, brain):
    """One sync: the space ships what waits (its registry notes included), the brain lands them."""
    report = release.ship(space_root, now=NOW, offline_roots={"lantern": brain})
    landing.land(brain, now=NOW)
    return report


def tide(brain):
    return registry.Registry(brain).get("tide")


def captured(root, rid="c-1"):
    rec = {"id": rid, "source": "calls", "kind": "call", "at": "2026-10-12T08:00:00Z", "from": "ben@lantern.example",
           "to": [], "subject": "Timetable", "text": CALL,
           "participants": ["ben@lantern.example", "dana@tide.example"], "identifiers": {"thread": rid}}
    feed = root / "_meta" / "state" / "feed" / "calls.jsonl"
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    sweep.run(root, now=NOW)


def route_to_brain(root, rid="c-1"):
    return route.record(root, {"record": rid, "decision": "route", "confidence": "high", "why": "client work",
                               "checked_by": "Checker",
                               "routes": [{"space": "lantern", "subject": "tide", "tier": "at_once", "why": "timetable",
                                           "content": "parts",
                                           "parts": [{"text": "[00:01:00] Dana: The new timetable starts in March."}]}]},
                        now=NOW)


def held(root):
    return [pages.read(p)[0] for p in sorted((root / "_outbound" / "held").glob("*.md"))]


def released_about(root, sid):
    path = root / "_meta" / "state" / "released.jsonl"
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    return [r for r in rows if r.get("subject") == sid]


def gate_of(root, sid="tide"):
    space = spaceyaml.load(root)
    return classify.gate(space, registry.Registry(root, space), sid, "lantern")


def test_owner_holds_the_obligation_and_a_colleague_finds_the_client_first(tmp_path):
    brain, ws = company(tmp_path, "Ada Okafor", "ada", "ben")
    # the colleague's workspace finds the client first: its route is held, nothing leaves
    captured(ws["ben"])
    out = route_to_brain(ws["ben"])
    assert out["queued"] == [] and len(out["held"]) == 1
    relay(ws["ben"], brain)
    assert list((ws["ben"] / "_outbound" / "queue").glob("*.md")) == []
    assert released_about(ws["ben"], "tide") == []
    with pytest.raises(classify.ClassifyError):   # a non-owner workspace never classifies
        classify.decide(ws["ben"], "tide", "company", "ordinary work", evidence=["c-1"], now=NOW)
    # the owner's workspace holds the obligation; it asks the others before it decides
    assert classify.pending(ws["ada"], now=NOW)["decide"][0]["next"] == "ask"
    assert classify.ask(ws["ada"], "tide", now=NOW)["asked"] == ["ben"]
    relay(ws["ada"], brain)
    assert tide(brain).extra["classification"]["state"] == "asking"
    check, = classify.pending(ws["ben"], now=NOW)["check"]
    assert check["subject"] == "tide" and check["why"] == "asked"
    classify.checked(ws["ben"], "tide", works_on=True, now=NOW)   # facts only: nothing found, Ben works on it
    relay(ws["ben"], brain)
    assert classify.pending(ws["ada"], now=NOW)["decide"][0]["next"] == "decide"
    classify.obligation(ws["ada"], "tide", "confidentiality", date="2026-04-14", evidence=["m-ada-1"], now=NOW)
    classify.decide(ws["ada"], "tide", "walled", "a confidentiality agreement", evidence=["m-ada-1"],
                    crew=["ada", "ben"], home="tidewater", now=NOW)
    relay(ws["ada"], brain)
    c = tide(brain).extra["classification"]
    assert c["class"] == "walled" and c["home"] == "tidewater" and c["checked_with"] == ["ada", "ben"]
    assert tide(brain).extra["obligations"][0] == {"kind": "confidentiality", "date": "2026-04-14", "seen_in": "ada",
                                                    "at": pages.iso(NOW), "evidence": ["m-ada-1"]}
    # the colleague's held projection is taken back for the router to send to the new home
    report = relay(ws["ben"], brain)
    assert report.rerouted == 1 and [h["reason"] for h in held(ws["ben"])] == ["withdrawn"]
    assert released_about(ws["ben"], "tide") == []
    assert not [p for p in (brain / "_inbound" / "candidates").glob("*.md")
                if pages.read(p)[0].get("subject") == "tide"]


def test_a_late_colleague_holds_an_obligation_the_owner_never_saw(tmp_path):
    brain, ws = company(tmp_path, "Ada Okafor", "ada", "ben")
    classify.ask(ws["ada"], "tide", now=NOW)
    relay(ws["ada"], brain)
    classify.checked(ws["ben"], "tide", now=NOW)
    relay(ws["ben"], brain)
    classify.decide(ws["ada"], "tide", "company", "ordinary client work", evidence=["m-1"], now=NOW)
    relay(ws["ada"], brain)
    c = tide(brain).extra["classification"]
    assert c["class"] == "company" and c["state"] == "settled" and c["not_yet_checked"] == ["chloe"]
    # Chloe onboards later: her workspace tells the brain, and her first pass finds the check
    late = make_space(tmp_path / "chloe", name="chloe", owner="Chloe Martin")
    mount_brain(late, brain)
    assert classify.announce(late, now=NOW) is not None
    relay(late, brain)
    assert registry.Registry(brain).get("chloe").extra["workspace"] == "chloe"
    check, = classify.pending(late, now=NOW)["check"]
    assert check["subject"] == "tide" and check["why"] == "not_yet_checked"
    classify.obligation(late, "tide", "named_people", date="2026-09-01", evidence=["m-chloe-7"], now=NOW)
    relay(late, brain)
    c = tide(brain).extra["classification"]
    assert c["state"] == "open" and c["not_yet_checked"] == [] and "chloe" in c["checked_with"]
    # re-opened: every space holds its routes until the owner settles it again
    assert all(gate_of(root)[0] == "unclassified" for root in (ws["ada"], ws["ben"], late))
    entry, = classify.pending(ws["ada"], now=NOW)["decide"]
    assert entry["next"] == "decide" and entry["state"] == "open" and "company" in entry["needs_person"]
    assert [o["kind"] for o in entry["obligations"]] == ["named_people"]
    # company again needs the owner's yes; walled tightens and needs no one
    assert classify.decide(ws["ada"], "tide", "company", "still ordinary", evidence=["m-1"], now=NOW)["proposal"]
    out = classify.decide(ws["ada"], "tide", "walled", "named people only", evidence=["m-chloe-7"],
                          crew=["ada", "chloe"], home="tidewater", now=NOW)
    assert out["note"] is not None
    relay(ws["ada"], brain)
    assert tide(brain).extra["classification"]["class"] == "walled"


def test_the_company_owner_decides_for_an_owner_without_a_workspace(tmp_path):
    brain, ws = company(tmp_path, "Chloe Martin", "ada", "ben")
    space = spaceyaml.load(ws["ada"])
    assert classify.decider(space, registry.Registry(ws["ada"], space), "tide") == "stand_in"
    with pytest.raises(classify.ClassifyError):   # Ben is neither the owner nor the company owner
        classify.ask(ws["ben"], "tide", now=NOW)
    classify.ask(ws["ada"], "tide", now=NOW)
    relay(ws["ada"], brain)
    classify.checked(ws["ben"], "tide", now=NOW)
    relay(ws["ben"], brain)
    classify.decide(ws["ada"], "tide", "company", "ordinary client work", evidence=["m-1"], now=NOW)
    relay(ws["ada"], brain)
    c = tide(brain).extra["classification"]
    assert c["class"] == "company" and c["for"] == "Chloe Martin" and c["decided_by"] == "Ada Okafor"
    assert "chloe" in c["not_yet_checked"]
    with pytest.raises(classify.ClassifyError):
        classify.decide(ws["ben"], "tide", "walled", "x", evidence=["m-2"], crew=["ben"], home="t", now=NOW)
