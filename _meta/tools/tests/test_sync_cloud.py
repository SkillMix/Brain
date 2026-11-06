"""sync.py --cloud sweep: a cloud run lands its own files once, on its chain's branch, through
one REST pull request, and waits for the merge (spec 4.3). Only the sweep runs in the cloud.
Against a throwaway bare origin and clones, as in test_sync.py; GitHub is a fake that answers the
REST routes. The secret below is assembled from parts so this file passes the scan itself."""
import datetime as dt
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest
import yaml

import cloud
import pages
import scan
import sync
from test_sync import KEY_BLOCK, git, mounted, remote_space, repo, write  # noqa: F401  (the repo fixture)
from test_sync_merge import SOURCE, WATERMARK, ClosedPulls, clone_of, cloud_branch, commit, marks, on_main

ITEMS = "_inbound/needs-you/sync-cloud.yaml"
RUNS = "_meta/state/runs.jsonl"
ANSWERS = "_meta/tests/answers.yaml"
QUESTION = {"question": "What are the notes?", "subject": "", "expect": ["line one"], "asker": "owner"}
WORD = "pass" + "word"   # assembled, so this file passes the scan itself
TOKEN = "gh" + "p_" + "a" * 36
FAILED_LOG = ("checks\tTests as data\t2026-10-21T18:24:14Z answers: 0 passed, 1 failed, 0 skipped\n"
              "checks\tTests as data\t2026-10-21T18:24:14Z   FAIL What are the notes? (both, in ): "
              "missing ['line one']\n")


OWNER = "owner-gh"   # the login the cloud's token is (`gh api user`)
PUSHED: dict = {}    # {(origin, branch): sha} each landing (`land`) pushed: GitHub's record of who pushed what


def heads(origin: Path) -> dict:
    out = git(origin, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads", check=False).stdout
    return dict(line.split(" ", 1) for line in out.splitlines() if " " in line)


class CloudGh:
    """GitHub as a cloud run's landing sees it, through REST (`gh api`) and `gh run` only. A POST
    opens a pull request; each read of one takes the next of `states` (the last one stays):
    `merged`, `closed` (closed without merging), or a mergeable state such as `clean` or `dirty`.
    `closed` also lists pull requests closed without merging, as their number and head: what GitHub
    answers when asked for the closed ones from a branch. A head in `red`, or every head when
    `red` is True, has a failed check whose log is `log`; each head's checks are their own run, each
    attempt of it its own job. Once a run is rerun, GitHub still lists the failed attempt for `lag`
    reads, then the new one, which ends as `rerun` says (None while it runs). `before_read(n)` runs
    before the n-th read (from 0): what happens on GitHub meanwhile. `slept` keeps the waits between
    reads."""

    def __init__(self, origin: Path, states=("merged",), red=(), log: str = "", before_read=None,
                 rerun: str | None = "success", lag: int = 0, closed=None):
        self.origin, self.states, self.red, self.log, self.before_read = origin, list(states), red, log, before_read
        self.closed = dict(closed or {})
        self.calls, self.pulls, self.reads, self.slept = [], {}, 0, []
        self.rerun, self.lag, self.attempts, self.since_rerun = rerun, lag, {}, 0

    def head(self, branch: str) -> str:
        return git(self.origin, "rev-parse", "-q", "--verify", f"refs/heads/{branch}", check=False).stdout.strip()

    def __call__(self, *args):   # set on the class, an instance is not bound: no self of the run
        self.calls.append(args)
        route = next((a for a in args if a.startswith("repos/")), "")
        out = ""
        if args[:3] == ("api", "-X", "POST") and route.endswith("/pulls"):
            number = 12 + len(self.pulls)
            self.pulls[number] = next(a[len("head="):] for a in args if a.startswith("head="))
            out = json.dumps({"number": number})
        elif "/pulls?" in route:
            wanted = unquote(route.split("&head=")[1].split("&")[0]).split(":", 1)[1] if "&head=" in route else None
            slug = "/".join(route.split("/")[1:3])
            out = json.dumps([{"number": n, "state": "closed", "merged_at": None, "head": {"ref": wanted, "sha": sha}}
                              for n, sha in self.closed.items()] if "state=closed" in route else [
                {"number": n, "title": "chore: sync", "draft": False, "auto_merge": None, "created_at": "",
                 "html_url": f"https://github.com/{slug}/pull/{n}",
                 "head": {"ref": ref, "sha": self.head(ref), "repo": {"full_name": slug}},
                 "base": {"repo": {"full_name": slug}}}
                for n, ref in self.pulls.items() if wanted in (None, ref)])
        elif m := re.fullmatch(r"repos/[^/]+/[^/]+/pulls/(\d+)", route):
            if self.before_read:
                self.before_read(self.reads)
            state = self.states[min(self.reads, len(self.states) - 1)]
            self.reads += 1
            merged, closed = state == "merged", state in ("merged", "closed")
            out = json.dumps({"number": int(m[1]), "state": "closed" if closed else "open", "merged": merged,
                              "mergeable_state": "unknown" if closed else state,
                              "head": {"sha": self.head(self.pulls[int(m[1])])}})
        elif m := re.fullmatch(r"repos/[^/]+/[^/]+/commits/([^/]+)/check-runs\?.*", route):
            run = int(m[1][:6], 16)
            attempt = self.attempts.get(run, 0)
            conclusion = "failure" if self.red is True or m[1] in self.red else "success"
            if attempt:   # rerun: the failed attempt is still listed for `lag` reads
                self.since_rerun += 1
                if self.since_rerun <= self.lag:
                    attempt -= 1
                else:
                    conclusion = self.rerun
            out = json.dumps({"check_runs": [{"name": "checks", "conclusion": conclusion, "details_url":
                                              f"https://github.com/o/r/actions/runs/{run}/job/{run * 10 + attempt}"}]})
        elif args[:3] == ("api", "user", "--jq"):
            out = OWNER + "\n"
        elif m := re.fullmatch(r"repos/[^/]+/[^/]+/activity\?ref=refs/heads/(.+)&per_page=1", route):
            sha = self.head(m[1])   # who last pushed the branch: the cloud when a landing (`land`) left it so
            who = OWNER if sha and PUSHED.get((str(self.origin), m[1])) == sha else "someone-else-gh"
            out = json.dumps([{"after": sha, "activity_type": "push", "actor": {"login": who}}] if sha else [])
        elif args[:2] == ("run", "view"):
            out = self.log
        elif args[:2] == ("run", "rerun"):
            self.attempts[int(args[2])] = self.attempts.get(int(args[2]), 0) + 1
            self.since_rerun = 0
        elif args != ("--version",):
            return subprocess.CompletedProcess(["gh", *args], 1, "", "unknown command")
        return subprocess.CompletedProcess(["gh", *args], 0, out, "")

    def posts(self) -> list:
        return [c for c in self.calls if c[:3] == ("api", "-X", "POST")]


def begin(clone: Path, chain: str = "sweep") -> dict:
    """What `cloud.py begin` does to the clone first: carry the earlier run, and lease its push."""
    return sync.carry(clone, chain, gh=ClosedPulls())


def land(clone: Path, monkeypatch, gh, *extra: str, chain: str = "sweep") -> tuple:
    """`sync.py --root <clone> --cloud <chain>`, a cloud run's last step: its exit code and the run's
    notes (a cloud run writes its line before its commit, so the notes after the push are printed)."""
    monkeypatch.setattr(sync.Run, "gh", gh)
    monkeypatch.setattr(sync, "_sleep", lambda seconds: gh.slept.append(seconds))
    made, real = [], sync.Run.__init__

    def init(self, *args, **kwargs):
        real(self, *args, **kwargs)
        made.append(self)
    monkeypatch.setattr(sync.Run, "__init__", init)
    origin = getattr(gh, "origin", None)
    was = heads(origin) if isinstance(origin, Path) else {}
    code = sync.main(["--root", str(clone), "--cloud", chain, *extra])
    for branch, sha in (heads(origin) if isinstance(origin, Path) else {}).items():
        if was.get(branch) != sha:   # what this landing pushed, GitHub names its login for
            PUSHED[(str(origin), branch)] = sha
    return code, made[0].notes


def branch_files(clone: Path, chain: str = "sweep") -> list:
    git(clone, "fetch", "-q", "origin")
    return sorted(git(clone, "diff", "--name-only", "origin/main", f"origin/cloud/{chain}").stdout.split())


def protected_files(clone: Path, chain: str = "sweep") -> list:
    """What `cloud/<chain>-protected` changes against main: what the gate would hold, on its own branch (round 3)."""
    git(clone, "fetch", "-q", "origin")
    return sorted(git(clone, "diff", "--name-only", "origin/main", f"origin/cloud/{chain}-protected").stdout.split())


def shown(clone: Path, ref: str, rel: str) -> str:
    return git(clone, "show", f"{ref}:{rel}").stdout


def line_at(clone: Path, ref: str) -> dict:
    return json.loads(shown(clone, ref, RUNS).splitlines()[-1])


def marker(clone: Path, chain: str = "sweep", **extra) -> Path:
    """The run's marker `cloud.py begin` writes under the home folder (a temporary one in every test): where the
    run began and its chain. `extra` adds what an earlier shape of begin wrote there too."""
    path = cloud.state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"workspace": clone.as_posix(), "chain": chain, **extra}) + "\n", encoding="utf-8")
    return path


