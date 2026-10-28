"""cloud.py: a cloud sweep's begin, the scheduled task it runs as, and its proof line (spec 4.1, 4.2, 4.6, 4.7, 4.9,
4.11; docs/design/2026-10-28-cloud-sweep-design.md). A cloud run's machine is a folder standing in for /home/user
(`SPACES_CLOUD_HOME`) that holds Ada's workspace clone, cloned from a throwaway bare origin as the platform clones it;
the home folder, where begin writes the run's marker, is conftest's."""
import datetime as dt
import json
import os
import shutil
import subprocess
import zoneinfo
from pathlib import Path

import pytest
import yaml

import bash_guard
import cloud
import onboard
import pages
import routine
import sync
from conftest import make_space

# Windows has no zone data without the tzdata package: there a zone is checked by its shape only, and times are UTC.
# A cloud run is Linux, which has the data; these tests are about what it does with it.
needs_zones = pytest.mark.skipif(not zoneinfo.available_timezones(),
                                 reason="no zone data on this computer (Windows without the tzdata package)")

TEMPLATE = Path(__file__).resolve().parents[3]
NOW = dt.datetime(2026, 10, 27, 9, 0, tzinfo=dt.timezone.utc)
TASKS = {"sweep": "trig_01AAAAAAAA"}
ENV = "env_01Test"   # the cloud environment the task runs in
MAIL_READ, MAIL_SEARCH = "mcp__mail__get_message", "mcp__mail__search_threads"
MAIL_SEND, MAIL_TRASH = "mcp__mail__send_message", "mcp__mail__trash_message"
TRACKER_READ, TRACKER_WRITE, TRACKER_DELETE = ("mcp__tracker__list_issues", "mcp__tracker__create_issue",
                                               "mcp__tracker__delete_issue")
GRANT = {"read": [MAIL_READ, MAIL_SEARCH, TRACKER_READ], "write": [TRACKER_WRITE], "send": [MAIL_SEND]}
READ_ONLY = {"read": [MAIL_READ, MAIL_SEARCH, TRACKER_READ], "write": [], "send": []}   # what the task is given
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
    """The sweep moved to the cloud at `since` (an hour before now)."""
    return routine.claim_cloud(ws, TASKS, zone, ["ada/ada-space"], READ_ONLY, since)


def next_run() -> None:
    """A new run's machine: what the last run's begin wrote in the home folder went with the old one (the clone
    here stands in for what main holds)."""
    shutil.rmtree(Path.home() / ".spaces" / "routines", ignore_errors=True)


def marker() -> dict:
    """The run's marker begin wrote."""
    return json.loads((Path.home() / ".spaces" / "routines" / "cloud.json").read_text(encoding="utf-8"))


def items_of(ws: Path) -> list:
    path = ws / ITEMS
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("items") or [] if path.exists() else []


def untouched(ws: Path) -> bool:
    """Nothing a run writes is there: no run line, no marker, no item, no carry's lease."""
    return (not (ws / "_meta" / "state" / "runs.jsonl").exists() and not (ws / ITEMS).exists()
            and not (Path.home() / ".spaces" / "routines").exists()
            and not list((ws / ".git").glob("sync-cloud-lease-*")))


def landed(ws: Path, left_out: int) -> None:
    """The cloud sync's own line, as it lands a run's lines: how many protected paths it left out."""
    with (ws / "_meta" / "state" / "runs.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"routine": "sync", "cloud": "sweep", "at": "2026-10-27T08:30:00Z", "space": "ada-space",
                             "cost_tokens": 0, "notes": {"staged": 2, "left_out": left_out}}) + "\n")


# ----- begin: the gate -------------------------------------------------------------------------------------------
def test_a_run_that_steps_aside_writes_nothing(ada):
    assert cloud.begin(ada, "sweep", now=NOW) == {"stopped": routine.NO_CLOUD}
    routine.claim_host(ada, "os", NOW)   # a computer runs them
    said = cloud.begin(ada, "sweep", now=NOW)
    assert said["stopped"].startswith("they run on ") and set(said) == {"stopped"}
    (ada / routine.HOST).unlink()
    assert untouched(ada)


def test_only_the_sweep_is_a_cloud_chain(ada):
    assert cloud.CHAINS == ("sweep",)
    with pytest.raises(ValueError, match="no cloud chain"):
        cloud.begin(ada, "daily", now=NOW)
    with pytest.raises(SystemExit):
        cloud.parse(["begin", str(ada), "--chain", "daily"])
    assert untouched(ada)


@needs_zones
def test_a_paused_cloud_run_steps_aside_in_the_persons_own_time(ada):
    in_the_cloud(ada)
    routine.set_pause(ada, NOW + dt.timedelta(hours=5))
    assert cloud.begin(ada, "sweep", now=NOW) == {"stopped": "paused until 2026-10-27 15:00"}   # Amsterdam, CET
    (ada / routine.HOST).unlink()
    assert untouched(ada)


def test_the_cli_exits_three_and_prints_why_when_it_steps_aside(ada, capsys):
    assert cloud.main(["begin", str(ada), "--chain", "sweep"]) == cloud.STOPPED
    assert json.loads(capsys.readouterr().out) == {"stopped": routine.NO_CLOUD}


