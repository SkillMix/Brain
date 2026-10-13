"""Requests: the fourth wire (vision section 10). A space asks a subject's home for what it
cannot read; almost none of them reach a person.

The asker writes a request as a file; the release leg carries it into the corridor (a
`channel` space between the two, read by those two only); the holder's channel step answers
the part its standing choices cover at once, names the rest without sending it, and lists
the remainder for its person, pre-answered; the answer travels back the same way and lands
in the asker as dated evidence with its tier and age.

    python request.py [root] ask --subject S --class C --question Q [--narrowest N]
                                 [--asked-by NAME] [--force]
    python request.py [root] answer          the holder's channel step (the sync task runs it)
    python request.py [root] collect         the asker lands answers and marks expiries
    python request.py [root] status <rq-id>
    python request.py [root] wait <rq-id> [--timeout MINUTES]    the helper a live session runs

Classes: `status` (status tier, milestones, last contact date), `milestones`, `contact` (last
contact date), `detail` (anything else from the knowledge layer), `correspondence` (mail,
calls, documents). Standing choices (`space.yaml` `request_choices: [{from, class,
answer}]`, `from` a space name, a profile or `any`, `answer` `at_once`, `ask` or `never`)
come with template defaults per profile; a personal space ships none, so its person always
decides. Unanswered after one scaled day a request expires. After five requests of one class
from one asker in thirty scaled days the holder gets one proposal to answer that class at
once. Both sides log; neither log leaves its space.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import registry as registry_mod  # noqa: E402
import spaceyaml  # noqa: E402

OUT_REQUESTS = Path("_outbound") / "requests"
OUT_ANSWERS = Path("_outbound") / "answers"
IN_REQUESTS = Path("_inbound") / "requests"
IN_ANSWERS = Path("_inbound") / "answers"
NEEDS = Path("_inbound") / "needs-you" / "requests.yaml"
HOLDER_LOG = Path("_meta") / "state" / "requests.jsonl"
ASKER_LOG = Path("_meta") / "state" / "requests-asked.jsonl"
EXPIRE_HOURS = 24
PROPOSE_AFTER = 5
PROPOSE_DAYS = 30


@dataclass
class Report:
    asked: list = field(default_factory=list)
    answered: list = field(default_factory=list)
    waiting: list = field(default_factory=list)
    never: list = field(default_factory=list)
    expired: list = field(default_factory=list)
    collected: list = field(default_factory=list)
    proposals: list = field(default_factory=list)


# ----- small helpers ---------------------------------------------------------------------
def _jsonl(path: Path) -> list:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _when(value) -> dt.datetime | None:
    try:
        return pages.parse_iso(value) if value else None
    except (TypeError, ValueError):
        return None


def new_id() -> str:
    return "rq-" + secrets.token_hex(4)


# ----- corridors ---------------------------------------------------------------------------
def corridor(space: spaceyaml.Space, other: str, offline_roots: dict | None = None):
    """(name, root) of the channel space between this space and `other`: a mount whose
    `space.yaml` says `profile: channel` and `between` both names; offline roots first."""
    names = {space.name, other}
    candidates = []
    for name, root in (offline_roots or {}).items():
        candidates.append((name, Path(root)))
    for mount in space.mounts:
        mroot = space.mount_root(str(mount.get("name", "")))
        if mroot is not None:
            candidates.append((str(mount.get("name")), mroot))
    for name, root in candidates:
        try:
            cs = spaceyaml.load(root)
        except Exception:
            continue
        if cs.profile == "channel" and set(cs.data.get("between") or []) == names:
            return cs.name, root
    return None, None


def corridors(space: spaceyaml.Space, offline_roots: dict | None = None) -> list:
    """(name, root, the other space) for every corridor this space can read."""
    out, seen = [], set()
    pairs = list((offline_roots or {}).items()) + [
        (str(m.get("name")), space.mount_root(str(m.get("name", "")))) for m in space.mounts]
    for name, root in pairs:
        if root is None:
            continue
        try:
            cs = spaceyaml.load(root)
        except Exception:
            continue
        between = list(cs.data.get("between") or [])
        if cs.profile == "channel" and space.name in between and cs.name not in seen:
            seen.add(cs.name)
            other = next((b for b in between if b != space.name), "")
            out.append((cs.name, Path(root), other))
    return out


def channel_files(root: Path, folder: Path) -> dict:
    """name -> (fm, body) of the pages under a corridor's folder (its working tree)."""
    out = {}
    base = root / folder
    for path in sorted(base.glob("*.md")) if base.is_dir() else []:
        try:
            out[path.stem] = pages.read(path)
        except Exception:
            continue
    return out


