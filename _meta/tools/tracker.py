"""The tracker: one routine writes it, nothing else does (vision section 12, rules.md 1.5).

Sessions never write the tracker. They queue a request as a file; the tracker routine (an
agent holding the tracker connector) creates each issue and records its key, so a requested
issue appears within the hour and nothing else needs a lock.

    python tracker.py [root] queue --title T [--subject S] [--body B] [--spec PAGE] --by NAME
    python tracker.py [root] update --issue KEY [--state S] [--comment C] --by NAME
    python tracker.py [root] observe              queue updates from new evidence
    python tracker.py [root] pending              what the routine creates or updates now, as JSON
    python tracker.py [root] record <id> --issue KEY [--url URL]

Updates go only to issues this tracker made (`_meta/state/tracker.jsonl`), never to issues
people made. `observe` reads the evidence for them: a merged pull request naming the key
closes it; a daily-note line naming the key with "done", "blocked" or "started" queues that
change; each piece of evidence is used once.

The badge decides (`authority.write_outside_system`): at `at_once`, the template default, the
routine creates every queued request; at `propose` a request waits for a person's yes on
Needs you (`sign` approves it). Every created issue is logged in `_meta/state/tracker.jsonl`
with whom it was created for and by which path. A request queued with `--spec <page>` writes
`tracker: <key>` into that page once the issue is made, so the librarian moves its state.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import authority  # noqa: E402
import pages  # noqa: E402
import spaceyaml  # noqa: E402

QUEUE = Path("_outbound") / "tracker"
LOG = Path("_meta") / "state" / "tracker.jsonl"


def _spec_page(root: Path, spec) -> Path | None:
    """The spec page a request names, when it is a page path inside the space."""
    path = (root / str(spec)).resolve()
    return path if path.is_relative_to(root.resolve()) and pages.is_page_path(path) else None


def queue(root, title: str, by: str, subject: str | None = None, body: str = "",
          spec: str | None = None, now: dt.datetime | None = None) -> Path:
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    if not str(title).strip() or not str(by).strip():
        raise ValueError("a tracker request needs a title and who asked for it (--by)")
    if spec and _spec_page(root, spec) is None:
        raise ValueError(f"{spec} is not a page inside this space")
    rid = pages.new_id().replace("p-", "tr-")
    fm = {"id": rid, "type": "tracker_request", "title": str(title).strip(), "status": "queued",
          "generated": pages.generated("tracker", now), "subject": subject, "requested_by": by,
          "requested_at": pages.iso(now)}
    if spec:
        fm["spec"] = _spec_page(root, spec).relative_to(root.resolve()).as_posix()
    return pages.write(root / QUEUE / f"{rid}.md", fm, (body or title).rstrip("\n") + "\n")


def made(root) -> dict:
    """issue key -> the log line of its creation, for the issues this tracker made."""
    out = {}
    log = Path(root) / LOG
    for line in log.read_text(encoding="utf-8").splitlines() if log.exists() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("issue") and "update" not in row:
            out[str(row["issue"])] = row
    return out


def update(root, issue: str, by: str, state: str | None = None, comment: str = "",
           evidence: str | None = None, now: dt.datetime | None = None) -> Path | None:
    """Queue a change to an issue this tracker made: a new state, a comment, or both. None when
    the same evidence was used before; ValueError for an issue people made."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    issue = str(issue)
    if issue not in made(root):
        raise ValueError(f"{issue} was not made by this tracker; updates go only to its own issues")
    if not state and not str(comment).strip():
        raise ValueError("an update needs a state or a comment")
    if evidence and any(fm.get("evidence") == evidence and fm.get("issue") == issue
                        for _, fm, _ in requests(root)):
        return None
    rid = pages.new_id().replace("p-", "tu-")
    fm = {"id": rid, "type": "tracker_update", "title": f"Update {issue}", "status": "queued",
          "generated": pages.generated("tracker", now), "issue": issue, "state": state,
          "comment": str(comment).strip(), "evidence": evidence, "requested_by": by,
          "requested_at": pages.iso(now)}
    return pages.write(root / QUEUE / f"{rid}.md", fm, (str(comment).strip() or f"State: {state}") + "\n")


DONE_WORDS = re.compile(r"(?i)\b(done|finished|shipped|completed|closed)\b")
BLOCKED_WORDS = re.compile(r"(?i)\bblocked\b")
STARTED_WORDS = re.compile(r"(?i)\b(started|working on|in progress|picked up)\b")


def _evidence_pages(root: Path, space: spaceyaml.Space) -> list:
    """Repository changes here and in every mounted space: a merged pull request swept in a
    project space counts for the issues a person's tracker made."""
    roots = [root] + [r for r in (space.mount_root(str(m.get("name", ""))) for m in space.mounts) if r]
    out = []
    for base in roots:
        folder = Path(base) / "knowledge" / "sources"
        for path in sorted(folder.rglob("*.md")) if folder.is_dir() else []:
            try:
                fm, body = pages.read(path)
            except Exception:
                continue
            if fm.get("kind") == "change" or fm.get("source") == "repository":
                out.append((str(fm.get("record") or fm.get("id") or path.stem), str(fm.get("title") or ""), body))
    return out


