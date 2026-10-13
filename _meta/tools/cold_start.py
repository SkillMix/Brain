"""The cold start of a company with history (vision section 15): research first, then the
archives as evidence; questions confirm, never open; nothing is signed or copied as signed.

`research.py` lands what an agent read on the public web as witness pages and confirmations
first. This tool then reads a confirmations file an agent drafted from the archives and writes,
into this space, as drafts:

- one question per confirmation, `_inbound/questions/cold-start-<id>.md`: "I think X, based
  on Y. Right?", with a recommendation, its batch and how much depends on it; batches over
  25 are split, the most depended-on first;
- one candidate per active subject the confirmation names (`candidate:
  knowledge/<kind>/<id>/` or `knowledge/<kind>/<all>/`), or an identity page
  (`identity/<name>.md`), whose body quotes the archived page as unsigned evidence with an
  `archive:<label>/<path>` pointer.

Re-runnable: a file that exists is kept.

    python cold_start.py [root] --confirmations FILE [--archive PATH] [--label NAME] [--dry-run]

The confirmations file: `batches: [{name, depends, confirmations: [{id, think, based_on,
candidate}]}]`; `based_on` may name archive pages as `archive/<path>.md`.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ids  # noqa: E402
import pages  # noqa: E402
import registry as registry_mod  # noqa: E402
import spaceyaml  # noqa: E402
import validate  # noqa: E402

ARCHIVE_REF = re.compile(r"archive/[A-Za-z0-9_./-]+\.md")
BATCH_CAP = 25
MAX_LINES = 80
TYPE_BY_KIND = {"topics": "concept", "domains": "entity"}


def archive_text(archive: Path | None, ref: str) -> str:
    if archive is None:
        return ""
    path = archive / ref.removeprefix("archive/")
    if not path.exists():
        return ""
    _, body = pages.split(path.read_text(encoding="utf-8", errors="replace"))
    lines = body.strip().splitlines()
    cut = lines[:MAX_LINES] + ([f"... ({len(lines) - MAX_LINES} more lines in the archive)"]
                               if len(lines) > MAX_LINES else [])
    return "\n".join(cut).strip() + "\n"


def targets(conf: dict, reg) -> list:
    """(candidate relpath, extra frontmatter, title) for one confirmation."""
    target = str(conf.get("candidate") or "none")
    if target.startswith("identity/"):
        stem = Path(target).stem
        return [(f"_inbound/candidates/cold-start-{stem}.md",
                 {"type": "concept", "kind": "identity", "target": target}, stem.replace("-", " ").title())]
    match = re.match(r"knowledge/([a-z0-9-]+)/([^/]+)/?", target)
    if not match:
        return []
    kind, slug = match.group(1), match.group(2)
    # sign only what is active and important now: an active subject that is important or
    # stateful (an open deal, a running project); the rest stays evidence
    subjects = [s for s in reg.all() if s.kind == kind and s.state == "active"
                and (s.important or s.stateful or slug not in ("<all>", "*"))
                and (slug in ("<all>", "*") or s.id == slug)]
    return [(f"_inbound/candidates/cold-start-{s.id}.md",
             {"type": TYPE_BY_KIND.get(kind, "entity"), "kind": kind, "subject": s.id,
              "target": f"knowledge/{kind}/{s.id}/{s.id}.md"}, s.title or s.id) for s in subjects]


def batches_of(data: dict) -> list:
    """Batches ordered by how much depends on them, none over the cap."""
    out = []
    for batch in sorted(data.get("batches") or [], key=lambda b: -int(b.get("depends", 1))):
        items = list(batch.get("confirmations") or [])
        for n in range(0, len(items), BATCH_CAP):
            part = dict(batch, confirmations=items[n:n + BATCH_CAP])
            if len(items) > BATCH_CAP:
                part["name"] = f"{batch.get('name')} ({n // BATCH_CAP + 1})"
            out.append(part)
    return out


def run(root, confirmations, archive=None, label: str = "archive", dry_run: bool = False) -> dict:
    root = Path(root)
    space = spaceyaml.load(root)
    reg = registry_mod.Registry(root, space)
    now = space.now()
    data = yaml.safe_load(Path(confirmations).read_text(encoding="utf-8")) or {}
    archive = Path(archive) if archive else None
    written, kept = [], []

    def put(rel: str, fm: dict, body: str) -> None:
        if (root / rel).exists():
            kept.append(rel)
            return
        written.append(rel)
        if not dry_run:
            pages.write(root / rel, fm, body)

    for batch in batches_of(data):
        depends = int(batch.get("depends", 1))
        for conf in batch["confirmations"]:
            think, based = str(conf["think"]).strip(), str(conf.get("based_on") or "").strip()
            refs = ARCHIVE_REF.findall(based)
            evidence = [f"archive:{label}/{r.removeprefix('archive/')}" for r in refs]
            planned = targets(conf, reg)
            put(f"_inbound/questions/cold-start-{conf['id']}.md",
                {"id": pages.new_id(), "type": "review", "title": f"Confirm: {conf['id']}", "status": "draft",
                 "generated": pages.generated("cold-start", now), "question": f"I think: {think} Based on {based}. Right?",
                 "recommendation": "yes", "default": "stays a draft until you answer", "depends": depends,
                 "batch": batch.get("name"), "payload": planned[0][0] if planned else "_meta/subjects.yaml",
                 "evidence": evidence},
                f"# Confirm: {conf['id']}\n\n{think}\n\nBased on: {based}\n")
            for rel, extra, title in planned:
                body = [f"# {title}", "", "## Proposed", think, "", "## Evidence (archive, unsigned)", f"- {based}"]
                for ref in refs:
                    text = archive_text(archive, ref)
                    if text:
                        body += ["", f"### From the archive: {ref}", text.rstrip()]
                fm = {"id": pages.new_id(), "type": extra.pop("type"), "title": title, "status": "draft",
                      "generated": pages.generated("cold-start", now), "evidence": evidence,
                      "recommendation": "yes", "depends": depends}
                fm.update(extra)
                put(rel, fm, "\n".join(body) + "\n")
    errs = []
    if written and not dry_run:
        ids.build(root)
        errs = [e for e in validate.validate(root) if not e.startswith("warning:")]
    return {"written": written, "kept": kept, "errors": errs}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--confirmations", required=True)
    ap.add_argument("--archive")
    ap.add_argument("--label", default="archive")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    out = run(args.root, args.confirmations, args.archive, args.label, args.dry_run)
    print(f"cold start: {len(out['written'])} written, {len(out['kept'])} kept, {len(out['errors'])} errors")
    for err in out["errors"][:20]:
        print("  " + err)
    return 1 if out["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
