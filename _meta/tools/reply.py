"""Answering by email reply: an adapter (vision section 14). A reply never signs by itself.

The person replies to their morning briefing ("1-4 yes, 5 edit: the date is the ninth,
6 later"). The mail sweep exports that reply like any mail; this tool finds it, maps its
numbers to the items of that morning's list (`_meta/state/morning/<date>.json`, written by the
morning view), and writes `_inbound/answers/reply-<date>.md`: the verdicts and the read-back of
every named item, listed on Needs you as one `answer`. The next session reads it back and asks
for "submit"; only then does `sign.py apply` run with those verdicts.

Anyone can write a mail that looks like a reply, so a mail is read as the person's only when
all of these hold (`from_the_owner`): its From address is exactly the space's `owner_email`
(parsed, compared ignoring case; a display name never counts), every To and Cc address is that
same address, its subject answers a briefing, it is not marked spam, and its text quotes the
reply reference of a briefing of the last few days (`morning_view.reference_of`). That reference
is random, is printed only in the mail the person sent themselves, and the space keeps only its
hash; the reply's numbers map to the list of the briefing it quotes. A mail that fails any of
these is marked seen as `rejected` and nothing of it is applied. What the mail provider proves
about the sender (SPF, DKIM) is outside this tool: it trusts the exported fields as given.

The owner's own mail is never ignored in silence: when its From is exactly the owner's address,
it is not marked spam and its own lines hold answers, but the reference line is missing, it
went to someone else too (a cc, or a forward) or it is a forward of the briefing, an owner
notice is queued (`owner_email.queue`, to the owner's own address, at most one a day) saying
why it was not applied and how to reply so it is. The notice quotes nothing of the mail.
Anyone else's mail stays silent.

A record the door could not place is never answered by mail: the sweep settles it by itself
(unsure.py), so no line of a reply brings one in or lets one go. Only the person's own lines
count (`own_lines`): the quoted mail, or the briefing quoted without `>` marks, never answers
anything.

    python reply.py [root]
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from email.utils import getaddresses
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import morning_view  # noqa: E402
import pages  # noqa: E402
import spaceyaml  # noqa: E402

FEED = Path("_meta") / "state" / "feed" / "mail.jsonl"
MORNING = Path("_meta") / "state" / "morning"
SEEN = Path("_meta") / "state" / "replies.jsonl"
ANSWERS = Path("_inbound") / "answers"
BRIEFING = re.compile(r"^\s*(re|aw|sv)\s*:\s*(\[shadow\]\s*)?morning briefing", re.I)
# A forward of the briefing (`Fwd:`, `Fw:`, `WG:`, `TR:`, after any `Re:`): never applied; the
# owner is told when it carries answers of theirs.
FORWARD = re.compile(r"^\s*((re|aw|sv)\s*:\s*)*(fwd?|wg|tr)\s*:\s*(\[shadow\]\s*)?morning briefing", re.I)
CLAUSE = re.compile(r"^\s*(all|\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*)\s+(yes|no|later|always|edit)\b\s*:?\s*(.*)$",
                    re.I)
CLAUSE_MAX = 200     # a longer line is no answer line
CLAUSES_MAX = 50     # a reply naming more clauses than this is read as none
QUOTED = re.compile(r"^\s*(>|on .+ wrote:)", re.I)
# The briefing's own words, for a reply whose mail program quoted it without `>` marks: its
# "Also worth knowing" part may carry outsiders' words (an older briefing listed parked records
# there, `Is this work? ... lunch; all yes`), so no line from that heading on is the person's own,
# and neither is its footer.
BRIEFING_OWN = re.compile(r"also worth knowing|is this work\?|sent automatically by your space|"
                          r"if you answer by reply, keep this line", re.I)


def own_lines(text: str) -> list:
    """The lines the person wrote: up to the quoted mail (`>` or `On ... wrote:`), or up to the
    first line of the briefing itself when it was quoted unmarked."""
    out = []
    for raw in str(text or "").splitlines():
        if QUOTED.match(raw) or BRIEFING_OWN.search(raw):
            break
        out.append(raw)
    return out


def parse(text: str, count: int) -> dict:
    """number -> (verb, note) from the reply's own lines (quoted mail ignored). A number written
    with a leading zero (`007`) is no item number, a line over CLAUSE_MAX characters is no
    answer line, and a reply of more than CLAUSES_MAX clauses answers nothing."""
    out: dict = {}
    clauses = 0
    for raw in own_lines(text):
        if len(raw) > CLAUSE_MAX:
            continue
        for part in re.split(r"[;\n]|,\s*(?=(?:all|\d+(?:\s*-\s*\d+)?)\s+(?:yes|no|later|always|edit)\b)", raw, flags=re.I):
            match = CLAUSE.match(part)
            if not match:
                continue
            numbers, verb, note = match.group(1), match.group(2).lower(), match.group(3).strip()
            if re.search(r"(?<!\d)0\d", numbers):
                continue
            clauses += 1
            if clauses > CLAUSES_MAX:
                return {}
            targets = []
            if numbers.lower() == "all":
                targets = list(range(1, count + 1))
            else:
                for piece in numbers.split(","):
                    if "-" in piece:   # only the listed numbers: `1-999999999` builds no huge range
                        a, b = (int(x) for x in piece.split("-"))
                        targets += list(range(max(1, min(a, b)), min(count, max(a, b)) + 1))
                    else:
                        targets.append(int(piece))
            for n in targets:
                if 1 <= n <= count:
                    out[n] = (verb, note if verb == "edit" else "")
    return out


LEFT = re.compile(r"^\s*left\s+(\d+(?:\s*,\s*\d+)*)\s+no\b", re.I)
def parse_left(text: str, left: list) -> list:
    """`left 2 no` (or `left 1, 3 no`): pull back what the briefing listed under Left yesterday."""
    out = []
    for raw in own_lines(text):
        match = LEFT.match(raw)
        if not match:
            continue
        for piece in match.group(1).split(","):
            n = int(piece.strip())
            if 1 <= n <= len(left) and left[n - 1] and left[n - 1] not in out:
                out.append(left[n - 1])
    return out


def _seen(root: Path) -> set:
    path = root / SEEN
    out = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.add(str(json.loads(line).get("record")))   # compared as text: an id may be a number
            except (ValueError, AttributeError):
                continue
    return out


def _mark_seen(root: Path, seen: set, rec: dict, now, answers: int,
               rejected: str | None = None, told: bool = False) -> None:
    row = {"record": rec.get("id"), "at": pages.iso(now), "answers": answers}
    if rejected:
        row["rejected"] = rejected
    if told:   # the owner was told by email why it was not applied
        row["told"] = True
    pages.append_text(root / SEEN, json.dumps(row) + "\n", root=root)
    seen.add(str(rec.get("id")))


def _addresses(value) -> list:
    """Every address in a field (a string or a list of them), lower case; an entry that holds no
    address gives "", which never equals the owner's."""
    entries = value if isinstance(value, list) else ([value] if value not in (None, "") else [])
    out = []
    for entry in entries:
        found = [addr.strip().lower() for _, addr in getaddresses([str(entry)])]
        out += found or [""]
    return out