def test_a_real_run_stays_stopped_while_a_proof_shows_a_refusal_missing(ada, capsys):
    """While a proof on main shows a refusal missing that no later proof showed again, the sweep is stopped, and
    begin holds that in code: a real run stops, writing nothing, with the sentence the person reads. A proof run still
    begins, so a proof that shows every refusal again can end the stop; one that shows another missing cannot, and
    no landing, whatever it left out, ends it."""
    in_the_cloud(ada)
    put(ada / "_meta" / "state" / "runs.jsonl", json.dumps(
        {"routine": "cloud-proof", "chain": "sweep", "edit": True, "tool": False, "at": "2026-10-27T08:00:00Z"}) + "\n")
    said = onboard.unrefused_sentence(ada, {"sweep": ["tool"]})
    assert said.startswith("Your sweep is stopped")
    assert cloud.begin(ada, "sweep", now=NOW) == {"stopped": said}
    assert items_of(ada) == [] and not (Path.home() / ".spaces" / "routines").exists()
    assert cloud.main(["begin", str(ada), "--chain", "sweep"]) == cloud.STOPPED
    assert json.loads(capsys.readouterr().out) == {"stopped": said}
    assert "stopped" not in cloud.begin(ada, "sweep", proof=True, now=NOW)   # a re-proof runs
    next_run()
    # the tool is refused again, but the request went through: the stop stays, now for both
    cloud.proof_line(ada, "sweep", edit=True, tool=True, network=False, now=NOW)
    both = onboard.unrefused_sentence(ada, {"sweep": ["tool", "network"]})
    assert cloud.begin(ada, "sweep", now=NOW) == {"stopped": both}
    landed(ada, left_out=1)   # a landing that left a protected file out proves nothing either way
    assert cloud.begin(ada, "sweep", now=NOW) == {"stopped": both}
    cloud.proof_line(ada, "sweep", edit=True, tool=True, network=True, now=NOW)
    assert "stopped" not in cloud.begin(ada, "sweep", now=NOW)


def test_begin_runs_only_on_the_clone_its_own_script_is_in(ada, machine, tmp_path, monkeypatch):
    """A begin run from a clone in the cloud's home is that clone's cloud.py on that clone: one that names another
    clone as the workspace is refused, writing nothing, so that clone's owner never becomes the run's."""
    in_the_cloud(ada)
    origin_space(tmp_path, "ada-harbor", profile="project")
    harbor = cloned(machine, tmp_path, "ada-harbor", "ada/ada-harbor")
    monkeypatch.setattr(cloud, "own_clone", lambda: harbor)
    with pytest.raises(ValueError, match="its own"):
        cloud.begin(ada, "sweep", now=NOW)
    with pytest.raises(ValueError, match="its own"):
        cloud.begin(ada, "sweep", proof=True, now=NOW)
    assert cloud.main(["begin", str(ada), "--chain", "sweep", "--proof"]) == 2
    assert untouched(ada) and untouched(harbor)
    monkeypatch.setattr(cloud, "own_clone", lambda: ada)
    assert cloud.begin(ada, "sweep", now=NOW)["roots"][0] == ada.as_posix()


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


def stub_carry(monkeypatch, **said):
    seen = []

    def carry(root, chain, gh=None):
        seen.append((Path(root), chain))
        return {"carried": False, "head": "0123456789abcdef", **said}
    monkeypatch.setattr(sync, "carry", carry)
    return seen


def test_a_carry_that_conflicts_stops_the_run_and_writes_nothing(ada, monkeypatch):
    """A stopped run writes no marker, so nothing it wrote could land: it writes nothing at all, and says why in its
    log alone."""
    in_the_cloud(ada)
    stub_carry(monkeypatch, conflict=True)
    result = cloud.begin(ada, "sweep", now=NOW)
    assert result == {"stopped": "the last cloud run's branch conflicts with main in ada-space"}
    assert items_of(ada) == []
    assert not (Path.home() / ".spaces" / "routines").exists()   # no marker: the next begin is free


def test_a_carry_that_fails_stops_the_run_too(ada, monkeypatch):
    in_the_cloud(ada)
    stub_carry(monkeypatch, error="the branch could not be fetched")
    result = cloud.begin(ada, "sweep", now=NOW)
    assert result == {"stopped": "the last cloud run's branch could not be brought into ada-space: the branch could "
                                 "not be fetched"}
    assert items_of(ada) == [] and not (Path.home() / ".spaces" / "routines").exists()


def test_a_branch_with_a_protected_change_is_one_security_item_and_the_run_goes_on(ada, monkeypatch):
    in_the_cloud(ada)
    stub_carry(monkeypatch, skipped="protected", protected=2)
    result = cloud.begin(ada, "sweep", now=NOW)
    assert "stopped" not in result and result["carried"] == []
    [item] = items_of(ada)
    assert item["kind"] == "security" and item["id"] == "cloud-branch-sweep-01234567"
    assert "cloud/sweep" in item["text"] and "2 protected files" in item["text"] and "not used" in item["text"]
    assert marker()["chain"] == "sweep"


