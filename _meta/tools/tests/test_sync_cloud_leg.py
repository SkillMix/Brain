"""The computer's leg (spec 4.5): while a workspace's sweep runs in the cloud, the computer's sync
does what only the computer has. It reads the cloud's run lines before it says a chain did not
start (and says nothing while it cannot merge main), scans the transcripts at most every six hours,
adopts a cloud pull request that nothing could merge, and names a space the cloud does not read; two
computers that both do so never stop each other's merge. Template sync and AGENTS.md are the daily
chain's, which the computer the record names keeps: the leg does neither. Against a throwaway bare
origin and clones, as in test_sync.py; GitHub is a fake that answers the REST routes. Two of the
cloud landing's own guards are here too: a private file staged by force never lands, and its
`git status` never runs a mount's file monitor."""
import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest
import yaml

import chain
import ghrest
import pages
import routine
import sync
import template_sync
import transcripts
from conftest import make_space
from test_sync import (git, mounted, platform_merges, remote_space, repo,  # noqa: F401  (the repo fixture)
                       run_lines, write)
from test_sync_cloud import CloudGh, begin, land
from test_sync_merge import SOURCE, ClosedPulls, clone_of, cloud_branch, commit, on_main

TASKS = {"sweep": "trig_01AAAAAAAA"}
GRANT = {"read": ["mcp__mail__get_message"], "write": [], "send": []}
ITEMS = Path("_inbound") / "needs-you" / "sync.yaml"
STALE = Path("_meta") / "state" / "stale.yaml"
SESSIONS = Path("_meta") / "state" / "sessions.yaml"
RUNS = "_meta/state/runs.jsonl"


# ----- the space, its record and GitHub ----------------------------------------------------------------
def at(moment: dt.datetime, monkeypatch) -> dt.datetime:
    """The sync's clock (SPACE_NOW), to the second."""
    moment = moment.replace(microsecond=0)
    monkeypatch.setenv("SPACE_NOW", pages.iso(moment))
    return moment


def landed(clone: Path, message: str) -> None:
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", message)
    git(clone, "push", "-q", "origin", "HEAD:main")


def in_the_cloud(clone: Path, roots: list | None = None, named: bool = True) -> None:
    """The person moved the workspace's routines to the cloud, attaching `roots` (by default this space's
    repository), and an earlier sync landed the record. The record names the computer the move ran on, which keeps
    the leg (`computer`); not `named`, it is one the move wrote before it named a computer, which lets every
    computer do the leg's writing."""
    routine.claim_cloud(clone, TASKS, "Europe/Amsterdam", roots or [ghrest.repo_slug(clone)], GRANT,
                        dt.datetime.now(dt.timezone.utc))
    if not named:
        path = clone / routine.HOST
        kept = [line for line in path.read_text(encoding="utf-8").splitlines(keepends=True)
                if not line.startswith("computer:")]
        path.write_text("".join(kept), encoding="utf-8", newline="\n")
        assert "computer" not in routine.host(clone)
    landed(clone, "routines in the cloud")


def on_this_computer(clone: Path, monkeypatch) -> None:
    monkeypatch.setattr(routine, "this_machine", lambda: "m-desktop")
    monkeypatch.setattr(routine, "machine_name", lambda: "DESK")
    routine.claim_host(clone, "os", dt.datetime.now(dt.timezone.utc))
    landed(clone, "routines here")


def with_transcripts(clone: Path, folder: Path) -> Path:
    """The workspace reads the person's session files in `folder`, which holds one session."""
    data = yaml.safe_load((clone / "space.yaml").read_text(encoding="utf-8"))
    data["transcripts"] = {"path": str(folder), "switch": "off"}
    (clone / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    landed(clone, "transcripts")
    session(folder, "s-one", dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3))
    return folder


def session(folder: Path, sid: str, start: dt.datetime) -> None:
    project = folder / "project-a"
    project.mkdir(parents=True, exist_ok=True)
    events = [{"type": "user", "sessionId": sid, "cwd": "C:/work/notes", "timestamp": pages.iso(start),
               "message": {"role": "user", "content": "Draft the plan"}},
              {"type": "assistant", "sessionId": sid, "cwd": "C:/work/notes",
               "timestamp": pages.iso(start + dt.timedelta(minutes=40)),
               "message": {"role": "assistant", "content": [{"type": "text", "text": "The plan is drafted."}]}}]
    (project / f"{sid}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def later(monkeypatch) -> dt.datetime:
    """The sync's clock ADOPT_MINUTES and ten more ahead of the computer's own, so a cloud branch pushed now is as old
    as a stuck one: its commit, and the pull request a test says was opened then."""
    return at(dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=sync.ADOPT_MINUTES + 10), monkeypatch)


def run_line(routine_name: str, moment: dt.datetime) -> str:
    return json.dumps({"routine": routine_name, "at": pages.iso(moment), "space": "ada", "cost_tokens": 0,
                       "notes": "ok"}) + "\n"


