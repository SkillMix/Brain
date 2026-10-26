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
  account `--org` names, else the person's own (one who belongs to an organisation is asked
  once, nothing created), and runs onboard's company case, which also creates the person's
  own workspace;
- renders the person's two routines (the sweep chain and the daily chain) for the agent to
  schedule so they outlive the session (`onboard.py <workspace> --os-routines`);
- prints what only the person can do (sign-ins), each with the check that proves it;
- keeps the onboarding page `~/.spaces/onboarding.html` (`onboarding_page.py`), rewritten after
  every step: what is done, what runs now, what is next, what only the person can do.

Defaults the person may want to change (the domains, the never-leaves list, the schedule)
are taken and return on their Needs-you list. Run again, it changes nothing that is right.
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

    python start.py [signins|apps|research|build|history|resume] [--wait] [--json]
                    [--invitation <brain repo>:<slug> | --brain <brain repo>] [--company NAME]
                    [--name NAME] [--email EMAIL] [--feeds mail,calendar,chat,calls,tracker]
                    [--org ORG] [--home DIR] [--template REPO] [--offline] [--dry-run]
"""
from __future__ import annotations

import argparse
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
import doctor  # noqa: E402
import onboard  # noqa: E402
import onboarding_page  # noqa: E402
import routine  # noqa: E402
import setup_journal  # noqa: E402
import spaceyaml  # noqa: E402

HERE = Path(__file__).resolve().parents[2]
# One start at a time on this machine. The lock holds its process id: one whose process is gone is
# a crashed run's (or a closed session's) and is taken over at once; a lock with no readable id
# waits out LOCK_HOURS. The lock lives in the machine's temporary folder; SPACES_START_LOCK moves
# it (the tests use their own).
LOCK = None
LOCK_HOURS = 1
# A start never runs this long (a routine is stopped after one hour), so a live process id on an
# older lock is a stranger's: Windows hands a finished process's number to the next one.
PID_REUSE_HOURS = 3
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


def namespace(who: dict, offline: bool, home=None, company=None) -> tuple:
    """Where a new company's repositories go without --org: the account of the brain an earlier
    start cloned under `home` (a re-run adopts it), else the person's own account. A person who
    belongs to an organisation is asked once, never given the first one: (None, their orgs)."""
    slug = onboard.slugify(company) if company else None
    for name in ([f"{slug}-brain"] if slug else []) + ["brain"] if home else []:
        settings, remote = Path(home) / name / "space.yaml", onboard.git_remote(Path(home) / name)
        data = _brain_yaml(settings.read_text(encoding="utf-8")) if settings.exists() else {}
        if data and remote and (not slug or data.get("name") == slug):
            return remote.split("/")[0], []
    own = who.get("login") or onboard.slugify(who.get("name") or getpass.getuser())
    if offline:
        return own, []
    orgs = [o for o in _out(["gh", "api", "user/memberships/orgs?state=active", "-q",
                             ".[].organization.login"]).splitlines() if o]
    return (None, orgs) if orgs else (own, [])


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
    long as the first runs. A lock whose process is gone is taken over at once, and so is one
    older than PID_REUSE_HOURS whose process id is running again as another process."""
    path = Path(path or lock_path())
    try:
        age = time.time() - path.stat().st_mtime
        held = _lock_pid(path)
        if held is None:
            stale = age >= LOCK_HOURS * 3600
        else:
            stale = age >= PID_REUSE_HOURS * 3600 or not routine.pid_alive(held)
        if not stale:
            return None
        path.unlink()
    except FileNotFoundError:
        pass
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    with os.fdopen(fd, "w") as fh:
        fh.write(f"{os.getpid()}\n")
    return path


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
          user_instructions=None) -> onboard.Report:
    report = onboard.Report(case="start")
    lock = take_lock()
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
                  dry_run, user_instructions)
        else:
            report.stopped = True   # nothing was created: the person's rows come first
    finally:
        if report.progress is not None:
            report.page = report.progress.finish(report)
        lock.unlink(missing_ok=True)
    return report


def setup(report, here, invitation, brain, company, name, email, feeds, org, home, template, offline, dry_run,
          user_instructions) -> onboard.Report:
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
        sub = onboard.run(dst, answers, offline=offline, org=space_org, dry_run=dry_run, home=home,
                          user_instructions=user_instructions, brain=answers["brain"], progress=report.progress)
        person, workspace = answers["name"], dst
    else:
        space_org, orgs = (org, []) if org else namespace(who, offline, space_home, company)
        if space_org is None:
            report.add("namespace", False, "python _meta/tools/start.py --org <account or organisation>",
                       f"where should the company's repositories go? Your own account {who['login']} "
                       f"(recommended) or an organisation you belong to: {', '.join(orgs)}. Say the sentence "
                       "again with --org <name>; nothing was created", needs_owner=True)
            report.stopped = True
            return report
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
        sub = onboard.run(dst, answers, offline=offline, org=space_org, dry_run=dry_run, home=home,
                          user_instructions=user_instructions, progress=report.progress)
        person = who["name"]
        workspace = next((Path(p) for p in sub.spaces.values()
                          if Path(p) != dst and spaceyaml.load(p).profile == "personal"), None)
    report.steps.extend(sub.steps)
    report.spaces.update(sub.spaces)
    report.user_instructions = sub.user_instructions
    if workspace is not None and not dry_run:
        routines = render_routines(workspace, person)
        ws = Path(workspace).as_posix()
        report.add("routine prompts", True, f"test -f {ws}/_meta/state/routines/daily-chain.md",
                   "; ".join(f"{r['name']} at {r['schedule']}" for r in routines)
                   + f" (cron, this machine's local time): next, `python {ws}/_meta/tools/onboard.py {ws} "
                   "--os-routines` schedules them so they outlive this session")
        report.workspace = workspace
        report.routines = routines
        report.who = who
    return report


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


