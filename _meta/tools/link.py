"""link: which subject a codebase belongs to (docs/design/2026-10-22-codebase-link-design.md).

A codebase is linked to a subject on the subject's entry in the registry that holds it (`repos`,
registry.py). `resolve` reads the git remote of a folder (a subfolder or a worktree works too),
finds the one active link in this space's registry and every mounted one, and prints the
subject, its parent, its home, the home's audience and left-out kinds, the codebase's readers
and the subject's status page as JSON. It never touches the network. The first time it meets a
repository nothing links, it scores the repository's owner and name against the subjects this
space's owner may link: one clear match is linked at once (the undo is logged), a tie becomes one
Needs-you item, no match leaves it unlinked. Every decision is remembered
(`_meta/state/links-seen.jsonl`), so no repository is asked about twice; a `yes` to the question
(or `edit <subject>` for another) links it at the next resolve, scan or check. One that matched
nothing leaves no trace and is scored again, so a subject registered later still claims it. A link to a registry
this space does not hold travels as a registry note (classify.py) and counts as pending until
that registry has applied it.

`scan` (onboarding, project creation) runs that placement over every repository the person's
account and this space's organisation list on the host, leaving out the spaces' own repositories.
`check` (the daily chain) keeps the links homed in this space: it follows a renamed repository by
its name, else its id (the old name still resolves, so old clones keep working, until another repository takes
that name), marks a lost one (found before) `gone` and
brings it back when the host finds it again, sets the readers against the home's audience (a
public repository is always wider), keeps the older of two claims wherever each is homed, applies
a link question answered since, and lists the linked repositories for the space's repository
feed in `_meta/state/links.json` (space.yaml is protected); the health line shows `links N,
wider M, repaired K`. Without the host (no `gh`, signed out, a fixture space) both skip their host calls
and try again next run.

Entries point into a codebase with a `ref:` line: `owner/repo:path@commit`, `owner/repo@commit`
or a pull request URL. In a personal space, an entry about a subject homed elsewhere that points
into a codebase linked to that subject (or its parent) becomes a `journal` feed record, pointers
first; the sweep sends it to the home after the window, the router at once when the home is
walled and its crew includes the writer (D8). Every other entry stays home.

    python link.py <root> resolve [--cwd DIR]   the link as JSON; exit 3 when there is none
    python link.py <root> link <repo> <subject>  link a codebase detection cannot place (owner/name,
                                                 its clone URL or the clone's folder)
    python link.py <root> unlink <repo>          undo a link
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import access  # noqa: E402
import pages  # noqa: E402
import registry  # noqa: E402
import spaceyaml  # noqa: E402

# {repo, at, outcome: linked|asked|unlinked, subject}, plus repo_id, was and the note's signal id
# when it travelled to another registry; an `asked` row's subject is the recommended one
SEEN = Path("_meta") / "state" / "links-seen.jsonl"
NEEDS_YOU = Path("_inbound") / "needs-you" / "links.yaml"
STATE = Path("_meta") / "state" / "links.json"   # the last check: {at, links, wider, repaired, repos, audience}
JOURNAL_FEED = Path("_meta") / "state" / "feed" / "journal.jsonl"   # journal entries that travel (D8)
REF_LINE = re.compile(r"^\s*(?:[-*]\s*)?ref:\s*`?(\S+?)`?\s*$", re.I | re.M)
# a lesson's other subject, on its own line or after the lesson: ring 3, never travels (distill reads it too)
FROM_LINE = re.compile(r"(?:^\s*(?:[-*]\s*)?|[\s;,(])from:\s*`?([\w-]*)", re.I | re.M)
STRUCTURED = re.compile(r"^\s*(?:[-*]\s*)?(?:subject|ref|lesson|from):.*$", re.I | re.M)   # an entry ends here
REF_PR = re.compile(r"^https?://github\.com/([\w.-]+/[\w.-]+)/pull/\d+/?$", re.I)
REF_AT = re.compile(r"^([A-Za-z0-9][\w-]*/[\w.-]+?)(?::([^@\s]+))?@([\w.-]+)$")
LOCAL = re.compile(r"^(?:[a-z]:|[\\/.~]|file:)", re.I)   # a folder, not a repository


@dataclass
class Link:
    subject: str
    repo: str                  # owner/name
    repo_id: str | None
    readers: str               # "inside" | "wider"; "wider" when never set
    checked: str | None        # the day the check last found it
    linked: str | None
    gone: str | None
    pending: bool              # decided here (SEEN), not yet in the holder's registry
    was: tuple = ()            # names the repository had before a rename: an old clone still finds it


def repo_key(value) -> str | None:
    """Lower-case owner/name for a GitHub repository, else the URL lower-cased without `.git`.
    A URL's user info (a token, `git@`) and port are dropped, so a token is never written
    anywhere, and every form of one remote is one key: `ssh://`, `git://` and the scp form
    `git@host:owner/name` count as the https URL, `ssh.github.com` as GitHub."""
    text = str(value or "").strip()
    text = re.sub(r"^[a-z][\w+.-]*://(?:[^/]*@)?([^/:]+)(?::\d+)?/", r"https://\1/", text, flags=re.I)
    text = re.sub(r"^[^/@:\s]+@([^/:\s]+):(?!/)", r"https://\1/", text)   # scp form
    url = registry.repo_url(re.sub(r"^https://ssh\.github\.com/", "https://github.com/", text, flags=re.I))
    if not url:
        return None
    url = url.lower().rstrip("/").removesuffix(".git")
    return url.removeprefix("https://github.com/")


def _parts(repo) -> list:
    """[owner, name] of a repository reference."""
    return (["", ""] + re.split(r"[/:]", repo_key(repo) or ""))[-2:]


def remote_of(path, name: str = "origin") -> str | None:
    """The remote `name` of the repository holding `path`, as repo_key, read from its config
    without calling git. A `.git` file (a worktree, a submodule) is followed to its config. A
    space's own checkout is not a codebase: None (the template repository is one), and neither is
    a clone of a folder."""
    folder = Path(path).resolve()
    for here in [folder, *folder.parents]:
        dot = here / ".git"
        if dot.exists() and (here / "space.yaml").is_file() and not (here / spaceyaml.TEMPLATE_MARKER).exists():
            return None
        if dot.is_dir():
            config = dot / "config"
        elif dot.is_file():
            found = re.search(r"^gitdir:\s*(.+?)\s*$", dot.read_text(encoding="utf-8", errors="replace"), re.M)
            if not found:
                return None
            gitdir = Path(found.group(1))
            gitdir = gitdir if gitdir.is_absolute() else here / gitdir
            common = gitdir / "commondir"
            if common.is_file():
                shared = Path(common.read_text(encoding="utf-8", errors="replace").strip())
                gitdir = shared if shared.is_absolute() else gitdir / shared
            elif gitdir.parent.name == "worktrees":
                gitdir = gitdir.parent.parent
            config = gitdir / "config"
        else:
            continue
        if not config.is_file():
            return None
        text = config.read_text(encoding="utf-8", errors="replace")
        match = re.search(r'\[remote "' + re.escape(name) + r'"\][^\[]*?^[ \t]*url[ \t]*=[ \t]*(\S+)', text, re.M)
        return repo_key(_unalias(match.group(1))) if match and not LOCAL.match(match.group(1)) else None
    return None


def _unalias(url: str) -> str:
    """An SSH remote through a host alias (`git@github-work:owner/name` or `git@github.com-work:...`,
    the usual two-account setup) with the alias's `HostName` from ~/.ssh/config; any other remote
    as it is."""
    found = re.match(r"^(?:ssh://)?[^/@:\s]+@([^/:\s]+)(?=[:/])", url)
    if not found:
        return url
    try:
        text = (Path.home() / ".ssh" / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return url
    hosts = []
    for line in text.splitlines():
        key, _, value = line.strip().replace("=", " ", 1).replace("\t", " ").partition(" ")
        if key.lower() == "host":
            hosts = value.split()
        elif key.lower() == "hostname" and found.group(1) in hosts:
            return url[:found.start(1)] + value.strip() + url[found.end(1):]
    return url


def _append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line.rstrip("\n") + "\n")


def _seen_rows(root) -> dict:
    """The newest SEEN row per repository, by repo_key."""
    return {repo_key(row.get("repo")): row for row in registry._jsonl(Path(root) / SEEN) if repo_key(row.get("repo"))}


def _arrived(space, holder: str, signal) -> bool:
    """The registry mounted as `holder` has applied or refused the note `signal`."""
    holder_root = space.mount_root(holder) if space is not None and holder else None
    if holder_root is None:
        return False
    return str(signal) in {str(r.get("id")) for r in registry._jsonl(holder_root / registry.NOTES_LOG)}


def links(root) -> list:
    """Every link: this space's registry first, then the mounted ones in mount order, then the
    links decided here that the holder has not applied yet. A newer unlink decided here hides
    the repository's link until the holder has applied it, and a newer link decided here
    replaces whatever the holder shows for that repository until then."""
    root = Path(root)
    reg = registry.Registry(root)
    out = []
    for subject in reg.everywhere():
        for entry in subject.extra.get("repos") or []:
            if not isinstance(entry, dict) or not str(entry.get("repo") or "").strip():
                continue
            day = {k: str(entry[k]) if entry.get(k) else None for k in ("checked", "linked", "gone")}
            out.append(Link(subject=subject.id, repo=str(entry["repo"]), repo_id=entry.get("repo_id"),
                            readers=entry.get("readers") or "wider", pending=False,
                            was=tuple(str(w) for w in entry.get("was") or [] if w), **day))
    for key, row in _seen_rows(root).items():
        subject = reg.get(str(row.get("subject") or ""))
        if subject is None or not row.get("signal") or _arrived(reg.space, subject.space, row["signal"]):
            continue
        if row.get("outcome") == "unlinked":
            out = [l for l in out if repo_key(l.repo) != key]
        elif row.get("outcome") == "linked" and not any(
                l.subject == subject.id and repo_key(l.repo) == key and not l.gone for l in out):
            out = [l for l in out if repo_key(l.repo) != key]
            out.append(Link(subject=subject.id, repo=str(row["repo"]), repo_id=row.get("repo_id"),
                            readers="wider", checked=None, linked=str(row.get("at") or "")[:10] or None,
                            gone=None, pending=True, was=tuple(str(w) for w in row.get("was") or [] if w)))
    return out


def link_for(root, repo) -> Link | None:
    """The one active link for the repository: not gone, not unlinked. The older `linked` wins
    (missing counts as newest), then this space before its mounts, in mount order. A name the
    repository had before a rename finds it too, when no link has that name now."""
    key = repo_key(repo)
    every = [l for l in links(root) if key and not l.gone]
    active = [l for l in every if repo_key(l.repo) == key] or [l for l in every if key in map(repo_key, l.was)]
    return min(active, key=lambda l: str(l.linked or "9999-99-99"), default=None)


def _tokens(*texts) -> set:
    return {t for text in texts for t in re.findall(r"[a-z0-9]{3,}", str(text or "").lower())}


def score(repo: str, subject, reg) -> int:
    """How well the repository's owner and name fit the subject: 2 per name word in the subject's
    names, 1 per owner word in its names or its client's, 1 more when another of its links has
    the same owner. People, archived subjects, those homed nowhere (no one would read the
    codebase) and those this space's owner may not link score 0."""
    import classify
    if subject.kind == "people" or subject.state == "archived":
        return 0
    owner, name = _parts(repo)
    names = _tokens(subject.id, subject.title, *subject.aliases)
    client = reg.get(subject.client) if subject.client else None
    clients = _tokens(subject.client, *(client.names() if client else []))
    same_owner = any(isinstance(e, dict) and not e.get("gone") and _parts(e.get("repo"))[0] == owner
                     for e in subject.extra.get("repos") or [])
    total = 2 * len(_tokens(name) & names) + len(_tokens(owner) & (names | clients)) + (1 if same_owner else 0)
    space = reg.space
    if total and not classify.may_link(reg, subject, space.owner if space else "", space.name if space else ""):
        return 0   # a link this space sends is never refused at the holder
    if total and space is not None and _home_name(space, reg, subject) is None:
        return 0
    return total