class LegGh:
    """GitHub as the computer's sync sees it, through REST (`gh api`) and `gh run`. `pulls` maps a number to
    (its head branch, when it was opened, its mergeable state); a pull request's head is the branch's head on
    origin, and a head whose branch is in `red` has a failed check. A POST opens one (the computer's own sync
    pull request), opened now, clean and green."""

    def __init__(self, origin: Path, pulls: dict | None = None, red=()):
        self.origin, self.pulls, self.red, self.calls = origin, dict(pulls or {}), set(red), []

    def head(self, branch: str) -> str:
        return git(self.origin, "rev-parse", "-q", "--verify", f"refs/heads/{branch}", check=False).stdout.strip()

    def __call__(self, *args):   # set on the class, an instance is not bound: no self of the run
        self.calls.append(args)
        route = next((a for a in args if a.startswith("repos/")), "")
        slug, out = "/".join(route.split("/")[1:3]), ""
        if args[:3] == ("api", "-X", "POST") and route.endswith("/pulls"):
            number = max(self.pulls, default=40) + 1
            self.pulls[number] = (next(a[len("head="):] for a in args if a.startswith("head=")),
                                  dt.datetime.now(dt.timezone.utc), "clean")
            out = json.dumps({"number": number})
        elif "/pulls?" in route:
            wanted = unquote(route.split("&head=")[1].split("&")[0]).split(":", 1)[1] if "&head=" in route else None
            out = json.dumps([] if "state=closed" in route else [
                {"number": n, "title": "chore: sync", "draft": False, "auto_merge": None,
                 "created_at": pages.iso(opened), "html_url": f"https://github.com/{slug}/pull/{n}",
                 "head": {"ref": ref, "sha": self.head(ref), "repo": {"full_name": slug}},
                 "base": {"repo": {"full_name": slug}}}
                for n, (ref, opened, _) in self.pulls.items() if wanted in (None, ref)])
        elif m := re.fullmatch(r"repos/[^/]+/[^/]+/pulls/(\d+)", route):
            ref, _, state = self.pulls[int(m[1])]
            out = json.dumps({"number": int(m[1]), "state": "open", "merged": False, "mergeable_state": state,
                              "head": {"sha": self.head(ref)}})
        elif m := re.fullmatch(r"repos/[^/]+/[^/]+/commits/([^/]+)/check-runs\?.*", route):
            red = any(self.head(ref) == m[1] for ref in self.red)
            out = json.dumps({"check_runs": [{"name": "checks", "conclusion": "failure" if red else "success",
                                              "details_url": "https://github.com/o/r/actions/runs/5/job/50"}]})
        elif args[:2] not in (("run", "view"), ("run", "rerun")) and args != ("--version",):
            return subprocess.CompletedProcess(["gh", *args], 1, "", "unknown command")
        return subprocess.CompletedProcess(["gh", *args], 0, out, "")

    def asked_for_cloud(self) -> bool:
        return any("cloud/" in unquote(arg) for call in self.calls for arg in call)


def sync_run(clone: Path, monkeypatch, gh, *extra: str) -> int:
    monkeypatch.setattr(sync.Run, "gh", gh)
    return sync.main(["--root", str(clone), *extra])


def items(clone: Path) -> list:
    path = clone / ITEMS
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("items") or [] if path.exists() else []


# ----- the order: a computer runner keeps today's -------------------------------------------------------
@pytest.mark.parametrize("runner", [None, "os", "cloud"])
def test_not_started_runs_after_the_merge_only_while_the_routines_run_in_the_cloud(repo, tmp_path, monkeypatch,
                                                                                   runner):
    if runner == "os":
        on_this_computer(repo, monkeypatch)
    elif runner == "cloud":
        in_the_cloud(repo)
    order, real_merge = [], sync.Run.merge_in
    monkeypatch.setattr(sync, "not_started", lambda root, now=None, **kw: order.append("not_started") or False)
    monkeypatch.setattr(sync.Run, "merge_in", lambda self: (order.append("merge_in"), real_merge(self))[1])
    assert sync_run(repo, monkeypatch, lambda *args: None, "--no-push") == 0
    expected = ["merge_in", "not_started"] if runner == "cloud" else ["not_started", "merge_in"]
    assert order == expected


def test_a_computer_runner_workspace_keeps_todays_order(repo, tmp_path, monkeypatch):
    """Routines on this computer: no transcripts scan, no template sync and no adoption in the sync; the
    chains do the first two, and there is no cloud pull request to adopt."""
    with_transcripts(repo, tmp_path / "transcripts")
    (repo / "_meta" / "state" / "onboard.yaml").write_text("at: 2026-10-01T08:00:00Z\n", encoding="utf-8")
    landed(repo, "onboarded")
    on_this_computer(repo, monkeypatch)
    calls = []
    monkeypatch.setattr(transcripts, "scan", lambda root, now=None: calls.append("scan") or {})
    monkeypatch.setattr(template_sync, "guarded", lambda root, source=None: calls.append("template") or None)
    cloud_branch(tmp_path, {SOURCE: "# r0\n"})
    gh = LegGh(tmp_path / "origin.git",
               {7: ("cloud/sweep", dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2), "dirty")})
    assert sync_run(repo, monkeypatch, gh) == 0
    assert calls == [] and not gh.asked_for_cloud() and not (repo / SOURCE).exists()
    assert not (Path.home() / ".spaces" / "sync-leg-ada.json").exists()


def test_not_started_reads_the_clouds_run_lines_after_the_merge(repo, tmp_path, monkeypatch):
    """The computer slept for three days while the cloud ran the chains: the cloud's run lines are on main
    by the time the sync asks whether a chain started, so it says nothing did not."""
    now = at(dt.datetime.now(dt.timezone.utc), monkeypatch)
    runs = repo / "_meta" / "state" / "runs.jsonl"
    write(runs, run_line("sweep_chain", now - dt.timedelta(days=3)) + run_line("daily", now - dt.timedelta(days=3)))
    in_the_cloud(repo)   # the last runs this computer saw, before it slept
    on_main(tmp_path, {"_meta/state/runs.jsonl": (runs.read_text(encoding="utf-8")
                                                  + run_line("sweep_chain", now - dt.timedelta(hours=1))
                                                  + run_line("daily", now - dt.timedelta(hours=2)))})
    assert sync_run(repo, monkeypatch, lambda *args: None, "--no-push") == 0
    stale = yaml.safe_load((repo / STALE).read_text(encoding="utf-8")) if (repo / STALE).exists() else {}
    assert not [key for key in stale or {} if key.endswith("not started")], stale


def test_a_computer_runner_still_names_a_chain_before_the_merge(repo, tmp_path, monkeypatch):
    """The same three days on a computer that runs the routines itself: the sync asks before the merge, as
    today, and names the chains its own scheduler did not start."""
    now = at(dt.datetime.now(dt.timezone.utc), monkeypatch)
    runs = repo / "_meta" / "state" / "runs.jsonl"
    write(runs, run_line("sweep_chain", now - dt.timedelta(days=3)) + run_line("daily", now - dt.timedelta(days=3)))
    landed(repo, "the last runs this computer saw")
    on_this_computer(repo, monkeypatch)
    on_main(tmp_path, {"_meta/state/runs.jsonl": (runs.read_text(encoding="utf-8")
                                                  + run_line("sweep_chain", now - dt.timedelta(hours=1)))})
    assert sync_run(repo, monkeypatch, lambda *args: None, "--no-push") == 0
    stale = yaml.safe_load((repo / STALE).read_text(encoding="utf-8"))
    assert "sweep_chain not started" in stale


