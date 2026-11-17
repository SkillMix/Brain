"""sync: land work without human hands (vision section 12, landing design section 3).

The one tool that runs git. The OS scheduler runs it every 15 minutes; sessions and
routines only edit files. Each run:

  1. take `.git/sync.lock`; skip if another run holds it or git is mid-operation (a merge, a rebase
     or an index lock; one left behind for BEHIND_HOURS is one Needs-you item, `left_behind`)
  1a. name in stale.yaml each chain whose scheduler stopped starting it and each stage whose last
     run is past its window (`not_started`: `<chain> not started`, `<stage> stale`); while
     the routines run in the cloud, after step 5 instead (the computer's leg, below)
  2. stage tracked changes and settled untracked text files, never under personal/,
     academic/, _scratch/, _meta/state/private/, _meta/state/feed/ or inside a mount, and never
     a file whose name is a credential file's (`.env`, `*.pem`, `id_rsa`, `scan.credential_file`;
     a Keynote deck, a `.key` that is a zip with no PEM header, is none), tracked or not,
     whatever it holds
  3. scan what the index holds for each staged path, the blob the commit takes (never the
     working tree, which may have changed since the add), whatever its size or encoding (a
     large blob streamed out of git and read in chunks, `scan.scan_blob`), and for a Git LFS
     pointer the content LFS would upload; unstage every file with a finding, one the scan
     could not read whole and a credential file staged by hand included (count recorded,
     never the match); commit `chore: sync`. Every commit the sync makes outside a merge scans
     the index again first (`commit`), so a file staged by hand meanwhile is judged too
  4. advance the pin of every clean mount to its remote branch; commit `chore: pins`. A mount with
     local changes is left alone and named in the run line (`pins_held`); one held for PIN_DAYS is
     `<mount> pin stale` in stale.yaml
  5. fetch origin and merge origin/main in (append-only files by union, and the record of which
     computer runs the routines: `MERGE_RULES` is written into the space's own git first, so a
     space whose .gitattributes is older has it too). The rules settle what GitHub's own merge
     cannot, so no merge wedges on them: a watermark keeps the later mark key by key (the
     `--merge-latest` driver), generated pages and state keep this checkout's side (so do the
     sessions list, the items the computer's leg writes again and template sync's provenance),
     and an item under _inbound/ or _outbound/ that one side deleted stays deleted (`resolve`).
     A conflict where origin holds only an earlier copy of this checkout's own change keeps ours
     (the rebuilt commits of step 6 share no history with it); any other conflict is
     aborted and recorded. A fetch that fails, a merge that stops or a run that dies, for
     BEHIND_HOURS and BEHIND_RUNS runs in a row, is one Needs-you item, with since when and what
     is in the way (`behind_health`)
  5m. when stale.yaml says `daily not started`: run the daily chain's steps that need no model
     (`model_free_daily`: status with landing and the id index, access, requests, release, the
     tests; no morning view and no template sync), at most once a day and only on the computer
     that runs the daily; the entry stays until an agent's own daily runs
  5s. one Needs-you item for everything stale.yaml says did not run in time (`stale_item`), named
     in the run line as `stale`; none while the routines are paused
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
     machine's name) and open the pull request the platform merges. Every path the gate would
     hold goes to its own branch sync/<machine>-protected (its own pull request, the owner's one
     click; `held_paths`): a `protected:` path or another name the gate holds
     (`gate.protected_reason`), a file template sync manages, what the gate reads against
     origin/main (a registry trust field, log.md not only appended to, a new security item on
     Needs you, a stable page not signed here, a link), and whatever the gate's own verdict on the
     plain part still holds, so the journal, desk and run lines never wait behind them; each branch is rebuilt on
     origin/main and pushed only when its content changed (main moving under a waiting pull
     request is no reason to rebuild it: every push reruns the checks). A change that is
     only run lines and mount pins goes out at most every QUIET_HOURS, one that is only
     journal lines and run logs at most every BATCH_HOURS, or with the next real change, so
     nobody watching the repository gets a pull request (and a paid check) every 15 minutes
  7. append one run line to _meta/state/runs.jsonl when something happened, plus at
     most one heartbeat a day; the line rides along in the next run's commit. What the run left
     on this computer (a scan finding, a file it could not read whole, a credential file's name)
     is written with why and since when to `_meta/state/private/left-local.json`, which never
     leaves it, counted in the run line (`left_local`), and one Needs-you item once a path has
     stayed local LEFT_LOCAL_DAYS days (`left_local_health`); never silent
  8. after the lock is released: rewrite the progress page (`~/.spaces/onboarding.html`, outside the
     repository) from the workspace, without the model (`onboarding_page.refresh`); not for a dry
     run or `--only`

    python sync.py [--root R] [--dry-run] [--no-push] [--release-leg]
                   [--min-age-minutes N] [--only PATH ...] [--message TEXT]
    python sync.py --root R --cloud sweep [--wait-minutes N]
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
is the sync's own: answer tests set aside or brought back (`protected_changes`). What the gate would
hold of the rest (that security item, a registry home a classification set) goes on its own branch
`cloud/<chain>-protected`, a commit on main with its own pull request, and back to main's copy on
`cloud/<chain>` (`split_cloud`), so the cloud pull request lands on green. Nor does a path in
a private zone, a mount or a nested checkout, or any pin, even one staged by force. Every git it
starts runs with a mount's own file monitor off (`monitor_off`), a command a mount's git config
could name. A line the secret scan hits is replaced by `[a line held back: <kind>]` (a private key
from its BEGIN line to its END line) and the page lands, since the clone is thrown away; a file over
1 MB (the tools' own logs, CLOUD_LOGS, at any size the scan reads whole: a run line must land), not
text, or that would not read with its lines held back is named in the run line. After the
merge of main and the answer tests (steps 5 and 5a), the push goes to `cloud/<chain>`, leased to the
head the carry saw; refused, it carries again and pushes once more. Run lines, marks and stale.yaml
alone open no pull request until the branch's oldest commit main lacks is QUIET_HOURS old, unless
they hold a proof run's line; anything else opens one through REST and waits for its merge, merging
main in again while it conflicts and repairing red checks once. A run whose carry brought in a head
whose pull request the person closed without merging while it worked pushes nothing. What happened
after the push is printed for the run log, and the next run's line says whether it carried this one.

The computer's leg (spec 4.5, `computer_leg`) runs while the workspace's routine record says the
sweep runs in the cloud (`runner: cloud`; for a space that is no workspace, its owner's workspace
on this computer). It comes right after step 5, so it reads the cloud's run lines first, and does
what only the computer has: `not_started` moves there from step 1a, waits for a run that merged
main (offline, or stopped by a conflict, it would not see the cloud's lines), and reads the run lines
the fetched `cloud/<chain>` branches hold too, where a quiet run's lines wait; the transcripts scan
runs at most every `chain.TRANSCRIPTS_HOURS`, where the session files are on this computer, its
clock kept outside the repository (`~/.spaces/sync-leg-<space>.json`); a
`cloud/<chain>` pull request open for ADOPT_MINUTES, in conflict with main or red, whose head is as
old, is merged here with the merge rules and rides this computer's own pull request (a push split
for a protected path keeps its head as a parent of the plain part), and one that still conflicts is
left to the next cloud run, which sets it aside and starts fresh from main; and the workspace names on
Needs you a space of the
person's that the move did not attach. All but `not_started` run only on the computer the record
names (`keeps_leg`); a record that names none lets each computer run them, and the merge rules keep
what two computers both write from stopping a merge. The scan waits while the routines are paused.
Template sync and AGENTS.md need no leg: the computer the record names keeps its daily chain. What
the leg wrote is committed before step 5a.

`--release-leg` runs `release.ship(root)` first when that module is present.
`--merge-latest` is the watermark driver git runs during a merge (`merge_latest`); nobody types it.
In a cloud run (`CLAUDE_CODE_REMOTE` is `true`) only `--cloud <chain>` and that driver run: anything
else is refused, as it would run the computer's landing in the clone.
Exit 0 on success, 2 when the run was skipped, 1 on an error.

`carry(root, chain)` is not a run of its own: a cloud run's `cloud.py begin` calls it first, to
merge in the earlier run's `cloud/<chain>` branch, which never merged, before anything is written.
A branch that changes a protected path (`protected_changes`) was pushed by someone else and is never
merged. Its git, too, runs with a mount's file monitor off.

A symbolic link (or a submodule no `.gitmodules` names) counts as a protected change wherever it
sits (`_link_moves`): a branch that adds one is never adopted or carried, a merge of origin/main
that brings one in is undone (`git reset --keep`) and named as a security item, and a cloud run
never lands one, nor the person's verdict store (`_meta/state/verdicts.jsonl`): a cloud run never
signs. Lines held back are written through `pages`, never through a link.
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
import tempfile
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
           "healing", "healed", "pr_red", "adopted", "gap_hours")
HEAL_STATE = "sync-heal.json"   # in the git dir: the repair waiting to be seen merged
HEAL_RERUNS = 3   # reruns of one sync pull request's failed checks a day, at most
CHAINS = {"sweep_chain": "sweep-chain", "daily": "daily-chain"}   # run line routine: its routine prompt
# A stage that ran here and then stopped is named in stale.yaml as `<stage> stale` (`not_started`), since its last
# run: the sweep chain's stages once that is older than twice the longest gap in the sweep's schedule, as a chain
# that did not start is; status (landing runs inside it) and release after STAGE_HOURS. A mount the sync could not
# move for PIN_DAYS is `<mount> pin stale` (`pins_held`). One Needs-you item names them all (`stale_item`).
SWEEP_STAGES = ("sweep", "distill", "route")
STAGE_HOURS = {"status": 48, "release": 48}
STAGE_STALE = "{} stale"
PIN_DAYS = 7
SYNC_GAP_HOURS = 2   # the sync cannot be late while it runs: the next run says how long it had stopped (`sync_gap`)
STALE_ITEM = "sync-stale-"
ROUTE_WAITS = "route waits"   # in `last_ran`: since when records wait for the router


# Written into every space's own git, whatever its .gitattributes says. The record of who runs the routines merges by
# union: two computers that claim them before either synced leave a record that reads as unclear (nobody runs) instead
# of a conflict that wedges the sync. A watermark keeps the later mark (`merge_latest`); generated pages and state keep
# this checkout's side, since the next status run writes them again from the files (spec 4.5a). So do the files the
# computer's leg writes again on its next run (`computer_leg`): the sessions the transcripts scan saw, the leg's own
# items, and template sync's provenance, which every weekly sync writes again with the second it ran. Two computers
# that both run the leg (a record that names no computer for it) fall due for template sync together, and two syncs of
# the same release differ only in that second; they never stop a merge on any of these. Nor does the health store,
# whose rows each writer writes again on its next run, one computer's apart from another's (`needs_you.set_health`).
MERGE_RULES = ("_meta/state/routine-host.yaml merge=union",
               "_meta/state/*-watermark.yaml merge=spaces-latest",
               "live/** merge=spaces-ours",
               "_meta/state/health/** merge=spaces-ours",
               "_meta/state/ids.yaml merge=spaces-ours",
               "_meta/state/stale.yaml merge=spaces-ours",
               "_meta/state/sessions.yaml merge=spaces-ours",
               "_inbound/needs-you/sync.yaml merge=spaces-ours",
               "_meta/state/template-version.yaml merge=spaces-ours")
SETTLED_BY_DELETION = ("_inbound/", "_outbound/")   # an answer or a shipping is the later fact than any change
CLOUD_CHAINS = ("sweep",)   # the chain that can run in the cloud; it lands on its own branch, cloud/<chain>
HELD_FOR = "held for {}"   # the body line of the commit that puts held paths back to main's copy (`split_cloud`)
LEASE = "sync-cloud-lease-{}"   # in the git dir: the cloud/<chain> head the carry saw, empty when there was none
DEEPEN, DEEPEN_TRIES = 200, 3   # a shallow clone fetches this much more history, this often, before it unshallows
# A cloud run's landing (`--cloud`): these alone push its branch and open no pull request (`quiet_cloud`), unless the
# run lines hold a proof run's line. The routine meter is protected, so it never lands from one.
CLOUD_QUIET = ("_meta/state/runs.jsonl", "_meta/state/stale.yaml", "_meta/state/*-watermark.yaml")
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"   # a Git LFS pointer's first line
LFS_OID = re.compile(rb"(?m)^oid sha256:([0-9a-f]{64})$")
CLOUD_MAX_BYTES = 1_000_000   # a file larger than this, or not text, is named in the run line and never landed
# The tools' own append-only logs grow by a line with every run and pass that size for good one day: held back,
# a cloud run's own run line and the router's log would never land again, and the health signals would say the
# cloud is not running. They land at any size the secret scan still reads whole (`scan.SCAN_MAX_BYTES`, 25 MB:
# years of run lines; past it a log is held again, and it is time to start a new one).
CLOUD_LOGS = ("_meta/state/*.jsonl", "log.md")
CLOUD_ITEMS = Path("_inbound") / "needs-you" / "sync-cloud.yaml"
CLOUD_REMERGE = 3   # merges of main while the pull request conflicts with it, before it is left open
POLL_SECONDS, WAIT_MINUTES = 20, 9   # how often a cloud run reads its pull request, and for how long at most
# The computer's leg, while the routines run in the cloud (`computer_leg`)
ADOPT_MINUTES = 30   # a cloud pull request open this long, in conflict or red, is merged here: its run waits less
ADOPTED = "sync-adopted"   # in the git dir: the cloud heads merged here (`adopt`) that main does not hold yet
LEG_STATE = "sync-leg-{}.json"   # under ~/.spaces/, beside the progress page: the leg's clocks for one space
HEALTH_STATE = "sync-health-{}.json"   # beside it: what the sync keeps about itself on this computer (`health_state`)
SYNC_ITEMS = Path("_inbound") / "needs-you" / "sync.yaml"   # the leg's items on Needs you
# A failed fetch and a merge that stops are tried again on the next run, and a run is skipped while git is
# mid-operation: right for an hour, and silent for good when the cause stays. Once the same cause has kept this
# computer from bringing main in for BEHIND_HOURS, it is one Needs-you item (`behind_item`).
BEHIND_HOURS = 24
BEHIND_RUNS = 4   # and this many runs in a row: one failed run after a weekend asleep is no alarm
BEHIND_ITEM = "sync-behind-"
# What the sync left on this computer, why and since when (`Run.left_local_health`): in the private zone, never staged
LEFT_LOCAL = spaceyaml.PRIVATE_STATE / "left-local.json"
LEFT_LOCAL_DAYS = 7   # a path left local this long becomes one Needs-you item
LEFT_LOCAL_ITEM = "sync-left-local-"
# A run on the computer reads staged files for at most this long in all; one not reached is left local unjudged
# (`NOT_YET`) and read on a later run, so a run stays well inside its lock (LOCK_HOURS) and the journal keeps landing.
SCAN_RUN_SECONDS = 600
NOT_YET = "not_scanned_yet"
VERIFY_REF = "spaces-sync/verify"   # no such ref: the plain part the gate judges (`verify_plain`) is always built anew
VERDICTS = "_meta/state/verdicts.jsonl"   # the person's answers (status.record_verdict): a cloud run never lands one
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
    "left_open": "pull request #{pr} has not merged yet; it stays open and merges once its checks are green, the next "
                 "cloud run carries it, and the computer's sync adopts it only if it is still in conflict or red after "
                 "half an hour",
    "turned_down": "the pull request from {branch} was closed without merging while this run worked, so nothing is "
                   "pushed or opened: the next cloud run starts again from main"}


def _sleep(seconds: float) -> None:
    """The wait between two reads of a cloud run's pull request (`wait_landed`); the tests replace it."""
    time.sleep(seconds)


