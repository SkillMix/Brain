"""Release: the one way content leaves a space (shape 1, rules.md section 6).

`ship` walks `_outbound/queue/` and `_outbound/signals/`. An item leaves when its
`stage_after` has passed and, for a `window` item, a morning view has listed it
(`listed: true`); signals, at-once items and retractions need no listing. What an item
says was judged before it was queued (the router and its second agent's check, or a
person), never matched against words here. On the way out every item is checked again
for facts only: a mode mismatch (`shadow` ships only to `shadow`), a file the secrets scan
cannot read (too large, a NUL byte, not UTF-8, or the scan missing or failing) or a
secrets-scan finding moves it to `_outbound/held/` with a reason; so does an item whose
id is not a page id, whose destination is not a space name (and whose subject has no home
in the registry to send it to instead), or that names no subject and
is not a page a person signed here (its fingerprint line in this space's `log.md`: no
classification could let it go). A retraction passes no gate, so it travels rebuilt from
`released.jsonl` (its id, the release it retracts, nothing of its file's text) and only to
where that release went; any other is held. The queue's other
fields (`class`, `listed`, `approved_by`) are trusted as the tools wrote them: only tools
write `_outbound/` (the routine profile denies an agent's edits there), and nothing here can
tell a tool's file from a forged one. What ships lands in the destination's
`_inbound/candidates/<id>.md` as a draft with `released_by`, `released_from`,
`release_class` and `evidence`; a signed page keeps its signature untouched. Every file
written at a destination is confined to its `_inbound/` folder and never written through a
link. Offline the destination root is given; online
it is the release clone under `~/.spaces/.release/<destination>` (or `$SPACES_HOME`),
`<destination>` always one lowercase space name, and `publish()` does all the git work, on
the branch `release/<slug>-<8 hex>` (`branch_slug`: the sender's name and a hash of its own
repository), on top of that branch while its pull request is open. A stable item ships only
with a signature made here: `signed_in` this space, `signed_by` one of the people who sign
here as another space's landing counts them (`signing_people`: the owner, the audience list,
and the owners and crew of the page's own subject and its umbrellas when it is homed here), and the whole `fingerprint` (and
`fields`) line in this space's own log.md; otherwise it goes back to be signed again. A
release clone is made only from `https://github.com/<owner>/<name>` (a mount's repository
or a registry subject's `match.repo`), with git told to speak https alone; a local path is
cloned only when `SPACES_LOCAL_CLONES=1` is set (the tests and offline fixtures). Shipped
items are remembered in `_meta/state/released.jsonl` (append-only) so `retract` can find
them. Routines never write `status: stable` and delete only their own queue files.

Requests and answers travel through the corridor at once: a request only as its question
and the fields request.py writes, each in its own shape (a slug, a time, one short line),
and only one request.py asked (`requests-asked.jsonl`); an answer only when this space's
request log records that it answered that asker, as its body and the fields request.py
writes. Nothing else in either file travels. Both are scanned like any release.

    python release.py [root] ship [--offline name=path ...]
    python release.py [root] retract <item-id>      a retraction item, ships at once
    python release.py [root] pull-back <item-id>    queue -> held, reason pulled
    python release.py [root] requeue <item-id> [--body FILE]   the agent puts back a hold that is fixed
                                                    (a scan hold: its text with values cut out)
    python release.py [root] set-aside <item-id>    a hold that never leaves as it is, held for good

No hold is silent. A hold the agent can act on (the scan could not read the item, a
destination that is no space name, a signature this space's history does not hold) is a row
of the `health` line on Needs you with what the agent does (`held_health`). Every ship puts
back on its own the holds that fixed themselves (`retry_held`: the scan runs again, the
registry names a home, no draft of that name waits), and every gate judges them again. A held
item's text changes here only by values cut out as [redacted] (`only_redacts`), which the scan
judges again; a hold that never leaves as it is is set aside. A destination that is no space name goes to its
subject's home when the registry names one, and a stable item whose signature this space's
history does not hold goes back on the person's list as a draft to sign (`sign_again`): a
person is asked only for the signature only they can give.

sweep.py imports the shared helpers (`destination_space`, `hold`, `append_run_line`).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import registry as registry_mod  # noqa: E402
import spaceyaml  # noqa: E402
import authority  # noqa: E402

try:  # the secrets scan is another tool; shipping works without it
    import scan  # noqa: E402
except ImportError:  # pragma: no cover
    scan = None

QUEUE = Path("_outbound") / "queue"
HELD = Path("_outbound") / "held"
SIGNALS = Path("_outbound") / "signals"
LANDING = Path("_inbound") / "candidates"
SIGNAL_LANDING = Path("_inbound") / "signals"
RUNS = Path("_meta") / "state" / "runs.jsonl"
RELEASED = Path("_meta") / "state" / "released.jsonl"
PAUSED = Path("_meta") / "state" / "paused.jsonl"
PAUSE_HOURS = 4 * 7 * 24
QUEUE_ONLY_KEYS = ("class", "stage_after", "listed", "destination", "from_space",
                   "why", "confidence", "routed_by", "content")   # the router's reasons stay home
NEEDS_NO_LISTING = ("at_once", "signal", "retraction")
COMMANDS = ("ship", "retract", "pull-back", "requeue", "set-aside")
# The id request.py gives a request, and its answer: a file name built from it is safe.
REQUEST_ID_RE = re.compile(r"^rq-[0-9a-f]{8}\Z")
# Set to 1 only by the tests and offline fixtures: a release clone may then come from a local path.
LOCAL_CLONES_ENV = "SPACES_LOCAL_CLONES"
QUESTION_CAP = 500   # what a request carries through the corridor: its question, this long at most
# What a request's frontmatter may carry into the corridor; anything else in the file stays here.
REQUEST_KEYS = ("id", "type", "status", "generated", "to", "from", "from_profile", "asked_by", "subject",
                "class", "narrowest", "at", "expires_at")
# What an answer's frontmatter may carry (the fields request.py writes); its body is the answer.
ANSWER_KEYS = ("id", "type", "title", "status", "generated", "request", "from", "to", "class", "approved_by",
               "answered_at", "covered", "destination")
# Each field's own shape (`_corridor_field`): a slug, an ISO time, or one short line.
SLUG_KEYS = ("to", "from_profile", "subject", "class")
TIME_KEYS = ("at", "expires_at", "answered_at")
NAME_KEYS = ("asked_by", "approved_by")
NAME_CAP = 120
# Set by release itself, whatever the file says.
FIXED_KEYS = ("id", "type", "title", "status", "generated", "from", "request", "destination")
UNREADABLE = (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError)


@dataclass
class ShipReport:
    shipped: int = 0
    skipped_unlisted: int = 0
    held: int = 0
    retracted: int = 0
    destinations: dict = field(default_factory=dict)
    signals: int = 0
    waiting: int = 0
    requeued: int = 0     # items held for a shadow-looking destination, back in the queue
    unreachable: int = 0  # queue items whose destination has no root on this machine
    rerouted: int = 0     # queue items re-addressed to their subject's current home
    failed: int = 0       # queue items whose online publish failed; they stay queued
    requests: int = 0     # requests and answers carried through a corridor
    unreadable: int = 0   # files in the queue that cannot be read: they never ship, the rest still do


# ----- shared helpers (sweep.py imports these) -----------------------------


def spaces_home() -> Path:
    """Where the release clones live: $SPACES_HOME, else ~/.spaces/.release (a working checkout
    made by onboard lives under ~/.spaces/<repository>, never in the same folder)."""
    return Path(os.environ.get("SPACES_HOME") or (Path.home() / ".spaces" / ".release"))


def is_request_id(value) -> bool:
    """A request's id as request.py makes it, `rq-` and eight hex digits."""
    return isinstance(value, str) and bool(REQUEST_ID_RE.match(value))


def release_dir(destination) -> Path | None:
    """Where a destination's release clone lives: `spaces_home()/<destination>`, or None when the
    destination is not one lowercase space name, when that folder is a link, or when it would
    resolve anywhere but directly inside the spaces home. A registry's `home` reaches this from
    data, and `../spaces/<name>` would otherwise name the person's own workspace. A Windows
    device name (`nul`, `com1`) is a slug but no folder, so it is refused too."""
    if not pages.is_slug(destination) or not pages.is_safe_name(destination):
        return None
    base = spaces_home()
    candidate = base / destination
    try:
        if pages.is_link(candidate) or candidate.resolve().parent != base.resolve():
            return None
    except (OSError, ValueError):
        return None
    return candidate


def checkout_of(destination: str) -> Path | None:
    """The writable online checkout of a destination, when it exists."""
    candidate = release_dir(destination)
    return candidate if candidate is not None and (candidate / ".git").exists() else None


def local_clones() -> bool:
    """The explicit fixture switch: only with SPACES_LOCAL_CLONES=1 may a clone come from a local path."""
    return os.environ.get(LOCAL_CLONES_ENV) == "1"


def _local_path(value: str) -> bool:
    """An absolute local folder that git cannot read as an option, a transport or a share: no
    leading `-`, no `\\\\host` or `//host`, no `file:`, `ext::` or other `scheme:` form."""
    if not value or value != value.strip() or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return False
    if value.startswith(("-", "\\\\", "//")) or "::" in value or "://" in value:
        return False
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]+:", value):   # a scheme (file:, ssh:); a drive is one letter
        return False
    return Path(value).is_absolute()


