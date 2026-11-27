"""unsure: records the door could not place as company or personal, settled without asking anyone.

The export parks such a record, with `"feed": "<source>"` added, in
`_meta/state/private/unsure.jsonl`: a local private zone the sync script never stages. Before the
sweep writes any page it runs the door's context check over every record parked here that was
not checked yet (`door`): a hard signal, a fact a script can check, admits it to its feed at once
as a company record. A hard signal is its thread, channel or event series on a witness page of
this space, or an identifier of a registered subject on it (a mail domain, a label, a linked
repository), never a keyword of its title, a subject it names or anything it says. A record the
export parked with `"reason": "mixed"` (a sign against beside a sign for) is never admitted.

Every other record waits as a pointer: its id, its source, when it was parked, a hash of each of
its thread, channel and series ids and its reason, in the tracked `_meta/state/unsure-ids.jsonl`,
and nothing else: no content, no subject, no sender, no address, no name, so a cloud run's pointer
outlives its clone. After the sweep wrote its pages, `resolve` compares every waiting pointer with
the ids of those pages: on a match, a record held here goes to its feed as the door admits one,
and the pointer is listed by `refetch` (`sweep.py --refetch`) until its witness page exists, so
the next export fetches by its id what a cloud run's clone took with it. A pointer with no match
90 days after it was parked goes, from the store and for matching, and no one is told (`expire`).
A whole calendar is no signal: only an event's series (`identifiers.series`). The owner is never asked; the health page counts what happened
(`health_line`). Matching compares ids: no model, no wording.

The ids file is append-only (a union merge keeps both sides): a pointer line
`{id, source, at, ids, reason}`, and a line `{id, admitted, signal, at}` when it was admitted
(`admitted` is `context` at the door and `match` later, which carries `"fetch": true`).

    python unsure.py [root]            how many pointers wait
    python sweep.py [root] --refetch   the records to fetch again by id (`refetch`), a routine's command
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
IDS = Path("_meta") / "state" / "unsure-ids.jsonl"   # tracked: ids, sources, times and hashes only
FEED = Path("_meta") / "state" / "feed"
WITNESS = Path("knowledge") / "sources"
EXPIRE_DAYS = 90
KINDS = ("thread", "channel", "series")   # the ids a pointer is matched on: never a whole calendar
REASONS = ("no-signal", "mixed")            # why it was parked; anything else reads as the first
BY = ("context", "match")
# Never copied to the feed on admission: the store's own fields (`first_listed` and `code` from
# before the door settled records itself, in a store an older version wrote).
LOCAL = ("key", "feed", "reason", "first_listed", "code")
PLAIN = re.compile(r"^[a-z][a-z_]{0,30}\Z")   # a signal or a `by`, as the door writes them
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+=-]{0,199}\Z")   # what `refetch` prints: no shell sign, no glob
SAFE_SOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
HEALTH_PREFIX = "- door:"


def key_of(record: dict) -> str:
    return hashlib.sha1(str(record.get("id")).encode("utf-8")).hexdigest()[:10]


def _row(line: str) -> dict:
    try:
        row = json.loads(line)
    except ValueError:
        return {}
    return row if isinstance(row, dict) else {}


def _jsonl(path: Path) -> list:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return [row for row in map(_row, lines) if row.get("id")]


def _read(root: Path) -> list:
    return _jsonl(Path(root) / STORE)


def _write(root: Path, records: list) -> None:
    path = Path(root) / STORE
    tmp = path.with_suffix(".tmp")
    pages.write_text(tmp, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), root=root)
    pages.check_no_links(path, root=root)
    tmp.replace(path)


def _id_rows(root: Path) -> list:
    return _jsonl(Path(root) / IDS)


def _append(root: Path, rows: list) -> None:
    if rows:
        pages.append_text(Path(root) / IDS, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                          root=root)


def _values(value) -> list:
    return [str(v) for v in (value if isinstance(value, list) else [value]) if v not in (None, "")]


def _hash(kind: str, value: str) -> str:
    return hashlib.sha256(f"{kind}\0{value}".encode("utf-8")).hexdigest()[:16]


def id_hashes(identifiers) -> dict:
    """{hash: kind} for each thread, channel and series id: what a pointer keeps, never the id
    (an export may have put a name there)."""
    ident = identifiers if isinstance(identifiers, dict) else {}
    return {_hash(kind, value): kind for kind in KINDS for value in _values(ident.get(kind))}


def _reason(record: dict) -> str:
    return record.get("reason") if record.get("reason") in REASONS else REASONS[0]


def door_of(value):
    """A record's `door` as a witness page keeps it, only in the shape the door writes; else None."""
    if isinstance(value, dict) and value.get("by") in BY and PLAIN.match(str(value.get("signal") or "")):
        return {"by": value["by"], "signal": value["signal"]}
    return None


