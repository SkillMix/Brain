"""doctor: what onboarding needs on this machine, checked before anything is created.

What can be fixed without a password is fixed: the GitHub CLI (and on Windows a minimal Git)
from their official releases, pinned and checked against their SHA-256 before anything is
unpacked, into `~/.spaces/tools/` (no admin rights); git's identity and its credential helper
from the person's GitHub account; long paths on Windows; a pending organization invitation
(the person's sentence asked to join). Homebrew or apt are used only when already there and
no password is needed. Everything else is one plain sentence for the person with the exact
command (`! <command>` in Claude Code) and the check that proves it: the GitHub sign-in in the
browser, a password, a dialog, an access grant only an owner can give. The agent never types
a password.

Standard library only: start.py runs it before PyYAML exists.

    python doctor.py [--org ORG ...] [--repo OWNER/NAME ...] [--spaces DIR] [--check] [--json]

`--check` reports and changes nothing. Exit 0 when every row is ok.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

VERSIONS = {"gh": "2.101.0", "uv": "0.12.18", "git": "2.56.0"}
_GH = "https://github.com/cli/cli/releases/download/v2.101.0/gh_2.101.0_"
_UV = "https://github.com/astral-sh/uv/releases/download/0.12.18/uv-"
_GIT = "https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.1/MinGit-2.56.0-"
# (system, arch) -> (url, sha256), from each release's published asset digests.
ASSETS = {
    "gh": {
        ("Linux", "x86_64"): (_GH + "linux_amd64.tar.gz", "9bca2d1c16825f109907a23307628a2f0698fbf99662b73a5cf0b020293072b8"),
        ("Linux", "arm64"): (_GH + "linux_arm64.tar.gz", "b57e8063f18862647c9d22727c32e9da1b963f8bf9db648fe123a6975695640f"),
        ("Darwin", "x86_64"): (_GH + "macOS_amd64.zip", "a6fd66c88e2f07d6e4e058173db341d07dd74d58cf8f19ae668293d2bb614ca3"),
        ("Darwin", "arm64"): (_GH + "macOS_arm64.zip", "e4303e39d8f07141c4bad4b99b01079f05029c59b27076e8fbc825c985ecdd8b"),
        ("Windows", "x86_64"): (_GH + "windows_amd64.zip", "bc6c814367b193cd8e713611d61e36013c0ef843b8f516458fe3eda039192794"),
        ("Windows", "arm64"): (_GH + "windows_arm64.zip", "e6cbb2d4afdad3e70f3d38b8d1ebaa3a0870a897cfc0e4cf569826710b96b4fd"),
    },
    "uv": {
        ("Linux", "x86_64"): (_UV + "x86_64-unknown-linux-gnu.tar.gz", "89eadd7c76fc063887959510d5ba0ab1264dfd5f1143b925ddb73021a40acf16"),
        ("Linux", "arm64"): (_UV + "aarch64-unknown-linux-gnu.tar.gz", "afb6291f3f0a6b4521fc67b947822506c41dde5b60d2189dd8f3695b2ac8c9e7"),
        ("Darwin", "x86_64"): (_UV + "x86_64-apple-darwin.tar.gz", "2e4108f5395397c8bc5d43bf83d3bdbb2d0e92b90d0efa607756be704905fa33"),
        ("Darwin", "arm64"): (_UV + "aarch64-apple-darwin.tar.gz", "cf40e0c6a202190ccd9e0406dcfdd5b2d6668a9a5c779b17948963df32aafe5b"),
        ("Windows", "x86_64"): (_UV + "x86_64-pc-windows-msvc.zip", "cae6a3bc25239f83dffb467a4b180508d9da23986c04639ebfa44e43e6a84bff"),
        ("Windows", "arm64"): (_UV + "aarch64-pc-windows-msvc.zip", "17f27b1c64eacc757ae603579f116a014881e486c5e79ae81877980d4699e943"),
    },
    "git": {
        ("Windows", "x86_64"): (_GIT + "64-bit.zip", "064b440ff870ed5198527e8f3a92cdf5bd2fd0fedf5e718af95e3fdaddeff718"),
        ("Windows", "arm64"): (_GIT + "arm64.zip", "cb3b0f2d486ea52673227151a5baf5bc13861ff80e74e94e46d614d1bfcd5c06"),
    },
}
ARCH = {"amd64": "x86_64", "x86_64": "x86_64", "x64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}
MIN_FREE = 1 << 30
PROTECTED = ("Documents", "Desktop", "Downloads", "Library/Mobile Documents")


def run_cmd(argv, cwd=None, input=None) -> subprocess.CompletedProcess:
    argv = [str(a) for a in argv]
    try:
        return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              check=False, cwd=cwd, input=input, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def _tls(exc) -> bool:
    return isinstance(getattr(exc, "reason", None), ssl.SSLError) or isinstance(exc, ssl.SSLError)


def download(url: str, dest) -> None:
    """Python's own download; where this Python cannot verify certificates (a python.org build
    on macOS without its certificates), the system's curl, which uses the system's store."""
    try:
        with urllib.request.urlopen(url, timeout=120) as res, open(dest, "wb") as fh:
            shutil.copyfileobj(res, fh)
    except (urllib.error.URLError, ssl.SSLError) as exc:
        if not _tls(exc):
            raise
        done = run_cmd(["curl", "-fsSL", "--retry", "3", "-o", str(dest), url])
        if done.returncode != 0:
            raise OSError(f"curl: {(done.stderr or '').strip()[:200]}") from exc


def reachable(url: str = "https://api.github.com/zen") -> bool:
    try:
        urllib.request.urlopen(url, timeout=15).close()
        return True
    except urllib.error.HTTPError:
        return True   # an answer, even a refusal, means the network works
    except (urllib.error.URLError, ssl.SSLError, OSError) as exc:
        return _tls(exc)   # a certificate this Python cannot verify still came over the network


def free_bytes(path) -> int:
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    return shutil.disk_usage(path).free


@dataclass
class Machine:
    """Everything the doctor touches, so a test can stand in for any machine."""
    run: Callable = run_cmd
    system: str = field(default_factory=platform.system)
    arch: str = field(default_factory=platform.machine)
    home: Path = field(default_factory=Path.home)
    download: Callable = download
    reach: Callable = reachable
    root: bool = field(default_factory=lambda: hasattr(os, "geteuid") and os.geteuid() == 0)
    free: Callable = free_bytes

    def ok(self, *argv) -> bool:
        return self.run(list(argv)).returncode == 0

    def out(self, *argv) -> str:
        res = self.run(list(argv))
        return (res.stdout or "").strip() if res.returncode == 0 else ""


@dataclass
class Row:
    name: str
    ok: bool
    found: str
    check: str
    yours: str = ""    # the person's one sentence, with the command, when the agent cannot fix it
    fixed: str = ""    # what the agent did


# ----- tools under ~/.spaces/tools ----------------------------------------------------------
def tools_home(home=None) -> Path:
    return Path(home or Path.home()) / ".spaces" / "tools"


def _exe(name: str, system: str) -> str:
    return name + (".exe" if system == "Windows" else "")


def bin_dirs(home=None) -> list:
    """The installed tools' directories that hold their programs."""
    base = tools_home(home)
    found = []
    for tool, rel in (("gh", "bin"), ("git", "cmd"), ("uv", "")):
        folder = base / tool / rel if rel else base / tool
        if folder.is_dir() and any((folder / n).exists() for n in (tool, tool + ".exe")):
            found.append(folder)
    return found


