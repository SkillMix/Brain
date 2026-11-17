"""access: who can really read this space's repository, against the `audience` it states.

A space's `audience` decides what may leave for it at once and what the release rule trusts.
That is only true if the host agrees: a collaborator outside the audience reads everything
that lands there, and a name in the audience without access never sees it. Once a day (the
daily chain) this compares the repository's readers on the host with the audience, through
the people subjects' `match: {github: <login>}` in the registry, and writes what does not
match to `_inbound/needs-you/access.yaml`. A person who left (their people subject archived,
`offboard.py <brain> --person`) is in no audience, a listed one included, so their remaining
access shows. A reader whose login matches no registered person is always named: when some
audience names have no login, as one `access-unknown` item beside `access-unverified`, since
that reader may be one of them or a stranger, and the item says when there are more such
readers than unlinked names, so at least one is a stranger. It never changes access; only the
space's owner does that.

    python access.py [root]
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import registry as registry_mod  # noqa: E402
import spaceyaml  # noqa: E402

NEEDS_YOU = Path("_inbound") / "needs-you" / "access.yaml"


@dataclass
class AccessReport:
    repo: str | None = None
    extra: list = field(default_factory=list)        # logins that read the repository, outside the audience
    unverified: list = field(default_factory=list)   # audience names without a known login
    unknown: list = field(default_factory=list)      # readers no registered person has as login, while names are unverified
    skipped: str = ""


def readers(repo: str) -> list | None:
    """Logins that can read the repository (collaborators, teams and organization owners), or
    None when the host cannot say (no `gh`, no permission to list them)."""
    try:
        done = subprocess.run(["gh", "api", "--paginate", f"repos/{repo}/collaborators?affiliation=all",
                               "-q", ".[].login"], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return sorted({line.strip() for line in done.stdout.splitlines() if line.strip()})


def logins(root: Path, space: spaceyaml.Space) -> dict:
    """person name -> host login, from people subjects with `match: {github: <login>}`."""
    out: dict = {}
    try:
        subjects = registry_mod.Registry(root, space).everywhere()
    except Exception:
        return out
    for subject in subjects:
        match = subject.match if isinstance(subject.match, dict) else {}
        if subject.kind == "people" and match.get("github"):
            out[str(subject.title)] = str(match["github"])
    return out


def audience_names(root: Path, space: spaceyaml.Space) -> list:
    """The names a space's `audience` stands for. A list is itself, less every name whose people
    subject is archived (they left: their access is what the check must show). `everyone` (the
    brain) is the owner and every registered person who has not left (state `archived`); `owner`
    and `crew` are the owner, since a space's crew is its audience list and its owner
    (mint.crew_of) and the word alone lists no one else."""
    try:
        subjects = [s for s in registry_mod.Registry(root, space).everywhere() if s.kind == "people"]
    except Exception:
        subjects = []
    left = {str(s.title) for s in subjects if s.state == "archived"}
    if isinstance(space.audience, list):
        return [name for name in space.audience if str(name) not in left]
    names = [space.owner] if space.owner and space.owner not in left else []
    if space.audience == "everyone":
        names += [str(s.title) for s in subjects if s.state != "archived"]
    return list(dict.fromkeys(names))


def check(root, readers_fn=readers) -> AccessReport:
    root = Path(root)
    space = spaceyaml.load(root)
    report = AccessReport()
    if space.mode == "fixture":
        report.skipped = "fixture"
        return report
    import onboard
    report.repo = onboard.git_remote(root)
    if not report.repo:
        report.skipped = "no repository"
        return report
    found = readers_fn(report.repo)
    if found is None:
        report.skipped = "the host did not list the readers"
        return report
    audience = audience_names(root, space)
    known = logins(root, space)
    allowed = {known[name].lower() for name in audience if name in known}
    report.unverified = sorted(name for name in audience if name not in known)
    outside = sorted(login for login in found if login.lower() not in allowed)
    if report.unverified:   # an unknown reader may be an unlinked member, or a stranger: named apart, never dropped
        known_logins = {v.lower() for v in known.values()}
        report.extra = [login for login in outside if login.lower() in known_logins]
        report.unknown = [login for login in outside if login.lower() not in known_logins]
    else:
        report.extra = outside
    return report


def write_needs_you(root, report: AccessReport) -> None:
    import pages
    root = Path(root)
    pages.refuse_copy(root)
    space = spaceyaml.load(root)
    items = []
    for login in report.extra:
        items.append({"id": f"access-{login.lower()}", "kind": "question", "for": space.owner,
                      "text": f"{login} can read {report.repo} but is not in the audience of {space.name}. "
                              "Everything that lands there reaches them.",
                      "recommendation": "add them to the audience in space.yaml if they work on it; "
                                        "otherwise remove their access on the host",
                      "default": "they keep reading it", "depends": 0,
                      "check": f"gh api repos/{report.repo}/collaborators/{login}/permission -q .permission"})
    if report.unknown:
        strangers = len(report.unknown) > len(report.unverified)
        items.append({"id": "access-unknown", "kind": "question", "for": space.owner,
                      "text": f"{', '.join(report.unknown)} can read {report.repo} and match no registered person. "
                              f"If one is {', '.join(report.unverified)}, add match: {{github: <login>}} to that "
                              "person's subject; anyone else reads everything that lands there."
                              + (f" There are more of them ({len(report.unknown)}) than people without a login "
                                 f"({len(report.unverified)}), so at least one is not in the audience."
                                 if strangers else ""),
                      "recommendation": "link each login to its person, or remove their access on the host",
                      "default": "they keep reading it", "depends": 0,
                      "check": f"gh api repos/{report.repo}/collaborators?affiliation=all -q '.[].login'"})
    if report.unverified:
        items.append({"id": "access-unverified", "kind": "question", "for": space.owner,
                      "text": "Access to " + str(report.repo) + " cannot be checked for " + ", ".join(report.unverified)
                              + ": their people subjects have no host login.",
                      "recommendation": "add match: {github: <login>} to each person's subject in the brain",
                      "default": "access stays unchecked for them", "depends": 0, "check": ""})
    path = root / NEEDS_YOU
    if items or path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({"items": items}, sort_keys=False, allow_unicode=True),
                        encoding="utf-8", newline="\n")


def run(root, now=None) -> AccessReport:
    report = check(root)
    if not report.skipped:
        write_needs_you(root, report)
    return report


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    report = run(Path(argv[0]) if argv else Path("."))
    print(json.dumps(report.__dict__))
    return 0


if __name__ == "__main__":
    sys.exit(main())
