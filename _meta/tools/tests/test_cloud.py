"""cloud.py: a cloud run's begin, the scheduled task it runs as, and its proof line (spec 4.1, 4.2, 4.6, 4.7, 4.9,
4.11). A cloud run's machine is a folder standing in for /home/user (`SPACES_CLOUD_HOME`) that holds Ada's workspace
clone, cloned from a throwaway bare origin as the platform clones it; the home folder, where begin writes the guard
profile and the state the guard plugin reads, is conftest's."""
import datetime as dt
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

import bash_guard
import cloud
import onboard
import pages
import routine
import send_guard
import sync
from conftest import make_space

TEMPLATE = Path(__file__).resolve().parents[3]
NOW = dt.datetime(2026, 10, 27, 9, 0, tzinfo=dt.timezone.utc)
TASKS = {"sweep": "trig_01AAAAAAAA", "daily": "trig_01BBBBBBBB"}
MAIL_READ, MAIL_SEARCH = "mcp__mail__get_message", "mcp__mail__search_threads"
MAIL_SEND, MAIL_TRASH = "mcp__mail__send_message", "mcp__mail__trash_message"
TRACKER_READ, TRACKER_WRITE, TRACKER_DELETE = ("mcp__tracker__list_issues", "mcp__tracker__create_issue",
                                               "mcp__tracker__delete_issue")
GRANT = {"read": [MAIL_READ, MAIL_SEARCH, TRACKER_READ], "write": [TRACKER_WRITE], "send": [MAIL_SEND]}
ITEMS = Path("_inbound") / "needs-you" / "sync-cloud.yaml"
GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false"]


def git(cwd: Path, *args: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *GIT_ID, *args], cwd=str(cwd), check=check, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=dict(os.environ, **env) if env else None)


