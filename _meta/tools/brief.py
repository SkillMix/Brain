"""brief: what an agent reads before it writes a subject's brief (shape 1).

Understand first (docs/superpowers/specs/2026-10-15-understand-first-design.md, 4.2 and 4.6):
before the router places a record or distill writes a page about a subject, the agent builds or
extends the subject's private brief, `_meta/state/private/briefs/<subject>.md` (never staged,
never signed, disposable). `pack` gathers what to read for it, as JSON:

- `subject`: the registry entry, its umbrella (`part_of`) and its parts (every subject under it);
- `brief`: the brief's path and whether it exists (with `built_at`, `updated_at`, `records_read`);
- `pages`: the signed pages (`status: stable`) of the subject and its parts under
  `knowledge/<kind>/<id>/`, here and at their home through the mounts;
- `status`: their status pages, `live/subjects/<id>.md`, here and at their home;
- `candidate`: the subject's open candidate in `_inbound/candidates/`, if any;
- `evidence`: every witness page, landed record and journal entry here that the subject or one
  of its parts is attached or routed to, names in `mentions`, or whose title or text names its
  title or an alias as a whole word (a split record comes whole);
- `lessons`: the lines of `_meta/lessons.md` for the subject, one of its parts, or all;
- `excluded`: what was left out and why. With `routing.archives` off (the default) every page
  `provenance.is_archive_derived` flags; with `--rebuild` the open candidate.

The agent reads every page the pack lists, in full, then writes the brief from
`_meta/templates/brief.md`. This tool never writes the brief and never runs git.

    python brief.py <root> pack <subject> [--out <path>] [--rebuild]
    python brief.py <root> path <subject>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import distill  # noqa: E402
import pages  # noqa: E402
import registry as registry_mod  # noqa: E402
import spaceyaml  # noqa: E402
from provenance import is_archive_derived  # noqa: E402

BRIEFS = spaceyaml.PRIVATE_STATE / "briefs"
LESSONS = Path("_meta") / "lessons.md"
CANDIDATES = Path("_inbound") / "candidates"
# - <YYYY-MM-DD> <route|distill|all> [<subject>]: <lesson> (from: ...); the subject is optional
LESSON_RE = re.compile(r"^-\s+\d{4}-\d{2}-\d{2}\s+(?:route|distill|all)(?:\s+\[?([a-z0-9][a-z0-9-]*)\]?)?\s*:")
ARCHIVE_WHY = "archive-derived: routing.archives is off"
REBUILD_WHY = "the open candidate: it is being rebuilt from scratch (--rebuild)"


class BriefError(ValueError):
    pass


def brief_path(subject: str) -> Path:
    return BRIEFS / f"{subject}.md"


def _subject(reg, text: str):
    subject = reg.get(text) or reg.find(text)
    if subject is None:
        raise BriefError(f"unknown subject `{text}`: not in the registry here or in a mounted space")
    return subject


def parts_of(reg, sid: str) -> list:
    """Every subject under `sid` by `part_of`, however deep."""
    return [s for s in reg.everywhere() if any(a.id == sid for a in reg.ancestors_of(s.id))]


def _shown(root: Path, path: Path) -> str:
    """A path as the agent opens it: relative to this space when inside it, else absolute."""
    for base, target in ((root, path), (root.resolve(), path.resolve())):
        try:
            return target.relative_to(base).as_posix()
        except ValueError:
            continue
    return path.as_posix()


def _text(value):
    return None if value in (None, "") else (value if isinstance(value, (int, float)) else str(value))


def journal_entries(root) -> list:
    """Every entry of the daily notes, attached or not. Distill's journal evidence keeps only the
    entries that name a subject by a `subject:` line or a desk path; a brief also reads the ones
    that name it in their words."""
    root = Path(root)
    base = root / distill.JOURNAL
    found = []
    for path in sorted(base.rglob("*.md")) if base.is_dir() else []:
        day = distill.JOURNAL_FILE.search(path.name)
        if not day:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for chunk in re.split(r"(?m)^(?=## )", text):
            if not chunk.startswith("## "):
                continue
            head, _, body = chunk.partition("\n")
            match = distill.ENTRY_HEAD.match(head.strip())
            hour, minute, title = (match.group(1), match.group(2), match.group(3)) if match else (None, None, head[3:])
            try:
                at = pages.parse_iso(f"{day.group(1)}T{int(hour or 12):02d}:{int(minute or 0):02d}:00Z")
            except (TypeError, ValueError):
                continue
            subjects = list(dict.fromkeys(distill.SUBJECT_LINE.findall(body) + distill.DESK_PATH.findall(body)))
            found.append(distill.Evidence(record=f"journal-{at:%Y%m%dT%H%M}", rel=rel, at=at, arrived=at,
                                          title=title.strip() or f"journal {day.group(1)}", body=body,
                                          subjects=subjects, landed=False, fm={"source": "journal"}))
    return found


def _about(ev, targets: dict) -> list:
    """How a page is about the subject or a part: `attached <id>` (its subject, a route here, a
    person the router named), `mentioned <id>`, or `named <id>` (title or alias, whole word)."""
    why = []
    mentions = {str(m) for m in ev.fm.get("mentions") or []}
    text = f"{ev.title}\n{ev.body}"
    for sid, subject in targets.items():
        if sid in ev.subjects:
            why.append(f"attached {sid}")
        elif sid in mentions:
            why.append(f"mentioned {sid}")
        elif any(registry_mod._word_in(text, name) for name in [subject.title] + list(subject.aliases) if name):
            why.append(f"named {sid}")
    return why


def lessons_for(root, ids) -> list:
    """The lines of `_meta/lessons.md` whose subject is one of `ids`, `all`, or none given."""
    path = Path(root) / LESSONS
    out = []
    for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
        match = LESSON_RE.match(line.strip())
        if match and (match.group(1) in (None, "all") or match.group(1) in ids):
            out.append(line.strip())
    return out


def _signed_pages(base: Path, subject):
    folder = base / "knowledge" / subject.kind / subject.id
    for path in sorted(folder.rglob("*.md")) if folder.is_dir() else []:
        if not pages.is_page_path(path):
            continue
        try:
            fm, body = pages.read(path)
        except Exception:
            continue
        if str(fm.get("status") or "") == "stable":
            yield path, fm, body


def pack(root, subject: str, rebuild: bool = False) -> dict:
    """The pack for one subject (see the module docstring); raises BriefError when unknown."""
    root = Path(root)
    space = spaceyaml.load(root)
    reg = registry_mod.Registry(root, space)
    found = _subject(reg, subject)
    sid = found.id
    parts = parts_of(reg, sid)
    targets = {s.id: s for s in [found] + parts}
    archives = bool(getattr(space, "routing_archives", False))
    excluded = []

    def kept(rel: str, fm: dict, record: str | None = None) -> bool:
        if archives or not is_archive_derived(fm):
            return True
        excluded.append(dict({"path": rel}, **({"record": record} if record else {}), why=ARCHIVE_WHY))
        return False

    # evidence: witness pages and landed records as distill gathers them, and every journal entry
    gathered = [ev for ev in distill.gather_evidence(root, distill.Reader(), space.name)
                if ev.fm.get("source") != "journal"] + journal_entries(root)
    why = {id(ev): _about(ev, targets) for ev in gathered}
    whole = {ev.record.split("#")[0] for ev in gathered if why[id(ev)] and ev.fm.get("source") != "journal"}
    evidence = []
    for ev in sorted(gathered, key=lambda e: (e.at, e.rel, e.record)):
        reasons = why[id(ev)]
        base = ev.record.split("#")[0]
        if not reasons and ev.fm.get("source") != "journal" and base in whole:
            reasons = [f"part of {base}"]
        if not reasons or not kept(ev.rel, ev.fm, ev.record):
            continue
        journal = ev.fm.get("source") == "journal"
        evidence.append({"path": ev.rel, "record": ev.record, "at": pages.iso(ev.at),
                         "source": _text(ev.fm.get("source")),
                         "kind": "note" if journal else _text(ev.fm.get("kind")),
                         "title": ev.title, "characters": len(ev.body), "why": reasons})

    # signed pages and status pages, here and at each one's home through the mounts
    signed, status = [], []
    for s in targets.values():
        places = [(space.name, root)]
        home = reg.home_of(s.id)
        if home and home != space.name and space.mount_root(home) is not None:
            places.append((home, space.mount_root(home)))
        for name, base in places:
            for path, fm, body in _signed_pages(base, s):
                rel = _shown(root, path)
                if kept(rel, fm):
                    signed.append({"subject": s.id, "space": name, "path": rel,
                                   "title": str(fm.get("title") or path.stem),
                                   "signed_at": _text(fm.get("signed_at")), "characters": len(body)})
            page = base / "live" / "subjects" / f"{s.id}.md"
            if page.is_file():
                status.append({"subject": s.id, "space": name, "path": _shown(root, page)})

    candidate = None
    path = root / CANDIDATES / f"{sid}.md"
    if path.is_file():
        fm, body = pages.read(path)
        rel = path.relative_to(root).as_posix()
        if fm.get("released_from"):
            pass   # a landed record from another space: evidence above, not the open candidate
        elif rebuild:
            excluded.append({"path": rel, "why": REBUILD_WHY})
        elif kept(rel, fm):
            candidate = {"path": rel, "title": str(fm.get("title") or sid), "status": _text(fm.get("status")),
                         "evidence": len(fm.get("evidence") or []), "characters": len(body)}

    rel = brief_path(sid).as_posix()
    brief = {"path": rel, "exists": (root / rel).is_file()}
    if brief["exists"]:
        try:
            brief_fm = pages.read(root / rel)[0]
        except Exception:
            brief_fm = {}
        for key in ("built_at", "updated_at", "records_read"):
            if _text(brief_fm.get(key)) is not None:
                brief[key] = _text(brief_fm.get(key))

    umbrella = reg.get(found.part_of) if found.part_of else None
    return {"subject": {"entry": found.to_dict(), "space": found.space or space.name,
                        "home": reg.home_of(sid), "owner": reg.owner_of(sid),
                        "umbrella": umbrella.to_dict() if umbrella else None,
                        "parts": [p.to_dict() for p in parts]},
            "brief": brief, "pages": signed, "status": status, "candidate": candidate,
            "evidence": evidence, "lessons": lessons_for(root, set(targets)),
            "excluded": excluded, "archives": archives}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_pack = sub.add_parser("pack")
    p_pack.add_argument("subject")
    p_pack.add_argument("--out")
    p_pack.add_argument("--rebuild", action="store_true")
    p_path = sub.add_parser("path")
    p_path.add_argument("subject")
    args = ap.parse_args(argv)
    root = Path(args.root)
    try:
        if args.cmd == "path":
            reg = registry_mod.Registry(root, spaceyaml.load(root))
            print((root / brief_path(_subject(reg, args.subject).id)).as_posix())
            return 0
        result = pack(root, args.subject, rebuild=args.rebuild)
    except BriefError as exc:
        print(f"brief: {exc}")
        return 2
    sid = result["subject"]["entry"]["id"]
    out = Path(args.out) if args.out else root / BRIEFS / f"{sid}.pack.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8", newline="\n")
    print(f"{sid}: {len(result['evidence'])} evidence, {len(result['pages'])} signed page(s), "
          f"{len(result['excluded'])} left out; brief {result['brief']['path']} "
          f"({'exists' if result['brief']['exists'] else 'to build'}); pack in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
