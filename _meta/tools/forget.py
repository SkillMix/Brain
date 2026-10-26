"""Forgetting: one listing by id finds everything about a subject (vision section 19).

    python forget.py [root] list <subject id> [--json]

Lists every file in this space that is about the subject: its registry entry, its hub and
signed pages (`knowledge/<kind>/<id>/`), its status page, its desk folders, the witness pages
and candidates that carry it as their subject or mention it, the queued, held and landed
items (a doubt item that names it as a candidate, a signal about it), the question-log
lines, the sign-question ledger's lines (its questions and every event on them), and the raw
feed lines that carry one of its identifiers. It changes nothing. What happens to the listing
is a person's decision: raw personal records live only in personal spaces and go at offboard
(`offboard.py --forget-records`, the person's own command); shared spaces keep scrubbed, signed
conclusions under a tombstone; git keeps history, so a true erasure is a history rewrite only
the law demands.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import registry as registry_mod  # noqa: E402
import spaceyaml  # noqa: E402

PAGE_ZONES = ("knowledge", "live", "_inbound", "_outbound")
ITEM_ZONES = ("_inbound", "_outbound")   # signals and other queued items are YAML, not pages


def _about(fm: dict, stem: str, sid: str) -> bool:
    """A page or item is about the subject when it carries it as its subject, mentions it,
    names it as one of the candidates a doubt item could belong to, or is named after it."""
    named = [str(m) for key in ("mentions", "candidates") if isinstance(fm.get(key), list) for m in fm[key]]
    return str(fm.get("subject") or "") == sid or sid in named or stem == sid


def listing(root, sid: str) -> dict:
    root = Path(root)
    space = spaceyaml.load(root)
    reg = registry_mod.Registry(root, space)
    subject = reg.get(sid)
    out = {"subject": sid, "registry": [], "folders": [], "pages": [], "questions": [], "feeds": []}
    if subject is not None:
        out["registry"].append(f"{registry_mod.REGISTRY_PATH.as_posix()}: {sid} ({subject.kind}, {subject.state})")
        for zone in ("knowledge", "work"):
            folder = root / zone / subject.kind / sid
            if folder.is_dir():
                out["folders"].append(folder.relative_to(root).as_posix() + "/")
    for zone in PAGE_ZONES:
        base = root / zone
        for path in sorted(base.rglob("*.md")) if base.is_dir() else []:
            try:
                fm, _ = pages.read(path)
            except Exception:
                continue
            if _about(fm, path.stem, sid):
                out["pages"].append(path.relative_to(root).as_posix())
    for zone in ITEM_ZONES:
        base = root / zone
        for path in sorted(base.rglob("*.yaml")) if base.is_dir() else []:
            try:
                item = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(item, dict) and _about(item, path.stem, sid):
                out["pages"].append(path.relative_to(root).as_posix())
    log = root / "_meta" / "questions.log"
    if log.exists():
        for n, line in enumerate(log.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            try:
                if json.loads(line).get("subject") == sid:
                    out["questions"].append(f"_meta/questions.log:{n}")
            except ValueError:
                continue
    ledger = root / "_meta" / "state" / "sign-questions.jsonl"   # sign_questions.LEDGER
    if ledger.exists():
        rows = []
        # split on "\n" only, as sign_questions reads it: a JSON string may hold U+2028
        for n, line in enumerate(ledger.read_text(encoding="utf-8", errors="replace").split("\n"), 1):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append((n, row))
        # a question's later events name it by id; its `asked` line (and a `checked` one) the subject
        asked = {str(row.get("id")) for _, row in rows if row.get("event") == "asked" and row.get("subject") == sid}
        for n, row in rows:
            if row.get("subject") == sid or str(row.get("id") or "") in asked:
                out["questions"].append(f"_meta/state/sign-questions.jsonl:{n}")
    needles = []
    if subject is not None:
        match = subject.match if isinstance(subject.match, dict) else {}
        needles = [str(v) for v in match.values() if v] + [subject.title]
    feed = root / "_meta" / "state" / "feed"
    for path in sorted(feed.glob("*.jsonl")) if feed.is_dir() and needles else []:
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if any(needle and needle in line for needle in needles):
                out["feeds"].append(f"{path.relative_to(root).as_posix()}:{n}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lst = sub.add_parser("list")
    lst.add_argument("subject")
    lst.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    found = listing(args.root, args.subject)
    if args.json:
        print(json.dumps(found, indent=2))
        return 0
    for key in ("registry", "folders", "pages", "questions", "feeds"):
        for row in found[key]:
            print(f"{key}: {row}")
    if not any(found[k] for k in ("registry", "folders", "pages", "questions", "feeds")):
        print(f"nothing here is about {args.subject}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
