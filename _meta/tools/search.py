"""Search: the second path to a subject, plain by default, an index only when chosen (shape 1).

Walking needs no tools; search is faster when it runs. Every space can search its own
`knowledge/` and `live/` (and a personal space its `work/`, its daily notes in `journal/`, its
drafts in `_inbound/candidates/` and its private briefs in `_meta/state/private/briefs/`) and
each mount's own zones (`knowledge/` and `live/` unless its engine file says more) with plain
text; a hit expands to its subject and shows its tier (`ground.tier_of`). The engine file
`_meta/search.yaml` says whether anything more runs:

    engine: none            # none | index | <adapter>: an index is disposable, rebuilt from git
    version: null           # the adapter's pinned version, when one runs
    sources: [knowledge, live]
    freshness_hours: 24     # an index older than this is rebuilt before it answers
    link_step: off          # extras stay off until the probe shows a gain:
    atoms: off              #   `measured: [{feature, gain, at}]` records it
    measured: []

`engine: index` keeps `_meta/state/private/search-index.json` (term -> files; never staged, as it
holds words of the private briefs) for this space's own
audience only; `prune` drops entries whose files moved or vanished. Another engine is an
adapter module `search_<engine>.py` beside this one with `query(root, text)`, `build(root)`
and `prune(root)`; when it is absent the plain search answers.

`query --for <subject>` is the search for work in a linked codebase: rings 1 and 2 of
docs/design/2026-10-22-codebase-link-design.md only. Ring 1 is the subject, its parents and its
parts, read here and in their home; ring 2 is the brain. Every other hit is dropped.

`lines` reads the same layers and zones and prints every matching line with its line number
and `-C` lines of context (2 by default), grouped by file: what an agent reading the evidence
needs, in one call. It runs on ripgrep when `rg` is installed, else on plain Python with the
same output; ripgrep is taken from PATH by its absolute path only, never from the current
folder or a space (`routine.which`). The text is literal and case-insensitive; `--regex` makes it a pattern. At most
`--max` files (30) and ten matches a file are shown, and what is left out is counted.

    python search.py [root] query [--for <subject>] <text>
    python search.py [root] lines [--for <subject>] [-C <n>] [--max <n>] [--regex] <text>
    python search.py [root] build | prune | config
"""
from __future__ import annotations

import base64
import importlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ground  # noqa: E402
import pages  # noqa: E402
import registry  # noqa: E402
import spaceyaml  # noqa: E402

CONFIG = Path("_meta") / "search.yaml"
INDEX = Path("_meta") / "state" / "private" / "search-index.json"
DEFAULTS = {"engine": "none", "version": None, "sources": ["knowledge", "live"], "freshness_hours": 24,
            "link_step": "off", "atoms": "off", "measured": []}
EXTRAS = ("link_step", "atoms")
# A personal space's own layer: where its knowledge is written before anyone signs it.
PERSONAL_ZONES = ("work", "journal", "_inbound/candidates", "_meta/state/private/briefs")
PRIVATE_NOTES = ("journal/", "_meta/state/private/")   # ring 3 for a linked codebase: never in its search
SKIP_PARTS = {".git", "__pycache__", ".pytest_cache"}
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{1,}")


@dataclass
class Hit:
    layer: str      # "" for this space, else the mount's name
    path: str       # relative to that layer's root
    subject: str    # the subject the hit expands to ("" when none)
    line: str       # the first matching line
    tier: str = ""  # signed, system, pending, witness, reported or working; `ground.label`

    def to_dict(self) -> dict:
        return {"layer": self.layer, "path": self.path, "subject": self.subject, "line": self.line,
                "tier": self.tier}


def _on(value) -> bool:
    return value is True or str(value).strip().lower() in ("on", "true", "yes")


def config(root) -> dict:
    """The engine file merged over the defaults; a missing file is all defaults."""
    out = dict(DEFAULTS)
    path = Path(root) / CONFIG
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            out.update(data)
    out["engine"] = str(out.get("engine") or "none")
    return out


