"""The daily-note digest: each whole day, the entries about a project or client homed elsewhere
travel as one `journal` record (link.journal_records), which the router sends only as verbatim
parts, after the window, with a second agent's check (route.py).

The seeded test plants twenty sensitive lines in one invented day of notes and drives the worst
case through that path: a writer that copies every line of the day, plus one reworded line and
one splice. Code stops what code can see; the rest cannot leave without the second agent's check.
Every credential-shaped string is assembled at runtime, so this file passes the scan it relies on.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest
import yaml

import distill
import link
import pages
import registry
import release
import route
import spaceyaml
import sweep
from conftest import NOW, make_space, mount_brain
from test_sweep import queue_items, walled  # noqa: E402

DAY = NOW.date()                                   # the day of notes: 2026-10-12
LATER = NOW.replace(day=13, hour=12)               # the next day, when the day is whole
DIGEST = f"journal-digest-{DAY}-harbor-app"


def j(*parts: str) -> str:
    return "".join(parts)


SUBJECTS = [
    {"id": "ada", "kind": "people", "title": "Ada Okafor"},
    {"id": "harbor", "kind": "clients", "title": "Harbor", "home": "harbor",
     "classification": walled("harbor", ["ada"])},
    {"id": "harbor-app", "kind": "projects", "title": "Harbor ordering app", "part_of": "harbor", "home": "harbor"},
    {"id": "lumen", "kind": "clients", "title": "Lumen", "home": "lumen", "classification": walled("lumen", ["ada"])},
    {"id": "beacon-bid", "kind": "deals", "title": "Beacon bid", "home": "beacon",
     "classification": walled("beacon", ["ada"])},
    {"id": "beacon-pilot", "kind": "projects", "title": "Beacon pilot", "part_of": "beacon-bid", "home": "beacon"},
]
BRAIN = [{"id": "ben", "kind": "people", "title": "Ben Liu", "aliases": ["Ben"]}]

# What a teammate on the project needs: decisions, dates, who answers what, pointers, the state of the knowledge.
WORK = [
    "- The order screen now shows the delivery slot per store.",
    "- Open issue: the slot picker breaks on older tablets, tracked as issue 214.",
    "- Their operations lead answers the questions on store opening hours.",
    "- Decision: the pilot covers the three harbour-side stores only.",
    "- Pull request 57 adds the CSV import for store tables.",
    "- The design note docs/import.md is current; the old mapping sheet is stale.",
]
# The twenty planted lines, by kind. (kind, text, where): `whole` a line of its own in an entry about the
# project, `fused` inside a work sentence, `private` in the entry marked private, `other` in the entry about
# another client, `deal` in the entry about the deal.
PLANTED = [
    ("commercial", "- Their renewal is worth about 40k a year, so keep them happy.", "whole"),
    ("commercial", "- Invoice 3 goes out Friday at the agreed 9,500 euro, and the scheduling module ships in week 49.",
     "fused"),
    ("commercial", "- We bid 120k for the Beacon pilot and keep 20k of margin for the discount.", "deal"),
    ("credentials", "- Staging login for the test shop: " + j("pass", "word: ") + "Tr0ub4dor-harbor-77", "whole"),
    ("credentials", "- Deployed build 1.4 to staging with " + j("STAGING_API", "_KEY=") + "k3Jd9sL2qP8vN4xR7tY1wZ6m"
     " set on the box.", "fused"),
    ("credentials", "- The read replica is at " + j("postgres://", "orders_ro:")
     + "Wq8-harbor-replica@db.harbor.example/orders; the sync job points at it now.", "whole"),
    ("client data", "- Their customer list export (2,300 names with phone numbers) sits in the shared folder.", "whole"),
    ("client data", "- The import now reads the store table, including the 18 staff home addresses they sent by mistake.",
     "fused"),
    ("client data", "- Their HR lead sent me the full shift roster with names and phone numbers.", "private"),
    ("never leaves", "- Lantern Works payroll runs late this month because the bank moved the date.", "whole"),
    ("never leaves", "- Our bank balance covers about three months of salaries.", "whole"),
    ("never leaves", "- My own salary review is next week.", "private"),
    ("private situation", "- Chloe from their team is on sick leave until January after surgery.", "private"),
    ("private situation", "- Marta signs off the order screen; she is off next week for a hospital check.", "fused"),
    ("other client", "- Lumen's rollout slipped because their warehouse system failed the audit.", "other"),
    ("other client", "- Lumen pays late again and their finance team stopped answering.", "other"),
    ("judgement", "- Ben Liu was sloppy in the review again.", "whole"),
    ("judgement", "- The API contract is frozen, though Ben keeps second-guessing it like he always does.", "fused"),
    ("stance", "- We agreed the pilot ends on 12 December, and I will not offer the extra sprint unless they ask twice.",
     "fused"),
    ("stance", "- If they push on price, I can drop to 8,000 but not lower.", "private"),
]
assert len(PLANTED) == 20 and len({text for _, text, _ in PLANTED}) == 20


def planted(where: str) -> list:
    return [text for _, text, w in PLANTED if w == where]


NOTE = "\n".join([
    f"# {DAY}", "",
    "## 09:10Z — order screen review", "", WORK[0], WORK[1], *planted("whole")[:3], "- subject: harbor-app", "",
    "## 11:30Z — sync with the client", "", WORK[2], WORK[3], *planted("fused")[:4], *planted("whole")[3:5],
    "- subject: harbor-app", "",
    "## 12:15Z — Lumen check-in", "", "- The Lumen kickoff moved to Thursday.", *planted("other"), "- subject: lumen", "",
    "## 14:00Z — data import", "", WORK[4], WORK[5], *planted("fused")[4:], *planted("whole")[5:],
    "- subject: harbor-app", "",
    "## 15:00Z — Beacon bid", "", "- The bid document is in draft.", *planted("deal"), "- subject: beacon-bid", "",
    "## 15:30Z — Beacon pilot", "", "- The pilot plan has two phases.", "- subject: beacon-pilot", "",
    "## 16:00Z — notes for myself", "", "- The demo went well.", *planted("private"), "- subject: harbor-app",
    "- private: yes", "",
])
# the worst-case writer's own words: a line reworded, and a fused line with its middle clause spliced out
REWORDED = "- The order screen displays each store's delivery slot."
SPLICED = "- Invoice 3 goes out Friday, and the scheduling module ships in week 49."
# What code cannot see: a sales aim, a stance, staff data, the never-leaves list, a private situation of a
# person nobody registered. These rest on the writer's rule and the second agent's check (the route skill).
JUDGED = {text for kind, text, where in PLANTED
          if where in ("whole", "fused") and kind not in ("credentials", "judgement")}


@pytest.fixture
def ws(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", subjects=BRAIN)
    root = make_space(tmp_path / "ada", name="ada", routing={}, subjects=SUBJECTS,
                      never_leaves=["the company's own finances and pay"])
    mount_brain(root, brain)
    note = root / "journal" / f"{DAY:%Y}" / f"{DAY:%m}" / f"{DAY}.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(NOTE, encoding="utf-8")
    today = note.with_name(f"{LATER.date()}.md")   # the day is not whole yet: no digest of it
    today.write_text(f"# {LATER.date()}\n\n## 08:00Z — standup\n\n- Two screens left.\n- subject: harbor-app\n",
                     encoding="utf-8")
    return root


def rows(root) -> dict:
    path = root / link.JOURNAL_FEED
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return {r["id"]: r for r in map(json.loads, lines)}


def swept(root) -> dict:
    link.journal_records(root, now=LATER)
    sweep.run(root, now=LATER)
    return rows(root)


def decision(parts, **over) -> dict:
    base = {"record": DIGEST, "decision": "route", "confidence": "high", "why": "the day's work on the app",
            "checked_by": "Second agent",
            "routes": [{"space": "harbor", "subject": "harbor-app", "tier": "at_once", "why": "project work",
                        "content": "parts", "parts": [{"text": p} for p in parts]}]}
    base.update(over)
    return base


def stopped_by_code(root, text: str) -> str | None:
    """Why code keeps `text` home as a part of the digest: the router's refusal, else the release's secrets
    scan on the projection it would queue; None when only the agents' rule and check can stop it."""
    space = spaceyaml.load(root)
    reg = registry.Registry(root, space)
    try:
        _, _, _, rec = route._validate(root, space, reg, decision([text]), LATER)
    except route.RouteError as exc:
        return f"router: {exc}"
    item = root / "_scratch" / "projection.md"
    item.parent.mkdir(exist_ok=True)
    item.write_text(route._projection(rec, decision([text])["routes"][0]), encoding="utf-8")
    held = release.scrub_reason(space, None, {}, item.read_text(encoding="utf-8"), item)
    return held and f"release: {held[1]}"


