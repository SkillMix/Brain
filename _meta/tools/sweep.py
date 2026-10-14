"""The sweep: feed records become witness pages, attachments, queue items and signals.

Deterministic from files: an adapter exports records to `_meta/state/feed/<source>.jsonl`
(one JSON object per line); this tool reads every record newer than
`_meta/state/sweep-watermark.yaml` that has no witness page yet and

1. writes the witness page `knowledge/sources/<yyyy>/<mm>/<record-id>.md` (a draft, never
   signed; `sensitive: true` when a never-leaves term appears);
2. attaches the record to the one subject whose `match:` identifier it carries. None or
   several: no attachment, the names in the text are kept as `mentions` (hints only);
3. for an attached record whose subject lives elsewhere (`home` is another space), applies
   the release rule (rules.md section 6): never-leaves term -> held; the destination's
   left-out term -> held; a call with participants outside the crew -> held in doubt;
   a high-stakes term -> the window, landing pending; audience already covering the
   destination, or a standing choice {destination, kind} (never for calls) -> at once;
   anything else -> after one window and one morning view. A content-free signal goes to the
   subject's home for every attached record that is not never-leaves. A record matching
   several subjects with a home elsewhere is held in doubt and shown as a count;
4. advances the watermark and appends one run line (cost = characters of records // 4).

    python sweep.py [root] [--dry-run]     --dry-run prints the plan and writes nothing
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
        for line in path.read_text(encoding="utf-8").splitlines():
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
    other reserved sign)."""
    import re as _re
    return _re.sub(r'[<>:"/\\|?*#%\s]+', "-", str(record_id)).strip("-.") or "record"


def witness_path(root: Path, record: dict) -> Path:
    at = pages.parse_iso(record["at"])
    return root / WITNESS_ROOT / f"{at:%Y}" / f"{at:%m}" / f"{file_name(record['id'])}.md"


def record_text(record: dict) -> str:
    return f"{record.get('subject', '')}\n{record.get('text', '')}"


# ----- the witness page ------------------------------------------------------
def witness_frontmatter(record: dict, hits: list, mentions: list, audience, sensitive: bool,
                        now: dt.datetime) -> dict:
    kind = record.get("kind", "document")
    title = str(record.get("subject") or "").strip() or f"{kind} from {record.get('from', 'unknown')}"
    fm = {"id": pages.new_id(), "type": "source", "title": title, "status": "draft",
          "generated": pages.generated("sweep", now), "record": str(record["id"]),
          "source": record.get("source"), "kind": kind,
          "at": pages.iso(pages.parse_iso(record["at"])), "from": record.get("from", "")}
    for key in ("to", "participants"):
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
    if sensitive:
        fm["sensitive"] = True
    return fm


# ----- the release rule --------------------------------------------------------
def crew_of(space: spaceyaml.Space, dest_space: spaceyaml.Space | None) -> set | None:
    """Who may hear a call bound for the destination; None means everyone."""
    crew = {space.owner}
    if isinstance(space.audience, list):
        crew.update(space.audience)
    if dest_space is not None:
        if dest_space.audience == "everyone":
            return None
        crew.add(dest_space.owner)
        if isinstance(dest_space.audience, list):
            crew.update(dest_space.audience)
    return {str(n).strip().lower() for n in crew if n}


def outsiders(space: spaceyaml.Space, dest_space, participants: list) -> list:
    crew = crew_of(space, dest_space)
    if crew is None:
        return []
    return [p for p in participants if str(p).strip().lower() not in crew]


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


PRICING = ("pricing", "price list")


def other_client(space: spaceyaml.Space, subject: registry.Subject, text: str) -> str | None:
    """Another client named in a record bound for this subject's home (left out by default)."""
    try:
        reg = registry.Registry(space.root, space)
        own = {subject.id} | {a.id for a in reg.ancestors_of(subject.id)}
        clients = [c for c in reg.everywhere() if c.kind == "clients" and c.id not in own]
    except Exception:
        return None
    if subject.kind != "projects" and not any(a.kind == "clients" for a in reg.ancestors_of(subject.id)):
        return None
    for client in clients:
        for name in [client.title, *(client.aliases or [])]:
            if name and release.first_term(text, [str(name)]):
                return str(client.title)
    return None


