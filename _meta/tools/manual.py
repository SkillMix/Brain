"""The manual: the tab "Your <company> brain" on the progress page, written in the company's own
names and shown first once part 1 of the history is read.

It answers what a newcomer asks the day the first stretch of history is in: what is connected,
where it lives, what runs by itself and when, what to say to it, who is in it, what happens next,
what it costs and what it never does. Nothing on it is written by hand. `facts` reads the setup
journal, the plan of the history and the workspace itself, so the page the sync script rewrites
every few minutes stays true; `render` turns the facts into HTML and escapes every value, because
an app name, a profile line or a client's name is not ours to trust.

    import manual
    facts = manual.facts(workspace, journal, now)
    html = manual.render(facts)

Plain words, as everywhere the person reads: clock times, a share of their own limits, no command
names, no file paths. A fact the data does not hold is left out; for a phrase that leaves the
phrase and drops its example.
"""
from __future__ import annotations

import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spaceyaml  # noqa: E402

# What it never does: spec 5.1's five things, each in one plain sentence.
NEVER = (
    "It never signs anything for you.",
    "It never sends anything to anyone but you.",
    "It never deletes anything.",
    "It never gives anyone access.",
    "It never spends beyond your limits.",
)

# Phrases that need nothing from the data, shown after the others, each with what it can take.
FIXED_PHRASES = (
    ("Change the plan", "read only the last 30 days"),
    ("Pause my routines", "until tomorrow morning, or a date"),
)
# What each phrase does, in a few words.
DOES = {
    "What needs me": "lists what is waiting for your yes.",
    "Status of a client": "tells you where it stands.",
    "Prep me for the call with a contact or client": "gathers what you need before the call.",
    "Invite a colleague": "prepares their invitation for you to hand over.",
    "Skip an app": "stops reading that app.",
    "Change the plan": "changes how far back your history is read, or how fast.",
    "Pause my routines": "stops them for a while, then they start again by themselves.",
}

# A name in a line of the profile: two or more capitalised words. What those words say when they are no name.
NAME = re.compile(r"\b([A-Z][a-z]+(?:[ -][A-Z][a-z]+)+)\b")
NOT_A_NAME = {"team", "manager", "director", "lead", "officer", "head", "founder", "chief", "company", "group", "limited",
              "partners", "consultant", "engineer", "designer", "developer", "office", "board", "the", "our", "and", "of"}
HEAD = 8192   # bytes of a page read to see whom it is about
RECENT = 300   # the newest pages looked at when no routing log counts the records: reading a page costs
ROUTING = Path("_meta") / "state" / "routing.jsonl"


def _page():
    """The progress page's readers (the plan, the routines, the clock): imported when needed, as the
    page imports this module."""
    import onboarding_page
    return onboarding_page


def _safe(make, default):
    """One fact that cannot be read leaves the others standing."""
    try:
        return make()
    except Exception:
        return default


# ----- the facts -------------------------------------------------------------------------------
def facts(workspace, journal: dict, now) -> dict:
    """{"company", "apps", "workspace_repo", "brain_repo", "routines": [(time, what)],
    "phrases": [(phrase, example)], "people": [names], "next": [lines], "usage": str}."""
    import registry
    page, ws = _page(), Path(workspace)
    journal = journal if isinstance(journal, dict) else {}
    space = spaceyaml.load(ws)
    brain = _safe(lambda: space.mount_root("brain"), None)
    apps = [app for app, _ in _safe(lambda: page.apps_read(journal), [])]
    reg = _safe(lambda: registry.load(ws), None)   # read once: the people and the clients are in it
    return {
        "company": _safe(lambda: _company(page, ws, journal), ""),
        "apps": apps,
        "workspace_repo": _repo(ws),
        "brain_repo": _repo(brain) if brain else "",
        "routines": _safe(lambda: _routines(page, ws, journal), []),
        "phrases": _safe(lambda: _phrases(page, ws, apps, reg), [("What needs me", "")]),
        "people": _safe(lambda: _people(reg), []),
        "next": _safe(lambda: _next(page, ws, journal, now), []),
        "usage": _safe(lambda: _usage(page, ws, now), ""),
    }


def _company(page, ws: Path, journal: dict) -> str:
    """The brain's own name for the company, else the one the research found."""
    named = page.context({"workspace": ws})["company"]
    if named:
        return named
    card = (journal.get("research") or {}).get("company") if isinstance(journal.get("research"), dict) else None
    return str(card.get("name") or "") if isinstance(card, dict) else str(card or "")


def _repo(root) -> str:
    """owner/name of the repository on the platform, else the folder's name."""
    import onboard
    return onboard.git_remote(root) or Path(root).name


