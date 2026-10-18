"""The onboarding page: rendered from the onboarding report, true to it, and self-contained."""
import json
import re
from pathlib import Path

import yaml

import onboard
import onboarding_page as page
from conftest import make_space
from onboard import Report, Step

SIGN_IN = Step("doctor: github sign-in", False, "gh auth status",
               "sign in to GitHub once: run `! gh auth login --web --git-protocol https`", True)


def states(html: str) -> list:
    return re.findall(r'<li class="step state-([a-z-]+)"[^>]*>.*?<span class="name">([^<]+)', html, re.S)


def company(tmp_path, schedule="30 6 * * *"):
    """A brain and Ada's workspace, her daily chain rendered at `schedule`."""
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern-works", company="Lantern Works")
    ws = make_space(tmp_path / "ada", profile="personal", name="ada", owner="Ada Okafor")
    prompt = ws / "_meta" / "state" / "routines" / "daily-chain.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(f'---\nname: daily-chain\nschedule: "{schedule}"\n---\nRun the daily chain.\n',
                      encoding="utf-8")
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
    assert states(html)[0] == ("running", "Your machine") and "Now: doctor: python" in html
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
    assert '<p class="now failed">create ada: refused: no right to create</p>' in html


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
    assert found["Routines"] == "done"   # read from the rendered prompt in the workspace
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
    assert "<script" not in html and "&lt;script&gt;" in html and "<b>" not in html
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