def use_tools(home=None) -> list:
    """Put the installed tools first on PATH for this process and what it starts (the sync
    task, git, gh). Returns the directories added."""
    current = os.environ.get("PATH", "").split(os.pathsep)
    added = [str(d) for d in bin_dirs(home) if str(d) not in current]
    if added:
        os.environ["PATH"] = os.pathsep.join(added + [p for p in current if p])
    return added


def _safe_extract(archive: Path, dest: Path) -> None:
    dest = dest.resolve()
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                if not (dest / name).resolve().is_relative_to(dest):
                    raise ValueError(f"unsafe path in archive: {name}")
            z.extractall(dest)
    else:
        with tarfile.open(archive) as t:
            for member in t.getmembers():
                if not (dest / member.name).resolve().is_relative_to(dest) or member.issym() or member.islnk():
                    raise ValueError(f"unsafe entry in archive: {member.name}")
            t.extractall(dest)


def install(tool: str, m: Machine) -> tuple:
    """Download the pinned release of `tool`, check its SHA-256, unpack it to
    ~/.spaces/tools/<tool>/ and put it on PATH. Returns (bin dir or None, what happened)."""
    key = (m.system, ARCH.get(str(m.arch).lower(), str(m.arch).lower()))
    if key not in ASSETS.get(tool, {}):
        return None, f"no pinned {tool} for {key[0]} on {key[1]}"
    url, sha = ASSETS[tool][key]
    target = tools_home(m.home) / tool
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / url.rsplit("/", 1)[1]
        try:
            m.download(url, archive)
        except Exception as exc:   # the network, a proxy, a full disk
            return None, f"the download of {tool} failed ({type(exc).__name__})"
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != sha:
            return None, f"the {tool} download did not match its published checksum, so nothing was installed"
        unpacked = Path(tmp) / "unpacked"
        unpacked.mkdir()
        try:
            _safe_extract(archive, unpacked)
        except (ValueError, OSError, zipfile.BadZipFile, tarfile.TarError) as exc:
            return None, f"the {tool} archive could not be unpacked ({exc})"
        tops = [p for p in unpacked.iterdir()]
        root = tops[0] if len(tops) == 1 and tops[0].is_dir() else unpacked
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root, target)
    rel = {"gh": "bin", "git": "cmd", "uv": ""}[tool]
    folder = target / rel if rel else target
    exe = folder / _exe(tool, m.system)
    if exe.exists() and m.system != "Windows":
        exe.chmod(0o755)
        for sibling in folder.iterdir():   # uv ships uvx beside it
            if sibling.is_file():
                sibling.chmod(0o755)
    os.environ["PATH"] = os.pathsep.join([str(folder)] + [p for p in os.environ.get("PATH", "").split(os.pathsep)
                                                          if p and p != str(folder)])
    return folder, f"installed {tool} {VERSIONS[tool]} from its official release into {folder}"