def git(root, *args, check: bool = False, input: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", input=input, check=check,
                          env=dict(os.environ, **env) if env else None)


def monitor_off():
    """Every git started inside it runs with the file monitor off, a mount's git included: a mount's own git config
    could name one, a command its git runs whenever the space's git looks into the mount (`git status`, and the diff
    that lists a merge's conflicts). A cloud run's landing and its carry run inside it. Also a decorator."""
    return git_config(("core.fsmonitor", "false"))


def signing_key() -> Path:
    """The computer's own commit-signing key, outside every repository: a cloud run never holds it."""
    return Path.home() / ".spaces" / "signing_key"


SIGNING = False   # inside `signing_on` with a key git can sign with: `subset_commit` signs what it pushes too
CANNOT_SIGN = "this computer cannot sign its commits"   # its stale.yaml entry (`signing_health`)


@contextlib.contextmanager
def signing_on():
    """Every commit and merge started inside it is signed with the computer's key (`signing_key`), when there is
    one git can sign with: the gate trusts a new signature on a stable page only from a commit that key signed
    (`gate._key_signed`). Yields why it cannot sign (`cannot_sign`), else None; the run then commits unsigned,
    so everything but a signature still lands on green."""
    global SIGNING
    key = signing_key()
    why = cannot_sign(key) if key.is_file() else None
    if not key.is_file() or why:
        yield why
        return
    with git_config(("gpg.format", "ssh"), ("user.signingkey", key.as_posix()), ("commit.gpgsign", "true")):
        SIGNING = True
        try:
            yield None
        finally:
            SIGNING = False


