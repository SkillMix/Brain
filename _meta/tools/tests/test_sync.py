"""sync.py: the one tool that runs git, exercised against a throwaway bare origin
and a clone, always with --no-push. The secret below is assembled from parts so
this file passes the scan itself."""
import datetime as dt
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

import sync
from conftest import make_space

GIT_ID = ["-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.test"]
KEY_BLOCK = "".join(["-----BEGIN ", "PRIVATE KEY-----\nabc\n-----END ", "PRIVATE KEY-----\n"])


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *GIT_ID, *args], cwd=str(cwd), check=check, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def write(path: Path, text: str, minutes_old: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    if minutes_old is not None:
        stamp = time.time() - minutes_old * 60
        os.utime(path, (stamp, stamp))
    return path


def run(clone: Path, *extra: str) -> int:
    return sync.main(["--root", str(clone), "--no-push", *extra])


def subjects(clone: Path) -> list:
    return git(clone, "log", "--format=%s").stdout.strip().splitlines()


def head_files(clone: Path) -> list:
    return git(clone, "show", "--name-only", "--format=", "HEAD").stdout.split()


def run_lines(clone: Path) -> list:
    path = clone / "_meta" / "state" / "runs.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    config = tmp_path / "gitconfig"
    config.write_text('[protocol "file"]\n\tallow = always\n', encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("SPACE_NOW", raising=False)
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    for key, value in (("user.name", "t"), ("user.email", "t@example.test"), ("commit.gpgsign", "false")):
        git(clone, "config", key, value)
    make_space(clone, profile="personal", name="ada")
    write(clone / ".gitignore", "personal/\nacademic/\n_scratch/\n")
    write(clone / ".gitattributes", "_meta/state/*.jsonl merge=union\n* text=auto eol=lf\n")
    write(clone / "work" / "notes.md", "# Notes\n\nline one\n")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "init")
    git(clone, "push", "-q", "-u", "origin", "main")
    return clone


def test_first_run_commits_tracked_and_settled_untracked_but_not_private_or_secrets(repo):
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n")
    write(repo / "work" / "old.md", "settled fifteen minutes ago\n", minutes_old=15)
    write(repo / "work" / "fresh.md", "still being written\n")
    write(repo / "personal" / "x.md", "private\n", minutes_old=15)
    key = write(repo / "work" / "key.md", KEY_BLOCK, minutes_old=15)
    assert run(repo) == 0
    assert subjects(repo)[0] == "chore: sync"
    files = head_files(repo)
    assert "work/notes.md" in files and "work/old.md" in files
    assert "work/fresh.md" not in files
    assert "personal/x.md" not in files
    assert "work/key.md" not in files
    status = git(repo, "status", "--porcelain").stdout
    assert "?? work/key.md" in status and key.exists()
    assert "?? work/fresh.md" in status
    lines = run_lines(repo)
    assert len(lines) == 1
    line = lines[0]
    assert set(line) >= {"routine", "at", "space", "cost_tokens", "notes"}
    assert line["routine"] == "sync" and line["space"] == "ada"
    assert line["notes"]["committed"] is True
    assert line["notes"]["scan_hits"] == 1
    assert not line["notes"].get("heartbeat")
    dump = json.dumps(line)
    assert "PRIVATE" not in dump and "key.md" not in dump


def test_quiet_runs_commit_nothing_and_write_one_heartbeat_a_day(repo):
    write(repo / "work" / "notes.md", "# Notes\n\nedited\n")
    assert run(repo) == 0
    count = len(subjects(repo))
    assert run(repo) == 0
    assert len(subjects(repo)) == count
    lines = run_lines(repo)
    assert len(lines) == 2
    assert lines[1]["notes"].get("heartbeat") is True
    assert run(repo) == 0
    assert len(subjects(repo)) == count
    assert len(run_lines(repo)) == 2


@pytest.mark.parametrize("marker", ["MERGE_HEAD", "index.lock", "rebase-merge", "rebase-apply"])
def test_run_is_skipped_while_git_is_mid_operation(repo, marker):
    write(repo / "work" / "notes.md", "# Notes\n\nedited\n")
    target = repo / ".git" / marker
    if marker.startswith("rebase"):
        target.mkdir()
    else:
        target.write_text("0" * 40 + "\n", encoding="utf-8")
    try:
        assert run(repo) == 2
    finally:
        if target.is_dir():
            target.rmdir()
        else:
            target.unlink()
    assert subjects(repo) == ["init"]
    assert run_lines(repo) == []


def test_fresh_lock_skips_and_stale_lock_is_taken_over(repo):
    write(repo / "work" / "notes.md", "# Notes\n\nedited\n")
    lock = repo / ".git" / "sync.lock"
    now = dt.datetime.now(dt.timezone.utc)
    lock.write_text(json.dumps({"pid": 1, "at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}), encoding="utf-8")
    assert run(repo) == 2
    assert subjects(repo) == ["init"]
    stale = now - dt.timedelta(hours=2)
    lock.write_text(json.dumps({"pid": 1, "at": stale.strftime("%Y-%m-%dT%H:%M:%SZ")}), encoding="utf-8")
    assert run(repo) == 0
    assert subjects(repo)[0] == "chore: sync"
    assert not lock.exists()


def test_dry_run_reports_and_changes_nothing(repo, capsys):
    write(repo / "work" / "notes.md", "# Notes\n\nedited\n")
    write(repo / "work" / "old.md", "settled\n", minutes_old=15)
    assert run(repo, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "work/notes.md" in out and "work/old.md" in out
    assert subjects(repo) == ["init"]
    status = git(repo, "status", "--porcelain").stdout
    assert " M work/notes.md" in status and "?? work/old.md" in status
    assert run_lines(repo) == []


def test_merges_origin_main_in_and_records_a_conflict(repo, tmp_path):
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(other))
    write(other / "work" / "remote.md", "from the other machine\n")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "remote")
    git(other, "push", "-q", "origin", "main")
    assert run(repo) == 0
    assert (repo / "work" / "remote.md").exists()
    assert git(repo, "rev-parse", "HEAD").stdout == git(repo, "rev-parse", "origin/main").stdout
    write(other / "work" / "notes.md", "# Notes\n\ntheirs\n")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "theirs")
    git(other, "push", "-q", "origin", "main")
    write(repo / "work" / "notes.md", "# Notes\n\nours\n")
    assert run(repo) == 0
    assert subjects(repo)[0] == "chore: sync"
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    assert (repo / "work" / "notes.md").read_text(encoding="utf-8") == "# Notes\n\nours\n"
    assert run_lines(repo)[-1]["notes"].get("conflict") is True


def test_release_leg_runs_ship_before_staging_or_notes_its_absence(repo, monkeypatch):
    calls = []
    fake = types.ModuleType("release")

    def ship(root):
        calls.append(Path(root).resolve())
        write(Path(root) / "work" / "notes.md", "# Notes\n\nshipped\n")
        return {"released": 1}

    fake.ship = ship
    monkeypatch.setitem(sys.modules, "release", fake)
    assert run(repo, "--release-leg") == 0
    assert calls == [repo.resolve()]
    assert subjects(repo)[0] == "chore: sync" and "work/notes.md" in head_files(repo)
    assert run_lines(repo)[-1]["notes"]["release"] == {"released": 1}
    monkeypatch.setitem(sys.modules, "release", None)
    assert run(repo, "--release-leg") == 0
    assert run_lines(repo)[-1]["notes"]["release"] == "absent"


def test_clean_submodule_pin_advances_as_chore_pins(repo, tmp_path):
    brain_origin = tmp_path / "brain.git"
    git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(brain_origin))
    brain_work = tmp_path / "brain-work"
    git(tmp_path, "clone", "-q", str(brain_origin), str(brain_work))
    write(brain_work / "index.md", "# Brain\n")
    git(brain_work, "add", "-A")
    git(brain_work, "commit", "-q", "-m", "one")
    git(brain_work, "push", "-q", "-u", "origin", "main")
    git(repo, "submodule", "add", "-q", "-b", "main", str(brain_origin), "brain")
    git(repo, "commit", "-q", "-m", "mount")
    git(repo, "push", "-q", "origin", "main")
    write(brain_work / "index.md", "# Brain\n\nmore\n")
    git(brain_work, "add", "-A")
    git(brain_work, "commit", "-q", "-m", "two")
    git(brain_work, "push", "-q", "origin", "main")
    new = git(brain_work, "rev-parse", "HEAD").stdout.strip()
    assert run(repo) == 0
    assert subjects(repo)[0] == "chore: pins"
    assert git(repo, "ls-tree", "HEAD", "brain").stdout.split()[2] == new
    assert run_lines(repo)[-1]["notes"]["pins"] == ["brain"]
    assert head_files(repo) == ["brain"]  # the pins commit carries only the pin
    assert (repo / "brain" / "index.md").read_text(encoding="utf-8") == "# Brain\n\nmore\n"


def test_protected_changes_go_to_their_own_branch_so_the_rest_lands_on_green(repo, monkeypatch):
    """A change to space.yaml (protected) and a desk note in one run: the note's branch holds
    only the note, the protected branch only space.yaml; a second run pushes nothing new."""
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    data = (repo / "space.yaml").read_text(encoding="utf-8") + "planning: personal/planning/x.md\n"
    write(repo / "space.yaml", data, minutes_old=20)
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    host = sync.hostname()
    git(repo, "fetch", "-q", "origin")
    plain = git(repo, "diff", "--name-only", "origin/main", f"origin/sync/{host}").stdout.split()
    guarded = git(repo, "diff", "--name-only", "origin/main", f"origin/sync/{host}-protected").stdout.split()
    assert "work/notes.md" in plain and "space.yaml" not in plain
    assert guarded == ["space.yaml"]
    assert sync.main(["--root", str(repo)]) == 0   # carries the first run's line along
    git(repo, "fetch", "-q", "origin")
    before = git(repo, "rev-parse", f"origin/sync/{host}", f"origin/sync/{host}-protected").stdout
    assert sync.main(["--root", str(repo)]) == 0   # nothing new: neither branch moves
    git(repo, "fetch", "-q", "origin")
    assert git(repo, "rev-parse", f"origin/sync/{host}", f"origin/sync/{host}-protected").stdout == before


def test_files_the_template_manages_travel_with_the_protected_ones(repo, monkeypatch):
    """A template update lands as one unit: a managed file outside the protected prefixes (a
    template prompt) goes to the protected branch with the tools that expect it."""
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    write(repo / "_meta" / "state" / "template-version.yaml",
          "repo: x\ncommit: abc\nfiles:\n  _meta/templates/prompt.md: a1\n", minutes_old=20)
    write(repo / "_meta" / "templates" / "prompt.md", "v2\n", minutes_old=20)
    write(repo / "space.yaml", (repo / "space.yaml").read_text(encoding="utf-8") + "x: 1\n", minutes_old=20)
    write(repo / "work" / "notes.md", "# Notes\n\nline three\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    host = sync.hostname()
    git(repo, "fetch", "-q", "origin")
    guarded = git(repo, "diff", "--name-only", "origin/main", f"origin/sync/{host}-protected").stdout.split()
    plain = git(repo, "diff", "--name-only", "origin/main", f"origin/sync/{host}").stdout.split()
    assert "_meta/templates/prompt.md" in guarded and "_meta/state/template-version.yaml" in guarded
    assert "_meta/templates/prompt.md" not in plain and "work/notes.md" in plain


def test_run_lines_and_pins_alone_wait_until_the_quiet_window_passes(repo, monkeypatch):
    """A space whose only change is its own run lines (or a mount pin) opens no pull request
    every 15 minutes: it publishes once, then waits QUIET_HOURS or until real content comes."""
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    host = sync.hostname()
    runs = repo / "_meta" / "state" / "runs.jsonl"

    def add_line(n):
        old = runs.read_text(encoding="utf-8") if runs.exists() else ""
        write(runs, old + json.dumps({"routine": "daily", "at": f"2026-10-14T0{n}:00:00Z"}) + "\n", minutes_old=20)

    def branch():
        git(repo, "fetch", "-q", "origin")
        return git(repo, "rev-parse", "-q", "--verify", f"origin/sync/{host}", check=False).stdout.strip()

    add_line(1)
    assert sync.main(["--root", str(repo)]) == 0
    first = branch()
    assert first   # nothing published from this checkout yet: it goes out
    add_line(2)
    assert sync.main(["--root", str(repo)]) == 0
    assert branch() == first   # only run lines, inside the window: it waits
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    second = branch()
    assert second != first   # real content goes out at once, the waiting lines with it
    add_line(3)
    stamp = repo / ".git" / "sync-quiet-pushed"
    stamp.write_text(str(time.time() - (sync.QUIET_HOURS + 1) * 3600), encoding="utf-8")
    assert sync.main(["--root", str(repo)]) == 0
    assert branch() != second   # the window passed: the lines go out
