"""onboard: one verb, three cases. Sets a space up and proves it (shape 1).

    python _meta/tools/onboard.py [root] [--answers FILE] [--brain URL|PATH] [--seed PATH]
                                  [--offline] [--org ORG] [--home DIR]
                                  [--user-instructions PATH] [--check] [--fixture DIR] [--dry-run]

Cases: no brain in reach, a company (this repository becomes the brain and the top
owner's workspace is created); a brain in reach, a person (this repository becomes their
workspace); a desk seed, a project (handed to `mint.py`). `--check` re-runs only the
read-back checks and changes nothing. `--fixture DIR` onboards the fixture company
offline and runs the chain and the tests over it. Offline mode never runs gh, git,
schtasks or crontab; online, every such call goes through `run_cmd`. Nothing is deleted,
nothing is signed, `main` is pushed only for a repository created seconds earlier.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
FEEDS = ("mail", "calendar", "chat", "calls", "documents", "tracker")
PROFILE_CASE = {"brain": "company", "personal": "person", "project": "project", "channel": "person"}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "personal", "academic", "_scratch"}
# Where a space mounts other spaces: a new space mounts them itself, it never copies them.
MOUNT_DIRS = ("brain", "mounts")
# Never copied from the space a new one is made from: its fixture, its settings and the company's
# own test questions (they name real facts).
SKIP_PATHS = ("_meta/tests/fixture", ".github/template-marker", "_meta/state/feed", "AGENTS.md",
              "space.yaml", "_meta/tests/answers.yaml", "_meta/tests/leaks.yaml", "_meta/search.yaml")
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

    def add(self, name, done, check, result="", needs_owner=False) -> Step:
        step = Step(name, bool(done), check, str(result), needs_owner)
        self.steps.append(step)
        return step

    def failed(self) -> list:
        return [s for s in self.steps if not s.done and not s.needs_owner]


# ----- the one door to the outside -----------------------------------------
def run_cmd(argv, dry_run=False, cwd=None) -> subprocess.CompletedProcess:
    """gh, git, schtasks and crontab all pass through here. Offline mode never calls it."""
    argv = [str(a) for a in argv]
    if dry_run:
        print("dry-run:", " ".join(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=False, cwd=cwd)
    except OSError as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


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


def parse_list(value, default=None) -> list:
    if value is None or value == "" or (isinstance(value, str) and value.strip().lower() == "default"):
        return list(default or [])
    if isinstance(value, list):
        return list(value)
    items = [v.strip() for v in str(value).split(",") if v.strip()]
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
def copy_template(src, dst) -> bool:
    """Copy the machinery and the empty skeleton of `src` to `dst`; never its content."""
    src, dst = Path(src), Path(dst)
    if (dst / "space.yaml").exists():
        return False
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        posix = rel.as_posix()
        if path.is_dir() or any(p in SKIP_DIRS for p in rel.parts):
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


def ensure_required(dst, profile) -> None:
    dst = Path(dst)
    for entry in spaceyaml.PROFILE_TABLE[profile]["required"]:
        target = dst / entry
        if "." in Path(entry).name:
            if not target.exists():
                write_text(target, FRESH.get(entry, f"---\ntitle: {Path(entry).stem}\n---\n"))
        else:
            target.mkdir(parents=True, exist_ok=True)
            if not any(target.iterdir()):
                write_text(target / ".gitkeep", "")
    if profile != "channel":
        for sub in SUBDIRS:
            (dst / sub).mkdir(parents=True, exist_ok=True)
    for sub in SUBDIRS[:8]:
        (dst / sub).mkdir(parents=True, exist_ok=True)


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
    if marker.exists():
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
            "never_leaves": parse_list(answers.get("never_leaves"), spaceyaml.DEFAULTS["never_leaves"]),
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
    clone of a new repository."""
    if offline:
        dst = (Path(home) if home else Path(root).parent) / dirname
        made = copy_template(root, dst)
        report.add(f"create {dirname}", True, f"test -f {dst / 'space.yaml'}", "copied" if made else "exists")
        return dst, None
    base = Path(home) if home else Path.home() / ".spaces"
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
               f"cloned into {dst}" if ok else (res.stderr or res.stdout).strip()[:200])
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
    if push and unconfigured_remote(dst, dry_run):
        out = run_cmd(["git", "push", "-q", "origin", "HEAD:main"], dry_run, cwd=dst)
        note = "bootstrap push to main" if out.returncode == 0 else out.stderr.strip()[:200]
    done = res.returncode == 0 or "nothing to commit" in (res.stdout + res.stderr)
    report.add(f"commit {Path(dst).name}", done, f"git -C {dst} status --short", note)


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


