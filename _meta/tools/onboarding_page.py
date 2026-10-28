"""The onboarding page: one self-contained HTML file that shows a newcomer, in as few words as
it can, where their setup stands, what only they can do, and how the system works, the last as
a diagram.

It is rendered from the onboarding report, the rows `start.py` and `onboard.py --check` print,
each with its check; nothing on it is written by hand, so the progress it shows is the
report's. `start.py` rewrites it after every step and once at the end, `onboard.py --check`
rewrites it from the recorded onboarding and the read-backs, and the fixture writes one beside
the spaces it builds. The rows themselves stay in the agent's terminal: the page shows only the
stages, the one thing the person must do, and the first briefing's time.

Once a workspace exists the page also holds six sections (`sections`: what is happening now, what was
found, the history by part, the usage, what waits, the settings) and, once part 1 of the history is read,
the manual (`manual.py`) as its first tab. The sync script rewrites it after every run (`refresh`) from the
setup journal and the workspace's own files, without the model, so it stays true while setup works on its own.

It lives at `~/.spaces/onboarding.html`, outside every repository, beside the machine's list
of spaces: it names the person's sign-ins, it exists before any space does (a doctor that
stops), and a file that changes on every run would open a sync pull request each time. No
server, no scripts, no external fonts or images; dark by default, light by a switch in the
banner that needs no script. The house style, and the brand, are in
`_meta/templates/onboarding.css`, the one brand file.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

TOOLS = Path(__file__).resolve().parent
THEME = TOOLS.parents[1] / "_meta" / "templates" / "onboarding.css"
TEMPLATE_ROUTINES = TOOLS.parents[1] / "_meta" / "templates" / "routines"
ROUTINES = Path("_meta") / "state" / "routines"
RUNS = Path("_meta") / "state" / "runs.jsonl"
STATE = Path("_meta") / "state" / "onboard.yaml"
REFRESH_SECONDS = 2

# The stages of a setup, in order, each with the step names that belong to it (by prefix). A
# step no stage names belongs to "Connect".
STAGES = (
    ("Your machine", ("doctor:", "one start at a time")),
    ("Your spaces", ("invitation", "namespace", "dry run", "create ", "fill ", "remove ", "registry")),
    ("Connect", ()),
    ("First run", ("AGENTS.md", "ids ", "first sweep", "status ", "validate", "answers ", "space.yaml validates",
                   "routine prompts")),
    ("Routines", ("routine",)),
    ("Sources", ("connect ",)),
)
OTHER = 2
LABELS = {"done": "Done", "running": "Running now", "next": "Next", "needs-you": "Needs you",
          "failed": "Did not work"}


def page_path() -> Path:
    return Path.home() / ".spaces" / "onboarding.html"


# ----- the report, grouped --------------------------------------------------------------
def _row(step) -> dict:
    row = step.row() if hasattr(step, "row") else dict(step)
    return {"name": str(row.get("name") or ""), "done": bool(row.get("done")),
            "check": str(row.get("check") or ""), "result": str(row.get("result") or ""),
            "needs_owner": bool(row.get("needs_owner"))}


def stage_of(name: str) -> int:
    for i, (_, prefixes) in enumerate(STAGES):
        if any(name.startswith(p) for p in prefixes):
            return i
    return OTHER


def _state(rows: list, running: bool) -> str:
    if any(not r["done"] and not r["needs_owner"] for r in rows):
        return "failed"
    if any(not r["done"] for r in rows):
        return "needs-you"
    return "running" if running else "done"


def stages(rows: list, live: bool = False, workspace=None, now=None, readback: bool = True) -> list:
    """Each stage with its rows and its state. A stage with no rows is next, unless a later
    stage has rows: then it did not apply to this setup and is left out. While a run is live,
    the stage of its latest row is the one running. `readback` False takes the routines' schedule
    from what the onboarding recorded and asks the scheduler nothing: the page the sync script
    rewrites starts no process."""
    groups = [[] for _ in STAGES]
    for row in rows:
        groups[stage_of(row["name"])].append(row)
    for i, fact in ((4, _routines_fact(workspace, readback)), (5, _sources_fact(workspace))):
        if fact and not groups[i]:
            groups[i].append(fact)   # read from the workspace itself: only what is already there
    latest = stage_of(rows[-1]["name"]) if live and rows else None
    rendered = _rendered(workspace)   # routines to schedule: their stage is never left out
    out = []
    for i, (title, _) in enumerate(STAGES):
        group = groups[i]
        if not group and any(groups[i + 1:]) and not (i == 4 and rendered):
            continue
        state = _state(group, i == latest) if group else "next"
        out.append({"title": title, "state": state, "rows": group, "sub": "", "note": _note(state, group, title)})
    ready = all(s["state"] == "done" for s in out if s["title"] != "Sources")
    out.append(_briefing(workspace, now, ready))
    return out


# What the stage running now is doing, in plain words: a step's own name and result stay in the agent's terminal and
# the setup journal, never on the page (R45, Minor 3).
RUNNING_NOTES = {"Your machine": "checking this computer.", "Your spaces": "making your spaces.",
                 "Connect": "connecting your spaces.", "First run": "the first run in your spaces.",
                 "Routines": "setting up your routines.", "Sources": "connecting your sources."}
FAILED_NOTE = "{title}: a step did not work. Your agent has the details and says what to do."


def _note(state: str, rows: list, title: str = "") -> str:
    """The one line under the stages: what the stage running now does, or that one of its steps did not work."""
    if state == "running":
        return f"Now: {RUNNING_NOTES.get(title, 'setting up.')}"
    if state == "failed":
        return FAILED_NOTE.format(title=title or "Setup")
    return ""


# ----- facts read from the workspace ----------------------------------------------------
def _routines(workspace) -> list:
    """(name, schedule) of the person's routines: those rendered into the workspace, else the
    template's, which every workspace gets."""
    folder = Path(workspace) / ROUTINES if workspace else None
    files = sorted(folder.glob("*.md")) if folder and folder.is_dir() else []
    out = []
    for path in files or sorted(TEMPLATE_ROUTINES.glob("*.md")):
        try:
            meta = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1]) or {}
        except (IndexError, yaml.YAMLError, OSError):
            continue
        out.append((str(meta.get("name") or path.stem), str(meta.get("schedule") or "")))
    return out


def _rendered(workspace) -> bool:
    folder = Path(workspace) / ROUTINES if workspace else None
    return bool(folder and folder.is_dir() and any(folder.glob("*.md")))


def _routines_fact(workspace, readback: bool = True):
    """Routines are done once their schedule reads back (`onboard.routine_schedule`: an OS task,
    or a persistent harness task with its recorded read-back); rendered prompts schedule nothing.
    None while nothing is scheduled: the stage is next. Without `readback` nothing is asked of the
    scheduler: the stage is done when the onboarding recorded tasks or read-backs, and says so."""
    if not _rendered(workspace):
        return None
    if not readback:
        try:
            state = _yaml(Path(workspace) / STATE)
        except Unreadable:
            return None   # the Waiting section says the record could not be read
        recorded = [r for key in ("routine_tasks", "routine_checks") if isinstance(state.get(key), list)
                    for r in state[key] if isinstance(r, dict)]
        return {"name": "routines", "done": True, "check": "", "needs_owner": False,
                "result": "scheduled: as recorded when it was set up"} if recorded else None
    import onboard
    rows = [_row(s) for s in onboard.routine_schedule(workspace)]
    if not rows:
        return None
    failed = next((r for r in rows if not r["done"]), None)
    return failed or {"name": "routines", "done": True, "check": rows[0]["check"],
                      "result": "scheduled: the schedule reads back", "needs_owner": False}


def _sources_fact(workspace):
    try:
        sources = [s.get("name") for s in spaceyaml.load(workspace).sources if isinstance(s, dict)]
    except Exception:
        return None
    if not sources:
        return None
    return {"name": "sources", "done": True, "check": f"grep -c sources {Path(workspace).as_posix()}/space.yaml",
            "result": "connected: " + ", ".join(map(str, sources)), "needs_owner": False}


def _run_lines(workspace) -> list:
    path = Path(workspace) / RUNS if workspace else None
    if path is None or not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            run = json.loads(line)
        except ValueError:
            continue
        if isinstance(run, dict):
            out.append(run)
    return out


