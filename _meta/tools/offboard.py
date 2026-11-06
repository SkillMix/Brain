"""offboard: close a space when its owner leaves (shape 1).

    python _meta/tools/offboard.py [root] [--online] [--dry-run]
    python _meta/tools/offboard.py <brain> --person <slug> [--online [--remove]] [--dry-run]

Prints what still waits (queue, held, candidates) and moves nothing; removes the sync task
(online: the Windows task, the macOS launch agent and its file, the systemd user timer and its two
unit files, or the cron line by its `# <task>` marker) and the personal instruction block from the
user-level file; archives
the repository (online, `gh repo archive`); sets `state: archived` on the subjects whose home
is this space: in its own registry at once, and in the registry that holds one (the brain) by
a registry note that ships at once, alone (online, or to `offline_roots`); writes
`_meta/state/offboard.yaml`. Deletes no file of the space. Online, before the archive, the
repository's own mount secrets (the deploy keys, a mount token) are deleted, so nothing that can still
read this repository's secrets reads a mount with them. A personal space also gets the rows only a
person can clear, never marked done (`access_steps`): the brain side, for the brain's owner's one yes;
a token the person made by hand, which no API revokes; and a sweep task in the cloud to disable.

`--person <slug>`, in the brain and by its owner (`leaver`), is the brain side: the person's people
subject is archived in the brain's registry, and every repository they can still read (the brain, each
space whose audience names them) is one row. With `--remove`, run by the brain owner's agent after the
owner's one yes that the person has left, the agent removes that access itself through GitHub's API
(the collaborator, every team membership, the outside collaborator, the organisation membership, the
organisation-approved tokens where the API allows it, and the deploy keys of the person's workspace)
and reads back that the access is `none`; only what did not read back stays a row and a Needs-you item,
with its command. Removing access never widens anyone's: nothing here grants, and the signed-in
account's own login is never removed. A walled crew that still names them is a question for its owner.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import onboard  # noqa: E402
import pages  # noqa: E402
import registry  # noqa: E402
import spaceyaml  # noqa: E402

ZONES = (("queue", "_outbound/queue"), ("held", "_outbound/held"), ("candidates", "_inbound/candidates"))


def run_cmd(argv, dry_run=False, cwd=None):
    """Offboard's door to the outside: the same one onboard uses, monkeypatched separately."""
    return onboard.run_cmd(argv, dry_run=dry_run, cwd=cwd)


def remove_probe_task(report, root, state=None, dry_run=False) -> None:
    """The routines' probe task a build recorded (`probe_task` in onboard.yaml, written before the task was
    registered) and did not remove, because it was stopped first: removed through `remove_sync_task`, and
    forgotten once it is gone. The record is read from a file a merge can change, so it only says whether there
    is one: what is removed is this space's own probe task by the name and launch agent label the code derives
    (`<prefix>probe-<name>`, the prefix `onboard.task_prefix` gives), never by a name or label the record holds."""
    root = Path(root)
    state = state if state is not None else onboard.read_yaml(root / "_meta" / "state" / "onboard.yaml", {}) or {}
    record = state.get("probe_task")
    try:
        name = f"{onboard.task_prefix()}probe-{spaceyaml.load(root).name}"
    except Exception:
        return
    if not (isinstance(record, dict) and record.get("name") == name):
        return
    kind = {"kind": record["kind"]} if record.get("kind") in ("schtasks", "launchd", "systemd", "cron") else {}
    remove_sync_task(report, root, {"routine_tasks": [{"name": name, **kind}]}, name, dry_run,
                     label=f"routine probe task {name} removed")
    if not dry_run and report.steps and report.steps[-1].done:
        onboard.forget_probe_task(root)


