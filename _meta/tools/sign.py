"""Sign: the one verb over any Needs-you item (shape 1).

A person answers yes, edit, no, later or always; `read_back` shows the exact text and
fingerprint of what would land and where; `apply` records the verdicts. This is the only
function in the template that writes `status: stable`, and only while executing a named
person's verdict: an empty `by`, or one that starts with `routine:`, is refused before
anything is touched (rules.md section 1, hard rule 3). Nothing here sends or runs git.

    python sign.py [root] read-back <ids...> [--body <id>=<file>]...
    python sign.py [root] apply --by <name> --verdicts <verdicts.yaml>

`--body` names an edit's write-up, a file relative to the root that must lie inside it
(`_scratch/sign/<id>.md`): the read-back shows that text and its fingerprint, and the
heading ends `(your edit)`; for an item whose edit does not become its text (a check, a doubt
item) it says the write-up is not used. For a page whose yes signs its text (a candidate that
is not a record or a review batch, or a leaving item that already carries a fingerprint), a
`base:` line follows the fingerprint: it names the draft the read-back was made from. A
first-release line lists the items it stands for and prints no fingerprint: a bare verb
answers it.

The verdicts file maps item ids to a verb, a [verb, note] pair, or a mapping. A yes, edit
or always on such a page is a mapping with the fingerprint and base its read-back printed,
and an edit names its write-up as `body_file`:
    p-1a2b3c4d: {verb: yes, fingerprint: <hex>, base: <hex>}
    p-5e6f7a8b: {verb: edit, body_file: _scratch/sign/p-5e6f7a8b.md, fingerprint: <hex>, base: <hex>}
    p-9c0d1e2f: [later, "after the call"]
    p-3a4b5c6d: no

`apply` signs exactly the text that was read back. When the draft changed since (distill
refreshed it), or the text did, the item is refused; from the command line, a page that
names no fingerprint is refused too, and so is a line of the weekly review batch (or the
whole batch) where a yes, edit or always would sign a draft: that line stays open, and the
draft is signed by its own id after its read-back. A refused item stays where it is, still
on Needs you, and writes only `- <ISO> refused <id> by <name>: <why>` to `log.md`; the
other items go ahead. A body file that is missing or outside the space, or one given with a
note or under another verb, stops the whole run before anything is written.

Per kind: a candidate lands at its `target` signed (yes), with the note or the body file
as its body (edit), is discarded with a tombstone (no), gets a deadline one scaled day out
(later), or lands and adds a standing choice for its destination (always); a signed candidate
with a `destination` also queues a release carrying the signature, `at_once`, or `window` when
the page is flagged `high_stakes`. Signed over a page already at its target, the page keeps its
id, every record in its `evidence` (unless the candidate sets it aside), its `set_aside` entries
and its `high_stakes` flag (unless the candidate names its own). A check
verdict goes to `_meta/state/check-verdicts.jsonl`. A leaving release ships tonight
(yes), is pulled to `_outbound/held/` (no) or waits a scaled day (later). Every verdict
appends one line to `log.md`: `- <ISO> <verb> <id> by <name>`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import distill  # noqa: E402
import needs_you  # noqa: E402
import pages  # noqa: E402
import registry as registry_mod  # noqa: E402
import spaceyaml  # noqa: E402

VERBS = ("yes", "edit", "no", "later", "always")
CANDIDATE_ONLY = ("destination", "target", "deadline", "recommendation", "depends")
# what a page already at the target keeps when an edit of it is signed, unless the edit sets it:
# open conflict notes and what its release and landing recorded
KEPT_ON_RESIGN = ("contested", "released_by", "released_from", "released_at", "release_class",
                  "acted_for", "path", "domain", "landed_at", "landed_from")
CHECK_VERDICTS = Path("_meta") / "state" / "check-verdicts.jsonl"
CHECK_VERDICT_OF = {"yes": "yes", "always": "yes", "no": "no", "edit": "edit"}


@dataclass
class ApplyReport:
    signed: list = field(default_factory=list)
    edited: list = field(default_factory=list)
    discarded: list = field(default_factory=list)
    deferred: list = field(default_factory=list)
    standing_choices_added: list = field(default_factory=list)
    verdicts_recorded: int = 0
    released_queued: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    retracted: list = field(default_factory=list)
    refused: list = field(default_factory=list)   # {"id", "why"}: not what was read back


@dataclass
class Found:
    kind: str
    path: Path | None
    fm: dict
    body: str
    item: needs_you.Item | None = None  # a routine-written item with no file of its own
    members: list = field(default_factory=list)  # the queue items one first-release line stands for


# ----- finding items -------------------------------------------------------------
def index(root: Path) -> dict:
    """Every item a verdict can name, by its id and by its file stem."""
    found: dict = {}

    def add(kind, rel, fm, body, *also):
        path = root / rel
        entry = Found(kind, path, fm, body)
        for key in (str(fm.get("id") or ""), path.stem, *also):
            if key:
                found.setdefault(key, entry)

    waiting = {rel for rel, _, _ in needs_you.waiting_candidates(root)}   # signed once classified
    for folder, kind in ((needs_you.CANDIDATES, "candidate"), (needs_you.CHECKS, "check"),
                         (needs_you.QUESTIONS, "question"), (needs_you.ANSWERS, "answer")):
        for rel, fm, body in needs_you.pages_in(root, folder):
            if rel not in waiting:
                add(kind, rel, fm, body)
    firsts: dict = {}
    for rel, fm, body in needs_you.pages_in(root, needs_you.QUEUE):
        add("high_stakes" if fm.get("high_stakes") else "leaving", rel, fm, body)
        if fm.get("first_release") and not fm.get("high_stakes"):
            firsts.setdefault(f"first-{fm['first_release']}", []).append(Found("leaving", root / rel, fm, body))
    for key, members in firsts.items():
        found.setdefault(key, Found("first_release", None, {}, "", members=members))
    for rel, fm, body in needs_you.doubt_pages(root):   # also by the record it holds
        add("doubt", rel, fm, body, *[str(e) for e in fm.get("evidence") or []][:1])
    try:
        import tracker
        for path, fm, body in tracker.waiting_for_a_person(root):
            add("tracker", Path(path).relative_to(root), fm, body)
    except Exception:
        pass
    for item in needs_you.routine_items(root):
        found.setdefault(item.id, Found(item.kind, None, {}, "", item=item))
    return found


# ----- the pin: what was read back is what is signed ----------------------------------
PINNED_VERBS = ("yes", "edit", "always")


def base_of(fm: dict, body: str) -> str:
    """The draft a read-back was made from: its body and where and how it would go. When
    distill refreshes a candidate during the sitting, this changes."""
    head = (f"destination: {fm.get('destination') or ''}\n"
            f"high_stakes: {fm.get('high_stakes') or ''}\n"
            f"target: {fm.get('target') or ''}\n")
    return pages.fingerprint(head + pages.normalize(body))


def _page_candidate(entry: Found) -> bool:
    """A candidate that lands as a page: not a record (filed as evidence), not a review batch."""
    return (entry.kind == "candidate" and str(entry.fm.get("type")) != "source"
            and not entry.fm.get("review_batch"))


def _pinned(entry: Found) -> bool:
    """A page whose yes signs its text: a page candidate, or a leaving item that already
    carries a signature. A yes, edit or always on it names the fingerprint and base its
    read-back printed."""
    return entry.path is not None and (_page_candidate(entry) or (
        entry.kind in ("leaving", "high_stakes") and bool(entry.fm.get("fingerprint"))))


def _takes_edit(entry: Found) -> bool:
    """An item whose edit becomes the text that lands (or leaves)."""
    return entry.path is not None and (_page_candidate(entry)
                                       or entry.kind in ("leaving", "high_stakes", "tracker"))


def _text_of(entry: Found, edit: str | None = None) -> str:
    """The exact text that would land: the edit when there is one, else the item's own body;
    a candidate's without its `## New evidence` list."""
    body = edit if edit and edit.strip() and _takes_edit(entry) else entry.body
    if entry.kind == "candidate" and str(entry.fm.get("type")) != "source":
        return landing_text(body)
    return pages.normalize(body)


