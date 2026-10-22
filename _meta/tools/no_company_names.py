"""The template's name gate: no company, person, client or product name of any real
company may appear in the template (shape 1, template profile only).

The names themselves never enter the repository, not even to be forbidden:
`_meta/tests/forbidden-names.sha256` holds one sha256 hex per line of each lowercased
name (`#` comments allowed), generated outside the repository. Every text file under the
root is tokenized and each token's hash is compared with the list.

Tokens: `[A-Za-z][A-Za-z0-9]{2,}`. A run joined by `-` or `_` is also tried whole
(`lantern-works`), with the joiners removed (`lanternworks`) and part by part.

One exception (docs/decisions.md, 2026-10-18 and 2026-10-21): the project's public brand is on
the list like any company name, and may appear in its brand file, `BRAND_FILE`, and in the root
`README.md`, which names the repository's own address; nowhere else (`BRAND_FILES`). Those files
are still checked for every other name.

    python no_company_names.py [root]      prints `path:line` per hit; exit 1 on any hit
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

HASHES_PATH = Path("_meta") / "tests" / "forbidden-names.sha256"
SKIP_DIRS = (".git",)
SKIP_PREFIXES = ("_meta/state/feed/",)
MAX_BYTES = 1024 * 1024
SNIFF_BYTES = 8192
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]{2,}")
BRAND_FILE = "_meta/templates/onboarding.css"
BRAND_FILES = (BRAND_FILE, "README.md")   # the root README only: the template's own address
BRAND = "59bcbc833b166b267b9ae14b6d07dd886cc66294aad86779f2fdc7fef7ab7f96"   # the sha256 of the brand, as on the list
COMPOUND = re.compile(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+")


def load_hashes(path: Path) -> set:
    if not path.exists():
        return set()
    hashes = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            hashes.add(line.lower())
    return hashes


def candidates(line: str) -> set:
    """Every lowercased form of every token on the line worth hashing."""
    found = set(m.group(0).lower() for m in TOKEN.finditer(line))
    for m in COMPOUND.finditer(line):
        whole = m.group(0).lower()
        found.add(whole)
        found.add(re.sub(r"[-_]", "", whole))
    return {c for c in found if len(c) >= 3}


def line_hits(text: str, hashes: set) -> list:
    hits = []
    for number, line in enumerate(text.splitlines(), start=1):
        if any(hashlib.sha256(c.encode("utf-8")).hexdigest() in hashes for c in candidates(line)):
            hits.append(number)
    return hits


def _is_text(path: Path) -> bool:
    try:
        if path.stat().st_size > MAX_BYTES:
            return False
        with path.open("rb") as handle:
            return b"\x00" not in handle.read(SNIFF_BYTES)
    except OSError:
        return False


def _walk(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        posix = rel.as_posix()
        if posix.startswith(SKIP_PREFIXES) or posix == HASHES_PATH.as_posix():
            continue
        yield path, posix


def check(root, hashes_path=None) -> list:
    """(relpath, line_no) for every line under root carrying a forbidden token."""
    root = Path(root)
    hashes = load_hashes(Path(hashes_path) if hashes_path else root / HASHES_PATH)
    if not hashes:
        return []
    hits = []
    for path, rel in _walk(root):
        if not _is_text(path):
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        hits.extend((rel, number) for number in line_hits(text, hashes - {BRAND} if rel in BRAND_FILES else hashes))
    return hits


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(argv[0]) if argv else Path(".")
    hits = check(root)
    for rel, number in hits:
        print(f"{rel}:{number}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