def errors(root) -> list:
    """What the validator refuses in the engine file: an extra on without a measured gain."""
    if not (Path(root) / CONFIG).exists():
        return []
    cfg = config(root)
    errs = []
    measured = {str(m.get("feature")) for m in cfg.get("measured") or []
                if isinstance(m, dict) and float(m.get("gain") or 0) > 0}
    for extra in EXTRAS:
        if _on(cfg.get(extra)) and extra not in measured:
            errs.append(f"_meta/search.yaml: {extra} is on without a measured probe gain "
                        f"(`measured: [{{feature: {extra}, gain, at}}]`)")
    if not isinstance(cfg.get("sources"), list):
        errs.append("_meta/search.yaml: sources must be a list")
    return errs


def subject_of(rel: str) -> str:
    """knowledge/<kind>/<subject>/... and live/subjects/<id>.md expand to their subject."""
    parts = rel.split("/")
    if len(parts) >= 4 and parts[0] in ("knowledge", "work") and parts[1] not in ("sources", "decisions"):
        return parts[2]
    if len(parts) == 3 and parts[0] == "live" and parts[1] == "subjects":
        return Path(parts[2]).stem
    return ""


def _zones(space: spaceyaml.Space, own: bool) -> list:
    zones = list(config(space.root).get("sources") or DEFAULTS["sources"])
    if own and space.profile == "personal":
        zones += [z for z in PERSONAL_ZONES if z not in zones]
    return zones


def tier(base: Path, rel: str) -> str:
    """The tier of a hit's file, with `checked, not signed` on a readable checked draft
    (`ground.label`); front matter that does not parse counts as none."""
    try:
        fm, body = pages.read(base / rel)
    except Exception:
        fm, body = {}, ""
    return ground.label(rel, fm if isinstance(fm, dict) else {}, body, base)


def _files(root: Path, zones: list):
    for zone in zones:
        base = root / zone
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            if SKIP_PARTS & set(path.parts):
                continue
            yield path


def _layers(root: Path) -> list:
    """(layer name, root, zones): this space first, then each mount on disk."""
    space = spaceyaml.load(root)
    out = [("", root, _zones(space, True))]
    for mount in space.mounts:
        name = str(mount.get("name", ""))
        mroot = space.mount_root(name)
        if mroot is not None:
            try:
                out.append((name, mroot, _zones(spaceyaml.load(mroot), False)))
            except Exception:
                out.append((name, mroot, list(DEFAULTS["sources"])))
    return out


def _first_line(text: str, needle: str) -> str:
    low = needle.lower()
    for line in text.splitlines():
        if low in line.lower():
            return line.strip()[:200]
    return ""


def plain(root, text: str, limit: int = 50) -> list:
    """Case-insensitive substring search over every layer the space may read."""
    root = Path(root)
    hits = []
    for layer, base, zones in _layers(root):
        for path in _files(base, zones):
            try:
                body = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            line = _first_line(body, text)
            if line:
                rel = path.relative_to(base).as_posix()
                hits.append(Hit(layer, rel, subject_of(rel), line))
                if len(hits) >= limit:
                    return hits
    return hits


# ----- lines: the matching lines with their context ---------------------------------
PER_FILE = 10       # matches shown a file; the rest are counted
LINE_WIDTH = 300    # a longer line is cut


@dataclass
class Found:
    layer: str      # "" for this space, else the mount's name
    path: str       # relative to that layer's root
    subject: str
    lines: list     # (line number, text, is a match), in order, context merged
    more: int = 0   # matches past the first PER_FILE
    tier: str = ""


def _ripgrep():
    """ripgrep's absolute path, or None: never an `rg.bat` in the current folder or in a space, where a record
    could have planted one (`routine.which`; finding 10)."""
    import routine
    return routine.which("rg")


def _cut(line: str) -> str:
    line = line.rstrip("\r\n").replace("\x00", "")   # a PDF extract's stray NUL byte
    return line if len(line) <= LINE_WIDTH else line[:LINE_WIDTH] + "…"


