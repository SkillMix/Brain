# Cloud routines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The sweep and daily chains run as cloud scheduled tasks that land their own files through `sync.py --cloud`, under the computer's own guard profile, while the computer's sync keeps the transcripts scan, template sync, the release leg, requests and the adoption of cloud pull requests nothing could merge.

**Architecture:** A new `cloud.py` starts a cloud run (roots, mounts, gate, carry, guard profile, cursors) and renders the task body the agent hands to `RemoteTrigger`. A `spaces-guard` plugin carries the computer's routine hooks (`bash_guard`, the connector grant, `send_guard`) into the cloud. `sync.py` gains merge rules that never wedge, a `carry` of the previous unmerged `cloud/<chain>` branch, and a `--cloud <chain>` mode that commits once, never lands a protected change, opens its pull request through REST and waits for the merge. Every GitHub call a cloud run reaches moves from GraphQL (`gh pr ...`) to REST (`gh api`) through a small `ghrest.py`. The routine host record gets a third runner, `cloud`.

**Tech Stack:** Python 3.11, standard library plus PyYAML; git; the `gh` command line (REST only); Claude Code plugins (hooks); pytest.

**Spec:** `docs/design/2026-10-27-cloud-routines-design.md`. Read it before any task: section numbers below point into it.

## Global Constraints

- Python 3.11 with PyYAML only; no new dependency (the cloud image has Python 3.11 and PyYAML 6).
- No GraphQL in any code path a cloud run reaches: no `gh pr ...`, no `gh repo list`, no `gh api graphql`. `gh run view` and `gh run rerun` are REST and stay.
- Branches: `cloud/sweep` and `cloud/daily` only (there is no protected twin: the cloud never lands a protected change). Never a push to `main`, never `gh pr merge`, never plain `--force` on a cloud branch (a lease always).
- The template carries no company, client or person names: fixtures use `Ada Okafor`, `ada`, `example.test` (CI runs `no_company_names.py`).
- Messages a person reads are plain sentences in the house voice of the file they sit in (look at the neighbouring messages). No jargon in Needs-you text.
- Tests: `python -m pytest _meta/tools/tests -q` from the repository root, with `GIT_CONFIG_GLOBAL` pointing at an empty file as CI does. Git-touching tests use a throwaway bare origin like `test_sync.py`'s `repo` fixture. Baseline before this branch: 2500 passed, 15 skipped.
- This session cannot commit (its deny rules refuse `git commit`). The controller stages each task's files with `git add` and records `git write-tree` with the task's message in `C:/Users/maintainer/.wt/cloud-routines-land/trees.txt`, outside the worktree (one line: `<tree><TAB><message>`), which the owner's land script turns into one commit per task. Implementers never stage.

## Execution order

Tasks touching the same file run one after another; the rest run in parallel waves:
A: 1, 6, 9. B: 2, 3, 5, 15, 16, 17. C: 4 (after 2), 8, 12 (after 5). D: 14 (after 4), 10 (after 4, 5, 6, 16). E: 7 (after 14), 11 (after 10, 16). F: 13, then 18.

## Review Focus

- The cloud clone's `origin` is a proxy URL (`http://local_proxy@127.0.0.1:34567/git/<owner>/<repo>`): the repository slug is its last two path segments. Pinned in Task 1.
- A shallow clone whose `origin/cloud/<chain>` shares no commit within the clone's depth: the carry deepens, then unshallows, and never merges unrelated histories. Pinned in Task 4.
- The sweep and the daily run land at the same time: the second pull request conflicts on GitHub in `runs.jsonl` and a watermark, which GitHub cannot merge. The wait loop merges `main` again with the merge rules and pushes, at most three times. Pinned in Task 14.
- A cloud run that changed a protected file (an injected edit, or `AGENTS.md` regenerated): the file never lands, `AGENTS.md` is put back quietly, anything else is one security item. Pinned in Task 14.
- The guard before `begin` has written its profile: only `cloud.py begin` runs; after, only the profile's shapes; outside a cloud run, everything. Pinned in Task 16.

---

### Task 1: REST helpers (`ghrest.py`)

**Files:**
- Create: `_meta/tools/ghrest.py`
- Test: `_meta/tools/tests/test_ghrest.py`

**Interfaces:**
- Produces (every `gh` argument is a callable `gh(*args) -> subprocess.CompletedProcess | None`, None when `gh` is not installed, so tests inject a fake):
  - `repo_slug(root: Path) -> str | None`: `owner/name` from `git remote get-url origin`, last two path segments, `.git` stripped; None without an origin.
  - `runner(cwd: Path) -> Callable`: the real `gh`, utf-8, `errors="replace"`, timeout 60 s, None only when `gh` itself is missing (a `FileNotFoundError` that names `gh`); any other launch failure is a failed call.
  - `open_pulls(gh, slug: str, head: str | None = None) -> list[dict] | None`: `GET repos/{slug}/pulls?state=open&per_page=100` (plus `&head={owner}:{head}`). Each dict: `number:int, head_ref:str, head_sha:str, title:str, url:str, draft:bool, auto_merge:bool, cross_repo:bool, created_at:str`. `cross_repo` is True when `head.repo` is null or its `full_name` differs from `base.repo.full_name`. None on a non-zero exit or output that is not a JSON list.
  - `create_pull(gh, slug: str, head: str, title: str, body: str) -> int | None`: `api -X POST repos/{slug}/pulls -f title=… -f head=… -f base=main -f body=…`; the new number.
  - `pull_state(gh, slug: str, number: int) -> dict | None`: `GET repos/{slug}/pulls/{number}` as `{merged: bool, state: str, mergeable_state: str | None, head_sha: str}`.
  - `failed_checks(gh, slug: str, sha: str) -> list[dict] | None`: `GET repos/{slug}/commits/{sha}/check-runs?per_page=100`, the runs whose `conclusion` is one of `failure, timed_out, cancelled, action_required, startup_failure`, as `{name, link}` (`link` = `details_url` else `html_url`).
  - `FAILED = ("failure", "timed_out", "cancelled", "action_required", "startup_failure")`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_slug_is_the_last_two_segments_of_any_origin(tmp_path):
    for url, slug in (("git@github.com:ada/ada-space.git", "ada/ada-space"),
                      ("https://github.com/ada/ada-space", "ada/ada-space"),
                      ("http://local_proxy@127.0.0.1:34567/git/ada/ada-space", "ada/ada-space")):
        repo = init_repo(tmp_path / slug.replace("/", "_"), origin=url)
        assert ghrest.repo_slug(repo) == slug

def test_no_origin_has_no_slug(tmp_path):
    assert ghrest.repo_slug(init_repo(tmp_path / "r", origin=None)) is None

def test_open_pulls_reads_rest_and_marks_forks():   # FakeGh answers the route with two pulls, one from a fork
    pulls = ghrest.open_pulls(fake, "ada/ada-space", head="cloud/sweep")
    assert fake.calls[0][:2] == ("api", "repos/ada/ada-space/pulls?state=open&per_page=100&head=ada:cloud/sweep")
    assert [(p["number"], p["cross_repo"], p["auto_merge"]) for p in pulls] == [(7, False, True), (8, True, False)]

def test_a_failed_call_is_none_not_an_empty_list():
    assert ghrest.open_pulls(failing_gh, "ada/ada-space") is None
    assert ghrest.open_pulls(lambda *a: None, "ada/ada-space") is None

def test_create_pull_posts_to_main_and_returns_the_number():
    assert ghrest.create_pull(fake, "ada/ada-space", "cloud/sweep", "chore: sync", "Landed.") == 12
    assert ("-X", "POST") == fake.calls[-1][1:3] and "base=main" in fake.calls[-1]

