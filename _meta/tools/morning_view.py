"""The morning view: the Needs-you list as it stands today, rendered and ready to mail.

Writes `live/needs-you.md` (the list, what left yesterday, what the sync fixed by itself in the
last day, the health lines), marks every release leaving within the window as listed (a
`window` item ships only after a morning view showed it), writes the notice and the mail
payload under `_meta/state/`, and records one run line. It never sends: the agent hands the
payload to its mail connector. In the shadow the subject and title say so.

    python morning_view.py [root] --person <name> [--shadow] [--limit N]
    python morning_view.py [root] sent-today        exit 0 when today's briefing was sent
    python morning_view.py [root] record-send --id <message id> [--to <address> ...] | --failed <reason>

`record-send --to` takes the recipients read back from the sent message, all after one `--to` or
one `--to` each; every one is kept. Any address but the owner's makes the line a failed send
and puts one security item on Needs you; no address is written into the space.

Every briefing carries a reply reference, a random code in its footer that only the mail itself
holds: the day's listing (`_meta/state/morning/<date>.json`) keeps a hash of it and never the
code, and reply.py takes an answer only from a mail that quotes one (`reference_of`). The mail
counts a briefing that reached someone else apart from the other security items, and counts
neither from another space (needs_you reads `security` only from its own writers' files). The
meetings section takes only the lines the sweep writes for a meeting and its people.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import secrets
import sys
from collections import Counter
from dataclasses import dataclass
from email.utils import getaddresses
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import needs_you  # noqa: E402
import owner_email  # noqa: E402
import pages  # noqa: E402
import spaceyaml  # noqa: E402
import unsure  # noqa: E402

ROUTINE = "morning-view"
PAGE = Path("live") / "needs-you.md"
HEALTH = Path("live") / "health.md"
# The notice and the payload live in the private zone the sync never stages.
NOTICE = spaceyaml.PRIVATE_STATE / "morning-notice.json"
PAYLOAD = spaceyaml.PRIVATE_STATE / "morning-payload.json"
LEGACY = (Path("_meta") / "state" / "morning-notice.json", Path("_meta") / "state" / "morning-payload.json")
RUNS = Path("_meta") / "state" / "runs.jsonl"
STALE = Path("_meta") / "state" / "stale.yaml"
# One security item per briefing that reached someone else, written by record_send only. It goes
# before every undated item on the list (the tools' own items depend 2 at most; 99 stays ahead of
# any value read from data), and the mail always counts it, however many other kinds wait.
ITEMS = Path("_inbound") / "needs-you" / "morning-view.yaml"
RECIPIENT_ITEM = "morning-view-recipient"
RECIPIENT_DEPENDS = 99
# The recipient item counts under `security`; every other security item (a cloud run that changed
# protected files) under its own label, so the mail never calls one the other.
SECURITY_OTHER = "security_warning"
ALWAYS_COUNTED = ("security", SECURITY_OTHER)
# The reply reference: twenty characters of base32 (100 random bits), shown as `reference <code>`.
# The tracked listing keeps its sha256, which a reader of the repository could test every guess
# against offline while the reply window runs: 100 bits keeps that out of reach (60 would not,
# for someone with enough hardware). A reply may quote one of 12 to 32 characters, so a
# briefing mailed before the length grew is still answered while its window runs.
REFERENCE_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
REFERENCE_LENGTH = 20
REFERENCE_RE = re.compile(r"\bref(?:erence)?\s*:?\s*([a-z2-7]{12,32})\b", re.I)
REFERENCES_KEPT = 10   # per day: a view rendered again before the send keeps the earlier ones
REFERENCE_DAYS = 7     # a reply to a briefing older than this is not read
MORNING = Path("_meta") / "state" / "morning"
# Counts that tell the person something and ask nothing: they rank after every kind that waits on
# them, so hundreds of attachments to read never push a change waiting for one click off the mail.
INFORMATION = ("landed_silently", "meetings", "unread_attachments", "health")

# One plain label per item kind for the email's counts (they pass the plain-language check).
LABELS = {
    "leaving": "updates leaving for another space within a day",
    "approved_leaving": "updates you approved, leaving within a day",
    "high_stakes": "high-stakes items waiting for the domain owner",
    "answer": "answers that arrived",
    "request": "requests waiting for you",
    "question": "questions only you can answer",
    "check": "pages where newer evidence disagrees",
    "candidate": "notes waiting for your signature",
    "reconnect": "apps to reconnect",
    "protected_pr": "changes waiting for your one click",
    "fork_pr": "changes proposed from outside, waiting for your review",
    "outbox": "drafts older than two weeks, not sent",
    "classification": "items waiting for a client's classification",
    "doubt": "items held back in doubt",
    "landed_silently": "updates that landed here silently this week",
    "health": "things your agent is fixing on its own",
    "meetings": "meetings today and tomorrow, with who is who on your page",
    "sample": "samples to look over",
    "security": "briefings that reached an address that is not yours",
    SECURITY_OTHER: "security warnings to look at",
    "unread_attachments": "attachments not yet read",
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
        paths, group = [root / item.payload], item.id.startswith("first-") and item.payload.endswith("/")
        if group:   # one line, every item of it
            sid = item.id[len("first-"):]
            paths = [p for p in sorted((root / item.payload).glob("*.md"))
                     if str(pages.read(p)[0].get("first_release") or "") == sid]
        for path in paths:
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
            stamped.append(str(fm.get("id") or path.stem) if group else item.id)
    return stamped


SEND_ROUTINES = ("morning-view", "morning_view", "daily-chain", "daily")


def _day_index(space: spaceyaml.Space, moment: dt.datetime) -> int:
    """Which scaled day a moment falls in (the UTC date when time_scale is 1)."""
    return int(moment.timestamp() // space.scale(24).total_seconds())


def _sends(root: Path):
    """The time of every briefing sent: any run line of a sending routine whose notes carry
    `email` or `message_id`, whichever routine wrote it. A line that says the send failed does
    not count, a briefing that reached someone else included."""
    path = root / RUNS
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            run = json.loads(line)
        except ValueError:
            continue
        notes = run.get("notes") if isinstance(run, dict) else None
        if not isinstance(notes, dict) or run.get("routine") not in SEND_ROUTINES:
            continue
        if notes.get("failed") or not (notes.get("email") or notes.get("message_id")):
            continue
        try:
            yield pages.parse_iso(run.get("at"))
        except (TypeError, ValueError):
            continue


def sent_today(root, now: dt.datetime | None = None) -> bool:
    """True when a briefing was already sent this scaled day (`_sends`)."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    today = _day_index(space, now)
    return any(_day_index(space, at) == today and at <= now for at in _sends(root))