# ----- choices -----------------------------------------------------------------------------
def choice_for(space: spaceyaml.Space, asker: str, asker_profile: str, cls: str) -> str:
    """at_once | ask | never: the first standing choice that names the asker, its profile or
    any; nothing named means the holder's person decides."""
    order = {asker: 0, asker_profile: 1, "any": 2}
    best = None
    for choice in space.request_choices:
        who = str(choice.get("from") or "any")
        if who in order and str(choice.get("class")) == cls:
            if best is None or order[who] < order[best[0]]:
                best = (who, str(choice.get("answer") or "ask"))
    return best[1] if best else "ask"


# ----- rendering the covered answer --------------------------------------------------------
def _status_lines(root: Path, subject: str) -> dict:
    """The holder's status page for the subject, by section."""
    path = root / "live" / "subjects" / f"{subject}.md"
    if not path.exists():
        return {}
    _, body = pages.read(path)
    out, current = {}, None
    for line in body.splitlines():
        if line.startswith("## "):
            current = line[3:].strip().lower()
            out[current] = []
        elif current and line.startswith("- ") and line.strip() != "- none":
            out[current].append(line.strip())
    return out


def render(root: Path, request: dict, now: dt.datetime, asker_left_out: list | None = None) -> tuple:
    """(covered lines, what was left out) for a class the standing choice answers."""
    import release
    holder = spaceyaml.load(root)
    reg = registry_mod.Registry(root, holder)
    subject = str(request.get("subject") or "")
    cls = str(request.get("class") or "detail")
    sections = _status_lines(root, subject)
    found = reg.get(subject)
    lines, left = [], []
    if cls in ("status", "milestones", "contact") and not sections:
        left.append(f"no status page for {subject} here")
    if cls == "status" and found is not None:
        lines.append(f"- status: {found.state} (system, {holder.name}, {now:%Y-%m-%d})")
    if cls in ("status", "milestones"):
        lines += sections.get("coming up", [])[:5]
    if cls in ("status", "contact"):
        timeline = sections.get("timeline", [])
        if timeline:
            newest = timeline[0]
            date = newest.rsplit(",", 1)[-1].strip(" )")
            lines.append(f"- last contact: {date} (witness, {holder.name}, {date})")
    if cls in ("detail", "correspondence"):
        left.append(f"{cls}: not covered by a standing choice")
    kept = []
    for line in lines:
        term = release.first_term(line, holder.never_leaves) or release.first_term(
            line, asker_left_out if asker_left_out is not None else spaceyaml.DEFAULTS["left_out"])
        if term:
            left.append("a line the asker's space leaves out")
        else:
            kept.append(line)
    return kept, left


# ----- the asker ---------------------------------------------------------------------------
def readable_here(space: spaceyaml.Space, home: str | None) -> bool:
    if not home or home == space.name:
        return True
    for mount in space.mounts:
        root = space.mount_root(str(mount.get("name", "")))
        if root is None:
            continue
        try:
            if spaceyaml.load(root).name == home or mount.get("name") == home:
                return True
        except Exception:
            continue
    return False