def remove_sync_task(report, root, state, task_name, dry_run, label="sync task removed") -> None:
    """Remove one OS task onboard registered: the sync task, or a routine's (`label` names it)."""
    if not dry_run and label == "sync task removed":
        onboard.forget_space(root)   # the chains stop running over it on this machine
    if onboard.SYSTEM == "Windows":
        present = run_cmd(["schtasks", "/Query", "/TN", task_name], dry_run)
        if not dry_run and present.returncode != 0:
            report.add(label, True, f"schtasks /Query /TN {task_name}",
                       "none registered (a minted project runs under its person's task)")
            return
        res = run_cmd(["schtasks", "/Delete", "/F", "/TN", task_name], dry_run)
        verify = run_cmd(["schtasks", "/Query", "/TN", task_name], dry_run)
        ok = res.returncode == 0 and (dry_run or verify.returncode != 0)
        report.add(label, ok, f"schtasks /Query /TN {task_name}", res.stderr.strip()[:200] or task_name)
        return
    recorded = next((r for r in [state.get("sync_task") or {}, *(state.get("routine_tasks") or [])]
                     if isinstance(r, dict) and r.get("name") == task_name), {})
    ident = str(recorded.get("label") or "")
    if not re.fullmatch(r"com\.spaces\.[\w.-]+", ident):   # the record is read from a file a merge can change
        ident = onboard.LAUNCHD_PREFIX + task_name
    agent = Path.home() / "Library" / "LaunchAgents" / f"{ident}.plist"
    if agent.exists() or recorded.get("kind") == "launchd":   # a Mac: the agent is let go of, then its file removed
        loaded = f"gui/{onboard.user_id()}/{ident}"
        run_cmd(["launchctl", "bootout", loaded], dry_run)
        verify = run_cmd(["launchctl", "print", loaded], dry_run)
        ok = dry_run or verify.returncode != 0   # whatever bootout said: the file goes only once it is not loaded
        if ok and not dry_run:
            agent.unlink(missing_ok=True)
        report.add(label, ok, f"launchctl print {loaded}",
                   "launch agent let go, its file removed" if ok else "the launch agent is still loaded")
        return
    timer = Path.home() / ".config" / "systemd" / "user" / f"{task_name}.timer"
    if timer.exists():   # a Linux with a systemd user session got a timer
        run_cmd(["systemctl", "--user", "disable", "--now", timer.name], dry_run)
        verify = run_cmd(["systemctl", "--user", "is-enabled", timer.name], dry_run)
        ok = dry_run or verify.returncode != 0   # the files stay while systemd still has the timer
        if ok and not dry_run:
            timer.unlink()
            timer.with_suffix(".service").unlink(missing_ok=True)
            run_cmd(["systemctl", "--user", "daemon-reload"])
        report.add(label, ok, f"systemctl --user is-enabled {timer.name}",
                   "timer disabled, its two unit files removed" if ok else "the timer is still enabled")
        return
    lines, state, why = onboard.read_crontab(dry_run, run=run_cmd)
    if state == "missing":
        report.add(label, True, "crontab -l", "no cron on this machine: nothing was registered")
        return
    if state == "failed":   # not an empty crontab: nothing was written, and "no line left" would be a guess
        report.add(label, False, "crontab -l", f"this computer's scheduler could not be read ({why}), so nothing was "
                   "removed: try again once it can be read")
        return
    keep = [l for l in lines if not onboard.is_task_line(l, task_name, root)]
    if len(keep) != len(lines):
        onboard.write_crontab(keep, dry_run, run=run_cmd)
    verify = run_cmd(["crontab", "-l"], dry_run).stdout
    ok = dry_run or not any(onboard.is_task_line(l, task_name, root) for l in verify.splitlines())
    report.add(label, ok, "crontab -l", "no line left" if ok else "a line remains")


def raw_records(root: Path) -> list:
    """A personal space's raw records: the feed files and the witness pages of its own sweep."""
    root = Path(root)
    out = sorted((root / "_meta" / "state" / "feed").glob("*.jsonl")) if (root / "_meta/state/feed").is_dir() else []
    base = root / "knowledge" / "sources"
    out += sorted(base.rglob("*.md")) if base.is_dir() else []
    return out


TOKENS_PAGE = "https://github.com/settings/personal-access-tokens"
# owner/name as GitHub allows them: the only repository text a command written for a person to run may hold,
# since `_meta/state/onboard.yaml` and a checkout's remote are files someone else may have edited
REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}")


def _repo_of(url) -> str | None:
    """owner/name of an https GitHub address (`onboard.MOUNT_URL`), else None."""
    match = onboard.MOUNT_URL.fullmatch(str(url or ""))
    return f"{match.group(1)}/{match.group(2)}" if match else None


