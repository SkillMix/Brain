"""link: which subject a codebase belongs to (docs/design/2026-10-22-codebase-link-design.md).

A codebase is linked to a subject on the subject's entry in the registry that holds it (`repos`,
registry.py). `resolve` reads the git remote of a folder (a subfolder or a worktree works too),
finds the one active link in this space's registry and every mounted one, and prints the
subject, its parent, its home, the home's audience and left-out kinds, the codebase's readers
and the subject's status page as JSON. It never touches the network. The first time it meets a
repository nothing links, it scores the repository's owner and name against the subjects this
space's owner may link: one clear match is linked at once (the undo is logged), a tie becomes one
Needs-you item, no match leaves it unlinked. Every outcome is remembered
(`_meta/state/links-seen.jsonl`), so no repository is asked about twice; one that matched nothing
is scored again, so a subject registered later still claims it. A link to a registry
this space does not hold travels as a registry note (classify.py) and counts as pending until
that registry has applied it.

`scan` (onboarding, project creation) runs that placement over every repository the person's
account and this space's organisation list on the host, leaving out the spaces' own repositories.
`check` (the daily chain) keeps the links homed in this space: it follows a renamed repository by
its name, else its id (the old name still resolves, so old clones keep working, until another repository takes
that name), marks a lost one `gone` and
brings it back when the host finds it again, sets the readers against the home's audience (a
public repository is always wider), keeps the older of two claims, and lists the linked
repositories on the space's `repository` source; the health line shows `links N, wider M,
repaired K`. Without the host (no `gh`, signed out, a fixture space) both skip their host calls
and try again next run.

Entries point into a codebase with a `ref:` line: `owner/repo:path@commit`, `owner/repo@commit`
or a pull request URL.

    python link.py <root> resolve [--cwd DIR]   the link as JSON; exit 3 when there is none
    python link.py <root> link <repo> <subject>  link a codebase detection cannot place
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

# {repo, at, outcome: linked|asked|none|unlinked, subject}, plus linked_by, repo_id and the note's
# signal id when it travelled to another registry
SEEN = Path("_meta") / "state" / "links-seen.jsonl"
NEEDS_YOU = Path("_inbound") / "needs-you" / "links.yaml"
STATE = Path("_meta") / "state" / "links.json"   # the last check: {at, links, wider, repaired}
REF_LINE = re.compile(r"^\s*(?:[-*]\s*)?ref:\s*`?(\S+?)`?\s*$", re.I | re.M)
REF_PR = re.compile(r"^https?://github\.com/([\w.-]+/[\w.-]+)/pull/\d+/?$", re.I)
REF_AT = re.compile(r"^([A-Za-z0-9][\w-]*/[\w.-]+?)(?::([^@\s]+))?@([\w.-]+)$")


@dataclass
class Link:
    subject: str
    repo: str                  # owner/name
    repo_id: str | None
    linked_by: str
    readers: str               # "inside" | "wider"; "wider" when never set
    checked: str | None
    linked: str | None
    gone: str | None
    holder: str                # "" this space's registry, else the mount name
    pending: bool              # decided here (SEEN), not yet in the holder's registry
    was: tuple = ()            # names the repository had before a rename: an old clone still finds it


def repo_key(value) -> str | None:
    """Lower-case owner/name for a GitHub repository, else the URL lower-cased without `.git`.
    A URL's user info (a token, `git@`) is dropped, so it is never written anywhere, and an
    `ssh://` or `git://` GitHub URL counts as the https one."""
    text = re.sub(r"^([a-z][\w+.-]*://)[^/]*@", r"\1", str(value or "").strip(), flags=re.I)
    url = registry.repo_url(re.sub(r"^(?:ssh|git)://github\.com/", "https://github.com/", text, flags=re.I))
    if not url:
        return None
    url = url.lower().rstrip("/").removesuffix(".git")
    return url.removeprefix("https://github.com/")


def _parts(repo) -> list:
    """[owner, name] of a repository reference."""
    return (["", ""] + re.split(r"[/:]", repo_key(repo) or ""))[-2:]


def remote_of(path) -> str | None:
    """The `origin` remote of the repository holding `path`, as repo_key, read from its config
    without calling git. A `.git` file (a worktree, a submodule) is followed to its config. A
    space's own checkout is not a codebase: None (the template repository is one)."""
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
        match = re.search(r'\[remote "origin"\][^\[]*?^[ \t]*url[ \t]*=[ \t]*(\S+)', text, re.M)
        return repo_key(match.group(1)) if match else None
    return None


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
    the repository's link until the holder has applied it."""
    root = Path(root)
    reg = registry.Registry(root)
    out = []
    for subject in reg.everywhere():
        for entry in subject.extra.get("repos") or []:
            if not isinstance(entry, dict) or not str(entry.get("repo") or "").strip():
                continue
            day = {k: str(entry[k]) if entry.get(k) else None for k in ("checked", "linked", "gone")}
            out.append(Link(subject=subject.id, repo=str(entry["repo"]), repo_id=entry.get("repo_id"),
                            linked_by=str(entry.get("linked_by") or ""), readers=entry.get("readers") or "wider",
                            holder=subject.space, pending=False,
                            was=tuple(str(w) for w in entry.get("was") or [] if w), **day))
    for key, row in _seen_rows(root).items():
        subject = reg.get(str(row.get("subject") or ""))
        if subject is None or not row.get("signal") or _arrived(reg.space, subject.space, row["signal"]):
            continue
        if row.get("outcome") == "unlinked":
            out = [l for l in out if repo_key(l.repo) != key]
        elif row.get("outcome") == "linked" and not any(
                l.subject == subject.id and repo_key(l.repo) == key for l in out):
            out.append(Link(subject=subject.id, repo=str(row["repo"]), repo_id=row.get("repo_id"),
                            linked_by=str(row.get("linked_by") or ""), readers="wider", checked=None,
                            linked=str(row.get("at") or "")[:10] or None, gone=None, holder=subject.space,
                            pending=True))
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
    the same owner. People, archived subjects and those this space's owner may not link score 0."""
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
                      "recommendation": f"link it to {ids[0]}: python _meta/tools/link.py . link {repo} {ids[0]}",
                      "default": "nothing is linked", "depends": 0})