def clone_source(value) -> str | None:
    """The repository a release clone may be made from: `https://github.com/<owner>/<name>` for
    any of its GitHub spellings (`registry.clone_url`), or, with the fixture switch on, an
    absolute local path. Anything else (another host, `file://`, `ext::`, a share, a value
    git would read as an option): None, and the destination stays unreachable."""
    url = registry_mod.clone_url(value)
    if url:
        return url
    text = value if isinstance(value, str) else ""
    return text if local_clones() and _local_path(text) else None


def _is_brain(space: spaceyaml.Space, name: str) -> bool:
    """`name` is the brain's own name: the one mount found by its space.yaml (`Space.space_root`);
    every other mount is found by the name this space gave it, never by what it says it is."""
    brain = space.mount_root("brain")
    return brain is not None and name != space.name and space.space_root(name) == brain


def _brain_repo(space: spaceyaml.Space):
    return next((m.get("repo") for m in space.mounts if m.get("name") == "brain"), None)


def _mount_repo(space: spaceyaml.Space, destination: str) -> str | None:
    """The repository behind a destination: the mount of that name (a corridor too: requests
    travel to it by the name this space mounted it under), else the brain when it is the brain's
    name (mounted as `brain`, addressed by its name; no other mount is found by what its own
    space.yaml says), else the `match.repo` of the subject whose home it is in the registry a
    trust decision reads (this space's and the brain's, never a corridor's or a project
    mount's), so a missing mount alone never leaves a release stuck. Only a value
    `clone_source` accepts counts."""
    for mount in space.mounts:
        if mount.get("name") == destination and mount.get("repo"):
            return clone_source(mount["repo"])
    if _is_brain(space, destination) and _brain_repo(space):
        return clone_source(_brain_repo(space))
    try:   # the registry a trust decision reads: never a corridor's or a project mount's
        subjects = registry_mod.Registry(space.root, space).trusted_view().everywhere()
    except Exception:
        return None
    for s in subjects:
        if s.home != destination or not s.match.get("repo"):
            continue
        values = s.match["repo"] if isinstance(s.match["repo"], list) else [s.match["repo"]]
        found = next((url for url in map(clone_source, values) if url), None)
        if found:
            return found
    return None


def trust_repo(space: spaceyaml.Space, name: str) -> str | None:
    """The repository whose history vouches for the space called `name` (landing's trust root):
    the mount of that name, never a corridor's (`Space.is_corridor`: both sides write it), else
    the brain's when it is the brain's name (no other mount is found by what its own space.yaml
    says; a mount other than the brain counts only when that registry homes a subject there or
    registers it as a person's workspace, `Registry.vouched`), else, in the registry a trust decision reads (`Registry.trusted_view`: this space's
    and the brain's, never a corridor's or a project mount's), the `match.repo` of the
    registry subject that is that space itself (`id == home == name`, as mint writes it), else
    the `workspace_repo` of the one person whose registered workspace it is (as onboarding
    writes it for every workspace it makes; two people naming that workspace with different
    repositories give none). All of it is this space's own data, never the release's: a change
    to any of these fields waits for the owner's click (`gate.registry_reasons`). Never another
    subject merely homed there: a client's `match.repo` names the client's codebase, a sweep
    identifier, not the repository whose log.md speaks for the space."""
    if space.is_corridor(name):
        return None   # both sides write a corridor: it vouches for no one
    try:   # the registry a trust decision reads: its own and the brain's, never a corridor's
        reg = registry_mod.Registry(space.root, space)
    except Exception:
        reg = None
    if not _is_brain(space, name) and (reg is None or not reg.vouched(name)):
        return None   # a mount that registry does not know (a corridor under a custom name) vouches for no one
    for mount in space.mounts:
        if mount.get("name") == name and mount.get("repo"):
            return clone_source(mount["repo"])
    if _is_brain(space, name) and _brain_repo(space):
        return clone_source(_brain_repo(space))
    if reg is None:
        return None
    subjects = reg.trusted_view().everywhere()
    for s in subjects:
        if s.id != name or s.home != name or not s.match.get("repo"):
            continue
        values = s.match["repo"] if isinstance(s.match["repo"], list) else [s.match["repo"]]
        found = next((url for url in map(clone_source, values) if url), None)
        if found:
            return found
    given = [clone_source(s.extra.get("workspace_repo")) for s in subjects
             if s.kind == "people" and s.state != "archived" and s.extra.get("workspace_repo")
             and str(s.extra.get("workspace") or "") == name]
    if given and all(given) and all(registry_mod.same_repo(url, given[0]) for url in given):
        return given[0]
    return None


def _in_spaces_home(path: Path) -> bool:
    """A folder directly inside the spaces home that is no link: the only kind set aside or removed."""
    try:
        return not pages.is_link(path) and Path(path).resolve().parent == spaces_home().resolve()
    except (OSError, ValueError):
        return False


def _discard(path: Path) -> None:
    """Remove a clone this run made seconds earlier (git keeps some files read-only). Only a
    folder directly inside the spaces home; a link inside it is removed, never followed."""
    if not _in_spaces_home(path):
        raise pages.UnsafePath(f"{ascii(str(path))} is not a release clone; nothing is removed")
    for item in path.rglob("*"):
        try:
            if item.is_file() and not pages.is_link(item):  # a directory keeps its mode: without x it cannot be walked
                os.chmod(item, item.stat().st_mode | stat.S_IWRITE)
        except OSError:
            pass
    shutil.rmtree(path, ignore_errors=True)


def _origin(checkout: Path) -> str | None:
    """The origin a release clone was made from; None when it has none or git cannot say."""
    try:
        res = subprocess.run(["git", "--git-dir", str(checkout / ".git"), "config", "--get",
                              "remote.origin.url"], capture_output=True, text=True)
    except OSError:
        return None
    return res.stdout.strip() or None if res.returncode == 0 else None


def _set_aside(checkout: Path) -> None:
    """Move a release clone out of the way under `.stale/`; nothing is deleted. Only a folder
    directly inside the spaces home is ever moved."""
    if not _in_spaces_home(checkout):
        raise pages.UnsafePath(f"{ascii(str(checkout))} is not a release clone; nothing is moved")
    stale = checkout.parent / ".stale"
    stale.mkdir(parents=True, exist_ok=True)
    target, n = stale / checkout.name, 1
    while target.exists():
        n += 1
        target = stale / f"{checkout.name}-{n}"
    checkout.rename(target)


def _names(checkout: Path, destination: str) -> bool:
    """The checkout is the space so named (its space.yaml says so)."""
    try:
        return spaceyaml.load(checkout).name == destination
    except Exception:
        return False


def clone_argv(repo: str, target: Path) -> tuple[list, dict]:
    """git's argument list and environment for a release clone: https only (and local files only
    with the fixture switch), every other transport refused, and `--` before the two positional
    arguments so the repository can never be read as an option."""
    local = not repo.startswith("https://")
    allowed = "https:file" if local else "https"
    argv = ["git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
            "-c", f"protocol.file.allow={'always' if local else 'never'}",
            "clone", "--quiet", "--no-recurse-submodules", "--", repo, str(target)]
    return argv, dict(os.environ, GIT_ALLOW_PROTOCOL=allowed)


def ensure_checkout(space: spaceyaml.Space, destination: str) -> Path | None:
    """The destination's online checkout, cloned on first use from the repository its mount (or
    its registry subject) names. A destination that is not a space name (`release_dir`), no
    repository `clone_source` accepts, a failed clone or a clone that is not the space so named:
    None, and the items stay queued as unreachable. A clone of another repository under that
    name (an older space of the same name), one whose origin `clone_source` would refuse, or a
    checkout there that is not the space so named, is never used: it is set aside and the
    destination cloned fresh, but only when there is a repository to clone."""
    target = release_dir(destination)
    if target is None:
        return None
    found = target if (target / ".git").exists() else None
    repo = _mount_repo(space, destination)
    try:
        if found is not None:
            origin = _origin(found)
            # an origin no release would clone from today (made before this rule, or edited):
            # git fetches and pushes to it, so the checkout is never used
            untrusted = origin is not None and clone_source(origin) is None
            other = untrusted or (bool(repo and origin) and not registry_mod.same_repo(origin, repo))
            if not other and _names(found, destination):
                return found
            if not repo:   # nothing to clone in its place: it stays, unused
                return None
            _set_aside(found)
    except pages.UnsafePath:
        return None
    if not repo:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    argv, env = clone_argv(repo, target)
    try:
        subprocess.run(argv, check=True, capture_output=True, text=True, env=env)
    except (subprocess.CalledProcessError, OSError):
        return None
    try:
        is_space = spaceyaml.load(target).name == destination
    except Exception:
        is_space = False
    if not is_space:  # a code repository, or another space: never published into
        _discard(target)
        return None
    return target if (target / ".git").exists() else None


def refresh_checkout(checkout: Path) -> bool:
    """Bring a release clone to its origin's main before its settings are read: a clone is only
    fetched when something ships, so its space.yaml can be days behind the destination."""
    git = ["git", "-C", str(checkout)]
    try:
        fetched = subprocess.run(git + ["fetch", "--quiet", "origin", "--no-recurse-submodules"],
                                 capture_output=True, text=True)
        if fetched.returncode != 0:
            return False
        moved = subprocess.run(git + ["checkout", "--quiet", "--detach", "origin/main"],
                               capture_output=True, text=True)
        return moved.returncode == 0
    except OSError:
        return False