def _routines(page, ws: Path, journal: dict) -> list:
    """(time, what) for what runs by itself: the rendered routines at their clock times, then the
    sync task by its interval. The history's night routine only while there is history to read."""
    import onboard
    plan = page.plan_or_empty(ws)
    reading = any(p.get("status") != "done" for p in page.parts_of(plan))
    out = []
    for name, schedule in page.rendered_routines(ws):
        key, times = page.routine_key(name), onboard.daily_times(schedule)
        if times and (key != "backfill-night" or reading):
            out.append((times[0], _joined([f"{h:02d}:{m:02d}" for h, m in times]), page.routine_what(key)))
    rows = [(when, what) for _, when, what in sorted(out)]
    return rows + [(f"every {page.sync_minutes(journal)} minutes", "Everything that changed is saved to your repositories.")]


def _joined(items: list) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _phrases(page, ws: Path, apps: list, reg) -> list:
    """(phrase, example) for what to say, spec 5.1's list (the fixed ones come after, in `FIXED_PHRASES`). The
    examples are the company's own: the client with the most records and the next one for a call, a colleague the
    profile names, the first app that is read. What the data does not give leaves the phrase alone."""
    clients = _safe(lambda: _ranked_clients(ws, reg), [])
    status = clients[0] if clients else ""
    prep = (clients[1] if len(clients) > 1 else status)
    colleague = _safe(lambda: _colleague(page, ws, reg), "")
    app = apps[0] if apps else ""
    return [("What needs me", ""),
            ("Status of a client", f"Status of {status}" if status else ""),
            ("Prep me for the call with a contact or client", f"Prep me for the call with {prep}" if prep else ""),
            ("Invite a colleague", f"Invite {colleague}" if colleague else ""),
            ("Skip an app", f"Skip {app}" if app else "")]


def _front(path: Path) -> dict:
    """The front matter of a page, read from its first bytes only: a history is thousands of pages."""
    with open(path, "rb") as fh:
        head = fh.read(HEAD).decode("utf-8", errors="replace")
    end = head.find("\n---", 3) if head.startswith("---") else -1
    data = yaml.load(head[3:end], Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)) if end > 0 else None
    return data if isinstance(data, dict) else {}


def _routed_counts(ws: Path) -> Counter:
    """Records per subject as the router placed them: one line a record in the routing log."""
    counts: Counter = Counter()
    path = ws / ROUTING
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("decision") == "route":
            counts.update({str(r.get("subject")) for r in row.get("routes") or [] if isinstance(r, dict)})
    return counts


def _witness_counts(ws: Path) -> Counter:
    """Records per subject among the newest pages the sweep wrote (the router is off, so no log counts them).
    A page that is not what it should be counts for nothing and costs nothing."""
    import registry
    base = ws / "knowledge" / "sources"
    found = []
    for path in base.rglob("*.md") if base.is_dir() else []:
        try:
            found.append((path.stat().st_mtime, path))
        except OSError:
            continue
    counts: Counter = Counter()
    for _, path in sorted(found, reverse=True)[:RECENT]:
        try:
            counts.update(set(registry.routed_subjects(_front(path), None)))
        except Exception:
            continue
    return counts


def _ranked_clients(ws: Path, reg) -> list:
    """The company's clients by how many records are about each (the counts), most first, those with none in
    the registry's order after them: from the routing log, else from the newest pages. Empty when no record is
    about a client, so the examples are dropped and not guessed."""
    clients = {s.id: s.title or s.id for s in reg.everywhere() if s.kind == "clients"} if reg is not None else {}
    counts = _routed_counts(ws) if clients else Counter()
    if clients and not any(sid in clients for sid in counts):
        counts = _witness_counts(ws)
    if not any(sid in clients for sid in counts):
        return []
    return [clients[sid] for sid in sorted(clients, key=lambda sid: -counts.get(sid, 0))]


def _colleague(page, ws: Path, reg) -> str:
    """A colleague to invite: a person the company profile names among its people (never the owner, nor the
    company itself), else another person in the registry; "" when there is none."""
    owner = str(spaceyaml.load(ws).owner or "").strip().lower()
    company = str(page.context({"workspace": ws})["company"] or "").lower()
    try:
        people = page.profile_lines().get("people")
    except Exception:
        people = None
    for line in people if isinstance(people, list) else []:
        for found in NAME.finditer(str(line.get("text") or "") if isinstance(line, dict) else ""):
            name = found.group(1)
            if name.lower() not in (owner, company) and not NOT_A_NAME & set(re.split(r"[ -]", name.lower())):
                return name
    return next((n for n in _people(reg) if n.lower() != owner), "")


