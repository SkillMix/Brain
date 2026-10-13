"""The badge: what an agent may do from this space, per action class (vision section 4).

`space.yaml: authority` sets one level per action class, `propose` < `after_window` <
`at_once`. The floors of `rules.md` section 3 (and any stricter `floors:` in the brain's own
`space.yaml`) cap them; an agent row (`agents:` with a `badge:`) can only lower its levels.
The tools ask before they act:

- `release`: at `propose` nothing leaves without a person's approval on the item;
- `edit_inside`: at `propose` the librarian's mechanical changes become proposals;
- `write_outside_system`: at `propose` the tracker routine only drafts;
- `send_outward` and `spend` are `propose` by floor: tools never send or spend.

Every action above `propose` records whom it acted for and by which path (`acted`).

    python authority.py [root]               the effective levels, one line each
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spaceyaml  # noqa: E402

LEVELS = spaceyaml.LEVELS


def rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else 0


def lowest(*levels) -> str:
    return min((lvl for lvl in levels if lvl in LEVELS), key=rank, default="propose")


def agent_row(space: spaceyaml.Space, name: str | None) -> dict:
    for row in space.agents:
        if isinstance(row, dict) and row.get("name") == name:
            return row
    return {}


def level(space: spaceyaml.Space, action: str, agent: str | None = None) -> str:
    """The level an agent may act at: the space's level, under the floors, under its own row."""
    lvl = space.authority.get(action, "propose")
    floor = space.floors.get(action)
    if floor:
        lvl = lowest(lvl, floor)
    badge = (agent_row(space, agent).get("badge") or {}) if agent else {}
    if badge.get(action):
        lvl = lowest(lvl, badge[action])
    return lvl


def allows(space: spaceyaml.Space, action: str, wanted: str = "at_once", agent: str | None = None) -> bool:
    return rank(level(space, action, agent)) >= rank(wanted)


def acted(space: spaceyaml.Space, path: str, person: str | None = None) -> dict:
    """Who an action above propose was for, and the path that let it through."""
    return {"acted_for": person or space.owner or "owner", "path": path}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    space = spaceyaml.load(Path(argv[0] if argv else "."))
    for action in spaceyaml.ACTION_CLASSES:
        print(f"{action}: {level(space, action)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