def _unread_attachments(root: Path) -> int:
    """How many attachments the sweep still lists to read (`sweep.py --attachments`); a count
    that cannot be read is none, never a briefing that fails."""
    try:
        import sweep
        return len(sweep.attachments(root))
    except Exception:
        return 0


def _others(own_address: str, to: list) -> int:
    """How many read-back recipients are not the person. The owner address and each entry may be
    `Name <address>`, and an entry may hold several addresses. An entry, or any piece of one,
    with no address in it could hide one, so it counts as someone else, even beside the
    person's own (`ada@example.test, undisclosed-recipients:;`); so does a read-back with no
    recipient at all, and every address when the space has no one owner address. A display name
    with an unquoted comma (`Okafor, Ada <address>`) reads as a bare name plus the address, so it
    counts as someone else too: the check fails closed, never open."""
    mine = [addr.strip().lower() for _, addr in getaddresses([str(own_address or "")]) if "@" in addr]
    own = mine[0] if len(mine) == 1 else ""
    others, seen = 0, False
    for entry in to:
        if not str(entry).strip():
            continue
        seen = True
        found = [addr.strip().lower() for _, addr in getaddresses([str(entry)])]
        # an empty address never equals the person's, not even when the space has none
        others += sum(1 for addr in found if not addr or addr != own) if found else 1
    return others if seen else 1


def _recipient_item(root: Path, message_id: str, now: dt.datetime) -> None:
    """One security item for a briefing that reached someone else, its id named after the
    message, so a later one is asked again after this one is answered. Answered items leave the
    file; no address is written."""
    path = root / ITEMS
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, yaml.YAMLError):
        data = {}
    rows = data.get("items") if isinstance(data, dict) else None
    answered = needs_you.answered_ids(root)
    items = [i for i in rows if isinstance(i, dict) and i.get("id") not in answered] if isinstance(rows, list) else []
    slug = (re.sub(r"[^a-z0-9]+", "-", message_id.lower()).strip("-")
            or re.sub(r"[^a-z0-9]+", "-", pages.iso(now).lower()).strip("-"))
    item_id = f"{RECIPIENT_ITEM}-{slug}"
    if not any(i.get("id") == item_id for i in items):
        which = f" (message {message_id})" if message_id else ""
        items.append({"id": item_id, "kind": "security",
                      "text": f"Your morning briefing{which} went to an address that is not yours. "
                              "Check the message in your Sent mail.",
                      "recommendation": "check who received it and tell them to delete it",
                      "default": "it stays on this list until you answer", "depends": RECIPIENT_DEPENDS})
    pages.write_text(path, yaml.safe_dump({"items": items}, sort_keys=False, allow_unicode=True, width=100),
                     root=root)