def test_run_lines_a_quiet_cloud_branch_holds_count_as_a_start(repo, tmp_path, monkeypatch):
    """A cloud sweep that changed only run lines and marks pushes cloud/sweep and opens no pull request for up to six
    hours, so main's newest sweep line can be older than `not_started` allows while the chain ran since. The leg reads
    the run lines the cloud branches hold, which its fetch brought, as well as main's."""
    now = at(dt.datetime.now(dt.timezone.utc), monkeypatch)
    runs = repo / RUNS
    write(runs, run_line("sweep_chain", now - dt.timedelta(hours=25)) + run_line("daily", now - dt.timedelta(hours=2)))
    in_the_cloud(repo)
    assert sync_run(repo, monkeypatch, lambda *args: None, "--no-push") == 0
    assert "sweep_chain not started" in yaml.safe_load((repo / STALE).read_text(encoding="utf-8"))
    held = runs.read_text(encoding="utf-8") + run_line("sweep_chain", now - dt.timedelta(hours=6))
    cloud_branch(tmp_path, {RUNS: held + run_line("sweep_chain", now - dt.timedelta(minutes=30))})   # quiet runs
    at(now + dt.timedelta(minutes=15), monkeypatch)
    assert sync_run(repo, monkeypatch, lambda *args: None, "--no-push") == 0
    stale = yaml.safe_load((repo / STALE).read_text(encoding="utf-8")) or {}
    assert "sweep_chain not started" not in stale, stale
    assert git(repo, "merge-base", "--is-ancestor", "origin/cloud/sweep", "HEAD", check=False).returncode != 0


@pytest.mark.parametrize("why", ["offline", "conflict"])
def test_a_main_that_could_not_be_merged_in_names_no_chain(repo, tmp_path, monkeypatch, why):
    """The computer wakes after three days offline, or with a change main conflicts with: the cloud's run lines are
    not in this checkout, so the sync does not ask whether a chain started, and stale.yaml stays as it was. The first
    run that merges main asks."""
    now = at(dt.datetime.now(dt.timezone.utc), monkeypatch)
    runs = repo / "_meta" / "state" / "runs.jsonl"
    write(runs, run_line("sweep_chain", now - dt.timedelta(days=3)) + run_line("daily", now - dt.timedelta(days=3)))
    write(repo / "work" / "notes.md", "# Notes\n")
    in_the_cloud(repo)
    on_main(tmp_path, {"_meta/state/runs.jsonl": (runs.read_text(encoding="utf-8")
                                                  + run_line("sweep_chain", now - dt.timedelta(hours=1))
                                                  + run_line("daily", now - dt.timedelta(hours=2)))})
    origin, away = tmp_path / "origin.git", tmp_path / "away.git"
    if why == "offline":   # origin out of reach, its address unchanged
        origin.rename(away)
    else:
        on_main(tmp_path, {"work/notes.md": "# Notes\n\nmain's line\n"})
        commit(repo, "a change here", {"work/notes.md": "# Notes\n\nthis computer's line\n"})
    asked, real = [], sync.not_started
    monkeypatch.setattr(sync, "not_started", lambda root, now=None, **kw: asked.append(1) or real(root, now, **kw))
    assert sync_run(repo, monkeypatch, lambda *args: None, "--no-push") == (1 if why == "offline" else 0)
    notes = run_lines(repo)[-1]["notes"]
    said = {"errors": ["fetch failed"]} if why == "offline" else {"conflict": True}
    assert {key: notes.get(key) for key in said} == said, notes
    assert asked == [] and "not_started" not in notes and not (repo / STALE).exists()
    if why == "offline":
        away.rename(origin)
    else:   # the person took main's line
        commit(repo, "main's line taken", {"work/notes.md": "# Notes\n\nmain's line\n"})
    assert sync_run(repo, monkeypatch, lambda *args: None, "--no-push") == 0
    stale = yaml.safe_load((repo / STALE).read_text(encoding="utf-8")) if (repo / STALE).exists() else {}
    assert asked == [1] and not [key for key in stale or {} if key.endswith("not started")], stale


# ----- transcripts ---------------------------------------------------------------------------------------
def test_a_cloud_workspace_scans_transcripts_on_the_computer_at_most_every_six_hours(repo, tmp_path, monkeypatch):
    with_transcripts(repo, tmp_path / "transcripts")
    in_the_cloud(repo)
    start = dt.datetime.now(dt.timezone.utc)

    def scans() -> int:
        return sum(1 for line in run_lines(repo) if line.get("routine") == "transcripts")
    at(start, monkeypatch)
    assert sync_run(repo, monkeypatch, lambda *args: None, "--no-push") == 0
    assert scans() == 1 and (repo / SESSIONS).exists()
    assert run_lines(repo)[-1]["notes"]["transcripts"]["seen"] == 1
    kept = Path.home() / ".spaces" / "sync-leg-ada.json"
    assert json.loads(kept.read_text(encoding="utf-8"))["transcripts"] == pages.iso(start)
    at(start + dt.timedelta(hours=1), monkeypatch)
    assert sync_run(repo, monkeypatch, lambda *args: None, "--no-push") == 0
    assert scans() == 1, "not again within six hours"
    at(start + dt.timedelta(hours=chain.TRANSCRIPTS_HOURS, minutes=1), monkeypatch)
    assert sync_run(repo, monkeypatch, lambda *args: None, "--no-push") == 0
    assert scans() == 2
    tracked = git(repo, "ls-files").stdout
    assert "sync-leg" not in tracked and str(SESSIONS.as_posix()) in tracked   # the clock stays on this computer