# ----- the checks ---------------------------------------------------------------------------
def _git(m: Machine, fix: bool) -> Row:
    check = "git --version"
    res = m.run(["git", "--version"])
    if res.returncode == 0:
        return Row("git", True, res.stdout.strip(), check)
    yours, fixed = "", ""
    if m.system == "Windows":
        if fix:
            folder, fixed = install("git", m)
            if folder and m.ok("git", "--version"):
                return Row("git", True, m.out("git", "--version"), check, fixed=fixed)
        yours = ("install Git for Windows once: run `! winget install --id Git.Git -e`, then say the "
                 "sentence again" + (f" ({fixed})" if fixed else ""))
    elif m.system == "Darwin":
        if fix and m.ok("brew", "--version"):
            m.run(["brew", "install", "git"])
            if m.ok("git", "--version"):
                return Row("git", True, m.out("git", "--version"), check, fixed="installed git with Homebrew")
        yours = ("a dialog installs Apple's command line tools, which include git: run "
                 "`! xcode-select --install`, click Install, then say the sentence again")
    else:
        pm = "apt-get" if m.ok("apt-get", "--version") else ("dnf" if m.ok("dnf", "--version") else None)
        if fix and pm and (m.root or m.ok("sudo", "-n", "true")):
            prefix = [] if m.root else ["sudo", "-n"]
            if pm == "apt-get":
                m.run(prefix + ["apt-get", "update", "-qq"])
            m.run(prefix + [pm, "install", "-y", "git"])
            if m.ok("git", "--version"):
                return Row("git", True, m.out("git", "--version"), check, fixed=f"installed git with {pm}")
        yours = (f"install git once (it asks for your password): run `! sudo {'dnf' if pm == 'dnf' else 'apt'} "
                 "install git`, then say the sentence again")
    return Row("git", False, (res.stderr or "not found").strip()[:120], check, yours=yours)