def put(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)   # git keeps no empty folder: a clone may lack _meta/state
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def commit_all(clone: Path, message: str, when: dt.datetime | None = None) -> str:
    stamp = {"GIT_AUTHOR_DATE": when.isoformat(), "GIT_COMMITTER_DATE": when.isoformat()} if when else None
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", message, env=stamp)
    return git(clone, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """The cloud's /home/user, empty, and git that clones from local folders."""
    config = tmp_path / "gitconfig"
    config.write_text('[protocol "file"]\n\tallow = always\n', encoding="utf-8")
    for key, value in (("GIT_CONFIG_GLOBAL", str(config)), ("GIT_CONFIG_NOSYSTEM", "1"), ("GIT_TERMINAL_PROMPT", "0")):
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("SPACE_NOW", raising=False)
    home = tmp_path / "home-user"
    home.mkdir()
    monkeypatch.setenv("SPACES_CLOUD_HOME", str(home))
    return home


def origin_space(tmp_path: Path, name: str, **extra) -> Path:
    """A space pushed to its own bare origin (`<name>.git`); the checkout that pushed it is `<name>-seed`."""
    origin = tmp_path / f"{name}.git"
    git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    seed = tmp_path / f"{name}-seed"
    git(tmp_path, "clone", "-q", str(origin), str(seed))
    make_space(seed, profile=extra.pop("profile", "personal"), name=name, **extra)
    shutil.copytree(TEMPLATE / "_meta" / "templates" / "routines", seed / "_meta" / "templates" / "routines")
    (seed / ".gitattributes").write_text("_meta/state/*.jsonl merge=union\n* text=auto eol=lf\n", encoding="utf-8")
    commit_all(seed, "init")
    git(seed, "push", "-q", "-u", "origin", "main")
    return seed


def cloned(machine: Path, tmp_path: Path, name: str, slug: str | None = None) -> Path:
    """The platform's fresh clone of `name` in /home/user; with `slug`, its origin is the platform's proxy url for
    that repository on GitHub (a carry stubbed out never fetches from it)."""
    clone = machine / name
    git(tmp_path, "clone", "-q", str(tmp_path / f"{name}.git"), str(clone))
    if slug:
        git(clone, "remote", "set-url", "origin", f"http://local_proxy@127.0.0.1:34567/git/{slug}")
    return clone.resolve()


@pytest.fixture
def ada(machine, tmp_path):
    """Ada's workspace, cloned into the cloud's home, with two declared sources."""
    origin_space(tmp_path, "ada-space", sources=[{"name": "mail"}, {"name": "calls"}])
    return cloned(machine, tmp_path, "ada-space")


def in_the_cloud(ws: Path, since: dt.datetime = NOW - dt.timedelta(hours=1), zone: str = "Europe/Amsterdam") -> dict:
    """The routines moved to the cloud at `since` (an hour before now: neither chain is silent yet)."""
    return routine.claim_cloud(ws, TASKS, zone, ["ada/ada-space"], GRANT, since)


def profile_of(chain: str) -> dict:
    path = Path.home() / ".spaces" / "routines" / f"cloud-{chain}.settings.json"
    return json.loads(path.read_text(encoding="utf-8"))


def items_of(ws: Path) -> list:
    path = ws / ITEMS
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("items") or [] if path.exists() else []


def untouched(ws: Path) -> bool:
    """Nothing a run writes is there: no run line, no profile, no state, no item, no carry's lease."""
    return (not (ws / "_meta" / "state" / "runs.jsonl").exists() and not (ws / ITEMS).exists()
            and not (Path.home() / ".spaces" / "routines").exists()
            and not list((ws / ".git").glob("sync-cloud-lease-*")))


# ----- begin: the gate -------------------------------------------------------------------------------------------
def test_a_run_that_steps_aside_writes_nothing(ada):
    assert cloud.begin(ada, "sweep", now=NOW) == {"stopped": routine.NO_CLOUD}
    routine.claim_host(ada, "os", NOW)   # a computer runs them
    said = cloud.begin(ada, "daily", now=NOW)
    assert said["stopped"].startswith("they run on ") and set(said) == {"stopped"}
    (ada / routine.HOST).unlink()
    assert untouched(ada)


def test_a_paused_cloud_run_steps_aside_in_the_persons_own_time(ada):
    in_the_cloud(ada)
    routine.set_pause(ada, NOW + dt.timedelta(hours=5))
    assert cloud.begin(ada, "sweep", now=NOW) == {"stopped": "paused until 2026-10-27 15:00"}   # Amsterdam, CET
    (ada / routine.HOST).unlink()
    assert untouched(ada)


def test_the_cli_exits_three_and_prints_why_when_it_steps_aside(ada, capsys):
    assert cloud.main(["begin", str(ada), "--chain", "sweep"]) == cloud.STOPPED
    assert json.loads(capsys.readouterr().out) == {"stopped": routine.NO_CLOUD}


# ----- begin: the carry ------------------------------------------------------------------------------------------
def test_begin_carries_the_last_run_before_printing_the_cursors(ada, tmp_path):
    in_the_cloud(ada)
    earlier = tmp_path / "earlier-run"   # the last cloud sweep, whose pull request never merged
    git(tmp_path, "clone", "-q", str(tmp_path / "ada-space.git"), str(earlier))
    marks = {"at": "2026-10-26T08:00:00Z", "feeds": {"mail": "2026-10-26T08:00:00Z", "calls": "2026-10-25T17:00:00Z"}}
    put(earlier / "_meta" / "state" / "sweep-watermark.yaml", yaml.safe_dump(marks))
    head = commit_all(earlier, "chore: sync")
    git(earlier, "push", "-q", "origin", "HEAD:refs/heads/cloud/sweep")
    result = cloud.begin(ada, "sweep", now=NOW, gh=lambda *a: None)
    assert result["roots"] == [ada.as_posix()] and result["carried"] == [ada.as_posix()]
    assert result["cursors"] == {ada.as_posix(): {"mail": "2026-10-26T08:00:00Z", "calls": "2026-10-25T17:00:00Z"}}
    assert (ada / ".git" / "sync-cloud-lease-sweep").read_text(encoding="utf-8") == head


def test_the_cursors_start_from_the_marks_else_ninety_days_back(ada):
    in_the_cloud(ada)
    put(ada / "_meta" / "state" / "sweep-watermark.yaml",
        yaml.safe_dump({"at": "2026-10-20T08:00:00Z", "feeds": {"mail": "2026-10-20T08:00:00Z"}}))
    result = cloud.begin(ada, "sweep", now=NOW)
    assert result["cursors"][ada.as_posix()] == {"mail": "2026-10-20T08:00:00Z", "calls": "2026-07-29T09:00:00Z"}
    assert "cursors" not in cloud.begin(ada, "daily", now=NOW)   # the daily exports nothing


def stub_carry(monkeypatch, **said):
    seen = []

    def carry(root, chain, gh=None):
        seen.append((Path(root), chain))
        return {"carried": False, "head": "0123456789abcdef", **said}
    monkeypatch.setattr(sync, "carry", carry)
    return seen


def test_a_carry_that_conflicts_stops_the_run_with_one_item(ada, monkeypatch):
    in_the_cloud(ada)
    stub_carry(monkeypatch, conflict=True)
    result = cloud.begin(ada, "sweep", now=NOW)
    assert result["stopped"] == "the last cloud run's branch conflicts with main in ada-space"
    [item] = items_of(ada)
    assert item["id"] == "cloud-stopped-sweep-01234567" and result["items"] == [item["id"]]
    assert "conflicts with main" in item["text"] and item["recommendation"] and item["default"]
    assert not (Path.home() / ".spaces" / "routines").exists()   # no profile, no state: the guard lets only begin run
    assert cloud.begin(ada, "sweep", now=NOW)["items"] == [item["id"]] and len(items_of(ada)) == 1   # once


def test_a_carry_that_fails_stops_the_run_too(ada, monkeypatch):
    in_the_cloud(ada)
    stub_carry(monkeypatch, error="the branch could not be fetched")
    result = cloud.begin(ada, "daily", now=NOW)
    assert result["stopped"] == ("the last cloud run's branch could not be brought into ada-space: the branch could "
                                 "not be fetched")
    [item] = items_of(ada)
    assert item["id"].startswith("cloud-stopped-daily-") and "could not be fetched" in item["text"]
    assert "log" in item["recommendation"]   # nothing to merge by hand: the next run tries again
    assert not (Path.home() / ".spaces" / "routines").exists()


def test_a_branch_with_a_protected_change_is_one_security_item_and_the_run_goes_on(ada, monkeypatch):
    in_the_cloud(ada)
    stub_carry(monkeypatch, skipped="protected", protected=2)
    result = cloud.begin(ada, "sweep", now=NOW)
    assert "stopped" not in result and result["carried"] == []
    [item] = items_of(ada)
    assert item["kind"] == "security" and item["id"] == "cloud-branch-sweep-01234567"
    assert "cloud/sweep" in item["text"] and "2 protected files" in item["text"] and "not used" in item["text"]
    assert profile_of("sweep")


def test_a_branch_the_person_closed_is_left_quietly(ada, monkeypatch):
    in_the_cloud(ada)
    stub_carry(monkeypatch, skipped="closed")
    result = cloud.begin(ada, "sweep", now=NOW)
    assert "stopped" not in result and result["carried"] == [] and items_of(ada) == []


def test_a_clean_carry_takes_back_an_earlier_stop(ada, monkeypatch):
    in_the_cloud(ada)
    stub_carry(monkeypatch, conflict=True)
    cloud.begin(ada, "sweep", now=NOW)
    stub_carry(monkeypatch)
    assert cloud.begin(ada, "sweep", now=NOW)["items"] == [] and items_of(ada) == []


def test_every_clone_the_person_owns_is_a_root_the_workspace_first(ada, machine, tmp_path, monkeypatch):
    routine.claim_cloud(ada, TASKS, "Europe/Amsterdam", ["ada/ada-space", "ada/ada-brain", "ada/ada-harbor",
                                                         "ben/ben-space"], GRANT, NOW - dt.timedelta(hours=1))
    origin_space(tmp_path, "ada-brain", profile="brain")
    origin_space(tmp_path, "ada-harbor", profile="project")
    origin_space(tmp_path, "ben-space", owner="Ben Liu")
    cloned(machine, tmp_path, "ada-brain", "ada/ada-brain")
    cloned(machine, tmp_path, "ada-harbor", "ADA/Ada-Harbor")   # GitHub's names know no case
    cloned(machine, tmp_path, "ben-space", "ben/ben-space")
    (machine / "ada-app").mkdir()   # a linked codebase: no space
    seen = stub_carry(monkeypatch)
    result = cloud.begin(ada, "sweep", now=NOW)
    assert result["roots"] == [ada.as_posix(), (machine / "ada-harbor").resolve().as_posix(),
                                (machine / "ada-brain").resolve().as_posix()]
    assert [root.name for root, _ in seen] == ["ada-space", "ada-harbor", "ada-brain"]


def codebase_claiming_a_space(machine: Path, tmp_path: Path, name: str, slug: str) -> Path:
    """A codebase the task attaches for the sweep's repository feed, into which someone who can push to it put a
    space.yaml naming Ada as its owner, and a tool of their own."""
    origin_space(tmp_path, name, profile="project")
    clone = cloned(machine, tmp_path, name, slug)
    put(clone / "_meta" / "tools" / "sweep.py", "print('someone else')\n")
    return clone


def test_a_linked_codebase_that_claims_to_be_a_space_is_no_root(ada, machine, tmp_path, monkeypatch):
    put(ada / "_meta" / "state" / "links.json", json.dumps({"links": 1, "repos": ["ADA/Ada-App"]}))
    space = yaml.safe_load((ada / "space.yaml").read_text(encoding="utf-8"))
    space["sources"].append({"name": "code", "repo": "https://github.com/ada/ada-lib.git"})
    put(ada / "space.yaml", yaml.safe_dump(space, sort_keys=False))
    for name in ("ada-app", "ada-lib"):
        codebase_claiming_a_space(machine, tmp_path, name, f"ada/{name}")
    origin_space(tmp_path, "ada-harbor", profile="project")
    harbor = cloned(machine, tmp_path, "ada-harbor", "ada/ada-harbor")
    put(harbor / "_meta" / "state" / "links.json", json.dumps({"links": 1, "repos": ["ada/ada-site"]}))
    codebase_claiming_a_space(machine, tmp_path, "ada-site", "ada/ada-site")   # a codebase another space links
    seen = stub_carry(monkeypatch)
    result = cloud.begin(ada, "sweep", proof=True, now=NOW)   # a proof run: no record to read the spaces from
    assert result["roots"] == [ada.as_posix(), harbor.as_posix()]
    assert [root.name for root, _ in seen] == ["ada-space", "ada-harbor"]
    allowed = json.dumps(profile_of("sweep")["permissions"]["allow"])
    assert not any(name in allowed for name in ("ada-app", "ada-lib", "ada-site"))


def test_once_moved_only_the_spaces_the_move_recorded_are_roots(ada, machine, tmp_path, monkeypatch):
    routine.claim_cloud(ada, TASKS, "Europe/Amsterdam", ["ada/ada-space", "ada/ada-harbor"], GRANT,
                        NOW - dt.timedelta(hours=1))
    origin_space(tmp_path, "ada-harbor", profile="project")
    harbor = cloned(machine, tmp_path, "ada-harbor", "ada/ada-harbor")
    codebase_claiming_a_space(machine, tmp_path, "ada-app", "ada/ada-app")   # attached, but no space the move kept
    stub_carry(monkeypatch)
    assert cloud.begin(ada, "daily", now=NOW)["roots"] == [ada.as_posix(), harbor.as_posix()]


# ----- begin: the guard profile ----------------------------------------------------------------------------------
def test_begin_writes_the_guard_profile_from_the_records_grant(ada):
    in_the_cloud(ada)
    cloud.begin(ada, "sweep", now=NOW)
    prof = profile_of("sweep")
    allow, deny = prof["permissions"]["allow"], prof["permissions"]["deny"]
    assert "Bash(gh api search/issues?*)" in allow and "Bash(gh api repos/*)" in allow
    assert {MAIL_READ, MAIL_SEARCH, TRACKER_READ, TRACKER_WRITE, MAIL_SEND} <= set(allow)
    assert MAIL_TRASH not in json.dumps(allow)
    assert {"Bash(gh api * *)", "Bash(gh api *\t*)", "Bash(gh api *graphql*)"} <= set(deny)
    hooks = {entry["matcher"] for entry in prof["hooks"]["PreToolUse"]}
    assert {"Bash", MAIL_SEND, routine.TASK_MATCHER, routine.CONNECTOR_MATCHER} <= hooks
    state = json.loads((Path.home() / ".spaces" / "routines" / "cloud.json").read_text(encoding="utf-8"))
    assert state == {"workspace": ada.as_posix(), "profile": (Path.home() / ".spaces" / "routines"
                                                                / "cloud-sweep.settings.json").as_posix(),
                     "person": "owner@example.test", "chain": "sweep"}


def test_the_daily_profile_holds_only_the_send_and_its_read_back(ada):
    in_the_cloud(ada)
    cloud.begin(ada, "daily", now=NOW)
    allow = profile_of("daily")["permissions"]["allow"]
    assert {MAIL_SEND, MAIL_READ} <= set(allow)
    assert not {MAIL_SEARCH, TRACKER_READ, TRACKER_WRITE} & set(allow)


# The reads a cloud run's repository feed makes, one endpoint word each, and calls that write to GitHub however the
# write is spelled: a flag after a space, joined to another (-iXPUT), quoted, after a tab, out of a brace.
GH_READS = ("gh api repos/ada/ada-space/pulls", "gh api repos/ada/ada-space/pulls/5/files",
            "gh api search/issues?q=repo:ada/ada-space+is:pr")
GH_WRITES = ("gh api -X POST repos/ada/ada-space/pulls -f title=x",
             "gh api repos/ada/ada-space/pulls -X POST",
             "gh api repos/ada/ada-space/pulls/5/merge -iXPUT",
             "gh api repos/ada/ada-space/pulls/5/merge '-XPUT'",
             'gh api repos/ada/ada-space/pulls/5/merge "-XPUT"',
             "gh api repos/ada/ada-space/pulls/5/merge\t-XPUT",
             "gh api repos/ada/ada-space/pulls/5/merge {-XPUT,}",
             "gh api repos/ada/ada-space/branches/main/protection/enforce_admins '-X' DELETE",
             "gh api repos/ada/ada-space/contents/rules.md '-XPUT' '-fmessage=x' '-fcontent=eA==' '-fbranch=main'",
             "gh api repos/ada/ada-space/pulls\t-ftitle=x\t-fhead=cloud/sweep\t-fbase=main",
             "gh api repos/ada/ada-space/pulls --paginate",
             "gh api graphql -f query=x")


def load_guard():
    spec = importlib.util.spec_from_file_location("spaces_guard", TEMPLATE / "_meta" / "plugins" / "spaces-guard"
                                                  / "guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_guard_plugin_holds_a_run_to_what_begin_wrote(ada):
    tools = ada / "_meta" / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    shutil.copy(bash_guard.__file__, tools / "bash_guard.py")
    shutil.copy(send_guard.__file__, tools / "send_guard.py")
    in_the_cloud(ada)
    guard, env = load_guard(), {"CLAUDE_CODE_REMOTE": "true", "HOME": str(Path.home())}

    def decide(mode, name, tool_input):
        raw = json.dumps({"hook_event_name": "PreToolUse", "tool_name": name, "tool_input": tool_input}).encode()
        return guard.main([mode], raw, env)
    begin = {"command": cloud.begin_command("ada-space", "daily")}
    assert decide("bash", "Bash", {"command": 'python3 -c "print(1)"'}) == guard.BLOCK   # before begin: begin only
    assert decide("bash", "Bash", begin) == 0
    cloud.begin(ada, "daily", now=NOW)
    cursor = f"python3 {ada.as_posix()}/_meta/tools/sweep.py {ada.as_posix()} --cursor mail"
    assert decide("bash", "Bash", {"command": cursor}) == 0
    assert decide("bash", "Bash", {"command": 'python3 -c "print(1)"'}) == guard.BLOCK
    for read in GH_READS:
        assert decide("bash", "Bash", {"command": read}) == 0, read
    for write in GH_WRITES:
        assert decide("bash", "Bash", {"command": write}) == guard.BLOCK, write
    assert decide("connector", MAIL_READ, {}) == 0 and decide("connector", MAIL_TRASH, {}) == guard.BLOCK
    assert decide("connector", TRACKER_WRITE, {}) == guard.BLOCK   # the daily writes no issue
    assert decide("send", MAIL_SEND, {"to": "nobody@example.test", "body": "x"}) == guard.BLOCK
    assert decide("send", MAIL_SEND, {"to": "owner@example.test", "body": "x"}) == 0


def test_the_begin_command_is_the_one_the_guard_lets_through_first(ada):
    guard = load_guard()
    for chain in cloud.CHAINS:
        for proof in (False, True):
            command = cloud.begin_command("ada-space", chain, proof)
            assert guard.BEGIN.fullmatch(command), command
            args = cloud.parse(command.split()[2:])
            assert (args.command, args.workspace, args.chain, args.proof) == ("begin", "/home/user/ada-space",
                                                                              chain, proof)


# ----- begin: a proof run, and mounts ----------------------------------------------------------------------------
def test_a_proof_begin_skips_only_the_runner_check_and_exports_nothing(ada, monkeypatch):
    seen = stub_carry(monkeypatch)
    result = cloud.begin(ada, "sweep", proof=True, now=NOW)   # no record yet: a proof runs before the claim
    assert "stopped" not in result and seen == [(ada, "sweep")]
    assert result["cursors"] == {ada.as_posix(): {"mail": pages.iso(NOW), "calls": pages.iso(NOW)}}
    assert profile_of("sweep")["permissions"]["deny"] and result["items"] == []


def add_mount(ws: Path, tmp_path: Path, path: str, url: str, pin: str) -> None:
    """A mount at `path` pinned to `pin`, as a person's click landed it: the .gitmodules entry and the gitlink."""
    git(ws, "config", "-f", ".gitmodules", f"submodule.{path}.path", path)
    git(ws, "config", "-f", ".gitmodules", f"submodule.{path}.url", url)
    git(ws, "update-index", "--add", "--cacheinfo", f"160000,{pin},{path}")
    git(ws, "add", ".gitmodules")
    git(ws, "commit", "-q", "-m", "mount")
    git(ws, "push", "-q", "origin", "main")


def brain_origin(tmp_path: Path, dates: list) -> list:
    """A mounted repository whose main gained one commit at each date; the commits."""
    seed = origin_space(tmp_path, "ada-brain", profile="brain")
    done = []
    for n, when in enumerate(dates):
        (seed / f"note-{n}.md").write_text(f"{n}\n", encoding="utf-8")
        done.append(commit_all(seed, f"note {n}", when))
    git(seed, "push", "-q", "origin", "main")
    return done


def test_a_mount_that_is_not_attached_is_named_not_failed(ada, tmp_path):
    in_the_cloud(ada)
    pins = brain_origin(tmp_path, [NOW - dt.timedelta(days=1)])
    add_mount(ada, tmp_path, "brain", (tmp_path / "ada-brain.git").as_uri(), pins[0])
    add_mount(ada, tmp_path, "mounts/harbor", (tmp_path / "nowhere.git").as_uri(), pins[0])
    result = cloud.begin(ada, "sweep", now=NOW)
    assert "stopped" not in result
    assert result["unreachable"] == [f"{ada.as_posix()}/mounts/harbor"]
    assert (ada / "brain" / "note-0.md").exists()   # attached: read at its pin


def test_a_mount_is_read_at_its_pin(ada, tmp_path):
    in_the_cloud(ada)
    pins = brain_origin(tmp_path, [NOW - dt.timedelta(days=2), NOW - dt.timedelta(days=1)])
    add_mount(ada, tmp_path, "brain", (tmp_path / "ada-brain.git").as_uri(), pins[0])
    cloud.begin(ada, "sweep", now=NOW)
    assert git(ada / "brain", "rev-parse", "HEAD").stdout.strip() == pins[0]
    assert (ada / "brain" / "note-0.md").exists() and not (ada / "brain" / "note-1.md").exists()


# ----- begin: a run that did nothing (4.11) ----------------------------------------------------------------------
def run_line_on_main(ws: Path, routine_name: str, at: dt.datetime) -> None:
    path = ws / "_meta" / "state" / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"routine": routine_name, "at": pages.iso(at), "space": "ada-space"}) + "\n")
    git(ws, "add", "--", "_meta/state/runs.jsonl")   # the line alone: a mount's checkout is not its pin
    git(ws, "commit", "-q", "-m", "chore: sync")
    git(ws, "push", "-q", "origin", "HEAD:main")