def test_a_scan_that_fails_is_tried_again_on_its_own_clock_not_every_run(repo, tmp_path, monkeypatch):
    with_transcripts(repo, tmp_path / "transcripts")
    in_the_cloud(repo)
    tried = []

    def broken(root, now=None):
        tried.append(1)
        raise OSError("a session file could not be read")
    monkeypatch.setattr(transcripts, "scan", broken)
    start = at(dt.datetime.now(dt.timezone.utc), monkeypatch)
    assert sync_run(repo, monkeypatch, lambda *args: None, "--no-push") == 1
    assert run_lines(repo)[-1]["notes"]["errors"] == ["transcripts scan failed: OSError"]
    at(start + dt.timedelta(minutes=15), monkeypatch)
    assert sync_run(repo, monkeypatch, lambda *args: None, "--no-push") == 0
    assert len(tried) == 1
    at(start + dt.timedelta(hours=chain.TRANSCRIPTS_HOURS, minutes=1), monkeypatch)
    sync_run(repo, monkeypatch, lambda *args: None, "--no-push")
    assert len(tried) == 2


def test_two_computers_that_both_scan_never_wedge_on_the_sessions_file(repo, tmp_path, monkeypatch):
    """Where the record names no computer for the leg, each computer's sync scans its own session files. A laptop
    that scanned and committed offline still merges main once a desktop's scan landed there, an hour and a day
    later: the sessions file is generated state, and keeps this checkout's side (`MERGE_RULES`)."""
    data = yaml.safe_load((repo / "space.yaml").read_text(encoding="utf-8"))
    data["transcripts"] = {"path": "$SPACES_TEST_SESSIONS", "switch": "off"}   # each computer its own folder
    (repo / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    landed(repo, "transcripts")
    in_the_cloud(repo, named=False)
    now = dt.datetime.now(dt.timezone.utc)
    desk, lap = tmp_path / "desk-sessions", tmp_path / "laptop-sessions"
    session(desk, "s-desk", now - dt.timedelta(hours=3))
    session(lap, "s-laptop", now - dt.timedelta(hours=2))
    laptop = clone_of(tmp_path, "laptop")
    kept = Path.home() / ".spaces" / "sync-leg-ada.json"   # one home folder in the test: each computer its own clock

    def scans(clone: Path, folder: Path) -> dict:
        monkeypatch.setenv("SPACES_TEST_SESSIONS", str(folder))
        kept.unlink(missing_ok=True)
        assert sync_run(clone, monkeypatch, lambda *args: None, "--no-push", "--min-age-minutes", "0") == 0
        return run_lines(clone)[-1]["notes"]
    assert "transcripts" in scans(laptop, lap)   # the laptop scans and commits, and goes offline before its push
    assert "transcripts" in scans(repo, desk)
    git(repo, "push", "-q", "origin", "HEAD:main")   # the desktop's scan lands
    for hours in (1, 30):
        at(now + dt.timedelta(hours=hours), monkeypatch)
        notes = scans(laptop, lap)
        assert "conflict" not in notes, notes
        assert git(laptop, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False).returncode == 0
        assert [row["id"] for row in yaml.safe_load((laptop / SESSIONS).read_text(encoding="utf-8"))["sessions"]] \
            == ["s-laptop"]


def test_only_the_computer_the_record_names_does_the_legs_writing(repo, tmp_path, monkeypatch):
    """The move names the computer that keeps the leg (the record's `computer`, its id): here the desktop, where it
    ran. Another computer's sync still says, after the merge, when a chain did not start, but scans nothing, adopts
    nothing and names no space: two computers would write the same files and conflict on them. Neither runs template
    sync or writes AGENTS.md: the desktop's daily does."""
    with_transcripts(repo, tmp_path / "transcripts")
    (repo / "_meta" / "state" / "onboard.yaml").write_text("at: 2026-10-01T08:00:00Z\n", encoding="utf-8")
    landed(repo, "onboarded")
    listed = Path.home() / ".spaces" / "spaces.yaml"
    listed.parent.mkdir(parents=True, exist_ok=True)
    listed.write_text(yaml.safe_dump({"spaces": [{"root": str(repo)}]}), encoding="utf-8")
    a_space(tmp_path, "ada-harbor")
    monkeypatch.setattr(routine, "this_machine", lambda: "m-desktop")
    in_the_cloud(repo)
    assert routine.host(repo)["computer"] == "m-desktop"
    cloud_branch(tmp_path, {SOURCE: "# r0\n"})
    now = later(monkeypatch)
    gh = LegGh(tmp_path / "origin.git", {7: ("cloud/sweep", now - dt.timedelta(hours=1), "dirty")})
    calls = []
    monkeypatch.setattr(sync, "not_started", lambda root, now=None, **kw: calls.append("not_started") or False)
    monkeypatch.setattr(transcripts, "scan", lambda root, now=None: calls.append("scan") or {"seen": 1})
    monkeypatch.setattr(template_sync, "guarded",
                        lambda root, source=None: calls.append("template") or template_sync.Report())
    monkeypatch.setattr(routine, "this_machine", lambda: "m-laptop")
    assert sync_run(repo, monkeypatch, gh, "--no-push") == 0
    assert calls == ["not_started"] and not gh.asked_for_cloud() and items(repo) == []
    assert not (repo / SOURCE).exists() and not (repo / "AGENTS.md").exists()
    monkeypatch.setattr(routine, "this_machine", lambda: "m-desktop")
    calls.clear()
    assert sync_run(repo, monkeypatch, gh, "--no-push") == 0
    assert calls == ["not_started", "scan"] and run_lines(repo)[-1]["notes"]["adopted"] == [7]
    assert not (repo / "AGENTS.md").exists()
    assert [i["text"] for i in items(repo)] == [
        "Your routines in the cloud do not read ada/ada-harbor: it was made after you moved them."]


def test_a_pause_stops_the_scan_not_the_adoption(repo, tmp_path, monkeypatch):
    """Paused, the computer's leg leaves what the paused chains would have done (spec 4.6: a pause works as it does
    today); a cloud pull request nothing merges is still adopted, and a chain that did not start still named."""
    with_transcripts(repo, tmp_path / "transcripts")
    (repo / "_meta" / "state" / "onboard.yaml").write_text("at: 2026-10-01T08:00:00Z\n", encoding="utf-8")
    landed(repo, "onboarded")
    in_the_cloud(repo)
    routine.set_pause(repo, dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2))
    landed(repo, "paused")
    cloud_branch(tmp_path, {SOURCE: "# r0\n"})
    now = later(monkeypatch)
    gh = LegGh(tmp_path / "origin.git", {7: ("cloud/sweep", now - dt.timedelta(hours=1), "dirty")})
    calls = []
    monkeypatch.setattr(sync, "not_started", lambda root, now=None, **kw: calls.append("not_started") or False)
    monkeypatch.setattr(transcripts, "scan", lambda root, now=None: calls.append("scan") or {"seen": 1})
    monkeypatch.setattr(template_sync, "guarded",
                        lambda root, source=None: calls.append("template") or template_sync.Report())
    assert sync_run(repo, monkeypatch, gh, "--no-push") == 0
    assert calls == ["not_started"] and run_lines(repo)[-1]["notes"]["adopted"] == [7]
    assert routine.paused_until(routine.host(repo), now) is not None
    assert not (repo / "AGENTS.md").exists()


