"""doctor: every prerequisite of onboarding, checked first; fixed without a password where an
official, pinned, checksummed install exists; the rest is one sentence for the person."""
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import doctor

TEMPLATE = Path(__file__).resolve().parents[3]

ALL_GOOD = {
    "git --version": (0, "git version 2.56.0"),
    "gh --version": (0, "gh version 2.101.0"),
    "gh auth status": (0, "Logged in to github.com"),
    "git config user.name": (0, "Ada Okafor"),
    "git config user.email": (0, "ada@lantern.example"),
    "git config --get-regexp credential": (0, "credential.helper manager"),
    "git config core.longpaths": (0, "true"),
    "gh api users/lantern-org": (0, "Organization"),
    "gh api user/memberships/orgs/lantern-org": (0, "active"),
    "gh api repos/lantern-org/brain": (0, "lantern-org/brain"),
    "gh api rate_limit": (0, "4999"),
    "crontab -l": (0, ""),
    "schtasks /Query": (0, ""),
}


class Fake:
    """A machine: `answers` maps the start of a command to (code, stdout); later entries win.
    Records every call. `installed` names tools that answer once their bin dir is on PATH."""

    def __init__(self, tmp_path, answers=None, system="Windows", arch="AMD64", reach=True, root=False,
                 archives=None, free=50 << 30):
        self.answers = dict(ALL_GOOD, **(answers or {}))
        self.calls = []
        self.archives = archives or {}
        self.machine = doctor.Machine(run=self.run, system=system, arch=arch, home=tmp_path / "home",
                                      download=self.download, reach=lambda: reach, root=root,
                                      free=lambda path: free)

    def run(self, argv, cwd=None, input=None):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        line = " ".join(argv)
        tools = self.machine.home / ".spaces" / "tools"
        if argv[0] in ("gh", "git") and argv[1] == "--version":
            on_path = any(Path(p).is_relative_to(tools / argv[0]) for p in os.environ["PATH"].split(os.pathsep) if p)
            if on_path:
                return subprocess.CompletedProcess(argv, 0, f"{argv[0]} version installed", "")
        found = [v for k, v in self.answers.items() if line.startswith(k)]
        code, out = found[-1] if found else (0, "")
        return subprocess.CompletedProcess(argv, code, out, "" if code == 0 else "failed")

    def download(self, url, dest):
        Path(dest).write_bytes(self.archives[url])

    def wrote(self):
        """Commands that change something: installs, settings, membership."""
        return [c for c in self.calls if c[:3] == ["git", "config", "--global"] and len(c) > 4
                and not c[3].startswith("--get") or c[:3] == ["gh", "auth", "setup-git"] or "--method" in c
                or "install" in c]


def rows(fake, **kw):
    return {r.name: r for r in doctor.diagnose(fake.machine, **kw)}


def zip_with(name: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, "binary")
        z.writestr("LICENSE", "license")
    return buf.getvalue()


def test_a_ready_machine_is_all_ok_and_nothing_changes(tmp_path):
    fake = Fake(tmp_path)
    found = rows(fake, orgs=["lantern-org"], repos=["lantern-org/brain"])
    assert all(r.ok for r in found.values()), [r for r in found.values() if not r.ok]
    assert {"python", "disk", "network", "git", "gh", "github sign-in", "git identity", "git credentials",
            "long paths", "scheduler", "organization lantern-org", "access lantern-org/brain"} <= set(found)
    assert fake.wrote() == []


def test_a_missing_gh_is_installed_from_its_pinned_release_once_the_checksum_matches(tmp_path, monkeypatch):
    archive = zip_with("bin/gh.exe")
    url = "https://github.com/cli/cli/releases/download/v2.101.0/gh_2.101.0_windows_amd64.zip"
    monkeypatch.setitem(doctor.ASSETS["gh"], ("Windows", "x86_64"), (url, hashlib.sha256(archive).hexdigest()))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    fake = Fake(tmp_path, {"gh --version": (127, "")}, archives={url: archive})
    found = rows(fake)
    assert found["gh"].ok and "2.101.0" in found["gh"].fixed
    assert (tmp_path / "home" / ".spaces" / "tools" / "gh" / "bin" / "gh.exe").is_file()
    assert os.environ["PATH"].split(os.pathsep)[0].endswith(os.path.join("gh", "bin"))


