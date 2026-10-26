"""Template archive: the template as one zip and its checksum, so a setup can fetch it without
git or GitHub's tool.

`build` zips the files git tracks (`git ls-files`, so no `.git`, no caches, no state) into
`brain-template-<tag>.zip` under one top folder, `brain-template-<tag>/`, and writes `SHA256SUMS`
beside it: one line, `<sha256>  brain-template-<tag>.zip`, the form `sha256sum -c` reads. The zip is
reproducible: files sorted by path, every entry dated 1980-01-01 00:00:00 with the same
permissions, so two builds of the same tree are byte-identical. `verify` recomputes the zip's
sha256 and compares it with its line in `SHA256SUMS`. The release workflow
(`.github/workflows/release.yml`) runs `build` on a pushed tag `v*` and attaches both files to the
release.

    python template_archive.py build <root> <out_dir> <tag>    writes the zip and SHA256SUMS
    python template_archive.py verify <zip> <sums>             exit 0 when it matches, 1 when not
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import zipfile
from pathlib import Path

NAME = "brain-template"
SUMS = "SHA256SUMS"
STAMP = (1980, 1, 1, 0, 0, 0)            # the earliest date a zip can hold
FILE_ATTR = 0o100644 << 16               # a regular file, rw-r--r--, as a Unix zip records it
# A tag becomes a folder, a file name and a checksum line, and git lets a tag hold `/`, `$` and
# quotes: only plain names pass.
TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
CHUNK = 1024 * 1024


def tracked(root: Path) -> list[str]:
    """The template's files: what git tracks, sorted, as far as they are on disk (a mount's folder
    is listed as one entry and is not a file)."""
    done = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True)
    if done.returncode != 0:
        raise RuntimeError(f"git ls-files failed in {root}: {done.stderr.decode('utf-8', 'replace').strip()}")
    names = sorted(n for n in done.stdout.decode("utf-8").split("\0") if n)
    return [n for n in names if (root / n).is_file()]


def digest(path: Path) -> str:
    """The sha256 of a file, read in pieces."""
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            sha.update(chunk)
    return sha.hexdigest()


def build(root: Path, out_dir: Path, tag: str) -> tuple[Path, Path]:
    """Write `brain-template-<tag>.zip` and `SHA256SUMS` into `out_dir`; return both paths."""
    if not TAG.fullmatch(tag):
        raise ValueError(f"not a tag a file can be named after: {tag!r}")
    root, out_dir = Path(root), Path(out_dir)
    top = f"{NAME}-{tag}"
    files = tracked(root)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{top}.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for rel in files:
            info = zipfile.ZipInfo(f"{top}/{rel}", STAMP)
            info.create_system = 3       # Unix on every system, so the permissions below are read as set
            info.external_attr = FILE_ATTR
            archive.writestr(info, (root / rel).read_bytes(), zipfile.ZIP_DEFLATED, 9)
    sums_path = out_dir / SUMS
    sums_path.write_bytes(f"{digest(zip_path)}  {zip_path.name}\n".encode("ascii"))   # bytes: no CRLF on Windows
    return zip_path, sums_path


def verify(zip_path: Path, sums_path: Path) -> bool:
    """True when `sums_path` has a line for this zip's name and that line's sha256 is the zip's."""
    zip_path = Path(zip_path)
    try:
        lines = Path(sums_path).read_text(encoding="utf-8").splitlines()
        actual = digest(zip_path)
    except (OSError, ValueError):
        return False
    for line in lines:
        want, _, name = line.strip().partition(" ")
        if name.lstrip(" *") == zip_path.name:      # two spaces (text) or a space and `*` (binary)
            return want.lower() == actual
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write the zip and SHA256SUMS")
    b.add_argument("root")
    b.add_argument("out_dir")
    b.add_argument("tag")
    v = sub.add_parser("verify", help="exit 0 when the zip matches its line in SHA256SUMS, 1 when not")
    v.add_argument("zip")
    v.add_argument("sums")
    args = ap.parse_args(argv)
    if args.cmd == "build":
        try:
            for path in build(Path(args.root), Path(args.out_dir), args.tag):
                print(path)
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"template_archive: {exc}", file=sys.stderr)
            return 1
        return 0
    if verify(Path(args.zip), Path(args.sums)):
        print(f"{args.zip}: matches {args.sums}")
        return 0
    print(f"{args.zip}: does NOT match {args.sums}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