def test_without_the_folder_on_this_computer_nothing_is_scanned(repo, tmp_path, monkeypatch):
    folder = with_transcripts(repo, tmp_path / "transcripts")
    in_the_cloud(repo)
    import shutil
    shutil.rmtree(folder)
    assert sync_run(repo, monkeypatch, lambda *args: None, "--no-push") == 0
    assert not (repo / SESSIONS).exists() and not any(line.get("routine") == "transcripts" for line in run_lines(repo))


def test_an_unchanged_scan_leaves_the_sessions_file_alone(tmp_path):
    folder = tmp_path / "transcripts"
    root = make_space(tmp_path / "ada", transcripts={"path": str(folder), "switch": "off"})
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    session(folder, "s-one", now - dt.timedelta(hours=3))
    transcripts.scan(root, now=now)
    path = root / SESSIONS
    text, old = path.read_text(encoding="utf-8"), now.timestamp() - 3600
    os.utime(path, (old, old))
    transcripts.scan(root, now=now + dt.timedelta(hours=6))
    assert path.read_text(encoding="utf-8") == text and path.stat().st_mtime == old
    session(folder, "s-two", now - dt.timedelta(hours=1))
    transcripts.scan(root, now=now + dt.timedelta(hours=12))
    rows = yaml.safe_load(path.read_text(encoding="utf-8"))["sessions"]
    assert sorted(r["id"] for r in rows) == ["s-one", "s-two"] and path.stat().st_mtime != old


# ----- template sync and AGENTS.md: the daily's, on the computer ---------------------------------------------
OLDER = "# Operating contract: ada\n\nAs an older release rendered it.\n"


def test_the_leg_leaves_template_sync_and_agents_md_to_the_daily_on_the_computer(repo, monkeypatch):
    """The computer the record names keeps its daily chain, which runs template sync and whose status writes AGENTS.md:
    the leg does neither, however stale AGENTS.md is and however long ago the template was synced."""
    write(repo / "_meta" / "state" / "onboard.yaml", "at: 2026-10-01T08:00:00Z\n")
    write(repo / "AGENTS.md", OLDER)
    landed(repo, "onboarded, with an older AGENTS.md")
    in_the_cloud(repo)
    monkeypatch.setattr(template_sync, "guarded", lambda root, source=None: pytest.fail("template sync ran"))
    assert sync_run(repo, monkeypatch, lambda *args: None, "--no-push") == 0
    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == OLDER
    notes = run_lines(repo)[-1]["notes"]
    assert "template_sync" not in notes and "agents_md" not in notes, notes
    assert chain.gate(repo, chain="daily") is None and chain.gate(repo, chain="sweep") == chain.IN_THE_CLOUD


# ----- adoption ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("stuck", ["red", "dirty"])
def test_a_red_cloud_pull_request_older_than_half_an_hour_rides_the_computers_sync(repo, tmp_path, monkeypatch,
                                                                                    stuck):
    in_the_cloud(repo)
    head = cloud_branch(tmp_path, {SOURCE: "# r0\n"})
    now = later(monkeypatch)
    opened = now - dt.timedelta(minutes=sync.ADOPT_MINUTES + 10)
    gh = LegGh(tmp_path / "origin.git", {7: ("cloud/sweep", opened, "dirty" if stuck == "dirty" else "clean")},
               red={"cloud/sweep"} if stuck == "red" else ())
    assert sync_run(repo, monkeypatch, gh) == 0
    assert (repo / SOURCE).read_text(encoding="utf-8") == "# r0\n"
    assert git(repo, "merge-base", "--is-ancestor", head, "HEAD", check=False).returncode == 0
    assert run_lines(repo)[-1]["notes"]["adopted"] == [7]
    git(repo, "fetch", "-q", "origin")
    assert git(repo, "show", f"origin/sync/{sync.hostname()}:{SOURCE}").stdout == "# r0\n"   # it rides the push
    assert not any(c[:3] == ("api", "-X", "PUT") or "merge" in c for c in gh.calls)   # never merged on GitHub


@pytest.mark.parametrize("pull", ["young", "green"])
def test_a_young_or_green_cloud_pull_request_is_left_to_its_run(repo, tmp_path, monkeypatch, pull):
    in_the_cloud(repo)
    cloud_branch(tmp_path, {SOURCE: "# r0\n"})
    now = later(monkeypatch)
    young = pull == "young"
    gh = LegGh(tmp_path / "origin.git",
               {7: ("cloud/sweep", now - (dt.timedelta(minutes=10) if young else dt.timedelta(hours=2)),
                    "dirty" if young else "clean")},
               red={"cloud/sweep"} if young else ())
    assert sync_run(repo, monkeypatch, gh, "--no-push") == 0
    assert not (repo / SOURCE).exists()
    assert "adopted" not in run_lines(repo)[-1]["notes"] and items(repo) == []