def _log(root: Path, now, text: str) -> None:
    _append(root / "log.md", f"- {pages.iso(now)} {text}")


def _remember(root: Path, repo: str, now, outcome: str, subject=None, **extra) -> None:
    _append(root / SEEN, json.dumps({"repo": repo, "at": pages.iso(now), "outcome": outcome, "subject": subject,
                                     **{k: v for k, v in extra.items() if v}}))


def _send(root: Path, repo: str, sid: str, data: dict, now) -> str | None:
    """The link note; the id of the signal when it travels to another registry."""
    import classify
    path = classify.note(root, sid, "link", dict(data, repo=repo), now=now)
    return path.stem if path else None


def _slug(repo: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", repo_key(repo) or repo).strip("-")


def _ask(root: Path, space, repo: str, ids: list) -> None:
    named = ", ".join(ids[:-1]) + f" or {ids[-1]}"
    _needs_you(root, {"id": "link-" + _slug(repo), "kind": "question",
                      "for": space.owner, "text": f"{repo} could belong to {named}.",
                      "recommendation": f"yes: link it to {ids[0]} (edit <subject> for another)",
                      "default": "nothing is linked", "depends": 0})


def _needs_you(root: Path, item: dict | None, drop: str | None = None) -> None:
    """Add or replace one item (by id) in this space's links Needs-you file, or `drop` one."""
    import yaml
    path = root / NEEDS_YOU
    if item is None and not path.exists():
        return
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    gone = drop or item["id"]
    items = [i for i in ((data or {}).get("items") or []) if isinstance(i, dict) and i.get("id") != gone]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"items": items + ([item] if item else [])}, sort_keys=False, allow_unicode=True),
                    encoding="utf-8", newline="\n")


