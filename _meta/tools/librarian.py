"""The librarian's structural duties (vision section 13): act on the mechanical, propose the
judgment, ask when ownership would move. `status.py` calls `run`; it stays the one writer of
structure in its space.

| Finding                                                         | Class      | Action                    |
|-----------------------------------------------------------------|------------|---------------------------|
| a subject's folders sit under another kind than the registry's  | mechanical | move them                 |
| a page whose `subject` names another subject; a loose page      | mechanical | move it to its subject    |
| a desk folder outside a kind, named like a registered subject   | mechanical | move it under the kind    |
| a desk folder outside a kind that matches no subject            | judgment   | propose where it goes     |
| a subject with more than 12 efforts or pages                    | judgment   | propose parts             |
| two subjects sharing a `match` identifier, a title or an alias  | judgment   | propose a merge           |
| three or more subjects sharing `client` or `theme`, no umbrella | judgment   | propose an umbrella       |
| a pending shape migration (`_meta/migrations/shape_<n>.py`)     | mechanical | run it                    |

Every change is act-then-verify: the answer and leak tests run before and after, a lower
score restores the snapshot, and each change is one line in `log.md` with how to undo it. A
shape migration that lowers it is undone whole and tried again after the next template update.
Links are by id, so a move never breaks one. A change that would move who owns a subject is
never made unasked: it becomes one question to the owners, asked once (`_meta/state/librarian/asked.yaml`).
An owner's yes to that question, through `sign.py apply`, makes the change (`answer`), still verified.
Judgment proposals wait in `_meta/state/librarian/<id>.yaml` until an independent second agent
reviewed them: `apply <id> --reviewed-by <name>`. With `--land`, each applied change asks the
sync script to land exactly its paths as one commit (`sync.py --only ...`).

    python librarian.py [root] scan                 run the mechanical duties, write proposals
    python librarian.py [root] list                 the open proposals
    python librarian.py [root] apply <id> --reviewed-by <name> [--land]
    python librarian.py [root] migrate [--land]     run pending shape migrations
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import registry as registry_mod  # noqa: E402
import spaceyaml  # noqa: E402

STATE = Path("_meta") / "state" / "librarian"
ASKED = STATE / "asked.yaml"
CHANGES = STATE / "changes.jsonl"     # each applied change and its paths: the sync script lands
LANDED = STATE / "landed.jsonl"       # each as one commit and records it here
MIGRATIONS = Path("_meta") / "migrations"
HELD = STATE / "migrations.yaml"      # shape migrations undone, each with the template it came from
TEMPLATE = Path("_meta") / "state" / "template-version.yaml"   # template_sync.py's provenance
SPLIT_AT = 12
UMBRELLA_AT = 3
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass
class Change:
    """One structural change: file moves and registry edits, applied together or not at all."""
    kind: str
    summary: str
    moves: list = field(default_factory=list)          # [(src rel, dst rel)]
    registry: list = field(default_factory=list)       # [{op: add|set|alias, id, ...}]
    undo: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "summary": self.summary, "moves": [list(m) for m in self.moves],
                "registry": self.registry, "undo": self.undo}

    @classmethod
    def from_dict(cls, data: dict) -> "Change":
        return cls(data["kind"], data.get("summary", ""), [tuple(m) for m in data.get("moves") or []],
                   list(data.get("registry") or []), data.get("undo", ""))

    def paths(self) -> list:
        out = []
        for src, dst in self.moves:
            out += [src, dst]
        if self.registry:
            out.append(registry_mod.REGISTRY_PATH.as_posix())
        return sorted(set(out))


@dataclass
class Report:
    applied: list = field(default_factory=list)
    reverted: list = field(default_factory=list)
    proposed: list = field(default_factory=list)
    asked: list = field(default_factory=list)
    migrations: list = field(default_factory=list)


# ----- helpers -------------------------------------------------------------------------
def _append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(("" if not text or text.endswith("\n") else "\n") + line.rstrip("\n") + "\n")


def _pid(*parts) -> str:
    return "lib-" + hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:8]


def _efforts(root: Path, kind: str, sid: str) -> list:
    base = root / "work" / kind / sid
    return sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else []


def _subject_pages(root: Path, kind: str, sid: str) -> list:
    base = root / "knowledge" / kind / sid
    return sorted(p for p in base.glob("*.md") if pages.is_page_path(p)) if base.is_dir() else []


def owners(reg: registry_mod.Registry) -> dict:
    return {s.id: reg.owner_of(s.id) for s in reg.all()}


def _registry_after(root: Path, space, change: Change) -> registry_mod.Registry:
    reg = registry_mod.Registry(root, space)
    _edit_registry(reg, change.registry, dry=True)
    return reg


def _edit_registry(reg: registry_mod.Registry, edits: list, dry: bool = False) -> None:
    for edit in edits:
        op, sid = edit.get("op"), str(edit.get("id"))
        if op == "add":
            data = {k: v for k, v in edit.items() if k != "op"}
            if reg.get(sid) is None:
                reg.subjects[sid] = registry_mod.Subject.from_dict(data)
        elif op == "set" and sid in reg.subjects:
            subject = reg.subjects[sid]
            for key, value in (edit.get("fields") or {}).items():
                if hasattr(subject, key) and key not in ("extra", "space"):
                    setattr(subject, key, value)
                else:
                    subject.extra[key] = value
        elif op == "alias" and sid in reg.subjects:
            subject = reg.subjects[sid]
            for alias in edit.get("aliases") or []:
                if alias not in subject.aliases:
                    subject.aliases.append(alias)


# ----- the score that guards every change -----------------------------------------------
def score(root: Path) -> tuple:
    """(answers passed, leaks passed): what a structural change must never lower."""
    try:
        import tests_run
    except ImportError:
        return (0, 0)
    answers = tests_run.answers(root, record=False) if (root / "_meta" / "tests" / "answers.yaml").exists() else None
    leaks = tests_run.leaks(root, record=False) if (root / "_meta" / "tests" / "leaks.yaml").exists() else None
    return ((answers.passed if answers else 0), (leaks.passed if leaks else 0))


def _lower(before: tuple, after: tuple) -> bool:
    return any(a < b for a, b in zip(after, before))


# ----- apply, verify, revert ------------------------------------------------------------
def _snapshot(root: Path, change: Change) -> dict:
    reg_path = root / registry_mod.REGISTRY_PATH
    return {"registry": reg_path.read_text(encoding="utf-8") if reg_path.exists() else None}


def _move(root: Path, src: str, dst: str) -> None:
    source, target = root / src, root / dst
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and source.is_dir():
        for child in sorted(source.iterdir()):
            _move(root, f"{src}/{child.name}", f"{dst}/{child.name}")
        if not any(source.iterdir()):
            source.rmdir()
        return
    shutil.move(str(source), str(target))


def _do(root: Path, space, change: Change) -> None:
    for src, dst in change.moves:
        if (root / src).exists():
            _move(root, src, dst)
    if change.registry:
        reg = registry_mod.Registry(root, space)
        _edit_registry(reg, change.registry)
        reg.save()


def _undo(root: Path, change: Change, snap: dict) -> None:
    for src, dst in reversed(change.moves):
        if (root / dst).exists():
            _move(root, dst, src)
    if snap.get("registry") is not None:
        (root / registry_mod.REGISTRY_PATH).write_text(snap["registry"], encoding="utf-8", newline="\n")


def land(root: Path, paths: list, runner=None) -> int:
    """One commit for one structural change: the sync script lands exactly these paths."""
    script = Path(__file__).resolve().parent / "sync.py"
    argv = [sys.executable, str(script), "--root", str(root), "--only", *paths]
    runner = runner or (lambda a: subprocess.run(a, capture_output=True, text=True).returncode)
    return runner(argv)


def _ask(root: Path, space, change: Change, before: dict, after: dict, now, report: Report, key: str) -> None:
    asked = yaml.safe_load((root / ASKED).read_text(encoding="utf-8")) if (root / ASKED).exists() else None
    asked = asked if isinstance(asked, dict) else {}
    if key in asked:
        return
    moved = {sid: (before.get(sid), after.get(sid)) for sid in after if before.get(sid) != after.get(sid)}
    people = sorted({p for pair in moved.values() for p in pair if p})
    lines = "; ".join(f"{sid}: {a or 'the space owner'} -> {b or 'the space owner'}" for sid, (a, b) in moved.items())
    qpath = root / "_inbound" / "questions" / f"librarian-{key}.md"
    fm = {"id": "p-" + key[-8:], "type": "review", "title": f"Ownership would move: {change.summary}",
          "status": "draft", "generated": pages.generated("librarian", now),
          "question": f"{change.summary}. That would move ownership ({lines}). Make the change?",
          "recommendation": "no, unless both owners agree", "default": "nothing moves",
          "for": people, "depends": 1}
    pages.write(qpath, fm, f"# Ownership would move\n\n{change.summary}.\n\nOwners before and after: {lines}.\n\n"
                           "Nothing moves until the owners answer.\n")
    asked[key] = {"at": pages.iso(now), "question": qpath.relative_to(root).as_posix(), "owners": people}
    (root / ASKED).parent.mkdir(parents=True, exist_ok=True)
    (root / ASKED).write_text(yaml.safe_dump(asked, sort_keys=False), encoding="utf-8", newline="\n")
    report.asked.append(key)


def apply_change(root, change: Change, key: str, now=None, land_it: bool = False, scorer=None,
                 report: Report | None = None, runner=None, approved_by: str = "") -> str:
    """Act, then verify: 'applied', 'reverted' or 'asked'. With `approved_by`, the owner already
    answered yes to the ownership question, so the change is made (and still verified)."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    report = report or Report()
    scorer = scorer or score
    reg = registry_mod.Registry(root, space)
    before_owners = owners(reg)
    after_owners = owners(_registry_after(root, space, change))
    if not approved_by and any(before_owners.get(sid) != after_owners.get(sid) for sid in before_owners):
        _ask(root, space, change, before_owners, after_owners, now, report, key)
        return "asked"
    snap = _snapshot(root, change)
    before = scorer(root)
    _do(root, space, change)
    after = scorer(root)
    if _lower(before, after):
        _undo(root, change, snap)
        _append(root / "log.md", f"- {pages.iso(now)} librarian reverted {change.kind}: {change.summary} "
                                 f"(tests {before} -> {after})")
        report.reverted.append(key)
        return "reverted"
    try:
        import ids
        ids.build(root)
    except Exception:
        pass
    approved = f" (approved by {approved_by})" if approved_by else ""
    _append(root / "log.md", f"- {pages.iso(now)} librarian {change.kind}: {change.summary}{approved}. "
                             f"Undo: {change.undo or 'move the files back and restore the registry line'}")
    report.applied.append(key)
    paths = change.paths() + ["log.md", "_meta/state/ids.yaml"]
    if land_it:
        land(root, paths, runner)
    else:
        _append(root / CHANGES, json.dumps({"id": f"{key}-{pages.iso(now)}", "at": pages.iso(now),
                                            "kind": change.kind, "paths": paths,
                                            "acted_for": space.owner or "owner", "path": "badge:edit_inside"}))
    return "applied"


