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
An evidence line names the record's `ref:` pointers into code after its title. A
`lesson: <point>` line in a daily-note entry becomes a candidate for the brain,
`lesson-<slug>.md` (target knowledge/topics/lessons/), merged by slug and carrying the
lesson text alone: the entry's `from: <subject>` (its own line, or after the lesson) never goes
into it. A lesson is proposed once per `from:` value (`_meta/state/lessons-proposed.jsonl`):
answered or still open, the same entry never proposes it again.

Evidence for a subject = witness pages under knowledge/sources/ carrying
`subject: <id>` or `<id>` in `mentions`, landed records in _inbound/candidates/
(frontmatter `released_from` and `subject: <id>`), and entries of the daily notes under
journal/ that name the subject (a `subject: <id>` line or a desk path `work/<kind>/<id>/`). A page is
new when `at`, `generated.at` or `released_at` is after the watermark. With `routing.archives` off (the
default) a page `provenance.is_archive_derived` flags is no evidence, and an archive-derived
target page lends no body.

Evidence the agent found to be about something else moves from `evidence` to
`set_aside: [{id, why}]`; every refresh keeps those ids out and the list as it is. `--rebuild`
writes a candidate from scratch: the stub, the subject's evidence as it is now, no set-aside
list, only the candidate's id kept (with `--subject`, that one subject, whether it moved or not;
the watermark stays). `verify` lists what keeps a candidate from the signer (a line without
`[src: ...]`, a source not in `evidence`, a never-leaves term, a left-out term of the
destination, a bracketed placeholder); `checked` stamps `checked: {by, at}` once it is clean.
A source also passes when it is the `q-` id of a question of the candidate's subject that was
answered or settled (`sign_questions.py`). `verify --body <file>` checks that file's text, a
write-up, against the candidate's frontmatter instead of the candidate's own body.
`prepare` lists the subject candidates whose questions need preparing, one tab-separated line
(candidate, subject, why) each: a subject never checked, or with records that arrived since its
last check (`sign_questions.needs_recheck`). It only reads; it writes nothing.

Nothing here writes `status: stable`, edits a page under knowledge/, or runs git.

    python distill.py [root] [--rebuild [--subject <id>]]
    python distill.py [root] verify <candidate path or subject id> [--body <file>]
    python distill.py [root] checked <candidate path or subject id> --by <name>
    python distill.py [root] prepare
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
import classify as gating  # noqa: E402
import link  # noqa: E402
import pages  # noqa: E402
import provenance  # noqa: E402
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
COMMANDS = ("verify", "checked", "prepare")
LESSON_LINE = re.compile(r"^\s*(?:[-*]\s*)?lesson:\s*(.+?)(?:[\s;,(]+from:.*)?\s*$", re.I | re.M)
LESSONS_PROPOSED = STATE / "lessons-proposed.jsonl"   # {slug, from, candidate, at}


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
    arrived: dt.datetime       # max(at, generated.at, released_at): when it reached this space
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
    # a record that waited out a release window lands long after it was generated
    arrived = max(t for t in (at, gen_at, stamp_of(fm.get("released_at"))) if t is not None)
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
            if pages.withdrawn(fm or {}):
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
            if not fm or not fm.get("released_from") or pages.withdrawn(fm):
                continue
            item = _evidence(root, path, fm, body, landed=True, space_name=space_name)
            if item:
                found.append(item)
    paged = {ev.record for ev in found if not ev.landed}   # a swept journal record: its page carries it
    found.extend(ev for ev in journal_evidence(root) if ev.record not in paged)
    return found


