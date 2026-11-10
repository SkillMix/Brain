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
The question lists every move and registry edit, so the yes is for the exact change. An owner's
yes to that question, through `sign.py apply`, makes the change (`answer`), still verified.
Judgment proposals wait in `_meta/state/librarian/<id>.yaml` until an independent second agent
reviewed them: `apply <id> --reviewed-by <name>`, which the daily chain's routine runs too. With
`--land`, each applied change asks the sync script to land exactly its paths as one commit
(`sync.py --only ...`).

A proposal file is data anyone who can write the state folder could have written, and the
reviewer's name is only a convention, so neither is trusted. `apply` and `answer` act only on a
proposal the librarian itself makes on the current tree: the same id, the same moves and the
same registry edits, else they refuse. Every move, the mechanical ones included, is checked
before anything is touched (`check_moves`): both paths stay inside the space, under
`knowledge/` or `work/` (the same zone for both), under one of the company's kinds, never on a
path the gate guards, never through a link and never onto a file that exists; a signed page
moves only within its own subject's folder, to the subject its `subject` names, or with the
merge that retires its subject. A registry edit names only the fields the finders write. A
refused change raises ValueError and moves nothing.

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
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*\Z")
KEY = re.compile(r"^lib-[0-9a-f]{8}\Z")   # a proposal's id, as _pid makes it
ZONES = ("knowledge", "work")
# The registry edits the finders make, and the fields each may name: a proposal that edits
# anything else (an owner, a home, a classification) is not one the librarian wrote.
ADD_KEYS = {"op", "id", "kind", "title", "state", "part_of", "added_by"}
SET_FIELDS = {"state", "merged_into", "part_of"}


class UnsafeMove(ValueError):
    """A structural change the librarian refuses to make, with the reason."""


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
    refused: list = field(default_factory=list)


# ----- helpers -------------------------------------------------------------------------
BREAKS = re.compile(r"[\r\n\x0b\x0c\x1c-\x1e\x85  ]+")


def _one_line(text) -> str:
    """One line from values that came from disk or data: a line break in a folder name or a
    summary never starts a line of its own (a line of log.md is never an instruction). A JSON
    row has none to lose: json.dumps escapes them."""
    return BREAKS.sub(" ", str(text)).strip()


def _append(path: Path, line: str) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    pages.append_text(path, ("" if not text or text.endswith("\n") else "\n") + _one_line(line) + "\n")


def _pid(*parts) -> str:
    return "lib-" + hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:8]


def _efforts(root: Path, kind: str, sid: str) -> list:
    base = root / "work" / kind / sid
    return sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else []


def _subject_pages(root: Path, kind: str, sid: str) -> list:
    base = root / "knowledge" / kind / sid
    return sorted(p for p in base.glob("*.md") if pages.is_page_path(p)) if base.is_dir() else []


def _governs(reg: registry_mod.Registry, sid: str):
    """The governing classification of a subject, without whose it is: what decides where its pages may go."""
    c = reg.classification(sid) or {}
    return (c.get("class"), c.get("home"), json.dumps(c.get("crew"), sort_keys=True, default=str))


def ownership_moves(before: registry_mod.Registry, after: registry_mod.Registry, change: Change) -> dict:
    """What a change would move between owners, {label: (owner before, owner after)}, with the classes
    before and after appended for a crossing whose governing classification differs: a subject whose owner
    changes, and content that crosses from one subject into another (a move between subject folders, a
    `merged_into` or `part_of`) whose owner or governing classification differs. A merged subject keeps its
    own owner_of, so the crossing is what shows a colleague's subject folded into another's. A move between
    folders compares owners only: the gates judge a page by the subject it names, which a filing move keeps.
    Under `part_of` nothing moves, so the subject's own classification after the change is compared."""
    out = {s.id: (before.owner_of(s.id), after.owner_of(s.id)) for s in before.all()
           if before.owner_of(s.id) != after.owner_of(s.id)}
    crossings = [(_parts(src)[2], _parts(dst)[2], None) for src, dst in change.moves
                 if len(_parts(src)) >= 3 and len(_parts(dst)) >= 3]
    for e in change.registry:
        fields = e.get("fields") if e.get("op") == "set" and isinstance(e.get("fields"), dict) else {}
        sid = str(e.get("id"))
        if fields.get("merged_into"):
            crossings.append((sid, str(fields["merged_into"]), str(fields["merged_into"])))
        if fields.get("part_of"):
            crossings.append((sid, str(fields["part_of"]), sid))
    for here, there, governed in crossings:
        if here == there or before.get(here) is None:
            continue
        owners = (before.owner_of(here), after.owner_of(there))
        if governed and _governs(before, here) != _governs(after, governed):   # the question must name the classes
            out[f"{here} into {there}"] = owners + (_class_label(before, here), _class_label(after, governed))
        elif owners[0] != owners[1]:
            out[f"{here} into {there}"] = owners
    return out


