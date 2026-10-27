"""What the rc1 setup run on a second Windows account ran into, one issue at a time: the desktop app
is a packaged Windows app, so what setup writes under %APPDATA% lands in a private copy that the
scheduled tasks never see (#7); a copy of the template takes itself for the template (#11); the
briefing address (#13); apps still loading when they are looked at (#3, #8); a first push that failed
without a word (#12, #14); and the smaller ones (#5, #6, #9, #15, the company site)."""
import datetime as dt
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import apps
import doctor
import gate
import onboard
import routine
import spaceyaml
import start
import sync
from conftest import make_space
from test_sync import FakeGh, repo, run_lines, write  # noqa: F401 (repo is a fixture)
from test_sync_task import world


# ----- #7: Python and the GitHub sign-in where the scheduler sees them -------------------------------------
ROOT = Path(__file__).resolve().parents[3]


def appdata(tmp_path) -> dict:
    """A Windows environment whose %APPDATA% is under the test's folder."""
    roaming = tmp_path / "AppData" / "Roaming"
    roaming.mkdir(parents=True)
    return {"APPDATA": str(roaming), "LOCALAPPDATA": str(tmp_path / "AppData" / "Local"), "PATH": ""}


def test_the_windows_bootstrap_installs_python_under_the_spaces_folder():
    text = (ROOT / "_meta" / "tools" / "bootstrap.ps1").read_text(encoding="utf-8")
    assert "$env:UV_PYTHON_INSTALL_DIR = Join-Path $tools 'python'" in text
    assert text.index("UV_PYTHON_INSTALL_DIR") < text.index("python install 3.12")


def test_gh_keeps_its_sign_in_under_the_spaces_folder_and_takes_the_one_the_app_made(tmp_path):
    env, home = appdata(tmp_path), tmp_path / "home"
    made = Path(env["APPDATA"]) / "GitHub CLI"
    made.mkdir()
    (made / "hosts.yml").write_text("github.com:\n    user: ada\n", encoding="utf-8")
    doctor.use_tools(home, env=env, windows=True)
    assert env["GH_CONFIG_DIR"] == str(home / ".spaces" / "gh")
    assert (home / ".spaces" / "gh" / "hosts.yml").read_text(encoding="utf-8") == "github.com:\n    user: ada\n"
    # a sign-in made again in the app is taken again; an older one is left alone
    (made / "hosts.yml").write_text("github.com:\n    user: ada-okafor\n", encoding="utf-8")
    later = time.time() + 60
    os.utime(made / "hosts.yml", (later, later))
    doctor.use_tools(home, env=env, windows=True)
    assert "ada-okafor" in (home / ".spaces" / "gh" / "hosts.yml").read_text(encoding="utf-8")


def test_a_gh_config_folder_already_chosen_is_kept_and_other_systems_are_left_alone(tmp_path):
    env = {**appdata(tmp_path), "GH_CONFIG_DIR": str(tmp_path / "mine")}
    doctor.use_tools(tmp_path / "home", env=env, windows=True)
    assert env["GH_CONFIG_DIR"] == str(tmp_path / "mine")
    env = appdata(tmp_path / "mac")
    doctor.use_tools(tmp_path / "home", env=env, windows=False)
    assert "GH_CONFIG_DIR" not in env


def a_venv(venv: Path) -> None:
    for program in ("Scripts/python.exe", "bin/python"):   # the tests run on every system
        (venv / program).parent.mkdir(parents=True, exist_ok=True)
        (venv / program).write_text("", encoding="utf-8")


def venv_from(tmp_path, home: Path) -> Path:
    venv = tmp_path / "venv"
    a_venv(venv)
    (venv / "pyvenv.cfg").write_text(f"home = {home}\nversion = 3.12.7\n", encoding="utf-8")
    return venv


