"""The history, read oldest first, in paced parts that survive a closed lid: the plan, what is due
now under the pacing caps, what a run came to, the routine that runs a part, and the phase that
starts it. Times are the fixed `+02:00` ones below, never the clock."""
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import backfill
import onboard
import routine
import setup_journal
import start
from conftest import args, make_space
from test_start import CALENDAR, GMAIL, OUTLOOK, finished_setup, no_outside

TZ = dt.timezone(dt.timedelta(hours=2))
AT_14H = dt.datetime(2026, 10, 24, 14, 0, tzinfo=TZ)
AT_19H = dt.datetime(2026, 10, 24, 19, 0, tzinfo=TZ)
AT_01H = dt.datetime(2026, 10, 25, 1, 0, tzinfo=TZ)
NOW = AT_14H
COUNTS = {"2026-07": 400, "2026-08": 1500, "2026-09": 1500, "2026-10": 1400}
FINISHED = {"outcome": "finished", "why": "", "resets_at": None, "usage": {}}


def reading(five_hour_pct=0, week_pct=0, extra_usage=False, at=AT_14H, **more) -> dict:
    return {"at": at, "five_hour_pct": five_hour_pct, "week_pct": week_pct,
            "five_hour_resets": more.get("five_hour_resets", at + dt.timedelta(hours=5)),
            "week_resets": more.get("week_resets", at + dt.timedelta(days=5)), "extra_usage": extra_usage}


def used(tokens: int) -> dict:
    return {"outcome": "finished", "why": "", "resets_at": None, "usage": {"input_tokens": tokens}}


def run_line(ws, at: str, tokens: int, step: str = "routine") -> None:
    path = ws / "_meta" / "state" / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"routine": "sweep-chain", "at": at, "space": "ws", "cost_tokens": tokens,
                             "notes": {"step": step}}) + "\n")
    if step == "routine":   # routine.py writes the same run to its meter, which the pacing reads
        meter_row(ws, routine="sweep-chain", at=at, outcome="finished", cost_tokens=tokens)


def meter_row(ws, **row) -> None:
    """A row routine.py itself writes for each run (`routine.LIMITS_FILE`), the file no routine can edit."""
    path = ws / routine.LIMITS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row) + "\n")


def parts_of(ws) -> list:
    return backfill.load(ws)["parts"]


def part(ws, which: str) -> dict:
    return next(p for p in parts_of(ws) if p["id"] == which)


def status(ws, which: str) -> str:
    return part(ws, which)["status"]


def finish_all(ws) -> None:
    for p in parts_of(ws):
        if p["status"] != "done":
            read_through(ws, p["id"], FINISHED)


def read_through(ws, which: str, outcome: dict, now=None) -> None:
    """A run read part `which` to its end and said so (`read`), then ended with `outcome`: a part is done only on
    `read` (R45, I-4)."""
    backfill.range_read(ws, which)
    backfill.after_run(ws, which, outcome, now=now)


def planned(tmp_path, per_token_share=None, part_tokens=None, p1_tokens=500_000):
    """A workspace with a three-part plan; with a share, part 1 is read (`p1_tokens`) and the plan is
    calibrated to it, and each part left costs `part_tokens`."""
    ws = make_space(tmp_path / "ws")
    backfill.plan(ws, COUNTS, NOW)
    if per_token_share is not None:
        read_through(ws, "p1", used(p1_tokens))
        backfill.calibrate(ws, 0, per_token_share * p1_tokens, p1_tokens)
        if part_tokens is not None:
            state = backfill.load(ws)
            state["tokens_per_record"] = 1000
            for p in state["parts"]:
                if p["status"] != "done":
                    p["records"] = part_tokens // 1000
            backfill.save(ws, state)
    return ws


# ----- the plan -----------------------------------------------------------------------------------
def test_parts_are_consecutive_and_oldest_first(tmp_path):
    ws = make_space(tmp_path / "ws")
    p = backfill.plan(ws, {"2026-07": 400, "2026-08": 1500, "2026-09": 1500, "2026-10": 1400}, NOW)
    parts = p["parts"]
    assert parts[0]["from"] == (NOW - dt.timedelta(days=90)).date().isoformat()
    assert all(a["to"] == b["from"] for a, b in zip(parts, parts[1:])) and parts[-1]["to"] == NOW.date().isoformat()
    assert parts[0]["records"] <= 1500


def test_part_one_is_the_oldest_stretch_and_the_rest_splits_in_two(tmp_path):
    p = backfill.plan(make_space(tmp_path / "ws"), COUNTS, NOW)
    first, second, third = p["parts"]
    assert [x["id"] for x in p["parts"]] == ["p1", "p2", "p3"]
    assert 500 < first["records"] <= 750 and abs(second["records"] - third["records"]) <= 0.2 * second["records"]   # a night's worth
    assert all(x["status"] == "pending" and x["checkpoints"] == {} and x["tokens"] == 0 for x in p["parts"])
    assert p["window_days"] == 90 and p["per_token_share"] is None and p["started"] == NOW.isoformat(timespec="seconds")
    assert backfill.load(tmp_path / "ws") == p and "current_by" in p


def test_a_small_history_is_one_part_and_no_history_is_nothing_to_read(tmp_path):
    p = backfill.plan(make_space(tmp_path / "a"), {"2026-10": 300}, NOW)
    assert [x["id"] for x in p["parts"]] == ["p1"] and p["parts"][0]["records"] <= 300
    none = make_space(tmp_path / "b")
    backfill.plan(none, {}, NOW)
    assert not backfill.active(none) and backfill.due(none, AT_01H) is None


def test_a_window_that_cannot_fit_by_morning_is_shortened_before_part_1(tmp_path):
    ws = make_space(tmp_path / "ws")
    big = {m: 9000 for m in ("2026-07", "2026-08", "2026-09", "2026-10")}
    p = backfill.plan(ws, big, AT_14H, reading=reading(five_hour_pct=5, week_pct=26, at=AT_14H))
    assert 28 <= p["window_days"] < 90 and p["window_days"] % 7 == 0 and p["shortened_because"]
    assert p["parts"][0]["from"] == (AT_14H - dt.timedelta(days=p["window_days"])).date().isoformat()
    assert "90" in p["shortened_because"] and "tool" not in p["shortened_because"]   # plain words


def test_a_window_that_fits_is_kept_and_without_a_reading_nothing_is_estimated(tmp_path):
    small = backfill.plan(make_space(tmp_path / "a"), {"2026-10": 120}, AT_14H, reading=reading(five_hour_pct=5))
    assert small["window_days"] == 90 and "shortened_because" not in small
    big = {m: 9000 for m in ("2026-07", "2026-08", "2026-09", "2026-10")}
    assert backfill.plan(make_space(tmp_path / "b"), big, AT_14H)["window_days"] == 90


def test_the_week_already_used_shortens_the_window_too(tmp_path):
    counts = {"2026-09": 600, "2026-10": 600}
    idle = backfill.plan(make_space(tmp_path / "a"), counts, AT_14H, reading=reading(week_pct=10))
    spent = backfill.plan(make_space(tmp_path / "b"), counts, AT_14H, reading=reading(week_pct=96))
    assert idle["window_days"] == 90 and spent["window_days"] < 90 and spent["shortened_because"]


def test_the_plan_keeps_the_reading_it_was_made_from_as_the_setup_reading(tmp_path):
    ws = make_space(tmp_path / "ws")
    p = backfill.plan(ws, COUNTS, NOW, reading=reading(five_hour_pct=7, week_pct=31))
    assert p["setup_reading"]["week_pct"] == 31 and p["reading"] == p["setup_reading"]
    assert p["setup_reading"]["at"] == AT_14H.isoformat(timespec="seconds")   # a moment is kept as text


def test_readings_keep_the_first_as_the_setup_and_the_latest(tmp_path):
    ws = make_space(tmp_path / "ws")
    backfill.record_reading(ws, reading(five_hour_pct=5, week_pct=26, at=AT_14H))
    backfill.record_reading(ws, reading(five_hour_pct=40, week_pct=30, at=AT_19H, extra_usage=True))
    state = backfill.load(ws)
    assert state["setup_reading"]["week_pct"] == 26 and state["reading"]["five_hour_pct"] == 40
    assert state["reading"]["extra_usage"] is True
    backfill.plan(ws, COUNTS, NOW)   # a plan made after readings keeps them
    assert backfill.load(ws)["setup_reading"]["week_pct"] == 26


def test_a_reading_is_parsed_from_the_phrase_the_commands_take():
    got = backfill.parse_reading("five_hour=12,week=40,five_hour_resets=2026-10-24T22:30:00Z,"
                                 "week_resets=2026-10-27T11:00:00.359Z,extra=1", AT_14H)
    assert got["five_hour_pct"] == 12 and got["week_pct"] == 40 and got["extra_usage"] is True
    assert got["at"] == AT_14H.isoformat(timespec="seconds") and got["five_hour_resets"].startswith("2026-10-24T22:30:00")
    assert backfill.parse_reading("", AT_14H) is None and backfill.parse_reading("unavailable", AT_14H) is None
    for bad in ("five_hour=lots", "week=40", "five_hour=120", "nonsense", "five_hour=1,wat=2"):
        with pytest.raises(ValueError):
            backfill.parse_reading(bad, AT_14H)


# ----- what is due now ----------------------------------------------------------------------------
def test_a_daytime_part_needs_a_reading_from_the_last_hour(tmp_path):
    ws = planned(tmp_path, per_token_share=0.00002, part_tokens=500_000)     # 10 points per part
    backfill.record_reading(ws, reading(five_hour_pct=5, at=AT_14H - dt.timedelta(minutes=90)))
    assert backfill.due(ws, AT_14H) is None                                   # 90 minutes old
    backfill.record_reading(ws, reading(five_hour_pct=5, at=AT_14H - dt.timedelta(minutes=20)))
    assert backfill.due(ws, AT_14H)["id"] == "p2"
    backfill.record_reading(ws, reading(five_hour_pct=5, at=AT_01H - dt.timedelta(hours=6)))
    assert backfill.due(ws, AT_01H)["id"] == "p2"                             # nights need no fresh one


def test_without_any_reading_only_the_hours_outside_work_are_due(tmp_path):
    ws = planned(tmp_path, per_token_share=0.00002, part_tokens=500_000)
    assert backfill.due(ws, AT_14H) is None and backfill.due(ws, AT_01H)["id"] == "p2"
    seven_fifty_nine = dt.datetime(2026, 10, 24, 7, 59, tzinfo=TZ)
    eight = dt.datetime(2026, 10, 24, 8, 0, tzinfo=TZ)
    assert backfill.due(ws, seven_fifty_nine)["id"] == "p2" and backfill.due(ws, eight) is None   # 19:00 is already night


def test_daytime_parts_leave_half_the_window_free(tmp_path):
    ws = planned(tmp_path, per_token_share=0.00002, part_tokens=1_500_000)   # 30 points per part
    backfill.record_reading(ws, reading(five_hour_pct=25, at=AT_14H))
    assert backfill.due(ws, AT_14H) is None                                   # 25 + 30 > 50
    backfill.record_reading(ws, reading(five_hour_pct=25, at=AT_01H))
    assert backfill.due(ws, AT_01H)["id"] == "p2"                             # 25 + 30 <= 90
    backfill.record_reading(ws, reading(five_hour_pct=65, at=AT_01H))
    assert backfill.due(ws, AT_01H) is None                                   # 65 + 30 > 90


def test_day_one_takes_at_most_thirty_percent_of_the_week_left(tmp_path):
    ws = planned(tmp_path, per_token_share=0.00002, part_tokens=500_000)     # a part: 10 points of the window
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=26, at=AT_14H))   # 74 left: 22.2 points of week
    assert backfill.due(ws, AT_01H)["id"] == "p2"
    # a week is about ten full windows: parts that already used 22 points of it are 220 points of the window
    backfill.after_run(ws, "p1", used(10_500_000))
    assert backfill.due(ws, AT_01H) is None
    assert "first day" in backfill.explain(ws, AT_01H)[1]
    assert backfill.due(ws, dt.datetime(2026, 10, 25, 7, 59, tzinfo=TZ)) is None     # the setup day runs to 08:00
    assert backfill.due(ws, dt.datetime(2026, 10, 25, 19, 0, tzinfo=TZ))["id"] == "p2"   # the setup day is over


def test_a_measured_weekly_share_replaces_the_guess_for_the_setup_day(tmp_path):
    ws = planned(tmp_path, per_token_share=0.00002, part_tokens=500_000)
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=26, at=AT_14H))   # 22.2 points of week for the day
    state = backfill.load(ws)
    state["week_per_token_share"] = 22.2 / 600_000                            # measured: 600,000 tokens are 22.2 points
    backfill.save(ws, state)
    assert backfill.due(ws, AT_01H) is None                                   # 500,000 used + 500,000 for the part
    state = backfill.load(ws)
    state["parts"][0]["tokens"] = 50_000
    backfill.save(ws, state)
    assert backfill.due(ws, AT_01H)["id"] == "p2"                             # 50,000 + 500,000 fit in 600,000


def test_before_part_one_is_measured_only_part_one_is_due(tmp_path):
    ws = planned(tmp_path)
    assert backfill.due(ws, AT_01H)["id"] == "p1"
    read_through(ws, "p1", used(500_000))
    assert backfill.due(ws, AT_01H) is None                                   # p2 waits for the measure
    state = backfill.load(ws)
    assert backfill.explain(ws, AT_01H)[1]
    done_at = dt.datetime.fromisoformat(part(ws, "p1")["finished_at"])
    assert state["per_token_share"] is None
    assert backfill.due(ws, done_at + dt.timedelta(hours=13))["id"] == "p2"    # nobody measured it: the guess takes over


def test_the_estimate_adds_what_the_runs_since_the_reading_used(tmp_path):
    ws = planned(tmp_path, per_token_share=0.00002, part_tokens=500_000)     # 10 points a part
    backfill.record_reading(ws, reading(five_hour_pct=75, at=AT_01H - dt.timedelta(minutes=30),
                                        five_hour_resets=AT_01H + dt.timedelta(hours=4)))
    assert backfill.due(ws, AT_01H)["id"] == "p2"                             # 75 + 10 <= 90
    run_line(ws, "2026-10-24T22:45:00Z", 500_000)                             # +10 points: 75 + 10 + 10 > 90
    assert backfill.due(ws, AT_01H) is None
    run_line(ws, "2026-10-24T22:45:00Z", 5_000_000, step="agent")             # an estimate by the agent: not counted twice
    run_line(ws, "2026-10-24T10:00:00Z", 5_000_000)                           # before the reading: already in it
    assert backfill.due(ws, AT_01H) is None
    after_reset = AT_01H + dt.timedelta(hours=4, minutes=1)                   # the window has reset: nothing in use
    assert backfill.due(ws, after_reset)["id"] == "p2"


def test_extra_usage_never_starts_a_part(tmp_path):
    ws = planned(tmp_path)
    backfill.record_reading(ws, reading(five_hour_pct=100, extra_usage=True, at=AT_01H))
    assert backfill.due(ws, AT_01H) is None
    assert "extra usage" in backfill.explain(ws, AT_01H)[1]
    backfill.record_reading(ws, reading(five_hour_pct=100, extra_usage=True, at=AT_01H,
                                        five_hour_resets=AT_01H - dt.timedelta(minutes=5)))
    assert backfill.due(ws, AT_01H)["id"] == "p1"                             # that window is over