def write_up(root, rel) -> str:
    """The text of an edit's write-up (a verdict's `body_file`, a read-back's `--body`): a file
    named relative to the root that must lie inside it."""
    base = Path(root).resolve()
    path = (base / str(rel)).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"{rel}: a body file must lie inside the space")
    if not path.is_file():
        raise ValueError(f"{rel}: no such body file")
    return path.read_text(encoding="utf-8-sig")


def _refusal(entry: Found, verb: str, note: str, pin: dict, require_pin: bool) -> str:
    """Why a verdict may not sign this item, or "" when it may."""
    if verb not in PINNED_VERBS or not _pinned(entry):
        return ""
    fingerprint, base = pin.get("fingerprint"), pin.get("base")
    if not (fingerprint and base):   # a pin missing either half is a missing pin
        return "no fingerprint: read it back first" if (require_pin or fingerprint or base) else ""
    if base != base_of(entry.fm, entry.body):
        return "the draft changed since the read-back: read it back again"
    if fingerprint != pages.fingerprint(_text_of(entry, note if verb == "edit" else None)):
        return "the text changed since the read-back"
    return ""


def read_back(root, selection, bodies: dict | None = None) -> str:
    """The exact text that would land for each item, its fingerprint, and where it goes.
    `bodies` maps an id to its edit's write-up: that text is shown instead, marked as the edit.
    A first-release line lists the items it stands for; a bare verb answers it, so it has no
    fingerprint."""
    root = Path(root)
    found = index(root)
    bodies = bodies or {}
    parts = []
    for item_id in selection:
        entry = found.get(str(item_id))
        if entry is None:
            parts.append(f"## {item_id}: not found\n")
            continue
        edit = bodies.get(str(item_id))
        given = bool(edit and edit.strip())
        edited = given and _takes_edit(entry)
        # a check or a doubt item: its edit is a note, never its text
        unused = ["the write-up is not used for this item"] if given and not edited else []
        if entry.kind == "first_release":   # as its Needs-you line: the first N items since ...
            n, sid = len(entry.members), str(item_id).removeprefix("first-")
            lines = [f"## {item_id}: the first {n} item{'s' if n != 1 else ''} since {sid} was classified "
                     f"({entry.kind})", *unused]
            for member in entry.members:
                mid, where = member.fm.get("id") or member.path.stem, member.fm.get("destination")
                lines.append(f"- {mid}: {member.fm.get('title') or mid}"
                             + (f" (it will go to {where})" if where else ""))
            parts.append("\n".join(lines) + "\n")
            continue
        title = entry.fm.get("title") or (entry.item.text if entry.item else item_id)
        lines = [f"## {item_id}: {title} ({entry.kind})" + (" (your edit)" if edited else ""), *unused]
        destination = entry.fm.get("destination")
        if destination:
            lines.append(f"it will go to {destination}")
        if entry.path is not None:
            body = _text_of(entry, edit if edited else None)
            if body != pages.normalize(edit if edited else entry.body):
                lines.append("the New evidence list is for your review and does not land")
            fingerprint = pages.fingerprint(body)
        else:
            body, fingerprint = entry.item.text, entry.item.fingerprint
        lines.append(f"fingerprint: {fingerprint}")
        if _pinned(entry):
            lines.append(f"base: {base_of(entry.fm, entry.body)}")
        lines += ["", pages.normalize(body) if body.strip() else "(no text)\n"]
        parts.append("\n".join(lines))
    return "\n".join(parts)