def revoke_list(root, space) -> list:
    """The repositories a leaving person read through this workspace: the brain and every other mount,
    as owner/name, in the order space.yaml lists them."""
    found = [_repo_of(m.get("repo")) for m in space.mounts if isinstance(m, dict)]
    return list(dict.fromkeys(r for r in found if r))


MOUNT_SECRETS = ("MOUNT_DEPLOY_KEYS", "MOUNT_READ_TOKEN", "BRAIN_READ_TOKEN")
HAND_MADE = ("MOUNT_READ_TOKEN", "BRAIN_READ_TOKEN")   # a fine-grained token: made by hand, revoked by hand


def remove_secrets(report, repo: str, dry_run=False) -> list:
    """Delete this repository's mount secrets (`MOUNT_SECRETS`) before it is archived, and read the list back:
    anyone who can still push to it could otherwise read a mount with them. Returns the names that were there;
    a token among them was made by hand, and its revoking stays the person's (`access_steps`)."""
    listed = run_cmd(["gh", "secret", "list", "--repo", repo], dry_run)
    names = {line.split()[0] for line in (listed.stdout or "").splitlines() if line.split()}
    there = [n for n in MOUNT_SECRETS if n in names]
    for name in there:
        run_cmd(["gh", "secret", "delete", name, "--repo", repo], dry_run)
    after = run_cmd(["gh", "secret", "list", "--repo", repo], dry_run)
    left = {line.split()[0] for line in (after.stdout or "").splitlines() if line.split()} & set(there)
    ok = listed.returncode == 0 and after.returncode == 0 and not left
    report.add("mount secrets removed", ok or dry_run, f"gh secret list --repo {repo}",
               (f"removed: {', '.join(there)}" if there else "none were there") if ok or dry_run
               else f"still there: {', '.join(sorted(left)) or 'the list could not be read'}")
    return there


def access_steps(report, root, space, hand_made=None) -> None:
    """What the leaver's own computer cannot undo, as rows only a person can clear (needs_owner, not
    done), so a finished offboard never reads as complete while access remains: the brain side
    (`leaver`), which the brain owner's agent runs after the owner's one yes that this person left; a
    token the person made by hand for these spaces (`hand_made`: the names `remove_secrets` found;
    None when nobody could look, offline), which no API revokes; a sweep that runs in the cloud
    (routine-host.yaml), a task on their claude.ai account, which only they disable. The deploy keys
    need no row: their private halves were this repository's secret, now deleted, and the brain side
    removes the public ones."""
    if space.profile != "personal":
        return
    slug = space.name   # a workspace is named by its person's registry id (onboard's person case)
    repos = revoke_list(root, space)
    brain = repos[0] if repos else "<brain>"
    report.add("access to revoke", False, f"python _meta/tools/offboard.py <brain checkout> --person {slug} --online --remove",
               f"the brain's owner says yes to '{space.owner or slug} has left' in {brain}, and their agent runs "
               f"`python _meta/tools/offboard.py <brain checkout> --person {slug} --online --remove`: it archives "
               f"{space.owner or slug} in the registry and removes their access to "
               f"{', '.join(repos) or 'the brain and the projects'}, reading it back", needs_owner=True)
    if hand_made is None or hand_made:
        report.add("tokens to revoke", False, TOKENS_PAGE,
                   f"revoke the personal access token{'s' if not hand_made or len(hand_made) > 1 else ''} made for these "
                   f"spaces ({', '.join(hand_made or HAND_MADE)}) at {TOKENS_PAGE}: no API revokes a person's own token",
                   needs_owner=True)
    else:
        report.add("tokens to revoke", True, f"gh secret list --repo {state_repo(root) or '<repo>'}",
                   "no hand-made token was stored here; the mount keys went with the secret")
    try:
        import routine
        where = routine.host(root) or {}
    except Exception:
        where = {}
    if where.get("runner") == "cloud":
        tasks = [t for t in (where.get("tasks") or []) if isinstance(t, str) and routine.TASK_ID.fullmatch(t)]
        report.add("cloud routine to disable", False, "https://claude.ai/code/scheduled",
                   f"disable the scheduled task{'s' if len(tasks) != 1 else ''} {', '.join(tasks) or 'of the sweep'} "
                   "on claude.ai: it reads your connectors until it is off", needs_owner=True)