# ----- finding work ---------------------------------------------------------------------
def kind_moves(root: Path, space, reg) -> list:
    """Folders of any subject this space can see (its own or an ancestor's) under a kind
    other than the registry's."""
    out = []
    for subject in reg.everywhere():
        for zone in ("knowledge", "work"):
            for kind in space.all_kinds:
                if kind == subject.kind or kind in spaceyaml.FIXED_KINDS:
                    continue
                if (root / zone / kind / subject.id).is_dir():
                    src, dst = f"{zone}/{kind}/{subject.id}", f"{zone}/{subject.kind}/{subject.id}"
                    out.append(Change("kind", f"{src} filed under its registry kind as {dst}", [(src, dst)],
                                      undo=f"move {dst} back to {src}"))
    return out


def stray_pages(root: Path, space, reg) -> list:
    out = []
    loose = sorted((root / "knowledge").glob("*.md")) if (root / "knowledge").is_dir() else []
    for kind in [None] + list(space.kinds):   # None: loose pages right under knowledge/
        base = root / "knowledge" / kind if kind else None
        if base is not None and not base.is_dir():
            continue
        for path in (sorted(base.rglob("*.md")) if base is not None else loose):
            if not pages.is_page_path(path):
                continue
            rel = path.relative_to(root).as_posix()
            parts = rel.split("/")
            try:
                fm, _ = pages.read(path)
            except Exception:
                continue
            sid = str(fm.get("subject") or "")
            subject = reg.get(sid) if sid else None
            if subject is None:
                continue
            here = parts[2] if len(parts) >= 4 else None
            if here == sid and parts[1] == subject.kind:
                continue
            dst = f"knowledge/{subject.kind}/{sid}/{path.name}"
            if (root / dst).exists():
                continue
            out.append(Change("stray", f"{rel} filed with its subject as {dst}", [(rel, dst)],
                              undo=f"move {dst} back to {rel}"))
    return out