def test_a_cloud_branch_its_run_pushed_minutes_ago_is_left_to_it(repo, tmp_path, monkeypatch):
    """A pull request a cloud run opened hours ago and carried since: the run that pushed to it minutes ago may still
    be merging main in again or repairing its checks, so its age is its head's, not the pull request's."""
    in_the_cloud(repo)
    cloud_branch(tmp_path, {SOURCE: "# r0\n"})   # pushed now
    now = at(dt.datetime.now(dt.timezone.utc), monkeypatch)
    gh = LegGh(tmp_path / "origin.git", {7: ("cloud/sweep", now - dt.timedelta(hours=3), "dirty")})
    assert sync_run(repo, monkeypatch, gh, "--no-push") == 0
    assert not (repo / SOURCE).exists() and "adopted" not in run_lines(repo)[-1]["notes"] and items(repo) == []
    later(monkeypatch)
    assert sync_run(repo, monkeypatch, gh, "--no-push") == 0
    assert run_lines(repo)[-1]["notes"]["adopted"] == [7] and (repo / SOURCE).exists()


def test_a_cloud_pull_request_that_cannot_be_merged_here_is_one_item(repo, tmp_path, monkeypatch):
    in_the_cloud(repo)
    cloud_branch(tmp_path, {"work/notes.md": "# Notes\n\nthe cloud's line\n"})
    on_main(tmp_path, {"work/notes.md": "# Notes\n\nmain's line\n"})
    now = later(monkeypatch)
    gh = LegGh(tmp_path / "origin.git", {7: ("cloud/sweep", now - dt.timedelta(hours=1), "dirty")})
    assert sync_run(repo, monkeypatch, gh, "--no-push") == 0
    assert items(repo) == [{
        "id": "cloud-adopt-7", "kind": "request",
        "text": "A cloud run's pull request #7 conflicts with your files in a way the sync cannot settle.",
        "recommendation": "open it and keep the side you want, or close it: the next cloud run starts again from main",
        "default": "it stays open and the cloud runs keep adding to it", "depends": 1}]
    assert not (repo / ".git" / "MERGE_HEAD").exists() and not git(repo, "diff", "--name-only",
                                                                    "--diff-filter=U").stdout.strip()
    assert (repo / "work" / "notes.md").read_text(encoding="utf-8") == "# Notes\n\nmain's line\n"
    assert "adopted" not in run_lines(repo)[-1]["notes"]
    at(now + dt.timedelta(minutes=15), monkeypatch)
    assert sync_run(repo, monkeypatch, gh, "--no-push") == 0
    assert [i["id"] for i in items(repo)] == ["cloud-adopt-7"]   # once
    del gh.pulls[7]   # the person closed it
    at(now + dt.timedelta(minutes=30), monkeypatch)
    assert sync_run(repo, monkeypatch, gh, "--no-push") == 0
    assert items(repo) == []


def test_a_cloud_branch_with_a_protected_change_is_never_adopted(repo, tmp_path, monkeypatch):
    """Someone else pushed it (a cloud run never lands a protected change): adopting it would run their tools on
    this computer. The next cloud run's carry names it and its push replaces it."""
    in_the_cloud(repo)
    cloud_branch(tmp_path, {SOURCE: "# r0\n", "_meta/tools/x.py": "print('hello')\n"})
    now = later(monkeypatch)
    gh = LegGh(tmp_path / "origin.git", {7: ("cloud/sweep", now - dt.timedelta(hours=1), "dirty")})
    assert sync_run(repo, monkeypatch, gh, "--no-push") == 0
    assert not (repo / SOURCE).exists() and not (repo / "_meta" / "tools" / "x.py").exists()
    assert "adopted" not in run_lines(repo)[-1]["notes"]


def test_a_cloud_branch_whose_change_main_moved_into_a_protected_path_is_never_adopted(repo, tmp_path, monkeypatch,
                                                                                         capsys):
    """The branch changes a plain file that main has since moved under _meta/templates/: git's merge follows the move
    and writes the branch's change there, which no list of the branch's own paths shows. Checked again after the
    merge, the adoption is undone."""
    text = "# Handover\n\n" + "".join(f"Step {n}: read the notes.\n" for n in range(1, 9))
    commit(repo, "base", {"work/prompts/handover.md": text})
    git(repo, "push", "-q", "origin", "main")
    in_the_cloud(repo)
    cloud_branch(tmp_path, {SOURCE: "# r0\n",
                            "work/prompts/handover.md": text.replace("Step 8: read", "Step 8: forward")})
    on_main(tmp_path, {"work/prompts/handover.md": None, "_meta/templates/cloud/handover.md": text})
    now = later(monkeypatch)
    gh = LegGh(tmp_path / "origin.git", {7: ("cloud/sweep", now - dt.timedelta(hours=1), "dirty")})
    assert sync_run(repo, monkeypatch, gh, "--no-push") == 0
    assert "adopt: cloud/sweep changes a protected path" in capsys.readouterr().out
    assert (repo / "_meta" / "templates" / "cloud" / "handover.md").read_text(encoding="utf-8") == text
    assert not (repo / SOURCE).exists() and "adopted" not in run_lines(repo)[-1]["notes"]
    assert not git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip()


