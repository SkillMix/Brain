"""mint: a desk seed becomes a project space (shape 1).

    python _meta/tools/mint.py [root] --seed PATH --name NAME --crew a b
                               [--offline] [--org ORG] [--home DIR] [--dry-run]
    python _meta/tools/mint.py [root] --mount NAME URL      mount another space read-only

The seed is `work/<kind>/<subject>/<effort>/` in a personal space. Mint creates the project
space from the template (online: a new private repository in the organization, cloned
under `--home` or ~/.spaces; offline: under `--home`, else a sibling directory), fills its
`space.yaml` with profile project and the crew as audience, copies the seed's pages in as
drafts under `knowledge/<kind>/<subject>/`, sets the subject's `home` in this space's registry and
proposes the platform team on Needs you. Every crew member's workspace checked out on this
machine mounts the new space; one checked out elsewhere gets a Needs-you item in the brain
with the mount line (`--mount`, the fix `onboard --check` names when a mount is missing).
Re-runnable: the receipt `_meta/state/mint-<name>.yaml` records the phases, and nothing is
written twice.
"""
from __future__ import annotations

import argparse
import shutil
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import classify  # noqa: E402
import onboard  # noqa: E402
import pages  # noqa: E402
import registry  # noqa: E402
import spaceyaml  # noqa: E402


def run_cmd(argv, dry_run=False, cwd=None):
    """Mint's door to the outside: the same one onboard uses, monkeypatched separately."""
    return onboard.run_cmd(argv, dry_run=dry_run, cwd=cwd)


def seed_parts(seed) -> tuple:
    parts = [p for p in Path(seed).as_posix().strip("/").split("/") if p]
    if len(parts) < 3 or parts[0] != "work":
        raise ValueError("the seed is a desk folder: work/<kind>/<subject>/<effort>/")
    return parts[1], parts[2]


def draft_page(src: Path, dst: Path, now) -> None:
    """Copy a seed page as a draft: frontmatter added when missing, signature dropped."""
    fm, body = pages.read(src)
    new = {"id": fm.get("id") or pages.new_id(), "type": fm.get("type") or "entity",
           "title": fm.get("title") or onboard.humanize(src.stem), "status": "draft",
           "generated": pages.generated("mint", now)}
    for key, value in fm.items():
        if key not in new and key not in pages.SIGNATURE_FIELDS and key != "signed_in":
            new[key] = value
    pages.write(dst, new, body)


# ----- corridors: the channel profile between two spaces -----------------------------------
def channel_name(a: str, b: str) -> str:
    return "channel-" + "-".join(sorted([onboard.slugify(a), onboard.slugify(b)]))


def _add_mount(root: Path, name: str, repo, path: str) -> bool:
    space = spaceyaml.load(root)
    if any(m.get("name") == name for m in space.mounts):
        return False
    space.data["mounts"] = space.mounts + [{"name": name, "repo": repo, "path": path, "branch": "main"}]
    spaceyaml.save(space)
    return True


def mount_space(root, name: str, url, path: str | None = None, online: bool = True,
                dry_run: bool = False) -> bool:
    """Mount another space read-only: its row in space.yaml and, online, the setup verb
    `git submodule add -b main <url> mounts/<name>`; the sync script lands both on its protected
    branch, the new mount's pin with its address in `.gitmodules`, after the person's click.
    True when the row is new."""
    root = Path(root).resolve()
    path = path or f"mounts/{name}"
    if dry_run:
        return not any(m.get("name") == name for m in spaceyaml.load(root).mounts)
    added = _add_mount(root, name, url, path)
    if online and url and not (root / path / "space.yaml").exists():
        run_cmd(["git", "submodule", "add", "-b", "main", url, path], dry_run, cwd=root)
    return added


def mount_line(name: str, url) -> str:
    """The one command that mounts a space, run in the workspace that should read it."""
    return f"python _meta/tools/mint.py . --mount {name} {url or '<repository url>'}"