def owner_address(space) -> str:
    """The space's one owner address, parsed; "" when it holds none or several."""
    mine = [a for a in _addresses(space.owner_email) if "@" in a]
    return mine[0] if len(mine) == 1 else ""


def sender_problem(rec: dict, me: str) -> str | None:
    """Why a mail is not one the person sent to themselves, or None: the From address exactly
    theirs (never a display name or a longer domain that holds it), every To and Cc address
    theirs too, and no spam label. Only the address fields count: a name says nothing."""
    if not me:
        return "no owner address"
    sender = _addresses(rec.get("from"))
    if sender != [me]:
        return "sender"
    to, cc = _addresses(rec.get("to")), _addresses(rec.get("cc"))
    if not to or any(a != me for a in to + cc):
        return "recipient"
    labels = (rec.get("identifiers") or {}).get("labels") if isinstance(rec.get("identifiers"), dict) else None
    labels = [labels] if isinstance(labels, str) else labels
    if isinstance(labels, list) and any(str(label).strip().upper() == "SPAM" for label in labels):
        return "spam"
    return None


# Why the owner's own reply was not applied, in the notice's words (`_tell_owner`).
NOT_APPLIED = {
    "reference": "it did not keep the reference line from the bottom of the briefing it answers",
    "recipient": "it went to someone else as well as to you",
    "forward": "it was a forward of the briefing, not a reply to it",
}


def _owner_sent(rec: dict, me: str) -> bool:
    """The From address is exactly the owner's and the mail is not marked spam: a reply of
    theirs that failed only on its recipients, its reference or being a forward."""
    if not me or _addresses(rec.get("from")) != [me]:
        return False
    labels = (rec.get("identifiers") or {}).get("labels") if isinstance(rec.get("identifiers"), dict) else None
    labels = [labels] if isinstance(labels, str) else labels
    return not (isinstance(labels, list) and any(str(label).strip().upper() == "SPAM" for label in labels))


