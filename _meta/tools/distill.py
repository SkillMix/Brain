"""Distill: fold new evidence into one candidate per subject page (shape 1).

Runs after every sweep. It takes the subjects whose evidence moved since the last
run (`_meta/state/distill-watermark.yaml`) and that are active and either
important, stateful or asked about (a line in `_meta/questions.log` naming the
subject), and writes one candidate per subject at
`_inbound/candidates/<subject>.md`: the subject's page body (or a stub) followed by
a `## New evidence` list, one line per evidence page. While that candidate is an
unsigned draft the next run rewrites it in place with the merged evidence list, so
the queue grows with subjects that moved, never with runs. A witness page whose
text starts with `Decision:` becomes its own candidate under knowledge/decisions/.

Evidence for a subject = witness pages under knowledge/sources/ carrying
`subject: <id>` or `<id>` in `mentions`, landed records in _inbound/candidates/
(frontmatter `released_from` and `subject: <id>`), and entries of the daily notes under
journal/ that name the subject (a `subject: <id>` line or a desk path `work/<kind>/<id>/`). A page is
new when `at` or `generated.at` is after the watermark.

Nothing here writes `status: stable`, edits a page under knowledge/, or runs git.

    python distill.py [root]
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import registry as registry_mod  # noqa: E402
import spaceyaml  # noqa: E402

ROUTINE = "distill"
STATE = Path("_meta") / "state"
RUNS = STATE / "runs.jsonl"
QUESTIONS = Path("_meta") / "questions.log"
CANDIDATES = Path("_inbound") / "candidates"
SOURCES = Path("knowledge") / "sources"
EVIDENCE_HEADING = "## New evidence"
MENTION_BLIND_KINDS = ("people", "domains")
DECISION_RE = re.compile(r"^\s*decision\s*:\s*(.*)$", re.I)


# ----- shared helpers (check.py imports these) ---------------------------------
class Reader:
    """Reads pages and counts what it read: cost_tokens = characters // 4."""

    def __init__(self):
        self.chars = 0
        self.pages = 0

    def read(self, path) -> tuple[dict, str]:
        text = Path(path).read_text(encoding="utf-8")
        self.chars += len(text)
        self.pages += 1
        return pages.split(text)

    @property
    def cost_tokens(self) -> int:
        return self.chars // 4


def read_watermark(root, routine: str) -> dict:
    path = Path(root) / STATE / f"{routine}-watermark.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def write_watermark(root, routine: str, data: dict) -> Path:
    path = Path(root) / STATE / f"{routine}-watermark.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n")
    return path


def stamp_of(value) -> dt.datetime | None:
    if value in (None, ""):
        return None
    try:
        return pages.parse_iso(value)
    except (TypeError, ValueError):
        return None


def append_run(root, routine: str, space: spaceyaml.Space, now: dt.datetime,
               cost_tokens: int, notes: dict) -> None:
    path = Path(root) / RUNS
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"routine": routine, "at": pages.iso(now), "space": space.name,
                       "cost_tokens": int(cost_tokens), "notes": notes}, default=str)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")


@dataclass
class Evidence:
    record: str
    rel: str
    at: dt.datetime            # the record's own time
    arrived: dt.datetime       # max(at, generated.at): when it reached this space
    title: str
    body: str
    subjects: list             # ids it is evidence for (subject first, then mentions)
    landed: bool               # a released record in _inbound/candidates/
    fm: dict = field(default_factory=dict)

    @property
    def subject(self) -> str | None:
        value = self.fm.get("subject")
        return str(value) if value else None