def test_an_adoption_that_rides_a_split_push_keeps_its_history(repo, tmp_path, monkeypatch):
    """This computer has a protected change waiting for the person's click (a template release), so its push is split
    and its plain part is a commit built on main. That commit keeps the adopted cloud head as a parent, in this run and
    the next, so once its pull request merges, main holds the head: GitHub marks the cloud pull request merged, and
    the next cloud run's carry has nothing to bring in. Carried again, every line the branch added to an append-only
    file would land twice."""
    def moment(hour: int) -> dt.datetime:
        return dt.datetime(2026, 10, 27, hour, tzinfo=dt.timezone.utc)
    runs = repo / RUNS
    write(runs, run_line("daily", moment(1)))
    landed(repo, "a daily line on main")
    in_the_cloud(repo)
    base = runs.read_text(encoding="utf-8")
    cloud_lines = run_line("sweep_chain", moment(2)) + run_line("sweep-chain", moment(3))
    head = cloud_branch(tmp_path, {RUNS: base + cloud_lines, SOURCE: "# r0\n"})
    on_main(tmp_path, {RUNS: base + run_line("daily", moment(4))})   # the daily landed meanwhile: #7 conflicts
    commit(repo, "a template release waiting for the click", {"_meta/tools/x.py": "print('release')\n"})
    now = later(monkeypatch)
    gh = LegGh(tmp_path / "origin.git", {7: ("cloud/sweep", now - dt.timedelta(hours=1), "dirty")})
    plain, protected = f"origin/sync/{sync.hostname()}", f"origin/sync/{sync.hostname()}-protected"

    def holds(ref: str) -> bool:
        git(repo, "fetch", "-q", "origin")
        return git(repo, "merge-base", "--is-ancestor", head, ref, check=False).returncode == 0

    def changes(ref: str) -> list:
        return git(repo, "diff", "--name-only", "origin/main", ref).stdout.split()
    assert sync_run(repo, monkeypatch, gh) == 0
    assert run_lines(repo)[-1]["notes"]["adopted"] == [7]
    assert holds(plain) and not holds(protected)
    assert SOURCE in changes(plain) and "_meta/tools/x.py" not in changes(plain)
    assert "_meta/tools/x.py" in changes(protected)
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n", minutes_old=20)
    at(now + dt.timedelta(minutes=15), monkeypatch)   # the plain part is built again, before its pull request merged
    assert sync_run(repo, monkeypatch, gh) == 0
    assert holds(plain) and "work/notes.md" in changes(plain)
    platform_merges(tmp_path, f"sync/{sync.hostname()}")
    assert holds("origin/main")
    git(tmp_path / "other", "pull", "-q", "--no-rebase", "origin", "main")
    on_main(tmp_path, {RUNS: (tmp_path / "other" / RUNS).read_text(encoding="utf-8") + run_line("sync", moment(5))})
    clone = clone_of(tmp_path, "next-run")
    assert sync.carry(clone, "sweep", gh=ClosedPulls()) == {"carried": False, "head": head}   # main holds it
    text = (clone / RUNS).read_text(encoding="utf-8")
    assert [text.count(line) for line in cloud_lines.splitlines(keepends=True)] == [1, 1]


def test_a_cloud_branch_whose_changes_cannot_be_listed_is_said_so(repo, tmp_path, monkeypatch, capsys):
    in_the_cloud(repo)
    cloud_branch(tmp_path, {SOURCE: "# r0\n"})
    now = later(monkeypatch)
    gh = LegGh(tmp_path / "origin.git", {7: ("cloud/sweep", now - dt.timedelta(hours=1), "dirty")})
    monkeypatch.setattr(sync, "_protected_moves", lambda *args: None)
    assert sync_run(repo, monkeypatch, gh, "--no-push") == 0
    said = capsys.readouterr().out
    assert "what cloud/sweep changes could not be listed" in said and "changes a protected path" not in said
    assert not (repo / SOURCE).exists()


# ----- a space the cloud does not read ------------------------------------------------------------------
def a_space(tmp_path: Path, name: str) -> Path:
    """A project space of the person's on this computer, its repository ada/<name>, listed with the sync's own."""
    origin, root = tmp_path / "ada" / f"{name}.git", tmp_path / name
    git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    git(tmp_path, "clone", "-q", str(origin), str(root))
    make_space(root, profile="project", name=name)
    landed(root, "init")
    assert ghrest.repo_slug(root) == f"ada/{name}"
    listed = Path.home() / ".spaces" / "spaces.yaml"
    listed.parent.mkdir(parents=True, exist_ok=True)
    rows = (yaml.safe_load(listed.read_text(encoding="utf-8")) or {}).get("spaces") if listed.exists() else None
    listed.write_text(yaml.safe_dump({"spaces": (rows or []) + [{"root": str(root)}]}), encoding="utf-8")
    return root


def test_a_space_the_cloud_does_not_read_is_named_once(repo, tmp_path, monkeypatch):
    listed = Path.home() / ".spaces" / "spaces.yaml"
    listed.parent.mkdir(parents=True, exist_ok=True)
    listed.write_text(yaml.safe_dump({"spaces": [{"root": str(repo)}]}), encoding="utf-8")
    harbor = a_space(tmp_path, "ada-harbor")
    in_the_cloud(repo)
    assert sync_run(repo, monkeypatch, lambda *args: None, "--no-push") == 0
    [named] = items(repo)
    assert re.fullmatch(r"cloud-roots-[0-9a-f]{8}", named.pop("id"))
    assert named == {"kind": "request",
                     "text": "Your routines in the cloud do not read ada/ada-harbor: it was made after you moved them.",
                     "recommendation": "say 'run my routines in the cloud' again to add it",
                     "default": "it is not swept or briefed until then", "depends": 1}
    first = items(repo)
    assert sync_run(repo, monkeypatch, lambda *args: None, "--no-push") == 0
    assert items(repo) == first
    assert sync_run(harbor, monkeypatch, lambda *args: None, "--no-push") == 0
    assert not (harbor / ITEMS).exists()   # named in the workspace, where the record is
    # the person answered it; a space made later is named again, with the one before it
    write(repo / "log.md", f"# Log\n\n- 2026-10-27 no {first[0]['id']} by Ada Okafor\n")
    landed(repo, "answered")
    a_space(tmp_path, "ada-port")
    assert sync_run(repo, monkeypatch, lambda *args: None, "--no-push") == 0
    [again] = items(repo)
    assert again["id"] != first[0]["id"] and again["text"].splitlines() == [
        "Your routines in the cloud do not read ada/ada-harbor: it was made after you moved them.",
        "Your routines in the cloud do not read ada/ada-port: it was made after you moved them."]
    in_the_cloud(repo, roots=[ghrest.repo_slug(repo), "ada/ada-harbor", "ada/ada-port"])
    assert sync_run(repo, monkeypatch, lambda *args: None, "--no-push") == 0
    assert items(repo) == []


