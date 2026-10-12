"""Research first (vision section 15): what an agent read on the public web, as evidence.

Before any archive is read, an agent reads the company's own public sources (its site, the
business registry, its code organization; never a person's private profiles compiled across
sources) and exports what it read as records, one JSON object per line:

    {"id": "web-site-about", "url": "https://...", "title": "About us", "at": "<ISO>",
     "about": "identity" | "people" | "registry" | "code",
     "text": "the passage it read", "claims": [{"think": "...", "candidate": "identity/spine.md",
                                                "depends": 3}]}

`ingest` writes one witness page per record (`source: web`, the URL, audience everyone: it
is public) and one confirmation question per claim, "I think X, based on <title> (<url>).
Right?", batched as "public web" ahead of the archives' batches. Nothing is signed.

    python research.py [root] ingest <records.jsonl>
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

ABOUT = ("identity", "people", "registry", "code")
SAFE = re.compile(r"[^a-z0-9-]+")


def _slug(text: str) -> str:
    return SAFE.sub("-", str(text).lower()).strip("-")[:60] or "record"


def ingest(root, records_path, dry_run: bool = False) -> dict:
    root = Path(root)
    space = spaceyaml.load(root)
    now = space.now()
    written, kept, skipped = [], [], []
    for line in Path(records_path).read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or not rec.get("url") or not rec.get("id"):
            continue
        if rec.get("about") not in ABOUT:
            skipped.append(f"{rec.get('id')}: about must be one of {', '.join(ABOUT)}")
            continue
        at = pages.parse_iso(rec.get("at") or pages.iso(now))
        rid = _slug(rec["id"])
        witness = root / "knowledge" / "sources" / f"{at:%Y}" / f"{at:%m}" / f"web-{rid}.md"
        if witness.exists():
            kept.append(witness.relative_to(root).as_posix())
        else:
            fm = {"id": pages.new_id(), "type": "source", "title": str(rec.get("title") or rec["url"])[:120],
                  "status": "draft", "generated": pages.generated("research", now), "record": f"web-{rid}",
                  "source": "web", "kind": "document", "at": pages.iso(at), "url": rec["url"],
                  "about": rec["about"], "mentions": [], "audience": "everyone", "identifiers": {}}
            if not dry_run:
                pages.write(witness, fm, f"{str(rec.get('text') or '').strip()}\n\nSource: {rec['url']}\n")
            written.append(witness.relative_to(root).as_posix())
        for n, claim in enumerate(rec.get("claims") or [], 1):
            think = str(claim.get("think") or "").strip()
            if not think:
                continue
            rel = f"_inbound/questions/research-{rid}-{n}.md"
            if (root / rel).exists():
                kept.append(rel)
                continue
            based = f"{rec.get('title') or 'a public page'} ({rec['url']})"
            fm = {"id": pages.new_id(), "type": "review", "title": f"Confirm: {rec.get('title') or rid}",
                  "status": "draft", "generated": pages.generated("research", now),
                  "question": f"I think: {think} Based on {based}. Right?", "recommendation": "yes",
                  "default": "stays a draft until you answer", "depends": int(claim.get("depends", 2)),
                  "batch": "public web", "payload": str(claim.get("candidate") or "_meta/subjects.yaml"),
                  "evidence": [rec["url"]]}
            if not dry_run:
                pages.write(root / rel, fm, f"# Confirm\n\n{think}\n\nBased on: {based}\n")
            written.append(rel)
    return {"written": written, "kept": kept, "skipped": skipped}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(".")
    if argv and argv[0] != "ingest":
        root = Path(argv.pop(0))
    if argv[:1] != ["ingest"] or len(argv) < 2:
        print(__doc__)
        return 2
    out = ingest(root, argv[1], dry_run="--dry-run" in argv)
    print(f"research: {len(out['written'])} written, {len(out['kept'])} kept, {len(out['skipped'])} skipped")
    for note in out["skipped"]:
        print("  skipped " + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
