"""The verdict cut-over: begin the person's verdict store now (not a shape migration).

Before the verdict store (`_meta/state/verdicts.jsonl`), the person's answers were the verdict
and note lines sign.py wrote to log.md. A space that upgrades keeps them: the store's first row
pins a fingerprint of each such line log.md holds when the store begins (`status.pin_cutover`),
and those lines keep counting; a line written to log.md after that is narrative and never an
answer, whatever it reads like. The librarian's next run pins the cut-over on its own, as do
needs_you's list and the person's first answer through sign.py; until then no line counts, and
the items answered in log.md wait on Needs you again. Run this right after the upgrade to pin it
at once:

    python _meta/migrations/verdicts_cutover.py [root]           pin the cut-over (once)
    python _meta/migrations/verdicts_cutover.py [root] --check   say where the space stands

The pin adds no answer the tools before the store did not read already, and it never moves: a
second run, or one after sign.py began the store, changes nothing. A cloud run never pins, nor
does any run while log.md is reached through a link or cannot be read: a pin of nothing is for good.
`--check` exits 1 when the cut-over is not pinned yet. It is not named `shape_<n>.py`, so the
librarian's shape migrations never pick it up.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import status  # noqa: E402


def describe(state: dict) -> str:
    if not state["pinned"]:
        return (f"not pinned: none of the {state['kept']} verdict or note line(s) in log.md counts yet; "
                "the next run of the librarian, or of this migration, pins them")
    line = f"pinned: {state['kept']} verdict or note line(s) from log.md still count"
    if state["after"]:
        line += f"; {state['after']} written after the cut-over do not"
    return line


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pin the verdict cut-over of a space.")
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root)
    if not (root / "space.yaml").is_file():
        print(f"verdict cut-over: no space.yaml in {ascii(str(root))}", file=sys.stderr)
        return 2
    if args.check:
        state = status.cutover_state(root)
        print(describe(state))
        return 0 if state["pinned"] else 1
    wrote = status.pin_cutover(root)
    state = status.cutover_state(root)
    if not state["pinned"]:   # a cloud run, a link where the store or log.md goes, or a file that cannot be read or written
        print(f"verdict cut-over: {status.VERDICTS.as_posix()} could not be begun here", file=sys.stderr)
        return 1
    print(("" if wrote else "already ") + describe(state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
