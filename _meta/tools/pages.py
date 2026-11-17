"""Pages: frontmatter, permanent ids, the approval fingerprint and safe paths (shape 1).

A page is a markdown file with a YAML frontmatter block. Every page carries a
permanent `id`; links point at ids (`[text](id:p-1a2b3c4d)`), so moves never break
them. A signed page (`status: stable`) carries `signed_by`, `signed_at`,
`signed_in` and `fingerprint`, the sha256 of the exact body that was approved.

This module is also the one path-safety layer the tools share, because ids, names and
paths reach the tools from data: a feed record, a queue file, another space's release.

- `is_slug`/`check_slug`, `is_page_id`/`check_page_id` and `is_record_id`/`check_record_id`
  test an id against its real shape; `record_file_name` turns a record id into the file
  name its witness page carries.
- `is_safe_name`/`check_name` test one file or folder name built from data: no separator,
  no `.` or `..`, no control character, no colon (a Windows stream), no trailing dot or
  space (Windows drops them, so `rules.md.` is `rules.md`), no reserved device name.
- `confine(root, rel)` turns a repository-relative path from data into a path under root,
  or raises `UnsafePath`: never absolute, never a drive or a share, never a backslash,
  never `.git`, never through a link, always inside root once resolved, and, on request,
  inside the zones named and outside what the gate guards (`guarded_reason`; compared
  ignoring case, as the file systems of Windows and macOS do).
- `write`, `write_text` and `append_text` never write through a symbolic link or a
  junction (`check_no_links`, `is_link`): a link at the destination, or at any folder
  between the space's root and the destination, raises `UnsafePath` and nothing is
  written. The root is the one a caller gives, else the outermost folder above the path
  that holds a space.yaml. A repository can carry a link, and a write through one lands
  outside the space (`~/.ssh/authorized_keys`).
- `copy_of`/`refuse_copy` tell a read-only copy of another space from a checkout: a mount, or a
  nested git folder nobody listed. `write`, `write_text` and `append_text` never write into one,
  and a tool that writes by other means asks `refuse_copy` of its root first. Reading one is fine.

`UnsafePath` is a `ValueError`, so a caller that refuses bad data on `ValueError` refuses
an unsafe path the same way. A refused path is never written.
"""
from __future__ import annotations

import datetime as dt
import errno
import functools
import hashlib
import os
import re
import secrets
import stat
import sys
import unicodedata
from pathlib import Path, PurePath

import yaml

FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.S)
# Anchored with \Z, not $: `$` also matches before a final newline, so an id with a line
# break after it would pass.
ID_RE = re.compile(r"^p-[0-9a-f]{8}\Z")
ID_LINK_RE = re.compile(r"\]\(id:([A-Za-z0-9_.-]+)\)")

PAGE_TYPES = ("concept", "entity", "source", "decision", "directive", "process",
              "policy", "snapshot", "review", "status")
STATUSES = ("draft", "stable", "deprecated")
TIERS = ("signed", "system", "pending", "witness", "reported", "working")
REQUIRED = ("id", "type", "title", "status", "generated")
SIGNATURE_FIELDS = ("signed_by", "signed_at", "fingerprint")
RELEASE_CLASSES = ("at_once", "window", "never", "doubt", "signal", "retraction")


def iso(moment: dt.datetime) -> str:
    moment = moment.astimezone(dt.timezone.utc).replace(microsecond=0)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day, tzinfo=dt.timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def new_id() -> str:
    return "p-" + secrets.token_hex(4)


def normalize(body: str) -> str:
    return body.replace("\r\n", "\n").rstrip() + "\n"


def fingerprint(body: str) -> str:
    """sha256 of the normalized body: what a person approved, independent of line endings."""
    return hashlib.sha256(normalize(body).encode("utf-8")).hexdigest()


def split(text: str) -> tuple[dict, str]:
    match = FM_RE.match(text)
    if not match:
        return {}, text
    data = yaml.safe_load(match.group(1)) or {}
    if not isinstance(data, dict):
        data = {}
    return data, text[match.end():]


def render(fm: dict, body: str) -> str:
    head = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=100,
                          default_flow_style=None).rstrip("\n")
    return f"---\n{head}\n---\n{normalize(body)}"


def read(path) -> tuple[dict, str]:
    return split(Path(path).read_text(encoding="utf-8"))


def write(path, fm: dict, body: str, *, root=None) -> Path:
    """Write a page, never through a link: `write_text` says how the root is found and what
    is refused."""
    return write_text(path, render(fm, body), root=root)


def generated(by: str, now: dt.datetime) -> dict:
    return {"by": by, "at": iso(now)}