# ----- crew mounts: every crew member's workspace mounts the project -----------------------
CREW_MOUNTS = Path("_inbound") / "needs-you" / "crew-mounts.yaml"


def local_spaces(*near, home=None, online=True) -> list:
    """The spaces checked out on this machine: every checkout beside the given ones and, online,
    every checkout under the spaces home (~/.spaces). A mount is never one: it sits inside a space."""
    places = [Path(p).resolve().parent for p in near]
    if online:
        places.append(Path(home) if home else Path.home() / ".spaces")
    found, seen = [], set()
    for place in dict.fromkeys(places):
        for child in sorted(place.iterdir()) if place.is_dir() else []:
            if child.resolve() in seen or not (child / "space.yaml").is_file():
                continue
            seen.add(child.resolve())
            try:
                found.append(spaceyaml.load(child))
            except Exception:
                continue
    return found


def crew_of(space: spaceyaml.Space) -> list:
    audience = space.audience if isinstance(space.audience, list) else []
    return list(dict.fromkeys([str(a) for a in audience] + ([space.owner] if space.owner else [])))


def mount_in_crew(report, project_root, name: str, url, crew, near=(), home=None, online=True,
                  dry_run=False) -> None:
    """Mount a project space in the workspace of each crew member checked out on this machine.
    A crew member whose workspace is elsewhere gets one Needs-you item in the brain, routed to
    them (`for`), holding the exact mount line."""
    project_root = Path(project_root).resolve()
    spaces = local_spaces(project_root, *near, home=home, online=online)
    workspaces = {}
    for space in spaces:
        if space.profile == "personal" and space.owner:
            workspaces.setdefault(space.owner, space.root)
    elsewhere = []
    for person in dict.fromkeys(str(c) for c in crew or []):
        ws = workspaces.get(person)
        if ws is None:
            elsewhere.append(person)
            continue
        path = f"mounts/{name}" if online else os.path.relpath(project_root, ws).replace("\\", "/")
        added = mount_space(ws, name, url, path, online, dry_run)
        report.add(f"mount {name} in {Path(ws).name}", True, f"grep -c {name} {Path(ws) / 'space.yaml'}",
                   "added" if added else "already there")
    if not elsewhere:
        return
    # the brain checkout on this machine: the project's own brain mount when it points outside
    # the project (offline), else a checked-out brain of the same name; never a mount
    broot = spaceyaml.load(project_root).mount_root("brain")
    brain = broot if broot is not None and not Path(broot).resolve().is_relative_to(project_root) else None
    if brain is None:
        bname = spaceyaml.load(broot).name if broot is not None else None
        brain = next((s.root for s in spaces if s.profile == "brain" and bname in (None, s.name)), None)
    who = ", ".join(elsewhere)
    if brain is None:
        report.add(f"mount {name} for {who}", False, f"test -f mounts/{name}/space.yaml",
                   f"no brain checkout on this machine: each runs `{mount_line(name, url)}` in their workspace",
                   needs_owner=True)
        return
    target = Path(brain) / CREW_MOUNTS
    items = [{"id": f"mount-{name}-{onboard.slugify(person)}", "kind": "question", "for": person,
              "text": f"Mount {name} in your workspace (you are crew on it): run `{mount_line(name, url)}` there",
              "recommendation": "yes", "default": f"your workspace does not read {name}", "deadline": None,
              "depends": 1, "fingerprint": "", "check": f"test -f mounts/{name}/space.yaml"}
             for person in elsewhere]
    ids = {i["id"] for i in items}
    existing = (onboard.read_yaml(target, {}) or {}).get("items") or []
    onboard.write_yaml(target, {"items": [i for i in existing if i.get("id") not in ids] + items})
    report.add(f"mount {name} for {who}", True, f"grep -c mount-{name} {target}",
               "not on this machine: a Needs-you item each in the brain, with the mount line")