def test_a_silent_other_chain_is_one_item(ada):
    in_the_cloud(ada, since=NOW - dt.timedelta(days=10))
    run_line_on_main(ada, "daily", NOW - dt.timedelta(hours=60))   # the daily runs once a day: silent past 48 hours
    result = cloud.begin(ada, "sweep", now=NOW)
    [item] = items_of(ada)
    assert item["id"] == "cloud-silent-daily-20261024" and result["items"] == [item["id"]]
    assert item["text"].startswith("The cloud daily chain has not run since 2026-10-24 23:00")   # Amsterdam time
    run_line_on_main(ada, "daily-chain", NOW - dt.timedelta(hours=2))
    assert cloud.begin(ada, "sweep", now=NOW)["items"] == [] and items_of(ada) == []   # it ran again: taken back


def test_a_chain_within_twice_its_longest_gap_is_not_silent(ada):
    in_the_cloud(ada, since=NOW - dt.timedelta(days=10))
    run_line_on_main(ada, "sweep_chain", NOW - dt.timedelta(hours=23))   # the sweep's longest gap is 12 hours
    run_line_on_main(ada, "cloud-proof", NOW - dt.timedelta(hours=1))
    assert cloud.begin(ada, "daily", now=NOW)["items"] == []
    run_line_on_main(ada, "sweep-chain", NOW - dt.timedelta(hours=25))   # an older line changes nothing
    assert cloud.begin(ada, "daily", now=NOW)["items"] == []