def ask(root, subject: str, cls: str, question: str, narrowest: str = "", asked_by: str = "",
        now: dt.datetime | None = None, force: bool = False) -> Path | None:
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    reg = registry_mod.Registry(root, space)
    home = reg.home_of(subject) or (reg.get(subject).space if reg.get(subject) else None)
    if not home:
        raise ValueError(f"{subject}: no home in the registry; ask its owner instead")
    if readable_here(space, home) and not force:
        return None
    if cls not in spaceyaml.REQUEST_CLASSES:
        raise ValueError(f"class must be one of {', '.join(spaceyaml.REQUEST_CLASSES)}")
    rid = new_id()
    fm = {"id": rid, "type": "request", "title": f"Request to {home}: {question[:80]}", "status": "open",
          "generated": pages.generated("request", now), "to": home, "from": space.name,
          "from_profile": space.profile, "asked_by": asked_by or space.owner, "subject": subject,
          "class": cls, "question": question, "narrowest": narrowest or spaceyaml.REQUEST_CLASSES[cls],
          "at": pages.iso(now), "expires_at": pages.iso(now + space.scale(EXPIRE_HOURS))}
    path = pages.write(root / OUT_REQUESTS / f"{rid}.md", fm, f"{question}\n")
    _append(root / ASKER_LOG, {"at": pages.iso(now), "id": rid, "to": home, "subject": subject,
                               "class": cls, "outcome": "asked"})
    return path


def _answer_page(root: Path, rid: str, fm: dict, body: str, request: dict, now) -> Path:
    page = {"id": "p-" + hashlib.sha1(rid.encode()).hexdigest()[:8], "type": "source",
            "title": f"Answer from {fm.get('from')}: {request.get('question', '')[:80]}", "status": "draft",
            "generated": pages.generated("request", now), "request": rid, "subject": request.get("subject"),
            "from_space": fm.get("from"), "approved_by": fm.get("approved_by"),
            "recommendation": "yes, noted", "default": "stays where it is"}
    return pages.write(root / IN_ANSWERS / f"{rid}.md", page, body)


