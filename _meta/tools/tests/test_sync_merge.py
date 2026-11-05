"""sync.py: merges that never wedge (spec 4.5a) and the carry of a cloud run's unmerged branch
(spec 4.2), against a throwaway bare origin and clones, as in test_sync.py. A merge here is the
sync's own `merge_in`; the platform's merge of a pull request is a merge commit pushed to main."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import sync
from test_sync import git, platform_merges, repo, write  # noqa: F401  (the repo fixture)

WATERMARK = "_meta/state/sweep-watermark.yaml"
SOURCE = "knowledge/sources/2026/10/r0.md"


def configured(clone: Path) -> Path:
    for key, value in (("user.name", "t"), ("user.email", "t@example.test"), ("commit.gpgsign", "false")):
        git(clone, "config", key, value)
    return clone


def clone_of(tmp_path: Path, name: str, *options: str, url: str | None = None) -> Path:
    """Another checkout of the space's origin: a second computer, or a cloud run's fresh clone."""
    clone = tmp_path / name
    git(tmp_path, "clone", "-q", *options, url or str(tmp_path / "origin.git"), str(clone))
    return configured(clone)


def commit(clone: Path, message: str, files: dict) -> str:
    """Each file written (None deletes it) and committed; the new commit."""
    for rel, text in files.items():
        if text is None:
            (clone / rel).unlink()
        else:
            write(clone / rel, text)
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", message)
    return git(clone, "rev-parse", "HEAD").stdout.strip()


def on_main(tmp_path: Path, files: dict, name: str = "other") -> str:
    """Another writer's change lands on origin's main."""
    other = tmp_path / name
    if not other.exists():
        clone_of(tmp_path, name)
    git(other, "pull", "-q", "--no-rebase", "origin", "main")
    done = commit(other, "elsewhere", files)
    git(other, "push", "-q", "origin", "HEAD:main")
    return done


def marks(mail: str, calls: str, at: str) -> str:
    """A sweep watermark as sweep.py writes it: the run's mark and one mark per feed."""
    return yaml.safe_dump({"at": f"2026-10-{at}T08:00:00Z",
                           "feeds": {"calls": f"2026-10-{calls}T08:00:00Z", "mail": f"2026-10-{mail}T08:00:00Z"}},
                          sort_keys=False)


def merge_in(clone: Path) -> dict:
    run = sync.Run(clone, clone / ".git", sync.parse(["--root", str(clone)]))
    run.merge_in()
    return run.notes


def merged_cleanly(clone: Path) -> bool:
    return (git(clone, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False).returncode == 0
            and not (clone / ".git" / "MERGE_HEAD").exists()
            and not git(clone, "diff", "--name-only", "--diff-filter=U").stdout.strip()
            and not git(clone, "status", "--porcelain").stdout.strip())


# ----- the feed export never leaves the computer ---------------------------------------------------
def test_the_feed_is_a_private_zone(repo):
    assert "_meta/state/feed/" in sync.PRIVATE_ZONES
    write(repo / "_meta" / "state" / "feed" / "mail.jsonl", '{"id": "a"}\n', minutes_old=20)
    write(repo / "work" / "notes.md", "# Notes\n\nline two\n", minutes_old=20)
    assert sync.main(["--root", str(repo), "--no-push"]) == 0
    tracked = git(repo, "ls-files").stdout.split()
    assert "work/notes.md" in tracked and not any(p.startswith("_meta/state/feed/") for p in tracked)


# ----- the merge rules -----------------------------------------------------------------------------
def test_watermarks_merge_to_the_later_mark_key_by_key(repo, tmp_path):
    commit(repo, "base", {WATERMARK: marks("19", "19", "19")})
    git(repo, "push", "-q", "origin", "main")
    on_main(tmp_path, {WATERMARK: marks(mail="20", calls="25", at="25")})
    commit(repo, "here", {WATERMARK: marks(mail="26", calls="21", at="26")})
    notes = merge_in(repo)
    assert not notes.get("conflict"), notes
    assert merged_cleanly(repo)
    assert yaml.safe_load((repo / WATERMARK).read_text(encoding="utf-8")) == {
        "at": "2026-10-26T08:00:00Z", "feeds": {"calls": "2026-10-25T08:00:00Z", "mail": "2026-10-26T08:00:00Z"}}