def stray_desk(root: Path, space, reg) -> tuple:
    """Desk folders outside a kind: named like a subject, moved; otherwise proposed."""
    moves, proposals = [], []
    work = root / "work"
    if not work.is_dir():
        return moves, proposals
    for child in sorted(work.iterdir()):
        if child.name in space.all_kinds or child.name.startswith("."):
            continue
        subject = reg.get(child.name) or reg.find(child.name)
        if subject is not None and child.is_dir():
            src, dst = f"work/{child.name}", f"work/{subject.kind}/{subject.id}/{child.name}" \
                if subject.id != child.name else f"work/{subject.kind}/{subject.id}"
            moves.append(Change("stray", f"desk folder {src} filed as {dst}", [(src, dst)],
                                undo=f"move {dst} back to {src}"))
        else:
            proposals.append(Change("file", f"work/{child.name} sits outside every kind; file it under a "
                                            f"subject (work/<kind>/<subject>/{child.name})", []))
    return moves, proposals


def splits(root: Path, space, reg) -> list:
    """Every subject with efforts or pages here, whichever registry holds it: a desk's efforts
    mostly belong to subjects the brain holds. The parts join this space's own registry."""
    out = []
    held = set(reg.subjects)
    for subject in reg.all() + [s for s in reg.ancestors.values() if s.id not in held]:
        efforts = _efforts(root, subject.kind, subject.id)
        count = max(len(efforts), len(_subject_pages(root, subject.kind, subject.id)))
        if count <= SPLIT_AT:
            continue
        groups: dict = {}
        for effort in efforts:
            word = effort.removeprefix(subject.id + "-").split("-")[0]
            groups.setdefault(word, []).append(effort)
        groups = {w: e for w, e in groups.items() if len(e) >= 2 and SLUG.match(w)}
        if not groups:
            continue
        edits, moves = [], []
        for word, members in sorted(groups.items()):
            part = f"{subject.id}-{word}"
            if reg.get(part) is not None:
                continue
            edits.append({"op": "add", "id": part, "kind": subject.kind,
                          "title": f"{subject.title}: {word}", "state": "active", "part_of": subject.id,
                          "added_by": "librarian"})
            moves += [(f"work/{subject.kind}/{subject.id}/{e}", f"work/{subject.kind}/{part}/{e}") for e in members]
        if edits:
            out.append(Change("split", f"{subject.id} has {count} efforts: parts "
                                       f"{', '.join(e['id'] for e in edits)} under it", moves, edits,
                              undo="move the efforts back and remove the part subjects"))
    return out