def state_repo(root) -> str | None:
    """owner/name this space's onboarding recorded, else its origin remote; None for any other shape."""
    state = onboard.read_yaml(Path(root) / "_meta" / "state" / "onboard.yaml", {}) or {}
    repo = state.get("repo") or onboard.git_remote(root)
    return repo if repo and REPO.fullmatch(str(repo)) else None


def _readers_of(root: Path) -> tuple:
    """(owner/name, space.yaml) of a space checkout, or (None, None) when it cannot be read."""
    try:
        return onboard.git_remote(root), spaceyaml.load(root)
    except Exception:
        return None, None


def leaver(brain_root, slug: str, online: bool = False, dry_run: bool = False, remove: bool = False,
           gh=None) -> onboard.Report:
    """The brain side of a departure, run by the brain owner's agent after the owner's one yes that the
    person has left: the person's `people` subject is archived in the brain's registry (the brain holds
    it, so only the brain archives it), and every repository the person can still read is one row: the
    brain, and every space the brain's onboarding recorded whose audience names them or that they own.
    With `remove` (online), the agent takes that access away itself (`remove_access`) before it reads
    it back; without it, each row carries the command. Online each repository's access is read back
    (`collaborators/<login>/permission`): a row is done only once the host says `none`. A walled subject
    whose crew still names them is a question for that subject's owner; the crew is never changed here.
    What is not done goes to `_inbound/needs-you/offboard-<slug>.yaml`, owned by the brain's owner.
    Nothing grants access, and nothing is deleted but access and the person's deploy keys. `gh` is the
    door to GitHub (`gh(args)`), `run_cmd` by default. `remove` carries out the owner's word that someone
    left, and removing an organisation membership deletes that member's private forks: a routine never
    runs it (`routine.in_routine`), whatever a mail or a page asks."""
    if remove:
        import routine
        running = routine.in_routine()
        if running:
            raise ValueError(f"removing a leaver's access is the brain owner's word, and {running}; "
                             "run it in a session with the owner")
    root = Path(brain_root).resolve()
    space = spaceyaml.load(root)
    report = onboard.Report(case="offboard-person")
    if space.profile != "brain":
        raise ValueError(f"{root} is a {space.profile} space: run --person in the brain")
    slug = pages.check_slug(slug, "person")
    reg = registry.Registry(root, space)
    person = reg.subjects.get(slug)
    if person is None or person.kind != "people":
        raise ValueError(f"{slug} is no person in {space.name}'s registry")
    if person.state != "archived" and not dry_run:
        person.state = "archived"
        reg.save()
    report.add("person archived", True, f"python _meta/tools/registry.py . get {slug}",
               f"{person.title} {'would be' if dry_run else 'is'} archived: the access check counts them out of "
               "every audience")
    login = str(person.match.get("github") or "").strip()
    shown = login if login and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", login) else "<their login>"
    state = onboard.read_yaml(root / "_meta" / "state" / "onboard.yaml", {}) or {}
    repos = {}
    own = state.get("repo") or onboard.git_remote(root)
    if own and REPO.fullmatch(str(own)):
        repos[own] = f"{space.name} (everyone)"
    elif own:
        report.add("access to the brain", False, "git remote get-url origin",
                   f"{pages._shown(own)} is not a repository name: remove {person.title}'s access to the brain by hand",
                   needs_owner=True)
    for name, where in sorted((state.get("spaces") or {}).items() if isinstance(state.get("spaces"), dict) else []):
        repo, other = _readers_of(Path(str(where)))
        if not repo or other is None or repo in repos:
            continue
        audience = other.audience if isinstance(other.audience, list) else []
        if person.title not in audience and other.owner != person.title:
            continue
        if REPO.fullmatch(str(repo)):
            repos[repo] = str(name)
        else:   # never pasted into a command for a person to run
            report.add(f"access {pages._shown(name)}", False, "git remote get-url origin",
                       f"{pages._shown(repo)} is not a repository name: remove {person.title}'s access to "
                       f"{pages._shown(where)} by hand", needs_owner=True)
    items = []
    gh = gh or (lambda args, input=None: run_cmd(["gh", *args], dry_run))
    if remove and online and login and shown == login and not dry_run:
        items += remove_access(report, gh, root, space, person, login, list(repos))
    for repo, name in repos.items():
        check = f"gh api repos/{repo}/collaborators/{shown}/permission -q .permission"
        remove_line = f"gh api -X DELETE repos/{repo}/collaborators/{shown}"
        owner = repo.split("/")[0]
        gone = False
        if online and login and shown == login:
            res = gh(["api", f"repos/{repo}/collaborators/{login}/permission", "-q", ".permission"])
            gone = res.returncode == 0 and res.stdout.strip() == "none"
        text = (f"{person.title} has left: remove their access to {repo} ({name}). Run `{remove_line}`; when they reach it "
                f"through a team of {owner}, `gh api -X DELETE orgs/{owner}/teams/<team>/memberships/{shown}`, or "
                f"take them out of the organisation with `gh api -X DELETE orgs/{owner}/memberships/{shown}`.")
        report.add(f"access {repo}", gone, check, "no access left" if gone else text, needs_owner=not gone)
        if not gone:
            items.append({"id": f"offboard-{slug}-{repo.replace('/', '-').lower()}", "kind": "question",
                          "for": space.owner, "text": text, "recommendation": "remove it today",
                          "default": "they keep reading everything that lands there", "depends": 0, "check": check})
    for subject in sorted(reg.subjects.values(), key=lambda s: s.id):
        if slug in (subject.crew or []) and subject.id != slug and subject.state != "archived":
            text = (f"{person.title} has left but is still on the crew of {subject.title or subject.id}: take them "
                    "off its crew, or say why they stay.")
            report.add(f"crew {subject.id}", False, f"python _meta/tools/registry.py . get {subject.id}", text,
                       needs_owner=True)
            items.append({"id": f"offboard-{slug}-crew-{subject.id}", "kind": "question",
                          "for": subject.owner or space.owner, "text": text, "recommendation": "take them off",
                          "default": "the crew still names them", "depends": 0, "check": ""})
    if not dry_run:
        path = root / "_inbound" / "needs-you" / f"offboard-{slug}.yaml"
        if items or path.exists():
            onboard.write_yaml(path, {"items": items})
    return report