def test_generated_files_keep_this_checkouts_side(repo, tmp_path):
    commit(repo, "base", {"live/now.md": "# Now\n\nbase\n", "_meta/state/stale.yaml": "x: base\n"})
    git(repo, "push", "-q", "origin", "main")
    on_main(tmp_path, {"live/now.md": "# Now\n\ntheirs\n", "_meta/state/stale.yaml": "x: theirs\n",
                       "live/subjects/harbor.md": "# Harbor\n\ntheirs\n", "_meta/state/ids.yaml": "harbor: theirs\n"})
    ours = {"live/now.md": "# Now\n\nours\n", "_meta/state/stale.yaml": "x: ours\n",
            "live/subjects/harbor.md": "# Harbor\n\nours\n", "_meta/state/ids.yaml": "harbor: ours\n"}
    commit(repo, "here", ours)   # two changed on both sides, two added on both sides
    notes = merge_in(repo)
    assert not notes.get("conflict"), notes
    assert merged_cleanly(repo)
    assert {rel: (repo / rel).read_text(encoding="utf-8") for rel in ours} == ours


def test_a_generated_page_changed_here_and_deleted_there_keeps_ours(repo, tmp_path):
    commit(repo, "base", {"live/now.md": "# Now\n\nbase\n"})
    git(repo, "push", "-q", "origin", "main")
    on_main(tmp_path, {"live/now.md": None})
    commit(repo, "here", {"live/now.md": "# Now\n\nours\n"})
    notes = merge_in(repo)   # git runs no driver where one side deleted the file: resolve settles it
    assert not notes.get("conflict") and notes.get("resolved") == 1, notes
    assert merged_cleanly(repo) and (repo / "live" / "now.md").read_text(encoding="utf-8") == "# Now\n\nours\n"


def test_a_generated_page_deleted_here_and_changed_there_stays_deleted(repo, tmp_path):
    commit(repo, "base", {"live/now.md": "# Now\n\nbase\n"})
    git(repo, "push", "-q", "origin", "main")
    on_main(tmp_path, {"live/now.md": "# Now\n\ntheirs\n"})
    commit(repo, "here", {"live/now.md": None})
    notes = merge_in(repo)
    assert not notes.get("conflict") and notes.get("resolved") == 1, notes
    assert merged_cleanly(repo) and not (repo / "live" / "now.md").exists()


def test_an_answered_item_deleted_here_and_changed_there_stays_deleted(repo, tmp_path):
    item = "_inbound/needs-you/sync.yaml"
    commit(repo, "base", {item: "items:\n- id: a\n"})
    git(repo, "push", "-q", "origin", "main")
    on_main(tmp_path, {item: "items:\n- id: a\n  text: changed there\n"})
    commit(repo, "the person answered it", {item: None})
    notes = merge_in(repo)
    assert not notes.get("conflict") and notes.get("resolved") == 1, notes
    assert merged_cleanly(repo) and not (repo / item).exists()


def test_a_shipped_queue_item_deleted_there_and_restamped_here_stays_deleted(repo, tmp_path):
    item = "_outbound/queue/q-0001.md"
    commit(repo, "base", {item: "---\nstage_after: 2026-10-20T08:00:00Z\n---\nA note.\n"})
    git(repo, "push", "-q", "origin", "main")
    on_main(tmp_path, {item: None})   # the release shipped it
    commit(repo, "restamped", {item: "---\nstage_after: 2026-10-21T08:00:00Z\n---\nA note.\n"})
    notes = merge_in(repo)
    assert not notes.get("conflict") and notes.get("resolved") == 1, notes
    assert merged_cleanly(repo) and not (repo / item).exists()


def test_any_other_conflict_still_stops_the_merge(repo, tmp_path):
    item = "_inbound/needs-you/sync.yaml"
    commit(repo, "base", {"knowledge/x.md": "# X\n\nbase\n", item: "items: []\n"})
    git(repo, "push", "-q", "origin", "main")
    on_main(tmp_path, {"knowledge/x.md": "# X\n\ntheirs\n", item: "items:\n- id: b\n"})
    before = commit(repo, "here", {"knowledge/x.md": "# X\n\nours\n", item: None})
    notes = merge_in(repo)
    assert notes.get("conflict") is True and "resolved" not in notes
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == before
    assert (repo / "knowledge" / "x.md").read_text(encoding="utf-8") == "# X\n\nours\n"
    assert not (repo / item).exists() and not git(repo, "status", "--porcelain").stdout.strip()