def test_a_download_whose_checksum_differs_installs_nothing(tmp_path, monkeypatch):
    url = "https://github.com/cli/cli/releases/download/v2.101.0/gh_2.101.0_windows_amd64.zip"
    monkeypatch.setitem(doctor.ASSETS["gh"], ("Windows", "x86_64"), (url, "0" * 64))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    fake = Fake(tmp_path, {"gh --version": (127, "")}, archives={url: zip_with("bin/gh.exe")})
    found = rows(fake)
    assert not found["gh"].ok and "checksum" in found["gh"].yours
    assert not (tmp_path / "home" / ".spaces" / "tools" / "gh").exists()


def test_the_github_sign_in_is_the_persons_one_command(tmp_path):
    fake = Fake(tmp_path, {"gh auth status": (1, "")})
    found = rows(fake, orgs=["lantern-org"], repos=["lantern-org/brain"])
    sign_in = found["github sign-in"]
    assert not sign_in.ok and "gh auth login --web" in sign_in.yours and sign_in.check == "gh auth status"
    # what needs the sign-in waits for it instead of failing on its own
    assert not {"git identity", "organization lantern-org", "access lantern-org/brain"} & set(found)


def test_git_identity_and_credentials_come_from_the_host_account(tmp_path):
    user = json.dumps({"login": "ada-lw", "id": 7, "name": "Ada Okafor", "email": None})
    fake = Fake(tmp_path, {"git config user.name": (1, ""), "git config user.email": (1, ""),
                           "git config --get-regexp credential": (1, ""), "gh api user": (0, user)})
    found = rows(fake)
    assert found["git identity"].ok and found["git credentials"].ok
    assert ["git", "config", "--global", "user.name", "Ada Okafor"] in fake.calls
    assert ["git", "config", "--global", "user.email", "7+ada-lw@users.noreply.github.com"] in fake.calls
    assert ["gh", "auth", "setup-git"] in fake.calls


def test_a_pending_organization_invitation_is_accepted_and_missing_access_names_who_grants_it(tmp_path):
    fake = Fake(tmp_path, {"gh api user/memberships/orgs/lantern-org": (0, "pending"),
                           "gh api repos/lantern-org/brain": (1, "")})
    found = rows(fake, orgs=["lantern-org"], repos=["lantern-org/brain"])
    assert found["organization lantern-org"].ok
    assert ["gh", "api", "--method", "PATCH", "user/memberships/orgs/lantern-org", "-f", "state=active"] in fake.calls
    access = found["access lantern-org/brain"]
    assert not access.ok and "read access" in access.yours and "lantern-org/brain" in access.check


def test_no_organization_membership_is_asked_of_a_personal_account(tmp_path):
    fake = Fake(tmp_path, {"gh api users/ada-lw": (0, "User")})
    assert "organization ada-lw" not in rows(fake, orgs=["ada-lw"])


def test_git_on_macos_without_homebrew_asks_for_the_command_line_tools(tmp_path):
    fake = Fake(tmp_path, {"git --version": (1, "xcrun: error"), "brew --version": (127, "")},
                system="Darwin", arch="arm64")
    git = rows(fake)["git"]
    assert not git.ok and "xcode-select --install" in git.yours and git.check == "git --version"


def test_git_on_linux_installs_with_apt_when_sudo_needs_no_password(tmp_path):
    fake = Fake(tmp_path, {"git --version": (127, ""), "sudo -n true": (0, ""), "apt-get --version": (0, "apt")},
                system="Linux", arch="x86_64")
    rows(fake)
    assert ["sudo", "-n", "apt-get", "install", "-y", "git"] in fake.calls