def _class_label(reg: registry_mod.Registry, sid: str) -> str:
    c = reg.classification(sid) or {}
    crew = c.get("crew")
    crew = ", ".join(map(str, crew)) if isinstance(crew, list) else crew
    extra = "; ".join(f"{k} {v}" for k, v in (("crew", crew), ("home", c.get("home"))) if v)
    return f"{c.get('class') or 'unclassified'}{f' ({extra})' if extra else ''}"


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


def _parts(rel: str) -> list:
    return str(rel).split("/")


def _check_path(root: Path, space, rel, what: str, source: bool) -> list:
    """The parts of a move's path when it is one the librarian may touch, else UnsafeMove:
    inside the space, under knowledge/ or work/, under one of the company's kinds (a source may
    also be a loose page right under knowledge/ or a desk folder right under work/), never a
    path the gate guards, never through a link."""
    try:
        pages.confine(root, rel, zones=[f"{z}/" for z in ZONES], guarded=True, what=what)
    except pages.UnsafePath as exc:
        raise UnsafeMove(str(exc)) from exc
    parts = _parts(rel)
    kinds = [k for k in space.kinds if k not in spaceyaml.FIXED_KINDS]
    if len(parts) >= 3 and parts[1] in kinds:
        return parts
    if source and len(parts) == 2 and parts[0] == "knowledge" and parts[1].endswith(".md"):
        return parts   # a loose page right under knowledge/
    if source and len(parts) == 2 and parts[0] == "work" and parts[1] not in space.all_kinds:
        return parts   # a desk folder outside every kind
    raise UnsafeMove(f"{what} {rel!r} is not under one of the company's kinds in knowledge/ or work/")


def _tree(path: Path, root: Path | None = None) -> list:
    """Every file and folder at and under `path`, never following a link; UnsafeMove when one
    is a link (a link moved whole could carry a folder outside the space in). The message names
    the link relative to the space, so log.md never carries a folder of this machine."""
    found = []
    for base, dirs, files in os.walk(path, followlinks=False) if path.is_dir() else [(str(path.parent), [], [path.name])]:
        for name in dirs + files:
            entry = Path(base) / name
            if pages.is_link(entry):
                try:
                    shown = entry.relative_to(root).as_posix() if root is not None else entry.name
                except ValueError:
                    shown = entry.name
                raise UnsafeMove(f"{shown} is a link; the librarian never moves one")
            found.append(entry)
    return found


def _hub(path: Path) -> bool:
    """A subject's generated hub (`knowledge/<kind>/<id>/index.md`, written by status.py), which
    the next status run rewrites; a hand-written index.md is not one."""
    if path.name != "index.md" or path.parent.parent.parent.name != "knowledge" \
            or pages.is_link(path) or not path.is_file():
        return False
    return f"Hub for subject `{path.parent.name}`" in path.read_text(encoding="utf-8", errors="replace")


def _collision(source: Path, target: Path) -> Path | None:
    """The first file the move would overwrite: a folder merges into one that exists, a file
    never replaces one, except one generated hub onto another (the source's is dropped)."""
    if not target.exists():
        return None
    if _hub(source) and _hub(target):
        return None
    if not (source.is_dir() and target.is_dir()):
        return target
    for child in sorted(source.iterdir()):
        hit = _collision(child, target / child.name)
        if hit is not None:
            return hit
    return None


def _stable_pages(path: Path) -> list:
    """(the page's path, its `subject`) of every signed page at or under `path`."""
    out = []
    for entry in ([path] if path.is_file() else [p for p in path.rglob("*.md")] if path.is_dir() else []):
        if entry.suffix != ".md" or not entry.is_file():
            continue
        try:
            fm, _ = pages.read(entry)
        except Exception:
            continue
        if fm.get("status") == "stable":
            out.append((entry, str(fm.get("subject") or "")))
    return out


