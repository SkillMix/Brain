"""The subject registry: `_meta/subjects.yaml` (shape 1).

A subject is anything people ask about by name. Hierarchy lives here (`part_of`),
never in paths; owners live on subjects and parts inherit their umbrella's owner;
`home` names the space where the subject's shared conclusions live; `match` holds
the fixed identifiers (tracker, repo, channel, label, thread, calendar, github) by which a
sweep attaches evidence, plus two inferred ones: `domain`, a mail domain on any address of the
record (from, to, cc, participants), and `keywords`, whole words in the title of a call or a
calendar event. Ids are shared up the ladder: a space reuses an ancestor's
id (read through its mount) and mints a new one only when no space above has one. The one
exception is a subject's home: it may hold the id too, to add the names the subject is known by
there (`aliases`, `match`) that its parent keeps out, and a space that mounts the home reads them
on top of the parent's entry. The register
fields say what an engagement was (the brain's projects page shows them): `period` (`from`,
`to`, months `YYYY-MM`), `client` (a subject id), `crew` (people subject ids) and `work` (one line).

    python registry.py <root> check     report loops, duplicates, unknown kinds
    python registry.py <root> find <x>  show a subject by id or alias
    python registry.py <root> add <name> --kind clients [--title T] [--domain D ...] [--owner NAME]
                                        [--part-of ID]
                                        add one subject (a client, project, deal, person, ...)
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
STATES = ("active", "dormant", "archived",
          "delivered", "stopped",        # a project that ended (history spec 4.4)
          "open", "won", "lost")          # a deal
# states that still move: distill proposes for them and status keeps their pages
LIVE_STATES = ("active", "open")
MATCH_KEYS = ("tracker", "repo", "channel", "label", "thread", "calendar", "github",
              "domain", "keywords")
INFERRED_KEYS = ("domain", "keywords")   # attach by inference: never released at once
TITLED_KINDS = ("call", "event")
# The classification gate (docs/superpowers/specs/2026-10-16-classification-gate-design.md):
# a subject of these kinds, or a part of one, leaves nowhere before its owner's agent classifies it.
GATED_KINDS = ("clients", "projects", "deals")
CLASSES = ("company", "walled", "owner")             # widest to narrowest
CLASS_STATES = ("asking", "settled", "open")         # only settled opens the gate
OBLIGATION_KINDS = {"confidentiality": "confidentiality agreement", "named_people": "named people only",
                    "data_processor": "data processing agreement", "other": "other obligation"}
OBLIGATION_KEYS = ("kind", "date", "seen_in", "at", "evidence")   # kind and date only: never wording
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
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
            for mount in self.space.mounts:
                name = mount.get("name", "")
                mount_root = self.space.mount_root(name)
                if mount_root is None:
                    continue
                try:
                    space_name = spaceyaml.load(mount_root).name
                except Exception:
                    space_name = name
                for entry in self._read(mount_root / REGISTRY_PATH):
                    if isinstance(entry, dict) and entry.get("id"):
                        sid = str(entry["id"])
                        if sid not in self.ancestor_ids:
                            self.ancestor_homes[sid] = entry.get("home")
                        self.ancestor_ids.add(sid)
                        if sid not in self.subjects and sid not in self.ancestors:
                            self.ancestors[sid] = Subject.from_dict(entry, space=name)
                        else:
                            later.append((space_name, entry))
            # a subject's home may hold its id too, with the names it is known by there (aliases,
            # match identifiers) that its parent keeps out; a space that mounts the home reads them
            for space_name, entry in later:
                base = self.ancestors.get(str(entry["id"]))
                if base is not None and base.home and base.home == space_name:
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

        for sid, row in sent.items():
            subject = self.ancestors.get(sid)
            if subject is None or arrived(subject, row):
                continue   # the holder applied or refused it: its own entry says what holds
            subject.extra["classification"] = dict(row["classification"], travelling=str(row.get("signal")))
            if row["classification"].get("home"):
                subject.home = str(row["classification"]["home"])
        for sid, row in states.items():
            subject = self.ancestors.get(sid)
            if subject is not None and not arrived(subject, row):
                subject.state = str(row["state"])

    def homed_here(self, sid: str) -> bool:
        """An ancestor's subject whose home is this space: this registry may add its names."""
        return self.space is not None and bool(self.space.name) and \
            self.ancestor_homes.get(sid) == self.space.name

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
        subject = self.get(sid)
        if subject is None:
            return None
        governing = self.classification(sid)   # a company or walled class names the home of all it covers
        if governing and governing.get("class") != "owner" and governing.get("home"):
            return str(governing["home"])
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
            if sid in self.ancestor_ids and not self.homed_here(sid):
                errs.append(f"registry: `{sid}` re-mints an id an ancestor already has")
            errs += _gate_errors(sid, subject)
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