def record_send(root, message_id: str | None = None, failed: str | None = None,
                cost_tokens: int = 0, now: dt.datetime | None = None, to: list | None = None) -> dict:
    """Append the canonical send line; the agent never writes it by hand. `to` is the
    recipients read back from the sent message: any address but the owner's, or an empty list,
    makes the line a failed send (`sent_today` passes it by) and one security item; without
    `to`, nothing is checked."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    notes = {"step": "send"}
    others = _others(space.owner_email, [to] if isinstance(to, str) else to) if to is not None else 0
    if others:
        notes.update(failed="recipient", email=str(message_id or ""), others=others)
        _recipient_item(root, str(message_id or ""), now)
    elif failed:
        notes["failed"] = str(failed)
    else:
        notes["email"] = str(message_id or "")
    line = {"routine": ROUTINE, "at": pages.iso(now), "space": space.name,
            "cost_tokens": int(cost_tokens), "notes": notes}
    pages.append_text(root / RUNS, json.dumps(line) + "\n", root=root)
    return line


def _health_lines(root: Path) -> list:
    path = root / HEALTH
    if not path.exists():
        return []
    try:
        _, body = pages.read(path)
    except Exception:
        return []
    # the door's counts stay on the health page: the briefing says nothing about parked records
    return [line.strip() for line in body.splitlines()
            if line.strip() and not line.startswith("#") and not line.startswith(unsure.HEALTH_PREFIX)]


def _with_stale(root: Path, name: str, lines: list) -> list:
    """This space's stale cell read from `stale.yaml` now, not from the health page status
    wrote at the start of the daily run, so a step that failed after it shows the same day."""
    path = root / STALE
    try:
        stale = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    except (OSError, yaml.YAMLError):
        return lines
    cell = ("stale: " + ", ".join(f"{k} since {str(v)[:10]}" for k, v in sorted(stale.items()))) if stale else ""
    own, out, seen = f"- {name}:", [], False
    for line in lines:
        if line.startswith(own):
            line, seen = line.split(", stale: ")[0] + (f", {cell}" if cell else ""), True
        out.append(line)
    if cell and not seen:
        out.append(f"{own} {cell}")
    return out


# The two lines the sweep writes for a meeting (sweep.write_today): the meeting, then each person.
MEETING_LINE = re.compile(r"^- \d{4}-\d\d-\d\d \d\d:\d\d \S")
PERSON_LINE = re.compile(r"^  - \S")
# What md_inline escapes (an image, a link target, a tag); a line that still holds one once its
# escaped pairs are set aside was not written by the sweep (a page edited by hand, or older).
ESCAPED = re.compile(r"\\.")
LIVE_MARKUP = re.compile(r"!\[|\]\(|<")


def _today_lines(root: Path) -> list:
    """The meetings snapshot the sweep wrote, with who is who: only the lines of the shapes the
    sweep writes (it made their text inert), so a page that holds anything else, one written
    before titles were kept to one line, adds nothing to the list or the count. A line of the
    right shape that still holds an unescaped image, link or tag (a page written by hand or by
    an older sweep) is left out too."""
    path = root / "live" / "today.md"
    if not path.exists():
        return []
    try:
        _, body = pages.read(path)
    except Exception:
        return []
    out = []
    for line in body.splitlines():
        line = line.rstrip()
        if LIVE_MARKUP.search(ESCAPED.sub("", line)):   # an image, link or tag the sweep did not escape
            continue
        if MEETING_LINE.match(line):
            out.append("- " + needs_you.untrusted_line(line[2:], None))
        elif PERSON_LINE.match(line) and out:
            out.append("  - " + needs_you.untrusted_line(line[4:], None))
    return out


def _owned_homes(root: Path, space: spaceyaml.Space) -> list:
    """This space and every mounted space with the same owner: where releases land without
    asking this person (the brain and the projects they own)."""
    homes = [Path(root)]
    for mount in space.mounts:
        target = space.mount_root(str(mount.get("name", "")))
        try:
            if target is not None and spaceyaml.load(target).owner == space.owner:
                homes.append(target)
        except Exception:
            continue
    return homes


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


def _left_ids(lines: list) -> list:
    """The item each numbered Left-yesterday line names, so "left 2 no" pulls it back."""
    import re as _re
    out = []
    for line in lines:
        found = _re.search(r"\b(p-[0-9a-f]{8}|rq-[0-9a-z]+)\b", line)
        out.append(found.group(1) if found else None)
    return out


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


def _fixed_lines(root: Path, space: spaceyaml.Space, now: dt.datetime) -> list:
    """What the machinery repaired by itself in the last scaled day (sync run lines `healed`)."""
    path = root / RUNS
    since = now - space.scale(24)
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []:
        try:
            run = json.loads(line)
            healed = run["notes"]["healed"]
            at = pages.parse_iso(run["at"])
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(healed, dict) and since < at <= now:
            out.append(f"- {healed.get('what') or 'a red check'} (pull request #{healed.get('pr')})")
    return out


def _approved_lines(root: Path, approved: list) -> list:
    """One line per destination for the approved high-stakes items this view lists."""
    groups: dict = {}
    for item in approved:
        dest = str(pages.read(root / item.payload)[0].get("destination") or "its destination")
        groups.setdefault(dest, []).append(item.deadline)
    return [f"- {len(whens)} item{'s' if len(whens) != 1 else ''} for {dest}, from {pages.iso(min(whens))}"
            for dest, whens in sorted(groups.items())]


def _markdown(root: Path, space: spaceyaml.Space, person: str, items: list, now: dt.datetime,
              limit: int, health: list, approved: list = ()) -> str:
    lines = needs_you.render(root, now=now, limit=limit, items=items).rstrip("\n").splitlines()
    lines.insert(1, f"For {person}, as of {pages.iso(now)}.")
    left = _left_yesterday(root, space, now)
    today = _today_lines(root)
    if approved:   # listed here, never asked again; `no <id>` still pulls one back
        lines += ["", "## Leaving, approved by you"] + _approved_lines(root, approved)
    if today:
        lines += ["", "## Today's meetings"] + today
    lines += ["", "## Left yesterday"] + (left or ["Nothing left at once."])
    fixed = _fixed_lines(root, space, now)
    if fixed:
        lines += ["", "## Fixed by itself"] + fixed
    lines += ["", "## Health"] + (health or ["No health line yet."])
    return "\n".join(lines) + "\n"


def _notice(space: spaceyaml.Space, items: list, counts: dict, health: list, shadow: bool) -> dict:
    count = len(items)
    if count == 0:
        title = "Nothing needs you today"
    else:
        title = f"{count} item{'s' if count != 1 else ''} need{'s' if count == 1 else ''} you"
    stats = [{"count": n, "label": LABELS.get(kind, kind.replace("_", " "))}
             for kind, n in sorted(counts.items(),
                                   key=lambda kv: (kv[0] not in ALWAYS_COUNTED, kv[0] in INFORMATION, -kv[1], kv[0]))
             ][:owner_email.LIMITS["stats"]]
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


def _write_json(path: Path, data: dict, root: Path | None = None) -> None:
    pages.write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", root=root)


# ----- the reply reference -------------------------------------------------------------
def new_reference() -> str:
    """A fresh reply reference: REFERENCE_LENGTH random characters, unguessable and never stored."""
    return "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_LENGTH))


def reference_hash(reference: str) -> str:
    """What the day's listing keeps of a reference: a hash, so a reader of the repository (a
    colleague, a cloud clone) learns nothing that would let a mail pass as the person's reply."""
    return hashlib.sha256(f"briefing-reference:{str(reference).lower()}".encode("utf-8")).hexdigest()


