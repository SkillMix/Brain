"""ids: the permanent-id index of a space (shape 1).

Every page carries a permanent `id`; links point at ids (`[text](id:p-1a2b3c4d)`).
This module keeps `_meta/state/ids.yaml`, the map from id to path, so links resolve
without a scan, and resolves an id through this space first and then every mounted
ancestor's own index. Hubs and kind indexes (`index.md`) carry no id and are skipped.

The committed index is data a pull request can change, so `table` trusts none of it in part:
an entry whose key is not a page id, or whose path is not a page file under one of the page
roots inside the space (`entry_problem`: no absolute path, no `..`, no link, nothing outside
root once resolved), makes it scan the page roots instead. The check stays cheap on an index
of thousands of pages: every entry is read as text, the page roots are walked once for links
(no page is opened), and only a path that could be a short 8.3 name (a `~` in it) is resolved
on its own (`usable`). A tool that writes to a page it found by id still checks that the page
carries that id (landing does).

    python ids.py [root] build           scan the page roots and write _meta/state/ids.yaml
    python ids.py [root] resolve <id>    print the path an id points at (exit 1 when unknown)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

PAGE_ROOTS = ("knowledge", "live", "identity", "_inbound/candidates", "_inbound/checks")
IDS_PATH = Path("_meta") / "state" / "ids.yaml"


def scan(root, roots=PAGE_ROOTS) -> dict:
    """id -> relative posix path for every page under the page roots, or the `roots` given
    (first path wins). No page is read through a link, at the page or at any folder between root
    and it (`_walk`)."""
    root = Path(root)
    found: dict = {}
    for sub in roots:
        files = sorted(Path(entry.path) for rel, entry, linked in _walk(root, (sub,))
                       if not linked and entry.name.endswith(".md") and entry.is_file(follow_symlinks=False))
        for path in files:
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
    text = yaml.safe_dump({"ids": table}, sort_keys=True, allow_unicode=True, width=200)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        pages.write_text(path, text, root=root)   # never through a link
    return table


def entry_problem(root, pid, rel) -> str | None:
    """Why one index entry cannot be used, or None: its key is not a page id, or its path is not
    a page file (`.md`, not an index) under a page root, inside root, reached through no link
    (pages.confine). Whether the file there carries the id is not read here."""
    if not pages.is_page_id(pid):
        return f"key {ascii(pid)[:60]} is not a page id"
    if not isinstance(rel, str) or not pages.is_page_path(Path(rel)):
        return f"{ascii(rel)[:90]} is not a page file"
    try:
        pages.confine(root, rel, zones=PAGE_ROOTS, what="indexed path")
    except pages.UnsafePath as exc:
        return str(exc)
    return None


def _text_problem(pid, rel) -> str | None:
    """What `entry_problem` refuses that the text alone shows: a key that is no page id, a path that
    is no page file, too long, backslashed, absolute or a drive, a part that is not a safe name
    (`..` among them) or `.git`, a path outside every page root (case ignored, as confine does)."""
    if not pages.is_page_id(pid):
        return f"key {ascii(pid)[:60]} is not a page id"
    if not isinstance(rel, str) or not rel or not pages.is_page_path(Path(rel)):
        return f"{ascii(rel)[:90]} is not a page file"
    if len(rel) > pages.MAX_REL or "\\" in rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        return f"indexed path {ascii(rel)[:90]} is too long, backslashed, absolute or a drive"
    for part in rel.split("/"):
        problem = pages.name_problem(part)
        if problem:
            return f"indexed path {ascii(rel)[:90]}: part {ascii(part)[:60]} {problem}"
        if part.casefold() in pages.ALWAYS_REFUSED:
            return f"indexed path {ascii(rel)[:90]} reaches into `{part}`"
    if not any(pages._under(rel, zone) for zone in PAGE_ROOTS):
        return f"indexed path {ascii(rel)[:90]} is outside {', '.join(PAGE_ROOTS)}"
    return None


def _walk(root: Path, zones=PAGE_ROOTS):
    """(path relative to root, os.DirEntry, is a link) for everything below the page roots, in one walk that
    opens no file and goes through no link (or junction): a link is yielded and not entered. A page root that is a
    link, or sits below one, is yielded as `(rel, None, True)`; one that cannot be listed the same way."""
    stack = []
    for zone in zones:
        current, rel, behind = Path(root), "", False
        for part in str(zone).strip("/").split("/"):
            current, rel = current / part, f"{rel}/{part}" if rel else part
            if pages.is_link(current):
                behind = True
                break
        if behind:
            yield rel, None, True
        elif current.is_dir():
            stack.append((current, rel))
    while stack:
        folder, rel = stack.pop()
        try:
            with os.scandir(folder) as entries:
                listed = list(entries)
        except OSError:
            yield rel, None, True
            continue
        for entry in listed:
            here = f"{rel}/{entry.name}"
            try:
                linked = pages._link_stat(entry.stat(follow_symlinks=False))
            except OSError:
                linked = True
            yield here, entry, linked
            if not linked and entry.is_dir(follow_symlinks=False):
                stack.append((Path(entry.path), here))


def _links(root: Path) -> set:
    """Every link (or junction) at or below the page roots, as a casefolded path relative to root
    (`_walk`); a folder that cannot be listed is counted as one, so nothing below it is trusted."""
    return {rel.casefold() for rel, _, linked in _walk(Path(root)) if linked}


def usable(root, ids: dict) -> bool:
    """Whether every entry of an index passes `entry_problem`, read in bulk: the text of each
    entry (`_text_problem`), then one walk for links (`_links`) that no entry's path may pass
    through, and the full check (resolved on disk) only for a path with a `~`, which could be a
    short 8.3 name standing for another folder."""
    root = Path(root)
    if any(_text_problem(k, v) for k, v in ids.items()):
        return False
    links = _links(root)
    for pid, rel in ids.items():
        if "~" in rel:
            if entry_problem(root, pid, rel):
                return False
            continue
        if links:
            parts = rel.casefold().split("/")
            if any("/".join(parts[:n]) in links for n in range(1, len(parts) + 1)):
                return False
    return True


_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)   # the same safe load, in C where PyYAML has it


def committed(root) -> dict | None:
    """The committed index as it stands, `{id: path}`, or None when there is none to read."""
    path = Path(root) / IDS_PATH
    if not path.exists():
        return None
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_LOADER) or {}
    except (yaml.YAMLError, OSError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    ids = data.get("ids") if "ids" in data else data
    return dict(ids) if isinstance(ids, dict) else None


def table(root) -> dict:
    """The space's index when it exists and every entry in it is usable (`entry_problem`, read in
    bulk by `usable`), else a fresh scan (nothing is written). The verdict is kept for the rest of
    the process while the index file is unchanged."""
    root = Path(root)
    ids = committed(root)
    if ids is not None:
        try:   # the check walks the page roots: kept while the index file is unchanged
            st = (root / IDS_PATH).stat()
            key = (str(root.resolve()), st.st_mtime_ns, st.st_size)
        except OSError:
            key = None
        if key is not None and _CHECKED.get(key) is not None:
            return dict(_CHECKED[key])
        if usable(root, ids):
            checked = {str(k): str(v) for k, v in ids.items()}
            if key is not None:
                if len(_CHECKED) > 32:   # a space and its mounts fit; an old index is dropped
                    _CHECKED.clear()
                _CHECKED[key] = checked
            return dict(checked)
    return scan(root)


_CHECKED: dict = {}


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
    """id -> relative path across this space and its ancestors; an id found in this space is
    resolved here before an ancestor's copy (ids are unique, so this only settles a stale copy)."""
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