def id_links(body: str) -> list:
    return ID_LINK_RE.findall(body)


def window_days(fm: dict, windows: dict) -> int | None:
    """The page's freshness window: review_every, else the default for its type."""
    own = fm.get("review_every")
    if own:
        try:
            return int(str(own).rstrip("d").strip())
        except ValueError:
            return None
    return windows.get(str(fm.get("type", "")))


def age_days(fm: dict, now: dt.datetime) -> float | None:
    """Days since the page was last verified (verified_at, signed_at or generated.at)."""
    stamp = fm.get("verified_at") or fm.get("signed_at")
    if not stamp:
        gen = fm.get("generated") or {}
        stamp = gen.get("at") if isinstance(gen, dict) else None
    if not stamp:
        return None
    return (now - parse_iso(stamp)).total_seconds() / 86400


def is_page_path(path: Path) -> bool:
    """A page file: `.md`, and not a hub index, a log or a readme, whatever the case of its name
    (on Windows and macOS `INDEX.md` is the hub `index.md`)."""
    return path.suffix == ".md" and path.name.casefold() not in ("index.md", "log.md", "readme.md")


def withdrawn(fm: dict) -> bool:
    """A release its sender retracted, or one withdrawn at its home: kept, no longer evidence."""
    return bool(fm.get("retracted") or fm.get("retracted_at"))


# ----- safe ids, names and paths ---------------------------------------------
# Ids, names and paths reach the tools from data (a feed record, a queue file, a release
# another space wrote), and a path built from one decides where a write lands. Each check
# below refuses what the id or path's real shape never holds, so a value from data cannot
# name a file outside the place its caller meant.

class UnsafePath(ValueError):
    """An id, a name or a path from data that the tools refuse to use, with the reason."""


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\Z")
MAX_SLUG = 100
MAX_RECORD_ID = 1024
MAX_NAME = 200
MAX_REL = 1024
# Windows opens a device, not a file, for these names whatever their extension (`nul.md`,
# `COM1 .txt`), in every folder.
WINDOWS_RESERVED = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$"}
                             | {f"{dev}{n}" for dev in ("com", "lpt") for n in "0123456789¹²³"})
# Characters no Windows file name holds; `:` would also name an alternate data stream.
NAME_FORBIDDEN = frozenset('/\\:<>"|?*')
# Never a part of a path from data, guarded or not: a write into `.git/` can plant a hook.
ALWAYS_REFUSED = (".git",)
# Instruction and repository files read at any depth (Codex reads a nested AGENTS.md, git a
# nested `.gitattributes`), held in addition to the gate's own ANY_DEPTH list.
GUARDED_ANY_DEPTH = (".git", "AGENTS.md", ".gitattributes", ".gitmodules")
# The reparse tags of a link on Windows: a junction, a symbolic link, and a link WSL made.
# Not the bare reparse-point bit, which OneDrive's files on demand carry too.
LINK_TAGS = (getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003),
             getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C),
             0xA000001D)   # IO_REPARSE_TAG_LX_SYMLINK
SPACE_MARKER = "space.yaml"
_HERE = Path(__file__).resolve().parent


def _shown(value) -> str:
    """The value for an error message: escaped, so a newline in data cannot start a line."""
    text = ascii(value)
    return text if len(text) <= 90 else text[:87] + "..."


def _bad_char(ch: str) -> bool:
    """A control character, or a line or paragraph separator: what starts a line of its own."""
    return unicodedata.category(ch) in ("Cc", "Zl", "Zp")


def is_slug(value) -> bool:
    """A lowercase slug, the shape of every subject, kind, workspace and effort id."""
    return isinstance(value, str) and len(value) <= MAX_SLUG and bool(SLUG_RE.match(value))


def check_slug(value, what: str = "id") -> str:
    if not is_slug(value):
        raise UnsafePath(f"{what} {_shown(value)} is not a lowercase slug")
    return value


def is_page_id(value) -> bool:
    """A permanent page id, `p-` and eight hex digits."""
    return isinstance(value, str) and bool(ID_RE.match(value))


def check_page_id(value, what: str = "page id") -> str:
    if not is_page_id(value):
        raise UnsafePath(f"{what} {_shown(value)} is not a page id (p- and eight hex digits)")
    return value


def is_record_id(value) -> bool:
    """A record id as the adapters write it (`gmail-<hex>`, `slack-<channel>-<ts>`,
    `gmail-<hex>/<attachment name>`): free text, so it may hold a slash or a space, but never
    a control character or a line break, and never longer than MAX_RECORD_ID. It is never a
    file name as it stands: `record_file_name` makes one."""
    return (isinstance(value, str) and 0 < len(value) <= MAX_RECORD_ID and value.strip() != ""
            and not any(_bad_char(ch) for ch in value))