def test_a_branch_the_person_closed_is_left_quietly(ada, monkeypatch):
    in_the_cloud(ada)
    stub_carry(monkeypatch, skipped="closed")
    result = cloud.begin(ada, "sweep", now=NOW)
    assert "stopped" not in result and result["carried"] == [] and items_of(ada) == []


def test_a_clean_carry_after_a_stop_goes_on(ada, monkeypatch):
    in_the_cloud(ada)
    stub_carry(monkeypatch, conflict=True)
    cloud.begin(ada, "sweep", now=NOW)
    stub_carry(monkeypatch)
    assert cloud.begin(ada, "sweep", now=NOW)["items"] == [] and items_of(ada) == []


def test_every_clone_the_person_owns_is_a_root_the_workspace_first(ada, machine, tmp_path, monkeypatch):
    routine.claim_cloud(ada, TASKS, "Europe/Amsterdam", ["ada/ada-space", "ada/ada-brain", "ada/ada-harbor",
                                                         "ben/ben-space"], READ_ONLY, NOW - dt.timedelta(hours=1))
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


def test_once_moved_only_the_spaces_the_move_recorded_are_roots(ada, machine, tmp_path, monkeypatch):
    routine.claim_cloud(ada, TASKS, "Europe/Amsterdam", ["ada/ada-space", "ada/ada-harbor"], READ_ONLY,
                        NOW - dt.timedelta(hours=1))
    origin_space(tmp_path, "ada-harbor", profile="project")
    harbor = cloned(machine, tmp_path, "ada-harbor", "ada/ada-harbor")
    codebase_claiming_a_space(machine, tmp_path, "ada-app", "ada/ada-app")   # attached, but no space the move kept
    stub_carry(monkeypatch)
    assert cloud.begin(ada, "sweep", now=NOW)["roots"] == [ada.as_posix(), harbor.as_posix()]


# ----- begin: the run's marker -----------------------------------------------------------------------------------
def test_begin_writes_the_runs_marker_with_the_workspace_and_the_chain_alone(ada):
    """The marker says where the run works and which chain it runs, and nothing about a proof: a proof's landing is
    a landing like any other, so a protected file it left out is a security item too."""
    in_the_cloud(ada)
    assert "stopped" not in cloud.begin(ada, "sweep", now=NOW)
    assert marker() == {"workspace": ada.as_posix(), "chain": "sweep"}
    assert sorted(p.name for p in (Path.home() / ".spaces" / "routines").iterdir()) == ["cloud.json"]   # no profile
    next_run()
    cloud.begin(ada, "sweep", proof=True, now=NOW)
    assert marker() == {"workspace": ada.as_posix(), "chain": "sweep"}   # a proof's marker is the same
    assert not hasattr(sync.Run, "cloud_proof")   # nothing in the landing asks whether a run was a proof


def test_a_real_run_lets_go_of_the_items_an_earlier_shape_wrote_and_a_proof_keeps_them(ada):
    """The cloud once watched a silent chain and a stale pin; it no longer does. A real run takes their items out,
    keeping the cloud sync's own; a proof run, which proves the task and nothing else, leaves the file as it is."""
    old = [{"id": "cloud-silent-daily-20261024", "kind": "check", "text": "x", "recommendation": "x", "default": "x"},
           {"id": "cloud-stale-pin-20261022", "kind": "check", "text": "x", "recommendation": "x", "default": "x"},
           {"id": "sync-cloud-held-1", "kind": "check", "text": "x", "recommendation": "x", "default": "x"}]
    put(ada / ITEMS, yaml.safe_dump({"items": old}, sort_keys=False))
    cloud.begin(ada, "sweep", proof=True, now=NOW)
    assert [i["id"] for i in items_of(ada)] == [i["id"] for i in old]
    next_run()
    in_the_cloud(ada)
    assert cloud.begin(ada, "sweep", now=NOW)["items"] == []
    assert [i["id"] for i in items_of(ada)] == ["sync-cloud-held-1"]


def test_begin_runs_once_per_run(ada, capsys):
    in_the_cloud(ada)
    assert "stopped" not in cloud.begin(ada, "sweep", now=NOW)
    state = Path.home() / ".spaces" / "routines" / "cloud.json"
    before = state.read_text(encoding="utf-8")
    runs = ada / "_meta" / "state" / "runs.jsonl"
    with pytest.raises(ValueError, match="once"):
        cloud.begin(ada, "sweep", now=NOW)
    capsys.readouterr()
    for argv in (["--chain", "sweep"], ["--chain", "sweep", "--proof"]):
        assert cloud.main(["begin", str(ada), *argv]) == 2, argv
        assert "once" in capsys.readouterr().out
    assert state.read_text(encoding="utf-8") == before   # still the first begin's
    assert not runs.exists()   # no line


def test_a_begin_that_stepped_aside_leaves_the_next_begin_free(ada):
    assert cloud.begin(ada, "sweep", now=NOW) == {"stopped": routine.NO_CLOUD}   # it wrote nothing, the state too
    in_the_cloud(ada)
    assert "stopped" not in cloud.begin(ada, "sweep", now=NOW)


