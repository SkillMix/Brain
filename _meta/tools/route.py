"""route: the judgment router's tool (docs/superpowers/specs/2026-10-14-judgment-routing-design.md).

An agent reads each new witness page in full and decides where it belongs: it stays private, or
it informs a subject in a space, at once or after the window, whole or as verbatim parts. This
tool lists what waits, validates a decision, applies the gates, writes the projections into
`_outbound/queue/`, logs every decision and stamps the witness page. It never decides a route.

Gates (refused: nothing written, the reason printed): the destination must be the subject's
home; every part must be a verbatim span of the record (whitespace aside); `whole` only when
everyone on the record is inside the destination's audience (the client's own mail domain
counts as inside). Then per projection: a never-leaves term in it -> held at home; a left-out
term of the destination in it -> held; a never-leaves term elsewhere in the record, medium
confidence, a high-stakes term, or the review period (`routing.review_until`) -> the window;
low confidence -> a question for the owner, nothing leaves. An owner's answer counts as approval.

    python route.py <root> pending [--subject ID] [--out FILE]
    python route.py <root> record FILE [FILE ...]
    python route.py <root> answer <record id> (--yes | --private) --by NAME
    python route.py <root> retract <record id> [--space NAME] [--queued-only] --by NAME
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import registry  # noqa: E402
import release  # noqa: E402
import spaceyaml  # noqa: E402
import sweep  # noqa: E402

LOG = Path("_meta") / "state" / "routing.jsonl"
PACK = spaceyaml.PRIVATE_STATE / "route-pending.json"
QUESTIONS = Path("_inbound") / "questions"
SOURCES = Path("knowledge") / "sources"
DEFAULT_SINCE_DAYS = 90
CONFIDENCE = ("high", "medium", "low")
TIERS = ("at_once", "window")
INFORMS = ("open_item", "decision", "person", "page")


class RouteError(ValueError):
    pass


class AlreadyRouted(RouteError):
    """The record has a decision: a rerun is skipped, never routed twice."""


def _flat(text) -> str:
    return " ".join(str(text or "").split())


def _pages(root: Path):
    base = root / SOURCES
    for path in sorted(base.rglob("*.md")) if base.is_dir() else []:
        if not pages.is_page_path(path):
            continue
        try:
            fm, body = pages.read(path)
        except Exception:
            continue
        if fm.get("type") == "source" and fm.get("record"):
            yield path, fm, body


def find_page(root, record_id) -> Path | None:
    """The witness page of a record: by its file name first (no full scan), else by a scan."""
    root = Path(root)
    base = root / SOURCES
    name = glob.escape(f"{sweep.file_name(record_id)}.md")
    for path in sorted(base.glob(f"*/*/{name}")) if base.is_dir() else []:
        try:
            if str(pages.read(path)[0].get("record")) == str(record_id):
                return path
        except Exception:
            continue
    for path, fm, _ in _pages(root):
        if str(fm.get("record")) == str(record_id):
            return path
    return None


def _full_text(root: Path, fm: dict, body: str) -> str:
    """A split record read whole: part 1 and every further part, in order."""
    parts = int(fm.get("parts") or 1)
    if parts == 1:
        return body
    texts = [body]
    for path in sweep._part_paths(root, {"id": str(fm.get("record")), "at": fm.get("at")}, parts)[1:]:
        texts.append(pages.read(path)[1] if path.exists() else "")
    return "".join(texts)


def _questioned(root: Path) -> set:
    out = set()
    base = root / QUESTIONS
    for path in base.glob("route-*.md") if base.is_dir() else []:
        try:
            out.add(str(pages.read(path)[0].get("record")))
        except Exception:
            continue
    return out


def _record_of(root: Path, fm: dict, body: str) -> dict:
    ident = fm.get("identifiers") if isinstance(fm.get("identifiers"), dict) else {}
    return {"record": str(fm.get("record")), "title": str(fm.get("title") or ""), "kind": fm.get("kind"),
            "at": fm.get("at"), "from": fm.get("from"), "to": list(fm.get("to") or []),
            "cc": list(fm.get("cc") or []), "participants": list(fm.get("participants") or []),
            "thread": ident.get("thread"), "identifiers": ident, "text": _full_text(root, fm, body)}


def _as_record(rec: dict) -> dict:
    """The record in the shape the registry matches."""
    return {"id": rec["record"], "subject": rec["title"], "text": rec["text"], "kind": rec["kind"],
            "at": rec["at"], "from": rec["from"], "to": rec["to"], "cc": rec["cc"],
            "participants": rec["participants"], "identifiers": rec["identifiers"]}


def pending(root, subject: str | None = None, now: dt.datetime | None = None, limit: int | None = None) -> dict:
    """What waits for a decision, with the context to decide it: unrouted witness pages since
    `routing.since` (90 days by default), or, for a project's backfill, every unrouted page that
    names the subject or carries one of its identifiers, however old."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    reg = registry.Registry(root, space)
    since = space.routing_since or (now - dt.timedelta(days=DEFAULT_SINCE_DAYS))
    wanted = reg.get(subject) if subject else None
    asked = _questioned(root)
    records = []
    for path, fm, body in _pages(root):
        if fm.get("routing") or str(fm.get("record")) in asked or int(fm.get("part") or 1) != 1:
            continue
        rec = _record_of(root, fm, body)
        try:
            at = pages.parse_iso(rec["at"])
        except (TypeError, ValueError):
            continue
        shaped = _as_record(rec)
        if subject:
            if wanted is None or not (wanted in reg.mentions(shaped) or reg.matched_by(wanted, shaped)):
                continue
        elif at < since:
            continue
        rec["suggested"] = [s.id for s in reg.match(shaped)]
        rec["mentions"] = list(fm.get("mentions") or [])
        rec["path"] = path.relative_to(root).as_posix()
        records.append(rec)
    records.sort(key=lambda r: (str(r.get("thread") or r["record"]), str(r["at"])))
    if limit:
        records = records[:limit]
    homes = sorted({h for h in (reg.home_of(s.id) for s in reg.everywhere()) if h and h != space.name})
    spaces = []
    for name in homes:
        dest = release.destination_space(space, name)
        spaces.append({"name": name, "profile": dest.profile if dest else "unknown",
                       "audience": dest.audience if dest else "unknown",
                       "left_out": release.left_out_of(dest)})
    subjects = [{"id": s.id, "kind": s.kind, "title": s.title, "home": reg.home_of(s.id),
                 "aliases": list(s.aliases), "match": dict(s.match)} for s in reg.everywhere()]
    status_pages = {}
    for s in reg.everywhere():
        home = reg.home_of(s.id)
        base = space.mount_root(home) if home and home != space.name else root
        page = base / "live" / "subjects" / f"{s.id}.md" if base is not None else None
        if page is not None and page.exists():
            status_pages[s.id] = page.read_text(encoding="utf-8")
    return {"records": records, "spaces": spaces, "subjects": subjects, "status_pages": status_pages}


