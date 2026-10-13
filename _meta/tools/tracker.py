"""The tracker: one routine writes it, nothing else does (vision section 12, rules.md 1.5).

Sessions never write the tracker. They queue a request as a file; the tracker routine (an
agent holding the tracker connector) creates each issue and records its key, so a requested
issue appears within the hour and nothing else needs a lock.

    python tracker.py [root] queue --title T [--subject S] [--body B] --by NAME
    python tracker.py [root] pending              what the routine creates now, as JSON
    python tracker.py [root] record <id> --issue KEY [--url URL]

The badge decides (`authority.write_outside_system`): at `at_once`, the template default, the
routine creates every queued request; at `propose` a request waits for a person's yes on
Needs you (`sign` approves it). Every created issue is logged in `_meta/state/tracker.jsonl`
with whom it was created for and by which path.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import authority  # noqa: E402
import pages  # noqa: E402
import spaceyaml  # noqa: E402

QUEUE = Path("_outbound") / "tracker"
LOG = Path("_meta") / "state" / "tracker.jsonl"


def queue(root, title: str, by: str, subject: str | None = None, body: str = "",
          now: dt.datetime | None = None) -> Path:
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    if not str(title).strip() or not str(by).strip():
        raise ValueError("a tracker request needs a title and who asked for it (--by)")
    rid = pages.new_id().replace("p-", "tr-")
    fm = {"id": rid, "type": "tracker_request", "title": str(title).strip(), "status": "queued",
          "generated": pages.generated("tracker", now), "subject": subject, "requested_by": by,
          "requested_at": pages.iso(now)}
    return pages.write(root / QUEUE / f"{rid}.md", fm, (body or title).rstrip("\n") + "\n")


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
    return [{"id": fm.get("id"), "title": fm.get("title"), "subject": fm.get("subject"), "body": body.strip(),
             "requested_by": fm.get("requested_by")}
            for path, fm, body in requests(root)
            if fm.get("status") == "queued" and (free or fm.get("approved_by"))]


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
            fm.update(status="created", issue=str(issue), url=url, created_at=pages.iso(now),
                      **authority.acted(space, "routine:tracker", fm.get("approved_by") or fm.get("requested_by")))
            pages.write(path, fm, body)
            line = {"at": pages.iso(now), "id": rid, "issue": str(issue), "url": url,
                    "acted_for": fm["acted_for"], "path": fm["path"]}
            log = root / LOG
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(line) + "\n")
            return path
    raise KeyError(f"no tracker request {rid}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("queue")
    q.add_argument("--title", required=True)
    q.add_argument("--subject")
    q.add_argument("--body", default="")
    q.add_argument("--by", required=True)
    sub.add_parser("pending")
    r = sub.add_parser("record")
    r.add_argument("id")
    r.add_argument("--issue", required=True)
    r.add_argument("--url")
    args = ap.parse_args(argv)
    if args.cmd == "queue":
        print(queue(args.root, args.title, args.by, subject=args.subject, body=args.body))
    elif args.cmd == "pending":
        print(json.dumps(pending(args.root), indent=2, ensure_ascii=False))
    else:
        print(record(args.root, args.id, args.issue, url=args.url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
