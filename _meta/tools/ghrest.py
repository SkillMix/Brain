"""ghrest: the pull request calls to GitHub, through REST (`gh api`), never GraphQL.

A cloud run refuses GraphQL, and every `gh pr ...` command is GraphQL, so each pull request call
a cloud run can reach goes through here, and a computer makes the same calls
(docs/design/2026-10-27-cloud-routines-design.md, section 4.4). The calls go to gh's default host
and read check runs, not the older commit statuses, which fits a space on github.com whose checks
run on GitHub Actions, as the template's do. Every function takes the `gh` to call: `runner(cwd)`
is the real one, and a test passes its own. A `gh` returns the finished process, or None when gh
is not installed. A reader returns None, never an empty list, when GitHub could not be asked or
its answer could not be read, so a caller can tell "nothing is open" from "nobody could look".

repo_slug(root) -> "owner/name" | None
    The last two path segments of origin's URL, `.git` dropped: one answer for the ssh form, the
    https form and a cloud run's proxy (`http://local_proxy@127.0.0.1:34567/git/<owner>/<name>`).
    None without an origin, or when either segment is missing, is not a plain ASCII name or is
    only dots (`../origin.git`). Any other local path still gives its last two segments
    (`/srv/ada/ada-space.git` gives `ada/ada-space`).
runner(cwd) -> gh
    The real `gh`, run in `cwd`. None when gh itself is not installed; a call that runs out of
    time, or cannot start for another reason (its working folder gone, say), is a failed call (a
    non-zero exit), never None.
open_pulls(gh, slug, head=None) -> list[dict] | None
    The open pull requests, at most 100 (only the branch `head` of this repository when given),
    each as `number, head_ref, head_sha, title, url, draft, auto_merge, cross_repo, created_at`.
    `cross_repo` is True for a fork, and for a pull request whose head repository is gone or
    cannot be read: such a one is never offered for merging.
create_pull(gh, slug, head, title, body) -> int | None
    Opens a pull request from `head` into main; its number. Every field is sent as a string.
pull_state(gh, slug, number) -> dict | None
    `merged, state, mergeable_state, head_sha` of one pull request. `mergeable_state` is GitHub's
    string as given (`unknown` while GitHub is still working it out, and for a merged pull
    request); None only when the answer has none.
failed_checks(gh, slug, sha) -> list[dict] | None
    The check runs on a commit whose conclusion is one of `FAILED`, as `name, link` (the run's
    details page, else its page on GitHub).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable
from urllib.parse import quote

FAILED = ("failure", "timed_out", "cancelled", "action_required", "startup_failure")
# A cloud run's answer for a repository its task did not attach: a host it could not ask, never "gone".
NOT_ENABLED = re.compile(r"is not enabled", re.I)
TIMEOUT_SECONDS = 60
SEGMENT = re.compile(r"(?!\.+$)[A-Za-z0-9_.-]+")


def repo_slug(root: Path) -> str | None:
    try:
        done = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(root), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    parts = [p for p in re.split(r"[/:\\]", (done.stdout or "").strip()) if p]
    if len(parts) < 2:
        return None
    owner, name = parts[-2], parts[-1].removesuffix(".git")
    if not (SEGMENT.fullmatch(owner) and SEGMENT.fullmatch(name)):
        return None
    return f"{owner}/{name}"


def runner(cwd: Path) -> Callable:
    def gh(*args: str) -> subprocess.CompletedProcess | None:
        argv = ["gh", *args]
        try:
            return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(argv, 1, "", f"gh timed out after {TIMEOUT_SECONDS} s")
        except OSError as exc:
            # gh itself is missing when the error names gh (POSIX) or no file at all (Windows);
            # POSIX names the working folder when that is what is gone.
            if isinstance(exc, FileNotFoundError) and exc.filename in (None, argv[0]):
                return None
            return subprocess.CompletedProcess(argv, 1, "", f"gh could not run ({type(exc).__name__})")
    return gh


def _answer(gh: Callable, *args: str):
    """The JSON one `gh api` call printed; None when gh is missing, the call failed, or it printed
    no JSON."""
    done = gh(*args)
    if done is None or done.returncode != 0:
        return None
    try:
        return json.loads(done.stdout or "")
    except ValueError:
        return None


def _number(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _pull(raw) -> dict | None:
    """One pull request of the REST list as plain fields; None for an entry without a number."""
    if not isinstance(raw, dict) or (number := _number(raw.get("number"))) is None:
        return None
    head, base = _dict(raw.get("head")), _dict(raw.get("base"))
    head_repo = str(_dict(head.get("repo")).get("full_name") or "").lower()
    base_repo = str(_dict(base.get("repo")).get("full_name") or "").lower()
    return {"number": number, "head_ref": str(head.get("ref") or ""), "head_sha": str(head.get("sha") or ""),
            "title": str(raw.get("title") or ""), "url": str(raw.get("html_url") or ""),
            "draft": bool(raw.get("draft")), "auto_merge": bool(raw.get("auto_merge")),
            "cross_repo": not head_repo or head_repo != base_repo,
            "created_at": str(raw.get("created_at") or "")}


def open_pulls(gh: Callable, slug: str, head: str | None = None) -> list[dict] | None:
    route = f"repos/{slug}/pulls?state=open&per_page=100"
    if head:
        route += "&head=" + quote(f"{slug.split('/')[0]}:{head}", safe="/:")
    listed = _answer(gh, "api", route)
    if not isinstance(listed, list):
        return None
    return [pull for pull in map(_pull, listed) if pull]


def create_pull(gh: Callable, slug: str, head: str, title: str, body: str) -> int | None:
    created = _answer(gh, "api", "-X", "POST", f"repos/{slug}/pulls", "-f", f"title={title}",
                      "-f", f"head={head}", "-f", "base=main", "-f", f"body={body}")
    return _number(_dict(created).get("number"))


def pull_state(gh: Callable, slug: str, number: int) -> dict | None:
    raw = _answer(gh, "api", f"repos/{slug}/pulls/{int(number)}")
    if not isinstance(raw, dict) or not isinstance(raw.get("state"), str):
        return None
    mergeable = raw.get("mergeable_state")
    return {"merged": bool(raw.get("merged")), "state": raw["state"],
            "mergeable_state": mergeable if isinstance(mergeable, str) else None,
            "head_sha": str(_dict(raw.get("head")).get("sha") or "")}


def failed_checks(gh: Callable, slug: str, sha: str) -> list[dict] | None:
    runs = _dict(_answer(gh, "api", f"repos/{slug}/commits/{sha}/check-runs?per_page=100")).get("check_runs")
    if not isinstance(runs, list):
        return None
    return [{"name": str(run.get("name") or ""), "link": str(run.get("details_url") or run.get("html_url") or "")}
            for run in runs if isinstance(run, dict) and run.get("conclusion") in FAILED]
