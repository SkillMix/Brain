"""Needs you: the one list of everything that needs a person in this space (shape 1).

Every routine writes its own items into `_inbound/needs-you/<routine>.yaml` (one writer
per file). This module gathers them together with the items it derives from the space
itself: candidates and checks under `_inbound/`, releases leaving within the window,
high-stakes items, questions, answers that arrived, stale outbox drafts, and the items
held in doubt as one count. What the agent fixes on its own (a hold it can clear, a release
that could not land, a repository the brain could not verify yet) is one `health` line that
says what the agent will do: the person is told, never asked. Each item says what it is, what the agent recommends and
what happens if nothing is done, and by when. Ordered by deadline, then by how much
depends on the answer, then by kind. `sign` is the one verb over any item.

Much of an item's text is someone else's: a mail subject, a calendar title, a page another
space wrote. So `render` puts every item on one line of its own (`untrusted_line`: no line
break, control character or bidirectional mark) with its links, images and HTML made inert
(`md_inline`), and a forged line can never pose as a numbered item or a heading. A mounted
space's items come with the space's name in brackets, one line each, as a `request` or one of
the few kinds a mount may raise (MOUNT_KINDS), never `security`, and depending on at most
MOUNT_DEPENDS; in this space a `security` item is read only from the files of the tools that
raise one (SECURITY_WRITERS), and one anywhere else is a `request` that cannot sort first. A decision candidate nobody checked is never recommended yes.

    python needs_you.py [root] [--json] [--limit N] [--once-a-day]

`--once-a-day` shows the list in the first session of the person's day only: it keeps the
local date it last printed in `_meta/state/private/needs-you-shown` and prints nothing
again that day. Before it lists, the command pins the verdict cut-over in a space with no
verdict store yet (`status.pin_cutover`), so the answers given before the store keep counting.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import authority  # noqa: E402
import ghrest  # noqa: E402
import pages  # noqa: E402
import spaceyaml  # noqa: E402

KIND_ORDER = ["security", "leaving", "high_stakes", "answer", "request", "question", "check", "candidate",
              "reconnect", "protected_pr", "fork_pr", "outbox", "classification", "doubt", "sample", "health"]
VERBS_LINE = "Answer with the number and yes, edit, no, later or always; then submit."
OUTBOX_DAYS = 14
# A verdict line in log.md, as sign.apply writes it: "- <ISO> <verb> <id> by <name>". Narrative: the
# answers are read from the verdict store (`status.verdicts`).
VERDICT_LINE = re.compile(r"^- (\S+) (yes|edit|no|always) (\S+) by ")

CANDIDATES = Path("_inbound") / "candidates"
CHECKS = Path("_inbound") / "checks"
QUESTIONS = Path("_inbound") / "questions"
ANSWERS = Path("_inbound") / "answers"
NEEDS_YOU = Path("_inbound") / "needs-you"
QUEUE = Path("_outbound") / "queue"
HELD = Path("_outbound") / "held"
OUTBOX = Path("outbox")
ROUTING_LOG = Path("_meta") / "state" / "routing.jsonl"
SHOWN = spaceyaml.PRIVATE_STATE / "needs-you-shown"   # the day the list last printed, YYYY-MM-DD
# The files whose writers raise a `security` item: the morning view (a briefing that reached
# someone else), the sync and the cloud sync (protected files a run changed). A `security` row
# in any other file is read as a `request`, so a file an agent or a pull request adds cannot
# put a forged alarm at the top of the list. Nor can it take the depends that puts an alarm
# first, or the kind the morning mail counts other security warnings under (SECURITY_COUNTS).
SECURITY_WRITERS = ("morning-view.yaml", "sync.yaml", "sync-cloud.yaml")
SECURITY_COUNTS = ("security", "security_warning")
# What a mounted space's items may be on this person's list. Anything else (`security`,
# `leaving`, `high_stakes`, `answer`, a kind nobody knows) arrives as a `request`, and none
# depends on more than MOUNT_DEPENDS, the most the tools' own items ask: 99 stays this space's.
MOUNT_KINDS = ("request", "question", "check", "candidate", "sample", "reconnect")
MOUNT_DEPENDS = 2
MOUNT_TEXT_MAX = 200
# What the agent works through on its own, one file per tool that writes it (`set_health`): a
# workspace repository the brain could not verify yet, say. With what is held in
# `_outbound/held/` for a reason the agent can fix and the releases landing could not file, it
# makes one `health` line: the person is told, never asked.
HEALTH = Path("_meta") / "state" / "health"
HEALTH_SHOWN = 3
# Whether a sentence the router read binds the company: its own label and a second agent's,
# each given alone (route.py stamps them, distill carries them to the decision draft).
LABELS = ("binds", "binds_second")


@dataclass
class Item:
    id: str
    kind: str
    text: str
    recommendation: str = ""
    default: str = ""
    deadline: dt.datetime | None = None
    depends: int = 0
    payload: str = ""
    fingerprint: str = ""
    check: str = ""        # the read-back that proves it is done, for an owner step
    for_: str = ""         # whom it is for, when not the space's owner

    def to_dict(self) -> dict:
        data = asdict(self)
        data["deadline"] = pages.iso(self.deadline) if self.deadline else None
        return data


# ----- reading -------------------------------------------------------------------
def pages_in(root: Path, folder: Path):
    """(relpath, frontmatter, body) for every readable page directly under a folder."""
    base = Path(root) / folder
    if not base.is_dir():
        return
    for path in sorted(base.glob("*.md")):
        try:
            fm, body = pages.read(path)
        except Exception:
            continue
        if fm:
            yield path.relative_to(root).as_posix(), fm, body


def _when(value) -> dt.datetime | None:
    if not value:
        return None
    try:
        return pages.parse_iso(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value, fallback: str) -> str:
    return str(value) if value not in (None, "") else fallback


def routine_items(root: Path) -> list:
    """Items routines wrote into `_inbound/needs-you/*.yaml`, as written, except that a
    `security` item counts only from SECURITY_WRITERS' files: elsewhere it is a `request` that
    depends on at most MOUNT_DEPENDS, and so is a `security_warning` (a mail count, never an item
    kind) from any file."""
    out = []
    base = Path(root) / NEEDS_YOU
    if not base.is_dir():
        return out
    for path in sorted(base.glob("*.yaml")):
        rel = path.relative_to(root).as_posix()
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            # never drop a routine's items silently: the broken file itself needs a person
            mark = getattr(exc, "problem_mark", None)
            where = f" (line {mark.line + 1})" if mark else ""
            out.append(Item(id=f"unreadable-{path.stem}", kind="request",
                            text=f"{rel} cannot be read{where}: its items are missing from this list",
                            recommendation="fix the file; the items come back on the next run",
                            payload=rel))
            continue
        rows = data.get("items") if isinstance(data, dict) else data
        for raw in rows or []:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            kind = _text(raw.get("kind"), "request")
            depends = _int(raw.get("depends"))
            if kind in SECURITY_COUNTS and (kind != "security" or path.name not in SECURITY_WRITERS):
                kind, depends = "request", min(depends, MOUNT_DEPENDS)
            out.append(Item(id=str(raw["id"]), kind=kind,
                            text=_text(raw.get("text"), str(raw["id"])),
                            recommendation=_text(raw.get("recommendation"), ""),
                            default=_text(raw.get("default"), ""), deadline=_when(raw.get("deadline")),
                            depends=depends,
                            payload=_text(raw.get("payload"), rel),
                            fingerprint=_text(raw.get("fingerprint"), ""), check=_text(raw.get("check"), ""),
                            for_=_text(raw.get("for"), "")))
    return out


def answered_ids(root: Path) -> set:
    """Ids with a recorded verdict (anything but `later`): no longer asked. Read from the verdict
    store sign.py writes (`status.verdicts`), never from a line added to log.md since the store
    began, where a line someone else wrote could hide an item from the person; the log's lines
    from before it (the cut-over, `status.pin_cutover`) still count."""
    import status   # here and not at the top: status imports this module's helpers when it renders
    return {k for k, v in status.verdicts(root).items() if v.get("verb") and v.get("verb") != "later"}


RAISED_AGAIN = re.compile(r"-\d{8}T\d{4}(-\d+)?")   # what `recurring_id` adds to an id that has an answer


def recurring_id(root, base: str, now: dt.datetime, answered: set | None = None) -> str:
    """The id for an item its writer raises now: `base` until `base` has an answer, then
    `base-<yyyymmddThhmm>`, with a count after it when that one has an answer too (twice in a minute, or a
    clock that stands still). This list hides every id with an answer (`answered_ids`, passed as `answered`
    by a writer that read them already), so an item that can come back (a sign-in lost again, an app
    disconnected again) is named per occurrence, or its second occurrence is never shown. A writer asks it
    when it raises the item anew, not while the item stays in its file."""
    answered = answered_ids(Path(root)) if answered is None else answered
    base = str(base)
    if base not in answered:
        return base
    stamped = f"{base}-{now:%Y%m%dT%H%M}"
    count = next(n for n in range(1, len(answered) + 3) if (stamped if n == 1 else f"{stamped}-{n}") not in answered)
    return stamped if count == 1 else f"{stamped}-{count}"


def same_item(item_id, base: str) -> bool:
    """Whether `item_id` is `base` or a later occurrence of it (`recurring_id`)."""
    item_id = str(item_id or "")
    return item_id == base or (item_id.startswith(base) and bool(RAISED_AGAIN.fullmatch(item_id[len(base):])))


# ----- the kinds -----------------------------------------------------------------
def _open_questions(root: Path, cid: str, subject) -> int:
    """How many questions to the person are open for a candidate: its subject's, because distill
    gives a subject's next candidate a new id and the questions carried over keep the old page;
    a candidate with no subject counts the questions asked for its own id."""
    import sign_questions   # here and not at the top, as registry and classify are
    found = (sign_questions.for_subject(root, str(subject)) if subject
             else sign_questions.for_page(root, cid))
    return len(found)


UNCHECKED_DECISION = "read it first: nobody checked who decided this"


def _candidate_default(fm: dict) -> str:
    """The recommendation a candidate gets when it names none: yes, except for a decision taken
    from a record that nobody checked and that does not come from inside the company
    (`origin.inside`, distill.write_decision): its first line is the sender's own words."""
    if str(fm.get("type")) != "decision" or fm.get("checked"):
        return "yes"
    origin = fm.get("origin") if isinstance(fm.get("origin"), dict) else {}
    return "yes" if origin.get("inside") is True else UNCHECKED_DECISION