JOURNAL = Path("journal")
JOURNAL_FILE = re.compile(r"(\d{4}-\d{2}-\d{2})\.md$")
ENTRY_HEAD = re.compile(r"^##\s+~?(?:(\d{1,2}):(\d{2})Z?)?\s*[:\-–—]*\s*(.*)$")
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
        seen: dict = {}   # a second entry of the same minute is its own record: `-2`, `-3`
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
            record = f"journal-{at:%Y%m%dT%H%M}"   # the note's date and heading: the same on every machine
            if hour is not None:   # a UTC heading in a note named for the person's own date: the day either side
                at = next((c for c in (at, at - dt.timedelta(days=1), at + dt.timedelta(days=1))
                           if c.astimezone().date().isoformat() == day.group(1)), at)
            seen[record] = seen.get(record, 0) + 1
            record += f"-{seen[record]}" if seen[record] > 1 else ""
            subjects = list(dict.fromkeys(SUBJECT_LINE.findall(body) + DESK_PATH.findall(body)))
            if not subjects:
                continue
            found.append(Evidence(record=record, rel=rel, at=at,
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
    unclassified: int = 0         # candidates not written: their home waits for a classification
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
    if subject.state not in registry_mod.LIVE_STATES:
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
    # a pointer is a location, not content, so it may travel; the release scrub still reads it.
    # Only the pointer forms count: a mail's `Ref: <number>` line is body text.
    refs = [r for r in dict.fromkeys(link.refs_in(ev.body)) if link.ref_repo(r)]
    text += f" (refs: {', '.join(refs)})" if refs else ""
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


def archives_on(space) -> bool:
    """`routing.archives`: archive-derived pages count as evidence only when it is on."""
    return bool(getattr(space, "routing_archives", False))


def evidence_by_subject(root) -> dict:
    """Subject id -> the evidence that counts for it, as `run` reads it: archive-derived pages
    are left out unless the space has `routing.archives` on."""
    space = spaceyaml.load(root)
    evidence = gather_evidence(root, Reader(), space.name)
    if not archives_on(space):
        evidence = [ev for ev in evidence if not provenance.is_archive_derived(ev.fm)]
    return by_subject(evidence)


def set_aside_of(fm: dict) -> list:
    """The candidate's `set_aside: [{id, why}]` entries, as the agent wrote them."""
    return [item for item in fm.get("set_aside") or [] if isinstance(item, dict) and item.get("id")]


def write_candidate(root: Path, space, reg, subject, new: list, index: dict,
                    now: dt.datetime, reader: Reader, rebuild: bool = False,
                    ignored: frozenset = frozenset()) -> str:
    """Refresh (or create) the subject's candidate with `new` folded in. With `rebuild`, `new` is
    the subject's whole evidence and nothing of the old candidate but its id survives. `ignored`
    record ids (archive-derived, with archives off) never enter the evidence list."""
    path = root / CANDIDATES / f"{subject.id}.md"
    existing = _draft_or_none(path, reader)
    if existing is None:
        return "skipped"
    old_fm, old_body = existing
    target, page_fm, page_body = target_for(root, subject, reader)
    if page_fm is not None and not archives_on(space) and provenance.is_archive_derived(page_fm):
        page_body = None   # an archive draft lends no text with archives off
    aside = [] if rebuild else set_aside_of(old_fm)
    out = set(ignored) | {str(item["id"]) for item in aside}
    merged = [] if rebuild else [str(r) for r in (old_fm.get("evidence") or [])]
    merged += [ev.record for ev in new if ev.record not in merged]
    merged = [r for r in merged if r not in out]
    merged.sort(key=lambda r: (index[r].at if r in index else now, r))
    keep = bool(old_fm) and not rebuild
    base = old_body if keep else (page_body if page_body is not None
                                  else f"# {subject.title or subject.id}\n")
    body = _with_evidence(base, [evidence_line(index[r]) for r in merged if r in index])
    prior = old_fm if keep else {}
    title = (page_fm or {}).get("title") or prior.get("title") or subject.title or subject.id
    fm = {"id": old_fm.get("id") or pages.new_id(),
          "type": (page_fm or {}).get("type") or prior.get("type") or "entity",
          "title": title, "status": "draft", "generated": pages.generated(ROUTINE, now),
          "subject": subject.id, "kind": subject.kind}
    home = reg.home_of(subject.id)
    if home and home != space.name:
        fm["destination"] = home
    fm["target"] = target
    fm["evidence"] = merged
    if aside:
        fm["set_aside"] = aside
    if prior.get("high_stakes"):   # the check's flag stays until a check removes it
        fm["high_stakes"] = prior["high_stakes"]
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


def write_lesson(root: Path, space, ev: Evidence, lesson: str, source: str | None, now: dt.datetime) -> str:
    """A `lesson:` line as a company-grain candidate for the brain, one per slug: the lesson text
    alone, never the `from:` subject it came from (ring 3). A slug is proposed once per `source`,
    signed, discarded or still open; one from another source merges into the open draft of the
    same slug, or is proposed again."""
    slug = slugify(lesson)
    if any(row.get("slug") == slug and row.get("from") == source
           for row in registry_mod._jsonl(root / LESSONS_PROPOSED)):
        return "skipped"
    path = root / CANDIDATES / f"lesson-{slug}.md"
    existing = _draft_or_none(path, Reader())
    if existing is None or ev.record in [str(r) for r in existing[0].get("evidence") or []]:
        return "skipped"
    old_fm, old_body = existing
    fm = {"id": old_fm.get("id") or pages.new_id(), "type": "concept", "title": old_fm.get("title") or lesson[:80],
          "status": "draft", "generated": pages.generated(ROUTINE, now), "kind": "topics",
          "target": f"knowledge/topics/lessons/{slug}.md"}
    brain = gating.brain_space(space)
    if brain is not None and brain.name != space.name:
        fm["destination"] = brain.name
    fm["evidence"] = [str(r) for r in old_fm.get("evidence") or []] + [ev.record]
    pages.write(path, fm, old_body or f"{lesson}\n")
    proposed = root / LESSONS_PROPOSED
    proposed.parent.mkdir(parents=True, exist_ok=True)
    with proposed.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"slug": slug, "from": source, "candidate": fm["id"], "at": pages.iso(now)}) + "\n")
    return "refreshed" if old_fm else "new"


