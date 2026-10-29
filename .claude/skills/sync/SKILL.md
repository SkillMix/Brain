---
name: sync
description: What the sync script does every 15 minutes from the OS scheduler and once at the end of a cloud sweep run, as the only process that runs git, and how to read its run lines. Use when the person asks why something has not landed or asks to land now, or when a routine needs to understand the landing path. Never run git yourself.
---

# sync: agents edit files, the script commits, the platform merges

Hard rule 7. Sessions do not commit, so a crash loses nothing; the next run lands what is
on disk. A cloud sweep run starts it once at its end, for each of its spaces.

## When

- Scheduled: every 15 minutes from the OS scheduler, registered by `onboard` (a hidden Task
  Scheduler task that also runs on battery on Windows, a launch agent on macOS, a systemd user
  timer on a Linux that has a user session, else a cron line), one task per checkout on the machine.
  All but cron run a start missed while the machine slept as soon as it wakes.
- By hand, only when the person asks to land now or asks why something did not land.
- In a cloud sweep run: once at its end for each of its spaces, the workspace last, whether
  or not the run wrote in it, as the cloud prompt says (`_meta/templates/cloud/sweep-chain.md`);
  a proof run lands its workspace alone, like any other landing: a protected file left out
  raises the security item.

## Run

`python _meta/tools/sync.py --root <root>` (`--dry-run` shows what it would do and writes
nothing; `--no-push` for a checkout without a remote; `--release-leg` ships due queue items
first). Running the script is the sanctioned way to land now; running git is not.
`--cloud sweep` is a cloud run's landing; never start it on a computer.

## What the tool already did

Took the lock `.git/sync.lock` and skipped when a merge, rebase or index lock was in
progress; staged tracked changes and untracked UTF-8 text files under 1 MB older than ten
minutes, never `personal/`, `academic/`, `_scratch/` or a mount; ran the secret scan and
unstaged every hit, counted, never quoted; committed `chore: sync`; advanced clean mount
pins (`chore: pins`); fetched without recursing into mounts; merged `origin/main`
(append-only files by union; a conflict aborts the merge and is recorded); pushed the
changes to `sync/<machine>` (a hash, never the machine's name) and opened a pull request
when none was open, with changes under `protected:` paths on their own branch
`sync/<machine>-protected` and pull request; one run
line. The platform merges the first on green; the protected one waits for the space owner's
one click, and nothing else waits behind it. Every push costs a paid check, so journal lines
and run logs alone wait up to an hour, run lines and mount pins alone up to six hours (the run
line says `waiting`), and a waiting pull request is not pushed again when only main moved.
When the merge could not bring main in (a conflict, or a file the scan left local that main also
changed, which git will not merge over), the push carries only what this checkout changed since
the two parted: a path main changed too waits here for the merge (the run line says
`waiting_merge`), unless main holds only an earlier copy of ours, so a push never undoes what main
got, such as a cloud run's lines in an append-only file.
The merge rules, written into the space's own git, settle what GitHub's merge cannot: a
watermark keeps the later mark, generated pages and state keep this checkout's side, and an
item under `_inbound/` or `_outbound/` that one side deleted stays deleted.

In a cloud run (`--cloud sweep`) it commits once, with its own run line, and new files need no
minimum age. It never lands a protected change: `AGENTS.md` is put back quietly, any other
protected path stays in the clone and is one security item. A line the scan hits is replaced by
`[a line held back: <kind>]` and the page lands. It pushes `cloud/<chain>`, leased to the head
the run's `begin` saw, opens the pull request through REST and waits up to nine minutes for its
merge, merging main in again while it conflicts and repairing red checks once; what stays open
the next cloud run carries in. Run lines and marks alone open no pull request. No pins, release
leg or chain clocks run there.

While the routines run in the cloud (`_meta/state/routine-host.yaml` has `runner: "cloud"`), the
computer's sync adds its leg right after the merge. Every computer runs `not_started`, which reads
the cloud's run lines first, and those the cloud branches hold. The computer the record names
(`computer`; every computer when it names none) also runs:
- the transcripts scan, at most every six hours (template sync and `AGENTS.md` stay with its
  daily chain, which that computer keeps);
- adopting a cloud pull request only when both hold: it is still open after 30 minutes, and it
  conflicts with main or its checks are red (`Run.stuck`, `Run.adopt`). Merged here, it rides this
  computer's own pull request (a split push keeps its head as a parent of the plain part). A
  green one that is only slow is not adopted, because auto-merge lands it;
- one Needs-you item naming the spaces the cloud does not read.

## Judgment left to you

None on landing. Reading `_meta/state/runs.jsonl` (one JSON object per line, append-only):
take the last line with `"routine": "sync"`; its `notes` say what it staged, how many scan
hits it unstaged, whether it committed, merged and pushed, the pull request, and `skipped`
with the reason when it did nothing. Translate for the person: a skip on a merge in
progress means another process is mid-merge, wait a run; a scan count above zero means a
changed file holds something that looks like a secret, so name the files that changed and
let the person look (never quote the match); a conflict is resolved by the person or the
parent's owner, not by you. A `conflict` that comes back every run while the scan count stays
above zero is often a file the scan left local that main keeps changing (`runs.jsonl` once a
cloud run lands lines): main's changes to it reach this checkout only after the person has looked
at the flagged line, and nothing of either side is lost meanwhile.

A cloud run's line has `"cloud": "<chain>"`; its `notes` say how many paths it `staged`,
whether it `carried` an earlier run that had not merged, the files `held` (too large, not text,
or unreadable with their lines held back) and how many protected paths it `left_out`. What
became of its pull request is in the task's run log on claude.ai, not in the line; the next
run's `carried` says it had not merged. On the computer, the leg's outcomes join the sync line's
`notes`: `not_started`, `transcripts`, `template_sync`, `agents_md` (written again), `adopted`
(the cloud pull requests it merged here) and `unattached` (the spaces named). Translate for the
person: nothing of a cloud run that did not land is lost, since the next run carries it and the
computer adopts one nothing else will merge; a `left_out` above zero is a security item on Needs
you, so read the run's log with them before the task runs again.

## Never

- Never run `git add`, `commit`, `push`, `merge`, `rebase` or `reset` yourself (the
  permission profile denies them; a denial is the system working).
- Never resolve a conflict, remove the lock or edit anything under `.git/`.
- Never edit a flagged file so the scan passes: that weakens a control; the person decides.
- Never stage `personal/`, `academic/` or `_scratch/` by any path; never push to `main`.
- Never merge, close or push to a `cloud/<chain>` pull request: the next cloud run or the
  computer's sync lands it.

## Scheduled invocation

Not an agent routine. The OS task runs `python _meta/tools/sync.py --root <root>` every 15
minutes; `onboard` registers it and `onboard --check` proves it is there
(`_meta/state/sync-task.txt` holds the command offline). An agent that needs to know whether
work landed reads the run line, as above.

## Behaviour scenarios

`_meta/tests/behaviour/sync.yaml`: in a temp repository with a bare origin, `--no-push`
commits an edited tracked file and an old untracked one, never `personal/x.md` nor a file
holding a private key; a second run commits nothing.