def _needs_you(root: Path, item: dict) -> None:
    """Add or replace one item (by id) in this space's links Needs-you file."""
    import yaml
    path = root / NEEDS_YOU
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    items = [i for i in ((data or {}).get("items") or []) if isinstance(i, dict) and i.get("id") != item["id"]]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"items": items + [item]}, sort_keys=False, allow_unicode=True),
                    encoding="utf-8", newline="\n")


def detect(root, repo: str, now=None, id_fn=None) -> dict:
    """Place a repository nothing links yet: one clear match is linked, a tie is asked once, no
    match leaves it unlinked; each outcome is a SEEN row. A repository already linked (under this
    name or one it had), asked about or unlinked is `known`, and nothing is written. One that
    matched nothing is scored again, so a subject registered later still claims it; its `none`
    row is written once."""
    import classify
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    key = repo_key(repo)
    row = _seen_rows(root).get(key)
    if any(key in {repo_key(l.repo), *map(repo_key, l.was)} for l in links(root)) \
            or (row is not None and row.get("outcome") != "none"):
        return {"outcome": "known", "subject": None, "candidates": []}
    reg = registry.Registry(root, space)
    scored = [(s, score(repo, s, reg)) for s in reg.everywhere()]
    top = max((n for _, n in scored), default=0)
    contenders = [s.id for s, n in sorted(scored, key=lambda p: -p[1]) if n >= 2 and n >= top - 1]
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
            _remember(root, repo, now, "linked", sid, linked_by="detect", repo_id=repo_id, signal=signal)
            return {"outcome": "linked", "subject": sid, "candidates": contenders}
    if len(contenders) > 1:
        _ask(root, space, repo, contenders)
        _remember(root, repo, now, "asked")
        return {"outcome": "asked", "subject": None, "candidates": contenders}
    if row is None:
        _remember(root, repo, now, "none")
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


def _home_name(space, reg, subject) -> str:
    """The space a subject is homed in: its home, else the space whose registry holds it."""
    import classify
    return reg.home_of(subject.id) or classify._holder_name(space, subject) or space.name


