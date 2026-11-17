"""The chain: routines run by dependency, each reading the files the previous one wrote.

Three times a day the sweep chain: `transcripts.scan` (a personal space with a transcripts
folder) → `sweep.run` → `distill.run` → `check.run`. Once a day
the daily chain: `status.run` (with landing at the home) → `onboard.keys_upkeep` (a live space
on GitHub that mounts one, or the brain) → `release.ship` (only items already listed) → `outbox_reconcile.run` (where an outbox exists) → `morning_view.run` →
the answer and leak tests when their files exist. A step that fails
is recorded as `{"step", "error"}` and the next step runs on yesterday's output; a tool
that is not present yet is skipped with a note. Every chain run appends one run line to
`_meta/state/runs.jsonl`.

A cloud run (`--cloud`) leaves to the computer's sync what needs the computer, the person's click
or one writer: the sweep chain skips the transcripts scan and settles the outbox after the reply
step, where the mail feed is read; the daily chain skips template sync, the two request steps, the
release leg and the outbox, and asks the link check without GraphQL (only the sweep runs in the cloud now, so the
daily's `--cloud` is a run no task makes). The computer's sync then scans the transcripts at most every
TRANSCRIPTS_HOURS, and the daily kept on the computer runs template sync when
`_template_sync_due` says so (`sync.Run.computer_leg`).

`chain.py gate <workspace>` is the first step of every task prompt a computer runs: it prints
nothing and exits 0 when this computer may run the chains now, else one sentence and exit 3
(the routines run in the cloud, on another computer, are paused, or the record is unclear).

The virtual clock walks `SPACE_NOW` forward over a number of scaled days (`time_scale`
from the first root's space.yaml), runs the sweep chain for every root at every step and
the daily chain once per day. `SPACE_NOW` is restored afterwards. It runs only over spaces
in `mode: fixture` (an invented company, `onboard --fixture`): on a real space it would move
the clock past release windows. In process, onboarding's fixture can also apply that company's
verdicts after a given day; the command line cannot, so no command a routine may run signs a
page in a person's name. Inside a routine or a cloud run (`routine.in_routine`) the command line
refuses the virtual clock, and the function refuses to apply verdicts.

    python chain.py sweep [root] [--cloud]
    python chain.py daily [root] [--person <name>] [--shadow] [--offline name=path ...] [--cloud]
    python chain.py gate [workspace]
    python chain.py checkout <root> <mount>    this machine's own checkout of a space <root> mounts
    python chain.py virtual --days N --roots a b c [--steps 3] [--persons a=Ada ...]
                            [--offline name=path ...]          (fixture spaces only)
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import importlib
import json
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

RUNS_LOG = Path("_meta") / "state" / "runs.jsonl"
ANSWERS_PATH = Path("_meta") / "tests" / "answers.yaml"
LEAKS_PATH = Path("_meta") / "tests" / "leaks.yaml"
# While the routines run in the cloud, the computer's sync scans the transcripts, the sweep chain's first
# step, at most this often: the session files are on the computer, and the cloud sweep never reads them.
TRANSCRIPTS_HOURS = 6


# ----- steps -------------------------------------------------------------------------

def _module(name: str):
    """The tool module, or None when it is not present yet (tests replace this)."""
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def _plain(value):
    """A JSON-safe copy of a step's report (dataclass, object, tuple or plain data)."""
    def default(obj):
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return dataclasses.asdict(obj)
        if hasattr(obj, "__dict__"):
            return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
        return str(obj)
    try:
        return json.loads(json.dumps(value, default=default))
    except (TypeError, ValueError):
        return str(value)


def _step(ctx: dict, step: str, module_name: str, fn_name: str, *args, **kwargs):
    """Run one step, record its report or its error; the chain goes on either way."""
    module = _module(module_name)
    fn = getattr(module, fn_name, None) if module is not None else None
    if fn is None:
        ctx["skipped"].append(f"{step}: tool not present ({module_name}.{fn_name})")
        ctx["steps"][step] = None
        return None
    try:
        result = fn(*args, **kwargs)
    except Exception as exc:  # the chain continues on yesterday's output
        ctx["errors"].append({"step": step, "error": str(exc)})
        ctx["steps"][step] = None
        _stale_step(ctx, step, failed=True)
        return None
    ctx["steps"][step] = _plain(result)
    _stale_step(ctx, step, failed=False)
    return result