def test_a_paid_account_is_held_until_the_person_says_go(tmp_path):
    ws = planned(tmp_path)
    backfill.hold(ws, "paid account: the person starts the read")
    assert backfill.active(ws) and backfill.due(ws, AT_01H) is None
    assert backfill.explain(ws, AT_01H)[1] == "paid account: the person starts the read"
    backfill.start(ws)
    assert backfill.due(ws, AT_01H)["id"] == "p1"


# ----- what a run came to -------------------------------------------------------------------------
def test_a_limit_without_a_reset_time_pauses_and_retries_next_run(tmp_path):
    ws = planned(tmp_path)
    backfill.after_run(ws, "p1", {"outcome": "limit", "why": "usage limit reached", "resets_at": None, "usage": {}})
    assert status(ws, "p1") == "paused"
    assert backfill.due(ws, AT_19H)["id"] == "p1"


def test_a_limit_without_a_reset_time_never_gets_one_made_up(tmp_path):
    ws = planned(tmp_path)
    backfill.after_run(ws, "p1", {"outcome": "limit", "why": "usage limit reached", "resets_at": None, "usage": {}})
    p = part(ws, "p1")
    assert p["resets_at"] is None and "didn't say" in p["why"] and not any(c.isdigit() for c in p["why"])
    assert "resets_at" not in backfill.load(ws) and p["checkpoints"] == {}


def test_a_limit_with_a_reset_time_waits_for_it(tmp_path):
    ws = planned(tmp_path)
    backfill.after_run(ws, "p1", {"outcome": "limit", "why": "limit", "resets_at": "2026-10-24T23:20:00+02:00", "usage": {}})
    assert status(ws, "p1") == "paused" and part(ws, "p1")["resets_at"] == "2026-10-24T23:20:00+02:00"
    assert backfill.due(ws, AT_19H) is None and "23:20" in backfill.explain(ws, AT_19H)[1]
    assert backfill.due(ws, AT_01H)["id"] == "p1"


@pytest.mark.parametrize("outcome", ["overage", "timeout", "auth"])
def test_overage_a_timeout_and_a_sign_in_pause_the_part_and_never_finish_it(tmp_path, outcome):
    ws = planned(tmp_path)
    backfill.after_run(ws, "p1", {"outcome": outcome, "why": "x", "resets_at": None, "usage": {}})
    p = part(ws, "p1")
    assert p["status"] == "paused" and p["why"] and "x" != p["why"]
    assert backfill.active(ws)


def test_extra_usage_that_ran_out_waits_for_the_reset_time_when_one_was_given(tmp_path):
    ws = planned(tmp_path)
    backfill.after_run(ws, "p1", {"outcome": "overage", "why": "", "resets_at": "2026-10-25T09:00:00+02:00", "usage": {}})
    assert part(ws, "p1")["resets_at"] == "2026-10-25T09:00:00+02:00"
    assert backfill.due(ws, AT_01H) is None and "09:00" in backfill.explain(ws, AT_01H)[1]
    backfill.after_run(ws, "p1", {"outcome": "timeout", "why": "", "resets_at": None, "usage": {}})
    assert part(ws, "p1")["resets_at"] is None and backfill.due(ws, AT_01H)["id"] == "p1"


def test_a_file_that_cannot_be_read_for_a_moment_is_no_empty_plan(tmp_path, monkeypatch):
    ws = planned(tmp_path)
    original = Path.read_text

    def locked(self, *a, **k):
        if self.name == "backfill.yaml":
            raise PermissionError(13, "in use by another process")
        return original(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", locked)
    monkeypatch.setattr(backfill, "_sleep", lambda s: None)
    with pytest.raises(PermissionError):
        backfill.record_reading(ws, reading(five_hour_pct=5))               # a write never starts from an unread file
    monkeypatch.setattr(Path, "read_text", original)
    assert [p["id"] for p in parts_of(ws)] == ["p1", "p2", "p3"] and "reading" not in backfill.load(ws)


def test_finished_sets_done_adds_the_tokens_and_keeps_a_short_history(tmp_path):
    ws = planned(tmp_path)
    backfill.after_run(ws, "p1", {"outcome": "limit", "why": "", "resets_at": None,
                                  "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 300,
                                            "cache_creation_tokens": 80}})
    read_through(ws, "p1", used(1000), now=AT_01H)
    p = part(ws, "p1")
    assert p["status"] == "done" and p["tokens"] == 1500 and p["finished_at"] == AT_01H.isoformat(timespec="seconds")
    assert [r["outcome"] for r in p["runs"]] == ["limit", "finished"]


def test_the_backfill_is_active_until_every_part_is_done(tmp_path):
    ws = planned(tmp_path)
    assert backfill.active(ws)
    finish_all(ws)
    assert not backfill.active(ws) and backfill.due(ws, AT_01H) is None
    assert backfill.active(tmp_path / "nowhere") is False


def test_a_run_that_stopped_itself_after_its_time_leaves_the_part_to_the_next_run(tmp_path):
    ws = planned(tmp_path)
    backfill.checkpoint(ws, "p1", "mail", "2026-08-02T10:00:00Z")
    backfill.unfinished(ws, "p1")
    backfill.after_run(ws, "p1", used(700))
    p = part(ws, "p1")
    assert p["status"] == "paused" and p["resets_at"] is None and p["tokens"] == 700 and "unfinished" not in p
    assert backfill.due(ws, AT_19H)["id"] == "p1" and backfill.export_mark(ws, "mail") == "2026-08-02T10:00:00Z"
    read_through(ws, "p1", used(100))
    assert status(ws, "p1") == "done"


def test_a_part_the_plan_does_not_know_is_left_alone(tmp_path):
    ws = planned(tmp_path)
    backfill.after_run(ws, "p9", FINISHED)
    assert status(ws, "p1") == "pending"


# ----- the regular sweep's mark -------------------------------------------------------------------
def test_the_regular_sweep_reads_its_mark_from_the_backfill_until_done(tmp_path):
    ws = planned(tmp_path)
    backfill.checkpoint(ws, "p1", "mail", "2026-08-02T10:00:00Z")
    assert backfill.export_mark(ws, "mail") == "2026-08-02T10:00:00Z"
    finish_all(ws)
    assert backfill.export_mark(ws, "mail") is None


def test_a_feed_without_a_checkpoint_starts_at_the_part_start_and_the_next_part_after_the_last(tmp_path):
    ws = planned(tmp_path)
    assert backfill.export_mark(ws, "calls") == part(ws, "p1")["from"]
    backfill.checkpoint(ws, "p1", "mail", "2026-08-02T10:00:00Z")
    backfill.checkpoint(ws, "p1", "mail", "2026-08-01T10:00:00Z")            # a checkpoint never goes back
    assert backfill.load(ws)["parts"][0]["checkpoints"]["mail"] == "2026-08-02T10:00:00Z"
    read_through(ws, "p1", FINISHED)
    assert backfill.export_mark(ws, "mail") == part(ws, "p2")["from"]
    assert backfill.export_mark(make_space(tmp_path / "other"), "mail") is None


def test_a_checkpoint_for_a_part_or_a_time_that_does_not_exist_is_refused(tmp_path):
    ws = planned(tmp_path)
    with pytest.raises(ValueError):
        backfill.checkpoint(ws, "p9", "mail", "2026-08-02T10:00:00Z")
    with pytest.raises(ValueError):
        backfill.checkpoint(ws, "p1", "mail", "yesterday-ish")


# ----- measuring part one -------------------------------------------------------------------------
def test_calibration_measures_the_share_and_resplits_what_is_left_to_fit_the_night(tmp_path):
    ws = make_space(tmp_path / "ws")
    before = backfill.plan(ws, COUNTS, NOW)
    rest = sum(p["records"] for p in before["parts"][1:])
    read_through(ws, "p1", used(9_000_000))
    share = backfill.calibrate(ws, 4, 94, 9_000_000)                      # 90 points of the window for part 1
    state = backfill.load(ws)
    assert share == pytest.approx(90 / 9_000_000) and state["per_token_share"] == share
    assert state["calibration"] == "measured" and state["window_days"] == before["window_days"]
    assert state["parts"][0] == dict(backfill.load(ws)["parts"][0]) and state["parts"][0]["status"] == "done"
    left = [p for p in state["parts"] if p["status"] != "done"]
    assert len(left) > 2 and [p["id"] for p in state["parts"]] == [f"p{n}" for n in range(1, len(state["parts"]) + 1)]
    for p in left:
        assert p["records"] * state["tokens_per_record"] * share <= backfill.NIGHT_CAP
    assert sum(p["records"] for p in left) == rest                        # nothing is lost or made up
    assert all(a["to"] == b["from"] for a, b in zip(state["parts"], state["parts"][1:]))
    assert state["parts"][-1]["to"] == NOW.date().isoformat() and state["parts"][1]["from"] == before["parts"][1]["from"]


def test_calibration_never_splits_a_part_that_already_fits(tmp_path):
    ws = planned(tmp_path)
    read_through(ws, "p1", used(500_000))
    backfill.calibrate(ws, 0, 10, 500_000)
    assert [p["id"] for p in parts_of(ws)] == ["p1", "p2", "p3"]


def test_calibration_leaves_a_part_that_has_started_alone(tmp_path):
    ws = make_space(tmp_path / "ws")
    backfill.plan(ws, COUNTS, NOW)
    read_through(ws, "p1", used(9_000_000))
    backfill.checkpoint(ws, "p2", "mail", "2026-09-01T00:00:00Z")
    before = part(ws, "p2")
    backfill.calibrate(ws, 4, 44, 9_000_000)
    assert part(ws, "p2") == before


def test_a_slower_pace_moves_the_finish_not_the_window(tmp_path):
    finish = {}
    for name, after in (("fast", 8), ("slow", 63)):
        ws = make_space(tmp_path / name)
        backfill.plan(ws, COUNTS, NOW)
        read_through(ws, "p1", used(2_000_000), now=AT_19H)
        backfill.calibrate(ws, 5, after, 2_000_000)
        finish[name] = backfill.load(ws)
    assert finish["fast"]["window_days"] == finish["slow"]["window_days"] == 90
    assert dt.datetime.fromisoformat(finish["slow"]["current_by"]) > dt.datetime.fromisoformat(finish["fast"]["current_by"])


def test_a_part_too_small_to_measure_leaves_the_guess_and_says_so(tmp_path):
    ws = planned(tmp_path)
    read_through(ws, "p1", used(40_000))
    share = backfill.calibrate(ws, 10, 11, 40_000)
    state = backfill.load(ws)
    assert share == backfill.DEFAULT_PER_TOKEN_SHARE and state["calibration"] == "assumed"
    assert backfill.due(ws, AT_01H)["id"] == "p2"


# ----- the command line ---------------------------------------------------------------------------
def cli(ws, *words) -> subprocess.CompletedProcess:
    home = str(Path.home())   # the test's own: the command line reads the routine lock there, never the person's
    return subprocess.run([sys.executable, str(backfill.__file__), str(ws), *words], capture_output=True, text=True,
                          encoding="utf-8", env={**os.environ, "HOME": home, "USERPROFILE": home})


def test_the_command_line_names_the_part_that_is_due_and_why_not(tmp_path):
    ws = planned(tmp_path)
    night = "--now=2026-10-25T01:00:00+02:00"
    got = json.loads(cli(ws, "due", "--json", night).stdout)
    assert got["active"] is True and got["part"]["id"] == "p1" and got["why"] == ""
    assert cli(ws, "due", night).stdout.startswith("p1:")
    backfill.hold(ws, "paid account: the person starts the read")
    held = json.loads(cli(ws, "due", "--json", night).stdout)
    assert held == {"active": True, "part": None, "why": "paid account: the person starts the read", "held": True}
    assert cli(ws, "due", night).stdout.strip() == "paid account: the person starts the read"
    nothing = json.loads(cli(make_space(tmp_path / "none"), "due", "--json", night).stdout)
    assert nothing["active"] is False and nothing["part"] is None and nothing["why"]


def test_the_command_line_checkpoints_marks_and_records_readings(tmp_path):
    ws = planned(tmp_path)
    assert cli(ws, "checkpoint", "p1", "mail", "2026-08-02T10:00:00Z").returncode == 0
    assert cli(ws, "mark", "mail").stdout.strip() == "2026-08-02T10:00:00Z"
    assert json.loads(cli(ws, "mark", "calls", "--json").stdout) == {"feed": "calls", "mark": part(ws, "p1")["from"]}
    done = cli(ws, "reading", "five_hour=12,week=40,five_hour_resets=2026-10-24T22:30:00Z,"
                              "week_resets=2026-10-27T11:00:00Z,extra=0")
    assert done.returncode == 0 and backfill.load(ws)["reading"]["week_pct"] == 40
    bad = cli(ws, "checkpoint", "p9", "mail", "2026-08-02T10:00:00Z")
    assert bad.returncode == 2 and "p9" in bad.stderr and "Traceback" not in bad.stderr


def test_the_command_line_calibrates_from_the_tokens_part_one_used(tmp_path):
    ws = planned(tmp_path)
    read_through(ws, "p1", used(500_000))
    done = cli(ws, "calibrate", "--before", "3", "--after", "13", "--now=2026-10-25T01:00:00+02:00")
    assert done.returncode == 0 and backfill.load(ws)["per_token_share"] == pytest.approx(10 / 500_000)
    status_out = json.loads(cli(ws, "status", "--json").stdout)
    assert [p["id"] for p in status_out["parts"]] == ["p1", "p2", "p3"] and "current_by" in status_out
    assert "p1" in cli(ws, "status").stdout


def test_the_command_line_starts_a_held_plan_and_marks_a_part_unfinished(tmp_path):
    ws = planned(tmp_path)
    backfill.hold(ws, "paid account: the person starts the read")
    assert cli(ws, "start").returncode == 0 and not backfill.load(ws).get("waiting")
    assert cli(ws, "unfinished", "p1").returncode == 0 and part(ws, "p1").get("unfinished") is True


def test_the_command_line_plans_from_counts(tmp_path):
    ws = make_space(tmp_path / "ws")
    made = cli(ws, "plan", "--counts", "2026-09=1500,2026-10=1400", "--json", "--now=2026-10-24T14:00:00+02:00")
    assert made.returncode == 0 and len(json.loads(made.stdout)["parts"]) == 3
    assert backfill.active(ws)


# ----- a routine that reads a part ----------------------------------------------------------------
def routine_ws(tmp_path, monkeypatch):
    import start as _start
    ws = make_space(tmp_path / "ada")
    _start.render_routines(ws, "Ada Okafor")
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    return ws


def run_prompt(ws, name, stdout="", seen=None):
    def run(argv, **kw):
        if seen is not None:
            seen.update(argv=list(argv), lock=routine.locked(ws), **kw)
        return subprocess.CompletedProcess(argv, 0, stdout, "")
    return routine.main([str(ws / "_meta" / "state" / "routines" / f"{name}.md")], run=run)


def stream(*events) -> str:
    return "".join(json.dumps(e) + "\n" for e in events)


RESULT = {"type": "result", "subtype": "success", "is_error": False, "result": "done", "usage": {}}