def cannot_sign(key: Path) -> str | None:
    """Why `ssh-keygen -Y sign`, which git runs for an ssh signature, fails with `key`; None when it signs."""
    doctor.use_tools()   # the ssh-keygen git will find is the one on the PATH the run gives it
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe"
        probe.write_text("probe\n", encoding="utf-8")
        try:
            done = subprocess.run(["ssh-keygen", "-Y", "sign", "-n", "git", "-f", str(key), str(probe)],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"ssh-keygen: {type(exc).__name__}"
    if done.returncode == 0:
        return None
    said = (done.stderr.strip().splitlines() or ["ssh-keygen failed"])[-1]
    # the key's path names this computer's user: stale.yaml lands, so only its home-relative name goes there
    for form in (str(key).replace("\\", "\\\\"), str(key), key.as_posix()):
        said = said.replace(form, "~/.spaces/signing_key")
    return re.sub(r"\S*" + re.escape(key.name), "~/.spaces/signing_key", said)[:120]   # any other spelling of it


def signing_health(root: Path, why: str | None, now: dt.datetime) -> None:
    """Name `why` in stale.yaml (`CANNOT_SIGN: <why>`), for the health line and the morning view, until the
    computer signs again; written only when it changes."""
    import chain
    import yaml
    path = root / chain.STALE
    try:
        data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    except (OSError, yaml.YAMLError):
        return
    if not isinstance(data, dict):
        return
    want = {k: v for k, v in data.items() if not str(k).startswith(CANNOT_SIGN)}
    if why:
        key = f"{CANNOT_SIGN}: {why}"
        want[key] = data.get(key) or pages.iso(now)
    if want != data:
        pages.write_text(path, yaml.safe_dump(want, sort_keys=True), root=root)


def keep_signing_key(root: Path) -> str | None:
    """Once main lists signing keys (`gate._key_signed`), every computer's sync makes its own key when it has none
    and lists it in the space (`make_signing_key`; the line lands on the person's one click), so no computer
    commits unsigned unseen: a second one, one set up before the tie, any while the routines run in the cloud.
    Why it cannot (for `signing_health`), else None. Only the computer's run calls it: a cloud run holds no key."""
    import gate
    if not (gate._show(root, "origin/main", gate.ALLOWED_SIGNERS) or "").strip():
        return None   # the tie is opt-in: nothing waits on a key yet
    try:
        local = root / gate.ALLOWED_SIGNERS
        if signing_key().is_file() and local.is_file() and lists(local.read_text(encoding="utf-8"), own_signer()):
            return None
        if make_signing_key(root) == OK:
            return None
    except (OSError, subprocess.TimeoutExpired):
        pass
    # the person's step, not a command: the sync already retries make_signing_key every run
    return ("ssh-keygen could not make or read ~/.spaces/signing_key: install OpenSSH, or remove a broken key; "
            "the next sync makes it")


def own_signer() -> str:
    """This computer's line in `.github/allowed_signers`, `sync <keytype> <key>` with nothing after the key (the
    only kind of line the gate reads, `gate._key_signed`); '' without a readable public half."""
    try:
        return "sync " + " ".join(Path(f"{signing_key()}.pub").read_text(encoding="utf-8").split()[:2])
    except OSError:
        return ""


def lists(text: str, line: str) -> bool:
    """Whether `text` holds `line` as a whole line: a key in another line's comment lists nothing."""
    return bool(line) and line in {" ".join(have.split()) for have in text.splitlines()}


def make_signing_key(root: Path) -> int:
    """`--signing-key`: make the computer's signing key when it has none (its public half again when only that is
    gone), and add the public half to the space's `.github/allowed_signers` (`gate.ALLOWED_SIGNERS`), which is
    protected: it lands on the person's one click."""
    import gate
    key = signing_key()
    pub = Path(f"{key}.pub")
    if not key.is_file():
        key.parent.mkdir(parents=True, exist_ok=True)
        try:
            done = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "spaces-sync", "-f", str(key)],
                                  capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            done = subprocess.CompletedProcess([], 1, "", type(exc).__name__)
        if done.returncode != 0:
            print(f"sync: ssh-keygen could not make the signing key ({done.stderr.strip()[:200]})")
            return ERROR
    elif not pub.is_file():
        try:
            done = subprocess.run(["ssh-keygen", "-y", "-f", str(key)], capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            done = subprocess.CompletedProcess([], 1, "", type(exc).__name__)
        if done.returncode != 0 or len(done.stdout.split()) < 2:
            print(f"sync: ssh-keygen could not read the signing key's public half back ({done.stderr.strip()[:200]})")
            return ERROR
        pub.write_text(done.stdout, encoding="utf-8", newline="")
    line = own_signer()
    path = root / gate.ALLOWED_SIGNERS
    have = path.read_text(encoding="utf-8") if path.is_file() else ""
    if lists(have, line):
        print("sync: this computer's signing key is in .github/allowed_signers already")
        return OK
    pages.write_text(path, have + ("" if not have or have.endswith("\n") else "\n") + f"{line}\n",
                     root=root, newline="")
    print("sync: this computer's signing key is added to .github/allowed_signers; it lands on the person's click "
          f"(its line is `sync <keytype> <key>` with nothing after the key, and the key ends in {line[-16:]}: "
          "a line that reads otherwise is no computer's of theirs)")
    return OK


@contextlib.contextmanager
def git_config(*pairs):
    """Every git started inside it runs with `pairs` (key, value) set through git's own environment
    (GIT_CONFIG_COUNT with its KEYs and VALUEs), which git hands to a mount's git, after the config the environment
    holds already, which keeps its effect; the environment is put back as it was after. A count git would refuse
    is left as it is."""
    count = os.environ.get("GIT_CONFIG_COUNT") or "0"
    if not count.isdigit():
        yield
        return
    first = int(count)
    keys = ["GIT_CONFIG_COUNT"] + [f"GIT_CONFIG_{kind}_{first + i}" for i in range(len(pairs)) for kind in ("KEY", "VALUE")]
    before = {key: os.environ.get(key) for key in keys}
    os.environ.update(zip(keys, [str(first + len(pairs))] + [part for pair in pairs for part in pair]))
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


def last_ran(root: Path, cloud: bool = False) -> dict:
    """{name: when it last ran} for every routine with a run line in `_meta/state/runs.jsonl` (a chain, a stage
    of one, the sync itself), and `route` from the router's own log, routing.jsonl, since it writes no run line.
    `cloud` reads the run lines the fetched `cloud/<chain>` branches hold as well. ROUTE_WAITS is what the router
    is judged by, when records wait for it: the oldest sweep since its last decision that wrote a witness page.
    With none it is absent, so a quiet weekend with nothing to route is not a router that stopped."""
    root = Path(root)
    path = root / RUNS_PATH
    texts = [path.read_text(encoding="utf-8", errors="replace")] if path.exists() else []
    for name in CLOUD_CHAINS if cloud else ():
        held = git(root, "show", f"refs/remotes/origin/cloud/{name}:{RUNS_PATH.as_posix()}")
        texts += [held.stdout] if held.returncode == 0 else []
    last, fed = {}, []
    for raw in (line for text in texts for line in text.splitlines()):
        try:
            row = json.loads(raw)
            name, at = str(row["routine"]), pages.parse_iso(row["at"])
            last[name] = max(at, last.get(name, at))
            if name == "sweep" and isinstance(row.get("notes"), dict) and row["notes"].get("witness"):
                fed.append(at)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    try:
        rows = (root / "_meta" / "state" / "routing.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        rows = []
    for raw in rows:
        try:
            at = pages.parse_iso(json.loads(raw)["at"])
            last["route"] = max(at, last.get("route", at))
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    waiting = [at for at in fed if at > last["route"]] if "route" in last else []
    if waiting:
        last[ROUTE_WAITS] = min(waiting)
    return last


def not_started(root: Path, now: dt.datetime | None = None, cloud: bool = False) -> bool:
    """A chain its agent scheduler never starts writes nothing, so only this script, which the OS
    runs whatever that scheduler does, can notice. A chain whose last run line (for one that never
    ran, the onboarding) is older than twice the longest gap in its routine's schedule is named
    in stale.yaml as `<routine> not started` since then, for the health line; the entry goes when
    the chain runs. A stage that ran here once and whose last run is past its window is named
    `<stage> stale` the same way (SWEEP_STAGES, STAGE_HOURS; the router only where `routing:` is
    on, and only while records wait for it). Written only when it changes. True when it wrote. While the routines run in the
    cloud the sync asks it after merging main in (`Run.computer_leg`), so the cloud's run lines are
    read first, and `cloud` reads those its `cloud/<chain>` branches hold as well, as the fetch
    left them: a quiet run's lines wait there, with no pull request, for up to QUIET_HOURS."""
    import chain
    import onboard
    import onboarding_page
    import yaml
    space = spaceyaml.load(root)
    now = now or space.now()
    last = last_ran(root, cloud=cloud)
    try:   # a space no onboarding made was promised no schedule
        onboarded = pages.parse_iso(onboard.read_yaml(root / "_meta" / "state" / "onboard.yaml", {})["at"])
    except (KeyError, TypeError, ValueError, OSError, yaml.YAMLError):
        onboarded = None
    schedules, sweep_hours = dict(onboarding_page._routines(root)), 0
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
        if routine == "sweep_chain":
            sweep_hours = 2 * gap / 60
    for stage in (*SWEEP_STAGES, *STAGE_HOURS):
        key, hours = STAGE_STALE.format(stage), STAGE_HOURS.get(stage, sweep_hours)
        since = last.get(stage) if stage != "route" else last.get(ROUTE_WAITS) if space.routing_on else None
        if since is not None and hours and now - since > space.scale(hours):
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


def behind_item(root: Path, cause: str | None, since: str = "", detail: str = "") -> None:
    """The item that says this computer cannot bring main in, in the sync's own Needs-you file: `cause` is
    "fetch", "merge", "run" (the run itself fails), or the name of the git operation a skipped run found
    (`in_progress`); `since` when it began, `detail` what a merge stops on or how the run fails. None takes
    every such item out: a run brought main in. The id holds
    the cause and the day it began, so one incident the person answered is not asked again and the same cause
    another day is a new item. The file is written only when that changes it."""
    import needs_you
    path = Path(root) / SYNC_ITEMS
    before = items_in(path)
    items = [i for i in before if not str(i.get("id", "")).startswith(BEHIND_ITEM)]
    if cause:
        day = since[:10] or "an earlier run"
        apart = "so nothing from main arrives here and nothing from here lands"
        if cause == "fetch":
            text = f"This computer has not reached the repository since {day}: every fetch fails, {apart}."
            advice = "yes: an agent checks the connection and the sign-in of git and gh on this computer"
            default = "the sync tries again every run; this computer stays apart from main until a fetch works"
        elif cause == "merge":
            text = (f"This computer has not merged main since {day}: the merge stops on "
                    f"{detail or 'a change git cannot merge'}, so nothing from main arrives here.")
            advice = "yes: an agent shows you both versions of each file and settles them with you"
            default = "the sync tries again every run; this computer stays apart from main until the merge goes through"
        elif cause == "run":
            text = (f"The sync has failed on every run since {day} ({detail or 'an error'}), so what is written "
                    "here may not land and nothing from main arrives.")
            advice = "yes: an agent reads the sync's run lines and says what stops it"
            default = "the sync tries again every run and fails the same way"
        else:
            text = (f"The sync has skipped every run since {day}: git says an operation is in progress ({cause}), "
                    f"and one that lasts a day was left behind, {apart}.")
            advice = ("yes: an agent checks that no git command is running and says which file is in the way; it "
                      "removes nothing without your word")
            default = "every run is skipped until the file is gone"
        item_id = BEHIND_ITEM + "-".join(part for part in (re.sub(r"[^a-z0-9]+", "-", cause.lower()).strip("-") or "git",
                                                           since[:10].replace("-", "")) if part)
        if item_id not in needs_you.answered_ids(root):
            items.append({"id": item_id, "kind": "request", "text": text, "recommendation": advice,
                          "default": default, "depends": 1})
    items_out(path, items, before)


def left_behind(root: Path, gdir: Path, name: str) -> None:
    """A run skipped because git is mid-operation (`in_progress`) is right while a commit or a merge runs, and
    wrong for good when the marker was left by one that died: every later run is skipped, and nothing says so.
    Once the marker is BEHIND_HOURS old, by its own time, the item says it (`behind_item`). Never in the way
    of the next run: a failure here is said on the console only."""
    try:
        made = (gdir / name).stat().st_mtime
        if time.time() - made >= spaceyaml.load(root).scale(BEHIND_HOURS).total_seconds():
            behind_item(root, name, pages.iso(dt.datetime.fromtimestamp(made, dt.timezone.utc)))
    except Exception as exc:
        print(f"sync: the item for the operation left behind was not written ({type(exc).__name__})")


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
        self.links: set = set()   # the staged links (`staged_blobs`)
        self.oversize: set = set()   # the staged blobs too large for the scan to read (`staged_blobs`)
        self.clean: set = set()   # (path, blob id) the scan found nothing in: not read again before a commit
        self.left_local: dict = {}   # path: the kinds of finding that kept it from landing this run (`left_local_health`)
        self.judged: dict = {}   # path: the blob the scan judged this run, so the next run need not read it again
        self.started = time.monotonic()   # the run's scan budget counts from here (SCAN_RUN_SECONDS)
        self.status_paths: set | None = None   # every path git status named this run (`candidates`), when it ran whole
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

    def refresh_lock(self) -> None:
        """Move the lock's time on, while this process holds it: a run that scans for long is never taken
        for a stale one, so no second run stages files while this one's scan reads (`scan_staged`)."""
        path = self.gdir / "sync.lock"
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
                path.write_text(json.dumps({"pid": os.getpid(), "at": pages.iso(self.space.now())}), encoding="utf-8")
        except (OSError, ValueError, AttributeError):
            pass

    def error(self, text: str) -> None:
        self.notes.setdefault("errors", []).append(text)
        print(f"sync: {text}")

    # ----- chains --------------------------------------------------------------
    def watch_chains(self, cloud: bool = False) -> bool:
        """`not_started`, never in the way of landing: True when it wrote stale.yaml. `cloud` (the computer's
        leg) reads the run lines the cloud branches hold too."""
        try:
            return bool(not_started(self.root, self.now, cloud=cloud))
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
        named: set = set()
        for item in done.stdout.split("\0"):
            if len(item) < 4:
                continue
            xy, rel = item[:2], item[3:]
            named.add(rel)
            if rel.endswith("/") or xy == "!!" or self.excluded(rel, subs) or not self.wanted(rel):
                continue
            # its deletion still lands; a Keynote deck (`*.key`, a zip with no PEM header) is no credential file
            if os.path.lexists(self.root / rel) and scan.credential_file(rel, scan.file_head(self.root / rel)):
                print(f"skip {rel}: a credential file; left local")
                self.notes["credential_files"] = self.notes.get("credential_files", 0) + 1
                self.left_local.setdefault(rel, set()).add("credential_file")
                continue
            # in a cloud run, `keep_out` names what cannot land instead
            if xy == "??" and not getattr(self.args, "only", None) and not self.cloud:
                why = self.settled_text(rel)
                if why:
                    print(f"skip {rel}: {why}")
                    continue
            chosen.append(rel)
        if not getattr(self.args, "only", None):
            self.status_paths = named
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
        any other stays in the clone, named in `left_out`, as a release (`forged_release`) or a signature
        (`forged_signature`) this run forged does, so either raises the security item and none waits on the cloud's protected branch for the person's
        click. Nor a file over CLOUD_MAX_BYTES or not text,
        or one that would not read once its lines were held back (`hold_lines`), named in `held`: a
        computer leaves such a file for a later run, and the clone is thrown away. They are named here;
        `unstage` keeps them out of each commit, also when they were staged before the run."""
        if not self.cloud:
            return rels
        guarded = set(protected_changes(self.root, rels, ["HEAD"], None))
        keep = []
        for rel in rels:
            path = self.root / rel
            if pages.is_link(path) or rel == VERDICTS:
                # a link points the next write anywhere, and a cloud run never signs: neither lands
                if rel not in self.left_out:
                    print(f"left out: {rel} is " + ("a link" if rel != VERDICTS else "the person's verdicts"))
                self.left_out.add(rel)
            elif rel in guarded:
                if rel == "AGENTS.md" and (self.dry or self.put_back(rel)):
                    continue
                if rel not in self.left_out:
                    print(f"left out: {rel} is protected")
                self.left_out.add(rel)
            elif self.forged_release(rel):
                if rel not in self.left_out:
                    print(f"left out: {rel} claims to come from another space, and a cloud run writes no release")
                self.left_out.add(rel)
            elif self.forged_signature(rel):
                if rel not in self.left_out:
                    print(f"left out: {rel} carries a signature this run wrote, and a cloud run never signs")
                self.left_out.add(rel)
            elif rel in self.held or path.is_dir():
                continue
            elif os.path.lexists(path) and not self.landable_text(path) and self.log_text(rel) is None:
                if rel not in self.held:
                    print(f"left out: {rel} is over 1 MB or not text")
                self.held.add(rel)
            else:
                keep.append(rel)
        return keep

    def forged_release(self, rel: str) -> bool:
        """Whether `rel`, as this cloud run left it, is a release no run of this space wrote: a signal in
        `_inbound/signals/`, or a candidate with `released_from` that is new or changed in any way (the marks
        landing.py puts on one that waits included: landing never runs in a cloud chain). A cloud run writes no release into its own space (release.py delivers on release/<slug>-<8 hex>, through
        the release clone), so one in its diff is forged, and on the cloud's protected pull request the person's
        click would land it as the sender's. A deletion is no release: landing and classify take them out."""
        import gate
        folded = rel.casefold()
        signal = folded.startswith(gate.SIGNALS_IN)
        if not signal and not (folded.startswith(gate.CANDIDATES) and folded.endswith(".md")):
            return False
        try:
            text = (self.root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):   # deleted, a folder, or no text: the gate reads no release there
            return False
        if signal:   # no tool rewrites a signal in place
            return True
        try:
            fm, _ = pages.split(text)
        except Exception:
            return False   # no frontmatter: no `released_from` the gate would read
        if not isinstance(fm, dict) or not fm.get("released_from"):
            return False
        # landing, the one tool that marks a release (`gate.marks_only`), runs in the computer's daily, never here
        return gate._show(self.root, "HEAD", rel) != text

    def forged_signature(self, rel: str) -> bool:
        """Whether `rel`, as this cloud run left it, is a stable page under knowledge/ (not knowledge/sources/)
        whose signature (`gate.SIGNATURE`) HEAD's copy does not hold: a cloud run never signs, and the gate
        trusts a signature its person's login pushed, which the cloud's is. Filing a release is no exception
        here: landing runs in the computer's daily (`status.run`), never in a cloud chain (`CLOUD_CHAINS`). A
        deletion, or a page made not stable, is no signature (`gate.withdrawal_reasons` holds a withdrawal)."""
        import gate
        folded = rel.casefold()
        if not folded.startswith("knowledge/") or folded.startswith("knowledge/sources/") or not folded.endswith(".md"):
            return False
        try:
            fm, _ = pages.split((self.root / rel).read_text(encoding="utf-8"))
        except Exception:   # deleted, a folder, no text or no frontmatter: no stable page the gate reads
            return False
        if not isinstance(fm, dict) or str(fm.get("status") or "") != "stable":
            return False
        sig = lambda f: [str(f.get(k) or "") for k in gate.SIGNATURE]   # noqa: E731 - compared as the gate does
        # no exemption for filing a release: landing runs in the computer's daily, never in a cloud run
        return sig(gate._frontmatter_at(self.root, "HEAD", rel)) != sig(fm)

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

    def log_text(self, rel: str) -> str | None:
        """The text of one of the tools' own logs (CLOUD_LOGS) at any size the secret scan reads whole, where
        `scan.text_of` stops at 1 MB; None for any other file, and for a log that is a link, larger still or
        not UTF-8 text. A cloud run lands such a log (`keep_out`) and holds its lines back like any other
        file's (`hold_lines`)."""
        path = self.root / rel
        # fnmatch's * crosses a /: a log is a file of that folder itself, never one further down
        if not any(fnmatch.fnmatchcase(rel, rule) and rel.count("/") == rule.count("/") for rule in CLOUD_LOGS) \
                or pages.is_link(path):
            return None
        try:
            if not path.is_file() or path.stat().st_size > scan.SCAN_MAX_BYTES:
                return None
            data = path.read_bytes()
            return None if b"\0" in data[:scan.SNIFF_BYTES] else data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None

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
            # a link lands as its target's name; a log past the text reader's size is read here
            text = None if path.is_symlink() else (scan.text_of(path) or self.log_text(rel))
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
            try:
                pages.write_text(path, done, root=self.root, newline="")
            except pages.UnsafePath:
                self.held.add(rel)
                print(f"left out: {rel} sits behind a link")
                continue
            count += len(kinds)
            print(f"scan: {len(kinds)} line(s) held back in {rel}")
        return count

    def staged(self) -> list[str]:
        done = git(self.root, "diff", "--cached", "--name-only", "-z", "--diff-filter=d", check=True)
        return [p for p in done.stdout.split("\0") if p]

    def unstage(self) -> None:
        """In a cloud run, the last word before each commit, which takes the whole index: whatever is staged
        and may not land is taken out of it, a deletion included, whoever staged it. That is a protected
        change (`protected_changes`, against HEAD), a file `keep_out` left out or held or `hold_lines` held, a path no sync
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
        drop = list(dict.fromkeys(guarded + private + [rel for rel in staged
                                                       if rel in self.held or rel in self.left_out]))
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

    def staged_blobs(self) -> dict:
        """{path: the bytes the index holds for it, or None when git could not hand them over} for each
        staged path that is no deletion and no gitlink, read with `git cat-file`: what the commit takes. A
        link's bytes are its target's name. A blob over `scan.SCAN_MAX_BYTES` is not read into memory: its
        bytes are b"" and its path is in `self.oversize`, and `scan_staged` streams it (`scan.scan_blob`)."""
        out = git(self.root, "diff", "--cached", "--raw", "--no-renames", "--no-abbrev", "-z", "--diff-filter=d",
                  check=True).stdout
        fields = out.split("\0")   # ":<old mode> <new mode> <old id> <new id> <status>", then its path
        entries = []
        for meta, rel in zip(fields[0::2], fields[1::2]):
            parts = meta.split()
            if rel and len(parts) >= 4 and parts[1] != "160000" and (rel, parts[3]) not in self.clean:
                entries.append((rel, parts[1], parts[3]))
        self.links = {rel for rel, mode, _ in entries if mode == "120000"}
        self.oversize = set()
        if not entries:
            return {}
        sizes = self.cat_file("--batch-check", [oid for _, _, oid in entries])
        blobs, wanted = {}, []
        self.oids = {rel: oid for rel, _, oid in entries}
        for (rel, _, oid), (kind, size, _) in zip(entries, sizes):
            if kind != "blob":
                blobs[rel] = None
            elif size > scan.SCAN_MAX_BYTES:
                blobs[rel] = b""
                self.oversize.add(rel)
            else:
                wanted.append((rel, oid))
        for (rel, _), (kind, _, data) in zip(wanted, self.cat_file("--batch", [oid for _, oid in wanted])):
            blobs[rel] = data if kind == "blob" else None
        return blobs

    def cat_file(self, mode: str, oids: list) -> list:
        """(type, size, bytes) for each object id, in order, from one `git cat-file --batch` (bytes) or
        `--batch-check` (no bytes); ("missing", 0, b"") for one git does not have."""
        if not oids:
            return []
        done = subprocess.run(["git", "cat-file", mode], cwd=str(self.root), capture_output=True, check=True,
                              input="".join(oid + "\n" for oid in oids).encode("ascii"))
        data, at, out = done.stdout, 0, []
        for _ in oids:
            end = data.index(b"\n", at)
            header, at = data[at:end].split(), end + 1
            if len(header) != 3:
                out.append(("missing", 0, b""))
                continue
            size = int(header[2])
            if mode == "--batch":
                out.append((header[1].decode("ascii"), size, data[at:at + size]))
                at += size + 1   # the content, then a newline
            else:
                out.append((header[1].decode("ascii"), size, b""))
        return out

    def scan_staged(self) -> None:
        """Every staged path the scan finds something in is taken out of the index again: it stays in the
        working tree for the person, and only the count is recorded. The scan reads the staged blob itself
        (`staged_blobs`), so a file saved again after the add is judged by what is committed. A blob of
        any size is read, a large one streamed (`scan.scan_blob`); one git cannot hand over, one the scan
        cannot read whole and a credential file's name are findings too; a link is also scanned through to
        its target, as before. Each path left local, and why, goes to the health record
        (`left_local_health`). A blob that record says this scan already left local is not read again; on
        the computer, one not reached within SCAN_RUN_SECONDS is left local unjudged (`NOT_YET`)."""
        findings, hit = [], set()
        known = {} if self.cloud else read_left_local(self.root)
        for rel, data in self.staged_blobs().items():
            oid, entry = self.oids[rel], known.get(rel) or {}
            if entry.get("blob") == oid and entry.get("scan") == scanner_id() and entry.get("why"):
                found = [scan.Finding(str(kind), rel, 0) for kind in entry["why"]]   # judged before: stays local
                self.judged[rel] = oid
            elif not self.cloud and time.monotonic() - self.started >= SCAN_RUN_SECONDS:
                found = [scan.Finding(NOT_YET, rel, 0)]
            else:
                self.refresh_lock()
                found = self.blob_findings(rel, data)
                self.judged[rel] = oid
            if found:
                hit.add(rel)
                findings += found
                self.left_local.setdefault(rel, set()).update(f.kind for f in found)
            else:
                self.clean.add((rel, self.oids[rel]))
        if not findings:
            return
        for finding in findings:
            if finding.kind == NOT_YET:
                print(f"scan: {finding.path} was not read this run, its scan time was used up; left local until "
                      "a later run reads it")
            elif finding.kind == "unscanned":
                print(f"scan: {finding.path} could not be read whole for the scan (unreadable, an archive that "
                      "unpacks past its bound, or out of time); left local")
            elif finding.kind == "credential_file":
                print(f"scan: {finding.path} is a credential file; left local")
            else:
                print(f"scan: {finding.kind} at {finding.path}:{finding.line_no}; left local")
        self.pathspec("reset", "-q", rels=sorted(hit))
        self.notes["scan_hits"] = self.notes.get("scan_hits", 0) + len(findings)

    def blob_findings(self, rel: str, data) -> list:
        """What the scan finds in one staged blob (`staged_blobs` gave `data`): a credential file's name, the
        blob itself, streamed when large, the content a Git LFS pointer stands for, and a link's target."""
        found = []
        large = rel in self.oversize
        head = scan.blob_head(self.root, self.oids[rel]) if large else data
        if scan.credential_file(rel, head):
            found.append(scan.Finding("credential_file", rel, 0))
        if large:   # streamed out of git and read in chunks: a blob of any size is read
            found += scan.scan_blob(self.root, self.oids[rel], rel)
        elif data is None:
            found.append(scan.Finding("unscanned", rel, 0))
        else:
            found += scan.scan_bytes(data, rel)
            if data.startswith(LFS_POINTER):
                found += self.lfs_findings(rel, data)
        if rel in self.links:
            found += scan.scan_file(self.root / rel, rel)
        return found

    def lfs_findings(self, rel: str, pointer: bytes) -> list:
        """What the scan finds in the content a Git LFS pointer stands for: that content goes to the
        LFS store on the push, beside the commit, so the pointer alone is no clean file. Read from the
        local LFS store, where `git add` put it, or else from the working tree; `unscanned` when
        neither holds it."""
        oid = LFS_OID.search(pointer)
        common = git(self.root, "rev-parse", "--git-common-dir").stdout.strip()
        if oid and common:
            digest = oid.group(1).decode("ascii")
            stored = (self.root / common).resolve() / "lfs" / "objects" / digest[:2] / digest[2:4] / digest
            if stored.is_file():
                return scan.scan_file(stored, rel)
        path = self.root / rel
        if path.is_file() and not path.read_bytes().startswith(LFS_POINTER):
            return scan.scan_file(path, rel)
        return [scan.Finding("unscanned", rel, 0)]

    def commit(self, message: str) -> bool:
        """Commit the index as it stands, after the last checks on it: in a cloud run what may not land
        is taken out (`unstage`), and outside a merge the scan judges the index once more
        (`scan_staged`; a blob already found clean is not read again), so a commit that only meant
        to carry one file (an answer test set aside, a security item) never carries another one
        someone staged by hand unscanned. A file staged while the scan read (a hand's `git add`) is judged
        too: the index is listed again after the scan, and what is new in it is scanned before the commit."""
        if self.cloud:
            self.unstage()
        if git(self.root, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode != 0:
            for _ in range(3):
                self.scan_staged()
                if not self.staged_blobs():
                    break
            else:   # staged again and again while the scan read: what is new now waits for the next run
                late = sorted(self.staged_blobs())
                self.pathspec("reset", "-q", rels=late)
                print(f"scan: {len(late)} path(s) staged while the scan read; left for the next run")
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
        moved, held = [], []
        for sub in self.submodules():
            if not (self.root / sub / ".git").exists():
                continue
            if git(self.root / sub, "status", "--porcelain").stdout.strip():
                print(f"pins: {sub} has local changes, left alone")
                held.append(sub)
                continue
            if self.dry:
                print(f"would advance the pin of {sub}")
                continue
            before = git(self.root, "rev-parse", f"HEAD:{sub}").stdout.strip()
            done = git(self.root, "submodule", "update", "--remote", "--", sub)
            if done.returncode != 0:
                self.error(f"pins: update of {sub} failed")
                held.append(sub)
                continue
            after = git(self.root / sub, "rev-parse", "HEAD").stdout.strip()
            if after != before:
                git(self.root, "add", "--", sub, check=True)
                moved.append(sub)
        if moved and self.commit("chore: pins"):
            self.notes["pins"] = moved
        if not self.dry:
            self.pins_held(held)

    def sync_gap(self) -> None:
        """The sync's own last run, kept on this computer's clock (`health_state`, one small file outside the
        repository written every run): when the run before this one was more than SYNC_GAP_HOURS ago (the
        computer slept, or its scheduler stopped starting the sync), this run writes a line that says for how
        many hours, as `gap_hours`. No item: a laptop that slept would raise one every morning. Only a whole
        run on the computer counts."""
        try:
            ago = self.now - pages.parse_iso(self.health_state().get("sync"))
        except (TypeError, ValueError, OverflowError):
            ago = None
        if ago is not None and ago > self.space.scale(SYNC_GAP_HOURS):
            self.notes["gap_hours"] = round(ago.total_seconds() / 3600)
        self.health_state(sync=pages.iso(self.now))

    def pins_held(self, held: list) -> None:
        """The mounts this run could not move, because something wrote into them or their update failed
        (`advance_pins`): named in the run line, each with since when, on this computer's own clock
        (`health_state`; where that file cannot be written the clock starts again every run and no mount is ever
        called stale). One held for PIN_DAYS is a stale stage, `<mount> pin stale` in stale.yaml, which the
        health line and the stale item show: a copy that stopped following its space is still read as if it
        were current. The entry goes when the mount moves again. stale.yaml is written only when that changes
        it."""
        import chain
        import yaml
        before = self.health_state().get("pins_held")
        before = before if isinstance(before, dict) else {}
        since = {sub: str(before.get(sub) or pages.iso(self.now)) for sub in held}
        self.health_state(pins_held=since or None)
        if since:
            self.notes["pins_held"] = sorted(since)
        late = {}
        for sub, at in since.items():
            try:
                if self.now - pages.parse_iso(at) >= self.space.scale(PIN_DAYS * 24):
                    late[STAGE_STALE.format(f"{sub} pin")] = at
            except (TypeError, ValueError, OverflowError):
                continue
        path = self.root / chain.STALE
        try:
            data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
        except (OSError, yaml.YAMLError):
            return
        if not isinstance(data, dict):
            return
        want = {**{k: v for k, v in data.items() if not str(k).endswith(STAGE_STALE.format("pin"))}, **late}
        if want != data:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump(want, sort_keys=True), encoding="utf-8", newline="\n")

    def stale_item(self) -> None:
        """One Needs-you item for everything stale.yaml says did not run in time: a chain that did not start, a
        stage past its window (`not_started`), a mount held for a week (`pins_held`). The health line and the
        briefing show stale.yaml too, but the daily chain writes both, and it is the first thing to stop: this
        item is the sync's own, and the session start reads it. The run line names them as `stale`. The item's
        id is the set of names with since when each is late, so one incident is asked once, the same stage late
        again later is a new item (Needs you hides an id that has an answer), and the item goes when nothing is
        late. While the routines are paused nothing is asked: the person stopped them. Only a whole run on the
        computer writes it."""
        if self.dry or self.cloud or getattr(self.args, "only", None):
            return
        import chain
        import routine
        import yaml
        try:
            data = yaml.safe_load((self.root / chain.STALE).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            data = {}
        ends = (chain.NOT_STARTED.format(""), STAGE_STALE.format(""))
        late = {str(k): str(v) for k, v in data.items() if str(k).endswith(ends)} if isinstance(data, dict) else {}
        if late:
            self.notes["stale"] = sorted(late)
        try:
            workspace = self.workspace()
            record = routine.host(workspace) if workspace is not None else None
            paused = bool(record) and not record.get("unclear") and routine.paused_until(record, self.now) is not None
        except Exception:
            paused = False
        asking = bool(late) and not paused
        if not asking and not any(str(i.get("id", "")).startswith(STALE_ITEM) for i in items_in(self.root / SYNC_ITEMS)):
            return
        incident = "\n".join(f"{key} {when}" for key, when in sorted(late.items()))
        item_id = STALE_ITEM + hashlib.sha256(incident.encode("utf-8")).hexdigest()[:8]
        names = ", ".join(f"{key.removesuffix(ends[0]).removesuffix(ends[1])} (last {when[:10]})"
                          for key, when in sorted(late.items()))
        item = {"id": item_id, "kind": "request",
                "text": (f"Part of this space did not run in time: {names}. The agent that runs your routines may "
                         "be signed out, out of allowance or no longer scheduled; a `pin` is a mounted space's copy "
                         "that something wrote into, so it stopped following its space."),
                "recommendation": ("yes: open a session and ask why your routines stopped; the agent reads the "
                                   "run lines and the scheduler and says what to do"),
                "default": ("they stay as they are: the sync keeps status and filing going without the model, "
                            "and the briefing and new drafts wait"), "depends": 1}
        self.leg_items([item] if asking else [],
                       lambda other: other.startswith(STALE_ITEM) and (other != item_id or not asking))

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
        once per head, as a flaky one (`healing`), unless the same checks failed again after the last
        rerun (a failure with a cause: the next push would only fail it again; `failed_again`), and
        HEAL_RERUNS times a day at most. True when it repaired something; False when nothing failed, or when nothing could be
        repaired (no answer test to set aside, and no run that may be rerun), which stays in the run line (`pr_red`) for the maintenance agent, not the owner."""
        failed = ghrest.failed_checks(self.gh, self.slug, head) or []
        if not failed:
            return False
        state = self.heal_state()
        runs = sorted({m.group(1) for c in failed for m in [re.search(r"/actions/runs/(\d+)", str(c.get("link")))] if m})
        logs = [self.gh("run", "view", run, "--log-failed") for run in runs]
        import tests_run
        held = tests_run.hold(self.root, tests_run.failures_in("".join(d.stdout for d in logs if d), self.root),
                              "ci", self.now)
        names, day = sorted({str(c.get("name")) for c in failed}), self.now.date().isoformat()
        today = int(state.get("count") or 0) if state.get("pr") == pr and state.get("day") == day else 0
        if held:
            git(self.root, "add", "--", tests_run.ANSWERS_PATH.as_posix())
            self.commit("chore: sync")
            self.notes["healing"] = {"pr": pr, "held_tests": held}
            self.heal_state({"pr": pr, "day": day, "count": today,   # the day's reruns still count
                             "what": f"set aside {len(held)} answer test(s) the checks could not pass: {'; '.join(held)}"})
            return True
        if runs and state.get("rerun") != head and today < HEAL_RERUNS and not self.failed_again(state, pr, names):
            for run in runs:
                self.gh("run", "rerun", run, "--failed")
            self.notes["healing"] = {"pr": pr, "rerun": runs}
            self.heal_state({"pr": pr, "rerun": head, "failing": names, "runs": runs, "day": day,
                             "count": today + 1, "what": "reran a failed check"})
            return True
        self.notes["pr_red"] = {"pr": pr, "checks": [c.get("name") for c in failed]}
        return False

    def failed_again(self, state: dict, pr, names: list) -> bool:
        """Whether the checks `names` of sync pull request `pr` are the ones the last rerun was for, and that rerun
        has not passed (still running counts as not passed: the next run looks again). A rerun that passed was
        chance, so the same checks failing on a later head are rerun too."""
        if state.get("pr") != pr or state.get("failing") != names:
            return False
        reran = state.get("runs") or []
        return not reran or not all(self._passed(run) for run in reran)

    def _passed(self, run) -> bool:
        viewed = self.gh("run", "view", str(run), "--json", "conclusion")   # the run's latest attempt
        try:
            return viewed is not None and viewed.returncode == 0 and \
                json.loads(viewed.stdout or "{}").get("conclusion") == "success"
        except ValueError:
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
            self.apart = ("fetch", "")
            return
        if not self.has_origin_main():
            return
        before = git(self.root, "rev-parse", "HEAD").stdout.strip()
        links_before = tracked_links(self.root)
        done = git(self.root, "merge", "--no-edit", "origin/main")
        if done.returncode != 0 and not self.settle():
            self.apart = ("merge", ", ".join(self.conflicted()[:5]))
            git(self.root, "merge", "--abort")
            self.behind = True
            self.notes["conflict"] = True
            print("merge: conflict with origin/main; aborted, next run retries")
            return
        brought = sorted(tracked_links(self.root) - links_before)
        if brought and git(self.root, "rev-parse", "HEAD").stdout.strip() != before:
            # a link merged in points this computer's next write or read anywhere: undone, and said
            undone = git(self.root, "reset", "-q", "--keep", before).returncode == 0
            self.behind = True
            self.apart = ("merge", f"{len(brought)} symbolic link(s) that main brings in")
            self.notes["links"] = len(brought)
            print(f"merge: origin/main brings in {len(brought)} symbolic link(s); "
                  + ("the merge was undone" if undone else "the merge could not be undone"))
            if not undone:
                self.error("a merge that brought in a symbolic link could not be undone")
            digest = hashlib.sha256("\n".join(brought).encode("utf-8")).hexdigest()[:8]
            self.leg_items([{"id": f"sync-link-{digest}", "kind": "security",
                             "text": f"origin/main holds {len(brought)} symbolic link(s) that this space never "
                                     "carries; the sync did not merge it.",
                             "recommendation": "find the pull request that added them and take them out on main",
                             "default": "this computer stops merging main until the links are gone",
                             "depends": 99}], lambda other: False)
            return
        if git(self.root, "rev-parse", "HEAD").stdout.strip() != before:
            print("merge: origin/main brought in")

    def behind_health(self) -> None:
        """At the end of a run: when the fetch failed, the merge stopped or was undone, or the run itself died
        (`self.apart`), since when the same cause has lasted and for how many runs in a row goes on this
        computer's own clock (`health_state`; where that file cannot be written the clock starts again every
        run and nothing is ever said). Once that is BEHIND_HOURS and BEHIND_RUNS, the cause is one Needs-you item
        (`behind_item`): a single failed run after days asleep is not. A run that brought main in clears the
        clock and takes the item out, one left by a git operation that was in the way included. Only a whole
        run on the computer writes it."""
        if self.dry or self.cloud or getattr(self.args, "only", None):
            return
        cause, detail = getattr(self, "apart", None) or (None, "")
        if cause is None:
            self.health_state(behind=None)
            behind_item(self.root, None)
            return
        was = self.health_state().get("behind")
        was = was if isinstance(was, dict) and was.get("cause") == cause and was.get("since") else {}
        since, runs = str(was.get("since") or pages.iso(self.now)), int(was.get("runs") or 0) + 1
        self.health_state(behind={"cause": cause, "since": since, "runs": runs})
        try:
            aged = self.now - pages.parse_iso(since) >= self.space.scale(BEHIND_HOURS)
        except (TypeError, ValueError, OverflowError):
            aged = False
        if aged and runs >= BEHIND_RUNS:
            behind_item(self.root, cause, since, detail)

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
        already kept ours. `.github/allowed_signers` takes main's copy (`main_signers`). Anything else is left for
        the caller."""
        import gate
        stages: dict = {}
        for entry in git(self.root, "ls-files", "-u", "-z").stdout.split("\0"):
            meta, _, path = entry.partition("\t")
            if path and meta:
                stages.setdefault(path, set()).add(meta.split()[-1])
        if gate.ALLOWED_SIGNERS in stages:
            self.main_signers(stages.pop(gate.ALLOWED_SIGNERS))
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

    def main_signers(self, have: set) -> None:
        """A conflicted `.github/allowed_signers` (two computers each added their line, or a file committed here
        before main's was merged) takes main's copy, deleted where main deleted it, plus this computer's own line
        when main lists keys but not that one: never a union, which could bring back a line main removed. The key
        then waits for the click as one added line."""
        import gate
        rel, spec = gate.ALLOWED_SIGNERS, self.spec([gate.ALLOWED_SIGNERS])
        if "3" not in have:
            git(self.root, "rm", "-q", "--pathspec-from-file=-", "--pathspec-file-nul", input=spec)
            return
        git(self.root, "checkout", "--theirs", "--pathspec-from-file=-", "--pathspec-file-nul", input=spec)
        path, mine = self.root / rel, own_signer()
        text = path.read_text(encoding="utf-8")
        if text.strip() and not lists(text, mine) and mine:
            pages.write_text(path, text + ("" if text.endswith("\n") else "\n") + f"{mine}\n", root=self.root,
                             newline="")
        git(self.root, "add", "--pathspec-from-file=-", "--pathspec-file-nul", input=spec)

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
            if not self.held_here(path, base, "MERGE_HEAD"):
                return False
        spec = "\0".join(f":(literal){rel}" for rel in conflicts)
        if git(self.root, "checkout", "HEAD", "--pathspec-from-file=-", "--pathspec-file-nul", input=spec).returncode \
                or git(self.root, "commit", "-q", "--no-edit").returncode:
            return False   # the caller aborts the merge: nothing is left half done
        self.notes["kept_ours"] = len(conflicts)
        print(f"merge: {len(conflicts)} path(s) where origin held an earlier copy of ours; kept ours")
        return True

    def held_here(self, path: str, base: str, theirs_ref: str) -> bool:
        """`theirs_ref`'s copy of `path` is one this checkout held since `base`, and HEAD still has the path:
        theirs is only an earlier copy of ours, so ours loses nothing of theirs."""
        theirs = git(self.root, "rev-parse", "-q", "--verify", f"{theirs_ref}:{path}").stdout.strip()
        ours = git(self.root, "rev-parse", "-q", "--verify", f"HEAD:{path}").stdout.strip()
        if not theirs or not ours:
            return False
        held = git(self.root, "log", "--full-history", "--no-renames", "--raw", "--no-abbrev", "--format=",
                   f"{base}..HEAD", "--", path).stdout
        return theirs in {line.split()[3] for line in held.splitlines() if line.startswith(":")}

    def ours_changed(self) -> list:
        """The paths a push from this checkout may carry: those HEAD changes against origin/main. When the merge
        left origin/main out (refused over a file the scan left local, or aborted on a conflict), only those this
        checkout itself changed since the two parted: a commit built on main's tip from HEAD's copy of a path
        only main moved would undo main's change, a cloud run's lines in an append-only file among them. A path
        both moved waits here for the merge, unless main holds only an earlier copy of ours (`held_here`); the
        run line counts them (`waiting_merge`)."""
        def names(*spec: str) -> list:
            out = git(self.root, "diff", "--name-only", "--no-renames", "-z", *spec).stdout
            return [p for p in out.split("\x00") if p]
        changed = names("origin/main", "HEAD")
        base = git(self.root, "merge-base", "origin/main", "HEAD").stdout.strip()
        if not base or base == git(self.root, "rev-parse", "origin/main").stdout.strip():
            return changed
        ours, theirs = set(names(base, "HEAD")), set(names(base, "origin/main"))
        both = [p for p in changed if p in ours and p in theirs and not self.held_here(p, base, "origin/main")]
        if both:
            self.notes["waiting_merge"] = len(both)
            print(f"push: {len(both)} path(s) main also changed wait here for the merge")
        return [p for p in changed if p in ours and p not in both]

    def split(self) -> tuple[list, list] | None:
        """The paths this checkout changes against origin/main, as (plain, protected); None, so HEAD is pushed,
        when nothing is held and main lists no keys (or in the template, whose pull requests land on green). A path goes
        to the protected branch wherever the gate would hold it (`held_paths`), not only under a
        protected prefix, so one held file never holds the journal, run lines and witness pages
        behind it. A mount whose address waits in `.gitmodules` waits with it (`readdressed`).
        While origin/main still holds the template's own space.yaml (onboarding's first push never
        landed), every path is protected: the fill goes as one pull request, checked as one tree,
        merged once."""
        try:
            import gate
        except ImportError:
            return None
        if gate.is_template(self.root):
            return None
        # --no-renames: a move lists its old path too, so the subset commit deletes it
        changed = self.ours_changed()
        shown = git(self.root, "show", "origin/main:space.yaml")
        unconfigured = spaceyaml.still_template(shown.stdout if shown.returncode == 0 else None)
        guarded = list(changed) if unconfigured else self.held_paths(changed, f"sync/{hostname()}")
        if ".gitmodules" in guarded:
            guarded += [p for p in self.readdressed() if p in changed and p not in guarded]
        if not guarded:
            # once main lists keys, the plain part goes as the one commit the preflight judged (`gate_holds`), not
            # as HEAD, whose older unsigned commits would hold a signature the key tie reads off their history
            return (list(changed), []) if (gate._show(self.root, "origin/main", gate.ALLOWED_SIGNERS) or "").strip() \
                else None
        return [p for p in changed if p not in guarded], guarded

    def held_paths(self, changed: list, branch: str) -> list:
        """The paths among `changed` the gate would hold on a pull request from `branch`, in their order,
        built from the gate's own tests so the two never drift apart: the name (`gate.protected_reason`
        with this checkout's protected list and origin/main's, which the gate reads) and a file template
        sync manages (`named_holds`); what the gate reads against origin/main beyond the names, a path at
        a time (`gate_holds`); and then the gate's whole verdict on the plain part as it would be pushed
        (`verify_plain`), so a hold no test here names still moves its path. What is left lands on green."""
        held = self.named_holds(changed)
        held.update(self.gate_holds([p for p in changed if p not in held], branch))
        for _ in range(3):   # moving a path can end an exemption another path had (a candidate filed with its page)
            plain = [p for p in changed if p not in held]
            more = self.verify_plain(plain, branch) if plain else {}
            if not more:
                break
            held.update(more)
        for rel in changed:
            if rel in held:
                print(f"split: {rel} waits on the protected branch ({held[rel]})")
        return [p for p in changed if p in held]

    def named_holds(self, changed: list) -> dict:
        """{path: why} for the paths the gate holds by their name (`gate.protected_reason`): this checkout's
        protected prefixes and origin/main's (the gate reads the base branch's list, so a prefix taken out
        here still holds), with the minimum; and each file template sync manages, since a template update
        lands as one unit, with its tools."""
        import gate
        prefixes, _ = gate.protected_prefixes(self.root)
        rules = self.base_rules()
        if rules:   # origin/main unreadable: this checkout's list and the minimum still stand
            prefixes = gate.with_minimum([*prefixes, *rules["protected"]])
        managed = self.template_managed()
        held = {}
        for rel in changed:
            why = "a file template sync manages" if rel in managed else gate.protected_reason(rel, prefixes)
            if why:
                held[rel] = why
        return held

    def gate_holds(self, rest: list, branch: str) -> dict:
        """{path: why} for the paths among `rest` the gate holds for what it reads against origin/main
        beyond their names: a case twin (`gate.case_twins`), a link (`gate.link_changes`), a registry trust
        field (`gate.registry_reasons`), a template release the provenance names anew, and a page check or a
        release check (`gate.page_reasons`, `gate.release_reasons`): log.md not only appended to, a new
        security item on Needs you (the sync's own items, `sync.yaml`, and the morning view's among them), a
        stable page not signed here, a release that is not on its sender's branch. The page and release
        checks run once on the whole change and, only when they hold something, on halves of it until each
        held path stands alone (with every deletion, which an exemption may need), so each hold is put on
        its own path whatever its wording."""
        import gate
        if not rest:
            return {}
        want, held = set(rest), {}
        for rel, other in gate.case_twins(self.root, "HEAD", rest).items():
            held[rel] = f"the same path as {other} where case is ignored"
        for reason in gate.link_changes(self.root, "origin/main", "HEAD"):
            rel = _named(reason, want)
            if rel:
                held[rel] = "a symbolic link or an unlisted submodule"
        author = self.login()   # the checks name the pull request's author: a signature or a ledger ties to it
        if gate.REGISTRY in want and gate.registry_reasons(self.root, "origin/main", "HEAD", author=author):
            held[gate.REGISTRY] = "a subject's home, repository, workspace or login changes"
        if gate.PROVENANCE in want and self.release_moved():
            held[gate.PROVENANCE] = "the template release it records changed"
        changes = [c for c in gate.raw_changes(self.root, "origin/main", "HEAD") if c[3] in want]
        deletions = [c for c in changes if c[2] == "D"]
        owner = (self.base_rules() or {}).get("owner") or str(self.space.owner or "")   # the base branch's, as the gate
        # on the computer, the commit the push carries once main lists keys (`split`: one on origin/main, signed,
        # the adopted cloud heads as parents), not HEAD: the key tie reads its history, and an unsigned local
        # commit of long ago stays in origin/main..HEAD for good. A cloud run pushes HEAD.
        head = (None if self.cloud else self.subset_commit(rest, VERIFY_REF, self.riders([]))) or "HEAD"

        def page_check(part):
            return gate.page_reasons(self.root, "origin/main", head, part, owner, author)

        def release_check(part):
            return gate.release_reasons(self.root, "origin/main", head, part, owner, author, branch)
        def bisect(check, part: list) -> list:   # (change, reasons) the check holds, in about k log n calls, not n
            found = check(part + deletions) if part else []
            if not found:
                return []
            if len(part) == 1:
                return [(part[0], found)]
            return bisect(check, part[:len(part) // 2]) + bisect(check, part[len(part) // 2:])
        kept = [c for c in changes if c[2] != "D"]
        for check, why in ((page_check, "a page check the gate holds"),
                           (release_check, "a release check the gate holds")):
            for change, found in bisect(check, [c for c in kept if c[3] not in held]):
                rel = change[3]   # the gate's own words, so an agent sees the cause (a key, a signer, a log line)
                held[rel] = next((r[len(rel) + 1:].strip() for r in found if r.startswith(f"{rel}:")), why)
        return held

    def base_rules(self) -> dict | None:
        """The gate's rules as origin/main has them (`gate.rules_at`), read once a run; None when they cannot be read."""
        if not hasattr(self, "_rules"):
            try:
                import gate
                self._rules = gate.rules_at(self.root, "origin/main")
            except Exception:
                self._rules = None
        return self._rules

    def release_moved(self) -> bool:
        """Whether the provenance names another template, release or commit than origin/main's: the gate
        holds that like machinery (it lands only as part of the release it names)."""
        def fields(ref: str) -> tuple:
            import yaml
            done = git(self.root, "show", f"{ref}:_meta/state/template-version.yaml")
            try:
                data = yaml.safe_load(done.stdout) if done.returncode == 0 else None
            except yaml.YAMLError:
                data = None
            data = data if isinstance(data, dict) else {}
            return tuple(str(data.get(key) or "") for key in ("repo", "release", "commit"))
        return fields("origin/main") != fields("HEAD")

    def verify_plain(self, plain: list, branch: str) -> dict:
        """{path: why} for the paths of the plain part the gate still holds: the gate's own verdict
        (`gate.classify`) on the commit the push would carry, against origin/main, each reason put on the
        path it names. A hold that names no path is left in the run line (`plain_held`) for the
        maintenance agent: the plain pull request then waits, as it would have before."""
        import gate
        commit = self.subset_commit(plain, VERIFY_REF)   # a ref never pushed: always the tree as it stands now
        if commit is None:
            return {}
        try:
            verdict, reasons = gate.classify(self.root, plain, base_ref="origin/main", head=commit,
                                             author=self.login(), head_ref=branch)
        except Exception as exc:   # never in the way of landing: the gate in the checks still judges
            self.error(f"the gate could not judge the plain part: {type(exc).__name__}")
            return {}
        if verdict != gate.PROTECTED:
            return {}
        held = {}
        for reason in reasons:
            rel = _named(reason, plain)
            if rel and rel not in held:
                held[rel] = reason[len(rel) + 1:].strip() or "the gate holds it"
        if not held:
            self.notes["plain_held"] = len(reasons)
            print(f"split: the gate holds the plain part for a reason no path names: {'; '.join(reasons)[:300]}")
        return held

    def login(self) -> str | None:
        """The author the preflight names, as the checks name the pull request's, for who edits a registry, signs a
        page or adds to a ledger: this checkout's login (`me`) in every profile, a personal space's included, whose
        registry may give its owner one. None in a personal space's cloud run, whose quiet runs never ask GitHub and
        whose signatures it leaves out anyway (`forged_signature`)."""
        return self.me() if self.space.profile != "personal" or not self.cloud else None

    def me(self) -> str | None:
        """The GitHub login this checkout pushes and opens pull requests as (`gh api user`), in every profile;
        None when GitHub cannot be asked. Asked once a run."""
        if not hasattr(self, "_me"):
            self._me = gh_login(self.gh) if self.slug else None
        return self._me

    def pushed_by_me(self, branch: str, sha: str) -> bool:
        """`pushed_by` for this checkout's own login (`me`). Asked once a run for each tip."""
        cache = self.__dict__.setdefault("_pushed", {})
        if (branch, sha) not in cache:
            cache[(branch, sha)] = bool(self.slug) and pushed_by(self.rest, self.slug, branch, sha, self.me())
        return cache[(branch, sha)]

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

    def subset_commit(self, paths: list, remote_ref: str, parents: list | None = None, source: str = "HEAD",
                      blobs: dict | None = None) -> str | None:
        """A commit on top of origin/main that carries only `paths` as they are in `source` (HEAD), built in
        a private index so the working tree and the real index are never touched. Each of `parents`
        is one more parent of it (the cloud heads it carries, `riders`), so their history lands with it.
        `blobs` names a blob for a path in place of the one `source` has (`split_cloud`'s merged items)."""
        parents = list(parents or [])
        index = self.gdir / "sync-split.index"
        env = {"GIT_INDEX_FILE": str(index)}
        try:
            if git(self.root, "read-tree", "origin/main", env=env).returncode != 0:
                return None
            tree = {}   # one listing of the whole tree, not one git a path
            for entry in git(self.root, "ls-tree", "-r", "-z", "--full-tree", source).stdout.split("\x00"):
                meta, _, name = entry.partition("\t")
                if name:
                    tree[name] = meta.split(" ")
            lines = []
            for path in paths:
                if path in (blobs or {}):
                    lines.append(f"{tree.get(path, ['100644'])[0]} {blobs[path]}\t{path}")
                elif path in tree:
                    mode, _, sha = tree[path]
                    lines.append(f"{mode} {sha}\t{path}")
                else:
                    lines.append(f"0 {'0' * 40}\t{path}")
            if lines and git(self.root, "update-index", "-z", "--index-info", input="\x00".join(lines) + "\x00",
                             env=env).returncode != 0:
                return None
            tree = git(self.root, "write-tree", env=env).stdout.strip()
            base = git(self.root, "rev-parse", "origin/main").stdout.strip()
            remote = git(self.root, "rev-parse", "-q", "--verify", f"refs/remotes/{remote_ref}").stdout.strip()
            if remote:   # unchanged since the last run: keep the same commit, so CI does not rerun
                same_tree = git(self.root, "rev-parse", f"{remote}^{{tree}}").stdout.strip() == tree
                same_base = git(self.root, "rev-parse", f"{remote}^").stdout.strip() == base
                riding = all(git(self.root, "merge-base", "--is-ancestor", p, remote).returncode == 0 for p in parents)
                if riding and ((same_tree and same_base) or self.still_carries(remote, paths, base)):
                    return remote
            # commit-tree ignores commit.gpgsign: the computer signs what it pushes itself (`gate._key_signed`)
            done = git(self.root, "commit-tree", *(["-S"] if SIGNING and not self.cloud else []), tree, "-p", base,
                       *(a for p in parents for a in ("-p", p)), "-m", "chore: sync")
            return done.stdout.strip() if done.returncode == 0 else None
        finally:
            index.unlink(missing_ok=True)

    def still_carries(self, commit: str, paths: list, base: str) -> bool:
        """The pushed commit carries exactly `paths`, as HEAD has them, and origin/main has not
        touched them since it was built: only main moved, so the pull request stays as it is. Its parent must
        be a commit of main: anyone who can push the branch could put `paths` on a tree of their own, and that
        tree would then ride under this run's pull request."""
        def names(*spec: str) -> set | None:
            done = git(self.root, "diff", "--name-only", "--no-renames", "-z", *spec)
            return None if done.returncode else {p for p in done.stdout.split("\x00") if p}
        if git(self.root, "merge-base", "--is-ancestor", commit, base).returncode == 0:
            return False   # it merged already: what is left needs a new commit
        if git(self.root, "merge-base", "--is-ancestor", f"{commit}^", base).returncode != 0:
            return False   # not built on main: not ours to keep
        want = set(paths)
        carried, stale, moved = names(f"{commit}^", commit), names(commit, "HEAD"), names(f"{commit}^", base)
        if carried is None or stale is None or moved is None:
            return False
        return carried == want and not (stale & want) and not (moved & want)

    def riders(self, guarded: list) -> list:
        """The cloud heads adopted here (`adopted_heads`) that the plain commit of a split push keeps as
        parents: built on main, it would otherwise leave their history behind, GitHub would never mark
        their pull requests merged, and the next cloud run would carry them again, with every line they
        added to an append-only file. A head that changes a path the split holds back for the click
        (`guarded`) is left out: the plain commit does not hold its whole change."""
        held = set(guarded)
        kept = []
        for head in self.adopted_heads():
            done = git(self.root, "diff", "--name-only", "--no-renames", "-z", f"refs/remotes/origin/main...{head}")
            if done.returncode == 0 and not {p for p in done.stdout.split("\x00") if p} & held:
                kept.append(head)
        return kept

    def push_branch(self, commit: str, branch: str) -> bool | None:
        """True when pushed, False when the branch already holds the commit, None on failure."""
        current = git(self.root, "rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}").stdout.strip()
        if current == commit:
            return False
        done = git(self.root, "push", "--quiet", "--force", "origin", f"{commit}:refs/heads/{branch}")
        return True if done.returncode == 0 else None

    def mark_protected(self, commit: str) -> None:
        """Keep, in this checkout's git folder, the commit this computer last pushed to its protected branch."""
        try:
            (self.gdir / "sync-protected-pushed").write_text(commit, encoding="utf-8")
        except OSError:
            pass

    def foreign_protected(self, name: str) -> None:
        """`sync/<machine>-protected`, whose pull request the person clicks, holds a commit this computer did not
        push there (`mark_protected`) and main does not hold: someone else pushed it, under this computer's pull
        request. It is reset to main's tree, so that click lands nothing, and a security item says so. shortcut: a
        branch pushed before the mark existed is trusted until this computer pushes it again."""
        tip = git(self.root, "rev-parse", "-q", "--verify", f"refs/remotes/origin/{name}").stdout.strip()
        try:
            mine = (self.gdir / "sync-protected-pushed").read_text(encoding="utf-8").strip()
        except OSError:
            mine = ""
        if not tip or not mine or tip == mine                 or git(self.root, "merge-base", "--is-ancestor", tip, "refs/remotes/origin/main").returncode == 0:
            return
        commit = self.subset_commit([], f"origin/{name}")
        if commit is None or self.push_branch(commit, name) is None:
            self.error(f"push failed ({name}); what someone else pushed there is still on it")
        else:
            self.mark_protected(commit)
            print(f"push: {name} held a commit this computer did not push; it now holds nothing")
        self.leg_items([{"id": f"sync-held-{tip[:8]}", "kind": "security",
                         "text": f"Someone pushed a commit to {name} that this computer did not push; do not merge "
                                 "its pull request.",
                         "recommendation": f"look at who pushed to {name}, and check your GitHub access tokens",
                         "default": "it stays on this list until you answer", "depends": 99}], lambda other: False)

    def publish(self) -> None:
        if not self.has_origin_main():
            return
        branch = f"sync/{hostname()}"
        if not (self.dry or self.args.no_push):
            self.foreign_protected(f"{branch}-protected")
        ahead = int(git(self.root, "rev-list", "--count", "origin/main..HEAD", check=True).stdout.strip() or 0)
        if ahead == 0:
            return
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
                riders = self.riders(guarded) if note == "pushed" else []
                commit = self.subset_commit(paths, f"origin/{name}", riders)
                pushed = None if commit is None else self.push_branch(commit, name)
                if pushed is None:
                    self.error(f"push failed ({name})")
                    continue
                if note == "pushed_protected":
                    self.mark_protected(commit)
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
        """What only the computer has, while the sweep runs in the cloud (spec 4.5), right after main is merged
        in, so that the cloud's run lines are read first. In order: `not_started`, which waited for the merge; the
        transcripts scan (`scan_transcripts`); the cloud pull requests nothing could merge (`adopt_cloud`);
        and, in the workspace, the spaces the cloud does not read (`name_unattached`). `not_started` runs on every
        computer, and not at all when main could not be merged in (the fetch failed, or the merge stopped): without
        the cloud's run lines it would name chains that ran, and stale.yaml stays as it was until a run merges main.
        The rest write files, so they run on the computer the record names (`keeps_leg`); a record that names none
        lets every computer run them, and the merge rules keep what two computers both write from stopping a merge.
        While the routines are paused, the scan waits, as the chain that ran it would. Template sync and AGENTS.md
        are the daily chain's, which that computer keeps. Each step's outcome goes into the run line's notes; a
        step that fails is said and the next one runs, since the landing must still happen. What the leg wrote is
        committed with this run. A merge that moved the routines back to a computer leaves only `not_started`. The
        outcomes, as those notes."""
        import routine
        done: dict = {}
        if not self.behind and self.watch_chains(cloud=True):
            done["not_started"] = True
        host = self.cloud_host()
        if host is not None and self.keeps_leg(host[1]):
            workspace, record = host
            steps = [] if routine.paused_until(record, now) else [
                ("transcripts", "transcripts scan", self.scan_transcripts)]
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
        (`transcripts`). `changes` set those keys (None takes one out); the
        file is written only when that changes it, and one that cannot be written leaves the step to run
        again next time."""
        return self.kept(LEG_STATE, **changes)

    def health_state(self, **changes) -> dict:
        """What the sync keeps about itself on this computer, never in the repository
        (`~/.spaces/sync-health-<space>.json`, beside the leg's clocks): when it last ran (`sync`) and since
        when it could not move each mount (`pins_held`). Kept as `leg_state` keeps its own."""
        return self.kept(HEALTH_STATE, **changes)

    def kept(self, name: str, **changes) -> dict:
        path = Path.home() / ".spaces" / name.format(self.space.name)
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

    def model_free_daily(self, now: dt.datetime) -> bool:
        """The daily chain's steps that need no model, run here when no agent started the daily: status with
        landing, the librarian's proposals and the id index, access, requests, the release of what an earlier
        briefing listed, the answer and leak tests (`chain.daily(model_free=True)`: no morning view, no template
        sync). An agent that is signed out or out of allowance starts nothing, and the daily was the only caller
        of all of these. It runs when stale.yaml says `daily not started` (`not_started` wrote it, and this run
        leaves it: the entry goes when an agent's daily runs), at most every `chain.MODEL_FREE_HOURS`, and only
        where the daily itself would run (`chain.gate`: this computer, not paused; a space whose owner's
        workspace is not on this computer is its owner's). The clock is the run's own line in runs.jsonl
        (`daily_model_free`), which needs no file outside the repository and travels with it; a run that fails
        before the chain wrote one gets a line here, so it is said once and tried again a day later, not every
        15 minutes. It runs also when this run could not merge main: the pages and the filing are this
        checkout's own. What it wrote is committed with this run, or with the next once the files are old
        enough. Never in the way of landing: a failure is said in the run line. True when it ran."""
        import chain
        import yaml
        try:
            data = yaml.safe_load((self.root / chain.STALE).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return False
        if not isinstance(data, dict) or chain.NOT_STARTED.format("daily") not in data:
            return False
        try:
            lines = (self.root / RUNS_PATH).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        for raw in lines:
            if '"daily_model_free"' not in raw:
                continue
            try:
                then = pages.parse_iso(json.loads(raw)["at"])
            except (ValueError, KeyError, TypeError):
                continue
            if then <= now and now - then < self.space.scale(chain.MODEL_FREE_HOURS):
                return False
        try:
            workspace = self.workspace()
            if workspace is None or chain.gate(workspace, now=now, chain="daily"):
                return False
            done = chain.daily(self.root, now=now, model_free=True)
        except Exception as exc:
            self.error(f"the daily's steps without the model failed: {type(exc).__name__}")
            self.append_line({"routine": "daily_model_free", "at": pages.iso(now), "space": self.space.name,
                              "cost_tokens": 0, "notes": f"failed: {type(exc).__name__}"})
            return False
        self.notes["model_free"] = True
        if done.get("errors"):
            self.notes["model_free_errors"] = sorted(e["step"] for e in done["errors"])
        leg = self.cloud_host() is not None
        if not (leg and self.behind):   # as the first reading was made: status just ran, so it is not stale
            self.watch_chains(cloud=leg)
        self.stage_and_commit()
        return True

    def adopt_cloud(self, now: dt.datetime) -> list:
        """The `cloud/<chain>` pull requests of this repository that nothing will merge, merged into this
        checkout with the merge rules (`adopt`), so that they ride this computer's own sync pull request, which
        it repairs as it repairs its own (`heal`); once that lands, GitHub marks them merged. That holds when the
        push is split for a protected change too: its plain part, a commit built on main, keeps each adopted head
        as a parent until main holds it (`adopted_heads`, `riders`), so the next cloud run never carries it again.
        One is adopted when it was opened ADOPT_MINUTES ago or more and conflicts with main or has a failed check
        (`stuck`), and its head is as old (`adopt`): a cloud run waits for its own for less. One whose merge here
        still conflicts is aborted and said in the output alone: the next cloud run sets that branch aside and
        starts fresh from main, so the person has nothing to answer. A fork's pull request is never one. The
        numbers adopted."""
        if self.slug is None or not self.has_origin_main():
            return []
        listed = []
        for chain_name in CLOUD_CHAINS:
            branch = f"cloud/{chain_name}"
            pulls = ghrest.open_pulls(self.gh, self.slug, head=branch)
            if pulls is None:
                continue   # not read: the next run asks again
            listed += [(chain_name, pull) for pull in pulls if pull["head_ref"] == branch and not pull["cross_repo"]]
        adopted, committed = [], False
        for chain_name, pull in listed:
            if not self.stuck(pull, now):
                continue
            if not committed:   # a merge stops at a local change in its way: what the leg wrote so far lands first
                self.stage_and_commit()
                committed = True
            if self.adopt(chain_name, pull["number"], now) == "adopted":
                adopted.append(pull["number"])
        # one that still conflicts here is no item: the next cloud run sets its branch aside and starts fresh from
        # main (`cloud.begin`), and says so on Needs you itself once that keeps happening. The item an earlier
        # template wrote for it goes.
        self.leg_items([], lambda item_id: item_id.startswith("cloud-adopt-"))
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
        person's click. A path the gate trusts only by its author counts as for no login, unless GitHub names this
        checkout's login as the last pusher of the tip (`pushed_by_me`), as the carry: then it is the cloud's own
        upkeep, and a merge of it that conflicts is left, as any other, to the next cloud run, which sets its branch
        aside and starts fresh from main."""
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
        vouch = not self.pushed_by_me(branch, head)   # a tip GitHub says this login pushed is the cloud's own, as carry
        guarded = _protected_moves(self.root, bases, "HEAD", tracking, vouch)
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
        guarded = _protected_moves(self.root, [before], before, "HEAD", vouch)
        if guarded != []:   # git followed a file main moved
            if git(self.root, "reset", "-q", "--keep", before).returncode != 0:
                what = "that changed a protected path" if guarded else "whose changes could not be listed"
                self.error(f"an adopted {branch} {what} could not be undone")
            else:
                print(f"adopt: {why(guarded)}")
            return "left"
        self.adopted_heads(head)
        print(f"adopt: pull request #{number} from {branch} merged here; it rides this computer's sync pull request")
        return "adopted"

    def adopted_heads(self, add: str | None = None) -> list:
        """The cloud heads this checkout adopted (`adopt`; `add` is one more) that HEAD holds and origin/main does
        not yet, kept in the git dir (ADOPTED) until main holds them, so a split push keeps them as parents of
        its plain commit in every run until it lands (`riders`). One HEAD no longer holds is dropped."""
        path = self.gdir / ADOPTED
        try:
            heads = path.read_text(encoding="utf-8").split()
        except OSError:
            heads = []
        before = list(heads)
        if add and add not in heads:
            heads.append(add)

        def holds(ref: str, head: str) -> bool:
            return git(self.root, "merge-base", "--is-ancestor", head, ref).returncode == 0
        kept = [h for h in heads if holds("HEAD", h) and not holds("refs/remotes/origin/main", h)]
        if kept != before:
            try:
                if kept:
                    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                pass   # unrecorded, a split push leaves the head's history behind, as it did before the record
        return kept

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
        item = protected_notice(item_id, len(self.left_out))
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
        committed first (`pushed_item`), so it lands with this run. Before each push what the gate would
        hold (that security item among it) goes on `cloud/<chain>-protected` instead (`split_cloud`), so the
        cloud pull request itself lands on green. Then, unless the push is quiet
        (`quiet_cloud`), the pull request opens through REST and is waited for (`wait_landed`).
        A run whose carry brought in a head whose pull request the person closed without merging since
        (`turned_down`) pushes and opens nothing: its push would bring the work they turned down back, in
        a new pull request or with the next run's carry. The next run starts again from main."""
        if not self.has_origin_main() or git(self.root, "diff", "--quiet", "origin/main", "HEAD").returncode == 0:
            return   # nothing main lacks
        branch = f"cloud/{self.cloud}"
        if self.dry or self.args.no_push:
            print(f"would push to {branch}")
            return
        if self.turned_down():
            self.notes["landing"] = "turned_down"
            print(LANDING["turned_down"].format(branch=branch))
            return
        if self.split_cloud() is None:
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
            if self.split_cloud() is None:   # the carried branch, or the item just committed, may hold a hold
                return
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

    def split_cloud(self) -> list | None:
        """In a cloud run, before the push: what the gate would hold that this run's tools wrote (protected
        paths are left out already, `keep_out`), such as a security item in `sync-cloud.yaml` or a registry
        home a classification set, goes on its own branch `cloud/<chain>-protected`, a commit on main with
        its own pull request for the click, and is put back on `cloud/<chain>` to main's copy in a commit of
        its own, whose body names that branch (HELD_FOR). So the cloud pull request lands on green, and
        nothing waits behind the held change. What an earlier, unmerged push of that branch held is kept
        (`held_before`): a path this run does not hold rides along, and a Needs-you file both hold keeps
        the earlier items too (`kept_items`), so one run never drops another's change or notice. That is
        read from this run's own history, never from the branch, which anyone who can push to it may have
        changed: what they put there is replaced. That history anyone may have written too (a put-back
        lookalike that nets to nothing), so it is read only while GitHub names this run's own login as the
        last pusher of the branch's tip (`pushed_by_me`); no protected path rides along from it (the carry
        also refuses any commit that touches one, `_commit_moves`), and of a Needs-you file only the
        cloud's own notices, word for word (`own_notice`). The paths moved; None when the protected branch
        could not be pushed, and then the cloud branch must not be pushed either: a held change on it reads
        to the next carry as someone else's (`_security_moves`)."""
        try:
            import gate
        except ImportError:
            return []
        if gate.is_template(self.root):
            return []
        name = f"cloud/{self.cloud}-protected"
        tracking = f"refs/remotes/origin/{name}"
        fetched = git(self.root, "fetch", "--quiet", "--no-recurse-submodules", "origin",
                      f"+refs/heads/{name}:{tracking}").returncode == 0
        earlier = git(self.root, "rev-parse", "-q", "--verify", tracking).stdout.strip() if fetched else ""
        if earlier and git(self.root, "merge-base", "--is-ancestor", earlier, "refs/remotes/origin/main").returncode == 0:
            earlier = ""   # merged already: nothing of it is lost
        # what someone else pushed there would wait for the person's click as the cloud's own: replaced, and said
        foreign = self.foreign_held(name, earlier) if earlier else []
        if foreign:
            self.held_item(name, earlier, len(foreign))
        out = git(self.root, "diff", "--name-only", "--no-renames", "-z", "origin/main", "HEAD").stdout
        changed = [p for p in out.split("\x00") if p]
        held = self.held_paths(changed, f"cloud/{self.cloud}") if changed else []
        if not held and not foreign:
            return []
        # an earlier hold rides along where this run's branch leaves the path as main has it, or holds it too
        before = {rel: oid for rel, oid in (self.held_before(name, earlier) if earlier else {}).items()
                  if (rel in held or rel not in changed) and not protected_paths(self.root, [rel])}
        blobs = {}
        for rel, oid in before.items():   # a Needs-you file keeps only the cloud's own notices (`kept_items`)
            merged = self.kept_items(rel, oid) if rel.startswith("_inbound/needs-you/") else oid
            if merged:
                blobs[rel] = merged
        paths = held + [rel for rel in blobs if rel not in held]
        commit = self.subset_commit(paths, f"origin/{name}", blobs=blobs)
        pushed = None if commit is None else self.push_branch(commit, name)
        if pushed is None:   # pushed on cloud/<chain>, the held change would read there as someone else's
            self.error(f"push failed ({name}); nothing is pushed, and the next run does this run's work again")
            return None
        self.notes["pushed_protected"] = len(paths)
        print(f"push: {len(paths)} path(s) the gate would hold to {name}")
        self.open_pr(name, f"chore: sync (cloud {self.cloud}, protected)")
        for rel in held:   # back to main's copy on the cloud branch, so its pull request lands on green
            if git(self.root, "cat-file", "-e", f"origin/main:{rel}").returncode == 0:
                git(self.root, "checkout", "origin/main", "--", rel)
            else:
                git(self.root, "rm", "-q", "-f", "--ignore-unmatch", "--", rel)
        self.commit(f"chore: sync\n\n{HELD_FOR.format(name)}")
        return held

    def held_commits(self, name: str) -> list:
        """The commits of this run's history that put what they held for `name` back to main's copy (HELD_FOR),
        newest first; each one's parent holds the change as a run's tools wrote it. A run that started fresh from
        main (`cloud.begin`'s set-aside) does not hold the branch it replaces, so that branch's history is read too;
        `held_before` keeps only what the cloud's own protected tip still holds. shortcut: the last 20."""
        aside = f"refs/remotes/origin/cloud/{self.cloud}" if self.cloud else ""
        tips = ["HEAD"] + ([aside] if aside and git(self.root, "rev-parse", "-q", "--verify", aside).returncode == 0
                           else [])
        log = git(self.root, "log", "--format=%H", "-F", f"--grep={HELD_FOR.format(name)}", "-n", "20", *tips)
        return log.stdout.split()

    def foreign_held(self, name: str, earlier: str) -> list:
        """The paths on `earlier`, the unmerged tip of `name` (`cloud/<chain>-protected`), that no run of this chain
        put there: none when GitHub names this run's own login as the last pusher of that tip (`pushed_by_me`);
        else every path it changes, against its parent when it is one commit on a commit of main (all that
        `subset_commit` builds), against main otherwise. Neither the tip's words nor this run's history can be the
        proof: the history that puts a held change back (`held_commits`) reaches main or `cloud/<chain>` from
        anyone's pull request or push, a lookalike that nets to nothing included."""
        if self.pushed_by_me(name, earlier):
            return []
        main = "refs/remotes/origin/main"
        parents = git(self.root, "rev-list", "--parents", "-n", "1", earlier).stdout.split()[1:]
        built = len(parents) == 1 and git(self.root, "merge-base", "--is-ancestor", parents[0], main).returncode == 0
        out = git(self.root, "diff", "--name-only", "--no-renames", "-z", parents[0] if built else main, earlier)
        return [p for p in out.stdout.split("\0") if p]

    def held_item(self, name: str, earlier: str, count: int) -> None:
        """The security item for a `name` someone else pushed (`held_branch_notice`), committed into this space's
        own `sync-cloud.yaml` as `pushed_item` commits its own, so the push that replaces that branch holds it."""
        import cloud
        print(f"split: {name} holds {count} path(s) no cloud run put there; this push replaces it")
        item = held_branch_notice(self.cloud, self.space.name, earlier, count, self.now)
        if cloud.write_items(self.root, [item]):
            git(self.root, "add", "--", CLOUD_ITEMS.as_posix())
            self.commit("chore: sync")

    def held_before(self, name: str, earlier: str) -> dict:
        """{path: blob} of what an earlier run of this chain held on `name` (pushed as `earlier`) and still
        waits there unmerged. Read from this run's own history (the cloud branch it carried, or main): the
        commit that put a held path back to main's copy (its body says HELD_FOR `name`), whose parent holds
        the change as the run's tools wrote it. Never from `earlier` itself, which anyone who can push may
        have changed; `earlier` only says it still waits: kept while it holds that same blob, main has not
        touched the path since it was built, nor holds that blob, and the person did not close its pull
        request unmerged. A held deletion is not
        carried. shortcut: the last 20 such commits are read, enough for runs a click never merged."""
        def blob(ref: str, rel: str) -> str:
            return git(self.root, "rev-parse", "-q", "--verify", f"{ref}:{rel}").stdout.strip()

        if self.slug and ghrest.closed_unmerged(self.rest, self.slug, name, earlier):
            return {}   # turned down: the next push holds only what this run holds
        if not self.pushed_by_me(name, earlier):
            return {}   # someone else's tip vouches for no history (`foreign_held`): only this run's holds
        found: dict = {}
        for put_back in self.held_commits(name):   # newest first: the newest change of a path wins
            out = git(self.root, "diff", "--name-only", "--no-renames", "-z", f"{put_back}^", put_back).stdout
            for rel in (p for p in out.split("\0") if p):
                found.setdefault(rel, blob(f"{put_back}^", rel))
        main, base = "refs/remotes/origin/main", f"{earlier}^"
        return {rel: oid for rel, oid in found.items()
                if oid and blob(earlier, rel) == oid and blob(main, rel) != oid
                and blob(base, rel) == blob(main, rel)}

    def kept_items(self, rel: str, theirs: str) -> str | None:
        """For a Needs-you file: a blob holding HEAD's items followed by each item of `theirs` (a blob an
        earlier run held, `held_before`) whose id HEAD's does not have and that is one of this chain's own
        notices word for word (`own_notice`), written into git's object store; None for any other file, or
        when there is nothing to keep. shortcut: an earlier run's other items in the file are not kept;
        `begin` writes its own again while they hold."""
        if not rel.startswith("_inbound/needs-you/") or not rel.endswith((".yaml", ".yml")):
            return None
        import yaml
        ours = _items_at(self.root, f"HEAD:{rel}")
        have = {str(i.get("id")) for i in ours}
        extra = [i for i in _items_at(self.root, theirs) if str(i.get("id")) not in have and self.own_notice(i)]
        if not extra:
            return None
        text = yaml.safe_dump({"items": ours + extra}, sort_keys=False, allow_unicode=True, width=100)
        done = git(self.root, "hash-object", "-w", "--stdin", input=text)
        return done.stdout.strip() or None if done.returncode == 0 else None

    def own_notice(self, item: dict) -> bool:
        """Whether `item` is a security item this chain's sync writes, as it writes it but for its count: one for
        protected paths a run left out (`protected_notice`), or one for a branch someone else pushed
        (`cloud._pushed_item`), or one for a protected branch someone else pushed (`held_branch_notice`). What
        history holds may be anyone's, so only the cloud's own words ride along, naming one of the spaces this
        run works in (`cloud_names`): `begin` writes, into the workspace's file, a notice for each of them."""
        import cloud
        item_id, found = str(item.get("id")), re.search(r"\d+", str(item.get("text")))
        count = int(found.group()) if found else 1
        mine, theirs, held = (f"cloud-protected-{self.cloud}-", f"cloud-branch-{self.cloud}-",
                              f"cloud-held-{self.cloud}-")
        if item_id.startswith(mine):
            return item == protected_notice(item_id, count)
        for prefix, notice in ((theirs, cloud._pushed_item), (held, held_branch_notice)):
            if item_id.startswith(prefix):
                return any(item == notice(self.cloud, name, item_id[len(prefix):], count, self.now)
                           for name in self.cloud_names())
        return False

    def cloud_names(self) -> set:
        """The names of the spaces this cloud run works in: this one's, and those of the roots `cloud.roots`
        finds for the workspace `begin` recorded in the run's state (`cloud._run_workspace`), as `begin` names
        them in its notices. Read from the clones' own space.yaml, never from an item."""
        if getattr(self, "_cloud_names", None) is None:
            import cloud
            import routine
            names = {self.space.name}
            workspace = cloud._run_workspace()
            if workspace:
                try:
                    names |= {spaceyaml.load(r).name
                              for r in cloud.roots(Path(workspace), routine.host(Path(workspace)))}
                except Exception:   # an unreadable root names nothing; this space's own name still stands
                    pass
            self._cloud_names = names
        return self._cloud_names

    def turned_down(self) -> bool:
        """Whether the head the carry brought in (`carried`) is that of a pull request the person closed without
        merging while this run worked. False when GitHub could not be asked."""
        head = self.lease()
        return bool(self.slug) and self.carried() and ghrest.closed_unmerged(self.rest, self.slug,
                                                                             f"cloud/{self.cloud}", head)

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
        `merged`; `closed` when the person closed it; `conflict` past the merges, or when a merge of main
        stops or its push fails; `red` when a failure comes after the repair; `left_open` when the time is
        up. What stays open merges once it is green; the next cloud run carries it, and the computer's sync
        adopts it only while it is in conflict or red (`stuck`)."""
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

    # ----- what stays on this computer ----------------------------------------------------------
    def left_local_health(self) -> None:
        """The files this run left on this computer (a scan finding, a file the scan could not read
        whole, a credential file's name), never silent: each path with why and since when goes to the
        health record `_meta/state/private/left-local.json` (never staged: a name can say more than it
        should; the status routine shows it, `left_local_cell`), the run line counts them (`left_local`),
        and a path left local LEFT_LOCAL_DAYS days running becomes one Needs-you item, with what an agent
        can do about it. A path drops out once it lands, scans clean, or is gone; one this run did not look
        at (changed too recently) keeps its clock. Only a whole run on the computer writes it."""
        if self.dry or self.cloud or getattr(self.args, "only", None) or self.status_paths is None:
            return
        before = read_left_local(self.root)
        now = pages.iso(self.now)
        clean = {rel for rel, _ in self.clean}
        files = {rel: {"since": (before.get(rel) or {}).get("since") or now, "why": sorted(kinds),
                       **({"blob": self.judged[rel], "scan": scanner_id()} if rel in self.judged else {})}
                 for rel, kinds in self.left_local.items()}
        for rel, entry in before.items():
            if rel not in files and rel not in clean and rel in self.status_paths \
                    and os.path.lexists(self.root / rel):
                files[rel] = entry   # not looked at this run: its clock goes on
        if files:
            self.notes["left_local"] = len(files)
        if files != before:
            path = self.root / LEFT_LOCAL
            try:
                if files:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps({"files": files}, indent=1, sort_keys=True) + "\n",
                                    encoding="utf-8", newline="\n")
                else:
                    path.unlink(missing_ok=True)
            except OSError as exc:
                self.error(f"the left-local record could not be written ({type(exc).__name__})")
        aged = sorted(rel for rel, entry in files.items() if self.aged(entry.get("since")))
        if not aged and not any(str(i.get("id", "")).startswith(LEFT_LOCAL_ITEM) for i in items_in(self.root / SYNC_ITEMS)):
            return
        item_id = LEFT_LOCAL_ITEM + hashlib.sha256("\n".join(aged).encode("utf-8")).hexdigest()[:8]
        count = len(aged)
        item = {"id": item_id, "kind": "request",
                "text": (f"{count} file(s) have stayed on this computer for {LEFT_LOCAL_DAYS} days or more: the "
                         "scan found what looks like a secret in them, could not read them whole, or the name is "
                         f"a credential file's. Each is named, with why, in {LEFT_LOCAL.as_posix()}."),
                "recommendation": ("yes: an agent goes through the list with you; a real secret moves to your "
                                   "password manager and the line says where it lives, a false hit in an export "
                                   "is exported again as text or moved under _scratch/, a credential file moves "
                                   "out of the space"),
                "default": "they stay on this computer and never land; nothing else waits on them", "depends": 1}
        self.leg_items([item] if aged else [],
                       lambda other: other.startswith(LEFT_LOCAL_ITEM) and other != item_id)

    def aged(self, since) -> bool:
        try:
            return self.now - pages.parse_iso(since) >= self.space.scale(LEFT_LOCAL_DAYS * 24)
        except (TypeError, ValueError, OverflowError):
            return False

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


_SCANNER: list = []


def scanner_id() -> str:
    """The scan's own version, a hash of scan.py: a verdict the left-local record keeps (`scan_staged`) is
    reused only under the scan that gave it, so a better scan reads the file again."""
    if not _SCANNER:
        _SCANNER.append(hashlib.sha256(Path(scan.__file__).read_bytes()).hexdigest()[:12])
    return _SCANNER[0]


def read_left_local(root: Path) -> dict:
    """{path: {"since", "why"}} from the left-local record (`Run.left_local_health`); empty when there is none
    or it does not read."""
    try:
        data = json.loads((Path(root) / LEFT_LOCAL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = data.get("files") if isinstance(data, dict) else None
    return {str(k): v for k, v in files.items() if isinstance(v, dict)} if isinstance(files, dict) else {}


def left_local_cell(root: Path) -> str:
    """One cell for the health page: how many files the sync keeps on this computer and since when, from the
    left-local record; the paths stay in the record, which never leaves the computer."""
    files = read_left_local(root)
    if not files:
        return "left local none"
    oldest = min(str(entry.get("since") or "") for entry in files.values())[:10]
    return f"left local {len(files)} (since {oldest}, see {LEFT_LOCAL.as_posix()})"


def gh_login(gh) -> str | None:
    """The GitHub login `gh` runs as (`gh api user`); None when GitHub cannot be asked."""
    done = gh("api", "user", "--jq", ".login")
    said = done.stdout.strip() if done is not None and done.returncode == 0 else ""
    return said or None


def pushed_by(gh, slug: str, branch: str, sha: str, me: str | None) -> bool:
    """Whether GitHub's repository activity names the login `me` as the one who last pushed `branch`, and to
    `sha`. A branch's commits, and the history they carry, anyone who can push may write; only GitHub says who
    pushed (on every plan, a Free private repository's included). False when it cannot be asked, so what cannot
    be vouched for is never trusted."""
    route = f"repos/{slug}/activity?ref=refs/heads/{branch}&per_page=1"
    listed = ghrest._answer(gh, "api", route) if slug and me and sha else None
    last = listed[0] if isinstance(listed, list) and listed and isinstance(listed[0], dict) else {}
    who = str(ghrest._dict(last.get("actor")).get("login") or "")
    return bool(me) and last.get("after") == sha and who.casefold() == me.casefold()


def _named(reason: str, paths) -> str | None:
    """The path among `paths` a gate reason is about (`<path>: ...`), the longest one when several fit."""
    return next((rel for rel in sorted(paths, key=len, reverse=True) if str(reason).startswith(f"{rel}:")), None)


def protected_notice(item_id: str, count: int) -> dict:
    """The security item a cloud run writes for the protected paths it left out (`protected_item`)."""
    return {"id": item_id, "kind": "security",
            "text": "A cloud run changed 1 protected file; it was not landed." if count == 1 else
                    f"A cloud run changed {count} protected files; they were not landed.",
            "recommendation": "look at what changed in the cloud run's log before you run it again",
            "default": "nothing protected lands from the cloud", "depends": 99}


def _items_at(root: Path, spec: str) -> list:
    """The items (dicts) of the Needs-you file `git show` gives for `spec` (`<ref>:<path>` or a blob); empty when
    it cannot be read."""
    import yaml
    done = git(root, "show", spec)
    try:
        data = yaml.safe_load(done.stdout) if done.returncode == 0 else None
    except yaml.YAMLError:
        data = None
    rows = data.get("items") if isinstance(data, dict) else None
    return [i for i in rows if isinstance(i, dict)] if isinstance(rows, list) else []


def held_branch_notice(chain: str, name: str, head, count: int, now: dt.datetime) -> dict:
    """The security item a cloud run writes when `cloud/<chain>-protected`, whose pull request the person
    clicks, holds what no run of this chain pushed there (`foreign_held`): the push replaced it."""
    import cloud
    branch, files = f"cloud/{chain}-protected", "1 file" if count == 1 else f"{count} files"
    return cloud._item(f"cloud-held-{chain}-{cloud._short(head, now)}", "security",
                       f"Someone pushed a change to {files} on {branch} in {name}; do not merge what they pushed. "
                       f"The cloud {cloud.WORDS[chain]}'s push replaced it.",
                       f"look at who pushed to {branch} in {name}, and check your GitHub access tokens",
                       "it stays on this list until you answer")


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
        if gate.protected_reason(posix, prefixes) \
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


def _protected_moves(root: Path, bases: list, ours: str, theirs: str, vouch: bool = True) -> list | None:
    """The protected paths (`protected_changes`) where merging `theirs` into `ours` can change a file: one `theirs`
    holds otherwise than some merge base does (git merges against all of them, so a branch that matches one base
    can still undo what another holds), otherwise than `ours`, and otherwise than origin/main, whose version landed
    through the gate. Where `theirs` matches every base the merge keeps ours; where it differs from every base only
    in answer tests set aside or brought back, the merge changes no more than that. A link (`_link_moves`), a
    protected path any one commit changes (`_commit_moves`), a new security item on Needs you
    (`_security_moves`) and a path the gate trusts only by its author (`_vouched_moves`, unless `vouch` is False:
    the carry's own tip, which GitHub says this login pushed) count too. None when git or the gate could not list
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
    links = _link_moves(root, bases, theirs)
    if links is None:
        return None
    guarded = protected_changes(root, sorted(found), bases, theirs)
    commits = _commit_moves(root, ours, theirs)
    if commits is None:
        return None
    vouched = _vouched_moves(root, found, theirs) if vouch else set()
    if vouched is None:
        return None
    extra = links | set(commits) | set(_security_moves(root, sorted(found), bases, ours, theirs)) | vouched
    return sorted(set(guarded) | extra) if extra else guarded


def _vouched_moves(root: Path, found: set, theirs: str) -> set | None:
    """The paths among `found` the gate holds at `theirs` for an author with no login: a signed page, an owner's
    ledger row or registry field, a release, which the gate trusts by who opened the pull request. The cloud
    opens its own with the owner's login, so carried, a colleague's push to `cloud/<chain>` would land as the
    owner's. A reason that names no path (a release off its sender's branch) can be about any of them, so then
    all count; a cloud run writes no release into its own space. Protected paths are `protected_changes`' to judge
    (the answer tests set aside). None when the gate could not judge."""
    import gate
    # judged without the protected paths, so a reason about them alone (a template release) names none of the rest
    rest = {rel for rel in found if not protected_paths(root, [rel])}
    if not rest or gate.is_template(root):
        return set()
    try:
        verdict, reasons = gate.classify(root, sorted(rest), base_ref="refs/remotes/origin/main", head=theirs,
                                         author="")
    except Exception:
        return None
    if verdict != gate.PROTECTED:
        return set()
    named = {_named(reason, rest) for reason in reasons}
    return rest if None in named else named


def _commit_moves(root: Path, ours: str, theirs: str) -> list | None:
    """The protected paths (`protected_changes`) one commit `theirs` brings changes against every one of its
    parents (for a merge, what it holds that no side did): the whole change can net to nothing, a commit that
    adds a file and one that takes it out again, and still leave a commit holding it that a later read of the
    history takes for a run's own (`held_before`). A cloud run commits no protected path, the answer tests it
    set aside apart. Commits `ours` or origin/main hold are not read. None when git could not list them."""
    done = git(root, "rev-list", "--parents", theirs, "--not", ours, "refs/remotes/origin/main")
    if done.returncode != 0:
        return None
    found: set = set()
    for line in done.stdout.splitlines():
        commit, *parents = line.split()
        moved = None
        for parent in parents:   # a root commit shares no history: the carry never merges one
            diff = git(root, "diff", "--name-only", "--no-renames", "-z", parent, commit)
            if diff.returncode != 0:
                return None
            paths = {p for p in diff.stdout.split("\0") if p}
            moved = paths if moved is None else moved & paths
        found |= set(protected_changes(root, sorted(moved or ()), parents, commit))
    return sorted(found)


def _security_moves(root: Path, changed: list, bases: list, ours: str, theirs: str) -> list:
    """The Needs-you files among `changed` where `theirs` holds a security item no base does: a cloud run puts
    every such item it writes on `cloud/<chain>-protected` and its own file back to main's copy (`split_cloud`),
    so one on the branch itself came from someone else, and carried it would ride the cloud's protected pull
    request under the cloud's name. An item `ours` or origin/main holds already, word for word, is no change;
    one that keeps a held id with other words is (no writer of a security item rewrites one in place)."""
    import gate

    def items(ref: str, rel: str) -> list:
        rows = gate._yaml_at(root, ref, rel).get("items")
        return [i for i in rows if isinstance(i, dict) and i.get("kind") == "security"] \
            if isinstance(rows, list) else []

    def new(rel: str, ref: str) -> bool:
        known = items(ref, rel) + items(ours, rel) + items("refs/remotes/origin/main", rel)
        return any(item not in known for item in items(theirs, rel))
    return [rel for rel in changed if gate._posix(rel).casefold().startswith(gate.NEEDS_YOU)
            and rel.endswith((".yaml", ".yml")) and any(new(rel, ref) for ref in bases)]


def _listed_submodules(root: Path, ref: str) -> set:
    """The submodule paths `.gitmodules` names at `ref`: the mounts, the only gitlinks a space holds."""
    done = git(root, "config", "--blob", f"{ref}:.gitmodules", "--get-regexp", r"^submodule\..*\.path$")
    return {line.split(" ", 1)[1].strip() for line in done.stdout.splitlines() if " " in line} \
        if done.returncode == 0 else set()


def _link_moves(root: Path, bases: list, theirs: str) -> set | None:
    """The paths `theirs` makes a symbolic link (mode 120000), or a submodule at a path no
    `.gitmodules` of a base names, against any of `bases`: a link in the tree points a later write
    or read anywhere, so it counts as a protected change. None when git could not list them."""
    found: set = set()
    for ref in bases:
        done = git(root, "diff", "--raw", "-z", "--no-renames", "--no-abbrev", ref, theirs)
        if done.returncode != 0:
            return None
        listed = _listed_submodules(root, ref)
        fields = done.stdout.split("\0")
        for meta, rel in zip(fields[0::2], fields[1::2]):
            parts = meta.lstrip(":").split()
            if len(parts) >= 5 and parts[4][:1] != "D" and (
                    parts[1] == "120000" or (parts[1] == "160000" and rel not in listed)):
                found.add(rel)
    return found


def tracked_links(root: Path) -> set:
    """The paths the index holds as symbolic links (mode 120000) or as submodules no `.gitmodules`
    at HEAD names. Empty when git cannot say."""
    done = git(root, "ls-files", "-s", "-z")
    if done.returncode != 0:
        return set()
    listed = _listed_submodules(root, "HEAD")
    out = set()
    for entry in done.stdout.split("\0"):
        meta, _, rel = entry.partition("\t")
        mode = meta.split(" ", 1)[0]
        if mode == "120000" or (mode == "160000" and rel not in listed):
            out.add(rel)
    return out


def _as_it_was(root: Path, before: str) -> bool:
    """Whether an aborted merge left the clone as it was at `before`: HEAD there, no merge in progress, and
    nothing tracked changed (untracked files are not the merge's; a mount's own state is not looked into)."""
    if git(root, "rev-parse", "HEAD").stdout.strip() != before:
        return False
    if git(root, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0:
        return False
    done = git(root, "status", "--porcelain=v1", "--untracked-files=no", "--ignore-submodules=all")
    return done.returncode == 0 and not done.stdout.strip()


@monitor_off()
def held_text(root: Path, chain: str, rel: str) -> str | None:
    """The text of `rel` on the earlier cloud run's branch as the carry fetched it (`refs/remotes/origin/cloud/
    <chain>`), or None when there is no such branch or file. What a fresh start counts from: a branch that is set
    aside never merges, so what it held is read there."""
    if chain not in CLOUD_CHAINS:
        raise ValueError(f"no cloud chain {chain!r}: {', '.join(CLOUD_CHAINS)}")
    done = git(Path(root), "show", f"refs/remotes/origin/cloud/{chain}:{Path(rel).as_posix()}")
    return done.stdout if done.returncode == 0 else None


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
    is no change (`_protected_moves`). A path the gate trusts only by its author counts as for no login, unless
    GitHub names this checkout's login as the last pusher of the tip (`pushed_by`): then it is the cloud's own
    upkeep, a registry or ledger row its run wrote. What the merge did is checked again, since git follows a file
    main moved, and undone when a protected file then reads otherwise. The new run's push replaces
    the branch.
    The merge runs with the merge rules (`resolve`, `keep_ours`, as `merge_in`); one that still
    conflicts is aborted (`"conflict": True`, with what kept it from settling in `"error"`), and the
    run must not work on top of it. When the abort left the clone as it was before the merge (`HEAD`
    unmoved, nothing tracked changed, no merge in progress) that is `"undone": True`: the clone is
    main's again, so the run sets the branch aside and starts fresh from main (`cloud.begin`), which
    loses nothing, since what that run wrote comes again from main's cursors, and its leased push
    replaces the branch. An abort that left anything is `"error"` too. A branch main already holds is
    not carried again. `{"carried": True, "head": <sha>}` when it merged.

    `"error"` says what could not be done (origin not reached, space.yaml not read, or a merge that
    changed a protected path and that `reset --keep` could not take back, which leaves that merge in
    the clone). In every case with `"error"`, or `"conflict"` without `"undone"`, the run stops: it
    never works on top of what the carry left. A chain that is not one of `CLOUD_CHAINS` is a ValueError. Whatever
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
        slug, gh = ghrest.repo_slug(root), gh or ghrest.runner(root)
        if not head or (slug and ghrest.closed_unmerged(gh, slug, branch, head)):
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
        # the cloud's own tip (GitHub names this login as its last pusher): what the gate trusts by author is its own
        vouch = not (slug and pushed_by(gh, slug, branch, head, gh_login(gh)))
        guarded = _protected_moves(root, bases, "HEAD", tracking, vouch)
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
            if _as_it_was(root, before):
                result["undone"] = True   # the clone is main's again: a run may start fresh on it
            else:
                result["error"] = "; ".join(filter(None, [result.get("error"),
                                                          "a carry that conflicted could not be taken back"]))
            return result
        # what the merge did, file by file: git follows a file main moved, so a change the branch made to a plain
        # file can land under a protected path its own paths never named. The lists were read before the merge
        # (gate, routine and tests_run are imported), and a space.yaml the merge changed is protected itself
        guarded = _protected_moves(root, [before], before, "HEAD", vouch)
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
    parser.add_argument("--signing-key", action="store_true",
                        help="make this computer's commit-signing key and list it in .github/allowed_signers")
    args = parser.parse_args(argv)
    if args.cloud and (args.release_leg or args.only):
        parser.error("--cloud lands a cloud run whole: not with --release-leg or --only")
    return args


def main(argv=None) -> int:
    args = parse(sys.argv[1:] if argv is None else argv)
    if args.merge_latest:   # git runs the watermark driver mid-merge: no lock, no run line
        return merge_latest(*(Path(p) for p in args.merge_latest))
    if os.environ.get("CLAUDE_CODE_REMOTE") == "true" and not args.cloud:
        print("sync: a cloud run lands only with --cloud <chain>; the computer's landing runs on the computer")
        return ERROR
    if args.cloud:   # every git a cloud run's landing starts, a mount's included, with the file monitor off
        with monitor_off():
            return sync_once(args)
    if args.signing_key:
        return make_signing_key(Path(args.root).resolve())
    with signing_on() as why:   # the computer's commits carry its key: the gate's proof that no cloud run made them
        args.cannot_sign = why
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
        if not args.dry_run and not args.cloud and not args.only:
            left_behind(root, gdir, busy)
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
            if not args.only and not run.dry:
                run.sync_gap()
            if not args.only and not run.dry and not leg:
                run.watch_chains()
            if args.release_leg and not args.only:
                run.release_leg()
            run.land_structure()
            run.stage_and_commit()
            if not args.only:
                run.advance_pins()
            run.merge_in()
            # after the merge, onto main's own file (the next run commits the line); a run left behind main leaves
            # the key, and its stale.yaml row, as they were
            if not run.dry and (getattr(args, "cannot_sign", None) or not run.behind):
                why = getattr(args, "cannot_sign", None) or keep_signing_key(root)
                signing_health(root, why, run.now)
                if why:
                    run.notes["cannot_sign"] = why
                    print(f"sync: {CANNOT_SIGN} ({why}); its commits land unsigned, a signature waits for the click")
            if leg:
                run.computer_leg(run.now)
            if not args.only and not run.dry:
                run.model_free_daily(run.now)
                run.stale_item()
            if not args.only:
                run.set_aside_tests()
                if not run.dry and not args.no_push:
                    run.heal()
            run.publish()
            run.left_local_health()
    except Exception as exc:  # unexpected: record, never lose the lock
        run.error(f"{type(exc).__name__}: {str(exc)[:200]}")
        run.apart = getattr(run, "apart", None) or ("run", type(exc).__name__)
    finally:
        try:   # said on Needs you once the same cause has kept this computer apart for a day (`behind_health`)
            run.behind_health()
        except Exception as exc:
            run.error(f"behind check: {type(exc).__name__}")
        if not run.dry and not args.cloud:   # a cloud run's line went into its commit (`cloud_line`)
            run.record()
        run.unlock()
    code = ERROR if run.notes.get("errors") else OK
    if not args.only and not run.dry and not args.cloud:   # the progress page is the computer's
        refresh_page(root)
    return code


if __name__ == "__main__":
    sys.exit(main())
