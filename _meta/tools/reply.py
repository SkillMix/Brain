"""Answering by email reply: an adapter (vision section 14). A reply never signs by itself.

The person replies to their morning briefing ("1-4 yes, 5 edit: the date is the ninth,
6 later"). The mail sweep exports that reply like any mail; this tool finds it (from and to
the person's own address, a reply to a briefing subject), maps its numbers to the items of
that morning's list (`_meta/state/morning/<date>.json`, written by the morning view), and
writes `_inbound/answers/reply-<date>.md`: the verdicts and the read-back of every named
item, listed on Needs you as one `answer`. The next session reads it back and asks for
"submit"; only then does `sign.py apply` run with those verdicts.

    python reply.py [root]
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

FEED = Path("_meta") / "state" / "feed" / "mail.jsonl"
MORNING = Path("_meta") / "state" / "morning"
SEEN = Path("_meta") / "state" / "replies.jsonl"
ANSWERS = Path("_inbound") / "answers"
BRIEFING = re.compile(r"^\s*(re|aw|sv)\s*:\s*(\[shadow\]\s*)?morning briefing", re.I)
CLAUSE = re.compile(r"^\s*(all|\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*)\s+(yes|no|later|always|edit)\b\s*:?\s*(.*)$",
                    re.I)
QUOTED = re.compile(r"^\s*(>|on .+ wrote:)", re.I)


def parse(text: str, count: int) -> dict:
    """number -> (verb, note) from the reply's own lines (quoted mail ignored)."""
    out: dict = {}
    for raw in str(text or "").splitlines():
        if QUOTED.match(raw):
            break
        for part in re.split(r"[;\n]|,\s*(?=(?:all|\d+(?:\s*-\s*\d+)?)\s+(?:yes|no|later|always|edit)\b)", raw, flags=re.I):
            match = CLAUSE.match(part)
            if not match:
                continue
            numbers, verb, note = match.group(1), match.group(2).lower(), match.group(3).strip()
            targets = []
            if numbers.lower() == "all":
                targets = list(range(1, count + 1))
            else:
                for piece in numbers.split(","):
                    if "-" in piece:
                        a, b = (int(x) for x in piece.split("-"))
                        targets += list(range(min(a, b), max(a, b) + 1))
                    else:
                        targets.append(int(piece))
            for n in targets:
                if 1 <= n <= count:
                    out[n] = (verb, note if verb == "edit" else "")
    return out


def _seen(root: Path) -> set:
    path = root / SEEN
    out = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.add(json.loads(line).get("record"))
            except ValueError:
                continue
    return out


def run(root, now: dt.datetime | None = None) -> list:
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    me = space.owner_email.lower()
    written = []
    if not me or not (root / FEED).exists():
        return written
    seen = _seen(root)
    for line in (root / FEED).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or str(rec.get("id")) in seen:
            continue
        to = [str(t).lower() for t in (rec.get("to") or [])]
        if me not in str(rec.get("from", "")).lower() or not any(me in t for t in to):
            continue
        if not BRIEFING.match(str(rec.get("subject") or "")):
            continue
        at = pages.parse_iso(rec["at"])
        day = f"{at:%Y-%m-%d}"
        listing = root / MORNING / f"{day}.json"
        if not listing.exists():
            continue
        items = json.loads(listing.read_text(encoding="utf-8")).get("items") or []
        verdicts = parse(rec.get("text"), len(items))
        if not verdicts:
            continue
        chosen = {items[n - 1]["id"]: ([v, note] if note else v) for n, (v, note) in sorted(verdicts.items())}
        lines = [f"{n}. {items[n - 1]['text']} -> {v}{(': ' + note) if note else ''} "
                 f"(fingerprint {items[n - 1].get('fingerprint') or 'none'})" for n, (v, note) in sorted(verdicts.items())]
        fm = {"id": pages.new_id(), "type": "source", "title": f"Your email reply of {day}: {len(chosen)} answers",
              "status": "draft", "generated": pages.generated("reply", now), "record": rec.get("id"),
              "verdicts": chosen, "recommendation": "read them back, then submit",
              "default": "nothing is applied until you submit in a session"}
        body = ("# Your email reply\n\nRead back before anything is applied; say submit to apply these, "
                "or correct any line first.\n\n" + "\n".join(lines) + "\n")
        target = pages.write(root / ANSWERS / f"reply-{day}-{str(rec.get('id'))[-6:]}.md", fm, body)
        with (root / SEEN).open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"record": rec.get("id"), "at": pages.iso(now), "answers": len(chosen)}) + "\n")
        written.append(target.relative_to(root).as_posix())
    return written


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    for rel in run(Path(argv[0]) if argv else Path(".")):
        print(f"reply: {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
