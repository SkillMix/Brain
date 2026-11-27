"""The sweep: feed records become witness pages, attachments, queue items and signals.

Deterministic from files: an adapter exports records to `_meta/state/feed/<source>.jsonl`
(one JSON object per line); this tool reads every record newer than
`_meta/state/sweep-watermark.yaml` that has no witness page yet and

1. writes the witness page `knowledge/sources/<yyyy>/<mm>/<record-id>.md` (a draft, never
   signed);
2. attaches the record to the one subject whose `match:` identifier it carries (a fixed
   identifier, a mail domain on one of its addresses, a keyword in the title of a call or
   event). None or several: no attachment, the names in the text are kept as `mentions`
   (hints only);
3. for an attached record whose subject lives elsewhere (`home` is another space), applies
   the release rule without the router (rules.md section 6). Nothing here reads what a record
   says, so nothing leaves on a judgement of its topic: audience already covering the
   destination -> at once, shallow; a call with participants outside the crew (an address
   counts as the registered person it belongs to; the subject's own mail domain is its
   counterpart, not an outsider), a record attached by inference (domain or keywords only) or
   re-attached from an older page, and anything else -> held in doubt, where one yes from its
   person releases it (a standing choice {destination, kind} makes yes the recommendation, never
   the answer). The person's own journal note pointing into code linked to its subject
   (link.journal_records) leaves after the window: at once only when the router judged it (D8).
   A day's digest of the person's notes (`kind: digest`) never leaves here: only as the parts
   the router cut (route.py).
   A content-free
   signal goes to the subject's home for every attached record queued for the window (one that
   leaves at once arrives itself). A record matching several
   subjects with a home elsewhere is held in doubt and shown as a count;
4. advances the watermark and appends one run line (cost = characters of records // 4).

A cloud run's feed and private zone go with its clone, so two tracked files keep what a later
run needs and nothing a record says: `_meta/state/attachments-unread.jsonl` the record id and
name of each attachment a run could not read, and `_meta/state/unsure-ids.jsonl` a pointer to each
record parked as unsure (unsure.py). Before it writes a page, the sweep runs the door's context
check over the records parked here (`unsure.door`); after, it compares every waiting pointer with
the pages it wrote (`unsure.resolve`) and lets go of those 90 days old (`unsure.expire`).

A call record says what it holds (`transcript_problem`): `transcript: complete` with utterance
lines, or `pending` or `none` with a `transcript_why`. One that says neither is refused: no page,
a `refuse` plan line, `refused` in the run line, and its feed's mark stays before it, so the fixed
line is read next run. A `pending` transcript goes on the attachments-unread list under the name
`transcript`: `--attachments` lists it every run until a record `<record id>/transcript` is
swept, or `--attachment-read` says the notetaker has none.

What a record says about itself is never trusted to widen anything. Its `source` is the feed
file it sits in, whatever the line claims; its `audience` may only narrow its source's declared
one (owner, then a list of names, then crew, then everyone); an `identifiers.subject` counts only
on the journal feed, which link.journal_records writes. Its subject, sender and people reach a
page as one line each (`needs_you.untrusted_line`), and the today snapshot makes their links,
images and HTML inert (`needs_you.md_inline`). A record's file name has no control character or
device name (`pages.record_file_name`), and a page that still cannot be written (a link in the
way, a name the system refuses) is skipped with a plan line and counted as `unwritable`, so one
record never stops the run. Every write goes through `pages`, never through a link.

    python sweep.py [root] [--dry-run]     --dry-run prints the plan and writes nothing
    python sweep.py [root] --attachments   the attachments to read as document records: those
                                           on records of exactly one subject, not read yet,
                                           then those an earlier run could not read; one line
                                           each, `<record id> TAB <subject> TAB <name> TAB
                                           <handle>`, a control character in an id or a name
                                           shown escaped (`\\u000a`)
    python sweep.py [root] --attachment-unread <handle>
                                           one this run could not read: every later run lists
                                           it again until a page for it exists
    python sweep.py [root] --attachment-read <handle>
                                           one listed is read (a file with no text gets no page)

A handle (`att-` and twelve hex digits) names one attachment of one record and never needs
quoting, so a name a sender chose never passes through a shell. It is the only value either
command takes: anything that is not a handle prints the usage.
    python sweep.py [root] --refetch       the records parked by a cloud run that a later match
                                           settled, to fetch again by id: `<source> TAB <signal>
                                           TAB <id>` (unsure.refetch); writes nothing
    python sweep.py [root] --cursor <source>
                                           where the export of that source starts: its feed
                                           file's newest record, else its mark, else 90 days
                                           back; writes nothing
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import classify as gating  # noqa: E402
import needs_you  # noqa: E402
import pages  # noqa: E402
import registry  # noqa: E402
import release  # noqa: E402
import spaceyaml  # noqa: E402
import unsure  # noqa: E402

FEED = Path("_meta") / "state" / "feed"
WATERMARK = Path("_meta") / "state" / "sweep-watermark.yaml"
WITNESS_ROOT = Path("knowledge") / "sources"
RESOURCES = Path("_meta") / "access" / "resources.yaml"
UNREAD = Path("_meta") / "state" / "attachments-unread.jsonl"   # tracked: record ids and names only
CURSOR_DAYS = 90   # how far back the first export of a source reads
LINKS = Path("_meta") / "state" / "links.json"   # the repository feed's audience (link.check)
TITLE_MAX = 200    # a record's subject as a page title: one line, at most this long
PEOPLE_MAX = 320   # one sender or recipient as a page keeps it
JOURNAL_FEED = "journal"   # the one feed whose `identifiers.subject` a tool wrote (link.journal_records)
HANDLE_RE = re.compile(r"^att-[0-9a-f]{12}\Z")


@dataclass
class SweepReport:
    witness_written: int = 0
    attached: int = 0
    queued: int = 0
    signals: int = 0
    held_in_doubt: int = 0
    held_other: int = 0
    security_notices: int = 0
    unwritable: int = 0   # records whose page could not be written: skipped, named in the plan
    refused: int = 0      # call records that do not say what they hold: no page, read again next run
    watermark: str | None = None
    records_seen: int = 0
    cost_tokens: int = 0
    plan: list = field(default_factory=list)


@dataclass
class Verdict:
    """Either a queue class (at_once | window) or a hold reason (never_leaves | left_out | doubt)."""
    why: str
    cls: str | None = None
    hold: str | None = None
    stage_after: dt.datetime | None = None
    extra: dict = field(default_factory=dict)


# ----- reading ---------------------------------------------------------------
def read_watermark(root: Path) -> dt.datetime | None:
    path = root / WATERMARK
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return pages.parse_iso(data["at"]) if isinstance(data, dict) and data.get("at") else None


def read_feed_marks(root: Path) -> dict:
    """feed name -> the newest record time swept from it. A feed without a mark (a source
    connected later, or a watermark file from before the marks) is read in full once: a record
    whose witness page exists is skipped anyway."""
    path = root / WATERMARK
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    marks = data.get("feeds") if isinstance(data, dict) else None
    out = {}
    for name, at in (marks or {}).items():
        try:
            out[str(name)] = pages.parse_iso(at)
        except (TypeError, ValueError):
            continue
    return out


def cursor(root, source: str, now: dt.datetime | None = None) -> str:
    """Where the next export of `source` starts: the newest record time in its feed file, else
    the newest record time the sweep took from that feed (its mark), else CURSOR_DAYS back. A
    cloud run's fresh clone has no feed files, so it starts from the marks. An upcoming event
    says nothing about how far the export read, so a time after now does not count. Writes
    nothing."""
    root = Path(root)
    now = now or spaceyaml.load(root).now()
    path = root / FEED / f"{source}.jsonl"
    times = [pages.parse_iso(r["at"]) for r in _feed_records(path)] if path.is_file() else []
    times = [at for at in times if at <= now]
    if times:
        return pages.iso(max(times))
    mark = read_feed_marks(root).get(str(source))
    return pages.iso(mark if mark is not None else now - dt.timedelta(days=CURSOR_DAYS))


def home_reads(dest_space, record: dict) -> bool:
    """The subject's home sweeps this record's channel, tracker project or repository itself:
    it gets the record on its own, so nothing is released to it."""
    if dest_space is None:
        return False
    ident = record.get("identifiers") or {}
    for source in dest_space.sources:
        if not isinstance(source, dict):
            continue
        channels = source.get("channels") or []
        channels = channels if isinstance(channels, list) else [channels]
        if ident.get("channel") and str(ident["channel"]) in {str(c) for c in channels}:
            return True
        if ident.get("tracker") and str(ident["tracker"]) in {str(source.get("project") or ""), str(source.get("team") or "")}:
            return True
        if ident.get("repo") and str(ident["repo"]).lower() == str(source.get("repo") or "").lower():
            return True
    return False


def _feed_records(path: Path):
    """The readable records of one feed file: a JSON object with an id and a time that parses.
    Any other line is skipped."""
    for line in path.read_text(encoding="utf-8").split("\n"):   # a record's text may hold U+2028
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not (isinstance(record, dict) and record.get("id") and record.get("at")):
            continue
        try:
            pages.parse_iso(record["at"])
        except (TypeError, ValueError):
            continue  # an unreadable timestamp never stalls the sweep
        yield record


def read_feed(root: Path) -> list:
    """Every readable record of every feed file. A record's `source` is its file's name, whatever
    the line says (a mail line claiming `wiki` would take the wiki's audience), and only the
    journal feed's `identifiers.subject` is kept (`_bind`)."""
    records = []
    feed = root / FEED
    if not feed.is_dir():
        return records
    for path in sorted(feed.glob("*.jsonl")):
        for record in _feed_records(path):
            _bind(record, path.stem)
            record["_feed"] = path.stem   # the declared source this file is (space.yaml sources)
            _scrub_record(record)   # a link's token never reaches a page
            records.append(record)
    return records


def _scrub_record(record: dict) -> dict:
    """The record with a token in a link cut out of every field a witness page writes: its text,
    its subject, each identifier and each attachment name (`scan.scrub_link_tokens`)."""
    if isinstance(record.get("text"), str):
        record["text"] = _scrub_links(record["text"])
    if isinstance(record.get("subject"), str):
        record["subject"] = _scrub_links(record["subject"])
    if isinstance(record.get("identifiers"), dict):
        record["identifiers"] = {k: (_scrub_links(v) if isinstance(v, str) else v)
                                 for k, v in record["identifiers"].items()}
    if isinstance(record.get("attachments"), list):
        record["attachments"] = [
            _scrub_links(a) if isinstance(a, str)
            else dict(a, name=_scrub_links(a["name"])) if isinstance(a, dict) and isinstance(a.get("name"), str)
            else a
            for a in record["attachments"]]
    return record


def _bind(record: dict, source: str) -> dict:
    """The record as the feed it came from vouches for it: `source` is that feed, and an
    `identifiers.subject` (which attaches a record to a subject with no inference, so nothing
    holds it in doubt) survives only on the journal feed, where a tool wrote it."""
    record["source"] = source
    ident = record.get("identifiers")
    if isinstance(ident, dict) and "subject" in ident and source != JOURNAL_FEED:
        record["identifiers"] = {k: v for k, v in ident.items() if k != "subject"}
    return record


def declared_feed(root: Path, space: spaceyaml.Space) -> list:
    """The feed records of the sources this space declared; the brain sweeps only its own
    `repository` feed (a product company's repository), nothing else of its own.
    A personal space always reads its `journal` feed (link.journal_records), and every space its
    `repository` feed (the codebases link.check lists in `_meta/state/links.json`), declared or not."""
    declared = {str(s.get("name")) for s in space.sources if isinstance(s, dict) and s.get("name")}
    if declared and space.profile == "personal":
        declared.add("journal")
    if declared:
        declared.add("repository")
    feed = read_feed(root)
    if space.profile == "brain":
        feed = [r for r in feed if r.get("_feed") == "repository"]
    return [r for r in feed if str(r.get("_feed")) in declared] if declared else feed


def attachments(root) -> list:
    """(record id, subject id, attachment) for every attachment on a feed record of exactly one
    subject that has no document record `<record id>/<attachment>` yet: what the export reads
    next, each as a document record of its own. Then every attachment an earlier run could not
    read and nothing has read since (`unread`), with no subject: its record may have left the
    feed with that run's clone. Writes nothing."""
    root = Path(root)
    space = spaceyaml.load(root)
    reg = registry.Registry(root, space)
    feed = declared_feed(root, space)
    have = {str(r["id"]) for r in feed}
    out = []
    for record in feed:
        if record.get("kind") in ("document", "change"):   # its attachment points at what it holds
            continue
        names = [str(a.get("name") or "") if isinstance(a, dict) else str(a)
                 for a in record.get("attachments") or []]
        names = [n for n in names if n.strip()]
        hits = reg.match(record) if names else []
        if len(hits) != 1 and not space.routing_on:   # with routing on, capture reads them all
            continue
        sid = hits[0].id if len(hits) == 1 else ""
        out += [(str(record["id"]), sid, name) for name in names
                if f"{record['id']}/{name}" not in have]
    listed = {(rid, name) for rid, _, name in out}
    return out + [(rid, "", name) for rid, name in unread(root, have) if (rid, name) not in listed]


# ----- attachments a run could not read --------------------------------------------------------
def _unread_rows(root: Path) -> tuple:
    """The (record id, name) pairs UNREAD lists, oldest first, and the set of those marked read."""
    path = Path(root) / UNREAD
    listed, read = [], set()
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not (isinstance(row, dict) and row.get("id") and row.get("name")):
            continue
        pair = (str(row["id"]), str(row["name"]))
        if row.get("read"):
            read.add(pair)
        elif pair not in listed:
            listed.append(pair)
    return listed, read


def _append_unread(root: Path, rows: list) -> None:
    """Lines are only ever appended: the file merges by union, so a line taken out would come back."""
    pages.append_text(Path(root) / UNREAD, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                      root=root)


def attachment_handle(record_id: str, name: str) -> str:
    """The handle of one attachment of one record: `att-` and twelve hex digits of a hash of both,
    so a command names it with nothing to quote (a sender chose the name, and may have chosen
    the record id too)."""
    return "att-" + hashlib.sha1(f"{record_id}\0{name}".encode("utf-8")).hexdigest()[:12]


def by_handle(root, handle: str) -> tuple | None:
    """The (record id, name) pair a handle names: an attachment `attachments` lists, or one the
    unread file holds, read or not; None when no attachment has it."""
    listed, read = _unread_rows(Path(root))
    for rid, name in [(rid, name) for rid, _, name in attachments(root)] + listed + sorted(read):
        if attachment_handle(rid, name) == handle:
            return rid, name
    return None


def _escaped(text: str) -> str:
    """An id or a name for one tab-separated line: a control character, line break, tab or
    invisible mark shown as its escape (`\\u000a`), so a name can never add or shift a row."""
    return "".join(f"\\u{ord(ch):04x}" if needs_you._UNSAFE.match(ch) else ch for ch in str(text))


def unread(root, have=()) -> list:
    """(record id, attachment name) for every attachment a run could not read and nothing has
    read since: no read line, no document record `<record id>/<name>` among `have` (the ids of
    the feed at hand) and no witness page of one. Writes nothing."""
    root = Path(root)
    listed, read = _unread_rows(root)
    have = set(have)
    pending = [(rid, name) for rid, name in listed if (rid, name) not in read and f"{rid}/{name}" not in have]
    if not pending:
        return []
    by_name: dict = {}
    folder = root / WITNESS_ROOT
    for path in folder.rglob("*.md") if folder.is_dir() else []:
        by_name.setdefault(path.stem, []).append(path)

    def witnessed(record_id: str) -> bool:
        for path in by_name.get(file_name(record_id), []):
            try:
                if str(pages.read(path)[0].get("record") or "") == record_id:
                    return True
            except Exception:
                continue
        return False
    return [(rid, name) for rid, name in pending if not witnessed(f"{rid}/{name}")]


def note_unread(root, record_id: str, name: str, now: dt.datetime | None = None) -> bool:
    """`--attachment-unread`: an attachment this run could not read. Its record id and name go
    to UNREAD, never its content, once per pair, so every later run lists it again until it is
    read, a cloud run's fresh clone included. True when it was not there yet."""
    root = Path(root)
    listed, read = _unread_rows(root)
    if (str(record_id), str(name)) in set(listed) | read:
        return False
    _append_unread(root, [{"id": str(record_id), "name": str(name),
                           "at": pages.iso(now or spaceyaml.load(root).now())}])
    return True


def mark_read(root, record_id: str, name: str, now: dt.datetime | None = None) -> bool:
    """`--attachment-read`: one listed attachment is read (a file with no text gets no witness
    page to say so). True when it was listed and not read yet."""
    root = Path(root)
    listed, read = _unread_rows(root)
    pair = (str(record_id), str(name))
    if pair not in listed or pair in read:
        return False
    _append_unread(root, [{"id": pair[0], "name": pair[1], "at": pages.iso(now or spaceyaml.load(root).now()),
                           "read": True}])
    return True


def declared_audience(space: spaceyaml.Space, name) -> str | list:
    """A source's declared audience: space.yaml's sources, then resources.yaml, then for the
    repository feed the audience link.check wrote to links.json (else the one it would write),
    else owner."""
    for source in space.sources:
        if isinstance(source, dict) and source.get("name") == name and source.get("audience"):
            return source["audience"]
    path = space.root / RESOURCES
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for source in (data.get("resources") or []) if isinstance(data, dict) else []:
            if isinstance(source, dict) and source.get("name") == name and source.get("audience"):
                return source["audience"]
    if name == "repository":
        try:
            found = json.loads((space.root / LINKS).read_text(encoding="utf-8")).get("audience")
        except (OSError, ValueError, AttributeError):
            found = None
        return found or {"brain": "everyone", "project": "crew"}.get(space.profile, "owner")
    return "owner"


def _rank(audience) -> int | None:
    """How wide an audience is: owner 0, a list of names 1, crew 2, everyone 3; None for a value
    that is none of these."""
    if isinstance(audience, list):
        return 1
    return {"owner": 0, "crew": 2, "everyone": 3}.get(audience) if isinstance(audience, str) else None


def audience_of(space: spaceyaml.Space, record: dict) -> str | list:
    """The record's audience when it is its source's declared audience or narrower, else the
    declared one: a record (an export that read a mail saying so) can never widen who reads it.
    A list of names narrows a list only when every name is on the declared one, and narrows
    `crew` only when every name is on this space's own audience list: a list naming someone
    outside the crew would add a reader, however narrow its rank."""
    declared = declared_audience(space, record.get("source"))
    own = record.get("audience")
    mine, theirs = _rank(own), _rank(declared)
    if not own or mine is None or theirs is None or mine > theirs:
        return declared
    if isinstance(own, list) and declared != "everyone":
        crew = declared if isinstance(declared, list) else space.audience if declared == "crew" else []
        allowed = {str(n).strip().lower() for n in crew} if isinstance(crew, list) else set()
        return own if own and all(str(n).strip().lower() in allowed for n in own) else declared
    return own


def file_name(record_id) -> str:
    """A record id as a file name every system and every link accepts (no colon, slash, hash,
    control character or other reserved sign, never a device name), short enough for every path
    limit: a long id keeps its start and a hash of the whole (`pages.record_file_name`)."""
    return pages.record_file_name(record_id)


def witness_path(root: Path, record: dict) -> Path:
    at = pages.parse_iso(record["at"])
    return root / WITNESS_ROOT / f"{at:%Y}" / f"{at:%m}" / f"{file_name(record['id'])}.md"


def record_text(record: dict) -> str:
    return f"{record.get('subject', '')}\n{record.get('text', '')}"


TRANSCRIPT_STATES = ("complete", "pending", "none")
TRANSCRIPT_NAME = "transcript"   # a pending transcript waits on the attachments list as `<record id>/transcript`


def transcript_problem(record: dict) -> str | None:
    """Why a call record is refused, or None: it says `transcript: complete` and holds utterance lines,
    or says `pending` or `none` with a `transcript_why`. Any notetaker, any connector: the export says
    what it could fetch, so a summary never passes for the meeting."""
    if record.get("kind") != "call":
        return None
    state = str(record.get("transcript") or "").strip().lower()
    if state not in TRANSCRIPT_STATES:
        return "a call record says transcript: complete, pending or none"
    if state == "complete":
        if not any(UTTERANCE.match(line) for line in str(record.get("text") or "").splitlines()):
            return "transcript: complete, yet no `[hh:mm:ss] Speaker: words` line"
    elif not str(record.get("transcript_why") or "").strip():
        return f"transcript: {state} without a transcript_why"
    return None


# ----- the witness page ------------------------------------------------------
def title_of(record: dict) -> str:
    """A record's subject as a page title: one line (`needs_you.untrusted_line`), at most
    TITLE_MAX characters; a record with none is named by its kind and sender."""
    kind = needs_you.untrusted_line(record.get("kind", "document"), 40) or "document"
    return (needs_you.untrusted_line(record.get("subject"), TITLE_MAX)
            or f"{kind} from {needs_you.untrusted_line(record.get('from'), 120) or 'unknown'}")


def _people(value) -> list:
    """A to, cc or participants field as a page keeps it: each entry one line."""
    values = value if isinstance(value, list) else [value]
    return [needs_you.untrusted_line(v, PEOPLE_MAX) for v in values if v not in (None, "")]


def witness_frontmatter(record: dict, hits: list, mentions: list, audience, now: dt.datetime) -> dict:
    kind = record.get("kind", "document")
    fm = {"id": pages.new_id(), "type": "source", "title": title_of(record), "status": "draft",
          "generated": pages.generated("sweep", now), "record": str(record["id"]),
          "source": record.get("source"), "kind": kind,
          "at": pages.iso(pages.parse_iso(record["at"])),
          "from": needs_you.untrusted_line(record.get("from", ""), PEOPLE_MAX)}
    for key in ("to", "cc", "participants"):
        if record.get(key):
            fm[key] = _people(record[key])
    if len(hits) == 1:
        fm["subject"] = hits[0].id
    fm["mentions"] = sorted({s.id for s in mentions})
    fm["audience"] = audience
    fm["identifiers"] = dict(record.get("identifiers") or {})
    if record.get("attachments"):
        fm["attachments"] = [str(a) for a in record["attachments"]]
    for key in ("issue", "state"):  # a tracker record's issue and its state: spec state reads them
        if record.get(key):
            fm[key] = str(record[key])
    for key in ("transcript", "transcript_why"):   # a call's page says what it holds: route and distill read it
        if kind == "call" and record.get(key):
            fm[key] = needs_you.untrusted_line(record[key], TITLE_MAX)
    if record.get("research"):   # exported by research for this subject: the router decides it for it
        fm["research"] = str(record["research"])
    if record.get("admitted") and unsure.door_of(record.get("door")):   # how and why the door let it in
        fm["door"] = unsure.door_of(record["door"])
    imported = record.get("imported_from") or record.get("witness_page")
    if imported:   # the text is an archive page's retelling, not the record: kept out while archives are off
        fm["imported_from"] = str(imported)
    return fm


# ----- the release rule --------------------------------------------------------
def crew_of(space: spaceyaml.Space, dest_space: spaceyaml.Space | None, reg=None) -> set:
    """Who may hear a call bound for the destination; `everyone` (the brain) is everyone
    registered in the company: each person in the registry."""
    crew = {space.owner}
    if isinstance(space.audience, list):
        crew.update(space.audience)
    if dest_space is not None:
        crew.add(dest_space.owner)
        if isinstance(dest_space.audience, list):
            crew.update(dest_space.audience)
        elif dest_space.audience == "everyone":
            crew.update(p.title for p in (reg.everywhere() if reg is not None else []) if p.kind == "people")
    return {_name_key(n) for n in crew if n}


def _name_key(name) -> str:
    """A name as the crew check compares it: case and accents ignored (a chat app shows
    `Zoë`, the audience lists `Zoe`)."""
    text = unicodedata.normalize("NFKD", str(name).strip())
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def _person_name(space: spaceyaml.Space, reg, participant) -> str:
    """A participant as the name the crew is listed by: the owner's address is the owner, a
    registered person's email, alias or id is that person; anything else stays as given."""
    text = str(participant).strip()
    address = registry.address_of(text)
    if address and address == registry.address_of(space.owner_email):
        return space.owner
    for person in (reg.everywhere() if reg is not None else []):
        if person.kind != "people":
            continue
        names = {registry.address_of(n) for n in [person.id, person.extra.get("email"), *person.aliases] if n}
        if address in names:
            return person.title
    return text


def outsiders(space: spaceyaml.Space, dest_space, participants: list, reg=None,
              subject: registry.Subject | None = None) -> list:
    crew = crew_of(space, dest_space, reg)
    own = subject.match.get("domain") if subject is not None else None
    own = {str(d).strip().lower() for d in (own if isinstance(own, list) else [own]) if d}
    out = []
    for p in participants:
        domains = registry.record_domains({"from": p})
        if domains and any(d == o or d.endswith("." + o) for d in domains for o in own):
            continue   # the subject's own people: its counterpart on the call
        if _name_key(_person_name(space, reg, p)) not in crew:
            out.append(p)
    return out


def covers(audience, subject: registry.Subject, dest_space) -> bool:
    """Does the record's audience already include the destination's readers?"""
    if audience == "everyone":
        return True
    if audience == "crew":
        if dest_space is not None:
            return dest_space.profile == "project"
        return subject.kind == "projects"
    if isinstance(audience, list) and dest_space is not None and isinstance(dest_space.audience, list):
        names = {str(n).strip().lower() for n in audience}
        return bool(names) and {str(n).strip().lower() for n in dest_space.audience} <= names
    return False


def classify(space: spaceyaml.Space, record: dict, subject: registry.Subject, destination: str,
             dest_space, audience, now: dt.datetime, reg=None, inferred: bool = False) -> Verdict:
    """Without the router nothing reads what a record says, so nothing leaves on a judgement of
    its topic: a record leaves by itself only when its destination's audience already reads it;
    anything else waits for its person's yes (a standing choice makes yes the expected answer,
    never the answer itself)."""
    kind = record.get("kind")
    if kind == "call":
        outside = outsiders(space, dest_space, record.get("participants") or [], reg, subject)
        if outside:
            return Verdict(hold="doubt",
                           why="call with participants outside the crew: " + ", ".join(outside))
    if inferred or record.get("_reattach"):
        how = "re-attached from an older page" if record.get("_reattach") else "attached by its domain or title"
        return Verdict(hold="doubt", why=f"widens who can read it; {how}, which nobody chose")
    if record.get("source") == "journal":   # the person's own note with a pointer to linked code
        path = f"journal:{destination}"   # at once only when the router judged it (D8, route.py)
        return Verdict(cls="window", stage_after=now + space.window(), extra={"path": path},
                       why="your own note with a pointer to linked code; it leaves after the window")
    paused_why = ""
    if covers(audience, subject, dest_space):
        path = f"audience:{record.get('source') or 'unknown'}"
        if not release.is_paused(space.root, path, now):
            return Verdict(cls="at_once", stage_after=now, why=f"audience {audience} already reads it",
                           extra={"path": path})
        paused_why = f"; {path} is paused"
    why = "widens who can read it and no router judged what it says" + paused_why
    if kind != "call" and any(c.get("destination") == destination and c.get("kind") == kind
                              for c in space.standing_choices):
        path = f"standing_choice:{destination}:{kind}"
        if not release.is_paused(space.root, path, now):
            return Verdict(hold="doubt", why=f"{why}; your standing choice covers {kind} -> {destination}",
                           extra={"recommendation": f"yes: your standing choice sends {kind} to {destination}"})
        why += f"; {path} is paused"
    return Verdict(hold="doubt", why=why)


def item_frontmatter(space: spaceyaml.Space, record: dict, subject_id, destination, cls: str,
                     stage_after: dt.datetime, now: dt.datetime, extra: dict | None = None) -> dict:
    kind = record.get("kind", "document")
    fm = {"id": pages.new_id(), "type": "source", "title": title_of(record), "status": "draft",
          "generated": pages.generated("sweep", now), "class": cls, "destination": destination,
          "subject": subject_id, "stage_after": pages.iso(stage_after), "listed": False,
          "evidence": [str(record["id"])], "from_space": space.name, "kind": kind}
    # when, who to whom, and the thread: the home's status page threads it and knows who moves
    try:
        fm["at"] = pages.iso(pages.parse_iso(record["at"]))
    except (KeyError, TypeError, ValueError):
        pass
    for key in ("from", "to"):
        if record.get(key):
            fm[key] = needs_you.untrusted_line(record[key], PEOPLE_MAX) if key == "from" else _people(record[key])
    thread = (record.get("identifiers") or {}).get("thread")
    if thread:
        fm["identifiers"] = {"thread": str(thread)}
    fm.update(extra or {})
    return fm


def signal_for(subject_id: str, destination: str, now: dt.datetime) -> dict:
    return {"id": pages.new_id(), "subject": subject_id, "destination": destination,
            "at": pages.iso(now), "note": "open item moved"}


# ----- the run -------------------------------------------------------------------
def run(root, now: dt.datetime | None = None, dry_run: bool = False) -> SweepReport:
    root = Path(root)
    if not dry_run:
        pages.refuse_copy(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    reg = registry.Registry(root, space)
    report = SweepReport()
    watermark = read_watermark(root)
    report.watermark = pages.iso(watermark) if watermark else None
    newest = watermark
    chars = 0
    dest_cache: dict = {}

    def dest_space_of(name: str):
        if name not in dest_cache:
            dest_cache[name] = release.destination_space(space, name)
        return dest_cache[name]

    if not dry_run:   # the door's context check: what it admits is swept in this run
        unsure.door(root, reg, now)
    feed = declared_feed(root, space)
    marks = read_feed_marks(root)
    # An attachment an earlier run could not read carries its mail's time, which the mark has
    # passed: its document record is swept all the same.
    owed = unread(root)
    owed_ids = {f"{rid}/{name}" for rid, name in owed}
    records = [r for r in feed if str(r.get("_feed")) not in marks or r.get("admitted") or r.get("backfill")
               or str(r["id"]) in owed_ids or pages.parse_iso(r["at"]) > marks[str(r.get("_feed"))]]
    newest_by_feed = dict(marks)
    for r in records:   # an upcoming event never moves a mark past this run
        at, name = min(pages.parse_iso(r["at"]), now), str(r.get("_feed"))
        if name not in newest_by_feed or at > newest_by_feed[name]:
            newest_by_feed[name] = at
    # Re-attach: a witness page written while the registry could not place its record (no
    # subject, not held in doubt) is tried again now that the registry may have caught up.
    # The page is rewritten from its own frontmatter and body, never from the feed.
    reattached = 0
    settled = set()   # records already held (doubt, left-out, never-leaves) or queued: not retried
    for folder in (release.HELD, release.QUEUE):
        for item in (root / folder).glob("*.md"):
            try:
                item_fm, _ = pages.read(item)
            except Exception:
                continue
            for rid in item_fm.get("evidence") or []:
                settled.add(str(rid))
    # With routing on the router decides where a page belongs: nothing is re-attached here.
    for path in [] if space.routing_on else sorted((root / "knowledge" / "sources").rglob("*.md")):
        if not pages.is_page_path(path):
            continue
        try:
            fm, body = pages.read(path)
        except Exception:
            continue
        if fm.get("type") != "source" or fm.get("subject") or fm.get("candidates") or int(fm.get("part") or 1) > 1:
            continue
        if int(fm.get("parts") or 1) > 1:   # a split page is re-attached whole, never part by part
            body = _existing_text(root, fm, body, {"id": str(fm.get("record") or path.stem), "at": fm.get("at")})
        if str(fm.get("record") or path.stem) in settled:
            continue
        rebuilt = {"id": fm.get("record") or path.stem, "source": fm.get("source"), "kind": fm.get("kind"),
                   "at": fm.get("at") or (fm.get("generated") or {}).get("at"), "from": fm.get("from"),
                   "to": fm.get("to") or [], "cc": fm.get("cc") or [], "subject": fm.get("title"), "text": body,
                   "identifiers": dict(fm.get("identifiers") or {}), "attachments": fm.get("attachments") or [],
                   "audience": fm.get("audience"), "participants": fm.get("participants") or []}
        _bind(rebuilt, str(rebuilt.get("source") or ""))   # bound when the page was written; a subject only from the journal
        if not rebuilt["at"] or not reg.match(rebuilt):
            continue
        if not dry_run:
            path.unlink()
        rebuilt["_reattach"] = True
        records.append(rebuilt)
        reattached += 1
    records.sort(key=lambda r: (pages.parse_iso(r["at"]), str(r["id"])))
    no_page = set()   # records this run read and gave no page
    written = []      # the front matter of every page this run wrote new, for the pointers
    refused_at: dict = {}   # feed -> its oldest refused record: its mark stays before it
    for record in records:
        at = pages.parse_iso(record["at"])
        report.records_seen += 1
        if not record.get("_reattach"):
            newest = min(at, now) if newest is None or at > newest else newest
        if is_own_notice(space, record):
            report.plan.append(f"skip {record['id']}: the workspace's own notice")
            no_page.add(str(record["id"]))
            continue
        if record.get("kind") == "document" and not str(record.get("text") or "").strip():
            report.plan.append(f"skip {record['id']}: a file with no text")   # its feed record settles it
            no_page.add(str(record["id"]))
            continue
        target = witness_path(root, record)
        shown = needs_you.untrusted_line(record["id"], 120)
        try:
            if target.exists():
                if not _refresh(root, target, record, now, dry_run, report):
                    report.plan.append(f"skip {shown}: witness page exists")
                continue
        except (OSError, ValueError) as exc:   # a link in the way, a name the system refuses
            _unwritable(report, no_page, record, exc)
            continue
        problem = None if record.get("_reattach") else transcript_problem(record)
        if problem:   # the export fixes the line in its feed; the next run reads it again
            report.plan.append(f"refuse {shown}: {problem}")
            report.refused += 1
            no_page.add(str(record["id"]))
            name = str(record.get("_feed"))
            refused_at[name] = min(at, refused_at.get(name, at))
            continue
        chars += len(json.dumps({k: v for k, v in record.items() if not str(k).startswith('_')}))
        audience = audience_of(space, record)
        hits = reg.match(record)
        mentions = reg.mentions(record)
        fm = witness_frontmatter(record, hits, mentions, audience, now)
        report.plan.append(f"witness {target.relative_to(root).as_posix()}")
        if not dry_run:
            try:
                _write_parts(root, target, record, fm, str(record.get("text") or ""))
            except (OSError, ValueError) as exc:
                report.plan.pop()
                _unwritable(report, no_page, record, exc)
                continue
            if not record.get("_reattach") and _credential_seen(record) and _security_notice(root, space, record):
                report.security_notices += 1
            if not record.get("_reattach"):
                written.append(fm)
            if record.get("kind") == "call" and str(record.get("transcript")).strip().lower() == "pending":
                note_unread(root, str(record["id"]), TRANSCRIPT_NAME, now)   # listed by --attachments until fetched
        report.witness_written += 1
        body = str(record.get("text") or "")

        if space.routing_on:   # the router decides where it goes; identifiers only suggested it
            report.attached += len(hits) == 1
            continue
        if len(hits) > 1:
            elsewhere = [(s, reg.home_of(s.id)) for s in hits]
            elsewhere = [(s, h) for s, h in elsewhere if h and h != space.name]
            if elsewhere:
                why = "matches several subjects: " + ", ".join(sorted(s.id for s in hits))
                held = item_frontmatter(space, record, None, None, "doubt", now, now,
                                        {"candidates": sorted(s.id for s in hits)})
                report.plan.append(f"hold doubt {record['id']}: {why}")
                if not dry_run:
                    release.hold(root, held, body, "doubt", why)
                report.held_in_doubt += 1
            continue
        if len(hits) != 1:
            continue
        subject = hits[0]
        report.attached += 1
        home = reg.home_of(subject.id)
        if not home or home == space.name:
            continue
        if record.get("kind") == "digest":   # a day's notes: only the parts a router cut and a second agent checked
            report.plan.append(f"keep {shown}: a digest leaves only through the router")
            continue
        if home_reads(dest_space_of(home), record):
            report.plan.append(f"keep {record['id']}: {home} reads that source itself")
            continue
        inferred = not (reg.matched_by(subject, record) - set(registry.INFERRED_KEYS))
        verdict = classify(space, record, subject, home, dest_space_of(home), audience, now, reg, inferred)
        blocked = gating.gate(space, reg, subject.id, home)   # an unclassified client, project or deal
        if verdict.hold:
            held = item_frontmatter(space, record, subject.id, home, verdict.hold, now, now,
                                    dict(verdict.extra or {}))
            report.plan.append(f"hold {verdict.hold} {record['id']} -> {home}: {verdict.why}")
            if not dry_run:   # a yes releases what is held: without the router, never in full
                release.hold(root, held, shallow(body), verdict.hold, verdict.why)
            if verdict.hold == "doubt":
                report.held_in_doubt += 1
            else:
                report.held_other += 1
        elif blocked:   # the release rule's class is kept, for when its classification lets it go
            extra = dict(verdict.extra or {}, **({"kept": True} if blocked[0] == "kept" else {}))
            held = item_frontmatter(space, record, subject.id, home, verdict.cls, verdict.stage_after, now, extra)
            report.plan.append(f"hold {blocked[0]} {record['id']} -> {home}: {blocked[1]}")
            if not dry_run:
                release.hold(root, held, shallow(body), *blocked)
            report.held_other += 1
        else:
            item = item_frontmatter(space, record, subject.id, home, verdict.cls,
                                    verdict.stage_after, now, dict(verdict.extra or {}))
            report.plan.append(f"queue {verdict.cls} {record['id']} -> {home}: {verdict.why}")
            if not dry_run:   # without the router: never in full
                pages.write(root / release.QUEUE / f"{item['id']}.md", item, shallow(body), root=root)
            report.queued += 1
        if blocked or verdict.cls == "at_once":   # gated: nothing moves; at once: the item itself arrives
            continue
        signal = signal_for(subject.id, home, now)   # content-free: that something moved
        report.plan.append(f"signal {shown} -> {home}")
        if not dry_run:
            pages.write_text(root / release.SIGNALS / f"{signal['id']}.yaml",
                             yaml.safe_dump(signal, sort_keys=False, allow_unicode=True), root=root)
        report.signals += 1

    report.cost_tokens = chars // 4
    for name, at in refused_at.items():   # a refused record is read again once its line is fixed
        newest_by_feed[name] = min(newest_by_feed[name], at - dt.timedelta(seconds=1))
    if newest is not None:
        report.watermark = pages.iso(newest)
    if dry_run:
        return report
    if (newest is not None and newest != watermark) or newest_by_feed != marks:
        data = {"at": pages.iso(newest)} if newest is not None else {}
        data["feeds"] = {name: pages.iso(at) for name, at in sorted(newest_by_feed.items())}
        pages.write_text(root / WATERMARK, yaml.safe_dump(data, sort_keys=False), root=root)
    unsure.resolve(root, written, now)
    unsure.expire(root, now)
    blank = [(rid, name) for rid, name in owed if f"{rid}/{name}" in no_page]
    if blank:   # read, yet no page says so (a file with no text): only its line here will
        _append_unread(root, [{"id": rid, "name": name, "at": pages.iso(now), "read": True}
                              for rid, name in blank])
    try:
        write_today(root, space, now)
    except pages.UnsafePath:   # a link where the snapshot goes: named, never written through
        report.plan.append("live/today.md is a link; not written")
    except Exception:  # a snapshot never stops the sweep
        pass
    release.append_run_line(root, "sweep", now, space, report.cost_tokens, {
        "records": report.records_seen, "witness": report.witness_written,
        "attached": report.attached, "queued": report.queued, "signals": report.signals,
        "held_in_doubt": report.held_in_doubt, "held_other": report.held_other,
        "reattached": reattached, "security_notices": report.security_notices,
        **({"unwritable": report.unwritable} if report.unwritable else {}),
        **({"refused": report.refused} if report.refused else {})})
    return report


def _unwritable(report: SweepReport, no_page: set, record: dict, exc: Exception) -> None:
    """A record whose page cannot be written: named in the plan and counted, and the run goes on
    to the next record, so one bad name never stops every later run."""
    report.plan.append(f"skip {needs_you.untrusted_line(record['id'], 120)}: unwritable page "
                       f"({type(exc).__name__})")
    report.unwritable += 1
    no_page.add(str(record["id"]))


# ----- without the router, what leaves by identifier stays shallow -----------------------------
CAP_WITHOUT_ROUTING = 8_000
UTTERANCE = pages.UTTERANCE_RE


def shallow(text: str) -> str:
    """What the identifier path may release now that capture is lossless: no transcript lines
    (`[hh:mm:ss] Speaker: words`), at most CAP_WITHOUT_ROUTING characters, cut at a line. Only the
    judgment router sends verbatim parts of a record."""
    kept, size = [], 0
    for line in str(text or "").splitlines():
        if UTTERANCE.match(line):
            continue
        if size + len(line) + 1 > CAP_WITHOUT_ROUTING:
            kept.append("[cut]")
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept) + ("\n" if kept else "")


# ----- lossless pages: refresh in place, split on lines, the workspace's own notices out ----
PART_BYTES = 900_000   # the sync never lands a file over 1 MB
OWN_NOTICE = re.compile(r"^\s*((re|aw|sv|fwd?)\s*:\s*)*(\[shadow\]\s*)?(morning briefing|security check|"
                        r"action needed|for your info)\b", re.I)


def is_own_notice(space: spaceyaml.Space, record: dict) -> bool:
    """The workspace's own briefings and notices, and replies to them: the reply reader takes
    them from the feed; they never become pages (they may list records kept out)."""
    own = registry.address_of(space.owner_email)
    if not own or "@" not in own or registry.address_of(record.get("from")) != own:
        return False
    to = [registry.address_of(t) for t in record.get("to") or []]
    return own in to and bool(OWN_NOTICE.match(str(record.get("subject") or "")))


def split_text(text: str, limit: int) -> list:
    """Line-boundary chunks of at most `limit` bytes; a single longer line is cut by bytes."""
    parts, current, size = [], [], 0
    for line in text.splitlines(keepends=True):
        data = line.encode("utf-8")
        while len(data) > limit:   # one line over the limit: cut it, never drop it
            if current:
                parts.append("".join(current))
                current, size = [], 0
            cut = data[:limit].decode("utf-8", errors="ignore")
            parts.append(cut)
            data = data[len(cut.encode("utf-8")):]
        if current and size + len(data) > limit:
            parts.append("".join(current))
            current, size = [], 0
        if data:
            current.append(data.decode("utf-8"))
            size += len(data)
    if current:
        parts.append("".join(current))
    return parts or [""]


def _part_paths(root: Path, record: dict, count: int) -> list:
    return [witness_path(root, record)] + [witness_path(root, dict(record, id=f"{record['id']}#{k}"))
                                           for k in range(2, count + 1)]


def _write_parts(root: Path, target: Path, record: dict, fm: dict, text: str) -> None:
    """One page, or a page per part when the text is over PART_BYTES (part 1 is the record's own
    page and carries the attachment; every part says which it is)."""
    chunks = split_text(text, PART_BYTES) if len(text.encode("utf-8")) > PART_BYTES else [text]
    if len(chunks) == 1:
        pages.write(target, fm, text, root=root)
        return
    for k, (path, chunk) in enumerate(zip(_part_paths(root, record, len(chunks)), chunks), start=1):
        part_fm = dict(fm, part=k, parts=len(chunks))
        if k > 1:
            part_fm = dict(part_fm, id=pages.new_id(), record=f"{record['id']}#{k}")
            part_fm.pop("subject", None)
        pages.write(path, part_fm, chunk, root=root)


def _existing_text(root: Path, fm: dict, body: str, record: dict) -> str:
    parts = int(fm.get("parts") or 1)
    if parts == 1:
        return body
    texts = [body]
    for path in _part_paths(root, record, parts)[1:]:
        texts.append(pages.read(path)[1] if path.exists() else "")
    return "".join(texts)


def _as_stored(text: str) -> str:
    """Text as a page keeps it (bare newlines, no trailing spaces): a refresh compares like with like."""
    return "\n".join(line.rstrip() for line in str(text).replace("\r\n", "\n").split("\n")).strip()


def _refresh(root: Path, target: Path, record: dict, now: dt.datetime, dry_run: bool, report) -> bool:
    """A deeper re-export of a record whose page exists: the page keeps its id and frontmatter,
    takes the longer text (or the record's own text, when the page held an archive's retelling),
    and loses its routing decision so the router reads it again."""
    if record.get("_reattach"):
        return False
    try:
        old_fm, old_body = pages.read(target)
    except Exception:
        return False
    new_text = str(record.get("text") or "")
    # an archive page's retelling gives way to the record itself, however short the record is
    original = old_fm.get("imported_from") and not (record.get("imported_from") or record.get("witness_page"))
    if old_fm.get("status") == "stable" or not new_text.strip() or (not original and len(_as_stored(new_text))
            <= len(_as_stored(_existing_text(root, old_fm, old_body, record)))):
        return False
    fm = {k: v for k, v in old_fm.items() if k not in ("routing", "part", "parts", "imported_from")}
    # the deeper export knows the people and the thread better than the shallow one did
    if record.get("from"):
        fm["from"] = needs_you.untrusted_line(record["from"], PEOPLE_MAX)
    for key in ("to", "cc", "participants", "attachments"):
        if key in record:
            fm.pop(key, None)
            if record.get(key):
                fm[key] = [str(x) for x in record[key]] if key == "attachments" else _people(record[key])
    if record.get("identifiers"):
        fm["identifiers"] = dict(record["identifiers"])
    fm["refreshed_at"] = pages.iso(now)
    report.plan.append(f"refresh {target.relative_to(root).as_posix()}")
    if not dry_run:
        _write_parts(root, target, record, fm, new_text)
    report.witness_written += 1
    return True


# ----- a credential seen: one plain warning to the person ---------------------------------
def _scrub_links(text: str) -> str:
    try:
        import scan
        return scan.scrub_link_tokens(text)
    except Exception:
        return text


def _credential_seen(record: dict) -> bool:
    """An obvious password or key: one the export cut out as `[redacted]` or one the scan still
    finds. A token in a link, a meeting passcode or a one-time code is no reason to warn."""
    text = str(record.get("text") or "")
    try:
        import scan
    except ImportError:
        return bool(record.get("redacted") or "[redacted]" in text)
    if record.get("redacted") and "[redacted]" not in text and "[link-token]" not in text:
        return True   # the export cut a value without leaving its marker
    return bool(scan.alarms(text))


def _security_notice(root: Path, space: spaceyaml.Space, record: dict) -> bool:
    """Queue a security notice for the person, once per record; it names where and when, never
    the value (vision: private security warnings stay with the person who can act)."""
    try:
        import owner_email
    except ImportError:
        return False
    source = {"mail": "mail", "message": "chat", "chat": "chat", "call": "calls"}.get(
        str(record.get("kind") or record.get("source") or ""), str(record.get("source") or "feeds"))
    try:
        when = f"{pages.parse_iso(record.get('at')):%d %B %Y}".lstrip("0")
    except Exception:
        when = "recently"
    notice = {"kind": "security", "sender": "sweep",
              "title": f"A password or key showed up in your {source}",
              "summary": f"Something in your {source} from {when} looks like a password or key. "
                         "Its value was not stored anywhere.",
              "steps": [f"Open that {source} item and check whether the key is real.",
                        "If it is, change it where it was issued and tell whoever shared it."],
              "reassure": "Nothing was copied: the item was kept without the value.",
              "where": source, "when": when, "key": f"security-{record.get('id')}"}
    try:
        return owner_email.queue(root, notice) is not None
    except (Exception, SystemExit):
        return False


# ----- snapshots: refresh folded into the sweep -------------------------------------
def _people_index(space: spaceyaml.Space) -> dict:
    """email or lower-case name -> 'Title (kind)' for every person and client subject."""
    out: dict = {}
    try:
        subjects = registry.Registry(space.root, space).everywhere()
    except Exception:
        return out
    for s in subjects:
        if s.kind not in ("people", "clients"):
            continue
        label = f"{s.title} ({'colleague' if s.kind == 'people' else 'client'})"
        out[s.title.strip().lower()] = label
        for alias in s.aliases or []:
            out[str(alias).strip().lower()] = label
        match = s.match if isinstance(getattr(s, "match", None), dict) else {}
        for key in ("email", "domain"):
            if match.get(key):
                out[str(match[key]).strip().lower()] = label
    return out


def _last_contact(root: Path, who: str) -> str | None:
    """The newest witness page this person sent, received or attended."""
    best = None
    needle = who.strip().lower()
    for path in (root / WITNESS_ROOT).rglob("*.md"):
        try:
            fm, _ = pages.read(path)
        except Exception:
            continue
        names = [str(fm.get("from") or "")] + [str(x) for x in (fm.get("to") or []) + (fm.get("participants") or [])]
        if any(needle and needle in n.lower() for n in names) and fm.get("kind") != "event":
            at = str(fm.get("at") or "")[:10]
            best = at if best is None or at > best else best
    return best


def who_is(space: spaceyaml.Space, root: Path, participant: str, index: dict) -> str:
    text = str(participant).strip()
    low = text.lower()
    address = low.split("<")[-1].rstrip(">").strip() if "<" in low else low
    for key in (address, low, address.split("@")[-1] if "@" in address else None):
        if key and key in index:
            return f"{text}: {index[key]}"
    last = _last_contact(root, address.split("@")[0] if "@" in address else address)
    return f"{text}: new here, last contact {last}" if last else f"{text}: first contact"


def write_today(root: Path, space: spaceyaml.Space, now: dt.datetime) -> Path | None:
    """`live/today.md`: today's and tomorrow's meetings from the calendar's witness pages, each
    participant with who they are (registry) or when we last heard from them. A title or a
    name is one line with its links, images and HTML inert (`needs_you.md_inline`): an invite's
    title can start no line of its own on the page or in the morning view."""
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + dt.timedelta(days=2)
    meetings = []
    for path in (root / WITNESS_ROOT).rglob("*.md") if (root / WITNESS_ROOT).is_dir() else []:
        try:
            fm, _ = pages.read(path)
            at = pages.parse_iso(fm.get("at"))
        except Exception:
            continue
        if fm.get("kind") == "event" and start <= at < end:
            meetings.append((at, fm))
    if not meetings and not (root / "live" / "today.md").exists():
        return None
    index = _people_index(space)
    own = {space.owner.strip().lower(), space.owner_email.strip().lower()} - {""}
    lines = []
    for at, fm in sorted(meetings, key=lambda m: m[0]):
        lines.append(f"- {at:%Y-%m-%d %H:%M} {needs_you.md_inline(fm.get('title'), TITLE_MAX) or 'a meeting'}")
        for person in fm.get("participants") or []:
            if str(person).strip().lower() in own:
                continue
            lines.append(f"  - {needs_you.md_inline(who_is(space, root, str(person), index), PEOPLE_MAX)}")
    body = ("# Today\n\nToday's and tomorrow's meetings from the calendar, with who is who. A "
            "snapshot the sweep rewrites each run.\n\n" + ("\n".join(lines) or "- No meetings.") + "\n")
    fm = {"id": _snapshot_id(root, "live/today.md"), "type": "snapshot", "title": "Today",
          "status": "draft", "generated": pages.generated("sweep", now)}
    target = root / "live" / "today.md"
    pages.write(target, fm, body, root=root)
    return target


def _snapshot_id(root: Path, rel: str) -> str:
    path = Path(root) / rel
    if path.exists() and not pages.is_link(path):   # never read through a link
        try:
            return str(pages.read(path)[0].get("id") or pages.new_id())
        except Exception:
            pass
    return pages.new_id()


VALUED = {"--cursor": 1, "--attachment-unread": 1, "--attachment-read": 1}   # flag -> how many values
PLAIN = ("--dry-run", "--attachments", "--refetch")   # the flags with no value


def _flag(argv: list, flag: str, count: int):
    """Take `flag` and its `count` values out of `argv`: None when it is not there, [] when it is
    written another way (`--cursor=mail`, `--cursors`) or a value is missing, so a slip prints
    the usage and never turns a read into a full sweep."""
    if any(a != flag and a.startswith(flag) for a in argv):
        return []
    if flag not in argv:
        return None
    at = argv.index(flag)
    values = argv[at + 1:at + 1 + count]
    if len(values) < count or any(not v or v.startswith("--") for v in values):
        return []
    del argv[at:at + 1 + count]
    return values


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    needs_you.utf8_stdio()
    dry_run = "--dry-run" in argv
    flags = {flag: _flag(argv, flag, count) for flag, count in VALUED.items()}
    if any(flags[f] and not HANDLE_RE.match(flags[f][0]) for f in ("--attachment-unread", "--attachment-read")):
        flags["--attachment-unread"] = []   # a name or an id is no handle: the usage, never a guess
    if [] in flags.values() or any(a.startswith("--") and a not in PLAIN for a in argv):
        print(__doc__)   # a flag it does not know (`--attachment`) is a slip too, never a full sweep
        return 2
    roots = [a for a in argv if not a.startswith("--")]
    root = Path(roots[0]) if roots else Path(".")
    if flags["--cursor"]:
        print(cursor(root, flags["--cursor"][0]))
        return 0
    for flag in ("--attachment-unread", "--attachment-read"):
        values = flags[flag]
        if not values:
            continue
        pair = by_handle(root, values[0])
        if pair is None:
            print(f"{values[0]}: no attachment with that handle")
            return 1
        rid, name = pair
        shown = f"{_escaped(rid)} {_escaped(name)}"
        if flag == "--attachment-unread":
            print(f"{shown}: " + ("listed until it is read" if note_unread(root, rid, name) else "already listed"))
        else:
            print(f"{shown}: " + ("read" if mark_read(root, rid, name) else "not listed as unread"))
        return 0
    if "--attachments" in argv:
        for rid, sid, name in attachments(root):
            print(f"{_escaped(rid)}\t{sid}\t{_escaped(name)}\t{attachment_handle(rid, name)}")
        return 0
    if "--refetch" in argv:
        for row in unsure.refetch(root):
            print("\t".join(row))
        return 0
    report = run(root, dry_run=dry_run)
    if dry_run:
        print("plan:" if report.plan else "nothing to sweep")
        for line in report.plan:
            print(f"  {line}")
    elif report.records_seen == 0:
        print("nothing to sweep")
    print(f"records {report.records_seen}, witness {report.witness_written}, attached {report.attached}, "
          f"queued {report.queued}, signals {report.signals}, held in doubt {report.held_in_doubt}, "
          f"held {report.held_other}, watermark {report.watermark}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
