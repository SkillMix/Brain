"""classify: the classification gate (docs/superpowers/specs/2026-10-16-classification-gate-design.md).

A client, project or deal (or anything part of one) sends nothing out of the space where its
material came in until its owner's agent has classified it: `company` (home the brain), `walled`
(a named crew, home their space) or `owner` (stays in the space it came in). The classification
and the obligations seen (their kind and date only) live on the subject's entry in the registry
every space reads through the brain. A space changes that registry with small notes: signals of
`kind: registry`, addressed to the space whose registry holds the entry, applied there by
`land_notes` (every ship and every landing). This tool decides nothing: the agent judges, the
tool checks who may say what and enforces the gate at every exit.

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
NOTE_KINDS = ("owner", "workspace", "ask", "classification", "obligation", "checked")
NOTES_LOG = registry_mod.NOTES_LOG
SIGNALS_OUT = Path("_outbound") / "signals"
SIGNALS_IN = Path("_inbound") / "signals"


def _append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line.rstrip("\n") + "\n")


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
    _append(root / NOTES_LOG, json.dumps({"id": signal.get("id"), "at": pages.iso(now), "note": signal.get("note"),
                                          "subject": signal.get("subject"), "from": signal.get("from"),
                                          "by": signal.get("by"), "outcome": outcome, "why": why}, default=str))
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
        person.extra["workspace"] = sender
        return "applied", ""
    subject = reg.subjects.get(sid)
    if subject is None:
        return "refused", f"{sid} is not held in this registry"
    c = classification_of(subject)
    if kind == "owner":
        if subject_owner(reg, sid):
            return "refused", f"{sid} already has an owner: {subject_owner(reg, sid)}"
        if not str(data.get("person") or "").strip() or not _ids(data.get("evidence")):
            return "refused", "an owner note names the person and the evidence"
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
        if cls == "walled" and (not data.get("home") or not _ids(data.get("crew"))):
            return "refused", "a walled classification names its home and crew"
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
        try:
            outcome, why = apply_note(root, space, reg, signal, now)
        except Exception as exc:   # one bad note is refused alone
            outcome, why = "refused", f"an unreadable note ({exc.__class__.__name__})"
        out[outcome] += 1
        _record(root, signal, outcome, why, now)
        path.unlink()
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


def _send(root: Path, sid, kind, data, now) -> Path | None:
    sent: dict = {}
    path = note(root, sid, kind, data, now, sent=sent)
    _append(root / SENT_LOG, json.dumps({"at": pages.iso(now), "note": kind, "subject": str(sid),
                                         "signal": path.stem if path else None, **sent}))
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
    return bool(home) and (home == space.name or space.mount_root(str(home)) is not None)


def _needs_evidence(space, reg, sid, cls, evidence, home) -> bool:
    """Under the open default, walled or owner needs a reason in the records: evidence or a
    recorded obligation. A project that has its own space already is walled for its crew, as at
    onboarding: the space is the fact."""
    if cls == "company" or default(space) != "open" or evidence or _obligations(reg, sid):
        return False
    return not (cls == "walled" and home and home != space.name and reachable(space, home))


def announce(root, now=None) -> Path | None:
    """Tell the brain this space is its person's workspace, once: a `workspace` note when the
    person's registry entry does not name this space yet and no such note is waiting to leave."""
    root, space, reg, now = _load(root, now)
    if space.profile != "personal":
        return None
    person = reg.get(_mine(space, reg))
    if person is None or person.kind != "people" or str(person.extra.get("workspace") or "") == space.name:
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
    return _send(root, person.id, "workspace", {}, now)


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
