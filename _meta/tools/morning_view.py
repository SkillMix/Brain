"""The morning view: the Needs-you list as it stands today, rendered and ready to mail.

Writes `live/needs-you.md` (the list, what left yesterday, the health lines), marks
every release leaving within the window as listed (a `window` item ships only after a
morning view showed it), writes the notice and the mail payload under `_meta/state/`,
and records one run line. It never sends: the agent hands the payload to its mail
connector. In the shadow the subject and title say so.

    python morning_view.py [root] --person <name> [--shadow] [--limit N]
    python morning_view.py [root] sent-today        exit 0 when today's briefing was sent
    python morning_view.py [root] record-send --id <message id> | --failed <reason>
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import needs_you  # noqa: E402
import owner_email  # noqa: E402
import pages  # noqa: E402
import spaceyaml  # noqa: E402

ROUTINE = "morning-view"
PAGE = Path("live") / "needs-you.md"
HEALTH = Path("live") / "health.md"
NOTICE = Path("_meta") / "state" / "morning-notice.json"
PAYLOAD = Path("_meta") / "state" / "morning-payload.json"
RUNS = Path("_meta") / "state" / "runs.jsonl"

# One plain label per item kind for the email's counts (they pass the plain-language check).
LABELS = {
    "leaving": "updates leaving for another space within a day",
    "high_stakes": "high-stakes items waiting for the domain owner",
    "answer": "answers that arrived",
    "request": "requests waiting for you",
    "question": "questions only you can answer",
    "check": "pages where newer evidence disagrees",
    "candidate": "notes waiting for your signature",
    "reconnect": "apps to reconnect",
    "protected_pr": "changes waiting for your one click",
    "outbox": "drafts older than two weeks, not sent",
    "doubt": "items held back in doubt",
    "landed_silently": "updates that landed here silently this week",
    "sample": "samples to look over",
}


@dataclass
class MorningReport:
    markdown_path: Path
    notice_path: Path
    payload_path: Path
    listed: list
    counts: dict


def _mark_listed(root: Path, space: spaceyaml.Space, items: list, now: dt.datetime) -> list:
    """Stamp every window item leaving within the window `listed`: an ordinary one and a
    high-stakes one alike (the latter lands pending at its destination, rules.md section 2)."""
    stamped = []
    horizon = now + space.window()
    for item in items:
        if item.kind not in ("leaving", "high_stakes"):
            continue
        path = root / item.payload
        if not path.is_file():
            continue
        fm, body = pages.read(path)
        if fm.get("class") != "window":
            continue
        try:
            stage_after = pages.parse_iso(fm.get("stage_after"))
        except (TypeError, ValueError):
            continue
        if stage_after > horizon:
            continue
        fm["listed"], fm["listed_at"] = True, pages.iso(now)
        pages.write(path, fm, body)
        stamped.append(item.id)
    return stamped


SEND_ROUTINES = ("morning-view", "morning_view", "daily-chain", "daily")


def _day_index(space: spaceyaml.Space, moment: dt.datetime) -> int:
    """Which scaled day a moment falls in (the UTC date when time_scale is 1)."""
    return int(moment.timestamp() // space.scale(24).total_seconds())


def sent_today(root, now: dt.datetime | None = None) -> bool:
    """True when a briefing was already sent this scaled day: any run line of a sending
    routine whose notes carry `email` or `message_id`, whichever routine wrote it."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    path = root / RUNS
    if not path.exists():
        return False
    today = _day_index(space, now)
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            run = json.loads(line)
        except ValueError:
            continue
        notes = run.get("notes") if isinstance(run, dict) else None
        if not isinstance(notes, dict) or run.get("routine") not in SEND_ROUTINES:
            continue
        if not (notes.get("email") or notes.get("message_id")):
            continue
        try:
            at = pages.parse_iso(run.get("at"))
        except (TypeError, ValueError):
            continue
        if _day_index(space, at) == today and at <= now:
            return True
    return False


