"""Template archive: the template as one zip and its checksum, so a setup can fetch it without
git or GitHub's tool.

`build` zips the files git tracks as regular files (`git ls-files -s`, so no `.git`, no caches,
no state, no mounts) into `brain-template-<tag>.zip` under one top folder, `brain-template-<tag>/`,
and writes `SHA256SUMS` beside it: one line, `<sha256>  brain-template-<tag>.zip`, the form
`sha256sum -c` reads. A tracked link, or a tracked path that is a link or no regular file on disk,
fails the build: its target never goes into the archive. The zip is reproducible: files sorted
by path, every entry dated 1980-01-01 00:00:00 with the same permissions, so two builds of the
same tree are byte-identical. `verify` recomputes the zip's sha256, compares it with its one line
in `SHA256SUMS`, and checks that every entry unpacks inside the top folder. A checksum shows the
download is the file the release published, not who published it: releases are not signed yet.
The release workflow
(`.github/workflows/release.yml`) runs `build` on a pushed tag `v*` and attaches both files to the
release. Before that it runs `ruleset`: with template main landing on green, the tag is the one
human gate on what reaches the spaces, so a release is published only while GitHub shows an
active tag ruleset over `refs/tags/v*` that restricts creating and moving such a tag and that the
workflow's own token cannot bypass (`tag_ruleset_problem`). A missing ruleset fails the job, with
the fix in its message, instead of staying a setting nobody checks.

    python template_archive.py build <root> <out_dir> <tag>    writes the zip and SHA256SUMS
    python template_archive.py verify <zip> <sums>             exit 0 when it matches, 1 when not
    python template_archive.py ruleset <owner/name>            exit 0 when the tag ruleset is there
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
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
    """The template's files: what git tracks as a regular file, sorted, as far as they are on disk.
    A mount (a gitlink) is no file and stays out, as before. A tracked link, or a path git tracks as
    a file that is a link or no regular file on disk, fails the build: zipping it would copy what it
    points at (a runner's environment, a file outside the tree) into the public archive, and
    leaving it out would ship a release without one of its files."""
    done = subprocess.run(["git", "-C", str(root), "ls-files", "-s", "-z"], capture_output=True)
    if done.returncode != 0:
        raise RuntimeError(f"git ls-files failed in {root}: {done.stderr.decode('utf-8', 'replace').strip()}")
    names, refused = [], []
    top = Path(root).resolve()
    for entry in done.stdout.decode("utf-8").split("\0"):
        if not entry:
            continue
        meta, _, name = entry.partition("\t")
        mode = meta.split(" ", 1)[0]
        if mode == "160000":
            continue
        path = Path(root) / name
        if mode not in ("100644", "100755") or path.is_symlink() or not path.is_file():
            if mode in ("100644", "100755") and not os.path.lexists(path):
                continue     # deleted in the working tree and not committed: not on disk, as before
            refused.append(name)
            continue
        try:
            path.resolve().relative_to(top)
        except ValueError:
            refused.append(name)    # a folder on its way is a link out of the tree
            continue
        names.append(name)
    if refused:
        raise RuntimeError("tracked links or non-regular files cannot go in the archive: "
                           + ", ".join(ascii(n) for n in sorted(set(refused))))
    return sorted(set(names))


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


def unsafe_entries(zip_path: Path) -> list[str]:
    """The entries of a release zip that unpacking could put outside its one top folder, or that
    unpack as something other than a file or folder: an absolute name, a `..` part, a backslash,
    a drive, a name outside `brain-template-<tag>/`, or a link. Empty for a zip `build` wrote."""
    zip_path = Path(zip_path)
    top = zip_path.name[:-len(".zip")] + "/" if zip_path.name.endswith(".zip") else ""
    bad = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename
            parts = name.rstrip("/").split("/")
            kind = (info.external_attr >> 16) & 0o170000
            if (not top or not name.startswith(top) or name.startswith("/") or "\\" in name or ":" in name
                    or any(p in ("", ".", "..") for p in parts) or kind not in (0, 0o100000, 0o040000)):
                bad.append(name)
    return bad


def verify(zip_path: Path, sums_path: Path) -> bool:
    """True when `sums_path` has a line for this zip's name, that line's sha256 (64 hex
    characters) is the zip's, and every entry unpacks inside the zip's one top folder
    (`unsafe_entries`). A zip that is not one, a missing file or a second line for the same
    name with another sum is False: the check fails closed."""
    zip_path = Path(zip_path)
    try:
        lines = Path(sums_path).read_text(encoding="utf-8").splitlines()
        actual = digest(zip_path)
    except (OSError, ValueError):
        return False
    wanted = set()
    for line in lines:
        want, _, name = line.strip().partition(" ")
        if name.lstrip(" *") == zip_path.name:      # two spaces (text) or a space and `*` (binary)
            wanted.add(want.lower())
    if len(wanted) != 1 or not re.fullmatch(r"[0-9a-f]{64}", next(iter(wanted))) or wanted != {actual}:
        return False
    try:
        return not unsafe_entries(zip_path)
    except (OSError, zipfile.BadZipFile):
        return False


# A release tag as the ruleset must cover it, a final release and a candidate.
RELEASE_REFS = ("refs/tags/v1.2.3", "refs/tags/v1.2.3-rc1")
TAG_RULES = ("creation", "update")   # making a release tag, and moving one to another commit


def _covers(patterns, ref: str) -> bool:
    return any(p == "~ALL" or fnmatch.fnmatchcase(ref, str(p)) for p in patterns or [])


def tag_ruleset_problem(rulesets: list) -> str | None:
    """Why the repository's rulesets (as GitHub's API gives them, each with its conditions and
    rules) do not make the release tag a maintainer's act, or None when one does: an active ruleset
    on tags whose `ref_name` includes and does not exclude every release tag (`RELEASE_REFS`),
    restricting creation and update (`TAG_RULES`), that the asking token cannot always bypass
    (`current_user_can_bypass`; in the release job that token is the workflow's own) and that
    lists no app or deploy key among its bypass actors when GitHub shows them."""
    seen = []
    for rs in rulesets or []:
        if not isinstance(rs, dict) or rs.get("target") != "tag" or rs.get("enforcement") != "active":
            continue
        ref = ((rs.get("conditions") or {}).get("ref_name") or {})
        if not all(_covers(ref.get("include"), r) and not _covers(ref.get("exclude"), r) for r in RELEASE_REFS):
            continue
        kinds = {str((rule or {}).get("type")) for rule in rs.get("rules") or [] if isinstance(rule, dict)}
        missing = [k for k in TAG_RULES if k not in kinds]
        if missing:
            seen.append(f"ruleset {rs.get('name') or rs.get('id')} does not restrict {' and '.join(missing)}")
            continue
        if rs.get("current_user_can_bypass") == "always":
            seen.append(f"ruleset {rs.get('name') or rs.get('id')} lets this workflow's token bypass it")
            continue
        apps = [a for a in rs.get("bypass_actors") or []
                if isinstance(a, dict) and a.get("actor_type") in ("Integration", "DeployKey")]
        if apps:
            seen.append(f"ruleset {rs.get('name') or rs.get('id')} lets an app or a deploy key bypass it")
            continue
        return None
    why = "; ".join(seen) or "no active tag ruleset covers refs/tags/v*"
    return (f"{why}: only a maintainer may create or move a release tag. Add a tag ruleset on refs/tags/v* "
            "(Settings, Rules, Rulesets) restricting creations and updates, with no bypass for apps or "
            "GitHub Actions (SECURITY.md)")


def _gh_json(*args):
    done = subprocess.run(["gh", "api", *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60)
    if done.returncode:
        raise RuntimeError((done.stderr or done.stdout or "gh api failed").strip().splitlines()[-1])
    return json.loads(done.stdout or "null")


def rulesets_of(repo: str) -> list:
    """The repository's tag rulesets in full (the listing leaves out conditions and rules)."""
    if not re.fullmatch(r"[A-Za-z0-9][\w.-]*/[\w.-]+", repo or ""):
        raise ValueError(f"{repo!r} is not owner/name")
    listed = _gh_json(f"repos/{repo}/rulesets?includes_parents=true&per_page=100")
    return [_gh_json(f"repos/{repo}/rulesets/{int(rs['id'])}") for rs in listed or []
            if isinstance(rs, dict) and rs.get("target") == "tag" and rs.get("id") is not None]


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
    r = sub.add_parser("ruleset", help="exit 0 when a tag ruleset makes the release tag a maintainer's act")
    r.add_argument("repo")
    args = ap.parse_args(argv)
    if args.cmd == "ruleset":
        try:
            problem = tag_ruleset_problem(rulesets_of(args.repo))
        except (ValueError, RuntimeError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
            problem = f"the rulesets of {args.repo} cannot be read ({exc}): nothing is published until they can"
        if problem:
            print(f"::error::{problem}")
            return 1
        print(f"{args.repo}: only a maintainer creates or moves a release tag")
        return 0
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