def test_failed_checks_keep_only_failed_conclusions():
    assert [c["name"] for c in ghrest.failed_checks(fake, "ada/ada-space", "abc")] == ["checks"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest _meta/tools/tests/test_ghrest.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'ghrest'`

- [ ] **Step 3: Implement `ghrest.py` with the interfaces above**

Module docstring in the house style (what it is, why REST: "a cloud run refuses GraphQL"). `create_pull` passes the body with `-f body=...`; `-f` sends strings, so a number-looking title stays a string.

- [ ] **Step 4: Run them to see them pass**

Run: `python -m pytest _meta/tools/tests/test_ghrest.py -q`
Expected: PASS

- [ ] **Step 5: Stage**

```bash
git add _meta/tools/ghrest.py _meta/tools/tests/test_ghrest.py
```

Tree message: `feat: REST helpers for pull requests, the calls a cloud run allows`

---

### Task 2: `sync.py` on REST

**Files:**
- Modify: `_meta/tools/sync.py` (`open_pr`, `heal`, a new `Run.slug`)
- Test: `_meta/tools/tests/test_sync.py` (update `FakeGh`, add tests)

**Interfaces:**
- Consumes: `ghrest.repo_slug`, `ghrest.open_pulls`, `ghrest.create_pull`, `ghrest.pull_state`, `ghrest.failed_checks` (Task 1). `Run.gh(*args)` stays the one entry to `gh`, so tests keep patching `sync.Run.gh`.
- Produces: `Run.open_pr(branch: str, title: str = "chore: sync") -> int | None` (the open or new pull request's number; `notes["pr"] = "opened"` when it opened one, as today). `Run.slug: str | None` set in `__init__`.

- [ ] **Step 1: Write the failing tests** (the `FakeGh` answers REST routes now: `api repos/{slug}/pulls?...`, `api -X POST repos/{slug}/pulls`, `api repos/{slug}/pulls/{n}`, `api repos/{slug}/commits/{sha}/check-runs?...`, plus `run view` and `run rerun` as before)

```python
def test_the_sync_never_calls_graphql(repo, monkeypatch):
    gh = FakeGh(""); monkeypatch.setattr(sync.Run, "gh", gh)
    write(repo / "work" / "notes.md", "# Notes\n\nline two\n", minutes_old=20)
    assert sync.main(["--root", str(repo)]) == 0
    assert gh.calls and all(c[0] in ("api", "run") and "graphql" not in c for c in gh.calls)

def test_a_sync_pull_request_opens_through_rest_once(repo, monkeypatch):
    ...   # first run: one POST; second run with the pull request open: no second POST
```

Keep `test_a_red_sync_pull_request_from_an_answer_test_heals_with_no_human_step` and `test_a_red_sync_pull_request_with_no_known_cause_is_rerun_once` passing on the REST fake (merged = `pull_state(...)["merged"]`).

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest _meta/tools/tests/test_sync.py -q`
Expected: FAIL on the two new tests and the two heal tests (the fake no longer answers `pr ...`).

- [ ] **Step 3: Move `open_pr` and `heal` to `ghrest`**

`gh` absent stays `notes["gh"] = "absent"`; a slug that cannot be read is `self.error("no GitHub repository behind origin")` once. `heal` reads the open pull request with `open_pulls(head=branch)`, its checks with `failed_checks(head_sha)`, and merged with `pull_state`.

- [ ] **Step 4: Run the sync tests**

Run: `python -m pytest _meta/tools/tests/test_sync.py _meta/tools/tests/test_sync_task.py -q`
Expected: PASS

- [ ] **Step 5: Stage**

```bash
git add _meta/tools/sync.py _meta/tools/tests/test_sync.py
```

Tree message: `refactor: the sync script opens and reads pull requests through REST`

---

### Task 3: `needs_you.py`, `link.py` and `landing.py` on REST

**Files:**
- Modify: `_meta/tools/needs_you.py:563-610` (`_protected_prs`)
- Modify: `_meta/tools/link.py` (`_host_repos`, `_host_lookup`, `_answer`, `check`)
- Modify: `_meta/tools/landing.py:235-260` (`signer_log`, `verified`, and where `land` reads them)
- Test: `_meta/tools/tests/test_needs_you.py`, `_meta/tools/tests/test_link.py`, `_meta/tools/tests/test_landing.py`

**Interfaces:**
- Consumes: `ghrest.repo_slug`, `ghrest.open_pulls`, `ghrest.runner` (Task 1).
- Produces:
  - `link.check(root, now=None, lookup_fn=None, readers_fn=None, graphql: bool = True) -> dict`; `link._host_lookup(repo: str, repo_id=None, graphql: bool = True) -> dict | None` (with `graphql=False` the by-id fallback raises `OSError("not asked by id")`, so the link is kept as it is).
  - `link._answer` raises `OSError` for an answer whose error text says access to the repository "is not enabled" (a cloud run's unattached repository), checked before the 404 rule, so such a link is never marked gone.
  - `landing.NOT_ENABLED = re.compile(r"is not enabled", re.I)`; when the signer's history cannot be read because access is not enabled in this session, `verified` returns the string `"unknown"` and `land` leaves that candidate exactly as it is for a later run (neither landed nor marked pending). Every other outcome is unchanged.

- [ ] **Step 1: Write the failing tests**

```python
def test_protected_pull_requests_come_from_rest(tmp_path, monkeypatch):
    # fake runner answers repos/ada/ada-space/pulls?state=open&per_page=100 with one unarmed, one armed, one fork
    items = needs_you._protected_prs(space)
    assert [i.id for i in items] == ["pr-5", "pr-forks"]

def test_pull_requests_that_cannot_be_read_say_so(tmp_path, monkeypatch):
    # runner exits 1 with "HTTP 401: Bad credentials"
    [item] = needs_you._protected_prs(space)
    assert item.id == "pr-list-unreadable" and "could not be read" in item.text

def test_no_gh_at_all_stays_silent(tmp_path, monkeypatch):
    assert needs_you._protected_prs(space) == []   # runner returns None: offline or not installed

def test_owner_repositories_are_listed_through_rest(monkeypatch):
    # _gh fake: users/ada -> {"type": "User"}; user -> {"login": "ada"};
    # user/repos?affiliation=owner&per_page=100 --paginate --jq .[].full_name -> two lines
    assert link._host_repos("ada") == ["ada/one", "ada/two"]

def test_a_repository_the_cloud_run_cannot_reach_is_never_gone():
    with pytest.raises(OSError):
        link._answer(1, "", "GitHub access to this repository is not enabled for this session (HTTP 404)")

def test_without_graphql_a_renamed_repository_by_id_keeps_its_link(monkeypatch):
    with pytest.raises(OSError):
        link._host_lookup("ada/old-name", "R_kgDOxyz", graphql=False)

def test_a_signer_history_the_run_cannot_reach_leaves_the_release_waiting(tmp_path, monkeypatch):
    # signer_log's gh answers "... is not enabled for this session": the candidate is untouched, not pending
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest _meta/tools/tests/test_needs_you.py _meta/tools/tests/test_link.py _meta/tools/tests/test_landing.py -q`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement**

`_protected_prs`: slug from `ghrest.repo_slug(root)`, pulls from `ghrest.open_pulls(ghrest.runner(root), slug)`. No `gh` at all stays silent; a failed call is one `Item(id="pr-list-unreadable", kind="request", text="your open pull requests could not be read: any waiting for your merge are missing from this list", recommendation="check that gh is signed in on this computer (gh auth status)", default="they wait unseen")`. Keep the fork rule and the draft and auto-merge filters as they are. Telling the two apart is your choice (call the runner once yourself, or a sentinel from `open_pulls`); say which in the docstring.

`_host_repos(owner)`: `_gh("api", f"users/{owner}", "--jq", ".type")`; `Organization` → `orgs/{owner}/repos?type=all&per_page=100`; a `User` who is the signed-in login (`_gh("api", "user", "--jq", ".login")`) → `user/repos?affiliation=owner&per_page=100`; another user → `users/{owner}/repos?per_page=100`; each with `--paginate --jq .[].full_name`.

- [ ] **Step 4: Run them to see them pass**

Run: `python -m pytest _meta/tools/tests/test_needs_you.py _meta/tools/tests/test_link.py _meta/tools/tests/test_landing.py -q`
Expected: PASS

Tree message: `refactor: Needs you, the link check and landing read GitHub through REST, and say when they cannot`

---

### Task 4: Merges that never wedge, and the carry

**Files:**
- Modify: `_meta/tools/sync.py` (`PRIVATE_ZONES`, `MERGE_RULES`, `Run.merge_rules`, `Run.merge_in`, new `Run.resolve`, new `merge_latest`, new `carry`, `parse`/`main` for `--merge-latest`)
- Test: `_meta/tools/tests/test_sync_merge.py` (new; reuse `test_sync.py`'s `repo` fixture by importing it, or a copy)

**Interfaces:**
- Consumes: `ghrest.repo_slug`, `ghrest.runner`, and a new `ghrest.closed_unmerged(gh, slug: str, branch: str, sha: str) -> bool` you add to `ghrest.py` (`GET repos/{slug}/pulls?state=closed&head={owner}:{branch}&per_page=100`; True when one of them has `head.sha == sha` and `merged_at` null; False on any failure, so a carry is never refused for an unreadable answer).
- Produces (spec 4.5a, 4.2):
  - `PRIVATE_ZONES` gains `"_meta/state/feed/"`.
  - `MERGE_RULES` gains `"_meta/state/*-watermark.yaml merge=spaces-latest"`, `"live/** merge=spaces-ours"`, `"_meta/state/ids.yaml merge=spaces-ours"`, `"_meta/state/stale.yaml merge=spaces-ours"`. `Run.merge_rules()` also writes, in the space's own git config, `merge.spaces-ours.driver = true` and `merge.spaces-latest.driver = "<sys.executable>" -I "<this file>" --merge-latest %O %A %B` (each path quoted), each only when missing or different. This applies on the computer too.
  - `merge_latest(base: Path, ours: Path, theirs: Path) -> int`: YAML of both sides; dicts merge key by key (recursively); where both values read as ISO moments (`pages.parse_iso`) the later wins; a key on one side only is kept; any other difference keeps ours. Writes the result to `ours`; returns 0, or 1 when either side is not readable YAML (git then keeps the conflict). CLI: `sync.py --merge-latest O A B`.
  - `Run.resolve() -> int`: after a merge stopped, resolves what the rules cover and returns how many conflicted paths are left. A path under `_inbound/` or `_outbound/` that one side deleted: the deletion wins (`git rm`). A path the `spaces-ours` rules name that both sides added: ours. `merge_in` becomes: merge → on failure `left = self.resolve()` → `left == 0`: commit the merge (`notes["resolved"] = n`) → else `keep_ours()` for what is left → else abort and record the conflict, as today.
  - `carry(root: Path, chain: str, gh=None) -> dict` (module level; `cloud.begin` calls it before any export, Task 10): fetch `+refs/heads/cloud/<chain>:refs/remotes/origin/cloud/<chain>` explicitly (a missing branch returns `{"carried": False, "head": None}` quietly). When `ghrest.closed_unmerged(...)` says the branch head is a pull request the person closed, nothing is merged (`{"carried": False, "head": <sha>, "skipped": "closed"}`). When `git merge-base HEAD origin/cloud/<chain>` finds nothing: `git fetch --deepen=200 origin main cloud/<chain>` up to three times, then `git fetch --unshallow origin`. Then the merge rules are written and `git merge --no-edit origin/cloud/<chain>` runs with `resolve`/`keep_ours` as in `merge_in`; never `--allow-unrelated-histories`. A merge that still conflicts is aborted: `{"carried": False, "head": <sha>, "conflict": True}`. Success: `{"carried": True, "head": <sha>}`. In every case the fetched head (or `None`) is written to `<git dir>/sync-cloud-lease-<chain>` for the push's lease (Task 14).

- [ ] **Step 1: Write the failing tests**

```python
def test_the_feed_is_a_private_zone():
    assert "_meta/state/feed/" in sync.PRIVATE_ZONES

def test_watermarks_merge_to_the_later_mark_key_by_key(repo, tmp_path):
    # main: feeds.mail 2026-10-20, feeds.calls 2026-10-25; here: feeds.mail 2026-10-26, feeds.calls 2026-10-21
    # -> after merge_in: mail 2026-10-26, calls 2026-10-25, no conflict recorded

def test_generated_files_keep_this_checkouts_side(repo, tmp_path):
    # live/now.md and _meta/state/stale.yaml changed on main and here -> merge succeeds, ours kept

def test_an_answered_item_deleted_here_and_changed_there_stays_deleted(repo, tmp_path): ...
def test_a_shipped_queue_item_deleted_there_and_restamped_here_stays_deleted(repo, tmp_path): ...

def test_any_other_conflict_still_stops_the_merge(repo, tmp_path):
    # knowledge/x.md changed on both sides -> notes["conflict"] and the merge aborted, as today

def test_merge_latest_keeps_ours_when_a_side_is_not_yaml(tmp_path):
    assert sync.merge_latest(base, ours, theirs_with_junk) == 1

def test_carry_brings_the_last_runs_unmerged_branch_in_before_anything_is_written(repo, tmp_path):
    # origin has cloud/sweep with r0.md and a newer sweep-watermark never merged; a fresh clone:
    # sync.carry(clone, "sweep") -> r0.md and the newer watermark are in the working tree, lease file holds its sha

def test_a_shallow_clone_deepens_to_carry_an_old_branch(tmp_path, monkeypatch):
    # origin main has 30 commits after cloud/sweep's base; clone --depth 1 --single-branch; carry merges it
    # without --allow-unrelated-histories

def test_a_branch_whose_pull_request_was_closed_unmerged_is_not_carried(repo, tmp_path): ...
def test_a_carry_that_conflicts_is_aborted_and_said(repo, tmp_path): ...
def test_no_branch_to_carry_is_quiet_and_leases_on_absence(repo, tmp_path): ...
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest _meta/tools/tests/test_sync_merge.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** with the interfaces above. The sync docstring's step list gains one sentence on the merge rules; `carry`'s docstring says why it runs at the start of a run (the cursors and watermarks then hold what the earlier run read).

- [ ] **Step 4: Run every sync test**

Run: `python -m pytest _meta/tools/tests/test_sync_merge.py _meta/tools/tests/test_sync.py _meta/tools/tests/test_ghrest.py -q`
Expected: PASS

Tree message: `feat: merges that never wedge, and the carry of a cloud run's unmerged branch`

---

### Task 5: The `cloud` runner

**Files:**
- Modify: `_meta/tools/routine.py:125-160` (`PROTECTED_EDITS`), `:1495-1671` (host record), `routine_settings` (new `extra_allow`)
- Modify: `_meta/tools/onboard.py` (`routines_elsewhere`, `pause_routines`)
- Modify: `_meta/tools/backfill.py:318-330` callers' sentence for a cloud record
- Test: `_meta/tools/tests/test_routines.py`, `_meta/tools/tests/test_onboard.py`, `_meta/tools/tests/test_backfill.py`

**Interfaces:**
- Produces:
  - `routine.RUNNERS = ("os", "harness", "cloud")`; `routine.CLOUD_MACHINE = "m-cloud"` (fits `MACHINE`); `routine.CLOUD_NAME = "the cloud"`; `routine.TASK_ID = re.compile(r"trig_[A-Za-z0-9]{8,64}")`.
  - `routine.PROTECTED_EDITS` gains `"_meta/plugins/**"` and `".claude-plugin/**"`.
  - `routine.host(workspace)` also returns, only when written: `tasks: dict` (keys `sweep`, `daily`; values matching `TASK_ID`), `timezone: str`, `roots: list[str]` (`owner/name` slugs), `grant: dict` (`read`, `write`, `send`: lists of full tool names). A value of the wrong shape makes the record unclear.
  - `routine.claim_cloud(workspace, tasks: dict, timezone: str, roots: list, grant: dict, now) -> dict`: writes `machine: m-cloud`, `name: the cloud`, `runner: cloud`, `since` and the four keys above, keeping a pause still to come. `claim_host` refuses `cloud` (`ValueError` naming `claim_cloud`). `set_pause` keeps every key.
  - `routine.cloud_gate(workspace, now) -> str | None`: None to go on; else "nothing says your routines run in the cloud" (no record), `UNCLEAR`, "they run on <name>" (runner not cloud), or "paused until <time>" (in the record's `timezone` when it has one).
  - `_held` returns `("away", "they run in the cloud")` for a cloud record on any computer.
  - `routine.routine_settings(..., extra_allow=())`: extra allow rules (Bash shapes) added after the tool rules, only when the Bash guard is written; Task 10 passes the cloud's read-only `gh api` shapes.
  - `onboard.routines_elsewhere` says "Your routines run in the cloud, as two scheduled tasks; this computer's sync lands what they leave open." for a cloud record. `pause_routines` with a cloud record adds "The cloud's next run reads the pause once this computer's sync lands it; away from this computer, turn the two tasks off on claude.ai instead." and not the harness sentence.
  - `backfill`'s refusal on a computer that is not the host says, for a cloud record: "Your routines run in the cloud, and the history is read only on a computer: say 'run my routines here' first."

- [ ] **Step 1: Write the failing tests**

```python
def test_a_cloud_claim_reads_back_with_its_tasks_zone_roots_and_grant(tmp_path):
    rec = routine.claim_cloud(tmp_path, {"sweep": "trig_01AAAAAAAA", "daily": "trig_01BBBBBBBB"},
                              "Europe/Amsterdam", ["ada/ada-space"], GRANT, NOW)
    assert (rec["runner"], rec["machine"], rec["tasks"]["daily"], rec["timezone"], rec["roots"]) == \
           ("cloud", "m-cloud", "trig_01BBBBBBBB", "Europe/Amsterdam", ["ada/ada-space"])

def test_every_computer_steps_aside_for_the_cloud(tmp_path, monkeypatch):
    routine.claim_cloud(tmp_path, TASKS, "Europe/Amsterdam", ROOTS, GRANT, NOW)
    assert routine.gate(tmp_path, NOW) == "they run in the cloud"

def test_a_task_id_or_grant_that_is_none_makes_the_record_unclear(tmp_path): ...

def test_a_pause_keeps_the_cloud_claims_keys(tmp_path): ...

def test_the_cloud_gate_goes_on_only_for_a_cloud_record_not_paused(tmp_path):
    assert routine.cloud_gate(tmp_path, NOW) == "nothing says your routines run in the cloud"
    routine.claim_cloud(tmp_path, TASKS, "Europe/Amsterdam", ROOTS, GRANT, NOW)
    assert routine.cloud_gate(tmp_path, NOW) is None

def test_a_pause_set_in_local_time_stops_a_utc_cloud_run_until_then(tmp_path):
    routine.claim_cloud(tmp_path, TASKS, "America/New_York", ROOTS, GRANT, NOW_UTC)
    routine.set_pause(tmp_path, dt.datetime(2026, 10, 28, 6, 0, tzinfo=ZoneInfo("America/New_York")))
    assert routine.cloud_gate(tmp_path, dt.datetime(2026, 10, 28, 9, 59, tzinfo=dt.timezone.utc)).startswith("paused until")
    assert routine.cloud_gate(tmp_path, dt.datetime(2026, 10, 28, 10, 1, tzinfo=dt.timezone.utc)) is None

def test_two_merged_claims_one_cloud_one_os_leave_nobody_running(tmp_path): ...
def test_the_extra_allow_rules_join_only_a_guarded_profile(tmp_path): ...
def test_the_check_says_the_routines_run_in_the_cloud(tmp_path): ...   # onboard.routines_elsewhere
def test_a_pause_with_cloud_routines_says_when_the_cloud_hears_of_it(tmp_path): ...
def test_a_history_read_cannot_start_while_the_routines_run_in_the_cloud(tmp_path): ...
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest _meta/tools/tests/test_routines.py _meta/tools/tests/test_onboard.py _meta/tools/tests/test_backfill.py -q -k "cloud or extra_allow"`
Expected: FAIL, `AttributeError: module 'routine' has no attribute 'claim_cloud'`

- [ ] **Step 3: Implement** with the interfaces above. `_write_host` writes each extra key as one JSON line after the claim's four, when present. The comment above `HOST` gains one sentence on the cloud runner.

- [ ] **Step 4: Run the routine, onboard and backfill tests**

Run: `python -m pytest _meta/tools/tests/test_routines.py _meta/tools/tests/test_onboard.py _meta/tools/tests/test_backfill.py _meta/tools/tests/test_sync.py _meta/tools/tests/test_permissions.py -q`
Expected: PASS

Tree message: `feat: a third runner, the cloud, that every computer steps aside for`

---

### Task 6: The transcripts scan never blanks, and the sweep's cursor

**Files:**
- Modify: `_meta/tools/transcripts.py:156-200` (`scan`)
- Modify: `_meta/tools/sweep.py` (new `cursor`, CLI `--cursor`)
- Test: `_meta/tools/tests/test_transcripts.py`, `_meta/tools/tests/test_sweep.py`

**Interfaces:**
- Produces:
  - `transcripts.scan(root, now=None) -> dict`: when `transcripts.path` is set but is not a folder on this computer, returns `{"seen": 0, "captured": 0, "witnessed": 0, "switch": ..., "path": "missing"}` and writes neither `sessions.yaml` nor a run line.
  - `sweep.cursor(root, source: str, now=None) -> str`: ISO time. The newest `at` among the records in `_meta/state/feed/<source>.jsonl`; else the watermark's mark for that feed (`read_feed_marks`); else `now - 90 days`. CLI `python sweep.py [root] --cursor <source>` prints it.
  - `sweep.CURSOR_DAYS = 90`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_missing_transcripts_folder_leaves_the_sessions_file_as_it_was(tmp_path): ...
def test_the_cursor_is_the_feeds_newest_record_else_the_mark_else_ninety_days(tmp_path):
    assert sweep.cursor(space, "mail", now=NOW) == "2026-07-29T12:00:00Z"   # nothing at all
    write_marks(space, {"mail": "2026-10-20T08:00:00Z"})
    assert sweep.cursor(space, "mail", now=NOW) == "2026-10-20T08:00:00Z"
    write_feed(space, "mail", [{"id": "a", "at": "2026-10-26T09:00:00Z"}, {"id": "b", "at": "2026-10-25T09:00:00Z"}])
    assert sweep.cursor(space, "mail", now=NOW) == "2026-10-26T09:00:00Z"
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest _meta/tools/tests/test_transcripts.py _meta/tools/tests/test_sweep.py -q`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement** with the interfaces above; `cursor` writes nothing; a record whose `at` cannot be read is skipped as `read_feed` skips it. Format the time with `pages.iso`.

- [ ] **Step 4: Run them to see them pass**

Run: `python -m pytest _meta/tools/tests/test_transcripts.py _meta/tools/tests/test_sweep.py -q`
Expected: PASS

- [ ] **Step 5: Stage**

```bash
git add _meta/tools/transcripts.py _meta/tools/sweep.py _meta/tools/tests/test_transcripts.py _meta/tools/tests/test_sweep.py
```

Tree message: `fix: the transcripts scan never blanks the sessions list, and the sweep says where each export starts`

---

### Task 7: The computer's leg

**Files:**
- Modify: `_meta/tools/sync.py` (the main flow after `merge_in`; new `Run.computer_leg`, `Run.adopt_cloud`)
- Modify: `_meta/tools/chain.py` (expose `_template_sync_due` and the transcripts cadence for the sync)
- Modify: `_meta/tools/transcripts.py` (`scan` writes `sessions.yaml` only when a row changed)
- Test: `_meta/tools/tests/test_sync_cloud_leg.py` (new), `_meta/tools/tests/test_transcripts.py`

**Interfaces:**
- Consumes: `routine.host`, `routine.CLOUD_MACHINE` (Task 5); `ghrest.open_pulls`, `ghrest.pull_state`, `ghrest.failed_checks` (Task 1); `Run.resolve`, the merge rules (Task 4); `Run.hold_failed` (Task 14); `transcripts.scan` (Task 6); `chain.spaces_on_machine`.
- Produces (spec 4.5), each only when this space's workspace record says `runner: cloud`:
  - `Run.computer_leg(now) -> dict`, called right after `merge_in`, before staging. Steps in order, each writing its outcome into `notes`:
    1. `not_started` (moved from before the merge to here when the runner is cloud; unchanged otherwise);
    2. transcripts: `transcripts.scan(root, now)` at most every 6 hours (`TRANSCRIPTS_HOURS = 6`, last run time in `_meta/state/routine-limits.json` under `transcripts_at`), only when `transcripts.path` is a folder here;
    3. template sync: when `chain._template_sync_due(root, now)` (weekly), `template_sync.guarded(root)` as the daily chain runs it today;
    4. adoption: `self.adopt_cloud(now)`;
    5. spaces not attached: the slugs of `chain.spaces_on_machine()` that this person owns, minus the record's `roots`; each one left is a line of one item in `_inbound/needs-you/sync.yaml` (`id: cloud-roots`, kind `request`, text "Your routines in the cloud do not read <space>: it was made after you moved them.", recommendation "say 'run my routines in the cloud' again to add it", default "it is not swept or briefed until then"). The item is removed when the list is empty.
  - `Run.adopt_cloud(now) -> list[int]`: open pull requests from `cloud/sweep` or `cloud/daily` in this repository (`ghrest.open_pulls(..., head=...)`), created more than 30 minutes ago (`ADOPT_MINUTES = 30`), whose `pull_state` is `mergeable_state: dirty` or that have failed checks. Each is fetched with an explicit refspec and merged into this checkout with the merge rules and `resolve`; the numbers adopted are returned and named in `notes["adopted"]`. A merge that still conflicts is aborted and becomes one item in `_inbound/needs-you/sync.yaml` (`id: cloud-adopt-<n>`, kind `request`, text "A cloud run's pull request #<n> conflicts with your files in a way the sync cannot settle.", recommendation "open it and keep the side you want, or close it: the next cloud run starts again from main", default "it stays open and the cloud runs keep adding to it").
  - `transcripts.scan` compares the rows it would write with the file's rows and leaves the file (and its mtime) untouched when they are equal.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_cloud_workspace_scans_transcripts_on_the_computer_at_most_every_six_hours(repo, tmp_path, monkeypatch): ...
def test_a_computer_runner_workspace_keeps_todays_order(repo, tmp_path, monkeypatch):
    # runner os: not_started still runs before merge_in; no transcripts, no adoption
def test_not_started_reads_the_clouds_run_lines_after_the_merge(repo, tmp_path, monkeypatch):
    # origin/main gained today's cloud sweep line; a computer that slept says nothing did not start
def test_template_sync_runs_weekly_from_the_computers_sync(repo, tmp_path, monkeypatch): ...
def test_a_red_cloud_pull_request_older_than_half_an_hour_rides_the_computers_sync(repo, tmp_path, monkeypatch):
    # FakeGh: cloud/sweep #7, created 40 min ago, a failed check -> merged here, notes["adopted"] == [7]
def test_a_young_cloud_pull_request_is_left_to_its_run(repo, tmp_path, monkeypatch): ...
def test_a_cloud_pull_request_that_cannot_be_merged_here_is_one_item(repo, tmp_path, monkeypatch): ...
def test_a_space_the_cloud_does_not_read_is_named_once(repo, tmp_path, monkeypatch): ...
def test_an_unchanged_scan_leaves_the_sessions_file_alone(tmp_path): ...
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest _meta/tools/tests/test_sync_cloud_leg.py _meta/tools/tests/test_transcripts.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** with the interfaces above. The sync docstring's step list gains one paragraph on the computer's leg for a cloud workspace.

- [ ] **Step 4: Run every sync, chain and transcripts test**

Run: `python -m pytest _meta/tools/tests/test_sync_cloud_leg.py _meta/tools/tests/test_sync.py _meta/tools/tests/test_sync_merge.py _meta/tools/tests/test_chain.py _meta/tools/tests/test_transcripts.py -q`
Expected: PASS

Tree message: `feat: the computer's sync scans transcripts, runs template sync and adopts stuck cloud pull requests`

---

### Task 8: `chain.py --cloud` and `chain.py gate`

**Files:**
- Modify: `_meta/tools/chain.py` (`sweep_chain`, `daily`, `parse`/`main`)
- Test: `_meta/tools/tests/test_chain.py`

**Interfaces:**
- Consumes: `routine.host`, `routine.gate`, `routine.cloud_gate`, `routine.UNCLEAR` (Task 5); `link.check(..., graphql=False)` (Task 3).
- Produces (spec 4.8, 4.6):
  - `sweep_chain(root, now, cloud: bool = False)`: with `cloud`, the transcripts step is not run (no line for it) and `outbox_reconcile` runs after the reply step.
  - `daily(root, person, now, shadow, offline_roots, cloud: bool = False)`: with `cloud`, `template_sync`, `release`, `requests_collect`, `requests_answer` and `outbox_reconcile` are left out, and the link check runs with `graphql=False`.
  - CLI: `chain.py sweep <root> --cloud`, `chain.py daily <root> --cloud`.
  - CLI `chain.py gate <workspace>`: exit 0 and print nothing when this computer may run the chains now; otherwise print one sentence and exit 3. Stops when the record's runner is `cloud` ("your routines run in the cloud"), the host is another machine ("they run on <name>"), the record is unclear (`routine.UNCLEAR`) or it is paused ("paused until <time>"). No record at all goes on (today's behaviour for a space that never claimed).

- [ ] **Step 1: Write the failing tests**

```python
def test_a_cloud_sweep_leaves_transcripts_to_the_computer_and_reconciles_the_outbox(tmp_path, monkeypatch):
    steps = run_sweep_chain(tmp_path, cloud=True)        # records each step name the chain called
    assert "transcripts" not in steps and steps.index("outbox_reconcile") > steps.index("reply")

def test_a_cloud_daily_leaves_release_requests_template_sync_and_outbox_to_the_computer(tmp_path, monkeypatch):
    steps = run_daily(tmp_path, cloud=True)
    assert not {"template_sync", "release", "requests_collect", "requests_answer", "outbox_reconcile"} & set(steps)

def test_a_cloud_daily_asks_the_link_check_without_graphql(tmp_path, monkeypatch): ...

def test_the_gate_stops_a_computer_task_when_the_routines_run_in_the_cloud(tmp_path, capsys):
    routine.claim_cloud(tmp_path, TASKS, "Europe/Amsterdam", ROOTS, GRANT, NOW)
    assert chain.main(["gate", str(tmp_path)]) == 3
    assert "cloud" in capsys.readouterr().out

def test_the_gate_lets_the_host_computer_run(tmp_path): ...
def test_the_gate_stops_a_paused_space(tmp_path): ...
def test_without_a_record_the_gate_goes_on(tmp_path): ...
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest _meta/tools/tests/test_chain.py -q -k "cloud or gate"`
Expected: FAIL

- [ ] **Step 3: Implement** with the interfaces above.

- [ ] **Step 4: Run the chain tests**

Run: `python -m pytest _meta/tools/tests/test_chain.py _meta/tools/tests/test_routines.py -q`
Expected: PASS

Tree message: `feat: the chains know what a cloud run leaves to the computer, and a gate for the computer's own tasks`

---

### Task 9: The briefing's recipients are checked

**Files:**
- Modify: `_meta/tools/morning_view.py:144-161` (`record_send`), `:459-481` (`_guard_cli`)
- Test: `_meta/tools/tests/test_morning_view.py`

**Interfaces:**
- Produces: `record_send(root, message_id=None, failed=None, cost_tokens=0, now=None, to: list | None = None) -> dict`; CLI `morning_view.py [root] record-send --id <id> --to <address> [<address> ...]`. Each `to` entry may be `Name <addr>`; the address is compared lowercased and stripped with the space's `owner_email`. Any other address: the line's notes are `{"step": "send", "failed": "recipient", "email": <id>, "others": <count>}` and one item is written to `_inbound/needs-you/morning-view.yaml` (`id: morning-view-recipient`, kind `security`, text "Your morning briefing (message <id>) went to an address that is not yours. Check the message in your Sent mail.", recommendation "check who received it and tell them to delete it", default "it stays on this list until you answer"). No address is written into the repository. Without `to`, today's behaviour.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_briefing_read_back_to_the_person_is_recorded_as_sent(tmp_path):
    line = morning_view.record_send(space, "m1", to=['"Ada Okafor" <Ada@Example.test>'])
    assert line["notes"] == {"step": "send", "email": "m1"}
def test_a_briefing_with_another_recipient_is_a_security_item(tmp_path):
    line = morning_view.record_send(space, "m2", to=["ada@example.test", "someone@else.test"])
    assert line["notes"]["failed"] == "recipient" and line["notes"]["others"] == 1
    assert "someone@else.test" not in (space / "_inbound" / "needs-you" / "morning-view.yaml").read_text()
def test_a_failed_recipient_check_does_not_count_as_sent_today(tmp_path):
    assert morning_view.sent_today(space, now=NOW) is False
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest _meta/tools/tests/test_morning_view.py -q`
Expected: FAIL, `TypeError: record_send() got an unexpected keyword argument 'to'`

- [ ] **Step 3: Implement** with the interfaces above (`email.utils.getaddresses` parses the display names). Check `sent_today` ignores a line with `failed`.

- [ ] **Step 4: Run them to see them pass**

Run: `python -m pytest _meta/tools/tests/test_morning_view.py _meta/tools/tests/test_needs_you.py -q`
Expected: PASS

- [ ] **Step 5: Stage**

```bash
git add _meta/tools/morning_view.py _meta/tools/tests/test_morning_view.py
```

Tree message: `feat: the briefing's read-back refuses any recipient but the person`

---

### Task 10: `cloud.py`: begin, the task body and the proof line

**Files:**
- Create: `_meta/tools/cloud.py`
- Modify: `_meta/tools/routine.py` (`routine_settings` gains `extra_allow` if Task 5 did not already land it)
- Test: `_meta/tools/tests/test_cloud.py` (new)

**Interfaces:**
- Consumes: `routine.host`, `routine.cloud_gate`, `routine.routine_settings`, `routine.PROTECTED_EDITS`, `routine.CLOUD_MACHINE` (Task 5); `sync.carry` (Task 4); `sweep.cursor` (Task 6); `chain.spaces`; `reader.DENIED`; `apps.yaml` verdicts through the module the onboard grant reads today; the guard plugin's paths (Task 16).
- Produces (spec 4.1, 4.2, 4.6, 4.7, 4.9, 4.11):
  - `HOME = Path("/home/user")`, overridable by `SPACES_CLOUD_HOME` for tests; `STATE = Path.home() / ".spaces" / "routines" / "cloud.json"`; `PIN_DAYS = 3`.
  - `begin(workspace: Path, chain: str, proof: bool = False, now=None, gh=None) -> dict`:
    1. roots: the clones under `HOME` the person owns (the workspace's `owner` matches the space's `owner`), workspace first;
    2. mounts: `git submodule update --init <path>` one at a time, at the pin; a mount not attached is named in `result["unreachable"]`, never an error;
    3. gate: unless `proof`, `routine.cloud_gate(workspace, now)`; a stop returns `{"stopped": <sentence>}` and writes nothing at all (no run line, no profile, no carry);
    4. carry: `sync.carry(root, chain, gh)` for each root; a conflict stops the run (`{"stopped": "the last cloud run's branch conflicts with main in <root>"}`) after writing one item to `_inbound/needs-you/sync-cloud.yaml` in the workspace;
    5. profile: `routine.routine_settings(...)` from the record's `grant` (with `--proof`, the grant `trigger` would render), `shell="/bin/sh"`, `extra_allow=("Bash(gh api search/issues?*)", "Bash(gh api repos/*)")`, written to `~/.spaces/routines/cloud-<chain>.settings.json`; then `STATE` = `{"workspace", "profile", "person", "chain"}`;
    6. cursors: for the sweep, `sweep.cursor(root, source, now)` for every declared source (with `proof`, `now` for each, so nothing is exported);
    7. silence (4.11): the other chain's newest line in `runs.jsonl` on `main` older than twice the longest gap between its scheduled hours, and not paused, is one item (`id: cloud-silent-<chain>`); the daily also reads each mount's pin commit date: older than `PIN_DAYS` is one item (`id: cloud-stale-pin`).
    Prints a JSON result: `{"roots", "unreachable", "carried", "cursors", "items"}`.
  - `trigger(workspace: Path, chain: str, proof: bool = False) -> dict`: the `RemoteTrigger` body. `name` ("<person>'s <chain>"), `cron_expression` (`CRON_TZ=<zone> m h * * *` from the prompts' hours, minutes off the hour), `enabled: False`; `mcp_connections` from the space's connector names, `permitted_tools` the grant; `job_config.ccr.session_context`: `sources` (the roots, their mounts, and for the sweep the linked codebases from `_meta/state/links.json` and the `repo` sources), `allowed_tools`, `disallowed_tools` (spec 4.7 layer 2, plus `Edit(~/.spaces/**)` and `Edit(//home/user/.spaces/**)`); `enabled_plugins: ["spaces-guard@spaces"]` and `extra_marketplaces` naming the template repository's marketplace, both built by one function `_plugin_fields(workspace)` so the shape can change after the trial; `auto_mode_soft_deny`: the five sentences of spec 4.7 layer 3; the prompt: who the person is, the workspace clone, and "follow `_meta/templates/cloud/<chain>-chain.md`" (with `proof`, "follow its Proof section only"). Roles: read for read apps, the tracker's create and update for the sweep, and `send_message` plus `get_message` for mail in both tasks. Writes the render with `grant` and `roots` to `~/.spaces/cloud-<chain>.json` and prints it.
  - `proof_line(workspace, chain, guard: bool, edit: bool, tool: bool, send: bool | None, now=None) -> dict`: appends `{"routine": "cloud-proof", "chain", "guard", "edit", "tool", "send", "at"}` to `_meta/state/runs.jsonl`; `send` only for the daily. CLI `cloud.py proof-line --chain c --guard yes|no --edit yes|no --tool yes|no [--send yes|no]`.
  - CLI: `cloud.py begin <workspace> --chain sweep|daily [--proof]`, `cloud.py trigger <workspace> --chain sweep|daily [--proof]`, `cloud.py proof-line ...`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_run_that_steps_aside_writes_nothing(cloud_home):
    # record runner os -> begin returns stopped; runs.jsonl, the profile and the state file are untouched
def test_begin_carries_the_last_run_before_printing_the_cursors(cloud_home, monkeypatch): ...
def test_a_carry_that_conflicts_stops_the_run_with_one_item(cloud_home, monkeypatch): ...
def test_begin_writes_the_guard_profile_from_the_records_grant(cloud_home):
    prof = json.loads((home / ".spaces/routines/cloud-sweep.settings.json").read_text())
    assert "Bash(gh api search/issues?*)" in prof["permissions"]["allow"]
    assert "mcp__mail__trash_message" not in json.dumps(prof["permissions"]["allow"])
def test_a_proof_begin_skips_only_the_runner_check_and_exports_nothing(cloud_home): ...
def test_a_mount_that_is_not_attached_is_named_not_failed(cloud_home, monkeypatch): ...
def test_a_silent_other_chain_is_one_item(cloud_home): ...
def test_a_pin_older_than_three_days_is_one_item(cloud_home): ...
def test_the_trigger_denies_every_ungranted_tool_by_name(tmp_path):
    body = cloud.trigger(space, "daily")
    denied = body["job_config"]["ccr"]["session_context"]["disallowed_tools"]
    assert "mcp__mail__trash_message" in denied and "WebFetch" in denied and "mcp__github" in denied
def test_the_trigger_enables_the_guard_plugin_and_never_runs_enabled(tmp_path): ...
def test_the_trigger_schedule_keeps_the_persons_hours_off_the_hour(tmp_path):
    assert body["cron_expression"].startswith("CRON_TZ=Europe/Amsterdam ") and not body["cron_expression"].split()[1] == "0"
def test_the_sweep_attaches_linked_codebases_and_the_daily_attaches_no_template(tmp_path): ...
def test_a_proof_line_lands_the_refusals_it_saw(tmp_path): ...
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest _meta/tools/tests/test_cloud.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'cloud'`

- [ ] **Step 3: Implement** `cloud.py` with the interfaces above, stdlib plus PyYAML, in the house style of `chain.py` (a module docstring that says what the tool is for and which steps call it).

- [ ] **Step 4: Run the cloud, routine and sync tests**

Run: `python -m pytest _meta/tools/tests/test_cloud.py _meta/tools/tests/test_routines.py _meta/tools/tests/test_sync_merge.py -q`
Expected: PASS

Tree message: `feat: cloud.py starts a cloud run under the computer's guard and renders its task`

---

### Task 11: The cloud prompts, and the skills that know about the cloud

**Files:**
- Create: `_meta/templates/cloud/sweep-chain.md`, `_meta/templates/cloud/daily-chain.md`
- Modify: `_meta/templates/routines/*.md` (step 0 `chain.py gate`)
- Modify: `.claude/skills/sweep/SKILL.md`, `.claude/skills/distill/SKILL.md`, `.claude/skills/owner-email/SKILL.md`
- Modify: `_meta/tools/routine.py` (`ROUTINE_TOOLS` gains `cloud.py`)
- Test: `_meta/tools/tests/test_cloud_prompts.py` (new), `_meta/tools/tests/test_routines.py`

**Interfaces:**
- Consumes: `cloud.py begin|proof-line` (Task 10); `chain.py gate`, `chain.py ... --cloud` (Task 8); `sweep.py --cursor` (Task 6); `sync.py --cloud` (Task 14); the guard plugin (Task 16).
- Produces:
  - Each cloud prompt is the chain's own steps in the order of spec 4.2, every command an absolute path (`python3 /home/user/<repo>/_meta/tools/<tool>.py ...`), one command per line, never a compound. The sweep's export reads the mail connector for mail and attachments and the repository feed through `gh api search/issues?q=...` and `gh api repos/<slug>/pulls/<n>/files`.
  - A `## Proof` section in both: (1) run `python3 -c "print(1)"` and note whether it was refused; (2) try to Edit `/home/user/<workspace>/rules.md` and note the refusal; (3) call one connector tool outside the grant (daily: the mail connector's `trash_message`; sweep: any write tool of a read app) and note the refusal; (4) daily only: call the send tool to `nobody@example.test` and note the refusal; (5) `cloud.py proof-line` with what it saw; (6) `sync.py --root <workspace> --cloud <chain>`.
  - A tool tripwire in both: a tool the prompt does not name, or a refusal the prompt does not expect, ends the run with its line and no landing of anything else.
  - Every file in `_meta/templates/routines/` gains a step 0: `python <tools>/chain.py gate <workspace>`; on exit 3 the task stops and writes nothing.
  - The sweep, distill and owner-email skills: when `routine-host.yaml` says `runner: cloud`, a session on the computer leaves the sweep, the distill and the queued owner notices to the cloud (a notice it queued itself it still sends). The sweep skill's repository feed reads pull requests through REST only.
  - `routine.ROUTINE_TOOLS` gains `"cloud.py"`.
  - The sweep skill and the cloud sweep prompt call `sweep.py <root> --attachment-unread <record id> <name>` for an attachment the run could not read, and `--attachment-read <record id> <name>` for one read with no text. They say that `--attachments` prints tab-separated rows whose subject may be empty (`<id>\t\t<name>`). The guard profile allows these shapes (Task 17).

- [ ] **Step 1: Write the failing tests**

```python
def test_every_cloud_prompt_command_is_one_absolute_call(template_root):
    for line in commands_in(template_root / "_meta/templates/cloud/sweep-chain.md"):
        assert line.startswith("python3 /home/user/") and not re.search(r"&&|\|\||;|\|", line)
def test_the_cloud_prompts_never_call_graphql(template_root): ...   # no "gh pr", "gh repo list", "api graphql"
def test_both_cloud_prompts_have_a_proof_that_tries_each_refusal(template_root): ...
def test_every_computer_prompt_starts_with_the_gate(template_root): ...
def test_the_cloud_prompts_are_not_rendered_into_computer_tasks(template_root):
    assert not any("cloud" in t["name"] for t in start.render_routines(template_root))
def test_a_cloud_run_may_call_cloud_py(tmp_path): ...   # bash_guard allows cloud.py begin
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest _meta/tools/tests/test_cloud_prompts.py _meta/tools/tests/test_routines.py -q`
Expected: FAIL

- [ ] **Step 3: Write the prompts and the skill changes.** Prompts and skills are read by an agent: plain imperative sentences, one step per numbered line, the house voice of the existing routine prompts.

- [ ] **Step 4: Run the prompt, routine and start tests, and the behaviour suite**

Run: `python -m pytest _meta/tools/tests/test_cloud_prompts.py _meta/tools/tests/test_routines.py _meta/tools/tests/test_start.py -q` then `python _meta/tools/behaviour.py . --all`
Expected: PASS

Tree message: `feat: the cloud chains' prompts, with a proof, and the computer's tasks step aside for them`

---

### Task 12: Onboarding: "run my routines in the cloud"

**Files:**
- Modify: `_meta/tools/onboard.py` (`--routines-cloud`, `--check`, the branch protection it sets)
- Modify: `.claude/skills/onboard/SKILL.md` (the cloud flow of spec 4.10)
- Test: `_meta/tools/tests/test_onboard.py`

**Interfaces:**
- Consumes: `routine.claim_cloud`, `routine.host` (Task 5); `ghrest.runner`, `ghrest.repo_slug` (Task 1); the `cloud-proof` lines (Task 10).
- Produces (spec 4.6, 4.10):
  - `onboard.py --routines-cloud --check [root]`: prints what blocks the move, one sentence each: history not yet read (`backfill.active`), no app grant, a space or mount with no GitHub repository, a repository whose `main` protection has `enforce_admins` off (`GET repos/{slug}/branches/main/protection/enforce_admins`, `enabled`). Exit 0 when nothing blocks.
  - `onboard.py --routines-cloud --sweep <trig_id> --daily <trig_id> --timezone <IANA>`: refuses (exit 2, one sentence) unless `runs.jsonl` on this checkout holds, within the last 48 hours (`PROOF_HOURS = 48`), a `cloud-proof` line for each chain with `guard`, `edit` and `tool` true, and `send` true for the daily. Then `routine.claim_cloud(...)` with the roots and the grant the render in `~/.spaces/cloud-<chain>.json` recorded.
  - The protection onboarding sets on a new space's `main` includes `enforce_admins: true`.
  - `onboard.py --routines-here` with a cloud record: one sentence asks the agent to disable both tasks first (it names their ids), then claims the host for this computer as today.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_move_is_refused_without_both_proofs(tmp_path, capsys):
    assert onboard.main(["--routines-cloud", "--sweep", "trig_01AAAAAAAA", "--daily", "trig_01BBBBBBBB",
                         "--timezone", "Europe/Amsterdam", str(tmp_path)]) == 2
def test_a_proof_older_than_two_days_does_not_count(tmp_path): ...
def test_a_daily_proof_without_the_send_refusal_does_not_count(tmp_path): ...
def test_two_green_proofs_claim_the_cloud(tmp_path): ...
def test_the_check_names_a_repository_without_enforce_admins(tmp_path, monkeypatch): ...
def test_the_check_names_unread_history(tmp_path): ...
def test_new_spaces_get_enforce_admins(tmp_path, monkeypatch): ...
def test_taking_the_routines_back_names_the_tasks_to_disable_first(tmp_path, capsys): ...
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest _meta/tools/tests/test_onboard.py -q -k "cloud or enforce"`
Expected: FAIL

- [ ] **Step 3: Implement** with the interfaces above; the onboard skill gains the four steps of spec 4.10 in its own voice.

- [ ] **Step 4: Run the onboard and start tests**

Run: `python -m pytest _meta/tools/tests/test_onboard.py _meta/tools/tests/test_start.py _meta/tools/tests/test_backfill.py -q`
Expected: PASS

Tree message: `feat: "run my routines in the cloud" moves them only after two proofs, and protects main for admins too`

---

### Task 13: Rules, decisions and docs

**Files:**
- Modify: `rules.md` (rule 7), `_meta/tools/agents_md.py:81` (the generated sentence), `AGENTS.md` (regenerated by the tool)
- Modify: the decisions register the template keeps (`knowledge/decisions/` or the file the existing decision rows live in), `docs/troubleshooting.md`, `CHANGELOG.md`, `.claude/skills/sync/SKILL.md`, `.claude/skills/sweep/SKILL.md`
- Modify: `docs/design/2026-10-27-cloud-routines-design.md` (status line: implemented on `feat/cloud-routines`, not yet proven live)
- Test: `_meta/tools/tests/test_agents_md.py`

**Interfaces:**
- Produces (spec 5):
  - Rule 7 reads exactly: "Agents edit files. The sync script commits: on the computer every 15 minutes, and once at the end of a cloud routine run. The platform merges. No session runs a git write itself."
  - `agents_md.py` renders the same sentence; `agents_md.py . --check` passes.
  - One decision row each, dated 2026-10-27: chains may run in the cloud by the person's choice; the cloud lands through `sync.py --cloud`; REST only in any path a cloud run reaches; transcripts and template sync run in the computer's sync; `runner: cloud`; the profile lives in the task and the `spaces-guard` plugin; merges resolve by file kind and never wedge; the cloud never lands a protected change; `enforce_admins` on.
  - Troubleshooting: "a cloud run did not land" (read its run line on `main`; a `cloud/<chain>` pull request left open; the computer adopts it after 30 minutes), "the cloud sweep has not run since …", "a cloud run changed protected files".
  - The sync skill: the cloud mode and the computer's leg, and how to read their run lines.

- [ ] **Step 1: Write the failing test**

```python
def test_rule_seven_names_the_cloud_landing(template_root):
    assert "once at the end of a cloud routine run" in (template_root / "rules.md").read_text(encoding="utf-8")
    assert agents_md.main([str(template_root), "--check"]) == 0
```

- [ ] **Step 2: Run it to see it fail**

Run: `python -m pytest _meta/tools/tests/test_agents_md.py -q`
Expected: FAIL

- [ ] **Step 3: Edit the docs, then regenerate `AGENTS.md`** with `python _meta/tools/agents_md.py .`

- [ ] **Step 4: Run the docs checks**

Run: `python -m pytest _meta/tools/tests/test_agents_md.py -q` then `python _meta/tools/validate.py . --template` then `python _meta/tools/no_company_names.py .`
Expected: PASS, and both checks clean

Tree message: `docs: rule 7, the decisions and the troubleshooting for cloud routines`

---

### Task 14: `sync.py --cloud <chain>`

**Files:**
- Modify: `_meta/tools/sync.py` (`parse`, `main`, `Run.stage`, `Run.publish`, new `Run.cloud_land`, `Run.wait_landed`, `Run.hold_failed` factored out of `heal`)
- Test: `_meta/tools/tests/test_sync_cloud.py` (new)

**Interfaces:**
- Consumes: `ghrest.create_pull`, `ghrest.open_pulls`, `ghrest.pull_state`, `ghrest.failed_checks` (Task 1); `Run.open_pr` (Task 2); `carry`, `Run.resolve`, the merge rules, `<git dir>/sync-cloud-lease-<chain>` (Task 4); `gate.protected_prefixes` with the minimum (Task 15); `routine.PROTECTED_EDITS` (Task 5).
- Produces (spec 4.3):
  - CLI `--cloud {sweep,daily}`; refused together with `--release-leg` or `--only` (argparse error); `--wait-minutes N` (default 9).
  - Order of a cloud run: lock → this run's own line written to `runs.jsonl` when there are candidates (`{"routine": "sync", "cloud": <chain>, "staged": n, "carried": bool, "held": [...], "left_out": k}`) → `land_structure` → stage (min age 0; private zones, submodules and nested checkouts never; protected paths left out: `AGENTS.md` put back quietly with `git checkout -- AGENTS.md`, any other protected path left unstaged and counted, then one item in `_inbound/needs-you/sync-cloud.yaml`: `id: cloud-protected-<chain>`, kind `security`, text "A cloud run changed <k> protected files; they were not landed.", recommendation "look at what changed in the cloud run's log before you run it again", default "nothing protected lands from the cloud") → secret scan: in the cloud each hit line is replaced by `[a line held back: <kind>]` and the file staged; a file over 1 MB (`CLOUD_MAX_BYTES = 1_000_000`) or not text is left out and named in the line → commit → `merge_in` with the rules → `set_aside_tests` → publish → pull request → wait → unlock.
  - Publish: `git push --force-with-lease=refs/heads/cloud/<chain>:<lease> origin HEAD:refs/heads/cloud/<chain>`, where `<lease>` is the sha in the lease file (an empty lease when the file says none: the branch must not exist). A rejected lease runs `carry` once more and pushes once more; a second rejection leaves it for the next run and says so.
  - Quiet runs: when every staged path is one of `_meta/state/runs.jsonl`, `_meta/state/routine-limits.json`, `_meta/state/stale.yaml` or matches `_meta/state/*-watermark.yaml` (`CLOUD_QUIET`), the branch is pushed and no pull request opens, unless the oldest commit on `cloud/<chain>` not on `origin/main` is older than six hours (`QUIET_HOURS`, today's constant).
  - `Run.wait_landed(pr: int, minutes: int) -> str`: polls `ghrest.pull_state` every 20 seconds until `merged` (returns `"merged"`), or the time is up (`"left_open"`). On `mergeable_state == "dirty"`: fetch `main`, `merge_in` with the rules, push with the lease, at most three times (`CLOUD_REMERGE = 3`; past that `"conflict"`). On failed checks: `self.hold_failed(pr, head_sha)` once (the computer's repair: answer tests the failed log names are set aside, the branch pushed again); a second red returns `"red"`.
  - `Run.hold_failed(pr: int, head_sha: str) -> bool`: the body of today's `heal` for one pull request, shared by `heal` and `wait_landed`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_cloud_run_lands_on_its_chain_branch_through_one_rest_pull_request(repo, tmp_path, monkeypatch):
    # FakeGh records every call; no "pr" subcommand; branch cloud/sweep; POST repos/.../pulls once
def test_its_own_line_lands_with_it(repo, tmp_path, monkeypatch): ...
def test_new_files_need_no_minimum_age_in_the_cloud(repo, tmp_path, monkeypatch): ...
def test_a_protected_change_never_lands_from_the_cloud(repo, tmp_path, monkeypatch):
    # rules.md and _meta/tools/x.py edited -> not in the commit; one security item; AGENTS.md restored silently
def test_a_held_secret_line_is_replaced_and_the_page_lands(repo, tmp_path, monkeypatch): ...
def test_a_large_file_is_named_not_landed(repo, tmp_path, monkeypatch): ...
def test_a_quiet_run_pushes_without_a_pull_request(repo, tmp_path, monkeypatch): ...
def test_a_quiet_branch_older_than_six_hours_opens_its_pull_request(repo, tmp_path, monkeypatch): ...
def test_the_push_is_leased_to_the_head_begin_saw(repo, tmp_path, monkeypatch):
    # another clone pushed cloud/sweep after begin -> lease rejected -> carry -> push once more -> both runs' files on the branch
def test_two_chains_landing_at_once_remerge_main_and_land(repo, tmp_path, monkeypatch):
    # pull_state: dirty, then clean, then merged -> wait_landed == "merged" after one re-merge
def test_red_checks_get_one_repair_then_are_left_open(repo, tmp_path, monkeypatch): ...
def test_a_pull_request_not_merged_in_time_is_left_open(repo, tmp_path, monkeypatch): ...
def test_cloud_excludes_the_release_leg(capsys):
    with pytest.raises(SystemExit):
        sync.parse(["--cloud", "sweep", "--release-leg"])
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest _meta/tools/tests/test_sync_cloud.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** with the interfaces above. The sleep in `wait_landed` goes through one module function (`_sleep`) the tests patch. The sync docstring gains one paragraph on the cloud mode.

- [ ] **Step 4: Run every sync test**

Run: `python -m pytest _meta/tools/tests/test_sync_cloud.py _meta/tools/tests/test_sync.py _meta/tools/tests/test_sync_merge.py _meta/tools/tests/test_ghrest.py -q`
Expected: PASS

Tree message: `feat: sync.py --cloud lands a cloud run once, leased, without a protected change, and waits for the merge`

---

### Task 15: The gate's minimum protected list

**Files:**
- Modify: `_meta/tools/gate.py` (`protected_prefixes`, new `MINIMUM_PROTECTED`)
- Modify: `_meta/tools/spaceyaml.py` (`DEFAULTS["protected"]`), `space.yaml` (the template's own list)
- Test: `_meta/tools/tests/test_gate.py`

**Interfaces:**
- Produces (spec 4.7 layer 4):
  - `gate.MINIMUM_PROTECTED = ("identity/", "rules.md", "space.yaml", ".claude/", "_meta/tools/", ".github/", "CLAUDE.md", "AGENTS.md", ".gitignore", ".gitattributes", ".gitmodules", "_meta/templates/", "_meta/plugins/", ".claude-plugin/")`.
  - `gate.protected_prefixes(root) -> tuple`: the space's own list, then every entry of `MINIMUM_PROTECTED` it lacks, in that order, no duplicates. A `space.yaml` that names fewer can never shrink the minimum.
  - `spaceyaml.DEFAULTS["protected"]` and the template's `space.yaml` list the same entries.
  - Settled after review (spec 4.7 layer 4): `Run.split` holds a new or re-addressed mount's gitlink back with `.gitmodules`, and the gate counts an `AGENTS.md` that equals `agents_md.py`'s render of the same checkout as part of a template release.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_short_protected_list_still_guards_the_minimum(tmp_path):
    write_space_yaml(tmp_path, protected=["rules.md"])
    assert set(gate.MINIMUM_PROTECTED) <= set(gate.protected_prefixes(tmp_path))
def test_the_spaces_own_entries_come_first(tmp_path): ...
def test_a_change_to_the_gitignore_is_protected(tmp_path): ...
def test_the_template_space_yaml_lists_the_minimum(template_root): ...
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest _meta/tools/tests/test_gate.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** with the interfaces above.

- [ ] **Step 4: Run the gate, sync and validate tests**

Run: `python -m pytest _meta/tools/tests/test_gate.py _meta/tools/tests/test_sync.py _meta/tools/tests/test_validate.py -q`
Expected: PASS

Tree message: `feat: the gate always protects the machinery, whatever space.yaml says`

---

### Task 16: The `spaces-guard` plugin

**Files:**
- Create: `.claude-plugin/marketplace.json`
- Create: `_meta/plugins/spaces-guard/.claude-plugin/plugin.json`, `_meta/plugins/spaces-guard/hooks/hooks.json`, `_meta/plugins/spaces-guard/guard.py`
- Modify: `_meta/tools/template_sync.py` (the new paths are machinery it carries)
- Test: `_meta/tools/tests/test_guard_plugin.py` (new), `_meta/tools/tests/test_template_sync.py`

**Interfaces:**
- Consumes: the workspace's `bash_guard.py` and `send_guard.py` and their decision format; the connector and task matchers `routine.routine_settings` writes today (`TASK_MATCHER`, `CONNECTOR_MATCHER`); the state file `~/.spaces/routines/cloud.json` (Task 10).
- Produces (spec 4.7 layer 1):
  - `marketplace.json`: `{"name": "spaces", "owner": {"name": "spaces"}, "plugins": [{"name": "spaces-guard", "source": "./_meta/plugins/spaces-guard", "description": "..."}]}`.
  - `plugin.json`: name `spaces-guard`, a version, a one-line description.
  - `hooks.json`: `PreToolUse` entries for `Bash`, the task matcher, the connector matcher and the send tools' matcher, each running `python3 -I "${CLAUDE_PLUGIN_ROOT}/guard.py" <bash|task|connector|send>`.
  - `guard.py <mode>` (stdlib only; reads the hook's JSON on stdin; answers in the hook decision format the workspace guards already use):
    - `CLAUDE_CODE_REMOTE` is not `true`: allow everything (a computer session that enabled the plugin is unchanged);
    - no state file: allow only a Bash command matching `^python3 /home/user/[^/\s]+/_meta/tools/cloud\.py begin /home/user/[^/\s]+ --chain (sweep|daily)( --proof)?$`; deny everything else;
    - otherwise: delegate to the workspace's guard (`bash_guard.py` for `bash`, `send_guard.py` for `send`, the profile's own rules for `task` and `connector`) with the profile path from the state file, as the computer's hooks run them;
    - any error (unreadable state, a guard that crashed, a timeout of 20 s): deny, with the reason.
  - `template_sync` carries `.claude-plugin/` and `_meta/plugins/` like the other machinery.

- [ ] **Step 1: Write the failing tests**

```python
def test_outside_the_cloud_everything_passes(tmp_path):
    assert decide("bash", {"command": "rm -rf /"}, env={}) == "allow"
def test_before_begin_only_begin_runs(tmp_path):
    env = {"CLAUDE_CODE_REMOTE": "true", "HOME": str(tmp_path)}
    assert decide("bash", {"command": "python3 /home/user/ada-space/_meta/tools/cloud.py begin /home/user/ada-space --chain sweep"}, env) == "allow"
    assert decide("bash", {"command": 'python3 -c "print(1)"'}, env) == "deny"
def test_after_begin_the_computers_allow_list_holds(tmp_path): ...
def test_an_ungranted_connector_tool_is_denied_by_name(tmp_path): ...
def test_a_send_to_another_address_is_denied(tmp_path): ...
def test_a_guard_that_crashes_denies(tmp_path): ...
def test_the_hooks_file_points_every_mode_at_the_guard(template_root): ...
def test_the_marketplace_offers_the_guard(template_root): ...
def test_template_sync_carries_the_plugin(tmp_path): ...
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest _meta/tools/tests/test_guard_plugin.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** with the interfaces above. `guard.py` keeps no state of its own and writes nothing.

- [ ] **Step 4: Run the guard, permission and template sync tests**

Run: `python -m pytest _meta/tools/tests/test_guard_plugin.py _meta/tools/tests/test_permissions.py _meta/tools/tests/test_template_sync.py -q`
Expected: PASS

Tree message: `feat: the spaces-guard plugin carries the computer's routine hooks into a cloud run`

---

### Task 17: Unsure ids and unread attachments

**Files:**
- Modify: `_meta/tools/unsure.py` (the content-free ids file), `_meta/tools/sweep.py` (`--attachment-unread`, `--attachments`), `_meta/tools/morning_view.py` (the two counts)
- Test: `_meta/tools/tests/test_unsure.py`, `_meta/tools/tests/test_sweep.py`, `_meta/tools/tests/test_morning_view.py`

**Interfaces:**
- Produces (spec 4.8):
  - Whenever `unsure.py` keeps a record in `_meta/state/private/`, it also appends `{"id", "source", "at"}` (no content, no subject, no sender) to `_meta/state/unsure-ids.jsonl`, once per id.
  - `sweep.py [root] --attachment-unread <record_id> <name>` appends `{"id", "name", "at"}` to `_meta/state/attachments-unread.jsonl`, once per pair; `sweep.py [root] --attachments` prints the pairs not yet read (a pair is read once a witness page for that record carries that attachment's name); `--attachment-read <record_id> <name>` marks one read.
  - The morning view's counts gain "records the sweep was unsure of" (lines in `unsure-ids.jsonl` newer than the last briefing) and "attachments not yet read" (`--attachments` count). Counts only, as the payload is today.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_unsure_record_leaves_only_its_id_in_the_repository(tmp_path):
    unsure.keep(tmp_path, {"id": "gmail-1a2b", "source": "mail", "subject": "Secret deal", "body": "..."})
    line = (tmp_path / "_meta/state/unsure-ids.jsonl").read_text()
    assert "gmail-1a2b" in line and "Secret" not in line
def test_an_unread_attachment_is_listed_until_read(tmp_path): ...
def test_the_briefing_counts_unsure_records_and_unread_attachments(tmp_path): ...
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest _meta/tools/tests/test_unsure.py _meta/tools/tests/test_sweep.py _meta/tools/tests/test_morning_view.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** with the interfaces above (use the names `unsure.py` already has for keeping a record; the test's `keep` stands for it).

- [ ] **Step 4: Run them to see them pass**

Run: `python -m pytest _meta/tools/tests/test_unsure.py _meta/tools/tests/test_sweep.py _meta/tools/tests/test_morning_view.py -q`
Expected: PASS

Tree message: `feat: a cloud run keeps the ids of unsure records and unread attachments, never their content`

---

### Task 18: End to end, and the full checks

**Files:**
- Test: `_meta/tools/tests/test_cloud_e2e.py` (new)

**Interfaces:**
- Consumes: everything above.
- Produces: one test that runs a cloud sweep and a cloud daily against two throwaway bare origins (a workspace and a mounted brain) in a fake `/home/user` (`SPACES_CLOUD_HOME`), with a fake `gh` that keeps pull requests in memory and merges them when asked:
  1. `cloud.py begin --chain sweep` (record says `runner: cloud`), carry finds nothing;
  2. the chain tools with `--cloud` on a small feed;
  3. `sync.py --cloud sweep` pushes `cloud/sweep`, opens one pull request through REST; the fake merges it;
  4. the daily does the same while a second sweep landed meanwhile: the daily's pull request goes dirty, is re-merged with the rules and lands;
  5. a third run whose pull request is never merged: the next `begin` carries it, and the computer's sync (runner cloud) adopts it after 30 minutes;
  6. at the end, `main` holds every run line, no witness page twice, the watermark at the later mark, nothing under `_meta/state/feed/`, and no protected file changed.

- [ ] **Step 1: Write the test** with the steps above as assertions.

- [ ] **Step 2: Run it**

Run: `python -m pytest _meta/tools/tests/test_cloud_e2e.py -q`
Expected: PASS (fix the task whose code fails it; the fix goes into that task's tree)

- [ ] **Step 3: Run the full checks** from the repository root, with `GIT_CONFIG_GLOBAL` pointing at an empty file:

```bash
python -m pytest _meta/tools/tests -q
python _meta/tools/validate.py .
python _meta/tools/validate.py . --template
python _meta/tools/scan.py --changed-since origin/main
python _meta/tools/no_company_names.py .
python _meta/tools/agents_md.py . --check
python _meta/tools/behaviour.py . --all
```

Expected: every test passes (the baseline 2500 plus the new ones, 15 skipped); every check prints clean.

Tree message: `test: a cloud sweep and daily end to end against throwaway origins`
