"""A computer that cannot bring main in says so on Needs you. The sync retries a failed fetch and
a merge that stops on its next run, and skips a run while git is mid-operation; each is right for
an hour and wrong for a week. Once the same cause has lasted a day it is one item on the sync's own
Needs-you file, with since when and what is in the way; it goes when a run brings main in again."""
import datetime as dt
import os
import time
from pathlib import Path

import needs_you
import pages
import pytest
import status
import sync
from conftest import NOW
from test_sync import git, repo, run, run_lines, write  # noqa: F401  (the repo fixture)

DAY = dt.timedelta(hours=25)
QUARTER = dt.timedelta(minutes=15)


def runs(clone: Path, monkeypatch, start: dt.datetime, times: int, code: int) -> None:
    """`times` runs a quarter of an hour apart from `start`, each ending as `code`."""
    for n in range(times):
        clock(monkeypatch, start + n * QUARTER)
        assert run(clone) == code


def asked(root: Path) -> list:
    return [i for i in needs_you.collect(root, online=False) if i.id.startswith(sync.BEHIND_ITEM)]


def clock(monkeypatch, moment: dt.datetime) -> None:
    monkeypatch.setenv("SPACE_NOW", pages.iso(moment))


def test_a_fetch_that_fails_for_a_day_is_one_item_until_a_fetch_works_again(repo, tmp_path, monkeypatch):
    real = git(repo, "remote", "get-url", "origin").stdout.strip()
    git(repo, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))
    runs(repo, monkeypatch, NOW, 4, sync.ERROR)
    assert asked(repo) == []   # a laptop off the network for an hour: tried again next run
    clock(monkeypatch, NOW + DAY)
    assert run(repo) == sync.ERROR
    (item,) = asked(repo)
    assert "fetch" in item.text and "2026-10-12" in item.text
    before = (repo / sync.SYNC_ITEMS).read_bytes()
    clock(monkeypatch, NOW + DAY + dt.timedelta(minutes=15))
    assert run(repo) == sync.ERROR and (repo / sync.SYNC_ITEMS).read_bytes() == before   # asked once

    git(repo, "remote", "set-url", "origin", real)
    clock(monkeypatch, NOW + DAY + dt.timedelta(minutes=30))
    assert run(repo) == 0 and asked(repo) == []


def test_a_merge_that_stops_for_a_day_is_one_item_that_names_the_files(repo, tmp_path, monkeypatch):
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(other))
    write(other / "work" / "notes.md", "# Notes\n\ntheirs\n")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "theirs")
    git(other, "push", "-q", "origin", "main")
    write(repo / "work" / "notes.md", "# Notes\n\nours\n")
    runs(repo, monkeypatch, NOW, 4, 0)
    assert run_lines(repo)[-1]["notes"].get("conflict") is True and asked(repo) == []
    clock(monkeypatch, NOW + DAY)
    assert run(repo) == 0
    (item,) = asked(repo)
    assert "work/notes.md" in item.text and "2026-10-12" in item.text

    git(other, "reset", "-q", "--hard", "HEAD~1")   # main takes the change back: the next merge goes through
    git(other, "push", "-q", "--force", "origin", "main")
    clock(monkeypatch, NOW + DAY + dt.timedelta(minutes=15))
    assert run(repo) == 0 and asked(repo) == []


@pytest.mark.parametrize("marker", ["index.lock", "MERGE_HEAD"])
def test_a_git_operation_left_behind_for_a_day_is_said_though_every_run_is_skipped(repo, monkeypatch, marker):
    lock = repo / ".git" / marker
    lock.write_text("0" * 40 + "\n", encoding="utf-8")
    assert run(repo) == sync.SKIPPED and asked(repo) == []   # a commit or a merge in progress: the next run lands
    old = time.time() - 26 * 3600
    os.utime(lock, (old, old))
    assert run(repo, "--dry-run") == sync.SKIPPED and run(repo, "--only", "work") == sync.SKIPPED
    assert asked(repo) == []   # a dry run and a run of named paths write nothing
    assert run(repo) == sync.SKIPPED
    (item,) = asked(repo)
    assert marker in item.text and "skipped" in item.text
    before = (repo / sync.SYNC_ITEMS).read_bytes()
    assert run(repo) == sync.SKIPPED and (repo / sync.SYNC_ITEMS).read_bytes() == before

    lock.unlink()
    assert run(repo) == 0 and asked(repo) == []


def test_the_same_cause_on_another_day_is_asked_again_after_an_answer(repo, tmp_path, monkeypatch):
    real = git(repo, "remote", "get-url", "origin").stdout.strip()
    git(repo, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))
    runs(repo, monkeypatch, NOW, 3, sync.ERROR)
    clock(monkeypatch, NOW + DAY)
    assert run(repo) == sync.ERROR
    (first,) = asked(repo)
    status.record_verdict(repo, first.id, "Ada Okafor", verb="yes", at=pages.iso(NOW + DAY))
    clock(monkeypatch, NOW + DAY + dt.timedelta(minutes=15))
    assert run(repo) == sync.ERROR and asked(repo) == []   # answered: this incident is not asked again
    git(repo, "remote", "set-url", "origin", real)
    clock(monkeypatch, NOW + DAY + dt.timedelta(minutes=30))
    assert run(repo) == 0 and asked(repo) == []

    git(repo, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))   # a week later it fails again
    runs(repo, monkeypatch, NOW + 7 * DAY, 3, sync.ERROR)
    clock(monkeypatch, NOW + 8 * DAY)
    assert run(repo) == sync.ERROR
    (again,) = asked(repo)
    assert again.id != first.id and "fetch" in again.text


def test_one_failed_run_after_days_asleep_is_no_alarm(repo, tmp_path, monkeypatch):
    real = git(repo, "remote", "get-url", "origin").stdout.strip()
    git(repo, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))
    clock(monkeypatch, NOW)
    assert run(repo) == sync.ERROR            # the last run before the lid closed, on a network that was gone
    clock(monkeypatch, NOW + 3 * DAY)
    assert run(repo) == sync.ERROR and asked(repo) == []   # awake again, one more failure: not yet a pattern
    git(repo, "remote", "set-url", "origin", real)
    clock(monkeypatch, NOW + 3 * DAY + QUARTER)
    assert run(repo) == 0 and asked(repo) == []


def test_a_run_that_dies_the_same_way_for_a_day_is_said_too(repo, monkeypatch):
    def broken(self):
        raise OSError("the index cannot be written")
    monkeypatch.setattr(sync.Run, "stage_and_commit", broken)
    runs(repo, monkeypatch, NOW, 3, sync.ERROR)
    assert asked(repo) == []
    clock(monkeypatch, NOW + DAY)
    assert run(repo) == sync.ERROR
    (item,) = asked(repo)
    assert "failed on every run" in item.text and "OSError" in item.text
    monkeypatch.undo()
    clock(monkeypatch, NOW + DAY + QUARTER)
    assert run(repo) == 0 and asked(repo) == []
