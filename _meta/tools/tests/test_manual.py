"""The manual: the "Your <company> brain" tab, written in the company's own names from the setup
journal, the plan of the history and the workspace itself, and shown first on the progress page
once part 1 of the history is read. Everything on it is escaped: every value is untrusted.

The fixtures below (`lantern_works_after_part_1` and the helpers that change it) are shared with
the progress page's tests. The shapes of `backfill.yaml` and the routine outcome lines are the
ones Tasks 10 and 11 produce."""
import datetime as dt
import html as htmllib
import json
import os
import re
from pathlib import Path

import yaml

import manual
import onboarding_page
import research
import spaceyaml
from conftest import make_space, mount_brain

TZ = dt.timezone(dt.timedelta(hours=2))
NOW = dt.datetime(2026, 10, 24, 14, 0, tzinfo=TZ)
PLAN = Path("_meta") / "state" / "backfill.yaml"
STALE = Path("_meta") / "state" / "stale.yaml"
MONTHS = {"2026-07": 400, "2026-08": 1500, "2026-09": 1500, "2026-10": 1400}
READING = {"at": "2026-10-24T13:50:00+02:00", "five_hour_pct": 20, "week_pct": 31, "extra_usage": False,
           "five_hour_resets": "2026-10-24T18:20:00+02:00", "week_resets": "2026-10-27T12:00:00+01:00"}


def write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")


def plan_of(ws: Path) -> dict:
    return yaml.safe_load((ws / PLAN).read_text(encoding="utf-8"))


def set_part(ws: Path, part: str, status: str, **fields) -> None:
    plan = plan_of(ws)
    next(p for p in plan["parts"] if p["id"] == part).update(status=status, **fields)
    write_yaml(ws / PLAN, plan)


def pause(ws: Path, part: str, resets_at=None, why: str = "usage limit reached") -> None:
    set_part(ws, part, "paused", resets_at=resets_at, why=why)


def stale(ws: Path, entry: str, since: str = "2026-10-23T04:30:00Z") -> None:
    """`_meta/state/stale.yaml` as the sync script writes it: `<routine> not started`, since when."""
    path = ws / STALE
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    write_yaml(path, {**data, entry: since})


def run_line(ws: Path, routine: str, at: str, **fields) -> None:
    line = {"routine": routine, "at": at, "space": "ada-okafor", "cost_tokens": 0, "notes": {"step": "routine"}, **fields}
    path = ws / "_meta" / "state" / "runs.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(line) + "\n")


def lock(ws: Path, routine: str = "backfill-night", part: str | None = "p2", pid: int | None = None,
         started: str = "2026-10-24T13:40:00+02:00") -> Path:
    """The lock a routine run holds while it runs (`~/.spaces/locks/<space name>.lock`: pid, routine, started, and
    the part Task 11 adds), through the test's fake home. The pid is this test process's own, which is alive."""
    path = Path.home() / ".spaces" / "locks" / f"{spaceyaml.load(ws).name}.lock"
    data = {"pid": os.getpid() if pid is None else pid, "routine": routine, "started": started}
    write_yaml(path, {**data, **({"part": part} if part else {})})
    return path


def witness(ws: Path, name: str, subject: str) -> None:
    path = ws / "knowledge" / "sources" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nid: p-{name}\ntitle: {name}\nsubject: {subject}\n---\nA record.\n", encoding="utf-8", newline="\n")


def routed(ws: Path, subject: str, times: int = 1) -> None:
    """The router's log: one line a record it placed, as `route.py` writes it."""
    path = ws / "_meta" / "state" / "routing.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for i in range(times):
            fh.write(json.dumps({"at": "2026-10-24T11:00:00Z", "record": f"r-{subject}-{i}", "decision": "route",
                                 "routes": [{"space": "ada-okafor", "subject": subject, "outcome": "attached"}]}) + "\n")


