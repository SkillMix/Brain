"""Search: the second path to a subject, plain by default, an index only when chosen (shape 1).

Walking needs no tools; search is faster when it runs. Every space can search its own
`knowledge/` and `live/` (and a personal space its `work/`) and each mount's `knowledge/`
and `live/` with plain text; a hit expands to its subject. The engine file
`_meta/search.yaml` says whether anything more runs:

    engine: none            # none | index | <adapter>: an index is disposable, rebuilt from git
    version: null           # the adapter's pinned version, when one runs
    sources: [knowledge, live]
    freshness_hours: 24     # an index older than this is rebuilt before it answers
    link_step: off          # extras stay off until the probe shows a gain:
    atoms: off              #   `measured: [{feature, gain, at}]` records it
    measured: []

`engine: index` keeps `_meta/state/search-index.json` (term -> files) for this space's own
audience only; `prune` drops entries whose files moved or vanished. Another engine is an
adapter module `search_<engine>.py` beside this one with `query(root, text)`, `build(root)`
and `prune(root)`; when it is absent the plain search answers.

    python search.py [root] query <text>
    python search.py [root] build | prune | config
"""
from __future__ import annotations

import importlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

CONFIG = Path("_meta") / "search.yaml"
INDEX = Path("_meta") / "state" / "search-index.json"
DEFAULTS = {"engine": "none", "version": None, "sources": ["knowledge", "live"], "freshness_hours": 24,
            "link_step": "off", "atoms": "off", "measured": []}
EXTRAS = ("link_step", "atoms")
SKIP_PARTS = {".git", "__pycache__", ".pytest_cache"}
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{1,}")


@dataclass
class Hit:
    layer: str      # "" for this space, else the mount's name
    path: str       # relative to that layer's root
    subject: str    # the subject the hit expands to ("" when none)
    line: str       # the first matching line

    def to_dict(self) -> dict:
        return {"layer": self.layer, "path": self.path, "subject": self.subject, "line": self.line}


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
    if own and space.profile == "personal" and "work" not in zones:
        zones.append("work")
    return zones


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


def query(root, text: str, limit: int = 50) -> list:
    """The space's search: its engine when one runs, the plain search otherwise and always for
    the mounts (each mount keeps its own engine for its own audience)."""
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


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    verbs = ("query", "build", "prune", "config")
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
    hits = query(root, " ".join(rest))
    for hit in hits:
        where = f"{hit.layer}:{hit.path}" if hit.layer else hit.path
        print(f"{where} [{hit.subject or '-'}] {hit.line}")
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())
