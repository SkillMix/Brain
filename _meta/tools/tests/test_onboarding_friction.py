"""What a colleague's first Windows onboarding ran into: a proxy that re-signs TLS, a gh installed
after the app started, routines as OS tasks on a machine with no `claude` command, and a routine
that fails every run without anyone seeing it."""
import subprocess
import sys
from pathlib import Path

import doctor
import onboard
import routine
import start
from conftest import make_space


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
