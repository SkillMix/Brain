"""Needs you: the one list of everything that needs a person in this space (shape 1).

Every routine writes its own items into `_inbound/needs-you/<routine>.yaml` (one writer
per file). This module gathers them together with the items it derives from the space
itself: candidates and checks under `_inbound/`, releases leaving within the window,
high-stakes items, questions, answers that arrived, stale outbox drafts, and the items
held in doubt as one count. Each item says what it is, what the agent recommends and
what happens if nothing is done, and by when. Ordered by deadline, then by how much
depends on the answer, then by kind. `sign` is the one verb over any item.

    python needs_you.py [root] [--json] [--limit N]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

KIND_ORDER = ["leaving", "high_stakes", "answer", "request", "question", "check", "candidate",
              "reconnect", "protected_pr", "outbox", "doubt", "sample"]
VERBS_LINE = "Answer with the number and yes, edit, no, later or always; then submit."
OUTBOX_DAYS = 14
# A verdict line in log.md, as sign.apply writes it: "- <ISO> <verb> <id> by <name>".
VERDICT_LINE = re.compile(r"^- (\S+) (yes|edit|no|always) (\S+) by ")

CANDIDATES = Path("_inbound") / "candidates"
CHECKS = Path("_inbound") / "checks"
QUESTIONS = Path("_inbound") / "questions"
ANSWERS = Path("_inbound") / "answers"
NEEDS_YOU = Path("_inbound") / "needs-you"
QUEUE = Path("_outbound") / "queue"
HELD = Path("_outbound") / "held"
OUTBOX = Path("outbox")


@dataclass
class Item:
    id: str
    kind: str
    text: str
    recommendation: str = ""
    default: str = ""
    deadline: dt.datetime | None = None
    depends: int = 0
    payload: str = ""
    fingerprint: str = ""
    check: str = ""        # the read-back that proves it is done, for an owner step
    for_: str = ""         # whom it is for, when not the space's owner

    def to_dict(self) -> dict:
        data = asdict(self)
        data["deadline"] = pages.iso(self.deadline) if self.deadline else None
        return data


# ----- reading -------------------------------------------------------------------
def pages_in(root: Path, folder: Path):
    """(relpath, frontmatter, body) for every readable page directly under a folder."""
    base = Path(root) / folder
    if not base.is_dir():
        return
    for path in sorted(base.glob("*.md")):
        try:
            fm, body = pages.read(path)
        except Exception:
            continue
        if fm:
            yield path.relative_to(root).as_posix(), fm, body


def _when(value) -> dt.datetime | None:
    if not value:
        return None
    try:
        return pages.parse_iso(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value, fallback: str) -> str:
    return str(value) if value not in (None, "") else fallback


def routine_items(root: Path) -> list:
    """Items routines wrote into `_inbound/needs-you/*.yaml`, as written."""
    out = []
    base = Path(root) / NEEDS_YOU
    if not base.is_dir():
        return out
    for path in sorted(base.glob("*.yaml")):
        rel = path.relative_to(root).as_posix()
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            # never drop a routine's items silently: the broken file itself needs a person
            mark = getattr(exc, "problem_mark", None)
            where = f" (line {mark.line + 1})" if mark else ""
            out.append(Item(id=f"unreadable-{path.stem}", kind="request",
                            text=f"{rel} cannot be read{where}: its items are missing from this list",
                            recommendation="fix the file; the items come back on the next run",
                            payload=rel))
            continue
        rows = data.get("items") if isinstance(data, dict) else data
        for raw in rows or []:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            out.append(Item(id=str(raw["id"]), kind=_text(raw.get("kind"), "request"),
                            text=_text(raw.get("text"), str(raw["id"])),
                            recommendation=_text(raw.get("recommendation"), ""),
                            default=_text(raw.get("default"), ""), deadline=_when(raw.get("deadline")),
                            depends=_int(raw.get("depends")),
                            payload=_text(raw.get("payload"), rel),
                            fingerprint=_text(raw.get("fingerprint"), ""), check=_text(raw.get("check"), ""),
                            for_=_text(raw.get("for"), "")))
    return out


def answered_ids(root: Path) -> set:
    """Ids with a recorded verdict in log.md (anything but `later`): no longer asked."""
    log = Path(root) / "log.md"
    if not log.exists():
        return set()
    found = set()
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        match = VERDICT_LINE.match(line.strip())
        if match:
            found.add(match.group(3))
    return found


# ----- the kinds -----------------------------------------------------------------
def _candidates(root: Path) -> list:
    out = []
    for rel, fm, body in pages_in(root, CANDIDATES):
        title = _text(fm.get("title"), Path(rel).stem)
        subject = fm.get("subject")
        text = f"{title} for {subject}: sign?" if subject else f"{title}: sign?"
        if fm.get("review_batch"):
            open_lines = [l for l in fm.get("lines") or [] if not l.get("answered")]
            text = (f"{title}: {len(open_lines)} pages to confirm, retire or correct; "
                    f"answer by line ({Path(rel).stem}#<n>)")
        elif fm.get("landing"):  # a release that arrived and waits (landing.py says why)
            who = f", for {fm['for']}" if fm.get("for") else ""
            text = f"{title} from {fm.get('released_from', 'another space')} ({fm['landing']}{who}): sign?"
        out.append(Item(id=_text(fm.get("id"), Path(rel).stem), kind="candidate",
                        text=text,
                        recommendation=_text(fm.get("recommendation"), "yes"), default="stays a draft",
                        deadline=_when(fm.get("deadline")), depends=_int(fm.get("depends")),
                        payload=rel, fingerprint=pages.fingerprint(body)))
    return out


def _checks(root: Path) -> list:
    out = []
    for rel, fm, body in pages_in(root, CHECKS):
        out.append(Item(id=_text(fm.get("id"), Path(rel).stem), kind="check",
                        text=_text(fm.get("title"), Path(rel).stem),
                        recommendation=_text(fm.get("recommendation"), ""), default="page stays as it is",
                        deadline=None, depends=_int(fm.get("depends")), payload=rel,
                        fingerprint=pages.fingerprint(body)))
    return out


def _queue(root: Path, space: spaceyaml.Space, now: dt.datetime) -> list:
    out = []
    horizon = now + space.window()
    for rel, fm, body in pages_in(root, QUEUE):
        title = _text(fm.get("title"), Path(rel).stem)
        destination = _text(fm.get("destination"), "its destination")
        stage_after = _when(fm.get("stage_after"))
        common = dict(id=_text(fm.get("id"), Path(rel).stem), depends=_int(fm.get("depends")),
                      payload=rel, fingerprint=pages.fingerprint(body))
        if fm.get("high_stakes"):
            out.append(Item(kind="high_stakes", text=f"{title} for {destination}: high stakes",
                            recommendation=_text(fm.get("recommendation"), "yes"),
                            default="waits for the domain owner", deadline=None, **common))
        elif fm.get("class") == "window" and stage_after is not None and stage_after <= horizon:
            out.append(Item(kind="leaving",
                            text=f"{title} leaves for {destination} at {pages.iso(stage_after)}",
                            recommendation=_text(fm.get("recommendation"), "yes"), default="leaves",
                            deadline=stage_after, **common))
    return out


def _outbox(root: Path, space: spaceyaml.Space, now: dt.datetime) -> list:
    out = []
    base = Path(root) / OUTBOX
    if not base.is_dir():
        return out
    stale = space.scale(OUTBOX_DAYS * 24)
    for path in sorted(base.glob("*.md")):
        try:
            fm, body = pages.read(path)
        except Exception:
            fm, body = {}, ""
        gen = fm.get("generated") if isinstance(fm.get("generated"), dict) else {}
        since = _when(gen.get("at") or fm.get("at") or fm.get("created"))
        if since is None:
            since = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
        if now - since <= stale:
            continue
        rel = path.relative_to(root).as_posix()
        out.append(Item(id=_text(fm.get("id"), path.stem), kind="outbox",
                        text=f"{_text(fm.get('title'), path.stem)} has waited in the outbox since "
                             f"{pages.iso(since)[:10]}",
                        recommendation="send it yourself, or no to drop it", default="archived silently",
                        deadline=None, payload=rel, fingerprint=pages.fingerprint(body)))
    return out


def _questions(root: Path) -> list:
    out = []
    for rel, fm, body in pages_in(root, QUESTIONS):
        out.append(Item(id=_text(fm.get("id"), Path(rel).stem), kind="question",
                        text=_text(fm.get("question"), _text(fm.get("title"), Path(rel).stem)),
                        recommendation=_text(fm.get("recommendation"), ""),
                        default=_text(fm.get("default"), "the recommendation stands"),
                        deadline=_when(fm.get("deadline")), depends=_int(fm.get("depends")),
                        payload=rel, fingerprint=pages.fingerprint(body)))
    return out


def _answers(root: Path) -> list:
    out = []
    for rel, fm, body in pages_in(root, ANSWERS):
        out.append(Item(id=_text(fm.get("id"), Path(rel).stem), kind="answer",
                        text=_text(fm.get("title"), Path(rel).stem),
                        recommendation=_text(fm.get("recommendation"), "yes"),
                        default=_text(fm.get("default"), "stays where it is"),
                        deadline=_when(fm.get("deadline")), depends=_int(fm.get("depends")),
                        payload=rel, fingerprint=pages.fingerprint(body)))
    return out


def doubt_pages(root) -> list:
    """The pages held at home in doubt that nobody has answered yet."""
    return [(rel, fm, body) for rel, fm, body in pages_in(Path(root), HELD)
            if fm.get("reason") == "doubt" and not fm.get("kept")]


def doubt_count(root) -> int:
    """How many pages are held at home in doubt (one Needs-you line counts them all)."""
    return len(doubt_pages(root))


def doubt_items(root) -> list:
    """The doubt line opened: one item per held page, its reason and a recommendation."""
    out = []
    for rel, fm, body in doubt_pages(Path(root)):
        dest = fm.get("destination") or ", ".join(fm.get("candidates") or []) or "nowhere yet"
        out.append(Item(id=str(fm.get("id") or Path(rel).stem), kind="doubt",
                        text=f"{fm.get('title') or Path(rel).stem} (to {dest}): {fm.get('why') or 'privacy doubt'}",
                        recommendation=_text(fm.get("recommendation"), "no: keep it home"),
                        default=_text(fm.get("default"), "stays home"), payload=rel,
                        fingerprint=pages.fingerprint(body)))
    return out


def _doubt(root: Path) -> list:
    count = doubt_count(root)
    if not count:
        return []
    return [Item(id="doubt", kind="doubt", text=f"held in doubt: {count}",
                 recommendation="leave them home", default="stay home", deadline=None,
                 payload=HELD.as_posix() + "/", fingerprint="")]


# ----- one list per person: items routed to them in the spaces they mount -------------------
def _names_person(value, person: str) -> bool:
    if not person or value in (None, ""):
        return False
    values = value if isinstance(value, list) else [value]
    return any(str(v).strip().lower() == person.strip().lower() for v in values)


def routed_items(root: Path, space: spaceyaml.Space, person: str) -> list:
    """Items in a mounted space that name this person: a landing pending for them (`for`), a
    check routed to them first, a librarian or question item `for` them. Shown with the
    space's name; answered in that space."""
    out = []
    for mount in space.mounts:
        name = str(mount.get("name", ""))
        mroot = space.mount_root(name)
        if mroot is None:
            continue
        for rel, fm, body in pages_in(mroot, CANDIDATES):
            if _names_person(fm.get("for"), person):
                out.append(Item(id=_text(fm.get("id"), Path(rel).stem), kind="candidate",
                                text=f"[{name}] {_text(fm.get('title'), Path(rel).stem)} ({fm.get('landing', 'pending')}): sign?",
                                recommendation=_text(fm.get("recommendation"), "yes"), default="stays pending",
                                payload=f"{name}:{rel}", fingerprint=pages.fingerprint(body)))
        for rel, fm, body in pages_in(mroot, CHECKS):
            route = fm.get("route") or []
            if route and _names_person(route[0], person):
                out.append(Item(id=_text(fm.get("id"), Path(rel).stem), kind="check",
                                text=f"[{name}] {_text(fm.get('title'), Path(rel).stem)}",
                                recommendation=_text(fm.get("recommendation"), ""), default="page stays as it is",
                                payload=f"{name}:{rel}", fingerprint=pages.fingerprint(body)))
        for rel, fm, body in pages_in(mroot, QUESTIONS):
            if _names_person(fm.get("for"), person):
                out.append(Item(id=_text(fm.get("id"), Path(rel).stem), kind="question",
                                text=f"[{name}] {_text(fm.get('question'), _text(fm.get('title'), Path(rel).stem))}",
                                recommendation=_text(fm.get("recommendation"), ""),
                                default=_text(fm.get("default"), "the recommendation stands"),
                                payload=f"{name}:{rel}", fingerprint=pages.fingerprint(body)))
        for item in routine_items(mroot):
            if _names_person(getattr(item, "for_", None), person):
                item.text = f"[{name}] {item.text}"
                item.payload = f"{name}:{item.payload}"
                out.append(item)
    return out


