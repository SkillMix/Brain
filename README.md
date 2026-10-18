# Company brain template

A **space** is a git repository that holds knowledge any agent can read and write under
one set of rules: a settings file (`space.yaml`), pages with permanent ids, a registry of
subjects, and queues for what needs a person. A company is a tree of spaces: one brain
everyone reads, one workspace per person, one space per project with its crew as audience.
This template is every profile of that tree in one repository; `onboard` turns a copy of it
into the profile it needs, and the shape is written down in `docs/shape-1.md`.

## Start in one sentence

You need git, GitHub's `gh` and Python 3.10 or newer. One command installs all three:

- macOS: `brew install git gh python`
- Debian or Ubuntu: `sudo apt install git gh python3 python3-venv`
- Windows: `winget install Git.Git GitHub.cli Python.Python.3.12`

Then open Claude Code (the desktop app's Code tab, or the CLI) in any folder and say one sentence:

- **A company with no brain yet:** "Set up our company brain from
  https://github.com/OWNER/REPOSITORY" (this repository's address).
- **Joining a company:** the sentence from your invitation, for example "Onboard me to Acme:
  https://github.com/OWNER/REPOSITORY with my invitation acme/brain:ada".

That is all. The agent copies this template and runs `_meta/tools/start.py`, which does every
step itself: it reads who you are from git and your host account, finds your invitation,
creates your repositories from this template, clones them, fills `space.yaml`, mounts the
brain and the projects you are crew on, installs one instruction at user level so every
session in any repository journals in your workspace, allows your workspace for the harness,
registers the sync task, arms auto-merge and the required check, runs the first sweep, the
status pass and the tests, and renders your two routines for the agent to schedule. Every
question has a default; the ones you may want to change come back on your Needs-you list.

What only you can do: sign in to GitHub once (`gh auth login`) if you never did, and sign in
to your own mail, calendar and chat when you connect them. The tool lists each with the check
that proves it. Your first morning briefing arrives the next day.

### For agents

When a person asks to be set up, onboarded or to start, and names this repository:

1. Clone it to a short folder: `gh repo clone OWNER/REPOSITORY ~/.spaces/template` (on Windows
   run `git config --global core.longpaths true` first; without `gh`, install it from
   https://cli.github.com, on Windows `winget install --id GitHub.cli -e`; not signed in, ask
   the person to run `gh auth login --web` once). The person's sentence is the go-ahead for
   every step below: say what you are doing and go on.
2. Run `sh <temporary folder>/_meta/tools/bootstrap.sh` (Windows: `powershell -NoProfile
   -ExecutionPolicy Bypass -File <temporary folder>\_meta\tools\bootstrap.ps1`): it finds or
   installs Python 3.10+ and runs `_meta/tools/start.py`, whose doctor checks this machine first
   and installs what needs no password. Pass it `--invitation <brain>:<slug>`
   when the person gave an invitation, `--company "<name>"` when they named their company, and
   `--feeds <list>` naming the connectors your session has (mail, calendar, chat, calls,
   tracker, documents). Ask nothing else: every other answer has a default.
3. Each `doctor:` row in `yours` is one sentence for the person with the exact command (a
   sign-in, a password, a dialog, an access grant): ask once, wait, then run step 2 again.
4. Schedule the two routines it rendered under `_meta/state/routines/` in the new workspace
   with your harness's scheduled tasks, each at its `schedule` (cron in the machine's local
   time). Without scheduled tasks, run
   `python <workspace>/_meta/tools/onboard.py <workspace> --os-routines`: the OS scheduler runs
   them through `claude -p`.
5. Follow steps 7 to 9 of `.claude/skills/onboard/SKILL.md` in the clone: quiet GitHub
   notifications for the repositories they just got, one sweep chain now, and an answer that
   shows the open items of the projects they are crew on.

Run again, `start.py` changes nothing that is right: it adopts the repositories it made.

Where they go: the person's own GitHub account, the brain named `<company>-brain`. Pass
`--org <name>` to put them in an organisation instead. A person who belongs to an organisation
and gave no `--org` gets a `namespace` row in `yours`: ask them once which account, then run
step 2 again with `--org`.

### Without an agent

Create a repository from this template, clone it, `pip install pyyaml`, then run
`python _meta/tools/onboard.py` and answer the questions (press Enter for each default, type
`skip` to decide later). `python _meta/tools/onboard.py --check` tells you what is missing
without changing anything.

## Invite a colleague

In the brain: `python _meta/tools/onboard.py . --invite <slug>` (or ask your agent "invite
<name>"). It writes the colleague's invitation (their name, the brain, the projects they are
crew on) and puts one item on your Needs-you list with the sentence to send them. They say it
in Claude Code on their own machine; their agent does the rest in about fifteen minutes,
mostly their sign-ins. Nothing is done for them.

## Start a project from a desk seed

When something on your desk gains a second reader, mint it:

    python _meta/tools/mint.py . --seed work/projects/<subject>/<effort> --name <project> --crew "Ada Okafor" "Ben Liu"

The seed's pages become the project's first drafts, the subject's home moves to the new
space (its id does not), and the platform team is proposed on your Needs-you list.

## Try it first on the fixture company

    python _meta/tools/onboard.py --fixture <some-empty-dir>

Builds Lantern Works offline (a brain, three workspaces, two projects) from
`_meta/tests/fixture/`, runs a week of sweeps on a fast clock, applies the owners'
verdicts, then runs the answer and leak tests and prints one table. No account, no network.

## What runs

- **Routines** (each a tool under `_meta/tools/` and a skill under `.claude/skills/`):
  `sweep` → `distill` → `check` three times a day; `status` → `release` → `morning-view`
  once a day. They write files and queue items; they never sign, send, post or spend.
- **The sync task**: `_meta/tools/sync.py` every 15 minutes through the OS scheduler
  (`schtasks` on Windows, cron elsewhere). It commits what sessions left, opens the pull
  request, and the host merges it when `checks` is green. Nothing else runs git.
- **Needs you**: `python _meta/tools/needs_you.py .` shows what waits for a person.

## Rules

`rules.md` is the constitution: the hard rules, the high-stakes list, the badge floors.
`docs/shape-1.md` states what any agent may assume about a space and must never do.
`docs/decisions.md` records why things are the way they are.

## License

MIT, see `LICENSE`.