def detect(root, repo: str, now=None, id_fn=None) -> dict:
    """Place a repository nothing links yet: one clear match is linked, a tie is asked once, no
    match leaves it unlinked; a link or a question is a SEEN row. A clear match scores 3, or 2
    for a repository in this space's own organisation (the owner of its remote), where a name
    word alone is the company's own naming. A repository already linked (under this name or one
    it had) or unlinked is `known`, and nothing is written; so is one asked about, until the
    person answers: `yes` links the recommended subject, `edit <subject>` the one named. One
    that matched nothing leaves no trace, so a subject registered later still claims it."""
    import classify
    import onboard
    import status
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    key = repo_key(repo)
    row = _seen_rows(root).get(key)
    known = {"outcome": "known", "subject": None, "candidates": []}
    if any(key in {repo_key(l.repo), *map(repo_key, l.was)} for l in links(root)):
        return known
    if row is not None:
        verdict = status.verdicts(root).get("link-" + _slug(repo)) or {}
        verb = verdict.get("verb")
        sid = row.get("subject") if verb in ("yes", "always") else (
            str(verdict.get("note") or "").strip() if verb == "edit" else None)
        if row.get("outcome") != "asked" or not sid:
            return known
        try:
            link(root, repo, sid, now=now)
        except (ValueError, classify.ClassifyError):
            return known
        return {"outcome": "linked", "subject": sid, "candidates": [sid]}
    reg = registry.Registry(root, space)
    scored = [(s, score(repo, s, reg)) for s in reg.everywhere()]
    top = max((n for _, n in scored), default=0)
    remote = onboard.git_remote(root)
    need = 2 if remote and _parts(repo)[0] == remote.split("/")[0].lower() else 3
    contenders = [s.id for s, n in sorted(scored, key=lambda p: -p[1]) if n >= need and n >= top - 1]
    if len(contenders) == 1:
        sid = contenders[0]
        data = {"linked_by": "detect", "linked": now.date().isoformat()}
        repo_id = id_fn(repo) if id_fn else None
        if repo_id:
            data["repo_id"] = repo_id
        try:
            signal = _send(root, repo, sid, data, now)
        except classify.ClassifyError:
            contenders = []
        else:
            _log(root, now, f"linked {repo} to {sid} (detect); undo: python _meta/tools/link.py . unlink {repo}")
            _remember(root, repo, now, "linked", sid, repo_id=repo_id, signal=signal)
            return {"outcome": "linked", "subject": sid, "candidates": contenders}
    if len(contenders) > 1:
        _ask(root, space, repo, contenders)
        _remember(root, repo, now, "asked", contenders[0])
        return {"outcome": "asked", "subject": None, "candidates": contenders}
    return {"outcome": "none", "subject": None, "candidates": []}