def reference_of(root, text: str, now: dt.datetime, days: int = REFERENCE_DAYS) -> str | None:
    """The day (YYYY-MM-DD) of the briefing whose reference `text` quotes, or None: the first
    `ref <code>` in the text (a reply quotes the briefing it answers above older ones) whose hash
    the listing of a day at most `days` before `now` keeps."""
    folder = Path(root) / MORNING
    known: dict = {}
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            day = dt.date.fromisoformat(path.stem)
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not (0 <= (now.date() - day).days <= days) or not isinstance(data, dict):
            continue
        for digest in data.get("references") or []:
            known.setdefault(str(digest), path.stem)
    for found in REFERENCE_RE.finditer(str(text or "")):
        day = known.get(reference_hash(found.group(1)))
        if day:
            return day
    return None


def _kept_references(path: Path) -> list:
    """The reference hashes a listing already keeps (a view of the same day rendered earlier)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return []
    refs = data.get("references") if isinstance(data, dict) else None
    return [str(r) for r in refs if isinstance(r, str)] if isinstance(refs, list) else []


def _count_kind(item) -> str:
    """The kind a mail count files an item under: a security item only from this space (a mounted
    space's payload carries its name before a colon), the recipient item apart from the rest."""
    if item.kind != "security":
        return item.kind
    if ":" in str(item.payload).split("/", 1)[0]:
        return ""
    return "security" if str(item.id).startswith(RECIPIENT_ITEM) else SECURITY_OTHER


def run(root, person: str, now: dt.datetime | None = None, shadow: bool = False,
        limit: int = 7) -> MorningReport:
    root = Path(root)
    pages.refuse_copy(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    shadow = bool(shadow) or space.mode == "shadow"
    items = needs_you.collect(root, now=now)
    counts = dict(Counter(_count_kind(item) for item in items))
    counts.pop("", None)   # a security item another space raised is never counted
    if "doubt" in counts:  # one list line, but the mail counts the held pages
        counts["doubt"] = needs_you.doubt_count(root)
    if any(item.id == "drafts" for item in items):   # one line for many drafts; the mail counts each
        counts["candidate"] += len(needs_you.draft_items(root, space)) - 1
    if "classification" in counts:   # one line too; the mail counts what waits
        counts["classification"] = sum(needs_you.classification_waits(root, space).values())
    if "fork_pr" in counts:   # one line for every pull request from a fork; the mail counts them
        counts["fork_pr"] = needs_you.fork_pr_count(items)
    if "health" in counts:   # one line too; the mail counts what the agent works through
        counts["health"] = len(needs_you.health_work(root))
    silent = sum(_landed_this_week(home, space, now) for home in _owned_homes(root, space))
    if silent:
        counts["landed_silently"] = silent
    meetings = sum(1 for line in _today_lines(root) if not line.startswith("  "))
    if meetings:
        counts["meetings"] = meetings
    approved = needs_you.approved_leaving(root, space, now)
    if approved:
        counts["approved_leaving"] = len(approved)
    unread = _unread_attachments(root)
    if unread:
        counts["unread_attachments"] = unread
    listed = _mark_listed(root, space, items + approved, now)
    health = _with_stale(root, space.name, _health_lines(root))
    page_fm = {"id": _page_id(root), "type": "snapshot", "title": "Needs you", "status": "draft",
               "generated": pages.generated(ROUTINE, now), "person": person}
    if PAGE.parts[0] not in spaceyaml.PROFILE_TABLE.get(space.profile, {}).get("forbidden", []):
        pages.write(root / PAGE, page_fm, _markdown(root, space, person, items, now, limit, health, approved))
    notice, base = _notice(space, items, counts, health, shadow)
    reference = new_reference()   # in the mail only; the listing keeps its hash
    notice["reference"] = reference
    payload = _payload(space, notice, base, shadow, now)
    for legacy in LEGACY:   # before the private zone these were tracked: never send a stale copy
        (root / legacy).unlink(missing_ok=True)
    _write_json(root / NOTICE, notice, root)
    _write_json(root / PAYLOAD, payload, root)
    # the day's numbering, so a reply ("1-4 yes, 5 later") maps to the items as listed, and the
    # hash of every reference a view of the day mailed, so only a reply quoting one is read
    listing = root / MORNING / f"{now:%Y-%m-%d}.json"
    references = (_kept_references(listing) + [reference_hash(reference)])[-REFERENCES_KEPT:]
    _write_json(listing, {"date": f"{now:%Y-%m-%d}", "items": [{"id": i.id, "kind": i.kind, "text": i.text,
                                                                "fingerprint": i.fingerprint} for i in items],
                          "left": _left_ids(_left_yesterday(root, space, now)), "references": references}, root)
    line = {"routine": ROUTINE, "at": pages.iso(now), "space": space.name, "cost_tokens": 0,
            "notes": {"items": len(items), "listed": len(listed), "shadow": shadow,
                      "recipient": bool(payload["to"])}}
    pages.append_text(root / RUNS, json.dumps(line) + "\n", root=root)
    return MorningReport(markdown_path=root / PAGE, notice_path=root / NOTICE,
                         payload_path=root / PAYLOAD, listed=listed, counts=counts)


def _guard_cli(argv: list):
    """`[root] sent-today` and `[root] record-send --id X [--to ADDRESS ...] | --failed REASON [--cost N]`."""
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
    ap.add_argument("--to", nargs="+", action="extend",
                    help="each recipient read back from the sent message: all after one --to, or one --to each")
    opts = ap.parse_args(rest)
    if not opts.message_id and not opts.failed:
        ap.error("give --id <message id> or --failed <reason>")
    print(json.dumps(record_send(root, opts.message_id, opts.failed, opts.cost, to=opts.to)))
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    needs_you.utf8_stdio()
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
