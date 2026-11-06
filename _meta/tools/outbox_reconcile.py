"""Outbox reconcile: file what was actually sent; archive what idled. Never sends (shape 1).

Agents draft into `outbox/`; the person fires them. This routine reads the sent mail the
sweep's adapter exported (`_meta/state/feed/mail.jsonl`, records sent from the person's own
address or marked `direction: sent`) and, for each draft:

- a sent record with the same subject (ignoring `Re:` and `Fwd:`) and, when the draft names
  recipients, at least one of them: the draft moves to `outbox/sent/` with a `## Sent`
  section (message id, time, recipients);
- no such record and the draft idle for fourteen scaled days: it moves to `outbox/archive/`
  silently (one line in `log.md`, nothing on Needs you).

A draft that is a symbolic link or a junction, or not a plain file, is never read and never
moved: a link a pull request committed would otherwise copy the file it points at (a
`~/.netrc`) into the repository. It stays where it is and the run line counts it as skipped.
Every write (the moved draft, `log.md`, the run line) goes through `pages.write_text` or
`pages.append_text`, which refuse to write through a link; a draft whose move is refused stays
in place and is skipped too. An `outbox/` that is itself a link is not read at all.

Nothing is deleted and nothing is sent. One run line.

    python outbox_reconcile.py [root]
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

OUTBOX = Path("outbox")
SENT = OUTBOX / "sent"
ARCHIVE = OUTBOX / "archive"
FEED = Path("_meta") / "state" / "feed" / "mail.jsonl"
RUNS = Path("_meta") / "state" / "runs.jsonl"
IDLE_DAYS = 14
PREFIX = re.compile(r"^\s*((re|fwd?|aw|wg)\s*:\s*)+", re.I)


@dataclass
class Report:
    sent: list = field(default_factory=list)
    archived: list = field(default_factory=list)
    waiting: int = 0
    skipped: list = field(default_factory=list)


def norm(subject) -> str:
    return " ".join(PREFIX.sub("", str(subject or "")).lower().split())


def _emails(value) -> set:
    items = value if isinstance(value, list) else [value]
    out = set()
    for item in items:
        for found in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", str(item or "")):
            out.add(found.lower())
        if item and "@" not in str(item):
            out.add(str(item).strip().lower())
    return out


def sent_records(root: Path, space: spaceyaml.Space) -> list:
    path = root / FEED
    if not path.exists():
        return []
    mine = {space.owner_email.lower()} - {""}
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("direction") == "sent" or (_emails(rec.get("from")) & mine):
            out.append(rec)
    return out


def _since(fm: dict, path: Path) -> dt.datetime:
    gen = fm.get("generated") if isinstance(fm.get("generated"), dict) else {}
    for value in (gen.get("at"), fm.get("at"), fm.get("created")):
        if value:
            try:
                return pages.parse_iso(value)
            except (TypeError, ValueError):
                continue
    return dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)


def _move(root: Path, src: Path, folder: Path, text: str) -> Path:
    """Write the draft's text under `folder`, then remove the draft. The write never follows a
    link (it raises pages.UnsafePath), and the draft goes only once its text is written."""
    target = folder / src.name
    n = 1
    while target.exists() or pages.is_link(target):   # a dangling link does not `exist`, yet is there
        target = folder / f"{src.stem}-{n}{src.suffix}"
        n += 1
    pages.write_text(target, text, root=root)
    src.unlink()
    return target


def _plain_draft(path: Path) -> bool:
    """A regular file, looked at without following a link: a linked draft is never read."""
    return not pages.is_link(path) and path.is_file()


def _one_line(value) -> str:
    """A field of a mail record as one word in a log line: a newline in a message id the feed
    carried can never start a line of its own in log.md."""
    return "-".join(str(value).split()) or "-"


def run(root, now: dt.datetime | None = None) -> Report:
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    report = Report()
    records = sent_records(root, space)
    log = root / "log.md"
    outbox = root / OUTBOX
    if pages.is_link(outbox):
        report.skipped.append(OUTBOX.as_posix())
    drafts = sorted(outbox.glob("*.md")) if outbox.is_dir() and not pages.is_link(outbox) else []
    for path in drafts:
        if not _plain_draft(path):
            report.skipped.append(path.relative_to(root).as_posix())
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        fm, body = pages.split(text)
        subject = norm(fm.get("subject") or fm.get("title") or path.stem)
        wanted = _emails(fm.get("to")) if fm.get("to") else set()
        match = None
        for rec in records:
            if norm(rec.get("subject")) != subject:
                continue
            if wanted and not (wanted & _emails(rec.get("to"))):
                continue
            match = rec
            break
        if match is not None:
            to = ", ".join(sorted(_emails(match.get("to")))) or "the recipients"
            mid = _one_line(match.get("id"))
            sent_text = text.rstrip("\n") + (f"\n\n## Sent\n\n- message {mid}, "
                                             f"sent {_one_line(match.get('at'))}, to {to}\n")
            try:
                target = _move(root, path, root / SENT, sent_text)
            except pages.UnsafePath:
                report.skipped.append(path.relative_to(root).as_posix())
                continue
            report.sent.append(target.relative_to(root).as_posix())
            pages.append_text(log, f"- {pages.iso(now)} outbox {path.name} sent as {mid}\n", root=root)
            continue
        if now - _since(fm, path) > space.scale(IDLE_DAYS * 24):
            try:
                target = _move(root, path, root / ARCHIVE, text)
            except pages.UnsafePath:
                report.skipped.append(path.relative_to(root).as_posix())
                continue
            report.archived.append(target.relative_to(root).as_posix())
            pages.append_text(log, f"- {pages.iso(now)} outbox {path.name} archived after "
                                   f"{IDLE_DAYS} idle days\n", root=root)
            continue
        report.waiting += 1
    notes = {"sent": len(report.sent), "archived": len(report.archived), "waiting": report.waiting}
    if report.skipped:
        notes["skipped"] = len(report.skipped)
    pages.append_text(root / RUNS, json.dumps({"routine": "outbox-reconcile", "at": pages.iso(now),
                                               "space": space.name, "cost_tokens": 0, "notes": notes}) + "\n",
                      root=root)
    return report


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    report = run(Path(argv[0]) if argv else Path("."))
    skipped = (f", {len(report.skipped)} skipped (a link, or a move refused): {', '.join(report.skipped)}"
               if report.skipped else "")
    print(f"outbox: {len(report.sent)} filed as sent, {len(report.archived)} archived, "
          f"{report.waiting} waiting{skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