def unwritten(rel: str, fm: dict, body: str) -> bool:
    """A draft only an agent can finish, so never shown to a person: a subject page that is still
    the stub alone, or a decision nobody checked whose body is still the record under its
    `Decision:` line (distill.write_decision). It is counted on the agent's health line."""
    import distill   # here and not at the top: distill imports this module
    if distill.is_subject_candidate(Path(rel), fm):
        return distill.stub_only(body)
    return (str(fm.get("type")) == "decision" and not fm.get("checked") and not fm.get("released_from")
            and distill.decision_title(body) is not None)


def unbinding(fm: dict) -> bool:
    """Both labellers said the sentence does not bind the company: the draft stays a draft, off
    the list. One label missing, or saying it binds, keeps it on."""
    return all(fm.get(key) is False for key in LABELS)


def _candidates(root: Path, space: spaceyaml.Space | None = None) -> list:
    import distill   # here and not at the top, as registry and classify are
    out = []
    waiting = {rel for rel, _, _ in waiting_candidates(root, space)}
    found = list(pages_in(root, CANDIDATES))
    by_id = {_text(fm.get("id"), Path(rel).stem): fm for rel, fm, _ in found}
    for rel, fm, body in found:
        if rel in waiting:   # counted on the classification line, never offered for a signature
            continue
        if landing_health(fm):   # a release that cannot land as it is: the health line, never a signature
            continue
        if unwritten(rel, fm, body) or unbinding(fm):   # the agent's work, or nothing that binds
            continue
        if fm.get("released_from") and pages.duplicate_of(fm):   # one record that arrived twice: its main is asked
            continue
        title = _text(fm.get("title"), Path(rel).stem)
        cid = _text(fm.get("id"), Path(rel).stem)
        subject = fm.get("subject")
        text = f"{title} for {subject}: sign?" if subject else f"{title}: sign?"
        if fm.get("review_batch"):
            open_lines = [l for l in fm.get("lines") or [] if not l.get("answered")]
            text = (f"{title}: {len(open_lines)} pages to confirm, retire or correct; "
                    f"answer by line ({Path(rel).stem}#<n>)")
        elif fm.get("landing"):  # a release that arrived and waits (landing.py says why)
            who = f", for {fm['for']}" if fm.get("for") else ""
            text = f"{title} from {fm.get('released_from', 'another space')} ({fm['landing']}{who}): sign?"
            others = fm.get("disagrees_with")
            for other in others if isinstance(others, list) else []:   # landing.py marks it in frontmatter only
                o = by_id.get(str(other))
                if o is None:   # the rival was discarded or filed since: nothing disagrees any more
                    continue
                text += (f" {untrusted_line(other, 40)}, released from {untrusted_line(o.get('released_from'), 60)}"
                         f" by {untrusted_line(o.get('signed_by'), 60)}, says otherwise.")
        else:
            asked = _open_questions(root, cid, subject)
            if asked:
                text += f" ({asked} question{'s' if asked != 1 else ''})"
        # what sign.read_back prints: a page lands without its tool-kept New evidence list,
        # a record (`type: source`) is filed whole
        signed = body if str(fm.get("type")) == "source" else distill.without_evidence(body)
        out.append(Item(id=cid, kind="candidate",
                        text=text,
                        recommendation=_text(fm.get("recommendation"), _candidate_default(fm)),
                        default="stays a draft",
                        deadline=_when(fm.get("deadline")), depends=_int(fm.get("depends")),
                        payload=rel, fingerprint=pages.fingerprint(signed)))
    return out


