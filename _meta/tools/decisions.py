"""The decisions index: what was decided, in force or superseded, without walking (shape 1).

A derived layer, never the only copy: `build` reads every signed decision of this space
(`knowledge/decisions/` and any page of `type: decision`) into `_meta/state/decisions.yaml`
with its id, title, subject, signer, signed date, `supersedes`, the derived `superseded_by`
and its first line. The librarian rebuilds it every run. `find` searches this space's index
and every mount's (a mount without one is scanned on the fly) and lists the decisions in
force first, superseded ones marked. The query skill asks it first for "what did we decide"
questions; the probe's `decisions` method measures it against the walk and the search.

    python decisions.py [root] build
    python decisions.py [root] find [--subject S] [--text T]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

INDEX = Path("_meta") / "state" / "decisions.yaml"
WORD = re.compile(r"[a-z0-9]{3,}")
STOP = {"what", "did", "we", "decide", "decided", "about", "the", "and", "for", "with", "when", "which",
        "who", "how", "why", "our", "was", "were", "this", "that", "have", "has"}


def _first_line(body: str) -> str:
    for line in body.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:200]
    return ""


def scan(root) -> list:
    root = Path(root)
    rows = []
    base = root / "knowledge"
    for path in sorted(base.rglob("*.md")) if base.is_dir() else []:
        rel = path.relative_to(root).as_posix()
        if rel.startswith("knowledge/sources/") or not pages.is_page_path(path):
            continue
        try:
            fm, body = pages.read(path)
        except Exception:
            continue
        if str(fm.get("status")) != "stable":
            continue
        if not (rel.startswith("knowledge/decisions/") or str(fm.get("type")) == "decision"):
            continue
        rows.append({"id": fm.get("id"), "title": fm.get("title"), "subject": fm.get("subject"),
                     "signed_at": fm.get("signed_at"), "signed_by": fm.get("signed_by"),
                     "supersedes": fm.get("supersedes"), "path": rel, "line": _first_line(body)})
    by_id = {r["id"]: r for r in rows}
    for row in rows:
        old = row.get("supersedes")
        for target in (old if isinstance(old, list) else [old]):
            if target in by_id:
                by_id[target]["superseded_by"] = row["id"]
    return rows


def build(root) -> list:
    root = Path(root)
    pages.refuse_copy(root)
    rows = scan(root)
    space = spaceyaml.load(root)
    text = yaml.safe_dump({"built_at": pages.iso(space.now()), "decisions": rows}, sort_keys=False,
                          allow_unicode=True, width=100)
    path = root / INDEX
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    strip = lambda t: "\n".join(l for l in t.splitlines() if not l.startswith("built_at:"))  # noqa: E731
    if strip(old) != strip(text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return rows


def _rows(root: Path) -> list:
    path = root / INDEX
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict) and isinstance(data.get("decisions"), list):
            return data["decisions"]
    return scan(root)


def _words(text: str) -> set:
    return {w for w in WORD.findall(str(text or "").lower()) if w not in STOP}


def find(root, subject: str | None = None, text: str | None = None) -> list:
    """Matching decisions across this space and its mounts: in force first, newest first."""
    root = Path(root)
    space = spaceyaml.load(root)
    layers = [("", root)] + [(str(m.get("name")), space.mount_root(str(m.get("name", ""))))
                             for m in space.mounts]
    wanted = _words(text) if text else set()
    out = []
    for layer, base in layers:
        if base is None:
            continue
        for row in _rows(base):
            hay = _words(f"{row.get('title')} {row.get('line')} {row.get('subject')}")
            by_subject = bool(subject) and str(row.get("subject") or "") == subject
            by_text = bool(wanted) and bool(wanted & hay)
            if (subject or wanted) and not (by_subject or by_text):
                continue
            out.append(dict(row, layer=layer))
    in_force = [r for r in out if not r.get("superseded_by")]
    superseded = [r for r in out if r.get("superseded_by")]
    in_force.sort(key=lambda r: str(r.get("signed_at") or ""), reverse=True)
    return in_force + superseded


def texts(space: spaceyaml.Space, subject: str, question: str) -> dict:
    """The probe's `decisions` method: the pages of the decisions the index finds."""
    found = {}
    for row in find(space.root, subject or None, question):
        base = space.root if not row.get("layer") else space.mount_root(row["layer"])
        if base is None:
            continue
        path = base / row["path"]
        if path.exists():
            key = f"{row['layer']}:{row['path']}" if row.get("layer") else row["path"]
            found[key] = path.read_text(encoding="utf-8", errors="replace")
    return found


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(".")
    if argv and argv[0] not in ("build", "find"):
        root = Path(argv.pop(0))
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "build":
        print(f"{len(build(root))} decisions -> {INDEX.as_posix()}")
        return 0
    ap = argparse.ArgumentParser(prog="decisions.py find")
    ap.add_argument("--subject")
    ap.add_argument("--text")
    opts = ap.parse_args(argv[1:])
    rows = find(root, opts.subject, opts.text)
    for row in rows:
        state = f"superseded by {row['superseded_by']}" if row.get("superseded_by") else "in force"
        where = f"{row['layer']}:{row['path']}" if row.get("layer") else row["path"]
        print(f"- {row['title']} ({state}, signed {str(row.get('signed_at') or '')[:10]} by "
              f"{row.get('signed_by')}) {where}: {row.get('line')}")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