def test_the_night_prompt_costs_nothing_once_the_backfill_is_done(tmp_path, monkeypatch):
    ws = routine_ws(tmp_path, monkeypatch)
    backfill.plan(ws, COUNTS, NOW)
    finish_all(ws)
    called = []
    prompt = ws / "_meta" / "state" / "routines" / "backfill-night.md"
    assert routine.main([str(prompt)], run=lambda *a, **k: called.append(a)) == 0
    assert called == [] and not routine.lock_path(ws).exists()


def test_the_night_prompt_costs_nothing_while_nothing_is_due(tmp_path, monkeypatch):
    """Active, but held for a paid account: a run would only spend its first fifty thousand tokens to learn that."""
    ws = routine_ws(tmp_path, monkeypatch)
    backfill.plan(ws, COUNTS, NOW)
    backfill.hold(ws, "paid account: the person starts the read")
    called = []
    prompt = ws / "_meta" / "state" / "routines" / "backfill-night.md"
    assert routine.main([str(prompt)], run=lambda *a, **k: called.append(a)) == 0 and called == []
    log = (tmp_path / "_home" / ".spaces" / "logs" / "backfill-night-ada.log").read_text(encoding="utf-8")
    assert "paid account" in log and "exit 0" in log


def test_the_lock_names_the_part_the_run_reads_and_the_outcome_is_noted(tmp_path, monkeypatch):
    ws = routine_ws(tmp_path, monkeypatch)
    backfill.plan(ws, COUNTS, NOW)
    seen = {}
    monkeypatch.setattr(backfill, "due", lambda workspace, now: backfill.load(workspace)["parts"][0])
    usage = {"input_tokens": 1000, "output_tokens": 50}

    def reads(argv, **kw):                                                    # the agent reads the range: nothing in it
        seen.update(lock=routine.locked(ws))
        backfill.range_read(ws, "p1")
        return subprocess.CompletedProcess(argv, 0, stream({**RESULT, "usage": usage}), "")
    assert routine.main([str(ws / "_meta" / "state" / "routines" / "backfill-night.md")], run=reads) == 0
    assert seen["lock"]["part"] == "p1" and seen["lock"]["routine"] == "backfill-night"
    assert status(ws, "p1") == "done" and part(ws, "p1")["tokens"] == 1050
    assert routine.locked(ws) is None


def test_a_limit_in_a_run_pauses_its_part_and_the_reset_time_is_kept_only_when_given(tmp_path, monkeypatch):
    ws = routine_ws(tmp_path, monkeypatch)
    backfill.plan(ws, COUNTS, NOW)
    monkeypatch.setattr(backfill, "due", lambda workspace, now: backfill.load(workspace)["parts"][0])
    limit = {"type": "result", "subtype": "success", "is_error": True, "result": "You've hit your session limit"}
    assistant = {"type": "assistant", "error": "rate_limit"}
    assert run_prompt(ws, "sweep-chain", stream(assistant, limit)) != 0
    assert status(ws, "p1") == "paused" and part(ws, "p1")["resets_at"] is None
    line = json.loads((ws / "_meta" / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert line["outcome"] == "limit" and line["resets_at"] is None


def test_a_routine_that_reads_no_part_notes_nothing(tmp_path, monkeypatch):
    ws = routine_ws(tmp_path, monkeypatch)
    backfill.plan(ws, COUNTS, NOW)
    monkeypatch.setattr(backfill, "due", lambda workspace, now: backfill.load(workspace)["parts"][0])
    seen = {}
    assert run_prompt(ws, "daily-chain", stream(RESULT), seen) == 0
    assert "part" not in (seen["lock"] or {}) and status(ws, "p1") == "pending"


def test_a_busy_run_reads_no_part(tmp_path, monkeypatch):
    ws = routine_ws(tmp_path, monkeypatch)
    backfill.plan(ws, COUNTS, NOW)
    monkeypatch.setattr(backfill, "due", lambda workspace, now: backfill.load(workspace)["parts"][0])
    assert routine.take_lock(ws, "daily-chain")                           # another run holds the workspace
    seen = {}
    assert run_prompt(ws, "sweep-chain", stream(RESULT), seen) == 0 and seen == {}
    assert status(ws, "p1") == "pending" and routine.locked(ws)["routine"] == "daily-chain"


def test_a_broken_backfill_never_stops_a_routine(tmp_path, monkeypatch):
    ws = routine_ws(tmp_path, monkeypatch)
    (ws / "_meta" / "state" / "backfill.yaml").write_text("parts: [unclosed", encoding="utf-8")
    assert backfill.load(ws) == {"broken": True} and backfill.active(ws)      # held with a plain line, not "no backfill"
    assert run_prompt(ws, "sweep-chain", stream(RESULT)) == 0
    backfill.plan(ws, COUNTS, NOW)
    assert list(ws.joinpath("_meta", "state").glob("backfill.yaml.bad-*"))   # the broken file was kept aside
    monkeypatch.setattr(backfill, "due", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert run_prompt(ws, "sweep-chain", stream(RESULT)) == 0 and status(ws, "p1") == "pending"
    monkeypatch.setattr(backfill, "after_run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(backfill, "due", lambda workspace, now: backfill.load(workspace)["parts"][0])
    assert run_prompt(ws, "sweep-chain", stream(RESULT)) == 0


def test_the_routines_may_run_the_backfills_own_tool(tmp_path):
    assert "backfill.py" in routine.ROUTINE_TOOLS
    tools = Path(routine.__file__).resolve().parents[2]                    # a real space, so the other tools are denied by name
    rules = routine.routine_settings(tools, "", [tools])["permissions"]
    assert "Bash(python _meta/tools/backfill.py *)" in rules["allow"]
    assert "Bash(*_meta/tools/backfill.py*)" not in rules["deny"] and "Bash(*_meta/tools/sign.py*)" in rules["deny"]


def test_the_sweep_chain_reads_its_step_zero_and_the_night_prompt_follows_it(tmp_path):
    ws = make_space(tmp_path / "ada")
    start.render_routines(ws, "Ada Okafor")
    sweep = (ws / "_meta" / "state" / "routines" / "sweep-chain.md").read_text(encoding="utf-8")
    night = (ws / "_meta" / "state" / "routines" / "backfill-night.md").read_text(encoding="utf-8")
    sweep = " ".join(sweep.split())
    assert "backfill.py" in sweep and "due --json" in sweep and "at most 50 records per batch" in sweep
    assert "checkpoint" in sweep and "nothing is due" in sweep and "mark" in sweep
    meta = yaml.safe_load(night.split("---")[1])
    assert meta["schedule"] == "0 1 * * *" and meta["only_while"] == "backfill" and meta["name"] == "backfill-night"
    assert "due --json" in night and "oldest" in night and "sweep-chain.md" in night
    assert "{person}" not in night and "{workspace}" not in night and "{python}" not in night   # rendered


# ----- the phase that plans and starts the history ------------------------------------------------
@pytest.fixture(scope="module", autouse=True)
def _slim_template(slim_template):
    yield slim_template


def history_args(tmp_path, ws, **more):
    given = args(tmp_path, **more)
    given.phase = "history"
    given.workspace = str(ws)
    return given


def history_world(tmp_path, monkeypatch, claude="account", started=None, verdicts=None):
    """A finished setup, a built workspace with its sweep-chain task recorded, the clock stopped, and a
    recorder in place of the scheduler."""
    no_outside(monkeypatch)
    finished_setup(verdicts)
    if claude != "account":
        setup_journal.set_phase("signins", "done", claude=claude)
    ws = make_space(tmp_path / "ada", owner="Ada Okafor", owner_email="ada@lanternworks.example")
    start.render_routines(ws, "Ada Okafor")
    onboard.write_yaml(ws / "_meta" / "state" / "onboard.yaml", {"routine_tasks": [
        {"name": "space-daily-chain-ada", "kind": "schtasks"},
        {"name": "space-sweep-chain-ada", "kind": "schtasks", "log": "sweep-chain-ada"}]})
    monkeypatch.setattr(start, "_now", lambda: AT_01H if started is None else started)
    started_tasks = []
    monkeypatch.setattr(onboard, "run_now", lambda record, dry_run=False: started_tasks.append(record["name"]) or True)
    return ws, started_tasks


READING_TEXT = "five_hour=5,week=26,five_hour_resets=2026-10-25T05:00:00+02:00,week_resets=2026-10-29T06:00:00+02:00,extra=0"


def test_history_plans_from_the_counts_of_the_apps_read_as_work_and_starts_part_one(tmp_path, monkeypatch):
    ws, started = history_world(tmp_path, monkeypatch)
    result = start.phase_history(history_args(tmp_path, ws, reading=READING_TEXT))
    assert result["state"] == "running", result
    assert result["counts"] == {"2026-10": 212 + 40, "2026-09": 187}      # Gmail and Calendar, not the private Outlook
    assert [p["id"] for p in result["parts"]][0] == "p1" and result["current_by"]
    assert started == ["space-sweep-chain-ada"]
    state = backfill.load(ws)
    assert state["setup_reading"]["week_pct"] == 26 and state["reading"]["five_hour_pct"] == 5


def test_history_run_again_changes_nothing_that_is_right(tmp_path, monkeypatch):
    ws, started = history_world(tmp_path, monkeypatch)
    first = start.phase_history(history_args(tmp_path, ws, reading=READING_TEXT))
    backfill.checkpoint(ws, "p1", "mail", "2026-09-02T10:00:00Z")
    again = start.phase_history(history_args(tmp_path, ws))
    assert again["parts"][0]["checkpoints"] == {"mail": "2026-09-02T10:00:00Z"} and again["parts"][0]["id"] == first["parts"][0]["id"]
    assert started == ["space-sweep-chain-ada"]      # part 1 was started once


def test_history_starts_no_part_when_none_is_due_and_says_why(tmp_path, monkeypatch):
    ws, started = history_world(tmp_path, monkeypatch, started=AT_14H)      # working hours, no fresh reading
    result = start.phase_history(history_args(tmp_path, ws))
    assert started == [] and result["state"] == "running" and result["why"]
    assert "tool" not in result["why"] and "backfill" not in result["why"].lower()


def test_a_paid_account_gets_a_plan_but_no_part_starts(tmp_path, monkeypatch):
    ws, started = history_world(tmp_path, monkeypatch, claude="api-key")
    result = start.phase_history(history_args(tmp_path, ws))
    assert result["state"] == "waiting" and result["why"] == start.PAID_WHY
    assert result["parts"] and started == [] and backfill.due(ws, AT_01H) is None
    backfill.start(ws)
    assert backfill.due(ws, AT_01H)["id"] == "p1"


def test_a_usage_reading_of_not_applicable_is_a_paid_account_too(tmp_path, monkeypatch):
    ws, started = history_world(tmp_path, monkeypatch)
    result = start.phase_history(history_args(tmp_path, ws, reading="not_applicable"))
    assert result["state"] == "waiting" and started == [] and backfill.load(ws)["waiting"]


def test_history_with_nothing_to_read_is_done(tmp_path, monkeypatch):
    ws, started = history_world(tmp_path, monkeypatch, verdicts=[OUTLOOK])
    result = start.phase_history(history_args(tmp_path, ws))
    assert result["state"] == "done" and started == [] and not backfill.active(ws)


def test_history_without_a_workspace_fails_in_plain_words(tmp_path, monkeypatch):
    ws, _ = history_world(tmp_path, monkeypatch)
    given = history_args(tmp_path, ws)
    given.workspace = None
    monkeypatch.setattr(start, "LAST_REPORT", None)
    result = start.phase_history(given)
    assert result["state"] == "failed" and "workspace" in result["why"]


def test_history_finds_the_workspace_on_the_machines_list_of_spaces(tmp_path, monkeypatch):
    ws, started = history_world(tmp_path, monkeypatch)
    onboard.remember_space(ws)
    monkeypatch.setattr(start, "LAST_REPORT", None)
    given = history_args(tmp_path, ws)
    given.workspace = None
    assert start.phase_history(given)["state"] == "running" and started == ["space-sweep-chain-ada"]


def test_a_reading_that_is_not_one_is_refused_in_plain_words(tmp_path, monkeypatch):
    ws, started = history_world(tmp_path, monkeypatch)
    result = start.phase_history(history_args(tmp_path, ws, reading="five_hour=lots"))
    assert result["state"] == "failed" and "reading" in result["why"] and started == []


def test_waiting_for_part_one_stops_when_it_is_done_or_paused(tmp_path, monkeypatch):
    ws, started = history_world(tmp_path, monkeypatch)
    start.phase_history(history_args(tmp_path, ws, reading=READING_TEXT))
    clock = {"t": 0.0}
    looks = []

    def sleep(seconds):
        looks.append(seconds)
        clock["t"] += seconds
        if len(looks) == 3:
            backfill.after_run(ws, "p1", {"outcome": "limit", "why": "", "resets_at": None, "usage": {}})
    monkeypatch.setattr(start, "_sleep", sleep)
    monkeypatch.setattr(start, "_clock", lambda: clock["t"])
    result = start.phase_history(history_args(tmp_path, ws, wait_part="p1"))
    assert looks == [30, 30, 30] and result["waited"]["part"] == "p1" and result["waited"]["status"] == "paused"
    assert "didn't say" in result["waited"]["why"] and result["state"] == "running"


def test_waiting_for_part_one_gives_up_after_an_hour(tmp_path, monkeypatch):
    ws, started = history_world(tmp_path, monkeypatch)
    start.phase_history(history_args(tmp_path, ws, reading=READING_TEXT))
    clock = {"t": 0.0}
    monkeypatch.setattr(start, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(start, "_clock", lambda: clock["t"])
    result = start.phase_history(history_args(tmp_path, ws, wait_part="p1"))
    assert clock["t"] == 3600 and result["waited"]["status"] == "pending"


def test_waiting_ends_at_once_when_nothing_can_start(tmp_path, monkeypatch):
    ws, started = history_world(tmp_path, monkeypatch, claude="api-key")
    start.phase_history(history_args(tmp_path, ws))
    monkeypatch.setattr(start, "_sleep", lambda s: pytest.fail("nothing is coming: do not wait"))
    result = start.phase_history(history_args(tmp_path, ws, wait_part="p1"))
    assert result["state"] == "waiting" and result["waited"]["status"] == "pending"


def test_the_history_command_takes_a_reading_and_a_part_to_wait_for():
    got = start.parser().parse_args(["history", "--reading", READING_TEXT, "--wait-part", "p1", "--workspace", "x", "--json"])
    assert got.reading == READING_TEXT and got.wait_part == "p1" and got.workspace == "x" and got.json


# ----- Task 13 fix round 1 (Ruling R38): whose usage a reading is, and a history that runs on in the background ----
LATER = AT_01H + dt.timedelta(minutes=50)


def part_one_ran(ws, limits: dict | None, at: str = "2026-10-24T23:40:00Z", outcome: str = "finished") -> None:
    """Part 1 was read by a routine run whose outcome line holds the windows its own rate_limit_event gave."""
    backfill.checkpoint(ws, "p1", "mail", "2026-08-02T10:00:00Z")
    read_through(ws, "p1", FINISHED, now=AT_01H + dt.timedelta(minutes=40))
    path = ws / "_meta" / "state" / "runs.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"routine": "sweep-chain", "at": at, "space": "ada", "cost_tokens": 0, "outcome": outcome,
                             "notes": {"step": "routine"}, **({"limits": limits} if limits else {})}) + "\n")
    meter_row(ws, routine="sweep-chain", at=at, outcome=outcome, cost_tokens=0, **({"limits": limits} if limits else {}))


SAME = {"five_hour": {"utilization": 0.1, "resets_at": "2026-10-25T03:00:00Z"},      # READING_TEXT's 05:00+02:00
        "seven_day": {"utilization": 0.3, "resets_at": "2026-10-29T04:00:00Z"},      # and its 29 October 06:00+02:00
        "overage_status": "rejected", "using_overage": False}
SECOND = ("five_hour=15,week=27,five_hour_resets=2026-10-25T05:01:30+02:00,week_resets=2026-10-29T05:59:10+02:00,extra=0,"
          "confirmed=0")


def test_a_reading_given_to_history_paces_as_unconfirmed_while_the_account_is_not_known(tmp_path, monkeypatch):
    ws, started = history_world(tmp_path, monkeypatch)
    start.phase_history(history_args(tmp_path, ws, reading=READING_TEXT + ",confirmed=1"))
    state = backfill.load(ws)
    assert state["reading"]["confirmed"] is False and state["setup_reading"]["confirmed"] is False
    assert "usage_account" not in setup_journal.load() and started == ["space-sweep-chain-ada"]


def test_after_part_one_the_same_windows_make_the_desktop_reading_the_routines_own(tmp_path, monkeypatch):
    ws, _ = history_world(tmp_path, monkeypatch)
    start.phase_history(history_args(tmp_path, ws, reading=READING_TEXT))
    part_one_ran(ws, SAME)
    monkeypatch.setattr(start, "_now", lambda: LATER)
    result = start.phase_history(history_args(tmp_path, ws, reading=SECOND))
    assert result["usage_account"] == "same" and setup_journal.load()["usage_account"] == "same"
    kept = backfill.load(ws)["reading"]
    assert kept["five_hour_pct"] == 15 and "confirmed" not in kept                # within two minutes on both windows
    start.phase_history(history_args(tmp_path, ws, reading=SECOND.replace("five_hour=15", "five_hour=20")))
    assert "confirmed" not in backfill.load(ws)["reading"]                       # settled: later readings pace as the routines'


@pytest.mark.parametrize("limits, outcome", [
    ({**SAME, "seven_day": {"utilization": 0.3, "resets_at": "2026-10-27T11:00:00Z"}}, "finished"),   # a week two days apart
    ({**SAME, "five_hour": {"utilization": 0.1, "resets_at": "2026-10-25T03:04:00Z"}}, "finished"),   # 150 seconds apart
    (None, "finished"),                                                          # the run gave no limits
])
def test_after_part_one_other_windows_or_none_stop_the_desktop_readings(tmp_path, monkeypatch, limits, outcome):
    ws, _ = history_world(tmp_path, monkeypatch)
    start.phase_history(history_args(tmp_path, ws, reading=READING_TEXT))
    part_one_ran(ws, limits, outcome=outcome)
    monkeypatch.setattr(start, "_now", lambda: LATER)
    result = start.phase_history(history_args(tmp_path, ws, reading=SECOND))
    assert result["usage_account"] == "different" and setup_journal.load()["usage_account"] == "different"
    assert backfill.load(ws)["reading"]["five_hour_pct"] == 5                    # the desktop's second reading is not kept


def test_nothing_is_decided_before_a_run_read_part_one_or_once_its_window_has_reset(tmp_path, monkeypatch):
    ws, _ = history_world(tmp_path, monkeypatch)
    start.phase_history(history_args(tmp_path, ws, reading=READING_TEXT))
    monkeypatch.setattr(start, "_now", lambda: LATER)
    start.phase_history(history_args(tmp_path, ws, reading=SECOND))             # no run has read part 1 yet
    assert "usage_account" not in setup_journal.load() and backfill.load(ws)["reading"]["confirmed"] is False
    part_one_ran(ws, {**SAME, "five_hour": {"utilization": 0.1, "resets_at": "2026-10-24T23:50:00Z"}})
    start.phase_history(history_args(tmp_path, ws, reading=SECOND))             # its 5-hour window ended before the reading
    assert "usage_account" not in setup_journal.load()


def test_resume_says_the_history_runs_in_the_background_once_part_one_was_started(tmp_path, monkeypatch, capsys):
    ws, started = history_world(tmp_path, monkeypatch)
    setup_journal.set_phase("build", "done")
    assert start.main(["history", "--reading", READING_TEXT, "--workspace", str(ws), "--json"]) == 0
    capsys.readouterr()
    assert started == ["space-sweep-chain-ada"] and setup_journal.load()["history"]["part1_started"]
    assert setup_journal.load()["history"]["in_background"] is True          # the setup counts as finished (R40, N-5)
    assert start.main(["resume", "--workspace", str(ws), "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["next"] is None and got["phases"]["history"]["in_background"] is True
    assert got["phases"]["history"]["part1"]["status"] == "pending"
    part_one_ran(ws, SAME)
    start.main(["resume", "--workspace", str(ws)])
    assert "history: running in the background, part 1 done" in capsys.readouterr().out
    monkeypatch.setattr(start, "_sleep", lambda s: pytest.fail("part 1 is read: no wait"))
    start.main(["history", "--wait-part", "p1", "--workspace", str(ws), "--json"])
    capsys.readouterr()
    assert setup_journal.load()["history"]["part1_waited"] == "done"


def test_a_held_history_is_still_next_on_resume(tmp_path, monkeypatch, capsys):
    ws, started = history_world(tmp_path, monkeypatch, claude="api-key")
    setup_journal.set_phase("build", "done")
    start.main(["history", "--workspace", str(ws), "--json"])
    capsys.readouterr()
    start.main(["resume", "--workspace", str(ws), "--json"])
    got = json.loads(capsys.readouterr().out)
    assert got["next"] == "history" and "in_background" not in got["phases"]["history"] and started == []
    assert "in_background" not in setup_journal.load()["history"]


def test_the_night_prompt_is_rendered_and_scheduled_at_one(tmp_path, monkeypatch):
    no_outside(monkeypatch)
    finished_setup()
    night = [r for r in start.phase_build(args(tmp_path))["routines"] if r["name"] == "backfill-night"]
    assert night and night[0]["schedule"] == "0 1 * * *"


# ===== fix round 1: the review's probes and Ruling R33 ================================================
SPEC_COUNTS = {"2026-07": 400, "2026-08": 1600, "2026-09": 1600, "2026-10": 1200}   # the spec's 4,800 records in 90 days
SEVEN_AM = dt.datetime(2026, 10, 25, 7, 0, tzinfo=TZ)


def guess(ws, which) -> float:
    """Points of the 5-hour window a part costs by the plan's own guess."""
    return backfill._points(backfill.load(ws), which, backfill.DEFAULT_PER_TOKEN_SHARE)


def a_year_ago_plan(tmp_path, name="ws", **more):
    """A plan made long ago, so the setup day and its week rule are over."""
    ws = make_space(tmp_path / name)
    backfill.plan(ws, {"2026-09": 300, "2026-10": 300}, dt.datetime(2026, 10, 20, 14, 0, tzinfo=TZ), **more)
    return ws


# ----- I-1: no part starts that, by the estimate, passes the cap when it starts ---------------------------------
def test_part_one_is_sized_so_that_by_the_guess_it_fits_the_cap_when_it_starts(tmp_path):
    ws = make_space(tmp_path / "ws")
    seen = reading(five_hour_pct=5, week_pct=26, at=AT_14H, five_hour_resets=AT_14H + dt.timedelta(hours=3))
    first = backfill.plan(ws, SPEC_COUNTS, AT_14H, reading=seen)["parts"][0]
    assert guess(ws, first) + 5 <= backfill.DAY_CAP                           # beside the 5% in use, inside the day's half
    assert backfill.due(ws, AT_14H)["id"] == "p1"
    for when in (AT_14H, AT_19H, AT_01H, SEVEN_AM):                           # nothing named passes the cap at its start
        named = backfill.due(ws, when)
        assert named is None or guess(ws, named) <= backfill._cap(when), when


def test_a_plan_without_a_reading_is_sized_for_the_first_night(tmp_path):
    ws = make_space(tmp_path / "ws")
    first = backfill.plan(ws, SPEC_COUNTS, AT_14H)["parts"][0]
    assert backfill.NIGHT_CAP / 2 < guess(ws, first) <= backfill.NIGHT_CAP    # it fills most of a night, not more
    assert backfill.due(ws, AT_14H) is None and backfill.due(ws, AT_19H)["id"] == "p1"


def test_the_twelve_hour_fallback_cuts_what_is_left_by_the_guess(tmp_path):
    ws = make_space(tmp_path / "ws")
    backfill.plan(ws, SPEC_COUNTS, AT_14H)
    read_through(ws, "p1", used(2_000_000), now=AT_14H + dt.timedelta(minutes=50))
    assert backfill.due(ws, AT_01H) is None and backfill.load(ws)["per_token_share"] is None   # 10 hours: not yet
    for when in (SEVEN_AM, dt.datetime(2026, 10, 25, 19, 0, tzinfo=TZ)):
        named = backfill.due(ws, when)
        assert named["id"] == "p2" and guess(ws, named) <= backfill.PART_SHARE
    state = backfill.load(ws)
    assert state["calibration"] == "assumed" and state["per_token_share"] == backfill.DEFAULT_PER_TOKEN_SHARE
    assert len(state["parts"]) > 3 and all(guess(ws, p) <= backfill.PART_SHARE for p in state["parts"][1:])


def test_a_part_bigger_than_any_night_is_not_started_and_says_how_big_it_is(tmp_path):
    ws = planned(tmp_path, per_token_share=0.00002, part_tokens=9_000_000)    # 180 points, over days that can be cut
    assert backfill.due(ws, AT_01H) is None
    assert "180%" in backfill.explain(ws, AT_01H)[1]                          # the true figure, not one clamped to 90


def test_one_day_that_cannot_be_cut_starts_only_at_night(tmp_path):
    ws = make_space(tmp_path / "ws")
    backfill.plan(ws, {"2026-10": 24_000}, AT_14H, window_days=1, first_part_max=10)    # 1,000 records a day: 120 points
    assert [p["id"] for p in parts_of(ws)] == ["p1"] and guess(ws, part(ws, "p1")) > backfill.NIGHT_CAP
    assert backfill.due(ws, AT_19H) is None                                   # no reading to say extra usage is off: it waits
    backfill.record_reading(ws, reading(five_hour_pct=0, at=AT_19H - dt.timedelta(minutes=10)))
    assert backfill.due(ws, AT_19H)["id"] == "p1"                             # a night, with the stop after fifty minutes
    backfill.record_reading(ws, reading(five_hour_pct=0, at=AT_14H))
    assert backfill.due(ws, AT_14H) is None and "120%" in backfill.explain(ws, AT_14H)[1]   # by day: never
    backfill.record_reading(ws, reading(five_hour_pct=95, at=AT_01H))
    assert backfill.due(ws, AT_01H) is None                                   # and not into a window that is nearly used


# ----- I-2: a used-up week is a used-up window when extra usage is on -----------------------------------------
def test_a_used_up_week_with_extra_usage_on_starts_nothing_until_a_reading_shows_room(tmp_path):
    ws = a_year_ago_plan(tmp_path)
    taken = AT_01H - dt.timedelta(minutes=10)
    week_resets = dt.datetime(2026, 10, 28, 6, 0, tzinfo=TZ)
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=100, extra_usage=True, at=taken, week_resets=week_resets))
    assert backfill.due(ws, AT_01H) is None
    assert "week" in backfill.explain(ws, AT_01H)[1] and "extra usage" in backfill.explain(ws, AT_01H)[1]
    assert backfill.due(ws, SEVEN_AM) is None                                 # still before the week resets
    assert backfill.due(ws, dt.datetime(2026, 10, 28, 7, 0, tzinfo=TZ))["id"] == "p1"   # the week has reset
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=60, extra_usage=True, at=SEVEN_AM - dt.timedelta(minutes=5)))
    assert backfill.due(ws, SEVEN_AM)["id"] == "p1"                           # a newer reading with room
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=100, extra_usage=False, at=SEVEN_AM))
    assert backfill.due(ws, SEVEN_AM)["id"] == "p1"                           # without extra usage a limit only stops the run


