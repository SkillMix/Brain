"""Template sync: bring the template's machinery into this space, file by file.

Machinery is everything a company does not change: the tools, their tests, the page
templates, the skills, the workflows, the permission profile, the
conformance page.
Each file is copied when this space still has the version it received last time (or
never had it); a file this space changed is kept and reported. Nothing is ever deleted.
Provenance lives in `_meta/state/template-version.yaml` (the template commit and the
hash of every file as received).

A space follows the template's releases (tags `vX.Y.Z`), never its `main`, unless
`space.yaml` says `template_updates: main`. It moves to a newer release only when its
owner said yes to that release (`- <ISO> yes template-<tag>-<commit8> by <owner>` in
this space's `log.md` or in the owner's workspace on this machine) and only forwards.
The owner's workspace asks once per release on Needs you; the yes is written into the
provenance as `approved`, which lets the protected pull request land on green when its
files are the release's (gate.py). A local `--from PATH` is synced as it is, as before.

    python template_sync.py [root] [--from PATH|owner/name] [--check]
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import spaceyaml  # noqa: E402

MACHINERY = ("_meta/tools", "_meta/templates", "_meta/migrations", "_meta/tests/behaviour",
             "_meta/tests/README.md",
             ".claude/skills", ".claude/settings.json", ".github/workflows", "docs/shape-1.md",
             "LICENSE", "README.md", "CLAUDE.md")
NEVER = (".github/CODEOWNERS", ".github/template-marker", "_meta/tests/fixture",
         "_meta/tests/forbidden-names.sha256")
PROVENANCE = Path("_meta") / "state" / "template-version.yaml"
# A space's own files unless it received them from the template: a swapped project keeps its
# README and its licence, and never gets the template's (the swap never adds them either).
OWN_UNLESS_RECEIVED = ("LICENSE", "README.md")
# Added where missing, but a space's own version is never replaced unless it came from here.
ADD_UNLESS_OWN = ("CLAUDE.md",)
# Local caches a test run leaves in the template's working tree are not machinery.
CACHE_DIRS = {"__pycache__", ".pytest_cache"}
# The one question per release, written by this tool (one writer per file).
RELEASE_ITEMS = Path("_inbound") / "needs-you" / "template-release.yaml"
YES_LINE = re.compile(r"^- (\S+) yes template-(v\d+\.\d+\.\d+)-([0-9a-f]{8}) by (.+?)\s*$")


@dataclass
class Report:
    added: list = field(default_factory=list)
    updated: list = field(default_factory=list)
    kept: list = field(default_factory=list)      # changed here, changed there: this space wins
    unchanged: int = 0
    commit: str = ""
    reverted: bool = False
    release: str = ""     # the release this space is on after the run
    note: str = ""        # why it did not move: no release yet, waiting for a yes, ahead

    def summary(self) -> str:
        where = f"template {self.commit[:8]}" + (f" ({self.release})" if self.release else "")
        return (f"template sync: {len(self.added)} added, {len(self.updated)} updated, "
                f"{len(self.kept)} kept (changed here), {self.unchanged} unchanged; {where}"
                + (f"; {self.note}" if self.note else ""))


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


# ----- releases ------------------------------------------------------------------------
def _git(cwd, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def version(tag) -> tuple | None:
    match = gate.RELEASE_TAG.match(str(tag or ""))
    return tuple(int(n) for n in match.groups()) if match else None


def item_id(tag: str, commit: str) -> str:
    return f"template-{tag}-{commit[:8]}"


def _workspaces(space) -> list:
    """This space, then its owner's personal workspaces on this machine (`~/.spaces/spaces.yaml`):
    where the owner's yes to a release is recorded."""
    found = [(space.root, space.name)]
    try:
        rows = (yaml.safe_load((Path.home() / ".spaces" / "spaces.yaml").read_text(encoding="utf-8"))
                or {}).get("spaces") or []
    except (OSError, ValueError, AttributeError):
        rows = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("root"):
            continue
        try:
            other = spaceyaml.load(Path(str(row["root"])))
        except Exception:
            continue
        if other.profile == "personal" and gate._person(other.owner) == gate._person(space.owner) \
                and Path(other.root).resolve() != Path(space.root).resolve():
            found.append((Path(other.root), other.name))
    return found


def approvals(space) -> list:
    """Every yes the space's owner gave to a template release, here or in their workspace."""
    out = []
    for root, name in _workspaces(space):
        try:
            lines = (Path(root) / "log.md").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            match = YES_LINE.match(line.strip())
            if match and space.owner and gate._person(match.group(4)) == gate._person(space.owner):
                out.append({"release": match.group(2), "commit8": match.group(3), "by": match.group(4),
                            "at": match.group(1), "in": name})
    return out


def approval(space, tag: str, commit: str) -> dict | None:
    for yes in approvals(space):
        if yes["release"] == tag and commit.startswith(yes["commit8"]):
            return {"release": tag, "commit": commit, "by": yes["by"], "at": yes["at"], "in": yes["in"]}
    return None


