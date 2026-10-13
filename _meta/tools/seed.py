"""Seed: a person's first backfill, then only what is active and important now (vision 15).

The agent first exports `--days` of each declared source (the person's mail, sent mail
included, calendar, chat, calls) into the feed files, exactly as the sweep skill says, only
further back. This tool then runs the sweep over them (witness pages: everything stays
evidence) and one distill pass, which proposes candidates only for subjects that are active
and important, stateful or asked about. Nothing older is signed; the archive stays evidence
until someone needs it, so signing grows with activity, not with history.

    python seed.py [root] [--days 90]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import distill  # noqa: E402
import spaceyaml  # noqa: E402
import sweep  # noqa: E402

STATE = Path("_meta") / "state" / "seed.yaml"


def run(root, days: int = 90, now=None) -> dict:
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    swept = sweep.run(root, now=now)
    proposed = distill.run(root, now=now)
    result = {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "days": int(days),
              "records": swept.records_seen, "witness": swept.witness_written,
              "candidates": getattr(proposed, "candidates_written", 0)}
    path = root / STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    path.write_text(yaml.safe_dump(result, sort_keys=False), encoding="utf-8", newline="\n")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args(argv)
    print(json.dumps(run(args.root, days=args.days)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