def project_lines(record: dict, left_out: list, space: spaceyaml.Space, subject: registry.Subject) -> str | None:
    """The record's lines without a left-out term or another client, or None when the title
    carries one or nothing is left."""
    if release.first_term(str(record.get("subject") or ""), left_out) or \
            other_client(space, subject, str(record.get("subject") or "")):
        return None
    kept = [line for line in str(record.get("text") or "").splitlines()
            if line.strip() and not release.first_term(line, left_out) and not other_client(space, subject, line)]
    return "\n".join(kept) if kept else None


def _address(value) -> str:
    text = str(value or "").strip().lower()
    return text.split("<")[-1].rstrip(">").strip() if "<" in text else text


def sent_by_owner(space: spaceyaml.Space, record: dict) -> bool:
    return bool(space.owner_email) and _address(record.get("from")) == space.owner_email.strip().lower()


def named_person(space: spaceyaml.Space, record: dict, text: str) -> str | None:
    """A mail from outside the company that names a registered person: a claim about a named
    person is high-stakes. Colleagues writing about each other are not flagged here."""
    if record.get("kind") not in ("mail", "message"):
        return None
    sender, own = _address(record.get("from")), _address(space.owner_email)
    if not sender or "@" not in sender or not own or "@" not in own:
        return None
    if sender.split("@")[1] == own.split("@")[1]:
        return None
    try:
        people = [s for s in registry.Registry(space.root, space).everywhere() if s.kind == "people"]
    except Exception:
        return None
    for person in people:
        for name in [person.title, *(person.aliases or [])]:
            if name and " " in str(name) and release.first_term(text, [str(name)]):
                return str(person.title)
    return None


def classify(space: spaceyaml.Space, record: dict, subject: registry.Subject, destination: str,
             dest_space, audience, now: dt.datetime) -> Verdict:
    text = record_text(record)
    term = release.first_term(text, space.never_leaves)
    if term:
        return Verdict(hold="never_leaves", why=f"never-leaves term `{term}` appears")
    left_out = release.left_out_of(dest_space)
    term = release.first_term(text, left_out)
    other = other_client(space, subject, text)
    if term or other:
        kept = project_lines(record, left_out, space, subject)
        what = f"left-out term `{term}`" if term else f"another client ({other})"
        if kept is None:
            return Verdict(hold="left_out", why=f"{what} for {destination} appears")
        verdict = classify(space, dict(record, text=kept), subject, destination, dest_space, audience, now)
        if not verdict.hold:   # a passage that mixes them keeps only its project facts
            verdict.extra = dict(verdict.extra or {}, trimmed_body=kept, trimmed=True)
            verdict.why += f"; {what} left out, the rest travels"
        return verdict
    kind = record.get("kind")
    if kind == "call":
        outside = outsiders(space, dest_space, record.get("participants") or [])
        if outside:
            return Verdict(hold="doubt",
                           why="call with participants outside the crew: " + ", ".join(outside))
    term = release.first_term(text, space.high_stakes)
    if term and term in PRICING and sent_by_owner(space, record):
        term = None   # a quote already sent is a record of what was said, not a pricing rule
    if term:
        return Verdict(cls="window", stage_after=now + space.window(), extra={"high_stakes": True},
                       why=f"high-stakes term `{term}`: waits the window, lands pending")
    named = named_person(space, record, text)
    if named:
        return Verdict(cls="window", stage_after=now + space.window(), extra={"high_stakes": True},
                       why=f"an outside party writes about {named}: a claim about a named person waits "
                           "the window and lands pending")
    paused_why = ""
    if covers(audience, subject, dest_space):
        path = f"audience:{record.get('source') or 'unknown'}"
        if not release.is_paused(space.root, path, now):
            return Verdict(cls="at_once", stage_after=now, why=f"audience {audience} already reads it",
                           extra={"path": path})
        paused_why = f"; {path} is paused"
    if kind != "call" and any(c.get("destination") == destination and c.get("kind") == kind
                              for c in space.standing_choices):
        path = f"standing_choice:{destination}:{kind}"
        if not release.is_paused(space.root, path, now):
            return Verdict(cls="at_once", stage_after=now, why=f"standing choice {kind} -> {destination}",
                           extra={"path": path})
        paused_why += f"; {path} is paused"
    return Verdict(cls="window", stage_after=now + space.window(), why="widens who can read it" + paused_why)


