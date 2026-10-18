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
   the answer). A content-free
   signal goes to the subject's home for every attached record. A record matching several
   subjects with a home elsewhere is held in doubt and shown as a count;
4. advances the watermark and appends one run line (cost = characters of records // 4).

    python sweep.py [root] [--dry-run]     --dry-run prints the plan and writes nothing
    python sweep.py [root] --attachments   the attachments to read as document records: those
                                           on records of exactly one subject, not read yet
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import classify as gating  # noqa: E402
import pages  # noqa: E402
import registry  # noqa: E402
import release  # noqa: E402
import spaceyaml  # noqa: E402

FEED = Path("_meta") / "state" / "feed"
WATERMARK = Path("_meta") / "state" / "sweep-watermark.yaml"
WITNESS_ROOT = Path("knowledge") / "sources"
RESOURCES = Path("_meta") / "access" / "resources.yaml"


@dataclass
class SweepReport:
    witness_written: int = 0
    attached: int = 0
    queued: int = 0
    signals: int = 0
    held_in_doubt: int = 0
    held_other: int = 0
    security_notices: int = 0
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


def read_feed(root: Path) -> list:
    records = []
    feed = root / FEED
    if not feed.is_dir():
        return records
    for path in sorted(feed.glob("*.jsonl")):
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
            record.setdefault("source", path.stem)
            record["_feed"] = path.stem   # the declared source this file is (space.yaml sources)
            records.append(record)
    return records


def declared_feed(root: Path, space: spaceyaml.Space) -> list:
    """The feed records of the sources this space declared; the brain sweeps nothing of its own."""
    declared = {str(s.get("name")) for s in space.sources if isinstance(s, dict) and s.get("name")}
    feed = [] if space.profile == "brain" else read_feed(root)
    return [r for r in feed if str(r.get("_feed")) in declared] if declared else feed


def attachments(root) -> list:
    """(record id, subject id, attachment) for every attachment on a feed record of exactly one
    subject that has no document record `<record id>/<attachment>` yet: what the export reads
    next, each as a document record of its own. Writes nothing."""
    root = Path(root)
    space = spaceyaml.load(root)
    reg = registry.Registry(root, space)
    feed = declared_feed(root, space)
    have = {str(r["id"]) for r in feed}
    out = []
    for record in feed:
        if record.get("kind") == "document":   # its attachment points at the file it already holds
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
    return out


def audience_of(space: spaceyaml.Space, record: dict) -> str | list:
    """The record's audience, else its source's declared audience, else owner."""
    own = record.get("audience")
    if own:
        return own
    name = record.get("source")
    for source in space.sources:
        if source.get("name") == name and source.get("audience"):
            return source["audience"]
    path = space.root / RESOURCES
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for source in (data.get("resources") or []) if isinstance(data, dict) else []:
            if isinstance(source, dict) and source.get("name") == name and source.get("audience"):
                return source["audience"]
    return "owner"


def file_name(record_id) -> str:
    """A record id as a file name every system and every link accepts (no colon, slash, hash or
    other reserved sign), short enough for every path limit: a long id keeps its start and
    a hash of the whole."""
    import hashlib
    import re as _re
    name = _re.sub(r'[<>:"/\\|?*#%\s]+', "-", str(record_id)).strip("-.") or "record"
    if len(name) > 80:
        name = f"{name[:68].rstrip('-.')}-{hashlib.sha1(str(record_id).encode('utf-8')).hexdigest()[:10]}"
    return name


def witness_path(root: Path, record: dict) -> Path:
    at = pages.parse_iso(record["at"])
    return root / WITNESS_ROOT / f"{at:%Y}" / f"{at:%m}" / f"{file_name(record['id'])}.md"


def record_text(record: dict) -> str:
    return f"{record.get('subject', '')}\n{record.get('text', '')}"


# ----- the witness page ------------------------------------------------------
def witness_frontmatter(record: dict, hits: list, mentions: list, audience, now: dt.datetime) -> dict:
    kind = record.get("kind", "document")
    title = str(record.get("subject") or "").strip() or f"{kind} from {record.get('from', 'unknown')}"
    fm = {"id": pages.new_id(), "type": "source", "title": title, "status": "draft",
          "generated": pages.generated("sweep", now), "record": str(record["id"]),
          "source": record.get("source"), "kind": kind,
          "at": pages.iso(pages.parse_iso(record["at"])), "from": record.get("from", "")}
    for key in ("to", "cc", "participants"):
        if record.get(key):
            fm[key] = list(record[key])
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
    if record.get("research"):   # exported by research for this subject: the router decides it for it
        fm["research"] = str(record["research"])
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
    title = str(record.get("subject") or "").strip() or f"{kind} from {record.get('from', 'unknown')}"
    fm = {"id": pages.new_id(), "type": "source", "title": title, "status": "draft",
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
            fm[key] = record[key] if key == "from" else list(record[key])
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

    feed = declared_feed(root, space)
    marks = read_feed_marks(root)
    records = [r for r in feed if str(r.get("_feed")) not in marks or r.get("admitted") or r.get("backfill")
               or pages.parse_iso(r["at"]) > marks[str(r.get("_feed"))]]
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
                   "identifiers": fm.get("identifiers") or {}, "attachments": fm.get("attachments") or [],
                   "audience": fm.get("audience"), "participants": fm.get("participants") or []}
        if not rebuilt["at"] or not reg.match(rebuilt):
            continue
        if not dry_run:
            path.unlink()
        rebuilt["_reattach"] = True
        records.append(rebuilt)
        reattached += 1
    records.sort(key=lambda r: (pages.parse_iso(r["at"]), str(r["id"])))
    for record in records:
        at = pages.parse_iso(record["at"])
        report.records_seen += 1
        if not record.get("_reattach"):
            newest = min(at, now) if newest is None or at > newest else newest
        if is_own_notice(space, record):
            report.plan.append(f"skip {record['id']}: the workspace's own notice")
            continue
        if record.get("kind") == "document" and not str(record.get("text") or "").strip():
            report.plan.append(f"skip {record['id']}: a file with no text")   # its feed record settles it
            continue
        target = witness_path(root, record)
        if target.exists():
            if not _refresh(root, target, record, now, dry_run, report):
                report.plan.append(f"skip {record['id']}: witness page exists")
            continue
        chars += len(json.dumps({k: v for k, v in record.items() if not str(k).startswith('_')}))
        audience = audience_of(space, record)
        hits = reg.match(record)
        mentions = reg.mentions(record)
        fm = witness_frontmatter(record, hits, mentions, audience, now)
        report.plan.append(f"witness {target.relative_to(root).as_posix()}")
        if not dry_run:
            _write_parts(root, target, record, fm, str(record.get("text") or ""))
            if not record.get("_reattach") and _credential_seen(record) and _security_notice(root, space, record):
                report.security_notices += 1
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
                pages.write(root / release.QUEUE / f"{item['id']}.md", item, shallow(body))
            report.queued += 1
        if blocked:   # nothing about it moves while the gate holds it, not even a signal
            continue
        signal = signal_for(subject.id, home, now)   # content-free: that something moved
        report.plan.append(f"signal {record['id']} -> {home}")
        if not dry_run:
            path = root / release.SIGNALS / f"{signal['id']}.yaml"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump(signal, sort_keys=False, allow_unicode=True),
                            encoding="utf-8", newline="\n")
        report.signals += 1

    report.cost_tokens = chars // 4
    if newest is not None:
        report.watermark = pages.iso(newest)
    if dry_run:
        return report
    if (newest is not None and newest != watermark) or newest_by_feed != marks:
        path = root / WATERMARK
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"at": pages.iso(newest)} if newest is not None else {}
        data["feeds"] = {name: pages.iso(at) for name, at in sorted(newest_by_feed.items())}
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n")
    try:
        write_today(root, space, now)
    except Exception:  # a snapshot never stops the sweep
        pass
    release.append_run_line(root, "sweep", now, space, report.cost_tokens, {
        "records": report.records_seen, "witness": report.witness_written,
        "attached": report.attached, "queued": report.queued, "signals": report.signals,
        "held_in_doubt": report.held_in_doubt, "held_other": report.held_other,
        "reattached": reattached, "security_notices": report.security_notices})
    return report