def test_a_chain_that_never_ran_is_silent_counted_from_the_move(ada):
    routine.claim_cloud(ada, TASKS, "Europe/Amsterdam", ["ada/ada-space"], GRANT, NOW - dt.timedelta(hours=20))
    assert cloud.begin(ada, "daily", now=NOW)["items"] == []   # moved 20 hours ago: not yet
    routine.claim_cloud(ada, TASKS, "Europe/Amsterdam", ["ada/ada-space"], GRANT, NOW - dt.timedelta(hours=30))
    result = cloud.begin(ada, "daily", now=NOW)
    [item] = items_of(ada)
    assert result["items"] == [item["id"]] == ["cloud-silent-sweep-20261026"]
    assert item["text"].startswith("The cloud sweep has not run since 2026-10-26 04:00")


def test_a_pin_older_than_three_days_is_one_item(ada, tmp_path):
    in_the_cloud(ada, since=NOW - dt.timedelta(days=10))
    run_line_on_main(ada, "sweep_chain", NOW - dt.timedelta(hours=2))
    run_line_on_main(ada, "daily", NOW - dt.timedelta(hours=3))
    pins = brain_origin(tmp_path, [NOW - dt.timedelta(days=9), NOW - dt.timedelta(days=5)])
    add_mount(ada, tmp_path, "brain", (tmp_path / "ada-brain.git").as_uri(), pins[0])
    assert cloud.begin(ada, "sweep", now=NOW)["items"] == []   # the sweep does not read the pins
    result = cloud.begin(ada, "daily", now=NOW)
    [item] = items_of(ada)
    assert item["id"] == "cloud-stale-pin-20261022" and result["items"] == [item["id"]]
    assert "ada-space/brain" in item["text"] and "3 days" in item["text"]


