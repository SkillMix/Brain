"""Template sync: bring the template's machinery into this space, file by file.

Machinery is everything a company does not change: the tools, their tests, the page
templates, the skills, the workflows, the permission profile, the
conformance page.
Each file is copied when this space still has the version it received last time (or
never had it); a file this space changed is kept and reported. Nothing is ever deleted. A
link is never copied, and a space's path that is a link is never written through.
Provenance lives in `_meta/state/template-version.yaml` (the template commit and the
hash of every file as received).

Where the template comes from is never read from a file a run may write. Without `--from`,
it is `space.yaml`'s `template: owner/name`, else `_meta/state/template-origin.yaml` (both
protected), else the template GitHub recorded when this space's repository was made from one,
else the official template (`OFFICIAL_TEMPLATE`), always on GitHub over https. A provenance
that names another repository stops the sync with the reason (set `template:` to follow it).
When GitHub cannot be asked, a space whose provenance names the official template goes on with
it (trusted code either way) and any other stops rather than guess. A local folder is
a source only when the person names it and adds `--local` (a fixture, or a template being
developed): it is copied as it is, with no release and no yes.

A space follows the template's releases (tags `vX.Y.Z`), never its `main`, unless
`space.yaml` says `template_updates: main`. That is a dev-only mode, for a space that
tests the template: the template's pull requests land on green and only its release tag
is reviewed by a person, so main is unreviewed agent work, copied here with no yes and
run in the next routine. Never set it in a space that holds real records. A space on
releases moves to a newer release only when its
owner said yes to that release (`- <ISO> yes template-<tag>-<commit> by <owner>`, with
the commit's full 40-character id, in this space's `log.md` or in the owner's workspace on
this machine) and only forwards. It stays on its release only while the provenance holds
that release's approval and the yes is still there (for that release, an older yes naming
the commit's first 8 characters still counts: `kept_yes`). The owner's workspace asks once per
release on Needs you; the yes is written into the provenance as `approved`, which lets the
protected pull request land on green when its files are the release's (gate.py).

Releases are read through git, so the full commit id the yes names binds every file of the
release, and a tag counts only on the template's default branch; tag signatures are not
checked yet (the release archive's checksum is checked by `template_archive.py verify`).
Once the owner said yes, the release is copied into the working tree and runs in the next
routine, before its pull request lands: the yes is the consent, not the merge. A sync that
landed new machinery then pins the verdict cut-over by running the copied
`_meta/migrations/verdicts_cutover.py` in a subprocess (`pin_verdicts`; never in a cloud run).

    python template_sync.py [root] [--from owner/name|URL] [--from PATH --local] [--check]
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import no_company_names  # noqa: E402
import pages  # noqa: E402
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
# Said on every run of a space that follows the template's main: that main is agents' work no person
# reviewed (its pull requests land on green; the release tag is the human gate), copied with no yes.
MAIN_NOTE = "follows the template's main: a dev-only mode, with no release and no yes"
# The one question per release, written by this tool (one writer per file).
RELEASE_ITEMS = Path("_inbound") / "needs-you" / "template-release.yaml"
# The yes names the release's full commit: a commit with a given 8-character prefix is found in minutes.
YES_LINE = re.compile(r"^- (\S+) yes template-(v\d+\.\d+\.\d+)-([0-9a-f]{40}) by (.+?)\s*$")
# The yes an older template wrote, with the commit's first 8 characters. It never moves a space
# (`approval` asks for the full id). It only keeps a space on the release it already stands on,
# whose full commit the provenance recorded as `approved` when that yes moved it there
# (`kept_yes`), so a space that upgraded through it stays quiet.
LEGACY_YES_LINE = re.compile(r"^- (\S+) yes template-(v\d+\.\d+\.\d+)-([0-9a-f]{8}) by (.+?)\s*$")
# The template every space follows unless its space.yaml names another (the same as start.OFFICIAL_TEMPLATE).
OFFICIAL_TEMPLATE = no_company_names.official_template()
# Where onboarding records the template a space was made from (protected: gate.MINIMUM_PROTECTED).
ORIGIN = Path("_meta") / "state" / "template-origin.yaml"
# A GitHub `owner/name`: the only form in which a template is read when it comes from a file.
REPO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9._-]{0,99}")


class TemplateRefused(RuntimeError):
    """The sync will not read this template; the message says why and what the person can do."""


class GitHubUnanswered(TemplateRefused):
    """GitHub could not be asked which template this space's repository was made from."""


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


