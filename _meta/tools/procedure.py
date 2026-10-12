"""Procedures as pages: steps any agent runs, checks any agent applies (vision section 5).

A `process` page is written as `## Steps` (what to do, in order) and `## Checks` (what must
hold of the outcome). A check line may carry a machine form in backticks, which this tool
applies to an outcome file:

    ## Checks
    - Never include margins in a client document. `never: margin`
    - The crew sends; agents never send. `always: crew sends`

`never: <term>` fails when the term appears (whole words, any case); `always: <term>` fails
when it does not. A failed check is evidence against the procedure: a witness page under
`knowledge/sources/` attached to the procedure's subject, naming the failed checks and the
outcome file (never its content), so the checker and distill see it like any evidence.

    python procedure.py [root] check <page> <outcome file>
    python procedure.py [root] lint <page>         are the two sections there?
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

SECTION = re.compile(r"^##\s+(.+?)\s*$", re.M)
MACHINE = re.compile(r"`\s*(never|always)\s*:\s*([^`]+?)\s*`", re.I)


@dataclass
class Result:
    checks: list = field(default_factory=list)     # (kind, term, ok)
    failed: list = field(default_factory=list)
    evidence: str = ""


def sections(body: str) -> dict:
    """heading (lowercase) -> text under it."""
    out, marks = {}, list(SECTION.finditer(body))
    for n, mark in enumerate(marks):
        end = marks[n + 1].start() if n + 1 < len(marks) else len(body)
        out[mark.group(1).strip().lower()] = body[mark.end():end]
    return out


def problems(fm: dict, body: str) -> list:
    """What a process page lacks: the steps and the checks."""
    if str(fm.get("type") or "") != "process":
        return []
    found = sections(body)
    out = []
    if "steps" not in found or not found["steps"].strip():
        out.append("a process page needs a `## Steps` section")
    if "checks" not in found or not found["checks"].strip():
        out.append("a process page needs a `## Checks` section")
    return out


def machine_checks(body: str) -> list:
    return [(m.group(1).lower(), m.group(2).strip()) for m in MACHINE.finditer(sections(body).get("checks", ""))]


def _present(text: str, term: str) -> bool:
    words = [re.escape(w) for w in term.split()]
    return bool(words) and re.search(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", text, re.I) is not None


def check(root, page, outcome) -> Result:
    root, page, outcome = Path(root), Path(page), Path(outcome)
    page = page if page.is_absolute() else root / page
    space = spaceyaml.load(root)
    fm, body = pages.read(page)
    text = Path(outcome).read_text(encoding="utf-8", errors="replace")
    result = Result()
    for kind, term in machine_checks(body):
        ok = (not _present(text, term)) if kind == "never" else _present(text, term)
        result.checks.append((kind, term, ok))
        if not ok:
            result.failed.append(f"{kind}: {term}")
    if result.failed:
        now = space.now()
        digest = hashlib.sha1(f"{fm.get('id')}|{outcome.name}|{'|'.join(result.failed)}".encode()).hexdigest()[:8]
        rid = f"proc-{digest}"
        lines = "\n".join(f"- failed check: {f}" for f in result.failed)
        wfm = {"id": "p-" + digest, "type": "source", "title": f"Procedure check failed: {fm.get('title')}",
               "status": "draft", "generated": pages.generated("procedure", now), "record": rid,
               "source": "procedure", "kind": "document", "at": pages.iso(now),
               "subject": fm.get("subject"), "mentions": [], "audience": "owner", "identifiers": {},
               "procedure": fm.get("id")}
        target = root / "knowledge" / "sources" / f"{now:%Y}" / f"{now:%m}" / f"{rid}.md"
        pages.write(target, wfm, f"# Procedure check failed\n\nThe outcome `{outcome.name}` did not hold "
                                 f"to [{fm.get('title')}](id:{fm.get('id')}):\n\n{lines}\n")
        result.evidence = target.relative_to(root).as_posix()
    return result


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(".")
    if argv and argv[0] not in ("check", "lint"):
        root = Path(argv.pop(0))
    if argv[:1] == ["lint"] and len(argv) == 2:
        fm, body = pages.read(root / argv[1] if not Path(argv[1]).is_absolute() else argv[1])
        found = problems(fm, body)
        for problem in found:
            print(problem)
        return 1 if found else 0
    if argv[:1] == ["check"] and len(argv) == 3:
        result = check(root, argv[1], argv[2])
        for kind, term, ok in result.checks:
            print(f"{'ok  ' if ok else 'FAIL'} {kind}: {term}")
        if result.evidence:
            print(f"evidence: {result.evidence}")
        print(json.dumps({"checks": len(result.checks), "failed": len(result.failed)}))
        return 1 if result.failed else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