# ----- the landing --------------------------------------------------------------------------------
def test_a_cloud_run_lands_on_its_chain_branch_through_one_rest_pull_request(repo, tmp_path, monkeypatch):
    main = git(repo, "rev-parse", "HEAD").stdout.strip()
    begin(repo)
    write(repo / SOURCE, "# r0\n")
    gh = CloudGh(tmp_path / "origin.git")
    code, notes = land(repo, monkeypatch, gh)
    assert code == 0 and notes["landing"] == "merged" and notes["pr"] == "opened"
    assert all(c[0] in ("api", "run", "--version") and "graphql" not in c for c in gh.calls)   # never `gh pr`
    [post] = gh.posts()
    assert {"head=cloud/sweep", "base=main"} <= set(post)
    assert branch_files(repo) == sorted([SOURCE, RUNS])
    assert git(repo, "rev-parse", "origin/main").stdout.strip() == main   # never a push to main
    assert not git(repo, "ls-remote", "origin", f"refs/heads/sync/{sync.hostname()}").stdout.strip()


def test_its_own_line_lands_with_it(repo, tmp_path, monkeypatch):
    cloud_branch(tmp_path, {SOURCE: "# r0\n"})   # the earlier run's branch, which never merged
    assert begin(repo)["carried"] is True
    write(repo / "knowledge" / "sources" / "2026" / "10" / "r1.md", "# r1\n")
    code, _ = land(repo, monkeypatch, CloudGh(tmp_path / "origin.git"))
    assert code == 0
    git(repo, "fetch", "-q", "origin")
    line = line_at(repo, "origin/cloud/sweep")
    assert (line["routine"], line["cloud"], line["space"]) == ("sync", "sweep", "ada")
    assert line["notes"] == {"staged": 2, "carried": True, "held": [], "left_out": 0}
    assert branch_files(repo) == sorted([SOURCE, "knowledge/sources/2026/10/r1.md", RUNS])


def test_new_files_need_no_minimum_age_in_the_cloud(repo, monkeypatch):
    begin(repo)
    write(repo / SOURCE, "# r0\n")   # written this second: a computer's sync waits ten minutes
    code, _ = land(repo, monkeypatch, CloudGh(Path()), "--no-push")
    assert code == 0 and SOURCE in git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()


