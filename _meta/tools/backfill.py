"""backfill: the history, read oldest first, in paced parts that survive a closed lid and never start
into paid usage.

The first read of a person's history (`start.py history`) is not one long run. `plan` cuts the window
(90 days by default) into consecutive parts, oldest first: part 1 is the oldest stretch, sized so that, by the
guess, it fits the cap when it starts; the rest splits in two until part 1 has measured what a part really costs
(`calibrate`), when what is left is cut again so each part fits a night. A part is read by the routines: the
sweep chain at 07:00, 13:00 and 19:00 and the backfill night at 01:00 ask `due` which part to read (a run reads
the part its own lock names), export its records per feed oldest first in batches, and `checkpoint` after each
batch. A closed lid, a usage limit or a sign-in that ran out pauses the part (`after_run`, called by `routine.py`
with what the run came to); the next run that finds it `due` goes on from its checkpoints. A part is done only
when a run said it is through it (`read`: every source's range read to its end, or holding nothing); a checkpoint
says how far a run got, not that it is through (R45, I-4). Nothing here ever starts a part into paid usage.

Pacing (Decision D keeps full judgment for every day inside the window; only the window shrinks):
- 08:00 to 19:00 local a part leaves at least half of the 5-hour window free and needs a usage reading from
  the last hour (the person's own chats are not in the run lines); the other hours allow up to 90%.
- A part whose estimate, with what is in use, passes the cap is not started. One day that cannot be cut is an
  exception, and only at night: the run stops itself after fifty minutes (`unfinished`) and goes on next time. A
  part a run has begun is the other: the fifty-minute stop bounds every run, so at night it goes on however big it
  is, when the latest reading says extra usage is off (R45, I-5).
- On the setup day the backfill takes at most 30% of the week that was left at setup.
- With extra usage on, a used-up week stops everything like a used-up 5-hour window, until the week resets or a
  newer reading shows room; after an `overage` that named no time the part waits for a newer reading.
- A reading that may be another account's (`confirmed: false`: the desktop app's, until setup found its windows are
  the routines'; the `reading` command confirms one only then) counts as extra usage on. The windows a routine run's
  own `rate_limit_event` gave are a reading of the right account, used when newer than the one kept
  (`routine_reading`). They and each run's tokens are read from the rows routine.py itself writes
  (`ROUTINE_LIMITS`, which no routine may edit), never from `runs.jsonl`, where an agent appends its own lines.
- Before part 1 is measured only part 1 is due. A guess (`DEFAULT_PER_TOKEN_SHARE`) takes over, and cuts what is
  left by it, when nobody measured it within `CALIBRATE_WAIT_HOURS`.
- A usage limit that names no reset time pauses the part with no time at all: nothing here makes one up.
- A paid account's plan is held from the write that makes it until the person says go (`start`); a held plan
  does not stop the regular sweep, which exports what is new from the day the plan was made (`export_mark`).
- A routine may not say what only a session or the person says: `plan`, `reading`, `calibrate` and `start`
  refuse while a routine run holds the workspace's lock. A routine run says nothing about another space's history:
  every command refuses while the space it is said from (the working directory's) holds a live routine lock and
  names another one (Ruling R43). Nor does any command take a `--now` while that space holds a live routine lock,
  or a `--now` later than this computer's clock (R47).
- The history's notes are written on the computer that runs the routines only: on any other (`routine.host` names
  another), `plan`, `reading`, `calibrate` and `start` refuse (`ELSEWHERE_WHY`), since two computers writing them
  would stop the second one's sync (R45, I-1).
- A reading dated more than FUTURE_SLACK after now, or after this computer's clock, is refused, and one kept anyway
  is not read, like a meter row dated ahead; a first reading kept so still bounds the setup day (R47, N3-1).

The state is `_meta/state/backfill.yaml` in the workspace (`PATH`): `started`, `window_days`, `current_by`,
`per_token_share`, `tokens_per_record`, `counts`, `setup_reading` and `reading` (see `record_reading`), `waiting`
(held until the person says go) and `person_started`, `shortened_because` (with `window_estimate` until part 1 has
measured it), and `parts`, each `{id, from, to, records, status (pending, paused, done), checkpoints {feed: iso},
tokens, resets_at, why, runs, finished_at}`. A file that cannot be read holds the history with a plain line
(`explain`); it never reads as no backfill.

    python backfill.py <workspace> plan --counts 2026-09=1500,2026-10=1400 [--window-days N] [--reading PHRASE]
    python backfill.py <workspace> due [--json]          # the part to read now, or why none is
    python backfill.py <workspace> checkpoint <part> <feed> <iso time>
    python backfill.py <workspace> read <part>           # the part's range is read in every source, or holds nothing
    python backfill.py <workspace> mark <feed> [--json]  # where the regular sweep starts during the backfill
    python backfill.py <workspace> reading PHRASE        # five_hour=12,week=40,five_hour_resets=<iso>,week_resets=<iso>,extra=0|1
    python backfill.py <workspace> calibrate --before PCT --after PCT [--tokens N] [--week-before PCT --week-after PCT]
    python backfill.py <workspace> unfinished <part>     # a run that stopped itself before the part's end says so
    python backfill.py <workspace> start                 # a paid account: the person said go
    python backfill.py <workspace> status [--json]
    (every command takes --now <iso time> to use that moment as the time)
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

import yaml

PATH = Path("_meta/state/backfill.yaml")
RUNS = Path("_meta/state/runs.jsonl")
ROUTINE_LIMITS = Path("_meta/state/routine-limits.jsonl")   # routine.LIMITS_FILE: written by routine.py alone
FUTURE_SLACK = dt.timedelta(minutes=5)   # a meter row dated later than this after now is no run that happened
TOKENS_PER_RECORD = 6000          # the estimate before part 1 measures the real figure
DEFAULT_PER_TOKEN_SHARE = 0.00002  # points of the 5-hour window per token: a guess, 500,000 tokens are 10 points
WEEK_WINDOWS = 10                 # a week holds about this many full 5-hour windows (a guess until measured)
WINDOW_HOURS = 5
DAY_FROM, DAY_TO = 8, 19          # local working hours: 08:00 up to, not including, 19:00
DAY_CAP, NIGHT_CAP = 50, 90       # the most of the 5-hour window in use after a part starts
READING_MINUTES = 60              # a daytime part needs a reading at most this old
FIRST_DAY_WEEK = 30               # percent of the week left at setup that the setup day may take
PART_SHARE = 60                   # after part 1 is measured, no part is cut larger than this many points
MIN_WINDOW_DAYS = 28
MIN_MEASURED = 3                  # points: a smaller change between two readings measures nothing
CALIBRATE_WAIT_HOURS = 12
SLOTS = (1, 7, 13, 19)            # local hours the routines run (backfill-night, sweep-chain)
MORNING = 8
HORIZON_DAYS = 14
TRIES = 5
_sleep = time.sleep               # the tests stand in for it

PENDING, PAUSED, DONE = "pending", "paused", "done"
PAUSING = ("limit", "overage", "timeout", "auth")
WHY = {   # plain words: what the person may read of a paused part
    "limit": "Your Claude limit was reached, so this part stopped; the next run goes on from where it got to.",
    "limit_no_reset": ("Your Claude limit was reached and Claude didn't say when it resets, so the next run "
                       "tries again from where this one got to."),
    "overage": "The run would have used extra usage, so it stopped; the read goes on when your limit has room.",
    "timeout": "The run took an hour and was stopped; the next run goes on from where it got to.",
    "auth": "Claude's sign-in ran out; sign in again and the read goes on from where it got to.",
    "unfinished": ("The run stopped before it was through this part, at its fifty minutes or earlier; the next run "
                   "goes on from where it got to."),
    "idle": "The run ended and did not read this part, so the next run tries again.",
}
BROKEN_WHY = ("The notes that keep track of how far your history has been read could not be read, so the history "
              "waits until someone has looked at them.")
NO_PART_WHY = "This run was started with no part of the history to read."
ANOTHER_RUN_WHY = ("A routine run is going in this workspace; it reads the part its own lock names, and nothing else "
                   "starts while it runs.")
REPLAN_WHY = ("A new plan was made because the notes on your history could not be read, so it is read only when you "
              "say go.")
RUN_GOING_WHY = ("A routine run is going in this workspace right now, and this is for you to say in a session, not "
                 "for a routine; say it again when the run is over.")
# The refusals below are read by whoever said the command: a routine run's agent, or the person in a session (R47, N3-4).
OTHER_SPACE_WHY = ("A routine run is going in the workspace this was said from, so nothing here reads or changes another "
                   "workspace's history until it ends; say it again when the run is over.")
NOW_WHY = ("A routine run is going in the workspace this was said from, so no time other than this computer's own is "
           "taken until it ends; say it again without one.")
NOW_AHEAD_WHY = ("That time is later than this computer's clock, so it is not taken; say it again with the time it is "
                 "now, or without one.")
ELSEWHERE_WHY = ("Your routines run on {name}, so your history is planned, measured and read there, and this computer "
                 "keeps none of it; say it on {name} instead.")
AHEAD_WHY = "A usage reading dated later than now is not kept: a reading is taken now, or earlier."
OVERAGE_WHY = ("The last run would have used extra usage and Claude didn't say when there is room again, so the read "
               "waits for your next reading of your usage.")
CALIBRATED_WHY = ("Part 1 was measured already, so it is not measured again: a second measurement would replace the pace "
                  "the rest of your history was cut by.")


# ----- times -------------------------------------------------------------------------------------
def _aware(moment: dt.datetime) -> dt.datetime:
    """A moment with its zone; one without is this machine's local time."""
    return moment if moment.tzinfo else moment.astimezone()


