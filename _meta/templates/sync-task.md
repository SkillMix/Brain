# Registering the sync task

`onboard` registers `_meta/tools/sync.py` with the OS scheduler on every machine where
the owner edits this space: every 15 minutes, no agent, no token. The script commits,
merges `origin/main` in, pushes `sync/<hostname>` and opens the pull request that the
platform merges (vision section 12, landing design section 3), and with `--release-leg` it
ships the listed releases whose window has passed into their destinations' clones under
`~/.spaces/.release/` (cloned on first use). Nothing else runs git.

Substitute `<name>` with `name` from `space.yaml` and `<root>` with the absolute path
of the checkout. The scheduler's `python` must be the one with PyYAML installed; give
the full interpreter path when in doubt.

## Windows (Task Scheduler)

Register:

    schtasks /Create /SC MINUTE /MO 15 /TN "space-sync-<name>" /TR "python <root>\_meta\tools\sync.py --root <root>"

Add `/F` to replace an existing task of the same name on a re-run.

Read-back (the task exists, is Ready, and shows the 15-minute cadence):

    schtasks /Query /TN "space-sync-<name>" /V /FO LIST

Remove (offboard):

    schtasks /Delete /TN "space-sync-<name>" /F

## Unix (cron)

Register by appending to the owner's crontab (`crontab -e`, or piped in by onboard):

    */15 * * * * cd <root> && python _meta/tools/sync.py --release-leg

Read-back (exactly one line for this root):

    crontab -l | grep sync.py

Remove (offboard): drop the line again, for example
`crontab -l | grep -v '<root>' | crontab -`.

## What a healthy registration looks like

- `_meta/state/runs.jsonl` gains a `sync` line when a run did something, and one
  heartbeat line (`notes.heartbeat: true`) per day otherwise. A day without either
  means the task is not running.
- `git log --oneline -5` on the checkout shows `chore: sync` and `chore: pins`
  commits; nobody made them by hand.
- A run that finds git mid-operation or another run holding `.git/sync.lock` exits
  with code 2 and leaves everything as it was.