def test_after_an_overage_that_gave_no_time_the_part_waits_for_a_newer_reading(tmp_path):
    ws = a_year_ago_plan(tmp_path)
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=10, extra_usage=True, at=AT_01H - dt.timedelta(minutes=10)))
    assert backfill.due(ws, AT_01H)["id"] == "p1"
    backfill.after_run(ws, "p1", {"outcome": "overage", "why": "out of extra usage", "resets_at": None, "usage": {}},
                       now=AT_01H + dt.timedelta(minutes=5))
    assert part(ws, "p1")["resets_at"] is None
    assert backfill.due(ws, SEVEN_AM) is None and "reading" in backfill.explain(ws, SEVEN_AM)[1]
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=10, extra_usage=True, at=SEVEN_AM - dt.timedelta(minutes=10)))
    assert backfill.due(ws, SEVEN_AM)["id"] == "p1"                           # a reading taken after the pause shows room


# ----- I-3: the run reads its own part, and a part is done only when a run read it ------------------------------
def test_due_names_the_part_a_live_routine_lock_names(tmp_path):
    ws = planned(tmp_path, per_token_share=0.00002, part_tokens=500_000)
    backfill.record_reading(ws, reading(five_hour_pct=5, at=dt.datetime(2026, 10, 25, 12, 0, 30, tzinfo=TZ)))
    at_lock, at_agent = (dt.datetime(2026, 10, 25, 13, 0, s, tzinfo=TZ) for s in (5, 40))
    assert backfill.due(ws, at_lock)["id"] == "p2" and backfill.due(ws, at_agent) is None   # the reading is 60 minutes old now
    assert routine.take_lock(ws, "sweep-chain", "p2")
    assert backfill.due(ws, at_agent, own=True)["id"] == "p2"                 # the run's own part, whatever the clock says
    assert backfill.due(ws, at_lock) is None                                  # anyone else is given none while it runs
    routine.release_lock(ws)
    assert routine.take_lock(ws, "sweep-chain")                               # a run that started with nothing due
    part_named, why = backfill.explain(ws, at_lock, own=True)
    assert part_named is None and "no part" in why
    routine.release_lock(ws)
    assert backfill.due(ws, at_lock)["id"] == "p2"


@pytest.mark.parametrize("evidence, done", [
    (lambda ws: None, False),
    (lambda ws: backfill.checkpoint(ws, "p1", "mail", "2026-08-02T10:00:00Z"), False),   # not through: I-4
    (lambda ws: backfill.range_read(ws, "p1"), True),
    (lambda ws: (backfill.checkpoint(ws, "p1", "mail", "2026-08-02T10:00:00Z"), backfill.unfinished(ws, "p1")), False)])
def test_a_run_that_read_nothing_does_not_finish_its_part(tmp_path, evidence, done):
    ws = planned(tmp_path)
    backfill.begin(ws, "p1")
    evidence(ws)
    backfill.after_run(ws, "p1", used(30_000))
    p = part(ws, "p1")
    assert (p["status"] == "done") is done and p["tokens"] == 30_000 and "unread" not in p
    if not done:
        assert p["status"] == "paused" and p["resets_at"] is None and backfill.due(ws, AT_19H)["id"] == "p1"
    if not done and not p["checkpoints"]:
        assert "did not read" in p["why"] and p["idle"] == 1 and backfill.export_mark(ws, "mail") == p["from"]