def _outside(space, reg, dest_space, subject, rec: dict) -> list:
    """Everyone on the record outside the destination's audience; the client's domain is inside."""
    people = [rec.get("from")] + list(rec.get("to") or []) + list(rec.get("cc") or []) + \
        list(rec.get("participants") or [])
    seen, out = set(), []
    for person in sweep.outsiders(space, dest_space, [p for p in people if p], reg, subject):
        key = registry.address_of(person)
        if key not in seen:
            seen.add(key)
            out.append(person)
    return out


def _projection(rec: dict, route_: dict) -> str:
    if route_.get("content") == "whole":
        return rec["text"]
    chunks = []
    for part in route_.get("parts") or []:
        note = str(part.get("note") or "").strip()
        chunks.append((f"_{note}_\n\n" if note else "") + str(part["text"]).strip())
    return "\n\n".join(chunks) + "\n"


def _validate(root: Path, space, reg, decision: dict, strict: bool = True) -> tuple:
    """The decision against the record; `strict` False (a question for the owner) checks only
    that the record exists, waits for a decision and the decision is well formed."""
    rid = str(decision.get("record") or "")
    path = find_page(root, rid)
    if path is None:
        raise RouteError(f"{rid}: no witness page for that record")
    fm, body = pages.read(path)
    routing = fm.get("routing") if isinstance(fm.get("routing"), dict) else {}
    if routing and routing.get("decision") != "retracted":
        raise AlreadyRouted(f"{rid}: already routed ({routing.get('decision')}); retract it first")
    if decision.get("decision") not in ("private", "route"):
        raise RouteError(f"{rid}: decision must be private or route")
    if decision.get("confidence") not in CONFIDENCE:
        raise RouteError(f"{rid}: confidence must be one of {', '.join(CONFIDENCE)}")
    rec = _record_of(root, fm, body)
    if not strict:
        return path, fm, body, rec
    flat_body = _flat(rec["text"])
    routes = list(decision.get("routes") or [])
    if decision["decision"] == "route" and not routes:
        raise RouteError(f"{rid}: a route decision names at least one route")
    if decision["decision"] == "private" and routes:
        raise RouteError(f"{rid}: a private decision has no routes")
    for r in routes:
        subject = reg.get(str(r.get("subject") or ""))
        if subject is None:
            raise RouteError(f"{rid}: unknown subject {r.get('subject')}")
        home = reg.home_of(subject.id) or space.name
        if str(r.get("space")) != home:
            raise RouteError(f"{rid}: {subject.id}'s home is {home}, not {r.get('space')}")
        if r.get("tier", "window") not in TIERS:
            raise RouteError(f"{rid}: tier must be at_once or window")
        for item in r.get("informs") or []:
            if not isinstance(item, dict) or item.get("kind") not in INFORMS or not str(item.get("text") or "").strip():
                raise RouteError(f"{rid}: each informs item needs a kind ({', '.join(INFORMS)}) and a text")
        if home == space.name:
            continue
        if r.get("content") == "whole":
            outside = _outside(space, reg, release.destination_space(space, home), subject, rec)
            if outside:
                raise RouteError(f"{rid}: whole is refused, people outside {home}'s audience: "
                                 + ", ".join(str(p) for p in outside))
        elif r.get("content") == "parts":
            parts = r.get("parts") or []
            if not parts:
                raise RouteError(f"{rid}: content parts names at least one part")
            for part in parts:
                text = _flat(part.get("text") if isinstance(part, dict) else "")
                if not text or text not in flat_body:
                    raise RouteError(f"{rid}: a part is not verbatim from the record: {text[:60]!r}")
        else:
            raise RouteError(f"{rid}: content must be whole or parts")
        if len(_projection(rec, r).encode("utf-8")) > sweep.PART_BYTES:
            raise RouteError(f"{rid}: the projection for {home} is too large for one release; send parts")
    return path, fm, body, rec


