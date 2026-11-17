"""classify: the classification gate (docs/design/2026-10-16-classification-gate-design.md).

A client, project or deal (or anything part of one) sends nothing out of the space where its
material came in until its owner's agent has classified it: `company` (home the brain), `walled`
(a named crew, home their space) or `owner` (stays in the space it came in). The classification
and the obligations seen (their kind and date only) live on the subject's entry in the registry
every space reads through the brain. A space changes that registry with small notes: signals of
`kind: registry`, addressed to the space whose registry holds the entry, applied there by
`land_notes` (every ship and every landing). This tool decides nothing: the agent judges, the
tool checks who may say what and enforces the gate at every exit.

A note says who it is by (`by`) and which space sent it (`from`); neither is proof, so the
holder binds them (`_bound`). A note from a person's registered workspace is by that person; a
person with a registered workspace speaks only from it or from the subject's own home; a
workspace never moves by note, and one space is never two people's workspace. On top of that,
a note that would let someone take or open a walled subject they are not part of is refused:
on a subject the governing walled classification covers, only its crew or the company owner
names an owner, opens it to a looser class, or, while it has no owner, classifies it at all.
A note's `approved_by` is its sender's own, and a note names every person exactly: a name
that only slugs to a registered person (`Ben.` for Ben Liu) is refused. The transport
(release.py) and the gate (gate.py) have to bind the sender to the account that pushed it;
this tool cannot see that account. A classification this space sent governs here while it
travels only when its signal is one this space wrote and the holder's rules still accept it
(`travelling`).

A `workspace` note names the workspace's own repository (`repo`). The holder records it as the
person's `workspace_repo`, the repository whose log.md vouches for their signatures elsewhere,
only once, never over a value it has, and only after the host confirms it
(`verify_workspace_repo`): one of the person's registered GitHub logins has admin on it, it
sits under one of those logins or nobody else may push to it, and its space.yaml on the
default branch names that workspace and that person. The notes log row says what was filled
and how it was checked (`filled`). When the host cannot be asked, the notes log row keeps the
repository the note named (`waiting`) and it is tried again on every landing; the agent's
health rows (`_meta/state/health/classify.yaml`) only show the wait, and nothing is read back
from them. When the host answers no, nothing is tried again: the brain's owner gets one
Needs-you item ("is it their workspace?"), and only their yes sets it.

    python classify.py <root> pending                 what this space decides, asks or checks
    python classify.py <root> ask <subject>           ask the other workspaces for obligations
    python classify.py <root> decide <subject> --class C --reason R [--evidence ID ...]
                                [--crew ID ...] [--home NAME]
    python classify.py <root> owner <subject> --person NAME --evidence ID [ID ...]
    python classify.py <root> obligation <subject> --kind K [--date YYYY-MM-DD]
                                --evidence ID [ID ...] [--works-on]
    python classify.py <root> checked <subject> [--works-on]
    python classify.py <root> backlog FILE            one-off: the list for an existing install
    python classify.py <root> apply                   act on the person's answers to this tool's items
    python classify.py <root> land                    apply the registry notes that arrived here
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import registry as registry_mod  # noqa: E402
import spaceyaml  # noqa: E402

from registry import CLASSES, GATED_KINDS  # noqa: E402

RELEASED = Path("_meta") / "state" / "released.jsonl"
STRICT = {cls: n for n, cls in enumerate(CLASSES)}   # higher is narrower


class ClassifyError(ValueError):
    pass


# ----- reading the registry ----------------------------------------------------------------------
def _chain(reg, sid: str) -> list:
    subject = reg.get(sid)
    return [] if subject is None else [subject] + reg.ancestors_of(sid)


def gated(reg, sid) -> bool:
    """A client, project or deal, or anything part of one."""
    return any(s.kind in GATED_KINDS for s in _chain(reg, str(sid or "")))


def classification_of(subject) -> dict | None:
    value = subject.extra.get("classification") if subject is not None else None
    return value if isinstance(value, dict) else None


def subject_owner(reg, sid) -> str | None:
    """The person holding the relationship: the subject's own `owner`, else the nearest gated
    umbrella's. Never a domain's owner, never the space owner."""
    for subject in _chain(reg, str(sid or "")):
        if subject.owner and (subject.id == sid or subject.kind in GATED_KINDS):
            return subject.owner
    return None


def effective(reg, sid) -> dict | None:
    """The classification that governs the subject (`Registry.classification`)."""
    return reg.classification(str(sid or ""))


def resolvable(reg, sid) -> bool:
    """The subject and every umbrella it names can be read (a missing brain hides them)."""
    current = reg.get(str(sid or ""))
    seen = set()
    while current is not None and current.part_of and current.part_of not in seen:
        seen.add(current.part_of)
        current = reg.get(current.part_of)
    return current is not None


def brain_space(space) -> spaceyaml.Space | None:
    """The brain's settings: this space when it is the brain, else its `brain` mount."""
    if space is None:
        return None
    if space.profile == "brain":
        return space
    root = space.mount_root("brain")
    if root is None:
        return None
    try:
        return spaceyaml.load(root)
    except Exception:
        return None


def company_owner(space) -> str:
    brain = brain_space(space)
    return brain.owner if brain is not None else ""


def default(space) -> str:
    """`classification_default` in the brain's space.yaml: open unless it says closed."""
    brain = brain_space(space)
    value = str((brain.data if brain is not None else {}).get("classification_default") or "open")
    return "closed" if value.strip().lower() == "closed" else "open"


def _same(a, b) -> bool:
    return bool(a) and bool(b) and str(a).strip().lower() == str(b).strip().lower()


def person_id(reg, name) -> str:
    """A person's registry id by their name (or id), else the slug of the name."""
    for subject in reg.everywhere():
        if subject.kind == "people" and (_same(subject.title, name) or _same(subject.id, name)):
            return subject.id
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")


def workspaces(reg) -> dict:
    """people id -> their workspace's space name, for everyone registered who has one."""
    return {s.id: str(s.extra["workspace"]) for s in reg.everywhere()
            if s.kind == "people" and s.state != "archived" and s.extra.get("workspace")}


def people(reg) -> list:
    return sorted(s.id for s in reg.everywhere() if s.kind == "people" and s.state != "archived")


def decider(space, reg, sid) -> str:
    """`self` (this space's person holds the relationship), `stand_in` (the holder has no
    workspace yet and this space's person is the company owner), `other`, or `no_owner`."""
    owner = subject_owner(reg, sid)
    if not owner:
        return "no_owner"
    if _same(owner, space.owner):
        return "self"
    if person_id(reg, owner) not in workspaces(reg) and _same(space.owner, company_owner(space)):
        return "stand_in"
    return "other"


# ----- the gate --------------------------------------------------------------------------------------
def gate(space, reg, sid, destination) -> tuple | None:
    """Why nothing about `sid` may go to `destination`: ("unclassified", why) holds it until its
    classification allows it, ("kept", why) keeps it home for good; None when it may go (it stays
    in this space, the subject is not gated, or its classification names that home)."""
    if not sid or not destination or destination == space.name:
        return None
    if not resolvable(reg, sid):   # an unreadable registry never lets anything go
        return "unclassified", f"{sid}: its registry entry cannot be read"
    if not gated(reg, sid):
        return None
    found = effective(reg, sid)
    if found is None:
        return "unclassified", f"{sid} waits for its owner's agent to classify it"
    if found["class"] == "owner":
        return "kept", f"{found['subject']} is classified owner: it stays in the space it came in"
    home = found.get("home")
    if found["class"] == "company" and not home:
        brain = brain_space(space)
        home = brain.name if brain is not None else None
    if destination != home:
        return "unclassified", f"{found['subject']} is classified {found['class']}: its home is {home}"
    return None


def next_step(space, reg, sid) -> str:
    """What opens the gate for an unclassified subject, said to the agent that met it."""
    who = decider(space, reg, sid)
    if who in ("self", "stand_in"):
        return (f"classify it here: classify.py <root> decide {sid} --class company|walled|owner --reason "
                "\"<one line>\" (the classify skill); it takes effect in this workspace at once, then "
                "record this decision again")
    if who == "no_owner":
        return (f"nobody handles it yet: classify.py <root> owner {sid} --person \"<name>\" --evidence <record id>, "
                "then that person's workspace classifies it")
    return (f"{subject_owner(reg, sid)}'s workspace classifies it; it reaches this workspace once the brain's "
            "sync has applied it and this workspace's sync has brought the brain in (a few sync runs, about "
            "an hour); record this decision again then")


def gate_all(space, reg, sid, also, destination) -> tuple | None:
    """The gate for a projection about `sid` that is also about the subjects in `also`: each must
    let it go; one whose classification names another home keeps it home for good."""
    blocked = gate(space, reg, sid, destination)
    if blocked:
        return blocked
    for other in _ids(also):
        blocked = gate(space, reg, other, destination)
        if blocked and blocked[0] == "unclassified" and effective(reg, other) is not None:
            return "kept", f"it is about {other} too, whose home is not {destination}"
        if blocked:
            return blocked
    return None


def also_about(reg, sid, record: dict) -> list:
    """The other clients, projects and deals a whole record is about: those whose identifiers it
    carries (never the subject's own umbrellas). Facts only: a name in the text is the router's
    judgement, and parts it chose leave for their subject alone."""
    skip = {str(sid)} | {a.id for a in reg.ancestors_of(str(sid))}
    return sorted(s.id for s in reg.match(record) if s.id not in skip and gated(reg, s.id))


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


def first_release(root, space, reg, sid) -> bool:
    """A subject classified `company` that this space has not released since: its projections
    wait the window and show as one line on the morning view (the safety net)."""
    found = effective(reg, sid)
    if found is None or found["class"] != "company":
        return False
    try:
        since = pages.parse_iso(found.get("at"))
    except (TypeError, ValueError):
        return True
    for row in _jsonl(Path(root) / RELEASED):
        if str(row.get("subject")) != str(sid) or row.get("class") in ("retraction", "signal"):
            continue
        try:
            if pages.parse_iso(row.get("at")) >= since:
                return False
        except (TypeError, ValueError):
            continue
    return True


