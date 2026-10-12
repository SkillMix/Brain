"""ids: the permanent-id index of a space (shape 1).

Every page carries a permanent `id`; links point at ids (`[text](id:p-1a2b3c4d)`).
This module keeps `_meta/state/ids.yaml`, the map from id to path, so links resolve
without a scan, and resolves an id through this space first and then every mounted
ancestor's own index. Hubs and kind indexes (`index.md`) carry no id and are skipped.

    python ids.py [root] build           scan the page roots and write _meta/state/ids.yaml
    python ids.py [root] resolve <id>    print the path an id points at (exit 1 when unknown)
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

PAGE_ROOTS = ("knowledge", "live", "identity", "_inbound/candidates", "_inbound/checks")
IDS_PATH = Path("_meta") / "state" / "ids.yaml"


def scan(root) -> dict:
    """id -> relative posix path for every page under the page roots (first path wins)."""
    root = Path(root)
    found: dict = {}
    for sub in PAGE_ROOTS:
        base = root / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            if not pages.is_page_path(path):
                continue
            try:
                fm, _ = pages.read(path)
            except Exception:
                continue
            pid = fm.get("id") if isinstance(fm, dict) else None
            if pid:
                found.setdefault(str(pid), path.relative_to(root).as_posix())
    return dict(sorted(found.items()))


def build(root) -> dict:
    """Scan and write `_meta/state/ids.yaml` as `{ids: {id: relpath}}`, sorted by id."""
    root = Path(root)
    table = scan(root)
    path = root / IDS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump({"ids": table}, sort_keys=True, allow_unicode=True, width=200)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8", newline="\n")
    return table


def table(root) -> dict:
    """The space's index when it exists, else a fresh scan (nothing is written)."""
    root = Path(root)
    path = root / IDS_PATH
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            data = {}
        if isinstance(data, dict):
            ids = data.get("ids") if "ids" in data else data
            if isinstance(ids, dict):
                return {str(k): str(v) for k, v in ids.items() if isinstance(v, str)}
    return scan(root)


def _roots(root) -> list:
    """This space, then each mounted ancestor present on disk."""
    root = Path(root)
    roots = [root]
    try:
        roots.extend(spaceyaml.load(root).ancestor_roots())
    except (FileNotFoundError, ValueError):
        pass
    return roots


def known(root) -> dict:
    """id -> relative path across this space and its ancestors; the nearest space wins."""
    merged: dict = {}
    for base in _roots(root):
        for pid, rel in table(base).items():
            merged.setdefault(pid, rel)
    return merged


def resolve(root, pid: str) -> Path | None:
    """The file an id points at: this space's index first, then each mount's."""
    for base in _roots(root):
        rel = table(base).get(str(pid))
        if rel:
            return base / rel
    return None


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = list(argv)
    root = Path(".")
    if args and args[0] not in ("build", "resolve"):
        root = Path(args.pop(0))
    if not args:
        print(__doc__)
        return 2
    cmd = args[0]
    if cmd == "build":
        found = build(root)
        print(f"{len(found)} ids -> {IDS_PATH.as_posix()}")
        return 0
    if cmd == "resolve" and len(args) > 1:
        path = resolve(root, args[1])
        print(path.as_posix() if path else f"unknown id {args[1]}")
        return 0 if path else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