# ----- small writers ---------------------------------------------------------------
def _append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    prefix = "" if (not text or text.endswith("\n")) else "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(prefix + line + "\n")


def _log(root: Path, line: str) -> None:
    _append(root / "log.md", line)


def _add_standing_choice(space: spaceyaml.Space, destination, kind, report: ApplyReport) -> None:
    if not destination or destination == space.name:
        return
    if kind == "call":
        return  # a call never travels under a standing choice
    kind = kind if kind in spaceyaml.RECORD_KINDS else "mail"
    choice = {"destination": str(destination), "kind": kind}
    if choice in space.standing_choices:
        return
    space.data["standing_choices"] = space.standing_choices + [choice]
    spaceyaml.save(space)
    report.standing_choices_added.append(choice)


def _home_of(root: Path, space: spaceyaml.Space, subject) -> str | None:
    if not subject:
        return None
    try:
        return registry_mod.Registry(root, space).home_of(str(subject))
    except Exception:
        return None


def target_of(fm: dict, path: Path, now: dt.datetime) -> str:
    """Where a candidate lands: its `target`, else derived from kind, subject and type."""
    if fm.get("target"):
        return str(fm["target"])
    pid = str(fm.get("id") or path.stem)
    kind = fm.get("kind") or "topics"
    if fm.get("released_from"):
        if fm.get("type") == "source":
            gen = fm.get("generated") if isinstance(fm.get("generated"), dict) else {}
            at = pages.parse_iso(gen.get("at")) if gen.get("at") else now
            return f"knowledge/sources/{at:%Y}/{at:%m}/{pid}.md"
        return f"knowledge/{kind}/{fm.get('subject') or pid}/{pid}.md"
    if fm.get("type") == "decision" or kind == "decisions":
        return f"knowledge/decisions/{path.stem}.md"
    return f"knowledge/{kind}/{fm.get('subject') or path.stem}/{path.stem}.md"