def _log(root: Path, row: dict) -> None:
    release.append_line(root / LOG, json.dumps(row, ensure_ascii=False, default=str))


def _question(root: Path, decision: dict, fm: dict, path: Path, now: dt.datetime) -> str:
    if decision["decision"] == "private":
        plan = "keep it in the workspace"
    else:
        plan = ", ".join(f"{r['space']} ({r['subject']}, {r.get('tier', 'window')})" for r in decision["routes"])
    rel = QUESTIONS / f"route-{sweep.file_name(decision['record'])}.md"
    qfm = {"id": pages.new_id(), "type": "review", "title": f"Route: {fm.get('title')}", "status": "draft",
           "generated": pages.generated("route", now), "record": str(decision["record"]),
           "question": f"Where should '{fm.get('title')}' go? I think: {plan}. Right?",
           "recommendation": f"yes: {plan}", "default": "stays in the workspace",
           "payload": path.relative_to(root).as_posix(), "decision": decision}
    pages.write(root / rel, qfm, f"# Route: {fm.get('title')}\n\n{decision.get('why', '')}\n")
    return rel.as_posix()


def _withdraw(root: Path, record_id: str, reason: str, why: str, space_name: str | None = None,
              router_only: bool = False) -> list:
    out = []
    for qp in sorted((root / release.QUEUE).glob("*.md")):
        qfm, qbody = pages.read(qp)
        if router_only and not qfm.get("routed_by"):
            continue   # an item of the identifier path: the owner's own word decides it
        if record_id in [str(e) for e in qfm.get("evidence") or []] and \
                (space_name is None or qfm.get("destination") == space_name):
            release.hold(root, qfm, qbody, reason, why)
            qp.unlink()
            out.append(qfm["id"])
    return out


