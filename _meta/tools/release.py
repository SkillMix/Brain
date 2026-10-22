"""Release: the one way content leaves a space (shape 1, rules.md section 6).

`ship` walks `_outbound/queue/` and `_outbound/signals/`. An item leaves when its
`stage_after` has passed and, for a `window` item, a morning view has listed it
(`listed: true`); signals, at-once items and retractions need no listing. Every item is
scrubbed again on the way out: a never-leaves term of this space, a left-out term of the
destination, a mode mismatch (`shadow` ships only to `shadow`) or a secrets-scan finding
moves it to `_outbound/held/` with a reason. What ships lands in the destination's
`_inbound/candidates/<id>.md` as a draft with `released_by`, `released_from`,
`release_class` and `evidence`; a signed page keeps its signature untouched. Offline the
destination root is given; online it is the checkout under `~/.spaces/<destination>`
(or `$SPACES_HOME`) and `publish()` does all the git work. Shipped items are remembered
in `_meta/state/released.jsonl` (append-only) so `retract` can find them. Routines never
write `status: stable` and delete only their own queue files.

    python release.py [root] ship [--offline name=path ...]
    python release.py [root] retract <item-id>      a retraction item, ships at once
    python release.py [root] pull-back <item-id>    queue -> held, reason pulled

sweep.py imports the shared helpers (`destination_space`, `hold`, `append_run_line`).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import registry as registry_mod  # noqa: E402
import spaceyaml  # noqa: E402
import authority  # noqa: E402

try:  # the secrets scan is another tool; shipping works without it
    import scan  # noqa: E402
except ImportError:  # pragma: no cover
    scan = None

QUEUE = Path("_outbound") / "queue"
HELD = Path("_outbound") / "held"
SIGNALS = Path("_outbound") / "signals"
LANDING = Path("_inbound") / "candidates"
SIGNAL_LANDING = Path("_inbound") / "signals"
RUNS = Path("_meta") / "state" / "runs.jsonl"
RELEASED = Path("_meta") / "state" / "released.jsonl"
PAUSED = Path("_meta") / "state" / "paused.jsonl"
PAUSE_HOURS = 4 * 7 * 24
QUEUE_ONLY_KEYS = ("class", "stage_after", "listed", "destination", "from_space",
                   "why", "confidence", "routed_by", "content")   # the router's reasons stay home
NEEDS_NO_LISTING = ("at_once", "signal", "retraction")
COMMANDS = ("ship", "retract", "pull-back")


@dataclass
class ShipReport:
    shipped: int = 0
    skipped_unlisted: int = 0
    held: int = 0
    retracted: int = 0
    destinations: dict = field(default_factory=dict)
    signals: int = 0
    waiting: int = 0
    requeued: int = 0     # items held for a shadow-looking destination, back in the queue
    unreachable: int = 0  # queue items whose destination has no root on this machine
    rerouted: int = 0     # queue items re-addressed to their subject's current home
    failed: int = 0       # queue items whose online publish failed; they stay queued
    requests: int = 0     # requests and answers carried through a corridor


# ----- shared helpers (sweep.py imports these) -----------------------------


def spaces_home() -> Path:
    """Where the release clones live: $SPACES_HOME, else ~/.spaces/.release (a working checkout
    made by onboard lives under ~/.spaces/<repository>, never in the same folder)."""
    return Path(os.environ.get("SPACES_HOME") or (Path.home() / ".spaces" / ".release"))


def checkout_of(destination: str) -> Path | None:
    """The writable online checkout of a destination, when it exists."""
    candidate = spaces_home() / destination
    return candidate if (candidate / ".git").exists() else None


def _mount_repo(space: spaceyaml.Space, destination: str) -> str | None:
    """The repository behind a destination: the mount of that name, else the mount whose space
    is named so (the brain is mounted as `brain` but releases are addressed to its name), else
    the `match.repo` of the registry subject whose home it is, so a missing mount alone never
    leaves a release stuck."""
    for mount in space.mounts:
        if mount.get("name") == destination and mount.get("repo"):
            return str(mount["repo"])
    for mount in space.mounts:
        root = space.mount_root(str(mount.get("name", "")))
        if root is None or not mount.get("repo"):
            continue
        try:
            if spaceyaml.load(root).name == destination:
                return str(mount["repo"])
        except Exception:
            continue
    try:
        subjects = registry_mod.Registry(space.root, space).everywhere()
    except Exception:
        return None
    return next((registry_mod.repo_url(s.match["repo"]) for s in subjects
                 if s.home == destination and s.match.get("repo")), None)


def _discard(path: Path) -> None:
    """Remove a clone this run made seconds earlier (git keeps some files read-only)."""
    for item in path.rglob("*"):
        try:
            if item.is_file():  # a directory keeps its mode: without x it cannot be walked
                os.chmod(item, item.stat().st_mode | stat.S_IWRITE)
        except OSError:
            pass
    shutil.rmtree(path, ignore_errors=True)


def _origin(checkout: Path) -> str | None:
    """The origin a release clone was made from; None when it has none or git cannot say."""
    try:
        res = subprocess.run(["git", "--git-dir", str(checkout / ".git"), "config", "--get",
                              "remote.origin.url"], capture_output=True, text=True)
    except OSError:
        return None
    return res.stdout.strip() or None if res.returncode == 0 else None


def _set_aside(checkout: Path) -> None:
    """Move a release clone out of the way under `.stale/`; nothing is deleted."""
    stale = checkout.parent / ".stale"
    stale.mkdir(parents=True, exist_ok=True)
    target, n = stale / checkout.name, 1
    while target.exists():
        n += 1
        target = stale / f"{checkout.name}-{n}"
    checkout.rename(target)


def ensure_checkout(space: spaceyaml.Space, destination: str) -> Path | None:
    """The destination's online checkout, cloned on first use from the repository its mount (or
    its registry subject) names. No repository, a failed clone or a clone that is not the space
    so named: None, and the items stay queued as unreachable. A clone of another repository under
    that name (an older space of the same name) is set aside and the destination cloned fresh."""
    found = checkout_of(destination)
    repo = _mount_repo(space, destination)
    if found is not None:
        origin = _origin(found)
        if not (repo and origin) or registry_mod.same_repo(origin, repo):
            return found
        _set_aside(found)
    if not repo:
        return None
    target = spaces_home() / destination
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["git", "clone", "--quiet", "--no-recurse-submodules", str(repo), str(target)],
                       check=True, capture_output=True, text=True)
    except (subprocess.CalledProcessError, OSError):
        return None
    try:
        is_space = spaceyaml.load(target).name == destination
    except Exception:
        is_space = False
    if not is_space:  # a code repository, or another space: never published into
        _discard(target)
        return None
    return target if (target / ".git").exists() else None


def refresh_checkout(checkout: Path) -> bool:
    """Bring a release clone to its origin's main before its settings are read: a clone is only
    fetched when something ships, so its space.yaml can be days behind the destination."""
    git = ["git", "-C", str(checkout)]
    try:
        fetched = subprocess.run(git + ["fetch", "--quiet", "origin", "--no-recurse-submodules"],
                                 capture_output=True, text=True)
        if fetched.returncode != 0:
            return False
        moved = subprocess.run(git + ["checkout", "--quiet", "--detach", "origin/main"],
                               capture_output=True, text=True)
        return moved.returncode == 0
    except OSError:
        return False


def _requeue_mode_held(root: Path, space: spaceyaml.Space) -> int:
    """Items a live space held only because their destination looked shadow go back to the
    queue; the next scrub reads the destination as it is now. Held signals stay held."""
    if space.mode != "live":
        return 0
    count = 0
    for path in sorted((Path(root) / HELD).glob("*.md")):
        try:
            fm, body = pages.read(path)
        except Exception:
            continue
        live_hold = str(fm.get("why") or "").startswith("this space runs live")
        if fm.get("reason") != "mode" or fm.get("class") == "signal" or not live_hold:
            continue
        clean = {k: v for k, v in fm.items() if k not in ("reason", "why")}
        pages.write(Path(root) / QUEUE / f"{fm['id']}.md", clean, body)
        path.unlink()
        count += 1
    return count


def destination_space(space: spaceyaml.Space, destination: str,
                      dest_root: Path | None = None) -> spaceyaml.Space | None:
    """The destination's settings: its given root, else its mount (moved by every sync run), else
    the mount whose space is named so (the brain is mounted as `brain` but addressed by its name),
    else its release checkout (refreshed only when something ships)."""
    mounted = [space.mount_root(str(mount.get("name", ""))) for mount in space.mounts]
    for root, by_name in [(dest_root, False), (space.mount_root(destination), False),
                          *[(root, True) for root in mounted], (checkout_of(destination), False)]:
        if root is not None and (Path(root) / "space.yaml").exists():
            try:
                found = spaceyaml.load(root)
            except Exception:
                continue
            if not by_name or found.name == destination:
                return found
    return None



def hold(root: Path, fm: dict, body: str, reason: str, why: str) -> Path:
    """Write an item to _outbound/held/ with its reason; held items never leave by themselves."""
    held_fm = dict(fm, reason=reason, why=why)
    if reason == "doubt":  # shown as one count; opened on demand, each with a recommendation
        held_fm.setdefault("recommendation", "no: keep it home")
        held_fm.setdefault("default", "stays home")
    return pages.write(Path(root) / HELD / f"{fm['id']}.md", held_fm, body)


def append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line.rstrip("\n") + "\n")


def append_run_line(root: Path, routine: str, now: dt.datetime, space: spaceyaml.Space,
                    cost_tokens: int, notes: dict) -> None:
    line = {"routine": routine, "at": pages.iso(now), "space": space.name,
            "cost_tokens": int(cost_tokens), "notes": notes}
    append_line(Path(root) / RUNS, json.dumps(line))


# ----- the path pause (vision section 10) ---------------------------------------
def pause(root, path: str | None, now: dt.datetime, reason: str, item: str | None = None,
          space: spaceyaml.Space | None = None) -> dict | None:
    """Pause the path that let an at-once item through (a standing choice, a source's
    audience or the router's judgment for a destination) for four scaled weeks. Lowering is automatic; only a person raises a level."""
    if not path:
        return None
    space = space or spaceyaml.load(root)
    row = {"path": str(path), "at": pages.iso(now), "until": pages.iso(now + space.scale(PAUSE_HOURS)),
           "reason": reason, "item": item}
    append_line(Path(root) / PAUSED, json.dumps(row))
    return row


def router_path(destination: str) -> str:
    return f"router:{destination}"


def path_of(shipped: dict) -> str | None:
    """The path that let a shipped item through. An at-once item that names none was the
    router's judgment, sent before its items carried the path: the router's for that destination."""
    if shipped.get("path"):
        return str(shipped["path"])
    if shipped.get("class") == "at_once" and shipped.get("destination"):
        return router_path(str(shipped["destination"]))
    return None


def is_paused(root, path: str | None, now: dt.datetime) -> bool:
    if not path:
        return False
    target = Path(root) / PAUSED
    if not target.exists():
        return False
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("path") != path:
            continue
        try:
            if pages.parse_iso(row.get("at")) <= now < pages.parse_iso(row.get("until")):
                return True
        except (TypeError, ValueError):
            continue
    return False


def slug_of(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", str(name).lower()).strip("-") or "space"


# ----- the scrub -------------------------------------------------------------
def scrub_reason(space: spaceyaml.Space, dest_space: spaceyaml.Space | None, fm: dict,
                 body: str, path: Path | None = None) -> tuple[str, str] | None:
    """Why an item may not leave: (reason, why) or None when it is clean. Facts only: what it
    says was judged before it was queued (the router and its check, or a person)."""
    if dest_space is not None and dest_space.mode != space.mode:
        return "mode", (f"this space runs {space.mode}, the destination "
                        f"{fm.get('destination')} runs {dest_space.mode}")
    if path is not None and scan is not None and Path(path).is_file() and \
            Path(path).stat().st_size > getattr(scan, "MAX_BYTES", 1024 * 1024):
        return "scan", "too large for the secrets scan to read"
    if path is not None and scan is not None and hasattr(scan, "scan_paths"):
        try:
            findings = scan.scan_paths([Path(path)])
        except Exception:
            findings = []
        if findings:  # never quote a finding
            return "scan", f"secrets scan: {len(findings)} finding(s)"
    return None


# ----- landing -----------------------------------------------------------------
def landed_frontmatter(space: spaceyaml.Space, fm: dict, now: dt.datetime) -> dict:
    out = {k: v for k, v in fm.items() if k not in QUEUE_ONLY_KEYS}
    if out.get("status") != "stable" or not all(out.get(k) for k in pages.SIGNATURE_FIELDS):
        out["status"] = "draft"  # a routine never writes stable; a signed page keeps its own
    out["released_by"] = space.owner or "owner"
    out["released_from"] = space.name
    out["released_at"] = pages.iso(now)
    out["release_class"] = fm.get("class")
    out["acted_for"] = fm.get("approved_by") or space.owner or "owner"
    if fm.get("path"):
        out["path"] = fm.get("path")
    if fm.get("subject") and not out.get("domain"):
        try:
            import registry as registry_mod
            domain = registry_mod.Registry(space.root, space).domain_of(str(fm["subject"]))
            if domain is not None:
                out["domain"] = domain.id
        except Exception:
            pass
    out.setdefault("evidence", [])
    return out


def mark_retracted(dest_root: Path, item_id: str) -> bool:
    target = Path(dest_root) / LANDING / f"{item_id}.md"
    if target.exists():
        fm, body = pages.read(target)
        pages.write(target, dict(fm, retracted=True), body)
    return target.exists()


def publish(checkout: Path, slug: str, source: str, write) -> bool:
    """The online leg: refresh the checkout, let `write()` add files, land them as a PR.

    Tests monkeypatch this function; everything that touches git or GitHub is here.
    """
    git = ["git", "-C", str(checkout)]
    branch = f"release/{slug}"
    subprocess.run(git + ["fetch", "origin", "--no-recurse-submodules"], check=True)
    subprocess.run(git + ["checkout", "-q", "-B", branch, "origin/main"], check=True)
    write()
    subprocess.run(git + ["add", "--", "_inbound"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "chore: release"], check=True)
    subprocess.run(git + ["push", "-u", "origin", branch, "--force-with-lease"], check=True)
    listed = subprocess.run(["gh", "pr", "list", "--head", branch, "--state", "open",
                             "--json", "number"], cwd=str(checkout), check=False,
                            capture_output=True, text=True)
    try:
        open_prs = json.loads(listed.stdout or "[]") if listed.returncode == 0 else []
    except ValueError:
        open_prs = []
    if not open_prs:
        subprocess.run(["gh", "pr", "create", "--base", "main", "--head", branch, "--title",
                        "chore: release", "--body", f"Released by {source}."],
                       cwd=str(checkout), check=True)
    return True


# ----- ship ----------------------------------------------------------------------
def _current_home(reg, fm: dict) -> str | None:
    """The registry's home for the item's subject today, or None when unknown."""
    if reg is None or not fm.get("subject"):
        return None
    try:
        return reg.home_of(str(fm["subject"]))
    except Exception:
        return None


def _ready_items(root: Path, now: dt.datetime, report: ShipReport,
                 space: spaceyaml.Space | None = None) -> list:
    ready = []
    reg = gating = None
    unreadable = False
    if space is not None:
        import classify as gating
        try:
            reg = registry_mod.Registry(root, space)
        except Exception as exc:   # the gate cannot be read: nothing about a subject leaves
            unreadable = True
            print(f"release: the registry cannot be read ({exc.__class__.__name__}): "
                  "items about a subject wait", file=sys.stderr)
    for path in sorted((root / QUEUE).glob("*.md")):
        fm, body = pages.read(path)
        if not fm.get("id") or not fm.get("destination"):
            continue
        if unreadable and fm.get("subject") and fm.get("class") != "retraction":
            report.waiting += 1
            continue
        # a subject that moved home since the item was queued (its space offboarded, say)
        # takes the item along; it never ships to a destination the registry no longer names
        home = _current_home(reg, fm)
        if home and home != fm["destination"] and space is not None and home != space.name:
            fm["rerouted_from"], fm["destination"] = fm["destination"], home
            pages.write(path, fm, body)
            report.rerouted += 1
        if gating is not None and reg is not None and fm.get("class") != "retraction":
            # the backstop: whatever path queued it, an unclassified subject does not leave
            blocked = gating.gate_all(space, reg, fm.get("subject"), fm.get("also_about"), fm["destination"])
            if blocked:
                hold(root, dict(fm, kept=True) if blocked[0] == "kept" else fm, body, *blocked)
                path.unlink()
                report.held += 1
                continue
            if fm.get("class") == "at_once" and not fm.get("approved_by") and \
                    gating.first_release(root, space, reg, fm.get("subject")):
                why = f"the first release since {fm['subject']} was classified company"
                fm.update({"class": "window", "stage_after": pages.iso(now + space.window()),
                           "first_release": str(fm["subject"]),
                           "why": "; ".join(x for x in (str(fm.get("why") or ""), why) if x)})
                pages.write(path, fm, body)
                report.waiting += 1
                continue
        try:
            stage_after = pages.parse_iso(fm.get("stage_after") or pages.iso(now))
        except (TypeError, ValueError):
            stage_after = now
        if stage_after > now:
            report.waiting += 1
        elif fm.get("class") not in NEEDS_NO_LISTING and fm.get("listed") is not True:
            report.skipped_unlisted += 1
        elif (space is not None and fm.get("class") not in ("retraction", "signal")
              and not fm.get("approved_by") and not authority.allows(space, "release", "after_window")):
            report.waiting += 1   # the badge says propose: nothing leaves without a person's yes
        else:
            ready.append((path, fm, body))
    return ready


# a registry note goes to the registry that holds its subject; a failed sample goes back to the
# space that released the item: neither carries the subject's material toward a home
UNGATED_SIGNALS = ("registry", "sample_failed")


def _signals(root: Path, now: dt.datetime) -> list:
    """(path, signal, item-shaped view) for every pending signal; the view feeds the scrub."""
    out = []
    for path in sorted((root / SIGNALS).glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict) and data.get("destination"):
            view = {"id": data.get("id"), "type": "source", "title": "signal", "status": "draft",
                    "generated": pages.generated("release", now), "class": "signal",
                    "destination": data["destination"], "subject": data.get("subject"),
                    "stage_after": data.get("at") or pages.iso(now), "listed": False,
                    "evidence": []}
            out.append((path, data, view))
    return out


def ship(root, now: dt.datetime | None = None,
         offline_roots: dict[str, Path] | None = None) -> ShipReport:
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    offline_roots = {k: Path(v) for k, v in (offline_roots or {}).items()}
    report = ShipReport()
    try:   # registry notes that arrived here (the brain applies them every sync)
        import classify
        classify.land_notes(root, space, now)
    except Exception as exc:  # a note never stops the ship
        print(f"release: registry notes not applied ({exc.__class__.__name__})", file=sys.stderr)
    report.requeued = _requeue_mode_held(root, space)
    try:   # what waited for a classification goes back to the queue once it has one
        import classify
        back = classify.requeue(root, space, None, now)
        report.requeued += back["requeued"]
        report.rerouted += back["rerouted"]
    except Exception as exc:  # held items stay held: the safe side
        print(f"release: classification requeue skipped ({exc.__class__.__name__}: {exc})", file=sys.stderr)
    batches: dict = {}
    for path, fm, body in _ready_items(root, now, report, space):
        batches.setdefault(fm["destination"], ([], []))[0].append((path, fm, body))
    try:
        reg = registry_mod.Registry(root, space)
    except Exception:
        reg = None
    for path, data, view in _signals(root, now):
        home = None if data.get("kind") == "registry" else _current_home(reg, data)   # a note goes to its registry
        if home and home != data["destination"] and home != space.name:
            # a signal follows its subject to its current home, like a queue item
            data["rerouted_from"], data["destination"] = data["destination"], home
            view["destination"] = home
            path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
            report.rerouted += 1
        if data.get("kind") not in UNGATED_SIGNALS and data.get("subject"):
            if reg is None:   # the gate cannot be read: the signal waits
                continue
            import classify
            blocked = classify.gate(space, reg, data.get("subject"), data["destination"])
            if blocked:   # a signal of a held subject waits; one kept home never goes
                if blocked[0] == "kept":
                    path.unlink()
                continue
        batches.setdefault(data["destination"], ([], []))[1].append((path, data, view))
    for destination, (items, signals) in sorted(batches.items()):
        offline = offline_roots.get(destination)
        checkout = None if offline is not None else ensure_checkout(space, destination)
        if checkout is not None:
            refresh_checkout(checkout)
        dest_root = offline if offline is not None else checkout
        dest_space = destination_space(space, destination, dest_root)
        outgoing_items, outgoing_signals = [], []
        for path, fm, body in items:
            problem = scrub_reason(space, dest_space, fm, body, path)
            if problem and problem[0] == "mode" and space.mode == "live":
                report.waiting += 1   # the destination may be a stale view: wait, never hold
                continue
            if problem:
                hold(root, fm, body, *problem)
                path.unlink()
                report.held += 1
            else:
                outgoing_items.append((path, fm, body))
        for path, data, view in signals:
            problem = scrub_reason(space, dest_space, view, "")
            if problem and problem[0] == "mode" and space.mode == "live":
                continue
            if problem:
                hold(root, view, "", *problem)
                path.unlink()
                report.held += 1
            else:
                outgoing_signals.append((path, data))
        if not outgoing_items and not outgoing_signals:
            continue
        if dest_root is None:
            report.unreachable += len(outgoing_items)
            continue

        def write(dest_root=dest_root, items=outgoing_items, signals=outgoing_signals):
            for _, fm, body in items:
                pages.write(dest_root / LANDING / f"{fm['id']}.md",
                            landed_frontmatter(space, fm, now), body)
                if fm.get("class") == "retraction" and fm.get("retracts"):
                    mark_retracted(dest_root, str(fm["retracts"]))
            for _, data in signals:
                target = dest_root / SIGNAL_LANDING / f"{data['id']}.yaml"
                target.parent.mkdir(parents=True, exist_ok=True)
                data = dict(data, **{"from": data.get("from") or space.name})
                target.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                                  encoding="utf-8", newline="\n")

        try:
            if checkout is not None:
                publish(checkout, slug_of(space.name), space.name, write)
            else:
                write()
        except (subprocess.CalledProcessError, OSError) as exc:
            report.failed += len(outgoing_items)
            print(f"release: {destination}: publish failed ({exc.__class__.__name__})",
                  file=sys.stderr)
            continue
        for path, fm, _ in outgoing_items:
            path.unlink()
            report.shipped += 1
            report.destinations[destination] = report.destinations.get(destination, 0) + 1
            report.retracted += fm.get("class") == "retraction"
            append_line(root / "log.md",
                        f"- {pages.iso(now)} released {fm['id']} to {destination} ({fm.get('class')})"
                        + (f" via {fm['path']}" if fm.get("path") else ""))
            append_line(root / RELEASED, json.dumps({
                "id": fm["id"], "at": pages.iso(now), "destination": destination,
                "class": fm.get("class"), "subject": fm.get("subject"),
                "evidence": list(fm.get("evidence") or []), "kind": fm.get("kind"),
                "path": fm.get("path")}))
        for path, _ in outgoing_signals:
            path.unlink()
            report.signals += 1
    ship_requests(root, space, now, offline_roots, report)
    append_run_line(root, "release", now, space, 0, {
        "shipped": report.shipped, "signals": report.signals, "held": report.held,
        "requests": report.requests,
        "skipped_unlisted": report.skipped_unlisted, "waiting": report.waiting,
        "requeued": report.requeued,
        "unreachable": report.unreachable, "rerouted": report.rerouted, "failed": report.failed,
        "destinations": report.destinations})
    return report


# ----- the fourth wire: requests and answers through the corridor -----------------------
REQUESTS_OUT = Path("_outbound") / "requests"
ANSWERS_OUT = Path("_outbound") / "answers"


def _corridor_root(space, name: str, offline_roots: dict):
    if name in offline_roots:
        return offline_roots[name], None
    checkout = ensure_checkout(space, name)
    return checkout, checkout


def ship_requests(root: Path, space: spaceyaml.Space, now: dt.datetime, offline_roots: dict,
                  report: ShipReport) -> None:
    """Requests and answers travel through the corridor between the two spaces, at once: a
    request carries a question, never content; an answer is scrubbed like any release."""
    try:
        import request as rq
    except ImportError:
        return
    batches: dict = {}
    for folder, kind in ((REQUESTS_OUT, "request"), (ANSWERS_OUT, "answer")):
        for path in sorted((root / folder).glob("*.md")) if (root / folder).is_dir() else []:
            fm, body = pages.read(path)
            if kind == "request" and fm.get("status") != "open":
                continue
            other = str(fm.get("to") or "")
            cname, _ = rq.corridor(space, other, offline_roots)
            if cname is None:
                report.unreachable += 1
                continue
            batches.setdefault(cname, []).append((kind, path, fm, body))
    for cname, items in sorted(batches.items()):
        dest_root, checkout = _corridor_root(space, cname, offline_roots)
        if dest_root is None:
            report.unreachable += len(items)
            continue
        dest_space = destination_space(space, cname, dest_root)
        outgoing = []
        for kind, path, fm, body in items:
            if kind == "answer":
                problem = scrub_reason(space, dest_space, dict(fm, destination=cname), body, path)
                if problem:
                    hold(root, dict(fm, destination=cname), body, *problem)
                    path.unlink()
                    report.held += 1
                    continue
            outgoing.append((kind, path, fm, body))
        if not outgoing:
            continue

        def write(dest_root=dest_root, outgoing=outgoing):
            for kind, _, fm, body in outgoing:
                folder = "requests" if kind == "request" else "answers"
                pages.write(Path(dest_root) / "_inbound" / folder / f"{fm['id']}.md", fm, body)

        try:
            if checkout is not None:
                publish(checkout, slug_of(space.name), space.name, write)
            else:
                write()
        except (subprocess.CalledProcessError, OSError) as exc:
            report.failed += len(outgoing)
            print(f"release: {cname}: publish failed ({exc.__class__.__name__})", file=sys.stderr)
            continue
        for kind, path, fm, body in outgoing:
            if kind == "request":
                pages.write(path, dict(fm, status="shipped", shipped_at=pages.iso(now), corridor=cname), body)
            else:
                path.unlink()
            report.requests += 1
            append_line(root / "log.md", f"- {pages.iso(now)} {kind} {fm['id']} through {cname}")


# ----- retract and pull back -------------------------------------------------
def released_record(root: Path, item_id: str) -> dict | None:
    """What this space knows about a shipped item: released.jsonl first, the log as fallback."""
    found = None
    released = root / RELEASED
    if released.exists():
        for line in released.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("id") == item_id:
                found = entry
    log = root / "log.md"
    if found is None and log.exists():
        pattern = re.compile(r"released " + re.escape(item_id) + r" to (\S+) \((\w+)\)")
        for match in filter(None, map(pattern.search, log.read_text(encoding="utf-8").splitlines())):
            found = {"id": item_id, "destination": match.group(1), "class": match.group(2)}
    return found


def retract(root, item_id: str, now: dt.datetime | None = None,
            offline_roots: dict[str, Path] | None = None) -> Path:
    """Queue a retraction of a shipped item; it leaves at once and needs no listing."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    shipped = released_record(root, item_id)
    if shipped is None:
        raise FileNotFoundError(f"{item_id} was not released from this space (see log.md)")
    destination = shipped["destination"]
    fm = {"id": pages.new_id(), "type": "source", "title": f"Retraction of {item_id}",
          "status": "draft", "generated": pages.generated("release", now), "class": "retraction",
          "destination": destination, "subject": shipped.get("subject"),
          "stage_after": pages.iso(now), "listed": False, "evidence": [],
          "from_space": space.name, "kind": shipped.get("kind") or "document",
          "retracts": item_id}
    path = pages.write(root / QUEUE / f"{fm['id']}.md", fm, f"retracted {item_id}\n")
    dest_root = (offline_roots or {}).get(destination)
    if dest_root is not None:
        mark_retracted(Path(dest_root), item_id)
    append_line(root / "log.md", f"- {pages.iso(now)} retraction queued for {item_id}")
    if shipped.get("class") == "at_once" and path_of(shipped):
        pause(root, path_of(shipped), now, "pulled back", item_id, space)
    return path


def pull_back(root, item_id: str) -> Path:
    root = Path(root)
    source = root / QUEUE / f"{item_id}.md"
    if not source.exists():
        raise FileNotFoundError(f"{item_id} is not in the queue")
    fm, body = pages.read(source)
    target = hold(root, fm, body, "pulled", "pulled back by the owner")
    source.unlink()
    if fm.get("path") and not str(fm["path"]).startswith("approved:"):
        space = spaceyaml.load(root)
        pause(root, fm["path"], space.now(), "pulled back", item_id, space)
    return target


# ----- CLI -----------------------------------------------------------------------
def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    offline: dict = {}
    positional = []
    while argv:
        arg = argv.pop(0)
        if arg == "--offline" and argv and "=" in argv[0]:
            name, _, path = argv.pop(0).partition("=")
            offline[name] = Path(path)
        else:
            positional.append(arg)
    root = Path(positional.pop(0)) if positional and positional[0] not in COMMANDS else Path(".")
    if not positional:
        print(__doc__)
        return 2
    command, args = positional[0], positional[1:]
    try:
        if command == "ship":
            report = ship(root, offline_roots=offline)
            print(f"shipped {report.shipped}, signals {report.signals}, held {report.held}, "
                  f"waiting {report.waiting}, unlisted {report.skipped_unlisted}, "
                  f"unreachable {report.unreachable}, failed {report.failed}")
            return 1 if report.failed else 0
        if command == "retract" and args:
            print(f"queued {retract(root, args[0], offline_roots=offline)}")
            return 0
        if command == "pull-back" and args:
            print(f"held {pull_back(root, args[0])}")
            return 0
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
