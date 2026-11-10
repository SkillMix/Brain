"""Requests: the fourth wire (vision section 10). A space asks a subject's home for what it
cannot read; almost none of them reach a person.

The asker writes a request as a file; the release leg carries it into the corridor (a
`channel` space between the two, read by those two only); the holder's channel step answers
the part its standing choices cover at once, names the rest without sending it, and lists
the remainder for its person, pre-answered; the answer travels back the same way and lands
in the asker as dated evidence with its tier and age.

    python request.py [root] ask --subject S --class C --question Q [--narrowest N]
                                 [--asked-by NAME] [--force]
    python request.py [root] answer          the holder's channel step (the sync task runs it)
    python request.py [root] collect         the asker lands answers and marks expiries
    python request.py [root] status <rq-id>
    python request.py [root] wait <rq-id> [--timeout MINUTES]    the helper a live session runs

Classes: `status` (status tier, milestones, last contact date), `milestones`, `contact` (last
contact date), `detail` (anything else from the knowledge layer), `correspondence` (mail,
calls, documents). Standing choices (`space.yaml` `request_choices: [{from, class,
answer}]`, `from` a space name, a profile or `any`, `answer` `at_once`, `ask` or `never`)
come with template defaults per profile; a personal space ships none, so its person always
decides. Unanswered after one scaled day a request expires. After five requests of one class
from one asker in thirty scaled days the holder gets one proposal to answer that class at
once. Both sides log; neither log leaves its space.

A corridor's other side is read from its `between`, bound to the name this space mounted it
under (`channel-<a>-<b>`), so the other side cannot rewrite it to pass for another space. A
request file is written by the other side of the corridor, so the holder trusts none of it
but the question. It answers only a request whose id is one `ask` makes, whose `from` is the
corridor's other side, and whose `subject` is a registered subject this space is home of; any
other is declined, logged, and nothing is sent. The asker's profile is read from the registry
this space trusts (a person's registered workspace is `personal`, a subject that is that space
itself a `project`), else from the asker's own space.yaml where this space can read it, never
from the request; where neither says, a standing choice that names a profile counts only at
its strictest. The asker follows a subject that moved: when the registry names another home
than the one a request went to (which declines a subject not homed there), `collect` asks the
new home the same question instead of letting it expire. At once only facts travel: a status,
the dates of what is coming up and the date of the last contact, never a line's wording. The
person's answers are read from the verdict store sign.py writes (`status.verdicts`).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import registry as registry_mod  # noqa: E402
import spaceyaml  # noqa: E402

OUT_REQUESTS = Path("_outbound") / "requests"
OUT_ANSWERS = Path("_outbound") / "answers"
IN_REQUESTS = Path("_inbound") / "requests"
IN_ANSWERS = Path("_inbound") / "answers"
NEEDS = Path("_inbound") / "needs-you" / "requests.yaml"
HOLDER_LOG = Path("_meta") / "state" / "requests.jsonl"
ASKER_LOG = Path("_meta") / "state" / "requests-asked.jsonl"
EXPIRE_HOURS = 24
PROPOSE_AFTER = 5
PROPOSE_DAYS = 30
DATED = re.compile(r"^- (\d{4}-\d{2}-\d{2}):")   # a status page's coming-up line, its date first
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")          # all that leaves at once of a timeline line
RID = re.compile(r"^rq-[0-9a-f]{8}\Z")              # a request id, as new_id makes it
STRICTEST = {"never": 0, "ask": 1, "at_once": 2}


@dataclass
class Report:
    asked: list = field(default_factory=list)
    answered: list = field(default_factory=list)
    waiting: list = field(default_factory=list)
    never: list = field(default_factory=list)
    expired: list = field(default_factory=list)
    collected: list = field(default_factory=list)
    proposals: list = field(default_factory=list)
    refused: list = field(default_factory=list)    # requests declined as invalid: nothing was sent
    readdressed: list = field(default_factory=list)   # asked again of the subject's current home


# ----- small helpers ---------------------------------------------------------------------
def _jsonl(path: Path) -> list:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _when(value) -> dt.datetime | None:
    try:
        return pages.parse_iso(value) if value else None
    except (TypeError, ValueError):
        return None


def new_id() -> str:
    return "rq-" + secrets.token_hex(4)


# ----- corridors ---------------------------------------------------------------------------
def corridor(space: spaceyaml.Space, other: str, offline_roots: dict | None = None):
    """(name, root) of the channel space between this space and `other`: a mount whose
    `space.yaml` says `profile: channel` and `between` both names; offline roots first."""
    names = {space.name, other}
    candidates = []
    for name, root in (offline_roots or {}).items():
        candidates.append((name, Path(root)))
    for mount in space.mounts:
        mroot = space.mount_root(str(mount.get("name", "")))
        if mroot is not None:
            candidates.append((str(mount.get("name")), mroot))
    for name, root in candidates:
        try:
            cs = spaceyaml.load(root)
        except Exception:
            continue
        if cs.profile == "channel" and set(cs.data.get("between") or []) == names                 and _named_for(name, cs, space.name, other):
            return cs.name, root
    return None, None


def _named_for(name: str, cs: spaceyaml.Space, here: str, other: str) -> bool:
    """The corridor this space reaches as `name` (its mount's name, or its offline root's key)
    joins `here` and `other`: its own space.yaml says that name, and a name of the form
    `channel-<a>-<b>` (as `mint.py --channel` makes one) is the two names sorted. Both sides
    can write a corridor's space.yaml, but not this space's mount of it, so the other side
    cannot rewrite `between` to pass for a space it is not. A corridor minted under another
    name is bound by its `between` alone."""
    if cs.name != name:
        return False
    return not name.startswith("channel-") or name == "channel-" + "-".join(sorted([here, other]))


def corridors(space: spaceyaml.Space, offline_roots: dict | None = None) -> list:
    """(name, root, the other space) for every corridor this space can read."""
    out, seen = [], set()
    pairs = list((offline_roots or {}).items()) + [
        (str(m.get("name")), space.mount_root(str(m.get("name", "")))) for m in space.mounts]
    for name, root in pairs:
        if root is None:
            continue
        try:
            cs = spaceyaml.load(root)
        except Exception:
            continue
        between = list(cs.data.get("between") or [])
        if cs.profile == "channel" and space.name in between and cs.name not in seen:
            other = next((b for b in between if b != space.name), "")
            if not _named_for(str(name), cs, space.name, other):
                continue   # its `between` is not the corridor this space mounted
            seen.add(cs.name)
            out.append((cs.name, Path(root), other))
    return out


def channel_files(root: Path, folder: Path) -> dict:
    """name -> (fm, body) of the pages under a corridor's folder (its working tree)."""
    out = {}
    base = root / folder
    for path in sorted(base.glob("*.md")) if base.is_dir() else []:
        try:
            out[path.stem] = pages.read(path)
        except Exception:
            continue
    return out