# ----- without the router, what leaves by identifier stays shallow -----------------------------
CAP_WITHOUT_ROUTING = 8_000
UTTERANCE = re.compile(r"^\s*\[\d{1,2}:\d{2}(?::\d{2})?\]")


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
        pages.write(target, fm, text)
        return
    for k, (path, chunk) in enumerate(zip(_part_paths(root, record, len(chunks)), chunks), start=1):
        part_fm = dict(fm, part=k, parts=len(chunks))
        if k > 1:
            part_fm = dict(part_fm, id=pages.new_id(), record=f"{record['id']}#{k}")
            part_fm.pop("subject", None)
        pages.write(path, part_fm, chunk)


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
        fm["from"] = record["from"]
    for key in ("to", "cc", "participants", "attachments"):
        if key in record:
            fm.pop(key, None)
            if record.get(key):
                fm[key] = [str(x) for x in record[key]] if key == "attachments" else list(record[key])
    if record.get("identifiers"):
        fm["identifiers"] = dict(record["identifiers"])
    fm["refreshed_at"] = pages.iso(now)
    report.plan.append(f"refresh {target.relative_to(root).as_posix()}")
    if not dry_run:
        _write_parts(root, target, record, fm, new_text)
    report.witness_written += 1
    return True


# ----- a credential seen: one plain warning to the person ---------------------------------
def _credential_seen(record: dict) -> bool:
    """The export redacted a key or password, or the scan still finds one in the text."""
    text = str(record.get("text") or "")
    if record.get("redacted") or "[redacted]" in text:
        return True
    try:
        import scan
        return bool(scan.scan_text(text, str(record.get("id"))))
    except Exception:
        return False


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
              "key": f"security-{record.get('id')}"}
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
    participant with who they are (registry) or when we last heard from them."""
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
        lines.append(f"- {at:%Y-%m-%d %H:%M} {fm.get('title') or 'a meeting'}")
        for person in fm.get("participants") or []:
            if str(person).strip().lower() in own:
                continue
            lines.append(f"  - {who_is(space, root, str(person), index)}")
    body = ("# Today\n\nToday's and tomorrow's meetings from the calendar, with who is who. A "
            "snapshot the sweep rewrites each run.\n\n" + ("\n".join(lines) or "- No meetings.") + "\n")
    fm = {"id": _snapshot_id(root, "live/today.md"), "type": "snapshot", "title": "Today",
          "status": "draft", "generated": pages.generated("sweep", now)}
    target = root / "live" / "today.md"
    pages.write(target, fm, body)
    return target


def _snapshot_id(root: Path, rel: str) -> str:
    path = Path(root) / rel
    if path.exists():
        try:
            return str(pages.read(path)[0].get("id") or pages.new_id())
        except Exception:
            pass
    return pages.new_id()


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in argv
    roots = [a for a in argv if not a.startswith("--")]
    if "--attachments" in argv:
        for rid, sid, name in attachments(Path(roots[0]) if roots else Path(".")):
            print(f"{rid}	{sid}	{name}")
        return 0
    report = run(Path(roots[0]) if roots else Path("."), dry_run=dry_run)
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
