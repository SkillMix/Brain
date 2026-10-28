"""ghrest: the pull request calls a cloud run allows (REST through `gh api`, never GraphQL),
against a fake `gh` that answers GitHub's REST routes as `gh api` prints them."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import ghrest


def init_repo(path: Path, origin: str | None) -> Path:
    """A git repository at `path`, with `origin` pointing at the given URL (none when None)."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    if origin is not None:
        subprocess.run(["git", "remote", "add", "origin", origin], cwd=str(path), check=True,
                       capture_output=True)
    return path


def pull(number: int, ref: str, *, fork: bool = False, gone: bool = False, auto: bool = False,
         draft: bool = False) -> dict:
    """One pull request as `GET repos/{slug}/pulls` returns it, trimmed to what ghrest reads."""
    base = {"repo": {"full_name": "ada/ada-space"}}
    head_repo = None if gone else {"full_name": "someone/ada-space" if fork else "ada/ada-space"}
    return {"number": number, "title": f"chore: sync {number}", "draft": draft,
            "html_url": f"https://github.com/ada/ada-space/pull/{number}",
            "created_at": "2026-10-27T07:10:00Z",
            "auto_merge": {"merge_method": "merge"} if auto else None,
            "head": {"ref": ref, "sha": f"sha{number}", "repo": head_repo}, "base": base}


class FakeGh:
    """GitHub as `gh api` answers it: a JSON answer per route (the first argument that is not a
    flag), HTTP 404 for any other route. Every call is kept in `calls`."""

    def __init__(self, routes: dict, code: int = 0):
        self.routes, self.code, self.calls = routes, code, []

    def __call__(self, *args):
        self.calls.append(args)
        route = next((a for a in args[1:] if a.startswith("repos/")), "")
        if self.code:
            return subprocess.CompletedProcess(["gh", *args], self.code, "", "HTTP 401: Bad credentials")
        if route not in self.routes:
            return subprocess.CompletedProcess(["gh", *args], 1, "", "gh: Not Found (HTTP 404)")
        answer = self.routes[route]
        out = answer if isinstance(answer, str) else json.dumps(answer)
        return subprocess.CompletedProcess(["gh", *args], 0, out, "")


PULLS = "repos/ada/ada-space/pulls?state=open&per_page=100"


# ----- the repository behind origin --------------------------------------------------------------
def test_the_slug_is_the_last_two_segments_of_any_origin(tmp_path):
    for url, slug in (("git@github.com:ada/ada-space.git", "ada/ada-space"),
                      ("https://github.com/ada/ada-space", "ada/ada-space"),
                      ("https://github.com/ada/ada-space.git/", "ada/ada-space"),
                      ("http://local_proxy@127.0.0.1:34567/git/ada/ada-space", "ada/ada-space")):
        repo = init_repo(tmp_path / url.replace("/", "_").replace(":", "_").replace("@", "_"), origin=url)
        assert ghrest.repo_slug(repo) == slug, url


def test_no_origin_has_no_slug(tmp_path):
    assert ghrest.repo_slug(init_repo(tmp_path / "r", origin=None)) is None
    assert ghrest.repo_slug(tmp_path / "not-a-repository") is None
    assert ghrest.repo_slug(init_repo(tmp_path / "local", origin="../origin.git")) is None
    accented = f"https://example.test/{chr(0xE4)}da/ada-space"   # a letter outside ASCII
    assert ghrest.repo_slug(init_repo(tmp_path / "accented", origin=accented)) is None


def test_an_absolute_local_origin_still_gives_its_last_two_segments(tmp_path):
    origin = tmp_path / "ada" / "ada-space.git"
    assert ghrest.repo_slug(init_repo(tmp_path / "absolute", origin=str(origin))) == "ada/ada-space"


# ----- a repository the run is not attached to ---------------------------------------------------
def test_one_pattern_knows_a_cloud_runs_answer_for_an_unattached_repository():
    said = "GitHub access to this repository is not enabled for this session (HTTP 404)"
    assert ghrest.NOT_ENABLED.search(said) and ghrest.NOT_ENABLED.search(said.upper())
    assert not ghrest.NOT_ENABLED.search("gh: Not Found (HTTP 404)")


# ----- open pull requests ------------------------------------------------------------------------
def test_open_pulls_reads_rest_and_marks_forks():
    fake = FakeGh({f"{PULLS}&head=ada:cloud/sweep": [pull(7, "cloud/sweep", auto=True),
                                                     pull(8, "cloud/sweep", fork=True)]})
    pulls = ghrest.open_pulls(fake, "ada/ada-space", head="cloud/sweep")
    assert fake.calls[0][:2] == ("api", "repos/ada/ada-space/pulls?state=open&per_page=100&head=ada:cloud/sweep")
    assert [(p["number"], p["cross_repo"], p["auto_merge"]) for p in pulls] == [(7, False, True), (8, True, False)]


