"""sync: land work without human hands (vision section 12, landing design section 3).

The one tool that runs git. The OS scheduler runs it every 15 minutes; sessions and
routines only edit files. Each run:

  1. take `.git/sync.lock`; skip if another run holds it or git is mid-operation
  1a. name in stale.yaml each chain whose scheduler stopped starting it (`not_started`); while
     the routines run in the cloud, after step 5 instead (the computer's leg, below)
  2. stage tracked changes and settled untracked text files, never under personal/,
     academic/, _scratch/, _meta/state/private/, _meta/state/feed/ or inside a mount
  3. scan the staged paths; unstage every file with a finding (count recorded, never
     the match); commit `chore: sync`
  4. advance the pin of every clean mount to its remote branch; commit `chore: pins`
  5. fetch origin and merge origin/main in (append-only files by union, and the record of which
     computer runs the routines: `MERGE_RULES` is written into the space's own git first, so a
     space whose .gitattributes is older has it too). The rules settle what GitHub's own merge
     cannot, so no merge wedges on them: a watermark keeps the later mark key by key (the
     `--merge-latest` driver), generated pages and state keep this checkout's side (so do the
     sessions list, the items the computer's leg writes again and template sync's provenance),
     and an item under _inbound/ or _outbound/ that one side deleted stays deleted (`resolve`).
     A conflict where origin holds only an earlier copy of this checkout's own change keeps ours
     (the rebuilt commits of step 6 share no history with it); any other conflict is
     aborted and recorded
  5a. when there is something to publish: run the answer tests; a question that fails is set
     aside (`held: {since, by: sync, missing}` in answers.yaml, named in the run line) so it
     cannot turn the pull request red and hold every other change back, and one the sync set
     aside that passes again comes back; commit `chore: sync`
  5b. read this machine's open sync pull request: when its checks failed, set aside the answer
     tests the failed log names (`by: ci`) and commit, or else rerun the failed run once per
     head; a repair is recorded `healed` once the pull request merges (the briefing's "Fixed by
     itself"), and what stays red after a rerun is left in the run line as `pr_red`. Pull
     requests are read and opened through REST (`ghrest`), never GraphQL, which a cloud run
     refuses
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
    python sync.py --root R --cloud sweep|daily [--wait-minutes N]
    python sync.py --merge-latest BASE OURS THEIRS

`--only` lands exactly the changes under the given paths as one commit (`chore: structure`
unless `--message` says otherwise), whatever their age: the librarian's one commit per
structural change. Pins and the release leg wait for the next ordinary run.

`--cloud <chain>` is a cloud run's landing, run once at its end (spec 4.3); `cloud.py begin` carried
the earlier run's branch first (`carry`). Nothing about pins, the release leg or the chains' clocks
runs. Its own run line is written before its commit, so it lands with it; new files need no minimum
age. A protected path (`protected_paths`) never lands from the cloud, even one staged before the
run: AGENTS.md, which the tools regenerate, is put back quietly (deleted, when main holds none), and
any other stays in the clone and is one security item on Needs you. The one change there that lands
is the sync's own: answer tests set aside or brought back (`protected_changes`). Nor does a path in
a private zone, a mount or a nested checkout, or any pin, even one staged by force. Every git it
starts runs with a mount's own file monitor off (`monitor_off`), a command a mount's git config
could name. A line the secret scan hits is replaced by `[a line held back: <kind>]` (a private key
from its BEGIN line to its END line) and the page lands, since the clone is thrown away; a file over
1 MB, not text, or that would not read with its lines held back is named in the run line. After the
merge of main and the answer tests (steps 5 and 5a), the push goes to `cloud/<chain>`, leased to the
head the carry saw; refused, it carries again and pushes once more. Run lines, marks and stale.yaml
alone open no pull request until the branch's oldest commit main lacks is QUIET_HOURS old, unless
they hold a proof run's line; anything else opens one through REST and waits for its merge, merging
main in again while it conflicts and repairing red checks once. What happened after the push is
printed for the run log, and the next run's line says whether it carried this one.

The computer's leg (spec 4.5, `computer_leg`) runs while the workspace's routine record says the
routines run in the cloud (`runner: cloud`; for a space that is no workspace, its owner's workspace
on this computer). It comes right after step 5, so it reads the cloud's run lines first, and does
what only the computer has: `not_started` moves there from step 1a, and waits for a run that merged
main (offline, or stopped by a conflict, it would not see the cloud's lines); the transcripts scan
runs at most every `chain.TRANSCRIPTS_HOURS`, where the session files are on this computer, its
clock kept outside the repository (`~/.spaces/sync-leg-<space>.json`); template sync runs weekly, as
the daily chain runs it on a computer, since its machinery changes need the person's click;
AGENTS.md is written again when it is not what `agents_md.py` renders, as status writes it on a
computer, but only while this checkout's `agents_md.py` is main's, which the gate renders with, and
rides this computer's protected pull request (a cloud run puts back its own); a
`cloud/<chain>` pull request open for ADOPT_MINUTES, in conflict with main or red, whose head is as
old, is merged here with the merge rules and rides this computer's own pull request, and one that
still conflicts is one item on Needs you; and the workspace names on Needs you a space of the
person's that the move did not attach. All but `not_started` run only on the computer the record
names (`keeps_leg`); a record that names none lets each computer run them, and the merge rules keep
what two computers both write from stopping a merge. The scan, template sync and AGENTS.md wait
while the routines are paused. What the leg wrote is committed before step 5a.

`--release-leg` runs `release.ship(root)` first when that module is present.
`--merge-latest` is the watermark driver git runs during a merge (`merge_latest`); nobody types it.
Exit 0 on success, 2 when the run was skipped, 1 on an error.

`carry(root, chain)` is not a run of its own: a cloud run's `cloud.py begin` calls it first, to
merge in the earlier run's `cloud/<chain>` branch, which never merged, before anything is written.
A branch that changes a protected path (`protected_changes`) was pushed by someone else and is never
merged. Its git, too, runs with a mount's file monitor off.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fnmatch
import hashlib
import importlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import doctor  # noqa: E402
import ghrest  # noqa: E402
import pages  # noqa: E402
import scan  # noqa: E402
import spaceyaml  # noqa: E402

OK, ERROR, SKIPPED = 0, 1, 2
PRIVATE_ZONES = ("personal/", "academic/", "_scratch/", "_meta/state/private/", "_meta/state/feed/")
IN_PROGRESS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "rebase-merge", "rebase-apply", "index.lock")
LOCK_HOURS = 0.5
QUIET_HOURS = 6   # run lines and mount pins alone go out at most this often
BATCH_HOURS = 1   # journal lines and run logs alone go out at most this often
RUNS_PATH = Path("_meta") / "state" / "runs.jsonl"
RUN_LOGS = {"_meta/state/tests.jsonl", "_meta/state/probe.jsonl"}
NOTABLE = ("committed", "pins", "conflict", "scan_hits", "errors", "held_tests", "restored_tests",
           "healing", "healed", "pr_red", "adopted")
HEAL_STATE = "sync-heal.json"   # in the git dir: the repair waiting to be seen merged
CHAINS = {"sweep_chain": "sweep-chain", "daily": "daily-chain"}   # run line routine: its routine prompt


# Written into every space's own git, whatever its .gitattributes says. The record of who runs the routines merges by
# union: two computers that claim them before either synced leave a record that reads as unclear (nobody runs) instead
# of a conflict that wedges the sync. A watermark keeps the later mark (`merge_latest`); generated pages and state keep
# this checkout's side, since the next status run writes them again from the files (spec 4.5a). So do the files the
# computer's leg writes again on its next run (`computer_leg`): the sessions the transcripts scan saw, the leg's own
# items, and template sync's provenance, which every weekly sync writes again with the second it ran. Two computers
# that both run the leg (a record that names no computer for it) fall due for template sync together, and two syncs of
# the same release differ only in that second; they never stop a merge on any of these.
MERGE_RULES = ("_meta/state/routine-host.yaml merge=union",
               "_meta/state/*-watermark.yaml merge=spaces-latest",
               "live/** merge=spaces-ours",
               "_meta/state/ids.yaml merge=spaces-ours",
               "_meta/state/stale.yaml merge=spaces-ours",
               "_meta/state/sessions.yaml merge=spaces-ours",
               "_inbound/needs-you/sync.yaml merge=spaces-ours",
               "_meta/state/template-version.yaml merge=spaces-ours")
SETTLED_BY_DELETION = ("_inbound/", "_outbound/")   # an answer or a shipping is the later fact than any change
CLOUD_CHAINS = ("sweep", "daily")   # each lands on its own branch, cloud/<chain>
LEASE = "sync-cloud-lease-{}"   # in the git dir: the cloud/<chain> head the carry saw, empty when there was none
DEEPEN, DEEPEN_TRIES = 200, 3   # a shallow clone fetches this much more history, this often, before it unshallows
# A cloud run's landing (`--cloud`): these alone push its branch and open no pull request (`quiet_cloud`), unless the
# run lines hold a proof run's line. The routine meter is protected, so it never lands from one.
CLOUD_QUIET = ("_meta/state/runs.jsonl", "_meta/state/stale.yaml", "_meta/state/*-watermark.yaml")
CLOUD_MAX_BYTES = 1_000_000   # a file larger than this, or not text, is named in the run line and never landed
CLOUD_ITEMS = Path("_inbound") / "needs-you" / "sync-cloud.yaml"
CLOUD_REMERGE = 3   # merges of main while the pull request conflicts with it, before it is left open
POLL_SECONDS, WAIT_MINUTES = 20, 9   # how often a cloud run reads its pull request, and for how long at most
# The computer's leg, while the routines run in the cloud (`computer_leg`)
ADOPT_MINUTES = 30   # a cloud pull request open this long, in conflict or red, is merged here: its run waits less
TEMPLATE_RETRY_HOURS = 24   # a template sync or AGENTS.md that failed is tried again after this, as the daily would
LEG_STATE = "sync-leg-{}.json"   # under ~/.spaces/, beside the progress page: the leg's clocks for one space
SYNC_ITEMS = Path("_inbound") / "needs-you" / "sync.yaml"   # the leg's items on Needs you
HELD_LINE = "[a line held back: {}]"
KEY_END = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY")   # where the block a private key's BEGIN line opens ends
# In YAML, the part of a held line that is kept: a `key: ` (in a list too), or a list's `- `; the value is held back
YAML_KEY = re.compile(r"[ \t]*(?:- +)?[^\s:#'\"\[\]{}][^:#]*?:[ \t]+(?=\S)")
YAML_ITEM = re.compile(r"[ \t]*- +(?=\S)")
NO_REPOSITORY = "no GitHub repository behind origin"
LANDING = {   # what became of a cloud run's pull request, for the run log
    "merged": "pull request #{pr} merged",
    "closed": "pull request #{pr} was closed without merging; the next cloud run starts again from main",
    "conflict": "pull request #{pr} still conflicts with main; it stays open, and the computer's sync adopts it",
    "red": "pull request #{pr} is still red after one repair; it stays open, and the computer's sync adopts it",
    "left_open": "pull request #{pr} has not merged yet; it stays open, the next cloud run carries it, and the "
                 "computer's sync adopts it after half an hour"}


def _sleep(seconds: float) -> None:
    """The wait between two reads of a cloud run's pull request (`wait_landed`); the tests replace it."""
    time.sleep(seconds)


