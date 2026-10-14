"""The chain: routines run by dependency, each reading the files the previous one wrote.

Three times a day the sweep chain: `transcripts.scan` (a personal space with a transcripts
folder) → `sweep.run` → `distill.run` → `check.run`. Once a day
the daily chain: `status.run` (with landing at the home) → `release.ship` (only items
already listed) → `outbox_reconcile.run` (where an outbox exists) → `morning_view.run` →
the answer and leak tests when their files exist. A step that fails
is recorded as `{"step", "error"}` and the next step runs on yesterday's output; a tool
that is not present yet is skipped with a note. Every chain run appends one run line to
`_meta/state/runs.jsonl`.

The virtual clock walks `SPACE_NOW` forward over a number of scaled days (`time_scale`
from the first root's space.yaml), runs the sweep chain for every root at every step and
the daily chain once per day, and can apply a fixture's verdicts after a given day.
`SPACE_NOW` is restored afterwards.

    python chain.py sweep [root]
    python chain.py daily [root] [--person <name>] [--shadow] [--offline name=path ...]
    python chain.py virtual --days N --roots a b c [--steps 3] [--persons a=Ada ...]
                            [--offline name=path ...] [--verdicts verdicts.yaml --after-day N]
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
        return None
    ctx["steps"][step] = _plain(result)
    return result


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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8", newline="\n")


def _template_sync_due(space: spaceyaml.Space, now: dt.datetime) -> bool:
    """Once a scaled week, where a provenance file names the template; never in the fixture."""
    if space.mode == "fixture" or not (space.root / "_meta" / "state" / "template-version.yaml").exists():
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
    return last is None or now - last >= space.scale(7 * 24)


def _run_line(space: spaceyaml.Space, ctx: dict, now: dt.datetime) -> None:
    _mark_stale(space, ctx, now)
    notes = []
    if ctx["errors"]:
        notes.append("errors: " + "; ".join(f"{e['step']}: {e['error']}" for e in ctx["errors"]))
    if ctx["skipped"]:
        notes.append("skipped: " + "; ".join(ctx["skipped"]))
    line = {"routine": ctx["routine"], "at": pages.iso(now), "space": space.name,
            "cost_tokens": 0, "notes": " | ".join(notes) or "ok"}
    path = space.root / RUNS_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")


# ----- the two chains ----------------------------------------------------------------

def sweep_chain(root, now: dt.datetime | None = None) -> dict:
    """sweep → distill → check, each on the files the previous step wrote."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    ctx = _context("sweep_chain", root, now)
    if space.profile == "personal" and (space.data.get("transcripts") or {}).get("path"):
        _step(ctx, "transcripts", "transcripts", "scan", root, now=now)
    _step(ctx, "sweep", "sweep", "run", root, now=now)
    if space.profile == "personal":
        _step(ctx, "reply", "reply", "run", root, now=now)
    _step(ctx, "distill", "distill", "run", root, now=now)
    _step(ctx, "check", "check", "run", root, now=now)
    _run_line(space, ctx, now)
    return ctx


def daily(root, person: str | None = None, now: dt.datetime | None = None, shadow: bool = False,
          offline_roots: dict | None = None) -> dict:
    """status → access → requests → release (listed items only) → outbox reconcile → morning
    view → answer and leak tests."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    person = person or space.owner
    ctx = _context("daily", root, now)
    if _template_sync_due(space, now):
        _step(ctx, "template_sync", "template_sync", "guarded", root)
    _step(ctx, "status", "status", "run", root, now=now)
    _step(ctx, "access", "access", "run", root, now=now)   # readers on the host against the audience
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

    `inject(day, at)`, when given, is called at the start of each scaled day (day 1-based)
    so a test can let late records arrive after earlier days were signed."""
    roots = [Path(r) for r in roots]
    if not roots:
        raise ValueError("virtual needs at least one root")
    if days < 1 or steps_per_day < 1:
        raise ValueError("days and steps_per_day must be 1 or more")
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


def spaces_on_machine(workspace, person: str | None = None) -> list:
    """The roots the person's chains run over: their workspace first, then every space on this
    machine with a sync task (`~/.spaces/spaces.yaml`) that they own, projects before
    channels and the brain last. A crew member's checkout of someone else's project is left to
    its owner's machine, so a space's chain runs in one place."""
    workspace = Path(workspace).resolve()
    person = person or spaceyaml.load(workspace).owner
    path = Path.home() / ".spaces" / "spaces.yaml"
    try:
        rows = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("spaces") or []
    except (OSError, ValueError):
        rows = []
    found = []
    for row in rows:
        root = Path(str(row.get("root") or ""))
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
    day = sub.add_parser("daily")
    day.add_argument("root", nargs="?", default=".")
    day.add_argument("--person")
    day.add_argument("--shadow", action="store_true")
    day.add_argument("--offline", nargs="*", default=[], metavar="name=path")
    places = sub.add_parser("spaces")
    places.add_argument("root", nargs="?", default=".")
    places.add_argument("--person")
    virt = sub.add_parser("virtual")
    virt.add_argument("--days", type=int, required=True)
    virt.add_argument("--roots", nargs="+", required=True)
    virt.add_argument("--steps", type=int, default=3)
    virt.add_argument("--persons", nargs="*", default=[], metavar="root=Name")
    virt.add_argument("--offline", nargs="*", default=[], metavar="name=path")
    virt.add_argument("--verdicts")
    virt.add_argument("--after-day", type=int, dest="after_day")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    offline = {k: Path(v) for k, v in _pairs(getattr(args, "offline", [])).items()} or None
    if args.command == "spaces":
        for root in spaces_on_machine(Path(args.root), args.person):
            print(root.as_posix())
        return 0
    if args.command == "sweep":
        result = sweep_chain(Path(args.root))
    elif args.command == "daily":
        result = daily(Path(args.root), args.person, shadow=args.shadow, offline_roots=offline)
    else:
        roots = [Path(r) for r in args.roots]
        verdicts = load_verdicts(args.verdicts, roots) if args.verdicts else None
        result = virtual(roots, args.days, steps_per_day=args.steps, persons=_pairs(args.persons),
                         offline_roots=offline, verdicts_after_day=args.after_day,
                         verdicts=verdicts)
    summary = {k: v for k, v in result.items() if k != "steps"}
    print(json.dumps(summary, indent=2, default=str))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