def _gh_ok(gh, *args) -> tuple:
    res = gh(list(args))
    return res.returncode == 0, (res.stdout or "").strip()


def remove_access(report, gh, root, space, person, login: str, repos: list) -> list:
    """Take a leaver's access away through GitHub's API, as the brain owner's agent, and read it back.
    Per repository: the collaborator. Per organisation that owns one of them: every team membership,
    the outside collaborator, the organisation membership (read back as gone), and the
    organisation-approved fine-grained tokens, revoked where the API lets this account list them
    (GitHub keeps that to apps: then the row says a token of theirs reads nothing once their access is
    none). The deploy keys of their workspace (`workspace_repo`) on every one of these repositories are
    removed, and so are theirs on every other repository the brain's record of attached keys lists for
    them (`onboard.attached_mounts`) and, record or not, on the brain and every project the registry has
    them on the crew of (`onboard.key_mounts`). The signed-in account's own login is never removed. Returns the
    Needs-you items for what did not read back."""
    items = []
    me = _gh_ok(gh, "api", "user", "-q", ".login")[1]
    if me and me.lower() == login.lower():
        report.add("access removed", False, f"gh api user -q .login",
                   f"{login} is the account this agent runs as: its access is never removed here", needs_owner=True)
        return items
    for repo in repos:
        gh(["api", "-X", "DELETE", f"repos/{repo}/collaborators/{login}"])
    owners = list(dict.fromkeys(r.split("/")[0] for r in repos))
    for org in owners:
        if _gh_ok(gh, "api", f"users/{org}", "-q", ".type")[1] != "Organization":
            continue
        ok, teams = _gh_ok(gh, "api", f"orgs/{org}/teams?per_page=100", "--paginate", "-q", ".[].slug")
        left_teams = []
        for team in [t for t in teams.splitlines() if re.fullmatch(r"[a-z0-9._-]+", t.strip())] if ok else []:
            if gh(["api", f"orgs/{org}/teams/{team}/memberships/{login}", "-q", ".state"]).returncode == 0:
                gh(["api", "-X", "DELETE", f"orgs/{org}/teams/{team}/memberships/{login}"])
                if gh(["api", f"orgs/{org}/teams/{team}/memberships/{login}", "-q", ".state"]).returncode == 0:
                    left_teams.append(team)
        gh(["api", "-X", "DELETE", f"orgs/{org}/outside_collaborators/{login}"])
        gh(["api", "-X", "DELETE", f"orgs/{org}/memberships/{login}"])
        member = gh(["api", f"orgs/{org}/memberships/{login}", "-q", ".state"])
        gone = member.returncode != 0 and not left_teams
        check = f"gh api orgs/{org}/memberships/{login} -q .state"
        text = (f"{person.title} has left but is still in {org}"
                + (f" (teams {', '.join(left_teams)})" if left_teams else "")
                + f": `gh api -X DELETE orgs/{org}/memberships/{login}`")
        report.add(f"organisation {org}", gone, check,
                   f"{login} is no longer a member or in a team of {org}" if gone else text, needs_owner=not gone)
        if not gone:
            items.append({"id": f"offboard-{person.id}-org-{org.lower()}", "kind": "question", "for": space.owner,
                          "text": text, "recommendation": "remove it today",
                          "default": "they keep what the organisation gives its members", "depends": 0,
                          "check": check})
        ok, ids = _gh_ok(gh, "api", f"orgs/{org}/personal-access-tokens?owner[]={login}&per_page=100", "--paginate",
                         "-q", ".[].id")
        ids = [i for i in ids.splitlines() if i.strip().isdigit()] if ok else []
        if ok and ids:
            args = ["api", "-X", "POST", f"orgs/{org}/personal-access-tokens", "-f", "action=revoke"]
            for i in ids:
                args += ["-F", f"pat_ids[]={i}"]
            revoked = gh(args).returncode == 0
            report.add(f"tokens in {org}", revoked, f"gh api 'orgs/{org}/personal-access-tokens?owner[]={login}'",
                       f"{len(ids)} organisation-approved token{'s' if len(ids) != 1 else ''} revoked" if revoked
                       else "GitHub did not revoke them: they read nothing once the access above is none")
        else:
            report.add(f"tokens in {org}", True, f"gh api 'orgs/{org}/personal-access-tokens?owner[]={login}'",
                       "none listed" if ok else "GitHub lets only an app list them: a token of theirs reads "
                       f"nothing in {org} once their access is none")
    # the record of attached keys, and without it the brain and the projects the registry has them on the crew of
    found = {}
    for repo in [*repos, *onboard.attached_mounts(root, person.id),
                 *sorted(onboard.key_mounts(root, registry.Registry(root, space), person))]:
        found.setdefault(repo.lower(), repo)
    for repo in found.values():
        removed, clear = remove_their_keys(gh, repo, person, login, owners)
        check = f"gh api repos/{repo}/keys -q '.[].title'"
        report.add(f"deploy keys on {repo}", clear, check,
                   (f"{removed} of their workspace's removed" if removed else "none of their workspace's") if clear
                   else "the deploy keys could not be read back: remove those titled for their workspace",
                   needs_owner=not clear)
        if not clear:
            items.append({"id": f"offboard-{person.id}-keys-{repo.replace('/', '-').lower()}", "kind": "question",
                          "for": space.owner, "text": f"{person.title} has left: remove the deploy keys of their "
                          f"workspace on {repo} (Settings, Deploy keys, the ones titled 'spaces checks of ...').",
                          "recommendation": "remove them today", "default": "their workspace's checks keep reading it",
                          "depends": 0, "check": check})
    return items