def _skeleton_file(path: Path) -> bool:
    """A file the template's skeleton ships: a placeholder, never anyone's content."""
    if path.name in (".gitkeep", "index.md"):
        return True
    if path.suffix != ".md":
        return False
    try:
        fm, _ = pages.read(path)
    except Exception:
        return False
    gen = fm.get("generated") if isinstance(fm.get("generated"), dict) else {}
    return gen.get("by") == "onboard" and not fm.get("subject")


def strip_skeleton(dst: Path, profile: str) -> list:
    """Drop the folders a profile forbids when they hold only the template's skeleton."""
    removed = []
    for entry in spaceyaml.PROFILE_TABLE[profile]["forbidden"]:
        folder = Path(dst) / entry
        files = [p for p in folder.rglob("*") if p.is_file()] if folder.is_dir() else []
        if folder.is_dir() and all(_skeleton_file(p) for p in files):
            shutil.rmtree(folder)
            removed.append(entry)
    return removed


def planned(report: onboard.Report, dst, repo, then: str) -> onboard.Report:
    """A dry run stops once it knows where the space would go: nothing was written."""
    if dst is not None:
        report.add("dry run", True, "the same command without --dry-run",
                   f"would create {repo or dst}, then {then}; nothing was written")
    return report


def channel(root, other_root, online=False, org=None, home=None, dry_run=False,
            name: str | None = None) -> onboard.Report:
    """A corridor between this space and another: a `channel` space read by those two only,
    mounted read-only by both. Offline (and for two checkouts on this machine) both mounts are
    written; a space this machine cannot write gets an owner item with the exact mount line."""
    root, other_root = Path(root).resolve(), Path(other_root).resolve()
    a, b = spaceyaml.load(root), spaceyaml.load(other_root)
    name = onboard.slugify(name) if name else channel_name(a.name, b.name)
    report = onboard.Report(case="channel")
    dst, repo = onboard.create_space(report, root, name, name, not online, org, home,
                                     onboard.template_repo(root), dry_run)
    if dry_run:
        return planned(report, dst, repo, f"mount it in {a.name} and {b.name}")
    if dst is None:
        return report
    audience = sorted({str(x) for s in (a, b) for x in (s.audience if isinstance(s.audience, list) else [s.owner])})
    mount = next((m for m in a.mounts if m.get("name") == "brain"), {})
    brain_path = mount.get("path") or "brain"
    if not online and a.mount_root("brain") is not None:
        brain_path = os.path.relpath(a.mount_root("brain"), dst).replace("\\", "/")
    values = onboard.space_values(a.data, name, a.owner, a.owner_email, audience, None, mount.get("repo"),
                                  brain_path)
    # the copy brought the skeleton's generated indexes along; a corridor has no knowledge,
    # live, desk or outbox, so the freshly copied, content-free folders go before the fill
    strip_skeleton(dst, "channel")
    onboard.setup_space(report, dst, "channel", values, [])
    data = spaceyaml.load(dst)
    data.data["between"] = sorted([a.name, b.name])
    for key in ("left_out",):
        data.data[key] = sorted(set(a.left_out) | set(b.left_out))
    spaceyaml.save(data)
    onboard.finish_space(report, dst)
    if online:  # the corridor's fill lands before anyone pins it
        onboard.platform_settings(report, dst, repo, org, dry_run)
        onboard.commit_fill(report, dst, True, dry_run)
    url = f"https://github.com/{repo}" if online and repo else repo
    for space_root in (root, other_root):
        rel = os.path.relpath(dst, space_root).replace("\\", "/") if not online else f"mounts/{name}"
        added = mount_space(space_root, name, url, rel, online, dry_run)
        report.add(f"mount {name} in {space_root.name}", True, f"grep -c {name} {space_root / 'space.yaml'}",
                   "added" if added else "already there")
    onboard.register_sync(report, dst, name, 15, not online, dry_run)   # the corridor lands like any space
    report.spaces[name] = dst
    return report


