"""The subject registry: `_meta/subjects.yaml` (shape 1).

A subject is anything people ask about by name. Hierarchy lives here (`part_of`),
never in paths; owners live on subjects and parts inherit their umbrella's owner;
`home` names the space where the subject's shared conclusions live; `match` holds
the fixed identifiers (tracker, repo, channel, label, thread, calendar, github) by which a
sweep attaches evidence, plus two inferred ones: `domain`, a mail domain on any address of the
record (from, to, cc, participants), and `keywords`, whole words in the title of a call or a
calendar event. Ids are shared up the ladder: a space reuses an ancestor's
id (read through its mount) and mints a new one only when no space above has one.

    python registry.py <root> check     report loops, duplicates, unknown kinds
    python registry.py <root> find <x>  show a subject by id or alias
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spaceyaml  # noqa: E402

REGISTRY_PATH = Path("_meta") / "subjects.yaml"
STATES = ("active", "dormant", "archived")
MATCH_KEYS = ("tracker", "repo", "channel", "label", "thread", "calendar", "github",
              "domain", "keywords")
INFERRED_KEYS = ("domain", "keywords")   # attach by inference: never released at once
TITLED_KINDS = ("call", "event")


@dataclass
class Subject:
    id: str
    kind: str
    title: str = ""
    state: str = "active"
    part_of: str | None = None
    owner: str | None = None
    home: str | None = None
    match: dict = field(default_factory=dict)
    aliases: list = field(default_factory=list)
    important: bool = False
    stateful: bool = True
    added: str | None = None
    added_by: str | None = None
    extra: dict = field(default_factory=dict)
    space: str = ""  # which space's registry holds it ("" for this one)

    @classmethod
    def from_dict(cls, data: dict, space: str = "") -> "Subject":
        known = {f for f in cls.__dataclass_fields__ if f not in ("extra", "space")}
        kwargs = {k: v for k, v in data.items() if k in known}
        extra = {k: v for k, v in data.items() if k not in known}
        kwargs.setdefault("title", kwargs.get("id", ""))
        kwargs["match"] = dict(kwargs.get("match") or {})
        kwargs["aliases"] = list(kwargs.get("aliases") or [])
        return cls(space=space, extra=extra, **kwargs)

    def to_dict(self) -> dict:
        out = {"id": self.id, "kind": self.kind, "title": self.title, "state": self.state}
        for key in ("part_of", "owner", "home"):
            value = getattr(self, key)
            if value:
                out[key] = value
        if self.match:
            out["match"] = dict(self.match)
        if self.aliases:
            out["aliases"] = list(self.aliases)
        if self.important:
            out["important"] = True
        if not self.stateful:
            out["stateful"] = False
        for key in ("added", "added_by"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        out.update(self.extra)
        return out

    def names(self) -> list:
        return [n for n in [self.title, self.id] + list(self.aliases) if n]


def repo_url(value) -> str | None:
    """A `match.repo` (or a mount's repo) as an https URL: `owner/name`, `github.com/owner/name`
    and `git@github.com:owner/name.git` become `https://github.com/owner/name`; any other URL or
    a local path is kept as given."""
    text = str(value or "").strip()
    if not text:
        return None
    short = re.match(r"^(?:git@github\.com:|(?:https?://)?github\.com/)?([A-Za-z0-9][\w-]*/[\w.-]+?)(?:\.git)?/?$", text)
    return f"https://github.com/{short.group(1)}" if short else text


def same_repo(a, b) -> bool:
    """Two repository references name the same repository (case, `.git` and `/` aside)."""
    def key(value):
        url = repo_url(value)
        return url.lower().rstrip("/").removesuffix(".git") if url else None
    return key(a) is not None and key(a) == key(b)


def address_of(value) -> str:
    """`Name <a@b>` or `a@b` as a lower-case address; anything else as lower-case text."""
    text = str(value or "").strip().lower()
    return text.split("<")[-1].rstrip(">").strip() if "<" in text else text


def record_domains(record: dict) -> set:
    """The mail domains of everyone on a record: from, to, cc and participants."""
    people = [record.get("from")]
    for key in ("to", "cc", "participants"):
        value = record.get(key) or []
        people += value if isinstance(value, list) else [value]
    return {address_of(p).rsplit("@", 1)[1] for p in people if "@" in address_of(p)}


def _word_in(text: str, needle: str) -> bool:
    if len(needle) < 3:
        return False
    return re.search(r"(?<![\w-])" + re.escape(needle.lower()) + r"(?![\w-])", text.lower()) is not None


class Registry:
    def __init__(self, root, space: spaceyaml.Space | None = None):
        self.root = Path(root)
        self.space = space
        self.path = self.root / REGISTRY_PATH
        self.subjects: dict = {}
        self.ancestors: dict = {}
        self.ancestor_ids: set = set()  # every id any ancestor holds, shadowed or not
        self._load()

    # ----- loading ---------------------------------------------------------
    def _read(self, path: Path) -> list:
        if not path.exists():
            return []
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(data, list):
            return data
        return list((data or {}).get("subjects") or [])

    def _load(self) -> None:
        for entry in self._read(self.path):
            if isinstance(entry, dict) and entry.get("id"):
                self.subjects[str(entry["id"])] = Subject.from_dict(entry)
        if self.space is None and (self.root / "space.yaml").exists():
            try:
                self.space = spaceyaml.load(self.root)
            except Exception:
                self.space = None
        if self.space is not None:
            for mount in self.space.mounts:
                name = mount.get("name", "")
                mount_root = self.space.mount_root(name)
                if mount_root is None:
                    continue
                for entry in self._read(mount_root / REGISTRY_PATH):
                    if isinstance(entry, dict) and entry.get("id"):
                        sid = str(entry["id"])
                        self.ancestor_ids.add(sid)
                        if sid not in self.subjects and sid not in self.ancestors:
                            self.ancestors[sid] = Subject.from_dict(entry, space=name)

    def save(self) -> None:
        data = {"subjects": [s.to_dict() for s in self.subjects.values()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100),
                             encoding="utf-8", newline="\n")

    # ----- lookups ---------------------------------------------------------
    def all(self) -> list:
        return list(self.subjects.values())

    def everywhere(self) -> list:
        return list(self.subjects.values()) + list(self.ancestors.values())

    def get(self, sid: str) -> Subject | None:
        return self.subjects.get(sid) or self.ancestors.get(sid)

    def find(self, text: str) -> Subject | None:
        if not text:
            return None
        key = str(text).strip().lower()
        for pool in (self.subjects, self.ancestors):
            if key in pool:
                return pool[key]
            for subject in pool.values():
                if any(n.lower() == key for n in subject.names()):
                    return subject
        return None

    def match(self, record: dict) -> list:
        """Subjects whose identifiers the record carries: its fixed identifiers, the mail
        domain of one of its addresses, a keyword in the title of a call or event."""
        return [s for s in self.everywhere() if self.matched_by(s, record)]

    def matched_by(self, subject: Subject, record: dict) -> set:
        """The `match` keys by which the record belongs to the subject (empty: it does not)."""
        identifiers = record.get("identifiers") or {}
        keys = set()
        for key, wanted in (subject.match or {}).items():
            wanted_values = [str(w).strip().lower() for w in (wanted if isinstance(wanted, list) else [wanted])]
            if key == "domain":
                if any(d == w or d.endswith("." + w) for d in record_domains(record) for w in wanted_values):
                    keys.add(key)
                continue
            if key == "keywords":
                title = str(record.get("subject") or record.get("title") or "")
                if record.get("kind") in TITLED_KINDS and any(_word_in(title, w) for w in wanted_values):
                    keys.add(key)
                continue
            have = identifiers.get(key)
            if have is None and key == "label":
                have = identifiers.get("labels")
            if have is None:
                continue
            values = have if isinstance(have, list) else [have]
            if any(str(v).lower() in wanted_values for v in values):
                keys.add(key)
        return keys

    def mentions(self, record: dict) -> list:
        """Subjects named in the record's text: hints for evidence, never attachment."""
        text = " ".join(str(record.get(k, "")) for k in ("subject", "title", "text"))
        found = []
        for subject in self.everywhere():
            if any(_word_in(text, name) for name in subject.names() if len(name) >= 3):
                found.append(subject)
        return found

    def ancestors_of(self, sid: str) -> list:
        chain, seen = [], set()
        current = self.get(sid)
        while current is not None and current.part_of and current.part_of not in seen:
            seen.add(current.part_of)
            parent = self.get(current.part_of)
            if parent is None:
                break
            chain.append(parent)
            current = parent
        return chain

    def owner_of(self, sid: str) -> str | None:
        subject = self.get(sid)
        if subject is None:
            return self.space.owner if self.space else None
        if subject.owner:
            return subject.owner
        for parent in self.ancestors_of(sid):
            if parent.owner:
                return parent.owner
        return self.space.owner if self.space else None

    def home_of(self, sid: str) -> str | None:
        subject = self.get(sid)
        if subject is None:
            return None
        if subject.home:
            return subject.home
        for parent in self.ancestors_of(sid):
            if parent.home:
                return parent.home
        return None

    def domain_of(self, sid: str) -> Subject | None:
        for parent in self.ancestors_of(sid):
            if parent.kind == "domains":
                return parent
        return None

    # ----- edits -----------------------------------------------------------
    def add(self, data: dict, by: str = "librarian", now: dt.datetime | None = None) -> Subject:
        sid = str(data["id"])
        if self.get(sid) is not None:
            raise ValueError(f"subject `{sid}` already exists (an ancestor's id is reused, never re-minted)")
        now = now or dt.datetime.now(dt.timezone.utc)
        data = dict(data)
        data.setdefault("added", now.strftime("%Y-%m-%d"))
        data.setdefault("added_by", by)
        subject = Subject.from_dict(data)
        self.subjects[sid] = subject
        return subject

    # ----- checks ----------------------------------------------------------
    def check(self) -> list:
        errs = []
        kinds = self.space.all_kinds if self.space else None
        for sid, subject in self.subjects.items():
            if not re.match(r"^[a-z0-9][a-z0-9-]*$", sid):
                errs.append(f"registry: id `{sid}` is not a lowercase slug")
            if kinds is not None and subject.kind not in kinds:
                errs.append(f"registry: `{sid}` has unknown kind `{subject.kind}`")
            if subject.state not in STATES:
                errs.append(f"registry: `{sid}` has unknown state `{subject.state}`")
            if subject.part_of and self.get(subject.part_of) is None:
                errs.append(f"registry: `{sid}` is part_of unknown `{subject.part_of}`")
            for key in subject.match:
                if key not in MATCH_KEYS:
                    errs.append(f"registry: `{sid}` match key `{key}` is not one of {', '.join(MATCH_KEYS)}")
            if sid in self.ancestor_ids:
                errs.append(f"registry: `{sid}` re-mints an id an ancestor already has")
            # loops
            seen, current = {sid}, subject
            while current is not None and current.part_of:
                if current.part_of in seen:
                    errs.append(f"registry: part_of loop through `{sid}`")
                    break
                seen.add(current.part_of)
                current = self.get(current.part_of)
        return errs


def load(root, space: spaceyaml.Space | None = None) -> Registry:
    return Registry(root, space)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    reg = Registry(argv[0])
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd == "check":
        errs = reg.check()
        for err in errs:
            print(err)
        print(f"{len(reg.subjects)} subjects, {len(reg.ancestors)} inherited, {len(errs)} errors")
        return 1 if errs else 0
    if cmd == "find" and len(argv) > 2:
        subject = reg.find(argv[2])
        print(json.dumps(subject.to_dict(), indent=2) if subject else "not found")
        return 0 if subject else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