def _home(root: Path, space, name: str | None):
    """(root, space) of the space named `name`: this one or a mount; (None, None) when unreadable."""
    if not name:
        return None, None
    if name == space.name:
        return root, space
    for mount in space.mounts:
        mount_root = space.mount_root(mount.get("name", ""))
        if mount_root is None:
            continue
        try:
            mounted = spaceyaml.load(mount_root)
        except Exception:
            continue
        if name in (mounted.name, mount.get("name")):
            return mount_root, mounted
    return None, None


def _home_name(space, reg, subject) -> str | None:
    """The space a subject is homed in: its home, else the space whose registry holds it. A
    client, project or deal the brain holds with no home of its own is homed nowhere: the brain
    reads its codebase for no one."""
    import classify
    home = reg.home_of(subject.id)
    if home:
        return home
    holder = classify._holder_name(space, subject) or space.name
    brain = classify.brain_space(space)
    if brain is not None and holder == brain.name and classify.gated(reg, subject.id):
        return None
    return holder


def resolve(root, cwd, now=None) -> dict | None:
    """The subject the codebase at `cwd` belongs to, with its home's audience, its readers and its
    status page; None when it is not linked. A fork's clone finds the link of its `upstream`. No
    network: a repository nothing links runs `detect`, so one that matched nothing is scored
    again on each resolve."""
    root = Path(root)
    repo = remote_of(cwd)
    if not repo:
        return None
    found = link_for(root, repo) or link_for(root, remote_of(cwd, "upstream"))
    if found is None and detect(root, repo, now=now)["outcome"] == "linked":
        found = link_for(root, repo)
    if found is None:
        return None
    space = spaceyaml.load(root)
    reg = registry.Registry(root, space)
    subject = reg.get(found.subject)
    if subject is None:
        return None
    home = _home_name(space, reg, subject)
    home_root, home_space = _home(root, space, home)
    page = Path("live") / "subjects" / f"{subject.id}.md"
    status_page = next((str((r / page).resolve()) for r in (home_root, root) if r is not None and (r / page).is_file()),
                       None)
    return {"subject": subject.id, "title": subject.title, "parent": subject.part_of, "home": home,
            "audience": access.audience_names(home_root, home_space) if home_space is not None else None,
            "readers": found.readers,
            "left_out": home_space.left_out if home_space is not None else list(spaceyaml.DEFAULTS["left_out"]),
            "status_page": status_page, "repo": found.repo}