def _checks(root: Path) -> list:
    out = []
    for rel, fm, body in pages_in(root, CHECKS):
        out.append(Item(id=_text(fm.get("id"), Path(rel).stem), kind="check",
                        text=_text(fm.get("title"), Path(rel).stem),
                        recommendation=_text(fm.get("recommendation"), ""), default="page stays as it is",
                        deadline=None, depends=_int(fm.get("depends")), payload=rel,
                        fingerprint=pages.fingerprint(body)))
    return out


def _subject_title(root: Path, space: spaceyaml.Space, sid: str) -> str:
    try:
        import registry
        subject = registry.Registry(root, space).get(sid)
    except Exception:
        subject = None
    return (subject.title if subject is not None else "") or sid


def _first_releases(root: Path, space: spaceyaml.Space, groups: dict) -> list:
    """One line per newly classified company subject for everything of its first release."""
    out = []
    for sid, group in sorted(groups.items()):
        dests = sorted({_text(fm.get("destination"), "its destination") for fm in group})
        whens = [w for w in (_when(fm.get("stage_after")) for fm in group) if w is not None]
        earliest = min(whens) if whens else None
        n = len(group)
        out.append(Item(id=f"first-{sid}", kind="leaving",
                        text=f"{_subject_title(root, space, sid)}: the first {n} item{'s' if n != 1 else ''} "
                             f"since it was classified company leave for {', '.join(dests)} at "
                             f"{pages.iso(earliest) if earliest else 'the end of the window'}",
                        recommendation="yes", deadline=earliest,
                        default="waits for your yes" if any(not fm.get("approved_by") for fm in group)
                        and not authority.allows(space, "release", "after_window") else "leaves",
                        payload=QUEUE.as_posix() + "/", fingerprint=""))
    return out


def pause_note(fm: dict) -> str:
    """What a no on a queue item also does (sign.py, release.pause): the path that let it through
    pauses for four weeks. "" for an item a person approved, whose no pauses nothing."""
    path = str(fm.get("path") or "")
    return f"a no pauses {untrusted_line(path, 80)} for four weeks" if path and not path.startswith("approved:") else ""


def _queue(root: Path, space: spaceyaml.Space, now: dt.datetime) -> list:
    out = []
    firsts: dict = {}
    horizon = now + space.window()
    for rel, fm, body in pages_in(root, QUEUE):
        if fm.get("first_release") and not fm.get("high_stakes"):
            firsts.setdefault(str(fm["first_release"]), []).append(fm)
            continue
        title = _text(fm.get("title"), Path(rel).stem)
        destination = _text(fm.get("destination"), "its destination")
        stage_after = _when(fm.get("stage_after"))
        common = dict(id=_text(fm.get("id"), Path(rel).stem), depends=_int(fm.get("depends")),
                      payload=rel, fingerprint=pages.fingerprint(body))
        pause = pause_note(fm)
        pause = f"; {pause}" if pause else ""
        if fm.get("high_stakes"):
            if fm.get("approved_by"):   # its person said yes: never asked twice (approved_leaving)
                continue
            out.append(Item(kind="high_stakes", text=f"{title} for {destination}: high stakes" + pause,
                            recommendation=_text(fm.get("recommendation"), "yes"),
                            default="waits for the domain owner", deadline=None, **common))
            continue
        reason = _text(fm.get("why"), "")   # the router's reason it waits, when it gave one
        reason = f" ({reason})" if reason else ""
        # at `propose` nothing leaves without a person's yes (release.py), whatever its class
        waits = (not fm.get("approved_by") and fm.get("class") not in ("retraction", "signal")
                 and not authority.allows(space, "release", "after_window"))
        if fm.get("class") == "window" and stage_after is not None and stage_after <= horizon:
            out.append(Item(kind="leaving",
                            text=f"{title} leaves for {destination} at {pages.iso(stage_after)}" + reason + pause,
                            recommendation=_text(fm.get("recommendation"), "yes"),
                            default="waits for your yes" if waits else "leaves",
                            deadline=stage_after, **common))
        elif waits:
            out.append(Item(kind="leaving",
                            text=f"{title} waits for your yes to leave for {destination}" + reason + pause,
                            recommendation=_text(fm.get("recommendation"), "yes"),
                            default="waits for your yes", deadline=None, **common))
    return out + _first_releases(root, space, firsts)