def landing_text(body: str) -> str:
    """What a signed candidate lands as, here and in any release: its text without the
    tool-kept `## New evidence` list. The page's own `[src: ...]` markers stay."""
    return pages.normalize(distill.without_evidence(body))


# ----- one handler per item kind ---------------------------------------------------
def _own_repo(root: Path) -> str | None:
    try:
        import onboard
        return onboard.git_remote(root)
    except Exception:
        return None


def provenance_kept(old_fm: dict, fm: dict) -> dict:
    """What a page already at the target keeps of its provenance when a draft is signed over it.
    Every record in its `evidence` stays, after the draft's own, unless the draft sets that record
    aside; its `set_aside` entries stay unless the draft cites the record again; its `high_stakes`
    flag stays unless the draft names one of its own, so the release still waits the window."""
    aside = distill.set_aside_of(fm)
    out = {str(item["id"]) for item in aside}
    evidence = list(dict.fromkeys(str(r) for r in fm.get("evidence") or []))
    evidence += [r for r in dict.fromkeys(str(r) for r in old_fm.get("evidence") or [])
                 if r not in evidence and r not in out]
    aside += [item for item in distill.set_aside_of(old_fm)
              if str(item["id"]) not in out and str(item["id"]) not in evidence]
    kept = {"evidence": evidence} if evidence else {}
    if aside:
        kept["set_aside"] = aside
    stakes = fm.get("high_stakes") or old_fm.get("high_stakes")
    if stakes:
        kept["high_stakes"] = stakes
    return kept


def _high_stakes(space: spaceyaml.Space, fm: dict, body: str) -> bool:
    """Flagged by the router or the person who released it; no word decides."""
    return bool(fm.get("high_stakes"))


def _file_evidence(root, space, fm: dict, body: str, path: Path, item_id, by, now, report) -> None:
    """A record is evidence: filed as a witness page, never signed (a stable page under
    `knowledge/sources/` is invalid)."""
    when = now
    for key in ("at", "released_at"):
        try:
            when = pages.parse_iso(fm.get(key))
            break
        except (TypeError, ValueError):
            continue
    target = root / "knowledge" / "sources" / f"{when:%Y}" / f"{when:%m}" / f"{fm.get('id') or path.stem}.md"
    page_fm = {k: v for k, v in fm.items() if k not in CANDIDATE_ONLY}
    page_fm.update(status="draft", filed_by=by, filed_at=pages.iso(now))
    pages.write(target, page_fm, body)
    path.unlink()
    _log(root, f"- {pages.iso(now)} filed record {item_id} as evidence by {by}")
    report.verdicts_recorded += 1


def _candidate(root, space, entry: Found, item_id, verb, note, by, now, report: ApplyReport):
    fm, body, path = dict(entry.fm), entry.body, entry.path
    if str(fm.get("type")) == "source" and verb in ("yes", "edit", "always"):
        _file_evidence(root, space, fm, body, path, item_id, by, now, report)
        return
    if verb == "later":
        fm["deadline"] = pages.iso(now + space.scale(24))
        pages.write(path, fm, body)
        report.deferred.append(item_id)
        return
    if verb == "no":
        _log(root, f"- {pages.iso(now)} discarded {item_id} by {by}: {note or 'no reason given'}")
        path.unlink()
        report.discarded.append(item_id)
        return
    if verb == "edit" and note.strip():
        body = note
        report.edited.append(item_id)
        if path.name.startswith("lesson-"):   # a lesson's title and page name are its text: the edit covers them
            first = note.strip().splitlines()[0]
            fm.update(title=first[:80], target=f"knowledge/topics/lessons/{distill.slugify(first)}.md")
    body = landing_text(body)
    fingerprint = pages.fingerprint(body)
    target = root / target_of(fm, path, now)
    if target.exists():   # an edit of a page already here: its id is permanent (shape-1.md, Pages)
        old_fm, _ = pages.read(target)
        fm["id"] = old_fm.get("id") or fm.get("id")
        fm.update({k: old_fm[k] for k in KEPT_ON_RESIGN if k in old_fm and k not in fm})
        fm.update(provenance_kept(old_fm, fm))
    signature = {"status": "stable", "signed_by": by, "signed_at": pages.iso(now),
                 "signed_in": space.name, "fingerprint": fingerprint}
    repo = _own_repo(root)
    if repo:
        signature["signed_repo"] = repo   # where another space's landing checks this signature
    page_fm = {k: v for k, v in fm.items() if k not in CANDIDATE_ONLY}
    page_fm.update(signature)
    pages.write(target, page_fm, body)
    path.unlink()
    # the trust root: this space's own history names what was signed, by whom, and its text
    _log(root, f"- {pages.iso(now)} fingerprint {fm.get('id') or item_id} {fingerprint} by {by}")
    report.signed.append(item_id)
    destination = fm.get("destination")
    if destination:
        qid = str(fm.get("id") or path.stem)
        stakes = _high_stakes(space, fm, body)  # a signature never skips the window for these
        queue_fm = {"id": qid, "type": fm.get("type") or "entity", "title": fm.get("title") or qid,
                    "generated": pages.generated("sign", now), "class": "window" if stakes else "at_once",
                    "destination": str(destination), "subject": fm.get("subject"),
                    "stage_after": pages.iso(now + space.window() if stakes else now), "listed": False,
                    "high_stakes": fm.get("high_stakes") or None, "path": f"approved:{by}", "approved_by": by,
                    "evidence": list(fm.get("evidence") or []), "from_space": space.name,
                    "kind": fm.get("kind"), "page": target.relative_to(root).as_posix()}
        if str(fm.get("target") or "").startswith("knowledge/topics/lessons/"):   # one page per lesson there too
            queue_fm["target"] = fm["target"]
        queue_fm.update(signature)
        pages.write(root / needs_you.QUEUE / f"{qid}.md", queue_fm, body)
        report.released_queued.append(qid)
    if verb == "always":
        _add_standing_choice(space, destination or _home_of(root, space, fm.get("subject")),
                             fm.get("kind") or "mail", report)


