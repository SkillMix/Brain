"""Answer and leak tests: what a space must answer, and what an audience must not find.

Questions are data. `_meta/tests/answers.yaml` lists the questions this space must be
able to answer, the facts an answer must contain and who asks; `_meta/tests/leaks.yaml`
lists what an audience must never be able to read. Both run in the daily chain and after
every machinery or structural change; every run appends one line to
`_meta/state/tests.jsonl`.

A question runs in the asker's space: here when the asker is this space (its name, its
owner, the directory name, or `owner`/`anyone`), in the named root when `roots` maps the
asker, otherwise it is skipped with a note. A leak's `audience_root` is a name in `roots`,
a mount, or a path relative to this space; one that resolves nowhere is skipped, never
silently passed. The leak scan reads content only: not the machinery, not the files that
name the terms (`space.yaml`, `rules.md`, `AGENTS.md`), not the two logs, not private zones.

    python tests_run.py [root] [answers|leaks|all|probe] [--root name=path ...]
                        [--answers <file>] [--leaks <file>]

`probe` runs every answer question by every method (walk, search, decisions) and records
recall per method and per kind of question (`kind: walk | status | decision`) in
`_meta/state/probe.jsonl`; the librarian runs it once a scaled week.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import registry as registry_mod  # noqa: E402
import spaceyaml  # noqa: E402

ANSWERS_PATH = Path("_meta") / "tests" / "answers.yaml"
LEAKS_PATH = Path("_meta") / "tests" / "leaks.yaml"
TESTS_LOG = Path("_meta") / "state" / "tests.jsonl"
PROBE_LOG = Path("_meta") / "state" / "probe.jsonl"
METHODS = ("walk", "search", "decisions")
KINDS = ("walk", "status", "decision")
IDS_PATH = Path("_meta") / "state" / "ids.yaml"
SEARCH_ZONES = ("knowledge", "live", "work")
MOUNT_ZONES = ("knowledge", "live")
VIAS = ("walk", "search", "decisions", "both")
SKIP_DIR_NAMES = {".git", "__pycache__", ".pytest_cache"}
PRIVATE_DIRS = ("personal", "academic", "_scratch", "_meta/state/feed")
# The leak scan reads content, never the machinery or the files that name the terms.
SKIP_RELATIVE = ("_meta/tests", "_meta/tools", "_meta/templates", ".claude", ".github", "docs")
SKIP_FILES = ("space.yaml", "rules.md", "AGENTS.md",
              "_meta/state/tests.jsonl", "_meta/state/runs.jsonl")
ANYONE = {"", "owner", "anyone", "everyone", "me"}


@dataclass
class Report:
    kind: str
    passed: int = 0
    failed: int = 0
    details: list = field(default_factory=list)
    skipped: list = field(default_factory=list)


# ----- shared helpers ----------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _read(path: Path, strict: bool = False) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="strict" if strict else "replace")
    except (UnicodeDecodeError, OSError):
        return None


def _rel(root: Path, path: Path, prefix: str = "") -> str:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.as_posix()
    return f"{prefix}:{rel}" if prefix else rel


def carries(text: str, needle) -> bool:
    """A leak needle found as a whole word or phrase, ignoring case: a name never matches
    inside another word ("Ben" is not in "beneath"), a number never inside a longer one."""
    key = str(needle).strip().lower()
    if not key:
        return False
    return re.search(r"(?<![^\W_])" + re.escape(key) + r"(?![^\W_])", text.lower()) is not None


def _found(texts: dict, expect: list) -> tuple[list, list]:
    """Which files carry each expected substring (case-insensitive), and what is missing."""
    lowered = {rel: text.lower() for rel, text in texts.items()}
    found_in: list = []
    missing: list = []
    for needle in expect:
        key = str(needle).lower()
        hits = [rel for rel, text in lowered.items() if key in text]
        if not hits:
            missing.append(str(needle))
        for rel in hits:
            if rel not in found_in:
                found_in.append(rel)
    return found_in, missing


def _append_line(root: Path, now: dt.datetime, report: Report) -> None:
    path = root / TESTS_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"at": pages.iso(now), "kind": report.kind, "passed": report.passed,
            "failed": report.failed, "skipped": len(report.skipped), "details": report.details}
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


# ----- answers -----------------------------------------------------------------------

def _ids_of(root: Path) -> dict:
    """id -> relative path: the generated index when present, else a scan of the pages."""
    index = root / IDS_PATH
    if index.exists():
        data = _load_yaml(index)
        table = data.get("ids") if isinstance(data.get("ids"), dict) else data
        return {str(k): str(v) for k, v in table.items() if isinstance(v, str)}
    found: dict = {}
    for zone in ("knowledge", "live", "_inbound/candidates"):
        base = root / zone
        if not base.is_dir():
            continue
        for path in base.rglob("*.md"):
            if not pages.is_page_path(path):
                continue
            try:
                fm, _ = pages.read(path)
            except Exception:
                continue
            if fm.get("id"):
                found.setdefault(str(fm["id"]), path.relative_to(root).as_posix())
    return found


def _resolve_id(space: spaceyaml.Space, pid: str, ids_cache: dict) -> tuple[Path, str, str] | None:
    """(root, relpath, prefix) of the page carrying `pid`, here first, then in each mount."""
    candidates = [(space.root, "")]
    for mount in space.mounts:
        mount_root = space.mount_root(mount.get("name", ""))
        if mount_root is not None:
            candidates.append((mount_root, str(mount.get("name"))))
    for root, prefix in candidates:
        key = str(root)
        if key not in ids_cache:
            ids_cache[key] = _ids_of(root)
        rel = ids_cache[key].get(pid)
        if rel and (root / rel).exists():
            return root, rel, prefix
    return None


def _walk_texts(space: spaceyaml.Space, reg: registry_mod.Registry, subject: str) -> dict:
    """The subject's folder under its kind, its status page, and the pages its hub links."""
    root = space.root
    texts: dict = {}
    found = reg.get(subject) or reg.find(subject)
    sid = found.id if found else subject
    kinds = [found.kind] if found else list(space.all_kinds)
    hubs: list = []
    for kind in kinds:
        folder = root / "knowledge" / kind / sid
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.md")):
            text = _read(path)
            if text is not None:
                texts[_rel(root, path)] = text
            if path.name == "index.md":
                hubs.append(text or "")
    status_page = root / "live" / "subjects" / f"{sid}.md"
    if status_page.exists():
        texts[_rel(root, status_page)] = _read(status_page) or ""
    # the subject's home, mounted here (a crew member's workspace): its status page, its signed
    # pages and its candidates are the first answer, whatever this space has gathered so far
    home = reg.home_of(sid) if found else None
    home_root = space.mount_root(home) if home and home != space.name else None
    if home_root is not None:
        paths = [home_root / "live" / "subjects" / f"{sid}.md"]
        for kind in kinds:
            folder = home_root / "knowledge" / kind / sid
            paths += sorted(folder.rglob("*.md")) if folder.is_dir() else []
        for path in sorted((home_root / "_inbound" / "candidates").glob("*.md")):
            try:
                if str(pages.read(path)[0].get("subject") or "") == sid:
                    paths.append(path)
            except Exception:
                continue
        for path in paths:
            text = _read(path) if path.exists() else None
            if text is not None:
                texts[_rel(home_root, path, home)] = text
    ids_cache: dict = {}
    for hub in hubs:
        for pid in pages.id_links(hub):
            hit = _resolve_id(space, pid, ids_cache)
            if hit is None:
                continue
            page_root, rel, prefix = hit
            label = f"{prefix}:{rel}" if prefix else rel
            if label not in texts:
                texts[label] = _read(page_root / rel) or ""
    return texts


