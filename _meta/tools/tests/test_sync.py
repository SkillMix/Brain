"""sync.py: the one tool that runs git, exercised against a throwaway bare origin
and a clone, always with --no-push. The secret below is assembled from parts so
this file passes the scan itself."""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

import ghrest
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


def test_a_routine_run_alone_is_no_commit_worth_a_sync_line(repo):
    """routine.py writes a run line and its meter row with every run. That alone is housekeeping, never `committed`,
    so a routine run makes no sync run line of its own (R43, N2-5)."""
    write(repo / "work" / "notes.md", "# Notes\n\nedited\n")
    assert run(repo) == 0                                                     # the day's committed line
    assert run(repo) == 0                                                     # and its one heartbeat
    count = len(run_lines(repo))
    runs = repo / "_meta" / "state" / "runs.jsonl"
    row = {"routine": "sweep-chain", "at": "2026-10-14T01:00:00Z", "outcome": "finished", "cost_tokens": 0}
    write(runs, runs.read_text(encoding="utf-8") + json.dumps({**row, "notes": {"step": "routine"}}) + "\n", minutes_old=20)
    write(repo / "_meta" / "state" / "routine-limits.jsonl", json.dumps(row) + "\n", minutes_old=20)
    assert run(repo) == 0
    lines = run_lines(repo)
    assert len(lines) == count + 1 and lines[-1]["routine"] == "sweep-chain"   # the routine's line, and no sync line


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


def test_a_moved_file_leaves_its_old_path_when_protected_changes_split_the_run(repo, monkeypatch):
    """A file moved in the same run as a protected change: the plain branch drops the old path
    too, not only adds the new one (git's rename detection names a move by its new path only)."""
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    write(repo / "space.yaml", (repo / "space.yaml").read_text(encoding="utf-8") + "x: 1\n", minutes_old=20)
    (repo / "work" / "notes.md").unlink()
    write(repo / "work" / "filed" / "notes.md", "# Notes\n\nline one\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    host = sync.hostname()
    git(repo, "fetch", "-q", "origin")
    plain = f"origin/sync/{host}"
    assert git(repo, "ls-tree", "--name-only", plain, "work/filed/notes.md").stdout.strip()
    assert not git(repo, "ls-tree", "--name-only", plain, "work/notes.md").stdout.strip()


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


def remote_space(tmp_path, name):
    """A space another checkout mounts: its bare origin and a working clone that moves it."""
    origin, work = tmp_path / f"{name}.git", tmp_path / f"{name}-work"
    git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    git(tmp_path, "clone", "-q", str(origin), str(work))
    return origin, work


def move(work, text):
    write(work / "index.md", text)
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "move")
    git(work, "push", "-q", "origin", "HEAD:main")
    return git(work, "rev-parse", "HEAD").stdout.strip()


def platform_merges(tmp_path, branch):
    """The platform merges a pull request: a merge commit on origin's main."""
    hub = tmp_path / "hub"
    if not hub.exists():
        git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(hub))
    git(hub, "fetch", "-q", "origin")
    git(hub, "checkout", "-q", "-B", "main", "origin/main")
    git(hub, "merge", "-q", "--no-ff", "--no-edit", f"origin/{branch}")
    git(hub, "push", "-q", "origin", "main")


def mounted(repo, origin, path):
    """A mount already on origin/main, as one the person's click landed."""
    git(repo, "submodule", "add", "-q", "-b", "main", str(origin), path)
    git(repo, "commit", "-q", "-m", "mount")
    git(repo, "push", "-q", "origin", "main")


def pin(clone, ref, path):
    return git(clone, "ls-tree", ref, path).stdout.split()[2]


def branch_paths(repo, branch):
    """What a sync branch changes against origin/main, sorted; empty when it was never pushed."""
    git(repo, "fetch", "-q", "origin")
    if git(repo, "rev-parse", "-q", "--verify", f"origin/{branch}", check=False).returncode:
        return []
    return sorted(git(repo, "diff", "--name-only", "--no-renames", f"origin/main...origin/{branch}").stdout.split())