def copyable(rel: str) -> bool:
    """A machinery path this sync copies: no local cache and no compiled module (gate.py asks the same)."""
    return not CACHE_DIRS & set(rel.split("/")) and not rel.endswith((".pyc", ".pyo"))


def machinery_files(root: Path) -> list:
    """The template's machinery files, as plain files. A link is never machinery: copying it would
    copy what it points at (a file of this machine, outside the template) into the space, which
    the sync then lands."""
    out = []
    for entry in MACHINERY:
        base = root / entry
        if base.is_symlink() or _through_link(root, base):
            continue
        if base.is_file():
            out.append(base.relative_to(root).as_posix())
        elif base.is_dir():
            for path in sorted(base.rglob("*")):
                rel = path.relative_to(root).as_posix()
                if path.is_file() and not path.is_symlink() and copyable(rel) and not _through_link(root, path):
                    out.append(rel)
    return [rel for rel in out if not any(rel == n or rel.startswith(n + "/") for n in NEVER)]


def _through_link(root: Path, path: Path) -> bool:
    """A folder between `root` and `path` is a link (or a junction): the file is not the tree's own."""
    root = Path(root)
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return True
    return any((root / parent).is_symlink() for parent in path.relative_to(root).parents if str(parent) != ".")


def _url(source: str) -> str:
    return source if "://" in source else f"https://github.com/{source}"


def fetch_template(source: str, workdir: Path, local: bool = False) -> tuple[Path, str]:
    """A read-only copy of the template: a local folder as it is (only when `local`), or a
    shallow clone of its main."""
    if local:
        head = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True,
                              text=True)
        return Path(source), (head.stdout.strip() if head.returncode == 0 else "local")
    dest = workdir / "template"
    # `--` before the address: a value that starts with `-` is never read as an option
    subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", "main", "--", _url(source), str(dest)],
                   check=True, capture_output=True, text=True)
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True)
    return dest, head.stdout.strip()


def repo_name(value) -> str | None:
    """`owner/name` from `owner/name` or `https://github.com/owner/name(.git)`; None for anything else
    (another host or scheme, a local path, a value that could be read as an option)."""
    text = str(value or "").strip()
    if text.casefold().startswith("https://github.com/"):
        text = text[len("https://github.com/"):]
    text = text.rstrip("/")
    text = text[:-4] if text.endswith(".git") else text
    return text if REPO_NAME.fullmatch(text) else None