def resolve(root, cwd, now=None) -> dict | None:
    """The subject the codebase at `cwd` belongs to, with its home's audience, its readers and its
    status page; None when it is not linked. No network: a repository nothing links runs `detect`;
    one that matched nothing is scored again on each resolve, and written once."""
    root = Path(root)
    repo = remote_of(cwd)
    if not repo:
        return None
    found = link_for(root, repo)
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
            "readers": found.readers, "left_out": home_space.left_out if home_space is not None else None,
            "status_page": status_page, "repo": found.repo}


def link(root, repo: str, sid: str, now=None) -> None:
    """The person's own link, for a codebase detection cannot place."""
    repo = repo_key(repo) or repo
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    current = link_for(root, repo)
    if current is not None and current.subject != sid:
        raise ValueError(f"{repo} is linked to {current.subject}; unlink it first")
    repo = current.repo if current is not None else repo   # a name it had before a rename: the same link
    signal = _send(root, repo, sid, {"linked_by": "person", "linked": now.date().isoformat(), "gone": None}, now)
    _log(root, now, f"linked {repo} to {sid} (person); undo: python _meta/tools/link.py . unlink {repo}")
    _remember(root, repo, now, "linked", sid, linked_by="person", signal=signal)


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
    for name in (repo, *current.was):   # an old clone does not link it again
        _remember(root, name, now, "unlinked", current.subject, signal=signal)


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
    until another repository takes one, a lost one is marked gone and comes back when found again,
    readers are set against this space's audience (a public repository is wider), the older of two
    claims stays, and the `repository` source lists what is linked. Never an owner alert."""
    import classify
    import onboard
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    today = now.date().isoformat()
    fixture = space.mode == "fixture"   # a fixture space never asks the host with the default functions
    lookup = lookup_fn or (None if fixture else _host_lookup)
    readers_of = readers_fn or (None if fixture else access.readers)
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
    claims: dict = {}   # repo_key -> subject -> (linked, order, Link); a missing `linked` counts as newest
    for n, found in enumerate(links(root)):
        if not found.gone and homed_here(found):
            by_subject = claims.setdefault(repo_key(found.repo), {})
            by_subject.setdefault(found.subject, (found.linked or "9999-99-99", n, found))
    kept = []
    for by_subject in claims.values():   # two subjects homed here claim one repository: the older stays
        first, *rest = [c[2] for c in sorted(by_subject.values(), key=lambda c: c[:2])]
        kept.append(first)
        repaired += sum(send(other, other.repo, remove=True) for other in rest)
        if rest:
            names = [first.subject] + [o.subject for o in rest]
            _needs_you(root, {"id": "link-claim-" + _slug(first.repo), "kind": "question", "for": space.owner,
                              "text": f"{first.repo} was linked to {', '.join(names[:-1])} and {names[-1]}; "
                                      f"I kept {first.subject}, linked first.",
                              "recommendation": f"keep {first.subject}", "default": f"{first.subject} keeps it",
                              "depends": 0})
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
                repaired += send(found, repo, gone=today)
                continue
            public = there.get("private") is False   # anyone can read it: wider, whoever the collaborators are
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
    wanted = sorted(spelling.values())
    source = next((s for s in space.sources if isinstance(s, dict) and s.get("name") == "repository"), None)
    if source is None and wanted:
        audience = {"brain": "everyone", "project": "crew"}.get(space.profile, "owner")
        onboard.connect(root, "repository", audience=audience, scope={"repos": wanted})
    elif source is not None and list(source.get("repos") or []) != wanted:
        source["repos"] = wanted   # only this key: a manual `repo` stays
        spaceyaml.save(space)
    counts = {"links": len(pairs), "wider": sum(r == "wider" for r in pairs.values()), "repaired": repaired}
    path = root / STATE
    try:
        before = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        before = None
    if not isinstance(before, dict) or {k: before.get(k) for k in counts} != counts:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": pages.iso(now), **counts}) + "\n", encoding="utf-8", newline="\n")
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
            link(args.root, args.repo, args.subject)
        else:
            unlink(args.root, args.repo)
    except (ValueError, classify.ClassifyError) as exc:
        print(f"refused: {exc}")
        return 1
    print(f"{args.command}ed {repo_key(args.repo) or args.repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