def test_an_open_pull_reads_as_plain_fields():
    fake = FakeGh({PULLS: [pull(5, "sync/m-1234abcd", draft=True)]})
    [only] = ghrest.open_pulls(fake, "ada/ada-space")
    assert fake.calls[0][:2] == ("api", PULLS)
    assert only == {"number": 5, "head_ref": "sync/m-1234abcd", "head_sha": "sha5",
                    "title": "chore: sync 5", "url": "https://github.com/ada/ada-space/pull/5",
                    "draft": True, "auto_merge": False, "cross_repo": False,
                    "created_at": "2026-10-27T07:10:00Z"}


def test_a_pull_whose_head_repository_is_gone_counts_as_a_fork():
    fake = FakeGh({PULLS: [pull(9, "patch-1", gone=True)]})
    assert [p["cross_repo"] for p in ghrest.open_pulls(fake, "ada/ada-space")] == [True]


def test_no_open_pulls_is_an_empty_list():
    assert ghrest.open_pulls(FakeGh({PULLS: []}), "ada/ada-space") == []


def test_a_failed_call_is_none_not_an_empty_list():
    failing_gh = FakeGh({}, code=1)
    assert ghrest.open_pulls(failing_gh, "ada/ada-space") is None
    assert ghrest.open_pulls(lambda *a: None, "ada/ada-space") is None
    assert ghrest.open_pulls(FakeGh({PULLS: {"message": "Moved Permanently"}}), "ada/ada-space") is None
    assert ghrest.open_pulls(FakeGh({PULLS: "not json"}), "ada/ada-space") is None


# ----- opening one -------------------------------------------------------------------------------
def test_create_pull_posts_to_main_and_returns_the_number():
    fake = FakeGh({"repos/ada/ada-space/pulls": {"number": 12, "html_url": "https://github.com/ada/ada-space/pull/12"}})
    assert ghrest.create_pull(fake, "ada/ada-space", "cloud/sweep", "chore: sync", "Landed.") == 12
    assert ("-X", "POST") == fake.calls[-1][1:3] and "base=main" in fake.calls[-1]
    assert {"head=cloud/sweep", "title=chore: sync", "body=Landed."} <= set(fake.calls[-1])


def test_create_pull_sends_every_field_as_a_string():
    fake = FakeGh({"repos/ada/ada-space/pulls": {"number": 13}})
    assert ghrest.create_pull(fake, "ada/ada-space", "cloud/daily", "2026", "@notes.md") == 13
    call = fake.calls[-1]
    fields = [i for i, arg in enumerate(call) if "=" in arg]
    assert len(fields) == 4 and all(call[i - 1] == "-f" for i in fields)
    assert "-F" not in call and "--field" not in call   # -F would turn "2026" into a number, "@x" into a file


def test_a_pull_that_could_not_be_opened_is_none():
    assert ghrest.create_pull(FakeGh({}, code=1), "ada/ada-space", "cloud/sweep", "chore: sync", "x") is None
    assert ghrest.create_pull(lambda *a: None, "ada/ada-space", "cloud/sweep", "chore: sync", "x") is None
    assert ghrest.create_pull(FakeGh({"repos/ada/ada-space/pulls": {"message": "Validation Failed"}}),
                              "ada/ada-space", "cloud/sweep", "chore: sync", "x") is None


# ----- one pull request's state ------------------------------------------------------------------
def test_pull_state_says_merged_and_mergeable():
    fake = FakeGh({"repos/ada/ada-space/pulls/7": dict(pull(7, "cloud/sweep"), merged=False, state="open",
                                                       mergeable_state="dirty")})
    assert ghrest.pull_state(fake, "ada/ada-space", 7) == {"merged": False, "state": "open",
                                                          "mergeable_state": "dirty", "head_sha": "sha7"}
    assert fake.calls[0][:2] == ("api", "repos/ada/ada-space/pulls/7")


