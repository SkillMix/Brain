"""onboard: one verb, three cases. Sets a space up and proves it (shape 1).

    python _meta/tools/onboard.py [root] [--answers FILE] [--brain URL|PATH] [--seed PATH]
                                  [--offline] [--org ORG] [--home DIR]
                                  [--user-instructions PATH] [--check] [--fixture DIR] [--dry-run]
    python _meta/tools/onboard.py <workspace> --routines-here | --pause [--until ISO] | --resume
    python _meta/tools/onboard.py <workspace> --routines-cloud --check
                                  | --routines-cloud --sweep <task> --timezone <zone>

Cases: no brain in reach, a company (this repository becomes the brain and the top
owner's workspace is created); a brain in reach, a person (this repository becomes their
workspace); a desk seed, a project (handed to `mint.py`). `--check` re-runs only the
read-back checks and changes nothing. `--fixture DIR` onboards the fixture company
offline and runs the chain and the tests over it. Offline mode never runs gh, git,
schtasks, launchctl, crontab or systemctl; online, every such call goes through `run_cmd`. Nothing is
deleted, nothing is signed, `main` is pushed only for a repository created seconds earlier. The OS tasks it
registers are named `space-sync-<name>` and `space-<routine>-<name>`; an isolated test run sets
SPACES_TASK_PREFIX=space-test-<id>- so that its tasks, and only those, carry that prefix instead, and a
computer where another account holds those names keeps its own (`~/.spaces/settings.yaml`). Each
runs through the scheduler of the system that catches up after sleep: the Task Scheduler on Windows, a
launch agent (`com.spaces.<task>`) on macOS, a systemd user timer on a Linux that has one, else cron,
which does not catch up and says so.

A workspace's routines run on one computer through one scheduler (`routine.HOST`): the build registers
them only where nothing else runs them, `--routines-here` moves them to this computer once the probe
passed there, and `--pause` (until 06:00 the next morning, or `--until`) and `--resume` hold them.
The sweep alone can run in the cloud, read-only: the computer the move ran on keeps the daily chain,
which sends the mail, and a pause reaches the cloud once the sync lands it. `--routines-cloud --check`
names what keeps the sweep from moving there, and the task it already runs as, or that a proof stopped
it; `--routines-cloud` with the task and the person's zone moves it, once the task's newest proof run
showed every refusal. While it runs there, `--check` names the task and when the sweep last wrote a
run line, or that a proof stopped it.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import getpass
import io
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import doctor  # noqa: E402
import pages  # noqa: E402
import registry  # noqa: E402
import spaceyaml  # noqa: E402
import validate  # noqa: E402

TOOLS = Path(__file__).resolve().parent
TEMPLATE_ROOT = TOOLS.parents[1]
TEMPLATES = TEMPLATE_ROOT / "_meta" / "templates"
FIXTURE = TEMPLATE_ROOT / "_meta" / "tests" / "fixture"
MARK_OPEN = "<!-- space:personal-instruction -->"
MARK_CLOSE = "<!-- /space:personal-instruction -->"
FEEDS = ("mail", "calendar", "chat", "calls", "documents", "tracker", "repository")
PROFILE_CASE = {"brain": "company", "personal": "person", "project": "project", "channel": "person"}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "personal", "academic", "_scratch"}
# Where a space mounts other spaces: a new space mounts them itself, it never copies them.
MOUNT_DIRS = ("brain", "mounts")
# Never copied from the space a new one is made from: its fixture, its settings and the company's
# own test questions (they name real facts).
# The template's files as an open-source project: a company's private space never presents them
# (template sync never carries them either; it reads CHANGELOG.md from the release itself).
PROJECT_FILES = ("CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", "CODE_OF_CONDUCT.md",
                 ".github/ISSUE_TEMPLATE", ".github/PULL_REQUEST_TEMPLATE.md", "requirements.txt",
                 "requirements-dev.txt")
SKIP_PATHS = ("_meta/tests/fixture", ".github/template-marker", "_meta/state/feed", "AGENTS.md",
              "space.yaml", "_meta/tests/answers.yaml", "_meta/tests/leaks.yaml",
              "_meta/search.yaml") + PROJECT_FILES
# Content never travels into a new space: a space made from another (offline, or a corridor)
# takes only its machinery and empty skeleton. `live/` is all generated content; `docs/` keeps
# only the conformance page.
CONTENT_DIRS = ("knowledge", "live", "work", "journal", "outbox", "identity",
                "_inbound", "_outbound", "_meta/state", "docs")
KEEP_ALWAYS = ("docs/shape-1.md",)
SUBDIRS = ("_inbound/candidates", "_inbound/checks", "_inbound/questions", "_inbound/needs-you",
           "_outbound/queue", "_outbound/held", "_outbound/requests", "_outbound/signals",
           "_meta/state", "knowledge/sources", "knowledge/decisions", "live/subjects")
FRESH = {
    "space.yaml": "# Not configured yet: onboard or mint fills this file for the profile the space takes.\n"
                  "shape: 1\nprofile: \"\"\n",
    "log.md":"---\ntitle: Log\n---\n# Log\n\nAppend-only: signatures, landings, reviews. One line each.\n",
    "index.md": "---\ntitle: Index\n---\n# Index\n\nEvery walk starts here: a kind, then a subject, "
                "then its pages.\n\n<!-- subjects -->\n<!-- /subjects -->\n",
    "_meta/subjects.yaml": "subjects: []\n",
    "_meta/access/resources.yaml": "# Connected systems, each with the audience that can already "
                                   "read its records.\nresources: []\n",
    "_meta/questions.log": "# Question log (one JSON object per line; append only)\n",
}


@dataclass
class Step:
    name: str
    done: bool
    check: str
    result: str = ""
    needs_owner: bool = False

    def row(self) -> dict:
        return {"name": self.name, "done": self.done, "check": self.check, "result": self.result,
                "needs_owner": self.needs_owner}


@dataclass
class Report:
    case: str
    steps: list = field(default_factory=list)
    spaces: dict = field(default_factory=dict)
    user_instructions: Path | None = None
    progress: object = None   # called with (report, step) after every step: the onboarding page

    def add(self, name, done, check, result="", needs_owner=False) -> Step:
        step = Step(name, bool(done), check, str(result), needs_owner)
        self.steps.append(step)
        if self.progress is not None:
            self.progress(self, step)
        return step

    def failed(self) -> list:
        return [s for s in self.steps if not s.done and not s.needs_owner]


# ----- the one door to the outside -----------------------------------------
def run_cmd(argv, dry_run=False, cwd=None) -> subprocess.CompletedProcess:
    """gh, git, schtasks, launchctl, crontab and systemctl all pass through here. Offline mode never calls it."""
    argv = [str(a) for a in argv]
    if dry_run:
        print("dry-run:", " ".join(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=False, cwd=cwd)
    except OSError as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def spawn_cmd(argv, dry_run=False, env=None) -> int | None:
    """The other door to the outside: start a command and leave it running on its own, in a session of its
    own (it outlives whoever asked), its output nowhere. Returns its process id, or None when it would not
    start. Only cron has no `start` of its own and needs it (`run_now`)."""
    argv = [str(a) for a in argv]
    if dry_run:
        print("dry-run:", " ".join(argv))
        return 0
    try:
        return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=env, start_new_session=True).pid
    except OSError:
        return None


# ----- small helpers ---------------------------------------------------------
def write_text(path, text) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def read_yaml(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return default if data is None else data


def write_yaml(path, data) -> None:
    write_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100))


def slugify(text) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-") or "space"


def humanize(slug) -> str:
    return " ".join(w.capitalize() for w in re.split(r"[-_\s]+", str(slug)) if w)


def parse_list(value, default=None, sep=",") -> list:
    """A list answer; categories (sentences that hold commas) are split on `;`."""
    if value is None or value == "" or (isinstance(value, str) and value.strip().lower() == "default"):
        return list(default or [])
    if isinstance(value, list):
        return list(value)
    items = [v.strip() for v in str(value).split(sep) if v.strip()]
    return [] if [i.lower() for i in items] == ["none"] else items


def parse_pairs(value, key, other, default) -> list:
    """`a, b: x` or [{key: a, other: x}] -> [{key: a, other: default}, {key: b, other: x}]."""
    out = []
    for item in parse_list(value):
        if isinstance(item, dict):
            first = item.get(key) or item.get("name") or item.get("id")
            second = item.get(other) or default
        else:
            first, _, second = str(item).partition(":")
            second = second.strip() or default
        if first:
            out.append({key: str(first).strip(), other: second})
    return out


def git_remote(root) -> str | None:
    """owner/name of the `origin` remote, read from .git/config without calling git."""
    config = Path(root) / ".git" / "config"
    if not config.exists():
        return None
    text = config.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', text)
    if not match:
        return None
    url = match.group(1).rstrip("/")
    url = url[:-4] if url.endswith(".git") else url
    tail = re.split(r"[:/]", url)[-2:]
    return "/".join(tail) if len(tail) == 2 and all(tail) else None


def template_repo(root, offline=False) -> str | None:
    """The template this repository came from: the origin onboarding recorded
    (`_meta/state/template-origin.yaml`), else, unless `offline`, what GitHub recorded when the
    repository was made from a template. Only an `offline` look (start.py, whose root is the copy
    the agent cloned of the template) at a copy still the template's own takes its origin remote as
    the template; a space's own remote is never its template, so otherwise None."""
    data = read_yaml(Path(root) / "_meta" / "state" / "template-origin.yaml", {}) or {}
    if data.get("repo"):
        return data["repo"]
    remote = git_remote(root)
    if remote and not offline:
        try:
            import template_sync   # an archived template counts as none (template_sync.made_from)
            res = run_cmd(["gh", "api", f"repos/{remote}", "-q", template_sync.TEMPLATE_RECORD])
            parent, _, state = (res.stdout or "").strip().partition(" ")
            if res.returncode == 0 and parent and parent != "null" and state != "archived":
                return parent
        except Exception:
            pass
        return None
    yaml_file = Path(root) / "space.yaml"
    text = yaml_file.read_text(encoding="utf-8", errors="replace") if yaml_file.is_file() else None
    return remote if remote and spaceyaml.still_template(text) else None


def sources_of(feeds) -> list:
    return [{"name": f["name"], "adapter": "files", "audience": f["audience"]} for f in feeds or []]


def minutes_of(value) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else 15


