"""swap: a project repository with history takes the shape-1 layer (vision section 18).

A project that already has a repository (code, history, an older knowledge layer) keeps its
code and history; only its knowledge layer is replaced, the old one kept as read-only
evidence. Run once per project, when its crew joins:

1. today's layer moves under `_archive/` (the names in `--archive`, default the common ones:
   `wiki`, `live`, `_meta`, `.claude`, `index.md`, `AGENTS.md`, `CLAUDE.md`, ...), with a
   README saying what it is; nothing is deleted;
2. the template's machinery and empty skeleton are added at the root, never over a file the
   repository already has (its README, its LICENSE, its own code); `.gitignore` gains the
   template's lines;
3. `space.yaml` is filled for the project profile, live, the crew as audience, the brain
   mounted; with `--online`, the brain submodule is replaced by the new brain;
4. the generated files are rebuilt and the space validated. The sync script lands the
   result.

Once it has landed, `--go-live` runs `onboard --go-live` (the sync task, the platform settings)
and mounts the project read-only in every crew member's workspace checked out on this machine;
a crew member whose workspace is elsewhere gets one Needs-you item in the brain with the mount
line. Without the mount, nothing in a crew workspace reads the project.

    python swap.py <repository checkout> --name NAME --owner NAME --crew A B ...
                   [--brain URL] [--brain-path PATH] [--archive NAMES ...] [--online] [--dry-run]
    python swap.py <repository checkout> --go-live [--offline] [--org ORG] [--home DIR] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mint  # noqa: E402
import onboard  # noqa: E402
import spaceyaml  # noqa: E402
import validate  # noqa: E402

ARCHIVE = ("wiki", "live", "_meta", ".claude", "index.md", "AGENTS.md", "CLAUDE.md", "SETUP.md", "SKILL.md",
           "okf.yaml", "_tasks.md", "memory", "_release",
           # the old layer's own workflow pins the old brain mount and fails once the layer moves
           ".github/workflows/project-knowledge.yml")
NEVER_ADD = ("LICENSE", "README.md", ".github/CODEOWNERS")
ARCHIVE_NOTE = ("# Archive\n\nThe project's knowledge layer and machinery as they were before the swap "
                "(read-only evidence for the cold start). Nothing here is maintained; the live layer "
                "is the shape-1 space at the repository root.\n")


def _rmtree(path: Path) -> None:
    def onerr(func, p, exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if path.exists():
        shutil.rmtree(path, onerror=onerr)


def add_layer(template: Path, repo: Path, dry_run: bool) -> list:
    """The template's machinery and skeleton, never over a file the repository has."""
    added = []
    for path in sorted(template.rglob("*")):
        rel = path.relative_to(template)
        posix = rel.as_posix()
        if path.is_dir() or any(p in onboard.SKIP_DIRS for p in rel.parts):
            continue
        if any(posix == s or posix.startswith(s + "/") for s in onboard.SKIP_PATHS):
            continue
        if any(posix.startswith(c + "/") for c in onboard.CONTENT_DIRS) and posix not in onboard.KEEP_ALWAYS:
            if path.name != ".gitkeep" and not (path.name == "index.md" and len(rel.parts) == 2):
                continue
        if posix in NEVER_ADD:  # a private repository's own license and readme are its owner's
            continue
        if posix in onboard.FRESH:  # the registry, the log, the index start empty, never copied
            continue
        target = repo / rel
        if posix in (".gitignore", ".gitattributes") and target.exists():
            if not dry_run:
                lines = target.read_text(encoding="utf-8").splitlines()
                new = path.read_text(encoding="utf-8").splitlines()
                target.write_text("\n".join(dict.fromkeys(lines + new)) + "\n", encoding="utf-8", newline="\n")
            continue
        if target.exists():
            continue
        added.append(posix)
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    return added


