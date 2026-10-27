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


# ----- #3 and #8: apps still connecting when they are looked at ----------------------------------------
def init_with(*statuses) -> str:
    servers = [{"name": f"claude.ai App{i}", "status": s} for i, s in enumerate(statuses)]
    tools = [f"mcp__claude_ai_App{i}__list" for i, s in enumerate(statuses) if s == "connected"]
    return json.dumps({"type": "system", "subtype": "init", "cwd": "x", "model": "claude-haiku-5-5",
                       "apiKeySource": "none", "tools": tools, "mcp_servers": servers}) + "\n"


def answering(monkeypatch, *outputs):
    """A claude that answers each probe with the next init (an exception is raised instead), on a clock
    that only the waits move."""
    asked, clock = [], [0.0]
    monkeypatch.setattr(routine, "_sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(routine, "_clock", lambda: clock[0])
    outputs = list(outputs)

    def run(argv, **kw):
        asked.append(argv)
        said = outputs.pop(0)
        if isinstance(said, Exception):
            raise said
        return subprocess.CompletedProcess(argv, 0, said, "")
    return run, asked, clock


def test_the_probe_looks_again_while_an_app_is_still_connecting(tmp_path, monkeypatch):
    run, asked, clock = answering(monkeypatch, init_with("connected", "pending"), init_with("connected", "connected"))
    seen = routine.probe_tools("claude", tmp_path, run=run)
    assert len(asked) == 2 and seen["tools"] == ["mcp__claude_ai_App0__list", "mcp__claude_ai_App1__list"]
    assert 0 < clock[0] <= routine.PENDING_BUDGET


def test_the_probe_keeps_the_look_with_the_fewest_apps_still_connecting(tmp_path, monkeypatch):
    run, asked, _ = answering(monkeypatch, init_with("pending", "pending"), init_with("connected", "pending"),
                              init_with("pending", "pending"))
    seen = routine.probe_tools("claude", tmp_path, run=run)
    assert len(asked) == routine.PENDING_LOOKS == 3 and seen["tools"] == ["mcp__claude_ai_App0__list"]
    assert [s["status"] for s in seen["mcp_servers"]] == ["connected", "pending"]


def test_a_probe_with_nothing_connecting_asks_once_and_a_failed_second_look_keeps_the_first(tmp_path, monkeypatch):
    run, asked, _ = answering(monkeypatch, init_with("connected", "needs-auth"))
    assert routine.probe_tools("claude", tmp_path, run=run)["tools"] == ["mcp__claude_ai_App0__list"] and len(asked) == 1
    run, asked, _ = answering(monkeypatch, init_with("connected", "pending"), subprocess.TimeoutExpired("claude", 1))
    assert routine.probe_tools("claude", tmp_path, run=run)["tools"] == ["mcp__claude_ai_App0__list"]


# ----- #12 and #14: the first push and the brain mount say when they did not happen ------------------------
def test_git_signs_in_to_github_through_gh_in_what_the_tools_start(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("gh", "gh.exe"):
        (bin_dir / name).write_text("", encoding="utf-8")
        os.chmod(bin_dir / name, 0o755)
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""), "GIT_CONFIG_COUNT": "1",
           "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "Ada Okafor"}
    env.pop("GH_CONFIG_DIR", None)
    doctor.use_tools(tmp_path / "home", env=env, windows=False)
    doctor.use_tools(tmp_path / "home", env=env, windows=False)   # once is enough
    helpers = subprocess.run(["git", "config", "--get-all", "credential.https://github.com.helper"], env=env,
                             capture_output=True, text=True).stdout.splitlines()
    assert helpers[-1].endswith("auth git-credential") and helpers.count(helpers[-1]) == 1   # after any others
    assert env["GIT_CONFIG_COUNT"] == "2" and env["GIT_CONFIG_VALUE_0"] == "Ada Okafor"
    assert env["GCM_INTERACTIVE"] == "never"   # a credential manager with nothing stored opens no window
    assert all(value for key, value in env.items() if key.startswith("GIT_CONFIG_"))   # Windows drops an empty one


def outside(answers: dict):
    """onboard's run_cmd, answering a command that starts with a key with (code, stdout, stderr)."""
    calls = []

    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        calls.append(argv)
        for prefix, (code, out, err) in answers.items():
            if " ".join(argv).startswith(prefix):
                return subprocess.CompletedProcess(argv, code, out, err)
        return subprocess.CompletedProcess(argv, 0, "", "")
    return fake, calls


def test_a_first_push_that_fails_is_a_failed_step_with_git_s_own_words(tmp_path, monkeypatch):
    fake, _ = outside({"git show origin/main:space.yaml": (0, "profile: template\n", ""),
                       "git push": (128, "", "remote: Permission to ada/ada-workspace.git denied to ada.\n"
                                             "fatal: unable to access 'https://github.com/ada/ada-workspace.git/'")})
    monkeypatch.setattr(onboard, "run_cmd", fake)
    report = onboard.Report("person")
    onboard.commit_fill(report, tmp_path / "ada-workspace", True, False)
    step = report.steps[-1]
    assert not step.done and "Permission to ada/ada-workspace.git denied" in step.result
    assert "main" in step.result and "chore: onboard" not in step.result


def test_a_push_that_leaves_main_unconfigured_is_not_done(tmp_path, monkeypatch):
    fake, calls = outside({"git show origin/main:space.yaml": (0, "profile: template\n", "")})
    monkeypatch.setattr(onboard, "run_cmd", fake)
    report = onboard.Report("person")
    onboard.commit_fill(report, tmp_path / "ada-workspace", True, False)
    assert ["git", "push", "-q", "origin", "HEAD:main"] in calls and not report.steps[-1].done
    fake, _ = outside({"git show origin/main:space.yaml": (0, "profile: template\n", ""),
                       "git push": (0, "", "")})
    shown = iter([(0, "profile: template\n"), (0, "profile: personal\n")])   # before the push, and after it

    def after(argv, dry_run=False, cwd=None):
        if argv[:2] == ["git", "show"]:
            code, out = next(shown)
            return subprocess.CompletedProcess(argv, code, out, "")
        return fake(argv, dry_run, cwd)
    monkeypatch.setattr(onboard, "run_cmd", after)
    report = onboard.Report("person")
    onboard.commit_fill(report, tmp_path / "ada-workspace", True, False)
    assert report.steps[-1].done and report.steps[-1].result == "bootstrap push to main"


def test_a_brain_that_did_not_mount_is_a_failed_step(tmp_path, monkeypatch):
    fake, _ = outside({"git submodule add": (128, "", "fatal: could not read Username for 'https://github.com'")})
    monkeypatch.setattr(onboard, "run_cmd", fake)
    report = onboard.Report("person")
    onboard.add_mount(report, tmp_path / "ada-workspace", "https://github.com/lantern/lantern-brain.git", "brain", False)
    step = report.steps[-1]
    assert not step.done and "could not read Username" in step.result and "brain" in step.name
    monkeypatch.setattr(onboard, "run_cmd", outside({})[0])
    (tmp_path / "ada-workspace" / "brain").mkdir(parents=True)
    (tmp_path / "ada-workspace" / "brain" / "space.yaml").write_text("profile: brain\n", encoding="utf-8")
    report = onboard.Report("person")
    onboard.add_mount(report, tmp_path / "ada-workspace", "https://github.com/lantern/lantern-brain.git", "brain", False)
    assert report.steps[-1].done


def test_while_main_is_still_the_template_s_the_sync_sends_the_whole_fill_as_one_protected_pull_request(repo, monkeypatch):
    from test_sync import git as sync_git
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    filled = (repo / "space.yaml").read_text(encoding="utf-8")
    write(repo / "space.yaml", filled.replace("profile: personal", "profile: template"))
    sync_git(repo, "commit", "-q", "-am", "Initial commit")   # what "use this template" put on main
    sync_git(repo, "push", "-q", "origin", "main")
    write(repo / "space.yaml", filled, minutes_old=20)         # onboarding's fill, never pushed
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    host = sync.hostname()
    sync_git(repo, "fetch", "-q", "origin")
    assert sync_git(repo, "rev-parse", "-q", "--verify", f"origin/sync/{host}", check=False).returncode != 0
    guarded = sync_git(repo, "diff", "--name-only", "origin/main", f"origin/sync/{host}-protected").stdout.split()
    assert sorted(guarded) == ["space.yaml", "work/notes.md"]


# ----- the company's site: a site that moved to another host is read there -----------------------------------
from test_research import Readers, journal as research_journal, phase, records_on_disk, the_apps, the_home  # noqa: E402,F401
from test_research import run as research_run  # noqa: E402

MOVED = {"records": [], "moved_to": "https://lanternworks.test/"}   # what WebFetch says of a redirect to another host
THERE = {"name": "Lantern Works", "records": [
    {"title": "What we do", "url": "https://lanternworks.test/", "about": "identity", "text": "Wayfinding for ports."}]}


def test_a_site_that_moved_to_another_host_is_read_on_that_host(phase, capsys, tmp_path):
    the_home(tmp_path)
    the_apps()
    readers = Readers(phase, {"site": lambda prompt: THERE if "https://lanternworks.test/" in prompt else MOVED})
    code, _ = research_run(capsys)
    first, second = readers.asked("site")
    assert first["tools"] == ["WebFetch(domain:lanternworks.example)", "WebFetch(domain:www.lanternworks.example)"]
    assert "moved_to" in first["schema"]["properties"] and "moved_to" in first["prompt"]
    assert second["tools"] == ["WebFetch(domain:lanternworks.test)", "WebFetch(domain:www.lanternworks.test)"]
    assert code == 0 and "site" in research_journal()["sources_done"]
    assert [r["url"] for r in records_on_disk() if r["id"].startswith("site")] == ["https://lanternworks.test/"]
    assert research_journal()["company"]["domain"] == "lanternworks.example"   # the register is still the .example one


def test_a_site_that_says_it_moved_to_itself_or_to_no_host_is_read_once(phase, capsys, tmp_path):
    the_home(tmp_path)
    the_apps()
    for moved in ("https://www.lanternworks.example/", "not a host", "file:///etc/passwd", ""):
        readers = Readers(phase, {"site": {"records": [], "moved_to": moved}})
        research_run(capsys)
        assert len(readers.asked("site")) == 1, moved