def _check(root, space, entry: Found, item_id, verb, note, by, now, report: ApplyReport):
    fm, body, path = dict(entry.fm), entry.body, entry.path
    if verb == "later":
        fm["deadline"] = pages.iso(now + space.scale(24))
        pages.write(path, fm, body)
        report.deferred.append(item_id)
        return
    _append(root / CHECK_VERDICTS, json.dumps({"check_id": item_id, "verdict": CHECK_VERDICT_OF[verb],
                                               "by": by, "at": pages.iso(now)}))
    report.verdicts_recorded += 1
    if verb == "edit":
        fm["note"], fm["noted_by"], fm["noted_at"] = note, by, pages.iso(now)
        pages.write(path, fm, body)
        report.edited.append(item_id)
    else:
        path.unlink()


def _queue_item(root, space, entry: Found, item_id, verb, note, by, now, report: ApplyReport):
    fm, body, path = dict(entry.fm), entry.body, entry.path
    if verb == "no":
        fm.update(reason="pulled", why=note or f"pulled by {by}", pulled_at=pages.iso(now))
        pages.write(root / needs_you.HELD / path.name, fm, body)
        path.unlink()
        if fm.get("path") and not str(fm["path"]).startswith("approved:"):
            import release   # the path that let it through pauses for four weeks
            release.pause(root, fm["path"], now, "pulled back", item_id, space)
        report.discarded.append(item_id)
        return
    if verb == "later":
        fm["stage_after"], fm["listed"] = pages.iso(now + space.scale(24)), False
        pages.write(path, fm, body)
        report.deferred.append(item_id)
        return
    if verb == "edit" and note.strip():
        body = pages.normalize(note)
        if fm.get("fingerprint"):  # a signed body changed: it is signed again, by this person
            fm.update(signed_by=by, signed_at=pages.iso(now), signed_in=space.name,
                      fingerprint=pages.fingerprint(body))
        report.edited.append(item_id)
    if _high_stakes(space, fm, body):
        # the holder's yes is recorded, but a high-stakes item still waits its window
        fm.update(approved_by=by, approved_at=pages.iso(now), path=f"approved:{by}")
    else:
        fm.update(stage_after=pages.iso(now), listed=True, listed_at=pages.iso(now), cls_was=fm.get("class"),
                  approved_by=by, approved_at=pages.iso(now), path=f"approved:{by}")
        fm["class"] = "at_once"
    pages.write(path, fm, body)
    report.signed.append(item_id)
    if verb == "always":
        _add_standing_choice(space, fm.get("destination"), fm.get("kind"), report)


def _first_release(root, space, entry: Found, item_id, verb, note, by, now, report: ApplyReport):
    """The one line of a subject's first release: the verdict applies to each of its items."""
    for member in entry.members:
        if member.path.exists():
            _queue_item(root, space, member, str(member.fm.get("id") or member.path.stem),
                        "yes" if verb == "edit" else verb, note, by, now, report)