def _evidence(root: Path, path: Path, fm: dict, body: str, landed: bool, space_name: str = "") -> Evidence | None:
    gen = fm.get("generated") or {}
    gen_at = stamp_of(gen.get("at")) if isinstance(gen, dict) else None
    at = stamp_of(fm.get("at")) or gen_at
    if at is None:
        return None
    arrived = max(at, gen_at) if gen_at else at
    # evidence attaches by identifier or by a routing decision: a record joins the subjects it
    # matched or was routed to here, never one it merely mentions; a person the router named
    # (`informs` kind person) is a subject too
    subjects = registry_mod.routed_subjects(fm, space_name)
    for item in fm.get("informs") or []:
        if isinstance(item, dict) and item.get("kind") == "person" and item.get("person") \
                and str(item["person"]) not in subjects:
            subjects.append(str(item["person"]))
    return Evidence(record=str(fm.get("record") or path.stem),
                    rel=path.relative_to(root).as_posix(), at=at, arrived=arrived,
                    title=str(fm.get("title") or ""), body=body, subjects=subjects,
                    landed=landed, fm=fm)


def gather_evidence(root, reader: Reader, space_name: str = "") -> list:
    """Every witness page and landed record in this space, oldest path first."""
    root = Path(root)
    found = []
    base = root / SOURCES
    if base.is_dir():
        for path in sorted(base.rglob("*.md")):
            if not pages.is_page_path(path):
                continue
            try:
                fm, body = reader.read(path)
            except Exception:
                continue
            item = _evidence(root, path, fm, body, landed=False, space_name=space_name) if fm else None
            if item:
                found.append(item)
    base = root / CANDIDATES
    if base.is_dir():
        for path in sorted(base.glob("*.md")):
            try:
                fm, body = reader.read(path)
            except Exception:
                continue
            if not fm or not fm.get("released_from"):
                continue
            item = _evidence(root, path, fm, body, landed=True, space_name=space_name)
            if item:
                found.append(item)
    found.extend(journal_evidence(root))
    return found


JOURNAL = Path("journal")
JOURNAL_FILE = re.compile(r"(\d{4}-\d{2}-\d{2})\.md$")
ENTRY_HEAD = re.compile(r"^##\s+(?:(\d{1,2}):(\d{2})Z?)?\s*[:\-–—]*\s*(.*)$")
SUBJECT_LINE = re.compile(r"^\s*(?:[-*]\s*)?subject:\s*`?([a-z0-9][a-z0-9-]*)`?\s*$", re.I | re.M)
DESK_PATH = re.compile(r"work/[a-z0-9-]+/([a-z0-9][a-z0-9-]*)/")


def journal_evidence(root) -> list:
    """Entries of the person's daily notes as evidence, so what a session captured reaches
    distill like a witness page. An entry joins a subject only by naming it: a `subject: <id>`
    line or a desk path `work/<kind>/<id>/`, never by a word it merely mentions."""
    root = Path(root)
    base = root / JOURNAL
    found = []
    if not base.is_dir():
        return found
    for path in sorted(base.rglob("*.md")):
        day = JOURNAL_FILE.search(path.name)
        if not day:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            changed = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for chunk in re.split(r"(?m)^(?=## )", text):
            if not chunk.startswith("## "):
                continue
            head, _, body = chunk.partition("\n")
            match = ENTRY_HEAD.match(head.strip())
            hour, minute, title = (match.group(1), match.group(2), match.group(3)) if match else (None, None, head[3:])
            try:
                at = pages.parse_iso(f"{day.group(1)}T{int(hour or 12):02d}:{int(minute or 0):02d}:00Z")
            except (TypeError, ValueError):
                continue
            subjects = list(dict.fromkeys(SUBJECT_LINE.findall(body) + DESK_PATH.findall(body)))
            if not subjects:
                continue
            found.append(Evidence(record=f"journal-{at:%Y%m%dT%H%M}", rel=rel, at=at,
                                  arrived=max(at, changed), title=title.strip() or f"journal {day.group(1)}",
                                  body=body, subjects=subjects, landed=False,
                                  fm={"source": "journal", "audience": "owner", "subject": subjects[0]}))
    return found


def new_since(evidence: list, since: dt.datetime | None) -> list:
    return [ev for ev in evidence if since is None or ev.arrived > since]