def test_the_merge_drivers_are_written_into_the_spaces_own_git_once(repo, monkeypatch):
    run = sync.Run(repo, repo / ".git", sync.parse(["--root", str(repo)]))
    run.merge_rules()

    def config(key):
        return git(repo, "config", "--get", key, check=False).stdout.strip()
    assert config("merge.spaces-ours.driver") == "true"
    latest = config("merge.spaces-latest.driver")
    # no -I (nor the -s it implies): a PyYAML installed with `pip install --user` imports as it does for the sync
    assert latest == f'"{Path(sys.executable).as_posix()}" "{Path(sync.__file__).resolve().as_posix()}" ' \
                     "--merge-latest %O %A %B"
    assert not {"-I", "-s"} & set(latest.split())
    attributes = (repo / ".git" / "info" / "attributes").read_text(encoding="utf-8").splitlines()
    assert all(rule in attributes for rule in sync.MERGE_RULES)
    seen, real = [], sync.git
    monkeypatch.setattr(sync, "git", lambda root, *a, **k: (seen.append(a), real(root, *a, **k))[1])
    run.merge_rules()
    assert not [a for a in seen if a[0] == "config" and a[1].startswith("merge.")]   # nothing to change
    git(repo, "config", "merge.spaces-ours.driver", "false")
    run.merge_rules()
    assert config("merge.spaces-ours.driver") == "true" and config("merge.spaces-latest.driver") == latest
    assert attributes == (repo / ".git" / "info" / "attributes").read_text(encoding="utf-8").splitlines()


# ----- the watermark driver ------------------------------------------------------------------------
def sides(tmp_path: Path, ours, theirs) -> tuple:
    """The three files git hands a merge driver: base, ours and theirs (a dict is written as YAML)."""
    paths = []
    for name, content in (("base", "at: '2026-10-19T08:00:00Z'\n"), ("ours", ours), ("theirs", theirs)):
        if isinstance(content, dict):
            content = yaml.safe_dump(content, sort_keys=False)
        path = tmp_path / f".merge_file_{name}"
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        paths.append(path)
    return tuple(paths)


def test_merge_latest_takes_the_later_moment_key_by_key_and_keeps_every_key(tmp_path):
    base, ours, theirs = sides(
        tmp_path,
        {"at": "2026-10-26T08:00:00Z", "feeds": {"mail": "2026-10-26T08:00:00Z", "calls": "2026-10-21T08:00:00Z"},
         "note": "ours", "count": 3},
        {"at": "2026-10-25T08:00:00+02:00", "feeds": {"calls": "2026-10-25T08:00:00Z", "calendar": "2026-10-24T08:00:00Z"},
         "note": "theirs", "count": 20261027, "review_at": "2026-10-27T08:00:00Z"})
    assert sync.merge_latest(base, ours, theirs) == 0
    assert yaml.safe_load(ours.read_text(encoding="utf-8")) == {
        "at": "2026-10-26T08:00:00Z",
        "feeds": {"mail": "2026-10-26T08:00:00Z", "calls": "2026-10-25T08:00:00Z", "calendar": "2026-10-24T08:00:00Z"},
        "note": "ours", "count": 3, "review_at": "2026-10-27T08:00:00Z"}


def test_merge_latest_keeps_ours_when_a_side_is_not_yaml(tmp_path):
    kept = "at: '2026-10-26T08:00:00Z'\n"
    for junk in ("feeds: [unclosed\n", b"at: \xff\xfe\n"):
        base, ours, theirs = sides(tmp_path, kept, junk)
        assert sync.merge_latest(base, ours, theirs) == 1
        assert ours.read_text(encoding="utf-8") == kept
        base, ours, theirs = sides(tmp_path, junk, kept)
        assert sync.merge_latest(base, ours, theirs) == 1
        assert ours.read_bytes() == (junk if isinstance(junk, bytes) else junk.encode("utf-8"))


def test_ours_is_left_as_it_is_when_theirs_adds_nothing(tmp_path):
    text = "# the sweep's mark\nat: '2026-10-26T08:00:00Z'\n"
    base, ours, theirs = sides(tmp_path, text, "at: '2026-10-20T08:00:00Z'\n")
    assert sync.merge_latest(base, ours, theirs) == 0
    assert ours.read_text(encoding="utf-8") == text   # comments and layout kept: nothing was rewritten