def vault(root, name: str, audience: list, online=False, org=None, home=None, dry_run=False) -> onboard.Report:
    """A vault: the project template with a list of names as its audience, for what only a few
    people may read (HR, finance, a deal). It mounts the brain; nobody else mounts it."""
    root = Path(root).resolve()
    space = spaceyaml.load(root)
    name = onboard.slugify(name)
    report = onboard.Report(case="vault")
    names = [str(a).strip() for a in audience if str(a).strip()]
    if len(names) < 1:
        report.add("audience", False, "--audience", "a vault names who may read it")
        return report
    dst, repo = onboard.create_space(report, root, name, name, not online, org, home,
                                     onboard.template_repo(root), dry_run)
    if dry_run:
        return planned(report, dst, repo, f"fill it for {', '.join(names)}")
    if dst is None:
        return report
    mount = next((m for m in space.mounts if m.get("name") == "brain"), {})
    brain_path = mount.get("path") or "brain"
    if not online and space.mount_root("brain") is not None:
        brain_path = os.path.relpath(space.mount_root("brain"), dst).replace("\\", "/")
    values = onboard.space_values(space.data, name, names[0], space.owner_email if names[0] == space.owner else "",
                                  names, "brain", mount.get("repo"), brain_path)
    onboard.setup_space(report, dst, "project", values, [])
    data = spaceyaml.load(dst)
    data.data["vault"] = True
    spaceyaml.save(data)
    onboard.finish_space(report, dst)
    if online:
        onboard.platform_settings(report, dst, repo, org, dry_run)
        onboard.commit_fill(report, dst, True, dry_run)
    report.add("vault", True, f"grep -c 'vault: true' {dst / 'space.yaml'}",
               f"{name}: read by {', '.join(names)} only; nobody else mounts it")
    onboard.register_sync(report, dst, name, 15, not online, dry_run)
    report.spaces[name] = dst
    return report


def link_items(name: str) -> tuple:
    """The chat channel and tracker project named like the space, and the two items that
    propose creating them (the adapters confirm on the next sweep)."""
    match = {"channel": f"#{name}", "tracker": name.upper()}
    items = [{"id": f"mint-{name}-channel", "kind": "question",
              "text": f"link or create the chat channel #{name} for {name}",
              "recommendation": "yes", "default": "records from that channel do not attach to the project",
              "deadline": None, "depends": 1, "payload": f"_meta/state/mint-{name}.yaml", "fingerprint": "",
              "check": f"a sweep record carries channel #{name}"},
             {"id": f"mint-{name}-tracker", "kind": "question",
              "text": f"link or create the tracker project {name.upper()} for {name}",
              "recommendation": "yes", "default": "tracker records do not attach to the project",
              "deadline": None, "depends": 1, "payload": f"_meta/state/mint-{name}.yaml", "fingerprint": "",
              "check": f"a sweep record carries tracker {name.upper()}"}]
    return match, items