def approved_leaving(root, space: spaceyaml.Space, now: dt.datetime) -> list:
    """The high-stakes window items a person already approved that leave within the window.
    Not on the list (the yes is recorded in log.md and as `approved_by`), but the morning view
    still lists them, so they leave at `stage_after` like every window item."""
    out = []
    horizon = now + space.window()
    for rel, fm, body in pages_in(Path(root), QUEUE):
        if not (fm.get("high_stakes") and fm.get("approved_by")) or fm.get("class") != "window":
            continue
        stage_after = _when(fm.get("stage_after"))
        if stage_after is None or stage_after > horizon:
            continue
        out.append(Item(id=_text(fm.get("id"), Path(rel).stem), kind="leaving",
                        text=f"{_text(fm.get('title'), Path(rel).stem)} leaves for "
                             f"{_text(fm.get('destination'), 'its destination')} at {pages.iso(stage_after)} "
                             f"(approved by {fm['approved_by']})",
                        recommendation="yes", default="leaves", deadline=stage_after,
                        payload=rel, fingerprint=pages.fingerprint(body)))
    return out


def _outbox(root: Path, space: spaceyaml.Space, now: dt.datetime) -> list:
    out = []
    base = Path(root) / OUTBOX
    if not base.is_dir():
        return out
    stale = space.scale(OUTBOX_DAYS * 24)
    for path in sorted(base.glob("*.md")):
        try:
            fm, body = pages.read(path)
        except Exception:
            fm, body = {}, ""
        gen = fm.get("generated") if isinstance(fm.get("generated"), dict) else {}
        since = _when(gen.get("at") or fm.get("at") or fm.get("created"))
        if since is None:
            since = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
        if now - since <= stale:
            continue
        rel = path.relative_to(root).as_posix()
        out.append(Item(id=_text(fm.get("id"), path.stem), kind="outbox",
                        text=f"{_text(fm.get('title'), path.stem)} has waited in the outbox since "
                             f"{pages.iso(since)[:10]}",
                        recommendation="send it yourself, or no to drop it", default="archived silently",
                        deadline=None, payload=rel, fingerprint=pages.fingerprint(body)))
    return out


def _questions(root: Path) -> list:
    out = []
    for rel, fm, body in pages_in(root, QUESTIONS):
        out.append(Item(id=_text(fm.get("id"), Path(rel).stem), kind="question",
                        text=_text(fm.get("question"), _text(fm.get("title"), Path(rel).stem)),
                        recommendation=_text(fm.get("recommendation"), ""),
                        default=_text(fm.get("default"), "the recommendation stands"),
                        deadline=_when(fm.get("deadline")), depends=_int(fm.get("depends")),
                        payload=rel, fingerprint=pages.fingerprint(body)))
    return out


def _answers(root: Path) -> list:
    out = []
    for rel, fm, body in pages_in(root, ANSWERS):
        out.append(Item(id=_text(fm.get("id"), Path(rel).stem), kind="answer",
                        text=_text(fm.get("title"), Path(rel).stem),
                        recommendation=_text(fm.get("recommendation"), "yes"),
                        default=_text(fm.get("default"), "stays where it is"),
                        deadline=_when(fm.get("deadline")), depends=_int(fm.get("depends")),
                        payload=rel, fingerprint=pages.fingerprint(body)))
    return out


def _tracker(root: Path) -> list:
    """At the badge's `propose`, each queued tracker request waits for a person's yes."""
    try:
        import tracker
        waiting = tracker.waiting_for_a_person(root)
    except Exception:
        return []
    return [Item(id=str(fm.get("id")), kind="tracker", text=f"Create the tracker issue \"{fm.get('title')}\"?",
                 recommendation="yes", default="it waits, nothing is created",
                 payload=Path(path).relative_to(Path(root)).as_posix(), fingerprint=pages.fingerprint(body))
            for path, fm, body in waiting]


def doubt_pages(root) -> list:
    """The pages held at home in doubt that nobody has answered yet."""
    return [(rel, fm, body) for rel, fm, body in pages_in(Path(root), HELD)
            if fm.get("reason") == "doubt" and not fm.get("kept")]


def unclassified_pages(root) -> list:
    """The pages held at home until their client, project or deal is classified. Counted on the
    classification line, never offered: only the subject's owner classifies, and the gate lets them go."""
    return [(rel, fm, body) for rel, fm, body in pages_in(Path(root), HELD)
            if fm.get("reason") == "unclassified" and not fm.get("kept")]


def doubt_count(root) -> int:
    """How many pages are held at home in doubt (one Needs-you line counts them)."""
    return len(doubt_pages(root))


def _registry(root: Path, space: spaceyaml.Space | None):
    try:
        import registry
        return registry.Registry(root, space)
    except Exception:   # an unreadable registry: only what is held is counted
        return None


def _waits(reg, sid) -> bool:
    """A client, project or deal (or a part of one) that nobody has classified yet."""
    if reg is None or not sid:
        return False
    import classify
    return classify.gated(reg, str(sid)) and classify.effective(reg, str(sid)) is None


def waiting_candidates(root, space: spaceyaml.Space | None = None, reg=None) -> list:
    """This space's own candidates about a client, project or deal nobody has classified yet:
    counted on the classification line and never offered for a signature until it is classified."""
    root = Path(root)
    found = list(pages_in(root, CANDIDATES))
    if not any(fm.get("subject") and not fm.get("released_from") for _, fm, _ in found):
        return []
    reg = reg if reg is not None else _registry(root, space)
    return [(rel, fm, body) for rel, fm, body in found
            if not fm.get("released_from") and _waits(reg, fm.get("subject"))]