def lantern_works_after_part_1(tmp_path):
    """Ada's workspace and her setup journal at the end of part 1 of a three-part history: apps
    read, the company profile, two routines rendered, a client named Harbor with most of the
    records, and a run line. Returns (workspace, journal)."""
    brain = make_space(tmp_path / "lantern-works-brain", profile="brain", name="lantern-works", company="Lantern Works",
                       subjects=[{"id": "ada-okafor", "kind": "people", "title": "Ada Okafor"},
                                 {"id": "ben-taylor", "kind": "people", "title": "Ben Taylor"},
                                 {"id": "harbor", "kind": "clients", "title": "Harbor"},
                                 {"id": "meadow", "kind": "clients", "title": "Meadow"}])
    ws = make_space(tmp_path / "ada-okafor-workspace", profile="personal", name="ada-okafor", owner="Ada Okafor")
    mount_brain(ws, brain)
    for root, repo in ((ws, "ada-okafor-workspace"), (brain, "lantern-works-brain")):
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text(
            f'[remote "origin"]\n\turl = https://github.com/lantern-works/{repo}.git\n', encoding="utf-8")
    for name, schedule in (("daily-chain", "30 6 * * *"), ("sweep-chain", "0 7,13,19 * * *")):
        prompt = ws / "_meta" / "state" / "routines" / f"{name}.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text(f'---\nname: {name}\nschedule: "{schedule}"\n---\nRun it.\n', encoding="utf-8")
    routed(ws, "harbor", 4)
    routed(ws, "meadow")
    write_yaml(research.profile_path(), {
        "company": [{"text": "Lantern Works makes lighting for ports", "source": "site, About us", "source_id": "r1", "at": "2026-10-20"}],
        "people": [{"text": "Ada Okafor founded the company", "source": "site, Team", "source_id": "r2", "at": "2026-10-20"},
                   {"text": "Ben Taylor leads delivery", "source": "site, Team", "source_id": "r2", "at": "2026-10-20"}],
        "clients": [{"text": "Harbor is a long-standing client", "source": "file, Pitch deck.pdf", "source_id": "r3", "at": "2026-10-18"}]})
    write_yaml(ws / PLAN, {
        "started": "2026-10-24T13:00:00+02:00", "window_days": 90, "current_by": "2026-10-25T08:00:00+02:00",
        "per_token_share": 0.00002, "setup_reading": {**READING, "at": "2026-10-24T12:55:00+02:00", "five_hour_pct": 5, "week_pct": 26},
        "reading": READING,
        "parts": [
            {"id": "p1", "from": "2026-07-26", "to": "2026-08-25", "records": 1400, "status": "done",
             "checkpoints": {"mail": "2026-08-25T00:00:00Z"}, "tokens": 410000},
            {"id": "p2", "from": "2026-08-25", "to": "2026-09-25", "records": 1500, "status": "pending", "checkpoints": {}, "tokens": 0},
            {"id": "p3", "from": "2026-09-25", "to": "2026-10-24", "records": 1900, "status": "pending", "checkpoints": {}, "tokens": 0}]})
    run_line(ws, "sweep-chain", "2026-10-24T11:05:00Z", outcome="finished", why="", resets_at=None, cost_tokens=410000,
             usage={"input_tokens": 1000, "output_tokens": 2000, "cache_read_tokens": 300000, "cache_creation_tokens": 107000})
    journal = {
        "signins": {"state": "done", "github": "ada-okafor", "claude": "account"},
        "apps": {"state": "done", "feeds": "mail: owner, calendar: owner", "verdicts": [
            {"app": "Gmail", "kinds": ["mail"], "verdict": "work", "account": "ada@lanternworks.example",
             "why": "Mostly clients.", "per_month": MONTHS},
            {"app": "Google Calendar", "kinds": ["calendar"], "verdict": "work", "account": "ada@lanternworks.example",
             "why": "Meetings.", "per_month": {"2026-09": 90, "2026-10": 70}},
            {"app": "Notion", "kinds": ["documents"], "verdict": "personal", "account": "ada@home.example",
             "why": "Family notes only.", "per_month": {"2026-10": 12}}],
         "card": {"read": ["Gmail · ada@lanternworks.example · work", "Google Calendar · ada@lanternworks.example · work"],
                  "skip": ["Notion · ada@home.example · personal, Family notes only."],
                  "not_signed_in": ["Slack · not signed in"], "starts_after": "2026-10-24T13:10:00+02:00"}},
        "research": {"state": "done", "company": {"name": "Lantern Works", "domain": "lanternworks.example"}},
        "build": {"state": "done", "owner_email": "ada@lanternworks.example"},
        "history": {"state": "running"},
        "needs": [],
        "settings": [{"key": "domains", "ask": "Which domains does the company have?", "default": "sales, delivery, operations"},
                     {"key": "classification_default", "ask": "Open or closed?", "default": "open"},
                     {"key": "schedule", "ask": "How often should the sync script run, in minutes?", "default": 15},
                     {"key": "never_leaves", "ask": "What must never leave?", "default": ["client contracts", "salaries"]}]}
    return ws, journal