def check_moves(root, space, change: Change) -> None:
    """Refuse (UnsafeMove) a change any of whose moves the librarian would never make, before
    anything is touched. A move stays in its zone; it never writes a guarded path, never goes
    through or moves a link, and never overwrites a file; a signed page moves only within its
    subject's folder (a kind move), to the subject its `subject` names, or with the merge whose
    registry edit retires the subject it leaves."""
    root = Path(root)
    merged = {str(e.get("id")): str((e.get("fields") or {}).get("merged_into"))
              for e in change.registry if e.get("op") == "set" and (e.get("fields") or {}).get("merged_into")}
    for move in change.moves:
        if not (isinstance(move, (list, tuple)) and len(move) == 2):
            raise UnsafeMove(f"a move is a pair of paths (got {move!r})")
        src, dst = move
        sp = _check_path(root, space, src, "the source", source=True)
        dp = _check_path(root, space, dst, "the destination", source=False)
        if sp[0] != dp[0]:
            raise UnsafeMove(f"{src!r} -> {dst!r} leaves its zone ({sp[0]}/)")
        source, target = root / src, root / dst
        if not source.exists():
            continue   # _do skips it too
        _tree(source, root)
        hit = _collision(source, target)
        if hit is not None:
            raise UnsafeMove(f"{src!r} -> {dst!r} would overwrite {hit.relative_to(root).as_posix()}")
        here = sp[2] if len(sp) >= 3 else None
        there = dp[2]
        for page, subject in _stable_pages(source):
            if there in (here, subject) or (here and merged.get(here) == there):
                continue
            raise UnsafeMove(f"{page.relative_to(root).as_posix()} is signed for {subject or here or 'no subject'}; "
                             f"it never moves to {there}")


def _check_registry(edits) -> None:
    """Refuse a registry edit the finders never write (UnsafeMove)."""
    for edit in edits or []:
        if not isinstance(edit, dict) or edit.get("op") not in ("add", "set", "alias"):
            raise UnsafeMove(f"a registry edit is add, set or alias (got {edit!r})")
        if not (isinstance(edit.get("id"), str) and SLUG.match(edit["id"])):
            raise UnsafeMove(f"a registry edit names a subject id (got {edit.get('id')!r})")
        if edit["op"] == "add" and (set(edit) - ADD_KEYS):
            raise UnsafeMove(f"adding {edit['id']} names {', '.join(sorted(set(edit) - ADD_KEYS))}")
        if edit["op"] == "set" and (not isinstance(edit.get("fields"), dict) or set(edit["fields"]) - SET_FIELDS):
            raise UnsafeMove(f"setting {edit['id']} may name only {', '.join(sorted(SET_FIELDS))}")


def _move(root: Path, src: str, dst: str, moved: list | None = None) -> None:
    """Move src to dst, merging into a folder that exists; each (src, dst) actually moved is
    appended to `moved`, so an undo moves back exactly those and never the target's own files."""
    source, target = root / src, root / dst
    if pages.is_link(source):
        raise UnsafeMove(f"{src} is a link; the librarian never moves one")
    if target.exists() and source.is_dir() and target.is_dir():
        for child in sorted(source.iterdir()):
            _move(root, f"{src}/{child.name}", f"{dst}/{child.name}", moved)
        if not any(source.iterdir()):
            source.rmdir()
        return
    if _hub(source) and _hub(target):   # the next status run rewrites the target's hub
        source.unlink()
        return
    if target.exists():   # checked before the change began; never overwritten on the way
        raise UnsafeMove(f"{dst} exists; the librarian never overwrites a file")
    pages.check_no_links(target, root=root)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    if moved is not None:
        moved.append((src, dst))


def _do(root: Path, space, change: Change) -> list:
    """Make the change; the (src, dst) pairs actually moved, for `_undo`."""
    _check_registry(change.registry)
    check_moves(root, space, change)
    moved: list = []
    for src, dst in change.moves:
        if (root / src).exists():
            _move(root, src, dst, moved)
    if change.registry:
        reg = registry_mod.Registry(root, space)
        _edit_registry(reg, change.registry)
        reg.save()
    return moved