def phase_signins(args) -> dict:
    return _pending("signins")


def phase_apps(args) -> dict:
    return _pending("apps")


def phase_research(args) -> dict:
    return _pending("research")


def phase_build(args) -> dict:
    """Everything start did before there were phases: the spaces, the personal instruction, the
    routines. Its state is `failed` when a step did not work (a dry run included), `pending` for
    a dry run that did (nothing was created), `waiting` when it stopped for the person or left
    rows only they can do (say the sentence again after), else `done`."""
    report = start(HERE, invitation=args.invitation, brain=args.brain, company=args.company, name=args.name,
                   email=args.email, feeds=args.feeds, org=args.org, home=args.home, template=args.template,
                   offline=args.offline, dry_run=args.dry_run, user_instructions=args.user_instructions)
    result = summary(report)
    if not args.json:
        onboard.print_steps(report.steps)
        if result["workspace"]:
            print(f"\nworkspace: {result['workspace']}")
        for r in result["routines"]:
            print(f"routine {r['name']} ({r['schedule']}, {r['timezone']} time): {r['prompt_file']}")
        for line in result["plan"]:
            print(f"plan: {line}")
        if result["page"]:
            print(f"page: {result['page']}")
        for row in result["yours"]:
            print(f"yours: {row.get('name')}: {row.get('result')} (check: {row.get('check')})")
        if result["failed"] or result["yours"]:
            print("then say the sentence again: every step already done is kept")
    state = ("failed" if result["failed"] else "pending" if args.dry_run
             else "waiting" if result["stopped"] or result["yours"] else "done")
    return {"phase": "build", "state": state, **result}


def phase_history(args) -> dict:
    return _pending("history")


def run_phase(args) -> dict:
    """One phase, journaled: `running` while it works, then the state it returns (when it names
    none: `waiting` while the person is still needed, else `done`). One that raises is left
    `failed`, not `running`. A dry run changes nothing, the journal included."""
    name, journal = args.phase, not args.dry_run
    if journal:
        setup_journal.set_phase(name, "running")
    try:
        result = globals()[f"phase_{name}"](args)   # looked up now, so a test can stand in for one
    except Exception:
        if journal:
            setup_journal.set_phase(name, "failed")
        raise
    result.setdefault("phase", name)
    if "state" not in result:
        result["state"] = "waiting" if setup_journal.open_needs(setup_journal.load()) else "done"
    if journal:
        setup_journal.set_phase(name, result["state"])
    return result


def say_needs(needs: list) -> None:
    """What only the person can do, a line each, with the check that proves it."""
    for n in needs:
        more = ", ".join(f"{k} {n[k]}" for k in ("code", "link") if n.get(k))
        print(f"yours: {n.get('kind')}: {n.get('text')}" + (f" ({more})" if more else "")
              + f" (check: {n.get('check')})")


def resume(args) -> int:
    """Where the setup stands after a closed session or a day: the first phase that is not done,
    what waits for the person, every phase's record. It runs nothing; the agent runs the phase."""
    data = setup_journal.load()
    result = {"next": setup_journal.next_phase(data), "needs": setup_journal.open_needs(data),
              "phases": {p: data.get(p) or {"state": "pending"} for p in PHASES}}
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    for phase, record in result["phases"].items():
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
    ap.add_argument("--company", help="no brain yet: the company's name (default: the organization's)")
    ap.add_argument("--name")
    ap.add_argument("--email")
    ap.add_argument("--feeds", help="the connectors this session has: mail, calendar, chat, calls, tracker")
    ap.add_argument("--org")
    ap.add_argument("--home", help="where the spaces are cloned (default ~/spaces)")
    ap.add_argument("--template", help="the template repository (default: where this copy came from)")
    ap.add_argument("--user-instructions", help="the user-level instruction file (default ~/.claude/CLAUDE.md)")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--doctor", action="store_true", help="only check this machine and fix what needs no password")
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
    elif args.phase != "build":   # the build printed its steps itself, as it always did
        print(f"{result['phase']}: {result['state']}")
        say_needs(needs)
    return 1 if result["state"] == "failed" or result.get("stopped") else 0


if __name__ == "__main__":
    sys.exit(main())
