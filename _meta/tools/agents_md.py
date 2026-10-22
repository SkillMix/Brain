"""AGENTS.md: the generated operating contract of a space (shape 1).

Rendered from `_meta/templates/AGENTS.md.tpl` (a plain str.format template) and
`space.yaml`: the profile and its zones, the never list (rules.md section 1), the
Needs-you instruction, the kinds and the pointers to rules.md, docs/shape-1.md and the
toolbox. Agents read it; nobody edits it by hand.

    python agents_md.py [root]            write AGENTS.md
    python agents_md.py [root] --check    exit 1 when AGENTS.md differs from the render
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spaceyaml  # noqa: E402

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
TEMPLATE_NAME = "AGENTS.md.tpl"
INSTRUCTION_NAME = "personal-instruction.md"

COMMON = (
    "- `_inbound/`: candidates, checks and questions a person must answer; Needs you renders "
    "them. `_outbound/`: the queue of what leaves after its window, what is held, and "
    "requests to other spaces.",
    "- `_meta/subjects.yaml`: the registry (subjects, `part_of`, owners, `match` "
    "identifiers). `_meta/state/`: run state. `_meta/tools/`: the tools.",
)
KNOWLEDGE = (
    "- `knowledge/<kind>/<subject>/`: signed pages and a generated hub per subject; "
    "`knowledge/decisions/`: frozen decisions with supersede links; "
    "`knowledge/sources/<yyyy>/<mm>/`: witness pages, dated evidence, never signed.",
    "- `live/`: `now.md` (the pin), `subjects/<id>.md` (status pages) and `health.md`. "
    "Operational state, rebuilt by the status routine, never signed.",
)
ZONES = {
    "brain": (
        "- `identity/` and `rules.md`: the constitution of the company; every space below "
        "inherits them through its mount. Signed by the top owner.",
        *KNOWLEDGE,
        "- `_meta/subjects.yaml` here is the root registry: every space below reuses its ids.",
        *COMMON,
        "- No desk here: conclusions arrive as candidates through `_inbound/`.",
    ),
    "project": (
        *KNOWLEDGE,
        *COMMON,
        "- No desk here: work happens in code and pull requests. `identity/` and `rules.md` "
        "are read through the brain mount. `outbox/` is optional: drafts the crew fires.",
    ),
    "personal": (
        *KNOWLEDGE,
        "- `work/<kind>/<subject>/<effort>/`: the desk, free-form inside. Anything expected to "
        "matter tomorrow is born here; a new subject folder is registered by the status "
        "routine on its next run.",
        "- `journal/<yyyy>/<mm>/<yyyy>-<mm>-<dd>.md`: the daily note. Capture is always on: "
        "during any substantive session, append durable-looking context to today's note. "
        "No secrets, ever.",
        "- `outbox/`: drafts the owner fires. Agents draft; they never send.",
        *COMMON,
        "- `personal/` and `academic/` are never staged and never leave.",
    ),
    "channel": (
        "- `_inbound/` and `_outbound/` only: this space is the corridor between two spaces. "
        "Records arrive in `_inbound/`, leave through `_outbound/`, and nothing settles here.",
    ),
    spaceyaml.TEMPLATE_PROFILE: (
        "- This repository is the template: the superset of every profile. `onboard` sets the "
        "profile and removes what that profile does not use.",
        *KNOWLEDGE,
        *COMMON,
    ),
}

NEVER = (
    "Never sign: `status: stable` is written only by `sign` while it executes a named "
    "person's verdict; a routine or agent that writes it is a defect. Never send, post, "
    "spend or delete: drafts go to `outbox/` and the person fires them. Agents edit files; "
    "the sync script commits them and the platform merges; no session runs a git write. "
    "Secrets never enter the repository, what is on the never-leaves list never leaves this "
    "space by any path, and agents never widen their own authority (`rules.md` section 1)."
)
KINDS_INTRO = {
    "pages": "Subjects and signed pages are grouped by kind under `knowledge/`; hierarchy and "
             "ownership live in `_meta/subjects.yaml`, never in paths. The kinds of this company:",
    "channel": "A channel holds no pages of its own; the kinds below are the company's vocabulary, "
               "declared once in the brain, for the records that pass through:",
}
NEEDS_YOU_FALLBACK = (
    "Before anything else, show Needs you: run `python _meta/tools/needs_you.py` and read its "
    "lines to the person, each with its recommendation and its default; then do the work."
)


def _template(root: Path) -> str:
    for path in (root / "_meta" / "templates" / TEMPLATE_NAME, TEMPLATES / TEMPLATE_NAME):
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"no {TEMPLATE_NAME} under {root} or {TEMPLATES}")


CAPTURE = {
    "personal": (
        "Capture is always on here: append durable context to today's daily note "
        "`journal/<yyyy>/<mm>/<yyyy>-<mm>-<dd>.md`. Any answer that took more than one lookup is "
        "captured with its sources and appended to the question log `_meta/questions.log`. "
        "Never put company knowledge in the harness's own memory; pointers and preferences only."),
    "other": (
        "Nothing is written to a daily note here: what a session learns is kept in the person's "
        "own workspace, through the instruction their onboarding installed."),
}


def _needs_you(root: Path, profile: str = "") -> str:
    """What a session does first and how it captures, for this space's profile. The user-level
    personal instruction (`_meta/templates/personal-instruction.md`) is installed by onboard
    with the person's workspace path; it is never copied in here."""
    capture = CAPTURE["personal" if profile == "personal" else "other"]
    return (NEEDS_YOU_FALLBACK + "\n\n" + capture + "\n\nNever commit, push or merge: the sync script "
            "lands files.")


def render(root) -> str:
    root = Path(root)
    space = spaceyaml.load(root)
    profile = space.profile or "unset"
    zones = ZONES.get(profile) or ZONES["personal"]
    kinds = [KINDS_INTRO["channel" if profile == "channel" else "pages"], ""]
    kinds += [f"- `{kind}`" for kind in space.kinds]
    kinds += [f"- `{kind}` (fixed)" for kind in spaceyaml.FIXED_KINDS]
    models = [str(m) for m in (space.models or [])]
    if models and models != ["any"]:
        never_text = NEVER + ("\n- Never read this space as a model outside its `models:` list ("
                              + ", ".join(models) + "): if you are not one of them, stop and tell the person.")
    else:
        never_text = NEVER
    text = _template(root).format(
        name=space.name, profile=profile, owner=space.owner or "unset",
        zones="\n".join(zones), never=never_text, needs_you=_needs_you(root, profile), kinds="\n".join(kinds))
    return text.replace("\r\n", "\n").rstrip("\n") + "\n"


def write(root) -> Path:
    root = Path(root)
    path = root / "AGENTS.md"
    path.write_text(render(root), encoding="utf-8", newline="\n")
    return path


def check(root) -> bool:
    """True when AGENTS.md exists and equals the render (line endings aside)."""
    path = Path(root) / "AGENTS.md"
    if not path.is_file():
        return False
    current = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return current == render(root)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="agents_md.py", description="Write AGENTS.md, the operating contract, from space.yaml.")
    parser.add_argument("root", nargs="?", default=".", help="the space (default: here)")
    parser.add_argument("--check", action="store_true",
                        help="write nothing; exit 1 when AGENTS.md differs from the render")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    root = Path(args.root)
    if args.check:
        if check(root):
            print("AGENTS.md is current")
            return 0
        print("AGENTS.md is stale; run agents_md.py to regenerate it")
        return 1
    path = write(root)
    print(f"wrote {path.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
