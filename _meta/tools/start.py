"""Start: one sentence in an agent session sets everything up (vision section 15).

The person says "onboard me" (or "set up our company brain") with this template's link, and
the invitation when they have one. The agent clones the template anywhere and runs this tool,
which does every step itself and asks nothing that has a default:

- reads who the person is from git and the host, and their invitation from the brain
  (named, or found by their name);
- a person: creates their workspace repository from the template, clones it, and runs
  onboard there (the brain and every crew project mounted, the personal instruction at user
  level, the sync task, the host's merge settings, the first status and the tests);
- a company (no brain given): creates the brain `<company>-brain` from the template, in the
  account `--org` names, else the one the signins phase recorded, else the organisation that
  matches the company or the mail domain, else the person's own (an account that is not clear
  is asked in the signins phase, never here), and runs onboard's company case, which also
  creates the person's own workspace;
- schedules the person's two routines (the sweep chain and the daily chain) itself, as OS tasks
  that outlive the session (`onboard.register_routines`), with no step left for the agent;
- prints what only the person can do (sign-ins), each with the check that proves it;
- keeps the onboarding page `~/.spaces/onboarding.html` (`onboarding_page.py`), rewritten after
  every step: what is done, what runs now, what is next, what only the person can do.

Defaults the person may want to change (the domains, the never-leaves list, the schedule)
are taken and kept in the setup journal's `settings`, which the progress page shows; they are not
questions on Needs you. Run again, it changes nothing that is right.
`--dry-run` prints the plan and stops before onboard; offline, a space is copied under
`--home` when given, else beside this copy.

The setup is five phases, in order: signins, apps, research, build, history. Each writes its
state to the setup journal (`~/.spaces/setup.yaml`, `setup_journal.py`), so a session that
closes, or a run the next day, continues at the first phase that is not done: `resume` says which
(`resume --json` prints the next phase, what waits for the person and every phase's record). The
build is everything described above; the flags below are its own, and without a phase word they
run it (`start.py --company X` is `start.py build --company X`). `--wait` is for a phase that
waits on the person: it stays until what they must do is done. `--json` prints the phase's
result, with the open needs, as one object.

`signins` starts both sign-ins that only the person can give, GitHub's one-time code and the
Claude command line's browser sign-in, in the first minute (`signin.py`): gh is made sure of and
its flow started at once, then claude is (installed when missing, the long part) and its
sign-in started; the rest of the doctor's walk is the build's. Each is a need; with `--wait` it
stays until both read back. Once GitHub does, it
works out where the company's repositories go (`pick_namespace`): when that is not clear it is
one more need, the `namespace`, which `build` refuses to start before, and `build --org <name>`
answers. Setup never stops at it.

`apps` asks Claude's command line which apps it reaches (`apps.py`), then runs one read-only
reader per app that decides whose account it is, whether it holds work for this company and how
much there is per month. Apps that are not work are skipped, and a mailbox in someone else's name
is only counted. The verdicts, the app card (shown at least ten minutes before any content is
copied) and the feeds go into the journal; an app to sign in to again, or one that cannot be
read, is a need that waits. `apps --probe` stops after the question, in the first minutes: it
records the apps found and, when there is none, asks the person to connect one.

`research` reads the company's own sources (`research.py`): its site, the public business register of
its country, its code organisation, the documents on this computer, a few notes of Claude's and, once
the app card has been shown for ten minutes, the documents app. What was read goes, with keys and
passwords dropped, to `~/.spaces/research/records.jsonl`, merged into a cited profile
(`profile.yaml`); the company card goes into the journal, where the apps phase finds it. A colleague
joining a brain skips it. The build puts the records into the new brain and drafts its company
profile (`onboard.write_profile`), one item on Needs you.

`history` reads the person's history oldest first, in parts paced to their own usage limits (`backfill.py`): it
plans the parts from the counts of the apps read as work, keeps the usage reading given with `--reading`, and
starts part 1 now. The routines read the rest at night, or by day while there is room; a closed lid or a usage
limit pauses a part and the next run goes on from its checkpoint. A paid account gets the plan and no part until
the person says go. `--wait-part p1` stays until part 1 is done or paused.

`build` takes what the phases before it found (`found_by_setup`): the company's name from the
researched card, the feeds of the apps that are read, the namespace as the account, and the
briefing address (`briefing_address`): the address given, else the account of the mail app read as
work when it is an address the person is known to have or one on a company domain they typed
(`--domain`), else git's, else the Claude sign-in's; with none, one item asks and the build waits. What the person said
on the command line wins, and the journal's build record says where the address came from. It
writes the apps' verdicts, without their counts, to the workspace's private zone, where the
routines read their grant, and schedules the routines (`finish_workspace`). The workspace goes in the brain's account; when that is an
organisation whose members can read each other's repositories, one item on Needs you gives the
click path to change it, which only the person can do. Online, the build is done only once the
routines proved they run on their own (`prove_routines`): a one-shot task the scheduler starts runs
`routine.py --probe` twice, the routines' allowlist written between the two, and every check of the
second must pass; the task is removed after.

    python start.py [signins|apps|research|build|history|resume] [--wait] [--json] [--probe]
                    [--invitation <brain repo>:<slug> | --brain <brain repo>] [--company NAME]
                    [--domain DOMAIN] [--name NAME] [--email EMAIL] [--feeds mail,calendar,chat,calls,tracker]
                    [--org ORG] [--home DIR] [--template REPO] [--offline] [--dry-run]
                    [--reading five_hour=<pct>,week=<pct>,five_hour_resets=<iso>,week_resets=<iso>,extra=<0|1>]
                    [--wait-part p1] [--workspace DIR]
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

VENV = Path.home() / ".spaces" / "venv"


def venv_python(venv: Path) -> Path:
    return Path(venv) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _pip(run, cmd) -> bool:
    """One pip install. A certificate failure (a company proxy that re-signs traffic, a Python
    without the machine's root certificates) is retried once with the system's own certificate
    store: verification stays on."""
    res = run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        return True
    said = f"{res.stdout or ''}{res.stderr or ''}".upper()
    if "CERTIFICATE" not in said and "SSL" not in said:
        return False
    flag = "--native-tls" if Path(str(cmd[0])).stem.lower() == "uv" else "--use-feature=truststore"
    return run([*cmd, flag], capture_output=True, text=True).returncode == 0


def ensure_yaml(argv, venv=VENV, run=subprocess.run, have=None, install=None) -> int | None:
    """None when PyYAML imports here. Else the agent installs it: `pip --user` first; where the
    system Python refuses that (PEP 668: Homebrew on macOS, Debian and Ubuntu), a venv at
    ~/.spaces/venv, and start.py runs again with that interpreter, so the sync task uses it
    too. A Python without its venv module (Debian without python3-venv) gets the venv from uv,
    pinned and checksummed, with no password. Returns that run's exit code, or 1 with the one
    fix only the person can make."""
    have = have or (lambda: importlib.util.find_spec("yaml") is not None)
    if have():
        return None
    if _pip(run, [sys.executable, "-m", "pip", "install", "--quiet", "--user", "pyyaml"]):
        import site
        sys.path.append(site.getusersitepackages())
        importlib.invalidate_caches()
        if have():
            return None
    py = venv_python(venv)
    pip = [str(py), "-m", "pip", "install", "--quiet", "pyyaml"]
    if not py.exists() and run([sys.executable, "-m", "venv", str(venv)]).returncode != 0:
        if install is None:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import doctor
            install = lambda tool: doctor.install(tool, doctor.Machine())  # noqa: E731
        folder, _ = install("uv")
        uv = str(Path(folder) / ("uv.exe" if os.name == "nt" else "uv")) if folder else None
        if uv is None or run([uv, "venv", "--python", sys.executable, str(venv)]).returncode != 0:
            print("start: this Python cannot make a virtual environment; install that part once with "
                  "`sudo apt install python3-venv` (Debian, Ubuntu), then say the sentence again")
            return 1
        pip = [uv, "pip", "install", "--python", str(py), "--quiet", "pyyaml"]
    if not _pip(run, pip):
        print(f"start: PyYAML did not install into {venv}; check the network, then say the sentence again")
        return 1
    return run([str(py), str(Path(__file__).resolve()), *argv]).returncode


if __name__ == "__main__":   # the agent does every step: the one library the tools need, first
    _code = ensure_yaml(sys.argv[1:])
    if _code is not None:
        sys.exit(_code)

import yaml  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apps  # noqa: E402
import doctor  # noqa: E402
import offboard  # noqa: E402
import onboard  # noqa: E402
import onboarding_page  # noqa: E402
import reader  # noqa: E402
import research  # noqa: E402
import routine  # noqa: E402
import setup_journal  # noqa: E402
import signin  # noqa: E402
import spaceyaml  # noqa: E402

HERE = Path(__file__).resolve().parents[2]
LAST_REPORT = None   # the report of the last build in this process: its steps stay readable after the phase has returned its summary
# One start at a time on this machine. The lock holds its process id: one whose process is gone is
# a crashed run's (or a closed session's) and is taken over at once; a lock with no readable id
# waits out LOCK_HOURS. The lock lives in the machine's temporary folder; SPACES_START_LOCK moves
# it (the tests use their own).
LOCK = None
LOCK_HOURS = 1
# A start never runs this long (a routine is stopped after one hour), so a live process id on an
# older lock is a stranger's: Windows hands a finished process's number to the next one.
PID_REUSE_HOURS = 3
_HELD = None   # the start lock `run_phase` holds for a build: from before it is marked running until its state is written
BUSY_WHY = ("Another setup is building your spaces on this computer right now. It finishes on its own; say the "
            "sentence again once it has.")
STOPPED_WHY = ("The build stopped before it finished, and nothing is running it now. Say the sentence again to carry "
               "on; every step already done is kept.")
FAULT_WHY = ("This step stopped on an unexpected fault, so it did not finish. Say the sentence again; if it stops "
             "again, your agent has the details.")
ROUTINES = HERE / "_meta" / "templates" / "routines"
INVITATIONS = "_meta/state/invitations"
GITHUB = "https://github.com/"


# ----- identity and references ---------------------------------------------------------
def _out(argv, cwd=None) -> str:
    try:
        res = onboard.run_cmd(argv, cwd=cwd)
    except Exception:
        return ""
    return (res.stdout or "").strip() if res.returncode == 0 else ""


def identity(name=None, email=None, offline=False) -> dict:
    """Who is starting: the given values, else git's identity, else the host's account."""
    who = {"name": name or "", "email": email or "", "login": ""}
    if not who["name"]:
        who["name"] = _out(["git", "config", "--global", "user.name"]) or _out(["git", "config", "user.name"])
    if not who["email"]:
        who["email"] = _out(["git", "config", "--global", "user.email"]) or _out(["git", "config", "user.email"])
    if not offline:
        raw = _out(["gh", "api", "user"])
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        who["login"] = data.get("login") or ""
        who["name"] = who["name"] or data.get("name") or ""
        who["email"] = who["email"] or data.get("email") or ""
    who["name"] = who["name"] or onboard.humanize(getpass.getuser())
    if "noreply" in who["email"]:
        who["email"] = ""  # a host's no-reply address cannot receive the morning briefing
    return who


def parse_invitation(text: str) -> tuple[str, str | None]:
    """`<brain>:<slug>` where brain is `owner/repo`, a URL or a local path; the slug is optional."""
    text = str(text).strip()
    head, sep, tail = text.rpartition(":")
    if sep and tail and not re.search(r"[/\\]", tail) and head:
        return head, tail.strip()
    return text, None


def normalize(brain: str) -> str:
    brain = str(brain).strip().rstrip("/")
    if brain.startswith(GITHUB):
        brain = brain[len(GITHUB):]
    return brain[:-4] if brain.endswith(".git") else brain


def is_local(brain: str) -> bool:
    return Path(brain).exists()


def brain_url(brain: str) -> str:
    return str(Path(brain).resolve()) if is_local(brain) else GITHUB + brain


def read_file(brain: str, rel: str) -> str | None:
    if is_local(brain):
        path = Path(brain) / rel
        return path.read_text(encoding="utf-8") if path.is_file() else None
    text = _out(["gh", "api", f"repos/{brain}/contents/{rel}", "-H", "Accept: application/vnd.github.raw"])
    return text or None


def list_dir(brain: str, rel: str) -> list:
    if is_local(brain):
        path = Path(brain) / rel
        return sorted(p.name for p in path.glob("*.yaml")) if path.is_dir() else []
    raw = _out(["gh", "api", f"repos/{brain}/contents/{rel}", "-q", ".[].name"])
    return sorted(n for n in raw.splitlines() if n.endswith(".yaml"))


def find_invitation(brain: str, slug: str | None, who: dict) -> tuple[dict | None, str]:
    """The invitation by its slug, else the one whose name, host login or email is this person's."""
    if slug:
        text = read_file(brain, f"{INVITATIONS}/{slug}.yaml")
        if not text:
            return None, f"no invitation {slug} in {brain}"
        return yaml.safe_load(text) or {}, f"invitation {slug} from {brain}"
    found = []
    for name in list_dir(brain, INVITATIONS):
        data = yaml.safe_load(read_file(brain, f"{INVITATIONS}/{name}") or "") or {}
        keys = {str(data.get(k) or "").strip().lower() for k in ("name", "github", "login", "email")} - {""}
        mine = {str(who.get(k) or "").strip().lower() for k in ("name", "login", "email")} - {""}
        if keys & mine:
            found.append((name, data))
    if len(found) == 1:
        return found[0][1], f"invitation {found[0][0][:-5]} from {brain}, found by name"
    if len(found) > 1:
        return None, f"{len(found)} invitations match {who.get('name')}: name the slug"
    return None, "no invitation found: set up with the brain's defaults"


def _brain_yaml(text) -> dict:
    try:
        data = yaml.safe_load(text or "")
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) and data.get("profile") == "brain" else {}


def adopted_namespace(home=None, company=None) -> str | None:
    """The account of the brain an earlier start cloned under `home`: a re-run adopts it."""
    slug = onboard.slugify(company) if company else None
    for name in ([f"{slug}-brain"] if slug else []) + ["brain"] if home else []:
        settings, remote = Path(home) / name / "space.yaml", onboard.git_remote(Path(home) / name)
        data = _brain_yaml(settings.read_text(encoding="utf-8")) if settings.exists() else {}
        if data and remote and (not slug or data.get("name") == slug):
            return remote.split("/")[0]
    return None


MAX_ORG_NAMES = 10   # display names are one lookup each: a person in more organisations than this is matched by login for the rest


def member_orgs() -> list:
    """The organisations the signed-in GitHub account is an active member of, each as
    {"login", "name"}: the display name comes from one lookup per organisation (the memberships
    carry none), for the first MAX_ORG_NAMES of them. A lookup that fails leaves the name empty,
    and the organisation is then matched by its login alone."""
    logins = [o for o in _out(["gh", "api", "user/memberships/orgs?state=active", "-q",
                               ".[].organization.login"]).splitlines() if o]
    orgs = []
    for number, login in enumerate(logins):
        name = _out(["gh", "api", f"orgs/{login}", "-q", ".name"]) if number < MAX_ORG_NAMES else ""
        orgs.append({"login": login, "name": "" if name == "null" else name})
    return orgs