def _first_time(schedule: str) -> str:
    """The first time of day a daily cron line runs at, `30 6 * * *` as 06:30; "" otherwise."""
    fields = str(schedule).split()
    if len(fields) != 5 or fields[2:] != ["*", "*", "*"]:
        return ""
    try:
        h, m = min((int(h), int(m)) for h in fields[1].split(",") for m in fields[0].split(","))
    except ValueError:
        return ""
    return f"{h:02d}:{m:02d}"


def _briefing(workspace, now, ready: bool = True) -> dict:
    """The first morning briefing: sent, or when it comes; "tomorrow" only once setup is done."""
    import morning_view
    sent = [r for r in _run_lines(workspace) if r.get("routine") in morning_view.SEND_ROUTINES
            and isinstance(r.get("notes"), dict) and (r["notes"].get("email") or r["notes"].get("message_id"))]
    if sent:
        return {"title": "Briefing", "state": "done", "rows": [], "note": "",
                "sub": f"sent {_local(sent[0].get('at'))[:10]}"}
    if offline(workspace):   # nothing is scheduled: no time to promise (R45, F-10)
        return {"title": "Briefing", "state": "next", "rows": [], "note": "", "sub": ""}
    when = _first_time(next((s for n, s in _routines(workspace) if n == "daily-chain"), ""))
    return {"title": "Briefing", "state": "next", "rows": [], "note": "",
            "sub": f"tomorrow {when}".strip() if ready else when}


def _local(value) -> str:
    try:
        return pages.parse_iso(value).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value or "")


# ----- who and where ----------------------------------------------------------------------
def _loaded(spaces: dict) -> list:
    out = []
    for path in dict.fromkeys(Path(p) for p in (spaces or {}).values()):
        try:
            out.append((path, spaceyaml.load(path)))
        except Exception:   # a space being filled right now
            continue
    return out


def context(spaces: dict) -> dict:
    """The person's workspace, its owner and the company, read from the spaces themselves."""
    import onboard
    loaded = _loaded(spaces)
    ws = next(((p, s) for p, s in loaded if s.profile == "personal"), None)
    brain = next(((p, s) for p, s in loaded if s.profile == "brain"), None)
    if brain is None and ws is not None and ws[1].mount_root("brain") is not None:
        try:
            root = ws[1].mount_root("brain")
            brain = (root, spaceyaml.load(root))
        except Exception:
            brain = None
    company = onboard.company_name(*brain) if brain else ""
    return {"workspace": ws[0] if ws else None, "person": ws[1].owner if ws else "", "company": company}


# ----- the six sections: the journal, the plan of the history and the workspace, in plain words -----------
# The setup journal (`setup_journal`), `_meta/state/backfill.yaml` (the plan of the history, in parts),
# `runs.jsonl` (what each routine run ended in), `stale.yaml` (what the sync script saw not start) and
# `onboard.yaml` (what the scheduler said) hold the facts; the sync script rewrites the page from them
# every few minutes, so nothing here asks the model. Every value is untrusted: it is escaped when it is
# drawn, and a file that cannot be read becomes a line saying so, never an error.
PLAN = Path("_meta") / "state" / "backfill.yaml"
STALE = Path("_meta") / "state" / "stale.yaml"
NEEDS_YOU = Path("_inbound") / "needs-you"
UNREAD = "_unread"   # a key the page puts in a journal it could not read
SECTIONS = (("now", "Happening now"), ("found", "What was found"), ("history", "The history, oldest first"),
            ("usage", "Your usage"), ("waiting", "Waiting"), ("settings", "Settings"))
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
PACE = ("By day, from 08:00 to 19:00, your history leaves at least half of every five hours free. At night it may use "
        "up to 90%. The first day takes at most 30% of what is left of your week.")
COSTS = {
    "signins": ("Signing in to GitHub and Claude.", "Signing in costs nothing."),
    "apps": ("Looking at your connected apps, one at a time, to see which hold work.",
             "Each app is read briefly, once. That uses a little of your Claude limit."),
    "research": ("Reading up on your company: its site, public records and documents.",
                 "This is read once. It uses a little of your Claude limit."),
    "build": ("Building your spaces.", "Building uses almost none of your Claude limit."),
    "history": ("Reading your history, oldest first.",
                "Reading your history is the big one. It uses your Claude limit, so it is paced: by day it leaves at "
                "least half of every five hours free, and it never runs into paid, extra usage."),
}
PHASES = tuple(COSTS)
# What a routine is called to the person, and whether the name is plural; by the name of its prompt without "-chain".
ROUTINES_BY_NAME = {"daily": ("morning briefing", False), "sweep": ("sweeps", True), "backfill-night": ("history reading", False)}
ROUTINE_DOES = {"daily": "Your morning briefing is made and sent to you.",
                "sweep": "What is new in your connected apps is read and filed.",
                "backfill-night": "Your history is read, oldest first, until it is all in."}
NEED_LABELS = {"github-code": "Sign in to GitHub", "claude-signin": "Sign in to Claude", "connect-app": "Connect an app",
               "namespace": "Choose where your repositories live", "it-admin": "Ask your IT administrator",
               "reconnect": "Sign in to an app again"}
# What a routine costs while it runs, by the name of its prompt without "-chain" ("" for any other).
ROUTINE_COSTS = {
    "daily": "It makes your briefing from what is already in your workspace. That uses a little of your Claude limit.",
    "sweep": "Sweeps read what is new in your apps. That uses a little of your Claude limit.",
    "": "It uses a little of your Claude limit.",
}
# A part is read now only when a routine holds the lock for it; one the plan calls `running` (a run that died)
# waits for its next turn.
PART_STATES = {"done": "Done", "paused": "Paused", "pending": "Waiting its turn", "running": "Waiting its turn"}
# The keys the plan of the history (`backfill.yaml`) keeps the first reading of the person's limits and the latest
# under, as `backfill.record_reading` writes them: one place to change if that ever does.
FIRST_READING, LATEST_READING = "setup_reading", "reading"
HELD = "waiting"   # the plan's flag, a plain reason: set while a paid account's history waits for the person's go
LOCK_HOURS = 2   # a run stops after an hour; a live process on an older lock is another one's (Ruling R11)
LOCAL_ZONE = None   # this computer's own zone; None asks the system, the tests set another
STATIC_REFRESH_SECONDS = 300   # an open tab of the page the sync script rewrites follows it
STOP_DAYS = 7   # a stop older than this is left off the page: the routine that stopped was probably taken away
UNKNOWN = "Not known: your setup notes could not be read this time."
STAGES_TROUBLE = "The list of setup steps could not be read in full this time."
UNBUILT = "This page could not be built this time. It is tried again at the next sync."
SETTINGS = {   # the interview's key: (what it is called to the person, how its default reads)
    "domains": ("Areas of the company", None),
    "never_leaves": ("Kept inside the space that holds it", None),
    "classification_default": ("New clients and projects", {
        "open": "shared across the company unless their owner keeps them apart",
        "closed": "kept apart until their owner says they may be shared"}),
    "schedule": ("How often your work is landed", lambda minutes: f"every {minutes} minutes" if minutes.isdecimal() and len(minutes) <= 4 else minutes),
    "org": ("Where the repositories live", None),
    "feeds": ("Apps to read", None),
    "company": ("The company", None), "owner": ("Who signs for the company", None),
    "owner_email": ("Where the briefing goes", None), "email": ("Where the briefing goes", None),
    "name": ("Your name", None), "parent": ("Your parent space", None),
}
URL = re.compile(r"https://[^\s<>\"'`]+")
CARD_SEP = " · "   # between the fields of a line of the app card (`apps.SEP`)


class Unreadable(Exception):
    """A file that is there but cannot be read as what it should be."""


def _yaml(path) -> dict:
    """A mapping from a YAML file; {} when there is no file, Unreadable when there is one and it is not a mapping."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise Unreadable(type(exc).__name__) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise Unreadable("not a mapping")
    return data


def _fact(label: str, text) -> dict:
    return {"label": label, "text": str(text)}


def _could_not_read(what: str) -> dict:
    return _fact("Could not read", f"{what} could not be read this time. It is tried again at the next sync.")


def _plan(workspace) -> dict:
    """The plan of the history; {} when there is none. A plan whose parts are not a list is unreadable."""
    plan = _yaml(Path(workspace) / PLAN) if workspace else {}
    if "parts" in plan and not isinstance(plan["parts"], list):
        raise Unreadable("parts")
    return plan


def plan_or_empty(workspace) -> dict:
    try:
        return _plan(workspace)
    except Unreadable:
        return {}


def parts_of(plan: dict) -> list:
    parts = plan.get("parts") if isinstance(plan, dict) else None
    return [p for p in parts if isinstance(p, dict)] if isinstance(parts, list) else []


def _text(value) -> str:
    """One line of a plain value; "" for anything else, and for a number that is no number (`.inf`)."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return " ".join(str(value).split())