def merges(reg) -> list:
    out, seen = [], set()
    subjects = sorted(reg.all(), key=lambda s: (str(s.added or ""), s.id))
    for i, a in enumerate(subjects):
        for b in subjects[i + 1:]:
            if (a.id, b.id) in seen or a.kind != b.kind:
                continue
            shared = [k for k, v in (a.match or {}).items()
                      if (b.match or {}).get(k) and str(v).lower() == str(b.match[k]).lower()]
            names_a = {n.lower() for n in a.names()}
            names_b = {n.lower() for n in b.names()}
            if not shared and not (names_a & names_b):
                continue
            seen.add((a.id, b.id))
            why = f"shared {', '.join(shared)}" if shared else "the same name"
            edits = [{"op": "alias", "id": a.id, "aliases": [b.id] + list(b.aliases)},
                     {"op": "set", "id": b.id, "fields": {"state": "archived", "merged_into": a.id}}]
            moves = []
            for zone in ("knowledge", "work"):
                base = Path(zone) / b.kind / b.id
                moves.append((base.as_posix(), (Path(zone) / a.kind / a.id).as_posix()))
            out.append(Change("merge", f"{b.id} and {a.id} are the same thing ({why}): {b.id} merges into "
                                       f"{a.id} and stays an alias", moves, edits,
                              undo=f"move {b.id}'s pages back and set its state active"))
    return out