def _people(reg) -> list:
    """The people the company's registry names, in its order."""
    seen = []
    for subject in reg.everywhere() if reg is not None else []:
        name = subject.title or subject.id
        if subject.kind == "people" and name not in seen:
            seen.append(name)
    return seen


def _next(page, ws: Path, journal: dict, now) -> list:
    """What happens next, in a line each: the history, the briefing, and what waits for the person."""
    plan, lines = page.plan_or_empty(ws), []
    parts = page.parts_of(plan)
    todo = [(i, p) for i, p in enumerate(parts, 1) if p.get("status") != "done"]
    if todo:
        lines.append(f"The rest of your history is read oldest first: part {todo[0][0]} of {len(parts)} is next.")
        if page.stamp(plan.get("current_by")):
            lines.append(f"All of it should be read by {page.words(plan.get('current_by'), now)}.")
    elif parts:
        lines.append("All of your history is read. From now on only what is new is read.")
    briefing = page.next_start(ws, now, "daily")
    if briefing:
        lines.append(f"Your next morning briefing comes {briefing[1]}.")
    waiting = page.needs_you_count(ws)
    if waiting:
        many = waiting != 1
        lines.append(f"{waiting} thing{'s are' if many else ' is'} waiting for you. Say \"What needs me\" to go through "
                     f"{'them' if many else 'it'}.")
    elif not journal.get(page.UNREAD):   # the setup notes hold the other things that wait: unread, nothing is claimed
        lines.append("Nothing is waiting for you.")
    return lines


def _usage(page, ws: Path, now) -> str:
    """Where the person's limits stand, and how the work is paced to leave them room."""
    return page.usage_line(page.plan_or_empty(ws), now)


# ----- the tab ---------------------------------------------------------------------------------------
def title(facts: dict) -> str:
    return f"Your {facts.get('company') or 'company'} brain"


def _e(text) -> str:
    return html.escape(str(text), quote=True)


def _list(items) -> str:
    items = list(items)
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>" if items else ""


def _card(heading: str, body: str) -> str:
    return f'<div class="card"><h3>{_e(heading)}</h3>{body}</div>' if body else ""


def render(facts: dict) -> str:
    """The tab as HTML: every value from `facts` is escaped, none is taken as markup."""
    facts = facts if isinstance(facts, dict) else {}
    company = facts.get("company") or "your company"
    apps = [a for a in facts.get("apps") or []]
    lead = (f"Your agents read {_joined([_e(a) for a in apps])} for {_e(company)} and file what they find. "
            if apps else f"Your agents work for {_e(company)}. ") + "This is how to use what they build."
    where = _list(line for line in (
        f"Your own workspace, which only you read: <code>{_e(facts['workspace_repo'])}</code>" if facts.get("workspace_repo") else "",
        f"The company brain, which everyone reads: <code>{_e(facts['brain_repo'])}</code>" if facts.get("brain_repo") else "")
        if line)
    runs = _list(f"<strong>{_e(when)}</strong> {_e(what)}" for when, what in facts.get("routines") or [])
    phrases = _list([*(_phrase(phrase, example) for phrase, example in facts.get("phrases") or []),
                     *(_phrase(phrase, example, fixed=True) for phrase, example in FIXED_PHRASES)])
    people = _list(_e(name) for name in facts.get("people") or [])
    after = _list(_e(line) for line in facts.get("next") or [])
    usage = f"<p>{_e(facts['usage'])}</p>" if facts.get("usage") else ""
    return (f'<section class="manual" id="manual"><h2>{_e(title(facts))}</h2><p class="lead">{lead}</p>'
            '<div class="cards">'
            + _card("Where it lives", where) + _card("What runs by itself", runs)
            + _card("What to say to it", f"<p>Say it in your own words. These work well.</p>{phrases}")
            + _card("Who is in it", people) + _card("What happens next", after) + _card("What it costs", usage)
            + _card("What it never does", _list(_e(line) for line in NEVER))
            + "</div></section>")


def _phrase(phrase: str, example: str, fixed: bool = False) -> str:
    """The words to say, what they do and, when the data gave one, an example in the company's own names. A fixed
    phrase takes what follows it, shown in brackets."""
    does = f' <span class="does">{_e(DOES[phrase])}</span>' if phrase in DOES else ""
    if not example:
        eg = ""
    elif fixed:
        eg = f' <span class="eg">({_e(example)})</span>'
    else:
        eg = f' <span class="eg">For example: <q>{_e(example)}</q></span>'
    return f'<span class="say">{_e(phrase)}</span>{does}{eg}'