def test_a_pin_advance_merges_while_a_protected_change_waits(repo, tmp_path, monkeypatch):
    """A protected change waiting splits the push: a mount already on origin/main moves, and its
    pin lands on green through a rebuilt commit that shares no history with this checkout (its
    address did not change, decision 2026-10-16). The mounted space moves again, the next run
    advances the pin again, and origin/main must still merge in, the run keeping its own newer pin."""
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    origin, work = remote_space(tmp_path, "harbor")
    move(work, "# Harbor\n")
    mounted(repo, origin, "mounts/harbor")
    first = move(work, "# Harbor\n\nmoved\n")
    write(repo / "space.yaml", (repo / "space.yaml").read_text(encoding="utf-8") + "x: 1\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    host = sync.hostname()
    assert branch_paths(repo, f"sync/{host}") == ["mounts/harbor"]
    assert branch_paths(repo, f"sync/{host}-protected") == ["space.yaml"]
    platform_merges(tmp_path, f"sync/{host}")   # the pin lands; the protected change waits
    git(repo, "fetch", "-q", "origin")
    assert pin(repo, "origin/main", "mounts/harbor") == first
    assert git(repo, "ls-tree", "origin/main", "space.yaml").stdout != git(repo, "ls-tree", "HEAD", "space.yaml").stdout
    second = move(work, "# Harbor\n\nmoved twice\n")
    assert sync.main(["--root", str(repo)]) == 0
    notes = run_lines(repo)[-1]["notes"]
    assert not notes.get("conflict"), notes
    assert git(repo, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False).returncode == 0
    assert pin(repo, "HEAD", "mounts/harbor") == second
    assert not git(repo, "diff", "--name-only", "--diff-filter=U").stdout.strip()
    # the protected change still waits on its own branch
    assert branch_paths(repo, f"sync/{host}-protected") == ["space.yaml"]


def test_a_new_mount_waits_for_the_click_with_its_address(repo, tmp_path, monkeypatch):
    """A new mount is its pin and its address in .gitmodules: both wait on the protected branch
    for the person's click, so main never holds a pin `git submodule update --init` cannot fetch
    ("No url found"). A mount already on main still advances on green beside it. After the click
    the new mount has moved, and origin/main merges in: git cannot merge two pins of a mount the
    merge base lacks, so the run keeps its own newer one."""
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    harbor, harbor_work = remote_space(tmp_path, "harbor")
    move(harbor_work, "# Harbor\n")
    mounted(repo, harbor, "mounts/harbor")
    moved = move(harbor_work, "# Harbor\n\nmoved\n")
    port, port_work = remote_space(tmp_path, "port")
    first = move(port_work, "# Port\n")
    git(repo, "submodule", "add", "-q", "-b", "main", str(port), "mounts/port")
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    host = sync.hostname()
    assert branch_paths(repo, f"sync/{host}-protected") == [".gitmodules", "mounts/port"]
    assert branch_paths(repo, f"sync/{host}") == ["mounts/harbor", "work/notes.md"]
    platform_merges(tmp_path, f"sync/{host}")
    git(repo, "fetch", "-q", "origin")
    assert pin(repo, "origin/main", "mounts/harbor") == moved
    assert not git(repo, "ls-tree", "origin/main", "mounts/port").stdout.strip()
    platform_merges(tmp_path, f"sync/{host}-protected")   # the person's click
    fresh = tmp_path / "fresh"
    git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(fresh))
    done = git(fresh, "submodule", "update", "--init", check=False)
    assert done.returncode == 0, done.stderr
    assert pin(fresh, "HEAD", "mounts/port") == first
    second = move(port_work, "# Port\n\nmoved\n")
    assert sync.main(["--root", str(repo)]) == 0
    notes = run_lines(repo)[-1]["notes"]
    assert not notes.get("conflict"), notes
    assert git(repo, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False).returncode == 0
    assert pin(repo, "HEAD", "mounts/port") == second
    assert not git(repo, "diff", "--name-only", "--diff-filter=U").stdout.strip()


def test_a_mount_at_a_new_address_waits_for_the_click_with_it(repo, tmp_path, monkeypatch):
    """A mount moved to another address and pinned there waits with .gitmodules: main's address
    would not hold the new pin."""
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    harbor, harbor_work = remote_space(tmp_path, "harbor")
    move(harbor_work, "# Harbor\n")
    mounted(repo, harbor, "mounts/harbor")
    mirror = tmp_path / "mirror.git"
    git(tmp_path, "clone", "-q", "--bare", str(harbor), str(mirror))
    mirror_work = tmp_path / "mirror-work"
    git(tmp_path, "clone", "-q", str(mirror), str(mirror_work))
    there = move(mirror_work, "# Harbor\n\nonly at the new address\n")
    git(repo, "submodule", "set-url", "--", "mounts/harbor", str(mirror))
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    host = sync.hostname()
    assert pin(repo, "HEAD", "mounts/harbor") == there
    assert branch_paths(repo, f"sync/{host}-protected") == [".gitmodules", "mounts/harbor"]
    assert branch_paths(repo, f"sync/{host}") == ["work/notes.md"]


def test_a_mount_moved_to_another_path_waits_with_its_old_path(repo, tmp_path, monkeypatch):
    """A mount moved to another folder is a new pin there and an old one gone: both wait with
    .gitmodules, so main never holds the one without the other's address."""
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    harbor, harbor_work = remote_space(tmp_path, "harbor")
    move(harbor_work, "# Harbor\n")
    mounted(repo, harbor, "mounts/harbor")
    git(repo, "mv", "mounts/harbor", "mounts/haven")
    git(repo, "commit", "-q", "-m", "the person moves the mount")
    assert sync.main(["--root", str(repo)]) == 0
    host = sync.hostname()
    assert branch_paths(repo, f"sync/{host}-protected") == [".gitmodules", "mounts/harbor", "mounts/haven"]
    assert branch_paths(repo, f"sync/{host}") == []


