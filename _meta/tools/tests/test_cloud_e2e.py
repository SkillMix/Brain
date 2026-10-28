"""End to end (spec 4.2 to 4.5a): the cloud sweep and the cloud daily, each run as its prompt runs it, against two
throwaway bare origins, Ada's workspace and the brain it mounts. Each run has a machine of its own: a folder standing
in for /home/user (`SPACES_CLOUD_HOME`) with fresh shallow clones, as the platform clones them, and a home folder the
last run's guard state went with. The tools run in this process, in the prompt's order: `cloud.py begin`, the export
from the cursors begin printed, `sweep.py`, `chain.py ... --cloud`, the agent's own run line, then `sync.py --cloud`
for every root, the workspace last. GitHub is a fake that keeps pull requests in memory and merges them as the
platform does, with git's own merge, which knows none of the space's merge rules; a `gh` the tools would start
themselves is not installed here. Ada's computer is one more clone, whose sync adopts the pull request nothing
merged."""
import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest
import yaml

import agents_md
import chain
import cloud
import ghrest
import pages
import routine
import sweep
import sync
from conftest import make_space
from test_sync import git, write
from test_sync_cloud import land

TEMPLATE = Path(__file__).resolve().parents[3]
TASKS = {"sweep": "trig_01AAAAAAAA", "daily": "trig_01BBBBBBBB"}
GRANT = {"read": ["mcp__mail__get_message", "mcp__mail__search_threads"], "write": [],
         "send": ["mcp__mail__send_message"]}
SPACE, BRAIN = "ada/ada-space", "ada/ada-brain"
RUNS = "_meta/state/runs.jsonl"
WATERMARK = "_meta/state/sweep-watermark.yaml"
FEED = "_meta/state/feed/"
# The mail in Ada's mailbox: its id, how many hours before the test it came, and the run that first finds it there.
MAIL = (("mail-0001", 30, 1), ("mail-0002", 20, 1), ("mail-0003", 10, 2), ("mail-0004", 5, 3), ("mail-0005", 2, 4))