def _search_texts(space: spaceyaml.Space) -> dict:
    """Every markdown page under knowledge/, live/, work/, plus the mounts' knowledge/ and live/."""
    texts: dict = {}
    targets = [(space.root, "", SEARCH_ZONES)]
    for mount in space.mounts:
        mount_root = space.mount_root(mount.get("name", ""))
        if mount_root is not None:
            targets.append((mount_root, str(mount.get("name")), MOUNT_ZONES))
    for root, prefix, zones in targets:
        for zone in zones:
            base = root / zone
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*.md")):
                if any(part in SKIP_DIR_NAMES for part in path.parts):
                    continue
                text = _read(path)
                if text is not None:
                    texts[_rel(root, path, prefix)] = text
    return texts


def _decision_texts(space: spaceyaml.Space, subject: str, question: str) -> dict | None:
    """The decisions index's answer texts, or None when the index tool is not present yet."""
    try:
        import decisions  # noqa: WPS433 (a sibling tool, optional until it ships)
    except ImportError:
        return None
    texts = getattr(decisions, "texts", None)
    return texts(space, subject, question) if texts is not None else None


def _texts_by(space: spaceyaml.Space, reg, subject: str, method: str, question: str) -> dict | None:
    if method == "walk":
        return _walk_texts(space, reg, subject)
    if method == "search":
        return _search_texts(space)
    return _decision_texts(space, subject, question)