def test_a_path_changed_again_while_a_protected_change_waits_merges(repo, tmp_path, monkeypatch):
    """The same for a plain file: origin holds the copy this checkout pushed before, this
    checkout has changed it since, so ours wins. A change origin got from anyone else still
    conflicts and aborts."""
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    write(repo / "space.yaml", (repo / "space.yaml").read_text(encoding="utf-8") + "x: 1\n", minutes_old=20)
    write(repo / "work" / "notes.md", "# Notes\n\nline one, edited\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    platform_merges(tmp_path, f"sync/{sync.hostname()}")
    write(repo / "work" / "notes.md", "# Notes\n\nline one, edited twice\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    assert not run_lines(repo)[-1]["notes"].get("conflict")
    assert git(repo, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False).returncode == 0
    assert (repo / "work" / "notes.md").read_text(encoding="utf-8") == "# Notes\n\nline one, edited twice\n"
    hub = tmp_path / "hub"
    git(hub, "fetch", "-q", "origin")
    git(hub, "checkout", "-q", "-B", "main", "origin/main")
    write(hub / "work" / "notes.md", "# Notes\n\nsomeone else's line\n")
    git(hub, "commit", "-q", "-am", "theirs")
    git(hub, "push", "-q", "origin", "main")
    write(repo / "work" / "notes.md", "# Notes\n\nline one, edited three times\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    assert run_lines(repo)[-1]["notes"].get("conflict") is True
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    assert (repo / "work" / "notes.md").read_text(encoding="utf-8") == "# Notes\n\nline one, edited three times\n"


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
    # routine.py's own meter line changes with every run, like a run line
    meter = repo / "_meta" / "state" / "routine-limits.jsonl"
    write(meter, json.dumps({"at": "2026-10-14T02:00:00Z"}) + "\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    assert branch() == first   # only run lines and the meter, inside the window: it waits
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    second = branch()
    assert second != first   # real content goes out at once, the waiting lines with it
    add_line(3)
    stamp = repo / ".git" / "sync-quiet-pushed"
    stamp.write_text(str(time.time() - (sync.QUIET_HOURS + 1) * 3600), encoding="utf-8")
    assert sync.main(["--root", str(repo)]) == 0
    assert branch() != second   # the window passed: the lines go out


def test_journal_and_run_logs_alone_go_out_at_most_hourly(repo, tmp_path, monkeypatch):
    """Daily-note lines and run logs open no pull request every 15 minutes: they go out once,
    then wait BATCH_HOURS or until real content comes. Run lines alone keep QUIET_HOURS."""
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    host = sync.hostname()
    note = repo / "journal" / "2026" / "10" / "2026-10-15.md"
    stamp = repo / ".git" / "sync-quiet-pushed"

    def branch():
        git(repo, "fetch", "-q", "origin")
        return git(repo, "rev-parse", "-q", "--verify", f"origin/sync/{host}", check=False).stdout.strip()

    write(note, "# 2026-10-15\n\n- one\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    first = branch()
    assert first   # nothing published from this checkout yet: it goes out
    write(note, "# 2026-10-15\n\n- one\n- two\n", minutes_old=20)
    write(repo / "_meta" / "state" / "tests.jsonl", '{"at": "2026-10-15T10:00:00Z"}\n', minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    assert branch() == first   # journal and run logs inside the hour: they wait
    stamp.write_text(str(time.time() - (sync.BATCH_HOURS + 0.1) * 3600), encoding="utf-8")
    assert sync.main(["--root", str(repo)]) == 0
    assert branch() != first   # the hour passed: they go out
    platform_merges(tmp_path, f"sync/{host}")
    assert sync.main(["--root", str(repo)]) == 0   # merges main in; only its own run line is left
    second = branch()
    stamp.write_text(str(time.time() - (sync.BATCH_HOURS + 0.1) * 3600), encoding="utf-8")
    assert sync.main(["--root", str(repo)]) == 0
    assert branch() == second   # run lines alone still wait for the quiet window


def test_a_waiting_pull_request_is_not_rebuilt_when_only_main_moves(repo, tmp_path, monkeypatch):
    """The protected change waits for the owner's click while the plain part lands. Main moves
    under it, but its branch keeps the same commit (no push, so no new checks) until its own
    content changes."""
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    host = sync.hostname()
    protected = f"origin/sync/{host}-protected"
    write(repo / "space.yaml", (repo / "space.yaml").read_text(encoding="utf-8") + "x: 1\n", minutes_old=20)
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    git(repo, "fetch", "-q", "origin")
    waiting = git(repo, "rev-parse", protected).stdout.strip()
    platform_merges(tmp_path, f"sync/{host}")   # the plain part lands: main moves
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\nline three\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    git(repo, "fetch", "-q", "origin")
    assert git(repo, "rev-parse", protected).stdout.strip() == waiting
    assert "work/notes.md" in git(repo, "diff", "--name-only", "origin/main", f"origin/sync/{host}").stdout.split()
    write(repo / "space.yaml", (repo / "space.yaml").read_text(encoding="utf-8") + "y: 2\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    git(repo, "fetch", "-q", "origin")
    assert git(repo, "rev-parse", protected).stdout.strip() != waiting   # its own content changed
    assert git(repo, "diff", "--name-only", f"origin/main...{protected}").stdout.split() == ["space.yaml"]


def on_origin_main(tmp_path, files: dict, branch: str = "cloud/sweep") -> None:
    """Lines another runner appends to files, landed on origin's main through its own pull request."""
    cloud = tmp_path / "cloud"
    if not cloud.exists():
        git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(cloud))
    git(cloud, "fetch", "-q", "origin")
    git(cloud, "checkout", "-q", "-B", branch, "origin/main")
    for rel, line in files.items():
        path = cloud / rel
        write(path, (path.read_text(encoding="utf-8") if path.exists() else "") + line)
    git(cloud, "add", "-A")
    git(cloud, "commit", "-q", "-m", "chore: sync")
    git(cloud, "push", "-q", "--force", "origin", f"{branch}:refs/heads/{branch}")
    platform_merges(tmp_path, branch)


def test_a_split_push_while_the_merge_is_refused_never_undoes_what_main_got(repo, tmp_path, monkeypatch):
    """A cloud run lands lines in runs.jsonl, the journal and the question log through its own pull request.
    This checkout cannot merge them in: its runs.jsonl holds a line the scan leaves local, so the file stays
    changed and git will not merge over it. A protected change waiting splits the push, and the plain part,
    built on main's tip, must carry only what this checkout changed: never its older copy of a file main moved.
    The journal lines wait first (BATCH_HOURS), then go out; after both pull requests merge, every cloud line
    is still on main, and today's note, which both sides wrote, waits here for the merge instead of
    overwriting main's."""
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    host = sync.hostname()
    runs, log = "_meta/state/runs.jsonl", "_meta/questions.log"
    today, yesterday = "journal/2026/10/2026-10-28.md", "journal/2026/10/2026-10-27.md"
    first = json.dumps({"routine": "daily", "at": "2026-10-28T06:00:00Z"}) + "\n"
    for rel, text in ((runs, first), (today, "# 2026-10-28\n\n## 06:00Z — start\n"), (log, "q0\n"),
                      (yesterday, "# 2026-10-27\n")):
        write(repo / rel, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "day")
    git(repo, "push", "-q", "origin", "main")
    proof = json.dumps({"routine": "cloud-proof", "chain": "sweep", "at": "2026-10-28T20:32:00Z"}) + "\n"
    on_origin_main(tmp_path, {runs: proof, today: "\n## 20:32Z — cloud sweep\n", log: "q-cloud\n"})
    # this checkout's own day: a protected change, journal entries, a run line the scan leaves local
    write(repo / "space.yaml", (repo / "space.yaml").read_text(encoding="utf-8") + "x: 1\n", minutes_old=20)
    for rel, entry in ((today, "\n## 20:30Z — here\n"), (yesterday, "\n## 23:50Z — late\n")):
        write(repo / rel, (repo / rel).read_text(encoding="utf-8") + entry, minutes_old=20)
    flagged = json.dumps({"routine": "sweep-chain", "at": "2026-10-28T20:00:00Z",
                          "notes": {"gaps": "pass" + "word=" + "Tr0ub4dor3x"}}) + "\n"
    write(repo / runs, first + flagged, minutes_old=20)
    stamp = repo / ".git" / "sync-quiet-pushed"
    stamp.write_text(str(time.time()), encoding="utf-8")   # pushed lately: journal lines and run logs wait
    assert sync.main(["--root", str(repo)]) == 0
    notes = run_lines(repo)[-1]["notes"]
    assert notes.get("conflict") is True and notes.get("scan_hits")
    assert notes.get("waiting") == "journal lines and run logs only"
    assert branch_paths(repo, f"sync/{host}-protected") == ["space.yaml"]
    assert branch_paths(repo, f"sync/{host}") == []
    stamp.write_text(str(time.time() - (sync.BATCH_HOURS + 0.1) * 3600), encoding="utf-8")
    assert sync.main(["--root", str(repo)]) == 0
    assert run_lines(repo)[-1]["notes"].get("conflict") is True
    assert branch_paths(repo, f"sync/{host}") == [yesterday]   # what only this checkout changed
    for branch in (f"sync/{host}", f"sync/{host}-protected"):   # green, then the owner's click
        platform_merges(tmp_path, branch)
    git(repo, "fetch", "-q", "origin")
    on_main = {rel: git(repo, "show", f"origin/main:{rel}").stdout for rel in (runs, today, log, yesterday)}
    assert proof in on_main[runs]
    assert "## 20:32Z — cloud sweep" in on_main[today]
    assert "q-cloud" in on_main[log]
    assert "## 23:50Z — late" in on_main[yesterday]
    assert "x: 1" in git(repo, "show", "origin/main:space.yaml").stdout
    assert "## 20:30Z — here" in (repo / today).read_text(encoding="utf-8")   # waits here, nothing lost


def test_the_quiet_clock_moves_only_when_something_is_pushed(repo, monkeypatch):
    """A run that finds its branch already up to date pushes nothing, so the window that holds
    back run lines and journal lines keeps counting from the last real push."""
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    host = sync.hostname()
    stamp = repo / ".git" / "sync-quiet-pushed"
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n", minutes_old=20)
    tips = []
    for _ in range(5):   # until the run lines settle and the branch stops moving
        assert sync.main(["--root", str(repo)]) == 0
        git(repo, "fetch", "-q", "origin")
        tips.append(git(repo, "rev-parse", f"origin/sync/{host}").stdout.strip())
        if len(tips) > 1 and tips[-1] == tips[-2]:
            break
    assert tips[-1] == tips[-2]
    old = str(time.time() - 3 * 3600)
    stamp.write_text(old, encoding="utf-8")
    assert sync.main(["--root", str(repo)]) == 0
    assert stamp.read_text(encoding="utf-8") == old


def test_the_private_state_zone_is_never_staged():
    import sync
    assert "_meta/state/private/" in sync.PRIVATE_ZONES


def test_the_sync_branch_never_carries_the_machines_name(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "Ada-Okafor-Laptop")
    first = sync.hostname()
    assert re.fullmatch(r"m-[0-9a-f]{8}", first) and "ada" not in first and "laptop" not in first
    assert sync.hostname() == first   # stable: the same machine pushes the same branch
    monkeypatch.setattr(socket, "gethostname", lambda: "other-machine")
    assert sync.hostname() != first


def test_the_template_keeps_no_run_lines(repo):
    """A sync run in the template writes no run line, so nothing there grows a diff on its own."""
    write(repo / ".github" / "template-marker", "")
    write(repo / "work" / "notes.md", "# Notes\n\nedited\n")
    assert run(repo) == 0
    assert run_lines(repo) == []


def test_a_failing_answer_test_is_set_aside_so_the_desk_still_lands(repo, monkeypatch):
    """A question no page answers would turn the sync pull request red and hold every desk and
    journal line behind it. The sync sets it aside with a record, lands the rest, and brings
    it back once a page carries its facts."""
    import tests_run
    import yaml
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    answers = repo / "_meta" / "tests" / "answers.yaml"
    write(answers, yaml.safe_dump({"questions": [
        {"question": "Who runs Harbor?", "subject": "harbor", "expect": ["Ben Liu"], "asker": "owner"},
        {"question": "What are the notes?", "subject": "", "expect": ["line one"], "asker": "owner"}]},
        sort_keys=False), minutes_old=20)
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    host = sync.hostname()
    git(repo, "fetch", "-q", "origin")
    pushed = yaml.safe_load(git(repo, "show", f"origin/sync/{host}:_meta/tests/answers.yaml").stdout)
    held = {q["question"]: q.get("held") for q in pushed["questions"]}
    assert held["Who runs Harbor?"]["missing"] == ["Ben Liu"] and held["Who runs Harbor?"]["by"] == "sync"
    assert held["What are the notes?"] is None
    assert "line two" in git(repo, "show", f"origin/sync/{host}:work/notes.md").stdout
    report = tests_run.answers(repo, record=False)
    assert report.failed == 0 and report.passed == 1
    assert any("Who runs Harbor?" in note and "Ben Liu" in note for note in report.skipped)
    assert run_lines(repo)[-1]["notes"]["held_tests"] == ["Who runs Harbor?"]
    write(repo / "work" / "harbor.md", "# Harbor\n\nBen Liu runs it.\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    git(repo, "fetch", "-q", "origin")
    pushed = yaml.safe_load(git(repo, "show", f"origin/sync/{host}:_meta/tests/answers.yaml").stdout)
    assert all(not q.get("held") for q in pushed["questions"])
    assert tests_run.answers(repo, record=False).passed == 2


class FakeGh:
    """The platform as the sync sees it, through REST (`gh api`) and `gh run`: the open sync pull
    requests, their checks and their failed logs. By default one open sync pull request, #7,
    whose checks failed; `pulls={}` starts with none, and a POST opens one whose checks pass.
    `state = "MERGED"` merges them all."""

    RED = "abc123"

    def __init__(self, log: str, pulls: dict | None = None):
        self.log, self.state, self.calls = log, "OPEN", []
        # number: (head branch, head sha)
        self.pulls = {7: (f"sync/{sync.hostname()}", self.RED)} if pulls is None else dict(pulls)

    def __call__(self, *args):   # set on the class, an instance is not bound: no self of the run
        self.calls.append(args)
        route = next((a for a in args if a.startswith("repos/")), "")
        slug, out = "/".join(route.split("/")[1:3]), ""
        if args[:3] == ("api", "-X", "POST") and re.fullmatch(r"repos/[^/]+/[^/]+/pulls", route):
            number = max(self.pulls, default=11) + 1
            head = next(a[len("head="):] for a in args if a.startswith("head="))
            self.pulls[number] = (head, f"sha{number}")
            out = json.dumps({"number": number})
        elif re.fullmatch(r"repos/[^/]+/[^/]+/pulls\?.*", route):
            wanted = route.split("&head=")[1].split(":", 1)[1] if "&head=" in route else None
            out = json.dumps([] if self.state == "MERGED" else [
                {"number": n, "title": "chore: sync", "draft": False, "auto_merge": None,
                 "html_url": f"https://github.com/{slug}/pull/{n}", "created_at": "2026-10-27T07:10:00Z",
                 "head": {"ref": ref, "sha": sha, "repo": {"full_name": slug}},
                 "base": {"repo": {"full_name": slug}}}
                for n, (ref, sha) in self.pulls.items() if wanted in (None, ref)])
        elif m := re.fullmatch(r"repos/[^/]+/[^/]+/pulls/(\d+)", route):
            merged = self.state == "MERGED"
            out = json.dumps({"number": int(m[1]), "state": "closed" if merged else "open", "merged": merged,
                              "mergeable_state": "unknown" if merged else "clean",
                              "head": {"sha": self.pulls.get(int(m[1]), ("", ""))[1]}})
        elif m := re.fullmatch(r"repos/[^/]+/[^/]+/commits/([^/]+)/check-runs\?.*", route):
            link = "https://github.com/o/r/actions/runs/123/job/456"
            out = json.dumps({"check_runs": [{"name": "checks", "details_url": link,
                                              "conclusion": "failure" if m[1] == self.RED else "success"}]})
        elif args[:2] == ("run", "view"):
            out = self.log
        elif args[:2] != ("run", "rerun"):
            return subprocess.CompletedProcess(["gh", *args], 1, "", "unknown command")
        return subprocess.CompletedProcess(["gh", *args], 0, out, "")


def test_a_red_sync_pull_request_from_an_answer_test_heals_with_no_human_step(repo, monkeypatch):
    """The checks fail an answer test the local run passed (a page CI does not see): the sync
    reads the failed log, sets that question aside for CI, lands again, and once the pull
    request merges says it fixed it, for the briefing."""
    import yaml
    answers = repo / "_meta" / "tests" / "answers.yaml"
    write(answers, yaml.safe_dump({"questions": [
        {"question": "What are the notes?", "subject": "", "expect": ["line one"], "asker": "owner"}]},
        sort_keys=False), minutes_old=20)
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n", minutes_old=20)
    gh = FakeGh("checks\tTests as data\t2026-10-21T18:24:14Z answers: 0 passed, 1 failed, 0 skipped\n"
                "checks\tTests as data\t2026-10-21T18:24:14Z   FAIL What are the notes? (both, in ): "
                "missing ['line one']\n")
    monkeypatch.setattr(sync.Run, "gh", gh)
    assert sync.main(["--root", str(repo)]) == 0
    host = sync.hostname()
    git(repo, "fetch", "-q", "origin")
    pushed = yaml.safe_load(git(repo, "show", f"origin/sync/{host}:_meta/tests/answers.yaml").stdout)
    held = pushed["questions"][0]["held"]
    assert held["by"] == "ci" and held["missing"] == ["line one"]
    assert run_lines(repo)[-1]["notes"]["healing"]["held_tests"] == ["What are the notes?"]
    gh.state = "MERGED"
    assert sync.main(["--root", str(repo)]) == 0
    healed = run_lines(repo)[-1]["notes"]["healed"]
    assert healed["pr"] == 7 and "What are the notes?" in healed["what"]
    assert sync.main(["--root", str(repo)]) == 0
    assert "healed" not in run_lines(repo)[-1].get("notes", {})


def test_a_red_sync_pull_request_with_no_known_cause_is_rerun_once(repo, monkeypatch):
    gh = FakeGh("checks\tSet up job\t2026-10-21T18:24:14Z ##[error]The runner lost its connection\n")
    monkeypatch.setattr(sync.Run, "gh", gh)
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    assert [c for c in gh.calls if c[:2] == ("run", "rerun")] == [("run", "rerun", "123", "--failed")]
    assert run_lines(repo)[-1]["notes"]["healing"]["rerun"] == ["123"]
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\nline three\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0   # the same head is still red: not rerun again
    assert len([c for c in gh.calls if c[:2] == ("run", "rerun")]) == 1
    assert run_lines(repo)[-1]["notes"]["pr_red"] == {"pr": 7, "checks": ["checks"]}


def test_the_sync_never_calls_graphql(repo, monkeypatch):
    """A cloud run refuses GraphQL, which every `gh pr` command is: the sync reads and opens its
    pull request, reads its checks and reruns them through `gh api` and `gh run` only."""
    gh = FakeGh("")
    monkeypatch.setattr(sync.Run, "gh", gh)
    write(repo / "work" / "notes.md", "# Notes\n\nline two\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    assert gh.calls and all(c[0] in ("api", "run") and "graphql" not in c for c in gh.calls)


def test_a_sync_pull_request_opens_through_rest_once(repo, monkeypatch):
    """No sync pull request open: one POST into main from this machine's branch. Once it is open,
    the next run that pushes finds it and opens no second one."""
    gh = FakeGh("", pulls={})
    monkeypatch.setattr(sync.Run, "gh", gh)
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    posts = [c for c in gh.calls if c[:3] == ("api", "-X", "POST")]
    assert len(posts) == 1 and posts[0][3] == f"repos/{ghrest.repo_slug(repo)}/pulls"
    assert {f"head=sync/{sync.hostname()}", "base=main", "title=chore: sync"} <= set(posts[0])
    assert run_lines(repo)[-1]["notes"]["pr"] == "opened"
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\nline three\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    assert len([c for c in gh.calls if c[:3] == ("api", "-X", "POST")]) == 1
    notes = run_lines(repo)[-1]["notes"]
    assert notes.get("pushed") and "pr" not in notes


def test_open_pr_answers_the_number_of_the_open_or_new_pull_request(repo, monkeypatch):
    monkeypatch.setattr(sync.Run, "gh", FakeGh("", pulls={}))
    run = sync.Run(repo, repo / ".git", sync.parse(["--root", str(repo)]))
    assert run.slug == ghrest.repo_slug(repo) and run.slug
    assert run.open_pr("sync/m-0000aaaa") == 12 and run.notes == {"pr": "opened"}
    run.notes.clear()
    assert run.open_pr("sync/m-0000aaaa") == 12 and run.notes == {}   # found open: not opened again


def test_without_gh_the_run_line_says_so_and_nothing_fails(repo, monkeypatch):
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    notes = run_lines(repo)[-1]["notes"]
    assert notes["gh"] == "absent" and not notes.get("errors")


def test_an_origin_that_names_no_repository_is_said_once(repo, monkeypatch):
    """A protected change splits the push into two branches, and origin names no repository to
    ask: one error for the run, not one per branch, and GitHub is never called (gh is asked only
    whether it is there)."""
    gh = FakeGh("", pulls={})
    monkeypatch.setattr(sync.Run, "gh", gh)
    monkeypatch.setattr(ghrest, "repo_slug", lambda root: None)
    write(repo / "space.yaml", (repo / "space.yaml").read_text(encoding="utf-8") + "x: 1\n", minutes_old=20)
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 1
    assert run_lines(repo)[-1]["notes"]["errors"] == ["no GitHub repository behind origin"]
    assert gh.calls == [("--version",)]


def test_sync_rewrites_the_page_every_run(repo, monkeypatch):
    import onboarding_page
    refreshed = []
    monkeypatch.setattr(onboarding_page, "refresh", lambda root: refreshed.append(Path(root)))
    assert run(repo) == 0
    assert refreshed == [repo.resolve()]
    assert run(repo) == 0 and refreshed == [repo.resolve()] * 2   # nothing to land: the page is still rewritten


def test_a_page_that_cannot_be_refreshed_never_fails_the_sync(repo, monkeypatch, capsys):
    import onboarding_page

    def broken(root):
        raise RuntimeError("the page could not be written")
    monkeypatch.setattr(onboarding_page, "refresh", broken)
    write(repo / "work" / "old.md", "settled\n", minutes_old=15)
    assert run(repo) == 0
    assert "work/old.md" in head_files(repo)   # the landing happened
    out = capsys.readouterr().out
    assert out.count("sync: the progress page could not be updated (RuntimeError)") == 1   # one plain line
    assert not (repo / ".git" / "sync.lock").exists()   # and the lock is gone


def test_the_page_is_left_alone_by_a_dry_run_and_a_structure_landing(repo, monkeypatch):
    import onboarding_page
    refreshed = []
    monkeypatch.setattr(onboarding_page, "refresh", lambda root: refreshed.append(root))
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n", minutes_old=15)
    assert run(repo, "--dry-run") == 0
    assert run(repo, "--only", "work/notes.md") == 0
    assert refreshed == []


def test_a_page_that_cannot_be_written_is_one_plain_line_and_the_sync_goes_on(repo, capsys):
    import onboarding_page
    onboarding_page.page_path().mkdir(parents=True)   # a folder where the page should be, in the test's fake home
    write(repo / "work" / "old.md", "settled\n", minutes_old=15)
    assert run(repo) == 0
    out = capsys.readouterr().out
    assert out.count("the progress page could not be updated") == 1
    assert "work/old.md" in head_files(repo) and not (repo / ".git" / "sync.lock").exists()


# ----- one computer runs the routines: two claims made before a sync (Task 14) ----------------------
from conftest import NOW   # noqa: E402

TEMPLATE = Path(sync.__file__).resolve().parents[2]
template_only = pytest.mark.skipif(not (TEMPLATE / ".github" / "template-marker").exists(),
                                   reason="reads the template's own .gitattributes")


def claimed(clone: Path, machine: str, name: str, monkeypatch) -> None:
    """`clone`'s computer records itself as the one that runs the routines, and an earlier sync committed it."""
    import routine
    monkeypatch.setattr(routine, "this_machine", lambda: machine)
    monkeypatch.setattr(routine, "machine_name", lambda: name)
    routine.claim_host(clone, "os", NOW)
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", f"routines on {name}")


def test_two_claims_made_before_a_sync_leave_nobody_running_and_nothing_stuck(repo, tmp_path, monkeypatch):
    import onboarding_page
    import routine
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(other))
    for key, value in (("user.name", "t"), ("user.email", "t@example.test"), ("commit.gpgsign", "false")):
        git(other, "config", key, value)
    claimed(other, "m-laptop", "LAPTOP", monkeypatch)
    git(other, "push", "-q", "origin", "main")   # the laptop's claim landed first
    claimed(repo, "m-desktop", "DESK", monkeypatch)   # the desktop claimed before it heard of it
    assert run(repo) == 0
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    assert not any(line["notes"].get("conflict") for line in run_lines(repo))   # the sync goes on: nothing is wedged
    git(repo, "push", "-q", "origin", "main")   # the desktop's merge lands, as its pull request would
    assert run(other) == 0 and not any(line["notes"].get("conflict") for line in run_lines(other))
    for clone, machine in ((repo, "m-desktop"), (other, "m-laptop")):
        monkeypatch.setattr(routine, "this_machine", lambda m=machine: m)
        assert routine.host(clone) == {"unclear": True}
        assert routine.gate(clone, NOW) == routine.UNCLEAR   # neither computer runs a routine: never two
        texts = [r["text"] for r in onboarding_page.sections(clone, {}, NOW)["waiting"]]
        assert onboarding_page.HOST_UNCLEAR in texts
    claimed(repo, "m-desktop", "DESK", monkeypatch)   # "run my routines here" writes the record whole
    assert run(repo) == 0 and routine.host(repo)["machine"] == "m-desktop"


def test_the_record_merge_rule_is_written_into_the_spaces_own_git_once(repo):
    attributes = repo / ".git" / "info" / "attributes"
    attributes.parent.mkdir(parents=True, exist_ok=True)
    attributes.write_text("*.bin binary", encoding="utf-8")   # a rule of the person's own, with no newline at its end
    assert run(repo, "--dry-run") == 0 and attributes.read_text(encoding="utf-8") == "*.bin binary"
    assert run(repo) == 0 and run(repo) == 0
    assert attributes.read_text(encoding="utf-8") == "*.bin binary\n" + "".join(f"{r}\n" for r in sync.MERGE_RULES)


def test_an_attributes_file_that_is_not_utf8_still_gets_the_rule_and_the_sync_runs(repo):
    """A byte the person's own attributes file can't decode never stops the sync: the rule is appended all the same."""
    attributes = repo / ".git" / "info" / "attributes"
    attributes.parent.mkdir(parents=True, exist_ok=True)
    own = b"*.bin binary # caf\xe9\n"   # Latin-1, not UTF-8
    attributes.write_bytes(own)
    assert run(repo) == 0
    assert attributes.read_bytes() == own + "".join(f"{r}\n" for r in sync.MERGE_RULES).encode("utf-8")

@template_only
def test_the_template_merges_the_routine_record_and_the_friction_log_by_union():
    rules = (TEMPLATE / ".gitattributes").read_text(encoding="utf-8").splitlines()
    for path in ("_meta/state/routine-host.yaml", "_meta/friction-log.md"):
        assert any(line.split() == [path, "merge=union"] for line in rules)
    assert sync.MERGE_RULES[0] == "_meta/state/routine-host.yaml merge=union"