# ----- the tree ----------------------------------------------------------------
def _checkout_files(src: Path) -> list | None:
    """What a fresh clone of `src` holds plus what is not committed yet: the files git lists as
    tracked, or untracked and not ignored. None when `src` is not a git checkout (a plain copy)."""
    try:
        res = subprocess.run(["git", "-C", str(src), "ls-files", "-z", "-co", "--exclude-standard"],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return [src / name for name in dict.fromkeys(res.stdout.decode("utf-8", "replace").split("\0")) if name]


def copy_template(src, dst) -> bool:
    """Copy the machinery and the empty skeleton of `src` to `dst`; never its content. In a git
    checkout, never what its git ignores either (a tool's scratch folder, a worktree)."""
    src, dst = Path(src), Path(dst)
    if (dst / "space.yaml").exists():
        return False
    listed = _checkout_files(src)
    for path in (src.rglob("*") if listed is None else listed):
        rel = path.relative_to(src)
        posix = rel.as_posix()
        if not path.is_file() or any(p in SKIP_DIRS for p in rel.parts):
            continue
        if rel.parts[0] in MOUNT_DIRS:  # another space's checkout: never copied as files
            continue
        if any(posix == s or posix.startswith(s + "/") for s in SKIP_PATHS):
            continue
        if any(posix.startswith(c + "/") for c in CONTENT_DIRS) and posix not in KEEP_ALWAYS:
            if path.name != ".gitkeep" and not (path.name == "index.md" and len(rel.parts) == 2):
                continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    for rel, text in FRESH.items():
        write_text(dst / rel, text)
    return True


def _keep(folder: Path) -> None:
    """A folder git keeps while it is empty."""
    folder.mkdir(parents=True, exist_ok=True)
    if not any(folder.iterdir()):
        write_text(folder / ".gitkeep", "")


def ensure_required(dst, profile) -> None:
    """The profile's required files and folders and the zone folders. The template ships none of
    them, so a repository made from it gets each one here, kept by a `.gitkeep` while empty."""
    dst = Path(dst)
    for sub in SUBDIRS if profile != "channel" else SUBDIRS[:8]:
        _keep(dst / sub)
    for entry in spaceyaml.PROFILE_TABLE[profile]["required"]:
        target = dst / entry
        if "." in Path(entry).name:
            if not target.exists():
                write_text(target, FRESH.get(entry, f"---\ntitle: {Path(entry).stem}\n---\n"))
        else:
            _keep(target)


def remove_forbidden(report, dst, profile) -> None:
    """Drop folders the profile forbids, but only when they hold nothing but .gitkeep."""
    dst = Path(dst)
    for entry in spaceyaml.PROFILE_TABLE[profile]["forbidden"]:
        folder = dst / entry
        if not folder.exists():
            continue
        content = [p for p in folder.rglob("*") if p.is_file() and p.name != ".gitkeep"]
        if content:
            report.add(f"remove {entry}/ from {dst.name}", False, f"test ! -e {folder}",
                       f"{len(content)} files inside; a {profile} space has no {entry}/, move them first",
                       needs_owner=True)
        else:
            shutil.rmtree(folder)
    marker = dst / spaceyaml.TEMPLATE_MARKER
    if marker.exists():   # a fresh copy of the template (a repository made from it online)
        for rel in PROJECT_FILES:
            path = dst / rel
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        marker.unlink()


def space_values(answers, name, owner, owner_email, audience, parent, brain_repo=None,
                 brain_path="brain", standing_choices=None, feeds=None, mounts=None) -> dict:
    # The brain mount is always the one given here (the sentence's brain): another mount named `brain`,
    # from a crew list an invitation carried, is dropped, so it can never stand in for the company layer.
    mounts = [m for m in (mounts or []) if m.get("name") != "brain"]
    mounts.insert(0, {"name": "brain", "repo": brain_repo, "path": brain_path, "branch": "main"})
    return {"name": name, "owner": owner or "", "owner_email": owner_email or "", "mounts": mounts,
            "audience": audience, "parent": parent, "mode": answers.get("mode") or "live",
            "time_scale": answers.get("time_scale"),
            "kinds": parse_list(answers.get("kinds"), spaceyaml.DEFAULTS["kinds"]),
            "never_leaves": parse_list(answers.get("never_leaves"), spaceyaml.DEFAULTS["never_leaves"], ";"),
            "left_out": list(spaceyaml.DEFAULTS["left_out"]),
            "protected": list(spaceyaml.DEFAULTS["protected"]),
            "standing_choices": list(standing_choices or []), "sources": sources_of(feeds),
            "brain_repo": brain_repo, "brain_path": brain_path}


def fill_space(dst, profile, **values) -> dict:
    """Write dst/space.yaml from _meta/templates/space-<profile>.yaml."""
    text = (TEMPLATES / f"space-{profile}.yaml").read_text(encoding="utf-8")
    fields = {k: json.dumps(v, ensure_ascii=False) for k, v in values.items()}
    extra = []
    if values.get("time_scale") is not None and values.get("mode") in ("shadow", "fixture"):
        extra.append(f"time_scale: {values['time_scale']}")
    if profile == "personal" and values.get("mode", "live") == "live":
        # the judgment router reads the last 90 days and is live from day one (the owner's
        # choice of 2026-10-14); `review_until` stays available for whoever wants a review period
        today = dt.datetime.now(dt.timezone.utc).date()
        extra.append(f"routing: {{since: '{today - dt.timedelta(days=90)}'}}")
    fields["extra"] = "\n".join(extra)
    text = text.format(**fields).rstrip("\n") + "\n"
    write_text(Path(dst) / "space.yaml", text)
    return yaml.safe_load(text)


def begin_verdicts(dst) -> bool:
    """Begin a new space's verdict store with its cut-over (`status.pin_cutover`), right after its
    log.md is written: a new space has no earlier answers to keep, so the pin is empty, and no line
    written to its log before its first status run can ever count as an answer. True when the row
    was written; a store already there, a cloud run or a log.md that cannot be read safely leave
    it to the next run that can pin (pin_cutover's own refusals)."""
    import status
    return status.pin_cutover(dst)


def record_template_origin(dst, tpl, dry_run=False) -> bool:
    """A space made from a template other than the official one records it in
    `_meta/state/template-origin.yaml` (`repo: <owner/name>`) before its first commit, so its
    template sync follows that template without asking GitHub (`template_sync.pinned`; the file is
    protected, gate.MINIMUM_PROTECTED). Only a plain GitHub owner/name is recorded: the official
    template, a local folder (a fixture) and a dry run write nothing. True when the file was written."""
    import template_sync
    name = template_sync.repo_name(tpl)
    if dry_run or name is None or name.casefold() == template_sync.OFFICIAL_TEMPLATE.casefold():
        return False
    try:
        pages.write_text(Path(dst) / template_sync.ORIGIN, yaml.safe_dump({"repo": name}, sort_keys=False),
                         root=Path(dst))
    except pages.UnsafePath:   # a link in the way: nothing is written through it
        return False
    return True


def setup_space(report, dst, profile, values, feeds) -> None:
    """Fill a space for its profile unless it is configured already; shape its tree. A space filled
    here for the first time begins its verdict store with an empty cut-over (`begin_verdicts`)."""
    dst = Path(dst)
    current = read_yaml(dst / "space.yaml", {}) or {}
    if current.get("profile") in (None, "", spaceyaml.TEMPLATE_PROFILE):
        fill_space(dst, profile, **values)
        result = "written"
    else:
        result = "kept: already configured"
    ensure_required(dst, profile)
    if result == "written":
        begin_verdicts(dst)
    remove_forbidden(report, dst, profile)
    if feeds:
        write_yaml(dst / "_meta" / "access" / "resources.yaml", {"resources": sources_of(feeds)})
    report.add(f"fill {dst.name} ({profile})", True, f"python _meta/tools/validate.py {dst}", result)


def create_space(report, root, dirname, repo_name, offline, org, home, tpl, dry_run):
    """The directory of a new space: offline a copy under `home` (default: a sibling), online a
    clone of a new repository made from the template `tpl`. A space it makes from a template that
    is not the official one records it (`record_template_origin`); a space it adopts or finds
    already there is left as it is. A dry run writes nothing: it only says where the space would go."""
    if offline:
        dst = (Path(home) if home else Path(root).parent) / dirname
        made = "would copy" if dry_run else "copied" if copy_template(root, dst) else "exists"
        if made == "copied":
            record_template_origin(dst, tpl)
        report.add(f"create {dirname}", True, f"test -f {dst / 'space.yaml'}", made)
        return dst, None
    base = Path(home) if home else Path.home() / ".spaces"
    if not dry_run:
        base.mkdir(parents=True, exist_ok=True)
    dst, full = base / repo_name, f"{org}/{repo_name}"
    if (dst / "space.yaml").exists():
        report.add(f"create {dirname}", True, f"gh repo view {full}", f"exists at {dst}")
        return dst, full
    if not org or not tpl:
        report.add(f"create {dirname}", False, f"gh repo view {full}", "organization or template repository "
                   "unknown: pass --org and write _meta/state/template-origin.yaml (repo: owner/name)",
                   needs_owner=True)
        return None, full
    if full.lower() == re.sub(r"^https://github\.com/|\.git$", "", str(tpl).strip().rstrip("/")).lower():
        # host names ignore case: Acme/brain is the template Acme/Brain, never a space to adopt
        report.add(f"create {dirname}", False, f"gh repo view {full}", f"refused: {full} is the template "
                   "itself; say the sentence again with --org <your account or organisation>", needs_owner=True)
        return None, full
    if not dry_run and run_cmd(["gh", "repo", "view", full, "--json", "name"], dry_run, cwd=base).returncode == 0:
        # the name is taken: an earlier onboarding's space is adopted, anything else is the owner's call
        is_space = run_cmd(["gh", "api", f"repos/{full}/contents/space.yaml", "-q", ".path"],
                           dry_run, cwd=base).returncode == 0
        if is_space:
            res = run_cmd(["gh", "repo", "clone", full, str(dst)], dry_run, cwd=base)
            ok = res.returncode == 0 and (dst / "space.yaml").exists()
            report.add(f"create {dirname}", ok, f"gh repo view {full}",
                       f"adopted the existing space, cloned into {dst}" if ok
                       else (res.stderr or res.stdout).strip()[:200])
            return (dst if ok else None), full
        report.add(f"create {dirname}", False, f"gh repo view {full}",
                   f"a repository named {full} already exists and is not a space; name this space "
                   f"differently (recommendation: {repo_name}-space) or move that repository; until "
                   "then the subject stays home in the brain", needs_owner=True)
        return None, full
    res = run_cmd(["gh", "repo", "create", full, "--template", tpl, "--private", "--clone"], dry_run, cwd=base)
    ok = res.returncode == 0 and (dry_run or (dst / "space.yaml").exists())
    if ok:
        record_template_origin(dst, tpl, dry_run)
    report.add(f"create {dirname}", ok, f"gh repo view {full}",
               f"{'would clone' if dry_run else 'cloned'} into {dst}" if ok else (res.stderr or res.stdout).strip()[:200])
    return (dst if ok else None), full


def unconfigured_remote(dst, dry_run) -> bool:
    """True while origin/main still carries the template's own space.yaml (or none at all; `spaceyaml.still_template`)."""
    if dry_run:
        return True
    run_cmd(["git", "fetch", "-q", "origin", "main", "--no-recurse-submodules"], dry_run, cwd=dst)
    shown = run_cmd(["git", "show", "origin/main:space.yaml"], dry_run, cwd=dst)
    return spaceyaml.still_template((shown.stdout or "") if shown.returncode == 0 else None)


def commit_fill(report, dst, push, dry_run, repo=None) -> None:
    """Commit the first fill; push it to main while the repository is still unconfigured.

    The fill is the owner's own setup action, so it does not wait for the owner's click like
    a later machinery change would (protected paths). Once main carries a profile, every
    further change goes through the sync script and the platform. `repo` is the repository
    (owner/name) the caller made, when it knows it (`hold_admins`).
    """
    run_cmd(["git", "add", "-A"], dry_run, cwd=dst)
    res = run_cmd(["git", "commit", "-q", "-m", "chore: onboard"], dry_run, cwd=dst)
    note, pushed = "committed; the sync script lands it", False
    done = res.returncode == 0 or "nothing to commit" in (res.stdout + res.stderr)
    if push and unconfigured_remote(dst, dry_run):
        out = run_cmd(["git", "push", "-q", "origin", "HEAD:main"], dry_run, cwd=dst)
        said = " ".join(((out.stderr or "") + (out.stdout or "")).split())[:300]
        if out.returncode != 0:   # main stays the template's: the build is not done (rc1 run, issue 12)
            done, note = False, f"the first push to main failed, so main is still the template's: {said or 'git gave no reason'}"
        elif not dry_run and unconfigured_remote(dst, dry_run):
            done, note = False, "the first push to main went through, but main is still the template's"
        else:
            note, pushed = "bootstrap push to main", True
    report.add(f"commit {Path(dst).name}", done, f"git -C {dst} status --short", note)
    if pushed:
        hold_admins(report, dst, dry_run, repo)


def hold_admins(report, dst, dry_run, repo=None) -> None:
    """Once the first fill is on main, the checks bind the person's own account too (`enforce_admins`): a run acting
    as them, a cloud run among them, then never pushes to main past the checks. The platform settings left it off
    only for that one push. A main with no protection yet (a project minted before its platform settings, which
    hold admins themselves) adds no step. The repository is `repo`, else origin's as `.git/config` names it; when
    neither says which, the hold is one failing row, never a gap nobody sees."""
    repo = repo or git_remote(dst)
    if not repo:
        report.add("admins held to the checks", False,
                   "gh api repos/<owner>/<name>/branches/main/protection/enforce_admins -q .enabled",
                   "repository unknown, so your own account may still push to main past the checks: hold it with "
                   "gh api -X POST repos/<owner>/<name>/branches/main/protection/enforce_admins")
        return
    res = run_cmd(["gh", "api", "-X", "POST", f"repos/{repo}/branches/main/protection/enforce_admins"], dry_run)
    said = f"{res.stdout} {res.stderr}".strip()
    if res.returncode != 0 and re.search(r"not protected", said, re.I):
        return
    report.add(f"admins held to the checks {repo}", res.returncode == 0,
               f"gh api repos/{repo}/branches/main/protection/enforce_admins -q .enabled",
               "your own account cannot push to main past the checks either" if res.returncode == 0 else said[:200])


MOUNT_URL = re.compile(r"https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?")


def mount_problem(repo, path) -> str | None:
    """Why a mount may not be added, or None: the repository must be an https GitHub address (never an
    option, a local path or another host), and the path `brain` or `mounts/<name>`, one plain folder,
    so a submodule can never land in `.claude/`, `.github/`, `_meta/` or any other guarded place."""
    if not isinstance(repo, str) or not MOUNT_URL.fullmatch(repo) or MOUNT_URL.fullmatch(repo).group(2) in (".", ".."):
        return f"{pages._shown(repo)} is not an https://github.com/<owner>/<name> address"
    rel = str(path or "")
    if rel != "brain" and not (rel.startswith("mounts/") and rel.count("/") == 1 and pages.is_slug(rel[7:])):
        return f"{pages._shown(rel)} is not brain or mounts/<name>"
    return None


def add_mount(report, root, repo, path, dry_run) -> None:
    """Mount `repo` at `path` (a submodule on its `main`), as one step: a mount that did not come says so with git's
    own words, since a workspace without its brain has no company layer to read (rc1 run, issue 14). A repository
    or a path `mount_problem` refuses is never handed to git: its row says why."""
    problem = mount_problem(repo, str(path).replace("\\", "/"))
    if problem:
        report.add(f"mount {path} in {Path(root).name}", False, f"test -f {Path(root).name}/{path}/space.yaml",
                   f"not mounted: {problem}")
        return
    res = run_cmd(["git", "submodule", "add", "-b", "main", repo, str(path)], dry_run, cwd=root)
    there = dry_run or (Path(root) / path / "space.yaml").exists()
    said = " ".join(((res.stderr or "") + (res.stdout or "")).split())[:300]
    report.add(f"mount {path} in {Path(root).name}", res.returncode == 0 and there,
               f"test -f {Path(root).name}/{path}/space.yaml",
               "mounted" if res.returncode == 0 and there else f"not mounted: {said or 'git gave no reason'}")


# ----- content written by onboard --------------------------------------------
def people_of(answers) -> list:
    people, order = {}, []
    owner = answers.get("owner") or ""
    if owner:
        slug = answers.get("owner_slug") or slugify(owner)
        people[slug] = {"name": owner, "slug": slug, "email": answers.get("owner_email") or "",
                        "parent": "brain", "domain": None}
        if LOGIN.fullmatch(str(answers.get("owner_login") or "")):   # the gate ties the owner's upkeep to it
            people[slug]["login"] = answers["owner_login"]
        order.append(slug)
    for p in answers.get("people") or []:
        slug = p.get("slug") or slugify(p.get("name"))
        if slug not in people:
            people[slug] = {"name": p.get("name") or humanize(slug), "slug": slug, "parent": "brain"}
            order.append(slug)
        for key in ("email", "parent", "domain"):
            if p.get(key):
                people[slug][key] = p[key]
        people[slug].setdefault("email", "")
        people[slug].setdefault("domain", None)
    return [people[s] for s in order]


def classification_default(answers) -> str:
    """The company's one setting for the classification gate: `open` unless it says closed."""
    value = answers.get("classification_default")
    value = value[0] if isinstance(value, list) and value else value
    return "closed" if str(value or "").strip().lower() == "closed" else "open"


def fill_registry(report, root, answers, top_owner, brain_name, now, workspaces=None,
                  workspace_repos=None) -> None:
    """`workspaces` maps a person's slug to the space name of the workspace made for them, and
    `workspace_repos` to that workspace's repository (`https://github.com/<owner>/<name>`), the
    one onboarding created or adopted for them. A person's `workspace_repo` is where landing
    reads that workspace's signatures (`release.trust_repo`), so it is written from what this
    run made, never from anything a workspace says; like `workspace`, it is set once, which
    also fills it in for a person registered before the field existed."""
    space = spaceyaml.load(root)
    reg = registry.Registry(root, space)
    names = {p["slug"]: p["name"] for p in people_of(answers)}
    workspaces = dict(workspaces or {})
    workspace_repos = dict(workspace_repos or {})
    added = []

    def upsert(data):
        existing = reg.subjects.get(data["id"])
        if existing is None:
            if reg.get(data["id"]) is None:
                reg.add(data, by="onboard", now=now)
                added.append(data["id"])
            return
        for key in ("owner", "home", "part_of"):
            if data.get(key) and not getattr(existing, key):
                setattr(existing, key, data[key])
        for key in ("workspace", "workspace_repo", "classification"):   # set once; never undone
            if data.get(key) and not existing.extra.get(key):
                if key == "workspace_repo" and existing.extra.get("workspace") != data.get("workspace"):
                    continue   # a repository only beside the workspace it is
                existing.extra[key] = data[key]
        for key, value in (data.get("match") or {}).items():   # set once; never undone
            existing.match.setdefault(key, value)

    for d in parse_pairs(answers.get("domains"), "id", "owner", top_owner):
        upsert({"id": slugify(d["id"]), "kind": "domains", "title": humanize(d["id"]),
                "owner": names.get(d["owner"], d["owner"]), "home": brain_name})
    for c in answers.get("clients") or []:
        data = {"id": slugify(c["id"]), "kind": "clients", "title": c.get("title") or humanize(c["id"])}
        if c.get("aliases"):
            data["aliases"] = list(c["aliases"])
        upsert(data)
    for p in answers.get("projects") or []:
        own_space = p.get("space", True) if answers.get("_fixture") else bool(p.get("space"))
        data = {"id": slugify(p["id"]), "kind": "projects", "title": p.get("title") or humanize(p["id"]),
                "home": slugify(p["id"]) if own_space else brain_name}
        if p.get("client"):
            data["part_of"] = slugify(p["client"])
        if p.get("match"):
            data["match"] = dict(p["match"])
        if p.get("aliases"):
            data["aliases"] = list(p["aliases"])
        if own_space:   # a project given its own space is walled: its crew, its home
            crew = [slugify(c) for c in p.get("crew") or []] or [people_of(answers)[0]["slug"]
                                                                if people_of(answers) else "owner"]
            data["classification"] = {"class": "walled", "state": "settled", "home": data["home"], "crew": crew,
                                      "reason": "given its own space at onboarding", "evidence": [],
                                      "decided_by": top_owner, "at": pages.iso(now),
                                      "checked_with": [], "not_yet_checked": []}
        upsert(data)
    # Any other subjects, in the company's own kinds (deals, topics, whatever it declared):
    # {id, kind, title, part_of, owner, home, match, aliases, important}.
    for s in answers.get("subjects") or []:
        if not (s.get("id") and s.get("kind")):
            continue
        data = {"id": slugify(s["id"]), "kind": s["kind"], "title": s.get("title") or humanize(s["id"])}
        for key in ("part_of", "owner", "home", "match", "aliases", "important", "client", "theme"):
            if s.get(key):
                data[key] = s[key]
        if data.get("owner"):
            data["owner"] = names.get(data["owner"], data["owner"])
        upsert(data)
    for person in people_of(answers):
        data = {"id": person["slug"], "kind": "people", "title": person["name"]}
        if person.get("email"):
            data["email"] = person["email"]
        if person.get("login"):
            data["match"] = {"github": person["login"]}
        if workspaces.get(person["slug"]):
            data["workspace"] = workspaces[person["slug"]]
            if workspace_repos.get(person["slug"]):
                data["workspace_repo"] = workspace_repos[person["slug"]]
        if person.get("domain") and reg.get(slugify(person["domain"])) is not None:
            data["part_of"] = slugify(person["domain"])
        upsert(data)
    reg.save()
    errs = reg.check()
    report.add("registry", not errs, "python _meta/tools/registry.py . check",
               "; ".join(errs) or f"{len(added)} subjects added")


def write_identity(root, company, owner, domains, never_leaves, now) -> None:
    terms = "; ".join(never_leaves) or "nothing beyond the hard rules"
    owners = "\n".join(f"- {humanize(d['id'])}: {d['owner']}" for d in domains) or "- none declared yet"
    drafts = {
        "identity/charter.md": ("policy", "Charter",
            f"# Charter\n\nThis space is the brain of {company}. Everyone in the company reads it; "
            f"{owner} signs for it.\n\n## What never leaves\n\nMatter of these kinds stays in the space "
            f"that holds it, whatever the release class: {terms}. An agent reads each record against "
            f"them; they are not words to match. The list itself is the `never_leaves` "
            f"setting in `space.yaml`; this page explains it in words.\n\n## Domains and their owners\n\n"
            f"{owners}\n\nDrafted by onboard from the interview; the owner signs it.\n"),
        "identity/spine.md": ("concept", "Spine",
            f"# Spine\n\nWhat {company} is, what it does and what it never does. Drafted by onboard; "
            f"confirmed and signed by {owner}.\n\n## What we do\n\n(to be confirmed by the owner)\n\n"
            "## What we never do\n\n- We never move what is on the never-leaves list.\n"
            "- We never send, post, spend or delete on someone's behalf without their yes.\n"),
    }
    for rel, (ptype, title, body) in drafts.items():
        path = Path(root) / rel
        fm = pages.read(path)[0] if path.exists() else {}
        if fm.get("status") == "stable" or (fm and (fm.get("generated") or {}).get("by") != "onboard"):
            continue
        pages.write(path, {"id": fm.get("id") or pages.new_id(), "type": ptype, "title": title,
                           "status": "draft", "generated": pages.generated("onboard", now)}, body)


def write_needs_you(root, skipped, path="_inbound/needs-you/onboard.yaml", prefix="onboard") -> None:
    """One item a question: `key`, `ask` and `default` (the recommendation); `if_nothing` says what happens
    when the person does nothing, when that is not the generic "the default stays". A file of its own and
    no question empties the file: an item an earlier run left is withdrawn."""
    target = Path(root) / path
    existing = (read_yaml(target, {}) or {}).get("items") or []
    kept = [i for i in existing if not str(i.get("id", "")).startswith(prefix + "-")]
    items = kept + [{"id": f"{prefix}-{q['key']}", "kind": "question", "text": q["ask"],
                     "recommendation": joiner(q).join(map(str, q["default"])) if isinstance(q["default"], list)
                     else str(q["default"]), "default": q.get("if_nothing") or "the default stays", "deadline": None,
                     "depends": 1, "payload": "space.yaml", "fingerprint": ""} for q in skipped]
    if items or target.exists():
        write_yaml(target, {"items": items})


# The company profile: what the research read about the company, drafted for the owner to correct and sign.
PROFILE_TITLES = {"company": "The company", "offering": "What we offer", "vision": "Vision", "locations": "Locations",
                  "people": "People", "clients": "Clients", "brand": "Brand", "tools": "Tools",
                  "signs_for": "Who signs for the company"}
PROFILE_ASK = "Correct any line of your company profile, or sign it"
PROFILE_ITEMS = "_inbound/needs-you/onboard-profile.yaml"   # its own file: onboard.yaml is rewritten by every run


def _own_draft(path) -> bool:
    """Whether a page is absent or still the draft onboard wrote: a signed page, or one that
    someone else wrote, is never overwritten (the rule `write_identity` keeps)."""
    fm = pages.read(path)[0] if Path(path).exists() else {}
    return not (fm.get("status") == "stable" or (fm and (fm.get("generated") or {}).get("by") != "onboard"))


def fill_what_we_do(root, bullets, now) -> bool:
    """Put the profile's offering lines under "What we do" in the spine, in place of the placeholder
    (or of an earlier fill). The spine must still be onboard's draft."""
    path = Path(root) / "identity" / "spine.md"
    if not bullets or not path.exists() or not _own_draft(path):
        return False
    fm, body = pages.read(path)
    filled, n = re.subn(r"(## What we do\n\n).*?(?=\n\n## |\Z)", lambda m: m.group(1) + "\n".join(f"- {b}" for b in bullets),
                        body, count=1, flags=re.S)
    if not n:
        return False
    pages.write(path, {**fm, "generated": pages.generated("onboard", now)}, filled)
    return True


def write_profile(root, profile: dict, now) -> Path | None:
    """Draft `identity/profile.md` from the research: `profile` is {section: [{"text", "source",
    "at"}]} (`research.PROFILE_SECTIONS`), and each line becomes one bullet ending ` (source:
    <source>, <date>)`; a line without text or a source is left out, and so is a section without a
    line. The offering lines also fill the spine's "What we do". One item on Needs you (id
    `onboard-profile`, in its own file: `onboard.yaml` is rewritten by every run) asks the owner to
    correct any line or sign. A signed page, or one someone else wrote, is never overwritten, and
    asks nothing: an item an earlier run left is emptied. Returns the page's path; None when there
    was no line to write."""
    import research
    today = f"{now:%Y-%m-%d}"
    sections = {}
    for section in research.PROFILE_SECTIONS:
        bullets = []
        for line in (profile or {}).get(section) or []:
            text, source = " ".join(str(line.get("text") or "").split()), " ".join(str(line.get("source") or "").split())
            if text and source:
                bullets.append(f"{text} (source: {source}, {str(line.get('at') or '')[:10] or today})")
        if bullets:
            sections[section] = bullets
    path = Path(root) / "identity" / "profile.md"
    if not _own_draft(path):   # signed (or someone's own): the question to sign it is withdrawn, not left to be asked
        write_needs_you(root, [], path=PROFILE_ITEMS, prefix="onboard")
        return path
    if not sections:
        return None
    body = ("# Company profile\n\nDrafted from the company's own sources: its site, the business register, its code "
            "organisation, its documents and files. Every line says where it comes from. Correct any line, then sign "
            "the page.\n")
    for section, bullets in sections.items():
        body += f"\n## {PROFILE_TITLES[section]}\n\n" + "\n".join(f"- {b}" for b in bullets) + "\n"
    fm = pages.read(path)[0] if path.exists() else {}
    pages.write(path, {"id": fm.get("id") or pages.new_id(), "type": "concept", "title": "Company profile",
                       "status": "draft", "generated": pages.generated("onboard", now)}, body)
    fill_what_we_do(root, sections.get("offering"), now)
    write_needs_you(root, [{"key": "profile", "ask": PROFILE_ASK, "default": "sign it when it reads right"}],
                    path=PROFILE_ITEMS, prefix="onboard")
    return path


BRIEFING_ITEMS = "_inbound/needs-you/onboard-briefing.yaml"   # its own file, like the profile's


def briefing_item(workspace, address: str) -> None:
    """The morning briefing goes to the address in the workspace's space.yaml, and every notice the
    agents queue for the person with it. With none, one plain item asks for it; once there is one, the
    item an earlier run left is withdrawn."""
    asked = [] if "@" in str(address) else [{
        "key": "briefing-address", "default": "your work email address",
        "ask": "Which email address should your morning briefing go to? It is sent from your own mailbox to you alone.",
        "if_nothing": "no morning briefing is sent"}]
    write_needs_you(workspace, asked, path=BRIEFING_ITEMS, prefix="onboard")


# ----- the personal instruction ---------------------------------------------
def instruction_block(workspace) -> str:
    text = (TEMPLATES / "personal-instruction.md").read_text(encoding="utf-8")
    try:
        planning = str(spaceyaml.load(workspace).data.get("planning") or "")
    except Exception:
        planning = ""
    line = (f"\nWhen the person asks to plan or review their day, read "
            f"`{Path(workspace).resolve().as_posix()}/{planning}` first and follow it.\n" if planning else "")
    text = text.replace("{planning}\n", line).replace("{planning}", line)
    return text.replace("{workspace_path}", Path(workspace).resolve().as_posix()).replace(
        "{python}", python_cmd()).strip() + "\n"


def remove_block(text) -> str:
    pattern = re.compile(re.escape(MARK_OPEN) + r".*?" + re.escape(MARK_CLOSE) + r"[ \t]*\n?", re.S)
    return pattern.sub("", text)


def install_instruction(path, workspace) -> bool:
    """Append (or replace) the marked block in the user-level instruction file."""
    path = Path(path)
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    rest = remove_block(current)
    new = (rest.rstrip("\n") + "\n\n" if rest.strip() else "") + instruction_block(workspace)
    if new != current:
        write_text(path, new)
    return new != current


def instruction_present(path, workspace) -> bool:
    path = Path(path)
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    ws = re.escape(Path(workspace).resolve().as_posix())   # the path itself, not a longer one it starts
    return MARK_OPEN in text and re.search(ws + r"(?![\w.-])", text) is not None


def connect(root, feed: str, audience: str = "owner", scope: dict | None = None) -> bool:
    """Add one connector to this space's sources (and the register of connected systems);
    the person's sign-in to the connector itself happens in their harness. `scope` names what
    the sweep reads from it (`channels`, `team`, `project`, `repo`), for a project's own sources."""
    root = Path(root)
    if feed not in FEEDS:
        raise ValueError(f"a feed is one of {', '.join(FEEDS)}")
    space = spaceyaml.load(root)
    if any(isinstance(s, dict) and s.get("name") == feed for s in space.sources):
        return False
    space.data["sources"] = space.sources + [{"name": feed, "adapter": "files", "audience": audience,
                                              **(scope or {})}]
    spaceyaml.save(space)
    reg = root / "_meta" / "access" / "resources.yaml"
    data = read_yaml(reg, {}) or {}
    rows = [r for r in (data.get("resources") or []) if r.get("name") != feed]
    rows.append({"name": feed, "adapter": "files", "audience": audience})
    write_yaml(reg, {"resources": rows})
    return True


def allow_directory(settings_path, workspace) -> bool:
    """Add the workspace to the harness's allowed directories in its user-level settings, so a
    session in any repository may read and journal there. Returns True when it changed."""
    import json
    path, folder = Path(settings_path), Path(workspace).resolve().as_posix()
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError:
            return False  # a settings file we cannot read is the person's; never overwrite it
    perms = data.setdefault("permissions", {})
    dirs = perms.setdefault("additionalDirectories", [])
    if folder in [Path(d).as_posix() for d in dirs]:
        return False
    dirs.append(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    return True


def disallow_directory(settings_path, workspace) -> bool:
    """Offboard's mirror of allow_directory: drop the workspace, keep every other entry."""
    import json
    path = Path(settings_path)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except ValueError:
        return False
    dirs = (data.get("permissions") or {}).get("additionalDirectories") or []
    keep = [d for d in dirs if Path(d).as_posix() != Path(workspace).resolve().as_posix()]
    if keep == dirs:
        return False
    data["permissions"]["additionalDirectories"] = keep
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    return True


def directory_allowed(settings_path, workspace) -> bool:
    import json
    path = Path(settings_path)
    if not path.exists():
        return False
    try:
        dirs = (json.loads(path.read_text(encoding="utf-8") or "{}").get("permissions") or {}).get(
            "additionalDirectories") or []
    except ValueError:
        return False
    return Path(workspace).resolve().as_posix() in [Path(d).as_posix() for d in dirs]


def default_user_instructions(root, offline, home) -> Path:
    if offline:
        return (Path(home) if home else Path(root).resolve().parent) / ".claude" / "CLAUDE.md"
    return Path.home() / ".claude" / "CLAUDE.md"


# ----- OS tasks: the sync task and, without a harness scheduler, the routines --------------
SYSTEM = platform.system()   # "Windows", "Darwin" or "Linux"
CONHOST = r"%SystemRoot%\System32\conhost.exe"
TASK_ENDED = "space-task-ended"   # the line a Windows task with a log adds to it as it ends (register_task)
# What `onboard --check` reads back from a Windows task, each with what it means.
WINDOWS_TASK = {"<DisallowStartIfOnBatteries>false": "starts on battery",
                "<StopIfGoingOnBatteries>false": "keeps running on battery",
                "<StartWhenAvailable>true": "catches up after sleep",
                "conhost.exe": "runs without a window"}


TASK_PREFIX = "space-"
TASK_PREFIX_ENV = "SPACES_TASK_PREFIX"
LAUNCHD_PREFIX = "com.spaces."   # a launch agent's label is this and the task's name
# Cron runs a routine only at its time: one due while the computer slept or was off is lost. Said once,
# in plain words, as a step that blocks nothing, and kept on the task's record for the progress page.
CRON_STEP = "routines missed in sleep are not made up"   # begins "routine": the progress page files it under Routines
CRON_WAITING = ("This computer's scheduler does not catch up on runs it missed while the computer was off or asleep, "
                "so a routine can skip a day.")
_sleep = time.sleep   # the tests stand in for it


def task_prefix() -> str:
    """The start of every OS task name onboard registers: `space-` (`space-sync-ada`,
    `space-sweep-chain-ada`). An isolated test run sets SPACES_TASK_PREFIX to `space-test-<id>-`, so
    it registers, reads back and removes tasks of its own and never touches a person's. Without it, this
    computer's `task_prefix` (`~/.spaces/settings.yaml`, saved by `start.py build --task-prefix` where another
    account holds the names). A value that is not `space-` and lower-case letters, digits and hyphens is
    refused, never replaced by the default: a typo must not register tasks under the person's own names. The refusal
    is check_task_prefix's, naming the setting the prefix came from."""
    said = os.environ.get(TASK_PREFIX_ENV)
    prefix = said or _saved_settings().get("task_prefix") or TASK_PREFIX
    check_task_prefix(prefix, TASK_PREFIX_ENV if said else f"task_prefix in {_settings_path()}")
    return prefix


def _settings_path() -> Path:
    return Path.home() / ".spaces" / "settings.yaml"


def _saved_settings() -> dict:
    try:
        data = yaml.safe_load(_settings_path().read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def check_task_prefix(prefix: str, source: str = "a task-name prefix") -> None:
    """A ValueError for a task-name prefix that is not ours, naming where it came from (`source`): the one rule,
    for task_prefix, save_task_prefix and the build's `--task-prefix` (the review of #61)."""
    if not re.fullmatch(r"space-[a-z0-9-]*", str(prefix)):
        raise ValueError(f"{source} must start with space- and use only lower-case letters, digits and hyphens, "
                         f"not {prefix!r}")


def save_task_prefix(prefix: str) -> None:
    """Keep `prefix` as this computer's task-name prefix, refused as task_prefix refuses it."""
    check_task_prefix(prefix)
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({**_saved_settings(), "task_prefix": prefix}, sort_keys=False), encoding="utf-8")


def task_taken(task: str) -> str:
    """What a Windows task row says when the name is another account's task, which this account can neither
    read nor replace: schtasks only says "Access is denied"."""
    return (f"a scheduled task named {task} already exists and belongs to another account on this computer, so this "
            f"account cannot replace it: an administrator removes it with `schtasks /Delete /F /TN {task}`, or set up "
            "again with names of your own (`start.py build --task-prefix space-<your initials>-`)")


def _denied_words() -> list:
    """Windows' "Access is denied." (its error 5) as schtasks prints it: in English, and in this computer's display
    language, as Windows itself words it (`ctypes.FormatError`, Windows only)."""
    words = ["Access is denied."]
    try:
        import ctypes
        own = ctypes.FormatError(5).strip()
    except (ImportError, AttributeError, OSError, ValueError):
        own = ""
    return words + [own] if own and own not in words else words


def _letters(text) -> str:
    """The ASCII letters and digits of `text`, in lower case: what is left the same of a line that a console code page
    other than the one Python decoded with garbled (only the accented letters change)."""
    return "".join(ch for ch in str(text).casefold() if ch.isascii() and ch.isalnum())


def access_denied(text) -> bool:
    """Whether what schtasks said is "access is denied", in whatever language this Windows speaks."""
    said = _letters(text)
    return any(_letters(word) and _letters(word) in said for word in _denied_words())


def python_cmd() -> str:
    """The interpreter that runs onboarding, for unattended text: absolute (macOS has no bare
    `python`), quoted when its path has a space."""
    path = Path(sys.executable).as_posix()
    return f'"{path}"' if " " in path else path


def user_id() -> int:
    """The person's numeric user id: launchd's per-user domain is `gui/<uid>`."""
    return os.getuid()


def daily_times(schedule: str):
    """(hour, minute) pairs of a cron schedule that runs at fixed times every day, else None."""
    fields = str(schedule).split()
    if len(fields) != 5 or fields[2:] != ["*", "*", "*"]:
        return None
    try:
        minutes, hours = ([int(x) for x in f.split(",")] for f in fields[:2])
    except ValueError:
        return None
    return sorted((h, m) for h in hours for m in minutes)


def _argline(py, args, quote=None) -> str:
    """The interpreter and its arguments, every value quoted, every flag bare. `quote` is the way of the
    system that reads the line: plain double quotes (Windows paths hold none), else `_sh` or `_unit_word`."""
    quote = quote or (lambda value: f'"{value}"')
    return " ".join([quote(py)] + [str(a) if str(a).startswith("-") else quote(a) for a in args])


def _sh(value) -> str:
    """One word for `sh` in double quotes, where only a backslash, a quote, `$` and a backtick mean anything:
    each is escaped, so a folder name is never read as a command, a variable or the end of the word."""
    return '"' + re.sub(r'([\\"$`])', r"\\\1", str(value)) + '"'


def _unit_word(value) -> str:
    """One argument of a systemd command line in double quotes: a backslash and a quote are escaped, and `%`
    (a specifier) and `$` (a variable) doubled."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def _exec_start(py, args) -> str:
    """The ExecStart= line of a unit. Its program (the first word) is read differently from its arguments,
    as systemd's own parser showed: `%` is still a specifier, but `$` is no variable there (`$$` would stay
    two dollar signs), and a program path holding a backslash or a quote is refused outright ("special
    characters"). Such a program is started by `sh`, to which it is an argument like any other."""
    program = str(py)
    if any(ch in program for ch in "\\\"'"):
        words = ['/bin/sh -c "exec \\"$$0\\" \\"$$@\\""', _unit_word(program)]
    else:
        words = ['"' + program.replace("%", "%%") + '"']
    return " ".join(words + [str(a) if str(a).startswith("-") else _unit_word(a) for a in args])


def task_xml(arguments: str, minutes: int | None = None, times=None) -> str:
    """A Windows task, every `minutes` or daily at `times`: hidden behind `conhost --headless`,
    started and kept running on battery, a missed start run as soon as the machine wakes.
    `schtasks /Create` cannot set the battery or catch-up flags; the task XML can, and its
    action has no 261-character limit."""
    now = dt.datetime.now()
    if minutes:
        triggers = (f"    <TimeTrigger>\n      <Repetition><Interval>PT{minutes}M</Interval>"
                    "<StopAtDurationEnd>false</StopAtDurationEnd></Repetition>\n"
                    f"      <StartBoundary>{now.strftime('%Y-%m-%dT%H:%M:%S')}</StartBoundary>\n"
                    "      <Enabled>true</Enabled>\n    </TimeTrigger>\n")
        what = f"every {minutes} minutes"
    else:
        triggers = "".join(f"    <CalendarTrigger>\n      <StartBoundary>{now.strftime('%Y-%m-%d')}T{h:02d}:{m:02d}:00"
                           "</StartBoundary>\n      <Enabled>true</Enabled>\n"
                           "      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>\n"
                           "    </CalendarTrigger>\n" for h, m in times)
        what = "daily at " + ", ".join(f"{h:02d}:{m:02d}" for h, m in times)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>space task, {what}</Description></RegistrationInfo>
  <Triggers>
{triggers}  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{CONHOST}</Command>
      <Arguments>{xml_escape(arguments)}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def windows_task_missing(xml: str) -> list:
    """What a registered Windows task lacks, in words (empty when it is as onboard registers it)."""
    return [words for key, words in WINDOWS_TASK.items() if key not in xml]


def cron_entry(when: str, root, py, args, task, path) -> str:
    """One crontab line: it runs in the checkout with the PATH onboarding saw (cron's own lacks
    gh and often git), logs instead of mailing, and ends in `# <task>`, the marker by which
    onboard replaces it and offboard removes it. cron reads `%` as a newline: escaped."""
    command = (f'cd {_sh(root)} && PATH={_sh(path)} {_argline(py, args, _sh)} '
               f'>> "$HOME/.spaces/logs/{task}.log" 2>&1  # {task}')
    return f"{when} " + command.replace("%", "\\%")


def cron_command(line: str) -> str | None:
    """The command of a crontab line of ours, as cron hands it to `sh`: after the five time fields, with
    `\\%` read as `%`. None for a line that has no command."""
    parts = str(line).split(None, 5)
    return parts[5].replace("\\%", "%") if len(parts) == 6 else None


def cron_line(minutes, py, script, root, task, path) -> str:
    return cron_entry(f"*/{minutes} * * * *", root, py, [script, "--root", root, "--release-leg"], task, path)


def is_task_line(text, task, root) -> bool:
    """A crontab line of this task: its marker, or, for a sync task written before the marker existed, exactly
    `<python> <root>/_meta/tools/sync.py --root <root> --release-leg` after its five time fields. Nothing looser:
    another space's line (a root that only begins like this one), a line that has another task's marker and a
    line the person wrote by hand are not this task's."""
    return text.rstrip().endswith(f"# {task}") or is_legacy_sync_line(text, task, root)


def is_legacy_sync_line(text, task, root) -> bool:
    """The sync task's crontab line as the template's first version wrote it, before there was a marker: exactly
    `<python> <root>/_meta/tools/sync.py --root <root> --release-leg` after the five time fields, for this root."""
    words = text.split()
    return (task.startswith("space-sync-") and len(words) == 10 and words[7] == "--root" and words[9] == "--release-leg"
            and Path(words[6]) == Path(root) / "_meta" / "tools" / "sync.py" and Path(words[8]) == Path(root))


def is_own_cron_line(text, task, root) -> bool:
    """The line `cron_entry` wrote for this task in this folder: its marker and, as the folder it starts in, this
    root as a whole word. Never a line without the marker, never the same marker from another folder."""
    command = cron_command(text)
    return (text.rstrip().endswith(f"# {task}") and command is not None
            and any(command.startswith(f"cd {_sh(folder)} && ") for folder in {str(root), str(Path(root).resolve())}))


def read_crontab(dry_run=False, run=None) -> tuple:
    """(lines, state, why) of the crontab, read before anything is written over it. state is `read` (a person with
    no crontab has an empty one: `crontab -l` fails and says "no crontab for <user>"), `missing` (no crontab
    command here) or `failed` (lines is None, why says what went wrong): any other failure is not an empty
    crontab, and a rewrite from it would drop the person's own lines."""
    res = (run or run_cmd)(["crontab", "-l"], dry_run)
    said = f"{res.stderr or ''} {res.stdout or ''}".strip()
    if res.returncode == 127:
        return None, "missing", ""
    if res.returncode == 0:
        return (res.stdout or "").splitlines(), "read", ""
    if "no crontab for" in said.lower():
        return [], "read", ""
    return None, "failed", (said or "no reason given")[:80]


def write_crontab(lines, dry_run=False, run=None) -> subprocess.CompletedProcess:
    """Install `lines` as the crontab: the one place a crontab is written (a file handed to `crontab`, which is
    removed after). `run` is the door to use; offboard has its own."""
    run = run or run_cmd
    with tempfile.NamedTemporaryFile("w", suffix=".cron", delete=False, encoding="utf-8", newline="\n") as tmp:
        tmp.write("\n".join(lines) + ("\n" if lines else ""))
    try:
        return run(["crontab", tmp.name], dry_run)
    finally:
        os.unlink(tmp.name)


def systemd_units(task, root, py, args, path, calendars) -> tuple:
    """A systemd user service and its timer. `Persistent=true` runs a start missed while the machine slept.
    `%` is a specifier in unit files and `$` a variable in the arguments of ExecStart=: both doubled;
    `Environment=` and `ExecStart=` words are double quoted with their backslashes and quotes escaped."""
    def pct(value):
        return str(value).replace("%", "%%")
    env = str(path).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    service = (f"[Unit]\nDescription={pct(task)}\n\n[Service]\nType=oneshot\nWorkingDirectory={pct(root)}\n"
               f"Environment=\"PATH={env}\"\nExecStart={_exec_start(py, args)}\n")
    timer = (f"[Unit]\nDescription={pct(task)}\n\n[Timer]\n" + "".join(f"OnCalendar={c}\n" for c in calendars)
             + "Persistent=true\n\n[Install]\nWantedBy=timers.target\n")
    return service, timer


def launch_agent(task: str, root, py: str, args: list, times: list | None, minutes: int | None,
                 path: str | None = None) -> str:
    """A macOS launch agent for one task, as a property list: it runs `py args` in `root` at the daily
    `times` ((hour, minute) pairs) or every `minutes`, with the PATH onboarding saw (launchd's own lacks gh
    and often git), and logs to `~/.spaces/logs/<task>.log`. `ProgramArguments` is an array, one string per
    word: no shell reads it, so a folder name is never parsed. launchd runs a calendar start it missed while
    the Mac slept as soon as the Mac wakes."""
    log = str(Path.home() / ".spaces" / "logs" / f"{task}.log")
    path = os.environ.get("PATH", "") if path is None else path
    agent = {"Label": LAUNCHD_PREFIX + task, "ProgramArguments": [str(py)] + [str(a) for a in args],
             "WorkingDirectory": str(root), "EnvironmentVariables": {"PATH": path},
             "StandardOutPath": log, "StandardErrorPath": log}
    if minutes:
        agent["StartInterval"] = int(minutes) * 60
    else:
        agent["StartCalendarInterval"] = [{"Hour": h, "Minute": m} for h, m in times]
    return plistlib.dumps(agent).decode("utf-8")


def control_trouble(root, py, path, args) -> str | None:
    """Why these words cannot be scheduled on this computer if one holds a control character (a tab, say), in
    plain words that name what holds it, else None. A unit file is made of lines and systemd refuses a program
    path with one; a property list cannot carry most of them (`plistlib` raises). Refused, never escaped."""
    words = (("the folder name", "rename the folder", root),
             ("the location of the Python program", "move Python to a folder without it", py),
             ("the list of folders this computer searches for programs", "take it out of that list", path),
             *(("the name of a file the task uses", "rename it", arg) for arg in args))
    for what, fix, word in words:
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in str(word)):
            return (f"a control character (a tab, say) in {what} cannot be scheduled on this computer: "
                    f"{fix} and run onboard again")
    return None


def unit_trouble(root, py, path, args) -> str | None:
    """Why a systemd unit file cannot carry these words, in plain words, else None: a control character (systemd
    refuses a program path with one, and a unit file is made of lines) and a folder name that ends in a
    backslash (`WorkingDirectory=` would go on in the next line). Refused, never escaped: neither has an escape
    that systemd's own parser was seen to honour."""
    trouble = control_trouble(root, py, path, args)
    if trouble is None and str(root).endswith("\\"):
        trouble = ("a folder name that ends in a backslash cannot be scheduled on this computer: rename the folder "
                   "and run onboard again")
    return trouble


def windows_expands(text: str) -> bool:
    """Whether Windows would swap a `%NAME%` in `text` for a variable's value. The Task Scheduler is not known to
    leave it alone in a task's arguments and has no escape for a percent sign, so such a name is refused, not
    escaped. Every word between two percent signs counts, at either alignment (`%A%B%` holds A and B), and
    names are compared without case, as Windows does. A lone `100%` is nothing."""
    names = {name.upper() for name in os.environ}
    return any(name.upper() in names for name in re.findall(r"(?=%([^%]+)%)", text))


def register_task(report, label, task, root, args, minutes=None, times=None, cron=None, dry_run=False,
                  stderr_log=None) -> dict:
    """Register one OS task that runs this interpreter with `args` in `root`, every `minutes` or daily at
    `times` (`cron` is the same schedule in cron's words), through the scheduler that catches up after sleep:
    a task XML on Windows, a launch agent on macOS, a systemd user timer on a Linux where
    `systemctl --user` works, else a cron line, which does not catch up (one plain line says so). A task runs
    through one scheduler: moving to another takes it out of the rest (`retire_others`). On Windows, a task
    given `stderr_log` runs through cmd, which adds what it printed to its error output and then TASK_ENDED
    to that file: a hidden task's Python that cannot start says so nowhere else (the other schedulers log). The
    line ends on `exit /b`, so cmd ends with Python's own exit code, not the echo's 0, and the scheduler's last result
    shows a run that failed (the review of #61)."""
    root, py, path = Path(root), sys.executable, os.environ.get("PATH", "")
    if SYSTEM == "Windows":
        arguments = f"--headless {_argline(py, args)}"
        if stderr_log:
            log = Path(stderr_log)
            arguments = (f'--headless cmd.exe /d /s /c "{_argline(py, args)} 2>>"{log}" & '
                         f'echo {TASK_ENDED}>>"{log}" & exit /b"')
        record = {"name": task, "kind": "schtasks", "line": f"{CONHOST} {arguments}",
                  "create": ["schtasks", "/Create", "/F", "/TN", task, "/XML", f"{task}.xml"],
                  "check": ["schtasks", "/Query", "/TN", task, "/XML"], "root": str(root)}
        if stderr_log:
            record["log"] = str(log)
            if not dry_run:
                log.parent.mkdir(parents=True, exist_ok=True)
        if windows_expands(arguments):
            report.add(label, False, " ".join(record["check"]), "a folder name with a word between percent signs "
                       "that Windows would swap for something else (%TEMP% is one) cannot be scheduled: rename "
                       "the folder and run onboard again")
            return record
        fd, xml_path = tempfile.mkstemp(suffix=".xml")
        os.close(fd)
        Path(xml_path).write_text(task_xml(arguments, minutes, times), encoding="utf-16")
        try:
            res = run_cmd(record["create"][:-1] + [xml_path], dry_run)
        finally:
            os.unlink(xml_path)
        verify = run_cmd(record["check"], dry_run)
        missing = [] if dry_run else windows_task_missing(verify.stdout or "")
        ok = res.returncode == 0 and verify.returncode == 0 and not missing
        if not ok and access_denied(f"{res.stderr} {verify.stderr}"):
            report.add(label, False, " ".join(record["check"]), task_taken(task), needs_owner=True)
            return record
        report.add(label, ok, " ".join(record["check"]), f"{task}: hidden, on battery too, catches up" if ok
                   else (res.stderr or verify.stderr).strip()[:200] or "registered without: " + ", ".join(missing))
        return record
    when = cron or f"*/{minutes} * * * *"
    record = {"name": task, "kind": "cron", "line": cron_entry(when, root, py, args, task, path),
              "create": ["crontab", "-"], "check": ["crontab", "-l"], "root": str(root)}
    if any(ch in str(word) for word in (root, py, path, *args) for ch in "\r\n"):   # a unit or crontab line is one line
        report.add(label, False, " ".join(record["check"]), "a folder name with a line break in it cannot be "
                   "scheduled: rename the folder and run onboard again")
        return record
    if SYSTEM == "Darwin":
        trouble = control_trouble(root, py, path, args)
        if trouble:
            report.add(label, False, f"launchctl print gui/{user_id()}/{LAUNCHD_PREFIX}{task}", trouble)
            return record
        return register_launchd(report, label, record, root, py, args, times, minutes, dry_run)
    if SYSTEM == "Linux" and run_cmd(["systemctl", "--user", "show-environment"], dry_run).returncode == 0:
        trouble = unit_trouble(root, py, path, args)
        if trouble:
            report.add(label, False, "systemctl --user show-environment", trouble)
            return record
        calendars = [f"*:0/{minutes}"] if minutes else [f"*-*-* {h:02d}:{m:02d}:00" for h, m in times]
        return register_systemd(report, label, record, root, py, args, path, calendars, dry_run)
    lines, state, why = read_crontab(dry_run)
    if state == "missing":   # neither a systemd user session nor cron on this machine
        report.add(label, False, "crontab -l", "cron is not installed: install it once (Debian or Ubuntu "
                   "`sudo apt install cron`, Fedora `sudo dnf install cronie`) and run onboard again", needs_owner=True)
        return record
    if state == "failed":
        report.add(label, False, "crontab -l", f"this computer's scheduler could not be read ({why}), so nothing was "
                   "changed: set up again once it can be read")
        return record
    gone, stuck, _ = retire_others(task, root, "cron", dry_run)   # first: one that cannot be stopped keeps cron out
    if stuck:
        report.add(label, False, " ".join(record["check"]), "; ".join(stuck), needs_owner=True)
        return record
    # what `run_now` rebuilds the line from, so it never runs text it did not build
    record.update({"python": py, "args": [str(a) for a in args], "path": path, "when": when})
    if not dry_run:
        (Path.home() / ".spaces" / "logs").mkdir(parents=True, exist_ok=True)
    wanted = [line for line in lines if not is_task_line(line, task, root)] + [record["line"]]
    res = subprocess.CompletedProcess(record["create"], 0, "", "")
    if wanted != lines:
        res = write_crontab(wanted, dry_run)
    verify = run_cmd(record["check"], dry_run)
    ok = res.returncode == 0 and verify.returncode == 0 and (dry_run or record["line"] in verify.stdout)
    report.add(label, ok, " ".join(record["check"]), f"{task}: {when} (cron)" + _removed(gone) if ok
               else (res.stderr or verify.stderr).strip()[:200])
    if ok:
        record["waiting"] = CRON_WAITING
        if not any(step.name == CRON_STEP for step in report.steps):
            report.add(CRON_STEP, True, "systemctl --user show-environment", CRON_WAITING)
    return record


def _removed(gone: list) -> str:
    return "; " + " and ".join(gone) + " removed" if gone else ""


# What an owner-facing line says without naming a tool: the earlier schedule of a task that is in the way.
NOT_STOPPED = ("this task's earlier schedule could not be stopped from here, because this computer's scheduler did not "
               "answer: set up again once it does")


def _retire_cron(task, root) -> tuple:
    """(gone, stuck, note) of taking the task's own line out of the crontab: this folder's line with its marker and,
    for the sync task, its exact pre-marker line; each is a sentence or None. A crontab that cannot be read is
    stuck, never empty. A line marked for this task that stays (another folder, another spelling of this one)
    is a note: it may be a copy, and then the task runs twice."""
    lines, state, why = read_crontab()
    if state == "missing":
        return None, None, None
    if state == "failed":
        return None, (f"this computer's scheduler could not be read ({why}), so an older job for this task may still "
                      "be in it, and the task would run twice: set up again once it can be read"), None
    keep = [line for line in lines if not (is_own_cron_line(line, task, root) or is_legacy_sync_line(line, task, root))]
    note = None
    if any(line.rstrip().endswith(f"# {task}") for line in keep):
        note = ("another job in this computer's scheduler is marked as this task's but starts in a different folder: "
                "if it is a copy of this one, the task runs twice")
    if len(keep) == len(lines):
        return None, None, note
    res = write_crontab(keep)
    if res.returncode == 0:
        return "its old cron line", None, note
    why = (res.stderr or res.stdout or "no reason given").strip()[:80]
    return None, (f"the older scheduled job for this task could not be removed ({why}), so the task runs twice until "
                  f"it is: remove the job whose note says {task}"), note


def _retire_timer(task) -> tuple:
    timer = Path.home() / ".config" / "systemd" / "user" / f"{task}.timer"
    if not timer.exists():
        return None, None, None
    stopped = (run_cmd(["systemctl", "--user", "disable", "--now", timer.name]).returncode == 0
               and run_cmd(["systemctl", "--user", "is-enabled", timer.name]).returncode != 0)
    if not stopped:   # no user session to ask just now: its files are left, and it stays the one scheduler
        return None, NOT_STOPPED, None
    timer.unlink()
    timer.with_suffix(".service").unlink(missing_ok=True)
    run_cmd(["systemctl", "--user", "daemon-reload"])
    return "its old systemd timer", None, None


def _retire_agent(task) -> tuple:
    ident = LAUNCHD_PREFIX + task
    agent = Path.home() / "Library" / "LaunchAgents" / f"{ident}.plist"
    if not agent.exists():
        return None, None, None
    loaded = f"gui/{user_id()}/{ident}"
    run_cmd(["launchctl", "bootout", loaded])
    if run_cmd(["launchctl", "print", loaded]).returncode == 0:
        return None, NOT_STOPPED, None
    agent.unlink()
    return "its old launch agent", None, None


def retire_others(task, root, keep, dry_run=False) -> tuple:
    """A task runs through one scheduler. Take it out of the others (`keep` names the one it stays in: cron,
    systemd or launchd), only its own entries: the cron line `cron_entry` wrote for this folder, the timer and
    agent of this task's name. Returns (gone, stuck, notes): what went, what could not be removed or read, and what
    stays that the person should know, each a list of sentences."""
    gone, stuck, notes = [], [], []
    if dry_run:
        return gone, stuck, notes
    for kept, retire in (("cron", lambda: _retire_cron(task, root)), ("systemd", lambda: _retire_timer(task)),
                         ("launchd", lambda: _retire_agent(task))):
        if kept != keep:
            for found, sentence in zip((gone, stuck, notes), retire()):
                found += [sentence] if sentence else []
    return gone, stuck, notes


def _after_move(report, label, record, task, root, keep, ok, said, failure, dry_run) -> None:
    """The step of a task registered with its new scheduler. Once that reads back, the old ones are cleared of it;
    one that could not be is a step only the person can finish, since the task would run twice, and its words
    name no tool."""
    gone, stuck, notes = retire_others(task, root, keep, dry_run) if ok else ([], [], [])
    if stuck:
        text = f"{task} is scheduled; " + "; ".join(stuck)
    else:
        text = said + _removed(gone) + "".join(f"; {note}" for note in notes) if ok else failure
    report.add(label, ok and not stuck, " ".join(record["check"]), text, needs_owner=bool(stuck))


def register_systemd(report, label, record, root, py, args, path, calendars, dry_run) -> dict:
    """A Linux with a systemd user session: a timer that runs a start it missed while the machine slept. The same
    units already in place with the timer running are left alone: nothing is reloaded, enabled or started."""
    task = record["name"]
    units = Path.home() / ".config" / "systemd" / "user"
    service, timer = systemd_units(task, root, py, args, path, calendars)
    files = ((units / f"{task}.service", service), (units / f"{task}.timer", timer))
    enable = ["systemctl", "--user", "enable", "--now", f"{task}.timer"]
    record.update({"kind": "systemd", "line": None, "create": enable,
                   "check": ["systemctl", "--user", "is-active", f"{task}.timer"],
                   "units": [str(files[0][0]), str(files[1][0])]})
    res = verify = None
    if not dry_run and all(f.is_file() and f.read_text(encoding="utf-8") == text for f, text in files):
        verify = run_cmd(record["check"])
        res = subprocess.CompletedProcess(enable, 0, "", "") if verify.returncode == 0 else None
    if res is None:
        if not dry_run:
            for f, text in files:
                write_text(f, text)
        run_cmd(["systemctl", "--user", "daemon-reload"], dry_run)
        res = run_cmd(enable, dry_run)
        verify = run_cmd(record["check"], dry_run)
    _after_move(report, label, record, task, root, "systemd", res.returncode == 0 and verify.returncode == 0,
                f"{task}.timer (systemd; catches up after sleep)", (res.stderr or verify.stderr).strip()[:200], dry_run)
    return record


def register_launchd(report, label, record, root, py, args, times, minutes, dry_run) -> dict:
    """macOS: a launch agent in the person's own login session, loaded now and at every login. The plist is
    written, any copy launchd already has is let go (`bootout`: none loaded is no error), and the new one
    loaded (`bootstrap`; the first try right after a `bootout` can lose a race with it, so it is tried up to
    three times); `launchctl print` reads it back. The same plist with the agent loaded is left alone: a job
    that is mid-run is not booted out."""
    task = record["name"]
    ident, domain = LAUNCHD_PREFIX + task, f"gui/{user_id()}"
    plist = Path.home() / "Library" / "LaunchAgents" / f"{ident}.plist"
    text = launch_agent(task, root, py, args, times, minutes)
    bootstrap = ["launchctl", "bootstrap", domain, str(plist)]
    record.update({"kind": "launchd", "line": None, "label": ident, "create": bootstrap,
                   "check": ["launchctl", "print", f"{domain}/{ident}"], "plist": str(plist)})
    if not dry_run:
        (Path.home() / ".spaces" / "logs").mkdir(parents=True, exist_ok=True)   # launchd does not make the log folder
    res = verify = None
    if not dry_run and plist.is_file() and plist.read_text(encoding="utf-8") == text:
        verify = run_cmd(record["check"])
        res = subprocess.CompletedProcess(bootstrap, 0, "", "") if verify.returncode == 0 else None
    if res is None:
        if not dry_run:
            write_text(plist, text)
        run_cmd(["launchctl", "bootout", f"{domain}/{ident}"], dry_run)
        for attempt in range(3):
            res = run_cmd(bootstrap, dry_run)
            if res.returncode == 0 or attempt == 2:
                break
            _sleep(1)
        verify = run_cmd(record["check"], dry_run)
    _after_move(report, label, record, task, root, "launchd", res.returncode == 0 and verify.returncode == 0,
                f"{task}: launch agent (catches up after sleep)", (res.stderr or verify.stderr).strip()[:200], dry_run)
    return record


def cron_line_of(record: dict) -> str | None:
    """The crontab line a cron record stands for, rebuilt from the fields it was made from. None when it lacks
    them, or when its own `line` is not exactly that: the record is read from a file a merge can change."""
    try:
        line = cron_entry(record["when"], record["root"], record["python"], record["args"], record["name"],
                          record["path"])
    except (KeyError, TypeError):
        return None
    return line if record.get("line") == line else None


def run_now(record: dict, dry_run: bool = False) -> bool:
    """Start a registered task at once, without waiting for its time and without waiting for it to end:
    the Windows task, the launch agent, the systemd service (`--no-block`: a oneshot service's `start`
    would otherwise wait for the whole run), or, for cron, the command rebuilt from the record's fields
    (`cron_line_of`: text that is not what registration built is refused), started on its own in the
    environment cron gives it, writing to the log the line itself writes to. True when it was started;
    nothing here says it succeeded."""
    kind, name = record.get("kind"), record.get("name") or ""
    if not (name or record.get("label")):
        return False
    if kind == "schtasks":
        return run_cmd(["schtasks", "/Run", "/TN", name], dry_run).returncode == 0
    if kind == "launchd":
        label = record.get("label") or LAUNCHD_PREFIX + name
        return run_cmd(["launchctl", "kickstart", f"gui/{user_id()}/{label}"], dry_run).returncode == 0
    if kind == "systemd":
        return run_cmd(["systemctl", "--user", "start", "--no-block", f"{name}.service"], dry_run).returncode == 0
    line = cron_line_of(record) if kind == "cron" else None
    if line is None:
        return False
    env = {"HOME": str(Path.home()), "LOGNAME": getpass.getuser(), "SHELL": "/bin/sh", "PATH": "/usr/bin:/bin"}
    return spawn_cmd(["sh", "-c", cron_command(line)], dry_run, env=env) is not None


MACHINE_SPACES = Path(".spaces") / "spaces.yaml"


def remember_space(root, task: dict | None = None) -> Path:
    """Note a checkout that has a sync task in `~/.spaces/spaces.yaml`, outside every
    repository: the person's chains run over the spaces on this machine that they own."""
    root = Path(root).resolve()
    path = Path.home() / MACHINE_SPACES
    data = read_yaml(path, {}) or {}
    rows = [r for r in (data.get("spaces") or []) if Path(str(r.get("root"))).resolve() != root]
    try:
        space = spaceyaml.load(root)
        name, owner, profile = space.name, space.owner, space.profile
    except Exception:
        name, owner, profile = root.name, "", ""
    rows.append({"name": name, "root": root.as_posix(), "owner": owner, "profile": profile,
                 "task": (task or {}).get("name")})
    write_yaml(path, {"spaces": rows})
    return path


def forget_space(root) -> bool:
    """Take a checkout off `~/.spaces/spaces.yaml` (offboard, or a checkout moved away)."""
    root = Path(root).resolve()
    path = Path.home() / MACHINE_SPACES
    data = read_yaml(path, {}) or {}
    rows = data.get("spaces") or []
    keep = [r for r in rows if Path(str(r.get("root"))).resolve() != root]
    if len(keep) != len(rows):
        write_yaml(path, {"spaces": keep})
    return len(keep) != len(rows)


def signing_key_check(root) -> str:
    import gate
    return f"git -C {Path(root)} show origin/main:{gate.ALLOWED_SIGNERS}"


def make_signing_key(root) -> str:
    """This computer's commit-signing key for the space at `root` (`sync.make_signing_key`, idempotent): made when
    the computer has none and listed in the space's protected `.github/allowed_signers`, never in a cloud run (the
    key is the gate's proof that no cloud run made a commit, `gate._key_signed`). '' when done, else what failed."""
    import sync
    if os.environ.get("CLAUDE_CODE_REMOTE", "").lower() == "true":
        return "a cloud run makes no signing key: the computer's own sync makes it"
    with contextlib.redirect_stdout(io.StringIO()) as said:
        made = sync.make_signing_key(Path(root))
    return "" if made == sync.OK else (said.getvalue().strip()[:200] + ": install OpenSSH (ssh-keygen), then the "
                                       "daily run makes the key")


def signing_key_step(root) -> Step:
    """Whether main's `.github/allowed_signers` lists this computer's key (read only). Until it does, every new
    signature this computer lands waits for a click (`gate._key_signed`): the person's one click on the sync's
    protected pull request when the line waits there, else the agent's `make_signing_key`."""
    import gate
    import sync
    root, check = Path(root), signing_key_check(root)
    pub = Path(f"{sync.signing_key()}.pub")
    public = " ".join(pub.read_text(encoding="utf-8").split()[:2]) if pub.is_file() else ""
    if not public:
        return Step("signing key", False, check, "this computer's commits are not key-signed: it has no signing key "
                    "(`python _meta/tools/sync.py --signing-key` makes it), so every new signature waits for a click")
    if public in (gate._show(root, "origin/main", gate.ALLOWED_SIGNERS) or ""):
        return Step("signing key", True, check, "main lists this computer's commit-signing key")
    local = root / gate.ALLOWED_SIGNERS
    if public in (local.read_text(encoding="utf-8") if local.is_file() else ""):
        # every computer's line reads alike: only its key's end, read here on the computer, tells the person's apart
        return Step("signing key", False, check, "this computer's key is not on main's .github/allowed_signers yet: it "
                    "lands with your one click on the sync's protected pull request; click only if the line it adds "
                    f"ends in {public[-16:]} (no computer of yours made a line that ends otherwise); till then every "
                    "new signature from this computer waits for a click", needs_owner=True)
    return Step("signing key", False, check, "this computer's key is not in .github/allowed_signers: the daily run "
                "lists it (`python _meta/tools/sync.py --signing-key`); till then every new signature waits for a click")


def register_sync(report, root, name, minutes, offline, dry_run) -> dict:
    """Register `sync.py` every `minutes`. Offline: recorded, not registered (the command of this system's
    scheduler; a Linux is recorded with cron, since whether it has a systemd user session is only found by
    asking systemctl, which offline mode never does)."""
    root = Path(root)
    if not dry_run:
        import sync
        made = make_signing_key(root)
        pub = Path(f"{sync.signing_key()}.pub")
        tail = (pub.read_text(encoding="utf-8").split() + ["", ""])[1][-16:] if not made and pub.is_file() else ""
        report.add(f"signing key {name}", not made, signing_key_check(root), made or (
            "this computer's commit-signing key is listed in .github/allowed_signers: it lands with the first fill, "
            f"else with your one click on the sync's protected pull request, if the line it adds ends in {tail}"),
            needs_owner=bool(made))
    script, task = root / "_meta" / "tools" / "sync.py", f"{task_prefix()}sync-{name}"
    args = [script, "--root", root, "--release-leg"]
    if offline:
        kind = {"Windows": "schtasks", "Darwin": "launchd"}.get(SYSTEM, "cron")
        if kind == "schtasks":
            line = f"{CONHOST} --headless {_argline(sys.executable, args)}"
            create = ["schtasks", "/Create", "/F", "/TN", task, "/XML", f"{task}.xml"]
            check = ["schtasks", "/Query", "/TN", task, "/XML"]
        elif kind == "launchd":
            ident, domain = LAUNCHD_PREFIX + task, f"gui/{user_id()}"
            line = launch_agent(task, root, sys.executable, args, None, minutes)   # the file launchd would be given
            plist = Path.home() / "Library" / "LaunchAgents" / f"{ident}.plist"
            create = ["launchctl", "bootstrap", domain, str(plist)]
            check = ["launchctl", "print", f"{domain}/{ident}"]
        else:
            line = cron_line(minutes, sys.executable, script, root, task, os.environ.get("PATH", ""))
            create, check = ["crontab", "-"], ["crontab", "-l"]
        if not dry_run:
            write_text(root / "_meta" / "state" / "sync-task.txt",
                       " ".join(create) + "\n" + line + "\n" + "check: " + " ".join(check) + "\n")
        report.add(f"sync task {name}", True, "cat _meta/state/sync-task.txt",
                   "dry run: nothing recorded" if dry_run else "offline: command recorded")
        return {"name": task, "kind": kind, "line": None if kind == "launchd" else line,
                "create": create, "check": check, "root": str(root)}
    record = register_task(report, f"sync task {name}", task, root, args, minutes=minutes, dry_run=dry_run)
    if not dry_run:
        remember_space(root, record)
    return record


ROUTINE_PROMPTS = Path("_meta") / "state" / "routines"
ELSEWHERE_STEP = "routines on this computer"   # begins "routine": the progress page files it under Routines
ELSEWHERE = "Your routines run on {name}; say 'run my routines here' to move them"
IN_THE_APP = ("Your routines run as scheduled tasks in the Claude app, so this computer's scheduler runs none of them; "
              "say 'run my routines here' to move them to it")
UNCLEAR_HOST = ("Which computer runs your routines is unclear, so none of them runs; say 'run my routines here' on the "
                "computer that should run them")
IN_THE_CLOUD = ("Your sweep runs in the cloud as a scheduled task, and your daily on the computer it moved from; this "
                "computer's sync lands what the sweep leaves open")


def _onboard_state(workspace) -> dict:
    """onboard.yaml as a mapping; {} when there is none or it cannot be read."""
    try:
        state = read_yaml(Path(workspace) / "_meta" / "state" / "onboard.yaml", {})
    except (OSError, yaml.YAMLError):
        return {}
    return state if isinstance(state, dict) else {}


def _harness_checks(workspace) -> list:
    rows = _onboard_state(workspace).get("routine_checks")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def routines_elsewhere(workspace) -> Step | None:
    """Why this computer's scheduler runs none of the routines, as a step under Routines that fails nothing: they run
    in the cloud, another computer runs them, the app's own scheduled tasks do (so recorded, or read back as
    `routine_checks` while nothing is recorded), or which computer does is unclear (`routine.host`). None when they
    run here, or nothing says where."""
    import routine
    record = routine.host(workspace)
    check = f"cat {(Path(workspace) / routine.HOST).as_posix()}"
    if record is None:
        return Step(ELSEWHERE_STEP, True, check, IN_THE_APP) if _harness_checks(workspace) else None
    if record.get("unclear"):
        return Step(ELSEWHERE_STEP, True, check, UNCLEAR_HOST)
    if record["runner"] == "cloud":
        if record.get("computer") not in (None, routine.this_machine()):
            return Step(ELSEWHERE_STEP, True, check, IN_THE_CLOUD)
        # the computer the move ran on keeps its tasks (its sweep steps aside), where its scheduler held them
        return Step(ELSEWHERE_STEP, True, check, IN_THE_APP) if _harness_checks(workspace) else None
    if record["machine"] != routine.this_machine():
        return Step(ELSEWHERE_STEP, True, check, ELSEWHERE.format(name=record["name"] or "another computer"))
    return Step(ELSEWHERE_STEP, True, check, IN_THE_APP) if record["runner"] != "os" else None


def _no_claude(report) -> bool:
    """Whether this machine lacks the `claude` command the OS tasks would start (the desktop app alone installs
    none); a failed step says so."""
    import routine
    if routine.find_claude() is not None:
        return False
    report.add("routines as OS tasks", False, "claude --version",
               "no `claude` command on this machine, so OS tasks would never run: schedule the "
               "routines with the app's own scheduled tasks if they persist, and record them "
               "with --routines-scheduled")
    return True


def register_routines(report, workspace, offline=False, dry_run=False, here=False) -> list:
    """The routines' schedule that outlives the session: each routine rendered under
    `_meta/state/routines/` becomes an OS task that runs `routine.py <prompt>` at the routine's
    times, which hands the prompt to `claude -p`. Recorded as `routine_tasks` for `--check`.
    One computer runs them, through one scheduler: where another computer or the app's own scheduled
    tasks run them, nothing is registered and one step says where (`routines_elsewhere`); with nothing
    recorded, this computer claims them once their tasks are registered (`routine.claim_host`). `here`
    (`--routines-here`) registers them whatever is recorded and claims nothing: the routines move only
    once the probe has passed."""
    workspace = Path(workspace).resolve()
    name = spaceyaml.load(workspace).name
    records = []
    elsewhere = None if here else routines_elsewhere(workspace)
    if elsewhere is not None:
        report.add(elsewhere.name, elsewhere.done, elsewhere.check, elsewhere.result)
        return records
    if not offline and _no_claude(report):
        return records
    for prompt in sorted((workspace / "_meta" / "state" / "routines").glob("*.md")):
        meta = yaml.safe_load(prompt.read_text(encoding="utf-8").split("---")[1]) or {}
        routine, schedule = meta.get("name") or prompt.stem, str(meta.get("schedule") or "")
        task, times = f"{task_prefix()}{routine}-{name}", daily_times(schedule)
        label = f"routine task {task}"
        if offline:
            report.add(label, True, f"cat {prompt}", "offline: not registered")
            continue
        if times is None:
            report.add(label, False, f"cat {prompt}", f"schedule {schedule!r} is not a list of daily times")
            continue
        record = register_task(report, label, task, workspace, [workspace / "_meta" / "tools" / "routine.py", prompt],
                               times=times, cron=schedule, dry_run=dry_run)
        record["log"] = f"{routine}-{name}"   # routine.py's log, whatever prefix the task has
        records.append(record)
    if records and not dry_run:
        path = workspace / "_meta" / "state" / "onboard.yaml"
        state = read_yaml(path, {}) or {}
        state["routine_tasks"] = records
        write_yaml(path, state)
        import routine
        if not here and routine.host(workspace) is None:
            routine.claim_host(workspace, "os", dt.datetime.now().astimezone())
    return records


def _task_file(check: str) -> str:
    """The file a harness task's read-back names (`test -f <path>`, `grep -c '<text>' <path>`), else the read-back."""
    words = check.split(None, 2)
    if len(words) == 3 and words[0] == "test" and words[1] in ("-s", "-f", "-d"):
        return words[2].strip("'\"")
    grep = re.match(r"grep -c '[^']+' (.+)$", check)
    return grep.group(1).strip().strip("'\"") if grep else check


def routines_here(report, workspace) -> list:
    """`--routines-here`: the routines move to this computer's scheduler, in an order that leaves the old runner as
    it is until this one is proved. The probe runs first, through its own one-shot task, twice, as the build proves
    the routines (`start.prove_routines`): a probe that fails registers nothing and changes no record. Then this
    computer's OS tasks are registered; a registration that fails takes back what it did (`_take_back`). Only then is
    this computer the host (`routine.claim_host`, `os`), when every other computer's runs stop by themselves, and the
    app's scheduled tasks the routines ran as are no longer read back (`routine_checks` goes). Returns those tasks to
    turn off (the file each read-back named), else []."""
    import routine
    import start
    workspace = Path(workspace).resolve()
    if not any((workspace / ROUTINE_PROMPTS).glob("*.md")) or _no_claude(report):
        return []
    if not start.prove_routines(report, workspace).get("ok"):
        return []
    before, failed = _onboard_state(workspace), len(report.failed())
    harness = _harness_checks(workspace)
    records = register_routines(report, workspace, here=True)
    if not records or len(report.failed()) > failed:
        _take_back(report, workspace, records, before)
        return []
    record = routine.claim_host(workspace, "os", dt.datetime.now().astimezone())
    state = _onboard_state(workspace)
    if state.pop("routine_checks", None) is not None:
        write_yaml(workspace / "_meta" / "state" / "onboard.yaml", state)
    report.add(ELSEWHERE_STEP, True, f"cat {(workspace / routine.HOST).as_posix()}",
               f"your routines run on {record['name']} from now on")
    return list(dict.fromkeys(_task_file(str(r.get("check") or "")) for r in harness if r.get("check")))


def _take_back(report, workspace: Path, records: list, before: dict) -> None:
    """A move whose registration failed after the probe passed: the tasks it registered on this computer are removed
    again (not when this computer already ran the routines through them), and the task records it replaced come back,
    so the old runner and its records stay as they were."""
    import offboard
    import routine
    record = routine.host(workspace) or {}
    here = (record.get("machine") == routine.this_machine() and record.get("runner") == "os") or (
        record.get("runner") == "cloud" and record.get("computer") == routine.this_machine())
    if not here:
        for task in records:
            if isinstance(task, dict) and task.get("name"):
                offboard.remove_sync_task(report, workspace, {"routine_tasks": [task]}, task["name"], False,
                                          label=f"routine task {task['name']} taken back")
    state = _onboard_state(workspace)
    if "routine_tasks" in before:
        state["routine_tasks"] = before["routine_tasks"]
    else:
        state.pop("routine_tasks", None)
    write_yaml(workspace / "_meta" / "state" / "onboard.yaml", state)


PAUSE_HOUR = 6   # a pause without an end lasts until this hour the next morning
PAUSE_CLOUD = ("The cloud's next sweep reads the pause once this computer's sync lands it; away from this computer, "
               "turn its task off on claude.ai instead.")
PAUSE_ELSEWHERE = ("None of your routines' tasks is on this computer, so nothing was paused. Say 'pause my routines' "
                   "on the computer that runs them.")


def _runs_here(workspace) -> str | None:
    """Which scheduler of this computer holds the routines, read back now: "os" when every recorded routine task
    reads back here, "harness" when every recorded read-back of the app's own tasks passes here, else None."""
    state = _onboard_state(workspace)
    tasks = [t for t in state.get("routine_tasks") or [] if isinstance(t, dict) and t.get("check")]
    try:
        if tasks and all(task_step(f"routine task {t.get('name')}", t).done for t in tasks):
            return "os"
    except (IndexError, KeyError, TypeError, OSError):   # a record a merge changed is one that does not read back
        pass
    checks = _harness_checks(workspace)
    if checks and all(readback(str(r.get("check") or "")) is True for r in checks):
        return "harness"
    return None


def pause_end(until: str | None, now: dt.datetime) -> dt.datetime:
    """When a pause ends: `until` (ISO; a time without a zone is this computer's own, a date alone that day at
    PAUSE_HOUR), else PAUSE_HOUR the next morning. ValueError, in plain words, for what is no moment or has passed."""
    now = now.astimezone()
    if until is None:
        end = dt.datetime.combine(now.date(), dt.time(PAUSE_HOUR)).astimezone()
        return end if end > now else dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time(PAUSE_HOUR)).astimezone()
    text = str(until).strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            end = dt.datetime.combine(dt.date.fromisoformat(text), dt.time(PAUSE_HOUR))
        else:
            end = dt.datetime.fromisoformat(text[:-1] + "+00:00" if text[-1:] in ("Z", "z") else text)
    except ValueError:
        raise ValueError(f'"{text[:40]}" is not a date or a time: give one like 2026-11-02 or 2026-11-02T09:00') from None
    if not end.tzinfo:
        try:
            end = end.astimezone()
        except (OSError, OverflowError):   # past what this computer's own time can say (Windows: 9999, before 1970)
            if end <= now.replace(tzinfo=None):
                raise ValueError("that time has passed: give one still to come") from None
            raise ValueError("that time is too far ahead: give one in the next few years") from None
    if end <= now:
        raise ValueError("that time has passed: give one still to come")
    return end


