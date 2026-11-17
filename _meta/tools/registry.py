"""The subject registry: `_meta/subjects.yaml` (shape 1).

A subject is anything people ask about by name. Hierarchy lives here (`part_of`),
never in paths; owners live on subjects and parts inherit their umbrella's owner;
`home` names the space where the subject's shared conclusions live (a space name, one
lowercase slug: any other value is no home, and `check` reports it); `match` holds
the fixed identifiers (tracker, repo, channel, label, thread, calendar, github) by which a
sweep attaches evidence, plus two inferred ones: `domain`, a mail domain on any address of the
record (from, to, cc, participants), and `keywords`, whole words in the title of a call or a
calendar event. `repo_owner` attaches nothing and grants nothing: it names a GitHub account (a
client's organisation) as a note for whoever links its repositories; link.py trusts an owner
only by `match.github`, whose change waits for the owner's click. A `match.repo` is also where a release clones the subject's home from, so only
a GitHub repository counts there (`clone_url`). Ids are shared up the ladder: a space reuses an ancestor's
id (read through its mount) and mints a new one only when no space above has one. The one
exception is a subject's home: it may hold the id too, to add the names the subject is known by
there (`aliases`, `match`) that its parent keeps out, and a space that mounts the home reads them
on top of the parent's entry. The register
fields say what an engagement was (the brain's projects page shows them): `period` (`from`,
`to`, months `YYYY-MM`), `client` (a subject id), `crew` (people subject ids) and `work` (one line).
`repos` lists the codebases linked to the subject: `repo` (owner/name), `repo_id`, `linked_by`,
`readers`, the dates `checked`, `linked` and `gone`, and `was` (the names it had before a rename)
(docs/design/2026-10-22-codebase-link-design.md).

    python registry.py <root> check     report loops, duplicates, unknown kinds
    python registry.py <root> find <x>  show a subject by id or alias
    python registry.py <root> add <name> --kind clients [--title T] [--domain D ...] [--owner NAME]
                                        [--part-of ID]
                                        add one subject (a client, project, deal, person, ...)
"""
from __future__ import annotations

import copy
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
STATES = ("active", "dormant", "archived",
          "delivered", "stopped",        # a project that ended (history spec 4.4)
          "open", "won", "lost")          # a deal
# states that still move: distill proposes for them and status keeps their pages
LIVE_STATES = ("active", "open")
MATCH_KEYS = ("tracker", "repo", "channel", "label", "thread", "calendar", "github",
              "domain", "keywords", "repo_owner")
# Keys that attach no record: `repo_owner`, a GitHub account named as a client's organisation. It
# points no trust root anywhere and is no gated field, so nothing trusts it: link.py trusts an
# owner only by `match.github`, a trust field.
NO_ATTACH_KEYS = ("repo_owner",)
LOGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}\Z")
INFERRED_KEYS = ("domain", "keywords")   # attach by inference: never released at once
TITLED_KINDS = ("call", "event")
# The classification gate (docs/design/2026-10-16-classification-gate-design.md):
# a subject of these kinds, or a part of one, leaves nowhere before its owner's agent classifies it.
GATED_KINDS = ("clients", "projects", "deals")
CLASSES = ("company", "walled", "owner")             # widest to narrowest
CLASS_STATES = ("asking", "settled", "open")         # only settled opens the gate
OBLIGATION_KINDS = {"confidentiality": "confidentiality agreement", "named_people": "named people only",
                    "data_processor": "data processing agreement", "other": "other obligation"}
OBLIGATION_KEYS = ("kind", "date", "seen_in", "at", "evidence")   # kind and date only: never wording
# Anchored with \Z: `$` also matches before a final newline, and a space name with a line
# break after it would pass.
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*\Z")
MAX_SPACE_NAME = 100
# The one repository shape a release clone is made from: GitHub, as `owner/name`,
# `github.com/owner/name`, `https://github.com/owner/name(.git)` or `git@github.com:owner/name.git`.
CLONE_RE = re.compile(r"^(?:git@github\.com:|(?:https://)?github\.com/)?([A-Za-z0-9][A-Za-z0-9-]*)/"
                      r"([A-Za-z0-9_][A-Za-z0-9_.-]*?)(?:\.git)?/?\Z", re.ASCII)
