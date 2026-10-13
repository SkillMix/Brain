"""Start: one sentence in an agent session sets everything up (vision section 15).

The person says "onboard me" (or "set up our company brain") with this template's link, and
the invitation when they have one. The agent clones the template anywhere and runs this tool,
which does every step itself and asks nothing that has a default:

- reads who the person is from git and the host, and their invitation from the brain
  (named, or found by their name);
- a person: creates their workspace repository from the template, clones it, and runs
  onboard there (the brain and every crew project mounted, the personal instruction at user
  level, the sync task, the host's merge settings, the first status and the tests);
- a company (no brain given): creates the brain from the template and runs onboard's
  company case, which also creates the person's own workspace;
- renders the person's two routines (the sweep chain and the daily chain) for the agent to
  schedule in its harness;
- prints what only the person can do (sign-ins), each with the check that proves it.

Defaults the person may want to change (the domains, the never-leaves list, the schedule)
are taken and return on their Needs-you list. Run again, it changes nothing that is right.
`--dry-run` prints the plan and stops before onboard; offline, a space is copied under
`--home` when given, else beside this copy.

    python start.py [--invitation <brain repo>:<slug> | --brain <brain repo>] [--company NAME]
                    [--name NAME] [--email EMAIL] [--feeds mail,calendar,chat,calls,tracker]
                    [--org ORG] [--home DIR] [--template REPO] [--offline] [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:  # the agent does every step: the one library the tools need is installed here
    import yaml
except ImportError:  # pragma: no cover - depends on the machine
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--user", "pyyaml"], check=False)
    import site
    sys.path.append(site.getusersitepackages())
    import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import onboard  # noqa: E402
import spaceyaml  # noqa: E402

HERE = Path(__file__).resolve().parents[2]
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


def default_org(who: dict, offline: bool) -> str:
    """The organization the person administers, when there is exactly one; else their account."""
    if not offline:
        orgs = [o for o in _out(["gh", "api", "user/memberships/orgs", "-q",
                                 '.[] | select(.role == "admin") | .organization.login']).splitlines() if o]
        if len(orgs) == 1:
            return orgs[0]
    return who.get("login") or onboard.slugify(who.get("name") or getpass.getuser())


def org_title(org: str, offline: bool) -> str:
    title = "" if offline else _out(["gh", "api", f"orgs/{org}", "-q", ".name"])
    return title if title and title != "null" else onboard.humanize(org)


# ----- the routines --------------------------------------------------------------------
def render_routines(workspace: Path, person: str) -> list:
    """Write the person's routine prompts to `_meta/state/routines/` for the agent to schedule."""
    out = []
    target = Path(workspace) / "_meta" / "state" / "routines"
    for src in sorted(ROUTINES.glob("*.md")):
        text = src.read_text(encoding="utf-8").format(workspace=Path(workspace).as_posix(), person=person)
        dst = target / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8", newline="\n")
        meta = yaml.safe_load(text.split("---")[1]) or {}
        out.append({"name": meta.get("name") or src.stem, "schedule": meta.get("schedule"),
                    "prompt_file": dst.as_posix()})
    return out


# ----- start ---------------------------------------------------------------------------
def preflight(report: onboard.Report, offline: bool) -> bool:
    if offline:
        return True
    ok = onboard.run_cmd(["gh", "auth", "status"]).returncode == 0
    report.add("signed in to the host", ok, "gh auth status",
               "signed in" if ok else "run `gh auth login` once, then say the sentence again", needs_owner=not ok)
    return ok


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
    here = Path(here).resolve()
    report = onboard.Report(case="start")
    if not preflight(report, offline):
        return report
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
                          user_instructions=user_instructions, brain=answers["brain"])
        person, workspace = answers["name"], dst
    else:
        space_org = org or default_org(who, offline)
        answers = {"company": company or org_title(space_org, offline), "owner": who["name"],
                   "owner_email": who["email"], "org": space_org}
        if feeds_text:
            answers["feeds"] = feeds_text
        dst, full = onboard.create_space(report, here, "brain", "brain", offline, space_org, space_home, tpl,
                                         dry_run)
        if dst is None:
            return report
        if dry_run:
            return plan(report, dst, full, tpl, "company")
        answers = onboard.interview(answers, interactive=False, case="company", root=dst)
        sub = onboard.run(dst, answers, offline=offline, org=space_org, dry_run=dry_run, home=home,
                          user_instructions=user_instructions)
        person = who["name"]
        workspace = next((Path(p) for p in sub.spaces.values()
                          if Path(p) != dst and spaceyaml.load(p).profile == "personal"), None)
    report.steps.extend(sub.steps)
    report.spaces.update(sub.spaces)
    report.user_instructions = sub.user_instructions
    if workspace is not None and not dry_run:
        routines = render_routines(workspace, person)
        report.add("routines", True, f"test -f {Path(workspace).as_posix()}/_meta/state/routines/daily-chain.md",
                   "; ".join(f"{r['name']} at {r['schedule']}" for r in routines)
                   + ": the agent schedules them in its harness")
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
            "done": sum(1 for s in report.steps if s.done), "failed": failed, "yours": owner}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = ap.parse_args(argv)
    report = start(HERE, invitation=args.invitation, brain=args.brain, company=args.company, name=args.name,
                   email=args.email, feeds=args.feeds, org=args.org, home=args.home, template=args.template,
                   offline=args.offline, dry_run=args.dry_run, user_instructions=args.user_instructions)
    result = summary(report)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        onboard.print_steps(report.steps)
        if result["workspace"]:
            print(f"\nworkspace: {result['workspace']}")
        for r in result["routines"]:
            print(f"routine {r['name']} ({r['schedule']}): {r['prompt_file']}")
        for line in result["plan"]:
            print(f"plan: {line}")
        for row in result["yours"]:
            print(f"yours: {row.get('name')}: {row.get('result')} (check: {row.get('check')})")
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