def pause_routines(workspace, until: str | None = None, now=None, resume: bool = False) -> tuple:
    """`--pause` and `--resume`: (done, what to say). A pause sets the record's `paused_until` (`pause_end`), which
    every computer's runs read before they start; resuming clears it. With nothing recorded yet, this computer is
    recorded as the one that runs them first, as the build would record it, but only when their tasks read back here
    (`_runs_here`); else nothing is paused and the line says to pause them where they run. A record that is not one
    is not touched. While they run in the cloud, the line says when the cloud hears of the pause (PAUSE_CLOUD)."""
    import onboarding_page
    import routine
    workspace = Path(workspace).resolve()
    now = (now or dt.datetime.now()).astimezone()
    record = routine.host(workspace)
    if record is not None and record.get("unclear"):
        return False, UNCLEAR_HOST
    if resume:
        if record is None or routine.paused_until(record, now) is None:
            return True, "Your routines are not paused."
        routine.set_pause(workspace, None)
        return True, "Your routines run again from their next time."
    try:
        end = pause_end(until, now)
    except ValueError as exc:
        return False, str(exc)
    if record is None:   # nothing says where they run: here only when their tasks read back on this computer
        runner = _runs_here(workspace)
        if runner is None:
            return False, PAUSE_ELSEWHERE
        record = routine.claim_host(workspace, runner, now)
    routine.set_pause(workspace, end)
    said = f"Your routines are paused until {onboarding_page.words(end, now)}, the history's reading with them."
    if record["runner"] == "cloud":
        return True, f"{said} {PAUSE_CLOUD}"
    if record["machine"] != routine.this_machine():
        said += f" {record['name'] or 'The other computer'} hears of it at its next sync."
    if record["runner"] != "os":   # each prompt's first step, `chain.py gate`, reads the pause
        said += " They run as scheduled tasks in the Claude app, and each one reads the pause first and stops."
    return True, said