def test_the_begin_command_is_the_one_its_parser_reads_back():
    for proof in (False, True):
        command = cloud.begin_command("ada-space", "sweep", proof)
        assert command.startswith("python3 /home/user/ada-space/_meta/tools/cloud.py begin ") and "--grant" not in command
        args = cloud.parse(command.split()[2:])
        assert (args.command, args.workspace, args.chain, args.proof) == ("begin", "/home/user/ada-space",
                                                                          "sweep", proof)


# ----- begin: a proof run, and mounts ----------------------------------------------------------------------------
def test_a_proof_begin_skips_only_the_runner_check_and_exports_nothing(ada, monkeypatch):
    seen = stub_carry(monkeypatch)
    result = cloud.begin(ada, "sweep", proof=True, now=NOW)   # no record yet: a proof runs before the claim
    assert "stopped" not in result and seen == [(ada, "sweep")]
    assert result["cursors"] == {ada.as_posix(): {"mail": pages.iso(NOW), "calls": pages.iso(NOW)}}
    assert marker()["chain"] == "sweep" and result["items"] == []


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


# The repository feed's reads of a codebase whose name holds "graphql", spelled as the prompts spell them, and calls
# that reach GraphQL however its endpoint is written.
GRAPHQL_SLUG_READS = (
    "gh api search/issues'?q=repo:acme/graphql-gateway+is:pr+is:merged+merged:>=2026-10-01&per_page=100'",
    "gh api repos/acme/graphql-gateway/pulls/5", "gh api repos/acme/graphql-gateway/pulls/5/files'?per_page=100'",
    "gh api repos/graphql/graphql-js/pulls/5")
GRAPHQL_CALLS = ("gh api graphql", "gh api graphql -f query=x", "gh api /graphql", "gh api repos/../graphql",
                 "gh api search/issues/../../graphql", "gh api https://api.github.com/graphql",
                 "gh api HTTPS://api.github.com/graphql", "gh api repos/%2e%2e/graphql", "gh api repos/%2E%2E/graphql")


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


