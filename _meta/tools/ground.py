"""Ground: the grounding lines every answer ends with (vision section 13, ages in every answer).

For each page an answer rests on: its tier (`signed`, `system`, `pending`, `witness`,
`reported`, `working`; `signed` only for a verified signature under knowledge/ or identity/:
stable, its fingerprint its body's and its signed fields unchanged; a checked draft that may be
read as knowledge carries `checked, not signed` after its tier, `label`), the layer's scope (`company`, `team`, `mine`), its date, its age in
days, and "past window", "confidence low" (four weeks past its window) or "contested"
where they apply. Then one line with the company view's date (the brain mount's pin) and
one with the index's age, so an answer never hides how old its ground is.

    python ground.py [root] <id or path> [...]
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ids  # noqa: E402
import pages  # noqa: E402
import spaceyaml  # noqa: E402

SCOPE = {"personal": "mine", "project": "team", "channel": "team", "brain": "company", "template": "mine"}
LOW_DAYS = 28
CHECKED = "checked, not signed"
# What stays signature-only even when checked (rules.md section 2): rules, policies and processes,
# decisions that may bind, a page about a person; the check flags the rest itself (`high_stakes`).
SIGN_TYPES = ("policy", "directive", "process", "decision")


def _locate(root: Path, ref: str) -> tuple[Path, Path] | None:
    """(the layer's root, the page) for an id or a path, here first, then each mount."""
    space = spaceyaml.load(root)
    layers = [root] + space.ancestor_roots()
    for base in layers:
        if pages.ID_RE.match(ref):
            rel = ids.table(base).get(ref)
            if rel and (base / rel).exists():
                return base, base / rel
        else:
            candidate = Path(ref)
            if not candidate.is_absolute():
                candidate = base / ref
            if candidate.exists():
                return base, candidate
    return None


def tier_of(rel: str, fm: dict, body: str = "") -> str:
    """The page's tier. `signed` means a verified signature where sign writes one: under
    knowledge/ (not knowledge/sources/) or identity/, stable, its fingerprint its body's and its
    signed fields unchanged (`landing.signed_ok`). A stable stamp anywhere else, or one that does
    not check, falls through to the tier the page's place gives it, so it counts as working."""
    signed_zone = (rel.startswith("knowledge/") and not rel.startswith("knowledge/sources/")) \
        or rel.startswith("identity/")
    if str(fm.get("status")) == "stable" and signed_zone:
        import landing   # lazily: landing imports much more than a grounding line needs
        if landing.signed_ok(fm, body):
            return "signed"
    if rel.startswith("knowledge/sources/"):
        return "system" if fm.get("source") in ("tracker", "repo", "sent") else "witness"
    if rel.startswith("_inbound/") or fm.get("released_from"):
        return "pending" if not fm.get("released_from") or fm.get("landing") else "reported"
    if rel.startswith("live/"):
        return "reported"
    return "working"


def readable(rel: str, fm: dict, root: Path | None = None) -> bool:
    """A draft of this space that a second agent checked (`checked: {by, at}`, distill.checked) and
    that is on no high-stakes kind: it counts as knowledge without a signature, always shown as
    `checked, not signed`. A released or held item, a page outside the drafts, a rule, policy,
    process or decision draft, one about a person, one the check flagged and one that would
    change a signed page (given the layer's `root`) all wait for a signature."""
    if not rel.startswith("_inbound/candidates/") or fm.get("released_from") or fm.get("high_stakes"):
        return False
    if not isinstance(fm.get("checked"), dict) or not fm["checked"].get("by"):
        return False
    if str(fm.get("type") or "").lower() in SIGN_TYPES or str(fm.get("kind") or "").lower() == "people":
        return False
    target = root / str(fm["target"]) if root is not None and fm.get("target") else None
    if target is not None and target.is_file():
        try:
            return str(pages.read(target)[0].get("status")) != "stable"
        except Exception:
            return False
    return True


def label(rel: str, fm: dict, body: str = "", root: Path | None = None) -> str:
    """The tier as a reader sees it: `pending, checked, not signed` for a readable checked draft."""
    tier = tier_of(rel, fm, body)
    return f"{tier}, {CHECKED}" if tier == "pending" and readable(rel, fm, root) else tier


def _stamp(fm: dict) -> dt.datetime | None:
    gen = fm.get("generated") if isinstance(fm.get("generated"), dict) else {}
    for value in (fm.get("verified_at"), fm.get("signed_at"), fm.get("at"), gen.get("at")):
        if value:
            try:
                return pages.parse_iso(value)
            except (TypeError, ValueError):
                continue
    return None


def line_for(root: Path, ref: str, now: dt.datetime) -> str:
    found = _locate(root, ref)
    if found is None:
        return f"- {ref} (not found here or in any mount)"
    base, path = found
    space = spaceyaml.load(base)
    fm, body = pages.read(path)
    rel = path.relative_to(base).as_posix()
    stamp = _stamp(fm)
    days = (now - stamp).total_seconds() / 86400 if stamp else None
    window = pages.window_days(fm, space.windows)
    marks = []
    if days is not None and window is not None and days > window:
        marks.append("past window")
        if days > window + LOW_DAYS:
            marks.append("confidence low")
    if fm.get("contested"):
        marks.append("contested")
    age = "age unknown" if days is None else (f"{int(days)} day" + ("" if int(days) == 1 else "s"))
    date = f"{stamp:%Y-%m-%d}" if stamp else "undated"
    extra = (", " + ", ".join(marks)) if marks else ""
    title = fm.get("title") or path.stem
    return f"- {title} ({label(rel, fm, body, base)}, {SCOPE.get(space.profile, 'mine')}, {date}, {age}{extra})"


def pin_date(root: Path) -> str | None:
    space = spaceyaml.load(root)
    brain = space.mount_root("brain")
    if brain is None:
        return None
    res = subprocess.run(["git", "-C", str(brain), "log", "-1", "--format=%cI"], capture_output=True, text=True)
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()[:10]
    return dt.datetime.fromtimestamp((brain / "space.yaml").stat().st_mtime, dt.timezone.utc).strftime("%Y-%m-%d")


def index_line(root: Path) -> str:
    try:
        import search
    except ImportError:
        return "index: plain search (no index)"
    engine = search.config(root)["engine"]
    if engine == "none":
        return "index: plain search (no index)"
    age = search.built_age_hours(root)
    return f"index: {engine}, built {age:.0f} hours ago" if age is not None else f"index: {engine}, not built yet"


def lines(root, refs, now: dt.datetime | None = None) -> list:
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    out = [line_for(root, str(ref), now) for ref in refs]
    pin = pin_date(root)
    if pin:
        out.append(f"company view as of {pin}")
    out.append(index_line(root))
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(".")
    if argv and Path(argv[0]).is_dir() and (Path(argv[0]) / "space.yaml").exists():
        root = Path(argv.pop(0))
    if not argv:
        print(__doc__)
        return 2
    for line in lines(root, argv):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
