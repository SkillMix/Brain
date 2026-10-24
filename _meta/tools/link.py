"""link: which subject a codebase belongs to (docs/design/2026-10-22-codebase-link-design.md).

A codebase is linked to a subject on the subject's entry in the registry that holds it (`repos`,
registry.py). `resolve` reads the git remote of a folder (a subfolder or a worktree works too),
finds the one active link in this space's registry and every mounted one, and prints the
subject, its parent, its home, the home's audience and left-out kinds, the codebase's readers
and the subject's status page as JSON. It never touches the network. The first time it meets a
repository nothing links, it scores the repository's owner and name against the subjects this
space's owner may link: one clear match is linked at once (the undo is logged), a tie becomes one
Needs-you item, no match leaves it unlinked. Every outcome is remembered
(`_meta/state/links-seen.jsonl`), so no repository is asked about twice. A link to a registry
this space does not hold travels as a registry note (classify.py) and counts as pending until
that registry has applied it.

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
                            holder=subject.space, pending=False, **day))
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
    (missing counts as newest), then this space before its mounts, in mount order."""
    key = repo_key(repo)
    active = [l for l in links(root) if key and repo_key(l.repo) == key and not l.gone]
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


def _ask(root: Path, space, repo: str, ids: list) -> None:
    import yaml
    named = ", ".join(ids[:-1]) + f" or {ids[-1]}"
    item = {"id": "link-" + re.sub(r"[^a-z0-9]+", "-", repo_key(repo) or repo).strip("-"), "kind": "question",
            "for": space.owner, "text": f"{repo} could belong to {named}.",
            "recommendation": f"link it to {ids[0]}: python _meta/tools/link.py . link {repo} {ids[0]}",
            "default": "nothing is linked", "depends": 0}
    path = root / NEEDS_YOU
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    items = [i for i in ((data or {}).get("items") or []) if isinstance(i, dict) and i.get("id") != item["id"]]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"items": items + [item]}, sort_keys=False, allow_unicode=True),
                    encoding="utf-8", newline="\n")


def detect(root, repo: str, now=None, id_fn=None) -> dict:
    """Place a repository nothing links yet: one clear match is linked, a tie is asked once, no
    match leaves it unlinked; each outcome is a SEEN row. A repository already linked or seen is
    `known`, and nothing is written."""
    import classify
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    key = repo_key(repo)
    if any(repo_key(l.repo) == key for l in links(root)) or key in _seen_rows(root):
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


def resolve(root, cwd, now=None) -> dict | None:
    """The subject the codebase at `cwd` belongs to, with its home's audience, its readers and its
    status page; None when it is not linked. No network: a first meeting runs `detect` once."""
    import classify
    root = Path(root)
    repo = remote_of(cwd)
    if not repo:
        return None
    found = link_for(root, repo)
    if found is None and repo_key(repo) not in _seen_rows(root):
        detect(root, repo, now=now)
        found = link_for(root, repo)
    if found is None:
        return None
    space = spaceyaml.load(root)
    reg = registry.Registry(root, space)
    subject = reg.get(found.subject)
    if subject is None:
        return None
    home = reg.home_of(subject.id) or classify._holder_name(space, subject) or space.name
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
    signal = _send(root, repo, sid, {"linked_by": "person", "linked": now.date().isoformat()}, now)
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
    signal = _send(root, current.repo, current.subject, {"remove": True}, now)
    _log(root, now, f"unlinked {repo} from {current.subject}; "
                    f"undo: python _meta/tools/link.py . link {repo} {current.subject}")
    _remember(root, repo, now, "unlinked", current.subject, signal=signal)


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
