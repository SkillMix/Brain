"""onboard: one verb, three cases. Sets a space up and proves it (shape 1).

    python _meta/tools/onboard.py [root] [--answers FILE] [--brain URL|PATH] [--seed PATH]
                                  [--offline] [--org ORG] [--home DIR]
                                  [--user-instructions PATH] [--check] [--fixture DIR] [--dry-run]
    python _meta/tools/onboard.py <workspace> --routines-here | --pause [--until ISO] | --resume

Cases: no brain in reach, a company (this repository becomes the brain and the top
owner's workspace is created); a brain in reach, a person (this repository becomes their
workspace); a desk seed, a project (handed to `mint.py`). `--check` re-runs only the
read-back checks and changes nothing. `--fixture DIR` onboards the fixture company
offline and runs the chain and the tests over it. Offline mode never runs gh, git,
schtasks, launchctl, crontab or systemctl; online, every such call goes through `run_cmd`. Nothing is
deleted, nothing is signed, `main` is pushed only for a repository created seconds earlier. The OS tasks it
registers are named `space-sync-<name>` and `space-<routine>-<name>`; an isolated test run sets
SPACES_TASK_PREFIX=space-test-<id>- so that its tasks, and only those, carry that prefix instead. Each
runs through the scheduler of the system that catches up after sleep: the Task Scheduler on Windows, a
launch agent (`com.spaces.<task>`) on macOS, a systemd user timer on a Linux that has one, else cron,
which does not catch up and says so.

A workspace's routines run on one computer through one scheduler (`routine.HOST`): the build registers
them only where nothing else runs them, `--routines-here` moves them to this computer once the probe
passed there, and `--pause` (until 06:00 the next morning, or `--until`) and `--resume` hold them.
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
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
    """Where this repository came from: a recorded origin, else what the platform says, else here."""
    data = read_yaml(Path(root) / "_meta" / "state" / "template-origin.yaml", {}) or {}
    if data.get("repo"):
        return data["repo"]
    remote = git_remote(root)
    if remote and not offline:
        try:
            res = run_cmd(["gh", "api", f"repos/{remote}", "-q", ".template_repository.full_name"])
            parent = (res.stdout or "").strip()
            if res.returncode == 0 and parent and parent != "null":
                return parent
        except Exception:
            pass
    return remote


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
    mounts = list(mounts or [])
    if not any(m.get("name") == "brain" for m in mounts):
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


def setup_space(report, dst, profile, values, feeds) -> None:
    """Fill a space for its profile unless it is configured already; shape its tree."""
    dst = Path(dst)
    current = read_yaml(dst / "space.yaml", {}) or {}
    if current.get("profile") in (None, "", spaceyaml.TEMPLATE_PROFILE):
        fill_space(dst, profile, **values)
        result = "written"
    else:
        result = "kept: already configured"
    ensure_required(dst, profile)
    remove_forbidden(report, dst, profile)
    if feeds:
        write_yaml(dst / "_meta" / "access" / "resources.yaml", {"resources": sources_of(feeds)})
    report.add(f"fill {dst.name} ({profile})", True, f"python _meta/tools/validate.py {dst}", result)


def create_space(report, root, dirname, repo_name, offline, org, home, tpl, dry_run):
    """The directory of a new space: offline a copy under `home` (default: a sibling), online a
    clone of a new repository. A dry run writes nothing: it only says where the space would go."""
    if offline:
        dst = (Path(home) if home else Path(root).parent) / dirname
        made = "would copy" if dry_run else "copied" if copy_template(root, dst) else "exists"
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
    report.add(f"create {dirname}", ok, f"gh repo view {full}",
               f"{'would clone' if dry_run else 'cloned'} into {dst}" if ok else (res.stderr or res.stdout).strip()[:200])
    return (dst if ok else None), full


def unconfigured_remote(dst, dry_run) -> bool:
    """True while origin/main still carries the template's own space.yaml (or none at all)."""
    if dry_run:
        return True
    run_cmd(["git", "fetch", "-q", "origin", "main", "--no-recurse-submodules"], dry_run, cwd=dst)
    shown = run_cmd(["git", "show", "origin/main:space.yaml"], dry_run, cwd=dst)
    return shown.returncode != 0 or "profile: template" in (shown.stdout or "")