def _stale_step(ctx: dict, step: str, failed: bool) -> None:
    """`stale.yaml` moves as each step ends, not when the run ends, so a later step in the
    same run (the morning view) already sees a failure or a recovery."""
    path = Path(ctx["root"]) / STALE
    try:
        data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    except (OSError, yaml.YAMLError):
        return   # the run's own bookkeeping at the end still has its say; the steps go on
    if not isinstance(data, dict):
        return
    if failed:
        data.setdefault(step, ctx["at"])
    elif data.pop(step, None) is None:
        return
    pages.write_text(path, yaml.safe_dump(data, sort_keys=True), root=ctx["root"])


def _context(routine: str, root: Path, now: dt.datetime) -> dict:
    return {"routine": routine, "root": str(root), "at": pages.iso(now), "steps": {},
            "errors": [], "skipped": []}


STALE = Path("_meta") / "state" / "stale.yaml"


def _mark_stale(space: spaceyaml.Space, ctx: dict, now: dt.datetime) -> None:
    """A failed step leaves yesterday's output, labelled: `stale.yaml` names each failed step
    and since when; the health line and the briefing show it until the step runs again."""
    path = space.root / STALE
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    failed = {e["step"] for e in ctx["errors"]}
    for step, result in ctx["steps"].items():
        if step in failed:
            data.setdefault(step, pages.iso(now))
        elif result is not None:
            data.pop(step, None)
    if data or path.exists():
        pages.write_text(path, yaml.safe_dump(data, sort_keys=True), root=space.root)


def _template_sync_due(space: spaceyaml.Space, now: dt.datetime) -> bool:
    """Once a scaled week, where a provenance file names the template or onboarding made the
    space from it (it stamps no provenance; the first sync does); never in the fixture. The daily
    chain asks it, and so does the computer's sync while the routines run in the cloud
    (`sync.Run.computer_leg`)."""
    state = space.root / "_meta" / "state"
    if space.mode == "fixture" or not ((state / "template-version.yaml").exists() or (state / "onboard.yaml").exists()):
        return False
    path = space.root / RUNS_LOG
    last = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []:
        try:
            row = json.loads(line)
            # only the weekly sync guarded by the tests counts; a hand sync never resets the clock
            if row.get("routine") == "template-sync" and (row.get("notes") or {}).get("guarded"):
                at = pages.parse_iso(row.get("at"))
                last = at if last is None or at > last else last
        except Exception:
            continue
    if last is None or now - last >= space.scale(7 * 24):
        return True
    # the owner's yes to a template release moves their spaces the same day, not next week
    try:
        import template_sync
        return any(pages.parse_iso(yes["at"]) > last for yes in template_sync.approvals(space))
    except Exception:
        return False


def _run_line(space: spaceyaml.Space, ctx: dict, now: dt.datetime) -> None:
    _mark_stale(space, ctx, now)
    notes = []
    if ctx["errors"]:
        notes.append("errors: " + "; ".join(f"{e['step']}: {e['error']}" for e in ctx["errors"]))
    if ctx["skipped"]:
        notes.append("skipped: " + "; ".join(ctx["skipped"]))
    line = {"routine": ctx["routine"], "at": pages.iso(now), "space": space.name,
            "cost_tokens": 0, "notes": " | ".join(notes) or "ok"}
    pages.append_text(space.root / RUNS_LOG, json.dumps(line, ensure_ascii=False) + "\n", root=space.root)


NOT_STARTED = "{} not started"   # the sync script's entry for a chain its scheduler never started


def _started(space: spaceyaml.Space, routine: str) -> None:
    """The chain runs, so the sync script's `<routine> not started` entry in stale.yaml goes."""
    path = space.root / STALE
    try:
        data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    except (OSError, yaml.YAMLError):
        return   # the chain runs on; the run's own bookkeeping at the end still has its say
    if isinstance(data, dict) and data.pop(NOT_STARTED.format(routine), None) is not None:
        pages.write_text(path, yaml.safe_dump(data, sort_keys=True), root=space.root)


# ----- the two chains ----------------------------------------------------------------