def run(root, now: dt.datetime | None = None, rebuild: bool = False,
        subject_id: str | None = None) -> DistillReport:
    """One distill pass. `rebuild` writes each subject candidate from scratch; with a
    `subject_id` only that subject's candidate is rebuilt and the watermark stays."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    reg = registry_mod.Registry(root, space)
    reader = Reader()
    report = DistillReport()
    since = stamp_of(read_watermark(root, ROUTINE).get("at"))
    evidence = gather_evidence(root, reader, space.name)
    ignored: frozenset = frozenset()
    if not archives_on(space):
        ignored = frozenset(ev.record for ev in evidence if provenance.is_archive_derived(ev.fm))
        evidence = [ev for ev in evidence if not provenance.is_archive_derived(ev.fm)]
    index = {ev.record: ev for ev in evidence}
    new = new_since(evidence, since)
    asked = asked_subjects(root)
    everything = by_subject(evidence) if rebuild or subject_id else {}

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

    def held(sid: str | None) -> bool:
        """A candidate addressed to another space while the gate holds its subject."""
        home = reg.home_of(sid) if sid else None
        if home and home != space.name and gating.gate(space, reg, sid, home):
            report.unclassified += 1
            return True
        return False

    def notes() -> dict:
        out = {"candidates_written": report.candidates_written, "refreshed": report.refreshed,
               "skipped": report.skipped, "subjects": report.subjects, "unclassified": report.unclassified}
        if rebuild or subject_id:
            out["rebuild"] = subject_id or True
        return out

    if subject_id:
        # one subject on request: rebuilt from what it has now, whether it moved or not; the
        # rest of the queue and the watermark wait for the next ordinary run
        subject = reg.get(subject_id)
        if subject is None:
            raise LookupError(f"{subject_id} is not in the registry")
        items = everything.get(subject_id, [])
        if subject.kind in MENTION_BLIND_KINDS and not any(ev.subject == subject_id for ev in items):
            report.skipped += 1
        elif not held(subject_id):
            count(write_candidate(root, space, reg, subject, items, index, now, reader,
                                  rebuild=True, ignored=ignored), subject_id)
        report.cost_tokens = reader.cost_tokens
        append_run(root, ROUTINE, space, now, report.cost_tokens, notes())
        return report

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
        if held(sid):
            continue
        chosen = everything.get(sid, items) if rebuild else items
        count(write_candidate(root, space, reg, subject, chosen, index, now, reader,
                              rebuild=rebuild, ignored=ignored), sid)
    for ev in new:
        if not ev.landed and decision_title(ev.body) and not held(ev.subject):
            count(write_decision(root, space, reg, ev, now, reader), None)
        for item in ev.fm.get("informs") or []:   # a decision the router saw in the record
            if isinstance(item, dict) and item.get("kind") == "decision" and str(item.get("text") or "").strip() \
                    and not held(ev.subject):
                said = dataclasses.replace(ev, body=f"Decision: {str(item['text']).strip()}\n\n{ev.body}")
                count(write_decision(root, space, reg, said, now, reader), None)
    # lessons are read from the daily note itself: a swept copy of an entry has lost its `from:` line
    for ev in new_since(journal_evidence(root), since):
        source = next((s for s in link.FROM_LINE.findall(ev.body) if s), None)
        for lesson in LESSON_LINE.findall(ev.body):
            count(write_lesson(root, space, ev, lesson, source, now), None)
    write_watermark(root, ROUTINE, {"at": pages.iso(now)})
    report.cost_tokens = reader.cost_tokens
    append_run(root, ROUTINE, space, now, report.cost_tokens, notes())
    return report


# ----- verify and checked (understand-first design 4.7) --------------------------------------
SRC_RE = re.compile(r"\[src:\s*([^\]]*)\]", re.I)
SRC_CLOSES = re.compile(r"\[src:\s*[^\]\s][^\]]*\]\s*$", re.I)
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})(?:\s|$)")
BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
PLACEHOLDERS = ("[unknown", "[?", "?]", "[todo", "[tbd")


def body_units(body: str) -> list:
    """(line number, heading?, text) for every heading, bullet and paragraph outside
    `## New evidence`. A paragraph's lines (and a bullet's continuation lines) are joined, so
    the source marker has to close the paragraph; each bullet starts a unit of its own."""
    units, current, in_evidence = [], None, False
    for number, line in enumerate(body.splitlines(), 1):
        head = HEADING_RE.match(line)
        if head:
            current = None
            if len(head.group(1)) <= 2:   # the section runs to the next heading of its level
                in_evidence = line.strip() == EVIDENCE_HEADING
            if not in_evidence:
                units.append([number, True, line.strip()])
            continue
        if in_evidence:
            continue
        if not line.strip():
            current = None
        elif current is None or BULLET_RE.match(line):
            current = [number, False, line.strip()]
            units.append(current)
        else:
            current[2] += " " + line.strip()
    return units


def without_evidence(body: str) -> str:
    """The body without its `## New evidence` section, which runs to the next heading of its
    level. That list is for the signer: record titles can name what the page leaves out."""
    kept, in_evidence = [], False
    for line in body.splitlines(keepends=True):
        head = HEADING_RE.match(line)
        if head and len(head.group(1)) <= 2:
            in_evidence = line.strip() == EVIDENCE_HEADING
        if not in_evidence:
            kept.append(line)
    return "".join(kept)


def candidate_path(root: Path, candidate) -> Path:
    path = Path(candidate)
    if path.is_absolute() or path.exists():
        return path
    named = Path(root) / CANDIDATES / f"{candidate}.md"   # a subject id names its candidate
    return named if path.suffix != ".md" and named.exists() else Path(root) / path


def verify(root, candidate) -> list:
    """Every problem that keeps a candidate from the signer, one line each; [] when clean. Facts
    only: never-leaves and left-out matter is the page check's judgement."""
    root = Path(root)
    text = candidate_path(root, candidate).read_text(encoding="utf-8")
    fm, body = pages.split(text)
    offset = text[:len(text) - len(body)].count("\n")   # report lines of the file, not the body
    return verify_body(root, fm, body, offset)