def umbrellas(reg) -> list:
    out = []
    for key in ("client", "theme"):
        groups: dict = {}
        for subject in reg.all():
            value = getattr(subject, key, None) or subject.extra.get(key)   # `client` is a field
            if value and not subject.part_of and subject.state == "active":
                groups.setdefault(str(value), []).append(subject)
        for value, members in sorted(groups.items()):
            if len(members) < UMBRELLA_AT:
                continue
            umbrella = reg.get(value) or reg.find(value)
            edits = []
            if umbrella is None:
                uid = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
                edits.append({"op": "add", "id": uid, "kind": "topics" if key == "theme" else "clients",
                              "title": value, "state": "active", "added_by": "librarian"})
            else:
                uid = umbrella.id
            edits += [{"op": "set", "id": s.id, "fields": {"part_of": uid}} for s in members if s.id != uid]
            out.append(Change("umbrella", f"{len(members)} subjects share {key} {value}: umbrella {uid}",
                              [], edits, undo=f"remove part_of {uid} from the members"))
    return out


def migrations_pending(root: Path, space, folder: Path | None = None) -> list:
    folder = folder or (root / MIGRATIONS)
    found = []
    for path in sorted(folder.glob("shape_*.py")) if folder.is_dir() else []:
        match = re.match(r"shape_(\d+)\.py$", path.name)
        if match and int(match.group(1)) > int(space.data.get("shape") or 0):
            found.append((int(match.group(1)), path))
    return sorted(found)


def _files(root: Path, space) -> dict:
    """path -> (size, mtime) of every file a migration may change: never git's own, never a mount."""
    skip = {".git"} | {str(m.get("path") or ("brain" if m.get("name") == "brain" else f"mounts/{m.get('name')}"))
                       .replace("\\", "/").strip("/") for m in space.mounts}
    out = {}
    for base, dirs, files in os.walk(root):
        rel = Path(base).relative_to(root).as_posix()
        dirs[:] = [d for d in dirs if (d if rel == "." else f"{rel}/{d}") not in skip]
        for name in files:
            stat = (Path(base) / name).stat()
            out[name if rel == "." else f"{rel}/{name}"] = (stat.st_size, stat.st_mtime_ns)
    return out


