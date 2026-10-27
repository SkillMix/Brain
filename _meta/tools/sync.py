"""sync: land work without human hands (vision section 12, landing design section 3).

The one tool that runs git. The OS scheduler runs it every 15 minutes; sessions and
routines only edit files. Each run:

  1. take `.git/sync.lock`; skip if another run holds it or git is mid-operation
  1a. name in stale.yaml each chain whose scheduler stopped starting it (`not_started`)
  2. stage tracked changes and settled untracked text files, never under personal/,
     academic/, _scratch/, _meta/state/private/ or inside a mount
  3. scan the staged paths; unstage every file with a finding (count recorded, never
     the match); commit `chore: sync`
  4. advance the pin of every clean mount to its remote branch; commit `chore: pins`
  5. fetch origin and merge origin/main in (append-only files by union, and the record of which
     computer runs the routines: `MERGE_RULES` is written into the space's own git first, so a
     space whose .gitattributes is older has it too); a conflict
     where origin holds only an earlier copy of this checkout's own change keeps ours
     (the rebuilt commits of step 6 share no history with it); any other conflict is
     aborted and recorded
  5a. when there is something to publish: run the answer tests; a question that fails is set
     aside (`held: {since, by: sync, missing}` in answers.yaml, named in the run line) so it
     cannot turn the pull request red and hold every other change back, and one the sync set
     aside that passes again comes back; commit `chore: sync`
  5b. read this machine's open sync pull request: when its checks failed, set aside the answer
     tests the failed log names (`by: ci`) and commit, or else rerun the failed run once per
     head; a repair is recorded `healed` once the pull request merges (the briefing's "Fixed by
     itself"), and what stays red after a rerun is left in the run line as `pr_red`
  6. when local main differs from origin/main: push it to sync/<machine> (a hash, never the
     machine's name) and open the pull request the platform merges. Changes under a
     `protected:` path go to their own branch sync/<machine>-protected (their own pull
     request, the owner's one click), so the journal, desk and run lines never wait behind them; each branch is rebuilt on
     origin/main and pushed only when its content changed (main moving under a waiting pull
     request is no reason to rebuild it: every push reruns the checks). A change that is
     only run lines and mount pins goes out at most every QUIET_HOURS, one that is only
     journal lines and run logs at most every BATCH_HOURS, or with the next real change, so
     nobody watching the repository gets a pull request (and a paid check) every 15 minutes
  7. append one run line to _meta/state/runs.jsonl when something happened, plus at
     most one heartbeat a day; the line rides along in the next run's commit
  8. after the lock is released: rewrite the progress page (`~/.spaces/onboarding.html`, outside the
     repository) from the workspace, without the model (`onboarding_page.refresh`); not for a dry
     run or `--only`

    python sync.py [--root R] [--dry-run] [--no-push] [--release-leg]
                   [--min-age-minutes N] [--only PATH ...] [--message TEXT]

`--only` lands exactly the changes under the given paths as one commit (`chore: structure`
unless `--message` says otherwise), whatever their age: the librarian's one commit per
structural change. Pins and the release leg wait for the next ordinary run.

`--release-leg` runs `release.ship(root)` first when that module is present.
Exit 0 on success, 2 when the run was skipped, 1 on an error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import doctor  # noqa: E402
import pages  # noqa: E402
import scan  # noqa: E402
import spaceyaml  # noqa: E402

OK, ERROR, SKIPPED = 0, 1, 2
PRIVATE_ZONES = ("personal/", "academic/", "_scratch/", "_meta/state/private/")
IN_PROGRESS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "rebase-merge", "rebase-apply", "index.lock")
LOCK_HOURS = 0.5
QUIET_HOURS = 6   # run lines and mount pins alone go out at most this often
BATCH_HOURS = 1   # journal lines and run logs alone go out at most this often
RUNS_PATH = Path("_meta") / "state" / "runs.jsonl"
RUN_LOGS = {"_meta/state/tests.jsonl", "_meta/state/probe.jsonl"}
NOTABLE = ("committed", "pins", "conflict", "scan_hits", "errors", "held_tests", "restored_tests",
           "healing", "healed", "pr_red")
HEAL_STATE = "sync-heal.json"   # in the git dir: the repair waiting to be seen merged
HEAL_RERUNS = 3   # reruns of one sync pull request's failed checks a day, at most
CHAINS = {"sweep_chain": "sweep-chain", "daily": "daily-chain"}   # run line routine: its routine prompt


# Merged by union in every space, whatever its .gitattributes says: two computers that claim the routines before
# either synced leave a record that reads as unclear (nobody runs) instead of a conflict that wedges the sync.
MERGE_RULES = ("_meta/state/routine-host.yaml merge=union",)


def git(root, *args, check: bool = False, input: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", input=input, check=check,
                          env=dict(os.environ, **env) if env else None)


def hostname() -> str:
    """This machine's part of its sync branch names: stable per machine, never its name, which
    would put a computer's (often its person's) name into every company repository."""
    return "m-" + hashlib.sha256(socket.gethostname().lower().encode("utf-8")).hexdigest()[:8]


def git_dir(root: Path) -> Path | None:
    done = git(root, "rev-parse", "--git-dir")
    if done.returncode != 0:
        return None
    path = Path(done.stdout.strip())
    return path if path.is_absolute() else root / path


def in_progress(gdir: Path) -> str | None:
    return next((name for name in IN_PROGRESS if (gdir / name).exists()), None)


def not_started(root: Path, now: dt.datetime | None = None) -> bool:
    """A chain its agent scheduler never starts writes nothing, so only this script, which the OS
    runs whatever that scheduler does, can notice. A chain whose last run line (for one that never
    ran, the onboarding) is older than twice the longest gap in its routine's schedule is named
    in stale.yaml as `<routine> not started` since then, for the health line; the entry goes when
    the chain runs. Written only when it changes. True when it wrote."""
    import chain
    import onboard
    import onboarding_page
    import yaml
    space = spaceyaml.load(root)
    now = now or space.now()
    last: dict = {}
    path = root / RUNS_PATH
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []:
        try:
            row = json.loads(raw)
            if row.get("routine") in CHAINS:
                at = pages.parse_iso(row["at"])
                last[row["routine"]] = max(at, last.get(row["routine"], at))
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    try:   # a space no onboarding made was promised no schedule
        onboarded = pages.parse_iso(onboard.read_yaml(root / "_meta" / "state" / "onboard.yaml", {})["at"])
    except (KeyError, TypeError, ValueError, OSError, yaml.YAMLError):
        onboarded = None
    schedules = dict(onboarding_page._routines(root))
    stale_path = root / chain.STALE
    data = (yaml.safe_load(stale_path.read_text(encoding="utf-8")) or {}) if stale_path.exists() else {}
    want = dict(data)
    for routine, prompt in CHAINS.items():
        key, since = chain.NOT_STARTED.format(routine), last.get(routine) or onboarded
        times = [h * 60 + m for h, m in onboard.daily_times(schedules.get(prompt, "")) or []]
        # the longest wait between two starts, the night included: once a day waits 24 hours
        gap = max(b - a for a, b in zip(times, times[1:] + [times[0] + 24 * 60])) if times else 0
        if since is not None and gap and now - since > space.scale(2 * gap / 60):
            want[key] = pages.iso(since)
        else:
            want.pop(key, None)
    if want == data:
        return False
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    stale_path.write_text(yaml.safe_dump(want, sort_keys=True), encoding="utf-8", newline="\n")
    return True


class Run:
    def __init__(self, root: Path, gdir: Path, args) -> None:
        self.root, self.gdir, self.args = root, gdir, args
        self.space = spaceyaml.load(root)
        self.now = self.space.now()
        self.min_age = self.space.scale(float(args.min_age_minutes) / 60)
        self.notes: dict = {}
        self.dry = bool(args.dry_run)

    # ----- lock ------------------------------------------------------------
    def lock(self) -> bool:
        path = self.gdir / "sync.lock"
        try:
            held = json.loads(path.read_text(encoding="utf-8"))
            fresh = self.now - pages.parse_iso(held["at"]) < self.space.scale(LOCK_HOURS)
        except (OSError, ValueError, KeyError, TypeError):
            fresh = False
        if fresh:
            return False
        path.unlink(missing_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "at": pages.iso(self.now)}, fh)
        return True

    def unlock(self) -> None:
        (self.gdir / "sync.lock").unlink(missing_ok=True)

    def error(self, text: str) -> None:
        self.notes.setdefault("errors", []).append(text)
        print(f"sync: {text}")

    # ----- chains --------------------------------------------------------------
    def watch_chains(self) -> None:
        try:
            not_started(self.root, self.now)
        except Exception as exc:  # never in the way of landing
            self.error(f"not started check: {type(exc).__name__}: {str(exc)[:200]}")

    # ----- release leg -------------------------------------------------------
    def release_leg(self) -> None:
        try:
            module = importlib.import_module("release")
        except ImportError:
            self.notes["release"] = "absent"
            print("release: module absent, leg skipped")
            return
        if self.dry:
            print("would run release.ship")
            return
        self.refresh_corridors()
        try:  # the channel step: land answers that arrived, answer requests from standing choices
            request = importlib.import_module("request")
            request.collect(self.root)
            request.answer(self.root)
        except ImportError:
            pass
        except Exception as exc:
            self.error(f"channel step failed: {type(exc).__name__}")
        try:
            result = module.ship(self.root)
        except Exception as exc:  # the landing must still happen
            self.error(f"release failed: {type(exc).__name__}")
            return
        self.notes["release"] = result if isinstance(result, (dict, list, int, str, bool)) else True

    def refresh_corridors(self) -> None:
        """Bring each corridor mount (a `channel` space) to its remote tip before the channel
        step reads it; the pin itself lands with the ordinary commit."""
        for mount in self.space.mounts:
            root = self.space.mount_root(str(mount.get("name", "")))
            if root is None:
                continue
            try:
                if spaceyaml.load(root).profile != "channel":
                    continue
            except Exception:
                continue
            here, mounted = self.root.resolve(), Path(root).resolve()
            rel = mounted.relative_to(here).as_posix() if mounted.is_relative_to(here) else None
            if rel and (self.root / rel / ".git").exists():
                git(self.root, "submodule", "update", "--remote", "--quiet", "--", rel)

    # ----- stage, scan, commit ----------------------------------------------
    def submodules(self) -> list[str]:
        if not (self.root / ".gitmodules").exists():
            return []
        done = git(self.root, "config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$")
        return [line.split(None, 1)[1].strip() for line in done.stdout.splitlines() if " " in line]

    def excluded(self, rel: str, subs: list[str]) -> bool:
        if rel.startswith(PRIVATE_ZONES):
            return True
        if any(rel == s or rel.startswith(s + "/") for s in subs):
            return True
        parent = Path(rel).parent
        while parent != Path("."):
            if (self.root / parent / ".git").exists():  # a nested checkout
                return True
            parent = parent.parent
        return False

    def settled_text(self, rel: str) -> str | None:
        """None when the untracked file may be staged, else why not."""
        path = self.root / rel
        if path.is_dir():
            return "directory"
        if scan.text_of(path) is None:
            return "binary or over 1 MB"
        age = self.now.timestamp() - path.stat().st_mtime
        if self.min_age.total_seconds() > 0 and age < self.min_age.total_seconds():
            return "changed less than %d minutes ago" % round(self.min_age.total_seconds() / 60)
        return None

    def wanted(self, rel: str) -> bool:
        only = getattr(self.args, "only", None) or []
        if not only:
            return True
        return any(o in (".", "") or rel == o.rstrip("/") or rel.startswith(o.rstrip("/") + "/") for o in only)

    def candidates(self) -> list[str]:
        subs = self.submodules()
        done = git(self.root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames", check=True)
        chosen: list[str] = []
        for item in done.stdout.split("\0"):
            if len(item) < 4:
                continue
            xy, rel = item[:2], item[3:]
            if rel.endswith("/") or xy == "!!" or self.excluded(rel, subs) or not self.wanted(rel):
                continue
            if xy == "??" and not getattr(self.args, "only", None):
                why = self.settled_text(rel)
                if why:
                    print(f"skip {rel}: {why}")
                    continue
            chosen.append(rel)
        return chosen

    def pathspec(self, *args: str, rels: list[str]) -> None:
        spec = "\0".join(f":(literal){rel}" for rel in rels)
        git(self.root, *args, "--pathspec-from-file=-", "--pathspec-file-nul", input=spec, check=True)

    def staged(self) -> list[str]:
        done = git(self.root, "diff", "--cached", "--name-only", "-z", "--diff-filter=d", check=True)
        return [p for p in done.stdout.split("\0") if p]

    def scan_staged(self) -> None:
        rels = self.staged()
        findings = scan.scan_paths([self.root / rel for rel in rels])
        if not findings:
            return
        hit = {rel for rel in rels if str(self.root / rel) in {f.path for f in findings}}
        for finding in findings:
            print(f"scan: {finding.kind} at {Path(finding.path).relative_to(self.root).as_posix()}:"
                  f"{finding.line_no}; left local")
        self.pathspec("reset", "-q", rels=sorted(hit))
        self.notes["scan_hits"] = len(findings)

    def commit(self, message: str) -> bool:
        if git(self.root, "diff", "--cached", "--quiet").returncode == 0:
            return False
        git(self.root, "commit", "-q", "-m", message, check=True)
        print(f"commit: {message}")
        return True

    def land_structure(self) -> None:
        """Each change the librarian applied lands as its own commit `chore: structure`, before
        the ordinary commit, so readers never see half a move (vision section 13)."""
        changes = self.root / "_meta" / "state" / "librarian" / "changes.jsonl"
        landed_path = self.root / "_meta" / "state" / "librarian" / "landed.jsonl"
        if not changes.exists() or self.dry or getattr(self.args, "only", None):
            return
        landed = set()
        if landed_path.exists():
            for line in landed_path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    landed.add(json.loads(line).get("id"))
                except ValueError:
                    continue
        subs = self.submodules()
        count = 0
        for line in changes.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                change = json.loads(line)
            except ValueError:
                continue
            if not isinstance(change, dict) or change.get("id") in landed:
                continue
            paths = [str(p) for p in change.get("paths") or []]
            self.args.only = paths
            rels = self.candidates()
            self.args.only = None
            if rels:
                self.pathspec("add", "-A", rels=[r for r in rels if not self.excluded(r, subs)])
                self.scan_staged()
                self.commit("chore: structure")
                count += 1
            with landed_path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps({"id": change.get("id"), "at": pages.iso(self.now)}) + "\n")
        if count:
            self.notes["structure"] = count

    def stage_and_commit(self) -> None:
        rels = self.candidates()
        if self.dry:
            for rel in rels:
                print(f"would stage {rel}")
            hits = scan.scan_paths([self.root / rel for rel in rels])
            if hits:
                print(f"would leave {len({f.path for f in hits})} file(s) local after the scan")
            if rels:
                print("would commit: chore: sync")
            return
        if rels:
            self.pathspec("add", "-A", rels=rels)
        self.scan_staged()
        # a routine run's own line and its meter row alone are no commit worth a run line (R43)
        real = [rel for rel in self.staged() if rel not in (RUNS_PATH.as_posix(), "_meta/state/routine-limits.jsonl")]
        message = getattr(self.args, "message", None) or (
            "chore: structure" if getattr(self.args, "only", None) else "chore: sync")
        if self.commit(message) and real:
            self.notes["committed"] = True

    # ----- pins ----------------------------------------------------------------
    def advance_pins(self) -> None:
        moved = []
        for sub in self.submodules():
            if not (self.root / sub / ".git").exists():
                continue
            if git(self.root / sub, "status", "--porcelain").stdout.strip():
                print(f"pins: {sub} has local changes, left alone")
                continue
            if self.dry:
                print(f"would advance the pin of {sub}")
                continue
            before = git(self.root, "rev-parse", f"HEAD:{sub}").stdout.strip()
            done = git(self.root, "submodule", "update", "--remote", "--", sub)
            if done.returncode != 0:
                self.error(f"pins: update of {sub} failed")
                continue
            after = git(self.root / sub, "rev-parse", "HEAD").stdout.strip()
            if after != before:
                git(self.root, "add", "--", sub, check=True)
                moved.append(sub)
        if moved and self.commit("chore: pins"):
            self.notes["pins"] = moved

    # ----- merge in, publish -----------------------------------------------------
    def set_aside_tests(self) -> None:
        """A failing answer test turns the pull request red and holds every other change back:
        before publishing, set it aside with a record, and bring back one that passes again."""
        try:
            import tests_run
        except ImportError:
            return
        if not self.has_origin_main() or git(self.root, "diff", "--quiet", "origin/main", "HEAD").returncode == 0:
            return   # nothing to publish
        if self.dry:
            print("would set aside the answer tests that fail")
            return
        held, back = tests_run.set_aside(self.root, self.now)
        if not (held or back):
            return
        git(self.root, "add", "--", tests_run.ANSWERS_PATH.as_posix())
        self.commit("chore: sync")
        if held:
            self.notes["held_tests"] = held
        if back:
            self.notes["restored_tests"] = back
        print(f"answer tests: {len(held)} set aside, {len(back)} brought back")

    # ----- a red pull request heals itself ---------------------------------------
    def heal(self) -> None:
        """Read this checkout's sync pull request. When its checks failed: answer tests the
        failed log names are set aside for CI (`held: {by: ci}`, which only a person brings
        back, since a pass here does not prove a pass there) and land with this run's push; any
        other failure is rerun once per head, as a flaky one, unless the same checks failed on the
        head rerun before (a failure with a cause: the next push would only fail it again), and
        HEAL_RERUNS times a day at most. A repair counts once the pull
        request merges: the run line says `healed` and the briefing lists it. What is still red
        after a rerun stays in the run line (`pr_red`) for the maintenance agent, not the owner."""
        state = self.heal_state()
        if state.get("pr"):
            viewed = self.gh("pr", "view", str(state["pr"]), "--json", "state")
            if viewed is not None and viewed.returncode == 0 and \
                    json.loads(viewed.stdout or "{}").get("state") == "MERGED":
                self.notes["healed"] = {"what": state.get("what"), "pr": state["pr"]}
                state = self.heal_state({})
        listed = self.gh("pr", "list", "--head", f"sync/{hostname()}", "--state", "open",
                         "--json", "number,headRefOid")
        try:
            open_prs = json.loads(listed.stdout or "[]") if listed is not None and listed.returncode == 0 else []
            if not open_prs:
                return
            pr, head = open_prs[0]["number"], open_prs[0].get("headRefOid")
            checks = self.gh("pr", "checks", str(pr), "--json", "name,bucket,link")   # exits 1 when one failed
            failed = [c for c in json.loads(checks.stdout or "[]") if c.get("bucket") == "fail"] if checks else []
        except (ValueError, KeyError, TypeError, AttributeError):
            return
        if not failed:
            return
        runs = sorted({m.group(1) for c in failed for m in [re.search(r"/actions/runs/(\d+)", str(c.get("link")))] if m})
        logs = [self.gh("run", "view", run, "--log-failed") for run in runs]
        import tests_run
        held = tests_run.hold(self.root, tests_run.failures_in("".join(d.stdout for d in logs if d), self.root),
                              "ci", self.now)
        names, day = sorted({str(c.get("name")) for c in failed}), self.now.date().isoformat()
        mine = state.get("pr") == pr
        again = mine and state.get("failing") == names   # what failed after the last rerun failed once more
        today = int(state.get("count") or 0) if mine and state.get("day") == day else 0
        if held:
            git(self.root, "add", "--", tests_run.ANSWERS_PATH.as_posix())
            self.commit("chore: sync")
            self.notes["healing"] = {"pr": pr, "held_tests": held}
            self.heal_state({"pr": pr, "what": f"set aside {len(held)} answer test(s) the checks could not "
                                               f"pass: {'; '.join(held)}"})
        elif runs and state.get("rerun") != head and not again and today < HEAL_RERUNS:
            for run in runs:
                self.gh("run", "rerun", run, "--failed")
            self.notes["healing"] = {"pr": pr, "rerun": runs}
            self.heal_state({"pr": pr, "rerun": head, "failing": names, "day": day, "count": today + 1,
                             "what": "reran a failed check"})
        else:
            self.notes["pr_red"] = {"pr": pr, "checks": [c.get("name") for c in failed]}

    def heal_state(self, new: dict | None = None) -> dict:
        path = self.gdir / HEAL_STATE
        if new is None:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
        if new:
            path.write_text(json.dumps(new), encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
        return new

    def has_origin_main(self) -> bool:
        if "origin" not in git(self.root, "remote").stdout.split():
            return False
        return git(self.root, "rev-parse", "--verify", "-q", "origin/main").returncode == 0

    def merge_in(self) -> None:
        if "origin" not in git(self.root, "remote").stdout.split():
            self.notes["remote"] = "absent"
            return
        if self.dry:
            print("would fetch origin and merge origin/main")
            return
        self.merge_rules()
        if git(self.root, "fetch", "--quiet", "--prune", "--no-recurse-submodules", "origin").returncode != 0:
            self.error("fetch failed")
            return
        if not self.has_origin_main():
            return
        before = git(self.root, "rev-parse", "HEAD").stdout.strip()
        done = git(self.root, "merge", "--no-edit", "origin/main")
        if done.returncode != 0 and not self.keep_ours():
            git(self.root, "merge", "--abort")
            self.notes["conflict"] = True
            print("merge: conflict with origin/main; aborted, next run retries")
            return
        if git(self.root, "rev-parse", "HEAD").stdout.strip() != before:
            print("merge: origin/main brought in")

    def merge_rules(self) -> None:
        """The rules of `MERGE_RULES` in this space's own git (`info/attributes`, which outranks .gitattributes):
        each one missing is appended; a line already there is left as it is."""
        done = git(self.root, "rev-parse", "--git-path", "info/attributes")
        if done.returncode != 0:
            return
        path = Path(done.stdout.strip())
        path = path if path.is_absolute() else self.root / path
        try:
            # a byte that is not UTF-8 in the person's own lines stays as it is: this only appends
            text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
            have = {line.strip() for line in text.splitlines()}
            missing = [rule for rule in MERGE_RULES if rule not in have]
            if missing:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8", newline="\n") as fh:
                    fh.write(("\n" if text and not text.endswith("\n") else "") + "".join(f"{rule}\n" for rule in missing))
        except OSError as exc:
            self.error(f"merge rule not written: {type(exc).__name__}")

    def keep_ours(self) -> bool:
        """Finish a conflicted merge when every conflict is origin holding an earlier copy of this
        checkout's own change. A protected change waiting splits the push, so the rest lands as a
        rebuilt commit that shares no history with this checkout; a path changed here again since
        (above all a new mount's pin, which git cannot merge without a base) then conflicts with
        its own earlier copy. Ours wins only where origin's version is one this checkout held."""
        conflicts = [p for p in git(self.root, "diff", "--name-only", "--diff-filter=U", "-z").stdout.split("\0") if p]
        base = git(self.root, "merge-base", "HEAD", "MERGE_HEAD").stdout.strip()
        if not conflicts or not base:
            return False
        for path in conflicts:
            theirs = git(self.root, "rev-parse", "-q", "--verify", f"MERGE_HEAD:{path}").stdout.strip()
            held = git(self.root, "log", "--full-history", "--no-renames", "--raw", "--no-abbrev", "--format=",
                       f"{base}..HEAD", "--", path).stdout
            ours = git(self.root, "rev-parse", "-q", "--verify", f"HEAD:{path}").stdout.strip()
            if not theirs or not ours or theirs not in {line.split()[3] for line in held.splitlines()
                                                        if line.startswith(":")}:
                return False
        spec = "\0".join(f":(literal){rel}" for rel in conflicts)
        if git(self.root, "checkout", "HEAD", "--pathspec-from-file=-", "--pathspec-file-nul", input=spec).returncode \
                or git(self.root, "commit", "-q", "--no-edit").returncode:
            return False   # the caller aborts the merge: nothing is left half done
        self.notes["kept_ours"] = len(conflicts)
        print(f"merge: {len(conflicts)} path(s) where origin held an earlier copy of ours; kept ours")
        return True

    def split(self) -> tuple[list, list] | None:
        """The paths this checkout changes against origin/main, as (plain, protected); None
        when nothing is protected (or in the template, whose pull requests land on green). While
        origin/main still holds the template's own space.yaml (onboarding's first push never landed),
        every path is protected: the fill goes as one pull request, checked as one tree, merged once."""
        try:
            import gate
        except ImportError:
            return None
        if gate.is_template(self.root):
            return None
        # --no-renames: a move lists its old path too, so the subset commit deletes it
        out = git(self.root, "diff", "--name-only", "--no-renames", "-z", "origin/main", "HEAD").stdout
        changed = [p for p in out.split("\x00") if p]
        prefixes, _ = gate.protected_prefixes(self.root)
        managed = self.template_managed()   # a template update lands as one unit, with its tools
        shown = git(self.root, "show", "origin/main:space.yaml")
        unconfigured = shown.returncode != 0 or "profile: template" in shown.stdout
        guarded = [p for p in changed if unconfigured or p in managed
                   or any(gate._under(gate._posix(p), pre) for pre in prefixes)]
        if not guarded:
            return None
        return [p for p in changed if p not in guarded], guarded

    def template_managed(self) -> set:
        """Paths the template sync manages (its provenance file and every file it lists)."""
        prov = self.root / "_meta" / "state" / "template-version.yaml"
        try:
            import yaml
            files = (yaml.safe_load(prov.read_text(encoding="utf-8")) or {}).get("files") or {}
        except Exception:
            return set()
        return {"_meta/state/template-version.yaml", *(str(k) for k in files)}

    def subset_commit(self, paths: list, remote_ref: str) -> str | None:
        """A commit on top of origin/main that carries only `paths` as they are in HEAD, built in
        a private index so the working tree and the real index are never touched."""
        index = self.gdir / "sync-split.index"
        env = {"GIT_INDEX_FILE": str(index)}
        try:
            if git(self.root, "read-tree", "origin/main", env=env).returncode != 0:
                return None
            lines = []
            for path in paths:
                entry = git(self.root, "ls-tree", "-z", "HEAD", "--", path).stdout.strip("\x00")
                if entry:
                    meta, _, name = entry.partition("\t")
                    mode, _, sha = meta.split(" ")
                    lines.append(f"{mode} {sha}\t{name}")
                else:
                    lines.append(f"0 {'0' * 40}\t{path}")
            if git(self.root, "update-index", "-z", "--index-info", input="\x00".join(lines) + "\x00",
                   env=env).returncode != 0:
                return None
            tree = git(self.root, "write-tree", env=env).stdout.strip()
            base = git(self.root, "rev-parse", "origin/main").stdout.strip()
            remote = git(self.root, "rev-parse", "-q", "--verify", f"refs/remotes/{remote_ref}").stdout.strip()
            if remote:   # unchanged since the last run: keep the same commit, so CI does not rerun
                same_tree = git(self.root, "rev-parse", f"{remote}^{{tree}}").stdout.strip() == tree
                same_base = git(self.root, "rev-parse", f"{remote}^").stdout.strip() == base
                if (same_tree and same_base) or self.still_carries(remote, paths, base):
                    return remote
            done = git(self.root, "commit-tree", tree, "-p", base, "-m", "chore: sync")
            return done.stdout.strip() if done.returncode == 0 else None
        finally:
            index.unlink(missing_ok=True)

    def still_carries(self, commit: str, paths: list, base: str) -> bool:
        """The pushed commit carries exactly `paths`, as HEAD has them, and origin/main has not
        touched them since it was built: only main moved, so the pull request stays as it is."""
        def names(*spec: str) -> set | None:
            done = git(self.root, "diff", "--name-only", "--no-renames", "-z", *spec)
            return None if done.returncode else {p for p in done.stdout.split("\x00") if p}
        if git(self.root, "merge-base", "--is-ancestor", commit, base).returncode == 0:
            return False   # it merged already: what is left needs a new commit
        want = set(paths)
        carried, stale, moved = names(f"{commit}^", commit), names(commit, "HEAD"), names(f"{commit}^", base)
        if carried is None or stale is None or moved is None:
            return False
        return carried == want and not (stale & want) and not (moved & want)

    def push_branch(self, commit: str, branch: str) -> bool | None:
        """True when pushed, False when the branch already holds the commit, None on failure."""
        current = git(self.root, "rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}").stdout.strip()
        if current == commit:
            return False
        done = git(self.root, "push", "--quiet", "--force", "origin", f"{commit}:refs/heads/{branch}")
        return True if done.returncode == 0 else None

    def publish(self) -> None:
        if not self.has_origin_main():
            return
        ahead = int(git(self.root, "rev-list", "--count", "origin/main..HEAD", check=True).stdout.strip() or 0)
        if ahead == 0:
            return
        branch = f"sync/{hostname()}"
        if self.dry or self.args.no_push:
            print(f"would push {ahead} commit(s) to {branch}")
            return
        if git(self.root, "diff", "--quiet", "origin/main", "HEAD").returncode == 0:
            return   # only merge commits: nothing this checkout changes, nothing to push
        parts = self.split()
        if parts is not None:
            # protected paths wait for the owner's click on their own branch; the rest lands on green
            plain, guarded = parts
            for paths, name, note in ((plain, branch, "pushed"), (guarded, f"{branch}-protected", "pushed_protected")):
                if not paths or self.waits(paths):
                    continue
                commit = self.subset_commit(paths, f"origin/{name}")
                pushed = None if commit is None else self.push_branch(commit, name)
                if pushed is None:
                    self.error(f"push failed ({name})")
                    continue
                if pushed:
                    self.notes[note] = len(paths)
                    self.quiet_pushed()
                    print(f"push: {len(paths)} path(s) to {name}")
                self.open_pr(name, "chore: sync (protected)" if note == "pushed_protected" else "chore: sync")
            return
        out = git(self.root, "diff", "--name-only", "--no-renames", "-z", "origin/main", "HEAD").stdout
        if self.waits([p for p in out.split("\x00") if p]):
            return
        current = git(self.root, "rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}").stdout.strip()
        if current != git(self.root, "rev-parse", "HEAD").stdout.strip():
            done = git(self.root, "push", "--quiet", "--force-with-lease", "origin", f"HEAD:refs/heads/{branch}")
            if done.returncode != 0:
                self.error("push failed")
                return
            self.notes["pushed"] = ahead
            self.quiet_pushed()
            print(f"push: {ahead} commit(s) to {branch}")
        self.open_pr(branch)

    def waits(self, paths: list) -> bool:
        """A change that is only run lines, the meter and mount pins waits QUIET_HOURS since the last push,
        one that is only journal lines and run logs BATCH_HOURS; anything else goes out now."""
        # routine.py writes its meter line with every run, as it writes a run line
        housekeeping = {RUNS_PATH.as_posix(), "_meta/state/routine-limits.jsonl", *self.submodules()}
        if paths and all(p in housekeeping for p in paths):
            hours, what = QUIET_HOURS, "run lines and pins only"
        elif paths and all(p in housekeeping or p in RUN_LOGS or p.startswith("journal/") for p in paths):
            hours, what = BATCH_HOURS, "journal lines and run logs only"
        else:
            return False
        if self.quiet_due(hours):
            return False
        self.notes["waiting"] = what
        return True

    def quiet_due(self, hours: float) -> bool:
        try:
            last = float((self.gdir / "sync-quiet-pushed").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return True
        return self.now.timestamp() - last >= hours * 3600

    def quiet_pushed(self) -> None:
        try:
            (self.gdir / "sync-quiet-pushed").write_text(str(self.now.timestamp()), encoding="utf-8")
        except OSError:
            pass

    def gh(self, *args: str) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(["gh", *args], cwd=str(self.root), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return None

    def open_pr(self, branch: str, title: str = "chore: sync") -> None:
        listed = self.gh("pr", "list", "--head", branch, "--state", "open", "--json", "number")
        if listed is None:
            self.notes["gh"] = "absent"
            return
        if listed.returncode != 0:
            self.error("gh pr list failed")
            return
        if json.loads(listed.stdout or "[]"):
            return
        created = self.gh("pr", "create", "--base", "main", "--head", branch, "--title", title,
                          "--body", "Landed by the sync script.")
        if created is None or created.returncode != 0:
            self.error("gh pr create failed")
            return
        self.notes["pr"] = "opened"
        print(f"pull request opened for {branch}")

    # ----- run line ------------------------------------------------------------
    def heartbeat_today(self, path: Path) -> bool:
        if not path.exists():
            return False
        today = pages.iso(self.now)[:10]
        for raw in reversed(path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]):
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            notes = line.get("notes") or {}
            if line.get("routine") == "sync" and notes.get("heartbeat") and str(line.get("at", ""))[:10] == today:
                return True
        return False

    def record(self) -> None:
        if not spaceyaml.keeps_run_state(self.root):
            return   # the template keeps no run lines
        path = self.root / RUNS_PATH
        if not any(self.notes.get(key) for key in NOTABLE):
            if self.heartbeat_today(path):
                return
            self.notes["heartbeat"] = True
        line = {"routine": "sync", "at": pages.iso(self.now), "space": self.space.name,
                "cost_tokens": 0, "notes": self.notes}
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


def refresh_page(root: Path) -> None:
    """The progress page (`~/.spaces/onboarding.html`, outside every repository) is rewritten from the workspace
    after every ordinary run, once the lock is gone, so it is never older than the last sync. It reads files and
    starts nothing; a page that cannot be written is one plain line here, never a failed sync."""
    try:
        import onboarding_page
        onboarding_page.refresh(root)
    except Exception as exc:
        print(f"sync: the progress page could not be updated ({type(exc).__name__})")


def parse(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--release-leg", action="store_true")
    parser.add_argument("--min-age-minutes", type=float, default=10)
    parser.add_argument("--only", nargs="+", default=None, metavar="PATH")
    parser.add_argument("--message", default=None)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse(sys.argv[1:] if argv is None else argv)
    doctor.use_tools()   # git and gh that onboarding installed under ~/.spaces/tools
    root = Path(args.root).resolve()
    gdir = git_dir(root)
    if gdir is None:
        print(f"sync: {root} is not a git repository")
        return SKIPPED
    busy = in_progress(gdir)
    if busy:
        print(f"sync: skipped, git is mid-operation ({busy})")
        return SKIPPED
    run = Run(root, gdir, args)
    if not run.lock():
        print("sync: skipped, another run holds the lock")
        return SKIPPED
    try:
        if not args.only and not run.dry:
            run.watch_chains()
        if args.release_leg and not args.only:
            run.release_leg()
        run.land_structure()
        run.stage_and_commit()
        if not args.only:
            run.advance_pins()
        run.merge_in()
        if not args.only:
            run.set_aside_tests()
            if not run.dry and not args.no_push:
                run.heal()
        run.publish()
    except Exception as exc:  # unexpected: record, never lose the lock
        run.error(f"{type(exc).__name__}: {str(exc)[:200]}")
    finally:
        if not run.dry:
            run.record()
        run.unlock()
    code = ERROR if run.notes.get("errors") else OK
    if not args.only and not run.dry:
        refresh_page(root)
    return code


if __name__ == "__main__":
    sys.exit(main())