def test_a_part_that_was_read_empty_is_done_and_not_retried_for_ever(tmp_path):
    ws = planned(tmp_path)
    backfill.begin(ws, "p1")
    backfill.range_read(ws, "p1")                                             # every source held nothing in the range
    backfill.after_run(ws, "p1", used(5_000))
    assert status(ws, "p1") == "done" and "idle" not in part(ws, "p1")


def test_the_routine_tells_the_backfill_a_part_began(tmp_path, monkeypatch):
    ws = routine_ws(tmp_path, monkeypatch)
    backfill.plan(ws, COUNTS, NOW)
    monkeypatch.setattr(backfill, "due", lambda workspace, now: backfill.load(workspace)["parts"][0])
    assert run_prompt(ws, "sweep-chain", stream(RESULT)) == 0
    assert status(ws, "p1") == "paused" and "did not read" in part(ws, "p1")["why"]   # the agent never said it read anything

    def reads(argv, **kw):                                                    # an agent that reads, checkpoints and is through
        backfill.checkpoint(ws, "p1", "mail", "2026-08-02T10:00:00Z")
        backfill.range_read(ws, "p1")                                         # it says so: a checkpoint alone is not (R45, I-4)
        return subprocess.CompletedProcess(argv, 0, stream(RESULT), "")
    assert routine.main([str(ws / "_meta" / "state" / "routines" / "sweep-chain.md")], run=reads) == 0
    assert status(ws, "p1") == "done"


# ----- I-4: the hold is made with the plan, and the person's go stays -------------------------------------------
def test_a_plan_is_held_in_the_same_write_that_makes_it_and_the_persons_go_stays(tmp_path):
    ws = make_space(tmp_path / "ws")
    backfill.plan(ws, COUNTS, NOW, hold="Say go and it starts.")
    assert backfill.load(ws)["waiting"] == "Say go and it starts." and backfill.due(ws, AT_01H) is None
    backfill.plan(ws, COUNTS, NOW, hold="Say go and it starts.")              # made again while still held: still held
    assert backfill.load(ws)["waiting"]
    backfill.start(ws)
    state = backfill.load(ws)
    assert state["person_started"] is True and "waiting" not in state and backfill.due(ws, AT_19H)["id"] == "p1"
    backfill.plan(ws, COUNTS, NOW, hold="Say go and it starts.")              # made again after the go: the go is kept
    assert "waiting" not in backfill.load(ws) and backfill.load(ws)["person_started"] is True


def test_going_again_after_the_person_said_go_does_not_hold_the_history_again(tmp_path, monkeypatch):
    ws, started = history_world(tmp_path, monkeypatch, claude="api-key")
    first = start.phase_history(history_args(tmp_path, ws))
    assert first["state"] == "waiting" and backfill.load(ws)["waiting"] and started == []
    backfill.start(ws)                                                        # the person said go
    again = start.phase_history(history_args(tmp_path, ws))
    assert again["state"] == "running" and not backfill.load(ws).get("waiting")
    assert backfill.due(ws, AT_01H)["id"] == "p1" and started == ["space-sweep-chain-ada"]   # saying it again starts it now


# ----- I-5: routines cannot lift the hold or rewrite the pacing -------------------------------------------------
def test_a_routine_may_not_edit_the_backfill_state(tmp_path):
    rules = routine.routine_settings(tmp_path, "", [tmp_path])["permissions"]
    assert "_meta/state/backfill.yaml" in routine.PROTECTED_EDITS and "Edit(//**/_meta/state/backfill.yaml)" in rules["deny"]
    assert "Edit(//**/_meta/tools/**)" in rules["deny"]                       # the tuple itself was not touched


def test_the_persons_verbs_refuse_while_a_routine_run_holds_the_lock(tmp_path):
    ws = planned(tmp_path)
    backfill.hold(ws, "held")
    assert routine.take_lock(ws, "sweep-chain", "p1")                         # a live run: this process
    for words in (("start",), ("reading", "five_hour=1,week=1"), ("calibrate", "--before", "1", "--after", "9"),
                  ("plan", "--counts", "2026-10=5")):
        res = cli(ws, *words)
        assert res.returncode == 2 and "routine run" in res.stderr and "Traceback" not in res.stderr, words
    assert backfill.load(ws)["waiting"] == "held" and len(parts_of(ws)) == 3
    for words in (("checkpoint", "p1", "mail", "2026-08-02T10:00:00Z"), ("read", "p1"), ("unfinished", "p1"),
                  ("due", "--json"), ("mark", "mail"), ("status",)):
        assert cli(ws, *words).returncode == 0, words                         # a run's own verbs stay open
    routine.release_lock(ws)
    assert cli(ws, "start").returncode == 0 and not backfill.load(ws).get("waiting")


# ----- I-6: a held plan does not stop the regular sweep -----------------------------------------------------------
def test_while_the_plan_is_held_the_regular_sweep_exports_from_the_plan_day(tmp_path):
    ws = planned(tmp_path)
    assert backfill.export_mark(ws, "mail") == part(ws, "p1")["from"]
    backfill.hold(ws, "held")
    assert backfill.export_mark(ws, "mail") == "2026-10-24"                   # the day the plan was made
    night = "--now=2026-10-25T01:00:00+02:00"
    assert json.loads(cli(ws, "due", "--json", night).stdout)["held"] is True
    assert json.loads(cli(ws, "mark", "mail", "--json").stdout)["mark"] == "2026-10-24"
    backfill.start(ws)
    assert backfill.export_mark(ws, "mail") == part(ws, "p1")["from"]
    assert json.loads(cli(ws, "due", "--json", night).stdout)["held"] is False


# ----- minors -------------------------------------------------------------------------------------------------------
def test_the_window_cut_at_plan_time_is_an_estimate_that_the_measurement_settles(tmp_path):
    settled = {}
    for name, tokens, after in (("cheap", 20_000_000, 8), ("dear", 2_000_000, 95)):
        ws = make_space(tmp_path / name)
        p = backfill.plan(ws, COUNTS, AT_14H, reading=reading(five_hour_pct=5, week_pct=20))
        assert p["window_days"] < 90 and p["window_estimate"] is True and "estimate" in p["shortened_because"]
        read_through(ws, "p1", used(tokens), now=AT_14H + dt.timedelta(minutes=50))
        backfill.calibrate(ws, 5, after, tokens, now=AT_14H + dt.timedelta(minutes=60))
        settled[name] = backfill.load(ws)
        assert "window_estimate" not in settled[name]
    cheap, dear = settled["cheap"], settled["dear"]
    assert cheap["window_days"] == 90 and "shortened_because" not in cheap          # restored up to the full window
    older = [p for p in cheap["parts"] if p.get("older")]
    assert len(older) == 1 and older[0]["to"] == cheap["parts"][0]["from"] and older[0]["status"] == "pending"
    assert [p["id"] for p in cheap["parts"]] == [f"p{n}" for n in range(1, len(cheap["parts"]) + 1)]
    assert dear["window_days"] < 90 and "estimate" not in dear["shortened_because"] and "first part" in dear["shortened_because"]
    assert not [p for p in dear["parts"] if p.get("older")]


def test_the_hours_the_estimate_counts_on_are_the_hours_the_routines_run(tmp_path):
    ws = make_space(tmp_path / "ada")
    start.render_routines(ws, "Ada Okafor")
    hours = set()
    for name in ("sweep-chain", "backfill-night"):
        meta = yaml.safe_load((ws / "_meta" / "state" / "routines" / f"{name}.md").read_text(encoding="utf-8").split("---")[1])
        hours |= {hour for hour, _ in onboard.daily_times(meta["schedule"])}
    assert tuple(sorted(hours)) == backfill.SLOTS


def test_the_prompts_say_which_records_belong_to_a_part_and_what_an_unreachable_source_does(tmp_path):
    ws = make_space(tmp_path / "ada")
    start.render_routines(ws, "Ada Okafor")
    sweep = (ws / "_meta" / "state" / "routines" / "sweep-chain.md").read_text(encoding="utf-8")
    night = (ws / "_meta" / "state" / "routines" / "backfill-night.md").read_text(encoding="utf-8")
    sweep, night = " ".join(sweep.split()), " ".join(night.split())
    for text in (sweep, night):
        assert "after the checkpoint" in text and "before the day" in text and "local time" in text
        assert "unreachable" in text and "unfinished" in text
    assert re.search(r"backfill\.py \S+ read` for the part", sweep)
    assert "`held`" in sweep and "plan was made" in sweep                       # a held plan exports what is new


def test_waiting_for_part_one_ends_when_the_newest_run_stopped(tmp_path, monkeypatch):
    ws, started = history_world(tmp_path, monkeypatch)
    start.phase_history(history_args(tmp_path, ws, reading=READING_TEXT))
    looks = []

    def sleep(seconds):
        looks.append(seconds)
        assert len(looks) < 6, "it waits on, though the run stopped"
        if len(looks) == 2:                                                   # a run that did not even start
            routine.write_outcome(ws, "sweep-chain", routine._stopped("claude did not start"), AT_01H + dt.timedelta(minutes=2))
    monkeypatch.setattr(start, "_sleep", sleep)
    monkeypatch.setattr(start, "_clock", lambda: 0.0)
    result = start.phase_history(history_args(tmp_path, ws, wait_part="p1"))
    assert looks == [30, 30] and result["waited"]["status"] == "pending" and "stopped" in result["waited"]["why"]


def test_the_paid_hold_speaks_to_the_person(tmp_path, monkeypatch):
    ws, started = history_world(tmp_path, monkeypatch, claude="api-key")
    why = start.phase_history(history_args(tmp_path, ws))["why"]
    assert why == start.PAID_WHY and why.startswith("Your") and "paid account:" not in why
    assert backfill.explain(ws, AT_01H)[1] == why


def test_a_damaged_progress_file_holds_the_history_with_a_plain_line(tmp_path):
    ws = planned(tmp_path)
    path = ws / "_meta" / "state" / "backfill.yaml"
    path.write_text("parts: [unclosed", encoding="utf-8")
    assert backfill.active(ws) and backfill.due(ws, AT_01H) is None
    why = backfill.explain(ws, AT_01H)[1]
    assert "could not be read" in why and "history" in why
    mark = backfill.export_mark(ws, "mail")
    assert mark is not None and not mark.startswith(("2026-07", "2026-08"))   # no unpaced export of the rest of the history
    for verb in (lambda: backfill.checkpoint(ws, "p1", "mail", "2026-08-02T10:00:00Z"),
                 lambda: backfill.record_reading(ws, reading(five_hour_pct=5)), lambda: backfill.after_run(ws, "p1", FINISHED),
                 lambda: backfill.calibrate(ws, 0, 10, 500_000), lambda: backfill.range_read(ws, "p1")):
        with pytest.raises(ValueError):
            verb()
    assert path.read_text(encoding="utf-8") == "parts: [unclosed" and not list(path.parent.glob("backfill.yaml.bad-*"))
    backfill.plan(ws, COUNTS, NOW)                                            # starting over is a choice, and keeps the old file
    assert list(path.parent.glob("backfill.yaml.bad-*")) and not backfill.load(ws).get("broken")


def test_history_with_a_damaged_progress_file_waits_instead_of_planning_over_it(tmp_path, monkeypatch):
    ws, started = history_world(tmp_path, monkeypatch)
    (ws / "_meta" / "state").mkdir(parents=True, exist_ok=True)
    (ws / "_meta" / "state" / "backfill.yaml").write_text("parts: [unclosed", encoding="utf-8")
    result = start.phase_history(history_args(tmp_path, ws))
    assert result["state"] == "waiting" and "could not be read" in result["why"] and started == []
    assert not list((ws / "_meta" / "state").glob("backfill.yaml.bad-*"))


def test_the_plan_is_made_again_while_no_part_has_run_when_the_counts_change(tmp_path, monkeypatch):
    ws, started = history_world(tmp_path, monkeypatch, verdicts=[OUTLOOK])    # nothing counted as work yet
    assert start.phase_history(history_args(tmp_path, ws))["state"] == "done" and not backfill.active(ws)
    finished_setup()                                                          # the apps are counted
    result = start.phase_history(history_args(tmp_path, ws, reading=READING_TEXT))
    assert result["state"] == "running" and started == ["space-sweep-chain-ada"] and backfill.active(ws)
    assert backfill.load(ws)["counts"] == {"2026-10": 252, "2026-09": 187}
    backfill.checkpoint(ws, "p1", "mail", "2026-09-02T10:00:00Z")
    finished_setup([dict(GMAIL, per_month={"2026-10": 9999})])
    again = start.phase_history(history_args(tmp_path, ws))                   # a part has run: the plan stays
    assert again["parts"][0]["checkpoints"] == {"mail": "2026-09-02T10:00:00Z"} and backfill.load(ws)["counts"]["2026-10"] == 252