def note_ids(root, now: dt.datetime | None = None) -> int:
    """Append a pointer line for every record held here that has none with its ids yet; returns
    how many. Nothing the record says leaves the private zone."""
    root = Path(root)
    known = {str(r["id"]) for r in _id_rows(root) if "ids" in r}
    at = pages.iso(now or spaceyaml.load(root).now())
    rows = []
    for record in _read(root):
        if str(record["id"]) not in known:
            known.add(str(record["id"]))
            rows.append({"id": str(record["id"]), "source": str(record.get("feed") or record.get("source") or "mail"),
                         "at": at, "ids": sorted(id_hashes(record.get("identifiers"))), "reason": _reason(record)})
    _append(root, rows)
    return len(rows)


def pointers(root, now: dt.datetime) -> dict:
    """{id: pointer} for every record parked here or by a cloud run: `source`, `at` (first parked),
    `ids`, `reason`, and `state`: `admitted`, `fetch`, `waiting` or `expired`."""
    out = {}
    for row in _id_rows(root):
        p = out.setdefault(str(row["id"]), {"source": "", "at": None, "ids": set(), "reason": REASONS[0],
                                            "admitted": None, "fetch": None})
        if row.get("admitted") in BY:
            if not PLAIN.match(str(row.get("signal") or "")):
                continue
            if row.get("fetch"):
                p["fetch"] = p["fetch"] or row
            else:
                p["admitted"] = row
            continue
        try:
            at = pages.parse_iso(row.get("at"))
        except (TypeError, ValueError):
            continue
        p["at"] = at if p["at"] is None else min(p["at"], at)
        p["source"] = p["source"] or str(row.get("source") or "mail")
        p["ids"] |= {str(h) for h in row.get("ids") or []}
        if _reason(row) == "mixed":   # a sign against stands, whichever line a merge put last
            p["reason"] = "mixed"
    days = dt.timedelta(days=EXPIRE_DAYS)
    for p in out.values():
        try:   # a fetch that never lands goes too, 90 days after its match
            fetching = bool(p["fetch"]) and now - pages.parse_iso(p["fetch"].get("at")) <= days
        except (TypeError, ValueError):
            fetching = False
        p["state"] = ("admitted" if p["admitted"] else "fetch" if fetching
                      else "expired" if p["fetch"] or p["at"] is None or now - p["at"] > days
                      else "waiting")
    return out


def admit(root, key: str, door: dict | None = None) -> bool:
    """The record goes to its feed (once) as a company record, with `admitted: true` (swept however
    old) and how the door admitted it. A `feed` that is no plain name raises UnsafePath and the
    record stays where it is."""
    root = Path(root)
    records = _read(root)
    hit = next((r for r in records if key_of(r) == key), None)
    if hit is None:
        return False
    feed = root / FEED / f"{pages.check_name(str(hit.get('feed') or 'mail'), 'feed name')}.jsonl"
    rid, lines = str(hit["id"]), feed.read_text(encoding="utf-8").splitlines() if feed.exists() else []
    # in the feed first, so a failed write loses nothing; only a line that holds the id is parsed
    if not any(rid in line and str(_row(line).get("id")) == rid for line in lines):
        clean = dict({k: v for k, v in hit.items() if k not in LOCAL and k != "door"}, admitted=True)
        if door_of(door):
            clean["door"] = door_of(door)
        pages.append_text(feed, json.dumps(clean, ensure_ascii=False) + "\n", root=root)
    _write(root, [r for r in records if r is not hit])
    return True


def _admitted_row(record_id: str, by: str, signal: str, now: dt.datetime, fetch: bool = False) -> dict:
    return dict({"id": record_id, "admitted": by, "signal": signal, "at": pages.iso(now)},
                **({"fetch": True} if fetch else {}))


def _on_witness_pages(root: Path, wanted: dict) -> dict:
    """{hash: kind} for the wanted hashes some witness page here carries among its identifiers.
    A page is read only when its front matter holds one of the ids as text."""
    found = {}
    needles = {value for value in wanted.values()}
    for path in (root / WITNESS).rglob("*.md") if needles else []:
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:   # the front matter, not the text
                head = "".join(line for _, line in zip(range(400), handle)).split("\n---", 1)[0]
        except OSError:
            continue
        if not any(n in head for n in needles):
            continue
        try:
            fm, _ = pages.read(path)
        except Exception:
            continue
        found.update({h: k for h, k in id_hashes(fm.get("identifiers")).items() if h in wanted})
    return found


def _registry_signal(reg, record: dict) -> str | None:
    """The first identifier of a registered subject the record carries: never a keyword of its
    title, and never a subject it names itself (only the journal feed may)."""
    ident = record.get("identifiers") if isinstance(record.get("identifiers"), dict) else {}
    record = dict(record, identifiers={k: v for k, v in ident.items() if k != "subject"})
    for subject in reg.match(record):
        keys = reg.matched_by(subject, record) - {"keywords", "subject"}
        if keys:
            return sorted(keys)[0]
    return None