def _undo(root: Path, change: Change, snap: dict) -> None:
    for src, dst in reversed(snap.get("moved") or []):
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


def _load(path: Path) -> dict:
    """A state file as a dict; {} when it is missing or not a mapping."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None
    return data if isinstance(data, dict) else {}


def _ask(root: Path, space, change: Change, moved: dict, now, report: Report, key: str) -> None:
    asked = yaml.safe_load((root / ASKED).read_text(encoding="utf-8")) if (root / ASKED).exists() else None
    asked = asked if isinstance(asked, dict) else {}
    stored = root / STATE / f"{key}.yaml"
    if key in asked:   # asked once, unless the change itself moved since (same id, other edits)
        held = _load(stored)
        entry = asked[key] if isinstance(asked[key], dict) else {}
        was = entry.get("change", held.get("change") if held.get("status") == "asked" else None)
        if was == change.to_dict():
            return
    # answer() reads the change back from here, so it always holds what is asked
    pages.write_text(stored, yaml.safe_dump({"id": key, "status": "asked", "at": pages.iso(now),
                                             "change": change.to_dict()}, sort_keys=False, allow_unicode=True),
                     root=root)
    people = sorted({p for move in moved.values() for p in move[:2] if p})
    lines = "; ".join(f"{sid}: {m[0] or 'the space owner'} -> {m[1] or 'the space owner'}"
                      + (f", {m[2]} -> {m[3]}" if len(m) > 2 else "") for sid, m in moved.items())
    classes = [m[2:] for m in moved.values() if len(m) > 2]
    recommendation = (f"no, unless {classes[0][0]} content may be shared as {classes[0][1]}"
                      if classes else "no, unless both owners agree")
    exact = _exact(change)
    qpath = root / "_inbound" / "questions" / f"librarian-{key}.md"
    fm = {"id": "p-" + key[-8:], "type": "review", "title": f"Ownership would move: {_one_line(change.summary)}",
          "status": "draft", "generated": pages.generated("librarian", now),
          "question": f"{_one_line(change.summary)}. That would move ownership ({lines}). The exact change: "
                      f"{'; '.join(exact)}. Make the change?",
          "recommendation": recommendation, "default": "nothing moves",
          "for": people, "depends": 1}
    listed = "\n".join(f"- {line}" for line in exact)
    pages.write(qpath, fm, f"# Ownership would move\n\n{_one_line(change.summary)}.\n\nOwners before and after: "
                           f"{lines}.\n\nThe exact change, all of it:\n\n{listed}\n\n"
                           "Nothing moves until the owners answer.\n")
    asked[key] = {"at": pages.iso(now), "question": qpath.relative_to(root).as_posix(), "owners": people,
                  "change": change.to_dict()}
    pages.write_text(root / ASKED, yaml.safe_dump(asked, sort_keys=False), root=root)
    report.asked.append(key)


def _exact(change: Change) -> list:
    """Every move and registry edit of a change, one line each, as the question shows them."""
    out = [f"move {_one_line(src)} to {_one_line(dst)}" for src, dst in change.moves]
    for edit in change.registry:
        rest = {k: v for k, v in edit.items() if k not in ("op", "id")}
        out.append(_one_line(f"registry {edit.get('op')} {edit.get('id')}: {json.dumps(rest, sort_keys=True)}"))
    return out or ["nothing"]


def apply_change(root, change: Change, key: str, now=None, land_it: bool = False, scorer=None,
                 report: Report | None = None, runner=None, approved_by: str = "") -> str:
    """Act, then verify: 'applied', 'reverted' or 'asked'. With `approved_by`, the owner already
    answered yes to the ownership question, so the change is made (and still verified). A change
    the librarian would never make (`check_moves`, a registry edit no finder writes) raises
    UnsafeMove before anything is asked or touched."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    report = report or Report()
    scorer = scorer or score
    _check_registry(change.registry)
    check_moves(root, space, change)
    moved = ownership_moves(registry_mod.Registry(root, space), _registry_after(root, space, change), change)
    if not approved_by and moved:
        _ask(root, space, change, moved, now, report, key)
        return "asked"
    snap = _snapshot(root, change)
    before = scorer(root)
    snap["moved"] = _do(root, space, change)
    after = scorer(root)
    if _lower(before, after):
        _undo(root, change, snap)
        _append(root / "log.md", f"- {pages.iso(now)} librarian reverted {change.kind}: {change.summary} "
                                 f"(tests {before} -> {after})")
        report.reverted.append(key)
        _refused(root, change, key, f"tests {before} -> {after}", now, status="reverted")   # named on health
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