# ----- the way back: held items once their classification lets them go ----------------------------
HELD = Path("_outbound") / "held"
QUEUE = Path("_outbound") / "queue"


def requeue(root, space=None, reg=None, now=None) -> dict:
    """Every item held `unclassified` whose gate is open now goes back to the queue (a window item
    gets a fresh window; a first release waits one); a `kept` answer keeps it home for good; a
    settled classification naming another home re-routes it: the router decides a router item
    again, an identifier item is re-addressed and waits a window."""
    root = Path(root)
    space = space or spaceyaml.load(root)
    now = now or space.now()
    reg = reg or registry_mod.Registry(root, space)
    out = {"requeued": 0, "kept": 0, "rerouted": 0}
    for path in sorted((root / HELD).glob("*.md")):
        try:
            fm, body = pages.read(path)
        except Exception:
            continue
        if fm.get("reason") != "unclassified":
            continue
        sid, dest, also = fm.get("subject"), fm.get("destination"), fm.get("also_about")
        blocked = gate_all(space, reg, sid, also, dest)
        if blocked and blocked[0] == "kept":
            pages.write(path, dict(fm, reason="kept", why=blocked[1], kept=True), body)
            out["kept"] += 1
            continue
        if blocked and not gate(space, reg, sid, dest):
            continue   # another subject it is about still waits for its classification
        if blocked:
            found = effective(reg, sid)
            if found is None:
                continue   # still waiting for its classification
            home = found.get("home")
            if found["class"] == "company" and not home:
                brain = brain_space(space)
                home = brain.name if brain is not None else None
            if not home or home == dest:
                continue
            if fm.get("routed_by"):   # the router decides it again, for the new home
                import route
                for rid in [str(e) for e in fm.get("evidence") or []]:
                    route.reopen(root, rid, str(sid), dest, by="classify", now=now)
                out["rerouted"] += 1
                continue
            fm = dict(fm, rerouted_from=dest, destination=home, **{"class": "window"})
            out["rerouted"] += 1
            if gate_all(space, reg, sid, also, home):
                pages.write(path, fm, body)
                continue
        clean = {k: v for k, v in fm.items() if k not in ("reason", "why", "kept", "listed")}
        if clean.get("class") not in ("at_once", "window"):
            clean["class"] = "window"
        if first_release(root, space, reg, sid):
            clean.update({"class": "window", "first_release": str(sid)})
            clean["why"] = "; ".join(x for x in (str(clean.get("why") or ""),
                                                 f"the first release since {sid} was classified company") if x)
        if clean["class"] == "window":
            clean["stage_after"] = pages.iso(now + space.window())
        pages.write(root / QUEUE / f"{fm['id']}.md", clean, body)
        path.unlink()
        out["requeued"] += 1
    return out


# ----- registry notes: how a space changes the registry that holds a subject -----------------------
NOTE_KINDS = ("owner", "workspace", "ask", "classification", "obligation", "checked", "state", "link", "key")
NOTES_LOG = registry_mod.NOTES_LOG
SIGNALS_OUT = Path("_outbound") / "signals"
SIGNALS_IN = Path("_inbound") / "signals"


BREAKS = re.compile(r"[\r\n\x0b\x0c\x1c-\x1e\x85\u2028\u2029]+")


def _append(path: Path, line: str) -> None:
    """One line to a log: a line break inside a value another space sent (a subject, a sender)
    never starts a line of its own. A JSON row has none: json.dumps escapes them."""
    pages.append_text(path, BREAKS.sub(" ", str(line)).strip() + "\n")


def _holder_name(space, subject) -> str | None:
    """The space whose registry holds an inherited subject (its mount's space name)."""
    root = space.mount_root(subject.space) if subject.space else None
    if root is None:
        return subject.space or None
    try:
        return spaceyaml.load(root).name
    except Exception:
        return subject.space


def _as_holder(space, subject, signal, now) -> tuple:
    """What the registry that holds `subject` would do with `signal`, read through its mount:
    ("applied", "", the classification it would write) or ("refused", why, None). Nothing is
    saved; an unreadable holder answers ("unknown", "", None)."""
    holder = space.mount_root(subject.space) if subject.space else None
    try:
        holder_space = spaceyaml.load(holder)
        holder_reg = registry_mod.Registry(holder, holder_space)
    except Exception:
        return "unknown", "", None
    outcome, why = apply_note(holder, holder_space, holder_reg, signal, now)
    entry = holder_reg.subjects.get(subject.id)
    return outcome, why, classification_of(entry) if outcome == "applied" else None


def note(root, sid, kind, data, now=None, sent=None) -> Path | None:
    """Tell the registry that holds `sid` something: applied at once when this space's own
    registry holds it (returns None), else a `kind: registry` signal to the holding space. A
    classification for another registry is first checked against that registry's rules, so a
    refusal comes now; `sent` (a dict) then gets the classification it will write, which governs
    this space at once while the note travels (registry.Registry)."""
    if kind not in NOTE_KINDS:
        raise ClassifyError(f"a note is one of {', '.join(NOTE_KINDS)}")
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    reg = registry_mod.Registry(root, space)
    signal = {"id": pages.new_id(), "kind": "registry", "note": kind, "subject": str(sid),
              "at": pages.iso(now), "by": space.owner, "from": space.name, "data": dict(data or {})}
    if str(sid) in reg.subjects:
        outcome, why = apply_note(root, space, reg, signal, now)
        if outcome == "applied":
            reg.save()
        _record(root, signal, outcome, why, now)
        if outcome != "applied":
            raise ClassifyError(why)
        return None
    subject = reg.get(str(sid))
    if subject is None:
        raise ClassifyError(f"{sid} is not in the registry")
    signal["destination"] = _holder_name(space, subject)
    if kind == "classification":
        outcome, why, result = _as_holder(space, subject, signal, now)
        if outcome == "refused":
            raise ClassifyError(why)
        if result and sent is not None:
            sent["classification"] = {k: v for k, v in result.items()
                                      if k in ("class", "state", "home", "crew", "at", "decided_by", "for")}
    path = root / SIGNALS_OUT / f"{signal['id']}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    path.write_text(yaml.safe_dump(signal, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")
    return path


def _record(root: Path, signal: dict, outcome: str, why: str, now) -> None:
    row = {"id": signal.get("id"), "at": pages.iso(now), "note": signal.get("note"),
           "subject": signal.get("subject"), "from": signal.get("from"),
           "by": signal.get("by"), "outcome": outcome, "why": why}
    if isinstance(signal.get("filled"), dict):   # a field set once, and how it was checked
        row["filled"] = signal["filled"]
    if isinstance(signal.get("waiting"), dict):   # what a landed note named that the host has not confirmed yet
        row["waiting"] = signal["waiting"]
    if isinstance(signal.get("asked"), dict):   # the host refused it: the owner is asked, the wait ends
        row["asked"] = signal["asked"]
    _append(root / NOTES_LOG, json.dumps(row, default=str))
    _append(root / "log.md", f"- {pages.iso(now)} registry note {signal.get('note')} for {signal.get('subject')} "
                             f"from {signal.get('from')}: {outcome}" + (f" ({why})" if why else ""))


def _ids(values) -> list:
    return [str(v) for v in (values if isinstance(values, list) else [values] if values else [])]


def may_decide_for(space, reg, owner, by) -> bool:
    """Whether `by` may classify for `owner`: it is them, or the company owner (this registry's
    owner) while they have no workspace."""
    if not str(owner or "").strip():
        return False
    if _same(by, owner):
        return True
    return person_id(reg, owner) not in workspaces(reg) and _same(by, company_owner(space) or space.owner)


def may_decide(space, reg, sid, by) -> bool:
    """Whether `by` may classify `sid` in the registry `space` holds."""
    return may_decide_for(space, reg, subject_owner(reg, sid), by)


def may_link(reg, subject, by: str, sender: str) -> bool:
    """Whether a note by `by` from the space `sender` may link a codebase to `subject`: the note
    comes from its home, or `by` owns the space whose registry holds it, owns the subject, is on
    its crew or an umbrella's (or the walled classification's that governs it, so a project's crew
    links its parts), or is anyone registered when its home is the brain."""
    space = reg.space
    home = reg.home_of(subject.id)
    if home and sender == home:
        return True
    holder = space
    if space is not None and subject.space:
        try:
            holder = spaceyaml.load(space.mount_root(subject.space))
        except Exception:
            holder = None
    if _same(by, holder.owner if holder is not None else None) or _same(by, subject_owner(reg, subject.id)):
        return True
    c = reg.classification(subject.id) or {}
    crew = {p for s in [subject, *reg.ancestors_of(subject.id)] for p in _ids(s.crew)} \
        | (set(_ids(c.get("crew"))) if c.get("class") == "walled" else set())
    named = [s for s in reg.everywhere() if s.kind == "people" and _same(s.title, by)]
    if any(p.id in crew for p in named):
        return True
    brain = brain_space(space)
    return brain is not None and home == brain.name and any(p.state != "archived" for p in named)


def _seen(value) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _when(value) -> dt.datetime:
    """A note's time, for its order; an unreadable one sorts last."""
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    try:
        return pages.parse_iso(str(value))
    except (TypeError, ValueError):
        return dt.datetime.max.replace(tzinfo=dt.timezone.utc)


def needs_person(space, previous: dict | None, cls: str) -> bool:
    """A looser class than before, `company` again after a re-opening, or `company` under a
    closed default: only the subject's owner, the person, may say it."""
    previous = previous or {}
    before = previous.get("class") or (previous.get("previous") or {}).get("class")
    if before in STRICT and STRICT[cls] < STRICT[before]:
        return True
    if cls == "company" and (previous.get("state") == "open" or
                             (previous.get("state") == "asking" and before == "company")):
        return True
    return cls == "company" and default(space) == "closed"