def changelog_summary(text: str, tag: str) -> str:
    """The first paragraph under the release's heading in CHANGELOG.md (`## [0.2.0]` or `## v0.2.0`)."""
    heading = re.compile(rf"^##\s+\[?v?{re.escape(tag.lstrip('v'))}\]?(\s|$)")
    lines = text.splitlines()
    for n, line in enumerate(lines):
        if not heading.match(line):
            continue
        para = []
        for nxt in lines[n + 1:]:
            if nxt.startswith("#") or (not nxt.strip() and para):
                break
            if nxt.strip():
                para.append(nxt.strip())
        return " ".join(para)[:300]
    return ""


def _releases(tpl: Path) -> dict:
    """Each release tag of the template with the commit it points at."""
    out = {}
    for tag in _git(tpl, "tag", "--list", "v*").stdout.split():
        commit = _git(tpl, "rev-parse", "--verify", "-q", f"refs/tags/{tag}^{{commit}}").stdout.strip()
        if version(tag) and commit:
            out[tag] = commit
    return out


def _position(tpl: Path, root: Path, previous: dict) -> str | None:
    """The template commit this space's machinery came from: the recorded one when the template's
    history holds it, else where the template's main stood when the space last synced or, never
    synced, when the space was made (its first commit); None when neither can be read."""
    commit = str(previous.get("commit") or "")
    if commit and _git(tpl, "cat-file", "-e", f"{commit}^{{commit}}").returncode == 0:
        return commit
    when = str(previous.get("synced_at") or "")
    if not when:
        times = _git(root, "log", "--max-parents=0", "--format=%cI", "HEAD").stdout.split()
        when = min(times) if times else ""
    if not when:
        return None
    return _git(tpl, "rev-list", "-1", f"--before={when}", "origin/HEAD").stdout.strip() or None


def _newer(tpl: Path, root: Path, previous: dict, tag: str, commit: str) -> bool:
    """Release `tag` moves this space forwards: a higher version than its release, or a commit that
    descends from where its machinery came from."""
    current = version(previous.get("release"))
    if current:
        return version(tag) > current
    here = _position(tpl, root, previous)
    if here is None:
        return True
    return here != commit and _git(tpl, "merge-base", "--is-ancestor", here, commit).returncode == 0


def _ask(root: Path, space, tpl: Path | None, tag: str = "", commit: str = "", previous: dict | None = None) -> None:
    """One Needs-you item for the newest release without a yes, written where the person reads it:
    their workspace, or this space when no workspace of theirs is on this machine. No tag clears it."""
    if space.profile != "personal" and len(_workspaces(space)) > 1:
        return
    path = root / RELEASE_ITEMS
    if not tag and not path.exists():
        return
    items = []
    if tag:
        summary = changelog_summary(_git(tpl, "show", f"{commit}:CHANGELOG.md").stdout, tag)
        items.append({"id": item_id(tag, commit), "kind": "request",
                      "text": f"Update to {tag}? {summary or 'Its notes are in CHANGELOG.md.'}",
                      "recommendation": "yes: every space you own takes it without another click, where "
                                        "its files are exactly the release's",
                      "default": f"your spaces stay on {(previous or {}).get('release') or 'what they have'}",
                      "fingerprint": commit})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"items": items}, sort_keys=False, allow_unicode=True),
                    encoding="utf-8", newline="\n")


def _pick_release(root: Path, space, source: str, workdir: Path, previous: dict, report: Report,
                  check: bool):
    """The release this space moves to or stays on, checked out, with its approval; None when there
    is nothing to sync. The template's history and tags come without file contents (a blobless
    clone); git's own credentials read a private template."""
    url = source if "://" in source else f"https://github.com/{source}"
    tpl = workdir / "template"
    subprocess.run(["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout", url, str(tpl)],
                   check=True, capture_output=True, text=True)
    tags = _releases(tpl)
    if not tags:
        report.note = "no release yet"
        return None
    newest = max(tags, key=version)
    target = receipt = None
    if _newer(tpl, root, previous, newest, tags[newest]):
        receipt = approval(space, newest, tags[newest])
        if receipt:
            target = newest
        else:
            report.note = f"{newest} waits for a yes"
            if not check:
                _ask(root, space, tpl, newest, tags[newest], previous)
    else:
        if not version(previous.get("release")):
            report.note = f"this space is at or past {newest}"
        if not check:
            _ask(root, space, None)
    current = str(previous.get("release") or "")
    if target is None and tags.get(current) and tags[current] == previous.get("commit"):
        target, receipt = current, previous.get("approved")   # stays on its release
    if target is None:
        return None
    if receipt is not None and target == newest and not check:
        _ask(root, space, None)
    if _git(tpl, "checkout", "--quiet", tags[target]).returncode:
        raise SystemExit(f"template sync: release {target} could not be checked out")
    report.release = target
    return tpl, tags[target], receipt