def refused_unclassified(root, reg) -> list:
    """The routing log's rows of records whose last word was a route refused as unclassified,
    while their subject still waits: the records stay here, unrouted, until it is classified."""
    path = Path(root) / ROUTING_LOG
    last: dict = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("record"):
                last[str(row["record"])] = row
    return [row for row in last.values() if row.get("decision") == "refused"
            and row.get("reason") == "unclassified" and _waits(reg, row.get("subject"))]


def classification_waits(root, space: spaceyaml.Space | None = None) -> dict:
    """subject id -> how many things in this space wait for its classification: projections held
    at home, records whose route was refused as unclassified, and candidates about it."""
    root = Path(root)
    reg = _registry(root, space)
    out: dict = {}
    sids = [str(fm.get("subject") or "") for _, fm, _ in unclassified_pages(root)]
    sids += [str(row.get("subject")) for row in refused_unclassified(root, reg)]
    sids += [str(fm.get("subject")) for _, fm, _ in waiting_candidates(root, space, reg)]
    for sid in sids:
        out[sid] = out.get(sid, 0) + 1
    return out


def doubt_items(root) -> list:
    """The doubt line opened: one item per held page, its reason and a recommendation."""
    out = []
    for rel, fm, body in doubt_pages(Path(root)):
        dest = fm.get("destination") or ", ".join(fm.get("candidates") or []) or "nowhere yet"
        out.append(Item(id=str(fm.get("id") or Path(rel).stem), kind="doubt",
                        text=f"{fm.get('title') or Path(rel).stem} (to {dest}): {fm.get('why') or 'privacy doubt'}",
                        recommendation=_text(fm.get("recommendation"), "no: keep it home"),
                        default=_text(fm.get("default"), "stays home"), payload=rel,
                        fingerprint=pages.fingerprint(body)))
    return out


FOLD_DRAFTS_OVER = 10   # more plain drafts than this fold into one line


def _plain_draft(item: Item, fm: dict) -> bool:
    """A draft nothing presses: no deadline, no high-stakes flag, not a release waiting for a
    person, not a review batch. Many of them fold into one count line (`_fold_drafts`)."""
    return (item.kind == "candidate" and item.deadline is None and not fm.get("high_stakes")
            and not fm.get("landing") and not fm.get("review_batch"))


def draft_items(root, space: spaceyaml.Space | None = None) -> list:
    """The drafts line opened: one item per plain draft, each signed by its own id as ever."""
    fms = {rel: fm for rel, fm, _ in pages_in(Path(root), CANDIDATES)}
    return [i for i in _candidates(Path(root), space) if _plain_draft(i, fms.get(i.payload, {}))]


def _fold_drafts(root: Path, items: list) -> list:
    """More plain drafts than a list shows become one line, so what needs the person is seen."""
    fms = {rel: fm for rel, fm, _ in pages_in(root, CANDIDATES)}
    plain = [i for i in items if _plain_draft(i, fms.get(i.payload, {}))]
    if len(plain) <= FOLD_DRAFTS_OVER:
        return items
    return [i for i in items if i not in plain] + [Item(
        id="drafts", kind="candidate", text=f"drafts to sign when you have time: {len(plain)} "
                                            "(open them: needs_you.py --drafts)",
        recommendation="open them when you have time", default="they stay drafts", deadline=None,
        payload=CANDIDATES.as_posix() + "/", fingerprint="")]


def _doubt(root: Path) -> list:
    count = len(doubt_pages(root))
    if not count:
        return []
    return [Item(id="doubt", kind="doubt", text=f"held in doubt: {count}",
                 recommendation="leave them home", default="stay home", deadline=None,
                 payload=HELD.as_posix() + "/", fingerprint="")]


def _classification(root: Path, space: spaceyaml.Space) -> list:
    """One line for everything that waits for a classification, naming the clients, projects and
    deals and who classifies each: this person's agent, or their owner's workspace."""
    waits = classification_waits(root, space)
    if not waits:
        return []
    reg = _registry(root, space)
    import classify

    def title(sid):
        subject = reg.get(sid) if reg is not None and sid else None
        return (subject.title if subject is not None else "") or sid

    sids = sorted(s for s in waits if s)
    mine = [s for s in sids if reg is not None and classify.decider(space, reg, s) in ("self", "stand_in")]
    others = [s for s in sids if s not in mine]
    steps = []
    if mine:
        steps.append(f"ask your agent to classify {', '.join(title(s) for s in mine)} (the classify skill)")
    for sid in others:
        owner = classify.subject_owner(reg, sid) if reg is not None else None
        steps.append(f"{title(sid)}: {owner}'s workspace classifies it" if owner
                     else f"{title(sid)}: say who handles it")
    count = sum(waits.values())
    names = ", ".join(title(s) for s in sids)
    return [Item(id="classification", kind="classification",
                 text=f"{count} wait for a classification" + (f" ({names})" if names else ""),
                 recommendation="; ".join(steps) or "classify them", default="they stay in this space",
                 deadline=None, payload=HELD.as_posix() + "/", fingerprint="")]


# ----- what the agent fixes on its own: one line, never a question -------------------------------
# The landing holds that only a bug or an attack produces (landing.AGENT_HOLDS): the agent reports
# them, the sender sends again, and nothing of them is filed meanwhile.
LANDING_AGENT_HOLDS = ("pending: malformed release", "pending: bad target", "pending: its id is already",
                       "pending: another page is at its target", "pending: the page there cannot be read",
                       "pending: no repository is known here for")


def landing_health(fm: dict) -> str:
    """What the agent does about a release landing held for it (its `health`), or "": only a
    release whose landing mark is one of LANDING_AGENT_HOLDS and whose `health` is word for word
    one landing writes (landing.AGENT_WORK, landing.REPO_WORK) counts. So a sender's own `health`
    hides nothing from the person and never puts its own words on the Needs-you page (landing
    also removes any `health` a release arrives with)."""
    import landing   # here and not at the top: landing imports much more than this needs
    mark = str(fm.get("landing") or "")
    if not fm.get("released_from") or fm.get("health") not in (landing.AGENT_WORK, landing.REPO_WORK):
        return ""
    return fm["health"] if mark.startswith(LANDING_AGENT_HOLDS) else ""


