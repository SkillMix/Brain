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

## Reading the progress page's Waiting lines

The progress page, which setup opens in your browser, lists under Waiting everything that holds
something up, on the person or on someone else, each with the one thing that clears it. The sync
task rewrites the page every 15 minutes without the model, so it stays true when a routine or
the model has stopped. Saying the setup sentence again is always safe: setup continues where it
stopped and repeats nothing that is done.

### "Sign in to GitHub" or "Sign in to Claude"

A sign-in from the first two minutes is still open, with its link (and GitHub's one-time code).
Looking at the apps and researching the company go on; the build waits for both. Open the link,
finish in the browser, then say the sentence again. A GitHub code expires after 15 minutes;
saying the sentence again shows a new one.

### "Choose where your repositories live"

You belong to GitHub organisations and none matches the company clearly. Answer the question
in the chat (your own account, or one of the organisations); the build uses the answer.

### "Connect an app"

No app is connected to Claude yet. Connect your mail, calendar and chat in Claude's connector
settings (the line links there). Claude may only see a new connector in a new chat: say the
sentence there. The build goes on; the reading waits for the first app.

### "Sign in to an app again"

An app's sign-in ran out. Its records wait; the other apps go on. Sign in to it again in
Claude's connector settings, and its reading continues from where it stopped.

### "Ask your IT administrator"

An app could not be read, usually because the organisation limits which apps Claude may use.
The other apps go on. Ask your IT administrator to allow that app for Claude.

### "Needs you"

Things wait for your yes on your Needs-you list. Say "what needs me" in any session.

### "Paused": a part of your history

The part stopped at your Claude plan's usage limit, or before it would have drawn on extra,
paid usage (routines never do). It saved as it read, so nothing is lost: it goes on by itself
from where it stopped, after the reset time the line shows. When the plan gave no reset time,
the line says so, and the part tries again at its next run (the night part at 01:00, or a day
sweep when there is room). Nothing to do.

### "Stopped": a routine

- At your Claude limit: it runs again after the reset the line shows. Nothing to do.
- Before extra, paid usage: routines never start into it; it runs again once the limit resets.
- Claude needs you to sign in again: the command line's sign-in ran out. The step is on your
  Needs-you list; routines stop cleanly until you sign in.
- Ran longer than an hour: it was stopped and tries again at its next time.

### "Not started"

The sync task saw a routine not start at its time, so a briefing or a sweep was missed. The
computer was off, or its scheduler is no longer running the task. Say "schedule my routines":
the agent reads each task back and registers what is missing.

### "This computer's scheduler does not catch up on runs it missed"

Seen on a Linux computer without a systemd user session, where the routines run from cron. A
run that was due while the computer was off or asleep is skipped, so a routine can skip a day.
Once the computer runs systemd for your user (whoever looks after the computer can turn it on;
"No cron" above says more), say "schedule my routines": the routines move to systemd timers,
which catch up: a routine missed while you were logged out runs when you log in again. Keeping
them running while nobody is logged in is the computer owner's choice.

### "Waiting for your word": your history

The plan for reading your history is made, and nothing of it is read until you say so. On an
API key, a cloud provider or a gateway, setup builds everything and writes the plan but starts
no part on its own: per-token billing has no plan limit to pace against. A plan made again
because its notes could not be read waits the same way. Until then your routines read only what
is new. Say "start reading my history" when you want it to begin.

### "This app and your routines use different Claude accounts"

Said once, when part 1 of your history is read. The desktop app's usage reading describes the
app's account; the routines draw on the command line's sign-in. Setup compares the two after
part 1: when the times your limits reset differ, the app's readings are no longer used, and the
history paces itself from what the routines' own runs report, mostly at night. Nothing is lost
and there is nothing to do; the history may be current later than first said.
### Cloud sessions

Plain coding works; brainstorm and write specs in a local session for now, because a cloud
session has no workspace instruction and its notes would be lost.

## Routines in the cloud

After "run my routines in the cloud", the sweep and the daily chain run as two scheduled tasks on
claude.ai. Each run lands its own files: at its end `sync.py --cloud <chain>` pushes
`cloud/<chain>` and opens a pull request that the space's checks merge. A run's proof is its run
line on `main`, never the platform's "succeeded"; a run that steps aside (the routines are paused,
or run on a computer again) writes nothing at all.

### A cloud run did not land