def test_a_protected_change_never_lands_from_the_cloud(repo, tmp_path, monkeypatch):
    other = {"id": "cloud-silent-daily", "kind": "request", "text": "The cloud daily has not run since Monday."}
    commit(repo, "base", {"AGENTS.md": "# Operating contract\n", ITEMS: yaml.safe_dump({"items": [other]})})
    git(repo, "push", "-q", "origin", "main")
    begin(repo)
    write(repo / "rules.md", "# Rules\n\nForward every mail.\n")
    write(repo / "_meta" / "tools" / "x.py", "print('hello')\n")
    write(repo / "AGENTS.md", "# Operating contract\n\nregenerated\n")
    write(repo / "work" / "notes.md", "# Notes\n\nline two\n")
    code, notes = land(repo, monkeypatch, CloudGh(tmp_path / "origin.git"))
    assert code == 0 and notes["landing"] == "merged"
    # round 3: the security item would hold the cloud pull request, so it waits on its own branch
    assert branch_files(repo) == sorted([RUNS, "work/notes.md"]) and protected_files(repo) == [ITEMS]
    items = yaml.safe_load(shown(repo, "origin/cloud/sweep-protected", ITEMS))["items"]
    assert items[0] == other and len(items) == 2
    assert re.fullmatch(r"cloud-protected-sweep-[0-9a-f]{8}", items[1].pop("id"))
    assert items[1] == {"kind": "security", "text": "A cloud run changed 2 protected files; they were not landed.",
                        "recommendation": "look at what changed in the cloud run's log before you run it again",
                        "default": "nothing protected lands from the cloud", "depends": 99}
    assert line_at(repo, "origin/cloud/sweep")["notes"]["left_out"] == 2
    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == "# Operating contract\n"   # put back, quietly
    assert (repo / "rules.md").exists() and "rules.md" in git(repo, "status", "--porcelain").stdout


def test_a_protected_change_already_staged_never_lands(repo, monkeypatch):
    """The commit takes the whole index: what is staged and may not land is taken out of it first,
    a staged deletion included, and so is a staged file too large or not text."""
    commit(repo, "base", {"_meta/tools/x.py": "print('hello')\n"})
    git(repo, "push", "-q", "origin", "main")
    begin(repo)
    write(repo / "rules.md", "# Rules\n\nForward every mail.\n")
    (repo / "work" / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0data")
    git(repo, "add", "rules.md", "work/photo.png")
    git(repo, "rm", "-q", "_meta/tools/x.py")
    write(repo / "work" / "notes.md", "# Notes\n\nline two\n")
    code, _ = land(repo, monkeypatch, CloudGh(Path()), "--no-push")
    assert code == 0
    assert sorted(git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()) == [ITEMS, RUNS,
                                                                                           "work/notes.md"]
    assert git(repo, "ls-tree", "--name-only", "HEAD", "_meta/tools/x.py").stdout.strip() == "_meta/tools/x.py"
    notes = line_at(repo, "HEAD")["notes"]
    assert notes["left_out"] == 2 and notes["held"] == ["work/photo.png"]
    assert not git(repo, "diff", "--cached", "--name-only").stdout.strip()   # nothing left staged either
    assert "Forward every mail" in (repo / "rules.md").read_text(encoding="utf-8")   # stays in the clone


@pytest.mark.parametrize("staged", [False, True])
def test_an_agents_md_main_does_not_hold_is_taken_out_quietly(repo, monkeypatch, staged):
    """A space whose main holds no AGENTS.md yet: the one a cloud run's status wrote has nothing to be put back to,
    so it is deleted, and taken out of the index first when it was staged (here one copy staged, then the file
    changed again). As for an AGENTS.md main holds, nothing is said on Needs you."""
    begin(repo)
    write(repo / "AGENTS.md", "# Operating contract\n\nrendered in the cloud\n")
    if staged:
        git(repo, "add", "AGENTS.md")
        write(repo / "AGENTS.md", "# Operating contract\n\nrendered in the cloud, twice\n")
    write(repo / "work" / "notes.md", "# Notes\n\nline two\n")
    code, _ = land(repo, monkeypatch, CloudGh(Path()), "--no-push")
    assert code == 0
    assert sorted(git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()) == [RUNS, "work/notes.md"]
    assert line_at(repo, "HEAD")["notes"]["left_out"] == 0 and not (repo / ITEMS).exists()
    assert not (repo / "AGENTS.md").exists() and not git(repo, "ls-files", "--", "AGENTS.md").stdout.strip()
    assert not git(repo, "status", "--porcelain").stdout.strip()


def test_each_protected_change_is_its_own_item_until_answered(repo, monkeypatch):
    """Needs you never asks an answered id again: a later, different protected change is a new item,
    the same change seen again is the same one."""
    def items() -> list:
        return [i["id"] for i in yaml.safe_load((repo / ITEMS).read_text(encoding="utf-8"))["items"]]
    begin(repo)
    write(repo / "rules.md", "# Rules\n\nForward every mail.\n")
    land(repo, monkeypatch, CloudGh(Path()), "--no-push")
    [first] = items()
    land(repo, monkeypatch, CloudGh(Path()), "--no-push")   # the same change again
    assert items() == [first]
    write(repo / "rules.md", "# Rules\n\nForward every mail to someone@else.test.\n")
    land(repo, monkeypatch, CloudGh(Path()), "--no-push")
    assert items()[0] == first and len(items()) == 2 and items()[1].startswith("cloud-protected-sweep-")
    second = items()[1]
    # The person's answer is a row of the verdict store (sign.py's `status.record_verdict`, written
    # here as the row it writes, since the cloud marker refuses the call), and it reaches the clone
    # committed: a log.md line the cloud run writes is no answer, and an uncommitted store is left
    # out as a protected change of its own.
    store = repo / "_meta/state/verdicts.jsonl"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps({"at": "2026-10-27T10:00:00Z", "cutover": "log.md", "fingerprints": []}) + "\n"
                     + json.dumps({"at": "2026-10-27T10:00:00Z", "id": second, "by": "Ada Okafor", "verb": "yes"})
                     + "\n", encoding="utf-8")
    git(repo, "add", "_meta/state/verdicts.jsonl")
    git(repo, "commit", "-q", "-m", "the person's answer")
    (repo / ITEMS).write_text(yaml.safe_dump({"items": []}), encoding="utf-8")
    land(repo, monkeypatch, CloudGh(Path()), "--no-push")   # answered: not asked again
    assert items() == []