def run(root, seed, name, crew, online, org=None, home=None, dry_run=False,
        parent: str | None = None) -> onboard.Report:
    root = Path(root).resolve()
    name = onboard.slugify(name)
    crew = [str(c) for c in crew or []]
    space = spaceyaml.load(root)
    now = space.now()
    kind, subject = seed_parts(seed)
    seed_dir = root / Path(seed)
    report = onboard.Report(case="project")
    receipt_path = root / "_meta" / "state" / f"mint-{name}.yaml"
    phases = dict((onboard.read_yaml(receipt_path, {}) or {}).get("phases") or {})
    offline = not online
    org = org or (onboard.git_remote(root) or "/").split("/")[0] or None

    def receipt():
        onboard.write_yaml(receipt_path, {"name": name, "seed": str(seed), "crew": crew, "at": pages.iso(now),
                                          "space": phases.get("create"), "phases": phases,
                                          "steps": [s.row() for s in report.steps]})

    dst, repo = onboard.create_space(report, root, name, name, offline, org, home,
                                     onboard.template_repo(root), dry_run)
    if dry_run:
        return planned(report, dst, repo, f"draft the seed's pages there, set home: {name} on {subject} "
                       "and mount it for the crew")
    if dst is None:
        receipt()
        return report
    phases["create"] = str(dst)
    mount = next((m for m in space.mounts if m.get("name") == "brain"), {})
    brain_path = mount.get("path") or "brain"
    if offline and not Path(brain_path).is_absolute() and space.mount_root("brain") is not None:
        brain_path = os.path.relpath(space.mount_root("brain"), dst).replace("\\", "/")
    # A sub-team's parent is its division: it mounts every ancestor flat (the brain and the
    # division), and its releases still go to each subject's home.
    extra_mounts = []
    if parent and parent != "brain":
        pmount = next((m for m in space.mounts if m.get("name") == parent), None)
        if pmount is not None:
            proot = space.mount_root(parent)
            ppath = os.path.relpath(proot, dst).replace("\\", "/") if offline and proot else f"mounts/{parent}"
            extra_mounts.append({"name": parent, "repo": pmount.get("repo"), "path": ppath, "branch": "main"})
    values = onboard.space_values(space.data, name, space.owner, space.owner_email, crew,
                                  parent or space.parent or "brain", mount.get("repo"), brain_path,
                                  mounts=extra_mounts or None)
    onboard.setup_space(report, dst, "project", values, [])
    if not offline:
        # every ancestor mounted, flat: the brain and a sub-team's division, read-only
        wanted = [{"name": "brain", "repo": mount.get("repo"), "path": "brain"}] + extra_mounts
        for m in wanted:
            if m.get("repo") and not (dst / m["path"] / "space.yaml").exists():
                onboard.run_cmd(["git", "submodule", "add", "-b", "main", m["repo"], m["path"]], dry_run, cwd=dst)
            report.add(f"mount {m['name']}", dry_run or (dst / m["path"] / "space.yaml").exists()
                       or not m.get("repo"), f"test -f {dst / m['path'] / 'space.yaml'}",
                       "mounted read-only" if m.get("repo") else "no repository known for this mount")
    phases["fill"] = True
    target = dst / "knowledge" / kind / subject
    copied = 0
    for src in sorted(seed_dir.rglob("*.md")) if seed_dir.is_dir() else []:
        out = target / src.relative_to(seed_dir)
        if not out.exists():
            draft_page(src, out, now)
            copied += 1
    report.add("copy seed", seed_dir.is_dir(), f"ls {target}",
               f"{copied} pages drafted" if seed_dir.is_dir() else f"seed {seed} not found")
    phases["copy"] = phases.get("copy", 0) + copied if isinstance(phases.get("copy"), int) else copied
    reg = registry.Registry(root, space)
    local, items = reg.subjects.get(subject), []
    match, link = link_items(name)
    items += link
    if local is not None:
        local.home = name
        if local.kind != "projects" and "projects" in space.all_kinds:
            local.kind = "projects"   # a won deal or a seed with a crew is a project now; the librarian moves its folders
        for key, value in match.items():
            local.match.setdefault(key, value)
        reg.save()
        result = f"home: {name} set on {subject}"
    elif reg.get(subject) is not None and name in (reg.home_of(subject),
                                                    (classify.effective(reg, subject) or {}).get("home")):
        result = f"{subject}'s classification already names home: {name}"
    elif reg.get(subject) is not None:
        result = f"{subject} lives in an ancestor's registry; its owner sets home: {name}"
        items.append({"id": f"mint-{name}-home", "kind": "question",
                      "text": f"set home: {name} on {subject} in the parent's registry",
                      "recommendation": "yes", "default": "the subject's conclusions keep landing in the parent",
                      "deadline": None, "depends": 2, "payload": f"_meta/state/mint-{name}.yaml", "fingerprint": ""})
    else:
        reg.add({"id": subject, "kind": kind, "title": onboard.humanize(subject), "home": name,
                 "match": match}, by="mint", now=now)
        reg.save()
        result = f"{subject} added with home: {name}"
    report.add("registry home", True, f"python _meta/tools/registry.py . find {subject}", result)
    phases["registry"] = result
    onboard.scan_codebases(report, root, now, offline)   # the new subject claims its codebases
    items.append({"id": f"mint-{name}-team", "kind": "question",
                  "text": f"create team {name}-crew with {', '.join(crew) or 'nobody yet'}",
                  "recommendation": "yes", "default": "the crew is added by hand later", "deadline": None,
                  "depends": 1, "payload": f"_meta/state/mint-{name}.yaml", "fingerprint": ""})
    needs = root / "_inbound" / "needs-you" / "mint.yaml"
    existing = (onboard.read_yaml(needs, {}) or {}).get("items") or []
    ids = {i["id"] for i in items}
    onboard.write_yaml(needs, {"items": [i for i in existing if i.get("id") not in ids] + items})
    report.add(f"platform team {name}-crew", False, f"gh api orgs/{org or '<org>'}/teams/{name}-crew",
               "proposed on Needs you; the owner creates it", needs_owner=True)
    phases["needs_you"] = True
    if online:
        onboard.commit_fill(report, dst, True, dry_run)
    # the fill has landed: every crew member's workspace mounts the project, read-only
    mount_in_crew(report, dst, name, f"https://github.com/{repo}" if online and repo else None,
                  crew_of(spaceyaml.load(dst)), near=(root,), home=home, online=online, dry_run=dry_run)
    onboard.finish_space(report, dst)
    onboard.register_sync(report, dst, spaceyaml.load(dst).name, 15, not online, dry_run)
    report.spaces[name] = dst
    receipt()
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--channel", metavar="OTHER_ROOT",
                    help="make the corridor between this space and another (both mount it)")
    ap.add_argument("--mount", nargs=2, metavar=("NAME", "URL"),
                    help="mount another space read-only at mounts/NAME (a crew project, a corridor)")
    ap.add_argument("--seed")
    ap.add_argument("--name")
    ap.add_argument("--parent", help="the parent space (a division) for a sub-team")
    ap.add_argument("--vault", action="store_true", help="a vault: --name and --audience, no seed")
    ap.add_argument("--audience", nargs="*", default=[], help="a vault's readers, by name")
    ap.add_argument("--crew", nargs="*", default=[])
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--org")
    ap.add_argument("--home")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if not args.mount:   # every other verb registers a sync task: a prefix that is not ours stops it before it makes a space
        try:
            onboard.task_prefix()
        except ValueError as exc:
            print(f"mint: {exc}")
            return 1
    if args.mount:
        name, url = args.mount
        added = mount_space(args.root, name, url, online=not args.offline, dry_run=args.dry_run)
        verb = ("would mount at" if args.dry_run else "mounted at") if added else "already mounted at"
        print(f"{name}: {verb} mounts/{name}")
        return 0
    if args.channel:
        report = channel(args.root, args.channel, online=not args.offline, org=args.org, home=args.home,
                         dry_run=args.dry_run, name=args.name)
        onboard.print_steps(report.steps)
        return 1 if report.failed() else 0
    if args.vault:
        if not args.name or not args.audience:
            ap.error("a vault needs --name and --audience")
        report = vault(args.root, args.name, args.audience, online=not args.offline, org=args.org,
                       home=args.home, dry_run=args.dry_run)
        onboard.print_steps(report.steps)
        return 1 if report.failed() else 0
    if not args.seed or not args.name:
        ap.error("give --seed and --name, or --channel, or --vault")
    report = run(args.root, args.seed, args.name, args.crew, not args.offline, org=args.org,
                 home=args.home, dry_run=args.dry_run, parent=args.parent)
    onboard.print_steps(report.steps)
    return 1 if report.failed() else 0


if __name__ == "__main__":
    sys.exit(main())
