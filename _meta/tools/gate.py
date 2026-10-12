"""gate: which pull requests land on green and which wait for the owner (rules.md
section 8), plus the signature check on changed pages (hard rule 3).

classify(root, changed_files) -> ("protected" | "auto-mergeable", reasons)
    A change under any `protected:` prefix of space.yaml waits for the owner's one
    click; everything else lands when the checks pass. In the template repository
    itself (`.github/template-marker` present) every pull request is auto-mergeable:
    the template is a code repository, not a space (docs/decisions.md).
check_signatures(root, changed_files) -> list[str]
    Every changed page under knowledge/, live/, identity/ or _inbound/ with
    `status: stable` carries signed_by, signed_at and a fingerprint equal to its
    body; a stable page under knowledge/sources/ or live/ is an error.

    python gate.py <base_ref> [root]

Prints `GATE: <class>`, the reasons, then any signature errors. Exit 1 on
signature errors only; the class alone never fails the run.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

PROTECTED = "protected"
AUTO = "auto-mergeable"
PAGE_ZONES = ("knowledge/", "live/", "identity/", "_inbound/")
NEVER_SIGNED = {"knowledge/sources/": "witness pages are never signed",
                "live/": "status pages are operational state, never signed"}


def _posix(rel: str) -> str:
    rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def _under(rel: str, prefix: str) -> bool:
    prefix = _posix(prefix)
    if prefix.endswith("/"):
        return rel.startswith(prefix)
    return rel == prefix or rel.startswith(prefix + "/")


def is_template(root) -> bool:
    return (Path(root) / spaceyaml.TEMPLATE_MARKER).exists()


def protected_prefixes(root) -> tuple[list[str], str | None]:
    """The space's protected prefixes, and a note when the defaults had to stand in."""
    try:
        return list(spaceyaml.load(root).protected), None
    except Exception as exc:  # no or unreadable space.yaml: the defaults still guard
        return list(spaceyaml.DEFAULTS["protected"]), f"space.yaml unreadable ({exc}); defaults used"


def classify(root, changed_files) -> tuple[str, list[str]]:
    root = Path(root)
    if is_template(root):
        return AUTO, ["template repository: pull requests land on green"]
    prefixes, note = protected_prefixes(root)
    reasons = [note] if note else []
    for raw in changed_files:
        rel = _posix(str(raw))
        for prefix in prefixes:
            if _under(rel, prefix):
                reasons.append(f"{rel}: protected by `{prefix}`")
                break
    if any(": protected by" in r for r in reasons):
        return PROTECTED, reasons
    reasons.append("no protected paths touched" if changed_files else "no changed files")
    return AUTO, reasons


def check_signatures(root, changed_files) -> list[str]:
    root = Path(root)
    errs: list[str] = []
    for raw in changed_files:
        rel = _posix(str(raw))
        path = root / rel
        if not rel.startswith(PAGE_ZONES) or not pages.is_page_path(path) or not path.is_file():
            continue
        try:
            fm, body = pages.read(path)
        except Exception as exc:
            errs.append(f"{rel}: frontmatter does not parse ({exc})")
            continue
        if str(fm.get("status") or "") != "stable":
            continue
        for prefix, why in NEVER_SIGNED.items():
            if rel.startswith(prefix):
                errs.append(f"{rel}: {why}")
        for key in pages.SIGNATURE_FIELDS:
            if not fm.get(key):
                errs.append(f"{rel}: stable page missing signature field `{key}`")
        if fm.get("fingerprint") and fm["fingerprint"] != pages.fingerprint(body):
            errs.append(f"{rel}: fingerprint does not match the body (edited after signing?)")
    return errs


def changed_files(root, base_ref: str) -> list[str]:
    """Paths changed between the merge base with `base_ref` and HEAD."""
    for spec in ([f"{base_ref}...HEAD"], [base_ref, "HEAD"]):
        done = subprocess.run(["git", "diff", "--name-only", "-z", *spec], cwd=str(root),
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if done.returncode == 0:
            return [p for p in done.stdout.split("\0") if p]
    raise RuntimeError(f"git diff against {base_ref} failed: {done.stderr.strip()}")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    base_ref = argv[0]
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    changed = changed_files(root, base_ref)
    cls, reasons = classify(root, changed)
    print(f"GATE: {cls}")
    for reason in reasons:
        print(f"  {reason}")
    errs = check_signatures(root, changed)
    for err in errs:
        print(f"error: {err}")
    print(f"{len(changed)} changed files, {len(errs)} signature errors")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