def their_consumers(person, login: str, owners) -> set:
    """The repositories whose deploy keys are the leaver's, lower case: their registered `workspace_repo`,
    their workspace by onboarding's name (`<workspace>-workspace`) in their own account and in each
    organisation here, and any repository in their own account."""
    found = {onboard.plain_repo(person.extra.get("workspace_repo"))}
    ws = str(person.extra.get("workspace") or "")
    if pages.is_slug(ws):
        found |= {onboard.plain_repo(f"{who}/{ws}-workspace") for who in [login, *owners]}
    return {f.lower() for f in found if f}


def remove_their_keys(gh, repo: str, person, login: str, owners) -> tuple:
    """Remove from `repo` every deploy key the agents made for one of the leaver's repositories (titled
    `onboard.mount_key_title`), and read back: (removed, none of theirs left). A key of another
    repository is never touched."""
    consumers = their_consumers(person, login, owners)
    prefix = onboard.mount_key_title("")

    def theirs():
        ok, out = _gh_ok(gh, "api", f"repos/{repo}/keys", "--paginate", "-q", '.[] | "\\(.id) \\(.title)"')
        keys = []
        for line in out.splitlines() if ok else []:
            kid, _, title = line.partition(" ")
            title = title.strip().lower()
            if not (kid.isdigit() and title.startswith(prefix)):
                continue
            consumer = title[len(prefix):]
            if consumer in consumers or consumer.split("/")[0] == login.lower():
                keys.append(kid)
        return ok, keys
    ok, keys = theirs()
    if not ok:
        return 0, False
    removed = sum(gh(["api", "-X", "DELETE", f"repos/{repo}/keys/{kid}"]).returncode == 0 for kid in keys)
    ok, left = theirs()
    return removed, ok and not left


