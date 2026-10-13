"""Transcripts: the capture rate, and a sweep adapter for sessions capture missed (shape 1).

Capture by instruction is the way a session's evidence enters the person's space. This
adapter measures whether it works and, only when the person switches it on, fills the gap:

    transcripts: {path: ~/.claude/projects, switch: off, exclude: [academic]}   # personal space.yaml

`scan` reads the harness's session transcripts under `path` (`<project>/<session>.jsonl`, one
JSON object per line per event: `sessionId`, `cwd`, `timestamp`, `message`; files untouched for
four scaled weeks are skipped, and so is a session whose folder names an `exclude` word, such
as the private zones' work, or whose person typed an `exclude_words` word or phrase, matched as
whole words), records every session in
`_meta/state/sessions.yaml` (id, start, end, working directory, captured; never content) and
marks a session captured when a daily note carries its marker (`<!-- session: <id> -->`) or
an entry heading (`## HH:MMZ`) falls inside the session's span. The health line shows the
share captured over four scaled weeks. With `switch: on`, each finished session that capture
missed becomes a witness page under `knowledge/sources/` in this personal space only: what
the person asked and how the session ended, no attachment, no subject, so it never travels
(`transcript` is on every destination's left-out list).

    python transcripts.py [root] scan
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

SESSIONS = Path("_meta") / "state" / "sessions.yaml"
MARKER = re.compile(r"<!--\s*session:\s*([A-Za-z0-9_.-]+)\s*-->")
HEADING = re.compile(r"^##\s+(\d{2}):(\d{2})Z\b", re.M)
FINISHED_HOURS = 1
GRACE_MINUTES = 30
EXCERPT = 1500
WINDOW_DAYS = 28
# A session a scheduler started opens with its task's tag: routines are not a person's sessions.
ROUTINE_MARKERS = ("<scheduled-task",)


def settings(space: spaceyaml.Space) -> dict:
    data = space.data.get("transcripts") or {}
    if not isinstance(data, dict):
        return {}
    out = dict(data)
    if out.get("path"):
        out["path"] = os.path.expanduser(os.path.expandvars(str(out["path"])))
    out["switch"] = "on" if str(out.get("switch")).lower() in ("on", "true", "yes") else "off"
    return out


def _typed_any(asked: list, words: list) -> bool:
    """Whether the person typed any of the words or phrases, as whole words, ignoring case."""
    if not words:
        return False
    text = " ".join(asked).lower()
    return any(re.search(r"(?<![^\W_])" + re.escape(w) + r"(?![^\W_])", text) for w in words)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content
                         if isinstance(part, dict) and part.get("type") == "text")
    return ""


def read_session(path: Path) -> dict | None:
    """id, start, end, cwd, the person's prompts and the session's last words."""
    start = end = None
    cwd, asked, last, first = "", [], "", None
    sid = path.stem
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        sid = str(event.get("sessionId") or sid)
        cwd = cwd or str(event.get("cwd") or "")
        stamp = event.get("timestamp")
        if stamp:
            try:
                moment = pages.parse_iso(stamp)
            except (TypeError, ValueError):
                moment = None
            if moment:
                start = moment if start is None or moment < start else start
                end = moment if end is None or moment > end else end
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        role = message.get("role") or event.get("type")
        text = _text(message.get("content")).strip()
        if role == "user" and first is None and text:
            first = text
        if role == "user" and text and not text.startswith("<"):
            asked.append(text)
        elif role == "assistant" and text:
            last = text
    if start is None:
        return None
    routine = any(str(first or "").startswith(marker) for marker in ROUTINE_MARKERS)
    return {"id": sid, "start": start, "end": end or start, "cwd": cwd, "asked": asked, "last": last,
            "routine": routine}


def _journal_texts(root: Path) -> dict:
    """date (YYYY-MM-DD) -> the daily note's text."""
    out = {}
    base = root / "journal"
    for path in base.rglob("*.md") if base.is_dir() else []:
        out[path.stem] = path.read_text(encoding="utf-8", errors="replace")
    return out