# ----- the trigger -----------------------------------------------------------------------------------------------
@pytest.fixture
def computer(tmp_path, monkeypatch):
    """Ada's workspace on their computer, whose origin is on GitHub, with a brain mount, a linked codebase and a repo
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


def render(ws: Path, **given) -> dict:
    """The sweep's task, in Ada's zone and the test environment unless `given` says otherwise."""
    return cloud.trigger(ws, "sweep", **{"timezone": "Europe/Amsterdam", "environment": ENV, **given})


def test_the_task_grant_is_the_read_tools_alone():
    assert cloud.task_grant(GRANT) == READ_ONLY
    assert cloud.task_grant({"read": [MAIL_READ, MAIL_READ, 7], "send": [MAIL_SEND]}) == {
        "read": [MAIL_READ], "write": [], "send": []}


def test_the_task_may_call_the_read_tools_and_never_a_write_or_a_send(computer):
    body = render(computer)
    allowed, denied = context(body)["allowed_tools"], context(body)["disallowed_tools"]
    assert set(READ_ONLY["read"]) <= set(allowed)
    assert not {TRACKER_WRITE, MAIL_SEND} & set(allowed) and {TRACKER_WRITE, MAIL_SEND} <= set(denied)
    assert body["mcp_connections"] == [{"name": "mail", "permitted_tools": ["get_message", "search_threads"]},
                                       {"name": "tracker", "permitted_tools": ["list_issues"]}]


def test_the_trigger_denies_every_ungranted_tool_by_name(computer):
    denied = context(render(computer))["disallowed_tools"]
    assert MAIL_TRASH in denied and "WebFetch" in denied and "mcp__github" in denied and "RemoteTrigger" in denied
    assert {TRACKER_DELETE, "mcp__calendar__delete_event"} <= set(denied)   # all the probe saw, as on the computer
    assert not set(READ_ONLY["read"]) & set(denied)


def test_the_trigger_denies_the_machinery_the_shell_shapes_and_git(computer):
    denied = context(render(computer))["disallowed_tools"]
    assert {f"Edit(//**/{pattern})" for pattern in routine.PROTECTED_EDITS} <= set(denied)
    # A mount's `.git` is a file naming its git folder, which `.git/**` does not cover: pointed elsewhere, a status
    # in the root would run that folder's config.
    assert "Edit(//**/.git)" in denied
    assert {"Edit(~/.spaces/**)", "Edit(//home/user/.spaces/**)", "Edit(//home/user/ada-space/identity/**)",
            "Edit(//home/user/ada-brain/identity/**)"} <= set(denied)
    # Every dot-entry of the home folder, in both spellings: user site-packages, git's own config, the shell's.
    assert {"Edit(~/.*)", "Edit(~/.*/**)", "Edit(//home/user/.*)", "Edit(//home/user/.*/**)"} <= set(denied)
    # The system's own folders, where a .pth or a sitecustomize would run inside every python3 the tools start; the
    # clones in /home/user and /tmp stay open.
    system = {rule for rule in denied if rule.startswith("Edit(//") and not rule.startswith(("Edit(//**/",
                                                                                             "Edit(//home/user/"))}
    assert system == {f"Edit(//{folder}/**)" for folder in (
        "bin", "boot", "dev", "etc", "lib", "lib32", "lib64", "libx32", "media", "mnt", "opt", "proc", "root", "run",
        "sbin", "srv", "sys", "usr", "var")}
    assert not {"Edit(//home/user/**)", "Edit(//home/**)", "Edit(//tmp/**)", "Edit(//**)"} & set(denied)
    assert {"Bash(python -m *)", "Bash(python3 -m *)", "Bash(git *)", "Bash(gh pr *)", *routine.GRAPHQL,
            "Bash(gh api * *)", "Bash(gh api *\t*)",
            "Bash(*_meta/tools/routine.py*)", "Bash(*_meta/tools/sign.py*)"} <= set(denied)
    # cloud.py is a routine tool (its begin runs first, its proof-line last): no deny rule of the task names it.
    assert "cloud.py" in routine.ROUTINE_TOOLS
    assert not any(tool in rule for rule in denied for tool in ("sweep.py", "cloud.py"))
    # sync.py runs as the run's landing, never as git's merge driver.
    patterns = bash_guard._patterns(denied, False)
    land = "python3 /home/user/ada-space/_meta/tools/sync.py --root /home/user/ada-space --cloud sweep"
    assert not any(p.fullmatch(land) for p in patterns)
    for flag in ("--merge-latest", "--merge", "'--merge-latest'"):
        driver = f"python3 /home/user/ada-space/_meta/tools/sync.py {flag} a /home/user/ada-space/space.yaml b"
        assert any(p.fullmatch(driver) for p in patterns), driver
    assert not {"Bash", "Read", "Edit", "Write", "Task"} & set(denied)


def test_the_trigger_refuses_every_spelling_of_a_github_write_by_its_text_alone(computer):
    # The platform matches a Bash rule against the command's text only (spec 3): read the rules as the guard does.
    denied = bash_guard._patterns(context(render(computer))["disallowed_tools"], False)
    for write in GH_WRITES:
        assert any(rule.fullmatch(write) for rule in denied), write
    for read in GH_READS:
        assert not any(rule.fullmatch(read) for rule in denied), read


def test_the_trigger_names_its_environment_and_no_plugin_and_never_runs_enabled(computer):
    """claude.ai drops a plugin, its marketplace and a soft deny list from a cloud task without a word: the body sends
    none of them, and names the environment the task runs in, which claude.ai requires."""
    (computer / "_meta" / "state" / "template-origin.yaml").write_text("repo: ada/spaces-template\n", encoding="utf-8")
    body = render(computer)
    assert body["enabled"] is False
    assert body["job_config"]["ccr"]["environment_id"] == ENV
    for key in ("enabled_plugins", "extra_marketplaces", "auto_mode_soft_deny"):
        assert key not in json.dumps(body), key
    assert "https://github.com/ada/ada-space" in [s["git_repository"]["url"] for s in context(body)["sources"]]
    assert "Bash" in context(body)["allowed_tools"] and MAIL_SEND not in context(body)["allowed_tools"]


@pytest.mark.parametrize("environment", [None, "", "01Test", "env_", "env_01 Test", "env_01/../x", "ENV_01Test",
                                         "env_" + "a" * 65])
def test_a_trigger_without_an_environment_is_never_rendered(computer, environment, capsys):
    with pytest.raises(ValueError, match="environment"):
        render(computer, environment=environment)
    assert not (Path.home() / ".spaces" / "cloud-sweep.json").exists()
    argv = ["trigger", str(computer), "--chain", "sweep", "--timezone", "Europe/Amsterdam"]
    assert cloud.main(argv + (["--environment", environment] if environment is not None else [])) == 2
    assert "environment" in capsys.readouterr().out
    assert not (Path.home() / ".spaces" / "cloud-sweep.json").exists()


def test_the_sweeps_task_keeps_the_shell_out_of_every_codebase_it_attaches(computer):
    """The sweep's task attaches codebases other people can push to, cloned beside the workspace: no shell command
    of the task names one's clone, so a begin (or anything else) run from or on one is refused."""
    patterns = bash_guard._patterns(context(render(computer))["disallowed_tools"], False)
    refused = lambda command: any(p.fullmatch(command) for p in patterns)   # noqa: E731
    for name in ("ada-app", "ada-site", "ada-lib"):
        for command in (f"python3 /home/user/{name}/_meta/tools/cloud.py begin /home/user/{name} --chain sweep",
                        f"python3 /home/user/ada-space/_meta/tools/cloud.py begin /home/user/{name} --chain sweep",
                        f"python3 /home/user/{name}/_meta/tools/cloud.py begin /home/user/ada-space --chain sweep",
                        f"python3 /home/user/{name}/x.py", f"ls /home/user/{name}", f"ls /home/user/{name} -a"):
            assert refused(command), command
    for command in (cloud.begin_command("ada-space", "sweep"), "gh api repos/ada/ada-app/pulls/5",
                    "python3 /home/user/ada-space/_meta/tools/sync.py --root /home/user/ada-space --cloud sweep",
                    "python3 /home/user/ada-brain/_meta/tools/sync.py --root /home/user/ada-brain --cloud sweep",
                    "python3 /home/user/ada-application/_meta/tools/x.py"):
        assert not refused(command), command


