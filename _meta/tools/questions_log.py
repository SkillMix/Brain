"""The question log: `_meta/questions.log`, what people actually ask (shape 1).

One JSON object per line, append only, merged by union. The query path reads it first:
the same subject and a similar question point at the pages that answered last time.
An entry with `needed_steering` becomes a test question and a see-also line in the
subject's hub; a question asked three times from evidence alone and never from a
signed page is what distill should propose next. Pointers only, never the one copy of
an answer.

    python questions_log.py [root] append --question Q --subject S --tier T [--page P ...]
                                          [--steering] [--by-request]
    python questions_log.py [root] lookup --question Q [--subject S] [--min-overlap 0.5]
    python questions_log.py [root] unanswered [--times 3]

An entry: {"at", "question", "subject", "tier", "pages": [...], "needed_steering",
"answered_by_request"}. Tiers follow the answer: signed, pending, evidence, working.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages as pages_mod  # noqa: E402
import spaceyaml  # noqa: E402

LOG_PATH = Path("_meta") / "questions.log"
HEADER = "# Question log (one JSON object per line; append only)"
EVIDENCE_TIERS = ("evidence", "witness")
WORD_RE = re.compile(r"\w{3,}")


def tokens(text) -> set:
    """Lowercased words of three characters or more."""
    return set(WORD_RE.findall(str(text or "").lower()))


def overlap(a, b) -> float:
    """Shared words over the shorter question's words: 1.0 when one contains the other."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def read(root) -> list:
    path = Path(root) / LOG_PATH
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("question"):
            entries.append(entry)
    return entries


def append(root, question: str, subject, tier: str, pages, needed_steering: bool = False,
           answered_by_request: bool = False, now: dt.datetime | None = None) -> dict:
    """Record one answered question; returns the entry written."""
    root = Path(root)
    pages_mod.refuse_copy(root)
    now = now or spaceyaml.load(root).now()
    entry = {"at": pages_mod.iso(now), "question": str(question).strip(), "subject": subject,
             "tier": tier, "pages": [str(p) for p in (pages or [])],
             "needed_steering": bool(needed_steering), "answered_by_request": bool(answered_by_request)}
    path = root / LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    prefix = "" if not text else ("" if text.endswith("\n") else "\n")
    if not text.strip():
        prefix = HEADER + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(prefix + json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _same_subject(entry: dict, subject) -> bool:
    if subject in (None, ""):
        return True
    return str(entry.get("subject") or "").lower() == str(subject).lower()


def lookup(root, subject, question: str, min_overlap: float = 0.5) -> list:
    """Earlier entries about the same subject whose question overlaps this one, best first."""
    scored = []
    for entry in read(root):
        if not _same_subject(entry, subject):
            continue
        score = overlap(entry["question"], question)
        if score >= min_overlap:
            scored.append((score, str(entry.get("at") or ""), entry))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [entry for _, _, entry in scored]


def unanswered(root, times: int = 3, min_overlap: float = 0.5) -> list:
    """Questions asked `times` or more from evidence alone and never from a signed page."""
    clusters: list = []
    for entry in read(root):
        for cluster in clusters:
            if _same_subject(entry, cluster[0].get("subject")) and any(
                    overlap(q["question"], entry["question"]) >= min_overlap for q in cluster):
                cluster.append(entry)
                break
        else:
            clusters.append([entry])
    out = []
    for cluster in clusters:
        if any(str(q.get("tier")) == "signed" for q in cluster):
            continue
        if sum(1 for q in cluster if str(q.get("tier")) in EVIDENCE_TIERS) >= times:
            out.append(cluster[-1]["question"])
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = ("append", "lookup", "unanswered")
    root = Path(".")
    if argv and argv[0] not in commands and not argv[0].startswith("-"):
        root = Path(argv.pop(0))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    add = sub.add_parser("append")
    add.add_argument("--question", required=True)
    add.add_argument("--subject", default=None)
    add.add_argument("--tier", required=True)
    add.add_argument("--page", action="append", default=[])
    add.add_argument("--steering", action="store_true")
    add.add_argument("--by-request", action="store_true")
    look = sub.add_parser("lookup")
    look.add_argument("--question", required=True)
    look.add_argument("--subject", default=None)
    look.add_argument("--min-overlap", type=float, default=0.5)
    un = sub.add_parser("unanswered")
    un.add_argument("--times", type=int, default=3)
    args = ap.parse_args(argv)
    if args.command == "append":
        entry = append(root, args.question, args.subject, args.tier, args.page,
                       needed_steering=args.steering, answered_by_request=args.by_request)
        print(json.dumps(entry, ensure_ascii=False))
        return 0
    if args.command == "lookup":
        hits = lookup(root, args.subject, args.question, args.min_overlap)
        print(json.dumps(hits, indent=2, ensure_ascii=False))
        return 0 if hits else 1
    for question in unanswered(root, args.times):
        print(question)
    return 0


if __name__ == "__main__":
    sys.exit(main())