def test_a_swallowed_error_still_gives_a_plain_why(tmp_path, monkeypatch):
    ws = routine_ws(tmp_path, monkeypatch)
    backfill.plan(ws, COUNTS, NOW)
    monkeypatch.setattr(backfill, "due", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    part_id, why = routine.next_part(ws, "sweep-chain")
    assert part_id is None and why and "boom" not in why and "Traceback" not in why


# ===== the port onto Task 10's fixes (Ruling R30): the Bash guard, the outcome on a fault ======================
ROUTINE_VERBS = {"due", "mark", "checkpoint", "read", "unfinished"}   # what a run says; the person's four are not here


def test_the_prompts_spell_out_only_the_verbs_a_run_may_say_and_each_command_passes_the_bash_guard(tmp_path):
    import bash_guard
    ws = make_space(tmp_path / "ada")
    start.render_routines(ws, "Ada Okafor")
    seen = set()
    for prompt in sorted((ws / "_meta" / "state" / "routines").glob("*.md")):
        body = routine.split_prompt(prompt.read_text(encoding="utf-8"))[1]
        flat = " ".join(body.split())
        seen |= set(re.findall(r"backfill\.py \S+ (\w+)", flat))
        settings = tmp_path / f"{prompt.stem}.settings.json"
        data = routine.routine_settings(ws, body, [ws], "ada@lanternworks.example", profile=settings)
        settings.write_text(json.dumps(data), encoding="utf-8")
        words = {"due": "due --json", "mark": "mark mail", "checkpoint": "checkpoint p1 mail 2026-08-02T10:00:00Z",
                 "read": "read p1", "unfinished": "unfinished p1"}
        py = routine.interpreters(body, [ws])[0]
        for verb in re.findall(r"backfill\.py \S+ (\w+)", flat):
            command = f"{py} {ws.resolve().as_posix()}/_meta/tools/backfill.py {ws.resolve().as_posix()} {words[verb]}"
            event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
            assert bash_guard.check(settings, event) is None, (prompt.name, command)
    assert seen == ROUTINE_VERBS                                              # and none of start, plan, reading, calibrate


def test_a_fault_inside_a_run_leaves_its_part_begun_and_not_done(tmp_path, monkeypatch):
    ws = routine_ws(tmp_path, monkeypatch)
    backfill.plan(ws, COUNTS, NOW)
    monkeypatch.setattr(backfill, "due", lambda workspace, now: backfill.load(workspace)["parts"][0])
    monkeypatch.setattr(routine, "write_settings", lambda *a, **k: (_ for _ in ()).throw(OSError(28, "No space left")))
    assert run_prompt(ws, "sweep-chain", stream(RESULT)) == 1
    p = part(ws, "p1")
    assert p["status"] == "pending" and "unread" not in p and [r["outcome"] for r in p["runs"]] == ["stopped"]
    assert runs_of(ws)[-1]["outcome"] == "stopped" and not routine.lock_path(ws).exists()
    assert backfill.explain(ws, AT_19H)[0]["id"] == "p1"                      # the next run reads it


def test_a_fault_after_the_outcome_was_written_tells_the_part_once(tmp_path, monkeypatch):
    ws = routine_ws(tmp_path, monkeypatch)
    backfill.plan(ws, COUNTS, NOW)
    monkeypatch.setattr(backfill, "due", lambda workspace, now: backfill.load(workspace)["parts"][0])
    monkeypatch.setattr(routine, "log_summary", lambda res: 1 / 0)
    seen = {}

    def reads(argv, **kw):
        backfill.checkpoint(ws, "p1", "mail", "2026-08-02T10:00:00Z")
        backfill.range_read(ws, "p1")
        return subprocess.CompletedProcess(argv, 0, stream(RESULT), "")
    assert routine.main([str(ws / "_meta" / "state" / "routines" / "sweep-chain.md")], run=reads) == 1
    assert status(ws, "p1") == "done" and [r["outcome"] for r in part(ws, "p1")["runs"]] == ["finished"]
    assert [r["outcome"] for r in runs_of(ws)] == ["finished"] and not routine.lock_path(ws).exists()


def runs_of(ws) -> list:
    path = ws / "_meta" / "state" / "runs.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ===== fix round 2: the re-review's probes 5 to 8 and N-1 to N-9 ===============================================
def one_big_day(tmp_path, name="lone"):
    """A single day of 1,200 records (144 points by the guess) that cannot be cut, planned on 20 October."""
    ws = make_space(tmp_path / name)
    backfill.plan(ws, {"2026-10": 24_000}, dt.datetime(2026, 10, 20, 14, 0, tzinfo=TZ), window_days=1, first_part_max=10)
    return ws


def test_one_day_that_cannot_be_cut_is_not_read_while_extra_usage_is_on_or_unknown(tmp_path):    # N-1, probe5 A
    ws = one_big_day(tmp_path)
    assert part(ws, "p1")["from"] == "2026-10-19" and guess(ws, part(ws, "p1")) > backfill.NIGHT_CAP
    assert backfill.due(ws, AT_01H) is None                                                       # no reading at all
    assert "Oct 19 alone is bigger than one window of your limits" in backfill.explain(ws, AT_01H)[1]
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=10, extra_usage=True, at=AT_01H - dt.timedelta(minutes=10)))
    assert backfill.due(ws, AT_01H) is None
    assert backfill.explain(ws, AT_01H)[1] == ("Oct 19 alone is bigger than one window of your limits; with extra usage on, "
                                               "reading it could cost extra. Turn extra usage off and it is read at night.")
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=10, extra_usage=False, at=AT_01H - dt.timedelta(minutes=5)))
    assert backfill.due(ws, AT_01H)["id"] == "p1"                                                 # the latest reading says it is off
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=10, extra_usage=True, at=AT_01H))
    assert backfill.due(ws, AT_01H) is None


def test_with_extra_usage_on_a_part_that_takes_the_week_past_the_end_waits(tmp_path):            # N-2, probe5 B
    ws = make_space(tmp_path / "week")
    backfill.plan(ws, {"2026-09": 3000, "2026-10": 3000}, dt.datetime(2026, 10, 20, 14, 0, tzinfo=TZ))
    read_through(ws, "p1", used(500_000), now=dt.datetime(2026, 10, 20, 15, 0, tzinfo=TZ))
    backfill.calibrate(ws, 0, 10, 500_000, now=dt.datetime(2026, 10, 20, 15, 5, tzinfo=TZ))
    week_resets = dt.datetime(2026, 10, 28, 6, 0, tzinfo=TZ)

    def with_week(week, extra):
        backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=week, extra_usage=extra,
                                            at=AT_01H - dt.timedelta(minutes=10), week_resets=week_resets))
        return backfill.explain(ws, AT_01H)
    named, _ = with_week(96, True)
    state = backfill.load(ws)
    share = backfill._tokens_left(state, named) * backfill._week_rate(state)
    assert named is not None and 96 + share <= 100                                                # it fits the week: due
    named, why = with_week(99, True)
    assert named is None and "week" in why and "extra usage" in why                               # past 100%: waits
    assert with_week(99, False)[0] is not None                                                    # without extra usage a limit only stops the run
    assert with_week(100, True)[0] is None


# ----- N-3 and N-5: whom the lock's part is served to, and the reason a part-less run reads --------------------
def test_only_the_run_that_holds_the_lock_gets_its_part_and_another_gets_none(tmp_path):         # N-3, probe6
    ws = a_year_ago_plan(tmp_path)
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=10, extra_usage=True, at=AT_01H - dt.timedelta(minutes=10)))
    assert routine.take_lock(ws, "backfill-night", "p1")                                          # run A
    backfill.begin(ws, "p1")
    backfill.after_run(ws, "p1", {"outcome": "overage", "why": "x", "resets_at": None, "usage": {}},
                       now=AT_01H + dt.timedelta(minutes=30))
    later = AT_01H + dt.timedelta(minutes=30, seconds=1)
    assert routine.next_part(ws, "sweep-chain", later)[0] is None                                 # run B starting now
    got, why = backfill.explain(ws, later)
    assert got is None and "routine run is going" in why
    assert backfill.explain(ws, later, own=True)[0]["id"] == "p1"                                 # A's agent asks as the holder
    assert json.loads(cli(ws, "due", "--json", "--now=2026-10-25T01:30:01+02:00").stdout)["part"]["id"] == "p1"
    routine.release_lock(ws)
    assert routine.next_part(ws, "sweep-chain", later)[0] is None                                 # the overage rule holds it now
    assert "reading" in routine.next_part(ws, "sweep-chain", later)[1]


def test_a_run_that_started_with_no_part_reads_the_real_reason(tmp_path):                       # N-5, probe6
    ws = make_space(tmp_path / "why")
    backfill.plan(ws, {"2026-10": 100}, dt.datetime(2026, 10, 20, 14, 0, tzinfo=TZ))              # a small part: due by day
    noon = dt.datetime(2026, 10, 25, 13, 0, tzinfo=TZ)
    real = backfill.explain(ws, noon)[1]
    assert "reading of your usage from the last hour" in real
    assert routine.take_lock(ws, "sweep-chain")                                                   # started with no part
    assert backfill.explain(ws, noon, own=True) == (None, real)
    backfill.record_reading(ws, reading(five_hour_pct=0, at=noon - dt.timedelta(minutes=5)))      # now a part would be due...
    assert backfill.explain(ws, noon, own=True) == (None, backfill.NO_PART_WHY)                   # ...but not for this run
    routine.release_lock(ws)
    assert backfill.due(ws, noon)["id"] == "p1"


# ----- N-4: a part is never finished without a begin ------------------------------------------------------------
def test_when_begin_fails_the_run_goes_on_without_its_part(tmp_path, monkeypatch):
    ws = routine_ws(tmp_path, monkeypatch)
    backfill.plan(ws, COUNTS, NOW)
    monkeypatch.setattr(backfill, "due", lambda workspace, now: backfill.load(workspace)["parts"][0])
    monkeypatch.setattr(backfill, "begin", lambda *a: (_ for _ in ()).throw(PermissionError(13, "in use")))

    def reads(argv, **kw):                                                                        # an agent that reads and says so
        backfill.checkpoint(ws, "p1", "mail", "2026-08-02T10:00:00Z")
        backfill.range_read(ws, "p1")
        return subprocess.CompletedProcess(argv, 0, stream(RESULT), "")
    assert routine.main([str(ws / "_meta" / "state" / "routines" / "sweep-chain.md")], run=reads) == 0
    p = part(ws, "p1")
    assert p["status"] == "pending" and "runs" not in p                                           # never done without a begin
    assert p["checkpoints"] == {"mail": "2026-08-02T10:00:00Z"}                                   # what it read is kept
    log = (tmp_path / "_home" / ".spaces" / "logs" / "sweep-chain-ada.log").read_text(encoding="utf-8")
    assert "could not be marked as begun" in log


# ----- N-6: what the message promises is true ---------------------------------------------------------------------
def test_a_part_a_little_too_big_for_the_day_waits_for_the_night(tmp_path):                     # N-6, probe8
    ws = make_space(tmp_path / "ws")
    backfill.plan(ws, SPEC_COUNTS, AT_14H)
    read_through(ws, "p1", used(4_500_000), now=AT_14H + dt.timedelta(minutes=50))
    backfill.due(ws, SEVEN_AM)                                                                    # the fallback cuts
    noon = dt.datetime(2026, 10, 25, 13, 0, tzinfo=TZ)
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=10, at=noon - dt.timedelta(minutes=5)))
    named, why = backfill.explain(ws, noon)
    assert named is None and "waits for the night" in why and "cut smaller" not in why
    assert backfill.due(ws, dt.datetime(2026, 10, 25, 19, 0, tzinfo=TZ))["id"] == "p2"


# ----- N-7: the fallback settles the window ------------------------------------------------------------------------
def test_the_fallback_settles_the_window_it_cut_on_a_guess(tmp_path):
    ws = make_space(tmp_path / "ws")
    seen = reading(five_hour_pct=5, week_pct=26, at=AT_14H, five_hour_resets=AT_14H + dt.timedelta(hours=3))
    first = backfill.plan(ws, SPEC_COUNTS, AT_14H, reading=seen)
    assert first["window_estimate"] is True and "estimate" in first["shortened_because"]
    read_through(ws, "p1", used(2_500_000), now=AT_14H + dt.timedelta(minutes=50))
    assert backfill.due(ws, dt.datetime(2026, 10, 25, 7, 0, tzinfo=TZ)) is not None               # the 12-hour fallback
    state = backfill.load(ws)
    assert state["calibration"] == "assumed" and "window_estimate" not in state
    assert "estimate" not in state["shortened_because"] and "checked when" not in state["shortened_because"]
    assert "guess" in state["shortened_because"] and "Measured" not in state["shortened_because"]
    # a calibration that measured nothing is the same: the text does not say it was measured
    other = make_space(tmp_path / "other")
    backfill.plan(other, SPEC_COUNTS, AT_14H, reading=seen)
    read_through(other, "p1", used(2_500_000), now=AT_14H + dt.timedelta(minutes=50))
    backfill.calibrate(other, 5, 6, 2_500_000, now=AT_14H + dt.timedelta(minutes=55))
    text = backfill.load(other)["shortened_because"]
    assert "guess" in text and "Measured" not in text and "estimate" not in text


# ----- N-8 and N-9: a plan made over a damaged file, and a plan made again --------------------------------------
def test_a_plan_made_over_a_damaged_file_is_held_until_the_person_says_go(tmp_path):
    ws = planned(tmp_path)
    (ws / "_meta" / "state" / "backfill.yaml").write_text("parts: [unclosed", encoding="utf-8")
    made = cli(ws, "plan", "--counts", "2026-09=1500,2026-10=1400", "--json", "--now=2026-10-24T14:00:00+02:00")
    assert made.returncode == 0 and json.loads(made.stdout)["waiting"]
    assert backfill.load(ws)["waiting"] and backfill.due(ws, AT_19H) is None and "say go" in backfill.explain(ws, AT_19H)[1]
    assert cli(ws, "start").returncode == 0 and backfill.due(ws, AT_19H)["id"] == "p1"


def test_a_plan_made_again_without_a_reading_uses_the_one_it_kept(tmp_path):
    ws = make_space(tmp_path / "ws")
    first = backfill.plan(ws, SPEC_COUNTS, AT_14H, reading=reading(five_hour_pct=5, week_pct=26, at=AT_14H))
    assert first["window_days"] < 90
    again = backfill.plan(ws, SPEC_COUNTS, AT_14H)                                                # made again, no reading given
    assert again["window_days"] == first["window_days"] and again["window_estimate"] is True
    assert "estimate" in again["shortened_because"] and again["reading"] == first["reading"]


# ===== fix round 2, follow-ups =====================================================================================
def test_a_run_may_say_nothing_about_a_part_its_lock_does_not_name(tmp_path):
    ws = planned(tmp_path)
    assert routine.take_lock(ws, "sweep-chain", "p1")
    for words in (("checkpoint", "p2", "mail", "2026-09-01T00:00:00Z"), ("read", "p2"), ("unfinished", "p2")):
        res = cli(ws, *words)
        assert res.returncode == 2 and "reads p1" in res.stderr and "p2" in res.stderr, words
        assert "Traceback" not in res.stderr
    assert part(ws, "p2")["checkpoints"] == {} and "unfinished" not in part(ws, "p2")       # nothing was written
    for words in (("checkpoint", "p1", "mail", "2026-08-02T10:00:00Z"), ("read", "p1"), ("unfinished", "p1")):
        assert cli(ws, *words).returncode == 0, words                                       # its own part stays open
    routine.release_lock(ws)
    assert routine.take_lock(ws, "sweep-chain")                                             # a run that started with none
    res = cli(ws, "checkpoint", "p1", "mail", "2026-08-03T10:00:00Z")
    assert res.returncode == 2 and "no part" in res.stderr
    routine.release_lock(ws)
    assert cli(ws, "checkpoint", "p2", "mail", "2026-09-01T00:00:00Z").returncode == 0     # a session, with no run going


def test_a_parts_entry_that_is_not_a_list_reads_as_damaged_and_never_as_no_backfill(tmp_path):
    ws = planned(tmp_path)
    path = ws / "_meta" / "state" / "backfill.yaml"
    for broken in ("parts: 5\nstarted: 2026-10-24T14:00:00+02:00\n", "parts:\nstarted: x\n", "parts: {p1: done}\n"):
        path.write_text(broken, encoding="utf-8")
        assert backfill.load(ws) == {"broken": True} and backfill.active(ws)
        assert backfill.due(ws, AT_01H) is None and "could not be read" in backfill.explain(ws, AT_01H)[1]
        assert backfill.export_mark(ws, "mail") is not None
    path.write_text("started: 2026-10-24T14:00:00+02:00\nreading: {five_hour_pct: 5}\n", encoding="utf-8")   # no parts: no plan yet
    assert "broken" not in backfill.load(ws) and not backfill.active(ws)


# ===== Task 13 fix round 1 (Ruling R38): which account a reading is from, and one calibration =======================
def limits_row(ws, at: str, five_hour=0.0, week=0.1, overage="rejected", using=False, tokens=0, **more) -> None:
    """A routine's outcome line with the windows its own rate_limit_event gave (`routine.limits_of`)."""
    limits = {"five_hour": {"utilization": five_hour, "resets_at": more.get("five_hour_resets", "2026-10-25T03:00:00Z")},
              "seven_day": {"utilization": week, "resets_at": more.get("week_resets", "2026-10-29T04:00:00Z")},
              "overage_status": overage, "using_overage": using}
    path = ws / "_meta" / "state" / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"routine": "sweep-chain", "at": at, "space": "lone", "cost_tokens": tokens, "outcome": "finished",
                             "notes": {"step": "routine"}, **({"limits": limits} if more.get("with_limits", True) else {})}) + "\n")
    meter_row(ws, routine="sweep-chain", at=at, outcome="finished", cost_tokens=tokens,
              **({"limits": limits} if more.get("with_limits", True) else {}))


