"""Template sync: bring the template's machinery into this space, file by file.

Machinery is everything a company does not change: the tools, their tests, the page
templates, the skills, the workflows, the permission profile, the conformance page.
Each file is copied when this space still has the version it received last time (or
never had it); a file this space changed is kept and reported. Nothing is ever deleted.
Provenance lives in `_meta/state/template-version.yaml` (the template commit and the
hash of every file as received).

    python template_sync.py [root] [--from PATH|owner/name] [--check]
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spaceyaml  # noqa: E402

MACHINERY = ("_meta/tools", "_meta/templates", "_meta/migrations", "_meta/tests/behaviour",
             "_meta/tests/README.md",
             ".claude/skills", ".claude/settings.json", ".github/workflows", "docs/shape-1.md",
             "LICENSE", "README.md")
NEVER = (".github/CODEOWNERS", ".github/template-marker", "_meta/tests/fixture",
         "_meta/tests/forbidden-names.sha256")
PROVENANCE = Path("_meta") / "state" / "template-version.yaml"
# Local caches a test run leaves in the template's working tree are not machinery.
CACHE_DIRS = {"__pycache__", ".pytest_cache"}


@dataclass
class Report:
    added: list = field(default_factory=list)
    updated: list = field(default_factory=list)
    kept: list = field(default_factory=list)      # changed here, changed there: this space wins
    unchanged: int = 0
    commit: str = ""

    def summary(self) -> str:
        return (f"template sync: {len(self.added)} added, {len(self.updated)} updated, "
                f"{len(self.kept)} kept (changed here), {self.unchanged} unchanged; template {self.commit[:8]}")


def sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def machinery_files(root: Path) -> list:
    out = []
    for entry in MACHINERY:
        base = root / entry
        if base.is_file():
            out.append(base.relative_to(root).as_posix())
        elif base.is_dir():
            for path in sorted(base.rglob("*")):
                if path.is_file() and not CACHE_DIRS & set(path.parts) and path.suffix != ".pyc":
                    out.append(path.relative_to(root).as_posix())
    return [rel for rel in out if not any(rel == n or rel.startswith(n + "/") for n in NEVER)]


def fetch_template(source: str, workdir: Path) -> tuple[Path, str]:
    """A read-only copy of the template: a local path as it is, or a shallow clone."""
    local = Path(source)
    if local.exists():
        head = subprocess.run(["git", "-C", str(local), "rev-parse", "HEAD"], capture_output=True,
                              text=True)
        return local, (head.stdout.strip() if head.returncode == 0 else "local")
    url = source if "://" in source else f"https://github.com/{source}"
    dest = workdir / "template"
    subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", "main", url, str(dest)],
                   check=True, capture_output=True, text=True)
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True)
    return dest, head.stdout.strip()


def template_source(root: Path, given: str | None) -> str:
    if given:
        return given
    data = yaml.safe_load((root / PROVENANCE).read_text(encoding="utf-8")) if (root / PROVENANCE).exists() else {}
    if isinstance(data, dict) and data.get("repo"):
        return str(data["repo"])
    try:
        import onboard  # noqa: E402
        found = onboard.template_repo(root)
        if found:
            return found
    except Exception:
        pass
    raise SystemExit("template unknown: pass --from owner/name or a path")


def run(root, source: str | None = None, check: bool = False) -> Report:
    root = Path(root).resolve()
    space = spaceyaml.load(root)
    report = Report()
    received = {}
    if (root / PROVENANCE).exists():
        data = yaml.safe_load((root / PROVENANCE).read_text(encoding="utf-8")) or {}
        received = dict(data.get("files") or {})
    with tempfile.TemporaryDirectory() as tmp:
        source = template_source(root, source)
        tpl, commit = fetch_template(source, Path(tmp))
        report.commit = commit
        new_received = {}
        for rel in machinery_files(tpl):
            src, dst = tpl / rel, root / rel
            tpl_hash = sha1(src)
            new_received[rel] = tpl_hash
            if not dst.exists():
                report.added.append(rel)
                if not check:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, dst)
                continue
            local_hash = sha1(dst)
            if local_hash == tpl_hash:
                report.unchanged += 1
                continue
            if rel in received and received[rel] != local_hash:
                report.kept.append(rel)   # this space changed it since it was received
                new_received[rel] = received[rel]
                continue
            report.updated.append(rel)
            if not check:
                shutil.copyfile(src, dst)
        if not check:
            previous = {}
            if (root / PROVENANCE).exists():
                previous = yaml.safe_load((root / PROVENANCE).read_text(encoding="utf-8")) or {}
            # A local path is a one-off source; remember the repository name when we have one.
            repo_name = source if not Path(source).exists() else previous.get("repo", source)
            (root / PROVENANCE).parent.mkdir(parents=True, exist_ok=True)
            (root / PROVENANCE).write_text(
                yaml.safe_dump({"repo": repo_name, "commit": commit,
                                "synced_at": space.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "files": new_received}, sort_keys=False),
                encoding="utf-8", newline="\n")
            _run_line(root, space, report)
    return report


def _run_line(root: Path, space, report: Report) -> None:
    import json
    line = {"routine": "template-sync", "at": space.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "space": space.name, "cost_tokens": 0,
            "notes": {"added": len(report.added), "updated": len(report.updated),
                      "kept": report.kept, "commit": report.commit[:8]}}
    path = root / "_meta" / "state" / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(line) + "\n")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    check = "--check" in argv
    source = None
    if "--from" in argv:
        source = argv[argv.index("--from") + 1]
    roots = [a for a in argv if not a.startswith("--") and a != source] or ["."]
    report = run(roots[0], source=source, check=check)
    print(report.summary())
    for rel in report.added:
        print(f"  added   {rel}")
    for rel in report.updated:
        print(f"  updated {rel}")
    for rel in report.kept:
        print(f"  kept    {rel} (changed in this space; the template's version was not applied)")
    return 1 if check and (report.added or report.updated) else 0


if __name__ == "__main__":
    sys.exit(main())