# ----- the list ------------------------------------------------------------------
def order(items: list) -> list:
    far = dt.datetime.max.replace(tzinfo=dt.timezone.utc)
    rank = {kind: n for n, kind in enumerate(KIND_ORDER)}
    return sorted(items, key=lambda i: (i.deadline is None, i.deadline or far, -i.depends,
                                        rank.get(i.kind, len(rank)), i.id))


def _protected_prs(root: Path) -> list:
    """Open pull requests on this repository that auto-merge did not arm: protected paths wait
    for the owner's one click (rules.md section 8). Silent when offline or not a repository."""
    import json
    import subprocess
    if not (root / ".git").exists():
        return []
    try:
        res = subprocess.run(["gh", "pr", "list", "--state", "open", "--json",
                              "number,title,url,autoMergeRequest,isDraft"],
                             capture_output=True, text=True, cwd=str(root), timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if res.returncode != 0:
        return []
    try:
        prs = json.loads(res.stdout or "[]")
    except ValueError:
        return []
    return [Item(id=f"pr-{pr['number']}", kind="protected_pr",
                 text=f"pull request {pr['number']} waits for your merge: {pr.get('title', '')}",
                 recommendation="merge it if the change is yours or you have read it",
                 default="it waits", depends=2, payload=str(pr.get("url", "")))
            for pr in prs if not pr.get("autoMergeRequest") and not pr.get("isDraft")]


def collect(root, now: dt.datetime | None = None, online: bool = True, person: str | None = None) -> list:
    """Every item that needs a person in this space, in the order to show them; in a personal
    space, also the items routed to its person in the spaces it mounts (one list per person)."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    answered = answered_ids(root)
    items = (_candidates(root) + _checks(root) + _queue(root, space, now) + _outbox(root, space, now)
             + _questions(root) + _answers(root)
             + [i for i in routine_items(root) if i.id not in answered] + _doubt(root)
             + (_protected_prs(root) if online else []))
    person = person if person is not None else (space.owner if space.profile == "personal" else "")
    if person:
        seen = {i.id for i in items}
        items += [i for i in routed_items(root, space, person) if i.id not in seen]
    return order(items)


def render(root, now: dt.datetime | None = None, limit: int = 7, items: list | None = None) -> str:
    """The list as markdown: about `limit` lines, the rest as a count, the five verbs last."""
    items = collect(root, now) if items is None else items
    count = len(items)
    lines = [f"# Needs you ({count} item{'s' if count != 1 else ''})", ""]
    if not items:
        lines.append("Nothing needs you.")
        return "\n".join(lines) + "\n"
    for n, item in enumerate(items[:limit], 1):
        text = item.text if item.text.endswith((".", "?", "!")) else item.text + "."
        when = pages.iso(item.deadline) if item.deadline else "no deadline"
        lines.append(f"{n}. {text} I recommend: {item.recommendation or 'your call'}. "
                     f"If nothing: {item.default or 'nothing changes'} ({when})."
                     + (f" Read-back: {item.check}." if item.check else ""))
    if count > limit:
        lines.append(f"and {count - limit} more")
    lines += ["", VERBS_LINE]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--json", action="store_true", help="print the items as JSON instead of markdown")
    ap.add_argument("--limit", type=int, default=7, help="lines to show before `and N more`")
    ap.add_argument("--doubt", action="store_true", help="open the held-in-doubt line: one item each")
    args = ap.parse_args(argv)
    if args.doubt:
        items = doubt_items(args.root)
        if args.json:
            print(json.dumps([i.to_dict() for i in items], indent=2, ensure_ascii=False))
        else:
            for n, item in enumerate(items, 1):
                print(f"{n}. {item.id}: {item.text} I recommend: {item.recommendation}. "
                      f"If nothing: {item.default}.")
            if not items:
                print("Nothing is held in doubt.")
        return 0
    items = collect(args.root)
    if args.json:
        print(json.dumps([i.to_dict() for i in items], indent=2, ensure_ascii=False))
    else:
        print(render(args.root, limit=args.limit, items=items), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
