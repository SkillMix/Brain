"""The shape-1 validator: is this space what it claims to be?

Checks `space.yaml` against the schema and the profile table, every page's
frontmatter, permanent ids and id links, signatures on stable pages, the kinds
under knowledge/ and work/, and the registry. Warnings never fail the run;
errors do. Mounts that are absent on disk produce warnings, not errors, because
CI often has no submodules.

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


def _check_page(root: Path, path: Path, space: spaceyaml.Space, seen_ids: dict, errs: list) -> str:
    rel = path.relative_to(root).as_posix()
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
        for key in pages.SIGNATURE_FIELDS:
            if not fm.get(key):
                errs.append(f"{rel}: stable page missing signature field `{key}`")
        if fm.get("fingerprint") and fm["fingerprint"] != pages.fingerprint(body):
            errs.append(f"{rel}: fingerprint does not match the body (edited after signing?)")
    if rel.startswith("knowledge/") and not rel.startswith("knowledge/sources/"):
        parts = rel.split("/")
        if len(parts) >= 2 and parts[1] not in space.all_kinds and parts[1] != "index.md":
            pass  # reported once per folder below
    return body


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
    # id links
    known = dict(_ids_of(root))
    for mount_root in space.ancestor_roots():
        for pid, rel in _ids_of(mount_root).items():
            known.setdefault(pid, rel)
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
                errs.append(f"{path.relative_to(root).as_posix()}: link to unknown id `{pid}`")
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
