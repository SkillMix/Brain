# Registering the sync task

`onboard` registers `_meta/tools/sync.py` with the OS scheduler on every machine where
the owner edits this space: every 15 minutes, no agent, no token. The script commits,
merges `origin/main` in, pushes `sync/<hostname>` and opens the pull request that the
platform merges (vision section 12, landing design section 3), and with `--release-leg` it
ships the listed releases whose window has passed into their destinations' clones under
`~/.spaces/.release/` (cloned on first use). Nothing else runs git.

Substitute `<name>` with `name` from `space.yaml` and `<root>` with the absolute path
of the checkout. `<python>` is the interpreter that ran onboarding (the one with PyYAML; on a
system that refuses `pip --user`, `~/.spaces/venv`).

## Windows (Task Scheduler)

`onboard` registers the task from XML (`schtasks /Create /F /TN "space-sync-<name>" /XML
<file>`), because `schtasks /Create` alone cannot set what the task needs: it repeats every 15
minutes, runs `%SystemRoot%\System32\conhost.exe --headless "<python>" "<root>\_meta\tools\sync.py"
--root "<root>" --release-leg` (no console window), starts and keeps running on battery, and
runs a start it missed while the machine slept as soon as it wakes.

Read-back (`onboard --check` does this and names what is missing):

    schtasks /Query /TN "space-sync-<name>" /XML

shows `<DisallowStartIfOnBatteries>false`, `<StopIfGoingOnBatteries>false`,
`<StartWhenAvailable>true` and `conhost.exe`.

Remove (offboard):

    schtasks /Delete /TN "space-sync-<name>" /F

## macOS and Linux (cron)

One line in the owner's crontab, replaced by its marker on a re-run:

    */15 * * * * cd "<root>" && PATH="<PATH at registration>" "<python>" "<root>/_meta/tools/sync.py" --root "<root>" --release-leg >> "$HOME/.spaces/logs/space-sync-<name>.log" 2>&1  # space-sync-<name>

The PATH is the one onboarding saw, so cron finds `gh` and `git` (Homebrew's `/opt/homebrew/bin`
is not on cron's own). Output goes to the log, not to local mail. A run missed while the
machine slept is simply the next one.

Read-back: `crontab -l | grep space-sync-<name>`. Remove (offboard): the line with the marker.

## Linux without cron (systemd user timer)

`~/.config/systemd/user/space-sync-<name>.service` and `.timer` (`OnCalendar=*:0/15`,
`Persistent=true`, so a start missed while asleep runs on waking), enabled with
`systemctl --user enable --now space-sync-<name>.timer`. Read-back:
`systemctl --user is-active space-sync-<name>.timer`. A user timer runs while the person is
logged in; `loginctl enable-linger` keeps it running when they are not.

## What a healthy registration looks like

- `_meta/state/runs.jsonl` gains a `sync` line when a run did something, and one
  heartbeat line (`notes.heartbeat: true`) per day otherwise. A day without either
  means the task is not running.
- `git log --oneline -5` on the checkout shows `chore: sync` and `chore: pins`
  commits; nobody made them by hand.
- A run that finds git mid-operation or another run holding `.git/sync.lock` exits
  with code 2 and leaves everything as it was.
