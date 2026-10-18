"""The onboarding page: one self-contained HTML file that shows a newcomer what is done, what
runs now, what is next and what only they can do, and how the system works.

It is rendered from the onboarding report, the rows `start.py` and `onboard.py --check` print,
each with its check; nothing on it is written by hand, so the progress it shows is the
report's. `start.py` rewrites it after every step and once at the end, `onboard.py --check`
rewrites it from the recorded onboarding and the read-backs, and the fixture writes one beside
the spaces it builds. After onboarding it also shows the day: the last sweep, the Needs-you
count and what leaves within the window.

It lives at `~/.spaces/onboarding.html`, outside every repository, beside the machine's list
of spaces: it names local paths and the person's sign-ins, it exists before any space does
(a doctor that stops), and a file that changes on every run would open a sync pull request
each time. No server, no scripts, no external fonts or images; light and dark. The house
style, and the brand, are in `_meta/templates/onboarding.css`, the one brand file.
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
# step no stage names belongs to "Connect them".
STAGES = (
    ("Check this machine", ("doctor:", "one start at a time"),
     "git, gh, Python and your GitHub sign-in"),
    ("Create your spaces", ("invitation", "namespace", "dry run", "create ", "fill ", "remove ", "registry"),
     "your workspace, made from this template, beside the company brain"),
    ("Connect them", (),
     "the brain and your projects mounted, the sync task, the instruction every session reads"),
    ("First run", ("AGENTS.md", "ids ", "first sweep", "status ", "validate", "answers ", "space.yaml validates"),
     "the first sweep, the status pass and the tests"),
    ("Your routines", ("routine",),
     "three sweeps and one daily chain a day, which your agent schedules"),
    ("Your sources", ("connect ",),
     "say \"connect my mail\" (or calendar, chat, calls) in a session"),
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
    for i, (_, prefixes, _) in enumerate(STAGES):
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
    out = []
    for i, (title, _, hint) in enumerate(STAGES):
        group = groups[i]
        if not group and any(groups[i + 1:]):
            continue
        state = _state(group, i == latest) if group else "next"
        out.append({"title": title, "state": state, "rows": group, "summary": _summary(state, group, hint)})
    out.append(_briefing(workspace, now))
    return out


def _summary(state: str, rows: list, hint: str) -> str:
    if state == "next":
        return hint
    if state == "running":
        return f"now: {rows[-1]['name']}"
    if state == "failed":
        first = next(r for r in rows if not r["done"] and not r["needs_owner"])
        return f"{first['name']}: {first['result']}"[:220]
    waiting = sum(1 for r in rows if not r["done"])
    if waiting:
        return f"{waiting} step{'s' if waiting != 1 else ''} only you can take: see below"
    return f"{len(rows)} steps done" if len(rows) > 1 else rows[0]["result"][:160] or "done"


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


def _routines_fact(workspace):
    path = Path(workspace) / ROUTINES / "daily-chain.md" if workspace else None
    if path is None or not path.exists():
        return None
    return {"name": "routines", "done": True, "check": f"test -f {path.as_posix()}",
            "result": "rendered: your agent schedules them", "needs_owner": False}


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


def _times(schedule: str) -> str:
    fields = str(schedule).split()
    if len(fields) != 5 or fields[2:] != ["*", "*", "*"]:
        return schedule
    try:
        pairs = sorted((int(h), int(m)) for h in fields[1].split(",") for m in fields[0].split(","))
    except ValueError:
        return schedule
    said = [f"{h:02d}:{m:02d}" for h, m in pairs]
    return said[0] if len(said) == 1 else ", ".join(said[:-1]) + " and " + said[-1]


def _briefing(workspace, now) -> dict:
    import morning_view
    sent = [r for r in _run_lines(workspace) if r.get("routine") in morning_view.SEND_ROUTINES
            and isinstance(r.get("notes"), dict) and (r["notes"].get("email") or r["notes"].get("message_id"))]
    if sent:
        return {"title": "First morning briefing", "state": "done", "rows": [],
                "summary": f"sent {_local(sent[0].get('at'))}"}
    daily = next((s for n, s in _routines(workspace) if n == "daily-chain"), "")
    when = f"tomorrow at {_times(daily)}, this machine's time" if daily else "the morning after setup"
    return {"title": "First morning briefing", "state": "next", "rows": [],
            "summary": f"{when}: one mail from your own mailbox to you"}


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


# ----- the daily state, after onboarding --------------------------------------------------
def today(workspace) -> dict | None:
    """The last sweep, the Needs-you count and what leaves within the window; None before the
    first sweep, or when the workspace cannot be read."""
    sweeps = [r for r in _run_lines(workspace) if r.get("routine") == "sweep"]
    if not sweeps:
        return None
    try:
        import needs_you
        items = needs_you.collect(workspace, online=False)
    except Exception:
        return None
    leaving = [i for i in items if i.kind == "leaving"]
    return {"last_sweep": _local(sweeps[-1].get("at")), "needs_you": len(items),
            "leaving": [i.text for i in leaving]}


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
    setup = [state for title, state in states.items() if title not in ("Your sources", "First morning briefing")]
    return "You are set up" if all(s == "done" for s in setup) else "Setup has not finished"


def _stage_html(stage: dict) -> str:
    rows = "".join(
        f'<tr class="{"done" if r["done"] else "not-done"}"><td class="mark">'
        f'{"ok" if r["done"] else ("you" if r["needs_owner"] else "fail")}</td>'
        f'<td>{_e(r["name"])}</td><td>{_code(r["result"])}</td></tr>' for r in stage["rows"])
    details = (f'<details><summary>{len(stage["rows"])} step{"s" if len(stage["rows"]) != 1 else ""}'
               f'</summary><table>{rows}</table></details>') if stage["rows"] else ""
    return (f'<li class="state-{stage["state"]}"><span class="stage-title">{_e(stage["title"])}</span>'
            f'<span class="badge">{LABELS[stage["state"]]}</span>'
            f'<div class="stage-summary">{_code(stage["summary"])}{details}</div></li>')


def _yours_html(rows: list, workspace) -> str:
    waiting = [r for r in rows if r["needs_owner"] and not r["done"]]
    cards = "".join(
        f'<div class="yours"><h3>{_e(_title(r["name"]))}</h3><p>{_code(r["result"])}</p>'
        f'<p class="proof">The check that proves it: <code>{_e(r["check"])}</code></p></div>' for r in waiting)
    if not waiting:
        cards = '<p class="quiet">Nothing waits for you right now.</p>'
    if workspace:
        ws = Path(workspace).as_posix()
        cards += ("<p>The defaults you may want to change wait on your Needs-you list: ask your agent "
                  f"\"what needs me\", or run <code>python {_e(ws)}/_meta/tools/needs_you.py {_e(ws)}</code>.</p>")
    return cards


def _tomorrow_html(workspace) -> str:
    said = {"daily-chain": "the daily chain rebuilds your status pages and your Needs-you list, and your "
                           "morning briefing arrives: one mail from your own mailbox to you, counts only, "
                           "the items stay in your workspace.",
            "sweep-chain": "the sweep reads your sources, keeps each record with its source and decides "
                           "what may travel."}
    items = [f"<li>At {_e(_times(schedule))}, {said.get(name, _e(name) + ' runs.')}</li>"
             for name, schedule in _routines(workspace)]
    return (f"<ul>{''.join(items)}</ul><p>Times are this machine's own. Nothing is signed or sent "
            "without you: drafts wait on your Needs-you list, and mail waits in your outbox.</p>")


def _today_html(day: dict) -> str:
    leaving = "".join(f"<li>{_e(t)}</li>" for t in day["leaving"][:5])
    return (f'<dl class="today"><dt>Last sweep</dt><dd>{_e(day["last_sweep"])}</dd>'
            f'<dt>Needs you</dt><dd>{day["needs_you"]}</dd>'
            f'<dt>Leaves within the window</dt><dd>{len(day["leaving"])}</dd></dl>'
            + (f"<ul>{leaving}</ul>" if leaving else ""))


DIAGRAM = """<figure><svg class="how" viewBox="0 0 920 300" role="img" aria-labelledby="how-title">
<title id="how-title">Your sources feed your workspace; facts travel to the company brain and to walled project spaces; nothing is signed or sent without a person's yes.</title>
<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="arrow" d="M0 0 L10 5 L0 10 z"/></marker></defs>
<g class="node"><rect x="330" y="10" width="200" height="60" rx="4"/><text class="t" x="430" y="36" text-anchor="middle">A colleague's workspace</text><text class="s" x="430" y="55" text-anchor="middle">only they read it</text></g>
<g class="node"><rect x="10" y="120" width="190" height="60" rx="4"/><text class="t" x="105" y="146" text-anchor="middle">Your mail, chat, calls,</text><text class="s" x="105" y="165" text-anchor="middle">calendar and documents</text></g>
<g class="node"><rect x="330" y="120" width="200" height="60" rx="4"/><text class="t" x="430" y="146" text-anchor="middle">Your workspace</text><text class="s" x="430" y="165" text-anchor="middle">only you read it</text></g>
<g class="node brain"><rect x="710" y="10" width="200" height="60" rx="14"/><text class="t" x="810" y="36" text-anchor="middle">Company brain</text><text class="s" x="810" y="55" text-anchor="middle">everyone reads it</text></g>
<g class="node"><rect x="710" y="120" width="200" height="60" rx="4"/><text class="t" x="810" y="146" text-anchor="middle">Walled project space</text><text class="s" x="810" y="165" text-anchor="middle">only its crew reads it</text></g>
<g class="node"><rect x="710" y="230" width="200" height="60" rx="4"/><text class="t" x="810" y="256" text-anchor="middle">Signed pages,</text><text class="s" x="810" y="275" text-anchor="middle">sent mail</text></g>
<path class="edge" marker-end="url(#arrow)" d="M200 150 H328"/><text class="lbl" x="265" y="128" text-anchor="middle">records in,</text><text class="lbl" x="265" y="142" text-anchor="middle">each with its source</text>
<path class="edge" marker-end="url(#arrow)" d="M530 40 H708"/><text class="lbl" x="619" y="31" text-anchor="middle">facts released</text>
<path class="edge" marker-end="url(#arrow)" d="M530 132 L708 62"/><text class="lbl" x="598" y="82" text-anchor="middle">facts released</text>
<path class="edge" marker-end="url(#arrow)" d="M530 150 H708"/><text class="lbl" x="619" y="142" text-anchor="middle">project facts</text>
<path class="edge" marker-end="url(#arrow)" d="M530 168 L708 248"/><text class="lbl" x="585" y="232" text-anchor="middle">nothing without</text><text class="lbl" x="585" y="246" text-anchor="middle">a person's yes</text>
</svg></figure>"""

POINTS = (
    ("Your workspace", "a private git repository only you read. The sweep copies your mail, chat, calls, "
     "calendar and documents into it, each record with its source. Personal and school records are "
     "counted, never stored."),
    ("The company brain", "the repository everyone reads. It is mounted read-only in every workspace and "
     "project, so every agent starts from what the company already knows."),
    ("Walled project spaces", "what only a project's crew may read lives in a space of its own, mounted "
     "for that crew alone."),
    ("The classification gate", "nothing about a client, project or deal leaves the space it came into "
     "until its owner's agent classifies it: company (to the brain), walled (to its crew's space) or "
     "owner (it stays home). A second agent checks every decision that sends anything out."),
    ("Nothing signed or sent without a person", "pages the agent drafts wait on your Needs-you list for "
     "a yes, and mail waits in your outbox until you send it."),
)


def render(steps, spaces=None, live: bool = False, now=None) -> str:
    rows = [_row(s) for s in steps]
    who = context(spaces or {})
    workspace, company = who["workspace"], who["company"] or "your company brain"
    now = now or dt.datetime.now(dt.timezone.utc)
    found = stages(rows, live=live, workspace=workspace, now=now)
    done = sum(1 for s in found if s["state"] == "done")
    day = None if live or workspace is None else today(workspace)
    theme = THEME.read_text(encoding="utf-8") if THEME.exists() else ""
    lede = (f"{_e(who['person'])}'s workspace: <code>{_e(Path(workspace).as_posix())}</code>. " if workspace
            else "Your workspace is made once this machine is ready. ")
    lede += f"Updated {_e(_local(pages.iso(now)))}."
    if live:
        lede += " This page reloads itself every two seconds while setup runs."
    refresh = f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">' if live else ""
    check = (f"<code>python {_e(Path(workspace).as_posix())}/_meta/tools/onboard.py "
             f"{_e(Path(workspace).as_posix())} --check</code>") if workspace else "the sentence again"
    title = company[:1].upper() + company[1:]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">{refresh}<title>{_e(title)}: onboarding</title>
<style>{theme}</style></head>
<body>
<header class="banner"><div class="inner"><span class="wordmark"></span>
<span class="label"><strong>Onboarding</strong><span>{_e(title)}</span></span></div></header>
<main class="inner">
<p class="eyebrow">{_e(title) + " | " if who["company"] else ""}company brain</p>
<h1>{_e(_headline(found, live))}</h1>
<p class="lede">{lede}</p>
<div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="{len(found)}" aria-valuenow="{done}"><span style="width:{round(100 * done / len(found))}%"></span></div>
<p class="count">{done} of {len(found)} stages done</p>
<ol class="stages">{''.join(_stage_html(s) for s in found)}</ol>
<h2>Only you can do this</h2>
{_yours_html(rows, workspace)}
{f'<h2>Today</h2>{_today_html(day)}' if day else ''}
<h2>What happens tomorrow</h2>
{_tomorrow_html(workspace)}
<h2>How it works</h2>
{DIAGRAM}
<ol class="points">{''.join(f'<li><strong>{_e(t)}:</strong> {_e(b)}</li>' for t, b in POINTS)}</ol>
</main>
<footer class="inner"><p>Rendered from the onboarding report by <code>_meta/tools/onboarding_page.py</code>.
To bring it up to date, ask your agent "is this set up?", or run {check}.</p></footer>
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