def _tracker_item(root, space, entry: Found, item_id, verb, note, by, now, report: ApplyReport):
    """A tracker request at `propose`: yes lets the tracker routine create it, no declines it."""
    fm, body, path = dict(entry.fm), entry.body, entry.path
    if verb == "later":
        report.deferred.append(item_id)
        return
    if verb == "no":
        fm.update(status="declined", declined_by=by, declined_at=pages.iso(now))
        report.discarded.append(item_id)
    else:
        if verb == "edit" and note.strip():
            body = pages.normalize(note)
        fm.update(approved_by=by, approved_at=pages.iso(now))
        report.signed.append(item_id)
    pages.write(path, fm, body)


def _doubt(root, space, entry: Found, item_id, verb, note, by, now, report: ApplyReport):
    """A page held in doubt: yes releases it (the holder approved it, so at once unless it is
    high-stakes), no keeps it home for good, always also adds the standing choice."""
    fm, body, path = dict(entry.fm), entry.body, entry.path
    if verb == "later":
        report.deferred.append(item_id)
        return
    if verb == "no":
        fm.update(kept=True, kept_by=by, kept_at=pages.iso(now))
        pages.write(path, fm, body)
        _log(root, f"- {pages.iso(now)} kept {item_id} home by {by}")
        report.discarded.append(item_id)
        return
    if verb == "edit" and note.strip() and not fm.get("destination"):
        subject = note.strip().split()[0]   # "edit: harbor" names the one subject it is about
        fm["subject"], fm["destination"] = subject, _home_of(root, space, subject)
    if not fm.get("destination"):
        report.skipped.append(item_id)   # several subjects: name the one with edit
        return
    stakes = _high_stakes(space, fm, body)
    fm.update({"class": "window" if stakes else "at_once", "approved_by": by, "approved_at": pages.iso(now),
               "path": f"approved:{by}", "stage_after": pages.iso(now + space.window() if stakes else now),
               "listed": False})
    for key in ("reason", "why", "recommendation", "default"):
        fm.pop(key, None)
    pages.write(root / needs_you.QUEUE / path.name, fm, body)
    path.unlink()
    _log(root, f"- {pages.iso(now)} released {item_id} from doubt by {by}")
    report.signed.append(item_id)
    if verb == "always":
        _add_standing_choice(space, fm.get("destination"), fm.get("kind"), report)


def _question(root, space, entry: Found, item_id, verb, note, by, now, report: ApplyReport):
    if entry.path is None:  # a question a routine wrote into Needs you: it has no page
        return _routine_item(root, space, entry, item_id, verb, note, by, now, report)
    fm, body, path = dict(entry.fm), entry.body, entry.path
    if verb == "later":
        fm["deadline"] = pages.iso(now + space.scale(24))
        pages.write(path, fm, body)
        report.deferred.append(item_id)
        return
    if path.name.startswith("route-") and fm.get("record") and verb in ("yes", "always", "no"):
        import route   # a routing question: the owner's word applies the route or keeps it private
        try:
            route.answer(root, fm["record"], yes=verb != "no", by=by, now=now)
        except route.RouteError as exc:
            _log(root, f"- {pages.iso(now)} routing answer for {item_id} by {by} not applied: {exc}")
            report.deferred.append(item_id)
            return
        _log(root, f"- {pages.iso(now)} routing answer {verb} for {fm['record']} by {by}")
        report.verdicts_recorded += 1
        return
    if path.name.startswith("librarian-") and verb in ("yes", "always"):
        import librarian   # an ownership question: the owner's yes makes the asked change
        key = path.stem[len("librarian-"):]
        try:
            outcome = librarian.answer(root, key, by=by, now=now)
            _log(root, f"- {pages.iso(now)} ownership answer yes for {key} by {by}: {outcome}")
        except (ValueError, OSError, yaml.YAMLError) as exc:
            _log(root, f"- {pages.iso(now)} ownership answer for {item_id} by {by} could not be applied: {exc}")
    if note.strip():
        _log(root, f"- {pages.iso(now)} note {item_id} by {by}: {' '.join(note.split())}")
    path.unlink()
    report.verdicts_recorded += 1


def _routine_item(root, space, entry: Found, item_id, verb, note, by, now, report: ApplyReport):
    """An item a routine wrote: the verdict line in log.md is the answer it reads back."""
    if verb == "later":
        report.deferred.append(item_id)
        return
    if note.strip():
        _log(root, f"- {pages.iso(now)} note {item_id} by {by}: {' '.join(note.split())}")
    report.verdicts_recorded += 1