def _asks_here(root: Path, space: spaceyaml.Space, asker: str) -> bool:
    key = asker.strip().lower()
    if key in ANYONE:
        return True
    return key in {space.name.lower(), space.owner.lower(), root.name.lower()} - {""}


def _answer_one(target: Path, question: dict) -> dict:
    space = spaceyaml.load(target)
    reg = registry_mod.load(target, space)
    subject = str(question.get("subject") or "")
    expect = [str(e) for e in (question.get("expect") or [])]
    via = str(question.get("via") or "both")
    if via not in VIAS:
        via = "both"
    found_in: list = []
    missing: list = expect
    for method in (("walk", "search") if via == "both" else (via,)):
        texts = _texts_by(space, reg, subject, method, str(question.get("question") or "")) or {}
        found_in, missing = _found(texts, expect)
        if not missing:
            break
    return {"question": str(question.get("question") or ""), "subject": subject, "via": via,
            "asked_in": target.name, "ok": not missing and bool(expect),
            "found_in": found_in, "missing": missing}


def missing_facts(root, question: dict) -> list:
    """The expected facts of one question that no page it would read here carries."""
    return _answer_one(Path(root), question)["missing"]


def answers(root, path=None, now: dt.datetime | None = None, roots: dict | None = None,
            record: bool = True) -> Report:
    """Run the answer tests. `roots` maps asker names to the spaces where they ask."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    path = Path(path) if path else root / ANSWERS_PATH
    report = Report("answers")
    if not path.exists():
        return report
    roots = {str(k): Path(v) for k, v in (roots or {}).items()}
    for question in _load_yaml(path).get("questions") or []:
        if not isinstance(question, dict):
            continue
        asker = str(question.get("asker") or "")
        if asker in roots:
            target = roots[asker]
        elif _asks_here(root, space, asker):
            target = root
        else:
            report.skipped.append(f"{question.get('question')}: asker `{asker}` is not this space")
            continue
        detail = _answer_one(target, question)
        report.details.append(detail)
        if detail["ok"]:
            report.passed += 1
        else:
            report.failed += 1
    if record:
        _append_line(root, now, report)
    return report


# ----- the probe ---------------------------------------------------------------------

@dataclass
class ProbeReport:
    kinds: dict = field(default_factory=dict)      # kind -> method -> [passed, total] | None
    questions: list = field(default_factory=list)
    skipped: list = field(default_factory=list)


def probe(root, path=None, now: dt.datetime | None = None, roots: dict | None = None) -> ProbeReport:
    """Every answer question by every method: recall per method and per kind of question."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    path = Path(path) if path else root / ANSWERS_PATH
    report = ProbeReport()
    if not path.exists():
        return report
    roots = {str(k): Path(v) for k, v in (roots or {}).items()}
    for question in _load_yaml(path).get("questions") or []:
        if not isinstance(question, dict):
            continue
        asker = str(question.get("asker") or "")
        target = roots.get(asker) or (root if _asks_here(root, space, asker) else None)
        if target is None:
            report.skipped.append(str(question.get("question")))
            continue
        tspace = spaceyaml.load(target)
        reg = registry_mod.load(target, tspace)
        kind = str(question.get("kind") or "walk")
        kind = kind if kind in KINDS else "walk"
        expect = [str(e) for e in (question.get("expect") or [])]
        subject = str(question.get("subject") or "")
        row = {"question": str(question.get("question") or ""), "kind": kind, "methods": {}}
        bucket = report.kinds.setdefault(kind, {m: [0, 0] for m in METHODS})
        for method in METHODS:
            texts = _texts_by(tspace, reg, subject, method, row["question"])
            if texts is None:
                bucket[method] = None
                row["methods"][method] = None
                continue
            _, missing = _found(texts, expect)
            ok = bool(expect) and not missing
            row["methods"][method] = ok
            if bucket[method] is not None:
                bucket[method][0] += int(ok)
                bucket[method][1] += 1
        report.questions.append(row)
    log = root / PROBE_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"at": pages.iso(now), "kinds": report.kinds}) + "\n")
    return report


