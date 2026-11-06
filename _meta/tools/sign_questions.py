"""Sign questions: the ledger of questions the sign flow puts to the person (shape 1).

`_meta/state/sign-questions.jsonl` is append only and merged by union across machines
(rules.md section 1, hard rule 5). This module is its only reader and writer; no code
rewrites or truncates it. It is not `_meta/questions.log`, which logs the person's own questions.

One JSON object per line, one event each: {"at", "event", "id", "by"} plus, per event,
`asked` (subject, page, text, why, recommendation, searched, sensitive), `reworded` (text,
optional recommendation), `settled` (source), `answered` (answer, or kept_in for a sensitive
question), `parked`, `dropped` and `reopened` (reason). `checked` (subject) has no id: it marks
when a subject's open questions were last looked over; the subject needs another look
(`needs_recheck`) until then, and again once a record arrives after it. Every line says who wrote
it; only a named person answers (an empty `by`, or one that starts with `routine:`, is refused).

A question is its events folded in order of `at` (at equal times an `asked` comes first, so a
merge never puts an answer ahead of its question; the rest keep file order). It is open
until it is settled by a record, answered, parked or dropped. Open and parked questions can be
settled, answered, dropped or reworded; open ones can be parked; an answered one can be
answered again, a correction (the later answer wins); settled and dropped ones can be
reopened. A new write that breaks this is refused. The fold also ignores a line that breaks it,
an `asked` for an id it already has, and any line that is not valid JSON, so lines a merge
duplicated or reordered, or a crash half wrote, change nothing and never reopen a question.

A question is asked once: the same subject and the same words (case and spacing aside), under
any text it has had, is the question that is already there. A subject holds at most four open
questions, from whatever pages they were asked. A sensitive question is answered with a
pointer to where the answer is kept (`kept_in`), never with the answer itself.

    python sign_questions.py [root] list <subject> [--state open|parked|all] [--json]
    python sign_questions.py [root] ask <subject> --page P --text T --why W --recommendation R
                                       [--searched S]... [--sensitive] --by NAME
    python sign_questions.py [root] reword <qid> --text T [--recommendation R] --by NAME
    python sign_questions.py [root] settle <qid> --source RECORD --by NAME
    python sign_questions.py [root] answer <qid> (--text T | --kept-in WHERE) --by NAME
    python sign_questions.py [root] park <qid> --by NAME
    python sign_questions.py [root] drop <qid> --reason WHY --by NAME
    python sign_questions.py [root] reopen <qid> --reason WHY --by NAME
    python sign_questions.py [root] checked <subject> --by NAME
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

LEDGER = Path("_meta") / "state" / "sign-questions.jsonl"
STATES = ("open", "settled", "answered", "parked", "dropped")
PER_SUBJECT = 4   # open questions one subject can hold
# the states an event may be applied from, and the state it leaves the question in
MOVES = {"reworded": ("open", "parked"), "settled": ("open", "parked"),
         "answered": ("open", "parked", "answered"), "parked": ("open",), "dropped": ("open", "parked"),
         "reopened": ("settled", "dropped")}
BECOMES = {"settled": "settled", "answered": "answered", "parked": "parked", "dropped": "dropped",
           "reopened": "open"}


class QuestionError(ValueError):
    """A write the ledger refuses: it names the reason in plain words."""


@dataclass
class Question:
    id: str
    subject: str
    page: str
    text: str
    texts: list        # every text it has had; the first one made the id
    why: str
    recommendation: str
    searched: list
    sensitive: bool
    status: str
    source: str = ""   # settled: the record that answers it
    by: str = ""       # who wrote the last line that changed it
    answer: str = ""   # answered: the answer (never for a sensitive question)
    kept_in: str = ""  # answered, sensitive: where the answer is kept
    reason: str = ""   # dropped or reopened: why
    asked_at: str = ""
    updated_at: str = ""


def _normal(text) -> str:
    return " ".join(str(text or "").lower().split())


def question_id(subject: str, text: str) -> str:
    return "q-" + hashlib.sha256(f"{subject}\n{_normal(text)}".encode("utf-8")).hexdigest()[:8]


# ----- reading: the ledger folded ---------------------------------------------------------
def _rows(root) -> list:
    """The ledger's usable lines as (moment, row), in order of `at`; at equal times an `asked`
    comes first and the rest keep file order. A line that is not a JSON object with an `event`
    and an `at` (a crash can leave half of one) is skipped."""
    path = Path(root) / LEDGER
    if not path.exists():
        return []
    rows = []
    # split on "\n" only: a JSON string may hold U+2028, which splitlines would cut in two
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        try:
            row = json.loads(line)
            moment = pages.parse_iso(row["at"])
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
        if isinstance(row, dict) and isinstance(row.get("event"), str):
            rows.append((moment, row))
    # an `asked` sorts first within its moment, so a merge can never put a line ahead of its question
    rows.sort(key=lambda item: (item[0], item[1]["event"] != "asked"))
    return rows


def _strings(value) -> list:
    return [str(item) for item in value] if isinstance(value, list) else []


def _fold(rows: list) -> dict:
    questions: dict = {}
    for moment, row in rows:
        kind, qid, stamp = row["event"], str(row.get("id") or ""), pages.iso(moment)
        if kind == "asked":
            if qid and qid not in questions:   # a second `asked` never reopens or resets
                text = str(row.get("text") or "")
                questions[qid] = Question(
                    id=qid, subject=str(row.get("subject") or ""), page=str(row.get("page") or ""),
                    text=text, texts=[text], why=str(row.get("why") or ""),
                    recommendation=str(row.get("recommendation") or ""), searched=_strings(row.get("searched")),
                    sensitive=bool(row.get("sensitive")), status="open", by=str(row.get("by") or ""),
                    asked_at=stamp, updated_at=stamp)
            continue
        q = questions.get(qid)
        if q is None or q.status not in MOVES.get(kind, ()):
            continue   # no such question, a move the table does not allow, or a line of another kind
        if kind == "reworded":
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            q.text = text
            if text not in q.texts:
                q.texts.append(text)
            if row.get("recommendation") is not None:
                q.recommendation = str(row["recommendation"])
        elif kind == "settled":
            q.source = str(row.get("source") or "")
        elif kind == "answered":
            q.answer, q.kept_in = str(row.get("answer") or ""), str(row.get("kept_in") or "")
        elif kind in ("dropped", "reopened"):
            q.reason = str(row.get("reason") or "")
            if kind == "reopened":
                q.source = ""   # open again: no record settles it
        q.status = BECOMES.get(kind, q.status)
        q.by, q.updated_at = str(row.get("by") or ""), stamp
    return questions


def load(root) -> dict:
    """Every question on the ledger by id, in the order it was asked."""
    return _fold(_rows(root))


def _pick(root, states: tuple, field: str, value: str) -> list:
    found = [q for q in load(root).values() if q.status in states and getattr(q, field) == value]
    return sorted(found, key=lambda q: q.asked_at)


def for_subject(root, subject: str, states: tuple = ("open",)) -> list:
    return _pick(root, states, "subject", subject)


def for_page(root, page: str, states: tuple = ("open",)) -> list:
    return _pick(root, states, "page", page)


def last_checked(root, subject: str) -> dt.datetime | None:
    """When `mark_checked` last ran for this subject, or None."""
    moments = [moment for moment, row in _rows(root)
               if row["event"] == "checked" and str(row.get("subject") or "") == subject]
    return max(moments) if moments else None


def needs_recheck(root, subject: str, grouped: dict | None = None) -> bool:
    """True when the subject was never checked, or has a record that arrived after its last
    check. A record counts as it does for `distill.run`. A caller that asks about many subjects
    reads the space once and passes `distill.evidence_by_subject(root)` as `grouped`."""
    since = last_checked(root, subject)
    if since is None:
        return True
    if grouped is None:
        import distill   # here and not at the top: distill imports this module
        grouped = distill.evidence_by_subject(root)
    return any(ev.arrived > since for ev in grouped.get(subject, []))


# ----- writing: one appended line per event ------------------------------------------------
def _append(root, event: dict) -> None:
    path = Path(root) / LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    glue = ""
    if path.exists() and path.stat().st_size:
        with path.open("rb") as handle:   # a crash can leave a last line with no newline: start a new one
            handle.seek(-1, 2)
            glue = "" if handle.read(1) == b"\n" else "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(glue + json.dumps(event, ensure_ascii=False) + "\n")


def _named(by, person: bool = False) -> str:
    who = str(by or "").strip()
    if not who:
        raise QuestionError("every line on the ledger says who wrote it: give `by`")
    if person and who.lower().startswith("routine:"):
        raise QuestionError("only a named person answers a question; a routine never does")
    return who


def _need(value, what: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise QuestionError(f"give the {what}")
    return text


def _get(root, qid: str) -> Question:
    q = load(root).get(str(qid))
    if q is None:
        raise QuestionError(f"there is no question {qid}")
    return q


def _change(root, qid: str, kind: str, by: str, now: dt.datetime, **fields) -> Question:
    """Append one event to a question, after the checks the fold makes: the move must be
    allowed, and the line must not be dated before the question's last change (the fold
    would put it first and ignore it)."""
    q = _get(root, qid)
    if q.status not in MOVES[kind]:
        raise QuestionError(f"{qid} is {q.status}, so it cannot be {kind}")
    stamp = pages.iso(now)
    if stamp < q.updated_at:
        raise QuestionError(f"{qid} last changed at {q.updated_at}, which is after {stamp}")
    _append(root, {"at": stamp, "event": kind, "id": q.id, "by": by, **fields})
    return _get(root, qid)


def ask(root, subject, page, text, why, recommendation, searched: list, by, now,
        sensitive: bool = False) -> Question:
    """Put a question on the ledger, or return the one already there (no new line) when the
    subject has a question that has ever had these words. Refused when the subject already has
    four open questions, whatever pages they were asked from."""
    root = Path(root)
    who = _named(by)
    subject, page, text = _need(subject, "subject"), _need(page, "page"), _need(text, "text")
    questions = load(root)
    for q in questions.values():
        if q.subject == subject and any(_normal(old) == _normal(text) for old in q.texts):
            return q
    if sum(1 for q in questions.values() if q.subject == subject and q.status == "open") >= PER_SUBJECT:
        raise QuestionError(f"{subject} already has {PER_SUBJECT} open questions: answer or park one first")
    qid = question_id(subject, text)
    _append(root, {"at": pages.iso(now), "event": "asked", "id": qid, "by": who, "subject": subject,
                   "page": page, "text": text, "why": str(why or ""), "recommendation": str(recommendation or ""),
                   "searched": [str(item) for item in searched or []], "sensitive": bool(sensitive)})
    return _get(root, qid)


def reword(root, qid, text, by, now, recommendation: str | None = None) -> Question:
    fields = {"text": _need(text, "new text")}
    if recommendation is not None:
        fields["recommendation"] = str(recommendation)
    return _change(root, qid, "reworded", _named(by), now, **fields)


def settle(root, qid, source: str, by, now) -> Question:
    return _change(root, qid, "settled", _named(by), now, source=_need(source, "record that settles it"))


def answer(root, qid, by, now, text: str = "", kept_in: str = "") -> Question:
    """A named person's answer, or their correction of one: the later answer wins. A sensitive
    question takes only `kept_in`, where the answer is kept; any other takes `text`. Refused inside a
    routine or a cloud run (`routine.in_routine`): an answer is a person's word, whatever `by` says."""
    import routine   # here and not at the top: only a person's answer asks it
    running = routine.in_routine()
    if running:
        raise QuestionError(f"an answer is a named person's, and {running}; a routine never answers")
    who = _named(by, person=True)
    if _get(root, qid).sensitive:
        if str(text or "").strip():
            raise QuestionError(f"{qid} is sensitive: keep the answer where it belongs and give `kept_in`")
        fields = {"kept_in": _need(kept_in, "place the answer is kept")}
    else:
        fields = {"answer": _need(text, "answer")}
        if str(kept_in or "").strip():
            fields["kept_in"] = str(kept_in).strip()
    return _change(root, qid, "answered", who, now, **fields)