def email_domain(email) -> str | None:
    return str(email).split("@", 1)[1] if "@" in str(email or "") else None


def _plain(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def pick_namespace(login: str, orgs: list, company: str | None, domain: str | None) -> tuple:
    """Where a new company's repositories go, from who the person is. `orgs` are logins, or
    {"login", "name"} with the organisation's display name too. No organisations: their own
    account. Exactly one organisation whose login or display name is the company's name or the
    first label of the mail domain (hyphens and capitals aside): that one. Anything else is a
    choice only the person can make: (None, the organisations' logins and their own account)."""
    names = {}
    for entry in orgs:
        org, name = (entry.get("login"), entry.get("name")) if isinstance(entry, dict) else (entry, "")
        if org:
            names[org] = names.get(org) or name or ""
    if not names:
        return login, []
    wanted = {_plain(company), _plain(str(domain or "").split(".")[0])} - {""}
    found = [org for org, name in names.items() if wanted & {_plain(org), _plain(name)}]
    return (found[0], []) if len(found) == 1 else (None, [*names, login])


def brain_name(slug: str, org: str, home, offline: bool) -> str:
    """The brain's repository is `<company>-brain`; a `brain` an earlier start made for this
    company (cloned under `home`, or on the host) is adopted unchanged by a re-run."""
    new = f"{slug}-brain"
    if (Path(home) / new / "space.yaml").exists():
        return new
    legacy = Path(home) / "brain" / "space.yaml"
    if legacy.exists():
        return "brain" if _brain_yaml(legacy.read_text(encoding="utf-8")).get("name") == slug else new
    if not offline and org and read_file(f"{org}/{new}", "space.yaml") is None:
        if _brain_yaml(read_file(f"{org}/brain", "space.yaml")).get("name") == slug:
            return "brain"
    return new


def org_title(org: str, offline: bool) -> str:
    title = "" if offline else _out(["gh", "api", f"orgs/{org}", "-q", ".name"])
    return title if title and title != "null" else onboard.humanize(org)


# ----- the routines --------------------------------------------------------------------
def render_routines(workspace: Path, person: str) -> list:
    """Write the person's routine prompts to `_meta/state/routines/` for the agent to schedule."""
    out = []
    target = Path(workspace) / "_meta" / "state" / "routines"
    for src in sorted(ROUTINES.glob("*.md")):
        text = src.read_text(encoding="utf-8").format(workspace=Path(workspace).as_posix(), person=person,
                                                      python=onboard.python_cmd())
        dst = target / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8", newline="\n")
        meta = yaml.safe_load(text.split("---")[1]) or {}
        out.append({"name": meta.get("name") or src.stem, "schedule": meta.get("schedule"),
                    "timezone": meta.get("timezone") or "local", "prompt_file": dst.as_posix()})
    return out


# ----- start ---------------------------------------------------------------------------
def lock_path() -> Path:
    return Path(LOCK or os.environ.get("SPACES_START_LOCK") or Path(tempfile.gettempdir()) / "spaces-start.lock")


def _lock_pid(path) -> int | None:
    """The process id on the lock's first line; None when there is none to read."""
    try:
        return int(Path(path).read_text(encoding="utf-8").splitlines()[0])
    except (OSError, ValueError, IndexError):
        return None


def take_lock(path=None) -> Path | None:
    """The sentence said twice: the second start waits for the first instead of racing it, for as
    long as the first runs. A lock whose process is gone is taken over at once. The lock keeps its
    process's start time on its second line (`routine.process_started`, Ruling R45): the same pid that
    started then is the start that still runs, however old the lock; one that started at another time
    is another process with that number, and its lock is taken over at once. A lock without a start
    time, or a system that does not say one, keeps the age rule: older than PID_REUSE_HOURS with its
    process id running is a stranger's."""
    path = Path(path or lock_path())
    try:
        age = time.time() - path.stat().st_mtime
        held = _lock_pid(path)
        if held is None:
            stale = age >= LOCK_HOURS * 3600
        elif not routine.pid_alive(held):
            stale = True
        else:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                lines = []
            then = lines[1].strip() if len(lines) > 1 else ""
            now = routine.process_started(held) if then else None
            stale = then != now if then and now else age >= PID_REUSE_HOURS * 3600
        if not stale:
            return None
        path.unlink()
    except FileNotFoundError:
        pass
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    mark = routine.process_started(os.getpid())
    with os.fdopen(fd, "w") as fh:
        fh.write(f"{os.getpid()}\n{mark}\n" if mark else f"{os.getpid()}\n")
    return path


def _hold_lock() -> tuple:
    """(the start lock, whether the caller took it and so releases it): the lock `run_phase` holds for this build
    (R45, F-5), else a new one (`take_lock`: None while another start holds it)."""
    if _HELD is not None:
        return _HELD, False
    lock = take_lock()
    return lock, lock is not None


def checkup(report: onboard.Report, brain, tpl, offline: bool, fix: bool, spaces) -> bool:
    """The doctor, before anything is created: every prerequisite of this machine, fixed where
    no password is needed; what is the person's becomes a row with its check, and start stops."""
    brain = normalize(brain) if brain and not is_local(brain) else None
    repos = [r for r in (brain, tpl) if r and "/" in r and not is_local(r)]
    # membership only in the company's own organization; the template needs read access alone
    rows = doctor.diagnose(fix=fix, orgs=[brain.split("/")[0]] if brain else [], repos=repos,
                           offline=offline, spaces=spaces)
    for r in rows:
        report.add(f"doctor: {r.name}", r.ok, r.check, (r.fixed or r.found) if r.ok else r.yours,
                   needs_owner=not r.ok)
    return all(r.ok for r in rows)


def plan(report: onboard.Report, dst, full, tpl, case) -> onboard.Report:
    """A dry run stops before onboard: nothing was created, so there is no space to read."""
    report.plan = [f"create {full} from {tpl} and clone it into {dst}" if full else f"copy the template to {dst}",
                   f"run onboard there ({case} case): mounts, the personal instruction, the sync task, "
                   "the first commit",
                   "nothing was created: say the sentence again without --dry-run"]
    report.add("dry run", True, "python _meta/tools/start.py (without --dry-run)",
               "stopped before onboard; the plan is below")
    return report


def start(here=HERE, invitation=None, brain=None, company=None, name=None, email=None, feeds=None,
          org=None, home=None, template=None, offline=False, dry_run=False,
          user_instructions=None, connected=()) -> onboard.Report:
    """`connected` are the feeds the apps phase has read already: no row asks the person to connect them."""
    report = onboard.Report(case="start")
    lock, own = _hold_lock()   # a build `run_phase` runs holds it already, until its state is written
    if lock is None:
        report.add("one start at a time", False, f"test -f {lock_path()}", "another start is running on this machine: "
                   "it finishes on its own; say the sentence again when it has")
        return report   # the running start keeps the page
    if not dry_run:
        report.progress = onboarding_page.Live()
    try:
        tpl = template or onboard.git_remote(Path(here).resolve())
        target = parse_invitation(invitation)[0] if invitation else brain
        spaces = Path(home) if home else Path.home() / "spaces"
        if checkup(report, target, tpl, offline, not dry_run, spaces):
            setup(report, here, invitation, brain, company, name, email, feeds, org, home, template, offline,
                  dry_run, user_instructions, connected)
        else:
            report.stopped = True   # nothing was created: the person's rows come first
    finally:
        if report.progress is not None:
            report.page = report.progress.finish(report)
        if own:
            lock.unlink(missing_ok=True)
    return report


def setup(report, here, invitation, brain, company, name, email, feeds, org, home, template, offline, dry_run,
          user_instructions, connected=()) -> onboard.Report:
    here = Path(here).resolve()
    who = identity(name, email, offline)
    given = Path(home) if home else None
    home = given or Path.home() / "spaces"
    space_home = given if offline else home   # offline: beside this copy unless a home is given
    tpl = template or onboard.git_remote(here)
    slug = None
    if invitation:
        brain, slug = parse_invitation(invitation)
    feeds_text = ", ".join(f.strip() for f in str(feeds).split(",") if f.strip()) if feeds else None
    if brain:
        brain = normalize(brain)
        inv, how = find_invitation(brain, slug, who)
        report.add("invitation", inv is not None or not slug, f"{INVITATIONS}/{slug or '<name>'}.yaml", how)
        if slug and inv is None:
            return report
        answers = dict(inv or {})
        report.invited = address_in(answers.get("email"))   # the journal says an address came from the invitation
        answers["name"] = answers.get("name") or who["name"]
        answers["email"] = answers.get("email") or who["email"]
        answers["slug"] = onboard.slugify(answers.get("slug") or answers["name"])
        answers["brain"] = brain_url(brain)
        answers.setdefault("parent", "brain")
        if feeds_text:
            answers["feeds"] = feeds_text
        space_org = org or answers.get("workspace_org") or answers.get("org") or who["login"]
        repo_name = f"{answers['slug']}-workspace"
        dst, full = onboard.create_space(report, here, answers["slug"], repo_name, offline, space_org,
                                         space_home, tpl, dry_run)
        if dst is None and not offline and who["login"] and space_org != who["login"]:
            # no right to create in the organization: the person's own account holds it instead
            space_org = who["login"]
            dst, full = onboard.create_space(report, here, answers["slug"], repo_name, offline, space_org,
                                             space_home, tpl, dry_run)
        if dst is None:
            return report
        if dry_run:
            return plan(report, dst, full, tpl, "person")
        answers = onboard.interview(answers, interactive=False, case="person", root=dst)
        report.settings = settings_of(answers.pop("_skipped", []))
        sub = onboard.run(dst, answers, offline=offline, org=space_org, dry_run=dry_run, home=home,
                          user_instructions=user_instructions, brain=answers["brain"], progress=report.progress,
                          connected=connected)
        person, workspace = answers["name"], dst
    else:
        space_org = org or adopted_namespace(space_home, company)
        if space_org is None:   # the signins phase already asked what is not clear: the own account is the default
            own = who.get("login") or onboard.slugify(who.get("name") or getpass.getuser())
            space_org = pick_namespace(own, [] if offline else member_orgs(), company,
                                       email_domain(who["email"]))[0] or own
        answers = {"company": company or org_title(space_org, offline), "owner": who["name"],
                   "owner_email": who["email"], "org": space_org}
        if feeds_text:
            answers["feeds"] = feeds_text
        repo_name = brain_name(onboard.slugify(answers["company"]), space_org, space_home, offline)
        dst, full = onboard.create_space(report, here, "brain", repo_name, offline, space_org, space_home, tpl,
                                         dry_run)
        if dst is None:
            report.stopped = True
            return report
        if dry_run:
            return plan(report, dst, full, tpl, "company")
        answers = onboard.interview(answers, interactive=False, case="company", root=dst)
        report.settings = settings_of(answers.pop("_skipped", []))
        sub = onboard.run(dst, answers, offline=offline, org=space_org, dry_run=dry_run, home=home,
                          user_instructions=user_instructions, progress=report.progress, connected=connected)
        person = who["name"]
        workspace = next((Path(p) for p in sub.spaces.values()
                          if Path(p) != dst and spaceyaml.load(p).profile == "personal"), None)
    report.steps.extend(sub.steps)
    report.spaces.update(sub.spaces)
    report.user_instructions = sub.user_instructions
    if workspace is not None and not dry_run:
        if who["email"]:
            fill_address(report, person, who["email"])
        report.workspace = workspace
        report.routines = finish_workspace(report, workspace, person, space_org, offline)
        report.who = who
    return report


def settings_of(skipped: list) -> list:
    """The interview's defaults that nobody was asked about, as the setup journal keeps them for
    the progress page's Settings section: each question's key and words, and the default taken. They
    are not questions on Needs you."""
    return [{"key": q["key"], "ask": q["ask"],
             "default": list(q["default"]) if isinstance(q["default"], (list, tuple)) else q["default"]}
            for q in skipped]


def fill_address(report, owner: str, address: str, replace: bool = False) -> None:
    """The person's own spaces that have no briefing address get the one now known: the answer to the item
    that asked, said again with the sentence. An address that is there is not replaced by one worked out
    (`replace` False); the one the person typed is their choice and replaces it (`replace` True). The line
    of space.yaml is changed in place, the rest of the file as it was."""
    for path in dict.fromkeys(report.spaces.values()):
        space = spaceyaml.load(path)
        if space.owner == owner and (not space.owner_email or (replace and space.owner_email != address)):
            file = Path(path) / "space.yaml"
            text = file.read_text(encoding="utf-8")
            filled = re.sub(r"^owner_email:.*$", lambda _: f"owner_email: {json.dumps(address)}", text, count=1, flags=re.M)
            if filled != text:
                file.write_text(filled, encoding="utf-8", newline="\n")


def finish_workspace(report, workspace, person: str, org: str, offline: bool) -> list:
    """What makes a built workspace run without the agent: the routine prompts are rendered and
    scheduled as OS tasks that outlive the session (`onboard.register_routines`; offline they are
    recorded, not registered), the account's base permission is read (`onboard.org_members_item`), and
    the briefing address is looked at: with none, one plain item asks for it and a row only the person
    can give makes the build wait, as every other thing only they can give does. Returns the rendered
    routines."""
    routines = render_routines(workspace, person)
    onboard.register_routines(report, workspace, offline=offline)
    if not offline:
        onboard.org_members_item(workspace, org)
    address = spaceyaml.load(workspace).owner_email
    onboard.briefing_item(workspace, address)
    if "@" not in address:
        report.add("briefing address", False, f"grep owner_email {Path(workspace).as_posix()}/space.yaml",
                   "tell your agent which email address your morning briefing should go to, and say the sentence again",
                   needs_owner=True)
    return routines


def summary(report: onboard.Report) -> dict:
    """What happened and what only the person can do, for the agent to say in a few lines."""
    failed = [s.row() for s in report.failed()]
    owner = [s.row() for s in report.steps if not s.done and s.needs_owner]
    return {"workspace": str(getattr(report, "workspace", "") or ""),
            "spaces": {k: str(v) for k, v in report.spaces.items()},
            "routines": getattr(report, "routines", []), "plan": getattr(report, "plan", []),
            "stopped": bool(getattr(report, "stopped", False)), "page": str(getattr(report, "page", "") or ""),
            "done": sum(1 for s in report.steps if s.done), "failed": failed, "yours": owner}


# ----- the phases ----------------------------------------------------------------------
PHASES = setup_journal.PHASES


def _pending(phase: str) -> dict:
    return {"phase": phase, "state": "pending"}


SIGNIN_NEEDS = ("github-code", "claude-signin", "namespace")
GITHUB_NEED = "Sign in to GitHub: open the link and type the one-time code there."
CLAUDE_NEED = "Sign in to Claude with your Claude account in the browser window that opened."
# What the phase says when it fails, in plain words; the tools' own words go to the journal's `detail` (R45, Minor 3).
NETWORK_WHY = ("This computer could not reach GitHub, so nothing could be signed in to. Check the internet connection, "
               "then say the sentence again.")
GH_MISSING_WHY = ("GitHub's command line is not on this computer and could not be put there by itself, so GitHub "
                  "could not be signed in to.")
CLAUDE_MISSING_WHY = ("Claude's command line is not on this computer and could not be put there by itself, so Claude "
                      "could not be signed in to.")
GITHUB_START_WHY = "GitHub's sign-in did not start. Say the sentence again to try once more."
CLAUDE_START_WHY = "Claude's sign-in did not start. Say the sentence again to try once more."


def _signins_record() -> dict:
    record = setup_journal.load().get("signins")
    return record if isinstance(record, dict) else {}


def _asked(kind: str, code: bool = False) -> bool:
    """Whether the journal lists a need of this kind (with a code shown, when `code`)."""
    return any(n.get("kind") == kind and (n.get("code") or not code)
               for n in setup_journal.open_needs(setup_journal.load()))


def record_namespace(org: str) -> None:
    """Where the company's repositories go is settled: remembered in the signins record, and the
    question closed. The phase is done when that was all it waited for."""
    record = _signins_record()
    left = [n for n in setup_journal.open_needs(setup_journal.load())
            if n.get("kind") in ("github-code", "claude-signin")]
    state = "done" if record.get("state") == "waiting" and not left else record.get("state", "pending")
    setup_journal.set_phase("signins", state, namespace=org)
    setup_journal.clear_need("namespace")


def _start_github(m, record, errors, plain) -> None:
    """Start GitHub's device flow, unless an earlier run's is still going and its code is still
    listed. The flow's process is written to the journal at once: a run that is closed while it
    waits must not start a second flow when it is run again. What cannot start is an error, not
    a stop: the Claude sign-in still starts."""
    if signin.flow_alive(record.get("github_pid"), record.get("github_started")) and _asked("github-code", code=True):
        return
    try:
        got = signin.github_start(m)
    except (OSError, signin.SigninError) as exc:
        errors.append(f"GitHub's sign-in did not start: {exc}")
        plain.append(GITHUB_START_WHY)
        return
    setup_journal.need("github-code", GITHUB_NEED, "gh auth status", link=got["link"], code=got["code"])
    setup_journal.set_phase("signins", "running", github_pid=got["pid"], github_started=int(time.time()))


def _start_claude(m, record, errors, plain) -> None:
    """Start the Claude command line's sign-in, on the same terms as GitHub's."""
    if signin.flow_alive(record.get("claude_pid"), record.get("claude_started")) and _asked("claude-signin"):
        return
    try:
        got = signin.claude_start(m)
    except (OSError, signin.SigninError) as exc:
        errors.append(f"Claude's sign-in did not start: {exc}")
        plain.append(CLAUDE_START_WHY)
        return
    setup_journal.need("claude-signin", CLAUDE_NEED, " ".join(signin.CLAUDE_STATUS), link=got["link"])
    setup_journal.set_phase("signins", "running", claude_pid=got["pid"], claude_started=int(time.time()))


def _settle_namespace(args, login, who, record, facts) -> tuple:
    """Where a new company's repositories go, once GitHub reads back: what `--org` said or an
    earlier run settled, else `pick_namespace`; a choice only the person can make is a need.
    The organisations that were looked up are kept in the journal (`orgs`): the research finds the
    person's other companies there. Returns (account or None, the choices)."""
    namespace, choices = args.org or record.get("namespace"), []
    if not namespace:
        orgs = member_orgs()
        facts["orgs"] = orgs
        namespace, choices = pick_namespace(login, orgs, args.company, email_domain(who["email"]))
    setup_journal.clear_need("namespace")   # asked again below when it is still not clear, whatever the list was
    if namespace:
        facts["namespace"] = namespace
    else:
        ask = (f"Where should the company's files live on GitHub? Your own account {login} (recommended), or an "
               f"organisation you belong to: {', '.join(choices[:-1])}.")
        setup_journal.need("namespace", ask, "python _meta/tools/start.py build --org <account or organisation>")
    return namespace, choices


def phase_signins(args) -> dict:
    """Both sign-ins at once, in the first minute: gh is made sure of and GitHub's device flow
    started at once, then claude is made sure of (installed when it is missing: the long part)
    and its browser sign-in started, each unless it already reads back (or an earlier run's is
    still going); each is a need with the check that proves it. The rest of the doctor's walk is
    the build's. With `--wait` it stays until both read back. Once GitHub does, where a new
    company's repositories go is worked out; when that is not clear it is a `namespace` need too
    (a colleague joining by invitation is not asked). State: `waiting` while a sign-in or the
    namespace is open, `done` when both read back and the namespace is known, `failed` when a
    tool is missing or a sign-in would not start. The journal's `signins` record keeps what later
    phases read: `github` (the account), `claude` (its state), `claude_email` and `claude_org`
    (whose Claude account the command line is: the desktop app can be another one), `namespace`,
    and the running flows' ids. Why it failed is said in plain words (NETWORK_WHY and the others); the tools' own
    words are its `detail`, which the journal keeps and the person is not shown (R45, Minor 3). A dry run only
    reads."""
    if args.offline:
        return {"state": "done", "needs": [], "github": False, "claude": "signed-out", "namespace": None,
                "why": "offline: nothing to sign in to"}
    m, fix, errors, plain = doctor.Machine(), not args.dry_run, [], []
    record = {} if args.dry_run else _signins_record()
    gh = doctor.tool_row("gh", m, fix)
    github = gh.ok and signin.github_signed_in(m)
    if not gh.ok:
        errors.append(gh.yours or "GitHub's command line could not be checked")
        plain.append(NETWORK_WHY if gh.name == "network" else GH_MISSING_WHY)   # with no network the row is the network's
    elif not github and not args.dry_run:
        _start_github(m, record, errors, plain)
    cli = doctor.tool_row("claude command line", m, fix)   # may install it: after the GitHub code exists
    claude = signin.claude_state(m) if cli.ok else "missing"
    if not cli.ok:
        errors.append(cli.yours or "Claude's command line could not be checked")
        plain.append(NETWORK_WHY if cli.name == "network" else CLAUDE_MISSING_WHY)
    elif claude != "account" and not args.dry_run:
        _start_claude(m, record, errors, plain)
    errors, plain = list(dict.fromkeys(errors)), list(dict.fromkeys(plain))   # no network: one sentence, not two
    failed = {"why": " ".join(plain), "detail": "; ".join(errors)} if plain else {}
    if args.dry_run:
        return {"state": "pending", "needs": [], "github": github, "claude": claude, "namespace": None, **failed}
    facts = {}
    if args.wait and not plain and not (github and claude == "account"):
        got = signin.wait(m)
        github, claude = got["github"], got["claude"]
    new_company = not (args.invitation or args.brain)
    namespace, choices = None, []
    if github:
        setup_journal.clear_need("github-code")
        who = identity(args.name, args.email)
        login = who["login"] or onboard.slugify(who["name"] or getpass.getuser())
        facts.update(github=login, github_pid=None, github_started=None)
        if new_company:
            namespace, choices = _settle_namespace(args, login, who, record, facts)
    facts["claude"] = claude
    if claude == "account":
        setup_journal.clear_need("claude-signin")
        account = signin.claude_identity(m)
        facts.update(claude_email=account.get("email"), claude_org=account.get("org"),
                     claude_pid=None, claude_started=None)
    setup_journal.set_phase("signins", "running", **facts)
    state = ("failed" if plain else "done" if github and claude == "account" and (namespace or not new_company)
             else "waiting")
    result = {"state": state, "github": github, "claude": claude, "namespace": namespace,
              "needs": [n for n in setup_journal.open_needs(setup_journal.load()) if n.get("kind") in SIGNIN_NEEDS]}
    if choices:
        result["choices"] = choices
    result.update(failed)
    return result


CONNECT_NEED = ("Connect the apps where your work lives (your email, calendar, chat and meeting notes) in "
                "Claude's connector settings.")
RECONNECT_NEED = "{app} needs to be connected again: its sign-in ran out. Sign in to {app} in Claude's connector settings."
ADMIN_NEED = ("{app} could not be read, so it is left out for now. If your organisation limits which apps Claude "
              "may use, ask your IT administrator to allow {app}.")
APPS_CHECK = "python _meta/tools/start.py apps --probe --json"   # the probe lists the app as connected
APPS_RUN = "python _meta/tools/start.py apps --json"


def _slow_why(names: list) -> str:
    """Why the apps phase waits on apps whose readers ran out of turns or time (R45, I-3): plain, no reader's words."""
    one = len(names) == 1
    listed = names[0] if one else f"{', '.join(names[:-1])} and {names[-1]}"
    return (f"{listed} took longer to read than a reader may take, so {'it is' if one else 'they are'} read again when "
            f"you say the sentence again. Say \"skip {names[0]}\" to leave {'it' if one else 'one'} out instead.")


def _unsorted_why(names: list) -> str:
    """Why the apps phase waits on apps no name knows that the sorting run could not look at yet (R5): plain, and it
    offers to skip one."""
    return (f"I could not look at {', '.join(names)} yet. I try again the next time your apps are read; say \"skip\" "
            "and the app's name to leave one out.")


def _app_needs(verdicts: list) -> None:
    """An app to sign in to again is a `reconnect` need, one that cannot be read an `it-admin`
    need; both wait on the person and the rest of the setup goes on. A run replaces the needs of
    the run before, so the line of an app that reads again goes."""
    setup_journal.clear_need("reconnect")
    setup_journal.clear_need("it-admin")
    for v in verdicts:
        if v["verdict"] == "reconnect":
            setup_journal.need("reconnect", RECONNECT_NEED.format(app=v["app"]), APPS_CHECK, link=apps.CONNECTORS_LINK)
        elif v["verdict"] == "blocked":
            setup_journal.need("it-admin", ADMIN_NEED.format(app=v["app"]), APPS_RUN)


def _read_apps(args, groups: list, unsigned: list, tools: list, claude: str, cwd: Path, facts: dict) -> dict:
    """One reader per app, then the verdicts, the card and the feeds into the journal. The person
    is compared with their own addresses (the one given, the Claude sign-in's, git's: no call to
    the network), and work is judged against the researched company when research is through,
    else the mail domain. What finished is kept (R45, I-2): an app the journal holds a final verdict for
    (`apps.kept`) is not read again, so a run after a limit reads the apps without one and those connected since;
    an app to reconnect, a blocked one and a slow one are read again. A slow app (its reader ran out of turns or
    time) has no verdict: the phase waits, with a plain line that offers to skip it, and needs nothing of the IT
    administrator (I-3). A skip saved while the verdicts were written is on the card, the stored feeds and the
    answer too (R47, N3-2). The card also says what the sorting run made of the apps no name knew (`facts`: `sorted`,
    `unsorted`), whichever of its two writes it is (`_app_card`). An app the sorting run could not look at yet that
    the person did not skip keeps the phase waiting too, like a slow app, also when there is no app to read at all:
    the answer names them (`unsorted`), and its why offers to skip one (R5)."""
    journal = setup_journal.load()
    who = identity(args.name, args.email, offline=True)
    emails = [e for e in dict.fromkeys([args.email, (journal.get("signins") or {}).get("claude_email"), who["email"]]) if e]
    person = {"name": who["name"], "email": emails[0] if emails else "", "emails": emails,
              **apps.company_of(journal, args.company, emails)}
    limit = None
    record = journal.get("apps") if isinstance(journal.get("apps"), dict) else {}
    sorted_apps, unsorted = facts.get("sorted") or {}, facts.get("unsorted") or []
    before = [str(s) for s in record.get("person_skips") or []]
    kept = [v for v in apps.kept(record, groups) if v["app"] not in before]   # what finished before is kept (R45, I-2)
    have = {v["app"] for v in kept}
    try:   # an app the person skipped is not read again, nor one skipped before its reader starts (R43, N2-3)
        verdicts = apps.analyse([g for g in groups if g["app"] not in before and g["app"] not in have], person, cwd,
                                reader=reader.read, known_tools=tools, claude=claude, skipped=_skips_now)
    except apps.LimitReached as stop:   # what finished is kept; the rest waits for the limit to reset
        verdicts, limit = stop.verdicts, stop
    merged = {**{v["app"]: v for v in kept}, **{v["app"]: v for v in verdicts}}
    verdicts = [merged[g["app"]] for g in groups if g["app"] in merged]   # in the probe's order
    fresh = setup_journal.load().get("apps")   # read again before writing: a skip said while the readers ran wins (R40)
    skips = list(dict.fromkeys([*before, *((fresh if isinstance(fresh, dict) else {}).get("person_skips") or [])]))
    verdicts = [_skipped(v) if v.get("app") in skips and v.get("app") not in before else v for v in verdicts]
    read = {v["app"] for v in verdicts}
    verdicts += [_skipped(g) for g in groups if g["app"] in skips and g["app"] not in read]
    unfinished = [g["app"] for g in groups if g["app"] not in read and g["app"] not in skips]
    looking = [a for a in unsorted if a not in skips]   # not looked at yet, and not skipped: the phase waits for them
    verdicts = _person_skips(verdicts, skips)   # the person's word stays
    now = dt.datetime.now().astimezone()
    card = _app_card(verdicts, now, unsigned, sorted_apps, groups, unsorted, skips)
    feeds = apps.feeds(verdicts)
    state = "waiting" if limit or unfinished or looking else "done"
    setup_journal.set_phase("apps", state, **facts, verdicts=verdicts, card=card, feeds=feeds, unfinished=unfinished)
    after = setup_journal.load().get("apps")   # a skip saved between the reload above and this write (R47, N3-2)
    late = [str(s) for s in (after if isinstance(after, dict) else {}).get("person_skips") or [] if str(s) not in skips]
    if late:
        skips += late
        verdicts = apps_verdicts(setup_journal.load())
        unfinished = [a for a in unfinished if a not in late]
        looking = [a for a in looking if a not in late]
        state = "waiting" if limit or unfinished or looking else "done"
        card = _app_card(verdicts, now, unsigned, sorted_apps, groups, unsorted, skips)
        feeds = apps.feeds(verdicts)
        setup_journal.set_phase("apps", state, verdicts=verdicts, card=card, feeds=feeds, unfinished=unfinished)
    _app_needs(verdicts)
    result = {"state": state, "apps": [g["app"] for g in groups], "card": card, "feeds": feeds, "unfinished": unfinished,
              "unsorted": looking}
    if limit:
        read_now = [v for v in verdicts if v.get("verdict") != "skip"]   # the apps actually read (R43, N2-4)
        result["why"] = (f"Your Claude limit was reached with {len(read_now)} of "
                         f"{len([g for g in groups if g['app'] not in skips])} apps read"
                         + (f" ({limit.text})" if limit.text else "")
                         + ". Say the sentence again once it resets: the others are read then.")
    elif unfinished:   # no limit: what has no verdict ran out of turns or time (R45, I-3)
        result["why"] = _slow_why(unfinished)
    elif looking:      # nothing else waits: the apps the sorting run could not look at yet (R5)
        result["why"] = _unsorted_why(looking)
    return result


SKIPPED_WHY = "you asked me to skip it"


def _person_skips(verdicts: list, skipped: list) -> list:
    """The verdicts with the person's word in place of the reader's for the apps they skipped: `skip`, the reader's
    verdict kept as `was`. Not `work` or `mixed`, so the grant, the feeds, the history's counts and the research's
    documents leave the app out."""
    names = {str(s) for s in skipped}
    return [{**v, "verdict": "skip", "was": v.get("verdict"), "why": SKIPPED_WHY}
            if isinstance(v, dict) and v.get("app") in names and v.get("verdict") != "skip" else v for v in verdicts]


def _skips_now() -> set:
    """The apps the person skipped, as the journal says now: asked again right before each app's reader starts."""
    try:
        record = setup_journal.load().get("apps")
    except Exception:   # noqa: BLE001 - a journal caught mid-write: the check after the readers still holds
        return set()
    return {str(s) for s in record.get("person_skips") or []} if isinstance(record, dict) else set()


def apps_verdicts(journal: dict) -> list:
    """The apps phase's verdicts with the person's word applied (`person_skips`, `_person_skips`). The feeds, the
    grant, the history's counts, the documents step and the work mailbox read these, so a skip saved while the phase
    wrote its verdicts still holds (R43, N2-8)."""
    record = journal.get("apps") if isinstance(journal.get("apps"), dict) else {}
    return _person_skips([v for v in record.get("verdicts") or [] if isinstance(v, dict)], record.get("person_skips") or [])


def _skipped(app: dict) -> dict:
    """The verdict of an app the person skipped, with nothing a reader found in it: what the probe saw of the app (its
    name, kinds and tools) and the person's word (R40, I-5 b)."""
    return {**{k: app[k] for k in ("app", "kinds", "read", "write", "send") if k in app}, "account": None,
            "verdict": "skip", "why": SKIPPED_WHY}


def _skip_lines(card: dict, verdicts: list, others=()) -> dict:
    """The app card with a line for each app the person skipped, among the skipped ones: each verdict `skip`, and each
    of `others`, skipped apps that have no verdict because they are no group (R6)."""
    lines = [f"{v['app']}{apps.SEP}{v.get('account') or 'unknown account'}{apps.SEP}skip, {SKIPPED_WHY}"
             for v in verdicts if isinstance(v, dict) and v.get("verdict") == "skip"]
    lines += [f"{app}{apps.SEP}unknown account{apps.SEP}skip, {SKIPPED_WHY}" for app in others]
    return {**card, "skip": [*card.get("skip", []), *[line for line in dict.fromkeys(lines) if line not in card.get("skip", [])]]}


def _app_card(verdicts: list, now: dt.datetime, unsigned, sorted_apps: dict, groups: list, unsorted: list, skips) -> dict:
    """The app card (`apps.card`) with the person's word on it (`_skip_lines`). An app the person skipped that is no
    group (sorted `other` or `none`, one with no tool that only reads, one not looked at yet) is named only among the
    skipped ones, never under `not_read` or `nothing_to_read` (R6). A sorted app of a kind setup reads that `apps.group`
    kept no group of has no tool that only reads: the card says so rather than leave it out (M1)."""
    skips, grouped = {str(s) for s in skips}, {g["app"] for g in groups if isinstance(g, dict)}
    shown = {app: s for app, s in sorted_apps.items() if app not in skips}
    card = apps.card(verdicts, now, unsigned, sorted_apps=shown, unsorted=[a for a in unsorted if a not in skips],
                     unreadable=[app for app, s in shown.items() if s["kind"] in apps.KINDS and app not in grouped])
    return _skip_lines(card, verdicts, [a for a in dict.fromkeys([*sorted_apps, *unsorted]) if a in skips and a not in grouped])


def _skips_in(record: dict) -> list:
    """The apps the person skipped, as the journal's `apps` record keeps them (`person_skips`)."""
    said = record.get("person_skips") if isinstance(record, dict) else None
    return [str(s) for s in said] if isinstance(said, list) else []


def _unsorted_in(record: dict) -> list:
    """The apps the sorting run could not look at yet, as the journal's `apps` record keeps them (`unsorted`): the
    names of a list; anything else counts as none."""
    said = record.get("unsorted") if isinstance(record, dict) else None
    return [s for s in said if isinstance(s, str)] if isinstance(said, list) else []


def skip_apps(args) -> dict:
    """"Skip <app>": the person's word becomes the app's verdict (`skip`) in the journal, its card line moves to the
    skipped ones and the feeds are those of the apps still read; the card's start time stays. Once the build has
    kept the verdicts in a workspace (`routine.APPS_FILE`), that file says it too, so the routines are not granted the
    app's tools from their next run on. An app that is not among the person's apps changes nothing, and why names
    them. The person's apps are those the card names: the groups and verdicts, and the apps no name knows that were
    sorted or could not be looked at yet; one of the last that is no group is never sorted or read again, and the card
    names it among the skipped ones only (R6). The phase keeps the state its record shows (`run_phase` has marked it
    running meanwhile) until the readers have written theirs; after, it waits only while an app is unfinished or one
    not looked at yet is not skipped."""
    journal = setup_journal.load()
    record = journal.get("apps") if isinstance(journal.get("apps"), dict) else {}
    verdicts = [v for v in record.get("verdicts") or [] if isinstance(v, dict)]
    judged = isinstance(record.get("verdicts"), list)   # the readers wrote theirs: none when no app was read (R5)
    groups = [g for g in record.get("groups") or [] if isinstance(g, dict) and g.get("app")]
    unsorted = _unsorted_in(record)
    known = list(dict.fromkeys([v["app"] for v in verdicts] + [g["app"] for g in groups] + list(_sorted_in(record))
                               + unsorted))
    asked = [n.strip() for item in args.skip for n in str(item).split(",") if n.strip()]
    found = [a for a in known if a.lower() in {n.lower() for n in asked}]
    # Before any verdict the phase keeps its state: its readers may be running (R40); a skip changes no state then.
    before = _skips_in(record)
    state = (("waiting" if record.get("unfinished") or [a for a in unsorted if a not in before] else "done") if judged
             else str(record.get("state") or "pending"))
    missing = [n for n in asked if n.lower() not in {a.lower() for a in found}]
    if missing:
        return {"state": state, "skipped": [], "why": (f"There is no app called {', '.join(missing)} among your apps"
                                                       + (f": {', '.join(known)}." if known else " yet."))}
    skipped = list(dict.fromkeys([*(record.get("person_skips") or []), *found]))
    facts = {"person_skips": skipped}
    result = {"state": state, "skipped": found}
    if not judged and not getattr(args, "again", False):   # the readers may have written theirs since: the skip
        latest = setup_journal.load().get("apps")                 # goes onto them, the state is theirs; asked once only,
        if isinstance(latest, dict) and isinstance(latest.get("verdicts"), list):   # (R47, N3-3)
            return skip_apps(argparse.Namespace(**{**vars(args), "again": True}))
    if judged:   # an app not read yet (stopped at a limit) is not read at all: its verdict is the person's word
        have = {v["app"] for v in verdicts}
        verdicts = _person_skips(verdicts, skipped) + [_skipped(g) for g in groups if g["app"] in found and g["app"] not in have]
        unfinished = [a for a in record.get("unfinished") or [] if a not in found]
        state = result["state"] = "waiting" if unfinished or [a for a in unsorted if a not in skipped] else "done"
        facts["unfinished"] = unfinished
        old = record.get("card") if isinstance(record.get("card"), dict) else {}
        card = _app_card(verdicts, _now(), record.get("not_signed_in") or [], _sorted_in(record), groups, unsorted, skipped)
        card["starts_after"] = old.get("starts_after") or card["starts_after"]
        facts.update(verdicts=verdicts, card=card, feeds=apps.feeds(verdicts))
        result.update(card=card, feeds=facts["feeds"])
    setup_journal.set_phase("apps", state, **facts)
    why = f"{', '.join(found)}: nothing more is copied from it."
    workspace = _history_workspace(args, journal)
    kept = Path(workspace) / routine.APPS_FILE if workspace else None
    if kept and kept.exists():
        try:
            rows = (yaml.safe_load(kept.read_text(encoding="utf-8")) or {}).get("apps") or []
            onboard.write_yaml(kept, {"apps": _person_skips([r for r in rows if isinstance(r, dict)], skipped)})
            why += " Your routines no longer read it from their next run; what was already read stays."
        except (OSError, yaml.YAMLError, AttributeError):
            why += " Your routines could not be told yet: say it again."
    result["why"] = why
    return result


NOTHING_READABLE_WHY = ("None of your connected apps holds mail, a calendar, chat, calls, documents or tasks I can "
                        "read yet.")
SORT_LIMIT_WHY = ("Your Claude limit was reached before your apps were read{text}. Say the sentence again once it "
                  "resets: they are read then.")


def _sorted_in(record: dict) -> dict:
    """What the sorting run made of the apps no name knows, as the journal's `apps` record keeps it
    ({app: {"kind", "why"}}): the entries that are one, so a damaged journal gives fewer, never a fault."""
    said = record.get("sorted") if isinstance(record, dict) else None
    return {str(app): s for app, s in (said.items() if isinstance(said, dict) else ())
            if isinstance(s, dict) and s.get("kind") in apps.SORT_KINDS and isinstance(s.get("why"), str)}


def _sort_unknown(record: dict, found: list, cwd: Path, claude: str, tools: list) -> tuple:
    """(sorted, unsorted, limit) for the apps no name knows (`apps.unknown`): one sorting run for those the journal's
    `apps` record has not sorted yet, so an app sorted before is not sorted again and one the probe no longer sees
    drops out. An app the person skipped (`person_skips`) is not sorted: it is neither sorted nor read again (R6). An
    app the run gave no answer for is `unsorted`: the card says it is not looked at yet, and the next run asks about
    it. The run fails into nothing (`apps.sort`), never into a failed phase; `limit` is the limit's text when the run
    stopped at the plan's limit (an answer that came with it is kept), else None (R5)."""
    before, skips = _sorted_in(record), set(_skips_in(record))
    kept = {u["app"]: before[u["app"]] for u in found if u["app"] in before}
    todo = [u for u in found if u["app"] not in kept and u["app"] not in skips]
    limits: list = []
    fresh = apps.sort(todo, cwd, claude=claude, known_tools=tools, limits=limits) if todo else {}
    sorted_ = {**kept, **fresh}
    return sorted_, [u["app"] for u in found if u["app"] not in sorted_], (limits[0] if limits else None)


def phase_apps(args) -> dict:
    """Which apps hold work, and the app card. Claude's command line is asked which apps it
    reaches (`routine.probe_tools`; a probe that fails is `failed`, never "no apps"), and every
    claude.ai connector of a known kind, or one the sorting run gave a kind, with a tool that reads
    is an app (`apps.group`). A connector no name knows (`apps.unknown`) is sorted once by a run that sees
    names only (`apps.sort`): the journal's `apps` record keeps `sorted` ({app: {kind, why}}) and `unsorted`
    (no answer yet: the next run asks again), written before any reader starts, and empties `to_sort`. A sorted
    app only reads, and one sorted `other` or `none` is not read: the card says so (`not_read`,
    `nothing_to_read`). A sort stopped at the plan's limit starts no reader (each would run on paid usage before
    it noticed): the phase waits for the limit to reset, the sort's answer kept (R5), and every app with no verdict
    yet is `unfinished`, so a skip of another app said before the reset leaves the phase waiting (R9). With no app
    to read and none left to sort, the one need is to connect one (`connect-app`, and the phase `waiting`). `--probe`
    and a dry run never sort, but apply the sort the journal keeps to the apps the probe still sees (R7, M2): `--probe` stops
    after asking Claude, having recorded the groups, the connectors that need a sign-in and `to_sort` (the apps no
    name knows not sorted yet and not skipped: research's documents step waits while there is one), and an account
    holding an app not sorted yet is not asked to connect one, while one whose apps were all sorted as holding
    nothing is. Else one reader per
    app decides whose account it is, whether it holds work and how much per month
    (`apps.analyse`): the journal's `apps` record keeps `verdicts` (with `per_month`), `card`
    (read, skip, not_signed_in, not_read, nothing_to_read, starts_after), `feeds` and `unfinished`. The state is
    `done` when every app has a verdict (an app to reconnect or one that is blocked is a need, not a stop),
    `waiting` when the plan's limit stopped the readers, or an app is unfinished or not looked at yet. A dry run only
    asks the question. `--skip <app>` asks nothing: the person's word becomes that app's verdict (`skip_apps`)."""
    if getattr(args, "skip", None):
        return skip_apps(args)
    if args.offline:
        return {"state": "done", "why": "offline: no app to read"}
    cwd = Path(tempfile.gettempdir()) if args.dry_run else apps.scratch()   # never the person's home or a repository
    claude = routine.find_claude() or "claude"
    try:
        probe = routine.probe_tools(claude, cwd)
    except routine.ProbeError as exc:
        return {"state": "failed", "detail": str(exc),
                "why": "Claude did not say which apps it can reach, so no app was read. Say the sentence again to try once more."}
    if not args.dry_run:
        apps.keep_probe(probe)   # the research beside it reads this one instead of asking again (dry run B N-4)
    tools, servers = probe.get("tools") or [], probe.get("mcp_servers") or []
    unsigned, unknown = apps.not_signed_in(servers), apps.unknown(tools, servers)
    record = setup_journal.load().get("apps")
    record = record if isinstance(record, dict) else {}
    skips, limit = set(_skips_in(record)), None
    if args.dry_run or getattr(args, "probe", False):   # the first look runs in the first two minutes: it never sorts,
        before = _sorted_in(record)                       # but the sort the journal keeps holds (R7, M2)
        kinds = {u["app"]: [before[u["app"]]["kind"]] for u in unknown
                 if u["app"] in before and before[u["app"]]["kind"] in apps.KINDS}
        groups = apps.group(tools, servers, sorted_kinds=kinds)
        to_sort = [u["app"] for u in unknown if u["app"] not in before and u["app"] not in skips]
        facts, readable = {"groups": groups, "not_signed_in": unsigned, "to_sort": to_sort}, bool(groups or to_sort)
    else:
        sorted_, unsorted, limit = _sort_unknown(record, unknown, cwd, claude, tools)
        kinds = {app: [s["kind"]] for app, s in sorted_.items() if s["kind"] in apps.KINDS}
        groups = apps.group(tools, servers, sorted_kinds=kinds)
        facts = {"groups": groups, "not_signed_in": unsigned, "sorted": sorted_, "unsorted": unsorted, "to_sort": []}
        # kept before anything reads: a skip said for a sorted app while the readers run finds it among the groups, and a
        # fault later does not lose the sort (it cost a run of Claude)
        setup_journal.set_phase("apps", "running", **facts)
        # an app not sorted yet may hold work: the card says it is not looked at yet, unless the person skipped it
        readable = bool(groups or [a for a in unsorted if a not in skips])
    found = {"apps": [g["app"] for g in groups], "not_signed_in": unsigned}
    if args.dry_run:
        return {"state": "pending", **found}
    if limit is not None:   # the sort stopped at the plan's limit: no reader starts before it resets (R5)
        have = {v["app"] for v in apps.kept(record, groups)}   # what has no verdict yet waits for the reset (R9)
        unfinished = [g["app"] for g in groups if g["app"] not in have and g["app"] not in skips]
        setup_journal.set_phase("apps", "waiting", **facts, unfinished=unfinished)
        return {"state": "waiting", **found, "why": SORT_LIMIT_WHY.format(text=f" ({limit})" if limit else "")}
    if not readable:
        setup_journal.set_phase("apps", "waiting", **facts)
        setup_journal.need("connect-app", CONNECT_NEED, APPS_CHECK, link=apps.CONNECTORS_LINK)
        return {"state": "waiting", **found, "why": NOTHING_READABLE_WHY if unknown else "No app is connected to Claude yet."}
    setup_journal.clear_need("connect-app")
    if getattr(args, "probe", False):
        setup_journal.set_phase("apps", "pending", **facts)
        return {"state": "pending", **found}
    return _read_apps(args, groups, unsigned, tools, claude, cwd, facts)


HEAD_START_LOOK = 60   # seconds between looks at the clock while a phase waits for the app card's ten minutes
_now = lambda: dt.datetime.now().astimezone()   # noqa: E731  the tests stand in for it, and for the next two
_sleep = time.sleep
_clock = time.monotonic
LIMIT_WHY = ("Your Claude limit was reached while the company was being researched{text}. Say the sentence again "
             "once it resets: what was read is kept, and only the rest is read then.")
HEAD_START_WHY = ("Your documents are read after {when}, ten minutes after you were shown which apps are read. Say "
                  "the sentence again then.")


def _company_card(args, journal: dict) -> dict:
    """The company the research is about: its name (`--company`, else the name its domain gives) and
    its domain (`--domain`, else the first of the person's own addresses that is not a mail provider's:
    the one given, the Claude sign-in's, git's). `domain_from` says which: `flag` (the person typed it),
    `address` (taken from one of their addresses, so an internet provider on no list can be the card's
    domain), `site` (the company's own site confirmed it; nothing sets that yet), "" (none). The briefing
    check trusts `flag` and `site` only. The card is what `apps.company_of` reads from the journal."""
    record = journal.get("signins") if isinstance(journal.get("signins"), dict) else {}
    who = identity(args.name, args.email, offline=True)
    emails = [e for e in dict.fromkeys([args.email, record.get("claude_email"), who["email"]]) if e]
    own = (research.clean_domain(email_domain(e)) for e in emails)
    flag = research.clean_domain(getattr(args, "domain", None))
    domain = flag or next((d for d in own if d and not research.is_provider(d)), "")
    name = args.company or (onboard.humanize(research.domain_label(domain)) if domain else "")
    return {"name": name, "domain": domain, "domains": [domain] if domain else [],
            "domain_from": "flag" if flag else "address" if domain else ""}


def _documents_apps(journal: dict) -> list:
    """The apps of the apps phase that hold documents and are read (`apps.READ`), with their read tools."""
    return [v for v in apps_verdicts(journal)
            if "documents" in (v.get("kinds") or []) and v.get("verdict") in apps.READ and v.get("read")]


APPS_FIRST = "apps"   # research's documents step waits for the apps phase's verdicts
APPS_FIRST_WHY = ("Your documents app is read once I have finished looking at your apps; the rest of the research is "
                  "done. Say the sentence again after that.")


def _documents_read_now() -> set:
    """The documents apps that are read, as the journal says now: looked up again right before a documents read."""
    return {v["app"] for v in _documents_apps(setup_journal.load())}


def _apps_judged(journal: dict) -> bool:
    """Whether the documents step may go on: the apps phase has given its verdicts, or its probe saw no documents app
    (none, or no probe at all). Research runs beside the apps phase, and a documents app the probe found has no
    verdict until the apps are read: without one it is not known whether it is read (Ruling R38, I-11). An apps phase
    stopped at a limit with a documents app among `unfinished` has not judged it either (R40, N-4); one the person
    skipped holds nothing. Nor has a first look that saw an app no name knows and that is not sorted yet (`to_sort`):
    it may hold documents (R7)."""
    record = journal.get("apps") if isinstance(journal.get("apps"), dict) else {}
    skips = set(_skips_in(record))
    to_sort = record.get("to_sort")
    if isinstance(to_sort, list) and [a for a in to_sort if str(a) not in skips]:
        return False
    docs = {g.get("app") for g in record.get("groups") or [] if isinstance(g, dict) and "documents" in (g.get("kinds") or [])}
    docs -= skips
    if "verdicts" in record:
        return not docs & set(record.get("unfinished") or [])
    return not docs


def _card_opens(journal: dict) -> dt.datetime | None:
    """When the app card has been shown for ten minutes (`starts_after`): before it no app's content is copied."""
    record = journal.get("apps") if isinstance(journal.get("apps"), dict) else {}
    try:
        when = dt.datetime.fromisoformat(record["card"]["starts_after"])
    except (KeyError, TypeError, ValueError):
        return None
    return when if when.tzinfo else when.astimezone()


EMPTY_SITE_WHY = "The company's website was looked at, but nothing usable came back; it is read again next time."


class _Empty(Exception):
    """A source that was read and gave nothing to keep: it is not done, and is read again next time (R45, Minor 14)."""


class _Reading:
    """The sources of one run of the research phase, read one after the other. A source that
    finished is remembered (`done`) and is not read again; what its readers returned goes through
    `research.clean` and into the records file; one that could not be read is noted (`unread`) and
    tried again next time. The plan's limit stops the run: `stop` holds its error."""

    def __init__(self, prior: dict):
        self.done = list(prior.get("sources_done") or [])
        self.unread: dict = {}
        self.faults: dict = {}   # the class of a fault that was ours, never its text: journal only
        old = prior.get("dropped") if isinstance(prior.get("dropped"), dict) else {}
        self.dropped = {"records": int(old.get("records") or 0), "kinds": sorted(old.get("kinds") or [])}
        self.stop = None

    def run(self, batch: list) -> None:
        for name, source in batch:
            if name in self.done or self.stop:
                continue
            try:
                found = source()
            except reader.ReaderError as exc:
                if exc.kind == "limit":
                    self.stop = exc
                else:
                    self.unread[name] = exc.text
                continue
            except routine.ProbeError:
                raise
            except _Empty as exc:   # read, and nothing came back: not done, tried again
                self.unread[name] = str(exc)
                continue
            except Exception as exc:   # a fault in one source is that source's, with a fixed sentence: its text could repeat a page
                self.unread[name], self.faults[name] = research.UNEXPECTED, type(exc).__name__
                continue
            if found is None:   # nothing there to read (no file, no note): looked at again next time
                continue
            kept, kinds = research.clean(found)
            self.dropped = {"records": self.dropped["records"] + len(found) - len(kept),
                            "kinds": sorted({*self.dropped["kinds"], *kinds})}
            research.write_records(kept)
            self.done.append(name)


def _read_company(args, card: dict, prior: dict, journal: dict, reading: _Reading, others: list) -> dict:
    """Read the company's sources that are not read yet: its site, its public register (where its
    country has one), its code organisation, the documents on this computer (not in the folders of
    `others`, the person's other companies), a few notes of Claude's, and last the documents app
    (not before the app card has been shown for ten minutes). Then one reader with no tool at all
    merges the records into the profile, every line citing a record. Returns the facts for the
    journal."""
    now, home, cwd = dt.datetime.now(dt.timezone.utc), Path.home(), research.scratch()
    claude, probed = routine.find_claude() or "claude", []
    record = journal.get("signins") if isinstance(journal.get("signins"), dict) else {}
    taken = {r.get("id") for r in research.read_records()}
    used = list(prior.get("files") or [])

    def known() -> list:   # what the probe sees: the connector tools a reader was not given are denied for its run
        if not probed:   # the apps phase's own probe, when it is fresh: one probe for both (dry run B N-4)
            probed.append(apps.recent_probe() or routine.probe_tools(claude, cwd).get("tools") or [])
        return probed[0]

    def ask(prompt: str, tools: list, schema: dict) -> dict:
        return reader.read(prompt, tools, schema, cwd, known_tools=known(), claude=claude)

    rules = research.web_rules(card["domain"])
    host = research.register_host(card["domain"])
    org = str(args.org or record.get("namespace") or "")
    own_account = bool(org) and org == record.get("github")
    sources = []

    def site() -> list:
        answer = ask(research.site_prompt(card), rules, research.WEB_SCHEMA)
        if not args.company and answer.get("name"):
            card["name"] = " ".join(str(answer["name"]).split())[:80]
        found = research.web_records(answer, "site", research.hosts_of(rules), now, taken)
        if not found:   # pages fetched and nothing kept: not read, and read again next time (R45, Minor 14)
            raise _Empty(EMPTY_SITE_WHY)
        return found

    def register() -> list:
        answer = ask(research.register_prompt(card, host), [f"WebFetch(domain:{host})"], research.WEB_SCHEMA)
        return research.web_records(answer, "register", [host], now, taken)

    def code() -> list:
        answer = ask(research.code_prompt(card, org), ["WebFetch(domain:github.com)"], research.WEB_SCHEMA)
        return research.web_records(answer, "code", ["github.com"], now, taken)

    def files() -> list | None:
        found = research.company_files(home, research.terms(card), limit=research.FILES_READ, others=others)   # the site may have named the company
        if not found:
            return None
        answer, names = reader.read_files(research.files_prompt(card), found, research.FILES_SCHEMA,
                                          known_tools=known(), claude=claude)
        used.extend(str(p) for p in names.values() if str(p) not in used)
        return research.file_records(answer, names, taken)

    def notes() -> list | None:
        return research.note_records(research.claude_notes(home, research.terms(card)), taken) or None

    if rules:
        sources.append(("site", site))
    else:
        reading.unread["site"] = "the company has no website address to read"
    if host:
        sources.append(("register", register))
    else:
        reading.unread["register"] = (f"no public business register is known for the .{card['domain'].rsplit('.', 1)[-1]} domain"
                                      if card["domain"] else "the company has no domain to look up in a register")
    if org and not own_account and re.fullmatch(r"[A-Za-z0-9-]+", org):
        sources.append(("code", code))
    else:
        reading.unread["code"] = ("the GitHub account is the person's own account, not an organisation of the company"
                                  if own_account else "no code organisation of the company is known")
    sources += [("files", files), ("notes", notes)]
    reading.run(sources)
    journal = setup_journal.load()   # the apps phase runs beside this one: its sort and verdicts as they are now (R7)
    waiting, judged = None, _apps_judged(journal)
    docs = [v for v in _documents_apps(journal) if f"documents:{v['app']}" not in reading.done]
    if not judged and not reading.stop:   # the documents app has no verdict yet: its step waits for the apps phase
        waiting = APPS_FIRST
    elif docs and not reading.stop:
        opens = _card_opens(journal)
        if opens and _now() < opens:
            if args.wait:   # stays until the app card has been shown for ten minutes
                while _now() < opens:
                    _sleep(min(HEAD_START_LOOK, max(1.0, (opens - _now()).total_seconds())))
            else:
                waiting = opens

        def documents(app: dict):
            def source() -> list | None:
                if app["app"] not in _documents_read_now():   # a skip said since: never read (R40, I-5 a)
                    return None
                answer = ask(research.documents_prompt(card, app["app"]), list(app["read"]), research.WEB_SCHEMA)
                if app["app"] not in _documents_read_now():   # skipped while it was read: what it read is dropped (R43)
                    return None
                return research.document_records(answer, app["app"], now, taken)
            return source
        if not waiting:   # the verdicts as they are after the wait: a skip said during it wins
            reading.run([(f"documents:{v['app']}", documents(v)) for v in docs if v["app"] in _documents_read_now()])
    records, merged, lines = research.read_records(), prior.get("profile_for"), prior.get("profile_lines") or 0
    if records and not reading.stop and (merged != len(records) or not research.profile_path().exists()):
        try:
            made = research.profile_lines(ask(research.merge_prompt(card, records), [], research.PROFILE_SCHEMA), records)
            research.profile_path().write_text(yaml.safe_dump(made, sort_keys=False, allow_unicode=True),
                                               encoding="utf-8", newline="\n")
            merged, lines = len(records), sum(len(v) for v in made.values())
        except reader.ReaderError as exc:
            if exc.kind == "limit":
                reading.stop = exc
            else:
                reading.unread["profile"] = exc.text
        except routine.ProbeError:
            raise
        except Exception as exc:
            reading.unread["profile"], reading.faults["profile"] = research.UNEXPECTED, type(exc).__name__
    return {"files": used, "records": len(records), "profile_for": merged, "profile_lines": lines,
            "documents_after": waiting.isoformat(timespec="seconds") if isinstance(waiting, dt.datetime) else None,
            "waiting": waiting}


def phase_research(args) -> dict:
    """Company research (phase 3): what the company's own sources say about it, as cited records
    and a profile. A colleague joining a brain skips it (`skipped: colleague`: the brain has the
    profile); offline there is nothing to read. The company is `--company` and the domain of the
    person's company mail address (or `--domain`); with neither, nothing is researched. Readers
    run one after the other, each with only what its source needs: page rules for the site, the
    public register of the domain's country, the code organisation (never the person's own GitHub
    account); the documents on this computer, through the files reader (`reader.read_files`); a few
    notes of Claude's about the company; the documents app's read tools, once the apps phase has
    given its verdicts (`_apps_judged`; until then the phase waits) and after the app card has
    been shown for ten minutes (`--wait` stays until then, else the phase waits); last a reader
    with no tools merges everything. Records (with any key or password dropped, only the kind
    journaled) go to `~/.spaces/research/records.jsonl`, the profile to `profile.yaml`, the
    company card to the journal's `research` record (`company`), which the apps phase judges
    work against. Sources that finished are not read again; the plan's limit keeps them and waits.
    The state is `done`, `waiting` (limit, or the documents' ten minutes), or `failed` when
    Claude does not say which tools it has. A dry run reads nothing and changes nothing."""
    if args.invitation or args.brain:   # a colleague: the brain already holds the company's profile (spec section 6)
        if args.dry_run:
            return {"state": "pending", "skipped": "colleague"}
        setup_journal.set_phase("research", "done", skipped="colleague")
        return {"state": "done", "skipped": "colleague"}
    if args.offline:
        return {"state": "done", "why": "offline: no site or app to read"}
    journal = setup_journal.load()
    card = _company_card(args, journal)
    if not card["name"]:
        result = {"state": "pending" if args.dry_run else "done", "skipped": "no company",
                  "why": "There is no company name or company mail address to research. Give the company's name and "
                         "say the sentence again to research it."}
        if not args.dry_run:
            setup_journal.set_phase("research", "done", skipped="no company")
        return result
    if args.dry_run:
        return {"state": "pending", "company": card}
    prior = journal.get("research") if isinstance(journal.get("research"), dict) else {}
    asked = {"company": args.company or "", "domain": card["domain"]}
    if prior.get("asked") != asked or (prior.get("sources_done") and not research.records_path().exists()):
        research.reset()   # another company than the one that was read, or what was read is gone: start over
        prior = {}
    stored = prior.get("company") if isinstance(prior.get("company"), dict) else {}
    if prior.get("asked") == asked and not args.company and stored.get("name"):
        card["name"] = stored["name"]   # a run again: the site is not read again, and its name is what the card keeps
    research.write_records([])
    reading = _Reading(prior)
    signins, apps_record = (journal.get(k) if isinstance(journal.get(k), dict) else {} for k in ("signins", "apps"))
    others = list(dict.fromkeys([*(research.other_companies()),
                                 *research.rivals(card, signins, apps_record, args.org or signins.get("namespace"))]))
    setup_journal.set_phase("research", "running", company=card, asked=asked, others=others,
                            **({"skipped": None} if (journal.get("research") or {}).get("skipped") else {}))
    failure = None
    try:
        facts = _read_company(args, card, prior, journal, reading, others)
    except routine.ProbeError as exc:
        facts, failure = {}, exc
    waiting = facts.pop("waiting", None)
    state = "failed" if failure else "waiting" if reading.stop or waiting else "done"
    setup_journal.set_phase("research", state, company=card, sources_done=reading.done, unread=reading.unread,
                            faults=reading.faults, dropped=reading.dropped, **facts)
    result = {"state": state, "company": card, "sources": reading.done, "unread": reading.unread,
              "records": facts.get("records", 0), "profile_lines": facts.get("profile_lines", 0)}
    if failure:
        result.update(detail=str(failure), why="Claude did not say which apps it can reach, so the company could not be "
                      "researched. Say the sentence again to try once more.")
    elif reading.stop:
        result["why"] = LIMIT_WHY.format(text=f" ({reading.stop.text})" if reading.stop.text else "")
    elif waiting:
        result["why"] = (APPS_FIRST_WHY if waiting == APPS_FIRST
                         else HEAD_START_WHY.format(when=f"{waiting.astimezone():%H:%M}"))
    return result


NOT_THIS_COMPANY = ("the research kept on this machine is about another company, or does not say which, so it was "
                    "not put into this brain")


def _same_company(card, brain_name) -> bool:
    """Whether the researched company (its card: name, domain) is the one a brain is named for: the
    names agree letter for letter apart from spaces and hyphens, or one begins with the other, at
    least four letters each (`lantern` for `lantern-works`; `works` is not). The same name is the same
    company whatever its length (QXR)."""
    brain = re.sub(r"[^a-z0-9]", "", str(brain_name or "").lower())
    card = card if isinstance(card, dict) else {}
    names = [re.sub(r"[^a-z0-9]", "", str(n or "").lower()) for n in (card.get("name"), research.domain_label(card.get("domain")))]
    return any(n and (n == brain or (len(n) >= 4 and len(brain) >= 4 and (n.startswith(brain) or brain.startswith(n))))
               for n in names)


def _land_research(report) -> dict:
    """What the research read, in the brain the build made: the records as witness pages (their
    claims stay on the page, unconfirmed) and the profile as a draft page with one item on Needs
    you. Only the research of this company: the company card in the journal must be the brain's
    (`_same_company`); else nothing is landed and `skipped` says why. {} when there is nothing to land."""
    brain = Path(report.spaces["brain"]) if report.spaces.get("brain") else None
    records = research.records_path()
    if brain is None or not (brain / "space.yaml").exists() or not records.exists():
        return {}
    card = (setup_journal.load().get("research") or {}).get("company")
    if not _same_company(card, spaceyaml.load(brain).name):
        return {"skipped": NOT_THIS_COMPANY}
    out = research.ingest(brain, records, as_profile=True)
    try:
        lines = yaml.safe_load(research.profile_path().read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        lines = None
    page = onboard.write_profile(brain, lines if isinstance(lines, dict) else {}, spaceyaml.load(brain).now())
    landed = {"written": len(out["written"]), "kept": len(out["kept"]), "left_out": len(out["skipped"])}
    if page:
        landed["profile"] = page.relative_to(brain).as_posix()
    return landed if landed["written"] or landed["kept"] or page else {}


def _research_line(landed: dict) -> str:
    """What the build did with the research, in a line: what it did, not what it set out to do."""
    if landed.get("error") or landed.get("skipped"):
        return landed.get("error") or landed["skipped"]
    said = [f"{landed['written']} pages written"] if landed.get("written") else []
    said += [f"{landed['kept']} already there"] if landed.get("kept") else []
    return ", ".join(said + ["profile drafted" if landed.get("profile") else "no profile drafted"])


ADDRESS = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def address_in(text) -> str:
    """The one address in a text (`ada@x.example`, `Ada <ada@x.example>`, `ada@x.example (work)`), in lower
    case; "" when there is none, or when there are several and so no telling which is meant."""
    found = {a.lower() for a in ADDRESS.findall(str(text or ""))}
    return found.pop() if len(found) == 1 else ""


def work_mailbox(journal: dict, known=(), domains=()) -> str:
    """The account of the first mail app the apps phase read as `work` (not `mixed`, not another person's),
    when that is an address the briefing may go to. The account is a reader's free-text answer, written
    after reading mail anyone can write, and the briefing address is the only one agents may mail: so it
    counts only when it is one the person is known to have (`known`, exactly), or sits on a domain of the
    company that did not come from a reader (`domains`; a mail provider's never does). "" otherwise. A mailbox
    the routines can send from (`send`) comes before one setup only reads, as it reads a sorted one (M3)."""
    found = []
    for v in apps_verdicts(journal):
        if v.get("verdict") == "work" and "mail" in (v.get("kinds") or []):
            account = address_in(v.get("account"))
            domain = account.rpartition("@")[2]
            if account and (account in known or (domain in domains and not research.is_provider(domain))):
                found.append((bool(v.get("send")), account))
    return next((account for sends, account in found if sends), found[0][1] if found else "")


def briefing_address(args, journal: dict) -> tuple:
    """Where the morning briefing goes, worked out rather than asked, and where that came from:
    the address the person gave (`asked`), else the work mailbox the apps phase found (`work-mailbox`),
    else git's (`git`: the host's when git has none), else the Claude sign-in's (`sign-in`); ("", "asked")
    when there is none, and one item asks. The work mailbox counts only as `work_mailbox` says: an address
    the person has (git's, the sign-in's, the one given), exactly, or one on a company domain that no
    reader named: `--domain`, or the researched company's card when its domain was typed (`--domain`) or
    confirmed by the site (`domain_from`), never a mail provider's. The domain of a known address is not one,
    and neither is a card domain taken from one (`domain_from` "address", or an old card without the marker):
    people share their provider with strangers, listed (hotmail.nl, fastmail.com) or not (an internet
    provider's own mail)."""
    signins = journal.get("signins") if isinstance(journal.get("signins"), dict) else {}
    research_record = journal.get("research") if isinstance(journal.get("research"), dict) else {}
    card = research_record.get("company") if isinstance(research_record.get("company"), dict) else {}
    given, signin = address_in(args.email), address_in(signins.get("claude_email"))
    host = address_in(identity(args.name, None, args.offline)["email"])
    known = {a for a in (given, host, signin) if a}
    trusted = card.get("domain_from") in ("flag", "site")   # a card without the marker is an old one: its domain came from an address
    named = [getattr(args, "domain", None), *([card.get("domain"), *(card.get("domains") or [])] if trusted else [])]
    domains = {d for d in (research.clean_domain(x) for x in named if x) if d and not research.is_provider(d)}
    if given:
        return given, "asked"
    for source, address in (("work-mailbox", work_mailbox(journal, known, domains)), ("git", host), ("sign-in", signin)):
        if address:
            return address, source
    return "", "asked"


def found_by_setup(args, journal: dict, new_company: bool) -> dict:
    """What the phases before the build found, as the arguments `start` takes: the researched
    company's name (a new company's), the briefing address (`briefing_address`) and the feeds of the
    apps that are read. What the person said on the command line wins. `connected` are the feeds the
    apps phase has read, so no row asks the person to connect them again."""
    apps_record = journal.get("apps") if isinstance(journal.get("apps"), dict) else {}
    card = (journal.get("research") or {}).get("company") if isinstance(journal.get("research"), dict) else None
    card_name = " ".join(str(card.get("name") or "").split()) if isinstance(card, dict) and new_company else ""
    verdicts = [v for v in apps_verdicts(journal) if isinstance(v.get("kinds"), list) and "verdict" in v]
    found = apps.feeds(verdicts) if verdicts else str(apps_record.get("feeds") or "")   # the person's word applied
    address, source = briefing_address(args, journal)
    return {"company": args.company or card_name or None, "email": address or None, "email_source": source,
            "feeds": args.feeds or found or None,
            "connected": [f.partition(":")[0].strip() for f in found.split(",") if f.strip()]}


def keep_findings(report, journal: dict, briefing=("", "asked")) -> None:
    """What the build leaves for later, outside every tracked file: the interview's defaults in the
    setup journal's `settings` (the progress page shows them), the apps' verdicts without their counts
    in the workspace's private zone, which the sync never stages and where the routines read the tools
    they may use, and in the build's record the briefing address with where it came from: `work-mailbox`,
    `git`, `sign-in` or `asked` when this run's choice is the address there; `invitation` when it is the one
    a colleague's invitation holds; what an earlier run recorded for it when that run set it; `kept` for one
    nothing here set (a hand edit). `asked` with no address is the item waiting for the person."""
    settings = getattr(report, "settings", None)
    if settings is not None:
        data = setup_journal.load()
        data["settings"] = settings
        setup_journal.save(data)
    workspace = getattr(report, "workspace", None)
    verdicts = [{k: v for k, v in verdict.items() if k != "per_month"} for verdict in apps_verdicts(journal)]
    if workspace and verdicts:
        onboard.write_yaml(Path(workspace) / "_meta" / "state" / "private" / "apps.yaml", {"apps": verdicts})
    if workspace:
        address, source = briefing
        actual = spaceyaml.load(workspace).owner_email
        earlier = journal.get("build") if isinstance(journal.get("build"), dict) else {}

        def same(a, b) -> bool:   # addresses are the same without regard to case: an invitation holds one as it was typed
            return str(a or "").lower() == str(b or "").lower()
        if same(actual, address):
            real = source
        elif actual and same(actual, getattr(report, "invited", "")):
            real = "invitation"
        elif actual and same(actual, earlier.get("owner_email")) and earlier.get("owner_email_source"):
            real = earlier["owner_email_source"]
        else:
            real = "kept" if actual else "asked"
        setup_journal.set_phase("build", "running", owner_email=actual, owner_email_source=real)


# ----- the routines prove they run on their own --------------------------------------------
PROBE_WAIT = routine.PROBE_SECONDS + 60   # seconds the build waits for each probe's result: longer than its own claude may take
PROBE_LOOK = 5                       # seconds between looks at the probe's file
PROBE_TIME = (3, 33)                 # the one-shot task's time of day: it is started at once and removed after
PROBE_STEP = "routines run on their own"   # begins "routine": the progress page files it under Routines
PROBE_REFUSED = ("The computer's scheduler would not start the check that proves your routines run on their own. "
                 "Say the sentence again; if it happens again, restart the computer first.")
PROBE_SILENT = ("The check that proves your routines run on their own did not report back within {minutes} minutes. "
                "Say the sentence again.")
PROBE_ERROR = ("Claude's command line could not be asked what your routines may use, so they were not proved to "
               "run on their own. Say the sentence again once Claude works in a terminal.")
PROBE_BUSY = ("A routine was running, so the check that proves your routines run on their own could not start. "
              "Say the sentence again once it is done; a routine runs an hour at most.")
PROBE_STARTING = ("Another setup is running on this computer, so the check that proves your routines run on their own "
                  "could not start. Try again once it is done.")
PROBE_FAILED = ("The check that proves your routines run on their own found something that does not work yet, so they "
                "were not proved. Your agent has the details; say the sentence again once it is put right.")
PROBE_NOTHING = "The check that proves your routines run on their own reported nothing. Say the sentence again."
PROBE_UNSCHEDULED = ("The check that proves your routines run on their own could not be scheduled on this computer. Say "
                     "the sentence again.")
# What the build may say of the proof as it is; any other line is PROBE_FAILED, its words the journal's detail (Minor 3).
PROBE_PLAIN = (PROBE_REFUSED, PROBE_SILENT.format(minutes=PROBE_WAIT // 60), PROBE_ERROR, PROBE_BUSY, PROBE_STARTING,
               PROBE_FAILED, PROBE_NOTHING, PROBE_UNSCHEDULED)


def _probe_result(started: float) -> dict | None:
    """The probe's result when one was written since `started` (its checks, or why it could not run)."""
    data = routine.read_probe()
    stamp = data.get("stamp")
    if not (isinstance(stamp, (int, float)) and stamp >= started and ("checks" in data or data.get("error"))):
        return None
    checks = [c for c in data.get("checks") or [] if isinstance(c, dict)]
    return {"ok": data.get("ok") is True and not data.get("error") and all(c.get("ok") is True for c in checks),
            "checks": checks, "notes": [str(n) for n in data.get("notes") or []], "error": data.get("error"),
            "busy": data.get("busy") is True}


def _probe_once(record: dict) -> dict:
    """Start the probe task at once and wait for its result, up to PROBE_WAIT; a result with `why` when the
    scheduler would not start it or it never reported."""
    started = time.time()
    if not onboard.run_now(record):
        return {"ok": False, "checks": [], "notes": [], "why": PROBE_REFUSED}
    deadline = _clock() + PROBE_WAIT
    while True:
        seen = _probe_result(started)
        if seen is not None:
            return seen
        if _clock() >= deadline:
            return {"ok": False, "checks": [], "notes": [], "why": PROBE_SILENT.format(minutes=PROBE_WAIT // 60)}
        _sleep(PROBE_LOOK)


def prove_routines(report, workspace) -> dict:
    """That the routines run on their own, as the scheduler starts them: a one-shot task
    (`<prefix>probe-<name>`) runs `routine.py --probe`, which looks at what such a run sees and checks it
    (`routine.probe`). The first probe gives the tools the allowlist is written from (`write_allowlist`);
    the second must pass every check with that allowlist in place (the first may have read an older
    one). When the first did not report, or could not ask Claude, there is no second. The task is
    removed whatever happened, and it is recorded in onboard.yaml before it is registered
    (`onboard.record_probe_task`): a build stopped before it removed the task leaves the record, by which the
    next build (first thing here), `onboard --check` and offboard remove it (`offboard.remove_probe_task`).
    It holds this computer's start lock while it runs (the build's own, which `run_phase` holds, else `take_lock`),
    so `onboard --check` leaves its probe task alone and no second start probes at the same time; with another start
    running it proves nothing and says so. Each failed check is one step that names it; the result is {"ok",
    "checks", "notes", "why", "detail"}: `why` is plain (PROBE_PLAIN), the failed checks' own words are `detail`."""
    workspace = Path(workspace).resolve()
    check = f'{onboard.python_cmd()} "{workspace.as_posix()}/_meta/tools/routine.py" --probe "{workspace.as_posix()}"'
    lock, own = _hold_lock()
    if lock is None:
        report.add(PROBE_STEP, False, check, PROBE_STARTING)
        return {"ok": False, "checks": [], "notes": [], "why": PROBE_STARTING}
    try:
        return _prove(report, workspace, check)
    finally:
        if own:
            lock.unlink(missing_ok=True)


def _prove(report, workspace: Path, check: str) -> dict:
    name = spaceyaml.load(workspace).name
    task = f"{onboard.task_prefix()}probe-{name}"
    hour, minute = PROBE_TIME
    offboard.remove_probe_task(report, workspace)   # one an earlier build was stopped before it removed
    record = {"name": task}
    onboard.record_probe_task(workspace, record)
    try:
        record = onboard.register_task(report, f"routine probe task {task}", task, workspace,
                                       [workspace / "_meta" / "tools" / "routine.py", "--probe", workspace],
                                       times=[PROBE_TIME], cron=f"{minute} {hour} * * *")
        onboard.record_probe_task(workspace, record)   # how this scheduler names it, for a later removal
        if not report.steps or not report.steps[-1].done:
            return {"ok": False, "checks": [], "notes": [], "why": PROBE_UNSCHEDULED}
        seen = _probe_once(record)
        if not (seen.get("why") or seen.get("error")):
            routine.write_allowlist(workspace)
            seen = _probe_once(record)
    finally:
        offboard.remove_sync_task(report, workspace, {"routine_tasks": [record]}, task, False,
                                  label=f"routine probe task {task} removed")
        if report.steps and report.steps[-1].done:
            onboard.forget_probe_task(workspace)
    if seen.get("busy"):
        seen["why"] = PROBE_BUSY
        report.add(PROBE_STEP, False, check, PROBE_BUSY)
    elif seen.get("error"):
        seen["why"] = PROBE_ERROR
        report.add(PROBE_STEP, False, check, PROBE_ERROR)
    elif seen.get("why"):
        report.add(PROBE_STEP, False, check, seen["why"])
    else:
        failed = [c for c in seen["checks"] if c.get("ok") is not True]
        for c in failed:
            report.add(f"routine check: {c.get('name')}", False, check, str(c.get("why") or "it did not pass"))
        if not failed and not seen["checks"]:
            report.add(PROBE_STEP, False, check, "the check reported nothing")
            seen["why"] = PROBE_NOTHING
        elif failed:   # plain words for the person; the checks' own for the journal (R45, Minor 3)
            seen["why"] = PROBE_FAILED
            seen["detail"] = " ".join(f"{c.get('name')}: {c.get('why')}." for c in failed)
        else:
            report.add(PROBE_STEP, True, check, "proved twice, as the scheduler starts them")
    seen["ok"] = bool(seen.get("ok")) and not seen.get("why")
    return {k: seen.get(k) for k in ("ok", "checks", "notes", "why", "detail")}


TASK_PREFIX_WHY = ("This computer is set to name the routines' scheduled tasks in a way that is not ours, so nothing was "
                   "created. Your agent has the details.")


def phase_build(args) -> dict:
    """Everything start did before there were phases: the spaces, the personal instruction, the
    routines. It builds from what the earlier phases left in the journal (`found_by_setup`) and
    schedules the routines itself (`finish_workspace`): the agent has no step left. Its state is
    `failed` when a step did not work (a dry run included), `pending` for a dry run that did
    (nothing was created), `waiting` when it stopped for the person or left rows only they can do
    (say the sentence again after; the briefing address is one when no source has it), else `done`. While the namespace question of the signins phase
    is open it refuses to start (`waiting`, and why); `--org <name>` answers it: the name is
    remembered and the question closed. The account the signins phase settled on is used when no
    `--org` is given. A task-name prefix that is not ours (`SPACES_TASK_PREFIX`) fails it before
    anything is created (TASK_PREFIX_WHY; the setting's own words are the journal's `detail`). The report is kept
    on `LAST_REPORT`."""
    global LAST_REPORT
    try:
        onboard.task_prefix()
    except ValueError as exc:
        if not args.json:
            print(f"build: failed: {TASK_PREFIX_WHY}")
        return {"phase": "build", "state": "failed", "why": TASK_PREFIX_WHY, "detail": str(exc)}
    if args.email and not address_in(args.email):
        why = (f'"{" ".join(str(args.email).split())[:80]}" is not an email address, or holds more than one: give the '
               "one address your morning briefing should go to.")
        if not args.json:
            print(f"build: failed: {why}")
        return {"phase": "build", "state": "failed", "why": why}
    new_company = not (args.invitation or args.brain)   # the question is a new company's; a colleague's workspace has its own place
    if new_company and args.org and not args.dry_run:
        record_namespace(args.org)
    asked = [n for n in setup_journal.open_needs(setup_journal.load()) if n.get("kind") == "namespace"]
    if new_company and asked and not args.org:
        why = f"{asked[0].get('text')} Nothing was created."
        if not args.json:
            print(f"build: waiting: {why}")
            say_needs(asked)
        return {"phase": "build", "state": "waiting", "stopped": True, "why": why}
    org = args.org or (_signins_record().get("namespace") if new_company else None)
    journal = setup_journal.load()
    found = found_by_setup(args, journal, new_company)
    report = start(HERE, invitation=args.invitation, brain=args.brain, company=found["company"], name=args.name,
                   email=found["email"], feeds=found["feeds"], org=org, home=args.home, template=args.template,
                   offline=args.offline, dry_run=args.dry_run, user_instructions=args.user_instructions,
                   connected=found["connected"])
    LAST_REPORT = report
    if args.email and getattr(report, "workspace", None):   # what they typed is their choice: it replaces an address worked out before
        fill_address(report, spaceyaml.load(report.workspace).owner, address_in(args.email), replace=True)
    if not args.dry_run:
        keep_findings(report, journal, (found["email"] or "", found["email_source"]))
    probe = elsewhere = None
    if not (args.offline or args.dry_run or report.failed()) and getattr(report, "workspace", None):
        elsewhere = onboard.routines_elsewhere(report.workspace)   # another computer, or the app, runs them
    if elsewhere is None and not (args.offline or args.dry_run or report.failed()) and getattr(report, "workspace", None):
        probe = prove_routines(report, report.workspace)   # after keep_findings: the grant is read from the verdicts
        notes = probe.get("notes") or []
        if notes or (setup_journal.load().get("build") or {}).get("routines_note"):
            setup_journal.set_phase("build", "running", routines_note=notes[0] if notes else None)
    result = summary(report)
    result["probe"] = probe
    if elsewhere is not None:   # nothing was proved here: the built message says where they run instead
        result["routines_where"] = elsewhere.result
    if new_company and not args.dry_run:   # the research of phase 3 goes into the brain just made
        try:
            landed = _land_research(report)
        except (OSError, ValueError, yaml.YAMLError):
            landed = {"error": "what was read about the company could not be put into the brain yet"}
        if landed:
            result["research"] = landed
        note = landed.get("skipped")
        if note or (setup_journal.load().get("build") or {}).get("research_skipped"):   # the journal says what was left out, and forgets it when it is not
            setup_journal.set_phase("build", "running", research_skipped=note)
    if not args.json:
        onboard.print_steps(report.steps)
        if result["workspace"]:
            print(f"\nworkspace: {result['workspace']}")
        if result.get("research"):
            print("research: " + _research_line(result["research"]))
        for r in result["routines"]:
            print(f"routine {r['name']} ({r['schedule']}, {r['timezone']} time): {r['prompt_file']}")
        for line in result["plan"]:
            print(f"plan: {line}")
        if result["page"]:
            print(f"page: {result['page']}")
        for row in result["yours"]:
            print(f"yours: {row.get('name')}: {row.get('result')} (check: {row.get('check')})")
        for note in (probe or {}).get("notes") or []:
            print(f"routines: {note}")
        if result.get("routines_where"):
            print(f"routines: {result['routines_where']}")
        if result["failed"] or result["yours"]:
            print("then say the sentence again: every step already done is kept")
    state = ("failed" if result["failed"] else "pending" if args.dry_run
             else "waiting" if result["stopped"] or result["yours"] else "done")
    if probe and probe.get("why"):   # plain words for the person, anything else in the journal only (R45, Minor 3)
        plain = probe["why"] in PROBE_PLAIN
        result["why"] = probe["why"] if plain else PROBE_FAILED
        detail = probe.get("detail") or (None if plain else probe["why"])
        if detail:
            result["detail"] = detail
    return {"phase": "build", "state": state, **result}


# ----- the history, read oldest first in paced parts -----------------------------------------
HISTORY_LOOK = 30         # seconds between looks at a part while `--wait-part` waits for it
HISTORY_WAIT = 3600       # the most it waits: a routine run is stopped after an hour
PAID_WHY = "Your Claude account is billed by use, so your history is read only when you say go."
NO_WORKSPACE = ("Your workspace was not found on this computer, so there is no history to read yet. Finish the "
                "build first.")


def history_counts(journal: dict) -> dict:
    """Records a month in the apps that are read (`apps.READ`): the journal's verdicts with the person's word applied
    (`apps_verdicts`), `per_month` summed."""
    counts: dict = {}
    for verdict in apps_verdicts(journal):
        if verdict.get("verdict") in apps.READ and isinstance(verdict.get("per_month"), dict):
            for month, number in verdict["per_month"].items():
                if isinstance(number, int) and not isinstance(number, bool) and number > 0:
                    counts[str(month)] = counts.get(str(month), 0) + number
    return counts


def _history_workspace(args, journal: dict) -> Path | None:
    """The person's workspace: the one `--workspace` names, else the one this process just built, else the one the
    build's journal record keeps (a build in another process; R45, F-3), else the personal workspace on the
    machine's list of spaces (the person's own, when there are several)."""
    if getattr(args, "workspace", None):
        return Path(args.workspace)
    if getattr(LAST_REPORT, "workspace", None):
        return Path(LAST_REPORT.workspace)
    built = journal.get("build") if isinstance(journal.get("build"), dict) else {}
    if built.get("workspace") and (Path(str(built["workspace"])) / "space.yaml").exists():
        return Path(str(built["workspace"]))
    try:
        rows = (yaml.safe_load((Path.home() / onboard.MACHINE_SPACES).read_text(encoding="utf-8")) or {}).get("spaces") or []
    except (OSError, yaml.YAMLError, AttributeError):
        rows = []
    mine = [r for r in rows if isinstance(r, dict) and r.get("profile") == "personal"
            and (Path(str(r.get("root"))) / "space.yaml").exists()]
    if len(mine) > 1:
        name = identity(args.name, args.email, offline=True)["name"]
        mine = [r for r in mine if str(r.get("owner")).lower() == name.lower()]
    return Path(str(mine[0]["root"])) if len(mine) == 1 else None


def _sweep_task(workspace: Path) -> dict | None:
    """The record of the workspace's sweep-chain task, the one that reads a part of the history."""
    try:
        want = f"{onboard.task_prefix()}sweep-chain-{spaceyaml.load(workspace).name}"
    except (ValueError, FileNotFoundError):
        return None
    state = onboard.read_yaml(Path(workspace) / "_meta" / "state" / "onboard.yaml", {}) or {}
    return next((r for r in state.get("routine_tasks") or [] if isinstance(r, dict) and r.get("name") == want), None)


def _stopped_since(workspace: Path, since) -> str | None:
    """The outcome of the newest run routine.py itself kept (`backfill.meter_rows`, never a line in runs.jsonl, where a
    routine's agent can append its own; R43, O-3), dated after `since`, when it stopped (`stopped`: it did not start,
    or ended with no result) or was held by a pause of the routines (`paused`): nothing is reading, so waiting on it
    is no use. None otherwise."""
    import backfill
    rows = backfill.meter_rows(workspace, _now())
    if rows and rows[-1]["at"] > since and rows[-1].get("outcome") in ("stopped", "paused"):
        return rows[-1]["outcome"]
    return None


STOPPED_RUN_WHY = "The run stopped before it read anything; the next routine run tries again."
PAUSE_OVER_WHY = "Your routines were paused when the run was due; the next routine run reads it."


def _paused_why(workspace: Path, which: str) -> str:
    """Why a run was held by a pause: until when the routines are paused, as the person said it (R45, Minor 13)."""
    record = routine.host(workspace)
    until = routine.paused_until(record if isinstance(record, dict) else {}, _now())
    if until is None:
        return PAUSE_OVER_WHY
    number = which[1:] if which[:1] == "p" and which[1:].isdigit() else which
    return (f"Your routines are paused until {onboarding_page.words(until, _now())}; part {number} is read once they "
            "run again.")


ACCOUNT_SECONDS = 120   # two readings of one account's window end this close at most (R38; never observed live)


def usage_account(workspace: Path, reading: dict, state: dict, now: dt.datetime | None = None) -> str | None:
    """Whether the desktop app's usage reading is the routines' account (cli-facts section 8: nothing names the account
    `get_usage` reads, so the windows decide). The newest meter row routine.py wrote since the plan was made
    (`backfill.meter_rows`, never a run line an agent can write; R40, N-1), of a run that started, is compared: `same`
    when its `limits` end both windows within ACCOUNT_SECONDS of the reading's, else `different` (other windows, or no
    limits at all). None while nothing can be compared: no such run yet, the reading names no reset times, or the
    run's 5-hour window had ended before the reading was taken."""
    import backfill
    since = backfill._dt(state.get("started"))
    row = next((r for r in reversed(backfill.meter_rows(workspace, now or _now()))
                if since and r["at"] >= since and r.get("outcome") not in ("stopped", "paused")), None)   # a held run never started
    if row is None:
        return None
    limits = row.get("limits") if isinstance(row.get("limits"), dict) else {}
    theirs = [backfill._dt((limits.get(k) or {}).get("resets_at")) if isinstance(limits.get(k), dict) else None
              for k in ("five_hour", "seven_day")]
    ours = [backfill._dt(reading.get(k)) for k in ("five_hour_resets", "week_resets")]
    taken = backfill._dt(reading.get("at"))
    if None in ours:
        return None
    if theirs[0] and taken and theirs[0] <= taken:
        return None
    if None in theirs:
        return "different"
    return "same" if all(abs((a - b).total_seconds()) <= ACCOUNT_SECONDS for a, b in zip(ours, theirs)) else "different"


def _wait_part(workspace: Path, which: str) -> dict:
    """Stay until part `which` is done or paused: at most HISTORY_WAIT seconds, a look every HISTORY_LOOK. It does
    not stay when nothing can start it (held for the person, no part due and no run going) or when the newest run
    stopped without reading."""
    import backfill
    deadline, since = _clock() + HISTORY_WAIT, _now()
    while True:
        state = backfill.load(workspace)
        part = next((p for p in state.get("parts") or [] if isinstance(p, dict) and p.get("id") == which), None)
        if part is None:
            return {"part": which, "status": "missing", "why": f"There is no part {which} in this history."}
        if part.get("status") in ("done", "paused"):
            return {"part": which, "status": part["status"], "why": part.get("why") or ""}
        coming, why = backfill.explain(workspace, _now())
        if (state.get("waiting") or coming is None) and not routine.locked(workspace):
            return {"part": which, "status": part.get("status"), "why": why}
        stopped = _stopped_since(workspace, since)
        if stopped and not routine.locked(workspace):
            return {"part": which, "status": part.get("status"),
                    "why": _paused_why(workspace, which) if stopped == "paused" else STOPPED_RUN_WHY}
        if _clock() >= deadline:
            return {"part": which, "status": part.get("status"),
                    "why": "It had not finished within the hour; it goes on with the routines."}
        _sleep(HISTORY_LOOK)


def phase_history(args) -> dict:
    """The history, oldest first, in paced parts (`backfill.py`). It cuts the window (90 days) into parts from the
    counts of the apps read as work (the journal's `per_month`), keeps the usage reading given as `--reading
    five_hour=<pct>,week=<pct>,five_hour_resets=<iso>,week_resets=<iso>,extra=<0|1>` (the window shrinks, in
    whole weeks, when the reading says it cannot be read by the next morning), and starts part 1 now as the sweep
    chain task, unless the pacing leaves no room right now or a run is going. A paid account (Claude signed in
    with a key, or a reading of `not_applicable`) gets the plan and no part: the person starts the read. It
    returns `parts`, `current_by` and `counts`; `state` is `running` while the routines read the rest, `waiting`
    for the person (a paid account, a sign-in that ran out), `done` once every part is read. `--wait-part p1`
    stays until that part is done or paused (an hour at most). Run again, it changes nothing that is right: the plan is
    held only in the call that makes it (the person's go, `backfill.py start`, stays), and it is made again only while
    no part has run and the counts have changed. A progress file that cannot be read waits with a plain line.
    The reading is the desktop app's, which may be another account than the routines' (cli-facts section 8): it is
    kept unconfirmed (paced as if extra usage were on) until a reading given after a run read part 1 compares its
    windows (`usage_account`); the journal's `usage_account` then says `same` (readings pace as the routines') or
    `different` (they are no longer kept: backfill paces from the routines' own meter, `backfill.meter_rows`).
    Once part 1 has been started the journal's history says `in_background`: the setup counts as finished.
    On a computer that does not run the routines (`backfill.runs_elsewhere`) it writes nothing: the history's notes
    are kept where the routines run, and the phase is done here (R45, I-1)."""
    import backfill
    journal, now = setup_journal.load(), _now()
    counts = history_counts(journal)
    workspace = _history_workspace(args, journal)
    if workspace is None:
        return {"phase": "history", "state": "failed", "why": NO_WORKSPACE}
    if args.dry_run:
        return {"phase": "history", "state": "pending", "counts": counts, "why": "dry run: nothing is planned"}
    elsewhere = backfill.runs_elsewhere(workspace)
    if elsewhere:   # two computers writing the history's notes would stop this one's sync
        return {"phase": "history", "state": "done", "counts": counts, "why": backfill.ELSEWHERE_WHY.format(name=elsewhere)}
    text = (getattr(args, "reading", None) or "").strip()
    signins = journal.get("signins") if isinstance(journal.get("signins"), dict) else {}
    paid, reading = signins.get("claude") == "api-key" or text == "not_applicable", None
    try:
        reading = None if text == "not_applicable" else backfill.parse_reading(text, now)
    except ValueError as exc:
        return {"phase": "history", "state": "failed", "why": f"That is not a usage reading: {exc}"}
    state = backfill.load(workspace)
    if state.get("broken"):
        return {"phase": "history", "state": "waiting", "why": backfill.BROKEN_WHY, "counts": counts}
    account = journal.get("usage_account")
    if reading is not None and account not in ("same", "different"):
        account = usage_account(workspace, reading, state, now)
        if account:
            data = setup_journal.load()
            data["usage_account"] = account
            setup_journal.save(data)
    if reading is not None:   # the desktop app's reading paces as the routines' only once their windows agreed (R38)
        reading = None if account == "different" else {**reading, "confirmed": account == "same"}
    again = (bool(state.get("parts")) and isinstance(state.get("counts"), dict) and state["counts"] != counts
             and not backfill.touched(workspace) and not routine.locked(workspace))   # the counts came in, or changed
    if not state.get("parts") or again:
        backfill.plan(workspace, counts, now, reading=reading, hold=PAID_WHY if paid else None)
    elif reading:
        backfill.record_reading(workspace, reading)
    why = ""
    part, reason = backfill.explain(workspace, now)
    first = (backfill.load(workspace).get("parts") or [{}])[0]
    fresh = first.get("status") == "pending" and not backfill.touched(workspace)
    if part is not None and fresh and part["id"] == first.get("id") and not (args.wait_part and not text):
        task = _sweep_task(workspace)
        if routine.locked(workspace):
            why = "A routine run is going; it reads the first part."
        elif task is None:
            why = "The routines are not scheduled yet, so the first part is read at the next routine run."
        elif not onboard.run_now(task):
            why = "The scheduler did not start the first part now; it is read at the next routine run."
        else:   # from here a resumed setup shows where the read stands instead of starting phase 5 again
            setup_journal.set_phase("history", "running", part1_started=now.isoformat(timespec="seconds"),
                                    in_background=True)   # the setup counts as finished from here (R40, N-5)
    elif part is None:
        why = reason
    result = {"phase": "history"}
    if account:
        result["usage_account"] = account
    if args.wait_part:
        result["waited"] = _wait_part(workspace, args.wait_part)
        if args.wait_part == "p1" and result["waited"].get("status") in ("done", "paused"):   # shown to the person: a resume knows
            setup_journal.set_phase("history", "running", part1_waited=result["waited"]["status"])
    state = backfill.load(workspace)
    waiting = state.get("waiting") or next((p.get("why") for p in state.get("parts") or []
                                           if p.get("status") == "paused" and (p.get("runs") or [{}])[-1].get("outcome") == "auth"), None)
    result.update(state="done" if not backfill.active(workspace) else "waiting" if waiting else "running",
                  parts=state.get("parts") or [], current_by=state.get("current_by"), counts=counts,
                  window_days=state.get("window_days"))
    if state.get("shortened_because"):
        result["shortened_because"] = state["shortened_because"]
    if result["state"] == "done":
        result["why"] = "There is nothing left to read." if not counts else "The history is read."
    elif waiting:
        result["why"] = str(waiting)
    elif why:
        result["why"] = why
    return result


def run_phase(args) -> dict:
    """One phase, journaled: `running` while it works, then the state it returns (when it names
    none: `waiting` while the person is still needed, else `done`), with its plain `why`, whether it ran `offline`
    and the `detail` that is not the person's to read (the tools' own words: the answer does not carry it), and for a
    build the `workspace` it made (R45, F-3 and F-6). One that raises is left `failed`, not `running`, with FAULT_WHY
    and the fault's class as its detail. A build holds this computer's start lock from before it is marked running
    until its state is written (F-5): one that cannot take it writes nothing and answers the state the journal holds,
    `busy`, with BUSY_WHY. Once a build's state is written its page is written again, so it never shows the build
    running after it finished (F-4). A dry run changes nothing, the journal included."""
    global _HELD
    name, journal = args.phase, not args.dry_run
    offline, skip = bool(getattr(args, "offline", False)), name == "apps" and bool(getattr(args, "skip", None))
    held, before = None, LAST_REPORT
    if name == "build" and journal:
        held = take_lock()
        if held is None:   # another start builds now: the build's state is its own (R45, F-5)
            record = setup_journal.load().get("build")
            state = record.get("state") if isinstance(record, dict) else None
            return {"phase": name, "state": str(state or "pending"), "busy": True, "why": BUSY_WHY}
        _HELD = held
    try:
        if journal and not skip:   # a skip leaves the state alone (R40), and the record too
            setup_journal.set_phase(name, "running")
        try:
            result = globals()[f"phase_{name}"](args)   # looked up now, so a test can stand in for one
        except Exception as exc:
            if journal:
                setup_journal.set_phase(name, "failed", why=FAULT_WHY, detail=type(exc).__name__, offline=offline)
            raise
        result.setdefault("phase", name)
        if "state" not in result:
            result["state"] = "waiting" if setup_journal.open_needs(setup_journal.load()) else "done"
        detail = result.pop("detail", None)
        if journal:
            facts = {} if skip else {"why": result.get("why"), "offline": offline, "detail": detail}
            if name == "build" and result.get("workspace"):
                facts["workspace"] = str(result["workspace"])
            setup_journal.set_phase(name, result["state"], **facts)
            report = LAST_REPORT
            if name == "build" and report is not None and report is not before and report.progress is not None:
                report.page = report.progress.finish(report)   # the page as the journal now says: not building (F-4)
        return result
    finally:
        if held is not None:
            _HELD = None
            held.unlink(missing_ok=True)


def say_needs(needs: list) -> None:
    """What only the person can do, a line each, with the check that proves it."""
    for n in needs:
        more = ", ".join(f"{k} {n[k]}" for k in ("code", "link") if n.get(k))
        print(f"yours: {n.get('kind')}: {n.get('text')}" + (f" ({more})" if more else "")
              + f" (check: {n.get('check')})")


def _history_going(args, data: dict) -> dict | None:
    """Part 1 of the history has been started (the history phase started it, or a run began or read it): the rest is
    the routines' work for days, not a phase to run again. Part 1's status, why, reset time and `current_by`; None
    before then, for a plan held for the person, or without a workspace or a plan."""
    import backfill
    record = data.get("history") if isinstance(data.get("history"), dict) else {}
    workspace = _history_workspace(args, data) if record else None
    state = backfill.load(workspace) if workspace else {}
    first = (state.get("parts") or [None])[0] if isinstance(state.get("parts"), list) else None
    if not isinstance(first, dict) or state.get("waiting"):
        return None
    if not (record.get("part1_started") or backfill.touched(workspace) or first.get("status") != "pending"):
        return None
    return {"status": first.get("status"), "why": first.get("why") or "", "resets_at": first.get("resets_at"),
            "current_by": state.get("current_by")}


def resume(args) -> int:
    """Where the setup stands after a closed session or a day: the first phase that is not done,
    what waits for the person, every phase's record. It runs nothing; the agent runs the phase.
    Once part 1 of the history has been started, the history runs on in the background: it is not
    `next`, and its record says so (`in_background`, `part1`). A build the journal says is running while no live
    start holds the start lock stopped with its process: its record says so (`stopped`, STOPPED_WHY), and saying
    the sentence again carries on (R45, F-9). It writes nothing about that."""
    data = setup_journal.load()
    result = {"next": setup_journal.next_phase(data), "needs": setup_journal.open_needs(data),
              "phases": {p: data.get(p) or {"state": "pending"} for p in PHASES}}
    build = result["phases"].get("build") if isinstance(result["phases"].get("build"), dict) else {}
    if build.get("state") == "running":
        lock = take_lock()   # the lock's own liveness test: taken only when no live start holds it
        if lock is not None:
            lock.unlink(missing_ok=True)
            result["phases"]["build"] = {**build, "stopped": True, "why": STOPPED_WHY}
    going = _history_going(args, data) if result["next"] == "history" else None
    if going:
        if not (data.get("history") or {}).get("in_background"):   # started by a routine run: marked now (R40, N-5)
            setup_journal.set_phase("history", str((data.get("history") or {}).get("state") or "running"), in_background=True)
        result["next"] = None
        result["phases"]["history"] = {**result["phases"]["history"], "in_background": True, "part1": going}
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    for phase, record in result["phases"].items():
        if record.get("in_background"):
            print(f"{phase}: running in the background, part 1 {record['part1']['status']}")
            continue
        if record.get("stopped"):
            print(f"{phase}: stopped before it finished; say the sentence again to carry on")
            continue
        print(f"{phase}: {record.get('state', 'pending')}")
    print(f"next: {result['next']}" if result["next"] else "every phase is done")
    say_needs(result["needs"])
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("phase", choices=[*PHASES, "resume"],
                    help="the phase to run, or resume to see where the setup stands (without one: build)")
    ap.add_argument("--invitation", help="<brain repo>:<slug>, from the invitation sentence")
    ap.add_argument("--brain", help="the company's brain repository (owner/name, a URL or a local path)")
    ap.add_argument("--company", help="no brain yet: the company's name (default: the researched company's, "
                    "else the organization's)")
    ap.add_argument("--domain", help="research: the company's website domain, when it is not the mail address's")
    ap.add_argument("--name")
    ap.add_argument("--email", help="the briefing address (default: the work mailbox the apps step found, else git's, else "
                    "the Claude sign-in's)")
    ap.add_argument("--feeds", help="the connectors to read: mail, calendar, chat, calls, tracker (default: "
                    "the apps the apps step read)")
    ap.add_argument("--org")
    ap.add_argument("--home", help="where the spaces are cloned (default ~/spaces)")
    ap.add_argument("--template", help="the template repository (default: where this copy came from)")
    ap.add_argument("--user-instructions", help="the user-level instruction file (default ~/.claude/CLAUDE.md)")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--doctor", action="store_true", help="only check this machine and fix what needs no password")
    ap.add_argument("--probe", action="store_true",
                    help="apps: stop after asking Claude which apps it reaches (no content is read)")
    ap.add_argument("--skip", action="append", metavar="APP",
                    help="apps: the person said skip this app; it is not read (its verdict is `skip`)")
    ap.add_argument("--reading", help="history: the usage tool's reading, five_hour=<pct>,week=<pct>,five_hour_resets="
                    "<iso>,week_resets=<iso>,extra=<0|1>,confirmed=0; not_applicable for a paid account")
    ap.add_argument("--wait-part", dest="wait_part", metavar="PART",
                    help="history: stay until this part (p1) is done or paused, an hour at most")
    ap.add_argument("--workspace", help="history: the person's workspace (default: the one just built, else the "
                    "personal one on this machine's list of spaces)")
    ap.add_argument("--wait", action="store_true",
                    help="a phase that waits on the person stays until what they must do is done")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    return ap


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-"):
        argv.insert(0, "build")   # before the phases the flags stood alone: start.py --company X
    args = parser().parse_args(argv)
    if args.doctor:
        report = onboard.Report(case="doctor")
        target = parse_invitation(args.invitation)[0] if args.invitation else args.brain
        ok = checkup(report, target, args.template or onboard.git_remote(HERE), args.offline, True,
                     Path(args.home) if args.home else Path.home() / "spaces")
        onboard.print_steps(report.steps)
        return 0 if ok else 1
    if args.phase == "resume":
        return resume(args)
    result = run_phase(args)
    needs = setup_journal.open_needs(setup_journal.load())
    if args.json:
        print(json.dumps({**result, "needs": needs}, indent=2))
    elif args.phase != "build" or result.get("busy"):   # the build printed its steps itself, as it always did
        print(f"{result['phase']}: {result['state']}" + (f": {result['why']}" if result.get("why") else ""))
        say_needs(needs)
    return 1 if result["state"] == "failed" or result.get("stopped") else 0


if __name__ == "__main__":
    sys.exit(main())