def test_a_pin_behind_by_less_than_three_days_or_at_the_tip_is_no_item(ada, tmp_path):
    in_the_cloud(ada, since=NOW - dt.timedelta(days=10))
    run_line_on_main(ada, "sweep_chain", NOW - dt.timedelta(hours=2))
    pins = brain_origin(tmp_path, [NOW - dt.timedelta(days=9), NOW - dt.timedelta(days=2)])
    add_mount(ada, tmp_path, "brain", (tmp_path / "ada-brain.git").as_uri(), pins[0])
    assert cloud.begin(ada, "daily", now=NOW)["items"] == []   # behind for two days only
    add_mount(ada, tmp_path, "brain", (tmp_path / "ada-brain.git").as_uri(), pins[1])
    later = NOW + dt.timedelta(days=30)   # an old brain nobody changed is not a stale pin
    run_line_on_main(ada, "sweep_chain", later - dt.timedelta(hours=2))
    assert cloud.begin(ada, "daily", now=later)["items"] == []


# ----- the trigger -----------------------------------------------------------------------------------------------
@pytest.fixture
def computer(tmp_path, monkeypatch):
    """Ada's workspace on her computer, whose origin is on GitHub, with a brain mount, a linked codebase and a repo
    source; the grant the computer's apps and probe give, and a probe that has seen two tools never granted."""
    ws = make_space(tmp_path / "ada-space", name="ada-space",
                    sources=[{"name": "mail"}, {"name": "code", "repo": "ada/ada-lib"}],
                    mounts=[{"name": "brain", "repo": "https://github.com/ada/ada-brain.git"}])
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    subprocess.run(["git", "-C", str(ws), "remote", "add", "origin", "https://github.com/ada/ada-space.git"],
                   check=True)
    (ws / "_meta" / "state" / "links.json").write_text(json.dumps(
        {"links": 2, "repos": ["ada/ada-app", "https://github.com/ada/ada-site.git"]}), encoding="utf-8")
    import start
    start.render_routines(ws, "Ada Okafor")
    tools = ws / "_meta" / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    for name in ("sweep.py", "sync.py", "cloud.py", "routine.py", "sign.py"):
        (tools / name).write_text("", encoding="utf-8")
    monkeypatch.setattr(routine, "grant", lambda workspace, *a, **k: {
        **GRANT, "reconnect": [], "notes": [], "person": "owner@example.test", "shell": "/bin/sh"})
    probe = Path.home() / ".spaces" / "probe.json"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(json.dumps({"tools": [*GRANT["read"], *GRANT["write"], *GRANT["send"]],
                                 "seen": [MAIL_TRASH, TRACKER_DELETE, "mcp__calendar__delete_event"]}),
                     encoding="utf-8")
    return ws