def _mark_checked(reg, c: dict | None, by, sender) -> None:
    if not c:
        return
    pid = person_id(reg, by)
    c["not_yet_checked"] = [p for p in _ids(c.get("not_yet_checked")) if p != pid]
    if c.get("state") == "asking" and pid in _ids(c.get("asked")) and pid not in _ids(c.get("answered")):
        c["answered"] = _ids(c.get("answered")) + [pid]
    if sender and sender not in _ids(c.get("checked_with")):
        c["checked_with"] = _ids(c.get("checked_with")) + [sender]


def _person_named(reg, name):
    """The people subject titled (or with the id) `name`, any registry this space reads."""
    return next((s for s in reg.everywhere() if s.kind == "people" and s.state != "archived"
                 and (_same(s.title, name) or _same(s.id, name))), None)


def _key(name) -> str:
    """A name as `person_id` slugs it when no person has it exactly."""
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")


def _lookalike(reg, name):
    """The registered person a name is not, but slugs to (`Ben.`, `ben-liu`, `Ben​Liu` for Ben
    Liu, whose id is `ben`), or None. `person_id` reads such a name as that person's id, so a
    crew check would pass it while every check by name missed it: a note never names a person
    that way."""
    if not str(name or "").strip() or _person_named(reg, name) is not None:
        return None
    key = _key(name)
    return next((s for s in reg.everywhere() if s.kind == "people" and key
                 and key in (s.id, _key(s.title))), None) if key else None


def _workspace_person(reg, sender):
    """The person whose registered workspace is the space `sender`, or None."""
    return next((s for s in reg.everywhere() if s.kind == "people" and s.state != "archived"
                 and str(s.extra.get("workspace") or "") == str(sender or "")), None) if sender else None


def _own_slugs(root, person) -> set:
    """The names a person's workspace may take: their row id, or the slug their invitation names."""
    import yaml
    out = {person.id}
    path = Path(root) / "_meta" / "state" / "invitations" / f"{person.id}.yaml"
    try:
        slug = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("slug") if path.is_file() else None
    except (OSError, UnicodeDecodeError, yaml.YAMLError, AttributeError):
        slug = None
    if pages.is_slug(slug):
        out.add(str(slug))
    return out


def _bound(space, reg, kind, sid, by, sender, data=None) -> str:
    """Why a note's `by` does not belong to the space it came from, or "". A note from this
    space is by its owner. A note from a person's registered workspace is by that person. A
    person with a registered workspace speaks only from it, except through a subject's own home
    (a project space speaks for its subject). The sending space itself is not authenticated
    here: release.py stamps `from` and gate.py binds it to the account that pushed it. A name
    that only slugs to a registered person (`_lookalike`), as `by` or as the person a note names
    owner, is refused: it would pass for them on the crew and nowhere else."""
    data = data or {}
    for field_, value in (("by", by), ("owner", data.get("owner")), ("person", data.get("person"))):
        alike = _lookalike(reg, value)
        if alike is not None:
            return (f"a note's {field_} {str(value)[:60]!r} is not how {alike.title} is registered: "
                    "a note names a person exactly")
    if sender == space.name:
        return "" if _same(by, space.owner) else f"a note from {sender} is by its owner, not {by}"
    sending = _workspace_person(reg, sender)
    if sending is not None:
        return "" if (_same(by, sending.title) or _same(by, sending.id)) else \
            f"a note from {sender} is by {sending.title}, not {by}"
    named = _person_named(reg, by)
    if named is not None and named.extra.get("workspace") and str(named.extra["workspace"]) != sender:
        if sid and reg.get(sid) is not None and reg.home_of(sid) == sender:
            return ""   # the subject's own home (a project space) speaks for it
        return f"{by} speaks from their workspace {named.extra['workspace']}, not from {sender}"
    return ""


def _crew_of(reg, sid) -> set:
    """Every person id on the crew of the subject, its umbrellas, or the walled classification
    that governs it."""
    subject = reg.get(str(sid))
    if subject is None:
        return set()
    c = reg.classification(subject.id) or {}
    return ({p for s in [subject, *reg.ancestors_of(subject.id)] for p in _ids(s.crew)}
            | (set(_ids(c.get("crew"))) if c.get("class") == "walled" else set()))


def _inside(space, reg, sid, by) -> bool:
    """`by` is the company owner, or on the crew of `sid` (an umbrella's or the walled
    classification's too)."""
    if _same(by, company_owner(space) or (space.owner if space.profile == "brain" else "")):
        return True
    return person_id(reg, by) in _crew_of(reg, sid)


def _propose_crew(reg, c: dict | None, by) -> None:
    pid = person_id(reg, by)
    if c and c.get("class") == "walled" and pid not in _ids(c.get("crew")) and pid not in _ids(c.get("crew_proposed")):
        c["crew_proposed"] = _ids(c.get("crew_proposed")) + [pid]


def apply_note(root, space, reg, signal: dict, now) -> tuple:
    """Apply one note to this space's own registry entries: ("applied", "") or ("refused", why)."""
    kind, sid = signal.get("note"), str(signal.get("subject") or "")
    by, sender = str(signal.get("by") or ""), str(signal.get("from") or "")
    at = pages.iso(_when(signal.get("at"))) if signal.get("at") else pages.iso(now)
    data = signal.get("data") if isinstance(signal.get("data"), dict) else {}
    if kind not in NOTE_KINDS:
        return "refused", f"unknown note {kind}"
    if kind == "workspace":
        person = next((s for s in reg.subjects.values() if s.kind == "people" and _same(s.title, by)), None)
        if person is None or not registry_mod.SLUG.match(sender):
            return "refused", f"no registered person named {by}"
        had = str(person.extra.get("workspace") or "")
        if not had and sender not in _own_slugs(root, person):   # as gate._own_fill binds it
            return "refused", f"{sender} is not {person.title}'s own slug"
        if had and had != sender:
            return "refused", f"{person.title}'s workspace is {had}; a workspace never moves by note"
        other = _workspace_person(reg, sender)
        if other is not None and other.id != person.id:
            return "refused", f"{sender} is {other.title}'s workspace already"
        person.extra["workspace"] = sender
        return "applied", _workspace_repo(root, space, person, sender, data.get("repo"), signal, now)
    if kind == "key":   # a workspace asks the brain's agent for a read-only deploy key on a mount
        if space.profile != "brain":
            return "refused", "a mount key is attached by the brain's agent"
        import onboard   # here: onboard imports much more than a note needs
        # the in-memory registry, so a key note in the same batch as the workspace's own note sees it
        return onboard.queue_mount_key(root, signal, reg=reg)
    return _apply_other(root, space, reg, signal, now, kind, sid, by, sender, at, data)


# ----- a workspace's repository: verified with the host, then set once --------------------------
HEALTH_WRITER = "classify"   # the rows this tool reports to the agent (needs_you.set_health)


def _gh(*args) -> tuple:
    """(exit code, stdout, stderr) of one `gh` call; OSError when it cannot run. Tests inject their own."""
    import subprocess
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OSError("gh cannot run here") from exc
    return done.returncode, done.stdout or "", done.stderr or ""


def _logins(person) -> list:
    got = person.match.get("github") if isinstance(person.match, dict) else None
    return [str(v).strip() for v in (got if isinstance(got, list) else [got]) if isinstance(v, str) and v.strip()]


def verify_workspace_repo(slug: str, logins: list, workspace: str, person_title: str, gh=None,
                          owner_logins: list | tuple = ()) -> tuple:
    """Whether the repository `slug` (owner/name) is the person's workspace, asked of the host:
    ("verified", how), ("refused", why) or ("unknown", why) when the host cannot be asked (no
    `gh`, signed out, a run with no access). It is, when the person alone controls who writes
    its log.md: one of their registered GitHub logins (`match.github`, which only the owner's
    click changes) has admin on it, and either it sits under one of those logins or every
    account that may push to it is one of them or one of the company owner's registered logins
    (`owner_logins`: the brain owner, trusted with the whole brain already, owns the company
    organisation where `onboard.py --invite` makes a workspace, and made it; any other
    organisation owner or member who may push refuses it); and its space.yaml on the default
    branch says `name: <workspace>` and `owner: <the person>`."""
    import yaml
    import ghrest
    gh = gh or _gh
    if not logins:
        return "unknown", f"no GitHub login is registered for {person_title} yet"

    def ask(*args):
        try:
            code, out, err = gh(*args)
        except OSError as exc:
            return None, str(exc)
        if code != 0:
            text = f"{err}\n{out}"
            # a rate limit is a 403 too, but no answer: asked again on a later landing
            if ghrest.NOT_ENABLED.search(text) or not re.search(r"HTTP 40[34]", text) \
                    or re.search(r"rate limit|abuse|HTTP 429", text, re.I):
                return None, "the host could not be asked"
            return False, "the host says no"
        return out, ""
    admin = None
    for login in logins:
        out, why = ask("api", f"repos/{slug}/collaborators/{login}/permission", "--jq", ".permission")
        if out is None:
            return "unknown", why
        if out and out.strip() == "admin":
            admin = login
            break
    if admin is None:
        return "refused", f"no registered login of {person_title} has admin on it"
    mine = {login.casefold() for login in logins}
    if slug.split("/", 1)[0].casefold() not in mine:   # not under the person's own login: who else writes?
        out, why = ask("api", "--paginate", f"repos/{slug}/collaborators?affiliation=all&per_page=100",
                       "--jq", ".[] | select(.permissions.push) | .login")
        if out is None:
            return "unknown", why
        pushers = {line.strip().casefold() for line in (out or "").splitlines() if line.strip()}
        allowed = mine | {login.casefold() for login in owner_logins}
        if out is False or not pushers or pushers - allowed:
            return "refused", f"accounts that are not {person_title}'s can write to it"
    out, why = ask("api", f"repos/{slug}/contents/space.yaml", "-H", "Accept: application/vnd.github.raw")
    if out is None:
        return "unknown", why
    try:
        data = yaml.safe_load(out) if out else None
    except yaml.YAMLError:
        data = None
    if not isinstance(data, dict) or str(data.get("name") or "") != workspace \
            or not _same(data.get("owner"), person_title):
        return "refused", f"its space.yaml is not {workspace}'s, owned by {person_title}"
    return "verified", f"{admin} has admin on it and its space.yaml names {workspace}, owned by {person_title}"