def _answers_something(text) -> bool:
    """The person's own lines hold an answer line: numbers and a verb, or a `left` line. A plain
    forward with nothing of theirs is no reply."""
    return bool(parse(text, CLAUSES_MAX)) or any(LEFT.match(l) for l in own_lines(text))


def _tell_owner(root: Path, space, rec: dict, why: str) -> bool:
    """Queue one owner notice (to the owner's own address) saying why their reply was not applied
    and how to reply so it is; at most one a day, so mail that only poses as theirs cannot fill
    their inbox. Nothing of the mail is quoted. True when a notice was queued."""
    try:
        import owner_email
    except ImportError:
        return False
    try:
        at = pages.parse_iso(rec.get("at"))
    except (TypeError, ValueError):
        at = space.now()
    day = f"{at.day} {at:%B %Y}"
    notice = {"kind": "action", "sender": "morning-view", "key": f"reply-not-applied-{at:%Y-%m-%d}",
              "title": "Your reply was not applied",
              "summary": f"Your reply of {day} was not applied: {NOT_APPLIED.get(why, why)}. Nothing in it was used.",
              "steps": ["Reply to the briefing itself, to your own address only.",
                        "Write your answers above the quoted briefing and keep its last line, the one with "
                        "the reference."],
              "reassure": "Your list is unchanged. Answers in a reply like that count as soon as one arrives."}
    try:
        return owner_email.queue(root, notice) is not None
    except (Exception, SystemExit):
        return False


def run(root, now: dt.datetime | None = None) -> list:
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    me = owner_address(space)
    written = []
    if not me or not (root / FEED).exists():
        return written
    seen = _seen(root)
    for line in (root / FEED).read_text(encoding="utf-8", errors="replace").split("\n"):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or str(rec.get("id")) in seen:
            continue
        subject = str(rec.get("subject") or "")
        if FORWARD.match(subject):   # never applied; the owner hears why when it answered something
            if _owner_sent(rec, me) and _answers_something(rec.get("text")):
                told = _tell_owner(root, space, rec, "forward")
                _mark_seen(root, seen, rec, now, 0, rejected="forward", told=told)
            continue
        if not BRIEFING.match(subject):
            continue
        problem = sender_problem(rec, me)
        if problem is None:
            try:
                at = pages.parse_iso(rec["at"])
            except (KeyError, TypeError, ValueError):
                continue
            day = morning_view.reference_of(root, rec.get("text"), at)
            problem = None if day else "reference"
        if problem:   # never the person's own reply: nothing of it is applied, and it is read once
            told = (problem in ("recipient", "reference") and _owner_sent(rec, me)
                    and _answers_something(rec.get("text")) and _tell_owner(root, space, rec, problem))
            _mark_seen(root, seen, rec, now, 0, rejected=problem, told=told)
            continue
        listing = root / MORNING / f"{day}.json"
        if not listing.exists():
            continue
        day_list = json.loads(listing.read_text(encoding="utf-8"))
        items = day_list.get("items") or []
        verdicts = parse(rec.get("text"), len(items))
        pulled = parse_left(rec.get("text"), day_list.get("left") or [])
        if not verdicts and not pulled:
            continue
        chosen = {items[n - 1]["id"]: ([v, note] if note else v) for n, (v, note) in sorted(verdicts.items())}
        chosen.update({pid: "no" for pid in pulled})
        lines = [f"{n}. {items[n - 1]['text']} -> {v}{(': ' + note) if note else ''} "
                 f"(fingerprint {items[n - 1].get('fingerprint') or 'none'})" for n, (v, note) in sorted(verdicts.items())]
        lines += [f"Left yesterday: pull back {pid} -> no (it is retracted)" for pid in pulled]
        fm = {"id": pages.new_id(), "type": "source", "title": f"Your email reply of {day}: {len(chosen)} answers",
              "status": "draft", "generated": pages.generated("reply", now), "record": rec.get("id"),
              "verdicts": chosen, "recommendation": "read them back, then submit",
              "default": "nothing is applied until you submit in a session"}
        body = ("# Your email reply\n\nRead back before anything is applied; say submit to apply these, "
                "or correct any line first.\n\n" + "\n".join(lines) + "\n")
        tail = pages.record_file_name(str(rec.get("id")))[-6:].strip("-.") or "reply"
        target = pages.write(root / ANSWERS / f"reply-{day}-{tail}.md", fm, body, root=root)
        _mark_seen(root, seen, rec, now, len(chosen))
        written.append(target.relative_to(root).as_posix())
    return written


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    import needs_you   # here and not at the top: its helpers are the CLI's alone
    needs_you.utf8_stdio()
    for rel in run(Path(argv[0]) if argv else Path(".")):
        print(f"reply: {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