def pinned(space) -> str:
    """The template this space follows, from what only the owner's click lands: space.yaml's
    `template:`, else `_meta/state/template-origin.yaml` (both protected), else what GitHub recorded
    when this space's repository was made from a template (`made_from`), else the official
    template. The provenance is never one of them: a run writes it."""
    value = space.data.get("template")
    if value not in (None, ""):
        name = repo_name(value)
        if name is None:
            raise TemplateRefused(f"template sync: space.yaml `template:` {ascii(str(value))} is not a GitHub "
                                  "owner/name; nothing was synced")
        return name
    origin = Path(space.root) / ORIGIN
    if origin.exists() and not pages.is_link(origin):
        try:
            data = yaml.safe_load(origin.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise TemplateRefused(f"template sync: {ORIGIN.as_posix()} does not parse ({exc})") from None
        value = data.get("repo") if isinstance(data, dict) else None
        if value not in (None, ""):
            name = repo_name(value)
            if name is None:
                raise TemplateRefused(f"template sync: {ORIGIN.as_posix()} names {ascii(str(value))}, which is not "
                                      "a GitHub owner/name; nothing was synced")
            return name
    return made_from(space.root) or OFFICIAL_TEMPLATE


def _github_remote(root: Path) -> str | None:
    """owner/name of this checkout's `origin` when it is a GitHub address (https or ssh), read from
    .git/config without calling git; None for any other remote, or none."""
    config = Path(root) / ".git" / "config"
    if not config.is_file():
        return None
    text = config.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', text)
    if not match:
        return None
    url = match.group(1)
    for prefix in ("https://github.com/", "git@github.com:", "ssh://git@github.com/"):
        if url.casefold().startswith(prefix):
            return repo_name(url[len(prefix):])
    return None


# GitHub's record of the template a repository was made from: "owner/name", "owner/name archived" or "null".
TEMPLATE_RECORD = '.template_repository | if . then .full_name + (if .archived then " archived" else "" end) else "null" end'


def made_from(root: Path) -> str | None:
    """The template GitHub recorded when this space's repository was created from one (`gh repo create
    --template`, onboarding's way), or None when it was not, or its origin is not on GitHub. The
    platform writes that record once and no run can change it, and it follows a renamed template to
    its new name. An archived template counts as none: it is a template's former home or a dead one,
    and has no release to offer. A GitHub origin whose record cannot be read stops the sync rather
    than falling back to the official template, which would offer a space made from another
    template the wrong release."""
    remote = _github_remote(root)
    if remote is None:
        return None
    try:
        res = subprocess.run(["gh", "api", f"repos/{remote}", "-q", TEMPLATE_RECORD],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        res = subprocess.CompletedProcess([], 1, "", str(exc))
    if res.returncode != 0:
        said = ascii((res.stderr or "").strip()[:200] or "no answer")
        raise GitHubUnanswered(f"template sync: GitHub did not say which template {remote} was made from "
                              f"({said}); nothing was synced. Record it as `template: owner/name` in "
                              "space.yaml, or sign in with gh and run again")
    found, _, state = (res.stdout or "").strip().partition(" ")
    return repo_name(found) if found and found != "null" and state != "archived" else None


def _is_folder(source: str) -> bool:
    return "://" not in source and Path(source).exists()


def _provenance(root: Path) -> dict:
    path = Path(root) / PROVENANCE
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    except yaml.YAMLError as exc:
        raise TemplateRefused(f"template sync: {PROVENANCE.as_posix()} does not parse ({exc})") from None
    return data if isinstance(data, dict) else {}


def template_source(root: Path, given: str | None, local: bool = False) -> str:
    """Where this run reads the template. A source the person named (`--from`) is used as given, a
    local folder only with `local`. Otherwise the pinned template (`pinned`), never what the
    provenance says: a run writes it, and a template is code this machine runs. A provenance
    naming another repository is refused, not followed. When GitHub cannot say what the space was
    made from, a provenance naming the official template lets the sync go on with that one."""
    root = Path(root)
    if given:
        if _is_folder(given) and not local:
            raise TemplateRefused(f"template sync: {ascii(given)} is a local folder; a local copy is synced only "
                                  "when it is named with --local (a fixture, or a template being developed)")
        return given
    named = _provenance(root).get("repo")
    try:
        source = pinned(spaceyaml.load(root))
    except GitHubUnanswered:
        # GitHub cannot be asked now (gh signed out, no network). A space whose provenance already
        # names the official template goes on with it: that is trusted code, and the provenance can
        # only keep it there, never point it elsewhere. Any other space stops, rather than guess.
        if not OFFICIAL_TEMPLATE or (repo_name(named) or "").casefold() != OFFICIAL_TEMPLATE.casefold():
            raise
        source = OFFICIAL_TEMPLATE
    if named not in (None, "") and (repo_name(named) or "").casefold() != source.casefold():
        raise TemplateRefused(f"template sync: the provenance names {ascii(str(named))}, but this space follows "
                              f"{source} (space.yaml `template:`, else {ORIGIN.as_posix()}, else the template "
                              "GitHub recorded, else the official one); nothing was synced. To follow the other "
                              "one, set `template: owner/name` in space.yaml")
    if not source:   # no brand file names the official template here: never a guess
        raise TemplateRefused(f"template sync: no template is named here and {no_company_names.BRAND_FILE}, which "
                              "names the official one, is missing; nothing was synced")
    return source


# ----- releases ------------------------------------------------------------------------
def _git(cwd, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def version(tag) -> tuple | None:
    match = gate.RELEASE_TAG.match(str(tag or ""))
    return tuple(int(n) for n in match.groups()) if match else None


def item_id(tag: str, commit: str) -> str:
    return f"template-{tag}-{commit}"


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
                out.append({"release": match.group(2), "commit": match.group(3), "by": match.group(4),
                            "at": match.group(1), "in": name})
    return out


def approval(space, tag: str, commit: str) -> dict | None:
    for yes in approvals(space):
        if yes["release"] == tag and commit == yes["commit"]:
            return {"release": tag, "commit": commit, "by": yes["by"], "at": yes["at"], "in": yes["in"]}
    return None


def kept_yes(space, tag: str, commit: str) -> bool:
    """The owner's yes is still there for the release this space stands on: a full-commit yes
    (`approval`), or an older 8-character one whose prefix is that commit's. Asked only when the
    provenance's `approved` already names this release and its full commit, and the tag still
    points there on the template's default branch; a newer release always needs a full yes."""
    if approval(space, tag, commit):
        return True
    for root, _ in _workspaces(space):
        try:
            lines = (Path(root) / "log.md").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            match = LEGACY_YES_LINE.match(line.strip())
            if match and space.owner and gate._person(match.group(4)) == gate._person(space.owner) \
                    and match.group(2) == tag and commit.startswith(match.group(3)):
                return True
    return False


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
    """Each release tag of the template with the commit it points at, when that commit is on the
    template's default branch (`origin/HEAD`): a tag on a branch nobody merged is no release,
    whoever pushed it (gate.py asks the same)."""
    out = {}
    for tag in _git(tpl, "tag", "--list", "v*").stdout.split():
        commit = _git(tpl, "rev-parse", "--verify", "-q", f"refs/tags/{tag}^{{commit}}").stdout.strip()
        on_main = bool(commit) and _git(tpl, "merge-base", "--is-ancestor", commit, "origin/HEAD").returncode == 0
        if version(tag) and on_main:
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
    tpl = workdir / "template"
    subprocess.run(["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout", "--", _url(source), str(tpl)],
                   check=True, capture_output=True, text=True)
    tags = _releases(tpl)
    if previous.get("release") and str(previous.get("release")) not in tags:
        # a release this template never had (a provenance edited to look ahead) places the space by its commit
        previous = {k: v for k, v in previous.items() if k != "release"}
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
        # stays on its release, only with the approval the provenance recorded and the owner's yes still there
        kept = previous.get("approved") if isinstance(previous.get("approved"), dict) else {}
        if str(kept.get("release") or "") == current and str(kept.get("commit") or "") == tags[current] \
                and gate._person(kept.get("by")) == gate._person(space.owner) \
                and kept_yes(space, current, tags[current]):
            target, receipt = current, kept
        else:
            report.note = "; ".join(filter(None, [report.note, f"{current} has no recorded yes: nothing synced"]))
    if target is None:
        return None
    if receipt is not None and target == newest and not check:
        _ask(root, space, None)
    if _git(tpl, "checkout", "--quiet", tags[target]).returncode:
        raise SystemExit(f"template sync: release {target} could not be checked out")
    report.release = target
    return tpl, tags[target], receipt


def run(root, source: str | None = None, check: bool = False, local: bool = False) -> Report:
    """Sync the template's machinery into `root`. Raises TemplateRefused, having copied nothing,
    when the source is not one this space may read (the module's docstring says which are)."""
    root = Path(root).resolve()
    space = spaceyaml.load(root)
    report = Report()
    previous = _provenance(root)
    received = dict(previous.get("files") or {}) if isinstance(previous.get("files"), dict) else {}
    with tempfile.TemporaryDirectory() as tmp:
        folder = bool(source) and local and _is_folder(str(source))
        source = template_source(root, source, local)
        receipt = None
        if folder or str(space.data.get("template_updates") or "releases") == "main":
            tpl, commit = fetch_template(source, Path(tmp), local=folder)
            if not folder:
                report.note = MAIN_NOTE
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
            if dst.is_symlink() or _through_link(root, dst):
                report.kept.append(rel)   # never written through a link: it would land outside the space
                continue
            tpl_hash = sha1(src)
            new_received[rel] = tpl_hash
            if not dst.exists():
                report.added.append(rel)
                if not check:
                    _copy(src, dst, root)
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
                _copy(src, dst, root)
        if not check:
            # A local folder is a one-off source: the space still follows the template it followed.
            repo = source if not folder else (previous.get("repo") or _pinned_or_none(space))
            release = {"release": report.release} if report.release else {}
            approved = {"approved": receipt} if report.release and receipt else {}
            pages.write_text(
                root / PROVENANCE,
                yaml.safe_dump({"repo": repo, **release, "commit": commit, **approved,
                                "synced_at": space.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "files": new_received}, sort_keys=False), root=root)
            _run_line(root, space, report)
            if report.added or report.updated:
                pin_verdicts(root)
    return report


# The migration that begins the verdict store with its cut-over (status.pin_cutover).
CUTOVER_MIGRATION = Path("_meta") / "migrations" / "verdicts_cutover.py"


def pin_verdicts(root: Path) -> bool:
    """After a sync on the computer landed new machinery, pin the verdict cut-over at once by
    running the migration the sync just copied (`_meta/migrations/verdicts_cutover.py`) in a
    subprocess: the status module this process imported is the old one. That pins log.md's
    answers at the moment of the upgrade, before any later line could join. Nothing runs in a
    cloud run (the migration refuses there by itself too), when the space has no such migration,
    when its verdict store has begun, or when either is reached through a link. A pin that fails
    is left to the librarian's next run. True when the migration ran and said it pinned."""
    root = Path(root)
    script, store = root / CUTOVER_MIGRATION, root / "_meta" / "state" / "verdicts.jsonl"
    if os.environ.get("CLAUDE_CODE_REMOTE", "").lower() == "true":
        return False
    if (not script.is_file() or pages.is_link(script) or _through_link(root, script) or store.exists()
            or pages.is_link(store)):
        return False
    try:
        res = subprocess.run([sys.executable, str(script), str(root)], cwd=str(root), capture_output=True,
                             text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0 and store.is_file()


def _pinned_or_none(space) -> str | None:
    """`pinned`, or None when it cannot be read now (a local copy still lands; the next run asks again)."""
    try:
        return pinned(space)
    except TemplateRefused:
        return None


def _copy(src: Path, dst: Path, root: Path) -> None:
    """The template's file `src` written at `dst`, never from a link and never through one: a
    link at the source is refused, and so is one at the destination or any folder on the way
    there (`pages.check_no_links`, which raises UnsafePath and writes nothing)."""
    if pages.is_link(src):
        raise pages.UnsafePath(f"{src.name} is a link in the template; nothing is copied from one")
    data = src.read_bytes()
    pages.check_no_links(dst, root=root)
    dst.parent.mkdir(parents=True, exist_ok=True)
    pages.check_no_links(dst, root=root)   # again, now that the folders exist
    with open(dst, "wb") as handle:
        handle.write(data)


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


def guarded(root, source: str | None = None, local: bool = False) -> Report:
    """Template sync with the tests before and after: a change that lowers the score is put
    back as it was, file by file, and the revert is recorded (vision section 13)."""
    root = Path(root)
    before = _failures(root)
    plan = run(root, source=source, check=True, local=local)
    backup = {}
    for rel in plan.added + plan.updated:
        path = root / rel
        backup[rel] = path.read_bytes() if path.exists() else None
    prov = root / PROVENANCE
    prov_bytes = prov.read_bytes() if prov.exists() else None
    report = run(root, source=source, local=local)
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
    local = "--local" in argv
    argv = [a for a in argv if a != "--local"]
    try:
        if "--guarded" in argv:
            argv = [a for a in argv if a != "--guarded"]
            src = argv[argv.index("--from") + 1] if "--from" in argv else None
            roots = [a for a in argv if not a.startswith("--") and a != src] or ["."]
            report = guarded(roots[0], source=src, local=local)
            print(report.summary() + ("; reverted: the tests scored lower" if report.reverted else ""))
            return 1 if report.reverted else 0
        source = None
        if "--from" in argv:
            source = argv[argv.index("--from") + 1]
        roots = [a for a in argv if not a.startswith("--") and a != source] or ["."]
        report = run(roots[0], source=source, check=check, local=local)
    except TemplateRefused as exc:
        print(str(exc), file=sys.stderr)
        return 1
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
