"""unsure: records the reader could not classify as company or personal (judgment routing design).

The export writes such a record, with `"feed": "<source>"` added, to
`_meta/state/private/unsure.jsonl` (or hands it to `keep`): a local private zone the sync script
never stages. The morning view lists each one in the owner's own email by a plain one-line label;
a `yes` in the reply admits it (written once to its feed as a company record, so the next sweep
makes its page and the router reads it), a `no` drops it (counted as personal). An item listed
for 14 days without an answer goes.

A cloud run's private zone goes with its clone, so every record kept here also leaves its id,
its source and the time it was kept in `_meta/state/unsure-ids.jsonl`, once per id, and nothing
else: no content, no subject, no sender. The morning view counts them and a session on the
computer can fetch each by its id. A record the export wrote to the store itself is noted the
next time the sweep runs. That file is the only tracked file this tool writes.

The owner answers with a record's code (`3fa2c09b yes`). A code is random, eight hex digits
drawn once and kept with the record in the private store (`waiting` draws one for a record that
has none), so no one can work it out from the record's id, which the tracked ids file holds.
The label the email shows is the subject made plain: no link, dash, long code, markup sign
(`*`, a backtick) or control character, so an outsider's subject cannot style or spoof the
person's own briefing.

    python unsure.py [root]            how many records wait
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

STORE = spaceyaml.PRIVATE_STATE / "unsure.jsonl"
IDS = Path("_meta") / "state" / "unsure-ids.jsonl"   # tracked: ids, sources and times only
FEED = Path("_meta") / "state" / "feed"
EXPIRE_DAYS = 14
LOCAL = ("key", "feed", "first_listed", "code")
CODE_RE = re.compile(r"^[0-9a-f]{8}\Z")
URL = re.compile(r"https?://\S+|www\.\S+", re.I)
DASHES = re.compile("[—–]")
LONG_TOKEN = re.compile(r"\S{32,}")
# Markup the email would honour (`**bold**`, a backtick), control characters, line and paragraph
# separators, zero-width and bidirectional marks.
MARKUP = re.compile("[*`\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]")


def key_of(record: dict) -> str:
    return hashlib.sha1(str(record.get("id")).encode("utf-8")).hexdigest()[:10]


def code_of(record: dict) -> str:
    """The eight hex digits the owner answers with ("3fa2c09b yes"), drawn at random when the
    record was first listed (`waiting`) and kept in the private store: stable across days and
    lists, never derived from the id. A record without one has none ("")."""
    code = str(record.get("code") or "")
    return code if CODE_RE.match(code) else ""


def _read(root: Path) -> list:
    path = Path(root) / STORE
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("id"):
            out.append(record)
    return out


def _write(root: Path, records: list) -> None:
    path = Path(root) / STORE
    tmp = path.with_suffix(".tmp")
    pages.write_text(tmp, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), root=root)
    pages.check_no_links(path, root=root)
    tmp.replace(path)


def _id_rows(root: Path) -> list:
    path = Path(root) / IDS
    out = []
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("id"):
            out.append(row)
    return out


def keep(root, record: dict, now: dt.datetime | None = None) -> bool:
    """Park a record the reader could not classify, once per id, with its `feed` (its source
    when the export left it out), and note its id. True when it was not kept yet."""
    root = Path(root)
    if not isinstance(record, dict) or not record.get("id"):
        return False
    records = _read(root)
    new = str(record["id"]) not in {str(r["id"]) for r in records}
    if new:
        _write(root, records + [dict(record, feed=record.get("feed") or record.get("source") or "mail")])
    note_ids(root, now)
    return new


def note_ids(root, now: dt.datetime | None = None) -> int:
    """Append `{id, source, at}` to IDS for every kept record it does not name yet (`at` is when
    it was noted, so the morning view can count what came since the last briefing); returns how
    many. Nothing else of the record leaves the private zone."""
    root = Path(root)
    known = {str(r["id"]) for r in _id_rows(root)}
    new = []
    for record in _read(root):
        if str(record["id"]) not in known:
            known.add(str(record["id"]))
            new.append(record)
    if not new:
        return 0
    at = pages.iso(now or spaceyaml.load(root).now())
    lines = "".join(json.dumps({"id": str(record["id"]), "source": str(record.get("feed") or record.get("source")
                                                                          or "mail"), "at": at},
                               ensure_ascii=False) + "\n" for record in new)
    pages.append_text(root / IDS, lines, root=root)
    return len(new)


def kept_since(root, since: dt.datetime | None, now: dt.datetime, besides=()) -> int:
    """How many records were noted as unsure after `since` and by `now` (every one by `now`
    without a `since`), leaving out the ids in `besides`: distinct ids, a count only."""
    ids, besides = set(), {str(b) for b in besides}
    for row in _id_rows(root):
        try:
            at = pages.parse_iso(row.get("at"))
        except (TypeError, ValueError):
            continue
        if (since is None or at > since) and at <= now and str(row["id"]) not in besides:
            ids.add(str(row["id"]))
    return len(ids)


def waiting(root) -> list:
    """Every record waiting for the owner's word, oldest first, each with its `key` and its
    `code`. A record that has no code yet (the export wrote it to the store itself) gets one
    now, drawn at random and kept, never two records the same."""
    records = _read(root)
    taken = {code_of(r) for r in records} - {""}
    drawn = False
    for record in records:
        if not code_of(record):
            code = secrets.token_hex(4)
            while code in taken:
                code = secrets.token_hex(4)
            record["code"] = code
            taken.add(code)
            drawn = True
    if drawn:
        _write(Path(root), records)
    out = [dict(r, key=key_of(r)) for r in records]
    return sorted(out, key=lambda r: str(r.get("at") or ""))


def mark_listed(root, keys, now: dt.datetime) -> None:
    keys = set(keys)
    records = _read(root)
    for r in records:
        if key_of(r) in keys and not r.get("first_listed"):
            r["first_listed"] = pages.iso(now)
    _write(root, records)


def _take(root, key: str):
    records = _read(root)
    hit = next((r for r in records if key_of(r) == key), None)
    if hit is not None:
        _write(root, [r for r in records if r is not hit])
    return hit


def admit(root, key: str) -> bool:
    """`yes`: the record goes to its feed (once) as a company record. A `feed` that is no plain
    name raises UnsafePath and the record stays where it is."""
    root = Path(root)
    hit = next((r for r in _read(root) if key_of(r) == key), None)
    if hit is None:
        return False
    feed = root / FEED / f"{pages.check_name(str(hit.get('feed') or 'mail'), 'feed name')}.jsonl"
    record = _take(root, key)
    if record is None:
        return False
    present = set()
    if feed.exists():
        for line in feed.read_text(encoding="utf-8").split("\n"):
            try:
                present.add(str(json.loads(line).get("id")))
            except (ValueError, AttributeError):
                continue
    if str(record["id"]) not in present:
        clean = dict({k: v for k, v in record.items() if k not in LOCAL}, admitted=True)   # swept however old
        pages.append_text(feed, json.dumps(clean, ensure_ascii=False) + "\n", root=root)
    return True


def drop(root, key: str) -> bool:
    """`no`: the record goes, counted as personal; nothing of it is kept."""
    return _take(Path(root), key) is not None


def expire(root, now: dt.datetime, days: int = EXPIRE_DAYS) -> int:
    """Items listed for `days` without an answer go; returns how many."""
    records = _read(root)
    kept, gone = [], 0
    for r in records:
        try:
            first = pages.parse_iso(r["first_listed"]) if r.get("first_listed") else None
        except (TypeError, ValueError):
            first = None
        if first is not None and now - first > dt.timedelta(days=days):
            gone += 1
        else:
            kept.append(r)
    if gone:
        _write(root, kept)
    return gone


def line_for(record: dict, width: int = 60) -> str:
    """A plain one-line label for the owner's email: the subject without links, dashes, long
    codes, markup or control characters, cut on a word, then the source and the day."""
    text = MARKUP.sub(" ", str(record.get("subject") or record.get("title") or "untitled"))
    text = URL.sub("", text)
    text = LONG_TOKEN.sub("", DASHES.sub(",", text))
    text = " ".join(text.replace(" ,", ",").split()).strip(" ,") or "untitled"
    if len(text) > width:
        text = text[:width].rsplit(" ", 1)[0].rstrip(" ,.") + "..."
    try:
        day = f"{pages.parse_iso(record.get('at')):%d %b}"
    except (TypeError, ValueError):
        day = "undated"
    source = MARKUP.sub("", str(record.get("feed") or record.get("kind") or "record"))[:20] or "record"
    return f"{text} ({source}, {day})"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    import needs_you   # here and not at the top: its helpers are the CLI's alone
    needs_you.utf8_stdio()
    print(f"{len(waiting(Path(argv[0]) if argv else Path('.')))} record(s) wait for the owner's word")
    return 0


if __name__ == "__main__":
    sys.exit(main())