# ----- moments, in the words a person says them -------------------------------------------------------
def _on_this_clock(when: dt.datetime) -> dt.datetime:
    return when.astimezone(LOCAL_ZONE) if LOCAL_ZONE else when.astimezone()


def stamp(value):
    """A moment as it was written, keeping the zone it was written in; one written with none is on this
    computer's own clock (the reset time `routine.outcome` writes is local, to the second, with no zone), and
    `Z` is UTC. None when it is not a moment."""
    if isinstance(value, dt.datetime):
        got = value
    elif isinstance(value, str):
        text = value.strip()
        text = text[:-1] + "+00:00" if text[-1:] in ("Z", "z") else text
        try:
            got = dt.datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if got.tzinfo is None:
        got = got.replace(tzinfo=LOCAL_ZONE) if LOCAL_ZONE else got.astimezone()
    return got


def _shown(when: dt.datetime) -> dt.datetime:
    """A moment on the clock it was written for: a time written in UTC, which says nothing to a person,
    on this computer's clock."""
    return _on_this_clock(when) if when.utcoffset() == dt.timedelta(0) else when


def _date(value):
    """A day: 2026-08-25 as written, or the day of a moment."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _day(day) -> str:
    day = _date(day)
    return f"{day.day} {MONTHS[day.month - 1]}" if day else ""


def _clock(value) -> str:
    when = stamp(value)
    return f"{_shown(when):%H:%M}" if when else ""


def words(value, now, on: bool = False) -> str:
    """`today at 23:20`, `tomorrow at 06:30`, `yesterday at 04:30`, else `27 October at 12:00` (with `on` first
    when the sentence needs it); "" for what is not a moment."""
    when = stamp(value)
    if when is None:
        return ""
    when, today = _shown(when), _shown(now).astimezone(_shown(when).tzinfo).date()
    gap = (when.date() - today).days
    clock = f"{when:%H:%M}"
    if gap in (-1, 0, 1):
        return f"{('yesterday', 'today', 'tomorrow')[gap + 1]} at {clock}"
    return f"{'on ' if on else ''}{_day(when)} at {clock}"


def _short(value, now) -> str:
    """A clock time on the day it falls today, else in words."""
    when = stamp(value)
    if when is None:
        return ""
    same = _shown(when).date() == _shown(now).astimezone(_shown(when).tzinfo).date()
    return _clock(value) if same else words(value, now)


def _pct(value):
    try:
        return f"{round(float(value))}%"
    except (TypeError, ValueError):
        return ""


# ----- the routines, by name and by clock ----------------------------------------------------------------
def routine_key(name) -> str:
    key = str(name).lower().replace("_", "-")
    return key[:-len("-chain")] if key.endswith("-chain") else key


def _routine_name(key: str) -> tuple:
    return ROUTINES_BY_NAME.get(key) or (" ".join(re.split(r"[-\s]+", key)).strip() or "routine", False)


def routine_what(key: str) -> str:
    said = ROUTINE_DOES.get(key)
    return said or f"{_routine_name(key)[0][:1].upper()}{_routine_name(key)[0][1:]} runs."


OFFLINE_NEXT = ("Your routines start once setup has run online; nothing is scheduled on this computer yet, so no "
                "time is promised.")


def offline(workspace) -> bool:
    """The workspace was set up offline (onboard.yaml's `offline`): its routines were rendered, never scheduled."""
    if not workspace:
        return False
    try:
        return bool(_yaml(Path(workspace) / STATE).get("offline"))
    except Unreadable:
        return False


def rendered_routines(workspace) -> list:
    """(name, schedule) of the routines rendered into the workspace and scheduled; the template's are not, and
    neither are those of a workspace set up offline (R45, F-10)."""
    return _routines(workspace) if _rendered(workspace) and not offline(workspace) else []


def sync_minutes(journal) -> int:
    """Minutes between the sync runs: the journal's `schedule` default when it is a whole number of minutes
    in a day, else 15 (what the template asks for)."""
    rows = (journal or {}).get("settings")
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("key") == "schedule":
            value = row.get("default")
            value = int(value) if isinstance(value, str) and value.strip().isdecimal() and len(value.strip()) <= 4 else value
            if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 24 * 60:
                return value
            break
    return 15


def next_start(workspace, now, key=None):
    """(name key, when in words) of the next time a rendered routine starts (only the one called `key`
    when given); None when none is scheduled at a clock time."""
    import onboard
    here, found = _shown(now), []
    for name, schedule in rendered_routines(workspace):
        if key and routine_key(name) != key:
            continue
        for hour, minute in onboard.daily_times(schedule) or []:
            for gap in (0, 1):
                at = dt.datetime.combine(here.date() + dt.timedelta(days=gap), dt.time(hour, minute), here.tzinfo)
                if at > here:
                    found.append((at, routine_key(name)))
                    break
    if not found:
        return None
    at, name = min(found)
    return name, words(at, now)


# ----- what the journal holds ----------------------------------------------------------------------------
def _card_names(lines) -> list:
    """The fields of each line of the app card: `Gmail · ada@lanternworks.example · work`."""
    if not isinstance(lines, list):
        return []
    return [[part.strip() for part in str(line).split(CARD_SEP)] for line in lines if str(line).strip()]


def _skips(record: dict) -> set:
    """The apps the person said to skip (`person_skips`): whatever the card or the verdicts still say, not read."""
    return {_text(s) for s in record.get("person_skips") or []} if isinstance(record.get("person_skips"), list) else set()


def apps_read(journal) -> list:
    """(app, account) of the apps that are read: the app card's lines, else the verdicts; never one the person
    skipped, though the card was written before their word was saved (R47, N3-2)."""
    record = (journal or {}).get("apps")
    record = record if isinstance(record, dict) else {}
    card = record.get("card") if isinstance(record.get("card"), dict) else {}
    skipped = _skips(record)
    if card.get("read"):
        return [(parts[0], parts[1] if len(parts) > 1 else "") for parts in _card_names(card["read"])
                if parts[0] and parts[0] not in skipped]
    return [(_text(v.get("app")), _text(v.get("account"))) for v in record.get("verdicts") or []
            if isinstance(v, dict) and v.get("verdict") in ("work", "mixed") and _text(v.get("app"))
            and _text(v.get("app")) not in skipped]


def profile_lines() -> dict:
    """The company profile the research kept: {section: [{"text", "source", "at"}]}. Unreadable raises."""
    import research
    return _yaml(research.profile_path())


def _needs_you(workspace) -> tuple:
    """(items, files that could not be read) in the workspace's Needs you files (one file a routine)."""
    count, bad = 0, 0
    for path in sorted((Path(workspace) / NEEDS_YOU).glob("*.yaml")) if workspace else []:
        try:
            items = _yaml(path).get("items")
        except Unreadable:
            bad += 1
            continue
        count += sum(1 for item in items if isinstance(item, dict)) if isinstance(items, list) else 0
    return count, bad


def needs_you_count(workspace) -> int:
    return _needs_you(workspace)[0]


def _https(link) -> str:
    """An address a link may point to: https, one word, nothing that could close an attribute; else ""."""
    return link if isinstance(link, str) and URL.fullmatch(link) else ""


def _need_label(kind) -> str:
    return NEED_LABELS.get(str(kind), "Needs you")


# ----- limits, in a share of the person's own -------------------------------------------------------------
def latest_reading(plan: dict):
    """The latest reading of the person's limits the plan keeps; None when it keeps none."""
    reading = plan.get(LATEST_READING) if isinstance(plan, dict) else None
    return reading if isinstance(reading, dict) else None


def usage_line(plan: dict, now) -> str:
    """Where the person's limits stand and how the work is paced to leave them room (the manual's "What it costs")."""
    said = limits_text(latest_reading(plan), now)
    return (f"{said} " if said else "") + PACE


def running_run(workspace, now):
    """The routine run that holds this workspace's lock now, as {"routine", "part"}: read from the lock the run
    writes (`~/.spaces/locks/<space name>.lock`: pid, routine, started, the part of the history it reads and its
    process's start time), and only read, by the rule of `routine._live`: a lock whose process runs and started when
    the lock says is that run's however old it is (a lid closed for hours); one whose process started at another time
    is another process's. A lock without a start time, or a system that does not say one, keeps the age rule: None
    when it is from the future or older than LOCK_HOURS. None too when there is no lock or it cannot be read."""
    try:
        import routine
        name = spaceyaml.load(workspace).name
        data = routine._lock_data((Path.home() / ".spaces" / "locks" / f"{name}.lock").read_bytes())
        if data is None or not routine.pid_alive(data["pid"]):
            return None
        held = {"routine": _text(data.get("routine")), "part": _text(data.get("part"))}
        mark = routine.process_started(data["pid"]) if data["pid_started"] else None
        if mark:
            return held if mark == data["pid_started"] else None
        age = now - data["started"]
        if age < -dt.timedelta(minutes=5) or age >= dt.timedelta(hours=LOCK_HOURS):
            return None
        return held
    except Exception:   # no lock, a lock that is not one, no way to ask: no run to show
        return None


def _num(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def limits_text(reading, now, started: bool = False) -> str:
    """`20% of your 5-hour limit is used; it resets today at 18:20. 31% of your week ...`; "" for no reading.
    `started` writes it as it stood when setup began."""
    if not isinstance(reading, dict):
        return ""
    five, week = _pct(reading.get("five_hour_pct")), _pct(reading.get("week_pct"))
    if started:
        used = " and ".join(u for u in (f"{five} of your 5-hour limit" if five else "", f"{week} of your week" if week else "") if u)
        return f"{used} were used when setup began." if used else ""
    said = []
    for share, name, resets in ((five, "5-hour limit", reading.get("five_hour_resets")), (week, "week", reading.get("week_resets"))):
        if share:
            when = words(resets, now, on=True)
            said.append(f"{share} of your {name} is used" + (f"; it resets {when}." if when else "."))
    if not said:
        return ""
    if reading.get("extra_usage") is True and (_num(reading.get("five_hour_pct")) or 0) >= 100:
        said.append("Extra usage is on, so nothing starts until it resets.")
    at = words(reading.get("at"), now)
    return " ".join(said) + (f" Read {at}." if at else "")


# ----- the six sections ----------------------------------------------------------------------------------
def sections(workspace, journal: dict, now) -> dict:
    """The page's six sections, each a list of {"label", "text"}: what is happening now and why it costs,
    what was found, the history by part, the usage, what waits and the settings. A workspace of None or a
    journal of {} gives lines that say nothing has happened yet; a file that cannot be read gives a line
    that says so, and one section's failure never takes the others with it."""
    ws = Path(workspace) if workspace else None
    journal = journal if isinstance(journal, dict) else {}
    out = {}
    for key, build in (("now", _now_rows), ("found", _found_rows), ("history", _history_rows),
                       ("usage", _usage_rows), ("waiting", _waiting_rows), ("settings", _settings_rows)):
        try:
            out[key] = build(ws, journal, now)
        except Exception:
            out[key] = [_fact("Could not read", "This part of the page could not be read this time.")]
    return out


def _now_rows(ws, journal, now) -> list:
    """What runs now: the routine that holds the workspace's lock (and the part of the history it reads), else
    a phase of the setup the journal says is running. The plan's own `status` never says it: only a live run does."""
    parts = parts_of(plan_or_empty(ws))
    run = running_run(ws, now) if ws else None
    phase = next((name for name in PHASES if isinstance(journal.get(name), dict) and journal[name].get("state") == "running"), None)
    if phase == "history" and parts:
        phase = None   # the history is between parts: what runs is a part, and none does now
    if run:
        key = routine_key(run["routine"])
        at = next(((i, p) for i, p in enumerate(parts, 1) if run["part"] and _text(p.get("id")) == run["part"]), None)
        if at or run["part"]:
            span = " to ".join(d for d in (_day(at[1].get("from")), _day(at[1].get("to"))) if d) if at else ""
            said = f"Reading your history, part {at[0]} of {len(parts)}" + (f" ({span})." if span else ".") if at \
                else COSTS["history"][0]
            return [_fact("Running now", said), _fact("Why it costs", COSTS["history"][1])]
        name, plural = _routine_name(key)
        return [_fact("Running now", f"Your {name} {'are' if plural else 'is'} running."),
                _fact("Why it costs", ROUTINE_COSTS.get(key, ROUTINE_COSTS[""]))]
    if phase:
        return [_fact("Running now", COSTS[phase][0]), _fact("Why it costs", COSTS[phase][1])]
    rows = [_fact("Running now", "Nothing is running at this moment.")]
    if offline(ws):
        return rows + [_fact("Next", OFFLINE_NEXT)]
    soon = next_start(ws, now) if ws else None
    if soon:
        name = _routine_name(soon[0])[0]
        rows.append(_fact("Next", f"{name[:1].upper()}{name[1:]}, {soon[1]}."))
    return rows


def _found_rows(ws, journal, now) -> list:
    record = journal.get("apps") if isinstance(journal.get("apps"), dict) else {}
    card = record.get("card") if isinstance(record.get("card"), dict) else {}
    rows = []
    read = [f"{app} ({account})" if account else app for app, account in apps_read(journal)]
    rows.append(_fact("Apps read", UNKNOWN if journal.get(UNREAD) else ", ".join(read) if read else "No app has been read yet."))
    skipped = []
    for parts in _card_names(card.get("skip")):
        if parts[0]:
            who = f"{parts[0]} ({parts[1]})" if len(parts) > 1 else parts[0]
            skipped.append(who + (f": {', '.join(parts[2:])}" if len(parts) > 2 else ""))
    if skipped:
        rows.append(_fact("Apps left out", "; ".join(skipped)))
    unsigned = [parts[0] for parts in _card_names(card.get("not_signed_in")) if parts[0]]
    if unsigned:
        rows.append(_fact("Not signed in", ", ".join(unsigned)))
    seen = [f"{parts[0]}: {', '.join(p for p in parts[1:] if p)}" if any(parts[1:]) else parts[0]   # no why: the name
            for parts in _card_names(card.get("not_read")) if parts[0]]
    if seen:
        rows.append(_fact("Seen, not read yet", "; ".join(seen)))
    nothing = [parts[0] for parts in _card_names(card.get("nothing_to_read")) if parts[0]]
    if nothing:
        rows.append(_fact("Connected, nothing to read", ", ".join(nothing)))
    starts = stamp(card.get("starts_after"))
    if starts is not None and starts > now:
        rows.append(_fact("Nothing is copied before", f"{_short(starts, now)}, so you can still say what to leave out."))
    rows += _profile_rows()
    rows += _month_rows(record)
    return rows


def _profile_rows() -> list:
    try:
        profile = profile_lines()
    except Unreadable:
        return [_could_not_read("The company profile")]
    import onboard
    import research
    rows = []
    for section in research.PROFILE_SECTIONS:
        lines = []
        for line in profile.get(section) or []:
            if not isinstance(line, dict) or not _text(line.get("text")):
                continue
            source = ", ".join(x for x in (_text(line.get("source")), _text(line.get("at"))[:10]) if x)
            lines.append(_text(line["text"]) + (f" (source: {source})" if source else ""))
        if lines:
            rows.append(_fact(onboard.PROFILE_TITLES.get(section, section.capitalize()), "\n".join(lines)))
    return rows or [_fact("The company profile", "No company profile has been read yet.")]


def _months(per_month: dict) -> str:
    out = []
    for key in sorted(per_month):
        match = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", str(key))
        count = per_month[key]
        if match and isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            out.append(f"{MONTHS[int(match.group(2)) - 1]} {match.group(1)}: {count:,}")
    return "; ".join(out)


def _month_rows(record: dict) -> list:
    """How much each app read holds, month by month (what the history is planned from)."""
    rows, total, skipped = [], {}, _skips(record)
    for verdict in record.get("verdicts") or []:
        if not isinstance(verdict, dict) or verdict.get("verdict") not in ("work", "mixed"):
            continue
        if _text(verdict.get("app")) in skipped:   # the person's word, saved after the verdicts (R47, N3-2)
            continue
        per_month = verdict.get("per_month") if isinstance(verdict.get("per_month"), dict) else {}
        said = _months(per_month)
        if said:
            rows.append(_fact(f"{_text(verdict.get('app'))} by month", said))
            for key, count in per_month.items():
                if isinstance(count, int) and not isinstance(count, bool):
                    total[key] = total.get(key, 0) + count
    return rows + ([_fact("All apps by month", _months(total))] if len(rows) > 1 else [])


def _part_name(part: dict, index: int) -> str:
    number = re.sub(r"\D", "", str(part.get("id") or ""))
    return f"Part {number or index}"


def _read_up_to(part: dict) -> str:
    if part.get("status") == "done":
        return _day(part.get("to"))
    marks = part.get("checkpoints")
    marks = [m for m in (stamp(v) for v in marks.values()) if m] if isinstance(marks, dict) else []
    return _day(_shown(min(marks))) if marks else ""


def _history_rows(ws, journal, now) -> list:
    try:
        plan = _plan(ws)
    except Unreadable:
        return [_fact("The plan", "The plan for your history could not be read this time. It is tried again at the next sync.")]
    parts = parts_of(plan)
    if not parts:
        return [_fact("The plan", "Your history has not been planned yet.")]
    days, start = plan.get("window_days"), _day(parts[0].get("from"))
    plan_text = (f"The last {days} days" if isinstance(days, int) and not isinstance(days, bool) else "Your history") \
        + (f", from {start}." if start else ".")
    if all(p.get("status") == "done" for p in parts):
        plan_text += " All of it is read."
    elif words(plan.get("current_by"), now):
        plan_text += f" All of it should be read by {words(plan.get('current_by'), now)}."
    if _text(plan.get("shortened_because")):
        plan_text += f" {_text(plan['shortened_because'])}"
    rows = [_fact("The plan", plan_text)]
    run = running_run(ws, now)   # the part a routine holds the lock for is the one being read
    for i, part in enumerate(parts, 1):
        status = _text(part.get("status"))
        span, records = " to ".join(d for d in (_day(part.get("from")), _day(part.get("to"))) if d), part.get("records")
        reading = bool(run and run["part"] and run["part"] == _text(part.get("id")) and status != "done")
        said = ["Reading now." if reading else f"{PART_STATES.get(status) or (status.capitalize() if status else 'Waiting its turn')}."]
        if span:
            said.append(f"{span}, {records:,} records." if isinstance(records, int) and not isinstance(records, bool) else f"{span}.")
        if _read_up_to(part):
            said.append(f"Read up to {_read_up_to(part)}.")
        rows.append(_fact(_part_name(part, i), " ".join(said)))
    return rows


def _usage_rows(ws, journal, now) -> list:
    try:
        plan = _plan(ws)
    except Unreadable:
        return [_could_not_read("The record of your usage")]
    rows = [_fact("Your limits", limits_text(latest_reading(plan), now) or "Your limits have not been read yet.")]
    start = limits_text(plan.get(FIRST_READING), now, started=True)
    if start:
        rows.append(_fact("At the start", start))
    share = plan.get("per_token_share")
    for i, part in enumerate(parts_of(plan), 1):
        tokens = part.get("tokens")
        if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens > 0:
            if isinstance(share, (int, float)) and not isinstance(share, bool) and share > 0:
                used = tokens * share
                rows.append(_fact(_part_name(part, i), f"{tokens:,} tokens, " + (
                    f"about {round(used)}% of a 5-hour limit." if used >= 0.5 else "less than 1% of a 5-hour limit.")))
            else:
                rows.append(_fact(_part_name(part, i), f"{tokens:,} tokens. The share of your limit is worked out once "
                                                       "a part has been measured."))
    return rows


def _waiting_rows(ws, journal, now) -> list:
    rows = []
    if journal.get(UNREAD):
        rows.append(_fact("Could not read", "Your setup notes could not be read this time. What your workspace holds is "
                                            "shown instead. It is tried again at the next sync."))
    for need in journal.get("needs") or [] if isinstance(journal.get("needs"), list) else []:
        if isinstance(need, dict) and _text(need.get("text")):
            link = _https(need.get("link"))   # a need's own link, and only that, is a link
            rows.append(_fact(_need_label(need.get("kind")), _text(need["text"]) + (f" {link}" if link else "")))
    waiting, unreadable = _needs_you(ws) if ws else (0, 0)
    if waiting:
        many = waiting != 1
        rows.append(_fact("Needs you", f"{waiting} thing{'s are' if many else ' is'} waiting for you in your Needs you list. "
                                       f"Say \"What needs me\" to go through {'them' if many else 'it'}."))
    if unreadable:
        rows.append(_could_not_read("Part of your Needs you list"))
    paused = False
    try:
        plan = _plan(ws)
    except Unreadable:
        plan = {}
        rows.append(_could_not_read("The plan for your history"))
    for i, part in enumerate(parts_of(plan), 1):
        if part.get("status") == "paused":
            paused = True
            idle = part.get("idle") if isinstance(part.get("idle"), int) and not isinstance(part.get("idle"), bool) else 0
            if idle >= IDLE_RUNS:   # runs that keep ending without reading it: someone should look (R45, Minor 1)
                rows.append(_fact("Needs a look", IDLE_TEXT.format(name=_part_name(part, i), runs=idle)))
            else:
                rows.append(_fact("Paused", _paused_text(part, _part_name(part, i), now)))
    if _held(plan, running_run(ws, now) if ws else None):
        rows.append(_fact("Waiting for your word", "Your history is ready to read when you say so."))
    rows += _sync_rows(ws, journal, now)
    note = (journal.get("build") or {}).get("routines_note") if isinstance(journal.get("build"), dict) else None
    if _text(note):   # the routines cannot send the briefing: said, so nobody waits for it (R45, I-6)
        rows.append(_fact("Your briefing", _text(note)))
    rows += _stopped_rows(ws, now, skip=("backfill-night",) if paused else ())
    rows += _host_rows(ws, now)
    rows += _scheduler_rows(ws)
    return rows or [_fact("Waiting", "Nothing is waiting for you.")]


IDLE_RUNS = 3
IDLE_TEXT = ("{name} of your history was not read in its last {runs} runs, though each was meant to read it. Ask your "
             "agent to look at what those runs did.")
NOT_SAVED = ("What this computer changed is not shared yet: the other computer changed the same files, so the last "
             "sync could not take its changes in. It tries again at every sync; if this stays, ask your agent to look.")


def _sync_rows(ws, journal, now) -> list:
    """A line when the newest sync run stopped before it could take in the other computer's changes (sync.py's run line
    with `conflict`), and it is recent: within two syncs and five minutes (R45, I-1). Plain words: no git's."""
    syncs = [r for r in _run_lines(ws) if r.get("routine") == "sync"] if ws else []
    if not syncs:
        return []
    last = syncs[-1]
    notes = last.get("notes") if isinstance(last.get("notes"), dict) else {}
    when = stamp(last.get("at"))
    if not notes.get("conflict") or when is None:
        return []
    if now - when > dt.timedelta(minutes=2 * sync_minutes(journal) + 5):
        return []
    return [_fact("Not saved", NOT_SAVED)]


HOST_UNCLEAR = ("Which computer runs your routines is unclear, so none of them runs. Say 'run my routines here' on the "
                "computer that should run them.")


HOST_ELSEWHERE = "Your routines run on {name}. Say 'run my routines here' to run them on this computer."
HOST_CLOUD = ("Your routines run in the cloud, as two scheduled tasks. Say 'run my routines here' to bring them to this "
              "computer.")
HOST_STOPPED = ("Your routines are stopped: the cloud no longer refuses {what}, so its two scheduled tasks stay off. "
                "Say 'run my routines here' to run them on this computer.")


def _host_rows(ws, now) -> list:
    """The lines on where the routines run, from the record every computer reads (`routine.host`); only read.
    Paused: until when. Unclear: that none runs. Another computer: that they run there, or, once the sync script saw
    them not start, since when they have not. The cloud is no computer, so it is never named as one: they run there as
    two scheduled tasks, or have not since when. Stopped (`onboard.cloud_stopped`: in the cloud, whose two tasks stay
    off once a proof showed a refusal missing): that they are, what the cloud no longer refuses, and 'run my routines
    here', in place of both, and of the pause, since resuming starts nothing while the cloud's tasks stay off. A
    routine paused, or one nobody may run, is not one the scheduler failed to start, so then no `not started` line;
    with nothing recorded, those lines are as the sync script wrote."""
    import onboard
    import routine
    until, away, cloud, stopped = None, "", False, {}
    try:
        record = routine.host(ws) if ws else None
        if record is not None and not record.get("unclear"):
            until, cloud = routine.paused_until(record, now), record["runner"] == "cloud"
            mine = record["machine"] == routine.this_machine()
            away = "" if cloud or mine else (_text(record["name"]) or "another computer")   # the cloud is no computer
            stopped = onboard.cloud_stopped(ws) if cloud else {}
    except Exception:   # never a file it could not read (`routine.host` reads that as unclear): said, not hidden
        return [_could_not_read("Where your routines run")]
    if record is None:
        return _missed_rows(ws, now)
    if record.get("unclear"):
        return [_fact("Routines", HOST_UNCLEAR)]
    if stopped:   # nothing runs them, so neither a run the sync script saw not start nor a pause says anything more
        return [_fact("Routines", HOST_STOPPED.format(what=onboard.unrefused_words(stopped)))]
    rows = [_fact("Routines", f"Routines paused until {words(until, now)}. Say 'resume my routines' to start them "
                              "sooner.")] if until else []
    if not (away or cloud):
        return rows or _missed_rows(ws, now)
    there = HOST_CLOUD if cloud else HOST_ELSEWHERE.format(name=away)
    return rows + (([] if until else _missed_rows(ws, now, away, cloud)) or [_fact("Routines", there)])


def _held(plan: dict, run) -> bool:
    """The plan is made and waits for the person's go (a paid account: `backfill.hold` sets the plan's `waiting`
    reason, `backfill.start` removes it), and no run is reading it."""
    return bool(plan.get(HELD)) and not run


def _why_kind(why) -> str:
    """What stopped a run, from the words it was given (the command line's, or `backfill.WHY`, its plain version
    of them): "overage", "limit", "auth", "timeout", "unfinished" or "". The words themselves never reach the page."""
    said = str(why or "").lower()
    if "extra usage" in said:
        return "overage"
    if "limit" in said:
        return "limit"
    if "sign-in" in said or "sign in" in said or "log in" in said or "login" in said:
        return "auth"
    if "an hour" in said:
        return "timeout"
    if "fifty minutes" in said or "50 minutes" in said:
        return "unfinished"
    if "did not read" in said:
        return "idle"
    return ""


CAUSES = {"overage": " because it would have used extra, paid usage", "limit": " because your Claude limit was reached",
          "auth": " because Claude needs you to sign in again", "timeout": " because it ran longer than an hour",
          "unfinished": " after a long run", "idle": " because its last run ended without reading it"}


def _at(value, now) -> str:
    """`at 07:10` for a time today, else with its day: `yesterday at 07:10`, `on 20 October at 18:20`."""
    when = stamp(value)
    if when is None:
        return ""
    same = _shown(when).date() == _shown(now).astimezone(_shown(when).tzinfo).date()
    return f"at {_clock(when)}" if same else words(when, now, on=True)


def _on_day(value, now) -> str:
    """` yesterday` or ` on 18 October` for a moment that is not from today; "" for today and for what is no moment."""
    when = stamp(value)
    if when is None:
        return ""
    when, today = _shown(when), _shown(now).astimezone(_shown(when).tzinfo).date()
    gap = (when.date() - today).days
    return "" if gap == 0 else " yesterday" if gap == -1 else f" on {_day(when)}"


def _paused_text(part: dict, name: str, now) -> str:
    """Why a part paused and what happens next. A reset time is spoken of only where there is one to speak of:
    given, or a limit (or extra usage) that did not give it. A sign-in runs out and the person signs in again; a
    run that ran out of time, or its fifty minutes, goes on at the next run, which is the ordinary state between two."""
    kind = _why_kind(part.get("why"))
    text = f"{name} of your history is paused{CAUSES.get(kind, '')}."
    resets = stamp(part.get("resets_at"))
    if kind == "auth":
        return text + " Sign in again and it carries on from where it got to."
    if resets is not None:
        if resets > now:
            return text + f" It can start again {words(resets, now)}, when your Claude limit resets."
        return text + f" Your Claude limit should have reset {_at(resets, now)}, so it starts again at its next run."
    if kind in ("limit", "overage"):
        return text + " The reset time wasn't given, so it tries again at its next run."
    if kind in ("timeout", "unfinished"):
        return text + " It carries on from where it got to at its next run."
    if kind == "idle":
        return text + " It tries again at its next run."
    return text


def _stopped_rows(ws, now, skip=()) -> list:
    """The last run of each routine, when it did not finish and nothing later did, and not older than STOP_DAYS;
    one that is not from today says on which day."""
    latest: dict = {}
    for line in _run_lines(ws) if ws else []:
        if line.get("outcome") and line.get("routine"):
            key = routine_key(line["routine"])
            before = latest.get(key)
            if before is None or (stamp(line.get("at")) or now) >= (stamp(before.get("at")) or now):
                latest[key] = line
    rows = []
    for key, line in latest.items():
        name = _routine_name(key)[0]
        outcome, resets, at = line["outcome"], stamp(line.get("resets_at")), stamp(line.get("at"))
        if key in skip or outcome not in ("limit", "overage", "auth", "timeout"):
            continue
        if at is not None and now - at > dt.timedelta(days=STOP_DAYS):
            continue   # a routine that was taken away leaves its last stop behind: it goes after a week
        subject, day = f"Your {name}", _on_day(at, now)
        if outcome == "auth":
            text = f"{subject} stopped{day} because Claude needs you to sign in again. The step is in your Needs you list."
        elif outcome == "limit":
            text = f"{subject} stopped at your Claude limit{day}." + (
                " The reset time wasn't given." if resets is None else
                f" It resets {words(resets, now, on=True)}." if resets > now else
                f" It should have reset {_at(resets, now)}, so it starts again at its next time.")
        elif outcome == "overage":
            text = f"{subject} stopped{day} before it ran into extra, paid usage. Routines never do that on their own."
        else:
            text = f"{subject} ran longer than an hour and was stopped{day}. It tries again at its next time."
        rows.append(_fact("Stopped", text))
    return rows


def _missed_rows(ws, now, away: str = "", cloud: bool = False) -> list:
    """What the sync script saw not start (`<routine> not started`, since when), as it writes it. When another
    computer runs the routines (`away`, its name) or the cloud does (`cloud`), one line from the earliest says so and
    offers to run them here."""
    try:
        data = _yaml(ws / STALE) if ws else {}
    except Unreadable:
        return [_could_not_read("The list of routines that did not start")]
    if away or cloud:
        entries = [since for entry, since in data.items() if str(entry).endswith(" not started")]
        if not entries:
            return []
        since = sorted(s for s in map(stamp, entries) if s)
        day = f" since {_day(_shown(since[0]))}" if since else ""
        where, offer = (("in the cloud", "bring them to this computer") if cloud else
                        (f"on {away}", "run them on this computer"))
        return [_fact("Not started", f"Your routines haven't run {where}{day}; say 'run my routines here' to {offer}.")]
    rows = []
    for entry, since in data.items():
        if str(entry).endswith(" not started"):
            name, plural = _routine_name(routine_key(str(entry)[:-len(" not started")]))
            when = words(since, now, on=True)
            rows.append(_fact("Not started", f"Your {name} {'have' if plural else 'has'} not started"
                              + (f" since {when}" if when else "")
                              + ". This computer's scheduler may not be running it. Say \"Schedule my routines\" to set it up again."))
    return rows


def _scheduler_rows(ws) -> list:
    """The plain line a scheduler record carries when it cannot promise a run (a missed run is not caught up)."""
    try:
        state = _yaml(ws / STATE) if ws else {}
    except Unreadable:
        return [_could_not_read("The record of your scheduled tasks")]
    tasks = state.get("routine_tasks") if isinstance(state.get("routine_tasks"), list) else []
    records = [r for r in [*tasks, state.get("sync_task")] if isinstance(r, dict)]
    lines = list(dict.fromkeys(_text(r.get("waiting")) for r in records if _text(r.get("waiting"))))
    return [_fact("Your computer", line) for line in lines]


def _setting_value(say, default) -> str:
    if isinstance(default, (list, tuple)):
        return "; ".join(_text(x) for x in default if _text(x))
    text = _text(default)
    if isinstance(say, dict):
        return say.get(text, text)
    return say(text) if callable(say) and text else text


def _settings_rows(ws, journal, now) -> list:
    """The interview's defaults in the words of what they decide (never the question that was not asked),
    then the two the history adds: how far back it reads and how it is paced."""
    rows = [_fact("Setup defaults", UNKNOWN)] if journal.get(UNREAD) else []
    settings = journal.get("settings")
    for row in settings if isinstance(settings, list) else []:
        if isinstance(row, dict) and row.get("key"):
            label, say = SETTINGS.get(str(row["key"]), (str(row["key"]).replace("_", " ").capitalize(), None))
            value = _setting_value(say, row.get("default"))
            if value:
                rows.append(_fact(label, value))
    days = plan_or_empty(ws).get("window_days")
    rows.append(_fact("How far back to read", f"the last {days if isinstance(days, int) and not isinstance(days, bool) else 90} days"))
    rows.append(_fact("Pace", PACE))
    return rows


# ----- rendering ----------------------------------------------------------------------------
def _e(text) -> str:
    return html.escape(str(text), quote=True)


def _code(text) -> str:
    """Escape, then show `backticked` commands as code."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", _e(text))


def _title(name: str) -> str:
    name = name.removeprefix("doctor: ").replace("github", "GitHub")
    return name[:1].upper() + name[1:]


def _headline(found: list, live: bool) -> str:
    states = {s["title"]: s["state"] for s in found}
    if live:
        return "Setup is running"
    if "failed" in states.values():
        return "A step did not work"
    if "needs-you" in states.values():
        return "Waiting for you"
    setup = [state for title, state in states.items() if title not in ("Sources", "Briefing")]
    return "You are set up" if all(s == "done" for s in setup) else "Setup has not finished"


ICONS = {"done": '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>',
         "failed": '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/></svg>',
         "needs-you": "!"}


def _step_html(number: int, stage: dict) -> str:
    sub = f'<small>{_e(stage["sub"])}</small>' if stage["sub"] else ""
    return (f'<li class="step state-{stage["state"]}" title="{LABELS[stage["state"]]}">'
            f'<span class="dot">{ICONS.get(stage["state"], number)}</span>'
            f'<span class="name">{_e(stage["title"])}</span>{sub}</li>')


def _joined(titles: list) -> str:
    """`Connect mail`, `Connect calls` as `Connect mail and calls`."""
    if len(titles) == 1:
        return titles[0]
    verb = titles[0].split(" ", 1)[0]
    if all(" " in t and t.split(" ", 1)[0] == verb for t in titles):
        rest = [t.split(" ", 1)[1] for t in titles]
    else:
        verb, rest = "", titles
    return f"{verb} {', '.join(rest[:-1])} and {rest[-1]}".strip()


def _yours_html(rows: list) -> str:
    """The steps only the person can take, each with its command; steps with the same
    instruction share one card. Nothing when none waits."""
    groups = {}
    for r in rows:
        if r["needs_owner"] and not r["done"]:
            groups.setdefault(r["result"], []).append(_title(r["name"]))
    if not groups:
        return ""
    return "<h2>Only you can do this</h2>" + "".join(
        f'<div class="yours"><h3>{_e(_joined(titles))}</h3><p>{_code(said[:1].upper() + said[1:])}</p></div>'
        for said, titles in groups.items())


DIAGRAM = """<figure class="how-card"><svg class="how" viewBox="0 0 900 262" role="img" aria-labelledby="how-title">
<title id="how-title">Your sources feed your workspace, which only you read. A gate sorts each fact: company facts go to the company brain, which everyone reads; a project's facts go to its walled project space, which only its crew reads. Nothing is signed or sent without your yes.</title>
<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="arrow" d="M0 0 L10 5 L0 10 z"/></marker></defs>
<g class="node"><rect x="1" y="80" width="170" height="64" rx="12"/><text class="t" x="86" y="108">Your sources</text><text class="s" x="86" y="128">mail, chat, calls</text></g>
<g class="node you"><rect x="225" y="80" width="190" height="64" rx="12"/><text class="t" x="320" y="108">Your workspace</text><text class="s" x="320" y="128">only you</text></g>
<g class="gate"><path d="M510 78 L544 112 L510 146 L476 112 Z"/><text class="t" x="510" y="174">The gate</text><text class="s" x="510" y="193">sorts each fact</text></g>
<g class="node brain"><rect x="640" y="20" width="258" height="64" rx="32"/><text class="t" x="769" y="48">Company brain</text><text class="s" x="769" y="68">everyone</text></g>
<g class="node"><rect x="640" y="140" width="258" height="64" rx="12"/><text class="t" x="769" y="168">Walled project space</text><text class="s" x="769" y="188">its crew only</text></g>
<path class="edge" marker-end="url(#arrow)" d="M171 112 H223"/>
<path class="edge" marker-end="url(#arrow)" d="M415 112 H474"/>
<path class="edge" marker-end="url(#arrow)" d="M544 112 C592 112 592 52 638 52"/>
<path class="edge" marker-end="url(#arrow)" d="M544 112 C592 112 592 172 638 172"/>
<g class="rule"><circle cx="292" cy="240" r="10"/><path d="M287 240.5l3.5 3.5 6-7"/><text x="462" y="245">Nothing is signed or sent without your yes</text></g>
</svg></figure>"""


def _text_html(text, links=()) -> str:
    """A value as HTML: escaped, line breaks kept and, for an address in `links` (a need's own, https), a link."""
    text, out, at = str(text), [], 0
    for found in URL.finditer(text) if links else ():
        url = found.group(0).rstrip(".,;:!?)")
        if url in links:
            out += [_e(text[at:found.start()]), f'<a href="{_e(url)}" rel="noopener noreferrer">{_e(url)}</a>']
            at = found.start() + len(url)
    out.append(_e(text[at:]))
    return "".join(out).replace("\n", "<br>")


def _need_links(journal) -> set:
    needs = journal.get("needs") if isinstance(journal, dict) and isinstance(journal.get("needs"), list) else []
    return {link for need in needs if isinstance(need, dict) for link in [_https(need.get("link"))] if link}


def _sections_html(found: dict, links=()) -> str:
    out = []
    for key, title in SECTIONS:
        rows = "".join(f'<div class="fact"><dt>{_e(r["label"])}</dt><dd>{_text_html(r["text"], links if key == "waiting" else ())}</dd></div>'
                       for r in found.get(key) or [])
        out.append(f'<section class="facts" id="{key}" aria-labelledby="{key}-title"><h2 id="{key}-title">{_e(title)}</h2>'
                   f'<dl>{rows}</dl></section>')
    return "".join(out)


def _manual_tab(workspace, journal, now) -> tuple:
    """(title, HTML) of the manual once part 1 of the history is read, else ("", "")."""
    parts = parts_of(plan_or_empty(workspace))
    first = next((p for p in parts if p.get("id") == "p1"), parts[0] if parts else None)
    if first is None or first.get("status") != "done":
        return "", ""
    import manual
    try:
        facts = manual.facts(workspace, journal, now)
        return manual.title(facts), manual.render(facts)
    except Exception:   # the rest of the page is still true
        return "Your brain", ('<section class="manual" id="manual"><h2>Your brain</h2><p>The manual could not be built this '
                              'time. It is tried again at the next sync.</p></section>')


def render(steps, spaces=None, live: bool = False, now=None, journal=None, workspace=None, readback: bool = True,
           trouble: str = "", follow: bool = False) -> str:
    """The page. With a setup `journal` and a workspace (given, else the person's own among `spaces`) it also
    holds the six sections and, once part 1 of the history is read, the manual as its first tab. `follow` makes an
    open tab reload every few minutes: the page the sync script keeps rewriting. `readback` False asks the
    scheduler nothing (see `stages`); `trouble` is a line the page says about itself."""
    rows = [_row(s) for s in steps]
    who = context(spaces or {})
    workspace, company = workspace or who["workspace"], who["company"] or "your company brain"
    now = now or dt.datetime.now(dt.timezone.utc)
    full = journal is not None and workspace is not None
    try:
        found = stages(rows, live=live, workspace=workspace, now=now, readback=readback)
    except Exception:   # a record of the setup that is not what it should be: the steps alone, and a line that says so
        trouble = trouble or STAGES_TROUBLE
        found = stages(rows, live=live, workspace=None, now=now, readback=False)
    done = sum(1 for s in found if s["state"] == "done")
    failed = next((s["note"] for s in found if s["state"] == "failed"), "") or trouble
    note = failed or next((s["note"] for s in found if s["note"]), "")
    theme = THEME.read_text(encoding="utf-8") if THEME.exists() else ""
    refresh = f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">' if live else (
        f'<meta http-equiv="refresh" content="{STATIC_REFRESH_SECONDS}">' if follow else "")
    title = company[:1].upper() + company[1:]
    first = who["person"].split()[0] if who["person"].split() else ""
    hello = f'<p class="hello">Welcome, {_e(first)}</p>' if first else ""
    updated = f"Updated {_e(_local(pages.iso(now)))}. " + (
        "This page updates itself while setup runs." if live else
        f"It is rewritten each time your workspace is saved, about every {sync_minutes(journal)} minutes." if full else
        'To refresh it, ask your agent "is this set up?"')
    stage_list = f"""<ol class="steps" aria-label="{done} of {len(found)} steps done">{''.join(_step_html(i, s) for i, s in enumerate(found, 1))}</ol>
{f'<p class="now{" failed" if failed else ""}">{_code(note)}</p>' if note else ''}
{_yours_html(rows)}
<h2>How it works</h2>
{DIAGRAM}"""
    body = stage_list
    if full:
        tab, manual_html = _manual_tab(workspace, journal, now)
        facts_html = _sections_html(sections(workspace, journal, now), _need_links(journal))
        body = (facts_html + stage_list if not manual_html else
                '<div class="tabs" role="radiogroup" aria-label="Pick a tab">'
                '<input type="radio" name="tab" id="tab-manual" class="tab-switch" checked>'
                f'<label for="tab-manual">{_e(tab)}</label>'
                '<input type="radio" name="tab" id="tab-progress" class="tab-switch">'
                '<label for="tab-progress">Progress</label></div>'
                f'<div class="panel panel-manual">{manual_html}</div>'
                f'<div class="panel panel-progress">{facts_html}{stage_list}</div>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light">{refresh}<title>{_e(title)}: onboarding</title>
<style>{theme}</style></head>
<body>
<input type="checkbox" id="light" class="theme-switch" aria-label="Light mode">
<header class="banner"><div class="inner"><span class="wordmark"></span>
<span class="label"><strong>Onboarding</strong><span>{_e(title)}</span></span>
<label for="light" class="theme"></label></div></header>
<main class="inner">
<section class="hero">{hello}<h1>{_e(_headline(found, live))}</h1>
<p class="count">{done} of {len(found)} done</p></section>
{body}
</main>
<footer class="inner"><p>{updated}</p></footer>
</body></html>
"""


def _write_whole(path, text: str) -> Path:
    """The page whole or not at all: written beside itself, then put in place in one step, so a browser that
    reloads never meets half of it and a failed write leaves the page there was."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def write(path, steps, spaces=None, live: bool = False, now=None, journal=None, workspace=None) -> Path:
    return _write_whole(path, render(steps, spaces, live=live, now=now, journal=journal, workspace=workspace))


def _journal() -> dict:
    """The setup journal for the page, read and nothing else (`setup_journal.load()` copies a file it cannot
    use aside every time it is called, and this runs at every sync). No journal, or an empty one, is {}; one that
    cannot be read or is not a mapping is marked, so the page says so instead of claiming nothing was found."""
    import setup_journal
    path = setup_journal.journal_path()
    text = None
    for attempt in range(3):
        try:
            text = path.read_text(encoding="utf-8")
            break
        except FileNotFoundError:
            return {}
        except PermissionError:   # another process is replacing it this instant (Windows): once more
            time.sleep(0.05 * (attempt + 1))
        except (OSError, ValueError):
            break
    if text is None:
        return {UNREAD: True}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return {UNREAD: True}
    if data is None:
        return {}
    return data if isinstance(data, dict) else {UNREAD: True}


def render_workspace(workspace, journal: dict, now=None) -> str:
    """The whole page for a person's workspace: the manual tab first once part 1 of the history is read, then
    the six sections, then the stages of the setup as the onboarding recorded them (the steps it finished;
    what waits on the person is in the sections). It asks nothing of the model, a connector or the scheduler."""
    ws = Path(workspace)
    now = now or dt.datetime.now().astimezone()
    try:
        state = _yaml(ws / STATE)
    except Unreadable:
        state = {}   # the Waiting section says the record could not be read
    recorded = [r for r in state.get("steps") or [] if isinstance(r, dict) and r.get("done") and not r.get("needs_owner")] \
        if isinstance(state.get("steps"), list) else []
    named = state.get("spaces") if isinstance(state.get("spaces"), dict) else {}
    spaces = {**{str(k): str(v) for k, v in named.items() if isinstance(v, (str, Path))}, "_root": str(ws)}
    return render(recorded, spaces, live=False, now=now, journal=journal if isinstance(journal, dict) else {},
                  workspace=ws, readback=False, follow=True)


def _unbuilt_page(now) -> str:
    """The page when the page could not be built: the stages of nothing, and a line that says so."""
    try:
        return render([], None, now=now, trouble=UNBUILT)
    except Exception:
        return f"<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title>Onboarding</title></head><body><p>{UNBUILT}</p></body></html>\n"


def setup_running() -> bool:
    """Whether a setup is running on this machine, by the lock `start.py` takes for the whole of one: held by a live
    process that started when the lock says, or, for a lock without that start time, not so old that the process id
    is another process's now (the rule of `start.take_lock`, which this only reads: nothing is taken, written or
    started). A setup writes this page itself, every step."""
    try:
        import routine
        import start
        path = start.lock_path()
        age = time.time() - path.stat().st_mtime
        held = start._lock_pid(path)
        if held is None:   # no id to ask about: the lock waits out its hour
            return age < start.LOCK_HOURS * 3600
        if not routine.pid_alive(held):
            return False
        lines = path.read_text(encoding="utf-8").splitlines()
        then = lines[1].strip() if len(lines) > 1 else ""
        mark = routine.process_started(held) if then else None
        if then and mark:   # the start that took it, however old the lock; another start time is another process
            return then == mark
        return age < start.PID_REUSE_HOURS * 3600
    except Exception:   # no lock, or no way to read it: no setup to keep the page for
        return False


def refresh(root) -> Path | None:
    """Rewrite the page from a person's workspace, as the sync script does after every run, so what the page
    says is never older than the last one. It reads the setup journal and the workspace's own files and nothing
    else: no model, no connector, no scheduler and no process. What it cannot read becomes a line on the page, and a
    page that cannot be built is a page that says so. Returns the page; None for a space that is not a person's
    workspace, and while a setup is running (it writes the page, every step, and its page is left alone); an
    OSError when the page cannot be written, which the caller says and goes on."""
    root = Path(root)
    try:
        if spaceyaml.load(root).profile != "personal":
            return None
    except Exception:
        return None
    if setup_running():
        return None
    now = dt.datetime.now().astimezone()
    try:
        text = render_workspace(root, _journal(), now)
    except Exception:
        text = _unbuilt_page(now)
    return _write_whole(page_path(), text)


class Live:
    """The progress hook: `onboard.Report(progress=Live(path))` rewrites the page after every
    step of a run, and `finish(report)` writes the final page. A page that cannot be written
    never stops a setup: it returns None instead."""

    def __init__(self, path=None):
        self.path, self.steps, self.spaces = Path(path or page_path()), [], {}

    def __call__(self, report, step) -> None:
        self.steps.append(step)
        self.spaces.update(report.spaces)
        self._write(live=True)

    def finish(self, report) -> Path | None:
        self.steps, self.spaces = list(report.steps), {**self.spaces, **report.spaces}
        return self._write(live=False, journal=_journal())   # the whole page, once, at the end

    def _write(self, live: bool, journal=None) -> Path | None:
        try:
            return write(self.path, self.steps, self.spaces, live=live, journal=journal)
        except Exception:
            return None


def after_check(root, steps, path=None) -> Path | None:
    """The page after `onboard --check`: what the onboarding recorded as done, then the checks
    just run, the owner's steps among them as read back now."""
    root = Path(root).resolve()
    try:
        state = yaml.safe_load((root / STATE).read_text(encoding="utf-8")) if (root / STATE).exists() else {}
        recorded = [r for r in (state or {}).get("steps") or [] if r.get("done") and not r.get("needs_owner")]
        spaces = {**((state or {}).get("spaces") or {}), "_root": str(root)}
        return write(path or page_path(), recorded + list(steps), spaces, journal=_journal())
    except Exception:
        return None