def test_git_runs_the_driver_as_a_command(tmp_path):
    base, ours, theirs = sides(tmp_path, {"at": "2026-10-20T08:00:00Z"}, {"at": "2026-10-26T08:00:00Z"})
    done = subprocess.run([sys.executable, str(Path(sync.__file__).resolve()), "--merge-latest",
                           str(base), str(ours), str(theirs)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert yaml.safe_load(ours.read_text(encoding="utf-8")) == {"at": "2026-10-26T08:00:00Z"}


# ----- the carry -----------------------------------------------------------------------------------
class ClosedPulls:
    """GitHub as the carry asks it: the closed pull requests from one branch, or a failed call."""

    def __init__(self, closed=()):
        self.closed, self.calls = list(closed), []

    def __call__(self, *args):
        self.calls.append(args)
        route = next((a for a in args if a.startswith("repos/")), "")
        if "state=closed" in route:
            return subprocess.CompletedProcess(["gh", *args], 0, json.dumps(self.closed), "")
        return subprocess.CompletedProcess(["gh", *args], 1, "", "gh: Not Found (HTTP 404)")


def cloud_branch(tmp_path: Path, files: dict, chain: str = "sweep") -> str:
    """An earlier cloud run pushed `cloud/<chain>` from main and nobody merged it: its head."""
    work = clone_of(tmp_path, f"earlier-{chain}")
    git(work, "checkout", "-q", "-b", f"cloud/{chain}")
    head = commit(work, "chore: sync", files)
    git(work, "push", "-q", "origin", f"HEAD:refs/heads/cloud/{chain}")
    return head


def lease(clone: Path, chain: str = "sweep") -> str:
    return (clone / ".git" / f"sync-cloud-lease-{chain}").read_text(encoding="utf-8").strip()


def test_carry_brings_the_last_runs_unmerged_branch_in_before_anything_is_written(repo, tmp_path):
    commit(repo, "base", {WATERMARK: marks("19", "19", "19")})
    git(repo, "push", "-q", "origin", "main")
    head = cloud_branch(tmp_path, {SOURCE: "# r0\n", WATERMARK: marks(mail="26", calls="25", at="26")})
    on_main(tmp_path, {"work/elsewhere.md": "a computer's note\n", WATERMARK: marks(mail="20", calls="27", at="27")})
    clone = clone_of(tmp_path, "cloud-run")   # the next cloud run's fresh clone, which never ran the sync
    gh = ClosedPulls()
    assert sync.carry(clone, "sweep", gh=gh) == {"carried": True, "head": head}
    assert (clone / SOURCE).exists() and (clone / "work" / "elsewhere.md").exists()
    assert yaml.safe_load((clone / WATERMARK).read_text(encoding="utf-8")) == {
        "at": "2026-10-27T08:00:00Z", "feeds": {"calls": "2026-10-27T08:00:00Z", "mail": "2026-10-26T08:00:00Z"}}
    assert merged_cleanly(clone)
    assert git(clone, "merge-base", "--is-ancestor", head, "HEAD", check=False).returncode == 0
    assert lease(clone) == head
    assert gh.calls and "state=closed" in gh.calls[0][1] and "cloud/sweep" in gh.calls[0][1]


def test_a_shallow_clone_deepens_to_carry_an_old_branch(repo, tmp_path, monkeypatch):
    head = cloud_branch(tmp_path, {SOURCE: "# r0\n"})
    for n in range(30):
        on_main(tmp_path, {f"work/n{n}.md": f"{n}\n"})
    clone = clone_of(tmp_path, "shallow", "--depth", "1", "--single-branch", url=(tmp_path / "origin.git").as_uri())
    assert git(clone, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    seen, real = [], sync.git
    monkeypatch.setattr(sync, "git", lambda root, *a, **k: (seen.append(a), real(root, *a, **k))[1])
    assert sync.carry(clone, "sweep", gh=ClosedPulls()) == {"carried": True, "head": head}
    assert (clone / SOURCE).exists() and (clone / "work" / "n29.md").exists()
    assert any(arg.startswith("--deepen") for args in seen for arg in args)
    assert not any("--allow-unrelated-histories" in args for args in seen)
    assert git(clone, "merge-base", "--is-ancestor", head, "HEAD", check=False).returncode == 0


def test_a_branch_with_no_history_in_common_is_never_merged(repo, tmp_path):
    stray = clone_of(tmp_path, "stray")
    git(stray, "checkout", "-q", "--orphan", "cloud/sweep")
    git(stray, "rm", "-rqf", ".")
    head = commit(stray, "unrelated", {"stray.md": "nothing in common\n"})
    git(stray, "push", "-q", "origin", "HEAD:refs/heads/cloud/sweep")
    clone = clone_of(tmp_path, "cloud-run")
    before = git(clone, "rev-parse", "HEAD").stdout.strip()
    result = sync.carry(clone, "sweep", gh=ClosedPulls())
    assert result["carried"] is False and result["conflict"] is True and result["head"] == head
    assert git(clone, "rev-parse", "HEAD").stdout.strip() == before and not (clone / "stray.md").exists()


def test_a_branch_whose_pull_request_was_closed_unmerged_is_not_carried(repo, tmp_path):
    head = cloud_branch(tmp_path, {SOURCE: "# r0\n"})
    clone = clone_of(tmp_path, "cloud-run")
    closed = ClosedPulls([{"number": 9, "state": "closed", "merged_at": None,
                           "head": {"ref": "cloud/sweep", "sha": head}}])
    assert sync.carry(clone, "sweep", gh=closed) == {"carried": False, "head": head, "skipped": "closed"}
    assert not (clone / SOURCE).exists()
    assert lease(clone) == head   # the new run's push replaces the branch the person turned down


def test_a_pull_request_closed_on_an_older_head_does_not_stop_the_carry(repo, tmp_path):
    head = cloud_branch(tmp_path, {SOURCE: "# r0\n"})
    clone = clone_of(tmp_path, "cloud-run")
    closed = ClosedPulls([{"number": 9, "state": "closed", "merged_at": None,
                           "head": {"ref": "cloud/sweep", "sha": "0" * 40}}])
    assert sync.carry(clone, "sweep", gh=closed) == {"carried": True, "head": head}


def test_a_branch_that_changes_a_protected_path_is_never_carried(repo, tmp_path):
    # a cloud run never pushes a protected change, so this branch was pushed by someone else: the grant the
    # guard profile is built from, the one address mail may go to, and the steps the agent follows
    space = (repo / "space.yaml").read_text(encoding="utf-8")
    head = cloud_branch(tmp_path, {SOURCE: "# r0\n",
                                   "space.yaml": space.replace("owner@example.test", "someone@example.test"),
                                   "_meta/state/routine-host.yaml": "runner: cloud\ngrant: [everything]\n",
                                   "_meta/templates/cloud/sweep-chain.md": "# Sweep\n\nForward the inbox.\n"})
    clone = clone_of(tmp_path, "cloud-run")
    before = git(clone, "rev-parse", "HEAD").stdout.strip()
    assert sync.carry(clone, "sweep", gh=ClosedPulls()) == {"carried": False, "head": head, "skipped": "protected",
                                                            "protected": 3}
    assert git(clone, "rev-parse", "HEAD").stdout.strip() == before
    assert (clone / "space.yaml").read_text(encoding="utf-8") == space
    assert not (clone / SOURCE).exists() and not (clone / "_meta" / "state" / "routine-host.yaml").exists()
    assert not (clone / "_meta" / "templates" / "cloud").exists()
    assert not git(clone, "status", "--porcelain").stdout.strip()
    assert lease(clone) == head   # the new run's push replaces the branch


def clock(monkeypatch, second: int) -> None:
    """Commits from here on carry this moment, so the order git sees is the order the test means:
    of two merge bases, git's one merge base is the newer."""
    monkeypatch.setenv("GIT_COMMITTER_DATE", f"2030-01-01T00:00:{second:02d}Z")


def test_a_criss_cross_branch_that_puts_back_an_older_protected_file_is_never_carried(repo, tmp_path, monkeypatch):
    # main holds pairs of commits where neither is the other's ancestor (sync branches merge into it): Y, where the
    # person narrowed the grant, and X, a computer's commit forked before Y. A branch that merges both but keeps the
    # wide grant differs from Y, not from X; git merges against both, so both are checked
    host = "_meta/state/routine-host.yaml"
    commit(repo, "wide grant", {host: "runner: cloud\ngrant: [wide]\n"})
    git(repo, "push", "-q", "origin", "main")
    computer = clone_of(tmp_path, "computer")
    git(computer, "checkout", "-q", "-b", "sync/abc")
    clock(monkeypatch, 1)
    y = on_main(tmp_path, {host: "runner: cloud\ngrant: [narrow]\n"})
    clock(monkeypatch, 2)
    x = commit(computer, "chore: sync", {"work/a.md": "a\n"})
    git(computer, "push", "-q", "origin", "HEAD:refs/heads/sync/abc")
    clock(monkeypatch, 3)
    platform_merges(tmp_path, "sync/abc")
    pushed = clone_of(tmp_path, "pushed")
    git(pushed, "checkout", "-q", "-b", "cloud/sweep", x)
    git(pushed, "merge", "-q", "--no-edit", "-s", "ours", y)   # X's tree: the wide grant
    head = commit(pushed, "chore: sync", {SOURCE: "# r0\n"})
    git(pushed, "push", "-q", "origin", "HEAD:refs/heads/cloud/sweep")
    clone = clone_of(tmp_path, "cloud-run")
    before = git(clone, "rev-parse", "HEAD").stdout.strip()
    assert sync.carry(clone, "sweep", gh=ClosedPulls()) == {"carried": False, "head": head, "skipped": "protected",
                                                            "protected": 1}
    assert git(clone, "rev-parse", "HEAD").stdout.strip() == before
    assert (clone / host).read_text(encoding="utf-8") == "runner: cloud\ngrant: [narrow]\n"
    assert not (clone / SOURCE).exists() and not git(clone, "status", "--porcelain").stdout.strip()


def test_a_criss_cross_after_a_race_is_carried(repo, tmp_path, monkeypatch):
    # run N carried the earlier head B1 into a clone where main had changed a tool, and pushed B2; meanwhile the
    # platform merged B1. Main's tool change is in B2 as main holds it, so nothing protected moves
    earlier = clone_of(tmp_path, "earlier")
    git(earlier, "checkout", "-q", "-b", "cloud/sweep")
    clock(monkeypatch, 1)
    on_main(tmp_path, {"_meta/tools/extra.py": "x = 1\n"})
    clock(monkeypatch, 2)
    b1 = commit(earlier, "chore: sync", {SOURCE: "# r0\n"})
    git(earlier, "push", "-q", "origin", "HEAD:refs/heads/cloud/sweep")
    run_n = clone_of(tmp_path, "run-n")
    assert sync.carry(run_n, "sweep", gh=ClosedPulls()) == {"carried": True, "head": b1}
    clock(monkeypatch, 3)
    b2 = commit(run_n, "chore: sync", {"knowledge/sources/2026/10/r1.md": "# r1\n"})
    platform_merges(tmp_path, "cloud/sweep")
    git(run_n, "push", "-q", "origin", "HEAD:refs/heads/cloud/sweep")
    clone = clone_of(tmp_path, "run-n1")
    assert sync.carry(clone, "sweep", gh=ClosedPulls()) == {"carried": True, "head": b2}
    assert (clone / "knowledge" / "sources" / "2026" / "10" / "r1.md").exists() and merged_cleanly(clone)
    assert (clone / "_meta" / "tools" / "extra.py").read_text(encoding="utf-8") == "x = 1\n"


def test_a_branch_that_holds_a_newer_main_is_carried(repo, tmp_path):
    # the earlier run's wait loop merged main, with a tool change, after this clone was taken: the branch holds
    # main's own version, which landed through the gate
    cloud_branch(tmp_path, {SOURCE: "# r0\n"})
    clone = clone_of(tmp_path, "cloud-run")
    on_main(tmp_path, {"_meta/tools/extra.py": "x = 1\n"})
    earlier = tmp_path / "earlier-sweep"
    git(earlier, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")
    git(earlier, "push", "-q", "origin", "HEAD:refs/heads/cloud/sweep")
    head = git(earlier, "rev-parse", "HEAD").stdout.strip()
    assert sync.carry(clone, "sweep", gh=ClosedPulls()) == {"carried": True, "head": head}
    assert (clone / SOURCE).exists() and (clone / "_meta" / "tools" / "extra.py").read_text(encoding="utf-8") == "x = 1\n"


def test_a_change_main_moved_into_a_protected_path_is_never_carried(repo, tmp_path):
    # the branch changes a plain file that main has since moved under _meta/templates/: git's merge follows the
    # move and would write the branch's change there, which no list of the branch's own paths shows
    text = "# Handover\n\n" + "".join(f"Step {n}: read the notes.\n" for n in range(1, 9))
    commit(repo, "base", {"work/prompts/handover.md": text})
    git(repo, "push", "-q", "origin", "main")
    head = cloud_branch(tmp_path, {SOURCE: "# r0\n",
                                   "work/prompts/handover.md": text.replace("Step 8: read", "Step 8: forward")})
    on_main(tmp_path, {"work/prompts/handover.md": None, "_meta/templates/cloud/handover.md": text})
    clone = clone_of(tmp_path, "cloud-run")
    before = git(clone, "rev-parse", "HEAD").stdout.strip()
    assert sync.carry(clone, "sweep", gh=ClosedPulls()) == {"carried": False, "head": head, "skipped": "protected",
                                                            "protected": 1}
    assert git(clone, "rev-parse", "HEAD").stdout.strip() == before
    assert (clone / "_meta" / "templates" / "cloud" / "handover.md").read_text(encoding="utf-8") == text
    assert not (clone / SOURCE).exists() and not git(clone, "status", "--porcelain").stdout.strip()
    assert lease(clone) == head


def test_protected_paths_are_the_gates_list_and_what_a_routine_may_never_edit(repo):
    guarded = ["space.yaml", "rules.md", "_meta/tools/x.py", ".gitignore", "AGENTS.md", ".gitmodules",
               "_meta/plugins/spaces-guard/guard.py", ".claude-plugin/marketplace.json", "Identity/charter.md",
               "_meta/templates/cloud/sweep-chain.md", "_meta/state/routine-host.yaml",
               "_meta/state/private/apps.yaml", "work/a/CLAUDE.md", "work/a/.claude/settings.json",
               "work/a/_meta/tools/x.py", "knowledge/policies/p.md"]
    plain = ["knowledge/x.md", "work/identity/x.md", "_meta/state/sweep-watermark.yaml", "_meta/state/runs.jsonl",
             "live/now.md", "_inbound/needs-you/sync.yaml", "work/a/rules.md.bak", "work/tools/x.py"]
    data = yaml.safe_load((repo / "space.yaml").read_text(encoding="utf-8"))
    data["protected"] = ["knowledge/policies/"]   # the space's own list: the minimum still stands
    (repo / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert sync.protected_paths(repo, plain + guarded) == guarded


def test_a_clone_whose_space_cannot_be_read_is_said_not_raised(repo, tmp_path):
    head = cloud_branch(tmp_path, {SOURCE: "# r0\n"})
    clone = clone_of(tmp_path, "cloud-run")
    (clone / "space.yaml").unlink()
    result = sync.carry(clone, "sweep", gh=ClosedPulls())
    assert result["carried"] is False and result["head"] == head and "space.yaml" in result["error"]
    assert not (clone / SOURCE).exists() and lease(clone) == head


def test_what_kept_a_carry_from_settling_is_said(repo, tmp_path, monkeypatch):
    commit(repo, "base", {"knowledge/x.md": "# X\n\nbase\n"})
    git(repo, "push", "-q", "origin", "main")
    head = cloud_branch(tmp_path, {"knowledge/x.md": "# X\n\nthe cloud's\n"})
    on_main(tmp_path, {"knowledge/x.md": "# X\n\nmain's\n"})
    clone = clone_of(tmp_path, "cloud-run")

    def broken(self):
        raise RuntimeError("no")
    monkeypatch.setattr(sync.Run, "resolve", broken)
    assert sync.carry(clone, "sweep", gh=ClosedPulls()) == {"carried": False, "head": head, "conflict": True,
                                                            "undone": True, "error": "merge not settled: RuntimeError"}
    assert not (clone / ".git" / "MERGE_HEAD").exists()


def test_a_carry_that_conflicts_is_aborted_and_said(repo, tmp_path):
    commit(repo, "base", {"knowledge/x.md": "# X\n\nbase\n"})
    git(repo, "push", "-q", "origin", "main")
    head = cloud_branch(tmp_path, {"knowledge/x.md": "# X\n\nthe cloud's\n"})
    on_main(tmp_path, {"knowledge/x.md": "# X\n\nmain's\n"})
    clone = clone_of(tmp_path, "cloud-run")
    before = git(clone, "rev-parse", "HEAD").stdout.strip()
    assert sync.carry(clone, "sweep", gh=ClosedPulls()) == {"carried": False, "head": head, "conflict": True,
                                                            "undone": True}
    assert not (clone / ".git" / "MERGE_HEAD").exists()
    assert git(clone, "rev-parse", "HEAD").stdout.strip() == before
    assert (clone / "knowledge" / "x.md").read_text(encoding="utf-8") == "# X\n\nmain's\n"
    assert lease(clone) == head


def test_candidate_pages_both_sides_rewrote_conflict_and_leave_the_clone_as_main(repo, tmp_path):
    """What wedged a cloud sweep for days: main and the cloud's unmerged run both refreshed the same candidate
    page, which no merge rule settles. The carry aborts and says the clone is main's again (`undone`), so the run
    can start fresh on it, and leases its push to the old head, which that push then replaces."""
    page = "_inbound/candidates/harbor.md"
    commit(repo, "base", {page: "---\nid: p-1\nevidence: [a]\n---\n# Harbor\n"})
    git(repo, "push", "-q", "origin", "main")
    head = cloud_branch(tmp_path, {page: "---\nid: p-1\nevidence: [a, mail-b]\n---\n# Harbor\n"})
    on_main(tmp_path, {page: "---\nid: p-1\nevidence: [a, journal-c]\n---\n# Harbor\n"})
    clone = clone_of(tmp_path, "cloud-run")
    before = git(clone, "rev-parse", "HEAD").stdout.strip()
    result = sync.carry(clone, "sweep", gh=ClosedPulls())
    assert result["conflict"] is True and result["undone"] is True and result["head"] == head
    assert git(clone, "rev-parse", "HEAD").stdout.strip() == before and merged_cleanly(clone)
    assert "journal-c" in (clone / page).read_text(encoding="utf-8")
    assert lease(clone) == head


def test_a_carry_whose_abort_leaves_the_clone_changed_is_no_fresh_start(repo, tmp_path, monkeypatch):
    """A clone the abort did not take back to main's is not safe to work on: no `undone`, and the run stops."""
    commit(repo, "base", {"knowledge/x.md": "# X\n\nbase\n"})
    git(repo, "push", "-q", "origin", "main")
    cloud_branch(tmp_path, {"knowledge/x.md": "# X\n\nthe cloud's\n"})
    on_main(tmp_path, {"knowledge/x.md": "# X\n\nmain's\n"})
    clone = clone_of(tmp_path, "cloud-run")
    real = sync.git

    def abort_fails(root, *args, **kw):
        if args[:2] == ("merge", "--abort"):
            return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: could not abort")
        return real(root, *args, **kw)
    monkeypatch.setattr(sync, "git", abort_fails)
    result = sync.carry(clone, "sweep", gh=ClosedPulls())
    assert result["conflict"] is True and "undone" not in result
    assert "could not be taken back" in result["error"]


def test_a_file_the_last_cloud_run_held_is_read_from_its_branch(repo, tmp_path):
    head = cloud_branch(tmp_path, {"_meta/state/cloud-fresh.yaml": "sweep: {runs: 2}\n"})
    clone = clone_of(tmp_path, "cloud-run")
    assert sync.carry(clone, "sweep", gh=ClosedPulls())["head"] == head
    assert sync.held_text(clone, "sweep", "_meta/state/cloud-fresh.yaml") == "sweep: {runs: 2}\n"
    assert sync.held_text(clone, "sweep", "_meta/state/nothing.yaml") is None


def test_no_branch_to_carry_is_quiet_and_leases_on_absence(repo, tmp_path, capsys):
    clone = clone_of(tmp_path, "cloud-run")
    capsys.readouterr()
    gh = ClosedPulls()
    assert sync.carry(clone, "sweep", gh=gh) == {"carried": False, "head": None}
    assert capsys.readouterr().out == ""
    assert (clone / ".git" / "sync-cloud-lease-sweep").exists() and lease(clone, "sweep") == ""
    assert gh.calls == []   # nothing to ask GitHub about


def test_a_branch_main_already_holds_is_not_carried_again(repo, tmp_path):
    head = cloud_branch(tmp_path, {SOURCE: "# r0\n"})
    platform_merges(tmp_path, "cloud/sweep")
    clone = clone_of(tmp_path, "cloud-run")
    before = git(clone, "rev-parse", "HEAD").stdout.strip()
    assert sync.carry(clone, "sweep", gh=ClosedPulls()) == {"carried": False, "head": head}
    assert git(clone, "rev-parse", "HEAD").stdout.strip() == before and lease(clone) == head


def test_only_the_cloud_sweep_is_carried(repo):
    assert sync.CLOUD_CHAINS == ("sweep",)
    for chain in ("main", "daily"):   # the daily stays on the computer: no cloud/daily branch to carry
        with pytest.raises(ValueError):
            sync.carry(repo, chain)
