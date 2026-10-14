---
name: onboard
description: One sentence sets a person or a company up - "onboard me", "set me up", "get started", "join <company>", "set up our company brain", an invitation sentence ("Onboard me to X - <link> with my invitation <brain>:<slug>"), "invite <colleague>", "schedule my routines", or "is this set up?". Runs start.py (fresh setup), onboard.py --invite, or onboard.py --check; the agent does every step but the sign-ins only the person can give.
---

# onboard: one sentence, then the agent does everything

The person says one sentence. You do every step; you ask nothing that has a default. Every
default they may want to change returns on their Needs-you list, so a question now is never
needed to finish.

`python` in these steps means `python3` where `python` is absent (macOS, many Linux systems).

## "Onboard me", "set me up", "get started", an invitation sentence

1. Find the template: the link in the sentence, else this repository when it has no
   `AGENTS.md` (a fresh copy), else the `repo` in `_meta/state/template-version.yaml` of the
   person's workspace. Clone it anywhere temporary when you are not already in a copy:
   `gh repo clone <owner>/<repo> <temporary folder>`. The template is private: without `gh`,
   install it from https://cli.github.com (the official release); not signed in, ask the person
   to run `! gh auth login --web --git-protocol https` once and finish in the browser.
2. Read the sentence for: an invitation (`<brain>:<slug>`, or "my invitation is <slug> in
   <brain>"), a brain without an invitation ("join the Acme brain at acme/brain"), or a
   company name with no brain ("set up our company brain", "for Acme").
3. Name the connectors this session has, as feeds: mail, calendar, chat, calls, tracker,
   documents (a mail connector is `mail`, a meeting recorder is `calls`). None is fine.
4. Run, from the template copy, the bootstrap script with start.py's arguments. It finds a
   Python 3.10 or newer, or installs one under the person's home folder, then runs start.py:
   `sh _meta/tools/bootstrap.sh [--invitation <brain>:<slug> | --brain <brain>]
   [--company "<name>"] [--feeds <list>] --json` (on Windows
   `powershell -NoProfile -ExecutionPolicy Bypass -File _meta\tools\bootstrap.ps1 ...`, which
   leaves the machine's execution policy as it is).
5. start.py runs the doctor first: it installs what needs no password (gh, on Windows git) from
   official, pinned, checksummed releases and sets git up from the GitHub account. A
   `doctor: <name>` row in `yours` is one sentence for the person with the exact command: say it
   in one line (`! <command>` in Claude Code), wait, then run step 4 again. Never type a
   password and never work around a row with a different fix. `--doctor` runs only this check.
6. Schedule the routines in the result (`routines`: name, `schedule` as cron, `prompt_file`)
   with your harness's scheduled tasks, one task each, the prompt file's text as the task's
   prompt. Without scheduled tasks (Claude Code in a terminal), run
   `python <workspace>/_meta/tools/onboard.py <workspace> --os-routines` instead: the OS
   scheduler runs each routine at its times through `claude -p`.
7. Show value now, before the first scheduled run: follow
   `<workspace>/_meta/state/routines/sweep-chain.md` once, as if it fired (a first run exports
   the last 14 days of every connected feed). Then read the Open section of each status page
   the person can already see: `live/subjects/*.md` in their workspace and in every mounted
   project (the mounts in `space.yaml`).
8. Answer in at most six lines: where the workspace is; what is still theirs (the `yours`
   rows, each with its check); per project they are crew on, its open items in one line
   each (at most three); two sentences to try, "status of <a project>" and "prep me for the
   call with <a project>"; that the morning briefing starts tomorrow; that "what needs me"
   shows their list any time.

Run again on the same person, `start.py` adopts what it made and repairs what is missing.
A failed row names its fix; do that fix, never a different workaround.

## "Invite <colleague>"

In the brain (the person's mounted brain, or the brain checkout): find the colleague's slug
in the registry (`people`), then `python _meta/tools/onboard.py <brain> --invite <slug>`. It
writes the invitation and one Needs-you item whose text holds the sentence to send. Show the
person that sentence and where to send it; you never send it yourself. When the colleague is
not in the registry yet, add them as a `people` subject first (the status routine lands it).

## "Connect my mail" (calendar, chat, calls, tracker, documents)

`python _meta/tools/onboard.py <workspace> --connect <feed>` adds it to the workspace's
sources and the register of connected systems. The person signs in to the connector in their
own harness (the claude.ai connector settings, say); the next sweep reads it. When this
session already has the connector, do not wait: export its last 14 days as the sweep skill
says and run `python <workspace>/_meta/tools/chain.py sweep <workspace>`, then show what it
added. Read-back: the feed file `_meta/state/feed/<feed>.jsonl` has records.

## "Schedule my routines"

The rendered prompts are in `<workspace>/_meta/state/routines/`; schedule each as in step 6
above (`--os-routines` when the harness has no scheduled tasks). Read-back: the next day's run
lines in `_meta/state/runs.jsonl` carry `sweep-chain` and `daily-chain`; `onboard --check`
reads OS routine tasks back.

## "Is this set up?"

`python _meta/tools/onboard.py <space> --check`: it changes nothing that is right, re-runs
every read-back, writes each owner step still failing to `_inbound/needs-you/onboard-owner.yaml`,
and names the fix for the rest. Show only the failing rows.

## What the tools already did

`start.py` reads who the person is (git, then the host account; a no-reply address is never
the briefing address), finds the invitation (by slug, else by the person's name, login or
email), creates the repository from the template (the person's own account when the
organization does not allow it), clones it under `~/spaces/`, and runs onboard there:
`space.yaml` filled for the profile; the brain and every crew project mounted read-only; for a
company the charter drafted, the domains registered as umbrella subjects and the owner's
workspace created; `_meta/access/resources.yaml` for the chosen feeds; the personal instruction
at user level and the workspace as an allowed directory for the harness; the sync task every
15 minutes; auto-merge and the required check on the host; `AGENTS.md`, `ids.yaml`, the first
sweep, the first status and the tests; every step recorded as `{step, done, check, result}` in
`_meta/state/onboard.yaml`.

## Other cases

- A checkout on this machine without a sync task (a space made by hand, a corridor from an
  older template): `python _meta/tools/onboard.py <checkout> --register-sync`. It registers the
  task and adds the space to `~/.spaces/spaces.yaml`; the person's chains run over every space
  listed there that they own (`python _meta/tools/chain.py spaces <workspace>` prints them).

- A shadow space going live: `onboard.py --go-live --answers <file>` (the file's `mounts:`
  maps each mount name to its live repository).
- A project that already has a repository: `python _meta/tools/swap.py <checkout> --name
  <space name> --owner <name> --crew <names> --brain <url> --online`; once that has landed,
  `swap.py <checkout> --go-live` runs onboard's go-live and mounts the project in every crew
  workspace (one elsewhere gets a Needs-you item in the brain with the mount line). The sync
  script lands the result.
- A workspace missing a crew project's mount (`onboard --check` fails the row): `python
  _meta/tools/mint.py . --mount <name> <url>`, the line the row names.
- To watch it work before connecting anything real: `onboard.py --fixture <dir>` runs the
  fixture company end to end, offline.
- Without an agent: `python _meta/tools/onboard.py` in a fresh copy runs the interview on the
  terminal (Enter takes a default, `skip` decides later).

## A company with history: research first, then the archives

After a company's onboarding, before anyone signs anything:
1. Research first: read the company's own public sources (its site, the business registry,
   its code organization; never a person's private profiles compiled across sources), and
   write what you read as records (one JSON object per line: `id`, `url`, `title`, `at`,
   `about` identity, people, registry or code, `text`, and `claims` you would confirm),
   then `python _meta/tools/research.py <brain> ingest <records.jsonl>`.
2. The archives as evidence: draft a confirmations file ("I think X, based on Y"), batched
   by how much depends on each answer, from the old knowledge you were given read-only, then
   `python _meta/tools/cold_start.py <brain> --confirmations <file> --archive <path>
   --label <archive name>`. Only what is active and important becomes a candidate.
3. The morning email of the next day carries the first batch. Nothing is signed by you.

## Judgment left to you

Read the sentence generously: any wording that asks to be set up, joined or started is this
skill. Never guess an answer that has no default; the tools have one for everything they need.
When a step needs the person's identity (signing in to their own mail, calendar and chat
through the connector; organization-level grants on the host), hand it over once with the
read-back that proves it took. Run line: `{"routine": "onboard", "at": "<ISO UTC>", "space":
"<name>", "cost_tokens": <n>, "notes": {"step": "agent", "case": "<start|invite|check>"}}`.

## Never

- Never ask for, type or store a password, token or key: the person signs in in their own
  browser and the connector holds the grant (hard rule 1).
- Never send the invitation sentence yourself: the person sends it.
- Never leave `profile: template` in an instance; never set it anywhere but the template.
- Never write `stable`: the charter and spine stay drafts until the owner signs them.
- Never widen the badge beyond the template defaults; never run git yourself (the tools'
  first push to an empty repository is the setup, then the sync script lands everything).

## Scheduled invocation

None for setup. The check may be scheduled:
```
In the space at <root>, run `python _meta/tools/onboard.py --check` and report the rows
that fail with their read-back check. Change nothing; commit nothing.
```

## Behaviour scenarios

`_meta/tools/tests/test_start.py` (one sentence sets a person up from an invitation, offline;
a company with its owner's workspace; a missing invitation stops before anything is created),
`_meta/tools/tests/test_onboard.py` and `_meta/tools/tests/test_fixture_e2e.py`.