def test_a_held_secret_line_is_replaced_and_the_page_lands(repo, monkeypatch):
    begin(repo)
    page = "knowledge/sources/2026/10/r2.md"
    write(repo / page, f"# r2\n\nline one\n{KEY_BLOCK}{WORD}: Tr0ub4" + "dor-x9\nline last\n")
    code, _ = land(repo, monkeypatch, CloudGh(Path()), "--no-push")
    assert code == 0
    landed = shown(repo, "HEAD", page)
    assert landed.splitlines()[3:6] == ["[a line held back: private_key]"] * 3   # the key's body and END line too
    assert landed.splitlines()[6] == "[a line held back: password]"
    assert "abc" not in landed and "-----END" not in landed
    assert "line one" in landed and "line last" in landed and scan.scan_text(landed, page) == []
    assert (repo / page).read_text(encoding="utf-8") == landed


def test_a_private_key_is_held_back_to_its_end_line_or_the_end_of_the_file(repo, monkeypatch):
    begin(repo)
    key = ["-----BEGIN OPENSSH " + "PRIVATE KEY-----", "b3BlbnNzaC1rZXktdjEAAAAABG5vbmU", "QyNTUxOQAAACDx",
           "-----END OPENSSH " + "PRIVATE KEY-----"]
    ended, open_ended = "knowledge/sources/2026/10/r3.md", "knowledge/sources/2026/10/r4.md"
    write(repo / ended, "\r\n".join(["# r3", "", *key, "after", ""]))   # a page with Windows line ends
    write(repo / open_ended, "\n".join(["# r4", "", *key[:3], ""]))   # cut before its END line
    code, _ = land(repo, monkeypatch, CloudGh(Path()), "--no-push")
    assert code == 0
    held = "[a line held back: private_key]"
    assert (repo / ended).read_bytes().decode("utf-8").split("\r\n") == ["# r3", "", *[held] * 4, "after", ""]
    assert shown(repo, "HEAD", ended).splitlines() == ["# r3", "", *[held] * 4, "after"]
    assert shown(repo, "HEAD", open_ended).splitlines() == ["# r4", "", *[held] * 3]


def test_a_held_line_keeps_its_indent_and_yaml_still_reads(repo, monkeypatch):
    """In YAML (a YAML file, a page's frontmatter) a `key: value` line keeps its key and the value is
    held back; any other line keeps its indent. A file that would no longer read is left out whole,
    and named in the run line."""
    begin(repo)
    item, page, links = "_inbound/needs-you/mail.yaml", "knowledge/sources/2026/10/r5.md", "work/links.json"
    write(repo / item, f"items:\n- id: m1\n  body: |\n    Hello,\n    {TOKEN}\n  {WORD}: Tr0ub4dor-x9\n- {TOKEN}\n")
    write(repo / page, f"---\nid: r5\ntitle: 'Your {WORD}: Tr0ub4dor-x9'\n---\n# r5\n\n    {TOKEN}\n")
    write(repo / links, f'{{"token": "{TOKEN}"}}\n')
    code, _ = land(repo, monkeypatch, CloudGh(Path()), "--no-push")
    assert code == 0
    data = yaml.safe_load(shown(repo, "HEAD", item))
    assert data["items"] == [{"id": "m1", "body": "Hello,\n[a line held back: github_token]\n",
                              WORD: "[a line held back: password]"}, "[a line held back: github_token]"]
    landed = shown(repo, "HEAD", page)
    assert pages.split(landed)[0] == {"id": "r5", "title": "[a line held back: password]"}
    assert landed.splitlines()[-1] == "    [a line held back: github_token]"
    assert links not in git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert line_at(repo, "HEAD")["notes"]["held"] == [links]
    assert TOKEN in (repo / links).read_text(encoding="utf-8")   # left as it was, in the clone


