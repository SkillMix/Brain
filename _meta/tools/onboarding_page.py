"""The onboarding page: one self-contained HTML file that shows a newcomer, in as few words as
it can, where their setup stands, what only they can do, and how the system works, the last as
a diagram.

It is rendered from the onboarding report, the rows `start.py` and `onboard.py --check` print,
each with its check; nothing on it is written by hand, so the progress it shows is the
report's. `start.py` rewrites it after every step and once at the end, `onboard.py --check`
rewrites it from the recorded onboarding and the read-backs, and the fixture writes one beside
the spaces it builds. The rows themselves stay in the agent's terminal: the page shows only the
stages, the one thing the person must do, and the first briefing's time.

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
import re
import sys
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


def stages(rows: list, live: bool = False, workspace=None, now=None) -> list:
    """Each stage with its rows and its state. A stage with no rows is next, unless a later
    stage has rows: then it did not apply to this setup and is left out. While a run is live,
    the stage of its latest row is the one running."""
    groups = [[] for _ in STAGES]
    for row in rows:
        groups[stage_of(row["name"])].append(row)
    for i, fact in ((4, _routines_fact(workspace)), (5, _sources_fact(workspace))):
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
        out.append({"title": title, "state": state, "rows": group, "sub": "", "note": _note(state, group)})
    ready = all(s["state"] == "done" for s in out if s["title"] != "Sources")
    out.append(_briefing(workspace, now, ready))
    return out


def _note(state: str, rows: list) -> str:
    """The one line under the stages: the step running now, or the one that did not work."""
    if state == "running":
        return f"Now: {rows[-1]['name']}"
    if state == "failed":
        first = next(r for r in rows if not r["done"] and not r["needs_owner"])
        return f"{first['name']}: {first['result']}"[:220]
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


def _routines_fact(workspace):
    """Routines are done once their schedule reads back (`onboard.routine_schedule`: an OS task,
    or a persistent harness task with its recorded read-back); rendered prompts schedule nothing.
    None while nothing is scheduled: the stage is next."""
    if not _rendered(workspace):
        return None
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


def render(steps, spaces=None, live: bool = False, now=None) -> str:
    rows = [_row(s) for s in steps]
    who = context(spaces or {})
    workspace, company = who["workspace"], who["company"] or "your company brain"
    now = now or dt.datetime.now(dt.timezone.utc)
    found = stages(rows, live=live, workspace=workspace, now=now)
    done = sum(1 for s in found if s["state"] == "done")
    failed = next((s["note"] for s in found if s["state"] == "failed"), "")
    note = failed or next((s["note"] for s in found if s["note"]), "")
    theme = THEME.read_text(encoding="utf-8") if THEME.exists() else ""
    refresh = f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">' if live else ""
    title = company[:1].upper() + company[1:]
    first = who["person"].split()[0] if who["person"].split() else ""
    hello = f'<p class="hello">Welcome, {_e(first)}</p>' if first else ""
    updated = f"Updated {_e(_local(pages.iso(now)))}. " + (
        "This page updates itself while setup runs." if live else 'To refresh it, ask your agent "is this set up?"')
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
<ol class="steps" aria-label="{done} of {len(found)} steps done">{''.join(_step_html(i, s) for i, s in enumerate(found, 1))}</ol>
{f'<p class="now{" failed" if failed else ""}">{_code(note)}</p>' if note else ''}
{_yours_html(rows)}
<h2>How it works</h2>
{DIAGRAM}
</main>
<footer class="inner"><p>{updated}</p></footer>
</body></html>
"""


def write(path, steps, spaces=None, live: bool = False, now=None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(steps, spaces, live=live, now=now), encoding="utf-8", newline="\n")
    return path


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
        return self._write(live=False)

    def _write(self, live: bool) -> Path | None:
        try:
            return write(self.path, self.steps, self.spaces, live=live)
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
        return write(path or page_path(), recorded + list(steps), spaces)
    except Exception:
        return None