# ----- run my routines in the cloud --------------------------------------------------------------
# A scheduled task on claude.ai runs the sweep, read-only, while every computer is closed; the daily chain stays on
# the computer (docs/design/2026-10-28-cloud-sweep-design.md). This computer never starts it: the agent renders,
# creates and proves it; here the move is checked first and recorded last, only once a proof run of the task,
# landed on main, showed every refusal.
CLOUD_CHAINS = ("sweep",)
CLOUD_PROOF = "cloud-proof"   # the run line a proof run lands (`cloud.py proof-line`)
PROOF_HOURS = 48   # a proof older than this proves a task as it was, not as it is
# What a proof run owes, on its proof line: an edit of a protected file, an app tool it was not given and a request
# past the network limit, each refused.
CLOUD_REFUSALS = {"sweep": ("edit", "tool", "network")}
CLOUD_CLEAR = "Nothing keeps your routines from moving to the cloud."
CLOUD_HISTORY = ("Your history is still being read, and only a computer reads it: your sweep can move to the cloud "
                 "once it is done.")
CLOUD_NO_GRANT = ("None of your apps is granted to your routines yet, so the cloud's sweep would have nothing to read: "
                  "connect your apps and run the build again first.")
CLOUD_NO_GH = "GitHub's command line is not on this computer, so your repositories could not be checked."
CLOUD_NO_REPO = "{what} has no repository on GitHub, so the cloud's task cannot attach it."
CLOUD_UNREAD = ("The repository {slug} ({what}) could not be read on GitHub: check that gh is signed in on this "
                "computer (gh auth status).")
CLOUD_UNPROTECTED = ("The repository {slug} ({what}) does not protect main, so a run acting as you could push to it "
                     "without the checks.")
CLOUD_UNPROTECTED_PUSH = ("The repository {slug} ({what}) does not protect main, and your account can push to it, so a "
                          "run acting as you could push to it without the checks: ask whoever administers it to protect "
                          "main.")
CLOUD_ADMINS = ("The repository {slug} ({what}) lets your own account push to main past the checks: turn on 'Do not "
                "allow bypassing the above settings' for main.")
CLOUD_PROTECTION_UNREAD = "Whether the repository {slug} ({what}) holds your own account to the checks could not be read."
CLOUD_NO_PROOF = ("The {chains} {have} no proof run from the last {hours} hours that shows every refusal, so your "
                  "sweep stays where it runs.")
# What each refusal a proof run tries is, in the person's words (CLOUD_REFUSALS).
REFUSED = {"edit": "a change to a protected file", "tool": "an app tool it was not given",
           "network": "a request to a host outside its network limit"}
CLOUD_UNREFUSED = "The {proof} that the cloud does not refuse {what}, so your sweep stays where it runs."
CLOUD_STOPPED = ("Your sweep is stopped: the {proof} that the cloud no longer refuses {what}, so its task stays off; say "
                 "'run my routines here' to run it on this computer again.")
CLOUD_NOT_RENDERED = ("The cloud's {chain} task was not rendered on this computer, so the repositories and the apps it "
                      "was given are unknown: render it again, then move it.")
CLOUD_MOVED = ("Your sweep runs in the cloud from now on, as the scheduled task {sweep}, and your daily stays on this "
               "computer; every other computer's runs step aside as soon as they read the move, which this computer's "
               "sync lands.")
CLOUD_OFF_FIRST = ("Your sweep runs in the cloud: turn off its scheduled task {sweep} first, so it never runs beside this "
                   "computer's.")
CLOUD_ALREADY = ("Your sweep already runs in the cloud, as the scheduled task {sweep}: prove and update that one rather "
                 "than making a new one.")
CLOUD_EARLIER = ("The cloud's earlier {tasks} {ids} still {run} beside the new one: turn {them} off now, so the sweep "
                 "never runs twice.")
CLOUD_STAY = "Your sweep stays where it runs: {why}."   # a move refused for what it was given
CLOUD_STEP = "routines in the cloud"   # begins "routine": the progress page files it under Routines
# The run lines the sweep writes: the chain tool's own (`chain.py`, the name the sync's `not_started` reads) and the
# agent's (the routine prompt's).
CLOUD_RUN_LINES = {"sweep": ("sweep_chain", "sweep-chain")}
CLOUD_RUNS = "Your sweep runs in the cloud, as the scheduled task {sweep}, and your daily on your computer. {lines}."
CLOUD_STOPPED_TASKS = "The cloud's task is {sweep}. {lines}."
CLOUD_RAN = "the {chain}'s newest run line was written {ago}"
CLOUD_NOT_RAN = "the {chain} has written no run line yet"
CLOUD_NEVER_RAN = "It has written no run line yet"
CLOUD_TASK_ID = "{ids} is not the id of a scheduled task on claude.ai, which starts with trig_"
CLOUD_TASK_IDS = "{ids} are not the ids of scheduled tasks on claude.ai, which start with trig_"
CLOUD_ZONE = "{zone} is not a time zone: give its IANA name, as Europe/Amsterdam"
CLOUD_NOT_HERE = ("your routines do not run on this computer, and the daily stays on the computer the sweep moves from: "
                  "move it from the computer that runs them, or say 'run my routines here' first")
CLOUD_RENDER = ("the task rendered on this computer names its repositories or its apps in a way the move cannot keep: "
                "render it again")
# A repository url names one on GitHub only with a scheme (https://, ssh://, a cloud run's http:// proxy) or in git's
# user@host: form; anything else (/home/ada/brain, C:\Users\ada\brain, ../brain, file://...) is a folder on a computer.
REMOTE_URL = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*)://|[^\s/\\@:]+@[^\s/\\@:]+:")


def zone_unknown(zone) -> bool:
    """Whether `zone` is no time zone: not of an IANA name's shape (`routine.ZONE`), or a name this computer's zone data
    does not know (Mars/Olympus). Without any zone data (Windows without the tzdata package) the shape is all there
    is to check, as `routine` keeps a zone it cannot read at its own offset."""
    import zoneinfo
    import routine
    if not (isinstance(zone, str) and routine.ZONE.fullmatch(zone)):
        return True
    try:
        zoneinfo.ZoneInfo(zone)
    except (KeyError, ValueError, OSError):   # ZoneInfoNotFoundError is a KeyError
        return bool(zoneinfo.available_timezones())
    return False


def _cloud_tasks(workspace) -> dict | None:
    """The scheduled task the sweep runs as while it runs in the cloud ({"sweep": its id}); None for any other record,
    or none."""
    import routine
    record = routine.host(workspace)
    if not record or record.get("unclear") or record.get("runner") != "cloud":
        return None
    return record["tasks"]


def _slug_of(url) -> str | None:
    """owner/name of a repository url (REMOTE_URL), by the rule `ghrest.repo_slug` reads a checkout's origin with: the
    last two path segments, `.git` dropped. None for a local path however it is written, and for what has no two plain
    names at its end."""
    import ghrest
    text = str(url or "").strip()
    remote = REMOTE_URL.match(text)
    if not remote or (remote.group(1) or "").lower() == "file":
        return None
    parts = [p for p in re.split(r"[/:\\]", text) if p]
    if len(parts) < 2:
        return None
    owner, name = parts[-2], parts[-1].removesuffix(".git")
    return f"{owner}/{name}" if ghrest.SEGMENT.fullmatch(owner) and ghrest.SEGMENT.fullmatch(name) else None


def _cloud_repositories(workspace: Path) -> tuple:
    """(the repository of every space the routines run over and of every space each mounts, as {slug: what it is},
    the first space named for one mounted twice; one sentence for each that has none on GitHub). The spaces are the
    chains' own (`chain.spaces_on_machine`), the workspace first; a mount's repository is the one its row names,
    else its checkout's origin."""
    import chain
    import ghrest
    repos, missing = {}, []
    for root in chain.spaces_on_machine(workspace):
        space = spaceyaml.load(root)
        what = "your workspace" if Path(root).resolve() == workspace else f"your space {space.name}"
        found = [(what, ghrest.repo_slug(root))]
        for mount in space.mounts:
            name = str(mount.get("name") or "")
            on_disk = space.mount_root(name)
            slug = _slug_of(mount.get("repo")) or (ghrest.repo_slug(on_disk) if on_disk else None)
            found.append((f"the mount {name} of {what}", slug))
        for label, slug in found:
            if slug is None:
                missing.append(CLOUD_NO_REPO.format(what=label[:1].upper() + label[1:]))
            else:
                repos.setdefault(slug, label)
    return repos, missing


def _gh_json(gh, *args) -> tuple:
    """(the JSON one `gh api` call printed, else None; the finished call, None when gh is not installed)."""
    done = gh(*args)
    if done is None or done.returncode != 0:
        return None, done
    try:
        return json.loads(done.stdout or ""), done
    except ValueError:
        return None, done


def _admins_held(gh, slug: str, what: str) -> str | None:
    """None when the person's own account cannot push to `slug`'s main past its checks; else one sentence that says
    why it can, or that it could not be read (CLOUD_NO_GH when gh is not installed). `enforce_admins` binds only
    those who administer a repository. One the person does not administer, such as a brain the company's owner set
    up, holds their account by its protection alone, which is not theirs to change: a main they can push to with no
    protection is named (CLOUD_UNPROTECTED_PUSH, read from the branch, which needs no admin), and one they cannot
    push to is no way past anything. An answer that does not say what the person may do is unread, never "not
    theirs"."""
    repo, done = _gh_json(gh, "api", f"repos/{slug}")
    if done is None:
        return CLOUD_NO_GH
    if not isinstance(repo, dict):
        return CLOUD_UNREAD.format(slug=slug, what=what)
    rights = repo.get("permissions")
    admin, push = (rights.get("admin"), rights.get("push")) if isinstance(rights, dict) else (None, None)
    if admin is not True:
        if admin is False and push is False:
            return None
        if admin is not False or push is not True:
            return CLOUD_PROTECTION_UNREAD.format(slug=slug, what=what)
        branch, _ = _gh_json(gh, "api", f"repos/{slug}/branches/main")
        protected = branch.get("protected") if isinstance(branch, dict) else None
        if protected is True:
            return None
        return (CLOUD_UNPROTECTED_PUSH if protected is False else CLOUD_PROTECTION_UNREAD).format(slug=slug, what=what)
    held, done = _gh_json(gh, "api", f"repos/{slug}/branches/main/protection/enforce_admins")
    enabled = held.get("enabled") if isinstance(held, dict) else None
    if enabled is True:
        return None
    if enabled is False:
        return CLOUD_ADMINS.format(slug=slug, what=what)
    said = f"{done.stdout} {done.stderr}" if done is not None else ""
    return (CLOUD_UNPROTECTED if re.search(r"not protected", said, re.I) else CLOUD_PROTECTION_UNREAD).format(
        slug=slug, what=what)


def runs_here(workspace) -> bool:
    """Whether this computer runs the routines, so it can keep the daily once the sweep moves: the record names it as
    the host, or, while the sweep runs in the cloud, as the computer the move ran on (or none); with no record, the
    routines' tasks read back here (`_runs_here`)."""
    import routine
    record = routine.host(workspace)
    if record is None:
        return _runs_here(workspace) is not None
    if record.get("unclear"):
        return False
    if record["runner"] == "cloud":
        return record.get("computer") in (None, routine.this_machine())
    return record["machine"] == routine.this_machine()


def cloud_grant(workspace) -> dict:
    """The apps a move to the cloud grants the routines, {"read", "write", "send"} (the sweep's task takes the read
    tools alone, `cloud.task_grant`): what this computer's verdicts and
    probe give (`routine.grant`). A computer with no probe of its own, while the routines run in the cloud, takes the
    grant the record kept at the move instead (a record that names no computer, or the move's own computer when
    its probe is gone): there the build proves nothing. A computer that replaced the one they moved from is refused
    by `runs_here` until the routines run there."""
    import routine
    record = routine.host(workspace) or {}
    if not routine.read_probe() and record.get("runner") == "cloud" and isinstance(record.get("grant"), dict):
        return {part: list(record["grant"].get(part) or []) for part in routine.GRANT_KEYS}
    got = routine.grant(workspace)
    return {part: list(got.get(part) or []) for part in routine.GRANT_KEYS}


def cloud_blockers(workspace, gh=None) -> list:
    """`--routines-cloud --check`: what keeps the routines from moving to the cloud, one sentence each, in this order:
    the history still being read (only a computer reads it, `backfill.active`); the routines not running on this
    computer (`runs_here`, CLOUD_NOT_HERE: the daily stays on the move's computer); no app granted to the routines
    (`cloud_grant`); a space the routines run over, or a space one of them mounts, with no repository on GitHub
    (the cloud attaches GitHub repositories only); a repository whose `main` lets the person's own account past the
    checks (`enforce_admins` off, or no protection at all), or that could not be read. `gh` is the GitHub command line
    (`ghrest.runner`), asked through REST only. [] when nothing does. Writes nothing."""
    import backfill
    import ghrest
    import routine
    workspace = Path(workspace).resolve()
    said = []
    if backfill.active(workspace):
        said.append(CLOUD_HISTORY)
    if not runs_here(workspace):
        said.append(CLOUD_STAY.format(why=CLOUD_NOT_HERE))
    if not cloud_grant(workspace)["read"]:
        said.append(CLOUD_NO_GRANT)
    repos, missing = _cloud_repositories(workspace)
    said += missing
    gh = gh or ghrest.runner(workspace)
    for slug, what in repos.items():
        why = _admins_held(gh, slug, what)
        if why == CLOUD_NO_GH:
            return said + [why]
        if why:
            said.append(why)
    return said


def _proof_lines(workspace) -> dict:
    """{chain: its `cloud-proof` lines, oldest first, each as (its moment, the line)} in this checkout's runs.jsonl
    (where the sync brings them from main); two lines from the same second keep the file's order. A line that cannot
    be read or dated is passed over."""
    try:
        text = (Path(workspace) / "_meta" / "state" / "runs.jsonl").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    found = {}
    for raw in text.splitlines():
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(line, dict) or line.get("routine") != CLOUD_PROOF or line.get("chain") not in CLOUD_CHAINS:
            continue
        try:
            at = pages.parse_iso(line.get("at"))
        except (TypeError, ValueError, OverflowError):   # OverflowError: a moment before the first UTC has
            continue
        found.setdefault(line["chain"], []).append((at, line))
    return {chain: sorted(lines, key=lambda seen: seen[0]) for chain, lines in found.items()}


def _proved(chain: str, line: dict) -> bool:
    """Whether a proof line saw every refusal its chain owes (CLOUD_REFUSALS), each `true`."""
    return all(line.get(seen) is True for seen in CLOUD_REFUSALS[chain])


def cloud_proofs(workspace, now=None) -> set:
    """The chains a proof run proved: those whose newest proof line (`_proof_lines`) is from the last PROOF_HOURS and
    saw every refusal (`_proved`). The newest run is the task as it is, so an older proof that showed every refusal
    no longer counts once a later one did not. A `now` without a zone is this computer's own."""
    since = (now or dt.datetime.now(dt.timezone.utc)).astimezone() - dt.timedelta(hours=PROOF_HOURS)
    return {chain for chain, lines in _proof_lines(workspace).items()
            if lines[-1][0] >= since and _proved(chain, lines[-1][1])}


def cloud_unrefused(workspace) -> dict:
    """{chain: the refusals the cloud did not make}, for the chains with one: a refusal written `false` on a proof
    line that no later line proved again (`_proved`). A later line that leaves a refusal out, or writes it as
    anything but `true` or `false`, proves nothing either way, so it hides none. And unlike a proof, which counts for
    PROOF_HOURS, a refusal missing counts until a later proof shows every refusal, however long ago it was: nothing
    else mends it."""
    missing = {}
    for chain, lines in _proof_lines(workspace).items():
        last = max((i for i, (_, line) in enumerate(lines) if _proved(chain, line)), default=-1)
        since = [line for _, line in lines[last + 1:]]
        seen = [refusal for refusal in CLOUD_REFUSALS[chain] if any(line.get(refusal) is False for line in since)]
        if seen:
            missing[chain] = seen
    return missing


def cloud_stopped(workspace) -> dict:
    """{chain: the refusals the cloud did not make} while the routines are stopped, else {}. Stopped is three things at
    once: the record says they run in the cloud, so every computer's own tasks step aside and are off; and a proof
    showed a refusal missing that no later proof showed again (`cloud_unrefused`), so the cloud's task, proved in
    place, stays off. Nothing runs the sweep until the person moves it here. A refusal missing under any other record
    came from tasks made new for a proof: the routines run where they ran."""
    return cloud_unrefused(workspace) if _cloud_tasks(workspace) else {}


def unrefused_words(missing: dict) -> str:
    """What the cloud does not refuse, in the person's words (REFUSED), for `missing` as `cloud_unrefused` gives it."""
    seen = [words for refusal, words in REFUSED.items() if any(refusal in found for found in missing.values())]
    return seen[0] if len(seen) == 1 else f"{', '.join(seen[:-1])} or {seen[-1]}"


def unrefused_sentence(workspace, missing: dict) -> str:
    """The one sentence a move refused for a refusal missing says: which task's proof showed it, and what the cloud
    does not refuse (`unrefused_words`). While the sweep runs in the cloud its task was proved in place, so it is
    off and stays off: the sweep is stopped until the person moves it here (CLOUD_STOPPED); else they
    stay where they run (CLOUD_UNREFUSED)."""
    chains = [chain for chain in CLOUD_CHAINS if chain in missing]
    proof = (f"{chains[0]} task's proof run shows" if len(chains) == 1
             else f"{' and '.join(chains)} tasks' proof runs show")
    what = unrefused_words({chain: missing[chain] for chain in chains})
    return (CLOUD_STOPPED if _cloud_tasks(workspace) else CLOUD_UNREFUSED).format(proof=proof, what=what)