def park(root, qid, by, now) -> Question:
    return _change(root, qid, "parked", _named(by), now)


def drop(root, qid, reason: str, by, now) -> Question:
    return _change(root, qid, "dropped", _named(by), now, reason=_need(reason, "reason"))


def reopen(root, qid, reason: str, by, now) -> Question:
    return _change(root, qid, "reopened", _named(by), now, reason=_need(reason, "reason"))


def mark_checked(root, subject: str, by, now) -> None:
    """Record that a subject's open questions were looked over at `now`."""
    _append(Path(root), {"at": pages.iso(now), "event": "checked", "by": _named(by),
                         "subject": _need(subject, "subject")})


# ----- command line ---------------------------------------------------------------------------
COMMANDS = ("list", "ask", "reword", "settle", "answer", "park", "drop", "reopen", "checked")


def main(argv=None) -> int:
    # a console that cannot show a character (cp1252 on Windows) must not fail a write that landed,
    # nor turn a question's own words into escape codes: UTF-8, escapes only for what still fails
    import needs_you   # here and not at the top: its helpers are the CLI's alone
    needs_you.utf8_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(".")
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        root = Path(argv.pop(0))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    show = sub.add_parser("list")
    show.add_argument("subject")
    show.add_argument("--state", choices=("open", "parked", "all"), default="open")
    show.add_argument("--json", action="store_true")
    new = sub.add_parser("ask")
    new.add_argument("subject")
    for flag in ("--page", "--text", "--why", "--recommendation"):
        new.add_argument(flag, required=True)
    new.add_argument("--searched", action="append", default=[])
    new.add_argument("--sensitive", action="store_true")
    rewrite = sub.add_parser("reword")
    rewrite.add_argument("qid")
    rewrite.add_argument("--text", required=True)
    rewrite.add_argument("--recommendation", default=None)
    settled = sub.add_parser("settle")
    settled.add_argument("qid")
    settled.add_argument("--source", required=True)
    given = sub.add_parser("answer")
    given.add_argument("qid")
    how = given.add_mutually_exclusive_group(required=True)
    how.add_argument("--text")
    how.add_argument("--kept-in", dest="kept_in")
    parked = sub.add_parser("park")
    parked.add_argument("qid")
    dropped = sub.add_parser("drop")
    dropped.add_argument("qid")
    dropped.add_argument("--reason", required=True)
    reopened = sub.add_parser("reopen")
    reopened.add_argument("qid")
    reopened.add_argument("--reason", required=True)
    checked = sub.add_parser("checked")
    checked.add_argument("subject")
    for writer in (new, rewrite, settled, given, parked, dropped, reopened, checked):
        writer.add_argument("--by", required=True, help="who writes this line")
    args = ap.parse_args(argv)
    try:
        if args.command == "list":
            states = STATES if args.state == "all" else (args.state,)
            found = for_subject(root, args.subject, states)
            if args.json:
                print(json.dumps([asdict(q) for q in found], indent=2, ensure_ascii=True))
            else:
                for q in found:
                    print(f"{q.id}  {q.status}  {q.page}  {q.text}")
            return 0
        now = spaceyaml.load(root).now()
        if args.command == "checked":
            mark_checked(root, args.subject, args.by, now)
            print(f"checked {args.subject} at {pages.iso(now)}")
            return 0
        if args.command == "ask":
            q = ask(root, args.subject, args.page, args.text, args.why, args.recommendation, args.searched,
                    args.by, now, sensitive=args.sensitive)
        elif args.command == "reword":
            q = reword(root, args.qid, args.text, args.by, now, recommendation=args.recommendation)
        elif args.command == "settle":
            q = settle(root, args.qid, args.source, args.by, now)
        elif args.command == "answer":
            q = answer(root, args.qid, args.by, now, text=args.text or "", kept_in=args.kept_in or "")
        elif args.command == "park":
            q = park(root, args.qid, args.by, now)
        elif args.command == "drop":
            q = drop(root, args.qid, args.reason, args.by, now)
        else:
            q = reopen(root, args.qid, args.reason, args.by, now)
    except QuestionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(q), indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