def collect(root, now: dt.datetime | None = None, offline_roots: dict | None = None) -> Report:
    """Land arrived answers as dated evidence; mark expired requests; tell the person."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    report = Report()
    answers = {}
    for name, croot, _ in corridors(space, offline_roots):
        answers.update(channel_files(croot, IN_ANSWERS))
    for path in sorted((root / OUT_REQUESTS).glob("*.md")) if (root / OUT_REQUESTS).is_dir() else []:
        req, qbody = pages.read(path)
        rid = str(req.get("id") or path.stem)
        if req.get("status") not in ("open", "shipped"):
            continue
        if rid in answers:
            fm, body = answers[rid]
            target = root / "knowledge" / "sources" / f"{now:%Y}" / f"{now:%m}" / f"answer-{rid}.md"
            wfm = {"id": "p-" + hashlib.sha1(f"w{rid}".encode()).hexdigest()[:8], "type": "source",
                   "title": f"Answer from {fm.get('from')}: {req.get('question', '')[:80]}", "status": "draft",
                   "generated": pages.generated("request", now), "record": f"answer-{rid}", "source": "request",
                   "kind": "document", "at": fm.get("answered_at") or pages.iso(now), "from": fm.get("from"),
                   "subject": req.get("subject"), "mentions": [], "audience": "owner", "identifiers": {},
                   "approved_by": fm.get("approved_by")}
            pages.write(target, wfm, body)
            _answer_page(root, rid, fm, body, req, now)
            pages.write(path, dict(req, status="answered", answered_at=pages.iso(now)), qbody)
            _append(root / ASKER_LOG, {"at": pages.iso(now), "id": rid, "to": req.get("to"), "outcome": "answered"})
            try:
                import questions_log
                questions_log.append(root, str(req.get("question")), req.get("subject"), "witness",
                                     [target.relative_to(root).as_posix()], answered_by_request=True)
            except Exception:
                pass
            _notice(root, space, req, body)
            report.collected.append(rid)
            continue
        expires = _when(req.get("expires_at"))
        if expires is not None and now > expires:
            pages.write(path, dict(req, status="expired"), qbody)
            _append(root / ASKER_LOG, {"at": pages.iso(now), "id": rid, "to": req.get("to"), "outcome": "expired"})
            _answer_page(root, rid, {"from": req.get("to"), "approved_by": None},
                         f"No answer arrived from {req.get('to')}; the request expired. A no and silence "
                         f"look the same: ask its owner directly if it matters.\n", req, now)
            report.expired.append(rid)
    return report


def _notice(root: Path, space, req: dict, body: str) -> None:
    """An owner notice payload the agent sends at once (never a chat self-message)."""
    try:
        import owner_email
    except ImportError:
        return
    notice = {"kind": "fyi", "sender": "request", "title": "One answer arrived",
              "summary": f"Your question to {req.get('to')} was answered. It waits on your list.",
              "allow_terms": [space.name, str(req.get("to"))]}
    try:
        payload = owner_email.render(notice, [space.owner_email] if "@" in space.owner_email else [],
                                     today=f"{space.now():%Y-%m-%d}")
    except Exception:
        return
    out = root / "_meta" / "state" / f"answer-notice-{req.get('id')}.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")


# ----- the holder --------------------------------------------------------------------------
def _verdicts(root: Path) -> dict:
    import status
    return status.verdicts(root)


def _paused(root: Path, path: str, now, space) -> bool:
    try:
        import release
        return release.is_paused(root, path, now)
    except Exception:
        return False


def _log_line(root: Path, line: str) -> None:
    log = Path(root) / "log.md"
    with log.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line.rstrip("\n") + "\n")


def _ship_answer(root: Path, holder, rid: str, request: dict, lines: list, left: list, approved: str, now) -> Path:
    uncovered = ("\n\nNot covered, named and not sent: " + "; ".join(sorted(set(left)))) if left else ""
    body = (f"Answer to: {request.get('question')}\n\n" + ("\n".join(lines) or "- nothing covered") + uncovered + "\n")
    fm = {"id": rid, "type": "source", "title": f"Answer to {rid}", "status": "draft",
          "generated": pages.generated("request", now), "request": rid, "from": holder.name,
          "to": request.get("from"), "class": request.get("class"), "approved_by": approved,
          "answered_at": pages.iso(now), "covered": bool(lines), "destination": None}
    return pages.write(root / OUT_ANSWERS / f"{rid}.md", fm, body)


def answer(root, now: dt.datetime | None = None, offline_roots: dict | None = None) -> Report:
    """The channel step: answer from standing choices, list the remainder, expire, propose."""
    root = Path(root)
    holder = spaceyaml.load(root)
    now = now or holder.now()
    report = Report()
    handled = {}
    for row in _jsonl(root / HOLDER_LOG):
        handled.setdefault(str(row.get("id")), []).append(row)
    given = _verdicts(root)
    items, choices_changed = [], False
    for cname, croot, other in corridors(holder, offline_roots):
        for rid, (req, _) in sorted(channel_files(croot, IN_REQUESTS).items()):
            if str(req.get("to")) != holder.name:
                continue
            outcomes = [r.get("outcome") for r in handled.get(rid, [])]
            if any(o in ("answered", "never", "expired", "declined") for o in outcomes):
                continue
            expires = _when(req.get("expires_at"))
            asker, aprofile, cls = str(req.get("from")), str(req.get("from_profile") or ""), str(req.get("class"))
            if expires is not None and now > expires:
                _append(root / HOLDER_LOG, {"at": pages.iso(now), "id": rid, "from": asker, "class": cls,
                                            "asked_by": req.get("asked_by"), "outcome": "expired"})
                report.expired.append(rid)
                continue
            import release
            text = f"{req.get('question')} {req.get('subject')}"
            choice = "never" if release.first_term(text, holder.never_leaves) else choice_for(holder, asker, aprofile, cls)
            asker_space = _space_named(holder, asker, offline_roots)
            left_out = asker_space.left_out if asker_space is not None else None
            if choice == "never":
                _append(root / HOLDER_LOG, {"at": pages.iso(now), "id": rid, "from": asker, "class": cls,
                                            "asked_by": req.get("asked_by"), "outcome": "never", "choice": "never"})
                report.never.append(rid)
                continue
            lines, left = render(root, req, now, left_out)
            verdict = given.get(rid) or {}
            choice_path = f"request_choice:{asker}:{cls}"
            if choice == "at_once" and lines and _paused(root, choice_path, now, holder):
                choice = "ask"   # a failed sample paused this standing choice: the holder answers by hand
            if choice == "at_once" and lines:
                _ship_answer(root, holder, rid, req, lines, left, f"standing choice: {cls} for {asker}", now)
                _log_line(root, f"- {pages.iso(now)} answered {rid} for {asker} at_once "
                                f"(standing choice: {cls}; path {choice_path})")
                _append(root / HOLDER_LOG, {"at": pages.iso(now), "id": rid, "from": asker, "class": cls,
                                            "asked_by": req.get("asked_by"), "outcome": "answered",
                                            "choice": f"{cls}:at_once", "subject": req.get("subject")})
                report.answered.append(rid)
                continue
            if verdict.get("verb") in ("yes", "edit", "always"):
                sent = [f"- {verdict['note']}"] if verdict.get("verb") == "edit" and verdict.get("note") else lines
                _ship_answer(root, holder, rid, req, sent, [] if verdict.get("verb") == "edit" else left,
                             f"{verdict.get('by')} by name", now)
                _append(root / HOLDER_LOG, {"at": pages.iso(now), "id": rid, "from": asker, "class": cls,
                                            "asked_by": req.get("asked_by"), "outcome": "answered",
                                            "choice": f"person:{verdict.get('verb')}"})
                if verdict.get("verb") == "always":
                    holder.data["request_choices"] = holder.request_choices + [
                        {"from": asker, "class": cls, "answer": "at_once"}]
                    choices_changed = True
                report.answered.append(rid)
                continue
            if verdict.get("verb") == "no":
                _append(root / HOLDER_LOG, {"at": pages.iso(now), "id": rid, "from": asker, "class": cls,
                                            "asked_by": req.get("asked_by"), "outcome": "declined"})
                report.never.append(rid)
                continue
            if "asked" not in outcomes:
                _append(root / HOLDER_LOG, {"at": pages.iso(now), "id": rid, "from": asker, "class": cls,
                                            "asked_by": req.get("asked_by"), "outcome": "asked"})
            draft = "; ".join(l.lstrip("- ") for l in lines) or "nothing my standing choices cover"
            widens = f"{asker} cannot read {req.get('subject')} today"
            items.append({"id": rid, "kind": "request",
                          "text": (f"{req.get('asked_by') or asker} ({asker}) asks: {req.get('question')} I would "
                                   f"answer: {draft}. It widens: {widens}."),
                          "recommendation": "the summary only (yes), or edit with your own words",
                          "default": f"expires {req.get('expires_at')}: no answer", "deadline": req.get("expires_at"),
                          "depends": 0})
            report.waiting.append(rid)
    for prop in _proposals(root, holder, now, given):
        items.append(prop)
        report.proposals.append(prop["id"])
    for item_id, verdict in given.items():
        if item_id.startswith("rq-propose-") and verdict.get("verb") in ("yes", "always"):
            _, _, rest = item_id.partition("rq-propose-")
            asker, _, cls = rest.rpartition("-")
            wanted = {"from": asker, "class": cls, "answer": "at_once"}
            if wanted not in holder.request_choices:
                holder.data["request_choices"] = holder.request_choices + [wanted]
                choices_changed = True
    if choices_changed:
        spaceyaml.save(holder)
    target = root / NEEDS
    if items or target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump({"items": items}, sort_keys=False, allow_unicode=True, width=100),
                          encoding="utf-8", newline="\n")
    return report


def _space_named(holder, name: str, offline_roots: dict | None):
    for key, root in (offline_roots or {}).items():
        if key == name:
            try:
                return spaceyaml.load(root)
            except Exception:
                return None
    root = holder.mount_root(name)
    return spaceyaml.load(root) if root is not None else None


def _proposals(root: Path, holder, now, given: dict) -> list:
    since = now - holder.scale(PROPOSE_DAYS * 24)
    counts: dict = {}
    for row in _jsonl(root / HOLDER_LOG):
        at = _when(row.get("at"))
        if at is None or at < since or row.get("outcome") != "asked":
            continue
        counts[(str(row.get("from")), str(row.get("class")))] = counts.get((str(row.get("from")), str(row.get("class"))), 0) + 1
    out = []
    for (asker, cls), n in sorted(counts.items()):
        item_id = f"rq-propose-{asker}-{cls}"
        if n < PROPOSE_AFTER or item_id in given or choice_for(holder, asker, "", cls) == "at_once":
            continue
        out.append({"id": item_id, "kind": "request",
                    "text": f"{asker} asked for {cls} {n} times this month. Answer {cls} at once from now on?",
                    "recommendation": "yes: one decision instead of one a week", "default": "you keep deciding each one",
                    "depends": 1})
    return out


# ----- status and the waiting helper ------------------------------------------------------
def state_of(root, rid: str) -> dict:
    root = Path(root)
    path = root / OUT_REQUESTS / f"{rid}.md"
    if not path.exists():
        return {"id": rid, "status": "unknown"}
    fm, _ = pages.read(path)
    out = {"id": rid, "status": fm.get("status"), "to": fm.get("to"), "expires_at": fm.get("expires_at")}
    answer_page = root / IN_ANSWERS / f"{rid}.md"
    if answer_page.exists():
        out["answer"] = pages.read(answer_page)[1].strip()
    return out


def wait(root, rid: str, timeout_minutes: float = 15, poll_seconds: float = 30, refresh=None) -> dict:
    """Poll for the answer: refresh the corridors (the sync script's pins), collect, report."""
    deadline = time.monotonic() + timeout_minutes * 60
    while True:
        if refresh is not None:
            refresh()
        collect(root)
        state = state_of(root, rid)
        if state.get("status") in ("answered", "expired") or time.monotonic() >= deadline:
            return state
        time.sleep(poll_seconds)


def _refresh_mounts(root: Path):
    def run():
        subprocess.run(["git", "-C", str(root), "submodule", "update", "--remote", "--quiet"],
                       capture_output=True, text=True)
    return run


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    verbs = ("ask", "answer", "collect", "status", "wait")
    root = Path(".")
    if argv and argv[0] not in verbs:
        root = Path(argv.pop(0))
    if not argv or argv[0] not in verbs:
        print(__doc__)
        return 2
    verb, rest = argv[0], argv[1:]
    if verb == "ask":
        ap = argparse.ArgumentParser(prog="request.py ask")
        ap.add_argument("--subject", required=True)
        ap.add_argument("--class", dest="cls", required=True)
        ap.add_argument("--question", required=True)
        ap.add_argument("--narrowest", default="")
        ap.add_argument("--asked-by", default="")
        ap.add_argument("--force", action="store_true")
        opts = ap.parse_args(rest)
        path = ask(root, opts.subject, opts.cls, opts.question, opts.narrowest, opts.asked_by, force=opts.force)
        print(f"readable here: walk or search it" if path is None else f"asked: {path.stem}")
        return 3 if path is None else 0
    if verb == "answer":
        print(json.dumps(answer(root).__dict__))
        return 0
    if verb == "collect":
        print(json.dumps(collect(root).__dict__))
        return 0
    if verb == "status" and rest:
        print(json.dumps(state_of(root, rest[0]), indent=2))
        return 0
    if verb == "wait" and rest:
        ap = argparse.ArgumentParser(prog="request.py wait")
        ap.add_argument("rid")
        ap.add_argument("--timeout", type=float, default=15)
        opts = ap.parse_args(rest)
        print(json.dumps(wait(root, opts.rid, opts.timeout, refresh=_refresh_mounts(root)), indent=2))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
