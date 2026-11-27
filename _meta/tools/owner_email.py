"""Render a short owner notice into a ready-to-send HTML email (shape 1).

A routine writes a small JSON notice; this module checks it for plain language and
fills `_meta/templates/email.html`, so no agent composes email HTML by hand. Nothing
here sends: the payload is what the agent hands to its mail connector. The recipient
is `owner_email` in the space's `space.yaml`; `--to` overrides it.

    python owner_email.py check  NOTICE.json
    python owner_email.py render NOTICE.json [--root R] [--to ADDR] [--out PAYLOAD.json]
                                             [--preview PAGE.html] [--date YYYY-MM-DD]

`render` prints (or writes) the send arguments {"to": [...], "subject": ..., "htmlBody":
..., "body": ...}. Exit code 2 means the notice failed the check; the errors say what
to rewrite. `--out` and `--preview` write only inside the space's
`_meta/state/notices/preview/` folder (a bare file name lands there), a `.json` and an `.html`
file, never through a link: a command line from a routine can never make them overwrite a
skill, a tool or a file outside the space. `record` moves only a `.json` file of
`_meta/state/notices/pending/`.

A notice: {"kind": security|action|fyi|briefing, "sender": <routine>, "title", "summary",
"steps": [...], "stats": [{"count", "label", "hint"}], "link": {"label", "url"},
"reassure", "also": [{"title", "text"}], "allow_terms": [...], "reference"}. No reader-facing
text holds a line break or a control character, so the subject stays one header line. An `also`
text may quote a record, so it is escaped and never bold. A
`reference` (the morning view's reply reference) is shown in the footer.

Queued security notices join one pending digest, and `pending` holds it back while a
security email went out in the last 24 hours: at most one a day, however many findings.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "templates" / "email.html"

KINDS = {
    #          badge text        accent     badge background
    "security": ("Security check", "#b91c1c", "#fee2e2"),
    "action":   ("Action needed",  "#b45309", "#fef3c7"),
    "fyi":      ("For your info",  "#1d4ed8", "#dbeafe"),
    "briefing": ("Morning briefing", "#047857", "#d1fae5"),
}

# Which routine sent it, in words a reader outside the machinery understands.
SENDERS = {
    "morning-view": "daily briefing",
    "sweep": "evidence check",
    "check": "consistency check",
    "status": "status refresh",
    "release": "release check",
    "sync": "sync check",
}

LIMITS = {"title": 70, "summary": 280, "step": 200, "steps": 5, "reassure": 220,
          "also": 2, "also_text": 240, "stats": 6, "words": 200}

# Words of the machinery a reader will not know -> what to say instead.
JARGON = {
    r"brain": "the company knowledge base",
    r"candidates?": "notes waiting for your sign-off",
    r"witness(?:ed)?": "a saved note",
    r"watermarks?": "leave it out",
    r"sweeps?": "check",
    r"distill(?:ed)?": "summarise",
    r"routines?": "scheduled check",
    r"outbox": "drafts waiting for you to send",
    r"promot(?:e|ed|ion)": "approve",
    r"charter": "your privacy rules",
    r"frontmatter": "leave it out",
    r"jsonl": "leave it out",
    r"fingerprints?": "leave it out",
    r"mounts?": "leave it out",
    r"librarian": "the upkeep check",
}
JARGON_RE = [(re.compile(rf"\b{pat}\b", re.I), fix) for pat, fix in JARGON.items()]
PATH_RE = re.compile(r"(?:\b(?:_inbound|_outbound|_meta|_scratch|knowledge|live|journal|work|outbox|"
                     r"brain|mounts|identity)/|\b[\w-]+\.(?:md|json|jsonl|yaml|yml|py)\b)", re.I)
URL_RE = re.compile(r"https?://|www\.", re.I)
SECRET_RE = re.compile(r"\b(?:sk|pk|rk|ghp|gho|xox[abpr])[-_][A-Za-z0-9_-]{8,}|[A-Za-z0-9+/_=-]{32,}")
DASH_RE = re.compile("[—–]")
# Line breaks and control characters, the line and paragraph separators, zero-width and
# bidirectional marks: none belongs in a notice, and in the subject a line break starts a header.
CONTROL_RE = re.compile("[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]")


class NoticeError(ValueError):
    def __init__(self, problems: list):
        super().__init__("\n".join(problems))
        self.problems = problems


def _texts(notice: dict) -> list:
    """Every reader-facing string with a label for error messages."""
    out = [("title", notice.get("title", "")), ("summary", notice.get("summary", ""))]
    out += [(f"steps[{i}]", s) for i, s in enumerate(notice.get("steps", []))]
    for i, s in enumerate(notice.get("stats", [])):
        out += [(f"stats[{i}].label", s.get("label", "")), (f"stats[{i}].hint", s.get("hint", ""))]
    if notice.get("reassure"):
        out.append(("reassure", notice["reassure"]))
    for i, a in enumerate(notice.get("also", [])):
        out += [(f"also[{i}].title", a.get("title", "")), (f"also[{i}].text", a.get("text", ""))]
    if notice.get("link"):
        out.append(("link.label", notice["link"].get("label", "")))
    return out


def check(notice: dict) -> list:
    """Return every problem; an empty list means the notice may be sent."""
    problems: list = []
    kind = notice.get("kind")
    if kind not in KINDS:
        problems.append(f"kind must be one of {sorted(KINDS)}")
    for field in ("title", "summary"):
        if not str(notice.get(field, "")).strip():
            problems.append(f"{field} is required")
    if kind == "briefing" and not (notice.get("stats") or notice.get("reassure") or notice.get("also")):
        problems.append("a briefing needs stats, or a reassure or also line on an all-quiet day")
    if kind in ("security", "action") and not notice.get("steps"):
        problems.append(f"a {kind} notice needs steps: what exactly should the reader do?")

    def over(label, value, limit):
        if len(value) > limit:
            problems.append(f"{label} is {len(value)} characters; keep it under {limit}")

    over("title", notice.get("title", ""), LIMITS["title"])
    over("summary", notice.get("summary", ""), LIMITS["summary"])
    over("reassure", notice.get("reassure", ""), LIMITS["reassure"])
    if len(notice.get("steps", [])) > LIMITS["steps"]:
        problems.append(f"at most {LIMITS['steps']} steps; move the rest to a follow-up")
    for i, s in enumerate(notice.get("steps", [])):
        over(f"steps[{i}]", s, LIMITS["step"])
    if len(notice.get("also", [])) > LIMITS["also"]:
        problems.append(f"at most {LIMITS['also']} 'also' items")
    for i, a in enumerate(notice.get("also", [])):
        over(f"also[{i}].text", a.get("text", ""), LIMITS["also_text"])
    if len(notice.get("stats", [])) > LIMITS["stats"]:
        problems.append(f"at most {LIMITS['stats']} stats")
    link = notice.get("link")
    if link and not re.match(r"https://", link.get("url", "")):
        problems.append("link.url must be an https:// address")
    if link and CONTROL_RE.search(str(link.get("url", ""))):
        problems.append("link.url: no line breaks or control characters")

    allowed = {t.lower() for t in notice.get("allow_terms", [])}
    words = 0
    for label, text in _texts(notice):
        text = str(text)
        words += len(text.split())
        if CONTROL_RE.search(text):
            problems.append(f"{label}: no line breaks or control characters; keep it on one line")
        if DASH_RE.search(text):
            problems.append(f"{label}: no em or en dashes; use a comma or a full stop")
        if URL_RE.search(text):
            problems.append(f"{label}: put links in the link field, not in the text")
        if PATH_RE.search(text):
            problems.append(f"{label}: no file paths or internal references ({PATH_RE.search(text).group(0)!r})")
        if SECRET_RE.search(text):
            problems.append(f"{label}: looks like a key, token or ID string; never put secrets or long codes in a notice")
        for rx, fix in JARGON_RE:
            m = rx.search(text)
            if m and m.group(0).lower() not in allowed:
                problems.append(f"{label}: '{m.group(0)}' is machinery jargon; say '{fix}' instead")
    if words > LIMITS["words"]:
        problems.append(f"{words} words in total; keep the whole notice under {LIMITS['words']}")
    return problems


def _inline(text: str) -> str:
    """Escape, then allow **bold** as the only markup."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(str(text)))