def record(root, decision: dict, now: dt.datetime | None = None, by: str | None = None) -> dict:
    """Validate and apply one decision; `by` is a person answering a question (their approval)."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    if not space.routing_on:
        raise RouteError("routing is off in this space (no `routing:` in space.yaml): the sweep releases "
                         "by identifier, and two paths must never release the same record")
    reg = registry.Registry(root, space)
    asks = decision.get("confidence") == "low" and not by
    path, fm, body, rec = _validate(root, space, reg, decision, strict=not asks)
    out = {"queued": [], "held": [], "question": None, "attached": []}
    row = {"at": pages.iso(now), "record": rec["record"], "decision": decision["decision"],
           "confidence": decision["confidence"], "why": decision.get("why", ""), "by": by or "router",
           "routes": []}
    if asks:
        out["question"] = _question(root, decision, fm, path, now)
        _log(root, dict(row, outcome="question"))
        return out
    if fm.get("refreshed_at"):   # a deeper page: its earlier projections still waiting are replaced
        _withdraw(root, rec["record"], "superseded", "replaced by the projection of the refreshed page",
                  router_only=True)
    elsewhere = release.first_term(f"{rec['title']}\n{rec['text']}", space.never_leaves)
    items, here_informs = [], []
    for r in decision.get("routes") or []:
        dest_name, sid = str(r["space"]), str(r["subject"])
        if dest_name == space.name:
            out["attached"].append(f"{dest_name}/{sid}")
            row["routes"].append({"space": dest_name, "subject": sid, "outcome": "attached"})
            here_informs += [dict(i) for i in r.get("informs") or []]
            continue
        dest_space = release.destination_space(space, dest_name)
        content = _projection(rec, r)
        # everything that would travel or explain the projection is checked: its parts, what it
        # informs (text and owner) and the router's reason
        said = " ".join([str(r.get("why") or "")] + [f"{i.get('text', '')} {i.get('owner', '')}"
                                                     for i in r.get("informs") or []])
        projected = f"{rec['title']}\n{content}\n{said}"
        term = release.first_term(projected, space.never_leaves)
        hold_reason = "never_leaves" if term else None
        if not hold_reason:
            term = release.first_term(projected, release.left_out_of(dest_space))
            hold_reason = "left_out" if term else None
        if not hold_reason:
            other = sweep.other_client(space, reg.get(sid), projected)
            if other:
                hold_reason, term = "left_out", f"another client ({other})"
        tier, reasons, extra = r.get("tier", "window"), [str(r.get("why") or "").strip()], {}
        if by:
            extra["approved_by"], extra["path"] = by, f"approved:{by}"
        if elsewhere and not hold_reason:
            tier = "window"
            reasons.append("the record holds never-leaves material elsewhere")
        if decision["confidence"] == "medium" and not by:
            tier = "window"
            reasons.append("medium confidence")
        if release.first_term(projected, space.high_stakes):
            tier = "window"
            extra["high_stakes"] = True
            reasons.append("high-stakes term: lands pending")
        named = sweep.named_person(space, _as_record(rec), projected)
        if named:
            tier = "window"
            extra["high_stakes"] = True
            reasons.append(f"an outside party writes about {named}: lands pending")
        if space.review_until and now < space.review_until and not by:
            tier = "window"
            reasons.append(f"review mode until {space.review_until:%Y-%m-%d}")
        stage_after = now if tier == "at_once" else now + space.window()
        extra.update({"why": "; ".join(x for x in reasons if x), "informs": list(r.get("informs") or []),
                      "confidence": decision["confidence"], "routed_by": by or "router",
                      "content": r.get("content")})
        item = sweep.item_frontmatter(space, _as_record(rec), sid, dest_name, hold_reason or tier,
                                      stage_after, now, extra)
        if hold_reason:
            release.hold(root, item, content, hold_reason,
                         f"{'never-leaves' if hold_reason == 'never_leaves' else 'left-out'} `{term}` in the projection")
            out["held"].append((item["id"], hold_reason))
            row["routes"].append({"space": dest_name, "subject": sid, "outcome": f"held:{hold_reason}",
                                  "item": item["id"]})
        else:
            pages.write(root / release.QUEUE / f"{item['id']}.md", item, content)
            out["queued"].append(item["id"])
            row["routes"].append({"space": dest_name, "subject": sid, "tier": tier, "outcome": "queued",
                                  "item": item["id"]})
        if hold_reason != "never_leaves":
            signal = sweep.signal_for(sid, dest_name, now)
            target = root / release.SIGNALS / f"{signal['id']}.yaml"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(yaml.safe_dump(signal, sort_keys=False), encoding="utf-8", newline="\n")
        items.append(item["id"])
    stamped = dict(fm, routing={
        "decided_at": pages.iso(now), "decision": decision["decision"], "confidence": decision["confidence"],
        "routes": [f"{r['space']}/{r['subject']}" for r in decision.get("routes") or []],
        "items": items, "by": by or "router"})
    if here_informs:   # what it informs in this space stays on the page itself
        stamped["informs"] = list(fm.get("informs") or []) + here_informs
    pages.write(path, stamped, body)
    _log(root, row)
    return out


def answer(root, record_id, yes: bool, by: str, now: dt.datetime | None = None) -> dict:
    """The owner's answer to a routing question: yes applies the proposed routes as approved,
    no keeps the record in the workspace."""
    root = Path(root)
    qpath = root / QUESTIONS / f"route-{sweep.file_name(record_id)}.md"
    if not qpath.exists():
        raise RouteError(f"{record_id}: no routing question waits")
    proposed = dict(pages.read(qpath)[0].get("decision") or {})
    if not yes:
        proposed = {"record": str(record_id), "decision": "private", "routes": [],
                    "why": f"{by} kept it in the workspace"}
    proposed["confidence"] = "high"
    result = record(root, proposed, now=now, by=by)
    qpath.unlink()
    return result if yes else {"private": True}


def retract(root, record_id, by: str, space: str | None = None, now: dt.datetime | None = None,
            queued_only: bool = False) -> dict:
    """Undo a routing: projections still queued are withdrawn, shipped ones retracted.
    `queued_only` changes a decision that still waits: only the router's queued projections are
    withdrawn, and nothing that already left (the owner's own releases included) is retracted."""
    root = Path(root)
    sp = spaceyaml.load(root)
    now = now or sp.now()
    rid = str(record_id)
    out = {"withdrawn": _withdraw(root, rid, "withdrawn", f"withdrawn by {by}", space, router_only=queued_only),
           "retractions": []}
    released = root / release.RELEASED
    lines = [] if queued_only or not released.exists() else released.read_text(encoding="utf-8").splitlines()
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if rid in [str(e) for e in entry.get("evidence") or []] and entry.get("class") != "retraction" and \
                (space is None or entry.get("destination") == space):
            out["retractions"].append(release.retract(root, entry["id"], now=now).relative_to(root).as_posix())
    page = find_page(root, rid)
    if page is not None:
        fm, body = pages.read(page)
        stamp = {"decision": "retracted", "retracted_at": pages.iso(now), "retracted_by": by,
                 "previous": dict(fm.get("routing") or {})}
        pages.write(page, dict(fm, routing=stamp), body)
    _log(root, {"at": pages.iso(now), "record": rid, "decision": "retract", "by": by, "space": space,
                "withdrawn": out["withdrawn"], "retractions": out["retractions"]})
    return out


def _decisions_in(path: Path) -> list:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    if isinstance(data, dict) and "decisions" in data:
        data = data["decisions"]
    return data if isinstance(data, list) else [data]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_pending = sub.add_parser("pending")
    p_pending.add_argument("--subject")
    p_pending.add_argument("--out")
    p_record = sub.add_parser("record")
    p_record.add_argument("files", nargs="+")
    p_answer = sub.add_parser("answer")
    p_answer.add_argument("record_id")
    group = p_answer.add_mutually_exclusive_group(required=True)
    group.add_argument("--yes", action="store_true")
    group.add_argument("--private", action="store_true")
    p_answer.add_argument("--by", required=True)
    p_retract = sub.add_parser("retract")
    p_retract.add_argument("record_id")
    p_retract.add_argument("--space")
    p_retract.add_argument("--by", required=True)
    p_retract.add_argument("--queued-only", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.root)
    if args.cmd == "pending":
        pack = pending(root, subject=args.subject)
        out = Path(args.out) if args.out else root / PACK
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(pack, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        print(f"{len(pack['records'])} record(s) wait for a decision; context in {out}")
        return 0
    if args.cmd == "record":
        refused = 0
        for name in args.files:
            source, failed = Path(name), 0
            for decision in _decisions_in(source):
                try:
                    result = record(root, decision)
                except AlreadyRouted as exc:
                    print(f"skipped: {exc}")
                    continue
                except RouteError as exc:
                    failed += 1
                    print(f"refused: {exc}")
                    continue
                print(f"{decision.get('record')}: queued {len(result['queued'])}, held {len(result['held'])}, "
                      f"attached {len(result['attached'])}" + (", question" if result["question"] else ""))
            refused += failed
            if not failed and source.parent.name != "done":   # recorded: filed, never re-read
                done = source.parent / "done"
                done.mkdir(parents=True, exist_ok=True)
                source.replace(done / source.name)
        return 1 if refused else 0
    if args.cmd == "answer":
        try:
            answer(root, args.record_id, yes=args.yes, by=args.by)
        except RouteError as exc:
            print(f"refused: {exc}")
            return 1
        print(f"{args.record_id}: answered")
        return 0
    result = retract(root, args.record_id, by=args.by, space=args.space, queued_only=args.queued_only)
    print(f"{args.record_id}: withdrawn {len(result['withdrawn'])}, retracted {len(result['retractions'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