# ----- GitHub ----------------------------------------------------------------------------------------------------
class Platform:
    """GitHub for the repositories under `hub` (`<owner>/<name>.git`), through REST (`gh api`) and `gh run` only. A
    POST opens a pull request. Reading one is when the platform acts on it, as its auto-merge would: one whose head
    main holds is merged (its commits landed through another pull request); one whose branch is in `red` has a
    failed check and waits (`blocked`); any other is merged with git's own merge, which knows none of the space's
    merge rules, and is `dirty` when that merge stops. `before_read(slug, number, branch)` runs before a read: what
    happens meanwhile. A failed check rerun fails again, as a new attempt with a link of its own. `slept` keeps the
    waits between reads."""

    def __init__(self, hub: Path):
        self.hub, self.pulls, self.red, self.calls, self.slept = hub, {}, set(), [], []
        self.attempts, self.before_read = {}, None

    def origin(self, slug: str) -> Path:
        return self.hub / f"{slug}.git"

    def head(self, slug: str, branch: str) -> str:
        return git(self.origin(slug), "rev-parse", "-q", "--verify", f"refs/heads/{branch}", check=False).stdout.strip()

    def holds(self, slug: str, sha: str) -> bool:
        return bool(sha) and git(self.origin(slug), "merge-base", "--is-ancestor", sha, "refs/heads/main",
                                 check=False).returncode == 0

    def merge(self, slug: str, branch: str) -> bool:
        """The platform's merge of `branch` into main: a merge commit, as GitHub makes it, with git's plain text merge
        for every file (`info/attributes` outranks the space's own .gitattributes, whose union rule GitHub ignores)."""
        work = self.hub / "platform" / slug.replace("/", "-")
        if not work.exists():
            git(self.hub, "clone", "-q", str(self.origin(slug)), str(work))
            (work / ".git" / "info").mkdir(parents=True, exist_ok=True)
            (work / ".git" / "info" / "attributes").write_text("* merge=text\n", encoding="utf-8")
        git(work, "fetch", "-q", "origin")
        git(work, "checkout", "-q", "-B", "main", "origin/main")
        if git(work, "merge", "-q", "--no-ff", "--no-edit", f"origin/{branch}", check=False).returncode != 0:
            git(work, "merge", "--abort", check=False)
            return False
        git(work, "push", "-q", "origin", "main")
        return True

    def act(self, number: int) -> dict:
        pull = self.pulls[number]
        sha = self.head(pull["slug"], pull["branch"])
        if not pull["merged"]:
            if self.holds(pull["slug"], sha):
                pull["merged"] = True
            elif (pull["slug"], pull["branch"]) in self.red:
                pull["mergeable"] = "blocked"
            elif self.merge(pull["slug"], pull["branch"]):
                pull["merged"] = True
            else:
                pull["mergeable"] = "dirty"
        return {"number": number, "state": "closed" if pull["merged"] else "open", "merged": pull["merged"],
                "mergeable_state": "unknown" if pull["merged"] else pull["mergeable"], "head": {"sha": sha}}

    def settle(self) -> None:
        """The platform between two runs: every open pull request is acted on, and again while one merged, which may
        have landed another's commits."""
        while any(not pull["merged"] and self.act(number)["merged"] for number, pull in list(self.pulls.items())):
            pass

    def open(self, slug: str, branch: str | None = None) -> list:
        return [n for n, p in self.pulls.items() if p["slug"] == slug and not p["merged"]
                and not self.holds(slug, self.head(slug, p["branch"])) and branch in (None, p["branch"])]

    def __call__(self, *args):   # set on the class, an instance is not bound: no self of the run
        self.calls.append(args)
        route = next((a for a in args if a.startswith("repos/")), "")
        slug, out = "/".join(route.split("/")[1:3]), ""
        if args[:3] == ("api", "-X", "POST") and route.endswith("/pulls"):
            number = 101 + len(self.pulls)
            self.pulls[number] = {"slug": slug, "branch": next(a[len("head="):] for a in args if a.startswith("head=")),
                                  "created_at": pages.iso(dt.datetime.now(dt.timezone.utc)), "merged": False,
                                  "mergeable": "unknown"}
            out = json.dumps({"number": number})
        elif "/pulls?" in route:
            wanted = unquote(route.split("&head=")[1].split("&")[0]).split(":", 1)[1] if "&head=" in route else None
            out = json.dumps([] if "state=closed" in route else [
                {"number": n, "title": "chore: sync", "draft": False, "auto_merge": None,
                 "created_at": self.pulls[n]["created_at"], "html_url": f"https://github.com/{slug}/pull/{n}",
                 "head": {"ref": self.pulls[n]["branch"], "sha": self.head(slug, self.pulls[n]["branch"]),
                          "repo": {"full_name": slug}},
                 "base": {"repo": {"full_name": slug}}} for n in self.open(slug, wanted)])
        elif m := re.fullmatch(r"repos/[^/]+/[^/]+/pulls/(\d+)", route):
            pull = self.pulls[int(m[1])]
            if self.before_read:
                self.before_read(slug, int(m[1]), pull["branch"])
            out = json.dumps(self.act(int(m[1])))
        elif m := re.fullmatch(r"repos/[^/]+/[^/]+/commits/([^/]+)/check-runs\?.*", route):
            red = any((slug, b) in self.red and self.head(slug, b) == m[1] for b in ("cloud/sweep", "cloud/daily"))
            run = int(m[1][:6], 16)
            out = json.dumps({"check_runs": [{"name": "checks", "conclusion": "failure" if red else "success",
                                              "details_url": f"https://github.com/{slug}/actions/runs/{run}/job/"
                                                             f"{run * 10 + self.attempts.get(run, 0)}"}]})
        elif args[:2] == ("run", "rerun"):
            self.attempts[int(args[2])] = self.attempts.get(int(args[2]), 0) + 1
        elif args[:2] != ("run", "view") and args != ("--version",):
            return subprocess.CompletedProcess(["gh", *args], 1, "", "unknown command")
        return subprocess.CompletedProcess(["gh", *args], 0, out, "")

    def posts(self, slug: str) -> list:
        return [n for n, p in self.pulls.items() if p["slug"] == slug]