def _plain(text: str) -> str:
    return str(text).replace("**", "")


def _escaped(text: str) -> str:
    """Escaped and nothing else: for an `also` text, which may quote what someone outside wrote
    (a mail subject), so its `**` never turns into the notice's own emphasis."""
    return html.escape(str(text))


def _section(heading: str, inner: str) -> str:
    return ('<tr><td style="padding:18px 28px 4px;">'
            f'<h2 style="margin:0 0 10px;font-size:13px;letter-spacing:.4px;text-transform:uppercase;color:#6b7280;">{heading}</h2>'
            f"{inner}</td></tr>")


def _blocks(notice: dict, accent: str) -> str:
    parts = []
    if notice.get("stats"):
        rows = "".join(
            '<tr><td valign="top" style="width:52px;padding:6px 0;">'
            f'<div style="width:40px;height:40px;border-radius:10px;background:#f3f4f6;color:{accent};'
            f'font-size:20px;font-weight:700;line-height:40px;text-align:center;">{int(s["count"])}</div></td>'
            f'<td valign="top" style="padding:6px 0;font-size:15px;line-height:1.45;"><strong>{_inline(s["label"])}</strong>'
            + (f'<br><span style="color:#6b7280;font-size:13px;">{_inline(s["hint"])}</span>' if s.get("hint") else "")
            + "</td></tr>"
            for s in notice["stats"])
        parts.append(_section("Today", f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">{rows}</table>'))
    if notice.get("steps"):
        rows = "".join(
            '<tr><td valign="top" style="width:34px;padding:5px 0;">'
            f'<div style="width:24px;height:24px;border-radius:50%;background:{accent};color:#fff;font-size:13px;'
            f'font-weight:700;line-height:24px;text-align:center;">{i}</div></td>'
            f'<td valign="top" style="padding:7px 0;font-size:15px;line-height:1.5;">{_inline(s)}</td></tr>'
            for i, s in enumerate(notice["steps"], 1))
        parts.append(_section("What to do", f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">{rows}</table>'))
    if notice.get("link"):
        parts.append('<tr><td style="padding:10px 28px 4px;">'
                     f'<a href="{html.escape(notice["link"]["url"], quote=True)}" style="display:inline-block;padding:10px 18px;'
                     f'border-radius:8px;background:{accent};color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;">'
                     f'{_inline(notice["link"]["label"])}</a></td></tr>')
    if notice.get("reassure"):
        parts.append('<tr><td style="padding:16px 28px 4px;"><div style="padding:12px 14px;border-radius:8px;'
                     'background:#f0fdf4;border-left:4px solid #16a34a;font-size:14px;line-height:1.5;color:#14532d;">'
                     f'{_inline(notice["reassure"])}</div></td></tr>')
    if notice.get("also"):
        inner = "".join(f'<p style="margin:0 0 10px;font-size:14px;line-height:1.5;color:#374151;">'
                        f'<strong>{_inline(a["title"])}</strong> {_escaped(a["text"])}</p>' for a in notice["also"])
        parts.append(_section("Also worth knowing", inner))
    return "".join(parts)


REFERENCE_RE = re.compile(r"^[a-z2-7]{8,32}\Z")


def _footer(notice: dict, today: str) -> str:
    who = SENDERS.get(notice.get("sender", ""), "space assistant")
    text = f"Sent automatically by your space's assistant ({who}) on {today}. You don't need to reply."
    reference = str(notice.get("reference") or "")
    if REFERENCE_RE.match(reference):   # what a reply quotes so its answers are read
        text += f" If you answer by reply, keep this line: reference {reference}."
    return text


def render(notice: dict, to: list, today: str | None = None) -> dict:
    """The send payload for a notice that passes the check; NoticeError otherwise."""
    problems = check(notice)
    if problems:
        raise NoticeError(problems)
    today = today or dt.date.today().isoformat()
    badge, accent, badge_bg = KINDS[notice["kind"]]
    # one header line whatever happened before: check() already refuses a line break
    subject = " ".join(CONTROL_RE.sub(" ", f"{badge}: {_plain(notice['title'])}").split())
    fills = {
        "subject": html.escape(subject), "preheader": html.escape(_plain(notice["summary"])),
        # Stops the inbox preview line from running on into the badge and title.
        "preheader_pad": "&#847;&nbsp;" * 100,
        "accent": accent, "badge_bg": badge_bg, "badge": badge,
        "title": _inline(notice["title"]), "summary": _inline(notice["summary"]),
        "blocks": _blocks(notice, accent), "footer": html.escape(_footer(notice, today)),
    }
    page = re.sub(r"@@(\w+)@@", lambda m: fills[m.group(1)], TEMPLATE.read_text(encoding="utf-8"))

    text = [_plain(notice["title"]).upper(), "", _plain(notice["summary"])]
    if notice.get("stats"):
        text += ["", "TODAY"] + [f"  {int(s['count'])}  {_plain(s['label'])}" + (f" ({_plain(s['hint'])})" if s.get("hint") else "")
                                 for s in notice["stats"]]
    if notice.get("steps"):
        text += ["", "WHAT TO DO"] + [f"  {i}. {_plain(s)}" for i, s in enumerate(notice["steps"], 1)]
    if notice.get("link"):
        text += ["", f"{_plain(notice['link']['label'])}: {notice['link']['url']}"]
    if notice.get("reassure"):
        text += ["", _plain(notice["reassure"])]
    if notice.get("also"):
        text += ["", "ALSO WORTH KNOWING"] + [f"  {_plain(a['title'])} {_plain(a['text'])}" for a in notice["also"]]
    text += ["", "--", _footer(notice, today)]
    return {"to": list(to), "subject": subject, "htmlBody": page, "body": "\n".join(text)}


def find_root(start=None) -> Path | None:
    """The nearest space root (holding space.yaml) at or above `start`, else this tool's own."""
    start = Path(start or Path.cwd()).resolve()
    for base in [start, *start.parents, *HERE.parents]:
        if (base / "space.yaml").is_file():
            return base
    return None


def recipient(root=None) -> list:
    """[owner_email] from the space's space.yaml; SystemExit with a plain reason when unset."""
    found = find_root(root)
    if found is None:
        raise SystemExit("no recipient: no space.yaml found; pass --to or run inside a space")
    email = spaceyaml.load(found).owner_email.strip()
    if not email or "@" not in email or "{{" in email:
        raise SystemExit(f"no recipient: set owner_email in {found / 'space.yaml'} (onboard fills it) or pass --to")
    return [email]


# ----- the queue: notices a routine raised, sent by the chain to the person themselves -------
NOTICES = Path("_meta") / "state" / "notices"


def _known_keys(root: Path) -> set:
    keys = set()
    for folder in ("pending", "sent"):
        for path in (Path(root) / NOTICES / folder).glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            keys.add(data.get("key"))
            keys.update(data.get("keys") or [])
    return keys


# Security findings make one email: every one joins the pending digest, and at most one
# security email leaves in any 24 hours; the rest wait for the next.
SECURITY_GAP = dt.timedelta(hours=24)


def _kind(path: Path) -> str | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("kind")
    except (OSError, ValueError):
        return None


def _day(text: str) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(str(text).strip(), "%d %B %Y")
    except ValueError:
        return None


def _security_digest(notices: list) -> dict:
    """One notice for several findings: counted by where they showed up, with their dates."""
    groups: dict = {}
    for n in notices:
        groups.setdefault(n.get("where") or "other places", []).append(n.get("when") or "")
    stats = []
    for where, whens in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:LIMITS["stats"]]:
        stat = {"count": len(whens), "label": f"in your {where}"}
        dates = sorted({d for d in map(_day, whens) if d})
        if dates:
            first, last = (f"{d.day} {d:%B %Y}" for d in (dates[0], dates[-1]))
            stat["hint"] = f"on {first}" if first == last else f"from {first} to {last}"
        stats.append(stat)
    return {"kind": "security", "sender": notices[-1].get("sender", "sweep"),
            "title": f"{len(notices)} passwords or keys showed up in your records",
            "summary": f"{len(notices)} items look like they hold a password or key. "
                       "None of the values was stored anywhere.",
            "stats": stats,
            "steps": ["Open the items from those dates and check whether each key is real.",
                      "If one is, change it where it was issued and tell whoever shared it."],
            "reassure": "Nothing was copied: each item was kept without the value. "
                        "You get at most one of these emails a day."}


def _security_file(path: Path, notices: list, to: str, today: str, queued_at: str) -> None:
    combined = notices[0] if len(notices) == 1 else _security_digest(notices)
    data = {"key": notices[0].get("key"), "keys": [n["key"] for n in notices if n.get("key")], "kind": "security",
            "queued_at": queued_at, "notices": notices, "notice": combined, "payload": render(combined, to, today)}
    pages.write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _new_file(folder: Path, stem: str) -> Path:
    """The first `<stem>-<n>.json` not in use: a queue only ever adds a file, never one a notice still holds."""
    n = len(list(folder.glob("*.json"))) + 1
    while (folder / f"{stem}-{n}.json").exists():
        n += 1
    return folder / f"{stem}-{n}.json"


def _queue_security(root: Path, notice: dict, space, today: str) -> Path:
    """A security notice in a pending file of its own: a queue only ever adds a file, so the cloud sweep queueing one
    never touches the file the computer's daily is sending (`pending` joins them into one digest there)."""
    to = recipient(root)
    render(notice, to, today)   # the notice itself must pass the check
    folder = root / NOTICES / "pending"
    folder.mkdir(parents=True, exist_ok=True)
    path = _new_file(folder, f"{space.now():%Y%m%dT%H%M%S}-security")
    _security_file(path, [notice], to, today, f"{space.now():%Y-%m-%dT%H:%M:%SZ}")
    return path


def _join_security(root: Path, paths: list, now: dt.datetime) -> list:
    """The pending security files joined into the oldest, as one digest, the others removed: one email for them all.
    Only the sender, on the computer, joins; a queue never rewrites a file."""
    found = [p for p in paths if _kind(p) == "security"]
    if len(found) < 2:
        return paths
    datas = [json.loads(p.read_text(encoding="utf-8")) for p in found]
    notices = [n for d in datas for n in (d.get("notices") or ([d["notice"]] if d.get("notice") else []))]
    _security_file(found[0], notices, recipient(root), f"{now:%Y-%m-%d}", datas[0].get("queued_at") or "")
    for extra in found[1:]:
        extra.unlink()
    return [p for p in paths if p not in found[1:]]


def _security_sent_within(root: Path, now: dt.datetime) -> bool:
    """Whether a security email went out less than SECURITY_GAP before `now` (a sent notice
    without `sent_at` counts by its file time)."""
    for path in (root / NOTICES / "sent").glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("kind") != "security":
            continue
        try:
            sent = dt.datetime.fromisoformat(str(data["sent_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            sent = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
        if now - sent < SECURITY_GAP:
            return True
    return False


def queue(root, notice: dict, today: str | None = None) -> Path | None:
    """Render a notice for the space's own person and leave it for the chain to send; None when
    a notice with the same `key` was queued or sent before. A failing check raises NoticeError."""
    root = Path(root)
    key = notice.get("key")
    if key and key in _known_keys(root):
        return None
    space = spaceyaml.load(root)
    today = today or f"{space.now():%Y-%m-%d}"
    if notice.get("kind") == "security":
        return _queue_security(root, notice, space, today)
    payload = render(notice, recipient(root), today)
    folder = root / NOTICES / "pending"
    folder.mkdir(parents=True, exist_ok=True)
    path = _new_file(folder, f"{space.now():%Y%m%dT%H%M%S}-{notice.get('kind')}")
    pages.write_text(path, json.dumps({"key": key, "kind": notice.get("kind"),
                                       "queued_at": f"{space.now():%Y-%m-%dT%H:%M:%SZ}",
                                       "notice": notice, "payload": payload}, ensure_ascii=False, indent=2) + "\n",
                     root=root)
    return path


def pending(root, now: dt.datetime | None = None) -> list:
    """The notices to send now: a security one waits while one went out in the last 24 hours; the security ones
    pending are joined into one digest first (`_join_security`)."""
    paths = sorted((Path(root) / NOTICES / "pending").glob("*.json"))
    if any(_kind(p) == "security" for p in paths):
        now = now or spaceyaml.load(root).now()
        if _security_sent_within(Path(root), now):
            paths = [p for p in paths if _kind(p) != "security"]
        else:
            paths = _join_security(Path(root), paths, now)
    return paths


def pending_file(root, path) -> Path:
    """`path` when it is a `.json` file directly in this space's pending notices, a regular file
    and no link; else UnsafePath. `record` reads, rewrites and removes only such a file."""
    if root is None:
        raise pages.UnsafePath("no space.yaml found: pass --root")
    folder = Path(root) / NOTICES / "pending"
    path = Path(path)
    if path.suffix != ".json" or pages.name_problem(path.name):
        raise pages.UnsafePath(f"{pages._shown(str(path))} is not a pending notice file")
    pages.check_no_links(path, root=root)
    try:
        inside = path.resolve().parent == folder.resolve()
    except OSError:
        inside = False
    if not inside or path.is_symlink() or not path.is_file():
        raise pages.UnsafePath(f"{pages._shown(str(path))} is not a file in {NOTICES.as_posix()}/pending")
    return path


def record(root, path, message_id: str | None = None, failed: str | None = None,
           now: dt.datetime | None = None) -> Path:
    """After the send: move the notice to sent/ with its message id, or note the failure and
    leave it pending for the next chain. Only a pending notice file (`pending_file`)."""
    path = pending_file(root, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if failed:
        data["failed"] = list(data.get("failed") or []) + [failed]
        pages.write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", root=root)
        return path
    data["message_id"] = message_id
    data["sent_at"] = f"{now or spaceyaml.load(root).now():%Y-%m-%dT%H:%M:%SZ}"
    target = Path(root) / NOTICES / "sent" / path.name
    pages.write_text(target, json.dumps(data, ensure_ascii=False, indent=2) + "\n", root=root)
    path.unlink()
    return target


PREVIEW = NOTICES / "preview"


def output_path(root, value, suffix: str) -> Path:
    """Where `--out` (`.json`) or `--preview` (`.html`) may write: inside this space's preview
    folder only (a bare file name lands there), with that suffix, never through a link; else
    UnsafePath. A command line, a routine's included, cannot point them anywhere else."""
    if root is None:
        raise pages.UnsafePath("no space.yaml found: pass --root")
    folder = Path(root) / PREVIEW
    path = Path(str(value))
    if len(path.parts) == 1:
        path = folder / path
    if path.suffix.lower() != suffix or pages.name_problem(path.name):
        raise pages.UnsafePath(f"{pages._shown(str(value))} must be a {suffix} file name")
    try:
        inside = folder.resolve() in path.resolve().parents
    except OSError:
        inside = False
    if not inside:
        raise pages.UnsafePath(f"{pages._shown(str(value))} is outside {PREVIEW.as_posix()}/")
    pages.check_no_links(path, root=root)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "render", "queue", "pending", "record"])
    ap.add_argument("notice", nargs="?", default="-",
                    help="notice JSON file, or - for stdin (queue, check, render); the pending file (record)")
    ap.add_argument("--id", dest="message_id", help="record: the sent message's id")
    ap.add_argument("--failed", help="record: why the send failed; the notice stays pending")
    ap.add_argument("--root", help="the space whose owner_email is the recipient (default: the nearest space.yaml)")
    ap.add_argument("--to", action="append", help="recipient; overrides owner_email")
    ap.add_argument("--out", help="write the send payload JSON here instead of stdout")
    ap.add_argument("--preview", help="also write the HTML page here to look at")
    ap.add_argument("--date", help="footer date (default: today)")
    args = ap.parse_args(argv)
    import needs_you   # here and not at the top: needs_you's helpers are the CLI's alone
    needs_you.utf8_stdio()
    if args.command == "pending":
        root = find_root(args.root)
        for path in pending(root) if root else []:
            print(path)
        return 0
    if args.command == "record":
        root = find_root(args.root)
        try:
            print(record(root, args.notice, message_id=args.message_id, failed=args.failed))
        except pages.UnsafePath as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        return 0

    raw = sys.stdin.read() if args.notice == "-" else Path(args.notice).read_text(encoding="utf-8")
    notice = json.loads(raw)
    if args.command == "queue":
        try:
            path = queue(find_root(args.root), notice, args.date)
        except NoticeError as e:
            for p in e.problems:
                print(f"- {p}", file=sys.stderr)
            return 2
        print(path or "already queued or sent")
        return 0
    if args.command == "check":
        problems = check(notice)
        for p in problems:
            print(f"- {p}", file=sys.stderr)
        print("ok" if not problems else f"{len(problems)} problem(s)")
        return 2 if problems else 0
    try:
        payload = render(notice, args.to or recipient(args.root), args.date)
    except NoticeError as e:
        for p in e.problems:
            print(f"- {p}", file=sys.stderr)
        return 2
    try:   # both checked before either is written: a refused one writes nothing at all
        space_root = find_root(args.root) if (args.preview or args.out) else None
        preview = output_path(space_root, args.preview, ".html") if args.preview else None
        target = output_path(space_root, args.out, ".json") if args.out else None
    except pages.UnsafePath as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if preview is not None:
        pages.write_text(preview, payload["htmlBody"], root=space_root)
    out = json.dumps(payload, ensure_ascii=False)
    if target is not None:
        pages.write_text(target, out, root=space_root)
        print(f"wrote {target}: {payload['subject']}")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