def item_frontmatter(space: spaceyaml.Space, record: dict, subject_id, destination, cls: str,
                     stage_after: dt.datetime, now: dt.datetime, extra: dict | None = None) -> dict:
    kind = record.get("kind", "document")
    title = str(record.get("subject") or "").strip() or f"{kind} from {record.get('from', 'unknown')}"
    fm = {"id": pages.new_id(), "type": "source", "title": title, "status": "draft",
          "generated": pages.generated("sweep", now), "class": cls, "destination": destination,
          "subject": subject_id, "stage_after": pages.iso(stage_after), "listed": False,
          "evidence": [str(record["id"])], "from_space": space.name, "kind": kind}
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

    declared = {str(s.get("name")) for s in space.sources if isinstance(s, dict) and s.get("name")}
    feed = [] if space.profile == "brain" else read_feed(root)   # the brain sweeps nothing of its own
    if declared:
        feed = [r for r in feed if str(r.get("_feed")) in declared]   # only the feeds this space declared
    marks = read_feed_marks(root)
    records = [r for r in feed if str(r.get("_feed")) not in marks
               or pages.parse_iso(r["at"]) > marks[str(r.get("_feed"))]]
    newest_by_feed = dict(marks)
    for r in records:
        at, name = pages.parse_iso(r["at"]), str(r.get("_feed"))
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
    for path in sorted((root / "knowledge" / "sources").rglob("*.md")):
        if not pages.is_page_path(path):
            continue
        try:
            fm, body = pages.read(path)
        except Exception:
            continue
        if fm.get("type") != "source" or fm.get("subject") or fm.get("candidates"):
            continue
        if str(fm.get("record") or path.stem) in settled:
            continue
        rebuilt = {"id": fm.get("record") or path.stem, "source": fm.get("source"), "kind": fm.get("kind"),
                   "at": fm.get("at") or (fm.get("generated") or {}).get("at"), "from": fm.get("from"),
                   "to": fm.get("to") or [], "subject": fm.get("title"), "text": body,
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
            newest = at if newest is None or at > newest else newest
        target = witness_path(root, record)
        if target.exists():
            report.plan.append(f"skip {record['id']}: witness page exists")
            continue
        chars += len(json.dumps({k: v for k, v in record.items() if not str(k).startswith('_')}))
        audience = audience_of(space, record)
        hits = reg.match(record)
        mentions = reg.mentions(record)
        sensitive = release.first_term(record_text(record), space.never_leaves) is not None
        fm = witness_frontmatter(record, hits, mentions, audience, sensitive, now)
        report.plan.append(f"witness {target.relative_to(root).as_posix()}")
        if not dry_run:
            pages.write(target, fm, str(record.get("text") or ""))
            if not record.get("_reattach") and _credential_seen(record) and _security_notice(root, space, record):
                report.security_notices += 1
        report.witness_written += 1
        body = str(record.get("text") or "")

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
        verdict = classify(space, record, subject, home, dest_space_of(home), audience, now)
        if verdict.hold:
            held = item_frontmatter(space, record, subject.id, home, verdict.hold, now, now)
            report.plan.append(f"hold {verdict.hold} {record['id']} -> {home}: {verdict.why}")
            if not dry_run:
                release.hold(root, held, body, verdict.hold, verdict.why)
            if verdict.hold == "doubt":
                report.held_in_doubt += 1
            else:
                report.held_other += 1
        else:
            extra = dict(verdict.extra or {})
            sent_body = extra.pop("trimmed_body", body)
            item = item_frontmatter(space, record, subject.id, home, verdict.cls,
                                    verdict.stage_after, now, extra)
            report.plan.append(f"queue {verdict.cls} {record['id']} -> {home}: {verdict.why}")
            if not dry_run:
                pages.write(root / release.QUEUE / f"{item['id']}.md", item, sent_body)
            report.queued += 1
        if verdict.hold != "never_leaves":
            signal = signal_for(subject.id, home, now)
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