def by_subject(evidence: list) -> dict:
    grouped: dict = {}
    for ev in evidence:
        for sid in ev.subjects:
            grouped.setdefault(sid, []).append(ev)
    return grouped


# ----- distill -----------------------------------------------------------------
@dataclass
class DistillReport:
    candidates_written: int = 0   # candidates created this run (decisions included)
    refreshed: int = 0            # existing draft candidates rewritten in place
    skipped: int = 0              # subjects or files left alone, with the reason counted
    subjects: list = field(default_factory=list)
    cost_tokens: int = 0


def asked_subjects(root) -> set:
    """Subjects someone asked about: any question-log line naming a subject, whatever the
    tier of its answer (the query skill logs witness, working and desk answers too)."""
    path = Path(root) / QUESTIONS
    asked = set()
    if not path.exists():
        return asked
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("subject"):
            asked.add(str(entry["subject"]))
    return asked


def in_scope(subject: registry_mod.Subject, asked: set) -> bool:
    if subject.state != "active":
        return False
    return bool(subject.important or subject.stateful or subject.id in asked)


def target_for(root: Path, subject: registry_mod.Subject, reader: Reader):
    """(target relpath, its frontmatter or None, its body or None)."""
    folder = root / "knowledge" / subject.kind / subject.id
    if folder.is_dir():
        for path in sorted(folder.glob("*.md")):
            if pages.is_page_path(path):
                fm, body = reader.read(path)
                return path.relative_to(root).as_posix(), fm, body
    return f"knowledge/{subject.kind}/{subject.id}/{subject.id}.md", None, None


def evidence_line(ev: Evidence) -> str:
    # The title only, never the body: a candidate may travel to another space, and a record's
    # text can hold what that space's left-out list forbids. A record without a title is
    # named by its id; the signer opens it where it lives.
    text = " ".join(ev.title.split()) or f"record {ev.record}"
    kind = "landed" if ev.landed else ("journal" if ev.fm.get("source") == "journal" else "witness")
    return f"- {ev.at:%Y-%m-%d}: {text} ({kind}, {ev.record})"


def _draft_or_none(path: Path, reader: Reader):
    """The existing candidate's (fm, body), {} when absent, None when it must not be touched."""
    if not path.exists():
        return {}, ""
    fm, body = reader.read(path)
    if fm.get("released_from") or str(fm.get("status") or "draft") != "draft":
        return None
    return fm, body


def _with_evidence(base: str, lines: list) -> str:
    base = base.split(EVIDENCE_HEADING, 1)[0].rstrip("\n")
    return f"{base}\n\n{EVIDENCE_HEADING}\n" + "\n".join(lines) + "\n"


def write_candidate(root: Path, space, reg, subject, new: list, index: dict,
                    now: dt.datetime, reader: Reader) -> str:
    path = root / CANDIDATES / f"{subject.id}.md"
    existing = _draft_or_none(path, reader)
    if existing is None:
        return "skipped"
    old_fm, old_body = existing
    target, page_fm, page_body = target_for(root, subject, reader)
    merged = [str(r) for r in (old_fm.get("evidence") or [])]
    merged += [ev.record for ev in new if ev.record not in merged]
    merged.sort(key=lambda r: (index[r].at if r in index else now, r))
    base = old_body if old_fm else (page_body if page_body is not None
                                    else f"# {subject.title or subject.id}\n")
    body = _with_evidence(base, [evidence_line(index[r]) for r in merged if r in index])
    title = (page_fm or {}).get("title") or old_fm.get("title") or subject.title or subject.id
    fm = {"id": old_fm.get("id") or pages.new_id(),
          "type": (page_fm or {}).get("type") or old_fm.get("type") or "entity",
          "title": title, "status": "draft", "generated": pages.generated(ROUTINE, now),
          "subject": subject.id, "kind": subject.kind}
    home = reg.home_of(subject.id)
    if home and home != space.name:
        fm["destination"] = home
    fm["target"] = target
    fm["evidence"] = merged
    pages.write(path, fm, body)
    return "refreshed" if old_fm else "new"