def link(root, repo: str, sid: str, now=None) -> str:
    """The person's own link, for a codebase detection cannot place: the repository as owner/name,
    its clone URL or its clone's folder (keyed by its remote, as resolve keys it); returns the key
    stored. A repository with no active link (a gone one under this name may be a new repository)
    starts without the old id, readers and check; one unlinked here keeps the names it had
    before a rename."""
    import classify
    given = str(repo).strip()
    if LOCAL.match(given):
        repo = remote_of(given) or ""
        if not repo:
            raise ValueError(f"{given} is no clone of a repository")
    repo = repo_key(repo) or repo
    if "://" not in repo and not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError(f"{repo} is not a repository; give it as owner/name, its clone URL or the clone's folder")
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    reg = registry.Registry(root, space)
    subject = reg.get(sid)
    if subject is not None and not classify.may_link(reg, subject, space.owner, space.name):
        raise ValueError(f"only the subject's crew may link a repository to {sid}")
    current = link_for(root, repo)
    if current is not None and current.subject != sid:
        raise ValueError(f"{repo} is linked to {current.subject}; unlink it first")
    repo = current.repo if current is not None else repo   # a name it had before a rename: the same link
    data = {"linked_by": "person", "linked": now.date().isoformat(), "gone": None, "checked": None}
    row = _seen_rows(root).get(repo_key(repo)) or {}
    was = []
    if current is None:
        data.update(repo_id=None, readers=None)
        if row.get("outcome") == "unlinked":
            was = [w for w in row.get("was") or [] if not registry.same_repo(w, repo)]
    if was:
        data["was"] = was
    signal = _send(root, repo, sid, data, now)
    _log(root, now, f"linked {repo} to {sid} (person); undo: python _meta/tools/link.py . unlink {repo}")
    _remember(root, repo, now, "linked", sid, signal=signal, was=was)
    _needs_you(root, None, drop="link-" + _slug(repo))
    return repo


def unlink(root, repo: str, now=None) -> None:
    repo = repo_key(repo) or repo
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    current = link_for(root, repo)
    if current is None:
        raise ValueError(f"{repo} is not linked")
    repo = current.repo   # a name it had before a rename: the same link
    signal = _send(root, repo, current.subject,
                   {"remove": True, **({"repo_id": current.repo_id} if current.repo_id else {})}, now)
    _log(root, now, f"unlinked {repo} from {current.subject}; "
                    f"undo: python _meta/tools/link.py . link {repo} {current.subject}")
    for name in (repo, *current.was):   # an old clone does not link it again; a new link keeps the names
        _remember(root, name, now, "unlinked", current.subject, signal=signal, was=[repo, *current.was])


# ----- the host: what `scan` and `check` ask of it (`gh`); tests inject their own -----------------
def _gh(*args) -> tuple:
    """(exit code, stdout, stderr) of one `gh` call; OSError when it cannot run at all."""
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise OSError("gh timed out") from exc
    return done.returncode, done.stdout or "", done.stderr or ""


def _answer(code: int, out: str, err: str):
    """The JSON a `gh` call printed; None when the host says it is not there; OSError for any
    other failure (signed out, rate limited), so nothing is marked gone on a bad day."""
    if code != 0:
        if re.search(r"HTTP 404|Could not resolve to a node", err, re.I):
            return None
        raise OSError(err.strip()[:200] or "gh failed")
    try:
        return json.loads(out) if out.strip() else None
    except ValueError as exc:
        raise OSError("gh printed no JSON") from exc


def _host_repos(owner: str) -> list | None:
    try:
        code, out, _ = _gh("repo", "list", owner, "--limit", "200", "--json", "nameWithOwner",
                           "--jq", ".[].nameWithOwner")
    except OSError:
        return None
    return [line.strip() for line in out.splitlines() if line.strip()] if code == 0 else None


def _host_id(repo: str) -> str | None:
    try:
        code, out, _ = _gh("api", f"repos/{repo_key(repo)}", "--jq", ".node_id")
    except OSError:
        return None
    return (out.strip() or None) if code == 0 else None


