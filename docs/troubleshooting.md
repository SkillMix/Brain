# Troubleshooting

Each entry: what the person sees, why, and what the agent does. `onboard --check` re-runs every
read-back and names what is missing.

## Nothing lands while the laptop is unplugged

Seen: no `chore: sync` commits for the hours the laptop ran on battery.
Why: `schtasks /Create` registers a task that never starts on battery and stops when the power
cable comes out.
The agent: onboard registers the task from XML with both battery settings off and
`StartWhenAvailable` on; `onboard --check` reads them back and says "registered without: starts
on battery" for a task made the old way. Running onboard again re-registers it.

## A console window flashes every 15 minutes (Windows)

Why: the task started `python.exe` directly.
The agent: the task runs `conhost.exe --headless` with the same command; re-running onboard
re-registers an old task.

## A missed run after sleep

Windows runs it on waking (`StartWhenAvailable`), a systemd timer too (`Persistent=true`), cron
at its next interval. Nothing is lost: sync lands whatever is on disk.

## Nothing lands on macOS or Linux, and the log says `gh: not found`

Why: cron starts with a minimal PATH.
The agent: the cron line carries the PATH seen at registration; re-running onboard replaces an
old line by its `# space-sync-<name>` marker. The log is `~/.spaces/logs/space-sync-<name>.log`.

## No cron (minimal Linux)

The agent: registers a systemd user timer instead when `systemctl --user` works; otherwise the
row asks the person to install cron once (`sudo apt install cron`, `sudo dnf install cronie`).

## `pip` refuses to install: "externally managed environment"

Seen on Homebrew Python (macOS) and Debian or Ubuntu system Python (PEP 668).
The agent: start.py makes a venv at `~/.spaces/venv`, installs PyYAML there and runs again with
that interpreter, which the sync task then uses. Without `python3-venv` it asks the person to run
`sudo apt install python3-venv` once.

## `python: command not found` on macOS

The agent: uses `python3`; the routines and the personal instruction name the absolute
interpreter that ran onboarding, so nothing unattended depends on the bare name.
