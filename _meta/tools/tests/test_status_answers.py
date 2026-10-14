"""The 2026-10-13 audit, batch 3: open items by direction, the system tier, attached evidence
only, the brain's projects page, today's meetings with who is who."""
import datetime as dt

import morning_view
import pages
import spaceyaml
import status
import sweep
from conftest import NOW, make_space, write_page

HARBOR = {"id": "harbor", "kind": "projects", "title": "Harbor", "state": "active"}
BEACON = {"id": "beacon", "kind": "projects", "title": "Beacon", "state": "active"}
BEN = {"id": "ben", "kind": "people", "title": "Ben Liu"}


def witness(root, name, at, **fm):
    rel = f"knowledge/sources/2026/10/{name}.md"
    return write_page(root, rel, fm.pop("body", "Body.\n"), type="source", at=at, **fm)


def page_text(root, subject):
    return (root / f"live/subjects/{subject}.md").read_text(encoding="utf-8")


def test_open_items_by_direction_and_the_system_tier(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR, BEACON], owner_email="ada@lantern.example")
    witness(root, "m1", "2026-10-11T08:00:00Z", subject="harbor", kind="mail", title="Can you send the plan?",
            **{"from": "ops@tidewater.example", "to": ["ada@lantern.example"]})
    witness(root, "m2", "2026-10-11T09:00:00Z", subject="harbor", kind="mail", title="Invoice question",
            **{"from": "ada@lantern.example", "to": ["ops@tidewater.example"]})
    witness(root, "t1", "2026-10-11T10:00:00Z", subject="harbor", kind="document", title="Fix the export",
            issue="HAR-1", state="In Progress", source="tracker")
    witness(root, "t2", "2026-10-10T10:00:00Z", subject="harbor", kind="document", title="Ship the pilot",
            issue="HAR-2", state="Done", source="tracker")
    witness(root, "x1", "2026-10-11T11:00:00Z", subject="beacon", kind="mail", title="Beacon only",
            mentions=["harbor"], **{"from": "a@beacon.example", "to": ["ada@lantern.example"]})
    status.run(root, now=NOW)
    text = page_text(root, "harbor")
    owe = text.split("### We owe")[1].split("###")[0]
    assert "Can you send the plan?" in owe
    waiting = text.split("### Waiting on them")[1].split("###")[0]
    assert "Invoice question (system" in waiting
    internal = text.split("### Internal")[1].split("## Recently closed")[0]
    assert "Fix the export (system" in internal
    closed = text.split("## Recently closed")[1].split("## Timeline")[0]
    assert "Ship the pilot" in closed
    assert "Beacon only" not in text   # a mention never attaches


def test_the_brains_projects_page_rolls_up_every_home(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain",
                       subjects=[dict(HARBOR, home="harbor", owner="Ada Okafor"), BEACON])
    status.run(brain, now=NOW)
    text = (brain / "live/projects.md").read_text(encoding="utf-8")
    assert "Harbor (`harbor`): active; owner Ada Okafor; home harbor" in text
    assert "Beacon (`beacon`)" in text


def test_todays_meetings_say_who_is_who(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[BEN], owner_email="ada@lantern.example")
    witness(root, "e1", "2026-10-12T14:00:00Z", kind="event", title="Harbor review",
            participants=["Ada Okafor", "Ben Liu", "Mo Reyes <mo@tidewater.example>", "zed@nowhere.example"])
    witness(root, "m1", "2026-10-03T08:00:00Z", kind="mail", title="Hello",
            **{"from": "Mo Reyes <mo@tidewater.example>", "to": ["ada@lantern.example"]})
    space = spaceyaml.load(root)
    sweep.write_today(root, space, NOW)
    text = (root / "live/today.md").read_text(encoding="utf-8")
    assert "2026-10-12 14:00 Harbor review" in text
    assert "Ben Liu: Ben Liu (colleague)" in text
    assert "mo@tidewater.example>: new here, last contact 2026-10-03" in text
    assert "zed@nowhere.example: first contact" in text
    assert "Ada Okafor:" not in text
    lines = morning_view._today_lines(root)
    assert lines and lines[0].startswith("- 2026-10-12 14:00")


def test_saying_an_item_is_done_closes_it_until_the_thread_moves_again(tmp_path):
    """"Item 2 of Harbor is done": the status verb closes that thread; the page lists it under
    recently closed with who said so, and a newer record in the thread opens it again."""
    root = make_space(tmp_path / "ada", subjects=[HARBOR], owner_email="ada@lantern.example")
    page = witness(root, "m1", "2026-10-11T08:00:00Z", subject="harbor", kind="mail",
                   title="Can you send the plan?", **{"from": "ops@tidewater.example", "to": ["ada@lantern.example"]})
    pid = pages.read(page)[0]["id"]
    status.run(root, now=NOW)
    assert pid in page_text(root, "harbor").split("### We owe")[1]
    status.close(root, "harbor", pid, as_="done", by="Ada Okafor", now=NOW)
    status.run(root, now=NOW)
    text = page_text(root, "harbor")
    assert "### We owe" not in text
    assert "Can you send the plan? (done by Ada Okafor" in text
    witness(root, "m2", "2026-10-13T08:00:00Z", subject="harbor", kind="mail",
            title="Re: Can you send the plan?", **{"from": "ops@tidewater.example", "to": ["ada@lantern.example"]})
    status.run(root, now=NOW + dt.timedelta(days=1))
    assert "### We owe" in page_text(root, "harbor")


def test_a_record_that_knows_its_state_wins_a_tie_in_its_thread(tmp_path):
    """Two records of one issue at the same moment: the one carrying the issue's state decides."""
    root = make_space(tmp_path / "ada", subjects=[HARBOR], owner_email="ada@lantern.example")
    witness(root, "a-old", "2026-10-11T08:00:00Z", subject="harbor", kind="document", title="HAR-9: plan",
            identifiers={"thread": "HAR-9"})
    witness(root, "z-new", "2026-10-11T08:00:00Z", subject="harbor", kind="issue", title="HAR-9: plan",
            identifiers={"thread": "HAR-9"}, issue="HAR-9", state="Todo")
    status.run(root, now=NOW)
    assert "### Internal" in page_text(root, "harbor") and "HAR-9: plan" in page_text(root, "harbor").split("### Internal")[1]


def test_one_record_witnessed_twice_shows_once(tmp_path):
    """An older sweep and a newer one both wrote the same issue: the page lists it once."""
    root = make_space(tmp_path / "ada", subjects=[HARBOR], owner_email="ada@lantern.example")
    for name in ("a-old", "z-new"):
        witness(root, name, "2026-10-11T08:00:00Z", subject="harbor", kind="issue", title="HAR-7: ship it",
                identifiers={"thread": "HAR-7"}, issue="HAR-7", state="Done")
    status.run(root, now=NOW)
    assert page_text(root, "harbor").count("HAR-7: ship it") == 2   # once closed, once on the timeline