def verify_body(root, fm: dict, body: str, offset: int = 0) -> list:
    """The problems `verify` reports for `body` against the frontmatter `fm` of its candidate.
    A source passes when it is in `evidence`, or is the `q-` id of a question of the candidate's
    subject that was answered or settled. `offset` is the number of lines before `body`."""
    import sign_questions   # here and not at the top: sign_questions imports distill
    evidence = {str(r) for r in fm.get("evidence") or []}
    subject = str(fm.get("subject") or "")
    answered = None   # the subject's citable questions, read from the ledger at the first `q-` source
    problems = []
    for number, heading, unit in body_units(body):
        where = f"line {number + offset}"
        if not heading and not SRC_CLOSES.search(unit):
            short = unit if len(unit) <= 80 else unit[:77] + "..."
            problems.append(f"{where}: no source: {short}")
        for marker in SRC_RE.findall(unit):
            for rid in (r.strip() for r in marker.split(",")):
                if not rid or rid in evidence:
                    continue
                if rid.startswith("q-"):
                    if answered is None:
                        answered = {q.id for q in sign_questions.load(root).values()
                                    if q.subject == subject and q.status in ("answered", "settled")}
                    if rid in answered:
                        continue
                problems.append(f"{where}: source {rid} is not in evidence")
        mark = next((m for m in PLACEHOLDERS if m in unit.lower()), None)
        if mark:
            problems.append(f"{where}: placeholder `{mark}`")
    return problems