def test_a_reading_says_whether_it_is_confirmed_and_one_that_is_not_counts_as_extra_usage_on(tmp_path):
    given = backfill.parse_reading("five_hour=0,week=10,extra=0,confirmed=0", AT_01H)
    assert given["confirmed"] is False and given["extra_usage"] is False
    assert "confirmed" not in backfill.parse_reading("five_hour=0,week=10,extra=0,confirmed=1", AT_01H)
    assert "confirmed" not in backfill.parse_reading("five_hour=0,week=10,extra=0", AT_01H)
    ws = one_big_day(tmp_path)
    taken = AT_01H - dt.timedelta(minutes=10)
    backfill.record_reading(ws, {**reading(five_hour_pct=0, week_pct=10, at=taken), "confirmed": False})
    assert backfill.due(ws, AT_01H) is None                                   # the desktop's reading may be another account's
    assert "with extra usage on" not in backfill.explain(ws, AT_01H)[1]       # it does not say extra usage is on: it may be
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=10, at=taken + dt.timedelta(minutes=1)))
    assert backfill.due(ws, AT_01H)["id"] == "p1"                             # the same reading, confirmed: the night is free


def test_an_unconfirmed_reading_keeps_a_part_that_would_pass_the_week_from_starting(tmp_path):
    ws = a_year_ago_plan(tmp_path)
    taken = AT_01H - dt.timedelta(minutes=10)
    backfill.record_reading(ws, {**reading(five_hour_pct=0, week_pct=100, at=taken), "confirmed": False})
    assert backfill.due(ws, AT_01H) is None and "extra usage may be on" in backfill.explain(ws, AT_01H)[1]
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=100, at=taken + dt.timedelta(minutes=1)))
    assert backfill.due(ws, AT_01H)["id"] == "p1"                             # confirmed, extra off: a limit only stops the run


def test_the_newest_routine_outcome_with_limits_is_a_reading_of_the_routines_own_account(tmp_path):
    ws = one_big_day(tmp_path)
    assert backfill.due(ws, AT_01H) is None                                   # no reading at all
    limits_row(ws, "2026-10-24T22:50:00Z")                                    # 00:50 local: extra usage refused, 0% in use
    got = backfill.routine_reading(ws, AT_01H)
    assert got["five_hour_pct"] == 0 and got["week_pct"] == 10 and got["extra_usage"] is False and "confirmed" not in got
    assert got["five_hour_resets"] == "2026-10-25T03:00:00Z" and backfill._dt(got["at"]) < backfill._dt("2026-10-24T22:50:00Z")
    assert backfill.due(ws, AT_01H)["id"] == "p1"
    limits_row(ws, "2026-10-24T22:55:00Z", overage="allowed")                 # the newest says extra usage may be used
    assert backfill.due(ws, AT_01H) is None
    limits_row(ws, "2026-10-24T22:56:00Z", five_hour=0.95)                    # a fraction is a share: 95% in use
    assert backfill.routine_reading(ws, AT_01H)["five_hour_pct"] == 95 and backfill.due(ws, AT_01H) is None
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=10, at=AT_01H - dt.timedelta(minutes=2)))
    assert backfill.due(ws, AT_01H)["id"] == "p1"                             # a newer confirmed reading than the routine's wins
    limits_row(ws, "2026-10-24T22:59:30Z", with_limits=False)                 # a run that printed no limits says nothing
    assert backfill.routine_reading(ws, AT_01H)["five_hour_pct"] == 95


def test_calibrate_refuses_a_second_time_in_plain_words(tmp_path):
    ws = planned(tmp_path, per_token_share=0.00002)
    before = backfill.load(ws)
    with pytest.raises(ValueError, match="measured already"):
        backfill.calibrate(ws, 3, 4, 500_000)
    assert backfill.load(ws) == before
    res = cli(ws, "calibrate", "--before", "3", "--after", "4")
    assert res.returncode == 2 and "measured already" in res.stderr and "Traceback" not in res.stderr
    guessed = make_space(tmp_path / "guessed")
    backfill.plan(guessed, SPEC_COUNTS, AT_14H)
    read_through(guessed, "p1", used(2_000_000), now=AT_14H + dt.timedelta(minutes=50))
    backfill.due(guessed, SEVEN_AM)                                           # twelve hours on: the guess took over
    with pytest.raises(ValueError, match="measured already"):
        backfill.calibrate(guessed, 3, 13, 500_000)


# ===== Task 13 fix round 2 (Ruling R40): a meter no agent writes, and a reading confirmed only by setup =============
FORGED = {"five_hour": {"utilization": 0, "resets_at": "2099-01-01T00:00:00Z"},
          "seven_day": {"utilization": 0, "resets_at": "2099-01-01T00:00:00Z"},
          "overage_status": "rejected", "using_overage": False}


def agent_line(ws, at: str, tokens: int = 0) -> None:
    """What a routine's agent can append to runs.jsonl with Edit or Write: a line shaped like routine.py's own."""
    path = ws / "_meta" / "state" / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"routine": "sweep-chain", "at": at, "space": "lone", "cost_tokens": tokens,
                             "outcome": "finished", "notes": {"step": "routine"}, "limits": FORGED}) + "\n")


def test_a_run_line_an_agent_could_write_never_paces_the_history(tmp_path):
    """Probe P1: the person's reading says extra usage is on, so R36 keeps the lone day from starting. Run lines that
    claim no use and extra usage refused, one of them dated far ahead, change nothing: the pacing reads only what
    routine.py itself kept (R40, N-1)."""
    ws = one_big_day(tmp_path)
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=10, extra_usage=True, at=AT_01H - dt.timedelta(minutes=10)))
    assert backfill.due(ws, AT_01H) is None
    agent_line(ws, "2099-01-01T00:00:00Z")
    agent_line(ws, "2026-10-24T22:55:00Z")
    assert backfill.routine_reading(ws, AT_01H) is None and backfill.due(ws, AT_01H) is None


def test_a_meter_row_dated_more_than_five_minutes_ahead_is_not_read(tmp_path):
    ws = one_big_day(tmp_path)
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=10, extra_usage=True, at=AT_01H - dt.timedelta(minutes=10)))
    limits_row(ws, "2099-01-01T00:00:00Z")                                    # 0% in use, extra usage refused
    limits_row(ws, "2026-10-24T23:06:00Z")                                    # six minutes after 01:00 local
    assert backfill.routine_reading(ws, AT_01H) is None and backfill.due(ws, AT_01H) is None
    limits_row(ws, "2026-10-24T23:04:00Z")                                    # four: a clock a little apart
    assert backfill.routine_reading(ws, AT_01H)["extra_usage"] is False and backfill.due(ws, AT_01H)["id"] == "p1"


def test_only_the_tokens_routine_py_kept_count_as_used(tmp_path):
    ws = planned(tmp_path, per_token_share=0.00002, part_tokens=500_000)     # 10 points a part
    backfill.record_reading(ws, reading(five_hour_pct=75, at=AT_01H - dt.timedelta(minutes=30),
                                        five_hour_resets=AT_01H + dt.timedelta(hours=4)))
    agent_line(ws, "2026-10-24T22:45:00Z", tokens=-5_000_000)                 # an agent's line lowers nothing
    agent_line(ws, "2026-10-24T22:46:00Z", tokens=500_000)                    # and adds nothing
    assert backfill.due(ws, AT_01H)["id"] == "p2"
    meter_row(ws, routine="sweep-chain", at="2026-10-24T22:45:00Z", outcome="finished", cost_tokens=500_000)
    assert backfill.due(ws, AT_01H) is None                                   # 75 + 10 + 10 > 90


def test_the_reading_command_keeps_a_reading_unconfirmed_until_setup_found_the_same_account(tmp_path):
    """`backfill.py reading` confirms a reading only when the setup journal says the app's usage is the routines'
    account (`usage_account: same`); otherwise it keeps it unconfirmed and says why in plain words (R40, N-2)."""
    ws = one_big_day(tmp_path)
    phrase = "five_hour=0,week=10,five_hour_resets=2026-10-25T03:00:00Z,week_resets=2026-10-29T04:00:00Z,extra=0"
    now = "--now=2026-10-25T00:50:00+02:00"
    done = cli(ws, "reading", phrase, now)
    assert done.returncode == 0 and backfill.load(ws)["reading"]["confirmed"] is False
    assert done.stdout.strip() == backfill.UNKNOWN_ACCOUNT_WHY and "usage_account" not in done.stdout
    setup_journal.save({"usage_account": "different"})
    done = cli(ws, "reading", phrase, now)
    assert backfill.load(ws)["reading"]["confirmed"] is False and done.stdout.strip() == backfill.OTHER_ACCOUNT_WHY
    setup_journal.save({"usage_account": "same"})
    done = cli(ws, "reading", phrase, now)
    assert done.returncode == 0 and "confirmed" not in backfill.load(ws)["reading"] and done.stdout.strip() == ""
    cli(ws, "reading", phrase + ",confirmed=0", now)
    assert backfill.load(ws)["reading"]["confirmed"] is False


def test_a_history_found_going_on_resume_is_marked_in_the_background(tmp_path, monkeypatch, capsys):
    """Part 1 started by a routine run rather than by the history phase: the first resume that sees it going marks
    the history `in_background`, so the setup counts as finished from then on (R40, N-5)."""
    ws, _ = history_world(tmp_path, monkeypatch)
    start.main(["history", "--reading", READING_TEXT, "--workspace", str(ws), "--json"])
    capsys.readouterr()
    setup_journal.set_phase("build", "done")
    data = setup_journal.load()
    data["history"] = {k: v for k, v in data["history"].items() if k not in ("in_background", "part1_started")}
    setup_journal.save(data)
    backfill.checkpoint(ws, "p1", "mail", "2026-08-02T10:00:00Z")
    assert start.main(["resume", "--workspace", str(ws), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["next"] is None and setup_journal.load()["history"]["in_background"] is True


def test_whose_usage_the_app_reads_is_decided_from_the_meter_alone(tmp_path, monkeypatch):
    """A run line with the same windows, which an agent could write, decides nothing; nor does a meter row dated
    far ahead (R40, N-1)."""
    ws, _ = history_world(tmp_path, monkeypatch)
    start.phase_history(history_args(tmp_path, ws, reading=READING_TEXT))
    path = ws / "_meta" / "state" / "runs.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"routine": "sweep-chain", "at": "2026-10-24T23:40:00Z", "space": "ada", "cost_tokens": 0,
                             "outcome": "finished", "notes": {"step": "routine"}, "limits": SAME}) + "\n")
    monkeypatch.setattr(start, "_now", lambda: LATER)
    start.phase_history(history_args(tmp_path, ws, reading=SECOND))
    assert "usage_account" not in setup_journal.load()
    meter_row(ws, routine="sweep-chain", at="2099-01-01T00:00:00Z", outcome="finished", cost_tokens=0, limits=SAME)
    start.phase_history(history_args(tmp_path, ws, reading=SECOND))
    assert "usage_account" not in setup_journal.load()
    meter_row(ws, routine="sweep-chain", at="2026-10-24T23:40:00Z", outcome="finished", cost_tokens=0, limits=SAME)
    assert start.phase_history(history_args(tmp_path, ws, reading=SECOND))["usage_account"] == "same"


# ===== fix round 3 (Ruling R43): a reading dated ahead, and a routine run in one space says nothing about another =====
AHEAD = "five_hour=0,week=0,five_hour_resets=2099-01-01T00:00:00Z,week_resets=2099-01-01T00:00:00Z,extra=0"
NIGHT = "--now=2026-10-25T01:00:00+02:00"


def test_a_reading_dated_ahead_is_refused_and_one_kept_anyway_is_never_read(tmp_path, capsys):
    """Probe Q1a: a reading dated 2099 with extra usage off would stay the newest for good and open the lone day that
    R36 holds. The commands refuse a reading dated more than FUTURE_SLACK after their now, as the meter does; one kept
    anyway (dated by a clock the caller chose) is not read, so the routines' own newer row decides (R43, N2-1 a)."""
    ws = one_big_day(tmp_path)
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=10, extra_usage=True, at=AT_01H - dt.timedelta(minutes=10)))
    setup_journal.save({"usage_account": "same"})
    kept = backfill.load(ws)["reading"]
    for at in ("2099-01-01T00:00:00Z", "2026-10-24T23:06:00Z"):                # six minutes after 01:00 local
        assert backfill.main([str(ws), "reading", f"{AHEAD},at={at}", NIGHT]) == 2, at
        err = capsys.readouterr().err
        assert backfill.AHEAD_WHY in err and "Traceback" not in err
    assert backfill.load(ws)["reading"] == kept and backfill.due(ws, AT_01H) is None
    other = make_space(tmp_path / "other")
    assert backfill.main([str(other), "plan", "--counts", "2026-10=1400", "--reading", f"{AHEAD},at=2099-01-01T00:00:00Z",
                          NIGHT]) == 2
    assert backfill.AHEAD_WHY in capsys.readouterr().err and not backfill.load(other).get("parts")
    assert backfill.main([str(ws), "reading", AHEAD, "--now=2099-01-01T00:00:00Z"]) == 2          # a clock of its own: refused (R47)
    assert backfill.NOW_AHEAD_WHY in capsys.readouterr().err and backfill.load(ws)["reading"] == kept
    backfill.record_reading(ws, backfill.parse_reading(AHEAD, dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc)))   # kept anyway
    assert backfill.load(ws)["reading"]["at"].startswith("2099")
    assert backfill.due(ws, AT_01H) is None                                                       # but never read
    assert backfill.explain(ws, AT_01H)[1].endswith("once a reading of your usage shows that extra usage is off.")
    limits_row(ws, "2026-10-24T22:58:00Z", overage="allowed")                 # a real run: extra usage may be on
    assert backfill.due(ws, AT_01H) is None
    limits_row(ws, "2026-10-24T22:59:00Z")                                    # a newer one: extra usage refused
    assert backfill.due(ws, AT_01H)["id"] == "p1"
    assert backfill.main([str(ws), "reading", f"{AHEAD},at=2026-10-24T23:04:00Z", NIGHT]) == 0    # four: a clock apart


def test_a_routine_run_in_one_space_says_nothing_about_another_spaces_history(tmp_path, monkeypatch, capsys):
    """Probes Q1a and Q1b, O-1: the brain's routine run holds the brain's lock, and its agent's commands run in the
    brain, but may name the person's workspace. Every verb refuses there and nothing changes: the paid hold stays, no
    reading is kept, no part is touched (R43, N2-1 b). Said in the person's own workspace, or once the run is over,
    they work."""
    brain = make_space(tmp_path / "brain", profile="brain")
    ws = planned(tmp_path)
    backfill.hold(ws, "paid account: the person starts the read")
    setup_journal.save({"usage_account": "same"})
    before = backfill.load(ws)
    assert routine.take_lock(brain, "sweep-chain")
    try:
        monkeypatch.chdir(brain / "_meta")                                    # anywhere in the brain: its own space.yaml
        for words in (("start",), ("reading", AHEAD), ("plan", "--counts", "2026-10=10"),
                      ("calibrate", "--before", "0", "--after", "10"), ("due",), ("status",), ("mark", "mail"),
                      ("checkpoint", "p1", "mail", "2026-08-02T10:00:00Z"), ("read", "p1"), ("unfinished", "p1")):
            assert backfill.main([str(ws), *words, NIGHT]) == 2, words
            err = capsys.readouterr().err
            assert backfill.OTHER_SPACE_WHY in err and "Traceback" not in err, words
        assert backfill.load(ws) == before and backfill.due(ws, AT_01H) is None
        assert backfill.main([str(brain), "start", NIGHT]) == 2                  # in its own space: a person's verb
        assert backfill.RUN_GOING_WHY in capsys.readouterr().err
        monkeypatch.chdir(ws)                                                   # said in the person's own workspace
        assert backfill.main([str(ws), "due", NIGHT]) == 0
        assert backfill.main([str(ws), "start", NIGHT]) == 0
    finally:
        routine.release_lock(brain)
    assert backfill.due(ws, AT_01H)["id"] == "p1"
    monkeypatch.chdir(brain)                                                    # the run is over: a session in the brain
    assert backfill.main([str(ws), "mark", "mail", NIGHT]) == 0