def _dt(value) -> dt.datetime | None:
    """A moment from a datetime or ISO text (`Z` and fractions allowed); None for anything else."""
    if isinstance(value, dt.datetime):
        return _aware(value)
    if isinstance(value, dt.date):
        return _aware(dt.datetime(value.year, value.month, value.day))
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    try:
        return _aware(dt.datetime.fromisoformat(text))
    except ValueError:
        return None


def _iso(moment: dt.datetime) -> str:
    return _aware(moment).isoformat(timespec="seconds")


def _real_now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _daytime(now: dt.datetime) -> bool:
    return DAY_FROM <= now.hour < DAY_TO


def _cap(now: dt.datetime) -> int:
    return DAY_CAP if _daytime(now) else NIGHT_CAP


def _morning(now: dt.datetime) -> dt.datetime:
    """The next 08:00 on the moment's own clock: the morning by which the first read should be through."""
    at = now.replace(hour=MORNING, minute=0, second=0, microsecond=0)
    return at if at > now else at + dt.timedelta(days=1)


def _clock(moment: dt.datetime, now: dt.datetime) -> str:
    """A moment as the person reads it on their own clock: `23:20`, `tomorrow 01:20`, `Oct 27, 13:00`."""
    moment = moment.astimezone(now.tzinfo)
    gap = (moment.date() - now.date()).days
    if gap == 0:
        return f"{moment:%H:%M}"
    if gap == 1:
        return f"tomorrow {moment:%H:%M}"
    return f"{moment:%b} {moment.day}, {moment:%H:%M}"


def _slots(now: dt.datetime):
    """The moments a run can read a part: now, then every routine time after it."""
    yield now
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for offset in range(HORIZON_DAYS + 1):
        for hour in SLOTS:
            at = day + dt.timedelta(days=offset, hours=hour)
            if at > now:
                yield at


# ----- the state file ----------------------------------------------------------------------------
def _path(workspace) -> Path:
    return Path(workspace) / PATH


