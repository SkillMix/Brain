---
name: sync
description: What the sync script does every 15 minutes from the OS scheduler, as the only process that runs git, and how to read its run lines. Use when the person asks why something has not landed or asks to land now, or when a routine needs to understand the landing path. Never run git yourself.
---

# sync: agents edit files, the script commits, the platform merges

Hard rule 7. Sessions do not commit, so a crash loses nothing; the next run lands what is
on disk.

## When

- Scheduled: every 15 minutes from the OS scheduler, registered by `onboard` (a hidden Task
  Scheduler task that also runs on battery on Windows, a cron line on macOS and Linux, a
  systemd user timer on a Linux without cron), one task per checkout on the machine.
- By hand, only when the person asks to land now or asks why something did not land.

## Run

`python _meta/tools/sync.py --root <root>` (`--dry-run` shows what it would do and writes
nothing; `--no-push` for a checkout without a remote; `--release-leg` ships due queue items
first). Running the script is the sanctioned way to land now; running git is not.

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

## Judgment left to you

None on landing. Reading `_meta/state/runs.jsonl` (one JSON object per line, append-only):
take the last line with `"routine": "sync"`; its `notes` say what it staged, how many scan
hits it unstaged, whether it committed, merged and pushed, the pull request, and `skipped`
with the reason when it did nothing. Translate for the person: a skip on a merge in
progress means another process is mid-merge, wait a run; a scan count above zero means a
changed file holds something that looks like a secret, so name the files that changed and
let the person look (never quote the match); a conflict is resolved by the person or the
parent's owner, not by you.

## Never

- Never run `git add`, `commit`, `push`, `merge`, `rebase` or `reset` yourself (the
  permission profile denies them; a denial is the system working).
- Never resolve a conflict, remove the lock or edit anything under `.git/`.
- Never edit a flagged file so the scan passes: that weakens a control; the person decides.
- Never stage `personal/`, `academic/` or `_scratch/` by any path; never push to `main`.

## Scheduled invocation

Not an agent routine. The OS task runs `python _meta/tools/sync.py --root <root>` every 15
minutes; `onboard` registers it and `onboard --check` proves it is there
(`_meta/state/sync-task.txt` holds the command offline). An agent that needs to know whether
work landed reads the run line, as above.

## Behaviour scenarios

`_meta/tests/behaviour/sync.yaml`: in a temp repository with a bare origin, `--no-push`
commits an edited tracked file and an old untracked one, never `personal/x.md` nor a file
holding a private key; a second run commits nothing.