def _refused(root: Path, change: Change, key: str, exc, now, status: str = "refused", **extra) -> None:
    """Mark `key` refused (or reverted: the tests scored lower) with the reason, so it leaves
    `list` and the health line names it (status.py `_librarian_cell`) until the tree lets it
    through (`_propose` reopens a refused one) or the finder stops making it (`run` closes it)."""
    path = root / STATE / f"{key}.yaml"
    data = _load(path) or {"id": key, "change": change.to_dict()}
    data.update(status=status, reason=_one_line(exc), closed_at=pages.iso(now), **extra)
    pages.write_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True), root=root)


def _propose(root: Path, change: Change, now, report: Report, space=None) -> None:
    key = _pid(change.kind, change.summary)
    path = root / STATE / f"{key}.yaml"
    held = _load(path) if path.exists() else {}
    if held.get("change") == change.to_dict():
        if held.get("status") == "reverted":   # the tests said no; health names it, `apply` retries it
            report.reverted.append(key)
            return
        if held.get("status") != "refused":
            return   # already proposed (or asked, or closed) as it stands; a changed change is proposed again
        try:   # refused: proposed again once the tree lets it through (one copy renamed, say)
            check_moves(root, space or spaceyaml.load(root), change)
        except UnsafeMove:
            report.refused.append(key)
            return
    data = {"id": key, "status": "open", "at": pages.iso(now), "change": change.to_dict(),
            "review": "an independent second agent reviews this before `apply --reviewed-by`"}
    pages.write_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True), root=root)
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
            key = _pid(change.kind, change.summary)
            if at_once:
                try:
                    apply_change(root, change, key, now, land_it, scorer, report, runner)
                except UnsafeMove as exc:   # one refused move never stops the others
                    _append(root / "log.md", f"- {pages.iso(now)} librarian refused {change.kind}: "
                                             f"{change.summary} ({exc})")
                    report.refused.append(key)
                    _refused(root, change, key, exc, now)
            else:
                _propose(root, change, now, report, space)
    reg = registry_mod.Registry(root, space)
    desk_props = stray_desk(root, space, reg)[1]
    for change in desk_props + splits(root, space, reg) + merges(reg) + umbrellas(reg):
        _propose(root, change, now, report, space)
    for path in sorted((root / STATE).glob("lib-*.yaml")) if (root / STATE).is_dir() else []:
        data = _load(path)
        held = {"refused": report.refused, "reverted": report.reverted}.get(data.get("status"))
        if held is not None and path.stem not in held:   # let through, applied since, or moved past
            data["status"] = "closed"
            pages.write_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True), root=root)
    if at_once:
        report.migrations = migrate(root, now=now, land_it=land_it, scorer=scorer, runner=runner)
    return report


def _current(root: Path, space) -> dict:
    """id -> Change of every change the librarian's finders make on the tree as it is now."""
    reg = registry_mod.Registry(root, space)
    desk_moves, desk_props = stray_desk(root, space, reg)
    found = (kind_moves(root, space, reg) + stray_pages(root, space, reg) + desk_moves + desk_props
             + splits(root, space, reg) + merges(reg) + umbrellas(reg))
    return {_pid(c.kind, c.summary): c for c in found}


def _stored(root: Path, key: str, *wanted: str) -> tuple:
    """(path, data, change) of a proposal file in one of the states `wanted`, read back only when
    the librarian makes the same change on the current tree: the same id, kind, moves and
    registry edits. A file written by hand, or one the tree has moved past, is refused."""
    if not isinstance(key, str) or not KEY.match(key):
        raise ValueError(f"{key!r} is not a proposal id (lib- and eight hex digits)")
    path = root / STATE / f"{key}.yaml"
    pages.check_no_links(path, root=root)
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.is_file() else {}
    if not isinstance(data, dict) or data.get("status") not in wanted or not isinstance(data.get("change"), dict):
        raise ValueError(f"{key} is not an {' or '.join(wanted)} proposal")
    change = Change.from_dict(data["change"])
    made = _current(root, spaceyaml.load(root)).get(key)
    if made is None or made.kind != change.kind or [list(m) for m in made.moves] != [list(m) for m in change.moves] \
            or made.registry != change.registry:
        raise ValueError(f"{key} is not a change the librarian makes on the tree as it is now; "
                         "run `librarian.py scan` again and review what it proposes")
    return path, data, change