def _host_lookup(repo: str, repo_id=None) -> dict | None:
    """The repository by name (the host follows a rename), else by its id; None only when both
    say it is not there. OSError when the host cannot be asked (no `gh`, signed out, a repository
    not on GitHub), so the link is kept as it is."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo_key(repo) or ""):
        raise OSError("not a GitHub repository")
    found = _answer(*_gh("api", f"repos/{repo_key(repo)}", "--jq",
                         "{repo: .full_name, repo_id: .node_id, private: .private}"))
    if found and (not repo_id or found.get("repo_id") == repo_id):
        return found
    if not repo_id:
        return None
    node = _answer(*_gh("api", "graphql", "-f", "query=query($id: ID!) { node(id: $id) { "
                        "... on Repository { nameWithOwner id isPrivate } } }", "-f", f"id={repo_id}",
                        "--jq", ".data.node"))
    if not node or not node.get("nameWithOwner"):
        return None
    return {"repo": node["nameWithOwner"], "repo_id": node["id"], "private": node.get("isPrivate")}


def _space_repos(space, reg, remote) -> set:
    """The repositories that are spaces, not codebases: this space's own, each mount's, and any in
    this space's organisation named after a space known here (a mount, a subject's home)."""
    names = {space.name} | {reg.home_of(s.id) for s in reg.everywhere()}
    keys = {repo_key(remote)}
    for mount in space.mounts:
        names.add(mount.get("name"))
        keys.add(repo_key(mount.get("repo")))
    org = remote.split("/")[0] if remote else None
    keys |= {repo_key(f"{org}/{n}") for n in names if org and n}
    return keys - {None}


def scan(root, now=None, list_fn=None, id_fn=None) -> dict:
    """Place every repository the person's account and this space's organisation list on the host
    (`detect` on each), leaving out the repositories that are spaces. A host that does not answer
    skips the scan; a session's first `resolve` still places what it missed."""
    import onboard
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    out = {"seen": 0, "linked": 0, "asked": 0, "skipped": ""}
    fixture = space.mode == "fixture"
    if list_fn is None and fixture:
        out["skipped"] = "fixture"
        return out
    list_fn = list_fn or _host_repos
    id_fn = id_fn or (None if fixture else _host_id)
    remote = onboard.git_remote(root)
    owners = [o for o in dict.fromkeys([access.logins(root, space).get(space.owner),
                                        remote.split("/")[0] if remote else None]) if o]
    if not owners:
        out["skipped"] = "no account to list"
        return out
    spaces = _space_repos(space, registry.Registry(root, space), remote)
    listed: dict = {}   # repo_key -> the name as the host lists it
    for owner in owners:
        repos = list_fn(owner)
        if repos is None:
            out["skipped"] = f"the host did not list the repositories of {owner}"
            return out
        for repo in repos:
            listed.setdefault(repo_key(repo), repo)
    listed.pop(None, None)
    for key, repo in listed.items():
        out["seen"] += 1
        if key in spaces:
            continue
        outcome = detect(root, repo, now=now, id_fn=id_fn)["outcome"]
        if outcome in ("linked", "asked"):
            out[outcome] += 1
    return out