def test_git_on_linux_asks_for_the_password_once(tmp_path):
    fake = Fake(tmp_path, {"git --version": (127, ""), "sudo -n true": (1, ""), "apt-get --version": (0, "apt")},
                system="Linux", arch="x86_64")
    git = rows(fake)["git"]
    assert not git.ok and "! sudo apt install git" in git.yours
    assert not any(c[:2] == ["sudo", "apt-get"] or c[:3] == ["sudo", "-n", "apt-get"] for c in fake.calls)


def test_windows_long_paths_are_switched_on(tmp_path):
    fake = Fake(tmp_path, {"git config core.longpaths": (1, "")})
    assert rows(fake)["long paths"].ok
    assert ["git", "config", "--global", "core.longpaths", "true"] in fake.calls


def test_no_network_stops_before_the_host_is_asked_anything(tmp_path):
    fake = Fake(tmp_path, reach=False)
    found = rows(fake, orgs=["lantern-org"])
    assert not found["network"].ok and "reconnect" in found["network"].yours
    assert not any(c[0] == "gh" and c[1] != "--version" for c in fake.calls)


def test_offline_looks_only_at_this_machine(tmp_path):
    fake = Fake(tmp_path)
    assert set(rows(fake, offline=True)) == {"python", "disk"} and fake.calls == []


def test_a_full_disk_is_named(tmp_path):
    fake = Fake(tmp_path, free=200 << 20)
    disk = rows(fake)["disk"]
    assert not disk.ok and "GB" in disk.yours


def test_a_spaces_folder_under_documents_on_macos_is_moved_to_the_default(tmp_path):
    fake = Fake(tmp_path, system="Darwin", arch="arm64")
    found = rows(fake, spaces=tmp_path / "home" / "Documents" / "spaces")
    assert not found["spaces folder"].ok and "~/spaces" in found["spaces folder"].yours
    assert "spaces folder" not in rows(Fake(tmp_path, system="Darwin", arch="arm64"), spaces=tmp_path / "home" / "spaces")


def test_read_only_mode_reports_and_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    fake = Fake(tmp_path, {"gh --version": (127, ""), "git config core.longpaths": (1, "")})
    found = rows(fake, fix=False)
    assert not found["gh"].ok and not found["long paths"].ok and fake.wrote() == []


def test_installed_tools_come_first_on_path(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".spaces" / "tools" / "gh" / "bin").mkdir(parents=True)
    (home / ".spaces" / "tools" / "gh" / "bin" / "gh.exe").write_text("x")
    monkeypatch.setenv("PATH", "elsewhere")
    added = doctor.use_tools(home)
    assert os.environ["PATH"].split(os.pathsep)[0] == str(home / ".spaces" / "tools" / "gh" / "bin")
    gh_bin = str(home / ".spaces" / "tools" / "gh" / "bin")
    assert doctor.use_tools(home) == [] and os.environ["PATH"].split(os.pathsep).count(gh_bin) == 1 and added


def test_every_platform_has_a_pinned_checksummed_asset():
    for tool, systems in (("gh", ("Windows", "Darwin", "Linux")), ("uv", ("Windows", "Darwin", "Linux")),
                          ("git", ("Windows",))):
        for system in systems:
            for arch in ("x86_64", "arm64"):
                url, sha = doctor.ASSETS[tool][(system, arch)]
                assert url.startswith("https://github.com/") and len(sha) == 64 and doctor.VERSIONS[tool] in url


def test_the_bootstrap_scripts_pin_the_same_uv_as_the_doctor():
    sh = (TEMPLATE / "_meta" / "tools" / "bootstrap.sh").read_text(encoding="utf-8")
    ps1 = (TEMPLATE / "_meta" / "tools" / "bootstrap.ps1").read_text(encoding="utf-8")
    for (system, arch), (url, sha) in doctor.ASSETS["uv"].items():
        script = ps1 if system == "Windows" else sh
        assert url in script and sha in script, (system, arch)