def _gh(m: Machine, fix: bool) -> Row:
    check = "gh --version"
    res = m.run(["gh", "--version"])
    if res.returncode == 0:
        return Row("gh", True, res.stdout.strip().splitlines()[0] if res.stdout.strip() else "gh", check)
    note = ""
    if fix:
        folder, note = install("gh", m)
        if folder and m.ok("gh", "--version"):
            return Row("gh", True, f"gh {VERSIONS['gh']}", check, fixed=note)
    return Row("gh", False, "not found", check, yours=(
        f"{note}; " if note else "") + "install GitHub's command line tool once: see https://cli.github.com, "
        "then say the sentence again")


def _identity(m: Machine, fix: bool) -> Row:
    check = "git config user.name && git config user.email"
    name, email = m.out("git", "config", "user.name"), m.out("git", "config", "user.email")
    if name and email:
        return Row("git identity", True, f"{name} <{email}>", check)
    if fix:
        try:
            user = json.loads(m.out("gh", "api", "user") or "{}")
        except ValueError:
            user = {}
        if user.get("login"):
            name = name or user.get("name") or user["login"]
            email = email or user.get("email") or f"{user['id']}+{user['login']}@users.noreply.github.com"
            m.run(["git", "config", "--global", "user.name", name])
            m.run(["git", "config", "--global", "user.email", email])
            return Row("git identity", True, f"{name} <{email}>", check, fixed="taken from your GitHub account")
    return Row("git identity", False, "not set", check, yours=(
        "tell git who you are once: `! git config --global user.name \"Your Name\"` and "
        "`! git config --global user.email you@example.com`"))


def _credentials(m: Machine, fix: bool) -> Row:
    check = "git config --get-regexp credential"
    if m.out("git", "config", "--get-regexp", "credential"):   # any scope: Git for Windows sets it system-wide
        return Row("git credentials", True, "a credential helper is set", check)
    if fix and m.ok("gh", "auth", "setup-git"):
        return Row("git credentials", True, "gh signs git in", check, fixed="ran `gh auth setup-git`")
    return Row("git credentials", False, "no credential helper", check,
               yours="let git push with your GitHub sign-in: run `! gh auth setup-git`")


def _long_paths(m: Machine, fix: bool) -> Row:
    check = "git config core.longpaths"
    if m.out("git", "config", "core.longpaths") == "true":
        return Row("long paths", True, "core.longpaths true", check)
    if fix:
        m.run(["git", "config", "--global", "core.longpaths", "true"])
        return Row("long paths", True, "core.longpaths true", check, fixed="git may use paths over 260 characters")
    return Row("long paths", False, "off", check, yours="run `! git config --global core.longpaths true`")


def _scheduler(m: Machine) -> Row:
    if m.system == "Windows":
        return Row("scheduler", True, "Task Scheduler", "schtasks /Query /TN space-sync-<name> /XML")
    if m.run(["crontab", "-l"]).returncode != 127:
        return Row("scheduler", True, "cron", "crontab -l")
    if m.system == "Linux" and m.ok("systemctl", "--user", "show-environment"):
        return Row("scheduler", True, "systemd user timers (no cron)", "systemctl --user show-environment")
    return Row("scheduler", False, "no cron", "crontab -l", yours=(
        "install cron once so your work lands every 15 minutes: run `! sudo apt install cron` "
        "(Fedora: `! sudo dnf install cronie`), then say the sentence again"))


def _organization(m: Machine, org: str, fix: bool) -> Row | None:
    if m.out("gh", "api", f"users/{org}", "-q", ".type") != "Organization":
        return None   # a person's own account has no membership to accept
    check = f"gh api user/memberships/orgs/{org} -q .state"
    state = m.out("gh", "api", f"user/memberships/orgs/{org}", "-q", ".state")
    if state == "active":
        return Row(f"organization {org}", True, "member", check)
    if state == "pending" and fix and m.ok("gh", "api", "--method", "PATCH", f"user/memberships/orgs/{org}",
                                           "-f", "state=active"):
        return Row(f"organization {org}", True, "member", check,
                   fixed=f"accepted the invitation to {org}, as your sentence asked")
    return Row(f"organization {org}", False, state or "not a member", check, yours=(
        f"accept the invitation to {org} at https://github.com/orgs/{org}/invitation" if state == "pending"
        else f"ask an owner of {org} to invite you to the organization on GitHub"))


