# Troubleshooting

Each entry: what the person sees, why, and what the agent does. `start.py` runs the doctor
before it creates anything (`start.py --doctor` runs only that); `onboard --check` re-runs every
read-back, the doctor's included, and names what is missing. Installs come from official
releases, pinned and checked against their published SHA-256, into `~/.spaces/tools/` with no
admin rights. When a person is needed (a sign-in, a password, a dialog, an access grant), the
agent asks once in one plain sentence with the exact command (`! <command>` in Claude Code),
waits, and checks again. It never types a password.

## Before anything exists

### The link in the sentence does not open

Seen: the repository in the sentence is a 404 for the agent, or start.py cannot read the brain.
Why: that repository is private (the company's brain, or a company's own private copy of the
template); an agent without `gh`, or not signed in, cannot read it. The public template opens
for anyone.
The agent: installs `gh` from https://cli.github.com and asks the person to sign in once
(`gh auth login --web`); then it clones. Signed in and still a 404 means no read access yet:
see "No read access to the template or the brain" below.

### No Python, a Python older than 3.10, or the Microsoft Store's `python`

Seen: `python: command not found`, a version error, or the Store opening.
The agent: runs `bootstrap.sh` (macOS, Linux) or `bootstrap.ps1` (Windows). It uses a Python
3.10+ when there is one (the Store's stub fails the check and is skipped), else installs uv
(pinned, checksummed) and through it a Python under the home folder, no admin rights. The
PowerShell script runs with `-ExecutionPolicy Bypass` for that one process; the machine's policy
is never changed.

### `pip` refuses to install: "externally managed environment"

Seen on Homebrew Python (macOS) and Debian or Ubuntu system Python (PEP 668).
The agent: start.py makes a venv at `~/.spaces/venv`, installs PyYAML there and runs again with
that interpreter, which the sync task then uses. Without `python3-venv`, uv makes the venv
instead; only when that fails too does it ask for `sudo apt install python3-venv`.

### `python: command not found` on macOS

The agent uses `python3`; the routines and the personal instruction name the absolute
interpreter that ran onboarding, so nothing unattended depends on the bare name.

## Tools

### No git

- Windows: the doctor installs MinGit (official, pinned) into `~/.spaces/tools/git`. When it
  cannot (no pinned build for the machine, a failed download), it asks for Git for Windows:
  `! winget install --id Git.Git -e`.
- macOS: with Homebrew, `brew install git`; without, Apple's command line tools come with a
  dialog only the person can click: `! xcode-select --install`.
- Linux: with apt or dnf and a sudo that needs no password (or as root), it installs git;
  otherwise it asks once: `! sudo apt install git`.

### No gh

The doctor installs the GitHub CLI (pinned, checksummed) into `~/.spaces/tools/gh` on every
system. Every tool, the sync task included, puts `~/.spaces/tools/*` first on its PATH. Downloads
made by the tools carry no quarantine flag, so macOS Gatekeeper does not stop them.

### Not signed in to GitHub

The browser sign-in is the person's: `! gh auth login --web --git-protocol https`. The doctor
stops there; what needs the sign-in waits for it.

### git does not know who you are, or cannot push over HTTPS

The doctor sets `user.name` and `user.email` from the GitHub account (the account's no-reply
address when the email is private) and runs `gh auth setup-git`.

### Paths longer than 260 characters (Windows)

The doctor sets `core.longpaths true` for git.

## Access

### The organization invitation is not accepted

The doctor accepts a pending invitation to the organization the sentence names: saying the
sentence is the person asking to join. No invitation at all: the person asks an owner.

### No read access to the template or the brain

Every space is created private, the brain included; the template is public unless the company uses a private copy of
it. The person asks the owner; the owner has an `invite-<slug>-access` item on their Needs-you
list recommending a company-wide team with read access to the brain (and to the template when the
company uses a private copy). Re-running the invitation removes the item once the colleague can
read them. Agents never grant access.

## The machine

### Nothing lands while the laptop is unplugged

Seen: no `chore: sync` commits for the hours the laptop ran on battery.
Why: `schtasks /Create` registers a task that never starts on battery and stops when the power
cable comes out.
The agent: onboard registers the task from XML with both battery settings off and
`StartWhenAvailable` on; `onboard --check` reads them back and says "registered without: starts
on battery" for a task made the old way. Running onboard again re-registers it.

### A console window flashes every 15 minutes (Windows)

The task runs `conhost.exe --headless`; re-running onboard re-registers an old task.

### The laptop was asleep at the scheduled time

Windows runs the missed start on waking (`StartWhenAvailable`), a systemd timer too
(`Persistent=true`), cron at its next interval. Nothing is lost: sync lands whatever is on disk.

### Nothing lands on macOS or Linux, and the log says `gh: not found`

Cron starts with a minimal PATH. The cron line carries the PATH seen at registration;
re-running onboard replaces an old line by its `# space-sync-<name>` marker. The log is
`~/.spaces/logs/space-sync-<name>.log`.

### No cron (minimal Linux)

The agent registers a systemd user timer instead when `systemctl --user` works (it runs while the
person is logged in; `loginctl enable-linger` keeps it running otherwise); with neither, it asks
once: `! sudo apt install cron`.

### The routines stopped when the session closed

Scheduled tasks of Claude Code in a terminal fire only while that session is open and expire
after a few days, so no briefing comes once it closes. `onboard.py <workspace> --os-routines`
registers each routine with the OS scheduler at its times; it runs `routine.py`, which hands the
prompt to `claude -p` in the workspace. Nobody answers a permission prompt there, so anything
that would ask is refused and the routine goes on without it; the routine profile it adds
(`~/.spaces/routines/<routine>-<space>.settings.json`) refuses edits to the machinery and every
tool the chains do not run (`SECURITY.md`). The log is
`~/.spaces/logs/<routine>-<space>.log`. A harness whose scheduled tasks persist may hold the
routines instead, recorded with `--routines-scheduled "<read-back>" ...`; `onboard.py --check`
shows "routines scheduled" failing until one of the two reads back.

### macOS asks for access to Documents, Desktop or Downloads

The spaces default to `~/spaces`, which needs no permission dialog; the doctor names a `--home`
inside a protected folder before anything is created there.

### The disk is full

The doctor wants 1 GB free in the home folder and says how much there is.

## While it runs

### The network drops, or GitHub's rate limit is used up

The doctor checks github.com first and stops with "reconnect, then say the sentence again"; an
exhausted rate limit is named (it resets within the hour). Every step that finished stays finished.

### The sentence is said twice

A second start on the same machine waits for the first (a lock in the temp folder; a lock older
than an hour is a crashed run's and is taken over). Said again later, start.py adopts the
repositories it made and repairs what is missing.

### A step failed halfway

Say the sentence again: every step that is done is kept, the rest runs. A failed row names its fix.

### Cloud sessions

Plain coding works; brainstorm and write specs in a local session for now, because a cloud
session has no workspace instruction and its notes would be lost.