def commit_fill(report, dst, push, dry_run) -> None:
    """Commit the first fill; push it to main while the repository is still unconfigured.

    The fill is the owner's own setup action, so it does not wait for the owner's click like
    a later machinery change would (protected paths). Once main carries a profile, every
    further change goes through the sync script and the platform.
    """
    run_cmd(["git", "add", "-A"], dry_run, cwd=dst)
    res = run_cmd(["git", "commit", "-q", "-m", "chore: onboard"], dry_run, cwd=dst)
    note = "committed; the sync script lands it"
    done = res.returncode == 0 or "nothing to commit" in (res.stdout + res.stderr)
    if push and unconfigured_remote(dst, dry_run):
        out = run_cmd(["git", "push", "-q", "origin", "HEAD:main"], dry_run, cwd=dst)
        said = " ".join(((out.stderr or "") + (out.stdout or "")).split())[:300]
        if out.returncode != 0:   # main stays the template's: the build is not done (rc1 run, issue 12)
            done, note = False, f"the first push to main failed, so main is still the template's: {said or 'git gave no reason'}"
        elif not dry_run and unconfigured_remote(dst, dry_run):
            done, note = False, "the first push to main went through, but main is still the template's"
        else:
            note = "bootstrap push to main"
    report.add(f"commit {Path(dst).name}", done, f"git -C {dst} status --short", note)


def add_mount(report, root, repo, path, dry_run) -> None:
    """Mount `repo` at `path` (a submodule on its `main`), as one step: a mount that did not come says so with git's
    own words, since a workspace without its brain has no company layer to read (rc1 run, issue 14)."""
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


def fill_registry(report, root, answers, top_owner, brain_name, now, workspaces=None) -> None:
    """`workspaces` maps a person's slug to the space name of the workspace made for them."""
    space = spaceyaml.load(root)
    reg = registry.Registry(root, space)
    names = {p["slug"]: p["name"] for p in people_of(answers)}
    workspaces = dict(workspaces or {})
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
        for key in ("workspace", "classification"):   # set once; a later decision is never undone
            if data.get(key) and not existing.extra.get(key):
                existing.extra[key] = data[key]
        if data.get("match") and not existing.match:
            existing.match = dict(data["match"])

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
        if workspaces.get(person["slug"]):
            data["workspace"] = workspaces[person["slug"]]
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
    it registers, reads back and removes tasks of its own and never touches a person's. A value
    that is not `space-` and lower-case letters, digits and hyphens is refused, never replaced by the
    default: a typo must not register tasks under the person's own names."""
    prefix = os.environ.get(TASK_PREFIX_ENV) or TASK_PREFIX
    if not re.fullmatch(r"space-[a-z0-9-]*", prefix):
        raise ValueError(f"{TASK_PREFIX_ENV} must start with space- and use only lower-case letters, digits and "
                         f"hyphens, not {prefix!r}")
    return prefix


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
    to that file: a hidden task's Python that cannot start says so nowhere else (the other schedulers log)."""
    root, py, path = Path(root), sys.executable, os.environ.get("PATH", "")
    if SYSTEM == "Windows":
        arguments = f"--headless {_argline(py, args)}"
        if stderr_log:
            log = Path(stderr_log)
            arguments = (f'--headless cmd.exe /d /s /c "{_argline(py, args)} 2>>"{log}" & '
                         f'echo {TASK_ENDED}>>"{log}""')
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