def fill_registry(report, root, answers, top_owner, brain_name, now) -> None:
    space = spaceyaml.load(root)
    reg = registry.Registry(root, space)
    names = {p["slug"]: p["name"] for p in people_of(answers)}
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
        if person.get("domain") and reg.get(slugify(person["domain"])) is not None:
            data["part_of"] = slugify(person["domain"])
        upsert(data)
    reg.save()
    errs = reg.check()
    report.add("registry", not errs, "python _meta/tools/registry.py . check",
               "; ".join(errs) or f"{len(added)} subjects added")


def write_identity(root, company, owner, domains, never_leaves, now) -> None:
    terms = ", ".join(never_leaves) or "nothing beyond the hard rules"
    owners = "\n".join(f"- {humanize(d['id'])}: {d['owner']}" for d in domains) or "- none declared yet"
    drafts = {
        "identity/charter.md": ("policy", "Charter",
            f"# Charter\n\nThis space is the brain of {company}. Everyone in the company reads it; "
            f"{owner} signs for it.\n\n## What never leaves\n\nThese terms mark what stays in the space "
            f"that holds it, whatever the release class: {terms}. The list itself is the `never_leaves` "
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
    target = Path(root) / path
    existing = (read_yaml(target, {}) or {}).get("items") or []
    kept = [i for i in existing if not str(i.get("id", "")).startswith(prefix + "-")]
    items = kept + [{"id": f"{prefix}-{q['key']}", "kind": "question", "text": q["ask"],
                     "recommendation": ", ".join(map(str, q["default"])) if isinstance(q["default"], list)
                     else str(q["default"]), "default": "the default stays", "deadline": None,
                     "depends": 1, "payload": "space.yaml", "fingerprint": ""} for q in skipped]
    if items or target.exists():
        write_yaml(target, {"items": items})


# ----- the personal instruction ---------------------------------------------
def instruction_block(workspace) -> str:
    text = (TEMPLATES / "personal-instruction.md").read_text(encoding="utf-8")
    return text.replace("{workspace_path}", Path(workspace).resolve().as_posix()).strip() + "\n"


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
    return MARK_OPEN in text and Path(workspace).resolve().as_posix() in text


def connect(root, feed: str, audience: str = "owner") -> bool:
    """Add one connector to this space's sources (and the register of connected systems);
    the person's sign-in to the connector itself happens in their harness."""
    root = Path(root)
    if feed not in FEEDS:
        raise ValueError(f"a feed is one of {', '.join(FEEDS)}")
    space = spaceyaml.load(root)
    if any(isinstance(s, dict) and s.get("name") == feed for s in space.sources):
        return False
    space.data["sources"] = space.sources + [{"name": feed, "adapter": "files", "audience": audience}]
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


# ----- the sync task and the platform ------------------------------------------
def task_wrapper(root, task, py, script, home=None) -> Path:
    """Windows limits a task's command to 261 characters, and a long Python path or checkout
    path passes it. The task runs one short wrapper outside the repository instead (never
    committed: it names this machine's paths)."""
    base = Path(home) if home else Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "spaces"
    path = base / f"{task}.cmd"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'@echo off\r\n"{py}" "{script}" --root "{root}" --release-leg\r\n', encoding="utf-8")
    return path


def register_sync(report, root, name, minutes, offline, dry_run) -> dict:
    root = Path(root)
    py, script, task = sys.executable, root / "_meta" / "tools" / "sync.py", f"space-sync-{name}"
    if os.name == "nt":
        command = f'"{py}" "{script}" --root "{root}" --release-leg'
        if len(command) > 255 and not offline:
            command = f'"{task_wrapper(root, task, py, script)}"'
        create = ["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", str(minutes), "/TN", task,
                  "/TR", command]
        check, line = ["schtasks", "/Query", "/TN", task], None
    else:
        line = f"*/{minutes} * * * * {py} {script} --root {root} --release-leg"
        create, check = ["crontab", "-"], ["crontab", "-l"]
    record = {"name": task, "create": create, "check": check, "line": line, "root": str(root)}
    if offline:
        write_text(root / "_meta" / "state" / "sync-task.txt",
                   " ".join(create) + "\n" + (line + "\n" if line else "") + "check: " + " ".join(check) + "\n")
        report.add(f"sync task {name}", True, "cat _meta/state/sync-task.txt", "offline: command recorded")
        return record
    if line is None:
        res = run_cmd(create, dry_run)
    else:
        current = run_cmd(["crontab", "-l"], dry_run).stdout
        res = subprocess.CompletedProcess(create, 0, "", "")
        if line not in current:
            with tempfile.NamedTemporaryFile("w", suffix=".cron", delete=False) as tmp:
                tmp.write((current.rstrip("\n") + "\n" if current.strip() else "") + line + "\n")
            res = run_cmd(["crontab", tmp.name], dry_run)
            os.unlink(tmp.name)
    verify = run_cmd(check, dry_run)
    ok = res.returncode == 0 and verify.returncode == 0 and (dry_run or line is None or line in verify.stdout)
    report.add(f"sync task {name}", ok, " ".join(check),
               f"{task} every {minutes} minutes" if ok else (res.stderr or verify.stderr).strip()[:200])
    return record


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
    # No organization-level grant is needed: the auto-merge workflow arms auto-merge with the
    # repository token, which the merge settings above allow (proved on the template's own
    # pull requests). The platform's own first pull request is the read-back check.


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
    """The onboarding root records the whole report; every other space only its own part."""
    dst = Path(dst)
    spaces = {k: str(v) for k, v in report.spaces.items() if full or Path(v) == dst}
    steps = [s.row() for s in report.steps] if full else own_rows(report, dst, repo)
    write_yaml(dst / "_meta" / "state" / "onboard.yaml",
               {"case": case, "at": pages.iso(spaceyaml.load(dst).now()), "offline": offline, "repo": repo,
                "workspace": str(workspace) if workspace else None, "user_instructions": str(ui),
                "sync_task": task, "spaces": spaces, "steps": steps})


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
        shown = ", ".join(map(str, default)) if isinstance(default, list) else (str(default) or "none")
        try:
            raw = input(f"{q['ask']} [{shown}; `skip` to decide later] ").strip()
        except EOFError:  # stdin closed: every remaining default stands
            raw = ""
        if raw.lower() == "skip":
            skipped.append({**q, "default": default})
        out[key] = default if raw == "" or raw.lower() == "skip" else (
            parse_list(raw) if q.get("kind") in ("list", "choice") else raw)
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


def run(root, answers, offline=False, org=None, dry_run=False, home=None, user_instructions=None,
        brain=None, everyone=False, with_projects=False) -> Report:
    root = Path(root).resolve()
    answers = dict(answers or {})
    skipped = answers.pop("_skipped", [])
    case = detect(root, brain=brain)
    if case == "configured":
        case = PROFILE_CASE.get(spaceyaml.load(root).profile, "person")
    report = Report(case=case)
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
                       parse_list(answers.get("never_leaves"), spaceyaml.DEFAULTS["never_leaves"]), now)
        skipped = list(skipped) + [{"key": "identity", "ask": (
            f"The identity of {company} is drafted (identity/charter.md and the spine): read it and "
            "sign it, or say what to change"), "default": "sign it when it reads right"}]
        fill_registry(report, root, answers, top["name"], name, now)
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
                            run_cmd(["git", "submodule", "add", "-b", "main", mount["repo"], mount["path"]],
                                    dry_run, cwd=dst)
            else:
                crew = [names.get(s, humanize(s)) for s in item.get("crew") or []]
                values = space_values(answers, slugify(item["id"]), crew[0] if crew else top["name"],
                                      top["email"], crew, "brain", brain_repo, path)
                setup_space(report, dst, "project", values, [])
            if not offline and brain_repo and not (dst / "brain" / "space.yaml").exists():
                run_cmd(["git", "submodule", "add", "-b", "main", brain_repo, "brain"], dry_run, cwd=dst)
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
            run_cmd(["git", "submodule", "add", "-b", "main", brain_repo, "brain"], dry_run, cwd=root)
        for mount in crew_mounts:
            if not offline and mount.get("repo") and not (root / mount["path"] / "space.yaml").exists():
                run_cmd(["git", "submodule", "add", "-b", "main", mount["repo"], mount["path"]], dry_run, cwd=root)
            report.add(f"mount {mount['name']}", offline or (root / mount["path"] / "space.yaml").exists() or dry_run,
                       f"test -f {root / mount['path'] / 'space.yaml'}", "crew project mounted read-only")
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
        report.add(f"connect {feed['name']}", False,
                   f"test -s {workspace or root}/_meta/state/feed/{feed['name']}.jsonl",
                   "the owner signs in to the connector; its records then appear in the feed file",
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


def invite(root, slug: str, org: str | None = None, workspace_org: str | None = None) -> Path:
    """Write `_meta/state/invitations/<slug>.yaml` (the colleague's answers: name, parent, the
    brain, the projects they are crew on, their host login when the registry has it) and one
    Needs-you item for the owner with its read-back. The sentence works only once the colleague
    can read the template and the brain: when the host does not confirm both (or no login is
    known), a second item recommends the access, which only the owner grants (decision
    2026-10-12, Organization teams). Nothing is sent: the owner hands the file over."""
    root = Path(root).resolve()
    space = spaceyaml.load(root)
    reg = registry.Registry(root, space)
    person = reg.get(slug)
    if person is None or person.kind != "people":
        raise ValueError(f"{slug} is not a person in the registry")
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
    company = space.data.get("company") or (str(repo).split("/")[0] if repo else humanize(space.name))
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
    items.append({"id": f"invite-{slug}", "kind": "question",
                  "text": (f"Invite {person.title}: send them this one sentence to say in Claude Code "
                           f"on their own machine: \"{say}\". Their agent does the rest (about fifteen "
                           f"minutes, mostly their sign-ins); the answers are in "
                           f"{target.relative_to(root).as_posix()}. The sentence works once they can "
                           f"read {both[0]} and {both[1]}"),
                  "recommendation": "yes, on their first day", "default": "they work without a workspace for now "
                  "(rules.md section 10)", "depends": 1, "check": f"gh repo view {workspace}"})
    blocked = [r for r in both if not login or r.startswith("<") or not can_read(r, login)]
    if blocked:
        text = (f"{person.title} cannot read {' and '.join(blocked)} yet (checked as {login}), so the "
                "invitation sentence stops at its first step" if login else
                f"{person.title}'s host login is unknown, so their read access to {both[0]} and {both[1]} "
                "cannot be checked; the invitation sentence works only once they can read both")
        rec = f"give a company-wide team read access to both {both[0]} and {both[1]}, and add {person.title} to it"
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
    """Re-run only the read-back checks; print nothing here. The one write: an onboarding root
    forgets spaces that were offboarded (`prune_offboarded`)."""
    root = Path(root).resolve()
    prune_offboarded(root)
    state = read_yaml(root / "_meta" / "state" / "onboard.yaml", {}) or {}
    steps = []
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
        res = run_cmd(task["check"])
        ok = res.returncode == 0 and (not task.get("line") or task["line"] in res.stdout)
        steps.append(Step("sync task", ok, " ".join(task["check"]), task.get("name", "")))
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


def go_live(root, answers=None, offline=False, org=None, dry_run=False) -> Report:
    """Switch a shadow space to live: mode, time_scale, mounts by name, onboard state paths,
    the sync task and the platform settings. Each step is a row; a live space is unchanged."""
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
    if not offline and changed:
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


def fixture_spaces(target, company_yaml=None) -> Report:
    """Offline company onboard of the fixture: brain, every workspace and every project as siblings."""
    target = Path(target).resolve()
    company_yaml = Path(company_yaml) if company_yaml else FIXTURE / "company.yaml"
    answers = company_answers(read_yaml(company_yaml, {}) or {})
    brain = target / "brain"
    copy_template(TEMPLATE_ROOT, brain)
    report = run(brain, answers, offline=True, home=target, everyone=True)
    for person in people_of(answers):
        ws, src = report.spaces.get(person["slug"]), company_yaml.parent / "feed" / f"{person['slug']}.jsonl"
        if ws is None or not src.exists():
            continue
        lines = [l for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
        for feed, mine in (("mail", [l for l in lines if json.loads(l).get("kind") != "call"]),
                           ("calls", [l for l in lines if json.loads(l).get("kind") == "call"])):
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


def fixture(target) -> int:
    """Onboard the fixture company, run the chain over it and the tests; 1 on any failure."""
    report = fixture_spaces(target)
    print_steps(report.steps)
    failed, missing = bool(report.failed()), []
    persons = {p["slug"]: p["name"] for p in people_of(company_answers(read_yaml(FIXTURE / "company.yaml", {}) or {}))}
    roots = dict(report.spaces)
    # corridors carry requests; they run no chain of their own
    unique = [p for p in dict.fromkeys(roots.values()) if spaceyaml.load(p).profile != "channel"]
    try:
        import chain
        chain.virtual(unique, days=7, steps_per_day=3, persons=persons, offline_roots=roots,
                      verdicts_after_day=2, verdicts=chain.load_verdicts(FIXTURE / "verdicts.yaml", unique),
                      inject=_late_feeds(report.spaces),
                      by={str(p): persons[p.name] for p in unique if p.name in persons})
    except ImportError:
        missing.append("chain")
    try:  # the walk-and-search probe at the end of the fixture's week, every space that has questions
        import tests_run as _probe
        for path in unique:
            if (path / "_meta" / "tests" / "answers.yaml").exists():
                _probe.probe(path, roots=roots)
    except ImportError:
        missing.append("tests_run")
    try:
        import behaviour
        failed = failed or bool(getattr(behaviour.run_all(TEMPLATE_ROOT), "failed", 0))
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
            failed = failed or bool(rep.failed)
        print(f"{name:10} {len(cands):>5} {signed:>6} {released:>8} {held:>4} {cells[0]:>9} {cells[1]:>9}")
    for mod in missing:
        print(f"missing: {mod}.py is not available; the fixture run is incomplete")
    return 1 if failed or missing else 0


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
    ap.add_argument("--connect", metavar="FEED", help="add one connector (mail, calendar, chat, calls, ...) "
                    "to this space's sources")
    ap.add_argument("--invite", metavar="SLUG", help="in the brain: write a colleague's invitation answers "
                    "and the owner's Needs-you item")
    ap.add_argument("--go-live", action="store_true",
                    help="switch this shadow space to live (mounts by name from --answers `mounts:`)")
    ap.add_argument("--with-projects", action="store_true",
                    help="company case: also create the project spaces listed in the answers")
    args = ap.parse_args(argv)
    if args.fixture:
        return fixture(args.fixture)
    root = Path(args.root).resolve()
    if args.connect:
        print(f"{args.connect}: {'added to the sources' if connect(root, args.connect) else 'already a source'}")
        return 0
    if args.invite:
        print(f"invitation: {invite(root, args.invite, org=args.org)}")
        return 0
    if args.go_live:
        report = go_live(root, read_yaml(args.answers, {}) if args.answers else {}, offline=args.offline,
                         org=args.org, dry_run=args.dry_run)
        print_steps(report.steps)
        return 1 if report.failed() else 0
    if args.check:
        steps = check(root)
        print_steps(steps)
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