# ----- Ada's spaces ----------------------------------------------------------------------------------------------
@pytest.fixture
def world(tmp_path, monkeypatch):
    """Ada's workspace and their brain on GitHub (two bare origins), the workspace mounting the brain at its pin, the
    routines moved to the cloud an hour ago; GitHub as the fake, and the person's computer, one more clone."""
    config = tmp_path / "gitconfig"   # longpaths: Windows needs it under a long basetemp; other systems ignore it
    config.write_text('[protocol "file"]\n\tallow = always\n[user]\n\tname = cloud\n\temail = cloud@example.test\n'
                      "[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n[core]\n\tlongpaths = true\n",
                      encoding="utf-8")
    for key, value in (("GIT_CONFIG_GLOBAL", str(config)), ("GIT_CONFIG_NOSYSTEM", "1"), ("GIT_TERMINAL_PROMPT", "0")):
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("SPACE_NOW", raising=False)
    hub = tmp_path / "github"
    platform = Platform(hub)
    real_run, stray = subprocess.run, []

    def no_gh(argv, *args, **kwargs):   # only the fake answers for GitHub: a gh started directly is not installed
        if isinstance(argv, (list, tuple)) and argv and argv[0] == "gh":
            stray.append(tuple(argv))
            raise FileNotFoundError(2, "not installed here", "gh")
        return real_run(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", no_gh)
    monkeypatch.setattr(ghrest, "runner", lambda cwd: platform)
    monkeypatch.setattr(sync.Run, "gh", platform)
    monkeypatch.setattr(sync, "_sleep", lambda seconds: platform.slept.append(seconds))
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    seeds = {}
    for slug, profile, extra in ((BRAIN, "brain", {}),
                                 (SPACE, "personal", {"sources": [{"name": "mail"}], "mounts": [{"name": "brain"}]})):
        origin = platform.origin(slug)
        git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
        seed = tmp_path / "seeds" / slug.split("/")[1]
        git(tmp_path, "clone", "-q", str(origin), str(seed))
        make_space(seed, profile=profile, name=slug.split("/")[1], owner_email="ada@example.test", **extra)
        for name in (".gitignore", ".gitattributes"):
            shutil.copy(TEMPLATE / name, seed / name)
        agents_md.write(seed)   # as onboarding leaves every space (`onboard.finish_space`)
        if profile == "personal":
            git(seed, "submodule", "add", "-q", "-b", "main", platform.origin(BRAIN).as_uri(), "brain")
            routine.claim_cloud(seed, TASKS, "Europe/Amsterdam", [SPACE, BRAIN], GRANT, now - dt.timedelta(hours=1))
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "init")
        git(seed, "push", "-q", "-u", "origin", "main")
        seeds[slug] = seed
    computer = tmp_path / "computer" / "ada-space"   # Ada's own checkout, their sync every 15 minutes
    git(tmp_path, "clone", "-q", str(platform.origin(SPACE)), str(computer))
    mailbox = [{"id": rid, "source": "mail", "kind": "mail", "at": pages.iso(now - dt.timedelta(hours=hours)),
                "from": "team@example.test", "to": ["ada@example.test"], "subject": f"Week notes {n}",
                "text": f"Notes for week {n}.", "identifiers": {"thread": f"t-{n}"}, "attachments": [], "_run": run}
               for n, (rid, hours, run) in enumerate(MAIL, start=1)]
    return {"tmp": tmp_path, "monkeypatch": monkeypatch, "platform": platform, "computer": computer,
            "mailbox": mailbox, "stray": stray, "seeds": seeds,
            "start": {slug: git(seed, "rev-parse", "HEAD").stdout.strip() for slug, seed in seeds.items()}}