def _owner_logins(root, space) -> list:
    """The registered GitHub logins (`match.github`, a trust field) of this space's owner's own
    people row, read from this space's registry alone: in the brain, the company owner."""
    reg = registry_mod.Registry(root, space)
    return [login for s in reg.subjects.values() if s.kind == "people" and _same(s.title, space.owner)
            for login in _logins(s)]


def _workspace_repo(root, space, person, workspace: str, repo, signal: dict | None, now) -> str:
    """Set the person's `workspace_repo` once, from the repository their workspace's note names,
    when the host confirms it (`verify_workspace_repo`). Never changes a value it has: a second
    repository is refused. When the host cannot be asked it waits in the agent's health rows and
    is tried again on every landing (`retry_workspace_repos`). When the host refuses it, it is
    never tried again: the owner gets one Needs-you item, whose yes sets it (`apply_verdicts`).
    Returns what the notes log says about it ("" when the note named none)."""
    if not repo:
        return ""
    slug = registry_mod.repo_url(registry_mod.clone_url(repo) or "") if isinstance(repo, str) else None
    slug = slug.split("github.com/", 1)[1] if slug and "github.com/" in slug else None
    had = person.extra.get("workspace_repo")
    if had:
        if slug and registry_mod.same_repo(had, slug):
            _health_drop(root, person.id)
            return ""
        return "its workspace repository is set already; a note never changes it"
    if slug is None:
        return "the repository it names is not a GitHub repository"
    if space.mode == "fixture":
        outcome, how = "unknown", "this space runs offline"
    else:
        outcome, how = verify_workspace_repo(slug, _logins(person), workspace, person.title,
                                             owner_logins=_owner_logins(root, space))
    if outcome == "verified":
        person.extra["workspace_repo"] = slug
        if signal is not None:   # the notes log row says what was filled and how (gate.py reads it)
            signal["filled"] = {"workspace_repo": slug, "person": person.id, "checked": how}
        _health_drop(root, person.id)
        _append(Path(root) / "log.md", f"- {pages.iso(now)} workspace_repo of {person.id} set to {slug}: {how}")
        return f"workspace_repo set to {slug}"
    if outcome == "refused":   # the host said no: asking again changes nothing, the owner decides
        _health_drop(root, person.id)
        if signal is not None:
            signal["asked"] = {"workspace_repo": slug, "person": person.id}
        item_id = f"classify-workspace-repo-{person.id}-{pages.fingerprint(slug)[-8:]}"
        if _put_item(Path(root), {"id": item_id, "kind": "question",   # once: never again after an answer
                                  "text": f"Is {slug} {person.title}'s workspace repository? Yes sets it as "
                                          f"their workspace_repo. The host refused it: {how}.",
                                  "recommendation": "no", "default": f"{person.title}'s releases keep waiting",
                                  "deadline": None, "depends": 0,
                                  "payload": (PROPOSALS / f"{item_id}.yaml").as_posix(), "fingerprint": ""}):
            _propose(Path(root), item_id, {"verb": "workspace_repo", "person": person.id,
                                           "workspace": workspace, "workspace_repo": slug})
        return f"workspace_repo not set: {how}; the owner is asked"
    if signal is not None:   # the notes log keeps what to try again (`retry_workspace_repos`)
        signal["waiting"] = {"workspace_repo": slug, "person": person.id, "workspace": workspace}
    # the health row only shows the wait: nothing is ever read back from it
    _health_put(root, {"id": f"workspace-repo-{person.id}", "person": person.id, "outcome": outcome,
                       "text": f"{person.title}'s workspace repository is not verified yet ({how})",
                       "agent": "tried again on every landing until the host answers"})
    return f"workspace_repo not set: {how}"


def _confirm_workspace_repo(root, plan: dict, by, now) -> None:
    """The owner's yes to a repository the host refused: set it once, on the row that was asked."""
    reg = registry_mod.Registry(root, spaceyaml.load(root))
    person = reg.subjects.get(str(plan.get("person") or ""))
    slug = str(plan.get("workspace_repo") or "")
    if person is None or person.kind != "people" or not slug or person.extra.get("workspace_repo")             or str(person.extra.get("workspace") or "") != str(plan.get("workspace") or ""):
        raise ClassifyError(f"{plan.get('person')}'s row changed since it was asked")
    person.extra["workspace_repo"] = slug
    reg.save()
    _append(Path(root) / "log.md", f"- {pages.iso(now)} workspace_repo of {person.id} set to {slug}: {by} said yes")


def _health_put(root, row: dict) -> None:
    import needs_you
    rows = [r for r in needs_you.health_rows(root, HEALTH_WRITER) if r.get("id") != row["id"]]
    needs_you.set_health(root, HEALTH_WRITER, [{k: v for k, v in r.items() if k != "writer"} for r in rows] + [row])


def _health_drop(root, person_id: str) -> None:
    import needs_you
    rows = needs_you.health_rows(root, HEALTH_WRITER)
    kept = [{k: v for k, v in r.items() if k != "writer"} for r in rows
            if r.get("id") != f"workspace-repo-{person_id}"]
    if len(kept) != len(rows):
        needs_you.set_health(root, HEALTH_WRITER, kept)


def waiting_workspace_repos(root) -> dict:
    """person id -> {workspace_repo, person, workspace}: the repository the latest landed workspace
    note of each person named that the host has not confirmed yet, read from the notes log
    (`_record` writes it, from a note this registry applied), never from the health rows, which
    only show the wait. A later fill ends the wait."""
    out: dict = {}
    for row in registry_mod._jsonl(Path(root) / NOTES_LOG):
        filled = row.get("filled") if isinstance(row.get("filled"), dict) else {}
        waiting = row.get("waiting") if isinstance(row.get("waiting"), dict) else {}
        if row.get("outcome") != "applied" or row.get("note") != "workspace":
            continue
        asked = row.get("asked") if isinstance(row.get("asked"), dict) else {}
        if filled.get("workspace_repo"):
            out.pop(str(filled.get("person") or ""), None)
        if asked.get("person"):   # the host refused it: the owner's item holds it now
            out.pop(str(asked["person"]), None)
        if waiting.get("person"):
            out[str(waiting["person"])] = waiting
    return out


def retry_workspace_repos(root, space, reg, now) -> int:
    """Try again every workspace repository a landed note named that the host could not confirm
    before (`waiting_workspace_repos`): the same check, set once when it passes. A health row with
    no such note behind it is dropped. Returns how many were set."""
    import needs_you
    done = 0
    waiting = waiting_workspace_repos(root)
    for row in needs_you.health_rows(root, HEALTH_WRITER):
        pid = str(row.get("person") or "")
        if str(row.get("id", "")).startswith("workspace-repo-") and pid not in waiting:
            _health_drop(root, pid)
    for pid, wait in waiting.items():
        person = reg.subjects.get(pid)
        if person is None or person.kind != "people" or person.extra.get("workspace_repo") \
                or str(person.extra.get("workspace") or "") != str(wait.get("workspace") or ""):
            _health_drop(root, pid)
            continue
        signal = {"id": None, "note": "workspace", "subject": person.id, "from": wait.get("workspace"),
                  "by": person.title}
        why = _workspace_repo(root, space, person, str(wait["workspace"]), wait.get("workspace_repo"), signal, now)
        if signal.get("filled") or signal.get("asked"):
            signal.pop("waiting", None)
            _record(Path(root), signal, "applied", why, now)
            done += bool(signal.get("filled"))
    return done