def load(workspace) -> dict:
    """The state; `{}` when there is none (no backfill). A file that is not a YAML mapping reads as
    `{"broken": True}`: the history is held with a plain line (`explain`), never taken for no backfill, and no
    write starts from it (`_writable`); `save` keeps it aside before `plan` replaces it. A file that cannot be
    read at this moment raises (Windows, while another process replaces it): an error is not an empty state."""
    try:
        data = yaml.safe_load(_retry(_path(workspace).read_text, encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (ValueError, yaml.YAMLError):
        return {"broken": True}
    if data is None:
        return {}
    if isinstance(data, dict) and "parts" in data and not isinstance(data["parts"], list):
        return {"broken": True}   # a plan whose parts cannot be read is damaged, not absent
    return data if isinstance(data, dict) else {"broken": True}


def _writable(workspace) -> dict:
    """The state to change: ValueError, in plain words, for a file that cannot be read."""
    state = load(workspace)
    if state.get("broken"):
        raise ValueError(BROKEN_WHY)
    return state


def _plain(value):
    """The state as plain YAML: moments as ISO text, tuples as lists."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dt.datetime):
        return _iso(value)
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def _retry(action, *args, **kwargs):
    """On Windows a file another process is replacing fails for a moment: try again a few times."""
    for attempt in range(TRIES):
        try:
            return action(*args, **kwargs)
        except PermissionError:
            if attempt == TRIES - 1:
                raise
            _sleep(0.05 * (attempt + 1))


def _keep_aside(path: Path) -> None:
    """A file that is there but is not a YAML mapping is copied to `<name>.bad-<time>` before it is replaced."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError, yaml.YAMLError):
        data = []
    if data is not None and not isinstance(data, dict):
        try:
            path.replace(path.with_name(f"{path.name}.bad-{dt.datetime.now():%Y%m%d-%H%M%S}"))
        except OSError:
            pass


def save(workspace, state: dict) -> Path:
    """The state written whole or not at all, with LF line ends."""
    path = _path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    _keep_aside(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            yaml.safe_dump(_plain(state), fh, sort_keys=False, allow_unicode=True, width=100)
        _retry(os.replace, tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def _parts(state: dict) -> list:
    rows = state.get("parts")
    return [p for p in rows if isinstance(p, dict) and p.get("id")] if isinstance(rows, list) else []


def _own_run(workspace) -> tuple:
    """(whether a routine run is going in this workspace, the part it reads): from the live lock `routine.py` keeps."""
    try:
        import routine
        held = routine.locked(Path(workspace))
    except Exception:
        return False, None
    return (True, held.get("part") or None) if held else (False, None)


def _caller_space() -> Path | None:
    """The space a command is said from: the nearest folder at or above the working directory that holds a
    space.yaml. A routine's agent runs its commands in its own space and cannot leave it (R43). None outside any."""
    try:
        here = Path.cwd().resolve()
    except OSError:
        return None
    return next((folder for folder in (here, *here.parents) if (folder / "space.yaml").is_file()), None)


def runs_elsewhere(workspace) -> str | None:
    """The name of the other computer that runs the workspace's routines (`routine.host`), when it is not this one;
    None when this computer runs them, or nothing (or nothing clear) is recorded."""
    try:
        import routine
        record = routine.host(Path(workspace))
        if not isinstance(record, dict) or record.get("unclear") or not record.get("machine"):
            return None
        return None if record["machine"] == routine.this_machine() else str(record.get("name") or "another computer")
    except Exception:   # noqa: BLE001 - no record to read: nothing says it is another computer's
        return None


def _same_folder(a, b) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return Path(a).resolve() == Path(b).resolve()


def _find(state: dict, which: str) -> dict:
    found = next((p for p in _parts(state) if p["id"] == which), None)
    if found is None:
        raise ValueError(f"there is no part {which} in this history")
    return found


def _next(state: dict) -> dict | None:
    """The running or next part: the first that is not done."""
    return next((p for p in _parts(state) if p.get("status") != DONE), None)


def active(workspace) -> bool:
    """Some part is not done (a file that cannot be read counts: the history waits, it is not over)."""
    state = load(workspace)
    return bool(state.get("broken")) or _next(state) is not None


def _state_now(state: dict) -> dt.datetime:
    """The latest moment the state itself knows of: when a caller gives no clock (the tests never use the real one)."""
    seen = [_dt(state.get("started"))]
    for reading in (state.get("setup_reading"), state.get("reading")):
        seen.append(_dt((reading or {}).get("at")))
    for p in _parts(state):
        seen.append(_dt(p.get("finished_at")))
        seen += [_dt(r.get("at")) for r in p.get("runs") or [] if isinstance(r, dict)]
    seen = [m for m in seen if m]
    return max(seen) if seen else _real_now()


# ----- readings ----------------------------------------------------------------------------------
def _number(value, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, not {value!r}") from None
    if not 0 <= number <= 100:
        raise ValueError(f"{name} is a share of your limit between 0 and 100, not {value!r}")
    return int(number) if number == int(number) else number


def _when(value, name: str) -> str | None:
    if value in (None, ""):
        return None
    moment = _dt(value)
    if moment is None:
        raise ValueError(f"{name} must be a date and time, not {value!r}")
    return value.strip() if isinstance(value, str) else _iso(moment)


def reading_of(value: dict, now: dt.datetime | None = None) -> dict:
    """A usage reading as the state keeps it: `{at, five_hour_pct, week_pct, five_hour_resets, week_resets,
    extra_usage}`, moments as ISO text, and `confirmed: false` for one that may be another account's (the desktop
    app's, until setup found its windows are the routines'): the pacing takes extra usage as on for it.
    ValueError for one that is not a reading, or, given `now`, one dated more than FUTURE_SLACK after it."""
    if not isinstance(value, dict):
        raise ValueError("a usage reading is five_hour, week, five_hour_resets, week_resets and extra")
    at = _dt(value.get("at")) or now
    if at is None:
        raise ValueError("a usage reading needs the time it was taken")
    if now is not None and at > _aware(now) + FUTURE_SLACK:   # dated ahead it would stay the newest for good (R43)
        raise ValueError(AHEAD_WHY)
    given = {"at": _iso(at), "five_hour_pct": _number(value.get("five_hour_pct"), "five_hour"),
             "week_pct": _number(value.get("week_pct", 0), "week"),
             "five_hour_resets": _when(value.get("five_hour_resets"), "five_hour_resets"),
             "week_resets": _when(value.get("week_resets"), "week_resets"),
             "extra_usage": bool(value.get("extra_usage"))}
    if value.get("confirmed", True) in (False, 0, "0", "false", "False", "no"):
        given["confirmed"] = False
    return given


def _unconfirmed(reading) -> bool:
    return isinstance(reading, dict) and reading.get("confirmed") is False


def _share_of(value) -> float | None:
    """A window's utilization as a share in percent: the command line gives a fraction (0.2 is 20%); a figure above 1
    is taken as percent already. None for anything else."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 100:
        return None
    return round(value * 100 if value <= 1 else value, 2)


def meter_rows(workspace, now: dt.datetime | None = None) -> list:
    """The rows routine.py itself wrote, one a run (`ROUTINE_LIMITS`: its moment, outcome, tokens and windows), oldest
    first, `at` as a moment. A row dated more than FUTURE_SLACK after now is left out: no run happened then (R40,
    N-1). Never `runs.jsonl`: a routine's agent appends its own lines there."""
    try:
        lines = (Path(workspace) / ROUTINE_LIMITS).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    latest, rows = (now or dt.datetime.now(dt.timezone.utc)) + FUTURE_SLACK, []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        at = _dt(row.get("at")) if isinstance(row, dict) else None
        if at is not None and at <= latest:
            rows.append({**row, "at": at})
    return rows


def routine_reading(workspace, now: dt.datetime | None = None) -> dict | None:
    """The newest reading of the routines' own account: the windows of the newest meter row with `limits`
    (`meter_rows`, from `routine.limits_of`). Extra usage counts as off only when that run said it was refused and not
    in use. Its moment is a second before the row, so what that run used counts on top: its event may have come early
    in the run. None without one."""
    for row in reversed(meter_rows(workspace, now)):
        if not isinstance(row.get("limits"), dict):
            continue
        limits, at = row["limits"], row["at"]
        five, week = (limits.get(k) if isinstance(limits.get(k), dict) else {} for k in ("five_hour", "seven_day"))
        if _share_of(five.get("utilization")) is None:
            continue
        refused = limits.get("overage_status") == "rejected" and limits.get("using_overage") is False
        try:
            return reading_of({"at": at - dt.timedelta(seconds=1), "five_hour_pct": _share_of(five["utilization"]),
                               "week_pct": _share_of(week.get("utilization")) or 0,
                               "five_hour_resets": five.get("resets_at"), "week_resets": week.get("resets_at"),
                               "extra_usage": not refused})
        except ValueError:
            continue
    return None


def _on_time(reading, now: dt.datetime) -> bool:
    """A kept reading not dated more than FUTURE_SLACK after now (one without a time is left as it is)."""
    at = _dt(reading.get("at")) if isinstance(reading, dict) else None
    return at is None or at <= _aware(now) + FUTURE_SLACK


def _timely(state: dict, now: dt.datetime | None, cap: bool = False) -> dict:
    """The state without a kept reading dated more than FUTURE_SLACK after now: like a meter row dated ahead, it is
    not read (R43, N2-1 a). The state as it is without a clock. With `cap`, a first reading dropped so still bounds
    the setup day: the share of the week it says was used stands, its time does not (R47, N3-1), so the setup day's
    cap is never switched off and is never more than FIRST_DAY_WEEK of a whole week."""
    if now is None:
        return state
    out = {k: v for k, v in state.items() if k not in ("reading", "setup_reading") or _on_time(v, now)}
    setup = state.get("setup_reading")
    if cap and isinstance(setup, dict) and "setup_reading" not in out:
        out["setup_reading"] = {"week_pct": setup.get("week_pct")}
    return out


def _newest_reading(workspace, state: dict, now: dt.datetime | None = None) -> dict:
    """The state with the newest reading of the two: the one kept, or the routines' own (`routine_reading`). A kept
    reading dated ahead of now is not one (`_timely`), but a first one still bounds the setup day."""
    state = _timely(state, now, cap=True)
    theirs, kept = routine_reading(workspace, now), state.get("reading")
    if theirs and (not kept or (_dt(kept.get("at")) or _dt("1970-01-01T00:00:00Z")) < _dt(theirs["at"])):
        return {**state, "reading": theirs}
    return state


READING_KEYS = {"five_hour": "five_hour_pct", "week": "week_pct", "five_hour_resets": "five_hour_resets",
                "week_resets": "week_resets", "extra": "extra_usage", "at": "at", "confirmed": "confirmed"}


def parse_reading(text, now: dt.datetime) -> dict | None:
    """A reading from the phrase the commands take: `five_hour=12,week=40,five_hour_resets=<iso>,
    week_resets=<iso>,extra=0|1[,confirmed=0|1]` (the usage tool's own figures; `confirmed=0` for one that may be
    another account's). None for nothing, or `unavailable` (it could not be read now). ValueError, in plain words, for
    anything else; `not_applicable` is the caller's to ask for."""
    text = (text or "").strip()
    if text in ("", "unavailable"):
        return None
    fields = {}
    for item in text.split(","):
        key, sep, value = item.partition("=")
        if not sep or key.strip() not in READING_KEYS:
            raise ValueError(f"{item.strip()!r} is not part of a usage reading: "
                             "five_hour=<pct>,week=<pct>,five_hour_resets=<time>,week_resets=<time>,extra=<0|1>")
        fields[READING_KEYS[key.strip()]] = value.strip()
    if "five_hour_pct" not in fields:
        raise ValueError("a usage reading needs five_hour=<the share of the 5-hour limit in use>")
    fields["extra_usage"] = fields.get("extra_usage", "0") in ("1", "true", "True", "yes")
    return reading_of(fields, now)


UNKNOWN_ACCOUNT_WHY = ("Kept as unconfirmed: setup has not yet found that this app's usage is the Claude account your "
                       "routines use, so the reading counts as if extra usage were on.")
OTHER_ACCOUNT_WHY = ("Kept as unconfirmed: this app's usage is another Claude account than the one your routines use, "
                     "so the reading counts as if extra usage were on.")


def unconfirmed_why() -> str | None:
    """Why the `reading` command keeps a reading unconfirmed: only setup's finding that the app's usage is the routines'
    account (`usage_account: same` in the setup journal) confirms one (R40, N-2). None then; a journal that cannot be
    read confirms nothing."""
    try:
        import setup_journal
        account = setup_journal.load().get("usage_account")
    except Exception:   # noqa: BLE001 - no journal to read: not confirmed
        account = None
    if account == "same":
        return None
    return OTHER_ACCOUNT_WHY if account == "different" else UNKNOWN_ACCOUNT_WHY


def record_reading(workspace, reading: dict) -> None:
    """Keep the latest reading, and the first one as `setup_reading` (the week left then bounds the setup day)."""
    state = _writable(workspace)
    given = reading_of(reading)
    state.setdefault("setup_reading", given)
    state["reading"] = given
    _refresh(workspace, state, _dt(given["at"]))
    save(workspace, state)


# ----- the plan ----------------------------------------------------------------------------------
def _densities(counts: dict, now: dt.datetime) -> dict:
    """Records a day, per month: a month's count spread over its days, the current month's over the days so far."""
    found = {}
    for key, number in (counts or {}).items():
        try:
            year, month = (int(x) for x in str(key).split("-"))
            days = now.day if (year, month) == (now.year, now.month) else calendar.monthrange(year, month)[1]
            found[(year, month)] = max(0, int(number)) / days
        except (ValueError, TypeError, calendar.IllegalMonthError):
            continue
    return found


def _stretch(per_day: list, first_part_max: int) -> list:
    """How many days each part takes: part 1 the oldest days up to `first_part_max` records (one day at least),
    the rest in two parts of about equal records."""
    total, days = sum(per_day), len(per_day)
    first, held = 0, 0.0
    for number in per_day:
        if first and held + number > first_part_max:
            break
        held += number
        first += 1
    if first >= days:
        return [days]
    rest = per_day[first:]
    if len(rest) < 2 or sum(rest) <= 0:
        return [first, len(rest)]
    half, running, best = sum(rest) / 2, 0.0, (None, 1)
    for cut in range(1, len(rest)):
        running += rest[cut - 1]
        if best[0] is None or abs(running - half) < best[0]:
            best = (abs(running - half), cut)
    return [first, best[1], len(rest) - best[1]] if total else [days]


def _split(counts: dict, now: dt.datetime, window: int, first_part_max: int) -> list:
    end = now.date()
    start = end - dt.timedelta(days=window)
    dens = _densities(counts, now)
    per_day = []
    for number in range(window):
        day = start + dt.timedelta(days=number)
        per_day.append(dens.get((day.year, day.month), 0.0))
    if sum(per_day) <= 0:
        return [_part(1, start, end, 0, DONE)]
    parts, at = [], 0
    for index, days in enumerate(_stretch(per_day, first_part_max), 1):
        parts.append(_part(index, start + dt.timedelta(days=at), start + dt.timedelta(days=at + days),
                           round(sum(per_day[at:at + days]))))
        at += days
    return parts


def _part(number: int, first: dt.date, last: dt.date, records: int, status: str = PENDING) -> dict:
    return {"id": f"p{number}", "from": first.isoformat(), "to": last.isoformat(), "records": int(records),
            "status": status, "checkpoints": {}, "tokens": 0}


def _tokens_left(state: dict, part: dict) -> float:
    """The tokens this part may still take: its records at the cost of a record, less what it already used."""
    cost = state.get("tokens_per_record") or TOKENS_PER_RECORD
    return max(0.0, part.get("records", 0) * cost - part.get("tokens", 0))


def _points(state: dict, part: dict, share: float | None = None) -> float:
    share = share if share is not None else state.get("per_token_share")
    return _tokens_left(state, part) * (share if share is not None else DEFAULT_PER_TOKEN_SHARE)


def _window_use(state: dict, now: dt.datetime) -> tuple:
    """(share of the 5-hour window in use when the reading was taken and still is, the moment it resets)."""
    reading = state.get("reading")
    if not reading:
        return 0.0, None
    taken = _dt(reading.get("at"))
    resets = _dt(reading.get("five_hour_resets"))
    if taken is not None and resets is None:
        resets = taken + dt.timedelta(hours=WINDOW_HOURS)   # a window lasts five hours at most: the latest it can end
    if resets is not None and resets > now:
        return float(reading.get("five_hour_pct") or 0), resets
    return 0.0, None


def _week_rate(state: dict) -> float:
    """Points of the week a token takes: measured with part 1 when the readings gave it, else a tenth of what it
    takes of a 5-hour window."""
    share = state.get("per_token_share")
    return state.get("week_per_token_share") or (share if share is not None else DEFAULT_PER_TOKEN_SHARE) / WEEK_WINDOWS


def _day_one_tokens(state: dict) -> float | None:
    """The tokens the setup day may still read: 30% of the week left at setup, less what the parts used. None
    without the reading the week left was taken from."""
    setup = state.get("setup_reading")
    if not setup:
        return None
    left = max(0.0, 100 - float(setup.get("week_pct") or 0))
    used = sum(p.get("tokens", 0) for p in _parts(state))
    return max(0.0, FIRST_DAY_WEEK * left / 100 / _week_rate(state) - used)


def _finish(points: float, now: dt.datetime, use: float = 0.0, window_end: dt.datetime | None = None,
            day_one: tuple | None = None) -> dt.datetime:
    """When `points` of the 5-hour window have been read: one run at each routine time (SLOTS), each taking what the
    pacing leaves free in its window (`day_one` is the moment the setup day ends and the points it may still take).
    An ESTIMATE: it assumes a run stops when it reaches the cap, which nothing enforces, and that the routines run
    at SLOTS. A history that does not finish within HORIZON_DAYS ends there."""
    if points <= 0:
        return now
    end = window_end if window_end and window_end > now else now + dt.timedelta(hours=WINDOW_HOURS)
    left, last = points, now
    for at in _slots(now):
        if at >= end:
            while at >= end:
                end += dt.timedelta(hours=WINDOW_HOURS)
            use = 0.0
        room = max(0.0, _cap(at) - use)
        first_day = day_one is not None and at < day_one[0]
        if first_day:
            room = min(room, day_one[1])
        take = min(left, room)
        if take <= 0:
            continue
        left -= take
        use += take
        last = at
        if first_day:
            day_one = (day_one[0], day_one[1] - take)
        if left <= 1e-9:
            return last + dt.timedelta(hours=1)
    return now + dt.timedelta(days=HORIZON_DAYS)


def _refresh(workspace, state: dict, now: dt.datetime | None) -> None:
    """`current_by`: when the parts left will be read, at the pace the plan knows of."""
    if not _parts(state):
        return
    now = now or _state_now(state)
    left = sum(_points(state, p) for p in _parts(state) if p.get("status") != DONE)
    use, end = _window_use(_timely(state, now), now)
    room = _day_one_tokens(_timely(state, now, cap=True))
    day_one = None
    if room is not None and state.get("started"):
        share = state.get("per_token_share")
        day_one = (_morning(_dt(state["started"])), room * (share if share is not None else DEFAULT_PER_TOKEN_SHARE))
    state["current_by"] = _iso(_finish(left, now, use, end, day_one))


def _windows(asked: int) -> list:
    """The windows to try, longest first: the one asked for, then whole weeks down to MIN_WINDOW_DAYS."""
    shorter = list(range((asked - 1) // 7 * 7, MIN_WINDOW_DAYS - 1, -7))
    return [asked] + [w for w in shorter if w < asked]


def _fit(counts, now, window, reading, first_part_max, cost) -> tuple:
    """(does it all fit by tomorrow morning at 08:00 under the pacing, when the estimate ends)."""
    parts = _split(counts, now, window, first_part_max)
    tokens = sum(p["records"] for p in parts) * cost
    state = {"reading": reading, "setup_reading": reading}
    use, end = _window_use(state, now)
    cutoff, room = _morning(now), _day_one_tokens(state)
    done = _finish(tokens * DEFAULT_PER_TOKEN_SHARE, now, use, end, (cutoff, room * DEFAULT_PER_TOKEN_SHARE))
    return done <= cutoff and tokens <= room, done


def _shortened(asked: int, window: int, fits: bool, ends: dt.datetime, now: dt.datetime) -> str:
    """Why the window was cut at plan time: an estimate, until part 1 has measured what a part costs."""
    if fits:
        return (f"By an estimate that is checked when the first part has been read, the last {asked} days would not be "
                f"through by tomorrow morning at {DAY_FROM}:00 within your usage limits, so the history starts "
                f"{window} days back for now.")
    return (f"By an estimate that is checked when the first part has been read, the last {asked} days would take far "
            f"longer than a night within your usage limits, so the history starts {window} days back for now, the "
            f"shortest it goes; reading it is expected to take until {_clock(ends, now)}.")


def _days(part: dict) -> int:
    """The days a part spans; 2 for dates that cannot be read (a part is only ever not cut at one day)."""
    try:
        return (dt.date.fromisoformat(part["to"]) - dt.date.fromisoformat(part["from"])).days
    except (KeyError, TypeError, ValueError):
        return 2


def _untouched(part: dict) -> bool:
    """A part nothing has read, started or run for yet."""
    return part.get("status") == PENDING and not (
        part.get("tokens") or part.get("checkpoints") or part.get("runs") or part.get("unread"))


def touched(workspace) -> bool:
    """Some part has run, been checkpointed or used tokens: its plan is no longer remade when the counts change."""
    return any(p.get("runs") or p.get("tokens") or p.get("checkpoints") or p.get("unread")
               for p in _parts(load(workspace)))


def _first_fit(now: dt.datetime, reading: dict | None, first_part_max: int, cost: int) -> int:
    """The records part 1 may hold: `first_part_max`, or fewer when, by the guess, more would pass the cap beside what
    is in use at the moment it can start (now, with a reading; else the first night, since nothing starts by day
    without one). At least one day's worth is kept by the caller."""
    use, when = 0.0, now
    if reading:
        use, _ = _window_use({"reading": reading}, now)
    elif _daytime(now):
        when = now.replace(hour=DAY_TO, minute=0, second=0, microsecond=0)
    room = _cap(when) - use
    return max(1, min(int(first_part_max), int(room / (cost * DEFAULT_PER_TOKEN_SHARE))))


def _older(counts: dict, started: dt.datetime, longer: int, window: int) -> tuple:
    """(first day, last day, records) of the days between a longer window and the one the plan has."""
    anchor = started.date()
    first, last = anchor - dt.timedelta(days=longer), anchor - dt.timedelta(days=window)
    dens = _densities(counts, started)
    days = (first + dt.timedelta(days=n) for n in range((last - first).days))
    return first, last, round(sum(dens.get((day.year, day.month), 0.0) for day in days))


def plan(workspace: Path, counts: dict, now: dt.datetime, reading: dict | None = None, window_days: int = 90,
         first_part_max: int = 1500, tokens_per_record: int = TOKENS_PER_RECORD, hold: str | None = None) -> dict:
    """Cut the window into consecutive parts from `now - window_days` to `now`, oldest first, and write the
    state. Part 1 holds at most `first_part_max` records and, by the guess, no more than fits the cap beside
    what the reading says is in use (without one: the first night). With a reading (the usage tool's, taken now)
    it estimates whether every part fits by 08:00 local tomorrow under the pacing caps; if not, it shortens the
    window in whole weeks, never below 28 days, for now (`shortened_because`, `window_estimate`: `calibrate`
    settles it from the measurement, or the guess). The days inside the window keep full judgment (Decision D).
    Readings recorded before the plan are kept, and a plan made again without one estimates with them. `hold` holds
    the plan in the same write that makes it (a paid account) unless the person already said go (`person_started`),
    which a plan made again keeps; a plan made over a file that cannot be read is held the same way."""
    now = _aware(now)
    old = _timely(load(workspace), now)   # a reading kept from before and dated ahead is not read, nor kept
    asked = window = int(window_days)
    given = reading_of(reading, now) if reading else None
    kept = given or old.get("reading") or old.get("setup_reading")   # a plan made again estimates with the reading kept
    first_max = _first_fit(now, kept, first_part_max, tokens_per_record)
    note, fits, ends = None, True, now
    if kept:
        for choice in _windows(window):
            fits, ends = _fit(counts, now, choice, kept, first_max, tokens_per_record)
            if fits or choice == _windows(window)[-1]:
                window = choice
                break
        if window != asked:
            note = _shortened(asked, window, fits, ends, now)
    state = {"started": _iso(now), "window_days": window, "current_by": None, "per_token_share": None,
             "tokens_per_record": int(tokens_per_record),
             "counts": {str(k): int(v) for k, v in sorted((counts or {}).items()) if isinstance(v, int)},
             "parts": _split(counts, now, window, first_max)}
    if note:
        state.update(shortened_because=note, window_estimate=True, asked_window_days=asked)
    setup = old.get("setup_reading") or given
    if setup:
        state["setup_reading"] = setup
    latest = given or old.get("reading")
    if latest:
        state["reading"] = latest
    if old.get("person_started"):
        state["person_started"] = True
    elif hold or old.get("waiting") or old.get("broken"):   # a plan made over a file that cannot be read waits for a go
        state["waiting"] = hold or old.get("waiting") or REPLAN_WHY
    _refresh(workspace, state, now)
    save(workspace, state)
    return load(workspace)


def hold(workspace, why: str) -> None:
    """Keep every part from starting until `start`: a paid account is the person's to start (decision E)."""
    state = _writable(workspace)
    if not _parts(state):
        raise ValueError("there is no plan to hold")
    state["waiting"] = why
    save(workspace, state)


def start(workspace) -> None:
    """The person said go: parts may start, and a plan made again keeps their word (`person_started`)."""
    state = _writable(workspace)
    state.pop("waiting", None)
    state["person_started"] = True
    save(workspace, state)


def unfinished(workspace, which: str) -> None:
    """A run stopped itself (its fifty minutes, or a source it could not reach) before this part's end: when the run
    ends the part is paused, not done."""
    state = _writable(workspace)
    part = _find(state, which)
    part["unfinished"] = True
    part.pop("unread", None)
    save(workspace, state)


def begin(workspace, which: str) -> None:
    """A run begins to read part `which` (`routine.py`, as the run starts): until it checkpoints, says `read` or
    `unfinished`, the run has read nothing of it, and `after_run` will not call it done. A `read` said by an earlier
    run is that run's: this one is through only when it says so itself."""
    state = _writable(workspace)
    part = _find(state, which)
    part["unread"] = True
    part.pop("through", None)
    save(workspace, state)


def range_read(workspace, which: str) -> None:
    """The run read the part's whole range in every source to its end, or found nothing in it (a source with no record
    in the range leaves no checkpoint): the run is through the part (`through`), and `after_run` calls it done."""
    state = _writable(workspace)
    part = _find(state, which)
    part.pop("unread", None)
    part["through"] = True
    save(workspace, state)


# ----- measuring part 1 --------------------------------------------------------------------------
def _resplit(state: dict) -> None:
    """Cut every part that has not started and costs more than PART_SHARE points into parts that do not, at day
    boundaries with about equal records. A part that already fits is left as it is, and so is one day (it cannot
    be cut)."""
    out = []
    for part in _parts(state):
        k = math.ceil(_points(state, part) / PART_SHARE) if _untouched(part) else 1
        if not _untouched(part) or k <= 1:
            out.append(part)
            continue
        first, last = dt.date.fromisoformat(part["from"]), dt.date.fromisoformat(part["to"])
        days = (last - first).days
        k = min(k, days)
        at, records = 0, 0
        for index in range(1, k + 1):
            upto = round(days * index / k)
            got = part["records"] - records if index == k else round(part["records"] * (upto - at) / days)
            piece = _part(0, first + dt.timedelta(days=at), first + dt.timedelta(days=upto), got)
            out.append({**piece, **({"older": True} if part.get("older") else {})})
            records += got
            at = upto
    state["parts"] = [{**p, "id": f"p{n}"} for n, p in enumerate(out, 1)]


def _settle_window(state: dict, now: dt.datetime) -> None:
    """Part 1 is measured (or the guess has taken over): the window cut at plan time is settled for good. The oldest stretch is read, so
    the window can only grow: when the measured cost lets the days between the full window and the one planned be
    read as well by tomorrow morning, they are added as one part (`older`), read after the part that is under way,
    and the window is the longest that fits. What the measurement does not allow stays cut, and says so."""
    if not state.pop("window_estimate", None):
        return
    asked, window = state.pop("asked_window_days", None) or state["window_days"], state["window_days"]
    started, share = _dt(state["started"]), state["per_token_share"]
    if asked <= window or started is None:
        state.pop("shortened_because", None)
        return
    cost = state.get("tokens_per_record") or TOKENS_PER_RECORD
    left = sum(_tokens_left(state, p) for p in _parts(state) if p.get("status") != DONE)
    use, end = _window_use(state, now)
    cutoff, room = _morning(started), _day_one_tokens(state)
    day_one = None if room is None else (cutoff, room * share)
    chosen, estimate = None, None
    for longer in _windows(asked):
        if longer <= window:
            break
        first, last, records = _older(state.get("counts") or {}, started, longer, window)
        tokens = left + records * cost
        done = _finish(tokens * share, now, use, end, day_one)
        estimate = estimate or done
        if done <= cutoff and (room is None or tokens <= room):
            chosen = (longer, first, last, records)
            break
    lead = ("Measured on the first part" if state.get("calibration") == "measured"
            else "By a guess, because the first part's cost was never measured")
    if chosen is None:
        state["shortened_because"] = (
            f"{lead}, reading the last {asked} days would take until {_clock(estimate, now)} "
            f"within your usage limits, so the history goes {window} days back.")
        return
    longer, first, last, records = chosen
    parts = _parts(state)
    at = next((n for n, p in enumerate(parts) if _untouched(p)), len(parts))
    parts.insert(at, {**_part(0, first, last, records), "older": True})
    state["parts"] = parts
    state["window_days"] = longer
    if longer == asked:
        state.pop("shortened_because", None)
    else:
        state["shortened_because"] = (
            f"{lead}, reading the last {asked} days would not be through by tomorrow morning "
            f"within your usage limits, so the history goes {longer} days back.")


def calibrate(workspace, before_pct: float, after_pct: float, tokens: int, week_before_pct: float | None = None,
              week_after_pct: float | None = None, now: dt.datetime | None = None) -> float:
    """Part 1 is read: the share of the 5-hour window it took per token (readings before and after, the tokens
    the part used), stored as `per_token_share` and returned. A change under MIN_MEASURED points measures
    nothing, and the guess stays (`calibration: assumed`). The window cut at plan time on the guess is settled for
    good from the measurement (`_settle_window`: it can grow, never shrink, since the oldest stretch is read), and
    what is left is cut again so each part fits a night (`_resplit`). A slower pace moves `current_by`. Once only: a
    second measurement (or one after the guess took over) is refused, since it would replace the pace the parts left
    were cut by."""
    state = _writable(workspace)
    if not _parts(state):
        raise ValueError("there is no plan to measure")
    if state.get("calibration"):
        raise ValueError(CALIBRATED_WHY)
    now = now or _state_now(state)
    change = float(after_pct) - float(before_pct)
    measured = tokens > 0 and change >= MIN_MEASURED
    share = change / tokens if measured else DEFAULT_PER_TOKEN_SHARE
    state["per_token_share"] = share
    state["calibration"] = "measured" if measured else "assumed"
    first = _parts(state)[0]
    if tokens > 0 and first.get("records") and first.get("status") == DONE:   # a paused part 1 is no record's cost (I-5)
        state["tokens_per_record"] = max(1, round(tokens / first["records"]))
    if measured and week_before_pct is not None and week_after_pct is not None and week_after_pct > week_before_pct:
        state["week_per_token_share"] = (float(week_after_pct) - float(week_before_pct)) / tokens
    _settle_window(state, now)
    _resplit(state)
    _refresh(workspace, state, now)
    save(workspace, state)
    return share


# ----- what is due -------------------------------------------------------------------------------
def _run_tokens(workspace, since: dt.datetime, until: dt.datetime) -> int:
    """The tokens the routines really used between two moments: the meter rows `routine.py` writes (`meter_rows`;
    nothing an agent wrote, its own run line included, counts)."""
    total = 0
    for row in meter_rows(workspace, until):
        tokens = row.get("cost_tokens")
        if since < row["at"] <= until and isinstance(tokens, int) and not isinstance(tokens, bool) and tokens > 0:
            total += tokens
    return total


def _use(workspace, state: dict, now: dt.datetime, share: float | None) -> float:
    """The share of the 5-hour window in use now: the latest reading and what the routines used since, and
    nothing of it once the window it was taken in has reset (what ran since the reset still counts)."""
    reading = state.get("reading")
    guess = share if share is not None else DEFAULT_PER_TOKEN_SHARE
    if not reading:
        return _run_tokens(workspace, now - dt.timedelta(hours=WINDOW_HOURS), now) * guess
    taken = _dt(reading.get("at")) or now
    base, end = _window_use(state, now)
    since = taken if end else (_dt(reading.get("five_hour_resets")) or taken + dt.timedelta(hours=WINDOW_HOURS))
    return base + _run_tokens(workspace, since, now) * guess


def _unmeasured_for_long(state: dict, now: dt.datetime) -> bool:
    """Part 1 has been done for CALIBRATE_WAIT_HOURS and nobody measured it: the guess takes over."""
    done = _dt((_parts(state) or [{}])[0].get("finished_at"))
    return bool(done) and now - done >= dt.timedelta(hours=CALIBRATE_WAIT_HOURS)


def _assume_guess(workspace, state: dict, now: dt.datetime) -> None:
    """Nobody measured part 1 within CALIBRATE_WAIT_HOURS: the guess takes over for good, with part 1's tokens per
    record, the window cut at plan time is settled by it (`_settle_window`) and what is left is cut by it so no part
    passes the cap. The one place a question about what is due writes: the run that asks must read parts that were
    cut, or the part it reads would not be the one `after_run` marks."""
    state["per_token_share"] = DEFAULT_PER_TOKEN_SHARE
    state["calibration"] = "assumed"
    first = (_parts(state) or [{}])[0]
    if first.get("tokens") and first.get("records"):
        state["tokens_per_record"] = max(1, round(first["tokens"] / first["records"]))
    _settle_window(state, now)
    _resplit(state)
    _refresh(workspace, state, now)
    save(workspace, state)


def _week_used(workspace, state: dict, now: dt.datetime) -> float:
    """The share of the week in use now: the latest reading's, and what the routines used since it."""
    reading = state.get("reading") or {}
    taken = _dt(reading.get("at")) or now
    return float(reading.get("week_pct") or 0) + _run_tokens(workspace, taken, now) * _week_rate(state)


def _lone_why(part: dict, reading: dict | None) -> str:
    """Why one day that cannot be cut waits: it is bigger than a window, and with extra usage on, or no reading to say
    it is off, reading it could cost extra."""
    try:
        day = dt.date.fromisoformat(part["from"])
        named = f"{day:%b} {day.day}"
    except (KeyError, ValueError):
        named = "This day"
    if reading:
        return (f"{named} alone is bigger than one window of your limits; with extra usage on, reading it could cost "
                "extra. Turn extra usage off and it is read at night.")
    return (f"{named} alone is bigger than one window of your limits; it is read at night once a reading of your usage "
            "shows that extra usage is off.")


def _gate(workspace, state: dict, part: dict, now: dt.datetime) -> tuple:
    """The pacing rules for the next part (`explain` says which): (the part, "") when it may start now, else (None, why)."""
    if state.get("waiting"):
        return None, str(state["waiting"])
    if state.get("per_token_share") is None and part["id"] != "p1":
        if not _unmeasured_for_long(state, now):
            return None, "The first part has not been measured yet, so the rest waits for it."
        _assume_guess(workspace, state, now)
        part = _next(state)
    share = state.get("per_token_share")
    resets = _dt(part.get("resets_at"))
    if part.get("status") == PAUSED and resets and now < resets:
        return None, f"Your Claude limit resets at {_clock(resets, now)}; the read goes on after that."
    reading = state.get("reading")
    taken = _dt((reading or {}).get("at"))
    runs = [r for r in part.get("runs") or [] if isinstance(r, dict)]
    if part.get("status") == PAUSED and not resets and runs and runs[-1].get("outcome") == "overage":
        stopped = _dt(runs[-1].get("at"))
        if stopped is None or taken is None or taken <= stopped:
            return None, OVERAGE_WHY
    day = _daytime(now)
    if day and (taken is None or now - taken > dt.timedelta(minutes=READING_MINUTES)):
        return None, (f"Between {DAY_FROM}:00 and {DAY_TO}:00 a part starts only with a reading of your usage from "
                      "the last hour, and there is none.")
    use = _use(workspace, state, now, share)
    # a reading that may be another account's says nothing about this one's extra usage: it counts as on (R38)
    extra = bool(reading and (reading.get("extra_usage") or _unconfirmed(reading)))
    on = "extra usage is on" if reading and reading.get("extra_usage") else "extra usage may be on"
    if extra:   # a limit reached is paid usage: nothing starts that, by its own estimate, passes the window or the week
        if use >= 100:
            return None, f"Your 5-hour limit is used up and {on}, so nothing starts until it resets."
        week_resets = _dt(reading.get("week_resets"))
        if week_resets is None or now < week_resets:
            week_used, week_need = _week_used(workspace, state, now), _tokens_left(state, part) * _week_rate(state)
            if week_used >= 100:
                return None, (f"Your week's limit is used up and {on}, so nothing starts until it resets or a "
                              "newer reading of your usage shows room.")
            if week_used + week_need > 100:
                return None, (f"This part needs about {week_need:.0f}% of your week's limit and {week_used:.0f}% is in use, "
                              f"and {on}, so it waits until the week resets or a newer reading of your "
                              "usage shows room.")
    cap = _cap(now)
    need = _points(state, part, share)
    single = _days(part) <= 1
    begun = not _untouched(part)
    # one day cannot be cut: a night, stopped at fifty minutes, and only when the latest reading says extra usage is off
    lone = single and need > cap and not day and use < cap and bool(reading) and not extra
    # a part a run has begun goes on at night however big: every run stops at its fifty minutes (R45, I-5)
    going_on = begun and not single and need > cap and not day and use < cap and bool(reading) and not extra
    if use + need > cap and not lone and not going_on:
        if need > cap and single and (extra or not reading):
            return None, _lone_why(part, None if _unconfirmed(reading) else reading)
        if need > cap:
            figure = f"This part needs about {math.ceil(round(need, 6))}% of your 5-hour limit, more than the "
            if single:
                return None, (f"{figure}{cap}% that may be in use at this time of day, and it is a single day that cannot "
                              "be cut smaller, so it starts only at night.")
            if begun and not day and use < cap:   # only extra usage holds it: it may be on, or no reading says it is off
                return None, (f"{figure}{cap}% that may be in use at night, and {on}, so it goes on once a reading of "
                              "your usage shows that extra usage is off.")
            if begun and not day:
                return None, (f"There is not enough room in your 5-hour limit right now: {use:.0f}% is in use, and at "
                              f"this time of day at most {cap}% may be.")
            if need > NIGHT_CAP and not begun:
                return None, f"{figure}{NIGHT_CAP}% that a night allows, so it is not started."
            return None, f"{figure}{cap}% that may be in use by day, so it waits for the night."
        return None, (f"There is not enough room in your 5-hour limit right now: this part needs about {need:.0f}% of "
                      f"it and {use:.0f}% is in use, and at this time of day at most {cap}% may be.")
    room = _day_one_tokens(state)
    started = _dt(state.get("started"))
    if room is not None and started and now < _morning(started):
        if _tokens_left(state, part) > room:
            return None, (f"On the first day the history takes at most {FIRST_DAY_WEEK}% of what is left of your week, "
                          f"and that is used up; the read goes on after {DAY_FROM}:00.")
    return dict(part), ""


def explain(workspace, now: dt.datetime, own: bool = False) -> tuple:
    """(the part to read now, else None and why, in plain words). While a routine run holds the workspace's lock only
    that run's agent (`own=True`, what the command line asks) is given the part the lock names, whatever the clock
    says by then; a run that started with none is given the normal checks' reason; anyone else, a run that starts
    meanwhile included, is given none. Otherwise it reads files and writes nothing, but for the one step that
    cuts what is left by the guess (`_assume_guess`)."""
    now = _aware(now)
    state = load(workspace)
    if state.get("broken"):
        return None, BROKEN_WHY
    if not _parts(state):
        return None, "There is no history to read."
    part = _next(state)
    if part is None:
        return None, "The history is read."
    state = _newest_reading(workspace, state, now)   # a routine's own windows, when they are newer than the reading kept
    going, named = _own_run(workspace)
    if not going:
        return _gate(workspace, state, part, now)
    if not own:
        return None, ANOTHER_RUN_WHY
    mine = next((p for p in _parts(state) if p["id"] == named and p.get("status") != DONE), None) if named else None
    if mine:
        return dict(mine), ""
    would, why = _gate(workspace, state, part, now)   # started with none: this run reads none, and says why
    return None, (why if would is None else NO_PART_WHY)


def due(workspace, now: dt.datetime, own: bool = False) -> dict | None:
    """The part to read now: the next pending or paused part when all of these hold: its reset time (if the limit gave
    one) has passed, and after an `overage` that gave none a newer reading is in; between 08:00 and 19:00 local the
    latest reading is at most 60 minutes old (without one, only the other hours are due); the part's estimated share
    plus the 5-hour use now is within the cap (50 by day, 90 otherwise; one day that cannot be cut may start at night,
    but only when the latest reading says extra usage is off); on the setup day the backfill's tokens are within 30%
    of the week left at setup. Before part 1 is measured only part 1 is due. With extra usage on, a part that would
    take the 5-hour window or the week past 100% starts none. While a routine run holds the lock `own` decides who is
    given its part (`explain`). None when no part is due."""
    return explain(workspace, now, own)[0]


# ----- what a run does to the state --------------------------------------------------------------
def checkpoint(workspace, which: str, feed: str, at: str) -> None:
    """The export of this feed has reached `at` (the newest record's time) in part `which`. It never goes back, and
    it is the run's proof that it read the part (`after_run`)."""
    state = _writable(workspace)
    part = _find(state, which)
    moment = _dt(at)
    if moment is None:
        raise ValueError(f"{at!r} is not a date and time")
    marks = part.setdefault("checkpoints", {})
    old = _dt(marks.get(feed))
    if old is None or moment > old:
        marks[str(feed)] = at if isinstance(at, str) else _iso(moment)
    part.pop("unread", None)
    save(workspace, state)


def export_mark(workspace, feed: str) -> str | None:
    """During the backfill the regular sweep's start mark for a feed: the running or next part's checkpoint, else its
    `from` (decision F). While the plan is held (a paid account) the parts read nothing, and the regular sweep exports
    what is new from the day the plan was made. A file that cannot be read gives the day it was last written: the
    rest of the history is not exported unpaced. None once every part is done: the feed file's last time takes over."""
    state = load(workspace)
    if state.get("broken"):
        return dt.datetime.fromtimestamp(_path(workspace).stat().st_mtime).astimezone().date().isoformat()
    part = _next(state)
    if part is None:
        return None
    started = _dt(state.get("started"))
    if state.get("waiting") and started:
        return started.date().isoformat()
    return (part.get("checkpoints") or {}).get(feed) or part["from"]


def _sum(usage) -> int:
    if not isinstance(usage, dict):
        return 0
    return sum(v for v in usage.values() if isinstance(v, int) and not isinstance(v, bool) and v > 0)


def after_run(workspace, which: str, outcome: dict, now: dt.datetime | None = None) -> None:
    """What the run that read part `which` came to (`routine.outcome`): the tokens it used are added;
    `finished` sets the part done only when the run said it is through (`read`, R45 I-4); a run that checkpointed
    and did not say so, or said it stopped short (`unfinished`), leaves it paused (WHY["unfinished"]), and one that
    began it with `begin` and read nothing leaves it paused and counts it `idle`. `limit`, `overage` and `timeout`
    pause it, with `resets_at` when the outcome gave one and none when it did not; `auth` pauses it with why. Any other
    outcome leaves the part as it was, and a part that is done stays done. When part 1 is done, a record's cost is
    its own from then on (`tokens_per_record`), and once it was measured what is left is cut again by it (I-5). A
    part the plan does not have is ignored."""
    state = _writable(workspace)
    part = next((p for p in _parts(state) if p["id"] == which), None)
    if part is None or not isinstance(outcome, dict):
        return
    now = _aware(now) if now else _state_now(state)
    kind = outcome.get("outcome")
    part["tokens"] = part.get("tokens", 0) + _sum(outcome.get("usage"))
    cut, unread = bool(part.pop("unfinished", False)), bool(part.pop("unread", False))
    through = bool(part.pop("through", False))
    runs = [r for r in part.get("runs") or [] if isinstance(r, dict)][-4:]
    part["runs"] = runs + [{"at": _iso(now), "outcome": str(kind)}]
    if part.get("status") == DONE:   # read already: what this run used is counted, nothing more
        pass
    elif kind == "finished" and through and not cut:
        part.update(status=DONE, finished_at=_iso(now))
        for key in ("resets_at", "why", "idle"):
            part.pop(key, None)
        first = _parts(state)[0]
        if part is first and part.get("tokens") and part.get("records"):   # what a record really costs (I-5)
            state["tokens_per_record"] = max(1, round(part["tokens"] / part["records"]))
            if state.get("per_token_share") is not None:
                _resplit(state)
    elif kind == "finished" or kind in PAUSING:   # a finished run that was not through, or read nothing, is paused too
        resets = outcome.get("resets_at") if kind in ("limit", "overage", "timeout") else None
        part.update(status=PAUSED, resets_at=resets or None)
        if kind == "finished":
            part["why"] = WHY["idle" if unread else "unfinished"]
        else:
            part["why"] = WHY["limit" if resets else "limit_no_reset"] if kind == "limit" else WHY[kind]
        if unread and kind == "finished":
            part["idle"] = part.get("idle", 0) + 1
        elif not unread:
            part.pop("idle", None)
    _refresh(workspace, state, now)
    save(workspace, state)


# ----- the command line --------------------------------------------------------------------------
PERSON_VERBS = ("plan", "reading", "calibrate", "start")   # a routine run may not run these (lock-based, not env-based)
RUN_VERBS = ("checkpoint", "read", "unfinished")            # a run says these, and only about the part its lock names


def _counts(text: str) -> dict:
    found = {}
    for item in (text or "").split(","):
        month, sep, number = item.partition("=")
        if not sep or not number.strip().isdigit():
            raise ValueError(f"{item.strip()!r} is not month=number, for example 2026-10=1400")
        found[month.strip()] = int(number)
    return found


def _show(state: dict, now: dt.datetime) -> str:
    lines = [f"window: {state.get('window_days')} days from {state.get('started', '?')}",
             f"expected through: {state.get('current_by') or 'unknown'}"]
    if state.get("shortened_because"):
        lines.append(state["shortened_because"])
    for p in _parts(state):
        extra = f", {p['why']}" if p.get("why") and p.get("status") != DONE else ""
        lines.append(f"{p['id']}: {p['status']}, {p['from']} to {p['to']}, about {p['records']} records{extra}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("workspace")
    sub = ap.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true")
    common.add_argument("--now", help="use this moment (an ISO date and time) as the time")
    p = sub.add_parser("plan", parents=[common])
    p.add_argument("--counts", required=True)
    p.add_argument("--window-days", type=int, default=90)
    p.add_argument("--reading")
    sub.add_parser("due", parents=[common])
    p = sub.add_parser("checkpoint", parents=[common])
    p.add_argument("part")
    p.add_argument("feed")
    p.add_argument("at")
    p = sub.add_parser("read", parents=[common])
    p.add_argument("part")
    p = sub.add_parser("mark", parents=[common])
    p.add_argument("feed")
    p = sub.add_parser("reading", parents=[common])
    p.add_argument("phrase")
    p = sub.add_parser("calibrate", parents=[common])
    p.add_argument("--before", type=float, required=True)
    p.add_argument("--after", type=float, required=True)
    p.add_argument("--tokens", type=int)
    p.add_argument("--week-before", type=float)
    p.add_argument("--week-after", type=float)
    p = sub.add_parser("unfinished", parents=[common])
    p.add_argument("part")
    sub.add_parser("start", parents=[common])
    sub.add_parser("status", parents=[common])
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    ws = Path(args.workspace)
    now = _dt(args.now) if args.now else _real_now()
    if now is None:
        print(f"backfill: {args.now!r} is not a date and time", file=sys.stderr)
        return 2
    try:
        caller = _caller_space()
        if caller is not None and not _same_folder(caller, ws) and _own_run(caller)[0]:
            raise ValueError(OTHER_SPACE_WHY)   # a routine run says nothing about another space's history (R43)
        going, named = _own_run(ws)
        if args.command in PERSON_VERBS and going:   # what only a session or the person says, not a routine
            raise ValueError(RUN_GOING_WHY)
        if args.command in RUN_VERBS and going and args.part != named:
            raise ValueError(f"This routine run reads {named}, so it may not say anything about {args.part}." if named else
                             f"This routine run reads no part of the history, so it has nothing to say about {args.part}.")
        if args.now and caller is not None and _own_run(caller)[0]:   # a run keeps the computer's clock (R47, O3-1)
            raise ValueError(NOW_WHY)
        if args.now and now > _real_now() + FUTURE_SLACK:
            raise ValueError(NOW_AHEAD_WHY)
        elsewhere = runs_elsewhere(ws) if args.command in PERSON_VERBS else None
        if elsewhere:   # the history's notes are written where the routines run (R45, I-1)
            raise ValueError(ELSEWHERE_WHY.format(name=elsewhere))
        if args.command == "plan":
            given = parse_reading(args.reading, now)
            if given is not None:
                reading_of(given, _real_now())   # dated ahead of this computer's clock: refused, whatever now says (N3-1)
            state = plan(ws, _counts(args.counts), now, given, args.window_days)
            print(json.dumps(state, indent=2) if args.json else _show(state, now))
        elif args.command == "due":
            part, why = explain(ws, now, own=True)   # the agent of a run asks, as the holder of the lock
            if args.json:
                held = load(ws)
                print(json.dumps({"active": active(ws), "part": part, "why": why,
                                  "held": bool(held.get("waiting") or held.get("broken"))}, indent=2))
            else:
                print(f"{part['id']}: {part['from']} to {part['to']}" if part else why)
        elif args.command == "checkpoint":
            checkpoint(ws, args.part, args.feed, args.at)
        elif args.command == "read":
            range_read(ws, args.part)
        elif args.command == "mark":
            mark = export_mark(ws, args.feed)
            print(json.dumps({"feed": args.feed, "mark": mark}) if args.json else (mark or ""))
        elif args.command == "reading":
            given = parse_reading(args.phrase, now)
            if given is None:
                raise ValueError("that is not a usage reading")
            reading_of(given, _real_now())   # dated ahead of this computer's clock: refused, whatever now says (N3-1)
            why = unconfirmed_why()
            if why:
                given["confirmed"] = False
            record_reading(ws, given)
            if why:
                print(json.dumps({"confirmed": False, "why": why}) if args.json else why)
        elif args.command == "calibrate":
            state = load(ws)
            first = (_parts(state) or [{}])[0]
            tokens = args.tokens if args.tokens is not None else first.get("tokens", 0)
            share = calibrate(ws, args.before, args.after, tokens, args.week_before, args.week_after, now)
            print(json.dumps({"per_token_share": share}) if args.json else f"{share:.8f} of the 5-hour limit per token")
        elif args.command == "unfinished":
            unfinished(ws, args.part)
        elif args.command == "start":
            start(ws)
        else:
            state = load(ws)
            if args.json:
                print(json.dumps({**state, "active": active(ws), "why": explain(ws, now, own=True)[1]}, indent=2))
            else:
                print(_show(state, now) if _parts(state) else explain(ws, now, own=True)[1])
    except (ValueError, OSError) as exc:
        print(f"backfill: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