# ----- choices -----------------------------------------------------------------------------
def choice_for(space: spaceyaml.Space, asker: str, asker_profile: str | None, cls: str) -> str:
    """at_once | ask | never: the first standing choice that names the asker, its profile or
    any; nothing named means the holder's person decides. With the asker's profile unknown
    (None), a choice that names a profile may be the one that governs, so the strictest of
    those and the `any` choice counts."""
    if asker_profile is None:
        named = [c for c in space.request_choices if str(c.get("class")) == cls]
        if any(str(c.get("from") or "any") == asker for c in named):
            return choice_for(space, asker, "", cls)
        profiled = [str(c.get("answer") or "ask") for c in named
                    if str(c.get("from") or "any") in spaceyaml.PROFILES]
        if profiled:
            fallback = choice_for(space, asker, "", cls)
            return min(profiled + [fallback], key=lambda a: STRICTEST.get(a, 0))
        asker_profile = ""
    order = {asker: 0, asker_profile: 1, "any": 2}
    best = None
    for choice in space.request_choices:
        who = str(choice.get("from") or "any")
        if who in order and who != "" and str(choice.get("class")) == cls:
            if best is None or order[who] < order[best[0]]:
                best = (who, str(choice.get("answer") or "ask"))
    return best[1] if best else "ask"