def _requeue_mode_held(root: Path, space: spaceyaml.Space) -> int:
    """Items a live space held only because their destination looked shadow go back to the
    queue; the next scrub reads the destination as it is now. Held signals stay held."""
    if space.mode != "live":
        return 0
    count = 0
    for path in sorted((Path(root) / HELD).glob("*.md")):
        try:
            fm, body = pages.read(path)
        except Exception:
            continue
        live_hold = str(fm.get("why") or "").startswith("this space runs live")
        if fm.get("reason") != "mode" or fm.get("class") == "signal" or not live_hold:
            continue
        if not pages.is_page_id(fm.get("id")):   # a name no queue file may carry: it stays held
            continue
        clean = {k: v for k, v in fm.items() if k not in ("reason", "why")}
        pages.write(Path(root) / QUEUE / f"{fm['id']}.md", clean, body, root=root)
        path.unlink()
        count += 1
    return count


def destination_space(space: spaceyaml.Space, destination: str,
                      dest_root: Path | None = None) -> spaceyaml.Space | None:
    """The destination's settings: its given root, else its mount, by the mount's name or the
    space's own (moved by every sync run), else its release checkout (refreshed only when
    something ships)."""
    mounted = space.space_root(destination) if destination != space.name else None
    for root in (dest_root, mounted, checkout_of(destination)):
        if root is not None and (Path(root) / "space.yaml").exists():
            try:
                return spaceyaml.load(root)
            except Exception:
                continue
    return None



def _doubt_defaults(fm: dict) -> dict:
    """The recommendation and default a doubt hold carries; a page's own are kept."""
    fm.setdefault("recommendation", "no: keep it home")
    fm.setdefault("default", "stays home")
    return fm


def _recommend_held_doubt(root: Path) -> None:
    """Pages held in doubt before holds carried a recommendation get the hold's own on disk."""
    for path in sorted((Path(root) / HELD).glob("*.md")):
        try:
            fm, body = pages.read(path)
        except Exception:
            continue
        if fm.get("reason") == "doubt" and "recommendation" not in fm:
            pages.write(path, _doubt_defaults(fm), body, root=root)


def hold(root: Path, fm: dict, body: str, reason: str, why: str) -> Path:
    """Write an item to _outbound/held/ with its reason; held items never leave by themselves.
    The file is named by the item's id only when it is a page id or a request id; any other id
    (from a queue file, so from data) is kept, escaped, as `refused_id`, and the item is held
    under a fresh page id, so no id can name a file outside `_outbound/held/`."""
    held_fm = dict(fm, reason=reason, why=why)
    if not (pages.is_page_id(fm.get("id")) or is_request_id(fm.get("id"))):
        held_fm["refused_id"] = ascii(fm.get("id"))[:120]
        held_fm["id"] = pages.new_id()
    if reason == "doubt":  # shown as one count; opened on demand, each with a recommendation
        _doubt_defaults(held_fm)
    target = pages.confine(root, f"{HELD.as_posix()}/{held_fm['id']}.md", zones=(HELD.as_posix(),))
    return pages.write(target, held_fm, body, root=root)


# What a page put back to be signed again keeps: what it is about and its text, never a signature.
# `destination` is this space's own queue data: once signed again, sign.py queues the page there
# again, and every gate judges it as before.
SIGN_AGAIN_KEYS = ("id", "type", "title", "subject", "kind", "high_stakes", "evidence", "also_about", "domain",
                   "destination")


def sign_again(root: Path, fm: dict, body: str, now: dt.datetime, why: str) -> Path | None:
    """A stable item whose signature this space's history does not hold goes back on the
    person's list as a draft candidate (`_inbound/candidates/<id>.md`): the one thing only a
    person can give is a signature, so they are asked for that, and nothing leaves until they
    sign it. Its signature fields are dropped, so nothing here assumes one. None when its id is
    no page id or a candidate already has that name (the caller holds it instead)."""
    root = Path(root)
    if not pages.is_page_id(fm.get("id")):
        return None
    try:
        target = pages.confine(root, f"{LANDING.as_posix()}/{fm['id']}.md", zones=(LANDING.as_posix(),))
    except pages.UnsafePath:
        return None
    if target.exists() or pages.is_link(target):
        return None
    draft = {k: fm[k] for k in SIGN_AGAIN_KEYS if fm.get(k) not in (None, "", [])}
    draft.update(status="draft", generated=pages.generated("release", now),
                 recommendation="yes: sign it again if it still reads right (its first signature is not "
                                "in this space's history, so it did not leave)")
    draft.setdefault("type", "concept")
    draft.setdefault("title", fm["id"])
    pages.write(target, draft, body, root=root)
    append_line(root / "log.md", f"- {pages.iso(now)} {fm['id']} back to sign again: {why}")
    return target


# What the agent does about a hold, by its reason (`held_health`). Holds a person decides
# (doubt, pulled), the gate decides (unclassified, kept), the next run clears (mode) or that are
# settled and never leave (superseded and withdrawn: route.py replaced or took back the record's
# projection; set aside: the agent routed the record again) are not here.
PERSON_HOLDS = ("doubt", "pulled", "unclassified", "kept", "mode")
ASIDE_HOLDS = ("scan", "no subject", "unrecorded", "bad id", "corridor")   # never leave as they are: `set_aside`
REDACTED = "[redacted]"   # the only text a scan hold's new body may hold that its held body does not
# Where the agent writes a scan hold's redacted text: inside the root, where a routine may write, and never staged.
REDACT_DIR = Path("_meta") / "state" / "private" / "redacted"
SETTLED_HOLDS = ("superseded", "withdrawn", "set aside")


def _hold_work(fm: dict) -> tuple[str, str]:
    """(what is wrong, what the agent does) for one item held for a reason the agent can act on."""
    reason, why, pid = str(fm.get("reason") or ""), str(fm.get("why") or ""), str(fm.get("id") or "")
    requeue_cmd = f"python _meta/tools/release.py . requeue {pid}"
    aside = f"python _meta/tools/release.py . set-aside {pid}"
    redo_file = f"{REDACT_DIR.as_posix()}/{pid}.md"
    redo = (f"write its text to {redo_file} (never staged) with each such value as {REDACTED}, then "
            f"{requeue_cmd} --body {redo_file}; if the value is the point of the record, {aside}")
    if reason == "scan" and why.startswith("secrets scan:"):
        return ("held: the secrets scan found what looks like a password or key", redo)
    if reason == "scan" and _scan_did_not_run(why):
        return ("held: the secrets scan did not run", "the release run puts it back once the scan runs")
    if reason == "scan":
        return ("held: the secrets scan could not read it whole", redo.replace("each such value", "what is not plain text"))
    if reason == "destination":
        return ("held: its destination is not a space name",
                "the release run sends it to its subject's home once the registry names one")
    if reason == "signature":
        return ("held: its signature is not in this space's history",
                "the release run puts it back on the list to sign again once no draft of that name waits")
    if reason == "no subject":
        return ("held: it names no subject", f"nothing lets it go without one: {aside}")
    if reason in ("unrecorded", "bad id", "corridor"):
        return (f"held: {why or reason}", f"it never leaves as it is: {aside}")
    return (f"held: {reason or 'no reason'}", "read it and act on it (the release skill)")


def held_health(root) -> list:
    """The items held in `_outbound/held/` for a reason the agent can act on, each as a row for
    the health line (`needs_you.health_work`): what is wrong and what the agent does next. Never
    silent: every hold that is not a person's or the gate's is one row."""
    out = []
    for path in sorted((Path(root) / HELD).glob("*.md")):
        try:
            fm, _ = _read_own(path)
        except UNREADABLE:
            ours = pages.is_page_id(path.stem) or is_request_id(path.stem)
            out.append({"id": path.stem, "text": f"{ascii(path.name)[:60]} in the held items cannot be read",
                        "agent": f"python _meta/tools/release.py . set-aside {path.stem}" if ours else
                                 "no tool wrote it, so it never leaves; the person removes it"})
            continue
        if fm.get("kept") or str(fm.get("reason") or "") in PERSON_HOLDS + SETTLED_HOLDS:
            continue
        what, does = _hold_work(fm)
        title = " ".join(str(fm.get("title") or fm.get("id") or path.stem).split())[:80]
        out.append({"id": str(fm.get("id") or path.stem), "text": f"{title} {what}", "agent": does})
    return out


def _scan_did_not_run(why: str) -> bool:
    return "not available" in why or "failed" in why


def _held_item(root: Path, item_id: str) -> tuple[Path, dict, str]:
    if not (pages.is_page_id(item_id) or is_request_id(item_id)):
        raise pages.UnsafePath(f"{ascii(item_id)[:40]} is not a page id or a request id")
    source = root / HELD / f"{item_id}.md"
    if not (source.is_file() or pages.is_link(source)):
        raise FileNotFoundError(f"{item_id} is not held")
    fm, body = _read_own(source)
    return source, fm, body