def _apply_other(root, space, reg, signal: dict, now, kind, sid, by, sender, at, data) -> tuple:
    """Every note but `workspace` (`apply_note`)."""
    if kind != "state":   # a state note is the home's alone, judged by its sender below
        why = _bound(space, reg, kind, sid, by, sender, data)
        if why:
            return "refused", why
    subject = reg.subjects.get(sid)
    if subject is None:
        return "refused", f"{sid} is not held in this registry"
    if kind == "state":   # dormancy is judged where the subject's evidence lives: its home
        state, home = str(data.get("state") or ""), reg.home_of(sid)
        if state not in ("active", "dormant", "archived") or subject.state not in ("active", "dormant"):
            return "refused", f"a state note moves {sid} between active and dormant, or archives it"
        if not home or sender != home:
            return "refused", f"only {sid}'s home ({home or 'none'}) says it is {state}"
        subject.state = state
        return "applied", ""
    if kind == "link":   # a codebase linked to the subject (docs/design/2026-10-22-codebase-link-design.md)
        if not may_link(reg, subject, by, sender):
            return "refused", f"only the subject's crew may link a repository to {sid}"
        repo = str(data.get("repo") or "").strip()
        if not repo:
            return "refused", "a link note names its repository"
        repos = list(subject.extra.get("repos") or [])

        def names(entry) -> list:   # the repository's name and the names it had before
            return [entry.get("repo"), *(entry.get("was") or [])]

        def same(a, b) -> bool:   # one repository: the same id when both have one, else a shared name
            if a.get("repo_id") and b.get("repo_id"):
                return a["repo_id"] == b["repo_id"]
            return any(registry_mod.same_repo(x, y) for x in names(a) for y in names(b))
        found = next((r for r in repos if isinstance(r, dict) and same(r, dict(data, repo=repo))), None)
        if data.get("remove"):
            repos = [r for r in repos if r is not found]
        else:
            entry = dict(found or {})
            entry.update({k: data[k] for k in ("repo_id", "linked_by", "linked", "readers", "checked", "gone", "was")
                          if k in data and not (k == "linked" and entry.get("linked"))})
            if found is None or "was" in data:   # only a rename moves a link's name, so none moves it back
                entry["repo"] = repo
            entry = {k: v for k, v in entry.items() if v is not None}   # `gone: null` brings a link back
            merged = [r for r in repos if isinstance(r, dict) and r is not found and same(r, entry)]
            if merged:   # the same repository under a second entry: one entry stays, with every name
                every, was = [entry, *([found] if found is not None else []), *merged], []   # found's names before the note too
                for name in (n for e in every for n in names(e)):
                    if name and not any(registry_mod.same_repo(name, w) for w in [entry["repo"], *was]):
                        was.append(name)
                linked = min((e["linked"] for e in every if e.get("linked")), key=str, default=None)
                repo_id = next((e["repo_id"] for e in every if e.get("repo_id")), None)
                entry = {k: v for k, v in dict(entry, was=was or None, linked=linked, repo_id=repo_id).items()
                         if v is not None}
            bad = registry_mod._repo_errors(sid, [entry])
            if bad:
                return "refused", bad[0]
            kept = [r for r in repos if not any(r is m for m in merged)]
            repos = [entry if r is found else r for r in kept] if found is not None else kept + [entry]
        if repos:
            subject.extra["repos"] = repos
        else:
            subject.extra.pop("repos", None)
        return "applied", ""
    c = classification_of(subject)
    governing = reg.classification(sid) or {}
    walled = governing.get("class") == "walled"
    if kind == "owner":
        if subject_owner(reg, sid):
            return "refused", f"{sid} already has an owner: {subject_owner(reg, sid)}"
        if not str(data.get("person") or "").strip() or not _ids(data.get("evidence")):
            return "refused", "an owner note names the person and the evidence"
        if walled and not (_inside(space, reg, sid, by)
                           and person_id(reg, str(data["person"]).strip()) in _crew_of(reg, sid) | {
                               person_id(reg, company_owner(space))}):
            return "refused", f"{sid} is walled: only its crew names its owner, from its crew"
        subject.owner = str(data["person"]).strip()
        return "applied", ""
    owner, named = subject_owner(reg, sid), str(data.get("owner") or "").strip()
    if kind in ("ask", "classification"):
        if owner and not may_decide(space, reg, sid, by):
            return "refused", (f"only {owner}'s workspace (or the company owner's while {owner} has none) "
                               f"classifies {sid}")
        if not owner and not (kind == "classification" and data.get("approved_by")
                              and may_decide_for(space, reg, named, by)):
            return "refused", f"{sid} has no owner yet"
        if not owner and walled and not _inside(space, reg, sid, by):
            return "refused", (f"{sid} is walled and has no owner yet: only its crew or the company owner "
                               "classifies it and names its owner")
        if data.get("approved_by") and not _same(data.get("approved_by"), by):
            return "refused", "a note's approval is by the person who sent it"
    if kind == "ask":
        if c and c.get("state") == "settled":
            return "refused", f"{sid} is classified already"
        new = {"state": "asking", "asked": _ids(data.get("asked")), "answered": [],
               "ask_until": data.get("ask_until"), "decided_by": by, "at": at}
        if c and (c.get("class") or c.get("previous")):
            new["previous"] = {k: v for k, v in (c if c.get("class") else c["previous"]).items()
                               if k in ("class", "at", "decided_by", "home", "crew")}
        subject.extra["classification"] = new
        return "applied", ""
    if kind == "classification":
        cls = data.get("class")
        if not isinstance(cls, str) or cls not in STRICT:
            return "refused", f"class must be one of {', '.join(CLASSES)}"
        if needs_person(space, c, cls) and not data.get("approved_by"):
            return "refused", f"{cls} for {sid} needs its owner's yes"
        if walled and STRICT[cls] < STRICT["walled"] and not _inside(space, reg, sid, by):
            return "refused", f"{sid} is walled: only its crew or the company owner opens it"
        if cls == "walled" and (not data.get("home") or not _ids(data.get("crew"))):
            return "refused", "a walled classification names its home and crew"
        if cls == "walled" and registry_mod.space_name(data.get("home")) is None:
            return "refused", "a walled home is a space name"
        if not owner:
            subject.owner = named
        new = {"class": cls, "state": "settled", "reason": str(data.get("reason") or ""),
               "evidence": _ids(data.get("evidence")), "decided_by": by, "at": at,
               "checked_with": _ids(data.get("checked_with")), "not_yet_checked": _ids(data.get("not_yet_checked"))}
        for key in ("for", "approved_by"):
            if data.get(key):
                new[key] = str(data[key])
        if cls == "walled":
            new["home"], new["crew"] = str(data["home"]), _ids(data.get("crew"))
            waiting = [p for p in _ids((c or {}).get("crew_proposed")) if p not in new["crew"]]
            if waiting:
                new["crew_proposed"] = waiting
        elif cls == "company":   # home the brain, whichever registry holds the entry
            brain = brain_space(space)
            if brain is None:
                return "refused", "the brain cannot be read: a company classification names it as home"
            new["home"] = brain.name
            if len(_obligations(reg, sid)) > _seen(data.get("obligations_seen")):   # one it never saw
                new.update(state="open", reopened_at=at, reopened_by=space.name)
        before = c if c and c.get("class") else (c or {}).get("previous")
        if before:
            new["previous"] = {k: v for k, v in before.items() if k in ("class", "at", "decided_by", "home", "crew")}
        subject.extra["classification"] = new
        subject.home = new.get("home")
        return "applied", ""
    if kind == "obligation":
        if data.get("kind") not in registry_mod.OBLIGATION_KINDS:
            return "refused", f"an obligation's kind is one of {', '.join(registry_mod.OBLIGATION_KINDS)}"
        if data.get("date") is not None and not registry_mod._is_date(data.get("date")):
            return "refused", "an obligation's date is YYYY-MM-DD"
        if not _ids(data.get("evidence")):
            return "refused", "an obligation names its evidence (record ids)"
        entry = {"kind": data["kind"]}
        if data.get("date") is not None:
            entry["date"] = str(data["date"])
        entry.update(seen_in=sender, at=at, evidence=_ids(data.get("evidence")))
        known = [o for o in subject.extra.get("obligations") or [] if isinstance(o, dict)]
        if not any(o.get("kind") == entry["kind"] and o.get("date") == entry.get("date") and
                   o.get("seen_in") == sender for o in known):
            subject.extra["obligations"] = known + [entry]
        if c and c.get("state") == "settled" and c.get("class") == "company":
            c.update(state="open", reopened_at=at, reopened_by=sender)
    _mark_checked(reg, c, by, sender)   # an obligation or a check: this workspace has looked
    if data.get("works_on"):
        _propose_crew(reg, c, by)
    return "applied", ""


def land_notes(root, space=None, now=None) -> dict:
    """Apply every registry note that arrived in `_inbound/signals/`; one log line each."""
    import yaml
    root = Path(root)
    space = space or spaceyaml.load(root)
    now = now or space.now()
    out = {"applied": 0, "refused": 0}
    folder = root / SIGNALS_IN
    paths = sorted(folder.glob("*.yaml")) if folder.is_dir() else []
    reg, notes = None, []
    for path in paths:
        try:
            signal = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if isinstance(signal, dict) and signal.get("kind") == "registry":
            notes.append((_when(signal.get("at")), str(signal.get("id") or path.stem), path, signal))
    for _, _, path, signal in sorted(notes, key=lambda n: n[:2]):   # in the order they were made
        reg = reg or registry_mod.Registry(root, space)
        signal.pop("filled", None)   # only `_workspace_repo` says what was filled or waits: never the note
        signal.pop("waiting", None)
        try:
            outcome, why = apply_note(root, space, reg, signal, now)
        except Exception as exc:   # one bad note is refused alone
            outcome, why = "refused", f"an unreadable note ({exc.__class__.__name__})"
        out[outcome] += 1
        _record(root, signal, outcome, why, now)
        path.unlink()
    import needs_you
    if needs_you.health_rows(root, HEALTH_WRITER):   # what the host could not confirm before
        reg = reg or registry_mod.Registry(root, space)
        out["applied"] += retry_workspace_repos(root, space, reg, now)
    if out["applied"]:
        reg.save()
    return out


# ----- the verbs: what this space's agent says ------------------------------------------------------
SENT_LOG = registry_mod.SENT_NOTES
ITEMS = Path("_inbound") / "needs-you" / "classify.yaml"
PROPOSALS = Path("_meta") / "state" / "classify-proposals"
PENDING = Path("_meta") / "state" / "private" / "classify-pending.json"


def _load(root, now=None):
    root = Path(root)
    space = spaceyaml.load(root)
    return root, space, registry_mod.Registry(root, space), now or space.now()


def travelling(space, subject, row: dict) -> dict | None:
    """The classification a row of this space's sent log (`classify.jsonl`) may make govern
    here while its note travels, or None. Only a row whose signal is on its way (waiting in this
    space's `_outbound/signals/<id>.yaml`, or arrived in the holder's `_inbound/signals/` and not
    landed yet) as a registry classification note for the same subject from this space, and
    that the holder's rules still accept as this space's (`_as_holder`, which also binds the
    note to this space's owner), governs; the classification is the one those rules write,
    never the row's own. A row with no such signal, a row the holder refuses, and a holder that
    cannot be read govern nothing (registry.py asks this)."""
    import yaml
    sig = str((row or {}).get("signal") or "")
    if not pages.is_page_id(sig) or space is None or subject is None:
        return None
    holder = space.mount_root(subject.space) if subject.space else None
    signal = None
    for folder in [Path(space.root) / SIGNALS_OUT] + ([Path(holder) / SIGNALS_IN] if holder else []):
        path = folder / f"{sig}.yaml"
        try:
            if pages.is_link(path) or not path.is_file():
                continue
            signal = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError):
            continue
        break
    if not (isinstance(signal, dict) and signal.get("kind") == "registry" and signal.get("note") == "classification"
            and str(signal.get("subject")) == subject.id == str(row.get("subject"))
            and signal.get("from") == space.name and _same(signal.get("by"), space.owner)
            and str(signal.get("id")) == sig):
        return None
    try:
        outcome, _, result = _as_holder(space, subject, signal, space.now())
    except Exception:
        return None
    if outcome != "applied" or not result:
        return None
    return {k: v for k, v in result.items() if k in ("class", "state", "home", "crew", "at", "decided_by", "for")}