def sweep_chain(root, now: dt.datetime | None = None, cloud: bool = False) -> dict:
    """(journal records →) sweep → distill → check, each on the files the previous step wrote.

    In a cloud run (`cloud`) the transcripts scan is the computer's sync's, since the session files
    are on the computer, and the outbox is settled right after the reply step, since the cloud
    exports the mail feed it reads in the sweep and nowhere else."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    ctx = _context("sweep_chain", root, now)
    _started(space, "sweep_chain")
    if not cloud and space.profile == "personal" and (space.data.get("transcripts") or {}).get("path"):
        _step(ctx, "transcripts", "transcripts", "scan", root, now=now)
    if space.profile == "personal":   # notes with a pointer to linked code become records to sweep
        _step(ctx, "journal_records", "link", "journal_records", root)
    _step(ctx, "sweep", "sweep", "run", root, now=now)
    if space.profile == "personal":
        _step(ctx, "reply", "reply", "run", root, now=now)
    if cloud and (root / "outbox").is_dir():
        _step(ctx, "outbox_reconcile", "outbox_reconcile", "run", root, now=now)
    _step(ctx, "distill", "distill", "run", root, now=now)
    _step(ctx, "check", "check", "run", root, now=now)
    if space.profile == "personal":   # evidence moves the issues this person's tracker made
        _step(ctx, "tracker_observe", "tracker", "observe", root, now=now)
    _run_line(space, ctx, now)
    return ctx


def daily(root, person: str | None = None, now: dt.datetime | None = None, shadow: bool = False,
          offline_roots: dict | None = None, cloud: bool = False) -> dict:
    """status → access → mount keys → crew logins → link check → requests → release (listed items only) → outbox
    reconcile → morning view → answer and leak tests.

    In a cloud run (`cloud`) the computer's sync keeps four steps. Template sync stays there
    because the machinery it changes needs the person's click, and a cloud run never lands a
    protected change. The two request steps and the release leg stay there so that two writers
    never answer or ship the same item. The outbox is the cloud sweep's, and the link check runs
    without GraphQL, which a cloud run refuses."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    person = person or space.owner
    ctx = _context("daily", root, now)
    _started(space, "daily")
    if not cloud and _template_sync_due(space, now):
        _step(ctx, "template_sync", "template_sync", "guarded", root)
    _step(ctx, "status", "status", "run", root, now=now)
    _step(ctx, "access", "access", "run", root, now=now)   # readers on the host against the audience
    if _mount_keys_due(space, root, cloud):   # the checks' read-only mount keys, made, attached and removed
        _step(ctx, "mount_keys", "onboard", "keys_upkeep", root)
    if not cloud and space.profile == "project" and str(person).casefold() == str(space.owner).casefold():
        _step(ctx, "crew_logins", "mint", "logins_upkeep", root)   # a later crew login, for the owner's click
    _step(ctx, "link_check", "link", "check", root, now=now,   # the codebases linked to subjects homed here
          graphql=not cloud)
    if not cloud:
        _step(ctx, "requests_collect", "request", "collect", root, now=now, offline_roots=offline_roots)
        _step(ctx, "requests_answer", "request", "answer", root, now=now, offline_roots=offline_roots)
        _step(ctx, "release", "release", "ship", root, now=now, offline_roots=offline_roots)
        if (root / "outbox").is_dir():
            _step(ctx, "outbox_reconcile", "outbox_reconcile", "run", root, now=now)
    _step(ctx, "morning_view", "morning_view", "run", root, person, now=now, shadow=shadow)
    if (root / ANSWERS_PATH).exists():
        _step(ctx, "answers", "tests_run", "answers", root, now=now, roots=offline_roots)
    if (root / LEAKS_PATH).exists():
        _step(ctx, "leaks", "tests_run", "leaks", root, roots=offline_roots, now=now)
    _run_line(space, ctx, now)
    return ctx


def _mount_keys_due(space: spaceyaml.Space, root: Path, cloud: bool) -> bool:
    """Whether the daily keeps this space's mount keys (`onboard.keys_upkeep`): on the computer, for a live space
    on GitHub that mounts a space or is the brain (which attaches its colleagues' keys). A fixture, a copy with
    no repository on GitHub and a cloud run never ask the host for keys."""
    if cloud or space.mode != "live" or not ((root / ".gitmodules").exists() or space.profile == "brain"):
        return False
    onboard = _module("onboard")
    try:
        return bool(onboard is not None and onboard.space_repo(root))
    except Exception:
        return False


# ----- the gate ----------------------------------------------------------------------

IN_THE_CLOUD = "your routines run in the cloud"
GATED = 3   # the exit of `chain.py gate` when this computer must not run the chains now


