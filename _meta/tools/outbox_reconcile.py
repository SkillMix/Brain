"""Outbox reconcile: file what was actually sent; archive what idled. Never sends (shape 1).

Agents draft into `outbox/`; the person fires them. This routine reads the sent mail the
sweep's adapter exported (`_meta/state/feed/mail.jsonl`, records sent from the person's own
address or marked `direction: sent`) and, for each draft:

- a sent record with the same subject (ignoring `Re:` and `Fwd:`) and, when the draft names
  recipients, at least one of them: the draft moves to `outbox/sent/` with a `## Sent`
  section (message id, time, recipients);
- no such record and the draft idle for fourteen scaled days: it moves to `outbox/archive/`
  silently (one line in `log.md`, nothing on Needs you).

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


def _move(src: Path, folder: Path, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / src.name
    n = 1
    while target.exists():
        target = folder / f"{src.stem}-{n}{src.suffix}"
        n += 1
    target.write_text(text, encoding="utf-8", newline="\n")
    src.unlink()
    return target


def run(root, now: dt.datetime | None = None) -> Report:
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    report = Report()
    records = sent_records(root, space)
    log = root / "log.md"
    for path in sorted((root / OUTBOX).glob("*.md")) if (root / OUTBOX).is_dir() else []:
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
            sent_text = text.rstrip("\n") + (f"\n\n## Sent\n\n- message {match.get('id')}, "
                                             f"sent {match.get('at')}, to {to}\n")
            target = _move(path, root / SENT, sent_text)
            report.sent.append(target.relative_to(root).as_posix())
            with log.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"- {pages.iso(now)} outbox {path.name} sent as {match.get('id')}\n")
            continue
        if now - _since(fm, path) > space.scale(IDLE_DAYS * 24):
            target = _move(path, root / ARCHIVE, text)
            report.archived.append(target.relative_to(root).as_posix())
            with log.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"- {pages.iso(now)} outbox {path.name} archived after "
                             f"{IDLE_DAYS} idle days\n")
            continue
        report.waiting += 1
    runs = root / RUNS
    runs.parent.mkdir(parents=True, exist_ok=True)
    with runs.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"routine": "outbox-reconcile", "at": pages.iso(now), "space": space.name,
                                 "cost_tokens": 0, "notes": {"sent": len(report.sent),
                                                             "archived": len(report.archived),
                                                             "waiting": report.waiting}}) + "\n")
    return report


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    report = run(Path(argv[0]) if argv else Path("."))
    print(f"outbox: {len(report.sent)} filed as sent, {len(report.archived)} archived, "
          f"{report.waiting} waiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