def test_a_large_file_is_named_not_landed(repo, monkeypatch):
    begin(repo)
    write(repo / "work" / "big.md", "x" * (sync.CLOUD_MAX_BYTES + 1))
    (repo / "work" / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0data")
    write(repo / "work" / "notes.md", "# Notes\n\nline two\n")
    code, _ = land(repo, monkeypatch, CloudGh(Path()), "--no-push")
    assert code == 0
    assert sorted(git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()) == [RUNS, "work/notes.md"]
    assert line_at(repo, "HEAD")["notes"]["held"] == ["work/big.md", "work/photo.png"]


def test_nothing_to_land_writes_no_line_and_pushes_nothing(repo, tmp_path, monkeypatch):
    begin(repo)
    gh = CloudGh(tmp_path / "origin.git")
    code, _ = land(repo, monkeypatch, gh)
    assert code == 0 and not (repo / RUNS).exists() and gh.calls == []
    assert not git(repo, "ls-remote", "origin", "refs/heads/cloud/sweep").stdout.strip()


# ----- quiet runs ---------------------------------------------------------------------------------
def test_a_quiet_run_pushes_without_a_pull_request(repo, tmp_path, monkeypatch):
    begin(repo)
    write(repo / WATERMARK, marks("26", "26", "26"))
    gh = CloudGh(tmp_path / "origin.git")
    code, notes = land(repo, monkeypatch, gh)
    assert code == 0 and notes.get("pushed") and "landing" not in notes
    assert gh.calls == []   # GitHub is not even asked
    assert branch_files(repo) == sorted([RUNS, WATERMARK])


def test_a_quiet_branch_older_than_six_hours_opens_its_pull_request(repo, tmp_path, monkeypatch):
    seven = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    monkeypatch.setenv("GIT_COMMITTER_DATE", seven)
    cloud_branch(tmp_path, {WATERMARK: marks("25", "25", "25")})   # a quiet run seven hours ago, never merged
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    assert begin(repo)["carried"] is True
    write(repo / WATERMARK, marks("26", "26", "26"))
    gh = CloudGh(tmp_path / "origin.git")
    code, notes = land(repo, monkeypatch, gh)
    assert code == 0 and len(gh.posts()) == 1 and notes["landing"] == "merged"


PROOF = {"routine": "cloud-proof", "chain": "sweep", "edit": True, "tool": True, "network": True,
         "at": "2026-10-27T09:00:00Z"}


def test_a_proof_line_opens_its_pull_request(repo, tmp_path, monkeypatch):
    """A proof run writes only its line, and the move to the cloud reads that line on main."""
    begin(repo)
    write(repo / RUNS, json.dumps(PROOF) + "\n")
    gh = CloudGh(tmp_path / "origin.git")
    code, notes = land(repo, monkeypatch, gh)
    assert code == 0 and len(gh.posts()) == 1 and notes["landing"] == "merged"


@pytest.mark.parametrize("run", ["a proof", "a proof whose marker says proof", "a sweep after an earlier proof",
                                 "a sweep that carries an earlier proof"])
def test_a_landing_that_left_a_protected_file_out_always_asks_about_it(repo, tmp_path, monkeypatch, run):
    """Every landing leaves a protected file out and asks about it as one security item, a proof run's too: a proof
    tries no change made with a command, so a protected file it changed anyway is as much news as a sweep's. Nothing
    makes a landing skip the item: not a proof line on its branch, not main or a carried branch holding an earlier
    proof's line, not a marker an earlier shape of begin wrote with `proof`. Its sync line counts what it left out
    (`left_out`) and says nothing about proofs; the proof's own line still lands beside it."""
    if run == "a sweep after an earlier proof":
        commit(repo, "an earlier proof", {RUNS: json.dumps(PROOF) + "\n"})
        git(repo, "push", "-q", "origin", "main")
    if run == "a sweep that carries an earlier proof":
        cloud_branch(tmp_path, {RUNS: json.dumps(PROOF) + "\n"})   # the proof's branch, which never merged
    carried = begin(repo).get("carried") is True
    assert carried is (run == "a sweep that carries an earlier proof")
    if run == "a proof whose marker says proof":
        marker(repo, proof=True)
    else:
        marker(repo)
    write(repo / "rules.md", "# Rules\n\nproof\n")
    proof = run.startswith("a proof")
    line = PROOF if proof else {"routine": "sweep-chain", "at": "2026-10-27T10:00:00Z", "space": "ada",
                                "cost_tokens": 0, "notes": {"step": "agent", "cloud": True}}
    with (repo / RUNS).open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(line) + "\n")
    code, notes = land(repo, monkeypatch, CloudGh(tmp_path / "origin.git"))
    # with the item on its own branch (round 3), a sweep's branch holds only run lines: quiet, no pull request yet
    assert code == 0 and notes.get("landing") == ("merged" if proof or carried else None)
    landed = line_at(repo, "origin/cloud/sweep")
    assert landed["routine"] == "sync" and landed["notes"]["left_out"] == 1 and "proof" not in landed["notes"]
    assert "rules.md" not in branch_files(repo) and "rules.md" in git(repo, "status", "--porcelain").stdout
    assert branch_files(repo) == [RUNS] and protected_files(repo) == [ITEMS]   # the item on its own branch
    [item] = yaml.safe_load(shown(repo, "origin/cloud/sweep-protected", ITEMS))["items"]
    assert item["id"].startswith("cloud-protected-sweep-") and item["kind"] == "security"
    assert item["text"] == "A cloud run changed 1 protected file; it was not landed."
    if proof:
        assert PROOF in [json.loads(raw) for raw in shown(repo, "origin/cloud/sweep", RUNS).splitlines()]


# ----- the lease ----------------------------------------------------------------------------------
def test_the_push_is_leased_to_the_head_begin_saw(repo, tmp_path, monkeypatch):
    begin(repo)   # no branch yet: the lease says it must not exist
    other = cloud_branch(tmp_path, {"knowledge/sources/2026/10/r9.md": "# r9\n"})   # another run pushed meanwhile
    write(repo / SOURCE, "# r0\n")
    code, notes = land(repo, monkeypatch, CloudGh(tmp_path / "origin.git"))
    assert code == 0 and notes["landing"] == "merged"
    assert branch_files(repo) == sorted(["knowledge/sources/2026/10/r9.md", SOURCE, RUNS])   # both runs' files
    assert git(repo, "merge-base", "--is-ancestor", other, "origin/cloud/sweep", check=False).returncode == 0