def check_record_id(value, what: str = "record id") -> str:
    if not is_record_id(value):
        raise UnsafePath(f"{what} {_shown(value)} is empty, too long or holds a control character")
    return value


def _reserved(name: str) -> bool:
    return name.split(".", 1)[0].rstrip(" ").casefold() in WINDOWS_RESERVED


def record_file_name(record_id) -> str:
    """A record id as a file name every system and every link accepts (no colon, slash, hash,
    control character or other reserved sign, never a device name), short enough for every
    path limit: a long id keeps its start and a hash of the whole. It is the name sweep's
    `file_name` gives every id that holds neither a control character nor a device name, so
    a witness page already on disk keeps its path."""
    name = re.sub(r'[<>:"/\\|?*#%\s\x00-\x1f\x7f-\x9f]+', "-", str(record_id)).strip("-.") or "record"
    if len(name) > 80:
        name = f"{name[:68].rstrip('-.')}-{hashlib.sha1(str(record_id).encode('utf-8')).hexdigest()[:10]}"
    if _reserved(name):
        name = f"record-{name}"
    return name


def name_problem(name, max_len: int = MAX_NAME) -> str | None:
    """Why `name` is not a safe file or folder name, or None when it is."""
    if not isinstance(name, str):
        return "is not text"
    if not name:
        return "is empty"
    if len(name) > max_len:
        return f"is longer than {max_len} characters"
    if name in (".", ".."):
        return "names a folder alias"
    if any(_bad_char(ch) for ch in name):
        return "holds a control character"
    if any(ch in NAME_FORBIDDEN for ch in name):
        return "holds a separator, a colon or a sign Windows refuses"
    if name != name.strip() or name.endswith("."):
        return "starts or ends with a space, or ends with a dot (Windows drops them)"
    if _reserved(name):
        return "is a Windows device name"
    return None


def is_safe_name(name, max_len: int = MAX_NAME) -> bool:
    """One file or folder name built from data that is safe on every system."""
    return name_problem(name, max_len) is None


def check_name(name, what: str = "file name", max_len: int = MAX_NAME) -> str:
    problem = name_problem(name, max_len)
    if problem:
        raise UnsafePath(f"{what} {_shown(name)} {problem}")
    return name


def _under(rel: str, prefix: str) -> bool:
    """`rel` is the prefix's path or inside it, ignoring case (as gate._under matches it)."""
    rel, bare = rel.casefold(), prefix.replace("\\", "/").casefold().rstrip("/")
    return rel == bare or rel.startswith(bare + "/")


def _gate_lists() -> tuple[tuple, tuple]:
    """The gate's protected minimum and its at-any-depth names. Read from gate.py when asked
    (gate imports this module), so the two lists keep one source."""
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    import gate  # noqa: E402  (late on purpose: gate imports pages)
    return tuple(gate.MINIMUM_PROTECTED), tuple(gate.ANY_DEPTH)


def guarded_reason(rel, protected=()) -> str | None:
    """Why a repository-relative path is one the gate guards (the protected minimum, the
    `protected` prefixes given, or a name read at any depth), or None. Ignores case."""
    rel = str(rel).replace("\\", "/")
    minimum, any_depth = _gate_lists()
    for prefix in [*(str(p) for p in protected), *minimum]:
        if _under(rel, prefix):
            return f"is under `{prefix}`, which the gate guards"
    names = {n.casefold() for n in (*any_depth, *GUARDED_ANY_DEPTH)}
    hit = next((part for part in rel.split("/") if part.casefold() in names), None)
    return f"holds `{hit}`, which is guarded at any depth" if hit else None


def _check_rel(rel: str, zones, guarded: bool, protected, what: str) -> None:
    if zones is not None and not any(_under(rel, str(zone)) for zone in zones):
        raise UnsafePath(f"{what} {_shown(rel)} is outside {', '.join(map(str, zones)) or 'every zone'}")
    if guarded:
        reason = guarded_reason(rel, protected)
    else:
        reason = next((f"is under `{p}`, which is protected" for p in protected if _under(rel, str(p))), None)
    if reason:
        raise UnsafePath(f"{what} {_shown(rel)} {reason}")