def checked(root, candidate, by: str, now: dt.datetime | None = None) -> list:
    """Stamp `checked: {by, at}` on a clean candidate; the problems, and no stamp, otherwise."""
    problems = verify(root, candidate)
    if problems:
        return problems
    path = candidate_path(Path(root), candidate)
    fm, body = pages.read(path)
    now = now or spaceyaml.load(root).now()
    fm["checked"] = {"by": by, "at": pages.iso(now)}
    pages.write(path, fm, body)
    return []


# ----- prepare: which pages need their questions prepared ------------------------------------------
def is_subject_candidate(path: Path, fm: dict) -> bool:
    """A candidate for a subject's own page: it names a subject, and is not a lesson, a decision,
    a review batch, a released record or a source."""
    return bool(fm.get("subject")) and not path.stem.startswith("lesson-") \
        and fm.get("type") not in ("decision", "source") \
        and not fm.get("review_batch") and not fm.get("released_from")


def to_prepare(root) -> list:
    """The subject candidates whose questions need preparing, by file name: {candidate (the file's
    stem), subject, why}. A subject needs it when it was never checked or has a record that
    arrived since its last check (`sign_questions.needs_recheck`). Reads only."""
    import sign_questions   # here and not at the top: sign_questions imports distill
    root = Path(root)
    found = []
    base = root / CANDIDATES
    if base.is_dir():
        for path in sorted(base.glob("*.md")):
            try:
                fm, _ = pages.read(path)
            except Exception:
                continue
            if is_subject_candidate(path, fm):
                found.append((path.stem, str(fm["subject"])))
    grouped = evidence_by_subject(root) if found else {}   # the space read once, not once per candidate
    rows = []
    for stem, subject in found:
        if sign_questions.needs_recheck(root, subject, grouped):
            never = sign_questions.last_checked(root, subject) is None
            rows.append({"candidate": stem, "subject": subject,
                         "why": "never checked" if never else "new records since the last check"})
    return rows


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    flags: dict = {}
    positional = []
    while argv:
        arg = argv.pop(0)
        if arg == "--rebuild":
            flags["rebuild"] = True
        elif arg in ("--subject", "--by", "--body") and argv:
            flags[arg[2:]] = argv.pop(0)
        else:
            positional.append(arg)
    root = Path(positional.pop(0)) if positional and positional[0] not in COMMANDS else Path(".")
    if "body" in flags and positional[:1] != ["verify"]:   # --body belongs to verify alone
        print(__doc__)
        return 2
    if positional:
        command, args = positional[0], positional[1:]
        if command not in COMMANDS or len(args) != (0 if command == "prepare" else 1) \
                or (command == "checked" and not flags.get("by")):
            print(__doc__)
            return 2
        if command == "prepare":
            rows = to_prepare(root)
            print("\n".join(f"{row['candidate']}\t{row['subject']}\t{row['why']}" for row in rows)
                  or "nothing to prepare")
            return 0
        try:
            if command == "checked":
                problems = checked(root, args[0], flags["by"])
            elif "body" in flags:   # a write-up, checked against the candidate's frontmatter
                fm, _ = pages.split(candidate_path(root, args[0]).read_text(encoding="utf-8"))
                # utf-8-sig, as sign.write_up: an editor may save the write-up with a BOM
                problems = verify_body(root, fm, Path(flags["body"]).read_text(encoding="utf-8-sig"))
            else:
                problems = verify(root, args[0])
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print("\n".join(problems) if problems else ("clean" if command == "verify" else "checked"))
        return 1 if problems else 0
    if flags.get("subject") and not flags.get("rebuild"):
        print(__doc__)
        return 2
    try:
        report = run(root, rebuild=bool(flags.get("rebuild")), subject_id=flags.get("subject"))
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"distill: {report.candidates_written} candidates written, {report.refreshed} refreshed, "
          f"{report.skipped} skipped, {report.cost_tokens} tokens; subjects: "
          f"{', '.join(report.subjects) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