# ----- rendering the covered answer --------------------------------------------------------
def _status_lines(root: Path, subject: str) -> dict:
    """The holder's status page for the subject, by section. A subject that is not a slug
    names no status page: nothing outside `live/subjects/` is ever read for a request."""
    if not pages.is_slug(subject):
        return {}
    try:
        path = pages.confine(root, f"live/subjects/{subject}.md", zones=("live/subjects/",), what="status page")
    except pages.UnsafePath:
        return {}
    if not path.is_file():
        return {}
    _, body = pages.read(path)
    out, current = {}, None
    for line in body.splitlines():
        if line.startswith("## "):
            current = line[3:].strip().lower()
            out[current] = []
        elif current and line.startswith("- ") and line.strip() != "- none":
            out[current].append(line.strip())
    return out


def render(root: Path, request: dict, now: dt.datetime, at_once: bool = True) -> tuple:
    """(covered lines, what was left out) for a class the standing choice answers. At once only
    facts travel (state, dates): a record's wording, which nobody here has judged, waits for the
    holder's person, who sees it all."""
    holder = spaceyaml.load(root)
    reg = registry_mod.Registry(root, holder)
    subject = str(request.get("subject") or "")
    cls = str(request.get("class") or "detail")
    sections = _status_lines(root, subject)
    found = reg.get(subject)
    lines, left = [], []
    if cls in ("status", "milestones", "contact") and not sections:
        left.append(f"no status page for {subject} here")
    if cls == "status" and found is not None:
        lines.append(f"- status: {found.state} (system, {holder.name}, {now:%Y-%m-%d})")
    if cls in ("status", "milestones"):
        coming = sections.get("coming up", [])[:5]
        if not at_once:
            lines += coming
        else:
            dates = [m.group(1) for m in (DATED.match(line) for line in coming) if m]
            lines += [f"- coming up: {day} (system, {holder.name}, {now:%Y-%m-%d})" for day in dates]
            if coming:
                left.append("the wording of what is coming up (the holder's person may send it)")
    if cls in ("status", "contact"):
        timeline = sections.get("timeline", [])
        if timeline:
            newest = timeline[0]
            date = newest.rsplit(",", 1)[-1].strip(" )")
            if DAY.match(date):   # a date, or nothing: never the line's own words
                lines.append(f"- last contact: {date} (witness, {holder.name}, {date})")
            else:
                left.append("the last contact date could not be read")
    if cls in ("detail", "correspondence"):
        left.append(f"{cls}: not covered by a standing choice")
    return lines, left


# ----- the asker ---------------------------------------------------------------------------
def readable_here(space: spaceyaml.Space, home: str | None) -> bool:
    return not home or space.space_root(home) is not None


def ask(root, subject: str, cls: str, question: str, narrowest: str = "", asked_by: str = "",
        now: dt.datetime | None = None, force: bool = False, readdressed_from: list | None = None) -> Path | None:
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    reg = registry_mod.Registry(root, space)
    home = reg.home_of(subject) or (reg.get(subject).space if reg.get(subject) else None)
    if not home:
        raise ValueError(f"{subject}: no home in the registry; ask its owner instead")
    if readable_here(space, home) and not force:
        return None
    if cls not in spaceyaml.REQUEST_CLASSES:
        raise ValueError(f"class must be one of {', '.join(spaceyaml.REQUEST_CLASSES)}")
    rid = new_id()
    fm = {"id": rid, "type": "request", "title": f"Request to {home}: {question[:80]}", "status": "open",
          "generated": pages.generated("request", now), "to": home, "from": space.name,
          "from_profile": space.profile, "asked_by": asked_by or space.owner, "subject": subject,
          "class": cls, "question": question, "narrowest": narrowest or spaceyaml.REQUEST_CLASSES[cls],
          "at": pages.iso(now), "expires_at": pages.iso(now + space.scale(EXPIRE_HOURS))}
    if readdressed_from:   # the requests it follows, kept here (release carries none of it)
        fm["readdressed_from"] = [str(r) for r in readdressed_from]
    path = pages.write(root / OUT_REQUESTS / f"{rid}.md", fm, f"{question}\n")
    _append(root / ASKER_LOG, {"at": pages.iso(now), "id": rid, "to": home, "subject": subject,
                               "class": cls, "outcome": "asked"})
    return path