def observe(root, now: dt.datetime | None = None) -> list:
    """Queue the updates the evidence calls for, for the issues this tracker made."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    keys = made(root)
    if not keys:
        return []
    found = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(k) for k in sorted(keys, key=len, reverse=True))
                       + r")(?![A-Za-z0-9])")
    queued = []
    for record, title, body in _evidence_pages(root, space):
        for key in sorted(set(found.findall(f"{title}\n{body}"))):
            path = update(root, key, "routine:tracker", state="Done", comment=f"Merged: {title}".strip(),
                          evidence=f"{record}:{key}", now=now)
            if path:
                queued.append(path)
    journal = root / "journal"
    for path in sorted(journal.rglob("*.md")) if journal.is_dir() else []:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, start=1):
            keys_here = sorted(set(found.findall(line)))
            if not keys_here:
                continue
            state = "Done" if DONE_WORDS.search(line) else ("In Progress" if STARTED_WORDS.search(line) else None)
            if state is None and not BLOCKED_WORDS.search(line):
                continue
            text = line.strip().lstrip("-* ").strip()
            for key in keys_here:
                out = update(root, key, "routine:tracker", state=state, comment=text[:300],
                             evidence=f"{path.relative_to(root).as_posix()}:{n}:{key}", now=now)
                if out:
                    queued.append(out)
    return queued


def requests(root) -> list:
    out = []
    folder = Path(root) / QUEUE
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            fm, body = pages.read(path)
        except Exception:
            continue
        out.append((path, fm, body))
    return out


def pending(root) -> list:
    """The queued requests the routine may create now: all of them at `at_once`, only the
    approved ones at `propose`."""
    root = Path(root)
    space = spaceyaml.load(root)
    free = authority.allows(space, "write_outside_system", "at_once", agent="tracker")
    out = []
    for path, fm, body in requests(root):
        if fm.get("status") != "queued" or not (free or fm.get("approved_by")):
            continue
        if fm.get("type") == "tracker_update":
            out.append({"id": fm.get("id"), "kind": "update", "issue": fm.get("issue"), "state": fm.get("state"),
                        "comment": fm.get("comment") or "", "requested_by": fm.get("requested_by")})
        else:
            out.append({"id": fm.get("id"), "kind": "create", "title": fm.get("title"), "subject": fm.get("subject"),
                        "body": body.strip(), "requested_by": fm.get("requested_by")})
    return out


def waiting_for_a_person(root) -> list:
    """At `propose`, the queued requests nobody approved yet: one Needs-you item each."""
    root = Path(root)
    space = spaceyaml.load(root)
    if authority.allows(space, "write_outside_system", "at_once", agent="tracker"):
        return []
    return [(path, fm, body) for path, fm, body in requests(root)
            if fm.get("status") == "queued" and not fm.get("approved_by")]


def record(root, rid: str, issue: str, url: str | None = None, now: dt.datetime | None = None) -> Path:
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    for path, fm, body in requests(root):
        if fm.get("id") == rid:
            is_update = fm.get("type") == "tracker_update"
            fm.update(status="applied" if is_update else "created", issue=str(issue), url=url,
                      **{("applied_at" if is_update else "created_at"): pages.iso(now)},
                      **authority.acted(space, "routine:tracker", fm.get("approved_by") or fm.get("requested_by")))
            pages.write(path, fm, body)
            line = {"at": pages.iso(now), "id": rid, "issue": str(issue), "url": url,
                    "acted_for": fm["acted_for"], "path": fm["path"]}
            if is_update:
                line["update"] = {"state": fm.get("state"), "comment": fm.get("comment") or ""}
            log = root / LOG
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(line) + "\n")
            if not is_update and fm.get("spec"):
                _link_spec(root, fm["spec"], str(issue))
            return path
    raise KeyError(f"no tracker request {rid}")


def _link_spec(root: Path, spec: str, issue: str) -> None:
    """Write `tracker: <issue>` into the spec page, unless it is gone or already names one."""
    path = _spec_page(root, spec)
    if path is None or not path.is_file():
        return
    try:
        fm, body = pages.read(path)
    except Exception:
        return
    if not fm.get("tracker"):
        fm["tracker"] = issue
        pages.write(path, fm, body)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("queue")
    q.add_argument("--title", required=True)
    q.add_argument("--subject")
    q.add_argument("--body", default="")
    q.add_argument("--spec")
    q.add_argument("--by", required=True)
    u = sub.add_parser("update")
    u.add_argument("--issue", required=True)
    u.add_argument("--state")
    u.add_argument("--comment", default="")
    u.add_argument("--by", required=True)
    sub.add_parser("observe")
    sub.add_parser("pending")
    r = sub.add_parser("record")
    r.add_argument("id")
    r.add_argument("--issue", required=True)
    r.add_argument("--url")
    args = ap.parse_args(argv)
    if args.cmd == "queue":
        print(queue(args.root, args.title, args.by, subject=args.subject, body=args.body, spec=args.spec))
    elif args.cmd == "update":
        print(update(args.root, args.issue, args.by, state=args.state, comment=args.comment) or "already queued")
    elif args.cmd == "observe":
        print(json.dumps([str(p) for p in observe(args.root)], indent=2))
    elif args.cmd == "pending":
        print(json.dumps(pending(args.root), indent=2, ensure_ascii=False))
    else:
        print(record(args.root, args.id, args.issue, url=args.url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
