"""The onboarding page: rendered from the onboarding report, true to it, and self-contained."""
import datetime as dt
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest
import yaml

import manual
import onboard
import onboarding_page as page
import research
import setup_journal
from conftest import make_space
from onboard import Report, Step
from test_manual import (NOW, PLAN, lantern_works_after_part_1, lock, pause, plan_of, run_line, set_part, stale,
                         write_yaml)

SIGN_IN = Step("doctor: github sign-in", False, "gh auth status",
               "sign in to GitHub once: run `! gh auth login --web --git-protocol https`", True)


def states(html: str) -> list:
    return re.findall(r'<li class="step state-([a-z-]+)"[^>]*>.*?<span class="name">([^<]+)', html, re.S)


def company(tmp_path, schedule="30 6 * * *", scheduled=True):
    """A brain and Ada's workspace, her daily chain rendered at `schedule` and, when `scheduled`,
    recorded with a read-back that passes (as `--routines-scheduled` records a harness task)."""
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern-works", company="Lantern Works")
    ws = make_space(tmp_path / "ada", profile="personal", name="ada", owner="Ada Okafor")
    prompt = ws / "_meta" / "state" / "routines" / "daily-chain.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(f'---\nname: daily-chain\nschedule: "{schedule}"\n---\nRun the daily chain.\n',
                      encoding="utf-8")
    if scheduled:
        onboard.write_yaml(ws / "_meta" / "state" / "onboard.yaml", {"routine_checks": [
            {"name": "routine scheduled 1", "check": f"test -f {prompt.as_posix()}"}]})
    return brain, ws, {"lantern-works": brain, "ada": ws}


def test_a_stopped_doctor_shows_the_sign_in_with_its_command_and_what_comes_next():
    html = page.render([Step("doctor: python", True, "python --version", "3.12"), SIGN_IN])
    assert "Waiting for you" in html and "0 of 7 done" in html
    assert "<code>! gh auth login --web --git-protocol https</code>" in html and "<h3>GitHub sign-in</h3>" in html
    assert "gh auth status" not in html   # the check stays in the agent's terminal
    found = states(html)
    assert found[0] == ("needs-you", "Your machine")
    assert all(state == "next" for state, _ in found[1:]) and found[-1][1] == "Briefing"


def test_while_a_run_is_live_the_page_reloads_and_names_the_stage_running(tmp_path):
    out = tmp_path / "onboarding.html"
    report = Report(case="start", progress=page.Live(out))
    report.add("doctor: python", True, "python --version", "3.12")
    html = out.read_text(encoding="utf-8")
    assert 'http-equiv="refresh"' in html and "Setup is running" in html
    assert states(html)[0] == ("running", "Your machine") and "Now: checking this computer." in html
    assert "doctor:" not in html   # a step's own name stays in the agent's terminal (R45, Minor 3)
    report.add("create ada", True, "test -f ada/space.yaml", "made")
    assert states(out.read_text(encoding="utf-8"))[:2] == [("done", "Your machine"), ("running", "Your spaces")]
    assert report.progress.finish(report) == out
    html = out.read_text(encoding="utf-8")
    assert 'http-equiv="refresh"' not in html and "running" not in dict(states(html)) and "Now:" not in html


