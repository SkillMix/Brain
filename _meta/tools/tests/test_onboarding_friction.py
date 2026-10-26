"""What a colleague's first Windows onboarding ran into: a proxy that re-signs TLS, a gh installed
after the app started, routines as OS tasks on a machine with no `claude` command, and a routine
that fails every run without anyone seeing it. And what the launch's newcomer runs ask of the
skills: setup as a guided conversation, one message per milestone in plain words, the plan's
first-read window everywhere, a sweep that saves as it reads, and the decisions it supersedes."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

import doctor
import onboard
import routine
import start
from conftest import make_space

ROOT = Path(__file__).resolve().parents[3]
SKILLS = ROOT / ".claude" / "skills"
# A space keeps its own README, has no decision log (template sync never carries it) and fills its own friction
# log, and its checks run these tests after every template sync: the tests of those files run in the template only.
template_only = pytest.mark.skipif(not (ROOT / ".github" / "template-marker").exists(),
                                   reason="runs in the template only (.github/template-marker): a space keeps its own "
                                          "README, decision log and friction log")


def skill(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def test_a_certificate_failure_is_retried_with_the_system_store(tmp_path):
    calls = []

    def run(argv, **kw):
        calls.append([str(a) for a in argv])
        if "--use-feature=truststore" in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 1, "", "SSL: CERTIFICATE_VERIFY_FAILED")
    code = start.ensure_yaml([], venv=tmp_path / "venv", run=run,
                             have=lambda: any("--use-feature=truststore" in c for c in calls))
    assert code is None
    assert calls[0][-1] == "pyyaml" and calls[1][-1] == "--use-feature=truststore"


def test_an_ordinary_pip_failure_is_not_retried():
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "externally-managed-environment")
    assert start._pip(run, [sys.executable, "-m", "pip", "install", "pyyaml"]) is False
    assert len(calls) == 1


def test_uv_gets_its_own_native_certificate_flag():
    calls = []

    def run(argv, **kw):
        calls.append([str(a) for a in argv])
        return subprocess.CompletedProcess(argv, 0 if "--native-tls" in argv else 1, "", "certificate verify failed")
    assert start._pip(run, ["C:/tools/uv/uv.exe", "pip", "install", "pyyaml"]) is True
    assert calls[1][-1] == "--native-tls"


def test_winget_installs_count_without_restarting_the_app(tmp_path):
    (tmp_path / "pf" / "GitHub CLI").mkdir(parents=True)
    (tmp_path / "local" / "Microsoft" / "WinGet" / "Links").mkdir(parents=True)
    found = doctor.system_dirs(windows=True, env={"ProgramFiles": str(tmp_path / "pf"),
                                                  "LOCALAPPDATA": str(tmp_path / "local")})
    assert tmp_path / "pf" / "GitHub CLI" in found
    assert tmp_path / "local" / "Microsoft" / "WinGet" / "Links" in found
    assert doctor.system_dirs(windows=False, env={"ProgramFiles": str(tmp_path / "pf")}) == []


def test_no_claude_command_means_no_os_routines(tmp_path, monkeypatch):
    ws = make_space(tmp_path / "ada")
    start.render_routines(ws, "Ada Okafor")
    monkeypatch.setattr(routine, "find_claude", lambda: None)
    report = onboard.Report(case="routines")
    assert onboard.register_routines(report, ws) == []
    (row,) = report.steps
    assert not row.done and "scheduled tasks" in row.result


def test_a_routine_that_fails_every_run_shows_in_the_check(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert onboard.routine_run_step("space-sweep-chain-ada").done   # not run yet
    log = tmp_path / "home" / ".spaces" / "logs" / "sweep-chain-ada.log"
    log.parent.mkdir(parents=True)
    log.write_text("\n== 2026-10-14T16:00:00 sweep-chain\nclaude is not installed or not on PATH\nexit 127\n",
                   encoding="utf-8")
    step = onboard.routine_run_step("space-sweep-chain-ada")
    assert not step.done and "claude is not installed" in step.result
    log.write_text(log.read_text(encoding="utf-8") + "\n== 2026-10-14T22:00:00 sweep-chain\ndone\nexit 0\n",
                   encoding="utf-8")
    assert onboard.routine_run_step("space-sweep-chain-ada").done


def test_the_onboard_skill_asks_nothing_after_the_signins():
    text = skill("onboard")
    assert "--os-routines" not in text and "ask nothing that has a default" in text
    assert text.index("## Phase 0") < text.index("signins --json") < text.index("apps --json")


def test_every_milestone_has_a_message_and_no_message_names_a_tool():
    msgs = (SKILLS / "onboard" / "messages.md").read_text(encoding="utf-8")
    for h in ("welcome", "mode", "signins", "leave", "apps", "company", "built", "plan", "part-1", "manual", "back-again"):
        assert f"## {h}\n" in msgs
    assert not re.search(r"\.py\b|--[a-z]|mcp__|_meta/", msgs)


@template_only
def test_the_first_read_window_is_the_plans_everywhere():
    for name in ("onboard", "sweep", "seed"):
        assert "14 days" not in skill(name)
    assert "14 days" not in (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_sweep_skill_saves_as_it_reads_and_checks_the_lock():
    text = skill("sweep")
    assert "at most 50 records per batch" in text and "routine.py --locked" in text and "backfill.py" in text


@template_only
def test_the_decision_log_records_what_onboarding_supersedes():
    log = (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    for old in ("Export watermark", "Workspace owner on the platform", "Connector permissions stay per person"):
        assert f"Supersedes \"{old}\"" in log
    assert "rule 4" in log and "card" in log


@template_only
def test_the_friction_log_the_instruction_names_ships_with_its_format():
    """The personal instruction sends every hand-off and every guardrail stop to
    `_meta/friction-log.md`, "the format is at its top": the template ships that file, so a space
    made from it has the log and its format before the first entry."""
    assert "_meta/friction-log.md` (the format is at its top)" in " ".join(
        (ROOT / "_meta" / "templates" / "personal-instruction.md").read_text(encoding="utf-8").split())
    log = (ROOT / "_meta" / "friction-log.md").read_text(encoding="utf-8")
    assert log.startswith("# Friction log\n") and "## How to write one" in log
    assert not re.search(r"^## \d{4}-", log, re.M), "the template's log holds the format, no entry"


def test_the_tests_of_template_only_files_skip_in_a_space():
    """A space's checks run these tests after every template sync. The decision log is not carried there, a space keeps
    its own README, and its friction log fills up: those three tests run in the template only (#46's marker)."""
    for test in (test_the_first_read_window_is_the_plans_everywhere, test_the_decision_log_records_what_onboarding_supersedes,
                 test_the_friction_log_the_instruction_names_ships_with_its_format):
        marks = [m for m in getattr(test, "pytestmark", []) if m.name == "skipif"]
        assert marks and "template-marker" in str(marks[0].kwargs.get("reason", "")) + str(marks[0].args), test.__name__


def test_part_ones_pending_line_names_the_waits_reason_and_a_history_in_the_background_ends_the_setup():
    """Phase 6's not-started line takes the wait's own reason, `waited.why`: the history answer's `why` is empty when
    part 1 was due (R40, N-3). The personal instruction counts a history in the background as done, so a session stops
    running the resume once the earlier phases are through (N-5)."""
    messages = (SKILLS / "onboard" / "messages.md").read_text(encoding="utf-8")
    pending = messages[messages.index("When part 1 did not start"):messages.index("When the measured pace")]
    assert "{waited.why}" in pending and "{why}" not in pending and "`waited.why`" in skill("onboard")
    instruction = " ".join((ROOT / "_meta" / "templates" / "personal-instruction.md").read_text(encoding="utf-8").split())
    assert "a `history` marked `in_background: true` counts as done" in instruction


def test_the_skills_description_and_the_personal_instruction_say_the_same_of_a_setup_partway():
    """Both count a history marked `in_background` as done, so the skill is not offered for a setup the personal
    instruction counts as finished (R43, N2-6)."""
    description = next(line for line in skill("onboard").splitlines() if line.startswith("description:"))
    assert "holds a phase not done; a history marked in_background counts as done" in description
    instruction = " ".join((ROOT / "_meta" / "templates" / "personal-instruction.md").read_text(encoding="utf-8").split())
    assert "a `history` marked `in_background: true` counts as done" in instruction