def confine(root, rel, *, zones=None, guarded: bool = False, protected=(), what: str = "path") -> Path:
    """`root / rel` for a repository-relative path that came from data, or UnsafePath.

    Refused: anything but text (a PurePath is read as its posix form); an empty, absolute,
    drive (`C:`), share (`//host`) or backslashed path; a part that is empty, `.`, `..` or
    not a safe name (`check_name`: a control character, a colon, a trailing dot or space, a
    device name); a `.git` part; a link at any part that exists; a path that resolves
    outside root. With `zones`, the path must be one of those prefixes or inside one
    (`("knowledge/",)`); with `guarded`, it must not be one the gate guards
    (`guarded_reason`); any `protected` prefix given is refused too. Zones and guards are
    compared ignoring case, on the path as written and on the path the file system resolves
    (a short 8.3 name such as `CLAUDE~1` is `.claude` on Windows)."""
    if isinstance(rel, PurePath):
        rel = rel.as_posix()
    if not isinstance(rel, str) or not rel:
        raise UnsafePath(f"{what} {_shown(rel)} is empty or not text")
    if len(rel) > MAX_REL:
        raise UnsafePath(f"{what} {_shown(rel)} is longer than {MAX_REL} characters")
    if "\\" in rel:
        raise UnsafePath(f"{what} {_shown(rel)} holds a backslash")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise UnsafePath(f"{what} {_shown(rel)} is absolute, a drive or a share")
    for part in rel.split("/"):
        check_name(part, what=f"{what} {_shown(rel)}: part")
        if part.casefold() in ALWAYS_REFUSED:
            raise UnsafePath(f"{what} {_shown(rel)} reaches into `{part}`")
    _check_rel(rel, zones, guarded, protected, what)
    base = Path(root)
    path = base / rel
    check_no_links(path, root=base)
    resolved = path.resolve().relative_to(base.resolve()).as_posix()
    if resolved.casefold() != rel.casefold():
        if any(part.casefold() in ALWAYS_REFUSED for part in resolved.split("/")):
            raise UnsafePath(f"{what} {_shown(rel)} resolves into `.git`")
        _check_rel(resolved, zones, guarded, protected, what)
    return path


def _link_stat(st) -> bool:
    return stat.S_ISLNK(st.st_mode) or getattr(st, "st_reparse_tag", 0) in LINK_TAGS


def is_link(path) -> bool:
    """A symbolic link or a junction, read without following it; a missing path is none.
    Python 3.11 reports a junction as no link at all, so its reparse tag decides."""
    try:
        return _link_stat(os.lstat(path))
    except (OSError, ValueError):
        return False


def space_root_of(path) -> Path | None:
    """The outermost folder above `path` that holds a space.yaml, as written (not resolved),
    or None. The outermost, so a link below the root cannot pass itself off as a root by
    pointing at a folder that holds a space.yaml of its own; a mount is a folder inside its
    space here, never a root of its own."""
    found = None
    for parent in Path(path).absolute().parents:
        try:
            if os.path.isfile(parent / SPACE_MARKER):
                found = parent
        except (OSError, ValueError):
            continue
    return found


def _stamp(path: Path):
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return st.st_mtime_ns, st.st_size