def test_a_stopped_line_an_agent_wrote_does_not_end_the_wait_for_part_one(tmp_path, monkeypatch):
    """`--wait-part` stops waiting when the newest run stopped without reading: routine.py's own meter says so, never
    a line in runs.jsonl, which a routine's agent can append (R43, O-3)."""
    ws, started = history_world(tmp_path, monkeypatch)
    start.phase_history(history_args(tmp_path, ws, reading=READING_TEXT))
    looks = []

    def sleep(seconds):
        looks.append(seconds)
        assert len(looks) < 6, "it waits on, though the run stopped"
        if len(looks) == 2:                                                   # shaped like routine.py's own line
            with (ws / "_meta" / "state" / "runs.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps({"routine": "sweep-chain", "at": "2026-10-24T23:02:00Z", "space": "ada",
                                     "cost_tokens": 0, "outcome": "stopped", "notes": {"step": "routine"}}) + "\n")
        if len(looks) == 3:
            routine.write_outcome(ws, "sweep-chain", routine._stopped("claude did not start"), AT_01H + dt.timedelta(minutes=3))
    monkeypatch.setattr(start, "_sleep", sleep)
    monkeypatch.setattr(start, "_clock", lambda: 0.0)
    result = start.phase_history(history_args(tmp_path, ws, wait_part="p1"))
    assert looks == [30, 30, 30] and result["waited"]["status"] == "pending" and "stopped" in result["waited"]["why"]


# ===== Fix wave F2 (Rulings R45 and R47) =========================================================================
PHRASE = "five_hour=12,week=40,five_hour_resets=2026-10-24T22:30:00Z,week_resets=2026-10-27T11:00:00Z,extra=0"


def test_a_run_that_checkpointed_but_never_said_read_pauses_its_part_and_the_next_goes_on(tmp_path):
    """A checkpoint is not "through": the run that stopped after its first batch leaves the part paused, and the next
    run goes on from the checkpoint and finishes it by saying `read` (R45, I-4)."""
    ws = planned(tmp_path)
    backfill.begin(ws, "p1")
    backfill.checkpoint(ws, "p1", "mail", "2026-08-02T10:00:00Z")
    backfill.after_run(ws, "p1", used(30_000))
    p = part(ws, "p1")
    assert p["status"] == "paused" and p["why"] == backfill.WHY["unfinished"] and "idle" not in p
    assert backfill.due(ws, AT_19H)["id"] == "p1" and backfill.export_mark(ws, "mail") == "2026-08-02T10:00:00Z"
    backfill.begin(ws, "p1")
    backfill.checkpoint(ws, "p1", "mail", "2026-08-20T10:00:00Z")
    backfill.range_read(ws, "p1")
    backfill.after_run(ws, "p1", used(20_000))
    assert status(ws, "p1") == "done" and "through" not in part(ws, "p1") and part(ws, "p1")["tokens"] == 50_000


def test_a_read_said_in_one_run_does_not_finish_the_part_in_the_next(tmp_path):
    ws = planned(tmp_path)
    backfill.begin(ws, "p1")
    backfill.range_read(ws, "p1")
    backfill.unfinished(ws, "p1")                                             # it stopped short after all
    backfill.after_run(ws, "p1", used(10_000))
    backfill.begin(ws, "p1")                                                  # the next run reads nothing
    backfill.after_run(ws, "p1", used(10_000))
    assert status(ws, "p1") == "paused" and part(ws, "p1")["idle"] == 1


def test_the_cost_of_a_record_is_taken_from_part_one_only_once_it_is_done(tmp_path):
    """Part 1 measured while paused: the share per token is kept, the cost of a record stays the estimate until part 1
    is done, and is then part 1's own (R45, I-5)."""
    ws = make_space(tmp_path / "ws")
    backfill.plan(ws, COUNTS, NOW)
    records = part(ws, "p1")["records"]
    backfill.begin(ws, "p1")
    backfill.checkpoint(ws, "p1", "mail", "2026-08-02T10:00:00Z")
    backfill.after_run(ws, "p1", used(2_000_000), now=AT_01H)                 # paused at its fifty minutes
    backfill.calibrate(ws, 5, 45, 2_000_000, now=AT_01H)
    state = backfill.load(ws)
    assert state["calibration"] == "measured" and state["tokens_per_record"] == backfill.TOKENS_PER_RECORD
    read_through(ws, "p1", used(4_000_000), now=AT_01H + dt.timedelta(hours=6))
    assert backfill.load(ws)["tokens_per_record"] == round(6_000_000 / records)


def test_a_part_already_begun_goes_on_at_night_however_big_and_an_untouched_one_does_not_start(tmp_path):
    """The fifty-minute stop bounds every run, so a begun part is not refused for needing more than a night; by day it
    still waits for the night (R45, I-5)."""
    ws = planned(tmp_path, per_token_share=0.00002, part_tokens=9_000_000)    # part 2: 180 points
    night = dt.datetime(2026, 10, 26, 1, 0, tzinfo=TZ)
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=10, at=night - dt.timedelta(minutes=10)))
    assert backfill.due(ws, night) is None and "night allows" in backfill.explain(ws, night)[1]
    backfill.begin(ws, "p2")
    backfill.checkpoint(ws, "p2", "mail", "2026-08-30T10:00:00Z")
    backfill.after_run(ws, "p2", used(1_000_000), now=night - dt.timedelta(minutes=5))
    assert status(ws, "p2") == "paused" and backfill.due(ws, night)["id"] == "p2"
    noon = dt.datetime(2026, 10, 26, 13, 0, tzinfo=TZ)
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=10, at=noon - dt.timedelta(minutes=5)))
    assert backfill.due(ws, noon) is None and "waits for the night" in backfill.explain(ws, noon)[1]
    later = night + dt.timedelta(days=1)
    backfill.record_reading(ws, reading(five_hour_pct=0, week_pct=10, extra_usage=True, at=later - dt.timedelta(minutes=5)))
    assert backfill.due(ws, later) is None
    why = backfill.explain(ws, later)[1]
    assert "not started" not in why and "extra usage" in why


def hosted_elsewhere(ws, monkeypatch, here="m-laptop"):
    """The routines run on ADA-DESKTOP; this computer is `here`."""
    monkeypatch.setattr(routine, "this_machine", lambda: "m-desktop")
    monkeypatch.setattr(routine, "machine_name", lambda: "ADA-DESKTOP")
    routine.claim_host(ws, "os", AT_14H)
    monkeypatch.setattr(routine, "this_machine", lambda: here)


def test_readings_and_the_plan_are_kept_only_on_the_computer_that_runs_the_routines(tmp_path, monkeypatch, capsys):
    """Two computers writing the history's notes stop the second one's sync: on a computer that does not run the
    routines, the commands that write them refuse in plain words and write nothing (R45, I-1)."""
    ws = planned(tmp_path)
    hosted_elsewhere(ws, monkeypatch)
    before = backfill.load(ws)
    for words in (("reading", PHRASE), ("calibrate", "--before", "3", "--after", "13"), ("plan", "--counts", "2026-10=10"),
                  ("start",)):
        assert backfill.main([str(ws), *words, NIGHT]) == 2, words
        err = capsys.readouterr().err
        assert "ADA-DESKTOP" in err and "Traceback" not in err and err.startswith("backfill: Your routines run on"), err
    assert backfill.load(ws) == before
    monkeypatch.setattr(routine, "this_machine", lambda: "m-desktop")         # the computer that runs them
    assert backfill.main([str(ws), "reading", PHRASE, NIGHT]) == 0 and backfill.load(ws)["reading"]["week_pct"] == 40


def test_a_history_read_cannot_start_while_the_routines_run_in_the_cloud(tmp_path, capsys):
    """The history is read on a computer only: while the routines run in the cloud, every command that writes its
    notes refuses with the one sentence that says how to bring them back, on any computer."""
    ws = planned(tmp_path)
    routine.claim_cloud(ws, {"sweep": "trig_01AAAAAAAA", "daily": "trig_01BBBBBBBB"}, "Europe/Amsterdam",
                        ["ada/ws"], {"read": [], "write": [], "send": []}, AT_14H)
    before = backfill.load(ws)
    for words in (("start",), ("reading", PHRASE), ("calibrate", "--before", "3", "--after", "13"),
                  ("plan", "--counts", "2026-10=10")):
        assert backfill.main([str(ws), *words, NIGHT]) == 2, words
        assert capsys.readouterr().err.strip() == f"backfill: {backfill.CLOUD_WHY}", words
    assert backfill.load(ws) == before
    assert backfill.CLOUD_WHY == ("Your routines run in the cloud, and the history is read only on a computer: say "
                                  "'run my routines here' first.")


def test_the_history_phase_on_a_computer_that_does_not_run_the_routines_writes_nothing(tmp_path, monkeypatch):
    ws, started = history_world(tmp_path, monkeypatch)
    hosted_elsewhere(ws, monkeypatch)
    result = start.phase_history(history_args(tmp_path, ws, reading=READING_TEXT))
    assert result["state"] == "done" and "ADA-DESKTOP" in result["why"] and started == []
    assert not backfill.load(ws).get("parts") and not backfill.load(ws).get("reading")


def test_a_routine_run_may_not_set_its_own_clock(tmp_path, monkeypatch, capsys):
    """Probe R5: part 1 read and not measured, the run holds the lock with no part; its agent asks `due` with a clock
    13 hours on. While the space the command is said from holds a live routine lock, no verb takes `--now`; nothing is
    written, and the person's measurement still goes in (R47, O3-1)."""
    ws = planned(tmp_path)
    done = dt.datetime.now().astimezone() - dt.timedelta(hours=1)          # an hour ago: the guess is hours off yet
    read_through(ws, "p1", used(500_000), now=done)
    later = (done + dt.timedelta(hours=13)).isoformat(timespec="seconds")
    assert routine.take_lock(ws, "sweep-chain")
    try:
        monkeypatch.chdir(ws)
        for words in (("due", "--json"), ("status",), ("mark", "mail")):
            assert backfill.main([str(ws), *words, f"--now={later}"]) == 2, words
            err = capsys.readouterr().err
            assert backfill.NOW_WHY in err and "--now" not in err and "Traceback" not in err
        assert backfill.main([str(ws), "due", "--json"]) == 0                 # the computer's own clock is fine
        capsys.readouterr()
    finally:
        routine.release_lock(ws)
    assert not backfill.load(ws).get("calibration")
    assert backfill.main([str(ws), "calibrate", "--before", "0", "--after", "20", "--tokens", "500000"]) == 0


def test_no_command_takes_a_clock_ahead_of_this_computers(tmp_path, capsys):
    ws = planned(tmp_path)
    ahead = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat(timespec="seconds")
    for words in (("due",), ("status",), ("reading", PHRASE), ("plan", "--counts", "2026-10=10")):
        assert backfill.main([str(ws), *words, f"--now={ahead}"]) == 2, words
        assert backfill.NOW_AHEAD_WHY in capsys.readouterr().err
    soon = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1)).isoformat(timespec="seconds")
    assert backfill.main([str(ws), "due", f"--now={soon}"]) == 0             # within the slack a clock may be off


def test_a_reading_dated_ahead_of_the_real_clock_is_refused_whatever_the_now(tmp_path, capsys):
    """N3-1: the commands check a reading's own time against this computer's clock too."""
    ws = planned(tmp_path)
    ahead = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).isoformat(timespec="seconds")
    assert backfill.main([str(ws), "reading", f"{PHRASE},at={ahead}"]) == 2
    assert backfill.AHEAD_WHY in capsys.readouterr().err and "reading" not in backfill.load(ws)
    other = make_space(tmp_path / "other")
    assert backfill.main([str(other), "plan", "--counts", "2026-10=1400", "--reading", f"{PHRASE},at={ahead}"]) == 2
    assert backfill.AHEAD_WHY in capsys.readouterr().err and not backfill.load(other).get("parts")


def test_a_first_reading_dated_ahead_never_lifts_the_setup_days_cap(tmp_path):
    """Probe R4: a reading saying 99% of the week is used holds part 1 on the setup night. Kept with a time ahead (by
    a clock that was off), it is not read, and the setup day's cap holds all the same (R47, N3-1)."""
    ws = make_space(tmp_path / "ws")
    backfill.plan(ws, COUNTS, AT_19H, reading=reading(five_hour_pct=0, week_pct=99, at=AT_19H))
    assert backfill.due(ws, AT_01H) is None and "first day" in backfill.explain(ws, AT_01H)[1]
    state = backfill.load(ws)
    state["setup_reading"]["at"] = state["reading"]["at"] = "2099-01-01T00:00:00Z"
    backfill.save(ws, state)
    assert backfill.due(ws, AT_01H) is None and "first day" in backfill.explain(ws, AT_01H)[1]


def test_the_refusals_are_worded_for_whoever_said_the_command():
    """A person's own session in a space a routine run holds reads OTHER_SPACE_WHY too: it does not call their command
    a routine run (R47, N3-4)."""
    for why in (backfill.OTHER_SPACE_WHY, backfill.NOW_WHY, backfill.NOW_AHEAD_WHY):
        assert "routine run may not" not in why and "--" not in why and "backfill" not in why
    assert "say it again when the run is over" in backfill.OTHER_SPACE_WHY


def test_the_wait_for_part_one_names_the_pause_and_when_it_ends(tmp_path, monkeypatch):
    """The routines are paused: the wait ends at once and says until when (R45, Minor 13)."""
    ws, started = history_world(tmp_path, monkeypatch)
    start.phase_history(history_args(tmp_path, ws, reading=READING_TEXT))
    monkeypatch.setattr(routine, "this_machine", lambda: "m-desktop")
    monkeypatch.setattr(routine, "machine_name", lambda: "ADA-DESKTOP")
    routine.claim_host(ws, "os", AT_14H)
    routine.set_pause(ws, "2026-10-25T06:00:00+02:00")
    looks = []

    def sleep(seconds):
        looks.append(seconds)
        assert len(looks) < 6, "it waits on, though the routines are paused"
        if len(looks) == 1:
            held = {**routine._stopped("paused until 2026-10-25T06:00:00+02:00"), "outcome": "paused"}
            routine.write_outcome(ws, "sweep-chain", held, AT_01H + dt.timedelta(minutes=2))
    monkeypatch.setattr(start, "_sleep", sleep)
    monkeypatch.setattr(start, "_clock", lambda: 0.0)
    result = start.phase_history(history_args(tmp_path, ws, wait_part="p1"))
    why = result["waited"]["why"]
    assert "paused until" in why and "06:00" in why and "stopped before it read" not in why