def _answer_page(root: Path, rid: str, fm: dict, body: str, request: dict, now) -> Path:
    page = {"id": "p-" + hashlib.sha1(rid.encode()).hexdigest()[:8], "type": "source",
            "title": f"Answer from {fm.get('from')}: {request.get('question', '')[:80]}", "status": "draft",
            "generated": pages.generated("request", now), "request": rid, "subject": request.get("subject"),
            "from_space": fm.get("from"), "approved_by": fm.get("approved_by"),
            "recommendation": "yes, noted", "default": "stays where it is"}
    return pages.write(root / IN_ANSWERS / f"{rid}.md", page, body)


READDRESS_MAX = 2   # how many times one question follows its subject to a new home


def _moved_home(space, reg, req: dict) -> str | None:
    """The subject's home in the registry today when it is not the space the request went to
    (nor this one): the holder declines a request for a subject not homed there, so the question
    follows the subject instead of expiring. None when the home is the same, unknown, or the
    question has followed it READDRESS_MAX times already."""
    subject = req.get("subject")
    if reg is None or not pages.is_slug(subject) or len(req.get("readdressed_from") or []) >= READDRESS_MAX:
        return None
    try:   # only the registry this space trusts moves a question: never a corridor's, which the holder writes
        home = (reg.trusted_view() if hasattr(reg, "trusted_view") else reg).home_of(str(subject))
    except Exception:
        return None
    if not pages.is_slug(home) or home in (str(req.get("to")), space.name):
        return None
    return home


def _readdress(root: Path, space, req: dict, qbody: str, path: Path, now, report) -> bool:
    """Ask the subject's current home the same question, once the registry names another
    (`_moved_home`): a new request, with its own `asked` line, so release ships it only to that
    home's corridor; the old one is marked `readdressed` and never ships again."""
    rid = str(req.get("id") or path.stem)
    try:
        new = ask(root, str(req.get("subject")), str(req.get("class")), str(req.get("question") or qbody.strip()),
                  str(req.get("narrowest") or ""), str(req.get("asked_by") or ""), now=now,
                  readdressed_from=[*(req.get("readdressed_from") or []), rid])
    except ValueError:
        return False
    to = new.stem if new is not None else "here"   # None: the new home is readable here, no request needed
    pages.write(path, dict(req, status="readdressed", readdressed_to=to), qbody)
    _append(root / ASKER_LOG, {"at": pages.iso(now), "id": rid, "to": req.get("to"), "outcome": "readdressed",
                               "readdressed_to": to})
    report.readdressed.append(rid)
    return True