def git(root, *args, check: bool = False, input: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", input=input, check=check,
                          env=dict(os.environ, **env) if env else None)


@contextlib.contextmanager
def monitor_off():
    """Every git started inside it runs with the file monitor off, a mount's git included: a mount's own git config
    could name one, a command its git runs whenever the space's git looks into the mount (`git status`, and the diff
    that lists a merge's conflicts). A cloud run's landing and its carry run inside it. Set through git's own
    environment (GIT_CONFIG_COUNT with its KEY and VALUE), which git hands to a mount's git, after the config the
    environment holds already, which keeps its effect; the environment is put back as it was after. A count git
    would refuse is left as it is. Also a decorator."""
    count = os.environ.get("GIT_CONFIG_COUNT") or "0"
    if not count.isdigit():
        yield
        return
    keys = ("GIT_CONFIG_COUNT", f"GIT_CONFIG_KEY_{count}", f"GIT_CONFIG_VALUE_{count}")
    before = {key: os.environ.get(key) for key in keys}
    os.environ.update(zip(keys, (str(int(count) + 1), "core.fsmonitor", "false")))
    try:
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


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
    the chain runs. Written only when it changes. True when it wrote. While the routines run in the
    cloud the sync asks it after merging main in (`Run.computer_leg`), so the cloud's run lines are
    read first."""
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


# ----- the merge drivers -----------------------------------------------------------------------------
def _quoted(path: Path) -> str:
    """A path as one word of the shell command git runs a driver with (forward slashes, which every
    shell git uses reads, Windows' included)."""
    # reader._quoted refuses a name it cannot quote, for a guard that must not fail open; a driver escapes it instead
    text = path.as_posix()
    for char in ("\\", '"', "$", "`"):
        text = text.replace(char, "\\" + char)
    return f'"{text}"'


def merge_drivers() -> dict:
    """The drivers `MERGE_RULES` name, as git config keys and values. `spaces-ours` is `true`, which
    leaves this checkout's file as it is; `spaces-latest` runs `merge_latest` with the Python running
    now, started as the sync itself is started, with no flags: `-I` would drop the user's own
    site-packages, where setup often installs PyYAML (`pip install --user`), and then every
    watermark would conflict. A script's own folder, never the working folder, heads its import path."""
    drivers = {"merge.spaces-ours.driver": "true"}
    if sys.executable:
        drivers["merge.spaces-latest.driver"] = (f"{_quoted(Path(sys.executable))} {_quoted(Path(__file__).resolve())}"
                                                 " --merge-latest %O %A %B")
    return drivers


def _moment(value) -> dt.datetime | None:
    if not isinstance(value, (str, dt.date)):
        return None   # a count is never a moment, however its digits read
    try:
        return pages.parse_iso(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _later(ours, theirs):
    """Two sides of a watermark as one: mappings key by key, two moments the later one, else ours."""
    if isinstance(ours, dict) and isinstance(theirs, dict):
        merged = dict(ours)
        for key, value in theirs.items():
            merged[key] = _later(ours[key], value) if key in ours else value
        return merged
    mine, other = _moment(ours), _moment(theirs)
    return theirs if mine is not None and other is not None and other > mine else ours


def merge_latest(base: Path, ours: Path, theirs: Path) -> int:
    """The watermark driver (`merge=spaces-latest`): both sides are read as YAML and merged key by
    key. Where both values are moments, the later wins; a key on one side only is kept; any other
    difference keeps ours. So neither a cloud run's progress nor a computer's is undone, whatever
    the base said. The result is written to `ours`, as git expects, and only when it differs from
    what is there. 0 when merged; 1 when either side is not readable YAML, and then `ours` is left as
    it was and git keeps the conflict."""
    import yaml
    try:
        mine = yaml.safe_load(Path(ours).read_text(encoding="utf-8"))
        other = yaml.safe_load(Path(theirs).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return 1
    mine, other = ({} if side is None else side for side in (mine, other))   # an empty file marks nothing
    merged = _later(mine, other)
    if merged != mine:
        try:
            Path(ours).write_text(yaml.safe_dump(merged, sort_keys=False), encoding="utf-8", newline="\n")
        except OSError:
            return 1
    return 0


# ----- a cloud run's held lines (`Run.hold_lines`) ------------------------------------------------------
def held_lines(text: str, rel: str) -> dict:
    """{line number: the kinds the secret scan finds there} for the text of `rel`, as the scan numbers its
    lines. The scan finds a private key by its BEGIN line alone; its body and END line (or, with no END line,
    the rest of the file) are held back with it."""
    lines, kinds = text.splitlines(), {}
    for finding in scan.scan_text(text, rel):
        kinds.setdefault(finding.line_no, set()).add(finding.kind)
        begin = lines[finding.line_no - 1]
        opened = scan.PATTERNS["private_key"].search(begin) if finding.kind == "private_key" else None
        if opened and not KEY_END.search(begin, opened.end()):
            for number in range(finding.line_no + 1, len(lines) + 1):
                kinds.setdefault(number, set()).add("private_key")
                if KEY_END.search(lines[number - 1]):
                    break
    return kinds


def yaml_lines(rel: str, text: str) -> range:
    """The numbers of the lines of `rel` that are YAML: all of a YAML file's, a page's frontmatter."""
    name = rel.lower()
    if name.endswith((".yaml", ".yml")):
        return range(1, len(text.splitlines()) + 1)
    front = pages.FM_RE.match(text) if name.endswith(".md") else None
    return range(2, len(text[:front.end()].splitlines())) if front else range(0)


def _held(line: str, kinds: set, in_yaml: bool, rel: str) -> str:
    """`line` held back: `[a line held back: <kinds>]` after its indent, its line end kept. In YAML the
    marker is a quoted value after the line's `key: ` or `- `, when that part itself holds nothing the scan
    finds, so the file still reads."""
    body = (line.splitlines() or [""])[0]
    marker = HELD_LINE.format(", ".join(sorted(kinds)))
    head = (YAML_KEY.match(body) or YAML_ITEM.match(body)) if in_yaml else None
    if head and not scan.scan_text(head.group(0), rel):
        return f'{head.group(0)}"{marker}"{line[len(body):]}'
    return body[:len(body) - len(body.lstrip(" \t"))] + marker + line[len(body):]


def reads_as(rel: str, text: str):
    """What a structured file reads as, for a hold to keep: True for YAML or JSON that parses, a page's
    frontmatter keys, True for any other text; None when it does not read."""
    import yaml
    name = rel.lower()
    try:
        if name.endswith((".yaml", ".yml")):
            yaml.safe_load(text)
        elif name.endswith(".json"):
            json.loads(text)
        elif name.endswith(".md"):
            return sorted(str(key) for key in pages.split(text)[0])
    except (ValueError, yaml.YAMLError):
        return None
    return True


def items_in(path: Path) -> list:
    """The items a Needs-you file (`_inbound/needs-you/<writer>.yaml`) holds, as written; none when it is missing or
    does not read."""
    import yaml
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        data = {}
    rows = data.get("items") if isinstance(data, dict) else None
    return [i for i in rows if isinstance(i, dict)] if isinstance(rows, list) else []


def items_out(path: Path, items: list, before: list) -> bool:
    """`items` written to the Needs-you file at `path`, only when they are not what it held (`before`, as `items_in`
    read it). True when it was written."""
    import yaml
    if items == before:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"items": items}, sort_keys=False, allow_unicode=True, width=100),
                    encoding="utf-8", newline="\n")
    return True


def _adopt_item(number: int) -> dict:
    """The item for a cloud pull request the computer's sync could not merge here (`Run.adopt_cloud`)."""
    return {"id": f"cloud-adopt-{number}", "kind": "request",
            "text": f"A cloud run's pull request #{number} conflicts with your files in a way the sync cannot settle.",
            "recommendation": "open it and keep the side you want, or close it: the next cloud run starts again "
                              "from main",
            "default": "it stays open and the cloud runs keep adding to it", "depends": 1}


