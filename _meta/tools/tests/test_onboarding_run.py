"""What the rc1 setup run on a second Windows account ran into, one issue at a time: the desktop app
is a packaged Windows app, so what setup writes under %APPDATA% lands in a private copy that the
scheduled tasks never see (#7); a copy of the template takes itself for the template (#11); the
briefing address (#13); apps still loading when they are looked at (#3, #8); a first push that failed
without a word (#12, #14); and the smaller ones (#5, #6, #9, #15, the company site)."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import doctor
import onboard
import routine
import start
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