def decision_title(body: str) -> str | None:
    text = body.strip()
    first = text.splitlines()[0] if text else ""
    match = DECISION_RE.match(first)
    if not match:
        return None
    return match.group(1).strip() or first.strip()


def slugify(text: str, limit: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit].rstrip("-") or "decision"


def write_decision(root: Path, space, reg, ev: Evidence, now: dt.datetime, reader: Reader) -> str:
    title = decision_title(ev.body) or ""
    name = f"{ev.at:%Y-%m-%d}-{slugify(title)}"
    path = root / CANDIDATES / f"{name}.md"
    existing = _draft_or_none(path, reader)
    if existing is None:
        return "skipped"
    old_fm, _ = existing
    fm = {"id": old_fm.get("id") or pages.new_id(), "type": "decision", "title": title,
          "status": "draft", "generated": pages.generated(ROUTINE, now)}
    if ev.subject:
        fm["subject"] = ev.subject
    fm["kind"] = "decisions"
    home = reg.home_of(ev.subject) if ev.subject else None
    if home and home != space.name:
        fm["destination"] = home
    fm["target"] = f"knowledge/decisions/{name}.md"
    fm["evidence"] = [ev.record]
    pages.write(path, fm, ev.body)
    return "refreshed" if old_fm else "new"


def run(root, now: dt.datetime | None = None) -> DistillReport:
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    reg = registry_mod.Registry(root, space)
    reader = Reader()
    report = DistillReport()
    since = stamp_of(read_watermark(root, ROUTINE).get("at"))
    evidence = gather_evidence(root, reader, space.name)
    index = {ev.record: ev for ev in evidence}
    new = new_since(evidence, since)
    asked = asked_subjects(root)

    def count(outcome: str, sid: str | None) -> None:
        if outcome == "new":
            report.candidates_written += 1
        elif outcome == "refreshed":
            report.refreshed += 1
        else:
            report.skipped += 1
            return
        if sid and sid not in report.subjects:
            report.subjects.append(sid)

    for sid, items in sorted(by_subject(new).items()):
        subject = reg.get(sid)
        if subject is None or not in_scope(subject, asked):
            report.skipped += 1
            continue
        if subject.kind in MENTION_BLIND_KINDS and not any(ev.subject == sid for ev in items):
            # A person or a domain merely mentioned in a record is not evidence about it:
            # claims about a named person are high-stakes, and domain names (sales, legal,
            # finance) are ordinary words. Only attached records count for these kinds.
            report.skipped += 1
            continue
        count(write_candidate(root, space, reg, subject, items, index, now, reader), sid)
    for ev in new:
        if not ev.landed and decision_title(ev.body):
            count(write_decision(root, space, reg, ev, now, reader), None)
        for item in ev.fm.get("informs") or []:   # a decision the router saw in the record
            if isinstance(item, dict) and item.get("kind") == "decision" and str(item.get("text") or "").strip():
                said = dataclasses.replace(ev, body=f"Decision: {str(item['text']).strip()}\n\n{ev.body}")
                count(write_decision(root, space, reg, said, now, reader), None)
    write_watermark(root, ROUTINE, {"at": pages.iso(now)})
    report.cost_tokens = reader.cost_tokens
    append_run(root, ROUTINE, space, now, report.cost_tokens,
               {"candidates_written": report.candidates_written, "refreshed": report.refreshed,
                "skipped": report.skipped, "subjects": report.subjects})
    return report


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(argv[0]) if argv else Path(".")
    report = run(root)
    print(f"distill: {report.candidates_written} candidates written, {report.refreshed} refreshed, "
          f"{report.skipped} skipped, {report.cost_tokens} tokens; subjects: "
          f"{', '.join(report.subjects) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