def test_a_venv_made_from_a_python_the_scheduler_cannot_see_is_made_again(tmp_path, monkeypatch):
    env = appdata(tmp_path)
    hidden = Path(env["APPDATA"]) / "uv" / "python" / "cpython-3.12.7-windows-x86_64-none"
    venv = venv_from(tmp_path, hidden)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "home" / ".spaces" / "tools" / "python" / "python.exe"))
    calls = []

    def run(argv, **kw):
        calls.append([str(a) for a in argv])
        if argv[1:3] == ["-m", "venv"]:
            a_venv(Path(argv[3]))
        return subprocess.CompletedProcess(argv, 0, "", "")
    code = start.ensure_yaml(["build"], venv=venv, run=run, have=lambda: False, windows=True, env=env)
    assert code == 0 and not (venv / "pyvenv.cfg").exists()   # the old venv went; the new one is made from this Python
    assert [c for c in calls if c[1:3] == ["-m", "venv"]] == [[sys.executable, "-m", "venv", str(venv)]]
    assert not [c for c in calls if "--user" in c]   # a --user install lands under %APPDATA% too


def test_a_venv_from_a_python_the_scheduler_sees_is_kept(tmp_path, monkeypatch):
    env = appdata(tmp_path)
    venv = venv_from(tmp_path, tmp_path / "home" / ".spaces" / "tools" / "python")
    calls = []

    def run(argv, **kw):
        calls.append([str(a) for a in argv])
        return subprocess.CompletedProcess(argv, 0, "", "")
    assert start.ensure_yaml([], venv=venv, run=run, have=lambda: False, windows=True, env=env) == 0
    assert (venv / "pyvenv.cfg").exists() and not [c for c in calls if c[1:3] == ["-m", "venv"]]