def _from_ripgrep(out: str) -> dict:
    """ripgrep's `--json` stream as {path: [(number, text, is a match)]}; a path or a line that
    is not UTF-8 comes base64-encoded and is decoded with replacement."""
    def text(part: dict) -> str:
        if "text" in part:
            return part["text"]
        return base64.b64decode(part.get("bytes", "")).decode("utf-8", errors="replace")
    found = {}
    for raw in out.split("\n"):        # not splitlines(): a line may hold U+2028 or U+0085
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if event.get("type") not in ("match", "context"):
            continue
        data = event["data"]
        found.setdefault(text(data["path"]), []).append(
            (data["line_number"], _cut(text(data["lines"])), event["type"] == "match"))
    return found


def _with_ripgrep(rg: str, dirs: list, text: str, context: int, regex: bool) -> dict:
    # Raw bytes like the Python scan: no BOM sniffing, `$` before "\r\n", a stray NUL is still text.
    cmd = [rg, "--json", "--text", "--encoding", "none", "--crlf", "--no-ignore", "--hidden", "--ignore-case",
           "--context", str(context),
           "--glob", "*.md", "--glob", "!.git", "--glob", "!__pycache__", "--glob", "!.pytest_cache"]
    if not regex:
        cmd.append("--fixed-strings")
    done = subprocess.run(cmd + ["--regexp", text, "--"] + [str(d) for d in dirs],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode == 2 and not done.stdout.strip():   # a pattern that does not parse: nothing searched
        raise ValueError(" ".join(done.stderr.split()) or "ripgrep failed")
    return _from_ripgrep(done.stdout)


def _with_python(dirs: list, text: str, context: int, regex: bool) -> dict:
    try:
        hit = re.compile(text if regex else re.escape(text), re.IGNORECASE).search   # ripgrep's case folding
    except re.error as exc:
        raise ValueError(f"the pattern does not parse: {exc}") from None
    found = {}
    for path in (p for d in dirs for p in _files(d, ["."]) if p.name.endswith(".md")):   # not "x.MD", as rg
        try:
            body = path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        rows = body.split("\n")
        if rows and rows[-1] == "":
            rows.pop()
        marks = [i for i, row in enumerate(rows) if hit(row.rstrip("\r"))]
        if not marks:
            continue
        keep = sorted({j for i in marks for j in range(max(0, i - context), min(len(rows), i + context + 1))})
        marked = set(marks)
        found[str(path)] = [(j + 1, _cut(rows[j]), j in marked) for j in keep]
    return found


def _cap(rows: list, context: int) -> tuple:
    """The first PER_FILE matches with their context, and how many matches are left out; a later
    match inside the last one's context shows as context."""
    marks = [n for n, _, m in rows if m]
    if len(marks) <= PER_FILE:
        return rows, 0
    cut = marks[PER_FILE - 1]
    return [(n, t, m and n <= cut) for n, t, m in rows if n <= cut + context], len(marks) - PER_FILE


def lines(root, text: str, context: int = 2, for_subject: str | None = None, regex: bool = False) -> list:
    """Every file in the layers a search reads that holds `text`, with its matching lines and
    their context: this space first, then each mount, paths in order. A pattern that does not
    parse raises ValueError."""
    root = Path(root)
    zones = [(i, layer, base, base / name) for i, (layer, base, names) in enumerate(_layers(root))
             for name in names if (base / name).is_dir()]
    if not zones:
        return []
    context = max(0, int(context))
    dirs = [zone for _, _, _, zone in zones]
    rg = _ripgrep()
    raw = _with_ripgrep(rg, dirs, text, context, regex) if rg else _with_python(dirs, text, context, regex)
    out = []
    for path, rows in raw.items():
        path = Path(path)
        for i, layer, base, zone in zones:
            if path.is_relative_to(zone):
                rel = path.relative_to(base).as_posix()
                shown_rows, more = _cap(sorted(rows), context)
                out.append((i, rel, Found(layer, rel, subject_of(rel), shown_rows, more)))
                break
    found = [f for _, _, f in sorted(out, key=lambda o: o[:2])]
    if for_subject:
        keep = _in_rings(root, for_subject)
        found = [f for f in found if keep(f)]
    return found


def tiered(root, found: list) -> list:
    """Each hit's tier, read for these hits only: a search reads no front matter it does not show."""
    root = Path(root)
    space = spaceyaml.load(root)
    for f in found:
        base = root if not f.layer else space.mount_root(f.layer)
        f.tier = tier(base, f.path) if base is not None else ""
    return found


# ----- the built-in index engine ---------------------------------------------------
def _terms(text: str) -> set:
    return {w.lower() for w in WORD.findall(text)}


def build(root) -> dict:
    """Rebuild the index of this space (its own audience only) from the files on disk."""
    root = Path(root)
    space = spaceyaml.load(root)
    files, terms = {}, {}
    for path in _files(root, _zones(space, True)):
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        files[rel] = int(path.stat().st_mtime)
        for term in _terms(text):
            terms.setdefault(term, []).append(rel)
    data = {"built_at": pages.iso(space.now()), "files": files, "terms": terms}
    target = root / INDEX
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return {"files": len(files), "terms": len(terms), "built_at": data["built_at"]}


def _load_index(root: Path) -> dict | None:
    path = root / INDEX
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def prune(root) -> int:
    """Drop index entries whose files moved or vanished; returns how many were dropped."""
    root = Path(root)
    cfg = config(root)
    if cfg["engine"] not in ("index",):
        adapter = _adapter(cfg["engine"])
        return int(adapter.prune(root)) if adapter is not None and hasattr(adapter, "prune") else 0
    data = _load_index(root)
    if data is None:
        return 0
    gone = [rel for rel in data.get("files", {}) if not (root / rel).exists()]
    if not gone:
        return 0
    for rel in gone:
        data["files"].pop(rel, None)
    data["terms"] = {t: [r for r in rels if r not in gone] for t, rels in data.get("terms", {}).items()}
    data["terms"] = {t: rels for t, rels in data["terms"].items() if rels}
    (root / INDEX).write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return len(gone)


def _adapter(engine: str):
    if engine in ("none", "index", ""):
        return None
    try:
        return importlib.import_module(f"search_{engine}")
    except ImportError:
        return None


def built_age_hours(root) -> float | None:
    data = _load_index(Path(root))
    if not data or not data.get("built_at"):
        return None
    space = spaceyaml.load(root)
    return (space.now() - pages.parse_iso(data["built_at"])).total_seconds() / 3600


def _in_rings(root, sid: str):
    """A test for hits in rings 1 and 2 of the subject: the brain's pages; a mount that is the
    home of the subject, a parent or a part; this space's pages about one of them."""
    root = Path(root)
    space = spaceyaml.load(root)
    reg = registry.Registry(root, space)
    ring = {sid} | {s.id for s in reg.ancestors_of(sid)} | {s.id for s in reg.everywhere() if s.part_of == sid}
    homes = {reg.home_of(r) for r in ring} - {None}
    kept = set()
    for mount in space.mounts:
        name = str(mount.get("name", ""))
        mroot = space.mount_root(name)
        if mroot is None:
            continue
        try:
            other = spaceyaml.load(mroot)
        except Exception:
            continue
        if other.profile == "brain" or other.name in homes:
            kept.add(name)

    def keep(hit: Hit) -> bool:
        if hit.layer:
            return hit.layer in kept
        if hit.path.startswith(PRIVATE_NOTES):
            return False
        if hit.subject in ring:
            return True
        try:   # front matter that names no ring subject cannot route to it: no YAML to parse then
            head = (root / hit.path).read_text(encoding="utf-8", errors="replace").split("\n---", 1)[0]
        except OSError:
            return False
        if not any(r in head for r in ring):
            return False
        try:
            fm, _ = pages.read(root / hit.path)
        except Exception:
            return False
        return bool(ring & set(registry.routed_subjects(fm, space.name)))
    return keep


def query(root, text: str, limit: int = 50, for_subject: str | None = None) -> list:
    """The space's search: its engine when one runs, the plain search otherwise and always for
    the mounts (each mount keeps its own engine for its own audience). With `for_subject`, only
    the hits in its rings 1 and 2 (`_in_rings`). Each hit carries its tier."""
    return tiered(root, _hits(root, text, limit, for_subject))


def _hits(root, text: str, limit: int = 50, for_subject: str | None = None) -> list:
    if for_subject:
        keep = _in_rings(root, for_subject)
        return [h for h in _hits(root, text, sys.maxsize) if keep(h)][:limit]
    root = Path(root)
    cfg = config(root)
    engine = cfg["engine"]
    if engine == "none":
        return plain(root, text, limit)
    adapter = _adapter(engine)
    if engine != "index" and adapter is None:
        return plain(root, text, limit)
    if adapter is not None:
        return list(adapter.query(root, text))[:limit]
    age = built_age_hours(root)
    if age is None or age > float(cfg.get("freshness_hours") or 24):
        build(root)
    prune(root)
    data = _load_index(root) or {}
    words = _terms(text) or {text.lower()}
    candidates = None
    for word in words:
        found = set(data.get("terms", {}).get(word, []))
        candidates = found if candidates is None else candidates & found
    hits = []
    for rel in sorted(candidates or []):
        path = root / rel
        if not path.exists():
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        line = _first_line(body, next(iter(words))) or body.strip().splitlines()[0][:200]
        hits.append(Hit("", rel, subject_of(rel), line))
    own = {h.path for h in hits}
    for hit in plain(root, text, limit):
        if hit.layer and hit.path not in own:
            hits.append(hit)
    return hits[:limit]


def _print_lines(root: Path, rest: list) -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure:   # UTF-8 to a pipe, as an agent reads it; a character that cannot go out prints its code
        reconfigure(encoding="utf-8", errors="backslashreplace")
    opts, words = {"--for": None, "-C": "2", "--max": "30", "--regex": False}, []
    while rest:                                   # a flag may stand before or after the text
        word = rest.pop(0)
        if word == "--":                          # the rest is text, dashes and all
            words += rest
            break
        if word == "--regex":
            opts["--regex"] = True
        elif word in ("--for", "-C", "--context", "--max"):
            opts["-C" if word == "--context" else word] = rest.pop(0) if rest else ""
        elif word.startswith("-") and len(word) > 1:
            print(f"search: unknown option {word} (the text is literal and case-insensitive; "
                  "-C <n>, --max <n>, --regex, --for <subject>)", file=sys.stderr)
            return 2
        else:
            words.append(word)
    text = " ".join(words)
    try:
        context, shown = int(opts["-C"]), max(1, int(opts["--max"]))
        if not text.strip() or opts["--for"] == "":
            raise ValueError("give the text to look for")
        found = lines(root, text, context, opts["--for"], opts["--regex"])
    except ValueError as exc:
        print(f"search: {exc}", file=sys.stderr)
        return 2
    for f in tiered(root, found[:shown]):
        print(f"{f.layer}:{f.path}" if f.layer else f.path, f"[{f.subject or '-'}]", f"({f.tier})")
        before = None
        for number, text, match in f.lines:
            if before is not None and number > before + 1:
                print("  --")
            print(f"  {number}{':' if match else '-'} {text}")
            before = number
        if f.more:
            print(f"  (+{f.more} more matches in this file)")
    if len(found) > shown:
        left = len(found) - shown
        print(f"{left} more file{'s' if left > 1 else ''} match{'' if left > 1 else 'es'}: "
              "narrow the text, or pass --max")
    return 0 if found else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    verbs = ("query", "lines", "build", "prune", "config")
    root = Path(".")
    if argv and argv[0] not in verbs:
        root = Path(argv.pop(0))
    if not argv or argv[0] not in verbs:
        print(__doc__)
        return 2
    verb, rest = argv[0], argv[1:]
    if verb == "config":
        print(json.dumps(config(root), indent=2, default=str))
        return 0
    if verb == "build":
        print(json.dumps(build(root)))
        return 0
    if verb == "prune":
        print(f"dropped {prune(root)}")
        return 0
    if verb == "lines":
        return _print_lines(root, rest)
    for_subject = None
    if rest[:1] == ["--for"] and len(rest) > 1:
        for_subject, rest = rest[1], rest[2:]
    hits = query(root, " ".join(rest), for_subject=for_subject)
    for hit in hits:
        where = f"{hit.layer}:{hit.path}" if hit.layer else hit.path
        print(f"{where} [{hit.subject or '-'}] ({hit.tier}) {hit.line}")
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())