def _person_only(what: str) -> None:
    """ValueError when this process runs inside a routine or a cloud run (`routine.in_routine`)."""
    import routine   # here and not at the top: only the person's verbs ask it
    running = routine.in_routine()
    if running:
        raise ValueError(f"{what} is the person's, and {running}; routines never act on it (rules.md section 1)")


def apply(root, key: str, reviewed_by: str, now=None, land_it: bool = False, scorer=None, runner=None) -> str:
    """Apply one reviewed proposal; refused without a named reviewer, and refused for any
    proposal the librarian does not make on the current tree (`_stored`). The reviewer's name
    is a convention the status skill keeps, not a proof: what keeps the change safe is that it
    is the librarian's own, checked move by move (`check_moves`, `_check_registry`), and that a
    change of owner, or content crossing into a subject of another owner or classification
    (`ownership_moves`: a merge among them), still waits for the owners' yes (`_ask`). That is the basis on which the
    mechanical moves are made at once inside a routine (`run`), so a routine applies a proposal
    too, after a second agent's review (the daily chain): a stale or hand-written proposal is
    refused there as anywhere. An owner's yes (`answer`) stays the person's."""
    root = Path(root)
    who = str(reviewed_by or "").strip()
    if not who:
        raise ValueError("a judgment change needs an independent review: give --reviewed-by")
    if who.lower() in ("librarian", "status", "routine") or who.lower().startswith("routine:"):
        raise ValueError("the reviewer is never the routine that proposed the change")
    path, data, change = _stored(root, key, "open", "reverted")   # reverted: tried again once the tests let it
    if not change.moves and not change.registry:
        raise ValueError(f"{key} names no change to apply; file it by hand on the person's word")
    report = Report()
    now = now or spaceyaml.load(root).now()
    try:
        outcome = apply_change(root, change, key, now, land_it, scorer, report, runner)
    except UnsafeMove as exc:   # never re-reviewed daily: the health line names it until the tree changes
        _refused(root, change, key, exc, now, reviewed_by=reviewed_by)
        raise
    data.update(_load(path))   # a revert wrote its reason there
    data.update(status=outcome, reviewed_by=reviewed_by, closed_at=pages.iso(now))
    pages.write_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True), root=root)
    return outcome


def _unask(root: Path, key: str) -> None:
    """Forget that `key` was asked: drop it from asked.yaml and reopen a proposal still marked
    asked, so a yes that could not be applied leads to a fresh question, never to none."""
    if not (isinstance(key, str) and KEY.match(key)):
        return
    asked = _load(root / ASKED)
    if asked.pop(key, None) is not None:
        pages.write_text(root / ASKED, yaml.safe_dump(asked, sort_keys=False), root=root)
    stored = root / STATE / f"{key}.yaml"
    data = _load(stored)
    if data.get("status") == "asked":
        data["status"] = "open"
        pages.write_text(stored, yaml.safe_dump(data, sort_keys=False, allow_unicode=True), root=root)


def answer(root, key: str, by: str, now=None) -> str:
    """The owner's yes to an ownership question (`librarian-<key>.md`): make the asked change,
    only when it is still the change the librarian makes on the current tree (`_stored`). Refused
    inside a routine or a cloud run (`routine.in_routine`), as `apply` is."""
    _person_only("an owner's yes")
    root = Path(root)
    try:
        path, data, change = _stored(root, key, "asked")
        outcome = apply_change(root, change, key, now, approved_by=by)
    except Exception:
        _unask(root, key)   # sign.py drops the question, so the next apply asks afresh
        raise
    if outcome != "applied":
        _unask(root, key)   # reverted: the retry (apply, or the next at-once run) asks the owners afresh
    data.update(_load(path))   # a revert wrote its reason there
    data.update(status=outcome, approved_by=by, closed_at=pages.iso(now or spaceyaml.load(root).now()))
    pages.write_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True), root=root)
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