def set_health(root, writer: str, rows: list) -> Path | None:
    """Replace what the tool `writer` reports to the agent: `_meta/state/health/<writer>.yaml`,
    one row each (`id`, `text`: what is wrong, `agent`: what the agent does next, and any data
    the tool needs to try again). A writer with nothing left keeps an empty file once it had one."""
    path = Path(root) / HEALTH / f"{pages.check_name(writer, what='health writer')}.yaml"
    rows = [dict(r) for r in rows or [] if isinstance(r, dict) and r.get("id")]
    if not rows and not path.exists():
        return None
    text = yaml.safe_dump({"items": rows}, sort_keys=False, allow_unicode=True)
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return path
    return pages.write_text(path, text, root=root)


def health_rows(root, writer: str | None = None) -> list:
    """Every row the tools report to the agent (`set_health`), or only `writer`'s. An unreadable
    file is a row of its own: the agent repairs it, the person is never asked."""
    base = Path(root) / HEALTH
    out = []
    for path in sorted(base.glob("*.yaml")) if base.is_dir() else []:
        if writer is not None and path.stem != writer:
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError):
            out.append({"id": f"health-{path.stem}", "writer": path.stem, "text": f"{path.name} cannot be read",
                        "agent": "the tool writes it again on its next run"})
            continue
        for row in (data.get("items") if isinstance(data, dict) else None) or []:
            if isinstance(row, dict) and row.get("id"):
                out.append(dict(row, writer=path.stem))
    return out


def health_work(root) -> list:
    """(what is wrong, what the agent does) for everything the agent works through on its own:
    the tools' health rows, items held in `_outbound/held/` for a reason the agent can fix
    (`release.held_health`), and the releases landing could not file (`landing_health`)."""
    root = Path(root)
    # a row's words are data in a file a pull request can reach: one short plain line each
    out = [(untrusted_line(r.get("text") or r["id"], 160), untrusted_line(r.get("agent") or "", 160))
           for r in health_rows(root)]
    try:
        import release   # here and not at the top: release imports much more than this list needs
        out += [(untrusted_line(r["text"], 160), untrusted_line(r["agent"], 160)) for r in release.held_health(root)]
    except Exception as exc:   # never a silent gap: the line says the held items could not be read
        out.append((f"the held items could not be read ({exc.__class__.__name__})",
                    "the release run reads them again"))
    stubs = 0
    for rel, fm, body in pages_in(root, CANDIDATES):
        work = landing_health(fm)
        if work:
            out.append((f"a release from {untrusted_line(fm.get('released_from'), 40)} could not land "
                        f"({untrusted_line(fm.get('landing'), 120)})", work))
        elif unwritten(rel, fm, body):
            stubs += 1
    if stubs:   # never on the list itself (`unwritten`): the distill skill's agent writes them
        them = "them" if stubs != 1 else "it"
        out.append((f"{stubs} draft{'s wait' if stubs != 1 else ' waits'} for an agent to write {them}",
                    f"the distill run writes {them} and a second agent checks {them}"))
    return out


def _health(root: Path) -> list:
    """One line for everything the agent fixes on its own, with what it does: told, never asked."""
    work = health_work(root)
    if not work:
        return []
    shown = "; ".join(f"{what} ({does})" if does else what for what, does in work[:HEALTH_SHOWN])
    more = f"; and {len(work) - HEALTH_SHOWN} more" if len(work) > HEALTH_SHOWN else ""
    return [Item(id="health", kind="health",
                 text=f"your agent is working on {len(work)} thing{'s' if len(work) != 1 else ''}: {shown}{more}",
                 recommendation="nothing: your agent handles these on its next runs",
                 default="the agent keeps working through them", payload=HEALTH.as_posix() + "/")]


# ----- one list per person: items routed to them in the spaces they mount -------------------
def _names_person(value, person: str) -> bool:
    if not person or value in (None, ""):
        return False
    values = value if isinstance(value, list) else [value]
    return any(str(v).strip().lower() == person.strip().lower() for v in values)


def routed_items(root: Path, space: spaceyaml.Space, person: str) -> list:
    """Items in a mounted space that name this person: a landing pending for them (`for`), a
    check routed to them first, a librarian or question item `for` them; and in a space they
    own, the routine items that name nobody (setup questions, the charter to sign, invitations
    are for its owner). Shown with the space's name; answered in that space.

    Another space wrote every word of these, so each text, recommendation, default and
    read-back is one short line (`untrusted_line`, MOUNT_TEXT_MAX), a routine item's kind is
    one of MOUNT_KINDS (else `request`) and it depends on at most MOUNT_DEPENDS: a mount can
    ask, never raise this person's alarms or jump their list."""
    out = []

    def said(value, fallback: str = "") -> str:
        return untrusted_line(_text(value, fallback), MOUNT_TEXT_MAX)

    for mount in space.mounts:
        raw = str(mount.get("name", ""))
        name = untrusted_line(raw, 60)   # the label shown; the payload keeps the mount's own name
        mroot = space.mount_root(raw)
        if mroot is None:
            continue
        for rel, fm, body in pages_in(mroot, CANDIDATES):
            if _names_person(fm.get("for"), person) and not landing_health(fm):
                out.append(Item(id=_text(fm.get("id"), Path(rel).stem), kind="candidate",
                                text=f"[{name}] {said(fm.get('title'), Path(rel).stem)} "
                                     f"({said(fm.get('landing'), 'pending')}): sign?",
                                recommendation=said(fm.get("recommendation"), "yes"), default="stays pending",
                                payload=f"{raw}:{rel}", fingerprint=pages.fingerprint(body)))
        for rel, fm, body in pages_in(mroot, CHECKS):
            route = fm.get("route") or []
            if route and _names_person(route[0], person):
                out.append(Item(id=_text(fm.get("id"), Path(rel).stem), kind="check",
                                text=f"[{name}] {said(fm.get('title'), Path(rel).stem)}",
                                recommendation=said(fm.get("recommendation")), default="page stays as it is",
                                payload=f"{raw}:{rel}", fingerprint=pages.fingerprint(body)))
        for rel, fm, body in pages_in(mroot, QUESTIONS):
            if _names_person(fm.get("for"), person):
                out.append(Item(id=_text(fm.get("id"), Path(rel).stem), kind="question",
                                text=f"[{name}] {said(fm.get('question'), _text(fm.get('title'), Path(rel).stem))}",
                                recommendation=said(fm.get("recommendation")),
                                default=said(fm.get("default"), "the recommendation stands"),
                                payload=f"{raw}:{rel}", fingerprint=pages.fingerprint(body)))
        try:
            owns = _names_person(spaceyaml.load(mroot).owner, person)
        except Exception:
            owns = False
        answered = answered_ids(mroot)
        for item in routine_items(mroot):
            if item.id in answered:
                continue
            if _names_person(item.for_, person) or (owns and not item.for_):
                item.kind = item.kind if item.kind in MOUNT_KINDS else "request"
                item.depends = min(item.depends, MOUNT_DEPENDS)
                item.text = f"[{name}] {said(item.text, item.id)}"
                item.recommendation, item.default = said(item.recommendation), said(item.default)
                item.check = said(item.check)
                item.payload = f"{raw}:{item.payload}"
                out.append(item)
    return out