def register_sync(report, root, name, minutes, offline, dry_run) -> dict:
    """Register `sync.py` every `minutes`. Offline: recorded, not registered (the command of this system's
    scheduler; a Linux is recorded with cron, since whether it has a systemd user session is only found by
    asking systemctl, which offline mode never does)."""
    root = Path(root)
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
        write_text(root / "_meta" / "state" / "sync-task.txt",
                   " ".join(create) + "\n" + line + "\n" + "check: " + " ".join(check) + "\n")
        report.add(f"sync task {name}", True, "cat _meta/state/sync-task.txt", "offline: command recorded")
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
    """Why this computer's scheduler runs none of the routines, as a step under Routines that fails nothing: another
    computer runs them, the app's own scheduled tasks do (so recorded, or read back as `routine_checks` while nothing
    is recorded), or which computer does is unclear (`routine.host`). None when they run here, or nothing says where."""
    import routine
    record = routine.host(workspace)
    check = f"cat {(Path(workspace) / routine.HOST).as_posix()}"
    if record is None:
        return Step(ELSEWHERE_STEP, True, check, IN_THE_APP) if _harness_checks(workspace) else None
    if record.get("unclear"):
        return Step(ELSEWHERE_STEP, True, check, UNCLEAR_HOST)
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
    if not (record.get("machine") == routine.this_machine() and record.get("runner") == "os"):
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
    end = end if end.tzinfo else end.astimezone()
    if end <= now:
        raise ValueError("that time has passed: give one still to come")
    return end


def pause_routines(workspace, until: str | None = None, now=None, resume: bool = False) -> tuple:
    """`--pause` and `--resume`: (done, what to say). A pause sets the record's `paused_until` (`pause_end`), which
    every computer's runs read before they start; resuming clears it. With nothing recorded yet, this computer is
    recorded as the one that runs them first, as the build would record it, but only when their tasks read back here
    (`_runs_here`); else nothing is paused and the line says to pause them where they run. A record that is not one
    is not touched."""
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
    if record["machine"] != routine.this_machine():
        said += f" {record['name'] or 'The other computer'} hears of it at its next sync."
    if record["runner"] != "os":
        said += (" They run as scheduled tasks in the Claude app, which this pause does not stop; say 'run my routines "
                 "here' first.")
    return True, said


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
    protection = {"required_status_checks": {"strict": False, "contexts": ["checks"]},
                  "enforce_admins": False, "required_pull_request_reviews": None, "restrictions": None}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(protection, tmp)
    res = run_cmd(["gh", "api", "-X", "PUT", f"repos/{repo}/branches/main/protection", "--input", tmp.name],
                  dry_run)
    os.unlink(tmp.name)
    report.add(f"branch protection {repo}", res.returncode == 0,
               f"gh api repos/{repo}/branches/main/protection -q .required_status_checks.contexts",
               res.stderr.strip()[:200] or "checks required")
    login = run_cmd(["gh", "api", "user", "-q", ".login"], dry_run).stdout.strip()
    owners = Path(root) / ".github" / "CODEOWNERS"
    if owners.exists() and login:
        text = owners.read_text(encoding="utf-8")
        if "@OWNER-LOGIN" in text:
            write_text(owners, text.replace("@OWNER-LOGIN", f"@{login}"))
        report.add("CODEOWNERS", "@OWNER-LOGIN" not in owners.read_text(encoding="utf-8"),
                   "grep -c OWNER-LOGIN .github/CODEOWNERS", f"@{login}")
    # A repository outside the organization cannot see the organization's mount-reading
    # secret: its checks need the same token as a repository secret, which only the owner holds.
    if org and not repo.lower().startswith(org.lower() + "/") and (Path(root) / ".gitmodules").exists():
        listed = run_cmd(["gh", "secret", "list", "--repo", repo], dry_run)
        present = any(name in (listed.stdout or "") for name in ("BRAIN_READ_TOKEN", "MOUNT_READ_TOKEN"))
        report.add(f"mount token {repo}", present or dry_run, f"gh secret list --repo {repo}",
                   "present" if present else "the owner adds the repository secret BRAIN_READ_TOKEN "
                   "(a token that reads the mounted repositories) so the checks can read the mounts",
                   needs_owner=not present)
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
                      workspaces={i["slug"]: i["slug"] for k, i, _, _ in created if k == "personal"})
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
        name = slugify(answers.get("slug") or answers.get("name") or answers.get("owner") or root.name)
        brain_repo, path = resolve_brain(root, brain or answers.get("brain") or None, offline)
        # a person joining later mounts every project they are crew on, as the company case does
        crew_mounts = []
        for project in answers.get("crew_projects") or []:
            pname = slugify(project.get("name") or "")
            if not pname or (project.get("vault") and answers.get("name") not in (project.get("audience") or [])):
                continue   # a vault is mounted only by the names on it
            ppath = project.get("path") or f"mounts/{pname}"
            if offline and project.get("path") and Path(project["path"]).is_absolute():
                ppath = os.path.relpath(project["path"], root).replace("\\", "/")
            crew_mounts.append({"name": pname, "repo": project.get("repo"), "path": ppath, "branch": "main"})
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
        for mount in crew_mounts:
            if not offline and mount.get("repo") and not (root / mount["path"] / "space.yaml").exists():
                add_mount(report, root, mount["repo"], mount["path"], dry_run)
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
        commit_fill(report, root, True, dry_run)
        for _, _, dst, prepo in created:
            platform_settings(report, dst, prepo, org, dry_run)
            if (dst / ".gitmodules").exists():
                pins = run_cmd(["git", "submodule", "update", "--remote", "--init"], dry_run, cwd=dst)
                report.add(f"pins {dst.name}", pins.returncode == 0, f"git -C {dst} submodule status",
                           "mounts pinned to their current main" if pins.returncode == 0
                           else pins.stderr.strip()[:200])
            commit_fill(report, dst, True, dry_run)
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
        commit_fill(report, root, not offline, dry_run)
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