# ----- the weekly review batch: verdicts per line --------------------------------------
def _review_line(root: Path, space, batch: Path, number: int, verb: str, note: str, by: str, now,
                 report: ApplyReport, require_pin: bool = False) -> str:
    """One line of a weekly review batch: "answered", "refused", or "" when the batch has no
    such open line. Under the pin, a line that would sign a draft is refused and stays open:
    the draft is signed by its own id, after its read-back."""
    fm, body = pages.read(batch)
    lines = fm.get("lines") or []
    line = next((l for l in lines if int(l.get("n", 0)) == number), None)
    if line is None or line.get("answered"):
        return ""
    if verb == "later":
        report.deferred.append(f"{batch.stem}#{number}")
        return "answered"
    target = root / str(line["page"])
    draft = (Found("candidate", target, *pages.read(target))
             if str(line["page"]).startswith("_inbound/candidates/") and target.exists() else None)
    if require_pin and verb in PINNED_VERBS and draft is not None and _pinned(draft):
        why = "no fingerprint: read it back first"
        _log(root, f"- {pages.iso(now)} refused {batch.stem}#{number} by {by}: {why}")
        report.refused.append({"id": f"{batch.stem}#{number}", "why": why})
        return "refused"
    if not target.exists():
        line["answered"] = "gone"
    elif draft is not None:
        _candidate(root, space, draft, str(line.get("id") or target.stem),
                   "yes" if verb in ("yes", "always") else verb, note, by, now, report)
        line["answered"] = verb
    else:
        pfm, pbody = pages.read(target)
        if verb in ("yes", "always"):
            pfm.update(verified_at=pages.iso(now), verified_by=by)
            pfm.pop("contested", None)
            report.signed.append(f"{batch.stem}#{number}")
        elif verb == "edit" and note.strip():
            pbody = pages.normalize(note)
            pfm.update(status="stable", signed_by=by, signed_at=pages.iso(now), signed_in=space.name,
                       fingerprint=pages.fingerprint(pbody), verified_at=pages.iso(now), verified_by=by)
            pfm.pop("contested", None)
            report.edited.append(f"{batch.stem}#{number}")
        elif verb == "no":
            pfm.update(status="deprecated", deprecated_at=pages.iso(now), deprecated_by=by)
            report.discarded.append(f"{batch.stem}#{number}")
        pages.write(target, pfm, pbody)
        line["answered"] = verb
    if all(l.get("answered") for l in lines):
        _log(root, f"- {pages.iso(now)} review batch {batch.stem} answered in full by {by}")
        batch.unlink()
    else:
        pages.write(batch, dict(fm, lines=lines), body)
    return "answered"


HANDLERS = {"candidate": _candidate, "check": _check, "leaving": _queue_item, "doubt": _doubt, "tracker": _tracker_item,
            "high_stakes": _queue_item, "question": _question, "answer": _question,
            "first_release": _first_release}


def _verb(value) -> str:
    """A verb as typed; YAML reads a bare `yes` or `no` as a boolean, so those come back."""
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return str(value if value is not None else "").strip().lower()


def _split(verdict) -> tuple:
    """(verb, note, pin) from a verb, a [verb, note] pair, or a mapping; only a mapping
    carries a pin: the read-back's `fingerprint` and `base`, and an edit's `body_file`."""
    if isinstance(verdict, (str, bool)):
        return _verb(verdict), "", {}
    if isinstance(verdict, (list, tuple)) and verdict:
        return _verb(verdict[0]), str(verdict[1]) if len(verdict) > 1 and verdict[1] else "", {}
    if isinstance(verdict, dict):
        pin = {key: str(verdict[key]) for key in ("fingerprint", "base", "body_file")
               if verdict.get(key) not in (None, "")}
        return _verb(verdict.get("verb")), str(verdict.get("note") or ""), pin
    return "", "", {}