def _template(root: Path) -> str:
    """The template commit this space last synced (template_sync.py's provenance), '' if never."""
    path = root / TEMPLATE
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    return str(data.get("commit") or "") if isinstance(data, dict) else ""


def _held(root: Path) -> dict:
    data = yaml.safe_load((root / HELD).read_text(encoding="utf-8")) if (root / HELD).exists() else None
    return data if isinstance(data, dict) else {}


def _restore(root: Path, backup: Path, files: dict, changed: set) -> None:
    """Each path the migration changed or deleted gets its old content back; each it added goes."""
    for rel in sorted(changed):
        target = root / rel
        if rel in files:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / rel, target)
        elif target.exists():
            target.unlink()
            parent = target.parent
            while parent != root and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent


def migrate(root, folder: Path | None = None, now=None, land_it: bool = False, scorer=None, runner=None) -> list:
    """Run every pending shape migration in order, each verified and landed as one commit. One that
    lowers the tests is undone whole and held until the next template update brings a fix."""
    root = Path(root)
    done = []
    scorer = scorer or score
    held, template = _held(root), _template(root)
    for number, path in migrations_pending(root, spaceyaml.load(root), folder):
        if number in held and str(held[number].get("template") or "") == template:
            break   # the later ones wait for it
        space = spaceyaml.load(root)
        now = now or space.now()
        spec = importlib.util.spec_from_file_location(f"shape_{number}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        before = scorer(root)
        files = _files(root, space)
        with tempfile.TemporaryDirectory(prefix="migration-", ignore_cleanup_errors=True) as tmp:
            for rel in files:   # the tree as it was, so the migration can be undone whole
                (Path(tmp) / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / rel, Path(tmp) / rel)
            try:
                module.migrate(root)
                broke = None
            except Exception as exc:   # one that breaks halfway is undone like one that lowers the tests
                broke = f"{type(exc).__name__}: {exc}"
            after = _files(root, space)
            changed = {p for p in set(files) | set(after) if files.get(p) != after.get(p)}
            # its one commit carries what it changed, never another session's work in progress
            paths = sorted(changed | {"space.yaml", "log.md"})
            if broke is None:
                data = yaml.safe_load((root / "space.yaml").read_text(encoding="utf-8")) or {}
                data["shape"] = number
                (root / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8",
                                                 newline="\n")
            result = broke or scorer(root)
            lowered = broke is not None or _lower(before, result)
            if lowered:
                _restore(root, Path(tmp), files, changed | {"space.yaml"})
        if lowered:
            held[number] = {"at": pages.iso(now), "template": template}
            (root / HELD).parent.mkdir(parents=True, exist_ok=True)
            (root / HELD).write_text(yaml.safe_dump(held, sort_keys=False), encoding="utf-8", newline="\n")
            why = f"it broke: {broke}" if broke else f"tests {before} -> {result}"
            _append(root / "log.md", f"- {pages.iso(now)} librarian restored shape migration {number} "
                                     f"({why}); tried again after the next template update")
            break
        if held.pop(number, None) is not None:
            (root / HELD).write_text(yaml.safe_dump(held, sort_keys=False), encoding="utf-8", newline="\n")
            paths = sorted(set(paths) | {HELD.as_posix()})
        _append(root / "log.md", f"- {pages.iso(now)} librarian ran shape migration {number}")
        done.append(number)
        if land_it:
            land(root, paths, runner)
        else:
            _append(root / CHANGES, json.dumps({"id": f"shape-{number}-{pages.iso(now)}", "at": pages.iso(now),
                                                "kind": "migration", "paths": paths}))
    return done


# ----- the run --------------------------------------------------------------------------
def proposals(root: Path) -> dict:
    base = root / STATE
    out = {}
    for path in sorted(base.glob("lib-*.yaml")) if base.is_dir() else []:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict) and data.get("status") == "open":
            out[path.stem] = data
    return out