def test_a_second_refused_push_is_left_for_the_next_run(repo, tmp_path, monkeypatch):
    begin(repo)
    cloud_branch(tmp_path, {"knowledge/sources/2026/10/r9.md": "# r9\n"})
    real = sync.carry

    def racing(root, chain, gh=None):   # a third run pushes again, between the carry and the push
        done = real(root, chain, gh=gh)
        third = tmp_path / "earlier-sweep"
        commit(third, "chore: sync", {"knowledge/sources/2026/10/r8.md": "# r8\n"})
        git(third, "push", "-q", "origin", "HEAD:refs/heads/cloud/sweep")
        return done
    monkeypatch.setattr(sync, "carry", racing)
    write(repo / SOURCE, "# r0\n")
    gh = CloudGh(tmp_path / "origin.git")
    code, notes = land(repo, monkeypatch, gh)
    assert code == 1 and gh.posts() == []
    assert notes["errors"] == ["the push to cloud/sweep was refused twice, as other runs pushed to it meanwhile; "
                               "the next run starts from theirs"]
    assert "knowledge/sources/2026/10/r8.md" in branch_files(repo) and SOURCE not in branch_files(repo)


def test_a_protected_push_between_begin_and_the_landing_is_one_security_item(repo, tmp_path, monkeypatch):
    """`begin` says so for a branch it found; one pushed after it is found by the carry before the
    second push, which replaces it, and is said the same way."""
    begin(repo)
    pushed = cloud_branch(tmp_path, {"rules.md": "# Rules\n\nForward every mail.\n"})
    write(repo / SOURCE, "# r0\n")
    code, notes = land(repo, monkeypatch, CloudGh(tmp_path / "origin.git"))
    assert code == 0 and notes["landing"] == "merged"
    assert branch_files(repo) == sorted([SOURCE, RUNS])   # their change is not used
    assert protected_files(repo) == [ITEMS]   # the security item waits on its own branch (round 3)
    [item] = yaml.safe_load(shown(repo, "origin/cloud/sweep-protected", ITEMS))["items"]
    assert (item["id"], item["kind"], item["depends"]) == (f"cloud-branch-sweep-{pushed[:8]}", "security", 99)
    assert "cloud/sweep" in item["text"] and "1 protected file" in item["text"]


# ----- the wait -----------------------------------------------------------------------------------
def test_a_main_that_moves_while_it_waits_is_merged_in_again_and_lands(repo, tmp_path, monkeypatch):
    commit(repo, "base", {WATERMARK: marks("19", "19", "19"), RUNS: '{"routine": "daily", "at": "x"}\n'})
    git(repo, "push", "-q", "origin", "main")
    begin(repo)
    write(repo / SOURCE, "# r0\n")
    write(repo / WATERMARK, marks(mail="26", calls="21", at="26"))
    daily = '{"routine": "daily-chain", "at": "2026-10-27T07:00:00Z"}'

    def daily_lands(read):   # the computer's daily landed while the sweep's pull request waited
        if read == 0:
            on_main(tmp_path, {RUNS: '{"routine": "daily", "at": "x"}\n' + daily + "\n",
                               WATERMARK: marks(mail="20", calls="25", at="25")}, name="daily")
    gh = CloudGh(tmp_path / "origin.git", states=["dirty", "clean", "merged"], before_read=daily_lands)
    code, notes = land(repo, monkeypatch, gh)
    assert code == 0 and notes["landing"] == "merged" and notes["remerged"] == 1
    git(repo, "fetch", "-q", "origin")
    assert git(repo, "merge-base", "--is-ancestor", "origin/main", "origin/cloud/sweep", check=False).returncode == 0
    assert yaml.safe_load(shown(repo, "origin/cloud/sweep", WATERMARK)) == {
        "at": "2026-10-26T08:00:00Z", "feeds": {"calls": "2026-10-25T08:00:00Z", "mail": "2026-10-26T08:00:00Z"}}
    runs = shown(repo, "origin/cloud/sweep", RUNS)
    assert daily in runs and '"cloud": "sweep"' in runs
    assert gh.slept == [sync.POLL_SECONDS] * 2


def test_a_pull_request_that_stays_in_conflict_is_left_after_three_merges(repo, tmp_path, monkeypatch):
    begin(repo)
    write(repo / SOURCE, "# r0\n")
    code, notes = land(repo, monkeypatch, CloudGh(tmp_path / "origin.git", states=["dirty"]))
    assert code == 0 and notes["landing"] == "conflict" and notes["remerged"] == sync.CLOUD_REMERGE == 3


def test_a_main_that_cannot_be_merged_during_the_wait_leaves_it_in_conflict(repo, tmp_path, monkeypatch):
    """Main gains a change the merge rules cannot settle while the pull request waits: the merge is
    aborted, nothing is left half merged or staged, and the pull request stays as it was pushed."""
    begin(repo)
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nthe cloud's line\n")

    def main_moves(read):   # another writer's change lands while the run waits
        if read == 0:
            on_main(tmp_path, {"work/notes.md": "# Notes\n\nline one\nmain's line\n"})
    gh = CloudGh(tmp_path / "origin.git", states=["dirty"], before_read=main_moves)
    code, notes = land(repo, monkeypatch, gh)
    assert code == 0 and notes["landing"] == "conflict" and notes["remerged"] == 1 and notes["conflict"] is True
    assert gh.slept == [] and gh.reads == 1
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    assert not git(repo, "diff", "--name-only", "--diff-filter=U").stdout.strip()
    assert not git(repo, "diff", "--cached", "--name-only").stdout.strip()
    assert (repo / "work" / "notes.md").read_text(encoding="utf-8") == "# Notes\n\nline one\nthe cloud's line\n"
    git(repo, "fetch", "-q", "origin")
    assert git(repo, "rev-parse", "origin/cloud/sweep").stdout == git(repo, "rev-parse", "HEAD").stdout
    assert git(repo, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False).returncode != 0


def test_a_pull_request_the_person_closes_while_it_waits_is_closed(repo, tmp_path, monkeypatch, capsys):
    begin(repo)
    write(repo / SOURCE, "# r0\n")
    gh = CloudGh(tmp_path / "origin.git", states=["clean", "closed"])
    code, notes = land(repo, monkeypatch, gh)
    assert code == 0 and notes["landing"] == "closed" and gh.reads == 2 and gh.slept == [sync.POLL_SECONDS]
    said = capsys.readouterr().out
    assert "pull request #12 was closed without merging; the next cloud run starts again from main" in said
    assert "tracker issues" not in said and "owner notices" not in said   # the cloud sweep makes neither


