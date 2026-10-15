"""space.yaml: the one settings file of a space (shape 1).

Every tool reads its settings through `load(root)`. All time arithmetic goes
through `Space.now()` and `Space.scale()`, so the virtual clock (the SPACE_NOW
environment variable) and `time_scale` reach every window and cadence.

    python spaceyaml.py <root>        print the resolved settings and any errors
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

SHAPE = 1
PROFILES = ("brain", "project", "personal", "channel")
TEMPLATE_PROFILE = "template"
MODES = ("live", "shadow", "fixture")
LEVELS = ("propose", "after_window", "at_once")
ACTION_CLASSES = ("edit_inside", "write_knowledge", "release",
                  "write_outside_system", "send_outward", "spend")
FIXED_KINDS = ("decisions", "sources")
RECORD_KINDS = ("mail", "call", "message", "event", "document")
# The fourth wire: what a request may ask for, and the template's standing choices per profile.
REQUEST_CLASSES = {"status": "the status tier, the milestones and the last contact date",
                   "milestones": "the milestones", "contact": "the last contact date",
                   "detail": "anything else from the knowledge layer",
                   "correspondence": "mail, calls and documents"}
REQUEST_ANSWERS = ("at_once", "ask", "never")
REQUEST_DEFAULTS = {
    "project": [{"from": "any", "class": "status", "answer": "at_once"},
                {"from": "any", "class": "milestones", "answer": "at_once"},
                {"from": "any", "class": "contact", "answer": "at_once"},
                {"from": "any", "class": "correspondence", "answer": "never"}],
    "brain": [{"from": "any", "class": "status", "answer": "at_once"},
              {"from": "any", "class": "milestones", "answer": "at_once"},
              {"from": "any", "class": "contact", "answer": "at_once"},
              {"from": "any", "class": "correspondence", "answer": "never"}],
    "personal": [], "channel": [], "template": [],
}
AUDIENCE_WORDS = ("owner", "crew", "everyone")
TEMPLATE_MARKER = Path(".github") / "template-marker"
PRIVATE_STATE = Path("_meta") / "state" / "private"   # never staged (sync.PRIVATE_ZONES)
SLUG = re.compile(r"^[a-z][a-z0-9-]*$")

DEFAULTS = {
    "kinds": ["clients", "projects", "people", "domains", "topics"],
    "authority": {"edit_inside": "at_once", "write_knowledge": "propose",
                  "release": "after_window", "write_outside_system": "at_once",
                  "send_outward": "propose", "spend": "propose"},
    "floors": {"send_outward": "propose", "spend": "propose", "release": "after_window"},
    "left_out": ["price", "prices", "pricing", "fee", "fees", "commercial terms",
                 "day rate", "hourly rate", "margin", "salary", "pay", "employment",
                 "immigration", "visa", "password", "credential", "credentials",
                 "access link", "api key", "client data"],
    "never_leaves": ["equity", "settlement", "termination", "legal position", "medical",
                     "salary"],
    "high_stakes": ["pricing", "price list", "contract", "commitment", "policy",
                    "identity", "rules", "lawsuit", "litigation", "indemnity", "liability",
                    "data breach", "security incident", "gdpr", "disciplinary", "dismissal"],
    "windows": {"snapshot": 7, "status": 7, "process": 90, "directive": 90, "review": 90,
                "concept": 180, "entity": 180, "policy": 365, "decision": 365},
    "protected": ["identity/", "rules.md", "space.yaml", ".claude/", "_meta/tools/", ".github/"],
    "budget": {"tokens_per_day": 100_000_000_000},
    "window_hours": 24,
    "models": ["any"],
    "routines": {"sweep": "0 1,10,16 * * *", "daily": "0 11 * * *"},
}

# Top-level entries each profile must have, and must not have (vision section 5).
PROFILE_TABLE = {
    "brain": {"required": ["space.yaml", "index.md", "log.md", "rules.md", "identity",
                           "knowledge", "live", "_inbound", "_outbound", "_meta/subjects.yaml"],
              "forbidden": ["work", "journal", "outbox"]},
    "project": {"required": ["space.yaml", "index.md", "log.md", "knowledge", "live",
                             "_inbound", "_outbound", "_meta/subjects.yaml"],
                "forbidden": ["work", "journal"]},
    "personal": {"required": ["space.yaml", "index.md", "log.md", "knowledge", "live", "work",
                              "journal", "outbox", "_inbound", "_outbound",
                              "_meta/subjects.yaml"],
                 "forbidden": []},
    "channel": {"required": ["space.yaml", "index.md", "log.md", "_inbound", "_outbound"],
                "forbidden": ["knowledge", "live", "work", "journal", "outbox"]},
    TEMPLATE_PROFILE: {"required": ["space.yaml"], "forbidden": []},
}


def parse_iso(value: str) -> dt.datetime:
    """Parse an ISO-8601 timestamp (Z or offset) into an aware UTC datetime."""
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


@dataclass
class Space:
    root: Path
    data: dict

    # ----- settings --------------------------------------------------------
    @property
    def profile(self) -> str:
        return str(self.data.get("profile") or "").strip()

    @property
    def mode(self) -> str:
        return str(self.data.get("mode") or "live").strip()

    @property
    def name(self) -> str:
        return str(self.data.get("name") or self.root.name)

    @property
    def owner(self) -> str:
        return str(self.data.get("owner") or "")

    @property
    def owner_email(self) -> str:
        return str(self.data.get("owner_email") or "")

    @property
    def audience(self):
        return self.data.get("audience") or []

    @property
    def parent(self):
        return self.data.get("parent")

    @property
    def time_scale(self) -> float:
        value = self.data.get("time_scale", 1) or 1
        return float(value)

    @property
    def window_hours(self) -> float:
        return float(self.data.get("window_hours") or DEFAULTS["window_hours"])

    @property
    def routing(self) -> dict:
        """`routing:` turns the judgment router on (`since`, `history_since`, `review_until`,
        `archives`)."""
        value = self.data.get("routing")
        return dict(value) if isinstance(value, dict) else {}

    @property
    def routing_on(self) -> bool:
        return isinstance(self.data.get("routing"), dict)

    def _routing_date(self, key: str) -> dt.datetime | None:
        value = self.routing.get(key)
        if not value:
            return None
        try:
            day = dt.date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
        return dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)

    @property
    def review_until(self) -> dt.datetime | None:
        """Until then every projection waits the window (the owner's review period)."""
        return self._routing_date("review_until")

    @property
    def routing_since(self) -> dt.datetime | None:
        """The oldest witness page the router reads by default."""
        return self._routing_date("since")

    @property
    def routing_history_since(self) -> dt.datetime | None:
        """The start of the history window: older than `since`, judged as background; None
        means no history window."""
        return self._routing_date("history_since")

    @property
    def routing_archives(self) -> bool:
        """Archive-derived pages count in briefs and pages only when this is on (default off)."""
        return self.routing.get("archives") is True

    @property
    def authority(self) -> dict:
        merged = dict(DEFAULTS["authority"])
        merged.update(self.data.get("authority") or {})
        return merged

    @property
    def agents(self) -> list:
        return list(self.data.get("agents") or [])

    @property
    def standing_choices(self) -> list:
        return list(self.data.get("standing_choices") or [])

    @property
    def left_out(self) -> list:
        value = self.data.get("left_out")
        return list(DEFAULTS["left_out"] if value is None else value)

    @property
    def never_leaves(self) -> list:
        """This space's list plus the brain's: a space may add terms, never drop the brain's."""
        value = self.data.get("never_leaves")
        own = list(DEFAULTS["never_leaves"] if value is None else value)
        if self.profile != "brain":
            try:
                brain = self.brain_space()
            except Exception:
                brain = None
            if brain is not None:
                own += [t for t in brain.never_leaves if t not in own]
        return own

    @property
    def floors(self) -> dict:
        """The badge floors: rules.md section 3, made stricter by the brain's `floors:`."""
        order = {lvl: i for i, lvl in enumerate(LEVELS)}
        merged = dict(DEFAULTS["floors"])
        sources = [self.data.get("floors") or {}]
        if self.profile != "brain":
            try:
                brain = self.brain_space()
            except Exception:
                brain = None
            if brain is not None:
                sources.append(brain.data.get("floors") or {})
        for extra in sources:
            for action, lvl in extra.items():
                if lvl in order and (action not in merged or order[lvl] < order[merged[action]]):
                    merged[action] = lvl
        return merged

    @property
    def high_stakes(self) -> list:
        value = self.data.get("high_stakes")
        if value is None:
            brain = self.brain_space()
            if brain is not None and brain is not self:
                return brain.high_stakes
            return list(DEFAULTS["high_stakes"])
        return list(value)

    @property
    def request_choices(self) -> list:
        value = self.data.get("request_choices")
        if value is None:
            return [dict(c) for c in REQUEST_DEFAULTS.get(self.profile, [])]
        return list(value)

    @property
    def protected(self) -> list:
        value = self.data.get("protected")
        return list(DEFAULTS["protected"] if value is None else value)

    @property
    def sources(self) -> list:
        return list(self.data.get("sources") or [])

    @property
    def mounts(self) -> list:
        return list(self.data.get("mounts") or [])

    @property
    def budget(self) -> dict:
        merged = dict(DEFAULTS["budget"])
        merged.update(self.data.get("budget") or {})
        return merged

    @property
    def models(self) -> list:
        return list(self.data.get("models") or DEFAULTS["models"])

    @property
    def routines(self) -> dict:
        merged = dict(DEFAULTS["routines"])
        merged.update(self.data.get("routines") or {})
        return merged

    @property
    def windows(self) -> dict:
        merged = dict(DEFAULTS["windows"])
        merged.update(self.data.get("windows") or {})
        return merged

    @property
    def kinds(self) -> list:
        """The company's kinds: declared in the brain, inherited by every other space."""
        if self.profile == "brain" or self.profile == TEMPLATE_PROFILE:
            return list(self.data.get("kinds") or DEFAULTS["kinds"])
        brain = self.brain_space()
        if brain is not None:
            return brain.kinds
        return list(self.data.get("kinds") or DEFAULTS["kinds"])

    @property
    def all_kinds(self) -> list:
        return self.kinds + list(FIXED_KINDS)

    def is_template(self) -> bool:
        return self.profile == TEMPLATE_PROFILE

    # ----- time ------------------------------------------------------------
    def now(self) -> dt.datetime:
        """The clock. SPACE_NOW (ISO-8601) overrides it for fixtures and the shadow."""
        override = os.environ.get("SPACE_NOW")
        if override:
            return parse_iso(override)
        return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)

    def scale(self, hours: float) -> dt.timedelta:
        """A duration of `hours` on the space's clock, divided by time_scale."""
        return dt.timedelta(hours=float(hours) / self.time_scale)

    def window(self) -> dt.timedelta:
        return self.scale(self.window_hours)

    # ----- mounts ----------------------------------------------------------
    def mount_root(self, name: str) -> Path | None:
        for mount in self.mounts:
            if mount.get("name") != name:
                continue
            path = mount.get("path") or ("brain" if name == "brain" else f"mounts/{name}")
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = self.root / candidate
            if (candidate / "space.yaml").exists():
                return candidate
            return None
        return None

    def brain_space(self) -> "Space | None":
        if self.profile == "brain":
            return self
        root = self.mount_root("brain")
        return load(root) if root else None

    def ancestor_roots(self) -> list:
        """Roots of every mounted ancestor that is present on disk."""
        roots = []
        for mount in self.mounts:
            root = self.mount_root(mount.get("name", ""))
            if root is not None:
                roots.append(root)
        return roots

    # ----- schema ----------------------------------------------------------
    def errors(self) -> list:
        errs = []
        data = self.data
        if data.get("shape") != SHAPE:
            errs.append(f"shape must be {SHAPE} (got {data.get('shape')!r})")
        profile = self.profile
        if profile == TEMPLATE_PROFILE:
            if not (self.root / TEMPLATE_MARKER).exists():
                errs.append("profile `template` is legal only in the template repository "
                            "(.github/template-marker); run onboard to set a profile")
        elif profile not in PROFILES:
            errs.append(f"profile must be one of {', '.join(PROFILES)} (got {profile!r})")
        if self.mode not in MODES:
            errs.append(f"mode must be one of {', '.join(MODES)} (got {self.mode!r})")
        if "time_scale" in data:
            if self.mode == "live":
                errs.append("time_scale is accepted only when mode is shadow or fixture")
            try:
                if float(data["time_scale"]) < 1:
                    errs.append("time_scale must be 1 or more")
            except (TypeError, ValueError):
                errs.append("time_scale must be a number")
        for action, level in (data.get("authority") or {}).items():
            if action not in ACTION_CLASSES:
                errs.append(f"authority: unknown action class `{action}`")
            elif level not in LEVELS:
                errs.append(f"authority.{action}: level must be one of {', '.join(LEVELS)}")
        for action, floor in self.floors.items():
            have = self.authority.get(action)
            if have in LEVELS and floor in LEVELS and LEVELS.index(have) > LEVELS.index(floor):
                errs.append(f"authority.{action} may not rise above `{floor}` (rules.md section 3)")
        for n, row in enumerate(self.agents):
            if not isinstance(row, dict) or not row.get("name") or not row.get("acts_for"):
                errs.append(f"agents[{n}]: each row names the agent (`name`) and whom it acts for (`acts_for`)")
                continue
            for action, lvl in (row.get("badge") or {}).items():
                if action not in ACTION_CLASSES or lvl not in LEVELS:
                    errs.append(f"agents[{n}].badge.{action}: an action class and a level")
                elif LEVELS.index(lvl) > LEVELS.index(self.authority.get(action, "propose")):
                    errs.append(f"agents[{n}].badge.{action}: an agent row never rises above the space's level")
        for ptype, days in (data.get("windows") or {}).items():
            if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
                errs.append(f"windows.{ptype}: a whole number of days")
        if self.window_hours < DEFAULTS["window_hours"]:
            errs.append("window_hours: 24 is the floor")
        declared_kinds = data.get("kinds")
        if declared_kinds is not None:
            if not isinstance(declared_kinds, list) or not declared_kinds:
                errs.append("kinds must be a non-empty list")
            else:
                for kind in declared_kinds:
                    if not isinstance(kind, str) or not SLUG.match(kind):
                        errs.append(f"kinds: `{kind}` is not a lowercase slug")
                    elif kind in FIXED_KINDS:
                        errs.append(f"kinds: `{kind}` is fixed and may not be declared")
            if profile not in ("brain", TEMPLATE_PROFILE):
                brain = self.brain_space()
                if brain is not None and list(declared_kinds) != brain.kinds:
                    errs.append("kinds are declared once, in the brain; this space's list differs")
        for choice in self.standing_choices:
            if not isinstance(choice, dict) or not choice.get("destination"):
                errs.append("standing_choices: each entry needs a destination")
            elif choice.get("kind") not in RECORD_KINDS:
                errs.append(f"standing_choices: kind must be one of {', '.join(RECORD_KINDS)}")
        for choice in data.get("request_choices") or []:
            if not isinstance(choice, dict) or choice.get("class") not in REQUEST_CLASSES:
                errs.append(f"request_choices: class must be one of {', '.join(REQUEST_CLASSES)}")
            elif str(choice.get("answer")) not in REQUEST_ANSWERS:
                errs.append(f"request_choices: answer must be one of {', '.join(REQUEST_ANSWERS)}")
        if profile == "channel":
            between = data.get("between")
            if not isinstance(between, list) or len(between) != 2:
                errs.append("between: a channel joins exactly two spaces, by name")
        transcripts = data.get("transcripts")
        if transcripts is not None:
            if profile not in ("personal", TEMPLATE_PROFILE):
                errs.append("transcripts: only a personal space reads its person's transcripts")
            if not isinstance(transcripts, dict):
                errs.append("transcripts must be {path, switch}")
            elif str(transcripts.get("switch", "off")).lower() not in ("on", "off", "true", "false"):
                errs.append("transcripts.switch must be on or off")
            elif not isinstance(transcripts.get("exclude_words") or [], list):
                errs.append("transcripts.exclude_words must be a list of words or phrases")
        for key in ("left_out", "never_leaves", "protected", "models"):
            value = data.get(key)
            if value is not None and not isinstance(value, list):
                errs.append(f"{key} must be a list")
        tokens = self.budget.get("tokens_per_day")
        if not isinstance(tokens, int) or tokens <= 0:
            errs.append("budget.tokens_per_day must be a positive integer")
        for mount in self.mounts:
            if not isinstance(mount, dict) or not mount.get("name"):
                errs.append("mounts: each entry needs a name")
        for source in self.sources:
            if not isinstance(source, dict) or not source.get("name"):
                errs.append("sources: each entry needs a name")
                continue
            audience = source.get("audience")
            if not (audience in AUDIENCE_WORDS or isinstance(audience, list)):
                errs.append(f"sources.{source['name']}: audience must be owner, crew, "
                            "everyone or a list of names")
        if "routing" in data and not isinstance(data.get("routing"), dict):
            errs.append("routing: must be a mapping (since, history_since, review_until, archives)")
        for key in ("since", "history_since", "review_until"):
            value = self.routing.get(key)
            if value and self._routing_date(key) is None:
                errs.append(f"routing: {key} must be a date (YYYY-MM-DD)")
        if "archives" in self.routing and not isinstance(self.routing["archives"], bool):
            errs.append("routing: archives must be true or false")
        return errs

    def resolved(self) -> dict:
        return {
            "root": str(self.root), "profile": self.profile, "mode": self.mode,
            "name": self.name, "owner": self.owner, "kinds": self.kinds,
            "time_scale": self.time_scale, "window": str(self.window()),
            "authority": self.authority, "mounts": [m.get("name") for m in self.mounts],
        }


def load(root) -> Space:
    root = Path(root)
    path = root / "space.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no space.yaml under {root}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: space.yaml must be a mapping")
    return Space(root=root, data=data)


def save(space: Space) -> None:
    text = yaml.safe_dump(space.data, sort_keys=False, allow_unicode=True, width=100)
    (space.root / "space.yaml").write_text(text, encoding="utf-8", newline="\n")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(argv[0]) if argv else Path(".")
    space = load(root)
    print(json.dumps(space.resolved(), indent=2, default=str))
    errs = space.errors()
    for err in errs:
        print(f"error: {err}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