def test_one_digest_a_day_per_project_or_client_never_a_deal(ws):
    found = swept(ws)
    assert sorted(found) == [DIGEST, f"journal-digest-{DAY}-lumen"]   # no deal, no part of one, not today yet
    row = found[DIGEST]
    assert (row["source"], row["kind"], row["identifiers"]) == ("journal", "digest", {"subject": "harbor-app"})
    # each entry under a heading with its own id; the private entry and other subjects' entries are not in it
    for hhmm in ("0910", "1130", "1400"):
        assert f"### journal-{DAY:%Y%m%d}T{hhmm}" in row["text"]
    for text in planted("private") + planted("other") + planted("deal") + ["- private: yes"]:
        assert text not in row["text"]
    assert link.journal_records(ws, now=LATER) == 0                     # written once
    assert not [r for r in rows(ws) if "beacon" in r]
    reg = registry.Registry(ws)
    assert [s for s in ("harbor", "harbor-app", "lumen", "beacon-bid", "beacon-pilot")
            if link.digestible(reg, s)] == ["harbor", "harbor-app", "lumen"]
    # the entries are evidence on their own: distill never counts the digest's page a second time
    assert not [e for evs in distill.evidence_by_subject(ws).values() for e in evs if e.record.startswith("journal-digest")]