LINKED_BY = ("onboard", "mint", "detect", "person")   # who linked a codebase to a subject
READERS = ("inside", "wider")                        # a codebase's readers against the subject's audience
# The notes a space sent to the registry that holds a subject (classify.py), and the log in which
# that registry records each one it applied or refused.
SENT_NOTES = Path("_meta") / "state" / "classify.jsonl"
NOTES_LOG = Path("_meta") / "state" / "registry-notes.jsonl"


def _jsonl(path: Path) -> list:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _is_date(value) -> bool:
    try:
        dt.date.fromisoformat(str(value))
        return True
    except ValueError:
        return False


def _gate_errors(sid: str, subject: "Subject") -> list:
    """The classification, obligations and workspace fields of one entry."""
    errs = []
    c = subject.extra.get("classification")
    if c is not None:
        if not isinstance(c, dict):
            return [f"registry: `{sid}` classification is not a mapping"]
        if c.get("class") is not None and c.get("class") not in CLASSES:
            errs.append(f"registry: `{sid}` classification class `{c.get('class')}` is not one of {', '.join(CLASSES)}")
        if c.get("state") not in CLASS_STATES:
            errs.append(f"registry: `{sid}` classification state `{c.get('state')}` is not one of "
                        f"{', '.join(CLASS_STATES)}")
        if c.get("state") == "settled" and c.get("class") not in CLASSES:
            errs.append(f"registry: `{sid}` classification is settled without a class")
        if c.get("state") == "settled" and c.get("class") == "walled":
            if not c.get("home"):
                errs.append(f"registry: `{sid}` walled classification names no home")
            if not isinstance(c.get("crew"), list) or not c.get("crew"):
                errs.append(f"registry: `{sid}` walled classification names no crew")
    obligations = subject.extra.get("obligations")
    items = obligations if isinstance(obligations, list) else ([] if obligations is None else [obligations])
    for n, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errs.append(f"registry: `{sid}` obligation {n} is not a mapping")
            continue
        extra_keys = sorted(set(item) - set(OBLIGATION_KEYS))
        if extra_keys:
            errs.append(f"registry: `{sid}` obligation {n} carries {', '.join(extra_keys)}: kind and date only")
        if item.get("kind") not in OBLIGATION_KINDS:
            errs.append(f"registry: `{sid}` obligation {n} kind `{item.get('kind')}` is not one of "
                        f"{', '.join(OBLIGATION_KINDS)}")
        if item.get("date") is not None and not _is_date(item.get("date")):
            errs.append(f"registry: `{sid}` obligation {n} date `{item.get('date')}` is not YYYY-MM-DD")
    workspace = subject.extra.get("workspace")
    if workspace is not None and not SLUG.match(str(workspace)):
        errs.append(f"registry: `{sid}` workspace `{workspace}` is not a space name")
    workspace_repo = subject.extra.get("workspace_repo")
    if workspace_repo is not None:   # release.trust_repo ignores such a value; this makes it visible
        if clone_url(workspace_repo) is None:
            errs.append(f"registry: `{sid}` workspace_repo is not a GitHub repository")
        if workspace is None:
            errs.append(f"registry: `{sid}` workspace_repo is set without workspace")
    return errs