def test_a_page_that_cannot_be_written_never_stops_a_setup(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("", encoding="utf-8")
    report = Report(case="start", progress=page.Live(blocked / "onboarding.html"))
    report.add("doctor: python", True, "python --version", "3.12")
    assert report.progress.finish(report) is None and len(report.steps) == 1


def test_a_failed_step_says_so_with_its_result():
    html = page.render([Step("create ada", False, "gh repo view x/ada", "refused: no right to create")])
    assert "A step did not work" in html and 'title="Did not work"' in html
    assert '<p class="now failed">Your spaces: a step did not work. Your agent has the details and says what to do.</p>' in html
    assert "refused" not in html and "create ada" not in html   # its own words go to the journal, not the page (R45, Minor 3)


def test_a_setup_names_the_company_the_person_and_the_briefing_time_from_the_spaces(tmp_path):
    _, ws, spaces = company(tmp_path, schedule="15 7 * * *")
    rows = [Step("create ada", True, "test -f ada/space.yaml", "made"),
            Step("sync task ada", True, "crontab -l", "space-sync-ada"),
            Step("first sweep ada", True, "test -f _meta/state/runs.jsonl", "0 records"),
            Step("connect mail", False, f"test -s {ws}/_meta/state/feed/mail.jsonl",
                 "sign in to your mail connector, if you have not (Claude Code: `/mcp`)", True)]
    html = page.render(rows, spaces)
    assert "<span>Lantern Works</span>" in html and "Welcome, Ada</p>" in html
    assert "<title>Lantern Works: onboarding</title>" in html
    found = dict((title, state) for state, title in states(html))
    assert "Your machine" not in found   # no doctor rows: it did not apply here
    assert found["Routines"] == "done"   # the recorded read-back passes
    assert found["Sources"] == "needs-you" and found["Briefing"] == "next"
    assert "<small>tomorrow 07:15</small>" in html   # the time from the rendered daily chain
    assert "<h3>Connect mail</h3>" in html and "<code>/mcp</code>" in html and "Waiting for you" in html


def test_once_the_person_has_signed_in_and_the_briefing_went_out_it_is_set_up(tmp_path):
    _, ws, spaces = company(tmp_path)
    runs = ws / "_meta" / "state" / "runs.jsonl"
    runs.write_text(json.dumps({"routine": "morning-view", "at": "2026-10-13T06:31:00Z", "space": "ada",
                                "notes": {"step": "send", "email": "m-1"}}) + "\n", encoding="utf-8")
    rows = [Step("create ada", True, "test -f ada/space.yaml", "made"),
            Step("connect mail", True, "test -s feed", "done: the read-back passes")]
    html = page.render(rows, spaces)
    assert "You are set up" in html and "<h2>Only you can do this</h2>" not in html   # nothing waits: no section
    assert dict((t, s) for s, t in states(html))["Briefing"] == "done"
    assert "<small>sent 2026-10-13</small>" in html


def test_steps_with_the_same_instruction_share_one_card():
    said = "sign in with `/mcp` in Claude Code"
    html = page.render([Step("connect mail", False, "test -s mail", said, True),
                        Step("connect calls", False, "test -s calls", said, True), SIGN_IN])
    assert html.count('class="yours"') == 2 and "<h3>Connect mail and calls</h3>" in html
    assert "<p>Sign in with <code>/mcp</code> in Claude Code</p>" in html


def test_a_briefing_says_tomorrow_only_once_setup_is_done(tmp_path):
    _, _, spaces = company(tmp_path, schedule="15 7 * * *")
    html = page.render([Step("doctor: python", True, "python --version", "3.12"), SIGN_IN], spaces)
    assert "<small>07:15</small>" in html and "tomorrow" not in html


def test_the_page_is_self_contained_escaped_and_in_the_house_style():
    html = page.render([Step("create <b>", False, "x", "<script>alert(1)</script>")])
    assert "<script" not in html and "alert(1)" not in html and "<b>" not in html   # a step's own words stay off the page
    # (R45, Minor 3); what the page does show from the journal and the files is escaped: the two tests below
    assert not re.search(r"https?:|<link|@import|src=", html)   # no server, no external anything
    assert "color-scheme: dark;" in html and ".wordmark::before" in html   # dark by default
    assert 'type="checkbox" id="light"' in html and ":root:has(#light:checked)" in html   # light, no script
    assert "<svg" in html and "Walled project space" in html and "The gate" in html   # how it works: a diagram
    assert "—" not in html and "·" not in html   # house rule: no em dash, no middot


def test_check_rewrites_the_page_from_what_onboarding_recorded_and_the_read_backs(tmp_path, capsys):
    _, ws, _ = company(tmp_path)
    feed = ws / "_meta" / "state" / "feed" / "mail.jsonl"
    state = {"case": "person", "offline": True, "workspace": str(ws), "spaces": {"ada": str(ws)},
             "steps": [Step("create ada", True, f"test -f {ws}/space.yaml", "made").row(),
                       Step("first sweep ada", False, "test -f runs", "failed then").row(),
                       Step("connect mail", False, f"test -s {feed.as_posix()}", "sign in", True).row()]}
    (ws / "_meta" / "state" / "onboard.yaml").write_text(yaml.safe_dump(state), encoding="utf-8")
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text('{"id": "m-1"}\n', encoding="utf-8")   # the person connected their mail since
    onboard.main([str(ws), "--check"])
    out = page.page_path()
    assert f"page: {out}" in capsys.readouterr().out
    html = out.read_text(encoding="utf-8")
    assert "failed then" not in html and dict((t, s) for s, t in states(html))["Your spaces"] == "done"
    assert "<h2>Only you can do this</h2>" not in html   # the sign-in reads back as done


def test_routines_are_done_only_once_their_schedule_reads_back(tmp_path, monkeypatch):
    _, ws, spaces = company(tmp_path, schedule="15 7 * * *", scheduled=False)
    rows = [Step("create ada", True, "test -f ada/space.yaml", "made"),
            Step("routine prompts", True, "test -f daily-chain.md", "daily-chain at 15 7 * * *"),
            Step("connect mail", False, "test -s mail", "sign in", True)]
    html = page.render(rows, spaces)
    found = dict((title, state) for state, title in states(html))
    assert found["First run"] == "done" and found["Routines"] == "next"   # rendered, not scheduled
    assert "tomorrow" not in html
    onboard.write_yaml(ws / "_meta" / "state" / "onboard.yaml", {"routine_tasks": [
        {"name": "space-daily-chain-ada", "kind": "cron", "line": "15 7 * * * routine", "check": ["crontab", "-l"]}]})
    monkeypatch.setattr(onboard, "run_cmd", lambda argv, **kw: __import__("subprocess").CompletedProcess(
        argv, 0, "15 7 * * * routine\n", ""))
    found = dict((title, state) for state, title in states(page.render(rows, spaces)))
    assert found["Routines"] == "done"


# ----- the six sections, kept true by sync without the model -----------------------------------------
KEYS = ["now", "found", "history", "usage", "waiting", "settings"]


def rows_of(tmp_path, key, mutate=None, now=NOW):
    ws, journal = lantern_works_after_part_1(tmp_path)
    if mutate:
        mutate(ws, journal)
    return page.sections(ws, journal, now)[key]


def by_label(rows):
    return {r["label"]: r["text"] for r in rows}


def test_waiting_shows_a_paused_part_and_a_missed_briefing_without_the_model(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    pause(ws, "p2", resets_at="2026-10-24T23:20:00+02:00")
    stale(ws, "daily-chain not started")
    texts = [r["text"] for r in page.sections(ws, journal, NOW)["waiting"]]
    assert any("23:20" in t for t in texts) and any("briefing" in t for t in texts)


def test_a_missed_briefing_is_named_as_sync_writes_it(tmp_path):
    texts = [r["text"] for r in rows_of(tmp_path, "waiting", lambda ws, j: stale(ws, "daily not started"))]
    assert any("morning briefing" in t and "has not started" in t for t in texts)
    texts = [r["text"] for r in rows_of(tmp_path / "again", "waiting", lambda ws, j: stale(ws, "sweep_chain not started"))]
    assert any("sweeps" in t for t in texts)


def test_a_pause_without_a_reset_time_says_so_and_invents_none(tmp_path):
    rows = rows_of(tmp_path, "waiting", lambda ws, j: pause(ws, "p2", resets_at=None))
    said = next(r["text"] for r in rows if "Part 2" in r["text"])
    assert "reset time wasn't given" in said and not re.search(r"\d\d:\d\d", said)
    assert "because your Claude limit was reached" in said   # a plain line, not the command line's own words


def test_a_pause_whose_reset_has_passed_still_names_the_time(tmp_path):
    rows = rows_of(tmp_path, "waiting", lambda ws, j: pause(ws, "p2", resets_at="2026-10-24T09:10:00+02:00"))
    said = next(r["text"] for r in rows if "Part 2" in r["text"])
    assert "should have reset at 09:10" in said and "next run" in said and "resets" not in said


def test_waiting_lists_open_needs_without_their_commands_and_the_schedulers_line(tmp_path):
    def setup(ws, journal):
        journal["needs"] = [{"kind": "reconnect", "text": "Slack needs to be connected again.",
                             "check": "python _meta/tools/start.py apps --probe --json",
                             "link": "https://claude.ai/settings/connectors", "code": "4F2A-9C1B"}]
        write_yaml(ws / "_inbound" / "needs-you" / "onboard.yaml",
                   {"items": [{"id": "onboard-domains"}, {"id": "onboard-profile"}]})
        write_yaml(ws / "_meta" / "state" / "onboard.yaml", {"routine_tasks": [
            {"name": "space-daily-chain-ada", "waiting": onboard.CRON_WAITING},
            {"name": "space-sweep-chain-ada", "waiting": onboard.CRON_WAITING}], "sync_task": {"name": "space-sync-ada"}})
    texts = [r["text"] for r in rows_of(tmp_path, "waiting", setup)]
    assert any("Slack needs to be connected again." in t and "https://claude.ai/settings/connectors" in t for t in texts)
    assert any("2 things are waiting for you" in t and "What needs me" in t for t in texts)
    assert texts.count(onboard.CRON_WAITING) == 1   # the same line from two records shows once
    assert not any("start.py" in t or "4F2A" in t for t in texts)   # no command, no one-time code


def test_a_routine_that_stopped_for_a_new_sign_in_or_a_limit_is_named(tmp_path):
    def setup(ws, journal):
        run_line(ws, "daily-chain", "2026-10-24T05:00:00Z", outcome="auth", why="signed out", resets_at=None, usage={})
        run_line(ws, "sweep-chain", "2026-10-24T11:30:00Z", outcome="limit", why="usage limit reached",
                 resets_at="2026-10-24T18:20:00+02:00", usage={})   # after the fixture's finished run of 11:05
    texts = [r["text"] for r in rows_of(tmp_path, "waiting", setup)]
    assert any("morning briefing" in t and "sign in" in t for t in texts)
    assert any("sweeps" in t and "18:20" in t for t in texts)

    def later_finished(ws, journal):
        run_line(ws, "sweep-chain", "2026-10-24T11:30:00Z", outcome="limit", why="x", resets_at=None, usage={})
        run_line(ws, "sweep_chain", "2026-10-24T12:00:00Z", outcome="finished", why="", resets_at=None, usage={})
    texts = [r["text"] for r in rows_of(tmp_path / "again", "waiting", later_finished)]
    assert not any("sweeps" in t for t in texts)   # a later run finished: nothing waits


def test_waiting_says_so_when_nothing_waits(tmp_path):
    assert [r["text"] for r in rows_of(tmp_path, "waiting")] == ["Nothing is waiting for you."]


def test_sections_work_with_no_workspace_and_no_journal():
    got = page.sections(None, {}, NOW)
    assert list(got) == KEYS
    for rows in got.values():
        assert rows and all(set(r) == {"label", "text"} and r["text"] for r in rows)


def test_now_names_the_running_part_from_the_routines_lock_and_why_it_costs(tmp_path):
    rows = rows_of(tmp_path, "now", lambda ws, j: lock(ws, "backfill-night", "p2"))   # the part is `pending` in the plan
    assert rows[0]["label"] == "Running now" and "part 2 of 3" in rows[0]["text"]
    assert "25 August to 25 September" in rows[0]["text"]
    assert rows[1]["label"] == "Why it costs" and "half of every five hours" in rows[1]["text"]


def test_now_names_the_running_phase_when_no_part_runs(tmp_path):
    def setup(ws, journal):
        journal["history"]["state"], journal["apps"]["state"] = "done", "running"
    rows = rows_of(tmp_path, "now", setup)
    assert "apps" in rows[0]["text"] and "little of your Claude limit" in rows[1]["text"]


def test_now_says_nothing_runs_and_when_the_next_routine_starts(tmp_path):
    def setup(ws, journal):
        journal["history"]["state"] = "done"
    rows = by_label(rows_of(tmp_path, "now", setup))
    assert rows["Running now"] == "Nothing is running at this moment."
    assert rows["Next"] == "Sweeps, today at 19:00."


def test_found_holds_the_app_card_the_profile_with_sources_and_the_months(tmp_path):
    rows = by_label(rows_of(tmp_path, "found"))
    assert rows["Apps read"] == "Gmail (ada@lanternworks.example), Google Calendar (ada@lanternworks.example)"
    assert rows["Apps left out"] == "Notion (ada@home.example): personal, Family notes only."
    assert rows["Not signed in"] == "Slack"
    assert rows["The company"] == "Lantern Works makes lighting for ports (source: site, About us, 2026-10-20)"
    assert rows["Clients"] == "Harbor is a long-standing client (source: file, Pitch deck.pdf, 2026-10-18)"
    assert rows["Gmail by month"] == "July 2026: 400; August 2026: 1,500; September 2026: 1,500; October 2026: 1,400"
    assert "Notion by month" not in rows   # an app that is not read has no map
    assert rows["All apps by month"].startswith("July 2026: 400;") and "September 2026: 1,590" in rows["All apps by month"]
    assert "Nothing is copied before" not in rows   # the ten minutes are over


def test_found_names_the_apps_it_saw_but_did_not_read(tmp_path):
    def sorted_apps(ws, journal):
        journal["apps"]["card"]["not_read"] = ["Dealbook · A CRM: deals and contacts.",
                                               "Quillmail · not looked at yet: I look at it the next time your apps are read"]
        journal["apps"]["card"]["nothing_to_read"] = ["Pictoria", "Tasko"]
    rows = by_label(rows_of(tmp_path, "found", sorted_apps))
    assert rows["Seen, not read yet"] == ("Dealbook: A CRM: deals and contacts.; "
                                          "Quillmail: not looked at yet: I look at it the next time your apps are read")
    assert rows["Connected, nothing to read"] == "Pictoria, Tasko"
    assert rows["Not signed in"] == "Slack"   # the rows that were there stay
    nothing = by_label(rows_of(tmp_path / "none", "found", lambda ws, journal: journal["apps"]["card"].update(
        not_read=[], nothing_to_read=[])))
    assert "Seen, not read yet" not in nothing and "Connected, nothing to read" not in nothing
    old = by_label(rows_of(tmp_path / "old", "found"))   # a card written before the sorting has neither key
    assert "Seen, not read yet" not in old and "Connected, nothing to read" not in old


def test_an_app_seen_with_no_why_is_shown_by_its_name_alone(tmp_path):
    """The card writes the bare name when the sorting run gave no why; a line kept from before, `Callwisp · `, shows as
    the name too: no "Dealbook: " and no "Callwisp: "."""
    def no_why(ws, journal):
        journal["apps"]["card"]["not_read"] = ["Dealbook", "Callwisp · ", "Tasko · A task list."]
    rows = by_label(rows_of(tmp_path, "found", no_why))
    assert rows["Seen, not read yet"] == "Dealbook; Callwisp; Tasko: A task list."


def test_found_says_when_nothing_is_copied_yet(tmp_path):
    rows = by_label(rows_of(tmp_path, "found", now=NOW - dt.timedelta(minutes=90)))
    assert rows["Nothing is copied before"] == "13:10, so you can still say what to leave out."


def test_found_names_what_it_could_not_read(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    research.profile_path().write_text("company: [unclosed", encoding="utf-8")
    texts = [r["text"] for r in page.sections(ws, journal, NOW)["found"]]
    assert any("company profile" in t and "could not be read" in t for t in texts)


def test_history_lists_every_part_with_its_status_and_how_far_it_is_read(tmp_path):
    rows = by_label(rows_of(tmp_path, "history", lambda ws, j: set_part(
        ws, "p2", "paused", checkpoints={"mail": "2026-09-02T10:00:00Z"}, resets_at=None, why="usage limit reached")))
    assert rows["Part 1"] == "Done. 26 July to 25 August, 1,400 records. Read up to 25 August."
    assert rows["Part 2"] == "Paused. 25 August to 25 September, 1,500 records. Read up to 2 September."
    assert rows["Part 3"] == "Waiting its turn. 25 September to 24 October, 1,900 records."
    assert "90 days" in rows["The plan"] and "tomorrow at 08:00" in rows["The plan"]


def test_history_says_why_the_window_was_shortened(tmp_path):
    def setup(ws, journal):
        plan = plan_of(ws)
        plan.update(window_days=56, shortened_because="Your last 56 days fit by tomorrow morning; the older weeks would not.")
        write_yaml(ws / PLAN, plan)
    rows = by_label(rows_of(tmp_path, "history", setup))
    assert "56 days" in rows["The plan"] and "older weeks would not" in rows["The plan"]


def test_history_without_a_plan_or_with_a_broken_one_says_so(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    (ws / PLAN).write_text("parts: [broken", encoding="utf-8")
    assert [r["text"] for r in page.sections(ws, journal, NOW)["history"]] == [
        "The plan for your history could not be read this time. It is tried again at the next sync."]
    (ws / PLAN).unlink()
    assert [r["text"] for r in page.sections(ws, journal, NOW)["history"]] == ["Your history has not been planned yet."]


def test_a_plan_with_parts_that_name_no_days_still_reads_plainly(tmp_path):
    def setup(ws, journal):
        plan = plan_of(ws)
        plan["parts"] = [{"id": "p1", "status": "done"}, {"id": "p2"}]
        plan.pop("current_by")
        write_yaml(ws / PLAN, plan)
    rows = rows_of(tmp_path, "history", setup)
    assert [(r["label"], r["text"]) for r in rows] == [("The plan", "The last 90 days."), ("Part 1", "Done."),
                                                       ("Part 2", "Waiting its turn.")]


def test_usage_shows_the_latest_reading_and_the_tokens_per_part(tmp_path):
    rows = by_label(rows_of(tmp_path, "usage"))
    assert rows["Your limits"] == ("20% of your 5-hour limit is used; it resets today at 18:20. 31% of your week is used; "
                                   "it resets on 27 October at 12:00. Read today at 13:50.")
    assert rows["At the start"].startswith("5% of your 5-hour limit and 26% of your week")
    assert rows["Part 1"] == "410,000 tokens, about 8% of a 5-hour limit."
    assert "Part 2" not in rows   # nothing read yet, nothing used


def test_usage_without_a_reading_or_a_measured_pace_says_so(tmp_path):
    def setup(ws, journal):
        plan = plan_of(ws)
        for key in ("reading", "setup_reading", "per_token_share"):
            plan.pop(key)
        write_yaml(ws / PLAN, plan)
    rows = by_label(rows_of(tmp_path, "usage", setup))
    assert rows["Your limits"] == "Your limits have not been read yet."
    assert rows["Part 1"] == "410,000 tokens. The share of your limit is worked out once a part has been measured."


def test_usage_says_when_extra_usage_is_on(tmp_path):
    def setup(ws, journal):
        plan = plan_of(ws)
        plan["reading"] = {**plan["reading"], "extra_usage": True, "five_hour_pct": 100}
        write_yaml(ws / PLAN, plan)
    text = by_label(rows_of(tmp_path, "usage", setup))["Your limits"]
    assert "100% of your 5-hour limit" in text and "Extra usage is on, so nothing starts until it resets" in text


def test_settings_are_the_defaults_in_plain_words(tmp_path):
    rows = by_label(rows_of(tmp_path, "settings"))
    assert rows["Areas of the company"] == "sales, delivery, operations"
    assert rows["New clients and projects"] == "shared across the company unless their owner keeps them apart"
    assert rows["How often your work is landed"] == "every 15 minutes"
    assert rows["Kept inside the space that holds it"] == "client contracts; salaries"
    assert rows["How far back to read"] == "the last 90 days"
    assert "leaves at least half of every five hours free" in rows["Pace"] and "up to 90%" in rows["Pace"]
    assert not any("sync script" in v or "?" in v for v in rows.values())   # the interview's words stay out


def test_a_section_that_fails_names_itself_and_the_others_still_render(tmp_path, monkeypatch):
    ws, journal = lantern_works_after_part_1(tmp_path)
    monkeypatch.setattr(page, "_found_rows", lambda *a: 1 / 0)
    got = page.sections(ws, journal, NOW)
    assert got["found"] == [{"label": "Could not read", "text": "This part of the page could not be read this time."}]
    assert got["history"][0]["label"] == "The plan"


def test_the_workspace_page_has_the_six_sections_before_the_stages(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    html = page.render_workspace(ws, journal, NOW)
    ids = [html.index(f'id="{key}"') for key in KEYS]
    assert ids == sorted(ids) and ids[-1] < html.index('<ol class="steps"') < html.index("<h2>How it works</h2>")
    assert "Updated" in html and '<meta http-equiv="refresh" content="300">' in html   # an open tab follows the page
    assert "—" not in html and "·" not in html   # house rule: no em dash, no middot


def test_the_workspace_page_keeps_the_stages_of_the_recorded_setup(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    state = {"case": "person", "spaces": {"ada-okafor": str(ws)},
             "steps": [Step("create ada", True, "test -f x", "made").row(),
                       Step("connect mail", False, "test -s y", "sign in", True).row()]}
    write_yaml(ws / "_meta" / "state" / "onboard.yaml", state)
    html = page.render_workspace(ws, journal, NOW)
    assert dict((t, s) for s, t in states(html))["Your spaces"] == "done" and "<h2>Only you can do this</h2>" not in html


def test_everything_from_the_journal_and_the_files_is_escaped(tmp_path):
    evil, img = "<script>alert(1)</script>", '"><img src=x onerror=alert(2)>'
    ws, journal = lantern_works_after_part_1(tmp_path)
    journal["apps"]["verdicts"][0]["app"] = img
    journal["apps"]["card"]["read"] = [f"{img} · {evil} · work"]
    journal["needs"] = [{"kind": "connect-app", "text": f"{evil} <b>bold</b>", "check": "x", "link": "javascript:alert(3)"}]
    write_yaml(research.profile_path(), {"company": [{"text": evil, "source": img, "at": "2026-10-20"}]})
    set_part(ws, "p2", "paused", why=evil, resets_at=None)
    journal["settings"] = [{"key": "domains", "ask": evil, "default": evil}]
    stale(ws, f"{img} not started")
    html = page.render_workspace(ws, journal, NOW)
    assert "<script" not in html and "<img" not in html and "<b>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "&quot;&gt;&lt;img src=x" in html
    assert 'href="javascript' not in html
    assert not re.search(r"<[a-z]+[^>]*\sonerror\s*=", html)   # no tag carries a handler


def test_a_link_in_a_need_is_a_link_and_only_when_it_is_https(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    journal["needs"] = [{"kind": "connect-app", "text": "Connect an app.", "check": "x",
                         "link": "https://claude.ai/settings/connectors"}]
    html = page.render_workspace(ws, journal, NOW)
    assert ('<a href="https://claude.ai/settings/connectors" rel="noopener noreferrer">'
            'https://claude.ai/settings/connectors</a>') in html


# ----- refresh: the page rewritten by the sync script, without the model, a process or a side effect -----------
def record_scheduler(ws):
    """The routine tasks as the onboarding recorded them for the OS scheduler (a Windows task and a cron line)."""
    write_yaml(ws / "_meta" / "state" / "onboard.yaml", {
        "case": "person", "spaces": {"ada-okafor": str(ws)},
        "steps": [{"name": "create ada", "done": True, "check": "test -f x", "result": "made", "needs_owner": False}],
        "routine_tasks": [
            {"name": "space-daily-chain-ada", "kind": "schtasks", "check": ["schtasks", "/Query", "/TN", "space-daily-chain-ada", "/XML"]},
            {"name": "space-sweep-chain-ada", "kind": "cron", "line": "0 7,13,19 * * * routine", "check": ["crontab", "-l"]}]})


def no_process(monkeypatch):
    """Anything that would start a command (the scheduler's read-back, git, the model) fails the test."""
    def boom(name):
        def fail(*args, **kwargs):
            raise AssertionError(f"refresh started a process: {name}")
        return fail
    for name in ("run", "Popen", "check_output", "check_call", "call"):
        monkeypatch.setattr(subprocess, name, boom(f"subprocess.{name}"))
    for name in ("system", "startfile"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, boom(f"os.{name}"))
    monkeypatch.setattr(onboard, "run_cmd", boom("onboard.run_cmd"))
    monkeypatch.setattr(onboard, "spawn_cmd", boom("onboard.spawn_cmd"))


def test_refresh_rewrites_the_page_from_the_workspace_and_starts_no_process(tmp_path, monkeypatch):
    ws, journal = lantern_works_after_part_1(tmp_path)
    setup_journal.save(journal)
    record_scheduler(ws)   # tasks are recorded, so the stages would read them back from the scheduler: they must not
    lock(ws)   # a live run: its process is asked about, never started
    no_process(monkeypatch)
    out = page.refresh(ws)
    assert out == page.page_path() and out.parent == Path.home() / ".spaces"
    html = out.read_text(encoding="utf-8")
    assert "Your Lantern Works brain" in html and 'id="waiting"' in html
    assert dict((t, s) for s, t in states(html))["Routines"] == "done"   # from the recorded state, the stage is reached
    assert b"\r\n" not in out.read_bytes()   # LF on every system


def test_a_direct_render_still_reads_the_schedule_back_and_the_workspace_page_never_does(tmp_path, monkeypatch):
    ws, journal = lantern_works_after_part_1(tmp_path)
    record_scheduler(ws)
    asked = []
    monkeypatch.setattr(onboard, "run_cmd", lambda argv, **kw: asked.append(list(argv)) or subprocess.CompletedProcess(argv, 1, "", ""))
    html = page.render([], {"ada-okafor": str(ws)}, workspace=ws)   # the person's own check: it reads back
    assert asked and dict((t, s) for s, t in states(html))["Routines"] != "done"
    asked.clear()
    page.render_workspace(ws, journal, NOW)
    assert asked == []


def test_refresh_follows_the_workspace_as_it_changes(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    setup_journal.save(journal)
    assert "Paused." not in page.refresh(ws).read_text(encoding="utf-8")
    pause(ws, "p2", resets_at="2026-10-24T23:20:00+02:00")
    assert "Paused." in page.refresh(ws).read_text(encoding="utf-8")


def test_refresh_is_for_a_persons_workspace_only(tmp_path):
    ws, _ = lantern_works_after_part_1(tmp_path)
    assert page.refresh(ws.parent / "lantern-works-brain") is None   # the brain has no history of its own
    assert page.refresh(tmp_path / "nowhere") is None
    assert not page.page_path().exists()


def test_a_corrupt_journal_is_said_in_plain_words_and_no_copy_of_it_is_left_behind(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    path = setup_journal.journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    for text in ("key: [unclosed", "- a\n- list\n", "\x00\x01 not text"):
        path.write_text(text, encoding="utf-8")
        for _ in range(3):   # every sync: the notes are only read
            html = page.refresh(ws).read_text(encoding="utf-8")
        assert "Your setup notes could not be read this time" in html
        assert "No app has been read yet" not in html and "Nothing is waiting for you" not in html   # nothing is claimed
        assert 'id="history"' in html and "Part 1" in html   # what the workspace itself holds is still there
        assert path.read_text(encoding="utf-8") == text   # left as it was
        assert list(path.parent.glob("setup.yaml.bad-*")) == []


def test_a_journal_that_cannot_be_opened_is_said_too_and_a_missing_or_empty_one_is_not_an_error(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    path = setup_journal.journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()   # a folder where the notes should be: it cannot be read
    assert "Your setup notes could not be read this time" in page.refresh(ws).read_text(encoding="utf-8")
    path.rmdir()
    assert "Your setup notes could not be read" not in page.refresh(ws).read_text(encoding="utf-8")   # none yet: nothing to read
    path.write_text("", encoding="utf-8")
    html = page.refresh(ws).read_text(encoding="utf-8")
    assert "Your setup notes could not be read" not in html and "No app has been read yet." in html   # read, and empty


def test_what_the_notes_would_say_is_not_known_when_they_cannot_be_read(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    got = page.sections(ws, {page.UNREAD: True}, NOW)
    said = {r["label"]: r["text"] for r in got["found"] + got["settings"]}
    assert said["Apps read"] == "Not known: your setup notes could not be read this time."
    assert said["Setup defaults"] == "Not known: your setup notes could not be read this time."
    assert "Pace" in said and "How far back to read" in said   # what the plan holds is still shown


def test_a_corrupt_onboard_yaml_is_a_row_and_the_page_is_still_written(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    setup_journal.save(journal)
    (ws / "_meta" / "state" / "onboard.yaml").write_text("steps: [broken", encoding="utf-8")
    html = page.refresh(ws).read_text(encoding="utf-8")
    assert "The record of your scheduled tasks could not be read this time" in html
    assert "Your Lantern Works brain" in html and 'id="history"' in html


def test_a_plan_whose_parts_are_not_a_list_is_a_row_and_the_page_is_still_written(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    setup_journal.save(journal)
    plan = plan_of(ws)
    plan["parts"] = 5
    write_yaml(ws / PLAN, plan)
    html = page.refresh(ws).read_text(encoding="utf-8")
    assert "The plan for your history could not be read this time" in html
    assert "Your history has not been planned yet" not in html and 'id="waiting"' in html


def test_an_odd_schedule_default_does_not_stop_the_page(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    for odd in (float("inf"), float("nan"), "soon", [1, 2], {"a": 1}, 10 ** 30, True):
        journal["settings"] = [{"key": "schedule", "ask": "x", "default": odd}]
        setup_journal.save(journal)
        html = page.refresh(ws).read_text(encoding="utf-8")
        assert "about every 15 minutes" in html and 'id="settings"' in html   # the footer keeps to the default


def test_a_page_that_cannot_be_built_is_a_page_that_says_so(tmp_path, monkeypatch):
    ws, journal = lantern_works_after_part_1(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(page, "render_workspace", lambda *a, **k: 1 / 0)
        out = page.refresh(ws)
    html = out.read_text(encoding="utf-8")
    assert "This page could not be built this time" in html and "It is tried again at the next sync" in html
    assert "<script" not in html


def test_a_stage_list_that_cannot_be_read_in_full_is_a_note_and_the_rest_is_there(tmp_path, monkeypatch):
    ws, journal = lantern_works_after_part_1(tmp_path)
    setup_journal.save(journal)
    monkeypatch.setattr(page, "_routines_fact", lambda workspace, *a, **k: 1 / 0 if workspace is not None else None)   # odd data in the workspace's own records
    html = page.refresh(ws).read_text(encoding="utf-8")
    assert "The list of setup steps could not be read in full this time" in html
    assert 'id="history"' in html and "Your Lantern Works brain" in html


def test_a_page_that_cannot_be_written_is_an_error_the_caller_sees(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    page.page_path().mkdir(parents=True)   # a folder where the page should be
    with pytest.raises(OSError):
        page.refresh(ws)


def test_the_page_is_written_whole_or_not_at_all(tmp_path, monkeypatch):
    ws, journal = lantern_works_after_part_1(tmp_path)
    setup_journal.save(journal)
    first = page.refresh(ws).read_text(encoding="utf-8")

    def fail(*args, **kwargs):
        raise OSError("the replace failed")
    pause(ws, "p2", resets_at="2026-10-24T23:20:00+02:00")
    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail)
        with pytest.raises(OSError):
            page.refresh(ws)
    assert page.page_path().read_text(encoding="utf-8") == first   # the old page, whole
    assert [p.name for p in page.page_path().parent.iterdir() if p.name.startswith("onboarding.html")] == ["onboarding.html"]


def test_the_page_after_check_and_the_final_page_of_a_run_keep_the_sections(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    setup_journal.save(journal)
    write_yaml(ws / "_meta" / "state" / "onboard.yaml", {"case": "person", "spaces": {"ada-okafor": str(ws)}, "steps": []})
    checked = page.after_check(ws, [Step("connect mail", True, "test -s x", "done")], path=tmp_path / "checked.html")
    assert 'id="history"' in checked.read_text(encoding="utf-8") and 'http-equiv="refresh"' not in checked.read_text(encoding="utf-8")
    report = Report(case="start", progress=page.Live(tmp_path / "live.html"))
    report.spaces["ada-okafor"] = ws
    report.add("create ada", True, "test -f x", "made")
    assert 'id="history"' not in (tmp_path / "live.html").read_text(encoding="utf-8")   # while it runs: the stages only
    final = report.progress.finish(report).read_text(encoding="utf-8")   # at the end: the whole page, which nothing rewrites yet
    assert 'id="history"' in final and 'http-equiv="refresh"' not in final


# ----- fix round 1: zones, the routines' lock, plain words, links ---------------------------------------------
def test_a_reset_time_without_a_zone_is_this_computers_local_time_as_task_10_writes_it(tmp_path, monkeypatch):
    for hours in (-4, 5):   # a computer west of UTC, and one east of it
        monkeypatch.setattr(page, "LOCAL_ZONE", dt.timezone(dt.timedelta(hours=hours)))

        def setup(ws, journal):   # `routine.outcome`'s shape: local, no zone, to the second
            pause(ws, "p2", resets_at="2026-10-24T23:20:00")
            run_line(ws, "sweep-chain", "2026-10-24T11:30:00Z", outcome="limit", why="x", resets_at="2026-10-24T23:20:00", usage={})
        rows = rows_of(tmp_path / f"zone{hours}", "waiting", setup)
        said = {r["label"]: r["text"] for r in rows}
        assert "It can start again today at 23:20" in said["Paused"]   # not 19:20 or 04:20: it is not read as UTC
        assert "It resets today at 23:20" in said["Stopped"]


def test_a_reset_time_with_a_zone_keeps_its_zone(tmp_path, monkeypatch):
    monkeypatch.setattr(page, "LOCAL_ZONE", dt.timezone(dt.timedelta(hours=-4)))
    rows = rows_of(tmp_path, "waiting", lambda ws, j: pause(ws, "p2", resets_at="2026-10-24T23:20:00+02:00"))
    assert "today at 23:20" in next(r["text"] for r in rows if r["label"] == "Paused")
    rows = rows_of(tmp_path / "utc", "waiting", lambda ws, j: pause(ws, "p2", resets_at="2026-10-24T21:20:00Z"))
    assert "today at 17:20" in next(r["text"] for r in rows if r["label"] == "Paused")   # UTC is shown on this computer's clock


def test_a_passed_reset_time_says_it_should_have_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(page, "LOCAL_ZONE", dt.timezone(dt.timedelta(hours=-4)))

    def setup(ws, journal):
        run_line(ws, "sweep-chain", "2026-10-24T11:30:00Z", outcome="limit", why="x", resets_at="2026-10-24T07:10:00", usage={})
    said = next(r["text"] for r in rows_of(tmp_path, "waiting", setup) if r["label"] == "Stopped")
    assert "should have reset at 07:10" in said and "resets" not in said and "yesterday" not in said


def test_the_command_lines_own_words_never_reach_the_page(tmp_path):
    cases = {"You've hit your session limit · resets 11:20pm (America/New_York)": "because your Claude limit was reached",
             "Claude's sign-in was refused": "because Claude needs you to sign in again",
             "the run started to draw on extra usage": "because it would have used extra, paid usage",
             "stopped after an hour": "because it ran longer than an hour",
             "claude stopped: error_during_execution": "."}
    for i, (why, plain) in enumerate(cases.items()):
        rows = rows_of(tmp_path / f"why{i}", "waiting", lambda ws, j: pause(ws, "p2", resets_at=None, why=why))
        said = next(r["text"] for r in rows if r["label"] == "Paused")
        assert plain in said and said.startswith("Part 2 of your history is paused")
        for raw in ("America", "session", "·", "refused", "error_during", "draw on"):
            assert raw not in said


def test_only_a_needs_own_link_becomes_a_link(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    journal["needs"] = [{"kind": "connect-app", "text": "Connect Slack, see https://evil.example/path for why.", "check": "x",
                         "link": "https://claude.ai/settings/connectors"},
                        {"kind": "it-admin", "text": "Ask your administrator.", "check": "x", "link": "http://claude.ai/plain"}]
    pause(ws, "p2", resets_at=None, why="see https://evil.example/why")
    html = page.render_workspace(ws, journal, NOW)
    assert re.findall(r'<a href="([^"]+)"', html) == ["https://claude.ai/settings/connectors"]   # the need's link field, only
    assert "https://evil.example/path" in html   # the address in a sentence is only text
    assert "http://claude.ai/plain" not in html   # and a link that is not https is not shown at all


HELD = "paid account: the person starts the read"   # the reason `backfill.hold` writes under the plan's top-level `waiting`
READY = "Your history is ready to read when you say so."


def test_a_plan_that_waits_for_the_persons_word_gets_a_row(tmp_path):
    def hold(ws, journal):
        plan = plan_of(ws)
        plan["waiting"] = HELD
        write_yaml(ws / PLAN, plan)
    texts = [r["text"] for r in rows_of(tmp_path, "waiting", hold)]
    assert READY in texts
    assert HELD not in " ".join(texts)   # the plan's own words stay off the page

    def said_go(ws, journal):   # `backfill.start` removes the key
        hold(ws, journal)
        plan = plan_of(ws)
        plan.pop("waiting")
        write_yaml(ws / PLAN, plan)
    assert READY not in [r["text"] for r in rows_of(tmp_path / "go", "waiting", said_go)]


def test_an_api_key_account_is_not_held_unless_the_plan_says_so(tmp_path):
    def api_key(ws, journal):
        journal["signins"]["claude"] = "api-key"
    assert READY not in [r["text"] for r in rows_of(tmp_path, "waiting", api_key)]   # no flag: nothing is held

    def api_key_and_held(ws, journal):
        api_key(ws, journal)
        plan = plan_of(ws)
        plan["waiting"] = HELD
        write_yaml(ws / PLAN, plan)
    assert READY in [r["text"] for r in rows_of(tmp_path / "held", "waiting", api_key_and_held)]


def test_a_plan_that_waits_shows_no_row_while_a_run_holds_the_lock(tmp_path):
    def setup(ws, journal):
        plan = plan_of(ws)
        plan["waiting"] = HELD
        write_yaml(ws / PLAN, plan)
        lock(ws, "backfill-night", "p2")
    assert READY not in [r["text"] for r in rows_of(tmp_path, "waiting", setup)]


def test_the_running_routine_and_part_come_from_the_routines_lock(tmp_path):
    rows = by_label(rows_of(tmp_path, "history", lambda ws, j: lock(ws, "backfill-night", "p2")))
    assert rows["Part 2"].startswith("Reading now. 25 August to 25 September")   # the plan says `pending`
    assert rows["Part 3"].startswith("Waiting its turn.")
    rows = rows_of(tmp_path / "sweep", "now", lambda ws, j: lock(ws, "sweep-chain", None))
    assert rows[0] == {"label": "Running now", "text": "Your sweeps are running."}
    assert "a little of your Claude limit" in rows[1]["text"]
    rows = rows_of(tmp_path / "other", "now", lambda ws, j: lock(ws, "daily-chain", None))
    assert rows[0]["text"] == "Your morning briefing is running."


def test_a_part_the_plan_calls_running_is_not_running_without_a_lock(tmp_path):
    def setup(ws, journal):
        set_part(ws, "p2", "running")
    assert rows_of(tmp_path, "now", setup)[0]["text"] == "Nothing is running at this moment."
    assert by_label(rows_of(tmp_path / "again", "history", setup))["Part 2"].startswith("Waiting its turn.")


def test_a_lock_that_is_dead_old_from_the_future_or_corrupt_is_no_run(tmp_path):
    cases = {"dead": dict(pid=2 ** 31 - 2),
             "old": dict(started="2026-10-24T09:00:00+02:00"),   # five hours: the pid is another process's by now
             "future": dict(started="2026-10-24T15:00:00+02:00")}
    for name, kwargs in cases.items():
        rows = rows_of(tmp_path / name, "now", lambda ws, j: lock(ws, "backfill-night", "p2", **kwargs))
        assert rows[0]["text"] == "Nothing is running at this moment.", name

    def corrupt(ws, journal):
        path = lock(ws)
        path.write_text("pid: [broken", encoding="utf-8")
    assert rows_of(tmp_path / "corrupt", "now", corrupt)[0]["text"] == "Nothing is running at this moment."


def test_the_tabs_are_a_labelled_group_and_the_sections_have_titles(tmp_path):
    ws, journal = lantern_works_after_part_1(tmp_path)
    html = page.render_workspace(ws, journal, NOW)
    assert 'role="radiogroup" aria-label="Pick a tab"' in html
    for key in KEYS:
        assert f'<section class="facts" id="{key}" aria-labelledby="{key}-title">' in html and f'<h2 id="{key}-title">' in html


def test_a_card_time_and_a_reading_time_without_a_zone_are_local_too(tmp_path, monkeypatch):
    monkeypatch.setattr(page, "LOCAL_ZONE", dt.timezone(dt.timedelta(hours=-4)))

    def setup(ws, journal):
        journal["apps"]["card"]["starts_after"] = "2026-10-24T13:10:00"   # 13:10 local: 19:10 on the clock of the test's NOW
        plan = plan_of(ws)
        plan["reading"] = {**plan["reading"], "at": "2026-10-24T07:50:00", "five_hour_resets": "2026-10-24T12:20:00"}
        write_yaml(ws / PLAN, plan)
    ws, journal = lantern_works_after_part_1(tmp_path)
    setup(ws, journal)
    found = by_label(page.sections(ws, journal, NOW - dt.timedelta(minutes=90))["found"])
    assert found["Nothing is copied before"] == "13:10, so you can still say what to leave out."
    limits = by_label(page.sections(ws, journal, NOW)["usage"])["Your limits"]
    assert "it resets today at 12:20." in limits and "Read today at 07:50." in limits


def test_the_latest_readings_key_is_one_constant(tmp_path, monkeypatch):
    ws, journal = lantern_works_after_part_1(tmp_path)
    plan = plan_of(ws)
    plan["latest"] = plan.pop(page.LATEST_READING)   # a key Task 11 may choose
    write_yaml(ws / PLAN, plan)
    assert by_label(page.sections(ws, journal, NOW)["usage"])["Your limits"] == "Your limits have not been read yet."
    monkeypatch.setattr(page, "LATEST_READING", "latest")
    assert "20% of your 5-hour limit" in by_label(page.sections(ws, journal, NOW)["usage"])["Your limits"]
    assert "20%" in manual.facts(ws, journal, NOW)["usage"]   # the manual reads the same key
    assert page.FIRST_READING == "setup_reading"


# ----- fix round 2: what a pause needs, an odd schedule, a setup that owns the page, old stops ------------------
# Task 11's own words for why a part paused (`backfill.WHY`), as `after_run` writes them next to `resets_at`.
WHY = {
    "limit": "Your Claude limit was reached, so this part stopped; the next run goes on from where it got to.",
    "limit_no_reset": ("Your Claude limit was reached and Claude didn't say when it resets, so the next run "
                       "tries again from where this one got to."),
    "overage": "The run would have used extra usage, so it stopped; the read goes on when your limit has room.",
    "timeout": "The run took an hour and was stopped; the next run goes on from where it got to.",
    "auth": "Claude's sign-in ran out; sign in again and the read goes on from where it got to.",
    "unfinished": "The run used up its fifty minutes; the next run goes on from where it got to.",
}


def paused_text(tmp_path, why, resets_at=None):
    rows = rows_of(tmp_path, "waiting", lambda ws, j: pause(ws, "p2", resets_at=resets_at, why=why))
    return next(r["text"] for r in rows if r["label"] == "Paused")


def test_a_pause_says_a_reset_time_only_for_a_limit_and_otherwise_what_happens_next(tmp_path):
    said = paused_text(tmp_path / "limit", WHY["limit"], "2026-10-24T23:20:00+02:00")
    assert "because your Claude limit was reached" in said and "It can start again today at 23:20" in said
    said = paused_text(tmp_path / "no-reset", WHY["limit_no_reset"])
    assert "because your Claude limit was reached" in said and "The reset time wasn't given" in said
    said = paused_text(tmp_path / "overage", WHY["overage"])   # extra usage and no time: it is the same to say
    assert "because it would have used extra, paid usage" in said and "The reset time wasn't given" in said
    for kind, cause, next_step in (("timeout", "because it ran longer than an hour", "It carries on from where it got to at its next run."),
                                   ("auth", "because Claude needs you to sign in again", "Sign in again and it carries on from where it got to."),
                                   ("unfinished", "after a long run", "It carries on from where it got to at its next run.")):
        said = paused_text(tmp_path / kind, WHY[kind])
        assert cause in said and next_step in said, kind
        assert "reset" not in said, kind   # nothing to say about a time that was never to be given
    assert paused_text(tmp_path / "odd", "something else") == "Part 2 of your history is paused."   # no cause, no time: no claim


def test_an_odd_schedule_default_such_as_a_superscript_or_a_huge_number_does_not_cost_the_page(tmp_path):
    for odd in ("²", "9" * 5000, " 15 ", "0", "-5", "1441"):
        assert page.sync_minutes({"settings": [{"key": "schedule", "default": odd}]}) == 15
    assert page.sync_minutes({"settings": [{"key": "schedule", "default": "20"}]}) == 20
    ws, journal = lantern_works_after_part_1(tmp_path)
    for odd in ("²", "9" * 5000):
        journal["settings"] = [{"key": "schedule", "ask": "x", "default": odd}]
        setup_journal.save(journal)
        html = page.refresh(ws).read_text(encoding="utf-8")
        assert "about every 15 minutes" in html and "Your Lantern Works brain" in html


def set_lock(path, pid, age_hours=0.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("" if pid is None else f"{pid}\n", encoding="utf-8")
    then = time.time() - age_hours * 3600
    os.utime(path, (then, then))


def test_a_setup_that_holds_the_start_lock_keeps_the_page_it_is_writing(tmp_path):
    import start
    ws, journal = lantern_works_after_part_1(tmp_path)
    setup_journal.save(journal)
    live = page.page_path()
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text("the live page of a setup that is running", encoding="utf-8")
    set_lock(start.lock_path(), os.getpid())   # a live process, a fresh lock
    assert page.refresh(ws) is None and live.read_text(encoding="utf-8") == "the live page of a setup that is running"
    set_lock(start.lock_path(), None)   # no readable id: it waits out an hour, and this one is new
    assert page.refresh(ws) is None and live.read_text(encoding="utf-8") == "the live page of a setup that is running"


def test_a_stale_start_lock_does_not_keep_the_page(tmp_path):
    import start
    ws, journal = lantern_works_after_part_1(tmp_path)
    setup_journal.save(journal)
    for name, pid, age in (("dead process", 2 ** 31 - 2, 0.0), ("old lock, reused id", os.getpid(), start.PID_REUSE_HOURS + 1),
                           ("no id, old", None, start.LOCK_HOURS + 1), ("no lock at all", "gone", 0)):
        live = page.page_path()
        live.parent.mkdir(parents=True, exist_ok=True)
        live.write_text("a page from earlier", encoding="utf-8")
        if pid == "gone":
            start.lock_path().unlink(missing_ok=True)
        else:
            set_lock(start.lock_path(), pid, age)
        out = page.refresh(ws)
        assert out == live and "Your Lantern Works brain" in live.read_text(encoding="utf-8"), name


def test_a_stop_older_than_a_week_drops_off_the_page_and_an_older_one_shows_its_date(tmp_path):
    def setup(ws, journal):
        for routine, at in (("backfill-night", "2026-10-16T12:00:00+02:00"),   # eight days: gone
                            ("daily-chain", "2026-10-18T12:00:00+02:00")):   # six days: still there, with its day
            run_line(ws, routine, at, outcome="auth", why="x", resets_at=None, usage={})
    said = [r["text"] for r in rows_of(tmp_path, "waiting", setup) if r["label"] == "Stopped"]
    assert said == ["Your morning briefing stopped on 18 October because Claude needs you to sign in again. "
                    "The step is in your Needs you list."]


def test_a_stop_from_today_has_no_date_and_one_from_yesterday_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(page, "LOCAL_ZONE", dt.timezone(dt.timedelta(hours=2)))

    def setup(ws, journal):
        run_line(ws, "sweep-chain", "2026-10-24T13:30:00+02:00", outcome="limit", why="x", resets_at="2026-10-24T18:20:00", usage={})   # after the fixture's finished run
        run_line(ws, "daily-chain", "2026-10-23T05:00:00+02:00", outcome="limit", why="x", resets_at="2026-10-23T06:10:00", usage={})
    said = {r["text"] for r in rows_of(tmp_path, "waiting", setup) if r["label"] == "Stopped"}
    assert "Your sweeps stopped at your Claude limit. It resets today at 18:20." in said
    assert ("Your morning briefing stopped at your Claude limit yesterday. It should have reset yesterday at 06:10, "
            "so it starts again at its next time.") in said


def test_a_stop_whose_reset_passed_days_ago_shows_the_day_of_that_reset(tmp_path):
    def setup(ws, journal):
        run_line(ws, "backfill-night", "2026-10-20T12:30:00+02:00", outcome="limit", why="x",
                 resets_at="2026-10-20T18:20:00+02:00", usage={})
    said = next(r["text"] for r in rows_of(tmp_path, "waiting", setup) if r["label"] == "Stopped")
    assert said == ("Your history reading stopped at your Claude limit on 20 October. It should have reset on 20 October at 18:20, "
                    "so it starts again at its next time.")


# ----- one computer, one runner, and a pause (Task 14) ------------------------------------------------
import routine   # noqa: E402


def hosted(ws, machine="m-desktop", name="ADA-DESKTOP", monkeypatch=None, here=None) -> None:
    """The record of where Ada's routines run: on `machine` (called `name`); this computer is `here`."""
    monkeypatch.setattr(routine, "this_machine", lambda: machine)
    monkeypatch.setattr(routine, "machine_name", lambda: name)
    routine.claim_host(ws, "os", NOW - dt.timedelta(days=3))
    monkeypatch.setattr(routine, "this_machine", lambda: here or machine)


def test_a_pause_shows_its_clock_time_and_no_routine_is_called_not_started_meanwhile(tmp_path, monkeypatch):
    def setup(ws, journal):
        hosted(ws, monkeypatch=monkeypatch)
        routine.set_pause(ws, "2026-10-25T06:00:00+02:00")
        stale(ws, "daily not started")   # a paused briefing does not start: that is the pause, not the scheduler
    texts = [r["text"] for r in rows_of(tmp_path, "waiting", setup)]
    assert "Routines paused until tomorrow at 06:00. Say 'resume my routines' to start them sooner." in texts
    assert not any("not started" in t or "Schedule my routines" in t for t in texts)
    over = [r["text"] for r in rows_of(tmp_path / "later", "waiting", setup, now=NOW + dt.timedelta(days=1))]
    assert not any(t.startswith("Routines paused") for t in over)   # a pause that ended is no line


def test_routines_another_computer_stopped_running_are_offered_here(tmp_path, monkeypatch):
    def setup(ws, journal):
        hosted(ws, monkeypatch=monkeypatch, here="m-laptop")
        stale(ws, "daily not started", since="2026-10-22T06:30:00+02:00")   # on the clock it was written for
        stale(ws, "sweep_chain not started", since="2026-10-23T07:00:00+02:00")
    texts = [r["text"] for r in rows_of(tmp_path, "waiting", setup)]
    assert texts.count("Your routines haven't run on ADA-DESKTOP since 22 October; say 'run my routines here' to run "
                       "them on this computer.") == 1
    assert not any("Schedule my routines" in t for t in texts)   # this computer's scheduler is not the one that stopped
    assert not any(t.startswith("Your routines run on") for t in texts)   # one line on the other computer, not two
    quiet = [r["text"] for r in rows_of(tmp_path / "quiet", "waiting", lambda ws, j: hosted(ws, monkeypatch=monkeypatch, here="m-laptop"))]
    assert quiet == ["Your routines run on ADA-DESKTOP. Say 'run my routines here' to run them on this computer."]


def test_on_the_computer_that_runs_them_a_routine_not_started_is_still_this_schedulers(tmp_path, monkeypatch):
    def setup(ws, journal):
        hosted(ws, monkeypatch=monkeypatch)
        stale(ws, "daily not started")
    texts = [r["text"] for r in rows_of(tmp_path, "waiting", setup)]
    assert any("morning briefing has not started" in t and "Schedule my routines" in t for t in texts)


def test_a_record_of_where_the_routines_run_that_is_not_one_is_said_plainly(tmp_path):
    def setup(ws, journal):
        (ws / routine.HOST).write_text('machine: "m-a"\nmachine: "m-b"\nrunner: "os"\n', encoding="utf-8")
        stale(ws, "daily not started")
    texts = [r["text"] for r in rows_of(tmp_path, "waiting", setup)]
    assert page.HOST_UNCLEAR in texts and not any("not started" in t for t in texts)


def in_the_cloud(ws, monkeypatch, here="m-laptop") -> None:
    """The record says Ada's sweep runs in the cloud, as a scheduled task; this computer is `here`."""
    routine.claim_cloud(ws, {"sweep": "trig_01AAAAAAAA"}, "Europe/Amsterdam", ["ada/ada"],
                        {"read": ["mcp__mail__get_message"], "write": [], "send": []}, NOW - dt.timedelta(days=3))
    monkeypatch.setattr(routine, "this_machine", lambda: here)


def test_routines_in_the_cloud_are_no_other_computer_on_the_page(tmp_path, monkeypatch):
    """The cloud is no computer: the page says the sweep runs there as a scheduled task and the daily on the computer
    it moved from, and that 'run my routines here' brings the sweep to this computer, never "on the cloud"."""
    assert page.HOST_CLOUD == ("Your sweep runs in the cloud, as a scheduled task, and your daily on the computer it "
                               "moved from. Say 'run my routines here' to bring the sweep back to this computer.")
    quiet = [r["text"] for r in rows_of(tmp_path, "waiting", lambda ws, j: in_the_cloud(ws, monkeypatch))]
    assert quiet == [page.HOST_CLOUD]

    def missed(ws, journal):
        in_the_cloud(ws, monkeypatch)
        stale(ws, "sweep_chain not started", since="2026-10-23T07:00:00+02:00")
    texts = [r["text"] for r in rows_of(tmp_path / "missed", "waiting", missed)]
    assert texts == ["Your routines haven't run in the cloud since 23 October; say 'run my routines here' to bring them "
                     "to this computer."]

    def paused(ws, journal):
        in_the_cloud(ws, monkeypatch)
        routine.set_pause(ws, "2026-10-25T06:00:00+02:00")
        stale(ws, "sweep_chain not started")
    texts = [r["text"] for r in rows_of(tmp_path / "paused", "waiting", paused)]
    assert texts == ["Routines paused until tomorrow at 06:00. Say 'resume my routines' to start them sooner.",
                     page.HOST_CLOUD]
    on_this_one = [r["text"] for r in rows_of(tmp_path / "here", "waiting",
                                              lambda ws, j: in_the_cloud(ws, monkeypatch, here=routine.CLOUD_MACHINE))]
    assert on_this_one == [page.HOST_CLOUD]   # whatever the computer's id, the cloud is said as the cloud
    assert not any("on the cloud" in t for t in quiet + texts + on_this_one)


def test_a_daily_that_did_not_start_under_the_cloud_is_said_as_this_computers_own(tmp_path, monkeypatch):
    """The daily stays on the computer the sweep moved from: there its miss is that computer's scheduler, never the
    cloud's; on another computer every miss is the cloud's line."""
    def missed(ws, journal):
        monkeypatch.setattr(routine, "this_machine", lambda: "m-desk")   # the move runs here, and stays here
        routine.claim_cloud(ws, {"sweep": "trig_01AAAAAAAA"}, "Europe/Amsterdam", ["ada/ada"],
                            {"read": ["mcp__mail__get_message"], "write": [], "send": []}, NOW - dt.timedelta(days=3))
        stale(ws, "daily not started", since="2026-10-22T06:30:00+02:00")
        stale(ws, "sweep_chain not started", since="2026-10-23T07:00:00+02:00")
    texts = [r["text"] for r in rows_of(tmp_path, "waiting", missed)]
    assert texts[0].startswith("Your routines haven't run in the cloud since 23 October")
    assert any("morning briefing has not started" in t and "Schedule my routines" in t for t in texts[1:])

    def elsewhere(ws, journal):
        missed(ws, journal)
        monkeypatch.setattr(routine, "this_machine", lambda: "m-laptop")
    texts = [r["text"] for r in rows_of(tmp_path / "other", "waiting", elsewhere)]
    assert texts == ["Your routines haven't run in the cloud since 22 October; say 'run my routines here' to bring "
                     "them to this computer."]


def proved(ws, chain, at, left_out=1, **seen) -> None:
    """One proof run as it lands: its proof line (`cloud.py proof-line`), every refusal its four tries owe seen unless
    `seen` says not (the command's change not refused at once, `shell`, so it rides to the landing), then the cloud
    sync line of the proof's own landing (`notes.proof`), which left `left_out` protected paths out."""
    run_line(ws, "cloud-proof", at, **{"chain": chain, "edit": True, "tool": True, "network": True, "shell": False,
                                       **seen})
    run_line(ws, "sync", at, cloud=chain, notes={"left_out": left_out, "proof": True})


def test_routines_the_cloud_stopped_are_said_stopped_and_offered_here(tmp_path, monkeypatch):
    """The record says cloud, so every computer's own sweep steps aside, and a proof showed a refusal missing, so the
    cloud's task stays off: the sweep runs nowhere. The page says so where it says where the routines run, with what
    the cloud no longer refuses and 'run my routines here', and no line that it runs in the cloud or missed a run.
    Paused as well, the pause goes unsaid: resuming starts nothing while the cloud's task stays off."""
    assert page.HOST_STOPPED == ("Your sweep is stopped: the cloud no longer refuses {what}, so its scheduled task stays "
                                 "off. Say 'run my routines here' to run it on this computer.")
    said = page.HOST_STOPPED.format(what="a change to a protected file")

    def stopped(ws, journal):
        in_the_cloud(ws, monkeypatch)
        proved(ws, "sweep", "2026-10-23T09:00:00+02:00", edit=False)
        stale(ws, "sweep_chain not started", since="2026-10-23T06:30:00+02:00")
    rows = rows_of(tmp_path, "waiting", stopped)
    assert [(r["label"], r["text"]) for r in rows] == [("Routines", said)]

    def paused(ws, journal):
        stopped(ws, journal)
        assert routine.set_pause(ws, "2026-10-25T06:00:00+02:00")["paused_until"]
        assert routine.paused_until(routine.host(ws), NOW) is not None   # the pause holds, and goes unsaid
    assert [r["text"] for r in rows_of(tmp_path / "paused", "waiting", paused)] == [said]

    def mended(ws, journal):
        stopped(ws, journal)
        proved(ws, "sweep", "2026-10-23T11:00:00+02:00")   # proved again since: the refusals hold now
    assert [r["text"] for r in rows_of(tmp_path / "mended", "waiting", mended)] == [
        "Your routines haven't run in the cloud since 23 October; say 'run my routines here' to bring them to this "
        "computer."]

    def let_through(ws, journal):   # a landing that left nothing out is a refusal missing too
        in_the_cloud(ws, monkeypatch)
        proved(ws, "sweep", "2026-10-23T09:00:00+02:00", left_out=0)
    assert [r["text"] for r in rows_of(tmp_path / "landed", "waiting", let_through)] == [
        page.HOST_STOPPED.format(what="a protected file changed with a command, on its way to main")]

    def reached_out(ws, journal):   # a request past the network limit that went through is a refusal missing as well
        in_the_cloud(ws, monkeypatch)
        proved(ws, "sweep", "2026-10-23T09:00:00+02:00", network=False)
    assert [r["text"] for r in rows_of(tmp_path / "network", "waiting", reached_out)] == [
        page.HOST_STOPPED.format(what="a request to a host outside its network limit")]

    def refused_at_once(ws, journal):   # the command's change refused in the run: nothing reached a landing to leave out
        stopped(ws, journal)
        proved(ws, "sweep", "2026-10-23T11:00:00+02:00", shell=True, left_out=0)
    assert [r["text"] for r in rows_of(tmp_path / "shell", "waiting", refused_at_once)] == [
        "Your routines haven't run in the cloud since 23 October; say 'run my routines here' to bring them to this "
        "computer."]

    def landed_as_a_sweep(ws, journal):   # a landing that is not the proof's own says nothing of the proof's change
        stopped(ws, journal)
        run_line(ws, "cloud-proof", "2026-10-23T11:00:00+02:00", chain="sweep", edit=True, tool=True, network=True,
                 shell=False)
        run_line(ws, "sync", "2026-10-23T11:00:00+02:00", cloud="sweep", notes={"left_out": 1, "proof": False})
    assert [r["text"] for r in rows_of(tmp_path / "plain", "waiting", landed_as_a_sweep)] == [said]

    def made_new(ws, journal):   # tasks made new for a proof: the routines run where they ran
        hosted(ws, monkeypatch=monkeypatch, here="m-laptop")
        proved(ws, "sweep", "2026-10-23T09:00:00+02:00", edit=False)
    assert [r["text"] for r in rows_of(tmp_path / "new", "waiting", made_new)] == [
        "Your routines run on ADA-DESKTOP. Say 'run my routines here' to run them on this computer."]


def test_the_computers_name_is_escaped_on_the_page(tmp_path, monkeypatch):
    ws, journal = lantern_works_after_part_1(tmp_path)
    hosted(ws, name='<img src=x onerror="alert(1)">', monkeypatch=monkeypatch, here="m-laptop")
    stale(ws, "daily not started")
    html = page.render_workspace(ws, journal, NOW)
    assert "<img src=x" not in html and "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html


def test_a_paused_run_is_neither_a_stop_nor_a_part_that_failed(tmp_path, monkeypatch):
    def setup(ws, journal):
        hosted(ws, monkeypatch=monkeypatch)
        routine.set_pause(ws, "2026-10-25T06:00:00+02:00")
        run_line(ws, "backfill-night", "2026-10-24T11:30:00Z", outcome="paused", why="paused until 2026-10-25 06:00",
                 resets_at=None, usage={})
        run_line(ws, "sweep-chain", "2026-10-24T11:40:00Z", outcome="paused", why="paused until 2026-10-25 06:00",
                 resets_at=None, usage={})
    ws, journal = lantern_works_after_part_1(tmp_path)
    setup(ws, journal)
    got = page.sections(ws, journal, NOW)
    assert not any(r["label"] == "Stopped" for r in got["waiting"])
    parts = by_label(got["history"])
    assert parts["Part 2"].startswith("Waiting its turn.") and parts["Part 3"].startswith("Waiting its turn.")


def test_where_the_routines_run_that_cannot_be_read_is_a_plain_row(tmp_path, monkeypatch):
    def broken(ws):
        raise RuntimeError("the record could not be read")
    monkeypatch.setattr(routine, "host", broken)
    rows = rows_of(tmp_path, "waiting", lambda ws, journal: None)
    assert {"label": "Could not read", "text": "Where your routines run could not be read this time. It is tried again "
                                               "at the next sync."} in rows



# ===== Fix wave F2 (Ruling R45) ===================================================================================
import backfill   # noqa: E402


def sync_line(ws, at, **notes):
    """A run line as sync.py writes it (`record`): its notes, `conflict` among them when a merge stopped."""
    path = ws / "_meta" / "state" / "runs.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"routine": "sync", "at": at, "space": "ada-okafor", "cost_tokens": 0, "notes": notes}) + "\n")


def test_a_sync_that_could_not_take_in_the_other_computers_changes_is_a_waiting_row(tmp_path):
    """The last sync's merge stopped (`notes.conflict`, as sync.py writes it): what this computer did is not shared,
    and the page says so plainly (R45, I-1). An older stop, or a later sync that landed, is no row."""
    rows = by_label(rows_of(tmp_path, "waiting", lambda ws, j: sync_line(ws, "2026-10-24T11:55:00Z", conflict=True)))
    said = rows["Not saved"]
    assert "other computer" in said and "merge" not in said and "conflict" not in said
    old = by_label(rows_of(tmp_path / "old", "waiting", lambda ws, j: sync_line(ws, "2026-10-24T09:00:00Z", conflict=True)))
    assert "Not saved" not in old

    def landed(ws, journal):
        sync_line(ws, "2026-10-24T11:40:00Z", conflict=True)
        sync_line(ws, "2026-10-24T11:55:00Z", committed=2)
    assert "Not saved" not in by_label(rows_of(tmp_path / "landed", "waiting", landed))


def test_the_build_s_note_on_the_briefing_is_a_waiting_row(tmp_path):
    """The routines cannot send the briefing (no mailbox, no Git Bash, the guard not proven): the build kept why, and
    the page says it, so nobody waits for a briefing that does not come (R45, I-6)."""
    def setup(ws, journal):
        journal["build"]["routines_note"] = routine.NO_MAILBOX
    rows = by_label(rows_of(tmp_path, "waiting", setup))
    assert rows["Your briefing"] == routine.NO_MAILBOX
    assert "Your briefing" not in by_label(rows_of(tmp_path / "none", "waiting"))


def test_a_part_whose_run_read_nothing_says_so_and_after_three_such_runs_needs_a_look(tmp_path):
    """An idle part has its own cause; three idle runs in a row are a Waiting line of their own (R45, Minor 1)."""
    said = paused_text(tmp_path / "once", backfill.WHY["idle"])
    assert "because its last run ended without reading it" in said and "It tries again at its next run." in said
    rows = rows_of(tmp_path / "thrice", "waiting",
                   lambda ws, j: set_part(ws, "p2", "paused", why=backfill.WHY["idle"], resets_at=None, idle=3))
    found = by_label(rows)
    assert "Paused" not in found and "Part 2 of your history was not read in its last 3 runs" in found["Needs a look"]


def test_after_an_offline_build_the_page_promises_no_schedule(tmp_path):
    """Offline, nothing was registered with the scheduler: the page says the routines start once setup runs online,
    and names no time (R45, F-10)."""
    def offline(ws, journal):
        journal["history"]["state"] = "done"
        write_yaml(ws / "_meta" / "state" / "onboard.yaml", {"case": "person", "offline": True})
    rows = by_label(rows_of(tmp_path, "now", offline))
    assert rows["Next"] == page.OFFLINE_NEXT and "19:00" not in rows["Next"]
    assert "online" in page.OFFLINE_NEXT and page.rendered_routines(tmp_path / "ada-okafor-workspace") == []
    briefing = page.stages([], workspace=tmp_path / "ada-okafor-workspace", now=NOW)[-1]
    assert briefing["title"] == "Briefing" and briefing["sub"] == ""


def test_apps_the_person_skipped_are_not_listed_as_read_wherever_the_page_reads_them(tmp_path):
    """A skip saved while the apps phase wrote its card: the card and the verdicts still list the app, the person's
    word (`person_skips`) leaves it out of what was read and of the months (R47, N3-2)."""
    def skipped(ws, journal):
        journal["apps"]["person_skips"] = ["Google Calendar"]
    rows = by_label(rows_of(tmp_path, "found", skipped))
    assert "Google Calendar" not in rows["Apps read"] and "Google Calendar by month" not in rows
    assert page.apps_read({"apps": {"person_skips": ["Gmail"], "verdicts": [
        {"app": "Gmail", "verdict": "work", "account": "a"}]}}) == []


def test_a_lock_that_names_its_process_start_is_read_as_the_routines_and_start_read_it(tmp_path):
    """A lid closed for hours: the lock is old, its pid runs, and that pid started when the lock says, so the run is
    still the one that took it (Ruling R45, I-8), on the page as in routine._live and start.take_lock. Another start
    time is another process with that number: no run, and no setup keeping the page."""
    import routine
    import start
    mine = routine.process_started(os.getpid())
    if not mine:
        pytest.skip("this system does not say when a process started")
    for name, mark, running in (("same process, old lock", mine, True), ("reused pid", "1999-01-01T00:00:00", False)):
        def setup(ws, journal, mark=mark):
            path = lock(ws, "backfill-night", "p2", started="2026-10-24T02:00:00+02:00")   # twelve hours before NOW
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            path.write_text(yaml.safe_dump({**data, "pid_started": mark}), encoding="utf-8")
        text = rows_of(tmp_path / name.replace(" ", "-").replace(",", ""), "now", setup)[0]["text"]
        assert (text != "Nothing is running at this moment.") is running, name
        set_lock(start.lock_path(), os.getpid(), start.PID_REUSE_HOURS + 1)
        start.lock_path().write_text(f"{os.getpid()}\n{mark}\n", encoding="utf-8")
        then = time.time() - (start.PID_REUSE_HOURS + 1) * 3600
        os.utime(start.lock_path(), (then, then))
        assert page.setup_running() is running, name
    start.lock_path().unlink(missing_ok=True)