def test_a_merged_pull_keeps_githubs_unknown_and_a_failed_read_is_none():
    fake = FakeGh({"repos/ada/ada-space/pulls/7": dict(pull(7, "cloud/sweep"), merged=True, state="closed",
                                                       mergeable_state="unknown")})
    assert ghrest.pull_state(fake, "ada/ada-space", 7) == {"merged": True, "state": "closed",
                                                          "mergeable_state": "unknown", "head_sha": "sha7"}
    bare = FakeGh({"repos/ada/ada-space/pulls/7": dict(pull(7, "cloud/sweep"), merged=False, state="open")})
    assert ghrest.pull_state(bare, "ada/ada-space", 7)["mergeable_state"] is None
    assert ghrest.pull_state(FakeGh({}), "ada/ada-space", 7) is None
    assert ghrest.pull_state(lambda *a: None, "ada/ada-space", 7) is None


# ----- failed checks -----------------------------------------------------------------------------
def test_failed_checks_keep_only_failed_conclusions():
    runs = [{"name": "checks", "conclusion": "failure",
             "details_url": "https://github.com/ada/ada-space/actions/runs/123/job/456",
             "html_url": "https://github.com/ada/ada-space/runs/456"},
            {"name": "arm", "conclusion": "success", "details_url": "x", "html_url": "y"},
            {"name": "scan", "conclusion": None, "details_url": "x", "html_url": "y"}]
    fake = FakeGh({"repos/ada/ada-space/commits/abc/check-runs?per_page=100": {"total_count": 3, "check_runs": runs}})
    assert [c["name"] for c in ghrest.failed_checks(fake, "ada/ada-space", "abc")] == ["checks"]
    assert ghrest.failed_checks(fake, "ada/ada-space", "abc")[0]["link"] == \
        "https://github.com/ada/ada-space/actions/runs/123/job/456"


def test_every_failed_conclusion_counts_and_the_link_falls_back_to_the_page():
    runs = [{"name": c, "conclusion": c, "details_url": None, "html_url": f"https://github.com/r/{c}"}
            for c in ghrest.FAILED + ("neutral", "skipped", "success")]
    fake = FakeGh({"repos/ada/ada-space/commits/abc/check-runs?per_page=100": {"check_runs": runs}})
    found = ghrest.failed_checks(fake, "ada/ada-space", "abc")
    assert [c["name"] for c in found] == list(ghrest.FAILED)
    assert found[0] == {"name": "failure", "link": "https://github.com/r/failure"}


def test_checks_that_cannot_be_read_are_none():
    assert ghrest.failed_checks(FakeGh({}, code=1), "ada/ada-space", "abc") is None
    assert ghrest.failed_checks(lambda *a: None, "ada/ada-space", "abc") is None
    assert ghrest.failed_checks(FakeGh({"repos/ada/ada-space/commits/abc/check-runs?per_page=100": []}),
                                "ada/ada-space", "abc") is None


# ----- the real gh -------------------------------------------------------------------------------
def test_the_runner_is_none_without_gh_and_a_failed_call_on_a_timeout(tmp_path, monkeypatch):
    seen = {}

    def missing(argv, **kw):   # POSIX names the program it could not find
        raise FileNotFoundError(2, "No such file or directory", "gh")
    monkeypatch.setattr(ghrest.subprocess, "run", missing)
    assert ghrest.runner(tmp_path)("api", "user") is None

    def missing_on_windows(argv, **kw):   # Windows names no file
        raise FileNotFoundError(2, "The system cannot find the file specified")
    monkeypatch.setattr(ghrest.subprocess, "run", missing_on_windows)
    assert ghrest.runner(tmp_path)("api", "user") is None

    def slow(argv, **kw):
        seen.update(argv=argv, **kw)
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
    monkeypatch.setattr(ghrest.subprocess, "run", slow)
    done = ghrest.runner(tmp_path)("api", "user")
    assert done is not None and done.returncode != 0 and "timed out" in done.stderr
    assert seen["argv"] == ["gh", "api", "user"] and seen["cwd"] == str(tmp_path)
    assert (seen["timeout"], seen["encoding"], seen["errors"]) == (60, "utf-8", "replace")


def test_a_gone_working_folder_is_a_failed_call_not_a_missing_gh(tmp_path, monkeypatch):
    gone = tmp_path / "gone"

    def no_folder(argv, **kw):   # POSIX names the folder when chdir fails; Windows raises NotADirectoryError
        raise FileNotFoundError(2, "No such file or directory", str(gone))
    monkeypatch.setattr(ghrest.subprocess, "run", no_folder)
    done = ghrest.runner(gone)("api", "user")
    assert done is not None and done.returncode != 0 and "could not run" in done.stderr

    def windows_folder(argv, **kw):
        raise NotADirectoryError(20, "The directory name is invalid")
    monkeypatch.setattr(ghrest.subprocess, "run", windows_folder)
    done = ghrest.runner(gone)("api", "user")
    assert done is not None and done.returncode != 0 and "could not run" in done.stderr