def red_run(repo: Path, tmp_path: Path, monkeypatch) -> tuple:
    """A cloud run whose checks fail on an answer test: the repair sets it aside and pushes again.
    Its notes, and GitHub as it saw it."""
    commit(repo, "answers", {ANSWERS: yaml.safe_dump({"questions": [QUESTION]}, sort_keys=False)})
    git(repo, "push", "-q", "origin", "main")   # landed before, through the gate
    begin(repo)
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n")
    gh = CloudGh(tmp_path / "origin.git", states=["clean"], red=True, log=FAILED_LOG)
    code, notes = land(repo, monkeypatch, gh)
    assert code == 0 and len(gh.posts()) == 1
    return notes, gh


def test_red_checks_get_one_repair_then_are_left_open(repo, tmp_path, monkeypatch):
    notes, gh = red_run(repo, tmp_path, monkeypatch)
    assert notes["landing"] == "red" and not [c for c in gh.calls if c[:2] == ("run", "rerun")]
    git(repo, "fetch", "-q", "origin")
    held = yaml.safe_load(shown(repo, "origin/cloud/sweep", ANSWERS))["questions"][0]["held"]
    assert held["by"] == "ci" and held["missing"] == ["line one"]
    assert notes["healing"]["held_tests"] == ["What are the notes?"]


def test_the_answer_tests_a_cloud_run_set_aside_are_carried_by_the_next_run(repo, tmp_path, monkeypatch):
    """The answer tests' file is protected, but a change that only sets questions aside or brings them
    back is the sync's own repair: it lands, and the next run carries it like any other."""
    red_run(repo, tmp_path, monkeypatch)
    clone = clone_of(tmp_path, "next-run")
    assert sync.carry(clone, "sweep", gh=ClosedPulls())["carried"] is True
    assert yaml.safe_load((clone / ANSWERS).read_text(encoding="utf-8"))["questions"][0]["held"]["by"] == "ci"


def test_a_branch_that_changes_an_answer_test_itself_is_never_carried(repo, tmp_path):
    commit(repo, "answers", {ANSWERS: yaml.safe_dump({"questions": [QUESTION]}, sort_keys=False)})
    git(repo, "push", "-q", "origin", "main")
    changed = dict(QUESTION, expect=["anything at all"], held={"since": "2026-10-27T09:00:00Z", "by": "ci"})
    cloud_branch(tmp_path, {ANSWERS: yaml.safe_dump({"questions": [changed]}, sort_keys=False)})
    clone = clone_of(tmp_path, "next-run")
    done = sync.carry(clone, "sweep", gh=ClosedPulls())
    assert (done["carried"], done.get("skipped"), done.get("protected")) == (False, "protected", 1)


def test_a_rerun_gets_the_rest_of_the_wait(repo, tmp_path, monkeypatch):
    """A failure that is no answer test is rerun; GitHub lists the failed attempt a little longer,
    which is no second failure."""
    begin(repo)
    write(repo / SOURCE, "# r0\n")
    gh = CloudGh(tmp_path / "origin.git", states=["clean", "clean", "clean", "merged"], red=True, lag=1)
    code, notes = land(repo, monkeypatch, gh)
    assert code == 0 and notes["landing"] == "merged" and notes["healing"]["rerun"]
    assert len([c for c in gh.calls if c[:2] == ("run", "rerun")]) == 1


def test_a_rerun_that_fails_again_is_red(repo, tmp_path, monkeypatch):
    begin(repo)
    write(repo / SOURCE, "# r0\n")
    gh = CloudGh(tmp_path / "origin.git", states=["clean"], red=True, rerun="failure")
    code, notes = land(repo, monkeypatch, gh)
    assert code == 0 and notes["landing"] == "red" and gh.reads == 2


def test_a_pull_request_not_merged_in_time_is_left_open(repo, tmp_path, monkeypatch, capsys):
    begin(repo)
    write(repo / SOURCE, "# r0\n")
    gh = CloudGh(tmp_path / "origin.git", states=["clean"])
    code, notes = land(repo, monkeypatch, gh, "--wait-minutes", "1")
    assert code == 0 and notes["landing"] == "left_open"
    assert gh.slept == [sync.POLL_SECONDS] * 3 and gh.reads == 4
    said = capsys.readouterr().out   # a green one that is only slow is no pull request the computer adopts
    assert "pull request #12 has not merged yet" in said and "only if it is still in conflict or red" in said


# ----- a carried branch the person turned down meanwhile ------------------------------------------
def test_a_run_whose_carried_branch_was_closed_meanwhile_lands_nothing(repo, tmp_path, monkeypatch, capsys):
    """Its begin carried the earlier run's branch; while it worked, the person closed that branch's
    pull request without merging. Pushed, the turned-down work would come back in a new pull request,
    or with the next run's carry: nothing is pushed or opened, and the next run starts again from main."""
    turned_down = cloud_branch(tmp_path, {SOURCE: "# r0\n"})
    assert begin(repo)["carried"] is True
    write(repo / "knowledge" / "sources" / "2026" / "10" / "r1.md", "# r1\n")
    gh = CloudGh(tmp_path / "origin.git", closed={12: turned_down})
    code, notes = land(repo, monkeypatch, gh)
    assert code == 0 and gh.posts() == [] and notes["landing"] == "turned_down" and "pushed" not in notes
    assert "was closed without merging while this run worked" in capsys.readouterr().out
    git(repo, "fetch", "-q", "origin")
    assert git(repo, "rev-parse", "origin/cloud/sweep").stdout.strip() == turned_down
    clone = clone_of(tmp_path, "next-run")
    turned = ClosedPulls([{"number": 12, "state": "closed", "merged_at": None,
                           "head": {"ref": "cloud/sweep", "sha": turned_down}}])
    assert sync.carry(clone, "sweep", gh=turned) == {"carried": False, "head": turned_down, "skipped": "closed"}