def run(repo, name, owner, crew, brain=None, brain_path="brain", archive=ARCHIVE, online=False,
        dry_run=False, owner_email="") -> onboard.Report:
    repo = Path(repo).resolve()
    report = onboard.Report(case="swap")
    if (repo / "space.yaml").exists():
        report.add("swap", True, f"test -f {repo / 'space.yaml'}", "already a space: nothing to swap")
        return report
    moved = []
    for item in archive:
        src = repo / item
        if not src.exists():
            continue
        moved.append(item)
        if not dry_run:
            dst = repo / "_archive" / item
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
    if not dry_run and moved:
        (repo / "_archive" / "README.md").write_text(ARCHIVE_NOTE, encoding="utf-8", newline="\n")
    report.add("archive the old layer", True, f"ls {repo / '_archive'}", ", ".join(moved) or "nothing to archive")
    added = add_layer(onboard.TEMPLATE_ROOT, repo, dry_run)
    if not dry_run:
        for rel, text in onboard.FRESH.items():
            if rel != "space.yaml" and not (repo / rel).exists():
                onboard.write_text(repo / rel, text)
    report.add("add the shape-1 layer", True, f"test -f {repo / '_meta' / 'tools' / 'validate.py'}",
               f"{len(added)} files added, none overwritten")
    if online and brain and not dry_run:
        for cmd in (["git", "submodule", "deinit", "-f", "brain"], ["git", "rm", "-q", "-f", "brain"]):
            onboard.run_cmd(cmd, dry_run, cwd=repo)
        _rmtree(repo / ".git" / "modules" / "brain")
        onboard.run_cmd(["git", "submodule", "add", "-b", "main", brain, "brain"], dry_run, cwd=repo)
    if dry_run:
        report.add("fill space.yaml", True, "cat space.yaml", "dry run: not written")
        return report
    values = onboard.space_values({"mode": "live"}, onboard.slugify(name), owner, owner_email, list(crew),
                                  "brain", brain, brain_path)
    onboard.setup_space(report, repo, "project", values, [])
    onboard.finish_space(report, repo)
    report.spaces[onboard.slugify(name)] = repo
    return report


def go_live(repo, offline=False, org=None, home=None, dry_run=False) -> onboard.Report:
    """The swapped project goes live: onboard's go-live, then the project mounted in every crew
    member's workspace (on this machine directly, elsewhere through a Needs-you item in the brain)."""
    repo = Path(repo).resolve()
    report = onboard.go_live(repo, {}, offline=offline, org=org, dry_run=dry_run, platform=True)
    space = spaceyaml.load(repo)
    remote = onboard.git_remote(repo)
    mint.mount_in_crew(report, repo, space.name, f"https://github.com/{remote}" if remote else None,
                       mint.crew_of(space), home=home, online=not offline, dry_run=dry_run)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo")
    ap.add_argument("--name")
    ap.add_argument("--owner")
    ap.add_argument("--owner-email", default="")
    ap.add_argument("--crew", nargs="*", default=[])
    ap.add_argument("--brain")
    ap.add_argument("--brain-path", default="brain")
    ap.add_argument("--archive", nargs="*", default=list(ARCHIVE))
    ap.add_argument("--online", action="store_true")
    ap.add_argument("--go-live", action="store_true",
                    help="after the swap has landed: go live and mount the project for its crew")
    ap.add_argument("--offline", action="store_true", help="--go-live without gh, git or the scheduler")
    ap.add_argument("--org")
    ap.add_argument("--home", help="where checkouts made by onboard live (default ~/.spaces)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.go_live:
        report = go_live(args.repo, offline=args.offline, org=args.org, home=args.home, dry_run=args.dry_run)
        onboard.print_steps(report.steps)
        return 1 if report.failed() else 0
    if not args.name or not args.owner:
        ap.error("a swap needs --name and --owner")
    report = run(args.repo, args.name, args.owner, args.crew or [args.owner], args.brain, args.brain_path,
                 args.archive, args.online, args.dry_run, args.owner_email)
    onboard.print_steps(report.steps)
    return 1 if report.failed() else 0


if __name__ == "__main__":
    sys.exit(main())