def context(body: dict) -> dict:
    return body["job_config"]["ccr"]["session_context"]


def test_the_trigger_denies_every_ungranted_tool_by_name(computer):
    body = cloud.trigger(computer, "daily", timezone="Europe/Amsterdam")
    denied = context(body)["disallowed_tools"]
    assert MAIL_TRASH in denied and "WebFetch" in denied and "mcp__github" in denied and "RemoteTrigger" in denied
    assert {MAIL_SEARCH, TRACKER_READ, TRACKER_WRITE} <= set(denied)   # granted, but not to the daily
    assert {TRACKER_DELETE, "mcp__calendar__delete_event"} <= set(denied)   # all the probe saw, as on the computer
    assert not {MAIL_SEND, MAIL_READ} & set(denied)
    assert body["mcp_connections"] == [{"name": "mail", "permitted_tools": ["get_message", "send_message"]}]
    sweep = cloud.trigger(computer, "sweep", timezone="Europe/Amsterdam")
    assert {MAIL_TRASH, TRACKER_DELETE} <= set(context(sweep)["disallowed_tools"])
    assert not set(GRANT["read"] + GRANT["write"] + GRANT["send"]) & set(context(sweep)["disallowed_tools"])
    assert [c["name"] for c in sweep["mcp_connections"]] == ["mail", "tracker"]


def test_the_trigger_denies_the_machinery_the_shell_shapes_and_git(computer):
    denied = context(cloud.trigger(computer, "sweep", timezone="Europe/Amsterdam"))["disallowed_tools"]
    assert {f"Edit(//**/{pattern})" for pattern in routine.PROTECTED_EDITS} <= set(denied)
    # A mount's `.git` is a file naming its git folder, which `.git/**` does not cover: pointed elsewhere, a status
    # in the root would run that folder's config.
    assert "Edit(//**/.git)" in denied
    assert {"Edit(~/.spaces/**)", "Edit(//home/user/.spaces/**)", "Edit(//home/user/ada-space/identity/**)",
            "Edit(//home/user/ada-brain/identity/**)"} <= set(denied)
    # Every dot-entry of the home folder, in both spellings: user site-packages, git's own config, the shell's.
    assert {"Edit(~/.*)", "Edit(~/.*/**)", "Edit(//home/user/.*)", "Edit(//home/user/.*/**)"} <= set(denied)
    assert {"Bash(python -m *)", "Bash(python3 -m *)", "Bash(git *)", "Bash(gh pr *)", "Bash(gh api *graphql*)",
            "Bash(gh api * *)", "Bash(gh api *\t*)",
            "Bash(*_meta/tools/routine.py*)", "Bash(*_meta/tools/sign.py*)"} <= set(denied)
    assert not any(tool in rule for rule in denied for tool in ("sweep.py", "sync.py", "cloud.py"))
    assert not {"Bash", "Read", "Edit", "Write", "Task"} & set(denied)