def run(root, online, dry_run=False, forget_records: bool = False,
        offline_roots: dict | None = None) -> onboard.Report:
    root = Path(root).resolve()
    space = spaceyaml.load(root)
    now = space.now()
    report = onboard.Report(case="offboard")
    counts = {zone: len([p for p in (root / rel).glob("*") if p.is_file() and p.name != ".gitkeep"])
              for zone, rel in ZONES}
    for zone, count in counts.items():
        print(f"{zone}: {count} still here (nothing moved)")
    report.add("still waiting", True, "ls _outbound/queue _outbound/held _inbound/candidates",
               ", ".join(f"{k} {v}" for k, v in counts.items()))
    if space.profile == "personal":
        raw = raw_records(root)
        if forget_records and not dry_run:
            # the person's own command: raw personal records go at offboard; git keeps history
            for path in raw:
                path.unlink(missing_ok=True)
            report.add("raw records removed", True, "ls _meta/state/feed knowledge/sources",
                       f"{len(raw)} files removed on the person's own command; history keeps them")
        else:
            report.add("raw records listed", True, "python _meta/tools/offboard.py . --forget-records",
                       f"{len(raw)} raw records stay; the person removes them with --forget-records")
    state = onboard.read_yaml(root / "_meta" / "state" / "onboard.yaml", {}) or {}
    try:   # unrecorded, the name onboard gave it under the prefix in force (F-7): a test run never names the person's
        task_name = (state.get("sync_task") or {}).get("name") or f"{onboard.task_prefix()}sync-{space.name}"
    except ValueError as exc:   # a prefix that is none of ours names no task, so none is touched
        task_name = None
        report.add("sync task removed", False, f"echo ${onboard.TASK_PREFIX_ENV}", str(exc)[:200])
    if online:
        if task_name:
            remove_sync_task(report, root, state, task_name, dry_run)
        for routine in state.get("routine_tasks") or []:   # routines the OS ran, without a harness scheduler
            remove_sync_task(report, root, state, routine["name"], dry_run, label=f"routine task {routine['name']} removed")
        remove_probe_task(report, root, state, dry_run)   # a build stopped before it removed its probe task
    elif task_name:
        report.add("sync task removed", True, f"schtasks /Query /TN {task_name}",
                   "offline: nothing was registered")
    # Only a space that installed an instruction block (recorded by onboard) has one to remove;
    # a minted project never touches the person's user-level file.
    ui = Path(state["user_instructions"]) if state.get("user_instructions") else None
    if ui is None:
        report.add("personal instruction removed", True, "cat _meta/state/onboard.yaml",
                   "none was installed for this space")
    elif ui.exists() and not onboard.instruction_present(ui, root):
        # the block names another space (the owner's workspace, say): it is theirs, not ours
        report.add("personal instruction removed", True, f"grep -c 'space:personal-instruction' {ui}",
                   "the block in the user-level file belongs to another space; kept")
    elif ui.exists():
        text = ui.read_text(encoding="utf-8")
        new = onboard.remove_block(text)
        if new != text and not dry_run:
            onboard.write_text(ui, new)
        report.add("personal instruction removed", dry_run or onboard.MARK_OPEN not in new,
                   f"grep -c 'space:personal-instruction' {ui}", str(ui))
    else:
        report.add("personal instruction removed", True, f"test ! -f {ui}", "no user-level file")
    if ui is not None:
        settings = ui.parent / "settings.json"
        if not dry_run:
            onboard.disallow_directory(settings, root)
        report.add("workspace no longer allowed", dry_run or not onboard.directory_allowed(settings, root),
                   f"grep -c additionalDirectories {settings}", str(settings))
    repo = state.get("repo") or onboard.git_remote(root)
    hand_made = None
    if online and repo and REPO.fullmatch(str(repo)):   # before the archive, which freezes the secrets too
        hand_made = [n for n in remove_secrets(report, repo, dry_run) if n in HAND_MADE]
    if online and repo:
        res = run_cmd(["gh", "repo", "archive", repo, "-y"], dry_run)
        verify = run_cmd(["gh", "repo", "view", repo, "--json", "isArchived", "-q", ".isArchived"], dry_run)
        report.add("repository archived", res.returncode == 0 and (dry_run or verify.stdout.strip() == "true"),
                   f"gh repo view {repo} --json isArchived -q .isArchived", res.stderr.strip()[:200] or repo)
    elif online:
        report.add("repository archived", False, "gh repo view <repo> --json isArchived",
                   "no origin remote; archive it by hand", needs_owner=True)
    else:
        report.add("repository archived", True, "gh repo view <repo> --json isArchived", "offline: not attempted")
    reg = registry.Registry(root, space)
    archived = []
    for subject in reg.subjects.values():
        if subject.home == space.name and subject.state != "archived":
            subject.state = "archived"
            archived.append(subject.id)
    if archived and not dry_run:
        reg.save()
    # a subject homed here that another registry holds: that registry hears it from its home
    held = sorted(sid for sid, s in reg.ancestors.items()
                  if sid not in reg.subjects and s.state in ("active", "dormant") and reg.home_of(sid) == space.name)
    notes = 0
    if held and not dry_run:
        import classify
        import release
        for sid in held:
            notes += classify.send_state(root, sid, "archived", now) is not None
        if online or offline_roots:
            release.ship(root, now, offline_roots=offline_roots, notes_only=True)
    archived += held
    waiting = len(list((root / "_outbound" / "signals").glob("*.yaml"))) if notes else 0
    report.add("registry archived", True, "python _meta/tools/registry.py . check",
               (", ".join(archived) or "no subject has its home here")
               + (f"; {waiting} signals wait for the next ship" if waiting else ""))
    access_steps(report, root, space, hand_made)
    if not dry_run:
        onboard.write_yaml(root / "_meta" / "state" / "offboard.yaml",
                           {"at": pages.iso(now), "space": space.name, "counts": counts, "archived": archived,
                            "steps": [s.row() for s in report.steps]})
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--online", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--forget-records", action="store_true",
                    help="the person's own command: remove the raw feed files and witness pages")
    ap.add_argument("--person", metavar="SLUG",
                    help="in the brain, by its owner: archive this person and list the access to remove")
    ap.add_argument("--remove", action="store_true",
                    help="with --person --online, after the owner's yes that they left: remove that access and read it back")
    args = ap.parse_args(argv)
    if args.remove and not (args.person and args.online):
        print("offboard: --remove goes with --person and --online")
        return 2
    if args.person:
        try:
            report = leaver(args.root, args.person, online=args.online, dry_run=args.dry_run, remove=args.remove)
        except ValueError as exc:
            print(f"offboard: {exc}")
            return 1
    else:
        report = run(args.root, args.online, dry_run=args.dry_run, forget_records=args.forget_records)
    onboard.print_steps(report.steps)
    for step in report.steps:
        if not step.done and step.needs_owner:
            print(f"yours: {step.name}: {step.result}")
    return 1 if report.failed() else 0


if __name__ == "__main__":
    sys.exit(main())