# ----- the list ------------------------------------------------------------------
def order(items: list) -> list:
    far = dt.datetime.max.replace(tzinfo=dt.timezone.utc)
    rank = {kind: n for n, kind in enumerate(KIND_ORDER)}
    return sorted(items, key=lambda i: (i.deadline is None, i.deadline or far, -i.depends,
                                        rank.get(i.kind, len(rank)), i.id))


PR_TITLE_MAX = 80
FORK_NUMBERS_SHOWN = 10
# Control characters, line and paragraph separators, zero-width and bidirectional marks.
_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁠-⁩﻿]")


def untrusted_line(value, limit: int | None = PR_TITLE_MAX) -> str:
    """Text someone outside the space chose (a pull request title, a mail subject, an item a
    mounted space wrote) as one short plain line: control characters, line breaks, zero-width
    and bidirectional marks become spaces, runs of space collapse, and the result is cut at
    `limit` characters (None: never cut). It still reaches agents and the page, so keep it short."""
    flat = " ".join(_UNSAFE.sub(" ", str(value or "")).split())
    return flat if limit is None or len(flat) <= limit else flat[:limit - 3].rstrip() + "..."


# What turns a line of markdown into something live: an image (`![`), a link target (`](`),
# and HTML or an autolink (`<`). Escaped with a backslash, they show as the characters they are;
# a backslash itself is escaped first, so `\<` in the text cannot undo the escape.
_MD_LIVE = re.compile(r"\\|!(?=\[)|\](?=\()|<")


def md_inline(value, limit: int | None = None) -> str:
    """Text someone else chose, for one line of a markdown page or list: `untrusted_line` (one
    line, no control character, cut at `limit` when one is given), then with images, links and
    HTML made inert, so a record's title can neither start a line of its own (a forged item or
    heading) nor put a tracking image or a disguised link on the person's own page."""
    return _MD_LIVE.sub(lambda m: "\\" + m.group(0), untrusted_line(value, limit))


def utf8_stdio() -> None:
    """Print any character a record carried. A Windows pipe writes cp1252 by default, and one
    emoji or Cyrillic subject would raise and print nothing (the session-start list included);
    UTF-8 with escapes for what still cannot be written never raises."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (ValueError, OSError):   # a stream already in use in another way: leave it
                pass


def _pr_number(pr) -> int | None:
    try:
        number = int(pr.get("number"))
    except (AttributeError, TypeError, ValueError):
        return None
    return number if number > 0 else None


PR_STATES_READ = 10   # the pull requests whose state is read, each one or two calls: the rest are listed as before


def _cannot_merge(gh, slug: str, number: int) -> str:
    """Why GitHub would refuse to merge an open pull request now, in a few words; "" when it would merge it,
    or cannot say (it has not worked it out yet, or the call failed). The person is asked to merge with one
    click: a click GitHub refuses should not be the first they hear of a conflict or a failed check."""
    state = ghrest.pull_state(gh, slug, number) or {}
    told = state.get("mergeable_state")
    if told == "dirty":
        return "it conflicts with main"
    if told == "behind":
        return "it is behind main"
    if told == "blocked":   # a required check that failed or still runs, or a review GitHub asks for
        sha = state.get("head_sha")
        return ("its checks failed" if sha and ghrest.failed_checks(gh, slug, sha)
                else "GitHub blocks it: a required check or review is missing")
    return ""


def _protected_prs(root: Path, why: bool = False) -> list:
    """Open pull requests on this repository that auto-merge did not arm: protected paths wait
    for the owner's one click (rules.md section 8). A pull request from a fork is never offered
    for merging: its checks ran the fork's own workflow and its title is a stranger's text, so
    all of them become one line with their numbers and no titles. With `why` (the command line, where the
    person reads the list and clicks), one that is offered for merging and that GitHub would refuse to merge
    now says so (`_cannot_merge`: a conflict with main, a failed or missing check), one more read per such
    pull request, for PR_STATES_READ of them at most; a routine's list makes no such read. They are read through REST
    (ghrest), which a cloud run allows. Silent when this is not a repository with an origin, or
    when there is no `gh` at all where this runs: the one `gh` call goes through a wrapper that
    notes whether gh ran, so a call that ran and failed (signed out, offline, refused) is told
    apart and becomes one item saying the list is incomplete, never an empty list."""
    if not (root / ".git").exists():
        return []
    slug = ghrest.repo_slug(root)
    if slug is None:
        return []
    gh, ran = ghrest.runner(root), []

    def asked(*args: str):
        done = gh(*args)
        ran.append(done is not None)
        return done
    prs = ghrest.open_pulls(asked, slug)
    if prs is None:
        if not any(ran):
            return []
        return [Item(id="pr-list-unreadable", kind="request",
                     text="your open pull requests could not be read: any waiting for your merge are missing "
                          "from this list",
                     recommendation="check that gh can reach GitHub where this runs (gh auth status)",
                     default="they wait unseen")]
    out, forks = [], []
    for pr in prs:
        number = _pr_number(pr)
        if number is None or pr.get("draft"):
            continue
        if pr.get("cross_repo") is not False:   # a fork, or GitHub could not say: never "merge it"
            forks.append(number)
            continue
        if pr.get("auto_merge"):
            continue
        # said before the title, which is the author's text; read for the first few only
        reason = _cannot_merge(asked, slug, number) if why and len(out) < PR_STATES_READ else ""
        out.append(Item(id=f"pr-{number}", kind="protected_pr",
                        text=(f"pull request {number} waits for your merge"
                              + (f" and cannot be merged yet ({reason})" if reason else "")
                              + f": {untrusted_line(pr.get('title'))}"),
                        recommendation=("ask an agent to make it mergeable (bring main in, or fix what the checks "
                                        "say), then merge it if the change is yours or you have read it" if reason
                                        else "merge it if the change is yours or you have read it"),
                        default="it waits", depends=2, payload=str(pr.get("url", ""))))
    if forks:
        forks.sort()
        n = len(forks)
        shown = ", ".join(str(f) for f in forks[:FORK_NUMBERS_SHOWN])
        if n > FORK_NUMBERS_SHOWN:
            shown += f" and {n - FORK_NUMBERS_SHOWN} more"
        head = "1 pull request from a fork waits" if n == 1 else f"{n} pull requests from forks wait"
        out.append(Item(id="pr-forks", kind="fork_pr",
                        text=f"{head} for a review ({shown}); read the diff on GitHub before merging",
                        recommendation="review the diff yourself; agents never merge",
                        default="they wait", payload=",".join(str(f) for f in forks)))
    return out


def fork_pr_count(items: list) -> int:
    """How many fork pull requests the one `fork_pr` line stands for (the mail counts them)."""
    return sum(len([p for p in i.payload.split(",") if p]) for i in items if i.kind == "fork_pr")


def collect(root, now: dt.datetime | None = None, online: bool = True, person: str | None = None,
            why: bool = False) -> list:
    """Every item that needs a person in this space, in the order to show them; in a personal
    space, also the items routed to its person in the spaces it mounts (one list per person)."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    answered = answered_ids(root)
    items = (_fold_drafts(root, _candidates(root, space)) + _checks(root) + _queue(root, space, now)
             + _outbox(root, space, now)
             + _questions(root) + _answers(root)
             + [i for i in routine_items(root) if i.id not in answered] + _classification(root, space)
             + _doubt(root) + _tracker(root) + _health(root)
             + (_protected_prs(root, why) if online else []))
    person = person if person is not None else (space.owner if space.profile == "personal" else "")
    if person:
        seen = {i.id for i in items}
        items += [i for i in routed_items(root, space, person) if i.id not in seen]
    return order(items)