def _repo_errors(sid: str, repos) -> list:
    """A subject's codebase links (`repos`): each names its repository; who linked it, its readers
    and its dates are one of the known values when set."""
    if repos is None:
        return []
    if not isinstance(repos, list):
        return [f"registry: `{sid}` repos is not a list"]
    errs = []
    for n, entry in enumerate(repos, start=1):
        if not isinstance(entry, dict):
            errs.append(f"registry: `{sid}` repo link {n} is not a mapping")
            continue
        if not isinstance(entry.get("repo"), str) or not entry["repo"].strip():
            errs.append(f"registry: `{sid}` repo link {n} names no repo")
        for key, known in (("readers", READERS), ("linked_by", LINKED_BY)):
            if entry.get(key) is not None and entry[key] not in known:
                errs.append(f"registry: `{sid}` repo link {n} {key} `{entry[key]}` is not one of {', '.join(known)}")
        for key in ("checked", "linked", "gone"):
            if entry.get(key) is not None and not _is_date(entry[key]):
                errs.append(f"registry: `{sid}` repo link {n} {key} `{entry[key]}` is not YYYY-MM-DD")
        was = entry.get("was")
        if was is not None and not (isinstance(was, list) and all(isinstance(w, str) and w.strip() for w in was)):
            errs.append(f"registry: `{sid}` repo link {n} was is not a list of repositories")
    return errs


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
    period: dict = field(default_factory=dict)   # {from, to}: months, YYYY-MM
    client: str | None = None                    # the client's subject id
    crew: list = field(default_factory=list)     # people subject ids
    work: str | None = None                      # the kind of work, one line
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
        kwargs["crew"] = list(kwargs.get("crew") or [])
        period = kwargs.get("period") or {}
        kwargs["period"] = dict(period) if isinstance(period, dict) else period
        return cls(space=space, extra=extra, **kwargs)

    def to_dict(self) -> dict:
        out = {"id": self.id, "kind": self.kind, "title": self.title, "state": self.state}
        for key in ("part_of", "owner", "home"):
            value = getattr(self, key)
            if value:
                out[key] = value
        if self.period:
            out["period"] = dict(self.period) if isinstance(self.period, dict) else self.period
        for key in ("client", "crew", "work"):
            value = getattr(self, key)
            if value:
                out[key] = list(value) if isinstance(value, list) else value
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


def clone_url(value) -> str | None:
    """A repository a release may clone, as `https://github.com/<owner>/<name>`, or None. Only the
    GitHub spellings `repo_url` knows are accepted (no `http://`); a local path, a share, another
    host, `file://`, `ext::` or a value git would read as an option (a leading `-`) is None, so a
    registry entry can never make git run a command or reach another server."""
    if not isinstance(value, str):
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):   # before strip: a line break is no repository
        return None
    text = value.strip()
    if not text or text.startswith("-"):
        return None
    found = CLONE_RE.match(text)
    return f"https://github.com/{found.group(1)}/{found.group(2)}" if found else None


def _is_slug(value) -> bool:
    """A lowercase slug of at most `pages.MAX_SLUG` characters, anchored at the very end (`\\Z`),
    so `harbor\\n` is none."""
    import pages
    return pages.is_slug(value)


def _named_subject(record: dict) -> str:
    """The subject a record names (`identifiers.subject`), honoured only on the journal feed,
    which link.journal_records writes: any other record that names one names nothing."""
    if str(record.get("source") or "") != "journal":
        return ""
    ident = record.get("identifiers")
    return str(ident.get("subject") or "") if isinstance(ident, dict) else ""


def space_name(value) -> str | None:
    """`value` as a space name (one lowercase slug), or None: a `home` reaches paths and git."""
    text = str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else ""
    return text if text and len(text) <= MAX_SPACE_NAME and SLUG.match(text) else None


def _repo_problem(value) -> str | None:
    """Why a `match.repo` value is refused outright: one git would read as an option or a
    transport, or one with a control character. Another host or a local path is not an error
    here; such a value is simply never cloned (`clone_url`)."""
    text = str(value)
    if text.strip().startswith("-"):
        return "starts with `-`, which git reads as an option"
    if "::" in text or re.match(r"^\s*(?:file|ext|fd):", text, re.I):
        return "names a git transport, not a repository"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        return "holds a control character"
    return None


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


def routed_subjects(fm: dict, space_name: str | None) -> list:
    """The subject ids a page is evidence for in this space: its own `subject`, and every route
    of its routing decision that names this space (a routed record attaches by judgment); with
    `space_name` None, every route wherever it went (the record's own space, for status)."""
    out = [str(fm["subject"])] if fm.get("subject") else []
    routing = fm.get("routing") if isinstance(fm.get("routing"), dict) else {}
    for entry in routing.get("routes") or []:
        where, _, sid = str(entry).partition("/")
        if sid and (space_name is None or where == space_name) and sid not in out:
            out.append(sid)
    return out