def _send(root: Path, sid, kind, data, now) -> Path | None:
    sent: dict = {}
    path = note(root, sid, kind, data, now, sent=sent)
    if kind == "workspace" and (data or {}).get("repo"):
        sent["repo"] = str(data["repo"])
    _append(root / SENT_LOG, json.dumps({"at": pages.iso(now), "note": kind, "subject": str(sid),
                                         "signal": path.stem if path else None, **sent}))
    return path


def send_state(root, sid, state: str, now) -> Path | None:
    """The home tells the registry that holds `sid` it went dormant, woke or was archived at
    offboard; the home reads the new state at once while the note travels (registry.Registry)."""
    path = note(root, sid, "state", {"state": state}, now)
    _append(Path(root) / SENT_LOG, json.dumps({"at": pages.iso(now), "note": "state", "subject": str(sid),
                                               "signal": path.stem if path else None, "state": state}))
    return path


def _mine(space, reg) -> str:
    return person_id(reg, space.owner)


def _gated_or_refuse(reg, sid) -> None:
    if reg.get(str(sid)) is None:
        raise ClassifyError(f"{sid} is not in the registry")
    if not gated(reg, sid):
        raise ClassifyError(f"{sid} is not a client, project or deal, nor part of one")


def _deciding_or_refuse(space, reg, sid) -> str:
    who = decider(space, reg, sid)
    if who not in ("self", "stand_in"):
        owner = subject_owner(reg, sid)
        raise ClassifyError(f"only {owner}'s workspace (or the company owner's while {owner} has none) "
                            f"classifies {sid}" if owner else f"{sid} has no owner yet: say who handles it first")
    return who


def _obligations(reg, sid) -> list:
    return [dict(o, subject=s.id) for s in _chain(reg, str(sid)) if s.id == sid or s.kind in GATED_KINDS
            for o in s.extra.get("obligations") or [] if isinstance(o, dict)]


def reachable(space, home) -> bool:
    return bool(home) and space.space_root(str(home)) is not None


def _needs_evidence(space, reg, sid, cls, evidence, home) -> bool:
    """Under the open default, walled or owner needs a reason in the records: evidence or a
    recorded obligation. A project that has its own space already is walled for its crew, as at
    onboarding: the space is the fact."""
    if cls == "company" or default(space) != "open" or evidence or _obligations(reg, sid):
        return False
    return not (cls == "walled" and home and home != space.name and reachable(space, home))


def announce(root, now=None) -> Path | None:
    """Tell the brain this space is its person's workspace, once: a `workspace` note when the
    person's registry entry does not name this space yet and no such note is waiting to leave.
    The note names this workspace's own repository (its origin remote), which the brain checks
    with the host before it records it as the person's `workspace_repo` (`_workspace_repo`); a
    workspace the brain knows sends it once more, once per repository, while none is recorded."""
    root, space, reg, now = _load(root, now)
    if space.profile != "personal":
        return None
    person = reg.get(_mine(space, reg))
    if person is None or person.kind != "people":
        return None
    try:
        import onboard
        remote = onboard.git_remote(root)
    except Exception:
        remote = None
    remote = remote if remote and registry_mod.clone_url(remote) else None
    if str(person.extra.get("workspace") or "") == space.name:
        # known as this workspace: its repository once more, only while the brain has none and
        # only once for each repository (the brain tries again itself while the host cannot answer)
        sent = {str(r.get("repo") or "") for r in _jsonl(root / SENT_LOG) if r.get("note") == "workspace"}
        if person.extra.get("workspace_repo") or not remote or remote in sent:
            return None
    import yaml
    folder = root / SIGNALS_OUT
    for path in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
        try:
            waiting = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if isinstance(waiting, dict) and waiting.get("kind") == "registry" and waiting.get("note") == "workspace":
            return None
    return _send(root, person.id, "workspace", {"repo": remote} if remote else {}, now)


def ask(root, sid, now=None) -> dict:
    """Ask every other workspace whether its records hold an obligation on `sid`; they answer
    with `obligation` or `checked` notes until one window from now."""
    root, space, reg, now = _load(root, now)
    _gated_or_refuse(reg, sid)
    _deciding_or_refuse(space, reg, sid)
    c = classification_of(reg.get(str(sid)))
    if c and c.get("state") == "settled":
        raise ClassifyError(f"{sid} is classified already")
    mine = _mine(space, reg)
    asked = sorted(p for p, ws in workspaces(reg).items() if p != mine and ws != space.name)
    if not asked:
        return {"asked": [], "ready": True}
    until = pages.iso(now + space.window())
    path = _send(root, sid, "ask", {"asked": asked, "ask_until": until}, now)
    return {"asked": asked, "ready": False, "ask_until": until, "note": path}


def _still_due(c: dict | None, now) -> list:
    """The asked workspaces that have not answered while the ask is still open."""
    if not c or c.get("state") != "asking":
        return []
    try:
        if now >= pages.parse_iso(c.get("ask_until")):
            return []
    except (TypeError, ValueError):
        return []
    return [p for p in _ids(c.get("asked")) if p not in _ids(c.get("answered"))]


def decide(root, sid, cls, reason, evidence=(), crew=(), home=None, by=None, owner=None, now=None) -> dict:
    """Classify `sid`: a `classification` note to the registry that holds it, or, when the class
    needs its owner's yes and `by` (who said it) is not given, a proposal on Needs you."""
    root, space, reg, now = _load(root, now)
    _gated_or_refuse(reg, sid)
    held_by = subject_owner(reg, sid)
    if held_by:
        _deciding_or_refuse(space, reg, sid)
    elif not (by and may_decide_for(space, reg, owner, space.owner)):
        raise ClassifyError(f"{sid} has no owner yet: say who handles it first"
                            if not owner else f"only {owner}'s workspace classifies {sid}")
    if cls not in STRICT:
        raise ClassifyError(f"class must be one of {', '.join(CLASSES)}")
    if not str(reason or "").strip():
        raise ClassifyError("a classification gives its reason")
    evidence, crew = _ids(list(evidence or [])), _ids(list(crew or []))
    if cls == "walled" and (not crew or not home):
        raise ClassifyError("a walled classification names its home and its crew")
    if home and registry_mod.space_name(home) is None:
        raise ClassifyError("a walled home is a space name (a lowercase slug)")
    if _needs_evidence(space, reg, sid, cls, evidence, home):
        raise ClassifyError(f"the company default is open: {cls} needs evidence or a recorded obligation")
    c = classification_of(reg.get(str(sid)))
    due = _still_due(c, now)
    if cls == "company" and due:
        raise ClassifyError(f"{', '.join(due)} may still answer until {c.get('ask_until')}")
    if needs_person(space, c, cls) and not by:
        stamp = now.strftime("%Y%m%d%H%M%S")
        item_id = f"classify-decide-{sid}-{stamp}"
        proposal = {"verb": "decide", "subject": str(sid), "class": cls, "reason": str(reason),
                    "evidence": evidence, "crew": crew, "home": home, "owner": owner}
        path = _propose(root, item_id, proposal)
        title = reg.get(str(sid)).title or sid
        _put_item(root, {"id": item_id, "kind": "question",
                         "text": f"Classify {title} as {cls}? {reason}",
                         "recommendation": "yes", "default": "nothing about it leaves until you answer",
                         "deadline": None, "depends": 0, "payload": path.relative_to(root).as_posix(),
                         "fingerprint": ""})
        return {"proposal": path, "item": item_id}
    mine = _mine(space, reg)
    answered = _ids((c or {}).get("answered"))
    known = workspaces(reg)
    checked_with = [space.name] + [known[p] for p in answered if p in known and known[p] != space.name]
    not_yet = sorted(p for p in people(reg) if p != mine and p not in answered)
    data = {"class": cls, "reason": str(reason), "evidence": evidence,
            "checked_with": checked_with, "not_yet_checked": not_yet}
    if cls == "walled":
        data.update(home=str(home), crew=crew)
    if cls == "company":
        data["obligations_seen"] = len(_obligations(reg, sid))
    if (held_by and decider(space, reg, sid) == "stand_in") or (not held_by and not _same(owner, space.owner)):
        data["for"] = held_by or str(owner)
    if by:
        data["approved_by"] = str(by)
    if owner:
        data["owner"] = str(owner)
    path = _send(root, sid, "classification", data, now)
    out = {"note": path, "class": cls, "mint": None}
    if cls == "walled" and not reachable(space, home):
        out["mint"] = _plan_mint(root, space, reg, str(sid), str(home), crew, now)
    return out


def _plan_mint(root: Path, space, reg, sid, home, crew, now) -> str:
    """A walled subject whose home does not exist yet: the space is made after one window
    unless the person says no; one Needs-you item and one notice say so."""
    stamp = now.strftime("%Y%m%d%H%M%S")
    item_id = f"classify-mint-{sid}-{stamp}"
    when = now + space.window()
    path = _propose(root, item_id, {"verb": "mint", "subject": sid, "home": home, "crew": crew})
    title = reg.get(sid).title or sid
    _put_item(root, {"id": item_id, "kind": "question",
                     "text": f"Make the private space {home} for {title}, shared with {', '.join(crew)}?",
                     "recommendation": "yes", "default": f"the space is made on {when:%Y-%m-%d}",
                     "deadline": pages.iso(when), "depends": 0,
                     "payload": path.relative_to(root).as_posix(), "fingerprint": ""})
    import owner_email
    notice = {"kind": "action", "sender": "classify", "key": item_id,
              "title": f"A private space for {title} is planned",
              "summary": f"Work on {title} will be kept in its own space, shared only with "
                         f"{', '.join(crew)}. It is made on {when:%Y-%m-%d} unless you stop it.",
              "steps": [f"To stop it, answer no to the question about {title} on your Needs you list "
                        f"before {when:%Y-%m-%d}."],
              "reassure": "Nothing leaves your workspace while the space is being made."}
    try:
        owner_email.queue(root, notice)
    except (Exception, SystemExit) as exc:   # the item stands; the notice is a courtesy
        _append(root / SENT_LOG, json.dumps({"at": pages.iso(now), "note": "notice", "subject": sid,
                                             "failed": str(exc)[:200]}))
    return item_id