# ----- the cloud landing's own guards ------------------------------------------------------------------------
def test_a_force_staged_private_file_never_lands_from_the_cloud(repo, monkeypatch):
    begin(repo)
    write(repo / "personal" / "diary.md", "# Diary\n\nprivate\n")
    git(repo, "add", "-f", "personal/diary.md")
    write(repo / "work" / "notes.md", "# Notes\n\nline two\n")
    code, _ = land(repo, monkeypatch, CloudGh(Path()), "--no-push")
    assert code == 0
    landed_files = git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert "work/notes.md" in landed_files and "personal/diary.md" not in landed_files
    assert not git(repo, "ls-tree", "-r", "--name-only", "HEAD", "personal").stdout.strip()
    assert "personal/diary.md" not in git(repo, "diff", "--cached", "--name-only").stdout


def test_a_nested_checkout_or_a_gitlink_staged_by_force_never_lands_from_the_cloud(repo, monkeypatch):
    """A checkout inside the space staged as a gitlink at its own path, and a gitlink staged with no checkout behind
    it: neither is a mount .gitmodules names, and no pin lands from the cloud."""
    begin(repo)
    nested = repo / "work" / "tool"
    git(repo, "init", "-q", "--initial-branch=main", str(nested))
    write(nested / "a.md", "# A\n")
    git(nested, "add", "-A")
    git(nested, "commit", "-q", "-m", "one")
    git(repo, "add", "work/tool")   # git stages the embedded checkout's gitlink, and warns
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},work/ghost")
    (repo / "work" / "ghost").mkdir()
    write(repo / "work" / "notes.md", "# Notes\n\nline two\n")
    code, _ = land(repo, monkeypatch, CloudGh(Path()), "--no-push")
    assert code == 0
    assert git(repo, "ls-tree", "-r", "--name-only", "HEAD", "work").stdout.split() == ["work/notes.md"]
    assert "work/notes.md" in git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert not git(repo, "diff", "--cached", "--name-only").stdout.strip()


def test_every_git_a_cloud_run_starts_has_the_file_monitor_off(repo, monkeypatch):
    """The carry and the landing alike, through git's own environment, which reaches a mount's git; any config the
    environment gave git keeps its effect, and the environment is put back after. The tools' own sign-in through
    gh (`doctor.use_gh_for_git`, set for the whole process) may stand beside them."""
    import doctor
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "Ada Okafor")
    seen, real = [], sync.git

    def watched(root, *args, **kwargs):
        count = int(os.environ["GIT_CONFIG_COUNT"])
        seen.append({(os.environ.get(f"GIT_CONFIG_KEY_{i}"), os.environ.get(f"GIT_CONFIG_VALUE_{i}"))
                     for i in range(count) if os.environ.get(f"GIT_CONFIG_KEY_{i}") != doctor.GITHUB_HELPER})
        return real(root, *args, **kwargs)
    monkeypatch.setattr(sync, "git", watched)
    cloud_branch(repo.parent, {SOURCE: "# r0\n"})
    assert begin(repo)["carried"]
    write(repo / "work" / "notes.md", "# Notes\n\nline two\n")
    code, _ = land(repo, monkeypatch, CloudGh(Path()), "--no-push")
    assert code == 0 and seen
    assert all(pairs == {("user.name", "Ada Okafor"), ("core.fsmonitor", "false")} for pairs in seen), seen
    assert git(repo, "log", "-1", "--format=%an").stdout.strip() == "Ada Okafor"
    left = [os.environ.get(f"GIT_CONFIG_KEY_{i}") for i in range(int(os.environ["GIT_CONFIG_COUNT"]))]
    assert left[0] == "user.name" and "core.fsmonitor" not in left and set(left[1:]) <= {doctor.GITHUB_HELPER}


def test_a_mounts_file_monitor_never_runs_in_a_cloud_run_even_when_a_merge_stops(repo, tmp_path, monkeypatch):
    """The real thing: a mount whose git config names a file monitor, and a merge of main that stops and that the
    rules settle, whose list of conflicts looks into every mount."""
    origin, work = remote_space(tmp_path, "harbor")
    write(work / "index.md", "# Harbor\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "one")
    git(work, "push", "-q", "origin", "HEAD:main")
    mounted(repo, origin, "mounts/harbor")
    marker, hook = tmp_path / "monitor-ran", tmp_path / "monitor.sh"
    hook.write_text(f'#!/bin/sh\necho ran >> "{marker.as_posix()}"\nexit 1\n', encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    git(repo / "mounts" / "harbor", "config", "core.fsmonitor", hook.as_posix())
    git(repo, "diff", "--name-only")
    if not marker.exists():
        pytest.skip("this git runs no file monitor hook")
    item = "_inbound/needs-you/sync.yaml"   # an answered item deleted here and changed on main: the rules settle it
    commit(repo, "base", {item: "items:\n- id: a\n"})
    git(repo, "push", "-q", "origin", "main")
    on_main(tmp_path, {item: "items:\n- id: a\n  text: changed there\n"})
    commit(repo, "the person answered it", {item: None})
    begin(repo)
    write(repo / "work" / "notes.md", "# Notes\n\nline two\n")
    marker.unlink(missing_ok=True)
    code, notes = land(repo, monkeypatch, CloudGh(Path()), "--no-push")
    assert code == 0 and notes.get("resolved") == 1, notes
    assert not marker.exists()


def test_the_cloud_status_never_runs_a_mounts_file_monitor(repo, monkeypatch):
    """A mount's own git config could name a file monitor, a command `git status` would run: a cloud run's status
    turns it off, and still looks into the mounts, so a pin move is seen."""
    seen, real = [], sync.git
    monkeypatch.setattr(sync, "git", lambda root, *a, **k: (seen.append(a), real(root, *a, **k))[1])
    begin(repo)
    write(repo / "work" / "notes.md", "# Notes\n\nline two\n")
    code, _ = land(repo, monkeypatch, CloudGh(Path()), "--no-push")
    statuses = [args for args in seen if "status" in args]
    assert code == 0 and statuses
    for args in statuses:
        assert args[:2] == ("-c", "core.fsmonitor=false") and args[2] == "status", args
        assert not any(arg.startswith("--ignore-submodules") for arg in args)