def _add_names(base: Subject, entry: dict) -> None:
    """The names a subject's home adds: its aliases and match identifiers, after the parent's."""
    for alias in entry.get("aliases") or []:
        if alias not in base.aliases:
            base.aliases.append(alias)
    for key, value in (entry.get("match") or {}).items():
        have = base.match.get(key)
        if have is None:
            base.match[key] = value
            continue
        merged = list(have) if isinstance(have, list) else [have]
        merged += [v for v in (value if isinstance(value, list) else [value]) if v not in merged]
        base.match[key] = merged if len(merged) > 1 else merged[0]


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
        self.ancestor_homes: dict = {}  # the home the first ancestor holding an id names for it
        self._brain_names: dict = {}  # a brain subject's aliases and match as the brain wrote them
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
            later = []   # (space name, entry): a second holder of an id, read once all are loaded
            read = {}
            for mount in self.space.mounts:
                name = mount.get("name", "")
                mount_root = self.space.mount_root(name)
                if mount_root is not None:
                    read[name] = self._read(mount_root / REGISTRY_PATH)
            # Subjects come from the brain and from a mount this registry or the brain's vouches for
            # (a subject homed there, a person's workspace), never from a corridor: its other side
            # writes its subjects.yaml and would steer where this space's records are released.
            known = [e for e in list(read.get("brain") or []) + self._read(self.path) if isinstance(e, dict)]
            vouched = {str((e.get("workspace") if e.get("kind") == "people" else e.get("home")) or "")
                       for e in known} - {""}
            for name in read:
                if name != "brain" and (self.space.is_corridor(name) or name not in vouched):
                    continue
                for entry in read[name]:
                    if isinstance(entry, dict) and entry.get("id"):
                        sid = str(entry["id"])
                        if sid not in self.ancestor_ids:
                            self.ancestor_homes[sid] = entry.get("home")
                        self.ancestor_ids.add(sid)
                        if sid not in self.subjects and sid not in self.ancestors:
                            self.ancestors[sid] = Subject.from_dict(entry, space=name)
                        elif not self.space.is_corridor(name):   # both sides write a corridor
                            later.append((name, entry))
            # a subject's home may hold its id too, with the names it is known by there (aliases,
            # match identifiers) that its parent keeps out; a space that mounts the home reads them,
            # keyed on the name this space mounted it under, never on what the mount's space.yaml
            # calls itself. A trust decision never reads them (`trusted_view`).
            for mount_name, entry in later:
                base = self.ancestors.get(str(entry["id"]))
                if base is not None and base.home and base.home == mount_name:
                    if base.space == "brain" and base.id not in self._brain_names:
                        self._brain_names[base.id] = (list(base.aliases), copy.deepcopy(base.match))
                    _add_names(base, entry)
            self._travelling()

    def _travelling(self) -> None:
        """A classification this space sent to the registry that holds the subject governs here
        at once, while it travels: until that registry's notes log shows it applied or refused.
        `classify.note` sends one only after the holder's own rules accepted it."""
        sent, states = {}, {}
        for row in _jsonl(self.root / SENT_NOTES):
            if row.get("note") == "classification" and isinstance(row.get("classification"), dict):
                sent[str(row.get("subject"))] = row   # the latest per subject
            elif row.get("note") == "state" and row.get("state") in ("active", "dormant", "archived"):
                states[str(row.get("subject"))] = row   # a state the home judged, the latest
        logs: dict = {}

        def arrived(subject, row) -> bool:
            if subject.space not in logs:
                holder = self.space.mount_root(subject.space)
                logs[subject.space] = {str(r.get("id")) for r in _jsonl(holder / NOTES_LOG)} if holder else set()
            return str(row.get("signal")) in logs[subject.space]

        import classify   # lazily: classify imports this module
        for sid, row in sent.items():
            subject = self.ancestors.get(sid)
            if subject is None or arrived(subject, row):
                continue   # the holder applied or refused it: its own entry says what holds
            found = classify.travelling(self.space, subject, row)
            if found is None:
                continue   # no signal of this space's on its way, or one the holder refuses
            home = found.get("home")
            if home and space_name(home) is None:
                continue   # a home that is not a space name governs nothing
            subject.extra["classification"] = dict(found, travelling=str(row.get("signal")))
            if home:
                subject.home = str(home)
        for sid, row in states.items():
            subject = self.ancestors.get(sid)
            if subject is not None and not arrived(subject, row):
                subject.state = str(row["state"])

    def homed_here(self, sid: str) -> bool:
        """An ancestor's subject whose home is this space: this registry may add its names."""
        return self.space is not None and bool(self.space.name) and \
            self.ancestor_homes.get(sid) == self.space.name

    def save(self) -> None:
        import pages
        pages.refuse_copy(self.path)   # a mount's registry is that space's to write
        data = {"subjects": [s.to_dict() for s in self.subjects.values()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100),
                             encoding="utf-8", newline="\n")

    # ----- lookups ---------------------------------------------------------
    def all(self) -> list:
        return list(self.subjects.values())

    def everywhere(self) -> list:
        return list(self.subjects.values()) + list(self.ancestors.values())

    def trusted_view(self) -> "Registry":
        """This registry as a trust decision reads it: its own entries and the brain's (the mount
        called `brain`), whose changes wait for their owner's click. A channel's or a project
        mount's registry is left out: both sides of a corridor write its files, unchecked."""
        view = copy.copy(self)
        view.ancestors = {k: v for k, v in self.ancestors.items() if v.space == "brain"}
        for sid, (aliases, match) in self._brain_names.items():   # never the names a home added
            if sid in view.ancestors:
                view.ancestors[sid] = copy.copy(view.ancestors[sid])
                view.ancestors[sid].aliases, view.ancestors[sid].match = list(aliases), copy.deepcopy(match)
        return view

    def vouched(self, name: str) -> bool:
        """The space called `name` is one this registry, as a trust decision reads it, knows: a
        subject homed there, or a person's registered workspace. A mount it does not know (a
        corridor under a custom name, whatever its own space.yaml says) vouches for no one."""
        return any((s.kind == "people" and str(s.extra.get("workspace") or "") == name)
                   or (s.kind != "people" and s.home == name) for s in self.trusted_view().everywhere())

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
        """Subjects whose identifiers the record carries: its fixed identifiers, a repository
        linked to the subject, the mail domain of one of its addresses, a keyword in the title of
        a call or event. A record that names its subject (`identifiers.subject`) belongs to it alone;
        one from a linked repository to the subject it is linked to (another's `match.repo` aside)."""
        named = self.get(_named_subject(record))
        if named is not None and named.state != "archived":
            return [named]
        hits = [(s, self.matched_by(s, record)) for s in self.everywhere()]
        linked = [s for s, keys in hits if "repos" in keys]   # a linked codebase is its subject's alone
        return linked or [s for s, keys in hits if keys]

    def matched_by(self, subject: Subject, record: dict) -> set:
        """The `match` keys by which the record belongs to the subject (empty: it does not), plus
        `subject` when the record names it and `repos` when its repository is linked to it."""
        identifiers = record.get("identifiers") or {}
        keys = set()
        if _named_subject(record) == subject.id and subject.state != "archived":
            keys.add("subject")
        repos = subject.extra.get("repos")
        have = identifiers.get("repo")
        if have is not None and isinstance(repos, list) and any(
                isinstance(link, dict) and not link.get("gone") and same_repo(link.get("repo"), value)
                for link in repos for value in (have if isinstance(have, list) else [have])):
            keys.add("repos")
        for key, wanted in (subject.match or {}).items():
            if key in NO_ATTACH_KEYS:
                continue
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

    def classification(self, sid: str) -> dict | None:
        """The classification that governs the subject: the narrowest settled one on its chain
        (the subject, then its umbrellas; the nearest on a tie), with `subject` naming whose it
        is. None when there is none, or when any on the chain is still asking or re-opened."""
        subject = self.get(sid)
        best = None
        for distance, s in enumerate([] if subject is None else [subject] + self.ancestors_of(sid)):
            c = s.extra.get("classification")
            if not isinstance(c, dict):
                continue
            if c.get("state") != "settled" or c.get("class") not in CLASSES:
                return None
            rank = (CLASSES.index(c["class"]), -distance)
            if best is None or rank > best[0]:
                best = (rank, dict(c, subject=s.id))
        return best[1] if best else None

    def home_of(self, sid: str) -> str | None:
        """The space where the subject's shared conclusions live, or None. A home that is not a
        space name (`../spaces/x`, a path) is no home at all: None, so nothing is addressed to it
        and the classification gate holds what is about the subject."""
        subject = self.get(sid)
        if subject is None:
            return None
        governing = self.classification(sid)   # a company or walled class names the home of all it covers
        if governing and governing.get("class") != "owner" and governing.get("home"):
            return space_name(governing["home"])
        if subject.home:
            return space_name(subject.home)
        for parent in self.ancestors_of(sid):
            if parent.home:
                return space_name(parent.home)
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
            if not _is_slug(sid):
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
            owners = subject.match.get("repo_owner")
            for value in (owners if isinstance(owners, list) else [] if owners is None else [owners]):
                if not isinstance(value, str) or not LOGIN_RE.match(value.strip()):
                    errs.append(f"registry: `{sid}` match.repo_owner {ascii(value)[:60]} is not a GitHub account name")
            if subject.home and space_name(subject.home) is None:
                errs.append(f"registry: `{sid}` home {ascii(subject.home)} is not a space name")
            c = subject.extra.get("classification")
            if isinstance(c, dict) and c.get("home") and space_name(c.get("home")) is None:
                errs.append(f"registry: `{sid}` classification home {ascii(c.get('home'))} is not a space name")
            repos = subject.match.get("repo")
            for value in (repos if isinstance(repos, list) else [] if repos is None else [repos]):
                problem = _repo_problem(value)
                if problem:
                    errs.append(f"registry: `{sid}` match.repo {ascii(value)[:80]} {problem}")
            if sid in self.ancestor_ids and not self.homed_here(sid):
                errs.append(f"registry: `{sid}` re-mints an id an ancestor already has")
            errs += _gate_errors(sid, subject)
            errs += _repo_errors(sid, subject.extra.get("repos"))
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