def set_owner(root, sid, person, evidence, now=None) -> Path | None:
    """Say who holds the relationship with `sid` (only when it has no owner yet)."""
    root, space, reg, now = _load(root, now)
    _gated_or_refuse(reg, sid)
    if subject_owner(reg, sid):
        raise ClassifyError(f"{sid} already has an owner: {subject_owner(reg, sid)}")
    if not str(person or "").strip() or not _ids(list(evidence or [])):
        raise ClassifyError("an owner note names the person and the evidence (record ids)")
    return _send(root, sid, "owner", {"person": str(person).strip(), "evidence": _ids(list(evidence))}, now)


def obligation(root, sid, kind, date=None, evidence=(), works_on=False, now=None) -> Path | None:
    """An obligation this workspace's records hold on `sid`: its kind and date, and the record ids
    that show it. Never its terms, amounts, people or wording."""
    root, space, reg, now = _load(root, now)
    _gated_or_refuse(reg, sid)
    if kind not in registry_mod.OBLIGATION_KINDS:
        raise ClassifyError(f"an obligation's kind is one of {', '.join(registry_mod.OBLIGATION_KINDS)}")
    if date is not None and not registry_mod._is_date(str(date)):
        raise ClassifyError("an obligation's date is YYYY-MM-DD")
    evidence = _ids(list(evidence or []))
    if not evidence:
        raise ClassifyError("an obligation names its evidence (record ids)")
    data = {"kind": kind}
    if date is not None:
        data["date"] = str(date)
    data["evidence"] = evidence
    if works_on:
        data["works_on"] = True
    return _send(root, sid, "obligation", data, now)


def checked(root, sid, works_on=False, now=None) -> Path | None:
    """This workspace looked through its records for `sid` and found no obligation."""
    root, space, reg, now = _load(root, now)
    _gated_or_refuse(reg, sid)
    return _send(root, sid, "checked", {"works_on": True} if works_on else {}, now)


# ----- Needs-you items and proposals ------------------------------------------------------------------
def _items(root: Path) -> list:
    import yaml
    path = root / ITEMS
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [i for i in (data.get("items") if isinstance(data, dict) else data) or [] if isinstance(i, dict)]


def _save_items(root: Path, items: list) -> None:
    import yaml
    path = root / ITEMS
    if not items and not path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"items": items}, sort_keys=False, allow_unicode=True, width=100),
                    encoding="utf-8", newline="\n")


def _put_item(root: Path, item: dict) -> bool:
    """Add an item once: never again after it was listed or answered."""
    import needs_you
    items = _items(root)
    if any(i.get("id") == item["id"] for i in items) or item["id"] in needs_you.answered_ids(root):
        return False
    _save_items(root, items + [item])
    return True


def _propose(root: Path, item_id: str, data: dict) -> Path:
    import yaml
    path = root / PROPOSALS / f"{item_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")
    return path


def _proposal(root: Path, item: dict) -> dict:
    import yaml
    path = root / str(item.get("payload") or "")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None
    except Exception:
        data = None
    return data if isinstance(data, dict) else {}


def _drop_proposal(root: Path, item: dict) -> None:
    rel = str(item.get("payload") or "")
    path = root / rel
    if rel.startswith(PROPOSALS.as_posix() + "/") and path.is_file():
        path.unlink()


# ----- the pending pack: what this space decides, asks or checks --------------------------------
def _waiting_send(root: Path, space, sid, notes, since, now) -> bool:
    """A note of these kinds sent after `since` and not yet in the registry (one window at most)."""
    for row in _jsonl(root / SENT_LOG):
        if str(row.get("subject")) != str(sid) or row.get("note") not in notes:
            continue
        try:
            at = pages.parse_iso(row.get("at"))
        except (TypeError, ValueError):
            continue
        if (since is None or at > since) and now - at < space.window():
            return True
    return False


def _at(c) -> dt.datetime | None:
    try:
        return pages.parse_iso((c or {}).get("at"))
    except (TypeError, ValueError):
        return None


def _held_unclassified(root: Path) -> dict:
    """subject -> held item ids this space keeps back because the subject is unclassified."""
    out: dict = {}
    folder = root / "_outbound" / "held"
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            fm, _ = pages.read(path)
        except Exception:
            continue
        if fm.get("reason") == "unclassified" and fm.get("subject"):
            out.setdefault(str(fm["subject"]), []).append(str(fm.get("id") or path.stem))
    return out


def backlog_subjects(root) -> set:
    """Subjects on an unanswered one-off list: decided there, not one by one."""
    import needs_you
    root = Path(root)
    answered = needs_you.answered_ids(root)
    return {str(row.get("subject")) for item in _items(root)
            if str(item.get("id", "")).startswith("classify-backlog") and item["id"] not in answered
            for row in _proposal(root, item).get("rows") or [] if isinstance(row, dict)}


# ----- existing installs: the one list ---------------------------------------------------------------
def backlog(root, rows, now=None) -> Path:
    """The one-off list for an install that had subjects before the gate: one row per registered
    client, project or deal without a settled classification, judged by the agent, put on Needs
    you as one item. Nothing changes until the person answers it; a home changes only through
    the classifications it makes."""
    root, space, reg, now = _load(root, now)
    clean = []
    for row in rows or []:
        sid = str((row or {}).get("subject") or "")
        subject = reg.get(sid)
        if subject is None or not gated(reg, sid):
            raise ClassifyError(f"{sid or 'a row'}: not a registered client, project or deal")
        c = classification_of(subject)
        if c and c.get("state") == "settled":
            continue
        cls, reason = row.get("class"), str(row.get("reason") or "").strip()
        evidence, crew = _ids(row.get("evidence")), _ids(row.get("crew"))
        if cls not in STRICT or not reason:
            raise ClassifyError(f"{sid}: a row names its class ({', '.join(CLASSES)}) and its reason")
        if cls == "walled" and (not crew or not row.get("home")):
            raise ClassifyError(f"{sid}: a walled row names its home and its crew")
        if _needs_evidence(space, reg, sid, cls, evidence, row.get("home")):
            raise ClassifyError(f"{sid}: the company default is open: {cls} needs evidence")
        owner = subject_owner(reg, sid) or str(row.get("owner") or "").strip()
        if not owner:
            raise ClassifyError(f"{sid}: name who handles it")
        if not may_decide_for(space, reg, owner, space.owner):
            raise ClassifyError(f"{sid}: {owner}'s workspace classifies it")
        clean.append({"subject": sid, "title": subject.title or sid,
                      "owner": str(row.get("owner") or subject_owner(reg, sid) or ""), "class": cls,
                      "reason": reason, "evidence": evidence, "home": row.get("home"), "crew": crew})
    if not clean:
        raise ClassifyError("nothing to list: every row is classified already")
    item_id = f"classify-backlog-{now:%Y%m%d%H%M%S}"
    path = _propose(root, item_id, {"verb": "backlog", "rows": clean})
    lines = [f"{n}. {r['title']}: {r['class']}, owner {r['owner'] or 'nobody yet'}: {r['reason']}"
             for n, r in enumerate(clean, 1)]
    _put_item(root, {"id": item_id, "kind": "question",
                     "text": f"Classify these {len(clean)} clients, projects and deals as listed?\n"
                             + "\n".join("   " + line for line in lines),
                     "recommendation": "yes: classify them as listed", "default": "nothing about them leaves",
                     "deadline": None, "depends": 0, "payload": path.relative_to(root).as_posix(),
                     "fingerprint": ""})
    return path


def _backlog_edits(said: str) -> dict:
    """`2 owner; 5 walled` -> {2: "owner", 5: "walled"}."""
    out = {}
    for part in str(said or "").split(";"):
        words = part.split()
        if len(words) >= 2 and words[0].isdigit() and words[1].lower() in STRICT:
            out[int(words[0])] = words[1].lower()
    return out


def _apply_backlog(root, plan: dict, verb: str, said: str, by, now, out: dict) -> None:
    edits = _backlog_edits(said) if verb == "edit" else {}
    for n, row in enumerate(plan.get("rows") or [], 1):
        try:
            decide(root, row["subject"], edits.get(n, row["class"]), row["reason"], evidence=row.get("evidence") or [],
                   crew=row.get("crew") or [], home=row.get("home"), by=by, owner=row.get("owner") or None, now=now)
            out["applied"] += 1
        except ClassifyError as exc:
            out["errors"].append(f"{row.get('subject')}: {exc}")


def _decide_entry(root, space, reg, subject, c, to_ask, proposals, now) -> dict:
    sid = subject.id
    if sid in proposals or _waiting_send(root, space, sid, ("ask", "classification"), _at(c), now):
        nxt = "wait"
    elif c is None:
        nxt = "ask" if to_ask else "decide"
    elif _still_due(c, now):
        nxt = "wait"
    else:
        nxt = "decide"
    previous = (c or {}).get("previous")
    if previous is None and c and c.get("class"):
        previous = {k: c[k] for k in ("class", "at", "home", "crew") if k in c}
    return {"subject": sid, "title": subject.title, "kind": subject.kind, "next": nxt,
            "state": (c or {}).get("state", "unclassified"),
            "needs_person": [k for k in CLASSES if needs_person(space, c, k)],
            "obligations": _obligations(reg, sid), "asked": _ids((c or {}).get("asked")),
            "answered": _ids((c or {}).get("answered")), "ask_until": (c or {}).get("ask_until"),
            "previous": previous}