def _line(item: Item) -> str:
    """One item as the one line render and `--doubt` print: its text, what is recommended and
    what happens if nothing is done, each made safe with `md_inline`."""
    text = md_inline(item.text)
    text = text if text.endswith((".", "?", "!")) else text + "."
    when = pages.iso(item.deadline) if item.deadline else "no deadline"
    check = md_inline(item.check)
    return (f"{text} I recommend: {md_inline(item.recommendation) or 'your call'}. "
            f"If nothing: {md_inline(item.default) or 'nothing changes'} ({when})."
            + (f" Read-back: {check}." if check else ""))


def render(root, now: dt.datetime | None = None, limit: int = 7, items: list | None = None) -> str:
    """The list as markdown: about `limit` lines, the rest as a count, the five verbs last. Each
    item is one line whatever its text holds (`md_inline`), so its number is its own."""
    items = collect(root, now) if items is None else items
    count = len(items)
    lines = [f"# Needs you ({count} item{'s' if count != 1 else ''})", ""]
    if not items:
        lines.append("Nothing needs you.")
        return "\n".join(lines) + "\n"
    for n, item in enumerate(items[:limit], 1):
        lines.append(f"{n}. {_line(item)}")
    if count > limit:
        lines.append(f"and {count - limit} more")
    lines += ["", VERBS_LINE]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--json", action="store_true", help="print the items as JSON instead of markdown")
    ap.add_argument("--limit", type=int, default=7, help="lines to show before `and N more`")
    ap.add_argument("--doubt", action="store_true", help="open the held-in-doubt line: one item each")
    ap.add_argument("--drafts", action="store_true", help="open the drafts line: one item per plain draft")
    ap.add_argument("--once-a-day", action="store_true",
                    help="print only in the day's first session; later calls that day print nothing")
    args = ap.parse_args(argv)
    utf8_stdio()
    if args.doubt or args.drafts:
        items = doubt_items(args.root) if args.doubt else draft_items(args.root)
        if args.json:
            print(json.dumps([i.to_dict() for i in items], indent=2, ensure_ascii=False))
        else:
            for n, item in enumerate(items, 1):
                print(f"{n}. {md_inline(item.id)}: {md_inline(item.text)} I recommend: "
                      f"{md_inline(item.recommendation)}. If nothing: {md_inline(item.default)}.")
            if not items:
                print("Nothing is held in doubt." if args.doubt else "No drafts wait.")
        return 0
    once = args.once_a_day and not args.json
    if once:
        shown = Path(args.root) / SHOWN
        today = spaceyaml.load(args.root).now().astimezone().date().isoformat()   # the person's day
        if shown.is_file() and shown.read_text(encoding="utf-8").strip() == today:
            return 0
    if (Path(args.root) / "space.yaml").is_file():
        import status   # the verdict cut-over, before the list reads the answers (`status.pin_cutover`)
        status.pin_cutover(args.root)
    items = collect(args.root, why=True)   # the person reads this list: a pull request says why it cannot merge
    if args.json:
        print(json.dumps([i.to_dict() for i in items], indent=2, ensure_ascii=False))
    else:
        print(render(args.root, limit=args.limit, items=items), end="")
    if once:
        pages.write_text(shown, today + "\n", root=args.root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