def _rendered(chain: str) -> dict | None:
    """What `cloud.py trigger` kept of a task it rendered on this computer (`~/.spaces/cloud-<chain>.json`): its
    `roots` (a list) and its `grant` (a mapping of lists); None when there is none or it is not of that shape."""
    import routine
    try:
        data = json.loads((Path.home() / ".spaces" / f"cloud-{chain}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("roots"), list) or not isinstance(data.get("grant"), dict):
        return None
    if not all(isinstance(data["grant"].get(part, []), list) for part in routine.GRANT_KEYS):
        return None
    return data


def routines_cloud(workspace, tasks: dict, timezone: str, now=None) -> tuple:
    """`--routines-cloud --sweep <task> --timezone <zone>`: (done, what to say, the app's own scheduled
    tasks to turn off). A proof that showed a refusal missing is said before anything else (`cloud_unrefused`,
    `unrefused_sentence`), since the skill keeps the task off for it alone; then a task id or a zone that is none,
    in plain words. The sweep moves only when the history is read (it is read on a computer only), when the task's
    newest proof run from the last PROOF_HOURS showed every refusal (`cloud_proofs`), and when it was rendered here:
    the record keeps the space repositories the task attaches (its roots, never the codebases it also attaches) and
    the apps it was given, as `cloud.py trigger` kept them (`_rendered`); it must read one app at least, or the move
    is refused (CLOUD_NO_GRANT). Then the cloud is the host (`routine.claim_cloud`, a pause still to come kept):
    every computer's sweep stops by itself, and so does every routine of any other computer, while this one keeps
    its daily chain; the app's scheduled task the sweep ran as is no longer read back (its `routine_checks` row
    goes, and its file is returned, as `--routines-here` returns them). A move from the cloud to
    new tasks (moving again, for a space made since) says which of the earlier tasks still run: the cloud's gate
    cannot tell one task from another, so they run beside the new ones until they are turned off. Nothing is
    written when the move is refused."""
    import backfill
    import routine
    workspace = Path(workspace).resolve()
    now = (now or dt.datetime.now()).astimezone()
    missing = cloud_unrefused(workspace)
    if missing:   # the cloud let through what the profile refuses: nothing said before it may hide it
        return False, unrefused_sentence(workspace, missing), []
    wrong = [str(tasks.get(chain))[:40] for chain in CLOUD_CHAINS
             if not routine.TASK_ID.fullmatch(str(tasks.get(chain)))]
    if wrong:
        ids = (CLOUD_TASK_ID if len(wrong) == 1 else CLOUD_TASK_IDS).format(ids=" and ".join(wrong))
        return False, CLOUD_STAY.format(why=ids), []
    if zone_unknown(timezone):
        return False, CLOUD_STAY.format(why=CLOUD_ZONE.format(zone=str(timezone)[:40])), []
    if backfill.active(workspace):
        return False, CLOUD_HISTORY, []
    if not runs_here(workspace):
        return False, CLOUD_STAY.format(why=CLOUD_NOT_HERE), []
    proved = cloud_proofs(workspace, now)
    unproved = [chain for chain in CLOUD_CHAINS if chain not in proved]
    if unproved:
        chains = f"{unproved[0]} task" if len(unproved) == 1 else f"{' and '.join(unproved)} tasks"
        return False, CLOUD_NO_PROOF.format(chains=chains, have="has" if len(unproved) == 1 else "have",
                                            hours=PROOF_HOURS), []
    renders = {}
    for chain in CLOUD_CHAINS:
        renders[chain] = _rendered(chain)
        if renders[chain] is None:
            return False, CLOUD_NOT_RENDERED.format(chain=chain), []
    roots = list(dict.fromkeys(root for chain in CLOUD_CHAINS for root in renders[chain]["roots"]))
    grant = {part: list(dict.fromkeys(tool for chain in CLOUD_CHAINS for tool in renders[chain]["grant"].get(part, [])))
             for part in routine.GRANT_KEYS}
    if not grant["read"]:   # a sweep that may read no app would read nothing
        return False, CLOUD_NO_GRANT, []
    earlier = _cloud_tasks(workspace) or {}
    try:
        record = routine.claim_cloud(workspace, tasks, timezone, roots, grant, now)
    except ValueError:   # the ids and the zone were checked above: what is left is the render's repositories or apps
        return False, CLOUD_STAY.format(why=CLOUD_RENDER), []
    # the prompts rendered again from this version: one rendered before has no tracker step in the daily, which is
    # the tracker's one writer while the sweep is in the cloud, and its sweep no `--chain sweep` to step aside by
    import start
    start.render_routines(workspace, spaceyaml.load(workspace).owner or "the person")
    harness = _harness_checks(workspace)
    # the daily's app task keeps running: the sweep's is the one whose task file or folder names it
    sweep = [r for r in harness if r.get("check") and any(
        "sweep" in part.lower() for part in Path(_task_file(str(r["check"]))).parts[-2:])]
    if sweep:
        state = _onboard_state(workspace)
        state["routine_checks"] = [r for r in harness if r not in sweep]
        if not state["routine_checks"]:
            state.pop("routine_checks")
        write_yaml(workspace / "_meta" / "state" / "onboard.yaml", state)
    to_turn_off = list(dict.fromkeys(_task_file(str(r.get("check") or "")) for r in sweep if r.get("check")))
    said = CLOUD_MOVED.format(**record["tasks"])
    still = [task for task in dict.fromkeys(earlier.values()) if task not in record["tasks"].values()]
    if still:
        one = len(still) == 1
        said += " " + CLOUD_EARLIER.format(tasks="task" if one else "tasks", ids=" and ".join(still),
                                           run="runs" if one else "run", them="it" if one else "them")
    return True, said, to_turn_off


def cloud_off_first(workspace) -> str | None:
    """`--routines-here` while the routines run in the cloud: the one sentence, said before this computer proves
    anything, that names the cloud's task to turn off first (the agent does it; Python cannot reach them), so
    the cloud and this computer never both run a chain. None for any other record."""
    tasks = _cloud_tasks(workspace)
    return CLOUD_OFF_FIRST.format(**tasks) if tasks else None


def ago(then: dt.datetime, now: dt.datetime) -> str:
    """How long before `now` the moment `then` was, in whole units, as a person says it: minutes under an hour, hours
    under two days, days after. A moment less than a minute before `now`, or after it (another clock), is less than
    a minute ago."""
    seconds = (now - then).total_seconds()
    if seconds < 60:
        return "less than a minute ago"
    size, unit = (60, "minute") if seconds < 3600 else (3600, "hour") if seconds < 48 * 3600 else (86400, "day")
    count = int(seconds // size)
    return f"{count} {unit}{'' if count == 1 else 's'} ago"


def routines_in_the_cloud(workspace, now=None) -> Step | None:
    """The plain check's row while the routines run in the cloud (spec section 6), under Routines and failing nothing:
    the scheduled task the record names, and how long ago the sweep's newest run line (CLOUD_RUN_LINES) was
    written, read from this checkout's runs.jsonl, which the sync keeps at main; a chain with none says so. Whether
    the sweep stopped running is for this row to show by that age. Once a proof showed a refusal missing that no
    later proof showed again (`cloud_unrefused`), the task, proved in place, stays off and the sweep runs nowhere: the row fails and says so first, with what to say (`unrefused_sentence`). None for any
    other record. A `now` without a zone is this computer's own."""
    import routine
    tasks = _cloud_tasks(workspace)
    if not tasks:
        return None
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone()
    names = {name: chain for chain, lines in CLOUD_RUN_LINES.items() for name in lines}
    try:
        text = (Path(workspace) / "_meta" / "state" / "runs.jsonl").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    newest = {}
    for raw in text.splitlines():
        try:
            line = json.loads(raw)
            chain = names.get(line.get("routine")) if isinstance(line, dict) else None
            if chain:
                at = pages.parse_iso(line.get("at"))
                newest[chain] = max(at, newest.get(chain, at))
        except (TypeError, ValueError, OverflowError):   # a line that cannot be read or dated is passed over
            continue
    said = [CLOUD_RAN.format(chain=chain, ago=ago(newest[chain], now)) if chain in newest
            else CLOUD_NOT_RAN.format(chain=chain) for chain in CLOUD_CHAINS]
    lines = ", and ".join(said) if newest else CLOUD_NEVER_RAN
    lines = lines[:1].upper() + lines[1:]
    check = f"cat {(Path(workspace) / routine.HOST).as_posix()}"
    missing = cloud_unrefused(workspace)
    if missing:
        return Step(CLOUD_STEP, False, check,
                    f"{unrefused_sentence(workspace, missing)} {CLOUD_STOPPED_TASKS.format(**tasks, lines=lines)}")
    return Step(CLOUD_STEP, True, check, CLOUD_RUNS.format(**tasks, lines=lines))


def record_routine_checks(report, workspace, checks) -> list:
    """The harness's own scheduled tasks hold the routines and outlive the session: one read-back
    per rendered routine (`test -f <path>`, `grep -c '<text>' <path>` or a `gh` command), run now
    and recorded as `routine_checks` for `--check` only when every one passes."""
    workspace = Path(workspace).resolve()
    prompts = sorted((workspace / ROUTINE_PROMPTS).glob("*.md"))
    checks = [str(c).strip() for c in checks or [] if str(c).strip()]
    if len(checks) < len(prompts) or not prompts:
        report.add("routines scheduled", False, f"ls {ROUTINE_PROMPTS.as_posix()}",
                   f"one read-back per routine: {len(prompts)} rendered, {len(checks)} given")
        return []
    rows = []
    for n, check in enumerate(checks, 1):
        passed = readback(check)
        rows.append({"name": f"routine scheduled {n}", "check": check})
        report.add(f"routine scheduled {n}", passed is True, check,
                   "reads back" if passed else ("the read-back fails" if passed is False
                                                else "not a read-back --check can run: test -f, grep -c or gh"))
    if all(s.done for s in report.steps[-len(rows):]):
        path = workspace / "_meta" / "state" / "onboard.yaml"
        state = read_yaml(path, {}) or {}
        state["routine_checks"] = rows
        write_yaml(path, state)
        return rows
    return []


def routine_schedule(workspace) -> list:
    """The routines' schedule read back: each OS routine task, each recorded harness read-back.
    Empty when nothing is recorded: rendered prompts alone schedule nothing."""
    state = read_yaml(Path(workspace) / "_meta" / "state" / "onboard.yaml", {}) or {}
    steps = [task_step(f"routine task {t['name']}", t) for t in state.get("routine_tasks") or [] if t.get("check")]
    for row in state.get("routine_checks") or []:
        passed = readback(row.get("check", "")) is True
        steps.append(Step(row.get("name") or "routine scheduled", passed, row.get("check", ""),
                          "reads back" if passed else "the read-back fails: schedule the routine again"))
    return steps


def unscheduled_step(workspace) -> Step:
    """The row for rendered routines nothing has scheduled yet, with the command that does."""
    ws = Path(workspace).as_posix()
    return Step("routines scheduled", False, f"python {ws}/_meta/tools/onboard.py {ws} --os-routines",
                "not scheduled: run the check's command (the OS scheduler outlives this session), or "
                "record the harness's own persistent tasks with --routines-scheduled")


MOUNT_TOKENS = ("BRAIN_READ_TOKEN", "MOUNT_READ_TOKEN")


def mount_token_advice(repo: str, brain: str | None = None) -> str:
    """How the owner makes the checks' mount token, in one sentence: a fine-grained token per repository."""
    owner = str(repo).split("/")[0]
    return (f"the owner adds the repository secret BRAIN_READ_TOKEN to {repo} only: a fine-grained personal access "
            f"token, resource owner {owner}, repository access 'Only select repositories: {brain or 'the brain'}', "
            "permission Contents: read-only, with an expiry. Never an organization secret and never a classic token: "
            "anyone who can push to a repository can read the secrets it sees")


def brain_of_gitmodules(root) -> str | None:
    """owner/name of the brain mount `.gitmodules` records, for the token advice; None when it names none."""
    try:
        text = (Path(root) / ".gitmodules").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found = re.search(r'\[submodule "brain"\][^\[]*?url\s*=\s*(\S+)', text)
    match = MOUNT_URL.fullmatch(found.group(1)) if found else None
    return f"{match.group(1)}/{match.group(2)}" if match else None


def mount_token(report, repo: str, org, dry_run, brain: str | None = None) -> None:
    """The checks' mount token for one repository with mounts: present as this repository's own secret,
    else one owner row with `mount_token_advice`. An organization secret of that name is one owner row of
    its own, to delete it: it reaches every repository of the organization, every colleague's workspace
    included, so every colleague can read it."""
    listed = run_cmd(["gh", "secret", "list", "--repo", repo], dry_run)
    names = {line.split()[0] for line in (listed.stdout or "").splitlines() if line.split()}
    present = listed.returncode == 0 and any(name in names for name in MOUNT_TOKENS)   # the name itself, not a longer one
    report.add(f"mount token {repo}", present or dry_run, f"gh secret list --repo {repo}",
               "present as this repository's own secret" if present else mount_token_advice(repo, brain),
               needs_owner=not present)
    organization_token(report, repo, org, dry_run)


def organization_token(report, repo: str, org, dry_run) -> None:
    """An organization secret of a mount token's name is one owner row, to delete it: it reaches every
    repository of the organization, every colleague's workspace included, so every colleague can read it."""
    owner = org or str(repo).split("/")[0]
    shared = [name for name in MOUNT_TOKENS
              if not dry_run and run_cmd(["gh", "api", f"orgs/{owner}/actions/secrets/{name}", "-q", ".name"],
                                         dry_run).returncode == 0]
    if shared:
        report.add(f"organization token {owner}", False, f"gh api orgs/{owner}/actions/secrets/{shared[0]} -q .name",
                   f"the organization secret {', '.join(shared)} reaches every repository of {owner}, so every "
                   f"colleague can read it: delete it (`gh secret delete {shared[0]} --org {owner}`) and give each "
                   "repository its own fine-grained token that reads the brain only", needs_owner=True)


# ----- mount keys: the checks read each mount with a read-only deploy key the agents make ----------
# A fine-grained token is made by hand and expires; a deploy key is made by the agent, reads one
# repository, read-only, and does not expire. Each consuming repository (a workspace, a project
# space) gets one keypair per repository it mounts: the public half is a read-only deploy key on
# the mounted repository, the private halves together are the consuming repository's secret
# MOUNT_DEPLOY_KEYS, which only its `mounts` job reads (checks.yml). A key is made only when everyone
# who can push to the consuming repository can already read the mounted one, since anyone who can
# push there can read its secrets: so no key reads anything a writer could not read already.
DEPLOY_KEYS_SECRET = "MOUNT_DEPLOY_KEYS"
MOUNT_KEYS_STATE = Path("_meta") / "state" / "mount-keys.yaml"        # in the consuming space: which mounts are keyed
KEY_REQUESTS = Path("_meta") / "state" / "private" / "key-requests"  # in the brain: public keys sent by note, never staged
ATTACHED_KEYS = Path("_meta") / "state" / "mount-keys-attached.yaml"  # in the brain: the answers its workspaces read
# The record removals read lives in the git dir (`key_record_path`), where no merge and no pull request writes it, as
# sync.py keeps its leases: a pull request that force-adds an ignored file overwrites it in the work tree on merge.
# KEY_RECORD is where it was before, read once to carry it over, and the record outside git, where nothing merges.
KEY_RECORD = KEY_REQUESTS / "record" / "attached.yaml"
RECORD_GIT_PATH = "spaces/mount-keys-attached.yaml"
PRIVATE_STATE = Path("_meta") / "state" / "private"
PENDING_DAYS = 7      # a key the brain's agent has not attached by then is asked for again
PENDING_GRACE = 14    # until then the checks warn, not fail, when the brain is unread only through that key
KEY_REFUSED = "the brain's agent refused it: "
LASTING = ("someone who can push to", KEY_REFUSED)   # left-out reasons tried again weekly, not daily
PUBLIC_KEY = re.compile(r"ssh-ed25519 [A-Za-z0-9+/]{68}")             # an ed25519 public key, no comment
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
PLAIN_REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}")


def plain_repo(value) -> str | None:
    """owner/name of a repository text, from `owner/name` or an https GitHub address; None for any
    other shape (an option, a path, another host), so nothing else reaches a `gh api` path."""
    text = str(value or "").strip()
    match = MOUNT_URL.fullmatch(text)
    if match:
        text = f"{match.group(1)}/{match.group(2)}"
    text = text[:-4] if text.endswith(".git") else text
    return text if PLAIN_REPO.fullmatch(text) and text.split("/")[1] not in (".", "..") else None


def mount_key_title(consumer: str) -> str:
    """The deploy key's title on a mounted repository: names the consuming repository, so a re-key
    or an offboarding finds and removes exactly that repository's keys, and nobody else's."""
    return f"spaces checks of {str(consumer).lower()}"


def gitmodules_repos(root) -> list:
    """owner/name of every mount `.gitmodules` records at a plain https GitHub address, the brain first."""
    try:
        text = (Path(root) / ".gitmodules").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found = []
    for block in re.split(r"(?=^\[submodule )", text, flags=re.M):
        url = re.search(r"^\s*url\s*=\s*(\S+)\s*$", block, flags=re.M)
        repo = plain_repo(url.group(1)) if url and MOUNT_URL.fullmatch(url.group(1)) else None
        if repo:
            found.append((0 if block.startswith('[submodule "brain"]') else 1, repo))
    return list(dict.fromkeys(repo for _, repo in sorted(found, key=lambda f: f[0])))


def gh_input(args, input_text: str) -> subprocess.CompletedProcess:
    """gh with its input on stdin: a private key never goes on a command line, where any process of
    this computer could read it."""
    argv = ["gh", *[str(a) for a in args]]
    try:
        return subprocess.run(argv, input=input_text, capture_output=True, text=True, check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def default_gh(dry_run=False):
    """The door the mount keys use: `gh <args>` through `run_cmd`, and through `gh_input` when it has
    input. Tests pass their own."""
    def gh(args, input=None):
        if dry_run:
            return run_cmd(["gh", *args], True)
        return run_cmd(["gh", *args]) if input is None else gh_input(args, input)
    return gh


def make_keypair(title: str):
    """(private key, public key) of a new ed25519 keypair, made by ssh-keygen in a private folder of
    the machine's temporary folder that is removed at once; None when ssh-keygen is not here. The public
    key comes back without its comment (`PUBLIC_KEY`)."""
    if not shutil.which("ssh-keygen"):
        return None
    folder = tempfile.mkdtemp(prefix="spaces-key-")
    try:
        key = Path(folder) / "key"
        res = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", title, "-f", str(key)],
                             capture_output=True, text=True, check=False, timeout=60, stdin=subprocess.DEVNULL)
        if res.returncode != 0 or not key.is_file():
            return None
        public = " ".join(key.with_suffix(".pub").read_text(encoding="utf-8").split()[:2])
        return key.read_text(encoding="utf-8"), public
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def key_fingerprint(public: str) -> str:
    """The key's SHA256 fingerprint, as GitHub and ssh-keygen show it."""
    import base64
    import hashlib
    blob = base64.b64decode(public.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def key_bundle(private: dict, pending: dict | None = None) -> str:
    """The secret MOUNT_DEPLOY_KEYS: per mounted repository a `mount <owner>/<name>` line, then its
    private key (the format checks.yml reads). A key the brain's agent has still to attach gets a
    `pending <owner>/<name> <yyyy-mm-dd>` line first: until that day the checks warn, not fail, when the
    brain is unread only through it, so a colleague's first pull requests land while the key waits."""
    pending = pending or {}
    return "".join((f"pending {repo} {pending[repo]}\n" if repo in pending else "") + f"mount {repo}\n{key.strip()}\n"
                   for repo, key in private.items())


def _gh_ok(gh, *args) -> tuple:
    res = gh(list(args))
    return res.returncode == 0, (res.stdout or "").strip()


def is_admin(gh, repo: str) -> bool:
    """Whether the signed-in account administers `repo` (a read)."""
    ok, out = _gh_ok(gh, "api", f"repos/{repo}", "-q", ".permissions.admin")
    return ok and out == "true"


def writers_can_read(gh, consumer: str, mount: str, own_login: str | None = None) -> bool:
    """Whether everyone who can push to `consumer` can already read `mount`: only then may a key that
    reads `mount` sit in `consumer`'s secrets. A public `mount` is readable by anyone. A list that
    cannot be read is a no (fail closed). `own_login` is a person whose read of `mount` is already known."""
    ok, private = _gh_ok(gh, "api", f"repos/{mount}", "-q", ".private")
    if ok and private == "false":
        return True
    ok, out = _gh_ok(gh, "api", f"repos/{consumer}/collaborators?affiliation=all&permission=push&per_page=100",
                     "--paginate", "-q", ".[].login")
    if not ok:
        return False
    for login in dict.fromkeys(l.strip() for l in out.splitlines() if l.strip()):
        if not LOGIN.fullmatch(login):
            return False
        if own_login and login.lower() == own_login.lower():
            continue
        ok, level = _gh_ok(gh, "api", f"repos/{mount}/collaborators/{login}/permission", "-q", ".permission")
        if not ok or level not in READ_LEVELS:
            return False
    return True


def attach_key(gh, mount: str, consumer: str, public: str) -> bool:
    """Put `public` on `mount` as the read-only deploy key of `consumer`, in place of any key of that
    title (a re-key), and read it back: True only when GitHub lists it read-only."""
    title = mount_key_title(consumer)
    ok, out = _gh_ok(gh, "api", f"repos/{mount}/keys", "--paginate", "-q", '.[] | "\\(.id) \\(.title)"')
    if not ok:
        return False
    for line in out.splitlines():
        kid, _, ktitle = line.partition(" ")
        if ktitle.strip().lower() == title and kid.isdigit():
            gh(["api", "-X", "DELETE", f"repos/{mount}/keys/{kid}"])
    res = gh(["api", "-X", "POST", f"repos/{mount}/keys", "-f", f"title={title}", "-f", f"key={public}",
              "-F", "read_only=true", "-q", ".read_only"])
    return res.returncode == 0 and (res.stdout or "").strip() == "true"


def remove_keys(gh, mount: str, consumer: str) -> tuple:
    """Remove every deploy key `consumer` holds on `mount` (offboarding, a mount let go); (removed,
    read back): read back is True once no key of that title is listed."""
    ok, ids = key_ids(gh, mount, consumer)
    if not ok:
        return 0, False
    removed = sum(gh(["api", "-X", "DELETE", f"repos/{mount}/keys/{kid}"]).returncode == 0
                  for kid in ids if kid.isdigit())
    ok, left = key_ids(gh, mount, consumer)
    return removed, ok and not left


def key_ids(gh, mount: str, consumer: str) -> tuple:
    """(listed, ids): the ids of the deploy keys on `mount` titled for `consumer` (`mount_key_title`)."""
    title = mount_key_title(consumer)
    ok, out = _gh_ok(gh, "api", f"repos/{mount}/keys", "--paginate", "-q", '.[] | "\\(.id) \\(.title)"')
    return ok, [l.partition(" ")[0] for l in out.splitlines() if l.partition(" ")[2].strip().lower() == title]


def request_key(root, mount: str, consumer: str, public: str) -> str | None:
    """Ask the brain's agent to attach `public` to `mount` for `consumer`, by a registry note from this
    workspace about its own person (`classify.note`, kind `key`): the brain checks the note against its
    registry before it attaches anything (`queue_mount_key`, `attach_mount_keys`). None when the note
    went, else why it could not."""
    try:
        import classify
        space = spaceyaml.load(root)
        # about this workspace's own person, by registry id, as its `workspace` note is (`classify.announce`)
        sid = classify.person_id(registry.Registry(root, space), space.owner)
        classify.note(root, sid, "key", {"consumer": consumer, "mount": mount, "key": public})
    except Exception as exc:   # a brain whose tools take no key note yet, a person it does not hold
        return str(exc)[:160] or exc.__class__.__name__
    return None


def _days_since(text, today) -> int:
    """Whole days from an ISO date to `today`; a date that cannot be read is long ago."""
    try:
        return (today - dt.date.fromisoformat(str(text)[:10])).days
    except ValueError:
        return 9999


def brain_answers(root) -> dict:
    """What the brain's agent did with the keys this space asked it for: its record (`ATTACHED_KEYS`),
    read through this space's brain mount, by `<consumer> <mount>` in lower case; {} when there is none.
    Only an outcome whose fingerprint is the key this space sent counts (`mount_access`)."""
    try:
        broot = spaceyaml.load(root).mount_root("brain")
        data = read_yaml(Path(broot) / ATTACHED_KEYS, {}) if broot is not None else {}
    except Exception:   # no brain mount, an unreadable record: nothing is known yet
        return {}
    keys = data.get("keys") if isinstance(data, dict) else None
    return {str(k).lower(): v for k, v in keys.items() if isinstance(v, dict)} if isinstance(keys, dict) else {}


def _lasting(why: str) -> bool:
    """A mount left out for a reason a retry today would not change: then it is tried again weekly."""
    return str(why).startswith(LASTING)


def mount_access(report, repo: str, root, org=None, dry_run=False, gh=None, keygen=None, today=None) -> None:
    """The checks' read access to every mount of one repository, made by the agent: one read-only deploy
    key per mounted repository, its private half in the repository's secret MOUNT_DEPLOY_KEYS. A mount
    this account administers gets its key at once (`attach_key`), only when everyone who can push here
    can already read it (`writers_can_read`); any other (the company's brain, seen from a colleague's
    own account) is asked of the brain's agent by a registry note (`request_key`), which checks the same
    before it attaches it and records what it did (`brain_answers`). Nothing changes while every mount is
    settled (keyed and attached, asked less than PENDING_DAYS ago, or left out for a lasting reason this
    week, in `_meta/state/mount-keys.yaml`, with the secret present); anything else makes every key again,
    since a secret cannot be read back to add one. So a key the brain refused or never attached, and a
    mount left out for a passing reason (the brain could not be asked, GitHub did not take it), are tried
    again on the next run (`keys_upkeep`, daily). Where the brain is unread by any key, the fine-grained
    token is the fallback (`mount_token`): the person hears of it only when neither is there."""
    root = Path(root)
    mounts = gitmodules_repos(root)
    consumer = plain_repo(repo)
    if not mounts or not consumer:
        mount_token(report, repo, org, dry_run, brain_of_gitmodules(root))
        return
    gh = gh or default_gh(dry_run)
    keygen = keygen or make_keypair
    today = today or dt.datetime.now(dt.timezone.utc).date()
    check = f"gh secret list --repo {consumer}"
    if dry_run:
        report.add(f"mount keys {repo}", True, check, f"would make a read-only deploy key for {', '.join(mounts)}")
        return
    state = read_yaml(root / MOUNT_KEYS_STATE, {}) or {}
    ours = isinstance(state, dict) and str(state.get("repo") or "").lower() == consumer.lower()
    keyed = dict(state.get("keys")) if ours and isinstance(state.get("keys"), dict) else {}
    was_left = dict(state.get("left")) if ours and isinstance(state.get("left"), dict) else {}
    answers, changed, unanswered = brain_answers(root), False, {}
    for mount, rec in list(keyed.items()):
        rec = rec if isinstance(rec, dict) else {}
        if rec.get("attached"):
            continue
        said = answers.get(f"{consumer} {mount}".lower()) or {}
        said = said if said.get("fingerprint") == rec.get("fingerprint") else {}
        if said.get("outcome") == "attached":
            keyed[mount], changed = {**rec, "attached": True}, True
        elif said.get("outcome") == "refused" or _days_since(rec.get("asked"), today) >= PENDING_DAYS:
            keyed.pop(mount)
            was_left[mount] = (f"{KEY_REFUSED}{pages._shown(said.get('why'))[:160]}" if said
                               else f"the brain's agent did not attach it in {PENDING_DAYS} days")
            if not said:
                unanswered[mount] = int(rec.get("asks") or 1)
            changed = True
    ok, out = _gh_ok(gh, "secret", "list", "--repo", consumer)
    present = ok and DEPLOY_KEYS_SECRET in {line.split()[0] for line in out.splitlines() if line.split()}
    week = _days_since(state.get("at"), today) < PENDING_DAYS if ours else False
    if ours and present and all(m in keyed or (m in was_left and _lasting(was_left[m]) and week) for m in mounts):
        if changed:
            write_yaml(root / MOUNT_KEYS_STATE, {**state, "keys": keyed, **({"left": was_left} if was_left else {})})
        _mount_keys_row(report, repo, check, list(keyed), [m for m in keyed if not (keyed[m] or {}).get("attached")],
                        {m: was_left[m] for m in mounts if m in was_left})
        if mounts[0] in was_left or int((keyed.get(mounts[0]) or {}).get("asks") or 1) > 1:
            mount_token(report, repo, org, dry_run, mounts[0])
        else:
            organization_token(report, repo, org, dry_run)
        return
    if not is_admin(gh, consumer):
        report.add(f"mount keys {repo}", True, check, f"no deploy key: this account cannot set {consumer}'s secrets")
        mount_token(report, repo, org, dry_run, mounts[0])
        return
    private, record, left, pending = {}, {}, {}, {}
    for mount in mounts:
        own = is_admin(gh, mount)
        if own and not writers_can_read(gh, consumer, mount):
            left[mount] = f"someone who can push to {consumer} cannot read it"
            continue
        pair = keygen(mount_key_title(consumer))
        if pair is None or not PUBLIC_KEY.fullmatch(str(pair[1])):
            left[mount] = "no ed25519 key could be made here (ssh-keygen)"
            continue
        secret, public = pair
        if own:
            if not attach_key(gh, mount, consumer, public):
                left[mount] = "GitHub did not take the deploy key"
                continue
        else:
            refused = request_key(root, mount, consumer, public)
            if refused:
                left[mount] = f"the brain's agent could not be asked ({refused})"
                continue
            pending[mount] = (today + dt.timedelta(days=PENDING_GRACE)).isoformat()
        private[mount] = secret
        record[mount] = {"fingerprint": key_fingerprint(public), "attached": own,
                         **({} if own else {"asked": today.isoformat(), "asks": unanswered.get(mount, 0) + 1})}
    if private and gh(["secret", "set", DEPLOY_KEYS_SECRET, "--repo", consumer],
                      input=key_bundle(private, pending)).returncode != 0:
        left.update({m: f"GitHub did not store the secret {DEPLOY_KEYS_SECRET}" for m in private})
        private, record = {}, {}
    if private:
        write_yaml(root / MOUNT_KEYS_STATE, {"repo": consumer, "secret": DEPLOY_KEYS_SECRET, "at": today.isoformat(),
                                             "keys": record, **({"left": left} if left else {})})
    _mount_keys_row(report, repo, check, list(private), [m for m, r in record.items() if not r["attached"]], left)
    # The brain unread by a key, or asked of the brain's agent more than once with no answer: the token is the
    # fallback, and only its absence is the person's.
    if mounts[0] in left or int((record.get(mounts[0]) or {}).get("asks") or 1) > 1:
        mount_token(report, repo, org, dry_run, mounts[0])
    else:
        organization_token(report, repo, org, dry_run)


def _mount_keys_row(report, repo, check, keyed, waiting, left) -> None:
    """One row for the mount keys: which mounts are keyed, which wait for the brain's agent, which were
    left out and why. Done: what is left out is a mount the checks skip with a warning, and the brain's
    own fallback is the token row."""
    report.add(f"mount keys {repo}", True, check,
               (f"a read-only deploy key for {', '.join(keyed)}" if keyed else "no deploy key")
               + (f"; the brain's agent attaches the one for {', '.join(waiting)}" if waiting else "")
               + "".join(f"; {m} left out: {why}" for m, why in left.items()))


def space_repo(root) -> str | None:
    """owner/name of this space's own repository: what onboarding recorded, else its origin remote."""
    state = read_yaml(Path(root) / "_meta" / "state" / "onboard.yaml", {}) or {}
    return plain_repo((state.get("repo") if isinstance(state, dict) else None) or git_remote(root))


def key_mounts(root, reg, person) -> set:
    """The repositories a colleague's workspace may hold a key for, in lower case: the brain itself, and
    each project the brain's registry has them on the crew of (its `match.repo`, else the space the
    brain's onboarding made for it). Nothing else, whatever else they can read. The crew is read as landing
    and the gate read it (`classify._crew_of`): the subject's, its umbrellas', and its walled classification's."""
    import classify
    state = read_yaml(Path(root) / "_meta" / "state" / "onboard.yaml", {}) or {}
    spaces = state.get("spaces") if isinstance(state, dict) and isinstance(state.get("spaces"), dict) else {}
    found = {space_repo(root)}
    for subject in reg.subjects.values():
        if subject.kind == "people" or subject.state == "archived" or person.id not in classify._crew_of(reg, subject.id):
            continue
        repo = plain_repo((subject.match or {}).get("repo"))
        if not repo and subject.home in spaces:
            repo = plain_repo(git_remote(Path(str(spaces[subject.home]))))
        found.add(repo)
    return {f.lower() for f in found if f}


def queue_mount_key(root, signal: dict, reg=None) -> tuple:
    """In the brain: keep a `key` registry note's request for `attach_mount_keys`, after the checks that
    need no host: the person who sent it is registered and the note came from their registered workspace
    (or they have none yet), the consuming repository is their `workspace_repo` when one is registered
    (else, once their login is known, in their own account), the mounted repository is a plain name and
    the key an ed25519 public key. `reg` is the registry the caller holds, so a note that comes in the same
    batch as the workspace's own `workspace` note sees it. ("applied", "") or ("refused", why). Only a note
    the brain's main branch holds as it is counts (`merged_note`): one written into `_inbound/signals/` here by
    hand never queues a key. The request goes to KEY_REQUESTS, which is never staged. Nothing is
    attached here: what is not settled yet (no workspace, login or workspace repository registered) waits
    there for `attach_mount_keys`, which checks everything again."""
    root = Path(root)
    data = signal.get("data") if isinstance(signal.get("data"), dict) else {}
    by, sender = str(signal.get("by") or ""), str(signal.get("from") or "")
    reg = reg if reg is not None else registry.Registry(root, spaceyaml.load(root))
    person = next((s for s in reg.subjects.values() if s.kind == "people" and s.state != "archived"
                   and str(s.title).strip().lower() == by.strip().lower()), None)
    had = str(person.extra.get("workspace") or "") if person is not None else ""
    someone_else = any(s is not person and s.kind == "people" and str(s.extra.get("workspace") or "") == sender
                       for s in reg.subjects.values())
    if person is None or not pages.is_slug(sender) or (had and had != sender) or someone_else:
        return "refused", f"{by or 'the sender'} has no registered workspace {sender or ''}".strip()
    consumer, mount = plain_repo(data.get("consumer")), plain_repo(data.get("mount"))
    public = str(data.get("key") or "").strip()
    if not consumer or not mount or not PUBLIC_KEY.fullmatch(public):
        return "refused", "a key note names its repository, the mount and an ed25519 public key"
    pinned = plain_repo(person.extra.get("workspace_repo"))
    login = str(person.match.get("github") or "")
    if pinned and pinned.lower() != consumer.lower():
        return "refused", f"{consumer} is not {person.title}'s workspace repository"
    if not merged_note(root, signal):
        return "refused", "a key note counts only once it reached the brain's main branch through a pull request"
    name = f"{consumer.replace('/', '--')}--{mount.replace('/', '--')}".lower()
    write_yaml(root / KEY_REQUESTS / f"{name}.yaml",
               {"person": person.id, "from": sender, "consumer": consumer, "mount": mount, "key": public,
                "fingerprint": key_fingerprint(public), "at": signal.get("at")})
    return "applied", ""


def merged_note(root, signal: dict) -> bool:
    """Whether `signal` is the note the brain's main branch on GitHub holds at `_inbound/signals/<id>.yaml`,
    unchanged: a note from another space reaches the brain only by a pull request, whose `from` the gate bound to
    the login that pushed it. A note written into `_inbound/signals/` on this computer (a routine a mail talked
    into it) is not on origin/main, and the sync's pull request that would carry it is held, so it never queues
    a key. Read with git directly, as bytes, so a name outside ASCII compares the same on every computer."""
    sid = str(signal.get("id") or "")
    if not pages.is_page_id(sid):
        return False
    try:
        res = subprocess.run(["git", "-C", str(root), "show", f"origin/main:_inbound/signals/{sid}.yaml"],
                             capture_output=True, check=False, timeout=60)
        return res.returncode == 0 and yaml.safe_load(res.stdout.decode("utf-8")) == signal
    except (OSError, subprocess.TimeoutExpired, UnicodeError, yaml.YAMLError):
        return False


def _tracked(root, folder) -> set | None:
    """The names git tracks in `folder`, or None when git cannot say. A key request git tracks came in a
    pull request, not from a note on this computer: it is never attached."""
    res = run_cmd(["git", "ls-files", "-z", "--", Path(folder).relative_to(root).as_posix()], cwd=root)
    if res.returncode != 0:
        return None
    return {Path(p).name for p in (res.stdout or "").split("\0") if p}


def _key_entries(path) -> dict:
    try:
        data = read_yaml(path, {}) or {}
    except (OSError, yaml.YAMLError):
        return {}
    keys = data.get("keys") if isinstance(data, dict) else None
    return {str(k): v for k, v in keys.items() if isinstance(v, dict)} if isinstance(keys, dict) else {}


def key_record_path(root) -> Path:
    """The brain's record of attached keys: `RECORD_GIT_PATH` in its git dir, which no merge or pull request
    writes; `KEY_RECORD` only where git names no git dir (then nothing merges into the work tree either)."""
    root = Path(root)
    try:
        res = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-path", RECORD_GIT_PATH],
                             capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return root / KEY_RECORD
    found = res.stdout.strip() if res.returncode == 0 else ""
    if not found:
        return root / KEY_RECORD
    return Path(found) if Path(found).is_absolute() else root / found


def attached_keys(root, taken=None) -> dict:
    """The brain's record of every key request its agent settled: by `<consumer> <mount>`, the person, the
    fingerprint and the outcome (attached, refused or removed). The record is in the git dir (`key_record_path`),
    so no pull request, merged or not, hides a key from its removal; the work-tree record of before
    (`KEY_RECORD`) is read only while the git dir holds none and git does not track it. A key the published
    answers (`ATTACHED_KEYS`) call attached and the record does not is added, so a record lost with a computer
    still finds the keys. Anyone can edit the answers in a pull request and an entry here can lead to a removal,
    so one is taken only under its own `<consumer> <mount>` key, where the record holds nothing, and for a
    registered person whose consumer it is (`_consumer_of`): it can then only remove that person's own key.
    `taken`, a set, receives the keys that came from the answers only."""
    root = Path(root)
    path, legacy = key_record_path(root), root / KEY_RECORD
    record = _key_entries(path)
    if not path.is_file() and path != legacy and legacy.is_file():
        tracked = _tracked(root, legacy.parent)
        if tracked is not None and KEY_RECORD.name not in tracked:
            record = _key_entries(legacy)
    answers = _key_entries(root / ATTACHED_KEYS)
    try:
        subjects = registry.Registry(root, spaceyaml.load(root)).subjects if answers else {}
    except Exception:   # no registry to check an answer against: take none
        subjects = {}
    for key, entry in answers.items():
        consumer, mount = plain_repo(entry.get("consumer")), plain_repo(entry.get("mount"))
        person = subjects.get(str(entry.get("person") or ""))
        if entry.get("outcome") == "attached" and consumer and mount and key == f"{consumer} {mount}".lower()                 and key not in record and person is not None and person.kind == "people"                 and _consumer_of(person, consumer):
            record[key] = entry
            if taken is not None:
                taken.add(key)
    return record


def _consumer_of(person, consumer: str) -> bool:
    """`consumer` is this person's repository as attaching requires: their `workspace_repo`, or, when none is
    registered, a repository in their own GitHub account."""
    pinned = plain_repo(person.extra.get("workspace_repo"))
    login = str(person.match.get("github") or "")
    if pinned:
        return pinned.lower() == consumer.lower()
    return bool(LOGIN.fullmatch(login)) and consumer.split("/")[0].lower() == login.lower()


def attached_mounts(root, person_id: str) -> list:
    """The mounted repositories the brain's record says hold a key attached for this person's workspace:
    offboarding removes their keys there too, not only on the repositories they read."""
    found = [plain_repo(v.get("mount")) for v in attached_keys(root).values()
             if v.get("outcome") == "attached" and str(v.get("person") or "") == str(person_id)]
    return list(dict.fromkeys(f for f in found if f))


def attach_mount_keys(brain_root, gh=None) -> Report:
    """In the brain, by its owner's agent: attach each queued key request (`queue_mount_key`) once the
    registry and the host agree it reads nothing new. A request git tracks is refused (it came in a pull
    request). The registry: the person is registered, not archived, the request came from their registered
    workspace, the consuming repository is their `workspace_repo` (or in their own account when none is
    registered), and the mount is the brain or a project they are crew on (`key_mounts`); what the
    registry does not hold yet waits. The host: this account administers the mount, their login reads it,
    and everyone who can push to the consuming repository reads it too (`writers_can_read`; only the brain,
    for a workspace in their own account, is theirs to share as a clone would be). Every outcome goes to
    the record (`KEY_RECORD`, never staged), which offboarding reads for their keys, and to the answers
    (`ATTACHED_KEYS`), which their workspace reads through its brain mount. Each run also removes a recorded key whose person is archived, is off that crew
    or no longer reads the mount. `python onboard.py <brain> --mount-keys`, in the daily chain."""
    root = Path(brain_root).resolve()
    gh = gh or default_gh()
    report = Report(case="mount-keys")
    reg = registry.Registry(root, spaceyaml.load(root))
    from_answers = set()
    record = attached_keys(root, from_answers)
    before = json.dumps(record, sort_keys=True, default=str)
    unconfirmed = {}   # answers GitHub could not be asked about: kept in the answers only, asked again next run
    for key in from_answers:   # anyone can edit the answers: one is kept only for a key the brain sees on a mount it administers
        mount, consumer = plain_repo(record[key].get("mount")), plain_repo(record[key].get("consumer"))
        res = gh(["api", f"repos/{mount}", "-q", ".permissions.admin"])
        ok, admin = res.returncode == 0, (res.stdout or "").strip()
        # a 404 is an answer: this account cannot see the mount, so it holds no key there to keep or remove
        unseen = not ok and bool(re.search(r"HTTP 404|Not Found", res.stderr or "", re.I))
        listed, ids = key_ids(gh, mount, consumer) if ok and admin == "true" else (ok or unseen, [])
        entry = record.pop(key)
        if ids:
            record[key] = entry
        elif not listed:   # a failed call is no answer: dropping it here would lose a real key's removal for good
            unconfirmed[key] = entry
            report.add(f"mount key {consumer} on {mount}", False, f"gh api repos/{mount}/keys -q '.[].title'",
                       f"could not confirm mount key {consumer} on {mount}: tried again next run")
    own_brain = space_repo(root)
    folder = root / KEY_REQUESTS
    paths = sorted(folder.glob("*.yaml")) if folder.is_dir() else []
    tracked = _tracked(root, folder) if paths else set()
    for path in paths:
        try:
            request = read_yaml(path, {}) or {}
        except (OSError, yaml.YAMLError):
            request = {}
        request = request if isinstance(request, dict) else {}
        consumer, mount = plain_repo(request.get("consumer")), plain_repo(request.get("mount"))
        public = str(request.get("key") or "")
        person = reg.subjects.get(str(request.get("person") or ""))
        name = f"mount key {consumer or '?'} on {mount or '?'}"
        check = f"gh api repos/{mount}/keys -q '.[].title'" if mount else ""

        def settle(outcome, why):
            if consumer and mount and person is not None:
                record[f"{consumer} {mount}".lower()] = {
                    "person": person.id, "consumer": consumer, "mount": mount, "outcome": outcome,
                    "fingerprint": key_fingerprint(public) if PUBLIC_KEY.fullmatch(public) else "",
                    **({"why": why} if why else {}), "at": dt.datetime.now(dt.timezone.utc).date().isoformat()}
            report.add(name, True, check, why and f"refused: {why}" or "attached")
            path.unlink()
        if tracked is None:
            report.add(name, False, "git ls-files _meta/state/private", "git cannot say whether this request came "
                       "from a note on this computer: tried again next run")
            continue
        if path.name in tracked:
            settle("refused", "a request git tracks came in a pull request, not from a note")
            continue
        if not consumer or not mount or not PUBLIC_KEY.fullmatch(public) or person is None \
                or person.kind != "people" or person.state == "archived":
            settle("refused", "the request names no registered person, or no plain repository and key")
            continue
        workspace = str(person.extra.get("workspace") or "")
        login = str(person.match.get("github") or "")
        pinned = plain_repo(person.extra.get("workspace_repo"))
        own_account = LOGIN.fullmatch(login) and consumer.split("/")[0].lower() == login.lower()
        if workspace and workspace != str(request.get("from") or ""):
            settle("refused", f"it did not come from {person.title}'s registered workspace")
            continue
        if pinned and pinned.lower() != consumer.lower():
            settle("refused", f"{consumer} is not {person.title}'s workspace repository")
            continue
        if not workspace or not LOGIN.fullmatch(login) or not (pinned or own_account):
            report.add(name, True, check, f"waits until the registry gives {person.title} their workspace, GitHub "
                       "login and workspace repository")
            continue
        if mount.lower() not in key_mounts(root, reg, person):
            settle("refused", f"{mount} is neither the brain nor a project {person.title} is crew on")
            continue
        if not is_admin(gh, mount):
            # only the brain attaches keys, and without admin it cannot read back a key added by hand: admin it is
            report.add(name, False, check, f"this account does not administer {mount}: ask whoever administers it "
                       f"(the {mount.split('/')[0]} account) to give you admin on {mount}; the next daily run then "
                       f"attaches {person.title}'s read-only key", needs_owner=True)
            continue
        ok, level = _gh_ok(gh, "api", f"repos/{mount}/collaborators/{login}/permission", "-q", ".permission")
        if not ok or level not in READ_LEVELS:
            settle("refused", f"{person.title} ({login}) cannot read {mount}")
            continue
        theirs = own_account and own_brain and mount.lower() == own_brain.lower()
        if not theirs and not writers_can_read(gh, consumer, mount, own_login=login):
            settle("refused", f"someone who can push to {consumer} cannot read {mount}")
            continue
        if attach_key(gh, mount, consumer, public):
            settle("attached", "")
        else:
            report.add(name, False, check, "GitHub did not take it: tried again next run")
    for key, entry in list(record.items()):
        person = reg.subjects.get(str(entry.get("person") or ""))
        consumer, mount = plain_repo(entry.get("consumer")), plain_repo(entry.get("mount"))
        if entry.get("outcome") != "attached" or not consumer or not mount:
            continue
        login = str(person.match.get("github") or "") if person is not None else ""
        why = ""
        if person is None or person.state == "archived":
            why = "their person is archived"
        elif mount.lower() not in key_mounts(root, reg, person):
            why = f"{person.title} is no longer on its crew"
        elif LOGIN.fullmatch(login):
            ok, level = _gh_ok(gh, "api", f"repos/{mount}/collaborators/{login}/permission", "-q", ".permission")
            why = f"{person.title} ({login}) no longer reads it" if ok and level not in READ_LEVELS else ""
        if not why:
            continue
        removed, clear = remove_keys(gh, mount, consumer)
        if not clear and not is_admin(gh, mount):
            report.add(f"mount key {consumer} on {mount}", False, f"gh api repos/{mount}/keys -q '.[].title'",
                       f"{why}, and this account does not administer {mount}: ask whoever administers it (the "
                       f"{mount.split('/')[0]} account) to remove the deploy key titled '{mount_key_title(consumer)}', "
                       f"or to give you admin on {mount} so the next daily run removes it", needs_owner=True)
            continue
        report.add(f"mount key {consumer} on {mount}", clear, f"gh api repos/{mount}/keys -q '.[].title'",
                   f"removed: {why}" if clear else f"{why}, and the key could not be removed: tried again next run")
        if clear:
            record[key] = {**entry, "outcome": "removed", "why": why}
    for target, keys in ((key_record_path(root), record), (root / ATTACHED_KEYS, {**record, **unconfirmed})):
        after = json.dumps(keys, sort_keys=True, default=str)   # the record removals read, the answers workspaces read
        if after != before or json.dumps(_key_entries(target), sort_keys=True, default=str) != after:
            write_yaml(target, {"keys": keys})
    private_tracked(report, root)
    return report


def private_tracked(report, root) -> None:
    """A file git tracks under `_meta/state/private/` came in a pull request (the sync never stages there): a
    security notice to the person through owner_email, once per set of files, and a failing row. Nothing reads it:
    the key record is in the git dir, and a tracked key request is refused."""
    names = _tracked(root, Path(root) / PRIVATE_STATE)
    if not names:
        return
    report.add("private files tracked", False, f"git ls-files {PRIVATE_STATE.as_posix()}",
               f"git tracks {len(names)} file(s) in the private folder, so a merged pull request put them there: "
               "they are not read; remove them from the repository", needs_owner=True)
    import hashlib
    import owner_email
    notice = {"kind": "security", "sender": "sync", "title": "A merged change added files that should stay private",
              "summary": "A change merged into the shared repository put files in the folder that stays on this "
                         "computer only. They were not read, and the list of colleague access stays as it was.",
              "steps": ["Find the change that added them and ask its author why.",
                        "Remove those files from the shared repository in a new change."],
              "reassure": "Nothing was read from them and no access was given or removed because of them.",
              "key": "private-tracked-" + hashlib.sha256("\n".join(sorted(names)).encode()).hexdigest()[:16]}
    try:
        owner_email.queue(root, notice)
    except (Exception, SystemExit):   # the failing row above still names it
        pass


def keys_upkeep(root, gh=None, keygen=None, today=None) -> Report:
    """The daily upkeep of the checks' mount access, run by any space's agent with no person: this space's
    own mount keys (`mount_access`, which changes nothing while they are settled), in the brain every key
    request a colleague's workspace sent by note (`attach_mount_keys`), and the crew projects setup left
    out (`crew_left_steps`). What the agent fixes goes to its health rows (`needs_you.set_health`, writer
    `mount-keys`), which the daily retries; what only the person can do (a needs_owner row) is a Needs-you
    item of theirs (`MOUNT_KEY_ITEMS`), an organization secret every colleague reads also a security notice
    (owner_email). In the brain, an owner's row with no GitHub login gets it (`owner_login_step`): the gate ties
    the owner's upkeep to it. `python onboard.py <space> --mount-keys`, in every space's daily chain."""
    import needs_you
    root = Path(root).resolve()
    space = spaceyaml.load(root)
    report = Report(case="mount-keys")
    if (root / ".gitmodules").exists():
        repo = space_repo(root)
        if repo:
            mount_access(report, repo, root, gh=gh, keygen=keygen, today=today)
        else:
            report.add("mount keys", False, "git remote get-url origin",
                       "this space's repository is not known: add the origin remote (`git remote add origin "
                       "<its address>`)", needs_owner=True)
    if space.profile == "brain":
        report.steps += attach_mount_keys(root, gh=gh).steps
        owner_login_step(report, root, space, gh=gh)
    import mint
    report.steps += crew_left_steps(space, mount=mint.mount_space)

    def row_id(s):
        return "mount-keys-" + (re.sub(r"[^a-z0-9]+", "-", s.name.lower()).strip("-") or "row")
    needs_you.set_health(root, "mount-keys", [
        {"id": row_id(s), "text": f"{s.name}: {s.result}",
         "agent": f"the daily chain tries it again (chain.py daily; check: {s.check})"} for s in report.failed()])
    made = make_signing_key(root)   # its own health writer: this computer's key, not the checks' mount keys
    signing = Step("signing key", False, signing_key_check(root), made) if made else signing_key_step(root)
    needs_you.set_health(root, "signing-key", [] if signing.done or signing.needs_owner else [
        {"id": "signing-key", "text": f"{signing.name}: {signing.result}",
         "agent": f"the daily chain checks it again (chain.py daily; check: {signing.check})"}])
    theirs = [s for s in report.steps if s.needs_owner and not s.done]
    # the key click is the person's: a question of theirs, whose full text keeps the key's end to compare. Its id
    # carries the key's end too: an answer stays with its own key, so a new key is a new question on Needs you
    def item_id(s):
        if s is not signing:
            return row_id(s)
        import sync
        key = Path(f"{sync.signing_key()}.pub").read_text(encoding="utf-8").split()[1]
        return row_id(s) + "-" + re.sub(r"[^a-z0-9]+", "-", key[-16:].lower()).strip("-")
    items = [{"id": item_id(s), "kind": "question", "for": space.owner, "text": s.name, "recommendation": s.result,
              "default": "it stays as it is; the daily chain checks it again", "depends": 0, "check": s.check}
             for s in theirs + ([signing] if signing.needs_owner and not signing.done else [])]
    path = root / MOUNT_KEY_ITEMS
    if items or path.exists():
        write_yaml(path, {"items": items})
    for s in theirs:
        if s.name.startswith("organization token "):
            org_secret_notice(root, s.name[len("organization token "):])
    return report


MOUNT_KEY_ITEMS = Path("_inbound") / "needs-you" / "mount-keys.yaml"


def owner_login_step(report, root, space, gh=None) -> None:
    """In the brain: the owner's own registry row needs `match.github`, which the gate ties the owner's
    signatures, upkeep and releases to. A brain set up before onboarding wrote it gets it here, as a tool write:
    the login the base branch's CODEOWNERS names (onboarding wrote it, and the gate already takes it as the
    owner's), else the signed-in account's. A `match.github` change waits for the owner's click on the sync's
    protected pull request, so the person only clicks."""
    import gate
    reg = registry.Registry(root, space)
    mine = [s for s in reg.subjects.values()
            if s.kind == "people" and " ".join(str(s.title).split()).casefold() == " ".join(str(space.owner).split()).casefold()]
    if not mine or any(LOGIN.fullmatch(str((s.match or {}).get("github") or "")) for s in mine):
        return
    named = gate._code_owner_logins(root, "origin/main")
    login = next(iter(named)) if len(named) == 1 else _gh_ok(gh or default_gh(), "api", "user", "-q", ".login")[1]
    if not LOGIN.fullmatch(login):
        report.add("owner login", False, "gh auth status", "your GitHub login is not known here: sign in with "
                   "`gh auth login`; the next daily run writes it to your registry row", needs_owner=True)
        return
    mine[0].match = {**(mine[0].match or {}), "github": login}
    reg.save()
    report.add("owner login", True, f"python _meta/tools/registry.py . find {mine[0].id}",
               f"@{login} written to your row `{mine[0].id}`: it lands with your one click on the sync's pull request")


def org_secret_notice(root, owner: str) -> None:
    """The organization-secret row as a security notice to the person, once per organisation."""
    import owner_email
    notice = {"kind": "security", "sender": "sync", "title": "A shared secret every colleague can read",
              "summary": f"The {owner} organisation holds a read token as an organisation secret. Every repository "
                         "there sees it, so every colleague who can push to one can read it.",
              "steps": ["Delete that organisation secret in the organisation's settings, under Secrets and variables.",
                        "Your agent then asks each repository for a read key of its own."],
              "reassure": "Nothing else changes: each repository keeps its own access.",
              "key": f"org-read-token-{owner.lower()}"}
    try:
        owner_email.queue(root, notice)
    except (Exception, SystemExit):   # the Needs-you item still names it
        pass


def platform_settings(report, root, repo, org, dry_run) -> None:
    if not repo:
        report.add("platform settings", False, "gh api repos/<org>/<repo> -q .allow_auto_merge",
                   "repository unknown: add the origin remote and run again", needs_owner=True)
        return
    res = run_cmd(["gh", "api", "-X", "PATCH", f"repos/{repo}", "-F", "allow_auto_merge=true",
                   "-F", "delete_branch_on_merge=true", "-F", "allow_squash_merge=false",
                   "-F", "allow_rebase_merge=false"], dry_run)
    report.add(f"merge settings {repo}", res.returncode == 0, f"gh api repos/{repo} -q .allow_auto_merge",
               res.stderr.strip()[:200] or "auto-merge on, merge commits only")
    # The checks bind admins too, so not even the person's own token (a cloud run acts as them) pushes to main past
    # them; while the first fill is still to be pushed to main, that waits for the push (`commit_fill`).
    held = not unconfigured_remote(root, dry_run)
    protection = {"required_status_checks": {"strict": False, "contexts": ["checks"]},
                  "enforce_admins": held, "required_pull_request_reviews": None, "restrictions": None}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(protection, tmp)
    res = run_cmd(["gh", "api", "-X", "PUT", f"repos/{repo}/branches/main/protection", "--input", tmp.name],
                  dry_run)
    os.unlink(tmp.name)
    report.add(f"branch protection {repo}", res.returncode == 0,
               f"gh api repos/{repo}/branches/main/protection -q .required_status_checks.contexts",
               res.stderr.strip()[:200] or ("checks required, your own account included" if held else
                                            "checks required; your own account too once the first fill is on main"))
    login = run_cmd(["gh", "api", "user", "-q", ".login"], dry_run).stdout.strip()
    owners = Path(root) / ".github" / "CODEOWNERS"
    if owners.exists() and login:
        text = owners.read_text(encoding="utf-8")
        if "@OWNER-LOGIN" in text:
            write_text(owners, text.replace("@OWNER-LOGIN", f"@{login}"))
        report.add("CODEOWNERS", "@OWNER-LOGIN" not in owners.read_text(encoding="utf-8"),
                   "grep -c OWNER-LOGIN .github/CODEOWNERS", f"@{login}")
    # The checks read the mounts with read-only deploy keys the agent makes, else a token. Anyone who
    # can push to a repository can read every Actions secret it sees (a branch that edits the workflow
    # runs it with the secrets), so each is a repository secret of this repository alone, reading only
    # what everyone who can push here already reads: never an organization secret, which would hand
    # every colleague one secret that reads every walled project (rules.md section 9).
    if (Path(root) / ".gitmodules").exists():
        mount_access(report, repo, root, org, dry_run)
    # No organization-level grant is needed: the checks workflow arms auto-merge with the
    # repository token, which the merge settings above allow (proved on the template's own
    # pull requests). The platform's own first pull request is the read-back check.


MEMBERS_READ = ("read", "write", "admin")   # the base permissions that let every member read all the organisation's repositories
MEMBERS_ITEMS = "_inbound/needs-you/onboard-org.yaml"   # its own file: onboard.yaml is rewritten by every run


def org_members_item(workspace, org: str) -> None:
    """The workspace is private, but it lives in the brain's account (decision A). When that account
    is an organisation whose base permission lets every member read all its repositories, they can
    read it: one item on the person's list gives the click path to "No permission". Agents change no
    organisation setting. A personal account has no such organisation (GitHub answers with an error)
    and a setting that cannot be read says nothing: neither raises an item. A later run withdraws the
    item once the permission reads `none`."""
    res = run_cmd(["gh", "api", f"orgs/{org}", "-q", ".default_repository_permission"]) if org else None
    permission = (res.stdout or "").strip() if res is not None and res.returncode == 0 else ""
    if permission in MEMBERS_READ:
        ask = (f"Everyone in {org} can read all of its repositories on GitHub, your workspace included. To keep it to "
               f"you and the people you choose: on GitHub open {org}, then Settings > Member privileges > "
               "Base permissions: No permission.")
        write_needs_you(workspace, [{"key": "org-members", "ask": ask, "default": "set it to No permission",
                                     "if_nothing": f"every member of {org} can read your workspace"}],
                        path=MEMBERS_ITEMS, prefix="onboard")
    elif permission == "none":
        write_needs_you(workspace, [], path=MEMBERS_ITEMS, prefix="onboard")


def _lazy(report, name, module, call, check, gate=True) -> None:
    """Run a sibling tool that another task may not have written yet."""
    try:
        mod = __import__(module)
    except ImportError:
        report.add(name, True, check, f"skipped: {module}.py not available yet")
        return
    try:
        result = call(mod)
    except Exception as exc:  # a sibling's defect must not hide the rest of the report
        report.add(name, False, check, f"{type(exc).__name__}: {exc}"[:200])
        return
    failed = getattr(result, "failed", 0) or 0
    counts = f"{getattr(result, 'passed', 0)} passed, {failed} failed" if hasattr(result, "passed") else "ran"
    report.add(name, not failed or not gate, check, counts if gate else f"{counts} (informative until pages are signed)")


def finish_space(report, dst) -> None:
    dst = Path(dst)
    _lazy(report, f"AGENTS.md {dst.name}", "agents_md", lambda m: m.write(dst), "python _meta/tools/agents_md.py --check")
    _lazy(report, f"ids {dst.name}", "ids", lambda m: m.build(dst), "test -f _meta/state/ids.yaml")
    if spaceyaml.load(dst).mode != "fixture":  # the fixture sweeps on its own clock
        _lazy(report, f"first sweep {dst.name}", "sweep", lambda m: m.run(dst),
              "test -f _meta/state/runs.jsonl")
    _lazy(report, f"status {dst.name}", "status", lambda m: m.run(dst), "test -f live/health.md")
    errs = [e for e in validate.validate(dst) if not e.startswith("warning:")]
    report.add(f"validate {dst.name}", not errs, f"python _meta/tools/validate.py {dst}",
               "; ".join(errs)[:300] or "clean")
    if (dst / "_meta" / "tests" / "answers.yaml").exists():
        _lazy(report, f"answers {dst.name}", "tests_run", lambda m: m.answers(dst),
              "python _meta/tools/tests_run.py . answers", gate=False)


def own_rows(report, dst, repo) -> list:
    """The steps about one space and no other: a project space's state file must never carry
    another client's name, so a row that mentions any other space is left out."""
    dst = Path(dst)
    mine = {dst.name} | ({str(repo)} if repo else set())
    others = {Path(p).name for p in report.spaces.values() if Path(p) != dst}
    others |= {k for k, p in report.spaces.items() if Path(p) != dst}
    others -= mine
    rows = []
    for step in report.steps:
        text = " ".join((step.name, step.check, step.result))
        if any(m in text for m in mine) and not any(o in text for o in others):
            rows.append(step.row())
    return rows


def write_state(dst, case, offline, ui, workspace, task, repo, report, full=True) -> None:
    """The onboarding root records the whole report; every other space only its own part. A probe task a
    build recorded and has not removed yet (`probe_task`) stays recorded, so the next build finds it."""
    dst = Path(dst)
    spaces = {k: str(v) for k, v in report.spaces.items() if full or Path(v) == dst}
    steps = [s.row() for s in report.steps] if full else own_rows(report, dst, repo)
    path = dst / "_meta" / "state" / "onboard.yaml"
    stray = (read_yaml(path, {}) or {}).get("probe_task") if path.exists() else None
    write_yaml(path,
               {"case": case, "at": pages.iso(spaceyaml.load(dst).now()), "offline": offline, "repo": repo,
                "workspace": str(workspace) if workspace else None, "user_instructions": str(ui),
                "sync_task": task, "spaces": spaces, "steps": steps, **({"probe_task": stray} if stray else {})})


def record_probe_task(root, record: dict) -> None:
    """The routines' probe task in onboard.yaml (`probe_task`), written before the task is registered: a build
    stopped before it removed the task leaves the record the next build, `--check` and offboard remove it by
    (`offboard.remove_probe_task`)."""
    path = Path(root) / "_meta" / "state" / "onboard.yaml"
    state = read_yaml(path, {}) or {}
    state["probe_task"] = dict(record)
    write_yaml(path, state)


def forget_probe_task(root) -> None:
    """The probe task is gone: its record goes too."""
    path = Path(root) / "_meta" / "state" / "onboard.yaml"
    state = read_yaml(path, {}) or {}
    if state.pop("probe_task", None) is not None:
        write_yaml(path, state)


# ----- detect, interview, run ------------------------------------------------------
def detect(root, brain=None, seed=None) -> str:
    root = Path(root)
    try:
        space = spaceyaml.load(root)
    except (FileNotFoundError, ValueError):
        space = None
    if space is not None and space.profile and not space.is_template():
        return "configured"
    if seed:
        return "project"
    if brain or (space is not None and space.mount_root("brain")) or (root / "brain" / "space.yaml").exists():
        return "person"
    return "company"


def questions() -> list:
    return list((read_yaml(TEMPLATES / "interview.yaml", {}) or {}).get("questions") or [])


def default_of(question, root=None):
    default = question.get("default")
    if default == "$never_leaves":
        return list(spaceyaml.DEFAULTS["never_leaves"])
    if default == "$user":
        return humanize(getpass.getuser())
    if default == "$folder":
        return humanize(Path(root).resolve().name) if root else ""
    if default == "$org":
        return (git_remote(root) or "/").split("/")[0] if root else ""
    return default


def joiner(q) -> str:
    """Categories are sentences that may hold commas: they are shown and split on `;`."""
    return "; " if q.get("kind") == "categories" else ", "


def interview(answers, interactive, case, root=None) -> dict:
    """Fill the questions for `case` from `answers`, then defaults; ask on stdin when interactive."""
    out, skipped = dict(answers or {}), []
    for q in questions():
        if case not in (q.get("applies_to") or []):
            continue
        key, default = q["key"], default_of(q, root)
        if out.get(key) not in (None, ""):
            continue
        if not interactive:
            out[key] = default
            skipped.append({**q, "default": default})
            continue
        shown = joiner(q).join(map(str, default)) if isinstance(default, list) else (str(default) or "none")
        try:
            raw = input(f"{q['ask']} [{shown}; `skip` to decide later] ").strip()
        except EOFError:  # stdin closed: every remaining default stands
            raw = ""
        if raw.lower() == "skip":
            skipped.append({**q, "default": default})
        out[key] = default if raw == "" or raw.lower() == "skip" else (
            parse_list(raw, sep=joiner(q).strip()) if q.get("kind") in ("list", "choice", "categories") else raw)
    out["_skipped"] = skipped
    return out


def resolve_brain(root, brain, offline):
    """(repo url or None, mount path) for a person's brain mount."""
    if brain and not re.match(r"^[a-z]+://|^git@", str(brain)):
        return None, Path(brain).resolve().as_posix()
    if brain:
        return str(brain), "brain"
    mount = next((m for m in spaceyaml.load(root).mounts if m.get("name") == "brain"), {})
    return mount.get("repo"), mount.get("path") or "brain"


def brain_account(brain_repo, brain_path=None) -> str | None:
    """The GitHub account that holds the brain: the owner in its https address, else, for a brain
    checked out on this machine, the owner of that checkout's origin remote or of the repository its
    onboarding recorded (`_meta/state/onboard.yaml`). None when neither says."""
    match = MOUNT_URL.fullmatch(str(brain_repo or ""))
    if match:
        return match.group(1)
    if not brain_path or not Path(str(brain_path)).is_absolute() or not Path(str(brain_path)).is_dir():
        return None
    recorded = read_yaml(Path(str(brain_path)) / "_meta" / "state" / "onboard.yaml", {}) or {}
    repo = git_remote(brain_path) or (recorded.get("repo") if isinstance(recorded, dict) else None)
    owner = str(repo or "").split("/")[0]
    return owner if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", owner) else None


CREW_LEFT = Path("_meta") / "state" / "crew-left-out.yaml"   # crew projects an invitation offered and setup left out


def registry_pin(subjects, name: str) -> str | None:
    """The repository the brain's own registry pins for the project space `name`: the `match.repo` of
    the subject that is that space itself (`id == home == name`, as mint writes it; the subject branch
    of `release.trust_repo`), never a person's workspace. An https GitHub address, else None."""
    for s in subjects or []:
        if s.id != name or s.home != name or s.kind == "people" or s.state == "archived":
            continue
        values = s.match.get("repo") if isinstance(s.match.get("repo"), list) else [s.match.get("repo")]
        found = next((url for url in map(registry.clone_url, values) if url), None)
        if found:
            return found
    return None


def brain_pins(brain_repo, brain_path=None, offline=False):
    """`registry_pin` over the brain's registry: the brain checked out on this machine (its mount, or a
    local brain), else, online, the registry on the brain's default branch read from GitHub, where a change
    to `home` or `match.repo` waits for the owner's click. A function of the project's name; it answers
    None for every name when the registry cannot be read."""
    subjects = None
    local = Path(str(brain_path)) if brain_path and Path(str(brain_path)).is_absolute() else None
    if local is not None and (local / "space.yaml").is_file():
        try:
            subjects = registry.Registry(local, spaceyaml.load(local)).all()
        except Exception:
            subjects = None
    elif not offline and plain_repo(brain_repo):
        res = run_cmd(["gh", "api", f"repos/{plain_repo(brain_repo)}/contents/{registry.REGISTRY_PATH.as_posix()}",
                       "-H", "Accept: application/vnd.github.raw"])
        try:
            data = yaml.safe_load(res.stdout) if res.returncode == 0 else None
        except yaml.YAMLError:
            data = None
        rows = data.get("subjects") if isinstance(data, dict) else None
        if isinstance(rows, list):
            subjects = [registry.Subject.from_dict(r) for r in rows if isinstance(r, dict) and r.get("id")]
    return lambda name: registry_pin(subjects, name)


def crew_mount(project: dict, root, own_name: str, brain_repo, offline: bool, brain_path=None,
               pinned=None) -> tuple:
    """One crew project an invitation offers, as a mount, or (None, why it is left out). The list sits
    in a file in the brain that someone other than the person may have written, so each entry is
    checked: its name must be an id that is neither `brain` nor this space's own name (the brain mount
    comes from the sentence alone, `space_values`); online its path is always `mounts/<name>`, whatever
    the entry says, and its repository an https GitHub address in the brain's own account
    (`brain_account`, from the brain's address or, for a brain checked out here, its checkout), or the
    one the brain's own registry pins for that project (`pinned`, from `brain_pins`: a client-hosted
    project), then mounted from the pinned address; anything else is left out with why, and the caller
    keeps it (`CREW_LEFT`) so the check mounts it once the registry pins it. Offline (the fixture, a copy
    beside the brain) a given path may point at a space outside this one or under its `mounts/`, never
    elsewhere inside it."""
    raw = project.get("name")
    pname = slugify(raw) if isinstance(raw, str) else ""
    if not pages.is_slug(pname) or pname in ("brain", own_name, "mounts"):
        return None, f"{pages._shown(raw)} is not a crew project's name"
    repo = project.get("repo")
    root = Path(root).resolve()
    if offline:
        given = project.get("path")
        if not given:
            return {"name": pname, "repo": repo, "path": f"mounts/{pname}", "branch": "main"}, ""
        if not isinstance(given, str) or "\n" in given or "\r" in given:
            return None, f"{pages._shown(given)} is not a path"
        target = (root / given).resolve() if not Path(given).is_absolute() else Path(given).resolve()
        inside = target == root or target.is_relative_to(root)
        if inside and not target.is_relative_to(root / "mounts"):
            return None, f"{pages._shown(given)} lies inside this space but not under mounts/"
        if not inside and not (target / "space.yaml").is_file():
            return None, f"{pages._shown(given)} holds no space"
        return {"name": pname, "repo": repo, "path": os.path.relpath(target, root).replace("\\", "/"),
                "branch": "main"}, ""
    path = f"mounts/{pname}"
    problem = mount_problem(repo, path)
    if problem:
        return None, problem
    account = brain_account(brain_repo, brain_path)
    owner = MOUNT_URL.fullmatch(repo).group(1)
    if account and owner.lower() == account.lower():
        return {"name": pname, "repo": repo, "path": path, "branch": "main"}, ""
    pin = pinned(pname) if pinned else None
    if pin and registry.same_repo(pin, repo):
        return {"name": pname, "repo": pin, "path": path, "branch": "main"}, ""
    if not account:
        return None, (f"{pages._shown(repo)} cannot be checked: the brain's account on GitHub is not known, "
                      "and the brain's registry does not pin it for this project")
    return None, (f"{pages._shown(repo)} is not in the brain's account {account}, and the brain's registry "
                  "does not pin it for this project")


def scan_codebases(report, root, now, offline) -> None:
    """The codebases the person can reach are linked to their subjects (link.py scan). Offline
    asks no host. Never an owner step: a session's first `resolve` places what a scan missed."""
    try:
        import link
        out = link.scan(root, now=now, list_fn=(lambda owner: None) if offline else None)
        result = (f"{out['linked']} linked, {out['asked']} asked" if not out.get("skipped")
                  else "skipped: offline" if offline else f"skipped: {out['skipped']}")
    except Exception as exc:
        result = f"skipped: {str(exc)[:200]}"
    report.add("codebases linked", True, "python _meta/tools/link.py . resolve --cwd <a codebase>", result)


def run(root, answers, offline=False, org=None, dry_run=False, home=None, user_instructions=None,
        brain=None, everyone=False, with_projects=False, progress=None, connected=()) -> Report:
    task_prefix()   # a name that is not ours stops the run here, before it creates a space it would then register
    root = Path(root).resolve()
    answers = dict(answers or {})
    skipped = answers.pop("_skipped", [])
    case = detect(root, brain=brain)
    if case == "configured":
        case = PROFILE_CASE.get(spaceyaml.load(root).profile, "person")
    report = Report(case=case, progress=progress)
    now = spaceyaml.load(root).now()
    org = org or answers.get("org") or (git_remote(root) or "/").split("/")[0] or None
    repo = None if offline else git_remote(root)
    tpl = template_repo(root)
    minutes = minutes_of(answers.get("schedule"))
    feeds = [f for f in parse_pairs(answers.get("feeds"), "name", "audience", "owner") if f["name"] in FEEDS]
    brain_repo = f"https://github.com/{repo}" if repo else None
    created, workspace = [], root
    if case == "company":
        company = answers.get("company") or humanize(root.name)
        name = slugify(company)
        people = people_of(answers)
        top = people[0] if people else {"name": "", "slug": "owner", "email": ""}
        names = {p["slug"]: p["name"] for p in people}
        # Personal workspaces may live under another owner on the platform (a person's own
        # account); `workspace_org` names it, the company's organization is the default.
        ws_org = answers.get("workspace_org") or org
        for person in (people if everyone else people[:1]):
            dst, prepo = create_space(report, root, person["slug"], f"{person['slug']}-workspace",
                                      offline, ws_org, home, tpl, dry_run)
            if dst:
                created.append(("personal", person, dst, prepo))
        # A listed project gets its own space when it says `space: true` (or for the fixture,
        # which builds everything); the others stay registry entries whose home is the brain.
        for project in [p for p in (answers.get("projects") or [])
                        if everyone or (with_projects and p.get("space"))]:
            pid = slugify(project["id"])
            dst, prepo = create_space(report, root, pid, pid, offline, org, home, tpl, dry_run)
            if dst:
                created.append(("project", project, dst, prepo))
        if dry_run:   # what would be created is said above; nothing is filled, registered or written
            report.add("dry run", True, "onboard.py without --dry-run", "nothing written")
            return report
        setup_space(report, root, "brain", space_values(answers, name, top["name"], top["email"],
                                                        "everyone", None), [])
        domains = parse_pairs(answers.get("domains"), "id", "owner", top["name"])
        for d in domains:
            d["owner"] = names.get(d["owner"], d["owner"])
        write_identity(root, company, top["name"], domains,
                       parse_list(answers.get("never_leaves"), spaceyaml.DEFAULTS["never_leaves"], ";"), now)
        skipped = list(skipped) + [{"key": "identity", "ask": (
            f"The identity of {company} is drafted (identity/charter.md and the spine): read it and "
            "sign it, or say what to change"), "default": "sign it when it reads right"}]
        settings = read_yaml(root / "space.yaml", {}) or {}
        if settings.get("classification_default") != classification_default(answers):
            settings["classification_default"] = classification_default(answers)
            write_yaml(root / "space.yaml", settings)
        fill_registry(report, root, answers, top["name"], name, now,
                      workspaces={i["slug"]: i["slug"] for k, i, _, _ in created if k == "personal"},
                      workspace_repos={i["slug"]: f"https://github.com/{r}" for k, i, _, r in created
                                       if k == "personal" and r})
        report.spaces[name] = root
        report.spaces.setdefault("brain", root)
        workspace = None
        project_dirs = {slugify(i["id"]): (d, r) for k, i, d, r in created if k == "project"}
        for kind, item, dst, prepo in created:
            path = "brain" if not offline else os.path.relpath(root, dst).replace("\\", "/")
            if kind == "personal":
                # A person mounts every project they are crew on, read-only, beside the brain.
                mounts = []
                for project in answers.get("projects") or []:
                    pid = slugify(project["id"])
                    if item["slug"] in (project.get("crew") or []) and pid in project_dirs:
                        pdst, prepo_url = project_dirs[pid]
                        mounts.append({"name": pid,
                                       "repo": f"https://github.com/{prepo_url}" if prepo_url else None,
                                       "path": os.path.relpath(pdst, dst).replace("\\", "/") if offline
                                       else f"mounts/{pid}", "branch": "main"})
                values = space_values(answers, item["slug"], item["name"], item["email"], [item["name"]],
                                      item.get("parent") or "brain", brain_repo, path,
                                      (answers.get("standing_choices") or {}).get(item["slug"]), feeds,
                                      mounts=mounts)
                setup_space(report, dst, "personal", values, feeds)
                workspace = workspace or dst
                if not offline:
                    for mount in mounts:
                        if mount.get("repo") and not (dst / mount["path"] / "space.yaml").exists():
                            add_mount(report, dst, mount["repo"], mount["path"], dry_run)
            else:
                crew = [names.get(s, humanize(s)) for s in item.get("crew") or []]
                values = space_values(answers, slugify(item["id"]), crew[0] if crew else top["name"],
                                      top["email"], crew, "brain", brain_repo, path)
                setup_space(report, dst, "project", values, [])
            if not offline and brain_repo and not (dst / "brain" / "space.yaml").exists():
                add_mount(report, dst, brain_repo, "brain", dry_run)
            report.spaces[spaceyaml.load(dst).name] = dst
    else:
        if dry_run:   # a person's case writes into this space alone, from here on
            report.add("dry run", True, "onboard.py without --dry-run", "nothing written")
            return report
        name = slugify(answers.get("slug") or answers.get("name") or answers.get("owner") or root.name)
        brain_repo, path = resolve_brain(root, brain or answers.get("brain") or None, offline)
        # a person joining later mounts every project they are crew on, as the company case does
        crew_mounts, crew_left = [], []
        pinned = brain_pins(brain_repo, path, offline) if answers.get("crew_projects") and not offline else None
        for project in answers.get("crew_projects") or []:
            if not isinstance(project, dict) or (
                    project.get("vault") and answers.get("name") not in (project.get("audience") or [])):
                continue   # a vault is mounted only by the names on it
            mount, why = crew_mount(project, root, name, brain_repo, offline, brain_path=path, pinned=pinned)
            if mount is None:
                pname = slugify(project["name"]) if isinstance(project.get("name"), str) else ""
                report.add(f"mount {pname or 'crew project'}", True,
                           "grep -A3 crew_projects _meta/state/invitations/<slug>.yaml",
                           f"left out: {why}; it mounts once the brain's registry pins it (onboard --check)")
                repo_given = project.get("repo")
                if pages.is_slug(pname) and isinstance(repo_given, str) and MOUNT_URL.fullmatch(repo_given):
                    crew_left.append({"name": pname, "repo": repo_given, "why": why[:200]})
                continue
            if all(m["name"] != mount["name"] for m in crew_mounts):
                crew_mounts.append(mount)
        if crew_left:
            write_yaml(root / CREW_LEFT, {"left_out": crew_left})
        values = space_values(answers, name, answers.get("name") or answers.get("owner"),
                              answers.get("email") or answers.get("owner_email"),
                              [answers.get("name") or answers.get("owner") or name],
                              answers.get("parent") or "brain", brain_repo, path, feeds=feeds,
                              mounts=crew_mounts)
        setup_space(report, root, "personal", values, feeds)
        if not offline and brain_repo and not (root / "brain" / "space.yaml").exists():
            add_mount(report, root, brain_repo, "brain", dry_run)
        try:   # the brain learns this is the person's workspace: late checks find it
            import classify
            sent = classify.announce(root, now)
            report.add("workspace announced", True, "python _meta/tools/classify.py . pending",
                       "a note names this workspace to the brain" if sent else "the brain knows it, or has no entry yet")
        except Exception as exc:
            report.add("workspace announced", False, "python _meta/tools/classify.py . pending", str(exc)[:200])
        for mount in crew_mounts:   # one row a mount: add_mount's when it mounts one (the review of #61)
            if not offline and mount.get("repo") and not (root / mount["path"] / "space.yaml").exists():
                add_mount(report, root, mount["repo"], mount["path"], dry_run)
            else:
                report.add(f"mount {mount['name']}", offline or (root / mount["path"] / "space.yaml").exists() or dry_run,
                           f"test -f {root / mount['path'] / 'space.yaml'}", "crew project mounted read-only")
        scan_codebases(report, root, now, offline)
        report.spaces[name] = root
    ui = Path(user_instructions) if user_instructions else default_user_instructions(root, offline, home)
    report.user_instructions = ui
    if workspace is not None:
        changed = install_instruction(ui, workspace)
        report.add("personal instruction", instruction_present(ui, workspace),
                   f"grep -c 'space:personal-instruction' {ui}", f"{'installed' if changed else 'present'}: {ui}")
        settings = Path(ui).parent / "settings.json"
        allowed = allow_directory(settings, workspace)
        report.add("workspace allowed", directory_allowed(settings, workspace),
                   f"grep -c additionalDirectories {settings}",
                   f"{'added' if allowed else 'present'}: sessions in any repository may journal there")
    tasks = {str(p): register_sync(report, p, spaceyaml.load(p).name, minutes, offline, dry_run)
             for p in dict.fromkeys(report.spaces.values())}
    if not offline:
        platform_settings(report, root, repo, org, dry_run)
        # The brain's fill lands first, so every space that mounts it pins the filled brain,
        # not the template-state commit the mount was added from.
        commit_fill(report, root, True, dry_run, repo)
        for _, _, dst, prepo in created:
            platform_settings(report, dst, prepo, org, dry_run)
            if (dst / ".gitmodules").exists():
                pins = run_cmd(["git", "submodule", "update", "--remote", "--init"], dry_run, cwd=dst)
                report.add(f"pins {dst.name}", pins.returncode == 0, f"git -C {dst} submodule status",
                           "mounts pinned to their current main" if pins.returncode == 0
                           else pins.stderr.strip()[:200])
            commit_fill(report, dst, True, dry_run, prepo)
    for feed in feeds:
        check = f"test -s {workspace or root}/_meta/state/feed/{feed['name']}.jsonl"
        if feed["name"] in connected:   # the apps step signed in to it and read it: nothing is the person's
            report.add(f"connect {feed['name']}", True, check, "connected: the apps step read it")
            continue
        report.add(f"connect {feed['name']}", False, check,
                   "sign in with `/mcp` in Claude Code, or Settings, Connectors on claude.ai",
                   needs_owner=True)
    write_needs_you(root, skipped)
    if workspace is not None and not feeds:
        # nothing connected yet: each connector is one offer on Needs you, never a requirement
        write_needs_you(workspace, [{"key": f"connect-{name}", "ask": (
            f"Connect your {name}? Say 'connect my {name}' in a session: it is added to your sources "
            "and the next sweep reads it"), "default": "yes, when you have a minute"}
            for name in ("mail", "calendar", "chat", "calls")], path="_inbound/needs-you/connect.yaml",
            prefix="onboard")
    for path in dict.fromkeys(report.spaces.values()):
        finish_space(report, path)
    if not offline:
        commit_fill(report, root, not offline, dry_run, repo)
    for path in dict.fromkeys(report.spaces.values()):
        write_state(path, case if path == root else "person", offline, ui,
                    workspace if path == root else path, tasks[str(path)],
                    repo if path == root else next((r for _, _, d, r in created if d == path), None),
                    report, full=path == root)
    return report


# ----- invitations: the owner invites, the colleague runs onboard on their own machine -------
READ_LEVELS = ("read", "triage", "write", "maintain", "admin")


def can_read(repo: str, login: str) -> bool:
    """True when the host says `login` can read `repo`: a permission read, never a grant."""
    res = run_cmd(["gh", "api", f"repos/{repo}/collaborators/{login}/permission", "-q", ".permission"])
    return res.returncode == 0 and (res.stdout or "").strip() in READ_LEVELS


def is_public(repo: str) -> bool:
    """True when the host says anyone can read `repo` (a read, like can_read)."""
    res = run_cmd(["gh", "api", f"repos/{repo}", "-q", ".private"])
    return res.returncode == 0 and (res.stdout or "").strip() == "false"


def grant_advice(repos: list, title: str, login: str | None) -> str:
    """How the read access to each repository is given and by whom (reads only, never a grant):
    one this person cannot administer is its owner's to share (a private template, say); on a
    personal account, a collaborator; in an organisation, a company-wide team."""
    teams, steps = [], []
    for repo in repos:
        owner = repo.split("/")[0]
        admin = run_cmd(["gh", "api", f"repos/{repo}", "-q", ".permissions.admin"])
        if admin.returncode == 0 and (admin.stdout or "").strip() == "false":
            steps.append(f"only {owner}, who owns {repo}, can give {title} read access to it while it is private: "
                         "ask them")
            continue
        kind = run_cmd(["gh", "api", f"users/{owner}", "-q", ".type"])
        if kind.returncode == 0 and (kind.stdout or "").strip() == "User":
            steps.append(f"add {title} as a collaborator on {repo}: `gh api -X PUT repos/{repo}/collaborators/"
                         f"{login or '<login>'}` (a personal account has no teams; a collaborator can also push)")
        else:
            teams.append(repo)
    if teams:
        steps.insert(0, f"give a company-wide team read access to {'both ' if len(teams) > 1 else ''}"
                        f"{' and '.join(teams)}, and add {title} to it")
    return "; ".join(steps)


def company_name(root, space) -> str:
    """The company as the brain's charter names it (onboard drafts that sentence), else the
    space's name in words; never the host account that holds the repository."""
    charter = Path(root) / "identity" / "charter.md"
    text = charter.read_text(encoding="utf-8") if charter.exists() else ""
    found = re.search(r"This space is the brain of (.+?)\. ", text)
    return space.data.get("company") or (found.group(1).strip() if found else humanize(space.name))


def _plain(text) -> str:
    """A name or an address as compared: lower case, one space between words."""
    return " ".join(str(text or "").lower().split())


def login_from_members(org: str | None, person, taken=()) -> tuple:
    """The colleague's GitHub login read from the organisation's member list: the one member whose
    public profile address is their registered address, and whom no other person in the registry has
    as login. GitHub shows only an address that account verified; a profile name is never used, since
    any member can set theirs to a colleague's name. (login, how it was found), or (None, "") when
    the account is no organisation, the list cannot be read, or no member or more than one matches:
    then the agent asks the person in chat (`--login`). A read only, never a grant."""
    if not org or not LOGIN.fullmatch(str(org)):
        return None, ""
    kind = run_cmd(["gh", "api", f"users/{org}", "-q", ".type"])
    if kind.returncode != 0 or (kind.stdout or "").strip() != "Organization":
        return None, ""
    listed = run_cmd(["gh", "api", f"orgs/{org}/members?per_page=100", "--paginate", "-q", ".[].login"])
    if listed.returncode != 0:
        return None, ""
    want_mail = _plain(person.extra.get("email"))
    if not want_mail:
        return None, ""
    taken = {_plain(t) for t in taken}
    found = []
    for member in [l.strip() for l in (listed.stdout or "").splitlines() if l.strip()][:500]:
        if not LOGIN.fullmatch(member) or _plain(member) in taken:
            continue
        res = run_cmd(["gh", "api", f"users/{member}", "-q", "{email: .email}"])
        try:
            profile = json.loads(res.stdout) if res.returncode == 0 and res.stdout else {}
        except ValueError:
            profile = {}
        if isinstance(profile, dict) and _plain(profile.get("email")) == want_mail:
            found.append((member, "their address"))
    if len({m.lower() for m, _ in found}) != 1:
        return None, ""
    return found[0][0], f"from {org}'s member list, by {found[0][1]}"


def invite(root, slug: str, org: str | None = None, workspace_org: str | None = None,
           name: str | None = None, email: str | None = None, login: str | None = None) -> Path:
    """Write `_meta/state/invitations/<slug>.yaml` (the colleague's answers: name, parent, the
    brain, the projects they are crew on, their host login when the registry has it) and one
    Needs-you item for the owner with its read-back (their workspace's first synced run line).
    The colleague's setup takes from the file only who they are and the crew projects, each checked
    (`start.invitation_answers`, `crew_mount`): where their workspace goes (`org`, `workspace_org`
    are kept for this item's read-back) and their briefing address stay theirs, and they confirm the
    brain by name first. The sentence works only once the colleague
    can read the template and the brain: when the host does not confirm both (or no login is
    known), a second item recommends the access and who grants it (`grant_advice`: a team, a
    collaborator on a personal account, or the owner of a repository this person cannot
    administer); agents never grant it (decision 2026-10-12, Organization teams). Nothing is
    sent: the owner hands the file over. A colleague
    not yet in the registry is added as a person when `name` is given; else a ValueError says how.
    A public template needs no grant: then the item asks for the brain alone.
    The colleague's GitHub login is written into the invitation and onto their registry row
    (`match: {github: <login>}`) in the same change, so their releases are trusted from their first
    day: `login` when the person gave it in chat, else the one member of the organisation's list that
    matches them (`login_from_members`). The item names it and how it was found, so the owner sees it
    before sending the sentence, and the registry change waits for their click like every login does.
    A login already registered is never replaced: a different `login` is a ValueError."""
    root = Path(root).resolve()
    space = spaceyaml.load(root)
    reg = registry.Registry(root, space)
    slug = slugify(slug)
    person = reg.get(slug)
    if person is None and name:
        reg.add({"id": slug, "kind": "people", "title": name, **({"email": email} if email else {})},
                by="onboard")
        reg.save()
        person = reg.get(slug)
    if person is None:
        raise ValueError(f"{slug} is not in the registry yet: run again with --name \"<their full name>\" "
                         "(and --email <their address>) to register and invite them")
    if person.kind != "people":
        raise ValueError(f"{slug} is one of the {person.kind} in the registry, not a person: invite them "
                         "under another slug")
    state = read_yaml(root / "_meta" / "state" / "onboard.yaml", {}) or {}
    repo = state.get("repo") or git_remote(root)
    org = org or (str(repo).split("/")[0] if repo else None)
    crew = []
    for name, where in sorted((state.get("spaces") or {}).items()):
        path = Path(str(where))
        try:
            other = spaceyaml.load(path)
        except Exception:
            continue
        if other.profile == "project" and isinstance(other.audience, list) and person.title in other.audience:
            other_repo = git_remote(path)
            crew.append({"name": other.name, "repo": f"https://github.com/{other_repo}" if other_repo else None})
    template = (read_yaml(root / "_meta" / "state" / "template-version.yaml", {}) or {}).get("repo") \
        or template_repo(root) or "<template>"
    company = company_name(root, space)
    say = (f"Onboard me to {company}: https://github.com/{template} with my invitation "
           f"{repo or '<brain>'}:{slug}")
    given = str(login or "").strip().lstrip("@")
    login = str(person.match.get("github") or "").strip()
    if given and not LOGIN.fullmatch(given):
        raise ValueError(f"{pages._shown(given)} is not a GitHub login")
    if given and login and given.lower() != login.lower():
        raise ValueError(f"the registry already names {login} as {person.title}'s GitHub login: a login is "
                         "changed in the registry by its owner, never by an invitation")
    how = ""
    if not login and slug in reg.subjects:
        taken = [str(s.match.get("github") or "") for s in reg.everywhere()
                 if s.kind == "people" and s.id != slug and s.match.get("github")]
        if given and given.lower() in {t.lower() for t in taken}:
            raise ValueError(f"{given} is another person's GitHub login in the registry")
        found, how = (given, "as you gave it") if given else login_from_members(org, person, taken)
        if found:
            reg.subjects[slug].match = {**reg.subjects[slug].match, "github": found}
            reg.save()
            login = found
    answers = {"name": person.title, "slug": slug, "email": person.extra.get("email") or "",
               "parent": space.name if space.profile != "brain" else "brain",
               "brain": f"https://github.com/{repo}" if repo else "", "feeds": "none", "schedule": 15,
               "org": org, "workspace_org": workspace_org or org, "crew_projects": crew, "say": say}
    if login:
        answers["login"] = login
    target = root / "_meta" / "state" / "invitations" / f"{slug}.yaml"
    write_yaml(target, answers)
    items_path = root / "_inbound" / "needs-you" / "invitations.yaml"
    access_id = f"invite-{slug}-access"
    items = [i for i in (read_yaml(items_path, {}) or {}).get("items") or []
             if i.get("id") not in (f"invite-{slug}", access_id)]
    workspace = f"{workspace_org or org}/{slug}-workspace" if (workspace_org or org) else f"<org>/{slug}-workspace"
    both = [template, repo or "<brain>"]
    if not template.startswith("<") and is_public(template):
        both = both[1:]   # anyone can clone a public template: the brain is the one grant
    readable = " and ".join(both)
    items.append({"id": f"invite-{slug}", "kind": "question",
                  "text": (f"Invite {person.title}: send them this one sentence to say in Claude Code "
                           f"on their own machine: \"{say}\". Their agent does the rest (about fifteen "
                           f"minutes, mostly their sign-ins); the answers are in "
                           f"{target.relative_to(root).as_posix()}"
                           + (f". Their GitHub login is {login} ({how}); if that is not them, say so before "
                              "you send the sentence" if how else "")
                           + ". Send it to them yourself, directly: "
                           f"their setup asks them to confirm {repo or 'the brain'} by name and then accepts "
                           f"its organisation's invitation; one from any other organisation is theirs to accept. "
                           f"The sentence works once they can "
                           f"read {readable}"),
                  "recommendation": "yes, on their first day", "default": "they work without a workspace for now "
                  "(rules.md section 10)", "depends": 1,
                  # done once their first sync landed a run line, not when the repository exists: a
                  # setup can stop right after its first push, with no sync task to land anything
                  "check": f"gh api repos/{workspace}/contents/_meta/state/runs.jsonl -q .path"})
    blocked = [r for r in both if not login or r.startswith("<") or not can_read(r, login)]
    if blocked:
        text = (f"{person.title} cannot read {' and '.join(blocked)} yet (checked as {login}), so the "
                "invitation sentence stops at its first step" if login else
                f"{person.title}'s host login is unknown, so their read access to {readable} cannot be "
                f"checked; the invitation sentence works only once they can read {readable}")
        rec = grant_advice([r for r in both if not r.startswith("<")], person.title, login) or \
            f"give {person.title} read access to {readable}"
        if not login:
            rec += (f"; then say their GitHub login, and the agent invites again with it (`--login <login>`), "
                    "so this check runs itself and their releases are trusted")
        items.append({"id": access_id, "kind": "question",
                      "text": text + ". Who sees the brain is your choice: agents never grant access "
                      "(decision 2026-10-12, Organization teams)",
                      "recommendation": rec, "default": f"{person.title}'s agent cannot clone the template or "
                      "read the invitation, so they work without a workspace for now", "depends": 1,
                      "check": " && ".join(f"gh api repos/{r}/collaborators/{login or '<login>'}/permission "
                                           "-q .permission" for r in both)})
    write_yaml(items_path, {"items": items})
    return target


# ----- the read-back checks ----------------------------------------------------------
def prune_offboarded(root) -> list:
    """The onboarding root forgets spaces that were offboarded or whose folder is gone: their
    rows move to `offboarded`, and the steps naming only them to `offboarded_steps`, never
    re-checked. The one repair `--check` makes, to its own state file. Returns the names."""
    root = Path(root).resolve()
    path = root / "_meta" / "state" / "onboard.yaml"
    state = read_yaml(path, {}) or {}
    spaces = dict(state.get("spaces") or {})
    gone = {}
    for name, where in spaces.items():
        folder = Path(str(where))
        if folder.resolve() == root:
            continue
        if not folder.is_dir() or (folder / "_meta" / "state" / "offboard.yaml").exists():
            gone[name] = str(where)
    if not gone:
        return []
    keep_names = {n for n in spaces if n not in gone} | {root.name}

    def only_gone(row) -> bool:
        text = " ".join(str(row.get(k, "")) for k in ("name", "check", "result"))
        names_gone = [n for n in gone if n in text or Path(gone[n]).name in text]
        return bool(names_gone) and not any(k in text for k in keep_names if k)

    steps = list(state.get("steps") or [])
    state["spaces"] = {n: w for n, w in spaces.items() if n not in gone}
    state["offboarded"] = dict(state.get("offboarded") or {}, **gone)
    state["offboarded_steps"] = list(state.get("offboarded_steps") or []) + [r for r in steps if only_gone(r)]
    state["steps"] = [r for r in steps if not only_gone(r)]
    write_yaml(path, state)
    return sorted(gone)


def check(root) -> list:
    """Re-run only the read-back checks, the doctor's included (read only); print nothing here.
    Two writes: an onboarding root forgets spaces that were offboarded (`prune_offboarded`), and a probe
    task a build left behind is removed (`offboard.remove_probe_task`), with its step; not while a setup
    holds this computer's start lock, since its probe task is then the one it is running. Routines that
    another computer runs are one row that says so: its tasks are not this computer's to read back. Routines
    the sweep that runs in the cloud is one row too, with its task and when it last wrote a run line
    (`routines_in_the_cloud`), which fails only once a proof stopped it; the computer the move ran on reads its
    own routines back as well, its daily among them."""
    import onboarding_page
    import routine
    root = Path(root).resolve()
    prune_offboarded(root)
    state = read_yaml(root / "_meta" / "state" / "onboard.yaml", {}) or {}
    steps = []
    if state.get("probe_task") and not onboarding_page.setup_running():
        import offboard
        removed = Report(case="check")
        offboard.remove_probe_task(removed, root, state)
        steps.extend(removed.steps)
    errs = [e for e in validate.validate(root) if not e.startswith("warning:")]
    steps.append(Step("space.yaml validates", not errs, f"python _meta/tools/validate.py {root}",
                      "; ".join(errs)[:200] or "clean"))
    space = spaceyaml.load(root)
    for mount in space.mounts:
        name = mount.get("name", "")
        steps.append(Step(f"mount {name} resolves", space.mount_root(name) is not None,
                          f"test -f {mount.get('path') or name}/space.yaml", str(space.mount_root(name) or "absent")))
    steps.extend(crew_mount_steps(space))
    steps.extend(crew_left_steps(space))
    if state.get("user_instructions") and state.get("workspace"):
        ui = Path(state["user_instructions"])
        steps.append(Step("personal instruction installed", instruction_present(ui, state["workspace"]),
                          f"grep -c 'space:personal-instruction' {ui}", str(ui)))
    task = state.get("sync_task") or {}
    if state.get("offline"):
        steps.append(Step("sync task", (root / "_meta" / "state" / "sync-task.txt").exists(),
                          "cat _meta/state/sync-task.txt", "offline: recorded, not registered"))
    elif task.get("check"):
        steps.append(task_step("sync task", task))
    record = routine.host(root)
    away = bool(record) and not record.get("unclear") and record["machine"] != routine.this_machine()
    cloud = away and record["runner"] == "cloud"
    if away:
        steps.append(routines_in_the_cloud(root) if cloud else routines_elsewhere(root))
    if not away or (cloud and record.get("computer") == routine.this_machine()):
        scheduled = routine_schedule(root)
        if not scheduled and not state.get("offline") and any((root / ROUTINE_PROMPTS).glob("*.md")):
            scheduled = [unscheduled_step(root)]   # rendered is not scheduled: done only once it reads back
        steps.extend(scheduled)
        steps.extend(routine_run_step(t["name"], t.get("log")) for t in state.get("routine_tasks") or [] if t.get("name"))
    if state and not state.get("offline"):
        # this machine's prerequisites, read only: what broke since onboarding is named with its fix
        for r in doctor.diagnose(doctor.Machine(run=run_cmd, reach=lambda: True), fix=False,
                                 repos=[state["repo"]] if state.get("repo") else []):
            steps.append(Step(f"doctor: {r.name}", r.ok, r.check, r.found if r.ok else r.yours, not r.ok))
        steps.append(signing_key_step(root))
    if state.get("repo") and not state.get("offline"):
        res = run_cmd(["gh", "api", f"repos/{state['repo']}", "-q", ".allow_auto_merge"])
        steps.append(Step("auto-merge armed", res.stdout.strip() == "true",
                          f"gh api repos/{state['repo']} -q .allow_auto_merge", res.stdout.strip() or res.stderr.strip()[:100]))
    owner_items = []
    for row in state.get("steps") or []:
        if row.get("needs_owner"):
            passed = readback(row.get("check", ""))
            if passed:
                steps.append(Step(row["name"], True, row["check"], "done: the read-back passes"))
            else:
                steps.append(Step(row["name"], False, row["check"], row["result"], True))
                owner_items.append({"id": f"onboard-{slugify(row['name'])}", "kind": "question",
                                    "text": f"{row['name']}: {row.get('result') or 'a step only you can take'}",
                                    "recommendation": "do it once; the next check confirms it",
                                    "default": "this part of the setup stays undone", "depends": 1,
                                    "check": row.get("check", "")})
    # every owner step sits on Needs you with its read-back until the read-back passes
    target = root / "_inbound" / "needs-you" / "onboard-owner.yaml"
    if owner_items or target.exists():
        current = read_yaml(target, {}) or {}
        if (current.get("items") or []) != owner_items:
            write_yaml(target, {"items": owner_items})
    return steps


def routine_run_step(task: str, stem: str | None = None) -> Step:
    """An OS routine's last run, from the log routine.py keeps (`<routine>-<space>.log`, the `log` the
    task was recorded with, here `stem`; a record from before that was kept has the default prefix): a task can be
    registered and still fail every time (no `claude` command, a sign-in gone). Never read from the
    environment: the check runs where the task was not registered."""
    log = Path.home() / ".spaces" / "logs" / f"{stem or task.removeprefix(TASK_PREFIX)}.log"
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines() if log.exists() else []
    exits = [i for i, line in enumerate(lines) if line.startswith("exit ")]
    if not exits:
        return Step(f"routine {task} runs", True, f"cat {log}", "not run yet")
    last = exits[-1]
    ok = lines[last].strip() == "exit 0"
    said = next((line for line in reversed(lines[:last]) if line.strip()), "")
    return Step(f"routine {task} runs", ok, f"cat {log}", "last run ok" if ok else f"last run failed: {said[:160]}")


def task_step(label: str, task: dict) -> Step:
    """Read an OS task back: a Windows task as XML, its battery, catch-up and hidden settings
    included; a cron line by its text; a systemd timer by `is-active`."""
    if task["check"][0] == "schtasks":
        name = task.get("name") or task["check"][3]
        argv = ["schtasks", "/Query", "/TN", name, "/XML"]
        res = run_cmd(argv)
        missing = windows_task_missing(res.stdout or "")
        ok = res.returncode == 0 and not missing
        return Step(label, ok, " ".join(argv), name if ok else (
            f"registered without: {', '.join(missing)}; run onboard again to re-register it" if res.returncode == 0
            else (res.stderr or res.stdout or "not registered").strip()[:200]))
    res = run_cmd(task["check"])
    ok = res.returncode == 0 and (not task.get("line") or task["line"] in res.stdout)
    return Step(label, ok, " ".join(task["check"]), task.get("name", ""))


def crew_left_steps(space, mount=None) -> list:
    """The crew projects setup left out (`CREW_LEFT`), one row each while it is not mounted: once the
    brain's registry pins the project at the address the invitation gave (`registry_pin`), the daily upkeep
    mounts it from the pinned address (`mount`, `mint.mount_space`): space.yaml and .gitmodules are protected,
    so it lands with the person's one click on the sync's protected pull request. Without `mount` (the check),
    a failing row with the command (`mint.mount_line`) the daily runs; until the pin, a row that says it waits
    for the brain to pin it. Never a silent skip, never a mount from the invitation's own address."""
    data = read_yaml(space.root / CREW_LEFT, {}) or {}
    rows = data.get("left_out") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return []
    import mint
    broot = space.mount_root("brain")
    pinned = brain_pins(None, broot) if broot is not None else (lambda name: None)
    steps = []
    for row in rows:
        name = str(row.get("name") or "") if isinstance(row, dict) else ""
        if not pages.is_slug(name) or any(m.get("name") == name for m in space.mounts):
            continue
        pin = pinned(name)
        if pin and registry.same_repo(pin, row.get("repo")):
            check = f"grep -c {name} {space.root / 'space.yaml'}"
            if mount is None:
                steps.append(Step(f"crew project {name} mounted", False, check,
                                  f"the brain's registry pins it now: the daily runs `{mint.mount_line(name, pin)}`"))
                continue
            try:
                mount(space.root, name, pin)
            except Exception as exc:   # an address mint refuses, a space.yaml it cannot write: tried again next run
                steps.append(Step(f"crew project {name} mounted", False, check,
                                  f"`{mint.mount_line(name, pin)}` failed ({str(exc)[:160]}): tried again next run"))
                continue
            steps.append(Step(f"crew project {name} mounted", True, check,
                              "mounted from the brain's registry pin; it lands with your one click on the sync's "
                              "protected pull request"))
        else:
            steps.append(Step(f"crew project {name} left out", True, f"cat {CREW_LEFT.as_posix()}",
                              f"waits for the brain's registry to pin {pages._shown(row.get('repo'))} for {name}"))
    return steps


def crew_mount_steps(space) -> list:
    """A workspace mounts every project space its owner is crew on: a subject in the brain's
    registry with a `home` of its own and a `match.repo`, whose owner (inherited, else the
    brain's owner) is the workspace's owner or whose `audience` names them. One row each."""
    broot = space.mount_root("brain") if space.profile == "personal" and space.owner else None
    if broot is None:
        return []
    import mint
    try:
        brain = spaceyaml.load(broot)
    except Exception:  # a brain that does not load fails its own validation, not this row
        return []
    reg = registry.Registry(broot, brain)
    steps = []
    for subject in reg.all():
        url, home = registry.repo_url(subject.match.get("repo")), subject.home
        if not url or not home or home in ("brain", brain.name, space.name) or subject.state == "archived":
            continue
        audience = subject.extra.get("audience") or []
        crew = [reg.owner_of(subject.id)] + (audience if isinstance(audience, list) else [audience])
        if space.owner not in crew:
            continue
        mounted = any(m.get("name") == home or registry.same_repo(m.get("repo"), url) for m in space.mounts)
        steps.append(Step(f"crew project {home} mounted", mounted, f"grep -c {home} {space.root / 'space.yaml'}",
                          "in space.yaml mounts" if mounted else f"not mounted: run `{mint.mount_line(home, url)}`"))
    return steps


def readback(check: str):
    """Run an owner step's read-back: True when it passes, False when it fails, None when the
    command is not one this function knows (the step then stays on the owner's list)."""
    check = (check or "").strip()
    words = check.split(None, 2)  # paths keep their backslashes: no shell quoting rules here
    if len(words) == 3 and words[0] == "test" and words[1] in ("-s", "-f", "-d"):
        target = Path(words[2].strip("'\""))
        if words[1] == "-d":
            return target.is_dir()
        return target.is_file() and (words[1] == "-f" or target.stat().st_size > 0)
    grep = re.match(r"grep -c '([^']+)' (.+)$", check)
    if grep:
        target = Path(grep.group(2).strip().strip("'\""))
        return target.is_file() and grep.group(1) in target.read_text(encoding="utf-8", errors="replace")
    if words and words[0] == "gh":
        import shlex
        try:
            res = run_cmd(shlex.split(check, posix=True))
        except ValueError:
            return None
        return res.returncode == 0 and bool(res.stdout.strip())
    return None


def print_steps(steps) -> None:
    width = max([len(s.name) for s in steps] + [4])
    for s in steps:
        mark = "owner" if s.needs_owner else ("ok" if s.done else "FAIL")
        # a row that is not done shows its whole result: it often names the fix; the cloud's row is there to be read
        whole = mark != "ok" or s.name == CLOUD_STEP
        print(f"{mark:5} {s.name:<{width}}  {s.result if whole else s.result[:70]}")
        if s.needs_owner or not s.done:
            print(f"      check: {s.check}")


# ----- go-live: a shadow space becomes live --------------------------------------------
def _replace_paths(value, old: str, new: str):
    """Every string in `value` (nested) with the old root path replaced by the new one."""
    if isinstance(value, str):
        return value.replace(old, new) if old else value
    if isinstance(value, list):
        return [_replace_paths(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: _replace_paths(v, old, new) for k, v in value.items()}
    return value


def go_live(root, answers=None, offline=False, org=None, dry_run=False, platform=False) -> Report:
    """Switch a shadow space to live: mode, time_scale, mounts by name, onboard state paths,
    the sync task and the platform settings. Each step is a row; a live space is unchanged.
    The platform settings follow a switch; `platform` applies them anyway (a space filled live
    from the start, as a swap fills it, has nothing to switch)."""
    root = Path(root).resolve()
    answers = answers or {}
    report = Report(case="go-live")
    space = spaceyaml.load(root)
    data = dict(space.data)
    changed = []
    if data.get("mode") != "live":
        data["mode"] = "live"
        changed.append("mode")
    if "time_scale" in data:
        data.pop("time_scale")
        changed.append("time_scale")
    wanted = dict(answers.get("mounts") or {})
    mounts = []
    for mount in space.mounts:
        mount = dict(mount)
        repo = wanted.get(mount.get("name"))
        if repo and mount.get("repo") != repo:
            mount["repo"] = repo
            changed.append(f"mount {mount.get('name')}")
        mounts.append(mount)
    if mounts:
        data["mounts"] = mounts
    if changed and not dry_run:
        spaceyaml.save(spaceyaml.Space(root=root, data=data))
    report.add("space.yaml live", True, "grep -c 'mode: live' space.yaml",
               ("changed: " + ", ".join(changed)) if changed else "already live")
    state_path = root / "_meta" / "state" / "onboard.yaml"
    state = read_yaml(state_path, {}) or {}
    old = None
    own = (state.get("spaces") or {}).get(space.name)
    for candidate in (own, state.get("workspace")):
        if candidate and Path(str(candidate)).name == root.name and str(candidate) != str(root):
            old = str(candidate)
            break
    if old:
        state = _replace_paths(state, old, str(root))
        if not dry_run:
            write_yaml(state_path, state)
    report.add("onboard state paths", True, "cat _meta/state/onboard.yaml",
               f"{old} -> {root}" if old else "already the live paths")
    task = state.get("sync_task") or {}
    task_ok = bool(task) and (task.get("root") == str(root)
                              or str(root) in " ".join(str(x) for x in (task.get("create") or [])))
    if not task_ok and (old or not task):
        minutes = minutes_of(answers.get("sync_minutes") or 15)
        record = register_sync(report, root, space.name, minutes, offline, dry_run)
        state["sync_task"] = record
        if not dry_run:
            write_yaml(state_path, state)
    else:
        report.add(f"sync task {space.name}", True, "cat _meta/state/onboard.yaml", "already for this checkout")
    repo = state.get("repo") or git_remote(root)
    if not offline and (changed or platform):
        platform_settings(report, root, repo, org, dry_run)
    elif not offline:
        report.add("platform settings", True, f"gh api repos/{repo} -q .allow_auto_merge", "unchanged")
    if changed and not dry_run:
        _lazy(report, "AGENTS.md", "agents_md", lambda m: m.write(root), "python _meta/tools/agents_md.py --check")
    errs = [e for e in validate.validate(root) if not e.startswith("warning:")]
    report.add("validate", not errs, f"python _meta/tools/validate.py {root}", "; ".join(errs)[:300] or "clean")
    return report


# ----- the fixture company -----------------------------------------------------------
def company_answers(data) -> dict:
    top = data.get("top_owner") or {}
    people = list(data.get("people") or [])
    names = {p.get("slug"): p.get("name") for p in people}
    names.setdefault(top.get("slug"), top.get("name"))
    return {"_fixture": True, "company": data.get("company") or "Company", "owner": top.get("name") or "",
            "owner_slug": top.get("slug"), "owner_email": top.get("email") or "", "people": people,
            "domains": [{"id": d["id"], "owner": names.get(d.get("owner"), d.get("owner"))}
                        for d in data.get("domains") or []],
            "clients": data.get("clients") or [], "projects": data.get("projects") or [],
            "subjects": data.get("subjects") or [],
            "standing_choices": data.get("standing_choices") or {}, "mode": data.get("mode") or "fixture",
            "time_scale": data.get("time_scale") or 1440, "feeds": "mail: owner, calls: owner"}


def _feed_lines(path) -> dict:
    """A fixture person's records by the feed file they are exported to: calls to `calls`, the rest to `mail`."""
    lines = [l for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    return {"mail": [l for l in lines if json.loads(l).get("kind") != "call"],
            "calls": [l for l in lines if json.loads(l).get("kind") == "call"]}


def fixture_spaces(target, company_yaml=None, hold=()) -> Report:
    """Offline company onboard of the fixture: brain, every workspace and every project as siblings. The
    feed of a person in `hold` is not copied: their history is read in parts (`staged_history`)."""
    target = Path(target).resolve()
    company_yaml = Path(company_yaml) if company_yaml else FIXTURE / "company.yaml"
    answers = company_answers(read_yaml(company_yaml, {}) or {})
    brain = target / "brain"
    copy_template(TEMPLATE_ROOT, brain)
    report = run(brain, answers, offline=True, home=target, everyone=True)
    for person in people_of(answers):
        ws, src = report.spaces.get(person["slug"]), company_yaml.parent / "feed" / f"{person['slug']}.jsonl"
        if ws is None or not src.exists() or person["slug"] in hold:
            continue
        for feed, mine in _feed_lines(src).items():
            if mine:
                write_text(ws / "_meta" / "state" / "feed" / f"{feed}.jsonl", "\n".join(mine) + "\n")
    data = read_yaml(company_yaml, {}) or {}
    for pair in data.get("channels") or []:
        a, b = (report.spaces.get(str(p)) for p in pair)
        if a is None or b is None:
            continue
        import mint
        made = mint.channel(a, b)
        report.steps.extend(made.steps)
        report.spaces.update(made.spaces)
    for kind, key in (("answers", "asker"), ("leaks", "audience_root")):
        data = read_yaml(company_yaml.parent / f"{kind}.yaml")
        if data is None:
            continue
        listed = data if isinstance(data, list) else next((v for v in data.values() if isinstance(v, list)), [])
        wrap = None if isinstance(data, list) else next(k for k, v in data.items() if isinstance(v, list))
        for name, path in report.spaces.items():
            mine = [e for e in listed if isinstance(e, dict) and str(e.get(key)) in (name, path.name)]
            if mine:
                write_yaml(path / "_meta" / "tests" / f"{kind}.yaml", mine if wrap is None else {wrap: mine})
    return report


def _late_feeds(spaces: dict):
    """Records in `feed/<slug>-late.jsonl` arrive on scaled day 4, stamped with the virtual
    clock, so the checker meets evidence newer than what was signed on day 2. On day 5 the
    top owner shortens the window of their signed Harbor page (`review_every: 2`), as a person
    may, so the weekly review batch has something to list within the fixture's week, and Ben
    answers yes to the conflict note his late record raised: it quotes a mail the release rule
    held for his yes, so the note waits for it too (check.conflict_notes), and travels once he
    gives it."""
    def inject(day: int, at) -> None:
        if day == 2:
            # Chloe, outside the Harbor crew, asks Harbor through her corridor: its status (a
            # standing choice answers at once), its correspondence (never), a detail (waits for
            # Harbor's person and expires). Ben asks Ada's workspace about her hiring plan: a
            # personal space waits for its person; nobody answers, it expires.
            import request
            chloe, ben = spaces.get("chloe"), spaces.get("ben")
            if chloe:
                request.ask(chloe, "harbor", "status", "Where does Harbor stand?", asked_by="Chloe Martin", now=at)
                request.ask(chloe, "harbor", "correspondence", "Send me the client's mails about Harbor",
                            asked_by="Chloe Martin", now=at)
                request.ask(chloe, "harbor", "detail", "Who runs the port schedule?", asked_by="Chloe Martin", now=at)
            if ben:
                request.ask(ben, "hiring-plan", "status", "When does the hiring plan start?", asked_by="Ben Liu", now=at)
            return
        if day == 3:
            # Ben files a desk folder under the wrong kind and a page in the wrong subject's
            # folder; the librarian moves both, each verified and logged.
            ben = spaces.get("ben")
            if ben:
                notes = Path(ben) / "work" / "clients" / "beacon" / "export-notes" / "notes.md"
                write_text(notes, "Export notes for the clinic.\n")
                stray = Path(ben) / "knowledge" / "projects" / "harbor" / "beacon-fact.md"
                pages.write(stray, {"id": "p-0be4c0de", "type": "entity", "title": "A Beacon fact",
                                    "status": "draft", "generated": pages.generated("ben", at),
                                    "subject": "beacon"}, "The ward export runs nightly.\n")
            return
        if day == 5:
            ada = spaces.get("ada")
            for path in sorted((Path(ada) / "knowledge" / "projects").rglob("*.md")) if ada else []:
                if pages.is_page_path(path):
                    fm, body = pages.read(path)
                    if fm.get("status") == "stable" and not fm.get("review_every"):
                        pages.write(path, dict(fm, review_every=2), body)
            ben = spaces.get("ben")
            held = Path(ben) / "_outbound" / "held" if ben else None
            notes = [path.stem for path in sorted(held.glob("*.md"))
                     if pages.read(path)[0].get("conflict_note")] if held and held.is_dir() else []
            if notes:
                import sign
                sign.apply(ben, {note: "yes" for note in notes}, by=spaceyaml.load(ben).owner, now=at)
            return
        if day != 4:
            return
        for slug, root in spaces.items():
            src = FIXTURE / "feed" / f"{slug}-late.jsonl"
            if not src.exists():
                continue
            stamp = at.strftime("%Y-%m-%dT%H:%M:%SZ")
            lines = []
            for line in src.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    rec["at"] = stamp
                    lines.append(json.dumps(rec))
            dst = Path(root) / "_meta" / "state" / "feed" / "mail.jsonl"
            dst.parent.mkdir(parents=True, exist_ok=True)
            with dst.open("a", encoding="utf-8", newline=chr(10)) as fh:
                fh.write(chr(10).join(lines) + chr(10))
    return inject


STAGED = ("ada",)   # whose history the fixture reads in three parts: her records are dated 1 to 3 October
STAGED_NOW = dt.datetime(2026, 10, 4, tzinfo=dt.timezone.utc)   # the day after the last of them
STAGED_BATCH = 1     # records a batch; a real run appends at most 50
STAGED_TOKENS = 120_000


def staged_history(spaces: dict, slugs=STAGED):
    """The history of each person in `slugs`, read oldest first in three paced parts on the fixture's fast clock
    (Task 11). The plan is made at once; then one run a scaled day, at night, reads the part `backfill.due`
    names: its records per feed, oldest first, in batches with a checkpoint after each. The first run of part 2
    ends at a usage limit that names no reset time; the next run goes on from its checkpoint. Part 1, through,
    is measured. Returns the `inject` for `chain.virtual`: it runs at the start of a scaled day, before the
    chains, as the routines run before the sweep they feed."""
    import backfill
    held = {slug: _feed_lines(FIXTURE / "feed" / f"{slug}.jsonl") for slug in slugs}
    for slug in held:
        backfill.plan(spaces[slug], {"2026-10": 4}, STAGED_NOW, window_days=3, first_part_max=1)

    def read(slug: str, part: dict, root: Path) -> str:
        """Append the part's records per feed from where the last run stopped; the outcome of the run."""
        limited = part["id"] == "p2" and not part.get("runs")
        for feed, lines in held[slug].items():
            marked = (part.get("checkpoints") or {}).get(feed)   # the last record read; else the part's first day
            low, high = pages.parse_iso(backfill.export_mark(root, feed)), pages.parse_iso(part["to"])
            todo = [rec for rec in map(json.loads, lines)
                    if (pages.parse_iso(rec["at"]) > low if marked else pages.parse_iso(rec["at"]) >= low)
                    and pages.parse_iso(rec["at"]) < high]
            for number in range(0, len(todo), STAGED_BATCH):
                batch = todo[number:number + STAGED_BATCH]
                target = root / "_meta" / "state" / "feed" / f"{feed}.jsonl"
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a", encoding="utf-8", newline="\n") as fh:
                    fh.write("".join(json.dumps({**rec, "backfill": True}) + "\n" for rec in batch))
                backfill.checkpoint(root, part["id"], feed, batch[-1]["at"])
                if limited:
                    return "limit"
        backfill.range_read(root, part["id"])   # every feed's range is read to its end: the run says so (R45, I-4)
        return "finished"

    def inject(day: int, at) -> None:
        night = at.replace(hour=1, minute=0, second=0, microsecond=0)
        for slug in held:
            root = Path(spaces[slug])
            part = backfill.due(root, night)
            if part is None:
                continue
            backfill.begin(root, part["id"])
            outcome = read(slug, part, root)
            backfill.after_run(root, part["id"], {"outcome": outcome, "why": "", "resets_at": None,
                                                  "usage": {"input_tokens": STAGED_TOKENS}}, now=at)
            if part["id"] == "p1":
                backfill.calibrate(root, 2, 14, backfill.load(root)["parts"][0]["tokens"], now=at)
    return inject


def fixture(target) -> int:
    """Onboard the fixture company, run the chain over it and the tests; 1 on any failure. Ends
    with one line, `fixture: passed` or `fixture: failed (what)`."""
    report = fixture_spaces(target, hold=STAGED)
    print_steps([s for s in report.steps if not s.needs_owner])   # sign-ins belong to a real setup
    import onboarding_page   # the page as the top owner sees it when setup finishes, before the week
    print(f"page: {onboarding_page.write(Path(target) / 'onboarding.html', report.steps, report.spaces)}")
    problems, missing = [f"step {s.name}" for s in report.failed()], []
    persons = {p["slug"]: p["name"] for p in people_of(company_answers(read_yaml(FIXTURE / "company.yaml", {}) or {}))}
    roots = dict(report.spaces)
    # corridors carry requests; they run no chain of their own
    unique = [p for p in dict.fromkeys(roots.values()) if spaceyaml.load(p).profile != "channel"]
    try:
        import backfill
        import chain
        history, late = staged_history(report.spaces), _late_feeds(report.spaces)
        chain.virtual(unique, days=7, steps_per_day=3, persons=persons, offline_roots=roots,
                      verdicts_after_day=2, verdicts=chain.load_verdicts(FIXTURE / "verdicts.yaml", unique),
                      inject=lambda day, at: (history(day, at), late(day, at)),
                      by={str(p): persons[p.name] for p in unique if p.name in persons})
        for slug in STAGED:
            left = backfill.active(report.spaces[slug])
            print(f"history: {slug} read in {len(backfill.load(report.spaces[slug]).get('parts') or [])} parts"
                  + ("" if left else ", all done"))
            problems += [f"{slug}'s history is not all read"] if left else []
    except ImportError as exc:
        missing.append(exc.name or "chain")
    try:  # the walk-and-search probe at the end of the fixture's week, every space that has questions
        import tests_run as _probe
        for path in unique:
            if (path / "_meta" / "tests" / "answers.yaml").exists():
                _probe.probe(path, roots=roots)
    except ImportError:
        missing.append("tests_run")
    try:
        import behaviour
        if getattr(behaviour.run_all(TEMPLATE_ROOT), "failed", 0):
            problems.append("behaviour scenarios")
    except ImportError:
        missing.append("behaviour")
    try:
        import tests_run
    except ImportError:
        tests_run, missing = None, missing + ["tests_run"]
    print(f"{'space':10} {'cand':>5} {'signed':>6} {'released':>8} {'held':>4} {'answers':>9} {'leaks':>9}")
    for path in unique:
        name = path.name
        cands = list((path / "_inbound" / "candidates").glob("*.md"))
        signed = sum(1 for p in (path / "knowledge").rglob("*.md") if pages.is_page_path(p)
                     and "sources" not in p.parts and pages.read(p)[0].get("status") == "stable")
        released = sum(1 for p in cands if pages.read(p)[0].get("released_from"))
        held = len([p for p in (path / "_outbound" / "held").glob("*") if p.name != ".gitkeep"])
        cells = []
        for fn, arg in (("answers", (path,)), ("leaks", (path, roots))):
            if tests_run is None or not (path / "_meta" / "tests" / f"{fn}.yaml").exists():
                cells.append("-")
                continue
            rep = getattr(tests_run, fn)(*arg)
            cells.append(f"{rep.passed}/{rep.failed}")
            if rep.failed:
                problems.append(f"{name} {fn}")
        print(f"{name:10} {len(cands):>5} {signed:>6} {released:>8} {held:>4} {cells[0]:>9} {cells[1]:>9}")
    print("cand: candidates waiting, signed: pages signed, released: releases received, held: items "
          "held at home, answers and leaks: tests passed/failed, -: none in that space")
    for mod in missing:
        print(f"missing: {mod}.py is not available; the fixture run is incomplete")
        problems.append(f"{mod}.py missing")
    print("fixture: passed" if not problems else f"fixture: failed ({'; '.join(problems)})")
    return 1 if problems else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--answers", help="YAML file of interview answers (no questions asked)")
    ap.add_argument("--brain", help="the brain to mount: a repository url or a local path")
    ap.add_argument("--seed", help="a desk seed work/<kind>/<subject>/<effort>/ (the project case)")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--org")
    ap.add_argument("--home", help="where new workspaces are made (default ~/.spaces; offline: beside this one)")
    ap.add_argument("--user-instructions", help="the user-level instruction file (default ~/.claude/CLAUDE.md)")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--fixture", metavar="DIR")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--connect", metavar="FEED", help="add one connector (mail, calendar, chat, calls, "
                    "documents, tracker, repository) to this space's sources")
    ap.add_argument("--audience", default="owner", help="with --connect: owner, crew or everyone")
    ap.add_argument("--scope", action="append", default=[], metavar="KEY=VALUE[,VALUE]",
                    help="with --connect: what to read (channels=C1,C2, team=KEY, project=ID, repo=owner/name)")
    ap.add_argument("--invite", metavar="SLUG", help="in the brain: write a colleague's invitation answers "
                    "and the owner's Needs-you item")
    ap.add_argument("--name", help="with --invite: the colleague's full name, to register them when new")
    ap.add_argument("--email", help="with --invite: the colleague's address (optional)")
    ap.add_argument("--login", help="with --invite: the colleague's GitHub login, as the person said it in chat "
                    "(else read from the organisation's member list)")
    ap.add_argument("--mount-keys", action="store_true", dest="mount_keys",
                    help="daily upkeep: this space's own mount keys, in the brain the keys colleagues' "
                         "workspaces asked for by note, and the crew projects left out")
    ap.add_argument("--go-live", action="store_true",
                    help="switch this shadow space to live (mounts by name from --answers `mounts:`)")
    ap.add_argument("--with-projects", action="store_true",
                    help="company case: also create the project spaces listed in the answers")
    ap.add_argument("--register-sync", action="store_true", dest="register_sync",
                    help="register this checkout's sync task (every 15 minutes) and add it to the "
                         "spaces on this machine, which the chains run over")
    ap.add_argument("--os-routines", action="store_true",
                    help="schedule this workspace's routines so they outlive the session: the OS runs them "
                         "via `claude -p`")
    ap.add_argument("--routines-scheduled", nargs="+", metavar="CHECK",
                    help="the harness's own scheduled tasks hold the routines and persist: one read-back per "
                         "routine (test -f <path>, grep -c '<text>' <path>, or a gh command)")
    ap.add_argument("--routines-here", action="store_true", dest="routines_here",
                    help="run this workspace's routines on this computer: its OS tasks, proved by the probe; then "
                         "every other computer's stop, and the app's scheduled tasks to turn off are printed")
    ap.add_argument("--pause", action="store_true", help="pause the routines, on every computer, until --until "
                    "(default 06:00 the next morning)")
    ap.add_argument("--until", metavar="ISO", help="with --pause: a date (06:00 that day) or a time, this computer's "
                    "own when it names no zone")
    ap.add_argument("--resume", action="store_true", help="end a pause of the routines")
    ap.add_argument("--routines-cloud", action="store_true", dest="routines_cloud",
                    help="run this workspace's sweep in the cloud: with --check, what keeps it from moving; with "
                         "--sweep and --timezone, the move, once a proof run of the task showed every refusal")
    ap.add_argument("--sweep", metavar="TASK", help="with --routines-cloud: the sweep's scheduled task (trig_...)")
    ap.add_argument("--timezone", metavar="ZONE", help="with --routines-cloud: the person's IANA zone (Europe/Amsterdam)")
    args = ap.parse_args(argv)
    if (args.until and not args.pause) or sum(map(bool, (args.pause, args.resume, args.routines_here,
                                                          args.routines_cloud))) > 1:
        print("onboard: --until goes with --pause only, and --pause, --resume, --routines-here and --routines-cloud are "
              "given one at a time")
        return 2
    moving = (args.sweep, args.timezone)
    if (args.routines_cloud or any(moving)) and (not args.routines_cloud or args.check == any(moving)
                                                 or any(moving) != all(moving)):
        print("onboard: --routines-cloud takes --check, or --sweep and --timezone together")
        return 2
    if args.dry_run and (args.routines_cloud or args.routines_here or args.pause or args.resume or args.connect
                         or args.invite or args.mount_keys):
        print("onboard: --dry-run goes with setting up a space or --register-sync only")
        return 2
    if args.routines_cloud:   # it registers no task
        root = Path(args.root).resolve()
        if args.check:
            blockers = cloud_blockers(root)
            print("\n".join(blockers) or CLOUD_CLEAR)
            running, stopped = _cloud_tasks(root), cloud_stopped(root)
            if stopped:   # the task is off and stays off: nothing runs the sweep until it moves here
                print(unrefused_sentence(root, stopped))
            elif running:   # moving again (a space made since): the task it runs as is proved, never made twice
                print(CLOUD_ALREADY.format(**running))
            return 1 if blockers else 0
        done, said, to_turn_off = routines_cloud(root, {"sweep": args.sweep}, args.timezone)
        print(said)
        for task in to_turn_off:   # the app's own scheduled tasks that ran them: the agent turns each off
            print(f"turn off: {task}")
        if done:
            import onboarding_page
            try:
                onboarding_page.refresh(root)
            except OSError:
                pass
        return 0 if done else 2
    if args.pause or args.resume:   # they register no task
        done, said = pause_routines(Path(args.root), until=args.until, resume=args.resume)
        print(said)
        import onboarding_page   # the progress page shows the pause at once, not at the next sync
        try:
            onboarding_page.refresh(Path(args.root).resolve())
        except OSError:
            pass
        return 0 if done else 1
    if not (args.check or args.connect or args.invite or args.routines_scheduled
            or args.mount_keys):   # the others register no task
        try:
            task_prefix()
        except ValueError as exc:
            print(f"onboard: {exc}")
            return 1
    if args.fixture:
        return fixture(args.fixture)
    root = Path(args.root).resolve()
    if args.connect:
        scope = {}
        for item in args.scope:
            key, _, value = item.partition("=")
            scope[key] = value.split(",") if key == "channels" else value
        added = connect(root, args.connect, audience=args.audience, scope=scope)
        print(f"{args.connect}: {'added to the sources' if added else 'already a source'}")
        return 0
    if args.invite:
        try:
            print(f"invitation: {invite(root, args.invite, org=args.org, name=args.name, email=args.email, login=args.login)}")
        except ValueError as exc:
            print(f"invite: {exc}")
            return 1
        return 0
    if args.mount_keys:
        report = keys_upkeep(root)
        print_steps(report.steps)
        return 1 if report.failed() else 0
    if args.register_sync:
        report = Report(case="sync")
        register_sync(report, root, spaceyaml.load(root).name, 15, args.offline, args.dry_run)
        print_steps(report.steps)
        return 1 if report.failed() else 0
    if args.routines_here:
        report = Report(case="routines")
        first = cloud_off_first(root)
        if first:   # at once: the agent turns the cloud's tasks off while this computer proves itself
            print(first, flush=True)
        to_turn_off = routines_here(report, root)
        print_steps(report.steps or [Step("routines", False, "ls _meta/state/routines", "none rendered yet: run start.py")])
        for task in to_turn_off:   # the app's own scheduled tasks that still run them: the agent turns each off
            print(f"turn off: {task}")
        import onboarding_page
        try:
            onboarding_page.refresh(root)
        except OSError:
            pass
        return 1 if report.failed() or not report.steps else 0
    if args.os_routines or args.routines_scheduled:
        report = Report(case="routines")
        if args.os_routines:
            register_routines(report, root, offline=args.offline, dry_run=args.dry_run)
        else:
            record_routine_checks(report, root, args.routines_scheduled)
        print_steps(report.steps or [Step("routines", False, "ls _meta/state/routines", "none rendered yet: run start.py")])
        if report.steps and not args.dry_run and not args.offline:
            import onboarding_page   # the page shows Routines done once the schedule reads back
            page = onboarding_page.after_check(root, routine_schedule(root) or report.steps)
            if page:
                print(f"page: {page}")
        return 1 if report.failed() or not report.steps else 0
    if args.go_live:
        report = go_live(root, read_yaml(args.answers, {}) if args.answers else {}, offline=args.offline,
                         org=args.org, dry_run=args.dry_run)
        print_steps(report.steps)
        return 1 if report.failed() else 0
    if args.check:
        steps = check(root)
        print_steps(steps)
        import onboarding_page
        page = onboarding_page.after_check(root, steps)
        if page:
            print(f"page: {page}")
        return 0 if all(s.done or s.needs_owner for s in steps) else 1
    if args.seed:
        import mint
        subject = Path(args.seed).as_posix().strip("/").split("/")[2:3] or ["project"]
        report = mint.run(root, args.seed, subject[0], [spaceyaml.load(root).owner], not args.offline,
                          org=args.org, home=args.home, dry_run=args.dry_run)
    else:
        given = read_yaml(args.answers, {}) if args.answers else {}
        case = detect(root, brain=args.brain)
        if case == "configured":
            case = PROFILE_CASE.get(spaceyaml.load(root).profile, "person")
        answers = interview(given, interactive=not args.answers and sys.stdin.isatty(), case=case, root=root)
        report = run(root, answers, offline=args.offline, org=args.org, dry_run=args.dry_run, home=args.home,
                     user_instructions=args.user_instructions, brain=args.brain,
                     with_projects=args.with_projects)
    print_steps(report.steps)
    return 1 if report.failed() else 0


if __name__ == "__main__":
    sys.exit(main())