class Run:
    def __init__(self, root: Path, gdir: Path, args) -> None:
        self.root, self.gdir, self.args = root, gdir, args
        self.space = spaceyaml.load(root)
        self.now = self.space.now()
        self.cloud = getattr(args, "cloud", None)   # the chain whose cloud run this lands, else None
        # a cloud run lands once, at its end: what it wrote is settled by then
        self.min_age = dt.timedelta(0) if self.cloud else self.space.scale(float(args.min_age_minutes) / 60)
        self.notes: dict = {}
        self.dry = bool(args.dry_run)
        self.slug = ghrest.repo_slug(root)   # the repository the pull request calls name; None when origin names none
        self.left_out: set = set()   # a cloud run's protected paths, never landed (`keep_out`)
        self.held: set = set()   # a cloud run's files over CLOUD_MAX_BYTES or not text, never landed
        self.behind = False   # origin/main could not be merged in (`merge_in`): its run lines are not here

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
    def watch_chains(self) -> bool:
        """`not_started`, never in the way of landing: True when it wrote stale.yaml."""
        try:
            return bool(not_started(self.root, self.now))
        except Exception as exc:  # never in the way of landing
            self.error(f"not started check: {type(exc).__name__}: {str(exc)[:200]}")
            return False

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
        """Whether no sync stages `rel`: a private zone, a mount (`subs`), or a nested checkout, its own gitlink
        included (a checkout .gitmodules does not name, staged at its own path)."""
        if rel.startswith(PRIVATE_ZONES):
            return True
        if any(rel == s or rel.startswith(s + "/") for s in subs):
            return True
        parent = Path(rel)
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
        # A cloud run's status still looks into each mount, so a pin move is seen, but a mount's own git config
        # must not start a command there: its file monitor is off (a `-c` reaches the mounts' git too).
        status = ("-c", "core.fsmonitor=false", "status") if self.cloud else ("status",)
        done = git(self.root, *status, "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames", check=True)
        chosen: list[str] = []
        for item in done.stdout.split("\0"):
            if len(item) < 4:
                continue
            xy, rel = item[:2], item[3:]
            if rel.endswith("/") or xy == "!!" or self.excluded(rel, subs) or not self.wanted(rel):
                continue
            # in a cloud run, `keep_out` names what cannot land instead
            if xy == "??" and not getattr(self.args, "only", None) and not self.cloud:
                why = self.settled_text(rel)
                if why:
                    print(f"skip {rel}: {why}")
                    continue
            chosen.append(rel)
        return chosen

    def pathspec(self, *args: str, rels: list[str]) -> None:
        spec = "\0".join(f":(literal){rel}" for rel in rels)
        git(self.root, *args, "--pathspec-from-file=-", "--pathspec-file-nul", input=spec, check=True)

    def add(self, rels: list[str]) -> None:
        """`git add -A` of `rels`, less any path in neither the index nor the working tree: one a `git mv`
        not yet committed moved away (a mount, say), whose deletion is staged already. `git add` would
        stop at it (its pathspec matches nothing) and fail the run."""
        if not rels:
            return   # an empty pathspec would add everything
        gone = {rel for rel in rels if not os.path.lexists(self.root / rel)}
        if gone:   # ls-files takes no pathspec file, so the index is read whole, and only when needed
            indexed = set(git(self.root, "ls-files", "-z", "--cached", check=True).stdout.split("\0"))
            rels = [rel for rel in rels if rel not in gone or rel in indexed]
        if rels:
            self.pathspec("add", "-A", rels=rels)

    def keep_out(self, rels: list[str]) -> list[str]:
        """The paths among `rels` this run may land. On a computer, all of them. In a cloud run, never a
        protected change (`protected_changes`): a cloud run's tools write none, so a change there came
        from somewhere else. AGENTS.md, which the tools regenerate, is put back quietly (`put_back`);
        any other stays in the clone, named in `left_out`. Nor a file over CLOUD_MAX_BYTES or not text,
        or one that would not read once its lines were held back (`hold_lines`), named in `held`: a
        computer leaves such a file for a later run, and the clone is thrown away. They are named here;
        `unstage` keeps them out of each commit, also when they were staged before the run."""
        if not self.cloud:
            return rels
        guarded = set(protected_changes(self.root, rels, ["HEAD"], None))
        keep = []
        for rel in rels:
            path = self.root / rel
            if rel in guarded:
                if rel == "AGENTS.md" and (self.dry or self.put_back(rel)):
                    continue
                if rel not in self.left_out:
                    print(f"left out: {rel} is protected")
                self.left_out.add(rel)
            elif rel in self.held or path.is_dir():
                continue
            elif os.path.lexists(path) and not self.landable_text(path):
                if rel not in self.held:
                    print(f"left out: {rel} is over 1 MB or not text")
                self.held.add(rel)
            else:
                keep.append(rel)
        return keep

    def put_back(self, rel: str) -> bool:
        """`rel` as HEAD has it, in the index and in the clone (`git checkout HEAD`). When HEAD has none, a staged
        copy is taken out of the index and the file is deleted, so that the clone has none either: a space whose
        main holds no AGENTS.md yet is no reason for a security item. True when that was done."""
        if git(self.root, "cat-file", "-e", f"HEAD:{rel}").returncode == 0:
            return git(self.root, "checkout", "HEAD", "--", rel).returncode == 0
        if git(self.root, "rm", "-q", "-f", "--cached", "--ignore-unmatch", "--", rel).returncode != 0:
            return False
        try:
            (self.root / rel).unlink(missing_ok=True)
        except OSError:   # a folder, or a file this run cannot delete
            return False
        return True

    @staticmethod
    def landable_text(path: Path) -> bool:
        try:
            if path.stat().st_size > CLOUD_MAX_BYTES:
                return False
        except OSError:   # a link to nothing
            return False
        return scan.text_of(path) is not None

    def hold_lines(self, rels: list[str]) -> int:
        """In a cloud run, each line of `rels` the secret scan hits is replaced, in the file, by `[a line
        held back: <kind>]`, so the page lands without it: a computer leaves such a file local, and the
        clone is thrown away. A private key is held back from its BEGIN line to its END line (or the
        file's end), since the scan finds its first line only. A held line keeps its indent; in YAML (a
        YAML file, a page's frontmatter) a `key: value` line keeps its key and only the value is held
        back, so the file still reads. A file that would then no longer read as it did (a JSON file, say)
        is left as it is, out of the landing, and named in `held`. How many lines were replaced; a line
        itself is never printed."""
        count = 0
        for rel in rels:
            path = self.root / rel
            text = None if path.is_symlink() else scan.text_of(path)   # a link lands as its target's name
            if text is None:
                continue
            kinds = held_lines(text, rel)
            if not kinds:
                continue
            lines, in_yaml = text.splitlines(keepends=True), yaml_lines(rel, text)
            for number, found in kinds.items():
                lines[number - 1] = _held(lines[number - 1], found, number in in_yaml, rel)
            done, shape = "".join(lines), reads_as(rel, text)
            if shape is not None and reads_as(rel, done) != shape:
                self.held.add(rel)
                print(f"left out: {rel} would not read with its lines held back")
                continue
            path.write_text(done, encoding="utf-8", newline="")
            count += len(kinds)
            print(f"scan: {len(kinds)} line(s) held back in {rel}")
        return count

    def staged(self) -> list[str]:
        done = git(self.root, "diff", "--cached", "--name-only", "-z", "--diff-filter=d", check=True)
        return [p for p in done.stdout.split("\0") if p]

    def unstage(self) -> None:
        """In a cloud run, the last word before each commit, which takes the whole index: whatever is staged
        and may not land is taken out of it, a deletion included, whoever staged it. That is a protected
        change (`protected_changes`, against HEAD), a file `keep_out` or `hold_lines` held, a path no sync
        ever stages (`excluded`: a private zone, a mount, a nested checkout), even one staged by force, and
        any gitlink staged, with a checkout behind it or none: a cloud run moves no pin. `keep_out` names the
        first two for the run line and the security item; this keeps them all out of the commit. A private
        path's name is never printed."""
        out = git(self.root, "diff", "--cached", "--raw", "--no-renames", "-z", check=True).stdout
        fields = out.split("\0")   # ":<old mode> <new mode> <old id> <new id> <status>", then its path
        staged, links = [], set()
        for meta, rel in zip(fields[0::2], fields[1::2]):
            if rel:
                staged.append(rel)
                if meta.split()[1:2] == ["160000"]:   # a gitlink
                    links.add(rel)
        guarded = protected_changes(self.root, staged, ["HEAD"], ":")
        subs = self.submodules()
        private = [rel for rel in staged if rel not in guarded and (rel in links or self.excluded(rel, subs))]
        drop = list(dict.fromkeys(guarded + private + [rel for rel in staged if rel in self.held]))
        if not drop:
            return
        for rel in guarded:
            if rel not in self.left_out:
                print(f"left out: {rel} is protected")
                self.left_out.add(rel)
        if private:
            print(f"left out: {len(private)} staged path(s) in a private zone, a mount or a nested checkout, "
                  "or a pin")
        self.pathspec("reset", "-q", rels=drop)

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
        if self.cloud:
            self.unstage()
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
            rels = self.keep_out(self.candidates())
            self.args.only = None
            if rels:
                self.add([r for r in rels if not self.excluded(r, subs)])
                self.scan_staged()
                self.commit("chore: structure")
                count += 1
            with landed_path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps({"id": change.get("id"), "at": pages.iso(self.now)}) + "\n")
        if count:
            self.notes["structure"] = count

    def stage_and_commit(self) -> None:
        rels = self.keep_out(self.candidates())
        if self.dry:
            for rel in rels:
                print(f"would stage {rel}")
            hits = scan.scan_paths([self.root / rel for rel in rels])
            if hits:
                print(f"would {'hold lines back in' if self.cloud else 'leave'} {len({f.path for f in hits})} "
                      f"file(s){'' if self.cloud else ' local'} after the scan")
            if rels:
                print("would commit: chore: sync")
            return
        self.add(rels)
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
        """Read this checkout's sync pull request and, when its checks failed, repair it
        (`hold_failed`), so the repair lands with this run's push. A repair counts once the pull
        request merges: the run line says `healed` and the briefing lists it. Quiet when GitHub
        cannot be asked: the pull request call after a push says why."""
        if self.slug is None:
            return
        state = self.heal_state()
        if state.get("pr"):
            viewed = ghrest.pull_state(self.gh, self.slug, state["pr"])
            if viewed is not None and viewed["merged"]:
                self.notes["healed"] = {"what": state.get("what"), "pr": state["pr"]}
                self.heal_state({})
        open_prs = ghrest.open_pulls(self.gh, self.slug, head=f"sync/{hostname()}")
        if open_prs:
            self.hold_failed(open_prs[0]["number"], open_prs[0]["head_sha"])

    def hold_failed(self, pr: int, head: str) -> bool:
        """The repair of a pull request whose checks failed on `head`, for a computer's sync pull request
        (`heal`) and a cloud run's (`wait_landed`): answer tests the failed log names are set aside
        for CI (`held: {by: ci}`, which only a person brings back, since a pass here does not prove a
        pass there) and committed, for the caller's push; any other failure of an Actions run is rerun
        once per head, as a flaky one (`healing`). True when it repaired something; False when nothing
        failed, or when nothing could be repaired (no answer test to set aside, and no run that was not
        rerun already), which stays in the run line (`pr_red`) for the maintenance agent, not the owner."""
        failed = ghrest.failed_checks(self.gh, self.slug, head) or []
        if not failed:
            return False
        state = self.heal_state()
        runs = sorted({m.group(1) for c in failed for m in [re.search(r"/actions/runs/(\d+)", str(c.get("link")))] if m})
        logs = [self.gh("run", "view", run, "--log-failed") for run in runs]
        import tests_run
        held = tests_run.hold(self.root, tests_run.failures_in("".join(d.stdout for d in logs if d), self.root),
                              "ci", self.now)
        if held:
            git(self.root, "add", "--", tests_run.ANSWERS_PATH.as_posix())
            self.commit("chore: sync")
            self.notes["healing"] = {"pr": pr, "held_tests": held}
            self.heal_state({"pr": pr, "what": f"set aside {len(held)} answer test(s) the checks could not "
                                               f"pass: {'; '.join(held)}"})
            return True
        if runs and state.get("rerun") != head:
            for run in runs:
                self.gh("run", "rerun", run, "--failed")
            self.notes["healing"] = {"pr": pr, "rerun": runs}
            self.heal_state({"pr": pr, "rerun": head, "what": "reran a failed check"})
            return True
        self.notes["pr_red"] = {"pr": pr, "checks": [c.get("name") for c in failed]}
        return False

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
            self.behind = True
            return
        if not self.has_origin_main():
            return
        before = git(self.root, "rev-parse", "HEAD").stdout.strip()
        done = git(self.root, "merge", "--no-edit", "origin/main")
        if done.returncode != 0 and not self.settle():
            git(self.root, "merge", "--abort")
            self.behind = True
            self.notes["conflict"] = True
            print("merge: conflict with origin/main; aborted, next run retries")
            return
        if git(self.root, "rev-parse", "HEAD").stdout.strip() != before:
            print("merge: origin/main brought in")

    def merge_rules(self) -> None:
        """The rules of `MERGE_RULES` in this space's own git (`info/attributes`, which outranks .gitattributes):
        each one missing is appended; a line already there is left as it is. The drivers they name go into the
        space's own git config, each only when it is missing or says something else."""
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
        for key, value in merge_drivers().items():
            if git(self.root, "config", "--get", key).stdout.rstrip("\n") != value \
                    and git(self.root, "config", key, value).returncode != 0:
                self.error(f"merge driver not written ({key})")

    def conflicted(self) -> list:
        return [p for p in git(self.root, "diff", "--name-only", "--diff-filter=U", "-z").stdout.split("\0") if p]

    def settle(self) -> bool:
        """Finish a merge that stopped, or say it cannot be: what the merge rules cover is resolved
        (`resolve`), and what is left must be origin holding an earlier copy of ours (`keep_ours`).
        False leaves the caller to abort; a merge that never started (a file in the way) is not
        settled, and nothing here raises, so a merge is never left half done to wedge the next run."""
        try:
            if git(self.root, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode != 0:
                return False
            before = len(self.conflicted())
            left = self.resolve()
            if left == 0:
                if git(self.root, "commit", "-q", "--no-edit").returncode != 0:
                    return False
            elif not self.keep_ours():
                return False
        except Exception as exc:
            self.error(f"merge not settled: {type(exc).__name__}")
            return False
        if before > left:
            self.notes["resolved"] = before - left
            print(f"merge: {before - left} path(s) settled by the merge rules")
        return True

    def resolve(self) -> int:
        """After a merge stopped, resolve the conflicts the merge rules cover; the number of conflicted
        paths left. An item under _inbound/ or _outbound/ that one side deleted and the other changed is
        deleted: the person's answer or the shipping is the later fact. A path whose merge rule is
        `spaces-ours` keeps this checkout's side also where git ran no driver for it, which is where one
        side deleted it (deleted here, it stays deleted); where both sides added it, git's driver
        already kept ours. Anything else is left for the caller."""
        stages: dict = {}
        for entry in git(self.root, "ls-files", "-u", "-z").stdout.split("\0"):
            meta, _, path = entry.partition("\t")
            if path and meta:
                stages.setdefault(path, set()).add(meta.split()[-1])
        ours = self.ruled(sorted(stages), "spaces-ours")
        drop, keep = [], []
        for path, have in stages.items():
            if path.startswith(SETTLED_BY_DELETION) and have in ({"1", "2"}, {"1", "3"}):
                drop.append(path)
            elif path in ours:
                (keep if "2" in have else drop).append(path)
        if drop:
            git(self.root, "rm", "-q", "--pathspec-from-file=-", "--pathspec-file-nul", input=self.spec(drop))
        if keep:
            git(self.root, "checkout", "--ours", "--pathspec-from-file=-", "--pathspec-file-nul", input=self.spec(keep))
            git(self.root, "add", "--pathspec-from-file=-", "--pathspec-file-nul", input=self.spec(keep))
        return len(self.conflicted())

    def ruled(self, paths: list, driver: str) -> set:
        """The paths whose `merge` attribute is `driver`, as git reads the rules (info/attributes first)."""
        if not paths:
            return set()
        out = git(self.root, "check-attr", "-z", "--stdin", "merge", input=self.spec(paths, literal=False)).stdout
        fields = out.split("\0")
        return {path for path, _, value in zip(fields[0::3], fields[1::3], fields[2::3]) if value == driver}

    @staticmethod
    def spec(paths: list, literal: bool = True) -> str:
        return "\0".join(f":(literal){p}" if literal else p for p in paths)

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
        when nothing is protected (or in the template, whose pull requests land on green).
        A mount whose address waits in `.gitmodules` waits with it (`readdressed`)."""
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
        guarded = [p for p in changed if p in managed or any(gate._under(gate._posix(p), pre) for pre in prefixes)]
        if ".gitmodules" in guarded:
            guarded += [p for p in self.readdressed() if p in changed and p not in guarded]
        if not guarded:
            return None
        return [p for p in changed if p not in guarded], guarded

    def readdressed(self) -> list:
        """The mounts whose pin must wait with `.gitmodules`: one new on HEAD, or one whose entry
        there (its path or url) differs from origin/main's. Landed alone, main would hold a pin
        with no address, or one its old address cannot fetch (`git submodule update --init` stops
        at it). A pin that only advanced lands on green (decision 2026-10-16)."""
        out = git(self.root, "diff", "--raw", "-z", "--no-renames", "--no-abbrev", "origin/main", "HEAD").stdout
        fields = out.split("\x00")
        links = {path: meta[1:].split()[0] for meta, path in zip(fields[::2], fields[1::2])
                 if meta.startswith(":") and "160000" in meta[1:].split()[:2]}
        before, after = self.addresses("origin/main"), self.addresses("HEAD")
        return [path for path, mode in links.items() if mode != "160000" or before.get(path) != after.get(path)]

    def addresses(self, ref: str) -> dict:
        """Each mount's url in `.gitmodules` at `ref`, by its path; empty when there is none."""
        done = git(self.root, "config", "-z", "--blob", f"{ref}:.gitmodules",
                   "--get-regexp", r"^submodule\..*\.(path|url)$")
        entries: dict = {}
        for item in done.stdout.split("\x00") if done.returncode == 0 else []:
            key, _, value = item.partition("\n")
            name, _, field = key.rpartition(".")
            entries.setdefault(name, {})[field] = value
        return {e["path"]: e.get("url") for e in entries.values() if e.get("path")}

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

    def rest(self, *args: str) -> subprocess.CompletedProcess | None:
        """`gh` as the pull request calls run it: `gh: absent` in the run line when it is not installed."""
        done = self.gh(*args)
        if done is None:
            self.notes["gh"] = "absent"
        return done

    def open_pr(self, branch: str, title: str = "chore: sync") -> int | None:
        """The number of the open pull request from `branch` into main, opened now when there is
        none (`pr: opened`). None when GitHub could not be asked: gh not installed (`gh: absent`,
        no error), origin naming no repository (one error a run, however many branches were
        pushed), or a call that failed. Whether gh is there is asked first: without it, an origin
        that names no repository (a local path) is no error either."""
        if self.slug is None:
            if self.notes.get("gh") != "absent" and NO_REPOSITORY not in self.notes.get("errors", []) \
                    and self.rest("--version") is not None:
                self.error(NO_REPOSITORY)
            return None
        listed = ghrest.open_pulls(self.rest, self.slug, head=branch)
        if listed is None:
            if self.notes.get("gh") != "absent":
                self.error("the open pull requests could not be read")
            return None
        if listed:
            return listed[0]["number"]
        number = ghrest.create_pull(self.rest, self.slug, branch, title, "Landed by the sync script.")
        if number is None:
            self.error("the pull request could not be opened")
            return None
        self.notes["pr"] = "opened"
        print(f"pull request #{number} opened for {branch}")
        return number

    # ----- the computer's leg, while the routines run in the cloud (spec 4.5) -----------------
    def workspace(self) -> Path | None:
        """The workspace whose routine record speaks for this space: this space when it is personal, else its
        owner's personal workspace on this computer (`~/.spaces/spaces.yaml`, the list `chain.spaces_on_machine`
        reads). None when there is none."""
        if self.space.profile == "personal":
            return self.root
        import yaml
        try:
            rows = (yaml.safe_load((Path.home() / ".spaces" / "spaces.yaml").read_text(encoding="utf-8"))
                    or {}).get("spaces") or []
        except (OSError, UnicodeDecodeError, AttributeError, yaml.YAMLError):
            rows = []
        for row in rows if isinstance(rows, list) else []:
            root = Path(str(row["root"])) if isinstance(row, dict) and row.get("root") else None
            if root is None or not (root / "space.yaml").is_file():
                continue
            try:
                other = spaceyaml.load(root)
            except Exception:
                continue
            if other.profile == "personal" and self.space.owner and other.owner == self.space.owner:
                return root.resolve()
        return None

    def cloud_host(self) -> tuple | None:
        """(the workspace, its routine record) when that record says the routines run in the cloud (`runner:
        cloud`): then the computer's leg runs (`computer_leg`). None otherwise, and for a record that cannot be
        read, which stops no landing."""
        try:
            import routine
            workspace = self.workspace()
            record = routine.host(workspace) if workspace is not None else None
        except Exception:
            return None
        if not record or record.get("unclear") or record.get("runner") != "cloud":
            return None
        return workspace, record

    @staticmethod
    def keeps_leg(record: dict) -> bool:
        """Whether this computer does the leg's writing steps (`computer_leg`): the one the record names
        (`computer`, its id as `routine.this_machine` makes it), as one computer ran the chains before the move.
        Two computers that both did them would each scan their own sessions, sync the template and name their
        own spaces, and conflict on what they wrote. A record that names no computer lets every one do them;
        the merge rules keep what they write again from stopping a merge (`MERGE_RULES`)."""
        import routine
        named = record.get("computer")
        return not named or named == routine.this_machine()

    def computer_leg(self, now: dt.datetime) -> dict:
        """What only the computer has, while the routines run in the cloud (spec 4.5), right after main is merged
        in, so that the cloud's run lines are read first. In order: `not_started`, which waited for the merge; the
        transcripts scan (`scan_transcripts`); template sync (`sync_template`); AGENTS.md, when it is not what
        `agents_md.py` renders (`write_agents_md`); the cloud pull requests nothing could merge (`adopt_cloud`);
        and, in the workspace, the spaces the cloud does not read (`name_unattached`). `not_started` runs on every
        computer, and not at all when main could not be merged in (the fetch failed, or the merge stopped): without
        the cloud's run lines it would name chains that ran, and stale.yaml stays as it was until a run merges main.
        The rest write files, so they run on the computer the record names (`keeps_leg`); a record that names none
        lets every computer run them, and the merge rules keep what two computers both write from stopping a merge.
        While the routines are paused, the scan, template sync and AGENTS.md wait, as the chains that ran them
        would. Each step's outcome goes into the run line's notes; a step that fails is said and the next one runs,
        since the landing must still happen. What the leg wrote is committed with this run. A merge that moved the
        routines back to a computer leaves only `not_started`. The outcomes, as those notes."""
        import routine
        done: dict = {}
        if not self.behind and self.watch_chains():
            done["not_started"] = True
        host = self.cloud_host()
        if host is not None and self.keeps_leg(host[1]):
            workspace, record = host
            steps = [] if routine.paused_until(record, now) else [
                ("transcripts", "transcripts scan", self.scan_transcripts),
                ("template_sync", "template sync", self.sync_template),
                ("agents_md", "writing AGENTS.md", self.write_agents_md)]
            steps.append(("adopted", "adopting cloud pull requests", self.adopt_cloud))
            if Path(workspace).resolve() == self.root.resolve():
                steps.append(("unattached", "naming the spaces the cloud does not read",
                              lambda moment: self.name_unattached(record)))
            for key, what, step in steps:
                try:
                    outcome = step(now)
                except Exception as exc:
                    self.error(f"{what} failed: {type(exc).__name__}")
                    continue
                if outcome:
                    done[key] = outcome
        self.notes.update(done)
        self.stage_and_commit()
        return done

    def leg_state(self, **changes) -> dict:
        """The leg's clocks for this space, kept on this computer, never in the repository
        (`~/.spaces/sync-leg-<space>.json`, beside the progress page): when it last scanned the transcripts
        (`transcripts`), and when a template sync or the writing of AGENTS.md last failed
        (`template_sync_failed`, `agents_md_failed`). `changes` set those keys (None takes one out); the
        file is written only when that changes it, and one that cannot be written leaves the step to run
        again next time."""
        path = Path.home() / ".spaces" / LEG_STATE.format(self.space.name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        if changes:
            new = {key: value for key, value in {**data, **changes}.items() if value is not None}
            if new != data:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(new, sort_keys=True), encoding="utf-8")
                except OSError:
                    pass
            data = new
        return data

    def since(self, key: str, now: dt.datetime) -> dt.timedelta | None:
        """How long ago `leg_state` says `key` happened; None when it never did, or when it says a time after
        `now` (a clock that went back)."""
        try:
            then = pages.parse_iso(self.leg_state().get(key))
        except (TypeError, ValueError, OverflowError):
            return None
        return now - then if then <= now else None

    def scan_transcripts(self, now: dt.datetime) -> dict | None:
        """The transcripts scan (`transcripts.scan`), the sweep chain's first step on a computer, at most every
        `chain.TRANSCRIPTS_HOURS`, and only where the session files are on this computer: a cloud sweep never
        reads them. A scan that fails waits as long, so it is said once, not every run. Its report; None when
        it did not run."""
        import chain
        import transcripts
        folder = transcripts.settings(self.space).get("path")
        if self.space.profile != "personal" or not folder or not Path(folder).is_dir():
            return None
        ago = self.since("transcripts", now)
        if ago is not None and ago < self.space.scale(chain.TRANSCRIPTS_HOURS):
            return None
        self.leg_state(transcripts=pages.iso(now))
        return transcripts.scan(self.root, now)

    def sync_template(self, now: dt.datetime) -> dict | None:
        """Template sync with the tests around it (`template_sync.guarded`), when the daily chain on a computer
        would run it (`chain._template_sync_due`: weekly, or the day the person said yes to a release). Its
        machinery changes wait for the person's click (`split`), and a cloud run never lands a protected
        change. One that failed is tried again TEMPLATE_RETRY_HOURS later, not every run. What it changed;
        None when it did not run."""
        import chain
        failed = self.since("template_sync_failed", now)
        if failed is not None and failed < self.space.scale(TEMPLATE_RETRY_HOURS):
            return None
        if not chain._template_sync_due(self.space, now):
            return None
        import template_sync
        try:
            report = template_sync.guarded(self.root)
        except Exception:
            self.leg_state(template_sync_failed=pages.iso(now))
            raise
        self.leg_state(template_sync_failed=None)
        return {"added": len(report.added), "updated": len(report.updated), "reverted": bool(report.reverted)}

    def write_agents_md(self, now: dt.datetime) -> bool:
        """AGENTS.md written again when it is not what `agents_md.py` renders (`agents_md.check`), as the status
        routine writes it on a computer (`status._refresh_agents_md`). While the routines run in the cloud no status
        runs here, and a cloud run puts back the one its own status wrote (`keep_out`), so without this step a release
        that changes what `agents_md.py` writes, or a change of kinds, would never reach the file. The file is
        protected: it rides this computer's protected pull request (`split`), which the gate lands when it is what
        main's `agents_md.py` renders from the pull request's files (`gate.rendered_at`), and which otherwise waits
        for the person's click. So nothing is written while this checkout's `agents_md.py` is not main's: a release
        that changes it has not landed, and a file written from it would hold that whole release for the click. Once
        the release is on main, the next run writes the file, and it lands on its own (`gate.GENERATED`). A release
        that changes only the page template still rides its own pull request, since both renders read the template
        from the files. A render that fails (a template whose placeholders it cannot fill) is said once and tried
        again TEMPLATE_RETRY_HOURS later, as status would try it once a day. Never in the template, which keeps none.
        True when it was written."""
        import agents_md
        if (self.root / spaceyaml.TEMPLATE_MARKER).exists() or not (self.root / "space.yaml").is_file():
            return False
        failed = self.since("agents_md_failed", now)
        if failed is not None and failed < self.space.scale(TEMPLATE_RETRY_HOURS):
            return False
        if self.has_origin_main() and git(self.root, "diff", "--quiet", "origin/main", "--",
                                          "_meta/tools/agents_md.py").returncode != 0:
            return False
        try:
            current = agents_md.check(self.root)
            if not current:
                agents_md.write(self.root)
        except Exception:
            self.leg_state(agents_md_failed=pages.iso(now))
            raise
        self.leg_state(agents_md_failed=None)
        return not current

    def adopt_cloud(self, now: dt.datetime) -> list:
        """The `cloud/<chain>` pull requests of this repository that nothing will merge, merged into this
        checkout with the merge rules (`adopt`), so that they ride this computer's own sync pull request, which
        it repairs as it repairs its own (`heal`); once that lands, GitHub marks them merged. One is adopted when
        it was opened ADOPT_MINUTES ago or more and conflicts with main or has a failed check (`stuck`), and its
        head is as old (`adopt`): a cloud run waits for its own for less. One whose merge here still conflicts is
        aborted and becomes one item on Needs you (`cloud-adopt-<n>`), which goes once it is adopted or no
        longer open. A fork's pull request is never one. The numbers adopted."""
        if self.slug is None or not self.has_origin_main():
            return []
        listed, complete = [], True
        for chain_name in CLOUD_CHAINS:
            branch = f"cloud/{chain_name}"
            pulls = ghrest.open_pulls(self.gh, self.slug, head=branch)
            if pulls is None:
                complete = False   # what could not be read says nothing about what is gone
                continue
            listed += [(chain_name, pull) for pull in pulls if pull["head_ref"] == branch and not pull["cross_repo"]]
        adopted, conflicts, committed = [], [], False
        for chain_name, pull in listed:
            if not self.stuck(pull, now):
                continue
            if not committed:   # a merge stops at a local change in its way: what the leg wrote so far lands first
                self.stage_and_commit()
                committed = True
            outcome = self.adopt(chain_name, pull["number"], now)
            if outcome == "adopted":
                adopted.append(pull["number"])
            elif outcome == "conflict":
                conflicts.append(pull["number"])
        open_now = {pull["number"] for _, pull in listed}

        def settled(item_id: str) -> bool:   # an item goes once its pull request was adopted or is no longer open
            number = item_id.removeprefix("cloud-adopt-")
            if not (item_id.startswith("cloud-adopt-") and number.isdigit()):
                return False
            return int(number) in adopted or (complete and int(number) not in open_now)
        self.leg_items([_adopt_item(n) for n in conflicts], settled)
        return adopted

    def stuck(self, pull: dict, now: dt.datetime) -> bool:
        """Whether a cloud pull request is one nothing will merge: opened ADOPT_MINUTES ago or more, still open,
        and in conflict with main (`dirty`) or with a failed check on its head."""
        try:
            opened = pages.parse_iso(pull.get("created_at"))
        except (TypeError, ValueError, OverflowError):
            return False
        if now - opened < self.space.scale(ADOPT_MINUTES / 60):
            return False
        state = ghrest.pull_state(self.gh, self.slug, pull["number"])
        if state is None or state["merged"] or state["state"] != "open":
            return False
        return state["mergeable_state"] == "dirty" or bool(
            ghrest.failed_checks(self.gh, self.slug, state["head_sha"] or pull["head_sha"]))

    def adopt(self, chain: str, number: int, now: dt.datetime) -> str:
        """`cloud/<chain>` fetched with an explicit refspec and merged into this checkout with the merge rules
        (`settle`, as `merge_in`): `adopted`. `conflict` when that merge still conflicts; it is aborted. `left`
        when it was not tried, for a later run or the next cloud run, whose push replaces the branch: it could
        not be fetched, this checkout holds it already, its head was made less than ADOPT_MINUTES before `now`
        (a pull request opened long ago that a run carried since: that run may still be merging main in again
        or repairing its checks), it shares no history with this checkout, a local file is in the merge's way,
        or it changes a protected path (`_protected_moves`, before the merge and again after it, as the carry
        checks), or what it changes could not be listed. A cloud run never lands a protected change, so such a
        branch was pushed by someone else, and merged here its tools would run on this computer before the
        person's click."""
        branch, tracking = f"cloud/{chain}", f"refs/remotes/origin/cloud/{chain}"

        def why(guarded) -> str:   # what `_protected_moves` said kept it out
            return (f"what {branch} changes could not be listed; the next run tries again" if guarded is None else
                    f"{branch} changes a protected path; left for the next cloud run, whose push replaces it")
        if git(self.root, "fetch", "--quiet", "--no-recurse-submodules", "origin",
               f"+refs/heads/{branch}:{tracking}").returncode != 0:
            print(f"adopt: {branch} could not be fetched; the next run tries again")
            return "left"
        head = git(self.root, "rev-parse", "-q", "--verify", tracking).stdout.strip()
        bases = git(self.root, "merge-base", "--all", "HEAD", tracking).stdout.split()
        if not head or head in bases:
            return "left"   # this checkout holds it already: it goes out with this computer's push
        made = git(self.root, "show", "-s", "--format=%ct", tracking).stdout.strip()
        if made.isdigit() and now.timestamp() - int(made) < self.space.scale(ADOPT_MINUTES / 60).total_seconds():
            print(f"adopt: {branch} was pushed less than {ADOPT_MINUTES} minutes ago; left to its run")
            return "left"
        if not bases:
            print(f"adopt: {branch} shares no history with this checkout; left alone")
            return "left"
        guarded = _protected_moves(self.root, bases, "HEAD", tracking)
        if guarded is None or guarded:
            print(f"adopt: {why(guarded)}")
            return "left"
        before = git(self.root, "rev-parse", "HEAD").stdout.strip()
        if git(self.root, "merge", "--no-edit", tracking).returncode != 0:
            if git(self.root, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode != 0:
                print(f"adopt: a local change is in the way of {branch}; the next run tries again")
                return "left"
            if not self.settle():
                git(self.root, "merge", "--abort")
                print(f"adopt: pull request #{number} conflicts in a way the merge rules cannot settle; aborted")
                return "conflict"
        guarded = _protected_moves(self.root, [before], before, "HEAD")
        if guarded != []:   # git followed a file main moved
            if git(self.root, "reset", "-q", "--keep", before).returncode != 0:
                what = "that changed a protected path" if guarded else "whose changes could not be listed"
                self.error(f"an adopted {branch} {what} could not be undone")
            else:
                print(f"adopt: {why(guarded)}")
            return "left"
        print(f"adopt: pull request #{number} from {branch} merged here; it rides this computer's sync pull request")
        return "adopted"

    def name_unattached(self, record: dict) -> list:
        """The person's spaces on this computer (`chain.spaces_on_machine`) whose repository the move to the cloud
        did not attach (the record's `roots`): a space made after the move, which no chain reads now, neither the
        cloud's nor this computer's. One item on the workspace's Needs you names them, a line each, and goes once
        none is left. Its id, `cloud-roots-<hash>`, is the list's own, as `protected_item`'s is its change's: a
        space made after the person answered is asked anew, with the others still left, and the item it replaces
        goes. A space with no repository on GitHub is not named here: the move's own check names it. The
        repositories named."""
        import chain
        attached = {str(slug).lower() for slug in record.get("roots") or []}
        left: list = []
        for root in chain.spaces_on_machine(self.root):
            slug = ghrest.repo_slug(root)
            if slug and slug.lower() not in attached and slug not in left:
                left.append(slug)
        item_id = "cloud-roots-" + hashlib.sha256("\n".join(sorted(left)).encode("utf-8")).hexdigest()[:8]
        item = {"id": item_id, "kind": "request",
                "text": "\n".join(f"Your routines in the cloud do not read {slug}: it was made after you moved them."
                                  for slug in left),
                "recommendation": "say 'run my routines in the cloud' again to add it",
                "default": "it is not swept or briefed until then", "depends": 1}
        self.leg_items([item] if left else [], lambda other: other.startswith("cloud-roots") and other != item_id)
        return left

    def leg_items(self, add: list, drop) -> None:
        """The leg's items in this space's `_inbound/needs-you/sync.yaml`: each of `add` written, in place of one
        with its id (one the person answered is not asked again); each other item whose id `drop` says is
        settled taken out; the rest kept as they are. The file is written only when that changes it."""
        import needs_you
        path = self.root / SYNC_ITEMS
        before = items_in(path)
        answered = needs_you.answered_ids(self.root) if add else set()
        adding = {item["id"]: item for item in add if item["id"] not in answered}
        items = []
        for item in before:
            item_id = str(item.get("id", ""))
            if item_id in adding:
                items.append(adding.pop(item_id))
            elif not drop(item_id):
                items.append(item)
        items_out(path, items + list(adding.values()), before)

    # ----- a cloud run's landing (`--cloud <chain>`) ---------------------------------
    def cloud_land(self) -> None:
        """A cloud run's landing, in order: what it may land is sorted and its run line written
        (`prepare_cloud`), the librarian's changes and then the rest are committed, main is merged
        in with the merge rules, failing answer tests are set aside, and the branch is pushed and
        its pull request seen through (`publish_cloud`)."""
        self.prepare_cloud()
        self.land_structure()
        self.stage_and_commit()
        self.merge_in()
        self.set_aside_tests()
        self.publish_cloud()

    def prepare_cloud(self) -> None:
        """Before anything is staged: the paths that may land are sorted from the rest (`keep_out`),
        the lines the secret scan hits are held back in them (`hold_lines`), protected paths left out
        become one security item, and this run's own line is written, so that its commit carries it.
        No line when nothing changed."""
        keep = self.keep_out(self.candidates())
        if self.dry:
            return
        self.hold_lines(keep)
        keep = [rel for rel in keep if rel not in self.held]
        staged = {*keep, RUNS_PATH.as_posix()}
        if self.left_out and self.protected_item():
            staged.add(CLOUD_ITEMS.as_posix())
        if keep or self.left_out or self.held:
            self.cloud_line(len(staged))

    def protected_item(self) -> bool:
        """One security item for the protected paths this run left out, in this space's own
        `_inbound/needs-you/sync-cloud.yaml` (the sync knows only the root it lands). Its id,
        `cloud-protected-<chain>-<hash>`, is the change's own (the paths and what they now hold), so a later,
        different change is asked anew, and the same change seen again is the same item, not asked again
        once answered. Other items in the file (`cloud.py begin` writes there too) are kept as they
        are. True when the file was written."""
        import needs_you
        digest = hashlib.sha256()
        for rel in sorted(self.left_out):
            try:
                held = hashlib.sha256((self.root / rel).read_bytes()).hexdigest()
            except OSError:   # deleted, or a folder
                held = "-"
            digest.update(f"{rel}\0{held}\n".encode("utf-8"))
        item_id = f"cloud-protected-{self.cloud}-{digest.hexdigest()[:8]}"
        if item_id in needs_you.answered_ids(self.root):
            return False
        path = self.root / CLOUD_ITEMS
        before = items_in(path)
        count = len(self.left_out)
        item = {"id": item_id, "kind": "security",
                "text": "A cloud run changed 1 protected file; it was not landed." if count == 1 else
                        f"A cloud run changed {count} protected files; they were not landed.",
                "recommendation": "look at what changed in the cloud run's log before you run it again",
                "default": "nothing protected lands from the cloud", "depends": 99}
        items = [item if i.get("id") == item_id else i for i in before]
        if item not in items:
            items.append(item)
        return items_out(path, items, before)

    def cloud_line(self, staged: int) -> None:
        """A cloud run's own run line, written before its commit so that it lands with it: how many
        paths it lands, whether `begin`'s carry brought in an earlier run's branch, the files it could
        not land and how many protected paths it left out. What happens after it is committed (a second
        carry after a refused push among it) is printed for the run log; the next run's line says
        `carried` when this one did not merge."""
        if not spaceyaml.keeps_run_state(self.root):
            return   # the template keeps no run lines
        self.append_line({"routine": "sync", "cloud": self.cloud, "at": pages.iso(self.now), "space": self.space.name,
                          "cost_tokens": 0, "notes": {"staged": staged, "carried": self.carried(),
                                                      "held": sorted(self.held), "left_out": len(self.left_out)}})

    def lease(self) -> str:
        """The head of `cloud/<chain>` the next push is leased to: the one the carry saw, then the one this
        run pushed. Empty when there was none, and then the branch must not exist."""
        try:
            return (self.gdir / LEASE.format(self.cloud)).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def carried(self) -> bool:
        """Whether the carry merged an earlier run's branch into this clone: the head it leased to is in
        HEAD and not on main."""
        head = self.lease()

        def holds(ref: str) -> bool:
            return git(self.root, "merge-base", "--is-ancestor", head, ref).returncode == 0
        return bool(head) and holds("HEAD") and not holds("refs/remotes/origin/main")

    def push_cloud(self) -> bool:
        """HEAD to `cloud/<chain>`, leased (`lease`), so a run that pushed in between is never overwritten;
        the lease then moves to what was pushed. False when the push was refused or failed."""
        ref = f"refs/heads/cloud/{self.cloud}"
        if git(self.root, "push", "--quiet", f"--force-with-lease={ref}:{self.lease()}", "origin",
               f"HEAD:{ref}").returncode != 0:
            return False
        try:
            (self.gdir / LEASE.format(self.cloud)).write_text(git(self.root, "rev-parse", "HEAD").stdout.strip(),
                                                              encoding="utf-8")
        except OSError:
            pass   # the next push is refused, carries and pushes again
        return True

    def quiet_cloud(self) -> bool:
        """The branch opens no pull request yet: it changes nothing against main but run lines, marks and
        stale.yaml (`CLOUD_QUIET`), and its oldest commit main lacks is younger than QUIET_HOURS. A later
        run carries it, and a real change or the hours passing opens the pull request: a quiet day costs
        one pull request, not four. A proof run's line (`onboard.CLOUD_PROOF`) is no quiet change: the
        move to the cloud reads it on main, and the proof reads the pull request it opened."""
        out = git(self.root, "diff", "--name-only", "--no-renames", "-z", "origin/main", "HEAD").stdout
        changed = [p for p in out.split("\0") if p]
        if not changed or not all(any(fnmatch.fnmatchcase(p, rule) and p.count("/") == rule.count("/")
                                      for rule in CLOUD_QUIET) for p in changed):
            return False
        if RUNS_PATH.as_posix() in changed and self.proof_landing():
            return False
        stamps = git(self.root, "log", "--format=%ct", "origin/main..HEAD").stdout.split()
        oldest = min((int(s) for s in stamps if s.isdigit()), default=None)
        return oldest is not None and self.now.timestamp() - oldest < QUIET_HOURS * 3600

    def proof_landing(self) -> bool:
        """Whether this branch adds a proof run's line to the run lines main has."""
        import onboard
        landed = set(git(self.root, "show", f"origin/main:{RUNS_PATH.as_posix()}").stdout.splitlines())
        for raw in git(self.root, "show", f"HEAD:{RUNS_PATH.as_posix()}").stdout.splitlines():
            try:
                line = json.loads(raw) if raw not in landed else None
            except ValueError:
                continue
            if isinstance(line, dict) and line.get("routine") == onboard.CLOUD_PROOF:
                return True
        return False

    def publish_cloud(self) -> None:
        """Push to `cloud/<chain>`, never to main or a machine's branch. A refused lease (another run pushed
        meanwhile) carries that branch in and pushes once more; refused again, the run stops and says so.
        A branch the carry would not take because it changes a protected path was pushed by someone else
        after `begin`: the push replaces it, and the security item `begin` writes for such a branch is
        committed first (`pushed_item`), so it lands with this run. Then, unless the push is quiet
        (`quiet_cloud`), the pull request opens through REST and is waited for (`wait_landed`)."""
        if not self.has_origin_main() or git(self.root, "diff", "--quiet", "origin/main", "HEAD").returncode == 0:
            return   # nothing main lacks
        branch = f"cloud/{self.cloud}"
        if self.dry or self.args.no_push:
            print(f"would push to {branch}")
            return
        if not self.push_cloud():
            print(f"push: refused, as another run may have pushed to {branch}; carrying it again")
            again = carry(self.root, self.cloud, gh=self.gh)
            if again.get("conflict") or again.get("error"):
                self.error(f"the push to {branch} was refused and the branch could not be carried again "
                           f"({again.get('error') or 'a conflict'}); the next run starts from it")
                return
            if again.get("skipped") == "protected":
                self.pushed_item(again)
            if not self.push_cloud():
                self.error(f"the push to {branch} was refused twice, as other runs pushed to it meanwhile; "
                           "the next run starts from theirs")
                return
        ahead = int(git(self.root, "rev-list", "--count", "origin/main..HEAD").stdout.strip() or 0)
        self.notes["pushed"] = ahead
        print(f"push: {ahead} commit(s) to {branch}")
        if self.quiet_cloud():
            print(f"pull request: none yet, {branch} holds only run lines and marks")
            return
        pr = self.open_pr(branch, f"chore: sync (cloud {self.cloud})")
        if pr is None:
            return
        outcome = self.wait_landed(pr, float(getattr(self.args, "wait_minutes", WAIT_MINUTES)))
        self.notes["landing"] = outcome
        print(LANDING[outcome].format(pr=pr))

    def pushed_item(self, carried: dict) -> None:
        """The security item `cloud.py begin` writes for a `cloud/<chain>` branch someone else pushed with a
        protected change, here for one pushed after `begin` (`carried` is what the carry said of it). Where
        `begin` writes its items into the workspace, this one goes into the landed space's own
        `_inbound/needs-you/sync-cloud.yaml`, as `protected_item`'s does: the sync knows only its own root.
        It is committed, so that it lands with this run, whose push replaces that branch."""
        import cloud
        count = int(carried.get("protected") or 0)
        print(f"carry: cloud/{self.cloud} was pushed meanwhile with a change to {count} protected file(s); "
              "it is not used, and this push replaces it")
        item = cloud._pushed_item(self.cloud, self.space.name, carried.get("head"), count, self.now)
        if cloud.write_items(self.root, [item]):
            git(self.root, "add", "--", CLOUD_ITEMS.as_posix())
            self.commit("chore: sync")

    def wait_landed(self, pr: int, minutes: float) -> str:
        """Wait for the pull request to merge, reading it every POLL_SECONDS for at most `minutes`.
        GitHub cannot merge one that conflicts with main (two chains landing at once, in the run lines
        and a watermark): main is merged in again with the merge rules and pushed (`remerge`), at most
        CLOUD_REMERGE times. Red checks get the computer's repair once (`hold_failed`, then a push when
        it set tests aside). A failure counts once: GitHub may name the head the repair replaced for a
        while, or list a rerun's failed attempt until the new one starts, and neither is a new failure.
        `merged`; `closed` when the person closed it; `conflict` past the merges; `red` when a failure
        comes after the repair; `left_open` when the time is up. What stays open, the next cloud run
        carries and the computer's sync adopts."""
        limit, waited, remerged, repaired, seen = float(minutes) * 60, 0.0, 0, False, set()
        while True:
            state = ghrest.pull_state(self.rest, self.slug, pr)
            if state is not None:
                if state["merged"]:
                    return "merged"
                if state["state"] == "closed":
                    return "closed"
                head = state["head_sha"]
                if state["mergeable_state"] == "dirty":
                    if remerged == CLOUD_REMERGE:
                        return "conflict"
                    remerged += 1
                    self.notes["remerged"] = remerged
                    if not self.remerge():
                        return "conflict"
                elif head and (failed := self.failures(head) - seen):
                    if repaired:
                        return "red"
                    repaired, seen, before = True, seen | failed, git(self.root, "rev-parse", "HEAD").stdout.strip()
                    if not self.hold_failed(pr, head):
                        return "red"
                    if git(self.root, "rev-parse", "HEAD").stdout.strip() != before and not self.push_cloud():
                        self.error(f"push failed (cloud/{self.cloud})")
                        return "red"
            if waited >= limit:
                return "left_open"
            _sleep(POLL_SECONDS)
            waited += POLL_SECONDS

    def failures(self, head: str) -> set:
        """The failed checks of `head`, each as (head, name, link): a rerun's new attempt has a link of its own."""
        return {(head, c["name"], c["link"]) for c in ghrest.failed_checks(self.gh, self.slug, head) or []}

    def remerge(self) -> bool:
        """Main merged in again with the merge rules, and pushed with the lease: GitHub's own merge knows
        no merge rules. False when the merge stopped or the push failed."""
        errors = len(self.notes.get("errors", []))
        self.notes.pop("conflict", None)
        self.merge_in()
        if self.notes.get("conflict") or len(self.notes.get("errors", [])) > errors:
            return False
        if not self.push_cloud():
            self.error(f"push failed (cloud/{self.cloud})")
            return False
        return True

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
        self.append_line({"routine": "sync", "at": pages.iso(self.now), "space": self.space.name,
                          "cost_tokens": 0, "notes": self.notes})

    def append_line(self, line: dict) -> None:
        path = self.root / RUNS_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


def protected_paths(root: Path, paths) -> list:
    """The paths among `paths` that a cloud run neither lands nor carries, in their order: the gate's
    protected list with its minimum (`gate.protected_prefixes`), which only the person's click lands,
    and every path the routine profile keeps a routine from editing (`routine.PROTECTED_EDITS` at any
    depth and `routine.PROTECTED_IN_ROOT` at the root, as the profile's own rules read them). A cloud
    run's tools write none of them, so a change there came from somewhere else (spec 4.3). Case is
    ignored, as the gate ignores it."""
    import gate
    import routine
    prefixes, _ = gate.protected_prefixes(root)
    rules = [(rule, True) for rule in routine.PROTECTED_EDITS] + [(rule, False) for rule in routine.PROTECTED_IN_ROOT]
    found = []
    for rel in paths:
        posix = gate._posix(rel)
        if any(gate._under(posix, prefix) for prefix in prefixes) \
                or any(_edit_denied(posix, rule, anywhere) for rule, anywhere in rules):
            found.append(rel)
    return found


def protected_changes(root: Path, paths, before: list, after: str | None) -> list:
    """The paths among `paths` whose change a cloud run neither lands nor carries: the protected ones
    (`protected_paths`), less the answer tests' file where it holds at `after` (a commit, `:` the index,
    None the working tree) what it holds at each commit of `before` but for questions set aside or
    brought back (`set_aside_only`). That is the sync's own repair (`set_aside_tests`, `hold_failed`),
    which a cloud run lands and the next one carries like any page."""
    import tests_run  # noqa: F401  (read now, as protected_paths reads gate and routine: before any carry's merge)
    return [rel for rel in protected_paths(root, paths)
            if not all(set_aside_only(root, rel, ref, after) for ref in before)]


def set_aside_only(root: Path, rel: str, before: str, after: str | None) -> bool:
    """Whether `rel` is the answer tests' file (`tests_run.ANSWERS_PATH`) and reads at `after` (a commit, `:`
    the index, None the working tree) as at the commit `before` but for the `held:` records of its
    questions. A change to a question itself, or a file either side cannot read, is no such change."""
    import tests_run
    import yaml
    if rel != tests_run.ANSWERS_PATH.as_posix():
        return False

    def text(ref: str | None) -> str | None:
        if ref is None:
            try:
                return (Path(root) / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return None
        done = git(root, "show", f"{'' if ref == ':' else ref}:{rel}")
        return done.stdout if done.returncode == 0 else None

    def bare(doc):
        questions = doc.get("questions") if isinstance(doc, dict) else None
        if not isinstance(questions, list):
            return doc
        return {**doc, "questions": [{k: v for k, v in q.items() if k != "held"} if isinstance(q, dict) else q
                                     for q in questions]}

    sides = [text(before), text(after)]
    if None in sides:
        return False
    try:
        old, new = (yaml.safe_load(side) for side in sides)
    except yaml.YAMLError:
        return False
    return bare(old) == bare(new)


def _edit_denied(rel: str, rule: str, anywhere: bool) -> bool:
    """`rel` falls under one of the profile's Edit rules: `<folder>/**` is the folder and all in it, any
    other rule one file; `anywhere` at any depth (the profile's `//**/`), else at the root."""
    parts = rel.casefold().split("/")
    tails = ["/".join(parts[i:]) for i in range(len(parts) if anywhere else 1)]
    rule = rule.casefold()
    globs = (rule[:-3], rule[:-3] + "/*") if rule.endswith("/**") else (rule,)   # fnmatch's * crosses a /
    return any(fnmatch.fnmatchcase(tail, glob) for tail in tails for glob in globs)


def _protected_moves(root: Path, bases: list, ours: str, theirs: str) -> list | None:
    """The protected paths (`protected_changes`) where merging `theirs` into `ours` can change a file: one `theirs`
    holds otherwise than some merge base does (git merges against all of them, so a branch that matches one base
    can still undo what another holds), otherwise than `ours`, and otherwise than origin/main, whose version landed
    through the gate. Where `theirs` matches every base the merge keeps ours; where it differs from every base only
    in answer tests set aside or brought back, the merge changes no more than that. None when git could not list
    them."""
    def differ(ref: str) -> set | None:
        done = git(root, "diff", "--name-only", "--no-renames", "-z", ref, theirs)
        return {p for p in done.stdout.split("\0") if p} if done.returncode == 0 else None

    found: set = set()
    for ref in bases:
        moved = differ(ref)
        if moved is None:
            return None
        found |= moved
    for ref in (ours, "refs/remotes/origin/main"):   # a file one of them holds already is no change
        moved = differ(ref)
        if moved is None:
            return None
        found &= moved
    return protected_changes(root, sorted(found), bases, theirs)


@monitor_off()
def carry(root: Path, chain: str, gh=None) -> dict:
    """Merge the earlier cloud run's `cloud/<chain>` branch that never merged into this clone, at the
    start of a cloud run (`cloud.py begin`), before anything is exported or written. Then the cursors
    and watermarks already hold what the earlier run read, and the new run never writes the same
    witness pages, queue items, tracker issues or notices again; its push carries both runs.

    A branch that is not on origin is no error: `{"carried": False, "head": None}`, quietly, and
    GitHub is not asked. A branch whose head is a pull request the person closed without merging is
    left alone (`"skipped": "closed"`): they turned that work down, and the new run's push replaces
    it. A clone too shallow to share a commit with the branch fetches more history (`DEEPEN`
    commits, `DEEPEN_TRIES` times), then all of it; histories that still share nothing are never
    merged. A branch that changes a protected path (`protected_changes`) is never merged either
    (`"skipped": "protected"`, with their number): a cloud run never pushes one, so someone else
    pushed it, and merging it would let them change what this run executes and may do, before the
    guard profile is written. The answer tests an earlier run set aside, its own repair of red
    checks, are the one such change it carries. The branch is checked against every merge base, as
    git merges against all of them, and a file it holds as this clone or origin/main already holds it
    is no change (`_protected_moves`). What the merge did is checked again, since git follows a file
    main moved, and undone when a protected file then reads otherwise. The new run's push replaces
    the branch.
    The merge runs with the merge rules (`resolve`, `keep_ours`, as `merge_in`); one that still
    conflicts is aborted (`"conflict": True`, with what kept it from settling in `"error"`), and the
    run must not work on top of it. A branch main already holds is not carried again.
    `{"carried": True, "head": <sha>}` when it merged.

    `"error"` says what could not be done (origin not reached, space.yaml not read, or a merge that
    changed a protected path and that `reset --keep` could not take back, which leaves that merge in
    the clone). In every case with `"error"` or `"conflict"` the run stops: it never works on top of
    what the carry left. A chain that is not one of `CLOUD_CHAINS` is a ValueError. Whatever
    happened, the head the fetch saw (empty when there was none) is written to `LEASE` in the git
    dir: the cloud push is leased to it, so a run that pushed in between is never overwritten. Every
    git it starts runs with a mount's file monitor off (`monitor_off`)."""
    if chain not in CLOUD_CHAINS:
        raise ValueError(f"no cloud chain {chain!r}: {', '.join(CLOUD_CHAINS)}")
    root = Path(root).resolve()
    gdir = git_dir(root)
    result: dict = {"carried": False, "head": None}
    if gdir is None:
        result["error"] = "not a git repository"
        return result
    branch, tracking = f"cloud/{chain}", f"refs/remotes/origin/cloud/{chain}"
    specs = ("+refs/heads/main:refs/remotes/origin/main", f"+refs/heads/{branch}:{tracking}")
    try:
        listed = git(root, "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}")
        if listed.returncode == 2:   # no such branch on origin
            git(root, "update-ref", "-d", tracking)
            return result
        if listed.returncode != 0 or git(root, "fetch", "--quiet", "--no-recurse-submodules", "origin",
                                         *specs).returncode != 0:
            result["error"] = "the branch could not be fetched"
            return result
        head = git(root, "rev-parse", "-q", "--verify", tracking).stdout.strip()
        result["head"] = head or None
        slug = ghrest.repo_slug(root)
        if not head or (slug and ghrest.closed_unmerged(gh or ghrest.runner(root), slug, branch, head)):
            if head:
                result["skipped"] = "closed"
            return result

        def shallow() -> bool:
            return git(root, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"

        def common() -> list:
            return git(root, "merge-base", "--all", "HEAD", tracking).stdout.split()

        bases, tries = common(), 0
        while not bases and tries < DEEPEN_TRIES and shallow():
            git(root, "fetch", "--quiet", "--no-recurse-submodules", f"--deepen={DEEPEN}", "origin", *specs)
            bases, tries = common(), tries + 1
        if not bases and shallow():
            git(root, "fetch", "--quiet", "--no-recurse-submodules", "--unshallow", "origin", *specs)
            bases = common()
        if not bases:   # nothing in common, ever: never --allow-unrelated-histories
            result["conflict"] = True
            return result
        if head in bases:
            return result   # main holds it already
        guarded = _protected_moves(root, bases, "HEAD", tracking)
        if guarded is None:
            result["error"] = "the branch's changes could not be listed"
            return result
        if guarded:
            result.update(skipped="protected", protected=len(guarded))
            return result
        try:
            run = Run(root, gdir, parse(["--root", str(root)]))
        except Exception as exc:   # the merge rules need the space; said, never raised into begin
            result["error"] = f"space.yaml could not be read ({type(exc).__name__})"
            return result
        run.merge_rules()
        before = git(root, "rev-parse", "HEAD").stdout.strip()
        done = git(root, "merge", "--no-edit", tracking)
        if done.returncode != 0 and not run.settle():
            git(root, "merge", "--abort")
            result["conflict"] = True
            if run.notes.get("errors"):
                result["error"] = "; ".join(run.notes["errors"])
            return result
        # what the merge did, file by file: git follows a file main moved, so a change the branch made to a plain
        # file can land under a protected path its own paths never named. The lists were read before the merge
        # (gate, routine and tests_run are imported), and a space.yaml the merge changed is protected itself
        guarded = _protected_moves(root, [before], before, "HEAD")
        if guarded != []:
            if git(root, "reset", "-q", "--keep", before).returncode != 0:
                result["error"] = "a carry that changed a protected path could not be undone"
            elif guarded is None:
                result["error"] = "the carry's changes could not be listed"
            else:
                result.update(skipped="protected", protected=len(guarded))
            return result
        result["carried"] = True
        return result
    finally:
        try:
            (gdir / LEASE.format(chain)).write_text(result["head"] or "", encoding="utf-8")
        except OSError as exc:
            result["error"] = f"the lease could not be written ({type(exc).__name__})"


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
    parser.add_argument("--merge-latest", nargs=3, default=None, metavar=("BASE", "OURS", "THEIRS"))
    parser.add_argument("--cloud", choices=CLOUD_CHAINS, default=None)
    parser.add_argument("--wait-minutes", type=float, default=WAIT_MINUTES)
    args = parser.parse_args(argv)
    if args.cloud and (args.release_leg or args.only):
        parser.error("--cloud lands a cloud run whole: not with --release-leg or --only")
    return args


def main(argv=None) -> int:
    args = parse(sys.argv[1:] if argv is None else argv)
    if args.merge_latest:   # git runs the watermark driver mid-merge: no lock, no run line
        return merge_latest(*(Path(p) for p in args.merge_latest))
    if args.cloud:   # every git a cloud run's landing starts, a mount's included, with the file monitor off
        with monitor_off():
            return sync_once(args)
    return sync_once(args)


def sync_once(args) -> int:
    """One run of the sync, as `main` read its arguments: the exit code."""
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
        if args.cloud:
            run.cloud_land()
        else:
            # while the routines run in the cloud, the computer's leg runs after the merge, `not_started` with it
            leg = not args.only and not run.dry and run.cloud_host() is not None
            if not args.only and not run.dry and not leg:
                run.watch_chains()
            if args.release_leg and not args.only:
                run.release_leg()
            run.land_structure()
            run.stage_and_commit()
            if not args.only:
                run.advance_pins()
            run.merge_in()
            if leg:
                run.computer_leg(run.now)
            if not args.only:
                run.set_aside_tests()
                if not run.dry and not args.no_push:
                    run.heal()
            run.publish()
    except Exception as exc:  # unexpected: record, never lose the lock
        run.error(f"{type(exc).__name__}: {str(exc)[:200]}")
    finally:
        if not run.dry and not args.cloud:   # a cloud run's line went into its commit (`cloud_line`)
            run.record()
        run.unlock()
    code = ERROR if run.notes.get("errors") else OK
    if not args.only and not run.dry and not args.cloud:   # the progress page is the computer's
        refresh_page(root)
    return code


if __name__ == "__main__":
    sys.exit(main())