def collect(root, now: dt.datetime | None = None, offline_roots: dict | None = None) -> Report:
    """Land arrived answers as dated evidence; re-address a request whose subject's home moved
    (`_readdress`); mark expired requests; tell the person."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    report = Report()
    try:
        reg = registry_mod.Registry(root, space)
    except Exception:
        reg = None
    answers = {}
    for name, croot, _ in corridors(space, offline_roots):
        answers.update(channel_files(croot, IN_ANSWERS))
    for path in sorted((root / OUT_REQUESTS).glob("*.md")) if (root / OUT_REQUESTS).is_dir() else []:
        req, qbody = pages.read(path)
        rid = str(req.get("id") or path.stem)
        if req.get("status") not in ("open", "shipped"):
            continue
        if rid in answers:
            fm, body = answers[rid]
            target = root / "knowledge" / "sources" / f"{now:%Y}" / f"{now:%m}" / f"answer-{rid}.md"
            wfm = {"id": "p-" + hashlib.sha1(f"w{rid}".encode()).hexdigest()[:8], "type": "source",
                   "title": f"Answer from {fm.get('from')}: {req.get('question', '')[:80]}", "status": "draft",
                   "generated": pages.generated("request", now), "record": f"answer-{rid}", "source": "request",
                   "kind": "document", "at": fm.get("answered_at") or pages.iso(now), "from": fm.get("from"),
                   "subject": req.get("subject"), "mentions": [], "audience": "owner", "identifiers": {},
                   "approved_by": fm.get("approved_by")}
            pages.write(target, wfm, body)
            _answer_page(root, rid, fm, body, req, now)
            pages.write(path, dict(req, status="answered", answered_at=pages.iso(now)), qbody)
            _append(root / ASKER_LOG, {"at": pages.iso(now), "id": rid, "to": req.get("to"), "outcome": "answered"})
            try:
                import questions_log
                questions_log.append(root, str(req.get("question")), req.get("subject"), "witness",
                                     [target.relative_to(root).as_posix()], answered_by_request=True)
            except Exception:
                pass
            _notice(root, space, req, body)
            report.collected.append(rid)
            continue
        if _moved_home(space, reg, req) and _readdress(root, space, req, qbody, path, now, report):
            continue
        expires = _when(req.get("expires_at"))
        if expires is not None and now > expires:
            pages.write(path, dict(req, status="expired"), qbody)
            _append(root / ASKER_LOG, {"at": pages.iso(now), "id": rid, "to": req.get("to"), "outcome": "expired"})
            _answer_page(root, rid, {"from": req.get("to"), "approved_by": None},
                         f"No answer arrived from {req.get('to')}; the request expired. A no and silence "
                         f"look the same: ask its owner directly if it matters.\n", req, now)
            report.expired.append(rid)
    return report


def _notice(root: Path, space, req: dict, body: str) -> None:
    """An owner notice the chain sends at once to the person themselves (never a chat
    self-message), queued once per request."""
    try:
        import owner_email
    except ImportError:
        return
    notice = {"kind": "fyi", "sender": "request", "title": "One answer arrived",
              "summary": f"Your question to {req.get('to')} was answered. It waits on your list.",
              "allow_terms": [space.name, str(req.get("to"))], "key": f"answer-{req.get('id')}"}
    try:
        owner_email.queue(root, notice, today=f"{space.now():%Y-%m-%d}")
    except (Exception, SystemExit):
        return


# ----- the holder --------------------------------------------------------------------------
def _verdicts(root: Path) -> dict:
    import status
    return status.verdicts(root)


def _paused(root: Path, path: str, now, space) -> bool:
    try:
        import release
        return release.is_paused(root, path, now)
    except Exception:
        return False


def _log_line(root: Path, line: str) -> None:
    """One line of log.md: values from a request (another space wrote them) never break it."""
    pages.append_text(Path(root) / "log.md", " ".join(str(line).split()) + "\n", root=Path(root))


def _problem(holder, reg, rid: str, req: dict, other: str) -> str:
    """Why a request is not one this space answers, or "": its id is not one `ask` makes, it
    does not come from the corridor's other side, its subject is not a registered subject this
    space is home of, or its class is none."""
    if not RID.match(rid) or str(req.get("id") or rid) != rid:
        return "its id is not a request id"
    if str(req.get("from")) != other:
        return f"it says it is from {str(req.get('from'))[:40]!r}, but the corridor's other side is {other}"
    subject = req.get("subject")
    if not pages.is_slug(subject) or reg.get(subject) is None:
        return "its subject is not a registered subject"
    found = reg.get(subject)
    home = reg.home_of(subject) or found.space
    if home != holder.name:
        return f"{subject} is not homed here"
    if str(req.get("class")) not in spaceyaml.REQUEST_CLASSES:
        return "its class is not a request class"
    return ""


def registry_profile(reg, name: str) -> str | None:
    """The profile the registry this space trusts (its own and the brain's it mounts) gives the
    space called `name`: `personal` when it is the registered workspace of exactly one person,
    `project` when one subject is that space itself (`id == home == name`, as mint writes it);
    None when neither, or both, says so. Only this space's own registry and the brain's are read
    (`Registry.trusted_view`), whose fields wait for their owner's click (`gate.registry_reasons`);
    never a corridor's or a project mount's, which the asker can write, so an asker cannot choose
    its own."""
    if reg is None or not pages.is_slug(name):
        return None
    reg = reg.trusted_view() if hasattr(reg, "trusted_view") else reg
    every = [s for s in reg.everywhere() if s.state != "archived"]
    people = [s for s in every if s.kind == "people" and str(s.extra.get("workspace") or "") == name]
    projects = [s for s in every if s.id == name and str(s.home or "") == name]
    if len(people) == 1 and not projects:
        return "personal"
    if len(projects) == 1 and not people:
        return "project"
    return None


def _asker_profile(holder, other: str, offline_roots: dict | None, reg=None) -> str | None:
    """The asker's profile: what the registry this space trusts says (`registry_profile`), else
    the asker's own space.yaml when this space can read it; else None (a standing choice that
    names a profile then counts only at its strictest)."""
    known = registry_profile(reg, other)
    if known is not None:
        return known
    try:
        found = _space_named(holder, other, offline_roots)
    except Exception:
        return None
    return found.profile if found is not None and found.name == other else None


def _ship_answer(root: Path, holder, rid: str, request: dict, lines: list, left: list, approved: str, now) -> Path:
    uncovered = ("\n\nNot covered, named and not sent: " + "; ".join(sorted(set(left)))) if left else ""
    body = (f"Answer to: {request.get('question')}\n\n" + ("\n".join(lines) or "- nothing covered") + uncovered + "\n")
    fm = {"id": rid, "type": "source", "title": f"Answer to {rid}", "status": "draft",
          "generated": pages.generated("request", now), "request": rid, "from": holder.name,
          "to": request.get("from"), "class": request.get("class"), "approved_by": approved,
          "answered_at": pages.iso(now), "covered": bool(lines), "destination": None}
    return pages.write(root / OUT_ANSWERS / f"{rid}.md", fm, body, root=root)


def _answer_digest(path: Path) -> str:
    """The fingerprint of the answer's body as written: release.ship_requests ships it only while
    the file still holds that text, so an answer changed after it was given never leaves."""
    return pages.fingerprint(pages.read(path)[1])


def answer(root, now: dt.datetime | None = None, offline_roots: dict | None = None) -> Report:
    """The channel step: answer from standing choices, list the remainder, expire, propose."""
    root = Path(root)
    holder = spaceyaml.load(root)
    now = now or holder.now()
    report = Report()
    handled = {}
    for row in _jsonl(root / HOLDER_LOG):
        handled.setdefault(str(row.get("id")), []).append(row)
    given = _verdicts(root)
    reg = registry_mod.Registry(root, holder)
    items, choices_changed = [], False
    for cname, croot, other in corridors(holder, offline_roots):
        aprofile = _asker_profile(holder, other, offline_roots, reg)
        for rid, (req, _) in sorted(channel_files(croot, IN_REQUESTS).items()):
            if str(req.get("to")) != holder.name:
                continue
            outcomes = [r.get("outcome") for r in handled.get(rid, [])]
            if any(o in ("answered", "never", "expired", "declined") for o in outcomes):
                continue
            why = _problem(holder, reg, rid, req, other)
            if why:   # declined and logged under the corridor's other side; nothing is sent
                _append(root / HOLDER_LOG, {"at": pages.iso(now), "id": rid, "from": other,
                                            "class": str(req.get("class"))[:40], "outcome": "declined",
                                            "reason": "invalid", "why": why})
                report.refused.append(rid)
                continue
            expires = _when(req.get("expires_at"))
            asker, cls = other, str(req.get("class"))
            if expires is not None and now > expires:
                _append(root / HOLDER_LOG, {"at": pages.iso(now), "id": rid, "from": asker, "class": cls,
                                            "asked_by": req.get("asked_by"), "outcome": "expired"})
                report.expired.append(rid)
                continue
            choice = choice_for(holder, asker, aprofile, cls)
            if choice == "never":
                _append(root / HOLDER_LOG, {"at": pages.iso(now), "id": rid, "from": asker, "class": cls,
                                            "asked_by": req.get("asked_by"), "outcome": "never", "choice": "never"})
                report.never.append(rid)
                continue
            verdict = given.get(rid) or {}
            choice_path = f"request_choice:{asker}:{cls}"
            if choice == "at_once" and _paused(root, choice_path, now, holder):
                choice = "ask"   # a failed sample paused this standing choice: the holder answers by hand
            lines, left = render(root, req, now, at_once=choice == "at_once")
            if choice == "at_once" and lines:
                written = _ship_answer(root, holder, rid, req, lines, left, f"standing choice: {cls} for {asker}", now)
                _log_line(root, f"- {pages.iso(now)} answered {rid} for {asker} at_once "
                                f"(standing choice: {cls}; path {choice_path})")
                _append(root / HOLDER_LOG, {"at": pages.iso(now), "id": rid, "from": asker, "class": cls,
                                            "asked_by": req.get("asked_by"), "outcome": "answered",
                                            "choice": f"{cls}:at_once", "subject": req.get("subject"),
                                            "sha256": _answer_digest(written)})
                report.answered.append(rid)
                continue
            if verdict.get("verb") in ("yes", "edit", "always"):
                sent = [f"- {verdict['note']}"] if verdict.get("verb") == "edit" and verdict.get("note") else lines
                written = _ship_answer(root, holder, rid, req, sent, [] if verdict.get("verb") == "edit" else left,
                                       f"{verdict.get('by')} by name", now)
                _append(root / HOLDER_LOG, {"at": pages.iso(now), "id": rid, "from": asker, "class": cls,
                                            "asked_by": req.get("asked_by"), "outcome": "answered",
                                            "choice": f"person:{verdict.get('verb')}",
                                            "sha256": _answer_digest(written)})
                if verdict.get("verb") == "always":
                    holder.data["request_choices"] = holder.request_choices + [
                        {"from": asker, "class": cls, "answer": "at_once"}]
                    choices_changed = True
                report.answered.append(rid)
                continue
            if verdict.get("verb") == "no":
                _append(root / HOLDER_LOG, {"at": pages.iso(now), "id": rid, "from": asker, "class": cls,
                                            "asked_by": req.get("asked_by"), "outcome": "declined"})
                report.never.append(rid)
                continue
            if "asked" not in outcomes:
                _append(root / HOLDER_LOG, {"at": pages.iso(now), "id": rid, "from": asker, "class": cls,
                                            "asked_by": req.get("asked_by"), "outcome": "asked"})
            draft = "; ".join(l.lstrip("- ") for l in lines) or "nothing my standing choices cover"
            widens = f"{asker} cannot read {req.get('subject')} today"
            items.append({"id": rid, "kind": "request",
                          "text": (f"{req.get('asked_by') or asker} ({asker}) asks: {req.get('question')} I would "
                                   f"answer: {draft}. It widens: {widens}."),
                          "recommendation": "the summary only (yes), or edit with your own words",
                          "default": f"expires {req.get('expires_at')}: no answer", "deadline": req.get("expires_at"),
                          "depends": 0})
            report.waiting.append(rid)
    for prop in _proposals(root, holder, now, given):
        items.append(prop)
        report.proposals.append(prop["id"])
    for item_id, verdict in given.items():
        if item_id.startswith("rq-propose-") and verdict.get("verb") in ("yes", "always"):
            _, _, rest = item_id.partition("rq-propose-")
            asker, _, cls = rest.rpartition("-")
            wanted = {"from": asker, "class": cls, "answer": "at_once"}
            if wanted not in holder.request_choices:
                holder.data["request_choices"] = holder.request_choices + [wanted]
                choices_changed = True
    if choices_changed:
        spaceyaml.save(holder)
    target = root / NEEDS
    if items or target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump({"items": items}, sort_keys=False, allow_unicode=True, width=100),
                          encoding="utf-8", newline="\n")
    return report


def _space_named(holder, name: str, offline_roots: dict | None):
    for key, root in (offline_roots or {}).items():
        if key == name:
            try:
                return spaceyaml.load(root)
            except Exception:
                return None
    root = holder.mount_root(name)
    return spaceyaml.load(root) if root is not None else None


def _proposals(root: Path, holder, now, given: dict) -> list:
    since = now - holder.scale(PROPOSE_DAYS * 24)
    counts: dict = {}
    for row in _jsonl(root / HOLDER_LOG):
        at = _when(row.get("at"))
        if at is None or at < since or row.get("outcome") != "asked":
            continue
        counts[(str(row.get("from")), str(row.get("class")))] = counts.get((str(row.get("from")), str(row.get("class"))), 0) + 1
    out = []
    for (asker, cls), n in sorted(counts.items()):
        item_id = f"rq-propose-{asker}-{cls}"
        if n < PROPOSE_AFTER or item_id in given or choice_for(holder, asker, "", cls) == "at_once":
            continue
        out.append({"id": item_id, "kind": "request",
                    "text": f"{asker} asked for {cls} {n} times this month. Answer {cls} at once from now on?",
                    "recommendation": "yes: one decision instead of one a week", "default": "you keep deciding each one",
                    "depends": 1})
    return out


# ----- status and the waiting helper ------------------------------------------------------
def state_of(root, rid: str) -> dict:
    root = Path(root)
    path = root / OUT_REQUESTS / f"{rid}.md"
    if not path.exists():
        return {"id": rid, "status": "unknown"}
    fm, _ = pages.read(path)
    out = {"id": rid, "status": fm.get("status"), "to": fm.get("to"), "expires_at": fm.get("expires_at")}
    answer_page = root / IN_ANSWERS / f"{rid}.md"
    if answer_page.exists():
        out["answer"] = pages.read(answer_page)[1].strip()
    return out


def wait(root, rid: str, timeout_minutes: float = 15, poll_seconds: float = 30, refresh=None) -> dict:
    """Poll for the answer: refresh the corridors (the sync script's pins), collect, report."""
    deadline = time.monotonic() + timeout_minutes * 60
    while True:
        if refresh is not None:
            refresh()
        collect(root)
        state = state_of(root, rid)
        if state.get("status") in ("answered", "expired") or time.monotonic() >= deadline:
            return state
        time.sleep(poll_seconds)


def _refresh_mounts(root: Path):
    def run():
        subprocess.run(["git", "-C", str(root), "submodule", "update", "--remote", "--quiet"],
                       capture_output=True, text=True)
    return run


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    verbs = ("ask", "answer", "collect", "status", "wait")
    root = Path(".")
    if argv and argv[0] not in verbs:
        root = Path(argv.pop(0))
    if not argv or argv[0] not in verbs:
        print(__doc__)
        return 2
    verb, rest = argv[0], argv[1:]
    if verb == "ask":
        ap = argparse.ArgumentParser(prog="request.py ask")
        ap.add_argument("--subject", required=True)
        ap.add_argument("--class", dest="cls", required=True)
        ap.add_argument("--question", required=True)
        ap.add_argument("--narrowest", default="")
        ap.add_argument("--asked-by", default="")
        ap.add_argument("--force", action="store_true")
        opts = ap.parse_args(rest)
        path = ask(root, opts.subject, opts.cls, opts.question, opts.narrowest, opts.asked_by, force=opts.force)
        print(f"readable here: walk or search it" if path is None else f"asked: {path.stem}")
        return 3 if path is None else 0
    if verb == "answer":
        print(json.dumps(answer(root).__dict__))
        return 0
    if verb == "collect":
        print(json.dumps(collect(root).__dict__))
        return 0
    if verb == "status" and rest:
        print(json.dumps(state_of(root, rest[0]), indent=2))
        return 0
    if verb == "wait" and rest:
        ap = argparse.ArgumentParser(prog="request.py wait")
        ap.add_argument("rid")
        ap.add_argument("--timeout", type=float, default=15)
        opts = ap.parse_args(rest)
        print(json.dumps(wait(root, opts.rid, opts.timeout, refresh=_refresh_mounts(root)), indent=2))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