def add_command(root, argv) -> int:
    """`add`: one subject, checked like the rest of the registry before it is written. A client,
    project or deal says the gate's rule: nothing about it leaves until it is classified."""
    import argparse
    ap = argparse.ArgumentParser(prog="registry.py <root> add")
    ap.add_argument("name", help="its id, or its name (made into the id)")
    ap.add_argument("--kind", required=True, help="one of the space's kinds: clients, projects, deals, people, ...")
    ap.add_argument("--title", help="how people name it (default: the name given)")
    ap.add_argument("--domain", action="append", default=[], help="a mail domain of theirs; repeat for more")
    ap.add_argument("--owner", help="who handles it: that person's agent classifies it")
    ap.add_argument("--part-of", dest="part_of", help="the subject it is part of (a project's client)")
    args = ap.parse_args(argv)
    sid = re.sub(r"[^a-z0-9]+", "-", args.name.lower()).strip("-")
    data = {"id": sid, "kind": args.kind, "title": args.title or args.name}
    for key in ("owner", "part_of"):
        if getattr(args, key):
            data[key] = getattr(args, key)
    if args.domain:
        data["match"] = {"domain": args.domain[0] if len(args.domain) == 1 else list(args.domain)}
    reg = Registry(root)
    try:
        reg.add(data, by="registry")
    except ValueError as exc:
        print(f"refused: {exc}")
        return 1
    errs = [e for e in reg.check() if f"`{sid}`" in e]
    if not sid or errs:
        print(f"refused: {'; '.join(errs) or 'a name is needed'}")
        return 1
    reg.save()
    print(f"added {sid} ({args.kind}) to {REGISTRY_PATH.as_posix()}")
    if args.kind in GATED_KINDS:
        who = f"{args.owner}'s agent" if args.owner else "its owner's agent"
        print(f"nothing about {data['title']} leaves the space it came into until {who} classifies it: "
              "what waits is counted on Needs you")
    return 0


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
    if cmd == "add" and len(argv) > 2:
        return add_command(argv[0], argv[2:])
    if cmd == "find" and len(argv) > 2:
        subject = reg.find(argv[2])
        print(json.dumps(subject.to_dict(), indent=2) if subject else "not found")
        return 0 if subject else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