def machine(world, name: str) -> list:
    """A run's own machine: its /home/user holding the platform's fresh shallow clones of the attached repositories,
    and a home folder without the last run's guard state. The clones, the workspace first."""
    home = world["tmp"] / name
    home.mkdir()
    world["monkeypatch"].setenv("SPACES_CLOUD_HOME", str(home))
    shutil.rmtree(Path.home() / ".spaces" / "routines", ignore_errors=True)
    for slug in (SPACE, BRAIN):
        git(world["tmp"], "clone", "-q", "--depth", "1", world["platform"].origin(slug).as_uri(),
            str(home / slug.split("/")[1]))
    return [(home / slug.split("/")[1]).resolve() for slug in (SPACE, BRAIN)]


# ----- a run, as its prompt runs it ------------------------------------------------------------------------------
def begin(workspace: Path, chain_name: str, capsys) -> dict:
    """Step 0: the begin command, exactly once; its JSON."""
    capsys.readouterr()
    code = cloud.main(["begin", str(workspace), "--chain", chain_name])
    out = capsys.readouterr().out
    said = json.loads(out[out.rfind("\n{\n") + 1 if not out.startswith("{") else 0:])
    assert code == 0, said
    return said


def export(root: Path, cursors: dict, mailbox: list, run: int) -> list:
    """Step 1, the sweep skill's export: the mail in the mailbox at this run newer than begin's cursor, oldest first,
    into the root's feed file, which a cloud run never pushes. The ids exported."""
    if "mail" not in cursors:
        return []
    since = pages.parse_iso(cursors["mail"])
    records = [{k: v for k, v in r.items() if k != "_run"} for r in mailbox
               if r["_run"] <= run and pages.parse_iso(r["at"]) > since]
    write(root / FEED / "mail.jsonl", "".join(json.dumps(r) + "\n" for r in records))
    return [r["id"] for r in records]


def agent_line(root: Path, routine_name: str, notes: dict) -> dict:
    """The agent's own run line (the sweep prompt's step 7, the daily's step 4)."""
    line = {"routine": routine_name, "at": pages.iso(dt.datetime.now(dt.timezone.utc)),
            "space": yaml.safe_load((root / "space.yaml").read_text(encoding="utf-8"))["name"], "cost_tokens": 0,
            "notes": {"step": "agent", "cloud": True, "complete": True, **notes}}
    with (root / RUNS).open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(line) + "\n")
    return line


def sweep_until_landing(world, name: str, run: int, capsys) -> dict:
    """A cloud sweep up to its landing: begin, then for each root the export, the attachments, the sweep, the chain
    and the agent's line."""
    roots = machine(world, name)
    said = begin(roots[0], "sweep", capsys)
    assert said["roots"] == [r.as_posix() for r in roots] and said["unreachable"] == []
    exported, lines = {}, []
    for root in roots:
        exported[root.name] = export(root, said["cursors"][root.as_posix()], world["mailbox"], run)
        capsys.readouterr()
        assert sweep.main([str(root), "--attachments"]) == 0 and capsys.readouterr().out == ""
        assert sweep.main([str(root)]) == 0
        assert chain.main(["sweep", str(root), "--cloud"]) == 0
        lines.append(agent_line(root, "sweep-chain", {"unreachable": [], "skipped": {}}))
    return {"roots": roots, "begin": said, "exported": exported, "lines": lines}


def land_all(world, roots: list, chain_name: str) -> dict:
    """The last step: `sync.py --root <root> --cloud <chain>` for each root, the workspace last. Each root's notes."""
    notes = {}
    for root in reversed(roots):
        code, notes[root.name] = land(root, world["monkeypatch"], world["platform"], chain=chain_name)
        assert code == 0, notes[root.name]
    return notes


# ----- what GitHub holds -----------------------------------------------------------------------------------------
def shown(world, slug: str, ref: str, rel: str) -> str:
    return git(world["platform"].origin(slug), "show", f"{ref}:{rel}", check=False).stdout


def lines_on(world, slug: str, ref: str = "main") -> list:
    return [json.loads(raw) for raw in shown(world, slug, ref, RUNS).splitlines() if raw.strip()]


