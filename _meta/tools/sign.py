"""Sign: the one verb over any Needs-you item (shape 1).

A person answers yes, edit, no, later or always; `read_back` shows the exact text and
fingerprint of what would land and where; `apply` records the verdicts. This is the only
function in the template that writes `status: stable`, and only while executing a named
person's verdict: an empty `by`, or one that starts with `routine:`, is refused before
anything is touched (rules.md section 1, hard rule 3). Nothing here sends or runs git.

    python sign.py [root] read-back <ids...>
    python sign.py [root] apply --by <name> --verdicts <verdicts.yaml>

The verdicts file maps item ids to a verb or a [verb, note] pair:
    p-1a2b3c4d: yes
    p-5e6f7a8b: [edit, "Go-live is 9 November."]

Per kind: a candidate lands at its `target` signed (yes), with the note as its body
(edit), is discarded with a tombstone (no), gets a deadline one scaled day out (later),
or lands and adds a standing choice for its destination (always); a signed candidate
with a `destination` also queues an `at_once` release carrying the signature. A check
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
import needs_you  # noqa: E402
import pages  # noqa: E402
import registry as registry_mod  # noqa: E402
import spaceyaml  # noqa: E402

VERBS = ("yes", "edit", "no", "later", "always")
CANDIDATE_ONLY = ("destination", "target", "deadline", "recommendation", "depends")
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


@dataclass
class Found:
    kind: str
    path: Path | None
    fm: dict
    body: str
    item: needs_you.Item | None = None  # a routine-written item with no file of its own


# ----- finding items -------------------------------------------------------------
def index(root: Path) -> dict:
    """Every item a verdict can name, by its id and by its file stem."""
    found: dict = {}

    def add(kind, rel, fm, body):
        path = root / rel
        entry = Found(kind, path, fm, body)
        for key in (str(fm.get("id") or ""), path.stem):
            if key:
                found.setdefault(key, entry)

    for folder, kind in ((needs_you.CANDIDATES, "candidate"), (needs_you.CHECKS, "check"),
                         (needs_you.QUESTIONS, "question"), (needs_you.ANSWERS, "answer")):
        for rel, fm, body in needs_you.pages_in(root, folder):
            add(kind, rel, fm, body)
    for rel, fm, body in needs_you.pages_in(root, needs_you.QUEUE):
        add("high_stakes" if fm.get("high_stakes") else "leaving", rel, fm, body)
    for rel, fm, body in needs_you.doubt_pages(root):
        add("doubt", rel, fm, body)
    try:
        import tracker
        for path, fm, body in tracker.waiting_for_a_person(root):
            add("tracker", Path(path).relative_to(root), fm, body)
    except Exception:
        pass
    for item in needs_you.routine_items(root):
        found.setdefault(item.id, Found(item.kind, None, {}, "", item=item))
    return found


def read_back(root, selection) -> str:
    """The exact text that would land for each item, its fingerprint, and where it goes."""
    root = Path(root)
    found = index(root)
    parts = []
    for item_id in selection:
        entry = found.get(str(item_id))
        if entry is None:
            parts.append(f"## {item_id}: not found\n")
            continue
        title = entry.fm.get("title") or (entry.item.text if entry.item else item_id)
        lines = [f"## {item_id}: {title} ({entry.kind})"]
        destination = entry.fm.get("destination")
        if destination:
            lines.append(f"it will go to {destination}")
        if entry.path is not None:
            body, fingerprint = entry.body, pages.fingerprint(entry.body)
        else:
            body, fingerprint = entry.item.text, entry.item.fingerprint
        lines.append(f"fingerprint: {fingerprint}")
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


# ----- one handler per item kind ---------------------------------------------------
def _own_repo(root: Path) -> str | None:
    try:
        import onboard
        return onboard.git_remote(root)
    except Exception:
        return None


def _high_stakes(space: spaceyaml.Space, fm: dict, body: str) -> bool:
    import release
    return bool(fm.get("high_stakes")) or release.first_term(
        f"{fm.get('title', '')}\n{body}", space.high_stakes) is not None


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
    body = pages.normalize(body)
    fingerprint = pages.fingerprint(body)
    target = root / target_of(fm, path, now)
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
                    "high_stakes": stakes or None, "path": f"approved:{by}", "approved_by": by,
                    "evidence": list(fm.get("evidence") or []), "from_space": space.name,
                    "kind": fm.get("kind"), "page": target.relative_to(root).as_posix()}
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
                 report: ApplyReport) -> bool:
    fm, body = pages.read(batch)
    lines = fm.get("lines") or []
    line = next((l for l in lines if int(l.get("n", 0)) == number), None)
    if line is None or line.get("answered"):
        return False
    if verb == "later":
        report.deferred.append(f"{batch.stem}#{number}")
        return True
    target = root / str(line["page"])
    if not target.exists():
        line["answered"] = "gone"
    elif str(line["page"]).startswith("_inbound/candidates/"):
        entry = Found("candidate", target, *pages.read(target))
        _candidate(root, space, entry, str(line.get("id") or target.stem),
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
    return True


HANDLERS = {"candidate": _candidate, "check": _check, "leaving": _queue_item, "doubt": _doubt, "tracker": _tracker_item,
            "high_stakes": _queue_item, "question": _question, "answer": _question}


def _verb(value) -> str:
    """A verb as typed; YAML reads a bare `yes` or `no` as a boolean, so those come back."""
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return str(value if value is not None else "").strip().lower()


def _split(verdict) -> tuple:
    if isinstance(verdict, (str, bool)):
        return _verb(verdict), ""
    if isinstance(verdict, (list, tuple)) and verdict:
        return _verb(verdict[0]), str(verdict[1]) if len(verdict) > 1 and verdict[1] else ""
    if isinstance(verdict, dict):
        return _verb(verdict.get("verb")), str(verdict.get("note") or "")
    return "", ""


def apply(root, verdicts: dict, by: str, now: dt.datetime | None = None) -> ApplyReport:
    """Record a named person's verdicts. Refuses routines and bad verbs before any write."""
    actor = str(by or "").strip()
    if not actor or actor.lower().startswith("routine:"):
        raise ValueError("sign.apply needs a named person as `by`; routines never sign "
                         "(rules.md section 1, hard rule 3)")
    wanted = {}
    for item_id, verdict in (verdicts or {}).items():
        verb, note = _split(verdict)
        if verb not in VERBS:
            raise ValueError(f"{item_id}: the verdict must be one of {', '.join(VERBS)} (got {verb!r})")
        wanted[str(item_id)] = (verb, note)
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    found = index(root)
    report = ApplyReport()
    for item_id, (verb, note) in wanted.items():
        if "#" in item_id:  # one line of a weekly review batch: `review-<date>#<n>`
            stem, _, number = item_id.partition("#")
            batch = root / needs_you.CANDIDATES / f"{stem}.md"
            if batch.exists() and number.isdigit() and _review_line(
                    root, space, batch, int(number), verb, note, actor, now, report):
                _log(root, f"- {pages.iso(now)} {verb} {item_id} by {actor}")
                continue
            report.skipped.append(item_id)
            continue
        entry = found.get(item_id)
        if entry is not None and entry.kind == "candidate" and entry.fm.get("review_batch") and verb != "later":
            # the whole batch at once: the verb applies to every open line
            for line in list(entry.fm.get("lines") or []):
                if not line.get("answered") and entry.path.exists():
                    _review_line(root, space, entry.path, int(line["n"]), verb, note, actor, now, report)
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
        print(read_back(root, argv), end="")
        return 0
    if command != "apply":
        print(__doc__)
        return 2
    ap = argparse.ArgumentParser(prog="sign.py apply")
    ap.add_argument("--by", required=True, help="the person whose verdicts these are")
    ap.add_argument("--verdicts", required=True, help="YAML file: {item_id: verb | [verb, note]}")
    args = ap.parse_args(argv)
    data = yaml.safe_load(Path(args.verdicts).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        print("error: the verdicts file must map item ids to verdicts", file=sys.stderr)
        return 1
    try:
        report = apply(root, data, by=args.by)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