def test_the_probe_task_on_windows_writes_what_it_said_to_a_log(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Windows")
    calls = []
    monkeypatch.setattr(onboard, "run_cmd", world({}, calls))
    log = Path.home() / ".spaces" / "logs" / "space-probe-ada.log"
    record = onboard.register_task(onboard.Report("person"), "routine probe task", "space-probe-ada", tmp_path / "ada",
                                   [tmp_path / "ada" / "routine.py", "--probe", tmp_path / "ada"], times=[(3, 33)],
                                   stderr_log=log)
    argv, files = next(c for c in calls if c[0][:2] == ["schtasks", "/Create"])
    xml = files[argv[argv.index("/XML") + 1]].decode("utf-16")
    line = f'"{sys.executable}" "{tmp_path / "ada" / "routine.py"}" --probe "{tmp_path / "ada"}"'
    assert (f'<Arguments>--headless cmd.exe /d /s /c "{line} 2&gt;&gt;"{log}" &amp; '
            f'echo {onboard.TASK_ENDED}&gt;&gt;"{log}""</Arguments>') in xml
    assert record["log"] == str(log) and log.parent.is_dir()


def test_a_probe_that_ends_without_a_result_fails_at_once_with_what_it_said(tmp_path, monkeypatch):
    log = tmp_path / "space-probe-ada.log"
    log.write_text("from an earlier build\n", encoding="utf-8")

    def run_now(record, dry_run=False):
        with open(record["log"], "a", encoding="utf-8") as fh:
            fh.write("No Python at '\"C:\\Users\\ada\\AppData\\Roaming\\uv\\python\\cpython-3.12\\python.exe'\n"
                     f"{onboard.TASK_ENDED}\n")
        return True
    monkeypatch.setattr(onboard, "run_now", run_now)
    clock = [0.0]
    monkeypatch.setattr(start, "_clock", lambda: clock[0])
    monkeypatch.setattr(start, "_sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    seen = start._probe_once({"name": "space-probe-ada", "kind": "schtasks", "log": str(log)})
    assert seen["why"] == start.PROBE_ENDED and seen["why"] in start.PROBE_PLAIN and clock[0] < 30
    assert "No Python at" in seen["detail"] and "earlier build" not in seen["detail"]
    assert onboard.TASK_ENDED not in seen["detail"]


def test_the_probe_proves_the_scheduler_s_runs_can_use_the_github_sign_in(monkeypatch):
    asked = []

    def run(argv, **kw):
        asked.append([str(a) for a in argv])
        return subprocess.CompletedProcess(argv, 0, "", "github.com\n  Logged in to github.com account ada")
    check = routine.github_check(run=run)
    assert check["ok"] and asked[0][1:] == ["auth", "status", "--hostname", "github.com"]
    out = "You are not logged into any GitHub hosts. To log in, run: gh auth login\n"
    check = routine.github_check(run=lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", out))
    assert not check["ok"] and "not logged into any GitHub hosts" in check["why"] and check["name"] == routine.GITHUB_CHECK


# ----- #11: a copy of the template is not the template ------------------------------------------------------
CHECKS = ROOT / ".github" / "workflows" / "checks.yml"


def test_github_s_own_setting_says_template_in_ci_and_the_marker_only_elsewhere(tmp_path, monkeypatch):
    copy = make_space(tmp_path / "ada", profile="template")   # a repository made from the template keeps the marker
    monkeypatch.setenv(spaceyaml.TEMPLATE_REPO_ENV, "false")
    assert not spaceyaml.is_template_repo(copy) and not gate.is_template(copy)
    monkeypatch.setenv(spaceyaml.TEMPLATE_REPO_ENV, "true")
    assert spaceyaml.is_template_repo(tmp_path)
    monkeypatch.delenv(spaceyaml.TEMPLATE_REPO_ENV)
    assert spaceyaml.is_template_repo(copy) and not spaceyaml.is_template_repo(tmp_path)


def test_the_checks_ask_github_whether_this_is_the_template_never_the_marker():
    import yaml
    text = CHECKS.read_text(encoding="utf-8")
    steps = {s.get("name"): s for job in yaml.safe_load(text)["jobs"].values() for s in job["steps"]}
    told = sorted(n for n, s in steps.items()
                  if (s.get("env") or {}).get(spaceyaml.TEMPLATE_REPO_ENV) == "${{ github.event.repository.is_template }}")
    assert told == ["Gate (protected paths and signatures)", "Gate (signatures)", "Unit tests", "What changed"]
    for name in ("The forbidden names list", "Validate as the template", "Build and run the fixture"):
        assert steps[name]["if"].startswith("github.event.repository.is_template"), name   # the fixture's own spaces: no word
    lines = [line for line in text.splitlines() if not line.strip().startswith("#")]
    assert not [line for line in lines if "template-marker" in line]   # no step decides by the file a copy inherits


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)


def test_a_protected_change_in_a_copy_that_still_has_the_marker_waits_for_the_owner(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada")
    (root / ".github").mkdir(exist_ok=True)
    (root / ".github" / "template-marker").write_text("", encoding="utf-8")   # onboarding's first commit never landed
    git(root, "init", "-q", "--initial-branch=main")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.test", "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-q", "-m", "base")
    monkeypatch.setenv(spaceyaml.TEMPLATE_REPO_ENV, "false")
    cls, reasons = gate.classify(root, ["space.yaml"], base_ref="HEAD")
    assert cls == gate.PROTECTED and not any("template repository" in r for r in reasons)
    monkeypatch.setenv(spaceyaml.TEMPLATE_REPO_ENV, "true")   # the template itself still lands on green
    assert gate.classify(root, ["space.yaml"], base_ref="HEAD")[0] == gate.AUTO


class MovingGh(FakeGh):
    """A sync pull request that gets a new head every run, red on the checks named in `failing`."""

    def __init__(self, failing):
        super().__init__("checks\tUnit tests\t2026-10-27T10:44:00Z FAILED test_onboard.py::test_x\n")
        self.failing, self.head = list(failing), 0

    def __call__(self, *args):
        if args[:2] == ("pr", "list"):
            self.head += 1
            self.calls.append(args)
            return subprocess.CompletedProcess(["gh", *args], 0, json.dumps([{"number": 7, "headRefOid": f"h{self.head}"}]), "")
        if args[:2] == ("pr", "checks"):
            self.calls.append(args)
            names = self.failing[min(self.head, len(self.failing)) - 1]
            return subprocess.CompletedProcess(["gh", *args], 1, json.dumps(
                [{"name": n, "bucket": "fail", "link": f"https://github.com/o/r/actions/runs/{100 + self.head}/job/1"}
                 for n in names]), "")
        return super().__call__(*args)


def reruns(gh) -> int:
    return len([c for c in gh.calls if c[:2] == ("run", "rerun")])


def sync_again(repo, n: int) -> None:
    write(repo / "work" / "notes.md", f"# Notes\n\nline {n}\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0


def test_a_check_that_fails_again_after_its_rerun_is_not_rerun_on_the_next_head(repo, monkeypatch):
    gh = MovingGh([["checks"]])
    monkeypatch.setattr(sync.Run, "gh", gh)
    sync_again(repo, 1)
    assert reruns(gh) == 1
    sync_again(repo, 2)   # a new head, the same check red: it fails for a reason, not by chance
    assert reruns(gh) == 1 and run_lines(repo)[-1]["notes"]["pr_red"] == {"pr": 7, "checks": ["checks"]}


def test_a_sync_pull_request_is_rerun_three_times_a_day_at_most(repo, monkeypatch):
    gh = MovingGh([["a"], ["b"], ["c"], ["d"]])   # a different check each time: each could be chance
    monkeypatch.setattr(sync.Run, "gh", gh)
    for n in range(4):
        sync_again(repo, n)
    assert reruns(gh) == sync.HEAL_RERUNS == 3 and "pr_red" in run_lines(repo)[-1]["notes"]


# ----- #13: the briefing goes to the work mailbox the app card named -------------------------------------
MAIL = {"app": "Gmail", "kinds": ["mail"], "read": ["mcp__claude_ai_Gmail__search_threads"], "write": [],
        "send": ["mcp__claude_ai_Gmail__send_message"], "account": "ada@desk.example", "verdict": "work",
        "per_month": {"2026-10": 212}, "why": "The mailbox holds the company's mail."}
DIARY = {"app": "Google Calendar", "kinds": ["calendar"], "read": ["mcp__claude_ai_Google_Calendar__list_events"],
         "write": [], "send": [], "account": "ada@desk.example", "verdict": "mixed", "per_month": {"2026-10": 40},
         "why": "Meetings and private appointments."}
NOW = dt.datetime(2026, 10, 27, 9, 0, tzinfo=dt.timezone.utc)


def test_the_app_card_names_the_work_mailbox_a_second_app_confirms_as_where_the_briefing_goes():
    assert apps.card([MAIL, DIARY], NOW)["briefing"] == "ada@desk.example"
    # one reader's word alone (a mail could have told it), a mailbox setup cannot send from, a mail provider's
    # address and one that is not the person's work mailbox name no address: the build falls back as before
    assert apps.card([MAIL], NOW)["briefing"] == ""
    assert apps.card([{**MAIL, "send": []}, DIARY], NOW)["briefing"] == ""
    assert apps.card([{**MAIL, "account": "ada@gmail.com"}, {**DIARY, "account": "ada@gmail.com"}], NOW)["briefing"] == ""
    assert apps.card([{**MAIL, "verdict": "mixed"}, DIARY], NOW)["briefing"] == ""
    assert apps.card([MAIL, {**DIARY, "verdict": "someone-else"}], NOW)["briefing"] == ""


def journal(card_named: bool) -> dict:
    verdicts = [MAIL, DIARY]
    return {"signins": {"claude_email": "ada@lanternworks.example"},
            "research": {"company": {"name": "Lantern Works", "domain": "lanternworks.example",
                                     "domains": ["lanternworks.example"], "domain_from": "address"}},
            "apps": {"verdicts": verdicts, **({"card": apps.card(verdicts, NOW)} if card_named else {})}}


def test_the_build_takes_the_mailbox_the_card_named_before_git_s_address(monkeypatch):
    monkeypatch.setattr(start, "_out", lambda argv, cwd=None: "ada.private@example.org" if "user.email" in argv else "")
    person = types.SimpleNamespace(email=None, name="Ada Okafor", domain=None, offline=True)
    assert start.briefing_address(person, journal(True)) == ("ada@desk.example", "work-mailbox")
    assert start.briefing_address(person, journal(False)) == ("ada.private@example.org", "git")   # an older card


def test_the_card_says_where_the_briefing_goes_and_how_to_change_it():
    text = (ROOT / ".claude" / "skills" / "onboard" / "messages.md").read_text(encoding="utf-8")
    assert "{card.briefing}" in text and "send my briefing to" in text
    skill = (ROOT / ".claude" / "skills" / "onboard" / "SKILL.md").read_text(encoding="utf-8").lower()
    assert "send my briefing to" in skill and "--email" in skill


def test_the_morning_view_says_the_send_tool_is_the_mailbox_check():
    flat = " ".join((ROOT / ".claude" / "skills" / "morning-view" / "SKILL.md").read_text(encoding="utf-8").split()).lower()
    assert "only for the mailbox whose account is the briefing address" in flat
    assert "the address claude itself is signed in with is not part of that check" in flat