Seen: nothing new on `main` after a cloud run's time, or a `cloud/sweep` or `cloud/daily` pull
request left open.
Why: its pull request did not merge in the nine minutes the run waits: it still conflicted with
`main` after three merges, its checks were still red after one repair, or they were slow. Or the
run stopped before its landing step: a run whose begin stopped (a carry that conflicted or failed,
or routines stopped because a proof showed a refusal missing) writes and lands nothing, and says
why only in the task's run log on claude.ai. While the routines are stopped, a real run stops in
code until a proof shows every refusal again.
The agent: reads the run's line on `main` (the last line of `_meta/state/runs.jsonl` with
`"cloud": "<chain>"`) and the task's latest run on claude.ai. Nothing is lost: the next run of that
chain carries the open branch in before it exports. The computer's sync adopts a cloud pull request
only when both hold: it is still open after 30 minutes, and it conflicts with `main` or its checks
are red (`Run.stuck`, `Run.adopt` in `sync.py`). It merges it into its own pull request (`adopted`
in its run line). A green pull request that is only slow is not adopted, because auto-merge lands
it. One the computer cannot merge either is a Needs-you item: the person keeps the side they want,
or closes it, and the next cloud run starts again from `main`, and may make that run's tracker
issues and owner notices again; a run already under way when it is closed lands nothing, and its
log says the pull request "was closed without merging while this run worked". Run lines and marks
alone open no pull request: the branch waits for a run with a real change, or six hours.

### "The cloud sweep has not run since …"

Seen: a Needs-you item a run of the other chain wrote (the daily names the sweep, the sweep the
daily).
Why: that chain's last line on `main` is older than twice the longest gap between its hours, and
the routines are not paused. Its task is off, failing, or stopping before it lands.
The agent: opens the task on claude.ai with the person, reads its latest run, and turns it on again
when it is off. `onboard.py --check` says where the routines run and when each chain last ran. The
item goes once the chain runs again.

### "The cloud runs read <mount> as it was more than 3 days ago"

The cloud reads each mount at its pin, and only the computer's sync moves pins. The computer has
been closed for days, or its sync task stopped: open it, or say "is this set up?".

### "A cloud run changed protected files; they were not landed"

Seen: a security item on Needs you.
Why: a cloud run never changes the machinery, the rules, `space.yaml`, the instructions or git's
own files, so a change there came from something the run read (a mail anyone can write) or a tool
that went wrong. Nothing protected lands from the cloud: the change stayed in the clone, which is
thrown away. An `AGENTS.md` a tool regenerated is put back without an item (the computer's sync
writes it again), and answer tests set aside are the one change there that lands.
The agent: reads that run's log on claude.ai with the person, and what the run read before the
change, before the task runs again; "pause my routines" holds both tasks meanwhile. The same change
seen again is not asked twice; a different one is.

### "Your routines in the cloud do not read <space>"

The space was made after the routines moved, so neither task attaches it and nothing sweeps or
briefs it. Say "run my routines in the cloud" again to add it.

### The computer the routines moved from was replaced

Seen: the routines run in the cloud, the computer the move ran on is gone (a new laptop took its
place), and since then cloud pull requests that conflict stay open, template sync does not run and
new sessions do not show up.
Why: while the routines run in the cloud, only the computer the routine record names (`computer`
in `_meta/state/routine-host.yaml`, the one the move ran on) does the computer's leg: the
transcripts scan, template sync, rewriting a stale `AGENTS.md`, adopting cloud pull requests and
naming the spaces the cloud does not read. Every other computer's sync leaves them to it.
The agent: on the new computer, once the workspace is set up there, moves the routines again ("run
my routines in the cloud", the onboard skill). Its check names the two tasks the routines run as;
the agent proves those two in place, and the move it records names this computer. Its next sync
does the leg. A new computer with no probe of its own checks and renders the tasks with the grant
the record kept. One that has its own probe goes by it: when its check says no app is granted,
connect the apps there, say "run my routines here" so the probe sees them, then move them to the
cloud again.

### A routine's `sync.py` command is refused

Seen: a routine's run log shows its `sync.py` command refused by the guard, after a prompt or a
skill was changed.
Why: a routine runs `sync.py` only in exact commands, its landing: on a computer `--root <root>`
alone or with `--release-leg`, in the cloud `--root <root> --cloud <chain>`. A prompt or skill that
names another flag (`--dry-run`, `--no-push`) is refused; on a computer, until
`routine.SYNC_LEGS` holds it.
The agent: puts the prompt or skill back to the landing command, or, when a computer's routine
needs the new flag, adds it to `routine.SYNC_LEGS` in the template.