@pytest.mark.skipif(os.name == "nt", reason="the POSIX bootstrap")
def test_the_posix_bootstrap_runs_start_with_a_new_enough_python(tmp_path):
    """With a Python 3.10+ on PATH the bootstrap goes straight to start.py with every argument."""
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "python3").write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n", encoding="utf-8")
    (stub / "python3").chmod(0o755)
    start = tmp_path / "start.py"
    start.write_text("import sys\nprint('start', sys.argv[1:])\n", encoding="utf-8")
    env = {"PATH": f"{stub}:/usr/bin:/bin", "HOME": str(tmp_path), "BOOTSTRAP_START": str(start)}
    done = subprocess.run(["sh", str(TEMPLATE / "_meta" / "tools" / "bootstrap.sh"), "--offline", "--x", "a b"],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    assert "start ['--offline', '--x', 'a b']" in done.stdout


# ----- where the doctor runs: start, onboard --check, the sync task -------------------------
def _start_world(tmp_path, monkeypatch, rows):
    import onboard
    import start
    from test_start import brain_with_invitation, template_copy
    seen = {}

    def diagnose(**kw):
        seen.update(kw)
        return rows
    monkeypatch.setattr(start.doctor, "diagnose", diagnose)
    monkeypatch.setattr(onboard, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    monkeypatch.setattr(start, "_out", lambda *a, **k: "")
    monkeypatch.setattr(start, "LOCK", tmp_path / "start.lock")
    return start, template_copy(tmp_path), brain_with_invitation(tmp_path), seen


def test_start_runs_the_doctor_first_and_stops_at_what_is_the_persons(tmp_path, monkeypatch):
    start, tpl, brain, seen = _start_world(tmp_path, monkeypatch, [
        doctor.Row("github sign-in", False, "not signed in", "gh auth status", yours="sign in: `! gh auth login --web`")])
    report = start.start(tpl, invitation="lantern-org/brain:ada", template="lantern-org/brain-template",
                         home=tmp_path / "home", user_instructions=tmp_path / "CLAUDE.md")
    row = next(s for s in report.steps if s.name == "doctor: github sign-in")
    assert row.needs_owner and not row.done and "gh auth login" in row.result
    assert not any(s.name.startswith("create") for s in report.steps)
    assert seen["orgs"] == ["lantern-org"] and set(seen["repos"]) == {"lantern-org/brain", "lantern-org/brain-template"}
    assert seen["fix"] is True and not (tmp_path / "start.lock").exists()


def test_start_doctor_only_checks_and_fixes_then_stops(tmp_path, monkeypatch, capsys):
    start, tpl, brain, seen = _start_world(tmp_path, monkeypatch, [doctor.Row("gh", True, "gh 2.101.0", "gh --version")])
    monkeypatch.setattr(start, "HERE", tpl)
    code = start.main(["--doctor", "--invitation", "lantern-org/brain:ada", "--template", "lantern-org/brain-template"])
    assert code == 0 and "doctor: gh" in capsys.readouterr().out and seen["fix"] is True


def test_a_second_sentence_waits_for_the_first(tmp_path, monkeypatch):
    start, tpl, brain, seen = _start_world(tmp_path, monkeypatch, [])
    (tmp_path / "start.lock").write_text("123\n")
    report = start.start(tpl, invitation=f"{brain.as_posix()}:ada", offline=True, home=tmp_path / "home",
                         user_instructions=tmp_path / "CLAUDE.md")
    assert [s.name for s in report.failed()] == ["one start at a time"] and not seen
    assert (tmp_path / "start.lock").exists()   # the running start keeps its lock
    old = (tmp_path / "start.lock").stat().st_mtime - 2 * 3600
    os.utime(tmp_path / "start.lock", (old, old))   # a crashed run's lock is taken over
    report = start.start(tpl, invitation=f"{brain.as_posix()}:ada", offline=True, home=tmp_path / "home",
                         user_instructions=tmp_path / "CLAUDE.md")
    assert "one start at a time" not in [s.name for s in report.steps] and not report.failed()


def test_without_the_venv_module_uv_makes_the_venv(tmp_path):
    import start
    calls = []

    def run(argv, **kw):
        calls.append([str(a) for a in argv])
        refused = "--user" in argv or argv[1:3] == ["-m", "venv"]
        return subprocess.CompletedProcess(argv, 1 if refused else 0, "", "")
    uv_dir = tmp_path / "tools" / "uv"
    venv = tmp_path / "venv"
    code = start.ensure_yaml(["--offline"], venv=venv, run=run, have=lambda: False,
                             install=lambda tool: (uv_dir, f"installed {tool}"))
    uv = str(uv_dir / ("uv.exe" if os.name == "nt" else "uv"))
    py = str(start.venv_python(venv))
    assert code == 0
    assert [uv, "venv", "--python", sys.executable, str(venv)] in calls
    assert [uv, "pip", "install", "--python", py, "--quiet", "pyyaml"] in calls
    assert calls[-1] == [py, str(Path(start.__file__).resolve()), "--offline"]


def test_onboard_check_includes_the_doctor_and_changes_nothing(tmp_path, monkeypatch):
    import onboard
    from conftest import make_space
    root = make_space(tmp_path / "ada")
    onboard.write_yaml(root / "_meta" / "state" / "onboard.yaml", {"case": "person", "offline": False,
                                                                   "repo": "lantern-org/ada-workspace"})
    seen = {}

    def diagnose(m=None, **kw):
        seen.update(kw)
        return [doctor.Row("gh", False, "not found", "gh --version", yours="install gh")]
    monkeypatch.setattr(onboard.doctor, "diagnose", diagnose)
    monkeypatch.setattr(onboard, "run_cmd", lambda *a, **k: subprocess.CompletedProcess(a, 0, "true", ""))
    row = next(s for s in onboard.check(root) if s.name == "doctor: gh")
    assert row.needs_owner and not row.done and seen["fix"] is False and seen["repos"] == ["lantern-org/ada-workspace"]


def test_the_sync_task_finds_the_installed_tools(tmp_path, monkeypatch):
    import sync
    called = []
    monkeypatch.setattr(sync.doctor, "use_tools", lambda home=None: called.append(True) or [])
    sync.main(["--root", str(tmp_path)])
    assert called


def _tls_refused(*args, **kwargs):
    import ssl
    import urllib.error
    raise urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))