def invite(root, slug: str, org: str | None = None, workspace_org: str | None = None,
           name: str | None = None, email: str | None = None) -> Path:
    """Write `_meta/state/invitations/<slug>.yaml` (the colleague's answers: name, parent, the
    brain, the projects they are crew on, their host login when the registry has it) and one
    Needs-you item for the owner with its read-back (their workspace's first synced run line).
    The sentence works only once the colleague
    can read the template and the brain: when the host does not confirm both (or no login is
    known), a second item recommends the access and who grants it (`grant_advice`: a team, a
    collaborator on a personal account, or the owner of a repository this person cannot
    administer); agents never grant it (decision 2026-10-12, Organization teams). Nothing is
    sent: the owner hands the file over. A colleague
    not yet in the registry is added as a person when `name` is given; else a ValueError says how.
    A public template needs no grant: then the item asks for the brain alone."""
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
    login = str(person.match.get("github") or "").strip()
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
                           f"{target.relative_to(root).as_posix()}. The sentence works once they can "
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
            rec += (f"; then add their host login to the registry (`match: {{github: <login>}}` on {slug}) "
                    "and invite again, so this check runs itself")
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
    another computer runs are one row that says so: its tasks are not this computer's to read back."""
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
    if record and not record.get("unclear") and record["machine"] != routine.this_machine():
        steps.append(routines_elsewhere(root))
    else:
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
        # a row that is not done shows its whole result: it often names the fix
        print(f"{mark:5} {s.name:<{width}}  {s.result[:70] if mark == 'ok' else s.result}")
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
    may, so the weekly review batch has something to list within the fixture's week."""
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
    args = ap.parse_args(argv)
    if (args.until and not args.pause) or sum(map(bool, (args.pause, args.resume, args.routines_here))) > 1:
        print("onboard: --until goes with --pause only, and --pause, --resume and --routines-here are given one at a time")
        return 2
    if args.pause or args.resume:   # they register no task
        done, said = pause_routines(Path(args.root), until=args.until, resume=args.resume)
        print(said)
        import onboarding_page   # the progress page shows the pause at once, not at the next sync
        try:
            onboarding_page.refresh(Path(args.root).resolve())
        except OSError:
            pass
        return 0 if done else 1
    if not (args.check or args.connect or args.invite or args.routines_scheduled):   # the others register no task
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
            print(f"invitation: {invite(root, args.invite, org=args.org, name=args.name, email=args.email)}")
        except ValueError as exc:
            print(f"invite: {exc}")
            return 1
        return 0
    if args.register_sync:
        report = Report(case="sync")
        register_sync(report, root, spaceyaml.load(root).name, 15, args.offline, args.dry_run)
        print_steps(report.steps)
        return 1 if report.failed() else 0
    if args.routines_here:
        report = Report(case="routines")
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