def requeue(root, item_id: str, body_file=None, now: dt.datetime | None = None) -> Path:
    """Put an item held for a reason that is fixed back where it came from, as it is; every gate
    and the scan judge it again on the next ship. `scan`, when the scan did not run: as it was,
    the scan runs now. `destination`: it goes to its subject's home in the registry.
    `signature`: back on the list to sign again (`sign_again`). Its text changes only for a scan
    hold the scan refused, and only by values cut out (`body_file`, `only_redacts`), so nothing new
    is added and the scan judges it again; a record whose value is its point is set aside
    (`set_aside`). Any other hold, and a hold a person or the gate decides, is refused
    (ValueError). The release run does all of this on its own (`retry_held`)."""
    root = Path(root)
    source, fm, body = _held_item(root, item_id)
    space = spaceyaml.load(root)
    now = now or space.now()
    reason = str(fm.get("reason") or "")
    refused = reason == "scan" and not _scan_did_not_run(str(fm.get("why") or ""))
    if body_file is not None:
        try:
            redacted = Path(body_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError(f"{ascii(str(body_file))[:60]} cannot be read ({exc.__class__.__name__})") from exc
        if fm.get("kept") or not refused or not only_redacts(body, redacted):
            raise ValueError("a held text changes only for a scan hold, and only by values cut out as "
                             f"{REDACTED}; export the record again and route it again otherwise")
        body = redacted
        done_with = Path(body_file)
    elif fm.get("kept") or reason in PERSON_HOLDS or reason not in ("scan", "destination", "signature") or refused:
        raise ValueError(f"{item_id} is held for {reason or 'no reason'}: not one the agent puts back as it is")
    clean = {k: v for k, v in fm.items() if k not in ("reason", "why", "refused_id")}
    if reason == "signature":
        if sign_again(root, clean, body, now, "put back by the agent") is None:
            raise ValueError(f"{item_id}: a candidate of that name is waiting already")
        source.unlink()
        return root / LANDING / f"{item_id}.md"
    if reason == "destination":
        try:
            home = _current_home(registry_mod.Registry(root, space), clean)
        except Exception:
            home = None
        if not home or home == space.name:
            raise ValueError(f"{item_id}: its subject has no other home in the registry")
        clean["destination"] = home
    folder = QUEUE
    if is_request_id(item_id):   # a corridor file goes back to its own folder
        folder = REQUESTS_OUT if str(clean.get("type")) == "request" else ANSWERS_OUT
        if folder == REQUESTS_OUT:
            clean.pop("destination", None)
    target = pages.confine(root, f"{folder.as_posix()}/{item_id}.md", zones=(folder.as_posix(),))
    if target.exists():
        raise ValueError(f"{item_id} is in {folder.as_posix()} already")
    pages.write(target, clean, body, root=root)
    source.unlink()
    if body_file is not None and (root / REDACT_DIR).resolve() in done_with.resolve().parents:
        done_with.unlink(missing_ok=True)   # the redacted copy is in the queue now; any other file is the caller's
    append_line(root / "log.md", f"- {pages.iso(now)} requeued {item_id} (held for {reason})")
    return target


def only_redacts(held: str, new: str) -> bool:
    """`new` is `held` with spans cut out, each as REDACTED, and nothing added: what is left between the
    marks appears in `held` in that order. The scan judges the result again on the next ship."""
    if REDACTED not in new or new == held:
        return False
    at = 0
    for piece in new.split(REDACTED):
        found = held.find(piece, at)
        if found < 0:
            return False
        at = found + len(piece)
    return True


def set_aside(root, item_id: str, now: dt.datetime | None = None) -> Path:
    """A hold that never leaves as it is (ASIDE_HOLDS: a scan hold the scan refused whose value is the
    point, no subject, unrecorded, a bad id, a corridor destination) is set aside, held for good
    (`set aside`), off the health line; an unreadable held file is renamed `<id>.md.unreadable`.
    Nothing is deleted, and setting aside only ever keeps something home."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    try:
        source, fm, body = _held_item(root, item_id)
    except UNREADABLE as exc:
        source = root / HELD / f"{item_id}.md"
        if isinstance(exc, (FileNotFoundError, pages.UnsafePath)) or not (source.is_file() or pages.is_link(source)):
            raise
        # unreadable (or a link): renamed off the health line (`*.md`), kept, never shipped
        target = source.with_name(source.name + ".unreadable")
        if target.exists() or pages.is_link(target):
            raise ValueError(f"{item_id}: an unreadable copy is set aside already")
        source.rename(target)
        append_line(root / "log.md", f"- {pages.iso(now)} set aside {item_id} (held, unreadable)")
        return target
    reason = str(fm.get("reason") or "")
    if fm.get("kept") or reason not in ASIDE_HOLDS or (reason == "scan" and _scan_did_not_run(str(fm.get("why") or ""))):
        raise ValueError(f"{item_id} is not a hold that never leaves as it is")
    pages.write(source, dict(fm, reason="set aside", why=f"set aside by the agent ({reason}: {fm.get('why')})"), body,
                root=root)
    append_line(root / "log.md", f"- {pages.iso(now)} set aside {item_id} (held for {reason})")
    return source


def retry_held(root: Path, space: spaceyaml.Space, now: dt.datetime) -> int:
    """Holds that fix themselves go back on their own, every ship, so no scheduled step has to:
    a destination hold once the registry names its subject's home, a signature hold once no
    draft of that name waits (and is set aside, superseded, once the page was signed again and
    queued), a scan hold once the scan is there. Each goes through `requeue`,
    so every gate and the scan judge it again. Returns how many went back."""
    count = 0
    reg = None
    for path in sorted((Path(root) / HELD).glob("*.md")):
        try:
            fm, _ = _read_own(path)
        except UNREADABLE:
            continue
        reason, item_id = str(fm.get("reason") or ""), str(fm.get("id") or "")
        if fm.get("kept") or path.stem != item_id:
            continue
        if reason == "destination":
            try:
                reg = reg or registry_mod.Registry(root, space)
                home = _current_home(reg, fm)
            except Exception:
                home = None
            due = bool(home) and home != space.name
        elif reason == "signature":
            try:   # signed again since and queued or gone: this unsigned copy is done with
                done = (Path(root) / QUEUE / f"{item_id}.md").exists() or released_record(Path(root), item_id)
            except (OSError, UnicodeDecodeError):
                done = False
            if done:
                pages.write(path, dict(fm, reason="superseded", why="signed again and queued"), _read_own(path)[1],
                            root=root)
                append_line(Path(root) / "log.md", f"- {pages.iso(now)} {item_id} held for its signature: "
                                                   "superseded, it was signed again")
                continue
            due = not (Path(root) / LANDING / f"{item_id}.md").exists()
        elif reason == "scan":
            due = _scan_did_not_run(str(fm.get("why") or "")) and scan is not None and hasattr(scan, "scan_paths")
        else:
            due = False
        if not due:
            continue
        try:
            requeue(root, item_id, now=now)
        except (ValueError, OSError, pages.UnsafePath):
            continue
        count += 1
    return count


def append_line(path: Path, line: str) -> None:
    """Append one line: a line break inside it (from an id or a class read from data) becomes a
    space, so a value can never start a log line of its own; never written through a link."""
    pages.append_text(Path(path), " ".join(str(line).splitlines()).rstrip() + "\n")


def append_run_line(root: Path, routine: str, now: dt.datetime, space: spaceyaml.Space,
                    cost_tokens: int, notes: dict) -> None:
    line = {"routine": routine, "at": pages.iso(now), "space": space.name,
            "cost_tokens": int(cost_tokens), "notes": notes}
    append_line(Path(root) / RUNS, json.dumps(line))


# ----- the path pause (vision section 10) ---------------------------------------
def pause(root, path: str | None, now: dt.datetime, reason: str, item: str | None = None,
          space: spaceyaml.Space | None = None) -> dict | None:
    """Pause the path that let an at-once item through (a standing choice, a source's
    audience or the router's judgment for a destination) for four scaled weeks. Lowering is automatic; only a person raises a level."""
    if not path:
        return None
    space = space or spaceyaml.load(root)
    row = {"path": str(path), "at": pages.iso(now), "until": pages.iso(now + space.scale(PAUSE_HOURS)),
           "reason": reason, "item": item}
    append_line(Path(root) / PAUSED, json.dumps(row))
    return row


def router_path(destination: str) -> str:
    return f"router:{destination}"


def path_of(shipped: dict) -> str | None:
    """The path that let a shipped item through. An at-once item that names none was the
    router's judgment, sent before its items carried the path: the router's for that destination."""
    if shipped.get("path"):
        return str(shipped["path"])
    if shipped.get("class") == "at_once" and shipped.get("destination"):
        return router_path(str(shipped["destination"]))
    return None


def is_paused(root, path: str | None, now: dt.datetime) -> bool:
    if not path:
        return False
    target = Path(root) / PAUSED
    if not target.exists():
        return False
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("path") != path:
            continue
        try:
            if pages.parse_iso(row.get("at")) <= now < pages.parse_iso(row.get("until")):
                return True
        except (TypeError, ValueError):
            continue
    return False


def slug_of(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", str(name).lower()).strip("-") or "space"


def branch_slug(space: spaceyaml.Space) -> str:
    """The sender's part of its release branch, `release/<slug>-<8 hex>`: its name's slug and a
    hash of its own repository (its origin remote, else its folder), so two spaces whose names
    fold to one slug never force-push over each other's open pull request. The gate reads the
    slug back (`gate.RELEASE_BRANCH`)."""
    import hashlib
    try:
        import onboard
        remote = onboard.git_remote(space.root)
    except Exception:
        remote = None
    own = registry_mod.repo_url(remote) if remote else None
    key = (own or str(remote or "") or str(Path(space.root).resolve())).strip().lower()
    return f"{slug_of(space.name)}-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:8]}"


# ----- the scrub -------------------------------------------------------------
def scrub_reason(space: spaceyaml.Space, dest_space: spaceyaml.Space | None, fm: dict,
                 body: str, path: Path | None = None) -> tuple[str, str] | None:
    """Why an item may not leave: (reason, why) or None when it is clean. Facts only: what it
    says was judged before it was queued (the router and its check, or a person). The scan
    fails closed: an item it cannot read, or a scan that is missing or fails, holds the item."""
    if dest_space is not None and dest_space.mode != space.mode:
        return "mode", (f"this space runs {space.mode}, the destination "
                        f"{fm.get('destination')} runs {dest_space.mode}")
    if path is None:
        return None
    p = Path(path)
    if scan is None or not hasattr(scan, "scan_paths"):
        return "scan", "the secrets scan is not available, so nothing leaves unscanned"
    try:
        if not p.is_file():
            return "scan", "not a file the secrets scan can read"
        if p.stat().st_size > getattr(scan, "MAX_BYTES", 1024 * 1024):
            return "scan", "too large for the secrets scan to read"
        readable = scan.text_of(p) is not None if hasattr(scan, "text_of") else True
    except Exception:
        readable = False
    if not readable:
        return "scan", "the secrets scan cannot read this item whole (a NUL byte, or not UTF-8)"
    try:
        findings = scan.scan_paths([p])
    except Exception:
        return "scan", "the secrets scan failed on this item"
    if findings:  # never quote a finding
        return "scan", f"secrets scan: {len(findings)} finding(s)"
    return None


# ----- landing -----------------------------------------------------------------
def landed_frontmatter(space: spaceyaml.Space, fm: dict, now: dt.datetime) -> dict:
    out = {k: v for k, v in fm.items() if k not in QUEUE_ONLY_KEYS}
    if out.get("status") != "stable" or not all(out.get(k) for k in pages.SIGNATURE_FIELDS):
        out["status"] = "draft"  # a routine never writes stable; a signed page keeps its own
    out["released_by"] = space.owner or "owner"
    out["released_from"] = space.name
    out["released_at"] = pages.iso(now)
    out["release_class"] = fm.get("class")
    out["acted_for"] = fm.get("approved_by") or space.owner or "owner"
    if fm.get("path"):
        out["path"] = fm.get("path")
    if fm.get("subject") and not out.get("domain"):
        try:
            import registry as registry_mod
            domain = registry_mod.Registry(space.root, space).domain_of(str(fm["subject"]))
            if domain is not None:
                out["domain"] = domain.id
        except Exception:
            pass
    out.setdefault("evidence", [])
    return out


def _landing_path(dest_root: Path, folder: Path, name: str) -> Path:
    """`dest_root/<folder>/<name>`, confined to that folder of the destination (pages.confine):
    never outside it, never through a link."""
    return pages.confine(dest_root, f"{folder.as_posix()}/{name}", zones=(folder.as_posix(),))


def mark_retracted(dest_root: Path, item_id: str) -> bool:
    """Mark the released item `item_id` retracted where it waits at the destination; only a page
    id names one, so `retracts` from a queue file can never name another file."""
    if not pages.is_page_id(item_id):
        return False
    target = _landing_path(Path(dest_root), LANDING, f"{item_id}.md")
    if target.exists():
        fm, body = pages.read(target)
        pages.write(target, dict(fm, retracted=True), body, root=dest_root)
    return target.exists()


def publish(checkout: Path, slug: str, source: str, write) -> bool:
    """The online leg: refresh the checkout, let `write()` add files, land them as a PR.

    Tests monkeypatch this function; everything that touches git or GitHub is here.
    """
    git = ["git", "-C", str(checkout)]
    branch = f"release/{slug}"
    subprocess.run(git + ["fetch", "origin", "--no-recurse-submodules"], check=True)
    listed = subprocess.run(["gh", "pr", "list", "--head", branch, "--state", "open",
                             "--json", "number"], cwd=str(checkout), check=False,
                            capture_output=True, text=True)
    try:
        open_prs = json.loads(listed.stdout or "[]") if listed.returncode == 0 else []
    except ValueError:
        open_prs = []
    # an open pull request keeps what it already carries: the new files go on top of its branch
    has_branch = bool(open_prs) and subprocess.run(
        git + ["rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}"],
        check=False, capture_output=True).returncode == 0
    subprocess.run(git + ["checkout", "-q", "-B", branch, f"origin/{branch}" if has_branch else "origin/main"],
                   check=True)
    write()
    subprocess.run(git + ["add", "--", "_inbound"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "chore: release"], check=True)
    subprocess.run(git + ["push", "-u", "origin", branch, "--force-with-lease"], check=True)
    if not open_prs:
        subprocess.run(["gh", "pr", "create", "--base", "main", "--head", branch, "--title",
                        "chore: release", "--body", f"Released by {source}."],
                       cwd=str(checkout), check=True)
    return True


# ----- ship ----------------------------------------------------------------------
def _current_home(reg, fm: dict) -> str | None:
    """The registry's home for the item's subject today, or None when unknown (a home that is
    not a space name is unknown: nothing is re-addressed to it)."""
    if reg is None or not fm.get("subject"):
        return None
    try:
        home = reg.home_of(str(fm["subject"]))
    except Exception:
        return None
    return home if pages.is_slug(home) else None


def signing_people(root: Path, here: spaceyaml.Space, subject=None) -> set:
    """The people who sign in this space a page about `subject`, exactly as another space's
    landing counts them (`landing.signing_space`): its owner and audience list, and from the
    registry this space trusts the person whose workspace it is and the explicit owners and
    crew of that subject and its umbrellas when it is homed here (a domain owner in the brain, a
    crew member in a project space); the crew of one subject never signs another's page.
    `subject` None: the people of every subject homed here. With the registry unreadable, the
    owner and the audience list only."""
    import landing   # lazily: landing imports this module
    try:
        reg = registry_mod.Registry(root, here)
    except Exception:
        reg = None
    try:
        return landing.signing_space(here, reg, here.name, subject=subject)[1]
    except Exception:
        return {here.owner} | ({str(a) for a in here.audience} if isinstance(here.audience, list) else set())


def _signature_logged(root: Path | None, fm: dict) -> bool:
    """This space's own history (`log.md`, which only the tools write) holds the whole line
    sign.py writes for this signature, `- <ISO> fingerprint <id> <sha256> by <signer>`, the
    signature names this space, and its signer is one of the people who sign here
    (`signing_people`, the set a landing elsewhere checks too). Anyone can compute a body's
    fingerprint; only sign.py, executing a person's verdict, writes that line."""
    if root is None or not pages.is_page_id(fm.get("id")):
        return False
    try:
        here = spaceyaml.load(root)
        if str(fm.get("signed_in") or "") != here.name:
            return False
        people = signing_people(Path(root), here, subject=str(fm.get("subject") or ""))
        if str(fm.get("signed_by") or "") not in people - {""}:
            return False
        text = (Path(root) / "log.md").read_text(encoding="utf-8")
    except Exception:
        return False
    import landing   # lazily: landing imports this module
    if not landing._has_line(text, "fingerprint", fm["id"], fm.get("fingerprint"), fm.get("signed_by")):
        return False
    return not fm.get("signed_fields") or landing._has_line(text, "fields", fm["id"], fm["signed_fields"],
                                                             fm.get("signed_by"))


def _signed(fm: dict, body: str, root: Path | None = None) -> bool:
    """A page a person signed here: stable, with every signature field, the fingerprint the
    body's, and the signature in this space's own history (`_signature_logged`)."""
    return fm.get("status") == "stable" and all(fm.get(k) for k in pages.SIGNATURE_FIELDS) \
        and fm.get("fingerprint") == pages.fingerprint(body) and _signature_logged(root, fm)


def item_problem(fm: dict, body: str, root: Path | None = None) -> tuple[str, str] | None:
    """Why a queue item may not even be considered: (reason, why), or None. Its id names the
    file written at the destination and its destination the folder of its release clone, so
    both must have their real shape; an item about no subject passes no classification gate, so
    only a page a person signed here (a lesson, say) may name none."""
    if not pages.is_page_id(fm.get("id")):
        return "bad id", "its id is not a page id (p- and eight hex digits), so it names no safe file"
    if not pages.is_slug(fm.get("destination")):
        return "destination", "its destination is not a space name"
    if fm.get("class") == "retraction":
        if not pages.is_page_id(fm.get("retracts")):
            return "bad id", "it retracts something that is not a page id"
        return None
    signed = _signed(fm, body, root)
    if not fm.get("subject") and not signed:   # a signature not made here is no signature: no gate is left
        return "no subject", "it names no subject, so no classification can let it go"
    if fm.get("status") == "stable" and not signed:
        return "signature", "signature not made here: a stable item leaves only with a signature this space's log holds"
    return None


def as_retraction(root: Path, fm: dict, now: dt.datetime) -> tuple[dict, str] | None:
    """A retraction as it travels, rebuilt from this space's own release record: its id, the
    release it retracts and where that went, and nothing else from its queue file, whose body
    and other fields stay here. A retraction passes no subject rule and no gate, so it may carry
    no text. None when this space never released that item to that destination."""
    target = str(fm.get("retracts") or "")
    try:
        shipped = released_record(Path(root), target) if pages.is_page_id(target) else None
    except (OSError, UnicodeDecodeError):
        shipped = None
    if shipped is None or str(shipped.get("destination")) != str(fm.get("destination")):
        return None
    subject, kind = shipped.get("subject"), shipped.get("kind")
    stage_after = fm.get("stage_after") if isinstance(fm.get("stage_after"), str) else pages.iso(now)
    rebuilt = {"id": fm["id"], "type": "source", "title": f"Retraction of {target}", "status": "draft",
               "generated": pages.generated("release", now), "class": "retraction",
               "destination": str(fm["destination"]), "subject": subject if pages.is_slug(subject) else None,
               "stage_after": stage_after, "listed": False, "evidence": [],
               "from_space": fm.get("from_space") if pages.is_slug(fm.get("from_space")) else None,
               "kind": kind if pages.is_slug(kind) else "document", "retracts": target}
    return rebuilt, f"retracted {target}\n"


def _read_own(path: Path) -> tuple[dict, str]:
    """A queue, request or answer file as a page; a link there (to a file anywhere else on this
    machine) is no file this space wrote, so it is never read, never ships, and stays put."""
    if pages.is_link(path):
        raise OSError("a link, not a file this space wrote")
    return pages.read(path)


def _ready_items(root: Path, now: dt.datetime, report: ShipReport,
                 space: spaceyaml.Space | None = None) -> list:
    ready = []
    reg = gating = None
    unreadable = False
    if space is not None:
        import classify as gating
        try:
            reg = registry_mod.Registry(root, space)
        except Exception as exc:   # the gate cannot be read: nothing about a subject leaves
            unreadable = True
            print(f"release: the registry cannot be read ({exc.__class__.__name__}): "
                  "items about a subject wait", file=sys.stderr)
    for path in sorted((root / QUEUE).glob("*.md")):
        try:
            fm, body = _read_own(path)
        except UNREADABLE as exc:   # one bad file never stops the others; it never ships
            report.unreadable += 1
            print(f"release: {ascii(path.name)} cannot be read ({exc.__class__.__name__}); it stays "
                  "in the queue and never ships", file=sys.stderr)
            continue
        if not fm.get("id") or not fm.get("destination"):
            continue
        if not pages.is_slug(fm.get("destination")) and fm.get("class") != "retraction":
            # a destination that is no space name goes to its subject's home when the registry
            # names one, as a moved home does below; the gates that follow judge it there
            home = _current_home(reg, fm)
            if home and space is not None and home != space.name:
                fm["destination"] = home
                pages.write(path, fm, body, root=root)
                report.rerouted += 1
        problem = item_problem(fm, body, root)
        if not problem and fm.get("class") == "retraction":
            rebuilt = as_retraction(root, fm, now)
            if rebuilt is None:
                problem = ("unrecorded", "this space never released what it retracts to that destination")
            else:
                fm, body = rebuilt
        if problem and problem[0] == "signature" and sign_again(root, fm, body, now, problem[1]) is not None:
            path.unlink()   # back on the person's list as a draft to sign: no signature is assumed
            report.held += 1
            continue
        if problem:
            hold(root, fm, body, *problem)
            path.unlink()
            report.held += 1
            continue
        if unreadable and fm.get("subject") and fm.get("class") != "retraction":
            report.waiting += 1
            continue
        # a subject that moved home since the item was queued (its space offboarded, say)
        # takes the item along; it never ships to a destination the registry no longer names
        home = _current_home(reg, fm)
        if home and home != fm["destination"] and space is not None and home != space.name:
            fm["rerouted_from"], fm["destination"] = fm["destination"], home
            pages.write(path, fm, body, root=root)
            report.rerouted += 1
        if gating is not None and reg is not None and fm.get("class") != "retraction":
            # the backstop: whatever path queued it, an unclassified subject does not leave
            blocked = gating.gate_all(space, reg, fm.get("subject"), fm.get("also_about"), fm["destination"])
            if blocked:
                hold(root, dict(fm, kept=True) if blocked[0] == "kept" else fm, body, *blocked)
                path.unlink()
                report.held += 1
                continue
            if fm.get("class") == "at_once" and not fm.get("approved_by") and \
                    gating.first_release(root, space, reg, fm.get("subject")):
                why = f"the first release since {fm['subject']} was classified company"
                fm.update({"class": "window", "stage_after": pages.iso(now + space.window()),
                           "first_release": str(fm["subject"]),
                           "why": "; ".join(x for x in (str(fm.get("why") or ""), why) if x)})
                pages.write(path, fm, body, root=root)
                report.waiting += 1
                continue
        try:
            stage_after = pages.parse_iso(fm.get("stage_after") or pages.iso(now))
        except (TypeError, ValueError):
            stage_after = now
        if stage_after > now:
            report.waiting += 1
        elif fm.get("class") not in NEEDS_NO_LISTING and fm.get("listed") is not True:
            report.skipped_unlisted += 1
        elif (space is not None and fm.get("class") not in ("retraction", "signal")
              and not fm.get("approved_by") and not authority.allows(space, "release", "after_window")):
            report.waiting += 1   # the badge says propose: nothing leaves without a person's yes
        else:
            ready.append((path, fm, body))
    return ready


# a registry note goes to the registry that holds its subject; a failed sample goes back to the
# space that released the item: neither carries the subject's material toward a home
UNGATED_SIGNALS = ("registry", "sample_failed")


# What a signal carries (the fields sweep, route, classify and landing write), each in its own shape.
SIGNAL_KEYS = ("id", "kind", "note", "subject", "at", "by", "from", "data", "destination", "item", "rerouted_from")
NOTE_CAP = 200


def signal_as_sent(space: spaceyaml.Space, data: dict) -> dict:
    """A signal as it lands: the fields the tools write, each in its own shape (slugs, page ids,
    an ISO time, one short line for `note` and `by`), `from` always this space, and `data` only
    on a registry note, whose holder checks it (classify.apply_note), and whose `by` is always
    this space's owner, never a value from the file. Anything else stays here."""
    out = {}
    for key in SIGNAL_KEYS:
        value = data.get(key)
        if value is None:
            continue
        if key in ("id", "item"):
            value = value if pages.is_page_id(value) else None
        elif key in ("kind", "subject", "destination", "rerouted_from"):
            value = value if pages.is_slug(value) else None
        elif key == "at":
            try:
                value = pages.iso(pages.parse_iso(value))
            except (TypeError, ValueError):
                value = None
        elif key == "data":
            value = value if data.get("kind") == "registry" and isinstance(value, dict) else None
        elif key != "from":
            value = _one_line(value, NOTE_CAP if key == "note" else NAME_CAP)
        if value is not None:
            out[key] = value
    out["from"] = space.name
    if out.get("kind") == "registry":   # the holder binds a note to its sender: this space's owner
        out["by"] = space.owner
    return out


def _signals(root: Path, now: dt.datetime, report: ShipReport | None = None) -> list:
    """(path, signal, item-shaped view) for every pending signal; the view feeds the scrub. A
    signal that cannot be read stays where it is and never ships."""
    out = []
    for path in sorted((root / SIGNALS).glob("*.yaml")):
        try:
            if pages.is_link(path):
                raise OSError("a link, not a file this space wrote")
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except UNREADABLE as exc:
            if report is not None:
                report.unreadable += 1
            print(f"release: signal {ascii(path.name)} cannot be read ({exc.__class__.__name__}); "
                  "it never ships", file=sys.stderr)
            continue
        if isinstance(data, dict) and data.get("destination"):
            view = {"id": data.get("id"), "type": "source", "title": "signal", "status": "draft",
                    "generated": pages.generated("release", now), "class": "signal",
                    "destination": data["destination"], "subject": data.get("subject"),
                    "stage_after": data.get("at") or pages.iso(now), "listed": False,
                    "evidence": []}
            out.append((path, data, view))
    return out


def ship(root, now: dt.datetime | None = None,
         offline_roots: dict[str, Path] | None = None, notes_only: bool = False) -> ShipReport:
    """`notes_only` ships the pending registry notes and nothing else (offboard's last word)."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    offline_roots = {k: Path(v) for k, v in (offline_roots or {}).items()}
    report = ShipReport()
    if notes_only:
        return _ship_batches(root, space, now, offline_roots, report, notes_only=True)
    try:   # registry notes that arrived here (the brain applies them every sync)
        import classify
        classify.land_notes(root, space, now)
    except Exception as exc:  # a note never stops the ship
        print(f"release: registry notes not applied ({exc.__class__.__name__})", file=sys.stderr)
    report.requeued = _requeue_mode_held(root, space)
    report.requeued += retry_held(root, space, now)
    _recommend_held_doubt(root)
    try:   # what waited for a classification goes back to the queue once it has one
        import classify
        back = classify.requeue(root, space, None, now)
        report.requeued += back["requeued"]
        report.rerouted += back["rerouted"]
    except Exception as exc:  # held items stay held: the safe side
        print(f"release: classification requeue skipped ({exc.__class__.__name__}: {exc})", file=sys.stderr)
    return _ship_batches(root, space, now, offline_roots, report)


def _ship_batches(root: Path, space, now: dt.datetime, offline_roots: dict, report: ShipReport,
                  notes_only: bool = False) -> ShipReport:
    batches: dict = {}
    for path, fm, body in [] if notes_only else _ready_items(root, now, report, space):
        batches.setdefault(fm["destination"], ([], []))[0].append((path, fm, body))
    try:
        reg = registry_mod.Registry(root, space)
    except Exception:
        reg = None
    for path, data, view in _signals(root, now, report):
        if notes_only and data.get("kind") != "registry":
            continue
        # its id names the file written at the destination, its destination the release clone
        if not pages.is_page_id(data.get("id")):
            bad = ("bad id", "its id is not a page id, so it names no safe file")
        elif not pages.is_slug(data.get("destination")):
            bad = ("destination", "its destination is not a space name")
        elif data.get("kind") not in UNGATED_SIGNALS and not pages.is_slug(data.get("subject")):
            bad = ("no subject", "it names no subject, so no classification can let it go")
        else:
            bad = None
        if bad:   # held as a page with the signal inside, never shipped
            hold(root, view, "```yaml\n" + yaml.safe_dump(data, sort_keys=False) + "```\n", *bad)
            path.unlink()
            report.held += 1
            continue
        home = None if data.get("kind") == "registry" else _current_home(reg, data)   # a note goes to its registry
        if home and home != data["destination"] and home != space.name:
            # a signal follows its subject to its current home, like a queue item
            data["rerouted_from"], data["destination"] = data["destination"], home
            view["destination"] = home
            pages.write_text(path, yaml.safe_dump(data, sort_keys=False), root=root)
            report.rerouted += 1
        if data.get("kind") not in UNGATED_SIGNALS and data.get("subject"):
            if reg is None:   # the gate cannot be read: the signal waits
                continue
            import classify
            blocked = classify.gate(space, reg, data.get("subject"), data["destination"])
            if blocked:   # a signal of a held subject waits; one kept home never goes
                if blocked[0] == "kept":
                    path.unlink()
                continue
        batches.setdefault(data["destination"], ([], []))[1].append((path, data, view))
    for destination, (items, signals) in sorted(batches.items()):
        if space.is_corridor(destination):   # only ship_requests writes into a corridor: its other side reads it
            for path, fm, body in items:
                hold(root, fm, body, "corridor", "its destination is a corridor, which only requests and answers enter")
                path.unlink()
            for path, data, view in signals:
                hold(root, view, "```yaml\n" + yaml.safe_dump(data, sort_keys=False) + "```\n", "corridor",
                     "its destination is a corridor, which only requests and answers enter")
                path.unlink()
            report.held += len(items) + len(signals)
            continue
        offline = offline_roots.get(destination)
        checkout = None if offline is not None else ensure_checkout(space, destination)
        if checkout is not None:
            refresh_checkout(checkout)
        dest_root = offline if offline is not None else checkout
        dest_space = destination_space(space, destination, dest_root)
        outgoing_items, outgoing_signals = [], []
        for path, fm, body in items:
            problem = scrub_reason(space, dest_space, fm, body, path)
            if problem and problem[0] == "mode" and space.mode == "live":
                report.waiting += 1   # the destination may be a stale view: wait, never hold
                continue
            if problem:
                hold(root, fm, body, *problem)
                path.unlink()
                report.held += 1
            else:
                outgoing_items.append((path, fm, body))
        for path, data, view in signals:
            problem = scrub_reason(space, dest_space, view, "", path)
            if problem and problem[0] == "mode" and space.mode == "live":
                continue
            if problem:
                hold(root, view, "", *problem)
                path.unlink()
                report.held += 1
            else:
                outgoing_signals.append((path, data))
        if not outgoing_items and not outgoing_signals:
            continue
        if dest_root is None:
            report.unreachable += len(outgoing_items)
            continue

        def write(dest_root=dest_root, items=outgoing_items, signals=outgoing_signals):
            # every file lands inside its `_inbound/` folder and never through a link: a refusal
            # raises before that file is written, and the items stay queued
            for _, fm, body in items:
                pages.write(_landing_path(dest_root, LANDING, f"{fm['id']}.md"),
                            landed_frontmatter(space, fm, now), body, root=dest_root)
                if fm.get("class") == "retraction" and fm.get("retracts"):
                    mark_retracted(dest_root, str(fm["retracts"]))
            for _, data in signals:
                target = _landing_path(dest_root, SIGNAL_LANDING, f"{data['id']}.yaml")
                data = signal_as_sent(space, data)
                pages.write_text(target, yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                                 root=dest_root)

        try:
            if checkout is not None:
                publish(checkout, branch_slug(space), space.name, write)
            else:
                write()
        except (subprocess.CalledProcessError, OSError, pages.UnsafePath) as exc:
            report.failed += len(outgoing_items)
            print(f"release: {destination}: publish failed ({exc.__class__.__name__})",
                  file=sys.stderr)
            continue
        for path, fm, _ in outgoing_items:
            path.unlink()
            report.shipped += 1
            report.destinations[destination] = report.destinations.get(destination, 0) + 1
            report.retracted += fm.get("class") == "retraction"
            append_line(root / "log.md",
                        f"- {pages.iso(now)} released {fm['id']} to {destination} ({fm.get('class')})"
                        + (f" via {fm['path']}" if fm.get("path") else ""))
            append_line(root / RELEASED, json.dumps({
                "id": fm["id"], "at": pages.iso(now), "destination": destination,
                "class": fm.get("class"), "subject": fm.get("subject"),
                "evidence": list(fm.get("evidence") or []), "kind": fm.get("kind"),
                "path": fm.get("path")}))
        for path, _ in outgoing_signals:
            path.unlink()
            report.signals += 1
    if not notes_only:
        ship_requests(root, space, now, offline_roots, report)
    notes = {"shipped": report.shipped, "signals": report.signals, "held": report.held,
             "requests": report.requests,
             "skipped_unlisted": report.skipped_unlisted, "waiting": report.waiting,
             "requeued": report.requeued,
             "unreachable": report.unreachable, "rerouted": report.rerouted, "failed": report.failed,
             "destinations": report.destinations}
    if report.unreadable:   # files that cannot be read: they never ship, and the run line says so
        notes["unreadable"] = report.unreadable
    append_run_line(root, "release", now, space, 0, notes)
    return report


# ----- the fourth wire: requests and answers through the corridor -----------------------
REQUESTS_OUT = Path("_outbound") / "requests"
ANSWERS_OUT = Path("_outbound") / "answers"


def _corridor_root(space, name: str, offline_roots: dict):
    if name in offline_roots:
        return offline_roots[name], None
    checkout = ensure_checkout(space, name)
    return checkout, checkout


def ship_requests(root: Path, space: spaceyaml.Space, now: dt.datetime, offline_roots: dict,
                  report: ShipReport) -> None:
    """Requests and answers travel through the corridor between the two spaces, at once. A
    request carries its question and nothing else in its file, and only one request.py asked
    (its `asked` line in requests-asked.jsonl); an answer leaves only when this space's request
    log records that it answered that asker. Both are scanned for secrets like any release, and
    an id that is not a request id, or a peer that is not a space name, holds the file."""
    try:
        import request as rq
    except ImportError:
        return
    asked = {(str(r.get("id")), str(r.get("to"))) for r in registry_mod._jsonl(root / rq.ASKER_LOG)
             if r.get("outcome") == "asked"}
    answered = {str(r.get("id")): r for r in registry_mod._jsonl(root / rq.HOLDER_LOG)
                if r.get("outcome") == "answered"}
    batches: dict = {}
    for folder, kind in ((REQUESTS_OUT, "request"), (ANSWERS_OUT, "answer")):
        for path in sorted((root / folder).glob("*.md")) if (root / folder).is_dir() else []:
            try:
                fm, body = _read_own(path)
            except UNREADABLE as exc:
                report.unreadable += 1
                print(f"release: {kind} {ascii(path.name)} cannot be read ({exc.__class__.__name__}); "
                      "it never ships", file=sys.stderr)
                continue
            if kind == "request" and fm.get("status") != "open":
                continue
            other = str(fm.get("to") or "")
            problem = _corridor_problem(kind, fm, body, other, asked, answered)
            if problem:
                hold(root, dict(fm, destination=other), body, *problem)
                path.unlink()
                report.held += 1
                continue
            cname, _ = rq.corridor(space, other, offline_roots)
            if cname is None:
                report.unreachable += 1
                continue
            batches.setdefault(cname, []).append((kind, path, fm, body))
    for cname, items in sorted(batches.items()):
        dest_root, checkout = _corridor_root(space, cname, offline_roots)
        if dest_root is None:
            report.unreachable += len(items)
            continue
        dest_space = destination_space(space, cname, dest_root)
        outgoing = []
        for kind, path, fm, body in items:
            # a request is scanned too; the mode rule is a release's, an answer's
            problem = scrub_reason(space, dest_space if kind == "answer" else None,
                                   dict(fm, destination=cname), body, path)
            if problem:
                hold(root, dict(fm, destination=cname), body, *problem)
                path.unlink()
                report.held += 1
                continue
            sent = _request_as_sent(space, fm) if kind == "request" else _answer_as_sent(space, fm, body)
            outgoing.append((kind, path, fm, body, sent))
        if not outgoing:
            continue

        def write(dest_root=dest_root, outgoing=outgoing):
            for kind, _, _, _, (sent_fm, sent_body) in outgoing:
                folder = REQUEST_LANDING if kind == "request" else ANSWER_LANDING
                pages.write(_landing_path(Path(dest_root), folder, f"{sent_fm['id']}.md"), sent_fm, sent_body,
                            root=dest_root)

        try:
            if checkout is not None:
                publish(checkout, branch_slug(space), space.name, write)
            else:
                write()
        except (subprocess.CalledProcessError, OSError, pages.UnsafePath) as exc:
            report.failed += len(outgoing)
            print(f"release: {ascii(cname)}: publish failed ({exc.__class__.__name__})", file=sys.stderr)
            continue
        for kind, path, fm, body, _ in outgoing:
            if kind == "request":
                pages.write(path, dict(fm, status="shipped", shipped_at=pages.iso(now), corridor=cname), body,
                            root=root)
            else:
                path.unlink()
            report.requests += 1
            append_line(root / "log.md", f"- {pages.iso(now)} {kind} {fm['id']} through {cname}")


REQUEST_LANDING = Path("_inbound") / "requests"
ANSWER_LANDING = Path("_inbound") / "answers"


def _corridor_problem(kind: str, fm: dict, body: str, other: str, asked: set, answered: dict):
    """Why a request or an answer may not go into the corridor: (reason, why), or None."""
    if not is_request_id(fm.get("id")):
        return "bad id", "its id is not a request id (rq- and eight hex digits), so it names no safe file"
    if not pages.is_slug(other):
        return "destination", "the space it is for is not a space name"
    if kind == "request" and (fm["id"], other) not in asked:
        return "unrecorded", f"request.py did not ask {other} this (no `asked` line in requests-asked.jsonl)"
    if kind == "answer":
        row = answered.get(fm["id"])
        if row is None or str(row.get("from")) != other:
            return "unrecorded", f"this space's request log records no answer to {other} for it"
        if row.get("sha256") and row["sha256"] != pages.fingerprint(body):
            return "unrecorded", "the answer changed after it was given"
    return None


def _one_line(value, cap: int) -> str | None:
    """A scalar from a corridor file as one line of at most `cap` characters, or None for
    anything else (a list, a mapping, nothing): no field carries more than its own shape."""
    if value is None or isinstance(value, (dict, list, tuple, set, bool)):
        return None
    text = " ".join(str(value).split())[:cap]
    return text or None


def _corridor_field(key: str, value):
    """One whitelisted field of a request or an answer, in its own shape, or None (dropped):
    names of spaces, subjects, classes and profiles are slugs, times are ISO times, names and
    the narrowest form are one short line. Free text in any other field never travels."""
    if key in SLUG_KEYS:
        return value if pages.is_slug(value) else None
    if key in TIME_KEYS:
        try:
            return pages.iso(pages.parse_iso(value))
        except (TypeError, ValueError):
            return None
    if key == "covered":
        return value if isinstance(value, bool) else None
    return _one_line(value, NAME_CAP if key in NAME_KEYS else QUESTION_CAP)


def _request_as_sent(space: spaceyaml.Space, fm: dict) -> tuple[dict, str]:
    """What a request carries into the corridor: its whitelisted fields, each in its own shape
    (`_corridor_field`), `from` this space, and its question on one line, at most QUESTION_CAP
    characters, as title and body. Whatever else its file holds stays here."""
    question = " ".join(str(fm.get("question") or "").split())[:QUESTION_CAP]
    sent = {k: v for k in REQUEST_KEYS if k in fm and k not in FIXED_KEYS
            for v in [_corridor_field(k, fm[k])] if v is not None}
    sent.update({"id": fm["id"], "type": "request", "status": "open", "from": space.name, "question": question,
                 "title": f"Request to {sent.get('to')}: {question[:80]}",
                 "generated": {"by": "request", "at": sent.get("at") or pages.iso(space.now())}})
    order = ["id", "type", "title"]   # the order request.py writes, so a page reads the same
    return {**{k: sent[k] for k in order if k in sent}, **{k: v for k, v in sent.items() if k not in order}}, \
        f"{question}\n"


def _answer_as_sent(space: spaceyaml.Space, fm: dict, body: str) -> tuple[dict, str]:
    """What an answer carries into the corridor: the fields request.py writes, each in its own
    shape, `from` this space, and its body, which the request log's sha256 binds when it has one."""
    sent = {k: v for k in ANSWER_KEYS if k in fm and k not in FIXED_KEYS
            for v in [_corridor_field(k, fm[k])] if v is not None}
    sent.update({"id": fm["id"], "type": "source", "title": f"Answer to {fm['id']}", "status": "draft",
                 "from": space.name, "request": fm["id"], "destination": None,
                 "generated": {"by": "request", "at": sent.get("answered_at") or pages.iso(space.now())}})
    order = ["id", "type", "title", "status", "generated"]
    return {**{k: sent[k] for k in order if k in sent}, **{k: v for k, v in sent.items() if k not in order}}, body


# ----- retract and pull back -------------------------------------------------
def released_record(root: Path, item_id: str) -> dict | None:
    """What this space knows about a shipped item: released.jsonl first, the log as fallback."""
    found = None
    released = root / RELEASED
    if released.exists():
        for line in released.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("id") == item_id:
                found = entry
    log = root / "log.md"
    if found is None and log.exists():
        pattern = re.compile(r"released " + re.escape(item_id) + r" to (\S+) \((\w+)\)")
        for match in filter(None, map(pattern.search, log.read_text(encoding="utf-8").splitlines())):
            found = {"id": item_id, "destination": match.group(1), "class": match.group(2)}
    return found


def retract(root, item_id: str, now: dt.datetime | None = None,
            offline_roots: dict[str, Path] | None = None) -> Path:
    """Queue a retraction of a shipped item; it leaves at once and needs no listing. Only a page
    id names one (UnsafePath otherwise). Refused inside a routine (`_refuse_in_routine`)."""
    _refuse_in_routine()
    pages.check_page_id(item_id, what="item id")
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    shipped = released_record(root, item_id)
    if shipped is None:
        raise FileNotFoundError(f"{item_id} was not released from this space (see log.md)")
    destination = shipped["destination"]
    fm = {"id": pages.new_id(), "type": "source", "title": f"Retraction of {item_id}",
          "status": "draft", "generated": pages.generated("release", now), "class": "retraction",
          "destination": destination, "subject": shipped.get("subject"),
          "stage_after": pages.iso(now), "listed": False, "evidence": [],
          "from_space": space.name, "kind": shipped.get("kind") or "document",
          "retracts": item_id}
    path = pages.write(root / QUEUE / f"{fm['id']}.md", fm, f"retracted {item_id}\n")
    dest_root = (offline_roots or {}).get(destination)
    if dest_root is not None:
        mark_retracted(Path(dest_root), item_id)
    append_line(root / "log.md", f"- {pages.iso(now)} retraction queued for {item_id}")
    if shipped.get("class") == "at_once" and path_of(shipped):
        pause(root, path_of(shipped), now, "pulled back", item_id, space)
    return path


def _refuse_in_routine() -> None:
    """ValueError inside a routine or a cloud run (`routine.in_routine`): a retraction or a pull-back is the
    person's word (the release skill), whatever command reached it. sign.py's in-process retract runs in theirs."""
    import routine   # here and not at the top: only the person's word asks it
    running = routine.in_routine()
    if running:
        raise ValueError(f"refused: {running}; a routine never withdraws what a person signed or released (rules.md section 1)")


def pull_back(root, item_id: str) -> Path:
    """Move a queue item to held, reason pulled; only a page id names one (UnsafePath otherwise).
    Refused inside a routine (`_refuse_in_routine`)."""
    _refuse_in_routine()
    pages.check_page_id(item_id, what="item id")
    root = Path(root)
    source = root / QUEUE / f"{item_id}.md"
    if not source.exists():
        raise FileNotFoundError(f"{item_id} is not in the queue")
    fm, body = pages.read(source)
    target = hold(root, fm, body, "pulled", "pulled back by the owner")
    source.unlink()
    if fm.get("path") and not str(fm["path"]).startswith("approved:"):
        space = spaceyaml.load(root)
        pause(root, fm["path"], space.now(), "pulled back", item_id, space)
    return target


# ----- CLI -----------------------------------------------------------------------
def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    offline: dict = {}
    positional = []
    while argv:
        arg = argv.pop(0)
        if arg == "--offline" and argv and "=" in argv[0]:
            name, _, path = argv.pop(0).partition("=")
            offline[name] = Path(path)
        else:
            positional.append(arg)
    root = Path(positional.pop(0)) if positional and positional[0] not in COMMANDS else Path(".")
    if not positional:
        print(__doc__)
        return 2
    command, args = positional[0], positional[1:]
    try:
        if command == "ship":
            report = ship(root, offline_roots=offline)
            print(f"shipped {report.shipped}, signals {report.signals}, held {report.held}, "
                  f"waiting {report.waiting}, unlisted {report.skipped_unlisted}, "
                  f"unreachable {report.unreachable}, failed {report.failed}"
                  + (f", unreadable {report.unreadable}" if report.unreadable else ""))
            return 1 if report.failed else 0
        if command == "retract" and args:
            print(f"queued {retract(root, args[0], offline_roots=offline)}")
            return 0
        if command == "pull-back" and args:
            print(f"held {pull_back(root, args[0])}")
            return 0
        if command == "requeue" and args:
            body = args[args.index("--body") + 1] if "--body" in args[:-1] else None
            print(f"requeued {requeue(root, args[0], body_file=body)}")
            return 0
        if command == "set-aside" and args:
            print(f"set aside {set_aside(root, args[0])}")
            return 0
    except (FileNotFoundError, ValueError, pages.UnsafePath) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