def test_the_trigger_refuses_every_spelling_of_a_github_write_by_its_text_alone(computer):
    # The platform matches a Bash rule against the command's text only (spec 3): read the rules as the guard does.
    denied = bash_guard._patterns(context(cloud.trigger(computer, "sweep", timezone="Europe/Amsterdam"))
                                  ["disallowed_tools"], False)
    for write in GH_WRITES:
        assert any(rule.fullmatch(write) for rule in denied), write
    for read in GH_READS:
        assert not any(rule.fullmatch(read) for rule in denied), read


def test_the_trigger_enables_the_guard_plugin_and_never_runs_enabled(computer):
    (computer / "_meta" / "state" / "template-origin.yaml").write_text("repo: ada/spaces-template\n", encoding="utf-8")
    body = cloud.trigger(computer, "sweep", timezone="Europe/Amsterdam")
    assert body["enabled"] is False
    assert context(body)["enabled_plugins"] == ["spaces-guard@spaces"]
    assert context(body)["extra_marketplaces"] == {"spaces": {"source": {"source": "github",
                                                                         "repo": "ada/spaces-template"}}}
    assert len(context(body)["auto_mode_soft_deny"]) == 5
    assert "Bash" in context(body)["allowed_tools"] and MAIL_SEND in context(body)["allowed_tools"]


def test_the_trigger_schedule_keeps_the_persons_hours_off_the_hour(computer):
    body = cloud.trigger(computer, "sweep", timezone="Europe/Amsterdam")
    assert body["cron_expression"].startswith("CRON_TZ=Europe/Amsterdam ")
    assert body["cron_expression"].split()[1:] == ["7", "7,13,19", "*", "*", "*"]
    assert cloud.trigger(computer, "daily", timezone="Europe/Amsterdam")["cron_expression"] == \
           "CRON_TZ=Europe/Amsterdam 30 6 * * *"


def test_the_trigger_takes_the_zone_from_the_record_and_refuses_none(computer, monkeypatch):
    routine.claim_cloud(computer, TASKS, "America/New_York", ["ada/ada-space"], GRANT, NOW)
    assert cloud.trigger(computer, "daily")["cron_expression"].startswith("CRON_TZ=America/New_York ")
    (computer / routine.HOST).unlink()
    monkeypatch.setattr(cloud, "local_zone", lambda: None)
    with pytest.raises(ValueError, match="time zone"):
        cloud.trigger(computer, "daily")
    with pytest.raises(ValueError, match="time zone"):
        cloud.trigger(computer, "daily", timezone="Mars/Olympus")


def test_the_sweep_attaches_linked_codebases_and_the_daily_attaches_no_template(computer):
    (computer / "_meta" / "state" / "template-origin.yaml").write_text("repo: ada/spaces-template\n", encoding="utf-8")
    urls = lambda body: [s["git_repository"]["url"] for s in context(body)["sources"]]   # noqa: E731
    sweep = cloud.trigger(computer, "sweep", timezone="Europe/Amsterdam")
    assert urls(sweep) == [f"https://github.com/ada/{name}" for name in
                           ("ada-space", "ada-brain", "ada-app", "ada-site", "ada-lib")]
    daily = cloud.trigger(computer, "daily", timezone="Europe/Amsterdam")
    assert urls(daily) == ["https://github.com/ada/ada-space", "https://github.com/ada/ada-brain"]
    assert not any("template" in url for url in urls(sweep) + urls(daily))


def test_a_repository_named_twice_in_two_cases_is_attached_once_as_first_named(computer):
    subprocess.run(["git", "-C", str(computer), "remote", "set-url", "origin", "https://github.com/ada/Ada-Space.git"],
                   check=True)
    (computer / "_meta" / "state" / "links.json").write_text(json.dumps(
        {"links": 3, "repos": ["ADA/Ada-Brain", "https://github.com/ada/Ada-App.git", "ada/ada-app", "ada/ada-space"]}),
        encoding="utf-8")
    body = cloud.trigger(computer, "sweep", timezone="Europe/Amsterdam")
    assert [s["git_repository"]["url"] for s in context(body)["sources"]] == \
           [f"https://github.com/ada/{name}" for name in ("Ada-Space", "ada-brain", "Ada-App", "ada-lib")]
    assert "Edit(//home/user/Ada-App/identity/**)" in context(body)["disallowed_tools"]   # the clone's own folder


def test_the_zone_of_a_computer_whose_tz_is_no_iana_name_is_where_its_localtime_points(monkeypatch):
    monkeypatch.setattr(os, "readlink", lambda path: "/usr/share/zoneinfo/Europe/Lisbon")
    monkeypatch.setenv("TZ", "CET-1CEST")   # a POSIX rule, of an IANA name's shape
    assert cloud.local_zone() == "Europe/Lisbon"
    monkeypatch.setenv("TZ", ":Europe/Amsterdam")
    assert cloud.local_zone() == "Europe/Amsterdam"
    monkeypatch.setattr(os, "readlink", lambda path: "/usr/share/zoneinfo/Mars/Olympus")
    monkeypatch.delenv("TZ")
    assert cloud.local_zone() is None