def test_the_tasks_refuse_graphql_by_its_endpoint_and_not_a_codebases_name(computer):
    denied = bash_guard._patterns(context(render(computer))["disallowed_tools"], False)
    for read in GRAPHQL_SLUG_READS:
        assert not any(rule.fullmatch(read) for rule in denied), read
    for call in GRAPHQL_CALLS:
        assert any(rule.fullmatch(call) for rule in denied), call


def no_grant(monkeypatch, **grant) -> None:
    """This computer grants the routines nothing (or only `grant`), as one with no probe of its own does."""
    monkeypatch.setattr(routine, "grant", lambda workspace, *a, **k: {
        "read": [], "write": [], "send": [], **grant, "reconnect": [], "notes": [], "person": "owner@example.test",
        "shell": ""})


def test_a_computer_without_a_probe_renders_the_moved_task_with_the_records_grant(computer, monkeypatch):
    """A computer that replaced the one the sweep moved from has no probe of its own (its build does not probe
    while the sweep runs in the cloud): it renders the task with the grant the move recorded, its reads alone."""
    routine.claim_cloud(computer, TASKS, "Europe/Amsterdam", ["ada/ada-space", "ada/ada-brain"], GRANT, NOW)
    (Path.home() / ".spaces" / "probe.json").unlink()
    no_grant(monkeypatch)
    body = render(computer)
    kept = json.loads((Path.home() / ".spaces" / "cloud-sweep.json").read_text(encoding="utf-8"))
    assert kept["grant"] == READ_ONLY
    assert set(cloud._tools(kept["grant"])) <= set(context(body)["allowed_tools"])


def test_a_task_with_no_app_to_read_is_never_rendered(computer, monkeypatch):
    """A render with nothing to read would prove nothing and read nothing: it is refused in one sentence, and nothing
    is kept. A grant of writes and sends alone is none, since the task gets neither. A probe on this computer is this
    computer's word, even beside a record that holds a grant."""
    no_grant(monkeypatch)
    with pytest.raises(ValueError, match="None of your apps"):
        render(computer)
    no_grant(monkeypatch, write=[TRACKER_WRITE], send=[MAIL_SEND])
    with pytest.raises(ValueError, match="None of your apps"):
        render(computer)
    no_grant(monkeypatch)
    routine.claim_cloud(computer, TASKS, "Europe/Amsterdam", ["ada/ada-space", "ada/ada-brain"], GRANT, NOW)
    with pytest.raises(ValueError, match="None of your apps"):
        render(computer)
    (Path.home() / ".spaces" / "probe.json").unlink()
    routine.claim_host(computer, "os", NOW)   # no cloud record: nothing to fall back on
    with pytest.raises(ValueError, match="None of your apps"):
        render(computer)
    assert not (Path.home() / ".spaces" / "cloud-sweep.json").exists()
    assert cloud.main(["trigger", str(computer), "--chain", "sweep", "--timezone", "Europe/Amsterdam",
                       "--environment", ENV]) == 2


def test_the_trigger_schedule_keeps_the_persons_hours_off_the_hour(computer):
    body = render(computer)
    assert body["cron_expression"].startswith("CRON_TZ=Europe/Amsterdam ")
    assert body["cron_expression"].split()[1:] == ["7", "7,13,19", "*", "*", "*"]


@needs_zones
def test_the_trigger_takes_the_zone_from_the_record_and_refuses_none(computer, monkeypatch):
    routine.claim_cloud(computer, TASKS, "America/New_York", ["ada/ada-space"], GRANT, NOW)
    assert render(computer, timezone=None)["cron_expression"].startswith("CRON_TZ=America/New_York ")
    (computer / routine.HOST).unlink()
    monkeypatch.setattr(cloud, "local_zone", lambda: None)
    with pytest.raises(ValueError, match="time zone"):
        render(computer, timezone=None)
    with pytest.raises(ValueError, match="time zone"):
        render(computer, timezone="Mars/Olympus")


def test_the_sweep_attaches_linked_codebases_and_no_template(computer):
    (computer / "_meta" / "state" / "template-origin.yaml").write_text("repo: ada/spaces-template\n", encoding="utf-8")
    urls = [s["git_repository"]["url"] for s in context(render(computer))["sources"]]
    assert urls == [f"https://github.com/ada/{name}" for name in
                    ("ada-space", "ada-brain", "ada-app", "ada-site", "ada-lib")]
    assert not any("template" in url for url in urls)


def test_a_repository_named_twice_in_two_cases_is_attached_once_as_first_named(computer):
    subprocess.run(["git", "-C", str(computer), "remote", "set-url", "origin", "https://github.com/ada/Ada-Space.git"],
                   check=True)
    (computer / "_meta" / "state" / "links.json").write_text(json.dumps(
        {"links": 3, "repos": ["ADA/Ada-Brain", "https://github.com/ada/Ada-App.git", "ada/ada-app", "ada/ada-space"]}),
        encoding="utf-8")
    body = render(computer)
    assert [s["git_repository"]["url"] for s in context(body)["sources"]] == \
           [f"https://github.com/ada/{name}" for name in ("Ada-Space", "ada-brain", "Ada-App", "ada-lib")]
    assert "Edit(//home/user/Ada-App/identity/**)" in context(body)["disallowed_tools"]   # the clone's own folder


