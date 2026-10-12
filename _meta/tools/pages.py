"""Pages: frontmatter, permanent ids and the approval fingerprint (shape 1).

A page is a markdown file with a YAML frontmatter block. Every page carries a
permanent `id`; links point at ids (`[text](id:p-1a2b3c4d)`), so moves never break
them. A signed page (`status: stable`) carries `signed_by`, `signed_at`,
`signed_in` and `fingerprint`, the sha256 of the exact body that was approved.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import secrets
from pathlib import Path

import yaml

FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.S)
ID_RE = re.compile(r"^p-[0-9a-f]{8}$")
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


def write(path, fm: dict, body: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(fm, body), encoding="utf-8", newline="\n")
    return path


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
    return path.suffix == ".md" and path.name not in ("index.md", "log.md", "README.md")