def last_probe(root) -> dict | None:
    """The newest probe line of a space, or None."""
    path = Path(root) / PROBE_LOG
    if not path.exists():
        return None
    last = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("kinds"), dict):
            last = row
    return last


def probe_summary(root) -> dict | None:
    """method -> [passed, total] over every kind of the newest probe (None: not measured)."""
    row = last_probe(root)
    if row is None or not row["kinds"]:
        return None
    out: dict = {m: None for m in METHODS}
    for per_method in row["kinds"].values():
        for method in METHODS:
            cell = (per_method or {}).get(method)
            if cell is None:
                continue
            total = out[method] or [0, 0]
            out[method] = [total[0] + int(cell[0]), total[1] + int(cell[1])]
    return out


# ----- leaks -------------------------------------------------------------------------

def _ignored_dirs(target: Path) -> tuple[set, set]:
    """Directory names and anchored paths git would ignore here (the template's private zones)."""
    names, anchored = set(), set()
    patterns = list(PRIVATE_DIRS)
    gitignore = target / ".gitignore"
    if gitignore.exists():
        for line in (_read(gitignore) or "").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and line.endswith("/") and "*" not in line:
                patterns.append(line)
    for pattern in patterns:
        clean = pattern.strip("/")
        (anchored if "/" in clean else names).add(clean)
    return names, anchored


def _mount_paths(target: Path) -> set:
    """The target's own mounts: other spaces' checkouts inside it, never its own content."""
    out = set()
    modules = target / ".gitmodules"
    if modules.exists():
        for line in modules.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.strip().partition("=")
            if key.strip() == "path" and value.strip():
                out.add(value.strip().replace("\\", "/").strip("/"))
    try:
        mounts = spaceyaml.load(target).mounts
    except Exception:
        mounts = []
    for mount in mounts:
        rel = str(mount.get("path") or "").replace("\\", "/").strip("/")
        if rel and not rel.startswith("..") and not Path(rel).is_absolute():
            out.add(rel)
    return out