def door(root, reg, now: dt.datetime) -> int:
    """The door's context check, before the sweep writes a page: every record held here that was
    not checked yet is admitted at once on a hard signal (`context` the first time, `match` for
    one parked before), unless it was parked as `mixed`; the rest get their pointer line.
    Returns how many were admitted."""
    root = Path(root)
    rows = _id_rows(root)
    checked = {str(r["id"]) for r in rows if "ids" in r}
    parked_before = {str(r["id"]) for r in rows if r.get("source")}
    todo = [r for r in _read(root) if str(r["id"]) not in checked and _reason(r) != "mixed"]
    raw = {}
    for record in todo:
        ident = record.get("identifiers") if isinstance(record.get("identifiers"), dict) else {}
        raw.update({_hash(kind, value): value for kind in KINDS for value in _values(ident.get(kind))})
    on_pages = _on_witness_pages(root, raw)
    admitted, out = 0, []
    for record in todo:
        signal = next((on_pages[h] for h in sorted(id_hashes(record.get("identifiers"))) if h in on_pages), None)
        signal = signal or _registry_signal(reg, record)
        if not signal:
            continue
        by = "match" if str(record["id"]) in parked_before else "context"
        try:
            if admit(root, key_of(record), {"by": by, "signal": signal}):
                out.append(_admitted_row(str(record["id"]), by, signal, now))
                admitted += 1
        except pages.UnsafePath:   # a feed that is no plain name: it waits, and expires
            continue
    _append(root, out)
    note_ids(root, now)
    return admitted


def resolve(root, written: list, now: dt.datetime) -> int:
    """Compare every waiting pointer with the identifiers of the witness pages this run wrote
    (their front matter). A match is marked to be fetched again by its id until its witness page
    exists (`refetch`), and a record held here goes to its feed at once (`match`), so a cloud run's
    admission, whose feed goes with its clone, is never lost. A record this computer holds whose
    pointer another run marked goes to its feed too. Returns how many matched."""
    root = Path(root)
    on_pages = {}
    for fm in written:
        on_pages.update(id_hashes((fm or {}).get("identifiers")))
    held = {str(r["id"]): r for r in _read(root)}
    rows = []
    for pid, p in pointers(root, now).items():
        if p["state"] == "fetch":
            signal = p["fetch"]["signal"]
        elif p["state"] == "waiting" and p["reason"] != "mixed":
            signal = next((on_pages[h] for h in sorted(p["ids"]) if h in on_pages), None)
            if signal:
                rows.append(_admitted_row(pid, "match", signal, now, fetch=True))
        else:
            continue
        if signal and pid in held:
            try:
                admit(root, key_of(held[pid]), {"by": "match", "signal": signal})
            except pages.UnsafePath:   # a feed that is no plain name: the export fetches it again
                continue
    _append(root, rows)
    return len(rows)


def expire(root, now: dt.datetime) -> int:
    """Records held here whose pointer found no match within 90 days of parking go; returns how
    many. Nothing is shown to anyone."""
    # an admitted one is on its feed somewhere already: a copy here would stay for ever
    gone = {pid for pid, p in pointers(root, now).items() if p["state"] in ("expired", "admitted")}
    records = _read(root)
    kept = [r for r in records if str(r["id"]) not in gone]
    if len(kept) != len(records):
        _write(Path(root), kept)
    return len(records) - len(kept)


def refetch(root, now: dt.datetime | None = None) -> list:
    """(source, signal, id) for every pointer a match settled while its record was on no computer
    that ran the match, until the record's witness page exists: the next export fetches each by
    its id. An id or a source outside a plain set of characters is left out: what is printed never
    carries a shell sign, a glob, a line break or a control character."""
    root = Path(root)
    now = now or spaceyaml.load(root).now()
    out = []
    for pid, p in sorted(pointers(root, now).items()):
        if p["state"] != "fetch" or not SAFE_ID.match(pid) or not SAFE_SOURCE.match(p["source"]):
            continue
        if any((root / WITNESS).glob(f"*/*/{pages.record_file_name(pid)}.md")):
            continue
        out.append((p["source"], p["fetch"]["signal"], pid))
    return out


def counts(root, now: dt.datetime) -> dict:
    out = {"context": 0, "match": 0, "waiting": 0, "expired": 0}
    for p in pointers(root, now).values():
        row = p["admitted"] or p["fetch"]
        if row:
            out[row["admitted"]] += 1
        elif p["state"] in out:
            out[p["state"]] += 1
    return out


def _skipped(value) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    return sum(_skipped(v) for v in value.values()) if isinstance(value, dict) else 0


def health_line(root, runs: list, now: dt.datetime) -> str:
    """One line of counts for the health page, or "" when there is nothing to count: the records
    the door admitted, those admitted later by a match, those the export left out as personal or
    academic (its run lines' `notes.skipped`), the pointers waiting and those expired."""
    c = counts(root, now)
    left_out = sum(_skipped((run.get("notes") or {}).get("skipped")) for run in runs
                   if isinstance(run.get("notes"), dict))
    if not any(c.values()) and not left_out:
        return ""
    return (f"{HEALTH_PREFIX} {c['context']} admitted at the door, {c['match']} admitted later by a match, "
            f"{left_out} left out as personal or academic, {c['waiting']} waiting, {c['expired']} expired")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    import needs_you   # here and not at the top: its helpers are the CLI's alone
    needs_you.utf8_stdio()
    root = Path(argv[0]) if argv else Path(".")
    now = spaceyaml.load(root).now()
    print(f"{counts(root, now)['waiting']} pointer(s) wait for a match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