def test_the_trigger_prompt_names_the_person_the_clone_and_the_chain_prompt(computer):
    def prompt(body):
        [event] = body["job_config"]["ccr"]["events"]
        assert event["data"]["type"] == "user" and event["data"]["message"]["role"] == "user"
        return event["data"]["message"]["content"]
    body = cloud.trigger(computer, "daily", timezone="Europe/Amsterdam")
    said = prompt(body)
    assert body["name"] == "Ada Okafor's daily"
    assert "Ada Okafor" in said and "/home/user/ada-space" in said
    assert "/home/user/ada-space/_meta/templates/cloud/daily-chain.md" in said and "Proof" not in said
    assert "Proof section only" in prompt(cloud.trigger(computer, "daily", proof=True, timezone="Europe/Amsterdam"))


def test_the_trigger_keeps_what_it_attached_and_granted_for_the_move(computer):
    body = cloud.trigger(computer, "daily", timezone="Europe/Amsterdam")
    kept = json.loads((Path.home() / ".spaces" / "cloud-daily.json").read_text(encoding="utf-8"))
    assert {k: v for k, v in kept.items() if k not in ("roots", "grant")} == json.loads(json.dumps(body))
    assert kept["roots"] == ["ada/ada-space", "ada/ada-brain"]
    assert kept["grant"] == {"read": [MAIL_READ], "write": [], "send": [MAIL_SEND]}
    # The sweep attaches the codebases too, but its roots are the spaces alone: a codebase is never one.
    assert cloud.trigger(computer, "sweep", timezone="Europe/Amsterdam") and json.loads(
        (Path.home() / ".spaces" / "cloud-sweep.json").read_text(encoding="utf-8"))["roots"] == kept["roots"]


def test_a_workspace_without_a_github_repository_renders_no_task(computer):
    subprocess.run(["git", "-C", str(computer), "remote", "remove", "origin"], check=True)
    with pytest.raises(ValueError, match="GitHub"):
        cloud.trigger(computer, "sweep", timezone="Europe/Amsterdam")


def test_the_trigger_cli_prints_the_render(computer, capsys):
    assert cloud.main(["trigger", str(computer), "--chain", "sweep", "--timezone", "Europe/Amsterdam"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["roots"][0] == "ada/ada-space" and printed["enabled"] is False
    assert cloud.main(["trigger", str(computer), "--chain", "sweep", "--timezone", "Nowhere/Zone"]) == 2


# ----- the proof line --------------------------------------------------------------------------------------------
def test_a_proof_line_lands_the_refusals_it_saw(tmp_path):
    ws = make_space(tmp_path / "ada-space")
    line = cloud.proof_line(ws, "daily", guard=True, edit=True, tool=False, send=True, now=NOW)
    assert line == {"routine": "cloud-proof", "chain": "daily", "guard": True, "edit": True, "tool": False,
                    "send": True, "at": "2026-10-27T09:00:00Z"}
    sweep = cloud.proof_line(ws, "sweep", guard=True, edit=True, tool=True, now=NOW)
    assert "send" not in sweep
    runs = (ws / "_meta" / "state" / "runs.jsonl").read_text(encoding="utf-8")
    lines = [json.loads(raw) for raw in runs.splitlines()]
    assert lines == [line, sweep]
    with pytest.raises(ValueError):
        cloud.proof_line(ws, "daily", guard=True, edit=True, tool=True, now=NOW)   # the daily owes the send


def test_the_proof_line_cli_reads_the_workspace_from_the_runs_state(tmp_path, capsys):
    ws = make_space(tmp_path / "ada-space")
    state = Path.home() / ".spaces" / "routines" / "cloud.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"workspace": ws.as_posix(), "profile": "p", "person": "", "chain": "sweep"}),
                     encoding="utf-8")
    assert cloud.main(["proof-line", "--chain", "sweep", "--guard", "yes", "--edit", "yes", "--tool", "no"]) == 0
    assert json.loads(capsys.readouterr().out)["tool"] is False
    assert cloud.main(["proof-line", str(ws), "--chain", "daily", "--guard", "yes", "--edit", "yes",
                       "--tool", "yes"]) == 2   # the daily's proof says whether the send was refused
    assert cloud.main(["proof-line", str(ws), "--chain", "sweep", "--guard", "yes", "--edit", "yes",
                       "--tool", "yes", "--send", "yes"]) == 2


def test_onboard_moves_the_routines_on_what_trigger_and_proof_line_wrote(computer):
    assert cloud.CHAINS == onboard.CLOUD_CHAINS   # the chains sync lands are the ones onboard reads
    for chain in cloud.CHAINS:
        cloud.trigger(computer, chain, timezone="Europe/Amsterdam")
    now = dt.datetime.now(dt.timezone.utc)
    cloud.proof_line(computer, "sweep", guard=True, edit=True, tool=True, now=now - dt.timedelta(hours=1))
    assert onboard.cloud_proofs(computer, now) == {"sweep"}
    cloud.proof_line(computer, "daily", guard=True, edit=True, tool=True, send=True, now=now - dt.timedelta(hours=1))
    assert onboard.cloud_proofs(computer, now) == {"sweep", "daily"} and onboard.cloud_unrefused(computer) == {}
    assert all(onboard._rendered(chain) for chain in cloud.CHAINS)
    done, said, _ = onboard.routines_cloud(computer, TASKS, "Europe/Amsterdam", now)
    assert done, said
    record = routine.host(computer)
    assert record["roots"] == ["ada/ada-space", "ada/ada-brain"]   # the spaces begin may work in, no codebase
    assert record["grant"] == GRANT
    cloud.proof_line(computer, "daily", guard=True, edit=True, tool=True, send=False, now=now)
    assert onboard.cloud_unrefused(computer) == {"daily": ["send"]}