def test_every_planted_line_code_can_see_is_stopped_and_the_rest_needs_the_second_check(ws):
    swept(ws)
    lines = [line for line in NOTE.splitlines() if line.strip()] + [REWORDED, SPLICED]   # the worst-case writer
    with pytest.raises(route.RouteError):
        route.record(ws, decision(lines), now=LATER)
    why = {text: stopped_by_code(ws, text) for _, text, _ in PLANTED}
    leaked = sorted(text for text, reason in why.items() if not reason and text not in JUDGED)
    assert leaked == []
    assert all(why[t].startswith("release: secrets scan") for k, t, _ in PLANTED if k == "credentials")
    for kind, text, where in PLANTED:
        if where in ("private", "other", "deal"):
            assert "not verbatim" in why[text], text
        if kind == "judgement":
            assert "Ben Liu, a registered person" in why[text], text
    for text in (REWORDED, SPLICED):
        assert "not verbatim" in stopped_by_code(ws, text)
    # a cut keeps the work half of a fused line: code lets it through, as it does every judged line
    assert stopped_by_code(ws, "- We agreed the pilot ends on 12 December") is None
    assert [t for t in JUDGED if why[t]] == []
    # so none of them leaves without a second agent's check, nor with the writer checking itself
    judged = [t for t in JUDGED]
    for bad in ({"checked_by": ""}, {"checked_by": "Writer", "routed_by": "Writer"}):
        with pytest.raises(route.RouteError, match="checked_by|second agent"):
            route.record(ws, decision(judged, **bad), now=LATER)
    assert queue_items(ws) == []


def test_a_clean_digest_leaves_once_as_parts_after_the_window(ws):
    found = swept(ws)
    space = spaceyaml.load(ws)
    whole = decision([], routes=[dict(decision([])["routes"][0], content="whole")])
    with pytest.raises(route.RouteError, match="only as parts"):
        route.record(ws, whole, now=LATER)
    to_deal = decision([WORK[0]], routes=[dict(decision([WORK[0]])["routes"][0], space="beacon", subject="beacon-bid")])
    with pytest.raises(route.RouteError, match="deal"):
        route.record(ws, to_deal, now=LATER)
    heads = [line for line in found[DIGEST]["text"].splitlines() if line.startswith("### ")]
    parts = [heads[0], WORK[0], WORK[1], heads[1], WORK[2], WORK[3],
             heads[2], WORK[4], WORK[5], "- We agreed the pilot ends on 12 December"]
    route.record(ws, decision(parts), now=LATER)   # at_once asked, to a walled home whose crew is the writer
    (item, body), = queue_items(ws)
    assert item["class"] == "window" and "a day's notes wait the window" in item["why"]
    assert pages.parse_iso(item["stage_after"]) == LATER + space.window()
    assert item["evidence"] == [DIGEST] and route.find_page(ws, DIGEST) is not None
    for text in parts:
        assert text.strip() in body
    for text in JUDGED | {t for _, t, _ in PLANTED}:
        assert text not in body


def test_without_the_router_no_digest_is_written_and_none_leaves(ws):
    swept(ws)
    data = yaml.safe_load((ws / "space.yaml").read_text(encoding="utf-8"))
    del data["routing"]
    (ws / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    (ws / sweep.WATERMARK).unlink(missing_ok=True)
    for page in (ws / "knowledge" / "sources").rglob("journal-digest-*.md"):
        page.unlink()   # the rows are swept again with the router off
    report = sweep.run(ws, now=LATER)
    assert queue_items(ws) == [] and any("a digest leaves only through the router" in p for p in report.plan)
    other = make_space(ws.parent / "plain", name="plain", subjects=SUBJECTS)
    (other / "journal" / f"{DAY:%Y}" / f"{DAY:%m}").mkdir(parents=True)
    (other / "journal" / f"{DAY:%Y}" / f"{DAY:%m}" / f"{DAY}.md").write_text(NOTE, encoding="utf-8")
    assert link.journal_records(other, now=LATER) == 0