@functools.lru_cache(maxsize=64)
def _copies(space: str, marker, modules) -> tuple:
    """((folder, name), ...): the copies of other spaces the space at `space` holds, by its
    .gitmodules and by `mounts:` in its space.yaml (placed as `spaceyaml.Space.mount_root` places
    them). `marker` and `modules` are the two files' time and size: a changed file is read again."""
    root, found = Path(space), {}
    try:
        text = (root / ".gitmodules").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for rel in re.findall(r"^\s*path\s*=\s*(.+?)\s*$", text, re.M):
        found[(root / rel).resolve()] = PurePath(rel).name
    try:
        data = yaml.safe_load((root / SPACE_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        data = None
    for mount in (data.get("mounts") or []) if isinstance(data, dict) else []:
        if isinstance(mount, dict) and mount.get("name"):
            name = str(mount["name"])
            rel = str(mount.get("path") or ("brain" if name == "brain" else f"mounts/{name}"))
            found[(root / rel).resolve()] = name
    return tuple(found.items())


def copy_of(path) -> str | None:
    """The name of the space `path` is in a read-only copy of, or None.

    A folder that a space above it lists, as a mount in its space.yaml or as a path in its
    .gitmodules, is a copy, and so is everything under it: the name is the mount's. Else the
    nearest folder at or above `path` that holds a `.git` decides: a `.git` file whose gitdir
    has no `commondir` is a submodule nobody listed, a copy too (its folder's name); a session's
    worktree always has a `commondir`, and a `.git` folder is a checkout of its own. A mount kept
    outside the space that lists it (a checkout beside it) is no copy: only spaces above are asked.

    Git fetches a copy and nothing commits what is written there, so a write into one never
    reaches the space it belongs to and stops the copy from following that space."""
    path = Path(path).resolve()
    for space in path.parents:
        marker = _stamp(space / SPACE_MARKER)
        if marker is None:
            continue
        for folder, name in _copies(str(space), marker, _stamp(space / ".gitmodules")):
            if path == folder or folder in path.parents:
                return name
    for folder in (path, *path.parents):
        git = folder / ".git"
        try:
            mode = os.lstat(git).st_mode
        except (OSError, ValueError):
            continue
        if stat.S_ISREG(mode):
            # shortcut: a clone made with `git clone --separate-git-dir` reads as a copy too; tell
            # the two apart when someone sets a space up that way
            try:
                line = git.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                line = ""
            if line.startswith("gitdir:"):
                gitdir = Path(line[len("gitdir:"):].strip())
                gitdir = gitdir if gitdir.is_absolute() else folder / gitdir
                if gitdir.is_dir() and not (gitdir / "commondir").exists():
                    return folder.name
        return None
    return None


def refuse_copy(path) -> None:
    """UnsafePath when `path` is in a read-only copy of another space (`copy_of`). The writers of
    this module ask it of every destination; a tool that also writes by other means (a raw file
    write, a move, a delete) asks it of its root before it starts. A verdict on another space's
    item is given in that space's own checkout."""
    name = copy_of(path)
    if name:
        raise UnsafePath(f"{_shown(str(path))} is in a read-only copy of {_shown(name)}: nothing is "
                         "written there; work in that space's own checkout (`chain.py checkout` finds it)")


def check_no_links(path, *, root=None) -> Path:
    """`path` when nothing between root and it is a link, else UnsafePath.

    Root is the one given, else the outermost folder above the path that holds a
    space.yaml. Refused: a path outside root, a `..` below root, a link (or junction) at
    the path or at any folder between root and it that exists, and a path that resolves
    outside root. A link at root or above it is the person's own setup and is allowed.
    With no root given and no space.yaml above the path, only the path itself is checked.
    It asks nothing about copies: reading a mount's files behind this check is fine, and the
    writers ask `refuse_copy` themselves."""
    path = Path(path)
    dest = path.absolute()
    base = Path(root).absolute() if root is not None else space_root_of(dest)
    if base is None:
        if is_link(dest):
            raise UnsafePath(f"{_shown(str(path))} is a link; nothing is written through one")
        return path
    if dest != base and base not in dest.parents:
        raise UnsafePath(f"{_shown(str(path))} is outside {_shown(str(base))}")
    current = base
    for part in dest.parts[len(base.parts):]:
        if part == "..":
            raise UnsafePath(f"{_shown(str(path))} climbs out with `..`")
        current = current / part
        try:
            st = os.lstat(current)
        except (OSError, ValueError):
            break   # missing from here on: what mkdir creates is no link
        if _link_stat(st):
            raise UnsafePath(f"{_shown(current.relative_to(base).as_posix())} is a link; "
                             "nothing is written through one")
    base_real, dest_real = base.resolve(), dest.resolve()
    if dest_real != base_real and base_real not in dest_real.parents:
        raise UnsafePath(f"{_shown(str(path))} resolves outside {_shown(str(base))}")
    return path


def _open_no_follow(path: Path, append: bool, newline: str | None):
    """Open for writing without following a link at the path itself (O_NOFOLLOW where the
    system has it), in binary at the descriptor on Windows, so the C runtime never turns a
    newline into two characters behind `newline`'s back."""
    flags = (os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        fd = os.open(path, flags, 0o666)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, getattr(errno, "EMLINK", -1)):
            raise UnsafePath(f"{_shown(str(path))} is a link; nothing is written through one") from exc
        raise
    return open(fd, "a" if append else "w", encoding="utf-8", newline=newline)


def write_text(path, text: str, *, root=None, newline: str | None = "\n") -> Path:
    """Write text to a file, creating its folders, never through a link (`check_no_links`)
    and never into a read-only copy of another space (`refuse_copy`), whatever root the caller
    names: either raises UnsafePath and nothing is written."""
    path = Path(path)
    check_no_links(path, root=root)
    refuse_copy(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _open_no_follow(path, append=False, newline=newline) as handle:
        handle.write(text)
    return path


def append_text(path, text: str, *, root=None, newline: str | None = "\n") -> Path:
    """Append text to a file (a log line, a run line), never through a link and never into a
    read-only copy of another space, as `write_text`."""
    path = Path(path)
    check_no_links(path, root=root)
    refuse_copy(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _open_no_follow(path, append=True, newline=newline) as handle:
        handle.write(text)
    return path