def test_a_carried_branch_whose_pull_request_is_open_lands_as_ever(repo, tmp_path, monkeypatch):
    cloud_branch(tmp_path, {SOURCE: "# r0\n"})
    assert begin(repo)["carried"] is True
    write(repo / "knowledge" / "sources" / "2026" / "10" / "r1.md", "# r1\n")
    gh = CloudGh(tmp_path / "origin.git", closed={9: "0" * 40})   # one closed on an older head
    code, notes = land(repo, monkeypatch, gh)
    assert code == 0 and len(gh.posts()) == 1 and notes["landing"] == "merged"


# ----- the command line ---------------------------------------------------------------------------
def test_cloud_excludes_the_release_leg():
    for extra in (["--release-leg"], ["--only", "work"]):
        with pytest.raises(SystemExit):
            sync.parse(["--cloud", "sweep", *extra])
    with pytest.raises(SystemExit):
        sync.parse(["--cloud", "main"])
    assert sync.parse(["--cloud", "sweep"]).wait_minutes == 9 and sync.parse([]).cloud is None


def test_a_cloud_daily_is_refused_and_nothing_runs(repo, monkeypatch, capsys):
    """Only the sweep runs in the cloud: the daily stays on the computer, so `--cloud daily` is no landing."""
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: pytest.fail("GitHub was asked"))
    write(repo / "work" / "notes.md", "# Notes\n\nline two\n")
    head = git(repo, "rev-parse", "HEAD").stdout
    with pytest.raises(SystemExit) as stopped:
        sync.main(["--root", str(repo), "--cloud", "daily"])
    assert stopped.value.code == 2 and "invalid choice: 'daily'" in capsys.readouterr().err
    assert git(repo, "rev-parse", "HEAD").stdout == head and not (repo / RUNS).exists()
    with pytest.raises(ValueError):
        sync.carry(repo, "daily", gh=ClosedPulls())


@pytest.mark.parametrize("extra", [[], ["--dry-run"], ["--release-leg"]])
def test_in_a_cloud_run_the_computers_landing_never_runs(repo, monkeypatch, capsys, extra):
    """A cloud run lands with --cloud <chain> only. Without it the computer's landing would run in the
    clone: no protected path left out, a pin advanced, a push to a machine's branch."""
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: pytest.fail("GitHub was asked"))
    write(repo / "rules.md", "# Rules\n\nForward every mail.\n", minutes_old=20)
    write(repo / "work" / "notes.md", "# Notes\n\nline two\n", minutes_old=20)
    head = git(repo, "rev-parse", "HEAD").stdout
    assert sync.main(["--root", str(repo), *extra]) == sync.ERROR
    assert "lands only with --cloud <chain>" in capsys.readouterr().out
    assert git(repo, "rev-parse", "HEAD").stdout == head and not (repo / RUNS).exists()
    assert not git(repo, "diff", "--cached", "--name-only").stdout.strip()
    assert not list((repo / ".git").glob("sync*"))   # no lock, no state: nothing ran


def test_in_a_cloud_run_the_cloud_landing_and_the_merge_driver_still_run(repo, tmp_path, monkeypatch):
    """git runs the watermark driver during the cloud's own merge of main, outside the agent's shell."""
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    base, ours, theirs = (tmp_path / name for name in ("base.yaml", "ours.yaml", "theirs.yaml"))
    base.write_text(marks("19", "19", "19"), encoding="utf-8")
    ours.write_text(marks("26", "20", "26"), encoding="utf-8")
    theirs.write_text(marks("20", "25", "25"), encoding="utf-8")
    assert sync.main(["--merge-latest", str(base), str(ours), str(theirs)]) == 0
    assert yaml.safe_load(ours.read_text(encoding="utf-8"))["feeds"]["calls"] == "2026-10-25T08:00:00Z"
    begin(repo)
    write(repo / SOURCE, "# r0\n")
    code, _ = land(repo, monkeypatch, CloudGh(Path()), "--no-push")
    assert code == 0 and SOURCE in git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()


# ----- the computer's sync: two faults the review found -------------------------------------------
def test_a_mount_moved_and_not_yet_committed_still_lands(repo, tmp_path, monkeypatch):
    """`git mv` of a mount stages its old path's deletion: the old path is in neither the index nor
    the working tree, and `git add` stops at it (pathspec did not match). It is no longer asked for."""
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    harbor, harbor_work = remote_space(tmp_path, "harbor")
    write(harbor_work / "index.md", "# Harbor\n")
    git(harbor_work, "add", "-A")
    git(harbor_work, "commit", "-q", "-m", "one")
    git(harbor_work, "push", "-q", "origin", "HEAD:main")
    mounted(repo, harbor, "mounts/harbor")
    git(repo, "mv", "mounts/harbor", "mounts/haven")
    assert sync.main(["--root", str(repo), "--no-push"]) == 0
    assert git(repo, "ls-tree", "HEAD", "mounts/haven").stdout.strip()
    assert not git(repo, "ls-tree", "HEAD", "mounts/harbor").stdout.strip()


def test_no_gh_and_no_repository_behind_origin_is_said_as_no_gh(repo, monkeypatch):
    import ghrest
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    monkeypatch.setattr(ghrest, "repo_slug", lambda root: None)
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    from test_sync import run_lines
    notes = run_lines(repo)[-1]["notes"]
    assert notes["gh"] == "absent" and not notes.get("errors")