def record_send(root, message_id: str | None = None, failed: str | None = None,
                cost_tokens: int = 0, now: dt.datetime | None = None) -> dict:
    """Append the canonical send line; the agent never writes it by hand."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    notes = {"step": "send"}
    if failed:
        notes["failed"] = str(failed)
    else:
        notes["email"] = str(message_id or "")
    line = {"routine": ROUTINE, "at": pages.iso(now), "space": space.name,
            "cost_tokens": int(cost_tokens), "notes": notes}
    runs = root / RUNS
    runs.parent.mkdir(parents=True, exist_ok=True)
    with runs.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(line) + "\n")
    return line


def _health_lines(root: Path) -> list:
    path = root / HEALTH
    if not path.exists():
        return []
    try:
        _, body = pages.read(path)
    except Exception:
        return []
    return [line.strip() for line in body.splitlines() if line.strip() and not line.startswith("#")]


def _landed_this_week(root: Path, space: spaceyaml.Space, now: dt.datetime) -> int:
    """Silent landings and filed records of the last scaled week: a count, never a list."""
    import json as _json
    path = root / "_meta" / "state" / "landed.jsonl"
    if not path.exists():
        return 0
    since, n = now - space.scale(7 * 24), 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            if since <= pages.parse_iso(_json.loads(line).get("at")) <= now:
                n += 1
        except Exception:
            continue
    return n


def _left_yesterday(root: Path, space: spaceyaml.Space, now: dt.datetime) -> list:
    """Log lines for releases shipped at once in the last scaled day."""
    log = root / "log.md"
    if not log.exists():
        return []
    since = now - space.scale(24)
    out = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.strip().split(" ", 2)
        if len(parts) < 3 or parts[0] != "-" or "at_once" not in parts[2]:
            continue
        try:
            at = pages.parse_iso(parts[1])
        except (TypeError, ValueError):
            continue
        if since < at <= now:
            out.append(f"{len(out) + 1}) " + line.strip()[2:])
    return out


def _markdown(root: Path, space: spaceyaml.Space, person: str, items: list, now: dt.datetime,
              limit: int, health: list) -> str:
    lines = needs_you.render(root, now=now, limit=limit, items=items).rstrip("\n").splitlines()
    lines.insert(1, f"For {person}, as of {pages.iso(now)}.")
    left = _left_yesterday(root, space, now)
    lines += ["", "## Left yesterday"] + (left or ["Nothing left at once."])
    lines += ["", "## Health"] + (health or ["No health line yet."])
    return "\n".join(lines) + "\n"


def _notice(space: spaceyaml.Space, items: list, counts: dict, health: list, shadow: bool) -> dict:
    count = len(items)
    if count == 0:
        title = "Nothing needs you today"
    else:
        title = f"{count} item{'s' if count != 1 else ''} need{'s' if count == 1 else ''} you"
    stats = [{"count": n, "label": LABELS.get(kind, kind.replace("_", " "))}
             for kind, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:owner_email.LIMITS["stats"]]
    base = "Here is what needs you today." if count else "Nothing is waiting for you today."
    notice = {"kind": "briefing", "sender": ROUTINE, "title": ("[shadow] " if shadow else "") + title,
              "summary": base, "stats": stats}
    if health:
        room = owner_email.LIMITS["summary"] - len(base) - len(" Health: ")
        notice["summary"] = f"{base} Health: {' '.join(health)[:max(room, 0)].rstrip()}"
    if not stats:
        notice["reassure"] = "Nothing needs you today. A skipped day costs nothing: every item has a default."
    terms = [space.name] + [str(m.get("name")) for m in space.mounts if m.get("name")]
    notice["allow_terms"] = sorted(set(t for t in terms if t))
    return notice, base


def _payload(space: spaceyaml.Space, notice: dict, base: str, shadow: bool, now: dt.datetime) -> dict:
    to = [space.owner_email.strip()] if "@" in space.owner_email else []
    today = now.date().isoformat()
    try:
        payload = owner_email.render(notice, to, today=today)
    except owner_email.NoticeError:
        # The health line did not pass the plain-language check: leave it to the page.
        notice["summary"] = f"{base} The health line is on your health page."
        payload = owner_email.render(notice, to, today=today)
    if shadow:
        payload["subject"] = "[shadow] " + payload["subject"].replace("[shadow] ", "")
    return payload


def _page_id(root: Path) -> str:
    path = root / PAGE
    if path.exists():
        try:
            existing = pages.read(path)[0].get("id")
            if existing and pages.ID_RE.match(str(existing)):
                return str(existing)
        except Exception:
            pass
    return pages.new_id()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def run(root, person: str, now: dt.datetime | None = None, shadow: bool = False,
        limit: int = 7) -> MorningReport:
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    shadow = bool(shadow) or space.mode == "shadow"
    items = needs_you.collect(root, now=now)
    counts = dict(Counter(item.kind for item in items))
    if "doubt" in counts:  # one list line, but the mail counts the held pages
        counts["doubt"] = needs_you.doubt_count(root)
        silent = _landed_this_week(root, space, now)
        if silent:
            counts["landed_silently"] = silent
    listed = _mark_listed(root, space, items, now)
    health = _health_lines(root)
    page_fm = {"id": _page_id(root), "type": "snapshot", "title": "Needs you", "status": "draft",
               "generated": pages.generated(ROUTINE, now), "person": person}
    pages.write(root / PAGE, page_fm, _markdown(root, space, person, items, now, limit, health))
    notice, base = _notice(space, items, counts, health, shadow)
    payload = _payload(space, notice, base, shadow, now)
    _write_json(root / NOTICE, notice)
    _write_json(root / PAYLOAD, payload)
    # the day's numbering, so a reply ("1-4 yes, 5 later") maps to the items as listed
    _write_json(root / "_meta" / "state" / "morning" / f"{now:%Y-%m-%d}.json",
                {"date": f"{now:%Y-%m-%d}", "items": [{"id": i.id, "kind": i.kind, "text": i.text,
                                                       "fingerprint": i.fingerprint} for i in items]})
    line = {"routine": ROUTINE, "at": pages.iso(now), "space": space.name, "cost_tokens": 0,
            "notes": {"items": len(items), "listed": len(listed), "shadow": shadow,
                      "recipient": bool(payload["to"])}}
    runs = root / RUNS
    runs.parent.mkdir(parents=True, exist_ok=True)
    with runs.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(line) + "\n")
    return MorningReport(markdown_path=root / PAGE, notice_path=root / NOTICE,
                         payload_path=root / PAYLOAD, listed=listed, counts=counts)


def _guard_cli(argv: list):
    """`[root] sent-today` and `[root] record-send --id X | --failed REASON [--cost N]`."""
    args = list(argv)
    verbs = ("sent-today", "record-send")
    root = "."
    if len(args) > 1 and args[0] not in verbs and args[1] in verbs:
        root = args.pop(0)
    if not args or args[0] not in verbs:
        return None
    command, rest = args[0], args[1:]
    if command == "sent-today":
        done = sent_today(root)
        print("a briefing was sent today: skip the send" if done else "no briefing sent today")
        return 0 if done else 1
    ap = argparse.ArgumentParser(prog="morning_view.py record-send")
    ap.add_argument("--id", dest="message_id")
    ap.add_argument("--failed")
    ap.add_argument("--cost", type=int, default=0)
    opts = ap.parse_args(rest)
    if not opts.message_id and not opts.failed:
        ap.error("give --id <message id> or --failed <reason>")
    print(json.dumps(record_send(root, opts.message_id, opts.failed, opts.cost)))
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    guarded = _guard_cli(argv)
    if guarded is not None:
        return guarded
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--person", required=True, help="whose list this is")
    ap.add_argument("--shadow", action="store_true", help="mark the subject and title [shadow]")
    ap.add_argument("--limit", type=int, default=7)
    args = ap.parse_args(argv)
    report = run(args.root, args.person, shadow=args.shadow, limit=args.limit)
    root = Path(args.root)
    print(f"wrote {report.markdown_path.relative_to(root).as_posix()} "
          f"({sum(report.counts.values())} items, {len(report.listed)} listed)")
    print(f"notice {report.notice_path.relative_to(root).as_posix()}, "
          f"payload {report.payload_path.relative_to(root).as_posix()}: hand it to the mail connector")
    return 0


if __name__ == "__main__":
    sys.exit(main())