def run(root, source: str | None = None, check: bool = False) -> Report:
    root = Path(root).resolve()
    space = spaceyaml.load(root)
    report = Report()
    received, previous = {}, {}
    if (root / PROVENANCE).exists():
        previous = yaml.safe_load((root / PROVENANCE).read_text(encoding="utf-8")) or {}
        received = dict(previous.get("files") or {})
    with tempfile.TemporaryDirectory() as tmp:
        source = template_source(root, source)
        receipt = None
        if Path(source).exists() or str(space.data.get("template_updates") or "releases") == "main":
            tpl, commit = fetch_template(source, Path(tmp))
        else:
            picked = _pick_release(root, space, source, Path(tmp), previous, report, check)
            if picked is None:
                if not check:
                    _run_line(root, space, report)
                return report
            tpl, commit, receipt = picked
        report.commit = commit
        new_received = {}
        for rel in machinery_files(tpl):
            if rel in OWN_UNLESS_RECEIVED and rel not in received:
                continue
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
            if rel in ADD_UNLESS_OWN and rel not in received:
                report.kept.append(rel)   # the space's own file, never received from here
                continue
            if rel in received and received[rel] != local_hash:
                report.kept.append(rel)   # this space changed it since it was received
                new_received[rel] = received[rel]
                continue
            report.updated.append(rel)
            if not check:
                shutil.copyfile(src, dst)
        if not check:
            # A local path is a one-off source; remember the repository name when we have one.
            repo_name = source if not Path(source).exists() else previous.get("repo", source)
            release = {"release": report.release} if report.release else {}
            approved = {"approved": receipt} if report.release and receipt else {}
            (root / PROVENANCE).parent.mkdir(parents=True, exist_ok=True)
            (root / PROVENANCE).write_text(
                yaml.safe_dump({"repo": repo_name, **release, "commit": commit, **approved,
                                "synced_at": space.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "files": new_received}, sort_keys=False),
                encoding="utf-8", newline="\n")
            _run_line(root, space, report)
    return report


def _failures(root: Path) -> int | None:
    """The answer and leak tests' failures, or None when this space has neither."""
    try:
        import tests_run
    except ImportError:
        return None
    total, ran = 0, False
    for name, rel in (("answers", "_meta/tests/answers.yaml"), ("leaks", "_meta/tests/leaks.yaml")):
        if (root / rel).exists():
            try:
                result = getattr(tests_run, name)(root, record=False)
            except Exception:
                return None
            total += int(getattr(result, "failed", 0) or 0)
            ran = True
    return total if ran else None


def guarded(root, source: str | None = None) -> Report:
    """Template sync with the tests before and after: a change that lowers the score is put
    back as it was, file by file, and the revert is recorded (vision section 13)."""
    root = Path(root)
    before = _failures(root)
    plan = run(root, source=source, check=True)
    backup = {}
    for rel in plan.added + plan.updated:
        path = root / rel
        backup[rel] = path.read_bytes() if path.exists() else None
    prov = root / PROVENANCE
    prov_bytes = prov.read_bytes() if prov.exists() else None
    report = run(root, source=source)
    after = _failures(root)
    if before is not None and after is not None and after > before:
        for rel, data in backup.items():
            path = root / rel
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(data)
        if prov_bytes is None:
            prov.unlink(missing_ok=True)
        else:
            prov.write_bytes(prov_bytes)
        report.reverted = True
        import json
        with (root / "_meta" / "state" / "runs.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"routine": "template-sync", "at": spaceyaml.load(root).now().strftime(
                "%Y-%m-%dT%H:%M:%SZ"), "space": spaceyaml.load(root).name, "cost_tokens": 0,
                "notes": {"reverted": True, "guarded": True, "failures_before": before,
                          "failures_after": after}}) + "\n")
    else:
        import json
        with (root / "_meta" / "state" / "runs.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"routine": "template-sync", "at": spaceyaml.load(root).now().strftime(
                "%Y-%m-%dT%H:%M:%SZ"), "space": spaceyaml.load(root).name, "cost_tokens": 0,
                "notes": {"guarded": True, "failures_before": before, "failures_after": after}}) + "\n")
    return report


def _run_line(root: Path, space, report: Report) -> None:
    import json
    line = {"routine": "template-sync", "at": space.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "space": space.name, "cost_tokens": 0,
            "notes": {"added": len(report.added), "updated": len(report.updated),
                      "kept": report.kept, "commit": report.commit[:8],
                      **({"release": report.release} if report.release else {}),
                      **({"note": report.note} if report.note else {})}}
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
    if "--guarded" in argv:
        argv = [a for a in argv if a != "--guarded"]
        src = argv[argv.index("--from") + 1] if "--from" in argv else None
        roots = [a for a in argv if not a.startswith("--") and a != src] or ["."]
        report = guarded(roots[0], source=src)
        print(report.summary() + ("; reverted: the tests scored lower" if report.reverted else ""))
        return 1 if report.reverted else 0
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