def _propose(root: Path, change: Change, now, report: Report) -> None:
    key = _pid(change.kind, change.summary)
    path = root / STATE / f"{key}.yaml"
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"id": key, "status": "open", "at": pages.iso(now), "change": change.to_dict(),
            "review": "an independent second agent reviews this before `apply --reviewed-by`"}
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")
    report.proposed.append(key)


def run(root, now=None, land_it: bool = False, scorer=None, runner=None) -> Report:
    """The mechanical duties applied (each verified), the judgment ones proposed."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    report = Report()
    if space.profile == "channel":
        return report
    import authority
    at_once = authority.allows(space, "edit_inside", "at_once")  # the badge: propose means ask first
    # one duty at a time, each finding computed on the layout the previous duty left
    for finder in (kind_moves, stray_pages, lambda r, s, g: stray_desk(r, s, g)[0]):
        reg = registry_mod.Registry(root, space)
        for change in finder(root, space, reg):
            if at_once:
                apply_change(root, change, _pid(change.kind, change.summary), now, land_it, scorer, report, runner)
            else:
                _propose(root, change, now, report)
    reg = registry_mod.Registry(root, space)
    desk_props = stray_desk(root, space, reg)[1]
    for change in desk_props + splits(root, space, reg) + merges(reg) + umbrellas(reg):
        _propose(root, change, now, report)
    if at_once:
        report.migrations = migrate(root, now=now, land_it=land_it, scorer=scorer, runner=runner)
    return report


def apply(root, key: str, reviewed_by: str, now=None, land_it: bool = False, scorer=None, runner=None) -> str:
    """Apply one reviewed proposal; refused without a named reviewer."""
    root = Path(root)
    who = str(reviewed_by or "").strip()
    if not who:
        raise ValueError("a judgment change needs an independent review: give --reviewed-by")
    if who.lower() in ("librarian", "status", "routine") or who.lower().startswith("routine:"):
        raise ValueError("the reviewer is never the routine that proposed the change")
    path = root / STATE / f"{key}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("status") != "open":
        raise ValueError(f"{key} is not an open proposal")
    change = Change.from_dict(data["change"])
    if not change.moves and not change.registry:
        raise ValueError(f"{key} names no change to apply; file it by hand on the person's word")
    report = Report()
    outcome = apply_change(root, change, key, now, land_it, scorer, report, runner)
    data.update(status=outcome, reviewed_by=reviewed_by, closed_at=pages.iso(now or spaceyaml.load(root).now()))
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")
    return outcome


def answer(root, key: str, by: str, now=None) -> str:
    """The owner's yes to an ownership question (`librarian-<key>.md`): make the asked change."""
    root = Path(root)
    path = root / STATE / f"{key}.yaml"
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    if data.get("status") != "asked" or not data.get("change"):
        raise ValueError(f"{key} is not an asked proposal")
    outcome = apply_change(root, Change.from_dict(data["change"]), key, now, approved_by=by)
    data.update(status=outcome, approved_by=by, closed_at=pages.iso(now or spaceyaml.load(root).now()))
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")
    return outcome


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    verbs = ("scan", "list", "apply", "migrate")
    root = Path(".")
    if argv and argv[0] not in verbs:
        root = Path(argv.pop(0))
    land_it = "--land" in argv
    argv = [a for a in argv if a != "--land"]
    if not argv or argv[0] not in verbs:
        print(__doc__)
        return 2
    if argv[0] == "scan":
        report = run(root, land_it=land_it)
        print(json.dumps({k: v for k, v in report.__dict__.items()}, indent=2))
        return 0
    if argv[0] == "list":
        for key, data in proposals(root).items():
            print(f"{key}: {data['change']['kind']}: {data['change']['summary']}")
        return 0
    if argv[0] == "migrate":
        print(json.dumps(migrate(root, land_it=land_it)))
        return 0
    if len(argv) >= 4 and argv[2] == "--reviewed-by":
        print(apply(root, argv[1], argv[3], land_it=land_it))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