def pending(root, now=None) -> dict:
    """Everything this space's agent has to do for the gate, written to the private pack."""
    root, space, reg, now = _load(root, now)
    announce(root, now)
    mine = _mine(space, reg)
    to_ask = sorted(p for p, ws in workspaces(reg).items() if p != mine and ws != space.name)
    import needs_you
    answered = needs_you.answered_ids(root)
    given = _verdicts(root)
    skip = backlog_subjects(root)
    pack = {"default": default(space), "decide": [], "check": [], "owner_unknown": [], "crew": [], "mint_due": []}
    proposals = {str(_proposal(root, i).get("subject")) for i in _items(root)
                 if str(i.get("id", "")).startswith("classify-decide-") and i["id"] not in answered}
    for subject in sorted(reg.everywhere(), key=lambda s: s.id):
        sid = subject.id
        if subject.state == "archived" or not gated(reg, sid):
            continue
        c = classification_of(subject)
        deciding = decider(space, reg, sid) in ("self", "stand_in")
        # a subject this space decides: unclassified at the top, or asking, or re-opened
        if deciding and subject.kind in GATED_KINDS and sid not in skip:
            umbrella = any(a.kind in GATED_KINDS for a in reg.ancestors_of(sid))
            if (c is None and not umbrella) or (c and c.get("state") in ("asking", "open")):
                pack["decide"].append(_decide_entry(root, space, reg, subject, c, to_ask, proposals, now))
        # a subject that asks this space, or names its person as not yet checked
        if c and not deciding and not _waiting_send(root, space, sid, ("obligation", "checked"), _at(c), now):
            why = None
            if c.get("state") == "asking" and mine in _ids(c.get("asked")) and mine not in _ids(c.get("answered")):
                why = "asked"
            elif c.get("state") != "asking" and mine in _ids(c.get("not_yet_checked")):
                why = "not_yet_checked"
            if why:
                pack["check"].append({"subject": sid, "title": subject.title, "why": why,
                                      "class": c.get("class"), "ask_until": c.get("ask_until")})
        # a walled subject of mine that someone else works on too
        if deciding and c and c.get("class") == "walled":
            for pid in _ids(c.get("crew_proposed")):
                item_id = f"classify-crew-{sid}-{pid}"
                pack["crew"].append({"subject": sid, "person": pid, "item": item_id})
                if item_id in answered or any(i.get("id") == item_id for i in _items(root)):
                    continue
                path = _propose(root, item_id, {"verb": "crew", "subject": sid, "person": pid})
                _put_item(root, {"id": item_id, "kind": "question",
                                 "text": f"{pid} also works on {subject.title or sid}. Add them to its crew?",
                                 "recommendation": "yes", "default": "the crew stays as it is",
                                 "deadline": None, "depends": 0,
                                 "payload": path.relative_to(root).as_posix(), "fingerprint": ""})
    # material held here for a subject nobody owns: one question, once
    for sid, held in sorted(_held_unclassified(root).items()):
        if reg.get(sid) is None or subject_owner(reg, sid):
            continue
        item_id = f"classify-owner-{sid}"
        pack["owner_unknown"].append({"subject": sid, "held": len(held), "item": item_id})
        if item_id in answered or any(i.get("id") == item_id for i in _items(root)):
            continue
        path = _propose(root, item_id, {"verb": "owner", "subject": sid, "person": space.owner,
                                        "evidence": held[:5]})
        title = reg.get(sid).title or sid
        _put_item(root, {"id": item_id, "kind": "question",
                         "text": f"Who handles {title}? Nothing about it leaves until its owner classifies it.",
                         "recommendation": "yes if it is you, or edit with their name",
                         "default": "it stays held here", "deadline": None, "depends": 0,
                         "payload": path.relative_to(root).as_posix(), "fingerprint": ""})
    # planned spaces: due once their window passed (or the person said yes); gone once they exist
    items, kept = _items(root), []
    for item in items:
        iid = str(item.get("id", ""))
        if iid.startswith("classify-mint-"):
            plan = _proposal(root, item)
            if reachable(space, plan.get("home")):
                _drop_proposal(root, item)
                continue
            verb = (given.get(iid) or {}).get("verb")
            try:
                passed = now >= pages.parse_iso(item.get("deadline"))
            except (TypeError, ValueError):
                passed = False
            if verb != "no" and (passed or verb in ("yes", "always")):
                pack["mint_due"].append({"subject": plan.get("subject"), "home": plan.get("home"),
                                         "crew": _ids(plan.get("crew")), "item": iid})
        kept.append(item)
    if len(kept) != len(items):
        _save_items(root, kept)
    target = root / PENDING
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(pack, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8",
                      newline="\n")
    return pack


# ----- the person's answers -----------------------------------------------------------------------
def _verdicts(root) -> dict:
    import status
    return status.verdicts(root)


def apply_verdicts(root, now=None) -> dict:
    """Act on the person's answers to this tool's items; answered items leave the file (a
    planned space stays until it exists, so a yes can make it now)."""
    pages.refuse_copy(root)
    root, space, reg, now = _load(root, now)
    given = _verdicts(root)
    out = {"applied": 0, "dropped": 0, "errors": []}
    kept, before = [], _items(root)
    for item in before:
        iid = str(item.get("id", ""))
        verdict = given.get(iid) or {}
        verb = verdict.get("verb")
        by, said = verdict.get("by") or space.owner, str(verdict.get("note") or "").strip()
        if not verb or verb == "later":
            kept.append(item)
            continue
        plan = _proposal(root, item)
        sid = str(plan.get("subject") or "")
        if iid.startswith("classify-mint-"):
            if verb == "no":
                _drop_proposal(root, item)
                out["dropped"] += 1
            else:
                kept.append(item)
            continue
        try:
            if verb == "no":
                out["dropped"] += 1
            elif iid.startswith("classify-backlog"):
                _apply_backlog(root, plan, verb, said, by, now, out)
            elif iid.startswith("classify-workspace-repo-"):
                if verb in ("yes", "always"):
                    _confirm_workspace_repo(root, plan, by, now)
                    out["applied"] += 1
            elif iid.startswith("classify-owner-"):
                person = said if verb == "edit" and said else plan.get("person")
                set_owner(root, sid, person, plan.get("evidence") or [iid], now=now)
                out["applied"] += 1
            elif iid.startswith("classify-decide-"):
                cls = said.split()[0].lower() if verb == "edit" and said else plan.get("class")
                decide(root, sid, cls, plan.get("reason"), evidence=plan.get("evidence") or [],
                       crew=plan.get("crew") or [], home=plan.get("home"), by=by, owner=plan.get("owner"),
                       now=now)
                out["applied"] += 1
            elif iid.startswith("classify-crew-"):
                c = classification_of(reg.get(sid)) or {}
                if c.get("class") == "walled":
                    decide(root, sid, "walled", c.get("reason") or "the crew grew",
                           evidence=_ids(c.get("evidence")) or [iid],
                           crew=_ids(c.get("crew")) + [str(plan.get("person"))], home=c.get("home"),
                           by=by, now=now)
                    out["applied"] += 1
        except ClassifyError as exc:
            out["errors"].append(f"{iid}: {exc}")
        _drop_proposal(root, item)
        _append(root / SENT_LOG, json.dumps({"at": pages.iso(now), "note": "answer", "item": iid,
                                             "verb": verb, "subject": sid}))
    listed = {i.get("id") for i in before}   # items an answer made (a planned space) stay
    _save_items(root, kept + [i for i in _items(root) if i.get("id") not in listed])
    return out


# ----- the command line ---------------------------------------------------------------------------------
def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root")
    sub = ap.add_subparsers(dest="verb", required=True)
    for verb in ("pending", "apply", "land", "announce"):
        sub.add_parser(verb)
    p = sub.add_parser("ask")
    p.add_argument("subject")
    p = sub.add_parser("decide")
    p.add_argument("subject")
    p.add_argument("--class", dest="cls", required=True, choices=CLASSES)
    p.add_argument("--reason", required=True)
    p.add_argument("--evidence", nargs="*", default=[])
    p.add_argument("--crew", nargs="*", default=[])
    p.add_argument("--home")
    p = sub.add_parser("owner")
    p.add_argument("subject")
    p.add_argument("--person", required=True)
    p.add_argument("--evidence", nargs="+", required=True)
    p = sub.add_parser("obligation")
    p.add_argument("subject")
    p.add_argument("--kind", required=True, choices=list(registry_mod.OBLIGATION_KINDS))
    p.add_argument("--date")
    p.add_argument("--evidence", nargs="+", required=True)
    p.add_argument("--works-on", action="store_true")
    p = sub.add_parser("checked")
    p.add_argument("subject")
    p.add_argument("--works-on", action="store_true")
    p = sub.add_parser("backlog")
    p.add_argument("file")
    args = ap.parse_args(argv)
    try:
        if args.verb == "pending":
            result = pending(args.root)
        elif args.verb == "apply":
            result = apply_verdicts(args.root)
        elif args.verb == "land":
            result = land_notes(args.root)
        elif args.verb == "announce":
            result = {"note": announce(args.root)}
        elif args.verb == "ask":
            result = ask(args.root, args.subject)
        elif args.verb == "decide":
            result = decide(args.root, args.subject, args.cls, args.reason, evidence=args.evidence,
                            crew=args.crew, home=args.home)
        elif args.verb == "owner":
            result = {"note": set_owner(args.root, args.subject, args.person, args.evidence)}
        elif args.verb == "obligation":
            result = {"note": obligation(args.root, args.subject, args.kind, date=args.date,
                                         evidence=args.evidence, works_on=args.works_on)}
        elif args.verb == "checked":
            result = {"note": checked(args.root, args.subject, works_on=args.works_on)}
        else:
            import yaml
            data = yaml.safe_load(Path(args.file).read_text(encoding="utf-8")) or []
            result = {"list": backlog(args.root, data.get("rows") if isinstance(data, dict) else data)}
    except ClassifyError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