def _access(m: Machine, repo: str) -> Row:
    check = f"gh api repos/{repo} -q .full_name"
    if m.ok("gh", "api", f"repos/{repo}", "-q", ".full_name"):
        return Row(f"access {repo}", True, "readable", check)
    return Row(f"access {repo}", False, "not readable", check, yours=(
        f"ask the owner for read access to {repo} (it is on their Needs-you list); the sentence works once "
        "you have it"))


def _rate_limit(m: Machine) -> Row | None:
    left = m.out("gh", "api", "rate_limit", "-q", ".resources.core.remaining")
    if not left.isdigit() or int(left) > 0:
        return None
    return Row("rate limit", False, "0 requests left", "gh api rate_limit -q .resources.core.remaining",
               yours="GitHub's hourly request limit is used up; it resets within the hour: say the sentence again then")


def diagnose(m: Machine | None = None, fix: bool = True, orgs=(), repos=(), offline: bool = False,
             spaces=None) -> list:
    """Every prerequisite as a Row, in order; with `fix`, what needs no password is fixed on the
    way. A row that blocks the ones after it (no network, no GitHub sign-in) ends the walk."""
    m = m or Machine()
    use_tools(m.home)
    rows = [Row("python", sys.version_info >= (3, 10), platform.python_version(), "python --version",
                yours="Python 3.10 or newer is needed: run the bootstrap script, which installs one "
                "(`sh _meta/tools/bootstrap.sh`, on Windows `_meta\\tools\\bootstrap.ps1`)")]
    free = m.free(m.home)
    rows.append(Row("disk", free >= MIN_FREE, f"{free / (1 << 30):.1f} GB free", "free space in your home folder",
                    yours=f"free at least 1 GB on the disk that holds {m.home} (now {free / (1 << 30):.1f} GB)"))
    if spaces and m.system == "Darwin":
        spaces = Path(spaces).expanduser().resolve()
        guarded = [p for p in PROTECTED if spaces.is_relative_to((Path(m.home) / p).resolve())]
        if guarded:
            rows.append(Row("spaces folder", False, str(spaces), "the --home folder", yours=(
                f"choose a folder outside {guarded[0]}: the default ~/spaces needs no permission dialog")))
    if offline:
        return rows
    if not m.reach():
        rows.append(Row("network", False, "github.com unreachable", "https://api.github.com",
                        yours="there is no connection to github.com: reconnect, then say the sentence again"))
        return rows
    rows.append(Row("network", True, "github.com reachable", "https://api.github.com"))
    git, gh = _git(m, fix), _gh(m, fix)
    rows += [git, gh]
    if git.ok and m.system == "Windows":
        rows.append(_long_paths(m, fix))
    rows.append(_scheduler(m))
    if not gh.ok:
        return rows
    signed = m.ok("gh", "auth", "status")
    rows.append(Row("github sign-in", signed, "signed in" if signed else "not signed in", "gh auth status",
                    yours="sign in to GitHub once: run `! gh auth login --web --git-protocol https` and finish in "
                    "the browser, then say the sentence again"))
    if not signed:
        return rows
    if git.ok:
        rows += [_identity(m, fix), _credentials(m, fix)]
    rows += [r for r in (_organization(m, org, fix) for org in dict.fromkeys(orgs)) if r]
    rows += [_access(m, repo) for repo in dict.fromkeys(repos)]
    limit = _rate_limit(m)
    if limit:
        rows.append(limit)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--org", action="append", default=[])
    ap.add_argument("--repo", action="append", default=[])
    ap.add_argument("--spaces")
    ap.add_argument("--check", action="store_true", help="report only, change nothing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rows = diagnose(fix=not args.check, orgs=args.org, repos=args.repo, spaces=args.spaces)
    if args.json:
        print(json.dumps([asdict(r) for r in rows], indent=2))
    else:
        for r in rows:
            print(f"{'ok' if r.ok else 'yours':5} {r.name:<24} {r.fixed or r.found if r.ok else r.yours}")
            if not r.ok:
                print(f"      check: {r.check}")
    return 0 if all(r.ok for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