def check(root, now=None, lookup_fn=None, readers_fn=None) -> dict:
    """Keep the links homed in this space, wherever the registry entry sits: every change is a
    link note (applied at once here, else sent to the holder, which keeps one entry per
    repository). A renamed repository is followed in one note and keeps its old names in `was`
    until another repository takes one, a lost one is marked gone (one this check never found, such as
    a client's private repository, is kept) and comes back when found again,
    readers are set against this space's audience (a public repository is wider), the older of two
    claims stays, a link question answered since is applied, and STATE lists what is linked for
    the repository feed. Never an owner alert."""
    import classify
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    today = now.date().isoformat()
    fixture = space.mode == "fixture"   # a fixture space never asks the host with the default functions
    lookup = lookup_fn or (None if fixture else _host_lookup)
    readers_of = readers_fn or (None if fixture else access.readers)
    for row in list(_seen_rows(root).values()):   # a question answered since: detect applies the answer
        if row.get("outcome") == "asked":
            detect(root, row["repo"], now=now)
    reg = registry.Registry(root, space)
    repaired = 0

    def send(found: Link, repo: str, **data) -> bool:
        try:
            _send(root, repo, found.subject, dict({"repo_id": found.repo_id} if found.repo_id else {}, **data), now)
        except classify.ClassifyError:
            return False   # one link the registry refuses never stops the others
        return True

    def homed_here(found: Link) -> bool:
        subject = reg.get(found.subject)
        return subject is not None and not found.pending and _home_name(space, reg, subject) == space.name

    def taken(name: str, repo_id) -> bool:
        """Another repository has the old name now: the host finds it under an id not this link's."""
        try:
            there = lookup(name, None) if repo_id else None
        except OSError:
            return False   # the host cannot be asked: the name stays, asked again next run
        return bool(there and there.get("repo_id") and there["repo_id"] != repo_id)

    for found in links(root) if lookup is not None else []:   # a gone link the host finds again comes back
        if found.gone and homed_here(found):
            try:
                there = lookup(found.repo, found.repo_id)
            except OSError:
                continue
            if there:
                repo = found.repo
                fill = {"repo_id": there["repo_id"]} if not found.repo_id and there.get("repo_id") else {}
                if there.get("repo") and not registry.same_repo(there["repo"], repo):   # renamed meanwhile: followed
                    repo = str(there["repo"])
                    fill["was"] = [w for w in dict.fromkeys([*found.was, found.repo]) if not registry.same_repo(w, repo)]
                repaired += send(found, repo, gone=None, **fill)
    claims: dict = {}   # repo_key -> subject -> Link, over every link seen here, wherever it is homed
    for found in links(root):
        if not found.gone and not found.pending:
            claims.setdefault(repo_key(found.repo), {}).setdefault(found.subject, found)
    kept = []
    for by_subject in claims.values():   # two subjects claim one repository: the older stays, the same in every home
        first, *rest = sorted(by_subject.values(), key=lambda l: (l.linked or "9999-99-99", l.subject))
        rest = [other for other in rest if homed_here(other)]
        if homed_here(first):
            kept.append(first)
        repaired += sum(send(other, other.repo, remove=True) for other in rest)
        if rest:
            names = [first.subject] + [o.subject for o in rest]
            other = names[1] if len(rest) == 1 else "<subject>"
            _needs_you(root, {"id": "link-claim-" + _slug(first.repo), "kind": "question", "for": space.owner,
                              "text": f"{first.repo} was linked to {', '.join(names[:-1])} and {names[-1]}; "
                                      f"I kept {first.subject}, linked first.",
                              "recommendation": f"keep {first.subject}; to give it to {other} instead: "
                                                f"python _meta/tools/link.py . unlink {first.repo}, then "
                                                f"python _meta/tools/link.py . link {first.repo} {other}",
                              "default": f"{first.subject} keeps it", "depends": 0})
    known = access.logins(root, space)
    allowed = {known[name].lower() for name in access.audience_names(root, space) if name in known}
    final = []   # (subject, repo, readers) of the links that stay
    for found in kept:
        repo, data, public, dropped = found.repo, {}, False, False
        if lookup is not None:
            try:
                there = lookup(found.repo, found.repo_id)
            except OSError:
                final.append((found.subject, repo, found.readers))   # the host cannot be asked: retried next run
                continue
            if there is None:
                if found.checked:   # found before, not now: lost
                    repaired += send(found, repo, gone=today)
                else:   # never found from here (a client's private repository): kept as it is
                    final.append((found.subject, repo, found.readers))
                continue
            public = there.get("private") is False   # anyone can read it: wider, whoever the collaborators are
            if not found.checked:
                data["checked"] = today
            if not found.repo_id and there.get("repo_id"):
                data["repo_id"] = there["repo_id"]
            if there.get("repo") and not registry.same_repo(there["repo"], repo):   # renamed: one note follows it
                repo = str(there["repo"])
                data["was"] = [w for w in dict.fromkeys([*found.was, found.repo]) if not registry.same_repo(w, repo)]
            was = data.get("was", list(found.was))
            mine = [w for w in was if not taken(w, found.repo_id or there.get("repo_id"))]
            if mine != was:   # a new repository took an old name: its clones are not this link's
                data["was"], dropped = mine or None, True
        readers = found.readers
        logins = None if public or readers_of is None else readers_of(repo)
        if public or logins is not None:
            readers = "inside" if not public and all(str(x).lower() in allowed for x in logins) else "wider"
            if readers != found.readers or not found.checked:
                data.update(readers=readers, checked=today)
        if (data or repo != found.repo) and not send(found, repo, **data):
            repo, readers = found.repo, found.readers   # refused: as it is
        elif repo != found.repo or dropped:
            repaired += 1
        final.append((found.subject, repo, readers))
    pairs, spelling = {}, {}   # a repository counts once per subject; wider when either entry says so
    for sid, repo, readers in final:
        key = (sid, repo_key(repo))
        spelling.setdefault(key[1], repo)
        pairs[key] = "wider" if "wider" in (readers, pairs.get(key)) else readers
    counts = {"links": len(pairs), "wider": sum(r == "wider" for r in pairs.values()), "repaired": repaired}
    # what the repository feed reads (sweep skill), kept here: space.yaml is protected, links are routine state
    data = dict(counts, repos=sorted(spelling.values()),
                audience={"brain": "everyone", "project": "crew"}.get(space.profile, "owner"))
    path = root / STATE
    try:
        before = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        before = None
    if not isinstance(before, dict) or {k: before.get(k) for k in data} != data:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": pages.iso(now), **data}) + "\n", encoding="utf-8", newline="\n")
    return counts