# ----- the brief's tests ----------------------------------------------------------------------
def test_the_manual_uses_the_companys_own_names(tmp_path):
    f = manual.facts(*lantern_works_after_part_1(tmp_path), NOW)
    html = manual.render(f)
    assert "Your Lantern Works brain" in html and "Status of Harbor" in html and "ada-okafor-workspace" in html
    assert html.count("<li") >= len(manual.NEVER)


def test_the_manual_tab_comes_first_only_after_part_1(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    page = onboarding_page.render_workspace(ws, journal, NOW)
    assert "Your Lantern Works brain" in page
    assert page.index("Your Lantern Works brain") < page.index('id="now"') < page.index('id="found"')
    set_part(ws, "p1", "running")
    assert "Your Lantern Works brain" not in onboarding_page.render_workspace(ws, journal, NOW)


# ----- the facts -------------------------------------------------------------------------------
def test_the_facts_are_read_from_the_workspace_and_the_journal(tmp_path):
    f = manual.facts(*lantern_works_after_part_1(tmp_path), NOW)
    assert f["company"] == "Lantern Works"
    assert f["apps"] == ["Gmail", "Google Calendar"]   # the ones that are read, not the personal Notion
    assert f["workspace_repo"] == "lantern-works/ada-okafor-workspace" and f["brain_repo"] == "lantern-works/lantern-works-brain"
    assert ("06:30", "Your morning briefing is made and sent to you.") in f["routines"]
    assert any(when == "07:00, 13:00 and 19:00" for when, _ in f["routines"])   # clock times, not cron lines
    assert f["people"] == ["Ada Okafor", "Ben Taylor"]
    assert "20%" in f["usage"] and "31%" in f["usage"]
    assert any("part 2" in line for line in f["next"])


def test_the_phrases_are_spec_5_1s_and_their_examples_are_the_companys_own(tmp_path):
    f = manual.facts(*lantern_works_after_part_1(tmp_path), NOW)
    assert [p for p, _ in f["phrases"]] == ["What needs me", "Status of a client", "Prep me for the call with a contact or client",
                                            "Invite a colleague", "Skip an app"]
    assert dict(f["phrases"]) == {
        "What needs me": "",
        "Status of a client": "Status of Harbor",   # the client with the most records
        "Prep me for the call with a contact or client": "Prep me for the call with Meadow",   # the next one: a different example
        "Invite a colleague": "Invite Ben Taylor",   # a person the profile names, never the owner
        "Skip an app": "Skip Gmail"}   # the first app read
    html = manual.render(manual.facts(*lantern_works_after_part_1(tmp_path / "again"), NOW))
    for invented in ("Who is on a team", "What came in from an app"):
        assert invented not in html


def test_the_second_example_is_the_next_client_else_the_only_one(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    (ws / "_meta" / "state" / "routing.jsonl").write_text("", encoding="utf-8")
    routed(ws, "harbor", 2)
    assert dict(manual.facts(ws, journal, NOW)["phrases"])["Prep me for the call with a contact or client"] == \
        "Prep me for the call with Meadow"   # the registry names two clients: the next in line, even with no record
    registry_file = ws.parent / "lantern-works-brain" / "_meta" / "subjects.yaml"
    subjects = yaml.safe_load(registry_file.read_text(encoding="utf-8"))["subjects"]
    write_yaml(registry_file, {"subjects": [s for s in subjects if s["id"] != "meadow"]})
    assert dict(manual.facts(ws, journal, NOW)["phrases"])["Prep me for the call with a contact or client"] == \
        "Prep me for the call with Harbor"


def test_missing_data_drops_the_example_and_keeps_the_phrase(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    (ws / "_meta" / "state" / "routing.jsonl").unlink()   # no counts, so no most active client
    research.profile_path().unlink()   # no profile, so no colleague named
    journal["apps"] = {}   # and no app read
    f = manual.facts(ws, journal, NOW)
    phrases = dict(f["phrases"])
    assert set(phrases) == {"What needs me", "Status of a client", "Prep me for the call with a contact or client",
                            "Invite a colleague", "Skip an app"}
    assert phrases["Status of a client"] == "" and phrases["Skip an app"] == ""
    assert phrases["Invite a colleague"] == "Invite Ben Taylor"   # no profile: the registry's other person
    html = manual.render(f)
    assert "Status of a client" in html and "Skip an app" in html and "Harbor" not in html and "Gmail" not in html


def test_the_invite_example_is_dropped_when_nobody_else_is_known(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    research.profile_path().unlink()
    registry_file = ws.parent / "lantern-works-brain" / "_meta" / "subjects.yaml"
    subjects = yaml.safe_load(registry_file.read_text(encoding="utf-8"))["subjects"]
    write_yaml(registry_file, {"subjects": [s for s in subjects if s["id"] != "ben-taylor"]})
    assert dict(manual.facts(ws, journal, NOW)["phrases"])["Invite a colleague"] == ""


def test_the_most_active_client_comes_from_the_routing_log_else_from_the_newest_pages(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    routed(ws, "meadow", 5)   # six against four: the log decides, whatever the pages say
    for i in range(3):
        witness(ws, f"harbor-{i}", "harbor")
    assert dict(manual.facts(ws, journal, NOW)["phrases"])["Status of a client"] == "Status of Meadow"
    (ws / "_meta" / "state" / "routing.jsonl").unlink()   # no router: the newest pages are counted instead
    witness(ws, "meadow-0", "meadow")
    assert dict(manual.facts(ws, journal, NOW)["phrases"])["Status of a client"] == "Status of Harbor"
    assert dict(manual.facts(ws, journal, NOW)["phrases"])["Prep me for the call with a contact or client"] == \
        "Prep me for the call with Meadow"


def test_a_subject_that_is_not_a_client_is_never_the_example(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    routed(ws, "ben-taylor", 20)   # a person with the most records is still not a client
    assert dict(manual.facts(ws, journal, NOW)["phrases"])["Status of a client"] == "Status of Harbor"


def test_one_malformed_page_does_not_cost_the_client_example(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    (ws / "_meta" / "state" / "routing.jsonl").unlink()
    witness(ws, "harbor-0", "harbor")
    bad = ws / "knowledge" / "sources" / "bad.md"
    bad.write_text("---\nid: p-bad\nrouting: {routes: 5}\n---\nA record.\n", encoding="utf-8", newline="\n")
    assert dict(manual.facts(ws, journal, NOW)["phrases"])["Status of a client"] == "Status of Harbor"


def test_a_workspace_without_a_remote_is_named_by_its_folder(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    for root in (ws, ws.parent / "lantern-works-brain"):
        (root / ".git" / "config").unlink()
    f = manual.facts(ws, journal, NOW)
    assert f["workspace_repo"] == "ada-okafor-workspace" and f["brain_repo"] == "lantern-works-brain"


def test_the_fixed_phrases_come_after_the_others(tmp_path):
    html = manual.render(manual.facts(*lantern_works_after_part_1(tmp_path), NOW))
    assert [p for p, _ in manual.FIXED_PHRASES] == ["Change the plan", "Pause my routines"]
    assert html.index("Status of Harbor") < html.index("Skip Gmail") < html.index("Change the plan") < html.index("Pause my routines")
    assert "until tomorrow morning, or a date" in html


def test_what_it_never_does_is_spec_5_1s_five_things_in_plain_sentences(tmp_path):
    assert manual.NEVER == ("It never signs anything for you.",
                            "It never sends anything to anyone but you.",
                            "It never deletes anything.",
                            "It never gives anyone access.",
                            "It never spends beyond your limits.")
    html = manual.render(manual.facts(*lantern_works_after_part_1(tmp_path), NOW))
    assert all(htmllib.escape(line) in html for line in manual.NEVER)
    assert html.index("What it never does") < html.index(manual.NEVER[0])


def test_the_manual_is_in_plain_words(tmp_path):
    html = manual.render(manual.facts(*lantern_works_after_part_1(tmp_path), NOW))
    text = re.sub(r"<[^>]+>", " ", html)
    assert not re.search(r"\.py\b|--[a-z]|mcp__|_meta/|cron|\{|\}", text)   # no command, path or tool name
    assert "—" not in html and "·" not in html   # house rule: no em dash, no middot


def test_the_manual_escapes_everything_it_is_given():
    evil = "<script>alert(1)</script>"
    html = manual.render({"company": evil, "apps": ['"><img src=x onerror=alert(2)>'], "workspace_repo": evil,
                          "brain_repo": evil, "routines": [(evil, evil)], "phrases": [(evil, evil)],
                          "people": [evil], "next": [evil], "usage": evil})
    assert "<script" not in html and "<img" not in html and "&lt;script&gt;" in html and "&lt;img" in html
    assert 'onerror="' not in html


def test_an_empty_manual_still_renders():
    html = manual.render({})
    assert "Your company brain" in html and html.count("<li") >= len(manual.NEVER)


def test_nothing_is_said_to_wait_or_not_when_the_setup_notes_cannot_be_read(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    nothing = "Nothing is waiting for you."
    assert nothing in manual.facts(ws, journal, NOW)["next"]
    assert nothing not in manual.facts(ws, {onboarding_page.UNREAD: True}, NOW)["next"]