@needs_zones
def test_the_zone_of_a_computer_whose_tz_is_no_iana_name_is_where_its_localtime_points(monkeypatch):
    monkeypatch.setattr(os, "readlink", lambda path: "/usr/share/zoneinfo/Europe/Lisbon")
    monkeypatch.setenv("TZ", "CET-1CEST")   # a POSIX rule, of an IANA name's shape
    assert cloud.local_zone() == "Europe/Lisbon"
    monkeypatch.setenv("TZ", ":Europe/Amsterdam")
    assert cloud.local_zone() == "Europe/Amsterdam"
    monkeypatch.setattr(os, "readlink", lambda path: "/usr/share/zoneinfo/Mars/Olympus")
    monkeypatch.delenv("TZ")
    assert cloud.local_zone() is None


def prompt(body: dict) -> str:
    [event] = body["job_config"]["ccr"]["events"]
    assert event["data"]["type"] == "user" and event["data"]["message"]["role"] == "user"
    return event["data"]["message"]["content"]


def test_the_trigger_prompt_names_the_person_the_clone_and_the_chain_prompt(computer):
    body = render(computer)
    said = prompt(body)
    assert body["name"] == "Ada Okafor's sweep"
    assert "Ada Okafor" in said and "/home/user/ada-space" in said
    assert "/home/user/ada-space/_meta/templates/cloud/sweep-chain.md" in said and "Proof" not in said
    assert "Proof section only" in prompt(render(computer, proof=True))


def test_the_prompt_gives_the_exact_begin_command_to_run_first(computer):
    said = prompt(render(computer))
    command = cloud.begin_command("ada-space", "sweep")
    assert f"`{command}`" in said and "--proof" not in said and "--grant" not in said
    assert said.index(command) < said.index("sweep-chain.md")   # begin first, then the chain's prompt
    for words in ("first", "exactly as written", "one command", "refused", "non-zero", "stop"):
        assert words in said, words
    proved = prompt(render(computer, proof=True))
    assert f"`{cloud.begin_command('ada-space', 'sweep', proof=True)}`" in proved and "--grant" not in proved


def test_the_trigger_keeps_what_it_attached_and_granted_for_the_move(computer):
    body = render(computer)
    kept = json.loads((Path.home() / ".spaces" / "cloud-sweep.json").read_text(encoding="utf-8"))
    assert {k: v for k, v in kept.items() if k not in ("roots", "grant")} == json.loads(json.dumps(body))
    # The sweep attaches the codebases too, but its roots are the spaces alone: a codebase is never one.
    assert kept["roots"] == ["ada/ada-space", "ada/ada-brain"]
    assert kept["grant"] == READ_ONLY


def test_a_workspace_without_a_github_repository_renders_no_task(computer):
    subprocess.run(["git", "-C", str(computer), "remote", "remove", "origin"], check=True)
    with pytest.raises(ValueError, match="GitHub"):
        render(computer)