def _tracked_files(target: Path):
    names, anchored = _ignored_dirs(target)
    anchored = set(anchored) | _mount_paths(target)
    for dirpath, dirnames, filenames in os.walk(target):
        here = Path(dirpath)
        rel_dir = here.relative_to(target).as_posix() if here != target else ""
        keep = []
        for name in sorted(dirnames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if name in SKIP_DIR_NAMES or name in names or rel in anchored:
                continue
            if any(rel == skip or rel.startswith(skip + "/") for skip in SKIP_RELATIVE):
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if rel not in SKIP_FILES:
                yield here / name


def _resolve_root(root: Path, space: spaceyaml.Space, name: str, roots: dict) -> Path | None:
    if name in roots:
        return roots[name]
    if name in ("", ".", "self") or name == space.name or name == root.name:
        return root
    mounted = space.mount_root(name)
    if mounted is not None:
        return mounted
    candidate = root / name
    return candidate if candidate.is_dir() else None


def leaks(root, roots: dict | None = None, path=None, now: dt.datetime | None = None,
          record: bool = True) -> Report:
    """Run the leak tests: grep every tracked text file under each audience root."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    path = Path(path) if path else root / LEAKS_PATH
    report = Report("leaks")
    if not path.exists():
        return report
    roots = {str(k): Path(v) for k, v in (roots or {}).items()}
    for leak in _load_yaml(path).get("leaks") or []:
        if not isinstance(leak, dict):
            continue
        name = str(leak.get("audience_root") or "")
        needles = [str(n) for n in (leak.get("must_not_find") or [])]
        if isinstance(leak.get("request"), dict):  # an asker must not get it by asking
            detail = _request_leak(root, space, leak, needles, roots, now)
            if detail is None:
                report.skipped.append(f"{name}: the request's holder is not reachable here")
                continue
            report.details.append(detail)
            report.passed += int(detail["ok"])
            report.failed += int(not detail["ok"])
            continue
        target = _resolve_root(root, space, name, roots)
        if target is None:
            report.skipped.append(f"{name}: audience root not found here")
            continue
        hits: list = []
        for file in _tracked_files(target):
            text = _read(file, strict=True)
            if text is None:
                continue
            found = [n for n in needles if carries(text, n)]
            if found:
                hits.append({"file": _rel(target, file, target.name), "found": found})
        detail = {"audience_root": name, "root": str(target), "reason": str(leak.get("reason") or ""),
                  "must_not_find": needles, "ok": not hits, "hits": hits}
        report.details.append(detail)
        if hits:
            report.failed += 1
        else:
            report.passed += 1
    if record:
        _append_line(root, now, report)
    return report


def _request_leak(root: Path, space, leak: dict, needles: list, roots: dict, now) -> dict | None:
    """Render the holder's covered answer to the asker's request and look for the phrases."""
    try:
        import request as rq
    except ImportError:
        return None
    req = dict(leak["request"])
    holder = _resolve_root(root, space, str(req.get("holder") or ""), roots)
    if holder is None:
        return None
    asker_root = _resolve_root(root, space, str(req.get("asker") or ""), roots)
    asker = spaceyaml.load(asker_root) if asker_root is not None else None
    req.setdefault("from", asker.name if asker else str(req.get("asker")))
    hspace = spaceyaml.load(holder)
    choice = rq.choice_for(hspace, req["from"], asker.profile if asker else "", str(req.get("class")))
    lines, _ = rq.render(holder, req, now)
    sent = "\n".join(lines) if choice == "at_once" else ""
    found = [n for n in needles if carries(sent, n)]
    return {"audience_root": str(leak.get("audience_root")), "root": str(holder),
            "reason": str(leak.get("reason") or ""), "must_not_find": needles, "ok": not found,
            "hits": [{"file": f"request to {req.get('holder')}", "found": found}] if found else [],
            "request": req.get("question")}


# ----- CLI ---------------------------------------------------------------------------

def _print(report: Report) -> None:
    print(f"{report.kind}: {report.passed} passed, {report.failed} failed, "
          f"{len(report.skipped)} skipped")
    for detail in report.details:
        if detail["ok"]:
            continue
        if report.kind == "answers":
            print(f"  FAIL {detail['question']} ({detail['via']}, in {detail['asked_in']}): "
                  f"missing {detail['missing']}")
        else:
            for hit in detail["hits"]:
                print(f"  LEAK {detail['audience_root']}: {hit['file']} carries {hit['found']} "
                      f"({detail['reason']})")
    for note in report.skipped:
        print(f"  skipped: {note}")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    roots: dict = {}
    files = {"answers": None, "leaks": None}
    positional: list = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--root" and i + 1 < len(argv) and "=" in argv[i + 1]:
            name, _, value = argv[i + 1].partition("=")
            roots[name] = Path(value)
            i += 2
        elif arg in ("--answers", "--leaks") and i + 1 < len(argv):
            files[arg[2:]] = Path(argv[i + 1])
            i += 2
        elif arg.startswith("--"):
            print(__doc__)
            return 2
        else:
            positional.append(arg)
            i += 1
    kind = "all"
    if positional and positional[-1] in ("answers", "leaks", "all", "probe"):
        kind = positional.pop()
    root = Path(positional[0]) if positional else Path(".")
    if kind == "probe":
        report = probe(root, path=files["answers"], roots=roots)
        for qkind, cells in sorted(report.kinds.items()):
            shown = ", ".join(f"{m} {c[0]}/{c[1]}" if c is not None else f"{m} n/a"
                              for m, c in cells.items())
            print(f"probe {qkind}: {shown}")
        return 0
    failed = 0
    if kind in ("answers", "all"):
        report = answers(root, path=files["answers"], roots=roots)
        _print(report)
        failed += report.failed
    if kind in ("leaks", "all"):
        report = leaks(root, roots=roots, path=files["leaks"])
        _print(report)
        failed += report.failed
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
