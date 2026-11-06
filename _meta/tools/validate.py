"""The shape-1 validator: is this space what it claims to be?

Checks `space.yaml` against the schema and the profile table, every page's
frontmatter, permanent ids and id links, signatures on stable pages, the kinds
under knowledge/ and work/, and the registry. Warnings never fail the run;
errors do. Mounts that are absent on disk produce warnings, not errors, because
CI often has no submodules.

What a pull request can plant is checked too, so the required check stops it before it
merges:

- a stable page whose `signed_fields` no longer match its title, type, subject, kind,
  high_stakes or signer (`sign.fields_problem`);
- a candidate whose `target` (or, for a signed release, the path derived from its kind,
  subject and id) is not a page file under `knowledge/` built from safe names, or reaches a
  path the gate guards; a release whose ids, names or sender are malformed
  (`landing.malformed`);
- an entry of `_meta/state/ids.yaml` that is not a page id pointing at a page file under the
  page roots inside the space (`ids.entry_problem`);
- a stable page outside `knowledge/` and `identity/` (under `work/`, `journal/` or
  `outbox/`), which only `sign` writes and only there: a warning, since a desk page can carry
  a stale stamp (mint drops it) and grounding must count it as working, not signed;
- a top-level folder spelled like a zone in another case (`Knowledge/`), and a page file
  whose suffix is `.md` in another case (`x.MD`): Windows and macOS merge them with the zone,
  where nothing else checked them;
- a symbolic link git tracks anywhere (`git ls-files -s`, mode 120000), and a page file that is
  a link: the repository carries none, since a link points the next write or read anywhere.

    python validate.py [root] [--template]
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import registry as registry_mod  # noqa: E402
import spaceyaml  # noqa: E402

try:  # the engine file's rules live with the search tool
    import search  # noqa: E402
except ImportError:  # pragma: no cover
    search = None

PAGE_ROOTS = ("knowledge", "live", "identity", "_inbound/candidates", "_inbound/checks")
LINK_ROOTS = PAGE_ROOTS + ("index.md", "work", "journal", "docs")
IDS_PATH = Path("_meta") / "state" / "ids.yaml"
# where agents and people write freely: a page there is never signed
DESK_ZONES = ("work", "journal", "outbox")
# the top-level folders a tool reads by name: a case variant of one is merged with it on
# Windows and macOS, out of sight of every check that compares names exactly
ZONES = ("knowledge", "live", "identity", "_inbound", "_outbound", "_meta", "work", "journal",
         "outbox", "personal", "academic", "docs")


def _ids_of(root: Path) -> dict:
    """id -> relative path for a space: the generated index when present, else a scan."""
    index = root / IDS_PATH
    if index.exists():
        data = yaml.safe_load(index.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in (data.get("ids") or data).items()
                    if isinstance(v, str)}
    found = {}
    for sub in PAGE_ROOTS:
        base = root / sub
        if not base.exists():
            continue
        for path in base.rglob("*.md"):
            if not pages.is_page_path(path):
                continue
            try:
                fm, _ = pages.read(path)
            except Exception:
                continue
            pid = fm.get("id")
            if pid:
                found.setdefault(str(pid), path.relative_to(root).as_posix())
    return found


def _candidate_problems(root: Path, path: Path, fm: dict, space: spaceyaml.Space) -> list:
    """What in a candidate would make sign or landing write outside the pages: a malformed
    release, a target that is no page file under `knowledge/` or one the gate guards, a kind
    this space does not have. The same rules sign.dest_of and landing.malformed apply, minus
    the links, which a checkout in CI does not have."""
    import landing
    import sign
    out = []
    released = bool(fm.get("released_from"))
    if released:
        problem = landing.malformed(fm, path.stem)
        if problem:
            return [f"malformed release: {problem}"]
    record = not landing.claims_signature(fm) and str(fm.get("type")) == "source"
    if record or not (fm.get("target") or released):   # a record is filed by its id, never a target
        return out
    try:
        rel = sign.check_target(sign.target_of(fm, path, space.now()), record=False)
    except pages.UnsafePath as exc:
        return [f"bad target: {exc}"]
    reason = pages.guarded_reason(rel)
    if reason:
        out.append(f"bad target: {ascii(rel)[:90]} {reason}")
    kind = rel.split("/")[1]
    if kind not in space.all_kinds:
        message = f"bad target: {ascii(rel)[:90]}: {ascii(kind)} is not one of the kinds"
        kinds_known = space.profile in ("brain", spaceyaml.TEMPLATE_PROFILE) or space.brain_space() is not None
        out.append(message if kinds_known else f"warning: {message}; the brain mount is absent")
    return out


def _check_page(root: Path, path: Path, space: spaceyaml.Space, seen_ids: dict, errs: list) -> str:
    rel = path.relative_to(root).as_posix()
    if pages.is_link(path):   # never read through one: what it points at is not this space's page
        errs.append(f"{rel}: a symbolic link; the repository carries none")
        return ""
    try:
        fm, body = pages.read(path)
    except Exception as exc:  # unreadable YAML
        errs.append(f"{rel}: frontmatter does not parse ({exc})")
        return ""
    if not fm:
        errs.append(f"{rel}: missing frontmatter")
        return ""
    for key in pages.REQUIRED:
        if not fm.get(key):
            errs.append(f"{rel}: missing required field `{key}`")
    pid = str(fm.get("id") or "")
    if pid:
        if not pages.ID_RE.match(pid):
            errs.append(f"{rel}: id `{pid}` is not `p-` plus 8 hex")
        elif pid in seen_ids:
            errs.append(f"{rel}: id `{pid}` already used by {seen_ids[pid]}")
        else:
            seen_ids[pid] = rel
    ptype = str(fm.get("type") or "")
    if ptype and ptype not in pages.PAGE_TYPES:
        errs.append(f"{rel}: type `{ptype}` is not one of {', '.join(pages.PAGE_TYPES)}")
    status = str(fm.get("status") or "")
    if status and status not in pages.STATUSES:
        errs.append(f"{rel}: status `{status}` is not one of {', '.join(pages.STATUSES)}")
    gen = fm.get("generated")
    if gen is not None and not (isinstance(gen, dict) and gen.get("by") and gen.get("at")):
        errs.append(f"{rel}: generated must be {{by, at}}")
    if rel.startswith("knowledge/sources/"):
        if ptype != "source":
            errs.append(f"{rel}: witness pages are `type: source`")
        if status == "stable":
            errs.append(f"{rel}: witness pages are never signed")
    if rel.startswith("live/") and status == "stable":
        errs.append(f"{rel}: status pages are operational state, never signed")
    if ptype == "process":
        import procedure  # the format of a procedure page lives with the tool that runs its checks
        for problem in procedure.problems(fm, body):
            errs.append(f"{rel}: {problem}" if status == "stable" else f"warning: {rel}: {problem}")
    if status == "stable":
        import sign   # the signed fields are sign's own rule
        for key in pages.SIGNATURE_FIELDS:
            if not fm.get(key):
                errs.append(f"{rel}: stable page missing signature field `{key}`")
        if fm.get("fingerprint") and fm["fingerprint"] != pages.fingerprint(body):
            errs.append(f"{rel}: fingerprint does not match the body (edited after signing?)")
        problem = sign.fields_problem(fm)
        if problem:
            errs.append(f"{rel}: {problem}")
    if rel.startswith("_inbound/candidates/"):
        for problem in _candidate_problems(root, path, fm, space):
            errs.append(f"warning: {rel}: {problem[len('warning: '):]}" if problem.startswith("warning: ")
                        else f"{rel}: {problem}")
    if rel.startswith("knowledge/") and not rel.startswith("knowledge/sources/"):
        parts = rel.split("/")
        if len(parts) >= 2 and parts[1] not in space.all_kinds and parts[1] != "index.md":
            pass  # reported once per folder below
    return body


def tracked_links(root: Path) -> list:
    """An error for each symbolic link git tracks here (mode 120000), whatever its zone (outbox/
    included): a link points a tool's next write or read anywhere, and the space carries none.
    Empty when git is missing or this folder is no repository."""
    import subprocess
    try:
        done = subprocess.run(["git", "-C", str(root), "ls-files", "-s", "-z"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
    except OSError:
        return []
    if done.returncode != 0:
        return []
    errs = []
    for entry in done.stdout.split("\0"):
        meta, _, rel = entry.partition("\t")
        if meta.split(" ", 1)[0] == "120000":
            errs.append(f"{rel}: a symbolic link; the repository carries none")
    return errs


def _planted(root: Path) -> list:
    """What a pull request could plant beside the pages: a forged index entry, a signed page on
    the desk, a zone or a page suffix in another case, a tracked symbolic link."""
    import ids
    errs = tracked_links(root)
    for pid, rel in sorted((ids.committed(root) or {}).items(), key=lambda kv: str(kv[0])):
        problem = ids.entry_problem(root, pid, rel)
        if problem:
            errs.append(f"{IDS_PATH.as_posix()}: entry {ascii(pid)[:60]}: {problem}")
    for zone in DESK_ZONES:
        base = root / zone
        for path in sorted(base.rglob("*")) if base.is_dir() else []:
            if path.suffix.lower() != ".md" or not path.is_file():
                continue
            try:
                fm, _ = pages.read(path)
            except Exception:
                continue
            if str(fm.get("status") or "") == "stable":   # a warning: mint drops such a stamp
                errs.append(f"warning: {path.relative_to(root).as_posix()}: a page under {zone}/ is never "
                            "signed; only sign writes `status: stable`, under knowledge/ or identity/, "
                            "so this one counts as working, not signed")
    for child in sorted(root.iterdir()):
        twin = next((z for z in ZONES if child.name != z and child.name.casefold() == z.casefold()), None)
        if twin and child.is_dir():
            errs.append(f"{child.name}/: spelled like `{twin}/` in another case; Windows and macOS merge them")
    for sub in PAGE_ROOTS:
        base = root / sub
        for path in sorted(base.rglob("*")) if base.is_dir() else []:
            if path.suffix.lower() == ".md" and path.suffix != ".md" and path.is_file():
                errs.append(f"{path.relative_to(root).as_posix()}: a page file ends in `.md`, in lower case")
    return errs


def validate(root, template: bool = False) -> list:
    root = Path(root).resolve()
    errs: list = []
    try:
        space = spaceyaml.load(root)
    except Exception as exc:
        return [f"space.yaml: {exc}"]
    errs.extend(f"space.yaml: {e}" for e in space.errors())
    profile = space.profile if space.profile in spaceyaml.PROFILE_TABLE else None
    if template and not space.is_template():
        errs.append("space.yaml: the template repository must keep `profile: template`")
    if profile:
        table = spaceyaml.PROFILE_TABLE[profile]
        for entry in table["required"]:
            if not (root / entry).exists():
                errs.append(f"profile {profile}: missing `{entry}`")
        for entry in table["forbidden"]:
            if (root / entry).exists():
                errs.append(f"profile {profile}: `{entry}/` does not belong in this profile")
    # audience and parent: who reads this space, and which space it hangs under
    if space.profile == "brain" and space.audience != "everyone":
        errs.append("space.yaml: the brain's audience is `everyone`")
    if space.profile == "personal" and isinstance(space.audience, list) and space.owner \
            and space.owner not in space.audience:
        errs.append("space.yaml: a personal workspace's audience names its owner")
    if space.profile not in ("brain", spaceyaml.TEMPLATE_PROFILE, "", None) and space.parent:
        names = {m.get("name") for m in space.mounts}
        mounted = {spaceyaml.load(r).name for r in space.ancestor_roots()} if space.ancestor_roots() else set()
        if space.parent not in names | mounted | {"brain"}:
            errs.append(f"warning: parent `{space.parent}` is not mounted here; a space mounts every ancestor")
    # mounts present?
    for mount in space.mounts:
        if space.mount_root(mount.get("name", "")) is None:
            errs.append(f"warning: mount `{mount.get('name')}` is not present on disk; "
                        "cross-space checks skipped")
    if space.profile not in ("brain", spaceyaml.TEMPLATE_PROFILE) and space.brain_space() is None:
        errs.append("warning: no brain mount resolvable; kinds fall back to the defaults")
    # kinds folders (a warning only when the company's kinds cannot be read: no brain mount)
    kinds_known = space.profile in ("brain", spaceyaml.TEMPLATE_PROFILE) or space.brain_space() is not None
    for zone in ("knowledge", "work"):
        base = root / zone
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if child.is_dir() and child.name not in space.all_kinds:
                message = f"{zone}/{child.name}/: not one of the kinds ({', '.join(space.all_kinds)})"
                errs.append(message if kinds_known else f"warning: {message}; the brain mount is absent")
    # windows: a per-page `review_every` is a number of days; a stable page past its window
    # is named as a warning (the checker's weekly batch asks about it)
    now = space.now()
    for zone in ("knowledge",):
        base = root / zone
        for path in sorted(base.rglob("*.md")) if base.is_dir() else []:
            if not pages.is_page_path(path) or path.name == "index.md":
                continue
            try:
                pfm, _ = pages.read(path)
            except Exception:
                continue
            every = pfm.get("review_every")
            if every is not None and (isinstance(every, bool) or not isinstance(every, int) or every <= 0):
                errs.append(f"{path.relative_to(root).as_posix()}: review_every is a whole number of days")
                continue
            if str(pfm.get("status")) != "stable":
                continue
            days = every or space.windows.get(str(pfm.get("type") or ""))
            seen = pfm.get("verified_at") or pfm.get("signed_at")
            try:
                if days and seen and (now - pages.parse_iso(seen)).days > int(days):
                    errs.append(f"warning: {path.relative_to(root).as_posix()}: past its {days}-day window")
            except (TypeError, ValueError):
                continue
    # pages
    seen_ids: dict = {}
    bodies: dict = {}
    for sub in PAGE_ROOTS:
        base = root / sub
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.md")):
            if not pages.is_page_path(path):
                continue
            bodies[path] = _check_page(root, path, space, seen_ids, errs)
    errs.extend(_planted(root))
    # id links: this space and every mount on disk. While a mount is absent, a link found
    # nowhere may point into it, so it is a warning that names the absent mounts, not an error.
    known = dict(_ids_of(root))
    for mount_root in space.ancestor_roots():
        for pid, rel in _ids_of(mount_root).items():
            known.setdefault(pid, rel)
    absent = [m.get("name", "") for m in space.mounts if space.mount_root(m.get("name", "")) is None]
    link_files = list(bodies.items())
    for sub in ("index.md", "work", "journal", "docs"):
        base = root / sub
        if base.is_file():
            link_files.append((base, base.read_text(encoding="utf-8", errors="replace")))
        elif base.is_dir():
            for path in base.rglob("*.md"):
                link_files.append((path, path.read_text(encoding="utf-8", errors="replace")))
    for path, text in link_files:
        for pid in pages.id_links(text or ""):
            if pid not in known:
                message = f"{path.relative_to(root).as_posix()}: link to unknown id `{pid}`"
                errs.append(f"warning: {message}; it may be in a mount not on disk "
                            f"({', '.join(f'`{n}`' for n in absent)})" if absent else message)
    # the engine file (an extra stays off until the probe measured a gain)
    if search is not None:
        errs.extend(search.errors(root))
    # registry
    try:
        errs.extend(registry_mod.Registry(root, space).check())
    except Exception as exc:
        errs.append(f"_meta/subjects.yaml: {exc}")
    # template-only: no company names
    if template:
        try:
            import no_company_names  # noqa: E402
            for hit in no_company_names.check(root):
                errs.append(f"{hit[0]}:{hit[1]}: a forbidden name appears here")
        except ImportError:
            errs.append("warning: no_company_names.py not present; name check skipped")
    return errs


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    template = "--template" in argv
    roots = [a for a in argv if not a.startswith("--")] or ["."]
    failed = False
    for root in roots:
        problems = validate(root, template=template)
        for problem in problems:
            print(problem)
        errors = [p for p in problems if not p.startswith("warning:")]
        print(f"{root}: {len(errors)} errors, {len(problems) - len(errors)} warnings")
        failed = failed or bool(errors)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