@needs_zones
def test_the_trigger_cli_prints_the_render(computer, capsys):
    assert cloud.main(["trigger", str(computer), "--chain", "sweep", "--timezone", "Europe/Amsterdam",
                       "--environment", ENV]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["roots"][0] == "ada/ada-space" and printed["enabled"] is False
    assert printed["job_config"]["ccr"]["environment_id"] == ENV
    assert cloud.main(["trigger", str(computer), "--chain", "sweep", "--timezone", "Nowhere/Zone",
                       "--environment", ENV]) == 2


# ----- the proof line --------------------------------------------------------------------------------------------
REFUSALS = ("edit", "tool", "network")   # what a proof run tries, each a yes or a no on its line


def test_a_proof_line_lands_the_three_refusals_it_saw(tmp_path):
    assert REFUSALS == onboard.CLOUD_REFUSALS["sweep"]   # what the line writes is what the move reads
    ws = make_space(tmp_path / "ada-space")
    line = cloud.proof_line(ws, "sweep", edit=True, tool=False, network=True, now=NOW)
    assert line == {"routine": "cloud-proof", "chain": "sweep", "edit": True, "tool": False, "network": True,
                    "at": "2026-10-27T09:00:00Z"}
    runs = ws / "_meta" / "state" / "runs.jsonl"
    assert [json.loads(raw) for raw in runs.read_text(encoding="utf-8").splitlines()] == [line]
    with pytest.raises(ValueError):
        cloud.proof_line(ws, "daily", edit=True, tool=True, network=True, now=NOW)
    for missing in REFUSALS:   # a line without one of the three would prove nothing either way
        given = {refusal: True for refusal in REFUSALS if refusal != missing}
        with pytest.raises(TypeError):
            cloud.proof_line(ws, "sweep", now=NOW, **given)
    with pytest.raises(TypeError):   # the change made with a command is tried no more
        cloud.proof_line(ws, "sweep", edit=True, tool=True, network=True, shell=True, now=NOW)
    assert [json.loads(raw) for raw in runs.read_text(encoding="utf-8").splitlines()] == [line]   # nothing more


def test_the_proof_line_cli_reads_the_workspace_from_the_runs_state(tmp_path, capsys):
    ws = make_space(tmp_path / "ada-space")
    state = Path.home() / ".spaces" / "routines" / "cloud.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"workspace": ws.as_posix(), "chain": "sweep"}), encoding="utf-8")
    flags = ["--edit", "yes", "--tool", "no", "--network", "yes"]
    assert cloud.main(["proof-line", "--chain", "sweep", *flags]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert {refusal: printed[refusal] for refusal in REFUSALS} == {"edit": True, "tool": False, "network": True}
    assert "shell" not in printed
    runs = ws / "_meta" / "state" / "runs.jsonl"
    assert [json.loads(raw) for raw in runs.read_text(encoding="utf-8").splitlines()] == [printed]
    for refusal in REFUSALS:   # each of the three is required: a run cannot leave one out
        given = [word for at in range(0, len(flags), 2) if flags[at] != f"--{refusal}" for word in flags[at:at + 2]]
        with pytest.raises(SystemExit):
            cloud.main(["proof-line", str(ws), "--chain", "sweep", *given])
        with pytest.raises(SystemExit):
            cloud.main(["proof-line", str(ws), "--chain", "sweep", *given, f"--{refusal}", "maybe"])
    # the plugin's guard, the send and the change made with a command are no proof's now
    for gone in (["--guard", "yes"], ["--send", "yes"], ["--shell", "yes"], ["--shell", "no"]):
        with pytest.raises(SystemExit):
            cloud.main(["proof-line", str(ws), "--chain", "sweep", *flags, *gone])
    assert len(runs.read_text(encoding="utf-8").splitlines()) == 1   # no refused call wrote a line
    capsys.readouterr()


def test_onboard_moves_the_sweep_on_what_trigger_and_proof_line_wrote(computer):
    """The move reads what the cloud's own tools wrote: the render `trigger` kept and the proof's line, which proves
    only when it saw all three refusals. No landing proves or unproves anything. It moves only from a computer that
    runs the routines, which keeps the daily."""
    assert cloud.CHAINS == onboard.CLOUD_CHAINS   # the chains sync lands are the ones onboard reads
    render(computer)
    now = dt.datetime.now(dt.timezone.utc)
    runs = computer / "_meta" / "state" / "runs.jsonl"
    runs.unlink(missing_ok=True)
    every = {refusal: True for refusal in REFUSALS}
    # the request went through: the line proves nothing, and says what the cloud did not refuse
    cloud.proof_line(computer, "sweep", **{**every, "network": False}, now=now - dt.timedelta(hours=4))
    assert onboard.cloud_proofs(computer, now) == set()
    assert onboard.cloud_unrefused(computer) == {"sweep": ["network"]}
    # a line of an earlier shape, without the request, proves nothing either way: what was missing stays missing
    with runs.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"routine": "cloud-proof", "chain": "sweep", "edit": True, "tool": True, "shell": True,
                             "at": pages.iso(now - dt.timedelta(hours=3))}) + "\n")
    assert onboard.cloud_proofs(computer, now) == set()
    assert onboard.cloud_unrefused(computer) == {"sweep": ["network"]}
    # every refusal seen: proved, and nothing missing
    cloud.proof_line(computer, "sweep", **every, now=now - dt.timedelta(hours=2))
    assert onboard.cloud_proofs(computer, now) == {"sweep"} and onboard.cloud_unrefused(computer) == {}
    # a landing that left a protected file out, or none, changes nothing: no landing is paired with a proof
    landed(computer, left_out=1)
    landed(computer, left_out=0)
    assert onboard.cloud_proofs(computer, now) == {"sweep"} and onboard.cloud_unrefused(computer) == {}
    assert onboard._rendered("sweep")
    # this computer does not run the routines: the daily would have no home, so the move is refused, writing nothing
    assert not onboard.runs_here(computer)
    assert onboard.routines_cloud(computer, TASKS, "Europe/Amsterdam", now) == (
        False, onboard.CLOUD_STAY.format(why=onboard.CLOUD_NOT_HERE), [])
    assert routine.host(computer) is None
    routine.claim_host(computer, "os", now)   # it does now
    done, said, _ = onboard.routines_cloud(computer, TASKS, "Europe/Amsterdam", now)
    assert done, said
    record = routine.host(computer)
    assert record["runner"] == "cloud" and record["computer"] == routine.this_machine()   # it keeps the daily
    assert record["roots"] == ["ada/ada-space", "ada/ada-brain"]   # the spaces begin may work in, no codebase
    assert record["grant"] == READ_ONLY and record["tasks"] == TASKS
    assert onboard.runs_here(computer)
    cloud.proof_line(computer, "sweep", **{**every, "tool": False}, now=now)
    assert onboard.cloud_unrefused(computer) == {"sweep": ["tool"]}
    cloud.proof_line(computer, "sweep", **{**every, "network": False}, now=now)
    assert onboard.cloud_unrefused(computer) == {"sweep": ["tool", "network"]}
