"""sync.py --cloud <chain>: a cloud run lands its own files once, on its chain's branch, through
one REST pull request, and waits for the merge (spec 4.3). Against a throwaway bare origin and
clones, as in test_sync.py; GitHub is a fake that answers the REST routes. The secret below is
assembled from parts so this file passes the scan itself."""
import datetime as dt
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest
import yaml

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


class CloudGh:
    """GitHub as a cloud run's landing sees it, through REST (`gh api`) and `gh run` only. A POST
    opens a pull request; each read of one takes the next of `states` (the last one stays):
    `merged`, or a mergeable state such as `clean` or `dirty`. A head in `red`, or every head when
    `red` is True, has a failed check whose log is `log`; each head's checks are their own run, each
    attempt of it its own job. Once a run is rerun, GitHub still lists the failed attempt for `lag`
    reads, then the new one, which ends as `rerun` says (None while it runs). `before_read(n)` runs
    before the n-th read (from 0): what happens on GitHub meanwhile. `slept` keeps the waits between
    reads."""

    def __init__(self, origin: Path, states=("merged",), red=(), log: str = "", before_read=None,
                 rerun: str | None = "success", lag: int = 0):
        self.origin, self.states, self.red, self.log, self.before_read = origin, list(states), red, log, before_read
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
            out = json.dumps([] if "state=closed" in route else [
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
            merged = state == "merged"
            out = json.dumps({"number": int(m[1]), "state": "closed" if merged else "open", "merged": merged,
                              "mergeable_state": "unknown" if merged else state,
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
    code = sync.main(["--root", str(clone), "--cloud", chain, *extra])
    return code, made[0].notes


def branch_files(clone: Path, chain: str = "sweep") -> list:
    git(clone, "fetch", "-q", "origin")
    return sorted(git(clone, "diff", "--name-only", "origin/main", f"origin/cloud/{chain}").stdout.split())


def shown(clone: Path, ref: str, rel: str) -> str:
    return git(clone, "show", f"{ref}:{rel}").stdout


def line_at(clone: Path, ref: str) -> dict:
    return json.loads(shown(clone, ref, RUNS).splitlines()[-1])


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
    assert branch_files(repo) == sorted([ITEMS, RUNS, "work/notes.md"])
    items = yaml.safe_load(shown(repo, "origin/cloud/sweep", ITEMS))["items"]
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
    write(repo / "log.md", f"# Log\n\n- 2026-10-27 yes {second} by Ada Okafor\n")
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


def test_a_proof_line_opens_its_pull_request(repo, tmp_path, monkeypatch):
    """A proof run writes only its line, and the move to the cloud reads that line on main."""
    begin(repo)
    proof = {"routine": "cloud-proof", "chain": "sweep", "guard": True, "edit": True, "tool": True,
             "at": "2026-10-27T09:00:00Z"}
    write(repo / RUNS, json.dumps(proof) + "\n")
    gh = CloudGh(tmp_path / "origin.git")
    code, notes = land(repo, monkeypatch, gh)
    assert code == 0 and len(gh.posts()) == 1 and notes["landing"] == "merged"


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
    assert branch_files(repo) == sorted([ITEMS, SOURCE, RUNS])   # their change is not used
    [item] = yaml.safe_load(shown(repo, "origin/cloud/sweep", ITEMS))["items"]
    assert (item["id"], item["kind"], item["depends"]) == (f"cloud-branch-sweep-{pushed[:8]}", "security", 99)
    assert "cloud/sweep" in item["text"] and "1 protected file" in item["text"]


# ----- the wait -----------------------------------------------------------------------------------
def test_two_chains_landing_at_once_remerge_main_and_land(repo, tmp_path, monkeypatch):
    commit(repo, "base", {WATERMARK: marks("19", "19", "19"), RUNS: '{"routine": "daily", "at": "x"}\n'})
    git(repo, "push", "-q", "origin", "main")
    begin(repo)
    write(repo / SOURCE, "# r0\n")
    write(repo / WATERMARK, marks(mail="26", calls="21", at="26"))
    daily = '{"routine": "daily-chain", "at": "2026-10-27T07:00:00Z"}'

    def daily_lands(read):   # the daily's pull request merged while the sweep's waited
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


def test_a_pull_request_not_merged_in_time_is_left_open(repo, tmp_path, monkeypatch):
    begin(repo)
    write(repo / SOURCE, "# r0\n")
    gh = CloudGh(tmp_path / "origin.git", states=["clean"])
    code, notes = land(repo, monkeypatch, gh, "--wait-minutes", "1")
    assert code == 0 and notes["landing"] == "left_open"
    assert gh.slept == [sync.POLL_SECONDS] * 3 and gh.reads == 4


# ----- the command line ---------------------------------------------------------------------------
def test_cloud_excludes_the_release_leg():
    for extra in (["--release-leg"], ["--only", "work"]):
        with pytest.raises(SystemExit):
            sync.parse(["--cloud", "sweep", *extra])
    with pytest.raises(SystemExit):
        sync.parse(["--cloud", "main"])
    assert sync.parse(["--cloud", "daily"]).wait_minutes == 9 and sync.parse([]).cloud is None


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