def tree(world, slug: str, ref: str = "main") -> list:
    return git(world["platform"].origin(slug), "ls-tree", "-r", "--name-only", ref).stdout.split()


# ----- the test --------------------------------------------------------------------------------------------------
def test_cloud_sweeps_and_a_daily_land_end_to_end(world, capsys, monkeypatch):
    platform, mailbox = world["platform"], world["mailbox"]
    at = {r["id"]: r["at"] for r in mailbox}
    written = []

    # 1. The first sweep: nothing to carry, the cursor ninety days back, the mail exported, swept and landed.
    first = sweep_until_landing(world, "sweep-1", 1, capsys)
    assert first["begin"]["carried"] == [] and first["begin"]["items"] == []
    assert first["exported"] == {"ada-space": ["mail-0001", "mail-0002"], "ada-brain": []}
    written += first["lines"]
    landed = land_all(world, first["roots"], "sweep")
    # 2. It pushed cloud/sweep and opened one pull request through REST, which the platform merged.
    assert landed["ada-space"]["landing"] == "merged" and landed["ada-space"]["pr"] == "opened"
    assert len(platform.posts(SPACE)) == 1 and platform.open(SPACE) == [] and platform.head(SPACE, "cloud/sweep")
    assert {"mail-0001", "mail-0002"} <= {Path(p).stem for p in tree(world, SPACE)
                                          if p.startswith("knowledge/sources/")}

    # 3. The daily begins, and a second sweep begins on its own machine meanwhile.
    daily = machine(world, "daily-1")
    said = begin(daily[0], "daily", capsys)
    assert "cursors" not in said and said["carried"] == []
    for root in daily:
        assert chain.main(["daily", str(root), "--cloud"]) == 0
        written.append(agent_line(root, "daily-chain", {"notices": 0, "briefing": "no mail connector in this test"}))
    second = sweep_until_landing(world, "sweep-2", 2, capsys)
    assert second["exported"]["ada-space"] == ["mail-0003"]   # from the first sweep's marks, which landed
    written += second["lines"]
    meanwhile = {}

    def the_second_sweep_lands(slug, number, branch):   # while the daily's pull request waits for its checks
        if slug == SPACE and branch == "cloud/daily" and not meanwhile:
            meanwhile.update(land_all(world, second["roots"], "sweep"))
    platform.before_read = the_second_sweep_lands
    landed = land_all(world, daily, "daily")
    platform.before_read = None
    # 4. The sweep merged first, so the daily's pull request conflicted on GitHub (the run lines, which GitHub
    # merges without the union rule); its sync merged main in again with the rules, pushed, and it merged.
    assert meanwhile["ada-space"]["landing"] == "merged"
    assert landed["ada-space"]["landing"] == "merged" and landed["ada-space"]["remerged"] == 1

    # 5. A third sweep whose pull request stays red: the next sweep's begin carries it, and so does its push.
    platform.red.add((SPACE, "cloud/sweep"))
    third = sweep_until_landing(world, "sweep-3", 3, capsys)
    assert third["exported"]["ada-space"] == ["mail-0004"]
    written += third["lines"]
    landed = land_all(world, third["roots"], "sweep")
    assert landed["ada-space"]["landing"] == "red"
    [stuck] = platform.open(SPACE, "cloud/sweep")
    fourth = sweep_until_landing(world, "sweep-4", 4, capsys)
    assert fourth["roots"][0].as_posix() in fourth["begin"]["carried"]
    assert fourth["begin"]["cursors"][fourth["roots"][0].as_posix()]["mail"] == at["mail-0004"]   # what it read
    assert fourth["exported"]["ada-space"] == ["mail-0005"]   # never mail-0004 again
    written += fourth["lines"]
    landed = land_all(world, fourth["roots"], "sweep")
    assert landed["ada-space"]["landing"] == "red" and platform.open(SPACE, "cloud/sweep") == [stuck]

    # 6. Ada's computer: its sync leaves the young pull request to its run, and adopts it half an hour on.
    computer, opened = world["computer"], len(platform.pulls)
    assert sync.main(["--root", str(computer)]) == 0
    assert not list(computer.glob("knowledge/sources/*/*/mail-0004.md")) and len(platform.pulls) == opened
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=sync.ADOPT_MINUTES + 10)
    monkeypatch.setenv("SPACE_NOW", pages.iso(later.replace(microsecond=0)))
    assert sync.main(["--root", str(computer)]) == 0
    monkeypatch.delenv("SPACE_NOW")
    notes = json.loads((computer / RUNS).read_text(encoding="utf-8").splitlines()[-1])["notes"]
    assert notes["adopted"] == [stuck], notes
    platform.settle()   # the computer's own pull request merges on green, and GitHub marks the cloud one merged
    assert platform.open(SPACE) == [] and platform.pulls[stuck]["merged"]

    # 7. Main holds every run line once, every witness page once, the latest mark, no feed and no protected change.
    on_main = lines_on(world, SPACE)
    for line in written:
        if line["space"] == "ada-space":
            assert on_main.count(line) == 1, line
        else:   # the brain's sweeps change only run lines and marks: quiet, each carried by the next, no pull request
            assert lines_on(world, BRAIN, "main" if line["routine"] == "daily-chain" else "cloud/sweep").count(line) \
                == 1, line
    assert [platform.pulls[n]["branch"] for n in platform.posts(BRAIN)] == ["cloud/daily"]
    counted = {name: sum(1 for line in on_main if line.get("routine") == name)
               for name in ("sweep-chain", "daily-chain", "sweep_chain", "daily")}
    assert counted == {"sweep-chain": 4, "daily-chain": 1, "sweep_chain": 4, "daily": 1}
    clouds = sorted((line for line in on_main if line.get("routine") == "sync" and line.get("cloud")),
                    key=lambda line: line["at"])   # a union merge keeps both sides' lines, not their order in time
    assert [line["cloud"] for line in clouds] == ["sweep", "daily", "sweep", "sweep", "sweep"]
    assert [line["notes"]["carried"] for line in clouds if line["cloud"] == "sweep"] == [False, False, False, True]
    assert not [line for line in clouds if line["notes"]["left_out"] or line["notes"]["held"]]
    assert "cloud-protected" not in shown(world, SPACE, "main", "_inbound/needs-you/sync-cloud.yaml")
    pages_of = [Path(p).stem for p in tree(world, SPACE) if p.startswith("knowledge/sources/") and p.endswith(".md")]
    assert sorted(pages_of) == sorted(at)
    assert sum(line["notes"]["witness"] for line in on_main if line.get("routine") == "sweep") == len(at)
    assert yaml.safe_load(shown(world, SPACE, "main", WATERMARK)) == {"at": at["mail-0005"],
                                                                     "feeds": {"mail": at["mail-0005"]}}
    for slug in (SPACE, BRAIN):
        assert not [p for p in tree(world, slug) if p.startswith(FEED)]
        changed = git(platform.origin(slug), "diff", "--name-only", world["start"][slug], "main").stdout.split()
        assert changed and sync.protected_paths(world["seeds"][slug], changed) == [], changed
        # Only the platform wrote to main, merging pull requests: no run pushed to it.
        merged = git(platform.origin(slug), "log", "--first-parent", "--format=%s", "main").stdout.splitlines()
        assert merged[-1] == "init" and all(s.startswith("Merge remote-tracking branch 'origin/") for s in merged[:-1])
    # The pull request calls went through the REST helpers (the fake), never `gh pr` or GraphQL; a tool that starts gh
    # itself (the access check) only reads, through REST: never GraphQL, never a write.
    assert all(c[0] in ("api", "run", "--version") and "graphql" not in " ".join(c) for c in platform.calls)
    writes = {"-X", "--method", "-f", "-F", "--field", "--raw-field", "--input"}
    assert all(c[1] == "api" and "graphql" not in " ".join(c) and not writes & set(c) for c in world["stray"])