def apply(root, verdicts: dict, by: str, now: dt.datetime | None = None,
          require_pin: bool = False) -> ApplyReport:
    """Record a named person's verdicts. Refuses routines, bad verbs and bad body files
    before any write. A yes, edit or always on a pinned page signs only the text that was read
    back; with `require_pin`, one that names no fingerprint is refused too."""
    actor = str(by or "").strip()
    if not actor or actor.lower().startswith("routine:"):
        raise ValueError("sign.apply needs a named person as `by`; routines never sign "
                         "(rules.md section 1, hard rule 3)")
    root = Path(root)
    wanted = {}
    for item_id, verdict in (verdicts or {}).items():
        verb, note, pin = _split(verdict)
        if verb not in VERBS:
            raise ValueError(f"{item_id}: the verdict must be one of {', '.join(VERBS)} (got {verb!r})")
        if "body_file" in pin:
            if verb != "edit":
                raise ValueError(f"{item_id}: a body_file goes with edit only (got {verb!r})")
            if note:
                raise ValueError(f"{item_id}: give the edit as a note or a body_file, not both")
            note = write_up(root, pin["body_file"])
        wanted[str(item_id)] = (verb, note, pin)
    space = spaceyaml.load(root)
    now = now or space.now()
    found = index(root)
    report = ApplyReport()
    for item_id, (verb, note, pin) in wanted.items():
        if "#" in item_id:  # one line of a weekly review batch: `review-<date>#<n>`
            stem, _, number = item_id.partition("#")
            batch = root / needs_you.CANDIDATES / f"{stem}.md"
            outcome = (_review_line(root, space, batch, int(number), verb, note, actor, now, report,
                                    require_pin) if batch.exists() and number.isdigit() else "")
            if outcome == "answered":
                _log(root, f"- {pages.iso(now)} {verb} {item_id} by {actor}")
            elif not outcome:
                report.skipped.append(item_id)
            continue
        entry = found.get(item_id)
        if entry is not None and entry.kind == "candidate" and entry.fm.get("review_batch") and verb != "later":
            # the whole batch at once: the verb applies to every open line
            outcomes = [_review_line(root, space, entry.path, int(line["n"]), verb, note, actor, now, report,
                                     require_pin)
                        for line in list(entry.fm.get("lines") or [])
                        if not line.get("answered") and entry.path.exists()]
            if "answered" in outcomes or "refused" not in outcomes:   # not when every line was refused
                _log(root, f"- {pages.iso(now)} {verb} {item_id} by {actor}")
            continue
        if entry is None and verb == "no":
            # an item that already left (listed under "left yesterday"): pulled back with one word
            import release
            if release.released_record(root, item_id) is not None:
                release.retract(root, item_id, now=now)
                report.retracted.append(item_id)
                _log(root, f"- {pages.iso(now)} {verb} {item_id} by {actor}")
                continue
        if entry is None:
            report.skipped.append(item_id)
            continue
        why = _refusal(entry, verb, note, pin, require_pin)
        if why:   # not what was read back: it stays where it is, still on Needs you
            _log(root, f"- {pages.iso(now)} refused {item_id} by {actor}: {why}")
            report.refused.append({"id": item_id, "why": why})
            continue
        HANDLERS.get(entry.kind, _routine_item)(root, space, entry, item_id, verb, note, actor, now, report)
        _log(root, f"- {pages.iso(now)} {verb} {item_id} by {actor}")
    return report


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(".")
    if argv and argv[0] not in ("read-back", "apply") and not argv[0].startswith("-"):
        root = Path(argv.pop(0))
    if not argv:
        print(__doc__)
        return 2
    command = argv.pop(0)
    if command == "read-back":
        ap = argparse.ArgumentParser(prog="sign.py read-back")
        ap.add_argument("ids", nargs="*", help="the items to read back")
        ap.add_argument("--body", action="append", default=[], metavar="ID=FILE",
                        help="an edit's write-up, relative to the root and inside it "
                             "(_scratch/sign/<id>.md): shown and fingerprinted instead")
        args = ap.parse_intermixed_args(argv)
        bodies = {}
        try:
            for pair in args.body:
                item_id, sep, rel = pair.partition("=")
                if not sep or not item_id or not rel:
                    raise ValueError(f"{pair}: --body takes <id>=<file>")
                bodies[item_id] = write_up(root, rel)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(read_back(root, args.ids, bodies=bodies), end="")
        return 0
    if command != "apply":
        print(__doc__)
        return 2
    ap = argparse.ArgumentParser(prog="sign.py apply")
    ap.add_argument("--by", required=True, help="the person whose verdicts these are")
    ap.add_argument("--verdicts", required=True,
                    help="YAML file: {item_id: verb | [verb, note] | {verb, fingerprint, base} | "
                         "{verb: edit, body_file, fingerprint, base}}; a yes, edit or always on a "
                         "page names the fingerprint and base its read-back printed")
    args = ap.parse_args(argv)
    data = yaml.safe_load(Path(args.verdicts).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        print("error: the verdicts file must map item ids to verdicts", file=sys.stderr)
        return 1
    try:
        report = apply(root, data, by=args.by, require_pin=True)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