def test_a_certificate_problem_is_not_a_missing_network(monkeypatch):
    monkeypatch.setattr(doctor.urllib.request, "urlopen", _tls_refused)
    assert doctor.reachable() is True


def test_a_download_python_cannot_verify_falls_back_to_curl(tmp_path, monkeypatch):
    calls = []

    def run(argv, cwd=None, input=None):
        calls.append([str(a) for a in argv])
        Path(argv[argv.index("-o") + 1]).write_bytes(b"archive")
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(doctor.urllib.request, "urlopen", _tls_refused)
    monkeypatch.setattr(doctor, "run_cmd", run)
    doctor.download("https://github.com/cli/cli/releases/download/x.zip", tmp_path / "x.zip")
    assert calls[0][0] == "curl" and (tmp_path / "x.zip").read_bytes() == b"archive"


def test_a_new_company_is_not_asked_to_join_the_templates_organization(tmp_path, monkeypatch):
    start, tpl, brain, seen = _start_world(tmp_path, monkeypatch, [doctor.Row("gh", False, "x", "gh --version",
                                                                              yours="install gh")])
    start.start(tpl, company="Lantern Works", template="elsewhere/brain-template", home=tmp_path / "home",
                user_instructions=tmp_path / "CLAUDE.md")
    assert seen["orgs"] == [] and seen["repos"] == ["elsewhere/brain-template"]


def test_a_start_the_doctor_stopped_exits_non_zero(tmp_path, monkeypatch, capsys):
    start, tpl, brain, seen = _start_world(tmp_path, monkeypatch, [
        doctor.Row("github sign-in", False, "not signed in", "gh auth status", yours="sign in")])
    monkeypatch.setattr(start, "HERE", tpl)
    code = start.main(["--invitation", "lantern-org/brain:ada", "--template", "lantern-org/brain-template",
                       "--home", str(tmp_path / "home"), "--json"])
    out = capsys.readouterr().out
    assert code == 1 and json.loads(out[out.index("{"):])["stopped"] is True