def captured(session: dict, notes: dict) -> bool:
    if any(session["id"] in MARKER.findall(text) for text in notes.values()):
        return True
    start, end = session["start"], session["end"] + dt.timedelta(minutes=GRACE_MINUTES)
    day = start
    while day.date() <= end.date():
        text = notes.get(f"{day:%Y-%m-%d}", "")
        for hour, minute in HEADING.findall(text):
            moment = day.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
            if start <= moment <= end:
                return True
        day = (day + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return False


def _witness(root: Path, session: dict, now: dt.datetime, space: spaceyaml.Space) -> Path:
    import release
    asked = "\n".join(f"- {' '.join(a.split())[:300]}" for a in session["asked"][:8]) or "- (nothing recorded)"
    last = " ".join(session["last"].split())[:EXCERPT] or "(nothing recorded)"
    body = (f"# Session {session['start']:%Y-%m-%d %H:%M}Z in {Path(session['cwd']).name or 'a folder'}\n\n"
            f"Capture missed this session; the transcript adapter recorded it.\n\n"
            f"The person asked:\n{asked}\n\nIt ended with:\n{last}\n")
    fm = {"id": pages.new_id(), "type": "source", "title": f"Session in {Path(session['cwd']).name or 'a folder'} "
          f"on {session['start']:%Y-%m-%d}", "status": "draft", "generated": pages.generated("transcripts", now),
          "record": f"session-{session['id']}", "source": "sessions", "kind": "session",
          "at": pages.iso(session["start"]), "from": space.owner, "mentions": [], "audience": "owner",
          "identifiers": {}}
    if release.first_term(body, space.never_leaves):
        fm["sensitive"] = True
    target = root / "knowledge" / "sources" / f"{session['start']:%Y}" / f"{session['start']:%m}" / \
        f"session-{session['id']}.md"
    return pages.write(target, fm, body)


def scan(root, now: dt.datetime | None = None) -> dict:
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    cfg = settings(space)
    report = {"seen": 0, "captured": 0, "witnessed": 0, "switch": cfg.get("switch", "off")}
    if space.profile != "personal" or not cfg.get("path"):
        return report
    base = Path(cfg["path"])
    notes = _journal_texts(root)
    rows = []
    since = now - space.scale(WINDOW_DAYS * 24)
    exclude = [str(x).lower() for x in cfg.get("exclude") or []]
    words = [str(x).lower().strip() for x in cfg.get("exclude_words") or [] if str(x).strip()]
    # top-level session files only (a harness keeps sub-agents' transcripts deeper), recent ones
    files = sorted(p for p in base.glob("*/*.jsonl")) if base.is_dir() else []
    for path in files:
        if dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc) < since:
            continue
        session = read_session(path)
        if session is None:
            continue
        if session["routine"] or any(x in session["cwd"].lower() for x in exclude):
            continue  # a scheduled routine is not a person's session; excluded folders are private
        if _typed_any(session["asked"], words):
            continue  # what the person typed marks the session private (school work, own life)
        is_captured = captured(session, notes)
        rows.append({"id": session["id"], "start": pages.iso(session["start"]), "end": pages.iso(session["end"]),
                     "cwd": session["cwd"], "captured": is_captured})
        report["seen"] += 1
        report["captured"] += int(is_captured)
        finished = now - session["end"] > space.scale(FINISHED_HOURS)
        if cfg["switch"] == "on" and not is_captured and finished:
            target = root / "knowledge" / "sources" / f"{session['start']:%Y}" / f"{session['start']:%m}" / \
                f"session-{session['id']}.md"
            if not target.exists():
                _witness(root, session, now, space)
                report["witnessed"] += 1
    (root / SESSIONS).parent.mkdir(parents=True, exist_ok=True)
    (root / SESSIONS).write_text(yaml.safe_dump({"scanned_at": pages.iso(now), "sessions": rows},
                                                sort_keys=False), encoding="utf-8", newline="\n")
    with (root / "_meta" / "state" / "runs.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"routine": "transcripts", "at": pages.iso(now), "space": space.name,
                             "cost_tokens": 0, "notes": report}) + "\n")
    return report


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(".")
    if argv and argv[0] != "scan":
        root = Path(argv.pop(0))
    if argv[:1] != ["scan"]:
        print(__doc__)
        return 2
    print(json.dumps(scan(root)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