def gate(workspace, now: dt.datetime | None = None, chain: str | None = None) -> str | None:
    """Why this computer must not run the chains now; None to go on. The first step of every
    task prompt a computer runs (`chain.py gate <workspace>`), since the app's own scheduled tasks
    never read the routine host record: the routines run in the cloud, paused or not
    (IN_THE_CLOUD; the daily `chain` goes on on the computer the move ran on), another computer is the host ("they run on <name>"), the record is not one
    (`routine.UNCLEAR`), or they are paused ("paused until <time>", in this computer's time, or in
    the record's own for a time this computer cannot put in its own).
    This computer as the host goes on through either of its runners, and so does a workspace with
    no record at all: a space that never claimed runs as it always has."""
    import routine
    record = routine.host(workspace)
    if record is None:
        return None
    if record.get("unclear"):
        return routine.UNCLEAR
    if record["runner"] == "cloud":
        if chain == "sweep" or record.get("computer") not in (None, routine.this_machine()):   # none named: each
            return IN_THE_CLOUD
    elif record["machine"] != routine.this_machine():
        return f"they run on {record['name'] or 'another computer'}"
    until = routine.paused_until(record, now or dt.datetime.now(dt.timezone.utc))
    if until is not None:
        try:
            until = until.astimezone()
        except (OSError, OverflowError, ValueError):   # Windows has no local time past the year 3000: the record's own
            pass
        return f"paused until {until:%Y-%m-%d %H:%M}"
    return None


# ----- the virtual clock -------------------------------------------------------------

def _key(path) -> str:
    return str(Path(path).resolve())


def _name(key) -> str:
    """A root's key: a path resolves to its absolute form, a slug stays a slug."""
    key = str(key)
    return _key(key) if ("/" in key or "\\" in key or Path(key).exists()) else key


def _load(root: Path) -> spaceyaml.Space | None:
    try:
        return spaceyaml.load(root)
    except (FileNotFoundError, ValueError):
        return None


def _owner(root: Path) -> str:
    space = _load(root)
    return space.owner if space else ""


def _pick(table: dict, root: Path):
    """A per-root value keyed by path, directory name or space name (slug), whichever is given."""
    space = _load(root)
    for key in (_key(root), root.name, space.name if space else None):
        if key is not None and key in table:
            return table[key]
    return None


def _set_now(moment: dt.datetime) -> None:
    os.environ["SPACE_NOW"] = pages.iso(moment)


def virtual(roots: list, days: int, steps_per_day: int = 3, persons: dict | None = None,
            offline_roots: dict | None = None, verdicts_after_day: int | None = None,
            verdicts: dict | None = None, by: dict | None = None, inject=None) -> dict:
    """Walk SPACE_NOW over `days` scaled days, running both chains for every root.

    Every root must be a fixture space (`mode: fixture`), else ValueError and nothing runs: the
    walk moves a space's clock days ahead, and `verdicts` sign pages in a person's name with no
    read-back, which only an invented company may have done to it. Inside a routine or a cloud
    run (`routine.in_routine`) a walk that would apply `verdicts` is refused too: they are a
    person's answers, even an invented one's.

    `inject(day, at)`, when given, is called at the start of each scaled day (day 1-based)
    so a test can let late records arrive after earlier days were signed."""
    roots = [Path(r) for r in roots]
    if not roots:
        raise ValueError("virtual needs at least one root")
    if days < 1 or steps_per_day < 1:
        raise ValueError("days and steps_per_day must be 1 or more")
    if verdicts:
        _person_only("a walk that applies verdicts")
    for root in roots:
        space = _load(root)
        if space is None or space.mode != "fixture":
            raise ValueError(f"chain.virtual runs only over fixture spaces; {pages._shown(str(root))} is "
                             f"{'no space' if space is None else 'in mode ' + repr(space.mode)}")
    first = spaceyaml.load(roots[0])
    scaled_day = first.scale(24)
    step_length = scaled_day / steps_per_day
    start = first.now()
    persons = {str(k): str(v) for k, v in (persons or {}).items()}
    verdicts = {_name(k): v for k, v in (verdicts or {}).items()}
    signers = {_name(k): v for k, v in (by or {}).items()}
    saved = os.environ.get("SPACE_NOW")
    result = {"start": pages.iso(start), "days": days, "steps_per_day": steps_per_day,
              "scaled_day_seconds": scaled_day.total_seconds(), "sweep_chains": 0, "dailies": 0,
              "errors": [], "steps": [], "verdicts_applied": []}
    at = start

    def note(kind: str, root: Path, day: int, step, report: dict) -> None:
        errors = list((report or {}).get("errors") or [])
        result["steps"].append({"day": day, "step": step, "kind": kind, "root": str(root),
                                "at": pages.iso(at), "errors": errors,
                                "skipped": list((report or {}).get("skipped") or [])})
        result["errors"].extend({"root": str(root), "at": pages.iso(at), **e} for e in errors)

    try:
        for day in range(days):
            if inject is not None:
                at = start + day * scaled_day
                _set_now(at)
                inject(day + 1, at)
            for step in range(steps_per_day):
                at = start + day * scaled_day + step * step_length
                _set_now(at)
                for root in roots:
                    note("sweep_chain", root, day + 1, step + 1, sweep_chain(root, now=at))
                    result["sweep_chains"] += 1
            at = start + (day + 1) * scaled_day
            _set_now(at)
            for root in roots:
                person = persons.get(root.name) or persons.get(str(root)) or _owner(root)
                note("daily", root, day + 1, None, daily(root, person, now=at, offline_roots=offline_roots))
                result["dailies"] += 1
            if verdicts_after_day is not None and day + 1 == verdicts_after_day:
                for root in roots:
                    items = {str(k): _verdict(v) for k, v in (_pick(verdicts, root) or {}).items()}
                    if not items:
                        continue
                    signer = _pick(signers, root) or _owner(root)
                    ctx = _context("sign", root, at)
                    _step(ctx, "sign", "sign", "apply", root, items, signer, now=at)
                    note("sign", root, day + 1, None, ctx)
                    result["verdicts_applied"].append({"root": str(root), "by": signer,
                                                       "items": len(items)})
    finally:
        if saved is None:
            os.environ.pop("SPACE_NOW", None)
        else:
            os.environ["SPACE_NOW"] = saved
    result["end"] = pages.iso(at)
    return result