def health_cell(root) -> str:
    """The health line's cell from the last check: `links N, wider M, repaired K`."""
    try:
        data = json.loads((Path(root) / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "links 0"
    return f"links {data.get('links', 0)}, wider {data.get('wider', 0)}, repaired {data.get('repaired', 0)}"


# ----- pointers: `ref:` lines in journal entries ------------------------------------------------
def refs_in(text: str) -> list:
    return REF_LINE.findall(text or "")


def ref_repo(ref: str) -> str | None:
    """owner/name from `owner/repo:path@commit`, `owner/repo@commit` or a pull request URL."""
    found = REF_PR.match(str(ref or "")) or REF_AT.match(str(ref or ""))
    return found.group(1) if found else None


def ref_url(ref: str) -> str | None:
    """The pointer's page: the file at the commit, the commit, or the pull request as given."""
    ref = str(ref or "")
    if REF_PR.match(ref):
        return ref
    found = REF_AT.match(ref)
    if not found:
        return None
    repo, path, commit = found.groups()
    return f"https://github.com/{repo}/blob/{commit}/{path}" if path else f"https://github.com/{repo}/commit/{commit}"


# ----- notes from a codebase session travel as records (D8) --------------------------------------
def _ref_link(root, ref: str, subjects) -> Link | None:
    """The link of one of `subjects` a pointer goes into: the repository's own link, or, for a
    codebase on another host (linked by its URL), one with the same owner/name."""
    name = ref_repo(ref)
    if not name:
        return None
    found = link_for(root, name)
    if found is not None:
        return found if found.subject in subjects else None
    return next((l for l in links(root) if not l.gone and l.subject in subjects
                 and "://" in (repo_key(l.repo) or "") and _parts(l.repo) == _parts(name)), None)


def journal_records(root) -> int:
    """Append a `journal` feed row for each journal entry about a subject homed elsewhere that
    carries a `ref:` into a codebase linked to that subject or its parent; the rows appended.
    Personal spaces only; an entry already in the feed is not written again. Only those pointers
    travel, first, since the sweep sends a body cut short; a pointer into a codebase on another
    host travels with that host's URL in place of owner/name, so no GitHub page is made of it.
    Any other `ref:` (another subject's code, a private path) stays home, as do the `lesson:` and
    `from:` lines (a lesson from another subject, ring 3; D5, D6: distill takes the lesson to the
    brain through the scrub and a signer) and whatever follows the entry's last `subject:`,
    `ref:`, `lesson:` or `from:` line. A row is `admitted`: the sweep takes it however old its
    heading time, since an entry can be written or become eligible after a newer one was swept."""
    import distill
    root = Path(root)
    space = spaceyaml.load(root)
    if space.profile != "personal":
        return 0
    reg = registry.Registry(root, space)
    have = {str(row.get("id")) for row in registry._jsonl(root / JOURNAL_FEED)}
    written = 0
    for entry in distill.journal_evidence(root):
        sid = entry.subjects[0]
        subject = reg.get(sid)
        home = reg.home_of(sid)
        if entry.record in have or subject is None or not home or home == space.name:
            continue
        refs = []
        for ref in refs_in(entry.body):
            found = _ref_link(root, ref, (sid, subject.part_of))
            if found is None:
                continue
            key = repo_key(found.repo) or ""   # on another host: its URL, which no GitHub link is made of
            refs.append(key + ref[len(ref_repo(ref)):] if "://" in key and REF_AT.match(ref) else ref)
        if not refs:
            continue
        last = list(STRUCTURED.finditer(entry.body))
        body = entry.body[:last[-1].end()] if last else entry.body
        rest = "\n".join(line for line in body.splitlines() if not REF_LINE.match(line)
                         and not FROM_LINE.search(line) and not distill.LESSON_LINE.match(line)).strip()
        text = "\n".join(f"ref: {r}" for r in refs) + ("\n\n" + rest if rest else "") + "\n"
        _append(root / JOURNAL_FEED, json.dumps({
            "id": entry.record, "source": "journal", "kind": "document", "at": pages.iso(entry.at),
            "subject": entry.title, "text": text, "audience": "owner", "from": space.owner,
            "identifiers": {"subject": sid}, "admitted": True}, ensure_ascii=False))
        have.add(entry.record)
        written += 1
    return written


def journal_at_once(space, reg, sid: str) -> bool:
    """A journal record about `sid` leaves at once: the classification that governs it is walled
    and its crew includes the space's owner (D8: the same people, nothing to pull back)."""
    import classify
    found = classify.effective(reg, sid) or {}
    crew = found.get("crew") if isinstance(found.get("crew"), list) else [found.get("crew")]
    return found.get("class") == "walled" and classify.person_id(reg, space.owner) in [str(c) for c in crew]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="link.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("root")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("resolve")
    p.add_argument("--cwd", default=".")
    p = sub.add_parser("link")
    p.add_argument("repo")
    p.add_argument("subject")
    p = sub.add_parser("unlink")
    p.add_argument("repo")
    args = parser.parse_args(argv)
    if args.command == "resolve":
        out = resolve(args.root, args.cwd)
        if out is None:
            return 3
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    import classify
    try:
        if args.command == "link":
            shown = link(args.root, args.repo, args.subject)
        else:
            unlink(args.root, args.repo)
            shown = repo_key(args.repo) or args.repo
    except (ValueError, classify.ClassifyError) as exc:
        print(f"refused: {exc}")
        return 1
    print(f"{args.command}ed {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
