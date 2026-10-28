# The sweep in the cloud, read-only

Supersedes the cloud parts of `2026-10-27-cloud-routines-design.md` where they differ. Status: built on
`feat/cloud-sweep`, for v0.3.0-rc4.

## Why

The trial of v0.3.0-rc3 on a throwaway space showed that claude.ai never loads a repository's plugin in a cloud
run. It drops `enabled_plugins`, `extra_marketplaces` and `auto_mode_soft_deny` without a word, and the docs say
so: a cloud session never installs the plugins a repository turns on. So the `spaces-guard` plugin, which carried
the computer's hooks (the bash guard, the connector grant, the send guard), never ran. Every proof failed and the
move was refused, as designed. What does hold in a cloud task: its `allowed_tools`, its `disallowed_tools`, each
connector's `permitted_tools` (a tool not permitted does not exist in the run), and the environment's network
limit. The same trial showed the landing works: `cloud/sweep`, a pull request through REST, merged on green.

The sweep is the routine worth moving. It runs three times a day and keeps `main` current, so a session opened on
a phone after a weekend away reads a brain that is up to date. The daily is once a day, catches up after sleep,
and is the one that must send mail.

## What changes

1. **Only the sweep moves.** The routine host record keeps `runner: cloud` and its `computer`, and its `tasks`
   hold the sweep alone (an older record with two ids still reads). The sweep steps aside on every computer; the
   computer named in `computer` keeps every other routine, the daily first (a record that names none: every
   computer, as the computer's leg reads it). A pause holds all of them. The move is refused on a computer that
   does not run the routines, so the daily always has a home.
2. **The cloud sweep only reads.** Its task gets the read tools of every app the routines may read, and nothing
   that writes, sends, shares or deletes: the tracker step and the owner notices leave the cloud sweep. The
   computer's daily runs the tracker step while the sweep is in the cloud (it already sends the notices). A queue
   only ever adds a notice file; the daily joins the pending security ones into one digest when it sends, so the
   cloud and the computer never write the same file.
3. **The proof needs no plugin.** A proof run tries four things, each of which must be refused or kept off main:
   an `Edit` of `rules.md` (the task's deny list), a connector tool the task was not given (it does not exist in
   the run), a request to a host outside the environment's network limit, and a change to `rules.md` made with a
   command (refused at once, or left out by `sync.py --cloud`: `left_out` on the sync line of the proof's own
   landing, which says `proof`). The run's marker says it is a proof, so only a proof's landing skips the security
   item for what it left out.
4. **The gate holds what runs on the computer.** A cloud run has the person's token and a shell, so it could push
   a branch of any name and open a pull request of its own; a branch name proves nothing. The gate's minimum list,
   for every pull request, now also holds what instructs or runs code on the computer: the rendered routine
   prompts, `onboard.yaml` (whose task records `--check` runs), the migrations, `.mcp.json`, `CLAUDE.local.md`
   and the files pytest reads at its start. What Claude Code or pytest load wherever it sits (`CLAUDE.md`,
   `CLAUDE.local.md`, `.claude/`, `.mcp.json`, `conftest.py`, pytest's config files) is held at any depth.
5. **The task names its environment.** `cloud.py trigger --environment <env_...>` sets
   `job_config.ccr.environment_id`, which claude.ai requires. The agent reads it from the account's routines.
6. **Gone:** the guard plugin and its marketplace file, the `--grant` word, the cloud daily task, and the
   computer's leg doing template sync and `AGENTS.md` (the computer's daily does both).
7. **`onboard.py --dry-run` writes nothing.** It stopped after creating nothing but still filled the spaces.

## What stays the same

The landing (`sync.py --cloud`, carry, adoption by the computer), the deny list, the begin step, the cursors, the
transcripts scan on the computer, `--routines-here`, `--pause`.

## Not done, on purpose

What the cloud run can still reach is bounded by the environment's network limit, which the person sets on
claude.ai and the proof checks: GitHub stays reachable (the landing needs it), so a run led astray by a mail could
still write on GitHub as the person, within the repositories the task attaches, or open a pull request the gate
holds for a click. Such a pull request can still land, on green, the state files the computer itself lands
(`routine-host.yaml`, `backfill.yaml`, `routine-limits.jsonl`, `_meta/tests/`): holding them would put a click on
every pause and every sync repair, and none of them runs code or instructs the computer. A run's shell can also
mark its own marker a proof, which only drops the security item for what its landing left out; the change itself
still stays off main. No new runner, record key or per-chain host.

## Done when

The rc3 trial space, upgraded to this branch, passes a sweep proof and one real cloud sweep that reads at least
two apps besides mail, both landed through merged pull requests; that run had no send, share or delete tool, and
its protected changes were refused; the computer's daily sends the notices the cloud queued; tests, validate,
scan and the name check pass.