def _person_only(what: str) -> None:
    """ValueError when this process runs inside a routine or a cloud run (`routine.in_routine`)."""
    import routine   # here and not at the top: routine reads this module's spaces
    running = routine.in_routine()
    if running:
        raise ValueError(f"{what} is the person's to start, and {running}")


def _verdict(value) -> tuple:
    if isinstance(value, bool):
        return ("yes" if value else "no", "")
    if isinstance(value, (list, tuple)):
        return (str(value[0]), str(value[1]) if len(value) > 1 else "")
    if isinstance(value, dict):
        return (str(value.get("verdict") or value.get("answer") or "yes"), str(value.get("note") or ""))
    return (str(value), "")


def load_verdicts(path, roots: list) -> dict:
    """`{slug: {item_id: verdict}}` from a yaml file, keyed by the matching root."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    by_name: dict = {}
    for root in roots:
        root = Path(root)
        by_name[root.name] = root
        try:
            by_name.setdefault(spaceyaml.load(root).name, root)
        except (FileNotFoundError, ValueError):
            pass
    out: dict = {}
    for slug, items in (data or {}).items():
        root = by_name.get(str(slug))
        if root is None:
            continue
        out[root] = {str(k): _verdict(v) for k, v in (items or {}).items()}
    return out


# ----- CLI ---------------------------------------------------------------------------

def _pairs(values) -> dict:
    out: dict = {}
    for item in values or []:
        name, _, value = str(item).partition("=")
        if name and value:
            out[name] = value
    return out


PROFILE_ORDER = {"personal": 0, "project": 1, "channel": 2, "brain": 3}


def _machine_roots() -> list:
    """The folders `~/.spaces/spaces.yaml` lists: every checkout on this machine with a sync task."""
    path = Path.home() / ".spaces" / "spaces.yaml"
    try:
        rows = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("spaces") or []
    except (OSError, ValueError):
        rows = []
    return [Path(str(row.get("root") or "")) for row in rows if isinstance(row, dict)]


def checkout_of(root, mount: str) -> Path | None:
    """This machine's own checkout of the space that `root` mounts as `mount`, or None. A mount is a
    read-only copy (`pages.copy_of`), so a verdict on one of its items is given in that checkout;
    with none on this machine it goes to that space as a request. A mount kept outside the space
    that lists it is that checkout. Else it is the space of that name on the machine's list, and
    of the same repository when the mount names one and the checkout has an origin: two spaces
    can carry one name."""
    try:
        space = spaceyaml.load(root)
        copy = space.mount_root(mount)
        name = spaceyaml.load(copy).name
    except Exception:   # no such mount here, or one that is not on disk
        return None
    if pages.copy_of(copy) is None:
        return copy.resolve()
    import ghrest
    wanted = next((str(m.get("repo") or "") for m in space.mounts if m.get("name") == mount), "")
    for other in _machine_roots():
        try:
            if spaceyaml.load(other).name != name or pages.copy_of(other) is not None:
                continue
        except Exception:
            continue
        slug = ghrest.repo_slug(other) if wanted else None
        if slug and slug.casefold() != wanted.casefold():
            continue
        return other.resolve()
    return None


def spaces_on_machine(workspace, person: str | None = None) -> list:
    """The roots the person's chains run over: their workspace first, then every space on this
    machine with a sync task (`~/.spaces/spaces.yaml`) that they own, projects before
    channels and the brain last. A crew member's checkout of someone else's project is left to
    its owner's machine, so a space's chain runs in one place."""
    workspace = Path(workspace).resolve()
    person = person or spaceyaml.load(workspace).owner
    found = []
    for root in _machine_roots():
        if not (root / "space.yaml").exists() or root.resolve() == workspace:
            continue
        try:
            other = spaceyaml.load(root)
        except Exception:
            continue
        if other.owner == person:
            found.append((PROFILE_ORDER.get(other.profile, 2), other.name, root.resolve()))
    return [workspace] + [r for _, _, r in sorted(found)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="chain.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sweep = sub.add_parser("sweep")
    sweep.add_argument("root", nargs="?", default=".")
    sweep.add_argument("--cloud", action="store_true")
    day = sub.add_parser("daily")
    day.add_argument("root", nargs="?", default=".")
    day.add_argument("--person")
    day.add_argument("--shadow", action="store_true")
    day.add_argument("--offline", nargs="*", default=[], metavar="name=path")
    day.add_argument("--cloud", action="store_true")
    held = sub.add_parser("gate")
    held.add_argument("workspace", nargs="?", default=".")
    held.add_argument("--chain", choices=("sweep", "daily"))   # the sweep's prompt names itself; a prompt that names
    # nothing (one rendered before the sweep could move) is the daily's or another routine's
    places = sub.add_parser("spaces")
    places.add_argument("root", nargs="?", default=".")
    places.add_argument("--person")
    real = sub.add_parser("checkout")
    real.add_argument("root")
    real.add_argument("mount")
    virt = sub.add_parser("virtual")
    virt.add_argument("--days", type=int, required=True)
    virt.add_argument("--roots", nargs="+", required=True)
    virt.add_argument("--steps", type=int, default=3)
    virt.add_argument("--persons", nargs="*", default=[], metavar="root=Name")
    virt.add_argument("--offline", nargs="*", default=[], metavar="name=path")
    # No --verdicts here: signing belongs to sign.py with its read-back, and onboarding's fixture
    # applies its verdicts in process. A routine's allowed `chain.py` command can never sign.
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    import needs_you   # here and not at the top: its helpers are the CLI's alone
    needs_you.utf8_stdio()

    offline = {k: Path(v) for k, v in _pairs(getattr(args, "offline", [])).items()} or None
    if args.command == "gate":
        why = gate(Path(args.workspace), chain=args.chain)
        if why:
            print(f"The chains do not run on this computer now: {why}.")
        return GATED if why else 0
    if args.command == "spaces":
        for root in spaces_on_machine(Path(args.root), args.person):
            print(root.as_posix())
        return 0
    if args.command == "checkout":
        found = checkout_of(Path(args.root), args.mount)
        if found is None:
            print(f"this computer has no checkout of {pages._shown(args.mount)}: nothing is answered in the "
                  "mount. The answer goes to that space as a request (the sign skill says how)", file=sys.stderr)
            return 1
        print(found.as_posix())
        return 0
    if args.command == "sweep":
        result = sweep_chain(Path(args.root), cloud=args.cloud)
    elif args.command == "daily":
        result = daily(Path(args.root), args.person, shadow=args.shadow, offline_roots=offline,
                       cloud=args.cloud)
    else:
        try:   # the virtual clock is a person's run over an invented company: never a routine's
            _person_only("the virtual clock")
            result = virtual([Path(r) for r in args.roots], args.days, steps_per_day=args.steps,
                             persons=_pairs(args.persons), offline_roots=offline)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
    summary = {k: v for k, v in result.items() if k != "steps"}
    print(json.dumps(summary, indent=2, default=str))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
