"""unsure: records the reader could not classify as company or personal (judgment routing design).

The export writes such a record, with `"feed": "<source>"` added, to
`_meta/state/private/unsure.jsonl`: a local private zone the sync script never stages. The
morning view lists each one in the owner's own email by a plain one-line label; a `yes` in the
reply admits it (written once to its feed as a company record, so the next sweep makes its page
and the router reads it), a `no` drops it (counted as personal). An item listed for 14 days
without an answer goes. Nothing here reads or writes the repository's tracked files.

    python unsure.py [root]            how many records wait
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

STORE = spaceyaml.PRIVATE_STATE / "unsure.jsonl"
FEED = Path("_meta") / "state" / "feed"
EXPIRE_DAYS = 14
LOCAL = ("key", "feed", "first_listed")
URL = re.compile(r"https?://\S+|www\.\S+", re.I)
DASHES = re.compile("[—–]")
LONG_TOKEN = re.compile(r"\S{32,}")


def key_of(record: dict) -> str:
    return hashlib.sha1(str(record.get("id")).encode("utf-8")).hexdigest()[:10]


def code_of(record: dict) -> str:
    """The five characters the owner answers with ("3fa2c yes"): stable across days and lists."""
    return key_of(record)[:5]


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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8",
                   newline="\n")
    tmp.replace(path)


def waiting(root) -> list:
    """Every record waiting for the owner's word, oldest first, each with its `key`."""
    out = [dict(r, key=key_of(r)) for r in _read(root)]
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
    """`yes`: the record goes to its feed (once) as a company record."""
    root = Path(root)
    record = _take(root, key)
    if record is None:
        return False
    feed = root / FEED / f"{record.get('feed') or 'mail'}.jsonl"
    feed.parent.mkdir(parents=True, exist_ok=True)
    present = set()
    if feed.exists():
        for line in feed.read_text(encoding="utf-8").splitlines():
            try:
                present.add(str(json.loads(line).get("id")))
            except (ValueError, AttributeError):
                continue
    if str(record["id"]) not in present:
        clean = dict({k: v for k, v in record.items() if k not in LOCAL}, admitted=True)   # swept however old
        with feed.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(clean, ensure_ascii=False) + "\n")
    return True


def drop(root, key: str) -> bool:
    """`no`: the record goes, counted as personal; nothing of it is kept."""
    return _take(Path(root), key) is not None


def expire(root, now: dt.datetime, days: int = EXPIRE_DAYS) -> int:
    """Items listed for `days` without an answer go; returns how many."""
    records = _read(root)
    keep, gone = [], 0
    for r in records:
        try:
            first = pages.parse_iso(r["first_listed"]) if r.get("first_listed") else None
        except (TypeError, ValueError):
            first = None
        if first is not None and now - first > dt.timedelta(days=days):
            gone += 1
        else:
            keep.append(r)
    if gone:
        _write(root, keep)
    return gone


def line_for(record: dict, width: int = 60) -> str:
    """A plain one-line label for the owner's email: the subject without links, dashes or long
    codes, cut on a word, then the source and the day."""
    text = URL.sub("", str(record.get("subject") or record.get("title") or "untitled"))
    text = LONG_TOKEN.sub("", DASHES.sub(",", text))
    text = " ".join(text.replace(" ,", ",").split()).strip(" ,") or "untitled"
    if len(text) > width:
        text = text[:width].rsplit(" ", 1)[0].rstrip(" ,.") + "..."
    try:
        day = f"{pages.parse_iso(record.get('at')):%d %b}"
    except (TypeError, ValueError):
        day = "undated"
    return f"{text} ({record.get('feed') or record.get('kind') or 'record'}, {day})"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    print(f"{len(waiting(Path(argv[0]) if argv else Path('.')))} record(s) wait for the owner's word")
    return 0


if __name__ == "__main__":
    sys.exit(main())
