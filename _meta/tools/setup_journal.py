"""The setup journal: where the first setup stands, so a closed session or a re-run the next day
continues where it stopped.

One YAML file outside every repository, `~/.spaces/setup.yaml` (`SPACES_SETUP_JOURNAL` moves it;
the tests use their own). `start.py` has five phases, `PHASES`, and each keeps a record under
its name: its `state` (`STATES`) and the facts it learned, which the later phases read. `needs`
lists what only the person can do, each with the check that proves it. A phase that finds its
need met clears it; a re-run of a phase replaces its own need, so the same code is never listed
twice.

    signins: {state: done, github: ada-okafor}
    apps: {state: running}
    needs:
    - {kind: github-code, text: ..., check: ..., link: ..., code: 4F2A-9C1B}
    settings:
    - {key: domains, ask: ..., default: ...}

`settings` is what the build took by default (the interview's questions nobody was asked), kept
for the progress page's Settings section; it belongs to no phase.

It holds no secret: a one-time sign-in code is shown to the person on screen anyway, and a token
or a password never enters it. Writes are atomic (a temporary file beside the journal, then a
replace), so a session closed mid-write leaves the old journal whole. Two writers at the same
moment can still lose one update; each write is a read, a change and a replace within a few
milliseconds.

Every write starts from a read, so a read that went wrong must never look like an empty journal.
Only a missing file is empty. On Windows a read that meets another process's replace fails for a
moment: it is tried again a few times, then the error is raised. A file that is there but is not
a YAML mapping is copied to `setup.yaml.bad-<time>` beside it before the next write replaces it,
so the reset leaves the old bytes to recover.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import tempfile
import time
from pathlib import Path

import yaml

PHASES = ("signins", "apps", "research", "build", "history")
STATES = ("pending", "running", "waiting", "done", "failed")
NEED_KINDS = ("github-code", "claude-signin", "connect-app", "namespace", "it-admin", "reconnect")
TRIES = 5
_sleep = time.sleep   # the tests stand in for it


def journal_path() -> Path:
    """Through `Path.home()`, so a test's fake home keeps every test off the real one."""
    return Path(os.environ.get("SPACES_SETUP_JOURNAL") or Path.home() / ".spaces" / "setup.yaml")


def _retry(action, *args, **kwargs):
    """On Windows a file another process is replacing, or has open, fails with PermissionError for
    a moment: try again a few times, then let the error out."""
    for attempt in range(TRIES):
        try:
            return action(*args, **kwargs)
        except PermissionError:
            if attempt == TRIES - 1:
                raise
            _sleep(0.05 * (attempt + 1))


def load() -> dict:
    """The journal; `{}` (no phase started, nothing waiting) when there is none. A file that is
    not a YAML mapping is copied aside first (`setup.yaml.bad-<time>`), so the reset the next
    write makes loses nothing; an empty file has nothing to keep. A file that cannot be read
    raises: an error is not an empty journal."""
    path = journal_path()
    try:
        data = yaml.safe_load(_retry(path.read_text, encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (ValueError, yaml.YAMLError):   # not UTF-8, not YAML: kept aside like any other non-mapping
        data = []
    if isinstance(data, dict):
        return data
    if data is not None:
        shutil.copy2(path, path.with_name(f"{path.name}.bad-{dt.datetime.now():%Y%m%d-%H%M%S}"))
    return {}


def _replace(src, dst) -> None:
    _retry(os.replace, src, dst)


def save(data: dict) -> Path:
    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
        _replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def set_phase(phase: str, state: str, **facts) -> dict:
    """The phase's new state; the facts join what it already learned. Returns its record."""
    if phase not in PHASES:
        raise ValueError(f"no phase {phase!r}: {', '.join(PHASES)}")
    if state not in STATES:
        raise ValueError(f"no state {state!r}: {', '.join(STATES)}")
    data = load()
    old = data.get(phase)
    record = {**(old if isinstance(old, dict) else {}), **facts}
    record.pop("state", None)
    data[phase] = {"state": state, **record}
    save(data)
    return data[phase]


def open_needs(data: dict) -> list[dict]:
    return [n for n in data.get("needs") or [] if isinstance(n, dict)]


def _kind(kind: str) -> str:
    if kind not in NEED_KINDS:
        raise ValueError(f"no kind of need {kind!r}: {', '.join(NEED_KINDS)}")
    return kind


def need(kind: str, text: str, check: str, link: str | None = None, code: str | None = None) -> dict:
    """List what only the person can do, with the check that proves it. A need of the same kind
    and text replaces the earlier one (the sign-in run again, with its new code); the same kind
    with other text is another need (two apps to reconnect)."""
    entry = {"kind": _kind(kind), "text": text, "check": check, "link": link, "code": code}
    data = load()
    needs = open_needs(data)
    same = [i for i, n in enumerate(needs) if n.get("kind") == kind and n.get("text") == text]
    if same:
        needs[same[0]] = entry
        needs = [n for i, n in enumerate(needs) if i not in same[1:]]
    else:
        needs.append(entry)
    data["needs"] = needs
    save(data)
    return entry


def clear_need(kind: str) -> None:
    """Every need of the kind is met."""
    _kind(kind)
    data = load()
    needs = open_needs(data)
    kept = [n for n in needs if n.get("kind") != kind]
    if len(kept) != len(needs):
        data["needs"] = kept
        save(data)


def next_phase(data: dict) -> str | None:
    """The first phase that is not done; None when the setup is complete."""
    for phase in PHASES:
        record = data.get(phase)
        if not (isinstance(record, dict) and record.get("state") == "done"):
            return phase
    return None
